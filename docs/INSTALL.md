# Installing beaconry

Audience: homelab operator running Linux on one or more hosts, wanting
beaconry to notify them about CVEs in their stack and manage the hub
remotely over HTTP.

> **Credentials rule up-front:** the Telegram bot token, `HUB_SCOUT_TOKEN`,
> `HUB_CLI_TOKEN`, and `HUB_TOKEN` **must never be committed to any git
> repo**. See `Credentials` below for the three safe places they may live.

---

## Prerequisites

| What | Version | Notes |
|---|---|---|
| Linux | any | Ubuntu, Debian, or Arch for both hub and scout hosts. |
| Docker Engine | 25+ | For the `start_interval` healthcheck knob. |
| Docker Compose plugin | v2 | `docker compose version` must work. |
| `make` | GNU make | For the operator Makefile targets. |
| Telegram bot token | — | Create via `@BotFather`; also get your chat ID from `@userinfobot`. |
| Tailscale (multi-host only) | any | Recommended transport between hub + scouts + operator CLI. |

Verify prerequisites:

```bash
docker version
docker compose version
make --version
```

---

## Quick start — single-host install

Hub and scout on the same machine. Covers 90% of homelab setups.

1. Clone + enter the repo:

    ```bash
    git clone https://github.com/<your-mirror-or-fork>/beaconry.git
    cd beaconry/poc
    ```

2. Create your `.env` from the template:

    ```bash
    cp .env.example .env
    chmod 600 .env
    ```

3. Edit `.env` and fill in `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
   Leave everything else at the defaults for now.

4. Boot the stack:

    ```bash
    cd ..                           # back to repo root
    make live-rebuild
    make status
    ```

    Both services should report `healthy`. If telegram shows
    `not_running`, re-check step 3 (the stack reads `.env` at boot).

5. Trigger the first scan + wait for Telegram:

    ```bash
    make scan
    make findings
    ```

Done. The hub is now scanning your Docker images every `SCAN_INTERVAL`
seconds (default 300) and will send a Telegram message on new CVEs.

---

## Multi-host install — hub on one machine, scouts on others

Topology: hub on `hetzner-staging`, scouts on each node you want
scanned (laptop, server2, …). All operator commands run from a
workstation. Transport is Tailscale (`100.x.x.x`); beaconry sends
HTTP over the already-encrypted link.

### 1. Set up the hub on the central host

Follow the single-host quick-start above, then edit `.env`:

* Set `HUB_SCOUT_TOKEN` to a 32+ char random string. Generate with
  `openssl rand -hex 32`. Every scout posting into this hub uses it.
* Set `HUB_CLI_TOKEN` to a **different** 32+ char random string. Every
  operator CLI uses this one. Independent blast radius: scout-side
  leak doesn't compromise the operator surface, and vice versa.
* Set `HUB_PUBLISH_ADDR` to the Tailscale address or `0.0.0.0`.
* Restart: `make live-rebuild`.

The hub now exposes port 8000 on the Tailscale IP, with two
independent bearer gates (one for `/scan` ingest, one for the
operator surface).

### 2. Install a scout on each remote host

On each scout host, drop the scout-only compose + a `.env`:

```bash
# On the scout host:
mkdir -p /etc/beaconry && cd /etc/beaconry
cat > .env <<EOF
HUB_URL=http://100.1.2.3:8000   # Tailscale IP of the hub
HUB_TOKEN=<paste HUB_SCOUT_TOKEN value from hub .env>
HOST_NAME=laptop                 # label this host in notifications
SCAN_INTERVAL=300
SCOUT_INCLUDE=*                  # scan everything on this host
EOF
chmod 600 .env

docker compose -f /path/to/beaconry/poc/docker-compose.scout-only.yml \
    --env-file .env up -d --build
