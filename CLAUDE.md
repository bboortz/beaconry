# Beaconry — Claude Instructions

Beaconry is a CVE scanning + auto-remediation tool for self-hosted operators.
Goal: compress "CVE published → infrastructure patched and verified" from days to minutes.

Full design: `DESIGN.md`

@memory/design-decisions.md
@memory/docker-standard.md
@memory/telegram-patterns.md
@memory/code-quality.md
@memory/workflow.md

---

## Repository layout

```
DESIGN.md          — full product design (vision, architecture, business model)
Makefile           — local gates: make lint / test / type / check / smoke
pyproject.toml     — ruff + pyright + pytest config
.pre-commit-config.yaml — ruff + pyright on commit, pytest on push
poc/               — Docker Compose POC: hub + scout containers
  scout/           — inventory collector (syft, read-only, no execution)
  hub/             — CVE matcher (grype) + Telegram bot + executor plugins
  hub/executor/    — pluggable remediation adapters (docker, manual, ...)
  tests/           — pytest pyramid (unit, integration, e2e, fixtures)
docs/              — user-facing documentation lands here
  stories/         — operator-scenario stories (BC-NNN-<slug>.md) + INDEX + _template + _8-questions
  plan/            — per-feature plan docs (BC-NNN-<slug>-plan.md) + _template
memory/            — persistent context for Claude sessions
```

---

## Core pipeline

```
scout  →  syft SBOM  →  hub  →  grype match  →  exposure score
                                     ↓
                             Telegram notify (CVE ID + severity + host + fix cmd + exploit context)
                                     ↓
                         [Fix now] callback  →  executor adapter  →  verify (re-scan)
```

---

## Key tools

| Tool | Role |
|------|------|
| syft | SBOM generation — container images, pacman DB, pip packages |
| grype | CVE matching — consumes syft SBOM, outputs structured findings |
| python-telegram-bot ≥ 20 | Async Telegram bot + inline keyboard callbacks |
| FastAPI + uvicorn | Hub HTTP server (receives SBOMs from scout) |

---

## Urgency tiers (always apply when scoring findings)

```
Tier 0 — CISA KEV: 15-min target, auto-fix if safe class
Tier 1 — CVSS ≥ 9.0 + internet-facing: 15-min target
Tier 2 — CVSS 7–8 or LAN-only: 1h target
Tier 3 — CVSS < 7: 24h batch
```

---

## Development process (DD-TGD)

Design-Driven, Test-Guarded Development. Per feature:

1. Operator scenario → `docs/stories/BC-NNN-<slug>.md` (8 canonical clarification questions, Gherkin acceptance)
2. Branch `feat/BC-NNN-<slug>` + plan doc `docs/plan/BC-NNN-<slug>-plan.md`
3. Red integration test under `poc/tests/integration/` encoding the Gherkin
4. Implementation to turn test green
5. Refactor + unit tests for non-trivial helpers
6. `make check` passes (lint, type, test, coverage ≥ 60%, docker build, smoke)
7. DESIGN.md updated only if architecture changed
8. PR to `dev` → user runs `/ultrareview` → Claude runs `/code-review` → user merges

Exceptions (no story required): `fix/*` (with repro), `refactor/*` (no behavior change), `chore/*` (tooling/deps).

## Git workflow

- All work branches off `dev`. Branches: `feat/BC-NNN-<slug>`, `fix/<slug>`, `docs/<slug>`, `refactor/<slug>`, `chore/<slug>`
- `main` is CI-promoted only — never pushed to directly; on green `dev` merge CI fast-forwards `main`
- Every `feat/*` and `fix/*` needs `docs/plan/<slug>-plan.md` (template: `docs/plan/_template.md`)
- Commit format: `<type>(<scope>): <description>` — see `memory/code-quality.md`

See `memory/workflow.md` for full gate matrix, testing strategy, and tooling details.
