"""BC-013 — `make status` table-format CLI.

Replaces BC-010's single-line output with a Docker-compose-ps-like table.
No raw Python exception class names; three exit codes (0 healthy, 1
degraded, 2 down). Both services queried uniformly via HTTP — no
docker exec special case.

Covers:
    - CAP-B015: Operator always knows if beaconry itself is working
    - CAP-B017: Operator sees beaconry stack status at a glance in a Docker-compose-ps-like table
    - CAP-T039: `make status` prints an operator-readable summary of all beaconry services
    - CAP-T044: `make status` queries both services uniformly via HTTP; no docker exec
    - CAP-T045: `make status` output follows a column format with human-readable error phrasing
"""

from __future__ import annotations

from typing import Any

import pytest

from poc.scripts import status as status_cli


def _hub_body(
    *, status: str = "ok", telegram: str = "polling", grype_db: str = "ok", last_scan_ts: int = 0
) -> dict:
    return {
        "status": status,
        "version": "0.1.0",
        "checks": {
            "db": "ok",
            "grype_db": grype_db,
            "grype_binary": "ok",
            "telegram": telegram,
            "last_scan_ts": last_scan_ts,
        },
    }


def _scout_body(
    *,
    status: str = "ok",
    last_scan_ts: int = 1,
    images_seen: int = 11,
    bytes_posted: int = 1024,
    docker_socket: str = "ok",
) -> dict:
    return {
        "status": status,
        "version": "0.1.0",
        "checks": {
            "docker_socket": docker_socket,
            "last_scan_ts": last_scan_ts,
            "images_seen": images_seen,
            "bytes_posted": bytes_posted,
        },
    }


def _mock_fetchers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    hub: dict[str, Any] | Exception | None = None,
    scout: dict[str, Any] | Exception | None = None,
) -> None:
    def _hub_fetch(_url: str) -> dict:
        if isinstance(hub, Exception):
            raise hub
        if hub is None:
            raise ConnectionError("hub down")
        return hub

    def _scout_fetch(_url: str) -> dict:
        if isinstance(scout, Exception):
            raise scout
        if scout is None:
            raise ConnectionError("scout down")
        return scout

    monkeypatch.setattr(status_cli, "fetch_healthz", _hub_fetch, raising=False)
    # Both services use fetch_healthz; parametrize via URL
    original = _hub_fetch

    def _dispatch(url: str) -> dict:
        if "8000" in url:
            return original(url)
        return _scout_fetch(url)

    monkeypatch.setattr(status_cli, "fetch_healthz", _dispatch, raising=False)


# ── table shape + no raw exception names ──────────────────────────────────────


def test_table_header_shape(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mock_fetchers(monkeypatch, hub=_hub_body(), scout=_scout_body())
    status_cli.main()
    out = capsys.readouterr().out
    for col in ("NAME", "STATE", "HEALTH", "LAST SCAN", "DETAILS"):
        assert col in out, f"missing column header {col} in output:\n{out}"


def test_two_rows_one_per_service(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mock_fetchers(monkeypatch, hub=_hub_body(), scout=_scout_body())
    status_cli.main()
    out = capsys.readouterr().out
    assert "beaconry-hub" in out
    assert "beaconry-scout" in out


def test_output_has_no_raw_exception_names(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Operators shouldn't need to read Python traceback vocabulary."""
    import urllib.error

    _mock_fetchers(
        monkeypatch,
        hub=urllib.error.URLError("Connection refused"),
        scout=ConnectionError("foo"),
    )
    status_cli.main()
    out = capsys.readouterr().out
    for banned in ("URLError", "ConnectionError", "Traceback", "<class", "Exception"):
        assert banned not in out, f"raw exception name leaked into user output: {banned}\n{out}"


# ── exit codes ────────────────────────────────────────────────────────────────


def test_exit_0_all_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_fetchers(monkeypatch, hub=_hub_body(), scout=_scout_body())
    assert status_cli.main() == 0


def test_exit_1_hub_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_fetchers(
        monkeypatch, hub=_hub_body(status="degraded", grype_db="missing"), scout=_scout_body()
    )
    assert status_cli.main() == 1


def test_exit_2_hub_down(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    _mock_fetchers(monkeypatch, hub=urllib.error.URLError("down"), scout=_scout_body())
    assert status_cli.main() == 2


def test_exit_2_both_down(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    _mock_fetchers(
        monkeypatch,
        hub=urllib.error.URLError("down"),
        scout=urllib.error.URLError("down"),
    )
    assert status_cli.main() == 2


# ── state surfacing in table ──────────────────────────────────────────────────


def test_down_hint_mentions_compose_up(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When both services are unreachable, DETAILS should hint operators to start the stack."""
    import urllib.error

    _mock_fetchers(
        monkeypatch,
        hub=urllib.error.URLError("down"),
        scout=urllib.error.URLError("down"),
    )
    status_cli.main()
    out = capsys.readouterr().out
    assert "docker compose up" in out, (
        f"down hint missing — operators shouldn't have to guess:\n{out}"
    )


def test_partial_up_state_hub_up_scout_down(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import urllib.error

    _mock_fetchers(monkeypatch, hub=_hub_body(), scout=urllib.error.URLError("down"))
    exit_code = status_cli.main()
    out = capsys.readouterr().out

    assert "beaconry-hub" in out
    assert "beaconry-scout" in out
    assert exit_code == 2


def test_degraded_details_names_failing_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mock_fetchers(
        monkeypatch, hub=_hub_body(status="degraded", grype_db="missing"), scout=_scout_body()
    )
    status_cli.main()
    out = capsys.readouterr().out
    assert "grype_db" in out, f"failing check must be named in DETAILS:\n{out}"


# ── no docker exec special case ───────────────────────────────────────────────


def test_no_docker_exec_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """BC-013 removes the docker exec fallback for scout — both services via HTTP."""
    import subprocess

    original_run = subprocess.run

    def _watcher(*args: Any, **kwargs: Any) -> Any:
        if (
            args
            and isinstance(args[0], list)
            and len(args[0]) > 1
            and args[0][0] == "docker"
            and args[0][1] == "exec"
        ):
            raise AssertionError(f"BC-013 should not use docker exec: {args[0]}")
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _watcher)
    _mock_fetchers(monkeypatch, hub=_hub_body(), scout=_scout_body())
    status_cli.main()
