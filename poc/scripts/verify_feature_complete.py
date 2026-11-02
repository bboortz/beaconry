"""BC-074 — enforce feature-completeness before a story claims shipped.

Rules:
  1. Any `docs/stories/BC-NNN-*.md` with `status: shipped` must have
     ZERO `❌` or `⚠️` rows in its paired `docs/plan/BC-NNN-*-plan.md`
     shipping-evidence table. A shipped story with open rows means
     the pre-ship audit is incomplete — the operator claimed done
     without the signals to back it up.
  2. In-progress stories CAN have open rows (that's the WIP state).
  3. Plans without a paired story are flagged as orphans (the story
     file should exist from the moment the branch is cut).

Bypass: `BEACONRY_SKIP_FEATURE_COMPLETE=1` — emergency only.

Wired into `make check` and the pre-push hook so the gap is caught
before it ships.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_STORY_ID_RE = re.compile(r"BC-(\d+)")
_STATUS_RE = re.compile(r"^status:\s*(?P<status>\S+)", re.MULTILINE)
# Shipping-evidence rows: pipe-delimited, status column at the end.
# `| ... | pointer | ✓ |` or `| ... | pointer | ❌ |` etc.
_EVIDENCE_ROW_RE = re.compile(r"\|\s*(?P<status>✓|❌|⚠️|⏳)\s*\|\s*$", re.MULTILINE)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_story_status(story_path: Path) -> str | None:
    try:
        content = story_path.read_text()
    except OSError:
        return None
    m = _STATUS_RE.search(content)
    return m.group("status").strip() if m else None


def _story_id(path: Path) -> str | None:
    m = _STORY_ID_RE.search(path.name)
    return m.group(0) if m else None


def _plan_for_story(story_path: Path, plans_dir: Path) -> Path | None:
    """Return the plan file for this story. Pairing precedence:

    1. **Exact slug match** — story `BC-011-target-health-probe.md` →
       plan `BC-011-target-health-probe-plan.md`. Preferred because
       it's unambiguous even when a BC has multiple sub-plans.
    2. **Unambiguous BC-NNN fallback** — if no exact match AND
       exactly one plan file shares the BC-NNN prefix, that one
       pairs. Handles historical slug drift (BC-031's story slug
       differs from its plan slug).
    3. **Ambiguous or missing** — return None.
    """
    # 1. Exact-slug match.
    slug = story_path.name[:-3] if story_path.name.endswith(".md") else story_path.name
    exact = plans_dir / f"{slug}-plan.md"
    if exact.exists():
        return exact
    # 2. Fallback: unambiguous BC-NNN prefix (exactly one plan).
    story_id = _story_id(story_path)
    if not story_id:
        return None
    candidates = [p for p in plans_dir.glob(f"{story_id}-*-plan.md") if not p.name.startswith("_")]
    if len(candidates) == 1:
        return candidates[0]
    # 3. Ambiguous (multiple plans, no exact match) or none.
    return None


def _any_story_at_prefix(plan_path: Path, stories_dir: Path) -> bool:
    """True iff any (non-template) story file exists with the same
    BC-NNN prefix as this plan. Used by the orphan check: a sub-plan
    for an existing BC isn't an orphan even if its slug doesn't match
    any story — fix/chore BCs don't require stories (CLAUDE.md § DD-TGD)."""
    plan_id = _story_id(plan_path)
    if not plan_id:
        return False
    return any(not s.name.startswith("_") for s in stories_dir.glob(f"{plan_id}-*.md"))


def _plan_open_rows(plan_path: Path) -> list[int]:
    """Return the 1-based line numbers of ❌ or ⚠️ shipping-evidence rows."""
    try:
        text = plan_path.read_text()
    except OSError:
        return []
    bad: list[int] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        # Skip the legend row that documents the glyphs.
        if "Legend:" in line or line.strip().startswith("|---"):
            continue
        if line.rstrip().endswith(("| ❌ |", "| ⚠️ |")):
            bad.append(lineno)
    return bad


def check(repo_root: Path | None = None) -> list[str]:
    if os.environ.get("BEACONRY_SKIP_FEATURE_COMPLETE") == "1":
        return []
    root = repo_root or _repo_root()
    stories_dir = root / "docs" / "stories"
    plans_dir = root / "docs" / "plan"
    if not stories_dir.exists() or not plans_dir.exists():
        return []
    errors: list[str] = []

    # Rule 1 + 2: shipped stories must have fully-closed plans.
    for story in sorted(stories_dir.glob("BC-*.md")):
        if story.name.startswith("_"):
            continue
        status = _parse_story_status(story)
        if status != "shipped":
            continue
        plan = _plan_for_story(story, plans_dir)
        if plan is None:
            errors.append(
                f"{story.name} is `shipped` but has no paired plan in {plans_dir}. "
                f"Create `docs/plan/{_story_id(story)}-<slug>-plan.md` or revert the status."
            )
            continue
        open_rows = _plan_open_rows(plan)
        if open_rows:
            errors.append(
                f"{story.name} is `shipped` but {plan.name} has {len(open_rows)} "
                f"open row(s) at lines {open_rows}. Close them (flip to ✓) "
                f"before claiming the story shipped, or revert status to `in-progress`."
            )

    # Rule 3: orphan plans — a plan where NO story shares its BC-NNN
    # prefix. Sub-plans for existing BCs (fix/chore follow-ups) are fine.
    for plan in sorted(plans_dir.glob("BC-*-plan.md")):
        if plan.name.startswith("_"):
            continue
        if not _any_story_at_prefix(plan, stories_dir):
            errors.append(
                f"{plan.name} has no paired story in {stories_dir}. "
                f"Every BC-NNN prefix should have at least one story file."
            )
    return errors


def main() -> int:  # pragma: no cover
    errors = check()
    if errors:
        print("verify-feature-complete: found gaps:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print("✓ feature-complete: every shipped story has a closed plan")
    return 0


if __name__ == "__main__":
    sys.exit(main())
