"""BC-043 — CVE alias on non-CVE primary ids (ingest-to-notification chain).

Covers:
    - CAP-T091: Finding.cve_alias field + DB migration + round-trip
    - CAP-T092: format_notification / regression / heartbeat surface the alias
    - CAP-B027: Operator always has a CVE paper trail for non-CVE primaries
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import hub
import pytest

from poc.hub import kev as _kev


@pytest.fixture
def bc043_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    _kev._reset_state_for_tests()
    hub.init_db()
    return data_dir


def _mk_match(
    cve_id: str,
    *,
    cvss: float = 9.5,
    related: list[str] | None = None,
    severity: str = "Critical",
) -> dict[str, Any]:
    return {
        "vulnerability": {
            "id": cve_id,
            "severity": severity,
            "cvss": [{"metrics": {"baseScore": cvss}}],
            "description": "test",
            "fix": {"versions": ["2.0"]},
        },
        "artifact": {"name": "stdlib", "version": "1.0"},
        "relatedVulnerabilities": [{"id": rid} for rid in (related or [])],
    }


async def test_cve_primary_no_alias(bc043_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CVE primary → cve_alias stays None even if related has entries."""

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [_mk_match("CVE-2026-X", related=["GHSA-xxxx"])]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    findings = await hub.process_sbom_entry(
        {"source_meta": {"type": "docker", "image": "nginx:1.24"}, "sbom": {}},
        host="h",
    )
    assert len(findings) == 1
    assert findings[0].cve_id == "CVE-2026-X"
    assert findings[0].cve_alias is None


async def test_non_cve_primary_resolves_alias(
    bc043_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-CVE primary + CVE in related → alias stored."""

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [_mk_match("GO-2026-6604", related=["CVE-2024-12345", "GHSA-xxxx"])]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    findings = await hub.process_sbom_entry(
        {"source_meta": {"type": "docker", "image": "postgres:16-alpine"}, "sbom": {}},
        host="h",
    )
    f = findings[0]
    assert f.cve_id == "GO-2026-6604"
    assert f.cve_alias == "CVE-2024-12345"


async def test_non_cve_primary_no_cve_alias(
    bc043_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-CVE primary + no CVE in related → cve_alias stays None."""

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [_mk_match("GO-2026-7777", related=["GHSA-xxxx", "GHSA-yyyy"])]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    findings = await hub.process_sbom_entry(
        {"source_meta": {"type": "docker", "image": "postgres:16-alpine"}, "sbom": {}},
        host="h",
    )
    f = findings[0]
    assert f.cve_alias is None


async def test_kev_non_cve_primary_shows_both_paths(
    bc043_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BC-043 motivation: non-CVE primary that's KEV-via-alias — notification
    body must carry BOTH the 'via <alias>' line AND the inline alias."""
    import time as _time

    kev_cve = "CVE-2024-KEV-ALIAS"
    cache_path = bc043_env / "kev_cache.json"
    cache_path.write_text(json.dumps({"fetched_at": int(_time.time()) - 60, "cve_ids": [kev_cve]}))

    def _mock_fail(_url: str) -> bytes:
        import urllib.error

        raise urllib.error.URLError("induced")

    monkeypatch.setattr(_kev, "_http_fetch", _mock_fail)
    await _kev.fetch_or_fallback()
    assert _kev.is_known_exploited(kev_cve) is True

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [_mk_match("GO-2026-SPECIAL", cvss=7.5, related=[kev_cve])]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    findings = await hub.process_sbom_entry(
        {"source_meta": {"type": "docker", "image": "postgres:16-alpine"}, "sbom": {}},
        host="h",
    )
    f = findings[0]
    assert f.kev is True
    assert f.tier == 0
    assert f.cve_alias == kev_cve

    body = hub.format_notification(f)
    assert "KEV — EXPLOITED IN THE WILD" in body
    assert f"via {kev_cve}" in body
    assert "GO-2026-SPECIAL" in body
    assert kev_cve in body  # also on severity line


async def test_audit_event_carries_cve_alias(
    bc043_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """finding_ingested audit event must carry cve_alias for forensic chain."""

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [_mk_match("GO-2026-AUDIT", related=["CVE-2024-AUDIT"])]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    findings = await hub.process_sbom_entry(
        {"source_meta": {"type": "docker", "image": "postgres:16-alpine"}, "sbom": {}},
        host="h",
    )
    assert hub.save_finding(findings[0]) is True

    audit_path = hub.DATA_DIR / "audit.log"
    events = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    ingested = [e for e in events if e["event"] == "finding_ingested"]
    assert len(ingested) == 1
    assert ingested[0]["cve_alias"] == "CVE-2024-AUDIT"
