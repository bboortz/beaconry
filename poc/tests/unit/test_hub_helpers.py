"""Unit tests for hub helper functions.

Covers:
    - CAP-B003: Operator can disable Fix-execution with a feature flag
    - CAP-B004: Operator is not flooded on noisy scans
    - CAP-B007: Operator can review the current state of findings from the CLI
    - CAP-B008: Operator closing fix flow — tap Fix button → re-scan verify → status set
    - CAP-T025: HTML-based notification format escapes `< > &` safely
    - CAP-T001: Finding ID is stable across runs
    - CAP-T002: CVSS extraction picks the highest score across entries
    - CAP-T003: Notification message is Telegram-HTML-safe for adversarial descriptions
    - CAP-T008: Hub persists findings to SQLite with idempotent inserts
    - CAP-T015: Per-run cap caps individual sends and triggers a digest
    - CAP-T019: Compose stack brings up hub + scout with :ro socket mount, healthchecks, env propagation
    - CAP-T020: Hub import suppresses noisy HTTP client loggers so bot tokens don't leak
    - CAP-T021: Hub /scan endpoint applies dedup + cap across the full inventory
    - CAP-T028: Callback_data validation rejects malformed input
    - CAP-T029: Button handlers are idempotent on double-tap
    - CAP-B014: Operator closes a CVE from Telegram — tap Fix, pulled, re-scanned, verified
    - CAP-T031: verify_fix re-runs syft + grype on the new image
    - CAP-T032: Fix flow distinguishes pull-failed from pull-OK-but-upstream-still-vulnerable
    - CAP-T033: Operator can opt out of fix execution via BEACONRY_FIX_ENABLED=0
    - CAP-B018: Operator confirms a fix actually brought the service back up
    - CAP-T050: New DB statuses `fixed_verified` + `verification_failed` with idempotent migration
    - CAP-T056: Hub configurable Telegram API base URL via `BEACONRY_TELEGRAM_BASE_URL`
    - CAP-T059: Hub ingest path wires urgency.classify_tier → DB tier column
    - CAP-T060: Notification format inserts a KEV header line for tier 0 findings
    - CAP-T062: Digest format surfaces KEV markers for suppressed findings
    - CAP-T063: KEV findings bypass MIN_CVSS filter in hub ingest
    - CAP-T066: KEV lookup resolves grype's relatedVulnerabilities aliases
"""

from __future__ import annotations

import hub
import pytest

from poc.beaconry_health import checks as _bh_checks
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


class _FakeQuery:
    """Minimal CallbackQuery stub for exercising on_button_click."""

    def __init__(self, data: str) -> None:
        self.data = data
        self.answered = False
        self.edits: list[dict[str, object]] = []

    async def answer(self) -> None:
        self.answered = True

    async def edit_message_text(self, text: str, **kwargs: object) -> None:
        self.edits.append({"text": text, **kwargs})


class _FakeUpdate:
    def __init__(self, data: str) -> None:
        self.callback_query = _FakeQuery(data)


async def test_on_button_click_rejects_malformed_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BC-003: a crafted callback_data must be rejected before any DB access."""
    hub.init_db()

    load_calls: list[str] = []

    def _spy_load(finding_id: str) -> object:  # pragma: no cover - should not run
        load_calls.append(finding_id)
        return None

    monkeypatch.setattr(hub, "load_finding", _spy_load)

    update = _FakeUpdate("'; DROP TABLE findings; --")
    await hub.on_button_click(update, context=None)  # type: ignore[arg-type]

    assert load_calls == [], "malformed callback must not reach load_finding"
    assert len(update.callback_query.edits) == 1
    assert "invalid" in str(update.callback_query.edits[0]["text"]).lower()


async def test_on_button_click_accepts_well_formed_callback() -> None:
    """Shape sanity: a valid snooze:<16hex>:4 payload must reach the handler."""
    hub.init_db()
    hub.save_finding(_finding(id="deadbeefcafe0001", cve_id="CVE-OK"))

    update = _FakeUpdate("snooze:deadbeefcafe0001:4")
    await hub.on_button_click(update, context=None)  # type: ignore[arg-type]

    import sqlite3

    con = sqlite3.connect(hub.DB_PATH)
    row = con.execute("SELECT status FROM findings WHERE id=?", ("deadbeefcafe0001",)).fetchone()
    con.close()
    assert row is not None and row[0] == "snoozed"


async def test_double_tap_snooze_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BC-003: a second Snooze tap on an already-snoozed finding must not re-write
    and must respond with an 'already' message."""
    hub.init_db()
    hub.save_finding(_finding(id="deadbeefcafe0002", cve_id="CVE-DOUBLE"))
    hub.set_status("deadbeefcafe0002", "snoozed")

    set_status_calls: list[tuple[str, str]] = []

    def _spy_set(fid: str, status: str) -> None:
        set_status_calls.append((fid, status))

    monkeypatch.setattr(hub, "set_status", _spy_set)

    update = _FakeUpdate("snooze:deadbeefcafe0002:4")
    await hub.on_button_click(update, context=None)  # type: ignore[arg-type]

    assert set_status_calls == [], "double-tap must not re-write the DB"
    assert len(update.callback_query.edits) == 1
    assert "already" in str(update.callback_query.edits[0]["text"]).lower()


