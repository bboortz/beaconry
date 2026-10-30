"""BC-012 close-gap — actual pyright enforcement of the restart-loop trap guard.

The runtime shadow test (`test_beaconry_health_trap.py::test_livez_and_readyz_have_distinct_signatures`)
only catches one degenerate case: `LivezProvider` and `ReadyzProvider`
collapsed to the same object. It does NOT catch the case where someone
redefines both aliases to the same type signature — pyright would silently
allow passing a dep-checking probe into the `/livez` slot again.

This test closes that gap by writing a fixture file whose code misuses
`build_router` (passing a `ReadyzProvider`-shaped callable to the
`livez_provider` slot) and asserting pyright reports it as a type error.

Covers:
    - CAP-T041: Typed LivezProvider / ReadyzProvider interfaces make the
                restart-loop trap a type error, not a runtime pathology
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]


pytestmark = pytest.mark.skipif(
    shutil.which("pyright") is None,
    reason="pyright not on PATH — the gate normally installs it; test skipped only in bare envs",
)


FIXTURE_CODE_MISUSE = dedent(
    '''\
    """Fixture: misuses build_router. Pyright MUST reject this.

    `bad_livez` returns dict[str, str] — the ReadyzProvider shape — but is
    wired to the `livez_provider` keyword slot which demands
    Callable[[], Literal["alive"]]. If pyright accepts this, the
    restart-loop trap has been quietly reopened and BC-012's compiler-as-
    guardrail claim is false.
    """

    from collections.abc import Callable
    from typing import Literal

    LivezProvider = Callable[[], Literal["alive"]]
    ReadyzProvider = Callable[[], dict[str, str]]


    def _hub_db_check() -> dict[str, str]:
        return {"db": "ok"}


    def build_router(
        *,
        livez_provider: LivezProvider,
        readyz_providers: ReadyzProvider,
    ) -> None:
        _ = (livez_provider, readyz_providers)


    # ↓ THIS IS THE MISUSE — _hub_db_check is a ReadyzProvider, not a LivezProvider.
    # Pyright must reject the keyword assignment on the next statement.
    build_router(
        livez_provider=_hub_db_check,
        readyz_providers=_hub_db_check,
    )
    '''
)


FIXTURE_CODE_CORRECT = dedent(
    '''\
    """Fixture: uses build_router correctly. Pyright MUST accept this."""

    from collections.abc import Callable
    from typing import Literal

    LivezProvider = Callable[[], Literal["alive"]]
    ReadyzProvider = Callable[[], dict[str, str]]


    def good_livez() -> Literal["alive"]:
        return "alive"


    def good_readyz() -> dict[str, str]:
        return {"db": "ok"}


    def build_router(
        *,
        livez_provider: LivezProvider,
        readyz_providers: ReadyzProvider,
    ) -> None:
        _ = (livez_provider, readyz_providers)


    build_router(
        livez_provider=good_livez,
        readyz_providers=good_readyz,
    )
    '''
)


def _run_pyright(target: Path) -> dict:
    """Return pyright's JSON report for `target`. Non-zero exit still returns JSON."""
    result = subprocess.run(
        ["pyright", "--outputjson", str(target)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"pyright did not emit JSON (stdout={result.stdout[:200]!r}, "
            f"stderr={result.stderr[:200]!r})"
        ) from e


def _summary_error_count(report: dict) -> int:
    return int(report.get("summary", {}).get("errorCount", 0))


def test_fixture_aliases_match_production_aliases() -> None:
    """Self-consistency: the fixture's inlined type aliases must match the real ones.

    If someone changes `LivezProvider` / `ReadyzProvider` in
    `poc/beaconry_health/trap.py` to new shapes without updating this fixture,
    we'd be testing a stale guarantee. This test is the drift alarm.
    """
    from poc.beaconry_health.trap import LivezProvider, ReadyzProvider

    assert "Literal['alive']" in str(LivezProvider) or 'Literal["alive"]' in str(LivezProvider), (
        f"production LivezProvider is {LivezProvider!r} — fixture expects Literal['alive']. "
        "Update both or revisit the trap guard semantics."
    )
    assert "dict[str, str]" in str(ReadyzProvider), (
        f"production ReadyzProvider is {ReadyzProvider!r} — fixture expects dict[str, str]"
    )


def test_pyright_rejects_readyz_shaped_fn_in_livez_slot(tmp_path: Path) -> None:
    """The main assertion: wiring a ReadyzProvider-shaped callable to the
    livez_provider slot must be a pyright error."""
    fixture = tmp_path / "trap_misuse_fixture.py"
    fixture.write_text(FIXTURE_CODE_MISUSE)

    report = _run_pyright(fixture)
    err_count = _summary_error_count(report)

    assert err_count >= 1, (
        f"pyright accepted the misuse — restart-loop trap guard has been "
        f"silently reopened. Report: {report}"
    )

    # The error must name the livez_provider slot or LivezProvider type —
    # otherwise it's unrelated noise.
    diagnostics = report.get("generalDiagnostics", [])
    messages = " ".join(d.get("message", "") for d in diagnostics)
    assert "livez_provider" in messages or "LivezProvider" in messages, (
        f"pyright complained but not about the livez slot — got: {messages[:400]}"
    )


def test_pyright_accepts_correct_usage(tmp_path: Path) -> None:
    """The negative control: with correctly-shaped providers, pyright passes clean.

    Without this test, we can't tell whether `test_pyright_rejects_readyz_shaped_fn_in_livez_slot`
    is passing because of the trap guard or because of some unrelated pyright error.
    """
    fixture = tmp_path / "trap_correct_fixture.py"
    fixture.write_text(FIXTURE_CODE_CORRECT)

    report = _run_pyright(fixture)
    err_count = _summary_error_count(report)

    assert err_count == 0, (
        f"pyright rejected a correct build_router usage — the fixture or the "
        f"type aliases have drifted. Report: {report}"
    )
