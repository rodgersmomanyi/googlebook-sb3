"""Build D:\\GB-iso\\googlebook-sb3-installer.iso - the Googlebook SB3 installer ISO (DESIGN.md section 6).

Windows driver; run with python -I. PERSONAL USE ONLY: the ISO carries Google's proprietary lapis image; it stays
on this PC and is never uploaded or published.

  python -I C:\\GB-x86\\iso\\build\\build_iso.py [--out D:\\GB-iso\\googlebook-sb3-installer.iso]
      [--usb-image C:\\GB-x86\\out\\googlebook-sb3-usb.img] [--new-export] [--no-payload-cache]
      [--keep-nx-compat] [--keep-work] [--dry-run]

Steps (6.2): 1 preflight (read-only; incl. verify_image.py on the USB image, DESIGN 13.2) - 2 dual-boot export (reuse
C:\\GB-x86\\iso\\work\\export or a fresh build_internal.py run) + one read-only pass over the USB image (sha256,
object/chunk hashes, zero checks) + the 1.5 cross-checks and cross-check 8 (both ESPs hold the same tree, DESIGN 13.3;
a reused export's ESP also passes verify_image.py --esp) - 3 builder setup and upload - 4 payload chunks (cache on the
builder) - 5 small payload files - 6 initramfs, GRUB, kernel, ISO on the builder - 7 transfer + records to
D:\\GB-iso\\ - 8 builder cleanup.

Read-only on: the USB image, lapis.bin (only build_internal.py reads it), C:\\GB-x86\\out. Never touches a disk
device; on the builder only files below /var/tmp/gbiso/ are written.

PIPELINE-TEST ONLY (never produces the real ISO): --test-tiny-payload builds the same ISO structure with every object
cut to 3 MiB in 1 MiB chunks (manifest key "test_payload": true; NOT installable), records in D:\\GB-iso\\pipeline-test\\;
the builder's last/ link is not touched. --installer-dir DIR (only with --test-tiny-payload) uses another installer
tree, e.g. build\\pipeline_test\\stub_installer (boots, reports "GBSTUB ..." lines, powers off). Only such a stub ISO is
copied to Windows; with the real installer the tiny ISO stays on the builder (integration check of the initramfs).
"""
import argparse
import datetime
import glob
import hashlib
import importlib.util
import io
import json
import os
import queue
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import threading
import time
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))                      # C:\GB-x86\iso\build
ISO_HOME = os.path.dirname(HERE)                                       # C:\GB-x86\iso
WORK = os.path.join(ISO_HOME, "work")
EXPORT = os.path.join(WORK, "export")
EXPORT_NEW = os.path.join(WORK, "export.new")
INSTALLER = os.path.join(ISO_HOME, "installer")
BUILD_INTERNAL = r"C:\GB-build\native\dualboot\build_internal.py"
VERIFY_IMAGE = r"C:\GB-x86\verify_image.py"
DEFAULT_OUT = r"D:\GB-iso\googlebook-sb3-installer.iso"
DEFAULT_USB = r"C:\GB-x86\out\googlebook-sb3-usb.img"
VERIFIED_USB_SHA256 = "7ac6ad52804146c1192f1490f47c86d4c27c97bee4549ab00213f7f7c1c887c7"
TEST_OUT_DIR = r"D:\GB-iso\pipeline-test"
SSH_KEY = "C:/GB-build/builder_key"
BUILDER = "builder@192.168.115.132"
SSH_CANDIDATES = [r"C:\Program Files\Git\usr\bin\ssh.exe"]   # Windows OpenSSH pipes are ~10x slower (8 MiB/s)
REMOTE = "/var/tmp/gbiso"
KREL = "6.8.0-147-generic"
ANDROID_BUILD = "CL3B.260622.276.R4"
LABEL = "GBSB3INST"
ISO_NAME = "googlebook-sb3-installer.iso"

SECTOR = 512
KiB, MiB, GiB = 1 << 10, 1 << 20, 1 << 30
CHUNK = 512 * MiB
RESERVE = GiB
USERDATA_MIN = 8 * GiB
ZSTD_LEVEL, ZSTD_LONG = 9, 27
ISO_CEILING = 8 * GiB
ISO_ESTIMATE = int(7.5 * GiB)
TINY_RAW, TINY_CHUNK = 3 * MiB, 1 * MiB

GB_NAMES = ["EFI-SYSTEM", "boot_a", "boot_b", "init_boot_a", "init_boot_b", "vendor_boot_a", "vendor_boot_b",
            "vbmeta_a", "vbmeta_b", "pvmfw_a", "pvmfw_b", "misc", "metadata", "frp_persist",
            "desktop_security_persist", "desktop_security_storage", "super", "userdata"]
SOURCED = ["boot_a", "boot_b", "init_boot_a", "init_boot_b", "vendor_boot_a", "vendor_boot_b", "vbmeta_a", "vbmeta_b",
           "pvmfw_a", "pvmfw_b", "super"]
OBJECT_ORDER = ["esp-usb", "esp-internal"] + SOURCED
TYPE_ESP = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
BUILD_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}$")
INSTALLER_REQUIRED = ["init", "bin/gbinstall.sh", "bin/gbmirror.sh", "lib/gbinstall.py", "lib/gb/run.py",
                      "etc/modules.list", "etc/initramfs.list"]
SAFE_ARG = re.compile(r"^[A-Za-z0-9._/@:=,+-]+$")
SHELL_OPS = {"&&", "|"}


class BuildError(Exception):
    pass


# ----------------------------------------------------------------------------------------------------------------
# logging

