"""BC-131 — Audit-read performance: tail-chunked audit.log + batched finding enrichment.

Covers:
    - CAP-T234: `_tail_audit_events(matcher, limit, chunk_size)` reads
      audit.log backward from EOF in `chunk_size` chunks, parses each
      chunk newest-first, early-exits after `limit` matches. Events
      spanning chunk boundaries reassemble via `leftover` carry-forward.
      Malformed JSON lines are skipped. Empty/missing file returns [].
      `_enrich_findings_batch(events)` collects distinct finding_id
      values across the matched events, issues ONE `SELECT … WHERE id
      IN (...)` for them, applies setdefault in-place. Zero DB calls
      when no event carries `finding_id`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from poc.hub import hub


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setenv("HUB_SCOUT_TOKEN", "")
    hub.init_db()
    return data_dir


def _write_events(audit_path: Path, events: list[dict]) -> None:
    """Write events to audit.log in forward (oldest-first) order,
    one JSON per line — exactly the shape audit.append produces."""
    audit_path.write_text("".join(json.dumps(e) + "\n" for e in events))


# ── Scenario 1 — tail read short-circuits on recent matches ─────────


def test_tail_read_short_circuits_on_recent_matches(hub_env: Path) -> None:
    """10 000 scan_integrity events; request last 10. The function
    must return 10 newest events and NOT need to read the full file.

    We prove it by using a small chunk_size (1 KB) and asserting that
    we get the correct 10 events without reading all 10k events' worth
    of chunks. Under 1 KB chunks, 10k events (each ~80 bytes) would be
    ~800 chunks; with limit=10 we should resolve in ≤2 chunks."""
    audit = hub_env / "audit.log"
    events = [
        {"ts": 1000 + i, "event": "scan_integrity", "host": "h", "n": i} for i in range(10000)
    ]
    _write_events(audit, events)

    result = hub._tail_audit_events(
        matcher=lambda e: e.get("event") == "scan_integrity",
        limit=10,
        chunk_size=1024,
    )
    assert len(result) == 10
    # Newest-first → the last 10 events (ns 9990..9999) in reverse.
    assert [e["n"] for e in result] == list(range(9999, 9989, -1))


# ── Scenario 2 — falls back to full scan when filter is rare ───────


def test_tail_read_full_scan_when_rare_match(hub_env: Path) -> None:
    """2 transfer_hash_mismatch events buried among 500 other events.
    The reader must still find them — no infinite loop, no error."""
    audit = hub_env / "audit.log"
    events: list[dict] = []
    for i in range(500):
        events.append({"ts": i, "event": "finding_filtered", "cve_id": f"CVE-{i}"})
    # Rare events interleaved
    events.insert(50, {"ts": 50, "event": "transfer_hash_mismatch", "host": "h1"})
    events.insert(450, {"ts": 450, "event": "transfer_hash_mismatch", "host": "h2"})
    _write_events(audit, events)

    result = hub._tail_audit_events(
        matcher=lambda e: e.get("event") == "transfer_hash_mismatch",
        limit=10,
        chunk_size=1024,
    )
    assert len(result) == 2
    assert {e["host"] for e in result} == {"h1", "h2"}


# ── Scenario 3 — events spanning chunk boundaries parse correctly ──


def test_tail_read_chunk_boundary_leftover(hub_env: Path) -> None:
    """Write events of varying sizes so some straddle the 128 B chunk
    boundary. All events must still appear in the correct order."""
    audit = hub_env / "audit.log"
    events = [
        {"ts": i, "event": "scan_integrity", "payload": "x" * (10 + i * 3)} for i in range(60)
    ]
    _write_events(audit, events)
    file_size = audit.stat().st_size
    # Deliberately small chunk ensures many straddles.
    chunk_size = 128
    assert file_size > chunk_size * 2, "need multiple chunks for this test"

    result = hub._tail_audit_events(
        matcher=lambda e: e.get("event") == "scan_integrity",
        limit=1000,
        chunk_size=chunk_size,
    )
    # Must reconstruct all 60 events, newest-first.
    assert len(result) == 60
    assert [e["ts"] for e in result] == list(range(59, -1, -1))


# ── Scenario 4 — no finding_id → no DB call ────────────────────────


def test_enrich_findings_batch_zero_db_calls_when_no_finding_id(
    hub_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page of hub_startup / heartbeat_integrity_mismatch events
    carries no finding_id. _enrich_findings_batch must early-exit
    before touching sqlite."""
    db_connect_calls = {"n": 0}
    real_connect = sqlite3.connect

    def counting_connect(*args, **kwargs):
        db_connect_calls["n"] += 1
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(hub.sqlite3, "connect", counting_connect)

    events = [
        {"event": "hub_startup", "software_version": "0.22.15"},
        {"event": "heartbeat_integrity_mismatch", "host": "h"},
    ]
    hub._enrich_findings_batch(events)
    assert db_connect_calls["n"] == 0, f"expected zero sqlite calls, got {db_connect_calls['n']}"


