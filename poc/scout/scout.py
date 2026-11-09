"""
Beaconry Scout 0.1.0

Read-only inventory collector. Generates SBOMs via syft, posts to hub.
Never executes anything. No CVE matching — that is the hub's job.

Scans:
  - Docker images on the host (via Docker socket + syft)
  - Arch Linux packages (via pacman local DB parser)
  - Open TCP ports (via ss -tlnp)

BC-013: scout runs a tiny FastAPI HTTP server on port 8001 serving the
four endpoints via `beaconry_health.build_router`. The scan loop runs as
a background asyncio task via `asyncio.to_thread(collect_and_post)` so
the HTTP event loop stays responsive during long-running scans. SentinelWriter
(BC-010) is no longer used — kept available in `beaconry_health` for
future HTTP-less loop services.
"""

import asyncio
import fnmatch
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import docker
import docker.errors
import requests
import uvicorn
from fastapi import FastAPI

try:
    # Container path: beaconry_health COPY'd next to scout.py on /app
    from beaconry_health import SOFTWARE_VERSION, build_router
except ImportError:
    # Host test path: imported as poc.scout.scout
    from poc.beaconry_health import SOFTWARE_VERSION, build_router

# BC-062: the HTTP contract version this scout speaks. Bumped on any
# breaking change to the hub's /scan payload or response shape.
CLIENT_API_VERSION = 1

# BC-087: operator-controlled root log level. Shared semantics with hub.
_LOG_LEVEL_RAW = os.environ.get("BEACONRY_LOG_LEVEL", "INFO").upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_RAW, None)
if not isinstance(_LOG_LEVEL, int):
    _LOG_LEVEL = logging.INFO
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s [scout] %(levelname)s %(message)s",
)
log = logging.getLogger("scout")
if os.environ.get("BEACONRY_LOG_LEVEL") and _LOG_LEVEL_RAW not in {
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
}:
    log.warning("BEACONRY_LOG_LEVEL=%r invalid; falling back to INFO", _LOG_LEVEL_RAW)

# Suppress noisy loggers (httpx might leak scan payloads at INFO).
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

HUB_URL = os.environ.get("HUB_URL", "http://hub:8000")
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "300"))
HOST_NAME = os.environ.get("HOST_NAME", os.uname().nodename)
PACMAN_DB = os.environ.get("PACMAN_DB", "/host/var/lib/pacman")
HTTP_PORT = int(os.environ.get("SCOUT_HTTP_PORT", "8001"))
# BC-063: short-interval keepalive to the hub so FLEET's HEALTH / VERSION
# cells stay fresh between SCAN_INTERVAL ticks. Default 60s is the
# "comfortable" value — one env var, no fiddling needed. Set to 0 to
# disable (falls back to BC-061 behaviour where /scan is the only
# heartbeat).
SCOUT_HEARTBEAT_INTERVAL_S = int(os.environ.get("SCOUT_HEARTBEAT_INTERVAL_S", "60"))
# BC-075: metrics opt-in. Default OFF — scout timing + action patterns are
# not aggregated by `beaconry metrics` unless explicitly enabled. Scouts
# running against a hub they don't own (future SaaS, multi-tenant) stay
# private by default. Setting `SCOUT_METRICS_ENABLED=1` adds the opt-in
# header to /scan + /heartbeat POSTs; hub honors it by writing the
# metrics-fuel audit events (`scan_received`, `heartbeat_received`).
# CVE matching, notifications, fleet view all keep working regardless.
SCOUT_METRICS_ENABLED = os.environ.get("SCOUT_METRICS_ENABLED", "0") == "1"

# BC-092: canary-ring role. Default `workstation` (least-privileged ring) —
# a misconfigured or legacy scout stays unaffected by the hub's
# production-fix gate. Valid values: workstation | staging | production.
# Operator must explicitly set to flag a host for the canary ladder.
_VALID_ROLES = frozenset({"workstation", "staging", "production"})
_raw_role = (os.environ.get("SCOUT_HOST_ROLE") or "workstation").strip().lower()
if _raw_role not in _VALID_ROLES:
    log.warning(
        "SCOUT_HOST_ROLE=%r invalid; using 'workstation' (valid: %s)",
        _raw_role,
        sorted(_VALID_ROLES),
    )
    _raw_role = "workstation"
HOST_ROLE = _raw_role

# BC-093: wall-clock daily rescan hour (0-23, default 02:00 local;
# -1 disables). Catches slow drift between regular SCAN_INTERVAL
# ticks (pip installs, port changes).
_daily_hour_raw = os.environ.get("BEACONRY_DAILY_RESCAN_HOUR") or "2"
try:
    DAILY_RESCAN_HOUR = int(_daily_hour_raw)
except ValueError:
    log.warning("BEACONRY_DAILY_RESCAN_HOUR=%r invalid; using 2 (02:00 local)", _daily_hour_raw)
    DAILY_RESCAN_HOUR = 2
if DAILY_RESCAN_HOUR != -1 and (DAILY_RESCAN_HOUR < 0 or DAILY_RESCAN_HOUR > 23):
    log.warning("BEACONRY_DAILY_RESCAN_HOUR=%d out of [0,23]; using 2", DAILY_RESCAN_HOUR)
    DAILY_RESCAN_HOUR = 2

