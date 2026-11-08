# BC-103 — Scout rescans on removal events

*Branch: `feat/BC-103-scout-destroy-events`*
*Story: `docs/stories/BC-103-scout-destroy-events.md`*

## Scope

- `poc/scout/scout.py` — widen `_docker_events_stream` filter.
- `poc/tests/unit/test_BC_103_scout_destroy_events.py` — grep-based
  assertion on the filter shape.
- `docs/CAPABILITIES.md` — CAP-T205.
- `CHANGELOG.md` — v0.18.1 entry.
- `VERSION`/`poc/VERSION`/`pyproject.toml` — 0.18.0 → 0.18.1.
- `docs/stories/INDEX.md` — row flip.

## Design

One-line change to the filter dict. Debounce (10 s via
`BEACONRY_DOCKER_EVENTS_DEBOUNCE_S`) is unchanged — bulk removals
don't produce N rescans.

## Testing

Grep test over `scout.py`. Full tier re-runs the live stack (no
functional regression expected).

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Filter widened | unit | `test_BC_103_scout_destroy_events.py::test_filter_includes_bc_103_removal_events` | ✓ |
| Filter is subset of docker's event vocabulary | unit | `test_BC_103_scout_destroy_events.py::test_filter_uses_valid_event_names_only` | ✓ |
| CAP-T205 registered | lint | `make verify-traceability` | ✓ |
