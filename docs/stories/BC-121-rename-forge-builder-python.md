---
id: BC-121
title: Track forge-builder-trixie → forge-builder-python-trixie rename
status: shipped
created: 2026-11-15
branch: fix/BC-121-rename-forge-builder-python
plan: docs/plan/BC-121-rename-forge-builder-python-plan.md
---

# BC-121: Track Forge's builder image rename

## Narrative

Forge's FG-056 renamed the Python-builder base from
`ghcr.io/bboortz/forge-builder-trixie` to
`ghcr.io/bboortz/forge-builder-python-trixie`, matching the sibling
`forge-builder-go-trixie` + `forge-builder-node-trixie` naming
convention. Beaconry's hub, scout, and telegram-mock all build FROM
that image. The old tag stays on GHCR for now but stops receiving
updates — this story flips every live reference so beaconry's daily
builds track the fresh tag.

## Trigger

Forge FG-056 shipped (PR bboortz/forge#104). Beaconry builds pulling
`forge-builder-trixie` now diverge from upstream.

## Expected outcome

- `poc/hub/Dockerfile`, `poc/scout/Dockerfile`,
  `poc/telegram_mock/Dockerfile` all reference the new tag.
- `verify_pinned_versions` lint matches the new GHCR regex.
- Unit tests `test_BC_095_builder_from` + `test_BC_100_telegram_mock_forge_base`
  assert the new FROM line.
- Daily builds pull the fresh tag; operators see no behavior change.

## Out of scope

- Rewriting historical CHANGELOG / shipped-story / plan docs (they
  document decisions at the time; rewriting erases history).
- Changing runtime image names (`python-scratch-trixie` stays).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Beaconry tracks Forge's forge-builder-python-trixie rename

  Scenario: Hub/scout/telegram-mock Dockerfiles reference the new tag
    Given Forge FG-056 published forge-builder-python-trixie
    When grep "forge-builder-trixie" poc/*/Dockerfile
    Then no matches outside historical comments
    And grep "forge-builder-python-trixie" returns the FROM lines

  Scenario: verify_pinned_versions regex matches the new name
    Given the renamed FROM lines
    When `make verify-pinned-versions` runs
    Then it exits 0

  Scenario: BC-095 / BC-100 unit tests pass against new base ref
    When `pytest test_BC_095_builder_from test_BC_100_telegram_mock_forge_base`
    Then both pass
```

## Capabilities delivered

No new capability — this is a cross-repo tracking fix for the already-
shipped CAP-T212 (forge-builder-trixie FROM) and CAP-T200 (telegram-mock
forge base). The capability refs continue to exist; only the GHCR path
behind them changes.

## Links

- Sibling: `bboortz/forge#104` (publishes the renamed tag)
- Related CAPs: CAP-T212, CAP-T200 (names unchanged)
