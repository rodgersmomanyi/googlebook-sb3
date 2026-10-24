"""Test tooling for the dual-boot installer's image mode (independent of gbdisk.exe's GPT code).

  mkimage OUT [--c-mib 1024] [--d-mib 6144]       synthetic GPT disk image shaped like the Surface Book 3 disk 0
                                                   (ESP, MSR, C:, D:, 1 MiB gap, Recovery; same names/types/GUIDs/
                                                   attributes as the host), sparse, partitions filled with random
                                                   "data" blocks so that any stray write is detected
  snapshot IMAGE OUT.json                          GPT + a 64-bit BLAKE2b digest of every MiB of the image
  validate IMAGE --snapshot S --expect clean|installed|uninstalled [--plan P] [--source-dir D]
           clean        image byte-identical to the snapshot (e.g. after a dry run)
           installed    GPT valid (protective MBR, both headers, CRC32s, identical entry arrays); pre-existing
                        entries unchanged except the shrunk anchor; the planned partitions present exactly (names,
                        type GUIDs, partition GUIDs, attributes, 1 MiB alignment, no overlaps, inside the usable
                        area); sourced partitions' sha256 = the export files' sha256, the rest zeroed as planned;
                        every MiB outside the new partitions identical to the snapshot
           uninstalled  GPT headers and entry arrays byte-identical to the snapshot (anchor back at its original
                        size), every MiB outside the former Googlebook region identical to the snapshot
Exit status 0 = all checks passed.
"""
import argparse, ctypes, hashlib, json, os, random, struct, sys, uuid, zlib

SECTOR = 512
MiB = 1 << 20
ESP = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
MSR = "e3c9e316-0b5c-4db8-817d-f92df00215ae"
BASIC = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"
RECOVERY = "de94bba4-06d1-4d40-a16a-bfd50179d6ac"
# A Surface Book 3 Windows disk layout (names/types/attributes as `gbdisk layout --disk 0` shows them, GUIDs are
# placeholders), reproduced at reduced scale
HOST_DISK_GUID = "fa4a0927-7742-4757-841f-57cb7af65a0c"
HOST = [("EFI system partition", ESP, "e89e4eda-1cfb-4449-844a-065414d2ed6b", 0),
        ("Microsoft reserved partition", MSR, "1b9b51ab-7300-4ccb-85f5-2ab769e14566", 0),
        ("Basic data partition", BASIC, "55a0ab44-da24-4724-8f7d-37429b6baa75", 0),
        ("Basic data partition", BASIC, "c8521573-8190-49e6-b0fa-755a8894e87a", 0),
        ("Basic data partition", RECOVERY, "4f3c29ec-6ed4-44c8-b42e-fb0c535d4bcf", 0x8000000000000001)]
ZERO_MIB = bytes(MiB)
ZD = hashlib.blake2b(ZERO_MIB, digest_size=8).hexdigest()


# ------------------------------------------------------------------------------------------------ GPT parsing
def parse_header(sec, expect_lba):
    """dict of header fields + list of problems (empty = valid)."""
    p = []
    if sec[:8] != b"EFI PART":
        return None, ["no EFI PART signature"]
    rev, hsz, hcrc, rsv, my, alt, first, last = struct.unpack_from("<IIIIQQQQ", sec, 8)
    disk = str(uuid.UUID(bytes_le=bytes(sec[56:72])))
    elba, n, esz, ecrc = struct.unpack_from("<QIII", sec, 72)
    if rev != 0x00010000: p.append(f"revision {rev:#x}")
    if hsz != 92: p.append(f"header size {hsz}")
    t = bytearray(sec[:hsz]); t[16:20] = b"\0\0\0\0"
    if zlib.crc32(bytes(t)) != hcrc: p.append("header CRC32 mismatch")
    if rsv != 0: p.append("reserved field not zero")
    if any(sec[hsz:SECTOR]): p.append("non-zero bytes after the header")
    if my != expect_lba: p.append(f"MyLBA {my} != {expect_lba}")
    if esz != 128: p.append(f"entry size {esz}")
    return dict(rev=rev, hsz=hsz, hcrc=hcrc, my=my, alt=alt, first=first, last=last, disk=disk, elba=elba, n=n,
                esz=esz, ecrc=ecrc), p


