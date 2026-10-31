"""BC-039 — environment-variable documentation drift lint.

Every `os.environ.get("X")` or `os.environ["X"]` in `poc/<non-tests>/`
must appear as a row in `docs/VARIABLES.md`. Likewise, every row in
`docs/VARIABLES.md` must correspond to at least one code read.

Rationale: ~30 env variables shipped in two weeks; developer / operator
cannot audit configuration surface without greping four files. VARIABLES.md
becomes the one authoritative index; the lint keeps it honest.

Exit codes:
  0  clean — code reads ↔ VARIABLES.md match
  1  drift — undocumented reads or orphan doc rows
  2  usage error (bad path, missing docs file)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Match three ways we read env variables:
#   1. Direct — `os.environ.get("X")`, `os.environ["X"]`
#   2. `_int_env("X", default)` wrapper (BC-035 close-gap for empty-string
#      compose fallbacks).
#   3. Class-attribute `env_var = "X"` indirection — BC-035 adapters set a
#      class attr the base class resolves via `os.environ.get(self.env_var)`.
#      Treating the assignment as a read is honest: the string literal is
#      the only place the var name appears, so documentation must cover it.
ENV_READ_RE = re.compile(
    r"""
    (?:
        os\.environ(?:\.get\(|\[)        # os.environ.get(  OR  os.environ[
        | _int_env\(                     # _int_env(
        | env_var\s*=\s*                 # env_var = "X"  (adapter class attr)
    )
    \s*["']([A-Z0-9_]+)["']
    """,
    re.VERBOSE,
)

# This scanner's module docstring contains example read-syntax that would
# otherwise self-reference — skip the scanner source itself.
SELF_PATH_SUFFIX = "scripts/verify_variables_documented.py"

# Vars that exist only in tests (fixtures that exercise env-reading code paths).
# NOT secret: visible, auditable, extensible by the person adding a new test.
TEST_ONLY_ALLOWLIST: frozenset[str] = frozenset(
    {
        "BEACONRY_FOO",
        "BEACONRY_MISSING",
        "BEACONRY_X",
        "BEACONRY_TEST_X",
        "BEACONRY_HUB_HEALTHZ_URL",
        "BEACONRY_SCOUT_HEALTHZ_URL",
        "BEACONRY_PLANS_DIR",
        "BEACONRY_TESTS_ROOT",
    }
)

# System vars we don't own and don't document (PATH, HOME, TERM, …).
# Keep tight: anything beaconry-prefixed is documentation-required.
NON_BEACONRY_IGNORED: frozenset[str] = frozenset(
    {
        "PATH",
    }
)

# Where the Markdown table lives.
VARIABLES_MD_HEADER_MARKER = "<!-- BC-039 BEGIN VARIABLES TABLE -->"
VARIABLES_MD_FOOTER_MARKER = "<!-- BC-039 END VARIABLES TABLE -->"


def scan_file(path: Path) -> set[str]:
    """Return the set of env var names read in `path`."""
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return set()
    return set(ENV_READ_RE.findall(text))


def scan_source_tree(poc_root: Path) -> set[str]:
    """Walk `poc_root`, return env vars read in non-test .py files."""
    names: set[str] = set()
    for py in poc_root.rglob("*.py"):
        rel = py.relative_to(poc_root)
        parts = rel.parts
        if "tests" in parts or "__pycache__" in parts:
            continue
        # Skip the scanner's own docstring self-references.
        if str(rel).endswith(SELF_PATH_SUFFIX) or rel.name == "verify_variables_documented.py":
            continue
        names.update(scan_file(py))
    return names - NON_BEACONRY_IGNORED


def _extract_body(path: Path) -> str:
    """Return the text between the BC-039 markers (exclusive). Raises on missing."""
    text = path.read_text()
    try:
        start = text.index(VARIABLES_MD_HEADER_MARKER)
        end = text.index(VARIABLES_MD_FOOTER_MARKER, start)
    except ValueError as exc:
        raise ValueError(
            f"{path} missing BC-039 markers (`{VARIABLES_MD_HEADER_MARKER}` / "
            f"`{VARIABLES_MD_FOOTER_MARKER}`). Add them around the variables table."
        ) from exc
    return text[start:end]


# A valid variable name: uppercase + digits + underscores, starts with letter.
# Covers both beaconry's `BEACONRY_X` convention and the handful of standard
# shell-style names we also read (TELEGRAM_BOT_TOKEN, DATA_DIR, MIN_CVSS, etc.).
_VALID_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def parse_variables_md_rows(path: Path) -> dict[str, list[str]]:
    r"""Return `{var_name: [cell, cell, ...]}` for every documented row.

    Rule: the first `` `...` `` code span inside the BC-039 markers of a row
    is the var name. Row shape: ``| `VAR_NAME` | default | type | where | reason | origin |``.
    Cells are returned trimmed, in column order (default, type, where, reason, origin).
    """
    body = _extract_body(path)
    rows: dict[str, list[str]] = {}
    row_re = re.compile(r"^\|\s*`([A-Z0-9_]+)`\s*\|(.*)$", re.MULTILINE)
    for match in row_re.finditer(body):
        name = match.group(1)
        # Split on `|`, trim each cell, drop the trailing empty cell from the final `|`.
        cells = [c.strip() for c in match.group(2).split("|")]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        rows[name] = cells
    return rows


def find_malformed_rows(path: Path) -> list[str]:
    """Return human-readable violations for row-shaped lines whose first
    column either (a) isn't backtick-wrapped, or (b) is backticked but
    doesn't match `_VALID_NAME_RE`. These are rows the parser would skip
    silently; the lint surfaces them explicitly.

    Header-separator rows (`|---|`) and the actual header row
    (`| Variable | Default | ...`) are intentionally skipped.
    """
    body = _extract_body(path)
    violations: list[str] = []
    # Any line starting with | that looks like a data row (first cell non-trivial)
    row_candidate = re.compile(r"^\|\s*([^|]+?)\s*\|", re.MULTILINE)
    header_markers = {"variable", "---", ":--", ":---:"}  # table headers / separators
    for match in row_candidate.finditer(body):
        first_cell = match.group(1).strip()
        low = first_cell.lower()
        # Skip the obvious non-data rows.
        if any(low.startswith(h) for h in header_markers):
            continue
        if first_cell.startswith("`") and first_cell.endswith("`"):
            name = first_cell.strip("`")
            if not _VALID_NAME_RE.match(name):
                violations.append(
                    f"malformed row in `{path}`: `{first_cell}` is backtick-wrapped "
                    f"but not a valid variable name (expected `^[A-Z][A-Z0-9_]*$`)."
                )
        else:
            # Row has non-header content but no backtick-wrapped name.
            # Could be a continuation / wrapped-line of a legit row.
            # Only flag if the first cell contains uppercase letters (looks
            # like an attempt at a var name without the backticks).
            if re.search(r"[A-Z]{3,}", first_cell) and not first_cell.endswith(")"):
                violations.append(
                    f"malformed row in `{path}`: `{first_cell}` looks like a variable "
                    f"name but is not backtick-wrapped. The parser will skip it. "
                    f"Wrap in backticks: `` `{first_cell}` ``."
                )
    return violations


def parse_variables_md(path: Path) -> set[str]:
    """Backward-compat convenience — just the names."""
    return set(parse_variables_md_rows(path).keys())


# Placeholder markers emitted by the undocumented-template. If any of these
# survives in a row's cells, the row is a stub that must be filled in.
_PLACEHOLDER_MARKERS: frozenset[str] = frozenset(
    {"*(default)*", "*(type)*", "*(service)*", "*(where)*", "*(one-line purpose)*", "*(BC-NNN)*"}
)
# Minimum columns a well-formed row must have (default, type, where, reason, origin).
_MIN_COLUMNS = 5


def _emit_undocumented_template(var: str) -> str:
    return f"| `{var}` | *(default)* | *(type)* | *(service)* | *(one-line purpose)* | *(BC-NNN)* |"


def check(poc_root: Path, variables_md: Path) -> list[str]:
    """Return a list of human-readable violation lines. Empty = clean.

    Three failure modes:
      1. Code reads a variable with no row → undocumented.
      2. Row exists but no code reads it → orphan.
      3. Row exists but one of its cells is still a `*(placeholder)*` or
         the row is too short → incomplete.
    """
    reads = scan_source_tree(poc_root)
    reads = {v for v in reads if v not in TEST_ONLY_ALLOWLIST}

    rows = parse_variables_md_rows(variables_md)
    documented = set(rows.keys())

    undocumented = reads - documented
    orphans = documented - reads

    violations: list[str] = []
    # Malformed-row check first — a bad row is useful signal regardless of
    # the drift state (parser would silently skip it otherwise).
    violations.extend(find_malformed_rows(variables_md))
    for var in sorted(undocumented):
        violations.append(
            f"`{var}` is read in code but missing from `{variables_md}`.\n"
            f"      Add a row (template, fill in the placeholders):\n"
            f"        {_emit_undocumented_template(var)}"
        )
    for var in sorted(orphans):
        violations.append(
            f"`{var}` is listed in `{variables_md}` but no code "
            f"reads it. Either delete the row or add the test-only allowlist "
            f"entry in `poc/scripts/verify_variables_documented.py` with justification."
        )
    # Incomplete rows — row exists AND is read, but still has placeholder text
    # or is too short.
    for var in sorted(documented & reads):
        cells = rows[var]
        if len(cells) < _MIN_COLUMNS:
            violations.append(
                f"`{var}` row in `{variables_md}` has {len(cells)} column(s); "
                f"expected ≥{_MIN_COLUMNS} (default | type | where | reason | origin)."
            )
            continue
        for cell in cells:
            # Only flag EXACT template-field markers — legitimate prose like
            # `*(empty)*`, `*(grype default)*`, `*(the-reason)*` is kept.
            if cell in _PLACEHOLDER_MARKERS:
                violations.append(
                    f"`{var}` row in `{variables_md}` has an unfilled placeholder "
                    f"cell: `{cell}`. Replace it with the real value."
                )
                break
    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    poc_root = repo_root / "poc"
    variables_md = repo_root / "docs" / "VARIABLES.md"
    if not variables_md.exists():
        print(f"✗ {variables_md} missing — create it first.", file=sys.stderr)
        return 2
    if not poc_root.exists():
        print(f"✗ {poc_root} missing — are you in the repo root?", file=sys.stderr)
        return 2
    violations = check(poc_root, variables_md)
    if violations:
        print(f"verify-variables: {len(violations)} violation(s) in `{variables_md}`:")
        for v in violations:
            print(f"    ✗ {v}")
        return 1
    rows = parse_variables_md_rows(variables_md)
    print(f"✓ verify-variables: {len(rows)} variable(s) documented, no drift")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
