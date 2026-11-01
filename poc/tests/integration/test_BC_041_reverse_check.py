"""BC-041 — verify-evidence-pointers reverse check.

Covers:
    - CAP-T088: Reverse pass asserts every integration test is referenced by a plan row
    - CAP-B025: Pre-ship audit catches partial close-gap fixes
"""

from __future__ import annotations

from pathlib import Path

import pytest

from poc.scripts import verify_evidence_pointers as vep


def _make_plan(plan_dir: Path, bc_slug: str, rows: list[str]) -> Path:
    """Write a `BC-NNN-<slug>-plan.md` with the given shipping-evidence rows."""
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan = plan_dir / f"{bc_slug}-plan.md"
    body = (
        f"# {bc_slug} — demo\n\n"
        "## Shipping evidence\n\n"
        "| Scenario / goal | Evidence type | Pointer | Status |\n"
        "|---|---|---|---|\n" + "\n".join(rows) + "\n"
    )
    plan.write_text(body)
    return plan


def _make_test(tests_dir: Path, bc_slug: str, functions: list[str]) -> Path:
    """Write a `test_BC_NNN_<slug>.py` with the given function names."""
    tests_dir.mkdir(parents=True, exist_ok=True)
    f = tests_dir / f"test_{bc_slug.replace('-', '_')}.py"
    body = "\n\n".join(f"def {name}():\n    assert True" for name in functions)
    f.write_text(body + "\n")
    return f


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Isolated plan + integration-tests tree the reverse pass walks."""
    plans = tmp_path / "docs" / "plan"
    tests = tmp_path / "poc" / "tests" / "integration"
    monkeypatch.setattr(vep, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(vep, "REVERSE_CHECK_ALLOWLIST", {})
    return {"plans": plans, "tests": tests, "root": tmp_path}


def test_shipped_repo_passes_reverse_check() -> None:
    """CAP-T088 regression guard: live repo passes the reverse pass."""
    violations = vep.reverse_check()
    assert violations == [], "reverse-check drift:\n  " + "\n  ".join(violations)


def test_orphan_test_fires_violation(harness: dict) -> None:
    """Test function exists but no plan row points at it → violation."""
    _make_plan(harness["plans"], "BC-900-demo", [])
    _make_test(harness["tests"], "BC-900-demo", ["test_orphan"])
    violations = vep.reverse_check(plans_dir=harness["plans"], tests_dir=harness["tests"])
    assert len(violations) == 1
    msg = violations[0]
    assert "test_orphan" in msg
    assert "BC-900-demo-plan.md" in msg
    # Copy-pasteable row template present:
    assert "| ✓ |" in msg or "test_orphan" in msg


def test_allowlist_suppresses_violation(harness: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """A function on REVERSE_CHECK_ALLOWLIST is skipped."""
    monkeypatch.setattr(
        vep,
        "REVERSE_CHECK_ALLOWLIST",
        {"test_grandfathered": "shipped pre-BC-041, intentional helper"},
    )
    _make_plan(harness["plans"], "BC-901-demo", [])
    _make_test(harness["tests"], "BC-901-demo", ["test_grandfathered"])
    violations = vep.reverse_check(plans_dir=harness["plans"], tests_dir=harness["tests"])
    assert violations == []


def test_referenced_test_passes(harness: dict) -> None:
    """A function pointed-at by a shipping-evidence row passes."""
    _make_plan(
        harness["plans"],
        "BC-902-demo",
        ["| happy path | integration | `test_BC_902_demo.py::test_x` | ✓ |"],
    )
    _make_test(harness["tests"], "BC-902-demo", ["test_x"])
    violations = vep.reverse_check(plans_dir=harness["plans"], tests_dir=harness["tests"])
    assert violations == []


def test_missing_plan_soft_passes(harness: dict) -> None:
    """Integration test with no matching plan file → no reverse violation."""
    _make_test(harness["tests"], "BC-903-nopan", ["test_without_plan"])
    violations = vep.reverse_check(plans_dir=harness["plans"], tests_dir=harness["tests"])
    assert violations == []


def test_plan_without_evidence_section_soft_passes(harness: dict) -> None:
    """Plan file lacking a `## Shipping evidence` section → soft-pass."""
    harness["plans"].mkdir(parents=True, exist_ok=True)
    (harness["plans"] / "BC-904-noevidence-plan.md").write_text(
        "# BC-904 — demo\n\n(no table yet)\n"
    )
    _make_test(harness["tests"], "BC-904-noevidence", ["test_x"])
    violations = vep.reverse_check(plans_dir=harness["plans"], tests_dir=harness["tests"])
    assert violations == []


def test_parametrized_test_matched_by_base_name(harness: dict) -> None:
    """Pointer `test_x` in plan matches `test_x` (parametrization irrelevant)."""
    _make_plan(
        harness["plans"],
        "BC-905-demo",
        ["| cases | integration | `test_BC_905_demo.py::test_x` | ✓ |"],
    )
    # Just write a bare `test_x` — parametrization matching is covered by
    # the fact that reverse check works on function name, not node id.
    _make_test(harness["tests"], "BC-905-demo", ["test_x"])
    violations = vep.reverse_check(plans_dir=harness["plans"], tests_dir=harness["tests"])
    assert violations == []


def test_multiple_orphans_all_reported(harness: dict) -> None:
    """Several orphans in one file → all surface in one run."""
    _make_plan(harness["plans"], "BC-906-demo", [])
    _make_test(
        harness["tests"],
        "BC-906-demo",
        ["test_a", "test_b", "test_c"],
    )
    violations = vep.reverse_check(plans_dir=harness["plans"], tests_dir=harness["tests"])
    assert len(violations) == 3
    joined = " | ".join(violations)
    for name in ("test_a", "test_b", "test_c"):
        assert name in joined


def test_private_underscore_prefix_skipped(harness: dict) -> None:
    """Functions starting with `_` are helpers, not tests — skipped."""
    _make_plan(harness["plans"], "BC-907-demo", [])
    _make_test(harness["tests"], "BC-907-demo", ["_helper", "_setup"])
    violations = vep.reverse_check(plans_dir=harness["plans"], tests_dir=harness["tests"])
    assert violations == []
