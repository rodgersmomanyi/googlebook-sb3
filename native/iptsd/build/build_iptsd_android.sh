#!/bin/bash
# Reproducible build of iptsd v3.1.1 for Android x86_64 (Googlebook native / Surface Book 3), fully static
# (static bionic + libc++, every dependency from meson wraps: CLI11 2.7.2, Eigen 5.0.1, fmt 12.0.0, inih r62,
# Microsoft GSL 4.2.2, spdlog 1.17.0). Run on the builder:
#   bash build_iptsd_android.sh [WORKDIR]      (default /build/native/iptsd/repro)
# Needs: git, meson >= 1.0, ninja, python3, internet (wrap downloads), the NDK at $NDK (default /build/ndk, r27c).
# Output: WORKDIR/out/bin/{iptsd,iptsd-check-device,iptsd-dump,iptsd-perf,iptsd-calibrate} (stripped).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
W=${1:-/build/native/iptsd/repro}
NDK=${NDK:-/build/ndk}
TAG=v3.1.1
PATCH=${PATCH:-$HERE/../patches/0001-iptsd-android-ndk-build.patch}
CROSS=${CROSS:-$HERE/android-x86_64-ndk.ini}
JOBS=${JOBS:-2}

mkdir -p "$W"
[ -d "$W/src/.git" ] || git clone -q https://github.com/linux-surface/iptsd.git "$W/src"
cd "$W/src"
git checkout -q "$TAG"
git checkout -q -- .
git apply "$PATCH"
# the cross file hardcodes /build/ndk; follow $NDK
sed "s#/build/ndk#$NDK#g" "$CROSS" > "$W/cross.ini"
rm -rf "$W/build"
meson setup "$W/build" --cross-file "$W/cross.ini" --wrap-mode=forcefallback --prefix=/odm \
	-Dwerror=false -Dservice_manager=[] -Dsample_config=false -Ddebug_tools=calibrate,dump,perf \
	-Daccess_checks=false \
	-Dpreset_dir=/odm/etc/iptsd/presets -Dconfig_dir=/odm/etc/iptsd/iptsd.d -Dconfig_file=/odm/etc/iptsd/iptsd.conf
nice -n 15 ninja -C "$W/build" -j"$JOBS" src/iptsd src/iptsd-check-device src/iptsd-dump src/iptsd-perf src/iptsd-calibrate
mkdir -p "$W/out/bin"
for b in iptsd iptsd-check-device iptsd-dump iptsd-perf iptsd-calibrate; do
	"$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip" -o "$W/out/bin/$b" "$W/build/src/$b"
done
file "$W/out/bin/iptsd"
"$W/out/bin/iptsd" --help > /dev/null && echo "iptsd --help ok"
strings "$W/out/bin/iptsd" | grep '^/odm/etc/iptsd'
sha256sum "$W/out/bin/"*
