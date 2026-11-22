"""BC-147 — docs/ three-folder layout (specs / plan / stories).

Covers:
    - CAP-D001: every doc under docs/ belongs to exactly one of three
      classes (spec/plan/story) with a dedicated subdirectory. docs/operations/
      is a separate class for runbooks. verify-docs-layout lints the
      invariant on every make check.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DOCS = REPO / "docs"
SPECS = DOCS / "specs"

# The six docs that are moving (or already moved) into docs/specs/.
SPEC_FILES = (
    "CAPABILITIES.md",
    "LIFECYCLE.md",
    "METRICS.md",
    "VARIABLES.md",
    "INSTALL.md",
    "DESIGN.md",
)


# ── Gherkin: Specs live under docs/specs/ ────────────────────────────────────


def test_specs_dir_exists() -> None:
    assert SPECS.is_dir(), f"docs/specs/ must exist, got {SPECS}"


def test_all_six_specs_live_under_docs_specs() -> None:
    missing = [name for name in SPEC_FILES if not (SPECS / name).is_file()]
    assert not missing, (
        f"docs/specs/ is missing: {missing}. Post-BC-147 every spec in "
        f"{SPEC_FILES!r} must live under docs/specs/."
    )


def test_specs_template_exists() -> None:
    assert (SPECS / "_template.md").is_file(), (
        "docs/specs/_template.md must exist (mirror of docs/stories/_template.md)"
    )


def test_specs_index_exists() -> None:
    idx = SPECS / "INDEX.md"
    assert idx.is_file(), "docs/specs/INDEX.md must exist"
    body = idx.read_text(encoding="utf-8")
    for name in SPEC_FILES:
        assert name in body, f"docs/specs/INDEX.md must list {name}"


# ── Gherkin: No stray specs at docs/ top-level ──────────────────────────────


def test_no_stray_md_at_docs_toplevel() -> None:
    """Only README.md may live at docs/ top-level. Everything else belongs in
    docs/specs/, docs/plan/, docs/stories/, or docs/operations/."""
    stray = sorted(
        p.name
        for p in DOCS.iterdir()
        if p.is_file() and p.suffix == ".md" and p.name != "README.md"
    )
    assert not stray, (
        f"docs/ top-level must only hold README.md (if any). Stray files: {stray}. "
        f"Move them under docs/specs/ (or the appropriate class folder)."
    )


# ── Gherkin: DESIGN.md moved from repo root ─────────────────────────────────


def test_design_md_not_at_repo_root() -> None:
    assert not (REPO / "DESIGN.md").exists(), (
        "DESIGN.md must not live at the repo root post-BC-147; it moved to docs/specs/DESIGN.md"
    )


# ── Gherkin: Spec INDEX enumerates the specs ─────────────────────────────────


def test_specs_index_lists_every_spec() -> None:
    body = (SPECS / "INDEX.md").read_text(encoding="utf-8")
    for name in SPEC_FILES:
        assert name in body, f"INDEX.md missing row for {name}"


# ── Gherkin: Every in-repo reference resolves ───────────────────────────────


_TRACKED_SUFFIXES = {".md", ".py", ".yml", ".yaml", ".toml", ".sh", ".txt", ".cfg"}
# Also check unsuffixed project-root files individually.
_UNSUFFIXED_FILES = ("Makefile", "CLAUDE.md")


def _tracked_files() -> list[Path]:
    """Return every git-tracked file under the repo."""
    out = subprocess.check_output(["git", "ls-files"], cwd=REPO, encoding="utf-8").splitlines()
    files: list[Path] = []
    for raw in out:
        p = REPO / raw
        if not p.is_file():
            continue
        if p.suffix in _TRACKED_SUFFIXES or p.name in _UNSUFFIXED_FILES:
            files.append(p)
    return files


# Match path-looking strings naming any moved doc. Keep tight so CHANGELOG
# narrative like "the old docs/CAPABILITIES.md path" is caught too.
_MOVED_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?:\./|/)?"  # optional leading ./ or /
    r"(?:(?P<prefix>docs/))?"
    r"(?P<name>" + "|".join(re.escape(n) for n in SPEC_FILES) + r")"
    r"(?![A-Za-z0-9_])"
)

# Historical-prose directories whose contents LEGITIMATELY name old paths
# (plans + stories describe the state at the time they were written).
# Only the live-wired files participate in the drift check: code, Makefile,
# README.md, CLAUDE.md, memory/*.md, docs/specs/*, docs/operations/*.
_HISTORICAL_PREFIXES = ("docs/plan/", "docs/stories/")
_REFERENCE_CHECK_ALLOWLIST_EXACT = {
    "CHANGELOG.md",  # entries describe the move itself
    "poc/tests/integration/test_BC_147_docs_specs_layout.py",  # this file's own SPEC_FILES tuple
    "poc/scripts/memory_drift_bans.toml",  # lists the banned old paths by design
}


def _allowlisted(rel: str) -> bool:
    if rel in _REFERENCE_CHECK_ALLOWLIST_EXACT:
        return True
    return any(rel.startswith(prefix) for prefix in _HISTORICAL_PREFIXES)


def test_every_in_repo_reference_resolves() -> None:
    """Every path-looking reference to a moved spec in a live-wired file must
    resolve to the new `docs/specs/<name>` location.

    "Live-wired" = loaded or read at runtime (code, Makefile, README.md,
    CLAUDE.md, memory/*.md, docs/specs/, docs/operations/). Historical prose
    in docs/plan/* + docs/stories/* is NOT rewritten — those docs describe
    the state at the time they were written; see `_HISTORICAL_PREFIXES`.
    """
    offenders: list[str] = []
    for f in _tracked_files():
        rel = f.relative_to(REPO).as_posix()
        if _allowlisted(rel):
            continue
        body = f.read_text(encoding="utf-8", errors="replace")
        for m in _MOVED_PATH_RE.finditer(body):
            name = m.group("name")
            prefix = m.group("prefix") or ""
            full = f"{prefix}{name}"
            start = max(0, m.start() - 20)
            end = min(len(body), m.end() + 20)
            snippet = body[start:end].replace("\n", " ⏎ ")
            legacy = REPO / "docs" / name
            new = SPECS / name
            repo_root_design = REPO / name
            # 1. `docs/<name>` when the legacy file no longer exists: stale link.
            if prefix == "docs/" and not legacy.exists() and new.exists():
                offenders.append(
                    f"{rel}: '{full}' → should be 'docs/specs/{name}' (near: …{snippet}…)"
                )
                continue
            # 2. `docs/<name>` when the legacy file still exists on disk:
            #    the move hasn't happened yet.
            if prefix == "docs/" and legacy.exists():
                offenders.append(
                    f"{rel}: '{full}' resolves to the OLD path — the move hasn't happened"
                )
                continue
            # 3. Bare `DESIGN.md` at repo root still existing is a signal the
            #    move is incomplete.
            if prefix == "" and name == "DESIGN.md" and repo_root_design.exists():
                offenders.append(
                    f"{rel}: 'DESIGN.md' at repo root still exists (move not complete)"
                )
                continue
            # 4. Bare `<name>` mentions are prose (e.g. "update VARIABLES.md"
            #    in a docstring or test fixture). Not flagged. The stale-link
            #    signals that matter are (1) `docs/<name>` paths that no longer
            #    resolve, caught above, and (2) DESIGN.md still at repo root,
            #    caught in rule 3.
    assert not offenders, "stale references to moved docs:\n  " + "\n  ".join(offenders)


# ── Gherkin: Layout lint blocks regression ──────────────────────────────────


def test_verify_docs_layout_script_present() -> None:
    assert (REPO / "poc" / "scripts" / "verify_docs_layout.py").is_file(), (
        "poc/scripts/verify_docs_layout.py must exist"
    )


def test_verify_docs_layout_wired_into_make_check() -> None:
    text = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "verify-docs-layout" in text, "Makefile must declare a `verify-docs-layout` target"
    # Must appear in the `check:` dependency list.
    check_line = re.search(r"^check:\s*(.+)$", text, re.MULTILINE)
    assert check_line and "verify-docs-layout" in check_line.group(1), (
        "`check:` target must depend on verify-docs-layout"
    )


def test_verify_docs_layout_catches_stray_toplevel_md(tmp_path: Path) -> None:
    """Run the lint against a synthetic repo where a stray doc lives at
    docs/ top-level; it must flag the violation."""
    from poc.scripts import verify_docs_layout

    # Synthetic layout: a stray StrayDoc.md at docs/ top-level.
    (tmp_path / "docs" / "specs").mkdir(parents=True)
    (tmp_path / "docs" / "plan").mkdir()
    (tmp_path / "docs" / "stories").mkdir()
    (tmp_path / "docs" / "StrayDoc.md").write_text("stray\n", encoding="utf-8")
    violations = verify_docs_layout.check(tmp_path)
    assert violations, "verify_docs_layout must flag docs/StrayDoc.md as stray"
    assert any("StrayDoc" in v for v in violations)


def test_verify_docs_layout_passes_on_live_repo() -> None:
    """The live beaconry repo must pass verify_docs_layout.check at HEAD."""
    from poc.scripts import verify_docs_layout

    violations = verify_docs_layout.check(REPO)
    assert violations == [], f"verify_docs_layout failed at HEAD: {violations}"


def test_verify_docs_layout_handles_missing_docs(tmp_path: Path) -> None:
    """If docs/ is absent, the lint reports that single fact and stops."""
    from poc.scripts import verify_docs_layout

    violations = verify_docs_layout.check(tmp_path)
    assert violations and "docs/ missing" in violations[0]


def test_verify_docs_layout_flags_unexpected_subdir(tmp_path: Path) -> None:
    """A subdirectory under docs/ outside the legal set is flagged."""
    from poc.scripts import verify_docs_layout

    (tmp_path / "docs" / "specs").mkdir(parents=True)
    (tmp_path / "docs" / "specs" / "_template.md").write_text("", encoding="utf-8")
    (tmp_path / "docs" / "specs" / "INDEX.md").write_text("", encoding="utf-8")
    (tmp_path / "docs" / "unexpected").mkdir()
    violations = verify_docs_layout.check(tmp_path)
    assert any("unexpected" in v and "unexpected subdirectory" in v for v in violations)


def test_verify_docs_layout_flags_missing_specs_scaffold(tmp_path: Path) -> None:
    """docs/specs/ without _template.md or INDEX.md is flagged."""
    from poc.scripts import verify_docs_layout

    (tmp_path / "docs" / "specs").mkdir(parents=True)
    violations = verify_docs_layout.check(tmp_path)
    assert any("_template.md: missing" in v for v in violations)
    assert any("INDEX.md: missing" in v for v in violations)


def test_verify_docs_layout_flags_design_at_repo_root(tmp_path: Path) -> None:
    """DESIGN.md at repo root (post-BC-147) is flagged."""
    from poc.scripts import verify_docs_layout

    (tmp_path / "docs" / "specs").mkdir(parents=True)
    (tmp_path / "docs" / "specs" / "_template.md").write_text("", encoding="utf-8")
    (tmp_path / "docs" / "specs" / "INDEX.md").write_text("", encoding="utf-8")
    (tmp_path / "DESIGN.md").write_text("# moved me", encoding="utf-8")
    violations = verify_docs_layout.check(tmp_path)
    assert any("DESIGN.md: must live under docs/specs" in v for v in violations)


def test_verify_docs_layout_main_prints_and_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() emits a success line and returns 0 when clean."""
    from poc.scripts import verify_docs_layout

    (tmp_path / "docs" / "specs").mkdir(parents=True)
    (tmp_path / "docs" / "specs" / "_template.md").write_text("", encoding="utf-8")
    (tmp_path / "docs" / "specs" / "INDEX.md").write_text("", encoding="utf-8")
    monkeypatch.setattr(verify_docs_layout.Path, "resolve", lambda self: self, raising=False)
    # Easier: monkeypatch the check() entry to return clean.
    monkeypatch.setattr(verify_docs_layout, "check", lambda _root: [])
    rc = verify_docs_layout.main([])
    out = capsys.readouterr()
    assert rc == 0
    assert "ok" in out.out


def test_verify_docs_layout_main_returns_1_on_violation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() emits violations on stderr and returns 1 when the lint trips."""
    from poc.scripts import verify_docs_layout

    monkeypatch.setattr(
        verify_docs_layout, "check", lambda _root: ["docs/StrayDoc.md: strays at docs/ top-level."]
    )
    rc = verify_docs_layout.main([])
    err = capsys.readouterr().err
    assert rc == 1
    assert "StrayDoc.md" in err


# ── Gherkin: Memory + CLAUDE.md names the three folders ─────────────────────


def test_claude_md_and_memory_name_the_three_folders() -> None:
    for rel in ("CLAUDE.md", "memory/workflow.md"):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "docs/specs" in body, (
            f"{rel} must reference `docs/specs/` in its layout/process section"
        )


# ── Covers block self-consistency ───────────────────────────────────────────


def test_cap_d001_registered() -> None:
    caps = (SPECS / "CAPABILITIES.md").read_text(encoding="utf-8")
    assert "CAP-D001" in caps, "CAP-D001 must be registered in CAPABILITIES.md"


@pytest.mark.parametrize("name", list(SPEC_FILES))
def test_each_moved_spec_readable(name: str) -> None:
    """Every moved spec must be non-empty and readable."""
    f = SPECS / name
    assert f.is_file(), f"{f} missing"
    body = f.read_text(encoding="utf-8")
    assert body.strip(), f"{f} is empty"
