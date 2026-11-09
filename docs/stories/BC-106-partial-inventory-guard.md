---
id: BC-106
title: Partial-inventory guard — scout reports expected/collected ratio
status: shipped
created: 2026-11-09
branch: feat/BC-106-partial-inventory-guard
plan: docs/plan/BC-106-partial-inventory-guard-plan.md
---

# BC-106: Scout explicitly tells hub when a scan was partial

## Narrative

BC-067 added a hub-side partial-inventory heuristic: if the scan's
`sbom_count` is < 50% of the rolling baseline, skip reconcile with
a `reconcile_skipped_partial_inventory` audit. The heuristic is
useful but **inferential** — hub guesses from the count delta alone.
Edge cases where the heuristic misses:

- Scout syft timed out on **one** image out of 50 → `sbom_count=49`
  vs baseline `50` is well above the 50% floor → reconcile fires →
  that one image's findings `absent_count++` (false-positive
  progress toward auto_fixed).
- Scout starts up fresh after an operator cleaned up (fewer images
  on host) → new baseline < old baseline → the next scan with the
  real count looks "partial" until baseline catches up.

BC-106 makes scout explicitly report per-image success:

- `images_expected` — count of images the SCOUT_INCLUDE filter
  matched (what scout tried to scan).
- `images_collected` — count that syft succeeded on.
- `scan_complete` — convenience boolean: `collected == expected`.

Hub's receive_scan reads these fields. If present AND
`collected/expected < BEACONRY_PARTIAL_SCAN_MIN_RATIO` (default
0.95), skip reconcile with the existing `reconcile_skipped_partial_inventory`
audit (adding the explicit ratio for forensics). If the fields are
absent (pre-BC-106 scouts), the BC-067 baseline heuristic still
applies — no regression.

## Expected outcome

- Scout `collect_and_post` adds three fields to the POST payload.
- Hub `receive_scan` reads them; explicit ratio takes precedence
  over the BC-067 baseline heuristic when available.
- One new env: `BEACONRY_PARTIAL_SCAN_MIN_RATIO` (default `0.95`,
  `0` disables).
- Audit shape extended: `reconcile_skipped_partial_inventory` gains
  `ratio`, `expected`, `collected` fields when the new path triggers.
- Backward compat: pre-BC-106 scouts work unchanged (fields omitted
  → BC-067 heuristic applies).

## Out of scope

- Per-image success/failure reporting (just counts, not names).
- Retry of failed syft calls — scout keeps BC-093's "trigger next
  scan" model.

## Capabilities delivered

- **CAP-T208: Scout-reported inventory completeness + hub
  threshold-based reconcile skip.** Strengthens CAP-T151 (BC-067
  baseline heuristic) with an explicit per-scan signal.

## Links

- BC-067 (predecessor — baseline heuristic).
- BC-086 (POST-driven reconcile — consumer of the "proceed" signal).
