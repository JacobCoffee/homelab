# Homelab baseline (Ansible)

Runs from the Mac against Proxmox nodes installed by [`../proxmox-pxe`](../proxmox-pxe).
Nodes appear in the inventory automatically once they report installed
(`../proxmox-pxe/state/hosts.json`); SSH uses `../proxmox-pxe/secrets/automation_ed25519`,
which every node trusts from install.

| Play | Hosts | Result |
|---|---|---|
| `pve_cluster` | all nodes | one cluster `homelab`: the first node creates it, every other node joins over node-to-node SSH (no password); a node with guests is refused; joined nodes get their ZFS pools registered and HA turned on |
| `pve_sdn` | all nodes | SDN VXLAN zone `lab` over the house LAN with one vnet per lab zone, so lab guests can run on any node |
| `pve_acme` | all nodes | trusted pveproxy cert per node via Let's Encrypt DNS-01 (Cloudflare): account `default` and the `cloudflare` challenge plugin live cluster-wide in `/etc/pve`, each node gets `acmedomain0=<node>.internal.z7x.org,plugin=cloudflare` and a cert ordered once it's missing, self-signed, wrong name, or <30 days from expiry |
| `tailscale_tailnet` | the Mac (API) | tailnet policy pushed from [`roles/tailscale_tailnet/templates/policy.hujson.j2`](roles/tailscale_tailnet/templates/policy.hujson.j2): tags `tag:pve`/`tag:router`, lab routes auto-approved, members reach everything |
| `tailscale` | all nodes | node joined to your tailnet as `tag:pve` (`--accept-dns=false`), reachable even when OPNsense is down |
| `opnsense` | nodes in `firewalls` (`hnhv01`, `hnhv02`) | VMs 100 `hnfw01` / 110 `hnfw02`: OPNsense 26.7 nano CARP pair, bootstrapped unattended, kept patched, sharing the lab subnets on Tailscale (see below); also forwards WAN 853/8443 to AdGuard for Fire TV DNS-over-TLS (`tasks/firetv_dot.yml`, see `../fire-tv/DECISIONS.md` "DNS") |
| `adguard` | nodes in `dns_servers` (`hnhv01`, `hnhv02`) | CTs 101 `hndns01` (10.10.10.54) / 111 `hndns02` (10.10.10.55): AdGuard Home, with keepalived floating the lab DNS address 10.10.10.53 between them; also registers the Fire TV's AdGuard client and telemetry blocklist (`tasks/firetv_dot.yml`) -- its DoT/HTTPS TLS cert is pushed separately by a k8s CronJob (`../kubernetes/apps/network/adguard-dot`) |
| `homeassistant` | node in `home_assistant.host` (`hnhv02`) | VM 301 `hnha01` (10.10.10.20): Home Assistant OS on INT with the Aeotec Z-Wave/Zigbee stick passed through |
| `pve_csi` | all nodes | Proxmox user/role/token for the Proxmox CSI plugin, captured straight into `kubernetes/apps/storage/proxmox-csi-plugin/app/secret.sops.yaml`; skipped once that file exists, rotates the token if it's ever missing |
| `media_nfs` | node in `media_nfs.host` (`hnhv01`) | VM 401 `hnnfs01` (10.10.30.20): Debian 13 + cloud-init, nfs-kernel-server exporting `/srv/media` to the K8S zone (temporary, until a NAS takes over) |
| `talos` | nodes in `k8s_nodes`, then the Mac | VMs 201–204 `hnk8s01`–`04` (10.10.30.11–14, VIP .10; `hnk8s04` a worker): Talos Kubernetes, Cilium, then Flux takes over from [`../kubernetes`](../kubernetes) |
| `pve_backup` | all nodes | ZFS dataset `vmdata/backups` registered as `dir` storage `backup`, plus one cluster-wide nightly vzdump job (snapshot mode, zstd, keep-daily 7/keep-weekly 4) for VMs/CTs 100/101/102/110/111/301/401 -- see "Backups" below |
| `pve_theme` | all nodes | [ProxMorph](https://github.com/IT-BAER/proxmorph) themes (pinned release, sha256-checked) in the Color Theme selector, with "UniFi (Auto)" (UniFi Light/UniFi following the browser's light/dark setting) as the default for browsers that haven't picked one; its APT hook re-patches after Proxmox updates. Revert with `bash /root/proxmorph-<ver>/install.sh uninstall` |
| `pve_oidc` | all nodes (realm config applied once, `/etc/pve` is cluster-shared) | Proxmox login gets a `pocket-id` OpenID realm (issuer `https://id.z7x.org`, client `proxmox`, `scripts/pocket-id.py` manages the OIDC client + `vault_pocket_id_proxmox_secret`): `preferred_username` as the username claim, `groups` claim with autocreate, so Pocket ID's `lab-admins` group becomes PVE group `lab-admins-pocket-id`, pre-created here with `Administrator` on `/`. Login as `jacob@pocket-id` once a passkey is registered (My Account on id.z7x.org). `root@pam` is untouched as break-glass |
| `homepage` | `hnhv01` (and the Mac) | credentials for the Homepage start page in one SOPS Secret, `../kubernetes/apps/status/homepage/app/secret.sops.yaml`: a read-only Proxmox token (`homepage@pve`, PVEAuditor), an OPNsense user `homepage` on each firewall limited to System Activity and Traffic with its own API key, and the app keys and logins (arr-api-keys, Bazarr's and Seerr's config, the vault). Re-runs only rewrite the file when a value changed; the token and keys are re-created only if they're missing from it |
| `netboot` | `hnhv01` | CT 102 `hnpxe01` (house LAN): the [`../proxmox-pxe`](../proxmox-pxe) installer, always on, so new PCs install without the Mac |

## Network

| Zone | VNI | Subnet | Proxmox bridge | OPNsense |
|---|---|---|---|---|
| WAN (house LAN, Spectrum) | – | 192.168.1.0/24 (DHCP; CARP 192.168.1.189) | `vmbr0` | `vtnet0` |
| INT | 10 | 10.10.10.0/24 | `vxint` | `vtnet1` .2 / .3, CARP .1 |
| DMZ | 20 | 10.10.20.0/24 | `vxdmz` | `vtnet2` .2 / .3, CARP .1 |
| K8S | 30 | 10.10.30.0/24 | `vxk8s` | `vtnet3` .2 / .3, CARP .1 |
| pfsync | 99 | 10.10.99.0/24 | `vxsync` | `vtnet4` .2 / .3 |

Edit zones in `inventory/group_vars/all/main.yml` (`lab_zones`, `lab_mtu`, `lab_dns`) before the
first OPNsense deploy.

The lab zones are Proxmox SDN vnets in one VXLAN zone, tunnelled between the nodes over the
house LAN (UDP 4789), so no managed switch is needed and a guest on any node can join any zone.
VXLAN costs 50 bytes, so lab NICs run at MTU 1450: OPNsense's lab interfaces are set to it,
DHCP hands it out (option 26) and containers with static IPs get it from an if-up hook.
When the managed switches arrive, the same vnets can move to an SDN VLAN zone on a trunk port
(`hnhv01`: card port 1 `enp1s0f0`, with `enp1s0f1` spare for a future modem-side WAN) and the
MTU back to 1500, without touching guests' bridge names.

## First run

```sh
cd ansible
ansible-vault edit inventory/group_vars/all/vault.yml   # add tailscale_api_key (see Tailscale)
ansible-playbook playbooks/site.yml                     # or --skip-tags tailscale until the key is in
```

Re-run `site.yml` after every new node: it joins the cluster on the next run. With two
nodes the cluster has no quorum while either is down (running guests keep running, but
nothing can be started or changed); a third node or a QDevice fixes that.

## Tailscale

One secret drives it: `tailscale_api_key`, an **API access token** from the tailnet's admin
console (**Settings → Keys → Generate access token**, at most 90 days; renew it in the vault
when it expires). With it, every run pushes the tailnet policy from the repo (edits made in the
admin console are overwritten), and each node or OPNsense that isn't connected yet gets its own
single-use, pre-approved, tagged auth key minted on the spot. No auth keys to manage by hand.

Both firewalls advertise 10.10.10/20/30.0/24 (Tailscale fails over between the two subnet
routers itself); the policy approves those routes for `tag:router`.
To reach the lab from a Mac or laptop, accept routes once on that device:
`tailscale set --accept-routes=true` (the macOS app: **Use Tailscale subnets**).

Names: the tailnet sends `z7x.org` lookups to AdGuard (split DNS, set by the same role), so
on any tailnet device `hnhv01.internal.z7x.org`, `hnhv02…`, `hnfw01…` and `hnfw02…` resolve to
their Tailscale addresses (records generated from the device list for `tag:pve`/`tag:router`),
and internal-gateway services like `echo.z7x.org` resolve to the lab. At home, Tailscale connects
peer-to-peer over the LAN, so these names are as fast as local IPs. Off the tailnet they don't
exist. They keep resolving while one Proxmox node is down (both firewalls and both AdGuards
would have to be gone).

Already-connected devices are left alone, so to move the nodes to another tailnet: run
`tailscale logout` on each, swap the token, re-run `site.yml`. (Moving OPNsense this way is
untested; the plugin only passes the auth key when tailscaled has no logged-in profile.)

## OPNsense

Two firewalls (`firewalls` in `group_vars/all/main.yml`): `hnfw01` on `hnhv01` and `hnfw02` on
`hnhv02`. Each play run ends by printing the firewall's WAN IP and MAC: **reserve both in the My
Spectrum app**.

The WAN CARP address `192.168.1.189` (`firewall_wan_vip`) can't be reserved: the router can't
keep an address out of its pool, and the app doesn't list the CARP MAC (it misses plenty of
wired devices). Instead `hnhv01` holds an active lease on it with a placeholder DHCP client
(`wan-vip-lease.service`: busybox `udhcpc` on a macvlan `wanvip0`, MAC `02:fa:00:00:01:fa`,
hostname `hnfw-vip`, script `/bin/true`, no ARP), and the router won't offer a leased address
to anyone else. Renewals can't reach the placeholder (the router unicasts them to the address,
i.e. to the CARP master), so the service restarts every 4 h and re-requests the address, which
refreshes the 12 h lease. The play stops (`wan_vip.yml`) if the router hands the placeholder
anything else. Gap: if `hnhv01` is down for more than 12 h, the lease can expire.

