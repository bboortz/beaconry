# BC-094 — Homelab consumes forge-signed ghcr images

*Branch: `feat/BC-094-signed-ghcr-images`*
*Start: 2026-11-06*
*Story: `docs/stories/BC-094-signed-ghcr-images.md`*

---

## Situation

Two independent facts:

1. Forge (bboortz/forge, FG-NNN BCs) already builds and cosign-signs
   `ghcr.io/bboortz/beaconry-{hub,scout}` keyless-OIDC
   (identity: `https://github.com/bboortz/forge/.github/workflows/build.yml@refs/heads/main`,
   issuer: `https://token.actions.githubusercontent.com`). Tag
   `:latest` tracks main; a fresh digest for beaconry's current
   HEAD was pushed hours after release v0.16.1.

2. Homelab (bb-claw/homelab, `feat/beaconry-deploy`) runs
   `docker compose build --pull` in `ansible/tasks/docker-compose-deploy.yml`
   — a generic task shared across every service. Beaconry's hub +
   scout roles chain to it with `-f docker-compose.yml -f
   docker-compose.hub-override.yml` (hub) / `-f
   docker-compose.scout-only.yml -f docker-compose.scout-override.yml`
   (scout).

Beaconry's compose files (`docker-compose.yml`, `docker-compose.scout-only.yml`)
have both `build:` + `image: beaconry-{hub,scout}:latest` keys. Nineteen
other scripts and files (`cleanup.py`, `dogfood_scan.py`, `demo.sh`,
`docker-compose.test.yml`, `docker-compose.cold-start.yml`, several
Makefile targets) reference `beaconry-{hub,scout}:latest` by that
unqualified name. Changing the base compose files' `image:` to the
ghcr ref would ripple through all of them.

## Problem

Enable homelab to opt into the ghcr-pulled image without disturbing
local dev or the 19+ downstream refs.

## Goal

Ship minimal additive beaconry-side artifacts so homelab can
`-f docker-compose.hub-ghcr.yml` into its ansible flow and consume
the signed image. Local dev stays on `beaconry-hub:latest`.

## Scope

### Added files
- `poc/docker-compose.hub-ghcr.yml` — override. Single service `hub`,
  single key `image: ghcr.io/bboortz/beaconry-hub:${BEACONRY_IMAGE_TAG:-latest}`.
- `poc/docker-compose.scout-ghcr.yml` — override. Single service
  `scout`, single key
  `image: ghcr.io/bboortz/beaconry-scout:${BEACONRY_IMAGE_TAG:-latest}`.
- `poc/tests/unit/test_BC_094_ghcr_image_overrides.py` — asserts
  both files exist, parse as YAML, carry exactly the expected
  service + image ref + envsubst syntax, and have NO `build:` key.

### Modified files
- `poc/.env.example` — new section header `# ─── Deploy / image
  (BC-094) ───` + `BEACONRY_IMAGE_TAG=latest`.
- `docs/VARIABLES.md` — new row for `BEACONRY_IMAGE_TAG`.
- `poc/scripts/verify_variables_documented.py` — new
  `DEPLOY_ONLY_ALLOWLIST` category for compose-substituted vars;
  orphan check excludes it.
- `docs/CAPABILITIES.md` — CAP-T194 + CAP-T195.
- `docs/stories/INDEX.md` — BC-094 row.
- `CHANGELOG.md` — v0.16.2 entry.
- `VERSION` / `poc/VERSION` / `pyproject.toml` — 0.16.1 → **0.16.2**.

### Not modified
- `poc/docker-compose.yml` — unchanged (keeps `build:` + `image:
  beaconry-hub:latest`). Local dev + scripts intact.
- `poc/docker-compose.scout-only.yml` — unchanged.
- `poc/docker-compose.{cold-start,test}.yml` — unchanged (local-only
  test stacks; no ghcr relevance).
- `poc/hub/Dockerfile`, `poc/scout/Dockerfile` — unchanged.
- Any Python runtime code — unchanged; `BEACONRY_IMAGE_TAG` is
  consumed by compose at config time, not by Python at runtime.

## Non-goals

- Homelab-side changes (new ansible task, cosign verify step,
  systemd unit tweak). Summary in PR body + follow-up PR against
  bb-claw/homelab.
- Signing images from beaconry's own release workflow (forge is
  authoritative; adding a second signer would just create drift).
- Multi-arch. Follow forge's matrix.
- Pinning beyond `latest` as a project-wide default (operator
  picks per-host).

## Design decisions

- **Override files, not base-compose edit**: minimises blast
  radius. 19+ files reference `beaconry-{hub,scout}:latest` by that
  exact string; changing the base `image:` would ripple through
  cleanup/dogfood/test fixtures/demo scripts. Override lets the
  main file stay the local-dev reference.
- **One override per service**, not a single combined override:
  compose merges additively, so a combined `docker-compose.ghcr.yml`
  with both `hub` and `scout` services would inject a dangling `hub`
  service when scout-only compose is the base. Two files, each
  touching one service, avoids the dangling-service hazard.
- **`${BEACONRY_IMAGE_TAG:-latest}`, not plain `:latest`**: pinning
  is one env change away. Operators who want rollback muscle set
  `BEACONRY_IMAGE_TAG=v0.16.1` in their `.env` or ansible group_vars;
  default behaviour (unset) is "track latest".
