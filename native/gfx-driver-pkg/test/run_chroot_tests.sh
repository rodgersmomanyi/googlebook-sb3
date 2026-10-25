#!/bin/bash
# GD-1B-09 (bionic part, v1.2; wave-1 GD-01): the Android builds of the shim test (GB_SHIM_TEST_HOOKS variant of
# vulkan.intel.so) in a read-only chroot of the lapis 276.R4 system/vendor/runtime, with Mesa's nouveau no-op drm-shim
# faking a TU117 for the REAL NVK (libgb_nvk.so from the 8a build). Builder only; the chroot runs as root, so the
# "render node not openable" case drops to uid 9999 in a child (test_shim.c).
#   run 1: fake Intel ICD as the platform driver + fake NVK + real NVK (drm-shim)  -> every v1.2 case on bionic, real
#          NVK listed FIRST with vendor.gb.gfx_active=nvidia, HWUI engine platform only, fds unchanged
#   run 2: the shim's own platform loader (lapis /vendor/lib64/hw/vulkan.intel.so via HMI; 0 Intel GPUs on the
#          builder) + real NVK (drm-shim)                                           -> loader path, NVK first
# usage: run_chroot_tests.sh [nvk results dir, default /build/native/nvk-android/results/8a-1]
set -uo pipefail
G=/build/native/dgpu/gfx-driver-pkg
N=/build/native/nvk-android
RES=${1:-$N/results/8a-1}
ROOTFS=/build/native/dgpu/nvk-sync/rootfs-276R4
SH=/build/native/dgpu/nvk-sync/chroot_dir.sh
R=$G/run-chroot
rm -rf "$R"; mkdir -p "$R"
cp "$G"/out/android-test/{vulkan.intel.so,test_shim,fake_nvk.so,fake_anv.so} "$R/"
cp "$RES/libgb_nvk.so" "$R/"
# test-only nouveau drm-shim of the same Mesa tree (35_build_drm_shim_test.sh; independent of the NVK patch set)
cp "${DRMSHIM:-$N/b-nvk/src/nouveau/drm-shim/libnouveau_noop_drm_shim.so}" "$R/libnouveau_noop_drm_shim.so"
sha256sum "$R"/*
export CHROOT_ENV="LD_PRELOAD=/data/libnouveau_noop_drm_shim.so NOUVEAU_CHIPSET=167"
"$SH" "$ROOTFS" "$R" \
  "/data/test_shim /data/vulkan.intel.so /data/fake_anv.so /data/fake_nvk.so /data/libgb_nvk.so" \
  "/data/test_shim /data/vulkan.intel.so - /data/fake_nvk.so /data/libgb_nvk.so"
