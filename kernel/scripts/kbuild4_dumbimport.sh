#!/bin/bash
# Kernel rebuild #4: vmwgfx GB_DUMB_IMPORT (dumb-buffer dma-bufs re-importable as 3D surfaces, for Mesa svga) on top
# of GB_RGBA_DUMB + GB_HWCURSOR + psmouse/VMMOUSE. --config=local = persistent incremental kernel out dir, so later
# vmwgfx-only changes rebuild in minutes. Log /build/kbuild4.log; markers /build/kbuild4.done | /build/kbuild4.failed
cd /build/gki || exit 1
exec > /build/kbuild4.log 2>&1 < /dev/null
rm -f /build/kbuild4.done /build/kbuild4.failed
export BUILD_NUMBER=16302624
BAZEL="./tools/bazel --output_user_root=/build/.bazel"
echo "=== start $(date -Is)"
for m in GB_RGBA_DUMB GB_HWCURSOR GB_DUMB_IMPORT; do
  grep -rq "$m" common/drivers/gpu/drm/vmwgfx/ && echo "patch $m present" || { echo "MISSING $m"; touch /build/kbuild4.failed; exit 1; }
done
grep -q "CONFIG_MOUSE_PS2_VMMOUSE=y" common/arch/x86/configs/gki_defconfig && echo "psmouse/VMMOUSE config present"
$BAZEL run --config=stamp --config=local //common:kernel_x86_64_dist -- --destdir=/build/dist
RC=$?
echo "=== build rc=$RC $(date -Is)"
ls -la --time-style=+%m-%d_%H:%M /build/dist/bzImage /build/dist/vmwgfx.ko /build/dist/psmouse.ko /build/dist/efidrm.ko \
  /build/dist/drm_ttm_helper.ko /build/dist/drm_sysfb_helper.ko 2>&1
[ $RC -eq 0 ] && touch /build/kbuild4.done || touch /build/kbuild4.failed
