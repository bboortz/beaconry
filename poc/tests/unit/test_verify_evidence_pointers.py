"""Unit tests for the evidence-pointer lint helpers.

Covers:
    - CAP-T057: Evidence-pointer lint — pointer-extraction regex, node-id
      normalization, status-cell strictness check
"""

from __future__ import annotations

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
