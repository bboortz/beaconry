"""BC-112 — Scan trigger proxy retry.

Covers:
    - CAP-T214: hub's `/scan/trigger` proxy to scout retries on
      transport failures (DNS blip, connection refused, timeout)
      up to `BEACONRY_SCAN_TRIGGER_MAX_ATTEMPTS` with exponential
      backoff. HTTP 4xx/5xx responses from scout are NOT retried.
"""

from __future__ import annotations

import pytest

from poc.hub import hub

# ── Env parsing ─────────────────────────────────────────────────────────────


def test_env_defaults_sensible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_SCAN_TRIGGER_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("BEACONRY_SCAN_TRIGGER_BASE_S", raising=False)
    import importlib

    importlib.reload(hub)
    assert hub.SCAN_TRIGGER_MAX_ATTEMPTS == 3
    assert hub.SCAN_TRIGGER_BASE_S == 1


def test_env_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_SCAN_TRIGGER_MAX_ATTEMPTS", "not-a-number")
    import importlib

    importlib.reload(hub)
    assert hub.SCAN_TRIGGER_MAX_ATTEMPTS == 3


# ── Retry behaviour ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_http_scan_trigger_succeeds_on_first_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def _fake_once(_url):
        calls["n"] += 1
        return 202, '{"status": "scheduled"}', None

    monkeypatch.setattr(hub, "_scan_trigger_once", _fake_once)

    result = await hub.http_scan_trigger()
    assert result.get("status") == "scheduled"
    assert result.get("code") == 202
    assert "retry_attempts" not in result
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_http_scan_trigger_retries_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hub, "SCAN_TRIGGER_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(hub, "SCAN_TRIGGER_BASE_S", 1)

    results = iter(
        [
            (None, None, "ConnectionRefusedError"),
            (202, '{"status": "scheduled"}', None),
        ]
    )

    def _fake_once(_url):
        return next(results)

    sleeps: list[float] = []

    async def _fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(hub, "_scan_trigger_once", _fake_once)
    monkeypatch.setattr(hub.asyncio, "sleep", _fake_sleep)

    result = await hub.http_scan_trigger()
    assert result.get("status") == "scheduled"
    assert result.get("retry_attempts") == 2
    assert sleeps == [1]  # BASE_S × 2^0 = 1s before attempt 2.


@pytest.mark.asyncio
async def test_http_scan_trigger_raises_503_after_max_transport_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hub, "SCAN_TRIGGER_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(hub, "SCAN_TRIGGER_BASE_S", 1)

    def _fake_once(_url):
        return None, None, "TimeoutError"

    async def _fake_sleep(_s):
        pass

    monkeypatch.setattr(hub, "_scan_trigger_once", _fake_once)
    monkeypatch.setattr(hub.asyncio, "sleep", _fake_sleep)

    with pytest.raises(hub.HTTPException) as exc_info:
        await hub.http_scan_trigger()
    assert exc_info.value.status_code == 503
    assert "TimeoutError" in exc_info.value.detail
    assert "3 attempt(s)" in exc_info.value.detail


@pytest.mark.asyncio
async def test_http_scan_trigger_does_not_retry_on_4xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 401 or 409 from scout is a real response — don't retry (would waste
    time and could hide a config issue)."""
    calls = {"n": 0}

    def _fake_once(_url):
        calls["n"] += 1
        return 409, '{"status": "already_running"}', None

    monkeypatch.setattr(hub, "_scan_trigger_once", _fake_once)

    result = await hub.http_scan_trigger()
    assert result.get("status") == "error"
    assert result.get("code") == 409
    assert calls["n"] == 1  # No retry.


@pytest.mark.asyncio
async def test_backoff_doubles_each_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hub, "SCAN_TRIGGER_MAX_ATTEMPTS", 4)
    monkeypatch.setattr(hub, "SCAN_TRIGGER_BASE_S", 2)

    def _fake_once(_url):
        return None, None, "fail"

    sleeps: list[float] = []

    async def _fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(hub, "_scan_trigger_once", _fake_once)
    monkeypatch.setattr(hub.asyncio, "sleep", _fake_sleep)

    with pytest.raises(hub.HTTPException):
        await hub.http_scan_trigger()
    # Between attempts 1→2, 2→3, 3→4: BASE × 2^0, 2^1, 2^2 = 2, 4, 8.
    assert sleeps == [2, 4, 8]
