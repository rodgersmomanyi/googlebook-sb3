"""Build a raw, flashable GPT disk image that boots Googlebook OS natively on the Surface Book 3 (USB flash drive),
and export the same partitions for the internal-disk (dual boot) installer.

Partitions come from the pristine lapis disk image (super with system/vendor/..., the A/B boot/init_boot/vendor_boot
and vbmeta images). userdata, metadata, misc and the security/FRP partitions are created BLANK: nothing from the VM's
/data (accounts, tokens, apps) is copied. userdata and metadata are 'formattable' in the fstab, so first boot formats
them. The ESP holds systemd-boot, the GKI kernel and the initrd built by build_esp.py, with gb.hw=native boot entries
whose androidboot.boot_devices name the Surface Book 3's two xHCI controllers, so Android only looks at the USB stick.
Native images also carry the NVIDIA test entry gb-<slot>-nvtest and the dGPU payload gb/dgpu-<krel>.erofs.

Usage: python -I build_usb.py [--out FILE] [--userdata-gib N] [--esp-mib 512] [--export DIR] [--sparse]
                              [--vm-test [--vm-test-gfx | --vm-test-kat | --vm-test-rehearsal | --vm-test-nogk |
                                          --vm-test-w5 [--vnni-off] | --vm-test-otagate]]
                              [--esp-only FILE [--super-guid G] [--bringup-test DIR]]
  The image is written to <out>.part (sha256 computed while writing), checked with verify_image.py, and only then
  renamed to <out>, with <out>.sha256 = "<hex>  <name>". A failed check keeps the .part file and writes no sidecar.
  --sparse      NTFS sparse file: all-zero 1 MiB blocks are not allocated (same content and sha256)
  --allow-running-vm  (W6 prep, IA-1) build the image although the user's VM (lapis-boot.vmx) runs: lapis.bin is then
                read while the guest writes it (refused by default); verify_image's VC compare still has to pass
  --esp-only F  build only the ESP (the same code path as the image), write it to F, check it with
                verify_image.py --esp, exit (checkpoints; --super-guid fixes the GUID for determinism checks)
  --vm-test-gfx test-only: the VM tree plus GbGpu and the gfx driver package (build_esp.vm_test_gfx_cpio); the output
                must be below D:\\gb-w2test\\
  --vm-test-kat test-only (WAVE3_PLAN 6.4): the VM tree plus the VNNI known-answer test (inject\\kat\\vnni_kat,
                build_esp.vm_test_kat_cpio; since W4 it waits for the VM tree's own mmd zram swap); the output must be
                below D:\\gb-w3test\\
  --vm-test-rehearsal  test-only (W4, dgpu\\w4\\VM_UPGRADE.md 2.3): EXACTLY the VM tree that GB_WRITE_LAPIS=1
                build_esp.py writes into lapis.bin (build_esp.INJECT_CPIO: Gatekeeper, zram, fsverity), for the
                throwaway rehearsal VM; also writes <output>.vmtree.sha256 (the gb/inject.gz sha256 the user-VM preview
                must match: verify_image --expect-inject-sha256); the output must be below D:\\gb-w4test\\
  --vm-test-nogk  test-only (W4 rehearsal, GATEKEEPER.md 9.5): that VM tree WITHOUT the Gatekeeper (binary, VINTF
                fragment, rc block; build_esp.vm_test_nogk_cpio), the stand-in for a pre-W4 ESP in the fix-forward and
                rollback tests on the throwaway rehearsal disk; the output must be below D:\\gb-w4test\\
  --vm-test-w5  test-only (W5, the VNNI libz patch measurement): EXACTLY the VM tree GB_WRITE_LAPIS=1 build_esp.py
                writes into lapis.bin (build_esp.INJECT_CPIO: Gatekeeper, zram, fsverity, the fingerprint-gated libz
                binds); also writes <output>.vmtree.sha256 (the W5 VM-tree record the user-VM preview must match);
                the output must be below D:\\gb-w5test\\
  --vnni-off    with --vm-test-w5 only: the same tree with gb.vnni=0 on the entry (gbinit deletes /odm/vnni: no bind,
                stock libz under emulation) and the title "(VM test, W5, VNNI off)": the measurement baseline
  --vm-test-otagate  test-only (W6, OTA gate follow-up SA-10 / OTA-force, ota-gating DESIGN.md 4.3): EXACTLY the VM
                tree GB_WRITE_LAPIS=1 build_esp.py writes into lapis.bin (build_esp.INJECT_CPIO, validated for the boot
                slot) with gb.otagate=force on the entry and the title "(VM test, OTA gate forced)": GBINIT takes the
                unvalidated path on the validated build (no /odm/vnni, ro.vendor.gb.ota_gate=unvalidated, the "System
                update not validated" notification); also writes <output>.vmtree.sha256 (the user-VM tree record); the
                output must be below D:\\gb-w6test\\
  --bringup-test DIR  test-only (WAVE3_PLAN R-12b), with --esp-only: the native ESP plus DIR\\gb.cfg -> gb/gb.cfg,
                DIR\\overlay\\** -> gb/overlay/** and gb.overlay=1 in the entries (never a shipped ESP); the output
                must be below D:\\gb-w3test\\
Flash the .img with Rufus (DD image mode), balenaEtcher or dd to a USB drive of at least 32 GB.
"""
import argparse, hashlib, importlib.util, os, struct, subprocess, sys, uuid, zlib

