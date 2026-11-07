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
3. Branding: ElegantFin (+ its Media Bar add-on) and mihaif7's companion sheet for Jellyfin 12's
   Modern layout, pinned, then the HomeNet logo (branding/logo.svg, inlined) in place of
   Jellyfin's mark and server name in the header; all one marked block at the very top of the
   Custom CSS (an @import after any other rule is ignored). The SSO login-button CSS after it is
   untouched. The splash screen (branding/splash.webp, the backdrop behind the login and server
   pages) is uploaded and switched on; its hash in the block says which one is up.
4. Libraries and their options: Movies, Shows, Music, Anime and Anime Movies on the arrs' root
   folders under /media/library (created if missing, the folder added if a library lost it),
   English subtitles downloaded for Movies, Shows and Anime (Open Subtitles), Fanart added to the
   image fetchers of type options that list them explicitly.
5. Open Subtitles login, when OPENSUBTITLES_USERNAME/PASSWORD are set (opensubtitles.com
   account; scripts/media-secrets.py copies them from the vault).
6. Jellyfin Enhanced's Seerr integration (search results, request button, quota info), when
   SEERR_API_KEY is set: an internal URL for the server-side API calls it makes, a public URL
   for the links it shows users, and auto-import of Jellyfin-linked Seerr users.

These only change the web client and the mobile apps that render it; the Fire TV's Jellyfin
client (Wholphin) draws its own UI.
"""

import base64
import hashlib
import os
import sys
import urllib.parse
from pathlib import Path

from jellyfin_api import (ApiError, call, ensure_repositories, install_missing, raw, uninstall,
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
# /media/library/books (Bindery) has no library: Jellyfin's book support is basic. Anime and Anime
# Movies are new roots that media-config also adds to Sonarr/Radarr; this Job creates their
# directories itself too (ensure_library_dirs(), below) rather than depending on media-config's
# Job, which depends on this one's Kustomization being ready first.
LIBRARIES = [
    ("Movies", "movies", "/media/library/movies"),
    ("Shows", "tvshows", "/media/library/tv"),
    ("Music", "music", "/media/library/music"),
    ("Anime", "tvshows", "/media/library/anime"),
    ("Anime Movies", "movies", "/media/library/anime-movies"),
]

# Jellyfin Enhanced's Seerr integration (PLUGINS above): internal URL for the server-side API
# calls JE makes, public URL for the links it shows users, and the Jellyfin public URL so JE
# knows to rewrite the former into the latter (JellyseerrUrlMappings). SEERR_API_KEY comes from
# the jellyfin-plugins Secret -- not vault-managed like the other credentials here, because
# Seerr has no config-as-code of its own; it's copied by hand from Seerr's settings.json
# (main.apiKey) with scripts/vault-set.py / sops directly.
JELLYFIN_ENHANCED = "f69e946a-4b3c-4e9a-8f0a-8d7c1b2c4d9b"
SEERR_INTERNAL_URL = "http://seerr.media.svc.cluster.local:5055"
SEERR_PUBLIC_URL = "https://requests.z7x.org"
JELLYFIN_PUBLIC_URL = "https://media.z7x.org"

# hnk8s05's passed-through Intel UHD 630 (ansible/roles/pve_gpu,
# kubernetes/apps/kube-system/intel-gpu-plugin). QuickSync (qsv) over the VAAPI backend: the
# official Jellyfin image ships oneVPL/libmfx already, no extra plugin needed. Coffee Lake Gen9.5
# decodes H.264, HEVC (8/10-bit), VP9, VC1 and MPEG2 and encodes H.264/HEVC; GuC/HuC firmware
# doesn't load on this card (checked: `talosctl dmesg` has no "GuC/HuC firmware" lines), so the
# low-power encoders stay off. VPP handles tonemapping; no OpenCL needed for QSV.
RENDER_DEVICE = "/dev/dri/renderD128"
ENCODING = {
    "HardwareAccelerationType": "qsv",
    "QsvDevice": RENDER_DEVICE,
    "VaapiDevice": RENDER_DEVICE,
    "EnableHardwareEncoding": True,
    "AllowHevcEncoding": True,
    "HardwareDecodingCodecs": ["h264", "hevc", "mpeg2video", "vc1", "vp9"],
    "EnableDecodingColorDepth10Hevc": True,
    "EnableTonemapping": True,
    "EnableVppTonemapping": True,
    "EnableIntelLowPowerH264HwEncoder": False,
    "EnableIntelLowPowerHevcHwEncoder": False,
}
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

BRANDING = Path(os.environ.get("BRANDING_DIR", "/branding"))  # branding/ (a ConfigMap in the Job)
THEME_START = "/* homelab theme (kubernetes/apps/media/jellyfin/app/plugins.py) */"
THEME_END = "/* end homelab theme */"
IMPORTS = [
    '@import url("https://cdn.jsdelivr.net/gh/lscambo13/ElegantFin@v26.09.05/Theme/ElegantFin-jellyfin-theme-build-latest-minified.css");',
    '@import url("https://cdn.jsdelivr.net/gh/lscambo13/ElegantFin@v26.09.05/Theme/assets/add-ons/media-bar-plugin-support-latest-min.css");',
    # Has to come after ElegantFin: fixes it up for Jellyfin 12's Modern layout.
    '@import url("https://cdn.jsdelivr.net/gh/mihaif7/elegantfin-jf12@v26.10.13/Theme/ElegantFin-jf12-modern-latest.css");',
]
# The Modern header's home button holds Jellyfin's icon (<img src=".../icon-transparent...">) and
# the server name; the logo replaces both. The Legacy header's .pageTitleWithDefaultLogo too.
# On the login and server pages (.standalonePage.backdropPage) ElegantFin's veils over the
# backdrop are near-opaque and would hide the splash; there they're thinned. Item pages keep theirs.
BRAND_CSS = """\
header.MuiAppBar-root a.MuiButton-root:has(img[src*="icon-transparent"]) {
  width: 124px; min-width: 124px; height: 44px; padding: 0; border-radius: 8px;
  background: url("LOGO") left center / contain no-repeat; color: transparent; font-size: 0;
}
header.MuiAppBar-root a.MuiButton-root:has(img[src*="icon-transparent"]) > * { visibility: hidden; }
header.MuiAppBar-root a.MuiButton-root:has(img[src*="icon-transparent"]):focus-visible {
  outline: 2px solid #FFC27A; outline-offset: 4px;
}
.pageTitleWithDefaultLogo { background-image: url("LOGO") !important; background-size: contain !important; }
body:has(.standalonePage.backdropPage:not(.hide)) .backgroundContainer.withBackdrop { opacity: 0.5 !important; }
.standalonePage.backdropPage { background: linear-gradient(rgba(8, 11, 20, 0.20), rgba(8, 11, 20, 0.58)) !important; }
/* ElegantFin sets focus outline color/width but not style, so Sign In / Quick Connect / Forgot
   Password / Sign in with Pocket ID show no visible keyboard focus ring (WCAG 2.4.7). */
