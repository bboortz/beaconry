"""BC-069 — `make soak`: audit log health check over the last N seconds.

Reads $DATA_DIR/audit.log (default resolves to `.beaconry-cache/` for
local dev, `/data/` in container), filters events within SOAK_WINDOW_S
(default 3600 = 1h), and flags any that match the "something broke"
set. Exit codes:

  0 — clean (zero error events)
  1 — error events present (operator should look)
  2 — can't read audit log

The error-event set is a module-level constant so operators can
grep the source to see what's considered worrying. Add a new audit
event in hub.py? Decide here whether it belongs in `_ERROR_EVENTS`.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# BC-069: audit events that indicate something went wrong. Keep this
# curated — adding every event flattens the signal; omitting real
# failures hides them.
_ERROR_EVENTS = frozenset(
    {
        "notify_sent_failed",  # Telegram or adapter dispatch failed
        "invalid_grype_match",  # BC-067 malformed CVE data
        "reconcile_skipped_partial_inventory",  # BC-067 scout glitch
        "regressed",  # BC-031 fix regression (critical)
        "set_status_failed",  # operator action failed
        "manual_fix_reminder_sent",  # BC-066 operator never acted on claim
    }
)


def _resolve_audit_path() -> Path:
    """Return the audit log location per BC-029 convention."""
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        return Path(data_dir) / "audit.log"
    # Fall back to the local-dev cache (`make demo` path).
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / ".beaconry-cache" / "audit.log"


def _read_window(path: Path, now: int, window_s: int) -> list[dict]:
    """Return events whose `ts` falls within [now - window_s, now].
    Malformed lines are dropped (same forgiving contract audit.append has)."""
    cutoff = now - window_s
    out: list[dict] = []
    for line in path.read_text().splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = ev.get("ts")
        if isinstance(ts, int) and ts >= cutoff:
            out.append(ev)
    return out


def _format_event(ev: dict) -> str:
    ts = ev.get("ts", 0)
    # BC-074: 24h window warrants the date too — HH:MM:SS alone is
    # ambiguous across the midnight boundary.
    stamp = time.strftime("%m-%d %H:%M:%S", time.localtime(ts)) if isinstance(ts, int) else "?"
    name = ev.get("event", "?")
    details = []
    for k in ("finding_id", "cve_id", "host", "artifact"):
        v = ev.get(k)
        if v:
            details.append(f"{k}={v}")
    return f"  {stamp}  {name:<40} {' '.join(details)}"


def soak(now: int | None = None) -> int:
    """Entry point. Returns the exit code."""
    audit_path = _resolve_audit_path()
    # BC-074: default window is 24h — the natural staging soak length
    # (deploy to hetzner-staging day N → `make soak` day N+1 → promote).
    # Operators who want the pre-BC-074 1h window can override:
    # `SOAK_WINDOW_S=3600 make soak`.
    window_s = int(os.environ.get("SOAK_WINDOW_S", "86400"))
    now_ts = now if now is not None else int(time.time())
    since_ts = now_ts - window_s

    if not audit_path.exists():
        print(
            f"soak: audit log not found at {audit_path} "
            f"(set DATA_DIR to override, or run `make demo` to seed)",
            file=sys.stderr,
        )
        return 2

    try:
        events = _read_window(audit_path, now_ts, window_s)
    except OSError as e:
        print(f"soak: cannot read {audit_path}: {e}", file=sys.stderr)
        return 2

    errors = [e for e in events if e.get("event") in _ERROR_EVENTS]
    since_human = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since_ts))
    # BC-074: render window in the chunkiest honest unit so a 24h run
    # doesn't say "1440m" and a 30m run doesn't say "0h".
    if window_s >= 3600:
        window_h = window_s // 3600
        window_label = f"{window_h}h" if window_s % 3600 == 0 else f"{window_s / 3600:.1f}h"
    else:
        window_label = f"{window_s // 60}m"
    print(f"Scanning {audit_path} (last {window_label}, since {since_human}; {len(events)} events)")
    if not errors:
        print(f"✓ 0 error events in last {window_label} — stack looks healthy")
        return 0
    print(f"⚠ {len(errors)} error event(s) in last {window_label}:")
    # Preserve chronological order for operator readability.
    for ev in sorted(errors, key=lambda e: e.get("ts", 0)):
        print(_format_event(ev))
    return 1


def main() -> int:  # pragma: no cover - entry point
    return soak()


if __name__ == "__main__":
    sys.exit(main())
