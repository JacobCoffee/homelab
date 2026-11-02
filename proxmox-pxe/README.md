# Proxmox VE network install from this Mac

PXE-boot each PC, install Proxmox VE 9.2 unattended onto its USB stick, and turn its
internal drive into VM/container storage. Runs entirely from the Mac; the Spectrum
router is left untouched.

![Netboot dashboard: six nodes at different stages, two ISO downloads in flight, live activity log](docs/dashboard-dark.png)

```mermaid
sequenceDiagram
  participant PC
  participant Router as Spectrum router (DHCP)
  participant Mac as Mac (dnsmasq proxyDHCP + TFTP, app.py HTTP + web UI)
  PC->>Router: DHCP → IP address
  PC->>Mac: proxyDHCP → boot file name
  PC->>Mac: TFTP ipxe.efi / undionly.kpxe
  PC->>Mac: GET /boot.ipxe?mac=…
  Mac-->>PC: installer (new MAC) or "exit" (already installed → boots USB)
  PC->>Mac: kernel + initrd + ISO (~1.7 GB)
  PC->>Mac: POST /answer → hostname hnhvNN, install to USB disk
  PC->>Mac: POST /installed (IP, MAC) → reboot into USB stick
  PC->>Mac: GET /first-boot → POST /first-boot-report
```

## What each node gets

| Item | Value |
|---|---|
| Hostname | `hnhv01`, `hnhv02`, … in install order (`<site><role><NN>`: homenet, hypervisor), FQDN `hnhv01.int.z7x.org`; set in `config.env` |
| OS disk | the USB disk (`ID_BUS=usb`), ext4, no swap, no data volume |
| IP | whatever the router's DHCP gave it during install, written as static config |
| root password | `secrets/root-password`: generated on first build as `xxxxx-xxxxx-xxxxx-xxxxx` (no lookalike characters); change it from the dashboard |
| root SSH keys | your SSH agent's keys (Secretive) + `secrets/automation_ed25519` for Ansible; refreshed on every `./build.sh` |
| VM/CT storage | `vmdata`: LVM-thin on the internal drive, if it is the only internal drive **and blank** |
| `local` storage (USB) | `iso,vztmpl,backup` only, so VM disks never land on the stick |
| USB wear | journal kept in RAM (lost on reboot), root mounted `noatime` |
| apt | enterprise repos disabled, `pve-no-subscription` enabled |

## Before you start

Per PC, in the firmware setup:
- Enable network boot (UEFI: "IPv4 PXE"; legacy BIOS works too).
- **Disable Secure Boot**: the iPXE build is unsigned.
- Plug in **only** its own USB stick (the installer wipes the first USB disk it finds).
- Wired Ethernet to the router, plug clicked fully in: the port's link lights must be on.
  A half-seated cable looks plugged in but gives no link, and the PC drops straight
  back to the boot menu without sending anything.
- RAM: the installer runs entirely from RAM. Tested with 6 GB.

On the Mac: Docker (OrbStack), Homebrew `dnsmasq`, Python 3, `uv` (`brew install uv`). In the Spectrum app,
reserve the Mac's IP (`192.168.1.10` at build time); it is baked into the boot files.

## Use

```sh
./build.sh      # once, and again if the Mac's IP / config.env / ISO changes (~1 min after first run)
./run.sh        # asks for your sudo password (dnsmasq needs ports 67/69/4011); Ctrl-C stops
```

Then on each PC: open the one-time boot menu (often F12/F11/Esc) and pick the network /
PXE IPv4 entry. Everything after that is unattended: the installer menu auto-selects
"Automated" after 10 s, installs, reboots, and the server hands the PC back to its USB
stick. Several PCs can install at once; each pulls ~1.7 GB over the Mac's Wi-Fi.

**PC drops straight back to the boot menu?** See [Troubleshooting](#troubleshooting);
start with the cable. If the firmware's own PXE is the problem, boot iPXE from the
node's USB stick instead: `./make-usb.sh` lists external disks, `./make-usb.sh diskN`
erases that stick and writes `out/tftp/ipxe.efi` as `EFI/BOOT/BOOTX64.EFI`. Boot the
stick from the boot menu (UEFI, Secure Boot off) with `./run.sh` running; iPXE uses its
own NIC driver, gets an address from the router, and chains to the Mac over HTTP. The
installer then overwrites that same stick.

Open **http://localhost:8070/** for the live dashboard: each node's status (installing →
first boot → ready), IP, MAC, model, first-boot report, download progress, and an
activity log (dnsmasq, installer, downloads) with filters. **Reserve each node's IP in
the My Spectrum app** (Advanced settings → IP reservations) right away — the dashboard
has a copy button for each node's IP and MAC. The router's DHCP pool is all of
`.2–.254` and cannot be changed, so an unreserved node IP can be handed to another
device later.

