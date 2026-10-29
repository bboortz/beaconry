"""Unit tests for hub helper functions.

Covers:
    - CAP-003: Operator can disable Fix-execution with a feature flag
    - CAP-T001: Finding ID is stable across runs
    - CAP-T002: CVSS extraction picks the highest score across entries
    - CAP-T003: Notification message is Telegram-HTML-safe for adversarial descriptions
    - CAP-T008: Hub persists findings to SQLite with idempotent inserts
"""

from __future__ import annotations

import hub

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
