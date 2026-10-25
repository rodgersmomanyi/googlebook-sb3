"""Verify a built Googlebook disk image (build_usb.py output) or an ESP image file before it is used
(WAVE2_PLAN_v2 B-03, section 3.2).

  python -I verify_image.py [IMG] [--expect-tree auto|native|vm|vmtest-gfx] [--krel FILE] [--no-gfxdriver]
                            [--compare-export ESP]
  python -I verify_image.py --esp FILE [--boot usb|internal] [--super-guid G] [--expect-tree ...] [--krel FILE]
                            [--no-gfxdriver] [--compare-export ESP]
  python -I verify_image.py --extract IMG ESP_PATH OUTDIR     (IMG: a disk image or an ESP image; OUTDIR new or empty)

Image mode (IMG, default C:\\GB-x86\\out\\googlebook-sb3-usb.img):
  VG protective MBR, primary + backup GPT headers and entry CRCs, the expected partitions, no overlaps, alignment
  VS vbmeta_a/b slot attributes (a slot marked successful)
  VC copied partitions identical to lapis.bin (full compare; super: first/last 64 MiB + 16 samples)
  VB blank partitions zero at start and end
  then the ESP checks on the EFI-SYSTEM partition (its super GUID taken from the GPT).
ESP checks (the tree kind comes from the default entry's gb.hw= unless --expect-tree names it):
  V1  FAT32 parses; size 536,870,912 B == the partition/file size; native: free >= max(initrd-*.img) + 144 MiB
  V2  loader.conf default gb-<slot> + editor yes; entries exactly gb-<slot>.conf (+ gb-<slot>-nvtest.conf native);
      nvtest = default + " [NVIDIA test]" title + " gb.nouveau=test" options; no gb.nouveau= in the default entry, no
      gb.gfxdriver= anywhere; every androidboot.boot_part_uuid= names the super GUID; --boot: a native default entry
      has gb.boot=<boot> (VM entries have no gb.boot=)
  V3  gb/inject.gz gunzips and is the first segment of initrd-<slot>.img; the cpio parses; no duplicate path
  V4  krel from the bzImage setup header; every .ko (cpio, and the payload's from inject\\native\\modules) has that
      vermagic and a module signature whose signer is a certificate built into vmlinuz; --krel FILE matches
  V5  none of the 5 opt-in modules in a native modules.load*, none of their .ko in the native cpio
  V6  no backup/superseded file names in the cpio        V7  native: the dGPU payload (INTERFACES 6.1/6.2)
  V8  native gb.rc content (15.1-v12 and the other component rules)   V8b every /odm and gb_* service binary is packed
  V9  native build.prop: exactly one ro.gfx.driver.0 (none with --no-gfxdriver), no forbidden graphics props; VM
      build.prop: no ro.gfx.driver. (vmtest-gfx: exactly the one line)   V10 no priv-app
  V11 the tree kind's dGPU rules (VM: no dGPU stack; vmtest-gfx: exactly the test additions; native: no gbstage)
  V12 every component manifest's files/payload/symlinks/rc/build_prop are in the image
  V13 the vulkan.nouveau.so symlink and the empty payload mount points   V14 no libvulkan.so
  V15 the GbGpu / GbGfxDriver APKs   V16 the default entry's native options   V17 --compare-export: same tree as ESP
  V18 gb/slot_<slot>.stamp and initrd-<slot>.img   V19 modules.load names this vmlinuz (md5)
  V20 /odm/etc/gb/gfx_build_id   V21 gb_nouveau.sh has GB_PANIC_TIMEOUT=30   V22 gfx_app_prefs.default, tu117.sha256
  V23 no gb_gfxdriver.sh   V24 VM kinds: gb.hw=vm and console=ttyS0 (vmtest-gfx: the test title)
Read-only: images, lapis.bin and the host inputs are opened "rb"; only --extract writes (into OUTDIR).
Exit 0 and last line "RESULT: OK"; exit 1 and "RESULT: <n> problem(s)" with one "PROBLEM: V<n> ..." line each;
exit 2 = usage. Module API (build_iso.py imports this file by path): esp_of_disk, fat_files, fat_parse, cpio_entries,
gunzip_padded, native_tree, erofs_listing, kernel_release, compare_export.
"""
import argparse
import glob
import hashlib
import io
import json
import os
import re
import struct
import sys
import time
import uuid
import zipfile
import zlib

SECTOR = 512
MiB = 1 << 20
HERE = os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__))   # workspace (README: GB_HOME)
GB_BUILD = os.environ.get("GB_BUILD") or (HERE if os.path.isdir(os.path.join(HERE, "native")) else r"C:\GB-build")
os.environ.setdefault("GB_HOME", HERE)   # component manifests may use ${GB_HOME} and ${GB_BUILD}
os.environ.setdefault("GB_BUILD", GB_BUILD)
DEFAULT_IMG = os.path.join(HERE, "out", "googlebook-sb3-usb.img")
LAPIS = os.path.join(HERE, "lapis.bin")
ESP_SIZE = 536870912
EXPECT = ["EFI-SYSTEM", "boot_a", "boot_b", "init_boot_a", "init_boot_b", "vendor_boot_a", "vendor_boot_b", "vbmeta_a",
          "vbmeta_b", "pvmfw_a", "pvmfw_b", "misc", "metadata", "frp_persist", "desktop_security_persist",
          "desktop_security_storage", "super", "userdata"]
COPIED = ["boot_a", "boot_b", "init_boot_a", "init_boot_b", "vendor_boot_a", "vendor_boot_b", "vbmeta_a", "vbmeta_b",
          "pvmfw_a", "pvmfw_b", "super"]
BLANK = ["misc", "metadata", "frp_persist", "desktop_security_persist", "desktop_security_storage", "userdata"]
# host inputs the native checks compare against (tests may point these elsewhere)
HOST = {
    "components": os.path.join(HERE, r"inject\native\components"),
    "modules": os.path.join(HERE, r"inject\native\modules"),
    "firmware": os.path.join(HERE, r"inject\native\firmware"),
    "fw_sha": os.path.join(GB_BUILD, r"native\nouveau-kernel\firmware_staged_sha256.txt"),
    "nk_fw": os.path.join(GB_BUILD, r"native\nouveau-kernel\firmware"),
    "gbgpu_sha": os.path.join(GB_BUILD, r"native\gpu-ui\out\GbGpu.apk.sha256"),
    # lapis vendor_dlkm HDA codec modules that stage_kernel copies unchanged into the native tree (A-03): their
    # vermagic names the lapis kernel and they are unsigned; they load through modversions (only the vermagic part
    # after the release must match). V4 accepts exactly byte-identical copies of these files.
    "lapis_mods": os.path.join(GB_BUILD, r"native\audio\modules"),
}
SIG_TRAILER = b"~Module signature appended~\n"
OPTIN = ("drm_exec", "drm_gpuvm", "gpu_sched", "drm_ttm_helper", "nouveau")          # names with "-" -> "_"
OPTIN_FILES = ("drm_exec.ko", "drm_gpuvm.ko", "gpu-sched.ko", "drm_ttm_helper.ko", "nouveau.ko")
BAD_NAME = re.compile(r"\.pre-|\.merged|\.orig|\.v1\.1|\.v1\.2r1|superseded/|\.bak$|\.part$")
BAD_PROP = ("ro.hardware.vulkan", "ro.hardware.egl", "ro.hwui.use_vulkan", "debug.hwui.renderer",
            "ro.vendor.gb.gfx_default", "persist.vendor.gb.gfx_mode")
VM_FORBIDDEN = ("nouveau.ko", "firmware/nvidia", "vulkan.nouveau.so", "gb_nouveau", "nvk_probe", "gb_gpu_compute",
                "app/GbGpu", "GbGfxDriver", "gb_gfxdriver", "gfx_build_id", "gfx_app_prefs.default",
                "gb_gfx_selftest", "dgpu")
VM_RC_FORBIDDEN = ("gb_nouveau", "gb_gfx_selftest", "nvk_probe", "gb_gpu_compute", "gb_gfxdriver")
VMTEST_GFX = {"gbstage/app/GbGpu/GbGpu.apk": 0o100644, "gbstage/app/GbGfxDriver/GbGfxDriver.apk": 0o100644,
              "gbstage/etc/gb/gfx_app_prefs.default": 0o100644, "gbstage/vup/bin/gb_gpu_ui_setup.sh": 0o100755}
VMTEST_RC_MARK = "\n# ---- component gpu_ui (VM test) ----\n"
VMTEST_TITLE = "(VM test, gfx components)"
GFXDRIVER_MEMBERS = {"AndroidManifest.xml", "resources.arsc", "assets/sphal_libraries.txt", "lib/x86_64/libgb_nvk.so",
                     "lib/x86_64/vulkan.intel.so"}
