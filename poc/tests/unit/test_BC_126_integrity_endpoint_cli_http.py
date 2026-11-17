"""BC-126 — /audit/integrity endpoint + verify-integrity HTTP routing.

Covers:
    - CAP-T229: Hub exposes `GET /audit/integrity?since=24h&host=…`
      behind the existing CLI-token auth. Returns a JSON list of
      scan_integrity audit events, filtered by since + host, capped
      at 10k rows. `beaconry verify-integrity` CLI now routes via
      `choose_source(cfg, …)` identical to `beaconry findings`:
      remote hub → HTTP, local dev-compose → docker-exec, else
      local file. Pre-BC-126 hub 404 → friendly "upgrade hub" error.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poc.hub import hub

# ── Endpoint behavior ──────────────────────────────────────────────────


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setenv("HUB_CLI_TOKEN", "")
    hub.init_db()
    return data_dir


def _write_audit(data_dir: Path, events: list[dict]) -> None:
    audit = data_dir / "audit.log"
    audit.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _ts(h: int = 18, d: int = 16) -> int:
    # BC-131: audit.append writes ts as int(time.time()); tests must mirror
    # that shape so the since-filter comparison path matches production.
    return int(dt.datetime(2026, 11, d, h, 0, 0).timestamp())


def test_endpoint_returns_scan_integrity_events(hub_env: Path) -> None:
    _write_audit(
        hub_env,
        [
            {"event": "scan_integrity", "ts": _ts(), "host": "a", "iteration_hash": "x"},
            {"event": "finding_filtered", "ts": _ts(), "host": "a", "cve_id": "CVE-X"},
        ],
    )
    client = TestClient(hub.api)
    r = client.get("/audit/integrity")
    assert r.status_code == 200
    rows = r.json()
    # Only the scan_integrity event is returned; finding_filtered is skipped.
    assert len(rows) == 1
    assert rows[0]["event"] == "scan_integrity"
    assert rows[0]["iteration_hash"] == "x"


def test_endpoint_since_filter_narrows(hub_env: Path) -> None:
    _write_audit(
        hub_env,
        [
            # Old (a week ago).
            {"event": "scan_integrity", "ts": _ts(d=9), "host": "a", "iteration_hash": "old"},
            # Fresh (~today in wall clock).
            {
                "event": "scan_integrity",
                "ts": int(dt.datetime.now().timestamp()),
                "host": "a",
                "iteration_hash": "new",
            },
        ],
    )
    client = TestClient(hub.api)
    r = client.get("/audit/integrity?since=1h")
    assert r.status_code == 200
    rows = r.json()
    hashes = {row["iteration_hash"] for row in rows}
    assert "new" in hashes
    assert "old" not in hashes


def test_endpoint_host_filter_narrows(hub_env: Path) -> None:
    _write_audit(
        hub_env,
        [
            {"event": "scan_integrity", "ts": _ts(), "host": "a", "iteration_hash": "a1"},
            {"event": "scan_integrity", "ts": _ts(), "host": "b", "iteration_hash": "b1"},
        ],
    )
    client = TestClient(hub.api)
    r = client.get("/audit/integrity?host=a")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["host"] == "a"


def test_endpoint_requires_cli_token(hub_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_CLI_TOKEN", "secret")
    client = TestClient(hub.api)
    r = client.get("/audit/integrity")
    assert r.status_code == 401


def test_endpoint_limit_cap(hub_env: Path) -> None:
    # Write 15 events; request limit=10 → only 10 returned.
    events = [
        {"event": "scan_integrity", "ts": _ts(), "host": "a", "iteration_hash": f"i{i}"}
        for i in range(15)
    ]
    _write_audit(hub_env, events)
    client = TestClient(hub.api)
    r = client.get("/audit/integrity?limit=10")
    assert r.status_code == 200
    assert len(r.json()) == 10


# ── CLI routing ───────────────────────────────────────────────────────


def test_cli_routes_http_when_remote_configured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from poc.scripts import verify_integrity as vi

    class FakeCfg:
        hub_url = "https://remote-hub:8000"
        hub_token = ""

    monkeypatch.setattr("poc.scripts.cli_config.load_cli_config", lambda: FakeCfg())

    fetched: list[str] = []

    def fake_http_get(url: str, token: str = "", timeout: float = 10.0) -> list[dict]:
        fetched.append(url)
        return [
            {
                "event": "scan_integrity",
                "ts": _ts(),
                "host": "a",
                "iteration_hash": "x" * 16,
                "transfer_match": True,
                "n_candidates": 5,
                "n_persisted": 5,
                "n_dedup": 0,
                "n_capped": 0,
                "n_dropped_in_save": 0,
                "n_filtered_other": 0,
                "transfer_hash_claimed": "a",
                "transfer_hash_computed": "a",
            }
        ]

    monkeypatch.setattr("poc.scripts.cli_http.http_get", fake_http_get)

    rc = vi.main(["--since", "24h"])
    out = capsys.readouterr().out
    assert rc == 0
    assert any("audit/integrity" in u for u in fetched)
    assert "since=24h" in fetched[0]
    assert "1 verified" in out


def test_cli_legacy_hub_404_friendly_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from poc.scripts import verify_integrity as vi

    class FakeCfg:
        hub_url = "https://remote-hub:8000"
        hub_token = ""

    monkeypatch.setattr("poc.scripts.cli_config.load_cli_config", lambda: FakeCfg())

    def fake_http_get(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise Exception("HTTP 404 Not Found")

    monkeypatch.setattr("poc.scripts.cli_http.http_get", fake_http_get)

    rc = vi.main([])
    err = capsys.readouterr().err
    assert rc == 2
    assert "pre-BC-126" in err
    assert "upgrade hub" in err


def test_cli_local_file_path_preserved(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    tmp_path: Path,
) -> None:
    """Regression: dev-mode / air-gapped path still works."""
    from poc.scripts import verify_integrity as vi

    class FakeCfg:
        hub_url = "http://localhost:8000"
        hub_token = ""

    monkeypatch.setattr("poc.scripts.cli_config.load_cli_config", lambda: FakeCfg())
    # Force local-file (bypass compose detection) via the new env knob.
    monkeypatch.setenv("BEACONRY_VERIFY_INTEGRITY_LOCAL", "1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    audit = tmp_path / "audit.log"
    audit.write_text(
        json.dumps(
            {
                "event": "scan_integrity",
                "ts": _ts(),
                "host": "a",
                "iteration_hash": "i" * 16,
                "transfer_match": True,
                "n_candidates": 2,
                "n_persisted": 2,
                "n_dedup": 0,
                "n_capped": 0,
                "n_dropped_in_save": 0,
                "n_filtered_other": 0,
            }
        )
        + "\n"
    )
    rc = vi.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 verified" in out


# ── Parse helpers on hub side ─────────────────────────────────────────


def test_parse_since_spec_variants() -> None:
    assert hub._parse_since_spec("30s") is not None
    assert hub._parse_since_spec("60m") is not None
    assert hub._parse_since_spec("24h") is not None
    assert hub._parse_since_spec("7d") is not None
    assert hub._parse_since_spec("garbage") is None
    assert hub._parse_since_spec("") is None
