"""BC-058 — mutation + audit cleanup.

Covers:
    - CAP-T115: set_status fail-fast + rich audit shape
    - CAP-T116: notify_sent + notify_digest_sent audit events
    - CAP-T117: Diagnosable Telegram "Finding not found" error
    - CAP-B041: Operator can diagnose stale-notification taps + has
      consistent audit coverage across every mutation path
"""

from __future__ import annotations

import importlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")

    import hub

    importlib.reload(hub)
    hub.init_db()
    yield {"hub": hub, "db": hub.DB_PATH, "audit": data_dir / "audit.log"}
    importlib.reload(hub)


def _insert(hub, fid: str, host: str, cve: str, status: str = "open") -> None:
    con = sqlite3.connect(hub.DB_PATH)
    con.execute(
        "INSERT INTO findings (id, host, cve_id, package, version, "
        "fixed_version, severity, cvss, source_type, source_ref, "
        "description, status, tier) VALUES (?, ?, ?, 'p', '1', '2', 'High', 7.5, "
        "'docker', 'nginx:1', '', ?, 2)",
        (fid, host, cve, status),
    )
    con.commit()
    con.close()


def _read_audit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── Fix 1 + 2: set_status rowcount + rich audit shape ──────────────────────


def test_set_status_raises_on_unknown_id(hub_env) -> None:
    """CAP-T115: unknown id → FindingNotFound, not silent no-op."""
    hub = hub_env["hub"]
    with pytest.raises(hub.FindingNotFound):
        hub.set_status("nope-x", "snoozed")


def test_set_status_unknown_id_no_audit(hub_env) -> None:
    """CAP-T115: no audit event when the id doesn't exist."""
    import contextlib

    hub = hub_env["hub"]
    with contextlib.suppress(hub.FindingNotFound):
        hub.set_status("nope-x", "snoozed")
    events = _read_audit(hub_env["audit"])
    assert not any(e.get("event") == "status_change" for e in events)


def test_set_status_emits_rich_audit(hub_env) -> None:
    """CAP-T115: audit event carries host, cve_id, old_status, new_status, source."""
    hub = hub_env["hub"]
    _insert(hub, "fid-1", "laptop", "CVE-X", status="open")
    hub.set_status("fid-1", "snoozed", source="telegram")

    events = _read_audit(hub_env["audit"])
    status_events = [e for e in events if e.get("event") == "status_change"]
    assert len(status_events) == 1
    e = status_events[0]
    assert e["finding_id"] == "fid-1"
    assert e["host"] == "laptop"
    assert e["cve_id"] == "CVE-X"
    assert e["old_status"] == "open"
    assert e["new_status"] == "snoozed"
    assert e["source"] == "telegram"


def test_set_status_source_telegram_default(hub_env) -> None:
    """CAP-T115: default source='telegram' when not passed."""
    hub = hub_env["hub"]
    _insert(hub, "fid-1", "laptop", "CVE-X")
    hub.set_status("fid-1", "snoozed")  # no source kwarg
    e = [e for e in _read_audit(hub_env["audit"]) if e.get("event") == "status_change"][0]
    assert e["source"] == "telegram"


def test_set_status_fixed_sets_last_fixed_ts(hub_env) -> None:
    """BC-031 contract preserved: 'fixed' transitions set last_fixed_ts."""
    hub = hub_env["hub"]
    _insert(hub, "fid-1", "laptop", "CVE-X")
    hub.set_status("fid-1", "fixed", source="telegram")

    con = sqlite3.connect(hub_env["db"])
    row = con.execute(
        "SELECT status, last_fixed_ts FROM findings WHERE id = ?", ("fid-1",)
    ).fetchone()
    con.close()
    assert row[0] == "fixed"
    assert row[1] > 0


# ── Fix 3: notify_sent audit events ────────────────────────────────────────


def _finding(hub, fid: str = "fid-1", host: str = "laptop", cve: str = "CVE-X") -> object:
    return hub.Finding(
        id=fid,
        host=host,
        cve_id=cve,
        package="p",
        version="1",
        fixed_version="2",
        severity="High",
        cvss=7.5,
        source_type="docker",
        source_ref="nginx:1",
        description="d",
        tier=2,
    )


