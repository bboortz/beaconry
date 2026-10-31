# BC-032 — Fix viability indicator

*Branch: `feat/BC-032-fix-viability-indicator`*
*Date: 2026-10-31*
*Story: `docs/stories/BC-032-fix-viability-indicator.md`*

## Situation

Operator screenshot 2026-10-31 showed 🔥 FIX REGRESSED notifications for `nginx:1.24` CVEs without:
1. Which version has the fix (missing in `format_regression`; present in `format_notification`)
2. Any indication that tapping 🔧 Re-fix will be a no-op loop (BC-014 same-track pull returns the same frozen bits for `:1.24`)

## Fix

New `assess_fix_viability(f) -> (label, reason)` function using a per-source_type heuristic. Rendered as one HTML line (`_viability_line`) and inserted into both notification bodies. `format_regression` also gets the "→ fixed in X" line it was missing.

## Scope

- `poc/hub/hub.py` — add `assess_fix_viability`, `_viability_line`; call from `format_notification` + `format_regression`
- `poc/tests/unit/test_hub_helpers.py` — 10 tests covering the heuristic (frozen docker tag, mutable tags, alpm with/without fixed_version, unknown source types, body-shape invariants)
- `docs/CAPABILITIES.md` — CAP-T074, CAP-T075

## Breaking changes

Per `docs/plan/_template.md § Breaking changes`:

### 1. API / return shape
None. Both formatters return strings as before.

### 2. Data model
None. Viability computed from existing Finding fields; no DB column added.

### 3. Behavioral semantics
Notification bodies gain one new line (`Fix viability: {marker} {label} — {reason}`). Operators get strictly more info; no one loses capability. Not opt-out configurable (would be anti-value — this is operator-facing clarity, not surveillance).

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Frozen docker semver tag → unlikely | unit test | `test_hub_helpers.py::test_viability_docker_frozen_semver_is_unlikely` | ✓ |
| Frozen patch version → unlikely (16-alpine case) | unit test | `test_hub_helpers.py::test_viability_docker_frozen_patch_version_is_unlikely` | ✓ |
| Docker :latest → likely | unit test | `test_hub_helpers.py::test_viability_docker_latest_is_likely` | ✓ |
| All mutable tag variants (main/stable/edge/nightly) → likely | unit test | `test_hub_helpers.py::test_viability_docker_mutable_variants_all_likely` | ✓ |
| alpm with fixed_version → likely | unit test | `test_hub_helpers.py::test_viability_alpm_with_fixed_version_is_likely` | ✓ |
| alpm without fixed_version → unknown | unit test | `test_hub_helpers.py::test_viability_alpm_without_fixed_version_is_unknown` | ✓ |
| Unknown source_type (python etc.) → unknown | unit test | `test_hub_helpers.py::test_viability_unknown_source_type_is_unknown` | ✓ |
| docker without :tag → treat as :latest → likely | unit test | `test_hub_helpers.py::test_viability_docker_no_tag_defaults_to_latest_likely` | ✓ |
| format_notification body includes viability + fixed in | unit test | `test_hub_helpers.py::test_format_notification_includes_viability_line` | ✓ |
| format_regression body includes viability + fixed in (operator-screenshot gap) | unit test | `test_hub_helpers.py::test_format_regression_includes_viability_and_fixed_version` | ✓ |
| CAP-T074 + CAP-T075 registered + bidirectional | verify-traceability | CAPABILITIES.md diff | ✓ |
| make check green | multi-gate | `make check` | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |
