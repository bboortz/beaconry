---
id: BC-080
title: make cleanup — teardown + image removal after tests
status: shipped
created: 2026-11-03
branch: feat/BC-080-test-cleanup-tooling
plan: docs/plan/BC-080-test-cleanup-tooling-plan.md
---

# BC-080: `make cleanup` — teardown + image removal after tests

Current test harnesses tear down containers + volumes but leave
images behind. Scout then scans the leftover images on its next
cycle, grype matches, and operators get Telegram alerts for
`redis:6.2.0`, `nginx:1.24`, `postgres:latest`, `beaconry-hub:ci-test`
etc. — none of which are actually deployed services.

Pre-BC-080 the suggested fix was CLAUDE.md guidance ("remember to
clean up"). That's instructions-as-safety: easy to forget, works
only for Claude, no gate.

BC-080 replaces the guidance with a concrete `make cleanup` target
+ auto-invocation from `make check-full` / `make test-e2e` /
`make test-scenarios`.

## Operator / developer scenario

**Who**: developer running `make check-full`. Or operator triggering
`make test-e2e` manually.

**When**: any time the test harness pulls or builds fixture images.

**Success**: when the harness exits (success or failure), the
host is left clean — no `beaconry-hub:ci-test`, no
`beaconry-hub:scenarios`, no `beaconry-test-telegram-mock`, no
leaked fixture tags like `nginx:1.24`. Operator sees no
Telegram false positives from "fleet image" alerts.

**Urgency**: Tier 2 — not an active CVE, but every false-positive
notification erodes operator trust in real alerts.

## Gherkin

```
Feature: test cleanup tooling

Scenario: make cleanup removes test-only images
  Given `make test-e2e` has run and left `beaconry-hub:ci-test`
   and `beaconry-test-telegram-mock:latest` on the host
  When `make cleanup` runs
  Then both images are removed via `docker image rm`
   And `beaconry-hub:latest` + `beaconry-scout:latest` are preserved
   And `make cleanup` reports count of removed images + exits 0

Scenario: cleanup fires on test-e2e exit (success or failure)
  Given `make test-e2e` is wrapped in a trap EXIT
  When pytest exits with non-zero (test failure)
  Then cleanup still runs before the recipe exits
   And the final exit code is still non-zero (test failure propagates)

Scenario: make check-full auto-cleans after container tiers
  Given `make check-full` runs test-e2e + test-scenarios + CLI-smoke
  When all tiers finish (or any fails)
  Then `make cleanup` runs as the last step
   And host is clean before the recipe returns

Scenario: operator's live stack is untouched
  Given operator has `beaconry-hub:latest` + `beaconry-scout:latest`
   deployed and the stack is up (make live-rebuild run earlier)
  When `make cleanup` fires
  Then `beaconry-hub` + `beaconry-scout` containers keep running
   And `beaconry-hub:latest` + `beaconry-scout:latest` tags survive
```

## Capabilities

- **CAP-T164** (new): `make cleanup` — tears down beaconry-test
  + beaconry-scenarios compose projects, removes test-only
  images (`beaconry-hub:ci-test`, `beaconry-hub:scenarios`,
  `beaconry-test-*`), removes test-fixture images beaconry's own
  tests pulled (`nginx:1.24`, `nginx:alpine`, probe targets).
  Never touches `beaconry-hub:latest` / `beaconry-scout:latest`
  or operator's actual service images.
- **CAP-T165** (new): `make check-full` / `make test-e2e` /
  `make test-scenarios` auto-invoke `make cleanup` on EXIT
  (success or failure), so container regressions never leak
  false-positive findings into the operator's live hub.

## Out of scope

- `SCOUT_EXCLUDE` defaults for beaconry's own test images in
  `docker-compose.yml`. Would be defense in depth, but the user
  wants it operator-controlled. Future BC if the cleanup gate
  ever misses an edge case.
- Scout-side auto-dismiss of findings on images that get removed
  immediately after being scanned (the "it was gone before you
  saw the alert" race).