async def test_double_tap_accept_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub.init_db()
    hub.save_finding(_finding(id="deadbeefcafe0003", cve_id="CVE-DBL-ACC"))
    hub.set_status("deadbeefcafe0003", "accepted")

    set_status_calls: list[tuple[str, str]] = []

    def _spy_set(fid: str, status: str) -> None:
        set_status_calls.append((fid, status))

    monkeypatch.setattr(hub, "set_status", _spy_set)

    update = _FakeUpdate("ack:deadbeefcafe0003")
    await hub.on_button_click(update, context=None)  # type: ignore[arg-type]

    assert set_status_calls == []
    assert "already" in str(update.callback_query.edits[0]["text"]).lower()


async def _run_fix_tap(finding_id: str) -> _FakeUpdate:
    update = _FakeUpdate(f"fix:{finding_id}")
    await hub.on_button_click(update, context=None)  # type: ignore[arg-type]
    return update


async def test_fix_now_pull_clean_sets_status_fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: DockerAdapter pulls, verify_fix clean → status=fixed, '✅ Fixed and verified'."""
    hub.init_db()
    hub.save_finding(_finding(id="bc9fcafebabe0001", cve_id="CVE-FIX-1", source_type="docker"))
    monkeypatch.setattr(hub, "FIX_ENABLED", True, raising=True)

    from poc.hub.executor.base import ExecutionResult

    async def _fake_execute(self: object, f: object) -> ExecutionResult:
        return ExecutionResult(success=True, message="pulled", command="docker pull x:latest")

    async def _fake_verify(f: object) -> bool:
        return True

    monkeypatch.setattr(
        hub.EXECUTORS[0].__class__, "execute", _fake_execute, raising=True
    )  # DockerAdapter
    monkeypatch.setattr(hub, "verify_fix", _fake_verify)

    update = await _run_fix_tap("bc9fcafebabe0001")

    assert hub.get_status("bc9fcafebabe0001") == "fixed"
    final_edit = str(update.callback_query.edits[-1]["text"])
    assert "Fixed and verified" in final_edit


async def test_fix_now_pull_upstream_still_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pull succeeded but verify_fix still finds the CVE → status stays open, honest message."""
    hub.init_db()
    hub.save_finding(_finding(id="bc9fcafebabe0002", cve_id="CVE-FIX-2", source_type="docker"))
    monkeypatch.setattr(hub, "FIX_ENABLED", True, raising=True)

    from poc.hub.executor.base import ExecutionResult

    async def _fake_execute(self: object, f: object) -> ExecutionResult:
        return ExecutionResult(success=True, message="pulled", command="docker pull x:latest")

    async def _fake_verify_still(f: object) -> bool:
        return False

    monkeypatch.setattr(hub.EXECUTORS[0].__class__, "execute", _fake_execute, raising=True)
    monkeypatch.setattr(hub, "verify_fix", _fake_verify_still)

    update = await _run_fix_tap("bc9fcafebabe0002")

    assert hub.get_status("bc9fcafebabe0002") == "open"
    final_edit = str(update.callback_query.edits[-1]["text"])
    assert "still present upstream" in final_edit.lower() or "upstream" in final_edit.lower()


