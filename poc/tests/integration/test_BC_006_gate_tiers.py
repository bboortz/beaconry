"""BC-006 gate-tier structure — asserts Makefile declares fast + full tiers.

Covers:
    - CAP-T023: make check includes smoke
    - CAP-T024: make check-full includes opt-in markers
"""

from __future__ import annotations

import re
from pathlib import Path

MAKEFILE = Path(__file__).resolve().parents[3] / "Makefile"


def _find_target_deps(target: str) -> list[str]:
    """Return the dependency list for a Makefile target (first match)."""
    text = MAKEFILE.read_text()
    # Match a non-pattern rule line like `check: lint type test ...`
    pattern = rf"^{re.escape(target)}\s*:\s*(.+?)$"
    m = re.search(pattern, text, re.MULTILINE)
    if not m:
        return []
    return m.group(1).split()


def test_check_depends_on_smoke() -> None:
    """BC-006: fast gate must invoke smoke so broken mock pipeline fails CI."""
    deps = _find_target_deps("check")
    assert "smoke" in deps, (
        f"`make check` must depend on `smoke`; got deps={deps!r}. "
        "Add 'smoke' to the check: target in Makefile."
    )


def test_check_depends_on_other_fast_tier_steps() -> None:
    """Fast tier retains lint + type + test + verify-traceability."""
    deps = _find_target_deps("check")
    for required in ("lint", "type", "test", "verify-traceability"):
        assert required in deps, f"`make check` must depend on {required!r}; got {deps!r}"


def test_check_full_target_exists_and_depends_on_check() -> None:
    """Full gate is additive on top of the fast tier."""
    deps = _find_target_deps("check-full")
    assert deps, "`make check-full` target not declared in Makefile"
    assert "check" in deps, (
        f"`make check-full` must depend on `check`; got deps={deps!r}. "
        "Full tier is a superset of the fast tier."
    )


def test_check_full_recipe_invokes_opt_in_markers() -> None:
    """Full gate must run pytest with the opt-in markers (compose + real_tools)."""
    text = MAKEFILE.read_text()
    # Find the check-full target and its recipe (indented lines).
    m = re.search(r"^check-full\s*:.*\n((?:\t.+\n?)+)", text, re.MULTILINE)
    assert m, "`check-full` target has no recipe body"
    recipe = m.group(1)
    assert "compose" in recipe and "real_tools" in recipe, (
        f"`make check-full` recipe must invoke pytest with 'compose' + 'real_tools' markers; "
        f"got:\n{recipe}"
    )
