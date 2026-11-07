"""check_stick_ota.py -- READ-ONLY check of a Googlebook stick (or disk image) after a Google system update (OTA).

w11prep ota-hw3 (third hardware test, 2026-11-06). A Google A/B update (update_engine, Virtual A/B) writes the
inactive slot; the android-desktop boot_control HAL then marks that slot active in the GPT attributes of
vbmeta_a / vbmeta_b, and gb_slotsync (in Android, every 60 s) rebuilds initrd-<x>.img, writes
loader/entries/gb-<x>.conf and makes it the systemd-boot default. Run this before you start the stick again: it says
which slot the next start runs, which Google build that slot holds, and whether that is the build the stick's image
was made for (the OTA gate's /odm/etc/gb/ota_validated, read from the ESP's gb/inject.gz -- the same comparison
gbinit makes at every start). It only reads:

  * the GPT (partition names and the vbmeta_a/b attributes: bits 48-51 priority, 52-55 tries left, 56 successful),
  * the EFI-SYSTEM file system's directory entries, loader/loader.conf, the title/initrd lines of
    loader/entries/gb-*.conf and gb/inject.gz (our own cpio; only its ota_validated file is kept),
  * the init_boot_a / init_boot_b partitions (Google's boot ramdisk; only ro.bootimage.build.fingerprint is kept).

It never reads gblog\\ (your private boot logs) or /data. It opens the disk with "rb" (GENERIC_READ) and never
writes, never mounts, never assigns a drive letter. Windows needs an administrator prompt to read a whole disk.

Usage (administrator PowerShell; N = the SanDisk stick's number in `Get-Disk`, NOT the internal NVMe):
    python -I check_stick_ota.py --disk N
    python -I check_stick_ota.py --image D:\\some\\disk.img        (a raw disk image file; tests)
    add --json for a machine-readable result.

Exit codes: 0 = the next start runs the build the image was made for (or, if a build cannot be read, the slot state
                shows no switch);
            1 = the next start runs ANOTHER build (an update was followed or has already started);
            2 = refused (not a Googlebook disk, not FAT32, bad arguments); 3 = read error;
            4 = an unexpected state (see the text; do not start the stick, send the output to the lead).
Python 3.8+, standard library only.
"""
import argparse
import json
import os
import re
import struct
import sys
import zlib

SECTORS = (512, 4096)
NEED = ("EFI-SYSTEM", "super", "boot_a", "vbmeta_a", "vbmeta_b")
ALIGN = 4096
MAX_DIR_BYTES = 4 << 20            # a directory larger than this is not ours
MAX_CONF = 64 << 10                # loader.conf / an entry file larger than this is not ours
MAX_INIT_BOOT = 64 << 20           # init_boot is 8 MiB on lapis
MAX_INJECT = 1 << 30               # gb/inject.gz read limit
FP_PROP = b"ro.bootimage.build.fingerprint="
RAMDISK_PROP = "system/etc/ramdisk/build.prop"
LZ4_LEGACY = b"\x02\x21\x4c\x18"


class Refused(Exception):
    pass


class Disk:
    """Read-only, aligned reads of a raw disk or an image file. The only open() mode is 'rb'."""

    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb", buffering=0)          # read-only: GENERIC_READ on Windows
        self.reads = []                                 # (offset, length) of every read (the tests check them)

    def read(self, off, n):
        if n <= 0:
            return b""
        a0 = off // ALIGN * ALIGN
        a1 = (off + n + ALIGN - 1) // ALIGN * ALIGN
        self.f.seek(a0)
        buf = bytearray()
        while len(buf) < a1 - a0:
            chunk = self.f.read(min(a1 - a0 - len(buf), 8 << 20))
            if not chunk:
                break
            buf += chunk
        self.reads.append((off, n))
        out = bytes(buf[off - a0:off - a0 + n])
        if len(out) != n:
            raise OSError(f"short read at {off} ({len(out)} of {n} bytes)")
        return out

    def close(self):
        self.f.close()


