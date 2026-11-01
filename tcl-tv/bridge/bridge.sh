#!/usr/bin/env bash
# Start/stop the TCL-side half of the remote bridge: tcl-keys.sh (remote app buttons + gear -> Fire TV) and
# tcl-listen.sh (Hearth -> TCL control, port 47801). Both are shell-user processes on the TCL, so they stop
# when it reboots: run `bridge.sh start` again after a TCL reboot (or a firmware update).
# Hearth hosts the Fire TV half itself (port 47800); this script only reports whether it's reachable.
#
# Usage: bridge/bridge.sh start|stop|status
set -euo pipefail
cd "$(dirname "$0")"
# Addresses come from ../../fire-tv/config/site.env (FIRETV_HOST, TV_HOST); FIRE_IP/TCL_IP override.
source ../../fire-tv/tools/site.sh
FIRE_IP="${FIRE_IP:-${FIRETV_HOST:?set FIRETV_HOST in fire-tv/config/site.env}}"
TCL_IP="${TCL_IP:-${TV_HOST:?set TV_HOST in fire-tv/config/site.env}}"
PORT=47800
LISTEN_PORT=47801

TCL=$(adb mdns services | awk -v ip="$TCL_IP" '$0 ~ ip && /tls-connect/ {print $3; exit}')
[ -n "$TCL" ] || { echo "TCL not advertising Wireless debugging: turn it on (Developer options)" >&2; exit 1; }
adb connect "$TCL" >/dev/null
t() { adb -s "$TCL" shell "$@"; }

# The [x] bracket keeps each pattern from matching the shell that runs it.
stop() {
  t 'kill $(cat /data/local/tmp/tcl-keys.pid 2>/dev/null) 2>/dev/null; pkill -f "[t]cl-keys.sh"; pkill -f "[l]ogcat -v raw -s InputReader"; pkill -f "[t]cl-listen.sh"; pkill -f "[n]c -L -p '"$LISTEN_PORT"'"; true'
}
status() {
  echo "Fire TV listener (Hearth, $FIRE_IP:$PORT): $(nc -z -w 2 "$FIRE_IP" "$PORT" && echo running || echo UNREACHABLE)"
  echo "TCL key watcher:   $(t "pgrep -f '[t]cl-keys.sh' >/dev/null && echo running || echo STOPPED")"
  echo "TCL listener:      $(t "pgrep -f '[t]cl-listen.sh' >/dev/null && echo running || echo STOPPED")"
}

case "${1:-}" in
  start)
    stop
    adb -s "$TCL" push tcl-keys.sh /data/local/tmp/ >/dev/null
    adb -s "$TCL" push tcl-listen.sh /data/local/tmp/ >/dev/null
    t "setsid nohup sh /data/local/tmp/tcl-keys.sh $FIRE_IP $PORT >/dev/null 2>&1 </dev/null &"
    t "setsid nohup nc -L -p $LISTEN_PORT sh /data/local/tmp/tcl-listen.sh >/dev/null 2>&1 </dev/null &"
    sleep 1; status ;;
  stop) stop; echo stopped ;;
  status) status ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
