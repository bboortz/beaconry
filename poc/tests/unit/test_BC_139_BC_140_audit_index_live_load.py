"""BC-139 + BC-140 — Audit-index write-amplitude fix + async decoupling.

Covers:
    - CAP-T236 (BC-139): `_audit_index_append` skips `finding_filtered` +
      `heartbeat_received`; buffered writes with background flush every
      2 s via a module-level deque + threading.Lock; WAL PRAGMAs
      bounded (wal_autocheckpoint=100, journal_size_limit=10 MB);
      shutdown drains the buffer before closing.
    - CAP-T237 (BC-140): hot-path append never opens sqlite; bounded
      deque drops oldest on overflow + increments cumulative counter
      surfaced via `_audit_index_metrics()`; rate-limited
      `audit_index_dropped` audit event (1/min); flush failure
      re-queues the batch instead of dropping it.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from pathlib import Path

import pytest

from poc.hub import audit as _audit_mod
from poc.hub import hub


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setenv("HUB_SCOUT_TOKEN", "")
    monkeypatch.setenv("HUB_CLI_TOKEN", "")
    hub.init_db()
    _audit_mod.clear_post_append_hooks()
    hub._reset_audit_index_conn()
    hub._audit_index_buffer.clear()
    hub._audit_index_dropped_events = 0
    hub._audit_index_last_dropped_audit_ts = 0
    _audit_mod.register_post_append_hook(hub._audit_index_append)
    yield data_dir
    # Teardown: clear the hook so subsequent tests (e.g. test_audit.py)
    # don't see our audit_index writer registered.
    _audit_mod.clear_post_append_hooks()
    hub._reset_audit_index_conn()
    hub._audit_index_buffer.clear()


# ── BC-139: skip filter ───────────────────────────────────────────────


def test_skip_filter_finding_filtered(hub_env: Path) -> None:
    """finding_filtered events must NOT enter the audit_index buffer."""
    _audit_mod.append("finding_filtered", host="h1", cve_id="CVE-X")
    assert len(hub._audit_index_buffer) == 0


def test_skip_filter_heartbeat_received(hub_env: Path) -> None:
    """heartbeat_received events must NOT enter the audit_index buffer."""
    _audit_mod.append("heartbeat_received", host="h1", health="ok")
    assert len(hub._audit_index_buffer) == 0


def test_non_skipped_events_buffer(hub_env: Path) -> None:
    """scan_integrity + other events DO enter the buffer."""
    _audit_mod.append("scan_integrity", host="h1", iteration_hash="abc")
    _audit_mod.append("hub_startup", software_version="0.22.17")
    assert len(hub._audit_index_buffer) == 2


# ── BC-139: batching behavior ────────────────────────────────────────


def test_batched_flush_single_executemany(hub_env: Path) -> None:
    """10 buffered events → one flush call drains them all into the
    table. Observable shape: buffer empty + 10 rows visible, achieved
    with one `executemany + commit` pass (not 10 separate commits).
    """
    for i in range(10):
        _audit_mod.append("scan_integrity", host="h1", iteration_hash=f"iter-{i}")
    assert len(hub._audit_index_buffer) == 10

    with hub._audit_index_lock:
        hub._flush_audit_index_batch_locked()

    assert len(hub._audit_index_buffer) == 0
    con = sqlite3.connect(hub.DB_PATH)
    rows = con.execute("SELECT COUNT(*) FROM audit_index").fetchone()[0]
    con.close()
    assert rows == 10


def test_flush_drains_exactly_once(hub_env: Path) -> None:
    """A second flush with an empty buffer is a no-op (idempotent)."""
    _audit_mod.append("scan_integrity", host="h1")
    with hub._audit_index_lock:
        hub._flush_audit_index_batch_locked()
    con = sqlite3.connect(hub.DB_PATH)
    rows_first = con.execute("SELECT COUNT(*) FROM audit_index").fetchone()[0]
    with hub._audit_index_lock:
        hub._flush_audit_index_batch_locked()
    rows_second = con.execute("SELECT COUNT(*) FROM audit_index").fetchone()[0]
    con.close()
    assert rows_first == rows_second == 1


def test_wal_pragmas_applied_on_connection_open(
    hub_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _flush_audit_index_batch_locked opens the connection, it
    sets wal_autocheckpoint=100 and journal_size_limit=10485760."""
    _audit_mod.append("scan_integrity", host="h1")
    with hub._audit_index_lock:
        hub._flush_audit_index_batch_locked()
    assert hub._audit_index_conn is not None
    checkpoint = hub._audit_index_conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
    size_limit = hub._audit_index_conn.execute("PRAGMA journal_size_limit").fetchone()[0]
    assert checkpoint == 100
    assert size_limit == 10485760


