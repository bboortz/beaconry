"""BC-035 — heartbeat sweep for stale open findings.

Covers:
    - CAP-T079: Heartbeat sweep emits reminders for findings sitting at
      status=open past BEACONRY_HEARTBEAT_INTERVAL_S, respecting enable/disable
      flags and skipping snoozed/accepted/fixed/absent-from-inventory rows.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import hub
import pytest

from poc.hub import notifiers
from poc.hub.executor.base import Finding
from poc.hub.notifiers.base import NotificationAdapter


class _StubAdapter(NotificationAdapter):
    """Minimal shim with just the heartbeat method the sweep needs."""

    name = "stub"
    env_var = ""

    def __init__(self) -> None:
        self.heartbeats: list[tuple[str, int]] = []

    async def send_finding(self, finding: Finding) -> None: ...
    async def send_regression(self, finding: Finding, last_fixed_ts: int) -> None: ...
    async def send_digest(self, suppressed: list[Finding]) -> None: ...

    async def send_heartbeat(self, finding: Finding, open_since_ts: int) -> None:
        self.heartbeats.append((finding.id, open_since_ts))


@pytest.fixture
def sweep_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_StubAdapter]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setattr(hub, "HEARTBEAT_ENABLED", True)
    monkeypatch.setattr(hub, "HEARTBEAT_INTERVAL_S", 7 * 86400)
    monkeypatch.setattr(hub, "HEARTBEAT_LIVENESS_WINDOW_S", 7 * 86400)

    hub.init_db()
    notifiers.reset_registry_for_tests()
    adapter = _StubAdapter()
    notifiers.register(adapter)
    yield adapter
    notifiers.reset_registry_for_tests()


def _finding(**overrides: object) -> Finding:
    kwargs: dict[str, object] = {
        "id": "deadbeef12345678",
        "host": "homelab",
        "cve_id": "CVE-X",
        "package": "p",
        "version": "1",
        "fixed_version": "2",
        "severity": "High",
        "cvss": 8.0,
        "source_type": "docker",
        "source_ref": "nginx:1.24",
        "description": "d",
    }
    kwargs.update(overrides)
    return Finding(**kwargs)  # type: ignore[arg-type]


def _set_times(
    db: Path, fid: str, *, last_seen_ts: int, last_notified_ts: int, found_at: int = 0
) -> None:
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE findings SET last_seen_ts=?, last_notified_ts=?, found_at=COALESCE(NULLIF(?,0), found_at) WHERE id=?",
        (last_seen_ts, last_notified_ts, found_at, fid),
    )
    con.commit()
    con.close()


async def test_stale_open_finding_gets_reminded(sweep_env: _StubAdapter) -> None:
    f = _finding(id="stale_01")
    hub.save_finding(f)
    now = int(time.time())
    # Last seen recently (still in inventory); last-notified 10 days ago (> 7d interval).
    _set_times(hub.DB_PATH, "stale_01", last_seen_ts=now, last_notified_ts=now - 10 * 86400)

    sent = await hub.heartbeat_sweep()

    assert sent == 1
    assert len(sweep_env.heartbeats) == 1
    fid, open_since = sweep_env.heartbeats[0]
    assert fid == "stale_01"
    # open_since is read from found_at — the first-ingestion timestamp.
    assert open_since > 0


async def test_recently_notified_finding_skipped(sweep_env: _StubAdapter) -> None:
    """Last notified 2 days ago — within 7d interval → no re-poke."""
    f = _finding(id="recent_01")
    hub.save_finding(f)
    now = int(time.time())
    _set_times(hub.DB_PATH, "recent_01", last_seen_ts=now, last_notified_ts=now - 2 * 86400)

    sent = await hub.heartbeat_sweep()

    assert sent == 0
    assert sweep_env.heartbeats == []


async def test_snoozed_finding_not_reminded(sweep_env: _StubAdapter) -> None:
    f = _finding(id="snoozed_01")
    hub.save_finding(f)
    hub.set_status("snoozed_01", "snoozed")
    now = int(time.time())
    _set_times(hub.DB_PATH, "snoozed_01", last_seen_ts=now, last_notified_ts=now - 10 * 86400)

    sent = await hub.heartbeat_sweep()
    assert sent == 0


async def test_accepted_finding_not_reminded(sweep_env: _StubAdapter) -> None:
    f = _finding(id="accepted_01")
    hub.save_finding(f)
    hub.set_status("accepted_01", "accepted")
    now = int(time.time())
    _set_times(hub.DB_PATH, "accepted_01", last_seen_ts=now, last_notified_ts=now - 10 * 86400)

    sent = await hub.heartbeat_sweep()
    assert sent == 0


async def test_fixed_finding_not_reminded(sweep_env: _StubAdapter) -> None:
    f = _finding(id="fixed_01")
    hub.save_finding(f)
    hub.set_status("fixed_01", "fixed")

    sent = await hub.heartbeat_sweep()
    assert sent == 0


async def test_absent_from_inventory_not_reminded(sweep_env: _StubAdapter) -> None:
    """last_seen older than liveness window → scout no longer sees it →
    BC-031 reconcile will auto-close soon; no reminder."""
    f = _finding(id="absent_01")
    hub.save_finding(f)
    now = int(time.time())
    _set_times(hub.DB_PATH, "absent_01", last_seen_ts=now - 30 * 86400, last_notified_ts=0)

    sent = await hub.heartbeat_sweep()
    assert sent == 0


async def test_heartbeat_disabled_is_a_noop(
    sweep_env: _StubAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BEACONRY_HEARTBEAT_ENABLED=0 → sweep is a no-op even with stale findings."""
    f = _finding(id="disabled_01")
    hub.save_finding(f)
    now = int(time.time())
    _set_times(hub.DB_PATH, "disabled_01", last_seen_ts=now, last_notified_ts=now - 30 * 86400)
    monkeypatch.setattr(hub, "HEARTBEAT_ENABLED", False)

    sent = await hub.heartbeat_sweep()
    assert sent == 0
    assert sweep_env.heartbeats == []