HERE = os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__))   # workspace (README: GB_HOME)
spec = importlib.util.spec_from_file_location("build_esp", os.path.join(HERE, "build_esp.py"))
be = importlib.util.module_from_spec(spec); spec.loader.exec_module(be)   # shared ESP/initrd pieces, no disk write

SECTOR = 512
MiB = 1 << 20
ESP_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
LINUX_TYPE = "0fc63daf-8483-4772-8e79-3d69d8477de4"
VERIFY_IMAGE = os.path.join(HERE, "verify_image.py")
DEFAULT_OUT = os.path.join(HERE, "out", "googlebook-sb3-usb.img")
VMTEST_OUT = os.path.join(HERE, "out", "googlebook-sb3-vmtest.img")
VMTEST_GFX_ROOT = "D:\\gb-w2test"                 # --vm-test-gfx images only below this (throwaway checks)
W3TEST_ROOT = "D:\\gb-w3test"                     # --vm-test-kat / --bringup-test outputs only below this (wave 3)
W4TEST_ROOT = "D:\\gb-w4test"                     # --vm-test-rehearsal / --vm-test-nogk outputs only below this (W4)
W5TEST_ROOT = "D:\\gb-w5test"                     # --vm-test-w5 outputs only below this (W5, VNNI measurement)
W6TEST_ROOT = "D:\\gb-w6test"                     # --vm-test-otagate outputs only below this (W6, OTA gate forced)
TITLE_NATIVE = "Googlebook OS - Surface Book 3 (USB)"
TITLE_VM = "Googlebook OS - USB image layout (VM test)"
TITLE_VM_GFX = "Googlebook OS - USB image layout (VM test, gfx components)"
TITLE_VM_KAT = "Googlebook OS - USB image layout (VM test, KAT)"
TITLE_VM_REHEARSAL = "Googlebook OS - USB image layout (VM test, W4 rehearsal)"
TITLE_VM_NOGK = "Googlebook OS - USB image layout (VM test, no Gatekeeper)"
TITLE_VM_W5 = "Googlebook OS - USB image layout (VM test, W5)"
TITLE_VM_W5_OFF = "Googlebook OS - USB image layout (VM test, W5, VNNI off)"     # verify_image VNNI_OFF_TITLE
TITLE_VM_OTAGATE = "Googlebook OS - USB image layout (VM test, OTA gate forced)"  # verify_image OTA_FORCE_TITLE
BRINGUP_MAX = 8 * MiB                             # --bringup-test: the test inputs' total size

# Surface Book 3 PCI paths (Windows DEVPKEY_Device_LocationPaths): PCH xHCI PCI(1400), Thunderbolt xHCI PCI(0D00),
# internal NVMe behind root port PCI(1D00). first-stage init matches the first PCI device under pci0000:00.
USB_BOOT_DEVICES = "pci0000:00/0000:00:14.0,pci0000:00/0000:00:0d.0"
NVME_BOOT_DEVICES = "pci0000:00/0000:00:1d.0"


def lapis_partitions(path):
    """name -> (first_lba, sectors, type_guid, attributes) from the lapis disk image's GPT. The attributes matter:
    the boot_control HAL keeps the A/B slot state (priority/tries/successful) in those of vbmeta_a/vbmeta_b."""
    with open(path, "rb") as f:
        f.seek(SECTOR); hdr = f.read(92)
        assert hdr[:8] == b"EFI PART", "lapis.bin has no GPT"
        ent_lba, n, esz = struct.unpack_from("<QII", hdr, 72)
        f.seek(ent_lba * SECTOR); raw = f.read(n * esz)
    parts = {}
    for i in range(n):
        e = raw[i * esz:(i + 1) * esz]
        if e[:16] == b"\0" * 16:
            continue
        first, last = struct.unpack_from("<QQ", e, 32)
        name = e[56:128].decode("utf-16-le").rstrip("\0")
        attrs = struct.unpack_from("<Q", e, 48)[0]
        parts[name] = (first, last - first + 1, str(uuid.UUID(bytes_le=e[:16])), attrs)
    return parts


