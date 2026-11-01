"""BC-055 — HTTP-only CLI → hub.

Covers:
    - CAP-T109: Hub HTTP management surface (/findings, /audit, /fleet,
      /scan/trigger, PATCH /findings/{id})
    - CAP-T110: Operator-CLI config loader + HTTP routing
    - CAP-T111: PATCH /findings/{id} mutation endpoint
    - CAP-B038: Operator manages beaconry on a remote hub from their
      laptop over HTTP only
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# ── Hub fixture ────────────────────────────────────────────────────────────


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.delenv("HUB_CLI_TOKEN", raising=False)

    import hub

    importlib.reload(hub)
    hub.init_db()
    client = TestClient(hub.api)
    yield {"client": client, "db": hub.DB_PATH, "audit": data_dir / "audit.log", "hub": hub}
    client.close()
    importlib.reload(hub)


def _insert(hub, host: str, cve: str, *, status: str = "open", last_seen_ts: int = 0) -> str:
    fid = f"{host}-{cve}"
    con = sqlite3.connect(hub.DB_PATH)
    con.execute(
        "INSERT INTO findings (id, host, cve_id, package, version, "
        "fixed_version, severity, cvss, source_type, source_ref, "
        "description, status, tier, last_seen_ts) VALUES "
        "(?, ?, ?, 'p', '1', '2', 'High', 7.5, 'docker', 'nginx:1', '', ?, 2, ?)",
        (fid, host, cve, status, last_seen_ts),
    )
    con.commit()
    con.close()
    return fid


def _append_audit(path: Path, events: list[dict]) -> None:
    with path.open("a") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


# ── /findings ──────────────────────────────────────────────────────────────


def test_findings_endpoint_returns_rows(hub_env) -> None:
    """CAP-T109: /findings returns JSON array with the full column set."""
    _insert(hub_env["hub"], "laptop", "CVE-A")
    _insert(hub_env["hub"], "server2", "CVE-B")
    r = hub_env["client"].get("/findings")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    for row in rows:
        for col in ("kev", "internet_facing", "cve_id", "severity", "cvss", "host", "status"):
            assert col in row


def test_findings_endpoint_filters(hub_env) -> None:
    """CAP-T109: status+host filters compose."""
    _insert(hub_env["hub"], "laptop", "CVE-A", status="open")
    _insert(hub_env["hub"], "laptop", "CVE-B", status="fixed")
    _insert(hub_env["hub"], "server2", "CVE-C", status="open")
    r = hub_env["client"].get("/findings?host=laptop&status=open")
    assert {row["cve_id"] for row in r.json()} == {"CVE-A"}


# ── /audit ─────────────────────────────────────────────────────────────────


def test_audit_endpoint_returns_events(hub_env) -> None:
    """CAP-T109: /audit returns events newest-first."""
    _append_audit(
        hub_env["audit"],
        [
            {"ts": 1000, "event": "finding_ingested"},
            {"ts": 2000, "event": "status_change"},
            {"ts": 3000, "event": "notify_sent"},
        ],
    )
    r = hub_env["client"].get("/audit?last=10")
    assert r.status_code == 200
    assert [e["ts"] for e in r.json()] == [3000, 2000, 1000]


# ── /fleet ─────────────────────────────────────────────────────────────────


def test_fleet_endpoint_returns_per_host(hub_env) -> None:
    """CAP-T109: /fleet aggregates by host, sorted newest-first."""
    _insert(hub_env["hub"], "server2", "CVE-A", last_seen_ts=1000)
    _insert(hub_env["hub"], "laptop", "CVE-B", last_seen_ts=2000)
    rows = hub_env["client"].get("/fleet").json()
    assert [r["host"] for r in rows] == ["laptop", "server2"]
    assert rows[0]["total_findings"] == 1


# ── /scan/trigger proxy ────────────────────────────────────────────────────


def test_scan_trigger_proxies(hub_env, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T109: /scan/trigger on the hub proxies to the scout."""
    captured: dict = {}

    class _FakeResp:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"status":"scheduled","started_at":42}'

    def fake_urlopen(req, timeout: float = 0):  # type: ignore[no-untyped-def]
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    r = hub_env["client"].post("/scan/trigger")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "scheduled"
    assert captured["method"] == "POST"
    assert captured["url"] == "http://scout:8001/scan/trigger"


