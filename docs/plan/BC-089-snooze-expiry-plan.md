# BC-089 — Snooze with real expiry

*Branch: `feat/BC-089-snooze-expiry`*
*Start: 2026-11-05*
*Story: `docs/stories/BC-089-snooze-expiry.md`*

---

## Situation

`_handle_snooze` (hub.py:1759) parses `hours` from callback_data,
includes it verbatim in the operator reply ("Snoozed 4h — CVE-…"),
but **never stores it anywhere**. The finding row flips to status
`snoozed` with no expiry metadata. No sweep ever runs over snoozed
rows. The only paths back to `open` are:

- Operator runs `make set-status FID=… STATUS=open` manually
- BC-086 auto-fix closes it to `auto_fixed` when the target disappears
  (wrong destination; operator wanted re-open, not silent close)

Discovered during BC-086 ship verification; logged in
`docs/plan/IDEAS.md` on 2026-11-05. Surfaced as a must-fix in BC-088's
lifecycle audit (§17.1 known bug 1, §16.7 missing feature).

---

## Problems to solve

- **Label lies.** Operator trust in timed-action labels erodes.
- **No escape from snooze without operator action.** Snooze is
  effectively "accept risk forever" with worse UX.
- **Hard-coded single duration.** Keyboard has one [⏰ Snooze 4h]
  option. Different findings warrant different snoozes (Tier 3 =
  1 week; critical-but-busy = 1 hour).

---

## Goals

1. **Expiry is stored and honoured** — Scenarios 1, 2, 3.
2. **Legacy rows don't surprise-expire** — Scenario 4 preserves
   pre-BC-089 "indefinite snooze" semantics.
3. **Keyboard offers 1/4/24/168 h buttons** (operator-configurable
   via env) — Scenario 5.
4. **Operator can opt out of expiry re-notification** — Scenario 6.
5. **Distinct notification body on expiry** so the operator knows
   "this is a snooze that expired" vs "fresh ingest".

---

## Scope

Files touched:

- `poc/hub/hub.py`:
  - Schema migration: v5 → v6, `ALTER TABLE ADD COLUMN snooze_until_ts
    INTEGER DEFAULT 0`
  - `_handle_snooze`: parse hours, compute `now + hours*3600`, store.
    Update reply to name the expiry time.
  - New: `snooze_expiry_sweep(now_ts: int | None = None) -> int` —
    SQL `WHERE status='snoozed' AND snooze_until_ts > 0 AND
    snooze_until_ts <= ?`, flip to `open`, audit, optionally notify.
  - New: `_snooze_expiry_loop_forever` — async task started in
    lifespan, runs every `BEACONRY_SNOOZE_SWEEP_INTERVAL_S`.
  - New: `format_snooze_expired(finding) -> str` — distinct body.
  - New: `notify_snooze_expired(finding) -> None` — dispatcher.
    Opt-out via `BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE=0`.
  - `_kb_finding` + `_kb_heartbeat`: iterate
    `BEACONRY_SNOOZE_DEFAULT_HOURS` to render buttons.
  - `EXPECTED_SCHEMA_VERSION = 6`.

- `poc/hub/notifiers/base.py`: add `send_snooze_expired(finding) ->
  None` abstract.

- `poc/hub/notifiers/__init__.py`: `notify_snooze_expired` dispatch.

- `poc/hub/notifiers/telegram.py`: `send_snooze_expired` → render via
  `format_snooze_expired` (new formatter key).

- `poc/hub/hub.py` TelegramAdapter wiring: add
  `"snooze_expired": format_snooze_expired` to `formatters`.

- `docs/VARIABLES.md`: three new rows.
- `poc/.env.example`: three new entries.
- `poc/docker-compose.yml`: three new `${…:-default}` hub env entries.
- `docs/CAPABILITIES.md`: CAP-T183 + CAP-T184.
- `docs/stories/INDEX.md`: BC-089 row.
- `CHANGELOG.md`: v0.13.0 entry.
- `VERSION` + `poc/VERSION` + `pyproject.toml`: 0.13.0.

