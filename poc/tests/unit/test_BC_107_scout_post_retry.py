"""BC-107 — Scout POST retry queue.

Covers:
    - CAP-T209: scout persists failed /scan POSTs to `pending_scans`;
      background sweep replays via `_post_scan_body` with exponential
      backoff; on success → DELETE + `scan_delivered` log; on failure
      beyond MAX_ATTEMPTS → DELETE + `scan_abandoned` log; `/healthz`
      exposes `pending_scans_queue_depth` so operators see backlog.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from poc.scout import scout


@pytest.fixture
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate scout-state.db to a tempdir so tests don't touch /data."""
    db_path = tmp_path / "scout-state.db"
    monkeypatch.setattr(scout, "SCOUT_STATE_DB_PATH", str(db_path))
    scout._init_scout_db()
    return db_path


def _read_pending(db_path: Path) -> list[tuple]:
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT id, attempts, next_attempt_ts, last_error FROM pending_scans ORDER BY id"
        ).fetchall()
    finally:
        con.close()


# ── Schema ──────────────────────────────────────────────────────────────────


def test_init_scout_db_creates_pending_scans_table(db_env: Path) -> None:
    con = sqlite3.connect(db_env)
    try:
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        con.close()
    assert "pending_scans" in tables


def test_init_scout_db_is_idempotent(db_env: Path) -> None:
    """Re-running init on an existing DB must not raise or drop data."""
    scout._queue_pending_scan('{"host": "a"}', "err1")
    scout._init_scout_db()  # Second call
    rows = _read_pending(db_env)
    assert len(rows) == 1


# ── Queueing ────────────────────────────────────────────────────────────────


def test_queue_pending_scan_writes_row(db_env: Path) -> None:
    scout._queue_pending_scan('{"host": "x", "sboms": []}', "Hub 503")
    rows = _read_pending(db_env)
    assert len(rows) == 1
    _, attempts, _, last_error = rows[0]
    assert attempts == 0
    assert last_error == "Hub 503"


def test_queue_truncates_long_error_to_400_chars(db_env: Path) -> None:
    long_err = "X" * 2000
    scout._queue_pending_scan('{"host": "x"}', long_err)
    rows = _read_pending(db_env)
    assert len(rows[0][3]) <= 400


def test_queue_schedules_first_retry_at_base_s(db_env: Path, monkeypatch) -> None:
    import time as _time

    frozen_now = 1_700_000_000
    monkeypatch.setattr(_time, "time", lambda: frozen_now)
    monkeypatch.setattr(scout, "POST_RETRY_BASE_S", 42)
    scout._queue_pending_scan('{"a": 1}', "e")
    rows = _read_pending(db_env)
    assert rows[0][2] == frozen_now + 42


# ── Sweep — success path ────────────────────────────────────────────────────


