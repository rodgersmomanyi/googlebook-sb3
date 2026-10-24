"""Export the Googlebook OS partition set for the INTERNAL (dual-boot) install on the Surface Book 3 NVMe.

Same partitions as build_usb.py --export, but:
  * the ESP's boot entry targets the internal disk: native_cmdline(NVME_BOOT_DEVICES, 'internal', ...) with
    gb.boot=internal. By default the strict selector androidboot.boot_part_uuid=<this export's super GUID> is used
    (only the partitions of the disk that holds that super get /dev/block/by-name links - the same mechanism the USB
    stick uses); --boot-devices uses the non-strict PCI path androidboot.boot_devices=pci0000:00/0000:00:1d.0.
  * every partition gets a fresh GUID here, recorded in manifest.json; the installer creates the partitions with
    exactly these GUIDs (the boot entry names the super GUID), names, types and attributes.
  * userdata is not exported: it is created by the installer to fill the space it was given (blank, first boot
    formats it). misc/metadata/frp_persist/desktop_security_* are blank too (zeroed by the installer).

Only READS lapis.bin (the running VM may hold it open) and writes into --out.

Usage: python -I build_internal.py --out D:\\gb-export [--esp-mib 512] [--boot-devices] [--reuse-guids OLD\\manifest.json]
                                   [--clear-nx-compat] [--dry-run] [--test-super-mib N]
  --reuse-guids   keep the GUIDs of an installed set (needed to rebuild only the ESP: install_dualboot.ps1 -UpdateEspOnly)
  --test-super-mib  TEST ONLY: export just the first N MiB of super (manifest marked test_truncated; the installer
                  refuses such a manifest on a real disk) for quick image-mode tests.
"""
import argparse, datetime, hashlib, importlib.util, json, os, struct, sys, uuid

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # <repo>/native/dualboot
HERE = os.environ.get("GB_HOME") or (_REPO if os.path.isfile(os.path.join(_REPO, "build_esp.py")) else r"C:\GB-x86")
spec = importlib.util.spec_from_file_location("build_usb", os.path.join(HERE, "build_usb.py"))
bu = importlib.util.module_from_spec(spec); spec.loader.exec_module(bu)   # imports build_esp itself (no disk write)
be = bu.be

MiB = 1 << 20
SECTOR = 512
TITLE = "Googlebook OS - Surface Book 3"
SOURCED = ("boot_a", "boot_b", "init_boot_a", "init_boot_b", "vendor_boot_a", "vendor_boot_b",
           "vbmeta_a", "vbmeta_b", "pvmfw_a", "pvmfw_b")
BLANK = (("misc", 4 * MiB), ("metadata", 64 * MiB), ("frp_persist", 2 * MiB),
         ("desktop_security_persist", 16 * MiB), ("desktop_security_storage", 32 * MiB))
USERDATA_MIN = 8 << 30


