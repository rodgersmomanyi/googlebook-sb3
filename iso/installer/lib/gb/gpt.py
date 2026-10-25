"""MBR / EBR / GPT parsing, validation and building (pure: works on bytes and a read(lba, count) callable).

Every structure is read from bytes handed in by the caller; nothing here opens a device. `read(lba, count)` returns
count * sector bytes starting at LBA `lba` of the disk (callers pass a function over a device fd or a test file).
"""
import struct

from . import const
from .util import crc32, guid_le, guid_bytes

MAX_ENTRY_BYTES = 1 << 20        # never read more than 1 MiB of entries for a header claiming more


# ------------------------------------------------------------------------------------------------ MBR / EBR
def parse_mbr(sec0):
    """Sector 0 -> {"sig": bool, "entries": [dict(index, status, type, start, count)] x 4}."""
    sig = len(sec0) >= 512 and sec0[510:512] == b"\x55\xaa"
    ents = []
    for i in range(4):
        o = 446 + 16 * i
        status, ptype = sec0[o], sec0[o + 4]
        start, count = struct.unpack_from("<II", sec0, o + 8)
        ents.append(dict(index=i, status=status, type=ptype, start=start, count=count))
    return dict(sig=sig, entries=ents, raw=bytes(sec0[:512]))


def protective_mbr_ok(mbr):
    """Valid protective MBR: 55AA and entry 0 of type 0xEE starting at LBA 1."""
    e0 = mbr["entries"][0]
    return mbr["sig"] and e0["type"] == 0xEE and e0["start"] == 1


def hybrid_mbr_entries(mbr):
    """MBR entries 1..3 that are not empty (a hybrid MBR next to a GPT)."""
    return [e for e in mbr["entries"][1:] if e["type"] != 0 or e["start"] or e["count"]]


EXTENDED_TYPES = (0x05, 0x0F, 0x85)


def ebr_chain(read, ext_start, ext_count, limit=128):
    """Logical partitions of an extended MBR partition: list of (first_lba, count, type).
    Every EBR must lie inside the extended partition and is visited once; at most `limit` logical partitions."""
    out = []
    seen = set()
    ext_end = ext_start + ext_count
    cur = ext_start
    while len(out) < limit:
        if cur in seen or not (ext_start <= cur < ext_end):
            break
        seen.add(cur)
        try:
            sec = read(cur, 1)
        except OSError:
            break
        if sec[510:512] != b"\x55\xaa":
            break
        t0 = sec[446 + 4]
        s0, c0 = struct.unpack_from("<II", sec, 446 + 8)
        if t0 != 0 and c0:
            out.append((cur + s0, c0, t0))
        t1 = sec[462 + 4]
        s1, c1 = struct.unpack_from("<II", sec, 462 + 8)
        if t1 not in EXTENDED_TYPES or not c1:
            break
        cur = ext_start + s1
    return out


# ------------------------------------------------------------------------------------------------ GPT header
HDR_FMT = "<8sIIIIQQQQ16sQIII"      # 92 bytes


def parse_header(sec):
    """Header sector -> dict, or None without the 'EFI PART' signature. Never raises for odd values."""
    if len(sec) < 92 or sec[:8] != b"EFI PART":
        return None
    (sig, rev, hsz, hcrc, rsv, my, alt, first, last, disk, elba, n, esz, ecrc) = struct.unpack_from(HDR_FMT, sec, 0)
    h = dict(rev=rev, hsz=hsz, hcrc=hcrc, rsv=rsv, my=my, alt=alt, first=first, last=last, disk=guid_le(disk),
             elba=elba, n=n, esz=esz, ecrc=ecrc, raw=bytes(sec[:512]))
    h["crc_ok"] = False
    if 92 <= hsz <= 512:
        t = bytearray(sec[:hsz])
        t[16:20] = b"\0\0\0\0"
        h["crc_ok"] = crc32(bytes(t)) == hcrc
    h["tail_zero"] = hsz == 92 and not any(sec[92:512])
    return h


def header_sector(h_raw, **changes):
    """Copy of a 512-byte header sector with fields replaced and the header CRC recomputed.
    Allowed keys: my, alt, first, last, elba, ecrc (the only fields the installer ever changes)."""
    s = bytearray(h_raw[:512])
    pos = dict(my=(24, "<Q"), alt=(32, "<Q"), first=(40, "<Q"), last=(48, "<Q"), elba=(72, "<Q"), ecrc=(88, "<I"))
    for k, v in changes.items():
        off, fmt = pos[k]
        struct.pack_into(fmt, s, off, v)
    hsz = struct.unpack_from("<I", s, 12)[0]
    if not 92 <= hsz <= 512:
        raise ValueError("header size %d" % hsz)
    s[16:20] = b"\0\0\0\0"
    struct.pack_into("<I", s, 16, crc32(bytes(s[:hsz])))
    return bytes(s)


