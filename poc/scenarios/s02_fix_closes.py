"""s02 — BC-031 auto-close when finding disappears from inventory."""

from __future__ import annotations

from ._base import Scenario
from ._context import ScenarioContext
from ._fixtures import cand


class FixClosesScenario(Scenario):
    order = 2
    name = "s02_fix_closes"
    description = "Finding absent from re-scan → BC-031 auto-closes to fixed_verified"

    def run(self, ctx: ScenarioContext) -> None:
        # Seed one open finding.
        ctx.inject_scan([cand(cve_id="CVE-S02", cvss=9.5)])
        ctx.assert_status("CVE-S02", "open")

        # Empty candidate list = scout no longer sees it → BC-031 reconcile closes.
        ctx.inject_scan([])
        ctx.assert_status("CVE-S02", "fixed_verified")
        ctx.assert_audit_fired("auto_closed", cve_id="CVE-S02")
