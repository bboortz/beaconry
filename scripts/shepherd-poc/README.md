# shepherd-poc scripts — Release 0 (BC-145)

Identical to healthy's R0 pilot (HC-008). See:

- Product design: [`bboortz/shepherd SH-004`](https://github.com/bboortz/shepherd/blob/main/docs/stories/SH-004-r0-poc-on-healthy.md)
- Shepherd trust boundary: [`bboortz/shepherd docs/DESIGN.md §12`](https://github.com/bboortz/shepherd/blob/main/docs/DESIGN.md)
- First pilot: [`bboortz/healthy HC-008`](https://github.com/bboortz/healthy/blob/main/docs/stories/HC-008-shepherd-poc.md)
- This story: [`docs/stories/BC-145-shepherd-poc.md`](../../docs/stories/BC-145-shepherd-poc.md)

## Files

| Script | Role |
|---|---|
| `install-trivy.sh` | Download Trivy v0.69.3 + `cosign verify-blob --new-bundle-format`. No `aquasecurity/trivy-action` wrapper (CVE-2026-33634). |
| `agent-call.sh` | `curl` to Anthropic Messages API with the findings JSON. Hard-coded: `max_tokens=4096` (~€1/run), 3 retries, scope level 1 (deps + lockfiles). |
| `validate.sh` | Protected-files blocklist + 50-line diff cap. The only enforcement line in R0. |
| `open-pr.sh` | `git apply --check`, branch `shepherd-poc/<timestamp>`, commit, push, `gh pr create` against `dev` with labels `shepherd-poc` + `needs-review`. |

## Operator setup

```sh
gh secret set ANTHROPIC_API_KEY -R bboortz/beaconry
gh workflow run shepherd-poc.yml -R bboortz/beaconry
```

## Known R0 gap

Agent step runs on a stock GitHub-hosted runner — no network perimeter. `validate.sh` is R0's only enforcement. R1 adds the network lockdown (DESIGN §12 `sandbox.no-network-except-model`).
