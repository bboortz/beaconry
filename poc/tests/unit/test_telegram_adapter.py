"""BC-035 — TelegramAdapter unit coverage.

Covers:
    - CAP-T078: Telegram notification adapter dispatches all four kinds
      (finding, regression, heartbeat, digest) through the shared `_send`
      path, honours MOCK mode, and no-ops cleanly when `tg_app` is None.
"""

from __future__ import annotations

from typing import Any

import pytest

from poc.hub.executor.base import Finding
from poc.hub.notifiers.telegram import TelegramAdapter


def _finding() -> Finding:
    return Finding(
        id="abc",
        host="h",
        cve_id="CVE-1",
        package="p",
        version="1",
        fixed_version="2",
        severity="High",
        cvss=8.0,
        source_type="docker",
        source_ref="nginx:1.24",
        description="d",
    )


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.sent.append(kwargs)


class _FakeApp:
    def __init__(self) -> None:
        self.bot = _FakeBot()


def _formatters() -> dict[str, Any]:
    return {
        "notification": lambda f: f"finding:{f.id}",
        "regression": lambda f, ts: f"regression:{f.id}:{ts}",
        "heartbeat": lambda f, ts: f"heartbeat:{f.id}:{ts}",
        "digest": lambda sup: f"digest:{len(sup)}",
    }


def _keyboards() -> dict[str, Any]:
    return {
        "finding": lambda f: f"kb-finding:{f.id}",
        "regression": lambda f: f"kb-regression:{f.id}",
        "heartbeat": lambda f: f"kb-heartbeat:{f.id}",
    }


@pytest.fixture
def live_adapter() -> tuple[TelegramAdapter, _FakeApp]:
    app = _FakeApp()
    adapter = TelegramAdapter(
        tg_app_getter=lambda: app,
        chat_id_getter=lambda: "42",
        formatters=_formatters(),
        keyboard_builders=_keyboards(),
    )
    return adapter, app


async def test_send_finding_uses_formatter_and_keyboard(
    live_adapter: tuple[TelegramAdapter, _FakeApp],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BEACONRY_MOCK", raising=False)
    adapter, app = live_adapter
    await adapter.send_finding(_finding())
    assert len(app.bot.sent) == 1
    msg = app.bot.sent[0]
    assert msg["chat_id"] == 42
    assert msg["text"] == "finding:abc"
    assert msg["parse_mode"] == "HTML"
    assert msg["reply_markup"] == "kb-finding:abc"


async def test_send_regression_passes_ts_to_formatter(
    live_adapter: tuple[TelegramAdapter, _FakeApp],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BEACONRY_MOCK", raising=False)
    adapter, app = live_adapter
    await adapter.send_regression(_finding(), last_fixed_ts=999)
    assert app.bot.sent[0]["text"] == "regression:abc:999"
    assert app.bot.sent[0]["reply_markup"] == "kb-regression:abc"


async def test_send_heartbeat_passes_open_since(
    live_adapter: tuple[TelegramAdapter, _FakeApp],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BEACONRY_MOCK", raising=False)
    adapter, app = live_adapter
    await adapter.send_heartbeat(_finding(), open_since_ts=111)
    assert app.bot.sent[0]["text"] == "heartbeat:abc:111"
    assert app.bot.sent[0]["reply_markup"] == "kb-heartbeat:abc"


async def test_send_digest_skips_empty(
    live_adapter: tuple[TelegramAdapter, _FakeApp],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BEACONRY_MOCK", raising=False)
    adapter, app = live_adapter
    await adapter.send_digest([])
    assert app.bot.sent == []


async def test_send_digest_renders_when_nonempty(
    live_adapter: tuple[TelegramAdapter, _FakeApp],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BEACONRY_MOCK", raising=False)
    adapter, app = live_adapter
    await adapter.send_digest([_finding(), _finding()])
    assert app.bot.sent[0]["text"] == "digest:2"
    assert "reply_markup" not in app.bot.sent[0]


async def test_mock_mode_short_circuits_with_renderer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BEACONRY_MOCK=1 + mock_renderer → prints instead of sending via bot."""
    monkeypatch.setenv("BEACONRY_MOCK", "1")

    app = _FakeApp()
    adapter = TelegramAdapter(
        tg_app_getter=lambda: app,
        chat_id_getter=lambda: "42",
        formatters=_formatters(),
        keyboard_builders=_keyboards(),
        mock_renderer=lambda text: f"<rendered: {text}>",
    )
    await adapter.send_finding(_finding())

    out = capsys.readouterr().out
    assert "[mock] telegram send" in out
    assert "<rendered: finding:abc>" in out
    assert app.bot.sent == [], "mock mode must not hit the bot"


async def test_mock_mode_without_renderer_is_silent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    app = _FakeApp()
    adapter = TelegramAdapter(
        tg_app_getter=lambda: app,
        chat_id_getter=lambda: "42",
        formatters=_formatters(),
        keyboard_builders=_keyboards(),
    )
    await adapter.send_finding(_finding())
    assert app.bot.sent == []
    assert capsys.readouterr().out == ""


async def test_missing_tg_app_is_a_warning_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If lifespan hasn't finished wiring tg_app yet, send is a no-op warning."""
    monkeypatch.delenv("BEACONRY_MOCK", raising=False)
    adapter = TelegramAdapter(
        tg_app_getter=lambda: None,
        chat_id_getter=lambda: "42",
        formatters=_formatters(),
        keyboard_builders=_keyboards(),
    )
    with caplog.at_level("WARNING", logger="hub.notifiers.telegram"):
        await adapter.send_finding(_finding())
    assert any("not ready" in rec.message for rec in caplog.records)