# BC-093: subscribe to docker events to trigger scans on image pull /
# container start. Default on (scouts run on Linux w/ docker socket mounted).
DOCKER_EVENTS_ENABLED = os.environ.get("BEACONRY_DOCKER_EVENTS_ENABLED", "1") != "0"
# Debounce window — compose up pulling N images fires one scan DEBOUNCE_S
# after the last event, not N scans back-to-back.
DOCKER_EVENTS_DEBOUNCE_S = max(1, int(os.environ.get("BEACONRY_DOCKER_EVENTS_DEBOUNCE_S") or "10"))

# BC-108: capped exponential backoff between reconnection attempts when the
# docker events stream drops (daemon restart, socket tear). Pre-BC-108 the
# single `_blocking_consume` thread exited silently and event-driven scans
# stopped forever (until scout restart). BC-108 wraps the consume in a loop
# that keeps retrying until daemon is up again. Clamps: MIN >= 1, MAX >= MIN.
DOCKER_EVENTS_BACKOFF_MIN_S = max(
    1, int(os.environ.get("BEACONRY_DOCKER_EVENTS_BACKOFF_MIN_S") or "1")
)
DOCKER_EVENTS_BACKOFF_MAX_S = max(
    DOCKER_EVENTS_BACKOFF_MIN_S,
    int(os.environ.get("BEACONRY_DOCKER_EVENTS_BACKOFF_MAX_S") or "60"),
)

# BC-107: scout-side persistent retry queue for failed /scan POSTs. Mirrors
# BC-104's hub-side `pending_notifications`. Scout had no DB pre-BC-107 —
# this is the first persistent file scout owns (`/data/scout-state.db`).
# `MAX_ATTEMPTS=1` effectively disables retry (one try then drop — matches
# pre-BC-107 behavior for operators who want air-gap sim).
SCOUT_STATE_DB_PATH = os.environ.get("BEACONRY_SCOUT_STATE_DB", "/data/scout-state.db")


def _parse_positive_int(raw: str | None, default: int, name: str) -> int:
    try:
        v = int(raw) if raw is not None else default
    except ValueError:
        log.warning("%s=%r invalid; using %d", name, raw, default)
        return default
    if v < 1:
        log.warning("%s=%d must be >= 1; using %d", name, v, default)
        return default
    return v


POST_RETRY_MAX_ATTEMPTS = _parse_positive_int(
    os.environ.get("BEACONRY_SCOUT_POST_RETRY_MAX_ATTEMPTS"),
    5,
    "BEACONRY_SCOUT_POST_RETRY_MAX_ATTEMPTS",
)
POST_RETRY_BASE_S = _parse_positive_int(
    os.environ.get("BEACONRY_SCOUT_POST_RETRY_BASE_S"),
    30,
    "BEACONRY_SCOUT_POST_RETRY_BASE_S",
)
POST_RETRY_SWEEP_INTERVAL_S = _parse_positive_int(
    os.environ.get("BEACONRY_SCOUT_POST_RETRY_SWEEP_INTERVAL_S"),
    60,
    "BEACONRY_SCOUT_POST_RETRY_SWEEP_INTERVAL_S",
)

# BC-005 defaults — demo-safe allowlist, no excludes by default.
DEFAULT_INCLUDE_GLOBS = "nginx*,redis*,postgres*"

# BC-013 health state — mutable module globals read by /readyz /healthz providers.
_docker_socket_ok: bool = False
_started: bool = False
_last_scan_ts: int = 0
_images_seen: int = 0
_bytes_posted: int = 0
# BC-106: per-scan completeness counts. `expected` is SCOUT_INCLUDE
# matches; `collected` is images that syft produced an SBOM for. Hub
# compares the ratio to `BEACONRY_PARTIAL_SCAN_MIN_RATIO` and skips
# reconcile when partial.
_last_scan_images_expected: int = 0
_last_scan_images_collected: int = 0
SCOUT_VERSION = SOFTWARE_VERSION  # BC-062: single source of truth = VERSION file

# BC-037: on-demand scan trigger. Single-threaded event loop → a bool is a
# sufficient mutex. Loop + manual trigger share this guard so a concurrent
# trigger short-circuits with 409 instead of double-scanning the inventory.
_scan_in_progress: bool = False

# SBOM cache: tag → (image_id, sbom). Avoids re-running syft when the image
# digest hasn't changed. Keyed by tag; eviction is implicit (stale tags simply
# never appear in matching and are never read again).
_sbom_cache: dict[str, tuple[str, dict]] = {}


def parse_globs(env: str) -> list[str]:
    """Parse a comma-separated glob list from an env var. Whitespace tolerated. Empty → []."""
    return [piece.strip() for piece in env.split(",") if piece.strip()]


# ── BC-030 — container port publication → internet_facing map ────────────────

# Loopback-only addresses that must NOT count as internet-facing. All other
# non-empty values (including "0.0.0.0", "::", and any routable host IP) do.
_LOOPBACK_HOST_IPS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def is_container_internet_facing(ports: dict | None) -> bool:
    """Return True if `ports` (Docker's NetworkSettings.Ports shape) has at
    least one binding to a non-loopback host IP.

    Shape: ``{"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "80"}], ...}``.
    An unpublished port carries `None` or missing bindings → not exposed.
    Empty HostIp (Docker's default binding) counts as exposed — equivalent to
    0.0.0.0 for most daemons.
    """
    if not ports:
        return False
    for bindings in ports.values():
        if not bindings:
            continue
        for b in bindings:
            host_ip = (b.get("HostIp") or "").strip()
            if host_ip and host_ip in _LOOPBACK_HOST_IPS:
                continue
            # Empty HostIp OR non-loopback IP → routable from outside the host.
            return True
    return False


