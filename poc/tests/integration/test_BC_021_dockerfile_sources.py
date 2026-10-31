"""BC-021 integration test — Dockerfile sources lint.

Covers:
    - CAP-T061: Dockerfile-sources lint — every service-local .py is COPY'd by its Dockerfile
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

_ = (subprocess, sys)  # retained for the real-repo scenario below


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"))


class _Result:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run(poc_root: Path) -> _Result:
    """Invoke `check_service` directly against the fixture dirs. No subprocess —
    the lint is pure Python and testing it that way is both faster and more
    diagnostic. Returns an object with `.returncode / .stdout / .stderr`."""
    from poc.scripts.verify_dockerfile_sources import check_service

    errors: list[str] = []
    for dockerfile in sorted(poc_root.glob("*/Dockerfile")):
        errors.extend(check_service(dockerfile.parent, dockerfile))

    if errors:
        stderr = "verify-dockerfile-sources: found issues:\n" + "".join(f"  {e}\n" for e in errors)
        return _Result(1, "", stderr)
    return _Result(0, "", "")


# ── Scenario 1 ───────────────────────────────────────────────────────────────


def test_uncovered_py_file_fails(tmp_path: Path) -> None:
    _write(tmp_path / "svcA" / "foo.py", "")
    _write(tmp_path / "svcA" / "bar.py", "")
    _write(
        tmp_path / "svcA" / "Dockerfile",
        """
        FROM python:3.12-slim
        WORKDIR /app
        COPY svcA/foo.py ./
        """,
    )
    r = _run(tmp_path)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "bar.py" in (r.stdout + r.stderr)
    assert "svcA" in (r.stdout + r.stderr)


# ── Scenario 2 ───────────────────────────────────────────────────────────────


def test_all_covered_passes(tmp_path: Path) -> None:
    _write(tmp_path / "svcA" / "foo.py", "")
    _write(tmp_path / "svcA" / "bar.py", "")
    _write(
        tmp_path / "svcA" / "Dockerfile",
        """
        FROM python:3.12-slim
        WORKDIR /app
        COPY svcA/foo.py svcA/bar.py ./
        """,
    )
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


# ── Scenario 3 ───────────────────────────────────────────────────────────────


def test_init_and_test_files_skipped(tmp_path: Path) -> None:
    _write(tmp_path / "svcA" / "service.py", "")
    _write(tmp_path / "svcA" / "__init__.py", "")
    _write(tmp_path / "svcA" / "test_fixture.py", "")
    _write(
        tmp_path / "svcA" / "Dockerfile",
        """
        FROM python:3.12-slim
        WORKDIR /app
        COPY svcA/service.py ./
        """,
    )
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


# ── Scenario 4 ───────────────────────────────────────────────────────────────


def test_no_dockerfile_skipped(tmp_path: Path) -> None:
    """A service dir with .py files but no Dockerfile must not raise — shared-package dirs."""
    _write(tmp_path / "shared_pkg" / "lib.py", "")
    _write(tmp_path / "shared_pkg" / "other.py", "")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


# ── Scenario 5 ───────────────────────────────────────────────────────────────


def test_glob_copy_matches_all(tmp_path: Path) -> None:
    _write(tmp_path / "svcA" / "foo.py", "")
    _write(tmp_path / "svcA" / "bar.py", "")
    _write(tmp_path / "svcA" / "baz.py", "")
    _write(
        tmp_path / "svcA" / "Dockerfile",
        """
        FROM python:3.12-slim
        WORKDIR /app
        COPY svcA/*.py ./
        """,
    )
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_dir_copy_matches_all(tmp_path: Path) -> None:
    _write(tmp_path / "svcA" / "foo.py", "")
    _write(tmp_path / "svcA" / "bar.py", "")
    _write(
        tmp_path / "svcA" / "Dockerfile",
        """
        FROM python:3.12-slim
        WORKDIR /app
        COPY svcA/ ./
        """,
    )
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


# ── Scenario 6 ───────────────────────────────────────────────────────────────


def test_retroactive_real_repo_clean() -> None:
    """Run against the real poc/ — must be silent (BC-021's d855ddc fixed the hub Dockerfile)."""
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "-m", "poc.scripts.verify_dockerfile_sources"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(repo_root),
    )
    assert result.returncode == 0, (
        f"real repo must pass; got stdout={result.stdout} stderr={result.stderr}"
    )
