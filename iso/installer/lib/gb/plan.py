"""Planning (pure): zero ranges, dual-boot candidate regions and placement (port of gbdisk PlanNew), the whole-disk
GPT built from the image's GPT copies, and the gbdisk-shaped JSON documents (plan.json, layout JSON)."""
import struct

from . import const
from . import gpt
from .util import align_up, align_down, crc32, hex_attr, sha256

A = const.ALIGN


# ------------------------------------------------------------------------------------------------ zero ranges (4.0.5)
def zero_ranges(length, raw_size=None):
    """Relative (offset, length) ranges to zero in a partition of `length` bytes.
    Blank partition (raw_size None): <= 256 MiB -> all of it, else the first and last 64 MiB.
    Sourced partition: [raw_size, length) if raw_size < length."""
    if raw_size is None:
        if length <= const.BIG_BLANK or length <= 2 * const.HEAD_TAIL:
            return [(0, length)]
        return [(0, const.HEAD_TAIL), (length - const.HEAD_TAIL, const.HEAD_TAIL)]
    if raw_size > length:
        raise ValueError("object larger than its partition")
    return [(raw_size, length - raw_size)] if raw_size < length else []


# ------------------------------------------------------------------------------------------------ dual boot (4.2.3)
def with_bytes(entries, sector=512):
    out = []
    for e in entries:
        d = dict(e)
        d["offset"] = e["first"] * sector
        d["length"] = (e["last"] - e["first"] + 1) * sector
        d["end"] = d["offset"] + d["length"]
        out.append(d)
    return out


def fixed_bytes(db_parts):
    return sum(align_up(p["size"], A) for p in db_parts if not p["fill"])


def dualboot_regions(entries, first_usable_lba, last_usable_lba, db_parts, reserve=const.RESERVE, sector=512,
                     userdata_min=const.USERDATA_MIN):
    """Candidate regions after each Basic Data partition (anchor). Returns a list of dicts:
    anchor (entry with offset/length), anchor_ordinal, start, region_end, usable_end, fill (all bytes)."""
    parts = with_bytes(entries, sector)
    ords = gpt.ordinals(entries)
    usable_start = first_usable_lba * sector
    usable_end_b = (last_usable_lba + 1) * sector
    fixed = fixed_bytes(db_parts)
    out = []
    for p in sorted(parts, key=lambda q: q["offset"]):
        if p["type"] != const.TYPE_BASIC or p["name"] in const.GB_NAMES:
            continue
        after = sorted((q for q in parts if q["offset"] > p["offset"]), key=lambda q: q["offset"])
        region_end = after[0]["offset"] if after else usable_end_b
        if any(q is not p and q["offset"] < p["end"] and q["end"] > p["offset"] for q in parts):
            continue
        start = align_up(max(p["end"], usable_start), A)
        usable_end = align_down(min(region_end, usable_end_b) - reserve, A)
        fill = align_down(usable_end - start - fixed, A)
        if fill >= userdata_min:
            out.append(dict(anchor=p, anchor_ordinal=ords[p["slot"]], start=start, region_end=region_end,
                            usable_end=usable_end, fill=fill, reserve=reserve))
    return out


def userdata_from_reply(reply, fill):
    """db.userdata: '' -> fill; integer N (GiB) with 8 <= N and N GiB <= fill -> alignup(N GiB). None = invalid."""
    r = reply.strip()
    if r == "":
        return fill
    if not r.isdigit():
        return None
    n = int(r)
    b = n * const.GIB
    if n < 8 or b > fill:
        return None
    return align_up(b, A)


def dualboot_placement(region, db_parts, userdata_len):
    """Partitions in export order from region start, each alignup(size) long, the fill partition userdata_len."""
    off = region["start"]
    out = []
    for p in db_parts:
        length = userdata_len if p["fill"] else align_up(p["size"], A)
        if p.get("file_size") and p["file_size"] > length:
            raise ValueError("source of %s larger than its partition" % p["name"])
        out.append(dict(name=p["name"], type=p["type"], guid=p["guid"], attrs=p["attrs"], offset=off, length=length,
                        object=p.get("object"), file=p.get("file"), file_size=p.get("file_size"),
                        sha256=p.get("sha256"), blank=p["blank"]))
        off += length
    if off > region["usable_end"]:
        raise ValueError("placement does not fit the region")
    return out


def new_entries_for_slots(placement, first_slot, sector=512):
    """slot -> 128-byte GPT entry for each planned partition, slots first_slot .. first_slot+17 in placement order."""
    out = {}
    for i, p in enumerate(placement):
        first = p["offset"] // sector
        last = (p["offset"] + p["length"]) // sector - 1
        out[first_slot + i] = gpt.build_entry(p["type"], p["guid"], first, last, p["attrs"], p["name"])
    return out


