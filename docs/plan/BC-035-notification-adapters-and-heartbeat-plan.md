# BC-035 — Notification adapter architecture + heartbeat for stale open findings

*Branch: `feat/BC-035-notification-adapters-and-heartbeat`*
*Start date: 2026-10-31*
*Story: `docs/stories/BC-035-notification-adapters-and-heartbeat.md`*

---

## Situation

Pre-BC-035 the hub's outbound notifications were tightly coupled to python-telegram-bot: `notify`, `notify_regression`, and `send_digest` each called `tg_app.bot.send_message` directly. Adding email, Reddit, or SMS would mean touching every call site and inventing a new global flag per channel. Operators had a single all-or-nothing `BEACONRY_MOCK` dial — no way to say "keep Telegram, kill Reddit".

Separately, BC-034 landed `last_seen_ts` + `last_notified_ts` groundwork but did not yet consume them. Findings sitting at `status=open` have no reminder loop — if an operator misses the first ping, the finding is effectively invisible until something else touches the row. "Fix is not forget" principle (operator feedback this session) demands a periodic re-poke.

---

## Problems to solve

- Outbound notifications are hard-coded to Telegram's SDK; cannot add a second channel without a cross-cutting refactor.
- Operators cannot enable/disable individual notification channels.
- Stale open findings get no reminder; a missed first ping = silent forever.

---

## Goals

1. Dispatch all outbound notifications through a `NotificationAdapter` registry with per-adapter env-based enable flag (maps to Gherkin "Notification adapter architecture" scenarios).
2. Preserve Telegram behavior bit-for-bit by shipping `TelegramAdapter` as the sole initial registered adapter.
3. Add `heartbeat_sweep()` + a background loop that re-pokes stale open findings via the registry (maps to Gherkin "Heartbeat sweep" scenarios).
4. Audit log records every reminder (BC-029 integration).

---

## Scope

- NEW `poc/hub/notifiers/__init__.py` — registry (`register`, `reset_registry_for_tests`, `enabled_adapters`, `_dispatch`) + four dispatch helpers (`notify_finding`, `notify_regression`, `notify_heartbeat`, `notify_digest`).
- NEW `poc/hub/notifiers/base.py` — `NotificationAdapter` ABC with four abstract methods + `enabled` property reading `env_var`.
- NEW `poc/hub/notifiers/telegram.py` — concrete `TelegramAdapter`; wraps tg_app + chat_id + formatter/keyboard closures; MOCK-aware.
- `poc/hub/hub.py` — refactor `notify`, `notify_regression`, `send_digest` to thin wrappers; add `format_heartbeat`, `_kb_finding`, `_kb_regression`, `format_digest_body`, `heartbeat_sweep`, `_heartbeat_loop_forever`; add 5 env vars; add `last_notified_ts` column + idempotent migration + backfill from `found_at`; register `TelegramAdapter` in lifespan; spawn heartbeat background task.
- `poc/hub/Dockerfile` — `COPY hub/notifiers/ notifiers/`.
- `poc/docker-compose.yml` — propagate 5 new env vars (`BEACONRY_HEARTBEAT_ENABLED`, `_INTERVAL_S`, `_SWEEP_INTERVAL_S`, `_LIVENESS_WINDOW_S`, `BEACONRY_NOTIFIER_TELEGRAM_ENABLED`).
- NEW `poc/tests/unit/test_notification_adapters.py` — 8 tests for registry + dispatch isolation.
- NEW `poc/tests/unit/test_telegram_adapter.py` — 8 tests for the Telegram adapter (MOCK, missing tg_app, four dispatch kinds).
- NEW `poc/tests/unit/test_heartbeat_sweep.py` — 10 tests covering all 7 Gherkin scenarios + idempotent re-poke + audit event + cold-boot safety.
- `poc/tests/unit/test_hub_helpers.py` — `_capture_digest` fixture refactor (register a capturing adapter instead of monkeypatching `hub.tg_app`).
- `docs/CAPABILITIES.md` — register CAP-T078, CAP-T079.
- `docs/stories/INDEX.md` — BC-035 row.

---

## Non-goals

- Second concrete adapter (email/Reddit/SMS/webhook): interface ships here; implementations are follow-up stories.
- Per-finding channel preferences: snooze/accept covers deferral today; fine-grained per-finding routing is a separate story.
- Interactive callbacks (button taps) in the adapter: `on_button_click` stays Telegram-specific. Only OUTBOUND dispatch is abstracted.

