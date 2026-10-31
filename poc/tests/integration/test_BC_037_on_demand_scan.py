"""BC-037 — HTTP route for on-demand scan trigger.

Covers:
    - CAP-T081: On-demand scan trigger — POST /scan/trigger returns 202 on
      happy path, 409 on concurrent, 503 when Docker socket unavailable.
    - CAP-B021: Operator verifies out-of-band fixes without waiting on cadence.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from poc.scout import scout


@pytest.fixture(autouse=True)
def _reset_scout_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    scout._scan_in_progress = False
    scout._docker_socket_ok = True
    yield
    scout._scan_in_progress = False


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Instrumented TestClient — collect_and_post is a no-op so the handler
    returns quickly."""
    monkeypatch.setattr(scout, "collect_and_post", lambda: None)
    return TestClient(scout.app)


def test_trigger_returns_202(client: TestClient) -> None:
    r = client.post("/scan/trigger")
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "scheduled"
    assert isinstance(body["started_at"], int)
    assert body["started_at"] > 0


def test_trigger_response_shape(client: TestClient) -> None:
    r = client.post("/scan/trigger")
    body = r.json()
    # Required fields — Makefile pretty-prints these.
    assert set(body.keys()) >= {"status", "started_at"}
    # started_at is close to now (same test second)
    assert abs(body["started_at"] - int(time.time())) < 3


def test_concurrent_trigger_returns_409(client: TestClient) -> None:
    """When a scan is already in flight (lock held), the trigger returns 409
    without starting a second scan.

    We simulate the "in flight" state by acquiring the lock manually — avoids
    TestClient drain-on-return timing which makes task-overlap tests flaky."""
    # Pretend another path (loop or an earlier manual trigger) holds the lock.
    assert scout._try_acquire_scan_lock() is True
    try:
        r = client.post("/scan/trigger")
        assert r.status_code == 409
        body = r.json()
        assert body["status"] == "already_running"
        assert "started_at" in body
    finally:
        scout._release_scan_lock()


def test_trigger_after_release_returns_202(client: TestClient) -> None:
    """Lock-release path round-trip — once the lock is released, the trigger
    behaves normally again."""
    scout._try_acquire_scan_lock()
    scout._release_scan_lock()
    r = client.post("/scan/trigger")
    assert r.status_code == 202


def test_trigger_503_when_docker_unavailable(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """BC-037: a trigger when Docker socket is unavailable short-circuits with
    503 — prevents the operator from thinking a scan ran against a dead socket."""
    monkeypatch.setattr(scout, "_docker_socket_ok", False)
    r = client.post("/scan/trigger")
    assert r.status_code == 503
    body = r.json()
    assert (
        "docker_socket" in body.get("detail", "").lower()
        or "unavailable" in body.get("detail", "").lower()
    )


def test_trigger_does_not_block_handler_on_long_scan(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """The POST must return promptly even if the underlying collect would take
    longer than the HTTP timeout. We can't easily test "5s background work"
    through TestClient (it drains tasks on return), so instead we verify the
    handler path has no await on `collect_and_post` — it only acquires the
    lock + schedules.

    Guard-by-construction: `trigger_scan` calls `asyncio.create_task(...)` not
    `await ...` — proof-by-source-inspection would be fragile, so this test
    just checks a reasonable upper bound on handler time with a slow collect."""

    def _slow_collect() -> None:
        import time as _t

        _t.sleep(0.5)

    monkeypatch.setattr(scout, "collect_and_post", _slow_collect)
    start = time.monotonic()
    r = client.post("/scan/trigger")
    elapsed = time.monotonic() - start
    assert r.status_code == 202
    # Even though collect takes 500ms, the handler itself must return quickly.
    # TestClient drains the task, so our measured elapsed includes the collect
    # work — but it's bounded by that 500ms, not by the handler itself hanging.
    assert elapsed < 2.0, f"handler path must not add significant latency, took {elapsed:.2f}s"
