#!/bin/bash
# Builder preparation for the Googlebook SB3 installer ISO (DESIGN.md 6.1). Idempotent.
# Installs missing packages from the configured Ubuntu archive only (apt-get); nothing else is downloaded.
# Never touches block devices, the kernel or the boot loader of the builder.
# Usage: bash setup.sh [--check]     (--check: report only, install nothing; exit 1 if something is missing)
set -euo pipefail
KREL=6.8.0-147-generic
# DESIGN 6.1 names xorriso, mtools, exfatprogs; the rest are needed by mk_*.sh and are normally present already.
PKGS="xorriso mtools exfatprogs grub-efi-amd64-bin grub-common busybox-static kbd console-setup-linux
      linux-modules-extra-$KREL ntfs-3g gdisk efibootmgr dosfstools cpio zstd python3.12 util-linux kmod coreutils"
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

missing=""
for p in $PKGS; do
  st=$(dpkg-query -W -f='${Status}' "$p" 2>/dev/null || true)
  case "$st" in
    "install ok installed") ;;
    *) missing="$missing $p" ;;
  esac
done
if [ -n "$missing" ]; then
  if [ $CHECK = 1 ]; then
    echo "SETUP-MISSING$missing"
    exit 1
  fi
  echo "setup: installing$missing"
  if ! sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $missing; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $missing
  fi
fi

# files and tools the build reads (read-only checks)
for f in /boot/vmlinuz-$KREL /boot/System.map-$KREL /lib/modules/$KREL/modules.dep /lib/modules/$KREL/modules.builtin \
         /lib/modules/$KREL/modules.builtin.modinfo /lib/modules/$KREL/modules.order /usr/bin/busybox \
         /usr/share/consolefonts/Lat15-TerminusBold32x16.psf.gz /usr/share/consolefonts/Lat15-Terminus16.psf.gz \
         /usr/lib/grub/x86_64-efi/search_fs_file.mod /usr/bin/python3.12; do
  sudo test -e "$f" || { echo "SETUP-MISSING-FILE $f"; exit 1; }
done
for t in xorriso mmd mcopy mdir mkfs.vfat grub-mkstandalone grub-script-check zstd sgdisk cpio depmod modprobe blkid \
         sha256sum python3.12; do
  command -v "$t" >/dev/null || { echo "SETUP-MISSING-TOOL $t"; exit 1; }
done
sudo -n true || { echo "SETUP-NO-SUDO"; exit 1; }

v_xorriso=$(xorriso -version 2>/dev/null | sed -n '1s/^xorriso \([0-9.]*\).*/\1/p')
v_grub=$(dpkg-query -W -f='${Version}' grub-efi-amd64-bin)
v_zstd=$(zstd -V | sed -n 's/.* v\([0-9.]*\),.*/\1/p')
v_busybox=$(busybox 2>&1 | sed -n '1s/^BusyBox v\([0-9.]*\).*/\1/p')
v_python=$(python3.12 --version | sed 's/^Python //')
echo "SETUP-OK xorriso=$v_xorriso grub=$v_grub zstd=$v_zstd busybox=$v_busybox python=$v_python"
