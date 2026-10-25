#!/bin/bash
# NV-02 / NV-03 / NV-06 for one NVK build (builder only, read-only chroot of the lapis rootfs streamed from the VM).
# usage: run_checks.sh <results dir with vulkan.nouveau.so [libgb_nvk.so]> <drm-shim .so> [rootfs]
#   NV-06: import_check.py on vulkan.nouveau.so, libgb_nvk.so (if present), nvk_probe (NEEDED within LLNDK +
#          libhardware, every import resolved against 276.R4, SONAMEs)
#   NV-02: nvk_probe on both entry paths (vk_icdGetInstanceProcAddr and the hwvulkan HMI path) -> exit 3 NO_DEVICE;
#          hmi_check (libvulkan's LoadDriver/CreateInstance/dispatch-magic contract)
#   NV-03: with the drm-shim (NOUVEAU_CHIPSET=167 = TU117): hmi_check device creation + Android entry points, and every
#          nvk_probe mode up to its first device call (pixel/allocation failures are expected: no GPU, no allocator)
set -uo pipefail
RES=$(readlink -f "$1"); SHIM=$(readlink -f "$2")
ROOTFS=${3:-/build/native/dgpu/nvk-sync/rootfs-276R4}
N=/build/native/nvk-android
V=/build/native/dgpu/nvk-sync
R=$V/run-$(basename "$RES")
rm -rf "$R"; mkdir -p "$R"
cp "$RES/vulkan.nouveau.so" "$N/probe/nvk_probe" "$N/verify/hmi_check" "$SHIM" "$R/"
[ -f "$RES/libgb_nvk.so" ] && cp "$RES/libgb_nvk.so" "$R/"
echo "### checks for $RES against lapis $(cat "$ROOTFS/BUILD_ID") ($(date -Is))"
sha256sum "$R"/*
echo "### NV-06 import / NEEDED check"
L=""; [ -f "$R/libgb_nvk.so" ] && L="$R/libgb_nvk.so"
# shellcheck disable=SC2086
python3 -I "$V/import_check.py" --root "$ROOTFS" "$R/vulkan.nouveau.so" $L "$R/nvk_probe"
echo "import_check_exit=$?"
echo "### NV-02 bionic chroot, no NVIDIA device"
"$V/chroot_dir.sh" "$ROOTFS" "$R" \
  "/data/nvk_probe -d /data/vulkan.nouveau.so" \
  "/data/nvk_probe -H -d /data/vulkan.nouveau.so" \
  "/data/hmi_check /data/vulkan.nouveau.so"
echo "### NV-03 drm-shim TU117"
CHROOT_ENV="LD_PRELOAD=/data/$(basename "$SHIM") NOUVEAU_CHIPSET=167" "$V/chroot_dir.sh" "$ROOTFS" "$R" \
  "/data/hmi_check /data/vulkan.nouveau.so" \
  "/data/nvk_probe -F -T 20 -t offscreen -n 20 -d /data/vulkan.nouveau.so" \
  "/data/nvk_probe -T 20 -t a17-swapchain-model -d /data/vulkan.nouveau.so" \
  "/data/nvk_probe -T 20 -t ahb-i915-readback -d /data/vulkan.nouveau.so" \
  "/data/nvk_probe -T 20 -t anb -d /data/vulkan.nouveau.so"
