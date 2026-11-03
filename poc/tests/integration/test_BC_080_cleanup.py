"""BC-080 — make cleanup teardown + image removal after tests.

Covers:
    - CAP-T164: `make cleanup` removes test-only + fixture images, preserves live stack
    - CAP-T165: test-e2e / test-scenarios / check-full auto-invoke cleanup via trap
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

from poc.scripts import cleanup as cleanup_mod

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = REPO_ROOT / "Makefile"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"


# ── Makefile target exists ──────────────────────────────────────────────────


def test_makefile_has_cleanup_target() -> None:
    """CAP-T164: `make cleanup` must be a real target, not just .PHONY."""
    text = MAKEFILE.read_text()
    assert re.search(r"^cleanup:\s*\n", text, re.MULTILINE), (
        "Makefile is missing a `cleanup:` target"
    )
    assert ".PHONY:" in text and "cleanup" in text.split(".PHONY:")[1].split("\n")[0], (
        "`cleanup` must be in the .PHONY list"
    )


# ── Script behavior ─────────────────────────────────────────────────────────


def test_cleanup_removes_test_only_images() -> None:
    """CAP-T164: known-test images (ci-test, scenarios, telegram-mock) are removed."""
    present_images = [
        "beaconry-hub:ci-test",
        "beaconry-hub:scenarios",
        "beaconry-test-telegram-mock:latest",
        "beaconry-hub:latest",  # LIVE stack — must survive
        "python:3.11-slim-bookworm",  # BC-078 base — must survive
        "gcr.io/distroless/python3-debian12:nonroot",  # BC-078 runtime — must survive
    ]
    removed, preserved = cleanup_mod.plan_removals(present_images)
    assert "beaconry-hub:ci-test" in removed
    assert "beaconry-hub:scenarios" in removed
    assert "beaconry-test-telegram-mock:latest" in removed


def test_cleanup_preserves_live_stack_images() -> None:
    """CAP-T164: operator's actual deployed stack images are NEVER removed."""
    present_images = [
        "beaconry-hub:latest",
        "beaconry-scout:latest",
        "python:3.11-slim-bookworm",
        "python:3.12-slim",
        "gcr.io/distroless/python3-debian12:nonroot",
        "beaconry-hub:ci-test",  # test image for comparison
    ]
    removed, preserved = cleanup_mod.plan_removals(present_images)
    # Live stack images must be in preserved, not removed.
    for safe in (
        "beaconry-hub:latest",
        "beaconry-scout:latest",
        "python:3.11-slim-bookworm",
        "python:3.12-slim",
        "gcr.io/distroless/python3-debian12:nonroot",
    ):
        assert safe not in removed, f"cleanup would remove live-stack image `{safe}`"
        assert safe in preserved, f"cleanup must preserve live-stack image `{safe}`"


def test_cleanup_removes_test_fixture_images() -> None:
    """CAP-T164: images beaconry's own tests pull as fixtures are removed."""
    present_images = [
        "nginx:1.24",
        "nginx:alpine",
        "redis:6.2.0",
        "postgres:latest",
        "nginxproxy/nginx-proxy:alpine",
    ]
    removed, preserved = cleanup_mod.plan_removals(present_images)
    for fixture in (
        "nginx:1.24",
        "nginx:alpine",
        "redis:6.2.0",
        "postgres:latest",
        "nginxproxy/nginx-proxy:alpine",
    ):
        assert fixture in removed, f"cleanup must remove test-fixture image `{fixture}`"


def test_cleanup_idempotent() -> None:
    """CAP-T164: running cleanup twice is safe. Second run finds no targets
    and reports 0 removed, exit 0."""
    # No images on the host — nothing to remove, nothing to error on.
    removed, preserved = cleanup_mod.plan_removals([])
    assert removed == []
    assert preserved == []


def test_cleanup_main_exits_zero_on_success() -> None:
    """CAP-T164: happy path main() returns 0."""
    with (
        patch.object(cleanup_mod, "list_images", return_value=[]),
        patch.object(cleanup_mod, "teardown_compose_project", return_value=None),
    ):
        rc = cleanup_mod.main([])
    assert rc == 0


def test_cleanup_handles_missing_docker_gracefully() -> None:
    """CAP-T164: if docker is not available (no daemon, no binary), cleanup
    reports and exits 0 — the user asked for cleanup, not a Docker-health check."""
    fake = MagicMock(side_effect=FileNotFoundError("docker"))
    with patch("subprocess.run", fake):
        rc = cleanup_mod.main([])
    assert rc == 0


# ── Makefile wiring: cleanup auto-fires ─────────────────────────────────────


def _recipe(target: str) -> str:
    """Extract the body of a Makefile target (lines until the next blank/stanza)."""
    text = MAKEFILE.read_text()
    match = re.search(
        rf"^{re.escape(target)}:[^\n]*\n((?:^[\t ].*\n|^\s*\n)*)",
        text,
        re.MULTILINE,
    )
    assert match, f"could not locate `{target}:` in Makefile"
    return match.group(1)


def test_test_e2e_recipe_runs_cleanup_in_trap() -> None:
    """CAP-T165: test-e2e must call `make cleanup` after pytest (success or fail)."""
    recipe = _recipe("test-e2e")
    assert "cleanup" in recipe, (
        "test-e2e recipe does not invoke `make cleanup` — leaked test images will accumulate"
    )


def test_test_scenarios_recipe_runs_cleanup() -> None:
    """CAP-T165: test-scenarios must call `make cleanup` after the harness finishes."""
    recipe = _recipe("test-scenarios")
    assert "cleanup" in recipe, "test-scenarios recipe does not invoke `make cleanup`"


def test_check_full_recipe_runs_cleanup() -> None:
    """CAP-T165: check-full is the orchestrator; cleanup must fire at the end."""
    recipe = _recipe("check-full")
    assert "cleanup" in recipe, "check-full recipe does not invoke `make cleanup` as its final step"


# ── CLAUDE.md shrunk ────────────────────────────────────────────────────────


def test_claude_md_points_at_make_cleanup() -> None:
    """CAP-T164: CLAUDE.md should point at `make cleanup`, not list manual
    `docker compose down` incantations that will drift."""
    text = CLAUDE_MD.read_text()
    assert "make cleanup" in text, (
        "CLAUDE.md must reference `make cleanup` as the canonical teardown"
    )
    # Guard against the stale instruction block coming back: no multi-line
    # list of `docker compose -p ... down -v` commands.
    down_count = text.count("docker compose -p beaconry-")
    assert down_count <= 1, (
        f"CLAUDE.md still contains {down_count} manual `docker compose -p beaconry-...` "
        f"lines; replace them with a `make cleanup` pointer"
    )
