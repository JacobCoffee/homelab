"""Codify Audiobookshelf: admin account, libraries, backups, Pocket ID sign-in, and the Bindery link.

Everything goes through Audiobookshelf's HTTP API (server/routers/ApiRouter.js, v2.37):

1. First run: POST /init creates the root user "jacob" with ABS_ROOT_PASSWORD (only possible while
   no root exists). Every run then signs in with POST /login and uses the returned access token.
2. Libraries "Audiobooks" (/media/library/audiobooks) and "Ebooks" (/media/library/books), the two
   roots Bindery files into. Audiobookshelf's folder watcher can't see files another NFS client
   writes, so it's off and each library is rescanned every 30 minutes instead.
3. Server settings: its own nightly backup (03:00, keep 3) into /metadata/backups, a consistent
   copy of the SQLite database for the 04:00 VolSync run to pick up.
4. Bindery (Settings > Audiobookshelf): base URL and the Audiobooks library, so Bindery triggers a
   scan right after each audiobook import. Bindery needs an API key, which Audiobookshelf shows
   only once, so one is minted (and older "bindery" keys deleted) only when Bindery has none or
   its saved one stopped working.
5. Sign in with Pocket ID (OpenID Connect, client "audiobookshelf" in scripts/pocket-id.py) next to
   the password form. Endpoints come from Pocket ID's discovery document. Existing accounts are
   matched by username (Pocket ID's preferred_username "jacob" is the root user) and nobody is
   auto-registered, so friends keep their Wizarr-made password accounts. Audiobookshelf caches
   its OIDC client, so when these settings change while OIDC is on, it's switched off and on again.

Idempotent: re-running changes nothing that already matches.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

SVC = "media.svc.cluster.local"
ABS = os.environ.get("ABS_URL", f"http://audiobookshelf.{SVC}:13378").rstrip("/")
BINDERY = os.environ.get("BINDERY_URL", f"http://bindery.{SVC}:8787").rstrip("/")
ROOT_USERNAME = "jacob"
ROOT_PASSWORD = os.environ["ABS_ROOT_PASSWORD"]
BINDERY_KEY_NAME = "bindery"
BINDERY_API_KEY = os.environ["BINDERY_API_KEY"]

SCAN_CRON = "*/30 * * * *"
LIBRARIES = [
    {
        "name": "Audiobooks", "path": "/media/library/audiobooks", "icon": "audiobookshelf",
        "provider": "audible", "settings": {"audiobooksOnly": True},
    },
    {
        "name": "Ebooks", "path": "/media/library/books", "icon": "book-1",
        "provider": "google", "settings": {},
    },
]
SERVER_SETTINGS = {"backupSchedule": "0 3 * * *", "backupsToKeep": 3, "maxBackupSize": 1}

ISSUER = "https://id.z7x.org"
OIDC_CLIENT_ID = "audiobookshelf"
OIDC_CLIENT_SECRET = os.environ["OIDC_CLIENT_SECRET"]
# Audiobookshelf serves under its default ROUTER_BASE_PATH; the callbacks registered in Pocket ID
# are https://books.z7x.org/audiobookshelf/auth/openid/{callback,mobile-redirect}.
BASE_PATH = "/audiobookshelf"

TIMEOUT = 30


def call(method: str, url: str, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw.strip().startswith((b"{", b"[")) else raw.decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


class Abs:
    def __init__(self) -> None:
        self.token = ""

    def api(self, method: str, path: str, body: dict | None = None):
        status, data = call(method, f"{ABS}{path}", body, {"Authorization": f"Bearer {self.token}"})
        if status >= 300:
            sys.exit(f"Audiobookshelf: {method} {path} -> {status} {str(data)[:300]}")
        return data


def bindery(method: str, path: str, body: dict | None = None, ok: bool = False):
    # Bindery's instance API key (the bindery Secret): Bindery asks everyone else to sign in.
    status, data = call(method, f"{BINDERY}/api/v1{path}", body, {"X-Api-Key": BINDERY_API_KEY})
    if status >= 300 and not ok:
        sys.exit(f"Bindery: {method} {path} -> {status} {str(data)[:300]}")
    return status, data


def wait_for(url: str, name: str) -> None:
    for _ in range(90):
        try:
            if call("GET", url)[0] < 500:
                return
        except (urllib.error.URLError, OSError) as e:
            print(f"waiting for {name}: {e}")
        time.sleep(10)
    sys.exit(f"{name} never became reachable")


def sign_in(abs_: Abs) -> dict:
    _status, info = call("GET", f"{ABS}/status")
    if not info.get("isInit"):
        status, body = call("POST", f"{ABS}/init",
                            {"newRoot": {"username": ROOT_USERNAME, "password": ROOT_PASSWORD}})
        if status != 200:
            sys.exit(f"Audiobookshelf: /init -> {status} {body}")
        print(f"Audiobookshelf: root user {ROOT_USERNAME!r} created")
    status, body = call("POST", f"{ABS}/login", {"username": ROOT_USERNAME, "password": ROOT_PASSWORD})
    if status != 200:
        sys.exit(f"Audiobookshelf: login as {ROOT_USERNAME!r} -> {status} (password out of step "
                 "with vault_audiobookshelf_password?)")
    abs_.token = body["user"]["accessToken"]
    return body["user"]


def ensure_server_settings(abs_: Abs) -> None:
    current = abs_.api("POST", "/api/authorize")["serverSettings"]
    changed = {k: v for k, v in SERVER_SETTINGS.items() if current.get(k) != v}
    if changed:
        abs_.api("PATCH", "/api/settings", changed)
        print(f"Audiobookshelf: server settings {', '.join(changed)}")


def ensure_libraries(abs_: Abs) -> dict[str, str]:
    """name -> library id."""
    existing = {lib["name"]: lib for lib in abs_.api("GET", "/api/libraries")["libraries"]}
    ids = {}
    for want in LIBRARIES:
        settings = {"disableWatcher": True, "autoScanCronExpression": SCAN_CRON, **want["settings"]}
        lib = existing.get(want["name"])
        if lib is None:
            lib = abs_.api("POST", "/api/libraries", {
                "name": want["name"], "folders": [{"fullPath": want["path"]}], "mediaType": "book",
                "icon": want["icon"], "provider": want["provider"], "settings": settings})
            print(f"Audiobookshelf: library {want['name']} added")
            abs_.api("POST", f"/api/libraries/{lib['id']}/scan")
        else:
            patch = {}
            if [f["fullPath"] for f in lib["folders"]] != [want["path"]]:
                patch["folders"] = [{"fullPath": want["path"]}]
            if lib["icon"] != want["icon"]:
                patch["icon"] = want["icon"]
            if lib["provider"] != want["provider"]:
                patch["provider"] = want["provider"]
            drift = {k: v for k, v in settings.items() if lib["settings"].get(k) != v}
            if drift:
                patch["settings"] = drift
            if patch:
                abs_.api("PATCH", f"/api/libraries/{lib['id']}", patch)
                print(f"Audiobookshelf: library {want['name']}: {', '.join(patch)}")
        ids[want["name"]] = lib["id"]
    return ids


def ensure_bindery_link(abs_: Abs, root_id: str, audiobooks_id: str) -> None:
    _status, cfg = bindery("GET", "/abs/config")
    desired = {"baseUrl": ABS, "enabled": True, "libraryIds": [audiobooks_id],
               "libraryId": audiobooks_id}
    patch = {k: v for k, v in desired.items() if cfg.get(k) != v}
    if patch:
        bindery("PUT", "/abs/config", patch)
        print(f"Bindery: Audiobookshelf {', '.join(patch)}")
    if cfg.get("apiKeyConfigured") and bindery("POST", "/abs/test", {}, ok=True)[0] == 200:
        return
    for key in abs_.api("GET", "/api/api-keys")["apiKeys"]:
        if key["name"] == BINDERY_KEY_NAME:
            abs_.api("DELETE", f"/api/api-keys/{key['id']}")
    created = abs_.api("POST", "/api/api-keys",
                       {"name": BINDERY_KEY_NAME, "userId": root_id, "isActive": True})
    bindery("PUT", "/abs/config", {"apiKey": created["apiKey"]["apiKey"]})
    status, body = bindery("POST", "/abs/test", {}, ok=True)
    if status != 200:
        sys.exit(f"Bindery: Audiobookshelf test failed with the new key: {str(body)[:300]}")
    print("Bindery: Audiobookshelf API key set and tested")


def ensure_openid(abs_: Abs) -> None:
    status, disco = call("GET", f"{ISSUER}/.well-known/openid-configuration")
    if status != 200:
        sys.exit(f"Pocket ID discovery -> {status}")
    desired = {
        "authOpenIDIssuerURL": disco["issuer"],
        "authOpenIDAuthorizationURL": disco["authorization_endpoint"],
        "authOpenIDTokenURL": disco["token_endpoint"],
        "authOpenIDUserInfoURL": disco["userinfo_endpoint"],
        "authOpenIDJwksURL": disco["jwks_uri"],
        "authOpenIDLogoutURL": disco.get("end_session_endpoint"),
        "authOpenIDTokenSigningAlgorithm": disco["id_token_signing_alg_values_supported"][0],
        "authOpenIDClientID": OIDC_CLIENT_ID,
        "authOpenIDClientSecret": OIDC_CLIENT_SECRET,
        "authOpenIDButtonText": "Sign in with Pocket ID",
        "authOpenIDAutoLaunch": False,
        "authOpenIDAutoRegister": False,
        "authOpenIDMatchExistingBy": "username",
        "authOpenIDSubfolderForRedirectURLs": BASE_PATH,
    }
    current = abs_.api("GET", "/api/auth-settings")
    drift = {k: v for k, v in desired.items() if current.get(k) != v}
    active = "openid" in current["authActiveAuthMethods"]
    if not drift and active:
        return
    if active:  # Drop the cached client so the new settings take effect.
        abs_.api("PATCH", "/api/auth-settings", {"authActiveAuthMethods": ["local"]})
    abs_.api("PATCH", "/api/auth-settings", drift)
    abs_.api("PATCH", "/api/auth-settings", {"authActiveAuthMethods": ["local", "openid"]})
    print(f"Audiobookshelf: Pocket ID sign-in {', '.join(drift) or 'enabled'}")


def main() -> int:
    wait_for(f"{ABS}/healthcheck", "Audiobookshelf")
    wait_for(f"{BINDERY}/api/v1/health", "Bindery")
    abs_ = Abs()
    root = sign_in(abs_)
    ensure_server_settings(abs_)
    ids = ensure_libraries(abs_)
    ensure_bindery_link(abs_, root["id"], ids["Audiobooks"])
    ensure_openid(abs_)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