# ------------------------------------------------------------------------------------------------- slot state
def attrs_tuple(a):
    return {"priority": (a >> 48) & 15, "tries": (a >> 52) & 15, "successful": (a >> 56) & 1, "raw": f"{a:016x}"}


def bootable(t):
    """gb_slotsync slot_bootable(): priority > 0 and (successful or tries left)."""
    return t["priority"] > 0 and (t["successful"] or t["tries"] > 0)


def wanted_slot(ta, tb):
    """gb_slotsync wanted_slot(): the highest priority among bootable slots, the first slot wins a tie."""
    best, bp = None, 0
    for s, t in (("a", ta), ("b", tb)):
        if bootable(t) and t["priority"] > bp:
            best, bp = s, t["priority"]
    return best


def read_gpt(d):
    for ss in SECTORS:
        hdr = d.read(ss, 92)
        if hdr[:8] == b"EFI PART":
            ent_lba, num, esz = struct.unpack_from("<QII", hdr, 72)
            if not (128 <= esz <= 4096 and 0 < num <= 1024):
                raise Refused(f"GPT header with an unusual entry table ({num} x {esz})")
            raw = d.read(ent_lba * ss, num * esz)
            parts = {}
            for i in range(num):
                e = raw[i * esz:(i + 1) * esz]
                if e[:16] == b"\0" * 16:
                    continue
                first, last, attr = struct.unpack_from("<QQQ", e, 32)
                name = e[56:128].decode("utf-16-le", "replace").split("\0", 1)[0]
                if name in parts:
                    raise Refused(f"two GPT partitions named {name!r}")
                parts[name] = {"first": first, "last": last, "attr": attr, "sector": ss}
            return parts
    raise Refused("no GPT on this disk")