async def test_fix_now_pull_fails_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """DockerAdapter returns success=False → '❌ Fix failed', no verify_fix call, status stays open."""
    hub.init_db()
    hub.save_finding(_finding(id="bc9fcafebabe0003", cve_id="CVE-FIX-3", source_type="docker"))
    monkeypatch.setattr(hub, "FIX_ENABLED", True, raising=True)

    from poc.hub.executor.base import ExecutionResult

    async def _fake_execute_fail(self: object, f: object) -> ExecutionResult:
        return ExecutionResult(
            success=False, message="docker pull failed: 500", command="docker pull x:latest"
        )

    verify_called: list[bool] = []

    async def _tracked_verify(f: object) -> bool:
        verify_called.append(True)
        return True

    monkeypatch.setattr(hub.EXECUTORS[0].__class__, "execute", _fake_execute_fail, raising=True)
    monkeypatch.setattr(hub, "verify_fix", _tracked_verify)

    update = await _run_fix_tap("bc9fcafebabe0003")

    assert hub.get_status("bc9fcafebabe0003") == "open"
    assert verify_called == [], "verify_fix must not run if pull failed"
    final_edit = str(update.callback_query.edits[-1]["text"])
    assert "fix failed" in final_edit.lower()


async def test_fix_now_disabled_shows_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """BEACONRY_FIX_ENABLED=0 → stub message, no DockerAdapter.execute call."""
    hub.init_db()
    hub.save_finding(_finding(id="bc9fcafebabe0004", cve_id="CVE-FIX-4", source_type="docker"))
    monkeypatch.setattr(hub, "FIX_ENABLED", False, raising=True)

    execute_calls: list[bool] = []

    async def _tracked_execute(self: object, f: object) -> object:
        execute_calls.append(True)
        return None

    monkeypatch.setattr(hub.EXECUTORS[0].__class__, "execute", _tracked_execute, raising=True)

    update = await _run_fix_tap("bc9fcafebabe0004")

    assert execute_calls == [], "disabled flag must prevent DockerAdapter.execute"
    assert hub.get_status("bc9fcafebabe0004") == "open"
    final_edit = str(update.callback_query.edits[-1]["text"])
    assert "disabled" in final_edit.lower() or "beaconry_fix_enabled" in final_edit.lower()


async def test_fix_now_non_docker_runs_manual_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-docker (alpm) finding → ManualAdapter path, status stays open, 'manual action required'."""
    hub.init_db()
    hub.save_finding(
        _finding(id="bc9fcafebabe0005", cve_id="CVE-FIX-5", source_type="alpm", package="openssl")
    )
    monkeypatch.setattr(hub, "FIX_ENABLED", True, raising=True)

    # Spy on DockerAdapter.execute — must NOT run for an alpm finding
    docker_execute_calls: list[bool] = []

    async def _tracked_docker_execute(self: object, f: object) -> object:
        docker_execute_calls.append(True)
        return None

    monkeypatch.setattr(
        hub.EXECUTORS[0].__class__, "execute", _tracked_docker_execute, raising=True
    )

    update = await _run_fix_tap("bc9fcafebabe0005")

    assert docker_execute_calls == [], "DockerAdapter must not run for alpm"
    assert hub.get_status("bc9fcafebabe0005") == "open"
    final_edit = str(update.callback_query.edits[-1]["text"])
    assert "manual action" in final_edit.lower() or "pacman" in final_edit.lower()


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


def test_save_and_load_round_trip_preserves_tier(tmp_path: pytest.TempdirFactory) -> None:
    """BC-007 close-gaps-2: tier must survive save → load (previously defaulted to 3)."""
    db = tmp_path / "tier-roundtrip.db"  # type: ignore[operator]
    original_db = hub.DB_PATH
    hub.DB_PATH = db
    try:
        hub.init_db()
        hub.save_finding(_finding(id="tier-0-roundtrip", cve_id="CVE-KEV", tier=0))
        loaded = hub.load_finding("tier-0-roundtrip")
        assert loaded is not None
        assert loaded.tier == 0
    finally:
        hub.DB_PATH = original_db


def test_init_db_migrates_legacy_schema_in_place(tmp_path: pytest.TempdirFactory) -> None:
    """BC-007 close-gaps-2: legacy DB without tier column gets ALTER'd on init_db."""
    import sqlite3

    db = tmp_path / "legacy.db"  # type: ignore[operator]
    con = sqlite3.connect(db)
    con.execute("""
        CREATE TABLE findings (
            id           TEXT PRIMARY KEY,
            host         TEXT,
            cve_id       TEXT,
            package      TEXT,
            version      TEXT,
            fixed_version TEXT,
            severity     TEXT,
            cvss         REAL,
            source_type  TEXT,
            source_ref   TEXT,
            description  TEXT,
            found_at     INTEGER,
            status       TEXT DEFAULT 'open'
        )
    """)
    con.commit()
    con.close()

    original_db = hub.DB_PATH
    hub.DB_PATH = db
    try:
        hub.init_db()
    finally:
        hub.DB_PATH = original_db

    con = sqlite3.connect(db)
    schema_cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
    con.close()
    assert "tier" in schema_cols, (
        "init_db must idempotently ALTER TABLE to add 'tier' on legacy DBs"
    )


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