```

Equivalently via Ansible — see the `Ansible installation` section.

### 3. Verify multi-host from the workstation

Point your operator CLI at the hub (next section), then:

```bash
make status
```

The FLEET section lists every scout host currently posting findings,
with `LAST SEEN` and open/total finding counts.

---

## Operator CLI setup

For operators running CLIs against a remote hub (`make findings`,
`make audit`, `make status`, `make scan`, `make set-status`), create
`~/.config/beaconry/cli.yml`:

```yaml
hub_url: http://100.1.2.3:8000     # Tailscale IP of the hub
hub_token: <paste HUB_CLI_TOKEN value from hub .env>
```

Lock permissions:

```bash
chmod 700 ~/.config/beaconry
chmod 600 ~/.config/beaconry/cli.yml
```

The CLIs auto-route: non-localhost `hub_url` → HTTP; localhost +
local compose stack running → `docker exec` (unchanged); else local
file. Override per invocation with env vars (`HUB_URL`, `HUB_TOKEN`).

Mutate a finding from the workstation:

```bash
make set-status FINDING=<finding_id> STATUS=snoozed
# STATUS=snoozed | accepted | fixed | open
```

Trigger an immediate scan on all scouts the hub knows about:

```bash
make scan
```

Both go through the hub's bearer-gated HTTP surface — no SSH anywhere.

---

## Ansible installation

If you already manage your homelab via Ansible, the recommended shape
is one role per component. The role bodies below are minimal — adapt
to your inventory conventions.

### Role tree

```
roles/
  beaconry_hub/
    defaults/main.yml
    tasks/main.yml
    templates/env.j2
  beaconry_scout/
    defaults/main.yml
    tasks/main.yml
    templates/env.j2
```

### `roles/beaconry_hub/defaults/main.yml`

```yaml
beaconry_hub_image: beaconry-hub:latest
beaconry_hub_data_dir: /var/lib/beaconry
beaconry_hub_publish_addr: "0.0.0.0"
beaconry_min_cvss: "9.0"
# telegram_bot_token + hub_scout_token + hub_cli_token come from
# Ansible Vault — never set them in defaults.
```

### `roles/beaconry_hub/tasks/main.yml`

```yaml
---
- name: Ensure beaconry data directory exists
  ansible.builtin.file:
    path: "{{ beaconry_hub_data_dir }}"
    state: directory
    mode: "0750"

- name: Write hub .env (0600, Vault-sourced secrets)
  ansible.builtin.template:
    src: env.j2
    dest: "{{ beaconry_hub_data_dir }}/env"
    mode: "0600"
    owner: root
    group: root

- name: Start the beaconry-hub container
  community.docker.docker_container:
    name: beaconry-hub
    image: "{{ beaconry_hub_image }}"
    state: started
    restart_policy: unless-stopped
    env_file: "{{ beaconry_hub_data_dir }}/env"
    published_ports:
      - "{{ beaconry_hub_publish_addr }}:8000:8000"
    volumes:
      - "{{ beaconry_hub_data_dir }}:/data"
```

### `roles/beaconry_hub/templates/env.j2`

```jinja
# Rendered by Ansible — do not edit by hand.
TELEGRAM_BOT_TOKEN={{ telegram_bot_token }}
TELEGRAM_CHAT_ID={{ telegram_chat_id }}
HUB_SCOUT_TOKEN={{ hub_scout_token }}
HUB_CLI_TOKEN={{ hub_cli_token }}
HUB_PUBLISH_ADDR={{ beaconry_hub_publish_addr }}
MIN_CVSS={{ beaconry_min_cvss }}
DATA_DIR=/data
```

### Example playbook

```yaml
---
- name: Install beaconry hub
  hosts: hub
  become: true
  roles:
    - role: beaconry_hub

- name: Install beaconry scouts
  hosts: scouts
  become: true
  roles:
    - role: beaconry_scout
