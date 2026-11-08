# BC-098 — Stage-at-runtime-path: Dockerfiles detect Python minor at build time

*Branch: `feat/BC-098-python-version-detect`*
*Start: 2026-11-08*
*Story: `docs/stories/BC-098-python-version-detect.md`*

---

## Situation

Current state (both Dockerfiles, post-BC-097):

- Builder: `FROM ghcr.io/bboortz/forge-builder-trixie:latest AS builder`
  → Python 3.13.18 today.
- Runtime: `FROM ghcr.io/bboortz/python-scratch-trixie:latest AS runtime`
  → Python 3.13.18 today.
- Site-packages path is hardcoded in COPY lines:
  - `poc/hub/Dockerfile:78` — `COPY --from=builder /install/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages`
  - `poc/scout/Dockerfile:31` — same shape

Forge FG-032 (PR open) bumps both `:latest` tags in-place to Python
3.14. Next daily cron after forge merges makes the published `:latest`
contain Python 3.14 binaries + sys.path rooted at `/usr/local/lib/python3.14/`.
Beaconry builds break at that moment because the hardcoded `python3.13`
paths don't exist in the builder, and even if they did, the runtime's
sys.path wouldn't look there.

---

## Problems to solve

- Hardcoded Python minor literals in both Dockerfiles.
- No general pattern for forge-driven minor bumps. Every bump would
  need a beaconry PR.
- BC-097's risk note promised cold-start smoke catches this — but
  "catches" means "fails loudly in CI," not "fixes itself."

---

## Goals

1. **G1 — Zero `python3.X` literals** in `poc/{hub,scout}/Dockerfile`
   (COPY, mkdir, ARG, or any shell command).
2. **G2 — Builder detects the Python minor** at build time via
   `python3 -c "import sys; …"` and moves site-packages into a
   staging path that mirrors runtime layout.
3. **G3 — Runtime stage imports `/stage/*`** with a single
   `COPY --from=builder /stage /`. All pre-existing targeted COPYs
   that overlap with /stage are retired.
4. **G4 — Cold-start imports probe** (`test_hub_can_import_all_runtime_deps`
   + scout equivalent) pass against today's Python 3.13.18 images.
   Same code produces working images under the forge 3.14 rebuild
   without a beaconry PR.
5. **G5 — SBOM still captures Python wheels** — `syft dir:/install`
   runs **before** the move, so SBOM content is unchanged.
6. **G6 — CAP-T200 registered**, CAP-T199 updated past-tense to
   reference BC-098 as the successor.

---

## Scope

### Modified files

- `poc/hub/Dockerfile`:
  - Insert new RUN step after the existing `syft dir:/install -o
    syft-json=/sbom.json` (line 56) and the `mkdir -p /stage/data`
    (line 60): the PY_VER detect + `mv` into `/stage/usr/local/lib/python${PY_VER}/`.
  - Replace line 78 (`COPY --from=builder /install/lib/python3.13/site-packages …`)
    with `COPY --from=builder /stage /`.
  - Drop line 89 (`COPY --from=builder --chown=65532:65532 /stage/data /data`)
    — now subsumed by `COPY /stage /`. Ownership preservation still
    guaranteed because `/stage/data` was chowned 65532 in the builder
    and COPY without `--chown` preserves source ownership from a stage.
  - Refresh prose: lines 14 (`BC-095: forge-builder-trixie ships
    python 3.13.18 …`), 64 (`Python 3.13.18, PATH=/usr/local/bin …`),
    and 68–74 (the BC-097 risk comment that predicted this exact
    story). Strip minor-version literals; past-tense the risk.
- `poc/scout/Dockerfile`:
  - Insert the same PY_VER detect + `mv` step after the existing
    `syft dir:/install -o syft-json=/sbom.json` (line 23).
  - Replace line 31 with `COPY --from=builder /stage /`.
  - Refresh prose: line 8 (`BC-095: forge-builder-trixie ships
    python 3.13.18 …`) — strip version literal.
- `docs/CAPABILITIES.md`:
  - Update CAP-T199 description past-tense: "risk was retired by
    BC-098 (CAP-T200)."
  - Insert new CAP-T200 entry above CAP-T195, mirroring the shape
    of the other CAP-T19* rows.
- `docs/stories/INDEX.md` — BC-098 in-progress row.
- `CHANGELOG.md` — v0.16.6 entry.
- `VERSION` + `poc/VERSION` + `pyproject.toml` — 0.16.5 → 0.16.6.

### Added files

