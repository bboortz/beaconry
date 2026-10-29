"""Urgency tier classification + safe-class detection.

See memory/design-decisions.md § Urgency tiers / Auto-fix trust model.

Tier 0 — CISA KEV entry
Tier 1 — CVSS ≥ 9.0 and internet-facing
Tier 2 — CVSS 7–8 or LAN-only
Tier 3 — CVSS < 7

Safe-class (BC-001 rule): KEV + container image → safe (rollback tag + reversible pull).
Everything else requires operator confirmation.
"""

from __future__ import annotations

from executor.base import Finding

TIER_LABELS: dict[int, str] = {
    0: "Tier 0 (KEV — 15-min target, auto-fix if safe class)",
    1: "Tier 1 (CVSS≥9 + internet-facing — 15-min target, operator confirmation)",
    2: "Tier 2 (CVSS 7–8 or LAN-only — 1h target)",
    3: "Tier 3 (CVSS < 7 — 24h batch)",
}


def classify_tier(finding: Finding, *, kev: bool, internet_facing: bool) -> int:
    """Return urgency tier (0 highest, 3 lowest)."""
    if kev:
        return 0
    if finding.cvss >= 9.0 and internet_facing:
        return 1
    if finding.cvss >= 7.0:
        return 2
    return 3


def is_safe_class(finding: Finding, *, kev: bool) -> bool:
    """Return True if the finding is safe to auto-fix without operator confirmation.

    BC-001 rule: Tier 0 KEV + container image = safe. Container fixes are
    reversible via the :rollback tag that DockerAdapter takes before pulling.
    """
    return kev and finding.source_type == "docker"
