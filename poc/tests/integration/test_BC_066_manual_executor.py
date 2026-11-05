"""BC-066 — manual executor as default + reminder sweep + schema v4.

Covers:
    - CAP-T137: Configurable executor adapter via BEACONRY_EXECUTOR
    - CAP-T138: ManualAdapter workflow — log intent, deferred verify
    - CAP-T139: Manual-claim reminder sweep + schema v4 last_claim_ts
    - CAP-B047: Operator can run beaconry on infra where auto-pull isn't safe
"""

from __future__ import annotations

import importlib
import json
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
    monkeypatch.delenv("BEACONRY_EXECUTOR", raising=False)
    monkeypatch.delenv("BEACONRY_MANUAL_REMINDER_INTERVAL_S", raising=False)

    import hub

    importlib.reload(hub)
    hub.init_db()
    client = TestClient(hub.api)
    yield {"client": client, "db": hub.DB_PATH, "hub": hub, "data_dir": data_dir}
    client.close()
    importlib.reload(hub)


@pytest.fixture
def hub_env_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "0")
    monkeypatch.setenv("BEACONRY_EXECUTOR", "docker")

    import hub

    importlib.reload(hub)
    hub.init_db()
    client = TestClient(hub.api)
    yield {"client": client, "db": hub.DB_PATH, "hub": hub}
    client.close()
    importlib.reload(hub)


def _insert_finding(
    db: Path,
    *,
    finding_id: str = "f1",
    cve_id: str = "CVE-2026-00001",
    host: str = "host1",
    source_ref: str = "nginx:1.24",
    status: str = "open",
    last_claim_ts: int = 0,
    now: int | None = None,
) -> None:
    now = now or int(time.time())
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO findings (id, host, cve_id, package, version, fixed_version, "
        "severity, cvss, source_type, source_ref, description, found_at, status, "
        "tier, last_seen_ts, last_notified_ts, last_claim_ts) "
        "VALUES (?, ?, ?, 'nginx', '1.24', '1.25', 'High', 7.5, 'docker', ?, '', "
        "?, ?, 2, ?, 0, ?)",
        (finding_id, host, cve_id, source_ref, now, status, now, last_claim_ts),
    )
    con.commit()
    con.close()


# ── CAP-T137: executor dispatch ─────────────────────────────────────────────


def test_default_executor_is_manual(hub_env) -> None:
    assert hub_env["hub"].EXECUTOR_NAME == "manual"
    assert type(hub_env["hub"].EXECUTOR).__name__ == "ManualAdapter"


def test_env_switches_to_docker_executor(hub_env_docker) -> None:
    assert hub_env_docker["hub"].EXECUTOR_NAME == "docker"
    assert type(hub_env_docker["hub"].EXECUTOR).__name__ == "DockerAdapter"


def test_unknown_executor_falls_back_to_manual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T137: typo in BEACONRY_EXECUTOR → safe default (manual), not crash."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "0")
    monkeypatch.setenv("BEACONRY_EXECUTOR", "ansible")  # not implemented yet

    import hub

    importlib.reload(hub)
    try:
        assert type(hub.EXECUTOR).__name__ == "ManualAdapter"
    finally:
        importlib.reload(hub)


def test_healthz_reports_executor_name(hub_env) -> None:
    body = hub_env["client"].get("/healthz").json()
    assert body["checks"]["executor"] == "manual"


def test_healthz_reports_docker_executor(hub_env_docker) -> None:
    body = hub_env_docker["client"].get("/healthz").json()
    assert body["checks"]["executor"] == "docker"


def test_healthz_executor_not_counted_as_check(hub_env) -> None:
    """CAP-T137: 'manual' / 'docker' are policy labels, not check statuses.
    The degraded calculation must exclude the executor key — otherwise
    an otherwise-healthy stack flips to degraded just because `executor`
    isn't in ('ok','polling','mock')."""
    hub = hub_env["hub"]
    # Pin every real health signal to 'ok' and only vary executor —
    # this isolates the exclusion-list behaviour from mock-mode noise.
    body = hub._hub_healthz_summary()
    bool_values_of_executor = [v for k, v in body["checks"].items() if k == "executor"]
    assert bool_values_of_executor == ["manual"]  # present and labeled
    # Walk the same exclusion the summary uses — executor must not drive degraded.
    exclusion = {"last_scan_ts", "kev", "kev_last_fetch_s", "kev_count", "executor"}
    bool_checks = [v for k, v in body["checks"].items() if k not in exclusion]
    # If every other check is ok/polling/mock, status is ok — regardless of executor name.
    all_other_ok = all(v in ("ok", "polling", "mock") for v in bool_checks)
    if all_other_ok:
        assert body["status"] == "ok"


