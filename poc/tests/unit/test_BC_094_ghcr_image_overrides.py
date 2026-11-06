"""BC-094 — ghcr image override compose files.

Covers:
    - CAP-T194: docker-compose.{hub,scout}-ghcr.yml override files
    - CAP-T195: BEACONRY_IMAGE_TAG documented + exempt from orphan check
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
POC = REPO_ROOT / "poc"


# ── CAP-T194 structural ─────────────────────────────────────────────────────


def test_override_files_exist() -> None:
    """Both ghcr override files land in poc/ next to the base compose."""
    assert (POC / "docker-compose.hub-ghcr.yml").exists()
    assert (POC / "docker-compose.scout-ghcr.yml").exists()


@pytest.mark.parametrize(
    "fname,expected_service",
    [
        ("docker-compose.hub-ghcr.yml", "hub"),
        ("docker-compose.scout-ghcr.yml", "scout"),
    ],
)
def test_each_override_has_exactly_one_service(fname: str, expected_service: str) -> None:
    """Override MUST define exactly one service — a combined file would
    inject a dangling second service when the scout-only base is in play
    (and vice versa)."""
    doc = yaml.safe_load((POC / fname).read_text())
    services = doc.get("services", {})
    assert list(services.keys()) == [expected_service], (
        f"{fname}: expected exactly one service `{expected_service}`, got {list(services.keys())}"
    )


@pytest.mark.parametrize(
    "fname,service,expected_image",
    [
        (
            "docker-compose.hub-ghcr.yml",
            "hub",
            "ghcr.io/bboortz/beaconry-hub:${BEACONRY_IMAGE_TAG:-latest}",
        ),
        (
            "docker-compose.scout-ghcr.yml",
            "scout",
            "ghcr.io/bboortz/beaconry-scout:${BEACONRY_IMAGE_TAG:-latest}",
        ),
    ],
)
def test_image_ref_matches_expected(fname: str, service: str, expected_image: str) -> None:
    """Exact ghcr ref with the BEACONRY_IMAGE_TAG envsubst default to `latest`."""
    doc = yaml.safe_load((POC / fname).read_text())
    assert doc["services"][service]["image"] == expected_image


@pytest.mark.parametrize(
    "fname,service",
    [
        ("docker-compose.hub-ghcr.yml", "hub"),
        ("docker-compose.scout-ghcr.yml", "scout"),
    ],
)
def test_no_build_key_in_overrides(fname: str, service: str) -> None:
    """Override MUST NOT carry a `build:` key. The base compose keeps it
    for local dev; override only replaces the image ref."""
    doc = yaml.safe_load((POC / fname).read_text())
    assert "build" not in doc["services"][service], (
        f"{fname}: override must not define `build:` — base compose owns it"
    )


def test_main_compose_unchanged_for_local_dev() -> None:
    """Sanity: the main `docker-compose.yml` still uses the unqualified
    `beaconry-{hub,scout}:latest` tag so local dev + 19+ downstream
    scripts (cleanup.py, dogfood_scan.py, demo.sh, …) keep working."""
    doc = yaml.safe_load((POC / "docker-compose.yml").read_text())
    assert doc["services"]["hub"]["image"] == "beaconry-hub:latest"
    assert doc["services"]["scout"]["image"] == "beaconry-scout:latest"
    # And `build:` is still there so local dev can `docker compose build`.
    assert "build" in doc["services"]["hub"]
    assert "build" in doc["services"]["scout"]


def test_scout_only_compose_unchanged_for_local_dev() -> None:
    """Same sanity for the scout-only compose used by multi-host deploys."""
    doc = yaml.safe_load((POC / "docker-compose.scout-only.yml").read_text())
    assert doc["services"]["scout"]["image"] == "beaconry-scout:latest"
    assert "build" in doc["services"]["scout"]


# ── CAP-T195 — lint exemption + doc coverage ──────────────────────────────────


def test_deploy_only_allowlist_covers_image_tag() -> None:
    """The orphan-check allowlist includes BEACONRY_IMAGE_TAG so the lint
    doesn't flag the (Python-unread, compose-substituted) var as orphaned."""
    from poc.scripts import verify_variables_documented as vv

    assert "BEACONRY_IMAGE_TAG" in vv.DEPLOY_ONLY_ALLOWLIST


def test_image_tag_documented_in_variables_md() -> None:
    """VARIABLES.md has a row for BEACONRY_IMAGE_TAG."""
    body = (REPO_ROOT / "docs" / "VARIABLES.md").read_text()
    assert "`BEACONRY_IMAGE_TAG`" in body
    assert "ghcr.io/bboortz/beaconry" in body  # the row names the registry
    assert "BC-094" in body


def test_image_tag_in_env_example() -> None:
    """`.env.example` carries the var with default `latest`."""
    env = (POC / ".env.example").read_text()
    assert "BEACONRY_IMAGE_TAG=latest" in env
