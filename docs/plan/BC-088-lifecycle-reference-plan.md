# BC-088 — Finding lifecycle reference document

*Branch: `feat/BC-088-lifecycle-reference`*
*Start date: 2026-11-05*
*Story: `docs/stories/BC-088-lifecycle-reference.md`*

---

## Situation

The finding lifecycle — states, transitions, timers, notifications,
operator buttons — is **fully implemented but nowhere documented**. The
sources today:

- `memory/design-decisions.md` — the 2026-10-29 lock has the urgency
  tiers, auto-fix trust model, canary ring design. Static design intent.
- `memory/health-checks.md` — health probe convention.
- `memory/telegram-patterns.md` — notification format.
- Fifteen plan docs (BC-003/004/005/007/020/023/030/031/034/035/036/061/065/086/087)
  each document one slice.
- `poc/hub/hub.py` (3.2 k lines) — the ground truth, interleaved.

Onboarding a new reader requires an afternoon to assemble the mental
model. Worse: **writing the BC-087 close-gap post-mortem surfaced that
`notify_auto_fix_regression`'s format helper is dead code on the real
Telegram path** (code grep confirms, see §5 below) — the comment at
hub.py:1586 claims the format is "already shaped upstream" but it isn't,
the TelegramAdapter renders `format_regression` not
`format_auto_fix_regression`. Nobody noticed because nobody read the
lifecycle top-to-bottom. The authoring exercise is the point.

---

## Problems to solve

- **No single-page reference** — the lifecycle is reconstructable but
  not reconstructed. Every new contributor pays the cost.
- **No inventory of gaps** — current flaws (snooze timer, auto-fix body,
  KEV 403, missing daily rescan vs design) live in IDEAS.md (one of
  them), in a monitor stream (another), and in nowhere (the rest).
  Nothing orders them for prioritisation.
- **Design vs code drift** — `memory/design-decisions.md` describes
  behaviour that is either partially implemented (canary ring, auto-
  rollback for packages) or not at all (daily 02:00 rescan, pacman
  post-transaction hook, docker events listener). No one audit forces a
  reconciliation.

---

## Goals

1. **`docs/LIFECYCLE.md` exists** and covers 12 descriptive sections —
   state diagram, ingest, row timestamps, notifications, buttons+handlers,
   fix pipeline, health waterfall, reconcile scope, isolation, timers,
   audit events, precedence. Mapped 1:1 to acceptance Scenario 1+2 of
   the story.
2. **Five known-flaw sections populated** — Unclearities, Necessary
   improvements, Known bugs, Missing implementation (vs design),
   Contradictions/inconsistencies. Each lists at least one concrete,
   code-grep-backed entry. Mapped to Scenario 3.
3. **Traceability** — CAP-T182 registered, BC-088 row in INDEX, both
   discoverable. Scenario 4.
4. **No code regression** — pure docs change, fast tier green.
   Scenario 5.

---

## Scope

- **ADD** `docs/LIFECYCLE.md` (new file, ~450 lines expected)
- **ADD** CAP-T182 entry in `docs/CAPABILITIES.md`
- **ADD** BC-088 row in `docs/stories/INDEX.md`
- **ADD** `docs/stories/BC-088-lifecycle-reference.md` (story, already written)
- **ADD** this plan doc
- **ADD** `poc/tests/unit/test_lifecycle_doc.py` — doc-structure test
  that makes the Gherkin "grep" scenarios executable (every status
  mentioned, every timer var mentioned, every flaw section populated,
  CAP-T182 registered). Zero runtime code touched.
- No `poc/{hub,scout,scripts,…}/*.py` touched. No `pyproject.toml`
  bump. No VERSION bump (patch convention: docs-only ≠ version bump).

---

## Non-goals

- Fixing any flaw surfaced in the known-flaw sections. Each is a
  future BC candidate; this BC is pure inventory.
- Mermaid/SVG diagrams. ASCII only for v1.
- Translating `memory/design-decisions.md` content — link to it, don't
  duplicate.
- Consolidating the fifteen lifecycle-adjacent plan docs. Those stay;
  LIFECYCLE.md references them as source material, not replacement.
- README.md changes. Lifecycle is advanced/reference material;
  operators land on README → docs/INSTALL.md first.

---

## Design decisions

- **Single markdown file** vs multi-page site. One file. Grep-friendly,
  fits a terminal, renders cleanly in GitHub web view. No site generator
  to maintain.
