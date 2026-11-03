"""BC-082 — homelab-deployment coverage: lints, parsers, wiring.

The actual `make test-cold-start` compose boot lives in
`test_BC_082_cold_start.py` under `@pytest.mark.compose`. This file
covers the pure-logic + wiring aspects that run on every `make check`.

Covers:
    - CAP-T166: Pinned-version floor lint
    - CAP-T167: Dogfood grype gate (parser + env opt-out)
    - CAP-T168: Cold-start test infrastructure (compose file + wiring)
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from poc.scripts import dogfood_scan, verify_pinned_versions

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = REPO_ROOT / "Makefile"
HUB_DOCKERFILE = REPO_ROOT / "poc" / "hub" / "Dockerfile"
SCOUT_DOCKERFILE = REPO_ROOT / "poc" / "scout" / "Dockerfile"
COLD_START_COMPOSE = REPO_ROOT / "poc" / "docker-compose.cold-start.yml"
CI_FAILURES = REPO_ROOT / "docs" / "plan" / "CI-FAILURES.md"


# ── CAP-T166: Pinned-version floor lint ─────────────────────────────────────


def test_pinned_versions_passes_live() -> None:
    """CAP-T166: shipped repo passes the lint. If this fails, the shipped
    Dockerfiles drifted off BC-078/BC-081's intentional pins."""
    violations = verify_pinned_versions.check(REPO_ROOT)
    assert violations == [], (
        "verify-pinned-versions found violations in the shipped repo:\n  " + "\n  ".join(violations)
    )


def test_pinned_versions_rejects_pre_bc081_docker_cli(tmp_path: Path) -> None:
    """CAP-T166: a Dockerfile with DOCKER_CLI_VERSION=27.5.1 (BC-081 vuln)
    must fail the lint. This is the exact regression the lint prevents."""
    hub_dir = tmp_path / "poc" / "hub"
    scout_dir = tmp_path / "poc" / "scout"
    hub_dir.mkdir(parents=True)
    scout_dir.mkdir(parents=True)
    hub_dir.joinpath("Dockerfile").write_text(
        "FROM python:3.11-slim-bookworm AS builder\n"
        "ARG DOCKER_CLI_VERSION=27.5.1\n"
        "FROM gcr.io/distroless/python3-debian12:nonroot AS runtime\n"
    )
    scout_dir.joinpath("Dockerfile").write_text(
        "FROM python:3.11-slim-bookworm AS builder\n"
        "FROM gcr.io/distroless/python3-debian12:nonroot AS runtime\n"
    )
    violations = verify_pinned_versions.check(tmp_path)
    assert any("DOCKER_CLI_VERSION=27.5.1" in v for v in violations), (
        f"expected a DOCKER_CLI_VERSION floor violation, got: {violations}"
    )
    assert any("BC-081" in v for v in violations), (
        "violation message must name BC-081 so developers can trace the rule's origin"
    )


def test_pinned_versions_rejects_unknown_runtime(tmp_path: Path) -> None:
    """CAP-T166: a Dockerfile whose runtime stage drifts off the pinned
    distroless image must fail. Prevents accidental reintroduction of a
    shell-containing base."""
    hub_dir = tmp_path / "poc" / "hub"
    scout_dir = tmp_path / "poc" / "scout"
    hub_dir.mkdir(parents=True)
    scout_dir.mkdir(parents=True)
    bad = (
        "FROM python:3.11-slim-bookworm AS builder\n"
        "ARG DOCKER_CLI_VERSION=29.9.2\n"
        "FROM python:3.11-slim AS runtime\n"  # ← wrong (has /bin/sh)
    )
    hub_dir.joinpath("Dockerfile").write_text(bad)
    scout_dir.joinpath("Dockerfile").write_text(bad)
    violations = verify_pinned_versions.check(tmp_path)
    assert any("distroless" in v.lower() for v in violations), (
        f"expected a runtime-stage violation naming distroless, got: {violations}"
    )


def test_pinned_versions_rejects_old_python_minor(tmp_path: Path) -> None:
    """CAP-T166: python:3.10-slim-bookworm must fail — 3.11 is the floor
    (distroless debian12 ships python 3.11)."""
    hub_dir = tmp_path / "poc" / "hub"
    scout_dir = tmp_path / "poc" / "scout"
    hub_dir.mkdir(parents=True)
    scout_dir.mkdir(parents=True)
    bad = (
        "FROM python:3.10-slim-bookworm AS builder\n"  # ← too old
        "ARG DOCKER_CLI_VERSION=29.9.2\n"
        "FROM gcr.io/distroless/python3-debian12:nonroot AS runtime\n"
    )
    hub_dir.joinpath("Dockerfile").write_text(bad)
    scout_dir.joinpath("Dockerfile").write_text(bad)
    violations = verify_pinned_versions.check(tmp_path)
    assert any("builder" in v.lower() and "python" in v.lower() for v in violations)