def header_fields_equal_except_crcs(a_raw, b_raw):
    """True if two header sectors are equal apart from the header CRC (16..20) and the entries CRC (88..92)."""
    a = bytearray(a_raw[:512])
    b = bytearray(b_raw[:512])
    for x in (a, b):
        x[16:20] = b"\0\0\0\0"
        x[88:92] = b"\0\0\0\0"
    return a == b


# ------------------------------------------------------------------------------------------------ entries
def parse_entries(raw, n, esz=128):
    """Used entries -> list of dict(slot, type, guid, first, last, attrs, name, raw). Unused = type all zero."""
    out = []
    for i in range(n):
        e = raw[i * esz:(i + 1) * esz]
        if len(e) < 128:
            break
        if e[:16] == b"\0" * 16:
            continue
        first, last, attrs = struct.unpack_from("<QQQ", e, 32)
        name = bytes(e[56:128]).decode("utf-16-le", "replace").split("\0")[0]
        out.append(dict(slot=i, type=guid_le(e[:16]), guid=guid_le(e[16:32]), first=first, last=last, attrs=attrs,
                        name=name, raw=bytes(e[:esz]), dirty_unused=False))
    return out


def unused_slots_dirty(raw, n, esz=128):
    """Slots whose type GUID is zero but which hold other non-zero bytes."""
    bad = []
    for i in range(n):
        e = raw[i * esz:(i + 1) * esz]
        if e[:16] == b"\0" * 16 and any(e):
            bad.append(i)
    return bad


def build_entry(type_guid, part_guid, first, last, attrs, name):
    nb = name.encode("utf-16-le")
    if len(nb) > 72:
        raise ValueError("partition name too long")
    return guid_bytes(type_guid) + guid_bytes(part_guid) + struct.pack("<QQQ", first, last, attrs) + nb.ljust(72, b"\0")


def highest_used_slot(raw, n, esz=128):
    h = -1
    for i in range(n):
        if raw[i * esz:i * esz + 16] != b"\0" * 16:
            h = i
    return h


def ordinals(entries):
    """slot -> gbdisk ordinal (1 + the number of used entries in lower slots; gbdisk.cs p.Number = ++num)."""
    out = {}
    for k, e in enumerate(sorted(entries, key=lambda e: e["slot"])):
        out[e["slot"]] = k + 1
    return out


def put_entries(raw, slot_bytes, esz=128):
    """Copy of the entry array with the given slots (dict slot -> 128 bytes) replaced."""
    b = bytearray(raw)
    for slot, eb in slot_bytes.items():
        if len(eb) != esz:
            raise ValueError("entry size")
        b[slot * esz:(slot + 1) * esz] = eb
    return bytes(b)


def zero_slots(raw, slots, esz=128):
    return put_entries(raw, {s: bytes(esz) for s in slots}, esz)


# ------------------------------------------------------------------------------------------------ reading a disk
def read_gpt(read, n_lbas, sector=512):
    """Read MBR, both GPT headers and both entry arrays (even when CRCs are wrong).

    Returns dict(mbr, N, sector, primary=copy, backup=copy) where copy = dict(hdr, entries_raw, entries, problems)
    (hdr None without signature)."""
    out = dict(N=n_lbas, sector=sector)
    try:
        out["mbr"] = parse_mbr(read(0, 1))
    except OSError as e:
        out["mbr"] = parse_mbr(bytes(512))
        out["mbr_error"] = str(e)
    out["primary"] = _read_copy(read, n_lbas, sector, 1)
    out["backup"] = _read_copy(read, n_lbas, sector, n_lbas - 1)
    return out


def _read_copy(read, n_lbas, sector, hdr_lba):
    c = dict(hdr=None, entries_raw=None, entries=[], problems=[], hdr_lba=hdr_lba)
    if hdr_lba < 1 or hdr_lba >= n_lbas:
        c["problems"].append("header LBA outside the disk")
        return c
    try:
        h = parse_header(read(hdr_lba, 1))
    except OSError as e:
        c["problems"].append("read error: %s" % e)
        return c
    if h is None:
        c["problems"].append("no EFI PART signature")
        return c
    c["hdr"] = h
    nbytes = h["n"] * h["esz"]
    if h["esz"] < 128 or nbytes <= 0 or nbytes > MAX_ENTRY_BYTES:
        c["problems"].append("entry array size %d x %d not supported" % (h["n"], h["esz"]))
        return c
    secs = (nbytes + sector - 1) // sector
    if h["elba"] < 1 or h["elba"] + secs > n_lbas:
        c["problems"].append("entry array outside the disk")
        return c
    try:
        raw = read(h["elba"], secs)[:nbytes]
    except OSError as e:
        c["problems"].append("entry read error: %s" % e)
        return c
    c["entries_raw"] = raw
    c["entries"] = parse_entries(raw, h["n"], h["esz"])
    return c


