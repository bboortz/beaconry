---
id: BC-033
title: verify_fix scans the operator's actual source_ref (not hardcoded :latest)
status: shipped
created: 2026-10-31
---

# BC-033: verify_fix scans the operator's actual source_ref

## Narrative

Operator tapped Re-fix on a 🔥 FIX REGRESSED for `nginx:1.24`. The handler never actually fired due to an unrelated Telegram polling state issue — but inspecting `verify_fix` surfaced a worse latent bug: it was scanning `nginx:latest`, not `nginx:1.24`, after the pull. Pre-BC-014 this made sense (the fix flow pulled `:latest`). BC-014 switched to same-track pulls (`:1.24` stays `:1.24`); `verify_fix` was not updated. Result: for every Fix flow that reached the verify step, the terminal "✅ Fixed and verified" or "⚠️ CVE still present" message was based on a DIFFERENT image than the operator was running.

Discovered while diagnosing why operator's Re-fix tap on CVE-2025-27363 hadn't shown a terminal state 7+ minutes later. Shipped as part of the diagnostic because once seen, the bug can't be left in — it corrupts the verify signal that BC-031 reconciliation and BC-032 viability both depend on.

## Expected outcome

`verify_fix(finding)` runs `syft {finding.source_ref}` (not `syft {base}:latest`). For a Fix tap on nginx:1.24 that resulted in a BC-014 same-track pull, the verification scans nginx:1.24 — the exact image the operator is running. Pairs cleanly with BC-032's `❌ unlikely` viability signal for frozen tags: those will now reliably return False in verify_fix (CVE still present on the frozen tag), driving the honest "⚠️ Pulled, but CVE still present upstream" terminal message.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Honest post-fix verification

  Scenario: verify_fix scans the actual source_ref
    Given a finding with source_ref="nginx:1.24"
    When verify_fix runs
    Then syft is invoked with "nginx:1.24" (not "nginx:latest")

  Scenario: Non-latest tags preserved exactly
    Given findings with source_refs ("postgres:16-alpine", "nginxproxy/nginx-proxy:latest", "myapp:prod-sha-abc123")
    When verify_fix runs for each
    Then syft is invoked with the matching ref each time (not re-mapped to :latest)

  Scenario: non-docker still short-circuits
    Given a finding with source_type="alpm"
    When verify_fix runs
    Then it returns True without invoking syft (ManualAdapter owns that path)
```

## Breaking changes

Per `docs/plan/_template.md § Breaking changes`:

### 1. API / return shape
None. verify_fix still returns bool.

### 2. Data model
None.

### 3. Behavioral semantics
**Real behavior change.** Operators who tapped Fix on any non-`:latest` docker tag used to get verification against the `:latest` of the same base — which could tell them "✅ Fixed" when their pinned tag was still vulnerable (false positive) or "⚠️ CVE still present" based on an irrelevant image (misleading). Post-BC-033 the verification is honest. Operators who relied on the pre-BC-033 behavior to get false "✅ Fixed" would now see honest "⚠️ Pulled, but CVE still present" for frozen tags with no upstream fix — strictly more correct, but it exposes the true state they were previously insulated from. Pairs with BC-032's viability indicator to pre-warn operators.

## Capabilities delivered

- **CAP-T076: verify_fix scans the operator's actual source_ref (not hardcoded :latest)** (technical, active)

## Links

- Trigger: diagnosing a stuck Re-fix tap 2026-10-31 revealed the latent bug.
- Related: BC-014 (same-track pull — the change that broke the pre-existing hardcoded :latest assumption without anyone noticing), BC-032 (viability indicator — pre-warn operator when verify would return False), BC-031 (reconciliation — depends on honest verify_fix signal to not auto-reopen correctly-fixed findings).
