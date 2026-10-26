"""T0 static ISO checks, builder side (DESIGN.md 7.2 T0, 2.1, 2.2, 2.4, 2.5, 3.1-3.3). Read-only on the ISO.

  python3 -I /var/tmp/gbiso/test/t0_static.py <iso> <build_id>

The ISO is examined as a file (PVD, El Torito catalog, GPT, appended ESP via mtools @@offset) and through a read-only
loop mount (losetup -r + mount -o ro below /var/tmp/gbiso/test/mnt/t0); the initrd is unpacked into
/var/tmp/gbiso/test/t0/initrd. The last stdout line is "T0-RESULT <json>" with {"checks": [[ok, text]...], "facts":
{...}}; the Windows side compares the facts with the USB image, the export manifest and the build record.
"""
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import uuid
import zlib

T = "/var/tmp/gbiso/test"
MNT = T + "/mnt/t0"
KREL = "6.8.0-147-generic"
KCONFIG = "/boot/config-" + KREL     # the config of the kernel BUILD copied (remote.sh kernel: /boot/vmlinuz-$KREL)
MiB = 1 << 20
TYPE_ESP = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
GRUB_ENTRIES = [("gb_normal", "n"), ("gb_basic", "b"), ("gb_serial", "s"), ("gb_serial_nocd", "d"),
                ("gb_serial_ntfs", "t"), ("gb_fwsetup", "f"), ("gb_reboot", "r"), ("gb_halt", "p")]
TEST_IDS = ("gb_serial", "gb_serial_nocd", "gb_serial_ntfs")
# == build/builder/stage_initramfs.py MODULE_DENY (DESIGN 13.1; tests\w2b\test_iso_static.py asserts the equality)
DENY = ("spi_intel*", "mtd*", "efi_test", "efi_pstore", "tpm*", "nouveau", "nvidia*", "drm_gpuvm", "drm_exec",
        "gpu_sched", "drm_ttm_helper", "mxm_wmi", "i2c_nvidia_gpu", "nova_core", "nova_drm", "snd_hda_intel")
FONTS = ("usr/share/consolefonts/gb-ter-32x64.psf", "usr/share/consolefonts/Lat15-TerminusBold32x16.psf.gz",
         "usr/share/consolefonts/Lat15-Terminus16.psf.gz")
checks, facts = [], {}


def check(ok, text):
    checks.append([bool(ok), text])
    print(("PASS " if ok else "FAIL ") + text, flush=True)
    return bool(ok)


def sh(argv, **kw):
    return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(8 * MiB), b""):
            h.update(b)
    return h.hexdigest()


def pe_dllchar(data):
    """DllCharacteristics of a PE32+ image (offset e_lfanew + 24 + 70), or None."""
    if data[:2] != b"MZ":
        return None
    e = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e:e + 4] != b"PE\0\0" or struct.unpack_from("<H", data, e + 24)[0] != 0x20B:
        return None
    return struct.unpack_from("<H", data, e + 24 + 70)[0]


# ------------------------------------------------------------------------------------------------ raw ISO checks
def eltorito(f):
    """[(platform, media, sector_count, load_rba)] from the boot catalog, or None."""
    f.seek(17 * 2048)
    br = f.read(2048)
    if br[0] != 0 or br[1:6] != b"CD001" or not br[7:30].startswith(b"EL TORITO SPECIFICATION"):
        return None
    cat = struct.unpack_from("<I", br, 0x47)[0]
    f.seek(cat * 2048)
    c = f.read(2048)
    if c[0] != 0x01 or c[30:32] != b"\x55\xAA":
        return None
    out = []
    plat = c[1]
    d = c[32:64]
    if d[0] in (0x88, 0x00) and any(d):
        out.append((plat, d[1] & 0x0F, struct.unpack_from("<H", d, 6)[0], struct.unpack_from("<I", d, 8)[0],
                    d[0] == 0x88))
    pos = 64
    while pos + 32 <= len(c):
        h = c[pos:pos + 32]
        if h[0] not in (0x90, 0x91):
            break
        plat, n = h[1], struct.unpack_from("<H", h, 2)[0]
        pos += 32
        for _ in range(n):
            e = c[pos:pos + 32]
            out.append((plat, e[1] & 0x0F, struct.unpack_from("<H", e, 6)[0], struct.unpack_from("<I", e, 8)[0],
                        e[0] == 0x88))
            pos += 32
        if h[0] == 0x91:
            break
    return out


