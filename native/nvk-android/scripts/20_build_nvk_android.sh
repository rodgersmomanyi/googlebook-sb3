#!/bin/bash
# Step 2: cross-build Mesa NVK (vulkan-drivers=nouveau) for Android x86_64 with the NDK.
# Needs: 10_build_host_clc.sh done (host mesa_clc/vtn_bindgen2), rustup toolchain with target x86_64-linux-android,
# bindgen-cli >= 0.71.1 (not 0.72.0/0.73.0/0.73.1), cbindgen >= 0.28, meson >= 1.4 (we use 1.12.1).
# Profile: PATCHSET=base|8a|8b (00_env.sh). Output in $STAGE (default $W/stage-$PATCHSET):
#   vulkan.nouveau.so (SONAME vulkan.nouveau.so, exports HMI + vk_icd*), stripped, + .unstripped.so
#   libgb_nvk.so      (8a/8b only: the same objects relinked with SONAME libgb_nvk.so, for the GbGfxDriver APK)
# Run it at nice 19 / ionice idle on the shared builder (the callers in run_*.sh do).
set -euo pipefail
. "$(dirname "$0")/00_env.sh"
cd "$W"
echo "=== profile PATCHSET=$PATCHSET platform-sdk-version=$PLATFORM_SDK shader-cache=$SHADER_CACHE B=$B STAGE=$STAGE"
rustc --version; bindgen --version; cbindgen --version; meson --version; command -v mesa_clc vtn_bindgen2

# pkg-config search dir: base keeps the 2026-10-24 one (libdrm only; zlib is then found as the NDK's libz.so by meson's
# system lookup and dropped by --as-needed because nothing uses it). With the shader cache the disk cache does use
# zlib, which would add NEEDED libz.so (not LLNDK on lapis); the other profiles get a private dir whose zlib.pc points
# at the NDK's static libz.a instead.
PCDIR=$W/sys/lib/pkgconfig
CROSS=$W/x86_64-android-nvk.ini
if [ "$PATCHSET" != base ]; then
  PCDIR=$W/pc-$PATCHSET
  CROSS=$W/x86_64-android-nvk-$PATCHSET.ini
  rm -rf "$PCDIR"; mkdir -p "$PCDIR"
  cp "$W/sys/lib/pkgconfig/"*.pc "$PCDIR/" 2>/dev/null || true
  ZV=$(sed -n 's/^#define ZLIB_VERSION "\(.*\)"/\1/p' "$SYSROOT/usr/include/zlib.h")
  cat > "$PCDIR/zlib.pc" <<EOF
libdir=$SYSROOT/usr/lib/x86_64-linux-android
includedir=$SYSROOT/usr/include
Name: zlib
Description: NDK zlib, linked statically into the ICD (keeps NEEDED within LLNDK)
Version: $ZV
Libs: \${libdir}/libz.a
Cflags:
EOF
fi
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
pkg_config_libdir = '$PCDIR'
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

echo "=== mesa ($MESA_TAG) local patches, set $PATCHSET"
mkdir -p "$W/patches"
python3 -I "$(dirname "$0")/apply_patches.py" --set "$PATCHSET" "$MESA_SRC" "$W/patches"

echo "=== mesa ($MESA_TAG) configure"
rm -rf "$B"
meson setup "$B" "$MESA_SRC" --cross-file "$CROSS" --prefix="$W/out" --libdir=lib \
  --buildtype=release -Db_ndebug=true \
  -Dplatforms=android -Dandroid-stub=true -Dplatform-sdk-version=$PLATFORM_SDK -Dandroid-libbacktrace=disabled \
  -Dvulkan-drivers=nouveau -Dgallium-drivers= -Dopengl=false -Degl=disabled -Dgles1=disabled -Dgles2=disabled \
  -Dglx=disabled -Dgbm=disabled -Dglvnd=disabled -Dllvm=disabled -Dmesa-clc=system \
  -Dxmlconfig=disabled -Dzstd=disabled -Dshader-cache=$SHADER_CACHE -Dvalgrind=disabled -Dlibunwind=disabled \
  -Dlmsensors=disabled -Dvideo-codecs= -Dperfetto=false -Dtools= -Dbuild-tests=false -Dvulkan-layers= \
  -Dspirv-tools=disabled
echo "=== mesa build (-j$JOBS)"
nice -n 5 ninja -C "$B" -j "$JOBS" src/nouveau/vulkan/libvulkan_nouveau.so

echo "=== stage"
mkdir -p "$STAGE"
# Relink the final .so with the wanted SONAME instead of patchelf'ing it: patchelf 0.18 moves .dynstr into a new RW
# PT_LOAD, which is legal but needlessly unusual for bionic's loader. Same objects/flags, only -o and -soname change
# (8a/8b also get -z max-page-size=16384: 16 KiB-aligned segments, required for libraries loaded straight from an
# APK on 16 KiB-page devices and harmless on 4 KiB x86_64; the base profile keeps the 2026-10-24 link line).
LDX=""
[ "$PATCHSET" = base ] || LDX="-Wl,-z,max-page-size=16384 "
relink() {   # relink <soname>
  ( cd "$B" && cmd=$(ninja -t commands src/nouveau/vulkan/libvulkan_nouveau.so | tail -1 |
      sed -e "s#-o src/nouveau/vulkan/libvulkan_nouveau.so #-o src/nouveau/vulkan/$1 $LDX#" \
          -e "s#-Wl,-soname,libvulkan_nouveau.so #-Wl,-soname,$1 #") &&
    case "$cmd" in *"-soname,$1 "*) ;; *) echo "relink edit failed for $1"; exit 1;; esac &&
    eval "$cmd" )
}
relink vulkan.nouveau.so
SO=$B/src/nouveau/vulkan/vulkan.nouveau.so
cp "$SO" "$STAGE/vulkan.nouveau.unstripped.so"
"$TC/llvm-strip" --strip-all -o "$STAGE/vulkan.nouveau.so" "$SO"
if [ "$PATCHSET" != base ]; then
  relink libgb_nvk.so
  "$TC/llvm-strip" --strip-all -o "$STAGE/libgb_nvk.so" "$B/src/nouveau/vulkan/libgb_nvk.so"
fi
for f in "$STAGE"/vulkan.nouveau.so "$STAGE"/libgb_nvk.so; do
  [ -f "$f" ] || continue
  echo "--- $f"; "$TC/llvm-readelf" -d "$f" | grep -E 'NEEDED|SONAME'
done
"$TC/llvm-nm" -D --defined-only "$STAGE/vulkan.nouveau.so" | grep -E ' (HMI|vk_icd)' || true
ls -la "$STAGE"
echo "=== done $(date -Is)"
