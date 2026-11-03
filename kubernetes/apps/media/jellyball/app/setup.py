"""Register Jellyball's lineup in Jellyfin, idempotently.

1. Wait for Jellyball and Jellyfin.
2. Jellyfin: an M3U tuner on Jellyball's playlist and an XMLTV guide on its epg.xml, the guide
   limited to that tuner. Jellyball's always-live channels carry tvg-ids like ESPN.us, which other
   tuners' channels (Dispatcharr's iptv-org list) can share, and they'd get Jellyball's schedule.
3. A guide refresh when anything changed. After that Jellyball asks for one itself whenever its
   channels change (JELLYFIN_URL / JELLYFIN_API_KEY in helmrelease.yaml).

Teams are picked in Jellyball's dashboard (jellyball.z7x.org), not here.
Safe to re-run: every step compares with what's there first.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

SVC = "media.svc.cluster.local"
JELLYBALL = os.environ.get("JELLYBALL_URL", f"http://jellyball.{SVC}:8000").rstrip("/")
JELLYFIN = os.environ.get("JELLYFIN_URL", f"http://jellyfin.{SVC}:8096").rstrip("/")
JELLYFIN_KEY = os.environ["JELLYFIN_API_KEY"]

M3U_URL = f"{JELLYBALL}/playlist.m3u"
EPG_URL = f"{JELLYBALL}/epg.xml"
TUNER_NAME = "Jellyball"

TIMEOUT = 60


def jellyfin(method: str, path: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"{JELLYFIN}{path}", data=data, method=method, headers={
        "Authorization": f'MediaBrowser Token="{JELLYFIN_KEY}"', "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def wait_for(what: str, check) -> None:
    for _ in range(90):
        try:
            check()
            return
        except (urllib.error.URLError, OSError) as e:
            print(f"waiting for {what}: {e}")
            time.sleep(10)
    sys.exit(f"{what} never became reachable")


def ensure_tuner() -> tuple[str, bool]:
    """The Jellyball tuner's id; True if it was just added."""
    options = jellyfin("GET", "/System/Configuration/livetv")
    tuner = next((t for t in options.get("TunerHosts", []) if t.get("Url") == M3U_URL), None)
    if tuner:
        return tuner["Id"], False
    tuner = jellyfin("POST", "/LiveTv/TunerHosts", {
        "Type": "m3u", "Url": M3U_URL, "FriendlyName": TUNER_NAME, "TunerCount": 0,
        # One Jellyball session already serves every viewer of a channel; this lets Jellyfin
        # share its own ffmpeg input between them too.
        "AllowStreamSharing": True, "ImportFavoritesOnly": False})
    print(f"Jellyfin: M3U tuner {M3U_URL} added")
    return tuner["Id"], True


def ensure_guide(tuner_id: str) -> bool:
    options = jellyfin("GET", "/System/Configuration/livetv")
    guide = next((p for p in options.get("ListingProviders", []) if p.get("Path") == EPG_URL), None)
    if guide and not guide.get("EnableAllTuners") and guide.get("EnabledTuners") == [tuner_id]:
        return False
    # Posting with the existing Id updates that provider instead of adding a second one.
    jellyfin("POST", "/LiveTv/ListingProviders", {
        **(guide or {}), "Type": "xmltv", "Path": EPG_URL,
        "EnableAllTuners": False, "EnabledTuners": [tuner_id]})
    print(f"Jellyfin: XMLTV guide {EPG_URL} {'updated' if guide else 'added'}, for the {TUNER_NAME} tuner only")
    return True


def main() -> int:
    wait_for("Jellyball", lambda: urllib.request.urlopen(f"{JELLYBALL}/healthz", timeout=TIMEOUT).close())
    wait_for("Jellyfin", lambda: jellyfin("GET", "/System/Info"))
    tuner_id, added = ensure_tuner()
    changed = ensure_guide(tuner_id) or added
    if changed:
        task = next(t for t in jellyfin("GET", "/ScheduledTasks") if t.get("Key") == "RefreshGuide")
        jellyfin("POST", f"/ScheduledTasks/Running/{task['Id']}")
        print("Jellyfin: guide refresh started")
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
