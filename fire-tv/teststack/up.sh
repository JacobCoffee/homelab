#!/usr/bin/env bash
# Bring up the Hearth local test stack (AdGuard Home, Uptime Kuma, catalog
# static server) and seed it: AdGuard admin user, Kuma admin + monitors +
# public status page "home". Writes config/hearth.local.json with tools/make-config.py.
# TESTSTACK_HOST (config/site.env) is the address the stack binds to and the stick reaches.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_DIR="${FT_ROOT}/config"
source "${FT_ROOT}/tools/site.sh" && site_require TESTSTACK_HOST
export TESTSTACK_HOST

HOST="${TESTSTACK_HOST}"
ADGUARD_URL="http://${HOST}:3000"
KUMA_URL="http://${HOST}:3001"
CATALOG_URL="http://${HOST}:8088"

ADGUARD_USER="admin"
ADGUARD_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
KUMA_USER="admin"
KUMA_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"

echo "==> Resetting stack (docker compose down -v)"
docker compose -f "${SCRIPT_DIR}/compose.yml" down -v --remove-orphans >/dev/null 2>&1 || true

echo "==> Starting stack"
docker compose -f "${SCRIPT_DIR}/compose.yml" up -d

wait_for_http() {
  local url="$1" name="$2" tries=60
  echo -n "==> Waiting for ${name} (${url})"
  until curl -fsS -o /dev/null "${url}" 2>/dev/null; do
    tries=$((tries - 1))
    if [ "${tries}" -le 0 ]; then
      echo " TIMEOUT"
      return 1
    fi
    echo -n "."
    sleep 2
  done
  echo " up"
}

wait_for_http "${ADGUARD_URL}/control/status" "AdGuard Home" || {
  # AdGuard returns 403/redirect until configured; check root instead
  wait_for_http "${ADGUARD_URL}/" "AdGuard Home (root)"
}

echo "==> Configuring AdGuard Home admin user"
curl -fsS -X POST "${ADGUARD_URL}/control/install/configure" \
  -H "Content-Type: application/json" \
  -d "{\"web\":{\"ip\":\"0.0.0.0\",\"port\":3000},\"dns\":{\"ip\":\"0.0.0.0\",\"port\":53},\"username\":\"${ADGUARD_USER}\",\"password\":\"${ADGUARD_PASS}\"}" \
  >/dev/null
echo "    AdGuard admin user '${ADGUARD_USER}' configured"

wait_for_http "${KUMA_URL}/" "Uptime Kuma"
wait_for_http "${CATALOG_URL}/index.json" "catalog server"

echo "==> Seeding Uptime Kuma (admin + monitors + status page)"
uv run "${SCRIPT_DIR}/seed-kuma.py" "${KUMA_URL}" "${KUMA_USER}" "${KUMA_PASS}" "${ADGUARD_URL}"

echo "==> Writing ${CONFIG_DIR}/hearth.local.json"
KUMA_URL="${KUMA_URL}" ADGUARD_URL="${ADGUARD_URL}" ADGUARD_USER="${ADGUARD_USER}" ADGUARD_PASSWORD="${ADGUARD_PASS}" \
  THEME_CATALOG_URL="${CATALOG_URL}/index.json" python3 "${FT_ROOT}/tools/make-config.py"

echo ""
echo "==> Stack ready:"
echo "    AdGuard:  ${ADGUARD_URL}  (admin / ${ADGUARD_PASS})"
echo "    Kuma:     ${KUMA_URL}  (admin / ${KUMA_PASS})"
echo "    Catalog:  ${CATALOG_URL}/index.json"
echo "    Config written to ${CONFIG_DIR}/hearth.local.json"
