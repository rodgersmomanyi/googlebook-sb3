#!/bin/bash
# Step 3: build nvk_probe for Android (NDK) and a Linux host copy used to validate the test logic with lavapipe
# (and the "no NVIDIA device" path with the host's NVK) on the builder VM, which has no NVIDIA GPU.
# Sources (copied from the repo's src/ into $W/probe): nvk_probe.c, tri.vert, tri.frag, fs.vert, pat.frag, readback.comp
set -euo pipefail
. "$(dirname "$0")/00_env.sh"
S=$W/probe
cd "$S"
spv() { glslangValidator -V --target-env vulkan1.0 --vn "$1" -o "$1.h" "$2" >/dev/null; }
spv tri_vert_spv tri.vert
spv tri_frag_spv tri.frag
spv fs_vert_spv fs.vert
spv pat_frag_spv pat.frag
spv readback_comp_spv readback.comp
cat tri_vert_spv.h tri_frag_spv.h fs_vert_spv.h pat_frag_spv.h readback_comp_spv.h > probe_shaders.h
rm -f tri_vert_spv.h tri_frag_spv.h fs_vert_spv.h pat_frag_spv.h readback_comp_spv.h
# Android x86_64: vulkan + vk_android_native_buffer.h from Mesa's include/, cutils/native_handle.h and
# hardware/gralloc.h from Mesa's android_stub (header-only use). Links only NDK libs: libnativewindow
# (AHardwareBuffer_*), libdl, libc.
"$TC/x86_64-linux-android${API}-clang" -O2 -Wall -Wextra -Wno-missing-field-initializers -Wno-unused-parameter \
  -I"$S" -I"$MESA_SRC/include" -I"$MESA_SRC/include/android_stub" \
  -o "$S/nvk_probe" nvk_probe.c -ldl -lnativewindow
"$TC/llvm-strip" --strip-unneeded -o "$S/nvk_probe.stripped" "$S/nvk_probe"
# Linux host build (no Android tests compiled in)
cc -O2 -Wall -Wextra -Wno-missing-field-initializers -Wno-unused-parameter -I"$S" -I"$MESA_SRC/include" \
  -o "$S/nvk_probe_host" nvk_probe.c -ldl
file "$S/nvk_probe" "$S/nvk_probe_host"
"$TC/llvm-readelf" -d "$S/nvk_probe" | grep -E 'NEEDED'