def collect_container_ports() -> dict[str, bool]:  # pragma: no cover - docker.from_env wrapper
    """Return `{image_tag: is_internet_facing}` for every running container.

    If multiple containers share an image and any publishes externally, the
    image is marked True. Images with no running container simply don't
    appear — hub treats absence as `False`.
    """
    try:
        client = docker.from_env()
    except docker.errors.DockerException as e:
        log.warning("Docker socket unavailable for port collection: %s", e)
        return {}

    out: dict[str, bool] = {}
    try:
        containers = client.containers.list()
    except docker.errors.DockerException as e:
        log.warning("Container list failed: %s", e)
        return {}
    for container in containers:
        ports = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
        exposed = is_container_internet_facing(ports)
        image = container.image
        if image is None:
            continue
        for tag in image.tags:
            out[tag] = out.get(tag, False) or exposed
    return out


def filter_image_tags(
    tags: list[str],
    *,
    include_globs: list[str],
    exclude_globs: list[str],
) -> list[str]:
    """Return tags matching any include glob AND no exclude glob.

    If include_globs is empty, nothing passes. If exclude_globs is empty,
    only include is applied. Exclude always overrides include.
    """
    return [
        tag
        for tag in tags
        if any(fnmatch.fnmatchcase(tag, g) for g in include_globs)
        and not any(fnmatch.fnmatchcase(tag, g) for g in exclude_globs)
    ]


# ── syft ─────────────────────────────────────────────────────────────────────


def syft_scan(
    target: str,
) -> dict | None:  # pragma: no cover - subprocess wrapper around syft binary
    result = subprocess.run(
        ["syft", target, "-o", "syft-json", "--quiet"],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if result.returncode != 0:
        log.warning("syft failed for %s: %s", target, result.stderr[:200])
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        log.warning("syft output parse error for %s: %s", target, e)
        return None


# ── Docker images ─────────────────────────────────────────────────────────────


def scan_docker_images() -> list[
    dict
]:  # pragma: no cover - docker.from_env() + subprocess around syft
    global _docker_socket_ok
    try:
        client = docker.from_env()
        _docker_socket_ok = True
    except docker.errors.DockerException as e:
        log.warning("Docker socket unavailable: %s", e)
        _docker_socket_ok = False
        return []

    include_globs = parse_globs(os.environ.get("SCOUT_INCLUDE", DEFAULT_INCLUDE_GLOBS))
    exclude_globs = parse_globs(os.environ.get("SCOUT_EXCLUDE", ""))

    tag_to_id: dict[str, str] = {}
    all_tags: list[str] = []
    for image in client.images.list():
        for t in image.tags:
            if "<none>" not in t:
                all_tags.append(t)
                tag_to_id[t] = image.id or ""
    matching = filter_image_tags(all_tags, include_globs=include_globs, exclude_globs=exclude_globs)
    skipped = sorted(set(all_tags) - set(matching))
    log.info(
        "Image inventory: %d total, %d matched (%s), %d skipped",
        len(all_tags),
        len(matching),
        ", ".join(include_globs) or "<none>",
        len(skipped),
    )
    if skipped:
        log.info("Skipped tags: %s", ", ".join(skipped[:10]) + (" …" if len(skipped) > 10 else ""))
    if not matching and all_tags:
        log.warning(
            "0 image(s) matched SCOUT_INCLUDE=%r — nothing to scan. "
            'Adjust the glob (e.g. SCOUT_INCLUDE="*" to scan everything).',
            os.environ.get("SCOUT_INCLUDE", DEFAULT_INCLUDE_GLOBS),
        )

    results = []
    cache_hits = 0
    for tag in matching:
        image_id = tag_to_id.get(tag, "")
        cached_id, cached_sbom = _sbom_cache.get(tag, ("", None))
        if image_id and image_id == cached_id and cached_sbom is not None:
            cache_hits += 1
            results.append({"source_meta": {"type": "docker", "image": tag}, "sbom": cached_sbom})
            continue
        log.info("Scanning image: %s", tag)
        sbom = syft_scan(tag)
        if sbom:
            _sbom_cache[tag] = (image_id, sbom)
            results.append(
                {
                    "source_meta": {"type": "docker", "image": tag},
                    "sbom": sbom,
                }
            )
    if cache_hits:
        log.info("Cache: %d/%d image(s) skipped (digest unchanged)", cache_hits, len(matching))
    # BC-106: expose per-scan counts for the POST payload. `expected` is the
    # number of images the SCOUT_INCLUDE filter matched (what we tried);
    # `collected` is what syft actually produced an SBOM for. The diff
    # (= syft failures / timeouts) feeds hub's partial-inventory guard.
    global _last_scan_images_expected, _last_scan_images_collected
    _last_scan_images_expected = len(matching)
    _last_scan_images_collected = len(results)
    return results


# ── Arch Linux packages ───────────────────────────────────────────────────────


def parse_pacman_desc(text: str) -> dict:
    fields: dict[str, str] = {}
    key = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("%") and line.endswith("%"):
            key = line.strip("%")
        elif key and line:
            fields[key] = line
            key = None
    return fields


def scan_pacman(
    db_path: str,
) -> dict | None:  # pragma: no cover - filesystem walk; parse_pacman_desc is unit-tested
    local = Path(db_path) / "local"
    if not local.exists():
        log.warning("Pacman DB not found at %s", local)
        return None

    artifacts = []
    for pkg_dir in local.iterdir():
        desc_file = pkg_dir / "desc"
        if not desc_file.exists():
            continue
        try:
            fields = parse_pacman_desc(desc_file.read_text(errors="replace"))
        except OSError:
            continue

        name = fields.get("NAME", "")
        version = fields.get("VERSION", "")
        if not name or not version:
            continue

        artifacts.append(
            {
                "id": f"alpm-{name}",
                "name": name,
                "version": version,
                "type": "alpm",
                "language": "",
                "cpes": [],
                "purl": f"pkg:alpm/arch/{name}@{version}",
                "metadataType": "AlpmPackage",
                "metadata": {"basePackage": name},
            }
        )

    if not artifacts:
        return None

    log.info("Arch packages: %d found in pacman DB", len(artifacts))
    return {
        "schema": {
            "version": "16.0.10",
            "url": "https://raw.githubusercontent.com/anchore/syft/main/schema/json/schema-16.0.10.json",
        },
        "artifacts": artifacts,
        "artifactRelationships": [],
        "source": {"type": "directory", "target": "/"},
        "distro": {"name": "Arch Linux", "id": "arch", "versionID": "rolling"},
        "descriptor": {"name": "beaconry-scout", "version": "0.1.0"},
    }


# ── Open ports ────────────────────────────────────────────────────────────────


def parse_ss_output(stdout: str) -> list[dict]:
    """Parse `ss -tlnp` output into a list of {port, address, raw} dicts.

    Pure function — extracted from scan_open_ports so the parsing logic is
    unit-testable without an actual `ss` binary. First line is the header.
    """
    ports: list[dict] = []
    for line in stdout.strip().splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        addr = parts[3]
        port = addr.rsplit(":", 1)[-1]
        if port.isdigit():
            ports.append({"port": port, "address": addr, "raw": line})
    return ports


def scan_open_ports() -> list[
    dict
]:  # pragma: no cover - subprocess wrapper; parse_ss_output is unit-tested
    result = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True)
    return parse_ss_output(result.stdout)


