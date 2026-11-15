"""BC-117 — Cross-repo CVE fix workflow.

Covers:
    - CAP-T219: policy-driven `awaiting_upstream` status. `save_finding`
      matches `BEACONRY_CROSSREPO_SOURCES` → persists status +
      `cross_repo_hint` JSON. Telegram body + keyboard route through
      a dedicated path. Operator's [✅ Accept risk] tap transitions to
      `accepted`; [📝 Open issue] fallback does not mutate status.
      BC-086 absent-reconcile closes when upstream ships. Empty env
      disables the feature entirely.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from poc.hub import audit as _audit_mod
from poc.hub import hub
from poc.hub.executor.base import Finding


@pytest.fixture
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setattr(_audit_mod, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(_audit_mod, "AUDIT_PATH", data_dir / "audit.log", raising=False)
    _audit_mod._degraded = False
    _audit_mod._last_error = ""
    _audit_mod._write_failures = 0
    _audit_mod._write_failures_since_degraded = 0
    hub.init_db()
    return data_dir


def _finding(fid: str = "fid-1", source_ref: str = "nginx:alpine") -> Finding:
    return Finding(
        id=fid,
        host="h-bb",
        cve_id="CVE-2026-111497",
        package="brotli-libs",
        version="1.2.0",
        fixed_version="1.2.1",
        severity="Critical",
        cvss=9.8,
        source_type="docker",
        source_ref=source_ref,
        description="brotli decompression flaw",
        tier=1,
        kev=False,
        internet_facing=True,
    )


# ── Pure helpers: policy parser + matcher + URL builder ─────────────────────


def test_parse_crossrepo_policy_single_entry() -> None:
    raw = "nginx:*=docker-library/official-images|https://example.test/i?t={{cve_id}}"
    policy = hub._parse_crossrepo_policy(raw)
    assert len(policy) == 1
    assert policy[0]["glob"] == "nginx:*"
    assert policy[0]["repo"] == "docker-library/official-images"
    assert "{{cve_id}}" in policy[0]["issue_template"]


def test_parse_crossrepo_policy_multi_entry() -> None:
    raw = "nginx:*=docker-library/official-images;postgres:*=docker-library/postgres"
    policy = hub._parse_crossrepo_policy(raw)
    assert len(policy) == 2
    assert policy[0]["glob"] == "nginx:*"
    assert policy[1]["glob"] == "postgres:*"
    # No URL template → empty string.
    assert policy[0]["issue_template"] == ""


def test_parse_crossrepo_policy_malformed_entries(caplog) -> None:
    """Missing '=', empty glob, empty repo → logged + skipped, not fatal."""
    raw = ";garbage;nginx:*=repo;=/orphan;valid:*=real-repo"
    policy = hub._parse_crossrepo_policy(raw)
    # Only the two well-formed entries land.
    globs = [p["glob"] for p in policy]
    assert "nginx:*" in globs
    assert "valid:*" in globs
    assert len(policy) == 2


def test_empty_env_leaves_policy_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_CROSSREPO_SOURCES", "")
    import importlib

    importlib.reload(hub)
    assert hub.CROSSREPO_POLICY == []
    assert hub._match_crossrepo_policy("nginx:alpine") is None


def test_match_crossrepo_policy_first_match_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hub,
        "CROSSREPO_POLICY",
        [
            {"glob": "nginx:1.24*", "repo": "specific", "issue_template": ""},
            {"glob": "nginx:*", "repo": "general", "issue_template": ""},
        ],
    )
    match_specific = hub._match_crossrepo_policy("nginx:1.24-alpine")
    assert match_specific is not None
    assert match_specific["repo"] == "specific"
    match_general = hub._match_crossrepo_policy("nginx:alpine")
    assert match_general is not None
    assert match_general["repo"] == "general"
    assert hub._match_crossrepo_policy("redis:7") is None


def test_build_issue_url_substitutes_placeholders() -> None:
    tpl = "https://host/new?cve={{cve_id}}&pkg={{package}}&ref={{source_ref}}"
    url = hub._build_issue_url(
        tpl, cve_id="CVE-2026-1", package="brotli libs", source_ref="nginx:alpine"
    )
    assert "CVE-2026-1" in url
    # Space in package name is URL-encoded.
    assert "brotli%20libs" in url
    assert "nginx%3Aalpine" in url


def test_build_issue_url_empty_template_returns_empty() -> None:
    assert hub._build_issue_url("", cve_id="x", package="p", source_ref="s") == ""


# ── save_finding → routing ─────────────────────────────────────────────────


def test_save_finding_matches_policy_routes_to_awaiting_upstream(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        hub,
        "CROSSREPO_POLICY",
        [{"glob": "nginx:*", "repo": "docker-library/x", "issue_template": ""}],
    )
    f = _finding(source_ref="nginx:alpine")
    assert hub.save_finding(f) is True

    con = sqlite3.connect(db_env / "beaconry.db")
    try:
        row = con.execute(
            "SELECT status, cross_repo_hint FROM findings WHERE id=?", (f.id,)
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    status, hint_raw = row
    assert status == "awaiting_upstream"
    import json as _json

    hint = _json.loads(hint_raw)
    assert hint["repo"] == "docker-library/x"
    assert hint["policy_key"] == "nginx:*"


def test_save_finding_without_match_uses_open_status(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        hub,
        "CROSSREPO_POLICY",
        [{"glob": "nginx:*", "repo": "x", "issue_template": ""}],
    )
    f = _finding(source_ref="redis:7")
    hub.save_finding(f)

    con = sqlite3.connect(db_env / "beaconry.db")
    try:
        row = con.execute(
            "SELECT status, cross_repo_hint FROM findings WHERE id=?", (f.id,)
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    status, hint_raw = row
    assert status == "open"
    assert hint_raw is None


def test_save_finding_emits_enriched_audit(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hub,
        "CROSSREPO_POLICY",
        [{"glob": "nginx:*", "repo": "docker-library/x", "issue_template": ""}],
    )
    f = _finding(source_ref="nginx:alpine")
    hub.save_finding(f)

    log_path = db_env / "audit.log"
    import json as _json

    events = [_json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    ing = [e for e in events if e.get("event") == "finding_ingested"]
    assert len(ing) == 1
    assert ing[0].get("cross_repo_policy_key") == "nginx:*"
    assert ing[0].get("cross_repo_repo") == "docker-library/x"


# ── load_cross_repo_hint ───────────────────────────────────────────────────


def test_load_cross_repo_hint_returns_none_for_open_finding(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub, "CROSSREPO_POLICY", [])
    f = _finding(source_ref="redis:7")
    hub.save_finding(f)
    assert hub.load_cross_repo_hint(f.id) is None


def test_load_cross_repo_hint_round_trip(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        hub,
        "CROSSREPO_POLICY",
        [{"glob": "nginx:*", "repo": "my/repo", "issue_template": "https://e/new"}],
    )
    f = _finding(source_ref="nginx:alpine")
    hub.save_finding(f)
    hint = hub.load_cross_repo_hint(f.id)
    assert hint is not None
    assert hint["repo"] == "my/repo"
    assert hint["policy_key"] == "nginx:*"


# ── Formatter + keyboard ───────────────────────────────────────────────────


def test_format_awaiting_upstream_body_shape() -> None:
    f = _finding()
    hint = {
        "repo": "docker-library/official-images",
        "issue_url": "",
        "policy_key": "nginx:*",
    }
    body = hub.format_awaiting_upstream(f, hint)
    assert "🤝" in body
    assert "Awaiting upstream" in body
    assert "docker-library/official-images" in body
    assert "nginx:*" in body


def test_kb_awaiting_upstream_url_button_when_issue_url_present() -> None:
    f = _finding()
    hint = {"repo": "a/b", "issue_url": "https://example.test/new", "policy_key": "x"}
    kb = hub._kb_awaiting_upstream(f, hint)
    # Flatten the keyboard; find the open-issue button.
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    open_issue = next((b for b in buttons if "Open issue" in b.text), None)
    assert open_issue is not None
    assert open_issue.url == "https://example.test/new"
    # No `[Fix now]`.
    assert not any("Fix now" in b.text for b in buttons)
    # Has `[Accept risk]`.
    assert any("Accept risk" in b.text for b in buttons)


def test_kb_awaiting_upstream_callback_fallback_when_url_empty() -> None:
    f = _finding()
    hint = {"repo": "a/b", "issue_url": "", "policy_key": "x"}
    kb = hub._kb_awaiting_upstream(f, hint)
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    open_issue = next((b for b in buttons if "Open issue" in b.text), None)
    assert open_issue is not None
    assert open_issue.url is None  # Callback button, not URL button.
    cb_data = open_issue.callback_data
    assert cb_data is not None
    assert isinstance(cb_data, str)
    assert cb_data.startswith("open_issue:")


# ── reconcile_scan scope ───────────────────────────────────────────────────


def test_reconcile_scan_scope_includes_awaiting_upstream(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upstream-shipped CVE disappears from scan → BC-086 absent-reconcile
    closes the awaiting_upstream finding to auto_fixed."""
    monkeypatch.setattr(hub, "RECONCILE_ENABLED", True)
    monkeypatch.setattr(hub, "AUTO_FIX_THRESHOLD", 3)
    monkeypatch.setattr(
        hub,
        "CROSSREPO_POLICY",
        [{"glob": "nginx:*", "repo": "x", "issue_template": ""}],
    )
    # Insert directly to set absent_count for threshold-trip.
    con = sqlite3.connect(db_env / "beaconry.db")
    try:
        con.execute(
            "INSERT INTO findings "
            "(id, host, cve_id, package, version, fixed_version, severity, cvss, "
            "source_type, source_ref, description, tier, kev, status, found_at, "
            "last_seen_ts, absent_count, cross_repo_hint, last_status_change_ts) "
            "VALUES (?, 'h-bb', 'CVE-X', 'brotli', '1.0', '1.1', 'Critical', 9.8, "
            "'docker', 'nginx:alpine', '', 1, 0, 'awaiting_upstream', ?, ?, 2, '{}', ?)",
            (
                "fid-recon",
                int(time.time()),
                int(time.time()),
                int(time.time()),
            ),
        )
        con.commit()
    finally:
        con.close()

    regressed_fix, auto_closes, regressed_auto = hub.reconcile_scan(host="h-bb", scanned_ids=set())
    assert len(auto_closes) == 1
    assert auto_closes[0].id == "fid-recon"

    con = sqlite3.connect(db_env / "beaconry.db")
    try:
        row = con.execute(
            "SELECT status, close_reason FROM findings WHERE id='fid-recon'"
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    status, close_reason = row
    assert status == "auto_fixed"
    assert close_reason == "post_absent"


# ── Callback handlers ──────────────────────────────────────────────────────


class _FakeQuery:
    def __init__(self):
        self.last_text = ""

    async def edit_message_text(self, text, **kwargs):
        self.last_text = text


@pytest.mark.asyncio
async def test_handle_ack_transitions_awaiting_upstream_to_accepted(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        hub,
        "CROSSREPO_POLICY",
        [{"glob": "nginx:*", "repo": "x", "issue_template": ""}],
    )
    f = _finding(source_ref="nginx:alpine")
    hub.save_finding(f)
    q = _FakeQuery()
    await hub._handle_ack(q, f, f.id, parts=["ack", f.id])

    con = sqlite3.connect(db_env / "beaconry.db")
    try:
        row = con.execute(
            "SELECT status, close_reason FROM findings WHERE id=?", (f.id,)
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    status, close_reason = row
    assert status == "accepted"
    assert close_reason == "operator"  # BC-105 convention
    assert "Risk accepted" in q.last_text


@pytest.mark.asyncio
async def test_handle_open_issue_emits_audit_and_does_not_mutate_status(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        hub,
        "CROSSREPO_POLICY",
        [{"glob": "nginx:*", "repo": "docker-library/x", "issue_template": ""}],
    )
    f = _finding(source_ref="nginx:alpine")
    hub.save_finding(f)
    q = _FakeQuery()
    await hub._handle_open_issue(q, f, f.id, parts=["open_issue", f.id])

    # Status unchanged.
    con = sqlite3.connect(db_env / "beaconry.db")
    try:
        status = con.execute("SELECT status FROM findings WHERE id=?", (f.id,)).fetchone()[0]
    finally:
        con.close()
    assert status == "awaiting_upstream"

    # Audit event fired.
    log_path = db_env / "audit.log"
    import json as _json

    events = [_json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    opens = [e for e in events if e.get("event") == "cross_repo_open_issue"]
    assert len(opens) == 1
    assert opens[0].get("repo") == "docker-library/x"
    assert opens[0].get("policy_key") == "nginx:*"
    assert "Noted" in q.last_text


# ── Schema migration ───────────────────────────────────────────────────────


def test_schema_v11_adds_cross_repo_hint(db_env: Path) -> None:
    con = sqlite3.connect(db_env / "beaconry.db")
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
        version = con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()
    assert "cross_repo_hint" in cols
    assert version == 11


# ── _VALID_STATUSES extension ──────────────────────────────────────────────


def test_valid_statuses_includes_awaiting_upstream() -> None:
    assert "awaiting_upstream" in hub._VALID_STATUSES


def test_set_status_accepts_awaiting_upstream(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub, "CROSSREPO_POLICY", [])
    f = _finding(source_ref="redis:7")
    hub.save_finding(f)
    hub.set_status(f.id, "awaiting_upstream")
    con = sqlite3.connect(db_env / "beaconry.db")
    try:
        row = con.execute("SELECT status FROM findings WHERE id=?", (f.id,)).fetchone()
    finally:
        con.close()
    assert row[0] == "awaiting_upstream"
