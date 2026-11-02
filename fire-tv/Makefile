.DEFAULT_GOAL := help
SHELL := /bin/bash

# Device addresses come from hearth/config/site.env (see hearth/config/site.env.example).
SITE := source hearth/tools/site.sh && site_require
STICK = "$$FIRETV_HOST:$${FIRETV_ADB_PORT:-5555}"

# Hearth (hearth/, mirrored to github.com/JacobCoffee/hearth) has its own Makefile; these
# targets run there, so `make install` works from here or from hearth/.
HEARTH_TARGETS := setup keystore build test install config push-config open remote logs \
	screenshot clean themes catalog stack-up stack-down stack-ps

# Lists Hearth's targets first (minus its help and Code Quality, which this file covers).
help: ## Display this help text
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} FILENAME == "hearth/Makefile" && /^(help|lint|ci):|^##@ Code Quality/ { next } /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' hearth/Makefile Makefile

$(HEARTH_TARGETS):
	@$(MAKE) --no-print-directory -C hearth $@

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
# Code Quality
# =============================================================================

##@ Code Quality

lint: ## Shellcheck firetv.sh and Hearth's scripts
	shellcheck -x -e SC1091 firetv.sh
	@$(MAKE) --no-print-directory -C hearth lint

ci: lint ## Run lint, then Hearth's theme validation and unit tests
	@$(MAKE) --no-print-directory -C hearth catalog test

.PHONY: help $(HEARTH_TARGETS)
.PHONY: connect status apply revert tweaks untweak snapshot after-reboot shell
.PHONY: lint ci
