#!/usr/bin/env bash
# Turn a USB stick into an iPXE boot stick, for PCs whose firmware PXE never reaches the network.
# The stick boots out/tftp/ipxe.efi (UEFI) and chains to this Mac over HTTP exactly like PXE would.
# The Proxmox installer later overwrites the same stick, so use the stick the node will boot from.
#   ./make-usb.sh            list external disks
#   ./make-usb.sh disk4      ERASE disk4 and write the boot stick
set -euo pipefail
cd "$(dirname "$0")"

efi=out/tftp/ipxe.efi
[[ -f $efi ]] || { echo "missing $efi; run ./build.sh first" >&2; exit 1; }

if [[ $# -eq 0 ]]; then
  diskutil list external physical
  echo "Usage: $0 diskN   (erases the whole disk)"
  exit 0
fi

disk=${1#/dev/}
info=$(diskutil info "$disk") || exit 1
grep -Eq 'Device Location: +External|Removable Media: +Removable' <<<"$info" \
  || { echo "$disk is not an external/removable disk; refusing" >&2; exit 1; }
grep -Eq 'Whole: +Yes' <<<"$info" || { echo "pass the whole disk (diskN), not a partition" >&2; exit 1; }

echo "$info" | grep -E 'Device / Media Name|Disk Size'
read -r -p "ERASE /dev/$disk? type yes: " ok
[[ $ok == yes ]] || exit 1

diskutil eraseDisk FAT32 IPXE GPT "/dev/$disk"
mnt=/Volumes/IPXE
mkdir -p "$mnt/EFI/BOOT"
cp "$efi" "$mnt/EFI/BOOT/BOOTX64.EFI"
sync
diskutil eject "/dev/$disk"
echo "Done. Boot the PC from this stick (UEFI, Secure Boot off); keep ./run.sh running."
