"""BC-087 — Wire `Last seen: X ago` into live notifications + BEACONRY_LOG_LEVEL.

Covers:
    - CAP-T180: hub.notify*() loads last_seen_ts from DB and passes it to
      format_*; the rendered body contains the "Last seen" line.
    - CAP-T181: BEACONRY_LOG_LEVEL env var controls root log level
      (default INFO; DEBUG re-enables BC-086 reconcile trace; invalid
      values fall back to INFO).
"""

from __future__ import annotations

import importlib
import logging
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
        description="fixture",
        tier=2,
        kev=False,
    )


def _seed_finding(db_path: Path, fid: str, last_seen_ts: int) -> None:
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO findings (id, host, cve_id, package, version, fixed_version, "
        "severity, cvss, source_type, source_ref, description, tier, kev, status, "
        "last_seen_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            fid,
            "homelab",
            "CVE-2026-9999",
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
            "open",
            last_seen_ts,
        ),
    )
    con.commit()
    con.close()


# ── CAP-T180: _load_last_seen helper ─────────────────────────────────────────


def test_load_last_seen_returns_ts(db_env: Path) -> None:
    """Row present with ts=1799990000 → helper returns 1799990000."""
    fid = "b" * 16
    _seed_finding(db_env / "beaconry.db", fid, 1799990000)
    assert hub._load_last_seen(fid) == 1799990000


def test_load_last_seen_missing_row(db_env: Path) -> None:
    """Unknown finding id → None (format_last_seen treats as skip-line)."""
    assert hub._load_last_seen("deadbeef00000000") is None


def test_load_last_seen_zero_ts(db_env: Path) -> None:
    """Row present but last_seen_ts=0 (never seen) → None."""
    fid = "c" * 16
    _seed_finding(db_env / "beaconry.db", fid, 0)
    assert hub._load_last_seen(fid) is None


