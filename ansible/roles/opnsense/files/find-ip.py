#!/usr/bin/env python3
"""Print the IPv4 address that MAC currently has on a local /24-ish subnet.

Usage: find-ip.py MAC CIDR DEV
Pokes every address in CIDR (UDP to the discard port) so the kernel ARPs for it,
then reads the neighbor table. Works even when the target drops all inbound
traffic, because ARP is answered below the firewall. Prints nothing and exits 1
if the MAC is not found.
"""

import ipaddress
import socket
import subprocess
import sys
import time


def main() -> int:
    mac, cidr, dev = sys.argv[1].lower(), sys.argv[2], sys.argv[3]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for _ in range(2):
        for ip in ipaddress.ip_network(cidr, strict=False).hosts():
            try:
                sock.sendto(b"", (str(ip), 9))
            except OSError:
                pass
        time.sleep(2)
        neigh = subprocess.run(["ip", "-4", "neigh", "show", "dev", dev], capture_output=True, text=True).stdout
        for line in neigh.splitlines():
            parts = line.split()
            if "lladdr" in parts and parts[parts.index("lladdr") + 1].lower() == mac:
                print(parts[0])
                return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
