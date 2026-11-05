# poc/hooks

Operator-installed hooks that trigger scout rescans on OS-level events.
Ship as templates; **not** auto-installed by beaconry (they need root and
system-specific placement). BC-093.

## `beaconry-pacman.hook`

Arch Linux's alpm hook. Fires after every pacman package transaction
(install / upgrade / remove), POSTs to `scout:/scan/trigger`.

Install on an Arch host:

```bash
sudo cp poc/hooks/beaconry-pacman.hook /etc/pacman.d/hooks/
sudo chmod 644 /etc/pacman.d/hooks/beaconry-pacman.hook
```

Verify with:

```bash
sudo pacman -S --needed <some-package>
# scout logs should show: "Scan triggered (reason=manual)"
```

**Security**

- The hook runs as root (pacman context).
- Scout's `/scan/trigger` is **unauthenticated**. Keep scout bound to
  `127.0.0.1` so no remote host can trigger your rescans.
- If scout is down / busy, `curl` fails silently with `|| true` —
  pacman transactions are NOT blocked. Rescans resume on scout restart.

## Future (not implemented)

- `beaconry-apt.hook` (apt/dpkg)
- `beaconry-dnf.plugin` (dnf hook)
- `beaconry-apk.hook` (Alpine)

Operator contribution welcome; same POST-to-`/scan/trigger` shape.