def test_health_endpoint_returns_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """CAP-T019 live verification via FastAPI TestClient — no docker required.

    BC-010: /health is now a transitional alias to /readyz. Body shape changed
    from {status:"ok"} to {status:"ready", checks:{...}} — assertion loosened to
    status_code only, which was the test's original intent.
    """
    from fastapi.testclient import TestClient

    grype_dir = tmp_path / "grype-db"  # type: ignore[operator]
    grype_dir.mkdir()
    (grype_dir / "vulnerability.db").write_text("x")
    monkeypatch.setenv("GRYPE_DB_CACHE_DIR", str(grype_dir))
    monkeypatch.setattr(hub, "_startup_complete", True, raising=False)
    monkeypatch.setattr(_bh_checks, "_which", lambda _cmd: "/usr/local/bin/grype")

    client = TestClient(hub.api)
    response = client.get("/health")
    assert response.status_code == 200


async def test_receive_scan_notifies_on_fresh_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """BC-005 close-gaps: prove notify IS called when save_finding returns True.
    The live run only exercised the dedup-across-runs path (DB had entries).
    """
    import sqlite3

    fresh_db = tmp_path / "fresh.db"  # type: ignore[operator]
    monkeypatch.setattr(hub, "DB_PATH", fresh_db)
    hub.init_db()
    # sanity: table empty
    con = sqlite3.connect(fresh_db)
    assert con.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 0
    con.close()

    async def _fake_process(entry: object, host: str) -> list[Finding]:
        return [_finding(id="fresh-A", cve_id="CVE-NEW", cvss=9.5, source_ref="img:a")]

    notified: list[Finding] = []

    async def _fake_notify(f: Finding) -> None:
        notified.append(f)

    async def _fake_digest(_: list[Finding]) -> None:
        pass

    monkeypatch.setattr(hub, "process_sbom_entry", _fake_process)
    monkeypatch.setattr(hub, "notify", _fake_notify)
    monkeypatch.setattr(hub, "send_digest", _fake_digest)

    result = await hub.receive_scan(
        {"host": "h", "sboms": [{"source_meta": {"type": "docker", "image": "img:a"}, "sbom": {}}]}
    )

    assert result["findings"] == 1
    assert len(notified) == 1
    assert notified[0].cve_id == "CVE-NEW"


