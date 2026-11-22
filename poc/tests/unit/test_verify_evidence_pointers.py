"""Unit tests for the evidence-pointer lint helpers.

Covers:
    - CAP-T057: Evidence-pointer lint — pointer-extraction regex, node-id
      normalization, status-cell strictness check
"""

from __future__ import annotations

from pathlib import Path

import pytest

from poc.scripts import verify_evidence_pointers as vep
from poc.scripts.verify_evidence_pointers import (
    extract_pointers_from_cell,
    normalize_node_id,
    row_is_strict,
)


def test_extract_pointers_from_cell() -> None:
    """Pointer-extraction regex covers all plan-table pointer shapes."""
    assert extract_pointers_from_cell("`test_x.py::test_y`") == ["test_x.py::test_y"]
    assert extract_pointers_from_cell("prose and `test_x.py::test_y` end") == ["test_x.py::test_y"]
    assert extract_pointers_from_cell("`test_x.py`") == ["test_x.py"]
    assert extract_pointers_from_cell("`test_x.py::test_p[case-1]`") == [
        "test_x.py::test_p[case-1]"
    ]
    # Two pointers in one cell (rare but possible)
    assert extract_pointers_from_cell("`test_a.py::test_x` plus `test_b.py::test_y`") == [
        "test_a.py::test_x",
        "test_b.py::test_y",
    ]
    # Nested path pointer
    assert extract_pointers_from_cell("`poc/tests/unit/test_x.py::test_y`") == [
        "poc/tests/unit/test_x.py::test_y"
    ]
    # Non-test-shaped: must return empty
    assert extract_pointers_from_cell("run `make demo` and curl") == []
    assert extract_pointers_from_cell("live manual, verified 2026-10-30") == []
    assert extract_pointers_from_cell("awaits Phase 5") == []
    # Trailing comma must NOT be slurped into the node id (plans sometimes
    # use `foo.py::test_a, foo.py::test_b` as a comma-separated list).
    assert extract_pointers_from_cell("`test_x.py::test_a`, `test_x.py::test_b`") == [
        "test_x.py::test_a",
        "test_x.py::test_b",
    ]
    # Parametrize id with internal `:` + `,` + spaces must be preserved
    assert extract_pointers_from_cell("`test_x.py::test_fn[nginx:1.24-Probe failed]`") == [
        "test_x.py::test_fn[nginx:1.24-Probe failed]"
    ]


def test_normalize_node_id() -> None:
    """Plan writes `test_x.py::fn`, pytest emits `path/test_x.py::fn` → matches."""
    assert normalize_node_id("poc/tests/unit/test_x.py::test_fn") == "test_x.py::test_fn"
    assert (
        normalize_node_id("poc/tests/e2e/test_bc_011.py::test_param[case-1]")
        == "test_bc_011.py::test_param[case-1]"
    )
    # No path prefix → unchanged
    assert normalize_node_id("test_x.py::test_fn") == "test_x.py::test_fn"
    # File only (no ::) also normalizes to basename
    assert normalize_node_id("poc/tests/unit/test_x.py") == "test_x.py"


def test_only_check_mark_rows_are_strict() -> None:
    """Status cell containing ✓ is the only strict trigger."""
    assert row_is_strict("✓") is True
    assert row_is_strict("✓ shipped 2026-10-30") is True
    assert row_is_strict("⚠️") is False
    assert row_is_strict("⚠️ deferred to BC-020") is False
    assert row_is_strict("❌") is False
    assert row_is_strict("") is False
    assert row_is_strict("done") is False  # must specifically contain ✓


# ── _resolve_pointer: node-id + file-only path coverage ──────────────────


def test_resolve_pointer_passes_on_known_node_id(tmp_path: Path) -> None:
    collected = {"test_x.py::test_y", "test_x.py::test_z"}
    assert vep._resolve_pointer("test_x.py::test_y", collected, tmp_path) is None


def test_resolve_pointer_normalizes_path_prefix(tmp_path: Path) -> None:
    # Plans may write `poc/tests/unit/test_x.py::test_y`; collected node ids
    # drop the path — both must resolve.
    collected = {"test_x.py::test_y"}
    assert vep._resolve_pointer("poc/tests/unit/test_x.py::test_y", collected, tmp_path) is None


def test_resolve_pointer_flags_missing_file() -> None:
    err = vep._resolve_pointer("test_missing.py::test_fn", set(), Path("."))
    assert err is not None
    assert "not collected" in err


def test_resolve_pointer_flags_wrong_test_name_in_known_file() -> None:
    collected = {"test_x.py::test_y"}
    err = vep._resolve_pointer("test_x.py::test_gone", collected, Path("."))
    assert err is not None
    assert "test_gone" in err


