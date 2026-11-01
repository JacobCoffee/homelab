#!/usr/bin/env bash
# Fire TV debloat state, kept in this directory. See DECISIONS.md.
#   ./firetv.sh connect | status | apply | revert | tweaks | untweak | snapshot
# The stick is FIRETV_HOST:FIRETV_ADB_PORT from config/site.env; FIRETV=<ip:port> overrides it.
set -euo pipefail
cd "$(dirname "$0")"
source tools/site.sh

TARGET="${FIRETV:-${FIRETV_HOST:?set FIRETV_HOST in config/site.env}:${FIRETV_ADB_PORT:-5555}}"
LIST=packages.txt
# IActivityManager transaction codes, read from this device's framework.jar
# (Fire OS 8.0, build RS8160.3380N). Re-check after a Fire OS update.
TX_SET_PROCESS_LIMIT=43
TX_GET_PROCESS_LIMIT=44
PROCESS_LIMIT=2

a() { adb -s "$TARGET" "$@" </dev/null; } # adb shell would otherwise eat a while-read loop's stdin

packages() {
  local pkg _
  while read -r pkg _; do
    [[ -z "$pkg" || "$pkg" == \#* ]] && continue
    printf '%s\n' "$pkg"
  done <"$LIST"
}

state_of() { # enabled | disabled | absent
  local pkg=$1
  if ! grep -qxF "package:$pkg" <<<"$ALL"; then echo absent
  elif grep -qxF "package:$pkg" <<<"$DISABLED"; then echo disabled
  else echo enabled; fi
}

load_state() {
  ALL=$(a shell pm list packages -s | tr -d '\r')
  DISABLED=$(a shell pm list packages -d | tr -d '\r')
}

connect() { adb connect "$TARGET"; a get-state; }

status() {
  load_state
  local pkg
  while read -r pkg; do printf '%-9s %s\n' "$(state_of "$pkg")" "$pkg"; done < <(packages)
  echo
  for k in window_animation_scale transition_animation_scale animator_duration_scale; do
    echo "global $k = $(a shell settings get global $k | tr -d '\r')"
  done
  for k in amz_limit_ad_tracking limit_ad_tracking non_amz_limit_ad_tracking usage_metrics_marketing_enabled; do
    echo "secure $k = $(a shell settings get secure $k | tr -d '\r')"
  done
  echo "process limit (-1 = standard): $(a shell service call activity $TX_GET_PROCESS_LIMIT | tr -d '\r')"
}

apply() {
  load_state
  local pkg st out
  while read -r pkg; do
    st=$(state_of "$pkg")
    case $st in
      absent) echo "absent    $pkg" ;;
      disabled) echo "already   $pkg" ;;
      *)
        out=$(a shell pm disable-user --user 0 "$pkg" 2>&1 | tr -d '\r') || true
        if [[ "$out" == *"new state: disabled-user"* ]]; then echo "disabled  $pkg"
        else echo "REFUSED   $pkg: $(grep -m1 -oE '[A-Za-z]*Exception: .*' <<<"$out" || echo "$out")"; fi ;;
    esac
  done < <(packages)
}

revert() {
  local pkg
  while read -r pkg; do
    a shell pm enable "$pkg" 2>&1 | tr -d '\r' || true
  done < <(packages)
}

tweaks() {
  for k in window_animation_scale transition_animation_scale animator_duration_scale; do
    a shell settings put global "$k" 0.5
  done
  a shell settings put secure amz_limit_ad_tracking 1
  a shell settings put secure limit_ad_tracking 1
  a shell settings put secure non_amz_limit_ad_tracking 1
  a shell settings put secure usage_metrics_marketing_enabled 0
  # Not persisted by Android: resets to standard on every reboot.
  a shell service call activity $TX_SET_PROCESS_LIMIT i32 $PROCESS_LIMIT >/dev/null
  echo "process limit now: $(a shell service call activity $TX_GET_PROCESS_LIMIT | tr -d '\r')"
}

untweak() {
  for k in window_animation_scale transition_animation_scale animator_duration_scale; do
    a shell settings put global "$k" 1.0
  done
  a shell settings put secure amz_limit_ad_tracking 0
  a shell settings put secure limit_ad_tracking 0
  a shell settings put secure non_amz_limit_ad_tracking 0
  a shell settings put secure usage_metrics_marketing_enabled 1
  a shell service call activity $TX_SET_PROCESS_LIMIT i32 -1 >/dev/null
}

snapshot() {
  local d; d=inventory/$(date +%F)
  a shell pm list packages -s | tr -d '\r' | sed 's/^package://' | sort >"$d-system-packages.txt"
  a shell pm list packages -3 | tr -d '\r' | sed 's/^package://' | sort >"$d-user-packages.txt"
  a shell pm list packages -d | tr -d '\r' | sed 's/^package://' | sort >"$d-disabled-packages.txt"
  echo "wrote $d-*.txt"
}

case "${1:-status}" in
  connect|status|apply|revert|tweaks|untweak|snapshot) "$1" ;;
  *) echo "usage: $0 connect|status|apply|revert|tweaks|untweak|snapshot" >&2; exit 2 ;;
esac
