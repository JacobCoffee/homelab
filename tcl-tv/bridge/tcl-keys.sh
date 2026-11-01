#!/system/bin/sh
# Runs on the TCL as the shell user (started by bridge.sh). Watches the remote's key log and tells the
# Fire TV which app button was pressed. The launcher's InputKeyService switches the TV to HDMI 2 for the
# same buttons (launcher-config/config.json), so the TCL's own copy of the app never opens.
FIRE="${1:?fire tv ip}"
PORT="${2:-47800}"
echo $$ > /data/local/tmp/tcl-keys.pid
last_app=; last_t=0
while true; do
  # -T: only presses from now on, so a restart never replays an old one.
  logcat -v raw -s InputReader:D -T "$(date +'%m-%d %H:%M:%S.000')" | while read -r l; do
    case "$l" in
      *"keyCode=297, action=ACTION_DOWN"*)  a=jellyfin ;;
      *"keyCode=4062, action=ACTION_DOWN"*) a=netflix ;;
      *"keyCode=4099, action=ACTION_DOWN"*) a=prime ;;
      *"keyCode=190, action=ACTION_DOWN"*)  a=youtube ;;
      *"keyCode=313, action=ACTION_DOWN"*)  a=hbo ;;
      *"keyCode=4141, action=ACTION_DOWN"*) a=disney ;;
      *"keyCode=4001, action=ACTION_DOWN"*) a=hearth ;;
      *"keyCode=83, action=ACTION_DOWN"*)
        # Gear: on HDMI 2 (Fire TV), forward Hearth's "settings" word; otherwise open the TCL's own settings.
        now=$(date +%s)
        if [ "$last_app" = gear ] && [ $((now - last_t)) -lt 2 ]; then continue; fi
        last_app=gear; last_t=$now
        if dumpsys tv_input | grep -A2 sessionStateMap | grep -q HW16; then
          echo settings | nc -w 2 "$FIRE" "$PORT" >/dev/null 2>&1 &
        else
          am start -n com.android.tv.settings/.MainSettings >/dev/null 2>&1 &
        fi
        continue
        ;;
      *) continue ;;
    esac
    now=$(date +%s)
    # A held button repeats ACTION_DOWN; send it once.
    if [ "$a" = "$last_app" ] && [ $((now - last_t)) -lt 2 ]; then continue; fi
    last_app=$a; last_t=$now
    echo "$a" | nc -w 2 "$FIRE" "$PORT" >/dev/null 2>&1 &
  done
  sleep 1
done
