"""BC-063 — fleet health visibility (version + health + last-scan per scout).

Covers:
    - CAP-T129: Scout POSTs software_version + api_version + health on /scan
    - CAP-T130: Scouts table schema v3 — per-scout metadata columns
    - CAP-T131: /fleet surfaces per-scout version + health + last_scan
    - CAP-T132: make status FLEET renders VERSION + HEALTH + LAST SCAN
    - CAP-T134: Scout heartbeat endpoint + bump_scan_ts=False path
    - CAP-B046: Operator can diagnose a stale/degraded scout from
      `make status` alone
"""

from __future__ import annotations

import importlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "0")
    monkeypatch.delenv("BEACONRY_STRICT_API", raising=False)

    import hub

    importlib.reload(hub)
    hub.init_db()
    client = TestClient(hub.api)
    yield {"client": client, "db": hub.DB_PATH, "hub": hub}
    client.close()
    importlib.reload(hub)


# ── Schema version ──────────────────────────────────────────────────────────


def test_expected_schema_version_at_least_3(hub_env) -> None:
    # BC-063 shipped at v3; later BCs bump the version while the BC-063
    # scouts columns must remain present. Guard the floor, not the point.
    assert hub_env["hub"].EXPECTED_SCHEMA_VERSION >= 3


def test_fresh_db_v3_has_new_scouts_columns(hub_env) -> None:
    con = sqlite3.connect(hub_env["db"])
    cols = {row[1] for row in con.execute("PRAGMA table_info(scouts)").fetchall()}
    con.close()
    assert {"software_version", "api_version", "last_health", "last_scan_ts"} <= cols


def test_schema_v2_to_v3_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T130: a v2 DB (BC-061 shape) migrates up idempotently."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "beaconry.db"
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE scouts (
            host TEXT PRIMARY KEY, first_seen_ts INTEGER NOT NULL,
            last_posted_ts INTEGER NOT NULL,
            last_sbom_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    con.execute(
        "INSERT INTO scouts (host, first_seen_ts, last_posted_ts, last_sbom_count) "
        "VALUES ('legacy-host', 100, 200, 5)"
    )
    con.execute("PRAGMA user_version = 2")
    con.commit()
    con.close()

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "0")
    import hub

    importlib.reload(hub)
    hub.init_db()

    con = sqlite3.connect(db_path)
    cols = {row[1] for row in con.execute("PRAGMA table_info(scouts)").fetchall()}
    (ver,) = con.execute("PRAGMA user_version").fetchone()
    row = con.execute(
        "SELECT host, last_sbom_count, software_version, api_version, "
        "last_health, last_scan_ts FROM scouts WHERE host = 'legacy-host'"
    ).fetchone()
    con.close()
    # BC-063 required ≥ 3. Later BCs bump schema; BC-063's columns + data
    # must still be present after migration.
    assert ver >= 3
    assert {"software_version", "api_version", "last_health", "last_scan_ts"} <= cols
    assert row == ("legacy-host", 5, None, None, None, None)
    importlib.reload(hub)


def test_init_db_v3_idempotent(hub_env) -> None:
    """CAP-T130: re-running init_db on an already-v3+ DB is a no-op."""
    hub_env["hub"].init_db()
    hub_env["hub"].init_db()
    con = sqlite3.connect(hub_env["db"])
    (ver,) = con.execute("PRAGMA user_version").fetchone()
    cols = [row[1] for row in con.execute("PRAGMA table_info(scouts)").fetchall()]
    con.close()
    assert ver >= 3
    # No duplicate columns from repeated ALTER TABLE runs.
    assert len(cols) == len(set(cols))


# ── Scout /scan carries BC-063 metadata ────────────────────────────────────


def test_scan_payload_persists_scout_metadata(hub_env) -> None:
    """CAP-T131 + CAP-T129 round-trip: scout POST → scouts row has fields."""
    r = hub_env["client"].post(
        "/scan",
        json={
            "host": "bb-82jq",
            "software_version": "0.2.0",
            "api_version": 1,
            "health": "ok",
            "sboms": [],
        },
    )
    assert r.status_code == 200
    con = sqlite3.connect(hub_env["db"])
    row = con.execute(
        "SELECT software_version, api_version, last_health, "
        "last_scan_ts IS NOT NULL FROM scouts WHERE host = 'bb-82jq'"
    ).fetchone()
    con.close()
    assert row == ("0.2.0", 1, "ok", 1)


