# BC-003 — Live-verified Telegram button handling

*Branch: `feat/BC-003-live-verified-buttons`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-003-live-verified-buttons.md`*

---

## Situation

BC-002 wired the three Telegram buttons (🔧 Fix now, ⏰ Snooze 4h, ✅ Accept risk) and shipped — but nobody ever tapped them live. Unit tests cover the DB write paths; `on_button_click` has test coverage for the fix-stub flow. What's never been verified: that an operator's actual tap routes through python-telegram-bot's polling loop, lands at `on_button_click`, finds the finding in the dev DB, updates its status, and edits the message in-place.

BC-003 closes that gap. Two sub-concerns: (a) a *malformed* callback_data payload should be rejected cleanly instead of silently misrouting (security operator critique from story time), and (b) a *double-tap* of the same button must be safely idempotent (future-me critique). Both have automated unit coverage before the live session.

## Problems to solve

- Buttons were shipped under BC-002 with no live evidence — just unit tests
- `on_button_click` currently calls `query.data.split(":")` with no validation — crafted payloads could misroute
- Nothing today handles double-tap of the same button; the handler would try to flip status twice and edit the message twice (second edit = same content = Telegram error at worst)

## Goals

1. **Callback-data validation**: regex `^(fix|snooze|ack):[a-f0-9]{16}(:\d+)?$` — anything else gets a `⚠️ invalid request` edit, no DB mutation, warning logged
2. **Idempotence**: tapping Snooze on an already-snoozed finding re-shows the current status in the edit (e.g., `⏰ Already snoozed`), does not re-write the DB
3. **README adds a short "Button actions" section** naming what each button does (Snooze = status snoozed, Accept = accepted, Fix now = stub message)
4. **Dev DB filename reconciled** across story + code: story Gherkin referenced `dev.db`; code uses `beaconry.db`. Pick `beaconry.db` (code wins); update story Gherkin
5. **Live tap session** (Phase 3): operator taps each button on their phone, verifies DB flip + message edit for Snooze/Accept/Fix-now stub/double-tap idempotence
6. **Register CAPs**: CAP-B007 (Snooze/Accept respect across runs), CAP-B008 (Fix-now honest stub), CAP-T028 (callback validation), CAP-T029 (button idempotence)

## Scope

- `poc/hub/hub.py` — callback_data regex validation at top of `on_button_click`; idempotence pre-check (if DB status already matches intent, edit with current state and return early)
- `poc/tests/unit/test_hub_helpers.py` — new tests: malformed callback rejected, double-snooze no-ops, double-accept no-ops
- `docs/CAPABILITIES.md` — 4 new CAPs
- `docs/stories/BC-003-live-verified-buttons.md` — reconcile dev DB filename in Gherkin
- `README.md` — "Button actions" section
- This plan doc with Shipping evidence table (will be filled progressively)

## Non-goals

- Fix-execution itself (BC-004+ territory; stub remains)
- Snooze expiration / cron (just stores status)
- Multi-finding batch actions
- Authn/authz on the bot (only configured chat_id can tap)
- Changing message format

## Design decisions

**Regex validation at the top of `on_button_click`.** First thing after `query.answer()`. Rejects before any DB work. One place to maintain.

**Idempotence via status check, not generation count.** If `finding.status != "open"` and the tap would flip to a non-open status, respond with "already X" instead of flipping. Simple; no new DB columns.

**Double-tap of Fix-now (which doesn't flip status under current `BEACONRY_FIX_ENABLED` default) → same stub message both times.** Operator sees "coming in BC-004" both taps; benign.

**Keep the regex permissive on the suffix.** `:(\d+)?` so `snooze:<id>:4` works today and future-growth like `snooze:<id>:24` works without regex change.

**Rejected: making idempotence a DB transaction (SELECT FOR UPDATE).** Overkill for a single-user bot; SQLite's write-lock serializes already.

## Breaking changes

None visible to the operator — new behavior is strictly more defensive. Any existing valid payload keeps working.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Callback validation rejects malformed payloads | unit test | `test_on_button_click_rejects_malformed_callback` | ✓ |
| Well-formed callback is accepted + routes to DB | unit test | `test_on_button_click_accepts_well_formed_callback` | ✓ |
| Double-tap Snooze is idempotent | unit test | `test_double_tap_snooze_is_idempotent` | ✓ |
| Double-tap Accept is idempotent | unit test | `test_double_tap_accept_is_idempotent` | ✓ |
| Snooze updates DB + edits message (live) | live manual | awaits Phase 3 session | ❌ |
| Accept-risk updates DB + edits message (live) | live manual | awaits Phase 3 session | ❌ |
| Fix-now stub honored (live) | live manual | awaits Phase 3 session | ❌ |
| Double-tap safe (live) | live manual | awaits Phase 3 session | ❌ |
| README documents button actions + fix stub wording | README diff | "Telegram button actions" section added | ✓ |
| Dev DB filename consistent story-to-code | grep sanity | BC-003 story Gherkin updated `dev.db` → `beaconry.db` | ✓ |
| CAP-B007/B008/T028/T029 registered | verify-traceability | 40 CAPs, 0 mismatches 2026-10-30 | ✓ |

(Rows flip to ✓ as evidence lands. Phase 3 live rows are the gating ones — Phase 2 can't ship without them.)

## Merge plan

1. Phase 1 (this commit batch): plan doc, red unit tests, BC-003 story reconciled
2. Phase 2: implement validation + idempotence, update README + CAPs, run `make check-full`
3. **PAUSE for Phase 3 live session with user** — Shipping evidence table has 4 ❌ rows blocking shipped
4. Phase 3: live tap session, I inspect DB, flip rows to ✓
5. Fast-forward merge to dev once every row is ✓

## Rollout plan

Additive. Any existing valid callback keeps working. New rejection path adds safety.

## Testing strategy

Phase 1/2 automated:
- Unit tests on `on_button_click` with mocked `telegram.Update` + `query.edit_message_text`
- Fakes for load_finding / save_finding / set_status to isolate the handler

Phase 3 manual:
- `make demo` running (Compose or local-Python mock — probably Compose to exercise the full real stack)
- User taps each button
- Claude inspects `.beaconry-cache/beaconry.db` or the Compose volume via `make findings`
- Four live scenarios ticked ✓

## Testing commands

```bash
source .venv/bin/activate
make check-full                                   # fast tier + opt-in markers
pytest poc/tests/unit/test_hub_helpers.py -v      # unit tier
```
