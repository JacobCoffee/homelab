"""Install K0lin/jellyfin-plugin-sso and configure its Pocket ID (OpenID) provider, idempotently.

1. Add the plugin repository (https://raw.githubusercontent.com/k0lin/jellyfin-plugin-sso/
   manifest-release/manifest.json) if it isn't already registered.
2. Install "SSO Authentication" (GUID 505ce9d1-d916-42fa-86ca-673ef241d7df) from that repo if it
   isn't already installed, then restart the jellyfin Deployment so the new assembly loads (plugins
   only load at process start, and Jellyfin's own /System/Restart doesn't restart the container).
   The Job's ServiceAccount may patch only that Deployment, the way `kubectl rollout restart` does.
3. Wait for the plugin to report Active, then PUT its configuration: a "pocket-id" OpenID
   provider pointed at https://id.z7x.org, client id "jellyfin", RBAC on, group "lab-admins"
   both allowed to sign in and mapped to Jellyfin admin. NewPath=true locks the callback at
   /sso/OID/redirect/pocket-id, which is exactly what's registered as this client's callback URL
   in Pocket ID (see scripts/pocket-id.py CLIENTS["jellyfin"]).

Safe to re-run: every step checks current state first. Needs JELLYFIN_API_KEY (Dashboard > API
Keys) -- see kubernetes/README.md "Jellyfin SSO" for how to provide it.
"""

import json
import ssl
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "http://jellyfin.media.svc.cluster.local:8096").rstrip("/")
API_KEY = os.environ["JELLYFIN_API_KEY"]
OIDC_CLIENT_SECRET = os.environ["OIDC_CLIENT_SECRET"]

REPO_NAME = "SSO Authentication (K0lin)"
REPO_URL = "https://raw.githubusercontent.com/k0lin/jellyfin-plugin-sso/manifest-release/manifest.json"
PLUGIN_NAME = "SSO Authentication"
PLUGIN_GUID = "505ce9d1-d916-42fa-86ca-673ef241d7df"

LOGIN_DISCLAIMER = (
    '<form action="https://media.z7x.org/sso/OID/start/pocket-id">'
    '<button class="raised block emby-button button-submit">Sign in with Pocket ID</button>'
    "</form>"
)
LOGIN_CSS = (
    ".disclaimerContainer{display:block;margin:1em auto 0;max-width:50em;text-align:center;}"
)
PROVIDER_NAME = "pocket-id"
PROVIDER_CONFIG = {
    "OidEndpoint": "https://id.z7x.org",
    "OidClientId": "jellyfin",
    "OidSecret": OIDC_CLIENT_SECRET,
    "Enabled": True,
    "EnableAuthorization": True,
    "EnableAllFolders": True,
    "EnableContentDownloading": None,
    "ApplyContentDownloadPermissionOnEveryLogin": False,
    "EnabledFolders": [],
    "AdminRoles": ["lab-admins"],
    "PreserveAdminPermissions": True,
    "Roles": ["lab-admins"],
    "EnableFolderRoles": False,
    "EnableLiveTvRoles": False,
    "EnableLiveTv": True,
    "EnableLiveTvManagement": False,
    "LiveTvRoles": [],
    "LiveTvManagementRoles": [],
    "FolderRoleMapping": [],
    "RoleClaim": "groups",
    "OidScopes": ["openid", "profile", "email", "groups"],
    "OverrideDefaultScopes": True,
    "OidAuthorizationParameters": None,
    "DefaultProvider": None,
    # Envoy talks plain HTTP to Jellyfin, so without this the plugin builds an http:// callback,
    # which Pocket ID rejects (only https://media.z7x.org/sso/OID/redirect/pocket-id is registered).
    "SchemeOverride": "https",
    "PortOverride": None,
    "NewPath": True,
    "CanonicalLinks": {},
    "DefaultUsernameClaim": None,
    "AvatarUrlFormat": None,
    "DisableHttps": False,
    "DisablePushedAuthorization": False,
    "DoNotValidateEndpoints": False,
    "DoNotValidateIssuerName": False,
    "DoNotLoadProfile": False,
}


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
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as err:
        raw = err.read()
        if err.code in ok:
            return err.code, (json.loads(raw) if raw else None)
        raise ApiError(err.code, f"{method} {path}: {err.code} {raw.decode(errors='replace')[:400]}")


class ApiError(Exception):
    """A Jellyfin API answer outside `ok`. Polls retry 503 (Jellyfin still starting); else fatal."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


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


def ensure_repository():
    _, repos = call("GET", "/Repositories")
    repos = repos or []
    if any(r.get("Url") == REPO_URL for r in repos):
        print("repository already registered")
        return
    print(f"adding repository {REPO_NAME}")
    call("POST", "/Repositories", repos + [{"Name": REPO_NAME, "Url": REPO_URL, "Enabled": True}])


def plugin_installed() -> bool:
    _, plugins = call("GET", "/Plugins")
    # /Plugins lists Ids without dashes (505ce9d1d916...), so compare the bare hex.
    want = PLUGIN_GUID.replace("-", "")
    return any(p.get("Id", "").replace("-", "").lower() == want for p in (plugins or []))


def ensure_plugin_installed():
    if plugin_installed():
        print("plugin already installed")
        return
    print(f"installing {PLUGIN_NAME}")
    qs = urllib.parse.urlencode({"AssemblyGuid": PLUGIN_GUID, "repositoryUrl": REPO_URL})
    call("POST", f"/Packages/Installed/{urllib.parse.quote(PLUGIN_NAME)}?{qs}")
    print("restarting the jellyfin Deployment to load the new plugin")
    restart_deployment()
    time.sleep(20)  # let the old pod stop before polling, or it answers without the plugin
    for _ in range(60):
        try:
            if plugin_installed():
                return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        except ApiError as e:
            if e.code != 503:
                raise
        time.sleep(5)
    sys.exit("plugin did not appear in /Plugins after install + restart")


def restart_deployment():
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


def set_config():
    status, config = call("GET", f"/Plugins/{PLUGIN_GUID}/Configuration")
    if status != 200 or config is None:
        sys.exit(f"could not read plugin configuration (status {status})")
    config.setdefault("OidConfigs", {})
    if config["OidConfigs"].get(PROVIDER_NAME) == PROVIDER_CONFIG:
        print(f"{PROVIDER_NAME} provider already configured")
        return
    config["OidConfigs"][PROVIDER_NAME] = PROVIDER_CONFIG
    call("POST", f"/Plugins/{PLUGIN_GUID}/Configuration", config)
    print(f"configured {PROVIDER_NAME} provider")


def set_branding():
    status, branding = call("GET", "/System/Configuration/branding")
    if status != 200 or branding is None:
        sys.exit(f"could not read branding configuration (status {status})")
    disclaimer = branding.get("LoginDisclaimer") or ""
    css = branding.get("CustomCss") or ""
    changed = False
    if LOGIN_DISCLAIMER not in disclaimer:
        branding["LoginDisclaimer"] = (disclaimer + "\n" + LOGIN_DISCLAIMER).strip()
        changed = True
    if LOGIN_CSS not in css:
        branding["CustomCss"] = (css + "\n" + LOGIN_CSS).strip()
        changed = True
    if not changed:
        print("login button already in branding")
        return
    call("POST", "/System/Configuration/branding", branding)
    print("added Sign in with Pocket ID button to login page")


def main():
    wait_for_server()
    ensure_repository()
    ensure_plugin_installed()
    set_config()
    set_branding()
    print("ok")


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        sys.exit(str(e))
