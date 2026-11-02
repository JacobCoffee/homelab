.DEFAULT_GOAL := help
SHELL := /bin/bash

VAULT := inventory/group_vars/all/vault.yml
# Narrow a run: make site TAGS=opnsense LIMIT=hnhv01
TAGS ?=
LIMIT ?=
RUN_ARGS = $(if $(TAGS),--tags $(TAGS)) $(if $(LIMIT),--limit $(LIMIT))

help: ## Display this help text
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

# =============================================================================
# Plays
# =============================================================================

##@ Plays

site: ## Apply the baseline: tailscale, network, OPNsense (TAGS=, LIMIT= narrow it)
	ansible-playbook playbooks/site.yml $(RUN_ARGS)

discover-nics: ## List each node's network ports (to set pve_trunk_nic)
	ansible-playbook playbooks/discover-nics.yml $(RUN_ARGS)

# =============================================================================
# Inventory
# =============================================================================

##@ Inventory

hosts: ## Show the inventory graph (nodes come from ../proxmox-pxe)
	ansible-inventory --graph

ping: ## Check SSH to every node
	ansible all -m ping $(if $(LIMIT),--limit $(LIMIT))

# =============================================================================
# Vault
# =============================================================================

##@ Vault

vault-edit: ## Edit the encrypted vault
	ansible-vault edit $(VAULT)

vault-view: ## Print the decrypted vault
	ansible-vault view $(VAULT)

.PHONY: help site discover-nics hosts ping vault-edit vault-view