async def test_receive_scan_empty_sbom_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty scan payload → 0 findings, no crash."""

    async def _fake_digest(_: list[Finding]) -> None:
        pass

    monkeypatch.setattr(hub, "send_digest", _fake_digest)
    result = await hub.receive_scan({"host": "h", "sboms": []})
    assert result == {"status": "ok", "findings": 0}


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


# ── BC-011: verify-service-health button handler ──────────────────────────────


def _probe(probe_arg: str = "host:5432"):
    from poc.hub.waterfall import Probe

    return Probe(image_ref="postgres:16-alpine", probe_type="tcp", probe_arg=probe_arg)


async def _run_verify_tap(finding_id: str) -> _FakeUpdate:
    update = _FakeUpdate(f"verify:{finding_id}")
    await hub.on_button_click(update, context=None)  # type: ignore[arg-type]
    return update


async def test_verify_action_tcp_pass_sets_fixed_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    hub.init_db()
    hub.save_finding(_finding(id="bc11cafebabe0001", source_ref="postgres:16-alpine"))

    from poc.hub.waterfall import ProbeResult

    monkeypatch.setattr(hub, "_resolve_probe_for", lambda _ref: _probe())

    async def _fake_run(_p: object) -> ProbeResult:
        return ProbeResult(ok=True, reason="tcp connect ok postgres:5432")

    monkeypatch.setattr("poc.hub.waterfall.run_probe", _fake_run)

    update = await _run_verify_tap("bc11cafebabe0001")
    assert hub.get_status("bc11cafebabe0001") == "fixed_verified"
    final = str(update.callback_query.edits[-1]["text"])
    assert "Service healthy" in final
    assert "probe passed" in final.lower()


async def test_verify_action_tcp_fail_sets_verification_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub.init_db()
    hub.save_finding(_finding(id="bc11cafebabe0002", source_ref="postgres:16-alpine"))

    from poc.hub.waterfall import ProbeResult

    monkeypatch.setattr(hub, "_resolve_probe_for", lambda _ref: _probe())

    async def _fake_run(_p: object) -> ProbeResult:
        return ProbeResult(
            ok=False, reason="tcp connect failed postgres:5432: ConnectionRefusedError"
        )

    monkeypatch.setattr("poc.hub.waterfall.run_probe", _fake_run)

    update = await _run_verify_tap("bc11cafebabe0002")
    assert hub.get_status("bc11cafebabe0002") == "verification_failed"
    final = str(update.callback_query.edits[-1]["text"])
    assert "Probe failed" in final
    assert "docker tag" in final
    assert "postgres:rollback" in final


async def test_verify_action_no_policy_shows_skip_message(monkeypatch: pytest.MonkeyPatch) -> None:
    hub.init_db()
    hub.save_finding(_finding(id="bc11cafebabe0003", source_ref="rare:custom"))

    monkeypatch.setattr(hub, "_resolve_probe_for", lambda _ref: None)

    update = await _run_verify_tap("bc11cafebabe0003")
    # DB status unchanged — operator informed, not misled.
    assert hub.get_status("bc11cafebabe0003") == "open"
    final = str(update.callback_query.edits[-1]["text"])
    assert "No probe configured" in final
    assert "rare:custom" in final


async def test_verify_action_is_idempotent_on_fixed_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub.init_db()
    hub.save_finding(_finding(id="bc11cafebabe0004", source_ref="postgres:16-alpine"))
    hub.set_status("bc11cafebabe0004", "fixed_verified")

    probe_calls: list[int] = []
    monkeypatch.setattr(hub, "_resolve_probe_for", lambda _ref: _probe())

    async def _fake_run(_p: object) -> object:
        probe_calls.append(1)
        from poc.hub.waterfall import ProbeResult

        return ProbeResult(ok=True, reason="")

    monkeypatch.setattr("poc.hub.waterfall.run_probe", _fake_run)

    update = await _run_verify_tap("bc11cafebabe0004")
    assert probe_calls == [], "idempotent handler must not re-run the probe"
    final = str(update.callback_query.edits[-1]["text"])
    assert "Already fixed_verified" in final


async def test_malformed_verify_callback_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    hub.init_db()
    update = _FakeUpdate("verify:not-a-hex-id")
    await hub.on_button_click(update, context=None)  # type: ignore[arg-type]
    final = str(update.callback_query.edits[-1]["text"])
    assert "malformed" in final.lower() or "invalid" in final.lower()


# ── BC-011 close-gap: button-attachment branch coverage ───────────────────────


async def test_fix_success_attaches_verify_button_when_policy_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BC-011 Scenario 1: ✅ Fixed message gets the Verify button only if resolver returns a probe.

    The handler currently emits the terminal state via `query.edit_message_text` with
    an optional `reply_markup`. This test confirms the keyboard is attached when a
    policy matches (and `_resolve_probe_for` returns a Probe) and NOT attached
    when it returns None.
    """
    hub.init_db()
    hub.save_finding(_finding(id="bc11beefcafe0101", source_ref="postgres:16-alpine"))
    monkeypatch.setattr(hub, "FIX_ENABLED", True, raising=True)

    from poc.hub.executor.base import ExecutionResult

    async def _fake_execute(self: object, f: object) -> ExecutionResult:
        return ExecutionResult(
            success=True, message="pulled", command="docker pull postgres:16-alpine"
        )

    async def _fake_verify(f: object) -> bool:
        return True

    monkeypatch.setattr(hub.EXECUTORS[0].__class__, "execute", _fake_execute, raising=True)
    monkeypatch.setattr(hub, "verify_fix", _fake_verify)
    monkeypatch.setattr(hub, "_resolve_probe_for", lambda _ref: _probe())

    update = _FakeUpdate("fix:bc11beefcafe0101")
    await hub.on_button_click(update, context=None)  # type: ignore[arg-type]
    final_edit = update.callback_query.edits[-1]
    assert "Fixed and verified" in str(final_edit["text"])
    assert "reply_markup" in final_edit, "verify button must be attached when policy matches"


