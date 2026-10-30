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

Only READS lapis.bin and writes into --out. (W6 prep, IA-1 of vnni-perf\\w5\\IMAGE_AUDIT.md) Refuses while the user's
VM (C:\\GB-x86\\lapis-boot.vmx) runs or VMware holds a lapis*.lck lock: the export copies boot/super partitions from
lapis.bin and must not read them while the guest may write them (an OTA in progress). --allow-running-vm overrides it
on purpose (a warning is printed and the manifest records "source_vm_running": true); --dry-run and the --test-*
exports only warn.

The ESP is a native one (WAVE2_PLAN_v2 B-04): the [NVIDIA test] entry gb-<slot>-nvtest.conf and the dGPU payload
gb/dgpu-<krel>.erofs + .sha256 (INTERFACES 6.1, 16), at least max(initrd) + 144 MiB left free. After manifest.json is
written, C:\\GB-x86\\verify_image.py --esp <out>\\EFI-SYSTEM.img --boot internal --super-guid <super> must pass; if it
does not, manifest.json is renamed manifest.json.unverified and the exit code is 1 (with --clear-nx-compat or a --test-*
option, which change or truncate the export and are never used by the ISO, problems are reported but not fatal).

Usage: python -I build_internal.py --out D:\\gb-export [--esp-mib 512] [--boot-devices] [--reuse-guids OLD\\manifest.json]
                                   [--clear-nx-compat] [--dry-run] [--test-super-mib N] [--allow-running-vm]
  --reuse-guids   keep the GUIDs of an installed set (needed to rebuild only the ESP: install_dualboot.ps1 -UpdateEspOnly)
  --test-super-mib  TEST ONLY: export just the first N MiB of super (manifest marked test_truncated; the installer
                  refuses such a manifest on a real disk) for quick image-mode tests.
"""
import argparse, datetime, hashlib, importlib.util, json, os, struct, subprocess, sys, uuid

sys.dont_write_bytecode = True   # never write __pycache__ into the workspace (same as python -B)
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # <repo>/native/dualboot
HERE = os.environ.get("GB_HOME") or (_REPO if os.path.isfile(os.path.join(_REPO, "build_esp.py")) else r"C:\GB-x86")
VERIFY_IMAGE = os.path.join(HERE, "verify_image.py")
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


VMRUN = r"C:\Program Files\VMware\VMware Workstation\vmrun.exe"


def user_vm_busy(here=HERE, vmrun=VMRUN):
    """(W6 prep, IA-1) Why the user's VM may be using lapis.bin now ([] = it is off): `vmrun list` names
    here\\lapis-boot.vmx, VMware holds a lapis*.lck lock (build_esp.py's write guard), or vmrun cannot tell."""
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
    ap.add_argument("--allow-running-vm", action="store_true",
                    help="(IA-1) export although the user's VM runs (lapis.bin read while the guest may write it)")
    a = ap.parse_args()
    test = bool(a.test_super_mib or a.test_userdata_min_mib)
    busy = user_vm_busy()
    if busy and not (a.allow_running_vm or a.dry_run or test):
        sys.exit("build_internal.py: refused: the user's VM may be using lapis.bin (" + "; ".join(busy) + "). Shut it "
                 "down first (or pass --allow-running-vm on purpose)")
    for w in busy:
        print(f"WARNING: {w}; lapis.bin is read while the guest may write it", flush=True)

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

    files = be.slot_files(cmdline, be.INJECT_CPIO_NATIVE, TITLE, timeout=4, native=True)
    nx_note = None
    if a.clear_nx_compat:
        files["vmlinuz"], v = clear_nx_compat(files["vmlinuz"])
        nx_note = f"DllCharacteristics 0x{v:04x} -> 0x{v & ~0x100:04x}"
        print("kernel NX_COMPAT cleared in the ESP copy:", nx_note)
    esp = be.make_esp(files, a.esp_mib * MiB, min_free=be.native_min_free(files))

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
        "source_vm_running": bool(busy),
        "partitions": parts,
    }
    mpath = os.path.join(a.out, "manifest.json")
    with open(mpath, "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"wrote {mpath} ({len(parts)} partitions{', TEST export' if test else ''})")

    # the export ESP passes the same checks as the USB image's ESP (B-04; the ISO's export cross-check 8 relies on it)
    cmd = [sys.executable, "-I", "-B", VERIFY_IMAGE, "--esp", os.path.join(a.out, "EFI-SYSTEM.img"),
           "--boot", "internal", "--super-guid", guid["super"]]
    if os.environ.get("GB_GFXDRIVER") == "0":     # build_esp's bisect opt-out: the tree has no gfx driver package
        cmd.append("--no-gfxdriver")
    print("$ python -I -B " + " ".join(cmd[3:]), flush=True)
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        print("  [verify] " + line, flush=True)
    if r.returncode != 0:
        if a.clear_nx_compat or test:
            print("verify_image.py reported problems: not fatal for a --clear-nx-compat / --test-* export "
                  "(never used by the ISO)")
        else:
            os.replace(mpath, mpath + ".unverified")
            sys.exit(f"verify_image.py reports problems on the export ESP (exit {r.returncode}); "
                     f"{mpath} renamed to manifest.json.unverified")
    else:
        print("verify_image.py: the export ESP is OK")


if __name__ == "__main__":
    main()
