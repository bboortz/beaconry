"""BC-145 Shepherd Release 0 POC — integration-tier acceptance.

Covers:
    - CAP-S001: Trivy fs scan runs on schedule — workflow exists + scheduled + dispatch-triggerable
    - CAP-S002: Shepherd agent step runs with no secrets beyond the Anthropic API key
    - CAP-S003: Shepherd agent output is a diff; a separate trusted step applies it
    - CAP-S004: Protected-files blocklist — validate.sh rejects protected paths
    - CAP-S005: Shepherd diff-size cap — validate.sh rejects > 50 changed lines
    - CAP-S006: Shepherd scope level 1 — agent prompt binds to deps + lockfiles
    - CAP-S007: Supply-chain — every uses: line is 40-hex SHA-pinned

No network required. Mirrors bboortz/healthy HC-008 shell acceptance.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "shepherd-poc.yml"
VALIDATE = REPO / "scripts" / "shepherd-poc" / "validate.sh"
INSTALL_TRIVY = REPO / "scripts" / "shepherd-poc" / "install-trivy.sh"
AGENT_CALL = REPO / "scripts" / "shepherd-poc" / "agent-call.sh"
OPEN_PR = REPO / "scripts" / "shepherd-poc" / "open-pr.sh"
REGEN_LOCKFILE = REPO / "scripts" / "shepherd-poc" / "regen-lockfile.sh"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ────────────────────────────────────────────────────────────────────
# Scenario 1 — workflow exists, is scheduled, is dispatch-triggerable
# ────────────────────────────────────────────────────────────────────


def test_workflow_file_exists() -> None:
    assert WORKFLOW.is_file(), f"missing {WORKFLOW}"


def test_workflow_has_cron() -> None:
    assert "cron:" in _read(WORKFLOW)


def test_workflow_has_workflow_dispatch() -> None:
    assert "workflow_dispatch:" in _read(WORKFLOW)


# ────────────────────────────────────────────────────────────────────
# Scenario 2 — no third-party Action wrapper for Trivy; SHA-pin invariant
# ────────────────────────────────────────────────────────────────────


def test_no_trivy_action_wrapper() -> None:
    assert "aquasecurity/trivy-action" not in _read(WORKFLOW), (
        "SH-001 invariant: no third-party Trivy wrapper in the critical path"
    )


_USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s+([^\s@]+)@([^\s]+)", re.MULTILINE)


def test_every_uses_is_sha_pinned() -> None:
    bad = [
        (owner_repo, ref)
        for owner_repo, ref in _USES_RE.findall(_read(WORKFLOW))
        if not re.fullmatch(r"[0-9a-f]{40}", ref)
    ]
    assert bad == [], f"non-SHA uses: refs in shepherd-poc.yml: {bad}"


# ────────────────────────────────────────────────────────────────────
# Scenario 3 — scripts are present and executable
# ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [INSTALL_TRIVY, AGENT_CALL, VALIDATE, REGEN_LOCKFILE, OPEN_PR],
    ids=lambda p: p.name,
)
def test_script_is_executable(path: Path) -> None:
    assert path.is_file(), f"missing {path}"
    mode = path.stat().st_mode
    assert mode & 0o111, f"{path} not executable (mode={mode:o})"


# ────────────────────────────────────────────────────────────────────
# Scenario 4 — validate.sh blocks protected paths and oversize diffs,
#               accepts small allowed diffs.
# ────────────────────────────────────────────────────────────────────


def _run_validate(fixture: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(VALIDATE), str(FIXTURES / fixture)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_validate_blocks_workflow_touch() -> None:
    result = _run_validate("BC_145_blocked_workflow.patch")
    assert result.returncode == 2, result.stderr
    assert ".github/workflows" in result.stderr


def test_validate_blocks_shepherd_yml_touch() -> None:
    # Prompt-injection drill at the validator layer: a diff that an injected
    # agent would produce if told to "modify .shepherd.yml" must be blocked.
    result = _run_validate("BC_145_blocked_shepherd_yml.patch")
    assert result.returncode == 2, result.stderr


def test_validate_blocks_oversize_diff() -> None:
    result = _run_validate("BC_145_oversize.patch")
    assert result.returncode == 2, result.stderr


def test_validate_accepts_small_allowed_diff() -> None:
    result = _run_validate("BC_145_ok_small.patch")
    assert result.returncode == 0, (
        f"validate.sh rejected an allowed pyproject.toml bump: "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ────────────────────────────────────────────────────────────────────
# Scenario 5 — install-trivy.sh --dry-run names cosign + sigstore
# ────────────────────────────────────────────────────────────────────


def test_install_trivy_dry_run_names_cosign_verify_blob() -> None:
    result = subprocess.run(
        [str(INSTALL_TRIVY), "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "cosign verify-blob" in result.stdout
    assert "sigstore" in result.stdout
    assert "new-bundle-format" in result.stdout


# ────────────────────────────────────────────────────────────────────
# Scenario 6 — agent prompt binds scope level 1 + protected files
# ────────────────────────────────────────────────────────────────────


def test_prompt_declares_manifest_only_scope() -> None:
    assert "manifest files only" in _read(AGENT_CALL), (
        "hybrid flow: agent writes manifest diffs only; "
        "lockfile is regenerated by regen-lockfile.sh afterwards"
    )


def test_prompt_names_shepherd_yml_as_forbidden() -> None:
    assert ".shepherd.yml" in _read(AGENT_CALL)


def test_prompt_specifies_no_fix_possible_fallback() -> None:
    assert "NO_FIX_POSSIBLE" in _read(AGENT_CALL)


# ────────────────────────────────────────────────────────────────────
# Scenario 6b — workflow wires the hybrid flow (setup-node + regen)
# ────────────────────────────────────────────────────────────────────


def test_workflow_wires_setup_node_step() -> None:
    assert "Setup Node.js" in _read(WORKFLOW), (
        "hybrid flow needs setup-node to run `npm install --package-lock-only`"
    )


def test_workflow_wires_regen_lockfile_step() -> None:
    assert "Regenerate lockfile" in _read(WORKFLOW)


def test_workflow_wires_second_validate_step() -> None:
    assert "Validate combined diff" in _read(WORKFLOW), (
        "validator must re-run on the manifest+lockfile combined diff after regen"
    )


# ────────────────────────────────────────────────────────────────────
# Scenario 7 — sandbox.no-secrets: agent step's env: does not expose
#               GITHUB_TOKEN. The only secret allowed is ANTHROPIC_API_KEY.
# ────────────────────────────────────────────────────────────────────


def _extract_agent_step_block(workflow_text: str) -> str:
    """Return the YAML slice between 'name: Agent call' and the next step.

    Deliberately regex, not a YAML parser: we want to catch accidental
    additions anywhere in the step's indented body — whether in env: or
    anywhere else — without taking a PyYAML dep.
    """
    lines = workflow_text.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        if re.match(r"^\s+- name: Agent call", line):
            inside = True
            out.append(line)
            continue
        if inside and re.match(r"^\s+- name:", line):
            break
        if inside:
            out.append(line)
    return "\n".join(out)


def test_agent_step_env_only_has_anthropic_api_key() -> None:
    block = _extract_agent_step_block(_read(WORKFLOW))
    assert "ANTHROPIC_API_KEY" in block, "agent step must pass ANTHROPIC_API_KEY"
    assert "GITHUB_TOKEN" not in block, (
        "agent step must NOT see GITHUB_TOKEN (shepherd.sandbox.no-secrets)"
    )