# --------------------------------------------------------------------------------------------- FAT32 (ESP)
class Fat32:
    """The minimum of a FAT32 reader: directory listings with long names, cluster chains, small file reads."""

    def __init__(self, d, base):
        self.d, self.base = d, base
        bs = d.read(base, 512)
        if bs[510:512] != b"\x55\xaa":
            raise Refused("EFI-SYSTEM has no FAT boot sector")
        bps, spc, res, nf, rootents, tot16, _media, fsz16 = struct.unpack_from("<HBHBHHBH", bs, 11)
        tot32, fsz32 = struct.unpack_from("<II", bs, 32)
        self.root = struct.unpack_from("<I", bs, 44)[0]
        if bps not in (512, 1024, 2048, 4096) or spc == 0 or spc & (spc - 1) or nf == 0 or res == 0:
            raise Refused("EFI-SYSTEM: not a FAT file system")
        if fsz16 != 0 or rootents != 0 or fsz32 == 0:
            raise Refused("EFI-SYSTEM is not FAT32 (the images always write FAT32)")
        self.bps, self.csize = bps, bps * spc
        self.fat0 = base + res * bps
        self.data0 = base + (res + nf * fsz32) * bps
        total = tot32 or tot16
        self.nclusters = (total - res - nf * fsz32) // spc
        self._fatcache = {}

    def fat(self, cl):
        blk = cl * 4 // 4096
        if blk not in self._fatcache:
            self._fatcache[blk] = self.d.read(self.fat0 + blk * 4096, 4096)
        return struct.unpack_from("<I", self._fatcache[blk], cl * 4 % 4096)[0] & 0x0FFFFFFF

    def chain(self, cl, limit):
        out, seen = [], set()
        while 2 <= cl < self.nclusters + 2 and cl not in seen:
            seen.add(cl)
            out.append(cl)
            if len(out) * self.csize >= limit:
                break
            cl = self.fat(cl)
            if cl >= 0x0FFFFFF8:
                break
        return out

    def runs(self, cl, size):
        """Contiguous (offset, length) runs of a cluster chain, cut to size bytes."""
        out, left = [], size
        for c in self.chain(cl, size):
            off, n = self.data0 + (c - 2) * self.csize, min(self.csize, left)
            if out and out[-1][0] + out[-1][1] == off:
                out[-1] = (out[-1][0], out[-1][1] + n)
            else:
                out.append((off, n))
            left -= n
            if left <= 0:
                break
        return out

    def read_chain(self, cl, size):
        return b"".join(self.d.read(o, n) for o, n in self.runs(cl, size))

    def iter_chain(self, cl, size, step=4 << 20):
        for o, n in self.runs(cl, size):
            for k in range(0, n, step):
                yield self.d.read(o + k, min(step, n - k))

    def listdir(self, cl):
        """{lower-case name: (name, is_dir, first cluster, size)} of the directory at cluster cl."""
        raw = b"".join(self.d.read(self.data0 + (c - 2) * self.csize, self.csize) for c in self.chain(cl, MAX_DIR_BYTES))
        out, lfn, lsum = {}, {}, None
        for i in range(0, len(raw) - 31, 32):
            e = raw[i:i + 32]
            if e[0] == 0x00:
                break
            if e[0] == 0xE5:
                lfn, lsum = {}, None
                continue
            attr = e[11]
            if attr == 0x0F:
                if e[0] & 0x40:
                    lfn = {}
                lsum = e[13]
                lfn[e[0] & 0x1F] = e[1:11] + e[14:26] + e[28:32]
                continue
            short = e[:11]
            if attr & 0x08 or short[:1] == b".":
                lfn, lsum = {}, None
                continue
            name = None
            if lfn:
                s = 0
                for c in short:
                    s = (((s & 1) << 7) + (s >> 1) + c) & 0xFF
                if s == lsum and sorted(lfn) == list(range(1, len(lfn) + 1)):
                    name = b"".join(lfn[k] for k in sorted(lfn)).decode("utf-16-le", "replace").split("\0", 1)[0]
            if not name:
                b, x = short[:8].decode("ascii", "replace").rstrip(), short[8:].decode("ascii", "replace").rstrip()
                name = b + ("." + x if x else "")
            hi, lo = struct.unpack_from("<H", e, 20)[0], struct.unpack_from("<H", e, 26)[0]
            out[name.lower()] = (name, bool(attr & 0x10), (hi << 16) | lo, struct.unpack_from("<I", e, 28)[0])
            lfn, lsum = {}, None
        return out

    def lookup(self, path):
        cl, ent = self.root, None
        for part in path.split("/"):
            ent = self.listdir(cl).get(part.lower())
            if not ent:
                return None
            cl = ent[2]
        return ent


