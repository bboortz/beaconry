"""BC-011 close-gap — compose env propagation lint.

Catches the recurring class of bug: a service's Python code reads
`os.environ.get("BEACONRY_X")`, but `poc/docker-compose.yml` doesn't
list `BEACONRY_X` in that service's `environment:` block. The code
silently gets the empty-string default; the operator sets the env in
`.env` and sees no effect; nothing fails until a live tap session.

Rule: every `BEACONRY_*` env read in `poc/<svc>/*.py` must appear as
either `- BEACONRY_X=...` or `- BEACONRY_X` in `poc/docker-compose.yml`
under the matching service's `environment:` block.

Hooked into `make check`. Exits 0 clean, 1 on any violation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # fallback to a tiny regex scan when yaml isn't installed
    yaml = None  # type: ignore[assignment]

ENV_READ_RE = re.compile(r'os\.environ\.(?:get|__getitem__)\(\s*["\']([A-Z0-9_]+)["\']')
BEACONRY_PREFIX = "BEACONRY_"

# Test-mode env vars that intentionally don't need to be in the production
# docker-compose.yml — they're only read when a dev / BC-017 e2e test harness
# sets them explicitly (e.g. via docker-compose.test.yml).
_TEST_MODE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "BEACONRY_TEST_MODE",  # BC-017: enables /__test__/* endpoints
    }
)


def read_env_vars_in_service(service_dir: Path) -> set[str]:
    """Return the set of BEACONRY_* env vars read by any .py file under the service dir."""
    found: set[str] = set()
    for py in service_dir.rglob("*.py"):
        try:
            text = py.read_text()
        except OSError:
            continue
        for match in ENV_READ_RE.findall(text):
            if match.startswith(BEACONRY_PREFIX):
                found.add(match)
    return found


def _parse_compose_env_list_regex(compose_text: str, service_name: str) -> set[str]:
    """Regex fallback when PyYAML isn't available. Walks line-by-line."""
    out: set[str] = set()
    in_service = False
    in_env = False
    service_header = re.compile(r"^\s{2}" + re.escape(service_name) + r":\s*$")
    env_header = re.compile(r"^\s{4}environment:\s*$")
    other_header = re.compile(r"^\s{4}\w+:\s*$")
    env_item = re.compile(r"^\s{6}-\s*([A-Z0-9_]+)")
    for line in compose_text.splitlines():
        if service_header.match(line):
            in_service = True
            in_env = False
            continue
        if in_service and env_header.match(line):
            in_env = True
            continue
        if in_service and in_env and other_header.match(line):
            in_env = False
        if in_env:
            m = env_item.match(line)
            if m:
                out.add(m.group(1))
    return out


def propagated_env_vars_for_service(compose_file: Path, service_name: str) -> set[str]:
    """Return the set of env var names propagated to `service_name` in compose."""
    text = compose_file.read_text()
    if yaml is None:
        return {
            v
            for v in _parse_compose_env_list_regex(text, service_name)
            if v.startswith(BEACONRY_PREFIX)
        }
    data = yaml.safe_load(text)
    service = data.get("services", {}).get(service_name, {})
    env = service.get("environment", [])
    out: set[str] = set()
    if isinstance(env, list):
        for item in env:
            # List form: `- NAME=value` or `- NAME`
            if "=" in item:
                out.add(item.split("=", 1)[0].strip())
            else:
                out.add(item.strip())
    elif isinstance(env, dict):
        out.update(env.keys())
    return {v for v in out if v.startswith(BEACONRY_PREFIX)}


def check_service(poc_root: Path, service_name: str) -> list[str]:
    service_dir = poc_root / service_name
    if not service_dir.is_dir():
        return []
    needed = read_env_vars_in_service(service_dir)
    compose = poc_root / "docker-compose.yml"
    if not compose.exists():
        return []
    propagated = propagated_env_vars_for_service(compose, service_name)
    missing = (needed - propagated) - _TEST_MODE_ALLOWLIST
    if not missing:
        return []
    return [
        f"{service_name}: Python reads {sorted(missing)} via os.environ "
        f"but poc/docker-compose.yml does not propagate them to the container. "
        f"Add `- {next(iter(missing))}=${{{next(iter(missing))}:-}}` to the "
        f"`{service_name}.environment:` list."
    ]


def main() -> int:
    poc_root = Path(__file__).resolve().parents[1]
    compose = poc_root / "docker-compose.yml"
    if not compose.exists():
        print(f"verify-compose-env-propagation: no compose file at {compose}; skipping")
        return 0
    errors: list[str] = []
    # Only services that actually exist as directories under poc/.
    for service in ("hub", "scout"):
        errors.extend(check_service(poc_root, service))

    if errors:
        print("verify-compose-env-propagation: found missing env propagation:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print(
        f"✓ {len(['hub', 'scout'])} service(s) checked — every BEACONRY_* env read "
        "has a matching compose propagation entry"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
