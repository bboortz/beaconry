"""s05 — KEV CVE tiers at 0 regardless of CVSS."""

from __future__ import annotations

from ._base import Scenario
from ._context import ScenarioContext
from ._fixtures import cand


class KevTier0Scenario(Scenario):
    order = 5
    name = "s05_kev_tier0"
    description = "KEV finding → tier=0 even at modest CVSS"

    def run(self, ctx: ScenarioContext) -> None:
        # CVSS 7.5 + KEV → Tier 0 (per urgency.classify_tier)
        ctx.inject_scan([cand(cve_id="CVE-S05-KEV", cvss=7.5, kev=True)])
        ctx.assert_tier("CVE-S05-KEV", 0)

        # Non-KEV at CVSS 7.5 falls through to Tier 2 (not internet-facing).
        ctx.inject_scan([cand(cve_id="CVE-S05-NOT-KEV", cvss=7.5, kev=False)])
        ctx.assert_tier("CVE-S05-NOT-KEV", 2)