async def test_fix_success_omits_verify_button_when_no_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BC-011 Scenario 1 (negative): no policy → no Verify button on the Fixed message."""
    hub.init_db()
    hub.save_finding(_finding(id="bc11beefcafe0202", source_ref="rare:custom"))
    monkeypatch.setattr(hub, "FIX_ENABLED", True, raising=True)

    from poc.hub.executor.base import ExecutionResult

    async def _fake_execute(self: object, f: object) -> ExecutionResult:
        return ExecutionResult(success=True, message="pulled", command="docker pull rare:custom")

    async def _fake_verify(f: object) -> bool:
        return True

    monkeypatch.setattr(hub.EXECUTORS[0].__class__, "execute", _fake_execute, raising=True)
    monkeypatch.setattr(hub, "verify_fix", _fake_verify)
    monkeypatch.setattr(hub, "_resolve_probe_for", lambda _ref: None)

    update = _FakeUpdate("fix:bc11beefcafe0202")
    await hub.on_button_click(update, context=None)  # type: ignore[arg-type]
    final_edit = update.callback_query.edits[-1]
    assert "Fixed and verified" in str(final_edit["text"])
    assert "reply_markup" not in final_edit, (
        "no policy → no Verify button; operator shouldn't be prompted to probe nothing"
    )


# ── BC-018: env-splitting dials (close-gap coverage) ─────────────────────────


def test_telegram_base_url_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """BC-018: BEACONRY_TELEGRAM_BASE_URL=... steers Application.builder.base_url.

    Covers CAP-T056. hub.py reads the env at import time, so reload under the
    monkeypatched env and inspect the module-level global.
    """
    import importlib

    monkeypatch.setenv("BEACONRY_TELEGRAM_BASE_URL", "http://telegram-mock:8080")
    reloaded = importlib.reload(hub)
    try:
        assert reloaded.TELEGRAM_BASE_URL == "http://telegram-mock:8080"
    finally:
        # Restore pristine module for subsequent tests.
        monkeypatch.delenv("BEACONRY_TELEGRAM_BASE_URL", raising=False)
        importlib.reload(hub)


def test_telegram_base_url_defaults_to_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """BC-018: unset env → empty string → hub keeps the real-Telegram default."""
    import importlib

    monkeypatch.delenv("BEACONRY_TELEGRAM_BASE_URL", raising=False)
    reloaded = importlib.reload(hub)
    try:
        assert reloaded.TELEGRAM_BASE_URL == ""
    finally:
        importlib.reload(hub)


def test_skip_grype_db_env_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """BC-018: BEACONRY_SKIP_GRYPE_DB=1 alone (no BEACONRY_MOCK) must skip DB download.

    The two dials were deliberately split so e2e stacks can run REAL
    python-telegram-bot against telegram-mock while still skipping the heavy
    grype download. Regressing this collapses them back together.
    """
    import importlib

    monkeypatch.delenv("BEACONRY_MOCK", raising=False)
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    reloaded = importlib.reload(hub)
    try:
        assert reloaded.SKIP_GRYPE_DB is True
        assert reloaded.MOCK is False, "BEACONRY_SKIP_GRYPE_DB must not imply MOCK"
    finally:
        monkeypatch.delenv("BEACONRY_SKIP_GRYPE_DB", raising=False)
        importlib.reload(hub)


def test_mock_env_still_implies_skip_grype_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """BC-018 back-compat: BEACONRY_MOCK=1 remains an alias that also skips grype DB."""
    import importlib

    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.delenv("BEACONRY_SKIP_GRYPE_DB", raising=False)
    reloaded = importlib.reload(hub)
    try:
        assert reloaded.MOCK is True
        assert reloaded.SKIP_GRYPE_DB is True
    finally:
        monkeypatch.delenv("BEACONRY_MOCK", raising=False)
        importlib.reload(hub)


# ── BC-020: Finding.kev + DB migration + save/load round-trip ────────────────


def test_finding_has_kev_field_default_false() -> None:
    """Finding dataclass carries a bool `kev` field, defaulting to False."""
    f = _finding()
    assert hasattr(f, "kev"), "Finding dataclass must have a `kev` field"
    assert f.kev is False
    kev_f = _finding(kev=True)
    assert kev_f.kev is True


def test_init_db_adds_kev_column_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """BC-020 migration: `kev` column lands on first init_db and is idempotent."""
    import sqlite3

    db_path = tmp_path / "beaconry.db"  # type: ignore[operator]
    monkeypatch.setattr(hub, "DB_PATH", db_path)
    hub.init_db()
    hub.init_db()  # second call must not raise (idempotent migration)

    con = sqlite3.connect(db_path)
    cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
    con.close()
    assert "kev" in cols
    assert "tier" in cols


def test_save_and_load_finding_round_trips_kev_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """kev=True Finding → save → load → kev is True (not an int, not None)."""
    db_path = tmp_path / "beaconry.db"  # type: ignore[operator]
    monkeypatch.setattr(hub, "DB_PATH", db_path)
    hub.init_db()
    f = _finding(id="bc020kev01", kev=True, tier=0)
    assert hub.save_finding(f) is True
    loaded = hub.load_finding("bc020kev01")
    assert loaded is not None
    assert loaded.kev is True
    assert loaded.tier == 0


def test_format_notification_prepends_kev_header_for_tier_0() -> None:
    """CAP-T060: Tier 0 findings get the "🚨 KEV — EXPLOITED IN THE WILD" header line."""
    f = _finding(tier=0, kev=True, cve_id="CVE-2026-9999", severity="High", cvss=7.5)
    body = hub.format_notification(f)
    first_line = body.splitlines()[0]
    assert "KEV" in first_line
    assert "🚨" in first_line


def test_format_notification_omits_kev_header_for_non_tier_0() -> None:
    f = _finding(tier=2, kev=False, cve_id="CVE-2026-1111", severity="Critical", cvss=9.5)
    body = hub.format_notification(f)
    assert "KEV" not in body


# ── BC-022: digest KEV awareness ─────────────────────────────────────────────


class _CapturingBot:
    """Captures send_message calls so we can assert on the rendered body.
    `send_digest` reads `tg_app.bot.send_message` and awaits it; this stub
    satisfies that contract."""

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_message(self, **kwargs: object) -> None:
        self.sent.append(kwargs)


async def _capture_digest(
    monkeypatch: pytest.MonkeyPatch, suppressed: list[Finding]
) -> _CapturingBot:
    bot = _CapturingBot()
    import types

    fake_tg_app = types.SimpleNamespace(bot=bot)
    monkeypatch.setattr(hub, "tg_app", fake_tg_app)
    monkeypatch.setattr(hub, "TELEGRAM_CHAT_ID", "123", raising=False)
    await hub.send_digest(suppressed)
    return bot


async def test_digest_row_prefixes_kev_with_siren(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T062: KEV rows start with 🚨; non-KEV rows do not."""
    suppressed = [
        _finding(id="k1", cve_id="CVE-KEV-01", package="openssl", tier=0, kev=True),
        _finding(id="n1", cve_id="CVE-NON-01", package="curl", tier=2, kev=False),
    ]
    bot = await _capture_digest(monkeypatch, suppressed)
    assert len(bot.sent) == 1
    body = str(bot.sent[0].get("text", ""))
    # Row with KEV CVE must contain the siren prefix
    for line in body.splitlines():
        if "CVE-KEV-01" in line:
            assert "🚨" in line, f"KEV row must have 🚨 prefix; got: {line!r}"
        if "CVE-NON-01" in line and line.strip().startswith("•"):
            assert "🚨" not in line, f"non-KEV row must NOT have 🚨; got: {line!r}"


