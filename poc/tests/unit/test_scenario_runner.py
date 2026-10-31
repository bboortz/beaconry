"""BC-038 — ScenarioContext primitives + Scenario base-class contract.

Covers:
    - CAP-T082: Live-stack scenario harness runner — context primitives
      (find, assert_status, audit_events, assert_audit_fired) against a
      tmp SQLite + in-process httpx client wrapping FastAPI TestClient.
    - CAP-T083: Hub test-mode endpoints — route-registration guard
      verified by test_test_endpoints_absent_without_flag.
    - CAP-B022: operator-facing promise (scenarios run deterministically)
      exercised via the primitives this test covers.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def scenario_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    """Build a hub app with TEST_MODE=1 under a tmp data dir, wrap its
    TestClient in an httpx-shaped adapter so ScenarioContext thinks it's
    talking to a real hub."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("BEACONRY_TEST_MODE", "1")
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    # Re-import hub with the env in place so TEST_MODE evaluates True.
    import importlib

    import hub

    importlib.reload(hub)

    client = TestClient(hub.api)
    yield {
        "hub_url": "",  # TestClient uses relative paths
        "db_path": data_dir / "beaconry.db",
        "audit_path": data_dir / "audit.log",
        "http": client,
        "hub": hub,
    }
    client.close()
    # BC-038: tests reload hub.py to apply env changes — other tests inherit
    # that module state. Reload once more with the monkeypatched env removed
    # so subsequent test collection sees the baseline hub module.
    monkeypatch.delenv("BEACONRY_TEST_MODE", raising=False)
    importlib.reload(hub)


def test_scenario_base_is_abstract() -> None:
    """Scenario cannot be instantiated directly — must be subclassed."""
    from poc.scenarios._base import Scenario

    with pytest.raises(TypeError):
        Scenario()  # type: ignore[abstract]


def test_registry_contains_ten_scenarios() -> None:
    """BC-038 ships 10 scenarios; registry must enumerate them explicitly."""
    from poc.scenarios._registry import SCENARIOS

    assert len(SCENARIOS) == 10
    # Orders must be unique + 1-indexed sequential.
    orders = sorted(s.order for s in SCENARIOS)
    assert orders == list(range(1, 11)), f"orders must be 1..10, got {orders}"
    # Names must be unique.
    names = [s.name for s in SCENARIOS]
    assert len(set(names)) == len(names), f"duplicate scenario names: {names}"


def test_context_wipe_resets_db_and_audit(scenario_env: dict) -> None:
    from poc.scenarios._context import ScenarioContext

    ctx = ScenarioContext(
        hub_url=scenario_env["hub_url"],
        db_path=scenario_env["db_path"],
        audit_path=scenario_env["audit_path"],
        http_client=scenario_env["http"],
    )
    ctx.wipe()
    # DB is re-created by init_db — table exists, zero rows.
    assert scenario_env["db_path"].exists()
    assert ctx.count_findings() == 0


def test_context_inject_scan_persists_candidate(scenario_env: dict) -> None:
    from poc.scenarios._context import ScenarioContext
    from poc.scenarios._fixtures import cand

    ctx = ScenarioContext(
        hub_url=scenario_env["hub_url"],
        db_path=scenario_env["db_path"],
        audit_path=scenario_env["audit_path"],
        http_client=scenario_env["http"],
    )
    ctx.wipe()
    ctx.inject_scan([cand(cve_id="CVE-RUN-01", cvss=9.5)])
    row = ctx.find("CVE-RUN-01")
    assert row is not None
    assert row["status"] == "open"
    assert row["cvss"] == 9.5


def test_context_assert_status_mismatch_raises(scenario_env: dict) -> None:
    from poc.scenarios._context import ScenarioContext
    from poc.scenarios._fixtures import cand

    ctx = ScenarioContext(
        hub_url=scenario_env["hub_url"],
        db_path=scenario_env["db_path"],
        audit_path=scenario_env["audit_path"],
        http_client=scenario_env["http"],
    )
    ctx.wipe()
    ctx.inject_scan([cand(cve_id="CVE-RUN-02", cvss=9.5)])
    # Correct expectation passes.
    ctx.assert_status("CVE-RUN-02", "open")
    # Wrong expectation raises AssertionError.
    with pytest.raises(AssertionError, match="expected status='fixed'"):
        ctx.assert_status("CVE-RUN-02", "fixed")


def test_context_audit_events_filter(scenario_env: dict) -> None:
    from poc.scenarios._context import ScenarioContext
    from poc.scenarios._fixtures import cand

    ctx = ScenarioContext(
        hub_url=scenario_env["hub_url"],
        db_path=scenario_env["db_path"],
        audit_path=scenario_env["audit_path"],
        http_client=scenario_env["http"],
    )
    ctx.wipe()
    ctx.inject_scan([cand(cve_id="CVE-RUN-03", cvss=9.5)])

    all_events = ctx.audit_events()
    ingested = ctx.audit_events("finding_ingested")
    assert len(ingested) == 1
    assert ingested[0]["cve_id"] == "CVE-RUN-03"
    assert len(ingested) <= len(all_events)

    ctx.assert_audit_fired("finding_ingested", cve_id="CVE-RUN-03", count=1)
    with pytest.raises(AssertionError, match="expected zero 'finding_ingested'"):
        ctx.assert_audit_absent("finding_ingested", cve_id="CVE-RUN-03")


def test_test_endpoints_absent_without_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T083 regression guard — without BEACONRY_TEST_MODE=1, /test/* routes
    are not registered → 404."""
    monkeypatch.delenv("BEACONRY_TEST_MODE", raising=False)
    data_dir = tmp_path / "nope"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    import importlib

    import hub

    importlib.reload(hub)
    client = TestClient(hub.api)
    r = client.post("/test/scan", json={"host": "h", "candidates": []})
    assert r.status_code == 404
    r = client.post("/test/wipe")
    assert r.status_code == 404
    r = client.post("/test/sweep", json={"at": 0})
    assert r.status_code == 404
    client.close()
    # Reset hub import state for other tests.
    monkeypatch.setenv("BEACONRY_TEST_MODE", "1")
    importlib.reload(hub)
