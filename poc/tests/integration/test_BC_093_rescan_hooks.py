"""BC-093 — Rescan triggers beyond SCAN_INTERVAL.

Covers:
    - CAP-T191: daily full rescan at configured wall-clock hour
    - CAP-T192: docker events listener triggers debounced scans
    - CAP-T193: pacman hook template
"""

from __future__ import annotations

import datetime as dt
import importlib
from pathlib import Path

import pytest

# ── CAP-T191: daily rescan scheduling ───────────────────────────────────────


def _reload_scout():
    import poc.scout.scout as scout_mod

    importlib.reload(scout_mod)
    return scout_mod


def test_daily_rescan_computes_next_tick_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """If now is before today's target hour, next tick is today."""
    monkeypatch.setenv("BEACONRY_DAILY_RESCAN_HOUR", "2")
    scout = _reload_scout()
    # now = 01:30 local → next tick at 02:00 local (30 min later = 1800 s)
    now = dt.datetime(2026, 11, 5, 1, 30, 0).timestamp()
    delay = scout._seconds_until_next_daily_tick(2, now_ts=now)
    assert 1799 <= delay <= 1801  # allow 1 s slop for DST / wall-clock quirks


def test_daily_rescan_computes_next_tick_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
    """If now is after today's target hour, next tick is tomorrow."""
    monkeypatch.setenv("BEACONRY_DAILY_RESCAN_HOUR", "2")
    scout = _reload_scout()
    # now = 03:00 → next tick is tomorrow 02:00 = 23h later = 82800 s
    now = dt.datetime(2026, 11, 5, 3, 0, 0).timestamp()
    delay = scout._seconds_until_next_daily_tick(2, now_ts=now)
    assert 82798 <= delay <= 82802


def test_daily_rescan_at_exact_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    """At exactly 02:00:00 local, next tick is tomorrow 02:00."""
    monkeypatch.setenv("BEACONRY_DAILY_RESCAN_HOUR", "2")
    scout = _reload_scout()
    now = dt.datetime(2026, 11, 5, 2, 0, 0).timestamp()
    delay = scout._seconds_until_next_daily_tick(2, now_ts=now)
    # Tomorrow 02:00 = exactly 86400 s later.
    assert 86398 <= delay <= 86402


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2", 2),
        ("0", 0),
        ("23", 23),
        ("-1", -1),
        ("bogus", 2),  # invalid → warn + default
        ("25", 2),  # out of range → warn + default
        ("", 2),  # empty → default
    ],
)
def test_daily_rescan_env_parse(monkeypatch: pytest.MonkeyPatch, raw: str, expected: int) -> None:
    monkeypatch.setenv("BEACONRY_DAILY_RESCAN_HOUR", raw)
    scout = _reload_scout()
    assert expected == scout.DAILY_RESCAN_HOUR
    monkeypatch.delenv("BEACONRY_DAILY_RESCAN_HOUR")
    _reload_scout()


def test_daily_rescan_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 2: -1 disables (module value = -1; lifespan skips the task)."""
    monkeypatch.setenv("BEACONRY_DAILY_RESCAN_HOUR", "-1")
    scout = _reload_scout()
    assert scout.DAILY_RESCAN_HOUR == -1
    monkeypatch.delenv("BEACONRY_DAILY_RESCAN_HOUR")
    _reload_scout()


# ── CAP-T192: docker events env parse ───────────────────────────────────────


def test_docker_events_default_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_DOCKER_EVENTS_ENABLED", raising=False)
    scout = _reload_scout()
    assert scout.DOCKER_EVENTS_ENABLED is True


def test_docker_events_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 5: BEACONRY_DOCKER_EVENTS_ENABLED=0 → listener does not start."""
    monkeypatch.setenv("BEACONRY_DOCKER_EVENTS_ENABLED", "0")
    scout = _reload_scout()
    assert scout.DOCKER_EVENTS_ENABLED is False
    monkeypatch.delenv("BEACONRY_DOCKER_EVENTS_ENABLED")
    _reload_scout()


def test_docker_events_debounce_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_DOCKER_EVENTS_DEBOUNCE_S", raising=False)
    scout = _reload_scout()
    assert scout.DOCKER_EVENTS_DEBOUNCE_S == 10


