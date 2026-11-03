"""Jellyfin's plugins, web scripts and theme, idempotently.

1. Plugin repositories and plugins (Jellyfin 12 builds only; 10.x plugins don't load on 12):
   Open Subtitles, Fanart (official repo); Intro Skipper; InPlayerEpisodePreview (episode list
   in the player); File Transformation + Media Bar (IAmParadox27); JavaScript Injector + Jellyfin
   Enhanced (n00bcodr's Jellyfin 12 manifest). TMDb ships with Jellyfin. Anything missing is
   installed, then Jellyfin is restarted once to load it. Editor's Choice is uninstalled: its
   home-page banner stacked on top of Media Bar's.
2. JavaScript Injector scripts for the web client: KefinTweaks and jf-avatars, pinned to a
   release. Scripts other plugins or the UI add (KefinTweaks writes its own config entries) are
   left alone; only entries named here are managed.
3. Branding CSS: ElegantFin (+ its Media Bar add-on) and mihaif7's companion sheet for Jellyfin
   12's Modern layout, pinned, kept as one marked block at the very top of the Custom CSS (an
   @import after any other rule is ignored). The SSO login-button CSS after it is untouched.
4. Libraries and their options: Movies, Shows and Music on the arrs' root folders under
   /media/library (created if missing, the folder added if a library lost it), English subtitles
   downloaded for Movies and Shows (Open Subtitles), Fanart added to the image fetchers of type
   options that list them explicitly.
5. Open Subtitles login, when OPENSUBTITLES_USERNAME/PASSWORD are set (opensubtitles.com
   account; scripts/media-secrets.py copies them from the vault).

These only change the web client and the mobile apps that render it; the Fire TV's Jellyfin
client (Wholphin) draws its own UI.
"""

import os
import sys
import urllib.parse

from jellyfin_api import (ApiError, call, ensure_repositories, install_missing, uninstall,
                          wait_for_server)

OFFICIAL = "https://repo.jellyfin.org/files/plugin/manifest.json"
INTRO_SKIPPER = "https://intro-skipper.org/manifest.json"
EDITORS_CHOICE = "https://github.com/lachlandcp/jellyfin-editors-choice-plugin/raw/main/manifest.json"
INPLAYER = "https://raw.githubusercontent.com/Namo2/InPlayerEpisodePreview/master/manifest.json"
PARADOX = "https://www.iamparadox.dev/jellyfin/plugins/manifest.json"
N00BCODR = "https://raw.githubusercontent.com/n00bcodr/jellyfin-plugins/main/12/manifest.json"

REPOSITORIES = [
    ("Intro Skipper", INTRO_SKIPPER),
    ("InPlayerEpisodePreview", INPLAYER),
    ("IAmParadox27", PARADOX),
    ("n00bcodr (Jellyfin 12)", N00BCODR),
]
# Uninstalled when present, with their repositories. Editor's Choice duplicated Media Bar's
# home-page banner.
REMOVED_REPOSITORIES = (EDITORS_CHOICE,)
REMOVED_PLUGINS = [("Editor's Choice", "70bb2ec1-f19e-46b5-b49a-942e6b96ebae")]

# (package name, GUID, repository). Dependencies first: Media Bar and InPlayerEpisodePreview
# inject through File Transformation; KefinTweaks/jf-avatars need the JavaScript Injector.
PLUGINS = [
    ("File Transformation", "5e87cc92-571a-4d8d-8d98-d2d4147f9f90", PARADOX),
    ("JavaScript Injector", "f5a34f7b-2e8a-4e6a-a722-3a216a81b374", N00BCODR),
    ("Open Subtitles", "4b9ed42f-5185-48b5-9803-6ff2989014c4", OFFICIAL),
    ("Fanart", "170a157f-ac6c-437a-abdd-ca9c25cebd39", OFFICIAL),
    ("Intro Skipper", "c83d86bb-a1e0-4c35-a113-e2101cf4ee6b", INTRO_SKIPPER),
    ("InPlayerEpisodePreview", "73833d5f-0bcb-45dc-ab8b-7ce668f4345d", INPLAYER),
    ("Media Bar", "08f615ea-2107-4f04-89cc-091035f54448", PARADOX),
    ("Jellyfin Enhanced", "f69e946a-4b3c-4e9a-8f0a-8d7c1b2c4d9b", N00BCODR),
]
JS_INJECTOR = "f5a34f7b-2e8a-4e6a-a722-3a216a81b374"
OPEN_SUBTITLES = "4b9ed42f-5185-48b5-9803-6ff2989014c4"

