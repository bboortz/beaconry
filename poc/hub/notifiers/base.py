"""BC-035 — abstract base for notification adapters.

Minimum contract: four `send_*` coroutines + an `enabled` property driven
by an env var. Adapters that don't support a given notification kind should
still implement the method (as a no-op) rather than raising."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from poc.hub.executor.base import Finding


class NotificationAdapter(ABC):
    #: Short identifier used in logs and env-var naming.
    name: str = "abstract"

    #: Env var that enables/disables this adapter. Default-enabled when unset.
    env_var: str = ""

    @property
    def enabled(self) -> bool:
        """`BEACONRY_NOTIFIER_<NAME>_ENABLED=0` disables this adapter. Default on."""
        if not self.env_var:
            return True
        return os.environ.get(self.env_var, "1") != "0"

    @abstractmethod
    async def send_finding(self, finding: Finding) -> None:
        """First-fire notification for a new finding."""

    @abstractmethod
    async def send_regression(self, finding: Finding, last_fixed_ts: int) -> None:
        """BC-031 regression alarm — previously-fixed CVE is back."""

    @abstractmethod
    async def send_heartbeat(self, finding: Finding, open_since_ts: int) -> None:
        """BC-035 periodic reminder for findings sitting open too long."""

    @abstractmethod
    async def send_digest(self, suppressed: list[Finding]) -> None:
        """BC-004 digest for findings suppressed by the per-run cap."""
