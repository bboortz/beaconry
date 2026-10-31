"""CISA Known Exploited Vulnerabilities (KEV) feed ingestion.

Fetches the CISA KEV catalog, caches the CVE-id set to disk, exposes an O(1)
lookup used by hub ingestion to score Tier 0 findings. Fetch failure is
non-fatal — scans continue with whatever cache snapshot exists.

Design posture:
    - Fetch runs in a background asyncio task (does NOT gate /readyz).
    - /healthz surfaces freshness so operators can see stale/unfetched state.
    - Cache file lives at `${DATA_DIR}/kev_cache.json`.
    - Default feed URL is CISA's canonical JSON; override via `BEACONRY_KEV_FEED_URL`
      for air-gapped setups.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("hub.kev")

DEFAULT_FEED_URL = (
    "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
)
# `os.environ.get(VAR, default)` returns the empty string (not `default`) when
# VAR is explicitly set to "". docker-compose's `${VAR:-}` expansion propagates
# empty strings exactly that way. Use `or` to treat empty-string as unset.
FEED_URL = os.environ.get("BEACONRY_KEV_FEED_URL") or DEFAULT_FEED_URL
REFRESH_INTERVAL_S = 3600  # hourly
STALE_THRESHOLD_S = 25 * 3600  # 25h — one missed daily publication + grace
FETCH_TIMEOUT_S = 20

# In-process state. One instance per hub process.
_cve_ids: frozenset[str] = frozenset()
_last_fetch_ts: float = 0.0
_fetch_ever_succeeded: bool = False


def _cache_path() -> Path:
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    return data_dir / "kev_cache.json"


def _http_fetch(url: str) -> bytes:
    """Blocking HTTP GET. Isolated so tests can monkeypatch it."""
    req = urllib.request.Request(url, headers={"User-Agent": "beaconry/0.1 (+kev)"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:  # noqa: S310
        return resp.read()


def parse_feed(raw: bytes) -> frozenset[str]:
    """Parse the CISA KEV JSON into a frozenset of CVE ids.

    CISA shape:
        {
          "title": "...", "catalogVersion": "...", "count": N,
          "vulnerabilities": [{"cveID": "CVE-...", ...}, ...]
        }
    """
    doc = json.loads(raw)
    vulns = doc.get("vulnerabilities", [])
    if not isinstance(vulns, list):
        raise ValueError("CISA KEV feed: 'vulnerabilities' is not a list")
    ids: set[str] = set()
    for v in vulns:
        if isinstance(v, dict):
            cve = v.get("cveID") or v.get("cve_id")
            if isinstance(cve, str) and cve.startswith("CVE-"):
                ids.add(cve.upper())
    return frozenset(ids)


def _write_cache(ids: frozenset[str]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": int(time.time()),
        "cve_ids": sorted(ids),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


def _read_cache() -> tuple[frozenset[str], float] | None:
    """Return (cve_ids, fetched_at) or None if no cache exists / is unreadable."""
    path = _cache_path()
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text())
        ids = frozenset(str(x).upper() for x in doc.get("cve_ids", []))
        ts = float(doc.get("fetched_at", 0.0))
        return ids, ts
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.warning("KEV cache unreadable (%s) — treating as unfetched", exc)
        return None


def is_known_exploited(cve_id: str) -> bool:
    """O(1) lookup. Case-insensitive on the CVE id."""
    if not cve_id:
        return False
    return cve_id.upper() in _cve_ids


def freshness() -> dict[str, int | str]:
    """/healthz-ready summary of KEV cache state.

    Returns one of:
      {"kev": "ok",         "kev_last_fetch_s": N, "kev_count": N}
      {"kev": "stale",      "kev_last_fetch_s": N, "kev_count": N}
      {"kev": "unfetched",  "kev_count": 0}
    """
    count = len(_cve_ids)
    if _last_fetch_ts <= 0:
        return {"kev": "unfetched", "kev_count": count}
    age = int(time.time() - _last_fetch_ts)
    label = "ok" if age < STALE_THRESHOLD_S else "stale"
    return {"kev": label, "kev_last_fetch_s": age, "kev_count": count}


def _reset_state_for_tests() -> None:
    """Test-only helper. Not exported. Clears the module-level cache."""
    global _cve_ids, _last_fetch_ts, _fetch_ever_succeeded
    _cve_ids = frozenset()
    _last_fetch_ts = 0.0
    _fetch_ever_succeeded = False


async def fetch_or_fallback() -> None:
    """One attempt: fetch feed → update in-memory + disk cache. On failure,
    fall back to disk cache if available. Non-raising."""
    global _cve_ids, _last_fetch_ts, _fetch_ever_succeeded

    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(None, _http_fetch, FEED_URL)
        ids = parse_feed(raw)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        log.warning("KEV fetch failed: %s — falling back to cache", exc)
        if not _fetch_ever_succeeded:
            cached = _read_cache()
            if cached is not None:
                _cve_ids, _last_fetch_ts = cached
                log.info(
                    "KEV using disk cache (%d entries, %ds old)",
                    len(_cve_ids),
                    int(time.time() - _last_fetch_ts),
                )
        return

    _cve_ids = ids
    _last_fetch_ts = time.time()
    _fetch_ever_succeeded = True
    try:
        _write_cache(ids)
    except OSError as exc:
        log.warning("KEV cache write failed: %s (in-memory still updated)", exc)
    log.info("KEV catalog refreshed: %d entries", len(_cve_ids))


async def refresh_loop() -> None:
    """Background task spawned by hub lifespan. Runs forever until cancelled."""
    while True:
        await fetch_or_fallback()
        await asyncio.sleep(REFRESH_INTERVAL_S)


def warm_from_cache_if_any() -> None:
    """Boot-time: pre-populate in-memory from disk cache before the first
    HTTP fetch completes. Lets the first scan already use KEV scoring
    without waiting on the network."""
    global _cve_ids, _last_fetch_ts
    if _cve_ids:
        return  # already warm
    cached = _read_cache()
    if cached is not None:
        _cve_ids, _last_fetch_ts = cached
        log.info("KEV warm-boot from disk cache: %d entries", len(_cve_ids))