Expand a node for its MACs, boot disk, full model and first-boot report. The dashboard
follows the system light/dark setting:

![Light theme with hnhv01 expanded](docs/dashboard-light.png)

API reference: **http://localhost:8070/api** (Scalar; raw schema at `/api/openapi.json`).
Like the dashboard, it is served to this Mac only; the installer endpoints stay open to
the LAN.

![API reference at /api](docs/api-scalar.png)

Web UI: `https://<node-ip>:8006`, user `root`, realm PAM, password from
`secrets/root-password`. The dashboard's **Root password** button copies it; the pencil
next to it sets a new one for future installs and, optionally, on every installed node
at once over SSH (automation key; only the SHA-512 crypt hash is sent).

## Day-2

```sh
python3 server/core.py list           # hosts, status, IPs, MACs
python3 server/core.py forget hnhv03  # next PXE boot of that PC reinstalls it (wipes its USB stick)
```

The dashboard's Reinstall button does the same as `forget`.

- Internal drive skipped (not blank or several drives)? The first-boot report says so.
  On the node: `wipefs -a /dev/sdX && pxe-first-boot` (destroys that drive's data).
- Cluster: on `hnhv01` run `pvecm create homelab`, then join the rest via Datacenter →
  Cluster → Join. Every node uses the same storage ID `vmdata`, so the cluster-wide
  storage config works unchanged.
- Installer failure: the PC drops to a shell. Logs: `/tmp/fetch_answer.log`,
  `/tmp/auto_installer`, `/tmp/install-low-level-start-session.log`.

## Troubleshooting

Note the PC's MAC first (firmware setup, system information) to match log lines.
Walk the layers in order; the first one without evidence is where it fails.

| # | Layer | Evidence | Missing means |
|---|---|---|---|
| 1 | Link | Port lights on; firmware shows "Media present"; iPXE does not print `Waiting for link-up on net0... Down` | Cable not seated, bad cable/port, or NIC disabled in firmware |
| 2 | DHCP request | `dnsmasq` lines in the dashboard Activity log (or the responder log, below) for that MAC | Firmware PXE gives up before the network: check its network-boot settings, or use the USB stick |
| 3 | Boot file | TFTP `sent ipxe.efi` to that IP | Secure Boot on, or firmware rejected the proxyDHCP offer |
| 4 | iPXE | Activity: `install <ip>: <mac> gets the installer` | iPXE prints why (no address, server unreachable) |
| 5 | Installer | Node status installing → first boot → ready | Installer logs above |

The USB stick doubles as a link tester: iPXE prints the link state of every NIC.

### Wired proxyDHCP responder (optional)

If the Mac's Wi-Fi does not see PXE broadcasts, any wired machine can answer them while
the Mac keeps serving HTTP. `responder/pxe-responder.py` is a stdlib proxyDHCP + TFTP
server that also logs every DHCP request it hears (layer 2 above). Copy it with
`out/tftp/ipxe.efi` and `out/tftp/undionly.kpxe` into one folder, open UDP 67, 69 and 4011
inbound, then:

```sh
uv run pxe-responder.py --ip <that machine's IP> --root <folder>   # needs admin/root for port 67
./run.sh --no-dnsmasq                                             # on the Mac
```

## Layout

| Path | Purpose |
|---|---|
| `config.env` | site settings (interface, hostname prefix, timezone, ISO) |
| `build.sh`, `builder/` | builds `out/` in a Debian container: prepared PXE installer, iPXE, password hash |
| `make-usb.sh` | writes iPXE to a USB stick, for PCs whose firmware PXE fails |
| `responder/pxe-responder.py` | optional proxyDHCP + TFTP responder for a wired machine |
| `run.sh` | writes `out/dnsmasq.conf`, starts `server/app.py` (which runs dnsmasq) |
| `server/app.py` | installer endpoints + web UI + dnsmasq supervisor; uv inline script (Litestar, Jinja2, embedded Granian) |
| `server/core.py` | stdlib-only answer-file logic and CLI; state in `state/hosts.json` |
| `server/templates/`, `server/static/` | web UI templates and assets (Basecoat UI via CDN, no build step) |
| `server/first-boot.sh` | runs once on each node (installed as `/usr/local/sbin/pxe-first-boot`) |
| `secrets/` | root password + hash (mode 600) |
| `cache/` | ISO and iPXE source |
| `docs/` | README screenshots (made-up fleet) |
