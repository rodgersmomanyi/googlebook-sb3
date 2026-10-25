"""Build a raw, flashable GPT disk image that boots Googlebook OS natively on the Surface Book 3 (USB flash drive),
and export the same partitions for the internal-disk (dual boot) installer.

Partitions come from the pristine lapis disk image (super with system/vendor/..., the A/B boot/init_boot/vendor_boot
and vbmeta images). userdata, metadata, misc and the security/FRP partitions are created BLANK: nothing from the VM's
/data (accounts, tokens, apps) is copied. userdata and metadata are 'formattable' in the fstab, so first boot formats
them. The ESP holds systemd-boot, the GKI kernel and the initrd built by build_esp.py, with gb.hw=native boot entries
whose androidboot.boot_devices name the Surface Book 3's two xHCI controllers, so Android only looks at the USB stick.
Native images also carry the NVIDIA test entry gb-<slot>-nvtest and the dGPU payload gb/dgpu-<krel>.erofs.

Usage: python -I build_usb.py [--out FILE] [--userdata-gib N] [--esp-mib 512] [--export DIR] [--sparse]
                              [--vm-test [--vm-test-gfx]] [--esp-only FILE [--super-guid G]]
  The image is written to <out>.part (sha256 computed while writing), checked with verify_image.py, and only then
  renamed to <out>, with <out>.sha256 = "<hex>  <name>". A failed check keeps the .part file and writes no sidecar.
  --sparse      NTFS sparse file: all-zero 1 MiB blocks are not allocated (same content and sha256)
  --esp-only F  build only the ESP (the same code path as the image), write it to F, check it with
                verify_image.py --esp, exit (checkpoints; --super-guid fixes the GUID for determinism checks)
  --vm-test-gfx test-only: the VM tree plus GbGpu and the gfx driver package (build_esp.vm_test_gfx_cpio); the output
                must be below D:\\gb-w2test\\
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
TITLE_NATIVE = "Googlebook OS - Surface Book 3 (USB)"
TITLE_VM = "Googlebook OS - USB image layout (VM test)"
TITLE_VM_GFX = "Googlebook OS - USB image layout (VM test, gfx components)"

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
    """Kernel command line for bare metal. Dropped: the VMware serial console/earlycon and 8250.nr_uarts (on the
    Surface the LPSS UART carries the Surface Aggregator link) and the virtual display mode. Added:
    efi_pstore.pstore_disable=1 (kernel panics must not be written into the Surface's UEFI NVRAM),
    firmware_class.path with /odm/firmware first (Surface firmware, present before the /vendor overlay),
    iommu.passthrough=1 (IPTS touch DMA faults under the IOMMU), reboot=pci (Surface Book 3 hangs on other reboot
    methods - linux-surface wiki), snd_intel_dspcfg.dsp_driver=3 (SOF; the built-in dspcfg has no ICL entry, so SOF
    and AVS would race), the module blacklist above, and gb.logpart for the boot log.
    The disk that holds this install's partitions: with part_uuid, androidboot.boot_part_uuid (strict: only that
    disk's partitions get /dev/block/by-name links, so a USB stick and an internal install never mix); otherwise the
    non-strict androidboot.boot_devices PCI paths."""
    drop = ("console=ttyS0", "earlycon=", "video=Virtual-1", "8250.nr_uarts", "firmware_class.path=")
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


def parse_args(argv=None):
    """Command line (plan 3.3). Usage errors exit 2 (argparse)."""
    ap = argparse.ArgumentParser(prog="build_usb.py")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--userdata-gib", type=int, default=14, help="14 fits a 32 GB stick (image ~27 GiB); 40 for 64 GB")
    ap.add_argument("--esp-mib", type=int, default=512, help="room for both slots' initrds (gb_slotsync) + growth")
    ap.add_argument("--export", help="also write each partition as DIR\\<name>.img for the dual-boot installer")
    ap.add_argument("--sparse", action="store_true", help="write the image as an NTFS sparse file")
    ap.add_argument("--vm-test", action="store_true",
                    help="same disk layout, but the VM overlay tree and the VMware NVMe boot device: boot-test the "
                         "image structure (GPT, slots, first-boot formatting) in VMware before flashing")
    ap.add_argument("--vm-test-gfx", action="store_true",
                    help="with --vm-test: add GbGpu and the gfx driver package to the VM tree (throwaway VM check)")
    ap.add_argument("--esp-only", metavar="FILE", help="build and check only the ESP, written to FILE")
    ap.add_argument("--super-guid", metavar="G", help="with --esp-only: use this super partition GUID")
    a = ap.parse_args(argv)
    if a.vm_test_gfx and not a.vm_test:
        ap.error("--vm-test-gfx needs --vm-test")
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


def run_verify(args):
    if os.environ.get("GB_GFXDRIVER") == "0":     # build_esp's bisect opt-out: the tree has no gfx driver package
        args = args + ["--no-gfxdriver"]
    cmd = [sys.executable, "-I", "-B", VERIFY_IMAGE] + args
    print("verify:", " ".join(cmd[3:]), flush=True)
    return subprocess.run(cmd).returncode


def main(argv=None):
    a = parse_args(argv)
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
        else:
            tree, title, kind = be.INJECT_CPIO, TITLE_VM, "vm"
        files = be.slot_files(usb_cmdline, tree, title, timeout=4)
        min_free = 0
    else:
        usb_cmdline = native_cmdline(USB_BOOT_DEVICES, "usb", part_uuid=super_guid)
        files = be.slot_files(usb_cmdline, be.INJECT_CPIO_NATIVE, TITLE_NATIVE, timeout=4, native=True)
        min_free, kind = be.native_min_free(files), "native"
    esp = be.make_esp(files, a.esp_mib * MiB, min_free=min_free)
    print(f"super GUID: {super_guid}", flush=True)

    if a.esp_only:
        os.makedirs(os.path.dirname(os.path.abspath(a.esp_only)), exist_ok=True)
        with open(a.esp_only, "wb") as f:
            f.write(esp)
        print(f"wrote ESP {a.esp_only}: {len(esp):,} B, sha256 {hashlib.sha256(esp).hexdigest()}", flush=True)
        rc = run_verify(["--esp", a.esp_only, "--boot", "usb", "--super-guid", super_guid, "--expect-tree", kind])
        if rc != 0:
            sys.exit(f"verify_image.py --esp reports problems (exit {rc})")
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
    rc = run_verify([part, "--expect-tree", kind])
    if rc != 0:
        sys.exit(f"verify_image.py reports problems (exit {rc}): kept {part}, no sidecar written")
    os.replace(part, a.out)
    with open(a.out + ".sha256", "w", encoding="ascii", newline="\n") as f:
        f.write(f"{hexd}  {os.path.basename(a.out)}\n")
    print(f"wrote {a.out} and {a.out}.sha256")
    print(f"boot entry gb-{be.SLOT}: {usb_cmdline}")


if __name__ == "__main__":
    main()
