"""Unit tests for the append-only audit log.

Covers:
    - CAP-T067: Append-only audit log of operator-visible state changes
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from poc.hub import audit


@pytest.fixture
def audit_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    return data_dir


def test_append_writes_jsonl_line(audit_env: Path) -> None:
    audit.append("status_change", finding_id="abc", status="fixed")
    content = (audit_env / "audit.log").read_text().strip()
    event = json.loads(content)
    assert event["event"] == "status_change"
    assert event["finding_id"] == "abc"
    assert event["status"] == "fixed"
    assert isinstance(event["ts"], int)


def test_append_is_append_only(audit_env: Path) -> None:
    audit.append("event_a", n=1)
    audit.append("event_b", n=2)
    audit.append("event_c", n=3)
    lines = (audit_env / "audit.log").read_text().strip().splitlines()
    assert len(lines) == 3
    types = [json.loads(line)["event"] for line in lines]
    assert types == ["event_a", "event_b", "event_c"]


def test_append_is_fire_and_forget_on_io_failure(
    audit_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit is observability, not control-flow — must never raise."""
    monkeypatch.setenv("DATA_DIR", "/nonexistent/path/that/cannot/be/created/by/user")
    # Should not raise even though the path is unwritable.
    audit.append("status_change", finding_id="x", status="y")


def test_read_events_returns_newest_first(audit_env: Path) -> None:
    audit.append("first")
    audit.append("second")
    audit.append("third")
    events = audit.read_events()
    assert [e["event"] for e in events] == ["third", "second", "first"]


def test_read_events_respects_limit(audit_env: Path) -> None:
    for i in range(10):
        audit.append("x", n=i)
    events = audit.read_events(limit=3)
    assert len(events) == 3
    assert [e["n"] for e in events] == [9, 8, 7]


def test_read_events_tolerates_malformed_lines(audit_env: Path) -> None:
    log_file = audit_env / "audit.log"
    log_file.write_text('{"event": "ok"}\nnot-json\n{"event": "also-ok"}\n')
    events = audit.read_events()
    assert [e["event"] for e in events] == ["also-ok", "ok"]


def test_read_events_returns_empty_when_no_log(audit_env: Path) -> None:
    assert audit.read_events() == []


def test_set_status_appends_audit_event(audit_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Integration: hub.set_status writes to audit log."""
    import hub

    monkeypatch.setattr(hub, "DB_PATH", audit_env / "beaconry.db")
    hub.init_db()
    # Insert a row directly so there's something for set_status to update.
    import sqlite3

    con = sqlite3.connect(hub.DB_PATH)
    con.execute(
        "INSERT INTO findings (id, host, cve_id, status) VALUES (?,?,?,?)",
        ("abc123", "h", "CVE-X", "open"),
    )
    con.commit()
    con.close()

    hub.set_status("abc123", "fixed")

    events = audit.read_events()
    assert len(events) == 1
    assert events[0]["event"] == "status_change"
    assert events[0]["finding_id"] == "abc123"
    assert events[0]["status"] == "fixed"
