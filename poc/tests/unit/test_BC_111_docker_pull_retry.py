"""BC-111 — DockerAdapter pull retry with exponential backoff.

Covers:
    - CAP-T213: `_run_docker_pull_once` is a shared single-attempt
      wrapper; `DockerAdapter.execute` retries it up to
      `BEACONRY_DOCKER_PULL_MAX_ATTEMPTS` times with exponential
      backoff (`BASE_S × 2^(attempt-1)`). A one-off registry hiccup
      clears on retry; auth / tag-not-found still fails after N
      attempts but with the same message shape as pre-BC-111.
"""

from __future__ import annotations

import pytest

from poc.hub.executor import docker_adapter


@pytest.fixture
def finding() -> docker_adapter.Finding:
    return docker_adapter.Finding(
        id="xyz",
        host="",
        cve_id="CVE-2026-9999",
        package="",
        version="",
        fixed_version="",
        severity="Critical",
        cvss=9.8,
        source_type="docker",
        source_ref="nginx:alpine",
        description="",
    )


# ── Env parsing ─────────────────────────────────────────────────────────────


def test_env_defaults_sensible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_DOCKER_PULL_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("BEACONRY_DOCKER_PULL_BASE_S", raising=False)
    import importlib

    importlib.reload(docker_adapter)
    assert docker_adapter.DOCKER_PULL_MAX_ATTEMPTS == 3
    assert docker_adapter.DOCKER_PULL_BASE_S == 5


def test_env_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_DOCKER_PULL_MAX_ATTEMPTS", "not-a-number")
    import importlib

    importlib.reload(docker_adapter)
    assert docker_adapter.DOCKER_PULL_MAX_ATTEMPTS == 3


def test_env_rejects_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_DOCKER_PULL_MAX_ATTEMPTS", "0")
    import importlib

    importlib.reload(docker_adapter)
    assert docker_adapter.DOCKER_PULL_MAX_ATTEMPTS == 3


# ── Retry behaviour ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_succeeds_on_first_attempt(
    monkeypatch: pytest.MonkeyPatch, finding: docker_adapter.Finding
) -> None:
    monkeypatch.setattr(docker_adapter, "MOCK", False)
    attempts = {"n": 0}

    async def _fake_pull(_argv):
        attempts["n"] += 1
        return True, ""

    monkeypatch.setattr(docker_adapter, "_run_docker_pull_once", _fake_pull)

    # Stub the rollback-tag subprocess to a no-op.
    async def _fake_exec(*args, **kwargs):
        class _Proc:
            returncode = 0

            async def communicate(self):
                return b"", b""

        return _Proc()

    monkeypatch.setattr(docker_adapter.asyncio, "create_subprocess_exec", _fake_exec)

    adapter = docker_adapter.DockerAdapter()
    result = await adapter.execute(finding)
    assert result.success is True
    assert "Pulled nginx:alpine" in result.message
    assert "(succeeded on attempt" not in result.message
    assert attempts["n"] == 1


