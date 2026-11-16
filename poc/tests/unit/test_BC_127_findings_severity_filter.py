"""BC-127 — `beaconry findings --severity` filter.

Covers:
    - CAP-T230: --severity filter accepts Critical/High/Medium/Low (+
      comma-separated multi-select, case-insensitive), threads through
      local-DB + HTTP + local-compose paths, exposed via Makefile.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poc.hub import hub
from poc.scripts.findings import _normalize_severities, load_findings, parse_args


def _seed(db: Path, rows: list[tuple[str, str, float]]) -> None:
    """Seed findings with (id, severity, cvss) tuples; other cols stock.
    Uses CREATE TABLE IF NOT EXISTS so tests that pre-run hub.init_db()
    don't trip over duplicate schema."""
    con = sqlite3.connect(str(db))
    con.execute(
        """CREATE TABLE IF NOT EXISTS findings (
            id TEXT PRIMARY KEY, cve_id TEXT, cve_alias TEXT, severity TEXT,
            cvss REAL, host TEXT, source_ref TEXT, package TEXT, version TEXT,
            fixed_version TEXT, description TEXT, status TEXT, source_type TEXT,
            found_at INTEGER)"""
    )
    for fid, sev, cvss in rows:
        con.execute(
            "INSERT INTO findings "
            "(id, cve_id, cve_alias, severity, cvss, host, source_ref, package, "
            "version, fixed_version, description, status, source_type, found_at) "
            "VALUES (?, 'CVE-X', '', ?, ?, 'h', 'r', 'p', 'v', 'f', 'd', 'open', 'docker', 0)",
            (fid, sev, cvss),
        )
    con.commit()
    con.close()


# ── Normalization helper ──────────────────────────────────────────────


def test_normalize_single_severity_title_case() -> None:
    assert _normalize_severities("critical") == ["Critical"]
    assert _normalize_severities("CRITICAL") == ["Critical"]
    assert _normalize_severities("Critical") == ["Critical"]


def test_normalize_comma_separated() -> None:
    assert _normalize_severities("Critical,High") == ["Critical", "High"]
    assert _normalize_severities("critical, high ") == ["Critical", "High"]


def test_normalize_empty_returns_none() -> None:
    assert _normalize_severities(None) is None
    assert _normalize_severities("") is None
    assert _normalize_severities("   ") is None


# ── argparse flag ─────────────────────────────────────────────────────


def test_parse_args_accepts_severity() -> None:
    args = parse_args(["--severity", "Critical"])
    assert args.severity == "Critical"


# ── Local-DB path ─────────────────────────────────────────────────────


def test_load_findings_single_severity(tmp_path: Path) -> None:
    db = tmp_path / "beaconry.db"
    _seed(db, [("a", "Critical", 9.9), ("b", "High", 8.0), ("c", "Medium", 5.0)])
    rows = load_findings(db, severity="Critical")
    assert len(rows) == 1
    assert rows[0]["severity"] == "Critical"


def test_load_findings_multi_severity(tmp_path: Path) -> None:
    db = tmp_path / "beaconry.db"
    _seed(db, [("a", "Critical", 9.9), ("b", "High", 8.0), ("c", "Medium", 5.0)])
    rows = load_findings(db, severity="Critical,High")
    sevs = sorted(r["severity"] for r in rows)
    assert sevs == ["Critical", "High"]


def test_load_findings_lowercase_severity_matches_canonical(tmp_path: Path) -> None:
    db = tmp_path / "beaconry.db"
    _seed(db, [("a", "Critical", 9.9), ("b", "High", 8.0)])
    rows = load_findings(db, severity="critical")
    assert len(rows) == 1
    assert rows[0]["severity"] == "Critical"


def test_load_findings_unknown_severity_returns_empty(tmp_path: Path) -> None:
    db = tmp_path / "beaconry.db"
    _seed(db, [("a", "Critical", 9.9)])
    rows = load_findings(db, severity="Catastrophic")
    assert rows == []


def test_load_findings_combined_filters(tmp_path: Path) -> None:
    db = tmp_path / "beaconry.db"
    con = sqlite3.connect(str(db))
    con.execute(
        """CREATE TABLE findings (
            id TEXT PRIMARY KEY, cve_id TEXT, cve_alias TEXT, severity TEXT,
            cvss REAL, host TEXT, source_ref TEXT, package TEXT, version TEXT,
            fixed_version TEXT, description TEXT, status TEXT, source_type TEXT,
            found_at INTEGER)"""
    )
    con.executemany(
        "INSERT INTO findings VALUES (?, 'CVE-X', '', ?, 9.9, ?, 'r', 'p', 'v', "
        "'f', 'd', 'open', 'docker', 0)",
        [
            ("a", "Critical", "host-a"),
            ("b", "High", "host-a"),
            ("c", "Critical", "host-b"),
        ],
    )
    con.commit()
    con.close()
    rows = load_findings(db, host="host-a", severity="Critical")
    assert len(rows) == 1
    assert rows[0]["host"] == "host-a"
    assert rows[0]["severity"] == "Critical"


# ── HTTP endpoint ─────────────────────────────────────────────────────


def test_http_findings_severity_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "beaconry.db"
    monkeypatch.setattr(hub, "DB_PATH", db)
    monkeypatch.setenv("HUB_CLI_TOKEN", "")
    hub.init_db()
    _seed(db, [("a", "Critical", 9.9), ("b", "High", 8.0), ("c", "Medium", 5.0)])
    client = TestClient(hub.api)
    r = client.get("/findings?severity=Critical")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["severity"] == "Critical"


def test_http_findings_severity_multi_select(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "beaconry.db"
    monkeypatch.setattr(hub, "DB_PATH", db)
    monkeypatch.setenv("HUB_CLI_TOKEN", "")
    hub.init_db()
    _seed(db, [("a", "Critical", 9.9), ("b", "High", 8.0), ("c", "Medium", 5.0)])
    client = TestClient(hub.api)
    r = client.get("/findings?severity=Critical,High")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2


def test_http_findings_case_insensitive_severity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "beaconry.db"
    monkeypatch.setattr(hub, "DB_PATH", db)
    monkeypatch.setenv("HUB_CLI_TOKEN", "")
    hub.init_db()
    _seed(db, [("a", "Critical", 9.9)])
    client = TestClient(hub.api)
    r = client.get("/findings?severity=critical")
    assert r.status_code == 200
    assert len(r.json()) == 1


# ── Makefile pass-through ────────────────────────────────────────────


def test_makefile_severity_pass_through() -> None:
    makefile = Path(__file__).resolve().parents[3] / "Makefile"
    body = makefile.read_text()
    # The findings target must forward SEVERITY to --severity.
    assert "$(if $(SEVERITY),--severity $(SEVERITY))" in body
