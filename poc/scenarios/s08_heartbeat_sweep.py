"""s08 — BC-035 heartbeat sweep re-pokes stale open findings via injected clock."""

from __future__ import annotations

from ._base import Scenario
from ._context import ScenarioContext
from ._fixtures import cand


class HeartbeatSweepScenario(Scenario):
    order = 8
    name = "s08_heartbeat_sweep"
    description = "Open finding past HEARTBEAT_INTERVAL_S → sweep emits heartbeat_reminder_sent"

    def run(self, ctx: ScenarioContext) -> None:
        T = 1700000000
        ctx.inject_scan([cand(cve_id="CVE-S08", cvss=9.5)], at=T)
        ctx.assert_status("CVE-S08", "open")

        # Sweep at T+1h → within interval → no reminder.
        ctx.trigger_sweep(at=T + 3600)
        ctx.assert_audit_absent("heartbeat_reminder_sent", cve_id="CVE-S08")

        # Sweep at T + 25h → past default 24h interval → reminder fires.
        ctx.trigger_sweep(at=T + 25 * 3600)
        ctx.assert_audit_fired("heartbeat_reminder_sent", cve_id="CVE-S08", count=1)