def read_gpt512(f, size):
    f.seek(0)
    mbr = f.read(512)
    f.seek(512)
    h = f.read(512)
    if h[:8] != b"EFI PART":
        return None, ["no GPT header at LBA 1"]
    probs = []
    hsz, hcrc = struct.unpack_from("<II", h, 12)
    t = bytearray(h[:hsz]); t[16:20] = b"\0\0\0\0"
    if zlib.crc32(bytes(t)) != hcrc:
        probs.append("primary header CRC")
    my, alt, first, last = struct.unpack_from("<QQQQ", h, 24)
    elba, n, esz, ecrc = struct.unpack_from("<QIII", h, 72)
    f.seek(elba * 512)
    raw = f.read(n * esz)
    if zlib.crc32(raw) != ecrc:
        probs.append("entries CRC")
    if alt != size // 512 - 1:
        probs.append("backup header not at the last LBA (%d)" % alt)
    if mbr[510:512] != b"\x55\xAA" or 0xEE not in (mbr[446 + 4], mbr[462 + 4], mbr[478 + 4], mbr[494 + 4]):
        probs.append("protective MBR")
    parts = []
    for i in range(n):
        e = raw[i * esz:(i + 1) * esz]
        if e[:16] == bytes(16):
            continue
        a, b = struct.unpack_from("<QQ", e, 32)
        parts.append(dict(num=i + 1, type=str(uuid.UUID(bytes_le=bytes(e[:16]))), first=a, last=b))
    return parts, probs


