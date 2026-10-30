# Beaconry

CVE scanning + auto-remediation for self-hosted operators.
Goal: compress "CVE published → infrastructure patched and verified" from days to minutes.

- **Full design**: [`DESIGN.md`](DESIGN.md)
- **Development process**: [`CLAUDE.md`](CLAUDE.md), [`memory/workflow.md`](memory/workflow.md)
- **Stories (operator scenarios)**: [`docs/stories/`](docs/stories/)

---

## Dev quickstart

```bash
git clone <repo>
cd beaconry

python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

make install-hooks   # pre-commit + pre-push
make check           # lint + type + test
```

`make check` is the authoritative local gate — same checks CI runs. Two tiers:

| Gate | Tier | Runs | Cost | When |
|------|------|------|------|------|
| `make lint`   | — | `ruff check` + `ruff format --check` | <2s | pre-commit |
| `make type`   | — | `pyright` (basic mode) | ~1s | pre-commit |
| `make test`   | — | `pytest` with coverage | ~3s | pre-push + make check |
| `make verify-traceability` | — | CAPABILITIES.md ↔ tests + plan-doc enforcement | <1s | make check |
| `make smoke`  | — | end-to-end pipeline (mocked) | ~2s | make check |
| **`make check`** | **fast** | lint + type + test + verify-traceability + smoke | **~7s** | **pre-push + merge gate** |
| **`make check-full`** | **full** | `make check` + `pytest -m "compose or real_tools"` | **~1–2m** | **pre-merge sanity + CI** |

Fast tier runs every commit cycle; full tier runs before pushing a feat/fix branch for review (and CI, when it exists). Full-tier opt-in markers skip gracefully when their prerequisites (docker compose, syft+grype) aren't available.

---

## POC quickstart

```bash
# One-time
cp poc/.env.example poc/.env   # fill TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID

# Each run
make demo                      # real pipeline via Docker Compose — host inventory
BEACONRY_MOCK=1 make demo      # local Python mock pipeline (fast dev loop)
```

`make demo` (real mode) runs `docker compose up --build` under `poc/`. Scout and hub run as containers; the host needs only Docker + Docker Compose. Scout scans images matching `SCOUT_INCLUDE` (default `nginx*,redis*,postgres*` — demo-safe allowlist). Set `SCOUT_INCLUDE=*` in `poc/.env` to scan everything, or narrow with `SCOUT_EXCLUDE=*-test,*-dev`.

First `make demo` builds the container images (installs syft + grype inside; ~5 min cold cache; subsequent runs are fast). Teardown:

```bash
docker compose -f poc/docker-compose.yml down
```

See [`docs/plan/BC-000-repo-scaffold-plan.md`](docs/plan/BC-000-repo-scaffold-plan.md) for the current state of the scaffold, and [`docs/stories/INDEX.md`](docs/stories/INDEX.md) for in-flight stories.

---

## Development process (DD-TGD)

Design-Driven, Test-Guarded Development. Every feature flows:

1. Operator scenario in `docs/stories/BC-NNN-<slug>.md` (8-question clarification → Gherkin)
2. Branch `feat/BC-NNN-<slug>` + plan doc `docs/plan/BC-NNN-<slug>-plan.md`
3. Red integration test in `poc/tests/integration/`
4. Implementation to turn red → green
5. Refactor + unit tests
6. `make check` passes
7. PR to `dev` → `/ultrareview` → `/code-review` → merge
8. CI fast-forwards `main` to `dev`

Full detail in [`memory/workflow.md`](memory/workflow.md).

---

## Repository layout

```
DESIGN.md               full product design (vision, architecture, business model)
Makefile                local gates — "make check"
pyproject.toml          ruff + pyright + pytest config
.pre-commit-config.yaml fast hooks on commit + pytest on push
poc/                    Docker Compose POC: hub + scout + executors
  scout/                inventory collector (syft, read-only)
  hub/                  CVE matcher (grype) + Telegram bot + executors
  hub/executor/         pluggable remediation adapters
  tests/                pytest pyramid (unit, integration, e2e, fixtures)
docs/                   user-facing documentation lands here
  stories/              operator scenarios (BC-NNN-<slug>.md) + INDEX + _template
  plan/                 per-feature plan docs (BC-NNN-<slug>-plan.md) + _template
memory/                 persistent context for development sessions
```