# ------------------------------------------------------------------------------------------------ union enumeration
def union_ranges(read, n_lbas, sector, gptinfo=None, kernel_parts=()):
    """4.0.16: union, deduplicated by (first LBA, sector count), of primary GPT entries, backup GPT entries, the MBR's
    entries (not 0x00, not 0xEE), EBR logical partitions, and the kernel's partitions.

    kernel_parts: iterable of dict(kname, partno, first, count) in LBA units of `sector`.
    Returns a list of range dicts sorted by first LBA."""
    g = gptinfo or read_gpt(read, n_lbas, sector)
    ranges = {}

    def add(first, count, src, **info):
        if count <= 0:
            return
        key = (first, count)
        r = ranges.get(key)
        if r is None:
            r = dict(first=first, count=count, sources=[], gpt_types=[], mbr_types=[], guids=[], names=[],
                     slots=[], attrs=None, kname=None, partno=None)
            ranges[key] = r
        if src not in r["sources"]:
            r["sources"].append(src)
        for k, lst in (("gpt_type", "gpt_types"), ("mbr_type", "mbr_types"), ("guid", "guids"), ("name", "names")):
            v = info.get(k)
            if v is not None and v not in r[lst]:
                r[lst].append(v)
        if info.get("slot") is not None and src == "gpt-primary" and info["slot"] not in r["slots"]:
            r["slots"].append(info["slot"])
        if info.get("attrs") is not None and r["attrs"] is None:
            r["attrs"] = info["attrs"]
        if info.get("kname"):
            r["kname"] = info["kname"]
            r["partno"] = info.get("partno")

    for copy, src in ((g["primary"], "gpt-primary"), (g["backup"], "gpt-backup")):
        for e in copy["entries"]:
            if e["last"] >= e["first"]:
                add(e["first"], e["last"] - e["first"] + 1, src, gpt_type=e["type"], guid=e["guid"], name=e["name"],
                    slot=e["slot"], attrs=e["attrs"])
    mbr = g["mbr"]
    if mbr["sig"]:
        for e in mbr["entries"]:
            if e["type"] in (0x00, 0xEE) or not e["count"]:
                continue
            add(e["start"], e["count"], "mbr", mbr_type=e["type"])
            if e["type"] in EXTENDED_TYPES:
                for first, count, t in ebr_chain(read, e["start"], e["count"]):
                    add(first, count, "ebr", mbr_type=t)
    for kp in kernel_parts:
        add(kp["first"], kp["count"], "kernel", kname=kp["kname"], partno=kp.get("partno"))
    return sorted(ranges.values(), key=lambda r: (r["first"], r["count"]))


# ------------------------------------------------------------------------------------------------ checks (4.2 / 4.3)
def primary_valid(g):
    h = g["primary"]["hdr"]
    return h is not None and h["crc_ok"] and h["my"] == 1 and 92 <= h["hsz"] <= 512


def check_no_gpt(g):
    """R_NO_GPT: no valid protective MBR or no valid primary header. Returns a message or None."""
    if not protective_mbr_ok(g["mbr"]):
        return "no valid protective MBR (entry 0 of type 0xEE starting at LBA 1)"
    if not primary_valid(g):
        return "no valid primary GPT header"
    return None


def copy_problems(c, n_lbas, which):
    """Problems of one GPT copy on its own (header CRC, its own entry CRC, header layout)."""
    p = []
    h = c["hdr"]
    if h is None:
        return ["%s: no header" % which] + ["%s: %s" % (which, x) for x in c["problems"]]
    p += ["%s: %s" % (which, x) for x in c["problems"]]
    if not h["crc_ok"]:
        p.append("%s: header CRC wrong" % which)
    if h["hsz"] != 92 or not h["tail_zero"]:
        p.append("%s: header size %d or non-zero bytes after the header" % (which, h["hsz"]))
    if h["rsv"] != 0:
        p.append("%s: reserved header field not zero" % which)
    if c["entries_raw"] is not None and crc32(c["entries_raw"]) != h["ecrc"]:
        p.append("%s: entry array CRC wrong" % which)
    if which == "primary":
        if h["my"] != 1:
            p.append("primary: MyLBA %d" % h["my"])
        if h["alt"] != n_lbas - 1:
            p.append("primary: AlternateLBA %d != last LBA %d" % (h["alt"], n_lbas - 1))
    else:
        if h["my"] != n_lbas - 1:
            p.append("backup: header not at the last LBA (MyLBA %d)" % h["my"])
        if h["alt"] != 1:
            p.append("backup: AlternateLBA %d != 1" % h["alt"])
    return p


