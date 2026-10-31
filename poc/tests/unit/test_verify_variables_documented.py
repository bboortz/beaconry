"""BC-039 — env variable documentation drift lint.

Covers:
    - CAP-T084: `docs/VARIABLES.md` — single source of truth for runtime env variables
    - CAP-T085: `verify-variables` lint catches code↔doc drift bidirectionally
    - CAP-B023: Developer can review the entire variable surface without greping
"""

from __future__ import annotations

from pathlib import Path

import pytest

from poc.scripts import verify_variables_documented as vv

# ── parse / scan primitives ─────────────────────────────────────────────────


def test_scan_file_captures_os_environ_get(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text('x = os.environ.get("BEACONRY_ALPHA", "default")\n')
    assert vv.scan_file(f) == {"BEACONRY_ALPHA"}


def test_scan_file_captures_os_environ_subscript(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text('x = os.environ["BEACONRY_BETA"]\n')
    assert vv.scan_file(f) == {"BEACONRY_BETA"}


def test_scan_file_captures_int_env_wrapper(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text('x = _int_env("BEACONRY_GAMMA", 42)\n')
    assert vv.scan_file(f) == {"BEACONRY_GAMMA"}


def test_scan_file_captures_adapter_env_var_classattr(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text('class Adapter:\n    name = "x"\n    env_var = "BEACONRY_NOTIFIER_X_ENABLED"\n')
    assert vv.scan_file(f) == {"BEACONRY_NOTIFIER_X_ENABLED"}


def test_scan_file_captures_both_forms(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text('a = os.environ.get("ONE")\nb = os.environ["TWO"]\nc = _int_env("THREE", 0)\n')
    assert vv.scan_file(f) == {"ONE", "TWO", "THREE"}


def test_scan_source_tree_skips_tests(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "s.py").write_text('os.environ.get("REAL_VAR")\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text('os.environ.get("TEST_VAR")\n')
    assert vv.scan_source_tree(tmp_path) == {"REAL_VAR"}


def test_scan_source_tree_excludes_scanner_self(tmp_path: Path) -> None:
    """The scanner's own docstring contains example syntax; it must not
    self-trigger as a 'read'."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify_variables_documented.py").write_text(
        'os.environ.get("SELF_TRIGGER")\n'
    )
    assert vv.scan_source_tree(tmp_path) == set()


def test_parse_variables_md_returns_names(tmp_path: Path) -> None:
    md = tmp_path / "VARIABLES.md"
    md.write_text(
        "preamble\n"
        f"{vv.VARIABLES_MD_HEADER_MARKER}\n"
        "| `BEACONRY_FOO` | 1 | bool | poc/hub/hub.py | why | BC-XXX |\n"
        "| `BEACONRY_BAR` | 2 | int | poc/scout/scout.py | because | BC-YYY |\n"
        f"{vv.VARIABLES_MD_FOOTER_MARKER}\n"
    )
    assert vv.parse_variables_md(md) == {"BEACONRY_FOO", "BEACONRY_BAR"}


def test_parse_variables_md_missing_markers_raises(tmp_path: Path) -> None:
    md = tmp_path / "VARIABLES.md"
    md.write_text("nothing here\n")
    with pytest.raises(ValueError, match="missing BC-039 markers"):
        vv.parse_variables_md(md)


def test_parse_variables_md_rows_returns_cells(tmp_path: Path) -> None:
    md = tmp_path / "VARIABLES.md"
    md.write_text(
        f"{vv.VARIABLES_MD_HEADER_MARKER}\n"
        "| `BEACONRY_X` | 1 | bool | poc/hub/hub.py | reason text | BC-123 |\n"
        f"{vv.VARIABLES_MD_FOOTER_MARKER}\n"
    )
    rows = vv.parse_variables_md_rows(md)
    assert rows["BEACONRY_X"] == ["1", "bool", "poc/hub/hub.py", "reason text", "BC-123"]


# ── check() — the three failure modes ──────────────────────────────────────


def _make_md(tmp_path: Path, rows: list[str]) -> Path:
    md = tmp_path / "VARIABLES.md"
    md.write_text(
        f"{vv.VARIABLES_MD_HEADER_MARKER}\n"
        + "\n".join(rows)
        + f"\n{vv.VARIABLES_MD_FOOTER_MARKER}\n"
    )
    return md


def _make_src(tmp_path: Path, reads: dict[str, str] | None = None) -> Path:
    """Build a fake poc/ root with scripts/ containing one .py per read."""
    root = tmp_path / "poc"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for name, content in (reads or {}).items():
        (scripts / f"{name}.py").write_text(content)
    return root


def test_clean_state_passes(tmp_path: Path) -> None:
    md = _make_md(
        tmp_path,
        ["| `BEACONRY_ALPHA` | 1 | bool | poc/x.py | reason | BC-1 |"],
    )
    src = _make_src(tmp_path, {"x": 'os.environ.get("BEACONRY_ALPHA")\n'})
    assert vv.check(src, md) == []


def test_undocumented_var_fails_with_template(tmp_path: Path) -> None:
    md = _make_md(tmp_path, [])
    src = _make_src(tmp_path, {"x": 'os.environ.get("BEACONRY_NEW")\n'})
    violations = vv.check(src, md)
    assert len(violations) == 1
    assert "BEACONRY_NEW" in violations[0]
    assert "read in code but missing" in violations[0]
    # Template is copy-pasteable.
    assert "| `BEACONRY_NEW` |" in violations[0]


def test_orphan_doc_entry_fails(tmp_path: Path) -> None:
    md = _make_md(
        tmp_path,
        ["| `BEACONRY_DEAD` | 1 | bool | poc/x.py | reason | BC-1 |"],
    )
    src = _make_src(tmp_path, {})  # no reads
    violations = vv.check(src, md)
    assert len(violations) == 1
    assert "BEACONRY_DEAD" in violations[0]
    assert "no code" in violations[0]


def test_test_only_allowlist_suppresses_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A var that is on the test-only allowlist does NOT need a doc row."""
    monkeypatch.setattr(vv, "TEST_ONLY_ALLOWLIST", frozenset({"BEACONRY_FIXTURE"}))
    md = _make_md(tmp_path, [])
    src = _make_src(tmp_path, {"x": 'os.environ.get("BEACONRY_FIXTURE")\n'})
    assert vv.check(src, md) == []


def test_placeholder_row_fails(tmp_path: Path) -> None:
    """A row with exact template-field stubs (`*(default)*`, `*(type)*`, etc.) fails."""
    md = _make_md(
        tmp_path,
        ["| `BEACONRY_STUB` | *(default)* | *(type)* | poc/x.py | real reason | BC-1 |"],
    )
    src = _make_src(tmp_path, {"x": 'os.environ.get("BEACONRY_STUB")\n'})
    violations = vv.check(src, md)
    assert violations
    assert any("placeholder" in v for v in violations)


def test_prose_in_parens_italics_not_flagged(tmp_path: Path) -> None:
    """`*(empty)*` is legitimate prose for "no default" — must NOT be flagged."""
    md = _make_md(
        tmp_path,
        ["| `BEACONRY_ALPHA` | *(empty)* | string | poc/x.py | reason | BC-1 |"],
    )
    src = _make_src(tmp_path, {"x": 'os.environ.get("BEACONRY_ALPHA")\n'})
    assert vv.check(src, md) == []


# ── Malformed-row detection (name validity) ────────────────────────────────


def test_malformed_lowercase_name_fails(tmp_path: Path) -> None:
    """Backtick-wrapped but lowercase → not a valid env var name."""
    md = _make_md(
        tmp_path,
        ["| `beaconry_lower` | 1 | bool | poc/x.py | reason | BC-1 |"],
    )
    src = _make_src(tmp_path, {})
    violations = vv.check(src, md)
    assert any("not a valid variable name" in v for v in violations)


def test_malformed_hyphenated_name_fails(tmp_path: Path) -> None:
    """Backtick-wrapped with a hyphen → not a valid env var name."""
    md = _make_md(
        tmp_path,
        ["| `BEACONRY-DASH` | 1 | bool | poc/x.py | reason | BC-1 |"],
    )
    src = _make_src(tmp_path, {})
    violations = vv.check(src, md)
    assert any("not a valid variable name" in v for v in violations)


def test_malformed_no_backticks_fails(tmp_path: Path) -> None:
    """Looks-like-a-var name but no backticks → parser skips it. Lint flags."""
    md = _make_md(
        tmp_path,
        ["| BEACONRY_UNQUOTED | 1 | bool | poc/x.py | reason | BC-1 |"],
    )
    src = _make_src(tmp_path, {"x": 'os.environ.get("BEACONRY_UNQUOTED")\n'})
    violations = vv.check(src, md)
    assert any("not backtick-wrapped" in v for v in violations)


def test_header_separator_rows_skipped(tmp_path: Path) -> None:
    """The Markdown header row + `---` separator must not trigger malformed-row."""
    md = _make_md(
        tmp_path,
        [
            "| Variable | Default | Type | Where read | Reason | Origin |",
            "|---|---|---|---|---|---|",
            "| `BEACONRY_HEADER_TEST` | 1 | bool | poc/x.py | reason | BC-1 |",
        ],
    )
    src = _make_src(tmp_path, {"x": 'os.environ.get("BEACONRY_HEADER_TEST")\n'})
    assert vv.check(src, md) == []


def test_short_row_fails(tmp_path: Path) -> None:
    """Row with fewer than the minimum columns must fail."""
    md = _make_md(tmp_path, ["| `BEACONRY_SHORT` | 1 |"])
    src = _make_src(tmp_path, {"x": 'os.environ.get("BEACONRY_SHORT")\n'})
    violations = vv.check(src, md)
    assert any("column" in v for v in violations)


def test_both_undocumented_and_orphan_in_one_run(tmp_path: Path) -> None:
    """A single run surfaces every category of violation, not just the first."""
    md = _make_md(
        tmp_path,
        ["| `BEACONRY_ORPHAN` | 1 | bool | poc/x.py | reason | BC-1 |"],
    )
    src = _make_src(tmp_path, {"x": 'os.environ.get("BEACONRY_NEW")\n'})
    violations = vv.check(src, md)
    assert len(violations) == 2
    joined = " | ".join(violations)
    assert "BEACONRY_NEW" in joined
    assert "BEACONRY_ORPHAN" in joined


# ── Live-repo regression guard ─────────────────────────────────────────────


def test_shipped_repo_passes() -> None:
    """CAP-T085 regression guard: the actual repo state must stay clean."""
    repo_root = Path(__file__).resolve().parents[3]
    poc_root = repo_root / "poc"
    variables_md = repo_root / "docs" / "VARIABLES.md"
    violations = vv.check(poc_root, variables_md)
    assert violations == [], "VARIABLES.md drift:\n  " + "\n  ".join(violations)
