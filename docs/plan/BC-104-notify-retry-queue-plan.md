# BC-104 — Notification retry queue

*Branch: `feat/BC-104-notify-retry-queue`*
*Story: `docs/stories/BC-104-notification-retry-queue.md`*

## Scope

- `poc/hub/hub.py`: new schema v8 → v9 (`pending_notifications` table);
  three new env vars; `_queue_pending_notification` hook writer;
  `notify_retry_sweep` + `_notify_retry_loop_forever` background
  task; lifespan registration.
- `poc/hub/notifiers/telegram.py`: `TelegramAdapter` gains `pending_writer`
  constructor arg; `_send` catches + queues on failure; `_maybe_queue`
  helper serializes keyboard via `.to_json()`; `replay(text, rm_json)`
  method replays queued rows; every `send_*` call passes `kind=`.
- `poc/docker-compose.yml` + `poc/.env.example` + `docs/VARIABLES.md`:
  three new env rows.
- `docs/CAPABILITIES.md`: CAP-T206.
- `CHANGELOG.md`: v0.19.0 entry.
- `VERSION`/`poc/VERSION`/`pyproject.toml`: 0.18.1 → 0.19.0.
- Sibling schema-version assertions in BC-066/BC-086/BC-092 bumped to 9.

## Shipping evidence

| Goal | Pointer | Status |
|---|---|---|
| Schema v9 adds pending_notifications | `test_BC_104_notify_retry.py::test_schema_v9_adds_pending_notifications` | ✓ |
| Queue hook writes a row + audits | `test_BC_104_notify_retry.py::test_queue_pending_notification_writes_row` | ✓ |
| Sweep delivers + deletes | `test_BC_104_notify_retry.py::test_sweep_delivers_and_deletes` | ✓ |
| Sweep requeues with exponential backoff | `test_BC_104_notify_retry.py::test_sweep_requeues_on_failure_with_backoff` | ✓ |
| Sweep abandons after MAX_ATTEMPTS | `test_BC_104_notify_retry.py::test_sweep_abandons_after_max_attempts` | ✓ |
| Sweep skips not-due rows | `test_BC_104_notify_retry.py::test_sweep_skips_not_due` | ✓ |
| Env defaults sane | `test_BC_104_notify_retry.py::test_env_defaults_reasonable` | ✓ |
| CAP-T206 registered | `make verify-traceability` | ✓ |
