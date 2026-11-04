"""Set up Dispatcharr's live TV lineup and hand it to Jellyfin, idempotently.

1. Create the admin account (the shared lab login) on a fresh install, then sign in with it.
2. Settings: Reverse Proxy Auth with the X-Lab-User header, which the dispatcharr route sets for
   requests that passed the gateway's passkey check (helmrelease.yaml); Streamlink as the default
   stream profile.
3. Guides first (GUIDES): channels pick up their guide by tvg-id when they are created, and only
   from guide data that's already loaded.
4. Playlists (PLAYLISTS): each gets its exclude filters, then only the groups whose categories are
   wanted are enabled, with automatic channel creation from a fixed number up. Dispatcharr then
   keeps those channels in step with every scheduled refresh. Local-news groups (a playlist's
   "local") take only the channels whose names match LOCAL_NEWS_MATCH, a regex kept in the SOPS
   secret so the public repo doesn't say which market this is; unset, they take every market.
5. Dedupe (dedupe_channels): several playlists carry the same network (e.g. ABC News Live on
   Pluto, Samsung and Tubi); merge those channels into one, with the extras kept as backup
   streams, and delete the rest. Re-run periodically (dispatcharr-dedupe CronJob) since a
   playlist refresh can recreate a channel for an orphaned stream.
6. Jellyfin: one M3U tuner and one XMLTV guide, both Dispatcharr's own outputs, recordings on the
   media share instead of the config volume, then a guide refresh after any dedupe change.

Safe to re-run: every step compares with what's there first.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

SVC = "media.svc.cluster.local"
DISPATCHARR = os.environ.get("DISPATCHARR_URL", f"http://dispatcharr.{SVC}:9191").rstrip("/")
JELLYFIN = os.environ.get("JELLYFIN_URL", f"http://jellyfin.{SVC}:8096").rstrip("/")
USERNAME = os.environ["DISPATCHARR_USERNAME"]
PASSWORD = os.environ["DISPATCHARR_PASSWORD"]
JELLYFIN_KEY = os.environ["JELLYFIN_API_KEY"]
LOCAL_NEWS_MATCH = os.environ.get("LOCAL_NEWS_MATCH", "").strip()

PROXY_AUTH = {"enabled": True, "header": "X-Lab-User"}
# Most sources are HLS. The stock FFmpeg profile copies every rendition of the master playlist
# (-map 0: five video streams pulled for one channel); Streamlink takes only the best one.
STREAM_PROFILE = "Streamlink"

# Picking which copy of a duplicate channel (same network, several playlists) survives, and
# which order its streams fail over in. Samsung/Pluto/Tubi are first-party ad-supported TV CDNs
# with maintained, matching XMLTV guides; iptv-org aggregates arbitrary community links of
# unknown/varying reliability (it already needs a geo-block filter, see PLAYLISTS below).
SOURCE_PRIORITY = {"Samsung TV Plus": 0, "Pluto TV": 1, "Tubi": 2, "iptv-org (English)": 3}

# name -> (XMLTV url, refresh hours)
GUIDES = {
    "Pluto TV": ("https://i.mjh.nz/PlutoTV/us.xml.gz", 12),
    "Samsung TV Plus": ("https://i.mjh.nz/SamsungTVPlus/us.xml.gz", 12),
    # Generated with the Tubi playlist below, same channel ids.
    "Tubi": ("https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/tubi_epg.xml", 12),
}

# name -> url, refresh hours, {category: first channel number}, [(filter type, regex)] to exclude,
# and which of its groups are local news. A group is enabled when one of its ;-separated
# categories is wanted (iptv-org's group titles are category lists); the first wanted category in
# the dict decides its numbers. The free services' local-news groups list stations from every
# market (Pluto ~18, Tubi ~11); LOCAL_NEWS_MATCH narrows them.
PLAYLISTS = {
    # i.mjh.nz no longer publishes Pluto playlists, only the guide above; this one is generated
    # daily from it, with matching tvg-ids.
    "Pluto TV": {
        "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plutotv_us.m3u",
        "refresh": 12,
        "groups": {"News + Opinion": 100, "Local News": 200, "Sports": 300},
        "local": {"Local News"},
        "exclude": [],
    },
    # National news and weather (ABC News Live, NBC News NOW, CBS News 24/7, FOX Weather,
    # AccuWeather NOW, a few market stations).
    "Samsung TV Plus": {
        "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/samsungtvplus_us.m3u",
        "refresh": 12,
        "groups": {"News & Opinion": 400},
        "exclude": [],
    },
    "Tubi": {
        "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/tubi_all.m3u",
        "refresh": 12,
        "groups": {"Local News": 500, "National News": 520, "Weather": 540, "Sports On Now": 600},
        "local": {"Local News"},
        "exclude": [],
    },
    "iptv-org (English)": {
        "url": "https://iptv-org.github.io/iptv/languages/eng.m3u",
        "refresh": 24,
        "groups": {"News": 1000, "Sports": 2000},
        "exclude": [
            # Unplayable from here.
            ("name", r"\[Geo-blocked\]"),
            # Pluto/Samsung relays: Pluto comes from its own playlist, with its guide. Streams are
            # keyed by URL, so a duplicate here would rename the Pluto stream it shares.
            ("url", r"jmp2\.uk/"),
        ],
    },
}

M3U_URL = f"{DISPATCHARR}/output/m3u"
EPG_URL = f"{DISPATCHARR}/output/epg"
TUNER_NAME = "Dispatcharr"
RECORDING_PATH = "/media/recordings"

TIMEOUT = 120


class ApiError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def request(method: str, url: str, headers: dict, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, method=method, data=data,
                                 headers={**headers, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise ApiError(e.code, f"{method} {url.split('?')[0]}: {e.code} {e.read().decode()[:300]}") from None
    return json.loads(raw) if raw else None


class Dispatcharr:
    def __init__(self):
        self.headers: dict = {}

    def call(self, method: str, path: str, body=None):
        try:
            return request(method, f"{DISPATCHARR}/api{path}", self.headers, body)
        except ApiError as e:
            if e.code != 401 or not self.headers:
                raise
        self.sign_in()  # access tokens last 30 minutes; the waits below can outlast one
        return request(method, f"{DISPATCHARR}/api{path}", self.headers, body)

    def wait_for_server(self) -> None:
        for _ in range(120):
            try:
                self.call("GET", "/core/version/")
                return
            except (ApiError, OSError) as e:
                print(f"waiting for Dispatcharr: {e}")
                time.sleep(10)
        sys.exit(f"Dispatcharr never became reachable at {DISPATCHARR}")

    def sign_in(self) -> None:
        self.headers = {}
        if not self.call("GET", "/accounts/initialize-superuser/")["superuser_exists"]:
            self.call("POST", "/accounts/initialize-superuser/",
                      {"username": USERNAME, "password": PASSWORD, "email": ""})
            print(f"Dispatcharr: admin account {USERNAME} created")
        token = self.call("POST", "/accounts/token/", {"username": USERNAME, "password": PASSWORD})
        self.headers = {"Authorization": f"Bearer {token['access']}"}

    def wait_status(self, path: str, done: set[str], what: str) -> dict:
        """Poll an M3U account or guide source until its status is one of `done`."""
        for _ in range(180):
            item = self.call("GET", path)
            if item["status"] in done:
                return item
            if item["status"] == "error":
                sys.exit(f"{what}: {item.get('last_message')}")
            time.sleep(5)
        sys.exit(f"{what}: still {item['status']} after 15 minutes")


def ensure_settings(d: Dispatcharr) -> None:
    settings = {s["key"]: s for s in d.call("GET", "/core/settings/")}

    def put(key: str, value: dict, what: str) -> None:
        if settings[key]["value"] != value:
            d.call("PATCH", f"/core/settings/{settings[key]['id']}/", {"value": value})
            print(f"Dispatcharr: {what}")

    put("reverse_proxy_auth", PROXY_AUTH, f"reverse proxy auth on ({PROXY_AUTH['header']})")
    profile = next(p for p in d.call("GET", "/core/streamprofiles/") if p["name"] == STREAM_PROFILE)
    put("stream_settings", {**settings["stream_settings"]["value"], "default_stream_profile": profile["id"]},
        f"default stream profile {STREAM_PROFILE}")


def upsert(d: Dispatcharr, kind: str, collection: str, name: str, wanted: dict) -> tuple[dict, bool]:
    """The `kind` called `name` in `collection`, created or patched to `wanted`; True if created."""
    existing = next((i for i in d.call("GET", collection) if i["name"] == name), None)
    if existing is None:
        print(f"Dispatcharr: {kind} {name} added")
        return d.call("POST", collection, {"name": name, **wanted}), True
    if any(existing.get(k) != v for k, v in wanted.items()):
        existing = d.call("PATCH", f"{collection}{existing['id']}/", wanted)
        print(f"Dispatcharr: {kind} {name} updated")
    return existing, False


def ensure_guide(d: Dispatcharr, name: str, url: str, hours: int) -> None:
    source, created = upsert(d, "guide", "/epg/sources/", name, {
        "source_type": "xmltv", "url": url, "refresh_interval": hours, "is_active": True})
    if created or source["status"] != "success":
        source = d.wait_status(f"/epg/sources/{source['id']}/", {"success"}, f"guide {name}")
        print(f"Dispatcharr: guide {name}: {source['epg_data_count']} channels")


def ensure_filters(d: Dispatcharr, account_id: int, exclude: list[tuple[str, str]]) -> bool:
    path = f"/m3u/accounts/{account_id}/filters/"
    have = {(f["filter_type"], f["regex_pattern"]) for f in d.call("GET", path) if f["exclude"]}
    added = False
    for order, (kind, pattern) in enumerate(exclude):
        if (kind, pattern) not in have:
            d.call("POST", path, {"filter_type": kind, "regex_pattern": pattern, "exclude": True,
                                  "order": order})
            print(f"Dispatcharr: playlist {account_id}: excluding {kind} ~ {pattern}")
            added = True
    return added


def first_number(group: str, wanted: dict[str, int]) -> int | None:
    cats = {c.strip() for c in group.split(";")}
    return next((start for cat, start in wanted.items() if cat in cats), None)


def ensure_playlist(d: Dispatcharr, name: str, spec: dict) -> None:
    account, created = upsert(d, "playlist", "/m3u/accounts/", name, {
        "server_url": spec["url"], "account_type": "STD", "refresh_interval": spec["refresh"],
        "auto_enable_new_groups_live": False, "is_active": True})
    path = f"/m3u/accounts/{account['id']}/"
    # Adding an account starts its first refresh, which only loads the group list.
    account = d.wait_status(path, {"pending_setup", "success"}, f"playlist {name}")
    changed = ensure_filters(d, account["id"], spec["exclude"]) or created

    group_names = {g["id"]: g["name"] for g in d.call("GET", "/channels/groups/")}
    settings = []
    for rel in account["channel_groups"]:
        group = group_names[rel["channel_group"]]
        start = first_number(group, spec["groups"])
        want = {"channel_group": rel["channel_group"], "enabled": start is not None,
                "auto_channel_sync": start is not None, "auto_sync_channel_start": start}
        # Auto-sync creates channels only for stream names matching name_match_regex.
        match = LOCAL_NEWS_MATCH if group in spec.get("local", ()) else ""
        want["custom_properties"] = {"name_match_regex": match} if match else {}
        settings.append(want)
        have = {**rel, "custom_properties": rel.get("custom_properties") or {}}
        changed |= any(have.get(k) != v for k, v in want.items())
    if changed or account["status"] != "success":
        d.call("PATCH", f"{path}group-settings/", {"group_settings": settings})
        d.call("POST", f"/m3u/refresh/{account['id']}/", {})
        time.sleep(5)  # let the refresh leave the previous status before polling
        account = d.wait_status(path, {"success"}, f"playlist {name}")
        enabled = sorted(group_names[s["channel_group"]] for s in settings if s["enabled"])
        print(f"Dispatcharr: playlist {name}: groups {', '.join(enabled)}: {account['last_message']}")


def normalize_channel_name(name: str) -> str:
    """"ABC News Live", "ABC News Live (720p)" and "ABC News Live HD" all collapse to the same
    key, so duplicates created by different playlists for the same network are found."""
    n = name.lower()
    n = re.sub(r"\(\s*(?:\d{3,4}p|hd|sd|east|west)\s*\)", " ", n)
    n = re.sub(r"\b(?:hd|sd)\b", " ", n)
    return re.sub(r"[^a-z0-9]+", "", n)


def stream_quality(name: str) -> int:
    m = re.search(r"(\d{3,4})p", name, re.IGNORECASE)
    if m:
        return int(m.group(1))
    if re.search(r"\bhd\b", name, re.IGNORECASE):
        return 720
    if re.search(r"\bsd\b", name, re.IGNORECASE):
        return 480
    return 600  # unlabeled: assume mid-quality


def dedupe_channels(d: Dispatcharr) -> int:
    """Auto-sync (ensure_playlist) makes one channel per playlist per stream, so a network
    carried by several playlists (e.g. ABC News Live on Pluto, Samsung and Tubi) ends up as
    several channels. Merge each group of same-network channels into the most reliable one
    (SOURCE_PRIORITY, then quality), with the others' streams kept as ordered backup streams on
    it, and delete the rest.

    Dispatcharr has no native merge yet (Dispatcharr/Dispatcharr#632): a duplicate just deleted
    here can come back the next time its playlist refreshes an unmapped stream. That's fine -
    this function is idempotent and also runs from the dispatcharr-dedupe CronJob (cronjob.yaml)
    on a schedule shorter than any playlist's refresh interval, so drift gets re-merged quickly.
    """
    groups: dict[str, list[dict]] = {}
    for c in d.call("GET", "/channels/channels/"):
        groups.setdefault(normalize_channel_name(c["name"]), []).append(c)

    def rank(c: dict) -> tuple[int, int]:
        source = c.get("auto_created_by_name") or ""
        return (SOURCE_PRIORITY.get(source, len(SOURCE_PRIORITY)), -stream_quality(c["name"]))

    merged = 0
    for dupes in groups.values():
        if len(dupes) < 2:
            continue
        dupes.sort(key=rank)
        primary, losers = dupes[0], dupes[1:]
        streams = list(primary["streams"])
        for loser in losers:
            streams += [sid for sid in loser["streams"] if sid not in streams]
        if streams != primary["streams"]:
            d.call("PATCH", f"/channels/channels/{primary['id']}/", {"streams": streams})
        for loser in losers:
            d.call("DELETE", f"/channels/channels/{loser['id']}/")
        merged += len(losers)
        print(f"Dispatcharr: merged {len(dupes)} copies of {primary['name']!r} into one "
              f"channel with {len(streams)} stream(s)")
    if merged:
        print(f"Dispatcharr: dedupe removed {merged} duplicate channel(s)")
    return merged


def jellyfin(method: str, path: str, body=None):
    return request(method, f"{JELLYFIN}{path}",
                   {"Authorization": f'MediaBrowser Token="{JELLYFIN_KEY}"'}, body)


def ensure_jellyfin(force_refresh: bool = False) -> None:
    options = jellyfin("GET", "/System/Configuration/livetv")
    changed = force_refresh
    if not any(t.get("Url") == M3U_URL for t in options.get("TunerHosts", [])):
        jellyfin("POST", "/LiveTv/TunerHosts", {
            "Type": "m3u", "Url": M3U_URL, "FriendlyName": TUNER_NAME, "TunerCount": 0,
            # Dispatcharr already shares one upstream connection per channel; this lets Jellyfin
            # serve several viewers of a channel from one Dispatcharr stream too.
            "AllowStreamSharing": True, "AllowHWTranscoding": False,
            "AllowFmp4TranscodingContainer": False, "EnableStreamLooping": False,
            "IgnoreDts": True, "ReadAtNativeFramerate": False, "ImportFavoritesOnly": False})
        print(f"Jellyfin: M3U tuner {M3U_URL} added")
        changed = True
    if not any(p.get("Path") == EPG_URL for p in options.get("ListingProviders", [])):
        jellyfin("POST", "/LiveTv/ListingProviders", {
            "Type": "xmltv", "Path": EPG_URL, "EnableAllTuners": True})
        print(f"Jellyfin: XMLTV guide {EPG_URL} added")
        changed = True
    options = jellyfin("GET", "/System/Configuration/livetv")
    if options.get("RecordingPath") != RECORDING_PATH:
        options["RecordingPath"] = RECORDING_PATH
        jellyfin("POST", "/System/Configuration/livetv", options)
        print(f"Jellyfin: recordings go to {RECORDING_PATH}")
    if changed:
        task = next(t for t in jellyfin("GET", "/ScheduledTasks") if t.get("Key") == "RefreshGuide")
        jellyfin("POST", f"/ScheduledTasks/Running/{task['Id']}")
        print("Jellyfin: guide refresh started")


def main() -> int:
    d = Dispatcharr()
    d.wait_for_server()
    d.sign_in()
    ensure_settings(d)
    for name, (url, hours) in GUIDES.items():
        ensure_guide(d, name, url, hours)
    for name, spec in PLAYLISTS.items():
        ensure_playlist(d, name, spec)
    merged = dedupe_channels(d)
    ensure_jellyfin(force_refresh=merged > 0)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
