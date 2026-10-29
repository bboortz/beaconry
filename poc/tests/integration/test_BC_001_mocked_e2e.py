"""BC-001 integration test — encodes the Gherkin acceptance for the mocked E2E demo.

Story: docs/stories/BC-001-mocked-e2e-demo.md
Plan:  docs/plan/BC-001-mocked-e2e-demo-plan.md

Runs the demo orchestrator programmatically with auto_confirm=True (no stdin).
Captures stdout. Asserts stage ordering, mock labels, and no-network invariants.

Covers:
    - CAP-001: Operator previews the full pipeline with no external dependencies
"""

from __future__ import annotations

import pytest


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raise if any HTTP call escapes the mock boundary."""
    import requests

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"unexpected HTTP call: args={args!r} kwargs={kwargs!r}")

    monkeypatch.setattr(requests, "post", _boom)
    monkeypatch.setattr(requests, "get", _boom)
    monkeypatch.setattr(requests, "put", _boom)
    monkeypatch.setattr(requests, "delete", _boom)


@pytest.mark.asyncio
async def test_demo_runs_full_pipeline_mocked(
    capsys: pytest.CaptureFixture[str],
    no_network: None,
) -> None:
    """Scenario: Full pipeline runs mocked in under 120 seconds (non-interactive)."""
    from poc.demo import run_demo

    exit_code = await run_demo(auto_confirm=True)

    assert exit_code == 0, "demo must exit 0 in auto-confirm mode"

    captured = capsys.readouterr()
    output = captured.out

    required_fragments_in_order = [
        "==> Scout: scanning fixture inventory",
        "==> Hub: SBOM received",
        "==> Finding: CVE-",
        "tier: Tier 0",
        "tier: Tier 1",
        "==> Notification",
        "fixed and verified",
    ]

    cursor = 0
    for fragment in required_fragments_in_order:
        idx = output.find(fragment, cursor)
        assert idx >= 0, f"missing stage {fragment!r} after position {cursor}; got:\n{output}"
        cursor = idx + len(fragment)


@pytest.mark.asyncio
async def test_mocked_actions_are_labelled(
    capsys: pytest.CaptureFixture[str],
    no_network: None,
) -> None:
    """Scenario: Mocked actions are explicitly labelled."""
    from poc.demo import run_demo

    await run_demo(auto_confirm=True)
    output = capsys.readouterr().out

    for label in ["[mock] docker pull", "[mock] docker tag", "[mock] telegram send"]:
        assert label in output, f"missing mock label {label!r} in demo output:\n{output}"


@pytest.mark.asyncio
async def test_both_tiers_processed(
    capsys: pytest.CaptureFixture[str],
    no_network: None,
) -> None:
    """Demo surfaces a Tier 0 auto-fix and a Tier 1 operator-confirm finding."""
    from poc.demo import run_demo

    await run_demo(auto_confirm=True)
    output = capsys.readouterr().out

    assert "Tier 0" in output, "Tier 0 finding (safe-class auto-fix) must appear"
    assert "Tier 1" in output, "Tier 1 finding (operator-confirm) must appear"
    assert output.count("Finding: CVE-") >= 2, "expected at least two findings surfaced"


@pytest.mark.asyncio
async def test_no_docker_subprocess(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    no_network: None,
) -> None:
    """Demo must not spawn any `docker` subprocess."""
    import subprocess

    original_run = subprocess.run
    original_create_subprocess_exec = __import__("asyncio").create_subprocess_exec
    original_create_subprocess_shell = __import__("asyncio").create_subprocess_shell

    def _check_cmd(cmd: object) -> None:
        if isinstance(cmd, (list, tuple)) and cmd and str(cmd[0]).endswith("docker"):
            raise AssertionError(f"demo invoked docker subprocess: {cmd!r}")
        if isinstance(cmd, str) and cmd.strip().split()[:1] == ["docker"]:
            raise AssertionError(f"demo invoked docker subprocess: {cmd!r}")

    def _guarded_run(cmd: object, *args: object, **kwargs: object) -> object:
        _check_cmd(cmd)
        return original_run(cmd, *args, **kwargs)  # type: ignore[arg-type]

    async def _guarded_async_exec(*cmd: object, **kwargs: object) -> object:
        if cmd and str(cmd[0]).endswith("docker"):
            raise AssertionError(f"demo invoked docker subprocess (async): {cmd!r}")
        return await original_create_subprocess_exec(*cmd, **kwargs)  # type: ignore[arg-type]

    async def _guarded_async_shell(cmd: str, **kwargs: object) -> object:
        _check_cmd(cmd)
        return await original_create_subprocess_shell(cmd, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", _guarded_run)
    import asyncio

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _guarded_async_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _guarded_async_shell)

    from poc.demo import run_demo

    await run_demo(auto_confirm=True)
