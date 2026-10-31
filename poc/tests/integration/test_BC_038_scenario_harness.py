"""BC-038 — full scenario suite run in-process against a TEST_MODE hub.

Covers:
    - CAP-T082: scenario harness runs end-to-end against a real hub binary
      (in-process via FastAPI TestClient, same code path as `make test-scenarios`)
    - CAP-T083: /test/* endpoints registered only when BEACONRY_TEST_MODE=1
    - CAP-B022: operator-facing promises verified across BCs 020/030/031/034/035/036
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("BEACONRY_TEST_MODE", "1")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "5")
    # Match the scenarios compose.yml defaults so s08 interval math is predictable.
    monkeypatch.setenv("BEACONRY_HEARTBEAT_INTERVAL_S", "86400")
    monkeypatch.setenv("BEACONRY_DIGEST_INTERVAL_S", "86400")

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


def test_full_suite_passes(hub_env: dict) -> None:
    """All 10 shipped scenarios pass against the in-process hub.
    This is the same code path `make test-scenarios` runs — minus Docker."""
    from poc.scenarios._context import ScenarioContext
    from poc.scenarios._registry import SCENARIOS

    ctx = ScenarioContext(
        hub_url=hub_env["hub_url"],
        db_path=hub_env["db_path"],
        audit_path=hub_env["audit_path"],
        http_client=hub_env["http"],
    )
    failures: list[str] = []
    for scenario in sorted(SCENARIOS, key=lambda s: s.order):
        ctx.wipe()
        try:
            scenario.run(ctx)
        except AssertionError as exc:
            failures.append(f"{scenario.name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{scenario.name}: {exc.__class__.__name__}: {exc}")
    assert not failures, "scenario failures:\n  " + "\n  ".join(failures)


def test_endpoints_registered_with_flag(hub_env: dict) -> None:
    """TEST_MODE=1 → /test/wipe reachable."""
    client: TestClient = hub_env["http"]
    r = client.post("/test/wipe")
    assert r.status_code == 200
    assert r.json()["status"] == "wiped"


def test_endpoints_404_without_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST_MODE unset → /test/* routes do not exist → 404."""
    monkeypatch.delenv("BEACONRY_TEST_MODE", raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    import hub

    importlib.reload(hub)
    client = TestClient(hub.api)
    try:
        for path, body in [
            ("/test/wipe", None),
            ("/test/scan", {"host": "h", "candidates": []}),
            ("/test/sweep", {"at": 0}),
        ]:
            r = client.post(path, json=body)
            assert r.status_code == 404, (
                f"{path} must be 404 without TEST_MODE, got {r.status_code}"
            )
    finally:
        client.close()
