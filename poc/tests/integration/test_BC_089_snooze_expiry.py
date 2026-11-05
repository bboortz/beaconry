"""BC-089 — Snooze with real expiry.

Covers:
    - CAP-T183: snooze_until_ts + sweep + distinct expiry notification
    - CAP-T184: configurable snooze-duration buttons on inline keyboard
"""

from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import hub
import pytest

from poc.hub.executor.base import Finding

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    hub.init_db()
    return data_dir


def _finding(fid: str = "a" * 16) -> Finding:
    return Finding(
        id=fid,
        host="homelab",
        cve_id="CVE-2026-9999",
        package="libc",
        version="1.0",
        fixed_version="1.1",
        severity="High",
        cvss=8.1,
        source_type="docker",
        source_ref="nginx:1.24",
        description="fixture CVE for BC-089",
        tier=2,
        kev=False,
    )


def _seed_snoozed(
    db_path: Path, fid: str, snooze_until_ts: int, cve_id: str = "CVE-2026-9999"
) -> None:
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO findings (id, host, cve_id, package, version, fixed_version, "
        "severity, cvss, source_type, source_ref, description, tier, kev, status, "
        "snooze_until_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            fid,
            "homelab",
            cve_id,
            "libc",
            "1.0",
            "1.1",
            "High",
            8.1,
            "docker",
            "nginx:1.24",
            "fixture",
            2,
            0,
            "snoozed",
            snooze_until_ts,
        ),
    )
    con.commit()
    con.close()


def _read_status(db_path: Path, fid: str) -> str | None:
    con = sqlite3.connect(db_path)
    row = con.execute("SELECT status FROM findings WHERE id = ?", (fid,)).fetchone()
    con.close()
    return row[0] if row else None


def _read_snooze_until(db_path: Path, fid: str) -> int:
    con = sqlite3.connect(db_path)
    row = con.execute("SELECT snooze_until_ts FROM findings WHERE id = ?", (fid,)).fetchone()
    con.close()
    return int(row[0]) if row else 0


# ── Schema + env ─────────────────────────────────────────────────────────────


def test_schema_migration_adds_column(db_env: Path) -> None:
    """Scenario: schema v5 → v6 adds snooze_until_ts column."""
    con = sqlite3.connect(db_env / "beaconry.db")
    cols = {row[1] for row in con.execute("PRAGMA table_info(findings)").fetchall()}
    con.close()
    assert "snooze_until_ts" in cols


def test_schema_version_at_least_6(db_env: Path) -> None:
    """BC-089 bumped to v6; later BCs (BC-092 → v7) bumped further.
    Assert at-least-6 so this test tolerates future migrations."""
    con = sqlite3.connect(db_env / "beaconry.db")
    v = con.execute("PRAGMA user_version").fetchone()[0]
    con.close()
    assert v >= 6


# ── _parse_snooze_hours ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", [1, 4, 24, 168]),
        ("   ", [1, 4, 24, 168]),
        ("4", [4]),
        ("1,4,24,168", [1, 4, 24, 168]),
        ("1,4,4,24", [1, 4, 24]),  # dedupe preserves order
        ("foo,4,bar,24", [4, 24]),  # bad tokens dropped
        ("0,1,2", [1, 1, 2]),  # clamp ≥1 — but 0→1 then 1 again means dedupe
    ],
)
def test_parse_snooze_hours(raw: str, expected: list[int]) -> None:
    # Clamp behaviour produces (1,1,2); dedup keeps first → (1,2)
    if raw == "0,1,2":
        expected = [1, 2]
    assert hub._parse_snooze_hours(raw) == expected


def test_env_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_SNOOZE_DEFAULT_HOURS", raising=False)
    monkeypatch.delenv("BEACONRY_SNOOZE_SWEEP_INTERVAL_S", raising=False)
    monkeypatch.delenv("BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE", raising=False)
    importlib.reload(hub)
    assert hub.SNOOZE_DEFAULT_HOURS == [1, 4, 24, 168]
    assert hub.SNOOZE_SWEEP_INTERVAL_S == 60
    assert hub.SNOOZE_NOTIFY_ON_EXPIRE is True


# ── Scenario 1: snooze tap stores expiry ─────────────────────────────────────


