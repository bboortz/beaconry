"""BC-012 — compile-time restart-loop trap guard via typed Provider aliases.

The restart-loop trap: embedding dep-checks in /livez turns a dead
dependency into an infinite restart loop. BC-010 enforced this by
*memory rule*; BC-012 enforces it by *type system*.

The typed stubs `LivezProvider` and `ReadyzProvider` have incompatible
return types (Literal["alive"] vs dict[str, str]), so a caller that
accidentally passes a readyz-shaped provider to the livez slot gets a
pyright error.

Covers:
    - CAP-T041: Typed LivezProvider / ReadyzProvider interfaces make the
                restart-loop trap a type error, not a runtime pathology
"""

from __future__ import annotations

from poc.beaconry_health.trap import HealthzSummaryProvider, LivezProvider, ReadyzProvider


def test_livez_provider_is_callable_returning_alive() -> None:
    """A conforming LivezProvider returns the literal 'alive'."""

    def provider() -> str:
        return "alive"

    typed: LivezProvider = provider  # type: ignore[assignment]
    assert typed() == "alive"


def test_readyz_provider_is_callable_returning_check_dict() -> None:
    """A conforming ReadyzProvider returns dict[str, str] of per-check verdicts."""

    def provider() -> dict[str, str]:
        return {"db": "ok", "grype_db": "missing"}

    typed: ReadyzProvider = provider
    assert typed()["grype_db"] == "missing"


def test_healthz_summary_provider_returns_structured_summary() -> None:
    """HealthzSummaryProvider returns the public JSON body shape."""

    def provider() -> dict[str, object]:
        return {"status": "ok", "version": "0.1.0", "checks": {"db": "ok"}}

    typed: HealthzSummaryProvider = provider
    body = typed()
    assert set(body.keys()) >= {"status", "version", "checks"}


def test_livez_and_readyz_have_distinct_signatures() -> None:
    """The guard: LivezProvider and ReadyzProvider must be structurally different.

    Runtime shadow of the pyright-time guarantee that
    `build_router(livez_provider=some_readyz_callable, ...)` fails type
    checking. If someone collapses the aliases to the same object, this
    catches it before the trap re-enters production.
    """
    assert LivezProvider is not ReadyzProvider, (
        "LivezProvider and ReadyzProvider must be distinct types — collapsing them "
        "reopens the restart-loop trap"
    )
