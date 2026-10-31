"""BC-030 — Finding.internet_facing + DB schema + save/load round-trip.

Covers:
    - CAP-T069: Hub joins finding to port map at ingest; sets Finding.internet_facing
      (dataclass + DB schema + round-trip).
"""

from __future__ import annotations

import sqlite3
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
    kwargs: dict[str, object] = {
        "id": "deadbeef12345678",
        "host": "homelab",
        "cve_id": "CVE-X",
        "package": "pkg",
        "version": "1.0",
        "fixed_version": "1.1",
        "severity": "High",
        "cvss": 8.0,
        "source_type": "docker",
        "source_ref": "nginx:1.24",
        "description": "d",
    }
    kwargs.update(overrides)
    return Finding(**kwargs)  # type: ignore[arg-type]


def test_finding_default_false() -> None:
    """Finding.internet_facing defaults to False — pre-BC-030 semantics preserved."""
    f = _finding()
    assert f.internet_facing is False


def test_finding_accepts_true() -> None:
    f = _finding(internet_facing=True)
    assert f.internet_facing is True


def test_init_db_adds_internet_facing_column(db: Path) -> None:
    con = sqlite3.connect(db)
    cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
    con.close()
    assert "internet_facing" in cols


def test_init_db_migration_idempotent(db: Path) -> None:
    hub.init_db()
    hub.init_db()
    con = sqlite3.connect(db)
    cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
    con.close()
    assert "internet_facing" in cols


def test_save_finding_persists_internet_facing(db: Path) -> None:
    f = _finding(id="exposed01", internet_facing=True)
    assert hub.save_finding(f) is True
    con = sqlite3.connect(db)
    row = con.execute("SELECT internet_facing FROM findings WHERE id=?", ("exposed01",)).fetchone()
    con.close()
    assert row[0] == 1


def test_save_finding_false_persists_as_zero(db: Path) -> None:
    f = _finding(id="lan01", internet_facing=False)
    assert hub.save_finding(f) is True
    con = sqlite3.connect(db)
    row = con.execute("SELECT internet_facing FROM findings WHERE id=?", ("lan01",)).fetchone()
    con.close()
    assert row[0] == 0


def test_load_finding_round_trip(db: Path) -> None:
    f = _finding(id="roundtrip01", internet_facing=True)
    hub.save_finding(f)
    loaded = hub.load_finding("roundtrip01")
    assert loaded is not None
    assert loaded.internet_facing is True


def test_load_finding_round_trip_false(db: Path) -> None:
    f = _finding(id="roundtrip02", internet_facing=False)
    hub.save_finding(f)
    loaded = hub.load_finding("roundtrip02")
    assert loaded is not None
    assert loaded.internet_facing is False
