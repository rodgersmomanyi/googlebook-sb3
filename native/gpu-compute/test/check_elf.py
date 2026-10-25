#!/usr/bin/env python3
"""GC-01 ELF contract checks of the Android artifacts (INTERFACES 11.1/11.2), complementing import_check.py.

usage: python3 -I -B check_elf.py <out-android dir> <gb_vk_core_names.txt> [llvm-readelf] [llvm-nm]

  * every file: ELF64 x86-64, no TEXTREL, PT_LOAD alignment 16 KiB, no libc++_shared.so / libvulkan.so NEEDED
  * libgb_nvk_loader.so: SONAME libgb_nvk_loader.so; exports every Vulkan 1.0-1.3 core command and nothing that
    does not start with "vk"
  * libgb_gpu_compute.so: SONAME libgb_gpu_compute.so; DT_RUNPATH exactly $ORIGIN; NEEDED includes
    libgb_nvk_loader.so; exports only gb_gpu_compute_main
  * gb_gpu_compute (launcher): PIE executable, no RUNPATH
"""
import os
import re
import subprocess
import sys

NDK_BIN = "/build/ndk/toolchains/llvm/prebuilt/linux-x86_64/bin"
FAILS = []


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def check(ok, what):
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        FAILS.append(what)


def main():
    out, names = sys.argv[1], sys.argv[2]
    readelf = sys.argv[3] if len(sys.argv) > 3 else os.path.join(NDK_BIN, "llvm-readelf")
    nm = sys.argv[4] if len(sys.argv) > 4 else os.path.join(NDK_BIN, "llvm-nm")
    core = [l.strip() for l in open(names) if l.strip()]
    files = {"loader": os.path.join(out, "lib64/libgb_nvk_loader.so"),
             "service": os.path.join(out, "lib64/libgb_gpu_compute.so"),
             "launcher": os.path.join(out, "gb_gpu_compute")}
    info = {}
    for k, f in files.items():
        h = run([readelf, "-h", "-l", "-d", "-W", f])
        info[k] = h
        print("== %s" % f)
        check("ELF64" in h and "X86-64" in h.replace("Advanced Micro Devices X86-64", "X86-64"), "ELF64 x86-64")
        check("TEXTREL" not in h, "no TEXTREL")
        aligns = re.findall(r"^\s+LOAD\s+.*\s(0x[0-9a-f]+)\s*$", h, re.M)
        check(aligns and all(int(a, 16) >= 0x4000 for a in aligns), "PT_LOAD align >= 16 KiB (%s)" % aligns)
        need = re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", h)
        print("  NEEDED: %s" % " ".join(need))
        check(not any(n.startswith("libvulkan") for n in need), "no libvulkan*.so NEEDED")
        check("libc++_shared.so" not in need, "libc++ linked statically")
    son = lambda h: (re.findall(r"\(SONAME\)\s+Library soname: \[([^\]]+)\]", h) or [None])[0]  # noqa: E731
    rpath = lambda h: re.findall(r"\((?:RUNPATH|RPATH)\)\s+Library (?:runpath|rpath): \[([^\]]+)\]", h)  # noqa: E731
    check(son(info["loader"]) == "libgb_nvk_loader.so", "loader SONAME libgb_nvk_loader.so")
    check(son(info["service"]) == "libgb_gpu_compute.so", "service SONAME libgb_gpu_compute.so")
    check(rpath(info["service"]) == ["$ORIGIN"] and "(RUNPATH)" in info["service"], "service DT_RUNPATH = $ORIGIN")
    check(not rpath(info["launcher"]), "launcher has no RPATH/RUNPATH")
    check("DYN (Shared object file)" in info["launcher"] or "Position-Independent" in info["launcher"],
          "launcher is PIE")
    check("libgb_nvk_loader.so" in re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", info["service"]),
          "service NEEDED libgb_nvk_loader.so")
    ex = set(l.split()[-1].split("@")[0] for l in run([nm, "-D", "--defined-only", files["loader"]]).splitlines()
             if l.strip())
    missing = [n for n in core if n not in ex]
    extra = sorted(n for n in ex if not n.startswith("vk"))
    check(not missing, "loader exports all %d core commands (missing %s)" % (len(core), missing[:5]))
    check(not extra, "loader exports only vk* (extra: %s)" % extra[:8])
    sx = sorted(set(l.split()[-1].split("@")[0] for l in run([nm, "-D", "--defined-only", files["service"]])
                    .splitlines() if l.strip()))
    check(sx == ["gb_gpu_compute_main"], "service exports only gb_gpu_compute_main (%s)" % sx[:8])
    print("CHECK_ELF %s (%d failures)" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
