#!/bin/bash
set -e
exec > /build/cvd_fetch.log 2>&1
cd /build
echo "=== get android-cuttlefish base deb ==="
URL=$(curl -sL https://api.github.com/repos/google/android-cuttlefish/releases/latest | grep browser_download_url | grep -E "cuttlefish-base_.*_amd64.deb" | head -1 | cut -d'"' -f4)
echo "deb: $URL"
wget -q "$URL" -O /build/cf-base.deb
rm -rf /build/cvdx && mkdir -p /build/cvdx && dpkg-deb -x /build/cf-base.deb /build/cvdx
echo "=== cvd/fetch binaries ==="
find /build/cvdx -type f \( -name "cvd" -o -name "fetch_cvd" \) 2>/dev/null
FETCH=$(find /build/cvdx -type f -name "fetch_cvd" 2>/dev/null | head -1)
CVD=$(find /build/cvdx -type f -name "cvd" 2>/dev/null | head -1)
rm -rf /build/cf && mkdir -p /build/cf && cd /build/cf
echo "=== fetch aosp_cf_x86_64 (anonymous) ==="
if [ -n "$FETCH" ]; then
  "$FETCH" -default_build=aosp-android-latest-release/aosp_cf_x86_64_only_phone-userdebug -directory=/build/cf || \
  "$FETCH" --default_build=aosp-android-latest-release/aosp_cf_x86_64_only_phone-userdebug --target_directory=/build/cf
else
  "$CVD" fetch --default_build=aosp-android-latest-release/aosp_cf_x86_64_only_phone-userdebug --target_directory=/build/cf
fi
echo "=== look for vulkan.pastel / super.img ==="
find /build/cf -iname "vulkan.pastel.so" -o -iname "super.img" -o -iname "vendor.img" -o -iname "*.img" 2>/dev/null | head -20
echo "CVD_FETCH_DONE"
