#!/usr/bin/env bash
# Wrapper for running OpenTofu against terraform/cabotage/.
#
# Pulls R2 state-backend credentials and GitHub App secrets from the Ansible vault at run time —
# nothing sensitive is ever written to disk in this repo. Also materializes module.cabotage's
# `secrets_dir` (gitignored) from the vault before every run, since the upstream module reads
# user-provided GitHub App secrets from plain files there.
#
# Usage: ./tf.sh plan | ./tf.sh apply | ./tf.sh <any tofu subcommand/args>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOMELAB_ROOT="$(cd "$ROOT/../.." && pwd)"
VAULT_FILE="$HOMELAB_ROOT/ansible/inventory/group_vars/all/vault.yml"
# The module reads ${secrets_dir}/<workspace>/ (certificate-authority.tf `local.secrets_dir`), so
# the GitHub App files must land there, not in .secrets/ itself.
SECRETS_DIR="$ROOT/.secrets/${TF_WORKSPACE:-default}"

cd "$ROOT"

vault_get() {
  (cd "$HOMELAB_ROOT/ansible" && ansible-vault view "$VAULT_FILE") | yq -r ".$1 // \"\""
}

mkdir -p "$SECRETS_DIR"
chmod 700 "$ROOT/.secrets" "$SECRETS_DIR"

WEBHOOK_SECRET="$(vault_get vault_cabotage_github_webhook_secret)"
if [ -n "$WEBHOOK_SECRET" ]; then
  printf '%s' "$WEBHOOK_SECRET" > "$SECRETS_DIR/github-webhook-secret"
  chmod 600 "$SECRETS_DIR/github-webhook-secret"
fi

GITHUB_APP_ID="$(vault_get vault_cabotage_github_app_id)"
GITHUB_CLIENT_ID="$(vault_get vault_cabotage_github_app_client_id)"
GITHUB_CLIENT_SECRET="$(vault_get vault_cabotage_github_app_client_secret)"
GITHUB_PRIVATE_KEY="$(vault_get vault_cabotage_github_app_private_key)"

if [ -n "$GITHUB_CLIENT_ID" ]; then
  printf '%s' "$GITHUB_CLIENT_ID" > "$SECRETS_DIR/github-app-client-id"
  chmod 600 "$SECRETS_DIR/github-app-client-id"
fi
if [ -n "$GITHUB_CLIENT_SECRET" ]; then
  printf '%s' "$GITHUB_CLIENT_SECRET" > "$SECRETS_DIR/github-app-client-secret"
  chmod 600 "$SECRETS_DIR/github-app-client-secret"
fi
if [ -n "$GITHUB_PRIVATE_KEY" ]; then
  printf '%s' "$GITHUB_PRIVATE_KEY" > "$SECRETS_DIR/github-app-private-key.pem"
  chmod 600 "$SECRETS_DIR/github-app-private-key.pem"
fi

export AWS_ACCESS_KEY_ID
export AWS_SECRET_ACCESS_KEY
AWS_ACCESS_KEY_ID="$(vault_get vault_r2_access_key_id)"
AWS_SECRET_ACCESS_KEY="$(vault_get vault_r2_secret_access_key)"

export TF_VAR_github_app_id="$GITHUB_APP_ID"
export KUBECONFIG="$HOMELAB_ROOT/kubernetes/kubeconfig"

# The upstream module has no toggle to label the namespaces it creates (cabotage,
# cabotage-tenant-builds). Consul (cert-watcher: SYS_PTRACE), Alloy (DAC_READ_SEARCH +
# hostPath) and cert-manager-csi-driver (privileged + hostPath, cert-manager namespace,
# labeled separately in kubernetes/apps/cert-manager/namespace.yaml) all need more than the
# cluster's baseline PodSecurity default. On `apply`, create just the namespaces first (if not
# already present) and label them before the rest of the graph races to schedule pods into them.
if [ "${1:-}" = "apply" ]; then
  mise exec -- tofu apply -auto-approve \
    -target=kubernetes_namespace_v1.traefik \
    -target=module.cabotage.kubernetes_namespace_v1.cabotage \
    -target=module.cabotage.kubernetes_namespace_v1.cabotage_tenant_builds || true
  mise exec -- kubectl --context admin@homelab label namespace cabotage \
    pod-security.kubernetes.io/enforce=privileged --overwrite || true
  mise exec -- kubectl --context admin@homelab label namespace cabotage-tenant-builds \
    pod-security.kubernetes.io/enforce=baseline --overwrite || true
fi

exec mise exec -- tofu "$@"
