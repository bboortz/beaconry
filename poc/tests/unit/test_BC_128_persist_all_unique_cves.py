"""BC-128 — Persist every identified unique CVE; cap only notifications.

Covers:
    - CAP-T231: `_ingest_candidates` runs `save_finding` on both
      `to_notify` (top-N by rank) and `suppressed` (tail). `notify()`
      + 0.3s rate-limit still only fires on `to_notify`. Digest still
      fires on `suppressed`. `scan_integrity` audit event gains
      `n_notified`, renames `n_capped` → `n_suppressed_from_notify`,
      and `n_persisted` now counts BOTH branches (balance invariant:
      candidates = persisted + dedup + dropped_in_save + filtered_other).
      verify-integrity tolerates both pre-BC-128 and post-BC-128 shapes.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from poc.hub import hub
from poc.hub.executor.base import Finding


def _make_finding(cve_id: str, cvss: float = 9.9, tier: int = 1) -> Finding:
    """Build a minimal Finding that save_finding will accept."""
    return Finding(
        id=f"fid-{cve_id}",
        cve_id=cve_id,
        severity="Critical" if cvss >= 9.0 else "High",
        cvss=cvss,
        description="d",
        host="host-a",
        source_type="docker",
        source_ref="nginx:test",
        package="pkg",
        version="1.0",
        fixed_version="1.1",
        tier=tier,
    )


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setenv("HUB_SCOUT_TOKEN", "")
    hub.init_db()
    return data_dir


# ── Phase 1: save_finding runs on every unique CVE ────────────────────


def test_ingest_saves_all_unique_candidates(hub_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """318 unique CVEs; cap=100. All 318 must get save_finding called."""
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "100")
    saved_ids: list[str] = []

    def fake_save(f, now_ts=None):
        saved_ids.append(f.id)
        return True

    async def fake_notify(f):  # no-op
        return None

    monkeypatch.setattr(hub, "save_finding", fake_save)
    monkeypatch.setattr(hub, "notify", fake_notify)
    monkeypatch.setattr(hub, "send_digest", lambda *a, **kw: _async_none())
    monkeypatch.setattr(hub, "should_send_digest", lambda *a, **kw: (False, "h", "throttled"))
    monkeypatch.setattr(hub, "record_digest_throttled", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "bump_last_seen", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "reconcile_scan", lambda *a, **kw: ([], [], []))
    monkeypatch.setattr(hub, "_dispatch_regression_notifications", lambda *a, **kw: _async_none())

    candidates = [_make_finding(f"CVE-{i}", cvss=9.0 - i * 0.01) for i in range(318)]
    asyncio.run(hub._ingest_candidates("host-a", candidates, now_ts=0, sbom_count=26))
    assert len(saved_ids) == 318


async def _async_none():
    return None


def test_ingest_notifies_only_top_n(hub_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Top-N by (tier, -cvss) → notify. Tail → digest only (for tier ≥ 2).

    Post-BC-129: Tier ≤ 1 items in the suppressed tail also loud-notify,
    so this test uses Tier 2 findings exclusively to isolate the BC-128
    top-N-only notification semantics for low-tier items.
    """
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "10")
    notified_ids: list[str] = []

    def fake_save(f, now_ts=None):
        return True

    async def fake_notify(f):
        notified_ids.append(f.id)

    monkeypatch.setattr(hub, "save_finding", fake_save)
    monkeypatch.setattr(hub, "notify", fake_notify)
    monkeypatch.setattr(hub, "should_send_digest", lambda *a, **kw: (False, "h", "throttled"))
    monkeypatch.setattr(hub, "record_digest_throttled", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "bump_last_seen", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "reconcile_scan", lambda *a, **kw: ([], [], []))
    monkeypatch.setattr(hub, "_dispatch_regression_notifications", lambda *a, **kw: _async_none())

    # 25 unique Tier 2 Highs, cap 10 → 10 notified, 15 suppressed (digest only).
    candidates = [_make_finding(f"CVE-{i}", cvss=8.0 - i * 0.01, tier=2) for i in range(25)]
    asyncio.run(hub._ingest_candidates("host-a", candidates, now_ts=0, sbom_count=5))
    assert len(notified_ids) == 10


