# BC-040 — Three-way env sync lint

*Branch: `feat/BC-040-three-way-env-sync`*
*Start date: 2026-10-31*
*Story: `docs/stories/BC-040-three-way-env-sync.md`*

---

## Situation

BC-039 made `VARIABLES.md` the authoritative doc and `make verify-variables`
catches code↔doc drift. BC-011 catches hub-code↔compose drift. The third
pair — `VARIABLES.md` ↔ `.env.example` + `VARIABLES.md` ↔ `docker-compose.yml`
— is not asserted. `.env.example` today ships with 7 variables; `VARIABLES.md`
has 32. An operator starting from `.env.example` discovers Heartbeat / Digest /
Internet-facing dials only by stumbling.

---

## Problems to solve

- `.env.example` is incomplete vs `VARIABLES.md`.
- `.env.example` default could drift from `VARIABLES.md` default silently.
- Variable could land in `docker-compose.yml`'s `environment:` lists without
  a `VARIABLES.md` row (BC-011 only catches the reverse: compose missing a
  var that code reads).
- No single command tells the developer "all three surfaces are aligned".

---

## Goals

1. Rewrite `poc/.env.example` as the full operator catalog in the format the
   user already uses in their `.env` — grouped headers, `VAR=default` lines,
   aligned `# default | description` comment column.
2. Extend `poc/scripts/verify_variables_documented.py` with three new checks
   (details in Design decisions below).
3. Explicit `.env.example` dev-only allowlist for variables that shouldn't
   show up in operator config (scenario harness URLs, test-mode flag).
4. No new make target — `make verify-variables` already runs the lint; the
   additional checks fold into its existing exit code.

---

## Scope

