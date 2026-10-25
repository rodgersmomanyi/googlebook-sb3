#!/bin/bash
# Builds the GbGfxDriver shim (builder). Run from the component scratch dir that holds shim/ test/ (copied from
# C:\GB-build\native\gfx-driver-pkg). Uses the NDK and the Mesa tree of nvk-android (headers + vk.xml, read only).
#   out/gen/gb_shim_tramp.inc            generated trampolines (vk.xml of the same Mesa tree as the headers)
#   out/android/vulkan.$HAL.so           the APK library (production: no test hooks)
#   out/android-test/vulkan.$HAL.so      bionic test variant (GB_SHIM_TEST_HOOKS) for the chroot test only
#   out/host/{vulkan.intel.so,fake_nvk.so,test_shim}   glibc unit-test build (GB_SHIM_TEST_HOOKS)
# HAL = ro.hardware.vulkan of the target (intel on lapis native and in the VMware VM, INTERFACES D12).
set -euo pipefail
# shellcheck source=/dev/null
. /build/native/nvk-android/scripts/00_env.sh
G=${G:-$(cd "$(dirname "$0")/.." && pwd)}
HAL=${HAL:-intel}
OUT=$G/out
mkdir -p "$OUT/gen" "$OUT/android" "$OUT/android-test" "$OUT/host"
python3 -I "$G/shim/gen_trampolines.py" "$MESA_SRC/src/vulkan/registry/vk.xml" "$OUT/gen/gb_shim_tramp.inc"
CF="-O2 -fPIC -fvisibility=hidden -std=gnu11 -Wall -Wextra -Werror -Wno-unused-parameter -ffile-prefix-map=$G=. \
    -I$OUT/gen -I$MESA_SRC/include"
ALD="-shared -Wl,-soname,vulkan.$HAL.so -Wl,-z,max-page-size=16384 -Wl,-z,relro -Wl,-z,now -Wl,--no-undefined \
     -Wl,--build-id=sha1"
CC_A=$TC/x86_64-linux-android${API}-clang
# shellcheck disable=SC2086
$CC_A $CF $ALD -o "$OUT/android/vulkan.$HAL.unstripped.so" "$G/shim/gb_shim.c" -ldl -llog
"$TC/llvm-strip" --strip-all -o "$OUT/android/vulkan.$HAL.so" "$OUT/android/vulkan.$HAL.unstripped.so"
# shellcheck disable=SC2086
$CC_A $CF -DGB_SHIM_TEST_HOOKS $ALD -o "$OUT/android-test/vulkan.$HAL.so" "$G/shim/gb_shim.c" -ldl -llog
# glibc unit-test build (system Vulkan headers would also do; use Mesa's for the same vk.xml version)
# shellcheck disable=SC2086
cc $CF -DGB_SHIM_TEST_HOOKS -shared -Wl,-soname,vulkan.intel.so -Wl,--no-undefined -o "$OUT/host/vulkan.intel.so" \
  "$G/shim/gb_shim.c" -ldl -lpthread
cc -O2 -fPIC -fvisibility=hidden -Wall -Wextra -Werror -Wno-unused-parameter -I"$MESA_SRC/include" -shared \
  -o "$OUT/host/fake_nvk.so" "$G/test/fake_icd.c"
cc -O2 -Wall -Wextra -Werror -Wno-unused-parameter -Wno-unused-result -I"$MESA_SRC/include" \
  -o "$OUT/host/test_shim" "$G/test/test_shim.c" -ldl
# bionic test helper: the same test program built for Android (run in the lapis chroot)
$CC_A -O2 -Wall -Wextra -Werror -Wno-unused-parameter -Wno-unused-result -I"$MESA_SRC/include" \
  -o "$OUT/android-test/test_shim" "$G/test/test_shim.c" -ldl
$CC_A -O2 -fPIC -fvisibility=hidden -Wall -Wextra -Werror -Wno-unused-parameter -I"$MESA_SRC/include" -shared \
  -o "$OUT/android-test/fake_nvk.so" "$G/test/fake_icd.c"
$CC_A -O2 -fPIC -fvisibility=hidden -Wall -Wextra -Werror -Wno-unused-parameter -I"$MESA_SRC/include" -shared \
  -DFAKE_VENDOR=0x8086 -DFAKE_NO_FAIL '-DFAKE_NAME="fake Intel Iris Plus (test ICD)"' -o "$OUT/android-test/fake_anv.so" \
  "$G/test/fake_icd.c"
f=$OUT/android/vulkan.$HAL.so
echo "--- $f"; "$TC/llvm-readelf" -d "$f" | grep -E 'NEEDED|SONAME'
"$TC/llvm-readelf" -lW "$f" | grep -E 'LOAD' | awk '{print "   LOAD align", $NF}'
echo "   exported: $("$TC/llvm-nm" -D --defined-only "$f" | awk '{print $3}' | tr '\n' ' ')"
grep -c '^static VKAPI_ATTR' "$OUT/gen/gb_shim_tramp.inc" | sed 's/^/trampolines generated: /'
sha256sum "$OUT"/android/*.so "$OUT/gen/gb_shim_tramp.inc"
