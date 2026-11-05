"""BC-088 — docs/LIFECYCLE.md structure invariants.

Covers:
    - CAP-T182: docs/LIFECYCLE.md — single source of truth for finding
      lifecycle. Structure assertions mirror the Gherkin scenarios in
      the BC-088 story (every status named, every timer var named, each
      known-flaw section populated).

These are not content tests — content review is manual, part of BC-088
shipping evidence. These tests only catch drift: a future BC that
removes a status enum value or renames a timer var would quietly break
the lifecycle doc's claims unless this test fails first.
"""

from __future__ import annotations

from pathlib import Path

import pytest

LIFECYCLE = Path(__file__).resolve().parents[3] / "docs" / "LIFECYCLE.md"


@pytest.fixture(scope="module")
def doc_text() -> str:
    assert LIFECYCLE.exists(), f"docs/LIFECYCLE.md missing at {LIFECYCLE}"
    return LIFECYCLE.read_text(encoding="utf-8")


# ── Scenario 1: every status named ───────────────────────────────────────────

STATUSES = [
    "open",
    "snoozed",
    "accepted",
    "fixed",
    "fixed_verified",
    "auto_fixed",
    "regressed",
    "regressed_from_auto_fix",
    "verification_failed",
]


@pytest.mark.parametrize("status", STATUSES)
def test_every_status_mentioned(doc_text: str, status: str) -> None:
    """Every finding status is referenced somewhere in the lifecycle doc."""
    assert status in doc_text, (
        f"status '{status}' missing from docs/LIFECYCLE.md — a status enum "
        f"may have been added/renamed without updating the doc"
    )


# ── Scenario 2: every timer env var named ───────────────────────────────────

TIMERS = [
    "SCAN_INTERVAL",
    "BEACONRY_AUTO_FIX_THRESHOLD",
    "BEACONRY_HEARTBEAT_INTERVAL_S",
    "BEACONRY_HEARTBEAT_SWEEP_INTERVAL_S",
    "BEACONRY_HEARTBEAT_LIVENESS_WINDOW_S",
    "BEACONRY_DIGEST_INTERVAL_S",
    "BEACONRY_MAX_FINDINGS_PER_RUN",
    "BEACONRY_LOG_LEVEL",
    # BC-089 additions.
    "BEACONRY_SNOOZE_SWEEP_INTERVAL_S",
    "BEACONRY_SNOOZE_DEFAULT_HOURS",
    "BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE",
]


@pytest.mark.parametrize("var", TIMERS)
def test_every_timer_var_mentioned(doc_text: str, var: str) -> None:
    """Every timer env var the lifecycle depends on is in the doc."""
    assert var in doc_text, (
        f"timer env var '{var}' missing from docs/LIFECYCLE.md — a new "
        f"timer was wired without documenting it, or a rename is unsynced"
    )


# ── Scenario 3: every known-flaw section is populated ───────────────────────

FLAW_SECTIONS = [
    "## 13. Unclearities",
    "## 14. Necessary improvements",
    "## 15. Contradictions / inconsistencies",
    "## 16. Missing implementation",
    "## 17. Known bugs",
]


@pytest.mark.parametrize("header", FLAW_SECTIONS)
def test_known_flaw_section_populated(doc_text: str, header: str) -> None:
    """Each flaw section has at least one numbered entry (e.g. '1.')."""
    assert header in doc_text, f"section header '{header}' missing"
    # Split the doc at this header and the NEXT `## ` header; the slice
    # between them is the section body.
    after_header = doc_text.split(header, 1)[1]
    # Next `## ` header terminates this section.
    section_body = after_header.split("\n## ", 1)[0]
    # Require at least one "1." numbered entry — proves the section
    # isn't empty / placeholder.
    assert "1. **" in section_body or "1. " in section_body, (
        f"section '{header}' has no numbered entries — a future BC "
        f"may have emptied it without re-populating"
    )


# ── Scenario 4: CAP-T182 pointer exists in CAPABILITIES.md ──────────────────


def test_cap_t182_registered_in_capabilities() -> None:
    """CAP-T182 entry exists and names docs/LIFECYCLE.md."""
    caps_path = LIFECYCLE.parent / "CAPABILITIES.md"
    assert caps_path.exists()
    caps = caps_path.read_text(encoding="utf-8")
    assert "### CAP-T182:" in caps, "CAP-T182 header missing"
    # The CAP's description must name the doc verbatim.
    assert "docs/LIFECYCLE.md" in caps, "CAP-T182 description must name docs/LIFECYCLE.md verbatim"


# ── Structural sanity: doc has the 12 descriptive + 5 flaw sections ─────────


def test_doc_has_expected_section_count(doc_text: str) -> None:
    """17 top-level `## ` sections (1–17). Catches accidental section
    renumbering that would drift from the plan doc's shipping-evidence
    table."""
    # Count section headers of the form "## 1.", "## 2.", … "## 17."
    for n in range(1, 18):
        marker = f"## {n}."
        assert marker in doc_text, f"section '{marker}' missing"
