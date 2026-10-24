#!/bin/bash
# Verification-only: run Android (bionic) binaries in a read-only chroot of the lapis system image + runtime APEX +
# lapis vendor. Everything mounted RO (except the private data dir) and unmounted on exit.
# usage: chroot_run.sh <cmd> [args...]   (cmd paths are inside the chroot; /data = $V/run)
set -uo pipefail
V=/build/native/nvk-verify
C=$V/chroot
R=$V/run
mounted=()
cleanup() {
  for ((i=${#mounted[@]}-1; i>=0; i--)); do sudo umount "${mounted[$i]}" 2>/dev/null || sudo umount -l "${mounted[$i]}"; done
  if mount | grep -q " $C"; then echo "WARNING: still mounted under $C"; else rmdir "$C" 2>/dev/null; fi
}
trap cleanup EXIT
mnt() { sudo mount "$@" && mounted+=("${@: -1}"); }
mkdir -p "$C" "$R"
if [ ! -f "$V/runtime_payload.img" ]; then
  sudo mount -t erofs -o loop,ro /build/system_a.img "$C" || exit 9
  sudo unzip -p "$C/system/apex/com.android.runtime.apex" apex_payload.img > "$V/runtime_payload.img"
  sudo umount "$C"
fi
mnt -t erofs -o loop,ro /build/system_a.img "$C"
mnt -t tmpfs -o size=1m tmpfs "$C/apex"
sudo mkdir -p "$C/apex/com.android.runtime"
mnt -o loop,ro "$V/runtime_payload.img" "$C/apex/com.android.runtime"
mnt --bind /dev "$C/dev"
mnt -t proc proc "$C/proc"
mnt -o bind,ro /sys "$C/sys"
mnt -o bind,ro /tmp/vmnt "$C/vendor"
mnt --bind "$R" "$C/data"
echo "=== chroot: $(sudo grep -h '^ro.build.id=' "$C/system/build.prop") / vendor $(sudo grep -h '^ro.vendor.build.id=' "$C/vendor/build.prop" 2>/dev/null)"
for spec in "$@"; do
  echo "##### $spec"
  sudo chroot "$C" /system/bin/sh -c "$spec"
  echo "exit=$?"
done