def test_ingest_digest_still_fires_on_tail(hub_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Suppressed tail → send_digest must be called (operator inbox parity)."""
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "5")
    digest_called = []

    def fake_save(f, now_ts=None):
        return True

    async def fake_notify(f):
        return None

    async def fake_send_digest(items):
        digest_called.append(len(items))

    monkeypatch.setattr(hub, "save_finding", fake_save)
    monkeypatch.setattr(hub, "notify", fake_notify)
    monkeypatch.setattr(hub, "send_digest", fake_send_digest)
    monkeypatch.setattr(hub, "should_send_digest", lambda *a, **kw: (True, "h", ""))
    monkeypatch.setattr(hub, "record_digest_sent", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "bump_last_seen", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "reconcile_scan", lambda *a, **kw: ([], [], []))
    monkeypatch.setattr(hub, "_dispatch_regression_notifications", lambda *a, **kw: _async_none())

    # 15 unique, cap 5 → 5 notified + 10 suppressed → digest fires with 10.
    candidates = [_make_finding(f"CVE-{i}", cvss=9.0 - i * 0.01) for i in range(15)]
    asyncio.run(hub._ingest_candidates("host-a", candidates, now_ts=0, sbom_count=5))
    assert digest_called == [10]


# ── scan_integrity audit event shape ────────────────────────────────


def _audit_from_env(hub_env: Path) -> list[dict]:
    audit = hub_env / "audit.log"
    return [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]


def test_scan_integrity_has_n_notified_field(
    hub_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "3")
    monkeypatch.setattr(hub, "save_finding", lambda f, now_ts=None: True)
    monkeypatch.setattr(hub, "notify", lambda f: _async_none())
    monkeypatch.setattr(hub, "should_send_digest", lambda *a, **kw: (False, "h", "throttled"))
    monkeypatch.setattr(hub, "record_digest_throttled", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "bump_last_seen", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "reconcile_scan", lambda *a, **kw: ([], [], []))
    monkeypatch.setattr(hub, "_dispatch_regression_notifications", lambda *a, **kw: _async_none())

    candidates = [_make_finding(f"CVE-{i}", cvss=9.0 - i * 0.01) for i in range(10)]
    asyncio.run(hub._ingest_candidates("host-a", candidates, now_ts=0, sbom_count=5))
    events = _audit_from_env(hub_env)
    scan_events = [e for e in events if e.get("event") == "scan_integrity"]
    assert len(scan_events) == 1
    ev = scan_events[0]
    assert "n_notified" in ev
    assert ev["n_notified"] == 3  # cap=3 → only 3 notified


def test_scan_integrity_renames_n_capped_to_suppressed_from_notify(
    hub_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "3")
    monkeypatch.setattr(hub, "save_finding", lambda f, now_ts=None: True)
    monkeypatch.setattr(hub, "notify", lambda f: _async_none())
    monkeypatch.setattr(hub, "should_send_digest", lambda *a, **kw: (False, "h", "throttled"))
    monkeypatch.setattr(hub, "record_digest_throttled", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "bump_last_seen", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "reconcile_scan", lambda *a, **kw: ([], [], []))
    monkeypatch.setattr(hub, "_dispatch_regression_notifications", lambda *a, **kw: _async_none())

    candidates = [_make_finding(f"CVE-{i}", cvss=9.0 - i * 0.01) for i in range(10)]
    asyncio.run(hub._ingest_candidates("host-a", candidates, now_ts=0, sbom_count=5))
    events = _audit_from_env(hub_env)
    ev = next(e for e in events if e.get("event") == "scan_integrity")
    assert "n_suppressed_from_notify" in ev
    assert "n_capped" not in ev  # renamed


def test_scan_integrity_balance_invariant(hub_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Post-BC-128: candidates = persisted + dedup + dropped_in_save + filtered_other.
    n_suppressed_from_notify is NOT a drop (persisted)."""
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "3")
    monkeypatch.setattr(hub, "save_finding", lambda f, now_ts=None: True)
    monkeypatch.setattr(hub, "notify", lambda f: _async_none())
    monkeypatch.setattr(hub, "should_send_digest", lambda *a, **kw: (False, "h", "throttled"))
    monkeypatch.setattr(hub, "record_digest_throttled", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "bump_last_seen", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "reconcile_scan", lambda *a, **kw: ([], [], []))
    monkeypatch.setattr(hub, "_dispatch_regression_notifications", lambda *a, **kw: _async_none())

    candidates = [_make_finding(f"CVE-{i}", cvss=9.0 - i * 0.01) for i in range(10)]
    asyncio.run(hub._ingest_candidates("host-a", candidates, now_ts=0, sbom_count=5))
    events = _audit_from_env(hub_env)
    ev = next(e for e in events if e.get("event") == "scan_integrity")
    bucket_sum = int(ev["n_dedup"]) + int(ev["n_dropped_in_save"]) + int(ev["n_filtered_other"])
    assert int(ev["n_candidates"]) == int(ev["n_persisted"]) + bucket_sum