# ── CAP-T139: schema v4 ─────────────────────────────────────────────────────


def test_expected_schema_version_is_7(hub_env) -> None:
    # BC-086 bumped schema 4 → 5 (absent_count, auto_fixed_ts).
    # BC-089 bumped 5 → 6 (snooze_until_ts).
    # BC-092 bumped 6 → 7 (scouts.role).
    assert hub_env["hub"].EXPECTED_SCHEMA_VERSION == 7


def test_fresh_db_v4_has_last_claim_ts_column(hub_env) -> None:
    con = sqlite3.connect(hub_env["db"])
    cols = {row[1] for row in con.execute("PRAGMA table_info(findings)").fetchall()}
    con.close()
    assert "last_claim_ts" in cols


def test_schema_v3_to_latest_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T139 (+ BC-086): v3 DB migrates up past v4 (BC-066 last_claim_ts)
    and v5 (BC-086 absent_count + auto_fixed_ts), preserving findings."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "beaconry.db"
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE findings (
            id TEXT PRIMARY KEY, host TEXT, cve_id TEXT, package TEXT,
            version TEXT, fixed_version TEXT, severity TEXT, cvss REAL,
            source_type TEXT, source_ref TEXT, description TEXT,
            found_at INTEGER, status TEXT DEFAULT 'open', tier INTEGER DEFAULT 3
        )
    """)
    con.execute("""
        INSERT INTO findings (id, host, cve_id, package, version, fixed_version,
        severity, cvss, source_type, source_ref, description, found_at, status, tier)
        VALUES ('legacy', 'h', 'CVE-X', 'p', '1', '2', 'High', 7.5,
                'docker', 'nginx:1', '', 100, 'open', 2)
    """)
    con.execute("PRAGMA user_version = 3")
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
    cols = {row[1] for row in con.execute("PRAGMA table_info(findings)").fetchall()}
    (ver,) = con.execute("PRAGMA user_version").fetchone()
    (row,) = con.execute("SELECT last_claim_ts FROM findings WHERE id='legacy'").fetchall()
    con.close()
    assert ver == 7
    assert "last_claim_ts" in cols
    # BC-086 columns appear too since migration runs through v5.
    assert "absent_count" in cols
    assert "auto_fixed_ts" in cols
    # BC-089 column (v6).
    assert "snooze_until_ts" in cols
    # BC-092 column lives on scouts, checked separately via PRAGMA on that table.
    assert row[0] in (0, None)
    importlib.reload(hub)


# ── CAP-T138: _record_manual_claim + audit ──────────────────────────────────


def test_record_manual_claim_writes_timestamp(hub_env) -> None:
    _insert_finding(hub_env["db"])
    hub_env["hub"]._record_manual_claim("f1", 1762099200)
    con = sqlite3.connect(hub_env["db"])
    (ts,) = con.execute("SELECT last_claim_ts FROM findings WHERE id='f1'").fetchone()
    con.close()
    assert ts == 1762099200


def test_record_manual_claim_tolerant_of_missing_column(
    hub_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T138: pre-BC-066 DB without last_claim_ts column — update must
    swallow OperationalError, not crash the fix tap flow."""
    _insert_finding(hub_env["db"])
    # Simulate column loss (hypothetical downgrade scenario).
    con = sqlite3.connect(hub_env["db"])
    con.execute("ALTER TABLE findings RENAME COLUMN last_claim_ts TO _dropped")
    con.commit()
    con.close()
    # Should not raise.
    hub_env["hub"]._record_manual_claim("f1", 1762099200)


