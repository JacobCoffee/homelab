#!/usr/bin/env bash
# Tear down the Hearth local test stack and its volumes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker compose -f "${SCRIPT_DIR}/compose.yml" down -v --remove-orphans
