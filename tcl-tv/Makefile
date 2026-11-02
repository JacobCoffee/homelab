.DEFAULT_GOAL := help
SHELL := /bin/bash

# TV_HOST and FIRETV_HOST come from ../fire-tv/config/site.env.
SITE := source ../fire-tv/tools/site.sh
# Wireless debugging moves to a new port after every reboot or Wi-Fi drop; find it over mDNS.
TV = "$$(adb mdns services | awk -v ip="$$TV_HOST" '$$0 ~ ip && /tls-connect/ {print $$3; exit}')"
CYT := cleanyourtv
PY := tools/venv/bin/python -I
STOCK_LAUNCHER := com.google.android.apps.tv.launcherx
LISTEN_PORT := 47801
WORD ?=

help: ## Display this help text
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

# =============================================================================
# Setup
# =============================================================================

##@ Setup

setup: ## Fetch the cleanyourtv submodule and create its tools venv
	git submodule update --init $(CYT)
	cd $(CYT) && python3 -m venv tools/venv && tools/venv/bin/pip install -q -r tools/requirements.txt

connect: ## Find the TV's Wireless debugging port and connect adb
	@$(SITE) TV_HOST && t=$(TV) && [ -n "$$t" ] || { echo "TV not advertising Wireless debugging (try: make adb-restart)" >&2; exit 1; }; \
		adb connect "$$t"

adb-restart: ## Restart the adb server (when mDNS finds nothing)
	adb kill-server
	adb start-server

shell: ## Open an adb shell on the TV
	@$(SITE) TV_HOST && adb -s $(TV) shell

# =============================================================================
# Remote bridge
# =============================================================================

##@ Remote bridge

bridge-start: ## Start the key watcher and listener on the TV (after any reboot)
	bridge/bridge.sh start

bridge-stop: ## Stop both bridge processes
	bridge/bridge.sh stop

bridge-status: ## Show the TV processes and whether Hearth's listener answers
	bridge/bridge.sh status

send: ## Send a word to the TV listener, e.g. make send WORD=hdmi2
	@[ -n "$(WORD)" ] || { echo "usage: make send WORD=<tvsettings|tvhome|hdmi1..hdmi4|off|sleep:<min>|sleep:cancel>" >&2; exit 2; }
	@$(SITE) TV_HOST && printf '%s\n' "$(WORD)" | nc -w 2 "$$TV_HOST" $(LISTEN_PORT)

# =============================================================================
# Debloat
# =============================================================================

##@ Debloat

check: ## Report packages a firmware update re-enabled (changes nothing)
	@$(SITE) TV_HOST && cd $(CYT) && TV_ADB_BINARY=adb TV_HOST=$(TV) $(PY) tools/check.py ../debloat.txt

apply: ## Disable every package in debloat.txt
	@$(SITE) TV_HOST && cd $(CYT) && TV_ADB_BINARY=adb TV_HOST=$(TV) tools/disable.sh ../debloat.txt

restore: ## Re-enable everything and give Home back to Google TV
	@$(SITE) TV_HOST && cd $(CYT) && TV_ADB_BINARY=adb TV_HOST=$(TV) tools/restore.sh ../debloat.txt $(STOCK_LAUNCHER)

launcher-config: ## Push launcher-config/ (dock, hidden apps, remote buttons)
	@$(SITE) TV_HOST && cd $(CYT) && TV_ADB_BINARY=adb TV_HOST=$(TV) tools/theme.sh ../launcher-config

# =============================================================================
# Code Quality
# =============================================================================

##@ Code Quality

lint: ## Shellcheck the bridge scripts
	shellcheck -x -e SC1091 bridge/*.sh

.PHONY: help setup connect adb-restart shell
.PHONY: bridge-start bridge-stop bridge-status send
.PHONY: check apply restore launcher-config
.PHONY: lint
