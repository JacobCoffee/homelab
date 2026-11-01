#!/bin/bash
# Runs once on each node after install (proxmox-first-boot, ordering=fully-up).
# Safe to re-run: /usr/local/sbin/pxe-first-boot
set -uo pipefail

SERVER="@SERVER@"
REPORT=""
note() { echo "$*"; REPORT+="$*"$'\n'; }

install -m 0755 "$(readlink -f "$0")" /usr/local/sbin/pxe-first-boot 2>/dev/null || true

# --- 1. Cut write load on the USB boot stick ---------------------------------
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/90-usb-boot-disk.conf <<'EOF'
[Journal]
Storage=volatile
RuntimeMaxUse=64M
EOF
systemctl restart systemd-journald
note "journald: logs in RAM only (64M cap)"

noatime_missing='$1 !~ /^#/ && $2 == "/" && $4 !~ /(^|,)noatime(,|$)/'
if awk "$noatime_missing {found=1} END {exit !found}" /etc/fstab; then
  awk "BEGIN {OFS=\"\t\"} $noatime_missing {\$4 = \$4 \",noatime\"} {print}" /etc/fstab > /etc/fstab.new \
    && mv /etc/fstab.new /etc/fstab
  mount -o remount,noatime /
fi
note "root fs: noatime"

# --- 2. Package repos: no subscription key on these nodes --------------------
srcdir=/etc/apt/sources.list.d
signed_by=$(awk -F': *' '/^Signed-By:/ {print $2; exit}' "$srcdir/pve-enterprise.sources" 2>/dev/null)
for f in "$srcdir/pve-enterprise.sources" "$srcdir/ceph.sources"; do
  [ -f "$f" ] && mv "$f" "$f.disabled"
done
codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
cat > "$srcdir/pve-no-subscription.sources" <<EOF
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: $codename
Components: pve-no-subscription
Signed-By: ${signed_by:-/usr/share/keyrings/proxmox-archive-keyring.gpg}
EOF
note "apt: enterprise repos disabled, pve-no-subscription ($codename) enabled"

# --- 3. Internal drive -> LVM-thin storage "vmdata" ---------------------------
boot_pv=$(pvs --noheadings -o pv_name -S vg_name=pve | tr -d ' ' | head -n1)
boot_disk=$(lsblk -no PKNAME "$boot_pv" | head -n1)
candidates=()
while read -r line; do
  NAME= TYPE= TRAN= RM= RO=
  eval "$line"
  [[ $TYPE == disk && $NAME != "$boot_disk" && $TRAN != usb && $RM == 0 && $RO == 0 ]] || continue
  [[ $NAME == zram* || $NAME == loop* ]] && continue
  candidates+=("$NAME")
done < <(lsblk -dn -P -o NAME,TYPE,TRAN,RM,RO)

if vgs vmdata >/dev/null 2>&1; then
  note "vmdata: volume group already present"
elif [ ${#candidates[@]} -ne 1 ]; then
  note "vmdata: SKIPPED - found ${#candidates[@]} internal disks (${candidates[*]:-none}); set up storage by hand"
else
  disk="/dev/${candidates[0]}"
  if [ -n "$(lsblk -n -o NAME "$disk" | tail -n +2)" ] || [ -n "$(wipefs -n "$disk")" ]; then
    note "vmdata: SKIPPED - $disk is not blank. To use it (DESTROYS its data): wipefs -a $disk && pxe-first-boot"
  elif pvcreate -y "$disk" && vgcreate vmdata "$disk" && lvcreate -y -l 100%FREE --thinpool data vmdata; then
    note "vmdata: LVM-thin pool created on $disk ($(lsblk -dn -o SIZE "$disk" | tr -d ' '))"
  else
    note "vmdata: FAILED creating LVM-thin on $disk"
  fi
fi

if vgs vmdata >/dev/null 2>&1; then
  grep -q '^lvmthin: vmdata$' /etc/pve/storage.cfg \
    || pvesm add lvmthin vmdata --vgname vmdata --thinpool data --content images,rootdir
  # Keep VM/CT disks off the USB stick.
  pvesm set local --content iso,vztmpl,backup
  note "storage: vmdata holds VM/CT disks; local (USB) holds iso,vztmpl,backup"
fi

curl -fsS -m 10 -X POST --data-binary "$REPORT" "$SERVER/first-boot-report?host=$(hostname -s)" >/dev/null \
  || echo "could not report to $SERVER"
exit 0
