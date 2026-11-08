#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["requests>=2.32", "pyyaml>=6"]
# ///
"""Keep Pocket ID (https://id.z7x.org) in step with this file: the groups, the owner account in
lab-admins, one OIDC client per app (open to lab-admins unless its entry names other groups), and
the Homelab branding (background, logos, favicon, client logos from
kubernetes/apps/auth/pocket-id/branding/).

    scripts/pocket-id.py            # apply
    scripts/pocket-id.py --check    # only report what would change
    scripts/pocket-id.py --link     # also print a fresh one-time sign-in link for the owner
    scripts/pocket-id.py --add-user leah --groups media,live-tv,books
                                    # also create/update a friend and print their sign-in link

Talks to the REST API with STATIC_API_KEY (vault_pocket_id_static_api_key). Client secrets are
generated into the vault as vault_pocket_id_<client>_secret and handed to Pocket ID, so the vault
is the source of truth: deleting a vault key rotates that client's secret on the next run. After
a rotation, re-run whatever reads the key (the sops Secrets, ansible --tags pocket_id, ...).

While the owner has no passkey yet, it prints a one-time link (30 minutes) that signs in once so
a passkey can be added under My Account.

Friends: only --add-user makes accounts (ALLOW_USER_SIGNUPS disabled), and nobody can rename
their own (ALLOW_OWN_ACCOUNT_EDIT false). That matters because Jellyfin and Audiobookshelf link a
Pocket ID login to their existing account with the same username: give a friend the username they
already have there (their Wizarr account) and both keep working. --groups replaces the friend's
groups; the link (7 days, one use) signs them in once so they can add a passkey, and running
--add-user again for them prints a new one (new phone, lost passkey).
"""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault import ROOT, vault_get, vault_set  # noqa: E402

BASE = "https://id.z7x.org"
# sops files that carry a client secret to its consumer: path -> {stringData key: vault key}.
SOPS = {
    "kubernetes/apps/network/lab-sso/app/secret-media.sops.yaml":
        {"client-secret": "vault_pocket_id_lab_gateway_secret"},
    "kubernetes/apps/network/lab-sso/app/secret-status.sops.yaml":
        {"client-secret": "vault_pocket_id_lab_gateway_secret"},
    "kubernetes/apps/media/jellyfin/app/secret.sops.yaml":
        {"OIDC_CLIENT_SECRET": "vault_pocket_id_jellyfin_secret"},
    "kubernetes/apps/observability/kube-prometheus-stack/app/secret-grafana-oidc.sops.yaml":
        {"client-secret": "vault_pocket_id_grafana_secret"},
    "kubernetes/apps/flux-system/flux-web/app/secret.sops.yaml":
        {"client-secret": "vault_pocket_id_flux_secret"},
    "kubernetes/apps/media/audiobookshelf/app/secret.sops.yaml":
        {"OIDC_CLIENT_SECRET": "vault_pocket_id_audiobookshelf_secret"},
    "kubernetes/apps/media/bindery/app/secret.sops.yaml":
        {"OIDC_CLIENT_SECRET": "vault_pocket_id_bindery_secret"},
}
BRANDING = ROOT / "kubernetes/apps/auth/pocket-id/branding"
# application image endpoint -> (file in BRANDING, query); built by BRANDING/build.py.
IMAGES = {
    "background": ("background.svg", {}),
    "logo-light": ("logo-light.svg", {"light": "true"}),
    "logo-dark": ("logo-dark.svg", {"light": "false"}),
    "favicon": ("favicon.svg", {}),
}
ADMINS = "lab-admins"
# What a friend can open (each client below lists the groups it lets in):
#   media    Jellyfin (every library) and the Seerr tile; Seerr itself signs in with Jellyfin
#   live-tv  Live TV in Jellyfin, on top of media
#   books    Bindery (as a requester) and Audiobookshelf
FRIEND_GROUPS = {
    "media": "Movies & TV",
    "live-tv": "Live TV",
    "books": "Books",
}
GROUPS = [{"name": ADMINS, "friendlyName": "Lab admins"}] + [
    {"name": name, "friendlyName": friendly} for name, friendly in FRIEND_GROUPS.items()]
