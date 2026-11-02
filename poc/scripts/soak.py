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
        "regressed",  # BC-031 fix regression (critical)
        "set_status_failed",  # operator action failed
        # BC-075: `manual_fix_reminder_sent` and
        # `reconcile_skipped_partial_inventory` moved out of per-event flags
        # into _detect_anomalies() — treated as patterns over the window
        # (3+ without fix, > 20% rate) so single occurrences don't spam
        # the operator with false alarms.
    }
)

# BC-075: anomaly thresholds — a developer would deny merging if any
# of these fire. Different from hard errors above (which flag EACH
# occurrence); thresholds flag PATTERNS over the window.
_NOTIFY_FAILURE_RATIO_THRESHOLD = 0.10  # notifier broken
_PARTIAL_INV_RATIO_THRESHOLD = 0.20  # scout flaky
_REMINDER_STORM_THRESHOLD = 3  # same finding, 3+ reminders, not fixed


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


def _detect_anomalies(events: list[dict]) -> list[str]:
    """BC-075: pattern-level anomalies that would make a developer deny
    the merge. Returns a list of human-readable anomaly strings; empty
    = clean. Different from `_ERROR_EVENTS` (per-event hard errors);
    these inspect patterns over the whole window.
    """
    from collections import defaultdict

    anomalies: list[str] = []
    notify_sent = sum(1 for e in events if e.get("event") == "notify_sent")
    notify_failed = sum(1 for e in events if e.get("event") == "notify_sent_failed")
    scan_received = sum(1 for e in events if e.get("event") == "scan_received")
    partial_inv = sum(1 for e in events if e.get("event") == "reconcile_skipped_partial_inventory")

    # Notifier health: > 10% failure ratio
    total_notify = notify_sent + notify_failed
    if total_notify >= 10:  # don't flag on tiny samples
        failure_rate = notify_failed / total_notify
        if failure_rate > _NOTIFY_FAILURE_RATIO_THRESHOLD:
            anomalies.append(
                f"notifier failure rate {failure_rate:.0%} "
                f"({notify_failed}/{total_notify}) exceeds "
                f"{_NOTIFY_FAILURE_RATIO_THRESHOLD:.0%} threshold — Telegram token revoked?"
            )

    # Partial-inventory saves: > 20% of scans tripping BC-067 → scout flaky
    if scan_received >= 5:
        partial_rate = partial_inv / scan_received
        if partial_rate > _PARTIAL_INV_RATIO_THRESHOLD:
            anomalies.append(
                f"partial-inventory save rate {partial_rate:.0%} "
                f"({partial_inv}/{scan_received}) exceeds "
                f"{_PARTIAL_INV_RATIO_THRESHOLD:.0%} threshold — scout glitching"
            )

    # Reminder storm: 3+ `manual_fix_reminder_sent` for the same finding
    # without a terminal transition (operator ignoring the nag).
    reminders_per_finding: dict[str, int] = defaultdict(int)
    fixed_finding_ids: set[str] = set()
    for ev in events:
        name = ev.get("event")
        fid = ev.get("finding_id")
        if not fid:
            continue
        if name == "manual_fix_reminder_sent":
            reminders_per_finding[fid] += 1
        elif name in {"auto_closed"} or (
            name == "status_change" and ev.get("new_status") == "fixed_verified"
        ):
            fixed_finding_ids.add(fid)
    stuck = [
        fid
        for fid, count in reminders_per_finding.items()
        if count >= _REMINDER_STORM_THRESHOLD and fid not in fixed_finding_ids
    ]
    if stuck:
        anomalies.append(
            f"reminder storm — {len(stuck)} finding(s) with "
            f"≥{_REMINDER_STORM_THRESHOLD} reminders and no fix "
            f"(operator may be ignoring nags; first: {stuck[0]})"
        )

    return anomalies


def _detect_pipeline_silence(
    events: list[dict], *, window_s: int, scan_interval_s: int = 300
) -> list[str]:
    """BC-075: pipeline-silence anomaly — zero `scan_received` for > 2× the
    expected cadence. Treated as its own check because it has to meaningfully
    consider the window size (don't flag silence in a 60s soak window)."""
    scan_cadence_window = scan_interval_s * 2
    if window_s < scan_cadence_window * 2:
        return []  # window too short to meaningfully assert silence
    if any(e.get("event") == "scan_received" for e in events):
        return []
    return [
        f"zero `scan_received` events in window — pipeline silent "
        f"(expected at least one scan every {scan_interval_s}s)"
    ]


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
    # BC-075: anomaly thresholds (pattern-level signals).
    anomalies = _detect_anomalies(events)
    anomalies += _detect_pipeline_silence(events, window_s=window_s)

    since_human = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since_ts))
    # BC-074: render window in the chunkiest honest unit so a 24h run
    # doesn't say "1440m" and a 30m run doesn't say "0h".
    if window_s >= 3600:
        window_h = window_s // 3600
        window_label = f"{window_h}h" if window_s % 3600 == 0 else f"{window_s / 3600:.1f}h"
    else:
        window_label = f"{window_s // 60}m"
    print(f"Scanning {audit_path} (last {window_label}, since {since_human}; {len(events)} events)")

    # Both lists empty → clean exit.
    if not errors and not anomalies:
        print(f"✓ 0 error events + 0 anomalies in last {window_label} — stack looks healthy")
        return 0

    # BC-075: section the output so operator can tell errors from thresholds.
    if errors:
        print(f"⚠ {len(errors)} error event(s) in last {window_label}:")
        for ev in sorted(errors, key=lambda e: e.get("ts", 0)):
            print(_format_event(ev))
    if anomalies:
        print(f"⚠ {len(anomalies)} anomaly threshold(s) in last {window_label}:")
        for a in anomalies:
            print(f"  • {a}")
    return 1


def main() -> int:  # pragma: no cover - entry point
    return soak()


if __name__ == "__main__":
    sys.exit(main())
