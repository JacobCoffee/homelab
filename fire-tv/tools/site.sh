# Source me. Loads config/site.env (or $SITE_ENV) into the environment without overriding
# variables that are already set to a non-empty value, then checks the ones the caller names:
#   source "$(dirname "$0")/tools/site.sh" FIRETV_HOST FIRETV_ADB_PORT
# shellcheck shell=bash

_site_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE_ENV="${SITE_ENV:-$_site_root/config/site.env}"

if [[ -f "$SITE_ENV" ]]; then
  while IFS= read -r _line || [[ -n "$_line" ]]; do
    [[ "$_line" =~ ^[[:space:]]*([A-Z_][A-Z0-9_]*)=(.*)$ ]] || continue
    _key="${BASH_REMATCH[1]}"
    _val="${BASH_REMATCH[2]}"
    _val="${_val%\"}"; _val="${_val#\"}"; _val="${_val%\'}"; _val="${_val#\'}"
    [[ -n "${!_key:-}" ]] || export "$_key=$_val"
  done <"$SITE_ENV"
fi

for _key in "$@"; do
  if [[ -z "${!_key:-}" ]]; then
    echo "error: $_key is not set. Copy config/site.env.example to config/site.env and fill it in." >&2
    exit 1
  fi
done
unset _site_root _line _key _val
