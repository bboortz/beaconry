"""BC-057 — multi-host polish (HOST filter + notification lead).

Covers:
    - CAP-T113: `make findings HOST=` filter
    - CAP-T114: Multi-host-aware notification lead line
    - CAP-B040: Operator with ≥2 scouts can tell at a glance which host
      a notification came from and filter CLI output by host
"""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

# ── findings.py --host filter ──────────────────────────────────────────────


def _make_test_db(path: Path, rows: list[dict]) -> None:
    """Create a minimal findings-shaped DB for load_findings tests."""
    con = sqlite3.connect(path)
    con.execute("""
        CREATE TABLE findings (
            id TEXT PRIMARY KEY,
            host TEXT,
            cve_id TEXT,
            package TEXT,
            version TEXT,
            fixed_version TEXT,
            severity TEXT,
            cvss REAL,
            source_type TEXT,
            source_ref TEXT,
            description TEXT,
            status TEXT,
            tier INTEGER,
            kev INTEGER,
            internet_facing INTEGER
        )
    """)
    for i, row in enumerate(rows):
        con.execute(
            "INSERT INTO findings (id, host, cve_id, package, version, "
            "fixed_version, severity, cvss, source_type, source_ref, "
            "description, status, tier, kev, internet_facing) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"fid-{i}",
                row["host"],
                row.get("cve_id", "CVE-X"),
                row.get("package", "pkg"),
                row.get("version", "1.0"),
                row.get("fixed_version", "1.1"),
                row.get("severity", "High"),
                row.get("cvss", 7.5),
                row.get("source_type", "docker"),
                row.get("source_ref", "nginx:1.24"),
                row.get("description", ""),
                row.get("status", "open"),
                row.get("tier", 2),
                int(row.get("kev", False)),
                int(row.get("internet_facing", False)),
            ),
        )
    con.commit()
    con.close()


def test_load_findings_filters_by_host(tmp_path: Path) -> None:
    """CAP-T113: load_findings(host=X) narrows to a single scout's rows."""
    from poc.scripts.findings import load_findings

    db = tmp_path / "beaconry.db"
    _make_test_db(
        db,
        [
            {"host": "laptop", "cve_id": "CVE-L1"},
            {"host": "server2", "cve_id": "CVE-S1"},
            {"host": "server2", "cve_id": "CVE-S2"},
        ],
    )

    all_rows = load_findings(db)
    assert {r["host"] for r in all_rows} == {"laptop", "server2"}

    s2 = load_findings(db, host="server2")
    assert len(s2) == 2
    assert all(r["host"] == "server2" for r in s2)

    laptop = load_findings(db, host="laptop")
    assert len(laptop) == 1
    assert laptop[0]["host"] == "laptop"


def test_load_findings_filters_compose(tmp_path: Path) -> None:
    """CAP-T113: HOST + STATUS compose as AND."""
    from poc.scripts.findings import load_findings

    db = tmp_path / "beaconry.db"
    _make_test_db(
        db,
        [
            {"host": "laptop", "status": "open"},
            {"host": "laptop", "status": "fixed"},
            {"host": "server2", "status": "open"},
        ],
    )
    rows = load_findings(db, host="laptop", status="open")
    assert len(rows) == 1
    assert rows[0]["host"] == "laptop" and rows[0]["status"] == "open"


def test_argparse_accepts_host() -> None:
    """CAP-T113: `--host X` reaches args.host unchanged."""
    from poc.scripts.findings import parse_args

    args = parse_args(["--host", "laptop"])
    assert args.host == "laptop"
    args = parse_args([])
    assert args.host is None


# ── Multi-host notification lead ────────────────────────────────────────────


@pytest.fixture
def hub_with_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Reload hub with a tmp DB_PATH; returns the hub module."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")

    import hub

    importlib.reload(hub)
    hub.init_db()
    yield hub
    importlib.reload(hub)


def _insert_finding(hub, host: str, cve: str) -> None:
    con = sqlite3.connect(hub.DB_PATH)
    con.execute(
        "INSERT INTO findings (id, host, cve_id, package, version, "
        "fixed_version, severity, cvss, source_type, source_ref, "
        "description, status, tier) VALUES (?, ?, ?, 'p', '1', '2', 'High', 7.5, "
        "'docker', 'nginx:1', '', 'open', 2)",
        (f"{host}-{cve}", host, cve),
    )
    con.commit()
    con.close()


def test_is_multi_host_single_host(hub_with_db) -> None:
    """CAP-T114: 0 or 1 distinct host → single-host; prefix suppressed."""
    hub = hub_with_db
    assert hub.is_multi_host_inventory() is False
    _insert_finding(hub, "server2", "CVE-A")
    assert hub.is_multi_host_inventory() is False


def test_is_multi_host_multiple_hosts(hub_with_db) -> None:
    """CAP-T114: ≥2 distinct hosts → multi-host; prefix active."""
    hub = hub_with_db
    _insert_finding(hub, "server2", "CVE-A")
    _insert_finding(hub, "laptop", "CVE-B")
    assert hub.is_multi_host_inventory() is True


def test_notification_prepends_host_multi_host(hub_with_db) -> None:
    """CAP-B040: multi-host inventory → notification leads with 🖥️ <host>."""
    hub = hub_with_db
    _insert_finding(hub, "server2", "CVE-A")
    _insert_finding(hub, "laptop", "CVE-B")

    finding = hub.Finding(
        id="x",
        host="server2",
        cve_id="CVE-99",
        package="openssl",
        version="1.0",
        fixed_version="1.1",
        severity="High",
        cvss=7.5,
        source_type="docker",
        source_ref="nginx:1.24",
        description="d",
        tier=2,
    )
    msg = hub.format_notification(finding)
    first_line = msg.splitlines()[0]
    assert "🖥️" in first_line
    assert "server2" in first_line


def test_notification_unchanged_single_host(hub_with_db) -> None:
    """CAP-T114: single-host inventory → the host prefix line is suppressed."""
    hub = hub_with_db
    _insert_finding(hub, "server2", "CVE-A")

    finding = hub.Finding(
        id="x",
        host="server2",
        cve_id="CVE-99",
        package="openssl",
        version="1.0",
        fixed_version="1.1",
        severity="High",
        cvss=7.5,
        source_type="docker",
        source_ref="nginx:1.24",
        description="d",
        tier=2,
    )
    msg = hub.format_notification(finding)
    first_line = msg.splitlines()[0]
    assert "🖥️" not in first_line
