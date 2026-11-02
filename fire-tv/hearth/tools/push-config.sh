#!/usr/bin/env bash
# Pushes a Hearth config JSON file to the device and writes it through ConfigProvider.
#
# Usage: tools/push-config.sh <path/to/hearth.json> [adb-serial]
#   (default serial: FIRETV_HOST:FIRETV_ADB_PORT from config/site.env)
set -euo pipefail

CONFIG_FILE="${1:?usage: push-config.sh <config.json> [adb-serial]}"
source "$(dirname "${BASH_SOURCE[0]}")/site.sh"
SERIAL="${2:-${FIRETV_HOST:?set FIRETV_HOST in config/site.env}:${FIRETV_ADB_PORT:-5555}}"
LOCK=/tmp/firetv-device.lock

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "error: $CONFIG_FILE does not exist" >&2
    exit 1
fi

while [[ -e "$LOCK" ]]; do
    echo "waiting for device lock ($LOCK) held by $(cat "$LOCK" 2>/dev/null || echo '?')..." >&2
    sleep 1
done
echo "hearth/push-config.sh" > "$LOCK"
cleanup() { rm -f "$LOCK"; }
trap cleanup EXIT

ADB="adb -s $SERIAL"
REMOTE=/data/local/tmp/hearth.json

$ADB push "$CONFIG_FILE" "$REMOTE"
$ADB shell "content write --uri content://dev.jacobcoffee.hearth.config/config < $REMOTE"
$ADB shell "rm -f $REMOTE"

echo "config pushed to dev.jacobcoffee.hearth"
