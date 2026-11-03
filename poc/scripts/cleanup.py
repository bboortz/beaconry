"""BC-080 — `make cleanup`: tear down beaconry test stacks + remove images
beaconry's own tests pulled or built.

The problem: beaconry's test harnesses tear down containers + volumes but
leave images on the host. Scout then scans them on the next cycle, grype
matches, and the operator gets Telegram alerts for `redis:6.2.0`,
`nginx:1.24`, `beaconry-hub:ci-test` etc. — none of which are deployed
services. Every false positive erodes trust in real alerts.

The fix: concrete tooling, not CLAUDE.md guidance. This script enumerates
test-only + test-fixture images and `docker image rm`s them. It is
Python-reviewable + unit-testable; the Makefile `cleanup:` target is a
thin wrapper.

Safety guarantees:
- NEVER removes `beaconry-hub:latest` / `beaconry-scout:latest` — those
  are the operator's actual deployed stack.
- NEVER removes base images (`python:*`, `gcr.io/distroless/*`) — they
  back the live stack.
- Idempotent: running it twice is safe. First run removes; second finds
  nothing and exits 0.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# ── Image enumeration ──────────────────────────────────────────────────────

# Images beaconry's own test harness BUILDS. Only ever present if a test
# ran recently; never part of the operator's live stack.
_TEST_ONLY_IMAGES: frozenset[str] = frozenset(
    {
        # test_BC_005_compose.py::test_hub_image_bundles_docker_cli builds this.
        "beaconry-hub:ci-test",
        # poc/scenarios/compose.yml builds this (BC-038 scenario harness).
        "beaconry-hub:scenarios",
        # poc/docker-compose.test.yml builds this (BC-018 e2e stack).
        "beaconry-test-telegram-mock:latest",
    }
)

# Images beaconry's own tests PULL as fixtures. Operator stacks pulling
# these for real reasons will see a one-time re-pull on next up — accepted.
# Based on grep of poc/scenarios + poc/tests for `docker pull` / image: refs.
_TEST_FIXTURE_IMAGES: frozenset[str] = frozenset(
    {
        # demo.sh pulls this.
        "nginx:1.24",
        # BC-002 real-pipeline + BC-005 compose fixtures.
        "nginx:1.24-fixture",
        # test-e2e probe-target-test uses nginx:alpine.
        "nginx:alpine",
        "nginx:1",
        "nginx:latest",
        "nginx:1.24-test",
        # Scenario fixtures (BC-038).
        "redis:6.2.0",
        "redis:7-alpine",
        "redis:7",
        "redis:8-alpine",
        "redis:latest",
        "postgres:16-alpine",
        "postgres:16.1.3-alpine",
        "postgres:16",
        "postgres:latest",
        "nginxproxy/nginx-proxy:alpine",
        "busybox:latest",
        "alpine:main",
    }
)

# Hard-coded SAFETY list — these are the operator's live-stack images.
# Must NEVER be removed, even if a future enumeration bug would list
# them. Belt-and-braces redundant check.
_LIVE_STACK_IMAGES: frozenset[str] = frozenset(
    {
        "beaconry-hub:latest",
        "beaconry-scout:latest",
    }
)

# Compose projects that beaconry test harnesses spin up. Teardown is
# idempotent — `docker compose down` on a non-existent project is a no-op.
_TEST_COMPOSE_PROJECTS: tuple[tuple[str, str], ...] = (
    # (project_name, compose_file_relative_to_repo_root)
    ("beaconry-test", "poc/docker-compose.test.yml"),
    ("beaconry-scenarios", "poc/scenarios/compose.yml"),
)

# Image names that are definitely NOT ours to remove — scanned against
# everything. Prefix-based: anything starting with these stays.
_PRESERVE_PREFIXES: tuple[str, ...] = (
    "python:",
    "gcr.io/distroless/",
)


# ── Pure helpers (unit-testable) ───────────────────────────────────────────


def is_removable(image: str) -> bool:
    """Return True if beaconry's own tests put this image here and we can
    safely remove it. Live-stack and base images always False."""
    if image in _LIVE_STACK_IMAGES:
        return False
    for prefix in _PRESERVE_PREFIXES:
        if image.startswith(prefix):
            return False
    return image in _TEST_ONLY_IMAGES or image in _TEST_FIXTURE_IMAGES


def plan_removals(present_images: list[str]) -> tuple[list[str], list[str]]:
    """Partition `present_images` into (to-remove, to-preserve) based on
    the removable rule. Pure function — safe to unit-test without Docker."""
    to_remove: list[str] = []
    to_preserve: list[str] = []
    for image in present_images:
        if is_removable(image):
            to_remove.append(image)
        else:
            to_preserve.append(image)
    return to_remove, to_preserve


# ── Docker shell-outs (integration-tested via main() mocks) ────────────────


def list_images() -> list[str]:
    """Return every image tag present on the host (one entry per tag).
    Untagged images (<none>:<none>) are skipped — they're overwritten
    layers from rebuilds and belong to Docker's prune workflow, not ours."""
    try:
        proc = subprocess.run(
            ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        # No docker binary. Can't list, can't remove — exit cleanly.
        return []
    if proc.returncode != 0:
        return []
    return [
        line.strip() for line in proc.stdout.splitlines() if line.strip() and "<none>" not in line
    ]


def remove_image(image: str) -> bool:
    """`docker image rm` with best-effort semantics. Returns True on
    successful removal, False if the image was in use / not found."""
    try:
        proc = subprocess.run(
            ["docker", "image", "rm", image],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return False
    return proc.returncode == 0


def teardown_compose_project(project: str, compose_file: str, repo_root: str) -> None:
    """`docker compose -p <project> -f <compose_file> down -v --remove-orphans`.
    Best-effort — a non-existent project is a no-op; a broken compose file
    just logs and moves on."""
    try:
        subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                project,
                "-f",
                compose_file,
                "down",
                "-v",
                "--remove-orphans",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError:
        # No docker CLI available. Nothing to tear down.
        return


# ── Orchestrator ───────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    import pathlib

    parser = argparse.ArgumentParser(prog="poc.scripts.cleanup", description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress per-image removal lines; still prints summary",
    )
    args = parser.parse_args(argv)

    repo_root = str(pathlib.Path(__file__).resolve().parents[2])

    # Phase 1: teardown compose projects.
    for project, compose_file in _TEST_COMPOSE_PROJECTS:
        if not args.quiet:
            print(f"→ tearing down compose project `{project}`")
        teardown_compose_project(project, compose_file, repo_root)

    # Phase 2: enumerate + partition.
    present = list_images()
    to_remove, to_preserve = plan_removals(present)

    # Phase 3: remove.
    removed_count = 0
    for image in to_remove:
        if not args.quiet:
            print(f"→ removing `{image}`")
        if remove_image(image):
            removed_count += 1

    print(f"✓ cleanup: removed {removed_count} image(s), preserved {len(to_preserve)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
