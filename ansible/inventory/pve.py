#!/usr/bin/env python3
"""Ansible inventory: every Proxmox node the PXE server has seen install.

Reads ../../proxmox-pxe/state/hosts.json (written by proxmox-pxe/server/core.py, via server/app.py)
and puts each installed node in group `pve` with its install-time IP.
Static group membership (e.g. `core`) lives in inventory/groups.yml.
"""

import json
import sys
from pathlib import Path

STATE = Path(__file__).resolve().parents[2] / "proxmox-pxe" / "state" / "hosts.json"


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--host":
        print("{}")
        return
    hosts = json.loads(STATE.read_text())["hosts"] if STATE.exists() else {}
    nodes = {
        name: {"ansible_host": host["ip"], "pve_mgmt_mac": host.get("mgmt_mac", "")}
        for name, host in sorted(hosts.items())
        if host.get("status") in ("installed", "ready") and host.get("ip")
    }
    print(json.dumps({"pve": {"hosts": list(nodes)}, "_meta": {"hostvars": nodes}}, indent=2))


if __name__ == "__main__":
    main()
