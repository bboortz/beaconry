"""Unit tests for executor dispatch logic.

Covers:
    - CAP-T006: Executor dispatcher picks the first matching adapter
    - CAP-T007: DockerAdapter in MOCK mode spawns no subprocess
    - CAP-T030: DockerAdapter.execute pulls same-track tag with a :rollback safety tag before pulling
    - CAP-T046: DockerAdapter preserves the source_ref tag on pull (no silent :latest jump)
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


def test_docker_adapter_describe_preserves_tag() -> None:
    """BC-014: pull command uses the source_ref tag verbatim — no silent :latest jump."""
    cmd = DockerAdapter().describe(_finding("docker", "postgres:16-alpine"))
    assert cmd == "docker pull postgres:16-alpine"


def test_docker_adapter_describe_preserves_pinned_tag() -> None:
    """BC-014: pinned tags stay pinned (operator decides when to bump majors)."""
    cmd = DockerAdapter().describe(_finding("docker", "postgres:16.1.3-alpine"))
    assert cmd == "docker pull postgres:16.1.3-alpine"


def test_docker_adapter_describe_fluid_tag() -> None:
    """Fluid tags re-pulled still pick up upstream updates via Docker Hub tag-mutability."""
    cmd = DockerAdapter().describe(_finding("docker", "nginx:alpine"))
    assert cmd == "docker pull nginx:alpine"


def test_docker_adapter_describe_fallback_when_no_tag() -> None:
    """If source_ref has no tag at all, fall back to :latest (preserves Docker's default)."""
    cmd = DockerAdapter().describe(_finding("docker", "nginx"))
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
        result = await DockerAdapter().execute(_finding("docker", "nginx:1.24"))
    finally:
        subprocess_mod.run = original  # type: ignore[assignment]

    assert result.success is True
    # BC-014: pull uses source_ref tag, not :latest.
    assert "nginx:1.24" in result.command
    assert ":latest" not in result.command


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

    # BC-067: subprocess uses argv-form (create_subprocess_exec) to kill the
    # shell-injection vector. Record each invocation as a flattened string
    # so the BC-014 assertions below (which check for the full command
    # text) still work.
    async def _fake_exec(*argv: str, **_kw: object) -> _FakeProc:
        cmds.append(" ".join(argv))
        return _FakeProc()

    monkeypatch.setattr(da_mod.asyncio, "create_subprocess_exec", _fake_exec)

    result = await DockerAdapter().execute(_finding("docker", "nginx:1.24"))

    assert result.success is True
    assert len(cmds) == 2, f"expected tag + pull, got {cmds}"
    assert cmds[0].startswith("docker tag"), f"first command should tag :rollback, got {cmds[0]}"
    assert ":rollback" in cmds[0]
    # BC-014: same-track pull — the source_ref tag is preserved verbatim.
    assert cmds[1] == "docker pull nginx:1.24"


@pytest.mark.asyncio
async def test_docker_adapter_execute_pulls_same_tag_for_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BC-014 regression: postgres:16-alpine → pull postgres:16-alpine (NOT postgres:latest).

    The headline motivation — operator running postgres:16-alpine taps Fix, must not
    receive postgres 18 as a silent surprise.
    """
    from poc.hub.executor import docker_adapter as da_mod

    monkeypatch.setattr(da_mod, "MOCK", False, raising=True)

    cmds: list[str] = []

    class _FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"", b"")

    async def _fake_exec(*argv: str, **_kw: object) -> _FakeProc:
        cmds.append(" ".join(argv))
        return _FakeProc()

    monkeypatch.setattr(da_mod.asyncio, "create_subprocess_exec", _fake_exec)

    result = await DockerAdapter().execute(_finding("docker", "postgres:16-alpine"))

    assert result.success is True
    assert cmds[1] == "docker pull postgres:16-alpine", (
        f"BC-014: must pull same-track tag, not :latest. Got: {cmds[1]}"
    )
    assert "postgres:latest" not in " ".join(cmds), (
        f"BC-014: must never pull postgres:latest from a postgres:16-alpine finding. Got: {cmds}"
    )
    # Rollback tag unchanged — still <base>:rollback keyed off the image name.
    assert cmds[0] == "docker tag postgres:16-alpine postgres:rollback", (
        f"Rollback tag command format regressed: {cmds[0]}"
    )
