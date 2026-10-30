# fix/BC-007-load-finding-tier — Plan

*Branch: `fix/BC-007-load-finding-tier`*
*Start date: 2026-10-30*

---

## Situation

Second audit of BC-007 found four remaining items the first close-gaps branch missed:

1. **load_finding doesn't SELECT tier.** save_finding was updated to persist tier; load_finding wasn't. `Finding(*row)` fills tier from the dataclass default (3) regardless of what's stored. No runtime impact today because `on_button_click` doesn't use `finding.tier`, but any future button handler (e.g. "Snooze longer for tier-0" logic) would get stale data silently.
2. **CAP-T025 description stale.** The capability describes the pre-migration behavior; nothing about the schema migration or the pre-migration-DB tolerant mode.
3. **No regression test for the schema migration itself.** We verified it works live but there's no test that proves `init_db` adds the column to an existing legacy DB.
4. **save→load round-trip for the tier field** is not asserted.

## Problems to solve

- Latent correctness bug in load_finding
- Doc staleness
- Testing holes around the migration path

## Goals

1. load_finding SELECTs tier and populates `Finding.tier` from the DB row
2. Round-trip test: `_finding(tier=0)` → save → load → `.tier == 0`
3. Migration test: open a legacy DB (no tier column), call init_db, assert PRAGMA shows tier
4. CAP-T025 description updated

## Scope

- `poc/hub/hub.py` — load_finding's SELECT grows `tier`; return constructor
- `poc/tests/unit/test_hub_helpers.py` — tier round-trip; migration test
- `docs/CAPABILITIES.md` — CAP-T025 refresh
- This plan doc

## Non-goals

- Changing `Finding` dataclass field order
- Backfilling tier for findings already in the live DB (they stay at 3; next scan writes correct values)
- Making load_finding behave differently when tier column is absent (hub.init_db migrates, so load_finding runs post-migration)

## Design decisions

**load_finding uses positional constructor still.** Finding has 11 required fields; adding tier at position 12 means `Finding(*row)` works if the SELECT returns 12 columns in the right order. Simplest change.

**Migration test uses a temp DB with the legacy schema.** Mimics the real "upgrade in place" scenario.

**No backfill of existing live rows.** They were tier=3 by default; next scan writes correct tier. Acceptable; no user-visible impact.

## Breaking changes

None.

## Merge plan

1. Plan + implementation + tests on this branch
2. `make check` + `make check-full` green
3. Fast-forward merge to dev

## Testing strategy

- Unit test: save `_finding(tier=0)`, load it, assert `.tier == 0`
- Unit test: build a legacy-schema DB in tmp_path (13 columns, no tier), call `init_db` with hub.DB_PATH monkeypatched to point there, assert PRAGMA table_info includes `tier`

## Testing commands

```bash
source .venv/bin/activate
make check
make findings
```
