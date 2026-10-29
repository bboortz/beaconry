"""End-to-end mocked POC demo (BC-001).

Runs scout → hub → urgency decision → executor → notification → verify in-process
with BEACONRY_MOCK=1. No Docker daemon, no network, no Telegram.

Usage:
    python -m poc.demo                       # interactive (prompts Enter for Tier 1)
    python -m poc.demo --non-interactive     # CI / smoke mode
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Set env BEFORE importing hub — hub reads env at module scope.
os.environ["BEACONRY_MOCK"] = "1"
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "mock-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "0")
os.environ.setdefault("MIN_CVSS", "0")
_tmp = tempfile.mkdtemp(prefix="beaconry-demo-")
os.environ["DATA_DIR"] = _tmp

# Make poc/hub/ importable so hub.py's `from executor.base import ...` resolves.
_HUB_DIR = Path(__file__).resolve().parent / "hub"
if str(_HUB_DIR) not in sys.path:
    sys.path.insert(0, str(_HUB_DIR))

import hub  # noqa: E402  — poc/hub/hub.py loaded as top-level 'hub'
import urgency  # noqa: E402  — poc/hub/urgency.py

from poc.mock_data import FIXTURE_HOST, fixture_scan_payload  # noqa: E402

RULE = "-" * 70
HEAVY = "=" * 70


def _print_header() -> None:
    print(HEAVY)
    print("Beaconry POC — End-to-end mocked demo")
    print(HEAVY)
    print(f"Mock env: BEACONRY_MOCK=1  DATA_DIR={_tmp}")
    print("(no network, no Docker daemon, no Telegram)")
    print()


async def _process_finding(
    finding: hub.Finding,
    *,
    kev: bool,
    internet_facing: bool,
    auto_confirm: bool,
) -> bool:
    """Print narration for one finding; auto- or operator-confirm; execute; verify."""
    print(RULE)
    print(f"==> Finding: {finding.cve_id} — {finding.package} {finding.version}")
    print(f"    severity: {finding.severity}  CVSS: {finding.cvss:.1f}")
    print(f"    exposure: fixture-flagged {'internet-facing' if internet_facing else 'LAN-only'}")
    print(f"    kev: {kev}")
    print(f"    tier: {urgency.TIER_LABELS[finding.tier]}")

    safe = urgency.is_safe_class(finding, kev=kev)
    print(f"    safe class: {safe} ({'KEV + container' if safe else 'operator confirm required'})")
    print()

    if not safe:
        if auto_confirm:
            print("    [auto-confirm] simulating [Fix now] tap...")
        else:
            try:
                input("    >>> Press Enter to simulate [Fix now] tap (Ctrl-C to skip): ")
            except (EOFError, KeyboardInterrupt):
                print("\n    (skipped by operator)")
                return False
        print()

    executor = next((e for e in hub.EXECUTORS if e.can_handle(finding)), None)
    assert executor is not None, "no executor matched"
    result = await executor.execute(finding)
    if not result.success:
        print(f"    ❌ fix failed: {result.message}")
        return False

    await hub.notify(finding)
    print()

    clean = await hub.verify_fix(finding)
    status = "✓ fixed and verified" if clean else "⚠ fix applied, CVE still present"
    print(f"    verify_fix: {status}")
    print()
    return clean


async def run_demo(*, auto_confirm: bool = False) -> int:
    """Run the full mocked pipeline. Returns 0 on success."""
    _print_header()

    hub.init_db()

    print(f"==> Scout: scanning fixture inventory (host: {FIXTURE_HOST})")
    payload = fixture_scan_payload()
    sboms = payload["sboms"]
    print(f"    [mock] discovered {len(sboms)} docker image(s)")
    print()

    print(f"==> Hub: SBOM received (host: {FIXTURE_HOST}, {len(sboms)} SBOM(s))")

    findings: list[tuple[hub.Finding, bool, bool]] = []
    for entry in sboms:
        matches = await hub.run_grype(entry.get("sbom", {}))
        print(f"    [mock] grype matched {len(matches)} finding(s)")
        for match in matches:
            vuln = match["vulnerability"]
            artifact = match["artifact"]
            meta = match.get("beaconry", {})
            kev = bool(meta.get("kev"))
            internet_facing = meta.get("exposure") == "internet-facing"

            finding = hub.Finding(
                id=hub.make_id(
                    FIXTURE_HOST, vuln["id"], entry["source_meta"]["image"], artifact["name"]
                ),
                host=FIXTURE_HOST,
                cve_id=vuln["id"],
                package=artifact["name"],
                version=artifact["version"],
                fixed_version=(vuln.get("fix", {}).get("versions") or ["n/a"])[0],
                severity=vuln.get("severity", "Unknown"),
                cvss=hub.extract_cvss(vuln),
                source_type=entry["source_meta"]["type"],
                source_ref=entry["source_meta"]["image"],
                description=vuln.get("description", "")[:200],
            )
            finding.tier = urgency.classify_tier(finding, kev=kev, internet_facing=internet_facing)
            findings.append((finding, kev, internet_facing))
    print()

    verified = 0
    for finding, kev, internet_facing in findings:
        ok = await _process_finding(
            finding, kev=kev, internet_facing=internet_facing, auto_confirm=auto_confirm
        )
        if ok:
            verified += 1

    print(HEAVY)
    print(f"Demo complete — {verified}/{len(findings)} findings fixed and verified")
    print(HEAVY)
    return 0 if verified == len(findings) else 1


def main() -> int:
    auto = "--non-interactive" in sys.argv
    return asyncio.run(run_demo(auto_confirm=auto))


if __name__ == "__main__":
    sys.exit(main())
