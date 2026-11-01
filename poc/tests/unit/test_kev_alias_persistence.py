"""BC-043 — Finding.cve_alias + DB migration + round-trip.

Covers:
    - CAP-T091: Finding.cve_alias field + DB migration + save/load round-trip
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
        "cve_id": "GO-2026-6604",
        "package": "stdlib",
        "version": "1.0",
        "fixed_version": "2.0",
        "severity": "High",
        "cvss": 9.8,
        "source_type": "docker",
        "source_ref": "postgres:16-alpine",
        "description": "test",
    }
    kwargs.update(overrides)
    return Finding(**kwargs)  # type: ignore[arg-type]


def test_finding_default_cve_alias_is_none() -> None:
    """Dataclass default: cve_alias is None unless explicitly set."""
    f = _finding()
    assert f.cve_alias is None


def test_finding_accepts_cve_alias() -> None:
    f = _finding(cve_alias="CVE-2024-12345")
    assert f.cve_alias == "CVE-2024-12345"


def test_init_db_adds_cve_alias_column(db: Path) -> None:
    con = sqlite3.connect(db)
    cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
    con.close()
    assert "cve_alias" in cols


def test_init_db_migration_idempotent(db: Path) -> None:
    hub.init_db()
    hub.init_db()
    con = sqlite3.connect(db)
    cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
    con.close()
    assert "cve_alias" in cols


def test_save_finding_roundtrips_cve_alias(db: Path) -> None:
    f = _finding(id="save01", cve_alias="CVE-2024-12345")
    assert hub.save_finding(f) is True
    con = sqlite3.connect(db)
    row = con.execute("SELECT cve_alias FROM findings WHERE id=?", ("save01",)).fetchone()
    con.close()
    assert row[0] == "CVE-2024-12345"


def test_load_finding_roundtrips_cve_alias(db: Path) -> None:
    f = _finding(id="load01", cve_alias="CVE-2024-99999")
    hub.save_finding(f)
    loaded = hub.load_finding("load01")
    assert loaded is not None
    assert loaded.cve_alias == "CVE-2024-99999"


def test_legacy_row_loads_cve_alias_as_none(db: Path) -> None:
    """A row inserted without the cve_alias column (legacy pre-BC-043) → None on load."""
    con = sqlite3.connect(db)
    # Direct INSERT without cve_alias to simulate the legacy case.
    con.execute(
        "INSERT INTO findings (id, host, cve_id, package, version, fixed_version, "
        "severity, cvss, source_type, source_ref, description, found_at, "
        "tier, kev, internet_facing) "
        "VALUES ('legacy01','h','GO-2026-X','pkg','1','2','High',9.0,'docker','img:1','d',1,2,0,0)"
    )
    con.commit()
    con.close()

    loaded = hub.load_finding("legacy01")
    assert loaded is not None
    assert loaded.cve_alias is None