def test_resolve_pointer_accepts_existing_test_file_on_disk(tmp_path: Path) -> None:
    f = tmp_path / "test_present.py"
    f.write_text("def test_x(): pass\n")
    # Pointer without `::` → file-only check falls back to disk lookup.
    # Function uses _REPO_ROOT internally, so we rglob against the repo; a
    # file created under tmp_path is NOT under _REPO_ROOT. Skip strict
    # existence check and assert the opposite: a bogus pointer is flagged.
    err = vep._resolve_pointer("test_definitely_does_not_exist_xyz.py", set(), tmp_path)
    assert err is not None
    assert "not found on disk" in err


def test_resolve_pointer_passes_non_test_file_pointer(tmp_path: Path) -> None:
    # File-only pointers whose basename doesn't start with `test_` are
    # intentionally ignored (prose anchors / scripts).
    assert vep._resolve_pointer("poc/scripts/hub.py", set(), tmp_path) is None


# ── check_plan: full table walk ──────────────────────────────────────────


def test_check_plan_passes_for_fully_resolved_rows(tmp_path: Path) -> None:
    plan = tmp_path / "BC-999-x-plan.md"
    plan.write_text(
        "## Shipping evidence\n\n"
        "| Scenario | Evidence type | Pointer | Status |\n"
        "|---|---|---|---|\n"
        "| foo | integration | `test_x.py::test_y` | ✓ |\n"
    )
    collected = {"test_x.py::test_y"}
    assert vep.check_plan(plan, collected, tmp_path) == []


def test_check_plan_flags_unresolved_pointer(tmp_path: Path) -> None:
    plan = tmp_path / "BC-999-x-plan.md"
    plan.write_text(
        "## Shipping evidence\n\n"
        "| Scenario | Evidence type | Pointer | Status |\n"
        "|---|---|---|---|\n"
        "| foo | integration | `test_missing.py::test_y` | ✓ |\n"
    )
    errs = vep.check_plan(plan, set(), tmp_path)
    assert len(errs) == 1
    assert "test_missing.py" in errs[0]


def test_check_plan_skips_non_strict_rows(tmp_path: Path) -> None:
    # ⚠️ rows are deferrals; the lint doesn't police their pointers.
    plan = tmp_path / "BC-999-x-plan.md"
    plan.write_text(
        "## Shipping evidence\n\n"
        "| Scenario | Evidence type | Pointer | Status |\n"
        "|---|---|---|---|\n"
        "| not shipped yet | integration | `test_missing.py::test_y` | ⚠️ |\n"
    )
    assert vep.check_plan(plan, set(), tmp_path) == []


def test_check_plan_skips_plans_without_evidence_table(tmp_path: Path) -> None:
    plan = tmp_path / "BC-999-x-plan.md"
    plan.write_text("# BC-999\n\nNo shipping-evidence section here.\n")
    # Grandfathered — parser returns None.
    assert vep.check_plan(plan, set(), tmp_path) == []


# ── reverse_check: integration-test backreference pass ───────────────────


def test_reverse_check_passes_when_every_test_is_referenced(tmp_path: Path) -> None:
    plans_dir = tmp_path / "plans"
    tests_dir = tmp_path / "tests"
    plans_dir.mkdir()
    tests_dir.mkdir()

    (plans_dir / "BC-900-x-plan.md").write_text(
        "## Shipping evidence\n\n"
        "| Scenario | Evidence type | Pointer | Status |\n"
        "|---|---|---|---|\n"
        "| a | integration | `test_BC_900_x.py::test_one` | ✓ |\n"
        "| b | integration | `test_BC_900_x.py::test_two` | ✓ |\n"
    )
    (tests_dir / "test_BC_900_x.py").write_text("def test_one(): pass\ndef test_two(): pass\n")
    assert vep.reverse_check(plans_dir=plans_dir, tests_dir=tests_dir) == []


def test_reverse_check_flags_orphan_test_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Error formatting uses `relative_to(_REPO_ROOT)`; root tmp_path to it.
    monkeypatch.setattr(vep, "_REPO_ROOT", tmp_path)
    plans_dir = tmp_path / "plans"
    tests_dir = tmp_path / "tests"
    plans_dir.mkdir()
    tests_dir.mkdir()

    (plans_dir / "BC-901-y-plan.md").write_text(
        "## Shipping evidence\n\n"
        "| Scenario | Evidence type | Pointer | Status |\n"
        "|---|---|---|---|\n"
        "| a | integration | `test_BC_901_y.py::test_one` | ✓ |\n"
    )
    (tests_dir / "test_BC_901_y.py").write_text("def test_one(): pass\ndef test_orphan(): pass\n")
    violations = vep.reverse_check(plans_dir=plans_dir, tests_dir=tests_dir)
    assert len(violations) == 1
    assert "test_orphan" in violations[0]
    # Error message must include the copy-pasteable template.
    assert "Template:" in violations[0]