MODULES_DGPU = b"drm_exec.ko\ndrm_gpuvm.ko\ngpu-sched.ko\ndrm_ttm_helper.ko\nnouveau.ko\n"
SYMLINK_ICD = ("gbnative/vup/lib64/hw/vulkan.nouveau.so", b"/odm/dgpu/lib64/hw/vulkan.nouveau.so")
# INTERFACES 15.1-v12 (exact, below the file's comment header)
NOUVEAU_RC_V12 = """on early-init
    mkdir /dev/gb_dgpu 0700 root root

on post-fs-data
    mkdir /data/vendor/gb 0751 root shell
    mkdir /data/vendor/gb/report 0750 root shell
    mkdir /data/vendor/gb/notify 0770 root shell
    mkdir /data/vendor/gb/payload 0700 root root
    mkdir /data/vendor/gb/selftest 0700 root root

service gb_nouveau /system/bin/sh /vendor/bin/gb_nouveau.sh auto
    user root
    group root shell
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_nouveau_off /system/bin/sh /vendor/bin/gb_nouveau.sh off
    user root
    group root shell
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_dgpu_dmesg /system/bin/sh /vendor/bin/gb_nouveau.sh dmesg-stream
    user root
    group root shell log
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_dgpu_report /system/bin/sh /vendor/bin/gb_dgpu_report.sh report
    user root
    group root shell log readproc graphics
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_dgpu_test /system/bin/sh /vendor/bin/gb_dgpu_report.sh test
    user root
    group root shell log readproc graphics
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_gfx_selftest /system/bin/sh /vendor/bin/gb_dgpu_report.sh selftest
    user root
    group root shell log readproc graphics
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_notify /system/bin/sh /vendor/bin/gb_notify.sh
    user shell
    group shell
    seclabel u:r:shell:s0
    oneshot
    disabled

on property:sys.boot_completed=1
    start gb_nouveau

on property:vendor.gb.nouveau.booted=1 && property:persist.vendor.gb.gfx_mode=auto
    start gb_nouveau

on property:vendor.gb.nouveau.booted=1 && property:persist.vendor.gb.gfx_mode=nvidia
    start gb_nouveau

on property:vendor.gb.nouveau.booted=1 && property:persist.vendor.gb.gfx_mode=intel
    start gb_nouveau_off

on property:vendor.gb.dgpu_report=1
    setprop vendor.gb.dgpu_report 0
    start gb_dgpu_report

on property:vendor.gb.dgpu_report=2
    setprop vendor.gb.dgpu_report 0
    start gb_dgpu_test

on property:vendor.gb.dgpu.dmesg=1
    start gb_dgpu_dmesg

on property:vendor.gb.dgpu.dmesg=0
    stop gb_dgpu_dmesg

on property:vendor.gb.notify=*
    start gb_notify
"""


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def is_reg(mode):
    return (mode & 0o170000) == 0o100000


def is_dir(mode):
    return (mode & 0o170000) == 0o040000


def is_lnk(mode):
    return (mode & 0o170000) == 0o120000


# ------------------------------------------------------------------------------------------------------------ GPT

def gpt_read(f, lba, problems=None):
    """GPT header at LBA lba of an open image: dict(my, alt, first, last, disk, parts{name: ...}, raw) or None."""
    def bad(msg):
        if problems is not None:
            problems.append(msg)
    f.seek(lba * SECTOR)
    h = f.read(92)
    if h[:8] != b"EFI PART":
        bad(f"no GPT header at LBA {lba}")
        return None
    hsize, crc = struct.unpack_from("<II", h, 12)
    hh = bytearray(h[:hsize])
    struct.pack_into("<I", hh, 16, 0)
    if zlib.crc32(bytes(hh)) != crc:
        bad(f"header CRC mismatch at LBA {lba}")
    my, alt, first, last = struct.unpack_from("<QQQQ", h, 24)
    ent_lba, n, esz, ecrc = struct.unpack_from("<QIII", h, 72)
    if esz < 128 or n > 1024:
        bad(f"GPT header at LBA {lba}: unsupported entry geometry {n} x {esz}")
        return None
    f.seek(ent_lba * SECTOR)
    raw = f.read(n * esz)
    if zlib.crc32(raw) != ecrc:
        bad(f"entry array CRC mismatch (header at LBA {lba})")
    parts = {}
    for i in range(n):
        e = raw[i * esz:(i + 1) * esz]
        if e[:16] == bytes(16):
            continue
        a, b = struct.unpack_from("<QQ", e, 32)
        parts[e[56:128].decode("utf-16-le", "replace").rstrip("\0")] = dict(
            type=str(uuid.UUID(bytes_le=e[:16])), guid=str(uuid.UUID(bytes_le=e[16:32])), first=a, last=b,
            attrs=struct.unpack_from("<Q", e, 48)[0])
    return dict(my=my, alt=alt, first=first, last=last, disk=str(uuid.UUID(bytes_le=h[56:72])), parts=parts, raw=raw)


def esp_of_disk(path):
    """Bytes of the EFI-SYSTEM partition of a GPT disk image (read-only)."""
    with open(path, "rb") as f:
        g = gpt_read(f, 1)
        if not g or "EFI-SYSTEM" not in g["parts"]:
            raise ValueError(f"{path}: no GPT with an EFI-SYSTEM partition")
        p = g["parts"]["EFI-SYSTEM"]
        f.seek(p["first"] * SECTOR)
        return f.read((p["last"] - p["first"] + 1) * SECTOR)


# ------------------------------------------------------------------------------------------------------------ FAT32

