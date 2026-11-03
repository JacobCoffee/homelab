"""Jellyfin API helpers shared by the jellyfin-sso-install and jellyfin-plugins Jobs."""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "http://jellyfin.media.svc.cluster.local:8096").rstrip("/")
API_KEY = os.environ["JELLYFIN_API_KEY"]


class ApiError(Exception):
    """A Jellyfin API answer outside `ok`. Polls retry 503 (Jellyfin still starting); else fatal."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def call(method: str, path: str, body=None, ok=(200, 204)):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        JELLYFIN_URL + path,
        method=method,
        data=data,
        # Jellyfin 12 dropped the legacy X-Emby-Token header; only this form authenticates.
        headers={"Authorization": f'MediaBrowser Token="{API_KEY}"', "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as err:
        raw = err.read()
        if err.code in ok:
            return err.code, (json.loads(raw) if raw else None)
        raise ApiError(err.code, f"{method} {path}: {err.code} {raw.decode(errors='replace')[:400]}")


def wait_for_server(tries=60, delay=5):
    for _ in range(tries):
        try:
            status, _ = call("GET", "/System/Info")
            if status == 200:
                return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        except ApiError as e:
            if e.code != 503:
                raise
        time.sleep(delay)
    sys.exit(f"Jellyfin never became reachable at {JELLYFIN_URL}")


def ensure_repositories(wanted: list[tuple[str, str]], remove: tuple[str, ...] = ()) -> None:
    """Add any (name, manifest URL) not registered yet and drop the manifest URLs in `remove`;
    leaves the others alone."""
    _, repos = call("GET", "/Repositories")
    repos = repos or []
    dropped = [r for r in repos if r.get("Url") in remove]
    repos = [r for r in repos if r.get("Url") not in remove]
    have = {r.get("Url") for r in repos}
    missing = [{"Name": name, "Url": url, "Enabled": True} for name, url in wanted if url not in have]
    if missing or dropped:
        call("POST", "/Repositories", repos + missing)
        for verb, changed in (("added", missing), ("removed", dropped)):
            if changed:
                print(f"{verb} repositories: {', '.join(r['Name'] for r in changed)}")


def installed_plugins() -> dict[str, dict]:
    """Active-or-not plugins by bare lowercase GUID (/Plugins lists Ids without dashes)."""
    _, plugins = call("GET", "/Plugins")
    return {p.get("Id", "").replace("-", "").lower(): p for p in (plugins or [])}


def bare(guid: str) -> str:
    return guid.replace("-", "").lower()


def install_missing(wanted: list[tuple[str, str, str]]) -> None:
    """Install every (package name, GUID, repository URL) not present, then restart Jellyfin once
    and wait until all of them are loaded: plugins only load at process start."""
    have = installed_plugins()
    missing = [w for w in wanted if bare(w[1]) not in have]
    if not missing:
        return
    for name, guid, repo in missing:
        print(f"installing {name}")
        qs = urllib.parse.urlencode({"assemblyGuid": guid, "repositoryUrl": repo})
        call("POST", f"/Packages/Installed/{urllib.parse.quote(name)}?{qs}")
    # Installs download in the background; give them time to land before the restart.
    time.sleep(30)
    print("restarting the jellyfin Deployment to load the new plugins")
    restart_deployment()
    time.sleep(20)  # let the old pod stop before polling, or it answers without the plugins
    for _ in range(60):
        try:
            have = installed_plugins()
            if all(bare(guid) in have for _, guid, _ in missing):
                return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        except ApiError as e:
            if e.code != 503:
                raise
        time.sleep(5)
    sys.exit("plugins did not appear in /Plugins after install + restart: "
             + ", ".join(n for n, g, _ in missing if bare(g) not in have))


def uninstall(unwanted: list[tuple[str, str]]) -> None:
    """Uninstall every (name, GUID) still present, then restart Jellyfin once and wait until they
    are gone: an uninstalled plugin stays loaded (status Deleted) until the process restarts."""
    have = installed_plugins()
    present = [(name, guid) for name, guid in unwanted if bare(guid) in have]
    if not present:
        return
    for name, guid in present:
        print(f"uninstalling {name}")
        call("DELETE", f"/Plugins/{guid}")
    print("restarting the jellyfin Deployment to unload them")
    restart_deployment()
    time.sleep(20)
    for _ in range(60):
        try:
            have = installed_plugins()
            if not any(bare(guid) in have for _, guid in present):
                return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        except ApiError as e:
            if e.code != 503:
                raise
        time.sleep(5)
    sys.exit("plugins still in /Plugins after uninstall + restart: "
             + ", ".join(n for n, g in present if bare(g) in have))


def restart_deployment():
    """`kubectl rollout restart deploy/jellyfin`: the Job's ServiceAccount may patch only that."""
    sa = "/var/run/secrets/kubernetes.io/serviceaccount"
    token = open(f"{sa}/token").read()
    patch = {"spec": {"template": {"metadata": {"annotations": {
        "kubectl.kubernetes.io/restartedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}}}}}
    req = urllib.request.Request(
        "https://kubernetes.default.svc/apis/apps/v1/namespaces/media/deployments/jellyfin",
        method="PATCH", data=json.dumps(patch).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/strategic-merge-patch+json"},
    )
    ctx = ssl.create_default_context(cafile=f"{sa}/ca.crt")
    with urllib.request.urlopen(req, timeout=20, context=ctx):
        pass
