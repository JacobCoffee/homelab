#!/bin/bash
# Runs inside the pve-pxe-builder container with the project mounted at /work.
# Inputs (env): SERVER_IP, HTTP_PORT, ISO, OUT_DIR
set -euo pipefail
cd /work

server="$SERVER_IP:$HTTP_PORT"
OUT_DIR="${OUT_DIR:-out}"

echo "==> Preparing auto-install PXE image (answer URL http://$server/answer)"
rm -rf "$OUT_DIR/http/pve" "$OUT_DIR/tftp"
mkdir -p "$OUT_DIR/http/pve" "$OUT_DIR/tftp"
proxmox-auto-install-assistant prepare-iso "$ISO" \
  --fetch-from http --url "http://$server/answer" \
  --pxe-loader ipxe --tmp /tmp --output "$OUT_DIR/http/pve"

sed -i 's/ ramdisk_size=16777216 / ramdisk_size=16777216 initramfs_options=size=90% /' "$OUT_DIR/http/pve/boot.ipxe"
grep -q 'initramfs_options=size=90%' "$OUT_DIR/http/pve/boot.ipxe" \
  || { echo "boot.ipxe format changed; cannot add initramfs_options" >&2; exit 1; }

echo "==> Building iPXE (embedded chain to http://$server/boot.ipxe)"
[ -d cache/ipxe/.git ] || git clone --quiet --depth 1 https://github.com/ipxe/ipxe.git cache/ipxe
sed "s/@SERVER@/$server/g" builder/embed.ipxe > /tmp/embed.ipxe
if ! make -C cache/ipxe/src -j"$(nproc)" bin-x86_64-efi/ipxe.efi bin/undionly.kpxe \
     EMBED=/tmp/embed.ipxe > /tmp/ipxe-build.log 2>&1; then
  tail -n 60 /tmp/ipxe-build.log
  exit 1
fi
cp cache/ipxe/src/bin-x86_64-efi/ipxe.efi cache/ipxe/src/bin/undionly.kpxe "$OUT_DIR/tftp/"
echo "    iPXE $(git -C cache/ipxe rev-parse --short HEAD)"

echo "==> Hashing root password"
mkpasswd -m yescrypt -s < secrets/root-password > secrets/root-password.hash
chmod 600 secrets/root-password.hash
