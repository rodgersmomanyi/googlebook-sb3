#!/bin/bash
# Kernel rebuild: vmwgfx RGBA dumb buffers (GB_RGBA_DUMB, for Mesa svga) + psmouse with VMMOUSE (seamless mouse).
# Log: /build/kbuild3.log; markers /build/kbuild3.done | /build/kbuild3.failed
cd /build/gki || exit 1
exec > /build/kbuild3.log 2>&1 < /dev/null
rm -f /build/kbuild3.done /build/kbuild3.failed
export BUILD_NUMBER=16302624
BAZEL="./tools/bazel --output_user_root=/build/.bazel"
echo "=== start $(date -Is)"
grep -n "GB_RGBA_DUMB" common/drivers/gpu/drm/vmwgfx/vmwgfx_surface.c || { echo "RGBA patch missing"; touch /build/kbuild3.failed; exit 1; }
echo "=== parse check"
timeout 300 $BAZEL query //common:kernel_x86_64_dist >/dev/null || { echo "PARSE_FAIL"; touch /build/kbuild3.failed; exit 1; }
echo "=== savedefconfig (canonicalize gki_defconfig)"
cp common/arch/x86/configs/gki_defconfig /build/gki_defconfig.before
timeout 1800 $BAZEL run //common:kernel_x86_64_config -- savedefconfig
echo "savedefconfig rc=$?"
diff /build/gki_defconfig.before common/arch/x86/configs/gki_defconfig && echo "defconfig unchanged (already canonical)"
grep -n "MOUSE_PS2" common/arch/x86/configs/gki_defconfig
echo "=== dist build $(date -Is)"
$BAZEL run --config=stamp //common:kernel_x86_64_dist -- --dist_dir=/build/dist
RC=$?
echo "=== build rc=$RC $(date -Is)"
cat /build/dist/kernel.release 2>/dev/null
ls -la /build/dist/bzImage /build/dist/vmwgfx.ko /build/dist/psmouse.ko /build/dist/efidrm.ko /build/dist/drm_ttm_helper.ko /build/dist/drm_sysfb_helper.ko 2>&1
[ $RC -eq 0 ] && [ -f /build/dist/psmouse.ko ] && touch /build/kbuild3.done || touch /build/kbuild3.failed
