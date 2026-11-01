#!/usr/bin/env bash
# Builds everything the PXE server needs into out/. Re-run whenever the Mac's
# LAN IP, config.env, or the Proxmox ISO version changes.
set -euo pipefail
cd "$(dirname "$0")"
source ./config.env

SERVER_IP=$(ipconfig getifaddr "$LAN_IFACE") || { echo "No IPv4 address on $LAN_IFACE" >&2; exit 1; }
ISO="cache/$PVE_ISO_NAME"
mkdir -p cache out secrets state

if [ ! -f "$ISO" ]; then
  echo "==> Downloading $PVE_ISO_NAME"
  curl -fL --progress-bar -o "$ISO.part" "https://enterprise.proxmox.com/iso/$PVE_ISO_NAME"
  mv "$ISO.part" "$ISO"
fi
echo "$PVE_ISO_SHA256  $ISO" | shasum -a 256 -c - >/dev/null || { echo "Checksum mismatch for $ISO" >&2; exit 1; }

if [ ! -s secrets/root-password ]; then
  (umask 077; openssl rand -base64 18 > secrets/root-password)
  echo "==> Generated root password in secrets/root-password"
fi

# root SSH access on every node: your agent's keys (Secretive) for you, plus a
# file key for Ansible so automation doesn't need a Touch ID prompt per connection.
if [ ! -f secrets/automation_ed25519 ]; then
  ssh-keygen -q -t ed25519 -N "" -C "homelab-automation" -f secrets/automation_ed25519
  echo "==> Generated automation SSH key secrets/automation_ed25519"
fi
{ ssh-add -L 2>/dev/null | grep -E '^(ssh-|ecdsa-|sk-)' || true; cat secrets/automation_ed25519.pub; } \
  | sort -u > secrets/authorized_keys
echo "==> root SSH keys: $(wc -l < secrets/authorized_keys | tr -d ' ') ($(cut -d' ' -f3 secrets/authorized_keys | paste -sd, -))"

echo "==> Building builder image"
docker build -q -t pve-pxe-builder builder/ >/dev/null

docker run --rm -v "$PWD":/work -e SERVER_IP="$SERVER_IP" -e HTTP_PORT="$HTTP_PORT" -e ISO="$ISO" \
  pve-pxe-builder bash /work/builder/build-inside.sh

printf 'SERVER_IP=%s\n' "$SERVER_IP" > out/build.env

echo "==> Validating answer file"
python3 server/server.py render-sample \
  | docker run --rm -i pve-pxe-builder sh -c 'cat > /tmp/answer.toml && proxmox-auto-install-assistant validate-answer /tmp/answer.toml'

echo "==> Done. Server IP baked in: $SERVER_IP. Start with ./run.sh"
