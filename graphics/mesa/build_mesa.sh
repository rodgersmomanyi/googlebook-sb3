#!/bin/bash
# Build Mesa's gallium "svga" (VMware SVGA3D) EGL/GLES driver for Android x86_64 with the NDK, plus egl_probe.
# Runs on builder2 under /build/mesa-android; progress in build.log, result marker build.done / build.failed.
set -uo pipefail
W=/build/mesa-android
mkdir -p "$W" && cd "$W"
exec > >(tee -a "$W/build.log") 2>&1
rm -f build.done build.failed
fail() { echo "BUILD_FAILED: $*"; touch "$W/build.failed"; exit 1; }
echo "=== start $(date -Is)"

NDK=/build/ndk
TC=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin
API=35
[ -x "$TC/x86_64-linux-android${API}-clang" ] || fail "NDK clang for API $API missing in $TC"

echo "=== deps"
sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq || true
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git meson ninja-build pkgconf python3-mako python3-yaml \
  python3-packaging python3-ply python3-venv bison flex patchelf build-essential >/dev/null || fail "apt install"
# Mesa 26.x needs meson >= 1.4 (Ubuntu 24.04 ships 1.3.2): use a venv with current meson + the codegen modules.
[ -x "$W/venv/bin/meson" ] || { python3 -m venv "$W/venv" && "$W/venv/bin/pip" install -q meson mako pyyaml packaging ply; } \
  || fail "venv meson"
export PATH="$W/venv/bin:$PATH"
meson --version

cat > "$W/x86_64-android.ini" <<EOF
[binaries]
ar = '$TC/llvm-ar'
c = ['$TC/x86_64-linux-android${API}-clang', '-fno-exceptions', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables']
cpp = ['$TC/x86_64-linux-android${API}-clang++', '-fno-exceptions', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '--start-no-unused-arguments', '-static-libstdc++', '--end-no-unused-arguments']
c_ld = 'lld'
cpp_ld = 'lld'
strip = '$TC/llvm-strip'
pkg-config = ['/usr/bin/pkgconf']

[host_machine]
system = 'android'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'

[properties]
needs_exe_wrapper = true
pkg_config_libdir = '$W/sys/lib/pkgconfig'
EOF

echo "=== libdrm (static)"
[ -d libdrm ] || git clone -q --depth 1 https://gitlab.freedesktop.org/mesa/libdrm.git || fail "clone libdrm"
rm -rf drm-b
meson setup drm-b libdrm --cross-file x86_64-android.ini --prefix="$W/sys" --libdir=lib --default-library=static \
  -Dintel=disabled -Dradeon=disabled -Damdgpu=disabled -Dnouveau=disabled -Dvc4=disabled \
  -Detnaviv=disabled -Dexynos=disabled -Domap=disabled -Dtegra=disabled -Dvmwgfx=enabled -Dudev=false \
  -Dcairo-tests=disabled -Dvalgrind=disabled -Dtests=false -Dman-pages=disabled -Dinstall-test-programs=false \
  || fail "libdrm configure"
ninja -C drm-b install >/dev/null || fail "libdrm build"

echo "=== mesa source"
# 26.3.0-rc2 is the first tag carrying both svga dma-buf import fixes that gralloc buffers need:
# b460cff22a5 "svga: Pass buffer handle for fd handles for resource_from_handle()" and
# 357f02d2 "svga: Remove erroneous else-if in surface_get_handle()" (they don't cherry-pick onto 26.2.x).
TAG=mesa-26.3.0-rc2
if [ ! -d mesa ]; then git clone -q --filter=blob:none https://gitlab.freedesktop.org/mesa/mesa.git || fail "clone mesa"; fi
cd mesa
git cherry-pick --abort >/dev/null 2>&1 || true
git fetch -q origin "refs/tags/$TAG:refs/tags/$TAG" || true
git checkout -q -f "$TAG" || fail "checkout $TAG"
for c in b460cff22a5 357f02d2; do
  git merge-base --is-ancestor "$c" HEAD && echo "fix $c present in $TAG" || fail "fix $c missing from $TAG"
done
git log -1 --format='mesa HEAD %h %s'
cd "$W"

echo "=== mesa configure"
rm -rf mesa-b
meson setup mesa-b mesa --cross-file x86_64-android.ini --prefix="$W/out" --libdir=lib --buildtype=release -Db_ndebug=true \
  -Dplatforms=android -Dandroid-stub=true -Dplatform-sdk-version=$API -Dandroid-libbacktrace=disabled \
  -Dgallium-drivers=svga -Dvulkan-drivers= -Dllvm=disabled -Degl=enabled -Dgles1=enabled -Dgles2=enabled \
  -Dglx=disabled -Dgbm=disabled -Dglvnd=disabled -Degl-lib-suffix=_mesa -Dgles-lib-suffix=_mesa \
  -Dxmlconfig=disabled -Dzstd=disabled -Dshader-cache=disabled -Dvalgrind=disabled -Dlibunwind=disabled \
  -Dlmsensors=disabled -Dgallium-va=disabled -Dvideo-codecs= -Dgallium-rusticl=false -Dperfetto=false -Dtools= \
  -Dbuild-tests=false -Dcpp_rtti=false || fail "mesa configure"

echo "=== mesa build ($(nproc) jobs)"
ninja -C mesa-b || fail "mesa build"
ninja -C mesa-b install >/dev/null || fail "mesa install"

echo "=== egl_probe"
"$TC/x86_64-linux-android${API}-clang" -O2 -Wall -o "$W/egl_probe" /build/mesa-android/egl_probe.c -ldl -lnativewindow -lEGL \
  || fail "egl_probe build"

echo "=== stage"
rm -rf stage && mkdir -p stage
find out mesa-b -name 'libEGL_mesa.so' -o -name 'libGLESv1_CM_mesa.so' -o -name 'libGLESv2_mesa.so' \
     -o -name 'libgallium*.so' -o -name '*_dri.so' | while read -r f; do [ -f "$f" ] && cp -n "$f" stage/; done
cp egl_probe stage/
for f in stage/*; do
  echo "--- $f ($(stat -c %s "$f") bytes)"
  "$TC/llvm-readelf" -d "$f" 2>/dev/null | grep -E 'NEEDED|SONAME|RUNPATH' || true
done
echo "=== done $(date -Is)"
touch "$W/build.done"
