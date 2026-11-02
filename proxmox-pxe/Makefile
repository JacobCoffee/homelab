.DEFAULT_GOAL := help
SHELL := /bin/bash

DASHBOARD := http://localhost:8070/
HOST ?=

help: ## Display this help text
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

# =============================================================================
# Install
# =============================================================================

##@ Install

build: ## Build out/ (installer, iPXE, secrets); re-run when the Mac's IP or config.env changes
	./build.sh

run: ## Serve PXE installs (asks for sudo; Ctrl-C stops)
	./run.sh

dashboard: ## Open the install dashboard
	open $(DASHBOARD)

# =============================================================================
# Day-2
# =============================================================================

##@ Day-2

hosts: ## List nodes with status, IP and MAC
	python3 server/core.py list

forget: ## Reinstall a node on its next PXE boot, e.g. make forget HOST=hnhv03 (wipes its USB stick)
	@[ -n "$(HOST)" ] || { echo "usage: make forget HOST=<hostname>" >&2; exit 2; }
	python3 server/core.py forget $(HOST)

password: ## Copy the nodes' root password to the clipboard
	@pbcopy < secrets/root-password && echo "root password copied"

# =============================================================================
# Code Quality
# =============================================================================

##@ Code Quality

lint: ## Shellcheck the build and run scripts
	shellcheck -x -e SC1091 build.sh run.sh

.PHONY: help build run dashboard hosts forget password lint
