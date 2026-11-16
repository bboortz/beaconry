# BC-128 — Persist every identified unique CVE; cap only notifications

*Branch: `feat/BC-128-persist-all-unique-cves`*
*Start date: 2026-11-16*
*Story: `docs/stories/BC-128-persist-all-unique-cves.md`*

---

## Situation

`BEACONRY_MAX_FINDINGS_PER_RUN=100` is doing double duty:
1. Notification rate-cap (prevent 2000 Telegram msgs per scan) ← intended
2. Persistence cap (only top-N get `save_finding`) ← unintended, bug

On server2: 2029 candidates → 318 unique → 100 top-N persisted
(+ notified) → 218 suppressed tail persisted NOWHERE.

## Goals

1. `save_finding` runs on every unique CVE (top-N + tail).
2. `notify()` + 0.3s rate-limit still only runs on top-N.
3. `send_digest(suppressed)` still runs (operator inbox parity).
4. `scan_integrity` audit event:
   - `n_persisted` counts both branches
   - New `n_notified` field (top-N count)
   - `n_capped` renamed → `n_suppressed_from_notify`
   - Balance invariant: `n_candidates == n_persisted + n_dedup + n_dropped_in_save + n_filtered_other`

## Scope

- `poc/hub/hub.py::_ingest_candidates`:
  - Two-phase: save all → notify top-N → digest suppressed.
  - Audit event rename + new field.
- `poc/scripts/verify_integrity.py::build_report`:
  - Update `_drop_sum` to drop `n_capped` from the formula (now
    persisted, not dropped). Keep backward-compat for pre-BC-128
    events that still have the old shape.
- `poc/tests/unit/test_BC_128_persist_all_unique_cves.py` — new.
- `docs/CAPABILITIES.md` — CAP-T231.
- `docs/VARIABLES.md` — `BEACONRY_MAX_FINDINGS_PER_RUN` description
  clarified: "notification cap (per scan); does NOT affect DB
  persistence as of BC-128."

## Non-goals

- Changing scout-side anything.
- Backfilling missed findings — the next scan after deploy picks
  them all up.

## Design decisions

- **Two-phase ingestion** (save all, then notify top-N) keeps the
  code paths mostly intact. The save loop is bulk (no Telegram
  rate-limit sleeps); the notify loop retains the 0.3s per-send
  sleep for Telegram flood control.
- **Audit field rename** (`n_capped` → `n_suppressed_from_notify`)
  is honest about new semantics. Verifier backward-compat: accept
  both names when summing drops; old name counts as a drop (pre-
  BC-128 semantics); new name doesn't (post-BC-128 semantics).
- **Keep `BEACONRY_MAX_FINDINGS_PER_RUN` name** — operators expect
  it; the description now clearly scopes to notifications.
- **Same `save_finding` function** — no new helper. The function
  is idempotent (`INSERT OR IGNORE`); calling it on a dup is a
  no-op returning False.

## Breaking changes

### 1. API / return shape
- `scan_integrity` audit event: new `n_notified` field, renamed
  `n_capped` → `n_suppressed_from_notify`. Pre-BC-128 consumers
  of `n_capped` see the old field name on pre-BC-128 events and
  the new name on post-BC-128 events. `beaconry verify-integrity`
  handles both.

### 2. Data model
- Persisted row count grows substantially on hosts with
  `unique_cves > BEACONRY_MAX_FINDINGS_PER_RUN`. server2 goes from
  ~100 → ~318 rows after one scan.

### 3. Behavioral semantics
- `beaconry findings --host <X>` shows MORE rows post-deploy. Good.
- Telegram operator inbox: unchanged (still ≤ MAX_FINDINGS_PER_RUN
  individual messages + 1 digest).
- `/healthz` unaffected.
- `BC-086 reconcile_scan` already uses `scanned_ids = {c.id for c
  in candidates}` — superset includes tail — so reconcile was
  correct all along.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| save_finding called on every unique CVE | unit | `test_BC_128_persist_all_unique_cves.py::test_ingest_saves_all_unique_candidates` | ✓ |
| notify() only on top-N | unit | `test_ingest_notifies_only_top_n` | ✓ |
| digest still fires on suppressed tail | unit | `test_ingest_digest_still_fires_on_tail` | ✓ |
| scan_integrity n_notified field | unit | `test_scan_integrity_has_n_notified_field` | ✓ |
| scan_integrity n_capped renamed | unit | `test_scan_integrity_renames_n_capped_to_suppressed_from_notify` | ✓ |
| Balance invariant holds post-BC-128 | unit | `test_scan_integrity_balance_invariant` | ✓ |
| verify-integrity backward-compat for pre-BC-128 events | unit | `test_verifier_tolerates_pre_bc128_n_capped_field` + `test_verifier_tolerates_post_bc128_n_suppressed_from_notify` | ✓ |
| Verifier still catches truly-unexplained drops | unit | `test_verifier_catches_truly_unexplained_drop` | ✓ |
| Memory drift pattern added | N/A | pure additive on persistence | ✓ |

## Testing strategy

Unit tests mock `save_finding` + `notify` so the behavior change
is visible as "how many times was this called" rather than needing
a full DB round-trip. Integration coverage comes from the live
deploy on server2 (visible in `beaconry findings` row count).

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_128_persist_all_unique_cves.py -v
# Post-deploy:
beaconry findings --host server2 | wc -l   # expect ~318, not 100
beaconry audit --event scan_integrity --last 1   # n_persisted ~318
```
