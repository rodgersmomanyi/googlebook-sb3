#!/bin/bash
# NDK r27c cross-build for Android x86_64 API 35 (lapis SDK 37 is forward compatible):
#   <out>/gb_gpu_compute                 launcher        -> /vendor/bin/gb_gpu_compute
#   <out>/lib64/libgb_nvk_loader.so      loader stub     -> /odm/dgpu/lib64/gpu-compute/
#   <out>/lib64/libgb_gpu_compute.so     service + llama -> /odm/dgpu/lib64/gpu-compute/
#   <out>/test/gc_probe                  test harness (never shipped)
#   <out>/unstripped/                    debug copies (never shipped)
#   <out>/SHA256SUMS
# usage: 30_build_android.sh [builddir] [outdir]
set -euo pipefail
. "$(dirname "$0")/00_env.sh"
B=${1:-$W/b-android}
O=${2:-$W/out-android}
gb_check_space
gb_wait_quiet
PM="-ffile-prefix-map=$LLAMA=/llama.cpp -ffile-prefix-map=$SRC=/gpu-compute -ffile-prefix-map=$B=/build -ffile-prefix-map=$NDK=/ndk"
LD="-Wl,-z,max-page-size=16384 -Wl,--build-id=sha1"
$NICE cmake -G Ninja -S "$SRC" -B "$B" -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" -DANDROID_ABI=x86_64 \
  -DANDROID_PLATFORM="android-$API" -DANDROID_STL=c++_static \
  -DCMAKE_C_FLAGS="$PM" -DCMAKE_CXX_FLAGS="$PM" -DCMAKE_SHARED_LINKER_FLAGS="$LD" -DCMAKE_EXE_LINKER_FLAGS="$LD" \
  -DGB_LLAMA_DIR="$LLAMA" -DGB_VK_INC="$INC" -DGB_LLAMA_TAG="$LLAMA_TAG" \
  -DVulkan_INCLUDE_DIR="$INC" -DVulkan_LIBRARY="$SRC/loader/libgb_nvk_loader.c" \
  -DVulkan_GLSLC_EXECUTABLE=/usr/bin/glslc -DSPIRV-Headers_DIR=/usr/share/cmake/SPIRV-Headers \
  -DPython3_EXECUTABLE=/usr/bin/python3 > "$B.configure.log" 2>&1 || { tail -40 "$B.configure.log"; exit 1; }
grep -E "supported by glslc|Vulkan found" "$B.configure.log" | head -20 || true
$NICE ninja -C "$B" -j"$JOBS" gb_nvk_loader gb_gpu_compute_lib gb_gpu_compute_launcher gc_probe \
  > "$B.build.log" 2>&1 || { grep -E "error|Error" "$B.build.log" | head -40; tail -20 "$B.build.log"; exit 1; }
rm -rf "$O"
mkdir -p "$O/lib64" "$O/unstripped" "$O/test"
cp "$B/libgb_nvk_loader.so" "$B/libgb_gpu_compute.so" "$B/gb_gpu_compute" "$O/unstripped/"
"$NDKBIN/llvm-strip" --strip-unneeded -o "$O/lib64/libgb_nvk_loader.so" "$B/libgb_nvk_loader.so"
"$NDKBIN/llvm-strip" --strip-unneeded -o "$O/lib64/libgb_gpu_compute.so" "$B/libgb_gpu_compute.so"
"$NDKBIN/llvm-strip" --strip-all -o "$O/gb_gpu_compute" "$B/gb_gpu_compute"
"$NDKBIN/llvm-strip" --strip-all -o "$O/test/gc_probe" "$B/gc_probe"
chmod 0644 "$O"/lib64/*.so
chmod 0755 "$O/gb_gpu_compute" "$O/test/gc_probe"
(cd "$O" && sha256sum gb_gpu_compute lib64/libgb_nvk_loader.so lib64/libgb_gpu_compute.so test/gc_probe) > "$O/SHA256SUMS"
echo "android build ok: $O"
cat "$O/SHA256SUMS"
ls -l "$O" "$O/lib64"
