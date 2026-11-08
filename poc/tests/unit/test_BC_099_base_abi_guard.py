"""BC-099 — Build-time ABI guard against base-image Python-minor drift.

Covers:
    - CAP-T201: both poc/{hub,scout}/Dockerfile require BuildKit via
      the `# syntax=docker/dockerfile:` directive, declare a named
      `runtime-src` stage pointing at python-scratch-trixie:latest,
      and run a single ABI-guard step that mounts the runtime base's
      /usr/local/lib, reads the runtime Python minor from the
      directory name, and fails the build if it disagrees with the
      builder's minor. Makefile exports DOCKER_BUILDKIT=1 +
      COMPOSE_DOCKER_CLI_BUILD=1.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HUB_DF = REPO / "poc" / "hub" / "Dockerfile"
SCOUT_DF = REPO / "poc" / "scout" / "Dockerfile"
MAKEFILE = REPO / "Makefile"

_DOCKERFILES = (HUB_DF, SCOUT_DF)


# ── G4a: syntax directive at top of both files ───────────────────────────────


def test_syntax_directive_present() -> None:
    """`# syntax=docker/dockerfile:` must be the first line of each file.

    The directive is Dockerfile's only mechanism to force BuildKit —
    classic builder rejects it with a clear error. BC-099's ABI guard
    uses `--mount=from=<stage>` which is BuildKit-only.
    """
    directive = re.compile(r"^#\s*syntax\s*=\s*docker/dockerfile:\S+\s*$")
    for df in _DOCKERFILES:
        first_line = df.read_text().splitlines()[0]
        assert directive.match(first_line), (
            f"{df.name}: first line must be a `# syntax=docker/dockerfile:...` directive; "
            f"got: {first_line!r}"
        )


# ── G3a: runtime-src named stage ─────────────────────────────────────────────


def test_runtime_src_stage_present() -> None:
    """Each Dockerfile declares `FROM ghcr.io/bboortz/python-scratch-trixie:latest AS runtime-src`.

    The name is load-bearing: the ABI guard mounts
    `--mount=from=runtime-src` and the runtime stage FROMs
    `runtime-src` — both resolve to the same digest within one
    BuildKit resolution context.
    """
    stage_re = re.compile(
        r"^FROM\s+ghcr\.io/bboortz/python-scratch-trixie:latest\s+AS\s+runtime-src\b",
        re.MULTILINE,
    )
    for df in _DOCKERFILES:
        body = df.read_text()
        assert stage_re.search(body), (
            f"{df.name}: missing `FROM ghcr.io/bboortz/python-scratch-trixie:latest AS runtime-src` "
            f"— BC-099 ABI guard needs this named handle"
        )


def test_runtime_stage_from_runtime_src() -> None:
    """Runtime stage must FROM runtime-src (same digest as the guard's mount)."""
    runtime_re = re.compile(r"^FROM\s+runtime-src\s+AS\s+runtime\b", re.MULTILINE)
    for df in _DOCKERFILES:
        body = df.read_text()
        assert runtime_re.search(body), (
            f"{df.name}: runtime stage must `FROM runtime-src AS runtime` — "
            f"reusing the same resolved digest is BC-099's correctness anchor"
        )


# ── G1 + G2: ABI guard RUN step ─────────────────────────────────────────────


def test_abi_guard_run_step_present() -> None:
    """Each Dockerfile has exactly one `RUN --mount=from=runtime-src,…` step."""
    guard_re = re.compile(
        r"^RUN\s+--mount=from=runtime-src,source=/usr/local/lib,target=/runtime-lib\b",
        re.MULTILINE,
    )
    for df in _DOCKERFILES:
        body = df.read_text()
        matches = guard_re.findall(body)
        assert len(matches) == 1, (
            f"{df.name}: expected exactly one BC-099 ABI guard RUN, got {len(matches)}"
        )


def test_abi_guard_compares_minors() -> None:
    """Guard reads both BUILDER_PY_VER and RUNTIME_PY_VER and compares them."""
    for df in _DOCKERFILES:
        body = df.read_text()
        assert 'BUILDER_PY_VER="$(python3 -c' in body, (
            f"{df.name}: guard must read BUILDER_PY_VER from python3 -c"
        )
        assert "RUNTIME_PY_VER=" in body and "/runtime-lib" in body, (
            f"{df.name}: guard must read RUNTIME_PY_VER from the mounted runtime lib dir"
        )
        assert '[ "$BUILDER_PY_VER" != "$RUNTIME_PY_VER" ]' in body, (
            f"{df.name}: guard must compare BUILDER_PY_VER vs RUNTIME_PY_VER"
        )


def test_abi_guard_emits_marker() -> None:
    """Guard's failure message carries the `BC-099 ABI guard tripped` marker."""
    for df in _DOCKERFILES:
        body = df.read_text()
        assert "BC-099 ABI guard tripped" in body, (
            f"{df.name}: guard failure message must include the `BC-099 ABI guard tripped` "
            f"marker (operators grep for it)"
        )


# ── G3b: single-python-dir assertion in the runtime base ────────────────────


def test_runtime_single_py_dir_assertion() -> None:
    """Guard asserts `python-scratch-trixie:latest` contains exactly one python3.X dir.

    Defense vs. a hypothetical forge bug shipping two co-existing dirs.
    """
    for df in _DOCKERFILES:
        body = df.read_text()
        assert 'RUNTIME_PY_DIR_COUNT="$(ls /runtime-lib | grep -cE' in body, (
            f"{df.name}: guard must count python3.X dirs in the runtime base"
        )
        assert '[ "$RUNTIME_PY_DIR_COUNT" != "1" ]' in body, (
            f"{df.name}: guard must fail if RUNTIME_PY_DIR_COUNT != 1"
        )


# ── BC-098 compatibility: single RUN, single layer ──────────────────────────


def test_bc_098_step_subsumed() -> None:
    """The old BC-098 standalone `RUN PY_VER=…` step is gone (subsumed by BC-099).

    Only one RUN line per Dockerfile should carry the PY_VER assignment —
    the merged BC-099 step. A second standalone RUN would be duplication.
    """
    standalone_bc_098 = re.compile(
        r"^RUN\s+PY_VER=\"",
        re.MULTILINE,  # pre-BC-099 shape started with `RUN PY_VER=...`
    )
    for df in _DOCKERFILES:
        body = df.read_text()
        assert not standalone_bc_098.search(body), (
            f"{df.name}: pre-BC-099 `RUN PY_VER=...` step still present — "
            f"BC-099's `RUN --mount=from=runtime-src,...` subsumes it"
        )


# ── G4b: Makefile exports BuildKit env vars ─────────────────────────────────


def test_makefile_exports_buildkit() -> None:
    """Makefile exports DOCKER_BUILDKIT=1 + COMPOSE_DOCKER_CLI_BUILD=1."""
    body = MAKEFILE.read_text()
    assert re.search(r"^export\s+DOCKER_BUILDKIT\s*=\s*1\s*$", body, re.MULTILINE), (
        "Makefile must `export DOCKER_BUILDKIT = 1` at scope so all subshells inherit it"
    )
    assert re.search(r"^export\s+COMPOSE_DOCKER_CLI_BUILD\s*=\s*1\s*$", body, re.MULTILINE), (
        "Makefile must `export COMPOSE_DOCKER_CLI_BUILD = 1` for `docker compose build` path"
    )


# ── Baked-in HEALTHCHECK for standalone `docker inspect` / `docker run` ─────


def test_hub_dockerfile_bakes_healthcheck() -> None:
    """Hub HEALTHCHECK must point at `/hc -t 3 http://localhost:8000/readyz`.

    Homelab smoke does `docker inspect <image>` and reads
    `.Config.Healthcheck.Test`. Pre-BC-099 this was `no-healthcheck`
    because the image had no HEALTHCHECK instruction (compose
    healthchecks live outside the image). Composed path overrides
    this (BC-097 still wins when compose sets its own).
    """
    body = HUB_DF.read_text()
    assert re.search(
        r"HEALTHCHECK\s+--interval=\S+\s+--timeout=\S+\s+--start-period=\S+\s+--retries=\d+\s*\\?\s*\n"
        r'\s*CMD\s+\[\s*"/hc"\s*,\s*"-t"\s*,\s*"3"\s*,\s*"http://localhost:8000/readyz"\s*\]',
        body,
    ), "hub/Dockerfile missing baked HEALTHCHECK targeting `/hc -t 3 http://localhost:8000/readyz`"


def test_scout_dockerfile_bakes_healthcheck() -> None:
    """Scout HEALTHCHECK: `/hc -t 3 http://localhost:8001/readyz`."""
    body = SCOUT_DF.read_text()
    assert re.search(
        r"HEALTHCHECK\s+--interval=\S+\s+--timeout=\S+\s+--start-period=\S+\s+--retries=\d+\s*\\?\s*\n"
        r'\s*CMD\s+\[\s*"/hc"\s*,\s*"-t"\s*,\s*"3"\s*,\s*"http://localhost:8001/readyz"\s*\]',
        body,
    ), (
        "scout/Dockerfile missing baked HEALTHCHECK targeting "
        "`/hc -t 3 http://localhost:8001/readyz`"
    )
