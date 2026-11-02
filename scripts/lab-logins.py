#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["requests>=2.32", "pyyaml>=6", "uptime-kuma-api==1.2.1"]
# ///
"""Give every lab app with a local login the same one: vault_lab_admin_username /
vault_lab_admin_password from the Ansible vault.

    scripts/lab-logins.py           # apply; then commit the files it says it changed
    scripts/lab-logins.py --check   # only report which apps take the shared login

Covers Sonarr, Radarr, Lidarr, Prowlarr, Bazarr, SABnzbd, Bindery, Cleanuparr, qBittorrent,
Uptime Kuma, Grafana and Unpackerr. Not AdGuard (its login is wired into external-dns, the DoT
cert push, its exporter, Homepage and the Fire TV app), not OPNsense/Proxmox root (recovery
accounts), not Jellyfin/Home Assistant/Seerr/Cabotage (personal or GitHub accounts).

Changes are made live through each app's API (the *arrs, Bazarr and SABnzbd by API key; the rest
by logging in), then the files that hand the login to an app on restart are kept in step:
  arr-api-keys UNPACKERR_UI_LOGIN            Unpackerr reads its login from env only
  gluetun-secret QBT_WEBUI_PASSWORD_PBKDF2   qBittorrent's login is rewritten on every start
  uptime-kuma secret KUMA_PASSWORD           the seed job logs in with it
  grafana-admin-credentials admin-password   the Git Sync job logs in with it

Rotating: scripts/vault-set.py vault_lab_admin_previous_password vault_lab_admin_password (the
current password, then the new one), then run this. Apps that need the current password to set a
new one try the shared password, then vault_lab_admin_previous_password, then whatever their
Kubernetes secret holds.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault import ROOT, vault_get  # noqa: E402

K8S = ROOT / "kubernetes/apps"
ARR_KEYS = K8S / "media/arr-api-keys/app/secret.sops.yaml"
GLUETUN = K8S / "media/gluetun-qbittorrent/app/secret.sops.yaml"
KUMA = K8S / "status/uptime-kuma/app/secret.sops.yaml"
GRAFANA = K8S / "observability/kube-prometheus-stack/app/secret.sops.yaml"

SERVARR = {  # name: (URL, API version, arr-api-keys key)
    "Sonarr": ("https://sonarr.z7x.org", "v3", "SONARR_API_KEY"),
    "Radarr": ("https://radarr.z7x.org", "v3", "RADARR_API_KEY"),
    "Lidarr": ("https://lidarr.z7x.org", "v1", "LIDARR_API_KEY"),
    "Prowlarr": ("https://prowlarr.z7x.org", "v1", "PROWLARR_API_KEY"),
}
TIMEOUT = 20
NOT_SET = "FAILED: login still refused after setting it"


@dataclass
class Run:
    user: str
    password: str
    previous: str
    check: bool
    results: dict[str, str] = field(default_factory=dict)
    changed_files: set[Path] = field(default_factory=set)

    def candidates(self, *extra: str) -> list[str]:
        """Passwords an app might still have, newest first, for apps that need the current one."""
        seen: list[str] = []
        for p in (self.previous, *extra):
            if p and p != self.password and p not in seen:
                seen.append(p)
        return seen


def sh(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["mise", "-C", str(ROOT), "exec", "--", *args],
        input=stdin, capture_output=True, text=True, check=True,
    ).stdout


def sops_get(path: Path, key: str) -> str:
    return sh("sops", "-d", "--extract", f'["stringData"]["{key}"]', str(path))


def sops_put(run: Run, path: Path, key: str, value: str) -> None:
    """Set stringData[key] unless it already holds value; the value never goes on argv."""
    try:
        if sops_get(path, key) == value:
            return
    except subprocess.CalledProcessError:
        pass  # key not there yet
    if run.check:
        run.results[f"{path.name}:{key}"] = "would update"
        return
    sh("sops", "set", "--value-stdin", str(path), f'["stringData"]["{key}"]', stdin=json.dumps(value))
    run.changed_files.add(path)


def eventually(check: Callable[[], bool], tries: int = 15) -> bool:
    """Apps may restart after an auth change: retry the post-change login for ~30s."""
    for _ in range(tries):
        try:
            if check():
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


# --- API-key apps: set the login outright --------------------------------------------------------

def forms_login(base: str, run: Run) -> bool:
    # rememberMe as "true": Sonarr 5 binds it as a bool and answers "on" with a 400.
    r = requests.post(f"{base}/login?returnUrl=%2F", allow_redirects=False, timeout=TIMEOUT,
                      data={"username": run.user, "password": run.password, "rememberMe": "true"})
    return r.status_code in (302, 303) and "loginFailed" not in r.headers.get("Location", "")


def servarr(run: Run, base: str, ver: str, key: str) -> str:
    s = requests.Session()
    s.headers["X-Api-Key"] = key
    host = s.get(f"{base}/api/{ver}/config/host", timeout=TIMEOUT)
    host.raise_for_status()
    cfg = host.json()
    if cfg["authenticationMethod"] == "forms" and cfg["username"] == run.user and forms_login(base, run):
        return "ok"
    if run.check:
        return "needs update"
    cfg.update(authenticationMethod="forms", authenticationRequired="enabled", username=run.user,
               password=run.password, passwordConfirmation=run.password)
    s.put(f"{base}/api/{ver}/config/host/{cfg['id']}", json=cfg, timeout=TIMEOUT).raise_for_status()
    return "set" if eventually(lambda: forms_login(base, run)) else NOT_SET


def bazarr(run: Run) -> str:
    conf = yaml.safe_load(sh("kubectl", "-n", "media", "exec", "deploy/bazarr", "--",
                             "cat", "/config/config/config.yaml"))
    s = requests.Session()
    s.headers["X-API-KEY"] = conf["auth"]["apikey"]
    url = "https://bazarr.z7x.org/api/system/settings"
    # Bazarr stores md5(password) and hashes whatever it's given.
    want = hashlib.md5(run.password.encode()).hexdigest()

    def matches() -> bool:
        auth = s.get(url, timeout=TIMEOUT).json()["auth"]
        return auth.get("type") == "form" and auth.get("username") == run.user and auth.get("password") == want

    if matches():
        return "ok"
    if run.check:
        return "needs update"
    s.post(url, timeout=TIMEOUT, data={
        "settings-auth-type": "form", "settings-auth-username": run.user,
        "settings-auth-password": run.password,
    }).raise_for_status()
    return "set" if eventually(matches) else NOT_SET


def sabnzbd(run: Run, key: str) -> str:
    base = "https://sabnzbd.z7x.org"

    def login() -> bool:
        r = requests.post(f"{base}/login/", allow_redirects=False, timeout=TIMEOUT,
                          data={"username": run.user, "password": run.password})
        return r.status_code in (302, 303) and "login_cookie" in r.headers.get("Set-Cookie", "")

    def api(**params: str) -> requests.Response:
        r = requests.get(f"{base}/api", timeout=TIMEOUT, params={**params, "output": "json", "apikey": key})
        r.raise_for_status()
        return r

    misc = api(mode="get_config", section="misc").json()["config"]["misc"]
    if misc.get("username") == run.user and login():
        return "ok"
    if run.check:
        return "needs update"
    api(mode="set_config", section="misc", keyword="username", value=run.user)
    api(mode="set_config", section="misc", keyword="password", value=run.password)
    return "set" if eventually(login) else NOT_SET


# --- Apps that need the current password (or a first-run setup) ---------------------------------

def bindery(run: Run) -> str:
    base = "https://bindery.z7x.org/api/v1"

    def login(pw: str) -> requests.Session | None:
        s = requests.Session()
        r = s.post(f"{base}/auth/login", timeout=TIMEOUT,
                   json={"username": run.user, "password": pw, "rememberMe": False})
        return s if r.ok else None

    if requests.get(f"{base}/auth/status", timeout=TIMEOUT).json().get("setupRequired"):
        if run.check:
            return "needs setup (anyone can claim it)"
        requests.post(f"{base}/auth/setup", timeout=TIMEOUT,
                      json={"username": run.user, "password": run.password}).raise_for_status()
        return "set (first-run setup)" if login(run.password) else NOT_SET
    if login(run.password):
        return "ok"
    for old in run.candidates():
        if (s := login(old)) is not None:
            if run.check:
                return "needs update"
            csrf = s.get(f"{base}/auth/csrf", headers={"X-Requested-With": "bindery-ui"},
                         timeout=TIMEOUT).json()["csrfToken"]
            s.post(f"{base}/auth/password", timeout=TIMEOUT, headers={"X-CSRF-Token": csrf},
                   json={"currentPassword": old, "newPassword": run.password}).raise_for_status()
            return "set" if login(run.password) else NOT_SET
    return "FAILED: neither the shared nor the previous password works"


def cleanuparr(run: Run) -> str:
    base = "https://cleanuparr.z7x.org/api"

    def login(pw: str) -> str | None:
        r = requests.post(f"{base}/auth/login", timeout=TIMEOUT,
                          json={"username": run.user, "password": pw})
        return r.json()["tokens"]["accessToken"] if r.ok else None

    if not requests.get(f"{base}/auth/status", timeout=TIMEOUT).json()["setupCompleted"]:
        if run.check:
            return "needs setup (anyone can claim it)"
        r = requests.post(f"{base}/auth/setup/account", timeout=TIMEOUT,
                          json={"username": run.user, "password": run.password})
        if r.status_code != 409:  # 409: an account exists from an unfinished setup
            r.raise_for_status()
        requests.post(f"{base}/auth/setup/complete", timeout=TIMEOUT).raise_for_status()
        return "set (first-run setup)" if login(run.password) else "FAILED: setup left a different account"
    if login(run.password):
        return "ok"
    for old in run.candidates():
        if (token := login(old)) is not None:
            if run.check:
                return "needs update"
            requests.put(f"{base}/account/password", timeout=TIMEOUT,
                         headers={"Authorization": f"Bearer {token}"},
                         json={"currentPassword": old, "newPassword": run.password}).raise_for_status()
            return "set" if login(run.password) else NOT_SET
    return "FAILED: neither the shared nor the previous password works"


def qbt_hash(password: str, salt: bytes | None = None) -> str:
    """qBittorrent's WebUI\\Password_PBKDF2: PBKDF2-HMAC-SHA512, 100000 rounds, 16-byte salt."""
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, 100_000)
    return f"@ByteArray({base64.b64encode(salt).decode()}:{base64.b64encode(dk).decode()})"


