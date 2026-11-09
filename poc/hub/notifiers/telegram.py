"""BC-035 — Telegram notification adapter.

Wraps the python-telegram-bot Application + chat_id. All existing
notification bodies (format_notification, format_regression, format_heartbeat,
digest) are rendered elsewhere in hub.py; this adapter just dispatches them.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from .base import NotificationAdapter

if TYPE_CHECKING:
    from poc.hub.executor.base import Finding

log = logging.getLogger("hub.notifiers.telegram")


class TelegramAdapter(NotificationAdapter):
    name = "telegram"
    env_var = "BEACONRY_NOTIFIER_TELEGRAM_ENABLED"

    def __init__(
        self,
        tg_app_getter,
        chat_id_getter,
        formatters,
        keyboard_builders,
        mock_renderer=None,
        pending_writer=None,
    ) -> None:
        """Lazy getters so hub.py can hand us closures over its module globals
        (tg_app may be None at import time; chat_id may be reconfigured for e2e).

        BC-104: `pending_writer(kind, text, reply_markup_json, error)` is a
        callback hub registers at startup. When `send_message` raises, the
        adapter persists a row via this hook; hub's retry loop re-invokes
        `_send(text, reply_markup_json)` until success or MAX_ATTEMPTS.
        None = retry disabled (test fixtures, pre-BC-104 call sites).
        """
        self._tg_app = tg_app_getter
        self._chat_id = chat_id_getter
        self._fmt = formatters
        self._kb = keyboard_builders
        self._mock_renderer = mock_renderer
        self._pending_writer = pending_writer

    def _is_mock(self) -> bool:
        return os.environ.get("BEACONRY_MOCK") == "1"

    async def _send(self, text: str, reply_markup=None, *, kind: str | None = None) -> None:
        """Common send path handling MOCK + missing tg_app gracefully.

        BC-104: wraps `send_message` in try/except. On failure, invokes
        the pending-writer hook (if registered) so hub's retry loop
        can redeliver when Telegram recovers. The caller passes `kind`
        for audit visibility (`notify_queued` event discriminator).
        """
        if self._is_mock():
            if self._mock_renderer is not None:
                print("    [mock] telegram send: notification dispatched")
                print("    ==> Notification")
                print(self._mock_renderer(text))
            return
        tg_app = self._tg_app()
        if tg_app is None:
            log.warning("Telegram app not ready — queueing notification")
            self._maybe_queue(text, reply_markup, kind, "tg_app not ready")
            return
        chat_id = self._chat_id()
        kwargs: dict = {"chat_id": int(chat_id), "text": text, "parse_mode": "HTML"}
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        try:
            await tg_app.bot.send_message(**kwargs)
        except Exception as exc:  # noqa: BLE001 — any failure queues for retry
            log.warning("Telegram send failed, queueing for retry: %s", exc)
            self._maybe_queue(text, reply_markup, kind, str(exc)[:400])

    def _maybe_queue(self, text: str, reply_markup, kind: str | None, error: str) -> None:
        """BC-104: persist a pending-notification row via hub's writer hook.
        Silent no-op if no writer registered (test fixtures)."""
        if self._pending_writer is None:
            return
        try:
            rm_json = (
                reply_markup.to_json()
                if reply_markup is not None and hasattr(reply_markup, "to_json")
                else None
            )
            self._pending_writer(kind or "unknown", text, rm_json, error)
        except Exception as exc:  # noqa: BLE001
            log.warning("pending-writer hook failed: %s", exc)

    async def send_finding(self, finding: Finding, last_seen_ts: int | None = None) -> None:
        text = self._fmt["notification"](finding, last_seen_ts=last_seen_ts)
        kb = self._kb["finding"](finding)
        await self._send(text, reply_markup=kb, kind="finding")

    async def send_regression(
        self, finding: Finding, last_fixed_ts: int, last_seen_ts: int | None = None
    ) -> None:
        text = self._fmt["regression"](finding, last_fixed_ts, last_seen_ts=last_seen_ts)
        kb = self._kb["regression"](finding)
        await self._send(text, reply_markup=kb, kind="regression")

    async def send_heartbeat(
        self, finding: Finding, open_since_ts: int, last_seen_ts: int | None = None
    ) -> None:
        text = self._fmt["heartbeat"](finding, open_since_ts, last_seen_ts=last_seen_ts)
        kb = self._kb["heartbeat"](finding)
        await self._send(text, reply_markup=kb, kind="heartbeat")

    async def send_digest(self, suppressed: list[Finding]) -> None:
        if not suppressed:
            return
        text = self._fmt["digest"](suppressed)
        await self._send(text, reply_markup=None, kind="digest")

    async def send_snooze_expired(self, finding: Finding) -> None:
        """BC-089: '⏰ Snooze expired' body + a fresh first-fire keyboard
        (Fix / Snooze / Accept) so the operator can act without hunting the
        original message."""
        text = self._fmt["snooze_expired"](finding)
        kb = self._kb["finding"](finding)
        await self._send(text, reply_markup=kb, kind="snooze_expired")

    async def send_auto_fix_regression(
        self, finding: Finding, auto_fixed_ts: int, last_seen_ts: int | None = None
    ) -> None:
        """BC-090: dedicated path — renders format_auto_fix_regression
        (not format_regression). Keyboard is the regression keyboard
        (Fix / Accept, no Snooze — a regression deserves attention)."""
        text = self._fmt["auto_fix_regression"](finding, auto_fixed_ts, last_seen_ts=last_seen_ts)
        kb = self._kb["regression"](finding)
        await self._send(text, reply_markup=kb, kind="auto_fix_regression")

    async def send_scout_offline(
        self,
        host: str,
        last_heartbeat_ts: int,
        offline_duration_s: int,
        last_health: str | None,
    ) -> None:
        """BC-102: edge-triggered offline alert. No keyboard — operator
        action is on the host, not in Telegram."""
        mins = offline_duration_s // 60
        health_line = f"\nLast health: <code>{last_health}</code>" if last_health else ""
        text = (
            f"⚠️ <b>Scout offline</b>\n"
            f"Host: <code>{host}</code>\n"
            f"Silent for: <b>{mins}m</b> (last POST: unix {last_heartbeat_ts})"
            f"{health_line}\n"
            f"\nFindings on this host will <b>auto-close</b> after "
            f"<code>BEACONRY_AUTO_FIX_THRESHOLD</code> sweep ticks "
            f"(<code>BEACONRY_AUTO_CLOSE_STALE_SCOUT=0</code> to opt out).\n"
            f"\nCheck <code>systemctl status beaconry-scout.service</code> on the host "
            f"or <code>docker ps --filter name=beaconry-scout</code>."
        )
        await self._send(text, reply_markup=None, kind="scout_offline")

    async def send_scout_online(self, host: str, offline_duration_s: int) -> None:
        """BC-102: edge-triggered recovery alert."""
        mins = offline_duration_s // 60
        text = (
            f"✓ <b>Scout back online</b>\n"
            f"Host: <code>{host}</code>\n"
            f"Was offline for: <b>{mins}m</b>\n"
            f"Resumed normal scanning."
        )
        await self._send(text, reply_markup=None, kind="scout_online")

    async def replay(self, text: str, reply_markup_json: str | None) -> bool:
        """BC-104 retry entry point. Hub's retry loop calls this with a
        row's `(text, reply_markup_json)` payload. Returns True on
        successful delivery; False (and does NOT re-queue) on another
        failure so the caller can bump attempts + reschedule.

        Rebuilding the reply_markup from JSON requires python-telegram-bot's
        InlineKeyboardMarkup.de_json. Pass None when the original call
        had no keyboard.
        """
        if self._is_mock():
            return True
        tg_app = self._tg_app()
        if tg_app is None:
            return False
        chat_id = self._chat_id()
        kwargs: dict = {"chat_id": int(chat_id), "text": text, "parse_mode": "HTML"}
        if reply_markup_json is not None:
            try:
                import telegram as _telegram

                kwargs["reply_markup"] = _telegram.InlineKeyboardMarkup.de_json(
                    __import__("json").loads(reply_markup_json), tg_app.bot
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("BC-104 replay: failed to rebuild reply_markup: %s", exc)
        try:
            await tg_app.bot.send_message(**kwargs)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("BC-104 replay: send still failing: %s", exc)
            return False