def entry_problems(entries, h):
    """Overlapping/out-of-range entries, duplicate GUIDs, zero GUIDs."""
    p = []
    ids = [e["guid"] for e in entries]
    if len(set(ids)) != len(ids):
        p.append("duplicate partition GUIDs")
    if const.ZERO_GUID in ids:
        p.append("zero partition GUID")
    s = sorted(entries, key=lambda e: e["first"])
    for i, e in enumerate(s):
        if e["first"] > e["last"]:
            p.append("slot %d: first LBA > last LBA" % e["slot"])
        if e["first"] < h["first"] or e["last"] > h["last"]:
            p.append("slot %d: outside the usable LBAs" % e["slot"])
        if i and e["first"] <= s[i - 1]["last"]:
            p.append("slots %d / %d overlap" % (s[i - 1]["slot"], e["slot"]))
    return p


def headers_consistent(h1, h2):
    p = []
    for k in ("rev", "hsz", "first", "last", "disk", "n", "esz"):
        if h1[k] != h2[k]:
            p.append("primary/backup headers differ in %s" % k)
    return p


def check_gpt_invalid(g, vbmeta_guids=None):
    """R_GPT_INVALID problems (list; empty = ok). With vbmeta_guids (mode 3): returns (problems, tolerated) where
    tolerated is True when the only difference between the two entry arrays is the vbmeta slot bits."""
    n_lbas = g["N"]
    p = []
    pr, bk = g["primary"], g["backup"]
    p += copy_problems(pr, n_lbas, "primary")
    p += copy_problems(bk, n_lbas, "backup")
    tolerated = False
    if pr["hdr"] is not None and bk["hdr"] is not None:
        p += headers_consistent(pr["hdr"], bk["hdr"])
        if pr["entries_raw"] is not None and bk["entries_raw"] is not None and pr["entries_raw"] != bk["entries_raw"]:
            if vbmeta_guids is not None and vbmeta_only_difference(pr["entries_raw"], bk["entries_raw"],
                                                                   pr["hdr"]["esz"], vbmeta_guids):
                tolerated = True
            else:
                p.append("primary and backup entry arrays differ")
        p += entry_problems(pr["entries"], pr["hdr"])
        if pr["entries_raw"] is not None:
            dirty = unused_slots_dirty(pr["entries_raw"], pr["hdr"]["n"], pr["hdr"]["esz"])
            if dirty:
                p.append("unused slots with non-zero bytes: %s" % dirty[:8])
    if hybrid_mbr_entries(g["mbr"]):
        p.append("other MBR entries present (hybrid MBR)")
    if vbmeta_guids is not None:
        # with the tolerance each copy must still be valid on its own; the tolerated difference changes the
        # entries CRC of one copy only, which copy_problems already checked against its own array
        return p, tolerated
    return p


def vbmeta_only_difference(a, b, esz, vbmeta_guids):
    """True if the arrays differ only in byte 54 and bit 0 of byte 55 of slots whose GUID (same in both) is one of
    vbmeta_guids (attribute bits 48..56)."""
    if len(a) != len(b):
        return False
    vg = set(vbmeta_guids)
    found = False
    for i in range(len(a) // esz):
        x, y = a[i * esz:(i + 1) * esz], b[i * esz:(i + 1) * esz]
        if x == y:
            continue
        if x[:54] != y[:54] or x[56:] != y[56:] or (x[55] & 0xFE) != (y[55] & 0xFE):
            return False
        if x[:16] == b"\0" * 16 or guid_le(x[16:32]) not in vg:
            return False
        found = True
    return found


def check_gpt_geometry(g):
    """R_GPT_GEOMETRY: primary entries at LBA 2, backup entries at N-33, entry size 128, array <= 32 sectors."""
    n_lbas = g["N"]
    p = []
    h1, h2 = g["primary"]["hdr"], g["backup"]["hdr"]
    if h1 is None or h2 is None:
        return ["GPT header missing"]
    if h1["elba"] != 2:
        p.append("primary entries at LBA %d (not 2)" % h1["elba"])
    if h2["elba"] != n_lbas - 33:
        p.append("backup entries at LBA %d (not N-33 = %d)" % (h2["elba"], n_lbas - 33))
    for h, w in ((h1, "primary"), (h2, "backup")):
        if h["esz"] != 128:
            p.append("%s entry size %d" % (w, h["esz"]))
        if h["n"] * h["esz"] > 32 * 512:
            p.append("%s entry array larger than 32 sectors" % w)
    if h1["first"] < 34:
        p.append("first usable LBA %d < 34" % h1["first"])
    if h1["last"] > n_lbas - 34:
        p.append("last usable LBA %d > N-34" % h1["last"])
    return p
