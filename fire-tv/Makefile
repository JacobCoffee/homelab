.DEFAULT_GOAL := help
SHELL := /bin/bash

# Device addresses come from config/site.env (see config/site.env.example).
SITE := source tools/site.sh
STICK = "$$FIRETV_HOST:$${FIRETV_ADB_PORT:-5555}"
BRIDGE_PORT := 47800
THEME ?=
WORD ?=

help: ## Display this help text
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

# =============================================================================
# Setup
# =============================================================================

##@ Setup

setup: ## Create config/site.env from the template (then edit it)
	@if [ -f config/site.env ]; then echo "config/site.env already exists"; \
	else cp config/site.env.example config/site.env && echo "created config/site.env: fill it in"; fi

keystore: ## Create the release signing keystore in ~/.hearth-keys if missing
	hearth/tools/make-keystore.sh

# =============================================================================
# Fire TV (debloat)
# =============================================================================

##@ Fire TV

connect: ## Connect adb to the stick
	./firetv.sh connect

status: ## Show which packages are disabled and which tweaks are on
	./firetv.sh status

apply: ## Disable every package in packages.txt
	./firetv.sh apply

revert: ## Re-enable every package in packages.txt
	./firetv.sh revert

tweaks: ## Apply animation, ad-tracking and process-limit tweaks
	./firetv.sh tweaks

untweak: ## Restore stock values for the tweaks
	./firetv.sh untweak

snapshot: ## Save a package and getprop inventory to inventory/
	./firetv.sh snapshot

after-reboot: ## Re-apply everything after the stick reboots
	./firetv.sh connect
	./firetv.sh apply
	./firetv.sh tweaks
	./firetv.sh status

shell: ## Open an adb shell on the stick
	@$(SITE) FIRETV_HOST && adb -s $(STICK) shell

# =============================================================================
# Hearth
# =============================================================================

##@ Hearth

build: ## Build the signed release APK
	cd hearth && ./gradlew assembleRelease

test: ## Run Hearth's unit tests
	cd hearth && ./gradlew testReleaseUnitTest

install: ## Build, install and make Hearth the Home redirect
	hearth/tools/install.sh

config: ## Render config/hearth.local.json from config/site.env
	tools/make-config.py

push-config: config ## Render the config and push it to the stick
	hearth/tools/push-config.sh config/hearth.local.json

open: ## Bring Hearth to the front (bridge word "hearth")
	@$(SITE) FIRETV_HOST && printf 'hearth\n' | nc -w 2 "$$FIRETV_HOST" $(BRIDGE_PORT)

remote: ## Send a bridge word, e.g. make remote WORD=netflix
	@[ -n "$(WORD)" ] || { echo "usage: make remote WORD=<jellyfin|netflix|prime|youtube|hbo|disney|hearth|settings>" >&2; exit 2; }
	@$(SITE) FIRETV_HOST && printf '%s\n' "$(WORD)" | nc -w 2 "$$FIRETV_HOST" $(BRIDGE_PORT)

logs: ## Follow Hearth's log lines
	@$(SITE) FIRETV_HOST && adb -s $(STICK) logcat -v time | grep --line-buffered -i hearth

screenshot: ## Save the stick's screen to hearth/screenshots/
	@mkdir -p hearth/screenshots
	@$(SITE) FIRETV_HOST && f="hearth/screenshots/$$(date +%Y%m%d-%H%M%S).png" && \
		adb -s $(STICK) exec-out screencap -p > "$$f" && echo "$$f"

clean: ## Remove Gradle build output and the built catalog
	cd hearth && ./gradlew -q clean
	rm -rf dist

# =============================================================================
# Themes
# =============================================================================

##@ Themes

themes: ## Repaint generated theme art (THEME=christmas for one)
	uv run tools/paint-themes.py $(THEME)

catalog: ## Validate every theme and build dist/catalog
	uv run tools/build-catalog.py

# =============================================================================
# Test stack
# =============================================================================

##@ Test stack

stack-up: catalog ## Reset and start AdGuard, Uptime Kuma and the catalog; writes the config
	teststack/up.sh

stack-down: ## Stop the test stack and delete its volumes
	teststack/down.sh

stack-ps: ## Show the test stack's containers
	@$(SITE) TESTSTACK_HOST && export TESTSTACK_HOST && docker compose -f teststack/compose.yml ps

# =============================================================================
# Code Quality
# =============================================================================

##@ Code Quality

lint: ## Shellcheck every script
	shellcheck -x -e SC1091 firetv.sh tools/site.sh hearth/tools/*.sh teststack/*.sh

ci: lint catalog test ## Run lint, theme validation, then unit tests

.PHONY: help setup keystore
.PHONY: connect status apply revert tweaks untweak snapshot after-reboot shell
.PHONY: build test install config push-config open remote logs screenshot clean
.PHONY: themes catalog
.PHONY: stack-up stack-down stack-ps
.PHONY: lint ci
