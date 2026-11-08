"""BC-097 — Compose healthchecks use forge's /hc; runtime base tracks :latest.

Covers:
    - CAP-T198: every compose healthcheck across the 5 compose files
      uses `["CMD", "/hc", "-t", "3", "http://localhost:<port>/<path>"]`.
      Zero inline `python3 -c` healthchecks remain.
    - CAP-T199: both poc/{hub,scout}/Dockerfile runtime FROM
      `ghcr.io/bboortz/python-scratch-trixie:latest`. No `:3.13`
      runtime FROM remains. `verify_pinned_versions.py` passes.
"""

from __future__ import annotations

import re
from pathlib import Path

from poc.scripts import verify_pinned_versions

REPO = Path(__file__).resolve().parents[3]

COMPOSE_FILES = (
    REPO / "poc" / "docker-compose.yml",
    REPO / "poc" / "docker-compose.cold-start.yml",
    REPO / "poc" / "docker-compose.scout-only.yml",
    REPO / "poc" / "scenarios" / "compose.yml",
)
HUB_DF = REPO / "poc" / "hub" / "Dockerfile"
SCOUT_DF = REPO / "poc" / "scout" / "Dockerfile"


# ── Compose healthcheck shape ────────────────────────────────────────────────

# `["CMD", "/hc", "-t", "3", "http://localhost:<port>/<readyz|startupz>"]`
_HC_PROBE_RE = re.compile(
    r'test:\s*\["CMD",\s*"/hc",\s*"-t",\s*"3",\s*'
    r'"http://localhost:\d+/(?:readyz|startupz)"\]'
)


def test_all_healthchecks_use_hc() -> None:
    """Every compose healthcheck across the 5 compose files uses `/hc -t 3`."""
    for compose in COMPOSE_FILES:
        text = compose.read_text()
        test_lines = [line for line in text.splitlines() if line.lstrip().startswith("test:")]
        assert test_lines, f"{compose}: no `test:` lines found"
        for line in test_lines:
            assert _HC_PROBE_RE.search(line), (
                f"{compose}: healthcheck does not match `/hc -t 3 ...`: {line.strip()}"
            )


def test_no_inline_python_healthchecks() -> None:
    """No compose file still shells out to `python3 -c` for a healthcheck."""
    for compose in COMPOSE_FILES:
        text = compose.read_text()
        for line in text.splitlines():
            if not line.lstrip().startswith("test:"):
                continue
            assert "python3" not in line, (
                f"{compose}: legacy python-inline healthcheck still present: {line.strip()}"
            )


def test_probe_count_matches_expectation() -> None:
    """Sanity: BC-097 ships 6 probe sites. Guards against a stray extra."""
    total = 0
    for compose in COMPOSE_FILES:
        text = compose.read_text()
        total += len([line for line in text.splitlines() if line.lstrip().startswith("test:")])
    assert total == 6, f"expected 6 compose healthcheck probes, got {total}"


# ── Dockerfile runtime FROM = :latest ────────────────────────────────────────


def test_runtime_from_tracks_latest() -> None:
    """Both Dockerfiles' runtime stage FROM python-scratch-trixie:latest."""
    expected = re.compile(
        r"^FROM\s+ghcr\.io/bboortz/python-scratch-trixie:latest\s+AS\s+runtime\b",
        re.MULTILINE,
    )
    for df in (HUB_DF, SCOUT_DF):
        body = df.read_text()
        assert expected.search(body), (
            f"{df}: runtime stage does not FROM ghcr.io/bboortz/python-scratch-trixie:latest"
        )


def test_no_colon_3_13_runtime_remains() -> None:
    """No runtime stage still FROMs the semver-pinned :3.13 tag."""
    stale = re.compile(
        r"^FROM\s+ghcr\.io/bboortz/python-scratch-trixie:3\.13\s+AS\s+runtime\b",
        re.MULTILINE,
    )
    for df in (HUB_DF, SCOUT_DF):
        body = df.read_text()
        assert not stale.search(body), (
            f"{df}: stale runtime FROM python-scratch-trixie:3.13 still present"
        )


# ── verify_pinned_versions.py accepts the new tag ────────────────────────────


def test_verify_pinned_versions_passes_on_branch() -> None:
    """verify_pinned_versions must consider the `:latest` runtime tag valid."""
    violations = verify_pinned_versions.check(REPO)
    assert violations == [], f"verify-pinned-versions failed on branch tip: {violations}"


def test_runtime_image_re_accepts_latest() -> None:
    """RUNTIME_IMAGE_RE directly matches the new preferred tag."""
    sample = "FROM ghcr.io/bboortz/python-scratch-trixie:latest AS runtime"
    assert verify_pinned_versions.RUNTIME_IMAGE_RE.search(sample), (
        "RUNTIME_IMAGE_RE must accept :latest"
    )


def test_runtime_image_re_still_accepts_3_13_for_rollback() -> None:
    """:3.13 stays accepted so operators rolling back don't trip the lint."""
    sample = "FROM ghcr.io/bboortz/python-scratch-trixie:3.13 AS runtime"
    assert verify_pinned_versions.RUNTIME_IMAGE_RE.search(sample)


def test_runtime_image_preferred_names_latest() -> None:
    assert (
        verify_pinned_versions.RUNTIME_IMAGE_PREFERRED
        == "ghcr.io/bboortz/python-scratch-trixie:latest"
    )
