# BC-127 — beaconry findings --severity filter

*Branch: `feat/BC-127-findings-severity-filter`*
*Start date: 2026-11-16*
*Story: `docs/stories/BC-127-findings-severity-filter.md`*

---

## Situation

Operator triaging Critical-only findings today does:

```
beaconry findings --status open --host X | grep -i Critical
```

Fragile — substring match on row text, loses header, terminal-width
dependent. BC-123 added `--cve` for the same forensic pattern;
`--severity` is the natural sibling.

## Goals

1. `--severity Critical` filter on `beaconry findings`.
2. Comma-separated multi-select (`--severity Critical,High`).
3. Case-insensitive input, canonical casing in SQL.
4. All three paths (local DB, HTTP, local-compose) carry the filter.
5. Makefile `SEVERITY=` pass-through.

## Scope

- `poc/scripts/findings.py`:
  - argparse: `--severity` flag.
  - `load_findings`: new `severity: str | None` param; normalizes to
    a list, adds `WHERE severity IN (?, …)` branch.
  - `_findings_via_http`: pass `severity` query param.
  - `_run_in_container`: thread through the inline script's
    `##SEVERITY_FILTER##` placeholder.
  - `main`: wire `args.severity` through all three paths; extend
    the no-match message with severity hint.
- `poc/hub/hub.py::http_findings`: new `severity` query param,
  same IN-clause logic.
- `Makefile`: `make findings SEVERITY=Critical` pass-through.
- `poc/tests/unit/test_BC_127_findings_severity_filter.py` — new.
- `docs/CAPABILITIES.md`: CAP-T230.

## Non-goals

- Numeric CVSS filter (`--cvss-min`). Separate story if needed.
- Changing the table rendering.
- Deprecating `--status` or `--host`.

## Design decisions

- **Comma-separated input** matches `beaconry audit --event a,b`
  precedent so operators don't learn two conventions.
- **Case-insensitive input**: `critical` → `Critical` via
  `.strip().title()` — grype's output uses title-case so
  operators typing lowercase still match.
- **SQL `WHERE severity IN (?, …)`** not `LIKE` — exact match only.
  Input "foo" simply returns zero rows, not partial substrings.
- **No validation** against the known set. `--severity Catastrophic`
  returns empty cleanly; operators see what they searched for.

## Breaking changes

### 1. API / return shape

- `/findings` endpoint gains optional `severity` query param. Pure
  additive; omitted = no filter.

### 2. Data model
None.

### 3. Behavioral semantics

- Pre-BC-127 CLI version that calls BC-127 hub: no change (CLI
  doesn't send `severity`).
- Pre-BC-127 hub called by BC-127 CLI: silently ignores the query
  param, returns all findings (hub doesn't filter).

## Merge plan

1. `make check` passes.
2. PR → dev; auto-merge on green CI.
3. Bump v0.22.10 → v0.22.11; forge rebuild.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Single-severity filter on local DB | unit | `test_BC_127_findings_severity_filter.py::test_load_findings_single_severity` | ✓ |
| Comma-separated multi-select | unit | `test_load_findings_multi_severity` + `test_http_findings_severity_multi_select` | ✓ |
| Case-insensitive | unit | `test_load_findings_lowercase_severity_matches_canonical` + `test_http_findings_case_insensitive_severity` | ✓ |
| Combines with existing filters | unit | `test_load_findings_combined_filters` | ✓ |
| HTTP endpoint severity query | unit | `test_http_findings_severity_filter` | ✓ |
| Makefile SEVERITY= passes through | unit | `test_makefile_severity_pass_through` | ✓ |
| Invalid severity → zero rows | unit | `test_load_findings_unknown_severity_returns_empty` | ✓ |
| CLI parses --severity flag | unit | `test_parse_args_accepts_severity` | ✓ |
| Normalization helper (title case, comma split, empty) | unit | `test_normalize_single_severity_title_case` + `test_normalize_comma_separated` + `test_normalize_empty_returns_none` | ✓ |
| Memory drift pattern added | N/A | pure additive | ✓ |

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_127_findings_severity_filter.py -v
```