# ---------------------------------------------------------------------------- ramdisks: LZ4 legacy, cpio
def lz4_block(src, max_out=16 << 20):
    """One LZ4 block (no frame), as the kernel's legacy LZ4 ramdisks hold them. Pure Python."""
    out, i, n = bytearray(), 0, len(src)
    while i < n:
        tok = src[i]
        i += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        if off == 0 or off > len(out):
            raise ValueError("lz4: bad match offset")
        ml = tok & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        if off >= ml:
            out += out[len(out) - off:len(out) - off + ml]
        else:
            pat = bytes(out[len(out) - off:])
            out += (pat * (ml // off + 1))[:ml]
        if len(out) > max_out:
            raise ValueError("lz4: block too large")
    return bytes(out)


def decompress_ramdisk(rd):
    """A boot ramdisk as build_esp._ramdisk_files reads it: legacy LZ4, gzip, or a plain newc cpio."""
    if rd[:4] == LZ4_LEGACY:
        buf, o = bytearray(), 0
        while o + 4 <= len(rd):
            if rd[o:o + 4] == LZ4_LEGACY:
                o += 4
                continue
            n = struct.unpack_from("<I", rd, o)[0]
            if n == 0 or o + 4 + n > len(rd):
                break
            buf += lz4_block(rd[o + 4:o + 4 + n], 8 << 20)
            o += 4 + n
        return bytes(buf)
    if rd[:2] == b"\x1f\x8b":
        return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(rd, 256 << 20)
    return rd


def cpio_find(buf, want):
    """{name: data} of the regular files of a newc cpio whose name satisfies want(name)."""
    out, o = {}, 0
    while o + 110 <= len(buf):
        if buf[o:o + 6] not in (b"070701", b"070702"):
            raise ValueError(f"cpio: no newc header at {o}")
        f = [int(buf[o + 6 + 8 * i:o + 14 + 8 * i], 16) for i in range(13)]
        mode, fsz, nsz = f[1], f[6], f[11]
        name = buf[o + 110:o + 110 + nsz - 1].decode("utf-8", "replace")
        o = (o + 110 + nsz + 3) & ~3
        if name == "TRAILER!!!":
            break
        if (mode & 0o170000) == 0o100000 and want(name):
            out[name] = bytes(buf[o:o + fsz])
        o = (o + fsz + 3) & ~3
    return out


def fingerprint_of(propfile):
    """gbinit's rule: the value of the last 'ro.bootimage.build.fingerprint=' line, CRs removed, printable only."""
    vals = [ln[len(FP_PROP):].replace(b"\r", b"") for ln in propfile.split(b"\n") if ln.startswith(FP_PROP)]
    v = vals[-1] if vals else b""
    return v.decode("ascii", "replace") if v and all(32 < c < 127 for c in v) else None


def slot_build(d, parts, s):
    """(fingerprint or None, note) of init_boot_<s>'s ramdisk."""
    p = parts.get(f"init_boot_{s}")
    if not p:
        return None, f"no init_boot_{s} partition"
    size = (p["last"] - p["first"] + 1) * p["sector"]
    if not 0 < size <= MAX_INIT_BOOT:
        return None, f"init_boot_{s}: unusual size {size}"
    base = p["first"] * p["sector"]
    hdr = d.read(base, 4096)
    if hdr[:8] != b"ANDROID!":
        return None, f"init_boot_{s}: no boot image header"
    ksz, rsz = struct.unpack_from("<II", hdr, 8)
    ver = struct.unpack_from("<I", hdr, 40)[0]
    if ver < 3:
        return None, f"init_boot_{s}: boot header v{ver}"
    roff = 4096 + (ksz + 4095) // 4096 * 4096
    if not 0 < rsz <= size - roff:
        return None, f"init_boot_{s}: bad ramdisk size"
    try:
        cp = decompress_ramdisk(d.read(base + roff, rsz))
        files = cpio_find(cp, lambda n: n == RAMDISK_PROP)
    except (ValueError, IndexError, zlib.error) as e:
        return None, f"init_boot_{s}: ramdisk unreadable ({e})"
    if RAMDISK_PROP not in files:
        return None, f"init_boot_{s}: no {RAMDISK_PROP}"
    fp = fingerprint_of(files[RAMDISK_PROP])
    return fp, "" if fp else f"init_boot_{s}: no fingerprint"


def validated_build(fs):
    """(fingerprint or None, note) from gb/inject.gz: every */etc/gb/ota_validated (both trees) must agree."""
    ent = fs.lookup("gb/inject.gz")
    if not ent or ent[1]:
        return None, "no gb/inject.gz on the ESP"
    if ent[3] > MAX_INJECT:
        return None, "gb/inject.gz is unusually large"
    z = zlib.decompressobj(16 + zlib.MAX_WBITS)
    buf, found, o = bytearray(), {}, 0
    try:
        for chunk in fs.iter_chain(ent[2], ent[3]):
            buf += z.decompress(chunk)
            while True:                                  # parse the cpio entries now complete in buf
                if o + 110 > len(buf):
                    break
                if buf[o:o + 6] not in (b"070701", b"070702"):
                    raise ValueError(f"cpio: no newc header at {o}")
                f = [int(buf[o + 6 + 8 * i:o + 14 + 8 * i], 16) for i in range(13)]
                mode, fsz, nsz = f[1], f[6], f[11]
                ds = (o + 110 + nsz + 3) & ~3
                end = (ds + fsz + 3) & ~3
                if end > len(buf):
                    break
                name = buf[o + 110:o + 110 + nsz - 1].decode("utf-8", "replace")
                if name == "TRAILER!!!":
                    raise StopIteration
                if (mode & 0o170000) == 0o100000 and name.endswith("etc/gb/ota_validated"):
                    found[name] = fingerprint_of(bytes(buf[ds:ds + fsz]))
                o = end
            if o > (8 << 20):                            # drop the parsed part now and then, not after every entry
                del buf[:o]
                o = 0
            if z.eof:
                break
    except StopIteration:
        pass
    except (ValueError, zlib.error) as e:
        return None, f"gb/inject.gz unreadable ({e})"
    vals = set(found.values())
    if not found:
        return None, "gb/inject.gz has no ota_validated (an image without the OTA gate)"
    if len(vals) != 1 or None in vals:
        return None, f"gb/inject.gz: the trees disagree on ota_validated ({sorted(found)})"
    return vals.pop(), ""


def build_id(fp):
    parts = (fp or "").split("/")
    return parts[3] if len(parts) >= 5 and parts[3] else (fp or "unknown")


# --------------------------------------------------------------------------------------------------- inspect
def loader_defaults(text):
    """'default' values of loader.conf, lines split at CR or LF as systemd-boot 262.1 does."""
    out = []
    for ln in re.split(r"\r|\n", text):
        m = re.match(r"^\s*default\s+(\S+)\s*$", ln)
        if m:
            out.append(m.group(1))
    return out


def slot_of_entry(name):
    m = re.fullmatch(r"gb-([ab])(?:\.conf)?", name or "")
    return m.group(1) if m else None


def inspect(path, read_builds=True):
    d = Disk(path)
    try:
        parts = read_gpt(d)
        missing = [n for n in NEED if n not in parts]
        if missing:
            raise Refused("not a Googlebook disk: no GPT partition named " + ", ".join(missing))
        ta, tb = attrs_tuple(parts["vbmeta_a"]["attr"]), attrs_tuple(parts["vbmeta_b"]["attr"])
        esp = parts["EFI-SYSTEM"]
        fs = Fat32(d, esp["first"] * esp["sector"])
        res = {"disk": path, "vbmeta_a": ta, "vbmeta_b": tb, "gpt_wants": wanted_slot(ta, tb)}
        lc = fs.lookup("loader/loader.conf")
        if not lc or lc[1] or lc[3] > MAX_CONF:
            raise Refused("EFI-SYSTEM has no usable loader/loader.conf")
        text = fs.read_chain(lc[2], lc[3]).decode("utf-8", "replace")
        res["loader_defaults"] = loader_defaults(text)
        res["preferred_line"] = bool(re.search(r"(?m)^\s*preferred\s", text.replace("\r", "\n")))
        ents = {}
        edir = fs.lookup("loader/entries")
        if edir and edir[1]:
            for low, (name, isdir, cl, size) in sorted(fs.listdir(edir[2]).items()):
                if isdir or not re.fullmatch(r"gb-[a-z0-9-]+\.conf", low):
                    continue
                info = {"size": size}
                if size <= MAX_CONF:
                    body = fs.read_chain(cl, size).decode("utf-8", "replace").replace("\r", "\n")
                    for key in ("title", "initrd"):
                        m = re.search(r"(?m)^" + key + r"\s+(.*?)\s*$", body)
                        info[key] = m.group(1) if m else None
                    info["nvidia_test"] = "gb.nouveau=test" in body
                ents[name.lower()] = info
        res["entries"] = ents
        root = fs.listdir(fs.root)
        res["initrd"] = {s: (root.get(f"initrd-{s}.img") or (None, False, 0, None))[3] for s in "ab"}
        gbd = root.get("gb")
        stamps = fs.listdir(gbd[2]) if gbd and gbd[1] else {}
        res["stamps"] = {s: f"slot_{s}.stamp" in stamps for s in "ab"}
        res["builds"], res["build_notes"] = {}, {}
        if read_builds:
            for s in "ab":
                res["builds"][s], res["build_notes"][s] = slot_build(d, parts, s)
            res["validated"], res["validated_note"] = validated_build(fs)
        else:
            res["validated"], res["validated_note"] = None, "not read"
        res["reads"] = list(d.reads)
        return res
    finally:
        d.close()


# --------------------------------------------------------------------------------------------------- verdict
def verdict(r):
    """(exit code, headline, [advice lines]) for an inspect() result."""
    defs, ents, want = r["loader_defaults"], set(r["entries"]), r["gpt_wants"]
    caveat = ("If you ever pressed d or D in the boot menu, a firmware variable overrides loader.conf; this tool "
              "cannot see it.")
    stop = ["Do not start the stick; send this output to the lead."]
    if len(defs) != 1:
        return 4, f"loader.conf has {len(defs)} 'default' lines {defs}: unexpected", stop
    ds = slot_of_entry(defs[0])
    if not ds or f"gb-{ds}.conf" not in ents:
        return 4, f"loader.conf names {defs[0]!r}, which is not an existing gb-a/gb-b entry: unexpected", stop
    if r["preferred_line"]:
        return 4, "loader.conf has a 'preferred' line (nothing of ours writes one): it overrides the default", stop
    os_ = "a" if ds == "b" else "b"
    D, O = ds.upper(), os_.upper()
    td, to = r["vbmeta_" + ds], r["vbmeta_" + os_]
    has_o = f"gb-{os_}.conf" in ents
    v, nb, ob = r.get("validated"), r["builds"].get(ds), r["builds"].get(os_)
    vid, nid, oid = build_id(v), build_id(nb), build_id(ob)
    pick = (f"To stay on the tested build at the next start: press the Down or Up arrow key while the boot menu shows "
            f"(3 s) to stop the timer, choose the entry that ends '(slot {O})' (not '[NVIDIA test]') and press Enter.")
    if v and nb and nb != v:
        if td["successful"]:
            return 1, (f"The next start runs slot {D} with build {nid}, which is NOT the build this image was made for "
                       f"({vid}). Slot {D} is marked successful: it has started before, so the stick already moved to "
                       f"that build."), [
                f"Going back is only possible by re-flashing the stick (the update has merged into the old slot's "
                f"space once {nid} reached boot_completed).", caveat]
        extra = [pick, f"After you start '(slot {O})', do not choose '(slot {D})' on this stick later (the update is "
                       f"then cancelled and that entry may no longer start); re-flashing removes it."] if has_o else []
        return 1, (f"SWITCHED: gb_slotsync made slot {D} the default after a system update; the next start runs build "
                   f"{nid}, which is NOT the build this image was made for ({vid}). Slot {D} has not started yet "
                   f"(GPT: tries {td['tries']}, not successful)."), extra + [caveat]
    if v and nb == v:
        head = f"The next start runs slot {D} with build {nid}: the build this image was made for."
    elif not v or not nb:
        head = (f"The next start runs slot {D} (loader.conf default). Its build could not be compared "
                f"({r['build_notes'].get(ds) or r.get('validated_note') or 'not read'}).")
    else:
        head = f"The next start runs slot {D}."
    adv = []
    if want == os_ and not has_o:
        adv.append(f"A system update COMPLETED into slot {O} (build {oid}; GPT: priority {to['priority']} > "
                   f"{td['priority']}, tries {to['tries']}, not successful) but gb_slotsync did not switch to it before "
                   f"Android stopped. Android treats it as rolled back at the next start and may download and install "
                   f"it again; once that completes, gb_slotsync switches the default and the start after that runs "
                   f"{oid}.")
    elif has_o:
        adv.append(f"'(slot {O})' (build {oid}) is also in the boot menu (a held update, or an update you left by "
                   f"picking '(slot {D})'). Do not choose it.")
    elif not bootable(to):
        # (review 2026-11-06) a freshly flashed stick is in this state too: the wave-9 USB image, read with this tool,
        # has vbmeta_a priority 0, tries 0, successful 0 as built, the same bits update_engine leaves while it writes
        adv.append(f"Slot {O} is marked not bootable. That is how a freshly flashed stick is made (slot {O} is not "
                   f"used), and also the state while a system update is being written into slot {O} (or after one "
                   f"failed). If Settings showed 'Installing system update' in an earlier session, Android resumes it "
                   f"in the next session with network; when it completes, gb_slotsync makes slot {O} the default "
                   f"within a minute, so the start AFTER that runs the new build unless you pick '(slot {D})' then.")
    if not (v and nb) and has_o and want == ds and not td["successful"] and td["tries"] > 0:
        # builds not comparable, but the slot state is a followed update that has not started yet
        return 1, (f"SWITCHED (by the slot state; builds not comparable): slot {D} is the default, activated by an "
                   f"update (GPT: tries {td['tries']}, not successful) and never started."), [pick, caveat]
    return 0, head, adv + [caveat]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only check of a Googlebook stick after a Google system update.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--disk", type=int, help="Windows disk number (Get-Disk) of the stick; needs an administrator prompt")
    g.add_argument("--image", help="a raw disk image file")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args(argv)
    if a.disk is not None:
        if a.disk < 0 or os.name != "nt":
            print("refused: --disk needs a non-negative Windows disk number", file=sys.stderr)
            return 2
        path = r"\\.\PhysicalDrive%d" % a.disk
    else:
        path = a.image
        if not os.path.isfile(path):
            print(f"refused: {path} is not a file", file=sys.stderr)
            return 2
    try:
        r = inspect(path)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        denied = isinstance(e, PermissionError) or getattr(e, "winerror", 0) == 5
        absent = isinstance(e, FileNotFoundError) or getattr(e, "winerror", 0) == 2     # (review 2026-11-06)
        hint = (" (run it from an administrator prompt)" if a.disk is not None and denied else
                " (no disk with this number is attached: plug the stick in and check the number with Get-Disk)"
                if a.disk is not None and absent else "")
        print(f"read error: {e}{hint}", file=sys.stderr)
        return 3
    code, head, advice = verdict(r)
    if a.json:
        out = {k: v for k, v in r.items() if k != "reads"}
        out.update({"exit": code, "verdict": head, "advice": advice})
        print(json.dumps(out, indent=1, sort_keys=True))
        return code
    print(f"disk: {path} (read only)")
    for s in "ab":
        t = r["vbmeta_" + s]
        print(f"  slot {s.upper()}: vbmeta_{s} priority {t['priority']}, tries {t['tries']}, successful "
              f"{t['successful']}{'' if bootable(t) else ' (not bootable)'}; init_boot_{s} build "
              f"{build_id(r['builds'].get(s)) if r['builds'].get(s) else '? ' + (r['build_notes'].get(s) or '')}")
    print(f"  image made for (ota_validated): {build_id(r['validated']) if r['validated'] else '? ' + r['validated_note']}")
    print(f"  the GPT wants slot: {(r['gpt_wants'] or 'none').upper()} (gb_slotsync's rule)")
    print(f"  loader.conf default: {', '.join(r['loader_defaults']) or '(none)'}")
    for name, info in sorted(r["entries"].items()):
        print(f"  entry {name}: title {info.get('title')!r}, initrd {info.get('initrd')!r}")
    print("  " + ", ".join(f"initrd-{s}.img {'%d bytes' % r['initrd'][s] if r['initrd'][s] is not None else 'absent'}"
                           for s in "ab")
          + "; stamps: " + ", ".join(f"slot_{s} {'yes' if r['stamps'][s] else 'no'}" for s in "ab"))
    print("")
    print("VERDICT: " + head)
    for x in advice:
        print("  - " + x)
    return code


if __name__ == "__main__":
    sys.exit(main())