- **ASCII state diagram** vs Mermaid. ASCII. Works in `cat`, in GitHub
  `.md` preview, and in plan-doc quotes. Mermaid needs GitHub-side
  rendering + breaks when someone edits raw.
- **Known-flaw sections inline** vs separate issue backlog. Inline. The
  doc is read front-to-back during onboarding; flaws belong next to the
  behaviour they describe. Rejected: separate `LIFECYCLE_ISSUES.md` —
  bifurcation makes drift more likely, not less.
- **Timer table shape**: `Knob | Default | Gates`. Columns match
  `docs/VARIABLES.md` so cross-referencing is a lookup, not a translation.
- **Code-grep evidence for every flaw claim.** A claim without a code
  pointer or a design-decisions.md pointer is suspicion, not a bug.
  Rejected aspirational claims stay out of the doc.

---

## Breaking changes

### 1. API / return shape

**None.** No code changes; no caller sees a different interface. Not
even the `make status` output changes.

### 2. Data model (schema + legacy rows)

**None.** No schema changes; no new columns; no reinterpretation of
existing columns.

### 3. Behavioral semantics

**None at runtime.** The operator-facing change is paper: a new
authoritative reference doc. The side effect — future BCs can cite
`docs/LIFECYCLE.md § X` as rationale — is additive and opt-in.

**One soft-contract change**: future BCs that materially change a
state transition, timer, or notification path are now expected to
update LIFECYCLE.md as part of Definition of Done. This is
documented in the CAP-T182 description so the convention is grep-able.
Not enforced by a lint (yet); could become a `verify-lifecycle-drift`
check in a future BC if drift becomes a real problem.

---

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Doc lists every status | unit | `test_lifecycle_doc.py::test_every_status_mentioned[open]` (parametrize covers all 9 statuses) | ✓ |
| Doc lists every timer var | unit | `test_lifecycle_doc.py::test_every_timer_var_mentioned[SCAN_INTERVAL]` (parametrize covers SCAN_INTERVAL + 7 BEACONRY_*) | ✓ |
| Every known-flaw category populated | unit | `test_lifecycle_doc.py::test_known_flaw_section_populated[## 13. Unclearities]` (parametrize covers § 13–17) | ✓ |
| CAP-T182 registered + references the doc | unit | `test_lifecycle_doc.py::test_cap_t182_registered_in_capabilities` | ✓ |
| Doc has 17 sections (12 descriptive + 5 flaw) | unit | `test_lifecycle_doc.py::test_doc_has_expected_section_count` | ✓ |
| INDEX updated | grep | `docs/stories/INDEX.md` has a `BC-088` row | ✓ |
| Fast tier green | gate log | `make check` passes (1034+ tests, 86% coverage, lint, type, traceability, memory-drift) | ✓ |
| Minimal code surface | git diff | `git diff --stat main..HEAD` — only `.md` files + one unit-test file | ✓ |

---

## Merge plan

1. Branch `feat/BC-088-lifecycle-reference` off `dev` (already done).
2. `make check` (fast tier only — no code = no `make check-full` need).
3. Commit: single commit, `docs(lifecycle): BC-088 add
   docs/LIFECYCLE.md + flaw inventory`.
4. Push → pre-push runs `make check-full` anyway (BC-079 convention).
   Expect green since no code changes.
5. Open PR → `dev`.
6. User runs `/ultrareview`, Claude runs `/code-review`, user merges.
7. CI fast-forwards `main`.
8. **No release.** Docs-only landing; no version bump; no deploy
   needed.

---

## Rollout

Not applicable — docs-only. No fleet migration, no cache invalidation,
no feature flag.

---

## Links

- Story: `docs/stories/BC-088-lifecycle-reference.md`
- Dependent-on CAPs: CAP-T084 (VARIABLES.md single-source-of-truth
  pattern that this follows)
- Source of truth for individual sections: `memory/design-decisions.md`
  + plan docs of BC-003/004/005/007/020/023/030/031/034/035/036/061/065/086/087
- The three flaws that triggered this BC:
  - IDEAS.md entry "Snooze 4h button doesn't actually snooze"
  - Monitor stream 2026-11-05 07:55 onward: KEV 403 hourly
  - Code review 2026-11-05 13:30: `notify_auto_fix_regression` body
    wire-up (§5 of LIFECYCLE.md)
