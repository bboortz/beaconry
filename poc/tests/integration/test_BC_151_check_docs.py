"""BC-151 check-docs fleet-tier wrapper (shepherd SH-055).

Asserts beaconry's Makefile exposes the fleet-wide `check-docs` wrapper
introduced by shepherd SH-055:
    - `.PHONY: check-docs` declared
    - `check-docs:` target has the six verify-* prerequisites
    - PUSH_STEPS AND BRANCH_STEPS include `check-docs` so the gate runs
      on both the pre-push hook and ci-branch (CI on PR)

Covers:
    - CAP-S009: Fleet-tier check-docs wrapper (shepherd SH-055)
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = REPO_ROOT / "Makefile"

EXPECTED_DELEGATES = (
    "verify-docs-drift",
    "verify-docs-layout",
    "verify-version-sync",
    "verify-evidence-pointers",
    "verify-traceability",
    "verify-install-doc",
)


def _phony_targets() -> set[str]:
    """Return every .PHONY target declared anywhere in the Makefile."""
    text = MAKEFILE.read_text()
    targets: set[str] = set()
    for line in text.splitlines():
        if line.startswith(".PHONY:"):
            targets.update(line[len(".PHONY:") :].split())
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


def test_check_docs_declared_phony() -> None:
    assert "check-docs" in _phony_targets(), ".PHONY: check-docs missing from Makefile"


def test_check_docs_target_exists() -> None:
    """check-docs: target body present in the Makefile."""
    text = MAKEFILE.read_text()
    assert re.search(r"^check-docs:\s", text, re.MULTILINE), (
        "check-docs: target body missing from Makefile"
    )


def test_check_docs_has_six_delegates() -> None:
    """check-docs lists all six expected verify-* targets as prerequisites.

    Beaconry's check-docs is a pure delegate — no new logic, only unifies the
    entry point. Each delegate must appear on the `check-docs:` prereq line.
    """
    text = MAKEFILE.read_text()
    match = re.search(r"^check-docs:\s*(.+)$", text, re.MULTILINE)
    assert match is not None, "check-docs: target not found"
    prereqs = match.group(1).split()
    for delegate in EXPECTED_DELEGATES:
        assert delegate in prereqs, (
            f"check-docs missing delegate '{delegate}'. Got prereqs: {prereqs}"
        )


def test_check_docs_dry_run_invokes_all_delegates() -> None:
    """`make -n check-docs` emits a marker for each verify-* delegate.

    Beaconry's verify-* targets invoke `python -m poc.scripts.verify_<name>`
    (underscores), so we match on the module form.
    """
    out = _dry_run("check-docs")
    for delegate in EXPECTED_DELEGATES:
        module = delegate.replace("-", "_")
        assert module in out, f"check-docs dry-run missing delegate marker '{module}':\n{out}"


def test_push_steps_includes_check_docs() -> None:
    """PUSH_STEPS must contain check-docs so pre-push gate catches doc drift."""
    text = MAKEFILE.read_text()
    match = re.search(r"^PUSH_STEPS\s*[:?]?=\s*(.+)$", text, re.MULTILINE)
    assert match is not None, "PUSH_STEPS declaration not found"
    assert "check-docs" in match.group(1).split(), (
        f"PUSH_STEPS missing 'check-docs'. Got: {match.group(1)!r}"
    )


def test_branch_steps_includes_check_docs() -> None:
    """BRANCH_STEPS must contain check-docs so ci-branch catches doc drift on PR."""
    text = MAKEFILE.read_text()
    match = re.search(r"^BRANCH_STEPS\s*[:?]?=\s*(.+)$", text, re.MULTILINE)
    assert match is not None, "BRANCH_STEPS declaration not found"
    assert "check-docs" in match.group(1).split(), (
        f"BRANCH_STEPS missing 'check-docs'. Got: {match.group(1)!r}"
    )


def test_hook_push_dry_run_includes_check_docs() -> None:
    """`make -n hook-push` must invoke the check-docs chain."""
    out = _dry_run("hook-push")
    # At minimum the first delegate (verify_docs_drift) must appear once
    # check-docs is wired into hook-push via PUSH_STEPS.
    assert "verify_docs_drift" in out, (
        f"hook-push dry-run missing check-docs chain marker 'verify_docs_drift':\n{out}"
    )


def test_ci_branch_dry_run_includes_check_docs() -> None:
    """`make -n ci-branch` must invoke the check-docs chain."""
    out = _dry_run("ci-branch")
    assert "verify_docs_drift" in out, (
        f"ci-branch dry-run missing check-docs chain marker 'verify_docs_drift':\n{out}"
    )
