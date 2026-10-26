"""set_attrs.py - TEST HELPER (image files only): set the GPT attribute field of named partitions in a raw disk image,
in the primary and the backup GPT, with the entry-array and header CRC32s recomputed (wave 3, item 7 image tests).

  python -I set_attrs.py IMAGE NAME=HEX [NAME=HEX ...]        e.g. vbmeta_a=0x010f000000000000 vbmeta_b=0

Refuses device paths. Prints the old and the new value of every changed entry.
"""
import struct, sys, zlib

SECTOR = 512


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    img = sys.argv[1]
    if img.startswith(("\\\\.\\", "\\\\?\\", "//./", "/dev/")) or "PhysicalDrive" in img:
        sys.exit("set_attrs: image files only")
    want = {}
    for a in sys.argv[2:]:
        n, v = a.split("=", 1)
        want[n] = int(v, 16)
    with open(img, "r+b") as f:
        f.seek(SECTOR)
        h = bytearray(f.read(SECTOR))
        if h[:8] != b"EFI PART":
            sys.exit("set_attrs: no GPT header at LBA 1")
        alt = struct.unpack_from("<Q", h, 32)[0]
        done = {}
        for lba in (1, alt):
            f.seek(lba * SECTOR)
            h = bytearray(f.read(SECTOR))
            if h[:8] != b"EFI PART":
                sys.exit(f"set_attrs: no GPT header at LBA {lba}")
            hsz = struct.unpack_from("<I", h, 12)[0]
            ent_lba, num, esz = struct.unpack_from("<QII", h, 72)
            f.seek(ent_lba * SECTOR)
            ents = bytearray(f.read(num * esz))
            for i in range(num):
                e = ents[i * esz:(i + 1) * esz]
                name = e[56:128].decode("utf-16-le", "replace").split("\0")[0]
                if name in want:
                    old = struct.unpack_from("<Q", ents, i * esz + 48)[0]
                    struct.pack_into("<Q", ents, i * esz + 48, want[name])
                    done.setdefault(name, (old, want[name]))
            struct.pack_into("<I", h, 88, zlib.crc32(bytes(ents)) & 0xFFFFFFFF)
            struct.pack_into("<I", h, 16, 0)
            struct.pack_into("<I", h, 16, zlib.crc32(bytes(h[:hsz])) & 0xFFFFFFFF)
            f.seek(ent_lba * SECTOR)
            f.write(ents)
            f.seek(lba * SECTOR)
            f.write(h)
    missing = set(want) - set(done)
    for n, (o, v) in sorted(done.items()):
        print(f"{n}: attributes 0x{o:016x} -> 0x{v:016x}")
    if missing:
        sys.exit(f"set_attrs: no partition named {sorted(missing)}")


if __name__ == "__main__":
    main()
