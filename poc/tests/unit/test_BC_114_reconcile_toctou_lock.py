"""BC-114 — Reconcile TOCTOU lock.

Covers:
    - CAP-T216: `reconcile_scan` wraps its SELECT + per-row UPDATEs in
      `BEGIN IMMEDIATE`. Concurrent writers block until reconcile
      COMMITs. Lock-acquisition timeout → skip cycle (no auto-close
      misfires).
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


def _insert_finding(db: Path, fid: str, host: str, absent_count: int = 0) -> None:
    """Minimal direct INSERT for test-fixture setup."""
    con = sqlite3.connect(db / "beaconry.db")
    try:
        con.execute(
            "INSERT OR REPLACE INTO findings "
            "(id, host, cve_id, package, version, fixed_version, severity, cvss, "
            "source_type, source_ref, description, tier, kev, status, found_at, "
            "last_seen_ts, absent_count) "
            "VALUES (?, ?, 'CVE-2026-9999', 'curl', '7.0', '7.9', 'Critical', 9.8, "
            "'docker', 'nginx:latest', '', 1, 0, 'open', ?, ?, ?)",
            (fid, host, int(time.time()), int(time.time()), absent_count),
        )
        con.commit()
    finally:
        con.close()


def _row(db: Path, fid: str) -> tuple | None:
    con = sqlite3.connect(db / "beaconry.db")
    try:
        return con.execute(
            "SELECT status, absent_count FROM findings WHERE id=?", (fid,)
        ).fetchone()
    finally:
        con.close()


# ── Happy path still works ──────────────────────────────────────────────────


def test_reconcile_scan_still_auto_closes_absent_finding(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Baseline: wrapping in BEGIN IMMEDIATE must not break the auto-close
    path. Finding with absent_count=THRESHOLD-1 + absent in scan → auto_fixed."""
    monkeypatch.setattr(hub, "RECONCILE_ENABLED", True)
    monkeypatch.setattr(hub, "AUTO_FIX_THRESHOLD", 3)

    _insert_finding(db_env, "f1", host="h1", absent_count=2)

    regressed_fix, auto_closes, regressed_auto = hub.reconcile_scan(host="h1", scanned_ids=set())
    assert len(auto_closes) == 1
    assert auto_closes[0].id == "f1"
    row = _row(db_env, "f1")
    assert row is not None
    status, absent = row
    assert status == "auto_fixed"
    assert absent == 3


def test_reconcile_scan_still_bumps_absent_count_below_threshold(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub, "RECONCILE_ENABLED", True)
    monkeypatch.setattr(hub, "AUTO_FIX_THRESHOLD", 5)

    _insert_finding(db_env, "f2", host="h1", absent_count=1)

    _, auto_closes, _ = hub.reconcile_scan(host="h1", scanned_ids=set())
    assert auto_closes == []
    row = _row(db_env, "f2")
    assert row is not None
    status, absent = row
    assert status == "open"
    assert absent == 2


def test_reconcile_scan_disabled_returns_empty(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub, "RECONCILE_ENABLED", False)
    _insert_finding(db_env, "f3", host="h1", absent_count=10)

    result = hub.reconcile_scan(host="h1", scanned_ids=set())
    assert result == ([], [], [])


# ── Lock acquisition ────────────────────────────────────────────────────────


