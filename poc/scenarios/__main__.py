"""CLI runner: `python -m poc.scenarios [--only NAME] [--hub-url URL] [--data-dir PATH]`."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ._base import Scenario
from ._context import ScenarioContext
from ._registry import SCENARIOS


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="poc.scenarios")
    p.add_argument(
        "--hub-url",
        default=os.environ.get("BEACONRY_SCENARIO_HUB_URL", "http://127.0.0.1:8004"),
        help="Base URL of the test-mode hub (default: http://127.0.0.1:8004)",
    )
    p.add_argument(
        "--data-dir",
        default=os.environ.get("BEACONRY_SCENARIO_DATA_DIR", "/tmp/beaconry-scenarios"),
        help="Shared data dir mounted into the hub (default: /tmp/beaconry-scenarios)",
    )
    p.add_argument(
        "--only",
        default=None,
        help="Run only the named scenario (e.g. s01_fresh_cve). Default: run all.",
    )
    return p.parse_args(argv)


def _run_one(scenario: Scenario, ctx: ScenarioContext) -> bool:
    try:
        ctx.wipe()
        scenario.run(ctx)
        print(f"✓ {scenario.name:30s}  {scenario.description}")
        return True
    except AssertionError as exc:
        print(f"✗ {scenario.name:30s}  {scenario.description}")
        print(f"  └─ assert: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"✗ {scenario.name:30s}  {scenario.description}")
        print(f"  └─ ERROR: {exc.__class__.__name__}: {exc}")
        return False


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    data_dir = Path(args.data_dir)
    ctx = ScenarioContext(
        hub_url=args.hub_url.rstrip("/"),
        db_path=data_dir / "beaconry.db",
        audit_path=data_dir / "audit.log",
    )

    to_run = sorted(SCENARIOS, key=lambda s: s.order)
    if args.only:
        to_run = [s for s in to_run if s.name == args.only]
        if not to_run:
            print(f"✗ no scenario named '{args.only}' — known: {[s.name for s in SCENARIOS]}")
            return 2

    print(f"==> running {len(to_run)} scenario(s) against {args.hub_url}")
    passed = sum(1 for s in to_run if _run_one(s, ctx))
    failed = len(to_run) - passed
    print()
    print(f"{passed}/{len(to_run)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
