"""s03 — BC-086 regression alarm when an auto-fixed CVE reappears.

Previously tested BC-031's fixed_verified → regressed path. BC-086
changed the auto-close status + emitted a distinct regression audit
event (regressed_from_auto_fix). Scenario now exercises that new
path end-to-end.
"""

from __future__ import annotations

from ._base import Scenario
from ._context import ScenarioContext
from ._fixtures import cand


class RegressionScenario(Scenario):
    order = 3
    name = "s03_regression"
    description = (
        "Auto-fixed CVE reappears → status=regressed + 🔥 regressed_from_auto_fix audit event"
    )

    def run(self, ctx: ScenarioContext) -> None:
        payload = cand(cve_id="CVE-S03", cvss=9.5)
        # Open → auto_fix (empty scan, threshold=1 in harness) → reappear.
        ctx.inject_scan([payload])
        ctx.inject_scan([])
        ctx.assert_status("CVE-S03", "auto_fixed")

        ctx.inject_scan([payload])
        ctx.assert_status("CVE-S03", "regressed")
        ctx.assert_audit_fired("regressed_from_auto_fix", cve_id="CVE-S03")