def test_legacy_scan_payload_persists_nulls(hub_env) -> None:
    """Pre-BC-063 scout (no version/health in payload) → NULLs."""
    r = hub_env["client"].post("/scan", json={"host": "ancient-host", "sboms": []})
    assert r.status_code == 200
    con = sqlite3.connect(hub_env["db"])
    row = con.execute(
        "SELECT software_version, api_version, last_health FROM scouts WHERE host = 'ancient-host'"
    ).fetchone()
    con.close()
    assert row == (None, None, None)


def test_fleet_returns_scout_metadata(hub_env) -> None:
    """CAP-T131: /fleet response carries the new fields."""
    hub_env["client"].post(
        "/scan",
        json={
            "host": "server2",
            "software_version": "0.1.9",
            "api_version": 1,
            "health": "degraded",
            "sboms": [],
        },
    )
    body = hub_env["client"].get("/fleet").json()
    assert len(body) == 1
    row = body[0]
    assert row["host"] == "server2"
    assert row["software_version"] == "0.1.9"
    assert row["api_version"] == 1
    assert row["last_health"] == "degraded"
    assert isinstance(row["last_scan_ts"], int) and row["last_scan_ts"] > 0


# ── /heartbeat endpoint ─────────────────────────────────────────────────────


def test_heartbeat_updates_metadata_without_bumping_scan_ts(hub_env) -> None:
    """CAP-T134: heartbeat refreshes version/health + last_posted_ts
    but leaves last_scan_ts untouched."""
    import time as _time

    hub_env["client"].post(
        "/scan",
        json={
            "host": "bb-82jq",
            "software_version": "0.2.0",
            "api_version": 1,
            "health": "ok",
            "sboms": [],
        },
    )
    con = sqlite3.connect(hub_env["db"])
    (scan_ts_before, posted_before) = con.execute(
        "SELECT last_scan_ts, last_posted_ts FROM scouts WHERE host = 'bb-82jq'"
    ).fetchone()
    con.close()
    _time.sleep(1.1)

    r = hub_env["client"].post(
        "/heartbeat",
        json={
            "host": "bb-82jq",
            "software_version": "0.2.0",
            "api_version": 1,
            "health": "degraded",
        },
    )
    assert r.status_code == 200
    con = sqlite3.connect(hub_env["db"])
    (scan_ts_after, posted_after, health_after) = con.execute(
        "SELECT last_scan_ts, last_posted_ts, last_health FROM scouts WHERE host = 'bb-82jq'"
    ).fetchone()
    con.close()
    assert scan_ts_after == scan_ts_before  # untouched
    assert posted_after > posted_before  # bumped
    assert health_after == "degraded"  # updated


def test_heartbeat_from_unseen_scout_registers_it(hub_env) -> None:
    """A heartbeat from a scout the hub has never seen still registers
    the row — operator sees it in FLEET immediately."""
    r = hub_env["client"].post(
        "/heartbeat",
        json={
            "host": "freshly-booted",
            "software_version": "0.2.0",
            "api_version": 1,
            "health": "ok",
        },
    )
    assert r.status_code == 200
    body = hub_env["client"].get("/fleet").json()
    hosts = {row["host"] for row in body}
    assert "freshly-booted" in hosts
    row = next(r for r in body if r["host"] == "freshly-booted")
    assert row["last_scan_ts"] is None  # never scanned, just heartbeat
    assert row["software_version"] == "0.2.0"
    assert row["last_health"] == "ok"


# ── make status FLEET rendering ─────────────────────────────────────────────


def test_make_status_renders_new_columns() -> None:
    """CAP-T132: VERSION / HEALTH / LAST SCAN columns appear."""
    from poc.scripts import status

    rows = [
        {
            "host": "bb-82jq",
            "last_seen_ts": 1762099240,
            "last_scan_ts": 1762099240,
            "software_version": "0.2.0",
            "api_version": 1,
            "last_health": "ok",
            "open_findings": 1,
            "total_findings": 3,
            "status": "active",
        }
    ]
    text = status.format_fleet(rows, hub_api=1)
    header = text.splitlines()[2]
    assert "VERSION" in header
    assert "HEALTH" in header
    assert "LAST SCAN" in header
    assert "0.2.0 · api1" in text
    assert "● healthy" in text


