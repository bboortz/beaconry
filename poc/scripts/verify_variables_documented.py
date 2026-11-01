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

# BC-042: Makefile-argument env vars read by CLI scripts (e.g. `SINCE=1h make audit`).
# These are per-invocation CLI arguments, not persistent operator config —
# documenting them in VARIABLES.md would mislead operators about what's
# runtime-tunable. Each entry is read by exactly one CLI script.
CLI_ONLY_ALLOWLIST: frozenset[str] = frozenset(
    {
        "LAST",  # make audit — number of events to show
        "SINCE",  # make audit — time-window start
        "UNTIL",  # make audit — time-window end
        "EVENT",  # make audit — event-type filter
        "CVE",  # make audit — CVE id filter
        "HOST",  # make audit — host filter
        "FINDING",  # make audit — finding_id filter
        "COUNT",  # make audit — summary mode flag
        "FOLLOW",  # make audit — tail -f mode flag
        "STATUS",  # make findings — status filter
    }
)

# System vars we don't own and don't document (PATH, HOME, TERM, …).
# Keep tight: anything beaconry-prefixed is documentation-required.
NON_BEACONRY_IGNORED: frozenset[str] = frozenset(
    {
        "PATH",
        # BC-055: FreeDesktop var, not beaconry's to document.
        "XDG_CONFIG_HOME",
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

# BC-040: variables that intentionally do NOT appear in `.env.example`.
# Documented in VARIABLES.md (so they stay discoverable) but excluded from
# the operator-facing starter file. Each entry needs a one-line reason.
ENV_EXAMPLE_DEV_ONLY_ALLOWLIST: dict[str, str] = {
    "BEACONRY_TEST_MODE": "DO NOT SET IN PROD — exposes destructive /test/* routes",
    "BEACONRY_SCENARIO_HUB_URL": "scenario harness; only set by poc/scenarios/run.sh",
    "BEACONRY_SCENARIO_DATA_DIR": "scenario harness; only set by poc/scenarios/run.sh",
    # BC-055: CLI config path override; default is $XDG_CONFIG_HOME/beaconry/cli.yml.
    # Server-side .env.example is for operators configuring the hub + scout
    # host, not the laptop CLI, so this stays out of the starter template.
    "BEACONRY_CLI_CONFIG": "CLI-side path override; set on the laptop, not hub host",
}

# Where .env.example and docker-compose.yml live, relative to the repo root.
ENV_EXAMPLE_RELPATH = Path("poc") / ".env.example"
COMPOSE_FILE_RELPATH = Path("poc") / "docker-compose.yml"

# Services whose `environment:` block we scan for VARIABLES.md coverage.
# Keep tight — telegram-mock + sidecars might reference vars we don't own.
COMPOSE_SERVICES_TO_SCAN: tuple[str, ...] = ("hub", "scout")


def _emit_undocumented_template(var: str) -> str:
    return f"| `{var}` | *(default)* | *(type)* | *(service)* | *(one-line purpose)* | *(BC-NNN)* |"


# ── BC-040: .env.example + docker-compose.yml parsers ──────────────────────


_ENV_LINE_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*(?:#.*)?$")
_ANGLE_PLACEHOLDER_RE = re.compile(r"^<[^>]+>$")


def parse_env_example(path: Path) -> dict[str, str]:
    """Return `{VAR: value}` from a .env.example-shaped file.

    Rules:
      - Lines starting with `#` are comments and skipped.
      - Blank lines skipped.
      - Trailing `# ...` comments on a line are stripped from the value.
      - Values are returned as the raw literal after `=`, trimmed.
    """
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw_line in path.read_text().splitlines():
        line = raw_line.rstrip("\n")
        # Strip leading whitespace, skip comments + blank.
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _ENV_LINE_RE.match(stripped)
        if not m:
            continue
        name, value = m.group(1), m.group(2)
        out[name] = value.strip()
    return out


_COMPOSE_ENV_LINE_RE = re.compile(r"^\s*-\s*([A-Z][A-Z0-9_]*)\s*(?:=|$)")


def parse_compose_environment(
    compose_path: Path, services: tuple[str, ...] = COMPOSE_SERVICES_TO_SCAN
) -> dict[str, set[str]]:
    """Return `{service: {VAR, ...}}` for each service's `environment:` list.

    Regex-based: avoids a yaml dep (same reasoning as BC-011's parser).
    Accepts both `- VAR=...` and bare `- VAR` forms. Comments ignored.
    """
    result: dict[str, set[str]] = {svc: set() for svc in services}
    if not compose_path.exists():
        return result

    lines = compose_path.read_text().splitlines()
    current_service: str | None = None
    in_environment = False
    current_indent: int | None = None

    for raw in lines:
        line = raw.rstrip()
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(stripped)

        # Service header: `  <name>:` at a known indent.
        for svc in services:
            if stripped == f"{svc}:" or stripped.startswith(f"{svc}:"):
                current_service = svc
                in_environment = False
                current_indent = indent
                break
        else:
            # Not a service header line. Detect `environment:` inside the
            # current service's block, then collect vars until indent returns
            # to the service level.
            if current_service and current_indent is not None and indent > current_indent:
                if stripped.startswith("environment:"):
                    in_environment = True
                    continue
                if in_environment:
                    # End of environment block = another key at same indent as
                    # environment, i.e. not a `-` list item.
                    if not stripped.startswith("-"):
                        in_environment = False
                        continue
                    m = _COMPOSE_ENV_LINE_RE.match(stripped)
                    if m:
                        result[current_service].add(m.group(1))
            else:
                # Returned to or above service-header indent → service changed.
                current_service = None
                in_environment = False
                current_indent = None
    return result


def _normalize_default(raw: str) -> str:
    """Extract the comparable literal from a VARIABLES.md default cell.

    Rules:
      - Empty cell → "" (prose, skip equality).
      - `*(…)*` italics (e.g. `*(empty)*`) → "" (prose).
      - If the cell starts with a `` `backticked literal` `` — extract the
        contents of the FIRST backticked span. Example: `` `604800` (7 d) ``
        → `604800`. This is the authoritative shape going forward.
      - Otherwise, raw prose (e.g. `CISA upstream`, `uname().nodename`,
        `derived from BEACONRY_MOCK`) → "" (prose, skip equality).

    The design intent: the ONLY way to express a concrete literal that the
    lint compares to `.env.example` is to backtick-wrap it. Everything else
    is prose and is skipped — `.env.example` can show whatever is useful to
    the operator (`<nodename>`, empty, a derived marker).
    """
    s = raw.strip()
    if not s:
        return ""
    # Italics prose like `*(empty)*`.
    if s.startswith("*(") and s.endswith(")*"):
        return ""
    # Angle-bracket prose like `<CISA upstream>`.
    if s.startswith("<") and s.endswith(">"):
        return ""
    # First backticked span wins — strict literal.
    m = re.match(r"^`([^`]+)`", s)
    if m:
        return m.group(1).strip()
    # No backticks → prose; skip the equality check.
    return ""


def _is_angle_placeholder(value: str) -> bool:
    """`<from @BotFather>` → True. Operator must fill in; no equality check."""
    return bool(_ANGLE_PLACEHOLDER_RE.match(value))


def check(
    poc_root: Path,
    variables_md: Path,
    *,
    env_example: Path | None = None,
    compose_file: Path | None = None,
) -> list[str]:
    """Return a list of human-readable violation lines. Empty = clean.

    BC-039 failure modes:
      1. Code reads a variable with no row → undocumented.
      2. Row exists but no code reads it → orphan.
      3. Row exists but one of its cells is still a `*(placeholder)*` or
         the row is too short → incomplete.
      4. Row has a non-conforming variable name (not `^[A-Z][A-Z0-9_]*$`).

    BC-040 failure modes (opt-in via env_example / compose_file):
      5. VARIABLES.md row missing from `.env.example` (unless dev-only-allowlisted).
      6. `.env.example` line not in VARIABLES.md.
      7. compose `environment:` entry not in VARIABLES.md.
      8. Default literal mismatch between VARIABLES.md and `.env.example`.
    """
    reads = scan_source_tree(poc_root)
    reads = {v for v in reads if v not in TEST_ONLY_ALLOWLIST and v not in CLI_ONLY_ALLOWLIST}

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

    # BC-040 — three-way sync checks (only if the extra paths are provided).
    if env_example is not None:
        violations.extend(_check_env_example(rows, env_example))
    if compose_file is not None:
        violations.extend(_check_compose_env(rows, compose_file))
    return violations


def _check_env_example(rows: dict[str, list[str]], env_example: Path) -> list[str]:
    """BC-040 checks 5, 6, 8 against `.env.example`."""
    violations: list[str] = []
    env_values = parse_env_example(env_example)

    documented = set(rows.keys())
    env_vars = set(env_values.keys())

    # Rule 5: every VARIABLES.md row should appear in .env.example (minus allowlist).
    for var in sorted(documented - env_vars - set(ENV_EXAMPLE_DEV_ONLY_ALLOWLIST)):
        default_cell = rows[var][0] if rows[var] else ""
        suggested = _normalize_default(default_cell) or ""
        violations.append(
            f"`{var}` is in VARIABLES.md but missing from `{env_example}`. "
            f"Add a line (or allowlist in ENV_EXAMPLE_DEV_ONLY_ALLOWLIST):\n"
            f"        {var}={suggested}             # {default_cell} | <description>"
        )

    # Rule 6: every .env.example line must be in VARIABLES.md.
    for var in sorted(env_vars - documented):
        violations.append(
            f"`{var}` is in `{env_example}` but no VARIABLES.md row exists. "
            f"Document it in VARIABLES.md first, then set the default in .env.example."
        )

    # Rule 8: default-literal mismatch.
    for var in sorted(documented & env_vars):
        md_default_cell = rows[var][0] if rows[var] else ""
        md_default = _normalize_default(md_default_cell)
        env_value = env_values[var]
        # Skip if operator-filled placeholder.
        if _is_angle_placeholder(env_value):
            continue
        # Skip if VARIABLES.md default is prose (we extracted "").
        if md_default == "":
            continue
        # Both sides are concrete literals — require equality.
        if md_default != env_value:
            violations.append(
                f"`{var}` default mismatch: VARIABLES.md says `{md_default_cell}`, "
                f"`{env_example}` has `{env_value}`. Align both to the same literal."
            )
    return violations


def _check_compose_env(rows: dict[str, list[str]], compose_file: Path) -> list[str]:
    """BC-040 check 7 against docker-compose.yml."""
    violations: list[str] = []
    documented = set(rows.keys())
    propagated = parse_compose_environment(compose_file)
    for svc, env_vars in propagated.items():
        for var in sorted(env_vars - documented):
            violations.append(
                f"`{var}` is propagated to `{svc}` in `{compose_file}` but no "
                f"VARIABLES.md row exists. Document it in VARIABLES.md first."
            )
    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    poc_root = repo_root / "poc"
    variables_md = repo_root / "docs" / "VARIABLES.md"
    env_example = repo_root / ENV_EXAMPLE_RELPATH
    compose_file = repo_root / COMPOSE_FILE_RELPATH
    if not variables_md.exists():
        print(f"✗ {variables_md} missing — create it first.", file=sys.stderr)
        return 2
    if not poc_root.exists():
        print(f"✗ {poc_root} missing — are you in the repo root?", file=sys.stderr)
        return 2
    violations = check(
        poc_root,
        variables_md,
        env_example=env_example if env_example.exists() else None,
        compose_file=compose_file if compose_file.exists() else None,
    )
    if violations:
        print(f"verify-variables: {len(violations)} violation(s):")
        for v in violations:
            print(f"    ✗ {v}")
        return 1
    rows = parse_variables_md_rows(variables_md)
    print(
        f"✓ verify-variables: {len(rows)} variable(s) documented, "
        f"no drift across VARIABLES.md ↔ .env.example ↔ docker-compose.yml"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
