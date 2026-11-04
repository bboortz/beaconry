"""BC-078 — distroless runtime for hub + scout.

Covers:
    - CAP-T160: Non-root hub + scout containers (BC-078 supersedes BC-077's build-arg path; non-root uid 65532 is baked into the distroless base and asserted here)
    - CAP-T161: Distroless runtime image for hub + scout
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
HUB_DOCKERFILE = REPO_ROOT / "poc" / "hub" / "Dockerfile"
SCOUT_DOCKERFILE = REPO_ROOT / "poc" / "scout" / "Dockerfile"
COMPOSE_FILE = REPO_ROOT / "poc" / "docker-compose.yml"
PYPROJECT = REPO_ROOT / "pyproject.toml"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"


# ── Multi-stage with distroless runtime ─────────────────────────────────────


def test_dockerfiles_are_multi_stage() -> None:
    """CAP-T161: distroless requires builder/runtime split."""
    for df in (HUB_DOCKERFILE, SCOUT_DOCKERFILE):
        content = df.read_text()
        from_lines = re.findall(r"^FROM\s+\S+", content, re.MULTILINE)
        assert len(from_lines) >= 2, (
            f"{df.name} must be multi-stage (builder + runtime); got {len(from_lines)} FROM lines"
        )


def test_hub_dockerfile_uses_distroless_runtime() -> None:
    """CAP-T161: hub runtime stage is gcr.io/distroless/python3-debian12:nonroot."""
    content = HUB_DOCKERFILE.read_text()
    # BC-082 bumped debian12 → debian13; BC-083 migrated to the self-owned
    # forge image. Accept any of the three during the soft-migration window
    # (verify_pinned_versions has the authoritative acceptance regex).
    accepted = r"(gcr\.io/distroless/python3-debian1[23]:nonroot|ghcr\.io/bboortz/python-scratch-trixie:3\.13)"
    assert re.search(accepted, content), (
        "hub Dockerfile runtime stage must use an accepted base image "
        "(forge primary; distroless fallback during migration)"
    )


def test_scout_dockerfile_uses_distroless_runtime() -> None:
    """CAP-T161 (BC-083 broadened): accepted runtime is forge OR distroless."""
    content = SCOUT_DOCKERFILE.read_text()
    accepted = r"(gcr\.io/distroless/python3-debian1[23]:nonroot|ghcr\.io/bboortz/python-scratch-trixie:3\.13)"
    assert re.search(accepted, content), (
        "scout Dockerfile runtime stage must use an accepted base image "
        "(forge primary; distroless fallback during migration)"
    )


# ── Binaries copied into the runtime stage ──────────────────────────────────


def test_hub_runtime_copies_binaries() -> None:
    """CAP-T161: hub runs grype against SBOMs + shells out to docker via
    DockerAdapter; distroless has neither by default. Both must be copied
    in via COPY --from=builder."""
    content = HUB_DOCKERFILE.read_text()
    # grype — CVE matcher
    assert re.search(r"COPY\s+--from=builder\s+\S*grype\s", content), (
        "hub Dockerfile must COPY grype from builder stage"
    )
    # docker CLI — DockerAdapter.execute shells out to `docker pull`
    assert re.search(r"COPY\s+--from=builder\s+\S*docker\s", content), (
        "hub Dockerfile must COPY docker binary from builder stage"
    )


def test_scout_runtime_copies_syft_binary() -> None:
    """CAP-T161: scout generates SBOMs via syft; distroless has no syft."""
    content = SCOUT_DOCKERFILE.read_text()
    assert re.search(r"COPY\s+--from=builder\s+\S*syft\s", content), (
        "scout Dockerfile must COPY syft from builder stage"
    )


# ── No shell in the runtime stage ───────────────────────────────────────────


def test_dockerfiles_do_not_install_shell_in_runtime() -> None:
    """CAP-T161: distroless has no shell by design. Guard against someone
    reintroducing `apt install bash` or `COPY --from=builder /bin/sh` in
    the runtime stage."""
    for df in (HUB_DOCKERFILE, SCOUT_DOCKERFILE):
        content = df.read_text()
        # Find the runtime stage boundary — everything after the second FROM.
        parts = re.split(r"^FROM\s+", content, flags=re.MULTILINE)
        assert len(parts) >= 3, f"{df.name} must have ≥2 FROM lines"
        runtime = parts[-1]
        # Red lines: don't copy /bin/sh or /bin/bash from builder.
        assert "COPY --from=builder /bin/sh" not in runtime, (
            f"{df.name}: runtime must not re-import /bin/sh"
        )
        assert "COPY --from=builder /bin/bash" not in runtime, (
            f"{df.name}: runtime must not re-import /bin/bash"
        )


# ── Compose group_add replaces the build-arg ────────────────────────────────


def _compose_doc() -> dict:
    with open(COMPOSE_FILE) as f:
        return yaml.safe_load(f)


def test_compose_uses_group_add() -> None:
    """CAP-T161: distroless has no groupadd — supplementary gid comes
    from compose runtime instead of Dockerfile build-arg."""
    doc = _compose_doc()
    for svc in ("hub", "scout"):
        group_add = doc["services"][svc].get("group_add", [])
        assert group_add, f"{svc}: compose must declare group_add for Docker-socket access"
        # Must reference DOCKER_GID env var so operators can override.
        joined = " ".join(str(g) for g in group_add)
        assert "DOCKER_GID" in joined, (
            f"{svc}: group_add must reference ${{DOCKER_GID}} for operator override"
        )


def test_compose_removed_docker_gid_build_arg() -> None:
    """CAP-T161: BC-077's build-arg path is retired now that distroless
    can't run groupadd. Compose should not pass DOCKER_GID as a build-arg
    anymore — operators should be told the one way, not two confusing ways."""
    doc = _compose_doc()
    for svc in ("hub", "scout"):
        build = doc["services"][svc].get("build", {})
        args = build.get("args", {}) if isinstance(build, dict) else {}
        assert "DOCKER_GID" not in args, (
            f"{svc}: BC-078 moves DOCKER_GID from build.args to group_add — "
            f"remove it from build.args to avoid confusing operators"
        )


# ── pyproject allows 3.11 at runtime ────────────────────────────────────────


def test_pyproject_requires_python_allows_311() -> None:
    """CAP-T161: distroless python3-debian12 ships 3.11; pyproject must allow it."""
    content = PYPROJECT.read_text()
    match = re.search(r'requires-python\s*=\s*"([^"]+)"', content)
    assert match, "pyproject.toml must declare requires-python"
    spec = match.group(1)
    assert "3.11" in spec or ">=3.1" in spec, (
        f"requires-python must allow 3.11 for distroless runtime, got `{spec}`"
    )
    # Explicit guard against the previous >=3.12 pin.
    assert spec != ">=3.12", "requires-python still pinned to >=3.12 — BC-078 lowered to >=3.11"


# ── CHANGELOG upgrade step ──────────────────────────────────────────────────


def test_changelog_documents_65532_chown() -> None:
    """CAP-T161: operators upgrading from BC-077 (uid 1000) to BC-078
    (uid 65532) need a second one-time chown. Must be documented."""
    content = CHANGELOG.read_text()
    assert "65532" in content, (
        "CHANGELOG must document the uid 65532 chown step for operators upgrading from BC-077"
    )
    assert "chown -R 65532" in content, (
        "CHANGELOG must contain the exact `chown -R 65532:65532 /data` command"
    )
