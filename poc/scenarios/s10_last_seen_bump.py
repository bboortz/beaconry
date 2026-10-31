"""s10 — BC-034 last_seen_ts bump on re-scan without creating a new row."""

from __future__ import annotations

from ._base import Scenario
from ._context import ScenarioContext
from ._fixtures import cand


class LastSeenBumpScenario(Scenario):
    order = 10
    name = "s10_last_seen_bump"
    description = "Rescanning the same CVE updates last_seen_ts in place — no duplicate row"

    def run(self, ctx: ScenarioContext) -> None:
        T = 1700000000
        payload = cand(cve_id="CVE-S10", cvss=9.5)

        ctx.inject_scan([payload], at=T)
        row1 = ctx.find("CVE-S10")
        assert row1 is not None
        assert row1["last_seen_ts"] == T, (
            f"first ingest should set last_seen={T}, got {row1['last_seen_ts']}"
        )

        # Re-scan 5 min later — same payload, no new row, bumped last_seen.
        ctx.inject_scan([payload], at=T + 300)
        ctx.assert_count(1)
        row2 = ctx.find("CVE-S10")
        assert row2 is not None
        assert row2["last_seen_ts"] == T + 300, (
            f"re-scan should bump last_seen to {T + 300}, got {row2['last_seen_ts']}"
        )
