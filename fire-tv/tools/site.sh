# Source me. Loads config/site.env (or $SITE_ENV) into the environment without overriding
# variables that are already set to a non-empty value; site_require then checks names:
#   source "$(dirname "$0")/tools/site.sh" && site_require FIRETV_HOST FIRETV_ADB_PORT
# The check is a function, not source arguments: a bare `source` passes on the caller's
# own arguments, which would be checked as variable names.
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

unset _site_root _line _key _val

site_require() {
  local key
  for key in "$@"; do
    if [[ -z "${!key:-}" ]]; then
      echo "error: $key is not set. Copy config/site.env.example to config/site.env and fill it in." >&2
      exit 1
    fi
  done
}