def test_flush_interval_background_task(hub_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Background flush task fires at the configured interval; buffer
    is drained within one window."""
    monkeypatch.setattr(hub, "_AUDIT_INDEX_FLUSH_INTERVAL_S", 0.1)

    async def runner() -> None:
        task = asyncio.create_task(hub._audit_index_flush_loop_forever())
        _audit_mod.append("scan_integrity", host="h1")
        assert len(hub._audit_index_buffer) == 1
        await asyncio.sleep(0.25)  # > one flush interval
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert len(hub._audit_index_buffer) == 0

    asyncio.run(runner())


def test_shutdown_flush_drains_buffer(hub_env: Path) -> None:
    """Shutdown drain path flushes the remaining buffer to sqlite."""
    for i in range(5):
        _audit_mod.append("scan_integrity", host="h1", iteration_hash=f"iter-{i}")
    assert len(hub._audit_index_buffer) == 5
    hub._audit_index_shutdown_drain(timeout_s=2.0)
    assert len(hub._audit_index_buffer) == 0
    con = sqlite3.connect(hub.DB_PATH)
    rows = con.execute("SELECT COUNT(*) FROM audit_index").fetchone()[0]
    con.close()
    assert rows == 5


# ── BC-140: hot-path never opens sqlite ──────────────────────────────


def test_append_never_opens_sqlite(hub_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The _audit_index_append hot path must not call sqlite3.connect.
    Patch connect to raise; append must still succeed."""

    def forbidden_connect(*args, **kwargs):
        raise AssertionError("_audit_index_append must not open sqlite on hot path")

    monkeypatch.setattr(hub.sqlite3, "connect", forbidden_connect)

    _audit_mod.append("scan_integrity", host="h1", iteration_hash="xyz")
    assert len(hub._audit_index_buffer) == 1


# ── BC-140: overflow + counter + rate-limited audit event ──────────


def test_overflow_drops_oldest_bumps_counter(
    hub_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When buffer is at maxlen, each subsequent append drops oldest
    and increments _audit_index_dropped_events."""
    # Shrink maxlen to make the test cheap.
    original_maxlen = hub._audit_index_buffer.maxlen
    hub._audit_index_buffer = hub.collections.deque(maxlen=10)
    try:
        for i in range(15):
            _audit_mod.append("scan_integrity", host="h1", iteration_hash=f"iter-{i}")
        assert len(hub._audit_index_buffer) == 10
        assert hub._audit_index_dropped_events == 5
    finally:
        hub._audit_index_buffer = hub.collections.deque(maxlen=original_maxlen)


def test_audit_index_dropped_event_rate_limited(
    hub_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When drops happen rapidly, at most 1 audit_index_dropped event
    fires per 60 s window."""
    hub._audit_index_buffer = hub.collections.deque(maxlen=5)
    # Fire 10 drops in quick succession.
    for i in range(15):
        _audit_mod.append("scan_integrity", host="h1", iteration_hash=f"iter-{i}")

    # Count audit_index_dropped lines in audit.log.
    audit_log = hub_env / "audit.log"
    dropped_count = sum(
        1 for line in audit_log.read_text().splitlines() if '"event": "audit_index_dropped"' in line
    )
    assert dropped_count <= 1, f"rate limit broken: {dropped_count} drop events in <1 min"


def test_metrics_surface_queue_depth_and_dropped(hub_env: Path) -> None:
    """_audit_index_metrics returns a dict with the two new keys."""
    _audit_mod.append("scan_integrity", host="h1")
    _audit_mod.append("scan_integrity", host="h1")
    m = hub._audit_index_metrics()
    assert m == {"audit_index_queue_depth": 2, "audit_index_dropped_events": 0}


# ── BC-140: flush failure preserves events ────────────────────────


def test_flush_failure_requeues_batch(hub_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If sqlite executemany raises, the drained batch is re-queued at
    the front of the deque so the next flush can retry.

    Simulated by replacing the module connection with a stub whose
    executemany raises OperationalError on first call."""
    for i in range(3):
        _audit_mod.append("scan_integrity", host="h1", iteration_hash=f"iter-{i}")

    class FailingConn:
        def execute(self, *args, **kwargs):
            return self

        def executemany(self, *args, **kwargs):
            raise sqlite3.OperationalError("simulated commit failure")

        def commit(self):
            pass

        def close(self):
            pass

    hub._audit_index_conn = FailingConn()  # type: ignore[assignment]

    with hub._audit_index_lock:
        hub._flush_audit_index_batch_locked()

    # Buffer must still have the 3 re-queued rows (in order).
    assert len(hub._audit_index_buffer) == 3
    # Connection should have been reset after the failure.
    assert hub._audit_index_conn is None
