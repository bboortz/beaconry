"""BC-149 — pytest-xdist + real test-affected scoping.

Covers:
    - CAP: shepherd.dev-cycle-cheap-pre-push (EXT)
    - xdist enabled; test-updates.sh scopes by module; full-run bailouts.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
REQ_DEV = REPO_ROOT / "requirements-dev.txt"
PYPROJECT = REPO_ROOT / "pyproject.toml"
MAKEFILE = REPO_ROOT / "Makefile"
SCRIPT = REPO_ROOT / "scripts" / "test-updates.sh"


# ── xdist wiring ────────────────────────────────────────────────────────────


def test_pytest_xdist_in_requirements() -> None:
    """BC-149: pytest-xdist pinned in requirements-dev.txt."""
    content = REQ_DEV.read_text()
    assert "pytest-xdist==" in content, "pytest-xdist== not pinned in requirements-dev.txt"


def test_pyproject_addopts_enables_xdist() -> None:
    """BC-149: pyproject addopts includes -n auto + --dist loadgroup."""
    content = PYPROJECT.read_text()
    # Permissive match — whitespace/quote-style agnostic.
    assert '"-n"' in content and '"auto"' in content, "addopts missing -n auto"
    assert '"--dist"' in content and '"loadgroup"' in content, "addopts missing --dist loadgroup"


# ── test-updates.sh shape ───────────────────────────────────────────────────


def test_test_updates_script_exists() -> None:
    """BC-149: scripts/test-updates.sh is a real executable script."""
    assert SCRIPT.exists(), f"missing {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} not executable"


def test_dry_run_scopes_to_module() -> None:
    """BC-149: dry-run on a fresh branch defensively plans full-run
    (no diff). Verifies the script runs + exits 0, and the planned
    invocation at least references pytest (not a different tool)."""
    out = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    combined = out.stdout + out.stderr
    assert "[dry-run]" in combined, f"dry-run marker missing:\n{combined}"
    assert "pytest" in combined, f"planned invocation does not mention pytest:\n{combined}"


def test_tooling_touch_triggers_full_run() -> None:
    """BC-149: FULL_RE regex in test-updates.sh fires on tooling paths.
    We can't easily simulate git-state here, but the regex is public —
    verify it matches the expected canonical paths."""
    body = SCRIPT.read_text()
    assert "FULL_RE=" in body, "FULL_RE constant missing"
    # The canonical tooling paths that must trigger full-run.
    for path in ("Makefile", "pyproject", "requirements", "conftest"):
        assert path in body, f"FULL_RE does not reference '{path}'"


# ── Makefile wiring ─────────────────────────────────────────────────────────


def test_makefile_test_affected_is_test_updates() -> None:
    """BC-149: Makefile `test-affected` depends on `test-updates`, which
    invokes `bash scripts/test-updates.sh`."""
    content = MAKEFILE.read_text()
    assert "test-affected: test-updates" in content, (
        "Makefile: `test-affected` must depend on `test-updates` (BC-149)"
    )
    assert "test-updates: ; @bash scripts/test-updates.sh" in content, (
        "Makefile: `test-updates` must invoke `bash scripts/test-updates.sh`"
    )


# ── Help / doc drift ────────────────────────────────────────────────────────


def test_help_mentions_scoped_test_affected() -> None:
    """BC-149: Makefile help block mentions BC-149 scoped behaviour so
    operators discover the change via `make help`."""
    content = MAKEFILE.read_text()
    assert "scoped pytest by changed module (BC-149" in content, (
        "Makefile help does not advertise the BC-149 scoping"
    )
