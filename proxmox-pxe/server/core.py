#!/usr/bin/env python3
"""Install logic for unattended Proxmox VE netboot: config, node state, answer files.

Standard library only, so build.sh can validate answers without the web app's
dependencies. The HTTP side lives in app.py.

  python3 server/core.py render-sample     sample answer file (build.sh validates it)
  python3 server/core.py list              known nodes
  python3 server/core.py forget hnhv03     next PXE boot of that PC reinstalls it
"""

import argparse
import hashlib
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTTP_DIR = ROOT / "out" / "http"
FIRST_BOOT = ROOT / "server" / "first-boot.sh"
ROOT_PASSWORD = ROOT / "secrets" / "root-password"
PASSWORD_HASH = ROOT / "secrets" / "root-password.hash"
AUTHORIZED_KEYS = ROOT / "secrets" / "authorized_keys"
AUTOMATION_KEY = ROOT / "secrets" / "automation_ed25519"
DEFAULT_STATE = ROOT / "state" / "hosts.json"
KNOWN_HOSTS = ROOT / "state" / "known_hosts"  # node host keys, for pushing password changes

INSTALLED = ("installed", "ready")


def read_env(path: Path) -> dict:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def load_cfg() -> dict:
    cfg = read_env(ROOT / "config.env")
    build_env = ROOT / "out" / "build.env"
    cfg.update(read_env(build_env) if build_env.exists() else {"SERVER_IP": "0.0.0.0"})
    return cfg


def base_url(cfg: dict) -> str:
    return f"http://{cfg['SERVER_IP']}:{cfg['HTTP_PORT']}"


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


# --- Root password ------------------------------------------------------------------
# SHA-512 crypt ($6$, glibc/libxcrypt), so the web app can hash without the build
# container: Python 3.13 dropped `crypt` and macOS LibreSSL lacks `openssl passwd -6`.

_B64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_ORDER = [(i, (i + 21) % 63, (i + 42) % 63) for i in (0, 22, 44, 3, 25, 47, 6, 28, 50, 9, 31, 53, 12, 34, 56, 15, 37, 59, 18, 40, 62)]


def sha512_crypt(password: str, salt: str | None = None, rounds: int = 5000) -> str:
    key, salt_b = password.encode(), (salt or secrets.token_urlsafe(12)[:16]).replace("_", ".").replace("-", "/").encode()[:16]
    sha = hashlib.sha512
    b = sha(key + salt_b + key).digest()
    a = sha(key + salt_b)
    a.update(b * (len(key) // 64) + b[: len(key) % 64])
    n = len(key)
    while n:
        a.update(b if n & 1 else key)
        n >>= 1
    c = a.digest()
    dp = sha(key * len(key)).digest()
    p = dp * (len(key) // 64) + dp[: len(key) % 64]
    ds = sha(salt_b * (16 + c[0])).digest()
    s = ds[: len(salt_b)]
    for r in range(rounds):
        h = sha(p if r & 1 else c)
        if r % 3:
            h.update(s)
        if r % 7:
            h.update(p)
        h.update(c if r & 1 else p)
        c = h.digest()
    out = []
    for i, j, k in _ORDER:
        w = c[i] << 16 | c[j] << 8 | c[k]
        out += [_B64[(w >> 6 * x) & 63] for x in range(4)]
    out += [_B64[(c[63] >> 6 * x) & 63] for x in range(2)]
    head = "$6$" if rounds == 5000 else f"$6$rounds={rounds}$"
    return f"{head}{salt_b.decode()}${''.join(out)}"


def check_password(password: str) -> str | None:
    """Why the password is unusable, or None."""
    if len(password) < 8:
        return "use at least 8 characters"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in password):
        return "no control characters or line breaks"
    if password != password.strip():
        return "no leading or trailing spaces"
    return None


def set_root_password(password: str) -> str:
    """Store the password and its hash for future installs; returns the hash."""
    hashed = sha512_crypt(password, rounds=200_000)
    for path, value in ((ROOT_PASSWORD, password), (PASSWORD_HASH, hashed)):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(value + "\n")
    return hashed


def render_answer(cfg: dict, hostname: str) -> str:
    base = base_url(cfg)
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


def render_first_boot(cfg: dict) -> str:
    return FIRST_BOOT.read_text().replace("@SERVER@", base_url(cfg))


# --- Request handling, shared by the web app ------------------------------------
# Each returns (response body, log line).


def boot_ipxe(state: State, mac: str) -> tuple[str, str]:
    name, host = state.lookup(mac) if mac else (None, None)
    if host and host.get("status") in INSTALLED:
        return (
            f"#!ipxe\necho {name} is already installed, booting from local disk\nsleep 3\nexit\n",
            f"{name} ({mac}) already installed, booting its USB stick",
        )
    return "#!ipxe\nchain pve/boot.ipxe\n", f"{mac or 'unknown MAC'} gets the installer"


def answer(state: State, cfg: dict, info: dict) -> tuple[str, str]:
    macs = [n["mac"] for n in info.get("network_interfaces", []) if n.get("mac")]
    if not macs:
        raise ValueError("request has no network interface MACs")
    name = state.assign(macs, info.get("dmi", {}))
    model = info.get("dmi", {}).get("system", {}).get("name", "?")
    return render_answer(cfg, name), f"answer: {name} for {', '.join(norm_mac(m) for m in macs)} ({model})"


def installed(state: State, info: dict) -> tuple[str, str]:
    hostname = info.get("fqdn", "").split(".")[0]
    mgmt = next((n for n in info.get("network-interfaces", []) if n.get("is-management")), {})
    ip = mgmt.get("address", "").split("/")[0]
    boot = next(
        (d["udev-properties"].get("DEVNAME") for d in info.get("disks", []) if d.get("is-bootdisk")), "?"
    )
    state.update(hostname, status="installed", ip=ip, mgmt_mac=norm_mac(mgmt.get("mac", "")), boot_disk=boot)
    return "ok\n", f"installed: {hostname} ip={ip} mac={norm_mac(mgmt.get('mac', ''))} boot_disk={boot}"


def first_boot_report(state: State, host: str, report: str) -> tuple[str, str]:
    lines = report.strip().splitlines()
    state.update(host, status="ready", first_boot=lines)
    return "ok\n", f"first boot done: {host}: " + "; ".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="node state file")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("render-sample", help="print a sample answer file (for validation)")
    sub.add_parser("list", help="show known nodes")
    forget = sub.add_parser("forget", help="drop a node so its next PXE boot reinstalls it")
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
            sys.exit(f"unknown node {args.name}")
        print(f"forgot {args.name}; its next PXE boot reinstalls it (wipes its USB stick)")


if __name__ == "__main__":
    main()
