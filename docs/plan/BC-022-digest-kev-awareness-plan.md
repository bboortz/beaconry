# BC-022 — Digest KEV awareness

*Branch: `feat/BC-022-digest-kev-awareness`*
*Start date: 2026-10-31*
*Story: `docs/stories/BC-022-digest-kev-awareness.md`*

---

## Situation

BC-020 added `Finding.kev: bool` + wired `urgency.classify_tier` into the ingest path + prepended a `🚨 KEV — EXPLOITED IN THE WILD` header on individual notifications when `f.tier == 0`. BC-020 did NOT touch `send_digest`.

`dedup_and_cap` sorts findings by `(tier, -cvss)` and notifies the top N individually; the overflow goes to `send_digest`. The sort order means KEV findings (tier=0) win top-N slots over any CVSS non-KEV (tier≥2) — so **usually** the suppressed set is non-KEV by construction. BUT when `#KEV > BEACONRY_MAX_FINDINGS_PER_RUN`, the overflow is itself KEV. The current digest renders those rows with the same prose shape as non-KEV findings:

```
📋 N additional finding(s) suppressed this run
• CVE-… Critical CVSS 9.8 — pkg
```

Operator sees a flat list with no urgency signal. Tier 0 findings hidden in a prose bullet list is the exact failure mode BC-020 was built to prevent for individual notifications.

## Problems to solve

- Digest rows don't flag KEV findings — breaks CAP-B019's at-a-glance differentiation when #KEV > cap.
- Operator has no way to tell from the digest alone whether to raise the cap or ignore.

## Goals

1. Each digest row for a KEV finding starts with `🚨 ` (Scenario 1).
2. If any suppressed finding is KEV, insert a count banner immediately after the header: `⚠️ <b>N suppressed finding(s) are KEV</b> — raise BEACONRY_MAX_FINDINGS_PER_RUN to see them individually.` (Scenario 2).
3. No banner when zero suppressed findings are KEV (Scenario 3).
4. Empty-list behavior unchanged (Scenario 4).

## Scope

- **Modified:** `poc/hub/hub.py::send_digest` — ~10 LOC addition.
- **Modified:** `poc/tests/unit/test_hub_helpers.py` — 4 Gherkin scenarios as unit tests (TestClient not needed; `send_digest` is pure render + side-effect, we monkeypatch `tg_app.bot.send_message`).
- **Modified:** `docs/CAPABILITIES.md` — register CAP-T062.
- **Modified:** `docs/stories/INDEX.md` — BC-022 row.

## Non-goals

- Not changing dedup/cap sort order.
- Not changing default cap or the top-10 truncation.
- Not adding tier labels beyond KEV (Tier 1/2/3 would be noise in a digest meant to say "these are the lowest-priority things we didn't surface").
- Not touching the smoke demo output format — operator eyeballs digest in Telegram, not stdout.

## Design decisions

- **Prefix vs separate column:** `🚨 ` prefix is used because Telegram-rendered bullet lists don't have aligned columns — prefix survives any line-wrap. Mirrors BC-020's header-line convention.
- **Banner line position:** immediately after the header, before any bullets, so operators see it even if they only glance at the top of the message.
- **No tier banner for non-KEV:** we don't say "N suppressed are Tier 2" — tier 2/3 in a digest is boring by definition. Only KEV warrants attention.
- **Still truncate at 10 rows:** cap doesn't change. If `all 42 suppressed are KEV`, the banner surfaces "42 are KEV" and only the top 10 render — that's enough info for the operator to raise the cap.

## Breaking changes

None. Message content changes but no API/schema.

## Merge plan

1. Branch `feat/BC-022-digest-kev-awareness`
2. Red unit tests (4 Gherkin scenarios) → red
3. Patch `send_digest` → green
4. Register CAP-T062, INDEX update
5. `make check` green
6. FF to `dev`

## Rollout plan

Rebuild hub to pick up the change (post-merge).

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario 1 — KEV row gets 🚨 prefix | unit test | `test_hub_helpers.py::test_digest_row_prefixes_kev_with_siren` | ✓ |
| Scenario 2 — Suppressed-KEV banner present when any kev | unit test | `test_hub_helpers.py::test_digest_banner_fires_when_any_suppressed_is_kev` | ✓ |
| Scenario 3 — No banner when zero suppressed are KEV | unit test | `test_hub_helpers.py::test_digest_no_banner_when_no_kev` | ✓ |
| Scenario 4 — Empty list no-op (unchanged) | unit test | `test_hub_helpers.py::test_digest_empty_list_no_send` | ✓ |
| CAP-T062 registered + bidirectional | verify-traceability | CAPABILITIES.md diff | ✓ |
| make check green | multi-gate | `make check` | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |

## Testing strategy

Unit test the render by monkeypatching `tg_app.bot.send_message` to capture the body, then assert on substrings. `send_digest` is async + side-effecting; standard pattern used by other hub_helpers tests.

## Testing commands

```bash
source .venv/bin/activate
pytest poc/tests/unit/test_hub_helpers.py -k digest -v --no-cov
make check
```