@pytest.mark.asyncio
async def test_execute_succeeds_on_second_attempt(
    monkeypatch: pytest.MonkeyPatch, finding: docker_adapter.Finding
) -> None:
    monkeypatch.setattr(docker_adapter, "MOCK", False)
    monkeypatch.setattr(docker_adapter, "DOCKER_PULL_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(docker_adapter, "DOCKER_PULL_BASE_S", 1)

    results = iter(
        [
            (False, "registry 503"),
            (True, ""),
        ]
    )

    async def _fake_pull(_argv):
        return next(results)

    sleeps: list[float] = []

    async def _fake_sleep(s):
        sleeps.append(s)

    async def _fake_exec(*args, **kwargs):
        class _Proc:
            returncode = 0

            async def communicate(self):
                return b"", b""

        return _Proc()

    monkeypatch.setattr(docker_adapter, "_run_docker_pull_once", _fake_pull)
    monkeypatch.setattr(docker_adapter.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(docker_adapter.asyncio, "create_subprocess_exec", _fake_exec)

    adapter = docker_adapter.DockerAdapter()
    result = await adapter.execute(finding)
    assert result.success is True
    assert "succeeded on attempt 2/3" in result.message
    assert sleeps == [1]  # Slept BASE_S × 2^0 = 1s before attempt 2.


@pytest.mark.asyncio
async def test_execute_fails_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch, finding: docker_adapter.Finding
) -> None:
    monkeypatch.setattr(docker_adapter, "MOCK", False)
    monkeypatch.setattr(docker_adapter, "DOCKER_PULL_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(docker_adapter, "DOCKER_PULL_BASE_S", 1)

    async def _fake_pull(_argv):
        return False, "auth required"

    async def _fake_sleep(_s):
        pass

    async def _fake_exec(*args, **kwargs):
        class _Proc:
            returncode = 0

            async def communicate(self):
                return b"", b""

        return _Proc()

    monkeypatch.setattr(docker_adapter, "_run_docker_pull_once", _fake_pull)
    monkeypatch.setattr(docker_adapter.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(docker_adapter.asyncio, "create_subprocess_exec", _fake_exec)

    adapter = docker_adapter.DockerAdapter()
    result = await adapter.execute(finding)
    assert result.success is False
    # Error passed through unchanged (same shape as pre-BC-111).
    assert "auth required" in result.message


@pytest.mark.asyncio
async def test_backoff_doubles_each_attempt(
    monkeypatch: pytest.MonkeyPatch, finding: docker_adapter.Finding
) -> None:
    monkeypatch.setattr(docker_adapter, "MOCK", False)
    monkeypatch.setattr(docker_adapter, "DOCKER_PULL_MAX_ATTEMPTS", 4)
    monkeypatch.setattr(docker_adapter, "DOCKER_PULL_BASE_S", 2)

    async def _fake_pull(_argv):
        return False, "fail"

    sleeps: list[float] = []

    async def _fake_sleep(s):
        sleeps.append(s)

    async def _fake_exec(*args, **kwargs):
        class _Proc:
            returncode = 0

            async def communicate(self):
                return b"", b""

        return _Proc()

    monkeypatch.setattr(docker_adapter, "_run_docker_pull_once", _fake_pull)
    monkeypatch.setattr(docker_adapter.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(docker_adapter.asyncio, "create_subprocess_exec", _fake_exec)

    adapter = docker_adapter.DockerAdapter()
    await adapter.execute(finding)
    # Sleep gaps: attempts 1→2, 2→3, 3→4 at BASE × 2^0, 2^1, 2^2 = 2, 4, 8.
    assert sleeps == [2, 4, 8]


@pytest.mark.asyncio
async def test_mock_mode_bypasses_retry(
    monkeypatch: pytest.MonkeyPatch, finding: docker_adapter.Finding
) -> None:
    """MOCK=1 short-circuits in execute() before the retry loop."""
    monkeypatch.setattr(docker_adapter, "MOCK", True)
    pull_calls = {"n": 0}

    async def _fake_pull(_argv):
        pull_calls["n"] += 1
        return False, "should not be called"

    monkeypatch.setattr(docker_adapter, "_run_docker_pull_once", _fake_pull)

    adapter = docker_adapter.DockerAdapter()
    result = await adapter.execute(finding)
    assert result.success is True
    assert "[mock]" in result.message
    assert pull_calls["n"] == 0


# ── Spawn-failure path ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pull_once_handles_spawn_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_exec(*args, **kwargs):
        raise FileNotFoundError("docker binary missing")

    monkeypatch.setattr(docker_adapter.asyncio, "create_subprocess_exec", _fake_exec)

    ok, err = await docker_adapter._run_docker_pull_once(["docker", "pull", "nginx:latest"])
    assert ok is False
    assert "spawn failed" in err