def test_scan_trigger_503_when_scout_unreachable(hub_env, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req, timeout: float = 0):  # type: ignore[no-untyped-def]
        raise urllib.error.URLError("no scout")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    r = hub_env["client"].post("/scan/trigger")
    assert r.status_code == 503
    assert "scout unreachable" in r.json()["detail"]


# ── PATCH /findings/{id} ──────────────────────────────────────────────────


def test_patch_status_transitions(hub_env) -> None:
    """CAP-T111: PATCH updates the DB row + writes a status_change audit event."""
    fid = _insert(hub_env["hub"], "laptop", "CVE-X", status="open")
    r = hub_env["client"].patch(f"/findings/{fid}", json={"status": "snoozed"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "snoozed"
    assert body["old_status"] == "open"

    con = sqlite3.connect(hub_env["db"])
    row = con.execute("SELECT status FROM findings WHERE id = ?", (fid,)).fetchone()
    con.close()
    assert row[0] == "snoozed"

    audit = hub_env["audit"].read_text()
    assert "status_change" in audit
    assert "snoozed" in audit


def test_patch_status_404_on_unknown_id(hub_env) -> None:
    """CAP-T111: unknown finding id → 404."""
    r = hub_env["client"].patch("/findings/nope", json={"status": "snoozed"})
    assert r.status_code == 404


def test_patch_status_rejects_bad_status(hub_env) -> None:
    """CAP-T111: invalid status → 400 with the allowed set in the message."""
    fid = _insert(hub_env["hub"], "laptop", "CVE-X")
    r = hub_env["client"].patch(f"/findings/{fid}", json={"status": "banana"})
    assert r.status_code == 400
    assert "snoozed" in r.json()["detail"]


# ── Auth gating ────────────────────────────────────────────────────────────


def test_all_endpoints_auth_gated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T109: /findings, /audit, /fleet, /scan/trigger, PATCH /findings
    all require Bearer when HUB_CLI_TOKEN is set."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("HUB_CLI_TOKEN", "sekret")

    import hub

    importlib.reload(hub)
    client = TestClient(hub.api)
    try:
        for method, path, payload in (
            ("GET", "/findings", None),
            ("GET", "/audit", None),
            ("GET", "/fleet", None),
            ("POST", "/scan/trigger", {}),
            ("PATCH", "/findings/x", {"status": "snoozed"}),
        ):
            r = client.request(method, path, json=payload)
            assert r.status_code == 401, f"{method} {path} must be 401 without Bearer"
        # With the right token, no auth error (code may be 200/404/503 but not 401).
        for method, path, payload in (
            ("GET", "/findings", None),
            ("GET", "/fleet", None),
        ):
            r = client.request(
                method, path, json=payload, headers={"Authorization": "Bearer sekret"}
            )
            assert r.status_code == 200
    finally:
        client.close()


def test_scout_and_cli_tokens_are_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T109: `/scan` gates on HUB_SCOUT_TOKEN; the operator surface
    gates on HUB_CLI_TOKEN. CLI-token leak doesn't let an attacker ingest
    SBOMs, and scout-token leak doesn't let them read/mutate findings."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("HUB_SCOUT_TOKEN", "scout-only")
    monkeypatch.setenv("HUB_CLI_TOKEN", "cli-only")

    import hub

    importlib.reload(hub)
    hub.init_db()
    client = TestClient(hub.api)
    try:
        # /scan accepts scout token, rejects CLI token.
        r = client.post(
            "/scan",
            json={"host": "h", "sboms": []},
            headers={"Authorization": "Bearer scout-only"},
        )
        assert r.status_code == 200
        r = client.post(
            "/scan",
            json={"host": "h", "sboms": []},
            headers={"Authorization": "Bearer cli-only"},
        )
        assert r.status_code == 401

        # /findings accepts CLI token, rejects scout token.
        r = client.get("/findings", headers={"Authorization": "Bearer cli-only"})
        assert r.status_code == 200
        r = client.get("/findings", headers={"Authorization": "Bearer scout-only"})
        assert r.status_code == 401
    finally:
        client.close()


def test_health_bypasses_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T109: /livez /readyz /healthz /startupz always reachable without token."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("HUB_CLI_TOKEN", "sekret")

    import hub

    importlib.reload(hub)
    client = TestClient(hub.api)
    try:
        for path in ("/livez", "/readyz", "/healthz", "/startupz"):
            r = client.get(path)
            assert r.status_code in (200, 503), f"{path} must be 200/503 without auth"
    finally:
        client.close()


# ── CliConfig loader ──────────────────────────────────────────────────────


def test_cli_config_env_overrides_yaml(tmp_path: Path) -> None:
    """CAP-T110: env > yaml."""
    from poc.scripts.cli_config import load_cli_config

    path = tmp_path / "cli.yml"
    path.write_text("hub_url: http://yaml-hub:8000\n")
    cfg = load_cli_config(path, env={"HUB_URL": "http://env-hub:8000"})
    assert cfg.hub_url == "http://env-hub:8000"


def test_cli_config_empty_env_skipped(tmp_path: Path) -> None:
    """CAP-T110: empty env doesn't clobber YAML."""
    from poc.scripts.cli_config import load_cli_config

    path = tmp_path / "cli.yml"
    path.write_text("hub_token: s3cr3t\n")
    cfg = load_cli_config(path, env={"HUB_TOKEN": ""})
    assert cfg.hub_token == "s3cr3t"


def test_cli_config_missing_file_uses_defaults(tmp_path: Path) -> None:
    from poc.scripts.cli_config import CliConfig, load_cli_config

    cfg = load_cli_config(tmp_path / "nope.yml", env={})
    assert isinstance(cfg, CliConfig)
    assert cfg.hub_url == "http://localhost:8000"
    assert cfg.source_path == ""


def test_cli_config_malformed_raises(tmp_path: Path) -> None:
    from poc.scripts.cli_config import CliConfigError, load_cli_config

    path = tmp_path / "cli.yml"
    path.write_text("hub_url: [unclosed\n")
    with pytest.raises(CliConfigError):
        load_cli_config(path, env={})


# ── choose_source routing matrix ──────────────────────────────────────────


def test_choose_source_routing() -> None:
    """CAP-T110: three states, driven by hub_url + compose + force_local."""
    from poc.scripts.cli_config import CliConfig
    from poc.scripts.cli_http import choose_source

    remote = CliConfig(hub_url="http://100.1.2.3:8000")
    local = CliConfig(hub_url="http://localhost:8000")

    assert choose_source(remote, compose_hub_running=False) == "http"
    assert choose_source(remote, compose_hub_running=True) == "http"
    assert choose_source(local, compose_hub_running=True) == "local-compose"
    assert choose_source(local, compose_hub_running=False) == "local-file"
    assert choose_source(remote, compose_hub_running=True, force_local=True) == "local-file"


def test_is_localhost_url_variants() -> None:
    from poc.scripts.cli_http import is_localhost_url

    assert is_localhost_url("http://localhost:8000") is True
    assert is_localhost_url("http://127.0.0.1:8000") is True
    assert is_localhost_url("http://[::1]:8000") is True
    assert is_localhost_url("") is True
    assert is_localhost_url("http://100.1.2.3:8000") is False


# ── CLI subprocess paths ──────────────────────────────────────────────────


def test_findings_cli_uses_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T110: findings.py with a remote hub_url skips docker and GETs the URL."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_dir = tmp_path / "beaconry"
    cfg_dir.mkdir()
    (cfg_dir / "cli.yml").write_text("hub_url: http://100.1.2.3:8000\nhub_token: tok\n")
    monkeypatch.delenv("BEACONRY_FINDINGS_LOCAL", raising=False)
    monkeypatch.delenv("HUB_URL", raising=False)
    monkeypatch.delenv("HUB_TOKEN", raising=False)

    captured: dict = {}

    def fake_urlopen(req, timeout: float = 0):  # type: ignore[no-untyped-def]
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization", "")

        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"[]"

        return _R()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    from poc.scripts import findings as findings_mod

    # findings.py defers to _running_compose_hub(); stub False so routing hits HTTP.
    monkeypatch.setattr(findings_mod, "_running_compose_hub", lambda: False)
    rc = findings_mod.main([])
    assert rc == 0
    assert captured["url"] == "http://100.1.2.3:8000/findings"
    assert captured["auth"] == "Bearer tok"


def test_set_status_patches_with_bearer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T111: `make set-status FINDING=X STATUS=Y` PATCHes with Bearer header."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_dir = tmp_path / "beaconry"
    cfg_dir.mkdir()
    (cfg_dir / "cli.yml").write_text("hub_url: http://100.1.2.3:8000\nhub_token: tok\n")
    monkeypatch.delenv("HUB_URL", raising=False)
    monkeypatch.delenv("HUB_TOKEN", raising=False)
    monkeypatch.setenv("FINDING", "fid-x")
    monkeypatch.setenv("STATUS", "snoozed")

    captured: dict = {}

    def fake_urlopen(req, timeout: float = 0):  # type: ignore[no-untyped-def]
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["auth"] = req.get_header("Authorization", "")
        captured["body"] = req.data

        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"id":"fid-x","host":"laptop","cve_id":"CVE-X","old_status":"open","status":"snoozed"}'

        return _R()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    from poc.scripts import set_status

    rc = set_status.main()
    assert rc == 0
    assert captured["method"] == "PATCH"
    assert captured["url"] == "http://100.1.2.3:8000/findings/fid-x"
    assert captured["auth"] == "Bearer tok"
    assert b'"status": "snoozed"' in captured["body"]


def test_set_status_rejects_bad_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDING", "fid-x")
    monkeypatch.setenv("STATUS", "banana")
    from poc.scripts import set_status

    rc = set_status.main()
    assert rc == 2


def test_set_status_requires_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINDING", raising=False)
    monkeypatch.setenv("STATUS", "snoozed")
    from poc.scripts import set_status

    rc = set_status.main()
    assert rc == 2


def test_set_status_requires_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINDING", "fid-x")
    monkeypatch.delenv("STATUS", raising=False)
    from poc.scripts import set_status

    rc = set_status.main()
    assert rc == 2


def test_set_status_http_error_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAP-T111: HTTP error from the hub → clean exit 2 with friendly message."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_dir = tmp_path / "beaconry"
    cfg_dir.mkdir()
    (cfg_dir / "cli.yml").write_text("hub_url: http://100.1.2.3:8000\n")
    monkeypatch.delenv("HUB_URL", raising=False)
    monkeypatch.setenv("FINDING", "fid-x")
    monkeypatch.setenv("STATUS", "snoozed")

    def fake_urlopen(req, timeout: float = 0):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", hdrs=None, fp=None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    from poc.scripts import set_status

    rc = set_status.main()
    assert rc == 2


# ── scan_cli routing ──────────────────────────────────────────────────────


def test_scan_cli_uses_hub_when_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T110: scan_cli with a remote hub_url POSTs to /scan/trigger on the hub."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_dir = tmp_path / "beaconry"
    cfg_dir.mkdir()
    (cfg_dir / "cli.yml").write_text("hub_url: http://100.1.2.3:8000\nhub_token: tok\n")
    monkeypatch.delenv("HUB_URL", raising=False)
    monkeypatch.delenv("HUB_TOKEN", raising=False)

    captured: dict = {}

    def fake_urlopen(req, timeout: float = 0):  # type: ignore[no-untyped-def]
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["auth"] = req.get_header("Authorization", "")

        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"status":"scheduled","started_at":42}'

        return _R()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    from poc.scripts import scan_cli

    rc = scan_cli.main()
    assert rc == 0
    assert captured["url"] == "http://100.1.2.3:8000/scan/trigger"
    assert captured["method"] == "POST"
    assert captured["auth"] == "Bearer tok"


