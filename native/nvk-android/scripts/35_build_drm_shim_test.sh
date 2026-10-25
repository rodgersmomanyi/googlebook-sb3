#!/bin/bash
# Optional test helper (builder only, NOT shipped): Mesa's nouveau no-op drm-shim for Android, used by
# 40_bionic_chroot_test.sh to give NVK a fake TU117 so device creation + NAK compilation can be exercised on bionic.
# Needs the $B build dir of the same PATCHSET from 20_build_nvk_android.sh. Two test-only tweaks:
#  - libdrm's nouveau/nvif headers (our libdrm.a was built with -Dnouveau=disabled; the shim only needs headers)
#  - drm_shim.c: bionic declares ioctl(int, int, ...) (+ an overload unless BIONIC_IOCTL_NO_SIGNEDNESS_OVERLOAD)
set -euo pipefail
. "$(dirname "$0")/00_env.sh"
mkdir -p "$W/sys/include/libdrm/nouveau/nvif"
cp "$W/src/libdrm/nouveau/nvif/"*.h "$W/sys/include/libdrm/nouveau/nvif/"
cp "$W/src/libdrm/nouveau/nouveau.h" "$W/sys/include/libdrm/nouveau/"
F=$MESA_SRC/src/drm-shim/drm_shim.c
[ -f "$F.orig" ] || cp "$F" "$F.orig"
python3 -I - "$F" <<'EOF'
import sys
p = sys.argv[1]
s = open(p + ".orig").read()
s = "/* test-only (nvk-android bionic chroot run): bionic ioctl() is (int, int, ...) */\n" \
    "#define BIONIC_IOCTL_NO_SIGNEDNESS_OVERLOAD 1\n" + s
old = "PUBLIC int\nioctl(int fd, unsigned long request, ...)\n"
assert s.count(old) == 1
s = s.replace(old, "PUBLIC int\n#ifdef __BIONIC__\nioctl(int fd, int request, ...)\n#else\n"
                   "ioctl(int fd, unsigned long request, ...)\n#endif\n")
open(p, "w").write(s)
EOF
meson configure "$B" -Dtools=drm-shim >/dev/null
nice -n 5 ninja -C "$B" -j "$JOBS" src/nouveau/drm-shim/libnouveau_noop_drm_shim.so
ls -la "$B/src/nouveau/drm-shim/libnouveau_noop_drm_shim.so"
