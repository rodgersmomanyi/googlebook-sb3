#!/bin/bash
# Stage 2: baseline GKI build to reproduce lapis's exact kernel (verify KMI), then we add display drivers.
# Usage: gki_build.sh baseline   | gki_build.sh display
set -uo pipefail
STAGE="${1:-baseline}"
cd /build/gki
export BUILD_NUMBER=16302624
BAZEL="./tools/bazel --output_user_root=/build/.bazel"
LOG=/build/build-$STAGE.log
exec > >(tee -a "$LOG") 2>&1
echo "=== $(date) build stage=$STAGE start ==="
free -h; df -h /build

if [ "$STAGE" = "baseline" ]; then
  echo "--- available x86_64 dist targets ---"
  $BAZEL query '//common:*x86_64*dist*' 2>/dev/null | tee /build/targets.txt || true
  echo "=== building //common:kernel_x86_64_dist (BUILD_NUMBER=$BUILD_NUMBER, stamp) ==="
  $BAZEL run --config=stamp //common:kernel_x86_64_dist -- --dist_dir=/build/dist
  RC=$?
  echo "=== build rc=$RC $(date) ==="
  echo "--- kernel.release (must be 6.18.21-android17-5-g3938c8d97142-ab16302624) ---"
  cat /build/dist/kernel.release 2>/dev/null
  echo "--- dist contents ---"; ls -la /build/dist/ 2>/dev/null | head -40
  [ $RC -eq 0 ] && echo "BUILD_BASELINE_OK" || echo "BUILD_BASELINE_FAIL"
fi

if [ "$STAGE" = "display" ]; then
  DEF=common/arch/x86/configs/gki_defconfig
  echo "=== adding display driver configs to $DEF ==="
  grep -qxF 'CONFIG_DRM_SIMPLEDRM=m' $DEF || echo 'CONFIG_DRM_SIMPLEDRM=m' >> $DEF
  grep -qxF 'CONFIG_SYSFB_SIMPLEFB=y' $DEF || echo 'CONFIG_SYSFB_SIMPLEFB=y' >> $DEF
  grep -qxF 'CONFIG_DRM_VMWGFX=m' $DEF || echo 'CONFIG_DRM_VMWGFX=m' >> $DEF
  grep -qxF 'CONFIG_DRM_TTM_HELPER=m' $DEF || echo 'CONFIG_DRM_TTM_HELPER=m' >> $DEF
  tail -6 $DEF
  echo "=== rebuilding with display drivers ==="
  $BAZEL run --config=stamp //common:kernel_x86_64_dist -- --dist_dir=/build/dist
  RC=$?
  echo "=== rebuild rc=$RC $(date) ==="
  cat /build/dist/kernel.release 2>/dev/null
  echo "--- new modules ---"; ls -la /build/dist/*.ko 2>/dev/null | grep -iE 'simpledrm|vmwgfx|sysfb|ttm' || true
  find /build/gki -name 'simpledrm.ko' -o -name 'vmwgfx.ko' 2>/dev/null | head
  [ $RC -eq 0 ] && echo "BUILD_DISPLAY_OK" || echo "BUILD_DISPLAY_FAIL"
fi
