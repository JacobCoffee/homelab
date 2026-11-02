#!/usr/bin/env python3
"""PXE boot responder: proxyDHCP + TFTP, standard library only.

Run it on any wired machine on the LAN (Windows, Linux, macOS) when the netboot
server itself can't hear PXE broadcasts, e.g. because it is on Wi-Fi. It never
hands out IP addresses (the router keeps doing that). It only tells PXE clients
which boot file to fetch and serves that file over TFTP. The iPXE binaries chain
to the netboot server over HTTP, so nothing else runs here.

  python pxe-responder.py --ip 192.168.1.240 --root C:\\Users\\jacob\\pxe

Needs UDP 67, 69 and 4011 (administrator/root).
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path

MAGIC = b"\x63\x82\x53\x63"
BOOTFILES = {0: "undionly.kpxe", 6: "ipxe.efi", 7: "ipxe.efi", 9: "ipxe.efi"}  # option 93 arch -> file
DISCOVER, OFFER, REQUEST, ACK = 1, 2, 3, 5


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --- proxyDHCP --------------------------------------------------------------------


def parse_dhcp(data: bytes) -> tuple[dict, dict[int, bytes]] | None:
    if len(data) < 240 or data[0] != 1 or data[236:240] != MAGIC:
        return None
    hdr = {
        "htype": data[1],
        "hlen": data[2],
        "xid": data[4:8],
        "flags": data[10:12],
        "ciaddr": data[12:16],
        "chaddr": data[28:44],
    }
    opts: dict[int, bytes] = {}
    i = 240
    while i < len(data):
        tag = data[i]
        if tag == 255:
            break
        if tag == 0:
            i += 1
            continue
        if i + 1 >= len(data):
            break
        length = data[i + 1]
        opts[tag] = data[i + 2 : i + 2 + length]
        i += 2 + length
    return hdr, opts


def opt(tag: int, value: bytes) -> bytes:
    return bytes([tag, len(value)]) + value


def build_reply(hdr: dict, opts: dict[int, bytes], msg_type: int, server_ip: str, bootfile: str) -> bytes:
    server = socket.inet_aton(server_ip)
    packet = struct.pack(
        "!BBBB4s2s2s4s4s4s4s16s64s128s",
        2,
        hdr["htype"],
        hdr["hlen"],
        0,
        hdr["xid"],
        b"\x00\x00",
        hdr["flags"],
        hdr["ciaddr"],
        b"\x00" * 4,  # yiaddr: proxyDHCP never assigns an address
        server,  # siaddr: TFTP server
        b"\x00" * 4,
        hdr["chaddr"],
        server_ip.encode(),
        bootfile.encode(),
    )
    body = MAGIC
    body += opt(53, bytes([msg_type]))
    body += opt(54, server)
    body += opt(60, b"PXEClient")
    if 97 in opts:  # echo the client UUID, as the PXE spec asks
        body += opt(97, opts[97])
    # Vendor options: PXE_DISCOVERY_CONTROL = 8 -> boot the file in this packet, no menu or discovery.
    body += opt(43, opt(6, b"\x08") + b"\xff")
    return packet + body + b"\xff"


def pxe_request(opts: dict[int, bytes]) -> tuple[int, str] | None:
    """(arch, bootfile) for a firmware PXE client; None for anything else, including iPXE itself."""
    if not opts.get(60, b"").startswith(b"PXEClient"):
        return None
    if opts.get(77, b"") == b"iPXE":  # iPXE runs its embedded script; it needs nothing from us
        return None
    arch = struct.unpack("!H", opts[93][:2])[0] if len(opts.get(93, b"")) >= 2 else 0
    bootfile = BOOTFILES.get(arch)
    return (arch, bootfile) if bootfile else None


def mac_of(hdr: dict) -> str:
    return ":".join(f"{b:02x}" for b in hdr["chaddr"][: hdr["hlen"] or 6])


def serve_dhcp(sock: socket.socket, port: int, server_ip: str, reply_to: tuple[str, int] | None):
    """port 67: answer DISCOVER with a broadcast OFFER. port 4011: answer REQUEST with a unicast ACK."""
    while True:
        data, addr = sock.recvfrom(4096)
        parsed = parse_dhcp(data)
        if not parsed:
            continue
        hdr, opts = parsed
        kind = opts.get(53, b"\x00")[0]
        want = pxe_request(opts)
        if not want:
            vendor = opts.get(60, b"").decode(errors="replace") or "-"
            log(f"DHCP {mac_of(hdr)} type {kind} vendor {vendor}: not a firmware PXE request, ignored")
            continue
        arch, bootfile = want
        if port == 67 and kind == DISCOVER:
            sock.sendto(build_reply(hdr, opts, OFFER, server_ip, bootfile), reply_to or ("255.255.255.255", 68))
            log(f"PXE {mac_of(hdr)} arch {arch}: offered {bootfile}")
        elif port == 4011 and kind == REQUEST:
            sock.sendto(build_reply(hdr, opts, ACK, server_ip, bootfile), reply_to or addr)
            log(f"PXE {mac_of(hdr)} arch {arch}: confirmed {bootfile} (port 4011)")


# --- TFTP (RFC 1350 + blksize/tsize options, RFC 2347-2349) ------------------------

RRQ, DATA, TACK, ERROR, OACK = 1, 3, 4, 5, 6


def tftp_error(sock: socket.socket, addr, code: int, msg: str):
    sock.sendto(struct.pack("!HH", ERROR, code) + msg.encode() + b"\0", addr)


def tftp_transfer(server_ip: str, root: Path, client, request: bytes):
    parts = request[2:].split(b"\0")
    name = parts[0].decode(errors="replace").replace("\\", "/").lstrip("/")
    options = {parts[i].decode().lower(): parts[i + 1].decode() for i in range(2, len(parts) - 1, 2) if parts[i]}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((server_ip, 0))
    sock.settimeout(1.0)
    try:
        path = root / Path(name).name  # serve files in root only, never subpaths
        if Path(name).name != name or not path.is_file():
            tftp_error(sock, client, 1, "file not found")
            log(f"TFTP {client[0]}: {name!r} not found")
            return
        data = path.read_bytes()
        blksize = 512
        oack = {}
        if "blksize" in options:
            blksize = max(8, min(int(options["blksize"]), 1468))
            oack["blksize"] = str(blksize)
        if "tsize" in options:
            oack["tsize"] = str(len(data))

        def exchange(packet: bytes, expect_block: int) -> bool:
            for _ in range(5):
                sock.sendto(packet, client)
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    try:
                        reply, addr = sock.recvfrom(1024)
                    except TimeoutError:
                        break
                    if addr != client or len(reply) < 4:
                        continue
                    op, block = struct.unpack("!HH", reply[:4])
                    if op == ERROR:
                        return False  # e.g. UEFI aborting after reading tsize; it asks again
                    if op == TACK and block == expect_block:
                        return True
            return False

        if oack:
            payload = b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in oack.items())
            if not exchange(struct.pack("!H", OACK) + payload, 0):
                return
        block = 0
        for offset in range(0, len(data) + 1, blksize):
            block += 1
            chunk = data[offset : offset + blksize]
            if not exchange(struct.pack("!HH", DATA, block & 0xFFFF) + chunk, block & 0xFFFF):
                log(f"TFTP {client[0]}: {name} aborted at {offset} bytes")
                return
            if len(chunk) < blksize:
                break
        log(f"TFTP {client[0]}: sent {name} ({len(data)} bytes, blksize {blksize})")
    finally:
        sock.close()


def serve_tftp(sock: socket.socket, server_ip: str, root: Path):
    while True:
        data, addr = sock.recvfrom(1024)
        if len(data) >= 4 and struct.unpack("!H", data[:2])[0] == RRQ:
            threading.Thread(target=tftp_transfer, args=(server_ip, root, addr, data), daemon=True).start()


# --- main ---------------------------------------------------------------------------


def udp_socket(ip: str, port: int, broadcast: bool = False) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if broadcast:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    # Windows delivers broadcasts to a socket bound to the interface address; Linux/macOS need 0.0.0.0.
    sock.bind((ip if os.name == "nt" or not broadcast else "0.0.0.0", port))
    return sock


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ip", required=True, help="this machine's LAN address (sent to PXE clients as the TFTP server)")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent, help="folder with ipxe.efi and undionly.kpxe")
    parser.add_argument("--port-offset", type=int, default=0, help=argparse.SUPPRESS)  # tests: unprivileged ports
    parser.add_argument("--reply-to", help=argparse.SUPPRESS)  # tests: send replies here instead of broadcast
    args = parser.parse_args()

    ipaddress.IPv4Address(args.ip)
    missing = [f for f in sorted(set(BOOTFILES.values())) if not (args.root / f).is_file()]
    if missing:
        sys.exit(f"missing in {args.root}: {', '.join(missing)}")
    reply_to = None
    if args.reply_to:
        host, port = args.reply_to.rsplit(":", 1)
        reply_to = (host, int(port))

    off = args.port_offset
    dhcp = udp_socket(args.ip, 67 + off, broadcast=True)
    proxy = udp_socket(args.ip, 4011 + off)
    tftp = udp_socket(args.ip, 69 + off)
    for target, a in ((serve_dhcp, (dhcp, 67, args.ip, reply_to)), (serve_dhcp, (proxy, 4011, args.ip, reply_to)), (serve_tftp, (tftp, args.ip, args.root))):
        threading.Thread(target=target, args=a, daemon=True).start()
    log(f"PXE responder on {args.ip}: proxyDHCP 67/4011, TFTP 69 from {args.root}. Ctrl-C to stop.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
