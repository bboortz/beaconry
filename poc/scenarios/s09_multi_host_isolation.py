"""s09 — multi-host state is per-host; actions on one host don't bleed to another."""

from __future__ import annotations

from ._base import Scenario
from ._context import ScenarioContext
from ._fixtures import cand


class MultiHostIsolationScenario(Scenario):
    order = 9
    name = "s09_multi_host_isolation"
    description = (
        "Same CVE on two hosts → two separate DB rows; snooze on one doesn't affect the other"
    )

    def run(self, ctx: ScenarioContext) -> None:
        payload = cand(cve_id="CVE-S09", cvss=9.5)

        ctx.inject_scan([payload], host="homelab")
        ctx.inject_scan([payload], host="staging")

        # Two separate rows:
        ctx.assert_count(2)
        ctx.assert_status("CVE-S09", "open", host="homelab")
        ctx.assert_status("CVE-S09", "open", host="staging")

        # Snooze on homelab must NOT touch staging.
        ctx.set_db_status("CVE-S09", "snoozed", host="homelab")
        ctx.assert_status("CVE-S09", "snoozed", host="homelab")
        ctx.assert_status("CVE-S09", "open", host="staging")
