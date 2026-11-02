"""BC-070 — `beaconry` CLI entry point.

Covers:
    - CAP-T150: Unified `beaconry` CLI dispatcher + pyproject entry point
"""

from __future__ import annotations

import io
import tomllib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from poc.scripts import cli

# ── CAP-T150: dispatcher surface ────────────────────────────────────────────


def test_root_help_lists_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["--help"])
    out = capsys.readouterr().out
    assert rc == 0
    for sub in ("status", "findings", "audit", "scan", "set-status", "soak", "version"):
        assert sub in out


def test_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    """CAP-T150: friendlier default — `beaconry` with no args shows help instead
    of argparse's cryptic 'error: a subcommand is required'."""
    rc = cli.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "beaconry" in out
    assert "status" in out


def test_version_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip().startswith("beaconry ")
    version = out.strip().split()[1]
    # Match VERSION file.
    repo_root = Path(__file__).resolve().parents[3]
    assert version == (repo_root / "VERSION").read_text().strip()


def test_unknown_subcommand_fails(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["bogus-command"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown subcommand" in err
    assert "bogus-command" in err


def test_subcommand_dispatch_sets_argv_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T150: delegate's sys.argv[0] gets a human-readable prefix so its
    argparse / help output credits `beaconry findings` rather than internal
    paths. The delegate's own argv parsing stays untouched."""
    captured: list[list[str]] = []

    def _fake_main() -> int:
        import sys as _sys

        captured.append(list(_sys.argv))
        return 0

    import poc.scripts.findings as findings_mod

    monkeypatch.setattr(findings_mod, "main", _fake_main)
    cli.main(["findings", "--status", "open"])
    assert captured
    assert captured[0][0] == "beaconry findings"
    assert captured[0][1:] == ["--status", "open"]


def test_subcommand_dispatch_restores_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T150: whatever the delegate's argv manipulation, sys.argv is
    restored on exit — no cross-test pollution."""
    import sys

    original = ["pytest", "-k", "something"]
    monkeypatch.setattr(sys, "argv", list(original))

    import poc.scripts.status as status_mod

    monkeypatch.setattr(status_mod, "main", lambda: 0)
    cli.main(["status"])
    assert sys.argv == original


def test_subcommand_dispatch_propagates_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T150: delegate's return code bubbles up (non-zero = failure)."""
    import poc.scripts.soak as soak_mod

    monkeypatch.setattr(soak_mod, "main", lambda: 2)
    rc = cli.main(["soak"])
    assert rc == 2


# ── pyproject.toml entry point ──────────────────────────────────────────────


def test_pyproject_declares_entry_point() -> None:
    """CAP-T150: `pip install -e .` makes `beaconry` discoverable because
    `[project.scripts]` names it. If that drops, this test flags it."""
    repo_root = Path(__file__).resolve().parents[3]
    with (repo_root / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)
    scripts = pyproject["project"]["scripts"]
    assert scripts.get("beaconry") == "poc.scripts.cli:main"


def test_pyproject_version_matches_version_file() -> None:
    """CAP-T150 regression guard: a bump that forgets the pyproject literal
    would ship a wheel with the wrong metadata version. verify-version-sync
    doesn't check pyproject today — this covers it."""
    repo_root = Path(__file__).resolve().parents[3]
    with (repo_root / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)
    live = (repo_root / "VERSION").read_text().strip()
    assert pyproject["project"]["version"] == live


def test_pyproject_declares_runtime_dependencies() -> None:
    """CAP-T150: pyyaml + requests are needed by the CLI config loader
    and HTTP helpers. If they vanish from `dependencies`, a fresh
    `pip install -e .` would ImportError on first `beaconry status`."""
    repo_root = Path(__file__).resolve().parents[3]
    with (repo_root / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)
    deps = pyproject["project"].get("dependencies", [])
    assert any("pyyaml" in d.lower() for d in deps)
    assert any("requests" in d for d in deps)


def test_pyproject_build_system_present() -> None:
    """CAP-T150: setuptools backend must be declared for `pip install -e .`
    to resolve. Without it, pip falls back to a legacy path on Python 3.12+."""
    repo_root = Path(__file__).resolve().parents[3]
    with (repo_root / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)
    bs = pyproject.get("build-system", {})
    assert "setuptools" in str(bs.get("requires", []))
    assert bs.get("build-backend") == "setuptools.build_meta"


# ── Help works for every subcommand ─────────────────────────────────────────


@pytest.mark.parametrize("sub", ["findings", "audit", "set-status"])
def test_subcommand_helps_work(sub: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T150: a delegate --help call should propagate through the
    dispatcher — the delegate's argparse owns formatting."""
    # Capture the delegate's argparse exit (SystemExit on --help).
    import sys

    monkeypatch.setattr(sys, "argv", ["beaconry", sub, "--help"])

    buf_out, buf_err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        try:
            cli.main([sub, "--help"])
        except SystemExit as e:
            # argparse exits 0 on --help; some delegates may not use argparse.
            assert e.code in (0, None, 2)
    out_combined = buf_out.getvalue() + buf_err.getvalue()
    # Delegate at minimum identifies itself.
    assert sub in out_combined.lower() or "usage" in out_combined.lower()
