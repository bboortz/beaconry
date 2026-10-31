"""s03 — BC-031 regression alarm when a fixed CVE reappears."""

from __future__ import annotations

from ._base import Scenario
from ._context import ScenarioContext
from ._fixtures import cand


class RegressionScenario(Scenario):
    order = 3
    name = "s03_regression"
    description = "Fixed CVE reappears → status=regressed + 🔥 regressed audit event"

    def run(self, ctx: ScenarioContext) -> None:
        payload = cand(cve_id="CVE-S03", cvss=9.5)
        # Open → fix (empty scan) → reappear.
        ctx.inject_scan([payload])
        ctx.inject_scan([])
        ctx.assert_status("CVE-S03", "fixed_verified")

        ctx.inject_scan([payload])
        ctx.assert_status("CVE-S03", "regressed")
        ctx.assert_audit_fired("regressed", cve_id="CVE-S03")
