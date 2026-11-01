#!/usr/bin/env bash
# Ansible Vault password from the macOS Keychain (never on disk).
# Create once:  security add-generic-password -a homelab -s ansible-vault -w "$(openssl rand -base64 32)"
exec security find-generic-password -a homelab -s ansible-vault -w
