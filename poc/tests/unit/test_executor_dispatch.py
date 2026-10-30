"""Unit tests for executor dispatch logic.

Covers:
    - CAP-T006: Executor dispatcher picks the first matching adapter
    - CAP-T007: DockerAdapter in MOCK mode spawns no subprocess
    - CAP-T030: DockerAdapter.execute pulls :latest with a :rollback safety tag before pulling
"""

from __future__ import annotations

import pytest

from poc.hub.executor.base import Finding
from poc.hub.executor.docker_adapter import DockerAdapter
from poc.hub.executor.manual_adapter import ManualAdapter


def _finding(source_type: str, source_ref: str = "nginx:1.24") -> Finding:
    return Finding(
        id="x",
        host="h",
        cve_id="CVE-x",
        package="p",
        version="1",
        fixed_version="2",
        severity="High",
        cvss=8.0,
        source_type=source_type,
        source_ref=source_ref,
        description="",
    )


def test_docker_adapter_claims_docker_findings() -> None:
    assert DockerAdapter().can_handle(_finding("docker")) is True
    assert DockerAdapter().can_handle(_finding("alpm")) is False
    assert DockerAdapter().can_handle(_finding("python")) is False


def test_manual_adapter_is_a_fallback_for_everything() -> None:
    adapter = ManualAdapter()
    for stype in ("docker", "alpm", "python", "unknown"):
        assert adapter.can_handle(_finding(stype)) is True


def test_dispatcher_picks_first_matching_adapter() -> None:
    executors = [DockerAdapter(), ManualAdapter()]
    docker_finding = _finding("docker")
    pkg_finding = _finding("alpm")
    assert next(e for e in executors if e.can_handle(docker_finding)).__class__ is DockerAdapter
    assert next(e for e in executors if e.can_handle(pkg_finding)).__class__ is ManualAdapter


def test_docker_adapter_describe_shows_pull_command() -> None:
    cmd = DockerAdapter().describe(_finding("docker", "nginx:1.24-fixture"))
    assert cmd == "docker pull nginx:latest"


def test_manual_adapter_describe_tailors_to_source_type() -> None:
    adapter = ManualAdapter()
    assert "pacman" in adapter.describe(_finding("alpm")).lower()
    assert "pip install" in adapter.describe(_finding("python"))


@pytest.mark.asyncio
async def test_docker_adapter_execute_mock_no_subprocess() -> None:
    """In MOCK mode, DockerAdapter.execute must not spawn a subprocess."""
    import subprocess as subprocess_mod

    original = subprocess_mod.run

    def _bomb(*a: object, **k: object) -> object:
        raise AssertionError("DockerAdapter spawned a subprocess in MOCK mode")

    subprocess_mod.run = _bomb  # type: ignore[assignment]
    try:
        result = await DockerAdapter().execute(_finding("docker"))
    finally:
        subprocess_mod.run = original  # type: ignore[assignment]

    assert result.success is True
    assert "nginx:latest" in result.command


@pytest.mark.asyncio
async def test_docker_adapter_sets_rollback_tag_before_pull(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-MOCK path: rollback tag command runs before docker pull."""
    from poc.hub.executor import docker_adapter as da_mod

    monkeypatch.setattr(da_mod, "MOCK", False, raising=True)

    cmds: list[str] = []

    class _FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"", b"")

    async def _fake_shell(cmd: str, **_kw: object) -> _FakeProc:
        cmds.append(cmd)
        return _FakeProc()

    monkeypatch.setattr(da_mod.asyncio, "create_subprocess_shell", _fake_shell)

    result = await DockerAdapter().execute(_finding("docker", "nginx:1.24"))

    assert result.success is True
    assert len(cmds) == 2, f"expected tag + pull, got {cmds}"
    assert cmds[0].startswith("docker tag"), f"first command should tag :rollback, got {cmds[0]}"
    assert ":rollback" in cmds[0]
    assert cmds[1] == "docker pull nginx:latest"
