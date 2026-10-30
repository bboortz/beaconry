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
| `make findings`  | — | print current findings DB (read-only; `STATUS=accepted` filters) | <1s | manual |
| **`make check`** | **fast** | lint + type + test + verify-traceability + smoke | **~7s** | **pre-push + merge gate** |
| **`make check-full`** | **full** | `make check` + `pytest -m "compose or real_tools"` | **~1–2m** | **pre-merge sanity + CI** |

Fast tier runs every commit cycle; full tier runs before pushing a feat/fix branch for review (and CI, when it exists). Full-tier opt-in markers skip gracefully when their prerequisites (docker compose, syft+grype) aren't available.

> **First-time `make check-full`?** On a laptop without `docker compose` or `syft`+`grype`, you'll see output like `8 passed, 1 skipped` — that's expected: the opt-in tests mark themselves `skipif` on missing prereqs. The gate still exits 0. Install the prereqs (see POC quickstart below) to activate the full coverage.

---

## POC quickstart

```bash
# One-time
cp poc/.env.example poc/.env   # fill TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID

# Each run
make demo                      # real pipeline via Docker Compose — host inventory
BEACONRY_MOCK=1 make demo      # local Python mock pipeline (fast dev loop)
make findings                  # read-only CLI view of what's in the dev DB
make findings STATUS=accepted  # filter by status (open|snoozed|accepted|fixed)
```

**Querying the Compose named volume.** `make findings` reads `$DATA_DIR/beaconry.db` (default `.beaconry-cache/beaconry.db` — the local-Python path). The Compose stack writes to the `beaconry-data` named volume at `/data` inside the hub container. To query that DB from the host:

```bash
# Copy the Compose DB into a tmp dir and point make findings at it
docker run --rm -v beaconry-data:/data busybox cat /data/beaconry.db > /tmp/compose.db
DATA_DIR=/tmp make findings
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

### Telegram button actions

Each notification carries three buttons:

| Button | Action |
|---|---|
| 🔧 Fix now | Runs the real executor (BC-009). For docker findings: tags `<base>:rollback`, `docker pull <base>:latest`, re-scans with syft + grype, and reports one of three terminal states (see below). For non-docker: shows a "Manual action required" message with the exact command. Opt-out: `BEACONRY_FIX_ENABLED=0`. |
| ⏰ Snooze 4h | Marks finding `snoozed`; next `make demo` won't re-notify. |
| ✅ Accept risk | Marks finding `accepted`; next `make demo` won't re-notify. |

Double-tapping Snooze/Accept is safe — the handler notices the finding is already in that state, re-shows the current status, and skips the DB write. Malformed callback payloads are rejected with a warning.

### Fix now behavior (BC-009 + BC-014)

Tapping [🔧 Fix now] on a `source_type=docker` finding walks `⏳ Fixing…` → `🔍 Verifying…` → one of three honest terminal states:

| Message | Meaning | DB status |
|---|---|---|
| `✅ Fixed and verified` | Pull succeeded; re-scan can no longer match the CVE in that package. | `fixed` |
| `⚠️ Pulled, but CVE still present upstream` | Pull succeeded, but upstream hasn't patched this CVE yet. Not a beaconry bug. | stays `open` |
| `❌ Fix failed: <reason>` | `docker pull` errored. Image unchanged. `<base>:rollback` tag still exists for inspection. | stays `open` |

**Pull preserves the source tag (BC-014).** For `postgres:16-alpine`, beaconry runs `docker pull postgres:16-alpine` — not `postgres:latest`. Fluid tags still pick up upstream security updates via Docker Hub tag-mutability; pinned tags (`postgres:16.1.3-alpine`) stay pinned so operators can't be silently moved across major versions. Trade-off: for pinned tags, `verify_fix` may surface "upstream still present" more often — operator decides when to manually bump the tag.

Beaconry pulls the image. **Your running containers continue with the old image until you restart them yourself** (`docker compose up -d` or equivalent). Beaconry does not touch the compose state.

A safety tag `<base>:rollback` is set before every pull. Restore with:
```bash
docker tag postgres:rollback postgres:16-alpine
```

Opt-out: `BEACONRY_FIX_ENABLED=0 make demo` restores the pre-BC-009 stub ("⚙️ Fix execution disabled") — useful for demos, dry-runs, and safety-conscious exploration.

Non-docker findings (pacman/pip) route to ManualAdapter: the message shows the exact command (`sudo pacman -Syu <package>`) and the DB status stays `open` until the operator runs it externally.

### Pre-ship audit

Pre-ship audit (step 6a) fills the `Shipping evidence` table in each plan doc. A story moves to `shipped` only when every Gherkin scenario and plan goal has a pointer (test, log line, or dated manual verification). Three audit lenses: Gherkin coverage, data-model symmetry, self-consistency.

Full detail in [`memory/workflow.md`](memory/workflow.md).

---

## Service health observability (BC-010)

Every beaconry service follows the four-endpoint convention from `memory/health-checks.md`:

| Endpoint | Question | Fail reaction |
|---|---|---|
| `/livez` | process alive, not deadlocked? | compose restarts the container |
| `/readyz` | ready to accept work? (local prereqs OK) | compose marks unhealthy; dependents wait |
| `/startupz` | first-run boot done? (grype DB downloaded) | gates `/livez`/`/readyz` during start_period |
| `/healthz` | operator-facing JSON summary; always 200 | — (read by `make status`, future dashboards) |

Compose healthcheck targets `/readyz`. The restart-loop trap is explicit: a dead Telegram token or missing DB **must not** fail `/livez` — restarting won't fix those failures. Degradation surfaces in `/healthz` instead.

**One-command status check (BC-013):**

```bash
make status
# NAME            STATE    HEALTH    LAST SCAN  DETAILS
# beaconry-hub    running  healthy   13s ago    grype_db ok · telegram polling
# beaconry-scout  running  healthy   13s ago    11 images · docker_socket ok
```

Both services queried uniformly via HTTP /healthz (hub on `:8000`, scout on `:8001` since BC-013). No `docker exec` fallbacks; no raw Python exception class names in the output.

**Exit codes:** `python3 -m poc.scripts.status` returns 0 (all healthy) / 1 (any degraded) / 2 (any down). `make status` collapses non-zero to 2 by GNU make convention — call the python module directly for granular exit codes if you're scripting around them.

**Hub `/health` kept** as a transitional 200-alias for pre-BC-010 monitors; body shape changed from `{"status":"ok"}` to `{"status":"ready", "checks":{...}}`.

See `memory/health-checks.md` for the full convention and the new-container checklist.

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
