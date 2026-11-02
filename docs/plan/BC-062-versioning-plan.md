# BC-062 — Versioning (software + API + schema)

*Branch: `feat/BC-062-versioning`*
*Start: 2026-11-02*

## Scope

### `VERSION` file + loader

- `VERSION` at repo root: `0.1.0`.
- `poc/beaconry_health/version.py` (shared between hub + scout since
  `beaconry_health` is already COPY'd into both images):
  ```python
  def read_version() -> str:
      p = Path(__file__).resolve().parent.parent / "VERSION"
      if not p.exists():
          # Container path — VERSION COPY'd next to the package.
          p = Path("/app/VERSION")
      return p.read_text().strip() if p.exists() else "0.0.0+unknown"
  ```
- `hub.py` / `scout.py` / `poc/scripts/cli_config.py` all import and
  surface `SOFTWARE_VERSION`.
- `verify-version-sync`: lint reads `VERSION`, greps for literal
  version-looking string in the three component files; fails if any
  carries a stale literal.

### API version

- `API_VERSION = 1` in `hub.py`. `CLIENT_API_VERSION = 1` in
  `poc/scout/scout.py` + `poc/scripts/cli_http.py`.
- Hub FastAPI middleware adds `X-Beaconry-API: 1` header on every
  response.
- Hub middleware also inspects `X-Beaconry-API-Expect`:
  - Missing → do nothing (unversioned client — probably curl).
  - Present + equal to API_VERSION → do nothing.
  - Present + different → log WARNING. If `BEACONRY_STRICT_API=1`,
    short-circuit with `412 Precondition Failed`. Else continue.
- Scout POST path (`scout.py::post_to_hub`) sends
  `X-Beaconry-API-Expect: 1`.
- CLI (`cli_http.py::_build_request`) adds the header on every call.

### Schema version

- `EXPECTED_SCHEMA_VERSION = 2` constant in `hub.py`.
- `SchemaTooNew(Exception)` for the hard-fail case.
- `init_db` reads `PRAGMA user_version`:
  - 0 → treat as pre-BC-062 (either empty DB or legacy, migrate both to v2).
  - 1 → run v1→v2 migrations (idempotent — v2 is just the scouts table
    + backfill, which `init_db` already does).
  - 2 → done.
  - > 2 → raise `SchemaTooNew`.
- After migration, `PRAGMA user_version = EXPECTED_SCHEMA_VERSION`.

### `/healthz` version block

```python
def _hub_healthz_summary() -> dict:
    …
    return {
        "status": …,
        "version": {
            "software": SOFTWARE_VERSION,
            "api": API_VERSION,
            "schema": EXPECTED_SCHEMA_VERSION,
        },
        …
    }
```

Scout `/healthz` returns the first two (no schema).

### `make status` VERSION column

`status.py` adds a VERSION column reading `body["version"]`:

```
beaconry-hub    running  healthy  0.1.0 · api1 · sch2  …
beaconry-scout  running  healthy  0.1.0 · api1         …
```

If `CLIENT_API_VERSION != body["version"]["api"]`, append a trailing
line to the output:

```
⚠ api mismatch: cli expects api1, hub serves api2 — upgrade CLI
```

### Dockerfile changes

Both `poc/hub/Dockerfile` and `poc/scout/Dockerfile`:

```dockerfile
COPY VERSION ./VERSION
```

### Env var

`BEACONRY_STRICT_API` — unset or `0` → soft, `1` → hard-fail with 412.

### Env + docs

- `docs/VARIABLES.md`: row for `BEACONRY_STRICT_API`.
- `poc/.env.example`: line for it.
- `docs/INSTALL.md`: section mentioning version checks (brief).
- `docker-compose.yml`: propagate `BEACONRY_STRICT_API`.

## Design decisions

### Why PRAGMA user_version, not a `schema_version` table

- Built into SQLite; atomic with the transaction that applied the
  migration.
- No extra read/write per `init_db` call.
- Grep-visible from the shell: `sqlite3 beaconry.db 'PRAGMA user_version'`.

### Why integer API version, not SemVer

Operator-visible compat question is "do I need to upgrade?" — a yes/no
captured by equality. SemVer "major.minor.patch" invites debate about
what counts as breaking.

### Why soft-default mismatch

Rolling upgrades mix versions briefly by nature. Hard-failing in the
default mode would make routine operations painful. Operators who
genuinely want zero version drift set `BEACONRY_STRICT_API=1`.

### Why fail-fast on schema-too-new

Downgrading hub (old code) against an upgraded DB (new column shapes,
new tables) risks:
- Writing rows the newer code expects, in a shape the old code can't
  consume on next read.
- Silent data loss if the old code's SELECT doesn't name a column that
  carries live state.

Refusing to boot forces the operator to decide: either upgrade the hub
or accept the data loss by wiping the volume. Non-negotiable.

### Why "soft" on mismatch means logging a WARNING

`make audit` could carry a `version_mismatch_detected` event too, but
that'd double-record the structured logs → not worth it for v1 of this
story.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| /VERSION and /poc/VERSION byte-equal | integration | `test_BC_062_versioning.py::test_version_files_are_in_sync` | ✓ |
| SOFTWARE_VERSION loader matches VERSION file | integration | `test_BC_062_versioning.py::test_loader_matches_version_file` | ✓ |
| verify-version-sync passes on live repo | integration | `test_BC_062_versioning.py::test_verify_version_sync_passes_live` | ✓ |
| verify-version-sync catches /VERSION ↔ poc/VERSION drift | integration | `test_BC_062_versioning.py::test_verify_version_sync_catches_drift` | ✓ |
| verify-version-sync catches hardcoded literal drift | integration | `test_BC_062_versioning.py::test_verify_version_sync_catches_hardcoded_literal` | ✓ |
| verify-version-sync CLI exits 0 on live repo | integration | `test_BC_062_versioning.py::test_verify_version_sync_cli_exits_zero` | ✓ |
| Hub response carries X-Beaconry-API header | integration | `test_BC_062_versioning.py::test_hub_response_carries_api_header` | ✓ |
| /healthz carries version block (software/api/schema) | integration | `test_BC_062_versioning.py::test_healthz_carries_version_block` | ✓ |
| API mismatch soft: WARNING logged, 200 returned | integration | `test_BC_062_versioning.py::test_api_mismatch_soft_passes` | ✓ |
| API mismatch strict: 412 returned | integration | `test_BC_062_versioning.py::test_api_mismatch_strict_412` | ✓ |
| API match strict: 200 returned | integration | `test_BC_062_versioning.py::test_api_match_strict_passes` | ✓ |
| Missing X-Beaconry-API-Expect never fails | integration | `test_BC_062_versioning.py::test_missing_expect_header_never_fails` | ✓ |
| Fresh DB → PRAGMA = EXPECTED (2) | integration | `test_BC_062_versioning.py::test_fresh_db_sets_pragma_to_expected` | ✓ |
| Legacy DB (PRAGMA=0) migrates to EXPECTED | integration | `test_BC_062_versioning.py::test_legacy_db_migrates_to_expected` | ✓ |
| Newer DB (PRAGMA=99) raises SchemaTooNew | integration | `test_BC_062_versioning.py::test_newer_db_raises_schema_too_new` | ✓ |
| make status renders VERSION column | integration | `test_BC_062_versioning.py::test_make_status_renders_version_column` | ✓ |
| make status surfaces API mismatch warning | integration | `test_BC_062_versioning.py::test_make_status_surfaces_api_mismatch` | ✓ |
| make status degrades legacy "version": "x.y.z" string | integration | `test_BC_062_versioning.py::test_make_status_legacy_version_string_degrades_gracefully` | ✓ |
| Scout module declares CLIENT_API_VERSION=1 | integration | `test_BC_062_versioning.py::test_scout_module_declares_client_api_version` | ✓ |
| cli_http module declares CLIENT_API_VERSION=1 | integration | `test_BC_062_versioning.py::test_cli_http_declares_client_api_version` | ✓ |
| cli_http._build_request sends X-Beaconry-API-Expect | integration | `test_BC_062_versioning.py::test_cli_build_request_sends_expect_header` | ✓ |
| Scout-style POST with mismatched Expect header round-trips (soft) | integration | `test_BC_062_versioning.py::test_scout_style_post_header_round_trip` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

Legend: ✓ covered · ❌ open.

## Follow-up — auto-bump + pre-push verification

Operator requested, same ship:

- `make bump-patch` / `bump-minor` / `bump-major` — manual SemVer bump
  of `/VERSION` + `/poc/VERSION` (atomic 2-file update).
- `make bump-auto` — infers kind from `git log origin/dev..HEAD`:
  `BREAKING CHANGE` or `!:` → major, any `feat` → minor, else patch.
- `make verify-version-bumped` (and pre-push hook) — refuses to push if
  `/VERSION` is unchanged relative to the branch's merge-base with
  `origin/dev`. Also catches a backwards bump. On `dev` / `main` the
  check is a no-op (merge targets, not sources). Emergency bypass:
  `BEACONRY_SKIP_VERSION_BUMP=1`.

Added rows:

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| bump() produces correct patch/minor/major | unit | `test_bump_version.py::test_bump_cases` | ✓ |
| bump() rejects non-SemVer input | unit | `test_bump_version.py::test_bump_rejects_non_semver` | ✓ |
| bump-auto infers feat → minor | unit | `test_bump_version.py::test_infer_feat_commit_bumps_minor` | ✓ |
| bump-auto infers `!:` → major | unit | `test_bump_version.py::test_infer_bang_commit_bumps_major` | ✓ |
| bump-auto infers BREAKING CHANGE → major | unit | `test_bump_version.py::test_infer_breaking_change_footer_bumps_major` | ✓ |
| bump-auto picks strongest when mixed | unit | `test_bump_version.py::test_infer_mixed_commits_prefers_strongest` | ✓ |
| write() updates both VERSION + poc/VERSION | unit | `test_bump_version.py::test_write_updates_both_files` | ✓ |
| verify blocks push when VERSION unchanged | unit | `test_bump_version.py::test_verify_fails_when_version_unchanged` | ✓ |
| verify blocks push on backwards version | unit | `test_bump_version.py::test_verify_fails_when_version_went_backwards` | ✓ |
| verify passes on dev/main branch | unit | `test_bump_version.py::test_verify_pass_on_dev_branch_itself` | ✓ |
| verify respects BEACONRY_SKIP_VERSION_BUMP | unit | `test_bump_version.py::test_verify_pass_with_skip_env` | ✓ |
| pre-push hook registered in .pre-commit-config.yaml | code | `.pre-commit-config.yaml#verify-version-bumped` | ✓ |