# ── Scan loop ─────────────────────────────────────────────────────────────────


def collect_and_post():  # pragma: no cover - orchestrator; integration-tested live via test-e2e
    """One scan iteration. Bumps module-local state so /healthz surfaces metrics."""
    global _last_scan_ts, _images_seen, _bytes_posted, _started

    log.info("Scan starting — host: %s", HOST_NAME)
    sboms: list[dict] = []
    images_seen = 0

    try:
        docker_sboms = scan_docker_images()
        sboms.extend(docker_sboms)
        images_seen = len(docker_sboms)
        log.info("Docker: %d image(s) scanned", len(docker_sboms))
    except Exception as e:
        log.error("Docker scan error: %s", e)

    try:
        pacman_sbom = scan_pacman(PACMAN_DB)
        if pacman_sbom:
            sboms.append(
                {
                    "source_meta": {"type": "alpm", "host": HOST_NAME},
                    "sbom": pacman_sbom,
                }
            )
    except Exception as e:
        log.error("Pacman scan error: %s", e)

    open_ports: list[dict] = []
    try:
        open_ports = scan_open_ports()
    except Exception as e:
        log.error("Port scan error: %s", e)

    # BC-030: {image_tag: is_internet_facing} map from running containers.
    container_exposure: dict[str, bool] = {}
    try:
        container_exposure = collect_container_ports()
        if container_exposure:
            exposed = [k for k, v in container_exposure.items() if v]
            log.info(
                "Container exposure: %d image(s) tracked, %d internet-facing",
                len(container_exposure),
                len(exposed),
            )
    except Exception as e:
        log.error("Container port collection error: %s", e)

    _last_scan_ts = int(time.time())
    _images_seen = images_seen

    if not sboms:
        log.warning("No SBOMs collected — nothing to send")
        return

    payload = {
        "host": HOST_NAME,
        "timestamp": int(time.time()),
        # BC-063: scout self-reports version + health so FLEET rows carry
        # real diagnostic info instead of just "stale 1h ago".
        "software_version": SOFTWARE_VERSION,
        "api_version": CLIENT_API_VERSION,
        "health": "ok" if _docker_socket_ok else "degraded",
        # BC-092: canary ring role on /scan too, so hub stays in sync even
        # if the heartbeat loop is disabled.
        "role": HOST_ROLE,
        "sboms": sboms,
        "open_ports": open_ports,
        "container_exposure": container_exposure,
        # BC-106: explicit per-scan completeness. Hub compares to the
        # BEACONRY_PARTIAL_SCAN_MIN_RATIO threshold; when below, reconcile
        # is skipped (findings still ingested). Backward compat: pre-BC-106
        # hubs ignore these keys, pre-BC-106 scouts omit them → BC-067
        # baseline heuristic is the fallback on both sides.
        "images_expected": _last_scan_images_expected,
        "images_collected": _last_scan_images_collected,
        "scan_complete": _last_scan_images_expected == _last_scan_images_collected,
    }
    try:
        body = json.dumps(payload)
    except (TypeError, ValueError) as e:
        log.error("Failed to serialize SBOM payload: %s", e)
        return
    _bytes_posted = len(body)
    ok, err, data = _post_scan_body(body)
    if ok:
        findings = (data or {}).get("findings", 0) if isinstance(data, dict) else 0
        log.info("Hub response: %d new finding(s)", findings)
        _started = True
    else:
        log.error("Failed to post to hub: %s", err)
        # BC-107: persist to pending_scans so a retry sweep can replay it.
        # Pre-BC-107 the SBOM was dropped here with only a log line.
        _queue_pending_scan(body, err)


