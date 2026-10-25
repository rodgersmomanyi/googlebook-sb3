#!/usr/bin/env python3
"""Builder-side tests of the installer modules against image FILES and loop devices over image files.

Never touches a real disk: every image lives below WORK (/var/tmp/gbinst-dev/...), loop devices are attached only
to those files (losetup -j checked first) and detached afterwards. The installer's UI never runs here.

  python3 -I -B builder_tests.py files <installer_dir> <work_dir>        as the build user
  sudo python3 -I -B builder_tests.py loop <installer_dir> <work_dir>    sysfs/ioctl/read-only guard on loop devices
Prints one PASS/FAIL line per check and 'SUMMARY builder-<mode>: <n> passed, <m> failed'.
"""
import errno
import hashlib
import json
import os
import random
import struct
import subprocess
import sys
import time
import uuid
import zlib

MODE, INST, WORK = sys.argv[1], os.path.abspath(sys.argv[2]), os.path.abspath(sys.argv[3])
if not WORK.startswith("/var/tmp/gbinst-dev/"):
    sys.exit("work dir must be below /var/tmp/gbinst-dev/")
os.makedirs(WORK, exist_ok=True)
sys.path.insert(0, os.path.join(INST, "lib"))

from gb import const                                         # noqa: E402

# small-scale constants for the synthetic payload (the code reads them at call time)
const.CHUNK = 1 << 20
const.BIG_BLANK = 2 << 20
const.HEAD_TAIL = 1 << 20
const.RESERVE = 16 << 20
const.USERDATA_MIN = 8 << 20

from gb import blockio, disks, dualboot, errors, gpt, log, plan, run, scan, sysfs, uninstall, wholedisk  # noqa
from gb import manifest as mf                                # noqa: E402

MiB = 1 << 20
RESULTS = []
log.LOG_FILE = os.path.join(WORK, "installer-test.log")
log.configure(markers=True)


def check(ok, msg):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + msg, flush=True)
    return ok


