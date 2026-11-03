"""BC-077 — non-root hub + scout containers.

Covers:
    - CAP-T160: Non-root hub + scout containers
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
HUB_DOCKERFILE = REPO_ROOT / "poc" / "hub" / "Dockerfile"
SCOUT_DOCKERFILE = REPO_ROOT / "poc" / "scout" / "Dockerfile"
COMPOSE_FILE = REPO_ROOT / "poc" / "docker-compose.yml"


def _dockerfile_lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


# ── USER directive present ──────────────────────────────────────────────────


def test_hub_dockerfile_has_user_directive() -> None:
    """CAP-T160: hub Dockerfile ends with USER beaconry before CMD."""
    assert any(
        re.match(r"^USER\s+beaconry\b", line) for line in _dockerfile_lines(HUB_DOCKERFILE)
    ), "hub Dockerfile missing `USER beaconry` directive"


def test_scout_dockerfile_has_user_directive() -> None:
    """CAP-T160: scout Dockerfile ends with USER beaconry before CMD."""
    assert any(
        re.match(r"^USER\s+beaconry\b", line) for line in _dockerfile_lines(SCOUT_DOCKERFILE)
    ), "scout Dockerfile missing `USER beaconry` directive"


# ── useradd creates the user ────────────────────────────────────────────────


def test_hub_dockerfile_creates_beaconry_user() -> None:
    """CAP-T160: uid 1000 explicit so container-side uid is predictable
    (operators can `chown -R 1000:100` without guessing)."""
    content = HUB_DOCKERFILE.read_text()
    assert "useradd" in content
    assert "beaconry" in content
    # Explicit uid 1000 for predictable chown
    assert re.search(r"useradd[^\n]*-u\s+1000", content), (
        "hub Dockerfile must create beaconry user with explicit uid 1000"
    )


def test_scout_dockerfile_creates_beaconry_user() -> None:
    """CAP-T160: same for scout."""
    content = SCOUT_DOCKERFILE.read_text()
    assert "useradd" in content
    assert "beaconry" in content
    assert re.search(r"useradd[^\n]*-u\s+1000", content)


# ── DOCKER_GID build arg ────────────────────────────────────────────────────


def test_dockerfiles_accept_docker_gid_arg() -> None:
    """CAP-T160: both Dockerfiles declare `ARG DOCKER_GID=...` with a default."""
    for df in (HUB_DOCKERFILE, SCOUT_DOCKERFILE):
        content = df.read_text()
        assert re.search(r"^ARG\s+DOCKER_GID=\d+", content, re.MULTILINE), (
            f"{df.name} missing `ARG DOCKER_GID=<default>`"
        )


def test_dockerfiles_groupadd_uses_docker_gid() -> None:
    """CAP-T160: groupadd must apply `${DOCKER_GID}` so operators can override."""
    for df in (HUB_DOCKERFILE, SCOUT_DOCKERFILE):
        content = df.read_text()
        assert "groupadd" in content
        assert "${DOCKER_GID}" in content, (
            f"{df.name} must pass ${{DOCKER_GID}} to groupadd for operator override"
        )


# ── Chown /app + /data ──────────────────────────────────────────────────────


def test_dockerfiles_chown_app_and_data() -> None:
    """CAP-T160: fresh named volumes inherit the image dir's ownership. If
    we don't chown /app and /data at build time, root-owned files leak into
    fresh deploys."""
    hub_content = HUB_DOCKERFILE.read_text()
    assert "chown" in hub_content and "beaconry:users" in hub_content
    assert "/data" in hub_content, "hub Dockerfile must chown /data"
    assert "/app" in hub_content, "hub Dockerfile must chown /app"

    scout_content = SCOUT_DOCKERFILE.read_text()
    assert "chown" in scout_content and "beaconry:users" in scout_content
    # Scout has no /data volume — only /app needs chown
    assert "/app" in scout_content


# ── USER before CMD ─────────────────────────────────────────────────────────


def test_user_directive_before_cmd() -> None:
    """CAP-T160: USER must come BEFORE the final CMD, otherwise the setup
    before CMD runs as root but CMD runs as the pre-USER user (none set) —
    i.e. still root."""
    for df in (HUB_DOCKERFILE, SCOUT_DOCKERFILE):
        lines = _dockerfile_lines(df)
        user_idx = next(
            (i for i, line in enumerate(lines) if re.match(r"^USER\s+beaconry\b", line)), -1
        )
        cmd_idx = next((i for i, line in enumerate(lines) if re.match(r"^CMD\s", line)), -1)
        assert user_idx >= 0, f"{df.name} missing USER"
        assert cmd_idx >= 0, f"{df.name} missing CMD"
        assert user_idx < cmd_idx, (
            f"{df.name}: USER must come before CMD, got USER@{user_idx} CMD@{cmd_idx}"
        )


# ── docker-compose propagates the arg ───────────────────────────────────────


def _compose_doc() -> dict:
    with open(COMPOSE_FILE) as f:
        return yaml.safe_load(f)


def test_compose_passes_docker_gid_build_arg_for_hub() -> None:
    """CAP-T160: hub service's build.args must declare DOCKER_GID."""
    doc = _compose_doc()
    hub_build = doc["services"]["hub"]["build"]
    args = hub_build.get("args", {})
    assert "DOCKER_GID" in args, "hub service missing DOCKER_GID build arg"


def test_compose_passes_docker_gid_build_arg_for_scout() -> None:
    """CAP-T160: scout service's build.args must declare DOCKER_GID."""
    doc = _compose_doc()
    scout_build = doc["services"]["scout"]["build"]
    args = scout_build.get("args", {})
    assert "DOCKER_GID" in args, "scout service missing DOCKER_GID build arg"


def test_compose_docker_gid_default_is_999() -> None:
    """CAP-T160: default 999 matches Debian/Ubuntu — the common case."""
    doc = _compose_doc()
    for svc in ("hub", "scout"):
        val = str(doc["services"][svc]["build"]["args"]["DOCKER_GID"])
        assert "999" in val, f"{svc} DOCKER_GID default should include 999 (Debian/Ubuntu)"


# ── CHANGELOG upgrade note ──────────────────────────────────────────────────


def test_changelog_documents_the_upgrade_step() -> None:
    """CAP-T160: operators upgrading from pre-BC-077 need the one-time chown
    step. Live-rebuild without the chown bricks the hub, so this is a hard
    requirement — must be documented at the top of the version section."""
    content = (REPO_ROOT / "CHANGELOG.md").read_text()
    assert "chown -R 1000:100" in content, (
        "CHANGELOG must document the one-time chown step for upgrading operators"
    )
    assert "DOCKER_GID" in content, "CHANGELOG must mention DOCKER_GID for non-Debian hosts"
