#!/bin/sh
# Builds gb_power (NDK, static bionic: it starts at early-init) and runs the host unit tests. Run on the builder from the
# directory holding gb_power.c, uapi/ and test/ (e.g. /build/native/power/src).
set -e
cd "$(dirname "$0")"
NDK=${NDK:-/build/ndk}
KSRC=${KSRC:-/build/gki/common}
# DTX uAPI: compile against the kernel tree's exact header (a read-only copy; nothing in the tree is written).
if [ -f "$KSRC/include/uapi/linux/surface_aggregator/dtx.h" ]; then
  mkdir -p uapi/linux/surface_aggregator
  cp "$KSRC/include/uapi/linux/surface_aggregator/dtx.h" uapi/linux/surface_aggregator/dtx.h
fi
[ -f uapi/linux/surface_aggregator/dtx.h ] || { echo "missing uapi/linux/surface_aggregator/dtx.h"; exit 1; }
CC="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/clang --target=x86_64-linux-android34"
$CC -O2 -Wall -Wextra -Werror -static -Iuapi -o gb_power gb_power.c
"$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip" gb_power
echo "built: $(ls -l gb_power)"
cc -O2 -Wall -Wextra -Wno-format-truncation -Iuapi -o test/test_gb_power test/test_gb_power.c -lm
W=${WORK:-$(pwd)/testwork}
rm -rf "$W"
./test/test_gb_power "$W"