def sh(*argv, ok=(0,)):
    p = subprocess.run(list(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode not in ok:
        raise RuntimeError("%s -> %d: %s" % (" ".join(argv), p.returncode, p.stdout[-400:]))
    return p.stdout


def sha_file(path, off=0, n=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(off)
        left = n if n is not None else os.path.getsize(path) - off
        while left:
            b = f.read(min(left, 4 * MiB))
            if not b:
                break
            h.update(b)
            left -= len(b)
    return h.hexdigest()


def guid(i):
    return str(uuid.UUID(int=(0x7e57 << 112) | (4 << 76) | (8 << 60) | i))


# ------------------------------------------------------------------------------------------------ test doubles
class FileTarget(blockio.Target):
    """blockio.Target over an image FILE (the flows' write gate, writes, syncs and O_DIRECT re-reads)."""

    def __init__(self, path):
        self.d = None
        self.kname = "testimg"
        self.node = path
        self.fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        self.gate = blockio.Gate()
        self.sector = 512
        self.n_lbas = os.path.getsize(path) // 512
        self.flow = "test"

    def recheck_geometry(self):
        return True


class J:
    def __init__(self):
        self.phases = []

    def phase(self, name):
        self.phases.append(name)


class FakePayload:
    def __init__(self, root, m):
        self.root, self.mount, self.m = root, root, m

    def path(self, rel):
        p = os.path.join(self.root, rel)
        if not os.path.exists(p):
            raise OSError("missing %s" % rel)
        return p


class HarnessStream:
    """Stands in for run.zstd_stream (the real one only runs inside the installer environment)."""

    def __init__(self, path):
        self.p = subprocess.Popen(["zstd", "-dcq", "--long=27", path], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)

    def read(self, n):
        return self.p.stdout.read1(n) if n else b""

    def read_exact(self, n):
        return self.p.stdout.read(n)

    def close(self, terminate=False):
        if terminate:
            self.p.kill()
        self.p.stdout.close()
        self.p.stderr.close()
        return self.p.wait()


run.zstd_stream = HarnessStream


# ------------------------------------------------------------------------------------------------ synthetic payload
WD_MIB = [("EFI-SYSTEM", 4), ("boot_a", 2), ("boot_b", 2), ("init_boot_a", 1), ("init_boot_b", 1),
          ("vendor_boot_a", 1), ("vendor_boot_b", 1), ("vbmeta_a", 1), ("vbmeta_b", 1), ("pvmfw_a", 1), ("pvmfw_b", 1),
          ("misc", 1), ("metadata", 2), ("frp_persist", 1), ("desktop_security_persist", 1),
          ("desktop_security_storage", 1), ("super", 5), ("userdata", 4)]
BLANK = ("misc", "metadata", "frp_persist", "desktop_security_persist", "desktop_security_storage", "userdata")


def ptype(nm):
    return const.TYPE_ESP if nm == "EFI-SYSTEM" else (const.TYPE_VBMETA if nm.startswith("vbmeta") else
                                                      const.TYPE_LINUX)


def make_payload(root):
    """Synthetic USB image + payload tree (objects in 1 MiB zstd chunks) + manifest. Returns (image path, m)."""
    rnd = random.Random(20261025)
    os.makedirs(os.path.join(root, "gbsb3", "parts"), exist_ok=True)
    os.makedirs(os.path.join(root, "gbsb3", "wd"), exist_ok=True)
    objs_raw = {}
    for nm, size in WD_MIB:
        if nm in BLANK:
            continue
        oid = "esp-usb" if nm == "EFI-SYSTEM" else nm
        raw = size * MiB - (3584 if nm == "super" else 0)        # super: zero tail inside its partition
        objs_raw[oid] = rnd.randbytes(raw)
    objs_raw["esp-internal"] = rnd.randbytes(4 * MiB)
    objects = {}
    for oid, data in objs_raw.items():
        chunks = []
        for k in range(0, (len(data) + MiB - 1) // MiB):
            piece = data[k * MiB:(k + 1) * MiB]
            rel = "gbsb3/parts/%s.%03d.zst" % (oid, k)
            p = subprocess.run(["zstd", "-q", "-9", "--long=27", "-c"], input=piece, stdout=subprocess.PIPE,
                               check=True)
            with open(os.path.join(root, rel), "wb") as f:
                f.write(p.stdout)
            chunks.append({"path": rel, "raw_offset": k * MiB, "raw_size": len(piece),
                           "raw_sha256": hashlib.sha256(piece).hexdigest(), "zst_size": len(p.stdout),
                           "zst_sha256": hashlib.sha256(p.stdout).hexdigest()})
        objects[oid] = {"raw_size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "chunks": chunks}
    # the image
    lba = 2048
    placed = []
    for i, (nm, size) in enumerate(WD_MIB):
        placed.append((i, nm, lba, lba + size * 2048 - 1))
        lba += size * 2048
    T = lba + 2048 + 33
    ents = b"".join(gpt.build_entry(ptype(nm), guid(100 + i), f, l, 0x010f000000000000 if nm == "vbmeta_b" else 0,
                                    nm) for i, nm, f, l in placed).ljust(16384, b"\0")
    mbr = bytearray(512)
    mbr[446:462] = struct.pack("<BBBBBBBBII", 0, 0, 2, 0, 0xEE, 0xFF, 0xFF, 0xFF, 1, T - 1)
    mbr[510:512] = b"\x55\xaa"
    hdr = bytearray(512)
    struct.pack_into("<8sIIIIQQQQ16sQIII", hdr, 0, b"EFI PART", 0x10000, 92, 0, 0, 1, T - 1, 34, T - 34,
                     uuid.UUID(guid(99)).bytes_le, 2, 128, 128, zlib.crc32(ents))
    ph = gpt.header_sector(bytes(hdr))
    bh = gpt.header_sector(ph, my=T - 1, alt=1, elba=T - 33)
    img = os.path.join(WORK, "usb.img")
    with open(img, "wb") as f:
        f.truncate(T * 512)
        f.seek(0)
        f.write(bytes(mbr) + ph + ents)
        for i, nm, first, last in placed:
            oid = "esp-usb" if nm == "EFI-SYSTEM" else nm
            if oid in objs_raw:
                f.seek(first * 512)
                f.write(objs_raw[oid])
        f.seek((T - 33) * 512)
        f.write(ents + bh)
    with open(img, "rb") as f:
        prim = f.read(17408)
        f.seek((T - 33) * 512)
        back = f.read(16896)
    for name, data in (("gpt-primary.bin", prim), ("gpt-backup.bin", back)):
        with open(os.path.join(root, "gbsb3", "wd", name), "wb") as f:
            f.write(data)
    wdp = []
    for i, nm, first, last in placed:
        oid = "esp-usb" if nm == "EFI-SYSTEM" else (None if nm in BLANK else nm)
        wdp.append({"slot": i, "name": nm, "type": ptype(nm), "guid": guid(100 + i),
                    "attributes": "0x%016x" % (0x010f000000000000 if nm == "vbmeta_b" else 0), "first_lba": first,
                    "last_lba": last, "size": (last - first + 1) * 512, "object": oid, "blank": oid is None,
                    "growable": nm == "userdata"})
    dbp = []
    for i, (nm, size) in enumerate(WD_MIB):
        fill = nm == "userdata"
        oid = "esp-internal" if nm == "EFI-SYSTEM" else (None if nm in BLANK else nm)
        dbp.append({"name": nm, "type": ptype(nm), "guid": guid(200 + i),
                    "attributes": "0x%016x" % (0x010f000000000000 if nm == "vbmeta_b" else 0),
                    "size": None if fill else size * MiB, "fill": fill,
                    "min_size": const.USERDATA_MIN if fill else None, "blank": oid is None, "object": oid,
                    "file": (nm + ".img") if oid else None,
                    "file_size": objects[oid]["raw_size"] if oid else None,
                    "sha256": objects[oid]["sha256"] if oid else None})
    h = "00" * 32
    m = {"format": "gbsb3-iso-payload/1", "build_id": "20261025-000000-0badc0de", "created_utc": "x",
         "volume_label": "GBSB3INST", "android_build": "TEST", "system_slot": "b", "kernel_release": "x",
         "kernel_nx_compat_cleared": True, "sector_size": 512, "align_bytes": MiB, "chunk_raw_bytes": const.CHUNK,
         "compression": {"tool": "zstd", "level": 9, "long": 27, "version": "x"},
         "source": {"usb_image": {}, "dualboot_export": {}}, "objects": objects,
         "whole_disk": {"image_bytes": T * 512, "total_lba": T, "disk_guid": guid(99), "first_usable_lba": 34,
                        "last_usable_lba": T - 34, "entries_lba": 2, "backup_entries_lba": T - 33,
                        "entry_count": 128, "entry_size": 128, "entries_crc32": "0x%08x" % zlib.crc32(ents),
                        "gpt_primary": {"path": "gbsb3/wd/gpt-primary.bin", "bytes": 17408,
                                        "sha256": hashlib.sha256(prim).hexdigest()},
                        "gpt_backup": {"path": "gbsb3/wd/gpt-backup.bin", "bytes": 16896,
                                       "sha256": hashlib.sha256(back).hexdigest()},
                        "super_guid": guid(116), "partitions": wdp},
         "dual_boot": {"export_manifest": "gbsb3/dualboot/manifest.json", "export_manifest_sha256": h,
                       "esp_guid": guid(200), "super_guid": guid(216), "cmdline": "x",
                       "reserve_bytes": const.RESERVE, "userdata_min_bytes": const.USERDATA_MIN,
                       "boot_entry": {"label": "Googlebook OS", "loader": "\\EFI\\BOOT\\BOOTX64.EFI"},
                       "partitions": dbp}}
    return img, mf.parse_manifest(json.dumps(m).encode(), "20261025-000000-0badc0de"), prim, back


def blank_file(path, n_bytes, fill_random=False):
    with open(path, "wb") as f:
        f.truncate(n_bytes)
        if fill_random:
            rnd = random.Random(7)
            for off in (0, n_bytes - 2 * MiB, n_bytes // 2):
                f.seek(off)
                f.write(rnd.randbytes(2 * MiB))


# ------------------------------------------------------------------------------------------------ mode 1
def test_wholedisk(payload, img, prim, back):
    m = payload.m
    wd = m["wd"]
    T = wd["total_lba"]
    for label, size, grow, rnd in (("exact size", T * 512, False, False), ("exact size, old data", T * 512, False,
                                                                           True),
                                   ("larger, grow", T * 512 + 64 * MiB, True, True),
                                   ("larger, no grow", T * 512 + 64 * MiB, False, False)):
        tgt = os.path.join(WORK, "wd_target.img")
        blank_file(tgt, size, rnd)
        n = size // 512
        built = plan.whole_disk_gpt(prim, back, wd, n, grow)
        parts = plan.whole_disk_partitions(wd, built["userdata_last"] if grow else None)
        t = FileTarget(tgt)
        j = J()
        wholedisk.setup_gate(t, parts, built, n)
        zrs = wholedisk.write_image(t, payload, parts, built, n, j)
        wholedisk.verify_image(t, payload, parts, built, zrs)
        t.close()
        check(j.phases == ["head_zeroed", "data_written", "gpt_written"], "mode 1 %s: journal phases %s" % (
            label, j.phases))
        out = sh("sgdisk", "-v", tgt, ok=(0, 1, 2))
        check("No problems found" in out, "mode 1 %s: sgdisk -v reports no problems" % label)
        if size == T * 512:
            check(sha_file(tgt) == sha_file(img), "mode 1 %s: target byte-identical to the image" % label)
        else:
            g = gpt.read_gpt(sysfs.DirectReader(tgt).read, n, 512)
            e = g["primary"]["entries"]
            ok = gpt.check_gpt_invalid(g) == [] and g["primary"]["hdr"]["last"] == n - 34 and \
                g["backup"]["hdr"]["my"] == n - 1 and len(e) == 18
            check(ok, "mode 1 %s: GPT valid, backup at the end, last usable N-34" % label)
            ud = e[17]
            want = (n - 33 - 2048) // 2048 * 2048 - 1 if grow else wd["partitions"][17]["last_lba"]
            check(ud["last"] == want, "mode 1 %s: userdata last LBA %d (expected %d)" % (label, ud["last"], want))
            same = all(sha_file(tgt, p["offset"], p["length"]) == sha_file(img, p["offset"], p["length"])
                       for p in parts if p["name"] != "userdata")
            check(same, "mode 1 %s: the 17 other partitions equal the image" % label)
            check(sha_file(tgt, 34 * 512, (2048 - 34) * 512) == hashlib.sha256(bytes((2048 - 34) * 512)).hexdigest(),
                  "mode 1 %s: LBA 34..2047 zero" % label)
        os.unlink(tgt)
    # the gate refuses a write outside the plan
    tgt = os.path.join(WORK, "wd_target.img")
    blank_file(tgt, T * 512)
    t = FileTarget(tgt)
    built = plan.whole_disk_gpt(prim, back, wd, T, False)
    parts = plan.whole_disk_partitions(wd)
    wholedisk.setup_gate(t, parts, built, T)
    try:
        t.gate.phase = "gpt"
        t.write(2048 * 512, bytes(512))
        check(False, "gate: data write in the GPT phase refused")
    except errors.Internal:
        check(True, "gate: data write in the GPT phase refused")
    t.gate.phase = "data"
    try:
        t.write((parts[-1]["offset"] + parts[-1]["length"]) * 1 + 0, bytes(2 * MiB))
        check(False, "gate: write past userdata refused")
    except errors.Internal:
        check(True, "gate: write past userdata refused")
    # a damaged chunk: refused before any byte of it is written
    c = payload.m["objects"]["super"]["chunks"][2]
    cp = payload.path(c["path"])
    with open(cp, "rb") as f:
        orig = f.read()
    with open(cp, "wb") as f:
        f.write(orig[:-1] + bytes([orig[-1] ^ 1]))
    sp = [p for p in parts if p["name"] == "super"][0]
    try:
        blockio.write_object(t, payload, "super", sp["offset"], sp["length"], "super")
        check(False, "damaged chunk -> F_PAYLOAD")
    except errors.Failed as e:
        check(e.code == "F_PAYLOAD", "damaged chunk -> F_PAYLOAD (%s)" % e.code)
    with open(cp, "wb") as f:
        f.write(orig)
    t.close()
    v = sha_file(tgt, sp["offset"] + 2 * MiB, MiB) == hashlib.sha256(bytes(MiB)).hexdigest()
    check(v, "damaged chunk: its raw range was not written")
    os.unlink(tgt)


# ------------------------------------------------------------------------------------------------ modes 2 and 3
def host_image(path, free_mib=60):
    """ESP 8, MSR 1, C 16, D 16 (random data), free_mib unallocated, Recovery 4 MiB, like a shrunk Windows disk."""
    rnd = random.Random(99)
    sizes = [8, 1, 16, 16]
    types = [const.TYPE_ESP, const.TYPE_MSR, const.TYPE_BASIC, const.TYPE_BASIC]
    offs, o = [], MiB
    for s in sizes:
        offs.append(o)
        o += s * MiB
    d_end = o
    rec = o + free_mib * MiB
    offs.append(rec)
    sizes.append(4)
    types.append(const.TYPE_WINRE)
    usable_end = rec + 4 * MiB + 335360
    size = usable_end + 33 * 512
    n = size // 512
    attrs = [0, 0, 0, 0, 0x8000000000000001]
    ents = b"".join(gpt.build_entry(t, guid(10 + i), off // 512, (off + s * MiB) // 512 - 1, a, "Basic data partition")
                    for i, (t, off, s, a) in enumerate(zip(types, offs, sizes, attrs))).ljust(16384, b"\0")
    mbr = bytearray(512)
    mbr[446:462] = struct.pack("<BBBBBBBBII", 0, 0, 2, 0, 0xEE, 0xFF, 0xFF, 0xFF, 1, n - 1)
    mbr[510:512] = b"\x55\xaa"
    hdr = bytearray(512)
    struct.pack_into("<8sIIIIQQQQ16sQIII", hdr, 0, b"EFI PART", 0x10000, 92, 0, 0, 1, n - 1, 34,
                     usable_end // 512 - 1, uuid.UUID(guid(1)).bytes_le, 2, 128, 128, zlib.crc32(ents))
    ph = gpt.header_sector(bytes(hdr))
    bh = gpt.header_sector(ph, my=n - 1, alt=1, elba=n - 33)
    with open(path, "wb") as f:
        f.truncate(size)
        f.write(bytes(mbr) + ph + ents)
        for off, s in zip(offs, sizes):
            f.seek(off)
            f.write(rnd.randbytes(s * MiB))
        f.seek(d_end + MiB)
        f.write(rnd.randbytes(MiB))                 # stale data in the free space (overwritten only where planned)
        f.seek((n - 33) * 512)
        f.write(ents + bh)
    return n


class D:
    kname = "testimg"


def layout_of(path):
    d = D()
    with sysfs.DirectReader(path) as rd:
        disks.read_layout(d, rd)
        d.n_lbas, d.sector, d.size = rd.n_lbas, rd.sector, rd.size
    return d


def test_dualboot(payload):
    m = payload.m
    img = os.path.join(WORK, "host.img")
    n = host_image(img)
    orig = sha_file(img)
    d = layout_of(img)
    g = d.g
    check(gpt.check_no_gpt(g) is None and gpt.check_gpt_invalid(g) == [] and gpt.check_gpt_geometry(g) == [],
          "mode 2: host image passes no_gpt / gpt_invalid / gpt_geometry")
    hdr = g["primary"]["hdr"]
    regs = plan.dualboot_regions(g["primary"]["entries"], hdr["first"], hdr["last"], m["db_parts"], const.RESERVE,
                                 512, const.USERDATA_MIN)
    check(len(regs) == 1 and regs[0]["anchor_ordinal"] == 4, "mode 2: one region after partition #4")
    rg = regs[0]
    pl = plan.dualboot_placement(rg, m["db_parts"], rg["fill"])
    h = gpt.highest_used_slot(g["primary"]["entries_raw"], 128)
    pre_p, pre_b = d.gpt_areas
    # gate: a placement overlapping D: is refused before anything is written
    bad = [dict(p) for p in pl]
    bad[0]["offset"] -= MiB
    t = FileTarget(img)
    try:
        dualboot.setup_gate(t, bad, d.ranges, n, 512)
        check(False, "mode 2 gate: a partition overlapping D: is refused")
    except errors.Internal:
        check(True, "mode 2 gate: a partition overlapping D: is refused")
    t.close()
    check(sha_file(img) == orig, "mode 2 gate refusal left the image byte-identical")
    t = FileTarget(img)
    j = J()
    dualboot.setup_gate(t, pl, d.ranges, n, 512)
    try:
        t.gate.phase = "gpt"
        t.write(0, bytes(512))
        check(False, "mode 2 gate: sector 0 never written")
    except errors.Internal:
        check(True, "mode 2 gate: sector 0 never written")
    t.gate.phase = "data"
    before = mib_digests(img)
    dualboot.write_data(t, payload, pl, j)
    a, b, g2 = dualboot.write_table(t, g, pl, h + 1, pre_p, pre_b, n, 512, j)
    t.close()
    check(j.phases == ["data_written", "data_verified", "gpt_backup_written", "gpt_primary_written"],
          "mode 2: journal phases %s" % j.phases)
    out = sh("sgdisk", "-v", img, ok=(0, 1, 2))
    check("No problems found" in out, "mode 2: sgdisk -v reports no problems")
    e2 = g2["primary"]["entries"]
    check(len(e2) == 23 and [e["slot"] for e in e2] == list(range(23)), "mode 2: 23 entries in slots 0..22")
    ok = all(g2["primary"]["entries_raw"][e["slot"] * 128:(e["slot"] + 1) * 128] == e["raw"]
             for e in g["primary"]["entries"])
    check(ok, "mode 2: the 5 pre-existing entries byte-identical in their slots")
    with open(img, "rb") as f:
        s0 = f.read(512)
    check(s0 == pre_p[:512], "mode 2: sector 0 unchanged")
    span = (pl[0]["offset"], pl[-1]["offset"] + pl[-1]["length"])
    after = mib_digests(img)
    changed = [u for u in range(len(after)) if after[u] != before[u]]
    bad = [u for u in changed if not (span[0] <= u * MiB and (u + 1) * MiB <= span[1]) and u not in (0, len(after) - 1)]
    check(not bad, "mode 2: every MiB outside the new partitions (and the GPT areas) unchanged (%d changed)" % len(changed))
    check(layout_of(img).g["primary"]["hdr"]["disk"] == hdr["disk"], "mode 2: disk GUID kept")
    for p in pl:
        if p["object"]:
            o = m["objects"][p["object"]]
            okc = sha_file(img, p["offset"], o["raw_size"]) == o["sha256"]
        else:
            okc = all(sha_file(img, p["offset"] + rel, ln) == hashlib.sha256(bytes(ln)).hexdigest()
                      for rel, ln in plan.zero_ranges(p["length"]))
        check(okc, "mode 2: %s content as planned" % p["name"])
    pj = plan.plan_json(hdr["disk"], d.size, "testimg", rg, g["primary"]["entries"], pl)
    check(pj["anchor"]["number"] == 4 and pj["region"]["start"] == span[0], "mode 2: plan.json anchor/region")
    # mode 3 on the result: the partition table returns byte-identical to before the install
    d3 = layout_of(img)
    db = set(p["guid"] for p in m["db_parts"])
    slots = sorted(e["slot"] for e in d3.g["primary"]["entries"] if e["guid"] in db)
    t = FileTarget(img)
    j3 = J()
    a3, b3, g3 = uninstall.remove_table(t, d3.g, slots, d3.gpt_areas[0], d3.gpt_areas[1], n, 512, j3)
    t.close()
    check(a3 == pre_p and b3 == pre_b, "mode 3: GPT areas byte-identical to the pre-install copy (yes)")
    check(j3.phases == ["gpt_backup_written", "gpt_primary_written"], "mode 3: journal phases")
    # mode 3 with an interrupted Android A/B update: the backup copy differs only in vbmeta bits
    t = FileTarget(img)
    dualboot.setup_gate(t, pl, d.ranges, n, 512)
    a, b, g2 = dualboot.write_table(t, g, pl, h + 1, pre_p, pre_b, n, 512, J())
    t.close()
    vb = [p["guid"] for p in m["db_parts"] if p["name"] == "vbmeta_a"][0]
    slot_vb = [e["slot"] for e in g2["primary"]["entries"] if e["guid"] == vb][0]
    arr = bytearray(b[:16384])
    arr[slot_vb * 128 + 54] = 0x3f
    arr[slot_vb * 128 + 55] |= 1
    hb = gpt.header_sector(b[16384:16896], ecrc=zlib.crc32(bytes(arr)))
    with open(img, "r+b") as f:
        f.seek((n - 33) * 512)
        f.write(bytes(arr) + hb)
    d4 = layout_of(img)
    vbg = [p["guid"] for p in m["db_parts"] if p["name"] in ("vbmeta_a", "vbmeta_b")]
    probs, tol = gpt.check_gpt_invalid(d4.g, vbmeta_guids=vbg)
    check(probs == [] and tol, "mode 3: vbmeta-bit difference tolerated")
    check(gpt.check_gpt_invalid(d4.g) != [], "mode 2: the same difference is refused (R_GPT_INVALID)")
    slots = sorted(e["slot"] for e in d4.g["primary"]["entries"] if e["guid"] in db)
    t = FileTarget(img)
    a5, b5, g5 = uninstall.remove_table(t, d4.g, slots, d4.gpt_areas[0], d4.gpt_areas[1], n, 512, J())
    t.close()
    check(a5 == pre_p and b5 == pre_b, "mode 3 (tolerated): both copies written from the primary = pre-install")
    # no region on a disk without free space
    img2 = os.path.join(WORK, "full.img")
    n2 = host_image(img2, free_mib=30)
    d5 = layout_of(img2)
    h5 = d5.g["primary"]["hdr"]
    check(plan.dualboot_regions(d5.g["primary"]["entries"], h5["first"], h5["last"], m["db_parts"], const.RESERVE,
                                512, const.USERDATA_MIN) == [], "mode 2: 30 MiB free -> no region (R_NO_REGION)")
    del n2
    os.unlink(img2)
    os.unlink(img)


def mib_digests(path):
    out = []
    with open(path, "rb") as f:
        while True:
            b = f.read(MiB)
            if not b:
                break
            out.append(hashlib.sha256(b).hexdigest())
    return out


# ------------------------------------------------------------------------------------------------ scan helpers
def test_scan():
    root = os.path.join(WORK, "tree")
    sh("rm", "-rf", root)
    for p in ("ISO", "a/b/c/d/e/f/g", "Windows/x", "SOURCES", "EFI/Microsoft/Boot", "GB-backup/run1"):
        os.makedirs(os.path.join(root, p), exist_ok=True)
    big = 65 * MiB

    def fake_iso(path, label):
        with open(path, "wb") as f:
            f.truncate(big)
            f.seek(32768)
            f.write(b"\x01CD001\x01\x00" + b" " * 32 + label.ljust(32).encode())

    fake_iso(os.path.join(root, "ISO", "googlebook-sb3-installer.iso"), "GBSB3INST")
    fake_iso(os.path.join(root, "a/b/c/d/e/deep.iso"), "GBSB3INST")              # depth 6: found
    fake_iso(os.path.join(root, "a/b/c/d/e/f/g/toodeep.iso"), "GBSB3INST")       # depth 8: not walked
    fake_iso(os.path.join(root, "Windows/x/skip.iso"), "GBSB3INST")              # skip list
    fake_iso(os.path.join(root, "ISO", "other.ISO"), "OTHER")
    with open(os.path.join(root, "ISO", "small.iso"), "wb") as f:
        f.write(b"x")
    open(os.path.join(root, "SOURCES", "Install.WIM"), "wb").close()
    w = scan.walk(root)
    names = sorted(r for r, s in w["iso"])
    check(names == ["/ISO/googlebook-sb3-installer.iso", "/ISO/other.ISO", "/a/b/c/d/e/deep.iso"],
          "walk: depth limit, skip list, size limit, case-insensitive .iso (%s)" % names)
    check(w["gbbackup"] and not w["timed_out"], "walk: GB-backup folder found")
    check(scan.pvd_label(root + "/ISO/googlebook-sb3-installer.iso") == "GBSB3INST" and
          scan.pvd_label(root + "/ISO/other.ISO") == "OTHER" and scan.pvd_label(root + "/ISO/small.iso") is None,
          "pvd_label")
    check(scan.winmedia_check(root) == (True, False), "winmedia: /sources/install.wim (any case)")
    open(os.path.join(root, "EFI/Microsoft/Boot/bootmgfw.efi"), "wb").close()
    check(scan.winmedia_check(root) == (True, True), "winmedia: bootmgfw.efi")
    check(scan.ci_path(root, "efi/MICROSOFT/boot/BOOTMGFW.EFI") is not None and scan.ci_path(root, "nope") is None,
          "ci_path")
    w2 = scan.walk(root, deadline_s=0.0)
    check(w2["timed_out"], "walk: time limit -> timed_out (unscanned)")
    sh("rm", "-rf", root)


# ------------------------------------------------------------------------------------------------ loop devices (root)
def wait_node(path, seconds=10.0):
    """The builder runs udev, which re-reads a loop device's partition table after a writable close: wait for the
    partition node to come back (the installer's initramfs has no udev)."""
    t_end = time.time() + seconds
    while time.time() < t_end:
        sh("udevadm", "settle", ok=(0, 1))
        if os.path.exists(path):
            return True
        time.sleep(0.2)
    return False


def attach(path, writable=True):
    if sh("losetup", "-j", path).strip():
        raise RuntimeError("%s is already attached" % path)
    args = ["losetup", "-P", "-f", "--show", path] if writable else ["losetup", "-r", "-P", "-f", "--show", path]
    dev = sh(*args).strip()
    for _ in range(50):
        if os.path.exists(dev + "p1"):
            break
        time.sleep(0.1)
    return dev


def test_loop():
    img = os.path.join(WORK, "loop.img")
    n = 32 * MiB // 512
    ents = (gpt.build_entry(const.TYPE_BASIC, guid(1), 2048, 2048 + 8191, 0, "Basic data partition") +
            gpt.build_entry(const.TYPE_LINUX, guid(2), 10240, 10240 + 8191, 0, "test")).ljust(16384, b"\0")
    mbr = bytearray(512)
    mbr[446:462] = struct.pack("<BBBBBBBBII", 0, 0, 2, 0, 0xEE, 0xFF, 0xFF, 0xFF, 1, n - 1)
    mbr[510:512] = b"\x55\xaa"
    hdr = bytearray(512)
    struct.pack_into("<8sIIIIQQQQ16sQIII", hdr, 0, b"EFI PART", 0x10000, 92, 0, 0, 1, n - 1, 34, n - 34,
                     uuid.UUID(guid(3)).bytes_le, 2, 128, 128, zlib.crc32(ents))
    ph = gpt.header_sector(bytes(hdr))
    bh = gpt.header_sector(ph, my=n - 1, alt=1, elba=n - 33)
    with open(img, "wb") as f:
        f.truncate(n * 512)
        f.write(bytes(mbr) + ph + ents)
        f.seek((n - 33) * 512)
        f.write(ents + bh)
    dev = attach(img)
    k = os.path.basename(dev)
    mnt = os.path.join(WORK, "mnt")
    os.makedirs(mnt, exist_ok=True)
    try:
        kp = sysfs.kernel_partitions(k)
        check([(p["partno"], p["start512"], p["size512"]) for p in kp] == [(1, 2048, 8192), (2, 10240, 8192)],
              "sysfs: kernel partitions of %s" % k)
        p1 = kp[0]["kname"]
        check(sysfs.is_partition(p1) and sysfs.parent_disk(p1) == k and not sysfs.is_partition(k),
              "sysfs: is_partition / parent_disk")
        check(sysfs.node(p1) == "/dev/" + p1, "sysfs: node() verifies major:minor")
        host = sysfs.name_of_devnum(sysfs.mount_devnum_of_path(img))
        want = sysfs.leaf_disks(host) if host else set()
        check(bool(want) and sysfs.leaf_disks(p1) == want and sysfs.leaf_disks(k) == want,
              "sysfs: leaf disks of %s / %s = %s (the disk holding the image file)" % (k, p1, sorted(want)))
        check(sysfs.transport(k) == "unknown" and sysfs.model(k) == "unknown", "sysfs: transport/model of a loop")
        fd = os.open(dev, os.O_RDONLY | os.O_CLOEXEC)
        try:
            check(sysfs.get_size64(fd) == n * 512 and sysfs.get_ssz(fd) == 512, "ioctl: BLKGETSIZE64 / BLKSSZGET")
            check(sysfs.get_diskseq(fd) == sysfs.diskseq(k), "ioctl: BLKGETDISKSEQ == sysfs diskseq (%s)"
                  % sysfs.diskseq(k))
        finally:
            os.close(fd)
        d = D()
        d.kname = k
        with sysfs.DirectReader(dev) as rd:
            disks.read_layout(d, rd)
        check(len(d.ranges) == 2 and d.ranges[0]["kname"] == p1 and d.ranges[0]["sources"] ==
              ["gpt-primary", "gpt-backup", "kernel"], "union enumeration merges GPT copies and kernel partitions")
        # read-only guard (3.10)
        check(sysfs.set_ro("/dev/" + p1, 1), "BLKROSET 1 on %s confirmed by BLKROGET" % p1)
        fdw = os.open("/dev/" + p1, os.O_WRONLY | os.O_CLOEXEC)
        try:
            os.pwrite(fdw, bytes(512), 0)
            check(False, "a userspace write to the read-only partition fails")
        except OSError as e:
            check(e.errno == errno.EPERM, "a userspace write to the read-only partition fails (%s)"
                  % errno.errorcode.get(e.errno))
        finally:
            os.close(fdw)
        fdw = os.open(dev, os.O_RDWR | os.O_CLOEXEC)
        try:
            check(os.pwrite(fdw, bytes(512), 40 * 512) == 512, "the whole disk stays writable (gated fd path)")
        finally:
            os.close(fdw)
        wait_node("/dev/" + p1)
        sysfs.set_ro("/dev/" + p1, 0)
        sh("mkfs.vfat", "-F", "12", "/dev/" + p1)
        wait_node("/dev/" + p1)
        sysfs.set_ro("/dev/" + p1, 1)
        p = subprocess.run(["mount", "-t", "vfat", "-o", "rw", "/dev/" + p1, mnt], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
        opts = (scan.mounted_at(mnt) or {}).get("options", "")
        if p.returncode == 0:
            sh("umount", mnt)
        check(p.returncode != 0 or opts.split(",")[0] == "ro", "a read-write mount of the read-only partition is "
              "refused by the kernel (mount falls back to read-only: %s)" % opts.split(",")[0])
        # busybox mount (the installer's /bin/mount) behaves the same way
        p = subprocess.run(["busybox", "mount", "-t", "vfat", "-o", "rw", "/dev/" + p1, mnt], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
        opts = (scan.mounted_at(mnt) or {}).get("options", "")
        if p.returncode == 0:
            sh("umount", mnt)
        check(p.returncode != 0 or opts.split(",")[0] == "ro", "busybox mount: read-write refused, falls back to "
              "read-only (%s; %s)" % (opts.split(",")[0], p.stdout.strip()[-50:]))
        p = subprocess.run(["mount", "-t", "vfat", "-o", "ro", "/dev/" + p1, mnt], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
        check(p.returncode == 0, "a read-only mount still works")
        # exclusive open while a partition is mounted -> R_IN_USE
        dk_ = D()
        dk_.kname, dk_.sector, dk_.n_lbas = k, 512, n
        tg = blockio.Target(dk_)
        try:
            tg.open_excl()
            check(False, "O_EXCL open with a mounted partition -> R_IN_USE")
        except errors.Refused as e:
            check(e.code == "R_IN_USE", "O_EXCL open with a mounted partition -> R_IN_USE")
        sh("umount", mnt)
        sysfs.set_ro("/dev/" + p1, 0)
        # identity check and rescan through the O_EXCL fd
        disk = D()
        disk.kname, disk.sector, disk.n_lbas = k, 512, n
        disk.devnum, disk.size, disk.model, disk.diskseq = sysfs.devnum(k), n * 512, sysfs.model(k), sysfs.diskseq(k)
        with sysfs.DirectReader(dev) as rd:
            disk.headtail_sha256 = hashlib.sha256(sysfs.headtail_bytes(rd)).hexdigest()
            disks.read_layout(disk, rd)
        tg = blockio.Target(disk)
        tg.flow = "test"
        tg.open_excl()
        try:
            tg.identity_check()
            check(True, "identity check passes on the unchanged disk")
            # remove partition 2 from the table (gated GPT write), then BLKRRPART on the O_EXCL fd
            pre_p, pre_b = tg.read_gpt_areas()
            ents2 = gpt.zero_slots(disk.g["primary"]["entries_raw"], [1])
            upd = plan.gpt_area_update(pre_p, pre_b, disk.g["primary"]["hdr"], disk.g["backup"]["hdr"], ents2, n)
            tg.gate.areas["gpt"] = [(512, 34 * 512, "p"), ((n - 33) * 512, n * 512, "b")]
            blockio.write_gpt(tg, upd["pieces"])
            with sysfs.DirectReader(dev) as rd:
                g2 = gpt.read_gpt(rd.read, n, 512)
            seq0 = sysfs.get_diskseq(tg.fd)
            blockio.rescan(tg, g2["primary"]["entries"])
            check([p["partno"] for p in sysfs.kernel_partitions(k)] == [1], "BLKRRPART on the O_EXCL fd: kernel "
                                                                              "partitions follow the new table")
            seq1 = sysfs.get_diskseq(tg.fd)
            print("INFO diskseq before/after BLKRRPART: %s / %s (sysfs %s)" % (seq0, seq1, sysfs.diskseq(k)))
            # a changed first sector -> R_DISK_CHANGED
            tg.gate.areas["data"] = [(40 * 512, 41 * 512, "x")]
            tg.gate.phase = "data"
            tg.write(40 * 512, b"\x55" * 512)
            tg.sync()
            try:
                tg.identity_check()
                check(False, "identity check after a change -> R_DISK_CHANGED")
            except errors.Refused as e:
                check(e.code == "R_DISK_CHANGED", "identity check after a change -> R_DISK_CHANGED")
        finally:
            tg.close()
        # environment guard: the real launcher refuses outside the installer
        try:
            run.run_tool(["/bin/sync"])
            check(False, "run_tool refuses outside the installer environment")
        except run.ToolRefused:
            check(True, "run_tool refuses outside the installer environment")
    finally:
        subprocess.run(["umount", mnt], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for p in sysfs.kernel_partitions(k):
            try:
                sysfs.set_ro("/dev/" + p["kname"], 0)
            except OSError:
                pass
        sh("losetup", "-d", dev)
        os.unlink(img)


# ------------------------------------------------------------------------------------------------ audit-2 units (files)
def scripted_prompts(replies):
    """Replace log.prompt with scripted replies; returns the list of prompt ids asked."""
    asked = []
    rep = list(replies)

    def prompt(pid, text):
        asked.append(pid)
        return rep.pop(0) if rep else ""
    log.prompt = prompt
    return asked


def test_audit2_units():
    from gb import flowlib, efi, efivars, journal
    real_prompt = log.prompt
    # Checks with a typed acknowledgement (power.ack / hiber.ack)
    asked = scripted_prompts(["AC-OK"])
    c = flowlib.Checks()
    c.report("x", "R_X", None)
    c.report_ack("power", "R_ON_BATTERY", "power state unknown", "power.ack", "AC-OK", "text")
    c.raise_first()
    check(asked == ["power.ack"], "Checks: ack asked after the checks, 'AC-OK' accepted")
    asked = scripted_prompts(["ac-ok"])
    c = flowlib.Checks()
    c.report_ack("power", "R_ON_BATTERY", "power state unknown", "power.ack", "AC-OK", "text")
    try:
        c.raise_first()
        check(False, "Checks: a wrong acknowledgement refuses")
    except errors.Refused as e:
        check(e.code == "R_ON_BATTERY" and asked == ["power.ack"], "Checks: a wrong acknowledgement refuses (%s)"
              % e.code)
    asked = scripted_prompts(["AC-OK"])
    c = flowlib.Checks()
    c.report_ack("power", "R_ON_BATTERY", "power state unknown", "power.ack", "AC-OK", "text")
    c.report("later", "R_LATER", "refused")
    try:
        c.raise_first()
        check(False, "Checks: an earlier refusal wins, no prompt")
    except errors.Refused as e:
        check(e.code == "R_LATER" and asked == [], "Checks: an earlier refusal wins, no prompt (%s)" % e.code)
    # power check on 'hardware'
    real_vm, real_ps = sysfs.is_vmware, sysfs.power_supplies
    sysfs.is_vmware = lambda: False
    cases = [([("ADP1", "Mains", 1, None)], "ok"), ([("BAT1", "Battery", None, "Discharging")], "refuse"),
             ([("BAT1", "Battery", None, "Charging"), ("BAT2", "Battery", None, "Full")], "ok"),
             ([], "ack"), ([("BAT1", "Battery", None, "Unknown")], "ack")]
    for sup, want in cases:
        sysfs.power_supplies = lambda sup=sup: sup
        c = flowlib.Checks()
        flowlib.check_power(c)
        got = c.results[-1][1]
        check(got == want, "check_power %s -> %s (%s)" % (sup, want, got))
    sysfs.is_vmware = lambda: True
    sysfs.power_supplies = lambda: []
    c = flowlib.Checks()
    flowlib.check_power(c)
    check(c.results[-1][1] == "warn" and not c.acks, "check_power in a VM: warn, no acknowledgement")
    sysfs.is_vmware, sysfs.power_supplies = real_vm, real_ps
    # hibernation: signature decision, BitLocker partitions reported as unchecked
    real_ls, real_head, real_node = run.ntfs_list_root, run.ntfs_read_head, sysfs.node
    sysfs.node = lambda k: "/dev/" + k
    run.ntfs_list_root = lambda node: (0, ["$MFT", "hiberfil.sys", "pagefile.sys"])
    d = D()
    d.ranges = [dict(ntfs=True, kname="nvme0n1p3", rflags=set())]
    for sig, want in ((b"HIBR", "R_HIBERNATED"), (b"hibr", "R_HIBERNATED"), (b"RSTR", "R_HIBERNATED"),
                      (b"wake", None), (b"WAKE", None), (bytes(4), None)):
        run.ntfs_read_head = lambda node, name, sig=sig: (sig + bytes(4092), None)
        code, prob, enc = flowlib.hibernation(d)
        check(code == want and (code is None or "signature" in prob and "powercfg /h off" in prob),
              "hibernation: hiberfil.sys %r -> %s" % (sig, code))
    d.ranges = [dict(ntfs=False, kname="nvme0n1p3", rflags={"bitlocker"}),
                dict(ntfs=True, kname="nvme0n1p4", rflags=set())]
    run.ntfs_list_root = lambda node: (0, ["$MFT"])
    code, prob, enc = flowlib.hibernation(d)
    check(code is None and enc == [1], "hibernation: a BitLocker C: is reported as unchecked (%s %s)" % (code, enc))
    run.ntfs_list_root, run.ntfs_read_head, sysfs.node = real_ls, real_head, real_node
    # destructive window: SIGINT/SIGQUIT/SIGTSTP ignored inside, restored after; flag file names this process
    import signal
    journal.FLOW_FLAG = os.path.join(WORK, "flow.running")
    before = [signal.getsignal(s) for s in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP)]
    with flowlib.destructive():
        inside = [signal.getsignal(s) for s in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP)]
        with open(journal.FLOW_FLAG) as f:
            flag = f.read().strip()
        os.kill(os.getpid(), signal.SIGINT)          # ignored: no KeyboardInterrupt
        time.sleep(0.1)
    after = [signal.getsignal(s) for s in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP)]
    check(all(x == signal.SIG_IGN for x in inside) and after == before and flag == str(os.getpid()) and
          not os.path.exists(journal.FLOW_FLAG), "destructive(): interrupts ignored inside, restored after, flag "
                                                "file removed")
    # EFI step: rollback on an exception AND on KeyboardInterrupt (BaseException), which is then re-raised
    esp = guid(200)
    win_guid = guid(10)
    win_raw = efi.build_load_option("Windows Boot Manager", efi.build_hd_node(1, 2048, 204800, win_guid) +
                                    efi.build_file_node(const.WINDOWS_LOADER) + efi.END_NODE)
    real = {k: getattr(efivars, k) for k in ("rw_window", "snapshot", "boot_order", "read_raw", "boot_next",
                                              "boot_numbers", "load_option")}
    realr = {k: getattr(run, k) for k in ("bootmgr_create", "bootmgr_set_order", "bootmgr_delete",
                                           "bootmgr_delete_next", "bootmgr_list_log")}
    for exc, want_exc in ((RuntimeError("x"), errors.Failed), (KeyboardInterrupt(), KeyboardInterrupt)):
        st = {"vars": {"Boot0000": b"\x07\x00\x00\x00" + win_raw}, "order": [0], "deleted": 0}

        def load_option(n, st=st):
            raw = st["vars"].get(efi.boot_name(n))
            return (efi.parse_load_option(raw[4:]), raw) if raw else (None, None)

        def create(node, partno, st=st):
            st["vars"]["Boot0004"] = b"\x07\x00\x00\x00" + efi.build_load_option(
                const.BOOT_LABEL, efi.build_hd_node(6, 6531072, 1048576, esp) + efi.build_file_node(const.LOADER) +
                efi.END_NODE)
            return True

        def set_order(text, st=st, exc=exc):
            if text.endswith(",0004"):
                raise exc
            st["order"] = [int(x, 16) for x in text.split(",")]
            return True

        def delete(num, st=st):
            st["vars"].pop("Boot" + num, None)
            st["order"] = [x for x in st["order"] if x != int(num, 16)]
            st["deleted"] += 1
            return True
        efivars.rw_window = contextlib_nullcontext
        efivars.snapshot = lambda st=st: dict(st["vars"], BootOrder=bytes(st["order"]))
        efivars.boot_order = lambda st=st: list(st["order"])
        efivars.read_raw = lambda name, st=st: st["vars"].get(name)
        efivars.boot_next = lambda: None
        efivars.boot_numbers = lambda st=st: sorted(int(k[4:], 16) for k in st["vars"])
        efivars.load_option = load_option
        run.bootmgr_create, run.bootmgr_set_order, run.bootmgr_delete = create, set_order, delete
        run.bootmgr_delete_next = lambda: True
        run.bootmgr_list_log = lambda: None
        t = D()
        t.node = "/dev/nvme0n1"
        w = (0, None, st["vars"]["Boot0000"])
        got = None
        try:
            dualboot.efi_create(t, J(), w, 6, 6531072, 1048576, esp, {})
        except BaseException as e:
            got = e
        check(isinstance(got, want_exc) and st["deleted"] == 1 and "Boot0004" not in st["vars"] and
              st["order"] == [0], "EFI step: %s during --bootorder -> rollback ran once, Boot0004 gone, order kept, "
                                  "%s raised" % (type(exc).__name__, type(got).__name__))
    for k, v in real.items():
        setattr(efivars, k, v)
    for k, v in realr.items():
        setattr(run, k, v)
    log.prompt = real_prompt


class contextlib_nullcontext:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ------------------------------------------------------------------------------------------------ audit-2 media (root)
def media_stubs():
    """Stand-ins for the installer's process wrappers (they only run inside the installer environment): blkid and
    read-only probe mounts below WORK/probe; the read-only guard list below WORK."""
    const.PROBE_DIR = os.path.join(WORK, "probe")
    os.makedirs(const.PROBE_DIR, exist_ok=True)
    sysfs.RO_LIST = os.path.join(WORK, "roguard.list")

    def blkid(node):
        p = subprocess.run(["blkid", "-p", "-o", "export", node], stdout=subprocess.PIPE, text=True)
        out = {}
        for ln in p.stdout.splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                out[k] = v
        out["_rc"] = p.returncode
        return out

    def mount_probe(fstype, node, name):
        mp = os.path.join(const.PROBE_DIR, name)
        os.makedirs(mp, exist_ok=True)
        p = subprocess.run(["mount", "-t", fstype, "-o", run.RO_MOUNTS[fstype], node, mp], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True)
        return p.returncode == 0, p.stdout.strip()

    def umount(path):
        p = subprocess.run(["umount", path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return p.returncode == 0, p.stdout.strip()
    run.blkid_probe, run.mount_probe, run.umount = blkid, mount_probe, umount


def media_image(name, layout, fs, files):
    """64 MiB image; layout 'whole' (file system on the whole device) or 'gpt' (one partition); files: rel paths."""
    img = os.path.join(WORK, name + ".img")
    with open(img, "wb") as f:
        f.truncate(64 * MiB)
    if layout == "gpt":
        sh("sgdisk", "-n", "1:2048:0", "-t", "1:0700", img)
    dev = attach(img)
    node = dev if layout == "whole" else dev + "p1"
    wait_node(node)
    sh("mkfs.exfat", node) if fs == "exfat" else sh("mkfs.vfat", "-F", "32" if fs == "fat32" else "16", node)
    mnt = os.path.join(WORK, "mmnt")
    os.makedirs(mnt, exist_ok=True)
    sh("mount", node, mnt)
    for rel in files:
        p = os.path.join(mnt, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(b"x" * 100)
    sh("umount", mnt)
    return img, dev


def scan_disk(dev):
    k = os.path.basename(dev)
    d = disks.Disk(k)
    insp = scan.Inspector()
    with sysfs.DirectReader(dev) as rd:
        disks.read_layout(d, rd)
        disks.media_scan(d, rd, insp)
    return d


def test_media():
    media_stubs()
    cases = [("sf_gbb", "whole", "exfat", ["GB-backup/x/gpt_pre_shrink_primary.bin"], {"gbbackup"}),
             ("sf_win", "whole", "fat32", ["sources/install.wim"], {"winmedia"}),
             ("sf_bm", "whole", "fat16", ["EFI/Microsoft/Boot/bootmgfw.efi"], {"winmedia", "windows"}),
             ("gpt_gbb", "gpt", "exfat", ["GB-backup/x/gpt_pre_shrink_primary.bin"], {"gbbackup"}),
             ("sf_plain", "whole", "exfat", ["data/file.txt"], set())]
    for name, layout, fs, files, want in cases:
        img, dev = media_image(name, layout, fs, files)
        try:
            d = scan_disk(dev)
            fl = d.flags - {"payload"}
            whole = bool(d.ranges) and bool(d.ranges[0].get("whole"))
            check(want <= fl and (fl - want) <= {"windows"} and (whole == (layout == "whole")),
                  "media scan %s (%s %s): flags %s, whole-disk range %s" % (name, layout, fs, sorted(fl), whole))
            if layout == "whole":
                shown = []
                real_out = log._out
                log._out = shown.append
                d.sector = 512
                try:
                    disks.show_ranges(d)
                finally:
                    log._out = real_out
                text = "".join(shown)
                check("whole-disk file system (no partition table)" in text and fs.replace("fat32", "vfat").replace(
                    "fat16", "vfat") in text and "no partitions found" not in text,
                      "wd.confirm listing of %s shows the whole-disk file system (%s)" % (name, text.strip()[-80:]))
        finally:
            for m in sysfs.mountinfo():
                if m["mountpoint"].startswith(const.PROBE_DIR + "/"):
                    subprocess.run(["umount", m["mountpoint"]])
            try:
                sysfs.set_ro(dev, 0)
            except OSError:
                pass
            sh("losetup", "-d", dev)
            os.unlink(img)
    # a blank disk: no range, no flag
    img = os.path.join(WORK, "blank.img")
    with open(img, "wb") as f:
        f.truncate(64 * MiB)
    dev = attach(img)
    try:
        d = scan_disk(dev)
        check(not d.ranges and not d.flags, "media scan of a blank disk: no range, no flag")
    finally:
        sh("losetup", "-d", dev)
        os.unlink(img)
    # read-only guard keyed by kname AND diskseq (3.10)
    img = os.path.join(WORK, "ro1.img")
    with open(img, "wb") as f:
        f.truncate(16 * MiB)
    dev = attach(img)
    k = os.path.basename(dev)
    seq1 = sysfs.diskseq(k)
    try:
        sysfs.guard([k], 1, "test")
        check(sysfs.ro_by_installer(k, seq1) and not sysfs.ro_by_installer(k, seq1 + 1000),
              "roguard list: (kname, diskseq) recorded (%s %s)" % (k, seq1))
        dd = D()
        dd.kname, dd.sector, dd.n_lbas, dd.diskseq = k, 512, 16 * MiB // 512, seq1
        tg = blockio.Target(dd)
        tg.prepare()
        fd = os.open(dev, os.O_RDONLY)
        ro = sysfs.get_ro(fd)
        os.close(fd)
        check(ro == 0, "prepare(): the installer's own read-only flag (same diskseq) is cleared")
    finally:
        sysfs.set_ro(dev, 0)
        sh("losetup", "-d", dev)
    img2 = os.path.join(WORK, "ro2.img")
    with open(img2, "wb") as f:
        f.truncate(16 * MiB)
    dev2 = attach(img2)
    k2 = os.path.basename(dev2)
    seq2 = sysfs.diskseq(k2)
    try:
        sh("blockdev", "--setro", dev2)                # set by the user, not by the installer
        check(seq2 != seq1 and not sysfs.ro_by_installer(k2, seq2),
              "another disk under the kname %s (diskseq %s, was %s) is not covered by the list" % (k2, seq2, seq1))
        dd = D()
        dd.kname, dd.sector, dd.n_lbas, dd.diskseq = k2, 512, 16 * MiB // 512, seq2
        tg = blockio.Target(dd)
        try:
            tg.prepare()
            check(False, "prepare(): a read-only flag the installer did not set -> R_READONLY")
        except errors.Refused as e:
            fd = os.open(dev2, os.O_RDONLY)
            ro = sysfs.get_ro(fd)
            os.close(fd)
            check(e.code == "R_READONLY" and ro == 1, "prepare(): a read-only flag the installer did not set -> "
                                                       "R_READONLY, flag kept (%s)" % e.code)
    finally:
        sysfs.set_ro(dev2, 0)
        sh("losetup", "-d", dev2)
        os.unlink(img)
        os.unlink(img2)
    # an unreadable disk stays listed (flag unreadable) and blocks the cross-disk checks
    from gb import flowlib
    img = os.path.join(WORK, "u.img")
    with open(img, "wb") as f:
        f.truncate(16 * MiB)
    dev = attach(img)
    k = os.path.basename(dev)
    real = (sysfs.CAND_EXCLUDE, sysfs.disk_names, sysfs.DirectReader)
    try:
        import re as _re
        sysfs.CAND_EXCLUDE = _re.compile(r"^(ram|zram|sr|dm-|md|nbd|mtdblock)")
        sysfs.disk_names = lambda: [k]

        class Broken(sysfs.DirectReader):
            def __init__(self, path):
                raise OSError(5, "Input/output error (test)")
        sysfs.DirectReader = Broken
        lst = disks.enumerate_disks(None, None, [], scan.Inspector())
        ok = len(lst) == 1 and "unreadable" in lst[0].flags and lst[0].size == 16 * MiB
        check(ok, "enumerate_disks: a disk that cannot be read is listed with flag unreadable")
        prob = flowlib.unreadable_problem(lst)
        check(prob is not None and k in prob and flowlib.unreadable_problem(lst, exclude=lst[0]) is None,
              "unreadable_problem names it (R_DISK_UNREADABLE text)")
    finally:
        sysfs.CAND_EXCLUDE, sysfs.disk_names, sysfs.DirectReader = real
        sh("losetup", "-d", dev)
        os.unlink(img)


def main():
    if MODE == "files":
        root = os.path.join(WORK, "payload")
        sh("rm", "-rf", root)
        img, m, prim, back = make_payload(root)
        payload = FakePayload(root, m)
        test_wholedisk(payload, img, prim, back)
        test_dualboot(payload)
        test_scan()
        test_audit2_units()
        os.unlink(img)
        sh("rm", "-rf", root)
    elif MODE == "loop":
        if os.geteuid() != 0:
            sys.exit("loop mode needs root")
        test_loop()
    elif MODE == "media":
        if os.geteuid() != 0:
            sys.exit("media mode needs root")
        test_media()
    else:
        sys.exit("mode: files | loop | media")
    fails = RESULTS.count(False)
    print("SUMMARY builder-%s: %d passed, %d failed" % (MODE, RESULTS.count(True), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