# ── verify-integrity tolerates both audit shapes ───────────────────


def test_verifier_tolerates_pre_bc128_n_capped_field() -> None:
    """Pre-BC-128 scan_integrity has `n_capped` (= suppressed, which was
    a drop then). Verifier must still treat it as a drop so the balance
    invariant holds for historical events."""
    from poc.scripts import verify_integrity as vi

    pre_bc128_event = {
        "event": "scan_integrity",
        "transfer_match": True,
        "n_candidates": 10,
        "n_persisted": 3,
        "n_dedup": 2,
        "n_capped": 4,  # OLD shape: suppressed-from-notify was a drop
        "n_dropped_in_save": 1,
        "n_filtered_other": 0,
    }
    report = vi.build_report([pre_bc128_event])
    assert report.exit_code == 0
    assert not report.unexplained_drops


def test_verifier_tolerates_post_bc128_n_suppressed_from_notify() -> None:
    """Post-BC-128 scan_integrity has `n_suppressed_from_notify` which is
    NOT a drop (those findings ARE persisted). Verifier must NOT include
    it in the drop sum."""
    from poc.scripts import verify_integrity as vi

    post_bc128_event = {
        "event": "scan_integrity",
        "transfer_match": True,
        "n_candidates": 10,
        "n_persisted": 7,  # includes the previously-suppressed tail
        "n_notified": 3,
        "n_dedup": 2,
        "n_suppressed_from_notify": 4,  # NEW shape: persisted, not dropped
        "n_dropped_in_save": 1,
        "n_filtered_other": 0,
    }
    report = vi.build_report([post_bc128_event])
    assert report.exit_code == 0
    assert not report.unexplained_drops


def test_verifier_catches_truly_unexplained_drop() -> None:
    """Regression guard: if persisted + known drops != candidates, flag."""
    from poc.scripts import verify_integrity as vi

    bad_event = {
        "event": "scan_integrity",
        "transfer_match": True,
        "n_candidates": 100,
        "n_persisted": 50,
        "n_dedup": 10,
        "n_suppressed_from_notify": 5,
        "n_dropped_in_save": 5,
        "n_filtered_other": 0,  # 50 + 10 + 5 + 0 = 65 ≠ 100; delta 35 unexplained
    }
    report = vi.build_report([bad_event])
    assert report.exit_code != 0
    assert len(report.unexplained_drops) == 1
