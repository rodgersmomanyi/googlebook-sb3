#!/usr/bin/env python3
"""GbGfxDriver APK helper (python3 -I, builder).

  add-libs <in.apk> <out.apk> <libdir>   copy in.apk and append every <libdir>/*.so as lib/x86_64/<name>, STORED
                                         (uncompressed), entries in name order, fixed timestamp (1980-01-01)
  check <apk>                            GD-03 / GD-1B-11 static checks: lib/x86_64/{vulkan.intel.so,libgb_nvk.so}
                                         present, STORED, data offset 16 KiB aligned, assets/sphal_libraries.txt
                                         present, no classes.dex, no FORBIDDEN member; prints one line per entry;
                                         exit 1 on a failed check

FORBIDDEN (INTERFACES v1.2 24.5): assets/allowlist.txt. GpuService copies that asset into
Settings.Global updatable_driver_production_allowlist at every boot and on every change of the driver package
(a17 GpuService.java:266-277); an entry "*" would hand the driver to every app that is not opted out, and the
"games + selected" policy could not be expressed. add-libs refuses an input APK that has one, check fails on it, and
any member named allowlist.txt anywhere in the APK counts.
"""
import os
import struct
import sys
import zipfile

PAGE = 16384
REQUIRED = ["lib/x86_64/vulkan.intel.so", "lib/x86_64/libgb_nvk.so", "assets/sphal_libraries.txt",
            "AndroidManifest.xml"]
FORBIDDEN = ["assets/allowlist.txt"]


def forbidden(names):
    """Members that must not be in the APK: FORBIDDEN, or any member whose base name is allowlist.txt."""
    return [n for n in names if n in FORBIDDEN or n.rsplit("/", 1)[-1] == "allowlist.txt"]


def add_libs(src, dst, libdir):
    with zipfile.ZipFile(src) as zi:
        bad = forbidden(zi.namelist())
    if bad:
        print("add-libs: REFUSED, forbidden member(s) in %s: %s" % (src, ", ".join(bad)))
        return 1
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w") as zo:
        for info in zi.infolist():
            zo.writestr(info, zi.read(info.filename))
        for name in sorted(os.listdir(libdir)):
            if not name.endswith(".so"):
                continue
            zinfo = zipfile.ZipInfo("lib/x86_64/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            zinfo.compress_type = zipfile.ZIP_STORED
            zinfo.external_attr = 0o644 << 16
            with open(os.path.join(libdir, name), "rb") as f:
                zo.writestr(zinfo, f.read())
    print("add-libs: %s" % dst)
    return 0


def data_offset(path, info):
    with open(path, "rb") as f:
        f.seek(info.header_offset)
        hdr = f.read(30)
        sig, _, _, _, _, _, _, _, _, nlen, xlen = struct.unpack("<IHHHHHIIIHH", hdr)
        assert sig == 0x04034B50
        return info.header_offset + 30 + nlen + xlen


def check(path):
    bad = 0
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for req in REQUIRED:
            if req not in names:
                print("FAIL missing %s" % req)
                bad += 1
        if any(n.endswith(".dex") for n in names):
            print("FAIL contains dex code (hasCode=false expected)")
            bad += 1
        for n in forbidden(names):
            print("FAIL forbidden member %s (INTERFACES 24.5)" % n)
            bad += 1
        if not forbidden(names):
            print("ok   no %s (INTERFACES 24.5)" % ", ".join(FORBIDDEN))
        for info in z.infolist():
            off = data_offset(path, info)
            line = "%-40s %8d bytes %-8s offset %8d" % (info.filename, info.file_size,
                                                       "STORED" if info.compress_type == 0 else "DEFLATED", off)
            if info.filename.endswith(".so"):
                ok = info.compress_type == zipfile.ZIP_STORED and off % PAGE == 0
                line += "  %s (stored, %d KiB aligned)" % ("ok" if ok else "FAIL", PAGE // 1024)
                bad += 0 if ok else 1
            print(line)
    print("RESULT: %s" % ("PASS" if not bad else "FAIL (%d)" % bad))
    return 1 if bad else 0


def main():
    if len(sys.argv) == 5 and sys.argv[1] == "add-libs":
        return add_libs(sys.argv[2], sys.argv[3], sys.argv[4])
    if len(sys.argv) == 3 and sys.argv[1] == "check":
        return check(sys.argv[2])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
