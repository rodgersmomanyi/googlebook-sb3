#!/bin/bash
# Build SwiftShader (CPU EGL/GLES + Vulkan) for the Android x86_64 target.
set -e
exec > /build/ss_build.log 2>&1
echo "=== $(date) install tools ==="
sudo apt-get update -y
sudo apt-get install -y cmake ninja-build unzip wget git
cd /build
echo "=== NDK ==="
if [ ! -d /build/ndk ]; then
  wget -q https://dl.google.com/android/repository/android-ndk-r27c-linux.zip -O /build/ndk.zip
  unzip -q /build/ndk.zip -d /build
  mv /build/android-ndk-r27c /build/ndk
  rm -f /build/ndk.zip
fi
echo "ndk: $(ls /build/ndk/build/cmake/android.toolchain.cmake)"
echo "=== clone swiftshader ==="
if [ ! -d /build/swiftshader ]; then
  git clone https://github.com/google/swiftshader.git /build/swiftshader
fi
cd /build/swiftshader
git submodule update --init --recursive
echo "=== configure (Android x86_64) ==="
rm -rf /build/swiftshader/build-x64
cmake -S /build/swiftshader -B /build/swiftshader/build-x64 -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE=/build/ndk/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=x86_64 -DANDROID_PLATFORM=30 \
  -DSWIFTSHADER_BUILD_TESTS=OFF -DCMAKE_BUILD_TYPE=Release
echo "=== build ==="
ninja -C /build/swiftshader/build-x64
echo "=== SS_BUILD_DONE; artifacts ==="
find /build/swiftshader/build-x64 -name "libEGL_swiftshader.so" -o -name "libGLESv2_swiftshader.so" -o -name "libGLESv1_CM_swiftshader.so" -o -name "libvk_swiftshader.so" 2>/dev/null
echo "EXIT_OK"