- `poc/tests/unit/test_BC_098_python_version_detect.py` — grep-based:
  - No `python3\.\d+` literal in either Dockerfile's instructions
    (COPY, RUN, mkdir, ARG). Comments may still name historical
    versions (prose), but no executable line.
  - Each Dockerfile contains exactly one RUN line matching the
    PY_VER shell detection pattern.
  - Each Dockerfile contains exactly one `COPY --from=builder /stage /`.
  - No `COPY --from=builder /install/lib/python3.X/...` remains.

### Not modified

- `poc/scripts/verify_pinned_versions.py` — `RUNTIME_IMAGE_RE`
  already accepts `:latest`; pattern unchanged.
- `poc/scripts/dogfood_sbom.py`, `poc/scripts/dogfood_scan.py` —
  consume the final runtime image at `/sbom.json` and binary tags;
  neither cares about site-packages layout.
- `poc/hub/hub.py`, `poc/scout/scout.py` — Python imports are
  Python-version-agnostic; sys.path magic is the stdlib's job.
- `memory/docker-standard.md`, `memory/health-checks.md` — the
  recipe templates mention Python 3.12/3.13 in prose; those are
  the ONLY places an operator is pointed at a specific minor, and
  the template itself is for new services (not forge consumers).
  Left as-is; optional follow-up.

---

## Non-goals

- Rolling back to the semver-pinned `:3.13` tag. Forge freezes it
  post-FG-032 — rollback would mean freezing beaconry on an unpatched
  Python minor.
- `pip install --target + PYTHONPATH` approach. Valid, but changes
  sys.path convention (env-var prepend vs. the native stdlib-detected
  path). Stage-at-runtime-path keeps the convention.
- Baking a `HEALTHCHECK` in Dockerfiles. Compose-level per BC-097.
- Any grype/syft/docker CLI changes.
- Memory docs rewrite (prose-only follow-up).

---

## Design decisions

- **Shell detection via `python3 -c`** rather than Dockerfile `ARG`:
  the user requirement is explicit — zero version literals. An ARG
  with a default (even unused) is still a literal. The interpreter
  answers "what minor am I" authoritatively with no drift risk.
- **`mv` not `cp`**: site-packages can be large (~50 MB hub, ~30 MB
  scout). `mv` is a rename in the overlay filesystem — O(1), no
  double space cost in the builder layer.
- **Move step placement**: AFTER the existing `syft dir:/install`
  step. If the move ran first, syft would scan an empty `/install`
  and produce an empty SBOM. Order is **pip install → source COPY
  → syft → mkdir /stage/data → PY_VER move**. Verified below in
  Testing strategy.
- **`COPY --from=builder /stage /`** vs. per-subdir COPYs: collapses
  what was two lines in hub (`/stage/data` + `/install/lib/.../site-packages`)
  and one in scout into one line. Also future-proofs for additional
  staged assets in later stories — just `mv/cp` them under `/stage`
  and the runtime picks them up.
- **Ownership preservation**: `/stage/data` is `chown -R 65532:65532
  /stage/data` in the hub builder; Docker COPY from a stage preserves
  source ownership by default when `--chown` is omitted. The dropped
  `COPY --from=builder --chown=65532:65532 /stage/data /data` was
  belt-and-braces; the chown-in-builder is the authoritative source.
- **Prose drift**: refreshed to strip `3.13.18` literals and past-tense
  the BC-097 prediction. This matches the user's instruction and
  retires the paper trail that would otherwise read "this will break"
  forever.
- **Not touching `memory/docker-standard.md`'s Python version**:
  that template is for new services (Python version is a
  consumer decision there); not a beaconry constraint.

---

## Breaking changes

### 1. API / return shape

**None.** HTTP contracts, callback_data, audit event names, SBOM
shape unchanged.

### 2. Data model (schema + legacy rows)

**None.** No schema changes.

### 3. Behavioral semantics

- **Build-time behavior changes.** Pre-BC-098: build succeeds iff
  the forge images are Python 3.13. Post-BC-098: build succeeds iff
  forge-builder and python-scratch-trixie ship the **same** minor as
  each other (which forge guarantees by shipping FG-032 as a paired
  bump). If a future forge release ever let the two drift, cold-start
  smoke still fails loudly — same oracle, same remedy.
- **Runtime behavior identical.** Site-packages lands at the exact
  path Python's native sys.path expects — no PYTHONPATH env var, no
  import hook. A `python3 -c "import fastapi"` inside the hub
  container behaves the same before and after.
- **SBOM content identical.** `syft dir:/install` runs before the
  move; the SBOM sees the same tree of wheels.

---

## Merge plan

1. All work on `feat/BC-098-python-version-detect`.
2. `make check` + `make check-full` pass locally (cold-start import
   probes are the oracle).
