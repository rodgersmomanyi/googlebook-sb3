#!/bin/bash
cd /build/gki || exit 1
python3 - <<'PY'
f='common/BUILD.bazel'
s=open(f).read()
bad='    module_outs = [\n        "drivers/gpu/drm/sysfb/drm_sysfb_helper.ko",\n        "drivers/gpu/drm/vmwgfx/vmwgfx.ko",\n        "drivers/gpu/drm/sysfb/efidrm.ko",\n        "drivers/gpu/drm/drm_ttm_helper.ko",\n    ],\n'
if bad in s:
    s=s.replace(bad,'',1); print("reverted bad module_outs block")
mods=' + ["drivers/gpu/drm/sysfb/drm_sysfb_helper.ko", "drivers/gpu/drm/vmwgfx/vmwgfx.ko", "drivers/gpu/drm/sysfb/efidrm.ko", "drivers/gpu/drm/drm_ttm_helper.ko"]'
oi='    module_implicit_outs = get_gki_modules_list("x86_64") + get_kunit_modules_list("x86_64"),'
os='    modules_superset = get_gki_modules_superset("x86_64") + get_kunit_modules_superset("x86_64"),'
if 'drivers/gpu/drm/vmwgfx/vmwgfx.ko' not in s:
    assert s.count(oi)==1, ("oi",s.count(oi))
    assert s.count(os)==1, ("os",s.count(os))
    s=s.replace(oi, oi[:-1]+mods+',',1)
    s=s.replace(os, os[:-1]+mods+',',1)
    print("added 4 modules to module_implicit_outs + modules_superset")
else:
    print("modules already present")
open(f,'w').write(s)
PY
echo "--- verify parse (bazel query) ---"
export BUILD_NUMBER=16302624
if timeout 200 ./tools/bazel --output_user_root=/build/.bazel query //common:kernel_x86_64_dist >/dev/null 2>/tmp/q.err; then
  echo "PARSE_OK - launching build"
  nohup bash -lc 'cd /build/gki; export BUILD_NUMBER=16302624; ./tools/bazel --output_user_root=/build/.bazel run --config=stamp //common:kernel_x86_64_dist -- --dist_dir=/build/dist; RC=$?; echo rebuild rc=$RC; cat /build/dist/kernel.release 2>/dev/null; if [ $RC -eq 0 ]; then echo BUILD_DISPLAY_OK; else echo BUILD_DISPLAY_FAIL; fi' > /build/build-display.log 2>&1 &
  echo "STARTED pid=$!"
else
  echo "PARSE_FAIL:"; tail -12 /tmp/q.err
fi
