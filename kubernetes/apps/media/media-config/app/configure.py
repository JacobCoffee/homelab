"""Wire the download side of the media stack to TRaSH Guides settings, idempotently.

SABnzbd   Folders: completed jobs land in /media/downloads/usenet/<category>, in-progress ones in
          /media/downloads/usenet/incomplete (same NFS tree as the library, so imports hardlink).
          Categories movies/tv/music/books. TRaSH "Switches": unwanted-extension blacklist that
          fails the job (fake .exe/.scr releases), encrypted rars abort instead of pausing,
          duplicates are tagged, folders inside archives are flattened, final filenames
          deobfuscated. The Newshosting server, then a connection test against it.
qBittorrent  TRaSH Saving Management: default save path /media/downloads/torrents (its default was
          /config/Downloads, on the config PVC and not hardlinkable into the library), Automatic
          torrent management with relocate-on-change, categories movies/tv/music/books.
Prowlarr  Sonarr/Radarr/Lidarr as full-sync applications, so every indexer added here appears in
          them by itself; NZBFinder and Anime Tosho (the animetosho.net successor; the original
          animetosho.org shut its feed down in October 2026) as indexers.
*arrs     SABnzbd and qBittorrent as download clients (their category per app), the
          /media/library/<kind> root folder, and a Jellyfin connection that refreshes the library
          on every import, upgrade, rename or delete (Jellyfin can't watch the NFS tree itself).
          Downloads and library are one NFS filesystem, so usenet imports are moves and torrent
          imports hardlinks: a seeding torrent and its library copy share the same blocks. Sonarr
          and Radarr also get a second, anime-only root folder (/media/library/anime,
          /media/library/anime-movies), created on the NFS share first if missing.

Lidarr    A lossless-first quality profile ("Lossless + High-Bitrate Fallback"): FLAC/ALAC/APE/
          WavPack preferred, falling back to MP3-320/MP3-VBR-V0/AAC-VBR/AAC-320 only when nothing
          lossless exists, cutoff set to Lossless so Lidarr keeps upgrading a lossy grab. Custom
          formats score CD and tagged-lossless releases above WEB and score Vinyl out entirely.
          TRaSH Guides has no Lidarr guide of its own and defers to the Servarr Wiki (see
          configure_lidarr_quality), which this profile is sourced from. Set as the
          /media/library/music root folder's default and applied to any artist still on one of
          Lidarr's built-in profiles. Track renaming is also turned on, using the instance's
          existing Jellyfin-friendly Artist/Album (Year)/Track naming.

Each step reads the current state and writes only what differs, so re-runs are safe. A step whose
credential is empty (NEWSHOSTING_USERNAME/PASSWORD, NZBFINDER_API_KEY, JELLYFIN_API_KEY) is
skipped with a note: scripts/media-secrets.py fills the first three from the vault,
JELLYFIN_API_KEY comes from the jellyfin-sso Secret (scripts/jellyfin-sso-key.py).
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SVC = "media.svc.cluster.local"
SAB_URL = f"http://sabnzbd.{SVC}:8080"
PROWLARR_URL = f"http://prowlarr.{SVC}:9696"
# Same pod network as the *arrs, which qBittorrent lets in without a login (its auth subnet
# whitelist is the pod CIDR; gluetun-qbittorrent's HelmRelease).
QBIT_URL = f"http://gluetun-qbittorrent.{SVC}:8080"
TORRENTS = "/media/downloads/torrents"
SAB_KEY = os.environ["SABNZBD_API_KEY"]
PROWLARR_KEY = os.environ["PROWLARR_API_KEY"]

# name, url, API version, key, SABnzbd category field + value, root folder
ARRS = [
    ("Sonarr", f"http://sonarr.{SVC}:8989", "v3", os.environ["SONARR_API_KEY"], "tvCategory", "tv", "/media/library/tv"),
    ("Radarr", f"http://radarr.{SVC}:7878", "v3", os.environ["RADARR_API_KEY"], "movieCategory", "movies", "/media/library/movies"),
    ("Lidarr", f"http://lidarr.{SVC}:8686", "v1", os.environ["LIDARR_API_KEY"], "musicCategory", "music", "/media/library/music"),
]
# Sonarr and Radarr only (Lidarr has no concept of anime): a second root folder for anime,
# alongside the regular one above, created by ensure_library_dirs() below.
ANIME_ROOTS = [
    ("Sonarr", f"http://sonarr.{SVC}:8989", "v3", os.environ["SONARR_API_KEY"], "/media/library/anime"),
    ("Radarr", f"http://radarr.{SVC}:7878", "v3", os.environ["RADARR_API_KEY"], "/media/library/anime-movies"),
]
JELLYFIN_KEY = os.environ.get("JELLYFIN_API_KEY", "")
# Events after which each app asks Jellyfin to rescan what changed.
JELLYFIN_EVENTS = {
    "Sonarr": ["onDownload", "onUpgrade", "onImportComplete", "onRename", "onSeriesDelete",
               "onEpisodeFileDelete", "onEpisodeFileDeleteForUpgrade"],
    "Radarr": ["onDownload", "onUpgrade", "onRename", "onMovieDelete", "onMovieFileDelete",
               "onMovieFileDeleteForUpgrade"],
    "Lidarr": ["onReleaseImport", "onUpgrade", "onRename", "onArtistDelete", "onAlbumDelete",
               "onTrackRetag"],
}

USENET = "/media/downloads/usenet"
CATEGORIES = ["movies", "tv", "music", "books"]
# TRaSH Guides SABnzbd Basic Setup, "Prevent unwanted extensions".
UNWANTED = (
    "ade, adp, app, application, appref-ms, asp, aspx, asx, bas, bat, bgi, cab, cer, chm, cmd, cnt, "
    "com, cpl, crt, csh, der, diagcab, exe, fxp, gadget, grp, hlp, hpj, hta, htc, inf, ins, iso, isp, "
    "its, jar, jnlp, js, jse, ksh, lnk, mad, maf, mag, mam, maq, mar, mas, mat, mau, mav, maw, mcf, "
    "mda, mdb, mde, mdt, mdw, mdz, msc, msh, msh1, msh2, mshxml, msh1xml, msh2xml, msi, msp, mst, msu, "
    "ops, osd, pcd, pif, pl, plg, prf, prg, printerexport, ps1, ps1xml, ps2, ps2xml, psc1, psc2, psd1, "
    "psdm1, pst, py, pyc, pyo, pyw, pyz, pyzw, reg, scf, scr, sct, shb, shs, sln, theme, tmp, url, vb, "
    "vbe, vbp, vbs, vcxproj, vhd, vhdx, vsmacros, vsw, webpnp, website, ws, wsc, wsf, wsh, xbap, xll, xnk"
).split(", ")
# complete_dir before download_dir: SABnzbd refuses a temporary folder that contains the
# completed one, and the default complete_dir (/config/Downloads/complete) is on the config PVC.
SAB_MISC = {
    "complete_dir": USENET,
    "download_dir": f"{USENET}/incomplete",
    "unwanted_extensions": UNWANTED,
    "unwanted_extensions_mode": 0,  # blacklist
    "action_on_unwanted_extensions": 2,  # fail job (to history), so the *arr picks another release
    "pause_on_pwrar": 2,  # encrypted rar: fail instead of pausing
    "no_dupes": 4,  # duplicate download: tag
    "flat_unpack": 1,  # ignore folders inside archives (*arrs don't look in subfolders)
    "deobfuscate_final_filenames": 1,
}
NEWSHOSTING = {
    "displayname": "Newshosting",
    "host": "news.newshosting.com",
    "port": 563,
    "ssl": 1,
    "ssl_verify": 3,  # strict
    "connections": int(os.environ.get("NEWSHOSTING_CONNECTIONS", "30")),
    "priority": 0,
    "enable": 1,
}

# Source: Servarr Wiki Lidarr Tips and Tricks -> Custom Formats and -> Quality definitions for
# FLAC (TRaSH Guides has no Lidarr guide of its own and defers to this wiki, see Third-Party Apps
# & Guides). https://wiki.servarr.com/lidarr/tips-and-tricks#custom-formats
LIDARR_PROFILE_NAME = "Lossless + High-Bitrate Fallback"
# Lidarr's own built-in default profiles, migrated away from once the profile above exists.
LIDARR_LEGACY_PROFILES = ("Any", "Standard", "Lossless")
# name, specification implementation, release-title regex
LIDARR_CUSTOM_FORMATS = [
    ("CD", "ReleaseTitleSpecification", r"\bCD\b"),
    ("WEB", "ReleaseTitleSpecification", r"\bWEB\b"),
    ("Lossless", "ReleaseTitleSpecification", r"\blossless\b"),
    ("Vinyl", "ReleaseTitleSpecification", r"\bVinyl\b"),
]
# CD and tagged-lossless outrank WEB; Vinyl is scored out rather than merely deprioritised.
LIDARR_CF_SCORES = {"CD": 10, "Lossless": 10, "WEB": 5, "Vinyl": -10000}
# Catches single-file CUE+FLAC rips, which present as one oversized file instead of split tracks.
LIDARR_QUALITY_DEFINITION_MAX = {"FLAC": 1400, "FLAC 24bit": 1495}  # kbps
# MP3-320/MP3-VBR-V0/AAC-VBR/AAC-320 fallback when nothing lossless exists; cutoff is Lossless, so
# Lidarr keeps upgrading from a lossy grab until a FLAC/ALAC/APE/WavPack release appears.
LIDARR_ALLOWED_QUALITY_GROUPS = ("High Quality Lossy", "Lossless")
LIDARR_CUTOFF_GROUP = "Lossless"


def call(method: str, url: str, key: str | None = None, body=None, form: dict | None = None):
    headers = {"Accept": "application/json"}
    data = None
    if key:
        headers["X-Api-Key"] = key
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form, doseq=True).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        sys.exit(f"{method} {url.split('?')[0]} -> {err.code}: {err.read().decode(errors='replace')[:2000]}")
    return json.loads(raw) if raw else None


def sab(mode: str, **params):
    out = call("POST", f"{SAB_URL}/api", form={"mode": mode, "output": "json", "apikey": SAB_KEY, **params})
    if isinstance(out, dict) and out.get("status") is False:
        sys.exit(f"SABnzbd {mode} {params.get('section', '')}/{params.get('keyword') or params.get('name', '')}: {out.get('error')}")
    return out


def same(current, wanted) -> bool:
    if isinstance(wanted, list):
        return sorted(str(v) for v in current or []) == sorted(wanted)
    if isinstance(wanted, int):
        return int(current or 0) == wanted
    return str(current) == str(wanted)


def configure_sabnzbd() -> None:
    config = sab("get_config")["config"]
    for key, wanted in SAB_MISC.items():
        if not same(config["misc"].get(key), wanted):
            value = ",".join(wanted) if isinstance(wanted, list) else wanted
            sab("set_config", section="misc", keyword=key, value=value)
            print(f"SABnzbd: {key} set")
    existing = {c["name"]: c for c in config.get("categories") or []}
    for cat in CATEGORIES:
        if existing.get(cat, {}).get("dir") != cat:
            sab("set_config", section="categories", name=cat, dir=cat)
            print(f"SABnzbd: category {cat} -> {USENET}/{cat}")

    user, password = os.environ.get("NEWSHOSTING_USERNAME", ""), os.environ.get("NEWSHOSTING_PASSWORD", "")
    if not (user and password):
        print("SABnzbd: Newshosting credentials not set yet, server skipped")
        return
    # The password can't be read back (SABnzbd masks it), so the server is written on every run;
    # this Job only runs when its script or credentials change.
    sab("set_config", section="servers", name="newshosting", username=user, password=password, **NEWSHOSTING)
    test = sab("config", name="test_server", server="newshosting", username=user, password=password,
               **{k: NEWSHOSTING[k] for k in ("host", "port", "ssl", "ssl_verify", "connections")})
    result = (test or {}).get("value", {})
    if not result.get("result"):
        sys.exit(f"SABnzbd: Newshosting connection test failed: {result.get('message')}")
    print(f"SABnzbd: Newshosting server set, test: {result.get('message')}")


def fields(item: dict) -> dict:
    return {f["name"]: f.get("value") for f in item["fields"]}


def set_fields(item: dict, values: dict) -> dict:
    for f in item["fields"]:
        if f["name"] in values:
            f["value"] = values[f["name"]]
    return item


def upsert(kind: str, base: str, key: str, existing: list, schema_item: dict, name: str,
           top: dict, values: dict) -> None:
    """Create `name` from its schema entry, or update the existing one if any wanted value differs."""
    current = next((i for i in existing if i["name"] == name), None)
    if current is None:
        item = set_fields(schema_item, values)
        item.update(top, name=name)
        item.pop("id", None)
        call("POST", f"{base}", key, item)
        print(f"{kind}: {name} added")
        return
    # The *arrs and Prowlarr read secrets back as "********": those count as unchanged (a PUT
    # makes the app re-test the connection, and it sends the real value anyway).
    have = fields(current)
    if all(current.get(k) == v for k, v in top.items()) and all(
        have.get(k) in (v, "********") for k, v in values.items()
    ):
        return
    set_fields(current, values).update(top)
    call("PUT", f"{base}/{current['id']}", key, current)
    print(f"{kind}: {name} updated")


def configure_prowlarr() -> None:
    api = f"{PROWLARR_URL}/api/v1"
    schema = call("GET", f"{api}/applications/schema", PROWLARR_KEY)
    existing = call("GET", f"{api}/applications", PROWLARR_KEY)
    for name, url, _, key, *_ in ARRS:
        item = next(s for s in schema if s["implementation"] == name)
        upsert("Prowlarr app", f"{api}/applications", PROWLARR_KEY, existing, item, name,
               {"syncLevel": "fullSync"},
               {"prowlarrUrl": PROWLARR_URL, "baseUrl": url, "apiKey": key})

    schema = call("GET", f"{api}/indexer/schema", PROWLARR_KEY)
    existing = call("GET", f"{api}/indexer", PROWLARR_KEY)
    profile = call("GET", f"{api}/appprofile", PROWLARR_KEY)[0]["id"]
    indexers = [
        # (schema preset name, indexer name, field values, credential that must be present)
        ("AnimeTosho (Usenet)", "Anime Tosho", {"baseUrl": "https://feed.animetosho.net", "apiKey": None}, "-"),
        ("NZBFinder", "NZBFinder", {"apiKey": os.environ.get("NZBFINDER_API_KEY", "")}, "NZBFINDER_API_KEY"),
    ]
    for preset, name, values, needs in indexers:
        if needs != "-" and not os.environ.get(needs):
            print(f"Prowlarr: {needs} not set yet, {name} skipped")
            continue
        item = next(s for s in schema if s["name"] == preset and s["protocol"] == "usenet")
        upsert("Prowlarr indexer", f"{api}/indexer", PROWLARR_KEY, existing, item, name,
               {"enable": True, "appProfileId": profile, "priority": 25}, values)


def configure_qbittorrent() -> None:
    api = f"{QBIT_URL}/api/v2"
    wanted = {
        "save_path": TORRENTS,
        "temp_path_enabled": False,
        "auto_tmm_enabled": True,  # Automatic: downloads go to the category's folder
        "torrent_changed_tmm_enabled": True,  # relocate when a torrent's category changes
        "save_path_changed_tmm_enabled": True,  # ... when the default save path changes
        "category_changed_tmm_enabled": True,  # ... when a category's save path changes
        # Minimal seeding (public trackers, no ratio to keep): stop at ratio 0.1 or 10 minutes
        # seeding, whichever comes first. Stop, not remove: Sonarr/Radarr/Lidarr then remove the
        # stopped torrent once it's imported (their "Remove Completed"), so qBittorrent never
        # deletes a download an arr still needs. The library copy is a hardlink and stays.
        "share_limits_mode": "MatchAny",
        "max_ratio_enabled": True,
        "max_ratio": 0.1,
        "max_seeding_time_enabled": True,
        "max_seeding_time": 10,  # minutes
        "max_ratio_act": 0,  # ShareLimitAction::Stop
    }
    prefs = call("GET", f"{api}/app/preferences")
    changed = {k: v for k, v in wanted.items() if prefs.get(k) != v}
    if changed:
        call("POST", f"{api}/app/setPreferences", form={"json": json.dumps(changed)})
        print(f"qBittorrent: {', '.join(changed)} set")
    categories = call("GET", f"{api}/torrents/categories") or {}
    for cat in CATEGORIES:
        path = f"{TORRENTS}/{cat}"
        if cat not in categories:
            call("POST", f"{api}/torrents/createCategory", form={"category": cat, "savePath": path})
        elif categories[cat].get("savePath") != path:
            call("POST", f"{api}/torrents/editCategory", form={"category": cat, "savePath": path})
        else:
            continue
        print(f"qBittorrent: category {cat} -> {path}")


def add_root_folder(name: str, api: str, key: str, root: str, extra: dict | None = None) -> None:
    extra = extra or {}
    existing = next((r for r in call("GET", f"{api}/rootfolder", key) if r["path"].rstrip("/") == root), None)
    if existing is None:
        call("POST", f"{api}/rootfolder", key, {"path": root, **extra})
        print(f"{name}: root folder {root} added")
        return
    if any(existing.get(k) != v for k, v in extra.items()):
        existing.update(extra)
        call("PUT", f"{api}/rootfolder/{existing['id']}", key, existing)
        print(f"{name}: root folder {root} updated")


def spec_from_schema(schema: dict, name: str, value: str) -> dict:
    """Build one Custom Format specification by cloning its implementation schema (from GET
    .../customformat/schema) and filling in the "value" field (a title regex)."""
    spec = {k: v for k, v in schema.items() if k != "presets"}
    spec["name"] = name
    spec["fields"] = [dict(f, value=value) if f["name"] == "value" else dict(f) for f in schema["fields"]]
    return spec


def configure_lidarr_custom_formats(api: str, key: str) -> dict[str, int]:
    schemas = {s["implementation"]: s for s in call("GET", f"{api}/customformat/schema", key)}
    existing = {c["name"]: c for c in call("GET", f"{api}/customformat", key)}
    ids: dict[str, int] = {}
    for cf_name, implementation, regex in LIDARR_CUSTOM_FORMATS:
        spec = spec_from_schema(schemas[implementation], cf_name, regex)
        current = existing.get(cf_name)
        if current is None:
            created = call("POST", f"{api}/customformat", key,
                            {"name": cf_name, "includeCustomFormatWhenRenaming": False, "specifications": [spec]})
            ids[cf_name] = created["id"]
            print(f"Lidarr: custom format {cf_name} added")
            continue
        ids[cf_name] = current["id"]
        have = [fields(s).get("value") for s in current.get("specifications", [])]
        if have != [regex]:
            current["specifications"] = [spec]
            call("PUT", f"{api}/customformat/{current['id']}", key, current)
            print(f"Lidarr: custom format {cf_name} updated")
    return ids


def quality_profile_items(schema_items: list) -> tuple[list, int]:
    """Clone the profile schema's quality groups, allow only the lossless and high-bitrate-lossy
    groups (MP3-320/MP3-VBR-V0/AAC-VBR/AAC-320, up to FLAC/FLAC 24bit/ALAC/APE/WavPack), and
    return the cutoff group's id."""
    items = json.loads(json.dumps(schema_items))
    cutoff_id = None
    for item in items:
        group_name = item.get("name") or item["quality"]["name"]
        allowed = group_name in LIDARR_ALLOWED_QUALITY_GROUPS
        item["allowed"] = allowed
        for sub in item.get("items", []):
            sub["allowed"] = allowed
        if group_name == LIDARR_CUTOFF_GROUP:
            cutoff_id = item["id"]
    return items, cutoff_id


