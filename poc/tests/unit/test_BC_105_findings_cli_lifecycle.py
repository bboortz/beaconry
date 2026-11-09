"""BC-105 — Findings CLI shows FIRST SEEN + LAST CHANGE + close_reason.

Covers:
    - CAP-T207: Schema v9 → v10 adds `close_reason TEXT` +
      `last_status_change_ts INTEGER NOT NULL DEFAULT 0` on findings.
      Auto-close write sites populate them. `make findings` renders
      age-formatted columns + a `(reason)` suffix on `auto_fixed`
      rows.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from poc.hub import audit as _audit_mod
from poc.hub import hub
from poc.scripts import findings as findings_cli


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


def _seed_finding(
    db_path: Path,
    fid: str,
    host: str = "host-a",
    status: str = "open",
    absent_count: int = 0,
    found_at: int = 0,
) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO findings (id, host, cve_id, package, version, fixed_version, "
            "severity, cvss, source_type, source_ref, description, found_at, status, "
            "last_seen_ts, absent_count) "
            "VALUES (?, ?, ?, 'pkg', 'v', '', 'Critical', 9.9, 'docker', 'img', 'd', "
            "?, ?, ?, ?)",
            (fid, host, f"CVE-2026-{fid[:4]}", found_at or 1, status, 1, absent_count),
        )
        con.commit()
    finally:
        con.close()


def _read(db_path: Path, fid: str) -> dict:
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT status, close_reason, last_status_change_ts FROM findings WHERE id=?",
            (fid,),
        ).fetchone()
    finally:
        con.close()
    return {
        "status": row[0],
        "close_reason": row[1],
        "last_status_change_ts": int(row[2]),
    }


# ── Schema ──────────────────────────────────────────────────────────────────


def test_schema_v10_adds_close_reason_and_change_ts(db_env: Path) -> None:
    con = sqlite3.connect(db_env / "beaconry.db")
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(findings)").fetchall()}
        version = con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()
    assert "close_reason" in cols
    assert "last_status_change_ts" in cols
    assert version == 10


# ── Write sites stamp close_reason + last_status_change_ts ─────────────────


def test_stale_scout_reconcile_stamps_close_reason(db_env: Path) -> None:
    """BC-102's stale-scout reconcile → close_reason='stale_scout'."""
    # Seed an alerted-offline scout + a finding that will cross threshold.
    con = sqlite3.connect(db_env / "beaconry.db")
    now = 20_000
    try:
        con.execute(
            "INSERT INTO scouts (host, first_seen_ts, last_posted_ts, last_offline_alert_ts) "
            "VALUES ('host-a', 1, ?, ?)",
            (now - 700, now - 60),
        )
        con.commit()
    finally:
        con.close()
    _seed_finding(db_env / "beaconry.db", "a" * 16, status="open", absent_count=2)

    hub.scout_offline_sweep(now_ts=now, threshold_s=600, auto_close=True, threshold_ticks=3)
    data = _read(db_env / "beaconry.db", "a" * 16)
    assert data["status"] == "auto_fixed"
    assert data["close_reason"] == "stale_scout"
    assert data["last_status_change_ts"] == now


def test_operator_set_status_stamps_close_reason(db_env: Path) -> None:
    """set_status(…, 'accepted') → close_reason='operator' + last_status_change_ts=now."""
    _seed_finding(db_env / "beaconry.db", "b" * 16, status="open")
    before = int(time.time())
    hub.set_status("b" * 16, "accepted", source="telegram")
    after = int(time.time())
    data = _read(db_env / "beaconry.db", "b" * 16)
    assert data["status"] == "accepted"
    assert data["close_reason"] == "operator"
    assert before <= data["last_status_change_ts"] <= after


def test_operator_set_status_non_closed_has_none_close_reason(db_env: Path) -> None:
    """Non-closed statuses (snoozed) don't set close_reason."""
    _seed_finding(db_env / "beaconry.db", "c" * 16, status="open")
    hub.set_status("c" * 16, "snoozed", source="telegram")
    data = _read(db_env / "beaconry.db", "c" * 16)
    assert data["status"] == "snoozed"
    assert data["close_reason"] is None


# ── CLI renders age columns + close_reason suffix ──────────────────────────


def test_cli_format_age_simple() -> None:
    now = 1000
    assert findings_cli._format_age(0, now=now) == "—"
    assert findings_cli._format_age(None, now=now) == "—"
    assert findings_cli._format_age(now - 30, now=now) == "30s"
    assert findings_cli._format_age(now - 120, now=now) == "2m"
    assert findings_cli._format_age(now - 7200, now=now) == "2h"
    assert findings_cli._format_age(now - 2 * 86400, now=now) == "2d"


def test_cli_adds_lifecycle_columns_to_header() -> None:
    header_keys = {name for _, name, _ in findings_cli.COLUMNS}
    assert "First seen" in header_keys
    assert "Last change" in header_keys


def test_cli_status_suffix_shown_for_auto_fixed_rows(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """auto_fixed rows with close_reason render as `auto_fixed (<label>)`."""
    _seed_finding(db_env / "beaconry.db", "d" * 16, status="auto_fixed", found_at=1000)
    # Stamp close_reason + last_status_change_ts directly.
    con = sqlite3.connect(db_env / "beaconry.db")
    try:
        con.execute(
            "UPDATE findings SET close_reason='stale_scout', last_status_change_ts=? WHERE id=?",
            (2000, "d" * 16),
        )
        con.commit()
    finally:
        con.close()

    rows = findings_cli.load_findings(db_env / "beaconry.db")
    assert rows
    r = rows[0]
    assert r["status"] == "auto_fixed (stale-scout)"
    assert r["found_at_fmt"] != "—"
    assert r["last_change_fmt"] != "—"


def test_cli_status_unchanged_for_open_with_null_close_reason(db_env: Path) -> None:
    """Non-auto_fixed rows don't get a suffix even if close_reason is set oddly."""
    _seed_finding(db_env / "beaconry.db", "e" * 16, status="open")
    rows = findings_cli.load_findings(db_env / "beaconry.db")
    assert rows[0]["status"] == "open"
    assert rows[0]["last_change_fmt"] == "—"  # never stamped
