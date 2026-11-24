"""BC-152 verify-index-status drift check (shepherd SH-056).

Asserts beaconry ships the fleet-wide `verify-index-status` doc-staleness
check introduced by shepherd SH-056:
    - `scripts/verify-index-status.sh` exists and is executable
    - Script body is byte-identical to shepherd's reference (fleet
      invariant — script lives on each repo, logic must not fork)
    - `.PHONY: verify-index-status` declared
    - `verify-index-status:` target invokes the script
    - `check-docs` prereqs include `verify-index-status`
    - Live pass: running on current-tree returns 0 (clean)
    - Live drift: flipping a shipped row's status in INDEX.md returns 1
      with a specific mismatch message, and the row is restored

Covers:
    - CAP-S010: Fleet-tier verify-index-status drift check (shepherd SH-056)
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest

# pytest-xdist parallel execution is beaconry's default. Two tests in this
# module touch docs/stories/INDEX.md live (one reads it clean, one patches +
# restores). Grouping pins them to the same worker so the patcher's restore
# finishes before the clean reader runs.
pytestmark = pytest.mark.xdist_group("BC_152_live_index_mutation")

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = REPO_ROOT / "Makefile"
SCRIPT = REPO_ROOT / "scripts" / "verify-index-status.sh"
INDEX = REPO_ROOT / "docs" / "stories" / "INDEX.md"

# Shepherd reference path (fleet invariant: byte-identical copy). Resolves to
# the sibling checkout for local dev; CI must mount or skip. The assertion
# is soft-skipped when the reference isn't reachable so CI doesn't flap on
# shepherd being absent.
SHEPHERD_REF = Path.home() / "git" / "shepherd" / "scripts" / "verify-index-status.sh"


def _phony_targets() -> set[str]:
    text = MAKEFILE.read_text()
    targets: set[str] = set()
    for line in text.splitlines():
        if line.startswith(".PHONY:"):
            targets.update(line[len(".PHONY:") :].split())
    return targets


def _run_script() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_script_exists_and_executable() -> None:
    assert SCRIPT.is_file(), f"{SCRIPT} missing"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} not executable"


def test_script_byte_identical_to_shepherd_reference() -> None:
    """Script body is a byte-identical copy of shepherd's canonical version.

    Fleet invariant (SH-056): the drift-check script lives on every managed
    repo, but the logic is authored once in shepherd. Divergence here means
    each repo answers "is my INDEX stale?" with a different definition.
    """
    if not SHEPHERD_REF.is_file():
        import pytest

        pytest.skip(f"shepherd reference not reachable: {SHEPHERD_REF}")
    local_sha = hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    ref_sha = hashlib.sha256(SHEPHERD_REF.read_bytes()).hexdigest()
    assert local_sha == ref_sha, (
        f"scripts/verify-index-status.sh has drifted from shepherd reference.\n"
        f"  local sha256:  {local_sha}\n"
        f"  shepherd sha256: {ref_sha}\n"
        f"Fix: cp {SHEPHERD_REF} {SCRIPT}"
    )


def test_verify_index_status_declared_phony() -> None:
    assert "verify-index-status" in _phony_targets(), (
        ".PHONY: verify-index-status missing from Makefile"
    )


def test_verify_index_status_target_exists() -> None:
    """`verify-index-status:` target invokes the script."""
    text = MAKEFILE.read_text()
    match = re.search(r"^verify-index-status:\s*$\n(^\t.+$)+", text, re.MULTILINE)
    assert match is not None, "verify-index-status: target body missing from Makefile"
    assert "scripts/verify-index-status.sh" in match.group(0), (
        f"verify-index-status: body does not invoke the script:\n{match.group(0)}"
    )


def test_check_docs_includes_verify_index_status() -> None:
    """`check-docs` prereqs must include `verify-index-status` (BC-151 wiring)."""
    text = MAKEFILE.read_text()
    match = re.search(r"^check-docs:\s*(.+)$", text, re.MULTILINE)
    assert match is not None, "check-docs: target not found"
    prereqs = match.group(1).split()
    assert "verify-index-status" in prereqs, (
        f"check-docs missing 'verify-index-status'. Got prereqs: {prereqs}"
    )


def test_help_echoes_verify_index_status() -> None:
    """BC-016 docs-drift: every .PHONY target must have a help-line echo."""
    text = MAKEFILE.read_text()
    assert "make verify-index-status" in text, (
        "help: block missing 'make verify-index-status' echo line "
        "(BC-016 requires every .PHONY in help:)"
    )


def test_live_pass_on_clean_tree() -> None:
    """Running the script on the current tree returns 0."""
    result = _run_script()
    assert result.returncode == 0, (
        f"verify-index-status.sh failed on clean tree:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "ok" in result.stdout, f"expected 'ok' in output, got: {result.stdout!r}"


def test_live_drift_detection() -> None:
    """Flipping a shipped story's INDEX row to draft trips the script.

    Picks BC-000 (always shipped, first row in the table) to minimise the
    blast radius of a failed restore. The patch is in-process, reverted
    in a `finally` block before any assertion, so a failure mid-test still
    leaves the repo clean.
    """
    original = INDEX.read_text()
    # BC-000 is always shipped; flip its status column to `draft`.
    patched = re.sub(
        r"^(\| BC-000 \| )shipped(\s+\|)",
        r"\1draft      \2",
        original,
        count=1,
        flags=re.MULTILINE,
    )
    assert patched != original, "patch did not match BC-000 row — update the test"

    try:
        INDEX.write_text(patched)
        result = _run_script()
    finally:
        INDEX.write_text(original)

    assert result.returncode == 1, (
        f"expected rc=1 on drift, got rc={result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "BC-000" in result.stdout, f"expected BC-000 in mismatch report, got: {result.stdout!r}"
    assert "frontmatter says 'shipped'" in result.stdout, (
        f"expected specific mismatch message, got: {result.stdout!r}"
    )

    # Belt-and-braces: assert restore actually worked so a later test doesn't
    # inherit a broken tree.
    assert INDEX.read_text() == original, "INDEX.md restore failed"
