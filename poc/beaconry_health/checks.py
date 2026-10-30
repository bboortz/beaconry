"""Reusable probe primitives.

Each returns a short string verdict: `"ok"` on success, a specific failure
reason otherwise. The three primitives cover the common probe shapes we
need across beaconry services:

- `check_db(path)` — can we open a SQLite connection and run a trivial query?
- `check_binary(name)` — is this binary on PATH?
- `check_dir_populated(path)` — does this directory exist and contain anything?

These are generic enough that both hub (DB, grype binary, grype DB dir) and
future containers (e.g. BC-013 scout `/readyz` probing docker socket, BC-011
target waterfall probing target containers) can compose them.

**Rule**: a check function MAY read local state, MUST NOT touch external
networks or external services. External-service health is a `/healthz`
degradation signal, not a `/readyz` or `/livez` failure.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path


def _which(name: str) -> str | None:
    """Indirection so tests can monkeypatch without touching shutil globally."""
    return shutil.which(name)


def check_db(path: Path) -> str:
    """Return 'ok' if we can open and query the DB at `path`, else 'unavailable'."""
    try:
        con = sqlite3.connect(str(path))
        con.execute("SELECT 1").fetchone()
        con.close()
        return "ok"
    except (sqlite3.Error, OSError):
        return "unavailable"


def check_binary(name: str) -> str:
    """Return 'ok' if `name` is a binary on PATH, else 'missing'."""
    return "ok" if _which(name) else "missing"


def check_dir_populated(path: Path) -> str:
    """Return 'ok' if `path` is a directory with contents, 'missing' or 'empty' otherwise."""
    if not path.exists():
        return "missing"
    try:
        return "ok" if any(path.iterdir()) else "empty"
    except OSError:
        return "unavailable"
