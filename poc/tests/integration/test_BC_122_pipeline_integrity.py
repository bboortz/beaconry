"""BC-122 integration — end-to-end hash wiring through receive_scan.

Covers:
    - CAP-T223: Scout-shaped body flows through hub's /scan middleware,
      `receive_scan` strips the integrity block, verifies the transfer
      hash, and emits a `scan_integrity` audit event with every
      expected field populated. Legacy body without an integrity block
      still processes, audit event records transfer_match=None.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poc.hub import hub


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Hub pointed at an isolated DATA_DIR + DB so audit writes don't
    pollute the real cache, and auth is off for tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setenv("HUB_SCOUT_TOKEN", "")
    monkeypatch.setattr(hub, "_pipeline_integrity_mismatches", 0)
    hub.init_db()
    return data_dir


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _read_audit(data_dir: Path) -> list[dict]:
    audit = data_dir / "audit.log"
    if not audit.exists():
        return []
    return [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]


# ── Happy path: hash matches, audit event complete ───────────────────────


def test_happy_path_transfer_verifies(hub_env: Path) -> None:
    client = TestClient(hub.api)
    payload_without_integrity = {"host": "host-a", "sboms": []}
    canonical = _canonical(payload_without_integrity)
    transfer_hash = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    body = {
        **payload_without_integrity,
        "integrity": {
            "iteration_hash": "f" * 16,
            "transfer_hash": transfer_hash,
            "n_sboms": 0,
        },
    }
    r = client.post("/scan", json=body)
    assert r.status_code == 200
    audit = _read_audit(hub_env)
    integrity_events = [e for e in audit if e.get("event") == "scan_integrity"]
    assert len(integrity_events) == 1
    ev = integrity_events[0]
    assert ev["iteration_hash"] == "f" * 16
    assert ev["transfer_hash_claimed"] == transfer_hash
    assert ev["transfer_hash_computed"] == transfer_hash
    assert ev["transfer_match"] is True
    assert ev["n_sboms_claimed"] == 0
    assert ev["n_sboms_received"] == 0
    assert ev["n_candidates"] == 0
    assert ev["n_persisted"] == 0
    assert "processing_hash" in ev


# ── Mutated body is caught (Scenario 2) ──────────────────────────────────


def test_mutated_body_recorded_as_mismatch(hub_env: Path) -> None:
    client = TestClient(hub.api)
    body = {
        "host": "host-a",
        "sboms": [],
        "integrity": {
            "iteration_hash": "1" * 16,
            # Pretend scout claimed a hash that doesn't match the body —
            # the same observable state as transit corruption that
            # mutates the body but not the integrity header.
            "transfer_hash": "deadbeefdeadbeef",
            "n_sboms": 0,
        },
    }
    r = client.post("/scan", json=body)
    assert r.status_code == 200  # fail-open on integrity mismatch
    audit = _read_audit(hub_env)
    ev = next(e for e in audit if e.get("event") == "scan_integrity")
    assert ev["transfer_match"] is False
    assert ev["transfer_hash_claimed"] == "deadbeefdeadbeef"
    assert ev["transfer_hash_computed"] != "deadbeefdeadbeef"
    assert hub._pipeline_integrity_mismatches >= 1


# ── Legacy pre-BC-122 body (Scenario 3) ──────────────────────────────────


def test_legacy_body_without_hashes_degrades_cleanly(hub_env: Path) -> None:
    """Pre-BC-122 scout omits the integrity block. Hub must not crash;
    audit event records `transfer_match=None` + `n_sboms_claimed=None`.
    Processing still runs; no mismatch counter increment."""
    client = TestClient(hub.api)
    r = client.post("/scan", json={"host": "host-a", "sboms": []})
    assert r.status_code == 200
    audit = _read_audit(hub_env)
    ev = next(e for e in audit if e.get("event") == "scan_integrity")
    assert ev["transfer_match"] is None
    assert ev["transfer_hash_claimed"] is None
    assert ev["n_sboms_claimed"] is None
    assert hub._pipeline_integrity_mismatches == 0


# ── Processing drops are accounted for (Scenario 6) ──────────────────────


def test_processing_drops_accounted_for(hub_env: Path) -> None:
    """Even on an empty-SBOM scan, drop accounting must balance:
    0 candidates = 0 persisted + 0 dedup + 0 capped + 0 save-dropped +
    0 other. The verifier's invariant check depends on this."""
    client = TestClient(hub.api)
    body = {"host": "host-a", "sboms": []}
    canonical = _canonical(body)
    body["integrity"] = {
        "iteration_hash": "2" * 16,
        "transfer_hash": hashlib.sha256(canonical.encode()).hexdigest()[:16],
        "n_sboms": 0,
    }
    r = client.post("/scan", json=body)
    assert r.status_code == 200
    audit = _read_audit(hub_env)
    ev = next(e for e in audit if e.get("event") == "scan_integrity")
    bucket_sum = (
        int(ev["n_dedup"])
        + int(ev["n_capped"])
        + int(ev["n_dropped_in_save"])
        + int(ev["n_filtered_other"])
    )
    assert int(ev["n_candidates"]) == int(ev["n_persisted"]) + bucket_sum
