# BC-101 — Rename dev-compose containers (`beaconry-{hub,scout}` → `beaconry-dev-{hub,scout}`)

*Branch: `feat/BC-101-dev-compose-container-rename`*
*Start: 2026-11-08*
*Story: `docs/stories/BC-101-dev-compose-container-rename.md`*

---

## Situation

Both `poc/docker-compose.yml` (dev stack) and
`poc/docker-compose.scout-only.yml` (operator production scout
wrapped by systemd) declare `container_name: beaconry-scout`. Same
collision for `beaconry-hub` (only used by dev; the systemd scout
service is scout-only). Running `docker compose up -d` or
`docker rm -f beaconry-scout` on a host that also runs the systemd
scout silently kills the operator's production container.

2026-11-08 ~21:20 UTC incident: this happened on bb-82jq. The
operator's scout stayed dead for 54 minutes without any alert;
17 open + 7 regressed findings froze.

---

## Problems to solve

- Container name collision between dev-compose and operator-systemd
  stacks on the same host.
- Any `docker` CLI command targeting the generic name is ambiguous.
- Several beaconry CLI scripts default to the generic name
  (`BEACONRY_HUB_CONTAINER=beaconry-hub` / `SCOUT_CONTAINER` hardcoded)
  — after this rename, defaults need to track the dev name OR the
  CLI silently queries production data from a dev invocation.

---

## Goals

1. **G1** — `poc/docker-compose.yml` container_name updated:
   - `beaconry-hub` → `beaconry-dev-hub`
   - `beaconry-scout` → `beaconry-dev-scout`
2. **G2** — `poc/docker-compose.scout-only.yml` **unchanged**:
   `container_name: beaconry-scout` stays as the operator/production
   name.
3. **G3** — CLI scripts default to the new dev names:
   - `BEACONRY_HUB_CONTAINER` default `beaconry-hub` → `beaconry-dev-hub`
     (in `audit.py`, `findings.py`, `cli_smoke.py`).
   - New `BEACONRY_SCOUT_CONTAINER` env (replaces hardcoded
     `SCOUT_CONTAINER = "beaconry-scout"`) defaulting to
     `beaconry-dev-scout` (in `scan_cli.py`, `cli_smoke.py`).
4. **G4** — Env docs updated (`poc/.env.example`, `docs/VARIABLES.md`).
5. **G5** — `Makefile` `live-rebuild` / `live-status` targets use the
   new dev names.
6. **G6** — Operator path untouched: operators running the systemd
   stack set `BEACONRY_HUB_CONTAINER=beaconry-hub` +
   `BEACONRY_SCOUT_CONTAINER=beaconry-scout` in `.env` (ansible
   templating does this).
7. **G7** — CAP-T203 registered.
8. **G8** — `make check` + e2e + cold-start tests still pass (none
   of those harnesses use the dev-compose container names
   literally — they use their own distinct names).

---

## Scope

### Modified files

- `poc/docker-compose.yml` — `container_name` entries for hub +
  scout services.
- `poc/scripts/audit.py` — default for `HUB_CONTAINER` env, plus
  the module docstring line that names the default.
- `poc/scripts/findings.py` — default for `HUB_CONTAINER` env.
- `poc/scripts/cli_smoke.py` — default for `HUB_CONTAINER` env;
  convert hardcoded `SCOUT_CONTAINER = "beaconry-scout"` to env.
- `poc/scripts/scan_cli.py` — convert hardcoded `SCOUT_CONTAINER`
  to env-driven with default `beaconry-dev-scout`.
- `poc/.env.example` — update `BEACONRY_HUB_CONTAINER` default +
  add `BEACONRY_SCOUT_CONTAINER` row.
- `docs/VARIABLES.md` — same.
- `Makefile` — `live-rebuild` comment + `live-status` docker exec
  target.
- `docs/CAPABILITIES.md` — add CAP-T203.
- `docs/stories/INDEX.md` — row updated to in-progress (done).
- `CHANGELOG.md` — v0.17.2 entry.
- `VERSION` + `poc/VERSION` + `pyproject.toml` — 0.17.1 → 0.17.2.

### Added files

- `poc/tests/unit/test_BC_101_dev_compose_rename.py` — grep tests:
  - `poc/docker-compose.yml` names hub + scout with `beaconry-dev-*`.
  - `poc/docker-compose.scout-only.yml` keeps `beaconry-scout`.
  - CLI scripts' defaults are `beaconry-dev-*`.
  - `BEACONRY_SCOUT_CONTAINER` is read from env in the two scripts
    that need it.
  - Makefile targets use the new names.

### Not modified

- `poc/docker-compose.scout-only.yml` — operator/systemd production
  stack, unchanged.
- `poc/docker-compose.cold-start.yml` — already distinct
  (`beaconry-cold-start-{hub,scout}`).
- `poc/docker-compose.test.yml` — already distinct (`beaconry-hub-test`,
  `telegram-mock`, `probe-target-test`).
- `poc/scripts/status.py` — queries HTTP URLs, not docker exec; the
  display-name labels (`beaconry-hub`, `beaconry-scout`) stay as
  service-level labels.
- `poc/scripts/cleanup.py` — allowlist is by IMAGE name, not
  container name; unchanged.
- Any ansible role in homelab — operator stack keeps its current
  names. BC-101 is beaconry-side only.
- `memory/*.md` + `docs/INSTALL.md` historical snippets — snippets
  read as examples, not load-bearing.

---

## Non-goals

- Renaming production/operator-systemd containers.
- Changing the port bindings of either stack.
- Fixing the hub-side "stale scout → findings freeze" gap (parked as
  BC-102 candidate).
