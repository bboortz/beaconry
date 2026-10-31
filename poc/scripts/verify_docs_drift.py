"""BC-016 — docs drift guard.

Cross-checks `Makefile` help block + `README.md` against the live code
for three enumeration classes:

1. `make <target>` strings in docs → must exist in `Makefile`'s `.PHONY`
2. `.PHONY` targets → must have at least one `make <target>` entry in `help:`
3. `STATUS=foo|bar|...` enums in docs → must match `poc/scripts/findings.py::STATUS_ORDER`
4. `BEACONRY_<NAME>` references in docs → must be read by at least one `os.environ.get(...)` call in `poc/`

Hooked into `make check` fast tier. Exits 0 clean, 1 with named violations.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

MAKE_TARGET_RE = re.compile(r"\bmake\s+([a-z][a-z0-9_-]*)\b")
# README references must be in inline code (`make foo`); plain prose like
# "GNU make convention" is not a target reference. The Makefile help block
# uses the looser MAKE_TARGET_RE since its content is always command-ish.
MAKE_TARGET_BACKTICK_RE = re.compile(r"`make\s+([a-z][a-z0-9_-]*)`")
STATUS_ENUM_RE = re.compile(r"STATUS=([a-z_][a-z0-9_]*(?:\|[a-z_][a-z0-9_]*)+)")
BEACONRY_ENV_RE = re.compile(r"\bBEACONRY_[A-Z0-9_]+\b")
PHONY_RE = re.compile(r"^\.PHONY:\s*(.+)$", re.MULTILINE)
HELP_BLOCK_RE = re.compile(r"^help:\n((?:\t.*\n)+)", re.MULTILINE)
STATUS_ORDER_RE = re.compile(
    r"STATUS_ORDER\s*=\s*\(([\s\S]+?)\)",
)
ENV_READ_RE = re.compile(r'os\.environ\.(?:get|__getitem__)\(\s*["\']([A-Z0-9_]+)["\']')


@dataclass(frozen=True)
class Violation:
    source: str  # "Makefile help" or "README.md"
    kind: str  # which drift class
    detail: str
    hint: str


def parse_phony(makefile_text: str) -> set[str]:
    out: set[str] = set()
    for m in PHONY_RE.finditer(makefile_text):
        out.update(m.group(1).split())
    return out


def extract_help_block(makefile_text: str) -> str:
    m = HELP_BLOCK_RE.search(makefile_text)
    return m.group(1) if m else ""


def extract_make_targets_from_text(text: str, *, require_backticks: bool = False) -> set[str]:
    """Return the set of targets referenced in prose text.

    `require_backticks=True` only matches `` `make foo` `` inline-code mentions,
    which is the safe mode for README (avoids false positives like "GNU make
    convention"). The Makefile help block uses the looser mode because its
    content is always command-shaped.
    """
    regex = MAKE_TARGET_BACKTICK_RE if require_backticks else MAKE_TARGET_RE
    return set(regex.findall(text))


def extract_status_enums_from_text(text: str) -> set[str]:
    """Return the set of STATUS values mentioned via `STATUS=a|b|c` in prose text."""
    out: set[str] = set()
    for match in STATUS_ENUM_RE.findall(text):
        out.update(match.split("|"))
    return out


def extract_beaconry_env_refs(text: str) -> set[str]:
    return set(BEACONRY_ENV_RE.findall(text))


def parse_status_order(findings_py_text: str) -> set[str]:
    m = STATUS_ORDER_RE.search(findings_py_text)
    if not m:
        return set()
    body = m.group(1)
    # Extract quoted strings
    return set(re.findall(r'["\']([a-z_][a-z0-9_]*)["\']', body))


def collect_env_reads(poc_root: Path) -> set[str]:
    """Return the set of BEACONRY_* env var names actually read by any poc/**.py file."""
    out: set[str] = set()
    for py in poc_root.rglob("*.py"):
        try:
            text = py.read_text()
        except OSError:
            continue
        for m in ENV_READ_RE.findall(text):
            if m.startswith("BEACONRY_"):
                out.add(m)
    return out


# Non-help-target entries in .PHONY that aren't commands operators invoke directly
# — these intentionally don't need help lines.
_INVISIBLE_PHONY_TARGETS: frozenset[str] = frozenset({"help"})


def scan(  # noqa: C901 — orchestrates 4 drift checks sequentially; splitting adds indirection without clarity
    makefile: Path,
    readme: Path,
    findings_py: Path,
    poc_root: Path,
) -> list[Violation]:
    violations: list[Violation] = []
    mf_text = makefile.read_text() if makefile.exists() else ""
    readme_text = readme.read_text() if readme.exists() else ""
    findings_text = findings_py.read_text() if findings_py.exists() else ""

    phony = parse_phony(mf_text)
    help_block = extract_help_block(mf_text)
    status_order = parse_status_order(findings_text)
    env_reads = collect_env_reads(poc_root)

    # ── Make targets referenced in docs → must exist in .PHONY ─────────────
    for source_name, text, backticks in (
        ("Makefile help", help_block, False),
        ("README.md", readme_text, True),
    ):
        for target in extract_make_targets_from_text(text, require_backticks=backticks):
            if target not in phony:
                violations.append(
                    Violation(
                        source=source_name,
                        kind="unknown-make-target",
                        detail=f"`make {target}` referenced but not in `.PHONY`",
                        hint=f"Add `{target}` to the Makefile `.PHONY` list, or correct the typo in {source_name}.",
                    )
                )

    # ── .PHONY targets → must have at least one `make <target>` line in help ──
    help_targets = extract_make_targets_from_text(help_block)
    for target in sorted(phony - _INVISIBLE_PHONY_TARGETS - help_targets):
        violations.append(
            Violation(
                source="Makefile help",
                kind="undocumented-phony",
                detail=f"`.PHONY` target `{target}` has no `make {target}` line in `help:`",
                hint=f'Add a `@echo "  make {target} …"` line to the `help:` block.',
            )
        )

    # ── STATUS enum values in docs → must match STATUS_ORDER ───────────────
    if status_order:
        for source_name, text in (("Makefile help", help_block), ("README.md", readme_text)):
            for val in extract_status_enums_from_text(text):
                if val not in status_order:
                    violations.append(
                        Violation(
                            source=source_name,
                            kind="stray-status-value",
                            detail=f"STATUS value `{val}` referenced but not in STATUS_ORDER",
                            hint=f"Update {source_name} to match STATUS_ORDER = {sorted(status_order)}.",
                        )
                    )
        # Opposite direction: STATUS_ORDER values should be reflected in help's STATUS=...
        help_status_values = extract_status_enums_from_text(help_block)
        missing = status_order - help_status_values
        if missing and help_status_values:
            # If the help has ANY STATUS= enum, it should list all of them.
            violations.append(
                Violation(
                    source="Makefile help",
                    kind="incomplete-status-enum",
                    detail=f"`help:` lists partial STATUS values; missing {sorted(missing)}",
                    hint=(
                        f"Update the `make findings` help line to include every value in "
                        f"STATUS_ORDER: {sorted(status_order)}"
                    ),
                )
            )

    # ── BEACONRY_* env vars in docs → must be read by code ─────────────────
    for source_name, text in (("Makefile help", help_block), ("README.md", readme_text)):
        for name in extract_beaconry_env_refs(text):
            if name not in env_reads:
                violations.append(
                    Violation(
                        source=source_name,
                        kind="bogus-env-var",
                        detail=f"`{name}` referenced but no code reads it",
                        hint=(
                            f"Either remove the stale mention in {source_name}, or add a "
                            f'`os.environ.get("{name}", ...)` read in `poc/`.'
                        ),
                    )
                )

    return violations


def format_violation(v: Violation) -> str:
    return f"[{v.source}] {v.kind}: {v.detail}\n    → {v.hint}"


def main() -> int:
    makefile = REPO_ROOT / "Makefile"
    readme = REPO_ROOT / "README.md"
    findings_py = REPO_ROOT / "poc" / "scripts" / "findings.py"
    poc_root = REPO_ROOT / "poc"
    violations = scan(makefile, readme, findings_py, poc_root)
    if not violations:
        print("✓ docs drift guard: Makefile help + README.md match code")
        return 0
    print(f"verify-docs-drift: {len(violations)} drift(s) found:", file=sys.stderr)
    for v in violations:
        print(f"  {format_violation(v)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
