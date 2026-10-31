"""BC-035 — notification adapter architecture.

Covers:
    - CAP-T078: Notification adapter architecture — per-channel enable/disable
      + dispatch isolation (one failing adapter doesn't block others)
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from poc.hub import notifiers
from poc.hub.executor.base import Finding
from poc.hub.notifiers.base import NotificationAdapter


class _RecordingAdapter(NotificationAdapter):
    """Stub adapter that just records what was asked of it."""

    def __init__(self, name: str, env_var: str = "", raises: bool = False) -> None:
        self.name = name  # type: ignore[misc]
        self.env_var = env_var  # type: ignore[misc]
        self._raises = raises
        self.calls: list[tuple[str, tuple]] = []

    async def send_finding(self, finding: Finding) -> None:
        self.calls.append(("send_finding", (finding.id,)))
        if self._raises:
            raise RuntimeError(f"{self.name} failed intentionally")

    async def send_regression(self, finding: Finding, last_fixed_ts: int) -> None:
        self.calls.append(("send_regression", (finding.id, last_fixed_ts)))
        if self._raises:
            raise RuntimeError(f"{self.name} failed intentionally")

    async def send_heartbeat(self, finding: Finding, open_since_ts: int) -> None:
        self.calls.append(("send_heartbeat", (finding.id, open_since_ts)))
        if self._raises:
            raise RuntimeError(f"{self.name} failed intentionally")

    async def send_digest(self, suppressed: list[Finding]) -> None:
        self.calls.append(("send_digest", (len(suppressed),)))
        if self._raises:
            raise RuntimeError(f"{self.name} failed intentionally")


def _finding(fid: str = "x") -> Finding:
    return Finding(
        id=fid,
        host="h",
        cve_id="CVE-X",
        package="p",
        version="1",
        fixed_version="2",
        severity="High",
        cvss=8.0,
        source_type="docker",
        source_ref="nginx:1.24",
        description="d",
    )


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    notifiers.reset_registry_for_tests()
    yield
    notifiers.reset_registry_for_tests()


async def test_single_adapter_receives_dispatch() -> None:
    a = _RecordingAdapter("a")
    notifiers.register(a)
    await notifiers.notify_finding(_finding("f1"))
    assert a.calls == [("send_finding", ("f1",))]


async def test_multiple_adapters_all_receive_dispatch() -> None:
    a = _RecordingAdapter("a")
    b = _RecordingAdapter("b")
    notifiers.register(a)
    notifiers.register(b)
    await notifiers.notify_finding(_finding("f2"))
    assert a.calls == [("send_finding", ("f2",))]
    assert b.calls == [("send_finding", ("f2",))]


async def test_disabled_adapter_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T078: operator can disable individual channels via env var."""
    a = _RecordingAdapter("a", env_var="BEACONRY_NOTIFIER_A_ENABLED")
    b = _RecordingAdapter("b", env_var="BEACONRY_NOTIFIER_B_ENABLED")
    monkeypatch.setenv("BEACONRY_NOTIFIER_A_ENABLED", "0")
    monkeypatch.setenv("BEACONRY_NOTIFIER_B_ENABLED", "1")
    notifiers.register(a)
    notifiers.register(b)

    await notifiers.notify_finding(_finding("f3"))

    assert a.calls == [], "disabled adapter must not receive dispatch"
    assert b.calls == [("send_finding", ("f3",))]


async def test_enabled_by_default_when_env_var_unset() -> None:
    """Adapters with no env var declared are always enabled."""
    a = _RecordingAdapter("a")
    assert a.enabled is True


async def test_failing_adapter_does_not_block_siblings() -> None:
    """CAP-T078 isolation: one broken adapter doesn't swallow dispatch to others."""
    a = _RecordingAdapter("broken", raises=True)
    b = _RecordingAdapter("healthy")
    notifiers.register(a)
    notifiers.register(b)

    await notifiers.notify_finding(_finding("f4"))

    # Broken adapter saw the call but raised
    assert a.calls == [("send_finding", ("f4",))]
    # Healthy adapter STILL received dispatch
    assert b.calls == [("send_finding", ("f4",))]


async def test_registry_order_independent_of_failures() -> None:
    """Registration order is dispatch order; a failure in slot N doesn't skip N+1."""
    a = _RecordingAdapter("a")
    broken = _RecordingAdapter("broken", raises=True)
    c = _RecordingAdapter("c")
    for ad in (a, broken, c):
        notifiers.register(ad)

    await notifiers.notify_regression(_finding("f5"), last_fixed_ts=123)

    assert a.calls == [("send_regression", ("f5", 123))]
    assert broken.calls == [("send_regression", ("f5", 123))]
    assert c.calls == [("send_regression", ("f5", 123))]


async def test_all_four_dispatch_kinds() -> None:
    a = _RecordingAdapter("a")
    notifiers.register(a)
    f = _finding("f6")
    await notifiers.notify_finding(f)
    await notifiers.notify_regression(f, last_fixed_ts=111)
    await notifiers.notify_heartbeat(f, open_since_ts=222)
    await notifiers.notify_digest([f, f])

    kinds = [call[0] for call in a.calls]
    assert kinds == ["send_finding", "send_regression", "send_heartbeat", "send_digest"]


def test_enabled_adapters_filters_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    a = _RecordingAdapter("a", env_var="BEACONRY_NOTIFIER_A_ENABLED")
    b = _RecordingAdapter("b", env_var="BEACONRY_NOTIFIER_B_ENABLED")
    notifiers.register(a)
    notifiers.register(b)

    monkeypatch.setenv("BEACONRY_NOTIFIER_A_ENABLED", "0")
    monkeypatch.setenv("BEACONRY_NOTIFIER_B_ENABLED", "1")
    enabled = notifiers.enabled_adapters()
    assert [a.name for a in enabled] == ["b"]
