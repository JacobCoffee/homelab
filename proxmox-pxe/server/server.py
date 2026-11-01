#!/usr/bin/env python3
"""Boot + answer server for unattended Proxmox VE installs over PXE.

Routes:
  GET  /boot.ipxe?mac=..     iPXE entry: install, or `exit` to local disk if already installed
  GET  /pve/<file>           installer kernel/initrd/ISO produced by build.sh
  POST /answer               auto-installer answer file (assigns hostname by MAC)
  POST /installed            post-installation webhook (records IP for DHCP reservation)
  GET  /first-boot           first-boot script run on each new node
  POST /first-boot-report    result report from the first-boot script
  GET  /status               JSON state
"""

import argparse
import json
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
HTTP_DIR = ROOT / "out" / "http"
FIRST_BOOT = ROOT / "server" / "first-boot.sh"
PASSWORD_HASH = ROOT / "secrets" / "root-password.hash"
AUTHORIZED_KEYS = ROOT / "secrets" / "authorized_keys"
DEFAULT_STATE = ROOT / "state" / "hosts.json"

INSTALLED = ("installed", "ready")


def read_env(path: Path) -> dict:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def norm_mac(mac: str) -> str:
    return mac.strip().lower().replace("-", ":")


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class State:
    def __init__(self, path: Path, prefix: str):
        self.path = path
        self.prefix = prefix
        self.lock = threading.Lock()
        self.hosts = json.loads(path.read_text())["hosts"] if path.exists() else {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"hosts": self.hosts}, indent=2, sort_keys=True) + "\n")
        tmp.replace(self.path)

    def _find(self, macs) -> str | None:
        wanted = {norm_mac(m) for m in macs}
        for name, host in self.hosts.items():
            if wanted & set(host["macs"]):
                return name
        return None

    def lookup(self, mac: str) -> tuple[str | None, dict | None]:
        with self.lock:
            name = self._find([mac])
            return name, (dict(self.hosts[name]) if name else None)

    def assign(self, macs, dmi) -> str:
        """Return the hostname for this machine, allocating <prefix>NN on first contact."""
        with self.lock:
            name = self._find(macs)
            if name is None:
                n = 1
                while f"{self.prefix}{n:02d}" in self.hosts:
                    n += 1
                name = f"{self.prefix}{n:02d}"
                self.hosts[name] = {"macs": []}
            host = self.hosts[name]
            host["macs"] = sorted(set(host["macs"]) | {norm_mac(m) for m in macs})
            host.update(status="installing", dmi=dmi, updated=now())
            self._save()
            return name

    def update(self, name: str, **fields):
        with self.lock:
            if name not in self.hosts:
                raise KeyError(name)
            self.hosts[name].update(fields, updated=now())
            self._save()

    def forget(self, name: str) -> bool:
        with self.lock:
            if self.hosts.pop(name, None) is None:
                return False
            self._save()
            return True

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.hosts))


def toml_str(value: str) -> str:
    # JSON string escaping is valid TOML basic-string escaping.
    return json.dumps(value)


def render_answer(cfg: dict, hostname: str) -> str:
    base = f"http://{cfg['SERVER_IP']}:{cfg['HTTP_PORT']}"
    return f"""\
[global]
keyboard = {toml_str(cfg['KEYBOARD'])}
country = {toml_str(cfg['COUNTRY'])}
fqdn = {toml_str(f"{hostname}.{cfg['DOMAIN']}")}
mailto = {toml_str(cfg['MAILTO'])}
timezone = {toml_str(cfg['TIMEZONE'])}
root-password-hashed = {toml_str(PASSWORD_HASH.read_text().strip())}
root-ssh-keys = [{", ".join(toml_str(k) for k in AUTHORIZED_KEYS.read_text().splitlines() if k.strip())}]

[network]
source = "from-dhcp"

# OS goes on the USB stick only: no swap, no data LV. The internal drive is
# turned into LVM-thin storage "vmdata" by the first-boot script.
[disk-setup]
filesystem = "ext4"
filter.ID_BUS = "usb"
lvm.swapsize = 0
lvm.maxvz = 0

[post-installation-webhook]
url = {toml_str(base + "/installed")}

[first-boot]
source = "from-url"
ordering = "fully-up"
url = {toml_str(base + "/first-boot")}
"""


def print_reservations(state: State):
    hosts = state.snapshot()
    rows = [(n, h) for n, h in sorted(hosts.items()) if h.get("status") in INSTALLED]
    if not rows:
        return
    print("\n  Reserve these in the My Spectrum app (Advanced settings > IP reservations):")
    for name, host in rows:
        print(f"    {name:<8} {host.get('ip', '?'):<16} {host.get('mgmt_mac', '?')}  [{host['status']}]")
    print(flush=True)