.emby-button:focus-visible { outline: 3px solid #FFC27A !important; outline-offset: 2px !important; }"""


def theme_block(splash_sha: str) -> str:
    logo = "data:image/svg+xml;base64," + base64.b64encode((BRANDING / "logo.svg").read_bytes()).decode()
    return "\n".join([THEME_START, *IMPORTS, f"/* splash {splash_sha} */",
                      BRAND_CSS.replace("LOGO", logo), THEME_END])


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


def set_branding() -> None:
    splash = (BRANDING / "splash.webp").read_bytes()
    sha = hashlib.sha256(splash).hexdigest()[:16]
    _, branding = call("GET", "/System/Configuration/branding")
    css = branding.get("CustomCss") or ""
    # Jellyfin re-encodes the splash it serves, so the upload is tracked by its hash in the block.
    if f"/* splash {sha} */" not in css or not branding.get("SplashscreenEnabled"):
        raw("POST", "/Branding/Splashscreen", base64.b64encode(splash), "image/webp")
        print(f"branding: splash {sha} uploaded")
        _, branding = call("GET", "/System/Configuration/branding")
        css = branding.get("CustomCss") or ""
    if THEME_START in css:
        before, _, rest = css.partition(THEME_START)
        _, _, after = rest.partition(THEME_END)
        css = (before + after).strip()
    new = (theme_block(sha) + "\n" + css).strip()
    if new == (branding.get("CustomCss") or "").strip() and branding.get("SplashscreenEnabled"):
        return
    branding.update(CustomCss=new, SplashscreenEnabled=True)
    call("POST", "/System/Configuration/branding", branding)
    print("branding: theme, logo and splash set")


def ensure_library_dirs() -> None:
    """Jellyfin's own anime/anime-movies root folders (/media mounted alongside the branding
    ConfigMap), so ensure_libraries() below never has to wait on media-config's Job -- which
    itself depends on this Kustomization being ready first."""
    for _, _, path in LIBRARIES:
        if not os.path.isdir(path):
            os.makedirs(path)
            print(f"created directory {path}")


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


def set_jellyseerr() -> None:
    key = os.environ.get("SEERR_API_KEY", "")
    if not key:
        print("Jellyfin Enhanced Seerr integration: SEERR_API_KEY not set yet, skipped")
        return
    _, config = call("GET", f"/Plugins/{JELLYFIN_ENHANCED}/Configuration")
    wanted = {
        "JellyseerrEnabled": True,
        "JellyseerrUrls": SEERR_INTERNAL_URL,
        "JellyseerrApiKey": key,
        "JellyseerrUrlMappings": f"{JELLYFIN_PUBLIC_URL}|{SEERR_PUBLIC_URL}",
        "JellyseerrShowSearchResults": True,
        "JellyseerrShowReportButton": True,
        "JellyseerrShowQuotaInfo": True,
        # Keeps Seerr aware of every linked Jellyfin user (Leah included) without a manual import.
        "JellyseerrAutoImportUsers": True,
    }
    if all(config.get(k) == v for k, v in wanted.items()):
        return
    config.update(wanted)
    call("POST", f"/Plugins/{JELLYFIN_ENHANCED}/Configuration", config)
    print("Jellyfin Enhanced: Seerr integration set")


def set_encoding() -> None:
    _, config = call("GET", "/System/Configuration/encoding")
    if all(config.get(k) == v for k, v in ENCODING.items()):
        return
    config.update(ENCODING)
    call("POST", "/System/Configuration/encoding", config)
    print("encoding: QuickSync hardware transcoding set")


def main():
    wait_for_server()
    ensure_repositories(REPOSITORIES, remove=REMOVED_REPOSITORIES)
    uninstall(REMOVED_PLUGINS)
    install_missing(PLUGINS)
    set_scripts()
    set_branding()
    ensure_library_dirs()
    ensure_libraries()
    set_library_options()
    set_open_subtitles()
    set_jellyseerr()
    set_encoding()
    print("ok")


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        sys.exit(str(e))
