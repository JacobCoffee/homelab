# Homelab baseline (Ansible)

Runs from the Mac against Proxmox nodes installed by [`../proxmox-pxe`](../proxmox-pxe).
Nodes appear in the inventory automatically once they report installed
(`../proxmox-pxe/state/hosts.json`); SSH uses `../proxmox-pxe/secrets/automation_ed25519`,
which every node trusts from install.

| Play | Hosts | Result |
|---|---|---|
| `pve_cluster` | all nodes | one cluster `homelab`: the first node creates it, every other node joins over node-to-node SSH (no password); a node with guests is refused; joined nodes get their ZFS pools registered and HA turned on |
| `pve_sdn` | all nodes | SDN VXLAN zone `lab` over the house LAN with one vnet per lab zone, so lab guests can run on any node |
| `tailscale_tailnet` | the Mac (API) | tailnet policy pushed from [`roles/tailscale_tailnet/templates/policy.hujson.j2`](roles/tailscale_tailnet/templates/policy.hujson.j2): tags `tag:pve`/`tag:router`, lab routes auto-approved, members reach everything |
| `tailscale` | all nodes | node joined to your tailnet as `tag:pve` (`--accept-dns=false`), reachable even when OPNsense is down |
| `opnsense` | `core` | VM 100 `hnfw01`: OPNsense 26.7 nano, bootstrapped unattended, kept patched, sharing the lab subnets on Tailscale (see below) |
| `adguard` | `core` | CT 101 `hndns01` (10.10.10.53): AdGuard Home, the DNS server for INT and K8S |

## Network

| Zone | VNI | Subnet | Proxmox bridge | OPNsense |
|---|---|---|---|---|
| WAN (house LAN, Spectrum) | – | 192.168.1.0/24 (DHCP) | `vmbr0` | `vtnet0` |
| INT | 10 | 10.10.10.0/24 | `vxint` | `vtnet1` 10.10.10.1 |
| DMZ | 20 | 10.10.20.0/24 | `vxdmz` | `vtnet2` 10.10.20.1 |
| K8S | 30 | 10.10.30.0/24 | `vxk8s` | `vtnet3` 10.10.30.1 |

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

OPNsense advertises 10.10.10/20/30.0/24; the policy approves those routes for `tag:router`.
To reach the lab from a Mac or laptop, accept routes once on that device:
`tailscale set --accept-routes=true` (the macOS app: **Use Tailscale subnets**).

Already-connected devices are left alone, so to move the nodes to another tailnet: run
`tailscale logout` on each, swap the token, re-run `site.yml`. (Moving OPNsense this way is
untested; the plugin only passes the auth key when tailscaled has no logged-in profile.)

## OPNsense

The play ends by printing OPNsense's WAN IP and MAC: **reserve that IP in the My Spectrum
app**, since the management rules and later port-forwards point at it.

- WebGUI: `https://<wan-ip>/`, user `root`, password `vault_opnsense_root_password`
  (`ansible-vault view inventory/group_vars/all/vault.yml`).
- SSH: `ssh -i ../proxmox-pxe/secrets/automation_ed25519 root@<wan-ip>` (keys only).
- API: key/secret `vault_opnsense_api_key` / `vault_opnsense_api_secret`.
- WebGUI, SSH and API are reachable from the house LAN (`opnsense_mgmt_sources`), and from the
  tailnet at `https://10.10.10.1/`.

### How the bootstrap works

1. Downloads the official nano image, verifies the published SHA-256.
2. Renders a minimal `config.xml` (hostname, interfaces, root password + SSH key, API key,
   WAN management rules). Hashes are made on the Proxmox host (`openssl passwd -6`).
3. Packs it as `conf/config.xml` on a small FAT partition inside an MBR disk image, attached as
   a USB disk. The importer only reads partitions, so a bare FAT image is rejected.
4. Starts the VM and answers the first-boot configuration importer over the serial socket
   (`roles/opnsense/files/opnsense-import.py`), then detaches the USB image and deletes it.
5. Finds the WAN IP by MAC on `vmbr0` and waits for the API.

Steps 1–4 only run when VM 100 doesn't exist (afterwards the images are deleted, since they'd
only fill the USB root); step 5 runs every time.
Format details and evidence: [`roles/opnsense/NOTES.md`](roles/opnsense/NOTES.md).

Every run then applies these settings through the REST API (`roles/opnsense/tasks/api.yml`,
plain `ansible.builtin.uri`; each change is checked first, so a second run changes nothing):

- **Updates:** pending updates within the 26.7 series are installed, rebooting OPNsense when
  the update needs it (plugins can't be installed on an out-of-date box). Skip for one run
  with `-e opnsense_update=false`.
- **DHCP (Dnsmasq)** on INT, DMZ and K8S: `.100`–`.199` per zone, with leases registered in DNS.
  Dnsmasq's DNS listens on `127.0.0.1:53053`. INT and K8S clients get AdGuard (`lab_dns`) as DNS;
  DMZ clients get their gateway, so DMZ needs no hole into INT.
- **DNS (Unbound)** on port 53 on all interfaces, with DNSSEC. It forwards each zone domain to
  Dnsmasq, so DHCP client names resolve. AdGuard uses it as its only upstream.
- **DMZ isolation:** DMZ → INT and DMZ → K8S are blocked, ahead of the DMZ allow-any rule.
- **Tailscale** (`os-tailscale` plugin) as `tag:router`, advertising the lab subnets.
- **Port-forward** WAN 53 → AdGuard, so house devices can use it: set a device's DNS to
  OPNsense's WAN IP.

The `oxlorg.opnsense` collection is pinned to OPNsense versions (latest supports 26.1), so it
isn't used. Endpoint and field details: [`roles/opnsense/NOTES.md`](roles/opnsense/NOTES.md).

## AdGuard Home

`hndns01`, an unprivileged Debian 13 LXC at 10.10.10.53 running a pinned, checksum-verified
AdGuard Home release, configured through its API inside the container (`pct exec`), so the
Mac needs no route into the lab. Upstream is OPNsense's Unbound (DNSSEC, lab names, and
client names in the query log); lists are AdGuard DNS filter plus AdAway (`adguard_filters`).

- Web UI: `http://10.10.10.53/` over Tailscale, user `admin`, password `vault_adguard_password`.
- Clients: INT and K8S via DHCP; house devices by pointing their DNS at OPNsense's WAN IP.

## Secrets

`inventory/group_vars/all/vault.yml` is ansible-vault encrypted; the vault password lives in
the macOS Keychain (`security find-generic-password -a homelab -s ansible-vault -w`), read by
`scripts/vault-pass.sh`. Back that password up (e.g. in your password manager): without it the
vault can't be decrypted.
