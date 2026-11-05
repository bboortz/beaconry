"""BC-035 notification adapter architecture.

Dispatch outbound notifications through a list of adapters so operators can
enable / disable each delivery channel independently (Telegram, email,
Reddit, etc.). Each adapter owns its own env-based enable flag.

Hub call sites (`notify_finding`, `notify_regression`, `notify_heartbeat`,
`notify_digest`) iterate the registry. One failing adapter must not block
the others — exceptions are logged and swallowed per-adapter.

Interactive callbacks (Telegram button taps) stay inbound-Telegram-specific
in hub.py's `on_button_click` dispatcher — they are not part of the adapter
abstraction. The adapter is for OUTBOUND notifications only.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import NotificationAdapter
from .telegram import TelegramAdapter

if TYPE_CHECKING:
    from poc.hub.executor.base import Finding

log = logging.getLogger("hub.notifiers")

_REGISTRY: list[NotificationAdapter] = []


def register(adapter: NotificationAdapter) -> None:
    """Add an adapter to the global dispatch list."""
    _REGISTRY.append(adapter)


def reset_registry_for_tests() -> None:
    _REGISTRY.clear()


def enabled_adapters() -> list[NotificationAdapter]:
    return [a for a in _REGISTRY if a.enabled]


async def _dispatch(method_name: str, *args: object) -> None:
    """Call `method_name(*args)` on every enabled adapter. Per-adapter
    exceptions are logged and swallowed; one broken adapter must not
    block others."""
    for adapter in _REGISTRY:
        if not adapter.enabled:
            continue
        try:
            method = getattr(adapter, method_name)
            await method(*args)
        except Exception as exc:  # noqa: BLE001
            log.warning("notifier '%s' failed %s: %s", adapter.name, method_name, exc)


async def notify_finding(finding: Finding, last_seen_ts: int | None = None) -> None:
    await _dispatch("send_finding", finding, last_seen_ts)


async def notify_regression(
    finding: Finding, last_fixed_ts: int, last_seen_ts: int | None = None
) -> None:
    await _dispatch("send_regression", finding, last_fixed_ts, last_seen_ts)


async def notify_heartbeat(
    finding: Finding, open_since_ts: int, last_seen_ts: int | None = None
) -> None:
    await _dispatch("send_heartbeat", finding, open_since_ts, last_seen_ts)


async def notify_digest(suppressed: list[Finding]) -> None:
    await _dispatch("send_digest", suppressed)


async def notify_snooze_expired(finding: Finding) -> None:
    """BC-089: dispatch the distinct snooze-expired body to every enabled
    adapter. Any adapter that doesn't override `send_snooze_expired`
    inherits the base no-op, so BC-089 doesn't force a schema change on
    out-of-tree adapters."""
    await _dispatch("send_snooze_expired", finding)


async def notify_auto_fix_regression(
    finding: Finding, auto_fixed_ts: int, last_seen_ts: int | None = None
) -> None:
    """BC-090: dispatch the distinct auto-fix regression body. Pre-BC-090
    hub reused `notify_regression` which rendered format_regression, not
    format_auto_fix_regression (dead code on Telegram path). This closes
    the CAP-T178 gap."""
    await _dispatch("send_auto_fix_regression", finding, auto_fixed_ts, last_seen_ts)


__all__ = [
    "NotificationAdapter",
    "TelegramAdapter",
    "enabled_adapters",
    "notify_auto_fix_regression",
    "notify_digest",
    "notify_finding",
    "notify_heartbeat",
    "notify_regression",
    "notify_snooze_expired",
    "register",
    "reset_registry_for_tests",
]