def configure_lidarr_quality(api: str, key: str) -> int:
    """Quality definitions, custom formats and the quality profile for a lossless-first music
    library: FLAC (and other lossless formats) preferred, upgrading from MP3-320/MP3-VBR-V0/
    AAC-VBR/AAC-320 only when nothing lossless is available -- the cutoff is the Lossless group,
    so Lidarr keeps searching until it lands a lossless release. CD and tagged-lossless releases
    outscore WEB; Vinyl rips are scored out entirely. TRaSH Guides has no Lidarr guide of its own
    (github.com/TRaSH-Guides/Guides/blob/master/docs/Lidarr/index.md) and points to the Servarr
    Wiki Lidarr Community Guide instead; this profile is sourced from there. Returns the
    profile's id."""
    for d in call("GET", f"{api}/qualitydefinition", key):
        wanted_max = LIDARR_QUALITY_DEFINITION_MAX.get(d["quality"]["name"])
        if wanted_max is not None and d.get("maxSize") != wanted_max:
            d["maxSize"] = wanted_max
            call("PUT", f"{api}/qualitydefinition/{d['id']}", key, d)
            print(f"Lidarr: quality definition {d['quality']['name']} max size -> {wanted_max}kbps")

    cf_ids = configure_lidarr_custom_formats(api, key)
    format_items = [{"format": cf_ids[n], "name": n, "score": s} for n, s in LIDARR_CF_SCORES.items()]
    schema = call("GET", f"{api}/qualityprofile/schema", key)
    items, cutoff_id = quality_profile_items(schema["items"])
    wanted = {"name": LIDARR_PROFILE_NAME, "upgradeAllowed": True, "cutoff": cutoff_id, "items": items,
              "minFormatScore": 0, "cutoffFormatScore": 0, "formatItems": format_items}

    profiles = call("GET", f"{api}/qualityprofile", key)
    current = next((p for p in profiles if p["name"] == LIDARR_PROFILE_NAME), None)
    if current is None:
        created = call("POST", f"{api}/qualityprofile", key, wanted)
        print(f"Lidarr: quality profile {LIDARR_PROFILE_NAME} added")
        return created["id"]

    def signature(items: list) -> list:
        return [(i.get("name") or i["quality"]["name"], i["allowed"]) for i in items]

    cur_fmt = sorted((f["format"], f["score"]) for f in current.get("formatItems", []))
    want_fmt = sorted((f["format"], f["score"]) for f in format_items)
    if (signature(current["items"]) != signature(items) or current["cutoff"] != cutoff_id
            or not current["upgradeAllowed"] or cur_fmt != want_fmt
            or current["minFormatScore"] != 0 or current["cutoffFormatScore"] != 0):
        wanted["id"] = current["id"]
        call("PUT", f"{api}/qualityprofile/{current['id']}", key, wanted)
        print(f"Lidarr: quality profile {LIDARR_PROFILE_NAME} updated")
    return current["id"]