async def test_sweep_updates_last_notified_ts(sweep_env: _StubAdapter) -> None:
    """After reminding, the row's last_notified_ts advances → next sweep
    within the interval is a no-op."""
    f = _finding(id="updates_01")
    hub.save_finding(f)
    now = int(time.time())
    _set_times(hub.DB_PATH, "updates_01", last_seen_ts=now, last_notified_ts=now - 30 * 86400)

    sent1 = await hub.heartbeat_sweep()
    assert sent1 == 1
    sent2 = await hub.heartbeat_sweep()
    assert sent2 == 0, "second sweep within interval must not re-remind"


async def test_sweep_audit_event_emitted(sweep_env: _StubAdapter) -> None:
    """BC-029 integration: each reminder generates one audit event."""
    f = _finding(id="audit_01", cve_id="CVE-HEARTBEAT-01")
    hub.save_finding(f)
    now = int(time.time())
    _set_times(hub.DB_PATH, "audit_01", last_seen_ts=now, last_notified_ts=now - 10 * 86400)

    await hub.heartbeat_sweep()

    import json

    audit_path = hub.DATA_DIR / "audit.log"
    events = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    heartbeat_events = [e for e in events if e["event"] == "heartbeat_reminder_sent"]
    assert len(heartbeat_events) == 1
    assert heartbeat_events[0]["cve_id"] == "CVE-HEARTBEAT-01"
    assert heartbeat_events[0]["finding_id"] == "audit_01"
    assert "days_open" in heartbeat_events[0]


