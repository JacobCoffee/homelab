"""Connect Bazarr to Sonarr/Radarr, give it one "English" language profile applied to every
series and movie, and turn on a handful of free-to-use subtitle providers plus OpenSubtitles.com,
idempotently.

1. Bazarr (Python/configparser) has no documented env var for presetting its API key -- it's
   generated on first boot into config.yaml (auth.apikey). Unlike Jellyfin's admin API key (a
   human creates it in the dashboard, so it's pushed through the vault like any other secret),
   Bazarr's is self-generated and never leaves the pod, so there's nothing to vault. This Job
   mounts the *same* "bazarr" PVC the HelmRelease already uses, read-only, purely to read that
   one value out of config.yaml -- config.yaml is world-readable (0644) so no uid/gid gymnastics
   are needed, and since the PVC is already bound (WaitForFirstConsumer), Kubernetes schedules
   this Job's pod onto the same node Bazarr is already running on, so the ReadWriteOnce volume
   mounts fine alongside Bazarr's own mount (same node, not multi-attach). Every other change goes
   through Bazarr's API (POST /api/system/settings, a dynaconf form endpoint hidden from its own
   swagger.json but very much alive -- see api/system/settings.py in the image).
2. Sonarr and Radarr: enabled, pointed at the in-cluster services, then an immediate
   "update_series"/"update_movies" task run (api/system/tasks) so this Job doesn't have to wait out
   their 60-minute sync interval to prove the connection on a fresh install.
3. One language profile named "English" (code "en", not forced, not HI-only but HI accepted as a
   fallback -- TRaSH's Setup Guide doesn't recommend a specific forced/HI split, just describes the
   toggles), set as the default for *new* series and movies (general.serie_default_profile /
   movie_default_profile) and applied to every *existing* one (TRaSH's After-Install-Configuration:
   "Bazarr only searches for subs of Episodes and Movies added after installation"). Profile items
   need both 'audio_exclude' and 'audio_only_include' keys or Bazarr's own missing-subtitles indexer
   throws a KeyError on every subsequent profile application (confirmed live against this cluster's
   Bazarr 1.6.2; its only migration for this lives in a startup-only compat pass that never touches
   profiles created later through the API).
4. Providers: OpenSubtitles.com (account from the jellyfin-plugins Secret's OPENSUBTITLES_USERNAME/
   PASSWORD -- Jellyfin's Open Subtitles plugin shares the same ~20-downloads/day account quota, see
   the top-of-repo README note this Job's ks.yaml points at: Bazarr is meant to own subtitle
   downloading, Jellyfin's plugin should stay off), plus three providers this Bazarr build (1.6.2)
   can use without ever creating an account: Gestdown (Addic7ed's data without Addic7ed's
   login/CAPTCHA wall -- Addic7ed itself is in Bazarr's own PROVIDERS_FORCED_OFF list), Embedded
   Subtitles (pulls tracks already muxed into the file via ffmpeg/ffprobe, both present in this
   image), and AnimeTosho (anime-only, no account). Podnapisi, mentioned as a TRaSH/community
   favourite, was removed from Bazarr entirely (app/config.py has "delete podnapisi section since
   this provider doesn't exist anymore"), so it's not offered here. Jimaku (anime, Jimaku.cc) would
   be a good fifth provider but its Python class raises ConfigurationError without an api_key --
   that's a free account with nothing in the vault to source it from yet, so it's left disabled;
   add a JIMAKU_API_KEY vault entry and a 'jimaku' provider_configs-compatible section if wanted.
5. Scoring and sync, from TRaSH's Bazarr-suggested-scoring: minimum score 90 (series) / 80 (movies),
   ffsubsync (subsync.use_subsync) on with a sync-score threshold of 96 (series) / 86 (movies) so
   Bazarr re-times slightly-off subs instead of just accepting them. Upgrading previously-downloaded
   subtitles and the default 6-hour wanted-search cadence are Bazarr defaults already and are only
   reasserted here for the record.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SVC = "media.svc.cluster.local"
BAZARR_URL = os.environ.get("BAZARR_URL", f"http://bazarr.{SVC}:6767").rstrip("/")
CONFIG_YAML = os.environ.get("BAZARR_CONFIG_YAML", "/bazarr-config/config/config.yaml")

SONARR_HOST, SONARR_PORT = f"sonarr.{SVC}", 8989
RADARR_HOST, RADARR_PORT = f"radarr.{SVC}", 7878
SONARR_API_KEY = os.environ["SONARR_API_KEY"]
RADARR_API_KEY = os.environ["RADARR_API_KEY"]
OPENSUBTITLES_USERNAME = os.environ.get("OPENSUBTITLES_USERNAME", "")
OPENSUBTITLES_PASSWORD = os.environ.get("OPENSUBTITLES_PASSWORD", "")

# Account-free providers this Bazarr build ships (subliminal_patch/providers/*.py): a free
# Addic7ed mirror, the file's own embedded tracks, and an anime-only indexer. opensubtitlescom
# only goes in if we actually have credentials for it.
FREE_PROVIDERS = ["gestdown", "embeddedsubtitles", "animetosho"]

PROFILE_ID = 1
PROFILE_NAME = "English"
LANGUAGE_PROFILE = {
    "profileId": PROFILE_ID,
    "name": PROFILE_NAME,
    "cutoff": None,
    "items": [{"id": 1, "language": "en", "forced": "False", "hi": "False",
               "audio_exclude": "False", "audio_only_include": "False"}],
    "mustContain": [],
    "mustNotContain": [],
    "originalFormat": None,
    "tag": None,
}

TIMEOUT = 30


class ApiError(Exception):
    pass


def read_apikey(path: str) -> str:
    """Scrape auth.apikey out of config.yaml without pulling in a YAML parser: find the top-level
    'auth:' section, then the first indented 'apikey:' line under it. ('apikey:' also appears
    under 'plex:' and 'jellyfin:', so this can't just grep the whole file.)"""
    section = None
    for line in open(path, encoding="utf-8"):
        if line.strip() and not line[0].isspace():
            section = line.split(":", 1)[0].strip()
            continue
        if section == "auth":
            m = re.match(r"\s+apikey:\s*(\S+)\s*$", line)
            if m:
                return m.group(1).strip("'\"")
    sys.exit(f"could not find auth.apikey in {path}")


APIKEY = read_apikey(CONFIG_YAML)


def api(method: str, path: str, query: list | None = None, form: dict | None = None):
    url = f"{BAZARR_URL}/api{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    data, headers = None, {"X-API-KEY": APIKEY}
    if form is not None:
        data = urllib.parse.urlencode(form, doseq=True).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raise ApiError(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')[:400]}") from None
    return json.loads(raw) if raw else None


def wait_for_bazarr() -> None:
    for _ in range(60):
        try:
            api("GET", "/system/status")
            return
        except (ApiError, urllib.error.URLError, OSError) as e:
            print(f"waiting for Bazarr: {e}")
            time.sleep(5)
    sys.exit("Bazarr never became reachable")


def form_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def current_settings() -> dict:
    return api("GET", "/system/settings")


def ensure_settings(desired: dict, label: str) -> bool:
    """desired maps (section, field) -> value. Only the fields that actually differ from the live
    config are POSTed, so re-runs don't needlessly restart the Sonarr/Radarr SignalR clients
    (save_settings() does that any time an ip/port/apikey/use_* key is present in the form, whether
    or not its value changed)."""
    current = current_settings()
    form, changed = {}, []
    for (section, field), value in desired.items():
        current_value = current[section][field]
        same = (sorted(current_value) == sorted(value) if isinstance(value, list)
                else current_value == value)
        if not same:
            form[f"settings-{section}-{field}"] = form_value(value)
            changed.append(f"{section}.{field}={value}")
    if not form:
        return False
    api("POST", "/system/settings", form=form)
    print(f"Bazarr: {label}: {', '.join(changed)}")
    return True


def ensure_arr(name: str, host: str, port: int, api_key: str) -> None:
    section = name.lower()
    ensure_settings({
        ("general", f"use_{section}"): True,
        (section, "ip"): host,
        (section, "port"): port,
        (section, "base_url"): "",
        (section, "ssl"): False,
        (section, "apikey"): api_key,
    }, f"{name} connection")
    api("POST", "/system/tasks", query=[("taskid", f"update_{'series' if section == 'sonarr' else 'movies'}")])
    for _ in range(12):
        status = api("GET", "/system/status")["data"]
        version = status[f"{section}_version"]
        if version not in ("", "unknown"):
            print(f"Bazarr: {name} connected (version {version})")
            return
        time.sleep(5)
    sys.exit(f"Bazarr never reported a {name} version -- connection failed")


def ensure_language_enabled() -> None:
    languages = api("GET", "/system/languages")
    enabled = sorted(x["code2"] for x in languages if x["enabled"])
    if enabled != ["en"]:
        api("POST", "/system/settings", form={"languages-enabled": ["en"]})
        print(f"Bazarr: enabled languages: en (was {', '.join(enabled) or 'none'})")


def ensure_language_profile() -> None:
    profiles = api("GET", "/system/languages/profiles")
    if profiles == [LANGUAGE_PROFILE]:
        return
    api("POST", "/system/settings", form={"languages-profiles": json.dumps([LANGUAGE_PROFILE])})
    print(f"Bazarr: language profile {PROFILE_NAME!r} (id {PROFILE_ID}) set")


def ensure_default_profile() -> None:
    ensure_settings({
        ("general", "serie_default_enabled"): True,
        ("general", "serie_default_profile"): PROFILE_ID,
        ("general", "movie_default_enabled"): True,
        ("general", "movie_default_profile"): PROFILE_ID,
    }, "default language profile")


def apply_profile_to_existing() -> None:
    series = api("GET", "/series", query=[("length", -1)])["data"]
    stale = [s["sonarrSeriesId"] for s in series if s["profileId"] != PROFILE_ID]
    if stale:
        query = []
        for sid in stale:
            query += [("seriesid", sid), ("profileid", PROFILE_ID)]
        api("POST", "/series", query=query)
        print(f"Bazarr: applied {PROFILE_NAME!r} to {len(stale)} series")

    movies = api("GET", "/movies", query=[("length", -1)])["data"]
    stale = [m["radarrId"] for m in movies if m["profileId"] != PROFILE_ID]
    if stale:
        query = []
        for rid in stale:
            query += [("radarrid", rid), ("profileid", PROFILE_ID)]
        api("POST", "/movies", query=query)
        print(f"Bazarr: applied {PROFILE_NAME!r} to {len(stale)} movies")


def ensure_providers() -> None:
    providers = list(FREE_PROVIDERS)
    desired = {("general", "enabled_providers"): providers}
    if OPENSUBTITLES_USERNAME and OPENSUBTITLES_PASSWORD:
        providers.append("opensubtitlescom")
        desired |= {
            ("opensubtitlescom", "username"): OPENSUBTITLES_USERNAME,
            ("opensubtitlescom", "password"): OPENSUBTITLES_PASSWORD,
            ("opensubtitlescom", "use_hash"): True,
        }
    else:
        print("Bazarr: OPENSUBTITLES_USERNAME/PASSWORD not set, leaving opensubtitlescom disabled")
    ensure_settings(desired, "providers")


def ensure_scoring() -> None:
    ensure_settings({
        ("general", "minimum_score"): 90,
        ("general", "minimum_score_movie"): 80,
        ("general", "upgrade_subs"): True,
        ("general", "days_to_upgrade_subs"): 7,
        ("general", "upgrade_manual"): True,
        ("general", "subfolder"): "current",
        ("general", "wanted_search_frequency"): 6,
        ("general", "wanted_search_frequency_movie"): 6,
        ("general", "adaptive_searching"): True,
        ("subsync", "use_subsync"): True,
        ("subsync", "use_subsync_threshold"): True,
        ("subsync", "subsync_threshold"): 96,
        ("subsync", "use_subsync_movie_threshold"): True,
        ("subsync", "subsync_movie_threshold"): 86,
    }, "scoring and sync")


def main() -> int:
    wait_for_bazarr()
    ensure_arr("Sonarr", SONARR_HOST, SONARR_PORT, SONARR_API_KEY)
    ensure_arr("Radarr", RADARR_HOST, RADARR_PORT, RADARR_API_KEY)
    ensure_language_enabled()
    ensure_language_profile()
    ensure_default_profile()
    apply_profile_to_existing()
    ensure_providers()
    ensure_scoring()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
