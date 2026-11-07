"""BC-096 — dogfood_sbom.py (grype against baked-in /sbom.json).

Covers:
    - CAP-T197: dogfood-sbom runs `grype sbom:/sbom.json` inside each
      beaconry image, filters High/Critical fixable findings with the
      same shape as dogfood_scan.py, and honours BEACONRY_SKIP_DOGFOOD.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from poc.scripts import dogfood_sbom

# ── parse_findings: same shape as dogfood_scan ────────────────────────────────


def _match(severity: str, fix_state: str, versions: list[str]) -> dict[str, Any]:
    return {
        "vulnerability": {
            "id": "CVE-2026-0001",
            "severity": severity,
            "fix": {"state": fix_state, "versions": versions},
        },
        "artifact": {"name": "fastapi", "version": "0.115.0", "type": "python"},
    }


def test_parse_findings_filters_high_fixable() -> None:
    report = {
        "matches": [
            _match("High", "fixed", ["0.115.1"]),  # kept
            _match("Critical", "fixed", ["0.115.2"]),  # kept
            _match("Medium", "fixed", ["0.115.3"]),  # dropped — below floor
            _match("High", "not-fixed", []),  # dropped — no fix
            _match("High", "wont-fix", []),  # dropped — no fix
            _match("High", "fixed", []),  # dropped — empty versions list
        ]
    }
    findings = dogfood_sbom.parse_findings(report)
    assert len(findings) == 2
    severities = sorted(f["severity"] for f in findings)
    assert severities == ["Critical", "High"]


def test_parse_findings_empty_report() -> None:
    assert dogfood_sbom.parse_findings({"matches": []}) == []
    assert dogfood_sbom.parse_findings({}) == []


# ── _scan_image: argv uses sbom:/sbom.json and NO docker-socket mount ────────


def test_scan_image_cmd_uses_sbom_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Argv for the grype step must point at the mounted sbom.json and no docker-socket mount."""
    seen_cmds: list[list[str]] = []

    class FakeProc:
        def __init__(self, stdout: str = "", returncode: int = 0) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    def fake_run(cmd: list[str], **_: Any) -> FakeProc:
        seen_cmds.append(cmd)
        if cmd[:2] == ["docker", "create"]:
            return FakeProc(stdout="fake-container-id\n")
        if cmd[:2] == ["docker", "cp"]:
            # Simulate the cp by touching the dest path so downstream logic works.
            # cmd shape: `docker cp <cid>:/sbom.json <host-dest>`. Simulate
            # the copy by writing an empty JSON object to the host-side dest.
            Path(cmd[3]).write_text("{}")
            return FakeProc()
        if cmd[:2] == ["docker", "rm"]:
            return FakeProc()
        # grype step
        return FakeProc(stdout='{"matches": []}')

    monkeypatch.setattr(dogfood_sbom.subprocess, "run", fake_run)
    result = dogfood_sbom._scan_image("beaconry-scout:latest")

    # Find the grype invocation among the captured commands
    grype_cmds = [c for c in seen_cmds if "/usr/local/bin/grype" in c]
    assert len(grype_cmds) == 1
    cmd = grype_cmds[0]

    assert cmd[0] == "docker"
    assert cmd[1] == "run"
    # Grype inside the hub image (scout has no grype binary)
    assert dogfood_sbom.GRYPE_IMAGE in cmd
    # Points grype at the mounted sbom.json
    assert "sbom:/sbom.json" in cmd
    # Read-only bind mount into the grype container
    joined = " ".join(cmd)
    assert ":/sbom.json:ro" in joined
    # Not a docker-socket scan
    assert "/var/run/docker.sock" not in joined
    # Output JSON for parse_findings
    assert "-o" in cmd
    assert "json" in cmd
    assert result == {"matches": []}


def test_scan_image_raises_on_grype_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProc:
        def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    def fake_run(cmd: list[str], **_: Any) -> FakeProc:
        if cmd[:2] == ["docker", "create"]:
            return FakeProc(stdout="fake-container-id\n")
        if cmd[:2] == ["docker", "cp"]:
            # cmd shape: `docker cp <cid>:/sbom.json <host-dest>`. Simulate
            # the copy by writing an empty JSON object to the host-side dest.
            Path(cmd[3]).write_text("{}")
            return FakeProc()
        if cmd[:2] == ["docker", "rm"]:
            return FakeProc()
        return FakeProc(stderr="grype: SBOM parse error", returncode=1)

    monkeypatch.setattr(dogfood_sbom.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="SBOM-scan of `beaconry-hub:latest` failed"):
        dogfood_sbom._scan_image("beaconry-hub:latest")


# ── main: skip env + exit codes ──────────────────────────────────────────────


def test_main_respects_skip_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BEACONRY_SKIP_DOGFOOD", "1")
    rc = dogfood_sbom.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "skipped per BEACONRY_SKIP_DOGFOOD" in out


def test_main_clean_pass_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_SKIP_DOGFOOD", raising=False)
    monkeypatch.setattr(dogfood_sbom, "_scan_image", lambda _img: {"matches": []})
    rc = dogfood_sbom.main([])
    assert rc == 0


def test_main_findings_returns_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEACONRY_SKIP_DOGFOOD", raising=False)
    monkeypatch.setattr(
        dogfood_sbom, "_scan_image", lambda _img: {"matches": [_match("High", "fixed", ["x"])]}
    )
    rc = dogfood_sbom.main([])
    assert rc == 1
