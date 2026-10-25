#!/bin/sh
# Builds gb_power (NDK, static bionic: it starts at early-init) and runs every host test:
#   1. NDK clang, -Wall -Wextra -Werror, static, stripped            -> ./gb_power (copy it to bin/gb_power)
#   2. clang (NDK toolchain, host target) -Wall -Wextra -Werror on the daemon and the unit tests (syntax only)
#   3. unit tests, gcc -Wall -Wextra -Werror                          (test/test_gb_power.c)
#   4. the same unit tests under ASan + UBSan (no recover, leaks checked)
#   5. host build of the daemon + control-socket protocol test       (test/test_socket.py, python3 -I)
#   6. gb_power.rc lint                                              (test/check_rc.py, python3 -I)
# Run on the builder from a scratch copy holding gb_power.c, uapi/ and test/ (for example
# /build/native/dgpu/gb-power/src). Heavy steps run at nice 19 / ionice idle. Nothing outside this directory and
# $WORK is written; KSRC is only read.
set -e
cd "$(dirname "$0")"
NDK=${NDK:-/build/ndk}
KSRC=${KSRC:-/build/gki/common}
NICE="nice -n 19 ionice -c3"
BIN="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
# DTX uAPI: compile against the kernel tree's exact header (a read-only copy; nothing in the tree is written).
if [ -f "$KSRC/include/uapi/linux/surface_aggregator/dtx.h" ]; then
  mkdir -p uapi/linux/surface_aggregator
  cp "$KSRC/include/uapi/linux/surface_aggregator/dtx.h" uapi/linux/surface_aggregator/dtx.h
fi
[ -f uapi/linux/surface_aggregator/dtx.h ] || { echo "missing uapi/linux/surface_aggregator/dtx.h"; exit 1; }

echo "== 1. NDK static build"
$NICE "$BIN/clang" --target=x86_64-linux-android34 -O2 -Wall -Wextra -Werror -static -Iuapi -o gb_power gb_power.c
"$BIN/llvm-strip" gb_power
file gb_power | grep -q "statically linked" || { echo "gb_power is not statically linked"; exit 1; }
file gb_power
sha256sum gb_power

echo "== 2. clang host -Wall -Wextra -Werror"
$NICE "$BIN/clang" --target=x86_64-unknown-linux-gnu -Wall -Wextra -Werror -Iuapi -fsyntax-only gb_power.c
$NICE "$BIN/clang" --target=x86_64-unknown-linux-gnu -Wall -Wextra -Werror -Iuapi -fsyntax-only test/test_gb_power.c
echo "clang: clean"

W=${WORK:-$(pwd)/testwork}
echo "== 3. unit tests"
$NICE cc -O2 -g -Wall -Wextra -Werror -Wno-format-truncation -Iuapi -o test/test_gb_power test/test_gb_power.c -lm
rm -rf "$W"
./test/test_gb_power "$W" > test/test_gb_power.log 2>&1 || { grep -E "^FAIL|checks," test/test_gb_power.log; exit 1; }
grep -E "^FAIL|checks," test/test_gb_power.log

echo "== 4. unit tests, ASan + UBSan"
$NICE cc -O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=all -Wall -Wextra -Werror \
  -Wno-format-truncation -Iuapi -o test/test_gb_power_asan test/test_gb_power.c -lm
rm -rf "$W"
ASAN_OPTIONS=detect_leaks=1:abort_on_error=1 UBSAN_OPTIONS=print_stacktrace=1:halt_on_error=1 \
  ./test/test_gb_power_asan "$W" > test/test_gb_power_asan.log 2>&1 || { tail -40 test/test_gb_power_asan.log; exit 1; }
if grep -qE "runtime error|AddressSanitizer|LeakSanitizer" test/test_gb_power_asan.log; then
  grep -E "runtime error|AddressSanitizer|LeakSanitizer" test/test_gb_power_asan.log; exit 1
fi
grep -E "^FAIL|checks," test/test_gb_power_asan.log
echo "sanitizers: no reports"

echo "== 5. control-socket protocol test (host daemon)"
$NICE cc -O2 -g -Wall -Wextra -Werror -Wno-format-truncation -Iuapi -o test/gb_power_host gb_power.c
rm -rf "$W"
python3 -I test/test_socket.py test/gb_power_host "$W"
rm -rf "$W"

echo "== 6. rc lint"
python3 -I test/check_rc.py gb_power.rc
echo "== all done"
