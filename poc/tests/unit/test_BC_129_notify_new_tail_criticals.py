"""BC-129 — Loud alert for every newly-persisted Tier ≤ 1 finding, even in tail.

Covers:
    - CAP-T232: After `_notify_top_n` (BC-128 Phase 2), `_ingest_candidates`
      runs a second pass over `suppressed` and fires `notify()` for any
      newly-persisted finding where `tier <= 1`. Pre-existing tail items
      don't re-notify. Tier ≥ 2 tail items remain digest-only. The
      `scan_integrity` audit event grows `n_tail_criticals_notified`.
      `verify-integrity` tolerates pre-BC-129 events missing the field.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from poc.hub import hub
from poc.hub.executor.base import Finding


def _make_finding(
    cve_id: str, *, cvss: float = 9.5, tier: int = 1, fid: str | None = None
) -> Finding:
    return Finding(
        id=fid or f"fid-{cve_id}",
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


async def _async_none():
    return None


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


def _wire_mocks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    save_returns: dict[str, bool] | bool = True,
    notified_ids: list[str] | None = None,
) -> None:
    """Common monkeypatch setup. `save_returns` can be a bool (all return
    same) or a dict mapping finding.id → True/False to simulate dups."""
    if notified_ids is None:
        notified_ids = []

    def fake_save(f, now_ts=None):
        if isinstance(save_returns, dict):
            return save_returns.get(f.id, True)
        return save_returns

    async def fake_notify(f):
        notified_ids.append(f.id)

    monkeypatch.setattr(hub, "save_finding", fake_save)
    monkeypatch.setattr(hub, "notify", fake_notify)
    monkeypatch.setattr(hub, "send_digest", lambda *a, **kw: _async_none())
    monkeypatch.setattr(hub, "should_send_digest", lambda *a, **kw: (False, "h", "throttled"))
    monkeypatch.setattr(hub, "record_digest_throttled", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "record_digest_sent", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "bump_last_seen", lambda *a, **kw: None)
    monkeypatch.setattr(hub, "reconcile_scan", lambda *a, **kw: ([], [], []))
    monkeypatch.setattr(hub, "_dispatch_regression_notifications", lambda *a, **kw: _async_none())


def _audit_from_env(hub_env: Path) -> list[dict]:
    audit = hub_env / "audit.log"
    return [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]


# ── Scenario 1 — new tail Critical fires loud alert ─────────────────


def test_new_tail_critical_fires_notify(hub_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rank 1-100 pre-existing (save returns False). Rank 101+ newly
    persisted Tier 1 Critical must fire notify()."""
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "100")
    notified_ids: list[str] = []
    # 100 highest-ranked exist in DB; 5 lower-ranked are new Tier 1
    top_100 = [_make_finding(f"TOP-{i:03d}", cvss=9.99 - i * 0.001, tier=1) for i in range(100)]
    new_tail = [_make_finding(f"NEW-{i:03d}", cvss=9.0 - i * 0.01, tier=1) for i in range(5)]
    save_returns = {f.id: False for f in top_100}  # all top-100 are dups
    save_returns.update({f.id: True for f in new_tail})  # tail are new
    _wire_mocks(monkeypatch, save_returns=save_returns, notified_ids=notified_ids)

    candidates = top_100 + new_tail
    asyncio.run(hub._ingest_candidates("host-a", candidates, now_ts=0, sbom_count=5))
    tail_ids = {f.id for f in new_tail}
    notified_set = set(notified_ids)
    assert tail_ids <= notified_set, (
        f"all {len(tail_ids)} new tail-Criticals must notify; got {notified_set & tail_ids}"
    )


# ── Scenario 2 — Tier 2 tail stays silent ───────────────────────────


def test_tier_2_tail_stays_silent(hub_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A newly-persisted Tier 2 finding in the suppressed tail must NOT
    fire notify() — digest-only (BC-022 semantics)."""
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "10")
    notified_ids: list[str] = []
    # 10 pre-existing in top-N, 5 newly-persisted Tier 2 Highs in tail.
    top = [_make_finding(f"TOP-{i:02d}", cvss=9.5 - i * 0.01, tier=1) for i in range(10)]
    tail_tier2 = [_make_finding(f"T2-{i:02d}", cvss=7.5 - i * 0.01, tier=2) for i in range(5)]
    save_returns = {f.id: False for f in top}
    save_returns.update({f.id: True for f in tail_tier2})
    _wire_mocks(monkeypatch, save_returns=save_returns, notified_ids=notified_ids)

    asyncio.run(hub._ingest_candidates("host-a", top + tail_tier2, now_ts=0, sbom_count=5))
    tail_ids = {f.id for f in tail_tier2}
    assert not (tail_ids & set(notified_ids)), (
        f"Tier 2 tail items must not notify; got {tail_ids & set(notified_ids)}"
    )


# ── Scenario 3 — Tail KEV always fires loud ──────────────────────────


def test_tail_kev_always_notifies(hub_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier 0 KEV in the tail must fire — the strictest SLA."""
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "5")
    notified_ids: list[str] = []
    # 5 pre-existing tier-1 top-N + 1 NEW tier-0 KEV that ranks top (tier 0 < 1)
    # Note: dedup_and_cap sorts by (tier, -cvss), so a tier-0 ranks ABOVE
    # tier-1. To force the KEV into the tail, we make 5 OTHER tier-0 items
    # that are already in DB (dups), then the 6th new tier-0 lands at rank 6.
    top_tier0_dups = [_make_finding(f"T0-DUP-{i}", cvss=9.9, tier=0) for i in range(5)]
    new_tail_kev = _make_finding("T0-NEW", cvss=9.0, tier=0, fid="fid-new-kev")
    save_returns = {f.id: False for f in top_tier0_dups}
    save_returns[new_tail_kev.id] = True
    _wire_mocks(monkeypatch, save_returns=save_returns, notified_ids=notified_ids)

    asyncio.run(
        hub._ingest_candidates("host-a", [*top_tier0_dups, new_tail_kev], now_ts=0, sbom_count=5)
    )
    assert new_tail_kev.id in notified_ids, "tail KEV must fire loud alert"


