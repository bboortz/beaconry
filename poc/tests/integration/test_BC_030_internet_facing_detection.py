"""BC-030 — internet_facing detection → Tier 1 fires.

Covers:
    - CAP-T068: Scout emits per-container port-publication map
    - CAP-T069: Hub joins finding to port map at ingest; honours
      BEACONRY_INTERNET_FACING_DETECTION_ENABLED
    - CAP-T070: `make findings` + Telegram notification surface exposure dimension
    - CAP-B020: Operator sees internet-facing CVEs ranked above LAN-only ones
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import hub
import pytest

from poc.hub import kev as _kev

NON_KEV_CVE_ID = "CVE-2026-BC030-A"
KEV_CVE_ID = "CVE-2026-BC030-KEV"


@pytest.fixture
def bc030_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate hub DB + KEV state. Enable detection by default."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setattr(hub, "INTERNET_FACING_DETECTION_ENABLED", True)
    _kev._reset_state_for_tests()
    hub.init_db()
    return data_dir


def _make_grype_match(cve_id: str, cvss: float) -> dict[str, Any]:
    return {
        "vulnerability": {
            "id": cve_id,
            "severity": "Critical",
            "cvss": [{"metrics": {"baseScore": cvss}}],
            "description": "test",
            "fix": {"versions": ["2.0"]},
        },
        "artifact": {"name": "testpkg", "version": "1.0"},
    }


async def test_exposed_container_tier_1(bc030_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CVSS ≥ 9.0 + container published to 0.0.0.0 → finding.internet_facing=True,
    finding.tier=1 (not 2)."""

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [_make_grype_match(NON_KEV_CVE_ID, 9.5)]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    sbom_entry = {
        "source_meta": {"type": "docker", "image": "nginx:1.24"},
        "sbom": {"packages": []},
    }
    port_map = {"nginx:1.24": True}
    findings = await hub.process_sbom_entry(sbom_entry, host="h", port_exposure=port_map)

    assert len(findings) == 1
    f = findings[0]
    assert f.internet_facing is True
    assert f.tier == 1, f"CVSS≥9 + exposed → Tier 1, got tier={f.tier}"


async def test_no_ports_tier_2(bc030_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CVSS ≥ 9.0 + container with no port map entry → internet_facing=False, tier=2."""

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [_make_grype_match(NON_KEV_CVE_ID, 9.5)]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    sbom_entry = {
        "source_meta": {"type": "docker", "image": "redis:7-alpine"},
        "sbom": {"packages": []},
    }
    port_map: dict[str, bool] = {}  # no running container for this image
    findings = await hub.process_sbom_entry(sbom_entry, host="h", port_exposure=port_map)

    f = findings[0]
    assert f.internet_facing is False
    assert f.tier == 2


async def test_loopback_only_not_exposed(bc030_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scout marks loopback-only publications as internet_facing=False → hub
    receives False → Tier 2 even at CVSS 9.5."""

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [_make_grype_match(NON_KEV_CVE_ID, 9.5)]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    sbom_entry = {
        "source_meta": {"type": "docker", "image": "nginx:1.24"},
        "sbom": {"packages": []},
    }
    # Scout's job to parse 127.0.0.1 as False — hub just trusts the dict.
    port_map = {"nginx:1.24": False}
    findings = await hub.process_sbom_entry(sbom_entry, host="h", port_exposure=port_map)

    f = findings[0]
    assert f.internet_facing is False
    assert f.tier == 2


async def test_kev_wins_over_internet_facing(
    bc030_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BC-020 regression guard: KEV CVE always gets Tier 0, even on a non-exposed
    container. internet_facing must not downgrade a KEV finding."""
    import json
    import time as _time

    cache_path = bc030_env / "kev_cache.json"
    cache_path.write_text(
        json.dumps({"fetched_at": int(_time.time()) - 60, "cve_ids": [KEV_CVE_ID]})
    )

    def _mock_fail(_url: str) -> bytes:
        import urllib.error

        raise urllib.error.URLError("induced")

    monkeypatch.setattr(_kev, "_http_fetch", _mock_fail)
    await _kev.fetch_or_fallback()
    assert _kev.is_known_exploited(KEV_CVE_ID) is True

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [_make_grype_match(KEV_CVE_ID, 7.5)]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    sbom_entry = {
        "source_meta": {"type": "docker", "image": "redis:7-alpine"},
        "sbom": {"packages": []},
    }
    # Not exposed — but KEV should still win.
    port_map = {"redis:7-alpine": False}
    findings = await hub.process_sbom_entry(sbom_entry, host="h", port_exposure=port_map)

    f = findings[0]
    assert f.kev is True
    assert f.tier == 0, "KEV always wins over internet_facing"


async def test_detection_disabled_forces_lan_only(
    bc030_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BEACONRY_INTERNET_FACING_DETECTION_ENABLED=0 → hub ignores port map,
    forces internet_facing=False regardless of scout's input."""
    monkeypatch.setattr(hub, "INTERNET_FACING_DETECTION_ENABLED", False)

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [_make_grype_match(NON_KEV_CVE_ID, 9.5)]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    sbom_entry = {
        "source_meta": {"type": "docker", "image": "nginx:1.24"},
        "sbom": {"packages": []},
    }
    # Scout claims it's exposed — hub ignores because the toggle is off.
    port_map = {"nginx:1.24": True}
    findings = await hub.process_sbom_entry(sbom_entry, host="h", port_exposure=port_map)

    f = findings[0]
    assert f.internet_facing is False, (
        "detection disabled must force False even when scout says True"
    )
    assert f.tier == 2


async def test_audit_event_carries_internet_facing(
    bc030_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BC-029 integration: save_finding emits an audit event that includes
    `internet_facing`. Needed so forensics can prove which findings were ranked
    as Tier 1 at ingest time (post-fact DB inspection isn't enough — tier may
    change on re-ingest if the toggle flips)."""
    import json

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [_make_grype_match("CVE-2026-AUDIT-030", 9.5)]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    sbom_entry = {
        "source_meta": {"type": "docker", "image": "nginx:1.24"},
        "sbom": {"packages": []},
    }
    findings = await hub.process_sbom_entry(
        sbom_entry, host="h", port_exposure={"nginx:1.24": True}
    )
    assert hub.save_finding(findings[0]) is True

    audit_path = hub.DATA_DIR / "audit.log"
    events = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    ingested = [e for e in events if e["event"] == "finding_ingested"]
    assert len(ingested) == 1
    assert ingested[0]["internet_facing"] is True
    assert ingested[0]["tier"] == 1


async def test_missing_port_exposure_arg_defaults_safe(
    bc030_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backward compat: calling process_sbom_entry without the port_exposure arg
    (older scouts, direct test code) treats everything as not exposed."""

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [_make_grype_match(NON_KEV_CVE_ID, 9.5)]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    sbom_entry = {
        "source_meta": {"type": "docker", "image": "nginx:1.24"},
        "sbom": {"packages": []},
    }
    findings = await hub.process_sbom_entry(sbom_entry, host="h")

    f = findings[0]
    assert f.internet_facing is False
    assert f.tier == 2
