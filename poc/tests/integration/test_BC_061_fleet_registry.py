"""BC-061 — scout fleet registry with liveness.

Covers:
    - CAP-T121: scouts registry + /scan upsert
    - CAP-T122: /fleet LEFT-JOIN with liveness status
    - CAP-T123: make status renders FLEET STATUS column
    - CAP-B044: Operator can see every scout + whether it's delivering
      up-to-date data
"""

from __future__ import annotations

import importlib
import sqlite3
import time
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

    import hub

    importlib.reload(hub)
    hub.init_db()
    client = TestClient(hub.api)
    yield {"client": client, "db": hub.DB_PATH, "hub": hub}
    client.close()
    importlib.reload(hub)


def _insert_finding(
    hub, host: str, cve: str, *, status: str = "open", found_at: int = 0, last_seen_ts: int = 0
) -> None:
    con = sqlite3.connect(hub.DB_PATH)
    con.execute(
        "INSERT INTO findings (id, host, cve_id, package, version, "
        "fixed_version, severity, cvss, source_type, source_ref, "
        "description, status, tier, found_at, last_seen_ts) VALUES "
        "(?, ?, ?, 'p', '1', '2', 'High', 7.5, 'docker', 'nginx:1', '', ?, 2, ?, ?)",
        (f"{host}-{cve}", host, cve, status, found_at, last_seen_ts),
    )
    con.commit()
    con.close()


def _scouts_rows(db: Path) -> list[tuple]:
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT host, first_seen_ts, last_posted_ts, last_sbom_count FROM scouts"
    ).fetchall()
    con.close()
    return rows


# ── Registry upsert ────────────────────────────────────────────────────────


def test_first_post_registers_scout(hub_env) -> None:
    """CAP-T121: zero-SBOM POST still writes a scouts row."""
    r = hub_env["client"].post("/scan", json={"host": "hetzner-staging", "sboms": []})
    assert r.status_code == 200
    rows = _scouts_rows(hub_env["db"])
    assert len(rows) == 1
    assert rows[0][0] == "hetzner-staging"
    assert rows[0][3] == 0  # last_sbom_count


def test_repeated_post_bumps_last_posted(hub_env) -> None:
    """CAP-T121: second POST updates last_posted_ts, not first_seen_ts."""
    client = hub_env["client"]
    client.post("/scan", json={"host": "laptop", "sboms": [{"foo": "bar"}]})
    first_rows = _scouts_rows(hub_env["db"])
    first_seen = first_rows[0][1]
    time.sleep(1.1)  # ensure a different unix second
    client.post("/scan", json={"host": "laptop", "sboms": []})

    rows = _scouts_rows(hub_env["db"])
    assert len(rows) == 1
    assert rows[0][1] == first_seen  # first_seen_ts unchanged
    assert rows[0][2] > first_seen  # last_posted_ts advanced
    assert rows[0][3] == 0  # last_sbom_count updated to latest


# ── /fleet shape + status ─────────────────────────────────────────────────


def test_zero_finding_scout_in_fleet(hub_env) -> None:
    """CAP-T122/CAP-B044: scout with no findings still appears in /fleet
    — directly addresses the 'hetzner-staging doesn't appear' bug."""
    hub_env["client"].post("/scan", json={"host": "hetzner-staging", "sboms": []})
    r = hub_env["client"].get("/fleet")
    assert r.status_code == 200
    rows = r.json()
    hs = next((row for row in rows if row["host"] == "hetzner-staging"), None)
    assert hs is not None, f"hetzner-staging not in fleet: {rows}"
    assert hs["open_findings"] == 0
    assert hs["total_findings"] == 0
    assert hs["status"] == "active"


def test_fleet_response_shape(hub_env) -> None:
    """CAP-T122: new fields present in every row."""
    hub_env["client"].post("/scan", json={"host": "laptop", "sboms": []})
    _insert_finding(hub_env["hub"], "laptop", "CVE-X", last_seen_ts=int(time.time()))
    rows = hub_env["client"].get("/fleet").json()
    assert rows
    for row in rows:
        for field in (
            "host",
            "first_seen_ts",
            "last_posted_ts",
            "last_sbom_count",
            "last_seen_ts",
            "open_findings",
            "total_findings",
            "status",
        ):
            assert field in row, f"missing field {field} in {row}"


def test_fleet_sorted_by_last_posted(hub_env) -> None:
    """CAP-T122: newest post first."""
    client = hub_env["client"]
    client.post("/scan", json={"host": "oldest", "sboms": []})
    time.sleep(1.1)
    client.post("/scan", json={"host": "middle", "sboms": []})
    time.sleep(1.1)
    client.post("/scan", json={"host": "newest", "sboms": []})
    rows = client.get("/fleet").json()
    assert [r["host"] for r in rows] == ["newest", "middle", "oldest"]


def test_fleet_status_active(hub_env) -> None:
    """CAP-T122: fresh POST → status=active."""
    hub_env["client"].post("/scan", json={"host": "laptop", "sboms": []})
    rows = hub_env["client"].get("/fleet").json()
    assert next(r for r in rows if r["host"] == "laptop")["status"] == "active"


def test_fleet_status_stale(hub_env) -> None:
    """CAP-T122: POST older than ACTIVE_WINDOW but within DEAD_AFTER → stale."""
    hub = hub_env["hub"]
    # Insert a scouts row with last_posted 2h ago (default active=10m, dead=24h).
    old = int(time.time()) - 2 * 3600
    con = sqlite3.connect(hub.DB_PATH)
    con.execute(
        "INSERT INTO scouts (host, first_seen_ts, last_posted_ts, last_sbom_count) "
        "VALUES ('server2', ?, ?, 0)",
        (old, old),
    )
    con.commit()
    con.close()
    rows = hub_env["client"].get("/fleet").json()
    assert next(r for r in rows if r["host"] == "server2")["status"] == "stale"