def test_reconcile_skips_cycle_when_lock_unavailable(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hold an EXCLUSIVE lock on the DB from another connection; reconcile's
    BEGIN IMMEDIATE times out. Must not auto-close the finding — reconcile
    skips the cycle and returns empty."""
    monkeypatch.setattr(hub, "RECONCILE_ENABLED", True)
    monkeypatch.setattr(hub, "AUTO_FIX_THRESHOLD", 3)

    _insert_finding(db_env, "f4", host="h1", absent_count=2)

    # Patch hub.sqlite3.connect so reconcile's connection gets a short timeout.
    # The lock holder uses the un-patched sqlite3 to keep its own timeout long.
    orig_connect = sqlite3.connect

    def _fast_connect(db, **kwargs):
        kwargs["timeout"] = 0.2
        return orig_connect(db, **kwargs)

    # Hold an EXCLUSIVE lock from a separate connection.
    lock_con = orig_connect(db_env / "beaconry.db", timeout=5)
    lock_con.isolation_level = None
    lock_con.execute("BEGIN EXCLUSIVE")
    try:
        monkeypatch.setattr(hub.sqlite3, "connect", _fast_connect)
        result = hub.reconcile_scan(host="h1", scanned_ids=set())
        assert result == ([], [], [])
    finally:
        lock_con.execute("ROLLBACK")
        lock_con.close()

    # Row must remain untouched — no silent auto-close. Read AFTER the
    # EXCLUSIVE lock is released.
    row = _row(db_env, "f4")
    assert row is not None
    status, absent = row
    assert status == "open"
    assert absent == 2


# ── Serialization against concurrent writes ────────────────────────────────


def test_reconcile_uses_begin_immediate(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Structural test: reconcile_scan sets isolation_level=None on its
    connection AND executes BEGIN IMMEDIATE. These two facts together are
    the BC-114 fix — the concurrency behavior tested elsewhere depends
    on both. We spy on sqlite3.connect to assert this.
    """
    monkeypatch.setattr(hub, "RECONCILE_ENABLED", True)
    monkeypatch.setattr(hub, "AUTO_FIX_THRESHOLD", 3)
    _insert_finding(db_env, "f5", host="h1", absent_count=2)

    executed_sql: list[str] = []
    isolation_levels: list[object] = []
    original_connect = sqlite3.connect

    class _SpyConnection:
        def __init__(self, real):
            self._real = real

        def __setattr__(self, name, value):
            if name == "_real":
                object.__setattr__(self, name, value)
                return
            if name == "isolation_level":
                isolation_levels.append(value)
            setattr(self._real, name, value)

        def execute(self, sql, *args, **kwargs):
            # Only track top-level statements — skip the per-row UPDATEs
            # to keep the assertion readable.
            if sql.startswith(("BEGIN", "COMMIT", "ROLLBACK")):
                executed_sql.append(sql)
            return self._real.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    def _spy_connect(path, **kwargs):
        return _SpyConnection(original_connect(path, **kwargs))

    monkeypatch.setattr(hub.sqlite3, "connect", _spy_connect)
    hub.reconcile_scan(host="h1", scanned_ids=set())

    # isolation_level must have been set to None at least once on the
    # reconcile connection.
    assert None in isolation_levels, f"isolation_level never set to None: {isolation_levels}"

    # BEGIN IMMEDIATE must have been issued, and COMMIT must have followed.
    assert any(s.startswith("BEGIN IMMEDIATE") for s in executed_sql), (
        f"BEGIN IMMEDIATE not executed: {executed_sql}"
    )
    assert "COMMIT" in executed_sql, f"COMMIT not executed: {executed_sql}"
    # BEGIN comes before COMMIT.
    begin_idx = next(i for i, s in enumerate(executed_sql) if s.startswith("BEGIN IMMEDIATE"))
    commit_idx = executed_sql.index("COMMIT")
    assert begin_idx < commit_idx


def test_isolation_level_restored_to_none_within_scope(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity: the connection reconcile_scan opens sets isolation_level=None.
    This is internal to the function and must not leak to other connections
    (they each get their own fresh connection + default isolation)."""
    monkeypatch.setattr(hub, "RECONCILE_ENABLED", True)

    # Reconcile once.
    hub.reconcile_scan(host="h-none", scanned_ids=set())

    # Open a fresh connection — default isolation_level should be unchanged.
    con = sqlite3.connect(db_env / "beaconry.db")
    try:
        assert con.isolation_level == ""  # Python default
    finally:
        con.close()