# ── Scenario 4 — Pre-existing tail Critical does NOT re-notify ──────


def test_preexisting_tail_critical_no_renotify(
    hub_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Critical that's been in the DB for a while and still sits in the
    tail this scan does not re-notify. `save_finding` returning False
    (dup) is the signal."""
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "10")
    notified_ids: list[str] = []
    top = [_make_finding(f"TOP-{i:02d}", cvss=9.5 - i * 0.01, tier=1) for i in range(10)]
    stale_tail = [_make_finding(f"STALE-{i:02d}", cvss=9.0 - i * 0.01, tier=1) for i in range(5)]
    # ALL are dups (nothing new this scan)
    save_returns = {f.id: False for f in top + stale_tail}
    _wire_mocks(monkeypatch, save_returns=save_returns, notified_ids=notified_ids)

    asyncio.run(hub._ingest_candidates("host-a", top + stale_tail, now_ts=0, sbom_count=5))
    assert notified_ids == [], f"no notifications when nothing is new; got {notified_ids}"


# ── Scenario 5 — balance invariant unchanged ────────────────────────


def test_scan_integrity_balance_invariant_bc129(
    hub_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BC-129 is additive — balance invariant from BC-128 holds unchanged."""
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "3")
    _wire_mocks(monkeypatch)
    candidates = [_make_finding(f"CVE-{i}", cvss=9.0 - i * 0.01, tier=1) for i in range(10)]
    asyncio.run(hub._ingest_candidates("host-a", candidates, now_ts=0, sbom_count=5))
    ev = next(e for e in _audit_from_env(hub_env) if e.get("event") == "scan_integrity")
    bucket_sum = int(ev["n_dedup"]) + int(ev["n_dropped_in_save"]) + int(ev["n_filtered_other"])
    assert int(ev["n_candidates"]) == int(ev["n_persisted"]) + bucket_sum


# ── Scenario 6 — audit event has n_tail_criticals_notified field ────


def test_scan_integrity_has_n_tail_criticals_notified_field(
    hub_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Field must be present on every post-BC-129 scan_integrity event."""
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "3")
    _wire_mocks(monkeypatch)
    candidates = [_make_finding(f"CVE-{i}", cvss=9.0 - i * 0.01, tier=1) for i in range(10)]
    asyncio.run(hub._ingest_candidates("host-a", candidates, now_ts=0, sbom_count=5))
    ev = next(e for e in _audit_from_env(hub_env) if e.get("event") == "scan_integrity")
    assert "n_tail_criticals_notified" in ev


def test_n_tail_criticals_notified_counts_correctly(
    hub_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Field value equals the actual number of tail-Critical loud alerts."""
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "5")
    notified_ids: list[str] = []
    top = [_make_finding(f"TOP-{i}", cvss=9.9 - i * 0.001, tier=1) for i in range(5)]
    tail_criticals = [_make_finding(f"TAIL-{i}", cvss=9.0 - i * 0.01, tier=1) for i in range(3)]
    save_returns = {f.id: False for f in top}
    save_returns.update({f.id: True for f in tail_criticals})
    _wire_mocks(monkeypatch, save_returns=save_returns, notified_ids=notified_ids)
    asyncio.run(hub._ingest_candidates("host-a", top + tail_criticals, now_ts=0, sbom_count=5))
    ev = next(e for e in _audit_from_env(hub_env) if e.get("event") == "scan_integrity")
    assert ev["n_tail_criticals_notified"] == 3


# ── Scenario 7 — verify-integrity backward-compat for pre-BC-129 events ─


def test_verify_integrity_backward_compat_pre_bc129() -> None:
    """Pre-BC-129 scan_integrity events have no `n_tail_criticals_notified`
    field. verify-integrity's balance invariant must not reference it."""
    from poc.scripts import verify_integrity as vi

    pre_bc129_event = {
        "event": "scan_integrity",
        "transfer_match": True,
        "n_candidates": 10,
        "n_persisted": 7,
        "n_notified": 3,
        "n_dedup": 2,
        "n_suppressed_from_notify": 4,
        "n_dropped_in_save": 1,
        "n_filtered_other": 0,
        # NO n_tail_criticals_notified
    }
    report = vi.build_report([pre_bc129_event])
    assert report.exit_code == 0
    assert not report.unexplained_drops
