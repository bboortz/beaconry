# BC-129 — Loud alert for every newly-persisted Tier ≤ 1 finding, even in the suppressed tail

*Branch: `feat/BC-129-notify-new-tail-criticals`*
*Start date: 2026-11-16*
*Story: `docs/stories/BC-129-notify-new-tail-criticals.md`*

---

## Situation

BC-128 closed the DB persistence gap — every unique CVE is now
`save_finding`'d. But `_notify_top_n` (`poc/hub/hub.py:5215`) only
fires Telegram for findings in the top-N slice from
`dedup_and_cap`. On hosts with more unique CVEs than
`BEACONRY_MAX_FINDINGS_PER_RUN`, a newly-persisted Tier 0 KEV or
Tier 1 CVSS≥9 internet-facing Critical that ranks outside top-N
lands in `suppressed` — it gets a DB row and a digest bullet but
no individual loud alert. The digest is hash-throttled by BC-036,
so a new KEV with a 15-min SLA (per `memory/design-decisions.md`
§ Urgency tiers) can ride a throttled digest and miss its target.

Observed on server2 (218 suppressed per scan) and bb-82jq (339
suppressed per scan). hetzner-staging's 31 tail is small enough
the risk is theoretical, but the design hole applies to any host
that grows past 100 unique CVEs.

---

## Problems to solve

- **P1**: New Tier ≤ 1 CVE in the suppressed tail is only surfaced
  via the digest body — a BC-036-throttled, hash-collapsed, 10-
  bullet summary. The operator cannot rely on it for a 15-min SLA.
- **P2**: `scan_integrity` audit event has no visibility into the
  "how many tail-Criticals got rescued" count — operator cannot
  tell whether the escape hatch fired.

---

## Goals

1. For every newly-persisted (via BC-128) finding `f` in
   `suppressed` where `f.tier <= 1`: call `notify(f)` with the
   same 0.3 s rate-limit as `_notify_top_n`. (maps to Gherkin
   scenarios 1 + 3 + 4)
2. Tier ≥ 2 suppressed findings remain digest-only. (maps to
   Gherkin scenario 2)
3. `scan_integrity` audit event gains an
   `n_tail_criticals_notified` field counting the second-pass
   sends. (maps to Gherkin scenario 5)
4. Balance invariant unchanged:
   `n_candidates == n_persisted + n_dedup + n_dropped_in_save + n_filtered_other`.
5. `beaconry verify-integrity` tolerates both pre-BC-129 events
   (no `n_tail_criticals_notified` field) and post-BC-129 events
   (field present).

---

## Scope

- `poc/hub/hub.py`:
  - New helper `_notify_tail_criticals(host, suppressed,
    persisted_ids) -> int` — second-pass loop.
  - `_ingest_candidates` calls it after `_notify_top_n`, folds the
    return into `new_findings`.
  - `scan_integrity` audit event gains `n_tail_criticals_notified`.
- `poc/tests/unit/test_BC_129_notify_new_tail_criticals.py` — new.
- `docs/CAPABILITIES.md` — CAP-T232 entry.
- `docs/stories/INDEX.md` — add BC-129 row (status: in-progress →
  shipped on merge).
- `docs/stories/BC-129-notify-new-tail-criticals.md` — this
  branch's story (already drafted).
- `pyproject.toml`, `VERSION`, `poc/VERSION`, `CHANGELOG.md` —
  version bump to 0.22.13.

No scout changes. No DB schema changes. No new env vars.

---

## Non-goals

- Changing `dedup_and_cap` ranking — the cap still protects
  Telegram throughput, this branch just carves Tier ≤ 1 out of its
  silencing effect.
- Separate rate-limit for the tail pass — reuse the existing 0.3 s
  inter-send sleep.
- Adding a CLI toggle to disable tail-Critical notifications —
  Tier 0/1 SLAs are non-negotiable per design-decisions.md.
- Backfilling missed tail-Criticals from pre-BC-129 scans — any
  still in the suppressed tail will re-trigger on the next scan.
- Changing the digest body — BC-022's render stays as-is (tail
  Criticals continue to appear there for scan-summary completeness).

---

## Design decisions

