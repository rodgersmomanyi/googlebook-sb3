#!/bin/bash
# Verification only (builder): run Android (bionic) binaries in a chroot built from an UNPACKED lapis 276.R4 rootfs
# (system/, vendor/, apex/com.android.runtime/, streamed read-only from the VM by component nvk-gfxdriver into
# /build/native/dgpu/nvk-sync/rootfs-276R4). Adapted from nvk-android/verify/chroot_dir.sh so that the temporary
# mount point lives in THIS component's scratch. Rootfs bind mounts are read-only; only <datadir> (/data) is
# writable. All mounts are undone on exit.
# usage: chroot_dir.sh <rootfs> <datadir> '<cmd>' ['<cmd2>' ...]    env CHROOT_ENV="VAR=value ..." for every command,
#        CHROOT_ODM=<dir> bind-mounts <dir> read-only at /odm
set -uo pipefail
ROOT=$(readlink -f "$1"); DATA=$(readlink -f "$2"); shift 2
C=$(mktemp -d /build/native/dgpu/gpu-compute/chroot.XXXXXX)
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
if [ -n "${CHROOT_ODM:-}" ]; then   # optional fake /odm (read-only), e.g. etc/gb_hw + the dgpu payload tree
  sudo mkdir -p "$C/odm"
  mnt --bind "$(readlink -f "$CHROOT_ODM")" "$C/odm"; sudo mount -o remount,bind,ro "$C/odm"
fi
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
