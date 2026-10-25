#!/bin/bash
# Step 1: build the HOST tools mesa_clc + vtn_bindgen2 from the same Mesa tree.
# NVK compiles its internal OpenCL-C kernels (src/nouveau/vulkan/cl/*.cl) at build time with mesa_clc, so a
# cross build (-Dmesa-clc=system) needs a native mesa_clc from the *same* Mesa version (see docs/android.rst).
# Host deps (apt, root fs): llvm-18-dev libclang-18-dev libclang-cpp18-dev libllvmspirvlib-18-dev spirv-tools
#                           libpolly-18-dev zlib1g-dev libzstd-dev libexpat1-dev glslang-tools
set -euo pipefail
. "$(dirname "$0")/00_env.sh"
B=$W/b-host-clc
rm -rf "$B"
CC=gcc CXX=g++ meson setup "$B" "$MESA_SRC" \
  -Dprefix="$HOST_CLC" -Dbuildtype=release -Dstrip=true \
  -Dplatforms= -Dgallium-drivers= -Dvulkan-drivers= -Degl=disabled -Dgles1=disabled -Dgles2=disabled \
  -Dopengl=false -Dglx=disabled -Dgbm=disabled -Dllvm=enabled -Dshared-llvm=enabled \
  -Dmesa-clc=enabled -Dinstall-mesa-clc=true -Dmesa-clc-bundle-headers=enabled \
  -Dxmlconfig=disabled -Dvalgrind=disabled -Dlibunwind=disabled -Dlmsensors=disabled -Dzstd=disabled \
  -Dvideo-codecs= -Dtools= -Dbuild-tests=false -Dperfetto=false
nice -n 10 meson compile -C "$B" -j "$JOBS" src/compiler/clc/mesa_clc src/compiler/spirv/vtn_bindgen2
mkdir -p "$HOST_CLC/bin"
install -m755 "$B/src/compiler/clc/mesa_clc" "$B/src/compiler/spirv/vtn_bindgen2" "$HOST_CLC/bin/"
"$HOST_CLC/bin/mesa_clc" --help >/dev/null 2>&1 || true
ls -la "$HOST_CLC/bin"
rm -rf "$B"          # ~0.5 GB of objects, not needed any more
