---
id: BC-062
title: Software + API + schema versioning
status: shipped
created: 2026-11-02
branch: feat/BC-062-versioning
plan: docs/plan/BC-062-versioning-plan.md
---

# BC-062: Versioning

## Narrative

Beaconry has shipped 61 BCs without any explicit versioning. Three
version concerns are now distinct enough to need separate tracking:

1. **Software version** — "which release is running?" Operator
   coordinating a rolling upgrade across laptop + hetzner-staging
   needs to compare.
2. **API version** — "does the CLI I'm running understand what the
   hub speaks?" Scouts and operator CLIs will diverge in the field
   when breaking endpoint changes land.
3. **Schema version** — "is my DB compatible with the hub binary
   running against it?" Downgrading to an older hub against a newer
   DB is a corruption hazard that's currently silent.

Add three independent version axes, surface them in `/healthz` +
`make status`, and build the plumbing for coordinated upgrades.

## Trigger

Operator will deploy BC-061's `scouts`-table change to hetzner-staging.
That's a schema change — the first one with persistent state the
operator is responsible for. Needs explicit versioning before the
rollout to catch mismatches cleanly instead of after.

## Expected outcome

### Software version

- Authoritative source: `VERSION` file at repo root (`0.1.0`).
- Hub, scout, and operator CLIs all read it at import.
- `verify-version-sync` lint fails if any component carries a different
  literal than `VERSION`.
- Dockerfiles `COPY VERSION .` so container images carry it too.

### API version

- `API_VERSION = 1` constant in hub.py (and `CLIENT_API_VERSION = 1` in
  scout + CLI).
- Every hub HTTP response carries `X-Beaconry-API: 1` header.
- Clients attach `X-Beaconry-API-Expect: 1` on requests.
- **Soft default**: mismatch logs a warning, request still goes
  through. `BEACONRY_STRICT_API=1` → hub returns `412 Precondition
  Failed` on any client-expected version that doesn't equal
  `API_VERSION`.

### Schema version

- SQLite `PRAGMA user_version` becomes the single source of truth.
- `EXPECTED_SCHEMA_VERSION = 2` (findings = 1 → +scouts table = 2).
- `init_db` branch:
  - Current == expected → done.
  - Current < expected → run migrations up to expected, set PRAGMA.
  - **Current > expected → hard-fail at startup** with a clear
    message. Running old code on new data risks silent corruption.

### Status surface

`/healthz` body gains:

```json
"version": {
  "software": "0.1.0",
  "api": 1,
  "schema": 2
}
```

`make status` renders a VERSION column:

```
NAME            STATE    HEALTH    VERSION               DETAILS
beaconry-hub    running  healthy   0.1.0 · api1 · sch2  grype_db ok · telegram polling
beaconry-scout  running  healthy   0.1.0 · api1          6 images · docker_socket ok
```

If CLI's `CLIENT_API_VERSION` differs from hub's `api_version`, trailing
line:

```
⚠ api mismatch: cli expects api1, hub serves api2 — upgrade CLI
```

## Out of scope

- Multi-API-version support (hub serving both v1 and v2 during a
  deprecation window). First breaking change will cross that bridge.
- Automatic down-migration (schema > expected). Operator chooses when
  to downgrade; we refuse to boot and log an actionable message.
- Per-feature flags (feature toggles decoupled from versioning).
- Signing / verifying the VERSION file (supply-chain hardening).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Versioning

  Scenario: VERSION file is the single source of truth
    When any component reports its software version
    Then it matches the content of `VERSION` at repo root

  Scenario: verify-version-sync catches drift
    Given hub.VERSION is edited to a different literal than VERSION
    When `make verify-version-sync` runs
    Then it exits non-zero naming the drifting component

  Scenario: Hub response carries X-Beaconry-API header
    Given any HTTP response from the hub
    Then the response has header `X-Beaconry-API: 1`

  Scenario: /healthz carries all three versions
    When GET /healthz arrives
    Then the response body includes `version.software`, `version.api`,
      `version.schema`

  Scenario: Client-expected API mismatch — soft default
    Given hub API_VERSION = 1
    When a request arrives with X-Beaconry-API-Expect: 2
    Then the response is 200 (soft mode) and a WARNING is logged

  Scenario: Client-expected API mismatch — strict
    Given BEACONRY_STRICT_API=1 and hub API_VERSION = 1
    When a request arrives with X-Beaconry-API-Expect: 2
    Then the response is 412 Precondition Failed

  Scenario: Schema PRAGMA bookkeeping on fresh DB
    Given a fresh DB
    When init_db runs
    Then PRAGMA user_version == EXPECTED_SCHEMA_VERSION (2)

  Scenario: Schema migration from older PRAGMA
    Given a DB with PRAGMA user_version = 0 (pre-BC-062)
    When init_db runs
    Then migrations run idempotently
    And PRAGMA user_version is set to EXPECTED_SCHEMA_VERSION

  Scenario: Schema PRAGMA refuses newer DB
    Given a DB with PRAGMA user_version = 99
    When init_db runs
    Then init_db raises SchemaTooNew with a clear message

  Scenario: make status renders VERSION column
    Given hub /healthz returns version.software=0.1.0, api=1, schema=2
    When operator runs make status
    Then the beaconry-hub row shows "0.1.0 · api1 · sch2"
    And no mismatch warning (CLI api version matches)

  Scenario: make status surfaces API mismatch
    Given hub serves api_version=2 but CLIENT_API_VERSION=1
    When operator runs make status
    Then output includes an "api mismatch" warning line
```

## Capabilities delivered

- **CAP-T124: `VERSION` file + verify-version-sync lint** (technical).
- **CAP-T125: Hub API version via header + `/healthz`; strict-mode gate** (technical).
- **CAP-T126: SQLite PRAGMA user_version + migration gate** (technical).
- **CAP-T127: `make status` VERSION column + mismatch warning** (technical).
- **CAP-T128: SemVer bump helper (`make bump-{patch,minor,major,auto}`) + pre-push enforcement** (technical).
- **CAP-B045: Operator can always see what's running + knows when a
  deployed component is out of sync** (business).

## Follow-up (same ship)

Operator asked for automated version bumping + enforcement on push:

- `make bump-patch` / `bump-minor` / `bump-major` — manual SemVer bump.
- `make bump-auto` — infer from conventional-commit messages since
  `origin/dev` (`feat` → minor, `!`/`BREAKING CHANGE` → major, else patch).
- `make verify-version-bumped` + `.pre-commit-config.yaml` pre-push
  hook — refuse to push if VERSION didn't move relative to the branch's
  merge-base with `origin/dev`. Emergency bypass: `BEACONRY_SKIP_VERSION_BUMP=1`.
- Also catches a backwards bump (new VERSION ≤ base VERSION).

## Links

- Related: BC-061 (schema version 2 — scouts table added).
- Follow-up: a `BEACONRY_FORCE_SCHEMA=1` escape hatch for operators
  who deliberately roll back and accept the risk.
