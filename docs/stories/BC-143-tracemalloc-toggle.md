---
id: BC-143
title: tracemalloc toggle + memory-leak investigation playbook
status: draft
created: 2026-11-17
branch: feat/BC-141-BC-144-memory-safety
plan: docs/plan/BC-143-tracemalloc-toggle-plan.md
---

# BC-143: tracemalloc toggle + memory-leak investigation playbook

## Narrative

BC-141 contains OOM blast radius; BC-142 makes memory climb visible.
Neither identifies WHICH python allocation is the source. On
2026-11-17, three top candidates emerged (BC-104 notify retry queue,
asyncio fire-and-forget regressions, KEV refresh leak) with no way
to disambiguate without running tracemalloc on the live hub.

BC-143 adds an opt-in `BEACONRY_TRACEMALLOC=1` env flag that enables
Python's `tracemalloc` at startup + exposes `GET /_tracemalloc/top`
returning the top N allocators as JSON. Zero cost when disabled
(tracemalloc has ~10-20 % runtime overhead when on, 0 % when off).
Operator enables it only when hunting a leak; disables after the fix
ships. Pairs with a docs/playbook that walks the operator through the
investigation steps.

## Trigger

- Operator sees `rss_mb` climb on `/healthz` (BC-142 signal).
- Operator wants to identify which allocation is responsible.
- Ships investigation workflow — not an automated detector.

## Expected outcome

- New env var `BEACONRY_TRACEMALLOC=1` enables Python's `tracemalloc`
  at hub startup; `_tracemalloc_enabled=True` surfaced on `/healthz`.
- New endpoint `GET /_tracemalloc/top` (CLI-token-auth, like /audit)
  returns JSON:
  ```json
  {
    "enabled": true,
    "total_mb": 184.5,
    "top": [
      {"file": "poc/hub/hub.py", "lineno": 2145, "size_mb": 42.3, "count": 1203},
      ...
    ]
  }
  ```
- If tracemalloc is disabled, endpoint returns 200 with
  `{"enabled": false, "hint": "set BEACONRY_TRACEMALLOC=1 and restart the hub"}`.
- New operator CLI: `beaconry tracemalloc` (routes like `beaconry
  audit`: remote hub → HTTP, dev-compose → docker-exec, else
  warn-and-local-only).
- New docs page `docs/operations/memory-leak-hunt.md` walks the
  investigation: capture before/after snapshots, diff, identify source
  file + line, cross-reference with the live BC list.

## Out of scope

- An always-on tracemalloc. The 10-20 % overhead isn't acceptable
  for a hub meant to run 24/7.
- Automated leak detection based on tracemalloc deltas — that's a
  future BC.
- Bundled visualization of tracemalloc output (JSON is enough;
  operators can pipe to `jq` or a notebook).
- Fixing the 2026-11-17 leak itself — that happens AFTER BC-143 lets
  us identify it. The investigation playbook calls out the top
  candidates and the expected signatures.

## Acceptance criteria (Gherkin)

```gherkin
Feature: on-demand Python heap introspection for memory-leak hunts

  Scenario: BEACONRY_TRACEMALLOC=1 enables at startup
    Given a hub boots with BEACONRY_TRACEMALLOC=1
    When operator curls /healthz
    Then checks.tracemalloc_enabled == true

  Scenario: Default off — zero overhead
    Given a hub boots without BEACONRY_TRACEMALLOC
    When operator curls /healthz
    Then checks.tracemalloc_enabled == false
    And /_tracemalloc/top returns {"enabled": false, ...}

  Scenario: Top-allocators endpoint returns JSON
    Given a hub running with tracemalloc enabled
    When operator curls /_tracemalloc/top
    Then the response is JSON with keys: enabled, total_mb, top[]
    And top[] entries have file, lineno, size_mb, count

  Scenario: beaconry tracemalloc CLI routes correctly
    Given cli config points at a remote hub
    When operator runs `beaconry tracemalloc`
    Then the CLI hits GET /_tracemalloc/top on the hub
    And renders a table: SIZE / COUNT / LOCATION

  Scenario: Playbook document covers the investigation steps
    Given docs/operations/memory-leak-hunt.md exists
    When operator opens it
    Then it covers: enable tracemalloc, capture T0 snapshot, wait
      N minutes, capture T1, diff, identify suspect, map to known
      candidate list (BC-104 queue, KEV retry, regression tasks, SBOM
      payload retention)
```

## Capabilities delivered

- **CAP-T239: On-demand Python heap introspection via tracemalloc**
  (type: technical, status: active)

  *`BEACONRY_TRACEMALLOC=1` toggles Python's `tracemalloc.start()` at
  lifespan startup. Hub exposes `GET /_tracemalloc/top` (CLI-token
  auth) returning the top N allocators by size, with file + lineno +
  cumulative size + count. `beaconry tracemalloc` CLI routes like
  `beaconry audit` (HTTP / docker-exec / local). Default off → zero
  overhead; enabling adds ~10-20 % runtime cost during an active
  investigation only. Paired with `docs/operations/memory-leak-hunt.md`
  that walks operators through the before/after snapshot workflow +
  candidate-leak signatures.*

Supports CAP-O042 (BC-141 caps) + CAP-T238 (BC-142 metrics). Operator
sees memory climb (BC-142) → enables tracemalloc (BC-143) → finds the
source → ships the fix → disables tracemalloc.

## Open questions

Resolved during drafting (2026-11-17):

1. Why `tracemalloc` and not `py-spy` / `memray`? **Stdlib only.**
   tracemalloc ships with Python; no new dep. memray is heavier but
   gives better output; keep it as a future opt-in.
2. Endpoint `/_tracemalloc` — why the underscore? **Marks it as
   operator-internal, not a public API.** Follows the `/health` /
   `/healthz` + `/sbom` style; underscore indicates "introspection
   surface, not stable".
3. Memory overhead of tracemalloc when enabled? **~5-10 % baseline
   RAM + ~10-20 % CPU.** Measured in Python docs; acceptable for
   investigation windows. `/healthz` warns when enabled so operator
   remembers to turn it off.
4. Should we expose a diff endpoint (`/_tracemalloc/diff?since=T0`)?
   **Yes, light version.** Hub stores the last manually-triggered
   snapshot in memory; `/_tracemalloc/diff` returns growth since then.
   The playbook uses this.

## Links

- Direct companions: BC-141 (caps the damage), BC-142 (surfaces the
  signal), BC-144 (gates future regressions). BC-143 is the
  investigation tool that bridges "I see memory climbing" → "I know
  which line is responsible".
- Playbook: `docs/operations/memory-leak-hunt.md` (new).
