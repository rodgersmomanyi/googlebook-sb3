#!/bin/bash
# Recreate the sb3-android branch of libcamera from the pristine v0.7.2 tag by applying the
# P0..P6 patchers (one commit each), then export the series to patches/.
set -euo pipefail
W=/build/native/camera-userspace
S=$W/src/libcamera
cd $S
git checkout -q -f v0.7.2 2>/dev/null || git checkout -q -f 191e202
git branch -q -D sb3-android 2>/dev/null || true
git checkout -q -b sb3-android
G="git -c user.name=SB3-port -c user.email=sb3@localhost"
declare -A MSG=(
 [p0_bionic_affinity]="base: thread: use sched_setaffinity() on bionic"
 [p1_softisp_nv12]="software_isp: debayer_cpu: add NV12 (sYCC) output"
 [p2_android_minigbm]="android: generic: handle minigbm gralloc buffer layouts"
 [p3_android_single_stream]="android: produce all streams from one processed stream"
 [p4_android_hal_glue]="android: firmware camera location, gralloc stride probe, Android log"
 [p5_simple_ipu4]="pipeline: simple: IPU4P driver names, extra drivers, frame duration limits"
 [p6_dmaheap_rdonly]="dma_buf_allocator: open DMA heaps read-only"
)
for p in p0_bionic_affinity p1_softisp_nv12 p2_android_minigbm p3_android_single_stream \
         p4_android_hal_glue p5_simple_ipu4 p6_dmaheap_rdonly; do
  python3 $W/patchers/$p.py $S
  git add src include
  $G commit -q -m "${MSG[$p]}"
done
rm -rf $W/patches && mkdir -p $W/patches
git format-patch -q -o $W/patches v0.7.2..sb3-android 2>/dev/null || git format-patch -q -o $W/patches 191e202..sb3-android
git diff 191e202..sb3-android -- src include > $W/patches/libcamera-v0.7.2-sb3-android.diff
ls $W/patches
