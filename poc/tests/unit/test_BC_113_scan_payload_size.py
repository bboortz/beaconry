"""BC-113 — SBOM payload size limit on /scan.

Covers:
    - CAP-T215: `/scan` returns 413 when Content-Length exceeds
      BEACONRY_SCAN_MAX_PAYLOAD_BYTES. Other endpoints are
      unaffected. 0 disables the check.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from poc.hub import hub


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Patch bearer auth OFF so tests don't need HUB_SCOUT_TOKEN."""
    monkeypatch.setenv("HUB_SCOUT_TOKEN", "")
    return TestClient(hub.api)


# ── Env parsing ─────────────────────────────────────────────────────────────


def test_env_defaults_to_50mb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_SCAN_MAX_PAYLOAD_BYTES", raising=False)
    import importlib

    importlib.reload(hub)
    assert hub.SCAN_MAX_PAYLOAD_BYTES == 50 * 1024 * 1024


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_SCAN_MAX_PAYLOAD_BYTES", "1024")
    import importlib

    importlib.reload(hub)
    assert hub.SCAN_MAX_PAYLOAD_BYTES == 1024


def test_env_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_SCAN_MAX_PAYLOAD_BYTES", "not-a-number")
    import importlib

    importlib.reload(hub)
    assert hub.SCAN_MAX_PAYLOAD_BYTES == 50 * 1024 * 1024


# ── Middleware behavior ─────────────────────────────────────────────────────


def test_scan_accepts_small_payload(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A payload under the limit flows through to receive_scan normally."""
    monkeypatch.setattr(hub, "SCAN_MAX_PAYLOAD_BYTES", 10_000_000)
    r = client.post("/scan", json={"host": "test-host", "sboms": []})
    # 200 or anything other than 413 means the middleware didn't reject it.
    assert r.status_code != 413


def test_scan_rejects_oversize_via_content_length(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit Content-Length header over limit → 413."""
    monkeypatch.setattr(hub, "SCAN_MAX_PAYLOAD_BYTES", 100)
    # Body size well above limit.
    big = "A" * 10_000
    r = client.post(
        "/scan",
        content=big,
        headers={"Content-Type": "application/json", "Content-Length": str(len(big))},
    )
    assert r.status_code == 413
    body = r.json()
    assert body["detail"] == "scan payload too large"
    assert body["max_bytes"] == 100
    assert body["declared_bytes"] == 10_000


def test_scan_size_limit_zero_disables_check(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SCAN_MAX_PAYLOAD_BYTES=0 skips the middleware. 0 is clamped up to 1
    by `_parse_positive_int_hub`, so we exercise the disable via setattr."""
    monkeypatch.setattr(hub, "SCAN_MAX_PAYLOAD_BYTES", 0)
    r = client.post("/scan", json={"host": "tiny", "sboms": []})
    assert r.status_code != 413


def test_other_endpoints_not_gated(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tiny GET to /healthz must not trip the size check (misconfigured
    header handling would have made it fail)."""
    monkeypatch.setattr(hub, "SCAN_MAX_PAYLOAD_BYTES", 1)  # Extreme: 1 byte.
    r = client.get("/healthz")
    assert r.status_code != 413


def test_scan_without_content_length_passes_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the client omits Content-Length (chunked transfer), the middleware
    can't reject upfront — the request flows through. Downstream parse would
    still fail on genuine attack, but the common case is fine."""
    monkeypatch.setattr(hub, "SCAN_MAX_PAYLOAD_BYTES", 10)

    def _no_cl_body():
        yield b'{"host":"x","sboms":[]}'

    # TestClient streams the body when given a generator.
    r = client.post(
        "/scan",
        content=_no_cl_body(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code != 413


def test_malformed_content_length_header_passes_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A garbage Content-Length value (int parse fails) is treated as -1 and
    passes the > SCAN_MAX_PAYLOAD_BYTES check (since -1 < anything)."""
    monkeypatch.setattr(hub, "SCAN_MAX_PAYLOAD_BYTES", 100)
    r = client.post(
        "/scan",
        content=b'{"host":"x","sboms":[]}',
        headers={"Content-Type": "application/json", "Content-Length": "garbage"},
    )
    # httpx may normalize Content-Length internally; the middleware's own
    # parse-fail branch results in declared=-1 which doesn't trigger 413.
    assert r.status_code != 413
