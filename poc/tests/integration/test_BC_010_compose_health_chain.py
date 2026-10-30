"""BC-010 compose integration — healthcheck + depends_on wiring.

Covers:
    - CAP-T037: Compose startup ordering uses depends_on: service_healthy
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

COMPOSE_FILE = Path(__file__).resolve().parents[3] / "poc" / "docker-compose.yml"


def _docker_compose_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return (
            subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                check=False,
                timeout=5,
            ).returncode
            == 0
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


pytestmark = [
    pytest.mark.compose,
    pytest.mark.skipif(not _docker_compose_available(), reason="docker compose not available"),
]


def _compose_config() -> dict:
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--format", "json"],
        capture_output=True,
        check=True,
        timeout=15,
        env={"PATH": "/usr/bin:/bin", "TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "0"},
    )
    return json.loads(result.stdout)


def test_hub_healthcheck_targets_readyz() -> None:
    """BC-010: hub healthcheck must probe /readyz (not /health). Compose has one slot."""
    config = _compose_config()
    hc = config["services"]["hub"]["healthcheck"]
    test = hc["test"]
    joined = " ".join(test) if isinstance(test, list) else str(test)
    assert "/readyz" in joined, f"hub healthcheck must target /readyz, got: {joined}"


def test_scout_depends_on_hub_service_healthy() -> None:
    """BC-010: scout must wait for hub /readyz-backed healthy signal."""
    config = _compose_config()
    scout = config["services"]["scout"]
    depends_on = scout.get("depends_on", {})
    hub_dep = depends_on.get("hub") if isinstance(depends_on, dict) else None
    assert hub_dep is not None, f"scout.depends_on.hub missing: {depends_on}"
    assert hub_dep.get("condition") == "service_healthy", (
        f"scout must depend on hub service_healthy, got: {hub_dep}"
    )


def test_scout_healthcheck_targets_readyz() -> None:
    """BC-013: scout healthcheck now probes /readyz HTTP (BC-010's sentinel-file test replaced)."""
    config = _compose_config()
    scout_hc = config["services"]["scout"].get("healthcheck")
    assert scout_hc, "scout missing healthcheck"
    test = scout_hc["test"]
    joined = " ".join(test) if isinstance(test, list) else str(test)
    assert "/readyz" in joined, f"scout healthcheck must target /readyz: {joined}"
    assert "8001" in joined, f"scout healthcheck must hit scout's HTTP port 8001: {joined}"


def test_scout_exposes_http_port() -> None:
    """BC-013: scout exposes 127.0.0.1:8001 so `make status` can query /healthz from host."""
    config = _compose_config()
    scout = config["services"]["scout"]
    ports = scout.get("ports", [])
    port_strs = [str(p) for p in ports] + [
        str(p.get("target", "")) for p in ports if isinstance(p, dict)
    ]
    joined = " ".join(port_strs)
    assert "8001" in joined, f"scout should expose port 8001: {ports}"


def test_scout_mounts_beaconry_data_volume() -> None:
    """BC-010: scout needs /data mount to write sentinel files to the shared volume."""
    config = _compose_config()
    scout = config["services"]["scout"]
    data_mounts = [
        v for v in scout.get("volumes", []) if "beaconry-data" in (v.get("source") or "")
    ]
    assert data_mounts, (
        f"scout missing beaconry-data volume mount — can't write sentinels. volumes: {scout.get('volumes', [])}"
    )