def test_load_last_seen_no_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DB file missing entirely (pre-init) → None, no raise."""
    monkeypatch.setattr(hub, "DB_PATH", tmp_path / "missing.db")
    # Don't call init_db — DB_PATH points to a path that will exist (sqlite
    # creates on open) but the findings table won't → OperationalError path.
    assert hub._load_last_seen("anything") is None


# ── CAP-T180: notify*() wire-through ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_notify_passes_last_seen(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """notify() → _dispatch_finding gets the loaded last_seen_ts."""
    fid = "d" * 16
    _seed_finding(db_env / "beaconry.db", fid, 1800000000)
    f = _finding(fid)
    monkeypatch.setattr(hub, "MOCK", False)
    dispatch = AsyncMock()
    monkeypatch.setattr(hub, "_dispatch_finding", dispatch)
    # bypass the MOCK/empty-registry branch by pretending an adapter is enabled
    monkeypatch.setattr(hub._notifiers_pkg, "enabled_adapters", lambda: [object()])
    await hub.notify(f)
    dispatch.assert_awaited_once_with(f, 1800000000)


@pytest.mark.asyncio
async def test_notify_regression_passes_last_seen(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid = "e" * 16
    _seed_finding(db_env / "beaconry.db", fid, 1800000111)
    f = _finding(fid)
    monkeypatch.setattr(hub, "MOCK", False)
    dispatch = AsyncMock()
    monkeypatch.setattr(hub, "_dispatch_regression", dispatch)
    monkeypatch.setattr(hub._notifiers_pkg, "enabled_adapters", lambda: [object()])
    await hub.notify_regression(f, last_fixed_ts=1799000000)
    dispatch.assert_awaited_once_with(f, 1799000000, 1800000111)


@pytest.mark.asyncio
async def test_notify_heartbeat_passes_last_seen(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid = "f" * 16
    _seed_finding(db_env / "beaconry.db", fid, 1800000222)
    f = _finding(fid)
    monkeypatch.setattr(hub, "MOCK", False)
    dispatch = AsyncMock()
    monkeypatch.setattr(hub, "_dispatch_heartbeat", dispatch)
    monkeypatch.setattr(hub._notifiers_pkg, "enabled_adapters", lambda: [object()])
    await hub.notify_heartbeat(f, open_since_ts=1799500000)
    dispatch.assert_awaited_once_with(f, 1799500000, 1800000222)


@pytest.mark.asyncio
async def test_notify_auto_fix_regression_passes_last_seen(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fid = "0" * 16
    _seed_finding(db_env / "beaconry.db", fid, 1800000333)
    f = _finding(fid)
    monkeypatch.setattr(hub, "MOCK", False)
    monkeypatch.setattr(hub, "AUTO_FIX_REGRESSION_NOTIFY_ENABLED", True)
    dispatch = AsyncMock()
    # BC-090: notify_auto_fix_regression now dispatches to the distinct
    # _dispatch_auto_fix_regression (pre-BC-090 fell through to _dispatch_regression).
    monkeypatch.setattr(hub, "_dispatch_auto_fix_regression", dispatch)
    monkeypatch.setattr(hub._notifiers_pkg, "enabled_adapters", lambda: [object()])
    await hub.notify_auto_fix_regression(f, auto_fixed_ts=1799800000)
    dispatch.assert_awaited_once_with(f, 1799800000, 1800000333)


# ── CAP-T180: mock-print path also carries the line ─────────────────────────


@pytest.mark.asyncio
async def test_notify_mock_path_renders_last_seen_line(
    db_env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """MOCK=1 + no adapters: body printed to stdout must include 'Last seen:'."""
    fid = "1" * 16
    _seed_finding(db_env / "beaconry.db", fid, 1800000000 - 3600)  # 1h ago
    f = _finding(fid)
    monkeypatch.setattr(hub, "MOCK", True)
    monkeypatch.setattr(hub._notifiers_pkg, "enabled_adapters", lambda: [])
    with patch("time.time", return_value=1800000000):
        await hub.notify(f)
    captured = capsys.readouterr().out
    assert "Last seen:" in captured


# ── CAP-T181: BEACONRY_LOG_LEVEL env ────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected_level",
    [
        ("DEBUG", logging.DEBUG),
        ("INFO", logging.INFO),
        ("WARNING", logging.WARNING),
        ("ERROR", logging.ERROR),
    ],
)
def test_log_level_env_parsed(
    monkeypatch: pytest.MonkeyPatch, value: str, expected_level: int
) -> None:
    """Re-import hub with BEACONRY_LOG_LEVEL set → module-local level matches.

    Asserting on hub._LOG_LEVEL rather than the root logger's level because
    `logging.basicConfig()` is a no-op when handlers already exist (pytest's
    caplog registers one). The module-local var captures the resolved value
    we'd pass to basicConfig on a clean boot.
    """
    monkeypatch.setenv("BEACONRY_LOG_LEVEL", value)
    importlib.reload(hub)
    assert expected_level == hub._LOG_LEVEL


def test_log_level_default_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env unset → default INFO."""
    monkeypatch.delenv("BEACONRY_LOG_LEVEL", raising=False)
    importlib.reload(hub)
    assert logging.INFO == hub._LOG_LEVEL


def test_log_level_invalid_falls_back_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """BEACONRY_LOG_LEVEL=nonsense → fall back to INFO."""
    monkeypatch.setenv("BEACONRY_LOG_LEVEL", "LOUD")
    importlib.reload(hub)
    assert logging.INFO == hub._LOG_LEVEL
    # Reset to default so later tests see INFO.
    monkeypatch.delenv("BEACONRY_LOG_LEVEL")
    importlib.reload(hub)


# ── CAP-T181: BC-086 diag logs downgraded to DEBUG ──────────────────────────


def test_bc086_diag_at_debug_not_info(db_env: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Default INFO → bump_last_seen diag line NOT emitted; at DEBUG it IS."""
    # Empty scanned_ids is a no-op early-return; seed one so the UPDATE runs.
    fid = "2" * 16
    _seed_finding(db_env / "beaconry.db", fid, 1799000000)

    with caplog.at_level(logging.INFO, logger="hub"):
        hub.bump_last_seen("homelab", {fid}, now_ts=1800000000)
    assert not any("BC-086 bump_last_seen" in rec.message for rec in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="hub"):
        hub.bump_last_seen("homelab", {fid}, now_ts=1800000001)
    assert any("BC-086 bump_last_seen" in rec.message for rec in caplog.records)
