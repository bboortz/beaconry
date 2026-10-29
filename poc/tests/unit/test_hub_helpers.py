"""Unit tests for hub helper functions.

Covers:
    - CAP-B003: Operator can disable Fix-execution with a feature flag
    - CAP-B004: Operator is not flooded on noisy scans
    - CAP-T001: Finding ID is stable across runs
    - CAP-T002: CVSS extraction picks the highest score across entries
    - CAP-T003: Notification message is Telegram-HTML-safe for adversarial descriptions
    - CAP-T008: Hub persists findings to SQLite with idempotent inserts
    - CAP-T015: Per-run cap caps individual sends and triggers a digest
    - CAP-T020: Hub import suppresses noisy HTTP client loggers so bot tokens don't leak
    - CAP-T021: Hub /scan endpoint applies dedup + cap across the full inventory
"""

from __future__ import annotations

import hub
import pytest

from poc.hub.executor.base import Finding


def _finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = dict(
        id="x",
        host="h",
        cve_id="CVE-DEMO-1",
        package="openssl",
        version="1.0.1",
        fixed_version="1.0.2",
        severity="Critical",
        cvss=9.8,
        source_type="docker",
        source_ref="nginx:1.24",
        description="desc",
    )
    defaults.update(overrides)
    return Finding(**defaults)  # type: ignore[arg-type]


def test_make_id_is_stable_for_same_inputs() -> None:
    a = hub.make_id("h", "CVE-1", "img:1", "pkg")
    b = hub.make_id("h", "CVE-1", "img:1", "pkg")
    assert a == b and len(a) == 16


def test_make_id_differs_for_different_hosts() -> None:
    a = hub.make_id("host-a", "CVE-1", "img:1", "pkg")
    b = hub.make_id("host-b", "CVE-1", "img:1", "pkg")
    assert a != b


def test_extract_cvss_picks_highest_score() -> None:
    vuln = {
        "cvss": [
            {"metrics": {"baseScore": 7.5}},
            {"metrics": {"baseScore": 9.1}},
            {"metrics": {"baseScore": 6.0}},
        ]
    }
    assert hub.extract_cvss(vuln) == 9.1


def test_extract_cvss_defaults_to_zero_when_empty() -> None:
    assert hub.extract_cvss({}) == 0.0
    assert hub.extract_cvss({"cvss": []}) == 0.0


def test_format_notification_uses_html_tags() -> None:
    f = _finding(description="plain desc")
    text = hub.format_notification(f)
    assert "<b>" in text and "</b>" in text  # severity + CVE id
    assert "<code>" in text and "</code>" in text  # cvss, host, target, package, fix cmd
    assert "<i>" in text and "</i>" in text  # description


def test_format_notification_html_escapes_angle_brackets_and_ampersand() -> None:
    """Regression for BC-002: switched Markdown -> HTML; must html-escape user data."""
    f = _finding(
        description="See <script>alert(1)</script> & [refs]_with*weirdness`",
        package="pkg<xml>",
    )
    text = hub.format_notification(f)
    # The raw strings must not appear — they've been html-escaped.
    assert "<script>" not in text  # replaced with &lt;script&gt;
    assert "&lt;script&gt;" in text
    # Markdown-ish chars like [ _ * ` are not special in HTML, so they survive.
    assert "[refs]_with*weirdness`" in text
    # And the ampersand is escaped to &amp; (not left as a bare &).
    assert "&amp;" in text


def test_format_notification_truncates_description_to_160_chars() -> None:
    f = _finding(description="A" * 500)
    text = hub.format_notification(f)
    italic_line = next(line for line in text.splitlines() if line.startswith("<i>"))
    inner = italic_line[len("<i>") : -len("</i>")]
    assert len(inner) == 160


def test_hub_import_silences_noisy_http_loggers() -> None:
    """Regression: importing hub must raise httpx/telegram log level >= WARNING
    so request URLs (which contain the bot token) never leak to docker logs."""
    import logging

    for name in ("httpx", "httpcore", "telegram", "telegram.ext"):
        assert logging.getLogger(name).level >= logging.WARNING, (
            f"logger {name!r} is below WARNING — bot token would leak via httpx logs"
        )


def test_save_finding_is_idempotent_on_duplicate_ids() -> None:
    hub.init_db()
    unique = _finding(id="unit-dup-key")

    first = hub.save_finding(unique)
    second = hub.save_finding(unique)

    assert first is True, "first insert should report new"
    assert second is False, "second insert of same id should be a no-op dup"


def test_load_finding_round_trips_fields() -> None:
    hub.init_db()
    saved = _finding(id="unit-roundtrip-1", cve_id="CVE-ROUNDTRIP")
    hub.save_finding(saved)

    loaded = hub.load_finding("unit-roundtrip-1")

    assert loaded is not None
    assert loaded.cve_id == "CVE-ROUNDTRIP"
    assert loaded.package == saved.package
    assert loaded.cvss == saved.cvss