# Native-only kernel parameters (see native_cmdline): never let Android touch the Surface's TPM or BIOS SPI flash
# (lapis ships tpm/spi-intel/gsmi modules for the Googlebook's own chips).
NATIVE_BLACKLIST = ("tpm,tpm_tis,tpm_tis_core,tpm_tis_spi,tpm_tis_i2c_cr50,spi_intel_pci,spi_intel,spi_nor,mtd,gsmi")


def native_cmdline(boot_devices, boot, part_uuid=None):
    """Kernel command line for bare metal. Kept from CMDLINE: 8250.nr_uarts=4 (WAVE3_PLAN item 1). Our GKI has
    CONFIG_SERIAL_8250_RUNTIME_UARTS=0, and with nr_uarts=0 serial8250_init() returns -ENODEV before it registers the
    8250 uart driver, so the LPSS UART that carries the Surface Aggregator (base keyboard/touchpad, batteries, charger,
    detach) could never register (dw-apb-uart probe -22). Once registered, the driver grows the port table on demand,
    so any value >= 1 works; 4 is the Kconfig default and the VM's value. Finding: gbOS86 HANDOFF.md:423 (Surface
    Laptop 7; github.com/linuxkid473/gbos-x86). Dropped: the VMware serial console/earlycon (never console=ttyS* or
    earlycon natively: the serdev core owns that UART) and the virtual display mode. Added:
    efi_pstore.pstore_disable=1 (kernel panics must not be written into the Surface's UEFI NVRAM),
    firmware_class.path with /odm/firmware first (Surface firmware, present before the /vendor overlay),
    iommu.passthrough=1 (IPTS touch DMA faults under the IOMMU), reboot=pci (Surface Book 3 hangs on other reboot
    methods - linux-surface wiki), snd_intel_dspcfg.dsp_driver=3 (SOF; the built-in dspcfg has no ICL entry, so SOF
    and AVS would race), the module blacklist above, and gb.logpart for the boot log.
    The disk that holds this install's partitions: with part_uuid, androidboot.boot_part_uuid (strict: only that
    disk's partitions get /dev/block/by-name links, so a USB stick and an internal install never mix); otherwise the
    non-strict androidboot.boot_devices PCI paths."""
    drop = ("console=ttyS0", "earlycon=", "video=Virtual-1", "firmware_class.path=")
    base = " ".join(a for a in be.CMDLINE.split() if not a.startswith(drop))
    disk = f"androidboot.boot_part_uuid={part_uuid.lower()}" if part_uuid else f"androidboot.boot_devices={boot_devices}"
    return (f"{base} firmware_class.path=/odm/firmware,/vendor/firmware efi_pstore.pstore_disable=1 "
            f"iommu.passthrough=1 reboot=pci snd_intel_dspcfg.dsp_driver=3 module_blacklist={NATIVE_BLACKLIST} "
            f"gb.logpart=EFI-SYSTEM gb.hw=native gb.boot={boot} {disk}")


def gpt_entry(type_guid, part_guid, first, last, name, attrs=0):
    return (uuid.UUID(type_guid).bytes_le + uuid.UUID(part_guid).bytes_le + struct.pack("<QQQ", first, last, attrs)
            + name.encode("utf-16-le").ljust(72, b"\0"))


def gpt_header(lba, backup_lba, first_usable, last_usable, disk_guid, entries_lba, entries_crc):
    h = bytearray(struct.pack("<8sIIIIQQQQ16sQIII", b"EFI PART", 0x00010000, 92, 0, 0, lba, backup_lba,
                              first_usable, last_usable, uuid.UUID(disk_guid).bytes_le, entries_lba, 128, 128,
                              entries_crc))
    struct.pack_into("<I", h, 16, zlib.crc32(bytes(h)))
    return bytes(h).ljust(SECTOR, b"\0")


def _below(path, root):
    """True if path (resolved) is strictly below the directory root."""
    p, r = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
    return p != r and p.startswith(r.rstrip("\\/") + os.sep)


VMRUN = r"C:\Program Files\VMware\VMware Workstation\vmrun.exe"