Tests:

- `poc/tests/integration/test_BC_089_snooze_expiry.py` — 10 tests
  covering all 6 scenarios + schema migration + legacy-row safety +
  env-parse fallbacks.
- `poc/tests/unit/test_notification_adapters.py` +
  `test_telegram_adapter.py` + `test_heartbeat_sweep.py` — expand
  adapter stub signatures to accept `send_snooze_expired`.

---

## Non-goals

- Snooze-until-specific-datetime (already noted in story out-of-scope).
- Default-duration-per-tier.
- Rolling snooze.
- Rewriting callback_data format. `snooze:<fid>:<hours>` stays as-is;
  BC-089 just stores the hours it already parses.

---

## Design decisions

- **`snooze_until_ts = 0` means "no expiry"** (pre-BC-089 behaviour).
  Legacy rows default to 0 in the schema migration → `AND
  snooze_until_ts > 0` in the sweep SQL filters them out. Operator who
  wants to retroactively add expiry to a legacy snooze: `UPDATE
  findings SET snooze_until_ts = <epoch> WHERE id = <fid>` manually.
  Rejected: setting legacy rows to `now + 4h` on upgrade — would
  surprise-re-open operator-snoozed findings.

- **Distinct expiry notification body** rather than reusing
  `notify_regression`. The semantics differ: regression = "a CVE we
  thought was fixed is back"; snooze-expired = "a CVE you deferred is
  back on your radar as planned". Shared adapter method
  `send_snooze_expired` added; shared formatter key.

- **Keyboard button render loop** rather than hard-coded buttons.
  `BEACONRY_SNOOZE_DEFAULT_HOURS=1,4,24,168` → four buttons. Keeps
  consistency between notify-time and heartbeat-time keyboards.
  Rejected: fixed 1/4/24/168 constants — doesn't let operator
  prune to one button if they want less clutter.

- **Sweep cadence 60s default**. Precision-vs-load tradeoff: at 60s,
  worst-case drift is 59s past `snooze_until_ts` — acceptable for
  Tier 2+/3 durations. Rejected: fire on wall-clock tick (complex);
  pure-interval poll is simpler.

- **Opt-out of expiry re-notification** (`BEACONRY_SNOOZE_NOTIFY_ON_
  EXPIRE=0`). Some operators want expiry to just surface in `make
  findings`, not Telegram. Audit still fires either way so expiry
  is observable.

---

## Breaking changes

### 1. API / return shape

**None for existing endpoints.** `/scan`, `/healthz`, `/livez`,
`/readyz` unchanged. callback_data format unchanged.

New: `notify_snooze_expired` on `poc.hub.notifiers` package exports,
and `send_snooze_expired` abstract on `NotificationAdapter`. Any
out-of-tree adapter (there are none today) would need to implement
the new method. For in-tree code, the Python abstract decorator
flags it at import time, so forgetting to implement is a
load-time error — not a runtime surprise.

### 2. Data model (schema + legacy rows)

**Schema v5 → v6.** `ALTER TABLE findings ADD COLUMN snooze_until_ts
INTEGER DEFAULT 0`.

- Default for legacy rows: `0`.
- Rendering: `make findings` doesn't show snooze_until_ts (operator
  can `make audit EVENT=finding_snoozed` to see the history; the
  canonical display is in the distinct expiry notification body).
- **Semantic of `0`**: "indefinite snooze" (pre-BC-089 behaviour
  preserved). Sweep SQL filters `AND snooze_until_ts > 0`; legacy
  rows never auto-expire. Operator who wants to migrate a legacy
  snooze to real-expiry: `make set-status` + re-tap.

### 3. Behavioral semantics

**Yes, intended.** A new snooze will now re-open at expiry. This is
the whole point of the BC and is loud in the CHANGELOG. Operators who
depended on "snooze = silence forever" can:
- Press [✅ Accept risk] instead → never notifies.
- Set `BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE=0` → silent re-open (audit
  only, no Telegram ping).

No surprise for legacy state (§2 above).

