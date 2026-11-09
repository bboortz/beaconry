# BC-106 — Partial-inventory guard strengthened

*Branch: `feat/BC-106-partial-inventory-guard`*
*Story: `docs/stories/BC-106-partial-inventory-guard.md`*

## Scope

- `poc/scout/scout.py`:
  - `scan_docker_images` tracks `expected` + `collected` locally.
  - Module globals `_last_scan_images_expected` + `_last_scan_images_collected`
    updated at the end of each scan.
  - `collect_and_post` adds `images_expected`, `images_collected`,
    `scan_complete` to the POST payload.
- `poc/hub/hub.py`:
  - `receive_scan` reads the three new fields (optional — pre-BC-106
    scouts omit them).
  - New env `BEACONRY_PARTIAL_SCAN_MIN_RATIO` (default 0.95, min 0, max 1.0).
  - When `images_expected > 0` and `collected/expected < ratio`,
    skip reconcile with `reconcile_skipped_partial_inventory` audit
    carrying the explicit numbers.
  - BC-067 baseline heuristic stays as fallback.
- `poc/docker-compose.yml` + `poc/.env.example` + `docs/VARIABLES.md`:
  one new env row.
- `docs/CAPABILITIES.md`: CAP-T208.
- `CHANGELOG.md`: v0.19.2 entry.
- `VERSION`: 0.19.1 → 0.19.2.
- Unit tests under `poc/tests/unit/test_BC_106_partial_inventory_guard.py`.
