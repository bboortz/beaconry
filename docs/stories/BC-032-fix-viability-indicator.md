---
id: BC-032
title: Fix viability indicator — show "fixed in X" + will-this-fix-work signal
status: shipped
created: 2026-10-31
---

# BC-032: Fix viability indicator

## Narrative

Operator opens Telegram, sees 🔥 FIX REGRESSED for `CVE-2025-27363` on `nginx:1.24`. The body says "Package: libfreetype6 2.10.4+dfsg-1+deb11u1" — but **doesn't say which version has the fix** or whether tapping 🔧 Re-fix will even help. For a frozen tag like `:1.24`, Re-fix is a no-op loop (BC-014 same-track pull returns the same bits). Operator correctly identified the gap 2026-10-31:

> "If I press here fix it will always come again because 1.24 will be pulled again but still contains the error, right?"

Two sub-fixes in one story:

1. **Add "→ fixed in X" to the regression notification body.** The regular notification already shows it; `format_regression` was missing it.
2. **Compute and show a fix-viability indicator.** Based on `source_type` + `source_ref` tag characteristics, label the Fix action `✅ likely`, `❌ unlikely`, or `❓ unknown` with a one-line reason.

## Expected outcome (both notifications)

```
🔴 Critical — CVE-2025-27363
CVSS: 8.1 · Host: homelab-dev
Target: nginx:1.24 (docker)
Package: libfreetype6 2.10.4+dfsg-1+deb11u1 → fixed in 2.10.5+dfsg-1+deb11u2
Fix viability: ❌ unlikely — frozen tag 'nginx:1.24' — pulling returns the same bits; edit compose to use a newer tag that ships 2.10.5+dfsg-1+deb11u2
```

Versus for AVG-2909:

```
Fix viability: ✅ likely — upstream repo has 8.5.12-1; `sudo pacman -Syu` pulls it
```

## Viability heuristic

| source_type | source_ref tag | Fix viability |
|---|---|---|
| docker | `latest` / `main` / `stable` / `edge` / `nightly` | ✅ likely (mutable; pull fetches current upstream) |
| docker | frozen semver (`1.24`, `16-alpine`, `1.24.0`) | ❌ unlikely (BC-014 same-track pull returns same bits) |
| docker | no `:tag` → defaults to `latest` | ✅ likely |
| docker | other | ❓ unknown |
| alpm | `fixed_version` present + not "not available" | ✅ likely (`sudo pacman -Syu` pulls it) |
| alpm | no fixed_version | ❓ unknown |
| other | any | ❓ unknown |

Heuristic, not a guarantee. Grype's `fix.versions` isn't always accurate; some mutable tags in operator setups are actually pinned via SHA. Operator makes the call.

## Capabilities delivered

- **CAP-T074: Notification body carries a fix-viability indicator** (technical, active)
- **CAP-T075: Regression notification body shows `→ fixed in X` (parity with regular notification)** (technical, active)

## Breaking changes

Per the discipline shipped in `~/.claude/.../breaking_changes_in_planning.md`:

### 1. API / return shape
None. `format_notification` and `format_regression` return strings as before; just richer content.

### 2. Data model
None. Viability is computed from existing `Finding` fields (`source_type`, `source_ref`, `fixed_version`).

### 3. Behavioral semantics
Notification bodies gain one new line. No breaking change for anyone who grepped a notification string, since the new text contains "Fix viability" prefix that's never been in a body before. Operators who tapped 🔧 Fix on frozen tags will now be forewarned ("unlikely") rather than discovering the loop by experience — strictly a UX improvement, no regression surface.

## Links

- Trigger: operator screenshot 2026-10-31 of two 🔥 FIX REGRESSED notifications for nginx:1.24.
- Related: BC-014 (same-track pull), BC-031 (reconciliation), BC-033 candidate (replace Fix with Upgrade when upstream unfixed).
