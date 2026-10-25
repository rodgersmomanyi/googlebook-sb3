#!/usr/bin/env python3
"""Import/loader check of Android ELF objects against an unpacked lapis rootfs (read-only).

For every ELF given on the command line:
  * DT_NEEDED must be a subset of --allow (default: the rootfs LLNDK list + libhardware.so);
  * every undefined dynamic symbol (weak ones are reported but not fatal) must be defined by one of its DT_NEEDED
    libraries, looked up like bionic does for vendor code: <root>/system/lib64, <root>/vendor/lib64,
    <root>/apex/com.android.runtime/lib64/bionic (libc/libm/libdl live there on lapis).
  * the symbol version an import asks for (e.g. AHardwareBuffer_allocate@LIBNATIVEWINDOW) must exist on the provider.

Usage: python3 -I import_check.py --root <rootfs> [--nm <llvm-nm>] [--readelf <llvm-readelf>] [--allow a.so,b.so]
                                  [--extra-provider <lib.so>] <elf>...
--extra-provider adds a library that satisfies imports (used for libgb_nvk.so <- shim tests); it does not widen --allow.
Exit 0 = all checks pass, 1 = a check failed, 2 = usage/tool error.
"""
import argparse
import os
import re
import subprocess
import sys

NDK_BIN = "/build/ndk/toolchains/llvm/prebuilt/linux-x86_64/bin"


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise SystemExit("tool failed: %s\n%s" % (" ".join(cmd), p.stderr))
    return p.stdout


def needed(readelf, path):
    out = run([readelf, "-d", "-W", path])
    return re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", out)


def soname(readelf, path):
    m = re.search(r"\(SONAME\)\s+Library soname: \[([^\]]+)\]", run([readelf, "-d", "-W", path]))
    return m.group(1) if m else None


def undefined(nm, path):
    syms = []
    for line in run([nm, "-D", "--undefined-only", path]).splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        kind, name = parts[-2], parts[-1]
        syms.append((name, kind))
    return syms


def defined(nm, path):
    names = set()
    for line in run([nm, "-D", "--defined-only", path]).splitlines():
        parts = line.split()
        if len(parts) >= 3:
            names.add(parts[-1])
    return names


def resolve(root, lib):
    for d in ("system/lib64", "vendor/lib64", "apex/com.android.runtime/lib64/bionic"):
        p = os.path.join(root, d, lib)
        if os.path.lexists(p):
            real = os.path.realpath(p)
            if real.startswith("/apex/") or not os.path.exists(real):
                # absolute symlink inside the image (e.g. /system/lib64/libc.so -> /apex/...): rebase on the root
                target = os.readlink(p)
                if target.startswith("/"):
                    real = os.path.join(root, target.lstrip("/"))
            if os.path.exists(real):
                return real
    return None


def split_ver(name):
    # llvm-nm prints versioned imports as name@VER and versioned definitions as name@VER or name@@VER
    if "@" in name:
        base, _, ver = name.partition("@")
        return base, ver.lstrip("@")
    return name, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--nm", default=os.path.join(NDK_BIN, "llvm-nm"))
    ap.add_argument("--readelf", default=os.path.join(NDK_BIN, "llvm-readelf"))
    ap.add_argument("--allow", default="")
    ap.add_argument("--extra-provider", action="append", default=[])
    ap.add_argument("elfs", nargs="+")
    a = ap.parse_args()

    llndk_txt = os.path.join(a.root, "system/etc/llndk.libraries.txt")
    if a.allow:
        allow = set(x for x in a.allow.split(",") if x)
    else:
        if not os.path.exists(llndk_txt):
            print("ERROR: %s missing and no --allow given" % llndk_txt)
            return 2
        allow = set(l.strip() for l in open(llndk_txt) if l.strip() and not l.startswith("#"))
        allow.add("libhardware.so")
    print("allowed NEEDED: %s" % " ".join(sorted(allow)))

    fails = 0
    cache = {}
    for elf in a.elfs:
        print("== %s (SONAME %s)" % (elf, soname(a.readelf, elf)))
        need = needed(a.readelf, elf)
        print("   NEEDED: %s" % " ".join(need))
        bad = [n for n in need if n not in allow and not any(os.path.basename(e) == n for e in a.extra_provider)]
        if bad:
            print("   FAIL NEEDED outside the allowed set: %s" % " ".join(bad))
            fails += 1
        providers = {}
        for n in need:
            p = None
            for e in a.extra_provider:
                if os.path.basename(e) == n:
                    p = e
            p = p or resolve(a.root, n)
            if not p:
                print("   FAIL %s not found in the rootfs" % n)
                fails += 1
                continue
            if p not in cache:
                cache[p] = defined(a.nm, p)
            providers[n] = cache[p]
        syms = undefined(a.nm, elf)
        missing, weak_missing = [], []
        for name, kind in syms:
            base, ver = split_ver(name)
            ok = False
            for defs in providers.values():
                if ver:
                    if (base + "@" + ver) in defs or (base + "@@" + ver) in defs:
                        ok = True
                        break
                elif base in defs or any(d.split("@")[0] == base for d in defs if d.startswith(base + "@")):
                    ok = True
                    break
            if not ok:
                (weak_missing if kind in ("w", "v") else missing).append(name)
        print("   %d undefined dynamic symbols, %d unresolved, %d weak unresolved" %
              (len(syms), len(missing), len(weak_missing)))
        for m in missing:
            print("   FAIL unresolved: %s" % m)
        for m in weak_missing:
            print("   note weak unresolved: %s" % m)
        fails += len(missing)
    print("RESULT: %s" % ("PASS" if fails == 0 else "FAIL (%d)" % fails))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
