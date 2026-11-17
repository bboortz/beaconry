"""BC-029 append-only audit log.

Every operator-visible state change — new finding ingested, status
transition, fix attempt, probe result — gets appended as a JSONL line
to `${DATA_DIR}/audit.log`.

Append-only by design: never truncate, never rewrite, never delete.
Operators can `tail -f` the file during an incident, or grep for a
specific CVE id weeks later to see what the system actually did.

Format: one JSON object per line:
  {"ts": 1793427500, "event": "status_change", "finding_id": "...", ...}

Design choices:
  - JSONL (not SQLite): streams-friendly for `tail -f`, grep-friendly,
    unaffected by DB locks during long scans.
  - int unix timestamp (not ISO): operators are shell-first; `awk '$2 > NOW-3600'`
    works without a time library.
  - Fire-and-forget: a failed audit write logs a warning but never raises
    — audit is observability, not control-flow.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("hub.audit")

# BC-115: sticky degraded-mode flag + counters so `/healthz` can surface
# audit-log write failures (e.g. disk full) without changing the
# never-raise contract. Pre-BC-115 a failed write logged a warning and
# vanished from operator visibility; during an incident (disk full) the
# audit trail could be silently losing events exactly when operators
# needed it most.
_degraded: bool = False
_last_error: str = ""
_write_failures: int = 0
_write_failures_since_degraded: int = 0

# BC-132: post-append hook registry. Hub registers a callback at startup
# that writes each event to the SQLite audit_index sidecar so queries
# are indexed-fast regardless of log size. Keeping the index in a
# separate module via a hook means audit.py stays pure JSONL (no DB
# dependency); the hook is best-effort (exceptions logged + swallowed)
# so an index-write failure never prevents the durable JSONL write.
_post_append_hooks: list = []  # list[Callable[[dict], None]]


def register_post_append_hook(hook) -> None:  # type: ignore[no-untyped-def]
    """BC-132: register a callback invoked after every successful
    `append()` with the full event dict. Hub uses this to mirror
    writes into the SQLite audit_index sidecar."""
    _post_append_hooks.append(hook)


def clear_post_append_hooks() -> None:
    """BC-132: test helper — drops any registered hooks. Production
    calls this only at hub shutdown."""
    _post_append_hooks.clear()


def _audit_path() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data")) / "audit.log"


def _mark_write_succeeded() -> None:
    """BC-115: clear sticky degraded state on first successful write after
    a failure. `_write_failures` is cumulative — intentionally sticky
    across recoveries so operators see the tail count in `/healthz`."""
    global _degraded, _last_error, _write_failures_since_degraded
    if _degraded:
        log.info(
            "BC-115: audit write recovered (had failed %d time(s) since last ok)",
            _write_failures_since_degraded,
        )
        _degraded = False
        _last_error = ""
        _write_failures_since_degraded = 0


def _mark_write_failed(exc: OSError) -> None:
    """BC-115: set sticky degraded state. Also increments counters."""
    global _degraded, _last_error, _write_failures, _write_failures_since_degraded
    _degraded = True
    _last_error = f"{type(exc).__name__}: {exc}"[:200]
    _write_failures += 1
    _write_failures_since_degraded += 1


def status() -> dict:
    """BC-115: snapshot of audit-log health for `/healthz`.

    Returns:
      - audit_degraded: True iff the last write attempt failed
      - audit_write_failures: cumulative failed-write count since hub start
      - audit_last_error: stringified exception type + message (truncated 200c)
    """
    return {
        "audit_degraded": _degraded,
        "audit_write_failures": _write_failures,
        "audit_last_error": _last_error,
    }


def append(event_type: str, **details: Any) -> None:
    """Append one event to the audit log. Fire-and-forget: never raises.

    BC-115: tracks write success/failure in a sticky module-level flag
    so `/healthz` can surface silent audit-log degradation (e.g.
    disk full). The never-raise contract is preserved — the caller
    still sees no exception.
    """
    event: dict[str, Any] = {"ts": int(time.time()), "event": event_type}
    event.update(details)
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(event, default=str) + "\n")
        _mark_write_succeeded()
    except OSError as exc:
        log.warning("audit write failed (%s): %s", exc, event)
        _mark_write_failed(exc)
    # BC-132: notify post-append hooks (e.g. SQLite index writer).
    # Best-effort — an index-write failure must not surface to the
    # caller because the JSONL durable write already completed.
    for hook in _post_append_hooks:
        try:
            hook(event)
        except Exception as exc:  # noqa: BLE001 — never raise from audit
            log.warning("audit post-append hook failed: %s", exc)


def read_events(limit: int | None = None) -> list[dict]:
    """Read the audit log, newest-first. Returns [] if no log exists."""
    path = _audit_path()
    if not path.exists():
        return []
    events: list[dict] = []
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    events.reverse()
    return events[:limit] if limit is not None else events
