"""BC-076 — `beaconry metrics --remote` reads from hub /audit endpoint.

Covers:
    - CAP-T158: metrics remote mode (HTTP fetch via /audit)
    - CAP-T159: auto-routing matches `beaconry status` / `findings`
"""

from __future__ import annotations

import json
import time
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from poc.scripts import metrics
from poc.scripts.cli_config import CliConfig

# ── Helpers ─────────────────────────────────────────────────────────────────


def _write_audit(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


class _FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, code: int, body: bytes = b""):
        super().__init__(url="x", code=code, msg="x", hdrs=None, fp=None)  # type: ignore[arg-type]
        self._body = body

    def read(self) -> bytes:  # type: ignore[override]
        return self._body


# ── CAP-T159: auto-routing ──────────────────────────────────────────────────


def test_auto_mode_picks_local_for_localhost_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T159: cli.yml with hub_url=localhost → local file source."""
    monkeypatch.setattr(
        "poc.scripts.cli_config.load_cli_config",
        lambda: CliConfig(hub_url="http://localhost:8000", hub_token=""),
    )
    args = _parse([])
    assert metrics._choose_source(args) == "local"


def test_auto_mode_picks_remote_for_remote_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T159: cli.yml with hub_url=remote-IP → remote source."""
    monkeypatch.setattr(
        "poc.scripts.cli_config.load_cli_config",
        lambda: CliConfig(hub_url="http://100.77.182.73:8000", hub_token="t"),
    )
    args = _parse([])
    assert metrics._choose_source(args) == "remote"


def test_auto_mode_falls_back_to_local_when_hub_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CAP-T159: empty hub_url → local file."""
    monkeypatch.setattr(
        "poc.scripts.cli_config.load_cli_config",
        lambda: CliConfig(hub_url="", hub_token=""),
    )
    args = _parse([])
    assert metrics._choose_source(args) == "local"


# ── CAP-T158: explicit flags ────────────────────────────────────────────────


def test_explicit_remote_flag_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T158: --remote overrides auto-route."""
    monkeypatch.setattr(
        "poc.scripts.cli_config.load_cli_config",
        lambda: CliConfig(hub_url="http://localhost:8000", hub_token=""),
    )
    args = _parse(["--remote"])
    assert metrics._choose_source(args) == "remote"