FRIEND_LINK_TTL = "168h"
# Created with these values; afterwards only isAdmin and the group are enforced, so profile edits
# made under My Account (username, names) stick. Found by email.
OWNER = {
    "username": "jacob",
    "email": "jacob@z7x.org",
    "emailVerified": True,
    "firstName": "Jacob",
    "lastName": "Coffee",
    "displayName": "Jacob Coffee",
    "isAdmin": True,
}
# id -> client settings. Callback URLs must match exactly (single-label * allowed).
CLIENTS: dict[str, dict] = {
    # Envoy Gateway's OIDC filter in front of the apps without their own SSO (SecurityPolicy
    # lab-sso in media/status); Envoy's callback is /oauth2/callback on each app's own host.
    "lab-gateway": {
        "name": "Lab apps",
        "callbackURLs": ["https://*.z7x.org/oauth2/callback"],
        "launchURL": "https://start.z7x.org",
    },
    "grafana": {
        "name": "Grafana",
        "callbackURLs": ["https://grafana.z7x.org/login/generic_oauth"],
        "launchURL": "https://grafana.z7x.org",
        "pkceEnabled": True,
    },
    "proxmox": {
        "name": "Proxmox",
        # *.internal.z7x.org for the Tailscale/mDNS hostnames; the bare LAN IPs too, since the
        # Proxmox UI's OIDC callback is whatever origin the browser used to reach it (hnhv01/02).
        "callbackURLs": [
            "https://*.internal.z7x.org:8006",
            "https://192.168.1.220:8006",
            "https://192.168.1.192:8006",
        ],
        "launchURL": "https://hnhv01.internal.z7x.org:8006",
    },
    # SSO plugin (apps/media/jellyfin/app/install-sso.py): lab-admins are admins, media signs in,
    # live-tv adds Live TV. A login lands in the Jellyfin account with the same username.
    "jellyfin": {
        "name": "Jellyfin",
        "callbackURLs": ["https://media.z7x.org/sso/OID/redirect/pocket-id"],
        "launchURL": "https://media.z7x.org",
        "groups": [ADMINS, "media"],
    },
    # Only a tile on My Apps: Seerr has no OpenID Connect, so people sign in there with Jellyfin
    # (Quick Connect for Pocket ID users, who have no Jellyfin password). The callback is pinned so
    # Pocket ID never adopts one, and no secret is set or stored.
    "seerr": {
        "name": "Requests",
        "callbackURLs": ["https://requests.z7x.org/"],
        "launchURL": "https://requests.z7x.org",
        "groups": [ADMINS, "media"],
        "secret": False,
    },
    # Audiobookshelf serves under /audiobookshelf; /mobile-redirect is for its apps' SSO. Its
    # setup.py links a login to the account with the same username and creates one otherwise.
    "audiobookshelf": {
        "name": "Audiobookshelf",
        "callbackURLs": [
            "https://books.z7x.org/audiobookshelf/auth/openid/callback",
            "https://books.z7x.org/audiobookshelf/auth/openid/mobile-redirect",
        ],
        "launchURL": "https://books.z7x.org",
        "pkceEnabled": True,
        "groups": [ADMINS, "books"],
    },
    # Bindery (bindery.z7x.org): books signs in as a requester, lab-admins as admin
    # (BINDERY_OIDC_ADMIN_GROUP in its HelmRelease). Its setup.py registers the provider with id
    # "pocket-id", which is the last path segment here.
    "bindery": {
        "name": "Book requests",
        "callbackURLs": ["https://bindery.z7x.org/api/v1/auth/oidc/pocket-id/callback"],
        "launchURL": "https://bindery.z7x.org",
        "pkceEnabled": True,
        "groups": [ADMINS, "books"],
    },
    "flux": {
        "name": "Flux",
        "callbackURLs": ["https://flux.z7x.org/oauth2/callback"],
        "launchURL": "https://flux.z7x.org",
        "pkceEnabled": True,
    },
    "opnsense": {
        "name": "OPNsense",
        # No config sync between the two firewalls (ansible/roles/opnsense): each one configures
        # its own os-sso OIDC provider with its own Base URL (its INT-zone address, never the
        # shared CARP VIP -- see opnsense role defaults), so the callback this client needs to
        # accept is the cross product of "which firewall" and "VIP or that firewall's own address
        # or hostname" the browser used to reach it.
        "callbackURLs": [
            "https://10.10.10.1/api/sso/oidc/callback",
            "https://10.10.10.2/api/sso/oidc/callback",
            "https://10.10.10.3/api/sso/oidc/callback",
            "https://hnfw01.int.z7x.org/api/sso/oidc/callback",
            "https://hnfw02.int.z7x.org/api/sso/oidc/callback",
        ],
        "launchURL": "https://10.10.10.1",
        "pkceEnabled": True,
    },
}
COMPARED = ("name", "callbackURLs", "launchURL", "pkceEnabled", "isPublic", "isGroupRestricted")


