#!/bin/bash
# glibc host build of the SAME sources (GB_COMPUTE_TESTING=ON) for the lavapipe tests (GC-02..08):
# libgb_nvk_loader.so, libgb_gpu_compute.so, the launcher, test_loader, test-backend-ops, gc_probe.
# usage: 20_build_host.sh [builddir]
set -euo pipefail
. "$(dirname "$0")/00_env.sh"
B=${1:-$W/b-host}
gb_check_space
gb_wait_quiet
PM="-ffile-prefix-map=$LLAMA=/llama.cpp -ffile-prefix-map=$SRC=/gpu-compute -ffile-prefix-map=$B=/build"
$NICE cmake -G Ninja -S "$SRC" -B "$B" -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++ -DCMAKE_C_FLAGS="$PM" -DCMAKE_CXX_FLAGS="$PM" \
  -DGB_LLAMA_DIR="$LLAMA" -DGB_VK_INC="$INC" -DGB_LLAMA_TAG="$LLAMA_TAG" -DGB_COMPUTE_TESTING=ON \
  -DVulkan_INCLUDE_DIR="$INC" -DVulkan_LIBRARY="$SRC/loader/libgb_nvk_loader.c" \
  -DVulkan_GLSLC_EXECUTABLE=/usr/bin/glslc -DSPIRV-Headers_DIR=/usr/share/cmake/SPIRV-Headers \
  -DPython3_EXECUTABLE=/usr/bin/python3 > "$B.configure.log" 2>&1 || { tail -40 "$B.configure.log"; exit 1; }
grep -E "supported by glslc|Vulkan found|GGML_" "$B.configure.log" | head -20 || true
$NICE ninja -C "$B" -j"$JOBS" gb_nvk_loader gb_gpu_compute_lib gb_gpu_compute_launcher test_loader test-backend-ops gc_probe \
  > "$B.build.log" 2>&1 || { grep -E "error|Error" "$B.build.log" | head -40; tail -20 "$B.build.log"; exit 1; }
echo "host build ok: $B"
ls -l "$B"/libgb_nvk_loader.so "$B"/libgb_gpu_compute.so "$B"/gb_gpu_compute "$B"/test-backend-ops
