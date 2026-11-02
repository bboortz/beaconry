"""BC-060 — notify_sent_failed audit symmetry.

Covers:
    - CAP-T120: notify_sent_failed + notify_digest_sent_failed audit events
    - CAP-B043: Operator can see which notifications failed in the audit log
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    """Hub reloaded with a non-MOCK-but-no-real-adapters fixture — the
    notify path takes the dispatch branch (not the _mock_print branch)
    so we can inject a raising _dispatch_* and observe the failure
    audit event."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    # MOCK=0 so notify() goes through the dispatch branch.
    monkeypatch.setenv("BEACONRY_MOCK", "0")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    # Required by hub import — the token read happens at module scope.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "0")

    import hub

    importlib.reload(hub)
    hub.init_db()
    yield {"hub": hub, "audit": data_dir / "audit.log"}
    importlib.reload(hub)


def _finding(hub, *, fid: str = "fid-1", cve: str = "CVE-X") -> object:
    return hub.Finding(
        id=fid,
        host="laptop",
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


def _read_audit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ── notify failures ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notify_failure_emits_notify_sent_failed(
    hub_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T120: notify failure → notify_sent_failed audit event."""
    hub = hub_env["hub"]
    f = _finding(hub)

    async def _boom(*_a, **_kw) -> None:
        raise RuntimeError("telegram down")

    monkeypatch.setattr(hub, "_dispatch_finding", _boom)
    with pytest.raises(RuntimeError):
        await hub.notify(f)

    events = _read_audit(hub_env["audit"])
    failed = [e for e in events if e.get("event") == "notify_sent_failed"]
    assert len(failed) == 1
    assert failed[0]["finding_id"] == "fid-1"
    assert failed[0]["kind"] == "finding"
    assert failed[0]["cve_id"] == "CVE-X"
    assert failed[0]["host"] == "laptop"
    assert "RuntimeError" in failed[0]["reason"]

    # And NO notify_sent — the dispatch failed before we reach the success emit.
    assert not any(e.get("event") == "notify_sent" for e in events)


@pytest.mark.asyncio
async def test_notify_regression_failure_emits_notify_sent_failed(
    hub_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = hub_env["hub"]
    f = _finding(hub, cve="CVE-REG")

    async def _boom(*_a, **_kw) -> None:
        raise ConnectionError("rst")

    monkeypatch.setattr(hub, "_dispatch_regression", _boom)
    with pytest.raises(ConnectionError):
        await hub.notify_regression(f, last_fixed_ts=1000)

    failed = [e for e in _read_audit(hub_env["audit"]) if e.get("event") == "notify_sent_failed"]
    assert any(e["cve_id"] == "CVE-REG" and e["kind"] == "regression" for e in failed)


@pytest.mark.asyncio
async def test_notify_heartbeat_failure_emits_notify_sent_failed(
    hub_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = hub_env["hub"]
    f = _finding(hub, cve="CVE-HB")

    async def _boom(*_a, **_kw) -> None:
        raise TimeoutError("slow")

    monkeypatch.setattr(hub, "_dispatch_heartbeat", _boom)
    with pytest.raises(TimeoutError):
        await hub.notify_heartbeat(f, open_since_ts=1000)

    failed = [e for e in _read_audit(hub_env["audit"]) if e.get("event") == "notify_sent_failed"]
    assert any(e["cve_id"] == "CVE-HB" and e["kind"] == "heartbeat" for e in failed)


@pytest.mark.asyncio
async def test_notify_failure_re_raises(hub_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T120: audit is additive — the exception still propagates so the
    caller's existing error path (log.warning in process_sbom_entry etc.)
    stays triggered."""
    hub = hub_env["hub"]
    f = _finding(hub)

    async def _boom(*_a, **_kw) -> None:
        raise RuntimeError("x")

    monkeypatch.setattr(hub, "_dispatch_finding", _boom)
    with pytest.raises(RuntimeError, match="x"):
        await hub.notify(f)


# ── Digest failures ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_digest_failure_emits_notify_digest_sent_failed(
    hub_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T120: send_digest failure → notify_digest_sent_failed with
    finding_ids list."""
    hub = hub_env["hub"]
    suppressed = [_finding(hub, fid=f"fid-{i}") for i in range(3)]

    async def _boom(*_a, **_kw) -> None:
        raise RuntimeError("send fail")

    monkeypatch.setattr(hub, "_dispatch_digest", _boom)
    with pytest.raises(RuntimeError):
        await hub.send_digest(suppressed)

    failed = [
        e for e in _read_audit(hub_env["audit"]) if e.get("event") == "notify_digest_sent_failed"
    ]
    assert len(failed) == 1
    assert set(failed[0]["finding_ids"]) == {"fid-0", "fid-1", "fid-2"}
    assert failed[0]["count"] == 3
    assert "RuntimeError" in failed[0]["reason"]


@pytest.mark.asyncio
async def test_digest_failure_re_raises(hub_env, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = hub_env["hub"]
    suppressed = [_finding(hub, fid="fid-a")]

    async def _boom(*_a, **_kw) -> None:
        raise ConnectionError("net")

    monkeypatch.setattr(hub, "_dispatch_digest", _boom)
    with pytest.raises(ConnectionError, match="net"):
        await hub.send_digest(suppressed)


# ── Reason formatting ──────────────────────────────────────────────────────


def test_reason_starts_with_class_name(hub_env) -> None:
    """CAP-T120: reason starts with 'ClassName: ' for grep-ability."""
    hub = hub_env["hub"]
    reason = hub._format_fail_reason(RuntimeError("boom"))
    assert reason.startswith("RuntimeError: ")
    assert "boom" in reason


def test_reason_truncated(hub_env) -> None:
    """CAP-T120: pathological long error messages get truncated so the audit
    log stays bounded. Length check includes the ClassName + ': ' prefix."""
    hub = hub_env["hub"]
    long_msg = "x" * 10_000
    reason = hub._format_fail_reason(RuntimeError(long_msg))
    # Message portion after 'RuntimeError: ' is capped at _NOTIFY_REASON_MAX.
    assert "x" in reason
    assert len(reason) <= len("RuntimeError: ") + hub._NOTIFY_REASON_MAX


# ── Success path still works ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_success_still_emits_notify_sent(hub_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T120: BC-058 success path unaffected by the BC-060 wrap."""
    hub = hub_env["hub"]
    f = _finding(hub)

    sent_calls: list[object] = []

    async def _ok(*a, **kw) -> None:
        sent_calls.append(a)

    monkeypatch.setattr(hub, "_dispatch_finding", _ok)
    await hub.notify(f)
    assert len(sent_calls) == 1

    events = _read_audit(hub_env["audit"])
    assert any(e.get("event") == "notify_sent" for e in events)
    assert not any(e.get("event") == "notify_sent_failed" for e in events)
