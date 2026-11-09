"""BC-110 — Verify probe retry with exponential backoff.

Covers:
    - CAP-T212: `run_probe_with_retry(probe, max_attempts, base_s)` retries
      `run_probe` up to N times, sleeping `base_s × 2^(attempt-1)` between
      failures, returning the FIRST successful result or the LAST failed
      result. Fixes the common race where a probe fires too soon after
      `docker restart` and the service hasn't bound its port yet.
"""

from __future__ import annotations

import pytest

from poc.hub import waterfall


def _probe(kind: str = "tcp", arg: str = "127.0.0.1:65535") -> waterfall.Probe:
    return waterfall.Probe(image_ref="redis:6", probe_type=kind, probe_arg=arg)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_run_probe_with_retry_succeeds_on_first_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"n": 0}

    async def _fake_run_probe(_p):
        attempts["n"] += 1
        return waterfall.ProbeResult(ok=True, reason="first-try ok")

    monkeypatch.setattr(waterfall, "run_probe", _fake_run_probe)

    result = await waterfall.run_probe_with_retry(_probe(), max_attempts=3, base_s=1)
    assert result.ok is True
    assert result.reason == "first-try ok"
    assert attempts["n"] == 1  # No retry needed.


@pytest.mark.asyncio
async def test_run_probe_with_retry_succeeds_on_second_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = iter(
        [
            waterfall.ProbeResult(ok=False, reason="tcp connect failed"),
            waterfall.ProbeResult(ok=True, reason="tcp connect ok"),
        ]
    )

    async def _fake_run_probe(_p):
        return next(results)

    sleeps: list[float] = []

    async def _fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(waterfall, "run_probe", _fake_run_probe)
    monkeypatch.setattr(waterfall.asyncio, "sleep", _fake_sleep)

    result = await waterfall.run_probe_with_retry(_probe(), max_attempts=3, base_s=2)
    assert result.ok is True
    # Second-attempt success → reason decorated with attempt count.
    assert "attempt 2/3" in result.reason
    assert sleeps == [2]  # Slept base_s × 2^0 = 2s before attempt 2.


@pytest.mark.asyncio
async def test_run_probe_with_retry_returns_last_failure_after_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = iter(
        [
            waterfall.ProbeResult(ok=False, reason="fail-1"),
            waterfall.ProbeResult(ok=False, reason="fail-2"),
            waterfall.ProbeResult(ok=False, reason="fail-3-FINAL"),
        ]
    )

    async def _fake_run_probe(_p):
        return next(results)

    async def _fake_sleep(_s):
        pass

    monkeypatch.setattr(waterfall, "run_probe", _fake_run_probe)
    monkeypatch.setattr(waterfall.asyncio, "sleep", _fake_sleep)

    result = await waterfall.run_probe_with_retry(_probe(), max_attempts=3, base_s=1)
    assert result.ok is False
    # Returns the LAST failure (not decorated).
    assert result.reason == "fail-3-FINAL"


@pytest.mark.asyncio
async def test_backoff_doubles_each_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_run_probe(_p):
        return waterfall.ProbeResult(ok=False, reason="fail")

    sleeps: list[float] = []

    async def _fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(waterfall, "run_probe", _fake_run_probe)
    monkeypatch.setattr(waterfall.asyncio, "sleep", _fake_sleep)

    await waterfall.run_probe_with_retry(_probe(), max_attempts=4, base_s=1)
    # base × 2^0, 2^1, 2^2 between attempts 1→2, 2→3, 3→4 (no sleep after 4).
    assert sleeps == [1, 2, 4]


@pytest.mark.asyncio
async def test_max_attempts_1_is_single_shot(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    async def _fake_run_probe(_p):
        attempts["n"] += 1
        return waterfall.ProbeResult(ok=False, reason="fail")

    async def _fake_sleep(_s):
        pytest.fail("Should not sleep when max_attempts=1")

    monkeypatch.setattr(waterfall, "run_probe", _fake_run_probe)
    monkeypatch.setattr(waterfall.asyncio, "sleep", _fake_sleep)

    result = await waterfall.run_probe_with_retry(_probe(), max_attempts=1, base_s=5)
    assert attempts["n"] == 1
    assert result.ok is False


@pytest.mark.asyncio
async def test_max_attempts_zero_or_negative_clamped_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"n": 0}

    async def _fake_run_probe(_p):
        attempts["n"] += 1
        return waterfall.ProbeResult(ok=True, reason="ok")

    monkeypatch.setattr(waterfall, "run_probe", _fake_run_probe)

    r = await waterfall.run_probe_with_retry(_probe(), max_attempts=0, base_s=1)
    assert attempts["n"] == 1
    assert r.ok is True

    r = await waterfall.run_probe_with_retry(_probe(), max_attempts=-5, base_s=1)
    assert attempts["n"] == 2


# ── Env propagation ────────────────────────────────────────────────────────


def test_env_defaults_sensible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_VERIFY_PROBE_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("BEACONRY_VERIFY_PROBE_BASE_S", raising=False)
    import importlib

    from poc.hub import hub

    importlib.reload(hub)
    assert hub.VERIFY_PROBE_MAX_ATTEMPTS == 3
    assert hub.VERIFY_PROBE_BASE_S == 2


def test_env_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_VERIFY_PROBE_MAX_ATTEMPTS", "not-a-number")
    import importlib

    from poc.hub import hub

    importlib.reload(hub)
    assert hub.VERIFY_PROBE_MAX_ATTEMPTS == 3