---

## Design decisions

- **Registry over strategy param**: adapters self-register in lifespan; call sites iterate. Rejected: thread an `adapters: list[Adapter]` arg through every call — would spread the dependency wide for no gain.
- **Env-based `enabled` property** (not constructor arg): operator ergonomics — toggle `BEACONRY_NOTIFIER_TELEGRAM_ENABLED=0` in `.env`, restart hub, done. Rejected: central YAML config — more moving parts for a single boolean per adapter.
- **Per-adapter failure isolation** (log + swallow): one bad adapter should not block Telegram. Rejected: fail-fast — would make heartbeat sweep crash the hub if a future Reddit adapter rate-limits.
- **Mock fallback stays in hub.py**: BC-001 e2e demo expects `[mock] telegram send: ...` on stdout. The Telegram adapter's `_mock_renderer` closure handles the live path; `_mock_print()` in hub preserves the string for the "empty registry + MOCK=1" cold-start demo case so the e2e assertion is unchanged.
- **Heartbeat reads `found_at` as `open_since_ts`** (not `last_seen_ts - interval`): the operator-facing number is "how long has this been open," not "how long since we last saw it live" — those are the same for actively observed findings and `found_at` is simpler.

---

## Breaking changes

### 1. API / return shape

**None.** `notify`, `notify_regression`, and `send_digest` keep their signatures (now wrappers over the registry). External HTTP/Telegram-callback shapes unchanged. New `heartbeat_sweep()` and `format_heartbeat()` are additive.

### 2. Data model (schema + legacy rows)

Added column `last_notified_ts INTEGER DEFAULT 0`. Legacy rows (pre-BC-035) would show `last_notified_ts=0` → first-ever heartbeat sweep after upgrade would re-poke every open finding at once (notification storm).

**Migration (in-branch per `breaking-changes-in-planning` discipline):** idempotent `UPDATE findings SET last_notified_ts = found_at WHERE last_notified_ts = 0 AND found_at > 0` in `init_db`. Treats "first ingestion" as "last notification" for legacy rows — approximation that holds because every ingestion sent the first notification. Keeps the sweep quiet on upgrade; stale rows will still trigger normally once `found_at > now - interval`.

### 3. Behavioral semantics

- **Operators who disabled all notifications via `BEACONRY_MOCK`**: unchanged. MOCK mode still short-circuits inside `TelegramAdapter._send`.
- **Operators who want zero heartbeat chatter**: `BEACONRY_HEARTBEAT_ENABLED=0`. Default is ON because silent-forever findings are the bug this story fixes.
- **Operators who want zero Telegram but keep the hub running** (e.g. during email-adapter development): `BEACONRY_NOTIFIER_TELEGRAM_ENABLED=0`. Pre-BC-035 impossible.
- **Noisier notifications once per week per open finding**: yes, by design. Opt-out via the env var above.

No breaking changes discovered during implementation or testing — planning matrix captured the surprises up front (`last_notified_ts=0` default would have caused a storm; adapter failure propagation would have crashed the sweep).

---

## Merge plan

1. All work on `feat/BC-035-notification-adapters-and-heartbeat`.
2. `make check` must pass on branch tip.
3. PR to `dev`; Claude opens, user runs `/ultrareview`, Claude runs `/code-review`.
4. Merge strategy: fast-forward (small, atomic, no squash needed).
5. Post-merge: story status → `shipped`, update `docs/stories/INDEX.md`, run `make live-rebuild`.

---

## Rollout plan

