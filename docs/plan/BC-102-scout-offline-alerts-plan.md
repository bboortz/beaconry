# BC-102 — Hub-side scout-offline alerts + stale-scout reconcile

*Branch: `feat/BC-102-scout-offline-alerts`*
*Start: 2026-11-08*
*Story: `docs/stories/BC-102-scout-offline-alerts-and-reconcile.md`*

---

## Situation

2026-11-08 ~21:20 UTC bb-82jq: operator scout died silently for 54
min. Hub knew (`heartbeat_stale=True` on `/fleet`) but took no
action — no Telegram, no audit, no auto-close. 17 open + 7
regressed findings froze until the operator noticed and manually
restarted the service.

Three gaps, all stemming from "scout POSTs drive everything":

1. No Telegram when scout crosses the offline threshold.
2. No audit trail (`scout_offline` / `scout_online` nowhere).
3. BC-086 reconcile can't fire without POSTs — findings freeze.

---

## Goals

1. **G1 — Edge-triggered Telegram alert.** One message per transition
   (not level-triggered spam). Carries host, last-heartbeat age,
   last health label, auto-close timeline.
2. **G2 — Recovery alert.** One message when a previously-offline
   scout POSTs again. Carries the outage duration.
3. **G3 — Audit events.** `scout_offline`, `scout_online`,
   `finding_auto_fixed_stale_scout`.
4. **G4 — Stale-scout reconcile.** Time-based tick that bumps
   `absent_count` on `open`/`regressed`/`snoozed`/`accepted`
   findings; after `BEACONRY_AUTO_FIX_THRESHOLD` ticks → `auto_fixed`.
5. **G5 — All three configurable envs.**
   `BEACONRY_SCOUT_OFFLINE_ALERT_THRESHOLD_S` (default 600),
   `BEACONRY_SCOUT_OFFLINE_SWEEP_INTERVAL_S` (default 60),
   `BEACONRY_AUTO_CLOSE_STALE_SCOUT` (default 1, opt-out).
6. **G6 — No regression on fresh scouts.** Never-posted scouts
   don't trigger offline alerts.

---

## Scope

### Modified files

- `poc/hub/hub.py`:
  - Schema bump 7 → 8: `ALTER TABLE scouts ADD COLUMN
    last_offline_alert_ts INTEGER NOT NULL DEFAULT 0` (idempotent
    via PRAGMA table_info — same pattern BC-063/BC-092 used).
  - Three new env reads near `AUTO_FIX_THRESHOLD`.
  - New pure function `scout_offline_sweep(now_ts)` returning the
    actions it would take (for unit testing without Telegram).
  - New background task `_scout_offline_sweep_loop_forever`
    registered in `lifespan` alongside the heartbeat + snooze loops.
  - Update `/scan` POST handler to detect the transition from
    "last_offline_alert_ts > 0" to "scout is back" → emit
    `scout_online` audit + Telegram.
- `poc/hub/notifiers/base.py`:
  - Two new default-no-op methods: `send_scout_offline` +
    `send_scout_online`. Same pattern as BC-089's
    `send_snooze_expired`.
- `poc/hub/notifiers/telegram.py`:
  - Implement the two methods with concise `<b>⚠️ Scout offline</b>`
    / `<b>✓ Scout back online</b>` messages.
- `docs/VARIABLES.md` — three new rows.
- `poc/.env.example` — three new rows.
- `docs/CAPABILITIES.md` — CAP-B050 + CAP-T204.
- `CHANGELOG.md` — v0.18.0 entry (minor — new operator-visible
  feature).
- `VERSION` + `poc/VERSION` + `pyproject.toml` — 0.17.2 → 0.18.0.
- `docs/stories/INDEX.md` — row in-progress (done).

### Added files

- `poc/tests/unit/test_BC_102_scout_offline_sweep.py` — pure-function
  unit tests:
  - Edge-triggered: `scout_offline_sweep` returns `send_offline`
    action once, then `noop` on subsequent calls while still stale.
  - Recovery path: once `last_posted_ts` moves forward, next sweep
    returns `send_online` + clears `last_offline_alert_ts`.
  - Auto-close reconcile: 3 ticks while scout offline → flip
    findings to `auto_fixed` + emit `finding_auto_fixed_stale_scout`.
  - Opt-out: `BEACONRY_AUTO_CLOSE_STALE_SCOUT=0` skips the
    reconcile, still emits alert/audit.
  - Fresh scout (never POSTed): no alert, no audit.
  - Below threshold: no action.

### Not modified

- BC-086's POST-driven reconcile — unchanged. Both paths coexist.
- Scout code — scout-side is already fine; it's hub that needs
  the new awareness.
- `verify_variables_documented.py` — three new vars documented in
  VARIABLES.md will be picked up automatically.

---

## Non-goals

- Forwarding BC-093 docker events from scout to hub.
- Self-healing (hub restarting scouts).
- "Container config drift" detection.
- Per-host override of threshold/cadence — global envs only in V1.

---

## Design decisions

- **`scouts.last_offline_alert_ts` column** stores the last time
  the hub alerted for this scout. Non-zero = "we're in an alerted-
  offline state". Zero = either online or never-alerted. One column
  carries edge-trigger state cleanly.