def gpt_area_update(pre_p, pre_b, hdr_p, hdr_b, entries_new, n_lbas, sector=512):
    """Modes 2 and 3: the new LBA 0..33 and last-33-LBA bytes when only the entry array changes (4.2.9 / 4.3.6).

    pre_p/pre_b: the areas as read when the plan was made; hdr_p/hdr_b: parsed headers of that read; entries_new:
    the complete new entry array (n * esz bytes). Every byte other than the entry array, the entries CRC and the
    header CRCs stays as read (sector 0 included). Returns dict(primary, backup, pieces) with pieces for
    blockio.write_gpt (MBR never included)."""
    S = sector
    nb = len(entries_new)
    if len(pre_p) != 34 * S or len(pre_b) != 33 * S or nb != hdr_p["n"] * hdr_p["esz"] or nb > 32 * S:
        raise ValueError("unexpected GPT area sizes")
    if hdr_p["elba"] != 2 or hdr_b["elba"] != n_lbas - 33 or hdr_b["my"] != n_lbas - 1:
        raise ValueError("unexpected GPT geometry")
    ecrc = crc32(entries_new)
    new_hp = gpt.header_sector(hdr_p["raw"], ecrc=ecrc)
    new_hb = gpt.header_sector(hdr_b["raw"], ecrc=ecrc)
    if not gpt.header_fields_equal_except_crcs(hdr_p["raw"], new_hp) or \
            not gpt.header_fields_equal_except_crcs(hdr_b["raw"], new_hb):
        raise AssertionError("header fields other than the CRCs changed")
    exp_p = pre_p[:S] + new_hp + entries_new + pre_p[2 * S + nb:]
    exp_b = entries_new + pre_b[nb:32 * S] + new_hb
    if len(exp_p) != 34 * S or len(exp_b) != 33 * S or exp_p[:S] != pre_p[:S]:
        raise AssertionError("GPT area sizes")
    pieces = {"backup_entries": (n_lbas - 33, exp_b[:32 * S]), "backup_hdr": (n_lbas - 1, new_hb),
              "primary_entries": (2, exp_p[2 * S:]), "primary_hdr": (1, new_hp)}
    return dict(primary=exp_p, backup=exp_b, pieces=pieces)


# ------------------------------------------------------------------------------------------------ whole disk (4.1.6)
def whole_disk_gpt(primary_bin, backup_bin, wd, n_lbas, grow):
    """Build MBR, primary header, entry array and backup header for a disk of n_lbas sectors from the USB image's
    GPT copies. Returns dict(mbr, primary_hdr, entries, backup_hdr, userdata_last, gap) with gap = (first LBA,
    end LBA exclusive) of the zero range after userdata."""
    T = wd["total_lba"]
    if len(primary_bin) != const.GPT_PRIMARY_BYTES or len(backup_bin) != const.GPT_BACKUP_BYTES:
        raise ValueError("GPT copy sizes")
    if n_lbas < T:
        raise ValueError("disk smaller than the image")
    entries = bytearray(primary_bin[1024:17408])
    ud = [p for p in wd["partitions"] if p["growable"]]
    if len(ud) != 1:
        raise ValueError("manifest: exactly one growable partition expected")
    ud = ud[0]
    ud_last = ud["last_lba"]
    if grow:
        ud_last = (n_lbas - 33 - 2048) // 2048 * 2048 - 1
        if ud_last <= ud["last_lba"]:
            raise ValueError("grow would not enlarge userdata")
        struct.pack_into("<Q", entries, ud["slot"] * 128 + 40, ud_last)
    entries = bytes(entries)
    mbr = bytearray(primary_bin[:512])
    struct.pack_into("<I", mbr, 458, min(n_lbas - 1, 0xFFFFFFFF))
    ecrc = crc32(entries)
    img_hdr = primary_bin[512:1024]
    p_hdr = gpt.header_sector(img_hdr, alt=n_lbas - 1, last=n_lbas - 34, ecrc=ecrc)
    b_hdr = gpt.header_sector(p_hdr, my=n_lbas - 1, alt=1, elba=n_lbas - 33)
    if n_lbas == T and not grow:
        built_p = bytes(mbr) + p_hdr + entries
        built_b = entries + b_hdr
        assert built_p == primary_bin, "rebuilt primary GPT differs from gpt-primary.bin"
        assert built_b == backup_bin, "rebuilt backup GPT differs from gpt-backup.bin"
    gap = (ud_last + 1, (n_lbas - 33) if grow else (T - 33))
    return dict(mbr=bytes(mbr), primary_hdr=p_hdr, entries=entries, backup_hdr=b_hdr, userdata_last=ud_last, gap=gap)


