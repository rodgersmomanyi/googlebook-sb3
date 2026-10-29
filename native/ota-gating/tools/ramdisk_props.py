"""Read-only: list the build.prop-like files and fingerprint properties inside slot a/b's generic (init_boot) and
vendor (vendor_boot) ramdisks of lapis.bin. Writes nothing (prints only).
  python -I -B ramdisk_props.py [--list]"""
import importlib.util, os, re, struct, sys
import lz4.block

HERE = r"C:\GB-x86"
spec = importlib.util.spec_from_file_location("extract_slot", os.path.join(HERE, "extract_slot.py"))
XS = importlib.util.module_from_spec(spec); spec.loader.exec_module(XS)


def unlz4_legacy(buf):
    out, o = bytearray(), 0
    while o < len(buf):
        if buf[o:o + 4] == b"\x02\x21\x4c\x18":
            o += 4; continue
        if o + 4 > len(buf):
            break
        n = struct.unpack_from("<I", buf, o)[0]; o += 4
        if n == 0 or o + n > len(buf):
            break
        out += lz4.block.decompress(buf[o:o + n], uncompressed_size=8 << 20); o += n
    return bytes(out)


def decomp(buf):
    if buf[:4] == b"\x02\x21\x4c\x18":
        return unlz4_legacy(buf)
    if buf[:2] == b"\x1f\x8b":
        import gzip; return gzip.decompress(buf)
    if buf[:6] in (b"070701", b"070702"):
        return buf
    raise SystemExit(f"unknown ramdisk format {buf[:8]!r}")


def cpio(buf):
    o, out = 0, []
    while o + 110 <= len(buf):
        if buf[o:o + 6] not in (b"070701", b"070702"):
            # concatenated archives: skip zero padding
            o += 1; continue
        f = [int(buf[o + 6 + 8 * i:o + 14 + 8 * i], 16) for i in range(13)]
        mode, fsz, nsz = f[1], f[6], f[11]
        name = buf[o + 110:o + 110 + nsz - 1].decode("utf-8", "replace")
        o = (o + 110 + nsz + 3) & ~3
        data = buf[o:o + fsz]
        o = (o + fsz + 3) & ~3
        if name == "TRAILER!!!":
            continue
        out.append((name, mode, data))
    return out


def main():
    lst = "--list" in sys.argv
    print("boot slot (extract_slot.boot_slot):", XS.boot_slot(), "attrs", XS.slot_attrs())
    for slot in "ab":
        try:
            gen, ven, bc = XS.slot_ramdisks(slot)
        except Exception as e:
            print(f"slot {slot}: unreadable: {e}"); continue
        for kind, rd in (("generic", gen), ("vendor", ven)):
            ents = cpio(decomp(rd))
            print(f"== slot {slot} {kind} ramdisk: {len(rd)} B packed, {len(ents)} entries")
            for n, m, d in ents:
                if lst:
                    print(f"   {oct(m)} {len(d):>9} {n}")
                if re.search(r"(^|/)(build|default|prop)[^/]*\.prop$|ramdisk/build\.prop$", n) and d:
                    for ln in d.decode("utf-8", "replace").splitlines():
                        if "fingerprint" in ln or ln.startswith(("ro.bootimage.build.id", "ro.bootimage.build.version.incremental")):
                            print(f"   {n}: {ln}")
        print(f"== slot {slot} vendor bootconfig fingerprint lines:",
              [l for l in bc.decode("utf-8", "replace").splitlines() if "finger" in l or "build" in l][:5])


if __name__ == "__main__":
    main()