- Changing how homelab ansible provisions beaconry (they set the
  env vars in `.env` templates; a one-line update in their playbook
  if they want to track the new defaults, but not required).

---

## Design decisions

- **Default to the dev name, not the production name.** A developer
  who clones beaconry + runs `make live-rebuild` + `make findings`
  should get results from the dev stack they just brought up — not
  silently query a production container that happens to share the
  old name. Operators on production-only hosts set the env.
- **Add `BEACONRY_SCOUT_CONTAINER` as a first-class env** instead of
  keeping `SCOUT_CONTAINER` hardcoded — parallels the
  `BEACONRY_HUB_CONTAINER` pattern. Operators on multi-tenant hosts
  gain the same retargeting ability.
- **Keep `beaconry-scout` (no prefix) as the production/operator
  name.** Ansible roles in `~/git/homelab/ansible/roles/beaconry_scout/`
  reference this; renaming would ripple into playbooks, systemd
  units, and dashboards. BC-101's blast radius stays beaconry-side.
- **Display labels in `make status` stay generic.** `status.py`'s
  `name="beaconry-hub"` field is a TABLE LABEL, not a container
  name. The URL it queries is what determines which container's
  data shows up. Label change would be churn without value.

---

## Breaking changes

### 1. API / return shape

**None.** HTTP endpoints unchanged.

### 2. Data model

**None.**

### 3. Behavioral semantics

- **Operators who manually ran `docker exec beaconry-hub ...` on the
  dev stack see different behavior.** Pre-BC-101 that worked; post-BC-101
  they need `docker exec beaconry-dev-hub ...`. Documented in the
  migration note of VARIABLES.md.
- **CLI default changes.** `make findings` / `make audit` /
  `make scan` now target `beaconry-dev-{hub,scout}` by default.
  Operators on homelab production hosts had `.env` with
  `BEACONRY_HUB_CONTAINER=beaconry-hub` already (per the existing
  override pattern); no change there. Operators without the
  explicit override hit production via the old default — they
  need to set the env now.

---

## Merge plan

1. All work on `feat/BC-101-dev-compose-container-rename`.
2. `make check` passes locally.
3. Manual live verify: with `beaconry-scout.service` systemd active +
   `cd poc && docker compose up -d`, `docker ps` shows BOTH
   containers alive, no collision.
4. PR to `dev`. Standard review → merge.
5. Post-merge: INDEX flipped to `shipped`.

---

## Rollout plan

- **Dev users**: pull `dev`, run `make live-rebuild` — the rebuild
  produces `beaconry-dev-*` containers. CLI tools target them by
  default.
- **Operators (systemd/ansible path)**: their `.env` already pins
  `BEACONRY_HUB_CONTAINER=beaconry-hub`. After BC-101 pulls down via
  ansible, that pinning becomes load-bearing (pre-BC-101 it was
  redundant). Operators who DIDN'T have the pinning should add it —
  ansible role templates get a one-line update that's safe to
  deploy anytime.
- **CI**: GitHub Actions runners run `make check-full` on fresh
  containers each time — no persistent name collision; CI is
  unaffected.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| G1 — docker-compose.yml renamed | unit | `test_BC_101_dev_compose_rename.py::test_dev_compose_uses_dev_prefix` | ✓ |
| G2 — scout-only.yml unchanged | unit | `test_BC_101_dev_compose_rename.py::test_scout_only_keeps_operator_name` | ✓ |
| G3 — CLI defaults updated | unit | `test_BC_101_dev_compose_rename.py::test_cli_scripts_default_to_dev_hub` | ✓ |
| G3b — BEACONRY_SCOUT_CONTAINER env introduced | unit | `test_BC_101_dev_compose_rename.py::test_cli_scripts_default_to_dev_scout` | ✓ |
| G4 — docs updated | unit | `test_BC_101_dev_compose_rename.py::test_variables_md_has_scout_container` | ✓ |
| G5 — Makefile targets use new names | unit | `test_BC_101_dev_compose_rename.py::test_makefile_live_targets_use_dev_names` | ✓ |
| G6 — operator config path still works (env override) | manual | documented in Rollout | ✓ |
| G7 — CAP-T203 | lint | `make verify-traceability` | ✓ |
| G8 — check + e2e + cold-start green | gate | `make check` + `make test-e2e` | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | TOML diff / N/A | N/A — no documented behavior deprecated; display labels stay generic | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open.

---

## Testing strategy

- Unit tests (grep-based) over compose files, CLI scripts, docs,
  Makefile.
- `make check` includes verify-traceability (CAP-T203) +
  verify-variables (BEACONRY_SCOUT_CONTAINER is a new env).
- Manual live verify: coexistence of both scouts on bb-82jq
  post-merge (writing the result into the Rollout line above).

---

## Testing commands

```bash
# Fast tier
make check

# Live verify coexistence
sudo systemctl start beaconry-scout            # operator (unchanged name)
cd poc && docker compose up -d                 # dev (new names)
docker ps --filter "name=beaconry" --format '{{.Names}}'
# Expected:
#   beaconry-scout        (operator, systemd)
#   beaconry-dev-hub      (dev, compose)
#   beaconry-dev-scout    (dev, compose)
```

---

## Links

- Story: `docs/stories/BC-101-dev-compose-container-rename.md`
- Incident: 2026-11-08 ~21:20 UTC bb-82jq
- Related gap (parked as BC-102 candidate): hub-side stale-scout
  sweep so an offline scout doesn't freeze findings indefinitely
- Operator stack systemd unit: `/etc/systemd/system/beaconry-scout.service`
