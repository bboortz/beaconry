"""FastAPI router factory — the four endpoints, one implementation.

`build_router(...)` returns an `APIRouter` with `/livez`, `/readyz`,
`/startupz`, `/healthz` mounted. Each container passes its own typed
providers; the router implements the convention (status codes, 503
reasons, JSON body shape) uniformly.

The restart-loop trap guard is in the signature itself: `LivezProvider`
and `ReadyzProvider` have incompatible return types, so a caller who
accidentally wires a readyz-shaped probe to the `livez_provider` slot
gets a pyright error at import time, not a container restart loop at
03:00.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Response

from .trap import HealthzSummaryProvider, LivezProvider, ReadyzProvider


def build_router(
    *,
    livez_provider: LivezProvider,
    readyz_providers: ReadyzProvider,
    startup_flag_getter: Callable[[], bool],
    healthz_summary_getter: HealthzSummaryProvider,
) -> APIRouter:
    """Build an APIRouter wiring the four endpoints to the given providers.

    Args:
        livez_provider: dependency-free probe; returns Literal["alive"]
        readyz_providers: returns dict[check_name, verdict]; verdict other
            than "ok" causes 503 with failing checks named
        startup_flag_getter: returns bool; False → /startupz 503
        healthz_summary_getter: returns full JSON summary for /healthz

    Returns:
        APIRouter — mount with `app.include_router(router)`
    """
    router = APIRouter()

    @router.get("/livez")
    async def livez() -> dict:
        return {"status": livez_provider()}

    @router.get("/readyz")
    async def readyz(response: Response) -> dict:
        checks = readyz_providers()
        failed = [name for name, verdict in checks.items() if verdict != "ok"]
        if failed:
            response.status_code = 503
            return {"status": "not_ready", "failed": failed, "checks": checks}
        return {"status": "ready", "checks": checks}

    @router.get("/startupz")
    async def startupz(response: Response) -> dict:
        if not startup_flag_getter():
            response.status_code = 503
            return {"status": "starting"}
        return {"status": "started"}

    @router.get("/healthz")
    async def healthz() -> dict:
        return healthz_summary_getter()

    return router
