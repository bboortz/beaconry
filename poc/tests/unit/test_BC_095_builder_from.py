"""BC-095 — Builder stage FROM forge-builder-trixie.

Covers:
    - CAP-T196: both Dockerfiles' builder stage FROM points at
      ghcr.io/bboortz/forge-builder-trixie:latest; upstream
      python:*-trixie removed; hub still installs grype + docker CLI;
      scout's builder prelude is minimal.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HUB_DF = REPO / "poc" / "hub" / "Dockerfile"
SCOUT_DF = REPO / "poc" / "scout" / "Dockerfile"

EXPECTED_BUILDER = "FROM ghcr.io/bboortz/forge-builder-trixie:latest AS builder"
# BC-097 broadened to :latest (preferred) with :3.13 kept as a rollback path.
RUNTIME_BASES = (
    "ghcr.io/bboortz/python-scratch-trixie:latest",
    "ghcr.io/bboortz/python-scratch-trixie:3.13",
)


def _hub() -> str:
    return HUB_DF.read_text(encoding="utf-8")


def _scout() -> str:
    return SCOUT_DF.read_text(encoding="utf-8")


# ── Builder FROM-ref swaps ───────────────────────────────────────────────────


def test_hub_builder_from_forge() -> None:
    """Hub's builder stage must FROM the forge builder image."""
    assert EXPECTED_BUILDER in _hub(), (
        f"hub/Dockerfile missing the BC-095 builder FROM line:\n  {EXPECTED_BUILDER}"
    )


def test_scout_builder_from_forge() -> None:
    """Scout's builder stage must FROM the forge builder image."""
    assert EXPECTED_BUILDER in _scout()


def test_no_upstream_python_from() -> None:
    """Neither Dockerfile may still FROM an upstream `python:…` image."""
    for name, body in (("hub", _hub()), ("scout", _scout())):
        # match `FROM python:` (word boundary — exclude `python-scratch-trixie`)
        offending = [
            line.strip() for line in body.splitlines() if line.lstrip().startswith("FROM python:")
        ]
        assert not offending, (
            f"{name}/Dockerfile still has an upstream python FROM:\n  " + "\n  ".join(offending)
        )


def test_runtime_stage_unchanged() -> None:
    """Runtime stage must still be the forge scratch python base (BC-097: any accepted tag)."""
    for name, body in (("hub", _hub()), ("scout", _scout())):
        assert any(base in body for base in RUNTIME_BASES), (
            f"{name}/Dockerfile lost the forge scratch python runtime base "
            f"(expected one of {RUNTIME_BASES})"
        )


# ── Hub still installs grype + docker CLI ───────────────────────────────────


def test_hub_still_installs_grype() -> None:
    """Hub needs grype for CVE matching — forge builder base doesn't ship it.

    Post-Docker-Hub-pivot: grype comes from `anchore/grype:latest` image via
    `COPY --from=grype-src` instead of `curl install.sh`. GitHub's release CDN
    flapped 504s on 2026-11-08; Docker Hub stayed up.
    """
    body = _hub()
    assert "anchore/grype" in body, "hub/Dockerfile lost grype entirely"
    assert "COPY --from=grype-src /grype" in body, (
        "grype must come from a FROM stage (Docker Hub-sourced), not curl install.sh"
    )


def test_hub_still_installs_docker_cli() -> None:
    """Hub needs the docker CLI static binary for DockerAdapter.execute."""
    body = _hub()
    assert "docker-${DOCKER_CLI_VERSION}.tgz" in body
    assert "ARG DOCKER_CLI_VERSION=" in body


def test_hub_purges_curl_after_fetches() -> None:
    """Build-time curl must not survive in the final COPY'd builder layer."""
    body = _hub()
    assert "apt-get purge -y curl" in body


# ── Scout's builder prelude is minimal ──────────────────────────────────────


def test_scout_builder_prelude_minimal() -> None:
    """Scout's builder prelude (between FROM and WORKDIR) should contain
    no RUN lines — the forge builder base already provides syft + python + pip.

    Finding ANY RUN between the builder FROM and WORKDIR means a prelude
    step snuck back in; investigate before accepting.
    """
    body = _scout()
    lines = body.splitlines()
    # Find the builder FROM + the next WORKDIR after it.
    from_idx = next(
        (
            i
            for i, ln in enumerate(lines)
            if ln.strip().startswith("FROM ghcr.io/bboortz/forge-builder-trixie:latest")
        ),
        -1,
    )
    workdir_idx = next(
        (i for i, ln in enumerate(lines) if i > from_idx and ln.strip().startswith("WORKDIR")),
        -1,
    )
    assert from_idx >= 0 and workdir_idx > from_idx, (
        "scout/Dockerfile: can't locate builder FROM → WORKDIR span"
    )
    prelude = [
        ln.strip() for ln in lines[from_idx + 1 : workdir_idx] if ln.strip().startswith("RUN ")
    ]
    assert prelude == [], (
        "scout/Dockerfile builder prelude unexpectedly runs commands:\n  "
        + "\n  ".join(prelude)
        + "\n(expected zero RUN lines — forge builder base covers scout's needs)"
    )


def test_scout_does_not_install_syft() -> None:
    """Belt-and-braces: scout's Dockerfile must NOT re-install syft."""
    body = _scout()
    assert "anchore/syft/main/install.sh" not in body, (
        "scout/Dockerfile still installs syft — delete the line, "
        "forge-builder-trixie base already ships it"
    )


# ── Runtime COPY still pulls syft (and hub pulls grype + docker) ────────────


def test_hub_runtime_copies_toolchain() -> None:
    """Hub's runtime stage must still COPY syft, grype, and docker from builder."""
    body = _hub()
    for tool in ("syft", "grype", "docker"):
        assert f"/usr/local/bin/{tool}" in body, (
            f"hub runtime copy for {tool} missing — runtime image won't have it"
        )


def test_scout_runtime_copies_syft() -> None:
    """Scout's runtime must still COPY syft from the builder."""
    body = _scout()
    assert "/usr/local/bin/syft" in body
