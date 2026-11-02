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

# --- 3. Internal drives -> ZFS "vmdata" (+ "fast") --------------------------------
# Only blank drives are used; anything with data on it is reported, never touched.
#   2 SATA/SAS drives       -> vmdata = mirror of them; a single NVMe beside them -> fast
#   2 NVMe, nothing else    -> vmdata = mirror
#   exactly 1 drive         -> vmdata = that drive
#   1 SATA + 1 NVMe         -> vmdata = the NVMe; the SATA drive is left for you
# Same pool name on every node, so cluster-wide storage config and replication work.
cat > /usr/local/sbin/pxe-wipe-disk <<'EOF'
#!/bin/bash
# DESTROYS all data on the given whole disk(s): deactivates LVM on them, then wipes
# every partition and the partition table. Usage: pxe-wipe-disk /dev/sdX [...]
set -euo pipefail
for disk in "$@"; do
  [ -b "$disk" ] || { echo "not a block device: $disk" >&2; exit 1; }
  for vg in $(pvs --noheadings -o vg_name,pv_name 2>/dev/null | awk -v d="$disk" 'index($2, d) == 1 {print $1}' | sort -u); do
    vgchange -an "$vg" >/dev/null
  done
  for part in $(lsblk -nr -o PATH "$disk" | tail -n +2 | tac); do wipefs -aq "$part" || true; done
  wipefs -aq "$disk"
  echo "wiped $disk"
done
EOF
chmod 0755 /usr/local/sbin/pxe-wipe-disk

boot_pv=$(pvs --noheadings -o pv_name -S vg_name=pve | tr -d ' ' | head -n1)
boot_disk=$(lsblk -no PKNAME "$boot_pv" | head -n1)
in_pool=$(zpool list -vHP 2>/dev/null | awk '$1 ~ /^\/dev\// {print $1}' | xargs -r -n1 lsblk -no PKNAME 2>/dev/null | sort -u)
blank_nvme=() blank_other=() used=()
while read -r line; do
  NAME= TYPE= TRAN= RM= RO=
  eval "$line"
  [[ $TYPE == disk && $NAME != "$boot_disk" && $TRAN != usb && $RM == 0 && $RO == 0 ]] || continue
  [[ $NAME == zram* || $NAME == loop* ]] && continue
  grep -qx "$NAME" <<<"$in_pool" && continue
  if [ -n "$(lsblk -n -o NAME "/dev/$NAME" | tail -n +2)" ] || [ -n "$(wipefs -n "/dev/$NAME")" ]; then
    used+=("/dev/$NAME")
  elif [[ $TRAN == nvme ]]; then
    blank_nvme+=("$NAME")
  else
    blank_other+=("$NAME")
  fi
done < <(lsblk -dn -P -o NAME,TYPE,TRAN,RM,RO)

by_id() {  # stable path for a disk, so pools survive device renaming
  local link
  for link in /dev/disk/by-id/ata-* /dev/disk/by-id/nvme-* /dev/disk/by-id/scsi-*; do
    [[ -e $link && $link != *-part* && $link != *nvme-eui.* && $link != *_1 ]] || continue
    [ "$(readlink -f "$link")" = "/dev/$1" ] && { echo "$link"; return; }
  done
  echo "/dev/$1"
}
make_pool() {  # name, then "mirror" or nothing, then disks
  local name=$1; shift
  local layout=() devs=()
  [ "$1" = mirror ] && { layout=(mirror); shift; }
  for d in "$@"; do devs+=("$(by_id "$d")"); done
  if zpool create -f -o ashift=12 -O compression=lz4 -O atime=off -O xattr=sa -O acltype=posixacl \
    "$name" "${layout[@]}" "${devs[@]}"; then
    note "$name: ZFS ${layout[*]:-single drive} on $* ($(lsblk -dn -o SIZE "/dev/$1" | tr -d ' ')${layout:+ each})"
  else
    note "$name: FAILED creating ZFS on $*"
  fi
}

if zpool list vmdata >/dev/null 2>&1; then
  note "vmdata: ZFS pool already present"
