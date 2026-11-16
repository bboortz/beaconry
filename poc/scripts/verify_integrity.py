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
    """Sum of every "finding was dropped from persistence" bucket.

    Pre-BC-128 events include `n_capped` (the suppressed-from-notify
    tail that was ALSO dropped from the DB). Post-BC-128 events rename
    it to `n_suppressed_from_notify` AND those findings are persisted
    (so they're NOT a drop). We tolerate both event shapes:
      - If `n_suppressed_from_notify` is present, we're on a BC-128
        event — the value is not a drop, don't add it.
      - Else if `n_capped` is present, we're on a pre-BC-128 event —
        count it as a drop (matches the old balance invariant).
    """
    suppressed_term = 0 if "n_suppressed_from_notify" in ev else int(ev.get("n_capped") or 0)
    return (
        int(ev.get("n_dedup") or 0)
        + suppressed_term
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


def fetch_events_via_http(cfg, since: str | None) -> list[dict]:  # type: ignore[no-untyped-def]
    """BC-126: fetch scan_integrity events from the hub's /audit/integrity
    endpoint. `since` is the raw CLI spec ("24h", "60m", …) — the hub
    parses it with the same grammar the local path uses.

    Raises on HTTP failure so `main()` can surface a friendly error +
    return exit code 2 (matches the local-file not-found contract).
    """
    import urllib.parse

    from poc.scripts.cli_http import http_get

    url = cfg.hub_url.rstrip("/") + "/audit/integrity"
    qs: dict[str, str] = {}
    if since:
        qs["since"] = since
    if qs:
        url += "?" + urllib.parse.urlencode(qs)
    rows = http_get(url, token=cfg.hub_token)
    if not isinstance(rows, list):
        return []
    return rows


def _running_compose_hub() -> bool:
    """BC-126: mirrors poc/scripts/findings.py::_running_compose_hub so
    the dev-compose path exec's the verifier inside the hub container."""
    import subprocess

    hub_container = os.environ.get("BEACONRY_HUB_CONTAINER", "beaconry-dev-hub")
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format={{.State.Running}}", hub_container],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _run_in_container(since: str | None) -> int:
    """BC-126: exec verify_integrity inside the dev-compose hub so the
    operator hits the compose volume's audit.log, not a non-existent
    local file. Mirrors the pattern beaconry findings uses."""
    import subprocess

    hub_container = os.environ.get("BEACONRY_HUB_CONTAINER", "beaconry-dev-hub")
    cmd = [
        "docker",
        "exec",
        "-e",
        "DATA_DIR=/data",
        hub_container,
        "python3",
        "-m",
        "poc.scripts.verify_integrity",
    ]
    if since:
        cmd += ["--since", since]
    r = subprocess.run(cmd, check=False)
    return r.returncode


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    # BC-126: route-pick mirrors beaconry findings / audit. Remote hub
    # → HTTP; local dev-compose → docker-exec; else local file (back-
    # compat for tests + air-gapped dev loops).
    from poc.scripts.cli_config import load_cli_config
    from poc.scripts.cli_http import choose_source, friendly_http_error

    cfg = load_cli_config()
    source = choose_source(
        cfg,
        compose_hub_running=_running_compose_hub(),
        force_local=bool(os.environ.get("BEACONRY_VERIFY_INTEGRITY_LOCAL")),
    )

    if source == "http":
        try:
            events = fetch_events_via_http(cfg, args.since)
        except Exception as exc:  # noqa: BLE001 — single friendly line.
            url = cfg.hub_url.rstrip("/") + "/audit/integrity"
            print("✗ " + friendly_http_error(url, exc), file=sys.stderr)
            # 404 on legacy pre-BC-126 hub: hint at the upgrade path.
            if "404" in str(exc):
                print(
                    "  (hub is pre-BC-126 — upgrade hub to v0.22.10+ to use this remotely)",
                    file=sys.stderr,
                )
            return 2
        report = build_report(events)
        print(_format_report(report))
        return report.exit_code

    if source == "local-compose":
        return _run_in_container(args.since)

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
