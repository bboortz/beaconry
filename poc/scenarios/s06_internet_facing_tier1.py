"""s06 — Tier 1 fires on CVSS≥9 + internet-facing."""

from __future__ import annotations

from ._base import Scenario
from ._context import ScenarioContext
from ._fixtures import cand


class InternetFacingTier1Scenario(Scenario):
    order = 6
    name = "s06_internet_facing_tier1"
    description = "CVSS≥9 + internet-facing → tier=1 (BC-030 differentiation)"

    def run(self, ctx: ScenarioContext) -> None:
        # Exposed → Tier 1
        ctx.inject_scan(
            [cand(cve_id="CVE-S06-EXP", cvss=9.5, source_ref="nginx:1.24")],
            port_exposure={"nginx:1.24": True},
        )
        ctx.assert_tier("CVE-S06-EXP", 1)
        ctx.assert_internet_facing("CVE-S06-EXP", True)

        # LAN-only → Tier 2 (same CVSS)
        ctx.inject_scan(
            [cand(cve_id="CVE-S06-LAN", cvss=9.5, source_ref="redis:7-alpine")],
            port_exposure={"redis:7-alpine": False},
        )
        ctx.assert_tier("CVE-S06-LAN", 2)
        ctx.assert_internet_facing("CVE-S06-LAN", False)
