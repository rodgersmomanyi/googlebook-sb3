#!/bin/bash
# Verification-only (builder): run Android (bionic) binaries in a chroot assembled from an UNPACKED lapis rootfs
# directory (system/, vendor/, apex/com.android.runtime/ as streamed read-only from the VM with
# `adb exec-out "cd / && tar -cf - ..."`). Everything from the rootfs is bind-mounted READ-ONLY on a private tmpfs;
# only <datadir> is writable (mounted at /data). All mounts are undone on exit.
# usage: chroot_dir.sh <rootfs> <datadir> '<shell command run inside, /data = datadir>' ['<cmd2>' ...]
# env: CHROOT_ENV="VAR=value ..." is prepended to every command (e.g. LD_PRELOAD for the drm-shim).
set -uo pipefail
ROOT=$(readlink -f "$1"); DATA=$(readlink -f "$2"); shift 2
C=$(mktemp -d /build/native/dgpu/nvk-sync/chroot.XXXXXX)
mounted=()
cleanup() {
  for ((i=${#mounted[@]}-1; i>=0; i--)); do sudo umount "${mounted[$i]}" 2>/dev/null || sudo umount -l "${mounted[$i]}"; done
  if mount | grep -q " $C"; then echo "WARNING: still mounted under $C"; else rmdir "$C" 2>/dev/null; fi
}
trap cleanup EXIT
mnt() { sudo mount "$@" && mounted+=("${@: -1}"); }
mnt -t tmpfs -o size=4m,mode=0755 tmpfs "$C"
sudo mkdir -p "$C/system" "$C/vendor" "$C/apex/com.android.runtime" "$C/dev" "$C/proc" "$C/sys" "$C/data" "$C/tmp"
mnt --bind "$ROOT/system" "$C/system";  sudo mount -o remount,bind,ro "$C/system"
mnt --bind "$ROOT/vendor" "$C/vendor";  sudo mount -o remount,bind,ro "$C/vendor"
mnt --bind "$ROOT/apex/com.android.runtime" "$C/apex/com.android.runtime"
sudo mount -o remount,bind,ro "$C/apex/com.android.runtime"
mnt --bind /dev "$C/dev"
mnt -t proc proc "$C/proc"
mnt -o bind,ro /sys "$C/sys"
mnt --bind "$DATA" "$C/data"
echo "=== chroot $ROOT (lapis $(cat "$ROOT/BUILD_ID" 2>/dev/null || echo '?')), data=$DATA"
for spec in "$@"; do
  echo "##### ${CHROOT_ENV:+$CHROOT_ENV }$spec"
  if [ -n "${CHROOT_ENV:-}" ]; then
    # shellcheck disable=SC2086
    sudo chroot "$C" /system/bin/env $CHROOT_ENV /system/bin/sh -c "$spec"
  else
    sudo chroot "$C" /system/bin/sh -c "$spec"
  fi
  echo "exit=$?"
done