- **Second-pass loop over `suppressed`** instead of folding into
  `_notify_top_n`: keeps the two concerns readable
  ("top-N operator inbox" vs. "SLA escape hatch for new
  Criticals"). The ruff C901 complexity gate already pushed BC-128
  to split; BC-129 continues that pattern.
- **Tier ≤ 1 threshold** (not CVSS ≥ 9 directly): piggybacks on
  the single source of truth in `urgency.classify_tier`. If the
  tier definition evolves, BC-129 picks up the change for free.
- **`persisted_set` membership check** (same filter as
  `_notify_top_n`): a pre-existing tail Critical that just happens
  to still be in the tail this scan does NOT re-notify. Operator
  already got the first-time alert; silence is correct.
- **Audit field** (`n_tail_criticals_notified`): parallel naming
  to BC-128's `n_notified`, discoverable in the same JSON object.
- **No rename** of `_notify_top_n` → the top-N path is still the
  primary notification channel; BC-129 adds a secondary channel,
  doesn't replace.

---

## Breaking changes

### 1. API / return shape

- `scan_integrity` audit event: new `n_tail_criticals_notified`
  field. Additive. Pre-BC-129 consumers (verify-integrity CLI, any
  external integrity-audit reader) tolerate extra keys; the
  verifier's balance invariant doesn't reference this field.
- No change to `/scan`, `/healthz`, `/audit*`, or any other HTTP
  surface.

### 2. Data model (schema + legacy rows)

- **None** — no schema change, no new columns, no new tables.
- `save_finding` behavior unchanged (already BC-128). The `notify`
  call doesn't touch DB state beyond `last_notified_ts` which was
  already updated for top-N-path notifies; the second-pass call
  updates it identically for tail-path notifies.

### 3. Behavioral semantics

- **Noisier notifications** for operators on hosts with
  `unique_cves > BEACONRY_MAX_FINDINGS_PER_RUN` AND fresh Tier ≤ 1
  findings landing in the suppressed tail. Expected impact at
  deploy: zero or one additional Telegram per scan on server2 /
  bb-82jq (most tail items are Tier 2 Highs; tail-Criticals are
  rare but when they happen, the SLA requires the loud alert).
- **No opt-out knob**: Tier 0 KEV has a design-mandated 15-min
  target. There is no semantically valid operator preference for
  "silence my KEVs" — if the cap is squeezing them out, the cap
  is wrong.
- **Operator cognitive surface**: the digest body still lists
  tail-Criticals (intentional; the digest is a scan summary, not
  a notification queue). Operator sees the same CVE as both a
  loud alert AND a digest bullet. This is the same pattern BC-022
  established for first-fire-above-cap: alert is the action
  trigger, digest is the scan summary.

---

## Merge plan

1. All work on `feat/BC-129-notify-new-tail-criticals`
2. `make check` must pass on branch tip; `make check-full` before
   opening the PR.
3. PR to `dev`; Claude opens, user runs `/ultrareview`, Claude
   runs `/code-review`.
4. Merge strategy: squash.
5. Post-merge: update story status → `shipped`, update
   `docs/stories/INDEX.md`.
6. CI promotes `dev` → `main`; tag `v0.22.13`; forge workflow
   rebuilds `beaconry-hub` + `beaconry-scout` images.

---

## Rollout plan

- No migration, no backfill, no new env vars.
- After hub deploy: next scan on any host with suppressed Tier ≤ 1
  findings fires the new path immediately. The audit event's new
  field appears on all post-deploy scan_integrity rows.
- `beaconry verify-integrity` transparently tolerates the new
  field (ignored in drop-sum invariant; additive field in event
  shape).

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| New Critical in tail gets individual notify | unit | `test_BC_129_notify_new_tail_criticals.py::test_new_tail_critical_fires_notify` | ✓ |
| Tier 2 High in tail stays silent | unit | `test_tier_2_tail_stays_silent` | ✓ |
| Tail KEV always fires loud alert | unit | `test_tail_kev_always_notifies` | ✓ |
| Pre-existing tail Critical does NOT re-notify | unit | `test_preexisting_tail_critical_no_renotify` | ✓ |
| scan_integrity balance invariant holds | unit | `test_scan_integrity_balance_invariant_bc129` | ✓ |
| scan_integrity has n_tail_criticals_notified field | unit | `test_scan_integrity_has_n_tail_criticals_notified_field` | ✓ |
| verify-integrity tolerates pre-BC-129 events | unit | `test_verify_integrity_backward_compat_pre_bc129` | ✓ |
| Live post-deploy — server2 tail-Critical on next scan | live | `beaconry audit --event scan_integrity --host server2 --last 1` shows `n_tail_criticals_notified > 0` (or `== 0` if no tail-Criticals this scan — both acceptable) | ⏳ pending post-deploy |
| Memory drift pattern added if story deprecates documented behavior | TOML diff / N/A | N/A — additive, no documented behavior deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial (notes required) · ❌ open (blocks shipping).

---

## Testing strategy

Unit tests mock `save_finding` + `notify` + `send_digest` so the
behavior change is visible as "how many times was notify called,
and for which findings" rather than requiring DB + Telegram round-
trip. The BC-128 test file is the template.

Integration coverage comes from the live deploy — the first
server2 scan post-deploy either exercises the new path (if
tail-Criticals exist) or confirms the balance invariant with
`n_tail_criticals_notified == 0`.

---

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_129_notify_new_tail_criticals.py -v
# Post-deploy:
beaconry audit --event scan_integrity --host server2 --last 1 | jq .
# Field n_tail_criticals_notified must be present on post-BC-129 events.
```

Expected output (per test):

```
test_new_tail_critical_fires_notify PASSED
test_tier_2_tail_stays_silent PASSED
test_tail_kev_always_notifies PASSED
test_preexisting_tail_critical_no_renotify PASSED
test_scan_integrity_balance_invariant_bc129 PASSED
test_scan_integrity_has_n_tail_criticals_notified_field PASSED
test_verify_integrity_backward_compat_pre_bc129 PASSED
```
