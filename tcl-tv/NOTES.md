# TCL QM8L cleanup — state & decisions

TV: TCL QM8L "Smart TV Pro", board G17, product G17_4K_US, Android 14 (SDK 34), firmware UTT2.250416.001 AU27.
Identical to upstream tested list `cleanyourtv/examples/tcl-65qm8l-google-tv`.
Addresses live in `../fire-tv/config/site.env` (`TV_HOST`, `FIRETV_HOST`; template `site.env.example`). The Fire TV is also on adb — always pass `-s`.

## Layout
- `cleanyourtv/` — upstream clone (tools, launcher). `tools/venv` created there.
- `debloat.txt` — this TV's list (apply/restore source of truth).
- `launcher-config/` — launcher theme overlay: functional keys (dock, hidden apps, `app_keys`) plus the "Local Dimming" look (see below). Push: `tools/theme.sh ../launcher-config`.
- `backup/<date>-pre-debloat/` — package lists, getprop, all settings, Home, wifi owner, meminfo before any change.
- `shots/` — screenshots from the TV.
- `~/.cleanyourtv/` — disable.sh's own restore record.

## Connecting (Wireless debugging only; connect port changes every reboot / Wi-Fi drop)
```
adb mdns services                              # shows _adb-tls-connect._tcp <ip>:<port>
adb connect $TV_HOST:<port>                   # already paired with this Mac
cd cleanyourtv && export TV_ADB_BINARY=adb TV_HOST=$TV_HOST:<port>
```
If unpaired: TV → Developer options → Wireless debugging → Pair with code; `adb pair <ip>:<pair-port> <code>`.

## Decisions (2026-11-01)
- Keep: Chromecast, AirPlay, Miracast, TCL firmware updates, YouTube, Netflix, Prime Video, Disney+, Hulu, TCL Game Bar (console ALLM/VRR).
- Disable: everything in upstream list + Assistant/Gemini (katniss), Tubi, YouTube TV, YouTube Music, Play Games, Haystack, Instagram, TCL Browser/TV+/Media Center.
- Replace Google TV home (launcherx) with upstream launcher `com.gathin.tvhome`; 4-app dock: Jellyfin, HBO Max, Apple TV, YouTube.
- Key helper ON (InputKeyService, INPUT_KEY_SERVICE=1, changed later on 2026-11-01): remote buttons remapped (table below). Cost: hold-to-ramp volume is one step per press. HOME_KEY_SERVICE stays off.
- Private DNS → dns.adguard-dns.com. Undo: `settings put global private_dns_mode opportunistic`.
- Baseline before: MemAvailable 637 MB / 2.29 GB; launcherx ~274 MB PSS, katniss ~119 MB PSS.
- Power-on: keep firmware behaviour (lands on Home, then TCL TV app switches to last input ~40 s later = Fire TV on HDMI). No HOME_KEY_SERVICE.
- Wireless debugging LEFT ON at owner's request (2026-11-01). Closing procedure below.
- Keep the Fire TV Stick 4K Max gen 2 on HDMI 2 for Luna + Xbox Cloud Gaming (no Xbox console; neither app exists for this TV yet). Guide button → HDMI 2. Revisit if TCL ships the Xbox app to the QM8L.

## Result (verified after reboot, 2026-11-01)
- 34 packages disabled, all still disabled after reboot. Home = com.gathin.tvhome (mFocusedApp confirmed). Wi-Fi owner moved to com.android.tv.settings.
- MemAvailable 637 MB → 850 MB (+212 MB). Home process 274 MB (launcherx) → 41 MB (tvhome). katniss (119 MB) gone.
- Private DNS = hostname / dns.adguard-dns.com, survived reboot.
- Installed via Play Store (ADB-driven): Jellyfin `org.jellyfin.androidtv`, HBO Max `com.wbd.stream`, Apple TV `com.apple.atve.androidtv.appletv`, GeForce NOW `com.nvidia.geforcenow`. Luna has no official Google TV app.

## Remote button remapping (active)
`app_keys` in `launcher-config/config.json`, read by InputKeyService. Keycodes read from `logcat` `InputReader: TIDD: keyCode=` (all buttons share scancode 240). Confirmed on the real remote 2026-11-01.

