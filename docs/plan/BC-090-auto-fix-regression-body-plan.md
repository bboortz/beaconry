# BC-090 — Auto-fix regression body actually reaches Telegram

*Branch: `feat/BC-090-auto-fix-regression-body`*
*Start: 2026-11-05*
*Story: `docs/stories/BC-090-auto-fix-regression-body.md`*

---

## Situation

`notify_auto_fix_regression` (hub.py) only uses
`format_auto_fix_regression` on the MOCK/no-adapter branch. The real
path at `hub.py:await _dispatch_regression(finding, auto_fixed_ts,
last_seen_ts)` routes through `notifiers.notify_regression` →
`TelegramAdapter.send_regression` which renders
`format_regression` — the **wrong** body. The comment at the call
site claims "the text body is already shaped by
format_auto_fix_regression upstream" — demonstrably false on the
Telegram path.

BC-086 CAP-T178 required a distinct body. The format helper exists,
is unit-tested in isolation, but is dead code where it matters.

## Problems to solve

- Operator sees `🔥 FIX REGRESSED` for an auto-fix regression,
  which semantically differs from a BC-031 regression and deserves a
  distinct visual signal.
- `notify_sent` audit event has `kind="auto_fix_regression"` but the
  payload shape (body) doesn't reflect the kind.

## Goals

1. Real Telegram body renders `format_auto_fix_regression`.
2. Audit continues to use `kind="auto_fix_regression"` on
   `notify_sent` / `notify_sent_failed`.
3. Operator gets a per-kind silence knob
   `BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED` consistent with
   BC-089's `BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE`.

## Scope

- `poc/hub/notifiers/base.py`: add `send_auto_fix_regression` method
  (default no-op; same pattern as BC-089 `send_snooze_expired`).
- `poc/hub/notifiers/__init__.py`: add `notify_auto_fix_regression`
  dispatcher.
- `poc/hub/notifiers/telegram.py`: add `send_auto_fix_regression`
  implementation; use `"auto_fix_regression"` formatter key.
- `poc/hub/hub.py`:
  - Add `"auto_fix_regression": format_auto_fix_regression` to the
    TelegramAdapter formatters dict.
  - Modify `notify_auto_fix_regression` to call the new dispatcher
    (not `_dispatch_regression`).
  - New env var parsed near AUTO_FIX_THRESHOLD block.
  - Import `notify_auto_fix_regression as _dispatch_auto_fix_
    regression` from the notifiers package.
- `docs/VARIABLES.md`, `poc/.env.example`, `poc/docker-compose.yml`:
  one new row each for the env var.
- `docs/CAPABILITIES.md`: CAP-T185, CAP-T186.
- `docs/stories/INDEX.md`: BC-090 row.
- `CHANGELOG.md`: v0.14.0 entry.
- Tests:
  - `poc/tests/integration/test_BC_090_auto_fix_regression_body.py`
    — the four Gherkin scenarios.
  - `poc/tests/unit/test_notification_adapters.py` — stub adapter
    gains `send_auto_fix_regression` to satisfy the new dispatch.
  - `poc/tests/unit/test_telegram_adapter.py` — assert
    TelegramAdapter.send_auto_fix_regression renders the distinct
    formatter.
- Version 0.13.0 → **0.14.0** (minor — new env var + behaviour fix
  changes operator-visible Telegram output).

## Non-goals

- Rewriting the keyboard shape for auto-fix regressions (still Fix /
  Accept).
- Backfilling pre-BC-090 regressed_from_auto_fix rows (there's no
  stored "body" to backfill; each notification is one-shot).
- Removing the `kind=auto_fix_regression` string from audit —
  unchanged.

## Design decisions

- **New adapter method, not formatter_key arg**: the alternative
  (pass formatter key through adapter dispatch) adds dispatch
  flexibility we don't need. One method per notification kind
  matches the existing pattern (send_finding / send_regression /
  send_heartbeat / send_digest / send_snooze_expired). Adds one
  more method; stays consistent.
- **Default-no-op base method**: not `@abstractmethod`. Same as
  BC-089's `send_snooze_expired`. Out-of-tree adapters (none today)
  stay back-compat; they can opt-in later. In-tree TelegramAdapter
  overrides with the real body.
- **Env var default `1` (loud)**: matches CAP-T178 operator expectation
  — regressions scream.
- **Independent of adapter-level toggle**: per-adapter env
  `BEACONRY_NOTIFIER_TELEGRAM_ENABLED=0` already silences Telegram
  entirely; per-kind env BC-090 adds is a finer dial for
  "I want everything on Telegram **except** auto-fix regressions".

## Breaking changes

### API / return shape

**None for stable callers.** Adapter base default no-op means pre-BC-090
adapters stay bootable. Test fixtures that mock adapters may need to
add `send_auto_fix_regression` if they strict-type-check — updated
in this PR.

### Data model

**None.** No schema change.

### Behavioral semantics

**Yes, intended.** Operators who saw `🔥 FIX REGRESSED` for an
auto-fix regression now see `🔥 AUTO-FIX REGRESSED` with distinct
body. Loud in CHANGELOG. Audit `notify_sent kind=auto_fix_regression`
was already correct; only the Telegram render changes.

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Scenario 1 — distinct body on Telegram | integration | `test_BC_090_auto_fix_regression_body.py::test_telegram_renders_distinct_body` | ✓ |
| Scenario 2 — opt-out suppresses Telegram, keeps audit | integration | `test_BC_090_auto_fix_regression_body.py::test_opt_out_suppresses_telegram` | ✓ |
| Scenario 3 — default loud | integration | `test_BC_090_auto_fix_regression_body.py::test_env_default_enabled` | ✓ |
| Scenario 4 — adapter back-compat | integration | `test_BC_090_auto_fix_regression_body.py::test_adapter_without_override_no_ops` | ✓ |
| Notifier dispatch exists | integration | `test_BC_090_auto_fix_regression_body.py::test_notifiers_package_exports_dispatcher` | ✓ |
| Env var default enabled | integration | `test_BC_090_auto_fix_regression_body.py::test_env_default_enabled` | ✓ |
| Env var parse variants | integration | `test_BC_090_auto_fix_regression_body.py::test_env_parse_variants[0-False]` | ✓ |
| Telegram adapter picks distinct formatter | integration | `test_BC_090_auto_fix_regression_body.py::test_telegram_adapter_picks_distinct_formatter` | ✓ |
| VARIABLES + env + compose sync | lint | `make verify-variables` + `verify-compose-env-propagation` | ✓ |
| CAP-T185 + T186 registered | lint | `make verify-traceability` | ✓ |
| Fast + full tier green | gate log | `make check` + `make check-full` | ✓ |

## Rollout

On upgrade:
- Hub boots; new env defaults to loud (`1`).
- Next `regressed_from_auto_fix` fire renders the distinct body.
- No fleet-wide migration; each host rolls own hub.
- Operator who wants silence: set
  `BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED=0` in `.env`,
  rebuild hub.

## Follow-up for BC-088 LIFECYCLE.md

After PR #6 + this PR merge, update §15.1 of `docs/LIFECYCLE.md`:
strike the inconsistency (now fixed) and note BC-090 CAPs. Mentioned
in the BC-088 branch chore.
