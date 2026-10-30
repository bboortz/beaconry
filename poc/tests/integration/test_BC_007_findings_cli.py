"""BC-007 findings CLI — integration tests.

Covers:
    - CAP-B012: Operator reviews current findings state without re-running the pipeline
    - CAP-T025: Findings reporter reads SQLite, formats a table, honors STATUS filter
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _init_dev_db(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    db = data_dir / "beaconry.db"
    con = sqlite3.connect(db)
    con.execute("""
        CREATE TABLE IF NOT EXISTS findings (
            id           TEXT PRIMARY KEY,
            host         TEXT,
            cve_id       TEXT,
            package      TEXT,
            version      TEXT,
            fixed_version TEXT,
            severity     TEXT,
            cvss         REAL,
            source_type  TEXT,
            source_ref   TEXT,
            description  TEXT,
            found_at     INTEGER,
            status       TEXT DEFAULT 'open'
        )
    """)
    con.commit()
    con.close()
    return db


def _insert(db: Path, **row: object) -> None:
    con = sqlite3.connect(db)
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" * len(row))
    con.execute(f"INSERT INTO findings ({cols}) VALUES ({placeholders})", list(row.values()))
    con.commit()
    con.close()


def _row(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "host": "h",
        "package": "p",
        "version": "1",
        "fixed_version": "2",
        "source_type": "docker",
        "source_ref": "img:1",
        "description": "",
        "found_at": 1,
    }
    defaults.update(overrides)
    return defaults


def _run(data_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATA_DIR": str(data_dir)}
    return subprocess.run(
        [sys.executable, "-m", "poc.scripts.findings", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=15,
    )


def test_empty_db_friendly_message(tmp_path: Path) -> None:
    _init_dev_db(tmp_path)
    result = _run(tmp_path)
    assert result.returncode == 0
    assert "No findings" in result.stdout
    assert "make demo" in result.stdout


def test_prints_all_findings(tmp_path: Path) -> None:
    db = _init_dev_db(tmp_path)
    _insert(db, id="x1", cve_id="CVE-A", severity="Critical", cvss=9.8, status="open", **_row())
    _insert(db, id="x2", cve_id="CVE-B", severity="High", cvss=7.5, status="snoozed", **_row())
    result = _run(tmp_path)
    assert result.returncode == 0
    assert "CVE-A" in result.stdout
    assert "CVE-B" in result.stdout
    assert "CVE" in result.stdout and "Severity" in result.stdout and "Status" in result.stdout


def test_status_filter_narrows_output(tmp_path: Path) -> None:
    db = _init_dev_db(tmp_path)
    _insert(db, id="x1", cve_id="CVE-OPEN", severity="High", cvss=8.0, status="open", **_row())
    _insert(
        db,
        id="x2",
        cve_id="CVE-ACCEPTED",
        severity="Critical",
        cvss=9.0,
        status="accepted",
        **_row(),
    )
    result = _run(tmp_path, "--status", "accepted")
    assert result.returncode == 0
    assert "CVE-ACCEPTED" in result.stdout
    assert "CVE-OPEN" not in result.stdout


def test_sort_open_before_other_statuses(tmp_path: Path) -> None:
    db = _init_dev_db(tmp_path)
    _insert(
        db, id="x1", cve_id="CVE-ACC", severity="Critical", cvss=9.5, status="accepted", **_row()
    )
    _insert(db, id="x2", cve_id="CVE-OPEN", severity="High", cvss=7.5, status="open", **_row())
    result = _run(tmp_path)
    assert result.returncode == 0
    assert result.stdout.index("CVE-OPEN") < result.stdout.index("CVE-ACC")


def test_pure_read_does_not_modify_db(tmp_path: Path) -> None:
    db = _init_dev_db(tmp_path)
    _insert(db, id="x1", cve_id="CVE-X", severity="High", cvss=7.5, status="open", **_row())
    before = db.stat().st_mtime_ns
    result = _run(tmp_path)
    after = db.stat().st_mtime_ns
    assert result.returncode == 0
    assert before == after


def test_long_fields_truncate_with_ellipsis(tmp_path: Path) -> None:
    db = _init_dev_db(tmp_path)
    row = _row(
        source_ref="ghcr.io/some-org/very-long-image-name-here:v1.2.3-alpine-slim-build-123",
        package="a-package-with-a-rather-long-identifier-indeed",
    )
    _insert(db, id="x1", cve_id="CVE-LONG", severity="High", cvss=7.5, status="open", **row)
    result = _run(tmp_path)
    assert result.returncode == 0
    assert "…" in result.stdout
    for line in result.stdout.splitlines():
        assert len(line) <= 110, f"line too long: {line!r}"


def test_unit_format_table_renders_header_and_rows() -> None:
    from poc.scripts.findings import format_table

    rows = [
        {
            "cve_id": "CVE-X",
            "severity": "High",
            "cvss": 7.5,
            "host": "h",
            "source_ref": "img:1",
            "package": "p",
            "status": "open",
        }
    ]
    output = format_table(rows)
    assert "CVE" in output
    assert "CVE-X" in output
    assert "7.5" in output