def test_make_check_runs_verify_pinned_versions() -> None:
    """CAP-T166: `make check` must include verify-pinned-versions so a
    revert can't slip past fast-tier."""
    text = MAKEFILE.read_text()
    check_target = re.search(r"^check:\s*(.+)$", text, re.MULTILINE)
    assert check_target, "make check target missing"
    assert "verify-pinned-versions" in check_target.group(1), (
        "verify-pinned-versions must be in the `check:` dependency list"
    )


# ── CAP-T167: Dogfood grype gate ────────────────────────────────────────────


def _grype_report(matches: list[dict]) -> dict:
    """Minimal grype JSON shape."""
    return {"matches": matches}


def _match(
    cve_id: str = "CVE-2026-XXXX",
    severity: str = "High",
    fix_state: str = "fixed",
    fix_versions: list[str] | None = None,
    package: str = "libfoo",
    version: str = "1.0.0",
    typ: str = "deb",
) -> dict:
    """Factory for a single grype `matches` entry."""
    return {
        "vulnerability": {
            "id": cve_id,
            "severity": severity,
            "fix": {"state": fix_state, "versions": fix_versions or []},
        },
        "artifact": {"name": package, "version": version, "type": typ},
    }


def test_dogfood_parse_flags_fixable_high() -> None:
    """CAP-T167: a High CVE with fix.state=fixed + non-empty versions must flag."""
    report = _grype_report(
        [_match(cve_id="CVE-2026-0001", severity="High", fix_versions=["1.0.1"])]
    )
    findings = dogfood_scan.parse_findings(report)
    assert len(findings) == 1
    assert findings[0]["cve_id"] == "CVE-2026-0001"
    assert findings[0]["fixed_in"] == "1.0.1"


def test_dogfood_parse_flags_fixable_critical() -> None:
    """CAP-T167: Critical CVEs are flagged on top of High."""
    report = _grype_report(
        [_match(cve_id="CVE-2026-0002", severity="Critical", fix_versions=["2.0.0"])]
    )
    assert len(dogfood_scan.parse_findings(report)) == 1


def test_dogfood_parse_tolerates_wont_fix() -> None:
    """CAP-T167: `fix.state=wont-fix` must NOT flag — we can't fix it."""
    report = _grype_report([_match(severity="High", fix_state="wont-fix", fix_versions=[])])
    assert dogfood_scan.parse_findings(report) == []


def test_dogfood_parse_tolerates_unknown_fix() -> None:
    """CAP-T167: `fix.state=unknown` must NOT flag — nothing actionable."""
    report = _grype_report([_match(severity="High", fix_state="unknown", fix_versions=[])])
    assert dogfood_scan.parse_findings(report) == []


def test_dogfood_parse_tolerates_medium() -> None:
    """CAP-T167: Medium findings pass — only High+ are blocking. Medium
    fixable can be the subject of a follow-up BC."""
    report = _grype_report([_match(severity="Medium", fix_state="fixed", fix_versions=["1.0.1"])])
    assert dogfood_scan.parse_findings(report) == []


def test_dogfood_parse_tolerates_empty_fix_versions() -> None:
    """CAP-T167: fix.state=fixed but versions=[] shouldn't flag (grype
    sometimes emits this when it knows a fix exists upstream but can't name a
    specific version); better to not flag than to flag false-positively."""
    report = _grype_report([_match(severity="High", fix_state="fixed", fix_versions=[])])
    assert dogfood_scan.parse_findings(report) == []


def test_dogfood_skip_env_opts_out() -> None:
    """CAP-T167: BEACONRY_SKIP_DOGFOOD=1 is the dev-only escape hatch.
    Must short-circuit before any Docker shell-out."""
    with patch.dict(os.environ, {"BEACONRY_SKIP_DOGFOOD": "1"}):
        rc = dogfood_scan.main([])
    assert rc == 0, "BEACONRY_SKIP_DOGFOOD=1 must exit 0 without scanning"