async def test_digest_banner_fires_when_any_suppressed_is_kev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CAP-T062: Loud banner surfaces the KEV count when >=1 suppressed finding is KEV."""
    suppressed = [
        _finding(id="k1", cve_id="CVE-KEV-01", tier=0, kev=True),
        _finding(id="k2", cve_id="CVE-KEV-02", tier=0, kev=True),
        _finding(id="n1", cve_id="CVE-NON-01", tier=2, kev=False),
    ]
    bot = await _capture_digest(monkeypatch, suppressed)
    body = str(bot.sent[0]["text"])
    assert "2 suppressed finding(s) are KEV" in body
    # Banner must be above the bullet list
    banner_pos = body.find("are KEV")
    first_bullet_pos = body.find("•")
    assert banner_pos < first_bullet_pos, "banner must appear before the bullet list"


async def test_digest_no_banner_when_no_kev(monkeypatch: pytest.MonkeyPatch) -> None:
    suppressed = [_finding(id="n1", cve_id="CVE-NON-01", tier=2, kev=False)]
    bot = await _capture_digest(monkeypatch, suppressed)
    body = str(bot.sent[0]["text"])
    assert "are KEV" not in body


async def test_digest_empty_list_no_send(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-BC-022 behavior preserved: empty suppressed list → no Telegram message."""
    bot = await _capture_digest(monkeypatch, [])
    assert bot.sent == []


# ── BC-023: KEV bypasses MIN_CVSS filter ────────────────────────────────────


