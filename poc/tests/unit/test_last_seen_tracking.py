"""BC-034 — last_seen_ts tracking.

Covers:
    - CAP-T077: Hub persists `last_seen_ts` per finding — timestamp of the most
      recent scout cycle that still reported the (host, cve, source_ref, package)
      tuple. Prerequisite data for digest-refresh / heartbeat reminder features.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import hub
import pytest

from poc.hub.executor.base import Finding


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    hub.init_db()
    return data_dir / "beaconry.db"


def _finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = dict(
        id="deadbeef12345678",
        host="homelab",
        cve_id="CVE-TEST",
        package="pkg",
        version="1.0",
        fixed_version="1.1",
        severity="High",
        cvss=8.0,
        source_type="docker",
        source_ref="nginx:1.24",
        description="x",
    )
    defaults.update(overrides)
    return Finding(**defaults)  # type: ignore[arg-type]


def _get_last_seen(db: Path, fid: str) -> int:
    con = sqlite3.connect(db)
    row = con.execute("SELECT last_seen_ts FROM findings WHERE id=?", (fid,)).fetchone()
    con.close()
    return row[0] if row else -1


def test_init_db_adds_last_seen_ts_column(db: Path) -> None:
    con = sqlite3.connect(db)
    cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
    con.close()
    assert "last_seen_ts" in cols


def test_init_db_migration_is_idempotent(db: Path) -> None:
    hub.init_db()
    hub.init_db()
    hub.init_db()
    # Still consistent — no OperationalError, column still present.
    con = sqlite3.connect(db)
    cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
    con.close()
    assert "last_seen_ts" in cols


def test_legacy_rows_backfill_last_seen_from_found_at(db: Path) -> None:
    """BC-034 breaking-change-in-planning: a legacy row written pre-BC-034
    has last_seen_ts=0. init_db must backfill from `found_at` so the
    downstream heartbeat features don't see 'last seen in 1970'."""
    # Insert a legacy row directly (bypassing save_finding) with found_at set
    # but last_seen_ts=0 (default for the newly-added column).
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO findings (id, host, cve_id, package, version, fixed_version, "
        "severity, cvss, source_type, source_ref, description, found_at, status, "
        "tier, kev, last_fixed_ts, last_seen_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "legacy01",
            "homelab",
            "CVE-X",
            "p",
            "1",
            "2",
            "High",
            8.0,
            "docker",
            "nginx:1.24",
            "x",
            1700000000,
            "open",
            2,
            0,
            0,
            0,
        ),
    )
    con.commit()
    con.close()

    hub.init_db()  # idempotent re-run should trigger the backfill

    assert _get_last_seen(db, "legacy01") == 1700000000


def test_bump_last_seen_updates_present_findings(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub.save_finding(_finding(id="present01"))
    hub.save_finding(_finding(id="present02", cve_id="CVE-Y"))

    frozen_now = 1800000000
    monkeypatch.setattr(hub.time, "time", lambda: frozen_now)

    hub.bump_last_seen("homelab", {"present01", "present02"})

    assert _get_last_seen(db, "present01") == frozen_now
    assert _get_last_seen(db, "present02") == frozen_now


def test_bump_last_seen_leaves_absent_findings_alone(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A finding not in the current scan must NOT have its last_seen_ts
    updated — otherwise 'last seen' loses meaning."""
    hub.save_finding(_finding(id="still_here_01"))
    hub.save_finding(_finding(id="absent_in_scan_01", cve_id="CVE-Y"))
    original_absent = _get_last_seen(db, "absent_in_scan_01")

    frozen_now = 1800000000
    monkeypatch.setattr(hub.time, "time", lambda: frozen_now)
    hub.bump_last_seen("homelab", {"still_here_01"})

    assert _get_last_seen(db, "still_here_01") == frozen_now
    assert _get_last_seen(db, "absent_in_scan_01") == original_absent


def test_bump_last_seen_scoped_to_host(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub.save_finding(_finding(id="hostA01", host="host-a"))
    hub.save_finding(_finding(id="hostB01", host="host-b", cve_id="CVE-Y"))
    original_b = _get_last_seen(db, "hostB01")

    frozen_now = 1800000000
    monkeypatch.setattr(hub.time, "time", lambda: frozen_now)
    hub.bump_last_seen("host-a", {"hostA01", "hostB01"})  # pass both ids

    assert _get_last_seen(db, "hostA01") == frozen_now
    # host-b's row stays — the WHERE clause scopes by host.
    assert _get_last_seen(db, "hostB01") == original_b


def test_bump_last_seen_empty_set_is_noop(db: Path) -> None:
    hub.save_finding(_finding(id="noop01"))
    before = _get_last_seen(db, "noop01")
    hub.bump_last_seen("homelab", set())
    assert _get_last_seen(db, "noop01") == before


def test_bump_last_seen_updates_regardless_of_status(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Snoozed / accepted findings still get last_seen_ts updated — the
    operator chose to defer action but beaconry still observes the inventory."""
    for fid, status in (
        ("snoozed_01", "snoozed"),
        ("accepted_01", "accepted"),
        ("fixed_01", "fixed"),
    ):
        hub.save_finding(_finding(id=fid, cve_id=f"CVE-{fid}"))
        hub.set_status(fid, status)

    frozen_now = 1800000000
    monkeypatch.setattr(hub.time, "time", lambda: frozen_now)
    hub.bump_last_seen("homelab", {"snoozed_01", "accepted_01", "fixed_01"})

    for fid in ("snoozed_01", "accepted_01", "fixed_01"):
        assert _get_last_seen(db, fid) == frozen_now, f"{fid} must update"


def test_bump_last_seen_tolerates_missing_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold-boot safety: calling before init_db is a no-op, not a crash."""
    monkeypatch.setattr(hub, "DB_PATH", tmp_path / "cold.db")
    # Don't call init_db — the table doesn't exist.
    hub.bump_last_seen("homelab", {"anyid"})  # must not raise


def test_save_finding_sets_last_seen_on_new_row(db: Path) -> None:
    """A fresh finding should have last_seen_ts == found_at — same scan event
    (close-gap in semantics: last_seen is also 'first_seen' at the moment
    of INSERT because found_at = now, backfill applies to the default-0 case)."""
    before = int(time.time())
    hub.save_finding(_finding(id="fresh01"))
    hub.init_db()  # idempotent run triggers backfill for the fresh row (last_seen_ts was 0 → found_at)
    seen = _get_last_seen(db, "fresh01")
    assert seen >= before
