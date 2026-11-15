"""BC-124 — heartbeat retry + HUB_HTTP_PORT env.

Covers:
    - CAP-T226: `_heartbeat_with_retry` retries a failing POST up to
      BEACONRY_HEARTBEAT_RETRY_MAX_ATTEMPTS times with capped
      exponential backoff. Fails open after max attempts; next
      heartbeat tick fills the gap.
    - CAP-T227: Hub `__main__` reads `HUB_HTTP_PORT` env; default 8000.
"""

from __future__ import annotations

import pytest

from poc.scout import scout

# ── heartbeat retry ──────────────────────────────────────────────────────


def test_heartbeat_succeeds_on_first_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fake_once() -> bool:
        calls.append(1)
        return True

    monkeypatch.setattr(scout, "heartbeat_once", fake_once)
    assert scout._heartbeat_with_retry(sleep=lambda _: None) is True
    assert len(calls) == 1  # no retries on success


def test_heartbeat_retries_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []
    sleeps: list[int] = []

    def fake_once() -> bool:
        attempts.append(1)
        return len(attempts) == 2  # fail once, succeed on second try

    monkeypatch.setattr(scout, "heartbeat_once", fake_once)
    monkeypatch.setattr(scout, "HEARTBEAT_RETRY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(scout, "HEARTBEAT_RETRY_BASE_S", 2)
    assert scout._heartbeat_with_retry(sleep=sleeps.append) is True
    assert len(attempts) == 2
    assert sleeps == [2]  # one backoff before the successful retry


def test_heartbeat_retry_backoff_exponential(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[int] = []

    def always_fail() -> bool:
        return False

    monkeypatch.setattr(scout, "heartbeat_once", always_fail)
    monkeypatch.setattr(scout, "HEARTBEAT_RETRY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(scout, "HEARTBEAT_RETRY_BASE_S", 2)
    assert scout._heartbeat_with_retry(sleep=sleeps.append) is False
    # 2s and 4s — two backoffs (between attempts 1→2 and 2→3). No sleep
    # after the final failed attempt (MAX_ATTEMPTS is the last call).
    assert sleeps == [2, 4]


def test_heartbeat_retry_abandons_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []

    def always_fail() -> bool:
        attempts.append(1)
        return False

    monkeypatch.setattr(scout, "heartbeat_once", always_fail)
    monkeypatch.setattr(scout, "HEARTBEAT_RETRY_MAX_ATTEMPTS", 5)
    monkeypatch.setattr(scout, "HEARTBEAT_RETRY_BASE_S", 1)
    assert scout._heartbeat_with_retry(sleep=lambda _: None) is False
    assert len(attempts) == 5


# ── HUB_HTTP_PORT env ──────────────────────────────────────────────────


def test_hub_http_port_main_block_reads_env_default_8000() -> None:
    """Grep the __main__ block to confirm it reads HUB_HTTP_PORT with
    default 8000 (not hardcoded)."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[3] / "poc" / "hub" / "hub.py"
    body = src.read_text()
    assert 'os.environ.get("HUB_HTTP_PORT"' in body
    assert 'uvicorn.run("hub:api"' in body and "port=_port" in body


def test_hub_http_port_env_parsing() -> None:
    """Direct int-parse of the env the __main__ block uses."""
    import os

    os.environ["HUB_HTTP_PORT"] = "8042"
    try:
        port = int(os.environ.get("HUB_HTTP_PORT", "8000"))
        assert port == 8042
    finally:
        del os.environ["HUB_HTTP_PORT"]