---

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Scenario 1 — snooze stores expiry | integration | `test_BC_089_snooze_expiry.py::test_snooze_tap_stores_expiry` | ✓ |
| Scenario 2 — sweep re-opens expired | integration | `test_BC_089_snooze_expiry.py::test_sweep_reopens_expired` | ✓ |
| Scenario 3 — sweep ignores not-yet-expired | integration | `test_BC_089_snooze_expiry.py::test_sweep_ignores_not_yet_expired` | ✓ |
| Scenario 4 — legacy indefinite snooze preserved | integration | `test_BC_089_snooze_expiry.py::test_sweep_ignores_legacy_zero_ts` | ✓ |
| Scenario 5 — multi-button keyboard | integration | `test_BC_089_snooze_expiry.py::test_keyboard_renders_env_hours_default` | ✓ |
| Scenario 6 — opt-out of expiry notify | integration | `test_BC_089_snooze_expiry.py::test_opt_out_notify_on_expire` | ✓ |
| Schema v5 → v6 migration | integration | `test_BC_089_snooze_expiry.py::test_schema_migration_adds_column` | ✓ |
| Schema PRAGMA user_version == 6 | integration | `test_BC_089_snooze_expiry.py::test_schema_version_is_6` | ✓ |
| `_parse_snooze_hours` dedupes + rejects garbage | integration | `test_BC_089_snooze_expiry.py::test_parse_snooze_hours[-expected0]` | ✓ |
| Keyboard renders default 1,4,24,168 → 1h/4h/1d/1w | integration | `test_BC_089_snooze_expiry.py::test_keyboard_renders_env_hours_default` | ✓ |
| Keyboard renders custom env hours | integration | `test_BC_089_snooze_expiry.py::test_keyboard_renders_env_hours_custom[4-expected_labels0]` | ✓ |
| Env var defaults | integration | `test_BC_089_snooze_expiry.py::test_env_defaults_when_unset` | ✓ |
| Sweep writes audit event | integration | `test_BC_089_snooze_expiry.py::test_sweep_writes_audit_event` | ✓ |
| Distinct expiry notification body | integration | `test_BC_089_snooze_expiry.py::test_expiry_notification_distinct_body` | ✓ |
| Adapter method required | unit | `test_notification_adapters.py` + `test_telegram_adapter.py` updated to send_snooze_expired | ✓ |
| VARIABLES / env / compose three-way sync | lint | `make verify-variables` + `verify-compose-env-propagation` | ✓ |
| CAP-T183 + T184 registered | lint | `make verify-traceability` | ✓ |
| Fast + full tiers green | gate log | `make check` + `make check-full` | ✓ |

---

## Rollout

On upgrade:
- `init_db()` runs the ALTER TABLE on first boot; idempotent (columns
  checked by name before adding).
- Existing snoozed rows get `snooze_until_ts = 0` (schema default).
  They stay snoozed indefinitely — no change for operator.
- Lifespan starts `_snooze_expiry_loop_forever` task; 60s cadence.
- First Telegram alert after upgrade has 4 snooze buttons (or
  whatever `BEACONRY_SNOOZE_DEFAULT_HOURS` is set to).

No fleet-wide migration; each host rolls its own hub independently.

---

## Follow-up for BC-088 LIFECYCLE.md

Once BC-088 PR #6 merges, update `docs/LIFECYCLE.md`:

- §1 state diagram: add `SNOOZED → OPEN` arrow with "expiry sweep"
  label.
- §13 unclearity 1 "Snoozed findings and reconcile": rewrite — snooze
  now has an explicit expiry path; the auto-fix close via reconcile
  is still a real interaction, but the main re-open path is the sweep.
- §16.7 "Snoozed → open on expiry (not implemented)": delete.
- §17.1 "Snooze 4h button doesn't actually snooze for 4h": delete.
- §10 Timers: add `BEACONRY_SNOOZE_SWEEP_INTERVAL_S` and
  `BEACONRY_SNOOZE_DEFAULT_HOURS` rows.

Done as part of a follow-up chore after both PR #6 and this PR merge.
