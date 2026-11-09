---
id: BC-104
title: Notification retry queue — Telegram outage no longer silently drops alerts
status: shipped
created: 2026-11-09
branch: feat/BC-104-notify-retry-queue
plan: docs/plan/BC-104-notification-retry-queue-plan.md
---

# BC-104: Pending-delivery queue + backoff retry for Telegram alerts

## Narrative

Pre-BC-104: when `notify_finding` / `notify_regression` /
`notify_heartbeat` / `notify_digest` / `notify_scout_offline` etc.
dispatch into `TelegramAdapter.send_*`, a transient Telegram outage
(API 503, network partition, token quota) throws → audit
`notify_sent_failed` fires → the alert is **lost forever**. There is
no retry; no backoff; no eventual-delivery guarantee.

BC-104 adds a lightweight persistent queue for failed deliveries:

- New table `pending_notifications` keyed by `(finding_id, kind)`.
- Wrapping the adapter call: catch the dispatch exception, write a
  pending row with `attempts=0, next_retry_ts=now+base_s`.
- Background `_notify_retry_loop_forever` task scans the queue every
  `BEACONRY_NOTIFY_RETRY_SWEEP_INTERVAL_S` (default 60 s) and re-invokes
  the adapter for rows whose `next_retry_ts <= now`.
- Exponential backoff: `next_retry_ts += base_s × 2^attempts`.
- Give-up: after `BEACONRY_NOTIFY_RETRY_MAX_ATTEMPTS` (default 5)
  → audit `notify_abandoned` + DELETE row.
- Success: audit `notify_delivered` + DELETE row.

## Expected outcome

- Telegram outage ≤ 30 min: zero alerts lost.
- Operator who was away from phone: alert arrives when coverage
  resumes.
- Audit log distinguishes "sent on first try" (existing path) from
  "sent after retry" (new `notify_delivered` event).
- No change to notification BODIES — just redelivery of whatever was
  queued.
- Not opt-out — the queue is a safety net, not a toggle.

## Out of scope

- Per-operator delivery channels (email, SMS). Framework stays
  Telegram-focused; retry wraps the existing adapter dispatch.
- Deduping retries across hub restarts via some external lock —
  the table itself is the lock.
- Payload schema evolution — payloads are stored as JSON in the
  queue row; a future schema bump is a separate migration.

## Capabilities delivered

- **CAP-T206: Notification retry queue with exponential backoff.**
  Pending-delivery rows survive hub restart; retry loop runs
  alongside the heartbeat + snooze + offline sweeps.

## Links

- BC-060 (`notify_sent_failed` audit — current silent-fail audit).
- BC-035 (notifier adapter architecture).
- BC-089 / BC-102 (sibling background-loop sweeps).