def qbt_hash_matches(stored: str, password: str) -> bool:
    try:
        salt_b64 = stored.removeprefix("@ByteArray(").split(":", 1)[0]
        return qbt_hash(password, base64.b64decode(salt_b64)) == stored
    except ValueError:
        return False


def qbittorrent(run: Run) -> str:
    base = "https://qbittorrent.z7x.org/api/v2"

    def login(pw: str) -> requests.Session | None:
        s = requests.Session()
        r = s.post(f"{base}/auth/login", timeout=TIMEOUT, data={"username": run.user, "password": pw})
        # 5.x answers 204 with no body; older versions 200 "Ok." (a refusal is 401 / "Fails.").
        return s if r.status_code == 204 or (r.ok and r.text.strip() == "Ok.") else None

    status = "ok"
    if not login(run.password):
        for old in run.candidates():
            if (s := login(old)) is not None:
                if run.check:
                    return "needs update"
                s.post(f"{base}/app/setPreferences", timeout=TIMEOUT,
                       data={"json": json.dumps({"web_ui_password": run.password})}).raise_for_status()
                status = "set" if login(run.password) else NOT_SET
                break
        else:
            return "FAILED: neither the shared nor the previous password works"
    # The init container rewrites the login from this hash on every start: keep it matching.
    if not qbt_hash_matches(sops_get(GLUETUN, "QBT_WEBUI_PASSWORD_PBKDF2"), run.password):
        sops_put(run, GLUETUN, "QBT_WEBUI_PASSWORD_PBKDF2", qbt_hash(run.password))
    return status


