"""BC-150 hook-commit + hook-push fleet tiers (SH-042).

Asserts beaconry's Makefile exposes the two canonical fleet-tier wrappers
introduced by shepherd SH-042:
    - `hook-commit` — commit-stage gate (~15 s budget)
    - `hook-push`   — push-stage gate (~2 min budget)

Covers:
    - CAP-SH-042: fleet-wide hook-commit / hook-push tier naming
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = REPO_ROOT / "Makefile"


def _phony_targets() -> set[str]:
    """Return every .PHONY target declared anywhere in the Makefile."""
    text = MAKEFILE.read_text()
    targets: set[str] = set()
    for line in text.splitlines():
        # Fold logical continuation into one line.
        if line.startswith(".PHONY:"):
            targets.update(line[len(".PHONY:"):].split())
    return targets


def _dry_run(target: str) -> str:
    result = subprocess.run(
        ["make", "-n", target],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"`make -n {target}` exited {result.returncode}:\n{result.stderr}"
    )
    return result.stdout


def test_hook_commit_declared_phony() -> None:
    assert "hook-commit" in _phony_targets(), (
        ".PHONY: hook-commit missing from Makefile"
    )


def test_hook_push_declared_phony() -> None:
    assert "hook-push" in _phony_targets(), (
        ".PHONY: hook-push missing from Makefile"
    )


def test_hook_commit_dry_run_resolves() -> None:
    out = _dry_run("hook-commit")
    # Beaconry's COMMIT_STEPS = lint type. `lint` runs ruff + black; `type`
    # runs pyright. Any one of these markers must appear.
    assert re.search(r"ruff|black|pyright", out), (
        f"hook-commit dry-run missing lint/type markers:\n{out}"
    )


def test_hook_push_dry_run_resolves() -> None:
    out = _dry_run("hook-push")
    # PUSH_STEPS = check-fast verify-version-bumped verify-changelog verify-feature-complete.
    # Beaconry's verify-* targets invoke `python -m poc.scripts.verify_<name>`
    # (underscores), so match on the module form.
    for marker in ("verify_version_bumped", "verify_changelog", "verify_feature_complete"):
        assert marker in out, (
            f"hook-push dry-run missing step marker '{marker}':\n{out}"
        )


def test_check_fast_alias_target_exists() -> None:
    """check-fast is a forward-compat alias; SH-023 will later override."""
    assert "check-fast" in _phony_targets(), (
        ".PHONY: check-fast missing — required as a SH-042 alias"
    )
    text = MAKEFILE.read_text()
    assert re.search(r"^check-fast:\s", text, re.MULTILINE), (
        "check-fast: target body missing from Makefile"
    )
