# BC-007 — `make findings` CLI

*Branch: `feat/BC-007-findings-cli`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-007-findings-cli.md`*

---

## Situation

Operators lose track of what they've already actioned. Telegram scrollback isn't a system of record. The dev DB (`.beaconry-cache/beaconry.db`) already holds every finding + status (open/snoozed/accepted/fixed), but nothing surfaces it. BC-007 adds a read-only CLI reporter.

## Problems to solve

- No way to answer "what did I accept yesterday" without SQLite CLI gymnastics
- Operator needs to know the current state across all images after many Telegram sessions
- Dashboard (BC-008 candidate) is overkill for the current need

## Goals

1. `make findings` prints a table from the dev DB; sorted by (status, tier, -cvss)
2. `make findings STATUS=<state>` filters to one status
3. Empty DB → friendly guidance, exit 0
4. Pure read — no HTTP, no docker, no writes to the DB
5. Honors `DATA_DIR` env so Compose named volume path works too
6. Long fields truncate with "…" so terminal table doesn't wrap
7. Register CAP-B012 + CAP-T025

## Scope

- `poc/scripts/findings.py` — new module exposing `main(argv)` and pure `format_table(rows, max_widths)` helper
- `Makefile` — new `findings` target: `python3 -m poc.scripts.findings $(if $(STATUS),--status $(STATUS))`
- `poc/tests/integration/test_BC_007_findings_cli.py` — Gherkin scenarios as pytest
- `docs/CAPABILITIES.md` — CAP-B012 + CAP-T025
- `README.md` — mention `make findings` in dev-quickstart
- This plan doc

## Non-goals

- Writing to the DB from this CLI (no `make findings SET-STATUS=…` or similar — read-only)
- Web UI
- CSV/JSON export (future flag)
- Color output
- Multi-host (single DB view)
- Pagination
- Interactive filtering (shell `grep` is enough)

## Design decisions

**Pure function + thin shell.** `format_table(rows, columns, max_widths)` is a pure function; `main(argv)` opens the SQLite DB and calls it. Tests exercise the pure function with synthetic rows and the main-level integration via subprocess.

**No argparse dependency on the Make side.** `make findings STATUS=accepted` passes an env/arg; the Python side uses `argparse` to parse `--status`. Keeps the Make interface friendly.

**`DATA_DIR` resolution matches hub.** `os.environ.get("DATA_DIR", ".beaconry-cache")` — same default the hub uses in Compose vs local, so one target serves both setups.

**No new dependencies.** stdlib `sqlite3`, `argparse`, `textwrap`. Rich / tabulate would be nice but add install cost and new attack surface for a trivial table.

**Truncation at the column level, not the row level.** `textwrap.shorten` with a per-column max length. CVE id and status columns stay full width (fixed-length values); long fields (description, image name, package name) get the "…".

**Rejected: ANSI color.** Keep it plain; color-on-tty logic has edge cases (NO_COLOR env, pipes, dumb terminals). Add later if operators ask.

**Rejected: show description field.** It's long and already in the Telegram message. The CLI goal is state, not re-reading content.

## Breaking changes

None. New read-only target.

## Merge plan

1. Plan doc + story + red test
2. Implement reporter + make target
3. `make check` + `make check-full` green
4. Register CAPs; verify-traceability clean
5. Flip story → shipped; fast-forward merge to dev

## Rollout plan

Additive. First-time users just type `make findings`.

## Testing strategy

- Unit test: `format_table` on synthetic rows — truncation, sort order, column alignment
- Integration test: subprocess calls `python -m poc.scripts.findings` against a tmp DB — asserts output rows, filter behavior, empty-DB message, exit codes
- Pure-read invariant: assert DB file mtime unchanged after invocation

## Testing commands

```bash
source .venv/bin/activate
make check
make findings                   # empty DB? friendly message. Else table.
make findings STATUS=accepted   # filter example
```
