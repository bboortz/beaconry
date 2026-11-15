"""BC-118 — beaconry status URL column + running-on host + fleet (you) marker.

Covers:
    - CAP-T220: status.py exposes probed URLs in the top table, prints
      Running on <hostname>, marks the fleet row that matches the local
      hostname, and explains dev-compose-not-configured false-downs
      distinctly from genuine downs.
"""

from __future__ import annotations

import socket

import pytest

from poc.scripts import status as sm

# ── ServiceStatus carries probed_url ────────────────────────────────────────


def test_service_status_has_probed_url_field() -> None:
    s = sm.ServiceStatus(
        name="x",
        state="running",
        health="healthy",
        last_scan="-",
        details="-",
        probed_url="http://e/x/healthz",
    )
    assert s.probed_url == "http://e/x/healthz"


# ── gather_hub_status + gather_scout_status populate probed_url ─────────────


def test_gather_hub_status_sets_probed_url_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sm,
        "fetch_healthz",
        lambda url, timeout=2.0, token="": {"status": "ok", "checks": {"last_scan_ts": 0}},
    )
    s = sm.gather_hub_status(url="http://probe/hub/healthz", token="")
    assert s.state == sm.STATE_RUNNING
    assert s.probed_url == "http://probe/hub/healthz"


def test_gather_hub_status_sets_probed_url_on_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*args, **kwargs):
        raise ConnectionRefusedError("hub")

    monkeypatch.setattr(sm, "fetch_healthz", _raise)
    s = sm.gather_hub_status(url="http://probe/hub/healthz", token="")
    assert s.state == sm.STATE_DOWN
    assert s.probed_url == "http://probe/hub/healthz"


def test_gather_scout_status_sets_probed_url_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sm,
        "fetch_healthz",
        lambda url, timeout=2.0, token="": {"status": "ok", "checks": {"last_scan_ts": 0}},
    )
    s = sm.gather_scout_status(url="http://localhost:18001/healthz")
    assert s.state == sm.STATE_RUNNING
    assert s.probed_url == "http://localhost:18001/healthz"


# ── Dev-compose detection branches on scout down ────────────────────────────


def test_scout_down_detail_when_dev_compose_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sm, "_dev_compose_configured", lambda: False)
    detail = sm._scout_down_detail("http://localhost:18001/healthz")
    assert detail == "down (dev-compose not configured on this host)"


def test_scout_down_detail_when_compose_stack_not_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sm, "_dev_compose_configured", lambda: True)
    monkeypatch.setattr(sm, "_dev_scout_container_exists", lambda: False)
    detail = sm._scout_down_detail("http://localhost:18001/healthz")
    assert "compose stack not up" in detail
    assert "docker compose up -d" in detail


def test_scout_down_detail_when_container_present_but_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sm, "_dev_compose_configured", lambda: True)
    monkeypatch.setattr(sm, "_dev_scout_container_exists", lambda: True)
    detail = sm._scout_down_detail("http://localhost:18001/healthz")
    assert "unreachable" in detail
    assert "http://localhost:18001/healthz" in detail


def test_gather_scout_status_on_down_uses_contextual_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args, **kwargs):
        raise ConnectionRefusedError("scout")

    monkeypatch.setattr(sm, "fetch_healthz", _raise)
    monkeypatch.setattr(sm, "_dev_compose_configured", lambda: False)
    s = sm.gather_scout_status(url="http://localhost:18001/healthz")
    assert s.state == sm.STATE_DOWN
    assert s.details == "down (dev-compose not configured on this host)"


# ── _dev_compose_configured / _dev_scout_container_exists ───────────────────


def test_dev_compose_configured_true_in_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live beaconry repo has poc/docker-compose.yml → returns True."""
    assert sm._dev_compose_configured() is True


def test_dev_compose_configured_false_when_repo_root_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sm, "_repo_root", lambda: None)
    assert sm._dev_compose_configured() is False


def test_dev_scout_container_exists_returns_false_on_docker_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args, **kwargs):
        raise OSError("docker: not found")

    monkeypatch.setattr(sm.subprocess, "run", _raise)
    assert sm._dev_scout_container_exists() is False


# ── _format_table renders URL column ───────────────────────────────────────


def test_format_table_includes_url_column_header() -> None:
    s = sm.ServiceStatus(
        name="beaconry-hub",
        state="running",
        health="healthy",
        last_scan="1m ago",
        details="ok",
        probed_url="http://example/healthz",
    )
    text = sm._format_table([s])
    header_line = text.splitlines()[0]
    assert "URL" in header_line
    assert header_line.index("URL") > header_line.index("NAME")
    assert header_line.index("URL") < header_line.index("STATE")


def test_format_table_renders_probed_url_cell() -> None:
    s = sm.ServiceStatus(
        name="beaconry-scout",
        state="down",
        health="-",
        last_scan="-",
        details="down (unreachable)",
        probed_url="http://localhost:18001/healthz",
    )
    text = sm._format_table([s])
    assert "http://localhost:18001/healthz" in text


def test_format_table_handles_empty_probed_url() -> None:
    """Rows with no probed_url render as blank in URL column, not crash."""
    s = sm.ServiceStatus(
        name="x", state="down", health="-", last_scan="-", details="-", probed_url=""
    )
    text = sm._format_table([s])
    assert "x" in text  # name still renders


# ── format_fleet (you) marker ──────────────────────────────────────────────


def test_format_fleet_marks_matching_host_with_you(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"host": "bb-82jq", "last_heartbeat_ts": 0, "open_findings": 1, "total_findings": 2},
        {"host": "server2", "last_heartbeat_ts": 0, "open_findings": 0, "total_findings": 0},
    ]
    text = sm.format_fleet(rows, local_hostname="bb-82jq")
    assert "bb-82jq (you)" in text
    # Other row NOT marked.
    other_line = [ln for ln in text.splitlines() if "server2" in ln][0]
    assert "(you)" not in other_line


def test_format_fleet_no_marker_when_hostname_missing_from_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {"host": "server2", "last_heartbeat_ts": 0, "open_findings": 0, "total_findings": 0},
    ]
    text = sm.format_fleet(rows, local_hostname="laptop-new")
    assert "(you)" not in text


def test_format_fleet_no_marker_when_local_hostname_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [{"host": "server2", "last_heartbeat_ts": 0}]
    text = sm.format_fleet(rows, local_hostname=None)
    assert "(you)" not in text


# ── main() prints Running on header ────────────────────────────────────────


def test_main_prints_running_on_header(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(
        sm,
        "fetch_healthz",
        lambda url, timeout=2.0, token="": {"status": "ok", "checks": {"last_scan_ts": 0}},
    )
    monkeypatch.setattr(sm, "fetch_fleet", lambda *a, **kw: [])
    monkeypatch.setattr(socket, "gethostname", lambda: "test-machine-42")

    rc = sm.main()
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "Running on: test-machine-42"
    assert rc == 0


def test_main_running_on_header_precedes_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(
        sm,
        "fetch_healthz",
        lambda url, timeout=2.0, token="": {"status": "ok", "checks": {"last_scan_ts": 0}},
    )
    monkeypatch.setattr(sm, "fetch_fleet", lambda *a, **kw: [])
    monkeypatch.setattr(socket, "gethostname", lambda: "bb-82jq")

    sm.main()
    out = capsys.readouterr().out
    running_on_idx = out.index("Running on:")
    header_idx = out.index("NAME")
    assert running_on_idx < header_idx
