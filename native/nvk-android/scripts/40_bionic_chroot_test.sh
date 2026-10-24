#!/bin/bash
# Step 4 (builder VM only): run the ANDROID builds (bionic nvk_probe + vulkan.nouveau.so) on the builder's Linux
# kernel inside a read-only chroot made from the real lapis system image (/build/system_a.img, EROFS, SDK 37) plus
# its com.android.runtime APEX (bionic linker/libc) and the lapis vendor image (/tmp/vmnt). No NVIDIA GPU here, so
# the expected result is NO_DEVICE (exit 3) - the same thing the VMware VM would report. This exercises bionic
# loading of the ICD (relocations, TLS, constructors), both entry paths (vk_icd* and the hwvulkan HMI path used by
# Android's libvulkan), vkCreateInstance and nouveau device enumeration.
# Everything is mounted read-only under $W/chroot and unmounted on exit (trap); nothing is written to the images.
set -uo pipefail
. "$(dirname "$0")/00_env.sh"
C=$W/chroot
R=$W/run
mounted=()
cleanup() {
  for ((i=${#mounted[@]}-1; i>=0; i--)); do sudo umount "${mounted[$i]}" 2>/dev/null || sudo umount -l "${mounted[$i]}"; done
  mount | grep -q " $C" && echo "WARNING: something is still mounted under $C" || rmdir "$C" 2>/dev/null
}
trap cleanup EXIT
mnt() { sudo mount "$@" && mounted+=("${@: -1}"); }

mkdir -p "$C" "$R"
cp "$W/probe/nvk_probe" "$W/stage/vulkan.nouveau.so" "$R/"
if [ ! -f "$R/runtime_payload.img" ]; then
  sudo mount -t erofs -o loop,ro /build/system_a.img "$C" && mounted+=("$C")
  sudo unzip -p "$C/system/apex/com.android.runtime.apex" apex_payload.img > "$R/runtime_payload.img"
  sudo umount "$C"; mounted=()
fi
mnt -t erofs -o loop,ro /build/system_a.img "$C"
mnt -t tmpfs -o size=1m tmpfs "$C/apex"
sudo mkdir -p "$C/apex/com.android.runtime"
mnt -o loop,ro "$R/runtime_payload.img" "$C/apex/com.android.runtime"
mnt --bind /dev "$C/dev"
mnt -t proc proc "$C/proc"
mnt -o bind,ro /sys "$C/sys"
mnt -o bind,ro /tmp/vmnt "$C/vendor"
mnt --bind "$R" "$C/data"
echo "=== chroot: lapis system $(sudo grep -h '^ro.build.id=' "$C/system/build.prop"), runtime APEX mounted"
for mode in "" "-H"; do
  echo "##### nvk_probe $mode -d /data/vulkan.nouveau.so"
  sudo chroot "$C" /data/nvk_probe $mode -d /data/vulkan.nouveau.so
  echo "exit=$?"
done
# Optional: Mesa's nouveau no-op drm-shim (LD_PRELOAD) fakes a nouveau render node, so NVK creates a real VkDevice for
# a TU117 (NV167) and compiles the probe's pipeline with NAK on bionic. Submissions are no-ops (no GPU), so the pixel
# check MUST fail here - this run only proves device creation, NAK compilation and command recording work.
# (-t offscreen: the anb tests need the gralloc allocator service over binder, which this chroot does not have.)
SHIM=$W/b-nvk/src/nouveau/drm-shim/libnouveau_noop_drm_shim.so
if [ -f "$SHIM" ]; then
  cp "$SHIM" "$R/"
  echo "##### drm-shim (fake TU117, no real GPU): nvk_probe -F -t offscreen -n 50"
  sudo chroot "$C" /system/bin/env LD_PRELOAD=/data/libnouveau_noop_drm_shim.so NOUVEAU_CHIPSET=167     /data/nvk_probe -F -t offscreen -n 50 -d /data/vulkan.nouveau.so
  echo "exit=$? (pixel FAIL expected: no hardware behind the shim)"
fi
