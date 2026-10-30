"""BC-019 — grep-verify every shipping-evidence pointer at make-check time.

Covers:
    - CAP-T057: Evidence-pointer lint — every ✓ shipping-evidence row with a
      test-shaped pointer is grep-verified against actual pytest node ids
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

SCRIPT = "poc.scripts.verify_evidence_pointers"


def _run(docs_root: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Invoke the lint pointed at a fixture docs-root (plans + tests dirs)."""
    import os

    env = os.environ.copy()
    env.update(extra_env or {})
    env["BEACONRY_PLANS_DIR"] = str(docs_root / "plan")
    env["BEACONRY_TESTS_ROOT"] = str(docs_root / "tests")
    return subprocess.run(
        [sys.executable, "-m", SCRIPT],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        cwd=str(Path(__file__).resolve().parents[3]),  # repo root
    )


def _write_plan(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip("\n"))


def _write_test(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip("\n"))


def _fixture_test_file(path: Path, test_names: list[str]) -> None:
    body = '"""fixture — Covers: - CAP-T057"""\n\n' + "\n".join(
        f"def {name}() -> None:\n    assert True\n" for name in test_names
    )
    _write_test(path, body)


# ── Scenario 1 ───────────────────────────────────────────────────────────────


def test_dead_file_pointer_fails(tmp_path: Path) -> None:
    _write_plan(
        tmp_path / "plan" / "BC-900-fixture-plan.md",
        """
        # BC-900 — fixture

        ## Shipping evidence

        | Scenario / goal | Evidence type | Pointer | Status |
        |---|---|---|---|
        | Dead file | unit test | `does_not_exist.py::test_x` | ✓ |
        """,
    )
    r = _run(tmp_path)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "does_not_exist.py" in (r.stdout + r.stderr)
    assert "BC-900" in (r.stdout + r.stderr)


# ── Scenario 2 ───────────────────────────────────────────────────────────────


def test_dead_node_id_fails(tmp_path: Path) -> None:
    _fixture_test_file(tmp_path / "tests" / "unit" / "test_fx.py", ["test_present"])
    _write_plan(
        tmp_path / "plan" / "BC-900-fixture-plan.md",
        """
        # BC-900 — fixture

        ## Shipping evidence

        | Scenario / goal | Evidence type | Pointer | Status |
        |---|---|---|---|
        | Dead node | unit test | `test_fx.py::test_missing` | ✓ |
        """,
    )
    r = _run(tmp_path)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "test_missing" in (r.stdout + r.stderr)


# ── Scenario 3 ───────────────────────────────────────────────────────────────


def test_non_test_shaped_pointer_skipped(tmp_path: Path) -> None:
    _write_plan(
        tmp_path / "plan" / "BC-900-fixture-plan.md",
        """
        # BC-900 — fixture

        ## Shipping evidence

        | Scenario / goal | Evidence type | Pointer | Status |
        |---|---|---|---|
        | Prose row | live manual | run `make demo` and curl `/__driver__/sent` | ✓ |
        | Make target row | lint diff | `make check` output | ✓ |
        | Non-test .py file (prose diff anchor) | code ref | `bait_snooze.py` ad-hoc script | ✓ |
        | Prod source file ref | code ref | `hub.py` lines 100-200 | ✓ |
        """,
    )
    r = _run(tmp_path)
    assert r.returncode == 0, (
        f"non-test-shaped pointers must be skipped; got stdout={r.stdout} stderr={r.stderr}"
    )


def test_bare_test_file_without_node_id_still_checked(tmp_path: Path) -> None:
    """File-only pointer that starts with `test_` IS strictly checked — catches
    dead test-file references like BC-013's `test_BC_013_compose_scout_port.py`."""
    _write_plan(
        tmp_path / "plan" / "BC-900-fixture-plan.md",
        """
        # BC-900 — fixture

        ## Shipping evidence

        | Scenario / goal | Evidence type | Pointer | Status |
        |---|---|---|---|
        | Dead test file | integration test | `test_definitely_does_not_exist.py` | ✓ |
        """,
    )
    r = _run(tmp_path)
    assert r.returncode != 0
    assert "test_definitely_does_not_exist.py" in (r.stdout + r.stderr)


# ── Scenario 4 ───────────────────────────────────────────────────────────────


def test_deferred_rows_skipped(tmp_path: Path) -> None:
    """⚠️ and ❌ rows are not grep-verified — they're explicit deferrals."""
    _write_plan(
        tmp_path / "plan" / "BC-900-fixture-plan.md",
        """
        # BC-900 — fixture

        ## Shipping evidence

        | Scenario / goal | Evidence type | Pointer | Status |
        |---|---|---|---|
        | Deferred row | unit test | `does_not_exist.py::test_x` | ⚠️ |
        | Open row | unit test | `does_not_exist.py::test_x` | ❌ |
        """,
    )
    r = _run(tmp_path)
    assert r.returncode == 0, (
        f"deferred rows must not be strictly validated; got {r.stdout} {r.stderr}"
    )


# ── Scenario 5 ───────────────────────────────────────────────────────────────


def test_happy_path_silent_pass(tmp_path: Path) -> None:
    _fixture_test_file(tmp_path / "tests" / "unit" / "test_fx.py", ["test_a", "test_b"])
    _write_plan(
        tmp_path / "plan" / "BC-900-fixture-plan.md",
        """
        # BC-900 — fixture

        ## Shipping evidence

        | Scenario / goal | Evidence type | Pointer | Status |
        |---|---|---|---|
        | A | unit test | `test_fx.py::test_a` | ✓ |
        | B | unit test | `test_fx.py::test_b` | ✓ |
        """,
    )
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


# ── Scenario 6 ───────────────────────────────────────────────────────────────


def test_missing_section_grandfathered(tmp_path: Path) -> None:
    """Plan without a Shipping evidence section is skipped (same rule as traceability lint)."""
    _write_plan(
        tmp_path / "plan" / "BC-900-fixture-plan.md",
        """
        # BC-900 — fixture

        ## Situation

        Nothing to see here. No shipping evidence section — grandfathered.
        """,
    )
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


# ── Scenario 7 ───────────────────────────────────────────────────────────────


def test_parametrized_node_id_matches(tmp_path: Path) -> None:
    """Pointer like test_x.py::test_fn[case-1] matches the parametrized collection."""
    test_body = textwrap.dedent(
        """
        '''fixture — Covers: - CAP-T057'''
        import pytest

        @pytest.mark.parametrize("name", ["case-1", "case-2"])
        def test_param(name: str) -> None:
            assert True
        """
    ).lstrip()
    _write_test(tmp_path / "tests" / "unit" / "test_fx.py", test_body)
    _write_plan(
        tmp_path / "plan" / "BC-900-fixture-plan.md",
        """
        # BC-900 — fixture

        ## Shipping evidence

        | Scenario / goal | Evidence type | Pointer | Status |
        |---|---|---|---|
        | Param hit | unit test | `test_fx.py::test_param[case-1]` | ✓ |
        """,
    )
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr

    # Negative: parametrized pointer that doesn't match any collected node fails.
    _write_plan(
        tmp_path / "plan" / "BC-900-fixture-plan.md",
        """
        # BC-900 — fixture

        ## Shipping evidence

        | Scenario / goal | Evidence type | Pointer | Status |
        |---|---|---|---|
        | Dead param | unit test | `test_fx.py::test_param[case-missing]` | ✓ |
        """,
    )
    r = _run(tmp_path)
    assert r.returncode != 0, "parametrized dead-id must still fail"
    assert "case-missing" in (r.stdout + r.stderr)
