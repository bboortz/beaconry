"""Beaconry shared health-check package.

One place for the four-endpoint convention from `memory/health-checks.md`.
Every beaconry container COPYs this package in its Dockerfile.

The restart-loop trap — "do not check external deps in /livez" — is enforced
by the typed `LivezProvider` vs `ReadyzProvider` aliases in `.trap`. Passing a
dep-checking provider to the `/livez` slot is a pyright error, not a runtime
pathology discovered at 03:00 when a token expires and the container
restart-loops.
"""

from .checks import check_binary, check_db, check_dir_populated
from .sentinels import SentinelWriter
from .trap import HealthzSummaryProvider, LivezProvider, ReadyzProvider
from .version import SOFTWARE_VERSION, read_version

# `build_router` requires FastAPI. Services that don't serve HTTP (e.g. scout,
# which uses the file-sentinel path) don't ship FastAPI in their requirements.txt.
# Gracefully degrade so `from beaconry_health import SentinelWriter` works for
# scout while `from beaconry_health import build_router` still works for hub.
try:
    from .endpoints import build_router  # noqa: F401 — re-export

    _HAS_ENDPOINTS = True
except ImportError:
    _HAS_ENDPOINTS = False

__all__ = [
    "SOFTWARE_VERSION",
    "HealthzSummaryProvider",
    "LivezProvider",
    "ReadyzProvider",
    "SentinelWriter",
    "check_binary",
    "check_db",
    "check_dir_populated",
    "read_version",
]
if _HAS_ENDPOINTS:
    __all__.append("build_router")