def migrate_lidarr_artists(api: str, key: str, profile_id: int) -> None:
    """Move any artist still left on one of Lidarr's built-in default profiles onto the new one;
    an artist an operator has deliberately put on something else is left alone."""
    profiles = {p["name"]: p["id"] for p in call("GET", f"{api}/qualityprofile", key)}
    legacy_ids = {profiles[n] for n in LIDARR_LEGACY_PROFILES if n in profiles}
    stale = [a["id"] for a in call("GET", f"{api}/artist", key) if a.get("qualityProfileId") in legacy_ids]
    if stale:
        call("PUT", f"{api}/artist/editor", key, {"artistIds": stale, "qualityProfileId": profile_id})
        print(f"Lidarr: moved {len(stale)} artist(s) to {LIDARR_PROFILE_NAME}")


def configure_lidarr_naming(api: str, key: str) -> None:
    """Jellyfin follows MusicBrainz conventions and wants Artist/Album (Year)/Track nesting with
    accurate tags (Servarr Wiki, Lidarr File Naming Guide -> Media server compatibility); the
    standard/multi-disc/artist-folder formats already configured on this instance match that, but
    renaming was left off, so Lidarr was importing files under their original names."""
    cfg = call("GET", f"{api}/config/naming", key)
    if not cfg["renameTracks"]:
        cfg["renameTracks"] = True
        call("PUT", f"{api}/config/naming/{cfg['id']}", key, cfg)
        print("Lidarr: track renaming enabled (Artist/Album (Year)/Track, Jellyfin-friendly)")


