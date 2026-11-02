# Hearth

A custom Fire TV launcher with switchable and seasonal themes, homelab status, and a command
rail built for a remote that only sends arrows, OK, Back and play/pause (a TCL TV remote over
HDMI-CEC). Built for a Fire TV Stick 4K (`AFTKRT`, Fire OS 8 / Android 11).

## License and credit

Hearth is licensed under the **GNU General Public License v3.0 or later** (see `LICENSE`).

Fire OS pins Amazon's launcher as the system Home app, so a third-party launcher cannot
register as Home the normal way. Hearth's Home-redirect mechanism
(`homekey/HomeRedirectService.kt`, `homekey/HomeKeyWatcher.kt`, `homekey/BootReceiver.kt`) is
ported from **[Homestead](https://github.com/Sarg338/Homestead)** (GPL-3.0) by Sarg338, which
solved this with an accessibility service plus a `READ_LOGS` logcat watcher. Hearth reuses
that approach and its security posture (`canRetrieveWindowContent=false`, no key filtering,
the logcat watcher trusts only lines the log daemon stamps as written by the system's own
uid) almost verbatim, retargeted to Hearth's package and Kotlin. Because Homestead is
GPL-3.0, Hearth is too.

## Building

Requires JDK 21, the Android SDK (`compileSdk 35`, `platform-tools`, `build-tools 34.0.0`),
and network access for Gradle/Maven dependencies the first time.

```sh
./gradlew testReleaseUnitTest assembleRelease
```

The build copies every theme folder from `../themes/<id>/` into the APK's
`assets/themes/<id>/` and fails with a clear error if `themes/default/theme.json` is
missing, since the theme contract requires a default theme to always exist.

## Installing on the device

Your devices, name, weather location and service addresses live in one file,
`../config/site.env` (gitignored). Every script reads it.

```sh
cp ../config/site.env.example ../config/site.env   # then edit it
tools/make-keystore.sh     # creates ~/.hearth-keys/hearth-release.keystore if missing
tools/install.sh           # builds, installs, grants READ_LOGS, sets Hearth as the Home redirect
../tools/make-config.py    # renders ../config/hearth.local.json from site.env
tools/push-config.sh ../config/hearth.local.json
```

From `fire-tv/`, the same steps are `make setup`, `make install` and `make push-config`
(`make` lists the rest).

`install.sh` points `enabled_accessibility_services` at Hearth's `HomeRedirectService` only.
Unset widgets (weather, guild events, Uptime Kuma, AdGuard, TV controls) stay hidden.

## Remote bridge

`homekey/RemoteBridge.kt` listens on TCP 47800 (LAN clients only) for one word per connection
from the TV (`../../tcl-tv/bridge/tcl-keys.sh`): jellyfin, netflix, prime, youtube, hbo,
disney, hearth, settings. It wakes the stick and opens that app. It lives in the
accessibility service, so it starts at boot and its activity starts are not blocked.
`widgets/TvLink.kt` talks the other way, to the TV's listener on TCP 47801 (config `tv`).

## Architecture

- `homekey/` -- the Home redirect (ported from Homestead, see above), the remote bridge, and
  `CoverSnapshot`, the picture of the home screen the redirect shows while Hearth comes up.
- `theme/` -- theme.json model, validation, season resolution ("Automatic"), particle
  effects, bundled/downloaded theme loading.
- `catalog/` -- remote theme catalog client: fetch, sha256/size verification, zip-slip
  protection, atomic install.
- `config/` -- the pushed-config contract: data model, app-private storage, and the
  write-only `ConfigProvider` (`content://dev.jacobcoffee.hearth.config/config`).
- `widgets/` -- weather (Open-Meteo), guild events (SideQuest), Uptime Kuma, AdGuard Home,
  and the TV link.
- `apps/` -- the app lists (TV apps and user-installed apps for the grid, Fire OS's own
  launchers for the Apps panel), ordering, hiding, pushed layouts, Fire TV settings.
- `ui/` -- Compose for TV: `home/` (header, quest log, command rail, panels, search, sigils,
  chamber, ornaments), the adaptive app grid with per-theme tile frames, and settings.

## Tests

`app/src/test` covers the edge logic that is worth testing in isolation: season-window
resolution (wrap-around, priority, ties, no-match), theme.json validation, and the catalog
installer's zip-slip and sha256-mismatch rejection. Everything else (UI, network widgets,
the home-redirect service) is exercised on-device.