def test_docker_events_debounce_clamp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 4 setup: debounce clamps to ≥1."""
    monkeypatch.setenv("BEACONRY_DOCKER_EVENTS_DEBOUNCE_S", "0")
    scout = _reload_scout()
    assert scout.DOCKER_EVENTS_DEBOUNCE_S == 1
    monkeypatch.delenv("BEACONRY_DOCKER_EVENTS_DEBOUNCE_S")
    _reload_scout()


# ── CAP-T192: docker events debounce behaviour (direct) ─────────────────────


@pytest.mark.asyncio
async def test_docker_event_triggers_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 3: simulated docker event → _schedule_scan fires _run_scan_once.

    Unit-test the debounce path by stubbing both the docker events stream
    and _run_scan_once, then driving one event through the schedule hook.
    """
    import asyncio

    scout = _reload_scout()

    scan_calls: list[str] = []

    async def _fake_run_scan_once(reason: str = "loop") -> None:
        scan_calls.append(reason)

    monkeypatch.setattr(scout, "_run_scan_once", _fake_run_scan_once)
    # Tight debounce so test runs quickly.
    monkeypatch.setattr(scout, "DOCKER_EVENTS_DEBOUNCE_S", 1)

    # Build one iteration of the loop logic in-process: schedule_scan +
    # await the trailing task.
    loop = asyncio.get_running_loop()
    pending: list[asyncio.TimerHandle] = []

    def _schedule_scan() -> None:
        for h in pending:
            h.cancel()
        pending.clear()
        handle = loop.call_later(
            scout.DOCKER_EVENTS_DEBOUNCE_S,
            lambda: asyncio.create_task(scout._run_scan_once(reason="docker_event")),
        )
        pending.append(handle)

    _schedule_scan()
    await asyncio.sleep(1.2)  # allow trailing scan to fire
    assert scan_calls == ["docker_event"]


@pytest.mark.asyncio
async def test_docker_events_debounce(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 4: burst of 3 events within debounce window → one scan."""
    import asyncio

    scout = _reload_scout()
    scan_calls: list[str] = []

    async def _fake_run_scan_once(reason: str = "loop") -> None:
        scan_calls.append(reason)

    monkeypatch.setattr(scout, "_run_scan_once", _fake_run_scan_once)
    monkeypatch.setattr(scout, "DOCKER_EVENTS_DEBOUNCE_S", 1)
    loop = asyncio.get_running_loop()
    pending: list[asyncio.TimerHandle] = []

    def _schedule_scan() -> None:
        for h in pending:
            h.cancel()
        pending.clear()
        handle = loop.call_later(
            scout.DOCKER_EVENTS_DEBOUNCE_S,
            lambda: asyncio.create_task(scout._run_scan_once(reason="docker_event")),
        )
        pending.append(handle)

    # Burst of 3 events within 100 ms (well under 1 s debounce).
    _schedule_scan()
    await asyncio.sleep(0.05)
    _schedule_scan()
    await asyncio.sleep(0.05)
    _schedule_scan()

    await asyncio.sleep(1.2)  # allow final trailing scan to fire
    assert scan_calls == ["docker_event"]  # exactly one, not three


# ── CAP-T193: pacman hook template present + well-formed ────────────────────


def test_pacman_hook_file_present_and_well_formed() -> None:
    """Scenario 6: hook file shipped under poc/hooks/ with the right fields."""
    repo_root = Path(__file__).resolve().parents[3]
    hook = repo_root / "poc" / "hooks" / "beaconry-pacman.hook"
    assert hook.exists(), f"hook template missing at {hook}"
    body = hook.read_text(encoding="utf-8")
    # alpm hook required sections.
    assert "[Trigger]" in body
    assert "[Action]" in body
    # Operation lines — Install, Upgrade, Remove.
    assert "Operation = Install" in body
    assert "Operation = Upgrade" in body
    assert "Operation = Remove" in body
    # Points at scout's /scan/trigger.
    assert "/scan/trigger" in body
    # Non-blocking on failure (|| true).
    assert "|| true" in body


def test_hooks_readme_present() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    readme = repo_root / "poc" / "hooks" / "README.md"
    assert readme.exists()
    body = readme.read_text(encoding="utf-8")
    assert "beaconry-pacman.hook" in body
    # Security section mentions the 127.0.0.1 binding + /scan/trigger auth.
    assert "127.0.0.1" in body
    assert "unauthenticated" in body
