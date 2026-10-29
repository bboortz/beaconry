"""BC-005 Compose integration — config validation (full up/down is manual).

Covers:
    - CAP-T019: Compose stack brings up hub + scout with correct :ro socket mount, healthchecks, env propagation
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
    """Return the resolved compose config as a dict."""
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--format", "json"],
        capture_output=True,
        check=True,
        timeout=15,
        env={"PATH": "/usr/bin:/bin", "TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "0"},
    )
    return json.loads(result.stdout)


def test_compose_file_is_valid() -> None:
    """docker compose config exits 0 — compose yaml is well-formed and resolvable."""
    config = _compose_config()
    assert "services" in config


def test_hub_and_scout_services_defined() -> None:
    config = _compose_config()
    assert set(config["services"].keys()) >= {"hub", "scout"}


def test_scout_docker_socket_mount_is_readonly() -> None:
    """Security: scout must mount the Docker socket :ro per BC-005 design decision."""
    config = _compose_config()
    scout = config["services"]["scout"]
    socket_mounts = [
        v for v in scout.get("volumes", []) if "/var/run/docker.sock" in (v.get("source") or "")
    ]
    assert socket_mounts, "scout missing docker socket mount"
    for mount in socket_mounts:
        assert mount.get("read_only") is True, f"scout docker socket must be :ro, got {mount}"


def test_hub_docker_socket_mount_is_read_write() -> None:
    """Hub needs rw for docker pull / tag (DockerAdapter)."""
    config = _compose_config()
    hub = config["services"]["hub"]
    socket_mounts = [
        v for v in hub.get("volumes", []) if "/var/run/docker.sock" in (v.get("source") or "")
    ]
    assert socket_mounts, "hub missing docker socket mount"
    for mount in socket_mounts:
        assert mount.get("read_only") is not True, f"hub docker socket must be rw, got {mount}"


def test_scout_env_propagates_bc005_vars() -> None:
    """SCOUT_INCLUDE / SCOUT_EXCLUDE / SCAN_INTERVAL / HOST_NAME / HUB_URL present."""
    config = _compose_config()
    scout_env = config["services"]["scout"].get("environment", {})
    env_keys = (
        scout_env.keys() if isinstance(scout_env, dict) else {e.split("=")[0] for e in scout_env}
    )
    for required in ("HUB_URL", "HOST_NAME", "SCAN_INTERVAL", "SCOUT_INCLUDE", "SCOUT_EXCLUDE"):
        assert required in env_keys, f"scout missing env {required}"


def test_hub_env_propagates_bc005_vars() -> None:
    """MIN_CVSS / BEACONRY_MAX_FINDINGS_PER_RUN / TELEGRAM_* / GRYPE_DB_CACHE_DIR present."""
    config = _compose_config()
    hub_env = config["services"]["hub"].get("environment", {})
    env_keys = hub_env.keys() if isinstance(hub_env, dict) else {e.split("=")[0] for e in hub_env}
    for required in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "GRYPE_DB_CACHE_DIR",
        "MIN_CVSS",
        "BEACONRY_MAX_FINDINGS_PER_RUN",
    ):
        assert required in env_keys, f"hub missing env {required}"


def test_hub_has_healthcheck() -> None:
    config = _compose_config()
    hc = config["services"]["hub"].get("healthcheck")
    assert hc, "hub missing healthcheck"
    assert hc.get("test"), "hub healthcheck has no test"


def test_beaconry_data_volume_declared() -> None:
    """Grype DB cache lives in a named volume so it survives docker compose down."""
    config = _compose_config()
    assert "beaconry-data" in config.get("volumes", {})