- WebGUI: `https://<wan-ip>/`, user `root`, password `vault_opnsense_root_password`
  (`ansible-vault view inventory/group_vars/all/vault.yml`).
- SSH: `ssh -i ../proxmox-pxe/secrets/automation_ed25519 root@<wan-ip>` (keys only).
- API: key/secret `vault_opnsense_api_key` / `vault_opnsense_api_secret`.
- WebGUI, SSH and API are reachable from the house LAN (`opnsense_mgmt_sources`), and from the
  tailnet at `https://10.10.10.2/` (`hnfw01`) and `https://10.10.10.3/` (`hnfw02`).

### High availability (CARP)

- **Addresses:** each firewall has its own `.2`/`.3` in every zone; `.1` (every zone's gateway)
  and `192.168.1.189` on the WAN are CARP addresses held by the master. `hnfw01` (advskew 0)
  is master whenever it is up; preemption moves every CARP address together.
- **State sync:** pfsync over its own vnet (`vxsync`, 10.10.99.0/24), so connections survive a
  failover. No config sync: Ansible configures both firewalls.
- **Outbound NAT** is hybrid: the lab zones leave through `192.168.1.189`, which moves with the
  master, so synced states stay valid.
- **DHCP:** Dnsmasq can't share leases, so each firewall serves half of every zone's pool
  (`hnfw01` `.100`–`.149`, `hnfw02` `.150`–`.199`, answering after a 3 s delay) and both hold
  the same reservations. A dynamic client's name resolves only through the firewall that gave
  it the lease.
- **WAN forwards** (lab DNS, Fire TV DoT, syslog) are bound to `192.168.1.189`, so house devices and the
  Proxmox hosts follow the master.
- Check who is master: **Interfaces → Virtual IPs → Status** on either firewall.

### How the bootstrap works

1. Downloads the official nano image, verifies the published SHA-256.
2. Renders a minimal `config.xml` (hostname, interfaces with this firewall's own addresses, root
   password + SSH key, API key, WAN management rules). Hashes are made on the Proxmox host
   (`openssl passwd -6`).
3. Packs it as `conf/config.xml` on a small FAT partition inside an MBR disk image, attached as
   a USB disk. The importer only reads partitions, so a bare FAT image is rejected.
4. Starts the VM and answers the first-boot configuration importer over the serial socket
   (`roles/opnsense/files/opnsense-import.py`), then detaches the USB image and deletes it.
5. Finds the WAN IP by MAC on `vmbr0` and waits for the API.

Steps 1–4 only run when the VM doesn't exist (afterwards the images are deleted, since they'd
only fill the USB root); step 5 runs every time.
Format details and evidence: [`roles/opnsense/NOTES.md`](roles/opnsense/NOTES.md).

