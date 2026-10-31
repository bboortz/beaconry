"""s01 — fresh CVE lands at correct tier + emits audit event."""

from __future__ import annotations

from ._base import Scenario
from ._context import ScenarioContext
from ._fixtures import cand


class FreshCveScenario(Scenario):
    order = 1
    name = "s01_fresh_cve"
    description = "Fresh CVE lands → open row at tier=2 + `finding_ingested` audit event"

    def run(self, ctx: ScenarioContext) -> None:
        ctx.inject_scan([cand(cve_id="CVE-S01", cvss=9.5)])
        ctx.assert_status("CVE-S01", "open")
        ctx.assert_tier("CVE-S01", 2)
        ctx.assert_internet_facing("CVE-S01", False)
        ctx.assert_audit_fired("finding_ingested", cve_id="CVE-S01", count=1)