- **Edge-triggered via column check** — not a set-in-memory flag —
  so the alert state survives hub restarts. If hub restarts while
  a scout is offline, we don't re-alert (the column stays set).
- **Pure function `scout_offline_sweep(now_ts, scouts_rows, findings_rows, env)`**
  returns `list[Action]` (dataclass with `{type, host, data}`).
  Loop body applies the actions. Enables deterministic unit tests
  with no Telegram, no clock.
- **Default sweep interval 60s** — same as the default
  `SCOUT_HEARTBEAT_INTERVAL_S`. Snooze-expiry + heartbeat loops
  already run at similar cadences; low cost.
- **Default alert threshold 600s (10 min)** — well above the
  `heartbeat_stale` cutoff (300s floor) so a 2-min network glitch
  doesn't page. Low enough to catch a real outage quickly.
- **Auto-close semantics** — time-based ticks, each tick = 1
  `absent_count` bump while scout is offline. `AUTO_FIX_THRESHOLD`
  (default 3) applies: after 3 ticks past the offline threshold =
  auto_fixed. With defaults: `600s + 3 × 60s = 780s` to auto-close.
  Different wall clock than BC-086 (POST-driven; `3 × SCAN_INTERVAL
  = 900s` typical) but same semantic: 3 confirmations of absence.
- **Opt-out via `BEACONRY_AUTO_CLOSE_STALE_SCOUT=0`.** Telegram
  alert + audit events always fire — never opt-out. Only the
  auto-close is gated.
- **Audit event names** carry the `_stale_scout` suffix on the
  auto-close so forensics can distinguish "closed because image
  genuinely disappeared" (BC-086) vs "closed because we couldn't
  tell" (BC-102). Important for `make audit EVENT=…` queries.

---

## Breaking changes

### 1. API / return shape
**None.** `/scan`, `/fleet`, `/findings` unchanged.

### 2. Data model
- **Schema v7 → v8.** New column `scouts.last_offline_alert_ts`
  INTEGER NOT NULL DEFAULT 0. Idempotent ALTER via PRAGMA pattern.
  Legacy rows get 0 → no false "already alerted" state.
- **New audit event types.** Downstream audit log consumers that
  strict-match event names should accept `scout_offline`,
  `scout_online`, `finding_auto_fixed_stale_scout`. Existing event
  types unchanged.

### 3. Behavioral semantics
- **New Telegram traffic.** Operators who had scouts flapping may
  see more messages. Edge-triggered design prevents spam (one per
  transition).
- **Findings auto-close faster when scout is offline.** Previously
  they froze forever; now they close after `threshold + 3 ×
  sweep_interval` seconds (default ~13 min). Operators who prefer
  the frozen state (as an "unknown — investigate" signal) set
  `BEACONRY_AUTO_CLOSE_STALE_SCOUT=0`.

---

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| G1 — edge-triggered offline alert | unit | `test_BC_102_scout_offline_sweep.py::test_offline_transition_alerts_once` | ✓ |
| G1b — no repeat alert while offline | unit | `test_BC_102_scout_offline_sweep.py::test_offline_stable_state_no_repeat` | ✓ |
| G2 — recovery alert | unit | `test_BC_102_scout_offline_sweep.py::test_recovery_emits_online_action` | ✓ |
| G3 — audit event names | unit | `test_BC_102_scout_offline_sweep.py::test_audit_event_names` | ✓ |
| G4 — auto-close after threshold ticks | unit | `test_BC_102_scout_offline_sweep.py::test_auto_close_after_threshold_ticks` | ✓ |
| G5a — alert threshold respected | unit | `test_BC_102_scout_offline_sweep.py::test_below_threshold_no_action` | ✓ |
| G5b — opt-out disables auto-close only | unit | `test_BC_102_scout_offline_sweep.py::test_opt_out_disables_only_auto_close` | ✓ |
| G6 — fresh scout no alert | unit | `test_BC_102_scout_offline_sweep.py::test_fresh_scout_no_alert` | ✓ |
| Schema migration idempotent | unit | `test_BC_102_scout_offline_sweep.py::test_schema_adds_column_idempotently` | ✓ |
| CAPs registered | lint | `make verify-traceability` | ✓ |
| Memory drift pattern | N/A | N/A — additive feature | ✓ |

---

## Testing commands

```bash
# Fast tier
make check

# Live stress (optional — simulate scout offline)
make live-rebuild
sudo systemctl stop beaconry-scout        # or docker stop beaconry-dev-scout
# Wait > BEACONRY_SCOUT_OFFLINE_ALERT_THRESHOLD_S (10 min default)
# Expect: Telegram alert, scout_offline audit event
sudo systemctl start beaconry-scout
# Expect: Telegram recovery, scout_online audit event
```

---

## Links

- Story: `docs/stories/BC-102-scout-offline-alerts-and-reconcile.md`
- Incident: 2026-11-08 ~21:20 UTC bb-82jq
- BC-086 (POST-driven reconcile — same semantic, different trigger)
- BC-064 (`heartbeat_stale` — the signal this story acts on)
- BC-061 (scouts table — where the new column lives)
- `hub.py:3028` — code comment documenting the exact gap this retires
