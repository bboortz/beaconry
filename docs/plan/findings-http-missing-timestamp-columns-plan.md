# fix/findings-http-missing-timestamp-columns — First seen / Last change / Last seen populated over HTTP

*Branch: `fix/findings-http-missing-timestamp-columns`*
*Start date: 2026-11-15*

---

## Situation

Operator on bb-82jq ran `beaconry findings --host bb-82jq --status open`
after v0.22.4 landed. Every row's `First seen` and `Last change` cells
were empty. All DB rows have those values (schema v10 + v11 both back
them), so the DB side is fine.

Root cause: `poc/hub/hub.py::_FINDINGS_ENDPOINT_COLS` (the tuple
driving the HTTP `/findings` serializer) omits `found_at`,
`last_status_change_ts`, `last_seen_ts`, `close_reason`,
`cross_repo_hint`. Those are therefore absent from the JSON the CLI
receives. The CLI's HTTP path (`_findings_via_http`) then renders via
`format_table(rows)` without ever calling the `_fmt` derivation that
the local-DB path (`load_findings`, lines 178-181 pre-fix) runs.

Separate but related request: add a `Last seen` column so operators
can answer "when did we last confirm this finding was still present?"
at a glance — `last_seen_ts` already lives in the DB (BC-034) but the
CLI never surfaced it.

## Problems to solve

- `beaconry findings` on a remote hub renders First seen / Last change
  as empty on every row (CAP-T207 regression over HTTP).
- No column exposes `last_seen_ts`; operators have to query the DB
  directly or guess from "last scan" on the FLEET row.
- The HTTP and local-DB paths have diverged formatters (local derives
  `_fmt` keys, HTTP doesn't) — a latent class of column regressions.

## Goals

1. Hub's `/findings` endpoint serializes the three raw timestamps + the
   two "suffix source" columns the CLI's formatter reads.
2. CLI has a single `_decorate_rows` helper called from BOTH paths.
3. New `Last seen` display column backed by `last_seen_ts`.
4. Regression lock: a test asserts `_FINDINGS_ENDPOINT_COLS` includes
   every timestamp column the CLI formats.

## Scope

- `poc/hub/hub.py` — extend `_FINDINGS_ENDPOINT_COLS` with `found_at`,
  `last_status_change_ts`, `last_seen_ts`, `close_reason`,
  `cross_repo_hint`.
- `poc/scripts/findings.py` — extract `_decorate_rows` helper; add
  `Last seen` column + `last_seen_fmt` key + `last_seen_ts` in
  `SORT_ONLY_COLUMNS`; call the helper from `_findings_via_http`.
- `poc/tests/unit/test_findings_timestamp_columns.py` — new unit
  regressions: column plumbing, per-field derivation, combined
  format_table render, hub endpoint contract.
- `poc/tests/integration/test_BC_007_findings_cli.py` — bump the
  "line too long" cap (170 → 185) to accommodate the new column.
- `docs/CAPABILITIES.md` — extend CAP-T207 description + verified-by.

## Non-goals

- Changing the `last_seen_ts` write path (BC-034 is untouched).
- Adding a `--since` filter (YAGNI — the age cells already answer this).
- Fixing other HTTP/local parity gaps beyond findings (if any).

## Design decisions

- **Single `_decorate_rows` helper vs. inline duplication**: shared
  helper eliminates the drift pattern that caused this bug. Cheap
  (~15 LOC) and the invariant "HTTP rows render identically to
  local-DB rows" is now one call away from each caller.
- **Expand endpoint cols vs. keep narrow**: expand. The endpoint is
  internal (gated by HUB_CLI_TOKEN), rows are small (int ts), and the
  alternative — doing all formatting server-side — would couple
  presentation to the hub, preventing future CLI-side features like
  `--format=json` or color.
- **Column position** (Last seen after Last change): both are recency
  signals; grouping them keeps the operator's eye in one spot for
  "when did anything change recently?" vs. First seen at the far
  right as the anchor date.

## Breaking changes

### 1. API / return shape

**`/findings` JSON gains 5 new keys** per row (the raw timestamps +
close_reason + cross_repo_hint). Callers that iterate with `.get()`
(the CLI, the `beaconry status` fleet view) tolerate extra keys —
confirmed by grep. External scripts that may be parsing /findings
output are informal; adding keys is additive by HTTP convention.

### 2. Data model

**None.** No schema change — every column added to the endpoint
already exists in the schema (BC-034 for `last_seen_ts`, BC-105 for
`found_at`/`last_status_change_ts`/`close_reason`, BC-117 for
`cross_repo_hint`).

### 3. Behavioral semantics

- **Operators who use `beaconry findings`**: three previously-empty
  columns are now populated; a new "Last seen" column appears.
  Readable improvement, no silent surprise.
- **Row width**: ~170 → ~185 chars. Still under typical terminal
  width (189/198 for Prompt-flavoured 110×24 or 132×50 terms); matches
  the line-cap bump in BC-007's regression test.

## Merge plan

1. All work on `fix/findings-http-missing-timestamp-columns`
2. `make check` passes on branch tip
3. PR to `dev`; Claude opens; user can `/ultrareview` or straight-merge
4. Squash merge
5. Post-merge: no story to flip — this is a `fix/*` branch

## Rollout plan

Additive. On next deploy:
- Operators see three populated columns + a new Last seen column.
- Any external /findings consumers see extra keys (tolerated).

## Testing strategy

Unit-only coverage is sufficient — this is a formatter wiring fix.
The contract-lock test on `_FINDINGS_ENDPOINT_COLS` prevents the
exact regression that caused the original bug.

## Testing commands

```bash
make check
pytest poc/tests/unit/test_findings_timestamp_columns.py -v
```
