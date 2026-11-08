---
id: BC-103
title: Scout rescans on image/container `delete` + `destroy` + `die` events
status: shipped
created: 2026-11-08
branch: feat/BC-103-scout-destroy-events
plan: docs/plan/BC-103-scout-destroy-events-plan.md
---

# BC-103: Immediate rescan when an operator deletes an image

## Narrative

BC-093 wired scout to subscribe to Docker events and trigger an
immediate (debounced) rescan — but only for `pull`, `create`, `start`.
Image deletion (`docker rmi`) and container removal (`docker rm`)
don't fire a rescan. The operator removes an image → nothing happens
until the next natural `SCAN_INTERVAL` (default 300 s). Combined
with BC-086's `AUTO_FIX_THRESHOLD=3`, that means up to 15 min
between removal and `auto_fixed`. Today's incident timeline showed
this directly: the user deleted `homelab-bot-alpine-test:latest` at
~16:20, but findings stayed `regressed` until ~16:44.

BC-103 widens the subscription filter so `delete` (image), `destroy`
(container), and `die` (container) also trigger a debounced rescan.
Image removals now produce POST-driven reconcile on the next sweep
(~10 s via BC-093's debounce), reducing the auto-close latency to
`3 × scan_interval` minimum (still bounded by AUTO_FIX_THRESHOLD)
but with the first absent_count bump landing within seconds, not
minutes.

## Trigger

Operator-observed: 24 min between image removal and `auto_fixed`
status flip. Also structural: the subscription filter is clearly
incomplete.

## Expected outcome

- `poc/scout/scout.py`'s `_docker_events_stream` filter widens
  from `{"event": ["pull", "create", "start"]}` to
  `{"event": ["pull", "create", "start", "destroy", "die", "delete"]}`.
- The existing debounce window (10 s via `_docker_events_debounce_s`)
  applies unchanged — operators scripting bulk removals don't trigger
  one rescan per image.
- Unit test asserts the widened filter.
- No new env vars (debounce already configurable via
  `BEACONRY_DOCKER_EVENTS_DEBOUNCE_S`).

## Out of scope

- Per-event-type behaviour differences (destroy vs die vs delete all
  behave identically — "something was removed, rescan").
- Scout → hub forwarding of lifecycle events as data (BC-102's
  Out-of-Scope).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Scout rescans on image/container removal

  Scenario: Image removed → scout triggers rescan
    Given scout is running with the BC-093 docker events listener
    When `docker image rm <image>` is executed on the host
    Then scout's events-loop receives a `delete` event
      And triggers a scan within the BC-093 debounce window
      And the next POST to hub carries an inventory without the removed image

  Scenario: Container removed → scout triggers rescan
    When `docker rm <container>` is executed
    Then scout's events-loop receives a `destroy` event
      And triggers a scan within the debounce window

  Scenario: Filter includes all six event types
    When a reader greps poc/scout/scout.py for the events filter
    Then the filter includes `pull`, `create`, `start`, `destroy`, `die`, `delete`
```

## Capabilities delivered

- **CAP-T205: Scout rescans on container/image removal events** —
  extends CAP-T192 (BC-093 docker events listener). Same debounce,
  wider filter.

## Links

- BC-093 (CAP-T192 — original events listener).
- BC-086 (POST-driven reconcile — consumer of the rescan).
- BC-102 (sibling — stale-scout path; complements BC-103's
  active-scout path).