def test_reverse_check_soft_passes_when_plan_missing(tmp_path: Path) -> None:
    # Test file for BC-902 exists but no matching plan file → soft-pass.
    plans_dir = tmp_path / "plans"
    tests_dir = tmp_path / "tests"
    plans_dir.mkdir()
    tests_dir.mkdir()
    (tests_dir / "test_BC_902_orphan.py").write_text("def test_x(): pass\n")
    assert vep.reverse_check(plans_dir=plans_dir, tests_dir=tests_dir) == []


def test_reverse_check_soft_passes_when_plan_has_no_evidence_section(
    tmp_path: Path,
) -> None:
    plans_dir = tmp_path / "plans"
    tests_dir = tmp_path / "tests"
    plans_dir.mkdir()
    tests_dir.mkdir()
    (plans_dir / "BC-903-z-plan.md").write_text("# BC-903\n\nNo table here.\n")
    (tests_dir / "test_BC_903_z.py").write_text("def test_x(): pass\n")
    assert vep.reverse_check(plans_dir=plans_dir, tests_dir=tests_dir) == []


def test_reverse_check_respects_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plans_dir = tmp_path / "plans"
    tests_dir = tmp_path / "tests"
    plans_dir.mkdir()
    tests_dir.mkdir()
    (plans_dir / "BC-904-w-plan.md").write_text(
        "## Shipping evidence\n\n"
        "| Scenario | Evidence type | Pointer | Status |\n"
        "|---|---|---|---|\n"
    )
    (tests_dir / "test_BC_904_w.py").write_text("def test_allowlisted_helper(): pass\n")
    monkeypatch.setitem(
        vep.REVERSE_CHECK_ALLOWLIST, "test_allowlisted_helper", "unit test coverage"
    )
    assert vep.reverse_check(plans_dir=plans_dir, tests_dir=tests_dir) == []


def test_reverse_check_short_circuits_when_dirs_missing(tmp_path: Path) -> None:
    # Non-existent dirs → empty violations, no crash.
    assert vep.reverse_check(plans_dir=tmp_path / "nope", tests_dir=tmp_path / "also-nope") == []


# ── _bc_slug_from_test_file + _find_plan_for_bc: helpers ────────────────


def test_bc_slug_from_test_file_extracts_dash_form() -> None:
    assert vep._bc_slug_from_test_file(Path("test_BC_041_reverse_check.py")) == (
        "BC-041-reverse-check"
    )


def test_bc_slug_from_test_file_returns_none_for_non_bc_file() -> None:
    assert vep._bc_slug_from_test_file(Path("test_random.py")) is None


def test_find_plan_prefers_file_with_shipping_evidence(tmp_path: Path) -> None:
    (tmp_path / "BC-100-a-plan.md").write_text("# BC-100\nnothing here\n")
    (tmp_path / "BC-100-b-plan.md").write_text("## Shipping evidence\n")
    picked = vep._find_plan_for_bc(tmp_path, "BC-100-whatever")
    assert picked is not None
    assert picked.name == "BC-100-b-plan.md"


def test_find_plan_returns_none_when_no_candidates(tmp_path: Path) -> None:
    assert vep._find_plan_for_bc(tmp_path, "BC-999-whatever") is None


def test_find_plan_falls_back_to_first_when_none_have_section(tmp_path: Path) -> None:
    (tmp_path / "BC-200-a-plan.md").write_text("plan a without section\n")
    (tmp_path / "BC-200-b-plan.md").write_text("plan b without section\n")
    picked = vep._find_plan_for_bc(tmp_path, "BC-200-x")
    assert picked is not None
    assert picked.name == "BC-200-a-plan.md"  # deterministic sort


def test_test_functions_in_file_extracts_names(tmp_path: Path) -> None:
    f = tmp_path / "test_x.py"
    f.write_text(
        "import pytest\ndef test_one(): pass\ndef _private(): pass\ndef test_two(x): return x\n"
    )
    assert vep._test_functions_in_file(f) == ["test_one", "test_two"]


def test_test_functions_in_file_tolerates_missing_file(tmp_path: Path) -> None:
    assert vep._test_functions_in_file(tmp_path / "missing.py") == []
