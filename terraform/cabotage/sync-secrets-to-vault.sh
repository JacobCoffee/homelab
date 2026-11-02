#!/usr/bin/env bash
# Run once after `./tf.sh apply` completes the Consul/Vault bootstrap (module.cabotage's bootstrap
# scripts write these into .secrets/<workspace>/, which is gitignored and lives only on this
# machine — the module namespaces secrets_dir by `terraform.workspace`, "default" unless a
# workspace was created). Backs the crown-jewel tokens up into the Ansible vault so they survive a
# lost laptop. Never prints secret values.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOMELAB_ROOT="$(cd "$ROOT/../.." && pwd)"
VAULT_FILE="$HOMELAB_ROOT/ansible/inventory/group_vars/all/vault.yml"
WORKSPACE="$(cd "$ROOT" && mise exec -- tofu workspace show 2>/dev/null || echo default)"
SECRETS_DIR="$ROOT/.secrets/$WORKSPACE"

TMP="$(mktemp)"
trap 'shred -u "$TMP" 2>/dev/null || rm -f "$TMP"' EXIT

(cd "$HOMELAB_ROOT/ansible" && ansible-vault view "$VAULT_FILE") > "$TMP"

CHANGED=0
sync_one() {
  file="$1"
  key="$2"
  path="$SECRETS_DIR/$file"
  [ -f "$path" ] || return 0
  if grep -q "^${key}:" "$TMP"; then
    return 0 # already stored, do not overwrite silently
  fi
  val="$(cat "$path")"
  printf '%s: "%s"\n' "$key" "$val" >> "$TMP"
  CHANGED=1
  echo "stored $key (from $file)"
}

sync_one "consul-bootstrap-token" "vault_cabotage_consul_management_token"
sync_one "vault-bootstrap-token" "vault_cabotage_vault_root_token"
sync_one "vault-unseal-key" "vault_cabotage_vault_unseal_key"
sync_one "vault-recovery-key" "vault_cabotage_vault_recovery_key"

if [ "$CHANGED" -eq 1 ]; then
  (cd "$HOMELAB_ROOT/ansible" && ansible-vault encrypt --output "$VAULT_FILE.new" "$TMP")
  chmod 600 "$VAULT_FILE.new"
  mv "$VAULT_FILE.new" "$VAULT_FILE"
  chmod 600 "$VAULT_FILE"
  echo "vault.yml updated."
else
  echo "Nothing new to store."
fi
