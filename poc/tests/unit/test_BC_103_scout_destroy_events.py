"""BC-103 — Scout rescans on image/container removal events.

Covers:
    - CAP-T205: `_docker_events_stream` filter in poc/scout/scout.py
      includes `delete`, `destroy`, and `die` alongside the BC-093
      `pull`, `create`, `start` set so operator-triggered removals
      produce an immediate debounced rescan (same BC-093 path), not
      a SCAN_INTERVAL wait.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCOUT_SRC = REPO / "poc" / "scout" / "scout.py"

_FILTER_RE = re.compile(
    r"filters\s*=\s*\{[^}]*\"event\"\s*:\s*\[([^\]]+)\]",
    re.DOTALL,
)


def _events_in_filter() -> set[str]:
    m = _FILTER_RE.search(SCOUT_SRC.read_text())
    assert m, "could not locate _docker_events_stream filter"
    return {tok.strip().strip('"').strip("'") for tok in m.group(1).split(",") if tok.strip()}


def test_filter_includes_bc_093_creation_events() -> None:
    """BC-093 originals must survive."""
    events = _events_in_filter()
    for name in ("pull", "create", "start"):
        assert name in events, f"BC-093 event {name!r} missing from filter"


def test_filter_includes_bc_103_removal_events() -> None:
    """BC-103 adds removal-class events — fast rescan on `docker rm[i]`."""
    events = _events_in_filter()
    for name in ("delete", "destroy", "die"):
        assert name in events, f"BC-103 event {name!r} missing from filter"


def test_filter_uses_valid_event_names_only() -> None:
    """Every event name is in the known Docker vocabulary. Guards against
    typos that would silently match nothing.
    """
    allowed = {
        # inventory-shape events we care about
        "pull", "create", "start", "destroy", "die", "delete",
        # kept for forward-compat if a future BC adds them:
        "stop", "kill", "oom", "remove", "unpause", "pause",
    }  # fmt: skip
    events = _events_in_filter()
    for name in events:
        assert name in allowed, f"unknown docker event name in filter: {name!r}"