def test_scan_cli_409_already_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T110: 409 from the hub maps to exit 2."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_dir = tmp_path / "beaconry"
    cfg_dir.mkdir()
    (cfg_dir / "cli.yml").write_text("hub_url: http://100.1.2.3:8000\n")
    monkeypatch.delenv("HUB_URL", raising=False)

    def fake_urlopen(req, timeout: float = 0):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, 409, "Conflict", hdrs=None, fp=None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    from poc.scripts import scan_cli

    rc = scan_cli.main()
    assert rc == 2


def test_scan_cli_local_without_scout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T110: localhost cfg + no scout container → exit 1 with hint."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HUB_URL", raising=False)
    from poc.scripts import scan_cli

    monkeypatch.setattr(scan_cli, "_scout_running", lambda: False)
    rc = scan_cli.main()
    assert rc == 1


def test_scan_cli_local_scout_202(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T110: localhost cfg + scout container up → scout-trigger 202 → exit 0."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HUB_URL", raising=False)
    from poc.scripts import scan_cli

    monkeypatch.setattr(scan_cli, "_scout_running", lambda: True)

    class _R:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"status":"scheduled"}'

    def fake_urlopen(req, timeout: float = 0):  # type: ignore[no-untyped-def]
        return _R()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    rc = scan_cli.main()
    assert rc == 0