def test_load_finding_returns_none_for_missing_id() -> None:
    hub.init_db()
    assert hub.load_finding("does-not-exist") is None


def test_dedup_and_cap_dedupes_by_cve_id_and_ranks_by_tier_cvss() -> None:
    """Across multiple SBOMs, same CVE on different packages collapses to one;
    results are ranked (tier asc, cvss desc), top N returned, rest suppressed."""
    findings = [
        _finding(id="a", cve_id="CVE-X", cvss=7.5, package="libssl1.1", source_ref="img:a"),
        _finding(id="b", cve_id="CVE-X", cvss=9.1, package="openssl", source_ref="img:b"),
        _finding(id="c", cve_id="CVE-Y", cvss=8.0, package="zlib", source_ref="img:a"),
        _finding(id="d", cve_id="CVE-Z", cvss=9.9, package="curl", source_ref="img:a"),
    ]
    # Simulate tier classification: higher CVSS → lower tier number
    for f, tier in zip(findings, [2, 1, 2, 1], strict=True):
        f.tier = tier

    top, suppressed = hub.dedup_and_cap(findings, cap=2)

    # CVE-X dedup → keep 9.1 (openssl variant), not 7.5
    cve_ids = [f.cve_id for f in top]
    assert "CVE-X" in cve_ids
    assert "CVE-Z" in cve_ids
    assert len(top) == 2
    assert len(suppressed) == 1
    assert suppressed[0].cve_id == "CVE-Y"


def test_dedup_and_cap_empty_input() -> None:
    top, suppressed = hub.dedup_and_cap([], cap=5)
    assert top == [] and suppressed == []


async def test_receive_scan_applies_dedup_and_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BC-005 regression: receive_scan must apply cap + dedup across all SBOMs
    before notifying. Previously it notified per-SBOM, flooding the operator."""
    hub.init_db()

    async def _fake_process(entry: object, host: str) -> list[Finding]:
        # Each SBOM "returns" 3 findings; same CVE ids across SBOMs so dedup matters.
        source = entry.get("source_meta", {}).get("image", "img")  # type: ignore[union-attr]
        return [
            _finding(id=f"{source}-A", cve_id="CVE-A", cvss=9.5, source_ref=source),
            _finding(id=f"{source}-B", cve_id="CVE-B", cvss=8.0, source_ref=source),
            _finding(id=f"{source}-C", cve_id="CVE-C", cvss=7.0, source_ref=source),
        ]

    notified: list[Finding] = []

    async def _fake_notify(f: Finding) -> None:
        notified.append(f)

    digests: list[list[Finding]] = []

    async def _fake_digest(suppressed: list[Finding]) -> None:
        digests.append(list(suppressed))

    monkeypatch.setattr(hub, "process_sbom_entry", _fake_process)
    monkeypatch.setattr(hub, "notify", _fake_notify)
    monkeypatch.setattr(hub, "send_digest", _fake_digest)
    monkeypatch.setenv("BEACONRY_MAX_FINDINGS_PER_RUN", "2")

    payload = {
        "host": "test-host",
        "sboms": [
            {"source_meta": {"type": "docker", "image": "img-1"}, "sbom": {}},
            {"source_meta": {"type": "docker", "image": "img-2"}, "sbom": {}},
            {"source_meta": {"type": "docker", "image": "img-3"}, "sbom": {}},
        ],
    }

    result = await hub.receive_scan(payload)

    # 3 SBOMs × 3 findings = 9 candidates → dedup to 3 unique CVEs (CVE-A, B, C)
    # Cap=2 → notify top 2 by (tier, -cvss), suppress 1
    assert result["status"] == "ok"
    assert len(notified) == 2
    assert len(digests) == 1 and len(digests[0]) == 1


def test_dedup_and_cap_fewer_than_cap() -> None:
    findings = [
        _finding(id="a", cve_id="CVE-A", cvss=9.0),
        _finding(id="b", cve_id="CVE-B", cvss=8.0),
    ]
    top, suppressed = hub.dedup_and_cap(findings, cap=5)
    assert len(top) == 2 and suppressed == []


def test_set_status_updates_the_row() -> None:
    hub.init_db()
    f = _finding(id="unit-status-1")
    hub.save_finding(f)

    hub.set_status("unit-status-1", "accepted")

    import sqlite3

    con = sqlite3.connect(hub.DB_PATH)
    row = con.execute("SELECT status FROM findings WHERE id=?", ("unit-status-1",)).fetchone()
    con.close()
    assert row is not None and row[0] == "accepted"
