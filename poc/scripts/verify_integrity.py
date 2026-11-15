"""BC-122: `beaconry verify-integrity` — walk the hub's audit log and
assert the pipeline is running losslessly.

Reports every `scan_integrity` audit event with pass/fail based on:
1. `transfer_match` — scout's claimed transfer_hash matched hub's recompute.
   (None → legacy scout; tracked separately, not a failure.)
2. Drop accounting — `n_candidates` equals the sum of persisted + every
   filter bucket (`n_dedup + n_capped + n_dropped_in_save +
   n_filtered_other`). An unexplained drop means a finding slipped out
   of the pipeline without a known reason — the exact invisible-loss
   class BC-122 was built to surface.

Exit codes:
  0 — every iteration verified clean (legacy nulls are tolerated)
  1 — at least one hard mismatch or unexplained drop
  2 — audit log unreadable

Operator usage:
  beaconry verify-integrity                # all available iterations
  beaconry verify-integrity --since 24h    # last 24h only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Data model ────────────────────────────────────────────────────────────


@dataclass
class IntegrityReport:
    verified_count: int = 0
    unknown_count: int = 0
    mismatches: list[dict] = field(default_factory=list)
    unexplained_drops: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.verified_count + self.unknown_count + len(self.mismatches)

    @property
    def exit_code(self) -> int:
        return 0 if (not self.mismatches and not self.unexplained_drops) else 1


# ── Report builder (unit-testable, pure function of events) ──────────────


def _drop_sum(ev: dict) -> int:
    return (
        int(ev.get("n_dedup") or 0)
        + int(ev.get("n_capped") or 0)
        + int(ev.get("n_dropped_in_save") or 0)
        + int(ev.get("n_filtered_other") or 0)
    )


def _is_unexplained_drop(ev: dict) -> bool:
    candidates = ev.get("n_candidates")
    persisted = ev.get("n_persisted")
    if candidates is None or persisted is None:
        return False
    return int(candidates) != int(persisted) + _drop_sum(ev)


def build_report(events: list[dict]) -> IntegrityReport:
    """Pure function: given a list of audit events (any mix), produce
    the integrity report. Tolerates and ignores events of unrelated types
    so callers can pass the raw audit stream."""
    report = IntegrityReport()
    for ev in events:
        if ev.get("event") != "scan_integrity":
            continue
        match = ev.get("transfer_match")
        if match is True:
            report.verified_count += 1
        elif match is None:
            # Legacy pre-BC-122 scout — tracked as unknown, not a failure.
            report.unknown_count += 1
        else:
            report.mismatches.append(ev)
        if _is_unexplained_drop(ev):
            report.unexplained_drops.append(ev)
    return report


# ── Audit-log ingest ──────────────────────────────────────────────────────


def _parse_since(spec: str | None) -> int | None:
    """`24h` → unix ts 24h ago. `60m` → 60 min ago. Returns None for no filter."""
    if not spec:
        return None
    import time as _time

    m = re.fullmatch(r"(\d+)\s*([smhd])", spec.strip())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    secs = n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return int(_time.time()) - secs


def load_events(audit_path: Path, since_ts: int | None = None) -> list[dict]:
    """Load audit events, optionally filtered to events at/after `since_ts`.
    Events without a parseable `ts` are included (defensive)."""
    events: list[dict] = []
    if not audit_path.exists():
        return events
    for line in audit_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since_ts is not None:
            raw_ts = ev.get("ts")
            try:
                import datetime as _dt

                event_ts = int(_dt.datetime.fromisoformat(str(raw_ts)).timestamp())
                if event_ts < since_ts:
                    continue
            except (ValueError, TypeError):
                # Unparseable ts → include defensively; verifier's role is
                # to surface integrity, not drop data.
                pass
        events.append(ev)
    return events


# ── Rendering ──────────────────────────────────────────────────────────────


def _format_report(report: IntegrityReport) -> str:
    lines: list[str] = []
    if report.total == 0:
        return "No scan_integrity events in audit log."
    status = "✓" if report.exit_code == 0 else "✗"
    headline = (
        f"{status} {report.verified_count} verified · "
        f"{report.unknown_count} unknown (pre-BC-122) · "
        f"{len(report.mismatches)} mismatch · "
        f"{len(report.unexplained_drops)} unexplained drop"
    )
    lines.append(headline)
    if report.mismatches:
        lines.append("\nTransfer-hash mismatches:")
        for ev in report.mismatches:
            lines.append(
                f"  iteration={ev.get('iteration_hash')} host={ev.get('host')}"
                f" claimed={ev.get('transfer_hash_claimed')}"
                f" computed={ev.get('transfer_hash_computed')}"
            )
    if report.unexplained_drops:
        lines.append("\nUnexplained processing drops (candidates ≠ persisted + known filters):")
        for ev in report.unexplained_drops:
            lines.append(
                f"  iteration={ev.get('iteration_hash')} host={ev.get('host')}"
                f" candidates={ev.get('n_candidates')} persisted={ev.get('n_persisted')}"
                f" drops=d{ev.get('n_dedup')}/c{ev.get('n_capped')}"
                f"/s{ev.get('n_dropped_in_save')}/o{ev.get('n_filtered_other')}"
            )
    return "\n".join(lines)


# ── CLI entry ──────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="beaconry verify-integrity",
        description="Verify the scout→hub pipeline is running losslessly.",
    )
    p.add_argument(
        "--since",
        default=None,
        help="Only consider events newer than this (e.g. 24h, 60m, 7d).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    data_dir = Path(os.environ.get("DATA_DIR", ".beaconry-cache"))
    audit_path = data_dir / "audit.log"
    if not audit_path.exists():
        print(f"✗ audit log not found at {audit_path}", file=sys.stderr)
        return 2
    since_ts = _parse_since(args.since)
    events = load_events(audit_path, since_ts=since_ts)
    report = build_report(events)
    print(_format_report(report))
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
