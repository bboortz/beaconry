"""BC-119 — integration tests for scout /scan gzip compression.

Covers:
    - CAP-T221: End-to-end wiring of scout's gzip-encoded body through
      the hub's `_ScanPayloadMiddleware`. Unit tests
      (`poc/tests/unit/test_BC_119_scout_scan_gzip_compression.py`)
      cover the atoms; this file exercises the real HTTP round-trip
      with both gzipped + identity-encoded bodies so a wiring
      regression between the middleware's body swap and FastAPI's
      downstream parse is caught.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poc.hub import hub


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setenv("HUB_SCOUT_TOKEN", "")
    # Reset BC-122 integrity counter so assertions are clean.
    monkeypatch.setattr(hub, "_pipeline_integrity_mismatches", 0)
    hub.init_db()
    return data_dir


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


# ── Scenario 1: real gzipped body → hub decompresses + processes ─────────


def test_gzip_body_round_trip(hub_env: Path) -> None:
    client = TestClient(hub.api)
    payload = {"host": "host-a", "sboms": []}
    body = _canonical(payload)
    compressed = gzip.compress(body.encode("utf-8"))
    r = client.post(
        "/scan",
        content=compressed,
        headers={
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
            "Content-Length": str(len(compressed)),
        },
    )
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok"


# ── Scenario 2: legacy identity body still processes ─────────────────────


def test_identity_body_still_works(hub_env: Path) -> None:
    client = TestClient(hub.api)
    r = client.post("/scan", json={"host": "host-a", "sboms": []})
    assert r.status_code == 200


# ── Scenario 3: zip-bomb (huge decompressed size) rejected with 413 ────


def test_gzip_body_decompressed_cap_rejected(
    hub_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tiny gzipped payload that expands to > BEACONRY_SCAN_MAX_DECOMPRESSED_BYTES
    is rejected post-decompression with a 413, not processed."""
    # Set a tiny decompressed cap so a modest body triggers the guard.
    monkeypatch.setattr(hub, "SCAN_MAX_DECOMPRESSED_BYTES", 100)
    client = TestClient(hub.api)
    # Build a body that compresses well but expands past 100 bytes.
    inflated = "A" * 1000
    payload = {"host": "host-a", "sboms": [], "_pad": inflated}
    body = _canonical(payload)
    compressed = gzip.compress(body.encode("utf-8"))
    r = client.post(
        "/scan",
        content=compressed,
        headers={
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
            "Content-Length": str(len(compressed)),
        },
    )
    assert r.status_code == 413
    j = r.json()
    assert "decompressed" in j["detail"].lower()


# ── Scenario 4: malformed gzip body → 400 (not 500) ────────────────────


def test_malformed_gzip_body_returns_400(hub_env: Path) -> None:
    client = TestClient(hub.api)
    r = client.post(
        "/scan",
        content=b"not actually gzip",
        headers={
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
            "Content-Length": "17",
        },
    )
    assert r.status_code == 400
    assert "gzip" in r.json()["detail"].lower()