- NEW/REWRITE `poc/.env.example` — ~30 variables in the proposed format.
- `poc/scripts/verify_variables_documented.py` — add `.env.example` parser,
  `docker-compose.yml` env-propagation parser (reuse BC-011's shape), 4
  new violation types, dev-only allowlist constant.
- `poc/tests/unit/test_verify_variables_documented.py` — add 8 unit tests
  covering the new checks + 1 live-state regression guard.
- `docs/CAPABILITIES.md` — register CAP-T086, CAP-T087, CAP-B024.
- No change to `make check` (already includes `verify-variables`).

---

## Non-goals

- Rewriting `docker-compose.yml`'s env propagation. BC-011 already enforces
  "hub code reads X ↔ compose propagates X"; BC-040 just adds "compose
  propagates X ↔ VARIABLES.md documents X".
- Making `.env.example` the single source of truth. `VARIABLES.md` is the
  index; `.env.example` is the operator starter. The lint enforces alignment
  between them, not direction.
- Auto-generating `.env.example` from `VARIABLES.md`. Hand-maintained with
  lint is simpler + PR-reviewable (same reasoning as BC-039).
- Fancy formatting (TOML, YAML). Flat shell-compatible format matches
  Compose's `env_file` consumption + operator's existing muscle memory.

---

## Design decisions

### Four new violation types

| # | Rule | Example error |
|---|---|---|
| A | VARIABLES.md row missing from `.env.example` (unless `DEV_ONLY_ALLOWLIST`-ed) | `` `BEACONRY_X` documented but missing from `.env.example` — add line (template given)`` |
| B | `.env.example` line missing from VARIABLES.md | `` `.env.example` has `BEACONRY_Y=` but no VARIABLES.md row — document in VARIABLES.md first`` |
| C | compose `environment:` entry missing from VARIABLES.md | `` `docker-compose.yml` propagates `BEACONRY_Z` to `hub` but no VARIABLES.md row`` |
| D | Default mismatch between VARIABLES.md and `.env.example` for literals | ``MIN_CVSS: VARIABLES.md=`7.0`, .env.example=`9.0`. Decide which is authoritative + align both.`` |

### `.env.example` dev-only allowlist

Variables that shouldn't appear in operator config but still exist for
developer workflows:

```
BEACONRY_TEST_MODE           # Dangerous in prod — intentionally undocumented in .env.example
BEACONRY_SCENARIO_HUB_URL    # BC-038 scenario harness; only set by poc/scenarios/run.sh
BEACONRY_SCENARIO_DATA_DIR   # Same
BEACONRY_HUB_HEALTHZ_URL     # Test fixture consumer (already on TEST_ONLY_ALLOWLIST for scanner)
BEACONRY_SCOUT_HEALTHZ_URL   # Same
```

### Default-value comparison

Three categories of VARIABLES.md default cells, matched differently:

1. **Literal defaults** (`7.0`, `300`, `1`, `86400`, `/data`) → strict string
   equality after trimming.
2. **Wrapped literals** (`604800` + ` (7 d)` suffix) → extract first bare
   literal with regex, compare.
3. **Prose defaults** (`*(empty)*`, `*(grype default)*`, `uname().nodename`,
   `CISA upstream`, `derived from BEACONRY_MOCK`) → not compared; the
   `.env.example` line is expected to use an angle-bracket placeholder OR
   empty value, no equality check attempted.

Comparison skipped if `.env.example` value is `<angle-bracket placeholder>` —
operator-filled, no equality meaningful.

### Rejected alternatives

- **Fail-closed on prose defaults** — would force every doc default to be
  machine-parsable. Too much churn in VARIABLES.md for marginal signal; the
  three-category softening buys most of the value.
- **Common env parser library** — `.env.example` is simple enough
  (`KEY=value` + `#` comments) that a 15-line regex parser is clearer than
  pulling in `python-dotenv` as a dependency.
- **Three-way graph diff** — overkill for 30 variables. Pairwise checks are
  simpler to explain in error messages.

---

## Breaking changes

### 1. API / return shape

**None.** No code-interface changes.

### 2. Data model

**None.**

### 3. Behavioral semantics

- Developers who do nothing: fast tier now also enforces .env.example ↔
  VARIABLES.md ↔ docker-compose.yml alignment. The initial pass of this
  branch brings all three into sync so the lint turns on clean.
- Operators who had an existing `.env`: no effect — the lint reads
  `.env.example`, not their local `.env`. Their file remains untouched.
- Future BC that adds a variable: must update `VARIABLES.md` + `.env.example`
  + `docker-compose.yml` + code in the same PR, or `make check` fails. This
  is the point.

---

## Merge plan

1. All work on `feat/BC-040-three-way-env-sync`.
2. `make check` + `make check-full` pass.
3. PR to `dev`; Claude opens, user runs `/ultrareview`, Claude runs `/code-review`.
4. Merge strategy: fast-forward.
5. Post-merge: story → `shipped`, INDEX update.

---

## Rollout plan

Additive. Operators who do nothing see no change — their `.env` is untouched.
The rewritten `.env.example` is a copy-pasteable starter that captures every
dial; developers consult it when adding to their own `.env`.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Clean-state live repo passes the full three-way lint | unit | `test_verify_variables_documented.py::test_shipped_repo_passes` (extended) | ✓ |
| VARIABLES.md row missing from .env.example → violation | unit | `test_verify_variables_documented.py::test_var_missing_from_env_example` | ✓ |
| .env.example line not in VARIABLES.md → violation | unit | `test_verify_variables_documented.py::test_env_example_orphan` | ✓ |
| compose env propagation not in VARIABLES.md → violation | unit | `test_verify_variables_documented.py::test_compose_env_orphan` | ✓ |
| Default mismatch on literal → violation | unit | `test_verify_variables_documented.py::test_default_mismatch_literal` | ✓ |
| Angle-bracket placeholder skips default comparison | unit | `test_verify_variables_documented.py::test_placeholder_skips_default_check` | ✓ |
| Dev-only allowlist suppresses "missing from .env.example" | unit | `test_verify_variables_documented.py::test_dev_only_allowlist_suppresses_env_example_check` | ✓ |
| Prose defaults (`*(empty)*`, `<CISA URL>`) match a prose or empty .env.example value | unit | `test_verify_variables_documented.py::test_prose_defaults_match_loosely` | ✓ |
| .env.example parser handles comments, blank lines, group headers | unit | `test_verify_variables_documented.py::test_parse_env_example_strips_comments` | ✓ |
| compose parser extracts hub + scout `environment:` entries | unit | `test_verify_variables_documented.py::test_parse_compose_environment_extracts_hub_and_scout` | ✓ |
| `.env.example` ships with full operator catalog in agreed format | manual | `poc/.env.example` diff | ✓ |
| CAP-T086 + CAP-T087 + CAP-B024 registered + bidirectional | verify-traceability | `docs/CAPABILITIES.md` diff | ✓ |
| make check green | multi-gate | `make check` | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

---

## Testing strategy

Unit-only. Each new parser (env file, compose env) has round-trip tests.
Each violation branch has a scenario-level test. Live-repo regression guard
runs the full lint over the shipped state.

---

## Testing commands

```bash
make check
make verify-variables
pytest poc/tests/unit/test_verify_variables_documented.py -v
```
