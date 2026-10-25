#!/bin/bash
# GC-10: reproducible build. Builds the Android artifacts a second time in a different build directory and compares
# the stripped payload files and the unstripped copies with the first build (30_build_android.sh defaults).
# usage: 60_repro.sh [first out dir]
set -uo pipefail
. "$(dirname "$0")/00_env.sh"
O1=${1:-$W/out-android}
B2=$W/b-android-r2
O2=$W/out-android-r2
rm -rf "$B2" "$O2"
bash "$(dirname "$0")/30_build_android.sh" "$B2" "$O2" > "$W/logs/repro_build.log" 2>&1 || { tail -20 "$W/logs/repro_build.log"; exit 1; }
fail=0
for f in gb_gpu_compute lib64/libgb_nvk_loader.so lib64/libgb_gpu_compute.so test/gc_probe \
         unstripped/gb_gpu_compute unstripped/libgb_nvk_loader.so unstripped/libgb_gpu_compute.so; do
  a=$(sha256sum < "$O1/$f" | cut -d' ' -f1); b=$(sha256sum < "$O2/$f" | cut -d' ' -f1)
  if [ "$a" = "$b" ]; then echo "same      $a  $f"; else echo "DIFFERENT $a $b  $f"; fail=1; fi
done
if [ $fail -eq 0 ]; then
  echo "REPRO PASS: two builds in $W/b-android and $B2 are byte-identical"
  rm -rf "$B2" "$O2"
else
  echo "REPRO FAIL (kept $B2 and $O2 for diffoscope-style inspection)"
fi
exit $fail
