"""BC-020 — KEV ingestion + wire urgency.classify_tier into the hub ingestion path.

Covers:
    - CAP-B019: Operator sees KEV CVEs visually distinct and ranked first
    - CAP-T058: CISA KEV feed ingestion with daily refresh + stale fallback
    - CAP-T059: Hub ingest path wires urgency.classify_tier → DB tier column
    - CAP-T060: Notification format inserts a KEV header line for tier-0 findings
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import hub
import pytest

from poc.hub import kev as _kev
from poc.scripts import findings as findings_cli

# ── Fixtures ─────────────────────────────────────────────────────────────────

FEED_CVE_ID = "CVE-2026-9999"
NON_KEV_CVE_ID = "CVE-2026-1111"

_FAKE_FEED = {
    "title": "CISA KEV (test fixture)",
    "catalogVersion": "test-1",
    "count": 1,
    "vulnerabilities": [
        {
            "cveID": FEED_CVE_ID,
            "vendorProject": "fixture",
            "product": "fixture",
            "vulnerabilityName": "fixture",
            "dateAdded": "2026-10-31",
            "shortDescription": "test",
            "requiredAction": "test",
            "dueDate": "2026-11-14",
            "knownRansomwareCampaignUse": "Unknown",
        }
    ],
}


@pytest.fixture
def kev_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate KEV cache + hub DB under tmp_path. Reset in-memory KEV state."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    _kev._reset_state_for_tests()
    return data_dir


def _mock_http_fetch_success(url: str) -> bytes:  # noqa: ARG001
    return json.dumps(_FAKE_FEED).encode()


def _mock_http_fetch_fail(url: str) -> bytes:  # noqa: ARG001
    import urllib.error

    raise urllib.error.URLError("test-induced-failure")


# ── Scenario 1 ───────────────────────────────────────────────────────────────


async def test_boot_fetch_populates_cache(kev_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_kev, "_http_fetch", _mock_http_fetch_success)
    await _kev.fetch_or_fallback()
    assert _kev.is_known_exploited(FEED_CVE_ID) is True
    cache_path = kev_env / "kev_cache.json"
    assert cache_path.exists(), "fetch_or_fallback must persist cache to disk"
    assert FEED_CVE_ID in cache_path.read_text()
    summary = _kev.freshness()
    assert summary["kev"] == "ok"
    assert summary["kev_count"] == 1


# ── Scenarios 2, 3 ───────────────────────────────────────────────────────────


async def test_kev_cve_classified_tier_0(kev_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A CVE in the KEV catalog gets tier=0 even with modest CVSS."""
    monkeypatch.setattr(_kev, "_http_fetch", _mock_http_fetch_success)
    await _kev.fetch_or_fallback()

    sbom_entry: dict[str, Any] = {
        "source_meta": {"type": "docker", "image": "nginx:1.24"},
        "sbom": {"packages": []},
    }

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [
            {
                "vulnerability": {
                    "id": FEED_CVE_ID,
                    "severity": "Medium",
                    "cvss": [{"metrics": {"baseScore": 7.5}}],
                    "description": "test",
                    "fix": {"versions": ["1.25"]},
                },
                "artifact": {"name": "testpkg", "version": "1.24"},
            }
        ]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    findings = await hub.process_sbom_entry(sbom_entry, host="test-host")
    assert len(findings) == 1
    f = findings[0]
    assert f.cve_id == FEED_CVE_ID
    assert f.kev is True
    assert f.tier == 0, f"KEV CVE must be Tier 0, got tier={f.tier}"


