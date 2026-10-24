"""Build a raw, flashable GPT disk image that boots Googlebook OS natively on the Surface Book 3 (USB flash drive),
and export the same partitions for the internal-disk (dual boot) installer.

Partitions come from the pristine lapis disk image (super with system/vendor/..., the A/B boot/init_boot/vendor_boot
and vbmeta images). userdata, metadata, misc and the security/FRP partitions are created BLANK: nothing from the VM's
/data (accounts, tokens, apps) is copied. userdata and metadata are 'formattable' in the fstab, so first boot formats
them. The ESP holds systemd-boot, the GKI kernel and the initrd built by build_esp.py, with gb.hw=native boot entries
whose androidboot.boot_devices name the Surface Book 3's two xHCI controllers, so Android only looks at the USB stick.

Usage: python -I build_usb.py [--out C:\\GB-x86\\out\\googlebook-sb3-usb.img] [--userdata-gib 8] [--export DIR]
Flash the .img with Rufus (DD image mode), balenaEtcher or dd to a USB drive of at least 32 GB.
"""
import argparse, importlib.util, os, struct, sys, uuid, zlib

HERE = os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__))   # workspace (README: GB_HOME)
spec = importlib.util.spec_from_file_location("build_esp", os.path.join(HERE, "build_esp.py"))
be = importlib.util.module_from_spec(spec); spec.loader.exec_module(be)   # shared ESP/initrd pieces, no disk write

SECTOR = 512
MiB = 1 << 20
ESP_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
LINUX_TYPE = "0fc63daf-8483-4772-8e79-3d69d8477de4"

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "out", "googlebook-sb3-usb.img"))
    ap.add_argument("--userdata-gib", type=int, default=14, help="14 fits a 32 GB stick (image ~27 GiB); 40 for 64 GB")
    ap.add_argument("--esp-mib", type=int, default=512, help="room for both slots' initrds (gb_slotsync) + growth")
    ap.add_argument("--export", help="also write each partition as DIR\\<name>.img for the dual-boot installer")
    ap.add_argument("--vm-test", action="store_true",
                    help="same disk layout, but the VM overlay tree and the VMware NVMe boot device: boot-test the "
                         "image structure (GPT, slots, first-boot formatting) in VMware before flashing")
    a = ap.parse_args()
    if a.vm_test and a.out == ap.get_default("out"):
        a.out = os.path.join(HERE, "out", "googlebook-sb3-vmtest.img")

    src = lapis_partitions(be.LAPIS)
    # Fresh partition GUIDs per image (never lapis's stock ones); the boot entry names this stick's super partition
    # (androidboot.boot_part_uuid), so only this disk's partitions are used even when an internal install exists.
    super_guid = str(uuid.uuid4())
    # Same ESP layout as the VM (gb-<slot> entry + /gb/* for the in-guest slot follower gb_slotsync), native cmdline.
    if a.vm_test:   # VM overlay, but the same strict disk selection as the stick (tests boot_part_uuid in VMware)
        usb_cmdline = f"{be.CMDLINE} gb.hw=vm androidboot.boot_part_uuid={super_guid}"
        tree, title = be.INJECT_CPIO, "Googlebook OS - USB image layout (VM test)"
    else:
        usb_cmdline = native_cmdline(USB_BOOT_DEVICES, "usb", part_uuid=super_guid)
        tree, title = be.INJECT_CPIO_NATIVE, "Googlebook OS - Surface Book 3 (USB)"
    esp = be.make_esp(be.slot_files(usb_cmdline, tree, title, timeout=4), a.esp_mib * MiB)

    # The copied system must be the one the source boots (an OTA leaves the other slot stale), and no update may be
    # half-applied: super is copied but /metadata (snapshot state) starts blank.
    at = be.XS.slot_attrs(be.LAPIS)
    other = "a" if be.SLOT == "b" else "b"
    if at.get(other, (0, 0, 0))[1] and not at.get(other, (0, 0, 0))[2]:
        sys.exit(f"slot {other} has a pending update (vbmeta_{other} tries left, not successful): boot the VM, let the "
                 f"update finish and merge, then rebuild")
    print(f"system slot: {be.SLOT} (vbmeta attrs {at})")

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

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    zero = bytes(16 * MiB)
    with open(a.out, "wb") as out, open(be.LAPIS, "rb") as lap:
        out.write(mbr)
        out.write(gpt_header(1, total_lba - 1, 34, last_usable, disk_guid, 2, ecrc))
        out.write(entries)
        out.write(bytes(MiB - out.tell()))
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
            while left > 0:                         # blank (or alignment tail): zeros
                k = min(left, len(zero)); out.write(zero[:k])
                if exp and s is None: exp.write(zero[:k])
                left -= k
            if exp: exp.close()
            print(f"  {name:<26} {f:>10} .. {l:>10}  {size / MiB:9.0f} MiB  {'blank' if s is None else 'copied' if isinstance(s, tuple) else 'built'}", flush=True)
        out.write(bytes(MiB))
        out.write(entries)
        out.write(gpt_header(total_lba - 1, 1, 34, last_usable, disk_guid, total_lba - 33, ecrc))
        assert out.tell() == total_lba * SECTOR
    print(f"wrote {a.out}: {total_lba * SECTOR / 2**30:.2f} GiB")
    print(f"boot entry gb-{be.SLOT}: {usb_cmdline}")


if __name__ == "__main__":
    main()