elif [ ${#blank_other[@]} -eq 2 ]; then
  make_pool vmdata mirror "${blank_other[@]}"
  [ ${#blank_nvme[@]} -eq 1 ] && ! zpool list fast >/dev/null 2>&1 && make_pool fast "${blank_nvme[0]}"
elif [ ${#blank_nvme[@]} -eq 2 ] && [ ${#blank_other[@]} -eq 0 ]; then
  make_pool vmdata mirror "${blank_nvme[@]}"
elif [ $(( ${#blank_nvme[@]} + ${#blank_other[@]} )) -eq 1 ]; then
  make_pool vmdata "${blank_nvme[@]}" "${blank_other[@]}"
elif [ ${#blank_nvme[@]} -eq 1 ] && [ ${#blank_other[@]} -eq 1 ]; then
  make_pool vmdata "${blank_nvme[0]}"
  note "storage: /dev/${blank_other[0]} left unused"
elif [ $(( ${#blank_nvme[@]} + ${#blank_other[@]} )) -eq 0 ]; then
  note "vmdata: not created - no blank internal drive"
else
  note "vmdata: SKIPPED - blank drives nvme=(${blank_nvme[*]}) other=(${blank_other[*]}) match no rule; set up by hand"
fi
[ ${#used[@]} -gt 0 ] && note "storage: not blank, left alone: ${used[*]}. To use (DESTROYS their data): pxe-wipe-disk ${used[*]} && pxe-first-boot"

# Storage entries are cluster-wide once clustered, so registering is its own script:
# Ansible's pve_cluster role re-runs it on a node after it joins.
cat > /usr/local/sbin/pxe-register-storage <<'EOF'
#!/bin/bash
# Register this node's ZFS pools in the Proxmox storage config (cluster-wide when clustered).
# vmdata exists on every node by convention; fast only where there is a spare NVMe.
self=$(hostname -s) cfg=/etc/pve/storage.cfg
nodes_of() { awk -v s="zfspool: $1" '$0 == s {f = 1; next} /^[a-z]+: / {f = 0} f && $1 == "nodes" {print $2}' "$cfg"; }
if zpool list vmdata >/dev/null 2>&1 && ! grep -qx 'zfspool: vmdata' "$cfg"; then
  pvesm add zfspool vmdata --pool vmdata --content images,rootdir --sparse 1
fi
if zpool list fast >/dev/null 2>&1; then
  if ! grep -qx 'zfspool: fast' "$cfg"; then
    pvesm add zfspool fast --pool fast --content images,rootdir --sparse 1 --nodes "$self"
  else
    nodes=$(nodes_of fast)
    if [ -n "$nodes" ] && [[ ",$nodes," != *",$self,"* ]]; then pvesm set fast --nodes "$nodes,$self"; fi
  fi
fi
exit 0
EOF
chmod 0755 /usr/local/sbin/pxe-register-storage
/usr/local/sbin/pxe-register-storage
if zpool list vmdata >/dev/null 2>&1; then
  # ZFS caches in RAM (ARC); cap it so VMs keep the memory.
  echo "options zfs zfs_arc_max=$((2 << 30))" > /etc/modprobe.d/zfs-arc.conf
  echo $((2 << 30)) > /sys/module/zfs/parameters/zfs_arc_max
  # Keep VM/CT disks off the USB stick.
  pvesm set local --content iso,vztmpl,backup
  note "storage: vmdata (ZFS) holds VM/CT disks; local (USB) holds iso,vztmpl,backup; ARC capped at 2G"
fi

# --- 4. HA services: only useful in a cluster, and they write to the stick constantly
if [ -f /etc/pve/corosync.conf ]; then
  note "ha: node is clustered; HA services left as they are"
else
  systemctl disable --now pve-ha-lrm pve-ha-crm >/dev/null 2>&1
  note "ha: pve-ha-lrm/crm disabled (single node). After joining a cluster: systemctl enable --now pve-ha-lrm pve-ha-crm"
fi

# --- 5. "No valid subscription" pop-up ------------------------------------------
# Patch only the login/update check's condition, so the UI proceeds without the
# dialog. A dpkg hook re-applies it whenever proxmox-widget-toolkit is upgraded.
cat > /usr/local/sbin/pxe-no-nag <<'EOF'
#!/bin/sh
f=/usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js
[ -f "$f" ] && perl -0pi -e "s/res\.data\.status\.toLowerCase\(\) !== 'active'(\s*\)\s*\{)/false\1/" "$f"
exit 0
EOF
chmod 0755 /usr/local/sbin/pxe-no-nag
echo 'DPkg::Post-Invoke { "/usr/local/sbin/pxe-no-nag"; };' > /etc/apt/apt.conf.d/86pxe-no-nag
/usr/local/sbin/pxe-no-nag
if grep -Pzq '\|\|\s*false\s*\)\s*\{' /usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js; then
  note "ui: subscription pop-up removed (re-applied after each upgrade)"
else
  note "ui: subscription pop-up NOT removed - proxmoxlib.js changed; check /usr/local/sbin/pxe-no-nag"
fi

# --- 6. Updates --------------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
if apt-get -q update >/dev/null && apt-get -yq -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
  dist-upgrade >/var/log/pxe-first-boot-upgrade.log 2>&1; then
  newest=$(ls /boot/vmlinuz-*-pve 2>/dev/null | sed 's|.*/vmlinuz-||' | sort -V | tail -n1)
  if [ -n "$newest" ] && [ "$newest" != "$(uname -r)" ]; then
    note "apt: upgraded; reboot to run kernel $newest (now $(uname -r))"
  else
    note "apt: upgraded; no reboot needed"
  fi
else
  note "apt: upgrade FAILED - see /var/log/pxe-first-boot-upgrade.log"
fi

curl -fsS -m 10 -X POST --data-binary "$REPORT" "$SERVER/first-boot-report?host=$(hostname -s)" >/dev/null \
  || echo "could not report to $SERVER"
exit 0