def parse_entries(raw, n, esz):
    ents = []
    for i in range(n):
        e = raw[i * esz:(i + 1) * esz]
        if e[:16] == b"\0" * 16:
            if any(e): ents.append(dict(slot=i, bad="unused entry with non-zero bytes"))
            continue
        first, last, attrs = struct.unpack_from("<QQQ", e, 32)
        ents.append(dict(slot=i, type=str(uuid.UUID(bytes_le=bytes(e[:16]))), id=str(uuid.UUID(bytes_le=bytes(e[16:32]))),
                         first=first, last=last, offset=first * SECTOR, length=(last - first + 1) * SECTOR,
                         attributes=attrs, name=bytes(e[56:128]).decode("utf-16-le").split("\0")[0], raw=bytes(e).hex()))
    return ents


def read_gpt(f, size):
    """Full structural check. Returns (info, problems)."""
    probs = []
    last_lba = size // SECTOR - 1
    f.seek(0); mbr = f.read(SECTOR)
    if mbr[510:512] != b"\x55\xaa": probs.append("protective MBR: no 55AA signature")
    ptype = mbr[446 + 4]; pstart = struct.unpack_from("<I", mbr, 446 + 8)[0]; psize = struct.unpack_from("<I", mbr, 446 + 12)[0]
    if ptype != 0xEE or pstart != 1: probs.append(f"protective MBR: first entry type {ptype:#x} start {pstart}")
    if psize not in (min(last_lba, 0xFFFFFFFF),): probs.append(f"protective MBR: size {psize} != {min(last_lba, 0xFFFFFFFF)}")
    f.seek(SECTOR); h1, p1 = parse_header(f.read(SECTOR), 1)
    f.seek(last_lba * SECTOR); h2, p2 = parse_header(f.read(SECTOR), last_lba)
    probs += ["primary: " + x for x in p1] + ["backup: " + x for x in p2]
    if not h1 or not h2:
        return None, probs
    if h1["alt"] != last_lba: probs.append(f"primary AlternateLBA {h1['alt']} != last LBA {last_lba}")
    if h2["alt"] != 1: probs.append(f"backup AlternateLBA {h2['alt']} != 1")
    if h1["elba"] != 2: probs.append(f"primary entries LBA {h1['elba']} != 2")
    esec = (h1["n"] * h1["esz"] + SECTOR - 1) // SECTOR
    if h2["elba"] != last_lba - esec: probs.append(f"backup entries LBA {h2['elba']} != {last_lba - esec}")
    for k in ("first", "last", "disk", "n", "esz", "ecrc", "rev", "hsz"):
        if h1[k] != h2[k]: probs.append(f"primary/backup differ in {k}: {h1[k]} / {h2[k]}")
    if h1["first"] < 2 + esec: probs.append("FirstUsableLBA overlaps the primary entry array")
    if h1["last"] >= last_lba - esec: probs.append("LastUsableLBA overlaps the backup entry array")
    f.seek(h1["elba"] * SECTOR); r1 = f.read(h1["n"] * h1["esz"])
    f.seek(h2["elba"] * SECTOR); r2 = f.read(h2["n"] * h2["esz"])
    if zlib.crc32(r1) != h1["ecrc"]: probs.append("primary entry array CRC32 mismatch")
    if zlib.crc32(r2) != h2["ecrc"]: probs.append("backup entry array CRC32 mismatch")
    if r1 != r2: probs.append("primary and backup entry arrays differ")
    ents = parse_entries(r1, h1["n"], h1["esz"])
    for e in ents:
        if "bad" in e: probs.append(f"slot {e['slot']}: {e['bad']}")
    ents = [e for e in ents if "bad" not in e]
    ids = [e["id"] for e in ents]
    if len(set(ids)) != len(ids): probs.append("duplicate partition GUIDs")
    if any(i == "00000000-0000-0000-0000-000000000000" for i in ids): probs.append("zero partition GUID")
    s = sorted(ents, key=lambda e: e["first"])
    for i, e in enumerate(s):
        if e["first"] > e["last"]: probs.append(f"{e['name']}: first > last")
        if e["first"] < h1["first"] or e["last"] > h1["last"]: probs.append(f"{e['name']}: outside the usable LBAs")
        if i and e["first"] <= s[i - 1]["last"]: probs.append(f"overlap: {s[i - 1]['name']} / {e['name']}")
    f.seek(SECTOR); hraw1 = f.read(SECTOR)
    f.seek(last_lba * SECTOR); hraw2 = f.read(SECTOR)
    return dict(size=size, last_lba=last_lba, h1=h1, h2=h2, entries=ents, entries_raw_sha=hashlib.sha256(r1).hexdigest(),
                hdr1_sha=hashlib.sha256(hraw1).hexdigest(), hdr2_sha=hashlib.sha256(hraw2).hexdigest(),
                mbr_sha=hashlib.sha256(mbr).hexdigest()), probs