# (name, collection type, folder): the root folders media-config gives Radarr, Sonarr and Lidarr.
# /media/library/books (Bindery) has no library: Jellyfin's book support is basic.
LIBRARIES = [
    ("Movies", "movies", "/media/library/movies"),
    ("Shows", "tvshows", "/media/library/tv"),
    ("Music", "music", "/media/library/music"),
]

KEFINTWEAKS_VERSION = "v0.5.3"
JF_AVATARS_VERSION = "2.4.1"
SCRIPTS = {
    # KefinTweaks' loader; on an admin's first visit it adds its own KefinTweaks-Config and
    # KefinTweaks-Injector entries, which this script doesn't touch.
    "KefinTweaks": (
        'const script = document.createElement("script");\n'
        f'script.src = "https://cdn.jsdelivr.net/gh/ranaldsgift/KefinTweaks@{KEFINTWEAKS_VERSION}/kefinTweaks-plugin.js";\n'
        "script.async = true;\n"
        "document.head.appendChild(script);\n"
    ),
    # jf-avatars: an avatar gallery in the user's profile settings.
    "JF-AVATARS": (
        "(() => {\n"
        '  const fix = document.createElement("style");\n'
        '  fix.textContent = "#jf-avatars-modal{position:fixed!important;inset:0!important;margin:auto!important}";\n'
        "  document.head.appendChild(fix);\n"
        "  const load = () => {\n"
        '    const script = document.createElement("script");\n'
        '    script.type = "module";\n'
        f'    script.src = "https://cdn.jsdelivr.net/gh/kalibrado/jf-avatars@{JF_AVATARS_VERSION}/main.js";\n'
        "    document.head.appendChild(script);\n"
        "  };\n"
        '  if (document.readyState === "complete") load(); else window.addEventListener("load", load);\n'
        "})();\n"
    ),
}

THEME_START = "/* homelab theme (kubernetes/apps/media/jellyfin/app/plugins.py) */"
THEME_END = "/* end homelab theme */"
THEME = "\n".join([
    THEME_START,
    '@import url("https://cdn.jsdelivr.net/gh/lscambo13/ElegantFin@v26.09.05/Theme/ElegantFin-jellyfin-theme-build-latest-minified.css");',
    '@import url("https://cdn.jsdelivr.net/gh/lscambo13/ElegantFin@v26.09.05/Theme/assets/add-ons/media-bar-plugin-support-latest-min.css");',
    # Has to come after ElegantFin: fixes it up for Jellyfin 12's Modern layout.
    '@import url("https://cdn.jsdelivr.net/gh/mihaif7/elegantfin-jf12@v26.10.13/Theme/ElegantFin-jf12-modern-latest.css");',
    THEME_END,
])

SUBTITLE_LANGUAGES = ["eng"]
SUBTITLED_TYPES = {"movies", "tvshows"}