def user_vm_busy(here=HERE, vmrun=VMRUN):
    """(W6 prep, IA-1 of vnni-perf\\w5\\IMAGE_AUDIT.md) Why the user's VM may be using lapis.bin now ([] = it is off):
    `vmrun list` names here\\lapis-boot.vmx, VMware holds a lapis*.lck lock (build_esp's write guard), or vmrun cannot
    tell. A full image copies boot/super partitions from lapis.bin and must not read them while the guest writes."""
    why = []
    try:
        out = subprocess.run([vmrun, "-T", "ws", "list"], capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        why.append(f"vmrun list failed ({e.__class__.__name__}): cannot show that the VM is off")
    else:
        if os.path.join(here, "lapis-boot.vmx").lower() in out.lower():
            why.append(f"VMware lists {os.path.join(here, 'lapis-boot.vmx')} as running")
    try:
        why += [f"VMware lock {n} present" for n in sorted(os.listdir(here))
                if n.lower().startswith("lapis") and n.lower().endswith(".lck")]
    except OSError as e:
        why.append(f"cannot list {here} for VMware locks ({e})")
    return why


def parse_args(argv=None):
    """Command line (plan 3.3). Usage errors exit 2 (argparse)."""
    ap = argparse.ArgumentParser(prog="build_usb.py")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--userdata-gib", type=int, default=14, help="14 fits a 32 GB stick (image ~27 GiB); 40 for 64 GB")
    ap.add_argument("--esp-mib", type=int, default=512, help="room for both slots' initrds (gb_slotsync) + growth")
    ap.add_argument("--export", help="also write each partition as DIR\\<name>.img for the dual-boot installer")
    ap.add_argument("--sparse", action="store_true", help="write the image as an NTFS sparse file")
    ap.add_argument("--allow-running-vm", action="store_true",
                    help="(IA-1) read lapis.bin even while the user's VM runs (refused by default)")
    ap.add_argument("--vm-test", action="store_true",
                    help="same disk layout, but the VM overlay tree and the VMware NVMe boot device: boot-test the "
                         "image structure (GPT, slots, first-boot formatting) in VMware before flashing")
    ap.add_argument("--vm-test-gfx", action="store_true",
                    help="with --vm-test: add GbGpu and the gfx driver package to the VM tree (throwaway VM check)")
    ap.add_argument("--vm-test-kat", action="store_true",
                    help="with --vm-test: add the VNNI known-answer test to the VM tree (throwaway VM check, R-13)")
    ap.add_argument("--vm-test-rehearsal", action="store_true",
                    help="with --vm-test: exactly the VM tree build_esp.py writes into lapis.bin (W4 rehearsal VM)")
    ap.add_argument("--vm-test-nogk", action="store_true",
                    help="with --vm-test: that VM tree without the Gatekeeper (W4 rehearsal: the pre-W4 stand-in)")
    ap.add_argument("--vm-test-w5", action="store_true",
                    help="with --vm-test: exactly the VM tree build_esp.py writes into lapis.bin (W5 VNNI measurement)")
    ap.add_argument("--vnni-off", action="store_true",
                    help="with --vm-test-w5: the same tree, gb.vnni=0 on the entry (the measurement baseline)")
    ap.add_argument("--esp-only", metavar="FILE", help="build and check only the ESP, written to FILE")
    ap.add_argument("--super-guid", metavar="G", help="with --esp-only: use this super partition GUID")
    ap.add_argument("--bringup-test", metavar="DIR",
                    help="with --esp-only (native): add DIR\\gb.cfg + DIR\\overlay and gb.overlay=1 (R-12b test ESP)")
    ap.add_argument("--vm-test-otagate", action="store_true",
                    help="with --vm-test: exactly the user-VM tree, gb.otagate=force on the entry (W6 forced OTA gate)")
    a = ap.parse_args(argv)
    if a.vm_test_gfx and not a.vm_test:
        ap.error("--vm-test-gfx needs --vm-test")
    if a.vm_test_kat and not a.vm_test:
        ap.error("--vm-test-kat needs --vm-test")
    if a.vm_test_kat and a.vm_test_gfx:
        ap.error("--vm-test-kat and --vm-test-gfx are separate test images")
    for flag, on in (("--vm-test-rehearsal", a.vm_test_rehearsal), ("--vm-test-nogk", a.vm_test_nogk),
                     ("--vm-test-w5", a.vm_test_w5)):
        if on and not a.vm_test:
            ap.error(f"{flag} needs --vm-test")
    if a.vnni_off and not a.vm_test_w5:
        ap.error("--vnni-off is a --vm-test-w5 test variant only (gb.vnni=0 never goes into another entry)")
    variants = [f for f, on in (("--vm-test-gfx", a.vm_test_gfx), ("--vm-test-kat", a.vm_test_kat),
                                 ("--vm-test-rehearsal", a.vm_test_rehearsal), ("--vm-test-nogk", a.vm_test_nogk),
                                 ("--vm-test-w5", a.vm_test_w5)) if on]
    if len(variants) > 1:
        ap.error(f"{' and '.join(variants)} are separate test images")
    if a.bringup_test is not None:
        if not a.esp_only or a.vm_test:
            ap.error("--bringup-test is a native --esp-only test variant (no --vm-test)")
        if not os.path.isfile(os.path.join(a.bringup_test, "gb.cfg")):
            ap.error(f"--bringup-test: {a.bringup_test}\\gb.cfg is missing")
    if a.super_guid is not None:
        if not a.esp_only:
            ap.error("--super-guid is accepted only with --esp-only")
        try:
            a.super_guid = str(uuid.UUID(a.super_guid))
        except ValueError:
            ap.error(f"--super-guid: not a GUID: {a.super_guid!r}")
    if a.esp_only and (a.export or a.sparse):
        ap.error("--esp-only writes only the ESP (no --export, no --sparse)")
    if a.vm_test and a.out == DEFAULT_OUT:
        a.out = VMTEST_OUT
    written = a.esp_only or a.out
    if a.vm_test_gfx and (not _below(written, VMTEST_GFX_ROOT) or a.export):
        ap.error(f"--vm-test-gfx output must be below {VMTEST_GFX_ROOT}\\ (and no --export): {written}")
    for flag, on in (("--vm-test-kat", a.vm_test_kat), ("--bringup-test", a.bringup_test is not None)):
        if on and (not _below(written, W3TEST_ROOT) or a.export):
            ap.error(f"{flag} output must be below {W3TEST_ROOT}\\ (and no --export): {written}")
    for flag, on in (("--vm-test-rehearsal", a.vm_test_rehearsal), ("--vm-test-nogk", a.vm_test_nogk)):
        if on and (not _below(written, W4TEST_ROOT) or a.export):
            ap.error(f"{flag} output must be below {W4TEST_ROOT}\\ (and no --export): {written}")
    if a.vm_test_w5 and (not _below(written, W5TEST_ROOT) or a.export):
        ap.error(f"--vm-test-w5 output must be below {W5TEST_ROOT}\\ (and no --export): {written}")
    if a.vm_test_otagate:               # (W6) one more test variant: the user-VM tree with gb.otagate=force
        if not a.vm_test or any((a.vm_test_gfx, a.vm_test_kat, a.vm_test_rehearsal, a.vm_test_nogk, a.vm_test_w5)):
            ap.error("--vm-test-otagate needs --vm-test and is a separate test image")
        if not _below(written, W6TEST_ROOT) or a.export:
            ap.error(f"--vm-test-otagate output must be below {W6TEST_ROOT}\\ (and no --export): {written}")
    for p in filter(None, (a.out, a.esp_only, a.export)):
        if os.path.normcase(os.path.realpath(p)) == os.path.normcase(os.path.realpath(be.LAPIS)):
            ap.error("refusing to write lapis.bin")
    return a


def set_sparse(f):
    """Mark an open file sparse (FSCTL_SET_SPARSE, as iso\\test\\common.py). True on success."""
    if os.name != "nt":
        return False
    import ctypes, msvcrt
    from ctypes import wintypes
    ret = wintypes.DWORD()
    return bool(ctypes.windll.kernel32.DeviceIoControl(wintypes.HANDLE(msvcrt.get_osfhandle(f.fileno())),
                                                       0x000900C4, None, 0, None, 0, ctypes.byref(ret), None))


class ImageWriter:
    """Sequential image writer: running sha256 of every byte; with sparse=True every all-zero 1 MiB block (aligned
    to the file offset) is skipped with a seek instead of written (it reads back as zeros; hashed as zeros)."""
    BLOCK = MiB
    ZERO = bytes(MiB)
    ZERO16 = bytes(16 * MiB)

    def __init__(self, f, sparse):
        self.f, self.sparse, self.h, self.pos, self.skipped = f, sparse, hashlib.sha256(), 0, 0

    def tell(self):
        return self.pos

    def _skip(self, n):
        self.f.seek(n, os.SEEK_CUR); self.pos += n; self.skipped += n

    def write(self, data):
        data = bytes(data)
        self.h.update(data)
        if not self.sparse:
            self.f.write(data); self.pos += len(data); return
        i = 0
        while i < len(data):
            n = min(len(data) - i, self.BLOCK - self.pos % self.BLOCK)
            piece = data[i:i + n]
            if n == self.BLOCK and piece == self.ZERO:
                self._skip(n)
            else:
                self.f.write(piece); self.pos += n
            i += n

    def zeros(self, n):
        while n:
            k = min(n, 16 * MiB)
            if self.sparse and k == 16 * MiB and self.pos % self.BLOCK == 0:
                self.h.update(self.ZERO16); self._skip(k)       # 16 whole zero blocks
            else:
                self.write(self.ZERO16[:k])
            n -= k


def bringup_files(d):
    """--bringup-test (WAVE3_PLAN R-12b): {ESP path: bytes} for d\\gb.cfg and every file below d\\overlay. Test inputs
    only (they may hold names the bring-up must refuse); at most BRINGUP_MAX bytes, regular files only."""
    out = {"gb/gb.cfg": open(os.path.join(d, "gb.cfg"), "rb").read()}
    root = os.path.join(d, "overlay")
    for dp, _, fs in os.walk(root):
        for n in sorted(fs):
            p = os.path.join(dp, n)
            if os.path.islink(p) or not os.path.isfile(p):
                sys.exit(f"--bringup-test: {p} is not a regular file")
            out["gb/overlay/" + os.path.relpath(p, root).replace("\\", "/")] = open(p, "rb").read()
    if sum(len(v) for v in out.values()) > BRINGUP_MAX:
        sys.exit(f"--bringup-test: {d} holds more than {BRINGUP_MAX >> 20} MiB")
    print(f"bring-up test ESP: {len(out)} file(s) from {d}: {sorted(out)}", flush=True)
    return out


def run_verify(args):
    if os.environ.get("GB_GFXDRIVER") == "0":     # build_esp's bisect opt-out: the tree has no gfx driver package
        args = args + ["--no-gfxdriver"]
    cmd = [sys.executable, "-I", "-B", VERIFY_IMAGE] + args
    print("verify:", " ".join(cmd[3:]), flush=True)
    return subprocess.run(cmd).returncode


def main(argv=None):
    a = parse_args(argv)
    busy = [] if a.esp_only else user_vm_busy()     # (W6 prep, IA-1) --esp-only copies no partition
    if busy and not a.allow_running_vm:
        sys.exit("build_usb.py: refused: the user's VM may be using lapis.bin (" + "; ".join(busy) + "). Shut it down "
                 "first (or pass --allow-running-vm on purpose)")
    for w in busy:
        print(f"WARNING (--allow-running-vm): {w}; lapis.bin is read while the guest may write it", flush=True)
    src = lapis_partitions(be.LAPIS)
    # The copied system must be the one the source boots (an OTA leaves the other slot stale), and no update may be
    # half-applied: super is copied but /metadata (snapshot state) starts blank. Checked before anything is built.
    at = be.XS.slot_attrs(be.LAPIS)
    other = "a" if be.SLOT == "b" else "b"
    if at.get(other, (0, 0, 0))[1] and not at.get(other, (0, 0, 0))[2]:
        sys.exit(f"slot {other} has a pending update (vbmeta_{other} tries left, not successful): boot the VM, let the "
                 f"update finish and merge, then rebuild")
    print(f"system slot: {be.SLOT} (vbmeta attrs {at})")

    # Fresh partition GUIDs per image (never lapis's stock ones); the boot entry names this stick's super partition
    # (androidboot.boot_part_uuid), so only this disk's partitions are used even when an internal install exists.
    super_guid = a.super_guid or str(uuid.uuid4())
    # Same ESP layout as the VM (gb-<slot> entry + /gb/* for the in-guest slot follower gb_slotsync), native cmdline.
    if a.vm_test:   # VM overlay, but the same strict disk selection as the stick (tests boot_part_uuid in VMware)
        usb_cmdline = f"{be.CMDLINE} gb.hw=vm androidboot.boot_part_uuid={super_guid}"
        if a.vm_test_gfx:
            tree, title, kind = be.vm_test_gfx_cpio(), TITLE_VM_GFX, "vmtest-gfx"
        elif a.vm_test_kat:
            try:                            # refuses unless inject\kat and the staged zram/zsmalloc match (2.7)
                tree = be.vm_test_kat_cpio()
            except RuntimeError as e:
                sys.exit(f"--vm-test-kat: {e}")
            title, kind = TITLE_VM_KAT, "vmtest-kat"
        elif a.vm_test_rehearsal:       # exactly the tree build_esp.py writes into lapis.bin (the object, not a copy)
            tree, title, kind = be.INJECT_CPIO, TITLE_VM_REHEARSAL, "vm"
        elif a.vm_test_nogk:
            tree, title, kind = be.vm_test_nogk_cpio(), TITLE_VM_NOGK, "vmtest-nogk"
        elif a.vm_test_w5:              # (W5) exactly the user-VM tree; --vnni-off: the kill switch on the entry
            tree, title, kind = be.INJECT_CPIO, (TITLE_VM_W5_OFF if a.vnni_off else TITLE_VM_W5), "vm"
            if a.vnni_off:
                usb_cmdline += " gb.vnni=0"
        else:
            tree, title, kind = be.INJECT_CPIO, TITLE_VM, "vm"
        if a.vm_test_otagate:           # (W6) exactly the user-VM tree; GBINIT's unvalidated path forced on the entry
            tree, title, kind = be.INJECT_CPIO, TITLE_VM_OTAGATE, "vm"
            usb_cmdline += " gb.otagate=force"
        files = be.slot_files(usb_cmdline, tree, title, timeout=4)
        min_free = be.vm_min_free(files)        # (W4) as for the user's VM ESP: an OTA's second initrd must fit
    else:
        usb_cmdline = native_cmdline(USB_BOOT_DEVICES, "usb", part_uuid=super_guid)
        if a.bringup_test is not None:      # R-12b only: never a shipped ESP (verify_image --expect-bringup-test)
            usb_cmdline += " gb.overlay=1"
        files = be.slot_files(usb_cmdline, be.INJECT_CPIO_NATIVE, TITLE_NATIVE, timeout=4, native=True)
        if a.bringup_test is not None:
            files.update(bringup_files(a.bringup_test))
        min_free, kind = be.native_min_free(files), "native"
    esp = be.make_esp(files, a.esp_mib * MiB, min_free=min_free)
    print(f"super GUID: {super_guid}", flush=True)
    record = None
    if a.vm_test_rehearsal or a.vm_test_nogk or a.vm_test_w5:   # the tree's identity (the user-VM preview is compared)
        record = be.vm_tree_record(tree)
        assert record.split()[0].decode() == hashlib.sha256(files["gb/inject.gz"]).hexdigest(), "gb/inject.gz"
        if a.vm_test_rehearsal or a.vm_test_w5:
            assert tree is be.INJECT_CPIO and record == be.vm_tree_record(), "the rehearsal tree is not INJECT_CPIO"
        print("VM tree: " + record.decode().replace("\n", "; "), flush=True)
    if a.vm_test_otagate:               # (W6) the user-VM tree record: the forced boot runs exactly that tree
        record = be.vm_tree_record(tree)
        assert tree is be.INJECT_CPIO and record.split()[0].decode() == hashlib.sha256(files["gb/inject.gz"]).hexdigest()
        print("VM tree: " + record.decode().replace("\n", "; "), flush=True)
    tree_args = ["--expect-inject-sha256", record.split()[0].decode()] if record else []

    def write_record(path):
        if record:
            with open(path + ".vmtree.sha256", "wb") as f:
                f.write(record)
            print(f"wrote {path}.vmtree.sha256", flush=True)

    if a.esp_only:
        os.makedirs(os.path.dirname(os.path.abspath(a.esp_only)), exist_ok=True)
        with open(a.esp_only, "wb") as f:
            f.write(esp)
        print(f"wrote ESP {a.esp_only}: {len(esp):,} B, sha256 {hashlib.sha256(esp).hexdigest()}", flush=True)
        rc = run_verify(["--esp", a.esp_only, "--boot", "usb", "--super-guid", super_guid, "--expect-tree", kind]
                        + (["--expect-bringup-test"] if a.bringup_test is not None else []) + tree_args)
        if rc != 0:
            sys.exit(f"verify_image.py --esp reports problems (exit {rc})")
        write_record(a.esp_only)
        return

    # (name, type, size bytes, source, gpt attributes) - source: bytes, ("lapis", name) or None (blank)
    layout = [("EFI-SYSTEM", ESP_TYPE, len(esp), esp, 0)]
    # pvmfw: vbmeta carries a hash descriptor for it
    for name in ("boot_a", "boot_b", "init_boot_a", "init_boot_b", "vendor_boot_a", "vendor_boot_b",
                 "vbmeta_a", "vbmeta_b", "pvmfw_a", "pvmfw_b"):
        if name not in src:
            continue
        first, n, t, attrs = src[name]
        layout.append((name, t, n * SECTOR, ("lapis", name), attrs))
    layout += [("misc", LINUX_TYPE, 4 * MiB, None, 0), ("metadata", LINUX_TYPE, 64 * MiB, None, 0),
               ("frp_persist", LINUX_TYPE, 2 * MiB, None, 0),
               ("desktop_security_persist", LINUX_TYPE, 16 * MiB, None, 0),
               ("desktop_security_storage", LINUX_TYPE, 32 * MiB, None, 0)]
    first, n, t, attrs = src["super"]
    layout.append(("super", t, n * SECTOR, ("lapis", "super"), attrs))
    layout.append(("userdata", LINUX_TYPE, a.userdata_gib << 30, None, 0))

    # place partitions at 1 MiB alignment after the primary GPT
    lba = MiB // SECTOR
    placed = []
    for name, t, size, s, attrs in layout:
        size = (size + MiB - 1) // MiB * MiB
        placed.append((name, t, lba, lba + size // SECTOR - 1, s, size, attrs))
        lba += size // SECTOR
    total_lba = lba + 33 + MiB // SECTOR          # backup GPT (entries + header) after a 1 MiB gap
    entries = b"".join(gpt_entry(t, super_guid if nm == "super" else str(uuid.uuid4()), f, l, nm, at_)
                       for nm, t, f, l, _, _, at_ in placed).ljust(128 * 128, b"\0")
    ecrc = zlib.crc32(entries)
    disk_guid = str(uuid.uuid4())
    last_usable = total_lba - 34
    mbr = bytearray(SECTOR)
    mbr[446:462] = struct.pack("<BBBBBBBBII", 0, 0, 2, 0, 0xEE, 0xFF, 0xFF, 0xFF, 1, min(total_lba - 1, 0xFFFFFFFF))
    mbr[510:512] = b"\x55\xAA"

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    part = a.out + ".part"
    with open(part, "wb") as raw, open(be.LAPIS, "rb") as lap:
        if a.sparse and not set_sparse(raw):
            sys.exit(f"--sparse: FSCTL_SET_SPARSE failed on {part}")
        out = ImageWriter(raw, a.sparse)
        out.write(bytes(mbr))
        out.write(gpt_header(1, total_lba - 1, 34, last_usable, disk_guid, 2, ecrc))
        out.write(entries)
        out.zeros(MiB - out.tell())
        for name, t, f, l, s, size, _attrs in placed:
            assert out.tell() == f * SECTOR, name
            exp = open(os.path.join(a.export, name + ".img"), "wb") if a.export else None
            left = size
            if isinstance(s, (bytes, bytearray)):
                out.write(s); exp and exp.write(s); left -= len(s)
            elif s:
                sf, sn, _, _ = src[s[1]]
                lap.seek(sf * SECTOR); n = sn * SECTOR
                while n:
                    chunk = lap.read(min(n, 16 * MiB)); out.write(chunk); exp and exp.write(chunk)
                    n -= len(chunk); left -= len(chunk)
            if left > 0:                            # blank (or alignment tail): zeros
                if exp and s is None:
                    zero = bytes(16 * MiB); k = left
                    while k:
                        j = min(k, len(zero)); exp.write(zero[:j]); k -= j
                out.zeros(left)
            if exp: exp.close()
            print(f"  {name:<26} {f:>10} .. {l:>10}  {size / MiB:9.0f} MiB  {'blank' if s is None else 'copied' if isinstance(s, tuple) else 'built'}", flush=True)
        out.zeros(MiB)
        out.write(entries)
        out.write(gpt_header(total_lba - 1, 1, 34, last_usable, disk_guid, total_lba - 33, ecrc))   # last: fixes the size
        assert out.tell() == total_lba * SECTOR
    assert os.path.getsize(part) == total_lba * SECTOR, (os.path.getsize(part), total_lba * SECTOR)
    hexd = out.h.hexdigest()
    print(f"wrote {part}: {total_lba * SECTOR / 2**30:.2f} GiB"
          + (f", {out.skipped / 2**30:.2f} GiB of zero blocks left unallocated (sparse)" if a.sparse else ""), flush=True)
    print(f"image sha256: {hexd}", flush=True)
    rc = run_verify([part, "--expect-tree", kind] + tree_args)
    if rc != 0:
        sys.exit(f"verify_image.py reports problems (exit {rc}): kept {part}, no sidecar written")
    os.replace(part, a.out)
    with open(a.out + ".sha256", "w", encoding="ascii", newline="\n") as f:
        f.write(f"{hexd}  {os.path.basename(a.out)}\n")
    print(f"wrote {a.out} and {a.out}.sha256")
    write_record(a.out)
    print(f"boot entry gb-{be.SLOT}: {usb_cmdline}")


if __name__ == "__main__":
    main()