def fat_parse(esp):
    """Read-only FAT32 (+ LFN) reader: ({path: bytes}, info). info: bytes, cluster_size, clusters, free (bytes),
    dirs (set of directory paths). Raises ValueError on anything that is not a sane FAT32 image."""
    if len(esp) < 1024 or esp[510:512] != b"\x55\xaa":
        raise ValueError("no boot sector signature")
    bps, spc, res, nf, rootent, tot16 = struct.unpack_from("<HBHBHH", esp, 11)
    fatsz16 = struct.unpack_from("<H", esp, 22)[0]
    tot32, fatsz, = struct.unpack_from("<II", esp, 32)
    root = struct.unpack_from("<I", esp, 44)[0]
    if bps not in (512, 1024, 2048, 4096) or spc == 0 or spc & (spc - 1) or nf == 0 or res == 0:
        raise ValueError(f"bad BPB (bytes/sector {bps}, sectors/cluster {spc}, reserved {res}, FATs {nf})")
    if rootent != 0 or fatsz16 != 0 or fatsz == 0 or esp[82:90] != b"FAT32   ":
        raise ValueError("not FAT32")
    total = tot32 or tot16
    size = total * bps
    if size > len(esp):
        raise ValueError(f"the FAT says {size:,} B, the image has {len(esp):,} B")
    data0 = (res + nf * fatsz) * bps
    cs = bps * spc
    clusters = (total - res - nf * fatsz) // spc
    if clusters < 65525:
        raise ValueError(f"{clusters} clusters: too few for FAT32")
    nent = min(fatsz * bps // 4, clusters + 2)
    fat = struct.unpack_from("<%dI" % nent, esp, res * bps)
    free = sum(1 for v in fat[2:clusters + 2] if (v & 0x0FFFFFFF) == 0) * cs

    def chain(c):
        out, seen = [], 0
        while 2 <= c < 0x0FFFFFF8:
            if c >= clusters + 2 or c >= nent:
                raise ValueError(f"cluster {c} out of range")
            out.append(c)
            seen += 1
            if seen > clusters:
                raise ValueError("cluster chain loop")
            c = fat[c] & 0x0FFFFFFF
        return out

    def read(c, size=None):
        cl = chain(c) if c else []
        parts, i = [], 0
        while i < len(cl):                          # contiguous runs are read with one slice
            j = i
            while j + 1 < len(cl) and cl[j + 1] == cl[j] + 1:
                j += 1
            o = data0 + (cl[i] - 2) * cs
            parts.append(esp[o:o + (j - i + 1) * cs])
            i = j + 1
        b = b"".join(parts)
        if size is not None:
            if size > len(b):
                raise ValueError(f"file of {size} B has only {len(b)} B of clusters")
            b = b[:size]
        return b

    files, dirs = {}, set()

    def walk(c, path, depth):
        if depth > 16:
            raise ValueError("directory tree too deep")
        raw = read(c)
        lfn, lfn_sum = [], None
        for i in range(0, len(raw) - 31, 32):
            e = raw[i:i + 32]
            if e[0] == 0:
                break
            if e[0] == 0xE5:
                lfn = []
                continue
            attr = e[11]
            if attr == 0x0F:
                if e[0] & 0x40:
                    lfn, lfn_sum = [], e[13]
                lfn.insert(0, (e[1:11] + e[14:26] + e[28:32]).decode("utf-16-le", "replace"))
                continue
            sn = e[0:11]
            if attr & 0x08:                             # volume label
                lfn = []
                continue
            s = 0
            for ch in sn:
                s = (((s & 1) << 7) + (s >> 1) + ch) & 0xFF
            if lfn and lfn_sum == s:
                name = "".join(lfn).split("\0")[0]
            else:
                base, ext = sn[:8].decode("ascii", "replace").rstrip(), sn[8:].decode("ascii", "replace").rstrip()
                if e[12] & 0x08:
                    base = base.lower()
                if e[12] & 0x10:
                    ext = ext.lower()
                name = base + ("." + ext if ext else "")
            lfn = []
            if name in (".", ".."):
                continue
            if not name or "/" in name or "\\" in name:
                raise ValueError(f"bad file name {name!r} in {path or '/'}")
            cl = (struct.unpack_from("<H", e, 20)[0] << 16) | struct.unpack_from("<H", e, 26)[0]
            full = path + name
            if attr & 0x10:
                dirs.add(full)
                walk(cl, full + "/", depth + 1)
            else:
                files[full] = read(cl, struct.unpack_from("<I", e, 28)[0])

    walk(root, "", 0)
    return files, dict(bytes=size, cluster_size=cs, clusters=clusters, free=free, dirs=dirs)


def fat_files(esp):
    """{path: bytes} of every file in a FAT32 image (paths with "/", case as stored)."""
    return fat_parse(esp)[0]


# ------------------------------------------------------------------------------------------------------------ cpio

def gunzip_padded(gz):
    """gunzip one gzip member followed only by zero padding (build_esp.inject_gz)."""
    d = zlib.decompressobj(31)
    out = d.decompress(gz) + d.flush()
    if not d.eof:
        raise ValueError("gzip stream truncated")
    if d.unused_data.strip(b"\0"):
        raise ValueError("data after the gzip member")
    return out


def cpio_entries(buf):
    """[(path, mode, data)] of a newc cpio (data: bytes for files, the target for symlinks, None for directories)."""
    out, off = [], 0
    while True:
        if off + 110 > len(buf) or buf[off:off + 6] not in (b"070701", b"070702"):
            raise ValueError(f"bad cpio header at offset {off}")
        try:
            fields = [int(buf[off + 6 + 8 * k: off + 14 + 8 * k], 16) for k in range(13)]
        except ValueError:
            raise ValueError(f"bad cpio header fields at offset {off}") from None
        mode, fsize, nsize = fields[1], fields[6], fields[11]
        if nsize < 1 or off + 110 + nsize > len(buf):
            raise ValueError(f"bad cpio name size at offset {off}")
        name = buf[off + 110:off + 110 + nsize - 1].decode("utf-8", "replace")
        doff = (off + 110 + nsize + 3) & ~3
        if doff + fsize > len(buf):
            raise ValueError(f"cpio entry {name} runs past the end")
        if name == "TRAILER!!!":
            if buf[doff:].strip(b"\0"):
                raise ValueError("data after the cpio trailer")
            return out
        data = None if is_dir(mode) else bytes(buf[doff:doff + fsize])
        out.append((name, mode, data))
        off = (doff + fsize + 3) & ~3


def native_tree(esp_files):
    """{path: (mode, data)} of the gbnative/ entries of the ESP's gb/inject.gz (first occurrence of each path)."""
    t = {}
    for p, m, d in cpio_entries(gunzip_padded(esp_files["gb/inject.gz"])):
        if p.startswith("gbnative/") and p not in t:
            t[p] = (m, d)
    return t


# ------------------------------------------------------------------------------------------------------------ erofs

def erofs_listing(img):
    """[(type, mode, uid, gid, size, path)] of every entry of an EROFS image (names, modes and sizes only; the logic
    of nouveau-kernel\\payload\\erofs_ls.py: superblock at 1024, 4 KiB blocks, compact/extended inodes, FLAT_PLAIN /
    FLAT_INLINE directories). The root is ".". Raises ValueError on anything else."""
    BS = 4096
    types = {1: "f", 2: "d", 3: "c", 4: "b", 5: "p", 6: "s", 7: "l"}

    def rd(off, ln):
        if off < 0 or off + ln > len(img):
            raise ValueError("erofs: read past the end of the image")
        return img[off:off + ln]

    sb = rd(1024, 128)
    if struct.unpack_from("<I", sb, 0)[0] != 0xE0F5E1E2:
        raise ValueError("not an EROFS image (magic)")
    if sb[12] != 12:
        raise ValueError(f"erofs: block size 2^{sb[12]} unsupported")
    root_nid = struct.unpack_from("<H", sb, 14)[0]
    meta = struct.unpack_from("<I", sb, 40)[0]

    def inode(nid):
        off = meta * BS + nid * 32
        h = rd(off, 64)
        fmt, xic, mode = struct.unpack_from("<HHH", h, 0)
        if fmt & 1:
            size = struct.unpack_from("<Q", h, 8)[0]
            u = struct.unpack_from("<I", h, 16)[0]
            uid, gid = struct.unpack_from("<II", h, 24)
            isz = 64
        else:
            size = struct.unpack_from("<I", h, 8)[0]
            u = struct.unpack_from("<I", h, 16)[0]
            uid, gid = struct.unpack_from("<HH", h, 24)
            isz = 32
        xsz = 0 if xic == 0 else 12 + (xic - 1) * 4
        return dict(off=off, mode=mode, size=size, layout=(fmt >> 1) & 7, u=u, uid=uid, gid=gid, isz=isz, xsz=xsz)

    def data(ino):
        size, lay = ino["size"], ino["layout"]
        if lay == 0:
            return rd(ino["u"] * BS, size)
        if lay == 2:
            nfull = size // BS
            tail = size - nfull * BS
            d = rd(ino["u"] * BS, nfull * BS) if nfull else b""
            if tail:
                d += rd(ino["off"] + ino["isz"] + ino["xsz"], tail)
            return d
        raise ValueError(f"erofs: directory layout {lay} unsupported")

    def readdir(ino):
        d = data(ino)
        out = []
        for bo in range(0, len(d), BS):
            blk = d[bo:bo + BS]
            n = struct.unpack_from("<H", blk, 8)[0] // 12
            ents = [struct.unpack_from("<QHBB", blk, i * 12) for i in range(n)]
            for i, (nid, no, ft, _) in enumerate(ents):
                end = ents[i + 1][1] if i + 1 < n else len(blk)
                out.append((blk[no:end].split(b"\0")[0].decode("utf-8", "replace"), nid, ft))
        return out

    lines = []

    def walk(ino, path, depth):
        if depth > 32:
            raise ValueError("erofs: directory tree too deep")
        for name, nid, ft in readdir(ino):
            if name in (".", ".."):
                continue
            ci = inode(nid)
            p = path + "/" + name if path else name
            lines.append((types.get(ft, "?"), ci["mode"] & 0o7777, ci["uid"], ci["gid"], ci["size"], p))
            if ft == 2:
                walk(ci, p, depth + 1)

    root = inode(root_nid)
    lines.append(("d", root["mode"] & 0o7777, root["uid"], root["gid"], root["size"], "."))
    walk(root, "", 0)
    return sorted(lines, key=lambda x: x[5])


# ------------------------------------------------------------------------------------------------------------ kernel

def kernel_release(bz):
    """First token of the bzImage setup header's kernel_version string (C-08)."""
    if len(bz) < 0x300 or bz[0x202:0x206] != b"HdrS":
        raise ValueError("vmlinuz is not a bzImage (no HdrS)")
    off = struct.unpack_from("<H", bz, 0x20E)[0] + 0x200
    s = bz[off:off + 256].split(b"\0")[0].decode("ascii", "replace").split()
    if not s:
        raise ValueError("vmlinuz: empty kernel_version")
    return s[0]


def lz4_block(src):
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        tok = src[i]
        i += 1
        lit = tok >> 4
        if lit == 15:
            b = 255
            while b == 255:
                b = src[i]
                i += 1
                lit += b
        if lit:
            out += src[i:i + lit]
            i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        ml = tok & 15
        if ml == 15:
            b = 255
            while b == 255:
                b = src[i]
                i += 1
                ml += b
        ml += 4
        st = len(out) - off
        if off == 0 or st < 0:
            raise ValueError("LZ4: bad match offset")
        if ml <= off:
            out += out[st:st + ml]
        else:
            pat = out[st:]
            q, r = divmod(ml, off)
            out += pat * q + pat[:r]
    return bytes(out)


def lz4_legacy(data):
    """Decode an LZ4 legacy frame (magic 02 21 4c 18; the kernel's bzImage payload)."""
    if data[:4] != b"\x02\x21\x4c\x18":
        raise ValueError("not an LZ4 legacy frame")
    out, o, n = [], 4, len(data)
    while o + 4 <= n:
        sz = struct.unpack_from("<I", data, o)[0]
        if sz == 0x184C2102:
            o += 4
            continue
        if sz == 0 or o + 4 + sz > n:               # the 4-byte size the kernel build appends, or the end
            break
        out.append(lz4_block(data[o + 4:o + 4 + sz]))
        o += 4 + sz
    if not out:
        raise ValueError("LZ4: no block")
    return b"".join(out)


def kernel_payload(bz):
    """The decompressed kernel (vmlinux) of a bzImage with an LZ4 or gzip payload."""
    setup = bz[0x1F1] or 4
    poff, plen = struct.unpack_from("<II", bz, 0x248)
    p = bz[(setup + 1) * 512 + poff:(setup + 1) * 512 + poff + plen]
    if p[:4] == b"\x02\x21\x4c\x18":
        return lz4_legacy(p)
    if p[:2] == b"\x1f\x8b":
        return zlib.decompressobj(31).decompress(p)
    raise ValueError(f"vmlinuz payload format {p[:4].hex()} not supported")


def der(b, o):
    """(tag, content_start, end) of the DER element at b[o]."""
    if o + 2 > len(b):
        raise ValueError("DER: truncated")
    tag, n = b[o], b[o + 1]
    o += 2
    if n & 0x80:
        k = n & 0x7F
        if k == 0 or k > 4 or o + k > len(b):
            raise ValueError("DER: bad length")
        n = int.from_bytes(b[o:o + k], "big")
        o += k
    if o + n > len(b):
        raise ValueError("DER: element past the end")
    return tag, o, o + n


def der_kids(b, start, end):
    out, o = [], start
    while o < end:
        t, c, e = der(b, o)
        out.append((t, o, c, e))
        o = e
    if o != end:
        raise ValueError("DER: children overrun")
    return out


def x509_ids(b, o):
    """(issuer DER, serial, subjectKeyIdentifier or None, end) of the X.509 certificate at b[o]."""
    t, c, e = der(b, o)
    if t != 0x30:
        raise ValueError("not a certificate")
    tbs = der_kids(b, c, e)[0]
    f = der_kids(b, tbs[2], tbs[3])
    i = 1 if f[0][0] == 0xA0 else 0
    if f[i][0] != 0x02 or f[i + 2][0] != 0x30:
        raise ValueError("certificate: no serial/issuer")
    serial = int.from_bytes(b[f[i][2]:f[i][3]], "big", signed=True)
    issuer = bytes(b[f[i + 2][1]:f[i + 2][3]])
    ski = None
    for x in f[i + 6:]:
        if x[0] == 0xA3:
            seq = der_kids(b, x[2], x[3])[0]
            for ext in der_kids(b, seq[2], seq[3]):
                parts = der_kids(b, ext[2], ext[3])
                if bytes(b[parts[0][2]:parts[0][3]]) == b"\x55\x1d\x0e":
                    _, ic, ie = der(b, parts[-1][2])
                    ski = bytes(b[ic:ie])
    return issuer, serial, ski, e


_CERTS = {}
CERT_RE = re.compile(rb"\x30\x82..\x30\x82..\xa0\x03\x02\x01\x02\x02", re.S)


def kernel_certs(bz):
    """{("is", issuer, serial), ("ski", keyid)} of every X.509 certificate in the decompressed kernel (memoized)."""
    k = sha256(bz)
    if k not in _CERTS:
        vm = kernel_payload(bz)
        ids = set()
        for m in CERT_RE.finditer(vm):
            try:
                issuer, serial, ski, _ = x509_ids(vm, m.start())
            except (ValueError, IndexError):
                continue
            ids.add(("is", issuer, serial))
            if ski:
                ids.add(("ski", ski))
        _CERTS[k] = ids
    return _CERTS[k]


def module_signer(ko):
    """("is", issuer DER, serial) or ("ski", keyid) of the first SignerInfo of an appended module signature."""
    if not ko.endswith(SIG_TRAILER):
        raise ValueError("no module signature")
    end = len(ko) - len(SIG_TRAILER) - 12
    ms = ko[end:end + 12]
    if ms[2] != 2:
        raise ValueError(f"signature id_type {ms[2]} is not PKCS#7")
    sig_len = int.from_bytes(ms[8:12], "big")
    sig = ko[end - sig_len:end]
    t, c, e = der(sig, 0)
    ci = der_kids(sig, c, e)
    if bytes(sig[ci[0][2]:ci[0][3]]) != bytes.fromhex("2a864886f70d010702"):
        raise ValueError("signature is not PKCS#7 signedData")
    _, c2, e2 = der(sig, ci[1][2])
    sd = der_kids(sig, c2, e2)
    sis = der_kids(sig, sd[-1][2], sd[-1][3])
    f = der_kids(sig, sis[0][2], sis[0][3])
    sid = f[1]
    if sid[0] == 0x30:
        isn = der_kids(sig, sid[2], sid[3])
        return ("is", bytes(sig[isn[0][1]:isn[0][3]]), int.from_bytes(sig[isn[1][2]:isn[1][3]], "big", signed=True))
    if sid[0] == 0x80:
        return ("ski", bytes(sig[sid[2]:sid[3]]))
    raise ValueError(f"SignerInfo sid tag {sid[0]:#x}")


def vermagic(ko):
    i = ko.find(b"vermagic=")
    if i < 0:
        return None
    j = ko.find(b"\0", i)
    return ko[i + 9:j if j > 0 else i + 300].decode("ascii", "replace")


# ------------------------------------------------------------------------------------------------------------ helpers

def parse_entry(text):
    """systemd-boot entry -> dict(lines, title, options, tokens) (None if text is None)."""
    if text is None:
        return None
    lines = text.splitlines()
    d = {"lines": lines, "title": None, "options": None, "tokens": []}
    for ln in lines:
        w = ln.split(None, 1)
        if w and w[0] in ("title", "options"):
            d[w[0]] = w[1] if len(w) > 1 else ""
    d["tokens"] = (d["options"] or "").split()
    return d


def rc_sections(text):
    """[[header, line, ...]] of an init rc text (comments and blank lines dropped, lines stripped)."""
    secs, cur = [], None
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if not ln[0].isspace() and s.split()[0] in ("service", "on", "import"):
            cur = [s]
            secs.append(cur)
        elif cur is not None:
            cur.append(s)
    return secs


def mode_of(e):
    m = int(str(e["mode"]).replace("0o", ""), 8)
    return m if m > 0o7777 else 0o100000 | m


def _xv(o):
    """Repository: expand ${GB_HOME} / ${GB_BUILD} in every string of a component manifest."""
    if isinstance(o, str):
        return os.path.expandvars(o)
    if isinstance(o, list):
        return [_xv(x) for x in o]
    if isinstance(o, dict):
        return {k: _xv(v) for k, v in o.items()}
    return o


def manifests(no_gfxdriver):
    out = []
    for p in sorted(glob.glob(os.path.join(HOST["components"], "*.json"))):
        with open(p, "rb") as fh:
            m = _xv(json.loads(fh.read().decode("utf-8")))
        if no_gfxdriver and m.get("name") == "gfxdriver":
            continue
        out.append((os.path.basename(p), m))
    return out


def rd_host(path):
    with open(path, "rb") as fh:
        return fh.read()


def first_line(path):
    return rd_host(path).decode("utf-8", "replace").splitlines()[0].strip()


class Ctx:
    """Everything the ESP checks look at, parsed once. files: {ESP path: bytes}; info: fat_parse info (None in unit
    tests that build files directly); cpio / listing: parsed from files unless given."""

    def __init__(self, files, info=None, kind="auto", super_guid=None, boot=None, part_size=None, krel_file=None,
                 no_gfxdriver=False, compare=None, cpio=None, listing=None):
        self.files, self.info, self.boot, self.part_size = files, info, boot, part_size
        self.super_guid = super_guid.lower() if super_guid else None
        self.krel_file, self.no_gfxdriver, self.compare = krel_file, no_gfxdriver, compare
        self.notes = []                                 # (check id, text): informational lines for the report
        lc = files.get("loader/loader.conf", b"").decode("utf-8", "replace")
        m = re.search(r"^default gb-([a-z])\s*$", lc, re.M)
        self.slot = m.group(1) if m else None
        self.entries = {k[len("loader/entries/"):]: v.decode("utf-8", "replace")
                        for k, v in files.items() if k.startswith("loader/entries/")}
        self.base = parse_entry(self.entries.get(f"gb-{self.slot}.conf"))
        self.cpio_error = None
        if cpio is None:
            try:
                cpio = cpio_entries(gunzip_padded(files["gb/inject.gz"]))
            except KeyError:
                cpio, self.cpio_error = [], "no gb/inject.gz"
            except (ValueError, zlib.error) as e:
                cpio, self.cpio_error = [], str(e)
        self.cpio = cpio
        self.tree = {}
        for p, md, d in cpio:
            self.tree.setdefault(p, (md, d))
        if kind == "auto":
            hw = [t[6:] for t in (self.base["tokens"] if self.base else []) if t.startswith("gb.hw=")]
            kind = hw[-1] if hw and hw[-1] in ("native", "vm") else (
                "native" if any(p.startswith("gbnative/") for p in self.tree) else "vm")
        self.kind = kind
        try:
            self.krel = kernel_release(files["vmlinuz"])
        except (KeyError, ValueError):
            self.krel = None
        self.listing_error = None
        if listing is None:
            er = [k for k in files if k.startswith("gb/dgpu-") and k.endswith(".erofs")]
            if len(er) == 1:
                try:
                    listing = erofs_listing(files[er[0]])
                except (ValueError, struct.error) as e:
                    self.listing_error = str(e)
        self.listing = listing

    def data(self, path):
        e = self.tree.get(path)
        return e[1] if e else None

    def rc(self, tree="gbnative"):
        d = self.data(f"{tree}/etc/init/gb.rc")
        return d.decode("utf-8", "replace") if d is not None else None


def entry_variant(text, title_sfx, opt_sfx):
    out = []
    for ln in text.splitlines(True):
        body = ln.rstrip("\r\n")
        eol = ln[len(body):]
        w = body.split(None, 1)
        if w and w[0] == "title":
            body += title_sfx
        elif w and w[0] == "options":
            body += opt_sfx
        out.append(body + eol)
    return "".join(out)


def compare_export(a, b):
    """Differences between two native ESP file sets that must come from the same tree (V17, build_iso check 8):
    the same file set; vmlinuz, gb/inject.gz, gb/bootconfig_extra, initrd-<slot>.img, gb/slot_<slot>.stamp and every
    gb/dgpu-* byte-identical; the entries' lines equal except the title text and the options tokens gb.boot= and
    androidboot.boot_part_uuid=. Returns a list of strings (empty = same tree)."""
    out = []
    sa, sb = set(a), set(b)
    if sa != sb:
        out.append(f"file sets differ (only in the first: {sorted(sa - sb)[:5]}, only in the second: "
                   f"{sorted(sb - sa)[:5]})")
    same = ["vmlinuz", "gb/inject.gz", "gb/bootconfig_extra"]
    same += [k for k in sorted(sa | sb) if re.fullmatch(r"initrd-[a-z]\.img|gb/slot_[a-z]\.stamp|gb/dgpu-.*", k)]
    for k in same:
        if k in a and k in b and a[k] != b[k]:
            out.append(f"{k} differs")
        elif (k in a) != (k in b):
            pass                                        # reported with the file sets
        elif k not in a:
            out.append(f"{k} missing on both")
    drop = ("gb.boot=", "androidboot.boot_part_uuid=")
    for k in sorted(sa & sb):
        if not k.startswith("loader/entries/"):
            continue
        ea, eb = parse_entry(a[k].decode("utf-8", "replace")), parse_entry(b[k].decode("utf-8", "replace"))
        la = [ln for ln in ea["lines"] if ln.split()[:1] not in (["title"], ["options"])]
        lb = [ln for ln in eb["lines"] if ln.split()[:1] not in (["title"], ["options"])]
        ta = [t for t in ea["tokens"] if not t.startswith(drop)]
        tb = [t for t in eb["tokens"] if not t.startswith(drop)]
        if la != lb or ta != tb or (ea["title"] is None) != (eb["title"] is None):
            extra = sorted(set(ta) ^ set(tb))[:4]
            out.append(f"{k}: lines or options differ beyond title/gb.boot=/boot_part_uuid= ({extra or 'line set'})")
    return out


# ------------------------------------------------------------------------------------------------------------ checks

def v1(c):
    if c.info is None:
        return ["the ESP was not read as a FAT32 image"]
    if c.info.get("error"):
        return [f"the ESP does not parse as FAT32: {c.info['error']}"]
    p = []
    size = c.info["bytes"]
    if size != ESP_SIZE:
        p.append(f"ESP size {size:,} B, expected {ESP_SIZE:,} B")
    if c.part_size is not None and c.part_size != size:
        p.append(f"the FAT covers {size:,} B but the partition/file is {c.part_size:,} B")
    if c.kind == "native":
        ini = [len(v) for k, v in c.files.items() if re.fullmatch(r"initrd-[a-z]\.img", k)]
        need = (max(ini) if ini else 0) + 144 * MiB
        if c.info["free"] < need:
            p.append(f"ESP free {c.info['free'] / MiB:.1f} MiB < max(initrd) + 144 MiB = {need / MiB:.1f} MiB")
    return p


def v2(c):
    p = []
    lc = c.files.get("loader/loader.conf")
    if lc is None:
        return ["no loader/loader.conf"]
    if not c.slot:
        return ["loader.conf has no 'default gb-<slot>' line"]
    if "editor yes" not in [ln.strip() for ln in lc.decode("utf-8", "replace").splitlines()]:
        p.append("loader.conf lacks 'editor yes'")
    want = {f"gb-{c.slot}.conf"} | ({f"gb-{c.slot}-nvtest.conf"} if c.kind == "native" else set())
    if set(c.entries) != want:
        p.append(f"entries {sorted(c.entries)}, expected {sorted(want)}")
    if any("gfxtest" in n for n in c.entries):
        p.append("a -gfxtest entry exists (retired, INTERFACES 16)")
    if c.base is None:
        p.append(f"no default entry gb-{c.slot}.conf")
        return p
    if any(t.startswith("gb.nouveau=") for t in c.base["tokens"]):
        p.append("the default entry has gb.nouveau= (the default never selects NVIDIA)")
    for n, t in sorted(c.entries.items()):
        if "gb.gfxdriver=" in t:
            p.append(f"{n} has gb.gfxdriver= (retired)")
    nv = c.entries.get(f"gb-{c.slot}-nvtest.conf")
    if c.kind == "native" and nv is not None:
        if nv != entry_variant(c.entries[f"gb-{c.slot}.conf"], " [NVIDIA test]", " gb.nouveau=test"):
            p.append("the nvtest entry is not the default entry + title ' [NVIDIA test]' + options ' gb.nouveau=test'")
    uu = [(n, u) for n, t in sorted(c.entries.items()) for u in re.findall(r"androidboot\.boot_part_uuid=(\S*)", t)]
    if c.super_guid:
        for n, u in uu:
            if u.lower() != c.super_guid:
                p.append(f"{n}: boot_part_uuid={u} does not name the super partition {c.super_guid}")
    elif len({u.lower() for _, u in uu}) > 1:
        p.append(f"the entries name different boot_part_uuid values {sorted({u for _, u in uu})}")
    if c.boot and c.kind == "native" and f"gb.boot={c.boot}" not in c.base["tokens"]:
        p.append(f"the default entry lacks gb.boot={c.boot}")         # VM entries carry no gb.boot= at all
    return p


def v3(c):
    if c.cpio_error:
        return [f"gb/inject.gz or its cpio: {c.cpio_error}"]
    p = []
    gz, ini = c.files.get("gb/inject.gz"), c.files.get(f"initrd-{c.slot}.img")
    if ini is None:
        p.append(f"no initrd-{c.slot}.img")
    elif gz is None or ini[:len(gz)] != gz:
        p.append(f"gb/inject.gz is not the first segment of initrd-{c.slot}.img")
    seen, dups = set(), []
    for path, _, _ in c.cpio:
        if path in seen:
            dups.append(path)
        seen.add(path)
    if dups:
        p.append(f"{len(dups)} duplicate cpio path(s), e.g. {', '.join(dups[:4])}")
    return p


def _module_problems(name, data, krel, certs):
    out = []
    vm = vermagic(data)
    if not vm or vm.split()[0] != krel:
        out.append(f"{name}: vermagic {(vm or 'none').split(' ')[0]!r} != krel")
    if not data.endswith(SIG_TRAILER):
        out.append(f"{name}: no module signature")
    elif certs is not None:
        try:
            if module_signer(data) not in certs:
                out.append(f"{name}: signer is not a certificate built into vmlinuz")
        except (ValueError, IndexError) as e:
            out.append(f"{name}: unreadable signature ({e})")
    return out


def lapis_module(name, data):
    """True if data is a byte-identical copy of the lapis vendor module of that file name (HOST lapis_mods)."""
    try:
        return rd_host(os.path.join(HOST["lapis_mods"], name.rsplit("/", 1)[-1])) == data
    except OSError:
        return False


def v4(c):
    if c.krel is None:
        return ["vmlinuz missing or not a bzImage"]
    p = []
    if c.krel_file:
        try:
            want = first_line(c.krel_file)
            if want != c.krel:
                p.append(f"krel {c.krel} != {want} ({c.krel_file})")
        except (OSError, IndexError) as e:
            p.append(f"--krel {c.krel_file}: {e}")
    try:
        certs = kernel_certs(c.files["vmlinuz"])
    except (ValueError, IndexError, zlib.error) as e:
        p.append(f"cannot read the certificates built into vmlinuz: {e}")
        certs = None
    if certs is not None and not certs:
        p.append("no X.509 certificate found in the decompressed vmlinuz")
        certs = None
    mods, bad = [(path, data) for path, mode, data in c.cpio if path.endswith(".ko") and is_reg(mode)], []
    for t, _, _, _, size, path in c.listing or []:
        if t == "f" and path.startswith("lib/modules/") and path.endswith(".ko"):
            hp = os.path.join(HOST["modules"], path.rsplit("/", 1)[-1])
            try:
                d = rd_host(hp)
            except OSError:
                bad.append(f"payload {path}: no staged copy {hp}")
                continue
            if len(d) != size:
                bad.append(f"payload {path}: size {size} != the staged module's {len(d)}")
            mods.append(("payload " + path, d))
    lapis, tails = [], set()
    for name, data in mods:
        if lapis_module(name, data):
            lapis.append((name, data))
            continue
        bad += _module_problems(name, data, c.krel, certs)
        vm = vermagic(data)
        if vm:
            tails.add(vm.split(" ", 1)[1] if " " in vm else "")
    for name, data in lapis:
        vm = vermagic(data) or ""
        tail = vm.split(" ", 1)[1] if " " in vm else ""
        if "modversions" not in tail or (tails and tail not in tails):
            bad.append(f"{name}: lapis module vermagic {vm!r} does not match this kernel's modversions vermagic")
    if bad:
        p.append(f"{len(bad)} module problem(s): " + "; ".join(bad[:5]) + (" ..." if len(bad) > 5 else ""))
    c.notes.append(("V4", f"krel {c.krel}; {len(mods) - len(lapis)} kernel modules checked (vermagic, signature, "
                          f"signer among {len(certs or ())} certificate ids in vmlinuz); {len(lapis)} lapis vendor "
                          f"module copies checked by their modversions vermagic only"))
    return p


def _optin(name):
    n = name.rsplit("/", 1)[-1]
    n = n[:-3] if n.endswith(".ko") else n
    return n.replace("-", "_") in OPTIN


def v5(c):
    p = []
    for path, mode, data in c.cpio:
        if not path.startswith("gbnative/"):
            continue
        if re.search(r"/modules\.load(\.[^/]*)?$", path) and data:
            for ln in data.decode("utf-8", "replace").splitlines():
                w = ln.split()
                if w and not w[0].startswith("#") and _optin(w[0]):
                    p.append(f"{path} names {w[0]} (opt-in modules ship only in the payload)")
        if is_reg(mode) and path.endswith(".ko") and _optin(path):
            p.append(f"{path} is in the native cpio (it belongs in the dGPU payload)")
    return p


def v6(c):
    bad = [p for p, _, _ in c.cpio if BAD_NAME.search(p)]
    return [f"{len(bad)} backup/superseded file(s) in the cpio, e.g. {', '.join(bad[:3])}"] if bad else []


def payload_expected(krel):
    """{path: size or None} of the regular files INTERFACES 6.2 requires (sizes from the host inputs)."""
    def size(p):
        try:
            return os.path.getsize(p)
        except OSError:
            return None
    want = {"VERSION": len(f"gb-dgpu-payload 1\nkrel {krel}\n"),
            "firmware/nvidia/LICENCE.nvidia": size(os.path.join(HOST["nk_fw"], "LICENCE.nvidia")),
            "firmware/nvidia/WHENCE.tu117": size(os.path.join(HOST["nk_fw"], "WHENCE.tu117")),
            "lib/modules/modules.dgpu": len(MODULES_DGPU)}
    for ln in rd_host(HOST["fw_sha"]).decode("utf-8").splitlines():
        w = ln.split()
        if len(w) == 2:
            rel = w[1].lstrip("*")
            want["firmware/" + rel] = size(os.path.join(HOST["firmware"], *rel.split("/")))
    for m in OPTIN_FILES:
        want["lib/modules/" + m] = size(os.path.join(HOST["modules"], m))
    for _, man in manifests(False):
        for e in man.get("payload", []):
            want[e["dest"]] = size(e["src"])
    want["MANIFEST.sha256"] = sum(64 + 2 + len(k) + 1 for k in want)
    return want


def v7(c):
    dg = sorted(k for k in c.files if k.startswith("gb/dgpu-"))
    if c.kind != "native":
        return [f"a VM ESP carries no dGPU payload ({dg})"] if dg else []
    if c.krel is None:
        return ["no krel (vmlinuz)"]
    p = []
    ename, sname = f"gb/dgpu-{c.krel}.erofs", f"gb/dgpu-{c.krel}.sha256"
    if dg != sorted([ename, sname]):
        p.append(f"ESP gb/dgpu-* files {dg}, expected exactly {ename} and {sname}")
    img, line = c.files.get(ename), c.files.get(sname)
    if img is None or line is None:
        return p
    if line != f"{sha256(img)}  dgpu-{c.krel}.erofs\n".encode():
        p.append(f"{sname} does not hold the payload's sha256 in the 6.1 format")
    if c.data("gbnative/etc/gb/dgpu_payload.sha256") != line:
        p.append("gbnative/etc/gb/dgpu_payload.sha256 is not byte-identical to the ESP .sha256")
    if img[1024:1028] != b"\xe2\xe1\xf5\xe0":
        p.append("payload: no EROFS magic at offset 1024")
    if c.listing_error or c.listing is None:
        p.append(f"payload listing: {c.listing_error or 'not readable'}")
        return p
    try:
        want = payload_expected(c.krel)
    except (OSError, ValueError, KeyError) as e:
        p.append(f"payload: host inputs unreadable ({e})")
        return p
    dirs = {"."}
    for k in want:
        parts = k.split("/")[:-1]
        for i in range(len(parts)):
            dirs.add("/".join(parts[:i + 1]))
    have_f = {x[5]: x for x in c.listing if x[0] == "f"}
    have_d = {x[5]: x for x in c.listing if x[0] == "d"}
    other = [x[5] for x in c.listing if x[0] not in ("f", "d")]
    if set(have_f) != set(want):
        p.append(f"payload files differ from INTERFACES 6.2: missing {sorted(set(want) - set(have_f))[:4]}, "
                 f"unexpected {sorted(set(have_f) - set(want))[:4]}")
    if set(have_d) != dirs:
        p.append(f"payload directories differ: {sorted(set(have_d) ^ dirs)[:4]}")
    if other:
        p.append(f"payload has non-regular entries (symlinks etc.): {other[:4]}")
    badmode = [x[5] for x in c.listing if (x[0] == "f" and x[1] != 0o644) or (x[0] == "d" and x[1] != 0o755)
               or x[2] or x[3]]
    if badmode:
        p.append(f"payload modes/owners not 0644/0755 root: {badmode[:4]}")
    badsize = [k for k, s in want.items() if k in have_f and s is not None and have_f[k][4] != s]
    if badsize:
        p.append(f"payload file sizes differ from the inputs: {badsize[:4]}")
    return p


def v8(c):
    rc = c.rc()
    if rc is None:
        return ["no gbnative/etc/init/gb.rc"]
    p = []
    secs = rc_sections(rc)
    keyed = [tuple(s) for s in secs]
    for spec in rc_sections(NOUVEAU_RC_V12):
        if tuple(spec) not in keyed:
            p.append(f"15.1-v12 section missing or changed: '{spec[0]}'")

    def sec(header_prefix):
        return [s for s in secs if s[0] == header_prefix or s[0].startswith(header_prefix + " ")]
    pw = sec("service gb_power")
    if not pw or "socket gb_power stream 0666 root root" not in pw[0]:
        p.append("service gb_power lacks 'socket gb_power stream 0666 root root'")
    if not any("mkdir /dev/gb_dgpu 0700 root root" in s for s in secs):
        p.append("no 'mkdir /dev/gb_dgpu 0700 root root'")
    nvk = sec("service gb_nvk_probe")
    if not nvk or "user shell" not in nvk[0]:
        p.append("service gb_nvk_probe missing or not 'user shell'")
    trig = [s for s in secs if s[0] == "on property:vendor.gb.nvk_probe=1"]
    if not trig or "setprop vendor.gb.nouveau.foreign 1" not in trig[0]:
        p.append("'on property:vendor.gb.nvk_probe=1' lacks 'setprop vendor.gb.nouveau.foreign 1'")
    gc = [s for s in secs if s[0] == "service gb_gpu_compute /vendor/bin/gb_gpu_compute"]
    if not gc or len(gc[0]) < 2 or gc[0][1] != "user nobody" or \
            "socket gb_gpu_compute stream 0666 nobody graphics" not in gc[0]:
        p.append("service gb_gpu_compute missing, not 'user nobody' next, or without its 0666 socket")
    if not [s for s in secs if s[0] == "service gb_gpu_ui_setup /system/bin/sh /vendor/bin/gb_gpu_ui_setup.sh"]:
        p.append("no 'service gb_gpu_ui_setup /system/bin/sh /vendor/bin/gb_gpu_ui_setup.sh'")
    if not any("mkdir /data/vendor/gb/ui 0700 shell shell" in s for s in secs):
        p.append("no 'mkdir /data/vendor/gb/ui 0700 shell shell'")
    if not sec("service gb_slotsync"):
        p.append("no service gb_slotsync")
    lines = [ln for s in secs for ln in s[1:]]
    g = [ln for ln in lines if ln.startswith("mkdir /data/vendor/gb ") or ln == "mkdir /data/vendor/gb"]
    r = [ln for ln in lines if ln.startswith("mkdir /data/vendor/gb/report ") or ln == "mkdir /data/vendor/gb/report"]
    if not g or any(ln != "mkdir /data/vendor/gb 0751 root shell" for ln in g):
        p.append(f"'mkdir /data/vendor/gb' must always read '0751 root shell' ({sorted(set(g))})")
    if not r or any(ln != "mkdir /data/vendor/gb/report 0750 root shell" for ln in r):
        p.append(f"'mkdir /data/vendor/gb/report' must always read '0750 root shell' ({sorted(set(r))})")
    if any("property:persist.vendor.gb.nouveau=" in s[0] for s in secs):
        p.append("a property:persist.vendor.gb.nouveau= trigger (retired v1.1)")
    for bad in ("gb_gfxdriver", "vendor.gb.gfxdriver", "debug.hwui.renderer"):
        if bad in rc:
            p.append(f"gb.rc mentions {bad}")
    return p


def v8b(c):
    rc = c.rc()
    if rc is None:
        return ["no gbnative/etc/init/gb.rc"]
    miss = []
    for s in rc_sections(rc):
        if not s[0].startswith("service "):
            continue
        for m in re.finditer(r"/odm/([^\s\"';|&<>$]+)", s[0]):
            if not is_reg(c.tree.get("gbnative/" + m.group(1), (0, None))[0]):
                miss.append(f"/odm/{m.group(1)}")
        for m in re.finditer(r"/vendor/bin/([^\s\"';|&<>$]+)", s[0]):
            if m.group(1).rsplit("/", 1)[-1].startswith(("gb_", "nvk_", "iptsd")) and \
                    not is_reg(c.tree.get("gbnative/vup/bin/" + m.group(1), (0, None))[0]):
                miss.append(f"/vendor/bin/{m.group(1)}")
    return [f"service binaries not packed: {sorted(set(miss))}"] if miss else []


def v9(c):
    p = []
    if c.kind == "native":
        prop = c.data("gbnative/etc/build.prop")
        if prop is None:
            return ["no gbnative/etc/build.prop"]
        lines = [ln.strip() for ln in prop.decode("utf-8", "replace").splitlines()]
        gl = [ln for ln in lines if ln.startswith("ro.gfx.driver.")]
        want = [] if c.no_gfxdriver else ["ro.gfx.driver.0=gb.sb3.gfxdriver"]
        if gl != want:
            p.append(f"native build.prop ro.gfx.driver lines {gl}, expected {want}")
        bad = [ln for ln in lines if ln.startswith(BAD_PROP)]
        if bad:
            p.append(f"native build.prop has forbidden lines {bad}")
    else:
        prop = c.data("gbstage/etc/build.prop")
        if prop is None:
            return ["no gbstage/etc/build.prop"]
        lines = [ln.strip() for ln in prop.decode("utf-8", "replace").splitlines()]
        gl = [ln for ln in lines if ln.startswith("ro.gfx.driver.")]
        want = ["ro.gfx.driver.0=gb.sb3.gfxdriver"] if c.kind == "vmtest-gfx" else []
        if gl != want:                                  # (the VM's own Mesa/svga props are not the native rules)
            p.append(f"VM build.prop ro.gfx.driver lines {gl}, expected {want}")
    return p


def v10(c):
    bad = [p for p, _, _ in c.cpio if p.startswith(("gbnative/priv-app", "gbstage/priv-app"))]
    return [f"priv-app entries {bad[:3]}"] if bad else []


def v11(c):
    p = []
    if c.kind == "native":
        st = [x for x in c.tree if x.startswith("gbstage")]
        if st:
            p.append(f"the native cpio carries VM (gbstage) entries, e.g. {st[:3]}")
        if not any(x.startswith("gbnative/") for x in c.tree):
            p.append("no gbnative tree")
        return p
    nat = [x for x in c.tree if x.startswith("gbnative")]
    if nat:
        p.append(f"the VM cpio carries native entries, e.g. {nat[:3]}")
    hits = sorted(x for x in c.tree if x.startswith("gbstage/") and any(s in x for s in VM_FORBIDDEN))
    rc = c.rc("gbstage")
    if rc is None:
        return p + ["no gbstage/etc/init/gb.rc"]
    if c.kind == "vm":
        if hits:
            p.append(f"dGPU stack in the VM tree: {hits[:4]}")
        for s in VM_RC_FORBIDDEN:
            if s in rc:
                p.append(f"VM gb.rc mentions {s}")
        return p
    allowed = set(VMTEST_GFX) | {"gbstage/app/GbGpu", "gbstage/app/GbGfxDriver"}
    if set(hits) - allowed:
        p.append(f"dGPU stack in the VM test gfx tree beyond the test additions: {sorted(set(hits) - allowed)[:4]}")
    src = {}
    for _, man in manifests(False):
        if man.get("name") in ("gpu_ui", "gfxdriver"):
            for e in man.get("files", []):
                src["gbstage/" + e["dest"]] = (mode_of(e), e["src"])
    for path, md in VMTEST_GFX.items():
        e = c.tree.get(path)
        if e is None or e[0] != md:
            p.append(f"{path} missing or not mode {md:o}")
        elif path in src:
            try:
                if rd_host(src[path][1]) != e[1]:
                    p.append(f"{path} differs from its manifest source")
            except OSError as ex:
                p.append(f"{path}: source unreadable ({ex})")
    try:
        ui = [m for _, m in manifests(False) if m.get("name") == "gpu_ui"][0]
        frag = "".join(VMTEST_RC_MARK + rd_host(r).decode("utf-8", "replace") for r in ui.get("rc_files", []))
        if rc.count(VMTEST_RC_MARK) != 1 or not rc.endswith(frag):
            p.append("the VM test gb.rc does not end with exactly one gpu_ui (VM test) fragment")
    except (IndexError, OSError) as ex:
        p.append(f"gpu_ui manifest/rc unreadable ({ex})")
    for s in VM_RC_FORBIDDEN:
        if s in rc:
            p.append(f"VM test gb.rc mentions {s}")
    return p


def v12(c):
    p = []
    rc = c.rc() or ""
    prop = (c.data("gbnative/etc/build.prop") or b"").decode("utf-8", "replace").splitlines()
    try:
        mans = manifests(c.no_gfxdriver)
    except (OSError, ValueError) as e:
        return [f"component manifests unreadable: {e}"]
    for fn, m in mans:
        for e in m.get("files", []):
            have = c.tree.get("gbnative/" + e["dest"])
            try:
                want = rd_host(e["src"])
            except OSError:
                p.append(f"{fn}: source {e['src']} unreadable")
                continue
            if have is None:
                p.append(f"{fn}: {e['dest']} not in the cpio")
            elif have[1] != want or have[0] != mode_of(e):
                p.append(f"{fn}: {e['dest']} differs from its source (bytes or mode)")
        for e in m.get("payload", []):
            hit = [x for x in c.listing or [] if x[5] == e["dest"] and x[0] == "f"]
            try:
                sz = os.path.getsize(e["src"])
            except OSError:
                sz = None
            if not hit or hit[0][4] != sz:
                p.append(f"{fn}: payload {e['dest']} missing from the payload or of another size")
        for s in m.get("symlinks", []):
            if c.tree.get("gbnative/" + s["dest"]) != (0o120777, s["target"].encode()):
                p.append(f"{fn}: symlink {s['dest']} -> {s['target']} missing")
        for r in m.get("rc_files", []):
            try:
                if rd_host(r).decode("utf-8", "replace") not in rc:
                    p.append(f"{fn}: rc file {os.path.basename(r)} not in gb.rc")
            except OSError:
                p.append(f"{fn}: rc file {r} unreadable")
        if m.get("rc_text") and m["rc_text"] not in rc:
            p.append(f"{fn}: rc_text not in gb.rc")
        for ln in m.get("build_prop", []):
            if ln not in prop:
                p.append(f"{fn}: build.prop line {ln!r} missing")
    if len(p) > 6:
        p = p[:6] + [f"... {len(p) - 6} more"]
    return ["; ".join(p)] if p else []


def v13(c):
    p = []
    e = c.tree.get(SYMLINK_ICD[0])
    if e is None or not is_lnk(e[0]) or e[0] & 0o7777 != 0o777 or e[1] != SYMLINK_ICD[1]:
        p.append(f"{SYMLINK_ICD[0]} is not a symlink to {SYMLINK_ICD[1].decode()}")
    for d in ("gbnative/dgpu", "gbnative/firmware/nvidia"):
        e = c.tree.get(d)
        if e is None or not is_dir(e[0]):
            p.append(f"{d} is not a directory")
        kids = [x for x in c.tree if x.startswith(d + "/")]
        if kids:
            p.append(f"{d} is not empty ({kids[:2]})")
    return p


def v14(c):
    bad = [p for p, _, _ in c.cpio if p.endswith("/libvulkan.so")]
    return [f"libvulkan.so in the cpio: {bad}"] if bad else []


def _zip(data, path):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        if z.testzip() is not None:
            return None, f"{path}: bad zip member"
        return z, None
    except (zipfile.BadZipFile, ValueError, OSError) as e:
        return None, f"{path}: not a zip ({e})"


def v15(c):
    p = []
    g = c.data("gbnative/app/GbGpu/GbGpu.apk")
    if g is None:
        p.append("no gbnative/app/GbGpu/GbGpu.apk")
    else:
        z, err = _zip(g, "GbGpu.apk")
        if err:
            p.append(err)
        else:
            n = {i.filename: i for i in z.infolist()}
            if not {"AndroidManifest.xml", "classes.dex", "resources.arsc"} <= set(n):
                p.append("GbGpu.apk lacks AndroidManifest.xml/classes.dex/resources.arsc")
            elif n["resources.arsc"].compress_type != zipfile.ZIP_STORED:
                p.append("GbGpu.apk resources.arsc is not STORED")
        try:
            want = rd_host(HOST["gbgpu_sha"]).decode("ascii", "replace").split()[0].lower()
            if sha256(g) != want:
                p.append("GbGpu.apk sha256 differs from gpu-ui\\out\\GbGpu.apk.sha256")
        except (OSError, IndexError) as e:
            p.append(f"GbGpu.apk.sha256 unreadable ({e})")
    d = c.data("gbnative/app/GbGfxDriver/GbGfxDriver.apk")
    if c.no_gfxdriver:
        if d is not None:
            p.append("GbGfxDriver.apk present with --no-gfxdriver")
        return p
    if d is None:
        p.append("no gbnative/app/GbGfxDriver/GbGfxDriver.apk")
        return p
    z, err = _zip(d, "GbGfxDriver.apk")
    if err:
        return p + [err]
    n = {i.filename: i for i in z.infolist()}
    if set(n) != GFXDRIVER_MEMBERS:
        p.append(f"GbGfxDriver.apk members {sorted(n)}, expected {sorted(GFXDRIVER_MEMBERS)}")
    if any(k.rsplit("/", 1)[-1] == "allowlist.txt" for k in n):
        p.append("GbGfxDriver.apk has an allowlist.txt (24.5)")
    for k in ("lib/x86_64/libgb_nvk.so", "lib/x86_64/vulkan.intel.so"):
        if k in n and n[k].compress_type != zipfile.ZIP_STORED:
            p.append(f"GbGfxDriver.apk {k} is not STORED")
    return p


def v16(c):
    if c.base is None:
        return ["no default entry"]
    t = c.base["tokens"]
    miss = [x for x in ("gb.hw=native", "efi_pstore.pstore_disable=1", "reboot=pci", "gb.logpart=EFI-SYSTEM")
            if x not in t] + ([] if any(x.startswith("module_blacklist=") for x in t) else ["module_blacklist="])
    return [f"the default entry lacks {miss}"] if miss else []


def v17(c):
    if c.compare is None:
        return []
    d = compare_export(c.files, c.compare)
    return [f"not the same tree as the export ESP: {'; '.join(d[:4])}"] if d else []


def v18(c):
    p = []
    st = c.files.get(f"gb/slot_{c.slot}.stamp")
    if st is None or not re.fullmatch(rb"[0-9a-f]{8}\n", st):
        p.append(f"gb/slot_{c.slot}.stamp missing or not '%08x\\n'")
    if f"initrd-{c.slot}.img" not in c.files:
        p.append(f"no initrd-{c.slot}.img")
    return p


def v19(c):
    ml = c.data("gbnative/lib/modules/modules.load")
    if ml is None:
        return ["no gbnative/lib/modules/modules.load"]
    first = (ml.decode("utf-8", "replace").splitlines() or [""])[0]
    m = re.fullmatch(r"# kernel build (\S+) \(bzImage md5 ([0-9a-f]{32})\)", first)
    if not m:
        return [f"modules.load line 1 is {first[:80]!r}"]
    if m.group(2) != hashlib.md5(c.files.get("vmlinuz", b"")).hexdigest():
        return [f"modules.load names bzImage md5 {m.group(2)}, vmlinuz is {hashlib.md5(c.files.get('vmlinuz', b'')).hexdigest()}"]
    return []


def v20(c):
    path = "gbnative/etc/gb/gfx_build_id"
    e = c.tree.get(path)
    if c.no_gfxdriver:
        return [f"{path} present with --no-gfxdriver"] if e else []
    if e is None:
        return [f"no {path}"]
    if e[0] != 0o100644:
        return [f"{path} mode {e[0]:o}"]
    m = re.fullmatch(rb"apk=([0-9a-f]{64}) icd=([0-9a-f]{64}) nvk=([0-9a-f]{64}) probe=([0-9a-f]{64}) pay=([0-9a-f]{64})\n",
                     e[1])
    if not m:
        return [f"{path} is not one 'apk= icd= nvk= probe= pay=' line"]
    got = dict(zip(("apk", "icd", "nvk", "probe", "pay"), (x.decode() for x in m.groups())))
    want = {}
    apk = c.data("gbnative/app/GbGfxDriver/GbGfxDriver.apk")
    if apk is not None:
        want["apk"] = sha256(apk)
        z, err = _zip(apk, "GbGfxDriver.apk")
        if z is not None:
            try:
                i = z.getinfo("lib/x86_64/libgb_nvk.so")
                if i.compress_type == zipfile.ZIP_STORED:
                    want["nvk"] = sha256(z.read(i))
            except KeyError:
                pass
    probe = c.data("gbnative/vup/bin/nvk_probe")
    if probe is not None:
        want["probe"] = sha256(probe)
    try:
        for _, man in manifests(False):
            for pe in man.get("payload", []):
                if pe["dest"] == "lib64/hw/vulkan.nouveau.so":
                    want["icd"] = sha256(rd_host(pe["src"]))
    except (OSError, ValueError):
        pass
    sl = c.data("gbnative/etc/gb/dgpu_payload.sha256")
    if sl:
        want["pay"] = sl.split()[0].decode("ascii", "replace")
    bad = [k for k in got if want.get(k) != got[k]]
    return [f"{path}: {bad} do not match the tree/payload inputs"] if bad else []


def v21(c):
    d = c.data("gbnative/vup/bin/gb_nouveau.sh")
    if d is None:
        return ["no gbnative/vup/bin/gb_nouveau.sh"]
    return [] if b"GB_PANIC_TIMEOUT=30" in d else ["the packed gb_nouveau.sh lacks GB_PANIC_TIMEOUT=30"]


def v22(c):
    p = []
    e = c.tree.get("gbnative/etc/gb/gfx_app_prefs.default")
    if e is None or e[0] != 0o100644 or e[1] != b"":
        p.append("gbnative/etc/gb/gfx_app_prefs.default missing or not an empty 0644 file")
    t = c.data("gbnative/etc/gb/tu117.sha256")
    try:
        if t != rd_host(HOST["fw_sha"]):
            p.append("gbnative/etc/gb/tu117.sha256 differs from firmware_staged_sha256.txt")
    except OSError as ex:
        p.append(f"firmware_staged_sha256.txt unreadable ({ex})")
    return p


def v23(c):
    bad = [p for p, _, _ in c.cpio if p.rsplit("/", 1)[-1] == "gb_gfxdriver.sh"]
    bad += [k for k in c.files if k.rsplit("/", 1)[-1] == "gb_gfxdriver.sh"]
    return [f"gb_gfxdriver.sh shipped: {bad}"] if bad else []


def v24(c):
    if c.base is None:
        return ["no default entry"]
    p = []
    t = c.base["tokens"]
    if "gb.hw=vm" not in t:
        p.append("the default entry lacks gb.hw=vm")
    if not any(x.startswith("console=ttyS0") for x in t):
        p.append("the default entry lacks console=ttyS0")
    if c.kind == "vmtest-gfx" and VMTEST_TITLE not in (c.base["title"] or ""):
        p.append(f"the VM test gfx title lacks {VMTEST_TITLE!r}")
    return p


ALL, NATIVE, VMS = "all", "native", "vm"
CHECKS = [("V1", v1, ALL), ("V2", v2, ALL), ("V3", v3, ALL), ("V4", v4, ALL), ("V5", v5, NATIVE),
          ("V6", v6, ALL), ("V7", v7, ALL), ("V8", v8, NATIVE), ("V8b", v8b, NATIVE), ("V9", v9, ALL),
          ("V10", v10, ALL), ("V11", v11, ALL), ("V12", v12, NATIVE), ("V13", v13, NATIVE), ("V14", v14, ALL),
          ("V15", v15, NATIVE), ("V16", v16, NATIVE), ("V17", v17, ALL), ("V18", v18, ALL), ("V19", v19, NATIVE),
          ("V20", v20, NATIVE), ("V21", v21, NATIVE), ("V22", v22, NATIVE), ("V23", v23, ALL), ("V24", v24, VMS)]


def run_checks(c, only=None):
    """{check id: [problem, ...]} of every check that applies to c's tree kind."""
    out = {}
    for vid, fn, scope in CHECKS:
        if only and vid not in only:
            continue
        if (scope == NATIVE and c.kind != "native") or (scope == VMS and c.kind == "native"):
            continue
        try:
            out[vid] = fn(c)
        except Exception as e:                                   # one broken check must not hide the others
            out[vid] = [f"internal error {e.__class__.__name__}: {e}"]
    return out


# ------------------------------------------------------------------------------------------------------------ runner

class Report:
    def __init__(self):
        self.problems = []

    def bad(self, vid, msg):
        self.problems.append((vid, msg))
        print(f"PROBLEM: {vid} {msg}", flush=True)

    def result(self):
        print("RESULT:", "OK" if not self.problems else f"{len(self.problems)} problem(s)", flush=True)
        return 1 if self.problems else 0


def esp_report(rep, files, info, a, super_guid, part_size, label):
    compare = None
    if a.compare_export:
        try:
            compare = fat_files(rd_host(a.compare_export))
        except (OSError, ValueError, struct.error) as e:
            rep.bad("V17", f"--compare-export {a.compare_export}: {e}")
    c = Ctx(files, info=info, kind=a.expect_tree, super_guid=super_guid, boot=a.boot, part_size=part_size,
            krel_file=a.krel, no_gfxdriver=a.no_gfxdriver, compare=compare)
    print(f"{label}: tree {c.kind}, slot {c.slot}, krel {c.krel}, {len(c.cpio)} cpio entries, "
          f"{len(files)} ESP files" + (f", free {info['free'] / MiB:.1f} MiB" if info and 'free' in info else ""),
          flush=True)
    t0 = time.time()
    for vid, probs in run_checks(c).items():
        if vid == "V17" and compare is None:
            continue
        if probs:
            for msg in probs:
                rep.bad(vid, msg)
        else:
            print(f"  {vid} ok", flush=True)
        for nid, text in c.notes:
            if nid == vid:
                print(f"  {vid} note: {text}", flush=True)
    print(f"  ESP checks took {time.time() - t0:.1f} s", flush=True)


def image_mode(a, rep):
    img = a.img or DEFAULT_IMG
    size = os.path.getsize(img)
    print(f"image {img}: {size / 2**30:.2f} GiB", flush=True)
    with open(img, "rb") as f, open(LAPIS, "rb") as lap:
        probs = []
        mbr = f.read(SECTOR)
        if mbr[510:512] != b"\x55\xaa" or mbr[450] != 0xEE:
            probs.append("protective MBR missing")
        p = gpt_read(f, 1, probs)
        if not p:
            for m in probs:
                rep.bad("VG", m)
            return
        b = gpt_read(f, p["alt"], probs)
        if b and (b["raw"] != p["raw"] or b["alt"] != 1):
            probs.append("backup GPT differs from primary")
        if p["alt"] != size // SECTOR - 1:
            probs.append(f"backup header LBA {p['alt']} is not the last sector")
        parts = p["parts"]
        for n in EXPECT:
            if n not in parts:
                probs.append(f"partition {n} missing")
        spans = sorted((v["first"], v["last"], k) for k, v in parts.items())
        for (a1, b1, k1), (a2, b2, k2) in zip(spans, spans[1:]):
            if a2 <= b1:
                probs.append(f"{k1} overlaps {k2}")
        for a_, b_, k in spans:
            if a_ < p["first"] or b_ > p["last"] or a_ % 2048:
                probs.append(f"{k} outside the usable range or not 1 MiB aligned")
        for m in probs:
            rep.bad("VG", m)
        if not probs:
            print(f"  VG ok: {len(parts)} partitions, primary + backup CRCs valid", flush=True)
        if any(n not in parts for n in ("EFI-SYSTEM", "super")):
            return
        for s in "ab":
            at = parts.get("vbmeta_" + s, {}).get("attrs", 0)
            print(f"  vbmeta_{s}: priority={(at >> 48) & 15} tries={(at >> 52) & 15} successful={(at >> 56) & 1}")
        if not any(((parts.get("vbmeta_" + s, {}).get("attrs", 0) >> 56) & 1) for s in "ab"):
            rep.bad("VS", "no slot marked successful in vbmeta attributes (boot_control HAL would find no bootable slot)")
        else:
            print("  VS ok", flush=True)
        lap.seek(SECTOR)
        lh = lap.read(92)
        le, ln, les = struct.unpack_from("<QII", lh, 72)
        lap.seek(le * SECTOR)
        lraw = lap.read(ln * les)
        lp = {}
        for k in range(ln):
            e = lraw[k * les:(k + 1) * les]
            if e[:16] != bytes(16):
                a_, b_ = struct.unpack_from("<QQ", e, 32)
                lp[e[56:128].decode("utf-16-le", "replace").rstrip("\0")] = (a_, b_)
        nbad = 0
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
                f.seek(parts[n]["first"] * SECTOR + off)
                left = ln_
                while left and same:
                    k = min(left, 16 * MiB)
                    if f.read(k) != lap.read(k):
                        same = False
                    left -= k
                if not same:
                    break
            if not same:
                rep.bad("VC", f"{n} differs from lapis.bin")
                nbad += 1
        if not nbad:
            print("  VC ok: copied partitions identical to lapis.bin (super sampled)", flush=True)
        nbad = 0
        for n in BLANK:
            pp = parts.get(n)
            if pp is None:
                continue
            ln_ = (pp["last"] - pp["first"] + 1) * SECTOR
            f.seek(pp["first"] * SECTOR)
            head = f.read(1 << 20)
            f.seek(pp["first"] * SECTOR + ln_ - (1 << 20))
            tail = f.read(1 << 20)
            if head.strip(b"\0") or tail.strip(b"\0"):
                rep.bad("VB", f"blank partition {n} is not zero")
                nbad += 1
        if not nbad:
            print(f"  VB ok: blank partitions zeroed: {', '.join(BLANK)}", flush=True)
        esp = parts["EFI-SYSTEM"]
        f.seek(esp["first"] * SECTOR)
        espb = f.read((esp["last"] - esp["first"] + 1) * SECTOR)
        sup = parts["super"]["guid"].lower()
    try:
        files, info = fat_parse(espb)
    except (ValueError, struct.error) as e:
        files, info = {}, {"error": str(e)}
    part_size = len(espb)
    del espb
    print(f"  ESP: super GUID {sup}", flush=True)
    esp_report(rep, files, info, a, sup, part_size, "ESP")


def esp_mode(a, rep):
    espb = rd_host(a.esp)
    print(f"ESP image {a.esp}: {len(espb):,} B", flush=True)
    try:
        files, info = fat_parse(espb)
    except (ValueError, struct.error) as e:
        files, info = {}, {"error": str(e)}
    esp_report(rep, files, info, a, a.super_guid, len(espb), "ESP")


def extract_mode(img, esp_path, outdir):
    with open(img, "rb") as f:
        head = f.read(1024)
    espb = esp_of_disk(img) if head[512:520] == b"EFI PART" else rd_host(img)
    files = fat_files(espb)
    want = esp_path.strip("/").replace("\\", "/")
    sel = {k: v for k, v in files.items() if k == want or (want and k.startswith(want + "/")) or not want}
    if not sel:
        print(f"extract: {esp_path} not found on the ESP")
        return 1
    if os.path.exists(outdir) and (not os.path.isdir(outdir) or os.listdir(outdir)):
        print(f"extract: {outdir} must be a new or empty directory")
        return 2
    for k, v in sorted(sel.items()):
        rel = k[len(want) + 1:] if want and k != want else (k.rsplit("/", 1)[-1] if want else k)
        parts = rel.split("/")
        if any(x in ("", ".", "..") or re.search(r'[<>:"|?*\x00-\x1f]', x) for x in parts):
            print(f"extract: refusing the name {k!r}")
            return 1
        dst = os.path.join(outdir, *parts)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "xb") as fh:
            fh.write(v)
        print(f"  {len(v):>12,} B  {k}")
    print(f"extract: {len(sel)} file(s) written to {outdir}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("img", nargs="?", help=f"disk image (default {DEFAULT_IMG})")
    ap.add_argument("--esp", help="verify an ESP image file instead of a disk image")
    ap.add_argument("--boot", choices=["usb", "internal"], help="--esp: the entry's gb.boot= value")
    ap.add_argument("--super-guid", help="--esp: the super GUID every boot_part_uuid must name")
    ap.add_argument("--expect-tree", choices=["auto", "native", "vm", "vmtest-gfx"], default="auto")
    ap.add_argument("--krel", help="file whose first line is the expected kernel release")
    ap.add_argument("--no-gfxdriver", action="store_true", help="the image was built with GB_GFXDRIVER=0")
    ap.add_argument("--compare-export", metavar="ESP", help="an export EFI-SYSTEM.img that must hold the same tree")
    ap.add_argument("--extract", nargs=3, metavar=("IMG", "ESP_PATH", "OUTDIR"))
    a = ap.parse_args(argv)
    if a.extract:
        if a.img or a.esp:
            ap.error("--extract takes no other image argument")
        return extract_mode(*a.extract)
    if a.esp and a.img:
        ap.error("give either IMG or --esp FILE")
    if not a.esp and (a.boot or a.super_guid):
        ap.error("--boot and --super-guid are only used with --esp")
    if a.super_guid:
        try:
            uuid.UUID(a.super_guid)
        except ValueError:
            ap.error(f"--super-guid {a.super_guid!r} is not a GUID")
    rep = Report()
    t0 = time.time()
    try:
        if a.esp:
            esp_mode(a, rep)
        else:
            image_mode(a, rep)
    except OSError as e:
        rep.bad("V0", f"cannot read: {e}")
    print(f"verify_image: {time.time() - t0:.0f} s", flush=True)
    return rep.result()


if __name__ == "__main__":
    sys.exit(main())