Additive. Operators who do nothing get the heartbeat sweep ON (desired default). Operators who want it OFF add `BEACONRY_HEARTBEAT_ENABLED=0` to their `.env` and restart hub. Legacy DBs backfill `last_notified_ts` from `found_at` on first boot — no manual migration.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Single adapter receives dispatch | unit | `test_notification_adapters.py::test_single_adapter_receives_dispatch` | ✓ |
| Multiple adapters all receive dispatch | unit | `test_notification_adapters.py::test_multiple_adapters_all_receive_dispatch` | ✓ |
| Disabled adapter is skipped | unit | `test_notification_adapters.py::test_disabled_adapter_is_skipped` | ✓ |
| Enabled by default when env unset | unit | `test_notification_adapters.py::test_enabled_by_default_when_env_var_unset` | ✓ |
| Failing adapter does not block siblings | unit | `test_notification_adapters.py::test_failing_adapter_does_not_block_siblings` | ✓ |
| Registry order preserved through failures | unit | `test_notification_adapters.py::test_registry_order_independent_of_failures` | ✓ |
| All four dispatch kinds reach each adapter | unit | `test_notification_adapters.py::test_all_four_dispatch_kinds` | ✓ |
| `enabled_adapters()` filters by env | unit | `test_notification_adapters.py::test_enabled_adapters_filters_by_env` | ✓ |
| TelegramAdapter.send_finding uses formatter + keyboard | unit | `test_telegram_adapter.py::test_send_finding_uses_formatter_and_keyboard` | ✓ |
| TelegramAdapter.send_regression passes ts | unit | `test_telegram_adapter.py::test_send_regression_passes_ts_to_formatter` | ✓ |
| TelegramAdapter.send_heartbeat passes open_since | unit | `test_telegram_adapter.py::test_send_heartbeat_passes_open_since` | ✓ |
| TelegramAdapter.send_digest skips empty | unit | `test_telegram_adapter.py::test_send_digest_skips_empty` | ✓ |
| TelegramAdapter.send_digest renders non-empty | unit | `test_telegram_adapter.py::test_send_digest_renders_when_nonempty` | ✓ |
| MOCK mode short-circuits + renderer path | unit | `test_telegram_adapter.py::test_mock_mode_short_circuits_with_renderer` | ✓ |
| MOCK mode without renderer is silent | unit | `test_telegram_adapter.py::test_mock_mode_without_renderer_is_silent` | ✓ |
| Missing tg_app is a warning not a crash | unit | `test_telegram_adapter.py::test_missing_tg_app_is_a_warning_not_a_crash` | ✓ |
| Open finding past interval gets reminded | unit | `test_heartbeat_sweep.py::test_stale_open_finding_gets_reminded` | ✓ |
| Recently notified finding is skipped | unit | `test_heartbeat_sweep.py::test_recently_notified_finding_skipped` | ✓ |
| Snoozed status is skipped | unit | `test_heartbeat_sweep.py::test_snoozed_finding_not_reminded` | ✓ |
| Accepted status is skipped | unit | `test_heartbeat_sweep.py::test_accepted_finding_not_reminded` | ✓ |
| Fixed status is skipped | unit | `test_heartbeat_sweep.py::test_fixed_finding_not_reminded` | ✓ |
| Absent-from-inventory is not reminded | unit | `test_heartbeat_sweep.py::test_absent_from_inventory_not_reminded` | ✓ |
| Operator opt-out via env | unit | `test_heartbeat_sweep.py::test_heartbeat_disabled_is_a_noop` | ✓ |
| `last_notified_ts` advances after reminder | unit | `test_heartbeat_sweep.py::test_sweep_updates_last_notified_ts` | ✓ |
| Audit event emitted per reminder | unit | `test_heartbeat_sweep.py::test_sweep_audit_event_emitted` | ✓ |
| Cold-boot safety (missing table) | unit | `test_heartbeat_sweep.py::test_sweep_tolerates_missing_table` | ✓ |
| CAP-T078 + CAP-T079 registered + bidirectional | verify-traceability | `docs/CAPABILITIES.md` diff | ✓ |
| Compose env propagation of 5 new vars | lint | `verify-compose-env-propagation` | ✓ |
| make check green | multi-gate | `make check` | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial (notes required) · ❌ open (blocks shipping).

---

## Testing strategy

- Unit-only: the adapter registry is pure Python, the Telegram adapter is isolated via injected getters, the heartbeat sweep is deterministic over a tmp SQLite DB.
- E2E coverage is implicit: existing BC-001 demo test exercises the MOCK fallback path (preserves `[mock] telegram send: ...` string the test asserts on); existing hub digest tests exercise the real adapter dispatch path via a capturing adapter registered in `_capture_digest`.
- Live rebuild + manual verify-fix on `nginx:1.24` runs post-merge per the live-rebuild discipline.

---

## Testing commands

```bash
make check
pytest poc/tests/unit/test_notification_adapters.py poc/tests/unit/test_telegram_adapter.py poc/tests/unit/test_heartbeat_sweep.py -v
```

Expected: all 26 BC-035 tests pass; `make check` all gates green.