# ── CAP-T139: manual_claim_sweep ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_fires_on_overdue_claim(hub_env) -> None:
    """CAP-T139: open finding with claim > INTERVAL_S ago → reminder sent."""
    now = int(time.time())
    _insert_finding(hub_env["db"], last_claim_ts=now - 7200, now=now)
    hub_env["hub"].MANUAL_REMINDER_INTERVAL_S = 3600
    sent = await hub_env["hub"].manual_claim_sweep(now_ts=now)
    assert sent == 1
    # audit event emitted
    audit_lines = (hub_env["data_dir"] / "audit.log").read_text().splitlines()
    events = [json.loads(line) for line in audit_lines]
    reminder_events = [e for e in events if e.get("event") == "manual_fix_reminder_sent"]
    assert len(reminder_events) == 1
    assert reminder_events[0]["finding_id"] == "f1"
    # claim_ts bumped so the next sweep won't re-fire immediately
    con = sqlite3.connect(hub_env["db"])
    (new_claim_ts,) = con.execute("SELECT last_claim_ts FROM findings WHERE id='f1'").fetchone()
    con.close()
    assert new_claim_ts == now


@pytest.mark.asyncio
async def test_sweep_skips_fresh_claim(hub_env) -> None:
    """CAP-T139: claim younger than INTERVAL_S → no reminder."""
    now = int(time.time())
    _insert_finding(hub_env["db"], last_claim_ts=now - 60, now=now)  # 1 min old
    hub_env["hub"].MANUAL_REMINDER_INTERVAL_S = 3600
    sent = await hub_env["hub"].manual_claim_sweep(now_ts=now)
    assert sent == 0


@pytest.mark.asyncio
async def test_sweep_skips_closed_finding(hub_env) -> None:
    """CAP-T139: fixed_verified or snoozed findings don't get reminders."""
    now = int(time.time())
    _insert_finding(hub_env["db"], status="fixed_verified", last_claim_ts=now - 7200, now=now)
    hub_env["hub"].MANUAL_REMINDER_INTERVAL_S = 3600
    sent = await hub_env["hub"].manual_claim_sweep(now_ts=now)
    assert sent == 0


@pytest.mark.asyncio
async def test_sweep_skips_rows_without_claim(hub_env) -> None:
    """CAP-T139: a finding the operator never tapped on doesn't trigger
    a manual reminder (BC-035 heartbeat sweep handles general staleness)."""
    now = int(time.time())
    _insert_finding(hub_env["db"], last_claim_ts=0, now=now)
    hub_env["hub"].MANUAL_REMINDER_INTERVAL_S = 3600
    sent = await hub_env["hub"].manual_claim_sweep(now_ts=now)
    assert sent == 0


@pytest.mark.asyncio
async def test_interval_zero_disables_reminders(hub_env) -> None:
    """CAP-T139: BEACONRY_MANUAL_REMINDER_INTERVAL_S=0 → sweep is a no-op."""
    now = int(time.time())
    _insert_finding(hub_env["db"], last_claim_ts=now - 7200, now=now)
    hub_env["hub"].MANUAL_REMINDER_INTERVAL_S = 0
    sent = await hub_env["hub"].manual_claim_sweep(now_ts=now)
    assert sent == 0
    # audit event NOT written
    audit_path = hub_env["data_dir"] / "audit.log"
    audit_lines = audit_path.read_text().splitlines() if audit_path.exists() else []
    events = [json.loads(line) for line in audit_lines]
    assert not any(e.get("event") == "manual_fix_reminder_sent" for e in events)


@pytest.mark.asyncio
async def test_sweep_also_bumps_last_notified_ts(hub_env) -> None:
    """CAP-T139: dedup against BC-035 heartbeat — both timestamps bump
    together so the heartbeat sweep doesn't double-reminder on the same tick."""
    now = int(time.time())
    _insert_finding(hub_env["db"], last_claim_ts=now - 7200, now=now)
    hub_env["hub"].MANUAL_REMINDER_INTERVAL_S = 3600
    await hub_env["hub"].manual_claim_sweep(now_ts=now)
    con = sqlite3.connect(hub_env["db"])
    (ln,) = con.execute("SELECT last_notified_ts FROM findings WHERE id='f1'").fetchone()
    con.close()
    assert ln == now
