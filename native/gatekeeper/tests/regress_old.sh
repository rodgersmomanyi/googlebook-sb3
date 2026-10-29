#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# tests/regress_old.sh OLD_SRC WORK_DIR LIBCRYPTO_HOST BSSL_INCLUDE      (called by build.sh 2b)
#
# Builds the CURRENT tests (tests/test_main.cpp, fake_binder.cpp, fake_sm.cpp) against the
# service sources in OLD_SRC (a whole older src/ directory; tests/regress/pre_w4_src is the
# reviewed pre-W4 one, binary sha256 b62b205c...a46e) with build.sh's ASan/UBSan flags, and runs
# them. It proves that each W4 test fails on the code it fixes:
#   test_throttling                   W4-GK-1  reboot during a lock-out
#   test_reboot_loop_no_bypass        W4-GK-1  repeated reboots: counter kept, timer from boot
#   test_crypto_failure_not_counted   W4-GK-2  a crypto failure counted as a wrong guess
#   test_registration                 W4-GK-3  IGatekeeper/default not registered -> idles
# Exit 0 when every one of the four has failed checks on OLD_SRC, 1 otherwise. Results:
# WORK_DIR/test.log (the full run), WORK_DIR/summary.txt, WORK_DIR/inputs.sha256.
set -eu
[ $# -eq 4 ] || { echo "usage: $0 OLD_SRC WORK_DIR LIBCRYPTO_HOST BSSL_INCLUDE" >&2; exit 2; }
OLD=$(cd "$1" && pwd)
W=$2
LIBCRYPTO=$3
BSSL_INC=$4
HERE=$(cd "$(dirname "$0")/.." && pwd)

rm -rf "$W"
mkdir -p "$W/tree/tests" "$W/obj" "$W/tmp"
cp -r "$OLD" "$W/tree/src"   # the tests include "../src/...": the old headers
cp "$HERE"/tests/test_main.cpp "$HERE"/tests/fake_binder.cpp "$HERE"/tests/fake_binder.h \
   "$HERE"/tests/fake_sm.cpp "$W/tree/tests/"
(cd "$W/tree" && sha256sum src/* tests/*) > "$W/inputs.sha256"

INC="-I$W/tree/src -I$HERE/third_party/aosp/system_gatekeeper/include \
     -I$HERE/third_party/aosp/libhardware/include -I$BSSL_INC"
SAN="-fsanitize=address,undefined -fno-sanitize-recover=undefined -fno-omit-frame-pointer"
CXX="g++ -std=c++17 -O1 -g $SAN"
LIB=""
for f in gb_gatekeeper gk_store shared_secret aidl_service gk_log; do
  $CXX -Wall -Wextra -Werror $INC -c "$W/tree/src/$f.cpp" -o "$W/obj/$f.o"
  LIB="$LIB $W/obj/$f.o"
done
for f in gatekeeper gatekeeper_messages; do
  $CXX -Wall -Werror $INC -c "$HERE/third_party/aosp/system_gatekeeper/$f.cpp" -o "$W/obj/aosp_$f.o"
  LIB="$LIB $W/obj/aosp_$f.o"
done
for f in tests/test_main tests/fake_binder tests/fake_sm src/service_main; do
  $CXX -Wall -Wextra -Werror $INC -c "$W/tree/$f.cpp" -o "$W/obj/$(basename "$f").o"
done
g++ $SAN $LIB "$W/obj/test_main.o" "$W/obj/fake_binder.o" "$LIBCRYPTO" -lpthread \
  -o "$W/gk_host_tests"
g++ $SAN $LIB "$W/obj/service_main.o" "$W/obj/fake_binder.o" "$W/obj/fake_sm.o" "$LIBCRYPTO" \
  -lpthread -o "$W/gk_host_service"

W4_TESTS="test_throttling test_reboot_loop_no_bypass test_crypto_failure_not_counted test_registration"
rc=0
"$W/gk_host_tests" "$W/vectors.json" "$W/tmp" "$W/gk_host_service" > "$W/test.log" 2>&1 || rc=$?

bad=0
{
  echo "regress-old: tests of $(sha256sum "$HERE/tests/test_main.cpp" | cut -c1-16) against $OLD"
  echo "regress-old: test binary exit status $rc (non-zero expected)"
  for t in $W4_TESTS; do
    n=$(grep -c "^FAIL \[$t\]" "$W/test.log" || true)
    echo "regress-old: $t: $n failed checks on the old code"
  done
  others=$(grep "^FAIL \[" "$W/test.log" | sed 's/^FAIL \[\([^]]*\)\].*/\1/' | sort | uniq -c |
           grep -v -e " test_throttling$" -e " test_reboot_loop_no_bypass$" \
                   -e " test_crypto_failure_not_counted$" -e " test_registration$" |
           sed 's/^ *//' | tr '\n' ';' || true)
  echo "regress-old: other tests with failed checks (expected: the boot_id file checks of" \
       "test_enroll_verify and test_replace_and_delete only): ${others:-none}"
} > "$W/summary.txt"
for t in $W4_TESTS; do
  n=$(grep -c "^FAIL \[$t\]" "$W/test.log" || true)
  [ "$n" -gt 0 ] || bad=1
done
[ "$rc" -ne 0 ] || bad=1
if [ $bad -eq 0 ]; then
  echo "regress-old: GK_REGRESS_OLD: PASS (every W4 test fails on the old code)" >> "$W/summary.txt"
else
  echo "regress-old: GK_REGRESS_OLD: FAIL (a W4 test passes on the old code)" >> "$W/summary.txt"
fi
grep "^FAIL \[\|^  \[" "$W/test.log" || true
grep GK_HOST_TESTS "$W/test.log" || true
cat "$W/summary.txt"
exit $bad
