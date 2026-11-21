"""BC-145 Shepherd on beaconry — integration-tier acceptance.

R1 rewrite: the R0 inline-script POC migrated to the shepherd reusable
workflow (SH-007). The R0 scripts/shepherd-poc/ tree and the Agent call /
Setup Node / Regenerate lockfile / Validate combined diff workflow steps
are retired from this repo — they run inside bboortz/shepherd's reusable
workflow now, and are tested there. This file asserts the caller WIRING:
shepherd.yml + shepherd-gates.yml pin the reusable workflow at an SHA.

Covers:
    - CAP-S001: shepherd caller is scheduled + dispatch-triggerable
    - CAP-S004: Protected-files blocklist — validate.sh rejects protected paths
    - CAP-S005: Shepherd diff-size cap — validate.sh rejects > 50 changed lines
    - CAP-S006: Shepherd scope level 1 — agent prompt binds to deps + lockfiles
    - CAP-S007: Supply-chain — every uses: line is 40-hex SHA-pinned

No network required. Mirrors bboortz/healthy HC-008 shell acceptance.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "shepherd.yml"
GATES = REPO / ".github" / "workflows" / "shepherd-gates.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ────────────────────────────────────────────────────────────────────
# Scenario 1 — workflow exists, is scheduled, is dispatch-triggerable
# ────────────────────────────────────────────────────────────────────


def test_workflow_file_exists() -> None:
    assert WORKFLOW.is_file(), f"missing {WORKFLOW}"


def test_gates_workflow_file_exists() -> None:
    assert GATES.is_file(), f"missing {GATES}"


def test_workflow_has_cron() -> None:
    assert "cron:" in _read(WORKFLOW)


def test_workflow_has_workflow_dispatch() -> None:
    assert "workflow_dispatch:" in _read(WORKFLOW)


def test_workflow_pins_reusable_shepherd() -> None:
    assert "bboortz/shepherd/.github/workflows/reusable-shepherd.yml@" in _read(WORKFLOW)


def test_gates_pins_reusable_shepherd_gates() -> None:
    assert (
        "bboortz/shepherd/.github/workflows/reusable-shepherd-gates.yml@"
        in _read(GATES)
    )


# ────────────────────────────────────────────────────────────────────
# Scenario 2 — no third-party Action wrapper for Trivy; SHA-pin invariant
# ────────────────────────────────────────────────────────────────────


def test_no_trivy_action_wrapper() -> None:
    assert "aquasecurity/trivy-action" not in _read(WORKFLOW), (
        "SH-001 invariant: no third-party Trivy wrapper in the critical path"
    )


_USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s+([^\s@]+)@([^\s]+)", re.MULTILINE)


def test_every_uses_is_sha_pinned() -> None:
    for path in (WORKFLOW, GATES):
        bad = [
            (owner_repo, ref)
            for owner_repo, ref in _USES_RE.findall(_read(path))
            if not re.fullmatch(r"[0-9a-f]{40}", ref)
        ]
        assert bad == [], f"non-SHA uses: refs in {path.name}: {bad}"


# Scenarios 3–7 were retired here: the R0 inline scripts
# (scripts/shepherd-poc/validate.sh, install-trivy.sh, agent-call.sh,
# regen-lockfile.sh, open-pr.sh) and the Agent call / Setup Node /
# Regenerate lockfile / Validate combined diff workflow steps no longer
# live on this repo — they run inside bboortz/shepherd's reusable
# workflow, and are tested in shepherd's own suite (CAPs:
# shepherd.sandbox.*, shepherd.gate.diff-size, shepherd.scope.level-1).
# What remains here is the caller-wiring contract on beaconry.