class Api:
    def __init__(self, key: str, check: bool):
        self.s = requests.Session()
        self.s.headers["X-API-Key"] = key
        self.check = check
        self.changes: list[str] = []

    def req(self, method: str, path: str, **kw) -> dict | list | None:
        r = self.s.request(method, f"{BASE}/api{path}", timeout=20, **kw)
        if r.status_code >= 400:
            sys.exit(f"{method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None

    def get_bytes(self, path: str, params: dict) -> bytes | None:
        """The raw body, or None when Pocket ID has no such image."""
        r = self.s.get(f"{BASE}/api{path}", params=params, timeout=20)
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            sys.exit(f"GET {path}: {r.status_code} {r.text[:300]}")
        return r.content

    def get_all(self, path: str) -> list[dict]:
        return self.req("GET", path, params={"pagination[limit]": 100})["data"]

    def change(self, what: str, method: str, path: str, **kw) -> dict | list | None:
        self.changes.append(what)
        print(f"{'would ' if self.check else ''}{what}")
        return None if self.check else self.req(method, path, **kw)


def ensure_groups(api: Api) -> dict[str, str]:
    """Group name -> id (missing while --check would create it)."""
    have = {g["name"]: g["id"] for g in api.get_all("/user-groups")}
    for group in GROUPS:
        if group["name"] not in have:
            made = api.change(f"create group {group['name']}", "POST", "/user-groups", json=group)
            if made:
                have[group["name"]] = made["id"]
    return have


def ensure_owner(api: Api, gid: str | None) -> str | None:
    user = next((u for u in api.get_all("/users") if u["email"] == OWNER["email"]), None)
    if user is None:
        made = api.change(f"create user {OWNER['username']}", "POST", "/users",
                          json={**OWNER, "userGroupIds": [gid] if gid else []})
        return made["id"] if made else None
    if not user["isAdmin"]:
        api.change(f"make {user['username']} admin", "PUT", f"/users/{user['id']}",
                   json={**user, "isAdmin": True})
    groups = {g["id"] for g in api.req("GET", f"/users/{user['id']}/groups")}
    if gid and gid not in groups:
        api.change(f"add {OWNER['username']} to {ADMINS}", "PUT",
                   f"/users/{user['id']}/user-groups", json={"userGroupIds": sorted(groups | {gid})})
    return user["id"]


def ensure_client(api: Api, cid: str, spec: dict, gids: dict[str, str], vault: dict) -> None:
    want = {"name": spec["name"], "callbackURLs": spec["callbackURLs"],
            "launchURL": spec.get("launchURL"), "pkceEnabled": spec.get("pkceEnabled", False),
            "isPublic": False, "isGroupRestricted": True, "skipConsent": True}
    have = next((c for c in api.get_all("/oidc/clients") if c["id"] == cid), None)
    if have is None:
        api.change(f"create client {cid}", "POST", "/oidc/clients", json={**want, "id": cid})
    elif any(have.get(k) != want[k] for k in COMPARED):
        api.change(f"update client {cid}", "PUT", f"/oidc/clients/{cid}", json={**have, **want})

    if api.check and have is None:
        return
    names = spec.get("groups", [ADMINS])
    wanted = {gids[n] for n in names if n in gids}
    full = api.req("GET", f"/oidc/clients/{cid}")
    allowed = {g["id"] for g in full.get("allowedUserGroups", [])}
    if wanted and allowed != wanted:
        api.change(f"limit client {cid} to {', '.join(names)}", "PUT",
                   f"/oidc/clients/{cid}/allowed-user-groups", json={"userGroupIds": sorted(wanted)})

    if not spec.get("secret", True):
        return
    key = f"vault_pocket_id_{cid.replace('-', '_')}_secret"
    secret = vault.get(key)
    if not secret:
        secret = secrets.token_urlsafe(32)
        if api.check:
            print(f"would generate {key}")
        else:
            vault_set({key: secret})
            vault[key] = secret
    current = api.req("GET", f"/oidc/clients/{cid}/secrets") or []
    if isinstance(current, dict):
        current = current.get("data", [])
    if any(s["isActive"] and s["prefix"] and secret.startswith(s["prefix"]) for s in current):
        return
    for s in current:
        api.change(f"drop old secret of {cid}", "DELETE", f"/oidc/clients/{cid}/secrets/{s['id']}")
    api.change(f"set secret of {cid} from {key}", "POST", f"/oidc/clients/{cid}/secrets",
               json={"secret": secret})


def ensure_image(api: Api, what: str, method: str, path: str, query: dict, file: Path) -> None:
    """Upload file unless Pocket ID already serves exactly these bytes (it stores SVGs as sent)."""
    data = file.read_bytes()
    if api.get_bytes(path, {**query, "default": "false"}) == data:
        return
    api.change(f"upload {what} from {file.relative_to(ROOT)}", method, path, params=query,
               files={"file": (file.name, data, "image/svg+xml")})


def ensure_branding(api: Api) -> None:
    for name, (fname, query) in IMAGES.items():
        endpoint = "logo" if name.startswith("logo") else name
        ensure_image(api, name, "PUT", f"/application-images/{endpoint}", query, BRANDING / fname)
    have = {c["id"] for c in api.get_all("/oidc/clients")}
    for cid in CLIENTS:
        logo = BRANDING / "clients" / f"{cid}.svg"
        if logo.exists() and cid in have:
            ensure_image(api, f"logo of {cid}", "POST", f"/oidc/clients/{cid}/logo", {"light": "true"}, logo)


def sync_sops(api: Api, path: Path, key: str, value: str) -> None:
    """Set stringData[key] unless it already holds value; the value never goes on argv."""
    index = f'["stringData"]["{key}"]'

    def sops(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["mise", "-C", str(ROOT), "exec", "--", "sops", *args],
                              input=stdin, capture_output=True, text=True)
    if not path.exists():
        return
    if sops("-d", "--extract", index, str(path)).stdout == value:
        return
    rel = path.relative_to(ROOT)
    api.changes.append(f"{rel}:{key}")
    print(f"{'would update' if api.check else 'update'} {rel} {key}; commit it")
    if not api.check:
        # `sops set` takes the file before the index (`-d --extract` takes it last).
        sops("set", "--value-stdin", str(path), index, stdin=json.dumps(value)).check_returncode()


def ensure_friend(api: Api, username: str, groups: list[str], gids: dict[str, str]) -> str | None:
    """Create the friend if missing and set their groups to exactly these; their user id."""
    unknown = [g for g in groups if g not in FRIEND_GROUPS]
    if unknown:
        sys.exit(f"unknown group(s) {', '.join(unknown)}; friends can have {', '.join(FRIEND_GROUPS)}")
    want = sorted(gids[g] for g in groups if g in gids)
    user = next((u for u in api.get_all("/users") if u["username"].lower() == username.lower()), None)
    if user is None:
        made = api.change(f"create user {username} in {', '.join(groups)}", "POST", "/users", json={
            "username": username, "firstName": username, "displayName": username,
            "isAdmin": False, "userGroupIds": want})
        return made["id"] if made else None
    if user["isAdmin"] or gids.get(ADMINS) in {g["id"] for g in api.req("GET", f"/users/{user['id']}/groups")}:
        sys.exit(f"{user['username']} is an admin; --add-user only manages friends")
    have = sorted(g["id"] for g in api.req("GET", f"/users/{user['id']}/groups"))
    if have != want:
        api.change(f"set {user['username']}'s groups to {', '.join(groups) or 'none'}", "PUT",
                   f"/users/{user['id']}/user-groups", json={"userGroupIds": want})
    return user["id"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--link", action="store_true")
    ap.add_argument("--add-user", metavar="USERNAME",
                    help="their username in Jellyfin/Audiobookshelf, if they have one")
    ap.add_argument("--groups", default="", help=f"comma-separated, from: {', '.join(FRIEND_GROUPS)}")
    args = ap.parse_args()
    if args.groups and not args.add_user:
        ap.error("--groups goes with --add-user")

    vault = vault_get()
    api = Api(vault["vault_pocket_id_static_api_key"], args.check)
    gids = ensure_groups(api)
    uid = ensure_owner(api, gids.get(ADMINS))
    for cid, spec in CLIENTS.items():
        ensure_client(api, cid, spec, gids, vault)
    ensure_branding(api)
    for rel, keys in SOPS.items():
        for key, vkey in keys.items():
            sync_sops(api, ROOT / rel, key, vault[vkey])

    if uid and not args.check:
        passkeys = api.req("GET", f"/users/{uid}/webauthn-credentials") or []
        if args.link or not passkeys:
            # 30m, not 15m: Pocket ID makes <=15m codes 6 characters, but its code page only
            # submits 12-character codes while email login codes are disabled.
            token = api.req("POST", f"/users/{uid}/one-time-access-token", json={"ttl": "30m"})["token"]
            print(f"{'no passkey yet; ' if not passkeys else ''}sign in once (30 min): {BASE}/lc/{token}")
    if args.add_user:
        groups = [g.strip() for g in args.groups.split(",") if g.strip()]
        fid = ensure_friend(api, args.add_user, groups, gids)
        if fid and not args.check:
            token = api.req("POST", f"/users/{fid}/one-time-access-token",
                            json={"ttl": FRIEND_LINK_TTL})["token"]
            print(f"{args.add_user}: sign in once and add a passkey (7 days): {BASE}/lc/{token}")
    print("ok, no changes" if not api.changes else f"{len(api.changes)} change(s)")


if __name__ == "__main__":
    main()
