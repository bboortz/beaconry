"""s07 — BC-036 digest content-hash bypasses the throttle on set change."""

from __future__ import annotations

from ._base import Scenario
from ._context import ScenarioContext
from ._fixtures import cand


class DigestContentHashScenario(Scenario):
    order = 7
    name = "s07_digest_content_hash"
    description = (
        "Repeat scan same set → digest_throttled. Different set → digest fires (content_changed)"
    )

    def run(self, ctx: ScenarioContext) -> None:
        # Build 15 candidates — cap=5 (default) → 10 suppressed → one digest fires.
        set_A = [cand(cve_id=f"CVE-S07-{i:02d}", cvss=9.5, package=f"pkg{i}") for i in range(15)]
        set_B = [cand(cve_id=f"CVE-S07-X-{i:02d}", cvss=9.5, package=f"pkgx{i}") for i in range(15)]

        T = 1700000000

        # First send — fires on `first_fire` reason.
        ctx.inject_scan(set_A, at=T)
        ctx.assert_audit_fired("digest_sent", count=1)

        # Same set, 1h later — should throttle.
        ctx.inject_scan(set_A, at=T + 3600)
        ctx.assert_audit_fired("digest_throttled")
        # Still just 1 send, 1 throttle:
        assert len(ctx.audit_events("digest_sent")) == 1, (
            "digest must not re-send same set within interval"
        )

        # Different set, 2h later — should bypass interval via content hash.
        ctx.inject_scan(set_B, at=T + 7200)
        assert len(ctx.audit_events("digest_sent")) == 2, (
            "digest must fire on content change even within interval"
        )
