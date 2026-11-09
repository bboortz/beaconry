"""BC-108 — Docker events watchdog.

Covers:
    - CAP-T210: scout wraps `docker.events()` consume in a reconnection
      loop with capped exponential backoff. `_next_backoff(prev)` returns
      MIN on the first attempt, doubles each subsequent call, clamps at
      MAX. `/healthz` exposes `docker_events_reconnects` counter +
      `docker_events_last_reconnect_ts`.
"""

from __future__ import annotations

import pytest

from poc.scout import scout

# ── Backoff formula ─────────────────────────────────────────────────────────


def test_next_backoff_first_attempt_returns_min(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout, "DOCKER_EVENTS_BACKOFF_MIN_S", 2)
    monkeypatch.setattr(scout, "DOCKER_EVENTS_BACKOFF_MAX_S", 60)
    assert scout._next_backoff(0) == 2


def test_next_backoff_doubles_each_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout, "DOCKER_EVENTS_BACKOFF_MIN_S", 1)
    monkeypatch.setattr(scout, "DOCKER_EVENTS_BACKOFF_MAX_S", 60)
    assert scout._next_backoff(1) == 2
    assert scout._next_backoff(2) == 4
    assert scout._next_backoff(4) == 8
    assert scout._next_backoff(16) == 32


def test_next_backoff_clamped_at_max(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout, "DOCKER_EVENTS_BACKOFF_MIN_S", 1)
    monkeypatch.setattr(scout, "DOCKER_EVENTS_BACKOFF_MAX_S", 60)
    assert scout._next_backoff(32) == 60
    assert scout._next_backoff(60) == 60
    assert scout._next_backoff(10**6) == 60


def test_next_backoff_negative_treated_as_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout, "DOCKER_EVENTS_BACKOFF_MIN_S", 3)
    monkeypatch.setattr(scout, "DOCKER_EVENTS_BACKOFF_MAX_S", 60)
    assert scout._next_backoff(-5) == 3


# ── Env parsing ─────────────────────────────────────────────────────────────


def test_env_defaults_sensible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_DOCKER_EVENTS_BACKOFF_MIN_S", raising=False)
    monkeypatch.delenv("BEACONRY_DOCKER_EVENTS_BACKOFF_MAX_S", raising=False)
    import importlib

    importlib.reload(scout)
    assert scout.DOCKER_EVENTS_BACKOFF_MIN_S == 1
    assert scout.DOCKER_EVENTS_BACKOFF_MAX_S == 60


def test_env_min_must_be_at_least_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_DOCKER_EVENTS_BACKOFF_MIN_S", "0")
    import importlib

    importlib.reload(scout)
    assert scout.DOCKER_EVENTS_BACKOFF_MIN_S == 1


def test_env_max_clamped_to_min(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAX < MIN silently clamps MAX up to MIN — avoids degenerate config."""
    monkeypatch.setenv("BEACONRY_DOCKER_EVENTS_BACKOFF_MIN_S", "10")
    monkeypatch.setenv("BEACONRY_DOCKER_EVENTS_BACKOFF_MAX_S", "3")
    import importlib

    importlib.reload(scout)
    assert scout.DOCKER_EVENTS_BACKOFF_MIN_S == 10
    assert scout.DOCKER_EVENTS_BACKOFF_MAX_S == 10


# ── /healthz surfacing ──────────────────────────────────────────────────────


def test_healthz_exposes_reconnect_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout, "_docker_events_reconnects", 0)
    monkeypatch.setattr(scout, "_docker_events_last_reconnect_ts", 0)
    summary = scout._scout_healthz_summary()
    assert summary["checks"]["docker_events_reconnects"] == 0
    assert summary["checks"]["docker_events_last_reconnect_ts"] == 0


def test_healthz_reflects_bumped_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout, "_docker_events_reconnects", 7)
    monkeypatch.setattr(scout, "_docker_events_last_reconnect_ts", 1_700_000_000)
    summary = scout._scout_healthz_summary()
    assert summary["checks"]["docker_events_reconnects"] == 7
    assert summary["checks"]["docker_events_last_reconnect_ts"] == 1_700_000_000
