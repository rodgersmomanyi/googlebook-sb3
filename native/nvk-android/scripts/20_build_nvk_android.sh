#!/bin/bash
# Step 2: cross-build Mesa NVK (vulkan-drivers=nouveau) for Android x86_64 with the NDK.
# Needs: 10_build_host_clc.sh done (host mesa_clc/vtn_bindgen2), rustup toolchain with target x86_64-linux-android,
# bindgen-cli >= 0.71.1 (not 0.72.0/0.73.0/0.73.1), cbindgen >= 0.28, meson >= 1.4 (we use 1.12.1).
# Output: $W/stage/vulkan.nouveau.so (SONAME vulkan.nouveau.so, exports HMI + vk_icd*), stripped.
set -euo pipefail
. "$(dirname "$0")/00_env.sh"
cd "$W"
rustc --version; bindgen --version; cbindgen --version; meson --version; command -v mesa_clc vtn_bindgen2

CROSS=$W/x86_64-android-nvk.ini
cat > "$CROSS" <<EOF
[constants]
ndk = '$NDK/toolchains/llvm/prebuilt/linux-x86_64'

[binaries]
ar = ndk / 'bin/llvm-ar'
c = [ndk / 'bin/x86_64-linux-android${API}-clang', '-fno-exceptions', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables']
cpp = [ndk / 'bin/x86_64-linux-android${API}-clang++', '-fno-exceptions', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '--start-no-unused-arguments', '-static-libstdc++', '--end-no-unused-arguments']
c_ld = 'lld'
cpp_ld = 'lld'
strip = ndk / 'bin/llvm-strip'
rust = ['rustc', '--target', 'x86_64-linux-android', '-C', 'linker=' + ndk / 'bin/x86_64-linux-android${API}-clang']
pkg-config = ['/usr/bin/pkgconf']

[properties]
needs_exe_wrapper = true
pkg_config_libdir = '$W/sys/lib/pkgconfig'
bindgen_clang_arguments = ['-target', 'x86_64-linux-android${API}', '--sysroot', ndk / 'sysroot']

[host_machine]
system = 'android'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
EOF

echo "=== libdrm (core only, static) -> $W/sys"
if [ ! -f "$W/sys/lib/libdrm.a" ]; then
  [ -d "$W/src/libdrm" ] || git clone -q --depth 1 https://gitlab.freedesktop.org/mesa/libdrm.git "$W/src/libdrm"
  rm -rf "$W/b-drm"
  meson setup "$W/b-drm" "$W/src/libdrm" --cross-file "$CROSS" --prefix="$W/sys" --libdir=lib --default-library=static \
    -Dintel=disabled -Dradeon=disabled -Damdgpu=disabled -Dnouveau=disabled -Dvc4=disabled -Dvmwgfx=disabled \
    -Detnaviv=disabled -Dexynos=disabled -Domap=disabled -Dtegra=disabled -Dudev=false \
    -Dcairo-tests=disabled -Dvalgrind=disabled -Dtests=false -Dman-pages=disabled -Dinstall-test-programs=false
  ninja -C "$W/b-drm" install >/dev/null
  rm -rf "$W/b-drm"
fi

echo "=== mesa ($MESA_TAG) local patches"
mkdir -p "$W/patches"
python3 -I "$(dirname "$0")/apply_patches.py" "$MESA_SRC" "$W/patches"

echo "=== mesa ($MESA_TAG) configure"
B=$W/b-nvk
rm -rf "$B"
meson setup "$B" "$MESA_SRC" --cross-file "$CROSS" --prefix="$W/out" --libdir=lib \
  --buildtype=release -Db_ndebug=true \
  -Dplatforms=android -Dandroid-stub=true -Dplatform-sdk-version=$API -Dandroid-libbacktrace=disabled \
  -Dvulkan-drivers=nouveau -Dgallium-drivers= -Dopengl=false -Degl=disabled -Dgles1=disabled -Dgles2=disabled \
  -Dglx=disabled -Dgbm=disabled -Dglvnd=disabled -Dllvm=disabled -Dmesa-clc=system \
  -Dxmlconfig=disabled -Dzstd=disabled -Dshader-cache=disabled -Dvalgrind=disabled -Dlibunwind=disabled \
  -Dlmsensors=disabled -Dvideo-codecs= -Dperfetto=false -Dtools= -Dbuild-tests=false -Dvulkan-layers= \
  -Dspirv-tools=disabled
echo "=== mesa build (-j$JOBS)"
nice -n 5 ninja -C "$B" -j "$JOBS" src/nouveau/vulkan/libvulkan_nouveau.so

echo "=== stage"
mkdir -p "$W/stage"
# Relink the final .so with the Android HAL SONAME instead of patchelf'ing it: patchelf 0.18 moves .dynstr into a new
# RW PT_LOAD, which is legal but needlessly unusual for bionic's loader. Same objects/flags, only -o and -soname change.
( cd "$B" && cmd=$(ninja -t commands src/nouveau/vulkan/libvulkan_nouveau.so | tail -1 |
    sed -e 's#-o src/nouveau/vulkan/libvulkan_nouveau.so #-o src/nouveau/vulkan/vulkan.nouveau.so #'         -e 's#-Wl,-soname,libvulkan_nouveau.so #-Wl,-soname,vulkan.nouveau.so #') &&
  case "$cmd" in *"-soname,vulkan.nouveau.so "*"") ;; *) echo "relink edit failed"; exit 1;; esac &&
  eval "$cmd" )
SO=$B/src/nouveau/vulkan/vulkan.nouveau.so
cp "$SO" "$W/stage/vulkan.nouveau.unstripped.so"
"$TC/llvm-strip" --strip-all -o "$W/stage/vulkan.nouveau.so" "$SO"
"$TC/llvm-readelf" -d "$W/stage/vulkan.nouveau.so" | grep -E 'NEEDED|SONAME'
"$TC/llvm-nm" -D --defined-only "$W/stage/vulkan.nouveau.so" | grep -E ' (HMI|vk_icd)' || true
ls -la "$W/stage"
echo "=== done $(date -Is)"
