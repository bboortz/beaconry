"""BC-141 — compose memory/CPU cap lint.

Every beaconry service in every compose file must declare:
  - `mem_limit` (hard cap)
  - `memswap_limit` equal to `mem_limit` (swap disabled; swap thrashing
    masked and prolonged the 2026-11-17 outages)
  - `cpus` (bound CPU quota)

The lint catches "I added a new beaconry service and forgot the cap"
at PR time, not after the next host-wide OOM.

Scope — services named beaconry-hub / beaconry-scout / hub / scout in
any of the compose files under `poc/*.yml`. Explicitly excluded by
name:
  - `telegram-mock`: ephemeral test-only service; no operator exposure.
  - probe-target / test fixtures: short-lived.

Exit codes:
  0  all beaconry services in all compose files have the required caps
  1  one or more services missing a cap
  2  usage error / compose file unreadable
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

COMPOSE_GLOBS = (
    "docker-compose.yml",
    "docker-compose.scout-only.yml",
    "docker-compose.hub-ghcr.yml",
    "docker-compose.scout-ghcr.yml",
)

# Services that MUST have caps. Anything under these names in any
# compose file is checked. Overrides (scout-ghcr, hub-ghcr) also carry
# caps so the operator deploy path has them.
REQUIRED_SERVICE_NAMES: frozenset[str] = frozenset(
    {
        "hub",
        "scout",
        "beaconry-hub",
        "beaconry-scout",
    },
)

# Short-lived services we don't gate — tests / mocks / probes.
EXEMPT_SERVICE_NAMES: frozenset[str] = frozenset(
    {
        "telegram-mock",
        "probe-target",
    },
)

REQUIRED_KEYS = ("mem_limit", "memswap_limit", "cpus")


def _load_compose(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("PyYAML not installed; BC-141 lint needs yaml")
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _check_service(path: Path, name: str, cfg: dict) -> list[str]:
    """Return a list of violation strings for a single service."""
    violations: list[str] = []
    for key in REQUIRED_KEYS:
        if key not in cfg:
            violations.append(f"{path.name} / {name}: missing `{key}`")

    mem = cfg.get("mem_limit")
    swap = cfg.get("memswap_limit")
    if mem is not None and swap is not None and mem != swap:
        violations.append(
            f"{path.name} / {name}: `memswap_limit` ({swap}) != `mem_limit` ({mem}); "
            "swap must be disabled (equal values)",
        )
    return violations


def check(repo_root: Path) -> list[str]:
    violations: list[str] = []
    poc = repo_root / "poc"
    for glob in COMPOSE_GLOBS:
        path = poc / glob
        if not path.exists():
            continue
        try:
            compose = _load_compose(path)
        except Exception as e:  # noqa: BLE001
            violations.append(f"{path.name}: unreadable ({e})")
            continue
        services = compose.get("services", {}) or {}
        for name, cfg in services.items():
            if name in EXEMPT_SERVICE_NAMES:
                continue
            if name not in REQUIRED_SERVICE_NAMES:
                continue
            violations.extend(_check_service(path, name, cfg or {}))
    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    violations = check(repo_root)
    if violations:
        print("✗ BC-141 compose memory-limit lint failed:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    print("✓ BC-141: all beaconry services in all compose files carry hard memory + CPU caps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