def test_scan_cli_local_scout_409(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T110: localhost cfg + scout 409 → exit 2."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HUB_URL", raising=False)
    from poc.scripts import scan_cli

    monkeypatch.setattr(scan_cli, "_scout_running", lambda: True)

    def fake_urlopen(req, timeout: float = 0):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(req.full_url, 409, "Conflict", hdrs=None, fp=None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    rc = scan_cli.main()
    assert rc == 2


def test_scan_cli_local_scout_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T110: localhost cfg + scout unreachable → exit 3."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("HUB_URL", raising=False)
    from poc.scripts import scan_cli

    monkeypatch.setattr(scan_cli, "_scout_running", lambda: True)

    def fake_urlopen(req, timeout: float = 0):  # type: ignore[no-untyped-def]
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    rc = scan_cli.main()
    assert rc == 3


def test_scan_cli_scout_running_without_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T110: `_scout_running` returns False when docker CLI is missing."""
    import subprocess

    from poc.scripts import scan_cli

    def fake_run(*a, **kw):  # type: ignore[no-untyped-def]
        raise FileNotFoundError("docker not installed")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert scan_cli._scout_running() is False


# ── cli_http error branches ───────────────────────────────────────────────


def test_friendly_http_error_http_error() -> None:
    from poc.scripts.cli_http import friendly_http_error

    msg = friendly_http_error(
        "http://x/y",
        urllib.error.HTTPError("http://x/y", 401, "unauthorized", {}, None),  # type: ignore[arg-type]
    )
    assert "401" in msg


def test_friendly_http_error_url_error() -> None:
    from poc.scripts.cli_http import friendly_http_error

    msg = friendly_http_error("http://x/y", urllib.error.URLError("no route"))
    assert "URLError" in msg


def test_friendly_http_error_fallback() -> None:
    from poc.scripts.cli_http import friendly_http_error

    msg = friendly_http_error("http://x/y", RuntimeError("boom"))
    assert "unexpected error" in msg
    assert "RuntimeError" in msg


def test_http_post_wraps_non_dict() -> None:
    """CAP-T110: non-dict JSON response wrapped in {"result": …}."""
    import io

    from poc.scripts.cli_http import http_post

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'["x", "y"]'

    def fake_urlopen(req, timeout: float = 0):  # type: ignore[no-untyped-def]
        return _R()

    import poc.scripts.cli_http as mod

    orig = mod.urllib.request.urlopen
    mod.urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
    try:
        got = http_post("http://x/y")
    finally:
        mod.urllib.request.urlopen = orig  # type: ignore[assignment]
    assert got == {"result": ["x", "y"]}
    del io  # keep ruff happy
