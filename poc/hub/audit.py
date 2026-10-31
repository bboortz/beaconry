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


def _audit_path() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data")) / "audit.log"


def append(event_type: str, **details: Any) -> None:
    """Append one event to the audit log. Fire-and-forget: never raises."""
    event: dict[str, Any] = {"ts": int(time.time()), "event": event_type}
    event.update(details)
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except OSError as exc:
        log.warning("audit write failed (%s): %s", exc, event)


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
