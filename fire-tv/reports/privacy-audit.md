# Fire TV Stick 4K (AFTKRT) privacy audit — new candidates

Run 2026-11-01 with `cleanyourtv/tools/privacy.py` against `TV_HOST=$FIRETV_HOST` (current
debloated state, Homestead active). Full report: `privacy.py` (no args); candidates list written to
`reports/privacy-candidates.txt` with `--write … --include-low`.

286 packages installed, 254 enabled. The audit flagged 10 packages by name/firmware-nomination; 2 of
those (`com.amazon.tv.acr`, `com.amazon.device.telemetry.emitter`) are already disabled and listed in
`$FT/packages.txt`, so they're not repeated here. The 8 below are genuinely new — none appear in
`packages.txt`, `DECISIONS.md`, or as already disabled in `inventory/2026-11-01-disabled-packages.txt`;
all 8 are present (enabled) in `inventory/2026-11-01-system-packages.txt`.

Evidence columns reproduce exactly what `privacy.py` printed for each package (its own appops/UID/
"named by the TV" checks) plus context from public package references, cited where used.

## `com.amazon.vizzini` — recommendation: **keep**

- **privacy.py evidence**: "named by the TV: assistant" (it's the value of the `assistant` secure
  setting — the firmware's own pick for the voice assistant); declares `RECORD_AUDIO`; appops shows
  a recorded `RECORD_AUDIO` access in its returned history.
- **Likely does**: Amazon's on-device Alexa/voice-recognition service for Fire TV — not just the
  assistant backend, but, per community debloat documentation, a "persistent system app behind voice
  **and the remote**" (dumbtv.org/blocked).
- **Breakage risk**: **high, and broader than voice**. Public debloat guidance (dumbtv.org) explicitly
  puts `com.amazon.vizzini` in its "do not disable" list: *"Disabling it does not stop it, and on some
  TVs it then covers the screen with 'Unable to Detect Your Remote'."* That means disabling it risks
  losing D-pad/remote pairing entirely (not just the mic button) while not even reliably stopping the
  service — the opposite of the privacy goal. Separately, other sources (XDA, dumbtv) describe it as
  the Alexa voice-assistant and media-session owner, so voice search and the mic button would also be
  expected to break if disabling did take effect.
- **Recommendation**: keep enabled. If the goal is to stop Alexa network calls rather than the local
  service, block its backend hosts (`api.amazonalexa.com`, `avs-alexa-*.amazon.com`, etc. — same hosts
  dumbtv's DNS filter uses) instead of disabling the package.

## `com.amazon.alexadirectivebrokerservice` — recommendation: **disable**

- **privacy.py evidence**: matched by name only (`directivebroker` is not in the stem list but the TV
  didn't nominate it either — picked up as a name match); no RECORD_AUDIO appops history returned.
- **Likely does**: routes Alexa "directives" (parsed voice commands) from the cloud to the app/service
  that should act on them — per dumbtv.org, "part of Alexa and the voice remote."
- **Breakage risk**: voice search / Alexa voice commands via the remote's mic button stop working.
  No casting, OTA, or general D-pad input impact documented.
- **Recommendation**: disable, if Alexa voice control is not used. `tools/disable.sh` records the
  undo first.

## `com.amazon.alexamediaplayer.runtime.ftv` — recommendation: **disable**

- **privacy.py evidence**: name match only; no RECORD_AUDIO appops history.
- **Likely does**: lets Alexa route "play X" voice commands to a media player on the device — per
  dumbtv.org, "part of Alexa and the voice remote."
- **Breakage risk**: voice-driven playback ("Alexa, play …") stops; no casting/OTA/D-pad impact noted.
- **Recommendation**: disable if Alexa voice control is not used.

## `com.amazon.alexa.datastore.app` — recommendation: **disable**

- **privacy.py evidence**: name match only; no RECORD_AUDIO appops history.
- **Likely does**: local state/cache store backing the Alexa voice remote integration — per
  dumbtv.org, "part of Alexa and the voice remote."
- **Breakage risk**: voice button / Alexa on the TV; no casting/OTA/D-pad impact noted.
- **Recommendation**: disable if Alexa voice control is not used.

## `com.amazon.tv.alexanotifications` — recommendation: **disable**

- **privacy.py evidence**: name match only; no RECORD_AUDIO appops history.
- **Likely does**: displays Alexa notifications/announcements on screen — per dumbtv.org, "part of
  Alexa and the voice remote."
- **Breakage risk**: Alexa notification banners; no casting/OTA/D-pad impact noted.
- **Recommendation**: disable if Alexa voice control/notifications are not used.

## `com.amazon.tv.alexaalerts` — recommendation: **disable**

- **privacy.py evidence**: name match only; no RECORD_AUDIO appops history.
- **Likely does**: Alexa alerts/timers/reminders UI on Fire TV — per dumbtv.org, "part of Alexa and
  the voice remote."
- **Breakage risk**: Alexa alert/timer display; no casting/OTA/D-pad impact noted.
- **Recommendation**: disable if Alexa alerts/timers are not used.

## `com.amazon.alexa.externalmediaplayer.fireos` — recommendation: **disable**

- **privacy.py evidence**: name match only; no RECORD_AUDIO appops history.
- **Likely does**: registers third-party media apps (Spotify, etc.) as Alexa-controllable players, so
  "Alexa, play Y on Spotify" can hand off to them. tvdebloat.com rates it "caution (documented
  trade-offs)"; dumbtv.org files it under "part of Alexa and the voice remote."
- **Breakage risk**: voice handoff to third-party media apps stops; the apps themselves (and their own
  casting) are unaffected — this only removes the Alexa-voice integration point. No OTA/D-pad impact.
- **Recommendation**: disable if Alexa voice control of third-party media apps is not used; tvdebloat's
  "caution" flag is about losing that specific voice handoff, not about general stability.

## `com.amazon.privacypassservice` — recommendation: **disable**

- **privacy.py evidence**: name match only (`privacy` stem); no RECORD_AUDIO appops history.
- **Likely does**: despite the name, this is **not** a privacy-protection feature — per dumbtv.org it
  "issues tokens used by Amazon's ad system" (filed under Ads/promos/shopping, rated "safe to
  disable"); tvdebloat.com corroborates it as an "Amazon privacy pass token service" at "low risk."
  It implements the IETF Privacy Pass protocol as an anti-fraud token for ad requests, not a
  user-facing privacy tool.
- **Breakage risk**: none documented beyond the ad/promo rows it feeds; no voice search, casting, OTA,
  or input impact.
- **Recommendation**: disable.

## Summary

| Package | Category | State | Recommendation |
|---|---|---|---|
| com.amazon.vizzini | voice (+ remote input) | enabled | **keep** |
| com.amazon.alexadirectivebrokerservice | unknown→Alexa | enabled | disable |
| com.amazon.alexamediaplayer.runtime.ftv | unknown→Alexa | enabled | disable |
| com.amazon.alexa.datastore.app | unknown→Alexa | enabled | disable |
| com.amazon.tv.alexanotifications | unknown→Alexa | enabled | disable |
| com.amazon.tv.alexaalerts | unknown→Alexa | enabled | disable |
| com.amazon.alexa.externalmediaplayer.fireos | unknown→Alexa | enabled | disable |
| com.amazon.privacypassservice | unknown→ad token | enabled | disable |

Apply with: `TV_HOST=$FIRETV_HOST tools/disable.sh reports/privacy-candidates.txt` after removing the
`com.amazon.vizzini` line (kept above) from that file — `tools/restore.sh` undoes it. Nothing on the
device was disabled by this audit; `reports/privacy-candidates.txt` is a list, not an action.
