#!/usr/bin/env bash
# Run once after `./tf.sh apply` completes the Consul/Vault bootstrap (module.cabotage's bootstrap
# scripts write these into .secrets/, which is gitignored and lives only on this machine).
# Backs the crown-jewel tokens up into the Ansible vault so they survive a lost laptop. Never
# prints secret values.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOMELAB_ROOT="$(cd "$ROOT/../.." && pwd)"
VAULT_FILE="$HOMELAB_ROOT/ansible/inventory/group_vars/all/vault.yml"
SECRETS_DIR="$ROOT/.secrets"

declare -A FILES=(
  [consul-management-token]=vault_cabotage_consul_management_token
  [vault-bootstrap-token]=vault_cabotage_vault_root_token
  [vault-unseal-key]=vault_cabotage_vault_unseal_key
  [vault-recovery-key]=vault_cabotage_vault_recovery_key
)

TMP="$(mktemp)"
trap 'shred -u "$TMP" 2>/dev/null || rm -f "$TMP"' EXIT

ansible-vault view "$VAULT_FILE" > "$TMP"

CHANGED=0
for f in "${!FILES[@]}"; do
  key="${FILES[$f]}"
  path="$SECRETS_DIR/$f"
  [ -f "$path" ] || continue
  if grep -q "^${key}:" "$TMP"; then
    continue # already stored, do not overwrite silently
  fi
  val="$(cat "$path")"
  printf '%s: "%s"\n' "$key" "$val" >> "$TMP"
  CHANGED=1
  echo "stored $key (from $f)"
done

if [ "$CHANGED" -eq 1 ]; then
  ansible-vault encrypt --output "$VAULT_FILE.new" "$TMP"
  chmod 600 "$VAULT_FILE.new"
  mv "$VAULT_FILE.new" "$VAULT_FILE"
  chmod 600 "$VAULT_FILE"
  echo "vault.yml updated."
else
  echo "Nothing new to store."
fi
