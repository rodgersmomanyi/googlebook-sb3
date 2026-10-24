#!/bin/bash
cd /build/gki || exit 1
F=common/BUILD.bazel
python3 - "$F" <<'PY'
import sys
f=sys.argv[1]
s=open(f).read()
anchor='    name = "kernel_x86_64",\n'
n=s.count(anchor)
assert n==1, ("anchor count",n)
block=anchor+('    module_outs = [\n'
 '        "drivers/gpu/drm/sysfb/drm_sysfb_helper.ko",\n'
 '        "drivers/gpu/drm/vmwgfx/vmwgfx.ko",\n'
 '        "drivers/gpu/drm/sysfb/efidrm.ko",\n'
 '        "drivers/gpu/drm/drm_ttm_helper.ko",\n'
 '    ],\n')
if 'drivers/gpu/drm/vmwgfx/vmwgfx.ko' not in s:
    s=s.replace(anchor,block,1)
    open(f,'w').write(s)
    print("edited: module_outs added")
else:
    print("already present")
PY
echo "--- verify ---"
grep -n -A6 'name = "kernel_x86_64",' "$F" | head -8
echo "--- relaunch build ---"
export BUILD_NUMBER=16302624
nohup bash -lc 'cd /build/gki; export BUILD_NUMBER=16302624; ./tools/bazel --output_user_root=/build/.bazel run --config=stamp //common:kernel_x86_64_dist -- --dist_dir=/build/dist; RC=$?; echo rebuild rc=$RC; cat /build/dist/kernel.release 2>/dev/null; if [ $RC -eq 0 ]; then echo BUILD_DISPLAY_OK; else echo BUILD_DISPLAY_FAIL; fi' > /build/build-display.log 2>&1 &
echo "STARTED pid=$!"
