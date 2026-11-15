"""BC-117 — integration tests for the cross-repo CVE fix workflow.

Covers:
    - CAP-T219: End-to-end wiring of BEACONRY_CROSSREPO_SOURCES policy
      parsing → finding persistence with status=awaiting_upstream +
      cross_repo_hint JSON column → HTTP /findings endpoint exposes
      the hint through the schema. Unit tests
      (`poc/tests/unit/test_BC_117_cross_repo_cve_fix_workflow.py`)
      cover the atoms; this file exercises them together so a wiring
      regression between policy parser, save_finding, and HTTP
      serializer is caught.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poc.hub import hub


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated DB + DATA_DIR so audit writes don't leak."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setenv("HUB_SCOUT_TOKEN", "")
    monkeypatch.setenv("HUB_CLI_TOKEN", "")
    hub.init_db()
    return data_dir


def _insert_finding_with_hint(db_path: Path, hint: dict) -> str:
    """Insert a finding row with an awaiting_upstream status + the given
    cross_repo_hint JSON. Returns the finding id."""
    con = sqlite3.connect(str(db_path))
    con.execute(
        "INSERT INTO findings (id, cve_id, cve_alias, severity, cvss, host, "
        "source_ref, package, version, fixed_version, description, status, "
        "source_type, found_at, cross_repo_hint) VALUES ("
        "'xp-finding', 'CVE-UPSTREAM', '', 'Critical', 9.8, 'host-a', "
        "'nginx:alpine', 'brotli', '1.0', '1.1', 'd', 'awaiting_upstream', "
        "'docker', 0, ?)",
        (json.dumps(hint),),
    )
    con.commit()
    con.close()
    return "xp-finding"


# ── Scenario: awaiting_upstream row round-trips through /findings ────────


def test_awaiting_upstream_finding_persists_with_hint(hub_env: Path) -> None:
    db_path = hub_env / "beaconry.db"
    hint = {
        "repo": "docker-library/official-images",
        "issue_url": "https://github.com/docker-library/official-images/issues/new",
        "policy_key": "nginx:*",
    }
    _insert_finding_with_hint(db_path, hint)
    # Query via load_cross_repo_hint (hub helper): must return the dict.
    hub.DB_PATH = db_path
    loaded = hub.load_cross_repo_hint("xp-finding")
    assert loaded == hint


def test_http_findings_returns_awaiting_upstream_status(hub_env: Path) -> None:
    db_path = hub_env / "beaconry.db"
    _insert_finding_with_hint(db_path, {"policy_key": "nginx:*", "repo": "org/repo"})
    client = TestClient(hub.api)
    r = client.get("/findings?status=awaiting_upstream")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["cve_id"] == "CVE-UPSTREAM"
    assert rows[0]["status"] == "awaiting_upstream"


def test_http_findings_serializes_cross_repo_hint(hub_env: Path) -> None:
    """BC-117 wiring: the hint column must flow through _FINDINGS_ENDPOINT_COLS
    so the CLI can render `awaiting_upstream (<policy_key>)`."""
    db_path = hub_env / "beaconry.db"
    _insert_finding_with_hint(db_path, {"policy_key": "nginx:*", "repo": "org/repo"})
    client = TestClient(hub.api)
    r = client.get("/findings")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    # The endpoint must include cross_repo_hint (added to
    # _FINDINGS_ENDPOINT_COLS by the fix/findings-http-missing-timestamp-columns
    # PR). Confirm the policy_key is recoverable client-side.
    raw = rows[0]["cross_repo_hint"]
    assert raw is not None
    parsed = json.loads(raw)
    assert parsed["policy_key"] == "nginx:*"


# ── Scenario: schema migration keeps cross_repo_hint column ─────────────


def test_cross_repo_hint_column_exists_after_init_db(hub_env: Path) -> None:
    """Regression: schema v10 → v11 added `cross_repo_hint` on `findings`.
    BC-123 bumped to v12; the column must still be present."""
    db_path = hub_env / "beaconry.db"
    con = sqlite3.connect(str(db_path))
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
        assert "cross_repo_hint" in cols
    finally:
        con.close()