```

### Secrets via Ansible Vault

Encrypt the file holding the tokens:

```bash
ansible-vault create group_vars/all/vault.yml
```

```yaml
# group_vars/all/vault.yml — encrypted at rest
telegram_bot_token: 123456:AAEEEEEEEEEEEEEEE
telegram_chat_id: "958906415"
hub_scout_token: 0123456789abcdef0123456789abcdef
hub_cli_token: fedcba9876543210fedcba9876543210
```

Reference in your inventory `group_vars/all/beaconry.yml` (plaintext,
checked in — but it only contains *var names*, not values):

```yaml
# This file is committed. It names the vars; vault.yml holds the values.
beaconry_hub_image: beaconry-hub:latest
# Scouts receive the scout token under the var the scout-only compose
# reads (HUB_TOKEN on the scout side).
scout_hub_token: "{{ hub_scout_token }}"
# Operator-CLI hosts receive the cli token.
cli_hub_token: "{{ hub_cli_token }}"
```

Run the playbook:

```bash
ansible-playbook -i inventory.yml site.yml --ask-vault-pass
# OR, for unattended runs:
ansible-playbook -i inventory.yml site.yml --vault-password-file ~/.ansible-vault-pass
```

The vault-password file must be `chmod 600` and **not** in VCS. Add it
to `.gitignore` first thing.

---

## Credentials

**Never commit tokens to git.** Tokens may live in exactly one of
these three places:

### Option 1 — `.env` on each host

Mode 0600, owned by the user that runs `docker compose up`, in
`.gitignore`. Fast path for a single operator managing their own stack.

```bash
chmod 600 poc/.env
grep -q '^\.env$' .gitignore || echo '.env' >> .gitignore
```

### Option 2 — Ansible Vault

Covered above. Values live in an encrypted file; Ansible decrypts at
play time and renders into the host-side env file. Right choice when
≥2 operators share the inventory or when you want a durable audit
trail on who rotated what and when.

### Option 3 — systemd `EnvironmentFile=`

If running the hub as a systemd unit (not compose), put the tokens in
a root-owned 0600 file outside the git-tracked tree:

```ini
# /etc/systemd/system/beaconry-hub.service
[Service]
EnvironmentFile=/etc/beaconry/hub.env
ExecStart=/usr/bin/docker run --rm --name beaconry-hub \
    --env-file /etc/beaconry/hub.env \
    -p 127.0.0.1:8000:8000 \
    beaconry-hub:latest
```

```bash
chmod 600 /etc/beaconry/hub.env
chown root:root /etc/beaconry/hub.env
```

### What to do if a token leaks

1. Rotate the Telegram bot token via `@BotFather` → `/revoke` →
   generate a new one.
2. Rotate `HUB_SCOUT_TOKEN` **and** `HUB_CLI_TOKEN` separately —
   they're independent. Change the value in `.env` / Vault /
   `EnvironmentFile=` and restart the hub. Every scout + CLI that
   uses the old token starts failing 401 immediately.
3. Audit `make audit SINCE=24h` for events using the stale token's
   paths.

---

## Verification

After install:

1. `make status` — both compose services `healthy`, FLEET lists every
   scout host.
2. `make scan` (or `make scan HOST=<remote-host>`) — scheduled 202 OK.
3. `make findings` — rows for whatever CVEs the scout(s) found.
4. `make audit LAST=10` — audit events for the recent scan + any
   notifications.
5. Telegram app — a message with the first critical CVE (if any match
   `MIN_CVSS`).

If any of the above shows wrong, see `Troubleshooting`.

---

## Troubleshooting

### `make status` says telegram `not_running`

* Token missing or wrong format. Re-check `.env`'s
  `TELEGRAM_BOT_TOKEN`.
* Chat ID missing. Message `@userinfobot` on Telegram; paste the
  returned id into `TELEGRAM_CHAT_ID`.
* After editing `.env`, you must `make live-rebuild` — the stack reads
  env at boot.

### Scout container not posting to a remote hub

* `HUB_URL` wrong — check the scout host's `.env`.
* `HUB_TOKEN` doesn't match the hub's `HUB_SCOUT_TOKEN`. Hub returns
  401 → scout logs `Failed to post to hub: 401`.
* Firewall / Tailscale ACL between scout and hub. Verify `curl -sf
  http://<hub>:8000/livez` works from the scout host.

### `make findings` on the workstation errors with "failed to reach hub"

* `~/.config/beaconry/cli.yml` wrong URL or stale token. Compare
  against the hub's `.env`.
* Tailscale not up on the workstation.

### `make findings` empty on a fresh install

* First scan not run yet. `make scan` to trigger one; wait ~30s;
  retry `make findings`.
* `MIN_CVSS` too high (default 9.0). Lower to 7.0 to see medium
  severities too.

### Grype DB download times out on first boot

* Grype pulls ~200MB on first start. The hub's `start_period=120s`
  healthcheck allows for it, but a slow network can exceed that.
  Retry `make live-rebuild`, or set `BEACONRY_SKIP_GRYPE_DB=1` +
  bind-mount a prepopulated DB dir.

### "unauthenticated exposure" warning in hub logs

Expected if `HUB_PUBLISH_ADDR=0.0.0.0` (or LAN IP) is set without
`HUB_SCOUT_TOKEN` + `HUB_CLI_TOKEN`. Set both tokens or terminate
TLS + auth at a reverse proxy. The warning is non-fatal.

---

## Related docs

* `docs/VARIABLES.md` — every env var, with defaults + where each is read.
* `README.md` — product overview + demo recipe.
* `DESIGN.md` — architecture + urgency tiers + health checks.
