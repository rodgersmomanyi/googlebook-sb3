"""Verify a built Googlebook disk image (build_usb.py output) before flashing:
  - protective MBR, primary + backup GPT headers and entry-array CRC32s, entries inside the usable range, no overlaps
  - the expected partition names, vbmeta_a/b slot attributes (boot slot successful)
  - the ESP's boot entry: androidboot.boot_part_uuid == the image's super partition GUID, gb.hw=native
  - copied partitions identical to lapis.bin (full compare for the small ones, first/last 64 MiB + 16 samples for super)
  - blank partitions zero at start and end
Read-only. Usage: python -I verify_image.py [image]
"""
import hashlib, os, struct, sys, uuid, zlib

SECTOR = 512
HERE = os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__))   # workspace (README: GB_HOME)
IMG = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out", "googlebook-sb3-usb.img")
LAPIS = os.path.join(HERE, "lapis.bin")
EXPECT = ["EFI-SYSTEM", "boot_a", "boot_b", "init_boot_a", "init_boot_b", "vendor_boot_a", "vendor_boot_b", "vbmeta_a",
          "vbmeta_b", "pvmfw_a", "pvmfw_b", "misc", "metadata", "frp_persist", "desktop_security_persist",
          "desktop_security_storage", "super", "userdata"]
COPIED = ["boot_a", "boot_b", "init_boot_a", "init_boot_b", "vendor_boot_a", "vendor_boot_b", "vbmeta_a", "vbmeta_b",
          "pvmfw_a", "pvmfw_b", "super"]
BLANK = ["misc", "metadata", "frp_persist", "desktop_security_persist", "desktop_security_storage", "userdata"]
problems = []


def bad(msg):
    problems.append(msg); print("PROBLEM:", msg)


def gpt(f, lba):
    f.seek(lba * SECTOR); h = f.read(92)
    if h[:8] != b"EFI PART":
        bad(f"no GPT header at LBA {lba}"); return None
    hsize = struct.unpack_from("<I", h, 12)[0]
    crc = struct.unpack_from("<I", h, 16)[0]
    hh = bytearray(h[:hsize]); struct.pack_into("<I", hh, 16, 0)
    if zlib.crc32(bytes(hh)) != crc:
        bad(f"header CRC mismatch at LBA {lba}")
    my, alt, first, last = struct.unpack_from("<QQQQ", h, 24)
    ent_lba, n, esz, ecrc = struct.unpack_from("<QIII", h, 72)
    f.seek(ent_lba * SECTOR); raw = f.read(n * esz)
    if zlib.crc32(raw) != ecrc:
        bad(f"entry array CRC mismatch (header at LBA {lba})")
    parts = {}
    for i in range(n):
        e = raw[i * esz:(i + 1) * esz]
        if e[:16] == bytes(16):
            continue
        a, b = struct.unpack_from("<QQ", e, 32)
        parts[e[56:128].decode("utf-16-le").rstrip("\0")] = dict(
            type=str(uuid.UUID(bytes_le=e[:16])), guid=str(uuid.UUID(bytes_le=e[16:32])), first=a, last=b,
            attrs=struct.unpack_from("<Q", e, 48)[0])
    return dict(my=my, alt=alt, first=first, last=last, parts=parts, raw=raw)


def region(f, p, off, n):
    f.seek(p["first"] * SECTOR + off); return f.read(n)