def set_scripts() -> None:
    _, config = call("GET", f"/Plugins/{JS_INJECTOR}/Configuration")
    scripts = config.get("CustomJavaScripts") or []
    by_name = {s.get("Name"): s for s in scripts}
    changed = []
    for name, body in SCRIPTS.items():
        entry = by_name.get(name)
        wanted = {"Name": name, "Script": body, "Src": "", "Module": False, "Enabled": True,
                  "RequiresAuthentication": True}
        if entry is None:
            scripts.append(wanted)
        elif any(entry.get(k) != v for k, v in wanted.items()):
            entry.update(wanted)
        else:
            continue
        changed.append(name)
    if changed:
        config["CustomJavaScripts"] = scripts
        call("POST", f"/Plugins/{JS_INJECTOR}/Configuration", config)
        print(f"JavaScript Injector: {', '.join(changed)} set")


def set_theme() -> None:
    _, branding = call("GET", "/System/Configuration/branding")
    css = branding.get("CustomCss") or ""
    if THEME_START in css:
        before, _, rest = css.partition(THEME_START)
        _, _, after = rest.partition(THEME_END)
        css = (before + after).strip()
    new = (THEME + "\n" + css).strip()
    if new == (branding.get("CustomCss") or "").strip():
        return
    branding["CustomCss"] = new
    call("POST", "/System/Configuration/branding", branding)
    print("branding: theme CSS set")


def ensure_libraries() -> None:
    _, folders = call("GET", "/Library/VirtualFolders")
    by_name = {f["Name"]: f for f in folders or []}
    for name, kind, path in LIBRARIES:
        folder = by_name.get(name)
        if folder is None:
            qs = urllib.parse.urlencode({"name": name, "collectionType": kind, "paths": path,
                                         "refreshLibrary": "true"})
            call("POST", f"/Library/VirtualFolders?{qs}", {"LibraryOptions": {}})
            print(f"library {name}: created on {path}")
        elif path not in folder.get("Locations", []):
            call("POST", "/Library/VirtualFolders/Paths?refreshLibrary=true",
                 {"Name": name, "PathInfo": {"Path": path}})
            print(f"library {name}: added {path}")


def set_library_options() -> None:
    _, folders = call("GET", "/Library/VirtualFolders")
    for folder in folders or []:
        options = folder["LibraryOptions"]
        changed = False
        if folder.get("CollectionType") in SUBTITLED_TYPES and options.get("SubtitleDownloadLanguages") != SUBTITLE_LANGUAGES:
            options["SubtitleDownloadLanguages"] = SUBTITLE_LANGUAGES
            changed = True
        # An empty TypeOptions list means Jellyfin's defaults (every fetcher); only explicit
        # lists, like the one the setup wizard wrote for Movies, need Fanart added.
        for type_options in options.get("TypeOptions") or []:
            fetchers = type_options.get("ImageFetchers")
            if fetchers and "Fanart" not in fetchers:
                fetchers.append("Fanart")
                changed = True
        if changed:
            call("POST", "/Library/VirtualFolders/LibraryOptions", {"Id": folder["ItemId"], "LibraryOptions": options})
            print(f"library {folder['Name']}: options set")


def set_open_subtitles() -> None:
    user, password = os.environ.get("OPENSUBTITLES_USERNAME", ""), os.environ.get("OPENSUBTITLES_PASSWORD", "")
    if not (user and password):
        print("Open Subtitles: credentials not set yet, login skipped")
        return
    _, config = call("GET", f"/Plugins/{OPEN_SUBTITLES}/Configuration")
    # The password can't be compared reliably (the plugin may rewrite it), so it's written on
    # every run; this Job only runs when its script or credentials change.
    config.update(Username=user, Password=password, CredentialsInvalid=False)
    call("POST", f"/Plugins/{OPEN_SUBTITLES}/Configuration", config)
    print("Open Subtitles: login set")


def main():
    wait_for_server()
    ensure_repositories(REPOSITORIES, remove=REMOVED_REPOSITORIES)
    uninstall(REMOVED_PLUGINS)
    install_missing(PLUGINS)
    set_scripts()
    set_theme()
    ensure_libraries()
    set_library_options()
    set_open_subtitles()
    print("ok")


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        sys.exit(str(e))