def uptime_kuma(run: Run) -> str:
    from uptime_kuma_api import UptimeKumaApi, UptimeKumaException

    status = None
    for pw in [run.password, *run.candidates(sops_get(KUMA, "KUMA_PASSWORD"))]:
        api = UptimeKumaApi("https://kuma.z7x.org", timeout=TIMEOUT)
        try:
            api.login(run.user, pw)
        except UptimeKumaException:
            continue
        else:
            if pw == run.password:
                status = "ok"
            elif run.check:
                status = "needs update"
            else:
                api.change_password(pw, run.password)
                status = "set"
            break
        finally:
            api.disconnect()
    if status is None:
        return "FAILED: neither the shared, the previous nor the stored password works"
    sops_put(run, KUMA, "KUMA_USERNAME", run.user)
    sops_put(run, KUMA, "KUMA_PASSWORD", run.password)
    return status


def grafana(run: Run) -> str:
    base = "https://grafana.z7x.org/api"

    def works(pw: str) -> bool:
        return requests.get(f"{base}/user", auth=(run.user, pw), timeout=TIMEOUT).ok

    status = "ok" if works(run.password) else None
    if status is None:
        for old in run.candidates(sops_get(GRAFANA, "admin-password")):
            if works(old):
                if run.check:
                    return "needs update"
                requests.put(f"{base}/user/password", auth=(run.user, old), timeout=TIMEOUT, json={
                    "oldPassword": old, "newPassword": run.password, "confirmNew": run.password,
                }).raise_for_status()
                status = "set" if works(run.password) else NOT_SET
                break
        else:
            return "FAILED: neither the shared, the previous nor the stored password works"
    sops_put(run, GRAFANA, "admin-user", run.user)
    sops_put(run, GRAFANA, "admin-password", run.password)
    return status


