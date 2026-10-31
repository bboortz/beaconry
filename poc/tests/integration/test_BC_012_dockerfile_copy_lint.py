"""BC-012 — Dockerfile-COPY lint: catch forgotten `COPY beaconry_health/`.

Covers:
    - CAP-B016: Operator can add a new service to beaconry without a container-boot regression
    - CAP-T042: Dockerfile lint gate catches forgotten `COPY beaconry_health/` at merge time
"""

from __future__ import annotations

from pathlib import Path

from poc.scripts import verify_dockerfile_health_copy as lint

POC_ROOT = Path(__file__).resolve().parents[2]


def test_hub_dockerfile_passes_lint() -> None:
    """BC-012: hub Dockerfile has the required COPY line."""
    errors = lint.check_dockerfile(POC_ROOT / "hub" / "Dockerfile", POC_ROOT / "hub")
    assert errors == [], f"hub Dockerfile should pass BC-012 lint: {errors}"


def test_scout_dockerfile_passes_lint() -> None:
    errors = lint.check_dockerfile(POC_ROOT / "scout" / "Dockerfile", POC_ROOT / "scout")
    assert errors == [], f"scout Dockerfile should pass BC-012 lint: {errors}"


def test_lint_flags_fixture_dockerfile_missing_copy(tmp_path: Path) -> None:
    """Fixture: a service that imports beaconry_health but forgets the COPY."""
    service_dir = tmp_path / "ghost_service"
    service_dir.mkdir()
    (service_dir / "main.py").write_text(
        "from beaconry_health import build_router\n# forgot the COPY in Dockerfile\n"
    )
    dockerfile = service_dir / "Dockerfile"
    dockerfile.write_text(
        "FROM python:3.12-slim\nWORKDIR /app\nCOPY main.py .\nCMD ['python', 'main.py']\n"
    )

    errors = lint.check_dockerfile(dockerfile, service_dir)
    assert errors, "lint must flag forgotten COPY"
    assert any("beaconry_health" in e for e in errors), f"error must name beaconry_health: {errors}"


def test_lint_tolerates_service_that_doesnt_use_beaconry_health(tmp_path: Path) -> None:
    """A service that neither imports beaconry_health nor COPYs it is fine."""
    service_dir = tmp_path / "innocent_service"
    service_dir.mkdir()
    (service_dir / "main.py").write_text("print('hello')\n")
    dockerfile = service_dir / "Dockerfile"
    dockerfile.write_text(
        "FROM python:3.12-slim\nWORKDIR /app\nCOPY main.py .\nCMD ['python', 'main.py']\n"
    )
    errors = lint.check_dockerfile(dockerfile, service_dir)
    assert errors == [], f"service that doesn't use beaconry_health should pass: {errors}"
