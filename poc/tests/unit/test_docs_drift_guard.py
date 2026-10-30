"""BC-016 — docs drift guard tests.

Covers:
    - CAP-T053: `make verify-docs-drift` catches help/README enumerations that no longer match code
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from poc.scripts import verify_docs_drift as guard


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text))


# ── Parsers ───────────────────────────────────────────────────────────────────


def test_parse_phony_single_line() -> None:
    text = ".PHONY: lint test check\n"
    assert guard.parse_phony(text) == {"lint", "test", "check"}


def test_parse_phony_multiple_declarations() -> None:
    text = ".PHONY: a b\n\n.PHONY: c d\n"
    assert guard.parse_phony(text) == {"a", "b", "c", "d"}


def test_extract_help_block_captures_help_recipe() -> None:
    text = '.PHONY: help lint\n\nhelp:\n\t@echo "line 1"\n\t@echo "line 2"\n\nlint:\n\truff check\n'
    block = guard.extract_help_block(text)
    assert "line 1" in block
    assert "ruff check" not in block


def test_extract_make_targets_from_help_text() -> None:
    text = '@echo "  make status   health table"\n@echo "  make findings  DB view"\n'
    assert guard.extract_make_targets_from_text(text) == {"status", "findings"}


def test_extract_status_enums_from_help_text() -> None:
    text = "STATUS=open|snoozed|accepted|fixed"
    assert guard.extract_status_enums_from_text(text) == {"open", "snoozed", "accepted", "fixed"}


def test_extract_beaconry_env_refs() -> None:
    text = "set BEACONRY_MOCK=1 and BEACONRY_FIX_ENABLED=0"
    assert guard.extract_beaconry_env_refs(text) == {"BEACONRY_MOCK", "BEACONRY_FIX_ENABLED"}


def test_parse_status_order_from_findings_py() -> None:
    text = 'STATUS_ORDER = (\n    "open",\n    "snoozed",\n    "accepted",\n    "fixed",\n)\n'
    assert guard.parse_status_order(text) == {"open", "snoozed", "accepted", "fixed"}


# ── Drift classes ─────────────────────────────────────────────────────────────


def test_clean_docs_pass(tmp_path: Path) -> None:
    _write(
        tmp_path / "Makefile",
        """\
        .PHONY: help status

        help:
        \t@echo "  make status   health table"
        \t@echo "  make findings STATUS=open|snoozed|accepted|fixed BEACONRY_MOCK=1"

        .PHONY: findings
        """,
    )
    _write(tmp_path / "README.md", "run `make status` to check.\n")
    _write(
        tmp_path / "poc" / "scripts" / "findings.py",
        'STATUS_ORDER = ("open", "snoozed", "accepted", "fixed")\n',
    )
    _write(tmp_path / "poc" / "hub" / "hub.py", 'import os\nos.environ.get("BEACONRY_MOCK", "")\n')

    violations = guard.scan(
        tmp_path / "Makefile",
        tmp_path / "README.md",
        tmp_path / "poc" / "scripts" / "findings.py",
        tmp_path / "poc",
    )
    assert violations == [], violations


def test_stray_status_value_caught(tmp_path: Path) -> None:
    _write(
        tmp_path / "Makefile",
        """\
        .PHONY: help findings

        help:
        \t@echo "  make findings STATUS=open|snoozed|unicorn"
        """,
    )
    _write(tmp_path / "README.md", "")
    _write(
        tmp_path / "poc" / "scripts" / "findings.py",
        'STATUS_ORDER = ("open", "snoozed")\n',
    )

    violations = guard.scan(
        tmp_path / "Makefile",
        tmp_path / "README.md",
        tmp_path / "poc" / "scripts" / "findings.py",
        tmp_path / "poc",
    )
    stray = [v for v in violations if v.kind == "stray-status-value"]
    assert stray, violations
    assert any("unicorn" in v.detail for v in stray)


def test_missing_make_target_caught(tmp_path: Path) -> None:
    _write(
        tmp_path / "Makefile",
        """\
        .PHONY: help status

        help:
        \t@echo "  make status"
        \t@echo "  make not-a-real-target"
        """,
    )
    _write(tmp_path / "README.md", "")
    _write(tmp_path / "poc" / "scripts" / "findings.py", "")

    violations = guard.scan(
        tmp_path / "Makefile",
        tmp_path / "README.md",
        tmp_path / "poc" / "scripts" / "findings.py",
        tmp_path / "poc",
    )
    unknown = [v for v in violations if v.kind == "unknown-make-target"]
    assert any("not-a-real-target" in v.detail for v in unknown)


def test_undocumented_phony_target_caught(tmp_path: Path) -> None:
    _write(
        tmp_path / "Makefile",
        """\
        .PHONY: help status undocumented-target

        help:
        \t@echo "  make status"
        """,
    )
    _write(tmp_path / "README.md", "")
    _write(tmp_path / "poc" / "scripts" / "findings.py", "")

    violations = guard.scan(
        tmp_path / "Makefile",
        tmp_path / "README.md",
        tmp_path / "poc" / "scripts" / "findings.py",
        tmp_path / "poc",
    )
    undocumented = [v for v in violations if v.kind == "undocumented-phony"]
    assert any("undocumented-target" in v.detail for v in undocumented)


def test_bogus_env_var_caught(tmp_path: Path) -> None:
    _write(
        tmp_path / "Makefile",
        """\
        .PHONY: help

        help:
        \t@echo "  BEACONRY_BOGUS=1 enables nothing"
        """,
    )
    _write(tmp_path / "README.md", "")
    _write(tmp_path / "poc" / "scripts" / "findings.py", "")
    _write(tmp_path / "poc" / "hub.py", "")

    violations = guard.scan(
        tmp_path / "Makefile",
        tmp_path / "README.md",
        tmp_path / "poc" / "scripts" / "findings.py",
        tmp_path / "poc",
    )
    bogus = [v for v in violations if v.kind == "bogus-env-var"]
    assert any("BEACONRY_BOGUS" in v.detail for v in bogus)


def test_incomplete_status_enum_caught(tmp_path: Path) -> None:
    _write(
        tmp_path / "Makefile",
        """\
        .PHONY: help findings

        help:
        \t@echo "  make findings STATUS=open|snoozed"
        """,
    )
    _write(tmp_path / "README.md", "")
    _write(
        tmp_path / "poc" / "scripts" / "findings.py",
        'STATUS_ORDER = ("open", "snoozed", "accepted", "fixed")\n',
    )

    violations = guard.scan(
        tmp_path / "Makefile",
        tmp_path / "README.md",
        tmp_path / "poc" / "scripts" / "findings.py",
        tmp_path / "poc",
    )
    incomplete = [v for v in violations if v.kind == "incomplete-status-enum"]
    assert incomplete, violations
    assert "accepted" in incomplete[0].detail or "fixed" in incomplete[0].detail


# ── Real-repo regression protection ───────────────────────────────────────────


def test_shipped_repo_passes_the_current_lint() -> None:
    """After the BC-016 drift fixes land, the real repo must pass clean."""
    repo = Path(__file__).resolve().parents[3]
    violations = guard.scan(
        repo / "Makefile",
        repo / "README.md",
        repo / "poc" / "scripts" / "findings.py",
        repo / "poc",
    )
    assert violations == [], "BC-016 drift found in live repo:\n" + "\n".join(
        f"  {guard.format_violation(v)}" for v in violations
    )
