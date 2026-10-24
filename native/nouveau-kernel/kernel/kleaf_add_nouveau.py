#!/usr/bin/env python3
"""Adds nouveau (NVIDIA TU117) to the android17-6.18 GKI Kleaf build, idempotently.

Usage (lead, for kernel build #12):  python3 kleaf_add_nouveau.py /build/gki/common
  - arch/x86/configs/gki_defconfig: appends the nouveau lines and drops "# CONFIG_FW_CACHE is not set"
    (nouveau's Kconfig does "select FW_CACHE if PM_SLEEP"; the select wins anyway, dropping the line just keeps the
    defconfig consistent with the resulting .config).
  - BUILD.bazel: appends the 5 new .ko paths to kernel_x86_64's module_implicit_outs AND modules_superset
    (Kleaf fails on a built-but-unlisted module, and both lists must name it).
Already-present entries are skipped, so re-running is harmless. Prints what it changed.
"""
import re
import sys

ADD_CONFIG = [
    "CONFIG_DRM_NOUVEAU=m",
    "# CONFIG_DRM_NOUVEAU_CH7006 is not set",
    "# CONFIG_DRM_NOUVEAU_SIL164 is not set",
]
DROP_CONFIG = ["# CONFIG_FW_CACHE is not set"]
MODULES = [
    "drivers/gpu/drm/nouveau/nouveau.ko",
    "drivers/gpu/drm/drm_exec.ko",
    "drivers/gpu/drm/drm_gpuvm.ko",
    "drivers/gpu/drm/scheduler/gpu-sched.ko",
    "drivers/platform/x86/mxm-wmi.ko",
]
# already listed by the earlier native builds and needed by nouveau too (checked, not added):
EXPECTED_PRESENT = [
    "drivers/gpu/drm/drm_ttm_helper.ko",
    "drivers/gpu/drm/display/drm_display_helper.ko",
    "drivers/i2c/algos/i2c-algo-bit.ko",
    "drivers/acpi/video.ko",
    "drivers/platform/x86/wmi.ko",
]


def edit_defconfig(path):
    lines = open(path).read().splitlines()
    out = [l for l in lines if l.strip() not in DROP_CONFIG]
    dropped = len(lines) - len(out)
    have = {l.strip() for l in out}
    added = [l for l in ADD_CONFIG if l not in have]
    out += added
    open(path, "w").write("\n".join(out) + "\n")
    print("gki_defconfig: added %d line(s) %s, dropped %d line(s)" % (len(added), added, dropped))


def add_to_list(src, attr):
    """Appends MODULES to the list literal of `attr = get_..._x86_64... + [...]` inside common_kernel(kernel_x86_64)."""
    start = src.find('name = "kernel_x86_64",')
    assert start >= 0, "kernel_x86_64 not found"
    m = re.compile(r"\n(\s*)%s = ([^\n]*)\[(.*?)\],\n" % re.escape(attr), re.S).search(src, start)
    assert m, attr + " not found after kernel_x86_64"
    assert 'x86_64' in m.group(2), "unexpected %s expression: %s" % (attr, m.group(2))
    items = m.group(3)
    new = [mod for mod in MODULES if '"%s"' % mod not in items]
    for mod in EXPECTED_PRESENT:
        if '"%s"' % mod not in items:
            print("WARNING: %s does not list %s (nouveau needs it)" % (attr, mod))
    if not new:
        return src, 0
    items2 = items.rstrip() + (", " if items.strip() else "") + ", ".join('"%s"' % x for x in new)
    repl = "\n%s%s = %s[%s],\n" % (m.group(1), attr, m.group(2), items2)
    return src[:m.start()] + repl + src[m.end():], len(new)


def edit_build(path):
    src = open(path).read()
    total = 0
    for attr in ("module_implicit_outs", "modules_superset"):
        src, n = add_to_list(src, attr)
        print("BUILD.bazel %s: added %d module(s)" % (attr, n))
        total += n
    if total:
        open(path, "w").write(src)


if __name__ == "__main__":
    common = sys.argv[1] if len(sys.argv) > 1 else "/build/gki/common"
    edit_defconfig(common + "/arch/x86/configs/gki_defconfig")
    edit_build(common + "/BUILD.bazel")
