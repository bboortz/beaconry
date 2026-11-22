"""BC-147 — docs/ three-folder layout lint.

Refuses stray `.md` files at `docs/` top-level, and refuses specs placed
outside `docs/specs/`. Runs on every `make check`.

Three legal homes for every doc:
  - docs/specs/   — specifications (CAPABILITIES, LIFECYCLE, METRICS, …)
  - docs/plan/    — per-feature plans (BC-NNN-<slug>-plan.md)
  - docs/stories/ — operator-scenario stories (BC-NNN-<slug>.md)

Plus one runbook class:
  - docs/operations/ — step-by-step playbooks (distinct from specs)

Top-level `docs/README.md` is the only tolerated file at `docs/` root.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Only `README.md` is allowed to live at `docs/` top-level.
TOPLEVEL_ALLOWED = {"README.md"}

# Legal subdirectories directly under `docs/`.
LEGAL_SUBDIRS = {"specs", "plan", "stories", "operations"}


def check(repo_root: Path) -> list[str]:
    """Return violation strings. Empty list = clean."""
    violations: list[str] = []
    docs = repo_root / "docs"
    if not docs.is_dir():
        return [f"docs/ missing at {docs}"]

    # 1. No stray .md at docs/ top-level.
    for p in sorted(docs.iterdir()):
        if p.is_file() and p.suffix == ".md" and p.name not in TOPLEVEL_ALLOWED:
            violations.append(
                f"docs/{p.name}: strays at docs/ top-level. Move into docs/specs/ "
                f"(for specifications), docs/plan/ (for plans), docs/stories/ "
                f"(for stories), or docs/operations/ (for runbooks)."
            )

    # 2. No unexpected subdirectory at docs/ top-level.
    for p in sorted(docs.iterdir()):
        if p.is_dir() and p.name not in LEGAL_SUBDIRS:
            violations.append(
                f"docs/{p.name}/: unexpected subdirectory. Legal subdirs: "
                f"{sorted(LEGAL_SUBDIRS)}. Extend `LEGAL_SUBDIRS` in "
                f"verify_docs_layout.py only after a conscious design choice."
            )

    # 3. If docs/specs/ exists, it must hold at least _template.md + INDEX.md.
    specs = docs / "specs"
    if specs.is_dir():
        for required in ("_template.md", "INDEX.md"):
            if not (specs / required).is_file():
                violations.append(
                    f"docs/specs/{required}: missing. Specs directory must carry a "
                    f"template + index (mirror of docs/stories/)."
                )

    # 4. DESIGN.md must not live at repo root.
    if (repo_root / "DESIGN.md").exists():
        violations.append("DESIGN.md: must live under docs/specs/DESIGN.md, not at repo root.")

    return violations


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    violations = check(repo_root)
    if violations:
        print("verify-docs-layout: found violations:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    print("✓ verify-docs-layout: docs/ three-folder layout ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
