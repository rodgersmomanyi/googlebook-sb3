"""k16 acceptance helpers (builder; python3 -I). Reuses k14check.py (Elf, builtin_cert, versions) by path.

  k16check.py modcrc <dist16> <lapis mfd-core.ko> [...]
      K-16. Natively the card-reader modules (rtsx_usb, rtsx_pci) link against lapis's own mfd-core (lapis's first stage
      loads it; ours is never staged). For every symbol a card-reader module imports from mfd-core, the CRC in its
      __versions / __version_ext_* must equal the CRC that each given lapis mfd-core.ko exports (__crc_<sym> in its
      __kcrctab*), and our dist's Module.symvers CRC for it.
  k16check.py cert <dist15> <dist16>
      K-18. The module-signing certificate built into vmlinux (system_certificate_list, module_cert_size) is
      byte-identical in both dists.
Exit status 0 = no problems.
"""
import os, struct, sys

sys.path.insert(0, "/build/k14/tools")
import k14check as K  # noqa: E402

CARD = ("rtsx_usb", "rtsx_pci", "rtsx_usb_sdmmc", "rtsx_pci_sdmmc")


def exported_crcs(path):
    """{symbol: crc} of a module's exports: the __crc_<sym> labels (each a .long in its ___kcrctab section)."""
    e = K.Elf(path)
    out = {}
    for n, v, shndx, bind in e.symbols():
        if n.startswith("__crc_") and 0 < shndx < len(e.sections):
            s = e.sections[shndx]
            out[n[len("__crc_"):]] = struct.unpack_from("<I", e.data, s["off"] + v)[0]
    return out


def cmd_modcrc(dist, lapis_kos):
    symvers = K.load_symvers(os.path.join(dist, "kernel_x86_64_Module.symvers"))
    lap = {p: exported_crcs(p) for p in lapis_kos}
    for p, c in lap.items():
        print("lapis %s: %d exported CRCs (%s)" % (p, len(c), ", ".join(sorted(c))))
    bad = n = 0
    for m in CARD:
        req, clash = K.versions(K.Elf(os.path.join(dist, m + ".ko")))
        for s in clash:
            print("%s: BASIC/EXT CRC disagree for %s" % (m, s)); bad += 1
        for s, crc in sorted(req.items()):
            src = symvers.get(s, (None, ""))
            if not src[1].endswith("mfd-core"):
                continue
            n += 1
            ok = src[0] == crc and all(c.get(s) == crc for c in lap.values())
            print("%s imports %s from mfd-core: module %#010x, our Module.symvers %#010x, lapis %s: %s" % (
                m, s, crc, src[0], " ".join("%#010x" % c.get(s, 0) for c in lap.values()), "ok" if ok else "MISMATCH"))
            bad += 0 if ok else 1
    print("card-reader imports from mfd-core: %d, CRC mismatches against lapis's mfd-core (%d copies): %d" % (n, len(lap), bad))
    return bad if n else 1


def cmd_cert(d15, d16):
    a = K.builtin_cert(K.Elf(os.path.join(d15, "vmlinux")))
    b = K.builtin_cert(K.Elf(os.path.join(d16, "vmlinux")))
    import hashlib
    print("k15 certificate: %d bytes DER sha256 %s" % (len(a), hashlib.sha256(a).hexdigest()))
    print("k16 certificate: %d bytes DER sha256 %s" % (len(b), hashlib.sha256(b).hexdigest()))
    print("built-in module-signing certificate: %s" % ("identical to k15's" if a == b else "DIFFERS from k15's"))
    return 0 if a == b else 1


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    cmd, a = sys.argv[1], sys.argv[2:]
    if cmd == "modcrc":
        rc = cmd_modcrc(a[0], a[1:])
    elif cmd == "cert":
        rc = cmd_cert(a[0], a[1])
    else:
        sys.exit(__doc__)
    sys.exit(1 if rc else 0)


if __name__ == "__main__":
    main()
