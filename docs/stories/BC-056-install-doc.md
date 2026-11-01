---
id: BC-056
title: INSTALL.md + verify-install-doc lint (short-name reshape)
status: shipped
created: 2026-11-01
branch: feat/BC-056-install-doc
plan: docs/plan/BC-056-install-doc-plan.md
---

# BC-056: INSTALL.md + verify-install-doc

## Narrative

Homelab operator just shipped BC-055 (HTTP-only CLI → hub, split
scout/CLI tokens, short-named env vars, Tailscale for transport).
There's no single install guide, so the install flow is scattered
across README + VARIABLES.md + the two compose files.

Add `docs/INSTALL.md` as the authoritative install guide. Cover
single-host, multi-host (hub + remote scouts posting via Tailscale),
operator-CLI setup, an Ansible section with Vault for credentials,
and a Credentials section calling out the three safe places tokens may
live. Documents go stale; add `verify-install-doc` to catch drift.

## Trigger

Operator says "verify the POC on my homelab" and wants an authoritative
install guide to hand off to Ansible.

## Expected outcome

- `docs/INSTALL.md` reads cleanly for a Linux homelab operator.
- Section order: Prerequisites → Quick start (single-host) →
  Multi-host (Tailscale) → Operator CLI → Ansible → Credentials →
  Verification → Troubleshooting.
- Ansible section ships a copyable role tree + playbook + Vault file.
- Credentials section enumerates the three safe places and makes the
  "never commit tokens" rule explicit at the top of the doc.
- `make verify-install-doc` fails if INSTALL.md references an unknown
  env var, `make` target, or `poc/`/`docs/` file path.
- `make check` includes `verify-install-doc`.

## Out of scope

- Shipping an actual Ansible collection (operators have their own).
- Non-Linux targets.
- Kubernetes install story.
- Image-signing / supply chain hardening.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Install documentation + drift lint

  Scenario: INSTALL.md has the mandated sections
    When any operator opens docs/INSTALL.md
    Then it has sections Prerequisites, Quick start, Multi-host,
      Operator CLI, Ansible, Credentials, Verification, Troubleshooting

  Scenario: Ansible section ships copyable artifacts
    When operator reads the Ansible section
    Then it includes a role tree, a complete playbook using
      community.docker, and an Ansible Vault example for the Telegram
      token + the hub tokens (HUB_SCOUT_TOKEN + HUB_CLI_TOKEN)

  Scenario: Credentials section forbids committing secrets
    When operator reads the Credentials section
    Then it enumerates the three safe places tokens may live
      (.env file not in git, Ansible Vault, systemd EnvironmentFile)
    And explicitly says tokens must never be committed

  Scenario: Lint catches stale env var references
    Given INSTALL.md mentions `HUB_NONEXISTENT`
    When `make verify-install-doc` runs
    Then it exits non-zero naming the unknown var

  Scenario: Lint catches stale make target references
    Given INSTALL.md mentions `make nonexistent-target`
    When `make verify-install-doc` runs
    Then it exits non-zero naming the missing target

  Scenario: Lint catches stale repo-path references
    Given INSTALL.md mentions `poc/nonexistent.yml`
    When `make verify-install-doc` runs
    Then it exits non-zero naming the missing file

  Scenario: `verify-install-doc` is part of `make check`
    When CI runs `make check`
    Then it invokes `make verify-install-doc`
```

## Capabilities delivered

- **CAP-T112: `verify-install-doc` lint** (technical).
- **CAP-B039: Homelab operator installs beaconry via Ansible with safe
  credentials handling using repo-authoritative docs** (business).

## Links

- Depends on: BC-055 (short-name env + split tokens + HTTP CLI).