@pytest.mark.asyncio
async def test_notify_emits_notify_sent(hub_env) -> None:
    """CAP-T116: notify(f) writes a notify_sent event with kind=finding."""
    hub = hub_env["hub"]
    f = _finding(hub)
    await hub.notify(f)
    events = _read_audit(hub_env["audit"])
    sent = [e for e in events if e.get("event") == "notify_sent"]
    assert len(sent) == 1
    assert sent[0]["finding_id"] == "fid-1"
    assert sent[0]["cve_id"] == "CVE-X"
    assert sent[0]["host"] == "laptop"
    assert sent[0]["kind"] == "finding"


@pytest.mark.asyncio
async def test_notify_regression_emits_notify_sent(hub_env) -> None:
    """CAP-T116: notify_regression writes notify_sent with kind=regression."""
    hub = hub_env["hub"]
    f = _finding(hub, cve="CVE-Y")
    await hub.notify_regression(f, last_fixed_ts=1000)
    sent = [e for e in _read_audit(hub_env["audit"]) if e.get("event") == "notify_sent"]
    assert any(e["cve_id"] == "CVE-Y" and e["kind"] == "regression" for e in sent)


@pytest.mark.asyncio
async def test_notify_heartbeat_emits_notify_sent(hub_env) -> None:
    """CAP-T116: notify_heartbeat writes notify_sent with kind=heartbeat."""
    hub = hub_env["hub"]
    f = _finding(hub, cve="CVE-Z")
    await hub.notify_heartbeat(f, open_since_ts=1000)
    sent = [e for e in _read_audit(hub_env["audit"]) if e.get("event") == "notify_sent"]
    assert any(e["cve_id"] == "CVE-Z" and e["kind"] == "heartbeat" for e in sent)


def test_digest_emits_notify_digest_sent(hub_env) -> None:
    """CAP-T116: record_digest_sent emits notify_digest_sent with finding_ids list."""
    hub = hub_env["hub"]
    suppressed = [_finding(hub, fid=f"fid-{i}", cve=f"CVE-{i}") for i in range(3)]
    hub.record_digest_sent("laptop", suppressed, now=1000)

    events = _read_audit(hub_env["audit"])
    digest = [e for e in events if e.get("event") == "notify_digest_sent"]
    assert len(digest) == 1
    assert digest[0]["host"] == "laptop"
    assert set(digest[0]["finding_ids"]) == {"fid-0", "fid-1", "fid-2"}
    assert digest[0]["count"] == 3


# ── Fix 4: Telegram "Finding not found" diagnosable ───────────────────────


def test_telegram_not_found_includes_id_prefix(hub_env) -> None:
    """CAP-T117: error includes first 8 chars of finding_id."""
    import re

    src = Path(hub_env["hub"].__file__).read_text()
    # The new error template references short_id[:8].
    assert "finding_id[:8]" in src
    # And includes the id in the user-facing message.
    assert re.search(r"finding\s*<code>\{short_id\}", src)


def test_telegram_not_found_includes_stale_hint(hub_env) -> None:
    """CAP-T117: error includes a stale-notification hint phrase."""
    src = Path(hub_env["hub"].__file__).read_text()
    assert "stale notification" in src.lower() or "old notification" in src.lower()


# ── Fix 5: format_notification multi_host hint ────────────────────────────


def test_format_notification_multi_host_hint(hub_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T116 drive-by: multi_host=True short-circuits is_multi_host_inventory."""
    hub = hub_env["hub"]
    f = _finding(hub)

    def _boom() -> bool:
        raise AssertionError("is_multi_host_inventory should not be called when hint provided")

    monkeypatch.setattr(hub, "is_multi_host_inventory", _boom)
    msg = hub.format_notification(f, multi_host=True)
    assert "🖥️" in msg.splitlines()[0]
    msg_single = hub.format_notification(f, multi_host=False)
    assert "🖥️" not in msg_single.splitlines()[0]


def test_format_notification_multi_host_fallback(hub_env) -> None:
    """CAP-T116 drive-by: multi_host=None falls back to the DB lookup (back-compat)."""
    hub = hub_env["hub"]
    _insert(hub, "fid-1", "laptop", "CVE-X")
    _insert(hub, "fid-2", "server2", "CVE-Y")
    f = _finding(hub)
    msg = hub.format_notification(f, multi_host=None)
    assert "🖥️" in msg.splitlines()[0]  # multi-host inventory, prefix on


def test_format_notification_single_host_fallback(hub_env) -> None:
    """multi_host=None + single-host inventory → no prefix."""
    hub = hub_env["hub"]
    _insert(hub, "fid-1", "laptop", "CVE-X")
    f = _finding(hub)
    msg = hub.format_notification(f, multi_host=None)
    assert "🖥️" not in msg.splitlines()[0]
