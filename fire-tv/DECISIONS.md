# Fire TV: decisions and state

## Device
- Fire TV Stick 4K, model `AFTKRT` (codename karat), Fire OS 8.1.8.5 / Android 11 (SDK 30), build `RS8185.3898N` (OTA 2026-11-02; set up on 8.0 `RS8160.3380N`). Plugged into HDMI 2 of the TCL QM8L (`../tcl-tv`).
- ADB over Wi-Fi at `FIRETV_HOST:5555` (`hearth/config/site.env`); this Mac's key is authorized. **Reserve the stick's address in DHCP**: the TV bridge and Hearth's config use it, and Hearth reaches the TV at `TV_HOST`.
- 1.7 GB RAM. Free memory before debloat: 414 MB MemAvailable, 304 processes. After: 538 MB, 288 processes.

## Debloat (`./firetv.sh apply`, list in `packages.txt`)
- Scope: ads, telemetry, tutorials, unused Amazon apps, and the Alexa voice pieces (privacy audit 2026-11-01, `reports/privacy-audit.md`). `com.amazon.vizzini` stays: disabling it can show "Unable to detect your remote". Voice search through the mic button still worked afterwards.
- Amazon apps **kept**: Silk browser, Live tab + Channels, Amazon Kids, Appstore (`com.amazon.venezia`, needed for installs and updates).
- Amazon apps **disabled**: Amazon Music, Amazon Photos, Freevee, Echo home audio, Show on Fire TV.
- **Re-enabled 2026-11-01**: Prime Video (`com.amazon.firebat`) and the Games/Luna hub (`com.amazon.gamehub`); commented out in `packages.txt`. The stick stays for Luna, Xbox and GeForce NOW, and the TCL remote's app buttons open apps here.
- Never touch: `com.amazon.ceviche` (disabling it crash-loops Amazon's launcher), `com.amazon.tv.launcher`, OTA, settings, input/HDMI/Wi-Fi/Bluetooth packages.
- Protected by Fire OS 8.0 (`pm` refuses them): `ftvads.deeplinking`, `client.metrics`, `device.metrics`, `diode`, `device.crashmanager`, `device.logmanager`. Commented out in `packages.txt`; their backend hosts are blocked at the DNS level instead (`ansible/roles/adguard/files/firetv-telemetry.txt`, see "DNS" below).
- `com.amazon.device.telemetry.emitter` is not on Homestead's checked list; I added it for this device. If anything misbehaves, re-enable it first.

## Tweaks (`./firetv.sh tweaks`)
- Animation scales 0.5; `amz_limit_ad_tracking=1`, `limit_ad_tracking=1`, `non_amz_limit_ad_tracking=1`, `usage_metrics_marketing_enabled=0`. These persist.
- Background process limit 2 via `service call activity 43 i32 2`. **Not persisted by Android**: run `./firetv.sh tweaks` after every reboot. Transaction codes 43/44 come from this device's `framework.jar` (re-checked on `RS8185.3898N`: unchanged); re-check after a Fire OS update.

## Apps added
- Wholphin `com.github.damontecres.wholphin` v1.0.9 (GPL-2.0), the Jellyfin client in use: sideloaded from GitHub, armeabi-v7a build (the stick is 32-bit; sha256 `c6b3cc91…cb3df`, matches the release digest; `apk/`). The remote's Jellyfin button (bridge word `jellyfin`) and the first Hearth tile open it.
- Jellyfin `org.jellyfin.androidtv` v0.19.10, sideloaded from GitHub (sha256 `8510a517…4628`, matches the release digest; `apk/`). Still installed as a fallback, hidden from Hearth. Works with the 12.4 server (Direct Play verified); 0.20, still beta, needs 10.11+.
- Both reach Jellyfin at `https://media.z7x.org:8920`, signed in as `jacob` with Quick Connect. The stick is on the house LAN and can't route to the lab, and server discovery (UDP broadcast) never crosses OPNsense, so "no servers found" is expected. AdGuard answers `media.z7x.org` with the WAN CARP address for the `firetv` client only (`ansible/roles/adguard/files/firetv-telemetry.txt`), and OPNsense forwards WAN 8920 to the internal gateway's 443 (`ansible/roles/opnsense/tasks/firetv_dot.yml`). Video stays on the LAN instead of going out through the Cloudflare tunnel. 443 on that address is the OPNsense GUI, hence 8920.
- YouTube `com.amazon.firetv.youtube`, Xbox `gamepass.fire.tv`, Spotify `com.spotify.tv.android`: Amazon Appstore.

## Home screen: Hearth (`hearth/`, GPL-3.0)
- Our own launcher; replaced Homestead (uninstalled 2026-11-01). Home redirect ported from Homestead: accessibility service `HomeRedirectService` + `READ_LOGS` watcher. The redirect fires only for Amazon's home activity (`HomeActivity_vNext`); Fire TV settings and the "Amazon Home" app pause it until Hearth is back in front.
- Hearth is self-contained in `hearth/` (own Makefile, tools, config, themes, test stack) and mirrored to github.com/JacobCoffee/hearth by `.github/workflows/mirror-hearth.yml`; this directory's Makefile passes Hearth targets through to it. `hearth/tools/install.sh` builds, installs, grants READ_LOGS and makes Hearth the only accessibility service. Config: `hearth/tools/make-config.py` renders `hearth/config/hearth.local.json` (gitignored) from the committed `hearth/config/hearth.example.json` plus `hearth/config/site.env` (gitignored: addresses, name, weather location, service logins; template `hearth/config/site.env.example`; `firetv.sh` and `../tcl-tv` read it too); `hearth/teststack/up.sh` runs it with the test stack's endpoints. Push with `hearth/tools/push-config.sh`.
- Grid order and hidden apps come from the config's `apps` block and are applied once per change; Arrange on the TV can change them afterwards.
- Remote-only use (the TCL remote reaches the stick with arrows, OK, Back and play/pause only): everything lives on the left command rail (Search, Arrange, Apps, Inputs, TV settings, Power, Controllers, Homelab, Theme, Settings). Search opens Prime Video and Disney+ straight to results (verified); Amazon's own search needs a signature permission.
- Controllers opens Fire TV settings: Fire OS PIN-gates its direct controllers deep links for outside apps and then opens nothing.
- Now Playing: the strip under the header shows another app's media session (cover, title, artist, time bar, previous / play-pause / next) next to one guild quest. Sessions come from `MediaSessionManager.getActiveSessions`, which needs Hearth to be an enabled notification listener (`install.sh`: `cmd notification allow_listener`); `MEDIA_CONTENT_CONTROL` would also work but is system-only.
- Themes: `hearth/themes/`, built into the APK and served as a catalog by `hearth/tools/build-catalog.py`. Theme contract additions: `wallpaperMotion`, `focusLight`, `tile.frame` (`plain` | `chamber`).
- Theme art: SideQuest's painting and lion crest are the author's own work (from the guild site). Christmas, New Year and Date Night art is generated by `hearth/tools/paint-themes.py` (rerun to rebuild). Fonts are OFL: each theme's `OFL.txt` carries the upstream copyright, and generated fonts are renamed ("Hearth <Theme> Display/Body") because pinning and subsetting make them modified versions.

## TCL remote ↔ Fire TV bridge
- TCL → stick: `../tcl-tv/bridge/tcl-keys.sh` sends one word to Hearth's listener on TCP 47800 (hosted by Hearth's accessibility service, so it starts at boot; LAN clients only). Words: jellyfin, netflix, prime, youtube, hbo, disney, hearth (Guide), settings (gear while on HDMI 2).
- Stick → TCL: Hearth sends words to `../tcl-tv/bridge/tcl-listen.sh` on TCP 47801: tvsettings, tvhome, hdmi1–4, off, sleep:<minutes>, sleep:cancel.
- The TCL half is shell processes: after a TCL reboot run `../tcl-tv/bridge/bridge.sh start` (TCL Wireless debugging on).

