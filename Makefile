.DEFAULT_GOAL := help
SHELL := /bin/bash

# Each project has its own Makefile; run its targets from here as <project>/<target>.
PROJECTS := fire-tv tcl-tv proxmox-pxe ansible

help: ## Display this help text
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n  make \033[36m<project>/<target>\033[0m   e.g. make fire-tv/status\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)
	@printf "\n\033[1mProjects\033[0m  (make \033[36m<project>/help\033[0m lists its targets)\n"
	@printf "  \033[36m%-15s\033[0m %s\n" \
		fire-tv "Fire TV debloat, Hearth launcher, themes, test stack" \
		tcl-tv "TCL TV debloat, launcher config, remote bridge" \
		proxmox-pxe "Proxmox VE network installs from this Mac" \
		ansible "Node baseline: Tailscale, VLANs, OPNsense"

# =============================================================================
# Repo
# =============================================================================

##@ Repo

hooks: ## Enable the pre-commit guard (gitleaks + vault encryption check)
	git config core.hooksPath .githooks
	@echo "pre-commit guard on"

scan: ## Scan every commit for secrets (what's public)
	mise exec -- gitleaks git --redact --no-banner .

help-all: ## List every project's targets
	@for p in $(PROJECTS); do printf "\n\033[1;35m== %s ==\033[0m" "$$p"; $(MAKE) --no-print-directory -C $$p help; done

lint: ## Shellcheck every project
	@for p in fire-tv tcl-tv proxmox-pxe; do $(MAKE) --no-print-directory -C $$p lint || exit 1; done

# <project>/<target> runs that target in the project's directory. FORCE keeps targets that
# share a name with a real path (fire-tv/themes) from being skipped as up to date.
define delegate
$(1)/%: FORCE
	@$$(MAKE) --no-print-directory -C $(1) $$*
endef
$(foreach p,$(PROJECTS),$(eval $(call delegate,$(p))))

FORCE:

.PHONY: help hooks scan help-all lint FORCE