def configure_arr(name: str, url: str, version: str, key: str, cat_field: str, category: str, root: str) -> None:
    api = f"{url}/api/{version}"
    schema = call("GET", f"{api}/downloadclient/schema", key)
    clients = call("GET", f"{api}/downloadclient", key)
    removes = {"enable": True, "priority": 1, "removeCompletedDownloads": True, "removeFailedDownloads": True}
    upsert(f"{name} download client", f"{api}/downloadclient", key, clients,
           next(s for s in schema if s["implementation"] == "Sabnzbd"), "SABnzbd", removes,
           {"host": f"sabnzbd.{SVC}", "port": 8080, "useSsl": False, "apiKey": SAB_KEY, cat_field: category})
    # removeCompletedDownloads only removes a torrent once qBittorrent has met its seeding goal.
    upsert(f"{name} download client", f"{api}/downloadclient", key, clients,
           next(s for s in schema if s["implementation"] == "QBittorrent"), "qBittorrent", removes,
           {"host": f"gluetun-qbittorrent.{SVC}", "port": 8080, "useSsl": False, cat_field: category})

    extra = {}
    if name == "Lidarr":
        # Lidarr's root folder carries the defaults for artists added under it: the TRaSH-sourced
        # lossless-first profile (see configure_lidarr_quality), Standard metadata = albums/EPs/
        # singles.
        quality = configure_lidarr_quality(api, key)
        migrate_lidarr_artists(api, key, quality)
        configure_lidarr_naming(api, key)
        metadata = next(p["id"] for p in call("GET", f"{api}/metadataprofile", key) if p["name"] == "Standard")
        extra = {"name": "Music", "defaultQualityProfileId": quality, "defaultMetadataProfileId": metadata,
                 "defaultMonitorOption": "all", "defaultNewItemMonitorOption": "all", "defaultTags": []}
    add_root_folder(name, api, key, root, extra)


