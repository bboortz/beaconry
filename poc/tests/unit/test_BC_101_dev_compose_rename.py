"""BC-101 — Dev-compose containers renamed (beaconry-dev-*).

Covers:
    - CAP-T203: poc/docker-compose.yml container_name entries are
      `beaconry-dev-{hub,scout}` so they coexist with the operator
      systemd stack (`beaconry-scout.service` wraps scout-only.yml
      which keeps the unqualified `beaconry-scout` name). CLI
      scripts default to the dev names and respect the
      BEACONRY_HUB_CONTAINER + BEACONRY_SCOUT_CONTAINER env overrides.
      Makefile targets use the new dev names.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEV_COMPOSE = REPO / "poc" / "docker-compose.yml"
SCOUT_ONLY_COMPOSE = REPO / "poc" / "docker-compose.scout-only.yml"
MAKEFILE = REPO / "Makefile"
VARIABLES_MD = REPO / "docs" / "VARIABLES.md"
ENV_EXAMPLE = REPO / "poc" / ".env.example"


# ── G1: dev compose uses beaconry-dev-* ──────────────────────────────────────


def test_dev_compose_uses_dev_prefix() -> None:
    body = DEV_COMPOSE.read_text()
    assert re.search(r"^\s*container_name:\s*beaconry-dev-hub\s*$", body, re.MULTILINE), (
        "poc/docker-compose.yml hub service must use container_name: beaconry-dev-hub"
    )
    assert re.search(r"^\s*container_name:\s*beaconry-dev-scout\s*$", body, re.MULTILINE), (
        "poc/docker-compose.yml scout service must use container_name: beaconry-dev-scout"
    )


def test_dev_compose_does_not_use_bare_names() -> None:
    """No `container_name: beaconry-hub` or `beaconry-scout` (bare) in dev compose."""
    body = DEV_COMPOSE.read_text()
    assert not re.search(r"^\s*container_name:\s*beaconry-hub\s*$", body, re.MULTILINE), (
        "dev compose must not use bare `beaconry-hub` — collides with operator systemd stack"
    )
    assert not re.search(r"^\s*container_name:\s*beaconry-scout\s*$", body, re.MULTILINE), (
        "dev compose must not use bare `beaconry-scout` — collides with operator systemd stack"
    )


# ── G2: scout-only.yml keeps the bare operator name ─────────────────────────


def test_scout_only_keeps_operator_name() -> None:
    """The operator/systemd-wrapped scout keeps `container_name: beaconry-scout`."""
    body = SCOUT_ONLY_COMPOSE.read_text()
    assert re.search(r"^\s*container_name:\s*beaconry-scout\s*$", body, re.MULTILINE), (
        "poc/docker-compose.scout-only.yml must keep container_name: beaconry-scout (operator stack)"
    )


# ── G3: CLI script defaults ─────────────────────────────────────────────────


def test_cli_scripts_default_to_dev_hub() -> None:
    """findings.py, audit.py, cli_smoke.py default HUB_CONTAINER to dev name."""
    for script in ("findings.py", "audit.py", "cli_smoke.py"):
        body = (REPO / "poc" / "scripts" / script).read_text()
        assert re.search(
            r'HUB_CONTAINER\s*=\s*os\.environ\.get\(\s*"BEACONRY_HUB_CONTAINER"\s*,\s*"beaconry-dev-hub"\s*\)',
            body,
        ), f"{script}: HUB_CONTAINER default must be `beaconry-dev-hub`"


def test_cli_scripts_default_to_dev_scout() -> None:
    """scan_cli.py + cli_smoke.py default SCOUT_CONTAINER to dev name (via env)."""
    for script in ("scan_cli.py", "cli_smoke.py"):
        body = (REPO / "poc" / "scripts" / script).read_text()
        assert re.search(
            r'SCOUT_CONTAINER\s*=\s*os\.environ\.get\(\s*"BEACONRY_SCOUT_CONTAINER"\s*,\s*"beaconry-dev-scout"\s*\)',
            body,
        ), f"{script}: SCOUT_CONTAINER must default to `beaconry-dev-scout` via env"


# ── G4: Docs updated ────────────────────────────────────────────────────────


def test_variables_md_has_scout_container() -> None:
    body = VARIABLES_MD.read_text()
    assert "`BEACONRY_SCOUT_CONTAINER`" in body, (
        "docs/VARIABLES.md must document BEACONRY_SCOUT_CONTAINER (BC-101)"
    )
    assert "beaconry-dev-scout" in body, (
        "docs/VARIABLES.md must mention the beaconry-dev-scout default"
    )


def test_env_example_has_scout_container() -> None:
    body = ENV_EXAMPLE.read_text()
    assert "BEACONRY_SCOUT_CONTAINER=beaconry-dev-scout" in body, (
        "poc/.env.example must include BEACONRY_SCOUT_CONTAINER=beaconry-dev-scout"
    )
    assert "BEACONRY_HUB_CONTAINER=beaconry-dev-hub" in body, (
        "poc/.env.example must set BEACONRY_HUB_CONTAINER default to beaconry-dev-hub"
    )


# ── Port coexistence: dev scout on 18001, operator keeps 8001 ─────────────


def test_dev_scout_host_port_is_18001() -> None:
    """Dev scout binds host 18001 so the operator stack's 0.0.0.0:8001 stays free."""
    body = DEV_COMPOSE.read_text()
    assert re.search(r'^\s*-\s*"127\.0\.0\.1:18001:8001"\s*$', body, re.MULTILINE), (
        "poc/docker-compose.yml scout ports must be `127.0.0.1:18001:8001` "
        "(BC-101: host port 8001 reserved for operator systemd scout)"
    )
    # No lingering 127.0.0.1:8001 binding for the dev scout
    assert not re.search(r'^\s*-\s*"127\.0\.0\.1:8001:8001"\s*$', body, re.MULTILINE), (
        "poc/docker-compose.yml must not bind 127.0.0.1:8001 — collides with operator stack"
    )


def test_scan_cli_uses_18001_default() -> None:
    body = (REPO / "poc" / "scripts" / "scan_cli.py").read_text()
    assert "http://127.0.0.1:18001/scan/trigger" in body, (
        "scan_cli.py default localhost URL must target port 18001"
    )
    assert "BEACONRY_SCOUT_LOCAL_URL" in body, (
        "scan_cli.py must read BEACONRY_SCOUT_LOCAL_URL env for localhost override"
    )


def test_status_scout_healthz_default_is_18001() -> None:
    body = (REPO / "poc" / "scripts" / "status.py").read_text()
    assert "http://localhost:18001/healthz" in body, (
        "status.py SCOUT_HEALTHZ_URL default must target 18001 (BC-101)"
    )


# ── G5: Makefile targets use the new names ──────────────────────────────────


def test_makefile_live_targets_use_dev_names() -> None:
    body = MAKEFILE.read_text()
    assert "docker exec beaconry-dev-hub" in body, (
        "Makefile live-rebuild must `docker exec beaconry-dev-hub`"
    )
    assert 'CONTAINER="beaconry-dev-$$svc"' in body, (
        "Makefile live-status must build container names as beaconry-dev-$svc"
    )
    assert "docker exec beaconry-hub " not in body, (
        "Makefile must not `docker exec beaconry-hub` (collides with operator stack)"
    )
