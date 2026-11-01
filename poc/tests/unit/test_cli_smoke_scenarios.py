"""BC-044 — unit tests for the live-smoke CLI harness.

Covers:
    - CAP-T093: `make test-cli` live-smoke harness
    - CAP-B028: Operator confidence in CLI output after any PR
"""

from __future__ import annotations

import re

import pytest

from poc.scripts import cli_smoke as cs

# ── Parsers (unit-testable) ────────────────────────────────────────────────


def test_findings_body_row_counter_counts_only_data_rows() -> None:
    """BC-030 regression base: KEV/NET columns use ✓/—/🌐 lead chars; the
    row counter picks data rows by those glyphs, not by column header text."""
    stdout = (
        "KEV  NET  CVE                   Severity    CVSS   Host\n"
        "---  ---  --------------------  ----------  -----  ------\n"
        "✓    —    CVE-2026-X            Critical    9.8    h\n"
        "—    🌐    CVE-2026-Y            Critical    9.5    h\n"
    )
    assert cs.count_findings_body_rows(stdout) == 2


def test_audit_body_row_counter() -> None:
    """BC-042: audit rows begin with HH:MM:SS. Header + separator excluded."""
    stdout = (
        "TIME      AGE          EVENT                     CVE\n"
        "--------  -----------  ------------------------  ---\n"
        "09:35:54  36s ago      digest_throttled          —\n"
        "09:35:36  54s ago      scan_received             —\n"
        "08:02:25  26m ago      scan_received             —\n"
    )
    assert cs.count_audit_body_rows(stdout) == 3


def test_audit_body_row_counter_rejects_json_lines() -> None:
    """BC-042 regression: raw JSON must not be miscounted as data rows."""
    stdout = (
        '{"ts": 1793525906, "event": "scan_received", "host": "h"}\n'
        '{"ts": 1793525907, "event": "digest_throttled"}\n'
    )
    assert cs.count_audit_body_rows(stdout) == 0


def test_audit_has_header_detects_formatted_output() -> None:
    stdout = (
        "TIME      AGE          EVENT                     CVE\n"
        "--------  -----------  ------------------------  ---\n"
        "09:35:54  36s ago      scan_received             —\n"
    )
    assert cs.audit_has_header(stdout) is True


def test_audit_has_header_rejects_raw_json_only() -> None:
    """BC-042 regression guard — raw JSON means the formatter path broke."""
    stdout = '{"ts": 1793525906, "event": "scan_received", "host": "h"}\n'
    assert cs.audit_has_header(stdout) is False


# ── Scenario base + registry ───────────────────────────────────────────────


def test_scenario_base_is_abstract() -> None:
    with pytest.raises(TypeError):
        cs.CliScenario()  # type: ignore[abstract]


def test_registry_has_ten_scenarios() -> None:
    assert len(cs.SCENARIOS) == 10
    orders = sorted(s.order for s in cs.SCENARIOS)
    assert orders == list(range(1, 11))
    names = [s.name for s in cs.SCENARIOS]
    assert len(set(names)) == len(names)


def test_registry_includes_bc042_regression_guards() -> None:
    """BC-042's two close-gap bugs must stay represented."""
    names = {s.name for s in cs.SCENARIOS}
    assert "audit_bad_since_exits_cleanly" in names
    assert "audit_follow_formats_output" in names


def test_registry_includes_bc030_regression_guard() -> None:
    """BC-030's `notifiers/` Dockerfile COPY must stay represented."""
    names = {s.name for s in cs.SCENARIOS}
    assert "hub_notifiers_importable" in names


# ── Scenario coverage — every operator-facing make target has a scenario ───


# Marker comments in `Makefile` delimit the authoritative operator-CLI set.
# Autodetecting from this source means adding a new CLI only requires updating
# the Makefile help once — the lint picks it up for free.
_MAKEFILE_MARKER_BEGIN = "# BC-044 BEGIN OPERATOR_CLI_TARGETS"
_MAKEFILE_MARKER_END = "# BC-044 END OPERATOR_CLI_TARGETS"
_MAKE_HELP_TARGET_RE = re.compile(r"make\s+([a-z][a-z0-9-]*)")


def _operator_targets_from_makefile() -> set[str]:
    """Parse `Makefile` for `make <target>` lines between the BC-044 markers.

    Convention: operator-facing CLIs land in the "Operator CLIs:" help
    section between the two markers. Lint + harness targets live outside.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    text = (repo_root / "Makefile").read_text()
    try:
        start = text.index(_MAKEFILE_MARKER_BEGIN)
        end = text.index(_MAKEFILE_MARKER_END, start)
    except ValueError as exc:
        raise AssertionError(
            f"Makefile missing BC-044 markers ({_MAKEFILE_MARKER_BEGIN} / "
            f"{_MAKEFILE_MARKER_END}). Wrap the Operator CLIs help lines "
            "between them so the lint can autodetect the set."
        ) from exc
    body = text[start:end]
    return set(_MAKE_HELP_TARGET_RE.findall(body))


def _targets_covered_by_registry() -> set[str]:
    """Introspect each scenario's source for `run_make("<target>", ...)` calls."""
    import inspect

    covered: set[str] = set()
    pattern = re.compile(r"""run_make\(\s*["']([a-z-]+)["']""")
    for scenario in cs.SCENARIOS:
        try:
            source = inspect.getsource(scenario.__class__)
        except (OSError, TypeError):
            continue
        covered.update(pattern.findall(source))
    return covered


