"""BC-083 — forge base image migration + verify-base-images preflight lint.

Covers:
    - CAP-T169: Runtime image from ghcr.io/bboortz/python-scratch-trixie:3.13
    - CAP-T170: Pre-flight inspection recorded in the plan doc
    - CAP-T171: `make verify-base-images` — standing preflight lint
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from unittest.mock import patch

from poc.scripts import verify_base_images, verify_pinned_versions

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = REPO_ROOT / "Makefile"
HUB_DOCKERFILE = REPO_ROOT / "poc" / "hub" / "Dockerfile"
SCOUT_DOCKERFILE = REPO_ROOT / "poc" / "scout" / "Dockerfile"
PLAN_DOC = REPO_ROOT / "docs" / "plan" / "BC-083-forge-base-image-plan.md"

# BC-097 broadened to accept either `:latest` (preferred) or `:3.13`
# (rollback path). The repo-identity check is the forge path, not the tag.
FORGE_RUNTIME_REPO = "ghcr.io/bboortz/python-scratch-trixie"
FORGE_RUNTIME_TAGS = ("latest", "3.13")
# Current preferred ref — used by cache-fixture tests below.
FORGE_RUNTIME = f"{FORGE_RUNTIME_REPO}:{FORGE_RUNTIME_TAGS[0]}"
_FORGE_RUNTIME_RE = re.compile(
    rf"^FROM\s+{re.escape(FORGE_RUNTIME_REPO)}:(?:{'|'.join(re.escape(t) for t in FORGE_RUNTIME_TAGS)})\s+AS\s+runtime\b",
    re.MULTILINE,
)


# ── CAP-T169: Dockerfiles use the forge runtime ─────────────────────────────


def test_hub_runtime_is_forge() -> None:
    """CAP-T169: hub runtime stage must be the forge image (any accepted tag)."""
    content = HUB_DOCKERFILE.read_text()
    assert _FORGE_RUNTIME_RE.search(content), (
        f"hub Dockerfile runtime stage must FROM {FORGE_RUNTIME_REPO}:"
        f"{{{','.join(FORGE_RUNTIME_TAGS)}}}"
    )


def test_scout_runtime_is_forge() -> None:
    """CAP-T169: scout runtime stage must be the forge image (any accepted tag)."""
    content = SCOUT_DOCKERFILE.read_text()
    assert _FORGE_RUNTIME_RE.search(content), (
        f"scout Dockerfile runtime stage must FROM {FORGE_RUNTIME_REPO}:"
        f"{{{','.join(FORGE_RUNTIME_TAGS)}}}"
    )


def test_runtime_site_packages_path() -> None:
    """CAP-T169: forge sys.path uses /usr/local/lib/python3.13; site-packages
    COPY must target that path or imports fail inside the container."""
    for df in (HUB_DOCKERFILE, SCOUT_DOCKERFILE):
        content = df.read_text()
        assert "/usr/local/lib/python3.13/site-packages" in content, (
            f"{df.name}: site-packages COPY must target "
            f"/usr/local/lib/python3.13/site-packages for forge sys.path"
        )


def test_runtime_binaries_in_local_bin() -> None:
    """CAP-T169: forge PATH=/usr/local/bin only; native binaries must land there."""
    hub = HUB_DOCKERFILE.read_text()
    scout = SCOUT_DOCKERFILE.read_text()
    assert "/usr/local/bin/syft" in hub, "hub Dockerfile must COPY syft to /usr/local/bin"
    assert "/usr/local/bin/grype" in hub, "hub Dockerfile must COPY grype to /usr/local/bin"
    assert "/usr/local/bin/docker" in hub, "hub Dockerfile must COPY docker to /usr/local/bin"
    assert "/usr/local/bin/syft" in scout, "scout Dockerfile must COPY syft to /usr/local/bin"


def test_runtime_does_not_override_user() -> None:
    """CAP-T169: forge image bakes USER 65532; beaconry runtime stage MUST NOT
    add a `USER root` directive. Any explicit USER would be adding complexity."""
    for df in (HUB_DOCKERFILE, SCOUT_DOCKERFILE):
        content = df.read_text()
        # Split at the runtime stage boundary
        parts = re.split(r"^FROM\s+", content, flags=re.MULTILINE)
        runtime = parts[-1]
        # No explicit USER root (that would defeat the baked-in 65532)
        assert "USER root" not in runtime, (
            f"{df.name}: runtime stage must not reset to root; forge image is uid 65532"
        )


# ── CAP-T170: Pre-flight findings recorded in the plan ──────────────────────


def test_preflight_findings_in_plan_doc() -> None:
    """CAP-T170: the plan doc must document user/uid, ENTRYPOINT, sys.path,
    SSL_CERT_FILE — the facts a reviewer needs to validate the swap."""
    assert PLAN_DOC.exists(), "BC-083 plan doc missing"
    text = PLAN_DOC.read_text()
    required = [
        "65532",  # uid
        "/usr/local/bin/python3",  # ENTRYPOINT
        "/usr/local/lib/python3.13",  # sys.path
        "SSL_CERT_FILE",  # TLS story
        "PYTHONDONTWRITEBYTECODE",  # Python env hygiene
        "cosign",  # signature awareness
    ]
    missing = [r for r in required if r not in text]
    assert not missing, (
        f"plan doc missing pre-flight evidence: {missing}. "
        f"CAP-T170 requires these facts documented before the Dockerfile swap."
    )


# ── CAP-T166 ext: Pinned-version lint soft migration ────────────────────────


def _write_fixture_dockerfile(
    tmp_path: Path,
    *,
    builder: str = "python:3.13-slim-trixie",
    docker_cli: str = "29.9.2",
    runtime: str,
) -> Path:
    """Build a minimal valid pair of Dockerfiles under tmp_path for lint testing."""
    hub_dir = tmp_path / "poc" / "hub"
    scout_dir = tmp_path / "poc" / "scout"
    hub_dir.mkdir(parents=True)
    scout_dir.mkdir(parents=True)
    hub_dir.joinpath("Dockerfile").write_text(
        f"FROM {builder} AS builder\n"
        f"ARG DOCKER_CLI_VERSION={docker_cli}\n"
        f"FROM {runtime} AS runtime\n"
    )
    scout_dir.joinpath("Dockerfile").write_text(
        f"FROM {builder} AS builder\nFROM {runtime} AS runtime\n"
    )
    return tmp_path


def test_pinned_versions_accepts_forge_tag(tmp_path: Path) -> None:
    """CAP-T166 ext: lint must accept the forge tag as the primary runtime."""
    repo = _write_fixture_dockerfile(tmp_path, runtime=FORGE_RUNTIME)
    violations = verify_pinned_versions.check(repo)
    assert violations == [], f"forge runtime rejected: {violations}"


def test_pinned_versions_accepts_distroless_fallback(tmp_path: Path) -> None:
    """CAP-T166 ext: soft migration — distroless debian13 is still accepted
    during the migration window. Hard cutoff ships in a later BC."""
    repo = _write_fixture_dockerfile(tmp_path, runtime="gcr.io/distroless/python3-debian13:nonroot")
    violations = verify_pinned_versions.check(repo)
    assert violations == [], f"distroless debian13 rejected (should pass migration): {violations}"


def test_pinned_versions_rejects_arbitrary_ghcr(tmp_path: Path) -> None:
    """CAP-T166 ext: the lint pins the specific forge repo, not any ghcr
    repo. A random `ghcr.io/attacker/malicious:3.13` must fail."""
    repo = _write_fixture_dockerfile(tmp_path, runtime="ghcr.io/attacker/malicious:3.13")
    violations = verify_pinned_versions.check(repo)
    assert any("runtime" in v.lower() for v in violations), (
        f"arbitrary ghcr repo must be rejected; got: {violations}"
    )


# ── CAP-T171: verify-base-images standing lint ──────────────────────────────


def test_verify_base_images_parses_froms(tmp_path: Path) -> None:
    """CAP-T171: enumerate_froms must find every FROM across a repo's
    Dockerfiles, dedupe, and skip stage aliases like `FROM builder`."""
    (tmp_path / "poc" / "hub").mkdir(parents=True)
    (tmp_path / "poc" / "hub" / "Dockerfile").write_text(
        "FROM python:3.13-slim-trixie AS builder\n"
        "FROM ghcr.io/bboortz/python-scratch-trixie:3.13 AS runtime\n"
        "COPY --from=builder /app /app\n"
    )
    (tmp_path / "poc" / "scout").mkdir(parents=True)
    (tmp_path / "poc" / "scout" / "Dockerfile").write_text(
        "FROM python:3.13-slim-trixie AS builder\n"
        "FROM ghcr.io/bboortz/python-scratch-trixie:3.13 AS runtime\n"
    )
    found = verify_base_images.enumerate_froms(tmp_path)
    assert "python:3.13-slim-trixie" in found
    assert "ghcr.io/bboortz/python-scratch-trixie:3.13" in found
    # Stage aliases must NOT appear (they're intra-build refs, not pulls).
    assert not any(f.startswith("builder") for f in found)


def test_verify_base_images_cache_hit(tmp_path: Path) -> None:
    """CAP-T171: a successful probe within TTL must short-circuit network I/O.
    Network-flakiness in operator environments can't gate pre-push."""
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps(
            {
                FORGE_RUNTIME: {
                    "ts": time.time(),
                    "ok": True,
                }
            }
        )
    )
    # If cache hits, the HTTP probe must not be invoked.
    with patch.object(verify_base_images, "_probe_image_live") as probe:
        rc = verify_base_images.check(
            [FORGE_RUNTIME], cache_path=cache, ttl_s=verify_base_images.DEFAULT_TTL_S
        )
    assert rc == []
    probe.assert_not_called()


