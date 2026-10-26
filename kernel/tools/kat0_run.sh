#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# KAT-0 runner (K-14): host unit test of the k15 emulator core + the k14 negative control. Builder or any Linux
# host with python3, GNU as/objdump/objcopy and gcc; the NDK clang build is optional (NDK=<r27c root>).
# Usage: bash kat0_run.sh <tools dir> <apply_vnni4.py> <work dir> [<traps.c-dir to compare the core header with>]
# Exit 0 only if: the k15 core PASSes every category (gcc, and NDK clang when available), and the k14 merge
# FAILs exactly sse_init,vex128_upper,maxvl.
# shellcheck disable=SC2015
set -u
T=${1:?tools dir}; GEN=${2:?apply_vnni4.py}; W=${3:?work dir}; TREE=${4:-}
NDK=${NDK:-/build/ndk}
mkdir -p "$W"
rc=0
python3 -I "$GEN" --emit-core "$W/gb_vnni_core.h" || exit 1
if [ -n "$TREE" ]; then
  if cmp -s "$W/gb_vnni_core.h" "$TREE/gb_vnni_core.h"; then echo "core header: identical to $TREE/gb_vnni_core.h"
  else echo "core header: DIFFERS from $TREE/gb_vnni_core.h"; rc=1; fi
fi
python3 -I "$T/gen_decode_vectors.py" "$W/vectors.txt" "$W/as" || exit 1
CF=(-O2 -std=gnu99 -Wall -Wextra -Werror "-I$W" "-I$T")
gcc "${CF[@]}" -o "$W/kat0" "$T/test_gb_vnni_core.c" || exit 1
gcc "${CF[@]}" -DGB_K14_MERGE -o "$W/kat0_k14" "$T/test_gb_vnni_core.c" || exit 1
echo "== k15 core (gcc $(gcc -dumpfullversion))"
"$W/kat0" "$W/vectors.txt" | tee "$W/kat0_k15.txt"; r15=${PIPESTATUS[0]}
echo "== k14 merge, negative control (gcc)"
"$W/kat0_k14" "$W/vectors.txt" | tee "$W/kat0_k14.txt"; r14=${PIPESTATUS[0]}
[ "$r15" = 0 ] && grep -q '^KAT0 RESULT PASS failed=none$' "$W/kat0_k15.txt" || { echo "K-14: k15 core did not PASS"; rc=1; }
[ "$r14" = 1 ] && grep -q '^KAT0 RESULT FAIL failed=vex128_upper,maxvl,sse_init$' "$W/kat0_k14.txt" ||
  { echo "K-14: k14 merge did not fail exactly vex128_upper,maxvl,sse_init"; rc=1; }
CL=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/clang
if [ -x "$CL" ]; then
  "$CL" --target=x86_64-linux-android34 -static "${CF[@]}" -o "$W/kat0_ndk" "$T/test_gb_vnni_core.c" || exit 1
  echo "== k15 core (NDK clang, static)"
  "$W/kat0_ndk" "$W/vectors.txt" | tee "$W/kat0_k15_ndk.txt"; rn=${PIPESTATUS[0]}
  [ "$rn" = 0 ] && grep -q '^KAT0 RESULT PASS failed=none$' "$W/kat0_k15_ndk.txt" || { echo "K-14: NDK build did not PASS"; rc=1; }
fi
if [ $rc = 0 ]; then echo "K-14 KAT-0: PASS (k15 core passes; k14 merge fails exactly the 3 defect categories)"
else echo "K-14 KAT-0: FAIL"; fi
exit $rc
