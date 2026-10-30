"""Typed provider aliases — the compiler-enforced restart-loop trap guard.

The classic healthcheck trap: embed DB / Telegram / external deps in
`/livez` → dependency fails → container restarts → dependency still fails
→ restart loop. `memory/health-checks.md § The restart-loop trap` codifies
the memory rule. This module makes it a type error.

- `LivezProvider` returns `Literal["alive"]` — no payload, no checks
- `ReadyzProvider` returns `dict[str, str]` — one entry per local prereq
- `HealthzSummaryProvider` returns the public JSON summary body

`build_router` accepts positional typed arguments, so a caller who swaps
them accidentally (`build_router(livez_provider=check_db, ...)`) gets a
pyright error:
    Expected `LivezProvider`, got `Callable[[Path], str]`

The runtime shadow test `test_livez_and_readyz_have_distinct_signatures`
in `tests/unit/test_beaconry_health_trap.py` catches the specific
degenerate case where someone collapses the aliases to the same type.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

LivezProvider = Callable[[], Literal["alive"]]
"""/livez probe — must be dependency-free. Returns the literal 'alive'."""

ReadyzProvider = Callable[[], dict[str, str]]
"""/readyz probe — returns dict[check_name, verdict] where verdict is 'ok' or any failure string."""

HealthzSummaryProvider = Callable[[], dict[str, Any]]
"""/healthz body — the operator-facing JSON summary. {status, version, checks}."""
