#!/bin/bash
# Build the guest helpers with the Android NDK (r27c; on the builder VM or any Linux x86_64 host) and stage them where
# build_esp.py looks for them. With NDK r27c these commands reproduce the binaries we boot bit for bit
# (gb_vmtools, gb_layout, gb_absmouse, gb_slotsync; checked 2026-10-24).
#   NDK=/build/ndk guest/build.sh [OUT_WORKSPACE]      (default workspace: the repository root)
set -euo pipefail
NDK=${NDK:-/build/ndk}
CC="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/clang --target=x86_64-linux-android34"
D=$(cd "$(dirname "$0")" && pwd)
WS=${1:-$(dirname "$D")}
mkdir -p "$WS/inject/vmtools" "$WS/inject/mouse" "$WS/inject/slotsync"
# VM: minimal VMware Tools (GuestRPC/TCLO, clean power-off, host battery bridge). Needs root (ioperm). The shipped
# binary is this dynamic (bionic) build, although the source header still mentions -static.
$CC -O2 -o "$WS/inject/vmtools/gb_vmtools" "$D/gb_vmtools.c"
# VM: vmwgfx display layout (preferred mode); static, runs at early-init
$CC -static -O2 -Wall -o "$WS/inject/vmtools/gb_layout" "$D/gb_layout.c"
# VM: VMware absolute pointer -> Android absolute mouse (uinput); static
$CC -static -O2 -Wall -o "$WS/inject/mouse/gb_absmouse" "$D/gb_absmouse.c"
# VM + native: A/B slot follower for the systemd-boot ESP; dynamic (bionic)
$CC -O2 -Wall -o "$WS/inject/slotsync/gb_slotsync" "$D/gb_slotsync.c"
# Diagnostic only (gb_vkenum service): needs the AOSP libhardware + Vulkan headers (hardware/hwvulkan.h) in AOSP_INC.
if [ -d "${AOSP_INC:-/build/inc}/hardware" ]; then
  $CC -O2 -Wall -I"${AOSP_INC:-/build/inc}" -o "$WS/inject/vkenum" "$D/vkenum.c" -ldl -llog
else
  echo "vkenum: skipped (set AOSP_INC to a directory with AOSP hardware/ and vulkan/ headers)"
fi
ls -l "$WS/inject/vmtools" "$WS/inject/mouse" "$WS/inject/slotsync"