def test_sweep_delivers_and_deletes_on_success(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scout._queue_pending_scan('{"host": "ok"}', "temp fail")
    monkeypatch.setattr(scout, "_post_scan_body", lambda body: (True, "", None))

    counts = scout.post_retry_sweep(now_ts=10**11)
    assert counts == {"delivered": 1, "requeued": 0, "abandoned": 0}
    assert _read_pending(db_env) == []


def test_sweep_skips_not_due_rows(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows with next_attempt_ts > now stay in queue, sweep is a no-op."""
    scout._queue_pending_scan('{"host": "future"}', "e")
    # Manually set next_attempt_ts far in the future.
    con = sqlite3.connect(db_env)
    con.execute("UPDATE pending_scans SET next_attempt_ts=?", (10**12,))
    con.commit()
    con.close()

    called = {"count": 0}

    def _spy(body):
        called["count"] += 1
        return True, "", None

    monkeypatch.setattr(scout, "_post_scan_body", _spy)
    counts = scout.post_retry_sweep(now_ts=1)
    assert counts == {"delivered": 0, "requeued": 0, "abandoned": 0}
    assert called["count"] == 0
    assert len(_read_pending(db_env)) == 1


# ── Sweep — failure + exponential backoff ────────────────────────────────────


def test_sweep_requeues_on_failure_with_exponential_backoff(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scout, "POST_RETRY_BASE_S", 30)
    monkeypatch.setattr(scout, "POST_RETRY_MAX_ATTEMPTS", 5)
    scout._queue_pending_scan('{"host": "x"}', "e")
    monkeypatch.setattr(scout, "_post_scan_body", lambda body: (False, "still 503", None))

    sweep_now = 10**12
    counts = scout.post_retry_sweep(now_ts=sweep_now)
    assert counts == {"delivered": 0, "requeued": 1, "abandoned": 0}

    rows = _read_pending(db_env)
    assert len(rows) == 1
    _, attempts, next_attempt_ts, last_error = rows[0]
    assert attempts == 1
    # First retry: base × 2^1 = 60s
    assert next_attempt_ts == sweep_now + 60
    assert last_error == "still 503"


def test_sweep_backoff_doubles_each_attempt(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout, "POST_RETRY_BASE_S", 30)
    monkeypatch.setattr(scout, "POST_RETRY_MAX_ATTEMPTS", 10)
    scout._queue_pending_scan('{"a": 1}', "e")
    monkeypatch.setattr(scout, "_post_scan_body", lambda body: (False, "fail", None))

    expected = [60, 120, 240, 480]  # 30 × 2^1, 2^2, 2^3, 2^4
    now = 10**12
    for exp in expected:
        scout.post_retry_sweep(now_ts=now)
        row = _read_pending(db_env)[0]
        _, _, next_attempt_ts, _ = row
        assert next_attempt_ts == now + exp, (
            f"attempt gap mismatch: expected {exp}, got {next_attempt_ts - now}"
        )
        # Advance past the schedule for the next sweep tick.
        now = next_attempt_ts


def test_sweep_abandons_row_after_max_attempts(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scout, "POST_RETRY_BASE_S", 30)
    monkeypatch.setattr(scout, "POST_RETRY_MAX_ATTEMPTS", 3)
    scout._queue_pending_scan('{"doomed": true}', "e")
    monkeypatch.setattr(scout, "_post_scan_body", lambda body: (False, "fail forever", None))

    now = 10**12
    counts_history = []
    # attempt 1: 0→1 (requeued, next=now+60)
    # attempt 2: 1→2 (requeued, next=now+120)
    # attempt 3: 2→3 == MAX_ATTEMPTS → abandoned
    for _ in range(3):
        c = scout.post_retry_sweep(now_ts=now)
        counts_history.append(c)
        # Advance past the newly-scheduled next_attempt_ts.
        rows = _read_pending(db_env)
        if rows:
            now = rows[0][2]

    assert counts_history[-1]["abandoned"] == 1
    assert _read_pending(db_env) == []


# ── Queue depth probe ───────────────────────────────────────────────────────


def test_pending_scans_queue_depth_empty_when_table_empty(db_env: Path) -> None:
    assert scout._pending_scans_queue_depth() == 0


def test_pending_scans_queue_depth_counts_rows(db_env: Path) -> None:
    scout._queue_pending_scan('{"a": 1}', "e1")
    scout._queue_pending_scan('{"a": 2}', "e2")
    scout._queue_pending_scan('{"a": 3}', "e3")
    assert scout._pending_scans_queue_depth() == 3


def test_healthz_surfaces_pending_scans_queue_depth(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scout._queue_pending_scan('{"a": 1}', "e")
    summary = scout._scout_healthz_summary()
    assert summary["checks"]["pending_scans_queue_depth"] == 1


# ── Env parsing ─────────────────────────────────────────────────────────────


def test_env_defaults_are_sensible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_SCOUT_POST_RETRY_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("BEACONRY_SCOUT_POST_RETRY_BASE_S", raising=False)
    monkeypatch.delenv("BEACONRY_SCOUT_POST_RETRY_SWEEP_INTERVAL_S", raising=False)
    import importlib

    importlib.reload(scout)
    assert scout.POST_RETRY_MAX_ATTEMPTS == 5
    assert scout.POST_RETRY_BASE_S == 30
    assert scout.POST_RETRY_SWEEP_INTERVAL_S == 60


def test_env_invalid_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_SCOUT_POST_RETRY_MAX_ATTEMPTS", "not-a-number")
    import importlib

    importlib.reload(scout)
    assert scout.POST_RETRY_MAX_ATTEMPTS == 5


def test_env_rejects_zero_or_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_SCOUT_POST_RETRY_MAX_ATTEMPTS", "0")
    monkeypatch.setenv("BEACONRY_SCOUT_POST_RETRY_BASE_S", "-5")
    import importlib

    importlib.reload(scout)
    assert scout.POST_RETRY_MAX_ATTEMPTS == 5
    assert scout.POST_RETRY_BASE_S == 30


# ── collect_and_post integration with queue ─────────────────────────────────


def test_post_scan_body_on_success_returns_data_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"findings": 42}

    monkeypatch.setattr(scout.requests, "post", lambda *a, **kw: _FakeResp())
    ok, err, data = scout._post_scan_body('{"host": "x"}')
    assert ok is True
    assert err == ""
    assert isinstance(data, dict) and data["findings"] == 42


def test_post_scan_body_on_network_fail_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*a, **kw):
        import requests as _requests

        raise _requests.ConnectionError("hub down")

    monkeypatch.setattr(scout.requests, "post", _raise)
    ok, err, data = scout._post_scan_body('{"host": "x"}')
    assert ok is False
    assert "hub down" in err
    assert data is None