async def test_non_kev_cve_falls_through_to_cvss_tier(
    kev_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-KEV CVE with CVSS >= 7 is Tier 2 (CVSS 7-8 or LAN-only branch)
    since internet_facing is deferred to a later story and defaults False."""
    monkeypatch.setattr(_kev, "_http_fetch", _mock_http_fetch_success)
    await _kev.fetch_or_fallback()

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [
            {
                "vulnerability": {
                    "id": NON_KEV_CVE_ID,
                    "severity": "Critical",
                    "cvss": [{"metrics": {"baseScore": 9.5}}],
                    "description": "high CVSS, not KEV",
                    "fix": {"versions": ["2.0"]},
                },
                "artifact": {"name": "testpkg", "version": "1.0"},
            }
        ]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    sbom_entry = {
        "source_meta": {"type": "docker", "image": "nginx:1.24"},
        "sbom": {"packages": []},
    }
    findings = await hub.process_sbom_entry(sbom_entry, host="test-host")
    f = findings[0]
    assert f.kev is False
    # Not KEV, not internet-facing (V1 default) → CVSS >= 7 branch → tier=2
    assert f.tier == 2


# ── Scenario 4 ───────────────────────────────────────────────────────────────


def test_tier_0_notification_has_kev_header(kev_env: Path) -> None:
    from poc.hub.executor.base import Finding

    f = Finding(
        id="t0",
        host="h",
        cve_id=FEED_CVE_ID,
        package="pkg",
        version="1.0",
        fixed_version="1.1",
        severity="High",
        cvss=7.5,
        source_type="docker",
        source_ref="nginx:1.24",
        description="x",
        tier=0,
        kev=True,
    )
    body = hub.format_notification(f)
    assert "KEV — EXPLOITED IN THE WILD" in body
    assert body.strip().startswith("🚨"), (
        "KEV header must be the first line so operator sees it at a glance"
    )


def test_non_tier_0_notification_omits_kev_header(kev_env: Path) -> None:
    from poc.hub.executor.base import Finding

    f = Finding(
        id="t2",
        host="h",
        cve_id=NON_KEV_CVE_ID,
        package="pkg",
        version="1.0",
        fixed_version="1.1",
        severity="Critical",
        cvss=9.5,
        source_type="docker",
        source_ref="nginx:1.24",
        description="x",
        tier=2,
        kev=False,
    )
    body = hub.format_notification(f)
    assert "KEV" not in body


# ── Scenario 5 ───────────────────────────────────────────────────────────────


async def test_fetch_failure_falls_back_to_cache(
    kev_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-seed disk cache, then fetch fails — in-memory populates from cache."""
    cache_path = kev_env / "kev_cache.json"
    cache_path.write_text(
        json.dumps({"fetched_at": int(time.time()) - 60, "cve_ids": [FEED_CVE_ID]})
    )
    monkeypatch.setattr(_kev, "_http_fetch", _mock_http_fetch_fail)

    await _kev.fetch_or_fallback()
    assert _kev.is_known_exploited(FEED_CVE_ID) is True
    summary = _kev.freshness()
    assert summary["kev"] == "ok"


async def test_stale_cache_reports_stale(kev_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache older than STALE_THRESHOLD_S reports 'stale', not 'ok'."""
    cache_path = kev_env / "kev_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "fetched_at": int(time.time()) - (26 * 3600),
                "cve_ids": [FEED_CVE_ID],
            }
        )
    )
    monkeypatch.setattr(_kev, "_http_fetch", _mock_http_fetch_fail)
    await _kev.fetch_or_fallback()
    assert _kev.freshness()["kev"] == "stale"


# ── Scenario 6 ───────────────────────────────────────────────────────────────


def test_findings_cli_surfaces_kev_and_sorts_tier_first(kev_env: Path) -> None:
    """`make findings` output: KEV column present, Tier 0 row surfaces first."""
    hub.init_db()
    con = sqlite3.connect(hub.DB_PATH)
    for fid, cve, cvss, tier, kev_flag in [
        ("kev0001", "CVE-KEV-01", 6.5, 0, 1),
        ("hi0001", "CVE-HI-01", 9.1, 2, 0),
        ("lo0001", "CVE-LO-01", 5.0, 3, 0),
    ]:
        con.execute(
            "INSERT INTO findings (id,host,cve_id,package,version,fixed_version,"
            "severity,cvss,source_type,source_ref,description,found_at,status,tier,kev) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                fid,
                "h",
                cve,
                "pkg",
                "1",
                "2",
                "sev",
                cvss,
                "docker",
                "img:tag",
                "desc",
                int(time.time()),
                "open",
                tier,
                kev_flag,
            ),
        )
    con.commit()
    con.close()

    rows = findings_cli.load_findings(hub.DB_PATH)
    assert len(rows) == 3
    # KEV row must surface first (tier 0 beats tier 2/3 regardless of CVSS)
    assert rows[0]["cve_id"] == "CVE-KEV-01"
    assert rows[0]["kev"] == 1

    table = findings_cli.format_table(rows)
    header_line = table.splitlines()[0]
    assert "KEV" in header_line, "format_table must emit a KEV column header"
    # First body row (3rd line: header, separator, row-1) must contain the KEV CVE
    assert "CVE-KEV-01" in table.splitlines()[2]
    assert "✓" in table.splitlines()[2], "KEV row body must render ✓"


# ── Scenario 7 ───────────────────────────────────────────────────────────────


async def test_cold_boot_fetch_fail_graceful(
    kev_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No disk cache + fetch fails → freshness='unfetched', lookup is False,
    hub ingestion still processes findings at tier=3 (CVSS fall-through)."""
    monkeypatch.setattr(_kev, "_http_fetch", _mock_http_fetch_fail)
    await _kev.fetch_or_fallback()
    assert _kev.freshness()["kev"] == "unfetched"
    assert _kev.is_known_exploited(FEED_CVE_ID) is False

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return [
            {
                "vulnerability": {
                    "id": "CVE-2026-5555",
                    "severity": "Medium",
                    "cvss": [{"metrics": {"baseScore": 5.5}}],
                    "description": "x",
                    "fix": {"versions": []},
                },
                "artifact": {"name": "pkg", "version": "1"},
            }
        ]

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    sbom_entry = {
        "source_meta": {"type": "docker", "image": "nginx:1.24"},
        "sbom": {"packages": []},
    }
    findings = await hub.process_sbom_entry(sbom_entry, host="h")
    assert findings[0].kev is False
    assert findings[0].tier == 3  # CVSS < 7 branch


# ── Smoke: event loop runs refresh_loop then shuts down ──────────────────────


async def test_refresh_loop_cancellable(kev_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """refresh_loop must be cancellable without raising past asyncio.CancelledError."""
    import contextlib

    monkeypatch.setattr(_kev, "_http_fetch", _mock_http_fetch_success)
    task = asyncio.create_task(_kev.refresh_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    # Post-cancel, the catalog should be populated from the first fetch
    assert _kev.is_known_exploited(FEED_CVE_ID) is True