## After a reboot or Fire OS update
A k8s CronJob (`kubernetes/apps/firetv/reapply`, every 5 min) now runs this automatically: it
connects, checks `getprop debug.homelab.applied` (cleared by Android on every reboot), and if
empty re-applies the debloat list and tweaks, then sets the marker. Manually, or to debug it:
```sh
./firetv.sh connect
./firetv.sh apply     # re-disables anything Amazon switched back on
./firetv.sh tweaks    # process limit is lost on reboot (Private DNS persists)
./firetv.sh status
```
If Home goes back to Amazon's screen after an update, run `hearth/tools/install.sh` again.

Fire OS 8.1.8.5 (2026-11-02) re-enabled Amazon Music, Shopping, Kindle connect and the Alexa media player, and added a "FireTV Troubleshooting" TV app whose banner is a sizeless gradient `<shape>`. Hearth crashed rasterizing it on every start; the crash took the accessibility service down with it, so Fire OS switched accessibility off (`accessibility_enabled=0`) and Home, Guide and the remote's app buttons all stopped reaching Hearth. Hearth now drops sizeless banners and gives sizeless icons a fallback size. If Home and the app buttons die together again, check `adb logcat -b crash` for Hearth first.

## Undo
- `./firetv.sh revert` re-enables every package in `packages.txt`; `./firetv.sh untweak` restores the defaults.
- `adb uninstall dev.jacobcoffee.hearth`, then `settings put secure enabled_accessibility_services null`, gives Home back to Amazon.

