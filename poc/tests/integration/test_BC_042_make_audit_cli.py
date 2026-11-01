"""BC-042 — `make audit` CLI + `scan_received` audit event.

Covers:
    - CAP-T089: `make audit` CLI with filters + compose-aware routing
    - CAP-T090: Hub emits `scan_received` audit event per `/scan` POST
    - CAP-B026: Operator answers troubleshooting questions without parsing JSON
"""

from __future__ import annotations

import importlib
import io
import json
import sqlite3
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poc.scripts import audit as audit_cli

# ── CLI invocation harness ──────────────────────────────────────────────────


def _run_cli(data_dir: Path, env: dict, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> str:
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_FINDINGS_LOCAL", "1")  # force local path
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in ("LAST", "SINCE", "UNTIL", "EVENT", "CVE", "HOST", "FINDING", "COUNT", "FOLLOW"):
        if k not in env:
            monkeypatch.delenv(k, raising=False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        audit_cli.main(argv)
    return buf.getvalue()


def _write_audit(audit_path: Path, events: list[dict]) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


# ── CLI scenarios ───────────────────────────────────────────────────────────


def test_default_run_returns_last_50(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = int(time.time())
    events = [
        {"ts": now - i, "event": "finding_ingested", "cve_id": f"CVE-X-{i:03d}"} for i in range(120)
    ]
    _write_audit(tmp_path / "audit.log", events)
    out = _run_cli(tmp_path, {}, monkeypatch, argv=[])
    # Header + separator + 50 rows:
    body_lines = [ln for ln in out.splitlines() if "finding_ingested" in ln]
    assert len(body_lines) == 50


def test_since_filter_restricts_to_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = int(time.time())
    events = [
        {"ts": now - 3600, "event": "x", "cve_id": "OLD"},
        {"ts": now - 60, "event": "x", "cve_id": "RECENT"},
    ]
    _write_audit(tmp_path / "audit.log", events)
    out = _run_cli(tmp_path, {"SINCE": "5m"}, monkeypatch, argv=[])
    assert "RECENT" in out
    assert "OLD" not in out


def test_multi_event_filter_or_semantics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = int(time.time())
    events = [
        {"ts": now, "event": "digest_sent"},
        {"ts": now, "event": "digest_throttled"},
        {"ts": now, "event": "finding_ingested"},
    ]
    _write_audit(tmp_path / "audit.log", events)
    out = _run_cli(tmp_path, {"EVENT": "digest_sent,digest_throttled"}, monkeypatch, argv=[])
    assert "digest_sent" in out
    assert "digest_throttled" in out
    assert "finding_ingested" not in out


def test_cve_filter_resolves_via_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Status-change events store only `finding_id`. CVE filter must resolve
    via the DB so operators can query by CVE and still find them."""
    # Seed DB with a finding.
    db = tmp_path / "beaconry.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE findings (id TEXT PRIMARY KEY, cve_id TEXT, host TEXT, "
        "package TEXT, version TEXT, fixed_version TEXT, severity TEXT, "
        "cvss REAL, source_type TEXT, source_ref TEXT, description TEXT, "
        "found_at INTEGER, status TEXT, tier INTEGER, kev INTEGER)"
    )
    con.execute("INSERT INTO findings (id, cve_id, host) VALUES ('abc', 'CVE-FINDME', 'h1')")
    con.commit()
    con.close()

    now = int(time.time())
    events = [
        {"ts": now, "event": "status_change", "finding_id": "abc", "status": "snoozed"},
        {"ts": now, "event": "status_change", "finding_id": "def", "status": "snoozed"},
    ]
    _write_audit(tmp_path / "audit.log", events)
    out = _run_cli(tmp_path, {"CVE": "CVE-FINDME"}, monkeypatch, argv=[])
    assert "CVE-FINDME" in out
    assert "status=snoozed" in out
    # The second event (finding_id=def, no DB mapping) must NOT appear.
    lines_with_status_change = [ln for ln in out.splitlines() if "status_change" in ln]
    assert len(lines_with_status_change) == 1


def test_count_mode_groups_by_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = int(time.time())
    events = [
        {"ts": now - 500, "event": "a"},
        {"ts": now - 400, "event": "a"},
        {"ts": now - 300, "event": "b"},
    ]
    _write_audit(tmp_path / "audit.log", events)
    out = _run_cli(tmp_path, {"COUNT": "1"}, monkeypatch, argv=[])
    assert "EVENT" in out  # header
    # Both event types present with counts.
    assert "a" in out
    assert "b" in out
    # Count for `a` is 2
    a_line = next(ln for ln in out.splitlines() if ln.startswith("a "))
    assert "2" in a_line


def test_host_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = int(time.time())
    events = [
        {"ts": now, "event": "x", "host": "homelab"},
        {"ts": now, "event": "x", "host": "staging"},
    ]
    _write_audit(tmp_path / "audit.log", events)
    out = _run_cli(tmp_path, {"HOST": "homelab"}, monkeypatch, argv=[])
    assert "homelab" in out
    assert "staging" not in out


def test_finding_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = int(time.time())
    events = [
        {"ts": now, "event": "status_change", "finding_id": "aaa"},
        {"ts": now, "event": "status_change", "finding_id": "bbb"},
    ]
    _write_audit(tmp_path / "audit.log", events)
    out = _run_cli(tmp_path, {"FINDING": "aaa"}, monkeypatch, argv=[])
    assert "finding_id=aaa" in out
    assert "finding_id=bbb" not in out


def test_empty_audit_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_audit(tmp_path / "audit.log", [])
    out = _run_cli(tmp_path, {}, monkeypatch, argv=[])
    assert "No events" in out


def test_bad_since_fails_with_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_audit(tmp_path / "audit.log", [{"ts": 1, "event": "x"}])
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BEACONRY_FINDINGS_LOCAL", "1")
    monkeypatch.setenv("SINCE", "yesterday")
    rc = audit_cli.main([])
    assert rc == 2
    err = capsys.readouterr().err
    assert "time parse error" in err


# ── scan_received audit event (hub side) ────────────────────────────────────


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("BEACONRY_TEST_MODE", "1")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")

    import hub

    importlib.reload(hub)
    client = TestClient(hub.api)
    yield {"client": client, "audit": data_dir / "audit.log", "hub": hub}
    client.close()
    monkeypatch.delenv("BEACONRY_TEST_MODE", raising=False)
    importlib.reload(hub)


def test_scan_received_emitted(hub_env: dict) -> None:
    """Hub emits `scan_received` on every /scan POST, including non-empty ones."""
    r = hub_env["client"].post("/scan", json={"host": "test-host", "sboms": []})
    assert r.status_code == 200
    events = [
        json.loads(line) for line in hub_env["audit"].read_text().splitlines() if line.strip()
    ]
    received = [e for e in events if e["event"] == "scan_received"]
    assert len(received) == 1
    assert received[0]["host"] == "test-host"
    assert received[0]["sbom_count"] == 0
    assert received[0]["exposed_count"] == 0


def test_scan_received_fires_even_with_zero_candidates(hub_env: dict) -> None:
    """BC-042 motivation: scout-posts-nothing is now distinguishable from scout-silent."""
    r = hub_env["client"].post("/scan", json={"host": "quiet-host", "sboms": []})
    assert r.status_code == 200
    events = [
        json.loads(line) for line in hub_env["audit"].read_text().splitlines() if line.strip()
    ]
    assert any(e["event"] == "scan_received" and e["host"] == "quiet-host" for e in events)


def test_scan_received_carries_exposed_count(hub_env: dict) -> None:
    """BC-030 integration: `container_exposure` map's True count surfaces in audit."""
    r = hub_env["client"].post(
        "/scan",
        json={
            "host": "test-host",
            "sboms": [],
            "container_exposure": {"img:a": True, "img:b": True, "img:c": False},
        },
    )
    assert r.status_code == 200
    events = [
        json.loads(line) for line in hub_env["audit"].read_text().splitlines() if line.strip()
    ]
    received = next(e for e in events if e["event"] == "scan_received")
    assert received["exposed_count"] == 2