def test_verify_base_images_cache_expired(tmp_path: Path) -> None:
    """CAP-T171: TTL-expired entries must be re-probed."""
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps(
            {
                FORGE_RUNTIME: {
                    "ts": time.time() - 999_999,  # ancient
                    "ok": True,
                }
            }
        )
    )
    with patch.object(verify_base_images, "_probe_image_live", return_value=True) as probe:
        rc = verify_base_images.check([FORGE_RUNTIME], cache_path=cache, ttl_s=60)
    assert rc == []
    probe.assert_called_once()


def test_verify_base_images_skip_env() -> None:
    """CAP-T171: BEACONRY_SKIP_BASE_IMAGES=1 must short-circuit the gate
    cleanly for air-gapped CI that can't reach ghcr.io / gcr.io / docker.io."""
    with patch.dict(os.environ, {"BEACONRY_SKIP_BASE_IMAGES": "1"}):
        rc = verify_base_images.main([])
    assert rc == 0


def test_verify_base_images_flags_private_image(tmp_path: Path) -> None:
    """CAP-T171: a probe returning False (401/403/404/5xx) must surface as
    a violation. This is the "forge went private" / "tag retagged" case."""
    cache = tmp_path / "cache.json"  # fresh — no entries yet
    with patch.object(verify_base_images, "_probe_image_live", return_value=False):
        violations = verify_base_images.check(
            ["ghcr.io/attacker/private-image:latest"],
            cache_path=cache,
            ttl_s=verify_base_images.DEFAULT_TTL_S,
        )
    assert len(violations) == 1
    assert "ghcr.io/attacker/private-image:latest" in violations[0]


def test_make_check_runs_verify_base_images() -> None:
    """CAP-T171: `make check` must include verify-base-images so a base
    image going private is caught at pre-push, not at `make live-rebuild`."""
    text = MAKEFILE.read_text()
    check_target = re.search(r"^check:\s*(.+)$", text, re.MULTILINE)
    assert check_target, "make check target missing"
    assert "verify-base-images" in check_target.group(1), (
        "verify-base-images must be in the `check:` dependency list"
    )
