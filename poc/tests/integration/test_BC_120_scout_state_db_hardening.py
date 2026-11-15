"""BC-120 — Scout state DB path hardening (integration — Gherkin encoding).

Covers:
    - CAP-T222: End-to-end wiring of the scout state-DB fault handling —
      `_init_scout_db` → fallback → `/healthz` surface → full
      retry-queue happy path (queue + sweep + depth) on the ephemeral
      fallback. Unit tests (`poc/tests/unit/test_BC_120_scout_state_db_hardening.py`)
      cover the atomic parts; this file exercises them together so a
      wiring regression between `_try_open_and_create`,
      `_effective_scout_state_db_path`, and the retry-queue callers is
      caught — the thing the per-unit mocks can't catch.
"""

from __future__ import annotations

import logging
import sqlite3
import time

import pytest

from poc.scout import scout

# ── Fixture: clean module state before each scenario ───────────────────────


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    scout._state_db_degraded = False  # type: ignore[attr-defined]
    scout._effective_state_db_path = scout.SCOUT_STATE_DB_PATH  # type: ignore[attr-defined]
    monkeypatch.setattr(scout, "_FALLBACK_STATE_DB_PATH", "")


# ── Scenario 1: Primary writable → no degradation (end to end) ────────────


def test_primary_writable_scenario_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Scenario 1 (full): writable primary path → scout boots, queues
    a scan, counts it, sweeps it (or leaves it due), reports healthy."""
    db = str(tmp_path / "scout-state.db")
    monkeypatch.setattr(scout, "SCOUT_STATE_DB_PATH", db)

    scout._init_scout_db()
    assert scout._state_db_degraded is False  # type: ignore[attr-defined]
    assert scout._effective_scout_state_db_path() == db

    # Queue → count ticks up; the row materializes in the primary DB.
    scout._queue_pending_scan('{"host":"h","sboms":[]}', "transient error")
    assert scout._pending_scans_queue_depth() == 1

    # /healthz reflects the healthy state.
    summary = scout._scout_healthz_summary()
    assert summary["checks"]["state_db_degraded"] is False
    assert summary["checks"]["state_db_effective_path"] == db
    assert summary["checks"]["pending_scans_queue_depth"] == 1


# ── Scenario 2+3: Unwritable primary → fallback end to end ────────────────


def test_unwritable_primary_full_fallback_loop(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path,
) -> None:
    """Scenario 2 + 3 encoded together: primary rejected by the real
    write probe → fallback picked → every retry-queue op (queue,
    depth, sweep) routes through the effective path → /healthz
    surfaces state_db_degraded=true + correct effective path."""
    # Make the primary directory read-only so sqlite can't create the
    # DB file there. The real BC-120 fault (UID 65532 can't create
    # /data) maps to this exactly.
    primary_dir = tmp_path / "primary"
    primary_dir.mkdir()
    primary = str(primary_dir / "scout-state.db")
    primary_dir.chmod(0o555)  # read + execute only
    monkeypatch.setattr(scout, "SCOUT_STATE_DB_PATH", primary)

    fallback = str(tmp_path / "fallback.db")
    monkeypatch.setattr(scout, "_FALLBACK_STATE_DB_PATH", fallback)

    try:
        with caplog.at_level(logging.WARNING, logger="scout"):
            scout._init_scout_db()

        assert scout._state_db_degraded is True  # type: ignore[attr-defined]
        assert scout._effective_scout_state_db_path() == fallback

        # WARN log carries the chown recipe.
        joined = "\n".join(r.message for r in caplog.records)
        assert "BC-120" in joined
        assert "chown" in joined.lower()
        assert "65532" in joined

        # Full retry-queue loop against the fallback DB.
        scout._queue_pending_scan('{"host":"h","sboms":[]}', "transient err")
        assert scout._pending_scans_queue_depth() == 1

        # Sweep runs — row stays queued (not yet due) since created_ts ≈ now
        # and next_attempt_ts = now + POST_RETRY_BASE_S > now.
        counts = scout.post_retry_sweep(now_ts=int(time.time()))
        assert counts["delivered"] + counts["requeued"] + counts["abandoned"] == 0

        # /healthz surfaces the degradation + the effective path.
        summary = scout._scout_healthz_summary()
        assert summary["checks"]["state_db_degraded"] is True
        assert summary["checks"]["state_db_effective_path"] == fallback
        assert summary["checks"]["pending_scans_queue_depth"] == 1

        # The row really lives in the fallback DB file, not the primary.
        con = sqlite3.connect(fallback)
        try:
            (count,) = con.execute("SELECT COUNT(*) FROM pending_scans").fetchone()
            assert count == 1
        finally:
            con.close()
    finally:
        primary_dir.chmod(0o755)


# ── Scenario 2.5 — ultrareview regression: real write probe ──────────────


def test_readonly_populated_primary_falls_back_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Primary DB file already contains the pending_scans table but is
    read-only. Pre-ultrareview-fix `_try_open_and_create` would silently
    pass (CREATE TABLE IF NOT EXISTS is a no-op); every later INSERT
    would then drop silently. Post-fix: the INSERT+ROLLBACK probe
    rejects it, scout falls back + flips state_db_degraded."""
    primary_dir = tmp_path / "primary"
    primary_dir.mkdir()
    primary = primary_dir / "scout-state.db"

    # Pre-populate with the right schema.
    con = sqlite3.connect(str(primary))
    con.execute(scout._CREATE_PENDING_SCANS_SQL)
    con.commit()
    con.close()
    primary.chmod(0o444)
    primary_dir.chmod(0o555)

    monkeypatch.setattr(scout, "SCOUT_STATE_DB_PATH", str(primary))
    fallback = str(tmp_path / "fallback.db")
    monkeypatch.setattr(scout, "_FALLBACK_STATE_DB_PATH", fallback)

    try:
        scout._init_scout_db()
        assert scout._state_db_degraded is True  # type: ignore[attr-defined]
        assert scout._effective_scout_state_db_path() == fallback
    finally:
        primary_dir.chmod(0o755)
        primary.chmod(0o644)


# ── /healthz shape symmetry with status.py expectations ──────────────────


def test_healthz_keys_match_status_py_consumer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Data-model symmetry check (DD-TGD 6a lens #2): every /healthz
    key BC-120 added must be consumable by the `status.py` cell
    renderer. A rename on either side would break the status table
    silently; keep them locked."""
    summary = scout._scout_healthz_summary()
    checks = summary["checks"]
    # Reader (status.py::_scout_details) reads state_db_degraded + the
    # effective path label. Writer must emit both keys unconditionally.
    assert "state_db_degraded" in checks
    assert "state_db_effective_path" in checks
    assert isinstance(checks["state_db_degraded"], bool)
    assert isinstance(checks["state_db_effective_path"], str)
