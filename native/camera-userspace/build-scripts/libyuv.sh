#!/bin/bash
# Static libyuv for Android x86_64 (same revision as libcamera's subprojects/libyuv.wrap).
set -euo pipefail
W=/build/native/camera-userspace
NDK=/build/ndk
SR=$W/sysroot
REV=500f45652c459cfccd20f83f297eb66cb7b015cb
cd $W/deps
if [ ! -d libyuv/.git ]; then
  rm -rf libyuv && mkdir libyuv && cd libyuv && git init -q && \
  git remote add origin https://chromium.googlesource.com/libyuv/libyuv.git && \
  git fetch -q --depth 1 origin $REV && git checkout -q FETCH_HEAD && cd ..
fi
rm -rf yuv-b
cmake -S libyuv -B yuv-b -G "Unix Makefiles" \
  -DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=x86_64 -DANDROID_PLATFORM=android-34 -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DJPEG_FOUND=OFF -DCMAKE_DISABLE_FIND_PACKAGE_JPEG=ON \
  -DCMAKE_CXX_FLAGS="-Wno-sign-compare -Wno-unused-variable -Wno-unused-parameter" >/dev/null
make -C yuv-b -j${J:-2} yuv >/dev/null
cp -f yuv-b/libyuv.a $SR/lib/libyuv.a
rm -rf $SR/include/libyuv $SR/include/libyuv.h
cp -r libyuv/include/libyuv libyuv/include/libyuv.h $SR/include/
cat > $SR/lib/pkgconfig/libyuv.pc <<PC
prefix=$SR
Name: libyuv
Description: libyuv (static, $REV)
Version: 0.0.1
Libs: -L\${prefix}/lib -lyuv
Cflags: -I\${prefix}/include
PC
ls -la $SR/lib/libyuv.a
echo LIBYUV_OK