def clear_nx_compat(pe):
    """Copy of a PE/COFF image with IMAGE_DLLCHARACTERISTICS_NX_COMPAT cleared (linux-surface: Surface firmware stalls
    when the kernel advertises NX; their kernels revert the flag). The EFI loader ignores the PE checksum."""
    b = bytearray(pe)
    off = struct.unpack_from("<I", b, 0x3C)[0]
    assert b[off:off + 4] == b"PE\0\0", "kernel is not a PE image"
    dc = off + 24 + 70
    v = struct.unpack_from("<H", b, dc)[0]
    struct.pack_into("<H", b, dc, v & ~0x0100)
    return bytes(b), v


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=r"D:\gb-export")
    ap.add_argument("--esp-mib", type=int, default=512, help="as build_usb: room for both slots' initrds + growth")
    ap.add_argument("--boot-devices", action="store_true", help="non-strict androidboot.boot_devices=<NVMe PCI path>")
    ap.add_argument("--reuse-guids", help="manifest.json of an installed set: keep its partition GUIDs")
    ap.add_argument("--clear-nx-compat", action="store_true", help="clear NX_COMPAT in the ESP's copy of the kernel")
    ap.add_argument("--userdata-min-gib", type=int, default=USERDATA_MIN >> 30)
    ap.add_argument("--test-super-mib", type=int, default=0, help="TEST ONLY: truncate super")
    ap.add_argument("--test-userdata-min-mib", type=int, default=0, help="TEST ONLY: lower userdata minimum")
    ap.add_argument("--dry-run", action="store_true", help="print the layout and command line, write nothing")
    a = ap.parse_args()
    test = bool(a.test_super_mib or a.test_userdata_min_mib)

    src = bu.lapis_partitions(be.LAPIS)
    at = be.XS.slot_attrs(be.LAPIS)
    other = "a" if be.SLOT == "b" else "b"
    if at.get(other, (0, 0, 0))[1] and not at.get(other, (0, 0, 0))[2]:
        sys.exit(f"slot {other} has a pending update (vbmeta_{other} tries left, not successful): boot the VM, let the "
                 f"update finish and merge, then rebuild")
    print(f"system slot: {be.SLOT} (vbmeta attrs {at})")

    # partition GUIDs: fresh, or those of an installed set
    old = {}
    if a.reuse_guids:
        m = json.load(open(a.reuse_guids))
        old = {p["name"]: p["guid"] for p in m["partitions"]}
    names = ["EFI-SYSTEM"] + [n for n in SOURCED if n in src] + [n for n, _ in BLANK] + ["super", "userdata"]
    guid = {n: old.get(n) or str(uuid.uuid4()) for n in names}
    if a.reuse_guids and set(old) != set(names):
        sys.exit(f"--reuse-guids: partition set differs ({sorted(set(old) ^ set(names))})")

    if a.boot_devices:
        cmdline = bu.native_cmdline(bu.NVME_BOOT_DEVICES, "internal")
    else:
        cmdline = bu.native_cmdline(bu.NVME_BOOT_DEVICES, "internal", part_uuid=guid["super"])
    assert "gb.boot=internal" in cmdline and "gb.hw=native" in cmdline

    # (name, type, size, source, attrs): source = bytes | ("lapis", name, length) | None
    layout = [["EFI-SYSTEM", bu.ESP_TYPE, a.esp_mib * MiB, "esp", 0]]
    for n in SOURCED:
        if n in src:
            first, cnt, t, attrs = src[n]
            layout.append([n, t, cnt * SECTOR, ("lapis", n, cnt * SECTOR), attrs])
    for n, size in BLANK:
        layout.append([n, bu.LINUX_TYPE, size, None, 0])
    first, cnt, t, attrs = src["super"]
    sup = cnt * SECTOR
    if a.test_super_mib:
        sup = min(sup, a.test_super_mib * MiB)
    layout.append(["super", t, sup, ("lapis", "super", sup), attrs])
    ud_min = (a.test_userdata_min_mib * MiB) if a.test_userdata_min_mib else (a.userdata_min_gib << 30)

    print(f"boot entry (systemd-boot gb-{be.SLOT}, '{TITLE}'):\n  {cmdline}")
    for n, t, size, s, attrs in layout:
        kind = "built" if s == "esp" else "blank" if s is None else "lapis"
        print(f"  {n:<26} {size / MiB:9.0f} MiB  {kind:<6} type {t} attr 0x{attrs:016x} guid {guid[n]}")
    print(f"  {'userdata':<26} {'fill':>9}      blank  type {bu.LINUX_TYPE} min {ud_min / MiB:.0f} MiB guid {guid['userdata']}")
    if a.dry_run:
        print("dry run: nothing built or written")
        return

    files = be.slot_files(cmdline, be.INJECT_CPIO_NATIVE, TITLE, timeout=4)
    nx_note = None
    if a.clear_nx_compat:
        files["vmlinuz"], v = clear_nx_compat(files["vmlinuz"])
        nx_note = f"DllCharacteristics 0x{v:04x} -> 0x{v & ~0x100:04x}"
        print("kernel NX_COMPAT cleared in the ESP copy:", nx_note)
    esp = be.make_esp(files, a.esp_mib * MiB)

    os.makedirs(a.out, exist_ok=True)
    parts = []
    with open(be.LAPIS, "rb") as lap:
        for n, t, size, s, attrs in layout:
            entry = {"name": n, "type": t, "guid": guid[n], "size": size, "attributes": f"0x{attrs:016x}",
                     "file": None, "file_size": None, "sha256": None, "fill": False, "min_size": None}
            if s is not None:
                fn = n + ".img"
                h = hashlib.sha256()
                tmp = os.path.join(a.out, fn + ".part")
                with open(tmp, "wb") as f:
                    if s == "esp":
                        f.write(esp); h.update(esp); length = len(esp)
                    else:
                        sf = src[s[1]][0]
                        lap.seek(sf * SECTOR); left = length = s[2]
                        while left:
                            chunk = lap.read(min(left, 16 * MiB))
                            assert chunk, f"short read of {n} from lapis.bin"
                            f.write(chunk); h.update(chunk); left -= len(chunk)
                os.replace(tmp, os.path.join(a.out, fn))
                entry.update(file=fn, file_size=length, sha256=h.hexdigest())
                print(f"  wrote {fn:<24} {length:>12} B  sha256 {entry['sha256']}", flush=True)
            parts.append(entry)
    parts.append({"name": "userdata", "type": bu.LINUX_TYPE, "guid": guid["userdata"], "size": None,
                  "attributes": "0x0000000000000000", "file": None, "file_size": None, "sha256": None,
                  "fill": True, "min_size": ud_min})
    manifest = {
        "format": "gb-dualboot-manifest/1",
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": be.LAPIS, "slot": be.SLOT, "vbmeta_attrs": {k: list(v) for k, v in at.items()},
        "esp_title": TITLE, "cmdline": cmdline,
        "disk_selector": "boot_devices" if a.boot_devices else "boot_part_uuid",
        "kernel_nx_compat_cleared": nx_note, "esp_files": sorted(files),
        "test_truncated": test,
        "partitions": parts,
    }
    with open(os.path.join(a.out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"wrote {os.path.join(a.out, 'manifest.json')} ({len(parts)} partitions{', TEST export' if test else ''})")


if __name__ == "__main__":
    main()
