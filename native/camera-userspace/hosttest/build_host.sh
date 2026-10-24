#!/bin/bash
# TEST ONLY: host (glibc) build of the patched libcamera + Android HAL, a fake minigbm-layout
# gralloc (libhardware.so) and the camera3 HAL driver hal_test, to run against vimc.
set -euo pipefail
W=/build/native/camera-userspace
H=$W/hosttest
J=${J:-2}
mkdir -p $H/fakehw/pc $H/out

# Source copy with the test-only soft IPA patch
rm -rf $W/src/libcamera-host
git clone -q -b sb3-android $W/src/libcamera $W/src/libcamera-host
python3 $W/patchers/ptest_vimc_softipa.py $W/src/libcamera-host

INC="-I$W/src/libcamera-host/include/android/hardware/libhardware/include \
 -I$W/src/libcamera-host/include/android/system/core/include \
 -I$W/src/libcamera-host/include/android/metadata"

gcc -O2 -g -shared -fPIC $INC -o $H/fakehw/libhardware.so $H/fake_hw.c -ldl
cat > $H/fakehw/pc/libhardware.pc <<PC
Name: libhardware
Description: fake minigbm-layout gralloc (host test)
Version: 1.0
Libs: -L$H/fakehw -lhardware -Wl,-rpath,$H/fakehw
Cflags:
PC

export PKG_CONFIG_PATH=$H/fakehw/pc
rm -rf $W/host-build
$W/venv/bin/meson setup $W/host-build $W/src/libcamera-host --buildtype=debugoptimized \
  -Dandroid=enabled -Dandroid_platform=generic -Dpipelines=simple -Dipas=simple \
  -Dsoftisp-gpu=disabled -Dcam=enabled -Dqcam=disabled -Dgstreamer=disabled -Dpycamera=disabled \
  -Dlc-compliance=disabled -Dtracing=disabled -Dudev=disabled -Ddocumentation=disabled \
  -Dtest=false -Dv4l2=false -Dlibdw=disabled -Dlibunwind=disabled > $H/host-configure.log 2>&1
nice ninja -C $W/host-build -j$J > $H/host-build.log 2>&1

gcc -O1 -g -c $INC -o $H/camera_metadata.o $W/src/libcamera-host/src/android/metadata/camera_metadata.c
g++ -O1 -g -std=c++17 $INC -o $H/hal_test $H/hal_test.cpp $H/camera_metadata.o \
  -ldl -L$H/fakehw -lhardware -Wl,-rpath,$H/fakehw -lpthread
echo HOST_BUILD_OK
