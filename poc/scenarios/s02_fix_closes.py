"""s02 — BC-031 + BC-086 auto-close when finding disappears from inventory.

BC-086 bumped the audit event name (auto_closed → finding_auto_fixed),
the status enum (fixed_verified → auto_fixed), and added counter-based
threshold. Scenario harness runs with BEACONRY_AUTO_FIX_THRESHOLD=1
(see poc/scenarios/compose.yml + _context.py) so one empty scan still
closes immediately — preserving the single-cycle round-trip these
scenarios were written against.
"""

from __future__ import annotations

from ._base import Scenario
from ._context import ScenarioContext
from ._fixtures import cand


class FixClosesScenario(Scenario):
    order = 2
    name = "s02_fix_closes"
    description = "Finding absent from re-scan → BC-086 auto-closes to auto_fixed"

    def run(self, ctx: ScenarioContext) -> None:
        # Seed one open finding.
        ctx.inject_scan([cand(cve_id="CVE-S02", cvss=9.5)])
        ctx.assert_status("CVE-S02", "open")

        # Empty candidate list = scout no longer sees it → BC-086 reconcile closes.
        ctx.inject_scan([])
        ctx.assert_status("CVE-S02", "auto_fixed")
        ctx.assert_audit_fired("finding_auto_fixed", cve_id="CVE-S02")
