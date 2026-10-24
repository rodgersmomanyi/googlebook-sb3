#!/bin/bash
# Step 3: build nvk_probe for Android (NDK) and a Linux host copy used to validate the test logic with lavapipe
# (and the "no NVIDIA device" path with the host's NVK) on the builder VM, which has no NVIDIA GPU.
set -euo pipefail
. "$(dirname "$0")/00_env.sh"
S=$W/probe
cd "$S"
glslangValidator -V --target-env vulkan1.0 --vn tri_vert_spv -o tri_vert.h tri.vert >/dev/null
glslangValidator -V --target-env vulkan1.0 --vn tri_frag_spv -o tri_frag.h tri.frag >/dev/null
cat tri_vert.h tri_frag.h > probe_shaders.h
# Android x86_64: vulkan + vk_android_native_buffer.h from Mesa's include/, cutils/native_handle.h from Mesa's
# android_stub (header-only use). Links only NDK libs: libnativewindow (AHardwareBuffer_*), libdl, libc.
"$TC/x86_64-linux-android${API}-clang" -O2 -Wall -Wextra -Wno-missing-field-initializers -Wno-unused-parameter \
  -I"$S" -I"$MESA_SRC/include" -I"$MESA_SRC/include/android_stub" \
  -o "$S/nvk_probe" nvk_probe.c -ldl -lnativewindow
"$TC/llvm-strip" --strip-unneeded -o "$S/nvk_probe.stripped" "$S/nvk_probe"
# Linux host build (no Android tests compiled in)
cc -O2 -Wall -Wextra -Wno-missing-field-initializers -Wno-unused-parameter -I"$S" -I"$MESA_SRC/include" \
  -o "$S/nvk_probe_host" nvk_probe.c -ldl
file "$S/nvk_probe" "$S/nvk_probe_host"
"$TC/llvm-readelf" -d "$S/nvk_probe" | grep -E 'NEEDED'
