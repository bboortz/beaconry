"""BC-109 — Grype DB update retry + /readyz gate.

Covers:
    - CAP-T211: `_grype_db_update_once` is a single-attempt helper
      shared by the startup path and the background retry loop.
      `_next_grype_backoff(prev)` returns BASE_S on first attempt,
      doubles each subsequent call, clamps at MAX_S. `/healthz`
      exposes `grype_db_update_attempts` + `_last_update_ts` +
      `_last_error` so operators see the retry drift.
"""

from __future__ import annotations

import pytest

from poc.hub import hub

# ── Backoff formula ─────────────────────────────────────────────────────────


def test_next_grype_backoff_first_attempt_returns_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hub, "GRYPE_DB_RETRY_BASE_S", 10)
    monkeypatch.setattr(hub, "GRYPE_DB_BACKOFF_MAX_S", 600)
    assert hub._next_grype_backoff(0) == 10


def test_next_grype_backoff_doubles_each_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hub, "GRYPE_DB_RETRY_BASE_S", 5)
    monkeypatch.setattr(hub, "GRYPE_DB_BACKOFF_MAX_S", 1000)
    assert hub._next_grype_backoff(5) == 10
    assert hub._next_grype_backoff(10) == 20
    assert hub._next_grype_backoff(80) == 160


def test_next_grype_backoff_clamped_at_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub, "GRYPE_DB_RETRY_BASE_S", 10)
    monkeypatch.setattr(hub, "GRYPE_DB_BACKOFF_MAX_S", 100)
    assert hub._next_grype_backoff(80) == 100
    assert hub._next_grype_backoff(100) == 100
    assert hub._next_grype_backoff(10**6) == 100


def test_next_grype_backoff_negative_treated_as_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hub, "GRYPE_DB_RETRY_BASE_S", 7)
    assert hub._next_grype_backoff(-100) == 7


# ── Env parsing ─────────────────────────────────────────────────────────────


def test_env_defaults_sensible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_GRYPE_DB_RETRY_BASE_S", raising=False)
    monkeypatch.delenv("BEACONRY_GRYPE_DB_BACKOFF_MAX_S", raising=False)
    import importlib

    importlib.reload(hub)
    assert hub.GRYPE_DB_RETRY_BASE_S == 10
    assert hub.GRYPE_DB_BACKOFF_MAX_S == 600


def test_env_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_GRYPE_DB_RETRY_BASE_S", "not-a-number")
    import importlib

    importlib.reload(hub)
    assert hub.GRYPE_DB_RETRY_BASE_S == 10


def test_env_max_clamped_to_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAX < BASE silently clamps up — avoids degenerate config."""
    monkeypatch.setenv("BEACONRY_GRYPE_DB_RETRY_BASE_S", "100")
    monkeypatch.setenv("BEACONRY_GRYPE_DB_BACKOFF_MAX_S", "5")
    import importlib

    importlib.reload(hub)
    assert hub.GRYPE_DB_RETRY_BASE_S == 100
    assert hub.GRYPE_DB_BACKOFF_MAX_S == 100


# ── _grype_db_update_once ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grype_db_update_once_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"", b"")

    async def _fake_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(hub.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(hub, "_grype_db_update_attempts", 0)
    monkeypatch.setattr(hub, "_grype_db_last_error", "prev-err")

    ok, err = await hub._grype_db_update_once()
    assert ok is True
    assert err == ""
    assert hub._grype_db_update_attempts == 1
    assert hub._grype_db_last_error == ""
    assert hub._grype_db_last_update_ts > 0


@pytest.mark.asyncio
async def test_grype_db_update_once_nonzero_exit_returns_err(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeProc:
        returncode = 2

        async def communicate(self):
            return (b"", b"Network unreachable while downloading DB")

    async def _fake_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr(hub.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(hub, "_grype_db_update_attempts", 0)

    ok, err = await hub._grype_db_update_once()
    assert ok is False
    assert "Network unreachable" in err
    assert hub._grype_db_update_attempts == 1
    assert "Network unreachable" in hub._grype_db_last_error


@pytest.mark.asyncio
async def test_grype_db_update_once_spawn_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_exec(*args, **kwargs):
        raise FileNotFoundError("grype binary missing")

    monkeypatch.setattr(hub.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(hub, "_grype_db_update_attempts", 0)

    ok, err = await hub._grype_db_update_once()
    assert ok is False
    assert "spawn failed" in err
    assert "grype binary missing" in err


# ── /healthz surfacing ──────────────────────────────────────────────────────


def test_healthz_exposes_grype_db_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub, "_grype_db_update_attempts", 3)
    monkeypatch.setattr(hub, "_grype_db_last_update_ts", 1_700_000_000)
    monkeypatch.setattr(hub, "_grype_db_last_error", "")

    summary = hub._hub_healthz_summary()
    assert summary["checks"]["grype_db_update_attempts"] == 3
    assert summary["checks"]["grype_db_last_update_ts"] == 1_700_000_000
    assert summary["checks"]["grype_db_last_error"] == ""


def test_healthz_surfaces_error_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub, "_grype_db_update_attempts", 5)
    monkeypatch.setattr(hub, "_grype_db_last_update_ts", 0)
    monkeypatch.setattr(hub, "_grype_db_last_error", "timeout after 30s")

    summary = hub._hub_healthz_summary()
    assert summary["checks"]["grype_db_update_attempts"] == 5
    assert summary["checks"]["grype_db_last_update_ts"] == 0
    assert "timeout" in summary["checks"]["grype_db_last_error"]