def test_make_status_renders_api_mismatch_glyph() -> None:
    """CAP-T132: hub serves api2, scout serves api1 → trailing ⚠ on the row."""
    from poc.scripts import status

    rows = [
        {
            "host": "legacy-scout",
            "last_seen_ts": 1,
            "last_scan_ts": 1,
            "software_version": "0.1.0",
            "api_version": 1,
            "last_health": "ok",
            "open_findings": 0,
            "total_findings": 0,
            "status": "active",
        }
    ]
    text = status.format_fleet(rows, hub_api=2)
    assert "api1 ⚠" in text


def test_make_status_renders_degraded_health() -> None:
    from poc.scripts import status

    rows = [
        {
            "host": "server2",
            "last_seen_ts": 1,
            "last_scan_ts": 1,
            "software_version": "0.1.0",
            "api_version": 1,
            "last_health": "degraded",
            "open_findings": 0,
            "total_findings": 0,
            "status": "active",
        }
    ]
    text = status.format_fleet(rows, hub_api=1)
    assert "⚠ degraded" in text


def test_make_status_renders_unknown_for_legacy_row() -> None:
    """CAP-T132: pre-BC-063 scout row (no health, no software_version)
    renders '? unknown' and '-' — not a crash."""
    from poc.scripts import status

    rows = [
        {
            "host": "ancient",
            "last_seen_ts": 1,
            "last_scan_ts": None,
            "software_version": None,
            "api_version": None,
            "last_health": None,
            "open_findings": 0,
            "total_findings": 0,
            "status": "stale",
        },
        # One modern row so has_bc063 triggers the extended columns.
        {
            "host": "modern",
            "last_seen_ts": 2,
            "last_scan_ts": 2,
            "software_version": "0.2.0",
            "api_version": 1,
            "last_health": "ok",
            "open_findings": 0,
            "total_findings": 0,
            "status": "active",
        },
    ]
    text = status.format_fleet(rows, hub_api=1)
    assert "? unknown" in text


def test_make_status_pre_bc063_response_degrades_gracefully() -> None:
    """A hub that doesn't report any BC-063 fields → no new columns."""
    from poc.scripts import status

    rows = [
        {
            "host": "x",
            "last_seen_ts": 1,
            "open_findings": 0,
            "total_findings": 0,
            "status": "active",
        }
    ]
    text = status.format_fleet(rows, hub_api=1)
    header = text.splitlines()[2]
    assert "VERSION" not in header
    assert "HEALTH" not in header


# ── /healthz schema version is tracked ─────────────────────────────────────


def test_healthz_version_schema_matches_expected(hub_env) -> None:
    body = hub_env["client"].get("/healthz").json()
    assert body["version"]["schema"] == hub_env["hub"].EXPECTED_SCHEMA_VERSION


# ── audit event for heartbeat ──────────────────────────────────────────────


def test_heartbeat_emits_audit_event(hub_env) -> None:
    """A heartbeat is auditable: operator should be able to see the
    keepalive stream in `make audit` — provided the scout opts in to
    metrics (BC-075 privacy-first gate)."""
    r = hub_env["client"].post(
        "/heartbeat",
        json={
            "host": "bb-82jq",
            "software_version": "0.2.0",
            "api_version": 1,
            "health": "ok",
        },
        headers={"X-Beaconry-Metrics-Opt-In": "1"},
    )
    assert r.status_code == 200
    # Audit JSONL lives in the DATA_DIR.
    import json

    audit_lines = (hub_env["db"].parent / "audit.log").read_text().splitlines()
    events = [json.loads(line) for line in audit_lines]
    hb_events = [e for e in events if e.get("event") == "heartbeat_received"]
    assert len(hb_events) == 1
    assert hb_events[0]["host"] == "bb-82jq"
    assert hb_events[0]["health"] == "ok"
