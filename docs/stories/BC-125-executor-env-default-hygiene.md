---
id: BC-125
title: EXECUTOR env default hygiene — empty string renders "manual", not blank
status: shipped
created: 2026-11-16
branch: feat/BC-125-executor-env-default-hygiene
plan: docs/plan/BC-125-executor-env-default-hygiene-plan.md
---

# BC-125: `beaconry status` POLICIES line shows EXECUTOR correctly

## Narrative

Operator ran `beaconry status` right after v0.22.8 deployed. The new
POLICIES line (BC-123) showed every knob populated except one:

```
POLICIES: MIN_CVSS=7.0 · MAX_FINDINGS_PER_RUN=100 · AUTO_FIX_THRESHOLD=3 · SCAN_INTERVAL=300s · EXECUTOR=
```

**`EXECUTOR=` empty.** The hub IS running the manual executor — that's
the Python-level fallback — but the operator can't tell. Looks like a
bug in beaconry.

Root cause chain:

- `poc/docker-compose.yml` has
  `- BEACONRY_EXECUTOR=${BEACONRY_EXECUTOR:-}`. The `:-` fallback is
  an empty string, not `manual`. When homelab's `hub.env.j2` doesn't
  set the env (it doesn't — the operator has no reason to), compose
  passes the empty string into the container.
- `poc/hub/hub.py::500` does
  `EXECUTOR_NAME = os.environ.get("BEACONRY_EXECUTOR", "manual").lower()`.
  `os.environ.get` returns `""` (not the default) because the key IS
  present, just with an empty value.
- `_executor_registry.get("", ManualAdapter())` falls through to the
  ManualAdapter fallback — so manual IS running — but
  `EXECUTOR_NAME == ""` is what `/healthz.checks.executor` + the
  POLICIES line render.

Post-BC-125: both layers default to `manual` when the env is unset
OR empty. Belt-and-suspenders prevents a future compose/env source
from reintroducing the same ambiguity.

## Trigger

- Fresh hub boot where `BEACONRY_EXECUTOR` is unset or empty (the
  most common operator path — manual is the recommended default).
- `beaconry status` or `curl /healthz` on any such hub.

## Expected outcome

- `POLICIES: … · EXECUTOR=manual` on every fresh hub.
- `/healthz.checks.executor == "manual"` (not `""`).
- Operators who explicitly set `BEACONRY_EXECUTOR=docker` still see
  `EXECUTOR=docker` — the fix only normalizes the empty case.

## Out of scope

- Removing the `_executor_registry.get(..., ManualAdapter())`
  fallback. Keep as a defensive no-match safeguard.
- Adding more executors. The registry still has `manual` + `docker`
  as the only entries.
- Homelab-side explicit `BEACONRY_EXECUTOR=manual` in `hub.env.j2`
  — nice-to-have, but BC-125 fixes it at the beaconry source so no
  operator ever needs to touch it.

## Acceptance criteria (Gherkin)

```gherkin
Feature: EXECUTOR default hygiene

  Scenario: Unset env → manual
    Given BEACONRY_EXECUTOR is unset
    When the hub imports
    Then EXECUTOR_NAME == "manual"
    And /healthz.checks.executor == "manual"
    And POLICIES line shows EXECUTOR=manual

  Scenario: Empty-string env → manual
    Given BEACONRY_EXECUTOR="" (what compose's :- fallback produces)
    When the hub imports
    Then EXECUTOR_NAME == "manual"
    And /healthz.checks.executor == "manual"

  Scenario: Explicit "docker" → docker
    Given BEACONRY_EXECUTOR=docker
    When the hub imports
    Then EXECUTOR_NAME == "docker"
    And /healthz.checks.executor == "docker"

  Scenario: compose default fallback is now "manual"
    Given a fresh clone of beaconry
    When `cd poc && docker compose config` renders the hub service
    Then BEACONRY_EXECUTOR=manual (the :- fallback)

  Scenario: Invalid value stays defensive
    Given BEACONRY_EXECUTOR=garbage
    When the hub imports
    Then EXECUTOR is the ManualAdapter fallback (registry.get(..., ManualAdapter()))
    And EXECUTOR_NAME == "garbage" (unchanged — operator sees what they set)
```

## Capabilities delivered

- **CAP-T228: EXECUTOR env default hygiene**
  (type: technical, status: active)

  *Two-layer fix. `poc/docker-compose.yml` changes the hub service's
  `- BEACONRY_EXECUTOR=${BEACONRY_EXECUTOR:-}` to `- BEACONRY_EXECUTOR=${BEACONRY_EXECUTOR:-manual}`
  so every compose-based deploy (dev + homelab + future operators)
  gets the correct default without needing the env set upstream.
  `poc/hub/hub.py::500` changes
  `EXECUTOR_NAME = os.environ.get("BEACONRY_EXECUTOR", "manual").lower()`
  to `EXECUTOR_NAME = (os.environ.get("BEACONRY_EXECUTOR") or "manual").lower()`
  so an empty-string env falls through to manual (previously
  `os.environ.get` returned `""`, not the Python default). Fixes
  the BC-123 POLICIES line showing `EXECUTOR=` after v0.22.8 deploy
  without operator-side config changes.*

Extends CAP-T185 (BEACONRY_EXECUTOR config knob, BC-066) and
CAP-T224 (POLICIES line, BC-123).

## Open questions

Resolved during drafting (2026-11-16):

1. Compose fix OR Python fix OR both?
   **Both.** Compose fix is universal (every deploy, including
   ones that bypass hub.py changes); Python fix is belt-and-suspenders
   against a future env source that reintroduces the empty string.
2. Should an invalid value (`BEACONRY_EXECUTOR=foo`) raise?
   **No.** Keep the registry.get fallback to ManualAdapter —
   operator still sees `EXECUTOR=foo` in the POLICIES line so they
   know to fix their config; the hub keeps running manually.
3. Homelab-side change too?
   **Not in this story.** BC-125 fixes it at source; homelab doesn't
   need to care. If operator wants to be explicit, they can set
   `beaconry_hub_executor: "manual"` later (would be a separate PR).

## Links

- Related: BC-066 (EXECUTOR adapter architecture), BC-123 (POLICIES
  line surfacing the gap).
