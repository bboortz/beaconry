"""BC-011 close-gap — compose env propagation lint tests.

Covers:
    - CAP-T051: Compose env propagation lint catches env vars read by code but missing from docker-compose.yml
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from poc.scripts import verify_compose_env_propagation as lint


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_read_env_vars_finds_os_environ_get(tmp_path: Path) -> None:
    svc = tmp_path / "hub"
    _write(svc / "app.py", 'import os\nx = os.environ.get("BEACONRY_FOO", "")\n')
    found = lint.read_env_vars_in_service(svc)
    assert "BEACONRY_FOO" in found


def test_read_env_vars_skips_non_beaconry_prefix(tmp_path: Path) -> None:
    svc = tmp_path / "hub"
    _write(svc / "app.py", 'import os\nos.environ.get("PATH", "")\n')
    found = lint.read_env_vars_in_service(svc)
    assert found == set()


def test_check_service_passes_when_env_propagated(tmp_path: Path) -> None:
    poc = tmp_path / "poc"
    _write(poc / "hub" / "hub.py", 'import os\nos.environ.get("BEACONRY_X", "")\n')
    _write(
        poc / "docker-compose.yml",
        dedent(
            """\
            services:
              hub:
                environment:
                  - BEACONRY_X=${BEACONRY_X:-}
            """
        ),
    )
    errors = lint.check_service(poc, "hub")
    assert errors == []


def test_check_service_fails_on_missing_propagation(tmp_path: Path) -> None:
    poc = tmp_path / "poc"
    _write(poc / "hub" / "hub.py", 'import os\nos.environ.get("BEACONRY_MISSING", "")\n')
    _write(
        poc / "docker-compose.yml",
        dedent(
            """\
            services:
              hub:
                environment:
                  - OTHER=foo
            """
        ),
    )
    errors = lint.check_service(poc, "hub")
    assert errors, "lint must flag missing env propagation"
    assert "BEACONRY_MISSING" in errors[0]
    assert "BEACONRY_MISSING=" in errors[0], "error must include a copy-pasteable fix hint"


def test_check_real_poc_passes() -> None:
    """Live assertion: the real poc/docker-compose.yml propagates every BEACONRY_* read by hub + scout.

    This is the regression protection that BC-011's env-propagation close-gap landed:
    before the fix, BEACONRY_HEALTH_PROBES was missing from the hub.environment block
    while hub.py reads it. BEACONRY_FIX_ENABLED (BC-009) had the same silent bug.
    """
    poc = Path(__file__).resolve().parents[2]
    for service in ("hub", "scout"):
        errors = lint.check_service(poc, service)
        assert errors == [], f"{service}: {errors}"


def test_check_service_is_empty_when_no_env_reads(tmp_path: Path) -> None:
    poc = tmp_path / "poc"
    _write(poc / "scout" / "scout.py", "x = 1\n")
    _write(
        poc / "docker-compose.yml",
        dedent(
            """\
            services:
              scout:
                environment:
                  - FOO=bar
            """
        ),
    )
    errors = lint.check_service(poc, "scout")
    assert errors == []


def test_check_service_short_circuits_when_service_dir_missing(tmp_path: Path) -> None:
    # No service directory → the lint silently returns [] (nothing to check).
    poc = tmp_path / "poc"
    poc.mkdir()
    assert lint.check_service(poc, "hub") == []


def test_check_service_short_circuits_when_compose_missing(tmp_path: Path) -> None:
    # Service directory exists but no compose file → no violations to report.
    poc = tmp_path / "poc"
    _write(poc / "hub" / "hub.py", "x = 1\n")
    assert lint.check_service(poc, "hub") == []


def test_propagated_env_vars_handles_dict_form_environment(tmp_path: Path) -> None:
    # compose supports `environment: { NAME: value }` dict form alongside the
    # list form. The lint must recognise both.
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        dedent(
            """\
            services:
              hub:
                environment:
                  BEACONRY_A: x
                  BEACONRY_B: y
                  OTHER: skip
            """
        )
    )
    propagated = lint.propagated_env_vars_for_service(compose, "hub")
    assert propagated == {"BEACONRY_A", "BEACONRY_B"}


def test_propagated_env_vars_regex_fallback_when_yaml_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    # Simulate PyYAML not being installed: lint falls back to a regex scan.
    monkeypatch.setattr(lint, "yaml", None)
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        dedent(
            """\
            services:
              hub:
                environment:
                  - BEACONRY_FOO=${BEACONRY_FOO:-}
                  - BEACONRY_BAR
                  - OTHER=skip
                image: hub:latest
            """
        )
    )
    propagated = lint.propagated_env_vars_for_service(compose, "hub")
    # Only BEACONRY_-prefixed names survive the filter; "OTHER" is dropped.
    assert propagated == {"BEACONRY_FOO", "BEACONRY_BAR"}
