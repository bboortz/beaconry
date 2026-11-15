"""BC-120 — Scout state DB path degrades loudly to ephemeral fallback.

Covers:
    - CAP-T222: Scout writability-tests BEACONRY_SCOUT_STATE_DB at startup;
      on fault falls back to /tmp/scout-state-<pid>.db, logs a fix-recipe
      WARN, flips `state_db_degraded: true` + exposes
      `state_db_effective_path` on /healthz. All pending_scans operations
      route through the effective path. `beaconry status` DETAILS cell
      renders `state_db degraded` when the flag fires.
"""

from __future__ import annotations

import importlib
import logging
import sqlite3

import pytest

from poc.scout import scout

# ── Reset scout-module state between tests ────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_state_db_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with a clean slate for BC-120 module globals."""
    scout._state_db_degraded = False  # type: ignore[attr-defined]
    scout._effective_state_db_path = scout.SCOUT_STATE_DB_PATH  # type: ignore[attr-defined]


# ── Scenario 1: writable primary → no degradation ─────────────────────────


def test_primary_writable_no_degradation(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    db_path = str(tmp_path / "scout-state.db")
    monkeypatch.setattr(scout, "SCOUT_STATE_DB_PATH", db_path)
    scout._init_scout_db()
    assert scout._state_db_degraded is False  # type: ignore[attr-defined]
    assert scout._effective_scout_state_db_path() == db_path
    summary = scout._scout_healthz_summary()
    assert summary["checks"]["state_db_degraded"] is False
    assert summary["checks"]["state_db_effective_path"] == db_path


# ── Scenario 2: unwritable primary → fallback + WARN ──────────────────────


def test_unwritable_primary_falls_back_to_tmp(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path,
) -> None:
    """Simulate `sqlite3.connect` raising only for the primary path."""
    primary = "/data/scout-state.db"
    monkeypatch.setattr(scout, "SCOUT_STATE_DB_PATH", primary)

    real_connect = sqlite3.connect

    def selective_connect(db_file, *args, **kwargs):  # type: ignore[no-untyped-def]
        if db_file == primary:
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(db_file, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", selective_connect)

    with caplog.at_level(logging.WARNING, logger="scout"):
        scout._init_scout_db()

    assert scout._state_db_degraded is True  # type: ignore[attr-defined]
    eff = scout._effective_scout_state_db_path()
    assert eff.startswith("/tmp/scout-state-")
    assert eff.endswith(".db")
    assert str(scout.os.getpid()) in eff
    # WARN includes the fix recipe.
    joined = "\n".join(r.message for r in caplog.records)
    assert "BC-120" in joined
    assert primary in joined
    assert "chown" in joined.lower()
    assert "65532" in joined


# ── Scenario 3: all pending_scans ops use effective path ──────────────────


def test_pending_scans_ops_use_effective_path(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """_queue_pending_scan, post_retry_sweep, _pending_scans_queue_depth
    must route through _effective_scout_state_db_path()."""
    fallback_db = str(tmp_path / "scout-state-fallback.db")
    primary = "/data/scout-state.db"
    monkeypatch.setattr(scout, "SCOUT_STATE_DB_PATH", primary)

    real_connect = sqlite3.connect

    def selective_connect(db_file, *args, **kwargs):  # type: ignore[no-untyped-def]
        if db_file == primary:
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(db_file, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", selective_connect)
    # Force the effective path to our tmp file so we can inspect rows.
    monkeypatch.setattr(scout, "_FALLBACK_STATE_DB_PATH", fallback_db)

    scout._init_scout_db()
    assert scout._effective_scout_state_db_path() == fallback_db

    scout._queue_pending_scan('{"fake":"body"}', "simulated error")
    assert scout._pending_scans_queue_depth() == 1
    # Row readable from the fallback path.
    con = real_connect(fallback_db)
    try:
        (row_count,) = con.execute("SELECT COUNT(*) FROM pending_scans").fetchone()
        assert row_count == 1
    finally:
        con.close()


# ── Scenario 4: fallback path includes PID (per-process uniqueness) ──────


def test_fallback_path_includes_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout.os, "getpid", lambda: 4242)
    fallback = scout._build_fallback_state_db_path()
    assert fallback == "/tmp/scout-state-4242.db"


def test_fallback_resets_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """BC-120 Scenario 4: a second 'process' (different pid) gets a
    distinct fallback path, so queued rows in the first process's DB
    file are not visible to the second. Simulates restart semantics
    deterministically — monkeypatching `os.getpid` is the closest a
    unit test gets to a real fork without actually forking."""
    monkeypatch.setattr(scout.os, "getpid", lambda: 1111)
    path_a = scout._build_fallback_state_db_path()
    monkeypatch.setattr(scout.os, "getpid", lambda: 2222)
    path_b = scout._build_fallback_state_db_path()
    assert path_a != path_b
    assert "1111" in path_a and "2222" in path_b


# ── Scenario 6: make status surfaces degradation ──────────────────────────


def test_status_renders_state_db_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """When /healthz reports state_db_degraded, status.py DETAILS gets
    `state_db degraded`."""
    from poc.scripts import status as sm

    importlib.reload(sm)

    monkeypatch.setattr(
        sm,
        "fetch_healthz",
        lambda url, timeout=2.0, token="": {
            "status": "ok",
            "checks": {
                "last_scan_ts": 0,
                "docker_socket": "ok",
                "state_db_degraded": True,
                "state_db_effective_path": "/tmp/scout-state-123.db",
            },
        },
    )
    s = sm.gather_scout_status(url="http://localhost:18001/healthz")
    assert s.state == sm.STATE_RUNNING
    assert "state_db degraded" in s.details


def test_status_omits_state_db_detail_when_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from poc.scripts import status as sm

    importlib.reload(sm)

    monkeypatch.setattr(
        sm,
        "fetch_healthz",
        lambda url, timeout=2.0, token="": {
            "status": "ok",
            "checks": {
                "last_scan_ts": 0,
                "docker_socket": "ok",
                "state_db_degraded": False,
                "state_db_effective_path": "/data/scout-state.db",
            },
        },
    )
    s = sm.gather_scout_status(url="http://localhost:18001/healthz")
    assert "state_db degraded" not in s.details


# ── /healthz summary shape ────────────────────────────────────────────────


def test_healthz_summary_includes_new_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    summary = scout._scout_healthz_summary()
    assert "state_db_degraded" in summary["checks"]
    assert "state_db_effective_path" in summary["checks"]


# ── Ultrareview regressions ──────────────────────────────────────────────


def test_probe_detects_readonly_populated_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """BC-120 (ultrareview fix #1): pre-populate a DB with the correct
    table, then make it read-only. A bare `CREATE TABLE IF NOT EXISTS`
    probe would silently pass (no-op on an existing table), the caller
    would mark the primary path healthy, and every later INSERT would
    fail — the exact silent-drop BC-120 is meant to prevent. The
    INSERT+ROLLBACK probe must reject it."""
    primary = tmp_path / "scout-state.db"
    # Seed the DB with the right schema so bare CREATE TABLE is a no-op.
    con = sqlite3.connect(str(primary))
    con.execute(scout._CREATE_PENDING_SCANS_SQL)
    con.commit()
    con.close()
    primary.chmod(0o444)  # read-only
    # The directory also has to be read-only so sqlite can't create the
    # journal file alongside the DB.
    tmp_path.chmod(0o555)
    try:
        assert scout._try_open_and_create(str(primary)) is False
    finally:
        # Restore perms so pytest can clean up tmp_path.
        tmp_path.chmod(0o755)
        primary.chmod(0o644)


def test_probe_detects_corrupt_db_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """BC-120 (ultrareview fix #4): DB file exists but is not a valid
    sqlite file. `sqlite3.connect` opens lazily so it succeeds; the
    first real read (CREATE TABLE) raises `sqlite3.DatabaseError`
    which is NOT an `OperationalError`. Pre-fix, this propagated out
    of `_init_scout_db` → uvicorn startup fails → scout crash-loops.
    Post-fix (catch `sqlite3.Error`) the probe returns False and
    scout falls back cleanly."""
    corrupt = tmp_path / "not-a-db.sqlite"
    corrupt.write_bytes(b"this is definitely not a sqlite file\x00")
    assert scout._try_open_and_create(str(corrupt)) is False


def test_init_scout_db_falls_back_on_corrupt_primary(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """BC-120 (ultrareview fix #4, end-to-end): corrupt primary →
    `_init_scout_db` falls back + flips the flag, does NOT propagate."""
    corrupt = tmp_path / "scout-state.db"
    corrupt.write_bytes(b"not a sqlite file")
    fallback_db = str(tmp_path / "fallback.db")
    monkeypatch.setattr(scout, "SCOUT_STATE_DB_PATH", str(corrupt))
    monkeypatch.setattr(scout, "_FALLBACK_STATE_DB_PATH", fallback_db)

    with caplog.at_level(logging.WARNING, logger="scout"):
        scout._init_scout_db()  # must not raise

    assert scout._state_db_degraded is True  # type: ignore[attr-defined]
    assert scout._effective_scout_state_db_path() == fallback_db


# ── Scenario 5: Dockerfile pre-creates /data owned by UID 65532 ──────────


def test_scout_dockerfile_creates_data_dir_for_nonroot() -> None:
    """BC-120: standalone scout deploys mount no /data volume. The image
    must ship with /data pre-created + owned by UID 65532 so the BC-107
    DB opens cleanly out of the box. Enforced via two paired lines:
    builder `mkdir -p /stage-data`, runtime `COPY --chown=65532:65532`."""
    from pathlib import Path

    dockerfile = Path(__file__).resolve().parents[3] / "poc" / "scout" / "Dockerfile"
    body = dockerfile.read_text()
    assert "mkdir -p /stage-data" in body, (
        "builder stage must create /stage-data for BC-120 /data fix"
    )
    assert "COPY --from=builder --chown=65532:65532 /stage-data /data" in body, (
        "runtime stage must COPY /stage-data → /data with nonroot ownership"
    )