class Handler(SimpleHTTPRequestHandler):
    server_version = "pve-pxe/1"
    cfg: dict = {}
    state: State

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HTTP_DIR), **kwargs)

    def _send(self, body: str, status: int = 200, ctype: str = "text/plain; charset=utf-8"):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def copyfile(self, source, outputfile):
        # sendfile(): the installer image is ~1.7 GB per machine.
        self.connection.sendfile(source)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/boot.ipxe":
            self._boot_ipxe(parse_qs(url.query).get("mac", [""])[0])
        elif url.path.startswith("/pve/"):
            super().do_GET()
        elif url.path == "/first-boot":
            base = f"http://{self.cfg['SERVER_IP']}:{self.cfg['HTTP_PORT']}"
            self._send(FIRST_BOOT.read_text().replace("@SERVER@", base))
        elif url.path == "/status":
            self._send(json.dumps(self.state.snapshot(), indent=2), ctype="application/json")
        else:
            self._send("not found\n", 404)

    def do_HEAD(self):
        if urlsplit(self.path).path.startswith("/pve/"):
            super().do_HEAD()
        else:
            self._send("", 404)

    def do_POST(self):
        url = urlsplit(self.path)
        try:
            if url.path == "/answer":
                self._answer(json.loads(self._body()))
            elif url.path == "/installed":
                self._installed(json.loads(self._body()))
            elif url.path == "/first-boot-report":
                host = parse_qs(url.query).get("host", [""])[0]
                self._first_boot_report(host, self._body().decode(errors="replace"))
            else:
                self._send("not found\n", 404)
        except Exception as exc:  # report to the client, keep serving others
            self.log_error("%s failed: %r", url.path, exc)
            self._send(f"error: {exc}\n", 500)

    def _boot_ipxe(self, mac: str):
        name, host = self.state.lookup(mac) if mac else (None, None)
        if host and host.get("status") in INSTALLED:
            self.log_message("%s (%s) already installed -> local boot", name, mac)
            self._send(f"#!ipxe\necho {name} is already installed, booting from local disk\nsleep 3\nexit\n")
        else:
            self.log_message("%s -> installer", mac or "unknown MAC")
            self._send("#!ipxe\nchain pve/boot.ipxe\n")

    def _answer(self, info: dict):
        macs = [n["mac"] for n in info.get("network_interfaces", []) if n.get("mac")]
        if not macs:
            raise ValueError("request has no network interface MACs")
        name = self.state.assign(macs, info.get("dmi", {}))
        dmi = info.get("dmi", {}).get("system", {})
        print(f"[{now()}] answer: {name} <- {', '.join(macs)} ({dmi.get('name', '?')})", flush=True)
        self._send(render_answer(self.cfg, name), ctype="application/toml")

    def _installed(self, info: dict):
        hostname = info.get("fqdn", "").split(".")[0]
        mgmt = next((n for n in info.get("network-interfaces", []) if n.get("is-management")), {})
        ip = mgmt.get("address", "").split("/")[0]
        boot = next((d["udev-properties"].get("DEVNAME") for d in info.get("disks", []) if d.get("is-bootdisk")), "?")
        self.state.update(hostname, status="installed", ip=ip, mgmt_mac=norm_mac(mgmt.get("mac", "")), boot_disk=boot)
        print(f"[{now()}] installed: {hostname} ip={ip} mac={mgmt.get('mac')} boot_disk={boot}", flush=True)
        self._send("ok\n")
        print_reservations(self.state)

    def _first_boot_report(self, host: str, report: str):
        self.state.update(host, status="ready", first_boot=report.strip().splitlines())
        print(f"[{now()}] first boot done: {host}\n" + "".join(f"    {l}\n" for l in report.strip().splitlines()), flush=True)
        self._send("ok\n")
        print_reservations(self.state)


def load_cfg() -> dict:
    cfg = read_env(ROOT / "config.env")
    build_env = ROOT / "out" / "build.env"
    cfg.update(read_env(build_env) if build_env.exists() else {"SERVER_IP": "0.0.0.0"})
    return cfg


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="host state file")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the server")
    sub.add_parser("render-sample", help="print a sample answer file (for validation)")
    sub.add_parser("list", help="show known hosts")
    forget = sub.add_parser("forget", help="drop a host so its next PXE boot reinstalls it")
    forget.add_argument("name")
    args = parser.parse_args()

    cfg = load_cfg()
    state = State(args.state, cfg["HOST_PREFIX"])

    if args.cmd == "render-sample":
        sys.stdout.write(render_answer(cfg, f"{cfg['HOST_PREFIX']}00"))
    elif args.cmd == "list":
        for name, host in sorted(state.snapshot().items()):
            print(f"{name:<8} {host.get('status', '?'):<11} {host.get('ip', '-'):<16} {', '.join(host['macs'])}")
    elif args.cmd == "forget":
        if not state.forget(args.name):
            sys.exit(f"unknown host {args.name}")
        print(f"forgot {args.name}; its next PXE boot reinstalls it (wipes its USB stick)")
    else:
        Handler.cfg = cfg
        Handler.state = state
        server = ThreadingHTTPServer(("0.0.0.0", int(cfg["HTTP_PORT"])), Handler)
        print(f"Serving on http://{cfg['SERVER_IP']}:{cfg['HTTP_PORT']} (state: {args.state})", flush=True)
        print_reservations(state)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