Every run then applies these settings through the REST API (`roles/opnsense/tasks/`, plain
`ansible.builtin.uri`; each change is checked first, so a second run changes nothing):

- **Updates:** pending updates within the 26.7 series are installed, rebooting OPNsense when
  the update needs it (plugins can't be installed on an out-of-date box). Skip for one run
  with `-e opnsense_update=false`.
- **Interfaces and HA** (`interfaces.yml`, `ha.yml`): own addresses, lab MTU, the pfsync
  interface, CARP addresses, pfsync, outbound NAT (see above).
- **DHCP (Dnsmasq)** on INT, DMZ and K8S, with leases registered in DNS. Dnsmasq's DNS listens on
  `127.0.0.1:53053`. Every zone gets the CARP `.1` as router; INT and K8S clients get AdGuard
  (`lab_dns`) as DNS; DMZ clients get the CARP `.1` (Unbound), so DMZ needs no hole into INT.
- **DNS (Unbound)** on port 53 on all interfaces, with DNSSEC. It forwards each zone domain to
  Dnsmasq, so DHCP client names resolve. AdGuard uses it (via the CARP `.1`) as its only upstream.
- **DMZ isolation:** DMZ → INT and DMZ → K8S are blocked, ahead of the DMZ allow-any rule.
- **Tailscale** (`os-tailscale` plugin) as `tag:router`, advertising the lab subnets.
- **Port-forward** WAN CARP address 53 → AdGuard, so house devices can use it: set a device's
  DNS to `192.168.1.189`.

The `oxlorg.opnsense` collection is pinned to OPNsense versions (latest supports 26.1), so it
isn't used. Endpoint and field details: [`roles/opnsense/NOTES.md`](roles/opnsense/NOTES.md).

## AdGuard Home

`hndns01` (`hnhv01`, 10.10.10.54) and `hndns02` (`hnhv02`, 10.10.10.55), unprivileged Debian 13
LXCs running a pinned, checksum-verified AdGuard Home release, configured identically through
its API inside each container (`pct exec`), so the Mac needs no route into the lab. Upstream is
OPNsense's Unbound (DNSSEC, lab names, and client names in the query log); lists are AdGuard
DNS filter plus AdAway (`adguard_filters`).

keepalived in each container (unicast VRRP, `roles/adguard/templates/keepalived.conf.j2`)
holds the lab DNS address `10.10.10.53` on the healthy instance with the higher priority
(`hndns01` while it answers on port 53), so every client keeps a single DNS server.
Query logs and statistics stay per instance; external-dns in `../kubernetes` writes its
records to both.

- Web UI: `http://10.10.10.54/` and `http://10.10.10.55/` over Tailscale, user `admin`,
  password `vault_adguard_password`.
- Clients: INT and K8S via DHCP; house devices by pointing their DNS at `192.168.1.189`.

## Home Assistant

`hnha01` is Home Assistant OS, imported from the official qcow2 the first time only (later
updates happen inside HA). It sits on INT with a DHCP reservation, and its radio stick is passed
through by USB ID (`home_assistant.usb`), so the VM must live on the node the stick is plugged
into.

- `https://home.z7x.org`: public through the Cloudflare tunnel; on the tailnet the same name
  goes straight to the lab gateway. **Turn on multi-factor authentication for every user**
  (profile → Security), since the login page is on the internet.
- `http://10.10.10.20/` directly over Tailscale (this HA release serves on port 80).

This HA release keeps its HTTP settings in `.storage/http`, not `configuration.yaml` (a YAML
`http:` block is ignored after the first start). The role merges the reverse-proxy trust
(`ha_trusted_proxies`, the K8S zone and pod ranges) into it with Core stopped.

## Kubernetes (Talos)

Talos VMs (`k8s_nodes` in `group_vars/all/main.yml`): three control planes (etcd + workloads;
two on `hnhv01`, one on `hnhv02`) and workers (`role: worker`, e.g. `hnk8s04` on `hnhv02`).
Losing `hnhv01` takes etcd down until it returns; moving a control plane to a third host fixes
that (a control plane can't become a worker in place: Talos reinstalls it). Control planes get
`kubernetes/talos/patches/controlplane.yaml` on top of `cluster.yaml`. The role creates the VMs
- **Sign in with Pocket ID** (`os-sso` plugin, `tasks/sso.yml`) -- see below.
from the Image Factory ISO, renders each
node's config from `kubernetes/talos` plus `roles/talos/templates/machine.yaml.j2`, bootstraps
etcd, installs Cilium and runs `flux bootstrap`; after that, the cluster is whatever is in
[`../kubernetes`](../kubernetes). `talosctl` and `kubectl` work from the repo root (paths come
from `mise.toml`).

### Sign in with Pocket ID (os-sso)

Adds an OIDC login button to each firewall's WebGUI login page, against
[Pocket ID](https://id.z7x.org) (`scripts/pocket-id.py`, client `opnsense`). The
[`os-sso`](https://github.com/MaximeWewer/os-sso) plugin isn't in the official OPNsense plugin
repo, so `tasks/sso.yml` installs it differently from every other plugin here: it downloads the
release `.pkg` matching the firewall's `pkg config ABI` straight to the box over SSH (root, the
automation key -- `opnsense_sso_sha256` in `defaults/main.yml` pins and checksum-verifies it,
since the only per-asset checksum GitHub publishes is the release API's own digest field), then
`pkg add`s it and restarts the WebGUI to pick up the new server type.

The provider itself (issuer, client ID/secret, group mapping) also has no REST API -- `System >
Access > Servers` is core's legacy, non-MVC page -- so it's written straight into
`<system><authserver>` in `config.xml` over the same SSH connection
(`templates/sso-authserver.php.j2`, idempotent: only the fields it owns are touched, and it
changes nothing on a second run), the same way os-sso's own lab scripts configure it.

- Pocket ID's `lab-admins` group maps to the local `admins` group (`sso_group_map`, an explicit
  operator mapping -- the plugin refuses the 1:1 name fallback for any group carrying firewall
  privileges) and gates who can sign in at all (`sso_required_groups`).
- **Base URL is per-firewall**, not the shared CARP VIP: each firewall points at its own
  INT-zone address (`10.10.10.2` / `.3`), since os-sso requires one and there's no config sync
  to keep a shared value consistent. Pocket ID's `opnsense` client is registered with every
  callback URL that implies (`scripts/pocket-id.py`): the VIP, both firewalls' own addresses,
  and both `hnfwNN.int.z7x.org` hostnames -- the VIP entry only keeps the IdP from refusing a
  request that reaches it (e.g. the WAN CARP address isn't an SSO door); **start the SSO login
  at a firewall's own address, not `10.10.10.1`**. The login's session cookie is bound to
  whichever host it started at, but the callback always lands on this firewall's own Base URL;
  starting at the shared VIP can round-trip to the *other* firewall's cookie-less session and
  fail with "missing provider" if `.1` resolves to the other box by the time the IdP redirects
  back. The local password form has no such redirect and works the same at any address.
- **The WebGUI runs as root**, not OPNsense's hardened `wwwonly` user (`config.xml.j2` no longer
  sets `<noroot>`; `tasks/sso.yml` clears it, and the `/var/run/www_non_root` lock core latches
  it behind, on an existing box). OPNsense's stock WebGUI runs as root too; `noroot` is opt-in
  hardening. os-sso refuses its state directory `/var/db/os-sso` unless it's owned by a uid it
  trusts (`StateDir::assertSafe()`), and without PHP's posix extension (OPNsense's php has none)
  the only uid it can see is 0. A `wwwonly`-owned directory is rejected ("owned by another
  user", tested on hnfw02), so noroot and os-sso can't both be on. Turn `noroot` back on if
  os-sso ever stops needing it. Local password login (root break-glass) works either way.
- The local `root` password stays the break-glass login; os-sso never touches it.

## Netboot (hnpxe01)

`hnpxe01` runs the same installer and dashboard as `../proxmox-pxe/run.sh`, permanently, in an
unprivileged LXC on the house LAN. It gets its IP by DHCP; reserve it in the My Spectrum app,
because the IP is built into the boot files (if it changes, re-run the play and it rebuilds).

- Dashboard: `ssh -L 8070:127.0.0.1:8070 root@<hnpxe01 IP>`, then `http://localhost:8070/`.
- Reinstalling `hnhv01` itself needs the Mac (hnpxe01 lives on it): stop hnpxe01 first so only
  one PXE server answers (`ssh root@<hnhv01> pct stop 102`), then `../proxmox-pxe/run.sh`.
- The inventory reads installed nodes from hnpxe01 and the Mac's `state/hosts.json` together.

## Proxmox certificates

Each node presents a trusted Let's Encrypt certificate on `pveproxy` (the web UI/API,
`:8006`) for `<inventory_hostname>.internal.z7x.org`, instead of the cluster's self-signed
`pve-ssl.pem`. Validation is DNS-01 against `z7x.org` through Cloudflare, so no port 80
exposure is needed. The ACME account (`default`) and the `cloudflare` challenge plugin are
cluster-wide config in `/etc/pve`, so they're set up once; each node then gets its own
`acmedomain0` and an initial cert order.

Renewal is automatic: once a node has an ACME-issued cert, `pve-daily-update.service`
renews it whenever it's within 30 days of expiring, using the same Cloudflare plugin — no
timer of ours needed. The Cloudflare API token lives in `/etc/pve/priv/acme/plugins.cfg`
on the cluster (same vault secret as everywhere else, `cloudflare_api_token`).

## Backups

`pve_backup` creates `vmdata/backups` (a ZFS dataset on the cluster-wide mirror, local to each
node) and registers it as `dir` storage `backup`, then creates one cluster-wide vzdump job
(id `nightly`, 02:30 daily, snapshot mode, zstd, `keep-daily=7,keep-weekly=4`) covering OPNsense
(100, 110), AdGuard (101, 111), netboot (102), Home Assistant (301) and the media NFS VM's root
disk (401 -- its data disk is `backup=0`, set by the `media_nfs` role, so only the OS disk is
backed up). Talos VMs (2xx) are excluded: they're rebuilt from git (`../kubernetes`), and etcd has its own
offsite backup (see below). The job runs locally only -- no restic/extra daemons on the Proxmox
hosts themselves, keeping them minimal.

Offsite (Cloudflare R2, bucket `homelab-backups`, kept lean for the 10 GB free tier) covers what
local vzdump can't survive a lost node: etcd, the OPNsense config, and every app's config PVC
(VolSync). See [`../kubernetes/README.md#backups-and-restore`](../kubernetes/README.md#backups-and-restore)
for all restore commands, R2 layout and retention.

```sh
# List/inspect local vzdump backups
ssh -i proxmox-pxe/secrets/automation_ed25519 root@<node> pvesh get /cluster/backup
ssh -i proxmox-pxe/secrets/automation_ed25519 root@<node> ls -la /vmdata/backups/dump/

# Restore a VM/CT from the most recent local backup (run on the node that holds it)
ssh -i proxmox-pxe/secrets/automation_ed25519 root@<node> \
  qmrestore /vmdata/backups/dump/vzdump-qemu-<vmid>-<timestamp>.vma.zst <vmid> --storage vmdata
ssh -i proxmox-pxe/secrets/automation_ed25519 root@<node> \
  pct restore <vmid> /vmdata/backups/dump/vzdump-lxc-<vmid>-<timestamp>.tar.zst --storage vmdata
```

## Secrets

`inventory/group_vars/all/vault.yml` is ansible-vault encrypted; the vault password lives in
the macOS Keychain (`security find-generic-password -a homelab -s ansible-vault -w`), read by
`scripts/vault-pass.sh`. Back that password up (e.g. in your password manager): without it the
vault can't be decrypted.

Also in the vault: `cloudflare_api_token` (z7x.org DNS edit + tunnel edit) and
`vault_sops_age_key`, the age key that decrypts every `*.sops.yaml` under `../kubernetes`
(the Mac's copy is `~/.config/sops/age/keys.txt`). `vault_r2_access_key_id` /
`vault_r2_secret_access_key` are the Cloudflare R2 S3 API credentials (account
`0346e28e1f2af422c32c0c37e9f7f363`, derived from `vault_r2_api_token`, an account API token with
Admin Read & Write on R2) used by every offsite backup in
[`../kubernetes/README.md#backups-and-restore`](../kubernetes/README.md#backups-and-restore).
