#!/usr/bin/env bash
# Tear down the Hearth local test stack and its volumes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# compose.yml needs TESTSTACK_HOST even to tear down.
source "${SCRIPT_DIR}/../tools/site.sh" && site_require TESTSTACK_HOST
export TESTSTACK_HOST

docker compose -f "${SCRIPT_DIR}/compose.yml" down -v --remove-orphans
