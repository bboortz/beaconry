"""BC-116 — Finding state machine extension.

Covers:
    - CAP-T218: `_VALID_STATUSES` adds `fixing` (transient, in-flight)
      and `rolled_back` (reserved terminal). `set_status` raises
      `ValueError` on unknown status. `stale_fixing_sweep` reverts
      `fixing` rows older than `BEACONRY_FIXING_TIMEOUT_S` back to
      `open` and emits `fix_timeout` audit events.
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
    # Reset audit state for test isolation.
    _audit_mod._degraded = False
    _audit_mod._last_error = ""
    _audit_mod._write_failures = 0
    _audit_mod._write_failures_since_degraded = 0
    hub.init_db()
    return data_dir


def _insert_finding(db: Path, fid: str, host: str, status: str) -> None:
    now = int(time.time())
    con = sqlite3.connect(db / "beaconry.db")
    try:
        con.execute(
            "INSERT OR REPLACE INTO findings "
            "(id, host, cve_id, package, version, fixed_version, severity, cvss, "
            "source_type, source_ref, description, tier, kev, status, found_at, "
            "last_seen_ts, last_status_change_ts) "
            "VALUES (?, ?, 'CVE-X', 'curl', '7.0', '7.9', 'Critical', 9.8, "
            "'docker', 'nginx:latest', '', 1, 0, ?, ?, ?, ?)",
            (fid, host, status, now, now, now),
        )
        con.commit()
    finally:
        con.close()


def _get_status(db: Path, fid: str) -> str:
    con = sqlite3.connect(db / "beaconry.db")
    try:
        row = con.execute("SELECT status FROM findings WHERE id=?", (fid,)).fetchone()
    finally:
        con.close()
    assert row is not None
    return row[0]


# ── _VALID_STATUSES ─────────────────────────────────────────────────────────


def test_valid_statuses_includes_fixing_and_rolled_back() -> None:
    assert "fixing" in hub._VALID_STATUSES
    assert "rolled_back" in hub._VALID_STATUSES


def test_valid_statuses_preserves_existing() -> None:
    """Regression guard: existing statuses MUST remain valid."""
    for existing in (
        "open",
        "fixed",
        "fixed_verified",
        "verification_failed",
        "regressed",
        "snoozed",
        "accepted",
        "auto_fixed",
    ):
        assert existing in hub._VALID_STATUSES


# ── set_status validation ──────────────────────────────────────────────────


def test_set_status_accepts_fixing(db_env: Path) -> None:
    _insert_finding(db_env, "f1", "h1", "open")
    hub.set_status("f1", "fixing")
    assert _get_status(db_env, "f1") == "fixing"


def test_set_status_accepts_rolled_back(db_env: Path) -> None:
    _insert_finding(db_env, "f2", "h1", "open")
    hub.set_status("f2", "rolled_back")
    assert _get_status(db_env, "f2") == "rolled_back"


def test_set_status_rejects_unknown(db_env: Path) -> None:
    _insert_finding(db_env, "f3", "h1", "open")
    with pytest.raises(ValueError) as exc:
        hub.set_status("f3", "garbage_status")
    assert "garbage_status" in str(exc.value)
    assert "BC-116" in str(exc.value)
    # Row must remain untouched.
    assert _get_status(db_env, "f3") == "open"


def test_set_status_existing_statuses_still_work(db_env: Path) -> None:
    """Regression guard: BC-058's existing statuses still accepted."""
    _insert_finding(db_env, "f4", "h1", "open")
    hub.set_status("f4", "fixed")
    assert _get_status(db_env, "f4") == "fixed"
    hub.set_status("f4", "fixed_verified")
    assert _get_status(db_env, "f4") == "fixed_verified"


# ── stale_fixing_sweep ─────────────────────────────────────────────────────


def test_sweep_reverts_stale_fixing_to_open(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub, "FIXING_TIMEOUT_S", 60)

    _insert_finding(db_env, "f5", "h1", "open")
    hub.set_status("f5", "fixing")
    # Fast-forward: pretend the transition was 10 min ago.
    now = int(time.time())
    con = sqlite3.connect(db_env / "beaconry.db")
    con.execute(
        "UPDATE findings SET last_status_change_ts=? WHERE id=?",
        (now - 600, "f5"),
    )
    con.commit()
    con.close()

    reverted = hub.stale_fixing_sweep(now_ts=now)
    assert reverted == 1
    assert _get_status(db_env, "f5") == "open"


def test_sweep_skips_recent_fixing(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub, "FIXING_TIMEOUT_S", 600)

    _insert_finding(db_env, "f6", "h1", "open")
    hub.set_status("f6", "fixing")
    # Fresh fixing — should NOT be reverted.
    reverted = hub.stale_fixing_sweep(now_ts=int(time.time()))
    assert reverted == 0
    assert _get_status(db_env, "f6") == "fixing"


def test_sweep_only_touches_fixing_status(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows in other statuses must be ignored even if last_status_change_ts
    is very old."""
    monkeypatch.setattr(hub, "FIXING_TIMEOUT_S", 60)

    _insert_finding(db_env, "f7", "h1", "open")
    _insert_finding(db_env, "f8", "h1", "snoozed")
    _insert_finding(db_env, "f9", "h1", "accepted")
    # Make all three old.
    con = sqlite3.connect(db_env / "beaconry.db")
    con.execute("UPDATE findings SET last_status_change_ts=1")
    con.commit()
    con.close()

    now = int(time.time())
    reverted = hub.stale_fixing_sweep(now_ts=now)
    assert reverted == 0
    assert _get_status(db_env, "f7") == "open"
    assert _get_status(db_env, "f8") == "snoozed"
    assert _get_status(db_env, "f9") == "accepted"


def test_sweep_emits_fix_timeout_audit_event(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub, "FIXING_TIMEOUT_S", 60)

    _insert_finding(db_env, "f10", "h-bb", "fixing")
    con = sqlite3.connect(db_env / "beaconry.db")
    con.execute(
        "UPDATE findings SET last_status_change_ts=? WHERE id=?",
        (int(time.time()) - 1000, "f10"),
    )
    con.commit()
    con.close()

    hub.stale_fixing_sweep(now_ts=int(time.time()))

    log_path = db_env / "audit.log"
    assert log_path.exists()
    import json as _json

    events = [_json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    timeout_events = [e for e in events if e.get("event") == "fix_timeout"]
    assert len(timeout_events) == 1
    e = timeout_events[0]
    assert e["finding_id"] == "f10"
    assert e["host"] == "h-bb"
    assert e["timeout_s"] == 60


def test_sweep_handles_cold_db_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-init_db: no findings table. Sweep must return 0 silently."""
    monkeypatch.setattr(hub, "DB_PATH", tmp_path / "nonexistent.db")
    monkeypatch.setattr(hub, "FIXING_TIMEOUT_S", 60)
    reverted = hub.stale_fixing_sweep(now_ts=int(time.time()))
    assert reverted == 0


# ── Env parsing ─────────────────────────────────────────────────────────────


def test_env_defaults_sensible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_FIXING_TIMEOUT_S", raising=False)
    monkeypatch.delenv("BEACONRY_FIXING_SWEEP_INTERVAL_S", raising=False)
    import importlib

    importlib.reload(hub)
    assert hub.FIXING_TIMEOUT_S == 600
    assert hub.FIXING_SWEEP_INTERVAL_S == 60


def test_env_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_FIXING_TIMEOUT_S", "garbage")
    import importlib

    importlib.reload(hub)
    assert hub.FIXING_TIMEOUT_S == 600