| Button | Keycode | Opens |
|---|---|---|
| Free TV | 297 (FEATURED_APP_1) | HDMI 2 + Jellyfin on Fire TV |
| Netflix | 4062 | HDMI 2 + Netflix on Fire TV |
| Prime Video | 4099 | HDMI 2 + Prime Video on Fire TV |
| YouTube | 190 (BUTTON_3) | HDMI 2 + YouTube on Fire TV |
| Star ★ | 313 (MACRO_1) | HDMI 2 + HBO Max on Fire TV |
| Disney+ | 4141 | HDMI 2 + Disney+ on Fire TV |
| Guide | 4001 | HDMI 2 + Hearth (Fire TV home, `../fire-tv/hearth`) |
| Gear (settings) | 83 (KEYCODE_NOTIFICATION, scanCode 240) | On HDMI 2: Hearth's settings via `tcl-keys.sh`. Otherwise: TCL's own settings (`am start -n com.android.tv.settings/.MainSettings`). Not in `app_keys` — it has no HDMI-2 passthrough mapping, unlike the other seven. |

All seven app buttons: TCL side `app_keys` = `input:com.tcl.tvinput/.TvPassThroughService/HW16` (HDMI 2; Fire TV CEC physical address 0x2000).

## Remote ↔ Fire TV bridge (2026-11-01)
`bridge/`: two TCL-side shell processes, managed by `bridge/bridge.sh start|stop|status` (**after a reboot of the TCL or Fire TV, run `bridge.sh start`**; needs TCL Wireless debugging on). Hearth (`../fire-tv/hearth`) hosts the Fire TV side itself on TCP 47800; `bridge.sh` no longer starts anything on the Fire TV.
- `tcl-keys.sh` (TCL → Hearth, port 47800): follows `logcat InputReader` `TIDD: keyCode=` lines, sends one word (`jellyfin netflix prime youtube hbo disney hearth settings`) to `$FIRETV_HOST:47800` with `nc` (bridge.sh passes the address). The gear key (83) only sends `settings`, and only when `dumpsys tv_input | grep -A2 sessionStateMap | grep -q HW16` (i.e. already on HDMI 2/Fire TV); otherwise it opens TCL settings locally and sends nothing.
- `tcl-listen.sh` (Hearth → TCL, port 47801): runs under `nc -L -p 47801` on the TCL, one word per connection, newline-terminated, unknown words ignored.
  | Word | Action |
  |---|---|
  | `tvsettings` | `am start -n com.android.tv.settings/.MainSettings` |
  | `tvhome` | `am start -a android.intent.action.MAIN -c android.intent.category.HOME` |
  | `hdmi1`..`hdmi4` | Switch input: `am start -a android.intent.action.VIEW -d content://android.media.tv/passthrough/<url-encoded inputId> -t vnd.android.cursor.item/channel` (`inputId` = `com.tcl.tvinput/.TvPassThroughService/HW15`..`HW18`) — the same passthrough URI the launcher's own Inputs menu and `app_keys` `input:` targets use (`RemoteKeys.launch()` in `cleanyourtv`). |
  | `off` | `input keyevent KEYCODE_SLEEP` now, cancelling any pending timer first |
  | `sleep:<1..240>` | Standby after that many minutes; replaces any running timer. PID of the `sleep` child kept in `/data/local/tmp/tcl-sleep.pid` so a later `sleep:<n>` or `sleep:cancel` can `kill -9` it before it fires (plain `kill`/SIGTERM does not interrupt the TCL's `sleep` builtin). Non-integer or out-of-range minutes are ignored. |
  | `sleep:cancel` | Cancel the pending timer, if any |
- Retired: `firetv-launch.sh` (the old Fire TV `nc -L -p 47800` listener) — Hearth replaced it; no references remain.
Verified on the real TV 2026-11-01: `hdmi1`/`hdmi2`/`tvhome`/`tvsettings` all switch/confirmed via `dumpsys tv_input`/`dumpsys activity activities`; `sleep:5` then `sleep:5` again (replaces, doesn't stack) then `sleep:cancel` all confirmed via pid file + `ps`. Not fired: `off` (would actually suspend the TV). Gear's HDMI-2 branch condition verified while on HDMI 2 (takes the Fire TV branch); the keycode-83 press itself was not injected.
The Fire TV's address is passed to the TCL by `bridge.sh start`: reserve it in DHCP, and re-run `bridge.sh start` if it changes.

Change a mapping: edit `app_keys`, then `cd cleanyourtv && TV_ADB_BINARY=adb TV_HOST=<ip:port> tools/theme.sh ../launcher-config`.
Reinstall launcher with helper: `INPUT_KEY_SERVICE=1 RESTORE_KEY_SERVICES=1 SKIP_BUILD=1 launcher/build-and-install.sh`.
Get volume ramp back (buttons go dead): `tools/tv.py "settings put secure enabled_accessibility_services ''"`.
Find a new button's code: `adb -s <S> logcat -c; adb -s <S> logcat -v time | grep 'TIDD: keyCode'`, press it.

## Theme: Local Dimming (designed 2026-11-01, NOT pushed yet)
Own look in `launcher-config/`, replacing the launcher defaults (the radial glow in the focused app's colour). Idea: the QM8L's own mini-LED backlight, rendered. Near-black `#030407`; one soft light per time of day, sampled into an 80×45 zone grid, each zone's LED visible as a point where it runs hot, spread through a diffuser with a long faint halo (the blooming). Light sits right of the hero name and behind the dock's top edge, so the tiles are backlit and the clock/buttons stay on black.
- Wallpapers: `launcher-config/wallpapers/{morning,evening,night}-{1,2,3}.jpg`, drawn by `launcher-config/make_wallpapers.py` (`uv run --script`; edit `MOODS`/`EXPOSURE` and rerun). Morning = cold ice/blue, evening (noon→sunset+30 min) = amber/rose, night = cobalt/violet (dimmest). Moods switch at 07:00/18:30 (no lat/long in config.xml); inside a mood the 3 frames rotate every 20 min, the light drifting a little.
- Look keys: no dock band, no ambient/app glow, `glass_style: halo` (white rim + soft glow), no overshoot, `fade_rows`, no greeting, icon-only top buttons, green Wi-Fi when online, 32-bit wallpaper decode (dark gradients band in RGB_565). Clock screensaver after 10 min idle on Home, over the wallpaper at 55% dim.
- Browser preview: `python3 -m http.server 8765` in this folder, open `http://127.0.0.1:8765/launcher-config/preview/`. Reads `config.json` + `wallpapers/` live; arrows move focus, `1`/`2`/`3` pin morning/evening/night, `0` follows the clock, `F` next frame, `T` 5 s rotation, `C` clock screensaver, `H` hides the key help. An approximation (layout measured from `shots/06`, tile art cropped from Hearth's mock, Apple TV tile is a placeholder), not a TV capture; `theme.sh` never pushes `preview/`. After pushing, take a real one with `tools/tv.py --screenshot`.
- Push: `cd cleanyourtv && TV_ADB_BINARY=adb TV_HOST=<ip:port> tools/theme.sh ../launcher-config`. Any later push (e.g. an `app_keys` change) carries the look too.
- Revert to the old look: delete the keys after `app_keys` in `config.json` and the `wallpapers/` folder, push again.

## Soundbar (Samsung HW-B750F on HDMI 1 ARC/eARC)
Checked 2026-11-01, no changes needed. TV sees it over CEC (logical 0x05, physical 0x1000, port 1). ARC established, System Audio Mode on, audio routed to `hdmi_arc`.
TV remote volume → CEC `<User Control Pressed> Volume up/down` → soundbar ACKs and reports its level (verified with both injected and real-remote presses).
Link runs as plain ARC (eARC status 2 = ARC pending/fallback). Soundbar advertises Dolby Digital 5.1 + DTS 5.1 only.
Power: `tv_send_standby_on_sleep=1`, `power_control_mode=broadcast` → TV standby turns the soundbar (and Fire TV) off too.
Check: `adb -s <S> shell dumpsys hdmi_control | grep -E 'User Control|Audio Status' | tail`.

## Closing ADB later
1. Note the port: `adb mdns services` (`_adb-tls-connect._tcp`).
2. TV: Developer options → Wireless debugging OFF, USB debugging OFF, Revoke USB debugging authorizations.
3. `cleanyourtv/tools/venv/bin/python cleanyourtv/tools/adb-closed.py $TV_HOST <port>` → must say closed.
4. Reboot TV, then `adb-closed.py $TV_HOST` (5555) → closed, and confirm Wireless debugging still off.

## Undo everything
```
cd cleanyourtv && TV_ADB_BINARY=adb TV_HOST=$TV_HOST:<port> tools/restore.sh ../debloat.txt com.google.android.apps.tv.launcherx
TV_ADB_BINARY=adb TV_HOST=... tools/tv.py 'settings delete secure screensaver_components'
TV_ADB_BINARY=adb TV_HOST=... tools/tv.py 'settings put global private_dns_mode opportunistic'
```
Re-enable one app: TV Settings → Apps → See all apps → Show system apps → Enable, or `tools/tv.py 'pm enable <pkg>'` and comment its line in `debloat.txt`.
