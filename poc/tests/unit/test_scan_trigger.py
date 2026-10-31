"""BC-037 — on-demand scan trigger mutex + guard semantics.

Covers:
    - CAP-T081: On-demand scan trigger — concurrent-run guard semantics;
      loop + manual share the same lock; manual trigger doesn't shift the
      scheduled loop timer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from poc.scout import scout


@pytest.fixture(autouse=True)
def _reset_flag() -> Iterator[None]:
    """Each test starts with the flag clean."""
    scout._scan_in_progress = False
    yield
    scout._scan_in_progress = False


async def test_run_scan_once_holds_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """While `collect_and_post` runs, `_scan_in_progress` must be True;
    on exit it must flip back to False."""
    witnessed: dict[str, bool] = {}

    def _fake_collect() -> None:
        witnessed["during"] = scout._scan_in_progress

    monkeypatch.setattr(scout, "collect_and_post", _fake_collect)
    await scout._run_scan_once(reason="unit")
    assert witnessed["during"] is True
    assert scout._scan_in_progress is False


async def test_run_scan_once_releases_flag_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if collect_and_post raises, the flag must flip back — otherwise
    one crash would wedge scout forever at 'already running'."""

    def _fake_collect() -> None:
        raise RuntimeError("induced")

    monkeypatch.setattr(scout, "collect_and_post", _fake_collect)
    await scout._run_scan_once(reason="unit")
    assert scout._scan_in_progress is False


async def test_concurrent_scan_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a scan is already in progress, a second _run_scan_once returns
    the sentinel without running collect_and_post again."""
    call_count = {"n": 0}
    gate = asyncio.Event()

    def _slow_collect() -> None:
        import time

        call_count["n"] += 1
        # Block until the test releases us — simulates slow grype work.
        while not gate.is_set():
            time.sleep(0.01)

    monkeypatch.setattr(scout, "collect_and_post", _slow_collect)

    # Start the first scan but don't await it yet.
    first = asyncio.create_task(scout._run_scan_once(reason="unit-1"))
    # Yield so the first task reaches the inner thread-dispatch
    await asyncio.sleep(0.05)
    assert scout._scan_in_progress is True, "first scan must be holding the flag"

    # Second call should see the flag and short-circuit without invoking collect.
    result = await scout._run_scan_once(reason="unit-2")
    assert result == "already_running"
    assert call_count["n"] == 1, "second call must not invoke collect"

    # Let the first scan finish.
    gate.set()
    await first
    assert scout._scan_in_progress is False


async def test_loop_and_manual_share_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """If a loop-driven scan is in progress, a manual trigger short-circuits.
    This is the inverse of concurrent_scan_short_circuits — verifies the two
    code paths share the module-level flag, not a per-caller lock."""
    call_count = {"n": 0}

    def _mark_only() -> None:
        call_count["n"] += 1

    monkeypatch.setattr(scout, "collect_and_post", _mark_only)

    # Simulate the loop acquiring the flag directly, then a manual trigger.
    scout._scan_in_progress = True
    result = await scout._run_scan_once(reason="manual")
    assert result == "already_running"
    assert call_count["n"] == 0, "manual must not run while loop holds the flag"


async def test_manual_trigger_does_not_shift_loop_timer() -> None:
    """BC-037 Design Decision: the scheduled loop keeps its own cadence,
    unaffected by manual triggers. We can't easily unit-test the sleep loop,
    but we CAN verify there is no shared timer state that _run_scan_once
    touches — proof-by-absence of a `_loop_next_tick_ts` module global or
    equivalent."""
    forbidden_names = {
        "_loop_next_tick_ts",
        "_loop_last_tick_ts",
        "_next_scheduled_scan_ts",
    }
    present = forbidden_names & set(vars(scout).keys())
    assert present == set(), (
        "manual trigger must not share scheduler state with the loop timer "
        f"(found: {present}). The loop runs on `asyncio.sleep(SCAN_INTERVAL)` "
        f"inside `_scan_loop_forever`, independent of any manual call."
    )
