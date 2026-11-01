#!/system/bin/sh
# Runs on the TCL as the shell user, once per connection (started by bridge.sh via `nc -L -p 47801`).
# Reads one word from Hearth (the Fire TV launcher) and acts on it. Unknown words are ignored.
TIMER_PID=/data/local/tmp/tcl-sleep.pid

passthrough() {
  # Same passthrough URI the launcher's Inputs menu / app_keys use (RemoteKeys.launch(), input: targets).
  am start -a android.intent.action.VIEW \
    -d "content://android.media.tv/passthrough/com.tcl.tvinput%2F.TvPassThroughService%2F$1" \
    -t vnd.android.cursor.item/channel >/dev/null 2>&1
}

cancel_timer() {
  kill -9 "$(cat "$TIMER_PID" 2>/dev/null)" 2>/dev/null
  rm -f "$TIMER_PID"
}

read -r word
case "$word" in
  tvsettings) am start -n com.android.tv.settings/.MainSettings >/dev/null 2>&1 ;;
  tvhome)     am start -a android.intent.action.MAIN -c android.intent.category.HOME >/dev/null 2>&1 ;;
  hdmi1)      passthrough HW15 ;;
  hdmi2)      passthrough HW16 ;;
  hdmi3)      passthrough HW17 ;;
  hdmi4)      passthrough HW18 ;;
  off)        cancel_timer; input keyevent KEYCODE_SLEEP ;;
  sleep:cancel) cancel_timer ;;
  sleep:*)
    mins="${word#sleep:}"
    case "$mins" in
      ''|*[!0-9]*) exit 0 ;;
    esac
    [ "$mins" -ge 1 ] && [ "$mins" -le 240 ] || exit 0
    cancel_timer
    ( sleep "$((mins * 60))" & spid=$!
      echo "$spid" > "$TIMER_PID"
      if wait "$spid"; then input keyevent KEYCODE_SLEEP; fi
      rm -f "$TIMER_PID" ) &
    ;;
  *) exit 0 ;;
esac
