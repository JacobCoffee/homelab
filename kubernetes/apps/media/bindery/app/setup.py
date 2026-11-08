"""Give Bindery ebook *and* audiobook root folders, download clients, and an indexer, idempotently.

1. Create (if missing) and register /media/library/books and /media/library/audiobooks as root
   folders, then set them as the install-wide default ebook/audiobook roots and default new
   authors to media type "both" -- without that an author added from the UI defaults to
   ebook-only and never gets audiobook releases searched.
2. SABnzbd and qBittorrent as download clients, both pointed at the "books" category media-config
   already creates in each (kubernetes/apps/media/media-config/app/configure.py) -- Bindery
   doesn't need a second category, it resolves ebook vs audiobook grabs by media type, not by
   category.
3. Indexers: Bindery has its own "Settings -> Indexers -> Add Prowlarr" pull integration
   (POST /prowlarr + POST /prowlarr/{id}/sync, docs/API.md), the mirror image of Sonarr/Radarr/
   Lidarr's Prowlarr *app* sync (Prowlarr pushing to them). Bindery isn't a Prowlarr app target
   (no Readarr-compatible endpoint Prowlarr's app sync can see), so the working direction is
   Bindery pulling from Prowlarr, not Prowlarr pushing to Bindery. This reuses NZBFinder's
   existing Prowlarr registration instead of a second copy of its API key: a freshly synced
   indexer already carries both the Newznab Books (7020) and Audio/Audiobook (3030) categories
   (confirmed live against this cluster's NZBFinder), so no further category tuning is needed.
   A synced MyAnonamouse indexer gets its seed limits pinned (MAM_SEED_MINUTES below).
4. Quality profiles: Bindery ships "E-Book" (cutoff epub) and "Audiobook" (cutoff audiobook)
   profiles on first boot; set an explicit preference order on each (epub first for ebooks, m4b
   first for audiobooks) and tighten the Audiobook cutoff to m4b specifically.
5. Sign-in: register Pocket ID (https://id.z7x.org, client "bindery" in scripts/pocket-id.py) as
   the OIDC provider "pocket-id", then switch the auth mode to "enabled". Bindery is public at
   bindery.z7x.org so friends can request books; the HelmRelease turns password logins off, makes
   new accounts requesters and lab-admins members admins. The provider is written every run,
   since Bindery never returns its client secret to compare against.

Auth: the instance API key (BINDERY_API_KEY, from the bindery Secret), which the HelmRelease also
uses to seed a fresh install, so the key this Job holds is the one Bindery accepts.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

SVC = "media.svc.cluster.local"
BINDERY = os.environ.get("BINDERY_URL", f"http://bindery.{SVC}:8787").rstrip("/")
API = f"{BINDERY}/api/v1"
SABNZBD_HOST = os.environ.get("SABNZBD_HOST", f"sabnzbd.{SVC}")
QBITTORRENT_HOST = os.environ.get("QBITTORRENT_HOST", f"gluetun-qbittorrent.{SVC}")
PROWLARR_URL = os.environ.get("PROWLARR_URL", f"http://prowlarr.{SVC}:9696").rstrip("/")
SABNZBD_API_KEY = os.environ["SABNZBD_API_KEY"]
PROWLARR_API_KEY = os.environ["PROWLARR_API_KEY"]
API_KEY = os.environ["BINDERY_API_KEY"]
OIDC_CLIENT_SECRET = os.environ["OIDC_CLIENT_SECRET"]
OIDC_PROVIDER = {
    "id": "pocket-id",
    "name": "Pocket ID",
    "issuer": "https://id.z7x.org",
    "client_id": "bindery",
    "scopes": ["openid", "email", "profile", "groups"],
    # Pocket ID already limits the client to these groups; this is the second lock.
    "allowed_groups": ["lab-admins", "books"],
}

EBOOK_ROOT = "/media/library/books"
AUDIOBOOK_ROOT = "/media/library/audiobooks"
CATEGORY = "books"
# MyAnonamouse counts a torrent seeded less than 72h as a hit and run. Bindery sends these with
# every grab from it (qBittorrent setShareLimits), overriding the global 10-minute / 0.1 ratio
# stop: no ratio limit, 7 days of seeding, the margin covering restarts and VPN reconnects (the
# tracker counts no seeding time while qBittorrent is down). Cleanuparr removes the torrent after
# the same 7 days (cleanuparr/app/setup.py, "Private trackers - 7 days").
MAM_SEED_MINUTES = 7 * 24 * 60

TIMEOUT = 30


def api(method: str, path: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"{API}{path}", data=data, method=method, headers={
        "Content-Type": "application/json", "X-Api-Key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')}") from None
    return json.loads(raw) if raw else None


def wait_for_bindery() -> None:
    for _ in range(90):
        try:
            api("GET", "/health")
            return
        except (urllib.error.URLError, OSError) as e:
            print(f"waiting for Bindery: {e}")
            time.sleep(10)
    sys.exit("Bindery never became reachable")


def ensure_dir(path: str) -> None:
    if not os.path.isdir(path):
        os.makedirs(path, mode=0o750)
        print(f"created {path}")


def ensure_root_folder(path: str) -> int:
    for rf in api("GET", "/rootfolder"):
        if rf["path"] == path:
            return rf["id"]
    rf = api("POST", "/rootfolder", {"path": path})
    print(f"Bindery: root folder {path} added")
    return rf["id"]


def ensure_setting(key: str, value) -> None:
    try:
        current = api("GET", f"/setting/{key}")
    except RuntimeError:
        current = None
    if current is not None and str(current.get("value")) == str(value):
        return
    api("PUT", f"/setting/{key}", {"value": str(value)})
    print(f"Bindery: setting {key} = {value}")


def ensure_download_client(name: str, type_: str, host: str, api_key: str | None = None) -> None:
    existing = {c["name"]: c for c in api("GET", "/downloadclient")}
    wanted = {"type": type_, "host": host, "category": CATEGORY, "enabled": True}
    cur = existing.get(name)
    if cur is None:
        body = {"name": name, **wanted}
        if api_key:
            body["apiKey"] = api_key
        api("POST", "/downloadclient", body)
        print(f"Bindery: download client {name} added")
        return
    changed = {k: v for k, v in wanted.items() if cur.get(k) != v}
    if api_key and not cur.get("apiKeyConfigured"):
        changed["apiKey"] = api_key
    if changed:
        api("PUT", f"/downloadclient/{cur['id']}", changed)
        print(f"Bindery: download client {name} updated: {', '.join(changed)}")


def ensure_prowlarr() -> int:
    row = next((p for p in api("GET", "/prowlarr") if p["url"] == PROWLARR_URL), None)
    if row is None:
        row = api("POST", "/prowlarr", {
            "name": "Prowlarr", "url": PROWLARR_URL, "apiKey": PROWLARR_API_KEY, "enabled": True})
        print("Bindery: Prowlarr server added")
        return row["id"]
    changed = {}
    if not row.get("enabled"):
        changed["enabled"] = True
    if not row.get("apiKeyConfigured"):
        changed["apiKey"] = PROWLARR_API_KEY
    if changed:
        api("PUT", f"/prowlarr/{row['id']}", changed)
        print("Bindery: Prowlarr server updated")
    return row["id"]


def sync_prowlarr(prowlarr_id: int) -> None:
    result = api("POST", f"/prowlarr/{prowlarr_id}/sync")
    print(f"Bindery: Prowlarr sync: added {result['added']}, removed {result['removed']}, "
          f"updated {result['updated']}")


def ensure_mam_seed_limits() -> None:
    wanted = {"seedRatio": -1, "seedTimeMinutes": MAM_SEED_MINUTES}
    for idx in api("GET", "/indexer"):
        if "myanonamouse" not in idx["name"].lower():
            continue
        if any(idx.get(k) != v for k, v in wanted.items()):
            api("PUT", f"/indexer/{idx['id']}", wanted)
            print(f"Bindery: {idx['name']} seeds {MAM_SEED_MINUTES // 1440} days, no ratio limit")


def ensure_quality_profile(id_: int, name: str, cutoff: str, items: list[str]) -> None:
    current = api("GET", f"/qualityprofile/{id_}")
    wanted_items = [{"quality": q, "allowed": True} for q in items]
    if current["cutoff"] == cutoff and current["items"] == wanted_items:
        return
    api("PUT", f"/qualityprofile/{id_}",
        {"name": name, "upgradeAllowed": current.get("upgradeAllowed", True),
         "cutoff": cutoff, "items": wanted_items})
    print(f"Bindery: quality profile {name}: cutoff {cutoff}, preferring {', '.join(items)}")


def ensure_sign_in() -> None:
    have = next((p for p in api("GET", "/auth/oidc/providers") if p["id"] == OIDC_PROVIDER["id"]), None)
    api("PUT", "/auth/oidc/providers", [{**OIDC_PROVIDER, "client_secret": OIDC_CLIENT_SECRET}])
    if have is None or any(have.get(k) != v for k, v in OIDC_PROVIDER.items() if k != "allowed_groups"):
        print("Bindery: Pocket ID sign-in configured")
    mode = api("GET", "/auth/config")["mode"]
    if mode != "enabled":
        api("PUT", "/auth/mode", {"mode": "enabled"})
        print(f"Bindery: auth mode {mode} -> enabled")


def main() -> int:
    wait_for_bindery()

    ensure_dir(EBOOK_ROOT)
    ensure_dir(AUDIOBOOK_ROOT)
    ebook_root_id = ensure_root_folder(EBOOK_ROOT)
    audiobook_root_id = ensure_root_folder(AUDIOBOOK_ROOT)
    ensure_setting("library.defaultRootFolderId", ebook_root_id)
    ensure_setting("library.defaultAudiobookRootFolderId", audiobook_root_id)
    ensure_setting("default.media_type", "both")

    ensure_download_client("SABnzbd", "sabnzbd", SABNZBD_HOST, SABNZBD_API_KEY)
    ensure_download_client("qBittorrent", "qbittorrent", QBITTORRENT_HOST)

    prowlarr_id = ensure_prowlarr()
    sync_prowlarr(prowlarr_id)
    ensure_mam_seed_limits()

    ensure_quality_profile(2, "E-Book", "epub", ["epub", "azw3", "mobi", "pdf"])
    ensure_quality_profile(3, "Audiobook", "m4b", ["m4b", "mp3", "flac", "m4a", "ogg"])

    ensure_sign_in()

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
