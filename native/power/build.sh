#!/bin/sh
# Builds gb_power (NDK, dynamic against bionic) and runs the host unit tests. Run on the builder from the directory
# holding gb_power.c and test/ (e.g. /build/native/power/src).
set -e
cd "$(dirname "$0")"
NDK=${NDK:-/build/ndk}
CC="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/clang --target=x86_64-linux-android34"
$CC -O2 -Wall -Wextra -Werror -o gb_power gb_power.c
"$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip" gb_power
echo "built: $(ls -l gb_power)"
cc -O2 -Wall -Wextra -Wno-format-truncation -o test/test_gb_power test/test_gb_power.c -lm
W=${WORK:-$(pwd)/testwork}
rm -rf "$W"
./test/test_gb_power "$W"
