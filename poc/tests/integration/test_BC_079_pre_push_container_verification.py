"""BC-079 — pre-push hook invokes make check-full.

Covers:
    - CAP-T162: Pre-push hook invokes `make check-full` with CLI-smoke skipped
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
MAKEFILE = REPO_ROOT / "Makefile"


def _hooks() -> list[dict]:
    doc = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    hooks: list[dict] = []
    for repo in doc["repos"]:
        hooks.extend(repo.get("hooks", []))
    return hooks


# ── Pre-push config ─────────────────────────────────────────────────────────


def test_pre_push_hook_runs_make_check_full() -> None:
    """CAP-T162: pre-push must invoke `make check-full` so container-level
    gates (compose + real_tools + test-e2e + test-scenarios) block pushes."""
    hooks = _hooks()
    pre_push_hooks = [h for h in hooks if "pre-push" in h.get("stages", [])]
    assert pre_push_hooks, "pre-push stage has no hooks configured"
    entries = " ".join(str(h.get("entry", "")) for h in pre_push_hooks)
    assert "check-full" in entries, (
        "no pre-push hook calls `make check-full`; container gates do not block pushes"
    )


def test_pre_push_skips_cli_smoke() -> None:
    """CAP-T162: test-cli needs a live operator stack; pre-push can't assume
    one is running. Hook must set BEACONRY_SKIP_CLI_SMOKE=1 so test-cli
    skips cleanly instead of failing on a missing hub."""
    hooks = _hooks()
    pre_push_hooks = [h for h in hooks if "pre-push" in h.get("stages", [])]
    entries = " ".join(str(h.get("entry", "")) for h in pre_push_hooks)
    assert "BEACONRY_SKIP_CLI_SMOKE" in entries, (
        "pre-push must set BEACONRY_SKIP_CLI_SMOKE=1 so test-cli doesn't block "
        "developers without a live stack"
    )


def test_pre_push_no_standalone_pytest() -> None:
    """CAP-T162: `make check-full` includes pytest. The standalone
    pytest-pre-push hook is now redundant — remove it to keep the hook
    chain minimal and the semantics unambiguous."""
    hooks = _hooks()
    pre_push_hook_names = {h.get("id", "") for h in _hooks() if "pre-push" in h.get("stages", [])}
    assert "pytest-pre-push" not in pre_push_hook_names, (
        "standalone pytest-pre-push hook is redundant with `make check-full`; remove it"
    )
    assert hooks, "pre-commit config lost all hooks"


# ── Makefile swallowing fix ─────────────────────────────────────────────────


def test_test_e2e_does_not_swallow_failures() -> None:
    """CAP-T162: Makefile:test-e2e previously prefixed its pytest invocation
    with `-$(PYTHON)` which made Make ignore the exit code. `exit $$EXIT`
    at the end already propagates; the `-` was redundant and silently
    defeated the propagation."""
    makefile_text = MAKEFILE.read_text()
    # Isolate the test-e2e recipe.
    match = re.search(
        r"^test-e2e:\s*\n((?:^[^\S\n].*\n|^\s*#.*\n|^\s*\n)+)", makefile_text, re.MULTILINE
    )
    assert match, "Could not find test-e2e: recipe in Makefile"
    recipe = match.group(1)
    assert "-$(PYTHON) -m pytest" not in recipe, (
        "Makefile:test-e2e still has `-$(PYTHON) -m pytest` — the leading `-` "
        "makes Make ignore the exit code, so test-e2e failures land as green builds"
    )
    assert "$(PYTHON) -m pytest" in recipe, "test-e2e no longer invokes pytest"


def test_no_dash_prefix_on_check_subcommands() -> None:
    """CAP-T162: broader guard — no `-$(MAKE)` or `-$(PYTHON) -m pytest` in
    check / check-full / test-* recipes. The `-` prefix on `install-hooks`
    and `clean` is intentional (best-effort cleanup); flag anything else."""
    makefile_text = MAKEFILE.read_text()
    violations: list[str] = []
    for lineno, line in enumerate(makefile_text.splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        if "pytest" in stripped or ("$(MAKE)" in stripped and "test" in stripped):
            violations.append(f"line {lineno}: `{stripped}`")
    assert not violations, "Makefile has swallowing-prone recipes:\n  " + "\n  ".join(violations)
