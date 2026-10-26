#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Build the KAT (r2): generate vnni_kat_ops.h (GNU as), cross-check the encodings (objdump), build the static target
# binary with the NDK clang, build + run the host self-check with gcc (the builder has AVX512_VNNI), build + run the
# user-space emulation test build twice (k15 core: must PASS; k14 merge: must FAIL) and once with injected foreign
# emulations (must PASS through the quiet re-run). Then emit the drop: vnni_kat (static ELF) + SHA256SUMS for
# C:\GB-x86\inject\kat\ (contract 2.7). Only the static target binary is dropped; the test builds stay here.
# Usage (builder): bash build_vnni_kat.sh <tools dir> <out dir> [<apply_vnni4.py>]
# (apply_vnni4.py --emit-core writes gb_vnni_core.h, the emulator core traps.c includes; default: <tools dir>/
# apply_vnni4.py, then <tools dir>/../apply_vnni4.py)
set -eu
T=${1:?tools dir}; OUT=${2:?out dir}; GEN=${3:-}
NDK=${NDK:-/build/ndk}
CL=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/clang
[ -n "$GEN" ] || for g in "$T/apply_vnni4.py" "$T/../apply_vnni4.py"; do [ -f "$g" ] && { GEN=$g; break; }; done
[ -n "$GEN" ] && [ -f "$GEN" ] || { echo "build_vnni_kat: apply_vnni4.py not found"; exit 1; }
W=$OUT/work
rm -rf "$W"; mkdir -p "$W/core" "$OUT"
rm -f "$OUT/vnni_kat" "$OUT/SHA256SUMS" "$OUT/vnni_kat_hostcheck" "$OUT"/vnni_kat_useremu*
python3 -I "$T/gen_vnni_kat_ops.py" "$W/vnni_kat_ops.h" "$W/gen"
python3 -I "$T/gen_vnni_kat_check.py" "$W/gen/ops_meta.txt" "$W/chk"
python3 -I "$GEN" --emit-core "$W/core/gb_vnni_core.h"
CF="-O2 -std=gnu11 -Wall -Wextra -Werror -I$W"
# target: static, NDK clang, no -mavx* for the C code
"$CL" --target=x86_64-linux-android34 -static $CF -o "$OUT/vnni_kat" "$T/vnni_kat.c"
file "$OUT/vnni_kat"
# host self-check: gcc, executes only EVEX + scalar reference (every case's harness)
gcc $CF -DGB_VNNI_HOSTCHECK -o "$OUT/vnni_kat_hostcheck" "$T/vnni_kat.c"
# user-space emulation test builds (host only): the k15 core, and the k14 merge as the negative control
gcc $CF -DGB_VNNI_USEREMU "-I$W/core" "-I$T" -o "$OUT/vnni_kat_useremu" "$T/vnni_kat.c"
gcc $CF -DGB_VNNI_USEREMU -DGB_VNNI_USEREMU_K14 "-I$W/core" "-I$T" -o "$OUT/vnni_kat_useremu_k14" "$T/vnni_kat.c"
rc=0
echo "== host self-check (builder has AVX512_VNNI, no AVX_VNNI)"
"$OUT/vnni_kat_hostcheck" | tee "$W/hostcheck.txt" || true
grep -q '^GB_VNNI_KAT: PASS ' "$W/hostcheck.txt" || { echo "hostcheck: not PASS"; rc=1; }
echo "== user-space emulation, k15 core (expect PASS; skips only zram)"
"$OUT/vnni_kat_useremu" | tee "$W/useremu.txt" || true
grep -q -E '^GB_VNNI_KAT: PASS [0-9]+ cases \(1 skipped: zram\)$' "$W/useremu.txt" ||
  { echo "useremu k15: not PASS with only the zram skip"; rc=1; }
echo "== user-space emulation, k15 core, 5 foreign emulations injected (expect PASS via the quiet re-run)"
GB_VNNI_UE_FOREIGN=5 "$OUT/vnni_kat_useremu" | tee "$W/useremu_foreign.txt" || true
grep -q '^GB_VNNI_KAT: counters exact on quiet re-run 1$' "$W/useremu_foreign.txt" &&
  grep -q -E '^GB_VNNI_KAT: PASS [0-9]+ cases \(1 skipped: zram\)$' "$W/useremu_foreign.txt" ||
  { echo "useremu foreign: the re-run path did not PASS"; rc=1; }
echo "== user-space emulation, k14 merge (negative control: expect FAIL)"
"$OUT/vnni_kat_useremu_k14" > "$W/useremu_k14.txt" 2>&1 && k14rc=0 || k14rc=$?
grep -E '^GB_VNNI_KAT: (FAIL|PASS|counters )' "$W/useremu_k14.txt" || true
[ "$k14rc" = 1 ] && grep -q '^GB_VNNI_KAT: FAIL ' "$W/useremu_k14.txt" || { echo "useremu k14: did not FAIL"; rc=1; }
# the static binary links the generated arrays (the c_* byte arrays are in .rodata)
nv=$(objdump -s -j .rodata "$OUT/vnni_kat" 2>/dev/null | wc -l)
echo "vnni_kat .rodata present: $([ "$nv" -gt 0 ] && echo yes || echo no)"
if [ $rc = 0 ]; then
  (cd "$OUT" && sha256sum vnni_kat > SHA256SUMS)
  echo "drop: $OUT/vnni_kat + SHA256SUMS"
  cat "$OUT/SHA256SUMS"
  echo "build_vnni_kat: OK"
else
  rm -f "$OUT/vnni_kat" "$OUT/SHA256SUMS"
  echo "build_vnni_kat: FAILED (no drop written)"
fi
exit $rc
