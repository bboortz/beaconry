"""BC-083 CAP-T171 — verify every FROM in beaconry's Dockerfiles is still
anonymously pullable from its registry.

Catches the failure class where a base-image provider pulls the rug: the
tag gets retagged, the repo goes private, the registry gets taken down.
BC-083's forge image is a self-owned ghcr.io repo — that risk is real.

Design:
- Enumerate every `FROM <image>:<tag>` across poc/**/Dockerfile*.
- Dedupe. Skip stage aliases (`FROM builder`).
- For each unique image, do an anonymous Docker Registry v2 flow:
  1. Request a bearer token: GET
     `<registry>/token?scope=repository:<repo>:pull&service=<service>`
  2. Fetch manifest with that token + OCI/Docker Accept headers.
  3. Treat token issuance failure or manifest 401/403/404/5xx as a failure.
- Cache successes in `.beaconry-cache/verify-base-images.json`
  (24h TTL) so the fast tier isn't network-bound every run.
- Opt-out: `BEACONRY_SKIP_BASE_IMAGES=1` for air-gapped CI.

This is a 'standing' preflight: BC-083's pre-flight inspection was a
one-shot plan-doc artifact (CAP-T170); this script runs forever.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_TTL_S = 24 * 60 * 60  # 24 hours

# Registry conventions:
#   - docker.io / library/* → auth.docker.io token service
#   - ghcr.io → ghcr.io/token
#   - gcr.io → gcr.io/v2/token
# We only need to support the ones beaconry actually uses.
_REGISTRY_TOKEN_URL: dict[str, str] = {
    "ghcr.io": "https://ghcr.io/token",
    "gcr.io": "https://gcr.io/v2/token",
    "docker.io": "https://auth.docker.io/token",
}
_REGISTRY_SERVICE: dict[str, str] = {
    "ghcr.io": "ghcr.io",
    "gcr.io": "gcr.io",
    "docker.io": "registry.docker.io",
}
# OCI + Docker legacy accept headers in priority order.
_MANIFEST_ACCEPT = (
    "application/vnd.oci.image.index.v1+json,"
    "application/vnd.oci.image.manifest.v1+json,"
    "application/vnd.docker.distribution.manifest.list.v2+json,"
    "application/vnd.docker.distribution.manifest.v2+json"
)

# Matches `FROM <image>:<tag> [AS <stage>]`. Captures the image ref.
_FROM_RE = re.compile(
    r"^\s*FROM\s+(?P<ref>[^\s]+)(?:\s+AS\s+\S+)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# Stage aliases (`FROM builder AS runtime`) are intra-build refs, not pulls.
# Heuristic: no registry prefix, no `/`, no `:` → treat as alias and skip.
_LIKELY_STAGE_ALIAS_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_\-]*$")


def enumerate_froms(repo_root: Path) -> set[str]:
    """Return the set of image refs pulled by any Dockerfile in the repo."""
    refs: set[str] = set()
    for dockerfile in repo_root.rglob("Dockerfile*"):
        # Ignore anything under .beaconry-cache, OLD, .venv, etc.
        if any(
            part in {".beaconry-cache", "OLD", ".venv", ".git", "node_modules"}
            for part in dockerfile.parts
        ):
            continue
        for match in _FROM_RE.finditer(dockerfile.read_text()):
            ref = match.group("ref").strip()
            if _LIKELY_STAGE_ALIAS_RE.match(ref):
                continue
            refs.add(ref)
    return refs


def _split_ref(ref: str) -> tuple[str, str, str]:
    """Split `ghcr.io/org/repo:tag` into (registry, repo, tag).

    Docker Hub shorthand (`python:3.13-slim`) normalizes to `docker.io` +
    `library/python` + `3.13-slim`.
    """
    # Tag split.
    if ":" in ref.rsplit("/", 1)[-1]:
        head, tag = ref.rsplit(":", 1)
    else:
        head, tag = ref, "latest"

    # Registry split: if the first path segment has a dot or colon, treat it
    # as a hostname. Otherwise it's Docker Hub shorthand.
    if "/" in head:
        maybe_registry, rest = head.split("/", 1)
        if "." in maybe_registry or ":" in maybe_registry:
            return maybe_registry, rest, tag
        return "docker.io", f"{maybe_registry}/{rest}", tag
    # Bare name (`python`, `nginx`) → Docker Hub `library/*`.
    return "docker.io", f"library/{head}", tag


def _probe_image_live(ref: str, timeout_s: int = 15) -> bool:
    """Do the live anonymous token + manifest HTTP flow. Return True iff
    both steps succeed."""
    registry, repo, tag = _split_ref(ref)
    token_url = _REGISTRY_TOKEN_URL.get(registry)
    service = _REGISTRY_SERVICE.get(registry)
    if not token_url or not service:
        # Unknown registry — fail loud rather than silently pass.
        return False

    # 1. Anonymous token.
    try:
        token_resp = urllib.request.urlopen(
            f"{token_url}?scope=repository:{repo}:pull&service={service}",
            timeout=timeout_s,
        )
        token = json.loads(token_resp.read()).get("token", "")
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return False
    if not token:
        return False

    # 2. Manifest fetch.
    manifest_url = f"https://{registry}/v2/{repo}/manifests/{tag}"
    req = urllib.request.Request(manifest_url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", _MANIFEST_ACCEPT)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError:
        return False
    except (urllib.error.URLError, TimeoutError):
        return False


def _load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(path: Path, data: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def check(
    refs: list[str],
    *,
    cache_path: Path,
    ttl_s: int = DEFAULT_TTL_S,
) -> list[str]:
    """Return a list of violation messages (empty = all pass). Writes
    successful probes to the cache for TTL reuse."""
    cache = _load_cache(cache_path)
    now = time.time()
    violations: list[str] = []

    for ref in refs:
        entry = cache.get(ref)
        if entry and entry.get("ok") and (now - entry.get("ts", 0)) < ttl_s:
            continue  # cache hit
        ok = _probe_image_live(ref)
        cache[ref] = {"ts": now, "ok": ok}
        if not ok:
            violations.append(
                f"`{ref}` is not anonymously pullable from its registry. "
                f"Either the repo went private, the tag was retagged, or the "
                f"registry is unreachable. Update the Dockerfile OR set "
                f"BEACONRY_SKIP_BASE_IMAGES=1 (dev-only emergency)."
            )

    _save_cache(cache_path, cache)
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="poc.scripts.verify_base_images",
        description=__doc__,
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Walk Dockerfiles starting from this directory.",
    )
    parser.add_argument(
        "--cache-path",
        type=Path,
        default=None,
        help="Where to read/write the probe cache (default: repo .beaconry-cache).",
    )
    args = parser.parse_args(argv)

    if os.environ.get("BEACONRY_SKIP_BASE_IMAGES"):
        print("↷ verify-base-images: skipped per BEACONRY_SKIP_BASE_IMAGES")
        return 0

    cache_path = args.cache_path or (args.repo_root / ".beaconry-cache" / "verify-base-images.json")
    refs = sorted(enumerate_froms(args.repo_root))

    if not refs:
        print("verify-base-images: no FROM refs found (nothing to check)")
        return 0

    violations = check(refs, cache_path=cache_path)
    if violations:
        print("verify-base-images: FROM refs failed anonymous probe:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1

    print(f"✓ verify-base-images: {len(refs)} FROM ref(s) pullable (24h cache)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
