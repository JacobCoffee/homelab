#!/bin/bash
# Runs inside the pve-pxe-builder container with the project mounted at /work.
# Inputs (env): SERVER_IP, HTTP_PORT, ISO
set -euo pipefail
cd /work

server="$SERVER_IP:$HTTP_PORT"

echo "==> Preparing auto-install PXE image (answer URL http://$server/answer)"
rm -rf out/http/pve out/tftp
mkdir -p out/http/pve out/tftp
proxmox-auto-install-assistant prepare-iso "$ISO" \
  --fetch-from http --url "http://$server/answer" \
  --pxe-loader ipxe --tmp /tmp --output out/http/pve

# The kernel caps the initramfs tmpfs at 50% of RAM, which the ~1.6 GB ISO
# overflows on machines with less than ~8 GB. Let it use 90%.
sed -i 's/ ramdisk_size=16777216 / ramdisk_size=16777216 initramfs_options=size=90% /' out/http/pve/boot.ipxe
grep -q 'initramfs_options=size=90%' out/http/pve/boot.ipxe \
  || { echo "boot.ipxe format changed; cannot add initramfs_options" >&2; exit 1; }

echo "==> Building iPXE (embedded chain to http://$server/boot.ipxe)"
[ -d cache/ipxe/.git ] || git clone --quiet --depth 1 https://github.com/ipxe/ipxe.git cache/ipxe
sed "s/@SERVER@/$server/g" builder/embed.ipxe > /tmp/embed.ipxe
if ! make -C cache/ipxe/src -j"$(nproc)" bin-x86_64-efi/ipxe.efi bin/undionly.kpxe \
     EMBED=/tmp/embed.ipxe > /tmp/ipxe-build.log 2>&1; then
  tail -n 60 /tmp/ipxe-build.log
  exit 1
fi
cp cache/ipxe/src/bin-x86_64-efi/ipxe.efi cache/ipxe/src/bin/undionly.kpxe out/tftp/
echo "    iPXE $(git -C cache/ipxe rev-parse --short HEAD)"

echo "==> Hashing root password"
mkpasswd -m yescrypt -s < secrets/root-password > secrets/root-password.hash
chmod 600 secrets/root-password.hash
