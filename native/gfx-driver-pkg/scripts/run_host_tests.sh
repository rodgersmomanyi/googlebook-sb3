#!/bin/bash
# Host part of the v1.2 shim tests (builder; 1b\TASKS_1B.md 3.2): GD-1B-01..08 against lavapipe (plays the platform
# driver "ANV") + the fake NVK ICD (vendor 0x10de), plain glibc build and ASan/UBSan build (GD-1B-10), plus the shim
# reproducibility check of GD-1B-12 (two NDK builds of vulkan.intel.so give the same bytes).
# Prerequisite: scripts/build_shim.sh ran (out/gen/gb_shim_tramp.inc, out/host/*). Run with nice -n 19 ionice -c3.
# Output: logs/gd1b_host.log, logs/gd1b_host_asan.log, logs/gd1b_repro.log; one summary line per step on stdout.
set -uo pipefail
# shellcheck source=/dev/null
. /build/native/nvk-android/scripts/00_env.sh
G=${G:-$(cd "$(dirname "$0")/.." && pwd)}
LVP=${LVP:-/usr/lib/x86_64-linux-gnu/libvulkan_lvp.so}
OUT=$G/out
A=$OUT/asan
L=$G/logs
mkdir -p "$A" "$L"
rc=0

echo "### GD-1B-01..08 host (glibc, lavapipe as the platform driver, fake NVK)"
( cd "$OUT/host" && ./test_shim ./vulkan.intel.so "$LVP" ./fake_nvk.so ) > "$L/gd1b_host.log" 2>&1
r=$?; echo "rc=$r $(grep '^RESULT' "$L/gd1b_host.log")"; [ $r = 0 ] || rc=1

echo "### GD-1B-10 host ASan + UBSan"
SAN="-O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=all"
W="-Wall -Wextra -Werror -Wno-unused-parameter"
# shellcheck disable=SC2086
cc $SAN $W -fPIC -fvisibility=hidden -std=gnu11 -DGB_SHIM_TEST_HOOKS -I"$OUT/gen" -I"$MESA_SRC/include" -shared \
  -Wl,-soname,vulkan.intel.so -o "$A/vulkan.intel.so" "$G/shim/gb_shim.c" -ldl -lpthread || rc=1
# shellcheck disable=SC2086
cc $SAN $W -fPIC -fvisibility=hidden -I"$MESA_SRC/include" -shared -o "$A/fake_nvk.so" "$G/test/fake_icd.c" || rc=1
# shellcheck disable=SC2086
cc $SAN $W -Wno-unused-result -I"$MESA_SRC/include" -o "$A/test_shim" "$G/test/test_shim.c" -ldl || rc=1
printf 'leak:libvulkan_lvp\nleak:libLLVM\nleak:libgallium\nleak:libdrm\n' > "$A/lsan.supp"
( cd "$A" && ASAN_OPTIONS=detect_leaks=1:halt_on_error=1 UBSAN_OPTIONS=print_stacktrace=1:halt_on_error=1 \
  LSAN_OPTIONS="suppressions=$A/lsan.supp:print_suppressions=0" \
  ./test_shim ./vulkan.intel.so "$LVP" ./fake_nvk.so ) > "$L/gd1b_host_asan.log" 2>&1
r=$?
n=$(grep -cE 'ERROR: (AddressSanitizer|LeakSanitizer)|runtime error:' "$L/gd1b_host_asan.log")
echo "rc=$r sanitizer_reports=$n $(grep '^RESULT' "$L/gd1b_host_asan.log")"; [ $r = 0 ] && [ "$n" = 0 ] || rc=1

echo "### GD-1B-12 shim reproducibility (two NDK builds)"
{
  sha256sum "$OUT/android/vulkan.intel.so"
  cp "$OUT/android/vulkan.intel.so" "$A/vulkan.intel.so.build1"
  bash "$G/scripts/build_shim.sh" > "$A/build2.log" 2>&1 || echo "second build_shim.sh failed"
  sha256sum "$OUT/android/vulkan.intel.so"
  if cmp "$A/vulkan.intel.so.build1" "$OUT/android/vulkan.intel.so"; then echo "identical"; else echo "DIFFERENT"; fi
} > "$L/gd1b_repro.log" 2>&1
grep -q '^identical$' "$L/gd1b_repro.log" || rc=1
echo "$(tail -1 "$L/gd1b_repro.log") $(head -1 "$L/gd1b_repro.log" | cut -c1-16)"
echo "RESULT: $([ $rc = 0 ] && echo PASS || echo FAIL)"
exit $rc
