#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6"]
# ///
"""Copy the lab's web logins from the Ansible vault into Bitwarden, folder "Homelab": one login
item per app, with its URLs so the browser extension autofills it.

    bw login                                # once
    set -x BW_SESSION (bw unlock --raw)     # fish; each new shell
    scripts/bw-sync.py

Items are matched by name inside the folder and only rewritten when something differs, so a
re-run is a no-op. The vault stays the source of truth: change a password there (and apply it, see
scripts/lab-logins.py), then re-run this. Secrets reach bw on stdin, never on argv.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault import vault_get  # noqa: E402

FOLDER = "Homelab"
NOTE = "Managed by scripts/bw-sync.py from the homelab Ansible vault; edits here get overwritten."

# name: (username vault key or literal, password vault key, URLs)
SHARED = ("vault_lab_admin_username", "vault_lab_admin_password")
ITEMS: dict[str, tuple[str, str, list[str]]] = {
    "Homelab: Sonarr": (*SHARED, ["https://sonarr.z7x.org"]),
    "Homelab: Radarr": (*SHARED, ["https://radarr.z7x.org"]),
    "Homelab: Lidarr": (*SHARED, ["https://lidarr.z7x.org"]),
    "Homelab: Prowlarr": (*SHARED, ["https://prowlarr.z7x.org"]),
    "Homelab: Bazarr": (*SHARED, ["https://bazarr.z7x.org"]),
    "Homelab: SABnzbd": (*SHARED, ["https://sabnzbd.z7x.org"]),
    "Homelab: Bindery": (*SHARED, ["https://bindery.z7x.org"]),
    "Homelab: Cleanuparr": (*SHARED, ["https://cleanuparr.z7x.org"]),
    "Homelab: qBittorrent": (*SHARED, ["https://qbittorrent.z7x.org"]),
    "Homelab: Unpackerr": (*SHARED, ["https://unpackerr.z7x.org"]),
    "Homelab: Uptime Kuma": (*SHARED, ["https://kuma.z7x.org"]),
    "Homelab: Grafana": (*SHARED, ["https://grafana.z7x.org"]),
    "Homelab: AdGuard Home": ("admin", "vault_adguard_password",
                              ["http://10.10.10.53", "https://dns.z7x.org:8443"]),
    "Homelab: OPNsense": ("root", "vault_opnsense_root_password",
                          ["https://10.10.10.1", "https://hnfw01.internal.z7x.org", "https://hnfw02.internal.z7x.org"]),
}


def bw(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(["bw", *args], input=stdin, capture_output=True, text=True, check=True).stdout


def encoded(obj: dict) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


def main() -> int:
    if not os.environ.get("BW_SESSION"):
        print("Unlock first (fish): set -x BW_SESSION (bw unlock --raw)", file=sys.stderr)
        return 2
    v = vault_get()
    bw("sync")
    folders = json.loads(bw("list", "folders", "--search", FOLDER))
    folder = next((f for f in folders if f["name"] == FOLDER), None)
    if folder is None:
        folder = json.loads(bw("create", "folder", stdin=encoded({"name": FOLDER})))
    existing = {i["name"]: i for i in json.loads(bw("list", "items", "--folderid", folder["id"]))}

    for name, (user, pw_key, urls) in ITEMS.items():
        username = v.get(user, user)
        want_login = {"username": username, "password": v[pw_key],
                      "uris": [{"uri": u, "match": None} for u in urls]}
        item = existing.get(name)
        if item is None:
            template = json.loads(bw("get", "template", "item"))
            template.update(name=name, folderId=folder["id"], notes=NOTE, type=1, login=want_login)
            bw("create", "item", stdin=encoded(template))
            print(f"{name:24} created")
            continue
        login = item.get("login") or {}
        same = (login.get("username") == username and login.get("password") == v[pw_key]
                and [u.get("uri") for u in login.get("uris") or []] == urls and item.get("notes") == NOTE)
        if same:
            print(f"{name:24} ok")
            continue
        item.update(notes=NOTE, login={**login, **want_login})
        bw("edit", "item", item["id"], stdin=encoded(item))
        print(f"{name:24} updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