def main():
    size = os.path.getsize(IMG)
    print(f"image {IMG}: {size / 2**30:.2f} GiB")
    with open(IMG, "rb") as f, open(LAPIS, "rb") as lap:
        mbr = f.read(SECTOR)
        if mbr[510:512] != b"\x55\xaa" or mbr[450] != 0xEE:
            bad("protective MBR missing")
        p = gpt(f, 1)
        if not p:
            return
        b = gpt(f, p["alt"])
        if b and (b["raw"] != p["raw"] or b["alt"] != 1):
            bad("backup GPT differs from primary")
        if p["alt"] != size // SECTOR - 1:
            bad(f"backup header LBA {p['alt']} is not the last sector")
        parts = p["parts"]
        names = list(parts)
        for n in EXPECT:
            if n not in parts:
                bad(f"partition {n} missing")
        spans = sorted((v["first"], v["last"], k) for k, v in parts.items())
        for (a1, b1, k1), (a2, b2, k2) in zip(spans, spans[1:]):
            if a2 <= b1:
                bad(f"{k1} overlaps {k2}")
        for a, b_, k in spans:
            if a < p["first"] or b_ > p["last"] or a % 2048:
                bad(f"{k} outside the usable range or not 1 MiB aligned")
        print(f"GPT ok: {len(names)} partitions, primary + backup CRCs valid")

        # slot attributes
        for s in "ab":
            at = parts.get("vbmeta_" + s, {}).get("attrs", 0)
            print(f"  vbmeta_{s}: priority={(at >> 48) & 15} tries={(at >> 52) & 15} successful={(at >> 56) & 1}")
        if not any(((parts.get("vbmeta_" + s, {}).get("attrs", 0) >> 56) & 1) for s in "ab"):
            bad("no slot marked successful in vbmeta attributes (boot_control HAL would find no bootable slot)")

        # ESP boot entry
        esp = parts["EFI-SYSTEM"]
        data = region(f, esp, 0, (esp["last"] - esp["first"] + 1) * SECTOR)
        sup = parts["super"]["guid"].lower()
        i = data.find(b"androidboot.boot_part_uuid=")
        if i < 0:
            bad("ESP has no entry with androidboot.boot_part_uuid")
        else:
            val = data[i + 27:i + 27 + 36].decode()
            print(f"  ESP entry boot_part_uuid={val}  super GUID={sup}  {'MATCH' if val == sup else 'MISMATCH'}")
            if val != sup:
                bad("boot_part_uuid does not name this image's super partition")
        for needle in (b"gb.hw=native", b"efi_pstore.pstore_disable=1", b"module_blacklist=", b"reboot=pci"):
            if needle not in data:
                bad(f"ESP entry lacks {needle.decode()}")

        # copied partitions vs lapis.bin
        lap.seek(SECTOR); lh = lap.read(92)
        le, ln, les = struct.unpack_from("<QII", lh, 72)
        lap.seek(le * SECTOR); lraw = lap.read(ln * les)
        lp = {}
        for k in range(ln):
            e = lraw[k * les:(k + 1) * les]
            if e[:16] != bytes(16):
                a, b_ = struct.unpack_from("<QQ", e, 32)
                lp[e[56:128].decode("utf-16-le").rstrip("\0")] = (a, b_)
        for n in COPIED:
            if n not in parts or n not in lp:
                continue
            nbytes = (lp[n][1] - lp[n][0] + 1) * SECTOR
            if n == "super":
                chunks = [(0, 64 << 20), (nbytes - (64 << 20), 64 << 20)] + \
                         [(k * (nbytes // 16) // (1 << 20) * (1 << 20), 1 << 20) for k in range(1, 16)]
            else:
                chunks = [(0, nbytes)]
            same = True
            for off, ln_ in chunks:
                lap.seek(lp[n][0] * SECTOR + off)
                if region(f, parts[n], off, ln_) != lap.read(ln_):
                    same = False; break
            print(f"  {n:<14} {'identical to lapis.bin' if same else 'DIFFERS'}{' (sampled)' if n == 'super' else ''}")
            if not same:
                bad(f"{n} differs from lapis.bin")
        for n in BLANK:
            pp = parts[n]
            ln_ = (pp["last"] - pp["first"] + 1) * SECTOR
            if region(f, pp, 0, 1 << 20).strip(b"\0") or region(f, pp, ln_ - (1 << 20), 1 << 20).strip(b"\0"):
                bad(f"blank partition {n} is not zero")
        print(f"  blank partitions zeroed: {', '.join(BLANK)}")
    print("RESULT:", "OK" if not problems else f"{len(problems)} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
