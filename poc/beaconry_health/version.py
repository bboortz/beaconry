"""BC-062 — shared software-version loader.

Reads the single-source-of-truth `VERSION` file at repo root (host-side
dev) or `/app/VERSION` (container runtime, COPY'd by Dockerfile).

Fallback: `"0.0.0+unknown"` so a missing file doesn't crash import.
"""

from __future__ import annotations

from pathlib import Path

_FALLBACK = "0.0.0+unknown"


def read_version() -> str:
    """Return the beaconry software version string.

    Lookup order:
      1. Container path `/app/VERSION` (Dockerfile COPY)
      2. Repo-root path (dev / tests) — three parents up from this file
         (poc/beaconry_health/version.py → poc → repo)
    """
    container_path = Path("/app/VERSION")
    if container_path.exists():
        return _read_or_fallback(container_path)
    # beaconry_health lives at poc/beaconry_health/version.py; VERSION
    # lives at the repo root (two parents up from `poc`).
    repo_root = Path(__file__).resolve().parents[2]
    return _read_or_fallback(repo_root / "VERSION")


def _read_or_fallback(path: Path) -> str:
    try:
        return path.read_text().strip() or _FALLBACK
    except OSError:
        return _FALLBACK


SOFTWARE_VERSION = read_version()
