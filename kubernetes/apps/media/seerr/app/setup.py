"""Codify Seerr's Jellyfin/Radarr/Sonarr connections, library sync and anime override rule,
idempotently, so a fresh install converges without clicking through the first-run wizard by hand.

1. API key: Seerr (server/lib/settings/index.ts, generateApiKey()) accepts a preset key through the
   API_KEY env var -- set on first boot and re-asserted (overwritten) on every boot after if it ever
   drifts from the env value. The seerr HelmRelease sets API_KEY from the jellyfin-plugins Secret's
   SEERR_API_KEY (the same value Jellyfin Enhanced's Seerr integration already uses, originally
   copied by hand from a live settings.json -- see the comment in jellyfin/app/plugins-secret.sops.yaml).
   This script reuses that same value as its own X-Api-Key, so there's exactly one Seerr API key in
   the whole cluster, not a second copy on a PVC.
2. First-admin gate: Seerr's auth middleware (server/middleware/auth.ts) only turns a matching
   X-Api-Key into req.user by looking up User id 1 -- and that row doesn't exist until someone signs
   in once through Seerr's own login page with a Jellyfin account (Seerr has no API for creating that
   first admin; it's a human clicking "Sign in with Jellyfin"). Until then, every /api/v1/settings/*
   call this script makes comes back 403, which it treats as "not initialized yet" and exits 0 so the
   Job goes green without looping forever on backoffLimit.
3. Jellyfin connection: POST /api/v1/settings/jellyfin tests the connection (and on success Seerr
   overwrites its own `name`/`serverId` from Jellyfin's System Info, so those aren't set here).
   Library sync: GET /api/v1/settings/jellyfin/library?sync=true&enable=<ids> is the only endpoint
   that both re-reads Jellyfin's library list *and* sets which are enabled -- critically, calling it
   with no `enable` query param disables every library as a side effect (settingsRoutes.get
   '/jellyfin/library', server/routes/settings/index.ts), so this script only ever calls it with the
   full desired id list attached, and only when the enabled set actually needs to change (read via
   the side-effect-free GET /api/v1/settings/jellyfin instead). The library ids it enables come from
   Jellyfin's own /Library/VirtualFolders (ItemId), which is confirmed to be the same id Seerr uses
   as Library.id -- not from Seerr's sync response, which would be a chicken-and-egg problem for
   libraries Seerr has never seen before (Anime, Anime Movies).
4. Radarr/Sonarr: POST .../test resolves quality profile ids and root folders by name/path against
   the live *arr, since Seerr stores the *arr's own numeric profile id, not a name. The quality
   profile "[Anime] Remux-1080p" happens to exist (under the same name) in both Radarr and Sonarr --
   that's this cluster's TRaSH-derived profile set, not a Seerr convention.
5. Anime override rule (server/entity/OverrideRule.ts + server/entity/MediaRequest.ts): genre "16" is
   TMDb's Animation genre id, keywords "210024" is ANIME_KEYWORD_ID -- the exact pair Seerr's own
   isMovieAnime logic already uses, so this rule fires only when Seerr itself would already call
   something anime. Matched rule is routed to Radarr's anime profile/root instead of the default
   movie profile/root.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SVC = "media.svc.cluster.local"
SEERR = os.environ.get("SEERR_URL", f"http://seerr.{SVC}:5055").rstrip("/")
API = f"{SEERR}/api/v1"
SEERR_API_KEY = os.environ["SEERR_API_KEY"]

JELLYFIN_HOST = os.environ.get("JELLYFIN_HOST", f"jellyfin.{SVC}")
JELLYFIN_PORT = 8096
JELLYFIN_API_KEY = os.environ["JELLYFIN_API_KEY"]
JELLYFIN_EXTERNAL_HOSTNAME = "https://media.z7x.org"
LIBRARY_NAMES = ["Movies", "Shows", "Anime", "Anime Movies"]

RADARR_HOST, RADARR_PORT = f"radarr.{SVC}", 7878
RADARR_API_KEY = os.environ["RADARR_API_KEY"]
RADARR_PROFILE_NAME = "UHD Bluray + WEB"
RADARR_ANIME_PROFILE_NAME = "[Anime] Remux-1080p"
RADARR_MOVIE_ROOT = "/media/library/movies"
RADARR_ANIME_MOVIE_ROOT = "/media/library/anime-movies"
RADARR_EXTERNAL_URL = "https://radarr.z7x.org"

SONARR_HOST, SONARR_PORT = f"sonarr.{SVC}", 8989
SONARR_API_KEY = os.environ["SONARR_API_KEY"]
SONARR_PROFILE_NAME = "WEB-2160p (Combined)"
SONARR_ANIME_PROFILE_NAME = "[Anime] Remux-1080p"
SONARR_TV_ROOT = "/media/library/tv"
SONARR_ANIME_ROOT = "/media/library/anime"
SONARR_EXTERNAL_URL = "https://sonarr.z7x.org"

# TMDb Animation genre id and ANIME_KEYWORD_ID (server/api/themoviedb/constants.ts) -- the same
# pair Seerr's own request pipeline uses to decide a title is anime.
ANIME_GENRE = "16"
ANIME_KEYWORD = "210024"

APPLICATION_URL = "https://requests.z7x.org"

TIMEOUT = 30


class ApiError(Exception):
    pass


def api(method: str, path: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"{API}{path}", data=data, method=method, headers={
        "Content-Type": "application/json", "X-Api-Key": SEERR_API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise ApiError(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')[:400]}") from None
    return json.loads(raw) if raw else None


def jellyfin_api(path: str):
    req = urllib.request.Request(f"http://{JELLYFIN_HOST}:{JELLYFIN_PORT}{path}",
                                  headers={"Authorization": f"MediaBrowser Token={JELLYFIN_API_KEY}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def wait_for_seerr() -> None:
    for _ in range(90):
        try:
            api("GET", "/status")
            return
        except (ApiError, urllib.error.URLError, OSError) as e:
            print(f"waiting for Seerr: {e}")
            time.sleep(10)
    sys.exit("Seerr never became reachable")


def is_initialized() -> bool:
    """True once a Seerr admin exists (User id 1) -- req.user only gets set for a matching
    X-Api-Key if that row is found (server/middleware/auth.ts), so a 403 here means nobody has
    signed in with Jellyfin yet."""
    try:
        api("GET", "/settings/main")
        return True
    except ApiError as e:
        if " -> 403:" in str(e):
            return False
        raise


def ensure_main() -> None:
    current = api("GET", "/settings/main")
    desired = {"localLogin": False, "applicationUrl": APPLICATION_URL}
    changed = {k: v for k, v in desired.items() if current.get(k) != v}
    if not changed:
        return
    api("POST", "/settings/main", changed)
    print(f"Seerr: main settings: {', '.join(f'{k}={v}' for k, v in changed.items())}")


def ensure_jellyfin() -> None:
    current = api("GET", "/settings/jellyfin")
    desired = {
        "ip": JELLYFIN_HOST, "port": JELLYFIN_PORT, "useSsl": False, "urlBase": "",
        "externalHostname": JELLYFIN_EXTERNAL_HOSTNAME, "apiKey": JELLYFIN_API_KEY,
    }
    changed = {k: v for k, v in desired.items() if current.get(k) != v}
    if changed:
        api("POST", "/settings/jellyfin", desired)
        print(f"Seerr: Jellyfin connection: {', '.join(changed)}")

    # Library sync/enable: a plain GET .../jellyfin/library (no "enable") disables every library
    # as a side effect, so the enabled set is always read from the side-effect-free settings
    # endpoint above, and the sync+enable call below only runs when it would actually change
    # something.
    folders = jellyfin_api("/Library/VirtualFolders")
    by_name = {f["Name"]: f["ItemId"] for f in folders}
    missing = [n for n in LIBRARY_NAMES if n not in by_name]
    if missing:
        sys.exit(f"Jellyfin has no library named {', '.join(missing)}")
    desired_ids = sorted(by_name[n] for n in LIBRARY_NAMES)

    current = api("GET", "/settings/jellyfin")
    current_enabled = sorted(lib["id"] for lib in current["libraries"] if lib["enabled"])
    if current_enabled == desired_ids:
        return
    enabled = api("GET", f"/settings/jellyfin/library?sync=true&enable={','.join(desired_ids)}")
    names = sorted(lib["name"] for lib in enabled if lib["enabled"])
    print(f"Seerr: Jellyfin libraries enabled: {', '.join(names)}")


def test_dvr(kind: str, host: str, port: int, api_key: str) -> dict:
    """POST .../test against a bare connection (no Seerr-side id yet) to resolve quality
    profile ids and root folders live from the *arr itself."""
    return api("POST", f"/settings/{kind}/test", {
        "hostname": host, "port": port, "apiKey": api_key, "useSsl": False, "baseUrl": "",
    })


def profile_id(profiles: list, name: str, kind: str) -> int:
    for p in profiles:
        if p["name"] == name:
            return p["id"]
    sys.exit(f"{kind}: no quality profile named {name!r}")


def ensure_service(kind: str, host: str, port: int, api_key: str, desired: dict) -> int:
    """Create or update the single Radarr/Sonarr instance, matched by hostname. Returns its
    Seerr-side service id (0 for the first/only instance, used by the override rule)."""
    existing = api("GET", f"/settings/{kind}")
    current = next((s for s in existing if s["hostname"] == host), None)
    if current is None:
        created = api("POST", f"/settings/{kind}", {"name": kind.capitalize(), "hostname": host,
                                                      "port": port, "apiKey": api_key, **desired})
        print(f"Seerr: {kind.capitalize()} server added")
        return created["id"]

    full_desired = {"name": current["name"], "hostname": host, "port": port, "apiKey": api_key,
                     **desired}
    changed = {k: v for k, v in full_desired.items() if current.get(k) != v}
    if changed:
        api("PUT", f"/settings/{kind}/{current['id']}", {**current, **full_desired})
        print(f"Seerr: {kind.capitalize()} server: {', '.join(changed)}")
    return current["id"]


def ensure_radarr() -> int:
    test = test_dvr("radarr", RADARR_HOST, RADARR_PORT, RADARR_API_KEY)
    pid = profile_id(test["profiles"], RADARR_PROFILE_NAME, "Radarr")
    desired = {
        "useSsl": False, "baseUrl": "", "activeProfileId": pid,
        "activeProfileName": RADARR_PROFILE_NAME, "activeDirectory": RADARR_MOVIE_ROOT,
        "minimumAvailability": "released", "tags": [], "is4k": False, "isDefault": True,
        "externalUrl": RADARR_EXTERNAL_URL, "syncEnabled": True, "preventSearch": False,
        "tagRequests": False, "overrideRule": [],
    }
    return ensure_service("radarr", RADARR_HOST, RADARR_PORT, RADARR_API_KEY, desired)


def ensure_sonarr() -> int:
    test = test_dvr("sonarr", SONARR_HOST, SONARR_PORT, SONARR_API_KEY)
    pid = profile_id(test["profiles"], SONARR_PROFILE_NAME, "Sonarr")
    anime_pid = profile_id(test["profiles"], SONARR_ANIME_PROFILE_NAME, "Sonarr")
    desired = {
        "useSsl": False, "baseUrl": "", "activeProfileId": pid,
        "activeProfileName": SONARR_PROFILE_NAME, "activeDirectory": SONARR_TV_ROOT,
        "activeAnimeProfileId": anime_pid, "activeAnimeProfileName": SONARR_ANIME_PROFILE_NAME,
        "activeAnimeDirectory": SONARR_ANIME_ROOT, "seriesType": "standard",
        "animeSeriesType": "anime", "tags": [], "animeTags": [], "is4k": False, "isDefault": True,
        "enableSeasonFolders": True, "monitorNewItems": "all", "externalUrl": SONARR_EXTERNAL_URL,
        "syncEnabled": True, "preventSearch": False, "tagRequests": False, "overrideRule": [],
    }
    return ensure_service("sonarr", SONARR_HOST, SONARR_PORT, SONARR_API_KEY, desired)


def ensure_anime_movie_override_rule(radarr_id: int) -> None:
    test = test_dvr("radarr", RADARR_HOST, RADARR_PORT, RADARR_API_KEY)
    anime_pid = profile_id(test["profiles"], RADARR_ANIME_PROFILE_NAME, "Radarr")
    desired = {
        "radarrServiceId": radarr_id, "sonarrServiceId": None, "users": None, "language": None,
        "genre": ANIME_GENRE, "keywords": ANIME_KEYWORD, "profileId": anime_pid,
        "rootFolder": RADARR_ANIME_MOVIE_ROOT, "tags": None,
    }
    rules = api("GET", "/overrideRule")
    current = next((r for r in rules if r.get("radarrServiceId") == radarr_id
                     and r.get("genre") == ANIME_GENRE and r.get("keywords") == ANIME_KEYWORD), None)
    if current is None:
        api("POST", "/overrideRule", desired)
        print("Seerr: anime-movie override rule added")
        return
    changed = {k: v for k, v in desired.items() if current.get(k) != v}
    if changed:
        api("PUT", f"/overrideRule/{current['id']}", desired)
        print(f"Seerr: anime-movie override rule: {', '.join(changed)}")


def main() -> int:
    wait_for_seerr()
    if not is_initialized():
        print("Seerr has no admin yet -- sign in with Jellyfin once at https://requests.z7x.org, "
              "then re-run this Job")
        return 0

    ensure_main()
    ensure_jellyfin()
    radarr_id = ensure_radarr()
    ensure_sonarr()
    ensure_anime_movie_override_rule(radarr_id)

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
