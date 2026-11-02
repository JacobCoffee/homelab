# OPNsense 26.7 bootstrap notes

Evidence from an actual end-to-end run in QEMU 11.1.1 (TCG, arm64 host) against
the official `OPNsense-26.7-nano-amd64.img.bz2`. Everything below was observed
on the serial console / via `curl`+`ssh`, not guessed.

## config.xml schema (26.7, verified against opnsense/core@master)

- `<system><disablereplyto>yes</disablereplyto>` is required here. The WAN sits on
  the house LAN, so its clients (the Mac running Ansible, the Proxmox hosts) are on
  the WAN subnet. With reply-to, pf routes replies to the WAN gateway (the Spectrum
  router, verified with `tcpdump -e` on `tap100i0`: SYN-ACKs to the router's MAC),
  which drops them, so GUI/SSH/API time out although OPNsense answers. QEMU's slirp
  network forwarded those replies, so the emulated test passed without it.
  `Firewall/Plugin.php` turns the global flag into a default for every rule,
  including MVC rules (per-rule `disablereplyto` exists since 26.1.2).
- `src/etc/config.xml.sample` is the baseline the installer itself ships.
- `<system><user><password>` and `<system><user><apikeys><item><secret>` are
  both checked with PHP `password_verify()`
  (`src/opnsense/mvc/app/library/OPNsense/Auth/API.php`). `password_verify()`
  is a thin wrapper around libc `crypt(3)` plus a constant-time compare — it
  is **not** restricted to bcrypt, despite the name. OPNsense's own API key
  generator (`OPNsense\Auth\FieldTypes\ApiKeyField::add()`,
  `src/opnsense/mvc/app/models/OPNsense/Auth/FieldTypes/ApiKeyField.php`)
  does exactly:
  ```php
  'secret' => crypt($result['secret'], '$6$')   // SHA-512 crypt
  ```
  so the role hashes **both** the root password and the API secret into the same
  `$6$<salt>$<hash>` format (`openssl passwd -6 -stdin` on the Proxmox host). This also matches what
  `generatePasswordHash()` falls back to when "password policy compliance"
  is enabled (`openssl passwd -6 -stdin`); the stock default (no compliance
  mode) is PHP `PASSWORD_ARGON2ID`, but `$6$` is accepted everywhere
  `password_verify()` is used, including GUI, API and (indirectly, see below)
  console/SSH login, so we standardize on `$6$` for both secrets.
- `<apikeys>` XML shape is `<apikeys><item><key>KEY</key><secret>HASH</secret></item></apikeys>`.
  On load, `ApiKeyField::setValue()` auto-flattens this to an internal
  `key|secret` line format; the XML shape above is exactly what the model's
  `SimpleXMLElement` loader expects (confirmed by a successful
  `curl -u KEY:SECRET .../api/core/firmware/status` against an imported <!-- gitleaks:allow -->
  config using this exact shape).
- `<system><user><authorizedkeys>` is a `StoreB64Field`
  (`src/opnsense/mvc/app/models/OPNsense/Auth/User.xml`): it holds the
  **base64 of the raw `authorized_keys` file content** (one or more
  `ssh-ed25519 AAAA... comment` lines, newline separated), not a bare key.
  The template base64-encodes the single-line `opnsense_root_ssh_key`
  variable with Ansible's built-in `b64encode` Jinja filter.
  `src/etc/inc/auth.inc:local_user_set()` base64-decodes this straight into
  `~root/.ssh/authorized_keys`.
- Root's actual FreeBSD system account password hash is set to the literal
  string `*` (`src/etc/inc/auth.inc:local_user_set()`, comment: "integrated
  authentication handles passwords"). All login paths (console, SSH
  password auth if enabled, WebGUI) go through OPNsense's own PAM/auth
  layer which checks the config.xml `<password>` value via
  `password_verify()`, not `/etc/master.passwd`. This is why a `$6$`
  SHA-512-crypt hash works for root login even though it was never run
  through `pw usermod`.
- `<system><ssh>` (`src/etc/inc/plugins.inc.d/openssh.inc`, legacy flat
  array, no formal MVC model) fields we use:
  - `<enabled>1</enabled>` — turns the service on.
  - `<permitrootlogin>1</permitrootlogin>` — sets `PermitRootLogin yes`.
  - Omitting `<passwordauth>` sets `PasswordAuthentication no` and
    `ChallengeResponseAuthentication no` (confirmed: `ssh -o
    PreferredAuthentications=password` against the booted VM returned
    `Permission denied (publickey)` — password method wasn't even offered).
  - `<interfaces>wan,lan</interfaces>` — comma list of OPNsense interface
    names to bind sshd to (`lo0` is always added implicitly). sshd's actual
    `AllowGroups wheel` is hard-coded by `openssh.inc`; root's primary group
    is `wheel` for uid 0, so this is already satisfied.
- Firewall rules live under `<OPNsense><Firewall><Filter><rules><rule>`
  (the newer MVC-backed flat-field format used by `config.xml.sample`, not
  the old nested `<source><network>` pfSense-style format).
  `destination_net` is a `NetworkAliasField`
  (`src/opnsense/mvc/app/models/OPNsense/Base/FieldTypes/NetworkAliasField.php`)
  which special-cases the literal string `(self)` → "This Firewall" (all of
  the firewall's own interface addresses). We use `(self)` + `interface=wan`
  + `destination_port` 443/22 for the mgmt-access rules instead of guessing
  at a `wanip`-style per-interface token. **Verified working**: SSH (22) and
  HTTPS (443) from the slirp WAN gateway (`10.0.2.2`, which matches the
  `10.0.2.0/24` entry put in `opnsense_mgmt_sources` for the test) both
  succeeded through this exact rule.

## Importer flow (src/sbin/opnsense-importer, verified on serial console)

1. Early boot prints (after `>>> Invoking import script 'importer'`):
   ```
   Press any key to start the configuration importer:  .......
   ```
   Each `.` is printed with a 1-second timeout read; the whole window is
   **~7 seconds**. Any keypress (we send a single space) during that window
   aborts the countdown and enters the importer instead of booting straight
   through.
2. The importer then prints `camcontrol devlist`-style output and prompts:
   ```
   Select device to import from (e.g. ada0) or leave blank to exit:
   ```
   A blank answer aborts (boots into the live/installed config as-is).
3. **Gotcha — USB (umass) device not yet live on first prompt.** QEMU's
   `usb-storage` device attached via `uhci0`/`umass0` is still being probed
   by the kernel at the moment the importer first shows the device list; the
   initial `camcontrol devlist` therefore does **not** list `da0` yet
   (only the CD-ROM `cd0` placeholder). Answering `da0` at that first
   prompt fails with:
   ```
   No known partition layout was found for 'da0'.
   ```
   ...and the importer loops back to the same prompt. By the 2nd/3rd
   prompt (a few seconds later, after `umass0:2:0: Attached to scbus2` /
   `da0 at umass-sim0 ...` appear on the console) the device is live and
   answering `da0` again succeeds:
   ```
   Starting import for partition '/dev/da0s1'.
   Restoring config.xml...done.
   ```
   **Practical fix**: retry the same device name up to ~3 times, ~2 seconds
   apart, after the first "No known partition layout" response, instead of
   giving up after one failure. (On real Proxmox/KVM hardware with a
   proper USB controller this race may not reproduce, but the retry is
   cheap and safe either way.)
4. The vvfat drive showed up as the **only USB mass-storage disk**, `da0`
   (504 MB — QEMU vvfat reports a small fixed geometry for a FAT12/16
   directory-backed image regardless of host directory size), with an
   MBR partition at `/dev/da0s1`. The config file must be at
   `conf/config.xml` relative to the drive root (i.e. the vvfat host
   directory must contain a `conf/` subdirectory containing `config.xml`),
   per `src/sbin/opnsense-importer`'s `${MNT}/conf/config.xml` check.
5. After "Restoring config.xml...done." the system proceeds through a
   normal OPNsense boot (services, interface configuration, sshd, nginx for
   the webgui) and prints the usual console banner with the new hostname:
   ```
   *** hnfw01.int.z7x.org: OPNsense 26.7 (amd64) ***
    DMZ (vtnet2)    -> v4: 10.10.20.1/24
    INT (vtnet1)    -> v4: 10.10.10.1/24
    K8S (vtnet3)    -> v4: 10.10.30.1/24
    WAN (vtnet0)    -> v4/DHCP4: 10.0.2.15/24
   ```
   Interfaces, hostname, and timezone all matched the rendered config
   exactly.

## Timings observed (TCG emulation on an Apple Silicon Mac — expect this to
be much faster on real Proxmox/KVM hardware)

- Boot to "Press any key..." prompt: ~25–30s.
- Importer device-probe race resolved (3 retries, 2s apart): ~5–10s.
- Import + full service boot (sshd, webgui, firewall rules all "done"): a
  further ~60–90s.
- Total time from cold boot to a working API/SSH endpoint: ~2–3 minutes
  under TCG. On KVM this should be on the order of 15–30s.
- Real Proxmox/KVM (hnhv01, HP EliteDesk 800 G5): a full `site.yml` run with the
  image already cached in the workdir (VM create, import, boot, API config) took
  about 80 s.

## Hash formats

Both the root password and the API secret use glibc-style SHA-512 crypt
(`$6$<salt>$<hash>`), exactly the format OPNsense's own `ApiKeyField::add()`
produces for API secrets, and accepted by `password_verify()` for the root
password too. The role generates them with `openssl passwd -6 -stdin` **on the
Proxmox host** (Debian ships real OpenSSL). Not on the Mac: macOS
`/usr/bin/openssl` is LibreSSL (`unknown option '-6'`) and Python 3.13+ dropped
the stdlib `crypt` module.

## QEMU command used for the acceptance test

```sh
qemu-system-x86_64 \
  -machine pc -accel tcg,thread=multi -m 2048 -smp 2 \
  -drive file=disk.img,format=raw,if=virtio \
  -usb -drive file=fat:rw:usb,format=raw,if=none,id=usbstick \
      -device usb-storage,drive=usbstick,bus=usb-bus.0 \
  -netdev user,id=wan,hostfwd=tcp:127.0.0.1:18443-:443,hostfwd=tcp:127.0.0.1:18022-:22 \
      -device virtio-net-pci,netdev=wan \
  -netdev user,id=lan,restrict=on -device virtio-net-pci,netdev=lan \
  -netdev user,id=dmz,restrict=on -device virtio-net-pci,netdev=dmz \
  -netdev user,id=k8s,restrict=on -device virtio-net-pci,netdev=k8s \
  -chardev socket,id=serial0,path=serial.sock,server=on,wait=off \
  -serial chardev:serial0 \
  -display none -monitor none -no-reboot
```

Key points:
- `disk.img` is the decompressed, bit-for-bit copy of
  `OPNsense-26.7-nano-amd64.img` (raw, 3 GiB, MBR/BSD-disklabel, legacy BIOS
  boot — **not** GPT/UEFI; booted fine with QEMU's default SeaBIOS, no
  `-bios`/`edk2` OVMF firmware needed. `-machine pc` (i440fx) worked; did
  not need `-machine q35`).
- `usb` directory is a plain host directory containing `conf/config.xml`;
  QEMU's built-in `fat:rw:<dir>` driver exposes it as a FAT-formatted block
  device with no image file to build/mount by hand.
- The serial socket is a plain AF_UNIX stream socket; connect to it
  (e.g. Python `socket.socket(AF_UNIX, SOCK_STREAM)` or `socat -,raw,echo=0
  UNIX-CONNECT:serial.sock`) to both watch console output and type at the
  importer prompts. `server=on,wait=off` means QEMU boots immediately
  without waiting for a client — **connect as fast as possible after
  launch** (ideally in the same shell invocation, backgrounding qemu with
  `&` and polling for the socket file) or you will miss the ~7-second
  importer window and the VM will fall through to the interactive
  "LAGG/VLAN/assign interfaces" wizard instead.

## What the parent must replicate on Proxmox (`qm` args)

Proxmox's `qm` / `pve-qemu-kvm` wraps QEMU, so the equivalent pieces are:

- **Disk**: `qm importdisk <vmid> OPNsense-26.7-nano-amd64.img <storage> --format raw`,
  then `qm set <vmid> --virtio0 <storage>:vm-<vmid>-disk-0`. Boot order /
  BIOS: `qm set <vmid> --bios seabios` (no OVMF/UEFI needed — the nano image
  is BIOS/MBR, same as in the QEMU test).
- **4 virtio NICs**: `qm set <vmid> --net0 virtio,bridge=<wan-bridge> --net1
  virtio,bridge=<int-bridge> --net2 virtio,bridge=<dmz-bridge> --net3
  virtio,bridge=<k8s-bridge>`. NIC order maps 1:1 to `vtnet0..vtnet3` inside
  the guest, same as QEMU `-device virtio-net-pci` order — keep the
  `net0`..`net3` ordering exactly matching WAN/INT/DMZ/K8S.
- **USB config drive (what the role does, verified in QEMU with the same args)**:
  Proxmox's `qm` has no `fat:` driver option, so the role builds a raw image on
  the host. **It must be partitioned**: the importer only probes partitions
  (`da0s1`), and a bare `mkfs.vfat` superfloppy image is rejected forever with
  "No known partition layout was found for 'da0'" (QEMU's vvfat worked in the
  first test only because it synthesizes an MBR). Recipe: 15 MiB FAT image with
  `conf/` copied in via `mcopy`, prepended with 1 MiB of zeros, then
  `echo 'start=2048, type=c' | sfdisk config.img`. Attached through its own xHCI
  controller so the bus name is deterministic:
  ```
  args: -device qemu-xhci,id=cfgxhci -drive file=<workdir>/config.img,format=raw,if=none,id=cfgusb -device usb-storage,bus=cfgxhci.0,drive=cfgusb
  ```
  First boot only; the role deletes `args` and the image after the import.
- **Serial socket**: `qm set <vmid> --serial0 socket` exposes the serial
  port over a Unix socket at `/var/run/qemu-server/<vmid>.serial0`; connect
  to that exactly like `serial.sock` in the test (`qm terminal <vmid>` also
  works interactively, but for Ansible automation connect to the socket
  path directly with the same retry-on-"No known partition layout" logic
  documented above).
- The device-probe race (retry `da0` ~3 times) is expected to reproduce on
  Proxmox/KVM as well since it's a kernel USB enumeration timing issue, not
  specific to TCG — the Ansible task that drives the importer should retry.

## API configuration (`tasks/api.yml`, post-bootstrap REST API)

Everything below was verified against a live OPNsense 26.7 instance (the same
QEMU harness as above, using the production `config.xml.j2` + the new
MBR-partitioned USB config image, booted with
`-netdev user,id=wan,net=192.168.1.0/24,host=192.168.1.2,...` so the slirp
WAN gateway's source address, `192.168.1.2`, falls inside the real
`opnsense_mgmt_sources` CIDR — no test-only firewall exception needed) and
against `opnsense/core@master` source for field names.

### Endpoints used

| Area | Endpoint | Method | Notes |
|---|---|---|---|
| Dnsmasq general | `/api/dnsmasq/settings/get` / `.../set` | GET / POST | body wrapped `{"dnsmasq": {...}}`; booleans are `"0"`/`"1"` strings |
| Dnsmasq ranges | `/api/dnsmasq/settings/searchRange`, `.../addRange`, `.../setRange/{uuid}`, `.../delRange/{uuid}` | GET, POST | body wrapped `{"range": {...}}`; `addRange` always creates a new uuid (not idempotent on its own) |
| Dnsmasq service | `/api/dnsmasq/service/reconfigure` | POST | restarts dnsmasq with the new config |
| Unbound general | `/api/unbound/settings/get` / `.../set` | GET / POST | body wrapped `{"unbound": {"general": {...}}}` |
| Unbound query forwarding | `/api/unbound/settings/searchForward`, `.../addForward`, `.../setForward/{uuid}`, `.../delForward/{uuid}` | GET, POST | body wrapped `{"dot": {...}}`; server forces `type=forward` regardless of what's posted, so `type` is omitted |
| Unbound service | `/api/unbound/service/reconfigureGeneral` | POST | the "general page" reconfigure: reloads DNS, regenerates all `unbound.opnsense.d/*.conf` fragments (incl. `dot.conf`), restarts unbound, and restarts dhcpd hooks -- used instead of the plain `reconfigure` because we change `general.*` and forwards together |
| Firewall rules | `/api/firewall/filter/search_rule/`, `.../add_rule/`, `.../set_rule/{uuid}`, `.../del_rule/{uuid}` | GET/POST, POST | body wrapped `{"rule": {...}}`; GET-with-no-body and POST-with-JSON-body both work for `search_rule/` (tested both) |
| Firewall apply | `/api/firewall/filter/apply` | POST | runs `rc.filter_configure`; response `status` is raw stdout text, no structured "no changes" signal -- apply is only called when a mutating task above actually changed something |

### Field names (confirmed against opnsense/core@master + live `get`/`search` output)

- Dnsmasq general: `enable` (service on/off -- note: **not** `enabled`), `regdhcp` (register DHCP leases in DNS), `port` (DNS listener port; a separate read-only `dns_port` mirror exists and must never be set directly).
- Dnsmasq DHCP range (`dhcp_ranges` ArrayField, ArrayField-uuid-keyed, **not** keyed by interface): `interface`, `start_addr`, `end_addr`, `domain_type` (`range`, so the range's own `domain` field is used instead of the interface default), `domain`, `description`.
- Unbound general: `general.enabled`, `general.dnssec`, `general.active_interface` -- this field has **no `"all"` sentinel**; the empty string (the default) means "listen on every interface", confirmed both in `plugins.inc.d/unbound.inc` and by `sockstat -4 -l` showing `unbound ... udp4 *:53` after setting it to `""`.
- Unbound query forwarding (`dots.dot` ArrayField, same array backs both DoT and plain-forward entries, discriminated by `type`): `enabled`, `domain`, `server`, `port`. `type` is set to `forward` server-side by `addForwardAction`/`setForwardAction` regardless of what's posted.
- Firewall rule fields map 1:1 to the bootstrap `config.xml` rule fields (`enabled`, `sequence`, `action`, `quick`, `interface`, `direction`, `ipprotocol`, `protocol`, `source_net`, `destination_net`, `description`, ...) -- no renaming between the importer XML and the REST API.

### Rule ordering (DMZ isolation)

`sequence` is the authoritative evaluation order **within same-priority rules on one interface** (verified in `src/opnsense/mvc/app/plugins.inc.d/pf.inc`: rules are registered into `pf.conf` via `sortedBy(['sequence'])`, ascending). Array/insertion order in the model is irrelevant. Omitting `sequence` on `add_rule` auto-assigns `max(existing) + 100`, i.e. appends to the end -- so to land a new rule *before* the bootstrap "Default allow DMZ to any" rule (`sequence: "2"`, interface `opt1`), `sequence: "1"` must be set explicitly. All rules in the bootstrap + api.yml set use `quick: "1"`, so the lower-`sequence` block rule short-circuits evaluation for the packets it matches, while non-matching DMZ traffic (to the firewall itself, or to the internet) falls through to the existing `pass ... to any` rule unaffected.

Live `pfctl -sr` evidence after running `api.yml` once:
```
block drop in quick on vtnet2 inet from (vtnet2:network) to 10.10.10.0/24 label "<uuid>"   # Block DMZ -> INT
block drop in quick on vtnet2 inet from (vtnet2:network) to 10.10.30.0/24 label "<uuid>"   # Block DMZ -> K8S
pass  in quick on vtnet2 inet from (vtnet2:network) to any flags S/SA keep state label "<bootstrap DMZ-allow uuid>"
```
The two `block` lines sort strictly before the bootstrap `pass ... to any` line, and the DMZ-interface DHCP/DNS self-service `pass` rules (`to (self)` / `from (self)`, generated automatically for the DHCP server bound to vtnet2) are untouched, so DMZ clients can still reach the firewall's own DNS/DHCP and the internet.

### Idempotence evidence

Two consecutive `ansible-playbook` runs of a throwaway play that only does
`include_role: opnsense, tasks_from: api.yml` against the booted test VM:
- **Run 1** (fresh bootstrap, dnsmasq/unbound untouched, no custom rules): `ok=15 changed=8 failed=0` -- enabled dnsmasq, added 3 DHCP ranges, reconfigured dnsmasq, enabled+DNSSEC'd unbound, added 3 forward zones, reconfigured unbound, added 2 DMZ-block rules, applied the filter.
- **Run 2** (immediately after, no other changes): `ok=7 changed=0 failed=0 skipped=9` -- every mutating task's `when:` drift-check saw the live state already matched the desired state and skipped; the two `GET`-only "list existing" tasks always run (`ok`) but never register as changed.

### DNS resolution functional proof

Direct query to Dnsmasq's own listener confirms it answers for the lab domain:
```
drill -p 53053 @127.0.0.1 hnfw01.int.z7x.org   ->  10.10.10.1 (aa flag set)
```
To prove Unbound (port 53, the client-facing resolver) actually *forwards*
`int.z7x.org` queries to Dnsmasq rather than answering from its own records
(querying the firewall's own hostname is a bad test -- Unbound auto-registers
`hnfw01.<every configured domain>` for all of its own interface IPs and
answers those from local data, out-scoring the forward-zone), a temporary
Dnsmasq host override was added (`addHost` API, `probe.int.z7x.org` -> <!-- gitleaks:allow -->
`10.10.10.50`, reconfigured, queried, then deleted):
```
drill -p 53 @127.0.0.1 probe.int.z7x.org   ->  10.10.10.50
```
That address only exists in Dnsmasq's config, proving the `int.z7x.org`
forward-zone in `dot.conf` is live and working end-to-end.

### DHCP client test: not performed, documented why

No actual DHCP client negotiation (e.g. a second guest doing `dhclient`) was
run against vtnet1-3. In this harness those interfaces are QEMU
`-netdev user,...,restrict=on` slirp networks private to the single VM's own
process -- there is no host-reachable L2 segment to attach a second DHCP
client to without tap/vmnet device access (unavailable without elevated
privileges on this Mac) or a second paired guest wired via `-netdev socket`.
Instead, server-readiness was proven directly: `sockstat -4 -l` on the VM
shows `dnsmasq udp4 *:67` (DHCP) and `dnsmasq udp4/tcp4 *:53053` (DNS) both
bound and listening, and the rendered `dnsmasq.conf` contains the exact
`dhcp-range=tag:vtnet{1,2,3},<start>,<end>,86400` + `domain=<zone-domain>,...`
lines for all three zones (shown above) -- i.e. the DHCP server is correctly
configured and ready to answer, which is everything that can be verified
without a second network-attached guest. On real Proxmox hardware, running
`qm` with real bridges, a DHCP client test against a lab VM on one of the
VLAN-tagged bridges is straightforward and should be done once before
relying on this in production.


## Firmware, Tailscale and lab DNS (`tasks/firmware.yml`, `tailscale.yml`, `lab_dns.yml`)

Verified on the real `hnfw01` (Proxmox/KVM), 26.7 → 26.7.8_2.

- **Plugins need a current box.** `POST /api/core/firmware/install/os-tailscale` on stock 26.7
  returned `status: ok` but the job log said `Installation out of date. The update to
  opnsense-26.7.8_2 is required.` and installed nothing.
- **Update flow:** `POST firmware/check`, poll `GET firmware/upgradestatus` until `done`, read
  `GET firmware/status` (`status: update`, `needs_reboot: "1"`), `POST firmware/update`.
  `upgradestatus` keeps showing the finished *check* job until the update job starts, so wait
  for the update's own marker `***GOT REQUEST TO UPDATE***` (written by
  `scripts/firmware/config.sh`, `REQUEST="UPDATE"` in `update.sh`). 91 packages + reboot took
  about 2 minutes. `firmware/status` without a fresh check has no top-level `product_version`;
  `product.product_version` is always present.
- **os-tailscale 1.4:** settings `{"settings": {enabled, acceptDNS, acceptSubnetRoutes,
  advertiseExitNode, ...}}` (booleans as `"0"`/`"1"`), routes via `settings/add_subnet`
  `{"subnet4": {subnet, description}}`, key via `authentication/set`
  `{"authentication": {preAuthKey}}`, apply via `POST tailscale/service/reconfigure`.
  `GET tailscale/status/status` is `tailscale status --json` (`BackendState`), or
  `{"error": ...}` while stopped.
- **os-tailscale first-start bug:** `/etc/rc.conf.d/tailscaled` adds `--auth-key` only if
  `/var/db/tailscale/tailscaled.state` exists and has no `"_current-profile"`. On the first
  start the file doesn't exist yet, so `tailscale up` runs keyless (`NeedsLogin`); the first
  start creates it, so `POST tailscale/service/restart` logs in with the same key.
- **Subnet routing works without assigning `tailscale0`:** with the default (SNAT on), a
  tailnet client reached `10.10.10.1` and `10.10.10.53` with no interface assignment or rules.
- **Dnsmasq DHCP options:** the `option` key for dns-server is `"6"` (`get_option` lists
  `"6": "dns-server [6]"`). `interface` takes **one** interface: `"lan"` saves, a list
  returns HTTP 500, `"lan,opt2"` fails validation. One option per zone. Rendered as
  `dhcp-option=tag:vtnet1,6,10.10.10.53` in `/usr/local/etc/dnsmasq.conf`.
- **Port forward (MVC DNAT)** `POST /api/firewall/d_nat/add_rule`, body `{"rule": {interface:
  wan, ipprotocol: inet, protocol: "TCP/UDP", destination: {network: wanip, port: "53"},
  target, "local-port", descr, pass: pass}}`. `wanip` reads back as "WAN address";
  `pass: pass` keeps the matching WAN filter rule. Note `descr`, not `description`. Applied
  with `POST /api/firewall/filter/apply`.

## Moving lab NICs to SDN vnets (`tasks/main.yml`, `interfaces.yml`)

- `qm set 100 --netN virtio=<same MAC>,bridge=<vnet>,mtu=1` on the running VM hot-replugs the
  NIC: the interfaces came back **without the UP flag** (no lab DHCP/DNS) until a reboot. The
  role reboots the VM whenever it moves NICs; same MAC keeps `vtnetN` = zone N.
- FreeBSD's vtnet ignores the MTU virtio advertises (`mtu=1`): the guest stayed at 1500 on a
  1450 vnet. `POST /api/interfaces/assignment/set_item/<if>` `{"interface": {"mtu": "1450"}}`
  + `POST .../reconfigure` applies it live; the interface stays up.

## CARP pair (`tasks/interfaces.yml`, `ha.yml`)

Verified on the real `hnfw01` (migrated in place from `.1`) and `hnfw02` (fresh), 26.7.8_2.
Model fields read from `opnsense/core` `stable/26.7` and the live `get`/`get_item` templates.

- **Interface assignment:** `GET interfaces/assignment/search_item` rows are keyed by
  identifier (`uuid` = `lan`/`optN`) and carry `descr`, `ipaddr` (`"10.10.10.2/24"`) and `mtu`.
  `POST set_item/<ident>` `{"interface": {"type4": "staticv4", "ipaddr": "<ip>/<bits>",
  "mtu": "1450"}}` + `POST reconfigure` re-addresses live. `POST add_item` with
  `{"interface": {"if": "vtnet4", "descr": "PFSYNC", "enable": "1", ...}}` assigns a new NIC
  (it became `opt3`); `if` only offers NICs the kernel already has, hence the reboot after
  adding the VM NIC.
- **CARP VIPs:** `POST interfaces/vip_settings/add_item` `{"vip": {"mode": "carp", "interface",
  "network": "<ip>/<bits>", "vhid", "advbase", "advskew", "password", "descr"}}`: the
  controller splits `network` into the model's `subnet`/`subnet_bits` (what `search_item`
  returns). A password is required; VHIDs must be unique per interface. Apply with
  `POST vip_settings/reconfigure`.
- **pfsync:** `core/hasync` (`Hasync.xml`): `pfsyncinterface` (identifier), `pfsyncpeerip`,
  `pfsyncversion` (`1500` = 26.7+; the migrated box still had `1400`), `disablepreempt`,
  `synchronizetoip` (empty: no XMLRPC config sync). `POST core/hasync/set` `{"hasync": {...}}`
  + `POST core/hasync/reconfigure`. Result: `ifconfig pfsync0` shows `syncdev: vtnet4 syncpeer:
  <peer>`, and `pfctl -ss` counts on both boxes match within a few states.
- **Outbound NAT:** `GET/POST firewall/source_nat/get|set` `{"filter": {"general":
  {"snat_mode": "hybrid"}}}`; rules via `source_nat/add_rule` `{"rule": {"interface": "wan",
  "source_net": "<cidr>", "target": "<WAN VIP>", ...}}`. `pfctl -s nat` then lists
  `nat on vtnet0 inet from 10.10.10.0/24 to any -> <WAN VIP>` ahead of the automatic rules.
- **Dnsmasq split pools:** `dnsmasq.dhcp.reply_delay` (seconds, global) on the second box.
- **WAN forwards on the VIP:** `d_nat` `destination.network` accepts a plain address
  (the WAN VIP); a syslog line from `hnhv01` showed up as
  `10.10.30.210:514 (<WAN VIP>:514) <- 192.168.1.220`.
- **Moving a VIP:** `vip_settings/set_item` refuses a new address while any rule still names
  the old one (`whereUsed`), so `wan_vip.yml` runs after the outbound NAT rules and every WAN
  forward have been pointed at the new address; one run moved 192.168.1.250 -> .189.
- **Spectrum WAN address:** the router can't exclude addresses from its pool, and a static
  `.250` never showed up in the app's device list. A placeholder DHCP client with its own MAC
  (requested `.250`, offered `.189`; lease 43200 s) does show up and holds the address.
  `dhclient` failed there: Debian's AppArmor profile blocked its `-sf /bin/true` script and its
  lease/pid paths; busybox `udhcpc -s /bin/true` is unconfined and needs no state files.
- **CARP over the SDN vnets works:** the VXLAN head-end replication carries the multicast
  advertisements; `hnfw02` sat in `BACKUP` on all four VHIDs.
- **Failover, measured** (`qm suspend 100`, ping every 0.5 s from `hndns02` to 1.1.1.1): `hnfw02`
  took every VHID within ~3.5 s, the same ICMP flow kept going (pfsync state), new HTTPS from
  the lab worked; `qm resume 100` handed everything back with no loss (preemption, advskew 0).
- **Migration gotcha:** a flow that was mid-flight while `.1` moved from the interface to the
  VIP kept a state without NAT and stayed dead until it went idle; new flows were fine.
- **Tailscale:** both boxes advertise the same routes; the control plane keeps whichever was
  primary (here `hnfw02`, after `hnfw01` rebooted for its new NIC). Subnet traffic via the
  backup is SNATed to its own zone address, so replies come straight back.
