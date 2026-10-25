#!/bin/bash
# Private header set used by BOTH builds (host test build and NDK build), so the Android build never picks up the
# NDK sysroot's Vulkan headers or anything else from /usr/include:
#   vulkan/, vk_video/  Ubuntu libvulkan-dev 1.3.275 (Vulkan-Headers incl. vulkan.hpp, needed by ggml-vulkan)
#   spirv/unified1/     Ubuntu spirv-headers (ggml-vulkan parses SPIR-V with spirv.hpp)
set -euo pipefail
. "$(dirname "$0")/00_env.sh"
rm -rf "$INC.tmp"
mkdir -p "$INC.tmp/spirv"
cp -a /usr/include/vulkan /usr/include/vk_video "$INC.tmp/"
cp -a /usr/include/spirv/unified1 "$INC.tmp/spirv/"
dpkg-query -W -f '${Package} ${Version}\n' libvulkan-dev spirv-headers > "$INC.tmp/SOURCES"
(cd "$INC.tmp" && find . -type f ! -name MANIFEST.sha256 | LC_ALL=C sort | xargs sha256sum) > "$INC.tmp/MANIFEST.sha256"
rm -rf "$INC"
mv "$INC.tmp" "$INC"
echo "headers: $(wc -l < "$INC/MANIFEST.sha256") files; $(tr '\n' ';' < "$INC/SOURCES")"
grep -m1 "define VK_HEADER_VERSION " "$INC/vulkan/vulkan_core.h"