def whole_disk_partitions(wd, userdata_last=None):
    """Partitions to write: list of dict(slot, name, offset, length, object, blank)."""
    out = []
    for p in wd["partitions"]:
        last = userdata_last if (p["growable"] and userdata_last is not None) else p["last_lba"]
        out.append(dict(slot=p["slot"], name=p["name"], offset=p["first_lba"] * 512,
                        length=(last - p["first_lba"] + 1) * 512, object=p["object"], blank=p["blank"]))
    return out


# ------------------------------------------------------------------------------------------------ JSON documents (4.5)
def part_json(e, ords, sector=512):
    return {"number": ords[e["slot"]], "name": e["name"], "type": e["type"], "id": e["guid"],
            "offset": e["first"] * sector, "length": (e["last"] - e["first"] + 1) * sector,
            "attributes": hex_attr(e["attrs"])}


def layout_json(hdr, entries, disk_size, target, time, tag, primary_bytes, backup_bytes, n_lbas, sector=512):
    """gbdisk layout JSON + backup extras (as gbdisk backup writes it)."""
    ords = gpt.ordinals(entries)
    parts = [part_json(e, ords, sector) for e in sorted(entries, key=lambda e: e["first"])]
    return {"disk_id": hdr["disk"], "disk_size": disk_size, "sector_size": sector,
            "first_usable": hdr["first"] * sector, "usable_length": (hdr["last"] - hdr["first"] + 1) * sector,
            "max_partition_count": hdr["n"], "partitions": parts, "target": target, "time": time,
            "primary_file": "gpt_%s_primary.bin" % tag, "backup_file": "gpt_%s_backup.bin" % tag,
            "backup_first_lba": n_lbas - 33, "primary_sha256": sha256(primary_bytes),
            "backup_sha256": sha256(backup_bytes)}


def plan_json(disk_guid, disk_size, kname, region, entries, placement, sector=512):
    """gb-dualboot-plan/1 exactly in gbdisk's Plan.ToJson shape."""
    ords = gpt.ordinals(entries)
    an = region["anchor"]
    base = [part_json(e, ords, sector) for e in sorted(entries, key=lambda e: e["first"])]
    parts = []
    for p in placement:
        parts.append({"name": p["name"], "type": p["type"], "id": p["guid"], "offset": p["offset"],
                      "length": p["length"], "attributes": hex_attr(p["attrs"]),
                      "file": None if p["blank"] else p["file"],
                      "file_size": 0 if p["blank"] else p["file_size"],
                      "sha256": None if p["blank"] else p["sha256"], "blank": bool(p["blank"])})
    return {"format": "gb-dualboot-plan/1", "disk_id": disk_guid, "disk_size": disk_size,
            "target": "iso:/dev/%s" % kname, "existing": False, "test_truncated": False,
            "anchor": {"number": ords[an["slot"]], "id": an["guid"], "length": an["length"]},
            "region": {"start": region["start"], "end": region["region_end"], "reserve": region["reserve"]},
            "base": base, "partitions": parts}


# ------------------------------------------------------------------------------------------------ GPT comparisons
HDR_FIELDS = ("rev", "hsz", "my", "alt", "first", "last", "disk", "elba", "n", "esz")


def header_diffs(a, b, which):
    """Differences of two parsed headers except the CRCs."""
    out = []
    for k in HDR_FIELDS:
        if a[k] != b[k]:
            out.append("%s header %s: %s -> %s" % (which, k, a[k], b[k]))
    return out


def slot_diffs(raw_a, raw_b, esz=128, ignore_vbmeta_slots=()):
    """List of (slot, kind) for 128-byte slots that differ; kind 'vbmeta-bits' when only bits 48..56 differ in a
    slot listed in ignore_vbmeta_slots."""
    out = []
    n = min(len(raw_a), len(raw_b)) // esz
    for i in range(n):
        x, y = raw_a[i * esz:(i + 1) * esz], raw_b[i * esz:(i + 1) * esz]
        if x == y:
            continue
        only_bits = x[:54] == y[:54] and x[56:] == y[56:] and (x[55] & 0xFE) == (y[55] & 0xFE)
        out.append((i, "vbmeta-bits" if (only_bits and i in ignore_vbmeta_slots) else "differs"))
    return out
