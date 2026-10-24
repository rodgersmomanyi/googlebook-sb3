#!/bin/bash
# Build static libjpeg-turbo + libexif for Android x86_64 (NDK r27c, API 34) and set up a
# link sysroot with the lapis vendor BoringSSL libcrypto.so (link stub only, not shipped).
set -euo pipefail
W=/build/native/camera-userspace
NDK=/build/ndk
API=34
TC=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin
SR=$W/sysroot
J=${J:-2}
mkdir -p $SR/lib/pkgconfig $SR/include $W/deps
cd $W/deps

echo "=== libjpeg-turbo"
rm -rf libjpeg-turbo-3.1.2 jt-b
tar -xzf $W/dl/libjpeg-turbo-3.1.2.tar.gz
cmake -S libjpeg-turbo-3.1.2 -B jt-b -G "Unix Makefiles" \
  -DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=x86_64 -DANDROID_PLATFORM=android-$API \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=$SR -DCMAKE_INSTALL_LIBDIR=lib \
  -DENABLE_SHARED=OFF -DENABLE_STATIC=ON -DWITH_TURBOJPEG=OFF -DWITH_JPEG8=OFF \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON -DWITH_SIMD=ON -DCMAKE_ASM_NASM_COMPILER=/usr/bin/nasm >/dev/null
make -C jt-b -j$J >/dev/null
make -C jt-b install >/dev/null
grep -E "JPEG_LIB_VERSION" $SR/include/jconfig.h

echo "=== libexif"
rm -rf libexif-0.6.25
tar -xjf $W/dl/libexif-0.6.25.tar.bz2
( cd libexif-0.6.25 && \
  CC="$TC/x86_64-linux-android$API-clang" AR=$TC/llvm-ar RANLIB=$TC/llvm-ranlib CFLAGS="-O2 -fPIC" \
  ./configure --host=x86_64-linux-android --prefix=$SR --libdir=$SR/lib --disable-shared --enable-static \
     --with-pic --disable-nls --disable-docs >/dev/null && make -j$J >/dev/null && make install >/dev/null )

echo "=== libcrypto link stub (lapis vendor BoringSSL) + headers"
cp -f /tmp/vmnt/lib64/libcrypto.so $SR/lib/libcrypto.so
rm -rf $SR/include/openssl && cp -r $W/dl/boringssl-include/openssl $SR/include/openssl
cat > $SR/lib/pkgconfig/libcrypto.pc <<PC
prefix=$SR
libdir=\${prefix}/lib
includedir=\${prefix}/include
Name: libcrypto
Description: lapis vendor BoringSSL (link stub)
Version: 3.0.0
Libs: -L\${libdir} -lcrypto
Cflags: -I\${includedir}
PC
ls -la $SR/lib $SR/lib/pkgconfig
echo DEPS_OK
