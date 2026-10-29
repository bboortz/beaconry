"""Unit tests for urgency tier classification and safe-class detection.

Covers:
    - CAP-T004: Urgency tier classification matches design-decisions.md
    - CAP-T005: Safe-class auto-fix rule (KEV + container only)
"""

from __future__ import annotations

import pytest

from poc.hub import urgency
from poc.hub.executor.base import Finding


def _finding(cvss: float, source_type: str = "docker") -> Finding:
    return Finding(
        id="x",
        host="h",
        cve_id="CVE-x",
        package="p",
        version="1",
        fixed_version="2",
        severity="High",
        cvss=cvss,
        source_type=source_type,
        source_ref="img:1",
        description="",
    )


@pytest.mark.parametrize(
    ("cvss", "kev", "internet_facing", "expected_tier"),
    [
        (3.0, True, False, 0),  # KEV wins regardless of CVSS/exposure
        (9.8, True, True, 0),
        (9.8, False, True, 1),  # CVSS≥9 + internet-facing
        (9.0, False, True, 1),
        (9.0, False, False, 2),  # CVSS≥7 LAN-only
        (7.0, False, True, 2),  # CVSS between 7 and 9 → Tier 2 even internet-facing
        (6.9, False, True, 3),
        (0.0, False, False, 3),
    ],
)
def test_classify_tier_matrix(
    cvss: float, kev: bool, internet_facing: bool, expected_tier: int
) -> None:
    assert (
        urgency.classify_tier(_finding(cvss), kev=kev, internet_facing=internet_facing)
        == expected_tier
    )


def test_safe_class_requires_kev_and_container() -> None:
    kev_container = _finding(9.0, source_type="docker")
    non_kev_container = _finding(9.0, source_type="docker")
    kev_pkg = _finding(9.0, source_type="alpm")

    assert urgency.is_safe_class(kev_container, kev=True) is True
    assert urgency.is_safe_class(non_kev_container, kev=False) is False
    assert urgency.is_safe_class(kev_pkg, kev=True) is False  # host package not safe class


def test_tier_labels_cover_all_tiers() -> None:
    assert set(urgency.TIER_LABELS.keys()) == {0, 1, 2, 3}
    for label in urgency.TIER_LABELS.values():
        assert "Tier" in label
