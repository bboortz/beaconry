"""BC-104 — Notification retry queue (pending_notifications + retry loop).

Covers:
    - CAP-T206: Telegram adapter persists failed sends to
      `pending_notifications`; the retry sweep replays rows whose
      `next_retry_ts <= now`; success → delete + `notify_delivered`
      audit; failure → bump attempts + exponential backoff; after
      MAX_ATTEMPTS → delete + `notify_abandoned` audit.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from poc.hub import audit as _audit_mod
from poc.hub import hub


@pytest.fixture
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setattr(_audit_mod, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(_audit_mod, "AUDIT_PATH", data_dir / "audit.log", raising=False)
    hub.init_db()
    return data_dir


def _audit_events(db_env: Path) -> list[dict]:
    log_path = db_env / "audit.log"
    if not log_path.exists():
        return []
    import json as _json

    return [_json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def _read_pending(db_path: Path) -> list[tuple]:
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT id, kind, attempts, next_retry_ts FROM pending_notifications"
        ).fetchall()
    finally:
        con.close()


# ── Schema ──────────────────────────────────────────────────────────────────


def test_schema_v9_adds_pending_notifications(db_env: Path) -> None:
    con = sqlite3.connect(db_env / "beaconry.db")
    try:
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        version = con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()
    assert "pending_notifications" in tables
    assert version == 9


# ── Queueing ────────────────────────────────────────────────────────────────


def test_queue_pending_notification_writes_row(db_env: Path) -> None:
    hub._queue_pending_notification(
        kind="finding",
        text="⚠ test",
        reply_markup_json=None,
        error="Telegram API 503",
    )
    rows = _read_pending(db_env / "beaconry.db")
    assert len(rows) == 1
    _, kind, attempts, _ = rows[0]
    assert kind == "finding"
    assert attempts == 0

    events = _audit_events(db_env)
    assert any(e["event"] == "notify_queued" for e in events)


def test_queue_is_idempotent_on_kind_without_finding_id(db_env: Path) -> None:
    """Multiple scout_offline failures (finding_id=NULL) each produce
    their own row — SQLite UNIQUE with NULL allows duplicates."""
    hub._queue_pending_notification("scout_offline", "a", None, "e1")
    hub._queue_pending_notification("scout_offline", "b", None, "e2")
    rows = _read_pending(db_env / "beaconry.db")
    assert len(rows) == 2


# ── Retry sweep ─────────────────────────────────────────────────────────────


class _FakeAdapter:
    """Pretends to be TelegramAdapter.replay. Tracks calls + returns a
    scripted sequence of (success|failure) booleans."""

    def __init__(self, results: list[bool]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, str | None]] = []

    async def replay(self, text: str, reply_markup_json: str | None) -> bool:
        self.calls.append((text, reply_markup_json))
        return self._results.pop(0) if self._results else False


@pytest.mark.asyncio
async def test_sweep_delivers_and_deletes(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A row due now, adapter returns True → row deleted + notify_delivered."""
    adapter = _FakeAdapter([True])
    monkeypatch.setitem(hub.__dict__, "_telegram_adapter", adapter)

    hub._queue_pending_notification("finding", "msg", None, "down")
    # Fast-forward time past the initial base backoff.
    future = int(time.time()) + hub.NOTIFY_RETRY_BASE_S + 1
    counts = await hub.notify_retry_sweep(now_ts=future)

    assert counts == {"delivered": 1, "requeued": 0, "abandoned": 0}
    assert _read_pending(db_env / "beaconry.db") == []
    assert len(adapter.calls) == 1
    events = _audit_events(db_env)
    assert any(e["event"] == "notify_delivered" for e in events)