def _grype_match(cve_id: str, cvss: float, severity: str = "High") -> dict:
    return {
        "vulnerability": {
            "id": cve_id,
            "severity": severity,
            "cvss": [{"metrics": {"baseScore": cvss}}],
            "description": "fixture",
            "fix": {"versions": ["1.1"]},
        },
        "artifact": {"name": "pkg", "version": "1.0"},
    }


async def _ingest(
    monkeypatch: pytest.MonkeyPatch,
    kev_cves: set[str],
    matches: list[dict],
    min_cvss: float = 9.0,
) -> list[Finding]:
    """Drive process_sbom_entry with mocked grype + KEV lookup."""
    from poc.hub import kev as _kev

    monkeypatch.setattr(hub, "MIN_CVSS", min_cvss, raising=False)
    monkeypatch.setattr(_kev, "is_known_exploited", lambda c: c in kev_cves)

    async def _fake_grype(_sbom: dict) -> list[dict]:
        return matches

    monkeypatch.setattr(hub, "run_grype", _fake_grype)
    return await hub.process_sbom_entry(
        {"source_meta": {"type": "docker", "image": "nginx:1.24"}, "sbom": {}},
        host="h",
    )


async def test_kev_below_min_cvss_passes_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T063 Scenario 1: KEV CVE below MIN_CVSS must survive the filter."""
    findings = await _ingest(
        monkeypatch,
        kev_cves={"CVE-2023-44487"},
        matches=[_grype_match("CVE-2023-44487", cvss=7.5)],
        min_cvss=9.0,
    )
    assert len(findings) == 1
    assert findings[0].cve_id == "CVE-2023-44487"
    assert findings[0].kev is True
    assert findings[0].tier == 0


async def test_non_kev_below_min_cvss_still_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 2: non-KEV below MIN_CVSS continues to be filtered (noise control)."""
    findings = await _ingest(
        monkeypatch,
        kev_cves=set(),
        matches=[_grype_match("CVE-NON-01", cvss=7.5)],
        min_cvss=9.0,
    )
    assert findings == []


async def test_non_kev_above_min_cvss_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scenario 3: non-KEV ≥ MIN_CVSS unchanged (tier=2 since internet_facing=False)."""
    findings = await _ingest(
        monkeypatch,
        kev_cves=set(),
        matches=[_grype_match("CVE-HIGH-01", cvss=9.5)],
        min_cvss=9.0,
    )
    assert len(findings) == 1
    assert findings[0].kev is False
    assert findings[0].tier == 2


async def test_kev_above_min_cvss_still_tier_0(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 4 regression guard: KEV ≥ MIN_CVSS keeps working post-BC-023."""
    findings = await _ingest(
        monkeypatch,
        kev_cves={"CVE-KEV-HIGH"},
        matches=[_grype_match("CVE-KEV-HIGH", cvss=9.8)],
        min_cvss=9.0,
    )
    assert len(findings) == 1
    assert findings[0].tier == 0
    assert findings[0].kev is True


# ── BC-028: KEV lookup resolves grype's relatedVulnerabilities aliases ───────


async def test_kev_resolved_via_related_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """BC-028: GO-* primary id with a CVE alias in KEV → finding gets tier=0.

    Non-CVE primary ids (GO-*, GHSA-*) silently miss KEV lookups when we only
    key on vuln["id"]. BC-028 extended ingest to check grype's
    relatedVulnerabilities list too. CVE-2023-44487 (HTTP/2 Rapid Reset) is a
    real-world case: Go stdlib emits GO-2023-1703 primary with CVE alias."""
    match_with_alias = {
        "vulnerability": {
            "id": "GO-2023-1703",
            "severity": "High",
            "cvss": [{"metrics": {"baseScore": 7.5}}],
            "description": "fixture",
            "fix": {"versions": ["1.21"]},
        },
        "artifact": {"name": "stdlib", "version": "1.20"},
        "relatedVulnerabilities": [{"id": "CVE-2023-44487"}],
    }
    findings = await _ingest(
        monkeypatch,
        kev_cves={"CVE-2023-44487"},
        matches=[match_with_alias],
        min_cvss=9.0,  # 7.5 < 9.0 — bypasses MIN_CVSS only if KEV detected
    )
    assert len(findings) == 1, "KEV via alias must survive the MIN_CVSS filter"
    f = findings[0]
    assert f.kev is True, "alias in KEV catalog must set finding.kev=True"
    assert f.tier == 0
    assert f.cve_id == "GO-2023-1703", "primary id is preserved for display"
