#!/usr/bin/env python3
"""Drive the OPNsense first-boot configuration importer over a serial socket.

Usage: opnsense-import.py SOCKET [--device da0] [--hostname hnfw01] [--timeout 600]

Connects to the VM's serial unix socket (Proxmox: /var/run/qemu-server/<vmid>.serial0),
answers the importer prompts, waits for the console banner and prints JSON:
  {"imported": true, "wan_ip": "192.168.1.23" | null}
Exits non-zero on timeout or if the importer never accepts the device.
"""

import argparse
import json
import re
import socket
import sys
import time

PRESS_KEY = b"Press any key to start the configuration importer"
SELECT = b"Select device to import from"
NO_LAYOUT = b"No known partition layout"
RESTORED = b"Restoring config.xml...done."
WAN_RE = re.compile(rb"WAN \(\w+\)\s+->\s+v4(?:/DHCP4)?:\s+(\d+\.\d+\.\d+\.\d+)/\d+")


def connect(path: str, deadline: float) -> socket.socket:
    while True:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(path)
            sock.settimeout(1.0)
            return sock
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("socket")
    ap.add_argument("--device", default="da0")
    ap.add_argument("--hostname", default="")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--attempts", type=int, default=10)
    args = ap.parse_args()

    deadline = time.monotonic() + args.timeout
    sock = connect(args.socket, deadline)
    buf = b""          # unconsumed console output
    seen = b""         # everything, for diagnostics
    imported = False
    answered = 0
    banner = f"*** {args.hostname}".encode() if args.hostname else b"*** "

    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            chunk = b""
        if chunk:
            buf += chunk
            seen += chunk
            sys.stderr.buffer.write(chunk)
            sys.stderr.flush()

        if not imported and PRESS_KEY in buf:
            sock.sendall(b" ")
            buf = buf.split(PRESS_KEY, 1)[1]
        elif not imported and SELECT in buf:
            # The USB disk can still be probing on the first prompt(s): retry.
            if answered >= args.attempts:
                print(json.dumps({"imported": False, "error": "importer rejected device"}))
                return 2
            if answered:
                time.sleep(3)
            sock.sendall(args.device.encode() + b"\r")
            answered += 1
            buf = buf.split(SELECT, 1)[1]
        elif not imported and RESTORED in buf:
            imported = True
            buf = buf.split(RESTORED, 1)[1]
        elif imported and banner in buf:
            # Give the banner a moment to print the interface lines.
            end = time.monotonic() + 5
            while time.monotonic() < end:
                try:
                    more = sock.recv(4096)
                except socket.timeout:
                    more = b""
                buf += more
                seen += more
            match = WAN_RE.search(buf)
            print(json.dumps({"imported": True, "wan_ip": match.group(1).decode() if match else None}))
            return 0

        # Keep the scan buffer bounded; prompts are short.
        if len(buf) > 65536:
            buf = buf[-8192:]

    print(json.dumps({"imported": imported, "error": "timeout", "tail": seen[-2000:].decode(errors="replace")}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
