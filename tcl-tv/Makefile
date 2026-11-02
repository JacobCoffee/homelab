.DEFAULT_GOAL := help
SHELL := /bin/bash

# TV_HOST and FIRETV_HOST come from ../fire-tv/config/site.env.
SITE := source ../fire-tv/tools/site.sh
# Wireless debugging moves to a new port after every reboot or Wi-Fi drop. Use the adb connection already
# open to TV_HOST, else find the port over mDNS (which can list nothing while a connection is up).
# TV_HOST may also be given as ip:port; only the ip is matched.
TV = "$$( { adb devices | awk -v p="$${TV_HOST%%:*}:" 'index($$1, p) == 1 && $$2 == "device" {print $$1}'; \
            adb mdns services | awk -v p="$${TV_HOST%%:*}:" '/tls-connect/ && index($$3, p) == 1 {print $$3}'; } | head -1)"
# Run first in a recipe line: loads site.env, finds the TV, connects, and leaves its ip:port in $$t.
TVSH = $(SITE) TV_HOST && t=$(TV) && { [ -n "$$t" ] || { echo "TV not connected or advertising Wireless debugging (try: make adb-restart)" >&2; exit 1; }; } && adb connect "$$t" >/dev/null
CYT := cleanyourtv
PY := tools/venv/bin/python -I
STOCK_LAUNCHER := com.google.android.apps.tv.launcherx
LISTEN_PORT := 47801
PREVIEW_PORT ?= 8765
WORD ?=
CMD ?=
ADB_PORT ?=
NAME ?= $(shell date +%Y%m%d-%H%M%S)

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
	@$(TVSH) && echo "=> connected $$t"

adb-restart: ## Restart the adb server (when mDNS finds nothing)
	adb kill-server
	adb start-server

shell: ## Open an adb shell on the TV
	@$(TVSH) && adb -s "$$t" shell

run: ## Run one shell command on the TV, e.g. make run CMD='dumpsys tv_input'
	@[ -n "$(CMD)" ] || { echo "usage: make run CMD='<shell command>'" >&2; exit 2; }
	@$(TVSH) && adb -s "$$t" shell '$(CMD)'

probe: ## Print the TV's model and Android version
	@$(TVSH) && cd $(CYT) && TV_ADB_BINARY=adb TV_HOST="$$t" tools/tv.py --probe

adb-closed: ## Check network ADB is closed after turning debugging off (ADB_PORT=<port>, default 5555)
	@$(SITE) TV_HOST && cd $(CYT) && $(PY) tools/adb-closed.py "$${TV_HOST%%:*}" $(ADB_PORT)

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
	@$(SITE) TV_HOST && printf '%s\n' "$(WORD)" | nc -w 2 "$${TV_HOST%%:*}" $(LISTEN_PORT)

# =============================================================================
# Debloat
# =============================================================================

##@ Debloat

check: ## Report packages a firmware update re-enabled (changes nothing)
	@$(TVSH) && cd $(CYT) && TV_ADB_BINARY=adb TV_HOST="$$t" $(PY) tools/check.py ../debloat.txt

apply: ## Disable every package in debloat.txt
	@$(TVSH) && cd $(CYT) && TV_ADB_BINARY=adb TV_HOST="$$t" tools/disable.sh ../debloat.txt

restore: ## Re-enable everything and give Home back to Google TV
	@$(TVSH) && cd $(CYT) && TV_ADB_BINARY=adb TV_HOST="$$t" tools/restore.sh ../debloat.txt $(STOCK_LAUNCHER)

# =============================================================================
# Launcher & theme
# =============================================================================

##@ Launcher & theme

launcher-config: ## Push launcher-config/ (Local Dimming look, dock, remote buttons) and restart Home
	@$(TVSH) && cd $(CYT) && TV_ADB_BINARY=adb TV_HOST="$$t" tools/theme.sh ../launcher-config

theme-reset: ## Drop the pushed theme: launcher back to its built-in config.xml
	@$(TVSH) && cd $(CYT) && TV_ADB_BINARY=adb TV_HOST="$$t" tools/theme.sh --reset

launcher: ## Reinstall the built launcher APK with the remote-key helper
	@$(TVSH) && cd $(CYT) && TV_ADB_BINARY=adb TV_HOST="$$t" INPUT_KEY_SERVICE=1 RESTORE_KEY_SERVICES=1 SKIP_BUILD=1 launcher/build-and-install.sh

wallpapers: ## Redraw launcher-config/wallpapers/ from make_wallpapers.py
	uv run --script launcher-config/make_wallpapers.py

preview: ## Preview the theme in a browser (Ctrl-C to stop)
	@echo "=> http://127.0.0.1:$(PREVIEW_PORT)/launcher-config/preview/"
	@(sleep 1 && open "http://127.0.0.1:$(PREVIEW_PORT)/launcher-config/preview/") &
	@python3 -m http.server $(PREVIEW_PORT) --bind 127.0.0.1

# =============================================================================
# Diagnostics
# =============================================================================

##@ Diagnostics

status: ## Home app, free memory, Private DNS and disabled-package count
	@$(TVSH) && s() { adb -s "$$t" shell "$$@"; } && \
		echo "Home:        $$(s cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME | tail -1)" && \
		echo "Memory:      $$(s grep MemAvailable /proc/meminfo | tr -s ' ')" && \
		echo "Private DNS: $$(s settings get global private_dns_mode) / $$(s settings get global private_dns_specifier)" && \
		echo "Disabled:    $$(s pm list packages -d | wc -l | tr -d ' ') packages"

screenshot: ## Save the screen to shots/NAME.png (NAME defaults to a timestamp)
	@$(TVSH) && cd $(CYT) && TV_ADB_BINARY=adb TV_HOST="$$t" tools/tv.py --screenshot ../shots/$(NAME).png && echo "=> shots/$(NAME).png"

keycodes: ## Print the keycode of each remote button you press (Ctrl-C to stop)
	@$(TVSH) && adb -s "$$t" logcat -c && adb -s "$$t" logcat -v time | grep --line-buffered 'TIDD: keyCode'

soundbar: ## Recent CEC volume traffic with the soundbar
	@$(TVSH) && adb -s "$$t" shell dumpsys hdmi_control | grep -E 'User Control|Audio Status' | tail

# =============================================================================
# Code Quality
# =============================================================================

##@ Code Quality

lint: ## Shellcheck the bridge scripts
	shellcheck -x -e SC1091 bridge/*.sh

.PHONY: help setup connect adb-restart shell run probe adb-closed
.PHONY: bridge-start bridge-stop bridge-status send
.PHONY: check apply restore
.PHONY: launcher-config theme-reset launcher wallpapers preview
.PHONY: status screenshot keycodes soundbar
.PHONY: lint
