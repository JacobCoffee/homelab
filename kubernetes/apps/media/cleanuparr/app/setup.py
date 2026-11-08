"""Give Cleanuparr arrs, download clients, and cleanup rules, idempotently.

Cleanuparr trusts the pod CIDR (its own General -> Auth settings, "disable auth for local
addresses"), so this script calls its API without logging in -- same as the exec'd curl used to
discover the API while building this. See scripts/lab-logins.py for Cleanuparr's own UI login
(used only for browser/gateway access, not needed here).

1. Wait for Cleanuparr, then register Sonarr/Radarr/Lidarr and test each connection.
2. Register qBittorrent (no auth needed from the pod network) and SABnzbd (its API key), and test
   both.
3. Queue Cleaner: failed-import, errored and "downloading metadata" strikes, plus one stall rule
   and one slow rule covering the whole 0-100% range.
4. Malware Blocker: Cleanuparr's official Sonarr/Radarr blocklists (no curated Lidarr list exists).
5. Download Cleaner, qBittorrent only (SABnzbd doesn't seed):
   - Unlinked handling tags (not categories, so qBittorrent never relocates files) a download
     whose files carry no hardlink into the library. Cleanuparr itself guards this against a
     pending/failed import: it only evaluates torrents qBittorrent reports Completed (still
     downloading/stalled torrents are excluded by GetSeedingDownloads()'s own `Completed`
     filter), and every Download Cleaner run re-reads every enabled arr's live queue first and
     skips any download hash still present there ("skip | download is used by an arr", see
     DownloadCleaner.cs's `_downloadsProcessedByArrs`); if an arr can't be reached to check, the
     whole run throws and does nothing (`ProcessArrConfigAsync(..., throwOnFailure: true)`). So a
     completed-but-not-yet-imported or import-pending-retry download is still "in an arr's
     queue" and is left alone -- only a download the arr is done with (imported, now with a
     hardlink; or struck out of the queue and blocklisted by our own Queue Cleaner) can ever be
     both unlinked and untracked at the same time.
   - Two seeding rules on the four media categories, public torrents only: the unlinked tag
     removes after at least an hour of seeding time once tagged -- on top of the guards above, a
     plain, auditable buffer (priority 1); a 24h-past-share-limit backstop catches anything the
     arrs' own "Remove Completed" missed (priority 2, same 0.1 ratio qBittorrent itself stops at
     -- see media-config's configure_qbittorrent()). A hardlinked file surviving in the library
     is unaffected either way: deleting one hardlink never touches the inode's other links.
   - Private-tracker torrents (MyAnonamouse) must seed 72h or count as a hit and run, so they get
     only a third rule: removed after 7 days of seeding, the same limit their indexer gives
     qBittorrent (MAM_SEED_MINUTES in bindery/app/setup.py). The margin covers pod restarts and
     VPN reconnects, during which the tracker counts no seeding time.
6. Trigger one Queue Cleaner run so its result is visible in the Cleanuparr logs immediately,
   instead of waiting for the first cron tick.

Safe to re-run: every step compares what's there against what's wanted first, and only calls the
API for what differs. A connection test failing fails the job (and its pod's logs show why),
since a Cleanuparr that cannot reach what it is meant to clean up is not actually configured.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

SVC = "media.svc.cluster.local"
CLEANUPARR = os.environ.get("CLEANUPARR_URL", f"http://cleanuparr.{SVC}:11011").rstrip("/")
API = f"{CLEANUPARR}/api"

SONARR_API_KEY = os.environ["SONARR_API_KEY"]
RADARR_API_KEY = os.environ["RADARR_API_KEY"]
LIDARR_API_KEY = os.environ["LIDARR_API_KEY"]
SABNZBD_API_KEY = os.environ["SABNZBD_API_KEY"]

QBIT_HOST = os.environ.get("QBITTORRENT_URL", f"http://gluetun-qbittorrent.{SVC}:8080")
SABNZBD_HOST = os.environ.get("SABNZBD_URL", f"http://sabnzbd.{SVC}:8080")

ARRS = [
    ("sonarr", "Sonarr", f"http://sonarr.{SVC}:8989", SONARR_API_KEY, 3.0),
    ("radarr", "Radarr", f"http://radarr.{SVC}:7878", RADARR_API_KEY, 3.0),
    ("lidarr", "Lidarr", f"http://lidarr.{SVC}:8686", LIDARR_API_KEY, 1.0),
]

CATEGORIES = ["movies", "tv", "music", "books"]
UNLINKED_TAG = "cleanuparr-unlinked"

TIMEOUT = 30


def api(method: str, path: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"{API}{path}", data=data, method=method,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} -> {e.code}: {detail}") from None
    return json.loads(raw) if raw else None


def wait_for_cleanuparr() -> None:
    for _ in range(90):
        try:
            req = urllib.request.Request(f"{CLEANUPARR}/health")
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                if resp.read().strip() == b"healthy":
                    return
        except (urllib.error.URLError, OSError) as e:
            print(f"waiting for Cleanuparr: {e}")
        time.sleep(10)
    sys.exit("Cleanuparr never became healthy")


def matches(current, desired) -> bool:
    """Whether current already has every field/value desired asks for (extra current keys OK)."""
    if isinstance(desired, dict):
        return isinstance(current, dict) and all(
            k in current and matches(current[k], v) for k, v in desired.items()
        )
    if isinstance(desired, list):
        if not isinstance(current, list) or len(current) != len(desired):
            return False
        if desired and all(isinstance(x, str) for x in desired):
            return sorted(desired) == sorted(current)
        return current == desired
    if isinstance(desired, (int, float)) and isinstance(current, (int, float)):
        return abs(current - desired) < 1e-9
    if isinstance(desired, str) and isinstance(current, str):
        # Cleanuparr round-trips Host/Url through .NET's Uri, which adds a trailing "/" to a
        # bare "scheme://host:port" with no path.
        return current.rstrip("/") == desired.rstrip("/")
    return current == desired


# --- download clients -------------------------------------------------------

def ensure_download_client(name: str, type_name: str, host: str, api_key: str | None = None) -> str:
    clients = api("GET", "/configuration/download_client")["clients"]
    existing = next((c for c in clients if c["name"] == name), None)

    desired = {
        "enabled": True,
        "name": name,
        "typeName": type_name,
        "host": host,
        "username": None,
        "password": None,
        "apiKey": api_key,
        "urlBase": None,
        "externalUrl": None,
        "downloadDirectorySource": None,
        "downloadDirectoryTarget": None,
    }
    # apiKey/password come back masked, so compare everything except those.
    comparable = {k: v for k, v in desired.items() if k not in ("apiKey", "password")}

    if existing is None:
        created = api("POST", "/configuration/download_client", desired)
        print(f"Cleanuparr: download client {name} created")
        return created["id"]

    if not matches(existing, comparable):
        api("PUT", f"/configuration/download_client/{existing['id']}", desired)
        print(f"Cleanuparr: download client {name} updated")

    return existing["id"]


def test_download_client(label: str, type_name: str, host: str, api_key: str | None = None) -> None:
    result = api("POST", "/configuration/download_client/test", {
        "typeName": type_name, "host": host, "apiKey": api_key,
    })
    print(f"Cleanuparr: {label} connection test: {result.get('message', result)}")


# --- arr instances -----------------------------------------------------------

def ensure_arr_instance(arr_type: str, name: str, url: str, api_key: str, version: float) -> None:
    config = api("GET", f"/configuration/{arr_type}")
    existing = next((i for i in config["instances"] if i["name"] == name), None)

    desired = {"enabled": True, "name": name, "url": url, "apiKey": api_key,
               "version": version, "externalUrl": None}
    comparable = {k: v for k, v in desired.items() if k != "apiKey"}

    if existing is None:
        api("POST", f"/configuration/{arr_type}/instances", desired)
        print(f"Cleanuparr: {name} instance added")
    elif not matches(existing, comparable):
        api("PUT", f"/configuration/{arr_type}/instances/{existing['id']}", desired)
        print(f"Cleanuparr: {name} instance updated")

    if config.get("failedImportMaxStrikes") != -1:
        api("PUT", f"/configuration/{arr_type}", {"failedImportMaxStrikes": -1})
        print(f"Cleanuparr: {name} failed-import max strikes reset to inherit the global value")


def test_arr_instance(name: str, arr_type: str, url: str, api_key: str, version: float) -> None:
    result = api("POST", f"/configuration/{arr_type}/instances/test", {
        "url": url, "apiKey": api_key, "version": version,
    })
    print(f"Cleanuparr: {name} connection test: {result.get('message', result)}")


# --- queue cleaner -------------------------------------------------------------

QUEUE_CLEANER_CONFIG = {
    "enabled": True,
    "cronExpression": "0 0/5 * * * ?",
    "useAdvancedScheduling": False,
    "failedImport": {
        "maxStrikes": 3,
        "ignorePrivate": False,
        "deletePrivate": False,
        "skipIfNotFoundInClient": True,
        "patterns": [],
        "patternMode": "Exclude",
        "changeCategory": False,
        "forceImport": False,
        "forceImportMaxTries": 3,
    },
    "downloadingMetadataMaxStrikes": 3,
    "errored": {
        "maxStrikes": 3,
        "ignorePrivate": False,
        "deletePrivate": False,
        "changeCategory": False,
    },
    "processNoContentId": False,
    "ignoredDownloads": [],
}

STALL_RULE = {
    "name": "Stalled downloads",
    "enabled": True,
    "maxStrikes": 3,
    "privacyType": "Both",
    "minCompletionPercentage": 0,
    "maxCompletionPercentage": 100,
    "resetStrikesOnProgress": True,
    "minimumProgress": None,
    "deletePrivateTorrentsFromClient": False,
    "changeCategory": False,
}

SLOW_RULE = {
    "name": "Slow downloads",
    "enabled": True,
    "maxStrikes": 3,
    "privacyType": "Both",
    "minCompletionPercentage": 0,
    "maxCompletionPercentage": 100,
    "resetStrikesOnProgress": True,
    "minSpeed": "100 KB",
    "maxTimeHours": 0,
    "ignoreAboveSize": "25 GB",
    "ignoreWhileAltSpeedActive": True,
    "deletePrivateTorrentsFromClient": False,
    "changeCategory": False,
}


def ensure_queue_cleaner_config() -> None:
    current = api("GET", "/configuration/queue_cleaner")
    if matches(current, QUEUE_CLEANER_CONFIG):
        return
    api("PUT", "/configuration/queue_cleaner", QUEUE_CLEANER_CONFIG)
    print("Cleanuparr: Queue Cleaner configuration updated")


def ensure_queue_rule(kind: str, desired: dict) -> None:
    rules = api("GET", f"/queue-rules/{kind}")
    existing = next((r for r in rules if r["name"] == desired["name"]), None)
    if existing is None:
        api("POST", f"/queue-rules/{kind}", desired)
        print(f"Cleanuparr: {kind} rule '{desired['name']}' created")
    elif not matches(existing, desired):
        api("PUT", f"/queue-rules/{kind}/{existing['id']}", desired)
        print(f"Cleanuparr: {kind} rule '{desired['name']}' updated")


# --- malware blocker -----------------------------------------------------------

OFFICIAL_BLACKLIST = "https://cleanuparr.pages.dev/static/blacklist"

MALWARE_BLOCKER_CONFIG = {
    "enabled": True,
    "cronExpression": "0 0/5 * * * ?",
    "useAdvancedScheduling": False,
    "ignorePrivate": False,
    "deletePrivate": False,
    "processNoContentId": False,
    "deleteIfAnyFileBlocked": False,
    "sonarr": {"enabled": True, "blocklistType": "Blacklist", "blocklistPath": OFFICIAL_BLACKLIST},
    "radarr": {"enabled": True, "blocklistType": "Blacklist", "blocklistPath": OFFICIAL_BLACKLIST},
    "lidarr": {"enabled": False},
    "readarr": {"enabled": False},
    "whisparr": {"enabled": False},
    "sportarr": {"enabled": False},
    "lazyLibrarian": {"enabled": False},
    "ignoredDownloads": [],
}


def ensure_malware_blocker_config() -> None:
    current = api("GET", "/configuration/malware_blocker")
    if matches(current, MALWARE_BLOCKER_CONFIG):
        return
    api("PUT", "/configuration/malware_blocker", MALWARE_BLOCKER_CONFIG)
    print("Cleanuparr: Malware Blocker configuration updated")


# --- download cleaner (qBittorrent only; SABnzbd doesn't seed) -----------------

DOWNLOAD_CLEANER_CONFIG = {
    "enabled": True,
    "cronExpression": "0 0 * * * ?",
    "useAdvancedScheduling": False,
    "ignoredDownloads": [],
}

UNLINKED_CONFIG = {
    "enabled": True,
    "targetCategory": UNLINKED_TAG,
    "useTag": True,
    "ignoredRootDirs": [],
    "categories": CATEGORIES,
}

SEEDING_RULES = [
    {
        "name": "Unlinked - no hardlink",
        "categories": CATEGORIES,
        "trackerPatterns": [],
        "tagsAny": [UNLINKED_TAG],
        "tagsAll": [],
        "priority": 1,
        "privacyType": "Public",
        "maxRatio": -1,
        "minSeedTime": 0,
        "maxSeedTime": 1,
        "minSeeders": 0,
        "maxInactiveDays": -1,
        "deleteSourceFiles": True,
        "action": "Delete",
    },
    {
        "name": "Completed - media categories",
        "categories": CATEGORIES,
        "trackerPatterns": [],
        "tagsAny": [],
        "tagsAll": [],
        "priority": 2,
        "privacyType": "Public",
        "maxRatio": 0.1,
        "minSeedTime": 24,
        "maxSeedTime": -1,
        "minSeeders": 0,
        "maxInactiveDays": -1,
        "deleteSourceFiles": True,
        "action": "Delete",
    },
    {
        "name": "Private trackers - 7 days",
        "categories": CATEGORIES,
        "trackerPatterns": [],
        "tagsAny": [],
        "tagsAll": [],
        "priority": 3,
        "privacyType": "Private",
        "maxRatio": -1,
        "minSeedTime": 168,
        "maxSeedTime": 168,
        "minSeeders": 0,
        "maxInactiveDays": -1,
        "deleteSourceFiles": True,
        "action": "Delete",
    },
]


def ensure_download_cleaner_config() -> None:
    current = api("GET", "/configuration/download_cleaner")
    if matches(current, DOWNLOAD_CLEANER_CONFIG):
        return
    api("PUT", "/configuration/download_cleaner", DOWNLOAD_CLEANER_CONFIG)
    print("Cleanuparr: Download Cleaner configuration updated")


def ensure_unlinked_config(client_id: str) -> None:
    current = api("GET", f"/unlinked-config/{client_id}")
    if matches(current, UNLINKED_CONFIG):
        return
    api("PUT", f"/unlinked-config/{client_id}", UNLINKED_CONFIG)
    print("Cleanuparr: qBittorrent unlinked-download handling updated")


def ensure_seeding_rule(client_id: str, desired: dict) -> None:
    rules = api("GET", f"/seeding-rules/{client_id}")
    existing = next((r for r in rules if r["name"] == desired["name"]), None)
    if existing is None:
        api("POST", f"/seeding-rules/{client_id}", desired)
        print(f"Cleanuparr: seeding rule '{desired['name']}' created")
    elif not matches(existing, desired):
        api("PUT", f"/seeding-rules/{existing['id']}", desired)
        print(f"Cleanuparr: seeding rule '{desired['name']}' updated")


def main() -> int:
    wait_for_cleanuparr()

    for arr_type, name, url, key, version in ARRS:
        ensure_arr_instance(arr_type, name, url, key, version)
        test_arr_instance(name, arr_type, url, key, version)

    qbit_id = ensure_download_client("qBittorrent", "qBittorrent", QBIT_HOST)
    test_download_client("qBittorrent", "qBittorrent", QBIT_HOST)
    ensure_download_client("SABnzbd", "Sabnzbd", SABNZBD_HOST, SABNZBD_API_KEY)
    test_download_client("SABnzbd", "Sabnzbd", SABNZBD_HOST, SABNZBD_API_KEY)

    ensure_queue_cleaner_config()
    ensure_queue_rule("stall", STALL_RULE)
    ensure_queue_rule("slow", SLOW_RULE)

    ensure_malware_blocker_config()

    ensure_download_cleaner_config()
    ensure_unlinked_config(qbit_id)
    for rule in SEEDING_RULES:
        ensure_seeding_rule(qbit_id, rule)

    api("POST", "/jobs/QueueCleaner/trigger", {})
    print("Cleanuparr: Queue Cleaner run triggered")

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
