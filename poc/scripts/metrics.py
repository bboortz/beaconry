"""BC-075 — beaconry metrics: usability + feature-usage reports.

Reads `$DATA_DIR/audit.log` + `$DATA_DIR/beaconry.db` and produces
one of three reports:

    beaconry metrics usability    # key-user lens
    beaconry metrics features     # developer lens
    beaconry metrics all          # both (default)

Flags:
    --json                        # machine-readable
    --since 7d                    # analysis window (default 7d; use 1d, 6h, 30m)

Two personas, same data:
- **Key user** asks "is this tool worth it?" (action rate, time-to-fix,
  regression rate, ignored rate).
- **Developer** asks "which features earn their keep?" (digest fires,
  heartbeat health, manual-fix engagement, reconcile saves).

Health signals (threshold breaches) are the responsibility of
`soak.py`; this script just measures.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

# ── Window parsing ──────────────────────────────────────────────────────────

_SINCE_RE = re.compile(r"^(?P<n>\d+)(?P<unit>[smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_window(since: str) -> int:
    """Parse 7d/24h/30m/60s into seconds. Raises ValueError on garbage."""
    m = _SINCE_RE.match(since.strip().lower())
    if not m:
        raise ValueError(f"invalid --since {since!r}; expected forms like 7d, 24h, 30m, 60s")
    return int(m.group("n")) * _UNIT_SECONDS[m.group("unit")]


# ── Paths ───────────────────────────────────────────────────────────────────


def _resolve_data_dir() -> Path:
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        return Path(data_dir)
    return Path(__file__).resolve().parents[2] / ".beaconry-cache"


# ── Audit log reader ────────────────────────────────────────────────────────


def read_audit_events(audit_path: Path, *, since_ts: int | None = None) -> list[dict]:
    """Return events whose `ts` is ≥ since_ts (if given). Malformed lines dropped."""
    if not audit_path.exists():
        return []
    out: list[dict] = []
    for line in audit_path.read_text().splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since_ts is not None:
            ts = ev.get("ts")
            if not isinstance(ts, int) or ts < since_ts:
                continue
        out.append(ev)
    return out


# ── Usability lens (key user) ───────────────────────────────────────────────


def _finding_lifecycle(events: list[dict]) -> dict[str, dict]:
    """Return {finding_id: {first_notify_ts, first_action_ts, final_status, ...}}.

    First action = first `set_status` audit event or terminal transition
    (`auto_closed`, `regressed`). Ignored = status still open at end of
    window AND no action event ever fired.
    """
    by_fid: dict[str, dict] = defaultdict(
        lambda: {
            "first_notify_ts": None,
            "first_action_ts": None,
            "actions": [],
            "reminders": 0,
            "fixed_verified": False,
            "regressed": False,
        }
    )
    for ev in events:
        fid = ev.get("finding_id")
        if not fid:
            continue
        ts = ev.get("ts")
        name = ev.get("event")
        row = by_fid[fid]
        if name == "notify_sent" and row["first_notify_ts"] is None:
            row["first_notify_ts"] = ts
        elif name in {"status_change", "auto_closed", "regressed"}:
            if row["first_action_ts"] is None:
                row["first_action_ts"] = ts
            row["actions"].append((ts, ev.get("new_status") or name))
            if ev.get("new_status") == "fixed_verified" or name == "auto_closed":
                row["fixed_verified"] = True
            if name == "regressed":
                row["regressed"] = True
        elif name == "manual_fix_reminder_sent":
            row["reminders"] += 1
    return by_fid


def _percentiles(values: list[int]) -> dict[str, float]:
    if not values:
        return {"p50": 0, "p75": 0, "p95": 0}
    s = sorted(values)
    return {
        "p50": statistics.median(s),
        "p75": s[int(0.75 * (len(s) - 1))],
        "p95": s[int(0.95 * (len(s) - 1))],
    }


def usability_metrics(events: list[dict], *, now_ts: int) -> dict:
    """Compute the key-user lens: did beaconry help them?"""
    notify_sent = [e for e in events if e.get("event") == "notify_sent"]
    notify_failed = [e for e in events if e.get("event") == "notify_sent_failed"]
    reminders = [e for e in events if e.get("event") == "manual_fix_reminder_sent"]
    regressed = [e for e in events if e.get("event") == "regressed"]

    sent_total = len(notify_sent) + len(notify_failed)
    delivery_success = len(notify_sent) / sent_total if sent_total else 1.0

    lifecycle = _finding_lifecycle(events)
    actioned = [f for f in lifecycle.values() if f["first_action_ts"] is not None]
    action_rate = len(actioned) / len(lifecycle) if lifecycle else 0.0
    fixed = [f for f in actioned if f["fixed_verified"]]
    fix_rate = len(fixed) / len(actioned) if actioned else 0.0
    # Snooze/accept: at least one action but not fixed_verified
    snooze_accept = [f for f in actioned if not f["fixed_verified"] and not f["regressed"]]
    snooze_accept_rate = len(snooze_accept) / len(actioned) if actioned else 0.0

    # Ignored: notify_sent > 24h ago AND no action.
    cutoff_24h = now_ts - 86400
    ignored = [
        f
        for f in lifecycle.values()
        if f["first_notify_ts"]
        and f["first_notify_ts"] < cutoff_24h
        and f["first_action_ts"] is None
    ]
    ignored_rate = len(ignored) / len(lifecycle) if lifecycle else 0.0

    # Time-to-first-action distribution.
    ttas = [
        f["first_action_ts"] - f["first_notify_ts"]
        for f in actioned
        if f["first_notify_ts"] and f["first_action_ts"]
    ]
    tta_percentiles = _percentiles(ttas)

    # Reminder effectiveness: of findings that got reminders, how many were eventually fixed_verified.
    with_reminders = [f for f in lifecycle.values() if f["reminders"] > 0]
    reminded_and_fixed = [f for f in with_reminders if f["fixed_verified"]]
    reminder_effectiveness = (
        len(reminded_and_fixed) / len(with_reminders) if with_reminders else 0.0
    )

    return {
        "alerts_delivered": len(notify_sent),
        "alerts_failed": len(notify_failed),
        "delivery_success_rate": delivery_success,
        "unique_findings_alerted": len(lifecycle),
        "action_rate": action_rate,
        "fix_rate": fix_rate,
        "snooze_or_accept_rate": snooze_accept_rate,
        "ignored_rate": ignored_rate,
        "time_to_action_s": tta_percentiles,
        "regression_count": len(regressed),
        "regression_rate_per_alert": (len(regressed) / len(notify_sent) if notify_sent else 0.0),
        "reminder_count": len(reminders),
        "reminder_effectiveness": reminder_effectiveness,
    }


# ── Features lens (developer) ───────────────────────────────────────────────


def features_metrics(events: list[dict], *, now_ts: int) -> dict:
    """Compute the developer lens: which features earn their keep?"""
    digest_sent = [e for e in events if e.get("event") == "digest_sent"]
    digest_throttled = [e for e in events if e.get("event") == "digest_throttled"]
    heartbeat = [e for e in events if e.get("event") == "heartbeat_received"]
    scan_received = [e for e in events if e.get("event") == "scan_received"]
    manual_fix_claimed = [e for e in events if e.get("event") == "manual_fix_claimed"]
    partial_inv = [e for e in events if e.get("event") == "reconcile_skipped_partial_inventory"]
    invalid_grype = [e for e in events if e.get("event") == "invalid_grype_match"]
    notify_sent = [e for e in events if e.get("event") == "notify_sent"]

    # Hosts in last 7 days (window-bounded; events are already filtered to since-cutoff).
    week_cutoff = now_ts - 7 * 86400
    hosts_7d = {
        e.get("host")
        for e in events
        if e.get("ts", 0) >= week_cutoff
        and e.get("event") in {"scan_received", "heartbeat_received"}
        and e.get("host")
    }

    # Heartbeat gaps per host.
    gaps_per_host: dict[str, int] = {}
    hb_by_host: dict[str, list[int]] = defaultdict(list)
    for ev in heartbeat:
        h = ev.get("host")
        t = ev.get("ts")
        if h and isinstance(t, int):
            hb_by_host[h].append(t)
    for host, times in hb_by_host.items():
        times.sort()
        max_gap = 0
        for i in range(1, len(times)):
            gap = times[i] - times[i - 1]
            if gap > max_gap:
                max_gap = gap
        gaps_per_host[host] = max_gap

    # Scan cadence: median delta between consecutive scan_received.
    scan_times: list[int] = sorted(ts for e in scan_received if isinstance(ts := e.get("ts"), int))
    scan_deltas = [scan_times[i] - scan_times[i - 1] for i in range(1, len(scan_times))]
    scan_cadence_median = statistics.median(scan_deltas) if scan_deltas else 0

    return {
        "digest_sent": len(digest_sent),
        "digest_throttled": len(digest_throttled),
        "digest_fire_ratio": (
            len(digest_sent) / (len(digest_sent) + len(digest_throttled))
            if (digest_sent or digest_throttled)
            else 0.0
        ),
        "distinct_hosts_7d": len(hosts_7d),
        "heartbeat_events": len(heartbeat),
        "max_heartbeat_gap_s_per_host": gaps_per_host,
        "manual_fix_claimed": len(manual_fix_claimed),
        "manual_fix_uptake_vs_alerts": (
            len(manual_fix_claimed) / len(notify_sent) if notify_sent else 0.0
        ),
        "reconcile_skipped_partial_inventory": len(partial_inv),
        "invalid_grype_matches": len(invalid_grype),
        "scan_received_total": len(scan_received),
        "scan_cadence_median_s": scan_cadence_median,
    }


# ── Rendering ───────────────────────────────────────────────────────────────


def _fmt_percent(v: float) -> str:
    return f"{v * 100:.1f}%"


def _fmt_duration(s: int | float) -> str:
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s / 60:.1f}m"
    if s < 86400:
        return f"{s / 3600:.1f}h"
    return f"{s / 86400:.1f}d"


def render_usability(m: dict) -> str:
    tta = m["time_to_action_s"]
    lines = [
        'Usability (key user — "is this tool worth it?")',
        "─" * 60,
        f"  Alerts delivered           {m['alerts_delivered']} "
        f"(delivery success {_fmt_percent(m['delivery_success_rate'])})",
        f"  Unique findings alerted    {m['unique_findings_alerted']}",
        f"  Action rate                {_fmt_percent(m['action_rate'])}"
        f"  (operator engaged with the alert)",
        f"  Fix rate                   {_fmt_percent(m['fix_rate'])}"
        f"  (of actioned, landed on fixed)",
        f"  Snooze/accept rate         {_fmt_percent(m['snooze_or_accept_rate'])}"
        f"  (of actioned, deferred or dismissed)",
        f"  Ignored rate               {_fmt_percent(m['ignored_rate'])}"
        f"  (notify_sent > 24h ago, no action)",
        f"  Time-to-action p50/p75/p95 {_fmt_duration(tta['p50'])} / "
        f"{_fmt_duration(tta['p75'])} / {_fmt_duration(tta['p95'])}",
        f"  Regression count           {m['regression_count']} "
        f"({_fmt_percent(m['regression_rate_per_alert'])} per alert)",
        f"  Reminders sent             {m['reminder_count']}",
        f"  Reminder effectiveness     {_fmt_percent(m['reminder_effectiveness'])}"
        f"  (reminded → fixed_verified)",
    ]
    return "\n".join(lines)


def render_features(m: dict) -> str:
    gaps = m["max_heartbeat_gap_s_per_host"]
    worst_gap = max(gaps.values()) if gaps else 0
    lines = [
        'Features (developer — "which features earn their keep?")',
        "─" * 60,
        f"  Digest fire ratio          {_fmt_percent(m['digest_fire_ratio'])} "
        f"({m['digest_sent']} fired / {m['digest_throttled']} throttled)",
        f"  Distinct hosts (last 7d)   {m['distinct_hosts_7d']}",
        f"  Heartbeat events           {m['heartbeat_events']}",
        f"  Worst heartbeat gap        {_fmt_duration(worst_gap)}",
        f"  Scan cadence (median)      {_fmt_duration(m['scan_cadence_median_s'])}",
        f"  Manual-fix claimed         {m['manual_fix_claimed']} "
        f"(uptake {_fmt_percent(m['manual_fix_uptake_vs_alerts'])} of alerts)",
        f"  Partial-inventory saves    {m['reconcile_skipped_partial_inventory']}  "
        f"(BC-067 guard firings)",
        f"  Invalid grype matches      {m['invalid_grype_matches']}  "
        f"(data loss — investigate if > 0)",
    ]
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="beaconry metrics",
        description="Report usability + feature-usage metrics from audit.log.",
    )
    parser.add_argument(
        "lens",
        nargs="?",
        default="all",
        choices=["usability", "features", "all"],
        help="which lens to report (default: all)",
    )
    parser.add_argument(
        "--since",
        default="7d",
        help="analysis window: 7d / 24h / 30m / 60s (default: 7d)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        window_s = parse_window(args.since)
    except ValueError as e:
        print(f"metrics: {e}", file=sys.stderr)
        return 2

    audit_path = _resolve_data_dir() / "audit.log"
    if not audit_path.exists():
        print(
            f"metrics: audit log not found at {audit_path} "
            f"(set DATA_DIR to override, or run `make demo` to seed)",
            file=sys.stderr,
        )
        return 2

    now_ts = int(time.time())
    events = read_audit_events(audit_path, since_ts=now_ts - window_s)

    out: dict = {"since_s": window_s, "events": len(events)}
    if args.lens in ("usability", "all"):
        out["usability"] = usability_metrics(events, now_ts=now_ts)
    if args.lens in ("features", "all"):
        out["features"] = features_metrics(events, now_ts=now_ts)

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    print(f"Window: last {_fmt_duration(window_s)}, {len(events)} events\n")
    if "usability" in out:
        print(render_usability(out["usability"]))
        print()
    if "features" in out:
        print(render_features(out["features"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