def test_explicit_local_flag_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T158: --local overrides auto-route."""
    monkeypatch.setattr(
        "poc.scripts.cli_config.load_cli_config",
        lambda: CliConfig(hub_url="http://100.1.2.3:8000", hub_token=""),
    )
    args = _parse(["--local"])
    assert metrics._choose_source(args) == "local"


def test_mutually_exclusive_source_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CAP-T158: --remote + --local → argparse usage error (exit 2)."""
    with pytest.raises(SystemExit) as excinfo:
        metrics.main(["--remote", "--local"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "not allowed with" in err or "mutually exclusive" in err


# ── CAP-T158: remote fetch mechanics ────────────────────────────────────────


def test_remote_mode_fetches_from_audit_endpoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T158: --remote calls http_get on the hub's /audit URL."""
    now = int(time.time())
    captured: dict[str, object] = {}

    def _fake_http_get(url: str, token: str = "", timeout: float = 10.0) -> list[dict]:
        captured["url"] = url
        captured["token"] = token
        return [{"ts": now - 100, "event": "notify_sent", "finding_id": "f1"}]

    monkeypatch.setattr(
        "poc.scripts.cli_config.load_cli_config",
        lambda: CliConfig(hub_url="http://100.1.2.3:8000", hub_token="secret"),
    )
    with patch("poc.scripts.cli_http.http_get", side_effect=_fake_http_get):
        rc = metrics.main(["--remote", "--since", "1h"])
    assert rc == 0
    assert isinstance(captured["url"], str)
    url = str(captured["url"])
    assert "/audit" in url
    assert "last=10000" in url
    assert "since=" in url
    out = capsys.readouterr().out
    assert "remote" in out
    assert "100.1.2.3" in out


def test_remote_mode_sends_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T158: hub_token from cli.yml flows into http_get."""
    captured: dict[str, object] = {}

    def _fake_http_get(url: str, token: str = "", timeout: float = 10.0) -> list[dict]:
        captured["token"] = token
        return []

    monkeypatch.setattr(
        "poc.scripts.cli_config.load_cli_config",
        lambda: CliConfig(hub_url="http://100.1.2.3:8000", hub_token="my-bearer-token"),
    )
    with patch("poc.scripts.cli_http.http_get", side_effect=_fake_http_get):
        metrics.main(["--remote"])
    assert captured["token"] == "my-bearer-token"


def test_remote_mode_passes_since_as_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T158: --since 7d translates into since=<timestamp> query param."""
    captured: dict[str, object] = {}

    def _fake_http_get(url: str, token: str = "", timeout: float = 10.0) -> list[dict]:
        captured["url"] = url
        return []

    monkeypatch.setattr(
        "poc.scripts.cli_config.load_cli_config",
        lambda: CliConfig(hub_url="http://100.1.2.3:8000", hub_token="t"),
    )
    now_before = int(time.time())
    with patch("poc.scripts.cli_http.http_get", side_effect=_fake_http_get):
        metrics.main(["--remote", "--since", "7d"])
    url = str(captured["url"])
    # since=<ts> should be approximately now - 7*86400
    import re

    m = re.search(r"since=(\d+)", url)
    assert m
    since_ts = int(m.group(1))
    expected = now_before - 7 * 86400
    assert abs(since_ts - expected) < 5  # tolerate test runtime


# ── CAP-T158: error handling ────────────────────────────────────────────────


def test_remote_mode_401_renders_friendly_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T158: HTTP 401 maps to a human-readable error, not a traceback."""
    monkeypatch.setattr(
        "poc.scripts.cli_config.load_cli_config",
        lambda: CliConfig(hub_url="http://100.1.2.3:8000", hub_token="wrong"),
    )
    with patch("poc.scripts.cli_http.http_get", side_effect=_FakeHTTPError(401, b"unauthorized")):
        rc = metrics.main(["--remote"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "401" in err or "returned 401" in err


def test_remote_mode_connection_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T158: ConnectionError → "failed to reach hub" message."""
    monkeypatch.setattr(
        "poc.scripts.cli_config.load_cli_config",
        lambda: CliConfig(hub_url="http://100.1.2.3:8000", hub_token="t"),
    )
    with patch(
        "poc.scripts.cli_http.http_get", side_effect=urllib.error.URLError("connection refused")
    ):
        rc = metrics.main(["--remote"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "failed to reach hub" in err


def test_remote_mode_cap_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T158: response hitting the 10000-event cap prints a truncation warning."""
    now = int(time.time())
    big = [
        {"ts": now - i, "event": "notify_sent", "finding_id": f"f{i}"}
        for i in range(metrics.REMOTE_AUDIT_CAP)
    ]
    monkeypatch.setattr(
        "poc.scripts.cli_config.load_cli_config",
        lambda: CliConfig(hub_url="http://100.1.2.3:8000", hub_token="t"),
    )
    with patch("poc.scripts.cli_http.http_get", side_effect=lambda *_a, **_k: big):
        rc = metrics.main(["--remote"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "hit the /audit cap" in out


def test_remote_mode_rejects_non_list_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T158: a dict/str response from the server triggers a RuntimeError."""
    monkeypatch.setattr(
        "poc.scripts.cli_config.load_cli_config",
        lambda: CliConfig(hub_url="http://100.1.2.3:8000", hub_token="t"),
    )
    with patch("poc.scripts.cli_http.http_get", side_effect=lambda *_a, **_k: {"oops": True}):
        rc = metrics.main(["--remote"])
    assert rc == 2


# ── Local mode still works ──────────────────────────────────────────────────


def test_local_mode_reads_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """BC-075 regression guard: --local still works unchanged."""
    now = int(time.time())
    _write_audit(
        tmp_path / "audit.log",
        [{"ts": now - 60, "event": "notify_sent", "finding_id": "f1"}],
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    rc = metrics.main(["--local"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "local file" in out


# ── Config missing hub_url when --remote ─────────────────────────────────────


def test_remote_mode_fails_when_hub_url_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CAP-T158: --remote with empty hub_url → clear setup message."""
    monkeypatch.setattr(
        "poc.scripts.cli_config.load_cli_config",
        lambda: CliConfig(hub_url="", hub_token=""),
    )
    rc = metrics.main(["--remote"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "hub_url not configured" in err or "cli.yml" in err


# ── Argparse plumbing ───────────────────────────────────────────────────────


def _parse(argv: list[str]):  # noqa: ANN202
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("lens", nargs="?", default="all")
    p.add_argument("--since", default="7d")
    p.add_argument("--json", action="store_true")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--remote", action="store_true")
    g.add_argument("--local", action="store_true")
    return p.parse_args(argv)
