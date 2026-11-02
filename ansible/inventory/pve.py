#!/usr/bin/env python3
"""Ansible inventory: every Proxmox node the PXE server has seen install.

Reads ../../proxmox-pxe/state/hosts.json (written by proxmox-pxe/server/core.py, via server/app.py)
and puts each installed node in group `pve` with its install-time IP.
Static group membership (e.g. `core`) lives in inventory/groups.yml.

Installs normally happen through the hnpxe01 LXC (CT 102 on hnhv01; see
../roles/netboot), not this Mac, so its state/hosts.json there is the live one. This
script merges it in over the Mac's local copy (which stops growing once hnpxe01 takes
over, except for installs made through the Mac-fallback ./run.sh path used to reinstall
hnhv01 itself) by reaching it through hnhv01 over SSH + `pct exec`, the same path
Ansible itself uses. Any failure to reach it (hnhv01 unknown yet, unreachable, or
hnpxe01 not deployed yet) falls back to the local file only.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "proxmox-pxe" / "state" / "hosts.json"
AUTOMATION_KEY = ROOT / "proxmox-pxe" / "secrets" / "automation_ed25519"

# Where the netboot LXC lives (ansible/roles/netboot's defaults) and the node that hosts it.
PVE_HOST = "hnhv01"
NETBOOT_CTID = 102
NETBOOT_STATE_PATH = "/opt/netboot/state/hosts.json"


def local_hosts() -> dict:
    return json.loads(STATE.read_text())["hosts"] if STATE.exists() else {}


def ct_hosts(pve_host_ip: str) -> dict:
    """hnpxe01's state/hosts.json, fetched live through hnhv01; {} if unreachable."""
    if not AUTOMATION_KEY.exists():
        return {}
    cmd = [
        "ssh",
        "-i", str(AUTOMATION_KEY),
        "-o", "IdentityAgent=none", "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=3",
        f"root@{pve_host_ip}",
        "--", "pct", "exec", str(NETBOOT_CTID), "--", "cat", NETBOOT_STATE_PATH,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if out.returncode != 0 or not out.stdout.strip():
        return {}
    try:
        return json.loads(out.stdout)["hosts"]
    except (json.JSONDecodeError, KeyError):
        return {}


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--host":
        print("{}")
        return
    hosts = local_hosts()
    if pve_host_ip := hosts.get(PVE_HOST, {}).get("ip"):
        hosts.update(ct_hosts(pve_host_ip))  # hnpxe01's records win for overlapping names
    nodes = {
        name: {"ansible_host": host["ip"], "pve_mgmt_mac": host.get("mgmt_mac", "")}
        for name, host in sorted(hosts.items())
        if host.get("status") in ("installed", "ready") and host.get("ip")
    }
    print(json.dumps({"pve": {"hosts": list(nodes)}, "_meta": {"hostvars": nodes}}, indent=2))


if __name__ == "__main__":
    main()
