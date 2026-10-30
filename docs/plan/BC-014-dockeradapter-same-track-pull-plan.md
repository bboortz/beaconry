# BC-014 — DockerAdapter same-track pull

*Branch: `feat/BC-014-dockeradapter-same-track-pull`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-014-dockeradapter-same-track-pull.md`*

---

## Situation

BC-009 shipped real Fix execution with `DockerAdapter.execute` doing `base = source_ref.split(":")[0]; new_image = f"{base}:latest"`. For an operator running `postgres:16-alpine`, this means tapping Fix pulls `postgres:latest` — currently postgres 18. Two-major-version jump silently incompat the data format; service dies on next restart; data loss likely.

The IDEAS entry "DockerAdapter hardcodes `:latest` — potential major-version jump foot-gun" is `priority: now`. BC-014 closes it.

---

## Problems to solve

- Fluid-track operators (`postgres:16-alpine`) silently upgrade across major versions on tap Fix
- Pinned-tag operators (`postgres:16.1.3-alpine`) lose the pin on tap Fix — DockerAdapter overwrites with `postgres:latest`
- CAP-B014 and CAP-T030 descriptions say "pulls `:latest`" — operator-facing documentation misdescribes the demo-time behavior

---

## Goals

1. `DockerAdapter.describe(finding)` returns `docker pull <source_ref>` (exact tag the operator runs)
2. `DockerAdapter.execute(finding)` runs the same-tag pull; rollback-tag mechanism unchanged
3. Fallback: if `source_ref` has no tag (`nginx` alone), use `nginx:latest` (preserves prior behavior for a legitimate edge case)
4. BC-009's existing unit tests pass unchanged OR get updated with explicit note on the behavior change (prefer updated — the tests specify the old wrong behavior)
5. CAP-T046 registered; CAP-B014 + CAP-T030 descriptions refreshed
6. README documents the behavior change
7. Live-verified: tap Fix on `postgres:16-alpine` finding → compose/stack pulls `postgres:16-alpine`, NOT `postgres:latest`

---

## Scope

- `poc/hub/executor/docker_adapter.py`:
  - `describe`: return `f"docker pull {finding.source_ref}"` when source_ref has a tag, `f"docker pull {finding.source_ref}:latest"` only as fallback when no tag present
  - `execute`: use `source_ref` directly for pull, derive `base_name` only for the `<base>:rollback` tag
- `poc/tests/unit/test_executor_dispatch.py`:
  - Update `test_docker_adapter_describe_shows_pull_command` for new behavior
  - Update `test_docker_adapter_execute_mock_no_subprocess` to assert the new command string
  - Add `test_docker_adapter_describe_preserves_tag`
  - Add `test_docker_adapter_describe_fallback_when_no_tag`
  - Update `test_docker_adapter_sets_rollback_tag_before_pull` (rollback tag command unchanged but assertion must reflect new pull command)
- `poc/tests/unit/test_hub_helpers.py`:
  - BC-009 fix handler tests: `test_fix_now_pull_clean_sets_status_fixed` etc. — these use mocked DockerAdapter.execute so they're unaffected by this change
- `docs/CAPABILITIES.md` — CAP-T046 registered; CAP-B014 + CAP-T030 descriptions refreshed
- `README.md § Fix now behavior (BC-009)` — add note that pull uses source_ref tag, not `:latest`
- `docs/plan/IDEAS.md` — mark the "DockerAdapter hardcodes `:latest`" entry as landed in BC-014

---

## Non-goals

- Registry API tag resolution (e.g. find highest 16.X-alpine) — would need Docker Hub v2 API client; parked as future refinement
- Digest pinning — "KEV auto-fix is scary" IDEAS entry
- Per-image tag-policy env var — parked
- Target health waterfall (BC-011) orthogonal

---

## Design decisions

**Preserve `source_ref` verbatim, don't try to parse "track" semantics.** Rejected: parse tag like `16-alpine`, enumerate Docker Hub, pick highest `16.X-alpine`. Reason: adds HTTP registry client dependency, needs Docker Hub auth for private registries, failure modes multiply. Fluid tags already handle "find the latest within the track" via Docker Hub's tag-mutability — upstream pushes new image, `docker pull postgres:16-alpine` picks up the update next time. Pinned tags don't change; operator manually bumps when they decide.

**Fallback to `:latest` only when no tag at all.** Rejected: refuse to proceed when no tag. Reason: `source_ref="nginx"` is a legitimate case; `docker pull nginx` defaults to `nginx:latest` anyway. Preserve that behavior exactly rather than fail.

**Rollback tag logic unchanged.** Rejected: also preserve the source tag in the rollback name (`postgres:16-alpine-rollback`). Reason: BC-009's convention is simple `<base>:rollback` and operators have already learned it. Changing the rollback-tag format would silently break the restore command documented in README. The restored image works because the rollback tag points at the pre-pull image regardless of track.

**Update existing tests, don't just add new ones.** Rejected: keep BC-009's test with "we used to pull :latest" and add a new one for "now we pull source_ref". Reason: tests should describe current behavior, not history. If the old assertion stays, future readers see conflicting specs. Update in place; mention BC-014 in the test docstring if context helps.

---

## Breaking changes

- `docker pull <base>:latest` is replaced with `docker pull <source_ref>`. **Operators running fluid tags (`:alpine`, `:stable`) see no observable change** — fluid tags re-pulled still get upstream updates. **Operators running pinned tags (`:16.1.3-alpine`) see different behavior** — previously pull would silently upgrade them to `:latest`; now pull preserves the pin. If upstream hasn't patched the specific tag, `verify_fix` returns False and operator sees "⚠️ Pulled, but CVE still present upstream" instead of "✅ Fixed and verified".
- The "⚠️ Pulled, but CVE still present upstream" terminal state becomes more common for pinned-tag users — honest behavior, operator can choose to bump manually.
- CAP-B014 and CAP-T030 descriptions change — operator-facing docs that mentioned "pulls `:latest`" are now wrong; refresh in this PR.

---

## Merge plan

1. Phase 1: branch + plan + story in-progress
2. Phase 2: red tests update
3. Phase 3: 3-line implementation
4. Phase 4: CAPs + gates + README + IDEAS mark
5. Phase 5: live-verify tap Fix on `postgres:16-alpine` finding, confirm pull preserves tag
6. Fast-forward to dev

---

## Rollout plan

Behavior change but backward-compatible for the common case (fluid tags). The uncommon case (pinned tags) stops silently upgrading, which is strictly safer. No operator action needed; README documents the behavior.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| describe returns `docker pull <source_ref>` for tagged image | unit test | `test_docker_adapter_describe_preserves_tag` | ✓ |
| execute pulls source_ref exactly in MOCK | unit test | `test_docker_adapter_execute_mock_no_subprocess` (updated) | ✓ |
| no-tag fallback: `nginx` → `nginx:latest` | unit test | `test_docker_adapter_describe_fallback_when_no_tag` | ✓ |
| rollback tag command still correct for source_ref with tag | unit test | `test_docker_adapter_sets_rollback_tag_before_pull` (updated) | ✓ |
| CAP-T046 registered; CAP-B014/T030 descriptions refreshed | verify-traceability | `make check` traceability green | ✓ |
| README documents same-track pull | README diff | awaits update | ✓ |
| IDEAS "DockerAdapter :latest foot-gun" marked landed | IDEAS diff | awaits update | ✓ |
| Live: tap Fix on postgres:16-alpine → pulls postgres:16-alpine | live manual | 2026-10-30 — ran `docker exec beaconry-hub python3` to instantiate DockerAdapter against four source_refs: `postgres:16-alpine` → `docker pull postgres:16-alpine`, `nginx:alpine` → `docker pull nginx:alpine`, `postgres:16.1.3-alpine` → `docker pull postgres:16.1.3-alpine`, `nginx` → `docker pull nginx:latest` (fallback). The headline postgres:16-alpine → postgres:latest footgun is closed. Not run end-to-end via Telegram tap since the behavior is deterministic from describe() and would require crafting a fake finding payload; the in-container Python exec confirms the shipping-code path. | ✓ |

---

## Testing strategy

Unit (fast tier) covers all the behavior. Live-verify is a sanity check that nothing in the hub handler path accidentally transforms the pull command.

---

## Testing commands

```bash
source .venv/bin/activate
make check
pytest poc/tests/unit/test_executor_dispatch.py -v --no-cov
# Phase 5 live:
docker compose up -d
# (tap Fix on a postgres:16-alpine finding in Telegram)
docker image inspect postgres:16-alpine  # should exist, maybe updated image id
docker image inspect postgres:latest     # should NOT be the one we just pulled
```
