---
id: BC-105
title: `make findings` surfaces FIRST SEEN + LAST CHANGE + close_reason
status: shipped
created: 2026-11-09
branch: feat/BC-105-findings-cli-lifecycle
plan: docs/plan/BC-105-findings-cli-lifecycle-plan.md
---

# BC-105: `make findings` shows the lifecycle, not just current status

## Narrative

`make findings` today shows status (`open`, `auto_fixed`, …) but no
*when* information. Operators who want to answer "when did this
finding first appear?" or "when did the system auto-close that
one?" have to grep the audit log. Also: three distinct auto-close
paths (BC-086 POST / BC-102 stale-scout / operator-manual) all show
as `status=auto_fixed` with no discriminator in the CLI.

BC-105 adds two columns and a status suffix:

- **FIRST SEEN** — already-persisted `found_at` in a human-readable
  age ("3d", "17m", etc.).
- **LAST CHANGE** — new `last_status_change_ts` column updated on
  every status-write path. Age-formatted.
- **auto_fixed reason** — new `close_reason` column populated at
  the three auto-close write sites: `post_absent` (BC-086),
  `stale_scout` (BC-102), `operator` (`make set-status`). Rendered
  as `auto_fixed (stale-scout)` / `auto_fixed (post)` /
  `auto_fixed (operator)` in the status column.

## Expected outcome

- Schema v9 → v10 adds `close_reason TEXT` + `last_status_change_ts
  INTEGER NOT NULL DEFAULT 0` columns on `findings` via idempotent
  ALTER.
- `make findings` output grows two columns.
- Legacy rows (pre-BC-105 `last_status_change_ts=0`) render LAST
  CHANGE as `—`. No backfill — operator-driven history takes over.
- Status suffix shown only when `close_reason` is set (auto_fixed
  path).

## Out of scope

- Backfilling `last_status_change_ts` from audit log for pre-BC-105
  findings. Audit log is authoritative; CLI just shows `—`.
- Changing the status *values* — `auto_fixed` stays as the status;
  the discriminator is a separate column, not a hyphenated status
  identifier (lest downstream `--status auto_fixed` filters break).

## Capabilities delivered

- **CAP-T207: Findings CLI shows FIRST SEEN + LAST CHANGE +
  auto-close reason.** Operators get the lifecycle at a glance,
  without grepping audit logs.

## Links

- BC-007 (original `make findings` CLI).
- BC-086 (POST-driven auto-close — one `close_reason` value).
- BC-102 (stale-scout auto-close — the other).
- BC-055 (`make set-status` — operator-driven close).
