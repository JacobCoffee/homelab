#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["requests>=2.32", "pyyaml>=6", "uptime-kuma-api2>=2.9.0"]
# ///
"""Keep the lab apps' own logins in line with the passkey sign-in.

    scripts/lab-logins.py           # apply; then commit the files it says it changed
    scripts/lab-logins.py --check   # only report

The login for the apps behind the gateway is Pocket ID (id.z7x.org, passkeys): Envoy's OIDC filter
(kubernetes/apps/network/lab-sso) sits in front of them, and their pods only take traffic from the
gateway and the lab's own namespaces. So each app's own login is switched off, the safest way it
offers:
  Sonarr, Radarr, Lidarr, Prowlarr   AUTH__METHOD External in their HelmReleases (checked here)
  Unpackerr                          UN_WEBSERVER_UI_PASSWORD noauth for the pod network (env)
  qBittorrent                        subnet bypass for the pod network (its init container)
  Bazarr                             auth type None
  SABnzbd                            no username/password
  Cleanuparr                         "disable auth for local addresses", pod network trusted
  Uptime Kuma                        disableAuth
  Dispatcharr                        Reverse Proxy Auth: its route asserts the user (X-Lab-User)

The shared login (vault_lab_admin_username / vault_lab_admin_password) stays on the accounts that
still have a job: qBittorrent's, for anything outside the pod network; Grafana's local admin, the
break-glass login next to "Sign in with Pocket ID" (and the Git Sync job's); and the Kuma,
Cleanuparr and Dispatcharr accounts this script uses to change their settings (Dispatcharr's is
the user the gateway signs in, and its setup Job's login). The files that hand it to an
app on restart are kept in step:
  gluetun-secret QBT_WEBUI_PASSWORD_PBKDF2   qBittorrent's login is rewritten on every start
  uptime-kuma secret KUMA_PASSWORD           the seed job logs in with it
  dispatcharr secret DISPATCHARR_PASSWORD    the setup Job logs in with it
  grafana-admin-credentials admin-password   the Git Sync job logs in with it

Apps are reached with kubectl port-forward (the gateway would send this script to the passkey
sign-in), except Grafana, which has no gateway check.

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
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
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
DISPATCHARR = K8S / "media/dispatcharr/app/secret.sops.yaml"
GRAFANA = K8S / "observability/kube-prometheus-stack/app/secret.sops.yaml"
POD_NETWORK = "10.244.0.0/16"

SERVARR = {  # name: (Service, port, API version, arr-api-keys key)
    "Sonarr": ("sonarr", 8989, "v3", "SONARR_API_KEY"),
    "Radarr": ("radarr", 7878, "v3", "RADARR_API_KEY"),
    "Lidarr": ("lidarr", 8686, "v1", "LIDARR_API_KEY"),
    "Prowlarr": ("prowlarr", 9696, "v1", "PROWLARR_API_KEY"),
}
TIMEOUT = 20
NOT_SET = "FAILED: login still refused after setting it"


@dataclass
class Run:
    user: str
    password: str
    previous: str
    check: bool
    stack: ExitStack
    results: dict[str, str] = field(default_factory=dict)
    changed_files: set[Path] = field(default_factory=set)

    def candidates(self, *extra: str) -> list[str]:
        """Passwords an app might still have, newest first, for apps that need the current one."""
        seen: list[str] = []
        for p in (self.previous, *extra):
            if p and p != self.password and p not in seen:
                seen.append(p)
        return seen

    def url(self, ns: str, svc: str, port: int) -> str:
        return self.stack.enter_context(forward(ns, svc, port))


@contextmanager
def forward(ns: str, svc: str, port: int) -> Iterator[str]:
    """http://127.0.0.1:<free port> forwarded to the Service, for as long as the block runs."""
    p = subprocess.Popen(
        ["mise", "-C", str(ROOT), "exec", "--", "kubectl", "-n", ns, "port-forward", f"svc/{svc}", f":{port}"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    try:
        m = re.search(r"127\.0\.0\.1:(\d+)", p.stdout.readline())
        if not m:
            raise RuntimeError(f"port-forward to {ns}/{svc} didn't start")
        yield f"http://127.0.0.1:{m.group(1)}"
    finally:
        p.terminate()
        p.wait()


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
    """Apps may restart after an auth change: retry the check for ~30s."""
    for _ in range(tries):
        try:
            if check():
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


# --- Login off -----------------------------------------------------------------------------------

def servarr(run: Run, svc: str, port: int, ver: str, key: str) -> str:
    # Set by env (AUTH__METHOD External), which overrides the API, so this only checks it.
    host = requests.get(f"{run.url('media', svc, port)}/api/{ver}/config/host",
                        headers={"X-Api-Key": key}, timeout=TIMEOUT)
    host.raise_for_status()
    method = host.json()["authenticationMethod"]
    return "ok (off)" if method == "external" else f"FAILED: auth is {method}; check its HelmRelease"


def bazarr(run: Run) -> str:
    conf = yaml.safe_load(sh("kubectl", "-n", "media", "exec", "deploy/bazarr", "--",
                             "cat", "/config/config/config.yaml"))
    s = requests.Session()
    s.headers["X-API-KEY"] = conf["auth"]["apikey"]
    url = f"{run.url('media', 'bazarr', 6767)}/api/system/settings"

    def off() -> bool:
        return s.get(url, timeout=TIMEOUT).json()["auth"].get("type") in (None, "None")

    if off():
        return "ok (off)"
    if run.check:
        return "needs turning off"
    s.post(url, timeout=TIMEOUT, data={"settings-auth-type": "None"}).raise_for_status()
    return "turned off" if eventually(off) else "FAILED: still asks for a login"


def sabnzbd(run: Run, key: str) -> str:
    base = run.url("media", "sabnzbd", 8080)

    def api(**params: str) -> requests.Response:
        r = requests.get(f"{base}/api", timeout=TIMEOUT, params={**params, "output": "json", "apikey": key})
        r.raise_for_status()
        return r

    def off() -> bool:
        return not api(mode="get_config", section="misc").json()["config"]["misc"].get("username")

    if off():
        return "ok (off)"
    if run.check:
        return "needs turning off"
    api(mode="set_config", section="misc", keyword="username", value="")
    api(mode="set_config", section="misc", keyword="password", value="")
    return "turned off" if eventually(off) else "FAILED: still asks for a login"


def cleanuparr(run: Run) -> str:
    base = f"{run.url('media', 'cleanuparr', 11011)}/api"

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
    token = login(run.password)
    if token is None:
        for old in run.candidates():
            if (token := login(old)) is not None:
                if run.check:
                    return "needs a password update"
                requests.put(f"{base}/account/password", timeout=TIMEOUT,
                             headers={"Authorization": f"Bearer {token}"},
                             json={"currentPassword": old, "newPassword": run.password}).raise_for_status()
                if (token := login(run.password)) is None:
                    return NOT_SET
                break
        else:
            return "FAILED: neither the shared nor the previous password works"
    auth = {"Authorization": f"Bearer {token}"}
    cfg = requests.get(f"{base}/configuration/general", headers=auth, timeout=TIMEOUT).json()
    want = {"disableAuthForLocalAddresses": True, "trustForwardedHeaders": False,
            "trustedNetworks": [POD_NETWORK]}
    if all(cfg.get("auth", {}).get(k) == v for k, v in want.items()):
        return "ok (off)"
    if run.check:
        return "needs turning off"
    cfg["auth"] = {**cfg.get("auth", {}), **want}
    requests.put(f"{base}/configuration/general", headers=auth, json=cfg, timeout=TIMEOUT).raise_for_status()
    return "turned off (pod network trusted)"


def uptime_kuma(run: Run) -> str:
    from uptime_kuma_api import UptimeKumaApi, UptimeKumaException

    base = run.url("status", "uptime-kuma", 3001)
    for pw in [run.password, *run.candidates(sops_get(KUMA, "KUMA_PASSWORD"))]:
        api = UptimeKumaApi(base, timeout=TIMEOUT)
        try:
            try:
                api.login(run.user, pw)
            except UptimeKumaException:
                continue
            if pw != run.password:
                if run.check:
                    return "needs a password update"
                api.change_password(pw, run.password)
            status = "ok (off)" if api.get_settings().get("disableAuth") else None
            if status is None:
                if run.check:
                    return "needs turning off"
                api.set_settings(run.password, disableAuth=True)
                status = "turned off"
            break
        finally:
            api.disconnect()
    else:
        return "FAILED: neither the shared, the previous nor the stored password works"
    sops_put(run, KUMA, "KUMA_USERNAME", run.user)
    sops_put(run, KUMA, "KUMA_PASSWORD", run.password)
    return status


def dispatcharr(run: Run) -> str:
    # Reverse Proxy Auth and the admin account itself come from the setup Job (app/setup.py).
    base = f"{run.url('media', 'dispatcharr', 9191)}/api"

    def login(pw: str) -> str | None:
        r = requests.post(f"{base}/accounts/token/", timeout=TIMEOUT,
                          json={"username": run.user, "password": pw})
        if r.status_code == 429:  # a few sign-ins a minute; a wrong password counts too
            r.raise_for_status()
        return r.json()["access"] if r.ok else None

    if not requests.get(f"{base}/accounts/initialize-superuser/", timeout=TIMEOUT).json()["superuser_exists"]:
        return "FAILED: no account yet (the dispatcharr-setup Job creates it)"
    token = login(run.password)
    if token is None:
        for old in run.candidates(sops_get(DISPATCHARR, "DISPATCHARR_PASSWORD")):
            if (token := login(old)) is not None:
                if run.check:
                    return "needs a password update"
                requests.patch(f"{base}/accounts/users/me/", timeout=TIMEOUT,
                               headers={"Authorization": f"Bearer {token}"},
                               json={"password": run.password}).raise_for_status()
                if (token := login(run.password)) is None:
                    return NOT_SET
                break
        else:
            return "FAILED: neither the shared, the previous nor the stored password works"
    settings = requests.get(f"{base}/core/settings/", timeout=TIMEOUT,
                            headers={"Authorization": f"Bearer {token}"}).json()
    proxy = next(s["value"] for s in settings if s["key"] == "reverse_proxy_auth")
    status = ("ok (off: gateway signs in)" if proxy.get("enabled") and proxy.get("header") == "X-Lab-User"
              else "FAILED: Reverse Proxy Auth is off (re-run the dispatcharr-setup Job)")
    sops_put(run, DISPATCHARR, "DISPATCHARR_USERNAME", run.user)
    sops_put(run, DISPATCHARR, "DISPATCHARR_PASSWORD", run.password)
    return status


# --- Shared login kept ---------------------------------------------------------------------------

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
    # The init container sets the login from this hash on every start, and the subnet bypass
    # means port-forwarded requests (from the node) can't prove the password: keep the hash right.
    if qbt_hash_matches(sops_get(GLUETUN, "QBT_WEBUI_PASSWORD_PBKDF2"), run.password):
        return "ok (fallback login)"
    sops_put(run, GLUETUN, "QBT_WEBUI_PASSWORD_PBKDF2", qbt_hash(run.password))
    if run.check:
        return "needs update"
    return "set (after the push it applies on restart: kubectl -n media rollout restart deploy/gluetun-qbittorrent)"


def grafana(run: Run) -> str:
    base = "https://grafana.z7x.org/api"

    def works(pw: str) -> bool:
        return requests.get(f"{base}/user", auth=(run.user, pw), timeout=TIMEOUT).ok

    status = "ok (break-glass login)" if works(run.password) else None
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


def main(argv: list[str]) -> int:
    v = vault_get()
    arr = json.loads(sh("sops", "-d", "--extract", '["stringData"]', "--output-type", "json", str(ARR_KEYS)))
    failed = False
    with ExitStack() as stack:
        run = Run(user=v["vault_lab_admin_username"], password=v["vault_lab_admin_password"],
                  previous=v.get("vault_lab_admin_previous_password", ""), check="--check" in argv,
                  stack=stack)
        steps: dict[str, Callable[[], str]] = {
            name: (lambda s=svc, p=port, ver=ver, k=key: servarr(run, s, p, ver, arr[k]))
            for name, (svc, port, ver, key) in SERVARR.items()
        }
        steps |= {
            "Bazarr": lambda: bazarr(run),
            "SABnzbd": lambda: sabnzbd(run, arr["SABNZBD_API_KEY"]),
            "Cleanuparr": lambda: cleanuparr(run),
            "Uptime Kuma": lambda: uptime_kuma(run),
            "Dispatcharr": lambda: dispatcharr(run),
            "qBittorrent": lambda: qbittorrent(run),
            "Grafana": lambda: grafana(run),
        }
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