def unpackerr(run: Run) -> str:
    # Env only (UN_WEBSERVER_UI_PASSWORD, "user:pass"): takes effect when the pod restarts.
    before = set(run.changed_files)
    sops_put(run, ARR_KEYS, "UNPACKERR_UI_LOGIN", f"{run.user}:{run.password}")
    if run.check:
        return "needs update" if run.results.pop(f"{ARR_KEYS.name}:UNPACKERR_UI_LOGIN", None) else "ok"
    if run.changed_files == before:
        return "ok"
    return "set (after the push: kubectl -n media rollout restart deploy/unpackerr)"


def main(argv: list[str]) -> int:
    v = vault_get()
    run = Run(user=v["vault_lab_admin_username"], password=v["vault_lab_admin_password"],
              previous=v.get("vault_lab_admin_previous_password", ""), check="--check" in argv)
    arr = json.loads(sh("sops", "-d", "--extract", '["stringData"]', "--output-type", "json", str(ARR_KEYS)))
    steps: dict[str, Callable[[], str]] = {
        name: (lambda b=base, ver=ver, k=key: servarr(run, b, ver, arr[k]))
        for name, (base, ver, key) in SERVARR.items()
    }
    steps |= {
        "Bazarr": lambda: bazarr(run),
        "SABnzbd": lambda: sabnzbd(run, arr["SABNZBD_API_KEY"]),
        "Bindery": lambda: bindery(run),
        "Cleanuparr": lambda: cleanuparr(run),
        "qBittorrent": lambda: qbittorrent(run),
        "Uptime Kuma": lambda: uptime_kuma(run),
        "Grafana": lambda: grafana(run),
        "Unpackerr": lambda: unpackerr(run),
    }
    failed = False
    for name, step in steps.items():
        try:
            result = step()
        except Exception as e:  # noqa: BLE001 -- one app failing mustn't stop the others
            if isinstance(e, requests.HTTPError) and e.response is not None:
                detail = f"HTTP {e.response.status_code} from {e.response.url.split('?')[0]}"
            else:  # strip query strings: SABnzbd's API key travels in one
                detail = re.sub(r"\?\S*", "?…", str(e))[:200]
            result = f"FAILED: {type(e).__name__}: {detail}"
        failed |= result.startswith("FAILED")
        print(f"{name:12} {result}")
    for key, result in run.results.items():
        print(f"{key:12} {result}")
    if run.changed_files:
        print("\nchanged (commit these):")
        for p in sorted(run.changed_files):
            print(f"  {p.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