@pytest.mark.asyncio
async def test_snooze_tap_stores_expiry(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = _finding()
    hub.save_finding(f, now_ts=1800000000)
    query = AsyncMock()
    # Freeze time.time so the expiry is predictable.
    monkeypatch.setattr(hub.time, "time", lambda: 1800000000.0)
    await hub._handle_snooze(query, f, f.id, parts=["snooze", f.id, "4"])
    assert _read_status(db_env / "beaconry.db", f.id) == "snoozed"
    assert _read_snooze_until(db_env / "beaconry.db", f.id) == 1800000000 + 4 * 3600
    # Reply includes the expiry datetime.
    args, kwargs = query.edit_message_text.call_args
    assert "Returns at" in args[0]


# ── Scenario 2 + 3 + 4: sweep behaviour ──────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_reopens_expired(db_env: Path) -> None:
    fid = "b" * 16
    _seed_snoozed(db_env / "beaconry.db", fid, snooze_until_ts=1799000000)
    flipped = await hub.snooze_expiry_sweep(now_ts=1800000000)
    assert flipped == 1
    assert _read_status(db_env / "beaconry.db", fid) == "open"
    # snooze_until_ts cleared after re-open.
    assert _read_snooze_until(db_env / "beaconry.db", fid) == 0


@pytest.mark.asyncio
async def test_sweep_ignores_not_yet_expired(db_env: Path) -> None:
    fid = "c" * 16
    _seed_snoozed(db_env / "beaconry.db", fid, snooze_until_ts=1900000000)
    flipped = await hub.snooze_expiry_sweep(now_ts=1800000000)
    assert flipped == 0
    assert _read_status(db_env / "beaconry.db", fid) == "snoozed"


@pytest.mark.asyncio
async def test_sweep_ignores_legacy_zero_ts(db_env: Path) -> None:
    """Pre-BC-089 snoozes have snooze_until_ts=0; must stay snoozed indefinitely."""
    fid = "d" * 16
    _seed_snoozed(db_env / "beaconry.db", fid, snooze_until_ts=0)
    flipped = await hub.snooze_expiry_sweep(now_ts=1800000000)
    assert flipped == 0
    assert _read_status(db_env / "beaconry.db", fid) == "snoozed"


# ── Scenario 5: multi-button keyboard ────────────────────────────────────────


def test_keyboard_renders_env_hours_default(db_env: Path) -> None:
    f = _finding()
    kb = hub._kb_finding(f)
    # Row 1 = fix button alone, row 2 = snooze buttons, row 3 = accept.
    rows = kb.inline_keyboard
    assert len(rows) == 3
    assert "Fix now" in rows[0][0].text
    snooze_texts = [b.text for b in rows[1]]
    assert snooze_texts == ["⏰ 1h", "⏰ 4h", "⏰ 1d", "⏰ 1w"]
    assert "Accept risk" in rows[2][0].text


@pytest.mark.parametrize(
    "env_value,expected_labels",
    [
        ("4", ["⏰ 4h"]),
        ("1,4", ["⏰ 1h", "⏰ 4h"]),
        ("36", ["⏰ 36h"]),  # non-multiple-of-24 stays as Nh
        ("48", ["⏰ 2d"]),
        ("336", ["⏰ 2w"]),
    ],
)
def test_keyboard_renders_env_hours_custom(
    monkeypatch: pytest.MonkeyPatch, env_value: str, expected_labels: list[str]
) -> None:
    monkeypatch.setenv("BEACONRY_SNOOZE_DEFAULT_HOURS", env_value)
    importlib.reload(hub)
    f = _finding()
    kb = hub._kb_finding(f)
    snooze_texts = [b.text for b in kb.inline_keyboard[1]]
    assert snooze_texts == expected_labels
    # Reset for subsequent tests.
    monkeypatch.delenv("BEACONRY_SNOOZE_DEFAULT_HOURS")
    importlib.reload(hub)


# ── Scenario 6: opt out of expiry notify ─────────────────────────────────────


@pytest.mark.asyncio
async def test_opt_out_notify_on_expire(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub, "SNOOZE_NOTIFY_ON_EXPIRE", False)
    notify_mock = AsyncMock()
    monkeypatch.setattr(hub, "notify_snooze_expired", notify_mock)
    fid = "e" * 16
    _seed_snoozed(db_env / "beaconry.db", fid, snooze_until_ts=1799000000)
    flipped = await hub.snooze_expiry_sweep(now_ts=1800000000)
    assert flipped == 1
    assert _read_status(db_env / "beaconry.db", fid) == "open"
    notify_mock.assert_not_called()


# ── Scenario 7: distinct expiry notification body ────────────────────────────


def test_expiry_notification_distinct_body() -> None:
    body = hub.format_snooze_expired(_finding())
    assert "SNOOZE EXPIRED" in body
    assert "back on your radar" in body
    assert "CVE-2026-9999" in body


# ── Audit trail ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_writes_audit_event(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hub, "SNOOZE_NOTIFY_ON_EXPIRE", False)  # isolate audit from notify
    fid = "f" * 16
    _seed_snoozed(db_env / "beaconry.db", fid, snooze_until_ts=1799000000)
    await hub.snooze_expiry_sweep(now_ts=1800000000)
    audit_path = db_env / "audit.log"
    assert audit_path.exists()
    events = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    expired = [e for e in events if e.get("event") == "snooze_expired"]
    assert len(expired) == 1
    assert expired[0]["finding_id"] == fid
    assert expired[0]["snooze_until_ts"] == 1799000000