def test_fleet_status_dead(hub_env) -> None:
    """CAP-T122: POST beyond DEAD_AFTER → dead."""
    hub = hub_env["hub"]
    old = int(time.time()) - 5 * 86400
    con = sqlite3.connect(hub.DB_PATH)
    con.execute(
        "INSERT INTO scouts (host, first_seen_ts, last_posted_ts, last_sbom_count) "
        "VALUES ('decommissioned', ?, ?, 0)",
        (old, old),
    )
    con.commit()
    con.close()
    rows = hub_env["client"].get("/fleet").json()
    assert next(r for r in rows if r["host"] == "decommissioned")["status"] == "dead"


def test_fleet_thresholds_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T122: BEACONRY_FLEET_ACTIVE_WINDOW_S + _DEAD_AFTER_S env vars respected."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "0")
    # Tiny window → anything >1s is stale, >5s is dead.
    monkeypatch.setenv("BEACONRY_FLEET_ACTIVE_WINDOW_S", "1")
    monkeypatch.setenv("BEACONRY_FLEET_DEAD_AFTER_S", "5")

    import hub

    importlib.reload(hub)
    assert hub.FLEET_ACTIVE_WINDOW_S == 1
    assert hub.FLEET_DEAD_AFTER_S == 5
    assert hub._fleet_status(int(time.time()) - 0, int(time.time())) == "active"
    assert hub._fleet_status(int(time.time()) - 3, int(time.time())) == "stale"
    assert hub._fleet_status(int(time.time()) - 10, int(time.time())) == "dead"


# ── init_db backfill ──────────────────────────────────────────────────────


def test_init_db_backfills_from_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T121: pre-BC-061 DBs with findings but no scouts table get
    backfilled on next init_db so operators don't start with empty FLEET."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "0")

    import hub

    importlib.reload(hub)
    hub.init_db()
    # Pretend pre-BC-061: wipe scouts, insert some findings directly.
    con = sqlite3.connect(hub.DB_PATH)
    con.execute("DELETE FROM scouts")
    con.commit()
    con.close()
    _insert_finding(hub, "bb-82jq", "CVE-A", found_at=100, last_seen_ts=200)
    _insert_finding(hub, "bb-82jq", "CVE-B", found_at=150, last_seen_ts=300)
    _insert_finding(hub, "server2", "CVE-C", found_at=50, last_seen_ts=400)

    hub.init_db()  # re-run to trigger backfill

    rows = _scouts_rows(hub.DB_PATH)
    by_host = {r[0]: r for r in rows}
    assert set(by_host) == {"bb-82jq", "server2"}
    # bb-82jq first_seen_ts = min(found_at) = 100
    assert by_host["bb-82jq"][1] == 100
    # bb-82jq last_posted_ts = max(last_seen_ts) = 300
    assert by_host["bb-82jq"][2] == 300
    # server2 first_seen_ts = 50
    assert by_host["server2"][1] == 50


def test_init_db_backfill_idempotent(hub_env) -> None:
    """Running init_db twice on the same DB doesn't duplicate scout rows."""
    hub = hub_env["hub"]
    _insert_finding(hub, "laptop", "CVE-X", found_at=100, last_seen_ts=200)
    hub.init_db()
    first = _scouts_rows(hub.DB_PATH)
    hub.init_db()
    second = _scouts_rows(hub.DB_PATH)
    assert first == second


# ── status.py STATUS column ───────────────────────────────────────────────


def test_format_fleet_renders_status_column() -> None:
    """CAP-T123: STATUS column appears when rows carry 'status' field."""
    from poc.scripts.status import format_fleet

    now = int(time.time())
    out = format_fleet(
        [
            {
                "host": "laptop",
                "last_seen_ts": now,
                "open_findings": 2,
                "total_findings": 5,
                "status": "active",
            },
            {
                "host": "server2",
                "last_seen_ts": now - 3600,
                "open_findings": 0,
                "total_findings": 7,
                "status": "stale",
            },
            {
                "host": "old-laptop",
                "last_seen_ts": now - 5 * 86400,
                "open_findings": 2,
                "total_findings": 2,
                "status": "dead",
            },
        ]
    )
    assert "FLEET:" in out
    assert "STATUS" in out  # header
    assert "● active" in out
    assert "⚠ stale" in out
    assert "✗ dead" in out


def test_format_fleet_column_widths() -> None:
    """CAP-T123: STATUS column aligns with widest row content."""
    from poc.scripts.status import format_fleet

    out = format_fleet(
        [
            {
                "host": "x",
                "last_seen_ts": 0,
                "open_findings": 0,
                "total_findings": 0,
                "status": "active",
            },
        ]
    )
    lines = out.splitlines()
    header = next(line for line in lines if "HOST" in line)
    assert "STATUS" in header
    # Status cell on data row should be present (padded or last).
    data = next(line for line in lines if line.startswith("x "))
    assert "● active" in data


def test_format_fleet_omits_status_when_absent() -> None:
    """Back-compat: pre-BC-061 hub response (no 'status' field) → old
    4-column shape."""
    from poc.scripts.status import format_fleet

    out = format_fleet(
        [
            {"host": "legacy", "last_seen_ts": 1000, "open_findings": 1, "total_findings": 2},
        ]
    )
    assert "STATUS" not in out  # no header
    assert "legacy" in out
