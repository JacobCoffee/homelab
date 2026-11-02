#!/usr/bin/env bash
# Builds the release APK, installs it, grants READ_LOGS, and makes Hearth's
# HomeRedirectService the only enabled accessibility service (switching Homestead's off
# without uninstalling it).
#
# Usage: tools/install.sh [adb-serial]   (default: FIRETV_HOST:FIRETV_ADB_PORT from config/site.env)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$HERE/tools/site.sh"
SERIAL="${1:-${FIRETV_HOST:?set FIRETV_HOST in config/site.env}:${FIRETV_ADB_PORT:-5555}}"
LOCK=/tmp/firetv-device.lock
APP_ID="dev.jacobcoffee.hearth"
SERVICE="$APP_ID/dev.jacobcoffee.hearth.homekey.HomeRedirectService"

"$HERE/tools/make-keystore.sh"

echo "building release APK..."
(cd "$HERE" && ./gradlew assembleRelease)

APK="$(find "$HERE/app/build/outputs/apk/release" -name '*.apk' | head -1)"
if [[ -z "$APK" ]]; then
    echo "error: no release APK found under app/build/outputs/apk/release" >&2
    exit 1
fi
echo "APK: $APK ($(du -h "$APK" | cut -f1))"

while [[ -e "$LOCK" ]]; do
    echo "waiting for device lock ($LOCK) held by $(cat "$LOCK" 2>/dev/null || echo '?')..." >&2
    sleep 1
done
echo "hearth/install.sh" > "$LOCK"
cleanup() { rm -f "$LOCK"; }
trap cleanup EXIT

ADB="adb -s $SERIAL"

echo "installing..."
$ADB install -r -g "$APK"

echo "granting READ_LOGS..."
$ADB shell pm grant "$APP_ID" android.permission.READ_LOGS

# A sideloaded APK gets only its small baseline profile compiled; ahead-of-time compiling all
# of it cut cold start from ~1.2 s to ~0.8 s on the stick (Compose runs interpreted otherwise).
echo "compiling ahead of time..."
$ADB shell cmd package compile -m speed -f "$APP_ID"

echo "enabling Hearth's accessibility service (and disabling any other)..."
$ADB shell settings put secure enabled_accessibility_services "$SERVICE"
$ADB shell settings put secure accessibility_enabled 1

echo "allowing Hearth to see what's playing (notification listener; reads no notifications)..."
$ADB shell cmd notification allow_listener "$APP_ID/dev.jacobcoffee.hearth.media.MediaListener"

echo "done. Hearth is installed and is now the Home redirect."