# ── BC-107 — Scout POST retry queue ──────────────────────────────────────────


def _init_scout_db() -> None:
    """BC-107: ensure `scout-state.db` exists with the `pending_scans` table.

    Called once from lifespan. Safe to re-run (CREATE TABLE IF NOT EXISTS).
    If the DB can't be opened (read-only mount, permission denied), scout
    continues — retry queue is best-effort; without it we degrade to
    pre-BC-107 behavior (log + drop).
    """
    import sqlite3

    db_dir = os.path.dirname(SCOUT_STATE_DB_PATH)
    if db_dir:
        with suppress(OSError):
            os.makedirs(db_dir, exist_ok=True)
    try:
        con = sqlite3.connect(SCOUT_STATE_DB_PATH)
    except sqlite3.OperationalError as e:
        log.warning("BC-107: can't open scout-state.db at %s: %s", SCOUT_STATE_DB_PATH, e)
        return
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS pending_scans (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                payload_json    TEXT NOT NULL,
                created_ts      INTEGER NOT NULL,
                attempts        INTEGER NOT NULL DEFAULT 0,
                next_attempt_ts INTEGER NOT NULL,
                last_error      TEXT
            )
        """)
        con.commit()
    finally:
        con.close()


def _post_scan_body(body: str) -> tuple[bool, str, object | None]:
    """BC-107: single-attempt POST to hub /scan.

    Returns `(ok, error_message, response_body_or_None)`. Pulled out of
    `collect_and_post` so the retry sweep replays through the same code
    path — same headers, token, URL, timeout. Any divergence between
    first-try and retry path is a bug magnet.
    """
    headers = {
        "Content-Type": "application/json",
        "X-Beaconry-API-Expect": str(CLIENT_API_VERSION),
    }
    if SCOUT_METRICS_ENABLED:
        headers["X-Beaconry-Metrics-Opt-In"] = "1"
    token = os.environ.get("HUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.post(f"{HUB_URL}/scan", data=body, headers=headers, timeout=1800)
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            data = None
        return True, "", data
    except requests.RequestException as e:
        return False, str(e), None


def _queue_pending_scan(payload_json: str, error: str) -> None:
    """BC-107: persist a failed /scan POST to `pending_scans`.

    Called from `collect_and_post`'s exception branch. Scheduled for
    replay at `now + POST_RETRY_BASE_S`.
    """
    import sqlite3

    now = int(time.time())
    try:
        con = sqlite3.connect(SCOUT_STATE_DB_PATH)
    except sqlite3.OperationalError:
        log.warning("BC-107: can't open DB to queue pending scan")
        return
    try:
        con.execute(
            "INSERT INTO pending_scans "
            "(payload_json, created_ts, attempts, next_attempt_ts, last_error) "
            "VALUES (?, ?, 0, ?, ?)",
            (payload_json, now, now + POST_RETRY_BASE_S, error[:400]),
        )
        con.commit()
        log.warning("BC-107: scan_queued size_bytes=%d err=%s", len(payload_json), error[:200])
    except sqlite3.OperationalError as e:
        log.warning("BC-107: failed to queue pending scan: %s", e)
    finally:
        con.close()


def post_retry_sweep(now_ts: int | None = None) -> dict[str, int]:
    """BC-107: process due rows in `pending_scans`.

    Mirror of hub's BC-104 `notify_retry_sweep`. Returns counts dict
    `{delivered, requeued, abandoned}` for tests + logging.

    For each due row:
      - call `_post_scan_body(payload_json)`;
      - on success → DELETE + log `scan_delivered`;
      - on failure → bump `attempts`; if `attempts >= MAX_ATTEMPTS`,
        DELETE + log `scan_abandoned`; else update
        `next_attempt_ts = now + base × 2^attempts`.
    """
    import sqlite3

    now = now_ts if now_ts is not None else int(time.time())
    counts = {"delivered": 0, "requeued": 0, "abandoned": 0}
    try:
        con = sqlite3.connect(SCOUT_STATE_DB_PATH)
    except sqlite3.OperationalError:
        return counts
    try:
        rows = con.execute(
            "SELECT id, payload_json, attempts FROM pending_scans "
            "WHERE next_attempt_ts <= ? ORDER BY id ASC",
            (now,),
        ).fetchall()
    except sqlite3.OperationalError:
        con.close()
        return counts

    for row_id, payload_json, attempts in rows:
        ok, err, _data = _post_scan_body(payload_json)
        if ok:
            con.execute("DELETE FROM pending_scans WHERE id=?", (row_id,))
            counts["delivered"] += 1
            log.info(
                "BC-107: scan_delivered id=%d retry_attempts=%d",
                row_id,
                int(attempts) + 1,
            )
        else:
            new_attempts = int(attempts) + 1
            if new_attempts >= POST_RETRY_MAX_ATTEMPTS:
                con.execute("DELETE FROM pending_scans WHERE id=?", (row_id,))
                counts["abandoned"] += 1
                log.warning(
                    "BC-107: scan_abandoned id=%d attempts=%d err=%s",
                    row_id,
                    new_attempts,
                    err[:200],
                )
            else:
                backoff = POST_RETRY_BASE_S * (2**new_attempts)
                con.execute(
                    "UPDATE pending_scans SET attempts=?, next_attempt_ts=?, last_error=? "
                    "WHERE id=?",
                    (new_attempts, now + backoff, err[:400], row_id),
                )
                counts["requeued"] += 1
    con.commit()
    con.close()
    return counts


async def _post_retry_loop_forever() -> None:  # pragma: no cover - infinite async loop
    """BC-107: background sweep. Cadence = POST_RETRY_SWEEP_INTERVAL_S."""
    while True:
        try:
            await asyncio.to_thread(post_retry_sweep)
        except Exception as e:  # noqa: BLE001
            log.warning("BC-107: post retry sweep failed: %s", e)
        await asyncio.sleep(POST_RETRY_SWEEP_INTERVAL_S)


def _pending_scans_queue_depth() -> int:
    """BC-107: /healthz key — count of pending_scans rows. Non-zero means
    the hub was unreachable at least once since this scout started and
    the payload is awaiting retry."""
    import sqlite3

    try:
        con = sqlite3.connect(SCOUT_STATE_DB_PATH)
    except sqlite3.OperationalError:
        return 0
    try:
        row = con.execute("SELECT COUNT(*) FROM pending_scans").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


# ── Health providers for build_router ─────────────────────────────────────────


def _scout_readyz_providers() -> dict[str, str]:
    """Local prereqs — Docker socket is the only external dep scout needs."""
    return {"docker_socket": "ok" if _docker_socket_ok else "unavailable"}


def _scout_healthz_summary() -> dict:
    checks = {
        "docker_socket": "ok" if _docker_socket_ok else "unavailable",
        "last_scan_ts": _last_scan_ts,
        "images_seen": _images_seen,
        "bytes_posted": _bytes_posted,
        # BC-107: non-zero = hub was unreachable at least once since this
        # scout started and the SBOM is awaiting retry.
        "pending_scans_queue_depth": _pending_scans_queue_depth(),
        # BC-108: non-zero = docker daemon restarted (or socket churned)
        # at least once since this scout started. Operators seeing a
        # steadily growing count should investigate daemon health.
        "docker_events_reconnects": _docker_events_reconnects,
        "docker_events_last_reconnect_ts": _docker_events_last_reconnect_ts,
    }
    degraded = not _docker_socket_ok
    return {
        "status": "degraded" if degraded else "ok",
        # BC-062: version block mirrors hub's shape. Scout has no DB so
        # no schema key — only software + api.
        "version": {
            "software": SOFTWARE_VERSION,
            "api": CLIENT_API_VERSION,
        },
        "checks": checks,
    }


# ── FastAPI app + background scan task ───────────────────────────────────────


def _try_acquire_scan_lock() -> bool:
    """BC-037: synchronous flag flip, usable by the HTTP handler so the
    `_scan_in_progress=True` state is visible to the very next request even
    before the background task runs. Returns True if we got the lock."""
    global _scan_in_progress
    if _scan_in_progress:
        return False
    _scan_in_progress = True
    return True


def _release_scan_lock() -> None:
    global _scan_in_progress
    _scan_in_progress = False


async def _run_scan_once(reason: str) -> str:
    """BC-037: single guarded entry point for both the scheduled loop and the
    manual trigger. Returns one of:
      - "ran"            — scan executed, flag released
      - "already_running" — another scan was in progress; this call no-op'd
    `reason` is only used in logs — distinguishes "loop" vs "manual" ingest."""
    if not _try_acquire_scan_lock():
        log.info("Scan skipped (%s): another scan already in progress", reason)
        return "already_running"
    log.info("Scan triggered: %s", reason)
    try:
        await asyncio.to_thread(collect_and_post)
    except Exception as e:
        log.error("Scan cycle error (%s): %s", reason, e)
    finally:
        _release_scan_lock()
    return "ran"


async def _run_already_held(reason: str) -> None:
    """BC-037: run `collect_and_post` with the lock ALREADY held by the caller.
    Used by the HTTP trigger — the handler acquires sync, the background task
    just does the work and releases. Must not re-check the lock."""
    log.info("Scan triggered: %s", reason)
    try:
        await asyncio.to_thread(collect_and_post)
    except Exception as e:
        log.error("Scan cycle error (%s): %s", reason, e)
    finally:
        _release_scan_lock()


async def _scan_loop_forever() -> None:  # pragma: no cover - infinite async loop; tested live
    """Background task: run `_run_scan_once` every SCAN_INTERVAL seconds.

    The sleep is independent of scan duration — a long scan does not postpone
    the next tick, and a BC-037 manual trigger does not shift this timer
    (they share the mutex, not the schedule)."""
    while True:
        await _run_scan_once(reason="loop")
        log.info("Next scan in %ds", SCAN_INTERVAL)
        await asyncio.sleep(SCAN_INTERVAL)


def _seconds_until_next_daily_tick(target_hour: int, now_ts: float | None = None) -> float:
    """BC-093: seconds until the next wall-clock `target_hour:00` local time.

    If `now_ts` is before today's target hour, the target is today. Otherwise
    it's tomorrow. Returns a non-negative float; caller sleeps for this many
    seconds. Pulled out for unit-testability.
    """
    import datetime as _dt

    now_wall = _dt.datetime.fromtimestamp(now_ts) if now_ts is not None else _dt.datetime.now()
    today_target = now_wall.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    if now_wall >= today_target:
        today_target = today_target + _dt.timedelta(days=1)
    return max(0.0, (today_target - now_wall).total_seconds())


async def _daily_rescan_loop_forever() -> None:  # pragma: no cover - infinite async loop
    """BC-093: wake at `BEACONRY_DAILY_RESCAN_HOUR:00` local, scan, sleep ~24h.

    Independent of SCAN_INTERVAL. Catches slow drift that regular ticks
    might miss (pip installs that didn't trigger a docker event, port
    reconfigurations). Disabled when `DAILY_RESCAN_HOUR == -1`.
    """
    while True:
        delay = _seconds_until_next_daily_tick(DAILY_RESCAN_HOUR)
        log.info("Daily rescan scheduled in %.0fs (target %d:00 local)", delay, DAILY_RESCAN_HOUR)
        await asyncio.sleep(delay)
        try:
            await _run_scan_once(reason="scheduled_daily")
        except Exception as e:  # noqa: BLE001
            log.warning("BC-093 daily rescan failed: %s", e)
        # Avoid tight-loop corner case if the scan took >24h (it won't,
        # but the sleep guarantees we don't re-fire within the same hour).
        await asyncio.sleep(60)


def _docker_events_stream():  # pragma: no cover - docker.from_env wrapper
    """BC-093 + BC-103: return a docker events iterator for inventory-shape changes.

    BC-093 (original): pull / create / start → image appeared.
    BC-103 (2026-11-08): delete / destroy / die → image or container disappeared.
    Both classes feed the same debounced rescan path; hub reconciles
    absent inventory via BC-086's POST-driven threshold — but now the
    operator doesn't wait up to SCAN_INTERVAL between `docker rmi` and
    the first absent_count bump.

    Separated for test monkeypatch; the real implementation hits the
    docker daemon.
    """
    client = docker.from_env()
    return client.events(
        decode=True,
        filters={
            "type": ["image", "container"],
            "event": ["pull", "create", "start", "destroy", "die", "delete"],
        },
    )


# BC-108: counters surfaced via /healthz so operators can see
# reconnection activity without tailing logs.
_docker_events_reconnects: int = 0
_docker_events_last_reconnect_ts: int = 0


def _next_backoff(prev: int) -> int:
    """BC-108: doubling backoff clamped to [MIN, MAX]. Pulled out for unit
    testability. First call passes `prev=0` → returns MIN."""
    if prev <= 0:
        return DOCKER_EVENTS_BACKOFF_MIN_S
    return min(prev * 2, DOCKER_EVENTS_BACKOFF_MAX_S)


async def _docker_events_loop_forever() -> None:  # pragma: no cover - infinite async loop
    """BC-093 + BC-108: subscribe to docker events; trigger a (debounced)
    scan on image pull / container create+start/destroy/die/delete.
    BC-108: wrap the blocking consume in a reconnection loop with capped
    exponential backoff so a daemon restart doesn't silently kill the
    listener.

    The docker events stream is blocking → runs in a worker thread per
    attempt. Each attempt returns to the async loop so the next attempt
    can be scheduled. Pre-BC-108 the thread ended on exception and no
    new attempt was ever scheduled — scout fell back to SCAN_INTERVAL
    until restart.
    """
    global _docker_events_reconnects, _docker_events_last_reconnect_ts

    loop = asyncio.get_running_loop()
    pending: list[asyncio.TimerHandle] = []

    def _schedule_scan() -> None:
        # Cancel any already-scheduled trailing scan so a burst coalesces
        # into one.
        for h in pending:
            h.cancel()
        pending.clear()
        handle = loop.call_later(
            DOCKER_EVENTS_DEBOUNCE_S,
            lambda: asyncio.create_task(_run_scan_once(reason="docker_event")),
        )
        pending.append(handle)

    def _blocking_consume() -> bool:
        """Return True if the stream ended cleanly (no exception), False on
        error. Caller decides whether to retry."""
        try:
            for _event in _docker_events_stream():
                loop.call_soon_threadsafe(_schedule_scan)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("BC-108 docker events stream broke: %s", e)
            return False

    backoff_s = 0
    attempt = 0
    while True:
        attempt += 1
        if attempt > 1:
            log.info(
                "BC-108 docker events reconnecting (attempt=%d, backoff=%ds)",
                attempt,
                backoff_s,
            )
        clean = await loop.run_in_executor(None, _blocking_consume)
        if clean:
            log.info("BC-108 docker events stream ended cleanly — exiting loop")
            return
        # Stream raised. Sleep, then reconnect.
        backoff_s = _next_backoff(backoff_s)
        await asyncio.sleep(backoff_s)
        _docker_events_reconnects += 1
        _docker_events_last_reconnect_ts = int(time.time())
        log.warning(
            "BC-108 docker_events_reconnected total=%d attempt=%d backoff_next=%ds",
            _docker_events_reconnects,
            attempt + 1,
            _next_backoff(backoff_s),
        )


def heartbeat_once() -> bool:
    """BC-063: POST /heartbeat with host + version + health.

    Returns True on 2xx, False otherwise. No exceptions escape — a hub
    outage or auth failure is logged + swallowed; the next tick will
    try again. SBOM-free body (<200 bytes).
    """
    body = json.dumps(
        {
            "host": HOST_NAME,
            "timestamp": int(time.time()),
            "software_version": SOFTWARE_VERSION,
            "api_version": CLIENT_API_VERSION,
            "health": "ok" if _docker_socket_ok else "degraded",
            # BC-092: canary ring role (workstation | staging | production).
            # Hub updates scouts.role on each heartbeat so the production fix
            # gate sees fresh data.
            "role": HOST_ROLE,
        }
    )
    headers = {
        "Content-Type": "application/json",
        "X-Beaconry-API-Expect": str(CLIENT_API_VERSION),
    }
    # BC-075: metrics opt-in header on heartbeat path.
    if SCOUT_METRICS_ENABLED:
        headers["X-Beaconry-Metrics-Opt-In"] = "1"
    token = os.environ.get("HUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.post(f"{HUB_URL}/heartbeat", data=body, headers=headers, timeout=10)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        log.warning("heartbeat POST failed: %s", e)
        return False


async def _heartbeat_loop_forever() -> None:  # pragma: no cover - infinite async loop
    """Fire a heartbeat once immediately on startup, then every
    SCOUT_HEARTBEAT_INTERVAL_S. Interval=0 disables the loop (caller
    does not spawn the task)."""
    while True:
        await asyncio.to_thread(heartbeat_once)
        await asyncio.sleep(SCOUT_HEARTBEAT_INTERVAL_S)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log.info(
        "Beaconry Scout %s — hub: %s, interval: %ds, heartbeat: %ds",
        SCOUT_VERSION,
        HUB_URL,
        SCAN_INTERVAL,
        SCOUT_HEARTBEAT_INTERVAL_S,
    )
    # BC-107: ensure scout-state.db + pending_scans table exist before the
    # scan loop or retry sweep try to write to them.
    _init_scout_db()
    tasks: list[asyncio.Task[None]] = [
        asyncio.create_task(_scan_loop_forever()),
        # BC-107: pulls failed POSTs from pending_scans and replays through
        # `_post_scan_body` with exponential backoff.
        asyncio.create_task(_post_retry_loop_forever()),
    ]
    if SCOUT_HEARTBEAT_INTERVAL_S > 0:
        tasks.append(asyncio.create_task(_heartbeat_loop_forever()))
    else:
        log.info("Heartbeat disabled (SCOUT_HEARTBEAT_INTERVAL_S=0)")
    # BC-093: wall-clock daily rescan.
    if DAILY_RESCAN_HOUR != -1:
        log.info("Daily rescan loop enabled (target %d:00 local)", DAILY_RESCAN_HOUR)
        tasks.append(asyncio.create_task(_daily_rescan_loop_forever()))
    else:
        log.info("Daily rescan disabled (BEACONRY_DAILY_RESCAN_HOUR=-1)")
    # BC-093: docker events listener — triggers debounced scan on image
    # pull / container create+start.
    if DOCKER_EVENTS_ENABLED:
        log.info("Docker events listener enabled (debounce %ds)", DOCKER_EVENTS_DEBOUNCE_S)
        tasks.append(asyncio.create_task(_docker_events_loop_forever()))
    else:
        log.info("Docker events listener disabled (BEACONRY_DOCKER_EVENTS_ENABLED=0)")
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Beaconry Scout", version=SCOUT_VERSION, lifespan=lifespan)
app.include_router(
    build_router(
        livez_provider=lambda: "alive",
        readyz_providers=_scout_readyz_providers,
        startup_flag_getter=lambda: _started,
        healthz_summary_getter=_scout_healthz_summary,
    )
)


@app.post("/scan/trigger")
async def trigger_scan() -> "_FastAPIResponseOrDict":
    """BC-037: operator-triggered scan cycle. Returns 202 Accepted with a
    background task running `_run_scan_once`; concurrent trigger → 409."""
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    now_ts = int(time.time())
    if not _docker_socket_ok:
        raise HTTPException(
            status_code=503,
            detail="Docker socket unavailable — cannot run scan. Check scout logs.",
        )
    # Synchronous lock-acquire: flip the flag HERE so the very next request
    # — even before our background task starts — sees `_scan_in_progress=True`
    # and gets 409. Deferring the flip to inside the task would race.
    if not _try_acquire_scan_lock():
        return JSONResponse(
            status_code=409,
            content={"status": "already_running", "started_at": now_ts},
        )
    # Fire-and-forget — handler returns immediately, scan runs in the background.
    # The background task releases the lock in its finally.
    asyncio.create_task(_run_already_held(reason="manual"))
    return JSONResponse(
        status_code=202,
        content={"status": "scheduled", "started_at": now_ts},
    )


# BC-096 — baked-in SBOM. Served from image-root `/sbom.json`.
# Tests monkeypatch `scout.SBOM_PATH` to point at a tempfile. Same
# shape as hub's /sbom endpoint.
SBOM_PATH = "/sbom.json"


@app.get("/sbom")
async def sbom() -> "_FastAPIResponseOrDict":
    from fastapi.responses import FileResponse, JSONResponse

    if not os.path.isfile(SBOM_PATH):
        return JSONResponse(
            status_code=503,
            content={
                "status": "sbom_unavailable",
                "detail": (
                    "no /sbom.json baked into this image (dev/host run "
                    "without a built image — see BC-096)"
                ),
            },
        )
    return FileResponse(SBOM_PATH, media_type="application/json")


# Pure type alias; keeps the return annotation parseable without pulling in
# fastapi.Response at module import time.
_FastAPIResponseOrDict = object  # noqa: N816


def main():  # pragma: no cover - uvicorn + asyncio entry; tested live
    once = "--once" in sys.argv
    if once:
        collect_and_post()
        return
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT, log_level="warning")


if __name__ == "__main__":
    main()
