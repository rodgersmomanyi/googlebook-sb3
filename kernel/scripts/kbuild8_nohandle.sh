#!/bin/bash
# Kernel rebuild #8: + GB_PRIME_NOHANDLE (no leaked GEM handle per prime surface import) on top of build #7.
# --config=local = incremental. Log /build/kbuild8.log; markers /build/kbuild8.done | /build/kbuild8.failed
cd /build/gki || exit 1
exec > /build/kbuild8.log 2>&1 < /dev/null
rm -f /build/kbuild8.done /build/kbuild8.failed
export BUILD_NUMBER=16302624
BAZEL="./tools/bazel --output_user_root=/build/.bazel"
echo "=== start $(date -Is)"
for m in GB_RGBA_DUMB GB_HWCURSOR GB_DUMB_IMPORT GB_PRIME_LEAK GB_PRIME_NOHANDLE; do
  grep -rq "$m" common/drivers/gpu/drm/vmwgfx/ && echo "patch $m present" || { echo "MISSING $m"; touch /build/kbuild8.failed; exit 1; }
done
grep -q "CONFIG_VMXNET3=m" common/arch/x86/configs/gki_defconfig && echo "vmxnet3 config present"
$BAZEL run --config=stamp --config=local //common:kernel_x86_64_dist -- --destdir=/build/dist
RC=$?
echo "=== build rc=$RC $(date -Is)"
ls -la --time-style=+%m-%d_%H:%M /build/dist/bzImage /build/dist/vmwgfx.ko /build/dist/psmouse.ko /build/dist/vmxnet3.ko /build/dist/ac.ko /build/dist/battery.ko /build/dist/efidrm.ko \
  /build/dist/drm_ttm_helper.ko /build/dist/drm_sysfb_helper.ko 2>&1
[ $RC -eq 0 ] && touch /build/kbuild8.done || touch /build/kbuild8.failed
