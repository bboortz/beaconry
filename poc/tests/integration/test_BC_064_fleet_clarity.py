"""BC-064 — FLEET column clarity + stale-heartbeat health degradation.

Covers:
    - CAP-T135: /fleet surfaces last_heartbeat_ts + heartbeat_stale;
      status.py renders LAST HEARTBEAT column
    - CAP-T136: HEALTH cell renders '? unknown' on stale heartbeat
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
    monkeypatch.setenv("SCOUT_HEARTBEAT_INTERVAL_S", "60")
    monkeypatch.delenv("BEACONRY_STRICT_API", raising=False)

    import hub

    importlib.reload(hub)
    hub.init_db()
    client = TestClient(hub.api)
    yield {"client": client, "db": hub.DB_PATH, "hub": hub}
    client.close()
    importlib.reload(hub)


def _insert_scout(
    db: Path,
    host: str,
    *,
    last_posted_ts: int,
    last_health: str | None = "ok",
    last_scan_ts: int | None = None,
) -> None:
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO scouts (host, first_seen_ts, last_posted_ts, last_sbom_count, "
        "software_version, api_version, last_health, last_scan_ts) "
        "VALUES (?, ?, ?, 0, '0.2.1', 1, ?, ?)",
        (host, last_posted_ts, last_posted_ts, last_health, last_scan_ts),
    )
    con.commit()
    con.close()


# ── /fleet response shape ───────────────────────────────────────────────────


def test_fleet_response_carries_last_heartbeat_ts(hub_env) -> None:
    _insert_scout(hub_env["db"], "bb-82jq", last_posted_ts=int(time.time()))
    body = hub_env["client"].get("/fleet").json()
    assert len(body) == 1
    assert "last_heartbeat_ts" in body[0]
    assert body[0]["last_heartbeat_ts"] > 0


def test_fleet_response_carries_heartbeat_stale_bool(hub_env) -> None:
    _insert_scout(hub_env["db"], "bb-82jq", last_posted_ts=int(time.time()))
    body = hub_env["client"].get("/fleet").json()
    assert body[0]["heartbeat_stale"] is False  # fresh


def test_fleet_preserves_legacy_last_seen_ts_field(hub_env) -> None:
    """Back-compat: pre-BC-064 CLIs still reading `last_seen_ts` keep working."""
    _insert_scout(hub_env["db"], "bb-82jq", last_posted_ts=int(time.time()))
    body = hub_env["client"].get("/fleet").json()
    assert "last_seen_ts" in body[0]


# ── heartbeat_stale thresholds ──────────────────────────────────────────────


def test_heartbeat_stale_true_when_old(hub_env) -> None:
    """Default threshold = max(2×60, 300) = 300s. 400s ago → stale."""
    _insert_scout(hub_env["db"], "server2", last_posted_ts=int(time.time()) - 400)
    body = hub_env["client"].get("/fleet").json()
    assert body[0]["heartbeat_stale"] is True


def test_heartbeat_stale_false_when_fresh(hub_env) -> None:
    _insert_scout(hub_env["db"], "bb-82jq", last_posted_ts=int(time.time()) - 30)
    body = hub_env["client"].get("/fleet").json()
    assert body[0]["heartbeat_stale"] is False


def test_heartbeat_stale_tracks_env_knob(hub_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T135: operator who bumps SCOUT_HEARTBEAT_INTERVAL_S to 600s
    gets the threshold proportional (2×600 = 1200s). 400s ago is
    no longer stale under the longer interval."""
    _insert_scout(hub_env["db"], "server2", last_posted_ts=int(time.time()) - 400)
    monkeypatch.setenv("SCOUT_HEARTBEAT_INTERVAL_S", "600")
    body = hub_env["client"].get("/fleet").json()
    assert body[0]["heartbeat_stale"] is False