def raw_checks(iso, build_id):
    size = os.path.getsize(iso)
    facts["iso_size"] = size
    check(size % 2048 == 0, "ISO size %d is a multiple of 2048" % size)
    with open(iso, "rb") as f:
        f.seek(32768)
        pvd = f.read(2048)
        vid = pvd[40:72].decode("ascii", "replace").strip()
        check(pvd[0] == 1 and pvd[1:6] == b"CD001" and vid == "GBSB3INST", "PVD volume ID %r == GBSB3INST" % vid)
        ents = eltorito(f) or []
        facts["eltorito"] = ents
        efi = [e for e in ents if e[0] == 0xEF and e[1] == 0 and e[4]]
        check(len(efi) == 1, "El Torito: exactly one bootable platform-0xEF no-emulation entry (%s)" % ents)
        check(len(ents) == 1, "El Torito: no other boot entry (no BIOS boot)")
        parts, probs = read_gpt512(f, size)
        check(parts is not None and not probs, "ISO GPT: protective MBR, header/entries CRC, backup at the end %s"
              % (probs or ""))
        parts = parts or []
        p1 = [p for p in parts if p["num"] == 1]
        p2 = [p for p in parts if p["num"] == 2]
        check(bool(p1) and p1[0]["first"] == 64, "GPT partition 1 starts at LBA 64 (%s)" % (p1[0] if p1 else None))
        if p1:
            f.seek(p1[0]["first"] * 512 + 32768)
            pv = f.read(2048)
            check(pv[1:6] == b"CD001" and pv[40:72].decode("ascii", "replace").strip() == "GBSB3INST",
                  "partition 1 holds the ISO9660 volume GBSB3INST")
        ok2 = bool(p2) and p2[0]["type"] == TYPE_ESP
        check(ok2, "GPT partition 2 is type EFI System")
        if ok2:
            esp_bytes = (p2[0]["last"] - p2[0]["first"] + 1) * 512
            facts["efiboot_img_bytes"] = esp_bytes
            facts["esp_offset"] = p2[0]["first"] * 512
            check(esp_bytes < 32 * MiB, "efiboot.img (partition 2) %d bytes < 32 MiB" % esp_bytes)
            if len(efi) == 1:
                check(efi[0][2] == esp_bytes // 512, "El Torito Ldsiz %d == efiboot.img bytes / 512 (%d)"
                      % (efi[0][2], esp_bytes // 512))
                check(efi[0][3] * 4 == p2[0]["first"], "El Torito image = appended partition 2 (RBA %d)" % efi[0][3])
    p = sh(["sgdisk", "-v", iso])
    check(p.returncode == 0 and b"No problems found" in p.stdout, "sgdisk -v reports no problems")
    rep = sh(["xorriso", "-indev", iso, "-report_el_torito", "plain", "-report_system_area", "plain"])
    facts["xorriso_report"] = rep.stdout.decode(errors="replace")[-6000:]
    if "esp_offset" in facts:
        env = dict(os.environ, MTOOLS_SKIP_CHECK="1")
        m = sh(["mtype", "-i", "%s@@%d" % (iso, facts["esp_offset"]), "::/EFI/BOOT/BOOTX64.EFI"], env=env)
        ok = m.returncode == 0 and m.stdout[:2] == b"MZ"
        check(ok, "the appended ESP contains /EFI/BOOT/BOOTX64.EFI")
        facts["esp_bootx64_sha256"] = hashlib.sha256(m.stdout).hexdigest() if ok else None


# ------------------------------------------------------------------------------------------------ mounted tree
def attach_ro(iso):
    real = os.path.realpath(iso)
    j = sh(["losetup", "-j", real]).stdout.decode().strip()
    if j:
        raise SystemExit("the ISO is attached already: " + j)
    p = sh(["sudo", "-n", "losetup", "-r", "-f", "--show", real])
    dev = p.stdout.decode().strip()
    if p.returncode or not re.match(r"^/dev/loop\d+$", dev):
        raise SystemExit("losetup failed: " + dev)
    back = open("/sys/block/%s/loop/backing_file" % os.path.basename(dev)).read().strip()
    ro = open("/sys/block/%s/ro" % os.path.basename(dev)).read().strip()
    if back != real or ro != "1":
        sh(["sudo", "-n", "losetup", "-d", dev])
        raise SystemExit("loop device check failed")
    os.makedirs(MNT, exist_ok=True)
    if sh(["sudo", "-n", "mount", "-t", "iso9660", "-o", "ro", dev, MNT]).returncode:
        sh(["sudo", "-n", "losetup", "-d", dev])
        raise SystemExit("mount failed")
    return dev


def detach(dev):
    sh(["sudo", "-n", "umount", MNT])
    sh(["sudo", "-n", "losetup", "-d", dev])


def tree_checks(build_id):
    r = MNT
    big = []
    files = []
    for dp, dn, fn in os.walk(r):
        for n in fn:
            p = os.path.join(dp, n)
            files.append(os.path.relpath(p, r))
            if os.path.getsize(p) >= 1 << 30:
                big.append(os.path.relpath(p, r))
    check(not big, "every file < 1 GiB %s" % (big or ""))
    marker = "gbsb3/GBSB3-%s.marker" % build_id
    msha = sha256_file(os.path.join(r, "gbsb3/manifest.json"))
    facts["manifest_sha256"] = msha
    try:
        mk = open(os.path.join(r, marker), "rb").read()
    except OSError:
        mk = b""
    want = ("GBSB3-PAYLOAD-MARKER 1\nbuild_id=%s\nvolume_label=GBSB3INST\nmanifest=gbsb3/manifest.json\n"
            "manifest_sha256=%s\n" % (build_id, msha)).encode()
    check(mk == want, "marker %s has exactly the 1.2 lines and the manifest's sha256" % marker)
    shas = {}
    sums = open(os.path.join(r, "gbsb3/SHA256SUMS"), encoding="utf-8").read().splitlines()
    listed, bad = set(), []
    for ln in sums:
        m = re.match(r"^([0-9a-f]{64}) [ *](.+)$", ln)
        if not m:
            bad.append("bad line " + ln[:60])
            continue
        rel = m.group(2).lstrip("./")
        listed.add(rel)
        got = sha256_file(os.path.join(r, rel)) if os.path.isfile(os.path.join(r, rel)) else None
        shas[rel] = got
        if got != m.group(1):
            bad.append(rel)
    check(not bad, "gbsb3/SHA256SUMS verifies (%d files) %s" % (len(listed), bad[:5] or ""))
    norm = set(f.replace(os.sep, "/") for f in files)
    unlisted = sorted(norm - listed - {"gbsb3/SHA256SUMS", "boot/boot.cat"})
    check(not unlisted, "SHA256SUMS covers every file except itself and boot.cat %s" % (unlisted[:5] or ""))
    man = json.load(open(os.path.join(r, "gbsb3/manifest.json")))
    check(man.get("build_id") == build_id and man.get("format") == "gbsb3-iso-payload/1",
          "manifest format/build_id (%s)" % man.get("build_id"))
    chunk_problems = []
    for oid, obj in man["objects"].items():
        oh = hashlib.sha256()
        total, expect_off = 0, 0
        for ch in obj["chunks"]:
            p = os.path.join(r, ch["path"])
            zs = shas.get(ch["path"]) or sha256_file(p)
            if zs != ch["zst_sha256"] or os.path.getsize(p) != ch["zst_size"]:
                chunk_problems.append("%s zst sha/size" % ch["path"])
            if ch["raw_offset"] != expect_off:
                chunk_problems.append("%s raw_offset %d != %d" % (ch["path"], ch["raw_offset"], expect_off))
            ch_h = hashlib.sha256()
            n = 0
            z = subprocess.Popen(["zstd", "-dcq", "--long=27", p], stdout=subprocess.PIPE)
            for b in iter(lambda: z.stdout.read(8 * MiB), b""):
                ch_h.update(b)
                oh.update(b)
                n += len(b)
            if z.wait() != 0:
                chunk_problems.append("%s zstd exit status" % ch["path"])
            if n != ch["raw_size"] or ch_h.hexdigest() != ch["raw_sha256"]:
                chunk_problems.append("%s raw sha/size" % ch["path"])
            total += n
            expect_off += ch["raw_size"]
        if total != obj["raw_size"] or oh.hexdigest() != obj["sha256"]:
            chunk_problems.append("object %s sha256/raw_size" % oid)
        print("  object %s: %d chunks checked" % (oid, len(obj["chunks"])), flush=True)
    check(not chunk_problems, "every chunk: zst_sha256, decompressed raw_sha256, object sha256 %s"
          % (chunk_problems[:5] or ""))
    wd = man["whole_disk"]
    for k, fn in (("gpt_primary", "gbsb3/wd/gpt-primary.bin"), ("gpt_backup", "gbsb3/wd/gpt-backup.bin")):
        s = sha256_file(os.path.join(r, fn))
        facts[k + "_sha256"] = s
        check(s == wd[k]["sha256"] and os.path.getsize(os.path.join(r, fn)) == wd[k]["bytes"],
              "%s matches the manifest (sha256, %d bytes)" % (fn, wd[k]["bytes"]))
    facts["dualboot_manifest_sha256"] = sha256_file(os.path.join(r, "gbsb3/dualboot/manifest.json"))
    check(facts["dualboot_manifest_sha256"] == man["dual_boot"]["export_manifest_sha256"],
          "gbsb3/dualboot/manifest.json sha256 == dual_boot.export_manifest_sha256")
    # kernel and GRUB
    vml = open(os.path.join(r, "boot/vmlinuz"), "rb").read()
    dc = pe_dllchar(vml)
    facts["vmlinuz_dllcharacteristics"] = "0x%04x" % dc if dc is not None else None
    check(dc is not None and not dc & 0x0100, "/boot/vmlinuz DllCharacteristics %s has 0x0100 clear"
          % facts["vmlinuz_dllcharacteristics"])
    kconfig_checks(vml)
    efi = open(os.path.join(r, "EFI/BOOT/BOOTX64.EFI"), "rb").read()
    dc = pe_dllchar(efi)
    facts["bootx64_dllcharacteristics"] = "0x%04x" % dc if dc is not None else None
    facts["bootx64_sha256"] = hashlib.sha256(efi).hexdigest()
    check(dc is not None and not dc & 0x0100, "BOOTX64.EFI DllCharacteristics %s has 0x0100 clear"
          % facts["bootx64_dllcharacteristics"])
    check(("/gbsb3/GBSB3-%s.marker" % build_id).encode() in efi, "BOOTX64.EFI contains the marker path")
    for s in (b"tpm.mod", b"shim_lock.mod", b"gfxterm.mod"):
        check(s not in efi, "BOOTX64.EFI does not contain %s" % s.decode())
    if facts.get("esp_bootx64_sha256"):
        check(facts["esp_bootx64_sha256"] == facts["bootx64_sha256"], "ESP image BOOTX64.EFI == ISO tree copy")
    grub_checks(open(os.path.join(r, "boot/grub/grub.cfg"), encoding="utf-8").read(), build_id)
    initrd_checks(os.path.join(r, "boot/initrd.img"))


def kconfig_checks(vml, config_path=KCONFIG):
    """WAVE3 item 1 (TA-7): the ISO kernel is KREL (the setup-header version string of its /boot/vmlinuz) and that
    KREL's config on the builder has CONFIG_SERIAL_8250_RUNTIME_UARTS >= 1, so the installer's 8250 driver (and the
    Surface Aggregator UART behind 8250_dw) registers without a cmdline token. Our native GKI has 0 there and needs
    8250.nr_uarts (build_usb.native_cmdline); grub.cfg's gb_args do not, as long as this holds."""
    ver = None
    if len(vml) > 0x210 and vml[0x202:0x206] == b"HdrS":
        off = struct.unpack_from("<H", vml, 0x20E)[0] + 0x200
        ver = vml[off:off + 256].split(b"\0")[0].split(b" ")[0].decode("ascii", "replace")
    facts["installer_kernel_version"] = ver
    check(ver == KREL, "/boot/vmlinuz setup-header version %r == KREL %s" % (ver, KREL))
    ru = None
    try:
        with open(config_path, encoding="ascii", errors="replace") as f:
            for ln in f:
                if ln.startswith("CONFIG_SERIAL_8250_RUNTIME_UARTS="):
                    ru = ln.split("=", 1)[1].strip()
    except OSError as e:
        ru = "unreadable (%s)" % e.__class__.__name__
    facts["installer_8250_runtime_uarts"] = int(ru) if ru and ru.isdigit() else ru
    check(bool(ru) and ru.isdigit() and int(ru) >= 1,
          "%s: CONFIG_SERIAL_8250_RUNTIME_UARTS=%s >= 1 (no 8250.nr_uarts needed)" % (config_path, ru))


def grub_checks(cfg, build_id):
    ents = re.findall(r'^\s*menuentry\s+"([^"]*)"\s+--id\s+(\S+)\s+--hotkey=(\S)', cfg, re.M)
    facts["grub_entries"] = ents
    check([(i, k) for _, i, k in ents] == GRUB_ENTRIES, "grub.cfg has the 8 entries/ids/hotkeys of 2.2 (%s)"
          % [(i, k) for _, i, k in ents])
    check(re.search(r"^set gb_build=%s\s*$" % re.escape(build_id), cfg, re.M) is not None, "gb_build = build id")
    m = re.search(r'^set gb_args="([^"]*)"', cfg, re.M)
    args = m.group(1) if m else ""
    check("initcall_blacklist=init_tis,crb_acpi_driver_init" in args.split(), "gb_args has the initcall blacklist")
    check("iommu.passthrough=1" in args.split(), "gb_args has iommu.passthrough=1")
    check("fbcon=" not in cfg, "no fbcon= in grub.cfg")
    blocks = re.findall(r'menuentry\s+"[^"]*"\s+--id\s+(\S+)[^{]*\{(.*?)\n\s*\}', cfg, re.S)
    for bid, body in blocks:
        if bid in TEST_IDS:
            lin = [ln for ln in body.splitlines() if ln.strip().startswith("linux ")]
            cons = [w for w in (lin[0].split() if lin else []) if w.startswith("console=")]
            check(bool(cons) and cons[-1] == "console=tty0" and "console=ttyS0,115200n8" in cons,
                  "%s: console list %s ends with console=tty0" % (bid, cons))


def resolve(root, rel):
    """Resolve rel inside root, following symlinks with absolute targets taken relative to root."""
    parts = [p for p in rel.split("/") if p]
    cur = root
    depth = 0
    while parts:
        p = parts.pop(0)
        nxt = os.path.join(cur, p)
        if os.path.islink(nxt):
            depth += 1
            if depth > 40:
                return None
            t = os.readlink(nxt)
            if t.startswith("/"):
                cur = root
                parts = [x for x in t.split("/") if x] + parts
            else:
                parts = [x for x in t.split("/") if x] + parts
            continue
        cur = nxt
    return cur


def initrd_checks(initrd):
    d = T + "/t0/initrd"
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)
    z = subprocess.Popen(["zstd", "-dcq", initrd], stdout=subprocess.PIPE)
    c = subprocess.run(["cpio", "-idm", "--quiet", "--no-absolute-filenames"], stdin=z.stdout, cwd=d,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    z.wait()
    check(z.returncode == 0, "initrd decompresses (zstd)")
    facts["cpio_messages"] = c.stdout.decode(errors="replace")[-2000:]
    for n in ("bin", "sbin"):
        st = os.lstat(os.path.join(d, n)) if os.path.lexists(os.path.join(d, n)) else None
        check(st is not None and stat.S_ISDIR(st.st_mode), "/%s is a real directory" % n)
    bb = os.path.join(d, "bin/busybox")
    check(os.path.lexists(bb) and stat.S_ISREG(os.lstat(bb).st_mode), "/bin/busybox is a regular file")
    for n in ("usr/bin/ntfsls", "usr/bin/ntfscat") + FONTS + ("lib/modules/%s/modules.builtin" % KREL,
                                                            "init", "opt/gb/lib/gbinstall.py", "etc/gb/build.env"):
        p = resolve(d, n)
        check(p is not None and os.path.isfile(p), "/%s exists in the initrd" % n)
    moddir = resolve(d, "lib/modules/" + KREL)
    facts["module_deny"] = list(DENY)
    denied = []
    for dp, dn, fn in os.walk(moddir or d):
        for n in fn:
            if n.endswith(".ko"):
                base = n[:-3].replace("-", "_")
                if any(fnmatch.fnmatch(base, pat) for pat in DENY):
                    denied.append(n)
    check(moddir is not None and not denied, "no denylisted .ko in the initrd %s" % (denied or ""))
    env = resolve(d, "etc/gb/build.env")
    if env and os.path.isfile(env):
        facts["build_env"] = open(env, encoding="utf-8", errors="replace").read()
    fs = subprocess.run([sys.executable, "-I", T + "/forbidden_scan.py", d, "--layout", "initrd"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = fs.stdout.decode(errors="replace")
    facts["forbidden_scan"] = out[-3000:]
    check(fs.returncode == 0 and "FORBIDDEN-SCAN OK" in out, "forbidden_scan.py --layout initrd: FORBIDDEN-SCAN OK")


def main():
    iso, build_id = sys.argv[1], sys.argv[2]
    if not re.match(r"^\d{8}-\d{6}-[0-9a-f]{8}$", build_id):
        raise SystemExit("bad build id")
    facts["build_id"] = build_id
    facts["iso_sha256"] = sha256_file(iso)
    raw_checks(iso, build_id)
    dev = attach_ro(iso)
    try:
        tree_checks(build_id)
    finally:
        detach(dev)
    print("T0-RESULT " + json.dumps({"checks": checks, "facts": facts}))


if __name__ == "__main__":
    main()
