"""BC-090 — Auto-fix regression body actually reaches Telegram.

Covers:
    - CAP-T185: notify_auto_fix_regression dispatches through
      send_auto_fix_regression (distinct body on the real path)
    - CAP-T186: BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED per-kind
      silence knob
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import hub
import pytest

from poc.hub.executor.base import Finding
from poc.hub.notifiers.base import NotificationAdapter

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
        description="fixture CVE for BC-090",
        tier=2,
        kev=False,
    )


# ── Scenario 1: distinct body on Telegram ───────────────────────────────────


@pytest.mark.asyncio
async def test_telegram_renders_distinct_body(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 1: notify_auto_fix_regression → send_auto_fix_regression
    → format_auto_fix_regression (distinct body)."""
    hub.save_finding(_finding())
    monkeypatch.setattr(hub, "MOCK", False)
    monkeypatch.setattr(hub, "AUTO_FIX_REGRESSION_NOTIFY_ENABLED", True)
    dispatch = AsyncMock()
    monkeypatch.setattr(hub, "_dispatch_auto_fix_regression", dispatch)
    monkeypatch.setattr(hub._notifiers_pkg, "enabled_adapters", lambda: [object()])
    await hub.notify_auto_fix_regression(_finding(), auto_fixed_ts=1799000000)
    # Dispatcher was called with the correct signature
    dispatch.assert_awaited_once()
    args = dispatch.call_args.args
    assert args[1] == 1799000000  # auto_fixed_ts
    # The adapter method name on the dispatcher is the distinct one
    # (verified elsewhere); here assert the hub wiring picks the new path
    # instead of the BC-031 fallback.


# ── Scenario 2: opt-out ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_opt_out_suppresses_telegram(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 2: BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED=0 →
    no adapter dispatch, but audit records the suppression."""
    hub.save_finding(_finding())
    monkeypatch.setattr(hub, "AUTO_FIX_REGRESSION_NOTIFY_ENABLED", False)
    dispatch = AsyncMock()
    monkeypatch.setattr(hub, "_dispatch_auto_fix_regression", dispatch)
    monkeypatch.setattr(hub._notifiers_pkg, "enabled_adapters", lambda: [object()])
    await hub.notify_auto_fix_regression(_finding(), auto_fixed_ts=1799000000)
    dispatch.assert_not_called()
    audit_path = db_env / "audit.log"
    assert audit_path.exists()
    events = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    suppressed = [e for e in events if e.get("event") == "notify_suppressed"]
    assert len(suppressed) >= 1
    e = suppressed[-1]
    assert e["kind"] == "auto_fix_regression"
    assert "BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED" in e["reason"]


# ── Scenario 3: default loud ─────────────────────────────────────────────────


def test_env_default_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 3: env unset → loud (default `1`)."""
    monkeypatch.delenv("BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED", raising=False)
    importlib.reload(hub)
    assert hub.AUTO_FIX_REGRESSION_NOTIFY_ENABLED is True


@pytest.mark.parametrize(
    "value,expected",
    [("0", False), ("1", True), ("", True), ("foo", True)],  # only "0" disables
)
def test_env_parse_variants(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool) -> None:
    monkeypatch.setenv("BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED", value)
    importlib.reload(hub)
    assert hub.AUTO_FIX_REGRESSION_NOTIFY_ENABLED is expected
    monkeypatch.delenv("BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED")
    importlib.reload(hub)


# ── Scenario 4: adapter back-compat ─────────────────────────────────────────


class _StubAdapter(NotificationAdapter):
    """Minimal adapter without overriding send_auto_fix_regression —
    inherits the base no-op."""

    name = "stub"
    env_var = ""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def send_finding(self, finding: Finding, last_seen_ts=None) -> None:
        self.events.append("finding")

    async def send_regression(self, finding, last_fixed_ts, last_seen_ts=None) -> None:
        self.events.append("regression")

    async def send_heartbeat(self, finding, open_since_ts, last_seen_ts=None) -> None:
        self.events.append("heartbeat")

    async def send_digest(self, suppressed) -> None:
        self.events.append("digest")

    # No override of send_auto_fix_regression → base no-op.


@pytest.mark.asyncio
async def test_adapter_without_override_no_ops(db_env: Path) -> None:
    """Scenario 4: stub adapter without override → base no-op fires,
    no exception."""
    from poc.hub import notifiers

    notifiers.reset_registry_for_tests()
    stub = _StubAdapter()
    notifiers.register(stub)
    try:
        # No exception expected — base no-op inherited.
        await notifiers.notify_auto_fix_regression(_finding(), auto_fixed_ts=1799000000)
        # Stub's send_auto_fix_regression wasn't overridden; events stays empty.
        assert stub.events == []
    finally:
        notifiers.reset_registry_for_tests()


# ── Notifier dispatcher exists ──────────────────────────────────────────────


def test_notifiers_package_exports_dispatcher() -> None:
    from poc.hub import notifiers

    assert hasattr(notifiers, "notify_auto_fix_regression")
    assert callable(notifiers.notify_auto_fix_regression)


# ── Telegram adapter wires the distinct formatter ───────────────────────────


@pytest.mark.asyncio
async def test_telegram_adapter_picks_distinct_formatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct test on TelegramAdapter.send_auto_fix_regression — confirms
    it reads self._fmt['auto_fix_regression'], not self._fmt['regression']."""
    from poc.hub.notifiers.telegram import TelegramAdapter

    monkeypatch.setenv("BEACONRY_MOCK", "1")
    picked_key: list[str] = []

    def _fake_auto_fix(f, ts, last_seen_ts=None) -> str:
        picked_key.append("auto_fix_regression")
        return "AUTOFIX_BODY"

    def _fake_regression(f, ts, last_seen_ts=None) -> str:
        picked_key.append("regression")
        return "GENERIC_BODY"

    adapter = TelegramAdapter(
        tg_app_getter=lambda: None,
        chat_id_getter=lambda: "42",
        formatters={
            "notification": lambda f, last_seen_ts=None: "x",
            "regression": _fake_regression,
            "heartbeat": lambda f, ts, last_seen_ts=None: "x",
            "digest": lambda s: "x",
            "auto_fix_regression": _fake_auto_fix,
        },
        keyboard_builders={
            "finding": lambda f: None,
            "regression": lambda f: None,
            "heartbeat": lambda f: None,
        },
    )
    await adapter.send_auto_fix_regression(_finding(), auto_fixed_ts=1799000000)
    assert picked_key == ["auto_fix_regression"]
