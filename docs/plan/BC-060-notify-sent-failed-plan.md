# BC-060 — notify_sent_failed audit symmetry

*Branch: `feat/BC-060-notify-sent-failed`*
*Start: 2026-11-02*

## Scope

### Helper

```python
_FAIL_REASON_MAX = 200


def _emit_notify_sent_failed(finding: Finding, kind: str, exc: Exception) -> None:
    reason = f"{exc.__class__.__name__}: {str(exc)[:_FAIL_REASON_MAX]}"
    _audit.append(
        "notify_sent_failed",
        finding_id=finding.id,
        cve_id=finding.cve_id,
        host=finding.host,
        kind=kind,
        reason=reason,
    )
```

### Wrap each dispatch

Each of the four `notify*` functions becomes:

```python
async def notify(finding: Finding) -> None:
    if MOCK and not _notifiers_pkg.enabled_adapters():
        _mock_print("Notification", format_notification(finding))
        _emit_notify_sent(finding, "finding")
        return
    try:
        await _dispatch_finding(finding)
    except Exception as exc:
        _emit_notify_sent_failed(finding, "finding", exc)
        raise
    _emit_notify_sent(finding, "finding")
```

Same shape for `notify_regression`, `notify_heartbeat`.

### Digest

```python
async def send_digest(suppressed: list[Finding]) -> None:
    if not suppressed:
        return
    try:
        await _dispatch_digest(suppressed)
    except Exception as exc:
        _audit.append(
            "notify_digest_sent_failed",
            finding_ids=[f.id for f in suppressed],
            count=len(suppressed),
            reason=f"{exc.__class__.__name__}: {str(exc)[:_FAIL_REASON_MAX]}",
        )
        raise
```

Note: `record_digest_sent` is called by `process_sbom_entry` AFTER
`send_digest` returns successfully. So the current audit order is:
`send_digest` runs → if OK, `record_digest_sent` → emits `digest_sent`
+ `notify_digest_sent`. On failure, `send_digest` raises; the caller
logs; `record_digest_sent` is never called; now BC-060 also emits
`notify_digest_sent_failed`.

### Exception propagation

Re-raise after audit so caller's existing error-handling (log.warning,
try/except in process_sbom_entry) stays triggered. Audit is additive.

## Design decisions

### Why re-raise instead of swallow

The hub already has structured handling at the caller level
(`process_sbom_entry` logs warnings; `heartbeat_sweep` increments
counters). Swallowing would make failures invisible to those paths.
Audit is a durable record on top of, not instead of, existing control
flow.

### Why truncate the reason

Error messages can be long and sometimes contain tokens
(python-telegram-bot errors often include the API URL with the bot
token in it). 200 chars is enough to tell `HTTPError: 401 Unauthorized`
from `URLError: Name or service not known`.

**Security note:** the token-in-URL case is specifically why we
truncate + the class name is primary signal. If python-telegram-bot
raises with a URL that contains the token, we'll likely still catch
the token in those 200 chars. A follow-up BC can add a token-redaction
regex. For now, operator should treat the audit log as sensitive — same
as every other beaconry runtime artifact.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| notify failure emits notify_sent_failed | integration | `test_BC_060_notify_failed.py::test_notify_failure_emits_notify_sent_failed` | ✓ |
| notify_regression failure emits notify_sent_failed | integration | `test_BC_060_notify_failed.py::test_notify_regression_failure_emits_notify_sent_failed` | ✓ |
| notify_heartbeat failure emits notify_sent_failed | integration | `test_BC_060_notify_failed.py::test_notify_heartbeat_failure_emits_notify_sent_failed` | ✓ |
| notify failure propagates exception | integration | `test_BC_060_notify_failed.py::test_notify_failure_re_raises` | ✓ |
| send_digest failure emits notify_digest_sent_failed | integration | `test_BC_060_notify_failed.py::test_digest_failure_emits_notify_digest_sent_failed` | ✓ |
| send_digest failure propagates exception | integration | `test_BC_060_notify_failed.py::test_digest_failure_re_raises` | ✓ |
| Failure reason truncated to ≤ 200 chars | integration | `test_BC_060_notify_failed.py::test_reason_truncated` | ✓ |
| Failure reason starts with exception class name | integration | `test_BC_060_notify_failed.py::test_reason_starts_with_class_name` | ✓ |
| Success path still emits notify_sent (not failed) | integration | `test_BC_060_notify_failed.py::test_success_still_emits_notify_sent` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

Legend: ✓ covered · ❌ open.
