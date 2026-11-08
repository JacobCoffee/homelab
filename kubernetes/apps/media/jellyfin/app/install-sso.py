"""Install K0lin/jellyfin-plugin-sso and configure its Pocket ID (OpenID) provider, idempotently.

1. Add the plugin repository (https://raw.githubusercontent.com/k0lin/jellyfin-plugin-sso/
   manifest-release/manifest.json) if it isn't already registered.
2. Install "SSO Authentication" (GUID 505ce9d1-d916-42fa-86ca-673ef241d7df) from that repo if it
   isn't already installed, then restart the jellyfin Deployment so the new assembly loads (plugins
   only load at process start, and Jellyfin's own /System/Restart doesn't restart the container).
   The Job's ServiceAccount may patch only that Deployment, the way `kubectl rollout restart` does.
3. Wait for the plugin to report Active, then PUT its configuration: a "pocket-id" OpenID
   provider pointed at https://id.z7x.org, client id "jellyfin", RBAC on: groups "lab-admins"
   (Jellyfin admin) and "media" may sign in, every library, and Live TV only for "lab-admins" and
   "live-tv" (the plugin re-applies admin, libraries and Live TV on every login). A login lands in
   the Jellyfin account with the same username, so friends' Pocket ID usernames match their
   Wizarr ones (scripts/pocket-id.py --add-user). The plugin's own CanonicalLinks (which login is
   which account) are kept as they are. NewPath=true locks the callback at
   /sso/OID/redirect/pocket-id, which is exactly what's registered as this client's callback URL
   in Pocket ID (see scripts/pocket-id.py CLIENTS["jellyfin"]).

Safe to re-run: every step checks current state first. Needs JELLYFIN_API_KEY (Dashboard > API
Keys) -- see kubernetes/README.md "Jellyfin SSO" for how to provide it.
"""

import os
import sys

from jellyfin_api import ApiError, call, ensure_repositories, install_missing, wait_for_server

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
    "Roles": ["lab-admins", "media"],
    "EnableFolderRoles": False,
    "EnableLiveTvRoles": True,
    "EnableLiveTv": False,
    "EnableLiveTvManagement": False,
    "LiveTvRoles": ["lab-admins", "live-tv"],
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
    "DefaultUsernameClaim": None,
    "AvatarUrlFormat": None,
    "DisableHttps": False,
    "DisablePushedAuthorization": False,
    "DoNotValidateEndpoints": False,
    "DoNotValidateIssuerName": False,
    "DoNotLoadProfile": False,
}


def set_config():
    status, config = call("GET", f"/Plugins/{PLUGIN_GUID}/Configuration")
    if status != 200 or config is None:
        sys.exit(f"could not read plugin configuration (status {status})")
    config.setdefault("OidConfigs", {})
    current = config["OidConfigs"].get(PROVIDER_NAME) or {}
    # The plugin records which login is which Jellyfin account here; never reset it.
    wanted = {**PROVIDER_CONFIG, "CanonicalLinks": current.get("CanonicalLinks") or {}}
    if current == wanted:
        print(f"{PROVIDER_NAME} provider already configured")
        return
    config["OidConfigs"][PROVIDER_NAME] = wanted
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
    ensure_repositories([(REPO_NAME, REPO_URL)])
    install_missing([(PLUGIN_NAME, PLUGIN_GUID, REPO_URL)])
    set_config()
    set_branding()
    print("ok")


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        sys.exit(str(e))