- **`DEPLOY_ONLY_ALLOWLIST`, not "add to VARIABLES.md without a
  row"**: operators discover dials through VARIABLES.md. A deploy-
  time var should be documented there with the same discoverability
  as runtime vars. The orphan check gets a new exemption category
  so the lint doesn't force a `os.environ.get` call somewhere just
  to satisfy itself.
- **No cosign artifacts in beaconry repo**: forge publishes the
  signature + keyless OIDC trust root. Beaconry's repo doesn't
  need a public key file. Homelab's ansible step verifies against
  the forge identity directly.

## Breaking changes

### API / return shape
**None.** No code changes; no HTTP contract changes.

### Data model
**None.** No schema changes.

### Behavioral semantics
**None for local dev.** Everything that worked yesterday works today.
`make demo`, `make live-rebuild`, `make test-scenarios`, `make
cleanup`, `make dogfood-scan` — all unchanged. Running `docker
compose up -d` without the ghcr override still builds locally and
tags as `beaconry-hub:latest`.

**New opt-in for homelab** (lands when the homelab-side PR merges):
`docker compose -f poc/docker-compose.yml -f poc/docker-compose.hub-ghcr.yml pull && up -d`
consumes the ghcr image. Consequences:
- No local docker build on homelab (faster deploy).
- Running image now has name `ghcr.io/bboortz/beaconry-hub:latest`
  on homelab (vs `beaconry-hub:latest` on dev boxes). Container
  name unchanged (`beaconry-hub`). Any homelab-side scripts that
  grep for `beaconry-hub:latest` need rechecking — none observed
  in the shared homelab task today.

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Both override files exist + valid YAML | unit | `test_BC_094_ghcr_image_overrides.py::test_override_files_exist` | ✓ |
| Each override defines exactly one service | unit | `test_BC_094_ghcr_image_overrides.py::test_each_override_has_exactly_one_service[docker-compose.hub-ghcr.yml-hub]` | ✓ |
| Image ref matches `ghcr.io/bboortz/beaconry-{hub,scout}:${BEACONRY_IMAGE_TAG:-latest}` | unit | `test_BC_094_ghcr_image_overrides.py::test_image_ref_matches_expected[docker-compose.hub-ghcr.yml-hub-ghcr.io/bboortz/beaconry-hub:${BEACONRY_IMAGE_TAG:-latest}]` | ✓ |
| No `build:` key in either override | unit | `test_BC_094_ghcr_image_overrides.py::test_no_build_key_in_overrides[docker-compose.hub-ghcr.yml-hub]` | ✓ |
| Main compose unchanged (sanity: still has `build: ./hub` + unqualified image) | unit | `test_BC_094_ghcr_image_overrides.py::test_main_compose_unchanged_for_local_dev` | ✓ |
| BEACONRY_IMAGE_TAG in VARIABLES.md | lint | `make verify-variables` | ✓ |
| BEACONRY_IMAGE_TAG in `.env.example` | lint | three-way sync (BC-040) | ✓ |
| Orphan check exempts BEACONRY_IMAGE_TAG | lint | `test_BC_094_ghcr_image_overrides.py::test_deploy_only_allowlist_covers_image_tag` | ✓ |
| INDEX updated | grep | `docs/stories/INDEX.md` has BC-094 row | ✓ |
| CAPs registered | lint | `make verify-traceability` | ✓ |
| Fast tier green | gate log | `make check` | ✓ |

## Rollout

1. Merge BC-094 to beaconry `dev` → `main` via normal release
   pipeline → tag `v0.16.2`. Override files appear in the repo.
2. Homelab-side PR (separate, against `bb-claw/homelab`
   `feat/beaconry-deploy`):
   - New `ansible/tasks/docker-compose-pull-deploy.yml` — sibling
     of `docker-compose-deploy.yml`:
     * `docker compose pull` (replaces `docker compose build --pull`).
     * cosign verify of each pulled image against forge identity.
     * Rest identical (pre/post hooks, systemd restart, smoke).
   - `ansible/roles/beaconry_hub/tasks/main.yml` + scout: swap
     the `include_tasks` target to the new pull task; add
     `-f docker-compose.hub-ghcr.yml` (resp. scout) to
     `docker_compose_flags`.
   - `defaults/main.yml` for both roles: `beaconry_image_tag: latest`.
   - `templates/{hub,scout}.env.j2`: inject `BEACONRY_IMAGE_TAG={{
     beaconry_image_tag }}`.
   - `systemd/beaconry-{hub,scout}/*.service.j2`: `ExecStartPre=
     docker compose pull` (replace existing build step).
   - Common prerequisites: cosign binary installed on target
     hosts (either via the existing `docker` role or a new
     prereq step).
3. Live verify on homelab: `make deploy-beaconry` runs → ansible
   log shows `docker compose pull` + `cosign verify … ok` →
   systemd restart → smoke healthy.

## Links

- Story: `docs/stories/BC-094-signed-ghcr-images.md`
- Forge publish workflow: `bboortz/forge:.github/workflows/build.yml`
  (matrix driven by `scripts/discover-images.sh`)
- Homelab roles (feat/beaconry-deploy):
  `bb-claw/homelab:ansible/roles/beaconry_{hub,scout}/`
- Prior art for deploy-time env vars: `DOCKER_GID`
  (`docker-compose.yml` compose-time substitution, documented in
  VARIABLES.md with BC-078 origin).
