# Specs index

One-line board for every specification in `docs/specs/`. Specs don't have a
story-style lifecycle enum (draft/ready/in-progress/shipped); they're always
live reference docs. Each row names the owning role + the date the spec was
last materially changed.

| Spec | Scope | Owner | Last updated |
|------|-------|-------|--------------|
| [DESIGN.md](DESIGN.md) | Architecture, vision, data model, pipeline | Claude + user | 2026-11-21 |
| [CAPABILITIES.md](CAPABILITIES.md) | CAP-NNN registry — every capability + its verifying test | Claude | 2026-11-21 |
| [LIFECYCLE.md](LIFECYCLE.md) | Finding lifecycle state machine + transitions | Claude | 2026-11-21 |
| [METRICS.md](METRICS.md) | What beaconry emits — Prometheus names, labels, meaning | Claude | 2026-11-21 |
| [VARIABLES.md](VARIABLES.md) | Operator-tunable env-var reference | Claude | 2026-11-21 |
| [INSTALL.md](INSTALL.md) | Operator install walkthrough (production + homelab) | Claude | 2026-11-21 |

House rules:

- New specs land under `docs/specs/` with an entry here.
- Templates: `_template.md` is the house shape.
- Related runbooks live under `docs/operations/` (not a spec — a how-to-when-X-happens).
- Related stories: see `docs/stories/INDEX.md`.
- Related plans: see `docs/plan/` (one per feature).
