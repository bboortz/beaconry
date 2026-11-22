"""BC-082 — dogfood grype gate unit tests.

Covers:
    - CAP-T167: Dogfood grype gate — rescan beaconry's own images
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from poc.scripts import dogfood_scan

# ── parse_findings: severity + fixable filter ─────────────────────────────


def _match(sev: str, fix_state: str, versions: list[str] | None = None) -> dict[str, Any]:
    return {
        "artifact": {"name": "pkg", "version": "1.0.0", "type": "deb"},
        "vulnerability": {
            "id": "CVE-2026-0001",
            "severity": sev,
            "fix": {"state": fix_state, "versions": versions or []},
        },
    }


def test_parse_findings_keeps_fixable_high_and_critical() -> None:
    report = {
        "matches": [
            _match("High", "fixed", ["1.0.1"]),
            _match("Critical", "fixed", ["2.0.0"]),
        ]
    }
    findings = dogfood_scan.parse_findings(report)
    assert len(findings) == 2
    assert {f["severity"] for f in findings} == {"High", "Critical"}
    assert findings[0]["package"] == "pkg"
    assert findings[0]["cve_id"] == "CVE-2026-0001"
    assert findings[0]["fixed_in"] == "1.0.1"


def test_parse_findings_drops_medium_and_low() -> None:
    report = {
        "matches": [
            _match("Medium", "fixed", ["1.0.1"]),
            _match("Low", "fixed", ["1.0.1"]),
        ]
    }
    assert dogfood_scan.parse_findings(report) == []


def test_parse_findings_drops_unfixable() -> None:
    report = {
        "matches": [
            _match("High", "wont-fix", []),
            _match("Critical", "not-fixed", []),
            _match("High", "unknown"),
            # fixed state with no versions is still unfixable
            _match("Critical", "fixed", []),
        ]
    }
    assert dogfood_scan.parse_findings(report) == []


def test_parse_findings_handles_missing_matches_key() -> None:
    assert dogfood_scan.parse_findings({}) == []


# ── main(): short-circuits + error paths ──────────────────────────────────


def test_main_returns_zero_when_skip_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACONRY_SKIP_DOGFOOD", "1")
    assert dogfood_scan.main() == 0


def test_main_returns_two_on_scan_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_SKIP_DOGFOOD", raising=False)
    monkeypatch.setattr(dogfood_scan, "_resolve_docker_gid", lambda: "999")

    def boom(_image: str, _gid: str) -> dict[str, Any]:
        raise RuntimeError("grype refused to start")

    monkeypatch.setattr(dogfood_scan, "_scan_image", boom)
    assert dogfood_scan.main() == 2


def test_main_returns_two_on_json_decode_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_SKIP_DOGFOOD", raising=False)
    monkeypatch.setattr(dogfood_scan, "_resolve_docker_gid", lambda: "999")

    def bad_json(_image: str, _gid: str) -> dict[str, Any]:
        raise json.JSONDecodeError("bad", "", 0)

    monkeypatch.setattr(dogfood_scan, "_scan_image", bad_json)
    assert dogfood_scan.main() == 2


def test_main_returns_zero_on_clean_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_SKIP_DOGFOOD", raising=False)
    monkeypatch.setattr(dogfood_scan, "_resolve_docker_gid", lambda: "999")
    monkeypatch.setattr(dogfood_scan, "_scan_image", lambda *_: {"matches": []})
    assert dogfood_scan.main() == 0


def test_main_returns_one_on_actionable_findings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("BEACONRY_SKIP_DOGFOOD", raising=False)
    monkeypatch.setattr(dogfood_scan, "_resolve_docker_gid", lambda: "999")
    report = {"matches": [_match("Critical", "fixed", ["2.0.0"])]}
    monkeypatch.setattr(dogfood_scan, "_scan_image", lambda *_: report)
    rc = dogfood_scan.main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "actionable finding" in err
    assert "CVE-2026-0001" in err


# ── _resolve_docker_gid: env override + ladder ────────────────────────────


def test_resolve_docker_gid_uses_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCKER_GID", "12345")
    assert dogfood_scan._resolve_docker_gid() == "12345"


def test_resolve_docker_gid_uses_getent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCKER_GID", raising=False)

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "getent":
            return subprocess.CompletedProcess(cmd, 0, stdout="docker:x:962:benni\n", stderr="")
        raise AssertionError(f"unexpected cmd: {cmd}")

    monkeypatch.setattr(dogfood_scan.subprocess, "run", fake_run)
    assert dogfood_scan._resolve_docker_gid() == "962"


def test_resolve_docker_gid_falls_back_to_stat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCKER_GID", raising=False)

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "getent":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found")
        if cmd[0] == "stat":
            return subprocess.CompletedProcess(cmd, 0, stdout="991\n", stderr="")
        raise AssertionError(f"unexpected cmd: {cmd}")

    monkeypatch.setattr(dogfood_scan.subprocess, "run", fake_run)
    assert dogfood_scan._resolve_docker_gid() == "991"


def test_resolve_docker_gid_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCKER_GID", raising=False)

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        # Simulate getent + stat both unavailable.
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(dogfood_scan.subprocess, "run", fake_run)
    assert dogfood_scan._resolve_docker_gid() == dogfood_scan._DEFAULT_DOCKER_GID
