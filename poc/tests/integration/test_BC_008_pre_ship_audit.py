"""BC-008 pre-ship audit protocol — doc-contract tests.

Covers:
    - CAP-B013: Developer trusts that `shipped` means every acceptance has evidence
    - CAP-T026: Plan template carries a Shipping evidence section used as the pre-ship checklist
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def test_plan_template_has_shipping_evidence_section() -> None:
    text = _read("docs/plan/_template.md")
    assert "## Shipping evidence" in text
    # 4-column header must appear so new plan docs copy the right shape.
    for col in ("Scenario", "Evidence type", "Pointer", "Status"):
        assert col in text, f"template missing column {col!r}"


def test_claude_md_names_step_6a() -> None:
    text = _read("CLAUDE.md")
    assert "6a" in text or "6a." in text, "CLAUDE.md must document step 6a"
    assert "Pre-ship audit" in text, "step 6a must be named 'Pre-ship audit'"
    for lens in ("Gherkin coverage", "data-model symmetry", "self-consistency"):
        assert lens in text, f"CLAUDE.md must name the lens {lens!r}"


def test_workflow_memory_names_step_6a() -> None:
    text = _read("memory/workflow.md")
    assert "6a" in text, "workflow memory must mention step 6a"
    assert "Pre-ship audit" in text


def test_bc_003_has_shipping_evidence_table() -> None:
    """BC-003 draft gets a preliminary table so it's clear what blocks ready/shipped."""
    text = _read("docs/stories/BC-003-live-verified-buttons.md")
    assert "Shipping evidence" in text, (
        "BC-003 draft should carry a preliminary Shipping evidence table"
    )


def test_readme_mentions_pre_ship_audit() -> None:
    text = _read("README.md")
    assert "pre-ship audit" in text.lower() or "Pre-ship audit" in text, (
        "README must point to the pre-ship audit step"
    )


def test_shipping_evidence_rows_mentioned_in_cap_t026() -> None:
    text = _read("docs/CAPABILITIES.md")
    assert "CAP-T026" in text
    assert "Shipping evidence" in text
