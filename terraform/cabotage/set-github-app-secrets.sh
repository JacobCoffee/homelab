#!/usr/bin/env bash
# Prompts for the 4 GitHub App values and writes them into the Ansible vault directly --
# avoids `ansible-vault edit` (which opens $EDITOR and can silently fail to save). Run from
# anywhere with `bash terraform/cabotage/set-github-app-secrets.sh` (works fine from fish).
# Never echoes the values back; reads client secret/private key as hidden input.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOMELAB_ROOT="$(cd "$ROOT/../.." && pwd)"
VAULT_FILE="$HOMELAB_ROOT/ansible/inventory/group_vars/all/vault.yml"

echo "GitHub App ID (visible, not secret):"
read -r APP_ID
echo "OAuth Client ID (visible, not secret):"
read -r CLIENT_ID
echo "OAuth Client secret (hidden):"
read -rs CLIENT_SECRET
echo
echo "Private key .pem -- paste the full contents, then press Ctrl-D on its own line:"
PRIVATE_KEY="$(cat)"

TMP="$(mktemp)"
trap 'shred -u "$TMP" 2>/dev/null || rm -f "$TMP"' EXIT

(cd "$HOMELAB_ROOT/ansible" && ansible-vault view "$VAULT_FILE") > "$TMP"

python3 - "$TMP" "$APP_ID" "$CLIENT_ID" "$CLIENT_SECRET" "$PRIVATE_KEY" <<'PYEOF'
import sys, re
path, app_id, client_id, client_secret, private_key = sys.argv[1:6]
lines = open(path).read().splitlines()
keys = {
    "vault_cabotage_github_app_id": app_id,
    "vault_cabotage_github_app_client_id": client_id,
    "vault_cabotage_github_app_client_secret": client_secret,
}
out, seen, i = [], set(), 0
block_key = "vault_cabotage_github_app_private_key"
while i < len(lines):
    line = lines[i]
    matched = False
    for k, v in keys.items():
        if line.startswith(k + ":"):
            out.append(f'{k}: "{v}"')
            seen.add(k)
            matched = True
            break
    if matched:
        i += 1
        continue
    if line.startswith(block_key + ":"):
        out.append(f"{block_key}: |")
        for pk_line in private_key.splitlines():
            out.append("  " + pk_line)
        seen.add(block_key)
        i += 1
        # skip the old block scalar's indented continuation lines
        while i < len(lines) and (lines[i].startswith("  ") or lines[i].strip() == ""):
            i += 1
        continue
    out.append(line)
    i += 1
for k, v in keys.items():
    if k not in seen:
        out.append(f'{k}: "{v}"')
if block_key not in seen:
    out.append(f"{block_key}: |")
    for pk_line in private_key.splitlines():
        out.append("  " + pk_line)
open(path, "w").write("\n".join(out) + "\n")
PYEOF

(cd "$HOMELAB_ROOT/ansible" && ansible-vault encrypt --output "$VAULT_FILE.new" "$TMP")
chmod 600 "$VAULT_FILE.new"
mv "$VAULT_FILE.new" "$VAULT_FILE"
chmod 600 "$VAULT_FILE"
echo "vault.yml updated with GitHub App credentials."