@pytest.mark.asyncio
async def test_sweep_requeues_on_failure_with_backoff(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = _FakeAdapter([False])
    monkeypatch.setitem(hub.__dict__, "_telegram_adapter", adapter)

    hub._queue_pending_notification("finding", "msg", None, "down")
    future = int(time.time()) + hub.NOTIFY_RETRY_BASE_S + 1
    counts = await hub.notify_retry_sweep(now_ts=future)

    assert counts == {"delivered": 0, "requeued": 1, "abandoned": 0}
    rows = _read_pending(db_env / "beaconry.db")
    assert len(rows) == 1
    _, _, attempts, next_retry_ts = rows[0]
    assert attempts == 1
    # Exponential: base × 2^1 = 2 × base
    assert next_retry_ts >= future + hub.NOTIFY_RETRY_BASE_S * 2


@pytest.mark.asyncio
async def test_sweep_abandons_after_max_attempts(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After NOTIFY_RETRY_MAX_ATTEMPTS failures, row is deleted + audit."""
    monkeypatch.setattr(hub, "NOTIFY_RETRY_MAX_ATTEMPTS", 2)
    adapter = _FakeAdapter([False, False])
    monkeypatch.setitem(hub.__dict__, "_telegram_adapter", adapter)

    hub._queue_pending_notification("finding", "msg", None, "down")
    # First sweep: fails, attempts becomes 1
    now1 = int(time.time()) + hub.NOTIFY_RETRY_BASE_S + 1
    await hub.notify_retry_sweep(now_ts=now1)
    assert len(_read_pending(db_env / "beaconry.db")) == 1

    # Second sweep: fails, attempts hits MAX → abandoned
    rows = _read_pending(db_env / "beaconry.db")
    now2 = rows[0][3] + 1  # past next_retry_ts
    counts = await hub.notify_retry_sweep(now_ts=now2)

    assert counts == {"delivered": 0, "requeued": 0, "abandoned": 1}
    assert _read_pending(db_env / "beaconry.db") == []
    events = _audit_events(db_env)
    assert any(e["event"] == "notify_abandoned" for e in events)


@pytest.mark.asyncio
async def test_sweep_skips_not_due(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows whose next_retry_ts > now stay queued, no adapter call."""
    adapter = _FakeAdapter([])
    monkeypatch.setitem(hub.__dict__, "_telegram_adapter", adapter)

    hub._queue_pending_notification("finding", "msg", None, "down")
    counts = await hub.notify_retry_sweep(now_ts=int(time.time()))  # queue scheduled for the future

    assert counts == {"delivered": 0, "requeued": 0, "abandoned": 0}
    assert adapter.calls == []


# ── TelegramAdapter new methods ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_adapter_maybe_queue_calls_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    """_maybe_queue invokes the pending_writer hook with the right shape."""
    from poc.hub.notifiers.telegram import TelegramAdapter

    captured: list[tuple] = []

    adapter = TelegramAdapter(
        tg_app_getter=lambda: None,
        chat_id_getter=lambda: 42,
        formatters={},
        keyboard_builders={},
        pending_writer=lambda k, t, rm, e: captured.append((k, t, rm, e)),
    )
    # tg_app returns None → hits the 'not ready' branch which queues.
    monkeypatch.setenv("BEACONRY_MOCK", "")
    await adapter._send("hello", reply_markup=None, kind="finding")

    assert len(captured) == 1
    kind, text, rm, error = captured[0]
    assert kind == "finding"
    assert text == "hello"
    assert rm is None
    assert "not ready" in error


@pytest.mark.asyncio
async def test_adapter_replay_returns_true_on_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """replay() in mock mode short-circuits to True (nothing to actually send)."""
    from poc.hub.notifiers.telegram import TelegramAdapter

    monkeypatch.setenv("BEACONRY_MOCK", "1")
    adapter = TelegramAdapter(
        tg_app_getter=lambda: None,
        chat_id_getter=lambda: 42,
        formatters={},
        keyboard_builders={},
    )
    assert await adapter.replay("hello", None) is True


@pytest.mark.asyncio
async def test_adapter_replay_returns_false_when_tg_app_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from poc.hub.notifiers.telegram import TelegramAdapter

    monkeypatch.setenv("BEACONRY_MOCK", "")
    adapter = TelegramAdapter(
        tg_app_getter=lambda: None,
        chat_id_getter=lambda: 42,
        formatters={},
        keyboard_builders={},
    )
    assert await adapter.replay("hello", None) is False


def test_env_defaults_reasonable(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "BEACONRY_NOTIFY_RETRY_MAX_ATTEMPTS",
        "BEACONRY_NOTIFY_RETRY_BASE_S",
        "BEACONRY_NOTIFY_RETRY_SWEEP_INTERVAL_S",
    ):
        monkeypatch.delenv(var, raising=False)
    import importlib

    importlib.reload(hub)
    assert hub.NOTIFY_RETRY_MAX_ATTEMPTS == 5
    assert hub.NOTIFY_RETRY_BASE_S == 30
    assert hub.NOTIFY_RETRY_SWEEP_INTERVAL_S == 60
