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

`make check` is the authoritative local gate — same checks CI runs.

| Target | Runs |
|--------|------|
| `make lint`   | `ruff check` + `ruff format --check` |
| `make format` | `ruff format` + `ruff check --fix` |
| `make type`   | `pyright` (basic mode) |
| `make test`   | `pytest` with coverage |
| `make check`  | lint + type + test (the merge gate) |
| `make smoke`  | end-to-end pipeline smoke test |

---

## POC quickstart

```bash
cd poc
cp .env.example .env         # add TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
bash demo.sh                 # seeds nginx:1.24, starts stack, tails hub logs
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