def configure_anime_root(name: str, url: str, version: str, key: str, root: str) -> None:
    """Sonarr and Radarr only: a second root folder for anime, alongside the regular one
    configure_arr already added. No download-client wiring needed -- that's per-app, not
    per-root-folder."""
    add_root_folder(name, f"{url}/api/{version}", key, root)


def configure_jellyfin_connection(name: str, url: str, version: str, key: str, *_) -> None:
    if not JELLYFIN_KEY:
        print(f"{name}: JELLYFIN_API_KEY not set, Jellyfin connection skipped")
        return
    api = f"{url}/api/{version}"
    schema = next(s for s in call("GET", f"{api}/notification/schema", key) if s["implementation"] == "MediaBrowser")
    upsert(f"{name} connection", f"{api}/notification", key, call("GET", f"{api}/notification", key),
           schema, "Jellyfin", {event: True for event in JELLYFIN_EVENTS[name]},
           {"host": f"jellyfin.{SVC}", "port": 8096, "useSsl": False, "apiKey": JELLYFIN_KEY,
            "notify": False, "updateLibrary": True})


def ensure_library_dirs() -> None:
    """The regular /media/library/{movies,tv,music,books} roots already exist on the NFS share;
    the anime roots are new, so create them here (uid/gid 1000, matching the *arrs and the rest
    of the NFS tree) before the *arrs' root-folder APIs are asked to use them -- they refuse a
    path that doesn't exist yet."""
    for _, _, _, _, root in ANIME_ROOTS:
        if not os.path.isdir(root):
            os.makedirs(root)
            print(f"created directory {root}")


def main() -> int:
    ensure_library_dirs()
    configure_sabnzbd()
    configure_qbittorrent()
    for arr in ARRS:
        configure_arr(*arr)
        configure_jellyfin_connection(*arr)
    for anime in ANIME_ROOTS:
        configure_anime_root(*anime)
    configure_prowlarr()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
