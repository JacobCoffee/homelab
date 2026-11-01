# Homelab baseline (Ansible)

Runs from the Mac against Proxmox nodes installed by [`../proxmox-pxe`](../proxmox-pxe).
Nodes appear in the inventory automatically once they report installed
(`../proxmox-pxe/state/hosts.json`); SSH uses `../proxmox-pxe/secrets/automation_ed25519`,
which every node trusts from install.

| Play | Hosts | Result |
|---|---|---|
| `tailscale` | all nodes | node joined to your tailnet (`--accept-dns=false`), reachable even when OPNsense is down |
| `pve_network` | `core` (hnhv01) | `vmbr1`: VLAN-aware bridge on the lab trunk NIC carrying VLANs 10/20/30 |
| `opnsense` | `core` | VM 100 `hnfw01`: OPNsense 26.7 nano, bootstrapped unattended (see below) |

## Network

| Zone | VLAN | Subnet | OPNsense |
|---|---|---|---|
| WAN (house LAN, Spectrum) | – | 192.168.1.0/24 (DHCP) | `vtnet0` on `vmbr0` |
| INT | 10 | 10.10.10.0/24 | `vtnet1` 10.10.10.1 |
| DMZ | 20 | 10.10.20.0/24 | `vtnet2` 10.10.20.1 |
| K8S | 30 | 10.10.30.0/24 | `vtnet3` 10.10.30.1 |

Edit zones in `inventory/group_vars/all/main.yml` (`lab_zones`) before the first OPNsense deploy.

## First run

```sh
cd ansible
ansible-playbook playbooks/discover-nics.yml        # find hnhv01's card ports
echo 'pve_trunk_nic: enp1s0f0' > inventory/host_vars/hnhv01.yml   # card port 1 (use the real name)
ansible-vault edit inventory/group_vars/all/vault.yml   # add tailscale_authkey (reusable, pre-approved)
ansible-playbook playbooks/site.yml
```

The play ends by printing OPNsense's WAN IP and MAC: **reserve that IP in the My Spectrum
app**, since the management rules and later port-forwards point at it.

- WebGUI: `https://<wan-ip>/`, user `root`, password `vault_opnsense_root_password`
  (`ansible-vault view inventory/group_vars/all/vault.yml`).
- SSH: `ssh -i ../proxmox-pxe/secrets/automation_ed25519 root@<wan-ip>` (keys only).
- API: key/secret `vault_opnsense_api_key` / `vault_opnsense_api_secret`.
- WebGUI, SSH and API are reachable from the house LAN (`opnsense_mgmt_sources`).

## How the OPNsense bootstrap works

1. Downloads the official nano image, verifies the published SHA-256.
2. Renders a minimal `config.xml` (hostname, interfaces, root password + SSH key, API key,
   WAN management rules). Hashes are made on the Proxmox host (`openssl passwd -6`).
3. Packs it as `conf/config.xml` on a small FAT partition inside an MBR disk image, attached as
   a USB disk. The importer only reads partitions, so a bare FAT image is rejected.
4. Starts the VM and answers the first-boot configuration importer over the serial socket
   (`roles/opnsense/files/opnsense-import.py`), then detaches the USB image and deletes it.
5. Finds the WAN IP by MAC on `vmbr0` and waits for the API.

Steps 1–4 only run when VM 100 doesn't exist; step 5 runs every time.
Format details and evidence: [`roles/opnsense/NOTES.md`](roles/opnsense/NOTES.md).

Every run then applies these settings through the REST API (`roles/opnsense/tasks/api.yml`,
plain `ansible.builtin.uri`; each change is checked first, so a second run changes nothing):

- **DHCP (Dnsmasq)** on INT, DMZ and K8S: `.100`–`.199` per zone, with leases registered in DNS.
  Dnsmasq's DNS listens on `127.0.0.1:53053`.
- **DNS (Unbound)** on port 53 on all interfaces, with DNSSEC. It forwards each zone domain to
  Dnsmasq, so DHCP client names resolve.
- **DMZ isolation:** DMZ → INT and DMZ → K8S are blocked, ahead of the DMZ allow-any rule.

The `oxlorg.opnsense` collection is pinned to OPNsense versions (latest supports 26.1), so it
isn't used. Endpoint and field details: [`roles/opnsense/NOTES.md`](roles/opnsense/NOTES.md).

## Secrets

`inventory/group_vars/all/vault.yml` is ansible-vault encrypted; the vault password lives in
the macOS Keychain (`security find-generic-password -a homelab -s ansible-vault -w`), read by
`scripts/vault-pass.sh`. Back that password up (e.g. in your password manager): without it the
vault can't be decrypted.