3. PR to `dev`. Per user direction: **hold the merge until the user
   confirms the diff** — forge's release pipeline is blocked on this,
   so a bad merge would cascade.
4. Squash-merge once user confirms.
5. Post-merge: INDEX flipped to `shipped`.
6. Signal forge-side: PR #60 can promote FG-032 to forge main; next
   daily cron republishes `:latest` with Python 3.14; beaconry's
   next `make live-rebuild` picks it up transparently.

---

## Rollout plan

- **Today** (Python 3.13.18 images): beaconry on this branch builds
  the same byte-for-byte runtime output as pre-BC-098. Only path is
  the staging layer in the builder.
- **After forge FG-032 lands + next daily cron**: forge publishes
  `python-scratch-trixie:latest` + `forge-builder-trixie:latest`
  with Python 3.14. Next beaconry `make live-rebuild` or next homelab
  `make deploy-beaconry`: runtime stays on `:latest`, builder stays
  on `:latest`, PY_VER reads `3.14`, mv targets `/stage/usr/local/lib/python3.14/site-packages`,
  runtime's sys.path finds it. No beaconry change.
- **Operator action**: none, unless they had pinned the forge tag
  via `BEACONRY_IMAGE_TAG` (BC-094). Those operators rebuild/pull
  explicitly on their schedule.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| G1 — No `python3.X` literal in either Dockerfile | unit | `test_BC_098_python_version_detect.py::test_no_python_minor_literals_in_instructions` | ✓ |
| G2 — Builder PY_VER shell step present | unit | `test_BC_098_python_version_detect.py::test_py_ver_detect_step_present_in_both` | ✓ |
| G3 — Runtime uses `COPY --from=builder /stage /` | unit | `test_BC_098_python_version_detect.py::test_runtime_copies_stage_root` | ✓ |
| G3b — No `/install/lib/python3.X/...` COPY remains | unit | `test_BC_098_python_version_detect.py::test_no_legacy_install_lib_copy_remains` | ✓ |
| G4 — Cold-start imports every runtime dep | integration | `test_BC_083_forge_runtime.py::test_hub_can_import_all_runtime_deps` + scout equivalent (run under `make check-full`) | ✓ |
| G5 — SBOM still captures wheels | gate log | `make dogfood-sbom` runs grype against the baked SBOM; same artifact count as pre-BC-098 | ✓ |
| G6 — CAP-T200 registered + CAP-T199 past-tense | lint | `make verify-traceability` | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | TOML diff / N/A | N/A — no documented behavior deprecated; BC-097's risk note is retired in prose but no banned pattern needed | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

---

## Testing strategy

- Unit tests grep both Dockerfiles for the exact forbidden /
  required patterns. Deterministic, no Docker needed.
- Full tier (`make check-full`) brings up the cold-start compose
  stack with fresh volumes + real forge images. The import-probe
  tests exercise the end-to-end property: site-packages reachable
  from Python's sys.path, every top-level requirement imports
  cleanly. If PY_VER resolution or mv target is wrong, every import
  fails and the probe exits non-zero.
- `make dogfood-sbom` (BC-096) exercises `grype sbom:/sbom.json`
  against the baked-in SBOM — confirms syft still saw the wheels
  pre-move.

---

## Testing commands

```bash
# Fast tier — lint + unit tests + traceability
make check

# Full tier — builds images with the new pattern, runs cold-start + scenarios + e2e
BEACONRY_SKIP_DOGFOOD=1 make check-full
# (BEACONRY_SKIP_DOGFOOD=1 skips the pre-existing syft v1.57.0 brotli
#  gate — tracked in BC-095/096/097 PR bodies.)

# Spot-check the built image's site-packages location
docker inspect beaconry-hub:latest --format '{{json .Config.Env}}'
docker run --rm --entrypoint /usr/local/bin/python3 beaconry-hub:latest \
  -c "import sys; print(sys.version_info); import fastapi; print('ok')"
```

Expected: `sys.version_info(major=3, minor=13, ...)` + `ok` today;
`...minor=14, ...` + `ok` after forge publishes FG-032.

---

## Links

- Story: `docs/stories/BC-098-python-version-detect.md`
- Forge FG-032 (counterpart): `~/git/forge/docs/stories/FG-032-python-3.14-bump.md`
- Forge PR #60: `https://github.com/bboortz/forge/pull/60`
- BC-097 (predecessor — documented exactly this risk)
- BC-083 (`test_{hub,scout}_can_import_all_runtime_deps` — oracle)
- BC-082 (cold-start smoke)
