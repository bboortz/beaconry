"""BC-119 — Scout gzips /scan POST bodies; hub decompresses transparently.

Covers:
    - CAP-T221: Scout sets Content-Encoding: gzip on every /scan POST;
      hub middleware decompresses bodies in place (behind BC-113's
      wire-size cap and a new decompressed-size cap); legacy pre-BC-119
      scouts posting raw JSON keep working; compression failure in
      scout falls back to raw POST.
"""

from __future__ import annotations

import gzip
import importlib
import json
import logging

import pytest
from fastapi.testclient import TestClient

from poc.hub import hub
from poc.scout import scout


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HUB_SCOUT_TOKEN", "")
    return TestClient(hub.api)


# ── Env parsing ─────────────────────────────────────────────────────────────


def test_env_defaults_decompressed_cap_to_10x_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default BEACONRY_SCAN_MAX_DECOMPRESSED_BYTES = 10× wire cap."""
    monkeypatch.delenv("BEACONRY_SCAN_MAX_DECOMPRESSED_BYTES", raising=False)
    monkeypatch.delenv("BEACONRY_SCAN_MAX_PAYLOAD_BYTES", raising=False)
    importlib.reload(hub)
    assert hub.SCAN_MAX_DECOMPRESSED_BYTES == 10 * hub.SCAN_MAX_PAYLOAD_BYTES


def test_env_decompressed_cap_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_SCAN_MAX_DECOMPRESSED_BYTES", "1234567")
    importlib.reload(hub)
    assert hub.SCAN_MAX_DECOMPRESSED_BYTES == 1234567


# ── Scout: _post_scan_body sets Content-Encoding: gzip ─────────────────────


def test_post_scan_body_sets_gzip_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scout always gzips /scan body + sends Content-Encoding: gzip."""
    captured: dict[str, object] = {}

    def fake_post(url, data, headers, timeout):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data

        class R:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"ok": True}

        return R()

    monkeypatch.setattr(scout.requests, "post", fake_post)
    body = json.dumps({"host": "test", "sboms": []})
    ok, err, data = scout._post_scan_body(body)
    assert ok is True
    assert err == ""
    assert data == {"ok": True}
    assert captured["headers"]["Content-Encoding"] == "gzip"  # type: ignore[index]
    # Body must be compressed, not raw JSON.
    assert captured["data"] != body
    assert isinstance(captured["data"], bytes | bytearray)
    # Round-trip must recover the JSON.
    assert gzip.decompress(captured["data"]).decode("utf-8") == body  # type: ignore[arg-type]


def test_gzip_fallback_to_raw_on_compress_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """If gzip.compress raises, scout logs + falls back to raw POST."""
    captured: dict[str, object] = {}

    def fake_post(url, data, headers, timeout):  # type: ignore[no-untyped-def]
        captured["headers"] = headers
        captured["data"] = data

        class R:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"ok": True}

        return R()

    def boom(*_a, **_kw) -> bytes:  # type: ignore[no-untyped-def]
        raise RuntimeError("gzip go boom")

    monkeypatch.setattr(scout.gzip, "compress", boom)
    monkeypatch.setattr(scout.requests, "post", fake_post)
    body = json.dumps({"host": "test", "sboms": []})
    with caplog.at_level(logging.WARNING, logger="scout"):
        ok, _err, _data = scout._post_scan_body(body)
    assert ok is True
    # No Content-Encoding header → raw body.
    assert "Content-Encoding" not in captured["headers"]  # type: ignore[operator]
    assert captured["data"] == body
    assert any("gzip" in r.message.lower() for r in caplog.records)