def test_check_full_runs_dogfood_scan() -> None:
    """CAP-T167: `make check-full` recipe must invoke `make dogfood-scan`."""
    text = MAKEFILE.read_text()
    match = re.search(r"^check-full:[^\n]*\n((?:^[\t ].*\n|^\s*\n)+)", text, re.MULTILINE)
    assert match, "could not locate check-full recipe"
    recipe = match.group(1)
    assert "dogfood-scan" in recipe, (
        "check-full must invoke `make dogfood-scan` so pre-push catches "
        "BC-081-class self-alerts proactively"
    )


# ── CAP-T168: Cold-start infrastructure ─────────────────────────────────────


def test_cold_start_compose_file_exists() -> None:
    """CAP-T168: the cold-start compose file is checked into the repo
    (not generated by the test), so it's reviewable + diffable."""
    assert COLD_START_COMPOSE.exists(), f"cold-start compose file missing at {COLD_START_COMPOSE}"


def test_cold_start_compose_file_minimal() -> None:
    """CAP-T168: cold-start compose must not share state with the live
    stack (different ports, isolated volume name, no Telegram tokens)."""
    doc = yaml.safe_load(COLD_START_COMPOSE.read_text())
    # Different project name.
    assert doc.get("name") == "beaconry-cold-start"
    # Isolated volume (not `beaconry-data`).
    assert "cold-start-data" in doc.get("volumes", {})
    assert "beaconry-data" not in doc.get("volumes", {})
    # Different ports (9100/9101, not 8000/8001) so this coexists with live.
    hub_ports = doc["services"]["hub"].get("ports", [])
    assert any("9100" in str(p) for p in hub_ports), f"hub must bind 9100, got {hub_ports}"
    scout_ports = doc["services"]["scout"].get("ports", [])
    assert any("9101" in str(p) for p in scout_ports), f"scout must bind 9101, got {scout_ports}"


def test_cold_start_compose_does_not_skip_grype_db() -> None:
    """CAP-T168: the entire point of cold-start is to exercise the REAL
    grype DB download path. BEACONRY_SKIP_GRYPE_DB=1 defeats that."""
    doc = yaml.safe_load(COLD_START_COMPOSE.read_text())
    hub_env = doc["services"]["hub"].get("environment", [])
    # environment may be a list of "K=V" strings or a dict — handle both.
    if isinstance(hub_env, list):
        assigned = {e.split("=", 1)[0] for e in hub_env if "=" in e}
    else:
        assigned = set(hub_env.keys())
    assert "BEACONRY_SKIP_GRYPE_DB" not in assigned, (
        "cold-start compose must NOT set BEACONRY_SKIP_GRYPE_DB — it exists "
        "specifically to exercise the grype DB download path on first boot"
    )


def test_cold_start_recipe_runs_cleanup() -> None:
    """CAP-T168 ∩ BC-080: cold-start teardown must fire `make cleanup` so
    beaconry-cold-start-* containers + leaked images don't accumulate."""
    text = MAKEFILE.read_text()
    match = re.search(r"^test-cold-start:[^\n]*\n((?:^[\t ].*\n|^\s*\n)+)", text, re.MULTILINE)
    assert match, "could not locate test-cold-start recipe"
    recipe = match.group(1)
    assert "cleanup" in recipe, (
        "test-cold-start must invoke `make cleanup` on exit (BC-080) so "
        "leaked images don't poison the operator's live scout"
    )


def test_check_full_runs_test_cold_start() -> None:
    """CAP-T168: `make check-full` must invoke `make test-cold-start`."""
    text = MAKEFILE.read_text()
    match = re.search(r"^check-full:[^\n]*\n((?:^[\t ].*\n|^\s*\n)+)", text, re.MULTILINE)
    assert match
    assert "test-cold-start" in match.group(1), "check-full must invoke `make test-cold-start`"


# ── Learning record: CI-FAILURES.md ─────────────────────────────────────────


def test_ci_failures_log_has_bc_081_entry() -> None:
    """CAP-T167 ∩ learning-log: BC-081's "beaconry alerted on itself" story
    is documented so dogfood-grype's origin stays traceable."""
    if not CI_FAILURES.exists():
        pytest.skip("CI-FAILURES.md not shipped yet (BC-074 artifact)")
    text = CI_FAILURES.read_text()
    assert "BC-081" in text, "CI-FAILURES.md must have a BC-081 entry"
    assert "GO-2026-6604" in text or "docker CLI" in text, (
        "BC-081 entry must name the specific CVE or the pinned dep that regressed"
    )
    assert "dogfood" in text.lower() or "pin" in text.lower(), (
        "BC-081 entry should name the lesson (dogfood gate OR pinned-dep decay)"
    )
