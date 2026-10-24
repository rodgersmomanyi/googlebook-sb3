#!/bin/bash
# Configure libcamera (Android HAL + simple pipeline + soft ISP + simple IPA) for the SB3 native build.
set -euo pipefail
W=/build/native/camera-userspace
B=${B:-$W/lc-build}
rm -rf $B
export PKG_CONFIG_LIBDIR=$W/sysroot/lib/pkgconfig
$W/venv/bin/meson setup $B $W/src/libcamera --cross-file $W/android-x86_64.ini \
  --prefix=/odm --libdir=lib64 --libexecdir=bin --sysconfdir=etc --datadir=etc \
  --buildtype=release -Db_ndebug=false -Dstrip=false \
  -Dandroid=enabled -Dandroid_platform=generic \
  -Dpipelines=simple -Dipas=simple -Dsoftisp-gpu=disabled \
  -Dcam=disabled -Dqcam=disabled -Dgstreamer=disabled -Dpycamera=disabled \
  -Dlc-compliance=disabled -Dtracing=disabled -Dudev=disabled -Ddocumentation=disabled \
  -Dtest=false -Dv4l2=false -Dlibdw=disabled -Dlibunwind=disabled -Dapps-output-dng=disabled \
  -Drpi-awb-nn=disabled "$@"