def test_scout_logs_compression_enabled(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Scout logs 'Content-Encoding: gzip' once at the first /scan POST."""

    def fake_post(url, data, headers, timeout):  # type: ignore[no-untyped-def]
        class R:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {}

        return R()

    monkeypatch.setattr(scout.requests, "post", fake_post)
    # Reset the one-shot log flag so this test is deterministic even after
    # earlier tests have already triggered it.
    scout._scan_gzip_log_once = False  # type: ignore[attr-defined]
    with caplog.at_level(logging.INFO, logger="scout"):
        scout._post_scan_body(json.dumps({"host": "h", "sboms": []}))
    assert any("Content-Encoding: gzip" in r.message for r in caplog.records)


# ── Hub middleware: identity (legacy) path still works ────────────────────


def test_identity_encoded_body_still_parses(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-BC-119 scouts POST raw JSON — hub must still accept it."""
    monkeypatch.setattr(hub, "SCAN_MAX_PAYLOAD_BYTES", 10_000_000)
    r = client.post("/scan", json={"host": "legacy", "sboms": []})
    assert r.status_code != 413
    assert r.status_code != 400


# ── Hub middleware: gzipped body is decompressed ─────────────────────────


def test_middleware_decompresses_gzip_body(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gzipped scout body round-trips through receive_scan."""
    monkeypatch.setattr(hub, "SCAN_MAX_PAYLOAD_BYTES", 10_000_000)
    monkeypatch.setattr(hub, "SCAN_MAX_DECOMPRESSED_BYTES", 10_000_000)
    body = json.dumps({"host": "gz-host", "sboms": []})
    compressed = gzip.compress(body.encode("utf-8"), compresslevel=6)
    r = client.post(
        "/scan",
        content=compressed,
        headers={
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "Content-Length": str(len(compressed)),
        },
    )
    # Must not be 413 or 400 — hub decompressed, parsed, and processed.
    assert r.status_code == 200, r.text


def test_large_sbom_gzipped_accepted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A payload whose RAW size exceeds the wire cap but gzipped size fits
    is accepted. Mirrors bb-82jq's real failure mode on 2026-11-15.

    `sboms` is empty so the hub skips DB writes — the point of the test
    is the middleware's size gating, not the SBOM pipeline. The padding
    goes in an ignored field so the payload is compressible but still a
    valid JSON dict for FastAPI.
    """
    monkeypatch.setattr(hub, "SCAN_MAX_PAYLOAD_BYTES", 1_000_000)  # 1MB wire cap
    monkeypatch.setattr(hub, "SCAN_MAX_DECOMPRESSED_BYTES", 100_000_000)
    body = json.dumps({"host": "big-fleet", "sboms": [], "_pad": "A" * 10_000_000})
    assert len(body) > 1_000_000  # confirms the wire cap would reject raw
    compressed = gzip.compress(body.encode("utf-8"), compresslevel=6)
    assert len(compressed) < 1_000_000  # confirms gzipped fits
    r = client.post(
        "/scan",
        content=compressed,
        headers={
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "Content-Length": str(len(compressed)),
        },
    )
    assert r.status_code == 200, r.text


# ── Hub middleware: zip-bomb guards ──────────────────────────────────────


def test_oversized_content_length_rejected_before_decompress(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gzipped POST whose WIRE size exceeds the wire cap is rejected by
    BC-113's middleware before BC-119 tries to decompress."""
    monkeypatch.setattr(hub, "SCAN_MAX_PAYLOAD_BYTES", 100)
    # Even a tiny gzip stream will have Content-Length > 100.
    payload = gzip.compress(b'{"host":"h","sboms":[]}' * 1000)
    assert len(payload) > 100
    r = client.post(
        "/scan",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "Content-Length": str(len(payload)),
        },
    )
    assert r.status_code == 413
    assert r.json()["detail"] == "scan payload too large"


def test_decompressed_over_cap_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tiny gzipped body that expands to > SCAN_MAX_DECOMPRESSED_BYTES
    is rejected post-decompression with a distinct 413 message."""
    monkeypatch.setattr(hub, "SCAN_MAX_PAYLOAD_BYTES", 10_000_000)
    monkeypatch.setattr(hub, "SCAN_MAX_DECOMPRESSED_BYTES", 500)
    big_body = b'{"host":"h","sboms":[]}' + b" " * 10_000  # decompresses >500B
    compressed = gzip.compress(big_body)
    r = client.post(
        "/scan",
        content=compressed,
        headers={
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "Content-Length": str(len(compressed)),
        },
    )
    assert r.status_code == 413
    assert r.json()["detail"] == "decompressed scan payload too large"
    assert r.json()["max_bytes"] == 500


def test_malformed_gzip_body_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Content-Encoding: gzip + garbage body → 400, not 500."""
    monkeypatch.setattr(hub, "SCAN_MAX_PAYLOAD_BYTES", 10_000_000)
    r = client.post(
        "/scan",
        content=b"this is not gzip",
        headers={
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "Content-Length": "16",
        },
    )
    assert r.status_code == 400
    assert "gzip" in r.json()["detail"].lower()