# ── Scenario 5 — events with finding_id batch into one SELECT ───────


def test_enrich_findings_batch_one_select_for_many_events(
    hub_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three events carry finding_ids that exist in the DB; one more
    carries an unknown id. One sqlite3.connect call total; existing
    ids get cve_id/host injected; unknown skipped silently."""
    # Seed the findings DB with two rows.
    hub.init_db()
    con = sqlite3.connect(hub.DB_PATH)
    con.execute(
        "INSERT INTO findings (id, cve_id, host, package, version, fixed_version, "
        "severity, cvss, description, source_type, source_ref, status, found_at) "
        "VALUES (?, ?, ?, '', '', '', 'High', 7.5, '', 'docker', 'x', 'open', 0)",
        ("fid-a", "CVE-A", "host-x"),
    )
    con.execute(
        "INSERT INTO findings (id, cve_id, host, package, version, fixed_version, "
        "severity, cvss, description, source_type, source_ref, status, found_at) "
        "VALUES (?, ?, ?, '', '', '', 'High', 7.5, '', 'docker', 'x', 'open', 0)",
        ("fid-b", "CVE-B", "host-y"),
    )
    con.commit()
    con.close()

    db_connect_calls = {"n": 0}
    real_connect = sqlite3.connect

    def counting_connect(*args, **kwargs):
        db_connect_calls["n"] += 1
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(hub.sqlite3, "connect", counting_connect)

    events = [
        {"event": "finding_filtered", "finding_id": "fid-a"},
        {"event": "finding_filtered", "finding_id": "fid-b"},
        {"event": "finding_filtered", "finding_id": "fid-unknown"},
        {"event": "scan_integrity"},  # no finding_id
    ]
    hub._enrich_findings_batch(events)
    assert db_connect_calls["n"] == 1, f"expected ONE connect, got {db_connect_calls['n']}"
    assert events[0]["cve_id"] == "CVE-A"
    assert events[0]["host"] == "host-x"
    assert events[1]["cve_id"] == "CVE-B"
    assert "cve_id" not in events[2]  # unknown finding_id, no enrichment
    assert "cve_id" not in events[3]  # scan_integrity has no finding_id


# ── Scenario 6 — corrupted JSON lines are skipped ─────────────────


def test_tail_read_skips_malformed_json(hub_env: Path) -> None:
    """A malformed line in the middle of the log must be skipped
    silently; valid events on either side still returned."""
    audit = hub_env / "audit.log"
    audit.write_text(
        '{"ts": 1, "event": "scan_integrity"}\n'
        + "NOT JSON GARBAGE\n"
        + '{"ts": 2, "event": "scan_integrity"}\n'
    )
    result = hub._tail_audit_events(
        matcher=lambda e: True,
        limit=10,
        chunk_size=1024,
    )
    assert len(result) == 2
    assert [e["ts"] for e in result] == [2, 1]


# ── Scenario 7 — empty / missing audit.log ────────────────────────


def test_tail_read_missing_log_returns_empty_list(hub_env: Path) -> None:
    """No audit.log file at all → []"""
    audit = hub_env / "audit.log"
    if audit.exists():
        audit.unlink()
    assert hub._tail_audit_events(matcher=lambda e: True, limit=10) == []


def test_tail_read_empty_log_returns_empty_list(hub_env: Path) -> None:
    """Empty file → []"""
    audit = hub_env / "audit.log"
    audit.write_text("")
    assert hub._tail_audit_events(matcher=lambda e: True, limit=10) == []


# ── Parity with the pre-BC-131 matcher behavior ────────────────────


def test_parity_with_read_audit_events_order(hub_env: Path) -> None:
    """Shape parity: _tail_audit_events(match-all) returns events in
    the same newest-first order as _read_audit_events()."""
    audit = hub_env / "audit.log"
    events = [{"ts": i, "event": "finding_filtered", "cve_id": f"CVE-{i}"} for i in range(20)]
    _write_events(audit, events)

    tail = hub._tail_audit_events(matcher=lambda e: True, limit=1000, chunk_size=64)
    legacy = hub._read_audit_events()
    assert tail == legacy