## Risk accepted
- ADB debugging stays on, so anything on the LAN can try to connect. Each new key still needs approval on the TV.
- `READ_LOGS` lets Hearth read the whole system log; it uses only ActivityManager lines from the system process.
- Hearth is an enabled notification listener (`media/MediaListener`), which Android requires before an app may read other apps' media sessions. Hearth never reads notifications through it; it only follows media sessions while Home is in front.
- TCP 47800 (stick) and 47801 (TCL) accept a fixed word list from anyone on the LAN; the worst a stranger can do is open an app, switch input or turn the TV off.
- WAN 8920 forwards to the internal gateway for anything on the house LAN, not only the stick: the stick's address is a DHCP lease, so a source filter would silently break if it changed. Every app there keeps its own gate (Pocket ID through lab-sso, or its own login); what's new is house-LAN devices reaching them without the tailnet.

## DNS (AdGuard, whole-LAN)
- DNS-over-TLS to AdGuard: `./firetv.sh tweaks` sets Android Private DNS to `firetv.dns.z7x.org`
  (persists across reboots). AdGuard serves DoT on 853 and its HTTPS API on 443, both forwarded
  from the OPNsense WAN CARP address (853->853, 8443->443; `ansible/roles/opnsense/tasks/firetv_dot.yml`)
  with a Let's Encrypt cert for `dns.z7x.org`/`*.dns.z7x.org` (`kubernetes/apps/network/adguard-dot`:
  a cert-manager `Certificate` plus a CronJob that pushes it into both AdGuard instances through
  `/control/tls/configure` every 6h, so renewal reaches them automatically -- proved by forcing a
  reissue and re-push during setup). Public `dns.z7x.org`/`*.dns.z7x.org` A records point at the WAN
  CARP address (`ansible/roles/cloudflare_cache_rules/tasks/dns.yml`); OPNsense's Unbound needed
  `dns.z7x.org` added to its private-domain allowlist (`advanced.privatedomain`) since it otherwise
  strips RFC1918 answers (DNS rebind protection) -- the first `*.z7x.org` record ever to point at a
  private address. The stick presents SNI `firetv.dns.z7x.org`, which AdGuard reads as ClientID
  `firetv`; a persistent client of that name is registered via the API
  (`ansible/roles/adguard/tasks/firetv_dot.yml`). Plain DNS (:53) and the HTTP UI/API (:80) are
  unchanged (`force_https: false`) for the lab, `external-dns-adguard` and `adguard-exporter`.
- Telemetry blocklist: `ansible/roles/adguard/files/firetv-telemetry.txt`, a local filter list
  (not `user_rules`, which `external-dns-adguard` owns) with every rule scoped `$client=firetv` so
  it can't affect other house devices. Covers the backend hosts of the protected packages above
  (device-metrics-us.amazon.com, unagi-*.amazon.com, prod.amazoncrl.com, etc.) and the Alexa hosts
  from `reports/privacy-audit.md` (api.amazonalexa.com, avs-alexa-*.amazon.com, etc.), cross-checked
  against Perflyst/PiHoleBlocklist and ultimate-pihole-list's Fire TV lists; hosts flagged
  "required for app install/updates" or video-metadata-adjacent (msh.*, mas-*, dp-*, fls-*) are
  deliberately left off. Verified end-to-end: the stick resolves `bob-dispatch-prod-na.amazon.com`
  (Alexa-only) to `0.0.0.0` over its DoT connection while a plain (non-firetv) query for the same
  host resolves normally; Netflix/YouTube/Amazon API hostnames still resolve. Spot-check on the TV:
  Prime Video, Netflix, YouTube and Appstore installs/updates.

## Homelab services (Hearth's Uptime Kuma / AdGuard / theme-catalog widgets)
- Uptime Kuma: `kubernetes/apps/status/uptime-kuma` (namespace `status`, SQLite on a
  `proxmox-vmdata` PVC, `strategy: Recreate`), all at `status.z7x.org`. The public status page
  (`/status/home`, its JSON under `/api/status-page/`, assets, badges) is open on both gateways
  and `/` redirects there from the internet; the admin UI is internal gateway only, behind the
  Pocket ID gate (`kuma.z7x.org` redirects to it). Monitors for every service on the start page,
  in the start page's groups, and the `home` status page are seeded idempotently by the
  `kuma-seed` CronJob (`uptime-kuma-api2`, same approach as `hearth/teststack/seed-kuma.py`).
- Theme catalog: `kubernetes/apps/network/theme-catalog`, public at `themes.z7x.org`. An
  initContainer shallow-clones the public mirror `github.com/JacobCoffee/hearth` at a pinned
  commit SHA and runs `tools/build-catalog.py`; bump the SHA in `app/deployment.yaml` to update.
- AdGuard's HTTP API stays house-LAN only at `https://dns.z7x.org:8443` (see "DNS" above) --
  never exposed through the cluster Gateways.
- `hearth/config/site.env` (gitignored) points `KUMA_URL`/`ADGUARD_URL`/`THEME_CATALOG_URL` at
  these; `tools/make-config.py` + `tools/push-config.sh` render and push `hearth.local.json`.
  `hearth/teststack/` still exists for local iteration without touching the stick.