def test_every_operator_target_has_at_least_one_scenario() -> None:
    """Every `make <target>` in the Makefile's `# BC-044 BEGIN OPERATOR_CLI_TARGETS`
    block must be exercised by at least one `CliScenario`. If a new operator
    CLI lands in the help section without a scenario, this fails + names the gap.

    Also catches the inverse: if a scenario calls `run_make("widget")` but
    "widget" isn't inside the Makefile markers, either promote it to the
    operator set OR remove the stale scenario."""
    expected = _operator_targets_from_makefile()
    assert expected, "no targets found between BC-044 markers — did someone remove them?"
    covered = _targets_covered_by_registry()

    missing = expected - covered
    assert not missing, (
        f"operator targets without a scenario: {sorted(missing)}. "
        f"Add a `CliScenario` to `poc/scripts/cli_smoke.py` + register it in `SCENARIOS`."
    )

    extra = covered - expected
    assert not extra, (
        f"scenarios reference make targets outside the Makefile's "
        f"`# BC-044 BEGIN OPERATOR_CLI_TARGETS` block: {sorted(extra)}. "
        f"Either promote the target by moving its help line into the block, "
        f"or remove the scenario."
    )


def test_operator_targets_from_makefile_matches_known_set() -> None:
    """Sanity check: what the autodetect returns matches the operator mental
    model. If the Makefile section drifts, this test catches it even if
    scenarios still pass by coincidence."""
    expected = _operator_targets_from_makefile()
    # findings/status/scan/audit are the current four. If this fails on a
    # new BC, update the Makefile markers to match + consider whether the
    # new target needs its own scenario.
    assert expected == {"findings", "status", "scan", "audit"}, (
        f"operator-CLI set drifted — autodetected: {sorted(expected)}. "
        "Update this test when the Makefile markers legitimately change."
    )


# ── Assertion helper ───────────────────────────────────────────────────────


def test_assert_contains_passes_on_match() -> None:
    cs.assert_contains("hello world", "world")


def test_assert_contains_raises_with_context() -> None:
    with pytest.raises(AssertionError, match=r"\(column header missing"):
        cs.assert_contains("header: KEV", "NET", context="(column header missing: NET)")


# ── Stack-detection prerequisite ───────────────────────────────────────────


def test_stack_check_detects_missing_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    """`is_hub_running()` returns False when docker exits non-zero (container absent)."""
    import subprocess as _sp

    def _fake_run(*args: object, **kwargs: object):
        class _R:
            returncode = 1
            stdout = ""

        return _R()

    monkeypatch.setattr(_sp, "run", _fake_run)
    assert cs.is_hub_running() is False


def test_stack_check_handles_missing_docker_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as _sp

    def _fake_run(*args: object, **kwargs: object):
        raise FileNotFoundError("no docker")

    monkeypatch.setattr(_sp, "run", _fake_run)
    assert cs.is_hub_running() is False


def test_stack_check_true_when_docker_says_running(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as _sp

    def _fake_run(*args: object, **kwargs: object):
        class _R:
            returncode = 0
            stdout = "true\n"

        return _R()

    monkeypatch.setattr(_sp, "run", _fake_run)
    assert cs.is_hub_running() is True


# ── Opt-out via env ────────────────────────────────────────────────────────


def test_skip_env_bypasses_scenarios(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BEACONRY_SKIP_CLI_SMOKE", "1")
    rc = cs.main()
    assert rc == 0
    assert "skipped" in capsys.readouterr().out


def test_follow_scenario_asserts_formatted_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """BC-042 regression guard — the scenario rejects raw-JSON FOLLOW output."""
    import subprocess as _sp

    def _fake_run(*args: object, **kwargs: object):
        # Simulate a FOLLOW invocation returning raw JSON (the pre-close-gap bug).
        raise _sp.TimeoutExpired(cmd="make", timeout=4, output=b'{"ts": 1, "event": "x"}\n')

    monkeypatch.setattr(_sp, "run", _fake_run)
    scenario = cs.S06AuditFollowFormatsOutput()
    with pytest.raises(AssertionError, match="did not emit the TIME/EVENT header"):
        scenario.run()


def test_bad_since_scenario_asserts_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    """BC-042 regression guard — the scenario rejects Python tracebacks."""
    import subprocess as _sp

    def _fake_run(*args: object, **kwargs: object):
        class _R:
            returncode = 1
            stdout = ""
            stderr = 'Traceback (most recent call last):\n  File "<string>"...'

        return _R()

    monkeypatch.setattr(_sp, "run", _fake_run)
    scenario = cs.S05AuditBadSinceExitsCleanly()
    with pytest.raises(AssertionError, match="Python traceback"):
        scenario.run()
