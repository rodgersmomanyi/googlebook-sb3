"""--selftest (3.1): import every module, run pure self-tests, touch no device, start no process.
Prints exactly 'GB-SELFTEST OK' on success (exit 0)."""
import importlib
import json
import posixpath
import struct
import sys
import traceback

from . import const
from . import efi
from . import gpt
from . import manifest as mf
from . import plan
from . import run
from .util import crc32, guid_bytes, hex_attr, sha256

MODULES = ("const", "util", "gpt", "plan", "manifest", "efi", "errors", "log", "run", "sysfs", "scan", "disks",
           "journal", "efivars", "blockio", "flowlib", "stick", "state", "wholedisk", "dualboot", "uninstall",
           "tools", "ui", "selftest")
MiB = 1 << 20


# ------------------------------------------------------------------------------------------------ fake disk
class FakeDisk:
    """Sparse in-memory disk: dict lba -> 512 bytes."""

    def __init__(self, n_lbas):
        self.n = n_lbas
        self.s = {}

    def read(self, lba, count):
        if lba < 0 or lba + count > self.n:
            raise OSError("out of range")
        return b"".join(self.s.get(lba + i, bytes(512)) for i in range(count))

    def write(self, lba, data):
        assert len(data) % 512 == 0
        for i in range(len(data) // 512):
            self.s[lba + i] = bytes(data[i * 512:(i + 1) * 512])


def fake_guid(i):
    return "%08x-0000-4000-8000-%012x" % (0x5e1f7e57, i)


def host_disk(c_mib=1024, d_mib=26624, d_shrunk_mib=2048):
    """A disk shaped like gpt_validate.py mkimage (ESP, MSR, C:, D: shrunk, 1 MiB gap, Recovery)."""
    sizes = [100 * MiB, 16 * MiB, c_mib * MiB, d_mib * MiB, 900 * MiB]
    gaps = [0, 0, 0, 0, MiB]
    types = [const.TYPE_ESP, const.TYPE_MSR, const.TYPE_BASIC, const.TYPE_BASIC, const.TYPE_WINRE]
    names = ["EFI system partition", "Microsoft reserved partition", "Basic data partition", "Basic data partition",
             "Basic data partition"]
    attrs = [0, 0, 0, 0, 0x8000000000000001]
    offs, o = [], MiB
    for s, g in zip(sizes, gaps):
        o += g
        offs.append(o)
        o += s
    usable_end = o + 335360
    size = usable_end + 33 * 512
    n = size // 512
    last = n - 1
    sizes[3] = d_shrunk_mib * MiB
    ents = b"".join(gpt.build_entry(t, fake_guid(10 + i), off // 512, (off + s) // 512 - 1, at, nm)
                    for i, (t, nm, at, off, s) in enumerate(zip(types, names, attrs, offs, sizes)))
    ents = ents.ljust(128 * 128, b"\0")
    ecrc = crc32(ents)
    disk = FakeDisk(n)
    mbr = bytearray(512)
    mbr[446:462] = struct.pack("<BBBBBBBBII", 0, 0, 2, 0, 0xEE, 0xFF, 0xFF, 0xFF, 1, min(last, 0xFFFFFFFF))
    mbr[510:512] = b"\x55\xaa"
    disk.write(0, bytes(mbr))
    hdr = bytearray(512)
    struct.pack_into("<8sIIIIQQQQ16sQIII", hdr, 0, b"EFI PART", 0x10000, 92, 0, 0, 1, last, 34,
                     usable_end // 512 - 1, guid_bytes(fake_guid(1)), 2, 128, 128, ecrc)
    p_hdr = gpt.header_sector(bytes(hdr))
    b_hdr = gpt.header_sector(p_hdr, my=last, alt=1, elba=n - 33)
    disk.write(1, p_hdr)
    disk.write(2, ents)
    disk.write(n - 33, ents)
    disk.write(n - 1, b_hdr)
    return disk


# ------------------------------------------------------------------------------------------------ sample manifest
WD_LAYOUT = [("EFI-SYSTEM", 2048, 1050623), ("boot_a", 1050624, 1181695), ("boot_b", 1181696, 1312767),
             ("init_boot_a", 1312768, 1329151), ("init_boot_b", 1329152, 1345535),
             ("vendor_boot_a", 1345536, 1411071), ("vendor_boot_b", 1411072, 1476607),
             ("vbmeta_a", 1476608, 1484799), ("vbmeta_b", 1484800, 1492991), ("pvmfw_a", 1492992, 1501183),
             ("pvmfw_b", 1501184, 1509375), ("misc", 1509376, 1517567), ("metadata", 1517568, 1648639),
             ("frp_persist", 1648640, 1652735), ("desktop_security_persist", 1652736, 1685503),
             ("desktop_security_storage", 1685504, 1751039), ("super", 1751040, 26916863),
             ("userdata", 26916864, 56276991)]
T_LBA = 56279073
OBJ_SIZES = {"esp-usb": 536870912, "esp-internal": 536870912, "boot_a": 67108864, "boot_b": 67108864,
             "init_boot_a": 8388608, "init_boot_b": 8388608, "vendor_boot_a": 33554432, "vendor_boot_b": 33554432,
             "vbmeta_a": 4194304, "vbmeta_b": 4194304, "pvmfw_a": 4194304, "pvmfw_b": 4194304,
             "super": 12884901888}
DB_SIZES = {"EFI-SYSTEM": 536870912, "boot_a": 67108864, "boot_b": 67108864, "init_boot_a": 8388608,
            "init_boot_b": 8388608, "vendor_boot_a": 33554432, "vendor_boot_b": 33554432, "vbmeta_a": 4194304,
            "vbmeta_b": 4194304, "pvmfw_a": 4194304, "pvmfw_b": 4194304, "misc": 4194304, "metadata": 67108864,
            "frp_persist": 2097152, "desktop_security_persist": 16777216, "desktop_security_storage": 33554432,
            "super": 12884901888}
H = "ab" * 32


def sample_gpt_bins():
    """gpt-primary.bin / gpt-backup.bin of a synthetic image with the whole-disk layout above."""
    ents = b""
    for i, (nm, f, l) in enumerate(WD_LAYOUT):
        t = const.TYPE_ESP if nm == "EFI-SYSTEM" else (const.TYPE_VBMETA if nm.startswith("vbmeta") else
                                                         const.TYPE_LINUX)
        at = 0x010f000000000000 if nm == "vbmeta_b" else 0
        ents += gpt.build_entry(t, fake_guid(100 + i), f, l, at, nm)
    ents = ents.ljust(16384, b"\0")
    last = T_LBA - 1
    mbr = bytearray(512)
    mbr[446:462] = struct.pack("<BBBBBBBBII", 0, 0, 2, 0, 0xEE, 0xFF, 0xFF, 0xFF, 1, last)
    mbr[510:512] = b"\x55\xaa"
    hdr = bytearray(512)
    struct.pack_into("<8sIIIIQQQQ16sQIII", hdr, 0, b"EFI PART", 0x10000, 92, 0, 0, 1, last, 34, T_LBA - 34,
                     guid_bytes(fake_guid(99)), 2, 128, 128, crc32(ents))
    p_hdr = gpt.header_sector(bytes(hdr))
    b_hdr = gpt.header_sector(p_hdr, my=last, alt=1, elba=T_LBA - 33)
    return bytes(mbr) + p_hdr + ents, ents + b_hdr


def sample_manifest():
    prim, back = sample_gpt_bins()
    objs = {}
    for oid, size in OBJ_SIZES.items():
        chunks, off, k = [], 0, 0
        while off < size:
            n = min(const.CHUNK, size - off)
            chunks.append({"path": "gbsb3/parts/%s.%03d.zst" % (oid, k), "raw_offset": off, "raw_size": n,
                           "raw_sha256": H, "zst_size": n // 2, "zst_sha256": H})
            off += n
            k += 1
        objs[oid] = {"raw_size": size, "sha256": H, "chunks": chunks}
    wdp = []
    for i, (nm, f, l) in enumerate(WD_LAYOUT):
        t = const.TYPE_ESP if nm == "EFI-SYSTEM" else (const.TYPE_VBMETA if nm.startswith("vbmeta") else
                                                         const.TYPE_LINUX)
        obj = "esp-usb" if nm == "EFI-SYSTEM" else (nm if nm in OBJ_SIZES else None)
        wdp.append({"slot": i, "name": nm, "type": t, "guid": fake_guid(100 + i),
                    "attributes": hex_attr(0x010f000000000000 if nm == "vbmeta_b" else 0), "first_lba": f,
                    "last_lba": l, "size": (l - f + 1) * 512, "object": obj, "blank": obj is None,
                    "growable": nm == "userdata"})
    dbp = []
    for i, nm in enumerate(const.GB_NAMES):
        t = const.TYPE_ESP if nm == "EFI-SYSTEM" else (const.TYPE_VBMETA if nm.startswith("vbmeta") else
                                                         const.TYPE_LINUX)
        obj = "esp-internal" if nm == "EFI-SYSTEM" else (nm if nm in OBJ_SIZES else None)
        fill = nm == "userdata"
        dbp.append({"name": nm, "type": t, "guid": fake_guid(200 + i),
                    "attributes": hex_attr(0x010f000000000000 if nm == "vbmeta_b" else 0),
                    "size": None if fill else DB_SIZES[nm], "fill": fill,
                    "min_size": const.USERDATA_MIN if fill else None, "blank": obj is None, "object": obj,
                    "file": (nm + ".img") if obj else None, "file_size": DB_SIZES[nm] if obj else None,
                    "sha256": H if obj else None})
    return {"format": "gbsb3-iso-payload/1", "build_id": "20261025-153000-1a2b3c4d",
            "created_utc": "2026-10-25T15:30:00Z", "volume_label": "GBSB3INST", "android_build": "TEST",
            "system_slot": "b", "kernel_release": "6.8.0-147-generic", "kernel_nx_compat_cleared": True,
            "sector_size": 512, "align_bytes": const.ALIGN, "chunk_raw_bytes": const.CHUNK,
            "compression": {"tool": "zstd", "level": 9, "long": 27, "version": "1.5.5"},
            "source": {"usb_image": {"file": "x.img", "size": T_LBA * 512, "sha256": H},
                       "dualboot_export": {"manifest_sha256": H, "created": "x", "esp_sha256": H}},
            "objects": objs,
            "whole_disk": {"image_bytes": T_LBA * 512, "total_lba": T_LBA, "disk_guid": fake_guid(99),
                           "first_usable_lba": 34, "last_usable_lba": T_LBA - 34, "entries_lba": 2,
                           "backup_entries_lba": T_LBA - 33, "entry_count": 128, "entry_size": 128,
                           "entries_crc32": "0x%08x" % crc32(prim[1024:]),
                           "gpt_primary": {"path": "gbsb3/wd/gpt-primary.bin", "bytes": 17408, "sha256": sha256(prim)},
                           "gpt_backup": {"path": "gbsb3/wd/gpt-backup.bin", "bytes": 16896, "sha256": sha256(back)},
                           "super_guid": fake_guid(116), "partitions": wdp},
            "dual_boot": {"export_manifest": "gbsb3/dualboot/manifest.json", "export_manifest_sha256": H,
                          "esp_guid": fake_guid(200), "super_guid": fake_guid(216), "cmdline": "x",
                          "reserve_bytes": const.RESERVE, "userdata_min_bytes": const.USERDATA_MIN,
                          "boot_entry": {"label": const.BOOT_LABEL, "loader": const.LOADER}, "partitions": dbp}}


# ------------------------------------------------------------------------------------------------ tests
def t_imports():
    for m in MODULES:
        importlib.import_module("gb." + m)


def t_manifest():
    m = mf.parse_manifest(json.dumps(sample_manifest()).encode(), "20261025-153000-1a2b3c4d")
    assert len(m["db_parts"]) == 18 and m["db_parts"][-1]["fill"]
    assert len(m["objects"]["super"]["chunks"]) == 24
    bad = sample_manifest()
    bad["objects"]["super"]["chunks"][3]["raw_offset"] += 512
    try:
        mf.parse_manifest(json.dumps(bad).encode())
        raise AssertionError("broken chunk list accepted")
    except mf.ManifestError:
        pass
    bad = sample_manifest()
    bad["dual_boot"]["partitions"][7]["attributes"] = hex_attr(0x8000000000000000)
    try:
        mf.parse_manifest(json.dumps(bad).encode())
        raise AssertionError("bad attribute bits accepted")
    except mf.ManifestError:
        pass
    mtext = ("GBSB3-PAYLOAD-MARKER 1\nbuild_id=20261025-153000-1a2b3c4d\nvolume_label=GBSB3INST\n"
             "manifest=gbsb3/manifest.json\nmanifest_sha256=%s\n" % sha256(b"x"))
    mk = mf.parse_marker(mtext.encode())
    assert mf.check_marker_for_build(mk, b"x", "20261025-153000-1a2b3c4d") is None
    assert mf.check_marker_for_build(mk, b"y", "20261025-153000-1a2b3c4d") is not None
    assert mf.check_marker_for_build(mk, b"x", "20261025-153000-00000000") is not None
    for broken in (mtext.replace("\n", "\r\n"), mtext.replace("volume_label=GBSB3INST\n", ""),
                   mtext.replace("MARKER 1", "MARKER 2")):
        try:
            mf.parse_marker(broken.encode())
            raise AssertionError("broken marker accepted")
        except mf.ManifestError:
            pass
    return m


def t_dualboot(m):
    disk = host_disk()
    g = gpt.read_gpt(disk.read, disk.n, 512)
    assert gpt.check_no_gpt(g) is None
    assert gpt.check_gpt_invalid(g) == [], gpt.check_gpt_invalid(g)
    assert gpt.check_gpt_geometry(g) == [], gpt.check_gpt_geometry(g)
    ur = gpt.union_ranges(disk.read, disk.n, 512, g, ())
    assert len(ur) == 5 and all(r["sources"] == ["gpt-primary", "gpt-backup"] for r in ur)
    hdr = g["primary"]["hdr"]
    regs = plan.dualboot_regions(g["primary"]["entries"], hdr["first"], hdr["last"], m["db_parts"])
    assert len(regs) == 1, regs
    rg = regs[0]
    assert rg["start"] == 3343908864 and rg["anchor_ordinal"] == 4, rg
    assert rg["fill"] == 10411 * MiB, rg["fill"]
    assert plan.userdata_from_reply("", rg["fill"]) == rg["fill"]
    assert plan.userdata_from_reply("8", rg["fill"]) == 8 << 30
    assert plan.userdata_from_reply("7", rg["fill"]) is None
    assert plan.userdata_from_reply("11", rg["fill"]) is None
    assert plan.userdata_from_reply("x", rg["fill"]) is None
    pl = plan.dualboot_placement(rg, m["db_parts"], rg["fill"])
    assert pl[0]["offset"] == rg["start"] and len(pl) == 18
    assert pl[-1]["offset"] + pl[-1]["length"] == rg["usable_end"]
    h = gpt.highest_used_slot(g["primary"]["entries_raw"], 128)
    assert h == 4
    new = plan.new_entries_for_slots(pl, h + 1)
    ents = gpt.put_entries(g["primary"]["entries_raw"], new)
    pre_p, pre_b = disk.read(0, 34), disk.read(disk.n - 33, 33)
    upd = plan.gpt_area_update(pre_p, pre_b, hdr, g["backup"]["hdr"], ents, disk.n)
    hp, hb = upd["pieces"]["primary_hdr"][1], upd["pieces"]["backup_hdr"][1]
    assert gpt.header_fields_equal_except_crcs(hdr["raw"], hp) and upd["primary"][:512] == pre_p[:512]
    disk2 = FakeDisk(disk.n)
    disk2.s = dict(disk.s)
    for key in ("backup_entries", "backup_hdr", "primary_entries", "primary_hdr"):
        disk2.write(*upd["pieces"][key])
    assert disk2.read(0, 34) == upd["primary"] and disk2.read(disk.n - 33, 33) == upd["backup"]
    g2 = gpt.read_gpt(disk2.read, disk2.n, 512)
    assert gpt.check_gpt_invalid(g2) == [] and len(g2["primary"]["entries"]) == 23
    for e in g["primary"]["entries"]:
        assert g2["primary"]["entries_raw"][e["slot"] * 128:(e["slot"] + 1) * 128] == e["raw"]
    o2 = gpt.ordinals(g2["primary"]["entries"])
    assert o2[5] == 6 and o2[22] == 23
    pj = plan.plan_json(hdr["disk"], disk.n * 512, "nvme0n1", rg, g["primary"]["entries"], pl)
    assert list(pj) == ["format", "disk_id", "disk_size", "target", "existing", "test_truncated", "anchor",
                        "region", "base", "partitions"]
    assert pj["anchor"]["number"] == 4 and len(pj["base"]) == 5 and pj["partitions"][-1]["blank"] is True
    assert pj["partitions"][0]["file"] == "EFI-SYSTEM.img" and pj["partitions"][11]["file_size"] == 0
    # mode 3: zero the 18 slots -> the original entry array and headers again
    db = set(p["guid"] for p in m["db_parts"])
    slots = [e["slot"] for e in g2["primary"]["entries"] if e["guid"] in db]
    assert slots == list(range(5, 23))
    back = gpt.zero_slots(g2["primary"]["entries_raw"], slots)
    assert back == g["primary"]["entries_raw"]
    assert gpt.header_sector(hp, ecrc=crc32(back)) == hdr["raw"]
    upd3 = plan.gpt_area_update(disk2.read(0, 34), disk2.read(disk.n - 33, 33), g2["primary"]["hdr"],
                                g2["backup"]["hdr"], back, disk.n)
    assert upd3["primary"] == pre_p and upd3["backup"] == pre_b
    # vbmeta tolerance (4.3 check 5)
    vb = [p["guid"] for p in m["db_parts"] if p["name"] in ("vbmeta_a", "vbmeta_b")]
    slot_vb = [e["slot"] for e in g2["primary"]["entries"] if e["guid"] == vb[1]][0]
    alt = bytearray(g2["primary"]["entries_raw"])
    alt[slot_vb * 128 + 54] ^= 0x0F
    alt[slot_vb * 128 + 55] ^= 0x01
    alt = bytes(alt)
    disk3 = FakeDisk(disk.n)
    disk3.s = dict(disk2.s)
    disk3.write(disk.n - 33, alt)
    disk3.write(disk.n - 1, gpt.header_sector(hb, ecrc=crc32(alt)))
    g3 = gpt.read_gpt(disk3.read, disk3.n, 512)
    probs, tol = gpt.check_gpt_invalid(g3, vbmeta_guids=vb)
    assert probs == [] and tol, probs
    assert gpt.check_gpt_invalid(g3) != []
    alt2 = bytearray(alt)
    alt2[slot_vb * 128 + 40] ^= 0x01
    disk3.write(disk.n - 33, bytes(alt2))
    disk3.write(disk.n - 1, gpt.header_sector(hb, ecrc=crc32(bytes(alt2))))
    probs, tol = gpt.check_gpt_invalid(gpt.read_gpt(disk3.read, disk3.n, 512), vbmeta_guids=vb)
    assert probs and not tol
    # a disk without free space: no region
    full = host_disk(d_shrunk_mib=26624)
    gf = gpt.read_gpt(full.read, full.n, 512)
    hf = gf["primary"]["hdr"]
    assert plan.dualboot_regions(gf["primary"]["entries"], hf["first"], hf["last"], m["db_parts"]) == []
    # union enumeration also sees a backup-only entry and MBR / EBR partitions
    disk4 = FakeDisk(disk.n)
    disk4.s = dict(disk2.s)
    disk4.write(1, bytes(512))
    ur4 = gpt.union_ranges(disk4.read, disk4.n, 512)
    assert len(ur4) == 23 and gpt.check_no_gpt(gpt.read_gpt(disk4.read, disk4.n, 512)) is not None
    mbrdisk = FakeDisk(1 << 20)
    mbr = bytearray(512)
    mbr[446:462] = struct.pack("<BBBBBBBBII", 0x80, 0, 0, 0, 0x07, 0, 0, 0, 2048, 4096)
    mbr[462:478] = struct.pack("<BBBBBBBBII", 0, 0, 0, 0, 0x0F, 0, 0, 0, 8192, 65536)
    mbr[510:512] = b"\x55\xaa"
    mbrdisk.write(0, bytes(mbr))
    ebr1 = bytearray(512)
    ebr1[446:462] = struct.pack("<BBBBBBBBII", 0, 0, 0, 0, 0x83, 0, 0, 0, 2048, 1024)
    ebr1[462:478] = struct.pack("<BBBBBBBBII", 0, 0, 0, 0, 0x05, 0, 0, 0, 8192, 8192)
    ebr1[510:512] = b"\x55\xaa"
    ebr2 = bytearray(512)
    ebr2[446:462] = struct.pack("<BBBBBBBBII", 0, 0, 0, 0, 0x07, 0, 0, 0, 2048, 2048)
    ebr2[462:478] = struct.pack("<BBBBBBBBII", 0, 0, 0, 0, 0x05, 0, 0, 0, 0, 8192)   # loops back: stop
    ebr2[510:512] = b"\x55\xaa"
    mbrdisk.write(8192, bytes(ebr1))
    mbrdisk.write(16384, bytes(ebr2))
    um = gpt.union_ranges(mbrdisk.read, mbrdisk.n, 512)
    assert [(r["first"], r["count"]) for r in um] == [(2048, 4096), (8192, 65536), (10240, 1024), (18432, 2048)], um


def t_wholedisk(m):
    prim, back = sample_gpt_bins()
    wd = m["wd"]
    b = plan.whole_disk_gpt(prim, back, wd, T_LBA, False)
    assert b["mbr"] + b["primary_hdr"] + b["entries"] == prim and b["entries"] + b["backup_hdr"] == back
    assert b["gap"] == (56276992, 56279040)
    N = 67108864
    b2 = plan.whole_disk_gpt(prim, back, wd, N, True)
    assert b2["userdata_last"] == 67104767
    h1 = gpt.parse_header(b2["primary_hdr"])
    h2 = gpt.parse_header(b2["backup_hdr"])
    assert h1["crc_ok"] and h2["crc_ok"] and h1["alt"] == N - 1 and h1["last"] == N - 34
    assert h2["my"] == N - 1 and h2["alt"] == 1 and h2["elba"] == N - 33 and h1["ecrc"] == crc32(b2["entries"])
    assert struct.unpack_from("<I", b2["mbr"], 458)[0] == N - 1
    e = gpt.parse_entries(b2["entries"], 128)
    assert e[17]["last"] == 67104767 and e[17]["raw"][:40] == gpt.parse_entries(prim[1024:], 128)[17]["raw"][:40]
    assert b2["gap"] == (67104768, N - 33)
    b3 = plan.whole_disk_gpt(prim, back, wd, N, False)
    assert b3["entries"] == prim[1024:] and b3["gap"] == (56276992, 56279040)
    parts = plan.whole_disk_partitions(wd, b2["userdata_last"])
    assert parts[17]["length"] == (67104767 - 26916864 + 1) * 512
    assert plan.zero_ranges(14 << 30) == [(0, 64 * MiB), ((14 << 30) - 64 * MiB, 64 * MiB)]
    assert plan.zero_ranges(64 * MiB) == [(0, 64 * MiB)]
    assert plan.zero_ranges(512 * MiB, 512 * MiB) == []
    assert plan.zero_ranges(5 * MiB, 4 * MiB) == [(4 * MiB, MiB)]


def t_efi():
    esp = fake_guid(200)
    fpl = efi.build_hd_node(6, 6531072, 1048576, esp) + efi.build_file_node(const.LOADER) + efi.END_NODE
    opt = efi.parse_load_option(efi.build_load_option(const.BOOT_LABEL, fpl))
    assert efi.is_ours(opt, esp) and efi.check_new_entry(opt, 6, 6531072, 1048576, esp) == []
    assert efi.check_new_entry(opt, 5, 6531072, 1048576, esp) != []
    pre = struct.pack("<BBH", 2, 1, 12) + b"\0" * 8          # an ACPI-like node before HD()
    win = efi.parse_load_option(efi.build_load_option(
        "Windows Boot Manager", pre + efi.build_hd_node(1, 2048, 204800, fake_guid(10)) +
        efi.build_file_node("\\EFI\\MICROSOFT\\BOOT\\BOOTMGFW.EFI") + efi.END_NODE))
    assert win["nodes_before_hd"] == 1
    assert efi.is_windows_boot_manager(win, 1, [1, 2], {fake_guid(10)})
    assert not efi.is_windows_boot_manager(win, 1, [2], {fake_guid(10)})
    assert not efi.is_windows_boot_manager(win, 1, [1], {fake_guid(11)})
    inact = efi.parse_load_option(efi.build_load_option(
        "Windows Boot Manager", efi.build_hd_node(1, 2048, 204800, fake_guid(10)) +
        efi.build_file_node("EFI\\Microsoft\\Boot\\bootmgfw.efi") + efi.END_NODE, attrs=0))
    assert not efi.is_windows_boot_manager(inact, 1, [1], {fake_guid(10)})
    slash = efi.parse_load_option(efi.build_load_option(
        "x", efi.build_hd_node(1, 2048, 204800, fake_guid(10)) + efi.build_file_node("/EFI/Microsoft/Boot/bootmgfw.efi")
        + efi.END_NODE))
    assert not efi.path_equal_ci(slash["file_path"], const.WINDOWS_LOADER)
    nolead = efi.parse_load_option(efi.build_load_option(
        "x", efi.build_hd_node(1, 2048, 204800, fake_guid(10)) + efi.build_file_node("EFI\\Microsoft\\Boot\\") +
        efi.build_file_node("bootmgfw.efi") + efi.END_NODE))
    assert efi.path_equal_ci(nolead["file_path"], const.WINDOWS_LOADER)
    assert efi.parse_u16_list(b"\x00\x00\x05\x00") == [0, 5] and efi.fmt_order([0, 5]) == "0000,0005"
    try:
        efi.parse_load_option(b"\x01\x00\x00\x00\x40\x00" + "x\0".encode("utf-16-le"))
        raise AssertionError("truncated load option accepted")
    except ValueError:
        pass


class FakeCtx:
    """Validator context without a system: plain path rules."""

    def __init__(self):
        self.permits = run.Permits()

    realpath = staticmethod(posixpath.normpath)

    @staticmethod
    def is_block_node(p):
        return p.startswith("/dev/") and ".." not in p

    @staticmethod
    def is_partition_node(p):
        return p in ("/dev/nvme0n1p3", "/dev/sda1", "/dev/sdb1")

    @staticmethod
    def is_regular_file(p):
        return p.endswith(".iso") or p.endswith(".zst")

    @staticmethod
    def payload_root():
        return "/run/gb/probe/sr0"

    @staticmethod
    def loop_backing_below_probe(dev):
        return dev == "/dev/loop0"

    @staticmethod
    def ntfs_allowed(node):
        return node == "/dev/sdb1"


ACCEPT = [
    [run.BLKID, "-p", "-o", "export", "/dev/sda1"],
    [run.LSBLK, "--json", "--bytes", "-o", run.LSBLK_COLS],
    [run.LOSETUP, "-r", "-f", "--show", "/run/gb/probe/sda1/ISO/x.iso"],
    [run.LOSETUP, "-d", "/dev/loop0"],
    [run.LOSETUP, "-l", "-J"],
    [run.MOUNT, "-t", "iso9660", "-o", "ro", "/dev/sr0", "/run/gb/probe/sr0"],
    [run.MOUNT, "-t", "vfat", "-o", "ro,shortname=mixed,utf8", "/dev/sda1", "/run/gb/probe/sda1"],
    [run.MOUNT, "-t", "exfat", "-o", "ro", "/dev/sda1", "/run/gb/probe/sda1"],
    [run.MOUNT, "-t", "ntfs3", "-o", "ro", "/dev/sdb1", "/run/gb/probe/sdb1"],
    [run.MOUNT, "-o", "remount,ro,nosuid,nodev,noexec", "/sys/firmware/efi/efivars"],
    [run.UMOUNT, "/run/gb/probe/sda1"],
    [run.UMOUNT, "/run/gb/esp"],
    [run.ZSTD, "-dcq", "--long=27", "/run/gb/probe/sr0/gbsb3/parts/super.000.zst"],
    [run.EFIBOOTMGR, "--verbose"],
    [run.NTFSLS, "-a", "-s", "-p", "/", "/dev/nvme0n1p3"],
    [run.DMESG], [run.SYNC], [run.SH, "-i"], [run.REBOOT, "-f"], [run.POWEROFF, "-f"],
]
ACCEPT_WITH = [
    (("rw-mount", ("/dev/nvme0n1p6", "/run/gb/esp")), [run.MOUNT, "-t", "vfat", "-o", "rw,flush", "/dev/nvme0n1p6",
                                                       "/run/gb/esp"]),
    (("rw-mount", ("/dev/sdc1", "/run/gb/savelog")), [run.MOUNT, "-t", "exfat", "-o", "rw", "/dev/sdc1",
                                                      "/run/gb/savelog"]),
    (("rw-mount", ("/dev/sdc1", "/run/gb/savelog")), [run.MOUNT, "-t", "vfat", "-o", "rw,flush,shortname=mixed,utf8",
                                                      "/dev/sdc1", "/run/gb/savelog"]),
    (("efivars-rw", True), [run.MOUNT, "-o", "remount,rw", "/sys/firmware/efi/efivars"]),
    (("target", "/dev/nvme0n1"), [run.SGDISK, "-v", "/dev/nvme0n1"]),
    (("target", "/dev/nvme0n1"), [run.SGDISK, "-p", "/dev/nvme0n1"]),
    (("target", "/dev/nvme0n1"), [run.PARTX, "-u", "/dev/nvme0n1"]),
    (("efi-create", ("/dev/nvme0n1", "6")), [run.EFIBOOTMGR, "--create-only", "--disk", "/dev/nvme0n1", "--part", "6",
                                             "--label", "Googlebook OS", "--loader", "\\EFI\\BOOT\\BOOTX64.EFI"]),
    (("efi-order", "0000,0001,0005"), [run.EFIBOOTMGR, "--bootorder", "0000,0001,0005"]),
    (("efi-delete", "0005"), [run.EFIBOOTMGR, "--bootnum", "0005", "--delete-bootnum"]),
    (("efi-delete-next", True), [run.EFIBOOTMGR, "--delete-bootnext"]),
    (("fsck", "/dev/nvme0n1p6"), [run.FSCK_VFAT, "-n", "/dev/nvme0n1p6"]),
    (("ntfs-read", ("/dev/nvme0n1p3", "hiberfil.sys")), [run.NTFSCAT, "/dev/nvme0n1p3", "/hiberfil.sys"]),
]
REJECT = [
    ["/bin/rm", "-r", "/run/gb"],
    ["blkid", "-p", "-o", "export", "/dev/sda1"],
    [run.BLKID, "/dev/sda1"],
    [run.BLKID, "-p", "-o", "export", "/dev/sda1;reboot"],
    [run.BLKID, "-p", "-o", "export", "/dev/sda1\n"],
    [run.MOUNT, "-t", "vfat", "-o", "rw", "/dev/sda1", "/run/gb/probe/sda1"],
    [run.MOUNT, "-t", "vfat", "-o", "ro", "/dev/sda1", "/mnt"],
    [run.MOUNT, "-t", "ntfs3", "-o", "ro", "/dev/nvme0n1p3", "/run/gb/probe/nvme0n1p3"],
    [run.MOUNT, "-t", "ntfs", "-o", "ro", "/dev/sdb1", "/run/gb/probe/sdb1"],
    [run.MOUNT, "-t", "vfat", "-o", "rw,flush", "/dev/nvme0n1p6", "/run/gb/esp"],
    [run.MOUNT, "-o", "remount,rw", "/sys/firmware/efi/efivars"],
    [run.MOUNT, "-t", "iso9660", "-o", "ro", "/dev/sr0", "/run/gb/probe/../esp"],
    [run.UMOUNT, "/"], [run.UMOUNT, "/run/gb"], [run.UMOUNT, "/run/gb/../../mnt"], [run.UMOUNT, "-l", "/run/gb/esp"],
    [run.ZSTD, "-d", "/run/gb/probe/sr0/gbsb3/parts/super.000.zst"],
    [run.ZSTD, "-dcq", "--long=27", "/etc/passwd.zst"],
    [run.ZSTD, "-dcq", "--long=27", "/run/gb/probe/sr0/../../../etc/x.zst"],
    [run.SGDISK, "-v", "/dev/nvme0n1"],
    [run.PARTX, "-u", "/dev/nvme0n1"],
    [run.EFIBOOTMGR, "-c"], [run.EFIBOOTMGR, "-v"], [run.EFIBOOTMGR, "-D"], [run.EFIBOOTMGR, "-O"],
    [run.EFIBOOTMGR, "-n", "0001"], [run.EFIBOOTMGR, "-B", "-b", "0001"], [run.EFIBOOTMGR, "-o", "0000"],
    [run.EFIBOOTMGR, "--bootorder", ""], [run.EFIBOOTMGR, "--bootorder"],
    [run.EFIBOOTMGR, "--bootorder", "0000,0001"],
    [run.EFIBOOTMGR, "--bootnum", "0005", "--delete-bootnum"],
    [run.EFIBOOTMGR, "--delete-bootnext"],
    [run.EFIBOOTMGR, "--create-only", "--disk", "/dev/nvme0n1", "--part", "6", "--label", "Linux", "--loader",
     "\\EFI\\BOOT\\BOOTX64.EFI"],
    [run.LOSETUP, "-f", "/run/gb/probe/sda1/x.iso"],
    [run.LOSETUP, "-r", "-f", "--show", "/tmp/x.iso"],
    [run.LOSETUP, "-d", "/dev/loop1"],
    [run.LOSETUP, "-D"],
    [run.NTFSCAT, "/dev/nvme0n1p3", "/hiberfil.sys"],
    [run.NTFSLS, "-a", "-s", "-p", "/", "/dev/nvme0n1"],
    [run.SH, "-c", "true"], [run.SH],
    [run.REBOOT], [run.DMESG, "-C"],
    [run.FSCK_VFAT, "-a", "/dev/sda1"], [run.FSCK_VFAT, "-n", "/dev/sda1"],
    [run.PARTX, "-d", "/dev/nvme0n1"],
    [], "not a list",
]


def t_run_validation():
    for argv in ACCEPT:
        run.validate(argv, FakeCtx())
    for (kind, val), argv in ACCEPT_WITH:
        ctx = FakeCtx()
        ctx.permits.grant(kind, val)
        run.validate(argv, ctx)
        if kind in ("rw-mount", "efi-create", "efi-order", "efi-delete", "efi-delete-next", "fsck", "ntfs-read"):
            try:
                run.validate(argv, ctx)          # one-shot permits are consumed
                raise AssertionError("permit %s reused" % kind)
            except run.ToolRefused:
                pass
    for argv in REJECT:
        try:
            run.validate(argv, FakeCtx())
        except run.ToolRefused:
            continue
        raise AssertionError("accepted: %r" % (argv,))
    ctx = FakeCtx()
    ctx.permits.grant("target", "/dev/nvme0n1")
    for argv in ([run.SGDISK, "-o", "/dev/nvme0n1"], [run.SGDISK, "-v", "/dev/sda"], [run.PARTX, "-u", "/dev/sda"]):
        try:
            run.validate(argv, ctx)
            raise AssertionError("accepted: %r" % (argv,))
        except run.ToolRefused:
            pass


def t_util():
    from .util import parse_sha256sums, sha256sums_text, marker_value
    txt = sha256sums_text([("a.bin", b"a"), ("b.json", b"{}")])
    assert parse_sha256sums(txt) == {"a.bin": sha256(b"a"), "b.json": sha256(b"{}")}
    assert marker_value("a b") == '"a b"' and marker_value('x"y') == "x?y" and marker_value(None) == "-"
    from . import state
    files = state.files_for_esp({"x.json": {"a": 1}, "y.bin": b"\0"}, "log", "20261025-153000-1a2b3c4d", fake_guid(1))
    assert state.verify_sums(files)


def t_fat_verdict():
    v = run.fat_check_verdict
    head = "fsck.fat 4.2 (2021-01-31)\n"
    tail = "Leaving filesystem unchanged.\n/dev/nvme0n1p6: 20 files, 386934/1032412 clusters\n"
    note = ("Label in boot sector is 'GBOOK_ESP', but there is no volume label in root directory.\n"
            "  Auto-removing label from boot sector.\n\n")
    iconv = ("Cannot initialize conversion from codepage 850 to ANSI_X3.4-1968: Invalid argument\n"
             "Cannot initialize conversion from ANSI_X3.4-1968 to codepage 850: Invalid argument\n"
             "Using internal CP850 conversion table\n")
    assert v(0, head + "/dev/x: 1 files, 1/2 clusters\n") == (True, "clean")
    assert v(1, head + note + tail) == (True, "label")
    assert v(1, head + note + tail + "\n" + iconv) == (True, "label")
    assert not v(1, head + tail + "\n" + iconv)[0]
    assert not v(1, head + tail)[0]
    assert not v(1, head + note + "/EFI/x\n  Contains a free cluster (5). Assuming EOF.\n" + tail)[0]
    assert not v(1, head + "There are differences between boot sector and its backup.\n" + tail)[0]
    assert not v(4, head + note + tail)[0] and not v(2, "")[0]


def t_power():
    from .util import power_verdict as pv
    assert pv([("ADP1", "Mains", 1, None), ("BAT1", "Battery", None, "Discharging")])[0] == "ac"
    assert pv([("ADP1", "Mains", 0, None), ("BAT1", "Battery", None, "Charging")])[0] == "battery"
    assert pv([("BAT1", "Battery", None, "Discharging"), ("BAT2", "Battery", None, "Full")])[0] == "battery"
    assert pv([("BAT1", "Battery", None, "Charging"), ("BAT2", "Battery", None, "Full")])[0] == "ac"
    assert pv([("BAT1", "Battery", None, "Not charging")])[0] == "ac"
    assert pv([("BAT1", "Battery", None, "Unknown")])[0] == "unknown"
    assert pv([("BAT1", "Battery", None, None)])[0] == "unknown"
    assert pv([])[0] == "unknown"
    assert pv([("ucsi-source-psy-1", "USB", 1, None)])[0] == "ac"
    assert pv([("ucsi-source-psy-1", "USB", None, None)])[0] == "unknown"


def t_hiber():
    from .util import hiber_signature as hs
    for sig in (b"hibr", b"HIBR", b"rstr", b"RSTR"):
        assert hs(sig + bytes(4092)) == (True, sig.decode())
    for sig in (b"wake", b"WAKE", b"link", b"\0\0\0\0", b"xyz"):
        assert hs(sig + bytes(100))[0] is False
    assert hs(b"") == (False, "(empty)") and hs(bytes(8))[1] == "zeros" and hs(b"WAKE")[1] == "WAKE"


def t_input():
    from .util import keyboard_names, parse_input_devices
    text = ('I: Bus=0019 Vendor=0000 Product=0001 Version=0000\nN: Name="Power Button"\nP: Phys=PNP0C0C/button/'
            'input0\nH: Handlers=kbd event0\n\nI: Bus=0011 Vendor=0001 Product=0001 Version=ab41\nN: Name="AT '
            'Translated Set 2 keyboard"\nU: Uniq=SECRET\nH: Handlers=sysrq kbd event1 leds\n\nN: Name="ImPS/2 '
            'Mouse"\nH: Handlers=mouse0 event2\n\nN: Name="Microsoft Surface Keyboard"\nH: Handlers=sysrq kbd '
            'leds event3\n')
    devs = parse_input_devices(text)
    assert len(devs) == 4 and all("SECRET" not in n + h for n, h in devs), devs
    assert keyboard_names(devs) == ["AT Translated Set 2 keyboard", "Microsoft Surface Keyboard"]


TESTS = [("imports", t_imports), ("util", t_util), ("efi", t_efi), ("run_validation", t_run_validation),
         ("fat_verdict", t_fat_verdict), ("power", t_power), ("hiber", t_hiber), ("input", t_input)]


def main():
    try:
        for name, fn in TESTS:
            fn()
        m = t_manifest()
        t_dualboot(m)
        t_wholedisk(m)
    except Exception:
        sys.stdout.write("GB-SELFTEST FAIL\n" + traceback.format_exc())
        sys.stdout.flush()
        return 1
    sys.stdout.write("GB-SELFTEST OK\n")
    sys.stdout.flush()
    return 0
