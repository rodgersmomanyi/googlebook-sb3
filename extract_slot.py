"""Extract the boot ramdisks of one A/B slot from lapis.bin into boot/slot_<x>/ for build_esp.py.

An OTA (update_engine) writes the new init_boot/vendor_boot images to the inactive slot. Our ESP boots its own GKI
kernel, so to boot the updated slot it needs that slot's generic ramdisk (init_boot_<x>), vendor ramdisk and vendor
bootconfig (vendor_boot_<x>), plus androidboot.slot_suffix=_<x>.

Usage: python -I extract_slot.py a|b [--check]     (--check: compare with boot/*.lz4 instead of writing)
"""
import os, struct, sys, uuid

HERE = os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__))   # workspace (README: GB_HOME)
LAPIS = os.path.join(HERE, "lapis.bin")
SECTOR = 512


def partitions(path=LAPIS):
    with open(path, "rb") as f:
        f.seek(SECTOR); hdr = f.read(92)
        assert hdr[:8] == b"EFI PART", "no GPT"
        ent_lba, n, esz = struct.unpack_from("<QII", hdr, 72)
        f.seek(ent_lba * SECTOR); raw = f.read(n * esz)
    parts = {}
    for i in range(n):
        e = raw[i * esz:(i + 1) * esz]
        if e[:16] == b"\0" * 16:
            continue
        first, last = struct.unpack_from("<QQ", e, 32)
        parts[e[56:128].decode("utf-16-le").rstrip("\0")] = (first * SECTOR, (last - first + 1) * SECTOR)
    return parts


def slot_attrs(path=LAPIS):
    """{'a'|'b': (priority, tries, successful)} from the GPT attributes of vbmeta_<x>, where the android-desktop
    boot_control HAL keeps its ChromeOS-style slot state (bits 48-51 priority, 52-55 tries, 56 successful)."""
    with open(path, "rb") as f:
        f.seek(SECTOR); hdr = f.read(92)
        ent_lba, n, esz = struct.unpack_from("<QII", hdr, 72)
        f.seek(ent_lba * SECTOR); raw = f.read(n * esz)
    out = {}
    for i in range(n):
        e = raw[i * esz:(i + 1) * esz]
        name = e[56:128].decode("utf-16-le").rstrip("\0")
        if name in ("vbmeta_a", "vbmeta_b"):
            a = struct.unpack_from("<Q", e, 48)[0]
            out[name[-1]] = ((a >> 48) & 15, (a >> 52) & 15, (a >> 56) & 1)
    return out


def boot_slot(path=LAPIS):
    """The slot to boot: highest priority among slots marked successful (a slot an update activated but that never
    booted -- e.g. rolled back -- has tries left but is not successful, and must not win); else highest priority
    with tries left; else a."""
    try:
        at = slot_attrs(path)
    except (OSError, struct.error):
        return "a"
    for ok in (lambda p, t, s: p and s, lambda p, t, s: p and t):
        cand = [x for x in "ab" if x in at and ok(*at[x])]
        if cand:
            return max(cand, key=lambda x: (at[x][0], x == "a"))
    return "a"


def slot_ramdisks(slot, path=LAPIS):
    """(generic ramdisk, vendor ramdisk, vendor bootconfig) of a slot, read from its init_boot/vendor_boot."""
    parts = partitions(path)
    gen = generic_ramdisk(read_part("init_boot_" + slot, parts))
    ven, bc = vendor_parts(read_part("vendor_boot_" + slot, parts))
    return gen, ven, bc.rstrip(b"\0")


def read_part(name, parts):
    off, size = parts[name]
    with open(LAPIS, "rb") as f:
        f.seek(off); return f.read(size)


def pad(n, page):
    return (n + page - 1) // page * page


def generic_ramdisk(img):
    """init_boot.img (boot image header v4, page 4096, no kernel): the ramdisk follows the header page."""
    assert img[:8] == b"ANDROID!", "init_boot: bad magic"
    kernel_size, ramdisk_size = struct.unpack_from("<II", img, 8)
    version = struct.unpack_from("<I", img, 40)[0]
    assert version >= 3, f"init_boot header v{version}"
    page = 4096
    start = page + pad(kernel_size, page)
    return img[start:start + ramdisk_size]


def vendor_parts(img):
    """vendor_boot.img v4: (all vendor ramdisks concatenated, bootconfig)."""
    assert img[:8] == b"VNDRBOOT", "vendor_boot: bad magic"
    version, page = struct.unpack_from("<II", img, 8)
    vr_size = struct.unpack_from("<I", img, 24)[0]
    header_size, dtb_size = struct.unpack_from("<II", img, 2096)
    table_size, _, _, bc_size = struct.unpack_from("<IIII", img, 2112)
    assert version >= 4, f"vendor_boot header v{version}"
    o = pad(header_size, page)
    ramdisk = img[o:o + vr_size]
    o += pad(vr_size, page) + pad(dtb_size, page) + pad(table_size, page)
    return ramdisk, img[o:o + bc_size]


def main():
    slot = sys.argv[1].lower().lstrip("_")
    assert slot in ("a", "b"), "slot must be a or b"
    parts = partitions()
    gen = generic_ramdisk(read_part("init_boot_" + slot, parts))
    ven, bc = vendor_parts(read_part("vendor_boot_" + slot, parts))
    bc = bc.rstrip(b"\0")
    print(f"slot {slot}: generic ramdisk {len(gen)} B, vendor ramdisk {len(ven)} B, bootconfig {len(bc)} B")
    if "--check" in sys.argv:
        boot = os.path.join(HERE, "boot")
        for name, data in (("generic_ramdisk.lz4", gen), ("vendor_ramdisk.lz4", ven), ("vendor_bootconfig.txt", bc)):
            ref = open(os.path.join(boot, name), "rb").read()
            print(f"  {name}: {'IDENTICAL' if ref == data else f'DIFFERS (ref {len(ref)} B)'}")
        return
    out = os.path.join(HERE, "boot", "slot_" + slot)
    os.makedirs(out, exist_ok=True)
    for name, data in (("generic_ramdisk.lz4", gen), ("vendor_ramdisk.lz4", ven), ("vendor_bootconfig.txt", bc)):
        open(os.path.join(out, name), "wb").write(data)
    print("wrote", out)


if __name__ == "__main__":
    main()
