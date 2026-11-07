"""BC-096 — SBOM at builder stage + /sbom endpoint.

Covers:
    - CAP-T197: Baked-in SBOM at builder stage + /sbom endpoint on
      hub + scout. Both Dockerfiles call `syft dir:/install -o
      syft-json=/sbom.json` in the builder and `COPY --from=builder
      /sbom.json /sbom.json` in the runtime. GET /sbom serves the
      baked-in file as application/json; 503 when the file is missing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poc.hub import hub as hub_mod
from poc.scout import scout as scout_mod

REPO = Path(__file__).resolve().parents[3]
HUB_DF = REPO / "poc" / "hub" / "Dockerfile"
SCOUT_DF = REPO / "poc" / "scout" / "Dockerfile"


def _hub_df() -> str:
    return HUB_DF.read_text()


def _scout_df() -> str:
    return SCOUT_DF.read_text()


# ── Dockerfile lint tests ────────────────────────────────────────────────────


def test_hub_builder_runs_syft() -> None:
    """Hub builder stage must generate /sbom.json via syft."""
    body = _hub_df()
    assert re.search(
        r"^RUN\s+syft\s+dir:/install\s+-o\s+syft-json=/sbom\.json\s*$",
        body,
        flags=re.MULTILINE,
    ), "hub/Dockerfile missing `RUN syft dir:/install -o syft-json=/sbom.json`"


def test_hub_runtime_copies_sbom() -> None:
    """Hub runtime stage must COPY /sbom.json from builder."""
    body = _hub_df()
    assert re.search(
        r"^COPY\s+--from=builder\s+/sbom\.json\s+/sbom\.json\s*$",
        body,
        flags=re.MULTILINE,
    ), "hub/Dockerfile missing `COPY --from=builder /sbom.json /sbom.json`"


def test_scout_builder_runs_syft() -> None:
    body = _scout_df()
    assert re.search(
        r"^RUN\s+syft\s+dir:/install\s+-o\s+syft-json=/sbom\.json\s*$",
        body,
        flags=re.MULTILINE,
    ), "scout/Dockerfile missing `RUN syft dir:/install -o syft-json=/sbom.json`"


def test_scout_runtime_copies_sbom() -> None:
    body = _scout_df()
    assert re.search(
        r"^COPY\s+--from=builder\s+/sbom\.json\s+/sbom\.json\s*$",
        body,
        flags=re.MULTILINE,
    ), "scout/Dockerfile missing `COPY --from=builder /sbom.json /sbom.json`"


# ── /sbom endpoint tests ─────────────────────────────────────────────────────


@pytest.fixture
def sbom_fixture(tmp_path: Path) -> Path:
    """Minimal but structurally-valid syft-json SBOM for /sbom responses."""
    p = tmp_path / "sbom.json"
    p.write_text(
        json.dumps(
            {
                "artifacts": [
                    {"name": "fastapi", "version": "0.115.0", "type": "python"},
                ],
                "source": {"type": "directory", "target": "/install"},
                "distro": {},
                "descriptor": {"name": "syft", "version": "1.57.0"},
                "schema": {"version": "16.0.0"},
            }
        )
    )
    return p


def test_hub_sbom_endpoint_returns_file(
    sbom_fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub_mod, "SBOM_PATH", str(sbom_fixture))
    client = TestClient(hub_mod.api)
    r = client.get("/sbom")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert "artifacts" in body, "syft-json shape: top-level `artifacts` key"


def test_scout_sbom_endpoint_returns_file(
    sbom_fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scout_mod, "SBOM_PATH", str(sbom_fixture))
    client = TestClient(scout_mod.app)
    r = client.get("/sbom")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert "artifacts" in body


def test_sbom_endpoint_503_without_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev/host runs without a baked image must degrade to 503, not 500."""
    missing = tmp_path / "does-not-exist.json"
    monkeypatch.setattr(hub_mod, "SBOM_PATH", str(missing))
    monkeypatch.setattr(scout_mod, "SBOM_PATH", str(missing))

    hub_client = TestClient(hub_mod.api)
    scout_client = TestClient(scout_mod.app)

    for client in (hub_client, scout_client):
        r = client.get("/sbom")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "sbom_unavailable"
