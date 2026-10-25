#!/bin/bash
# run_build.sh <name> <PATCHSET> [Bdir-suffix]: one clean NVK build at nice 19 / idle IO, results in results/<name>/
set -uo pipefail
W=/build/native/nvk-android; N=$1; export PATCHSET=$2; SUF=${3:-}
export B=$W/b-nvk-$PATCHSET$SUF STAGE=$W/stage-$PATCHSET$SUF
L=$W/logs/build_$N.log
{ echo "=== build $N start $(date -Is) PATCHSET=$PATCHSET B=$B"; nice -n 19 ionice -c3 bash $W/scripts/20_build_nvk_android.sh; echo "build exit=$?"; } > $L 2>&1
rc=$(tail -1 $L | sed -n "s/build exit=//p")
mkdir -p $W/results/$N
cp -p $STAGE/*.so $W/results/$N/ 2>/dev/null
cp -p $W/patches/*.patch $W/results/$N/ 2>/dev/null
( cd $W/results/$N && sha256sum *.so ) >> $L
echo "=== build $N end $(date -Is) rc=$rc" >> $L
echo $rc > $W/results/$N.done
