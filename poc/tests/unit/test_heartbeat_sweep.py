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