class Log:
    def __init__(self):
        self.buf = []
        self.f = None

    def open(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.f = open(path, "a", encoding="utf-8", newline="\n")
        for line in self.buf:
            self.f.write(line + "\n")
        self.f.flush()
        self.buf = []
        self.path = path

    PROFILE_RE = re.compile(r"(?i)[a-z]:[\\/]+users[\\/]+[^\\/\s'\"]+")

    def __call__(self, msg=""):
        for line in self.PROFILE_RE.sub("<profile>", str(msg)).split("\n"):
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S")
            text = f"{stamp} {line}"
            print(text, flush=True)
            if self.f:
                self.f.write(text + "\n")
                self.f.flush()
            else:
                self.buf.append(text)

    def section(self, title):
        self("")
        self(f"===== {title}")

    def close(self):
        if self.f:
            self.f.close()
            self.f = None


log = Log()


def fmt_gib(n):
    return f"{n / GiB:.2f} GiB"


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def json_bytes(obj):
    return (json.dumps(obj, indent=1, ensure_ascii=True) + "\n").encode("ascii")


# ----------------------------------------------------------------------------------------------------------------
# builder access (ssh)

def find_ssh():
    for c in SSH_CANDIDATES:
        if os.path.isfile(c):
            return c
    w = shutil.which("ssh")
    if not w:
        raise BuildError("no ssh client found (Git for Windows ssh.exe expected)")
    return w


class Builder:
    def __init__(self):
        self.exe = find_ssh()
        self.base = [self.exe, "-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
                     "-o", "ServerAliveInterval=30", "-o", "Compression=no", "-o", "LogLevel=ERROR", BUILDER]

    def _cmd(self, words):
        for w in words:
            if w not in SHELL_OPS and not SAFE_ARG.match(w):
                raise BuildError(f"unsafe remote argument {w!r}")
        return self.base + [" ".join(words)]

    def run(self, words, check=True, quiet=False, timeout=None, stdin_bytes=None):
        """Run a remote command (simple words, no shell metacharacters). Output lines go to the log."""
        if not quiet:
            log(f"[builder] $ {' '.join(words)}")
        p = subprocess.Popen(self._cmd(words), stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if stdin_bytes is not None:
            t = threading.Thread(target=self._feed, args=(p, stdin_bytes), daemon=True)
            t.start()
        lines = []
        start = time.time()
        for raw in iter(p.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            lines.append(line)
            if not quiet:
                log(f"[builder] {line}")
            if timeout and time.time() - start > timeout:
                p.kill()
                raise BuildError(f"builder command timed out: {' '.join(words)}")
        rc = p.wait()
        if check and rc != 0:
            if quiet:
                for line in lines[-40:]:
                    log(f"[builder] {line}")
            raise BuildError(f"builder command failed (exit {rc}): {' '.join(words)}")
        return rc, lines

    @staticmethod
    def _feed(p, data):
        try:
            mv = memoryview(data)
            for i in range(0, len(mv), 4 * MiB):
                p.stdin.write(mv[i:i + 4 * MiB])
            p.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    def stream_in(self, words, producer):
        """Run a remote command with stdin fed by producer(write) (binary). Returns output lines."""
        p = subprocess.Popen(self._cmd(words), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, bufsize=0)
        out = []
        t = threading.Thread(target=lambda: out.append(p.stdout.read()), daemon=True)
        t.start()
        err = None
        try:
            producer(p.stdin.write)
        except (BrokenPipeError, OSError) as e:
            err = e
        try:
            p.stdin.close()
        except OSError:
            pass
        rc = p.wait()
        t.join()
        lines = out[0].decode("utf-8", "replace").splitlines() if out else []
        if rc != 0 or err:
            for line in lines[-20:]:
                log(f"[builder] {line}")
            raise BuildError(f"builder command failed (exit {rc}{', ' + str(err) if err else ''}): {' '.join(words)}")
        return lines

    def fetch(self, remote_path, local_path, expect_size):
        """Copy a remote file to local_path (ssh 'cat' with stdout going straight to the file: ~56 MiB/s here,
        reading the pipe from Python is far slower on Windows). Returns the sha256 of the file as written."""
        t0 = time.time()
        with open(local_path, "wb") as f:
            p = subprocess.Popen(self._cmd(["cat", remote_path]), stdin=subprocess.DEVNULL, stdout=f,
                                 stderr=subprocess.PIPE)
            errs = []
            t = threading.Thread(target=lambda: errs.append(p.stderr.read()), daemon=True)
            t.start()
            while p.poll() is None:
                time.sleep(1)
                if int(time.time() - t0) % 15 == 0:
                    n = os.path.getsize(local_path)
                    log(f"  transfer {fmt_gib(n)} / {fmt_gib(expect_size)} ({n / MiB / (time.time() - t0):.0f} MiB/s)")
            t.join()
            f.flush()
            os.fsync(f.fileno())
        rc = p.returncode
        n = os.path.getsize(local_path)
        if rc != 0 or n != expect_size:
            err = (errs[0] if errs else b"").decode("utf-8", "replace")
            raise BuildError(f"transfer of {remote_path} failed (exit {rc}, {n} of {expect_size} bytes): {err.strip()}")
        log(f"  transferred {fmt_gib(n)} in {time.time() - t0:.0f} s")
        h = hashlib.sha256()
        with open(local_path, "rb") as f:
            while True:
                blk = f.read(16 * MiB)
                if not blk:
                    break
                h.update(blk)
        return h.hexdigest()

    def put_tar(self, remote_dir, members):
        """members: list of (arcname, bytes, mode). Extracted below remote_dir (created)."""
        bio = io.BytesIO()
        with tarfile.open(fileobj=bio, mode="w", format=tarfile.PAX_FORMAT) as tf:
            for name, data, mode in members:
                ti = tarfile.TarInfo(name)
                ti.size = len(data)
                ti.mode = mode
                ti.mtime = int(time.time())
                tf.addfile(ti, io.BytesIO(data))
        self.run(["mkdir", "-p", remote_dir, "&&", "tar", "--no-same-owner", "-x", "-C", remote_dir, "-f", "-"],
                 stdin_bytes=bio.getvalue(), quiet=True)


# ----------------------------------------------------------------------------------------------------------------
# USB image: GPT, ESP

def guid_le(b):
    return str(uuid.UUID(bytes_le=bytes(b)))


def read_usb_gpt(path):
    size = os.path.getsize(path)
    if size % SECTOR:
        raise BuildError("USB image size is not a multiple of 512")
    n = size // SECTOR
    with open(path, "rb") as f:
        primary = f.read(34 * SECTOR)
        f.seek(size - 33 * SECTOR)
        backup = f.read(33 * SECTOR)
    mbr = primary[:SECTOR]
    if mbr[510:512] != b"\x55\xaa" or mbr[450] != 0xEE:
        raise BuildError("USB image: no protective MBR")
    st, cnt = struct.unpack_from("<II", mbr, 454)
    if st != 1 or cnt != min(n - 1, 0xFFFFFFFF):
        raise BuildError("USB image: protective MBR entry does not cover the disk")

    def header(sec, want_my, want_alt):
        if sec[:8] != b"EFI PART":
            raise BuildError(f"USB image: no GPT header at LBA {want_my}")
        rev, hsize, crc = struct.unpack_from("<III", sec, 8)
        h = bytearray(sec[:hsize])
        struct.pack_into("<I", h, 16, 0)
        if hsize != 92 or zlib.crc32(bytes(h)) != crc:
            raise BuildError(f"USB image: GPT header CRC/size wrong at LBA {want_my}")
        my, alt, first, last = struct.unpack_from("<QQQQ", sec, 24)
        disk = guid_le(sec[56:72])
        ent_lba, count, esize, ecrc = struct.unpack_from("<QIII", sec, 72)
        if my != want_my or alt != want_alt:
            raise BuildError(f"USB image: GPT header at LBA {want_my} has my/alternate {my}/{alt}")
        return dict(first=first, last=last, disk=disk, entries_lba=ent_lba, count=count, esize=esize, ecrc=ecrc)

    ph = header(primary[SECTOR:2 * SECTOR], 1, n - 1)
    bh = header(backup[-SECTOR:], n - 1, 1)
    if ph["entries_lba"] != 2 or bh["entries_lba"] != n - 33 or ph["count"] != 128 or ph["esize"] != 128:
        raise BuildError("USB image: unexpected GPT geometry")
    ents = primary[2 * SECTOR:34 * SECTOR]
    if zlib.crc32(ents) != ph["ecrc"] or backup[:128 * 128] != ents or bh["ecrc"] != ph["ecrc"]:
        raise BuildError("USB image: GPT entry array CRC wrong or primary/backup differ")
    if (ph["first"], ph["last"], ph["disk"]) != (bh["first"], bh["last"], bh["disk"]):
        raise BuildError("USB image: primary and backup headers differ")
    parts = []
    for i in range(128):
        e = ents[i * 128:(i + 1) * 128]
        if e[:16] == bytes(16):
            if any(e):
                raise BuildError(f"USB image: unused GPT slot {i} is not zero")
            continue
        first, last, attrs = struct.unpack_from("<QQQ", e, 32)
        parts.append(dict(slot=i, type=guid_le(e[:16]), guid=guid_le(e[16:32]), first=first, last=last, attrs=attrs,
                          name=e[56:128].decode("utf-16-le").rstrip("\0")))
    if [p["slot"] for p in parts] != list(range(18)) or [p["name"] for p in parts] != GB_NAMES:
        raise BuildError(f"USB image: expected the 18 Googlebook partitions in slots 0..17, found "
                         f"{[p['name'] for p in parts]}")
    prev_end = ph["first"] - 1
    for p in parts:
        if p["first"] <= prev_end or p["last"] < p["first"] or p["last"] > ph["last"] or p["first"] % 2048:
            raise BuildError(f"USB image: partition {p['name']} overlaps, is out of range or not 1 MiB aligned")
        prev_end = p["last"]
    return dict(path=path, size=size, total_lba=n, disk_guid=ph["disk"], first_usable=ph["first"],
                last_usable=ph["last"], entries_lba=2, backup_entries_lba=n - 33, entry_count=128, entry_size=128,
                entries_crc32=ph["ecrc"], parts=parts, by_name={p["name"]: p for p in parts},
                gpt_primary=primary, gpt_backup=backup)


def read_usb_esp(usb):
    p = usb["by_name"]["EFI-SYSTEM"]
    with open(usb["path"], "rb") as f:
        f.seek(p["first"] * SECTOR)
        esp = f.read((p["last"] - p["first"] + 1) * SECTOR)
    slots = sorted(set(m.group(1).decode() for m in re.finditer(rb"initrd  /initrd-([a-z])\.img", esp)))
    if len(slots) != 1:
        raise BuildError(f"USB ESP: expected one 'initrd  /initrd-<slot>.img' loader line, found slots {slots}")
    ids = sorted(set(m.group(1).decode() for m in re.finditer(
        rb"androidboot\.boot_part_uuid=([0-9a-f-]{36})", esp)))
    sup = usb["by_name"]["super"]["guid"]
    if ids != [sup]:
        raise BuildError(f"USB ESP: androidboot.boot_part_uuid {ids} does not name the image's super ({sup})")
    return slots[0]


# ----------------------------------------------------------------------------------------------------------------
# one read-only pass over a file: whole sha256, object/chunk sha256, zero ranges, needle

def scan_file(path, objects, zeros, chunk_bytes, whole=True, needle=None, label="image"):
    """objects: [dict(id, start, raw_size)]; zeros: [(start, end, label)]. Returns dict(sha256, objects:
    {id: {sha256, chunks: [hex]}}, nonzero: [labels], needle: bool)."""
    size = os.path.getsize(path)
    BLK = 16 * MiB
    whole_h = hashlib.sha256() if whole else None
    st = {o["id"]: dict(o, h=hashlib.sha256(), ch=hashlib.sha256(), cpos=0, chunks=[], pos=0) for o in objects}
    nonzero = set()
    found = [False]
    q = queue.Queue(maxsize=4)

    def reader():
        try:
            with open(path, "rb", buffering=0) as f:
                off = 0
                while off < size:
                    b = f.read(min(BLK, size - off))
                    if not b:
                        break
                    q.put((off, b))
                    off += len(b)
        finally:
            q.put(None)

    def do_objects(off, b, which):
        mv = memoryview(b)
        end = off + len(b)
        for o in st.values():
            a0, a1 = max(off, o["start"]), min(end, o["start"] + o["raw_size"])
            if a0 >= a1:
                continue
            if which == "obj":
                o["h"].update(mv[a0 - off:a1 - off])
                continue
            r = a0 - o["start"]
            while r < a1 - o["start"]:
                cend = min((r // chunk_bytes + 1) * chunk_bytes, o["raw_size"])
                n = min(cend, a1 - o["start"]) - r
                o["ch"].update(mv[r + o["start"] - off:r + o["start"] - off + n])
                r += n
                if r == cend:
                    o["chunks"].append(o["ch"].hexdigest())
                    o["ch"] = hashlib.sha256()

    def do_zero(off, b):
        end = off + len(b)
        for z0, z1, lab in zeros:
            a0, a1 = max(off, z0), min(end, z1)
            if a0 < a1 and lab not in nonzero and b.count(0, a0 - off, a1 - off) != a1 - a0:
                nonzero.add(lab)
        if needle and not found[0] and b.find(needle) >= 0:
            found[0] = True

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t0 = last = time.time()
    done = 0
    with ThreadPoolExecutor(4) as ex:
        while True:
            item = q.get()
            if item is None:
                break
            off, b = item
            futs = [ex.submit(do_objects, off, b, "obj"), ex.submit(do_objects, off, b, "chunk"),
                    ex.submit(do_zero, off, b)]
            if whole_h:
                futs.append(ex.submit(whole_h.update, b))
            for fu in futs:
                fu.result()
            done += len(b)
            if time.time() - last > 15:
                last = time.time()
                log(f"  scan {label}: {fmt_gib(done)} / {fmt_gib(size)} ({done / MiB / (last - t0):.0f} MiB/s)")
    t.join()
    if done != size:
        raise BuildError(f"short read of {path}")
    log(f"  scan {label}: {fmt_gib(size)} in {time.time() - t0:.0f} s")
    res = {"sha256": whole_h.hexdigest() if whole_h else None, "objects": {}, "nonzero": sorted(nonzero),
           "needle": found[0]}
    for oid, o in st.items():
        nch = (o["raw_size"] + chunk_bytes - 1) // chunk_bytes
        if len(o["chunks"]) != nch:
            raise BuildError(f"scan: {oid} produced {len(o['chunks'])} chunk hashes, expected {nch}")
        res["objects"][oid] = {"sha256": o["h"].hexdigest(), "chunks": o["chunks"], "raw_size": o["raw_size"]}
    return res


# ----------------------------------------------------------------------------------------------------------------
# verify_image.py (DESIGN 13.2, 13.3): its readers are imported by path (module API, WAVE2_PLAN_v2 3.2); the full
# check set runs as its own process

_VI = []


def verify_image_module():
    if not _VI:
        spec = importlib.util.spec_from_file_location("gb_verify_image", VERIFY_IMAGE)
        mod = importlib.util.module_from_spec(spec)
        old = sys.dont_write_bytecode
        sys.dont_write_bytecode = True                  # never a __pycache__ next to verify_image.py
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.dont_write_bytecode = old
        _VI.append(mod)
    return _VI[0]


def run_verify_image(args, what):
    """verify_image.py ARGS (read-only); every output line goes to the log. True = exit 0 and 'RESULT: OK'."""
    log(f"verify_image.py on {what}: python -I -B {VERIFY_IMAGE} {' '.join(args)}")
    try:
        p = subprocess.run([sys.executable, "-I", "-B", VERIFY_IMAGE] + args, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=1800)
    except subprocess.TimeoutExpired:
        log("  [verify] timed out after 1800 s")
        return False
    lines = p.stdout.decode("utf-8", "replace").splitlines()
    for line in lines:
        log(f"  [verify] {line}")
    return p.returncode == 0 and bool(lines) and lines[-1].strip() == "RESULT: OK"


def esp_digest_files(raw):
    """verify_image.fat_files of an ESP image, with every file except the loader entries replaced by its sha256
    digest (compare_export only needs equality there; keeps two 512 MiB ESPs in memory at most once each)."""
    files = verify_image_module().fat_files(raw)
    return {k: (v if k.startswith("loader/entries/") else hashlib.sha256(v).digest()) for k, v in files.items()}


def usb_esp_bytes(usb):
    p = usb["by_name"]["EFI-SYSTEM"]
    with open(usb["path"], "rb") as f:
        f.seek(p["first"] * SECTOR)
        return f.read((p["last"] - p["first"] + 1) * SECTOR)


# ----------------------------------------------------------------------------------------------------------------
# dual-boot export (1.5)

def load_export(d):
    mp, ep = os.path.join(d, "manifest.json"), os.path.join(d, "EFI-SYSTEM.img")
    if not (os.path.isfile(mp) and os.path.isfile(ep)):
        return None
    mb = open(mp, "rb").read()
    return dict(dir=d, manifest=json.loads(mb), manifest_bytes=mb, manifest_sha256=sha256_bytes(mb), esp_path=ep)


def run_fresh_export(slot):
    if os.path.exists(EXPORT_NEW):
        log(f"removing an old {EXPORT_NEW}")
        shutil.rmtree(EXPORT_NEW)
    free = shutil.disk_usage("C:\\").free
    if free < 15 * GiB:
        raise BuildError(f"C: has {fmt_gib(free)} free; a fresh export needs >= 15 GiB")
    env = dict(os.environ, GB_SLOT=slot)
    cmd = [sys.executable, "-I", "-B", BUILD_INTERNAL, "--out", EXPORT_NEW]
    log(f"$ set GB_SLOT={slot} & python -I -B {BUILD_INTERNAL} --out {EXPORT_NEW}")   # no user-profile paths
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    for raw in iter(p.stdout.readline, b""):
        log("  [export] " + raw.decode("utf-8", "replace").rstrip())
    if p.wait() != 0:
        raise BuildError("build_internal.py failed (see the log)")
    exp = load_export(EXPORT_NEW)
    if not exp:
        raise BuildError("build_internal.py did not produce manifest.json + EFI-SYSTEM.img")
    return exp


def finalize_export(exp):
    """export.new -> export (old export renamed export.prev-<created>, then deleted); keep only EFI-SYSTEM.img."""
    for f in os.listdir(EXPORT_NEW):
        if f.lower().endswith(".img") and f != "EFI-SYSTEM.img":
            os.remove(os.path.join(EXPORT_NEW, f))
    if os.path.exists(EXPORT):
        old = load_export(EXPORT)
        created = (old["manifest"].get("created", "unknown") if old else "unknown")
        prev = os.path.join(WORK, "export.prev-" + re.sub(r"[^0-9A-Za-z-]", "", created.replace("T", "-")))
        if os.path.exists(prev):
            shutil.rmtree(prev)
        os.rename(EXPORT, prev)
        os.rename(EXPORT_NEW, EXPORT)
        shutil.rmtree(prev)
        log(f"replaced the previous export (created {created})")
    else:
        os.rename(EXPORT_NEW, EXPORT)
    exp.update(dir=EXPORT, esp_path=os.path.join(EXPORT, "EFI-SYSTEM.img"))
    return exp


def check_esp_tree(usb_files, exp_files):
    """Cross-check 8 (DESIGN 13.3) on two {ESP path: bytes or digest} maps: both ESPs hold the same tree - vmlinuz,
    gb/inject.gz, gb/bootconfig_extra, initrd-<slot>.img, gb/slot_<slot>.stamp and every gb/dgpu-* byte-identical,
    the same file set, and the entries (gb-<slot>.conf, gb-<slot>-nvtest.conf) equal except the title text, gb.boot=
    and androidboot.boot_part_uuid= (verify_image.compare_export, the same rule as its V17). Raises BuildError."""
    diff = verify_image_module().compare_export(usb_files, exp_files)
    if diff:
        raise BuildError(f"export cross-check 8 failed: the export ESP was not built from the same tree as the USB "
                         f"image ({diff[0]})")


def export_checks(exp, usb, slot, scan, esp_scan):
    """1.5 cross-checks 1-7. Raises BuildError with the design's message; returns the export_check record."""
    m = exp["manifest"]
    names = [p.get("name") for p in m.get("partitions", [])]
    by = {p["name"]: p for p in m.get("partitions", [])}
    checks = {}

    def need(cond, key, msg):
        if not cond:
            raise BuildError(f"export cross-check {key} failed: {msg}")
        checks[key] = "ok"

    sup = by.get("super", {}).get("guid", "")
    cmd = m.get("cmdline", "")
    need(m.get("format") == "gb-dualboot-manifest/1" and m.get("test_truncated") is False
         and m.get("disk_selector") == "boot_part_uuid" and "gb.boot=internal" in cmd.split()
         and "gb.hw=native" in cmd.split() and f"androidboot.boot_part_uuid={sup}" in cmd.split(),
         "1_manifest", "format/test_truncated/disk_selector/cmdline")
    ud = by.get("userdata", {})
    need(names == GB_NAMES and ud.get("fill") is True and ud.get("min_size") == USERDATA_MIN,
         "2_partitions", f"partition names/order or userdata fill/min_size ({names})")
    need(m.get("slot") == slot, "3_slot", f"export slot {m.get('slot')!r} != USB image slot {slot!r}")
    for n in SOURCED:
        e, u = by[n], usb["by_name"][n]
        o = scan["objects"].get(n)
        psize = (u["last"] - u["first"] + 1) * SECTOR
        ok = (e["type"] == u["type"] and int(e["attributes"], 16) == u["attrs"] and e.get("file_size")
              and e["file_size"] <= psize and o is not None and o["raw_size"] == e["file_size"]
              and o["sha256"] == e["sha256"] and f"tail:{n}" not in scan["nonzero"])
        if not ok:
            raise BuildError(f"export cross-check 4 failed for {n}: the USB image and lapis.bin differ (an OTA in "
                             f"the running VM?): rebuild and verify the USB image first")
    checks["4_sourced"] = "ok"
    esp = by["EFI-SYSTEM"]
    need(os.path.getsize(exp["esp_path"]) == 536870912 and esp.get("file_size") == 536870912
         and esp_scan["objects"]["esp-internal"]["sha256"] == esp.get("sha256"),
         "5_esp", "EFI-SYSTEM.img size or sha256 differs from the export manifest")
    egu = {p["guid"].lower() for p in m["partitions"]}
    ugu = {p["guid"].lower() for p in usb["parts"]} | {usb["disk_guid"].lower()}
    need(not (egu & ugu) and len(egu) == 18, "6_guids", "export GUIDs overlap the USB image GUIDs or repeat")
    overlap = []
    cands = [r"D:\gb-export\manifest.json", r"C:\gb-export\manifest.json"] + \
        sorted(glob.glob(r"C:\GB-backup\**\manifest_installed.json", recursive=True))
    for c in cands:
        try:
            if not os.path.isfile(c):
                continue
            other = json.load(open(c, encoding="utf-8"))
            hit = sorted(egu & {str(p.get("guid", "")).lower() for p in other.get("partitions", [])})
        except (OSError, ValueError, AttributeError) as e:
            log(f"  WARNING: could not read {c}: {e.__class__.__name__}")
            continue
        if hit:
            overlap.append({"file": c, "guids": hit})
            log(f"  WARNING: {len(hit)} export GUID(s) also in {c} (only an ISO install that was imported on "
                f"Windows explains this)")
    checks["6_guid_overlap_info"] = "ok"
    try:
        ua = esp_digest_files(usb_esp_bytes(usb))
        ea = esp_digest_files(open(exp["esp_path"], "rb").read())
    except (OSError, ValueError, struct.error) as e:
        raise BuildError(f"export cross-check 8 failed: the ESPs cannot be read as FAT32 ({e})")
    check_esp_tree(ua, ea)
    checks["8_esp_tree"] = "ok"
    return {"format": "gbsb3-export-check/1", "checked_utc": utc_now(), "usb_image_sha256": scan["sha256"],
            "export_manifest_sha256": exp["manifest_sha256"], "export_created": m.get("created"),
            "esp_sha256": esp_scan["objects"]["esp-internal"]["sha256"], "checks": checks, "guid_overlap": overlap}


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------------------------------------------
# installer tree checks (Windows side)

def check_lf_tree(root, skip_tests):
    bad = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x != "__pycache__" and not (skip_tests and d == root and x == "tests")]
        for f in files:
            if f.endswith(".pyc"):
                continue
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                if b"\r" in fh.read():
                    bad.append(os.path.relpath(p, root))
    return bad


def collect_tree(root, arc_prefix, skip_tests, exec_dirs=()):
    """(arcname, bytes, mode) for every file below root (no tests/, no bytecode)."""
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x != "__pycache__" and not (skip_tests and d == root and x == "tests"))
        for f in sorted(files):
            if f.endswith(".pyc"):
                continue
            p = os.path.join(d, f)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            mode = 0o755 if (f.endswith(".sh") or rel == "init" or rel.split("/")[0] in exec_dirs) else 0o644
            out.append((f"{arc_prefix}/{rel}", open(p, "rb").read(), mode))
    return out


def run_forbidden_scan(path):
    r = subprocess.run([sys.executable, "-I", "-B", os.path.join(HERE, "forbidden_scan.py"), path],
                       capture_output=True, text=True)
    for line in (r.stdout + r.stderr).splitlines():
        log(f"  {line}")
    return r.returncode == 0 and "FORBIDDEN-SCAN OK" in r.stdout


# ----------------------------------------------------------------------------------------------------------------
# payload

def chunk_plan(raw_size, chunk_bytes):
    out, off = [], 0
    while off < raw_size:
        n = min(chunk_bytes, raw_size - off)
        out.append((off, n))
        off += n
    return out


def parse_kv_json(lines, tag):
    for line in reversed(lines):
        if line.startswith(tag + " "):
            return json.loads(line[len(tag) + 1:])
    raise BuildError(f"builder output lacks {tag}")


def build_payload(b, rbuild, sources, scan_objs, cache_dir, chunk_bytes, use_cache, tiny):
    """sources: {obj: (file, offset, raw_size)}. Returns the manifest 'objects' dict (OBJECT_ORDER)."""
    _, lines = b.run(["bash", f"{rbuild}/builder/remote.sh", "cache-status", cache_dir], quiet=True)
    cache, files = {}, {}
    for line in lines:
        if line.startswith("CACHEJSON "):
            try:
                cache = json.loads(line[10:])
            except ValueError:
                cache = {}
        elif line.startswith("F "):
            _, name, size, sha = line.split()
            files[name] = (int(size), sha)
    if cache.get("format") != "gbsb3-payload-cache/1" or cache.get("chunk_raw_bytes") != chunk_bytes \
            or cache.get("level") != ZSTD_LEVEL or cache.get("long") != ZSTD_LONG:
        cache = {}
    cached = cache.get("objects", {}) if use_cache else {}
    objects = {}
    newcache = {"format": "gbsb3-payload-cache/1", "chunk_raw_bytes": chunk_bytes, "level": ZSTD_LEVEL,
                "long": ZSTD_LONG, "objects": {}}
    reused = made = 0
    t_all = time.time()
    for oid in OBJECT_ORDER:
        path, base, raw_size = sources[oid]
        plan = chunk_plan(raw_size, chunk_bytes)
        want = scan_objs[oid]["chunks"] if scan_objs else None
        old = {c["path"]: c for c in cached.get(oid, {}).get("chunks", [])} \
            if cached.get(oid, {}).get("raw_size") == raw_size else {}
        chunks = []
        objh = hashlib.sha256() if tiny else None
        for k, (roff, rsize) in enumerate(plan):
            rel = f"gbsb3/parts/{oid}.{k:03d}.zst"
            name = rel.rsplit("/", 1)[1]
            c = old.get(rel)
            if (c and want and not tiny and c["raw_offset"] == roff and c["raw_size"] == rsize
                    and c["raw_sha256"] == want[k] and files.get(name) == (c["zst_size"], c["zst_sha256"])):
                chunks.append(c)
                reused += 1
                continue
            t0 = time.time()
            rh = hashlib.sha256()

            def producer(write, path=path, start=base + roff, n=rsize, rh=rh):
                with open(path, "rb", buffering=0) as f:
                    f.seek(start)
                    left = n
                    while left:
                        blk = f.read(min(8 * MiB, left))
                        if not blk:
                            raise BuildError(f"short read of {path}")
                        rh.update(blk)
                        if objh is not None:
                            objh.update(blk)
                        write(blk)
                        left -= len(blk)

            out = b.stream_in(["bash", f"{rbuild}/builder/remote.sh", "compress", f"{cache_dir}/parts/{name}"],
                              producer)
            kv = dict(line.split(" ", 1) for line in out if line[:5] in ("SIZE ", "ZSHA ", "RSHA "))
            raw_hex = rh.hexdigest()
            if want and raw_hex != want[k]:
                raise BuildError(f"{name}: raw bytes changed between the scan and the upload")
            if kv.get("RSHA") != raw_hex:
                raise BuildError(f"{name}: end-to-end check failed (builder decompressed {kv.get('RSHA')}, "
                                 f"Windows read {raw_hex})")
            zsize = int(kv["SIZE"])
            if zsize >= GiB:
                raise BuildError(f"{name}: compressed size {zsize} is not < 1 GiB")
            c = {"path": rel, "raw_offset": roff, "raw_size": rsize, "raw_sha256": raw_hex, "zst_size": zsize,
                 "zst_sha256": kv["ZSHA"]}
            chunks.append(c)
            made += 1
            dt = time.time() - t0
            log(f"  {name}: {rsize / MiB:.0f} MiB -> {zsize / MiB:.1f} MiB in {dt:.0f} s "
                f"({rsize / MiB / max(dt, 0.001):.0f} MiB/s)")
            newcache["objects"][oid] = {"raw_size": raw_size, "chunks": chunks}
            b.run(["bash", f"{rbuild}/builder/remote.sh", "put", f"{cache_dir}/cache.json"],
                  stdin_bytes=json_bytes(merge_cache(cache, newcache)), quiet=True)
        sha = objh.hexdigest() if tiny else scan_objs[oid]["sha256"]
        objects[oid] = {"raw_size": raw_size, "sha256": sha, "chunks": chunks}
        newcache["objects"][oid] = {"raw_size": raw_size, "sha256": sha, "chunks": chunks}
    b.run(["bash", f"{rbuild}/builder/remote.sh", "put", f"{cache_dir}/cache.json"], stdin_bytes=json_bytes(newcache),
          quiet=True)
    keep = "".join(c["path"].rsplit("/", 1)[1] + "\n" for o in objects.values() for c in o["chunks"])
    b.run(["bash", f"{rbuild}/builder/remote.sh", "put", f"{rbuild}/chunks.list"], stdin_bytes=keep.encode(),
          quiet=True)
    b.run(["bash", f"{rbuild}/builder/remote.sh", "cache-clean", cache_dir, f"{rbuild}/chunks.list"])
    log(f"payload: {made} chunk(s) compressed, {reused} reused from the cache, {time.time() - t_all:.0f} s")
    return objects


def merge_cache(old, new):
    """cache.json written after each chunk: new objects so far + old entries for objects not yet processed."""
    m = {k: v for k, v in new.items() if k != "objects"}
    m["objects"] = dict(old.get("objects", {}))
    m["objects"].update(new["objects"])
    return m


# ----------------------------------------------------------------------------------------------------------------
# manifest, marker, README

def make_manifest(bid, created, usb, slot, scan_sha, exp, objects, zstd_version, nx_cleared, chunk_bytes, tiny):
    m = exp["manifest"]
    wd_parts = []
    for p in usb["parts"]:
        n = p["name"]
        obj = "esp-usb" if n == "EFI-SYSTEM" else (n if n in SOURCED else None)
        wd_parts.append({"slot": p["slot"], "name": n, "type": p["type"], "guid": p["guid"],
                         "attributes": f"0x{p['attrs']:016x}", "first_lba": p["first"], "last_lba": p["last"],
                         "size": (p["last"] - p["first"] + 1) * SECTOR, "object": obj, "blank": obj is None,
                         "growable": n == "userdata"})
    db_parts = []
    for e in m["partitions"]:
        n = e["name"]
        obj = "esp-internal" if n == "EFI-SYSTEM" else (n if n in SOURCED else None)
        db_parts.append({"name": n, "type": e["type"], "guid": e["guid"].lower(), "attributes": e["attributes"],
                         "size": e["size"], "fill": e["fill"], "min_size": e["min_size"], "blank": e["file"] is None,
                         "object": obj, "file": e["file"], "file_size": e["file_size"], "sha256": e["sha256"]})
    by = {e["name"]: e for e in m["partitions"]}
    man = {
        "format": "gbsb3-iso-payload/1",
        "build_id": bid,
        "created_utc": created,
        "volume_label": LABEL,
        "android_build": ANDROID_BUILD,
        "system_slot": slot,
        "kernel_release": KREL,
        "kernel_nx_compat_cleared": nx_cleared,
        "sector_size": SECTOR,
        "align_bytes": MiB,
        "chunk_raw_bytes": chunk_bytes,
        "compression": {"tool": "zstd", "level": ZSTD_LEVEL, "long": ZSTD_LONG, "version": zstd_version},
        "source": {
            "usb_image": {"file": os.path.basename(usb["path"]), "size": usb["size"], "sha256": scan_sha},
            "dualboot_export": {"manifest_sha256": exp["manifest_sha256"], "created": m.get("created"),
                                "esp_sha256": objects["esp-internal"]["sha256"] if not tiny else by["EFI-SYSTEM"]["sha256"]},
        },
        "objects": {oid: objects[oid] for oid in OBJECT_ORDER},
        "whole_disk": {
            "image_bytes": usb["size"],
            "total_lba": usb["total_lba"],
            "disk_guid": usb["disk_guid"],
            "first_usable_lba": usb["first_usable"],
            "last_usable_lba": usb["last_usable"],
            "entries_lba": usb["entries_lba"],
            "backup_entries_lba": usb["backup_entries_lba"],
            "entry_count": usb["entry_count"],
            "entry_size": usb["entry_size"],
            "entries_crc32": f"0x{usb['entries_crc32']:08x}",
            "gpt_primary": {"path": "gbsb3/wd/gpt-primary.bin", "bytes": len(usb["gpt_primary"]),
                            "sha256": sha256_bytes(usb["gpt_primary"])},
            "gpt_backup": {"path": "gbsb3/wd/gpt-backup.bin", "bytes": len(usb["gpt_backup"]),
                           "sha256": sha256_bytes(usb["gpt_backup"])},
            "super_guid": usb["by_name"]["super"]["guid"],
            "partitions": wd_parts,
        },
        "dual_boot": {
            "export_manifest": "gbsb3/dualboot/manifest.json",
            "export_manifest_sha256": exp["manifest_sha256"],
            "esp_guid": by["EFI-SYSTEM"]["guid"].lower(),
            "super_guid": by["super"]["guid"].lower(),
            "cmdline": m["cmdline"],
            "reserve_bytes": RESERVE,
            "userdata_min_bytes": by["userdata"]["min_size"],
            "boot_entry": {"label": "Googlebook OS", "loader": "\\EFI\\BOOT\\BOOTX64.EFI"},
            "partitions": db_parts,
        },
    }
    if tiny:
        man["test_payload"] = True
    return man


def marker_bytes(bid, manifest_sha):
    return (f"GBSB3-PAYLOAD-MARKER 1\nbuild_id={bid}\nvolume_label={LABEL}\nmanifest=gbsb3/manifest.json\n"
            f"manifest_sha256={manifest_sha}\n").encode("ascii")


def render(template, bid):
    t = open(os.path.join(HERE, "templates", template), "rb").read().decode("utf-8")
    if "\r" in t:
        raise BuildError(f"template {template} has CR bytes")
    return t.replace("@BUILD_ID@", bid).replace("@ANDROID_BUILD@", ANDROID_BUILD).encode("utf-8")


def set_readonly(path, ro):
    os.chmod(path, stat.S_IREAD if ro else (stat.S_IREAD | stat.S_IWRITE))


# ----------------------------------------------------------------------------------------------------------------
# main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None, help=f"default {DEFAULT_OUT}")
    ap.add_argument("--usb-image", default=DEFAULT_USB)
    ap.add_argument("--usb-sha256", default=VERIFIED_USB_SHA256,
                    help="expected sha256 of the USB image (default: the image verified on 2026-10-25)")
    ap.add_argument("--new-export", action="store_true", help="force a fresh dual-boot export (NEW dual-boot GUIDs)")
    ap.add_argument("--no-payload-cache", action="store_true", help="recompress every chunk")
    ap.add_argument("--keep-nx-compat", action="store_true", help="do not clear NX_COMPAT in the ISO's kernel copy")
    ap.add_argument("--keep-work", action="store_true", help="keep /var/tmp/gbiso/<build_id>/ on the builder")
    ap.add_argument("--dry-run", action="store_true", help="read-only checks and plan; builds and writes nothing")
    ap.add_argument("--test-tiny-payload", action="store_true", help="PIPELINE TEST ONLY (see the docstring)")
    ap.add_argument("--installer-dir", default=INSTALLER, help="installer tree (default C:\\GB-x86\\iso\\installer)")
    a = ap.parse_args()
    tiny = a.test_tiny_payload
    if tiny:
        out_iso = a.out or os.path.join(TEST_OUT_DIR, "googlebook-sb3-installer-TEST.iso")
        if os.path.normcase(os.path.abspath(out_iso)) == os.path.normcase(DEFAULT_OUT):
            raise SystemExit("--test-tiny-payload never writes the real ISO path")
    else:
        out_iso = a.out or DEFAULT_OUT
        if os.path.normcase(os.path.abspath(a.installer_dir)) != os.path.normcase(INSTALLER):
            raise SystemExit("--installer-dir is only allowed with --test-tiny-payload")
    out_iso = os.path.abspath(out_iso)
    out_dir = os.path.normcase(os.path.dirname(out_iso))
    top, test_dir = os.path.normcase("D:\\GB-iso"), os.path.normcase("D:\\GB-iso\\test")
    if not (out_dir == top or out_dir.startswith(top + "\\")) or out_dir == test_dir or \
            out_dir.startswith(test_dir + "\\") or not out_iso.lower().endswith(".iso"):
        raise SystemExit("--out must be an .iso below D:\\GB-iso\\ (not in D:\\GB-iso\\test\\, which TEST owns)")
    now = datetime.datetime.now(datetime.timezone.utc)
    bid = now.strftime("%Y%m%d-%H%M%S") + "-" + os.urandom(4).hex()
    created = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    wdir = os.path.join(WORK, bid)
    log.open(os.path.join(wdir, "build.log"))
    log(f"Googlebook SB3 installer ISO build {bid}{' (DRY RUN)' if a.dry_run else ''}"
        f"{' (PIPELINE TEST: tiny payload)' if tiny else ''}")
    log(f"output {out_iso}; installer {a.installer_dir}")
    t_start = time.time()
    try:
        record = build(a, bid, created, wdir, out_iso, tiny)
    except BuildError as e:
        log(f"BUILD FAILED: {e}")
        log.close()
        return 1
    except KeyboardInterrupt:
        log("BUILD INTERRUPTED")
        log.close()
        return 130
    log(f"done in {(time.time() - t_start) / 60:.1f} min")
    if record and not a.dry_run:
        logdst = os.path.join(out_dir, f"build-{bid}.log")
        log(f"log: {logdst}")
        log.close()
        shutil.copyfile(os.path.join(wdir, "build.log"), logdst)
    else:
        log.close()
    return 0


def build(a, bid, created, wdir, out_iso, tiny):
    dry = a.dry_run
    problems = []

    def problem(msg):
        if not dry:
            raise BuildError(msg)
        problems.append(msg)
        log(f"  PROBLEM: {msg}")

    # ---- 1 preflight -------------------------------------------------------------------------------------------
    log.section("1 preflight (read-only)")
    b = Builder()
    log(f"ssh client: {b.exe}")
    _, lines = b.run(["uname", "-r", "&&", "df", "-B1", "--output=avail", "/var/tmp", "|", "tail", "-n", "1",
                      "&&", "test", "-e", f"/boot/vmlinuz-{KREL}", "&&", "sudo", "-n", "true", "&&", "echo", "PING-OK"],
                     quiet=True, check=False)
    if not lines or lines[-1] != "PING-OK":
        raise BuildError(f"builder not reachable or not ready: {lines[-5:]}")
    free_b = int(lines[1])
    log(f"builder: kernel {lines[0]}, /var/tmp free {fmt_gib(free_b)}")
    need_b = 2 * GiB if tiny else 25 * GiB
    if free_b < need_b:
        problem(f"builder / has {fmt_gib(free_b)} free, need >= {fmt_gib(need_b)}")
    drive = os.path.splitdrive(out_iso)[0] + "\\"
    free_d = shutil.disk_usage(drive).free
    need_d = (1 * GiB + 6 * GiB) if tiny else (ISO_ESTIMATE + 6 * GiB + 2 * GiB)
    log(f"{drive} free {fmt_gib(free_d)} (need >= {fmt_gib(need_d)})")
    if free_d < need_d:
        problem(f"{drive} has {fmt_gib(free_d)} free, need >= {fmt_gib(need_d)}")

    usb_path = os.path.abspath(a.usb_image)
    side = usb_path + ".sha256"
    if not os.path.isfile(usb_path) or not os.path.isfile(side):
        raise BuildError(f"USB image or its .sha256 file missing: {usb_path}")
    side_hex = open(side, encoding="ascii").read().split()[0].lower()
    if side_hex != a.usb_sha256.lower():
        raise BuildError(f"{side} says {side_hex}, expected {a.usb_sha256} (verify a rebuilt image with "
                         f"verify_image.py, then pass --usb-sha256)")
    if not run_verify_image([usb_path], "the USB image"):            # DESIGN 13.2
        problem("verify_image.py reports problems on the USB image")
    usb = read_usb_gpt(usb_path)
    log(f"USB image: {usb['size']} bytes, {usb['total_lba']} LBAs, disk GUID {usb['disk_guid']}, GPT ok "
        f"(18 partitions, CRCs, backup at the end)")
    slot = read_usb_esp(usb)
    log(f"USB image system slot: {slot}; ESP boot_part_uuid = super GUID {usb['by_name']['super']['guid']}")

    inst = os.path.abspath(a.installer_dir)
    if not os.path.isdir(inst):
        problem(f"installer tree missing: {inst}")
    else:
        miss = [r for r in INSTALLER_REQUIRED if not os.path.isfile(os.path.join(inst, *r.split("/")))]
        if miss:
            problem(f"installer files missing: {miss}")
        cr = check_lf_tree(inst, skip_tests=True)
        if cr:
            problem(f"installer files with CR bytes: {cr}")
        log("forbidden-token scan (4.0.11):")
        if not run_forbidden_scan(inst):
            problem("forbidden-token scan of the installer found hits (see above)")
    cr = check_lf_tree(os.path.join(HERE, "builder"), False) + check_lf_tree(os.path.join(HERE, "templates"), False)
    if cr:
        raise BuildError(f"build scripts with CR bytes: {cr}")

    # ---- 2 export + scan + cross-checks ------------------------------------------------------------------------
    log.section("2 dual-boot export, USB image scan, cross-checks")
    exp = None if a.new_export else load_export(EXPORT)
    fresh = False
    if exp:
        log(f"reusing the export {EXPORT} (created {exp['manifest'].get('created')})")
    elif dry:
        log("no export to reuse (or --new-export): a fresh build_internal.py export would be made "
            "(not in a dry run)")
    else:
        log("making a fresh export (NEW dual-boot GUIDs)" if a.new_export else "no export yet: making a fresh one")
        exp = run_fresh_export(slot)
        fresh = True

    by_exp = {p["name"]: p for p in exp["manifest"]["partitions"]} if exp else {}
    scan_objs = [{"id": "esp-usb", "start": usb["by_name"]["EFI-SYSTEM"]["first"] * SECTOR,
                  "raw_size": (usb["by_name"]["EFI-SYSTEM"]["last"] - usb["by_name"]["EFI-SYSTEM"]["first"] + 1)
                  * SECTOR}]
    zeros = []
    for n in SOURCED:
        p = usb["by_name"][n]
        psize = (p["last"] - p["first"] + 1) * SECTOR
        rs = by_exp.get(n, {}).get("file_size") or psize
        rs = min(rs, psize)
        scan_objs.append({"id": n, "start": p["first"] * SECTOR, "raw_size": rs})
        if rs < psize:
            zeros.append((p["first"] * SECTOR + rs, (p["last"] + 1) * SECTOR, f"tail:{n}"))
    for p in usb["parts"]:
        if p["name"] not in SOURCED and p["name"] != "EFI-SYSTEM":
            zeros.append((p["first"] * SECTOR, (p["last"] + 1) * SECTOR, f"blank:{p['name']}"))
    zeros.append((34 * SECTOR, usb["parts"][0]["first"] * SECTOR, "gap:head"))
    for p, q in zip(usb["parts"], usb["parts"][1:]):
        if q["first"] > p["last"] + 1:
            zeros.append(((p["last"] + 1) * SECTOR, q["first"] * SECTOR, f"gap:{p['name']}"))
    zeros.append(((usb["parts"][-1]["last"] + 1) * SECTOR, (usb["total_lba"] - 33) * SECTOR, "gap:tail"))
    log(f"scanning the USB image (read-only, one pass): sha256, {len(scan_objs)} objects, {len(zeros)} zero ranges")
    scan = scan_file(usb_path, scan_objs, zeros, CHUNK, needle=ANDROID_BUILD.encode(), label="USB image")
    log(f"USB image sha256 {scan['sha256']}")
    if scan["sha256"] != a.usb_sha256.lower():
        raise BuildError("USB image sha256 differs from the verified value: rebuild/verify it first")
    blank_bad = [z for z in scan["nonzero"] if not z.startswith("tail:")]
    if blank_bad:
        raise BuildError(f"USB image areas that must be zero are not: {blank_bad}")
    log(f"zero checks ok; android build string {ANDROID_BUILD} {'found' if scan['needle'] else 'NOT found'} in the "
        f"image (informational)")

    esp_scan = None
    check_rec = None
    if exp:
        esp_scan = scan_file(exp["esp_path"], [{"id": "esp-internal", "start": 0,
                                                 "raw_size": os.path.getsize(exp["esp_path"])}], [], CHUNK,
                             whole=False, label="export ESP")
        try:
            check_rec = export_checks(exp, usb, slot, scan, esp_scan)
        except BuildError as e:
            if fresh:
                if not a.keep_work:
                    shutil.rmtree(EXPORT_NEW, ignore_errors=True)
                raise
            raise BuildError(f"{e}. The reused export {EXPORT} does not match; rerun with --new-export (NEW "
                             f"dual-boot GUIDs: an install made from an older ISO must then be removed with that ISO "
                             f"or uninstall_dualboot.ps1 -Force)")
        log(f"export cross-checks: {check_rec['checks']}; guid_overlap: {len(check_rec['guid_overlap'])}")
        if not fresh:                                   # a fresh export was verified by build_internal.py itself
            esup = {p["name"]: p for p in exp["manifest"]["partitions"]}.get("super", {}).get("guid", "")
            if not run_verify_image(["--esp", exp["esp_path"], "--boot", "internal", "--super-guid", esup],
                                    "the reused export ESP"):
                problem(f"verify_image.py reports problems on the reused export ESP {exp['esp_path']} (rerun with "
                        f"--new-export: NEW dual-boot GUIDs)")
        if fresh:
            exp = finalize_export(exp)
        if not dry:
            atomic_write(os.path.join(EXPORT, "export_check.json"), json_bytes(check_rec))
    if dry:
        log.section("dry run summary")
        log(f"build id would be {bid}; ISO {out_iso}")
        log(f"objects: " + ", ".join(f"{o['id']}={o['raw_size']}" for o in scan_objs))
        for p in problems:
            log(f"PROBLEM: {p}")
        log("DRY RUN OK" if not problems else f"DRY RUN: {len(problems)} problem(s)")
        return None

    # ---- 3 builder setup + upload ------------------------------------------------------------------------------
    log.section("3 builder setup and upload")
    rbuild = f"{REMOTE}/{bid}"
    members = collect_tree(os.path.join(HERE, "builder"), "builder", False)
    members.append(("builder/forbidden_scan.py", open(os.path.join(HERE, "forbidden_scan.py"), "rb").read(), 0o644))
    members += collect_tree(inst, "installer", True, exec_dirs=("bin",))
    b.put_tar(rbuild, members)
    log(f"uploaded {len(members)} files to {rbuild}/{{builder,installer}}")
    b.run(["bash", f"{rbuild}/builder/remote.sh", "prune-builds", bid])
    _, lines = b.run(["bash", f"{rbuild}/builder/setup.sh"], timeout=1800)
    setup = dict(kv.split("=", 1) for kv in lines[-1].split()[1:]) if lines and lines[-1].startswith("SETUP-OK") \
        else None
    if not setup:
        raise BuildError("builder setup failed")
    tools = {"xorriso": setup["xorriso"], "grub": setup["grub"], "zstd": setup["zstd"], "busybox": setup["busybox"],
             "python": setup["python"]}

    # ---- 4 payload ---------------------------------------------------------------------------------------------
    log.section("4 payload chunks")
    esp_sha = esp_scan["objects"]["esp-internal"]["sha256"]
    if tiny:
        cache_dir = f"{REMOTE}/cache/test-tiny"
        chunk_bytes = TINY_CHUNK
        cap = TINY_RAW
    else:
        cache_dir = f"{REMOTE}/cache/{scan['sha256'][:16]}-{esp_sha[:16]}"
        chunk_bytes = CHUNK
        cap = None
    sources = {"esp-internal": (exp["esp_path"], 0, os.path.getsize(exp["esp_path"]))}
    for o in scan_objs:
        sources[o["id"]] = (usb_path, o["start"], o["raw_size"])
    if cap:
        sources = {k: (p, s, min(n, cap)) for k, (p, s, n) in sources.items()}
    all_scan = dict(scan["objects"], **esp_scan["objects"])
    objects = build_payload(b, rbuild, sources, None if tiny else all_scan, cache_dir, chunk_bytes,
                            use_cache=not a.no_payload_cache, tiny=tiny)
    if not tiny:
        for oid in OBJECT_ORDER:
            if objects[oid]["sha256"] != all_scan[oid]["sha256"] or objects[oid]["raw_size"] != all_scan[oid]["raw_size"]:
                raise BuildError(f"object {oid}: payload and scan disagree")

    # ---- 5 small payload files ---------------------------------------------------------------------------------
    log.section("5 manifest, marker, small files")
    man = make_manifest(bid, created, usb, slot, scan["sha256"], exp, objects, tools["zstd"],
                        not a.keep_nx_compat, chunk_bytes, tiny)
    mbytes = json_bytes(man)
    msha = sha256_bytes(mbytes)
    marker_name = f"GBSB3-{bid}.marker"
    small = [
        ("iso/gbsb3/wd/gpt-primary.bin", usb["gpt_primary"], 0o644),
        ("iso/gbsb3/wd/gpt-backup.bin", usb["gpt_backup"], 0o644),
        ("iso/gbsb3/dualboot/manifest.json", exp["manifest_bytes"], 0o644),
        ("iso/gbsb3/manifest.json", mbytes, 0o644),
        (f"iso/gbsb3/{marker_name}", marker_bytes(bid, msha), 0o644),
        ("iso/README.txt", render("README.txt.in", bid), 0o644),
        ("iso/boot/grub/grub.cfg", render("grub.cfg.in", bid), 0o644),
        ("gen/early.cfg", render("early.cfg.in", bid), 0o644),
    ]
    stage_local = os.path.join(wdir, "stage")
    for name, data, _ in small:
        atomic_write(os.path.join(stage_local, *name.split("/")), data)
    b.put_tar(rbuild, small)
    b.run(["bash", f"{rbuild}/builder/remote.sh", "link-chunks", cache_dir, bid, f"{rbuild}/chunks.list"])
    log(f"manifest sha256 {msha}; marker gbsb3/{marker_name}")

    # ---- 6 builder: kernel, initramfs, GRUB, ISO ---------------------------------------------------------------
    log.section("6 kernel, initramfs, GRUB, ISO (builder)")
    _, lines = b.run(["bash", f"{rbuild}/builder/remote.sh", "kernel", bid, "keep" if a.keep_nx_compat else "clear"])
    kern = parse_kv_json(lines, "KERNEL-JSON")
    if not a.keep_nx_compat and int(kern["after"], 16) & 0x0100:
        raise BuildError("kernel NX_COMPAT still set after clearing")
    # WAVE3 item 1: the installer kernel registers its 8250 driver (and so the Surface Aggregator UART) without a
    # cmdline token only with CONFIG_SERIAL_8250_RUNTIME_UARTS >= 1 (remote.sh already refuses 0; T0 re-checks)
    if not isinstance(kern.get("runtime_uarts"), int) or kern["runtime_uarts"] < 1:
        raise BuildError(f"installer kernel CONFIG_SERIAL_8250_RUNTIME_UARTS={kern.get('runtime_uarts')!r}: needs >= 1")
    _, lines = b.run(["bash", f"{rbuild}/builder/mk_initramfs.sh", rbuild, f"{rbuild}/installer", bid, ANDROID_BUILD,
                      KREL], timeout=3600)
    initrd = parse_kv_json(lines, "INITRD-JSON")
    _, lines = b.run(["bash", f"{rbuild}/builder/mk_grub.sh", f"{rbuild}/grub", f"{rbuild}/gen/early.cfg",
                      f"{rbuild}/iso/boot/grub/grub.cfg", bid])
    grub = parse_kv_json(lines, "GRUB-JSON")
    if int(grub["grub_efi_dllcharacteristics"], 16) & 0x0100:
        raise BuildError("BOOTX64.EFI advertises NX_COMPAT")
    b.run(["bash", f"{rbuild}/builder/remote.sh", "assemble", bid])
    _, lines = b.run(["bash", f"{rbuild}/builder/mk_iso.sh", rbuild, bid], timeout=7200)
    iso = parse_kv_json(lines, "ISO-JSON")
    if iso["iso_size"] > ISO_CEILING:
        raise BuildError("ISO exceeds 8.0 GiB")

    # ---- 7 transfer + records ----------------------------------------------------------------------------------
    log.section("7 transfer and records")
    out_dir = os.path.dirname(out_iso)
    os.makedirs(out_dir, exist_ok=True)
    free_d = shutil.disk_usage(out_dir).free
    if free_d - iso["iso_size"] < 6 * GiB + (0 if tiny else 2 * GiB):
        raise BuildError(f"{out_dir} has {fmt_gib(free_d)} free: not enough for the ISO and the 6 GiB reserve")
    # A tiny-payload ISO carrying the REAL installer is not installable (truncated objects) but would look usable:
    # it stays on the builder (deleted at cleanup unless --keep-work); only the stub variant is copied to Windows.
    keep_on_builder = tiny and os.path.normcase(inst) == os.path.normcase(INSTALLER)
    base = os.path.splitext(out_iso)[0]
    if keep_on_builder:
        log(f"pipeline test with the real installer: the ISO stays on the builder ({rbuild}/out/{ISO_NAME})")
        for stale in (out_iso, out_iso + ".sha256"):
            if os.path.exists(stale):
                set_readonly(stale, False)
                os.remove(stale)
    else:
        part = out_iso + ".part"
        if os.path.exists(part):
            set_readonly(part, False)
            os.remove(part)
        got = b.fetch(f"{rbuild}/out/{ISO_NAME}", part, iso["iso_size"])   # sha256 re-read from the written file
        if got != iso["iso_sha256"]:
            raise BuildError(f"transferred ISO sha256 {got} differs from the builder's {iso['iso_sha256']}")
        if os.path.exists(out_iso):
            set_readonly(out_iso, False)
        os.replace(part, out_iso)
        set_readonly(out_iso, True)
        atomic_write(out_iso + ".sha256", f"{iso['iso_sha256']}  {os.path.basename(out_iso)}\n".encode("ascii"))
    atomic_write(base + ".manifest.json", mbytes)
    record = {
        "format": "gbsb3-iso-build/1", "build_id": bid, "created_utc": created,
        "iso": None if keep_on_builder else out_iso, "iso_size": iso["iso_size"], "iso_sha256": iso["iso_sha256"],
        "payload_manifest": base + ".manifest.json",
        "usb_image": {"path": usb_path, "size": usb["size"], "sha256": scan["sha256"]},
        "export_dir": EXPORT, "export_manifest_sha256": exp["manifest_sha256"],
        "builder_iso": f"{REMOTE}/last/{ISO_NAME}" if not tiny else None,
        "kernel_release": KREL, "kernel_dllcharacteristics": {"before": kern["before"], "after": kern["after"]},
        "kernel_8250_runtime_uarts": kern["runtime_uarts"],
        "grub_efi_sha256": grub["grub_efi_sha256"], "grub_efi_dllcharacteristics": grub["grub_efi_dllcharacteristics"],
        "efiboot_img_bytes": grub["efiboot_img_bytes"],
        "initrd_sha256": initrd["initrd_sha256"], "initrd_size": initrd["initrd_size"],
        "tools": tools,
    }
    if tiny:
        record["test_payload"] = True
    atomic_write(base + ".build.json", json_bytes(record))
    log(f"ISO {'(builder only)' if keep_on_builder else out_iso} ({fmt_gib(iso['iso_size'])}) "
        f"sha256 {iso['iso_sha256']}")

    # ---- 8 builder cleanup -------------------------------------------------------------------------------------
    log.section("8 builder cleanup")
    if not tiny:
        b.run(["bash", f"{rbuild}/builder/remote.sh", "prune-cache", cache_dir])
    b.run(["bash", f"{rbuild}/builder/remote.sh", "finish", bid, "test" if tiny else "real",
           "keep" if a.keep_work else "delete"])
    log("BUILD OK")
    return record


if __name__ == "__main__":
    sys.exit(main())