# ------------------------------------------------------------------------------------------------ mkimage
def set_sparse(f):
    if os.name != "nt":
        return False
    import msvcrt
    from ctypes import wintypes
    h = msvcrt.get_osfhandle(f.fileno())
    ret = wintypes.DWORD()
    ok = ctypes.windll.kernel32.DeviceIoControl(wintypes.HANDLE(h), 0x000900C4, None, 0, None, 0, ctypes.byref(ret), None)
    return bool(ok)


def gpt_entry(t, i, first, last, attrs, name):
    return (uuid.UUID(t).bytes_le + uuid.UUID(i).bytes_le + struct.pack("<QQQ", first, last, attrs)
            + name.encode("utf-16-le").ljust(72, b"\0"))


def gpt_header(my, alt, first, last, disk, elba, ecrc):
    h = bytearray(struct.pack("<8sIIIIQQQQ16sQIII", b"EFI PART", 0x00010000, 92, 0, 0, my, alt, first, last,
                              uuid.UUID(disk).bytes_le, elba, 128, 128, ecrc))
    struct.pack_into("<I", h, 16, zlib.crc32(bytes(h)))
    return bytes(h).ljust(SECTOR, b"\0")


def cmd_mkimage(a):
    host = list(HOST)
    sizes = [100 * MiB, 16 * MiB, a.c_mib * MiB, a.d_mib * MiB, 900 * MiB]
    gaps = [0, 0, 0, 0, MiB]                                   # host: 1 MiB unallocated between D: and Recovery
    # edge-case shapes (review tests): a foreign Linux-type partition with a Googlebook name between C: and D:, and/or
    # another data partition (E:) between D: and Recovery
    if a.foreign_before_d:
        name, mib = a.foreign_before_d.split(":")
        host.insert(3, (name, "0fc63daf-8483-4772-8e79-3d69d8477de4", "f0f0f0f0-1111-4222-8333-444455556666", 0))
        sizes.insert(3, int(mib) * MiB); gaps.insert(3, 0)
    if a.extra_after_d_mib:
        i = len(host) - 1                                      # before Recovery
        host.insert(i, ("Basic data partition", BASIC, "e0e0e0e0-1111-4222-8333-444455556666", 0))
        sizes.insert(i, a.extra_after_d_mib * MiB); gaps.insert(i, 0)
    offs, o = [], MiB
    for s, g in zip(sizes, gaps):
        o += g
        offs.append(o); o += s
    usable_end = o + 335360                                    # host: 0.32 MiB free after Recovery
    size = usable_end + 33 * SECTOR
    last_lba = size // SECTOR - 1
    ents = b"".join(gpt_entry(t, i, off // SECTOR, (off + s) // SECTOR - 1, at, n)
                    for (n, t, i, at), off, s in zip(host, offs, sizes)).ljust(128 * 128, b"\0")
    ecrc = zlib.crc32(ents)
    mbr = bytearray(SECTOR)
    mbr[446:462] = struct.pack("<BBBBBBBBII", 0, 0, 2, 0, 0xEE, 0xFF, 0xFF, 0xFF, 1, min(last_lba, 0xFFFFFFFF))
    mbr[510:512] = b"\x55\xAA"
    rnd = random.Random(a.seed)
    with open(a.out, "wb") as f:
        sparse = set_sparse(f)
        # no f.truncate(): the CRT extends files by writing zeros (allocating every cluster); writing the backup GPT
        # at the end extends the sparse file without allocating the gap
        f.seek(0); f.write(mbr)
        f.write(gpt_header(1, last_lba, 34, usable_end // SECTOR - 1, HOST_DISK_GUID, 2, ecrc)); f.write(ents)
        f.seek(size - 33 * SECTOR); f.write(ents)
        f.write(gpt_header(last_lba, 1, 34, usable_end // SECTOR - 1, HOST_DISK_GUID, last_lba - 32, ecrc))
        written = 0
        for off, s in zip(offs, sizes):
            # stand-in for file system data: first and last MiB in full, 64 KiB every 8 MiB in between
            spots = [(off, MiB), (off + s - MiB, MiB)] + [(x, 64 << 10) for x in range(off + MiB, off + s - MiB, 8 * MiB)]
            for x, n in spots:
                f.seek(x); f.write(rnd.randbytes(n)); written += n
        # the 1 MiB gap before Recovery: stale bytes too (must stay untouched)
        f.seek(offs[-1] - MiB); f.write(rnd.randbytes(64 << 10)); written += 64 << 10
    print(f"wrote {a.out}: {size} bytes ({size / 2**30:.2f} GiB), sparse={sparse}, {written / MiB:.0f} MiB of pattern data")
    for (n, t, i, at), off, s in zip(host, offs, sizes):
        print(f"  {n:<30} offset {off:>12} length {s:>12} type {t} id {i} attr {at:#018x}")


# ------------------------------------------------------------------------------------------------ snapshot
def unit_ranges(size):
    """(start, end) of every MiB unit, minus the GPT areas (LBA 0-33 and the last 33 LBAs)."""
    n = (size + MiB - 1) // MiB
    for u in range(n):
        s, e = u * MiB, min(size, (u + 1) * MiB)
        if u == 0: s = 34 * SECTOR
        if e > size - 33 * SECTOR: e = size - 33 * SECTOR
        yield u, s, e


def digests(path):
    size = os.path.getsize(path)
    out = []
    with open(path, "rb") as f:
        for u, s, e in unit_ranges(size):
            f.seek(s); b = f.read(e - s)
            out.append(ZD if (e - s == MiB and b == ZERO_MIB) else hashlib.blake2b(b, digest_size=8).hexdigest())
    return out


def cmd_snapshot(a):
    size = os.path.getsize(a.image)
    with open(a.image, "rb") as f:
        info, probs = read_gpt(f, size)
    if probs:
        print("snapshot: GPT problems:", *probs, sep="\n  "); return 1
    info["units"] = digests(a.image)
    with open(a.out, "w") as f:
        json.dump(info, f)
    print(f"snapshot {a.out}: {len(info['entries'])} partitions, {len(info['units'])} MiB units")
    return 0


# ------------------------------------------------------------------------------------------------ validate
class Report:
    def __init__(self): self.fails = 0; self.passes = 0
    def check(self, ok, msg):
        if ok: self.passes += 1; print("  PASS", msg)
        else: self.fails += 1; print("  FAIL", msg)
        return ok


def sha256_range(f, off, n):
    h = hashlib.sha256(); f.seek(off)
    while n:
        b = f.read(min(n, 16 * MiB)); h.update(b); n -= len(b)
        if not b: raise IOError("short read")
    return h.hexdigest()


def zero_range(f, off, n):
    f.seek(off)
    while n:
        b = f.read(min(n, 16 * MiB))
        if b.count(0) != len(b): return False
        n -= len(b)
    return True


def file_sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(16 * MiB), b""): h.update(b)
    return h.hexdigest()


def cmd_validate(a):
    snap = json.load(open(a.snapshot))
    size = os.path.getsize(a.image)
    r = Report()
    print(f"validate {a.image} (expect {a.expect}) against {a.snapshot}")
    with open(a.image, "rb") as f:
        info, probs = read_gpt(f, size)
        for p in probs: r.check(False, "GPT: " + p)
        r.check(info is not None and not probs,
                "GPT structure: protective MBR, primary + backup header CRC32, entry array CRC32, identical arrays, "
                "Alternate/Entries LBAs, usable range, no overlaps, unique GUIDs")
        if info is None:
            print(f"RESULT: FAIL ({r.fails} failed)"); return 1
        r.check(size == snap["size"], f"image size unchanged ({size})")
        r.check(info["h1"]["disk"] == snap["h1"]["disk"], f"disk GUID unchanged ({info['h1']['disk']})")
        r.check(info["h1"]["first"] == snap["h1"]["first"] and info["h1"]["last"] == snap["h1"]["last"],
                "usable LBA range unchanged")
        r.check(info["mbr_sha"] == snap["mbr_sha"], "protective MBR byte-identical")
        cur = {e["id"]: e for e in info["entries"]}
        old = {e["id"]: e for e in snap["entries"]}
        new_region = []                                    # (offset, end) of Googlebook partitions, from the plan
        plan = json.load(open(a.plan)) if a.plan else None
        if plan:
            for p in plan["partitions"]: new_region.append((p["offset"], p["offset"] + p["length"]))

        if a.expect in ("clean", "uninstalled"):
            r.check(info["hdr1_sha"] == snap["hdr1_sha"] and info["hdr2_sha"] == snap["hdr2_sha"],
                    "primary and backup GPT header sectors byte-identical to the snapshot")
            r.check(info["entries_raw_sha"] == snap["entries_raw_sha"], "GPT entry array byte-identical to the snapshot")
            r.check([(e["slot"], e["raw"]) for e in info["entries"]] == [(e["slot"], e["raw"]) for e in snap["entries"]],
                    f"same {len(snap['entries'])} entries in the same slots (anchor back at its original size)")
            if a.expect == "uninstalled":
                r.check(bool(plan), "--plan given (former Googlebook region)")
                gb = [e for e in info["entries"] if e["name"] in GB_NAMES]
                r.check(not gb, "no Googlebook-named partitions left")
        else:  # installed
            r.check(bool(plan), "--plan given")
            anchor = plan["anchor"]
            for oid, oe in old.items():
                ce = cur.get(oid)
                if not r.check(ce is not None, f"pre-existing #{oe['slot'] + 1} '{oe['name']}' {oid} still present"):
                    continue
                if oid == anchor["id"]:
                    same = (ce["slot"], ce["type"], ce["first"], ce["attributes"], ce["name"]) == \
                           (oe["slot"], oe["type"], oe["first"], oe["attributes"], oe["name"])
                    r.check(same and ce["length"] == anchor["length"] and ce["length"] <= oe["length"],
                            f"anchor '{ce['name']}' shrunk {oe['length']} -> {ce['length']} B, start/type/name/attrs/slot unchanged")
                else:
                    r.check(ce["raw"] == oe["raw"] and ce["slot"] == oe["slot"],
                            f"pre-existing entry slot {oe['slot']} '{oe['name']}' byte-identical")
            planned = {p["id"]: p for p in plan["partitions"]}
            extra = [e for e in info["entries"] if e["id"] not in old and e["id"] not in planned]
            r.check(not extra, "no unexpected partitions" + (f": {[e['name'] for e in extra]}" if extra else ""))
            for p in plan["partitions"]:
                e = cur.get(p["id"])
                if not r.check(e is not None, f"{p['name']}: present ({p['id']})"):
                    continue
                attrs = int(p["attributes"], 16)
                ok = (e["name"], e["type"], e["offset"], e["length"], e["attributes"]) == \
                     (p["name"], p["type"], p["offset"], p["length"], attrs)
                r.check(ok and e["offset"] % MiB == 0 and e["length"] % MiB == 0,
                        f"{p['name']:<26} name/type/GUID/attrs {attrs:#018x}/offset {e['offset']}/length {e['length']} as planned, 1 MiB aligned")
                if p["file"]:
                    want = p["sha256"]
                    if a.source_dir:
                        fs = file_sha256(os.path.join(a.source_dir, p["file"]))
                        r.check(fs == want, f"{p['name']:<26} export file {p['file']} sha256 matches the manifest")
                    got = sha256_range(f, e["offset"], p["file_size"])
                    r.check(got == want, f"{p['name']:<26} content sha256 {got[:16]}.. == source ({p['file_size']} B)")
                    tail = e["length"] - p["file_size"]
                    if tail: r.check(zero_range(f, e["offset"] + p["file_size"], tail), f"{p['name']:<26} alignment tail ({tail} B) zero")
                else:
                    L = e["length"]
                    if L <= 256 * MiB or a.zero_full:
                        r.check(zero_range(f, e["offset"], L), f"{p['name']:<26} blank: all {L // MiB} MiB zero")
                    else:
                        r.check(zero_range(f, e["offset"], 64 * MiB) and zero_range(f, e["offset"] + L - 64 * MiB, 64 * MiB),
                                f"{p['name']:<26} blank: first and last 64 MiB zero (old D: data overwritten)")
            # the planned set is contiguous, aligned, in the freed region, and leaves the reserve free
            s = sorted(plan["partitions"], key=lambda p: p["offset"])
            contiguous = all(s[i]["offset"] == s[i - 1]["offset"] + s[i - 1]["length"] for i in range(1, len(s)))
            r.check(contiguous, "Googlebook partitions contiguous")
            reg = plan["region"]
            end = s[-1]["offset"] + s[-1]["length"]
            r.check(s[0]["offset"] == reg["start"] and end <= reg["end"] - reg["reserve"],
                    f"inside the freed region, {(reg['end'] - end) // MiB} MiB left unallocated before the next partition")
            names = [e["name"] for e in info["entries"]]
            r.check(len(names) == len(set(names)) or all(n not in GB_NAMES for n in names if names.count(n) > 1),
                    "Googlebook partition names unique on the disk (by-name links unambiguous)")

        # byte-identity of everything outside the new partitions
        now = digests(a.image)
        units = snap["units"]
        r.check(len(now) == len(units), "unit count")
        changed = []
        for u, s_, e_ in unit_ranges(size):
            if now[u] == units[u]: continue
            inside = any(s_ >= ro and e_ <= re_ for ro, re_ in new_region)
            if a.expect == "clean" or not inside:
                changed.append(u)
        r.check(not changed, ("every MiB outside the Googlebook partitions byte-identical to the snapshot"
                              if a.expect != "clean" else "every MiB byte-identical to the snapshot") +
                (f" (changed MiB units: {changed[:10]}{'...' if len(changed) > 10 else ''})" if changed else
                 f" ({len(units)} MiB units compared)"))
    print(f"RESULT: {'PASS' if not r.fails else 'FAIL'} ({r.passes} passed, {r.fails} failed)")
    return 1 if r.fails else 0


GB_NAMES = {"EFI-SYSTEM", "boot_a", "boot_b", "init_boot_a", "init_boot_b", "vendor_boot_a", "vendor_boot_b",
            "vbmeta_a", "vbmeta_b", "pvmfw_a", "pvmfw_b", "misc", "metadata", "frp_persist",
            "desktop_security_persist", "desktop_security_storage", "super", "userdata"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mkimage"); m.add_argument("out"); m.add_argument("--c-mib", type=int, default=1024)
    m.add_argument("--d-mib", type=int, default=6144); m.add_argument("--seed", type=int, default=20261024)
    m.add_argument("--foreign-before-d", help="NAME:MIB - Linux-type partition with that GPT name between C: and D:")
    m.add_argument("--extra-after-d-mib", type=int, default=0, help="another basic data partition between D: and Recovery")
    s = sub.add_parser("snapshot"); s.add_argument("image"); s.add_argument("out")
    v = sub.add_parser("validate"); v.add_argument("image"); v.add_argument("--snapshot", required=True)
    v.add_argument("--expect", choices=("clean", "installed", "uninstalled"), required=True)
    v.add_argument("--plan"); v.add_argument("--source-dir"); v.add_argument("--zero-full", action="store_true")
    a = ap.parse_args()
    return {"mkimage": cmd_mkimage, "snapshot": cmd_snapshot, "validate": cmd_validate}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