def test_heartbeat_stale_300s_floor_when_disabled(hub_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T135: SCOUT_HEARTBEAT_INTERVAL_S=0 → threshold floors at 300s,
    not 2×0=0s (which would flap every scout to stale instantly)."""
    _insert_scout(hub_env["db"], "server2", last_posted_ts=int(time.time()) - 60)
    monkeypatch.setenv("SCOUT_HEARTBEAT_INTERVAL_S", "0")
    body = hub_env["client"].get("/fleet").json()
    # 60s ago under a 300s floor is still fresh.
    assert body[0]["heartbeat_stale"] is False


# ── make status rendering ───────────────────────────────────────────────────


def test_status_header_reads_last_heartbeat() -> None:
    from poc.scripts import status

    text = status.format_fleet(
        [
            {
                "host": "x",
                "last_heartbeat_ts": int(time.time()),
                "last_scan_ts": int(time.time()),
                "software_version": "0.2.1",
                "api_version": 1,
                "last_health": "ok",
                "heartbeat_stale": False,
                "open_findings": 0,
                "total_findings": 0,
                "status": "active",
            }
        ],
        hub_api=1,
    )
    header = text.splitlines()[2]
    assert "LAST HEARTBEAT" in header
    assert "LAST SEEN" not in header


def test_status_uses_last_heartbeat_ts_not_last_seen() -> None:
    """CAP-T135: regression against the pre-BC-064 bug where the column
    read findings.last_seen_ts (12m ago) while a scan was 57s ago.
    The column must track last_heartbeat_ts, NOT last_seen_ts."""
    from poc.scripts import status

    now = int(time.time())
    text = status.format_fleet(
        [
            {
                "host": "x",
                # Simulated BC-055 skew: findings-watermark says 12m,
                # but we actually heartbeat'd 30s ago.
                "last_seen_ts": now - 12 * 60,
                "last_heartbeat_ts": now - 30,
                "last_scan_ts": now - 57,
                "software_version": "0.2.1",
                "api_version": 1,
                "last_health": "ok",
                "heartbeat_stale": False,
                "open_findings": 0,
                "total_findings": 0,
                "status": "active",
            }
        ],
        hub_api=1,
    )
    # Rendered heartbeat age uses the 30s value, not the 12m one.
    assert "30s ago" in text or "29s ago" in text
    assert "12m ago" not in text


def test_status_falls_back_to_last_posted_ts() -> None:
    """CAP-T135: BC-064 CLI pointed at pre-BC-064 hub (no last_heartbeat_ts
    key in response) still renders a sensible value via last_posted_ts."""
    from poc.scripts import status

    now = int(time.time())
    text = status.format_fleet(
        [
            {
                "host": "x",
                "last_posted_ts": now - 45,
                "last_scan_ts": now - 45,
                "software_version": "0.2.1",
                "api_version": 1,
                "last_health": "ok",
                "open_findings": 0,
                "total_findings": 0,
                "status": "active",
            }
        ],
        hub_api=1,
    )
    assert "45s ago" in text or "44s ago" in text


def test_stale_heartbeat_renders_unknown_health() -> None:
    """CAP-T136: a cached 'ok' label is dropped once the heartbeat is stale."""
    from poc.scripts import status

    text = status.format_fleet(
        [
            {
                "host": "server2",
                "last_heartbeat_ts": int(time.time()) - 3600,
                "last_scan_ts": None,
                "software_version": "0.2.0",
                "api_version": 1,
                "last_health": "ok",  # cached — must be ignored
                "heartbeat_stale": True,
                "open_findings": 0,
                "total_findings": 5,
                "status": "stale",
            }
        ],
        hub_api=1,
    )
    assert "? unknown" in text
    assert "● healthy" not in text


def test_fresh_heartbeat_renders_reported_health() -> None:
    """CAP-T136: a scout within the window still gets its reported value."""
    from poc.scripts import status

    text = status.format_fleet(
        [
            {
                "host": "bb-82jq",
                "last_heartbeat_ts": int(time.time()) - 30,
                "last_scan_ts": int(time.time()) - 60,
                "software_version": "0.2.1",
                "api_version": 1,
                "last_health": "degraded",
                "heartbeat_stale": False,
                "open_findings": 2,
                "total_findings": 5,
                "status": "active",
            }
        ],
        hub_api=1,
    )
    assert "⚠ degraded" in text
    assert "? unknown" not in text
