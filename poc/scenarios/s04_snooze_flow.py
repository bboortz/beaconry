"""s04 — snooze state survives re-scan + excludes finding from first-fire."""

from __future__ import annotations

from ._base import Scenario
from ._context import ScenarioContext
from ._fixtures import cand


class SnoozeFlowScenario(Scenario):
    order = 4
    name = "s04_snooze_flow"
    description = "Snoozed finding stays snoozed on re-scan; new finding alongside first-fires"

    def run(self, ctx: ScenarioContext) -> None:
        a = cand(cve_id="CVE-S04-A", cvss=9.5, package="openssl")
        b = cand(cve_id="CVE-S04-B", cvss=9.5, package="curl")

        # Seed A, snooze it operator-style.
        ctx.inject_scan([a])
        ctx.set_db_status("CVE-S04-A", "snoozed")

        # Re-scan with A still present + a new B.
        ctx.inject_scan([a, b])

        ctx.assert_status("CVE-S04-A", "snoozed")  # untouched
        ctx.assert_status("CVE-S04-B", "open")  # first-fired