async def test_sweep_tolerates_missing_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold-boot before init_db — no crash, returns 0."""
    monkeypatch.setattr(hub, "DB_PATH", tmp_path / "cold.db")
    monkeypatch.setattr(hub, "DATA_DIR", tmp_path)
    monkeypatch.setattr(hub, "HEARTBEAT_ENABLED", True)
    sent = await hub.heartbeat_sweep()
    assert sent == 0


# ── BC-035 breaking-change-in-planning: last_notified_ts migration + backfill ──


def test_init_db_adds_last_notified_ts_column(sweep_env: _StubAdapter) -> None:
    """Fresh DB has the column; `ALTER TABLE ADD COLUMN` migration is reachable."""
    con = sqlite3.connect(hub.DB_PATH)
    cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
    con.close()
    assert "last_notified_ts" in cols


def test_init_db_last_notified_migration_is_idempotent(sweep_env: _StubAdapter) -> None:
    """Re-running init_db must not raise on an already-migrated DB."""
    hub.init_db()
    hub.init_db()
    con = sqlite3.connect(hub.DB_PATH)
    cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
    con.close()
    assert "last_notified_ts" in cols


def test_legacy_rows_backfill_last_notified_from_found_at(sweep_env: _StubAdapter) -> None:
    """BC-035 upgrade-day safety: pre-BC-035 rows have last_notified_ts=0 →
    without backfill the first sweep after upgrade would re-poke every open
    finding at once. init_db backfills from `found_at` so the sweep stays quiet."""
    found_at = int(time.time()) - 3 * 86400  # 3 days ago
    con = sqlite3.connect(hub.DB_PATH)
    con.execute(
        "INSERT INTO findings (id, host, cve_id, package, version, fixed_version, "
        "severity, cvss, source_type, source_ref, description, found_at, status, "
        "tier, kev, last_fixed_ts, last_seen_ts, last_notified_ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "legacy01",
            "homelab",
            "CVE-LEGACY",
            "p",
            "1",
            "2",
            "High",
            8.0,
            "docker",
            "nginx:1.24",
            "d",
            found_at,
            "open",
            3,
            0,
            0,
            found_at,
            0,  # legacy: last_notified_ts was 0 before BC-035
        ),
    )
    con.commit()
    con.close()

    hub.init_db()  # idempotent run triggers the backfill

    con = sqlite3.connect(hub.DB_PATH)
    row = con.execute("SELECT last_notified_ts FROM findings WHERE id=?", ("legacy01",)).fetchone()
    con.close()
    assert row[0] == found_at


# ── BC-035 format_heartbeat rendering parity with format_notification ──


def test_format_heartbeat_contains_core_elements() -> None:
    finding = _finding(id="fmt01", cve_id="CVE-FMT-1", severity="High", host="h1")
    open_since = int(time.time()) - 10 * 86400
    body = hub.format_heartbeat(finding, open_since)
    assert "OPEN SINCE" in body
    assert "CVE-FMT-1" in body
    assert "h1" in body
    assert "⏰" in body
    # Days-open label grammar: "10 days" (plural)
    assert "10 day" in body


def test_format_heartbeat_kev_badge_only_when_tier_zero() -> None:
    low = _finding(id="k0", tier=3, kev=False)
    assert "KEV — EXPLOITED" not in hub.format_heartbeat(low, int(time.time()) - 86400)
    high = _finding(id="k1", tier=0, kev=True)
    assert "KEV — EXPLOITED" in hub.format_heartbeat(high, int(time.time()) - 86400)


def test_format_heartbeat_handles_zero_open_since() -> None:
    """Missing/zero `open_since_ts` renders 'unknown' date rather than epoch."""
    body = hub.format_heartbeat(_finding(id="zero01"), 0)
    assert "unknown" in body


# ── BC-035 _mock_print fallback preserves BC-001 e2e expectations ──


async def test_notify_heartbeat_mock_fallback_prints_when_registry_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BEACONRY_MOCK=1 + no registered adapters → `_mock_print` dumps the body
    so the BC-001 demo test still sees the `[mock] telegram send: ...` string."""
    from poc.hub import notifiers

    monkeypatch.setattr(hub, "MOCK", True)
    notifiers.reset_registry_for_tests()
    try:
        await hub.notify_heartbeat(
            _finding(id="mock01", cve_id="CVE-MOCK-H"), int(time.time()) - 86400
        )
        out = capsys.readouterr().out
        assert "[mock] telegram send: Heartbeat dispatched" in out
        assert "CVE-MOCK-H" in out
    finally:
        notifiers.reset_registry_for_tests()


async def test_notify_mock_fallback_prints_when_registry_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Parity check for the first-fire notify() mock fallback — same contract."""
    from poc.hub import notifiers

    monkeypatch.setattr(hub, "MOCK", True)
    notifiers.reset_registry_for_tests()
    try:
        await hub.notify(_finding(id="mock02", cve_id="CVE-MOCK-N"))
        out = capsys.readouterr().out
        assert "[mock] telegram send: Notification dispatched" in out
        assert "CVE-MOCK-N" in out
    finally:
        notifiers.reset_registry_for_tests()


async def test_notify_regression_mock_fallback_prints_when_registry_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Third branch — regression path mock fallback."""
    from poc.hub import notifiers

    monkeypatch.setattr(hub, "MOCK", True)
    notifiers.reset_registry_for_tests()
    try:
        await hub.notify_regression(
            _finding(id="mock03", cve_id="CVE-MOCK-R"), last_fixed_ts=int(time.time()) - 86400
        )
        out = capsys.readouterr().out
        assert "[mock] telegram send: Regression dispatched" in out
        assert "CVE-MOCK-R" in out
    finally:
        notifiers.reset_registry_for_tests()
