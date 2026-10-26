# googlebook-sb3-installer.iso — design v2

Status: specification for three parallel implementers (INSTALLER, BUILD, TEST). Everything marked **MUST** is a
contract between them; where this document gives an exact string, path, key or code, implement it byte for byte.
v2 = v1 plus the resolution of the safety review and the boot review; section 10 records every review decision
(accepted, changed, rejected, with reasons). Where v2 and v1 differ, v2 wins.
Personal use only: the ISO contains Google's proprietary "lapis" image. It stays on this PC (`D:\GB-iso\`), is never
uploaded, pushed or published.

Hard safety rules (repeated from the project brief, they bind all three roles and the code they write):

* Never touch real disks of the Windows host (no `\\.\PhysicalDrive*`, diskpart, Resize-Partition, bcdedit,
  efibootmgr, mountvol on the host). On the builder never write `/dev/nvme*`, `/dev/sd*`, `/dev/vd*`; loop devices over
  image FILES are fine (always check `losetup -j <file>` before mkfs/dd).
* The installer runs ONLY inside the ISO's initramfs, in practice only inside throwaway VMware test VMs (TEST) and on
  the user's Surface (the user). Host-side unit tests may import pure modules only (see 8.4).
* Never modify `C:\GB-x86\out\googlebook-sb3-usb.img`, `C:\GB-x86\lapis.bin`, the lapis VM or its files; never use
  `C:\GB-x86\gbsh.py` or `\\.\pipe\lapis_con`; never reboot the builder or change its kernel/bootloader. `D:\gb-export`,
  `D:\gb-test`, `D:\gb-usbtest` belong to other work: read-only for us.
* Test VMs: <= 2 GB RAM, <= 2 vCPUs, one at a time, all their files under `D:\GB-iso\test\`; power off and delete their
  disks when done; keep D: free space > 6 GB at every moment. The pre-start guard of 7.1 MUST pass before every start.
* No personal data in output or files (no user names, disk serials, e-mails). Python always `python -I`.

---------------------------------------------------------------------------------------------------------------------

## 0. Decisions at a glance

| topic | decision |
|---|---|
| boot loader | GRUB 2.12 x86_64-efi **standalone** image (`grub-mkstandalone`) at `\EFI\BOOT\BOOTX64.EFI`, embedded early config finds the payload by a **build-unique marker file** `/gbsb3/GBSB3-<build_id>.marker` (never by FS UUID or label). BOOTX64.EFI MUST NOT advertise NX_COMPAT |
| ISO | `xorriso -as mkisofs`, ISO9660 + Rock Ridge + Joliet, volume ID `GBSB3INST`, El Torito EFI no-emulation image (< 32 MiB) = appended FAT ESP partition, GPT for dd mode. No BIOS boot. |
| kernel | Ubuntu `6.8.0-147-generic` from the builder; NX_COMPAT flag **cleared** in the ISO copy (Surface + GRUB + NX risk, see README_DUALBOOT); command line blacklists the built-in TPM driver initcalls and sets `iommu.passthrough=1 reboot=pci` like the native Surface cmdline |
| initramfs | busybox (static, from the builder) as `init` (PID 1, generated inittab), **Python 3.12** (from the builder, pruned stdlib) for all disk logic, plus sgdisk, efibootmgr, zstd, util-linux (blkid, lsblk, losetup, blockdev, partx), kmod, setfont, fsck.vfat, ntfs-3g's read-only `ntfsls`/`ntfscat` — each with its shared libraries |
| disk safety | every external command goes through one allow-list (`run_tool`, 4.0.10) and BUILD greps the installer for forbidden tools/ioctls; the kernel read-only flag (BLKROSET) is set on every partition and disk the flow does not write; Windows NTFS volumes are never mounted by the kernel (ntfs-3g userspace, read-only); protected disks come from a separate full scan for any installer medium or ISO file of any build; Windows/recovery media, GB-backup sticks, Ventoy sticks and BitLocker volumes are refused; a non-USB disk must be blank for mode 1; AC power is required; a RAM journal records the phase reached |
| payload | 13 content objects (both ESPs + 11 partitions shared by both modes), each split into raw 512 MiB chunks compressed independently with zstd; manifest with raw and compressed sha256 per chunk and per object; GPT of the USB image stored as two small files |
| content source | **the verified USB image** (`googlebook-sb3-usb.img`, sha256 `cad193d6…0d45`) for every partition, plus the internal ESP from a `build_internal.py` export. lapis.bin is only read by build_internal.py; its export is cross-checked byte-for-byte against the USB image |
| whole-disk mode | writes exactly the USB image (same GPT, same disk/partition GUIDs); on a larger disk the backup GPT moves to the end and userdata may grow (same name/GUID). A blank target of exactly the image size ends up **byte-identical** to the image |
| dual-boot mode | same partitions, GUIDs, names, types, attributes and **placement** as `install_dualboot.ps1`/`gbdisk plan` (port of `PlanNew`), into unallocated space the user made in Windows. Data first, GPT last; new entries in the free GPT slots **after the highest used slot**; existing 128-byte entries kept byte-identical in their slots; EFI entry "Googlebook OS" created with `efibootmgr --create-only` and appended to BootOrder **after a verified, active Windows Boot Manager entry** (refused without one) |
| Windows interop | state written to `<new ESP>\gbinstall\` in gbdisk/install_dualboot formats; new `Import-GooglebookIsoInstall.ps1` reads it (from the ESP itself by `-EspId`, no drive letter, or from a folder) into `C:\GB-backup`; `uninstall_dualboot.ps1` gets two small guards; `Restart-ToGooglebook.ps1` and `install_dualboot.ps1` unchanged |
| UI | plain line-oriented text menu (no curses, no ANSI escapes) on tty1; on VMware test entries (`gb.serial=1`) on ttyS0 with machine-readable `@@GB` marker lines; tty1 then mirrors the log |
| auto-install | none in v1 (tests drive the serial menu, typing the same confirmations a user types) |

---------------------------------------------------------------------------------------------------------------------

## 1. Payload format on the ISO

### 1.1 ISO file tree (paths relative to the ISO root; all names lower case except the marker and EFI paths)

```
/EFI/BOOT/BOOTX64.EFI                 GRUB standalone image (same bytes as inside the appended ESP image)
/boot/grub/grub.cfg                   menu (section 2.2)
/boot/vmlinuz                         Ubuntu 6.8.0-147-generic kernel, NX_COMPAT cleared (section 2.5)
/boot/initrd.img                      installer initramfs (newc cpio, zstd)
/boot/boot.cat                        El Torito catalog (xorriso)
/gbsb3/GBSB3-<build_id>.marker        marker (1.2)
/gbsb3/manifest.json                  payload manifest (1.4)
/gbsb3/SHA256SUMS                     sha256sum(1) format, every file of the ISO tree except itself and boot.cat
/gbsb3/wd/gpt-primary.bin             USB image LBA 0..33   (17408 bytes: protective MBR, GPT header, 128x128 entries)
/gbsb3/wd/gpt-backup.bin              USB image last 33 LBAs (16896 bytes: 128x128 entries, then the backup header)
/gbsb3/dualboot/manifest.json         the build_internal.py export manifest, verbatim (gb-dualboot-manifest/1)
/gbsb3/parts/<object>.<NNN>.zst       compressed chunks, NNN = 000, 001, ... (1.3)
/README.txt                           short text: what this is, personal use only, see README-ISO.md
```

Every file MUST be smaller than 1 GiB (FAT32 / Rufus ISO mode limit is 4 GiB; we keep a wide margin).

### 1.2 Identity: volume label, build id, marker

* **Volume ID** (ISO9660 PVD bytes 40..71, space padded): `GBSB3INST`. Also the GRUB/Rufus label. Not used to find files.
* **build_id**: `YYYYMMDD-HHMMSS-xxxxxxxx` = UTC time of the build start + 8 lower-case hex digits from `os.urandom`.
  Example `20261025-153000-1a2b3c4d`. Baked into: the marker file name, `early.cfg` (inside BOOTX64.EFI), `grub.cfg`,
  `/etc/gb/build.env` in the initramfs, `manifest.json`, the build record.
* **Marker** `gbsb3/GBSB3-<build_id>.marker`, ASCII, LF line ends, exactly these lines:

```
GBSB3-PAYLOAD-MARKER 1
build_id=<build_id>
volume_label=GBSB3INST
manifest=gbsb3/manifest.json
manifest_sha256=<sha256 hex of gbsb3/manifest.json>
```

A payload root is **valid** iff the marker exists, parses, `build_id` equals the initramfs's `GB_BUILD_ID`, and the sha256
of `gbsb3/manifest.json` equals `manifest_sha256`. Manifest `build_id` must match too.

### 1.3 Objects and chunks

The content objects (object id -> raw source):

| object id | raw source (BUILD) | raw_size today |
|---|---|---|
| `esp-usb` | USB image partition `EFI-SYSTEM` (whole partition) | 536870912 |
| `esp-internal` | export `EFI-SYSTEM.img` (gb.boot=internal, boot_part_uuid = export super GUID) | 536870912 |
| `boot_a`, `boot_b` | USB image partition of that name, first `file_size` bytes of the export manifest | 67108864 each |
| `init_boot_a`, `init_boot_b` | same | 8388608 each |
| `vendor_boot_a`, `vendor_boot_b` | same | 33554432 each |
| `vbmeta_a`, `vbmeta_b` | same | 4194304 each |
| `pvmfw_a`, `pvmfw_b` | same | 4194304 each |
| `super` | same | 12884901888 |

The 11 shared objects serve both modes (whole-disk and dual-boot partitions have identical content; BUILD proves it,
1.5). Blank partitions (misc, metadata, frp_persist, desktop_security_persist, desktop_security_storage, userdata) have
no object.

* An object's raw bytes are cut into chunks at raw offsets `k * 536870912` (last chunk shorter). Each chunk is
  compressed **independently**: `zstd -q -9 -T3 --long=27 -c` (BUILD may raise the level, never lower `--long`).
  Decompression: `zstd -dcq --long=27 <file>`; a non-zero exit status is a payload error.
* File name `gbsb3/parts/<object>.<NNN>.zst`, NNN zero-padded to 3 digits.
* Object `sha256` = sha256 over all `raw_size` raw bytes in order. Chunk `raw_sha256` = sha256 of that chunk's raw
  bytes, `zst_sha256` = sha256 of the compressed file.
* A partition whose size exceeds its object's `raw_size` is zero-filled from `raw_size` to its end.

### 1.4 `gbsb3/manifest.json` (written by BUILD, read by INSTALLER and TEST)

JSON, UTF-8, integers as JSON integers (never floats or strings), GUIDs lower case with hyphens, attributes as the
string `"0x%016x"`, paths relative to the payload root with `/`. Keys (all MUST be present):

```jsonc
{
  "format": "gbsb3-iso-payload/1",
  "build_id": "20261025-153000-1a2b3c4d",
  "created_utc": "2026-10-25T15:30:00Z",
  "volume_label": "GBSB3INST",
  "android_build": "CL3B.260622.276.R4",
  "system_slot": "b",                       // the slot the USB image boots (from its ESP entry gb-<slot>)
  "kernel_release": "6.8.0-147-generic",
  "kernel_nx_compat_cleared": true,
  "sector_size": 512,
  "align_bytes": 1048576,
  "chunk_raw_bytes": 536870912,
  "compression": {"tool": "zstd", "level": 9, "long": 27, "version": "1.5.5"},
  "source": {
    "usb_image": {"file": "googlebook-sb3-usb.img", "size": 28814885376,
                  "sha256": "cad193d629c6d05340841718bc569a51802878b96ed64fa58e7135cf49460d45"},
    "dualboot_export": {"manifest_sha256": "<sha256 of gbsb3/dualboot/manifest.json>",
                        "created": "<export manifest 'created'>", "esp_sha256": "<= objects['esp-internal'].sha256>"}
  },
  "objects": {
    "super": {
      "raw_size": 12884901888,
      "sha256": "<hex>",
      "chunks": [
        {"path": "gbsb3/parts/super.000.zst", "raw_offset": 0, "raw_size": 536870912,
         "raw_sha256": "<hex>", "zst_size": 491234567, "zst_sha256": "<hex>"}
        // ... 24 chunks for super, ordered by raw_offset, contiguous, covering raw_size exactly
      ]
    }
    // "esp-usb", "esp-internal", "boot_a", ... "pvmfw_b": same shape
  },
  "whole_disk": {
    "image_bytes": 28814885376,
    "total_lba": 56279073,
    "disk_guid": "<USB image disk GUID>",
    "first_usable_lba": 34,
    "last_usable_lba": 56279039,
    "entries_lba": 2,
    "backup_entries_lba": 56279040,
    "entry_count": 128,
    "entry_size": 128,
    "entries_crc32": "0x99077776",
    "gpt_primary": {"path": "gbsb3/wd/gpt-primary.bin", "bytes": 17408, "sha256": "<hex>"},
    "gpt_backup":  {"path": "gbsb3/wd/gpt-backup.bin",  "bytes": 16896, "sha256": "<hex>"},
    "super_guid": "<USB image super GUID = the USB ESP entry's androidboot.boot_part_uuid>",
    "partitions": [                          // all 18, in GPT slot order 0..17
      {"slot": 0, "name": "EFI-SYSTEM", "type": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b", "guid": "<hex guid>",
       "attributes": "0x0000000000000000", "first_lba": 2048, "last_lba": 1050623, "size": 536870912,
       "object": "esp-usb", "blank": false, "growable": false}
      // ... slot 11..15 and 17 have "object": null, "blank": true; only slot 17 (userdata) has "growable": true
    ]
  },
  "dual_boot": {
    "export_manifest": "gbsb3/dualboot/manifest.json",
    "export_manifest_sha256": "<hex>",
    "esp_guid": "<export EFI-SYSTEM GUID>",
    "super_guid": "<export super GUID>",
    "cmdline": "<export manifest cmdline>",
    "reserve_bytes": 1073741824,
    "userdata_min_bytes": 8589934592,
    "boot_entry": {"label": "Googlebook OS", "loader": "\\EFI\\BOOT\\BOOTX64.EFI"},
    "partitions": [                          // all 18, in export manifest order (= placement order)
      {"name": "EFI-SYSTEM", "type": "c12a7328-...", "guid": "<hex>", "attributes": "0x0000000000000000",
       "size": 536870912, "fill": false, "min_size": null, "blank": false, "object": "esp-internal",
       "file": "EFI-SYSTEM.img", "file_size": 536870912, "sha256": "<export sha256>"}
      // misc etc.: "blank": true, "object": null, "file": null, "file_size": null, "sha256": null
      // userdata: "size": null, "fill": true, "min_size": 8589934592, "blank": true, "object": null
    ]
  }
}
```

Today's whole-disk layout (from the image, for orientation; the manifest is authoritative): EFI-SYSTEM 2048..1050623,
boot_a 1050624..1181695, boot_b ..1312767, init_boot_a ..1329151, init_boot_b ..1345535, vendor_boot_a ..1411071,
vendor_boot_b ..1476607, vbmeta_a ..1484799, vbmeta_b ..1492991 (attrs 0x010f000000000000), pvmfw_a ..1501183,
pvmfw_b ..1509375, misc ..1517567, metadata ..1648639, frp_persist ..1652735, desktop_security_persist ..1685503,
desktop_security_storage ..1751039, super 1751040..26916863, userdata 26916864..56276991; LBA 34..2047 and
56276992..56279039 are zero; total 56279073 sectors.

### 1.5 The dual-boot export: how it is generated and checked (BUILD)

The ISO bakes ONE dual-boot export (fixed GUID set per export). To keep those GUIDs stable across ISO rebuilds (so a
later ISO can uninstall an earlier ISO's install), BUILD keeps the export at a fixed place and reuses it:

* Location: `C:\GB-x86\iso\work\export\` holding only `manifest.json`, `EFI-SYSTEM.img`, `export_check.json`.
* Default: reuse it if present and it passes the cross-checks below. `--new-export` forces a fresh one. There is **no**
  option to import an export made elsewhere (v1's `--reuse-export` is removed): every export the ISO carries was
  created by the fresh-export command below, so its GUIDs are new random GUIDs that no Windows-made install carries.
* Fresh export, exact command (cmd syntax; BUILD runs it via subprocess with the environment variable set):

```
set GB_SLOT=<system slot of the USB image, today: b>
python -I -B C:\GB-build\native\dualboot\build_internal.py --out C:\GB-x86\iso\work\export.new
```

  This uses the default internal ESP: systemd-boot entry `gb-<slot>` with `gb.hw=native gb.boot=internal` and the
  strict selector `androidboot.boot_part_uuid=<this export's super GUID>` (no `--boot-devices`, no
  `--clear-nx-compat`, no `--test-*`, never `--reuse-guids`). It reads lapis.bin (the running VM may hold it;
  read-only) and needs ~12.6 GiB on C: temporarily. After the cross-checks pass, BUILD deletes every `*.img` except
  `EFI-SYSTEM.img`, then renames `export.new` -> `export` (an old `export` is renamed `export.prev-<created>` first,
  then deleted).
* Cross-checks (abort the build on any failure, with the message in brackets):
  1. manifest `format` == `gb-dualboot-manifest/1`, `test_truncated` false, `disk_selector` == `boot_part_uuid`,
     `cmdline` contains `gb.boot=internal`, `gb.hw=native` and `androidboot.boot_part_uuid=<its super guid>`.
  2. partition names in order == `EFI-SYSTEM, boot_a, boot_b, init_boot_a, init_boot_b, vendor_boot_a, vendor_boot_b,
     vbmeta_a, vbmeta_b, pvmfw_a, pvmfw_b, misc, metadata, frp_persist, desktop_security_persist,
     desktop_security_storage, super, userdata`; userdata `fill` true, `min_size` 8589934592.
  3. `slot` == the USB image's slot (the USB ESP raw bytes contain `initrd  /initrd-<slot>.img`).
  4. for each of the 11 sourced partitions: export `type` and `attributes` == the USB image GPT entry's; export
     `sha256` == sha256 of the USB image partition's first `file_size` bytes; the rest of that USB partition is zero.
     ["the USB image and lapis.bin differ (an OTA in the running VM?): rebuild and verify the USB image first"]
  5. sha256(`EFI-SYSTEM.img`) == manifest sha256, size 536870912.
  6. no export GUID equals any USB image partition GUID or its disk GUID. Informational (never aborts, read-only):
     every file that exists among `D:\gb-export\manifest.json`, `C:\gb-export\manifest.json` and
     `C:\GB-backup\**\manifest_installed.json` (recursive glob) is read, and every export GUID found in one of them is
     listed under `guid_overlap` in export_check.json with the file path and a warning line in the build log (an
     overlap can only come from an ISO install that was imported and maintained from Windows, i.e. the same install).
  7. `export_check.json` records the results (incl. `guid_overlap`, an empty list when none) + the USB image sha256
     they were checked against.

### 1.6 Size budget

Sampling of the image (1 MiB every 64 MiB): super is 37 % zero blocks; the rest compresses poorly (zlib-1 ratio 0.89).

| item | raw | compressed (estimate) |
|---|---|---|
| super | 12.0 GiB | 6.6–6.9 GiB |
| esp-usb + esp-internal | 2 x 512 MiB (189 MiB used each) | ~0.37 GiB |
| boot/init_boot/vendor_boot/vbmeta/pvmfw (10) | 224 MiB | ~0.1 GiB |
| kernel + initrd + GRUB + efiboot.img | | ~0.06 GiB |
| **ISO** | | **7.1–7.5 GiB** (hard ceiling 8.0 GiB: BUILD aborts above it) |

Consequences: the ISO needs a >= 16 GB stick for Rufus/dd; a Ventoy stick needs an exFAT (default) or NTFS Ventoy
partition with free space >= the ISO size (about 8.1 GB; a FAT32 Ventoy partition cannot hold it). Disk budget: D:
holds the ISO (~7.5 GiB) plus at most one test disk set at a time (<= 13.5 GiB physical, sparse); C: holds a fresh
export temporarily (12.6 GiB); the builder holds the chunk cache (~7.2 GiB) + one ISO (~7.5 GiB) + one test medium
(~9 GiB).

---------------------------------------------------------------------------------------------------------------------

## 2. Boot chain

### 2.1 GRUB standalone image with embedded early config

`early.cfg` (template `C:\GB-x86\iso\build\templates\early.cfg.in`, `@BUILD_ID@` substituted), embedded as
`/boot/grub/grub.cfg` of the memdisk:

```
set gb_marker=/gbsb3/GBSB3-@BUILD_ID@.marker
insmod part_gpt
insmod part_msdos
insmod iso9660
insmod fat
insmod exfat
insmod ntfs
insmod search_fs_file
if search --no-floppy --file --set=gbroot "$gb_marker"; then
  set root="$gbroot"
  configfile "($gbroot)/boot/grub/grub.cfg"
else
  echo "Googlebook installer: $gb_marker was not found on any disk."
  echo "The installer files are incomplete (the gbsb3 folder must be copied too)."
  echo "Firmware setup in 60 seconds; press Esc for the GRUB command line."
  if sleep --interruptible 60; then
    insmod efifwsetup
    fwsetup
  fi
fi
```

(Esc in our menu returns from `configfile` and ends the script: GRUB then shows its command line, no wrong message.)
`$prefix` MUST stay `(memdisk)/boot/grub` (never set it to the medium): every module comes from the memdisk.

Build command (BUILD; on the builder):

```
grub-mkstandalone -O x86_64-efi -o BOOTX64.EFI --locales="" --fonts="" --themes="" \
  --modules="part_gpt part_msdos iso9660 fat exfat ntfs search search_fs_file configfile normal echo test sleep" \
  --install-modules="part_gpt part_msdos iso9660 fat exfat ntfs search search_fs_file search_label configfile normal
                     linux echo test true sleep reboot halt efifwsetup serial terminfo terminal smbios all_video
                     efi_gop efi_uga video_fb regexp ls cat minicmd" \
  "boot/grub/grub.cfg=early.cfg"
```

`--install-modules` pulls in the dependency closure (boot, relocator, mmap, video, extcmd; confirmed by the boot
review on the builder). No `--compress` option (the memdisk tar stays uncompressed, so T0 can grep it).
BUILD checks (abort on failure; T0 re-checks): (a) **BOOTX64.EFI's PE DllCharacteristics (offset `e_lfanew + 24 + 70`,
PE32+) has bit 0x0100 (NX_COMPAT) clear** (today 0x0000); (b) no `tpm`, `shim_lock` or `gfxterm` module file in the
memdisk: the strings `tpm.mod`, `shim_lock.mod` and `gfxterm.mod` do not occur in BOOTX64.EFI (Ubuntu 2.12 has no
shim_lock.mod at all — its shim_lock check is part of the core and inactive with Secure Boot off; tpm.mod exists in
`/usr/lib/grub/x86_64-efi` and must stay out). The DllCharacteristics value is recorded in the build record.

### 2.2 `/boot/grub/grub.cfg` (template `C:\GB-x86\iso\build\templates\grub.cfg.in`)

```
set gb_build=@BUILD_ID@
set timeout=10
set timeout_style=menu
set default=gb_normal
insmod all_video
insmod smbios
set gb_vm=0
if smbios --type 1 --get-string 4 --set gb_vendor; then
  if [ "$gb_vendor" = "VMware, Inc." ]; then set gb_vm=1; fi
fi
if [ "$gb_vm" = "1" ]; then
  if serial --unit=0 --speed=115200 --word=8 --parity=no --stop=1; then
    terminal_input console serial
    terminal_output console serial
  fi
fi
set gb_args="gb.build=${gb_build} loglevel=4 panic=0 reboot=pci iommu.passthrough=1 efi_pstore.pstore_disable=1 initcall_blacklist=init_tis,crb_acpi_driver_init"

menuentry "Googlebook SB3 installer" --id gb_normal --hotkey=n {
  set gfxpayload=keep
  linux /boot/vmlinuz ${gb_args} console=tty0
  initrd /boot/initrd.img
}
menuentry "Googlebook SB3 installer (basic display: nomodeset, kernel font)" --id gb_basic --hotkey=b {
  linux /boot/vmlinuz ${gb_args} console=tty0 nomodeset gb.font=kernel
  initrd /boot/initrd.img
}
if [ "$gb_vm" = "1" ]; then
  menuentry "TEST: installer on serial console ttyS0" --id gb_serial --hotkey=s {
    set gfxpayload=keep
    linux /boot/vmlinuz ${gb_args} console=ttyS0,115200n8 console=tty0 gb.serial=1
    initrd /boot/initrd.img
  }
  menuentry "TEST: serial console, ISO9660 volume discovery off" --id gb_serial_nocd --hotkey=d {
    set gfxpayload=keep
    linux /boot/vmlinuz ${gb_args} console=ttyS0,115200n8 console=tty0 gb.serial=1 gb.discover=marker,isofile
    initrd /boot/initrd.img
  }
  menuentry "TEST: serial console, ISO9660 discovery off, NTFS on any disk" --id gb_serial_ntfs --hotkey=t {
    set gfxpayload=keep
    linux /boot/vmlinuz ${gb_args} console=ttyS0,115200n8 console=tty0 gb.serial=1 gb.discover=marker,isofile gb.ntfs_any=1
    initrd /boot/initrd.img
  }
fi
menuentry "UEFI firmware setup" --id gb_fwsetup --hotkey=f { insmod efifwsetup; fwsetup }
menuentry "Reboot" --id gb_reboot --hotkey=r { reboot }
menuentry "Power off" --id gb_halt --hotkey=p { halt }
```

8 entries in total (3 of them VMware-only). Why the SMBIOS gate: the GRUB serial terminal and the TEST entries exist
only on VMware ("VMware, Inc."). On the Surface (no legacy UART) they never appear, so no stray serial input and no
confusing entries. In the TEST entries the last `console=` is `tty0`, so `/dev/console` (busybox init messages, stray
console writes) is tty0 and never splits `@@GB` lines on ttyS0; the UI owns ttyS0 through inittab.

`gb_args` rationale: `reboot=pci` (the SB3 hangs with other reboot methods; native cmdline in build_usb.py),
`iommu.passthrough=1` (native cmdline; Ubuntu has CONFIG_INTEL_IOMMU_DEFAULT_ON=y), `efi_pstore.pstore_disable=1`,
`initcall_blacklist=init_tis,crb_acpi_driver_init` (the built-in TPM drivers CONFIG_TCG_TIS=y / CONFIG_TCG_CRB=y never
probe the Surface fTPM; with no TPM, IMA runs in bypass mode). No `fbcon=font:` (the kernel picks TER16x32 itself on
large modes and 8x16 on small ones; /init chooses the console font, 3.5).

### 2.3 Kernel command line parameters (shared interface)

| parameter | values | consumer | meaning |
|---|---|---|---|
| `gb.build=` | build id | INSTALLER | informational; logged; a mismatch with `/etc/gb/build.env` is a warning |
| `gb.serial=1` | 1 | INSTALLER init | UI on `/dev/ttyS0` (only if `/proc/tty/driver/serial` shows ttyS0 with a real `uart:` type, i.e. not `unknown`; otherwise ignored with a warning and the UI stays on tty1); `@@GB` markers printed; tty1 mirrors the log |
| `gb.discover=` | comma list of `iso9660`, `marker`, `isofile` | INSTALLER | enabled payload discovery methods, in this order. Default `iso9660,marker,isofile`. Unknown words are ignored with a warning; an empty result means the default. It never limits the protected-disk scan (3.7) |
| `gb.ntfs_any=1` | 1 | INSTALLER | TEST only: kernel NTFS mounts for payload discovery and the protected scan are allowed on any disk, not only removable/USB ones. Honoured only when `is_vmware` (4.0.17); otherwise ignored with a warning |
| `gb.stick_any=1` | 1 | INSTALLER | TEST only (12.E7): savelog/gptbackup (4.4) may target a non-USB disk (a VMware test disk standing in for a stick). Honoured only when `is_vmware`; otherwise ignored with a warning. No GRUB entry passes it: the harness types it on GRUB's command line |
| `gb.font=` | `auto` (default), `32x64`, `16x32`, `8x16`, `kernel` | INSTALLER init | console font (3.5) |
| `gb.debug=1` | 1 | INSTALLER init | kernel messages stay on the console, verbose log, `/init` drops to a shell instead of halting on a fatal init error |
| `gb.autotest=` | — | INSTALLER | RESERVED. v1 MUST ignore it (log a warning). See 5.5 |
| `nomodeset` | | kernel | see 3.5 (little effect: no DRM driver besides the built-in simpledrm is loaded) |
| `initcall_blacklist=`, `iommu.passthrough=`, `reboot=`, `efi_pstore.pstore_disable=` | as in 2.2 | kernel | 2.2 |

### 2.4 ESP image and hybrid ISO (BUILD)

* `efiboot.img`: FAT image, 8 MiB (16 MiB if BOOTX64.EFI > 6 MiB): `mkfs.vfat -C efiboot.img 8192`, then mtools:
  `mmd -i efiboot.img ::/EFI ::/EFI/BOOT; mcopy -i efiboot.img BOOTX64.EFI ::/EFI/BOOT/BOOTX64.EFI`. The same
  BOOTX64.EFI also goes into the ISO tree as `/EFI/BOOT/BOOTX64.EFI` (Rufus ISO mode copies files; it must find it).
  efiboot.img **MUST be smaller than 32 MiB** (the El Torito load size is a 16-bit count of 512-byte sectors).
* Reference xorriso command (BUILD MAY adjust flags, MUST keep the properties checked below; the boot review ran it
  as written on the builder):

```
xorriso -as mkisofs -iso-level 3 -r -J -joliet-long -V GBSB3INST \
  -volset "GBSB3 <build_id>" -A "Googlebook SB3 installer <build_id> - personal use only" \
  -o googlebook-sb3-installer.iso \
  -partition_offset 16 \
  -append_partition 2 C12A7328-F81F-11D2-BA4B-00A0C93EC93B efiboot.img \
  -appended_part_as_gpt \
  -iso_mbr_part_type EBD0A0A2-B9E5-4433-87C0-68B6B72699C7 \
  -c /boot/boot.cat \
  -e --interval:appended_partition_2:all:: -no-emul-boot \
  <iso tree>
```

* Required properties (BUILD verifies with `xorriso -indev X -report_el_torito plain -report_system_area plain` and
  `sgdisk -p X` / `sgdisk -v X` on the builder, TEST re-checks in T0): El Torito catalog with one platform-0xEF
  no-emulation entry whose `Ldsiz` equals efiboot.img bytes / 512 (16384 for 8 MiB); GPT with a valid protective MBR,
  partition 1 covering the ISO9660 data (starts at LBA 64, mountable as iso9660 with label GBSB3INST), partition 2
  type EFI System containing `/EFI/BOOT/BOOTX64.EFI`; `sgdisk -v` reports no problems; PVD volume ID `GBSB3INST`; ISO
  size multiple of 2048; every file < 1 GiB.

### 2.5 Kernel

`/boot/vmlinuz` = `/boot/vmlinuz-6.8.0-147-generic` from the builder (root-only readable: `sudo cat`), with
`IMAGE_DLLCHARACTERISTICS_NX_COMPAT` (0x0100) cleared at PE offset `e_lfanew + 24 + 70` — same transformation as
`build_internal.py clear_nx_compat()`; today's value 0x0100 -> 0x0000. BUILD records old/new value in the build record;
`--keep-nx-compat` skips it. Rationale: README_DUALBOOT "Boot loader choice" (Surface + GRUB + NX advertised hangs).
BUILD also verifies (read-only, `sudo grep -w`) that `init_tis` and `crb_acpi_driver_init` are symbols in
`/boot/System.map-6.8.0-147-generic` (both present today) and aborts otherwise (a renamed initcall would silently
re-enable the TPM drivers).

---------------------------------------------------------------------------------------------------------------------

## 3. Initramfs

### 3.1 Layout and the INSTALLER -> BUILD contract

INSTALLER source tree `C:\GB-x86\iso\installer\` (all text files LF-only, UTF-8; BUILD refuses CR bytes):

```
installer\init                       -> /init                       (0755, busybox sh)
installer\bin\gbinstall.sh           -> /opt/gb/bin/gbinstall.sh    (0755)  UI entry, execs Python
installer\bin\gbmirror.sh            -> /opt/gb/bin/gbmirror.sh     (0755)  tty1 log mirror when the UI is on ttyS0
installer\bin\*                      -> /opt/gb/bin/                (0755)
installer\lib\gbinstall.py           -> /opt/gb/lib/gbinstall.py    (0644)
installer\lib\gb\run.py              -> /opt/gb/lib/gb/run.py       (0644)  run_tool(): the ONLY process launcher (4.0.10)
installer\lib\gb\*.py                -> /opt/gb/lib/gb/             (0644)  package (other module names: INSTALLER's choice)
installer\etc\modules.list           -> /etc/gb/modules.list        build input + runtime load list (3.3)
installer\etc\initramfs.list         -> /etc/gb/initramfs.list      build input (3.2), copied for reference
installer\etc\*                      -> /etc/gb/
installer\tests\                     not copied (host unit tests of pure modules, 8.4)
```

Generated by BUILD inside the initramfs:

```
/etc/gb/build.env            sh-sourceable, exactly these keys:
                               GB_BUILD_ID=<build_id>
                               GB_VOLUME_LABEL=GBSB3INST
                               GB_MARKER=gbsb3/GBSB3-<build_id>.marker
                               GB_KREL=6.8.0-147-generic
                               GB_ANDROID_BUILD=CL3B.260622.276.R4
/etc/gb-installer-release    one line: "Googlebook SB3 installer <build_id>"
/etc/gb/modules.order        resolved insmod order (relative .ko paths), for logs/diagnostics only
/lib/modules/6.8.0-147-generic/   decompressed .ko files of the resolved set, modules.builtin, modules.builtin.modinfo,
                             modules.order (copied from the builder) + depmod output (modules.dep, .alias, ...)
/usr/share/consolefonts/gb-ter-32x64.psf          Lat15-TerminusBold32x16 pixel-doubled (PSF2, 32x64)
/usr/share/consolefonts/Lat15-TerminusBold32x16.psf.gz
/usr/share/consolefonts/Lat15-Terminus16.psf.gz   8x16
/bin/busybox                 + one symlink per `busybox --list` applet, in /bin only
/bin, /sbin                  REAL directories (the builder's merged-usr /bin -> usr/bin and /sbin -> usr/sbin links
                             are NOT reproduced); /sbin holds only the kmod links from initramfs.list
/usr/bin, /usr/sbin, /usr/lib/...   real tools and their libraries at their builder paths (merged-usr symlinks
                             /lib -> usr/lib and /lib64 -> usr/lib64 exactly as on the builder)
/dev /proc /sys /run /tmp /mnt (empty dirs)
```

No `/ventoy` path is ever created by BUILD (Ventoy's injected hook may create one at boot; it is ignored, 3.4).

PATH inside: `/usr/sbin:/usr/bin:/sbin:/bin` (real tools win over busybox applets).

The UI entry point MUST refuse to run outside the installer environment: `/etc/gb-installer-release` exists,
`os.path.realpath("/proc/1/exe") == os.path.realpath("/bin/busybox")`, and `/proc/cmdline` contains `gb.build=`.
Otherwise it prints `gbinstall: not inside the Googlebook installer environment - refusing` and exits 3.

`/opt/gb/lib/gbinstall.py --selftest` MUST import every module, run pure self-tests (GPT build/parse round trip, plan
computation on a synthetic layout, manifest parsing of an embedded sample, EFI load-option parse/build round trip,
run_tool argument validation accept/reject tables), touch no device, start no process, and print exactly
`GB-SELFTEST OK` on success (exit 0). BUILD runs it in a chroot of the staging tree (3.2).

### 3.2 Tools, libraries, Python (BUILD implements, INSTALLER declares)

`installer\etc\initramfs.list` grammar (one directive per line, `#` comments):

```
bin  <abs path on builder>                 copy to the same path, plus every library from ldd (symlink chains kept)
lib  <abs path>                            a shared library (+ its ldd deps)
file <src abs path> <dst abs path>         plain file
link <dst abs path> <target>               symlink
python3 /usr/bin/python3.12                the interpreter + every library from `ldd /usr/bin/python3.12` (libm, libz,
                                           libexpat, ...) + the pruned stdlib (rules below)
```

Initial content (INSTALLER may add lines; BUILD MUST support the grammar):

```
python3 /usr/bin/python3.12
link /usr/bin/python3 python3.12
lib  /usr/lib/x86_64-linux-gnu/libcrypto.so.3
bin  /usr/sbin/sgdisk
bin  /usr/bin/efibootmgr
bin  /usr/bin/zstd
bin  /usr/sbin/blkid
bin  /usr/bin/lsblk
bin  /usr/sbin/losetup
bin  /usr/sbin/blockdev
bin  /usr/bin/partx
bin  /usr/bin/kmod
link /sbin/modprobe /usr/bin/kmod
link /sbin/insmod /usr/bin/kmod
link /sbin/lsmod /usr/bin/kmod
link /sbin/modinfo /usr/bin/kmod
bin  /usr/bin/setfont
bin  /usr/sbin/fsck.vfat
bin  /usr/bin/stty
bin  /usr/bin/ntfsls
bin  /usr/bin/ntfscat
```

(`ntfsls`/`ntfscat` are ntfs-3g userspace tools, present on the builder, linked against `libntfs-3g.so.89`; they open
the device read-only. They are the only way the installer reads Windows NTFS volumes, 4.0.8.)

Busybox: `/usr/bin/busybox` of the builder (static, 1.36.1) -> `/bin/busybox`; links for every `busybox --list`
applet in `/bin` only. (Busybox has no `mountpoint` and no `blkid` applet; the real blkid is used.)

Python pruning (BUILD): copy `/usr/lib/python3.12` without `test`, `idlelib`, `tkinter`, `turtledemo`, `ensurepip`,
`lib2to3`, `pydoc_data`, `venv`, `sqlite3`, `curses`, `dbm`, `unittest`, `asyncio`, `multiprocessing`, `concurrent`,
`wsgiref`, `xmlrpc`, `ctypes`, `config-3.12-x86_64-linux-gnu`, `site-packages`, `dist-packages`, `__pycache__`;
`lib-dynload` keeps only `_hashlib`, `_json`, `_contextvars`, `_queue`, `mmap`, `termios`, `resource` (+ their ldd libs).
INSTALLER code MUST restrict itself to modules that survive this (builtins incl. `_sha2`, `zlib`, `_struct`, `fcntl`,
`select`, `_posixsubprocess`, `_opcode`, plus pure-Python stdlib: os, sys, io, json, hashlib, struct, subprocess,
termios, mmap, time, datetime, uuid, shutil, glob, re, threading, errno, stat, signal, textwrap, base64, binascii,
contextlib, dataclasses, enum, functools, itertools, collections, typing, traceback, pathlib, tempfile, string, math).
No ctypes: ioctls go through `fcntl.ioctl`, and these are the ONLY ioctl request numbers INSTALLER code may use:
BLKGETSIZE64 0x80081272, BLKSSZGET 0x1268, BLKRRPART 0x125f, BLKFLSBUF 0x1261, BLKROSET 0x125d, BLKROGET 0x125e,
BLKGETDISKSEQ 0x80081280.

BUILD smoke test (MUST pass, else abort): `sudo chroot <staging> /usr/bin/python3 -I /opt/gb/lib/gbinstall.py
--selftest` prints `GB-SELFTEST OK`; `sudo chroot <staging> /bin/sh -c 'for t in sgdisk efibootmgr zstd blkid lsblk
losetup blockdev partx modprobe setfont fsck.vfat stty ntfsls ntfscat; do command -v $t >/dev/null || exit 1; done'`;
`<staging>/bin` and `<staging>/sbin` are directories and not symlinks, and `<staging>/bin/busybox` is a regular file;
every modules.list name resolves with `modprobe -d <staging> -S 6.8.0-147-generic --show-depends` (built-in names
resolve because modules.builtin* are in staging); the forbidden-token scan (4.0.11) of `C:\GB-x86\iso\installer\`
found nothing; the resolved module set contains no denied module (3.3).

Archive: `cd <staging> && find . | sort | cpio -o -H newc -R 0:0 | zstd -19 -T3 > initrd.img` (kernel has
CONFIG_RD_ZSTD=y; INITRAMFS_SOURCE is empty, so the kernel's built-in cpio provides /dev/console). Expected ~12–16 MB
compressed.

### 3.3 Kernel modules and load order

Ubuntu 6.8.0-147-generic, linux-modules-extra already installed on the builder. Built in (no module needed): xhci_hcd,
ehci/uhci/ohci, sd_mod, sr_mod, cdrom, vfat/fat, nls_cp437, loop, efivarfs, dm_mod, serdev (+ its tty port driver),
8250, i8042, atkbd, evdev, ata_piix, virtio_blk, squashfs, simpledrm/fbcon (TER16x32 font built in; firmware fb is
off, so simpledrm is the only display driver), ACPI AC adapter, TPM (tcg tis/crb — their initcalls are blacklisted on
the command line, 2.2).
(Wave 3, item 1) This kernel's `CONFIG_SERIAL_8250_RUNTIME_UARTS` is 32, so its 8250 driver registers and `8250_dw`
binds the Surface Aggregator's LPSS UART with no command-line help; unlike the native GKI (`RUNTIME_UARTS=0`, whose
boot entries therefore carry `8250.nr_uarts=4`), the installer needs no `8250.nr_uarts`. `remote.sh kernel` refuses a
build whose `/boot/config-6.8.0-147-generic` has 0, the build record keeps the value (`kernel_8250_runtime_uarts`)
and T0 re-checks it (13.10).

`installer\etc\modules.list` (names, load order; INSTALLER owns, this is the initial content):

```
# storage: payload media and targets (VMware: nvme, ahci, pvscsi/mptspi/mptsas; Surface: nvme, xhci + uas/usb-storage)
xhci_pci
usb_storage
uas
vmd
nvme
ahci
vmw_pvscsi
mptspi
mptsas
# file systems: payload discovery and checks (ntfs3: read-only payload discovery on removable/USB disks only, 4.0.8)
isofs
exfat
ntfs3
nls_iso8859_1
nls_utf8
nls_ascii
# input: USB keyboards (PS/2 is built in)
hid
hid_generic
usbhid
hid_multitouch
hid_microsoft
hid_logitech_dj
hid_logitech_hidpp
# Surface Book 3 base keyboard/touchpad: Surface Aggregator Module on the LPSS UART (GPIO irq: pinctrl_icelake)
pinctrl_icelake
intel_lpss_pci
intel_lpss_acpi
8250_dw
surface_aggregator
surface_aggregator_registry
surface_aggregator_hub
surface_hid_core
surface_hid
surface_kbd
# Surface Book 3 power state for the AC check (4.0.15)
surface_charger
surface_battery
```

`vmd` (kernel/drivers/pci/controller/vmd.ko) binds to nothing when Intel VMD is off; it makes an NVMe behind VMD/RST
visible (as a target and, above all, as a disk to protect).

BUILD resolves each with `modprobe -S 6.8.0-147-generic --show-depends <name>` (verified: e.g. `intel_lpss_pci`
-> idma64, intel-lpss, intel-lpss-pci; `surface_hid` -> crc-itu-t, hid, surface_aggregator, surface_hid_core,
surface_hid; `nvme` -> nvme-auth, nvme-core, nvme; `xhci_pci` -> xhci-pci-renesas, xhci-pci; `mptspi` ->
scsi_transport_spi, mptbase, mptscsih; `mptsas` -> scsi_transport_sas, ...; `hid_microsoft` -> ff-memless, hid), copies
the `.ko.zst` files decompressed to `.ko` at the same relative path, copies `modules.builtin`,
`modules.builtin.modinfo` and `modules.order` from `/lib/modules/6.8.0-147-generic/` into staging, then runs
`depmod -b <staging> 6.8.0-147-generic`, and writes the deduplicated insmod order to `/etc/gb/modules.order`. A name
that is neither a module nor built in aborts the build. **Denylist**: BUILD aborts if the resolved module set (module
names with `-` and `_` treated as equal) contains `spi-intel*`, `mtd*`, `efi_test`, `efi_pstore` or `tpm*`.

At boot `/init` runs `modprobe -q <name>` for every name of `/etc/gb/modules.list` in order (kmod resolves the deps),
logs failures (normal on VMware for the Surface modules: they load and bind to nothing) and never stops on one. It then
sets `/proc/sys/kernel/modprobe` to `/sbin/modprobe`: on-demand loads can only come from the included set.

### 3.4 `/init` and inittab (INSTALLER)

`/init` (busybox sh, PID 1; under Ventoy normal mode it is exec'd by Ventoy's injected init, which may already have
mounted /proc, /sys, /dev and may have created `/ventoy` and `/dev/mapper/ventoy`):

1. Mounts, each only if its mount point is not already listed in `/proc/mounts` (for `/proc` itself: only if
   `/proc/self` does not exist); a failed mount is logged and never fatal: `mount -t proc proc /proc`;
   `mount -t sysfs sys /sys`; `mount -t devtmpfs dev /dev`; `mkdir -p /dev/pts`, `mount -t devpts devpts /dev/pts`;
   `mount -t tmpfs -o mode=0755 run /run`; `mount -t tmpfs tmp /tmp`. Then, if `/sys/firmware/efi` exists: efivarfs
   already mounted -> `mount -o remount,ro,nosuid,nodev,noexec /sys/firmware/efi/efivars`, else
   `mount -t efivarfs -o ro,nosuid,nodev,noexec efivarfs /sys/firmware/efi/efivars` (read-only from boot on; only
   4.2.10 and 4.3.5 remount it rw, 4.0.12).
   `mkdir -p /run/gb/log /run/gb/probe /run/gb/backup /run/gb/state`. Everything is logged to `/run/gb/log/init.log`
   (lines produced before /run exists are buffered and written first). init.log also records `/proc/cmdline`,
   `/proc/mounts`, `ls -l /dev/mapper`, `ls -la /ventoy` (if it exists) and the realpath of `/proc/1/exe`, so a
   failure on a real Ventoy stick can be diagnosed. A `/ventoy` directory is otherwise ignored.
2. Parse `/proc/cmdline` into `/run/gb/cmdline.env` (`GB_SERIAL`, `GB_DISCOVER`, `GB_FONT`, `GB_DEBUG`, `GB_NTFS_ANY`).
3. Load modules (3.3). Console font (3.5).
4. Choose the UI console: `ttyS0` if `gb.serial=1` and ttyS0 is a real UART, else `tty1`. Create `/run/gb/markers`
   (empty file) iff the UI console is ttyS0.
5. Unless `gb.debug=1`: `echo "1 4 1 7" > /proc/sys/kernel/printk` (kernel messages off the console; dmesg kept).
6. Write `/etc/inittab`:

```
::sysinit:/bin/true
<ui-tty>::respawn:/opt/gb/bin/gbinstall.sh --console <ui-tty>
tty1::respawn:/opt/gb/bin/gbmirror.sh            # only when <ui-tty> is ttyS0
::ctrlaltdel:/opt/gb/bin/gbinstall.sh --shutdown reboot
::shutdown:/opt/gb/bin/gbinstall.sh --shutdown cleanup
```

7. `exec /bin/busybox init`.

`gbinstall.sh`: on ttyS0 first `stty 115200 cs8 -parenb -cstopb -crtscts icrnl onlcr`; then
`exec /usr/bin/python3 -I /opt/gb/lib/gbinstall.py "$@"`. If Python exits non-zero, the traceback is in the log; init
respawns the UI. `--shutdown cleanup`: `sync`, unmount `/run/gb/esp`, `/run/gb/savelog`, the payload and probe mounts,
detach loop devices (busybox init then performs its own reboot/halt). `--shutdown reboot|poweroff` (tools menu,
Ctrl+Alt+Del): cleanup, then `reboot -f` / `poweroff -f`.

UI start sequence (every UI start, including a respawn after a crash): (1) clean up a previous instance: unmount every
mount below `/run/gb/` (deepest first), detach every loop device whose backing file lies below `/run/gb/probe/`, remount
efivarfs read-only; (2) read-only guard pass (3.10); (3) payload discovery (3.6); (4) protected-disk scan (3.7);
(5) journal check (4.0.14); (6) main menu.

### 3.5 Console and font

The installer loads no GPU driver: the console is fbcon on the firmware framebuffer (simpledrm, built in), i.e. the
native 3000x2000 on the Surface, the GOP mode in VMware. `/init` reads `/sys/class/graphics/fb0/virtual_size`
(`W,H`; missing -> kernel font) and walks this ladder:

| level | font | auto condition |
|---|---|---|
| L3 | `/usr/share/consolefonts/gb-ter-32x64.psf` | W >= 2560 and H >= 1536 (Surface: 93x31 characters) |
| L2 | `/usr/share/consolefonts/Lat15-TerminusBold32x16.psf.gz` | W >= 1280 and H >= 768 |
| L1 | `/usr/share/consolefonts/Lat15-Terminus16.psf.gz` (8x16) | otherwise |
| L0 | the kernel's font (no setfont) | `gb.font=kernel` only, or after L1 failed |

`gb.font=auto` starts at the highest level whose condition holds; `gb.font=32x64|16x32|8x16` starts at L3/L2/L1. At
each level: `setfont -C /dev/tty1 <file>`; if setfont fails (e.g. the tall 32x64 font needs KD_FONT_OP_SET_TALL; a
plain set with height 64 returns EINVAL) or `stty size </dev/tty1` reports fewer than 24 rows or 80 columns, go one
level down. L0 is accepted as it is. The result is logged (`font=<file|kernel> cols=<n> rows=<n>`). The UI MUST fit
80x24 and print no ANSI escapes. Progress output (tty1 and ttyS0) is at most one line per payload chunk or per 5 s,
whichever is less frequent (fbcon on simpledrm redraws the whole 3000x2000 screen on every scroll); markers are not
rate-limited.

### 3.6 Payload discovery (INSTALLER)

Runs in the UI at start (progress shown) and again on "rescan". Waits for devices: repeat the whole pass every 2 s
until a valid payload is found or 30 s have passed (USB sticks enumerate late); then shows the "no payload" menu
(rescan / shell / reboot / power off). Methods in `gb.discover` order; first valid hit wins; within a method,
candidates are tried in sorted kernel-name order.

**Probe devices**: every entry of `/sys/class/block/` (whole disks, partitions, `sr*`, `dm-*`, `md*`) except `loop*`,
`ram*`, `zram*`, `nbd*`, `mtdblock*`, `mmcblk*boot*`, `mmcblk*rpmb` and size 0. **Whole-device rule**: a whole device
for which the kernel created partition devices (any `/sys/block/<d>/<x>/partition` file exists) is NOT probed itself,
only its partitions are (on a dd-written stick both `sda` and `sda1` report iso9660 GBSB3INST, and a whole-disk mount
would claim the disk exclusively). A whole device without kernel partitions (`sr0`, `dm-*`, an unpartitioned stick) is
probed as a whole, even if blkid reports a PTTYPE (the ISO's own hybrid GPT shows up on `sr0` and on Ventoy's dm map).

**`ntfs_allowed(disk)`** := the disk is USB (4.0.17), or (`gb.ntfs_any=1` and `is_vmware`).

Before any probe mount the read-only guard (3.10) is set on the device. Mount points `/run/gb/probe/<kname>`.

* **iso9660**: probe devices where `blkid -p -o export <dev>` gives `TYPE=iso9660` and `LABEL=GBSB3INST`:
  `mount -t iso9660 -o ro <dev> /run/gb/probe/<kname>`; valid marker (1.2) -> FOUND. (Under Ventoy normal mode this
  finds `/dev/dm-N` = `/dev/mapper/ventoy`, Ventoy's map of the ISO file.)
* **marker**: probe devices with `TYPE` `vfat` or `exfat`, and `ntfs` only if `ntfs_allowed`: mount read-only
  (`-t vfat -o ro,shortname=mixed,utf8`, `-t exfat -o ro`, `-t ntfs3 -o ro`); valid marker -> FOUND; else unmount.
* **isofile** (Ventoy and similar): the same file systems; walk at most 6 directory levels below the root (Ventoy
  accepts any depth), at most 10 s wall-clock per file system, skipping `System Volume Information`, `$RECYCLE.BIN`,
  `Windows`, `Program Files`, `Program Files (x86)`, `ProgramData`, `Users` (case-insensitive); consider regular files
  whose name ends in `.iso` (case-insensitive) and size >= 64 MiB; read 2048 bytes at offset 32768: bytes 1..5 ==
  `CD001` and bytes 40..71 stripped of spaces == `GBSB3INST` -> `losetup -r -f --show <file>` -> `mount -t iso9660 -o
  ro /dev/loopN /run/gb/probe/loopN`; valid marker -> FOUND (the hosting file system stays mounted read-only
  underneath).

Mount failures: EBUSY ("Device or resource busy": the device is held, e.g. by Ventoy's dm map) is not fatal — the
device's leaf disks (3.7) and those of every holder in `/sys/class/block/<dev>/holders/*` are added to `protected`
and discovery continues. Any other mount error is logged; discovery continues.

On FOUND: `/run/gb/payload` is a symlink to the payload root mount; every other probe mount is unmounted; the result
goes to `/run/gb/payload.json`:

```json
{"kind": "iso9660|marker|isofile", "dev": "/dev/sr0", "disk": "sr0", "file": null, "mount": "/run/gb/probe/sr0",
 "build_id": "<id>", "protected": ["sr0"]}
```

`disk` is the payload device's leaf disk (for `dm-N` the leaf disk of its slaves, for `loopN` the leaf disk holding the
backing file); `protected` is the list computed by 3.7 (sorted knames).

### 3.7 Protected disks: separate full scan (INSTALLER)

Computed by its own pass, independent of `gb.discover` and of which candidate discovery hit first. It runs after
discovery (FOUND or not) and again before every disk listing (3.8). Over all probe devices (3.6 rules, incl. the
whole-device rule, read-only guard first, devices already mounted by the installer are examined through that mount) it
runs all three probes and **accepts any build_id**. A disk is **protected** when any of these holds:

1. a probe device on it is an ISO9660 volume labelled `GBSB3INST`;
2. a file system on it has a `gbsb3` directory at its root (case-insensitive);
3. the isofile walk on one of its file systems finds a file with the `GBSB3INST` PVD label (1.2 check, size >= 64 MiB);
4. it is the leaf disk of the chosen payload, of any installer mount or of any loop backing file the installer uses;
5. a probe mount on it failed with EBUSY (3.6), or it is a leaf disk of a holder of such a device.

(A walk that hits its 10 s limit does not make a disk protected — mode 2/3 never write into existing partitions — but
it gets flag `unscanned` (3.9), which mode 1 and savelog/gptbackup refuse.)

**Leaf-disk resolution**: partition -> its parent disk in sysfs; `dm-*`/`md*` -> recursively through
`/sys/block/<d>/slaves/*` down to whole disks (all of them); `loopN` -> its `loop/backing_file`, the file system holding
that file (major:minor from `/proc/self/mountinfo`), then that device's leaf disks; `sr*` -> itself. Every leaf disk
found is protected. Protected disks are never offered as targets and every flow refuses them (`R_PAYLOAD_DISK`).
Nothing on a protected disk is ever written (logs included); the read-only guard sets their whole devices read-only.

### 3.8 Disk enumeration (INSTALLER)

Candidates: `/sys/block/*` except `loop*`, `ram*`, `zram*`, `sr*`, `dm-*`, `md*`, `nbd*`, `mtdblock*`, `mmcblk*boot*`,
`mmcblk*rpmb` and size 0. Per disk, recorded at listing time: kname, `dev` major:minor, size (BLKGETSIZE64), logical
sector size (BLKSSZGET and `queue/logical_block_size`), `ro`, removable, transport (`usb` if the sysfs path contains
`/usb`, else `nvme`, `sata`, `scsi`, `virtio`, `unknown`), model (`device/model`, stripped; `unknown` if absent),
**`diskseq`** (`/sys/block/<d>/diskseq`), **`headtail_sha256`** = sha256 over LBA 0..2047 followed by the last 2081
LBAs (read with `O_RDONLY|O_DIRECT`; clamped to the disk size), holders (any entry in `/sys/block/<d>/holders/` or
`/sys/block/<d>/<p>/holders/` -> flag `inuse`), and the media scan (3.9) of each partition. Never read or print serial
numbers or WWNs. Listed in kname sort order, numbered from 1. The protected scan (3.7) runs right before each listing.

### 3.9 Media scan (INSTALLER)

Per partition of a disk (the union enumeration of 4.0.16) the installer records: start, size, `blkid -p -o export`
`TYPE`, `LABEL`, `PART_ENTRY_TYPE`, `PART_ENTRY_UUID`. For `TYPE` `vfat` or `exfat` (never NTFS here; disks with an
NTFS boot sector are already refused by `R_WINDOWS_DISK` in mode 1 and are never savelog targets) it sets the
read-only guard, mounts read-only at `/run/gb/probe/<kname>` (or uses an existing installer mount), records used and
total bytes (`os.statvfs`) and walks the tree (same walk as the isofile probe: depth <= 6, 10 s, skip list) to set
partition flags; all name comparisons are case-insensitive; depth 1 = entries of the root:

| flag | condition |
|---|---|
| `winmedia` | any of `/sources/boot.wim`, `/sources/install.wim`, `/sources/install.esd`, `/sources/install.swm`, `/sources/reconstruct*` (any name starting `reconstruct`), `/bootmgr`, `/EFI/Microsoft/Boot/BCD`, `/EFI/Microsoft/Boot/bootmgfw.efi` exists (Windows install media, the Surface USB recovery drive, a Windows ESP) |
| `gbbackup` | a directory named `GB-backup` at depth 1..3, or a file matching `gpt_*_primary.bin`, `install_state.json` or `googlebook_install.json` at depth 1..3 |
| `ventoy` | `LABEL` is `VTOYEFI` (any file system), or `TYPE=exfat` with `LABEL` `Ventoy` |
| `bitlocker` | `blkid -p` `TYPE=BitLocker` (also the old FAT-compatible BitLocker To Go format, OEM ID `MSWIN4.1`), or `-FVE-FS-` at offset 3 of the partition's sector 0 (these are probed for every partition, whatever its TYPE) |
| `unscanned` | the walk hit the 10 s limit (not fully scanned: refused by mode 1 and as a savelog/gptbackup target; does not make the disk protected) |

v2.2 (12.S1): a disk without kernel partitions whose whole-device blkid `TYPE` is vfat/exfat, or whose sector 0 is a
FAT/exFAT boot sector, is scanned as one pseudo-range covering the whole disk (first LBA 0, kname = the disk); the
confirmation listing shows it as "whole-disk file system (no partition table)". (12.S4): a disk whose enumeration
fails (open or read error) stays listed with disk flag `unreadable` and its error; it can never be chosen.

The disk carries the union of its partitions' flags in `@@GB DISK` (5.2). The media scan results feed `R_BITLOCKER`,
`R_WINDOWS_DISK` (bootmgfw.efi), `R_PROTECTED_MEDIA`, savelog/gptbackup eligibility (4.4) and the `wd.confirm`
partition listing. Volume labels are shown on the screen only; they are never written to a log, a marker or a file.

### 3.10 Read-only guard (INSTALLER)

Mechanism: open the node `O_RDONLY|O_CLOEXEC`, `fcntl.ioctl(fd, 0x125d, struct.pack("i", 1))` (BLKROSET), confirm
with BLKROGET (0x125e) == 1, close. Effect in kernel 6.8: userspace `write()`/`pwrite()` on that node fail with EPERM
and read-write mounts of it are refused; it is NOT relied upon to stop I/O that an already mounted file system or a
stacked driver issues itself (the block layer only warns about such bios). It is a second line of defence behind
"never mount read-write, never write outside the gate".

When (each pass is idempotent and logged as `roguard set=<n> cleared=<n>`):

1. before every discovery pass and every protected scan / listing: every partition of every candidate disk, every
   `dm-*` and `md*` device;
2. at the start of every flow (after the disk was picked, before its checks): every pre-existing partition of the
   target (union enumeration ranges that the kernel exposes), every partition of every non-target disk, every `dm-*`
   and `md*` device, and the **whole device** of every protected disk;
3. after every BLKRRPART/`partx -u` (the kernel recreates partition devices without the flag) and whenever a listing
   finds a new device: the same set again — except the partitions this flow just created;
4. cleared (BLKROSET 0) only for the one partition chosen for savelog/gptbackup (4.4), for the duration of that write,
   and set again afterwards.

The target's whole device is never set read-only (all target writes go through the gated whole-disk fd, 4.0.2). The new
partitions of mode 1 / mode 2 are new devices and are not set read-only by the flow that created them.

v2.2 (12.S5): when the guard sets the flag on a whole device that was writable, it records `<kname> <diskseq>` in
`/run/gb/roguard.list`. A listing ignores `/sys/block/<d>/ro` only for a recorded (kname, diskseq) pair, and the flow
start (`Target.prepare`) clears the target's whole-device flag only then; any other read-only flag (set by the user,
the driver or a write-protect switch) -> `R_READONLY`, nothing written.

---------------------------------------------------------------------------------------------------------------------

## 4. Installer flows

### 4.0 Invariants for all flows (MUST)

1. **Open and identity.** The target is opened as the whole-disk device with `O_RDWR|O_EXCL|O_CLOEXEC` only after the
   typed confirmation, and only after unmounting every installer mount on that disk. `O_EXCL` failure -> `R_IN_USE`.
   Right after the open the installer compares with the listing (3.8): kname, `dev` major:minor, size (BLKGETSIZE64),
   logical sector size (BLKSSZGET), model, **diskseq via `ioctl(fd, BLKGETDISKSEQ 0x80081280)`** (u64) == the
   recorded `/sys/block/<d>/diskseq`, and `headtail_sha256` re-read with a second `O_RDONLY|O_DIRECT` fd. Any
   difference -> `REFUSED code=R_DISK_CHANGED` ("the disk changed since it was listed; nothing was written").
2. **Write gate.** Every write goes through one function that asserts `[offset, offset+len)` lies inside an area this
   flow allows, and aborts with `F_INTERNAL` before any byte is written otherwise. All zeroing is a gated `pwrite` of
   zero buffers (never BLKDISCARD/BLKSECDISCARD/BLKZEROOUT or fallocate, 4.0.11). Allowed areas:
   * mode 1: the 18 planned partitions; LBA [0, 2048) and [N - 2081, N); the gap ranges of 4.1.6; (GPT phase) LBA
     [0, 34) and [N - 33, N).
   * mode 2: the 18 planned partitions, which must lie inside the planned span, and the span outside every range of the
     union enumeration (4.0.16) of the original disk; (GPT phase only) LBA [1, 34) and [N - 33, N). LBA 0 never.
   * mode 3: (GPT phase only) LBA [1, 34) and [N - 33, N). Nothing else.
   In modes 2 and 3, after the GPT write sector 0 is re-read with O_DIRECT and MUST be byte-identical to the sector 0
   read when the plan was made (else `F_GPT`).
3. **Re-reads.** Immediately before the first write AND again immediately before the GPT write (4.0.7), LBA 0..33 and
   the last 33 LBAs are re-read with O_DIRECT, and size, sector size and diskseq (ioctl) are re-checked. Expected bytes:
   modes 2/3 — byte-identical to the bytes the plan was made from (both times); mode 1 — before the first write the
   bytes covered by `headtail_sha256`, before the GPT write all zero (zeroed by 4.1.6). A difference before the first
   write -> `REFUSED R_DISK_CHANGED`; before the GPT write -> `FAILED F_GPT` with "the partition table was not changed"
   and no GPT byte written.
4. **Payload integrity**: per chunk, first sha256 of the compressed file == `zst_sha256` (abort before writing anything
   of that chunk otherwise), then decompress (`zstd -dcq --long=27`), hash the raw stream == `raw_sha256`, and after the
   last chunk the object sha256. Any mismatch/IO error -> `F_PAYLOAD`.
5. **Zero ranges** (same rule as gbdisk `ZeroRanges`): blank partition of length L: L <= 256 MiB -> zero [0, L); else
   zero [0, 64 MiB) and [L - 64 MiB, L). Sourced partition: zero [raw_size, L) if raw_size < L.
6. **Read-back**: after writing, `fsync`, `BLKFLSBUF`, then read back with a new fd opened `O_RDONLY|O_DIRECT` using
   page-aligned `mmap` buffers and `os.preadv` (4 MiB): sourced partitions sha256 over raw_size == object sha256; zero
   ranges all zero; GPT areas byte-identical to the bytes written. Any mismatch -> `F_VERIFY`. Each partition prints
   `@@GB VERIFY ...`.
7. **GPT write order**: backup entries, backup header, `fsync`, primary entries, primary header, (whole-disk only:
   MBR sector 0), `fsync`. Then `BLKRRPART`; on EBUSY `partx -u <disk>`; then wait <= 10 s until the kernel's
   partitions match the new table (start/size from `/sys/block/<d>/<part>/start|size`, and `partition` == slot + 1)
   -> else `F_KERNEL_RESCAN` (the table on disk is still correct; message says "reboot the installer"). Then the
   read-only guard pass 3.10.3.
8. **Never written**: protected disks, every pre-existing partition of any disk, the Windows ESP, any EFI variable other
   than the ones named in 4.2.10/4.3.5. **NTFS**: the kernel never mounts NTFS on a disk that is not USB (4.0.17),
   except with `gb.ntfs_any=1` on VMware; where it does (payload discovery and the protected scan, 3.6/3.7) only
   `-t ntfs3 -o ro`, after the read-only guard. Windows NTFS volumes (hibernation check 4.2.2) are read only with
   `ntfsls`/`ntfscat` (ntfs-3g userspace, read-only device open); Windows detection uses OEM IDs and GPT/MBR types.
9. **Refusals** leave every disk and every EFI variable byte-identical (the installer writes nothing before the
   confirmation and runs all checks before it).
10. **Command allow-list.** Every external program is started by `run_tool(argv)` in `installer/lib/gb/run.py`, the
    only code that creates processes (`subprocess.Popen`, `shell=False`, argv[0] an absolute path from the table,
    environment `PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C`, a timeout per call, exit status and output logged —
    binary stdout of ntfscat/zstd is not logged, only its length; for zstd and ntfscat it returns a streaming handle).
    `run.py` exposes one wrapper function per allowed argv form below (e.g. create-boot-entry, set-boot-order,
    delete-boot-entry, fsck-esp); the other modules call only these wrappers and never name a program (4.0.11). Each
    argument is validated by a per-tool rule; anything else -> `F_INTERNAL`, nothing started:

    | program | allowed argv after the program path | used by |
    |---|---|---|
    | `/usr/sbin/blkid` | `-p -o export <node>` | everywhere (read-only) |
    | `/usr/bin/lsblk` | `--json --bytes -o NAME,KNAME,MAJ:MIN,SIZE,RO,RM,TYPE,TRAN,FSTYPE,PARTUUID,MOUNTPOINTS` | logs only |
    | `/usr/sbin/losetup` | `-r -f --show <regular file below /run/gb/probe/>`; `-d /dev/loopN` (only loop devices this UI instance or its predecessor created, 3.4); `-l -J` | 3.6, cleanup |
    | `/bin/mount` | `-t iso9660 -o ro <node> /run/gb/probe/<name>`; `-t vfat -o ro,shortname=mixed,utf8 <node> /run/gb/probe/<name>`; `-t exfat -o ro <node> /run/gb/probe/<name>`; `-t ntfs3 -o ro <node> /run/gb/probe/<name>` (only if `ntfs_allowed`); `-t vfat -o rw,flush <node> /run/gb/esp` (only 4.2.11, after 4.0.13); `-t vfat -o rw,flush,shortname=mixed,utf8 <node> /run/gb/savelog` or `-t exfat -o rw <node> /run/gb/savelog` (only 4.4, after 4.0.13); `-o remount,rw /sys/firmware/efi/efivars` (only inside 4.2.10 / 4.3.5); `-o remount,ro,nosuid,nodev,noexec /sys/firmware/efi/efivars` | |
    | `/bin/umount` | `<directory below /run/gb/>` | |
    | `/usr/bin/zstd` | `-dcq --long=27 <file below the payload root>` | 4.0.4 |
    | `/usr/sbin/sgdisk` | `-v /dev/<target>`; `-p /dev/<target>` | verification, logs |
    | `/usr/bin/efibootmgr` | `--verbose` (list, logs only); `--create-only --disk /dev/<target> --part <efi_partition_number> --label "Googlebook OS" --loader \EFI\BOOT\BOOTX64.EFI` (one argv element each; only 4.2.10.2); `--bootorder <list>` where `<list>` equals the list computed and verified in 4.2.10.3 / 4.2.10.5 (never empty); `--bootnum <XXXX> --delete-bootnum` where XXXX was re-verified as ours (description `Googlebook OS` + HD() GUID == `dual_boot.esp_guid`, read from efivarfs) immediately before; `--delete-bootnext` only when BootNext equals a number this run deleted or created and rolled back | 4.2.10, 4.3.5 |
    | `/usr/bin/partx` | `-u /dev/<target>` | 4.0.7 |
    | `/usr/sbin/fsck.vfat` | `-n <node verified by 4.0.13>` | 4.2.11 |
    | `/usr/bin/ntfsls` | `-a -s -p / <partition node>` | 4.2.2, 4.3.2 |
    | `/usr/bin/ntfscat` | `<partition node> /<name as listed by ntfsls>` (stdout read up to 4096 bytes, then the process is terminated) | 4.2.2, 4.3.2 |
    | `/bin/dmesg`, `/bin/sync` | no arguments | logs, shutdown |
    | `/bin/sh` | `-i` (environment adds `PS1=gbsh# `) | tools `s` only |
    | `/bin/reboot`, `/bin/poweroff` | `-f` | 3.4 |

    Never: efibootmgr `--create`/`-c`, `--remove-dups`/`-D`, `--bootnext`/`-n`, `--delete-bootorder`/`-O`,
    `--active`/`--inactive`, a bare or empty `--bootorder`; sgdisk with any option other than `-v`/`-p`; any program not
    in the table (blkdiscard, wipefs, mkfs*, dd, fdisk, parted, ntfsfix, ntfs-3g, ...).
11. **Forbidden tokens (BUILD enforces, TEST re-checks in T0).** BUILD scans every file below
    `C:\GB-x86\iso\installer\` except `installer\tests\` line by line with these Python regular expressions
    (case-sensitive unless marked) and aborts the build on any hit, printing file:line:
    `\bblkdiscard\b`, `\bwipefs\b`, `\bmkfs`, `\bmke2fs\b`, `\bmkdosfs\b`, `\bmkntfs\b`, `\bmkexfatfs\b`, `\bmkswap\b`,
    `\bshred\b`, `\bdd\b`, `\bfdisk\b`, `\bsfdisk\b`, `\bcfdisk\b`, `\bgdisk\b`, `\bparted\b`, `\bntfsfix\b`,
    `\bntfs-3g\b`, `\bntfscp\b`, `\bntfsresize\b`, `\bntfswipe\b`, `\bfallocate\b`, `\bBLKDISCARD\b`,
    `\bBLKSECDISCARD\b`, `\bBLKZEROOUT\b`, `(?i)\b0x0*127[7df]\b`, `--zap`, `--clear\b`, `--mbrtogpt`,
    `--move-second-header`, `--delete-bootorder`, `--remove-dups`, `--create(?!-only)`, `--bootnext\b`, `--inactive\b`,
    `--active\b`, `\bctypes\b`. In addition, in `*.py` files other than `installer/lib/gb/run.py` these must not occur:
    `subprocess`, `os.system`, `os.popen`, `os.exec`, `os.spawn`, `os.posix_spawn`, `os.fork`, `import pty`,
    `from pty`, `efibootmgr`, `sgdisk`, `partx`, `fsck.vfat`; and in every file other than `run.py` and
    `installer/etc/initramfs.list` the words `efibootmgr`, `sgdisk`, `fsck.vfat` must not occur.
12. **efivarfs** is mounted read-only (`ro,nosuid,nodev,noexec`) by /init. Only 4.2.10 and 4.3.5 remount it
    read-write, inside `try`/`finally` that remounts it read-only (a failed read-only remount is logged as an error
    and shown; the flow result is unchanged). EFI variables are read directly from efivarfs files (4 attribute bytes +
    data), never parsed from efibootmgr output.
13. **Read-write mounts** happen in exactly two places (4.2.11 `/run/gb/esp`, 4.4 `/run/gb/savelog`). Before each, the
    node is verified against the plan: `/sys/class/block/<p>/start` and `/size` == the planned first LBA and length
    in sectors, `/sys/class/block/<p>/partition` == the planned partition number (slot + 1 for GPT, MBR index for MBR),
    `/sys/class/block/<p>/dev` == the node's major:minor, its parent disk == the planned disk (kname and diskseq), and
    `blkid -p -o export` `PART_ENTRY_UUID`, `PART_ENTRY_OFFSET`, `PART_ENTRY_SIZE` == the planned GUID (4.2: the
    manifest `esp_guid`; 4.4: the value recorded at listing), first LBA and length. Any difference -> no mount,
    `F_ESP_STATE` (4.2.11) / savelog `FAILED code=F_BACKUP` (4.4).
14. **Journal** `/run/gb/journal.json` (RAM; survives a UI crash and respawn, not a reboot):

    ```json
    {"format": "gbsb3-journal/1", "flow": "wholedisk|dualboot|uninstall", "disk": "<kname>", "diskseq": 12,
     "build_id": "<id>", "started": "<UTC ISO 8601>",
     "phases": [{"phase": "planned", "time": "<UTC ISO 8601>"}],
     "result": null, "acknowledged": false}
    ```

    Written atomically (temp file in /run/gb, fsync, rename) when a destructive flow passes its identity check (phase
    `planned`) and after each phase below. `result` becomes `"OK"`, `"REFUSED <code>"`, `"CANCELLED"` or
    `"FAILED <code>"` when the flow prints its RESULT. A journal is **open** when `result` is null (UI crash) or
    starts with `FAILED`, and `acknowledged` is false. Phases:
    * wholedisk: `planned`, `head_zeroed`, `data_written`, `gpt_written`, `verified`;
    * dualboot: `planned`, `offdisk_backup_written` (only if chosen), `data_written`, `data_verified`,
      `gpt_backup_written`, `gpt_primary_written`, `efi_created`, `efi_ordered`, `state_written`;
    * uninstall: `planned`, `offdisk_backup_written` (only if chosen), `efi_removed`, `gpt_backup_written`,
      `gpt_primary_written`.
    At step (5) of the UI start sequence and before every main menu, an open journal is shown: `@@GB JOURNAL ...`
    (5.2), the recovery text for its last phase (table below), then prompt `journal.ack`: `ACK <kname>` sets
    `acknowledged` true and renames the file to `/run/gb/journal-<UTC yyyymmdd-HHMMSS>.json`; `b` goes to the main
    menu, and every flow on that disk (same kname and diskseq) is refused with `R_INCOMPLETE_RUN` until acknowledged.
    A closed journal (OK/REFUSED/CANCELLED) is renamed the same way without a prompt. Recovery texts:

    | flow / last phase | text (gist; INSTALLER words it) |
    |---|---|
    | any / `planned`, `offdisk_backup_written` | nothing was written to the disk |
    | wholedisk / `head_zeroed`..`gpt_written` | the disk was partly overwritten and has no valid table yet: run option 1 on it again, or discard it |
    | wholedisk / `verified` | the disk is complete; only the final report is missing |
    | dualboot / `data_written`, `data_verified` | only unallocated space was written; the partition table is unchanged; nothing to undo |
    | dualboot / `gpt_backup_written` | the backup GPT copy holds the new table, the primary the old one: restore both from the pre-install copy (`gbdisk restore-gpt --from <dir> --tag pre_install --apply` on Windows, from the off-disk copy or `C:\GB-backup\pre-iso-<date>`; README_DUALBOOT Recovery) |
    | dualboot / `gpt_primary_written` | the new table is complete; the boot entry is missing: boot the installer again and use option 3 to remove it, or import on Windows and use `install_dualboot.ps1 -RepairBootEntry` |
    | dualboot / `efi_created`, `efi_ordered` | partitions and boot entry exist (Windows stays first); `\gbinstall\` state missing: option 3 removes everything |
    | dualboot / `state_written` | the install is complete; only the final report is missing |
    | uninstall / `efi_removed` | the boot entry is gone, the partitions are still there: run option 3 again |
    | uninstall / `gpt_backup_written` | the two GPT copies differ: restore both from the `pre_uninstall` copy (off-disk copy, `gbdisk restore-gpt --tag pre_uninstall`) |
    | uninstall / `gpt_primary_written` | the partitions are removed; only verification/report is missing |

15. **Power state** (`R_ON_BATTERY`; v2.2, 12.E4, `util.power_verdict`): AC := some `/sys/class/power_supply/*` has
    `type` `Mains` or `USB` and `online` `1`, or — when no Mains/USB supply reports `online` 0 and no battery reports
    `Discharging` — at least one `Battery` reports `Charging`, `Full` or `Not charging`. `battery` := a battery
    discharges or a Mains/USB supply reports `online` 0. Otherwise `unknown`. On hardware: `battery` -> refuse
    (`R_ON_BATTERY`, no override); `unknown` -> `CHECK result=warn` and, after all checks passed, prompt `power.ack`
    (exactly `AC-OK`, else `R_ON_BATTERY`). Every supply's type/online/status is logged at each check (and by /init at
    boot). In a VM (`is_vmware`) it never refuses: `@@GB CHECK name=power result=ok` if AC, else `result=warn code=-`
    (ignored, no prompt). Applies to modes 2 and 3, and to mode 1 on a disk that is not USB.
16. **Union partition enumeration** (used for every Windows/BitLocker/NTFS detection, for "pre-existing partitions"
    and for the media scan): the union, deduplicated by (first LBA, sector count), of (a) the entries of the primary GPT
    (header at LBA 1 with signature `EFI PART`, entries from its `entries_lba`), (b) the entries of the backup GPT
    (header at N - 1 with signature `EFI PART`, entries from its `entries_lba`) — both read even when a CRC is wrong,
    (c) the MBR's four entries other than type 0x00 and the protective 0xEE, (d) the logical partitions of the EBR chain
    of every MBR entry of type 0x05, 0x0F or 0x85 (at most 128, every EBR inside the extended partition, each EBR LBA
    visited once), (e) the kernel's partitions (`/sys/block/<d>/<p>/start|size`). Types are taken from wherever an
    entry was found (GPT type GUID, MBR type byte). Sector 0 of each range is read for the OEM ID / BitLocker checks.
17. **Predicates.** `is_vmware` := `/sys/class/dmi/id/sys_vendor` with trailing whitespace removed equals
    `VMware, Inc.`. A disk is **USB** := `/sys/block/<d>/removable` = 1 or its transport is `usb`.

### 4.1 Mode 1: write Googlebook to a whole disk

Steps:

1. List disks (5.2 `@@GB DISK`), the user picks one (`wd.disk`).
2. Checks, all run and printed (`@@GB CHECK name=<name>`), the RESULT code is the first failing one in this order:

   | order | name | code | refuse when |
   |---|---|---|---|
   | 0 | `incomplete_run` | `R_INCOMPLETE_RUN` | an open, unacknowledged journal names this disk (4.0.14) |
   | 1 | `payload_disk` | `R_PAYLOAD_DISK` | the disk is protected (3.7) |
   | 2 | `readonly` | `R_READONLY` | `/sys/block/<d>/ro` = 1 |
   | 3 | `in_use` | `R_IN_USE` | the disk or a partition is mounted (other than installer probe mounts, which are unmounted first) or has holders |
   | 4 | `sector_size` | `R_SECTOR_SIZE` | logical sector size != 512 |
   | 5 | `too_small` | `R_TOO_SMALL` | size < `whole_disk.image_bytes` |
   | 6 | `bitlocker` | `R_BITLOCKER` | sector 0 of the disk or of any partition (4.0.16) has `-FVE-FS-` at offset 3, or `blkid -p` reports `TYPE=BitLocker` for the disk or any partition |
   | 7 | `windows_disk` | `R_WINDOWS_DISK` | any of: a partition (4.0.16, either GPT copy) of type MSR `e3c9e316-0b5c-4db8-817d-f92df00215ae`, WinRE `de94bba4-06d1-4d40-a16a-bfd50179d6ac`, LDM `5808c8aa-7e8f-42e0-85d2-e1e90434cfb3` / `af9b60a0-1431-4f62-bc68-3311714a69ad`, Storage Spaces `e75caf8f-f680-4cee-afa3-b001e56efc2d`; `NTFS    ` at offset 3 of sector 0 of the disk or of any partition; a FAT partition containing `/EFI/Microsoft/Boot/bootmgfw.efi` (media scan, read-only). (MBR type 0x07 alone is NOT a reason: exFAT uses it too.) |
   | 8 | `duplicate_guids` | `R_DUPLICATE_GUIDS` | another, non-target disk carries the image's disk GUID or any of its 18 partition GUIDs (an existing stick made from the same image: by-name/boot_part_uuid would be ambiguous; "unplug it") |
   | 9 | `protected_media` | `R_PROTECTED_MEDIA` | the media scan (3.9) flags any partition of the disk `winmedia`, `gbbackup`, `ventoy` or `unscanned` (message names the flag: Windows/recovery media, GB-backup copy, Ventoy stick, not fully scanned) |
   | 10 | `internal_not_blank` | `R_INTERNAL_NOT_BLANK` | the disk is not USB (4.0.17) and any byte of LBA 0..2047 or of the last 2081 LBAs is non-zero ("wipe it on purpose elsewhere first") |
   | 11 | `power` | `R_ON_BATTERY` | the disk is not USB and the power state (4.0.15) is not AC (never refuses in a VM) |

3. If size >= image_bytes + 1 GiB: ask `wd.grow` (grow userdata to the end of the disk, y/n).
4. Show the plan: model, size in GiB (2 decimals) and bytes, "ALL DATA ON THIS DISK WILL BE ERASED", **every existing
   partition** of the disk (union enumeration: number, start, size, FS type or `-`, label (screen only), used space for
   vfat/exfat or `-`), the 18 new partitions, userdata size; then `wd.confirm`: the user must type exactly
   `ERASE <kname>` (e.g. `ERASE sda`); anything else -> `CANCELLED`.
5. Open and identity (4.0.1), re-read (4.0.3, first write). Journal `planned`.
6. Let N = disk sectors, T = `whole_disk.total_lba`. Write, in order:
   * zero LBA [0, 2048) and LBA [N - 2081, N) (old tables gone first; an interrupted write leaves no valid table);
     journal `head_zeroed`;
   * every partition in slot order at `first_lba * 512`: its object (4.0.4) + zero tail, or its zero ranges (4.0.5);
   * zero the gap after userdata: without grow LBA [image userdata `last_lba` + 1, T - 33) (today [56276992,
     56279040), the image's 1 MiB gap; on a larger disk the space from T - 33 to N - 2081 stays as it was:
     unallocated); with grow LBA [new userdata `last_lba` + 1, N - 33); journal `data_written`;
   * re-read 4.0.3 (head and tail all zero), then the GPT, built from `gpt-primary.bin` / `gpt-backup.bin`
     (sha256-checked against the manifest): entries = primary bytes [1024, 17408); with grow set slot 17 `last_lba`
     (entry offset 17*128+40) to `floor((N - 33 - 2048) / 2048) * 2048 - 1`; MBR = primary sector 0 with the
     partition-0 size field (offset 458) = `min(N - 1, 0xFFFFFFFF)`; primary header = image header with
     `alternate_lba = N - 1`, `last_usable_lba = N - 34`, `entries_crc32 = crc32(entries)`, header CRC recomputed;
     backup header = same with `my_lba = N - 1`, `alternate_lba = 1`, `entries_lba = N - 33`. Disk GUID, first usable
     LBA, all other entries unchanged. If N == T and no grow, the built bytes MUST equal the two files exactly
     (assert). Journal `gpt_written`.
7. Verify (4.0.6) all partitions + GPT, `sgdisk -v <disk>` must report no problems, kernel rescan (4.0.7). Journal
   `verified`.
8. `@@GB RESULT flow=wholedisk status=OK`. Nothing is written to the target's ESP (the image stays exact).
   Effect for a blank target of exactly T sectors: the disk is byte-identical to `googlebook-sb3-usb.img`.

No EFI variables are touched in mode 1 (sticks boot via the removable-media path).

### 4.2 Mode 2: install next to Windows (dual boot)

The user first makes unallocated space in Windows (Disk Management -> D: -> Shrink Volume by >= 22,400 MB; see
README-ISO). Required unallocated span: 13,142 MiB fixed partitions + 8 GiB userdata minimum + 1 GiB reserve =
22,358 MiB (+ <= 1 MiB alignment).

**EFI load options** (shared definitions for 4.2 and 4.3): a `Boot####` variable is read from
`/sys/firmware/efi/efivars/Boot####-8be4df61-93ca-11d2-aa0d-00e098032b8c` (4 attribute bytes, then EFI_LOAD_OPTION:
u32 Attributes, u16 FilePathListLength, NUL-terminated UTF-16LE Description, FilePathList, optional data). Within the
first device-path instance (up to the first End node 0x7F/0xFF): an **HD node** is type 4 subtype 1 (u32 partition
number, u64 start LBA, u64 size in sectors, 16-byte signature, u8 MBR type, u8 signature type); a **FilePath node** is
type 4 subtype 4 (NUL-terminated UTF-16LE); consecutive FilePath nodes concatenate. Nodes before the HD node (a full
device path) are accepted. LOAD_OPTION_ACTIVE = 0x00000001.
* **Windows Boot Manager entry of disk D** := a `Boot####` listed in BootOrder, Attributes & ACTIVE set, whose HD node
  has signature type 2 (GUID) with a GUID equal to the GUID of a type-ESP partition of D's GPT, and whose FilePath
  equals `\EFI\Microsoft\Boot\bootmgfw.efi` case-insensitively (a missing leading `\` is accepted; `/` is not).
* **Our entry** := description exactly `Googlebook OS` and HD-node GUID == `dual_boot.esp_guid`.

Steps:

1. List disks; the user picks one (`db.disk`).
2. Checks in this order (first failure = RESULT code; all are printed):

   | order | name | code | refuse when |
   |---|---|---|---|
   | 0 | `incomplete_run` | `R_INCOMPLETE_RUN` | as 4.1 |
   | 1 | `not_efi` | `R_NOT_EFI` | `/sys/firmware/efi` missing, or efivarfs not mounted/readable at `/sys/firmware/efi/efivars` (no boot entry possible) |
   | 2 | `payload_disk` | `R_PAYLOAD_DISK` | as 4.1 |
   | 3 | `readonly` | `R_READONLY` | as 4.1 |
   | 4 | `in_use` | `R_IN_USE` | as 4.1 |
   | 5 | `sector_size` | `R_SECTOR_SIZE` | as 4.1 |
   | 6 | `no_gpt` | `R_NO_GPT` | no valid protective MBR (entry 0 type 0xEE starting at LBA 1) or no valid primary header |
   | 7 | `gpt_invalid` | `R_GPT_INVALID` | any header or entry-array CRC wrong, primary/backup arrays differ, `alternate_lba` mismatch, backup header not at the last LBA, overlapping/out-of-range entries, duplicate GUIDs, other MBR entries present (hybrid MBR), header size != 92 or non-zero bytes after it |
   | 8 | `gpt_geometry` | `R_GPT_GEOMETRY` | primary entries not at LBA 2, backup entries not at N-33, entry size != 128, or the entry array does not fit in 32 sectors (gbdisk `CheckGptCopies` geometry, needed for 34/33-LBA backups) |
   | 9 | `bitlocker` | `R_BITLOCKER` | as 4.1 (any partition: `-FVE-FS-` at offset 3 or blkid `TYPE=BitLocker`) |
   | 10 | `hibernated` | `R_HIBERNATED` / `R_NTFS_UNCHECKED` | for each NTFS partition (OEM ID `NTFS    `, 4.0.16): `ntfsls -a -s -p / <part>`; non-zero exit or timeout (30 s) -> `R_NTFS_UNCHECKED`. If the listing has an entry equal to `hiberfil.sys` (case-insensitive): `ntfscat <part> /<that name>`, read up to 4096 bytes; on EOF before 4096 bytes wait for the exit status — non-zero -> `R_NTFS_UNCHECKED`; the first 4 bytes are `hibr`, `HIBR`, `rstr` or `RSTR` -> `R_HIBERNATED` (Windows hibernated / Fast Startup shutdown / restore in progress; the message names the signature and the remedy `powercfg /h off`); `wake`/`WAKE` (an image Windows already resumed from, v2.2 12.E5), fewer than 4 bytes (empty file) or anything else -> ok; the signature is logged. A BitLocker partition (`-FVE-FS-`/`TYPE=BitLocker`) cannot be checked: `R_NTFS_UNCHECKED` in mode 2 (R_BITLOCKER refuses first anyway), the `hiber.ack` acknowledgement in mode 3 (12.S2). (No other override; the kernel never mounts these volumes.) |
   | 11 | `already_installed` | `R_ALREADY_INSTALLED` | any disk (protected or not) has a partition with one of the 18 dual-boot GUIDs, or any disk has a partition named like any of the 18 Googlebook names (also a plugged-in Googlebook USB stick: "unplug it") |
   | 12 | `boot_entry_exists` | `R_BOOT_ENTRY_EXISTS` | a `Boot####` with description `Googlebook OS` exists |
   | 13 | `table_full` | `R_TABLE_FULL` | h + 18 > entry_count - 1, where h = the highest used slot (fewer than 18 free slots after it) |
   | 14 | `no_region` | `R_NO_REGION` | no candidate region (below). Message gives the required 22,358 MiB |
   | 15 | `no_windows_entry` | `R_NO_WINDOWS_ENTRY` | `BootOrder` does not exist, or contains no Windows Boot Manager entry of this disk (definition above). Message: "Windows Boot Manager is not in the firmware boot order: in Windows run `bcdboot C:\Windows /f UEFI` (elevated) first". The installer never creates BootOrder |
   | 16 | `windows_dynamic` | `R_WINDOWS_DYNAMIC` | a partition of type LDM metadata `5808c8aa-7e8f-42e0-85d2-e1e90434cfb3`, LDM data `af9b60a0-1431-4f62-bc68-3311714a69ad` or Storage Spaces `e75caf8f-f680-4cee-afa3-b001e56efc2d` (dynamic disk / Storage Spaces: writing their GPT is unsupported) |
   | 17 | `no_windows` | `R_NO_WINDOWS` | the GPT lacks a type-ESP partition, an MSR partition or a Basic Data partition (GPT types only; not a Windows system disk) |
   | 18 | `power` | `R_ON_BATTERY` | power state (4.0.15) not AC (never refuses in a VM) |

3. Candidate regions (port of gbdisk `PlanNew`; all byte values, A = 1 MiB, reserve R = 1 GiB):
   for each partition P of type Basic Data `ebd0a0a2-b9e5-4433-87c0-68b6b72699c7` ("anchor", normally D:):
   `next` = the partition with the smallest offset > P.offset; `region_end` = next.offset, or the usable end
   ((last_usable_lba+1)*512) if none; skip P if any partition overlaps it; `start = alignup(max(P.end, first_usable),
   A)`; `usable_end = aligndown(min(region_end, usable end) - R, A)`; `fixed` = sum of alignup(size, A) of the 17
   non-fill partitions; `fill = aligndown(usable_end - start - fixed, A)`; candidate iff `fill >= 8589934592`.
   One candidate -> used (shown); several -> `db.region` choice.
4. `db.userdata`: Enter = fill (default), or an integer N of GiB with 8 <= N and N GiB <= fill: userdata =
   alignup(N GiB, A) (gbdisk `--userdata` semantics; the rest stays unallocated).
5. Off-disk GPT copy: if at least one partition is eligible (4.4 savelog/gptbackup rules), prompt `gptbackup.part`:
   a listed `n` = save the pre-install GPT there (step 7), `s` = skip. No eligible partition -> no prompt.
6. Plan: partitions in export order from `start`, each `alignup(size, A)` long (userdata as chosen), type/GUID/name/
   attributes from `dual_boot.partitions`. GPT slots: h + 1 .. h + 18 in placement order (h = highest used slot).
   Print the plan and the space left before `next`; then `db.confirm`: type exactly `INSTALL <kname>`.
7. Open and identity (4.0.1), re-read (4.0.3). Journal `planned`. Backups into RAM
   `/run/gb/backup/<UTC yyyymmdd-HHMMSS>/`: `gpt_pre_install_primary.bin` (LBA 0..33), `gpt_pre_install_backup.bin`
   (last 33 LBAs), `layout_pre_install.json`, `efivars_pre.json` (4.5). If chosen in step 5: write the off-disk copy
   (4.4 procedure, files `gpt_pre_install_primary.bin`, `gpt_pre_install_backup.bin`, `layout_pre_install.json`,
   `SHA256SUMS`, directory `/gbsb3-gptbackup/<UTC yyyymmdd-HHMMSS>-<kname>/`), read it back; failure -> `FAILED
   F_BACKUP` ("nothing was written to <kname>"); success -> `@@GB GPTBACKUP ...`, journal `offdisk_backup_written`.
8. Write data: every partition in placement order (objects / zero ranges, 4.0.4-4.0.5), all inside the planned span,
   which is unallocated space (gate, 4.0.2); journal `data_written`; verify (4.0.6); journal `data_verified`. A
   failure here -> RESULT FAILED, and the message says "the partition table was not changed".
9. GPT: re-read (4.0.3, before the GPT write). Copy of the read primary header sector and entry array; put the 18 new
   128-byte entries into slots h+1..h+18 (type, GUID, first/last LBA, attributes, UTF-16LE name zero-padded to 72
   bytes); every other byte of the entry array unchanged; new `entries_crc32`, header CRCs recomputed; nothing else in
   either header changes (assert: all fields except the two CRCs equal); the MBR is not written. Write per 4.0.7
   (journal `gpt_backup_written` after the backup copy's fsync, `gpt_primary_written` after the primary's), re-read and
   verify: the pre-existing entries' 128 bytes identical in the same slots, new entries exactly as planned, both copies
   identical and valid, sector 0 identical (4.0.2). After the rescan assert that the kernel partition starting at the
   ESP's first LBA has `partition` == `efi_partition_number` (= slot + 1 of EFI-SYSTEM), else `F_KERNEL_RESCAN`.
   Save `gpt_post_install_*.bin`, `layout_post_install.json`.
10. EFI boot entry (efivarfs remounted rw for this step only, 4.0.12):
    1. Snapshot every `Boot####`, `BootOrder`, `BootNext` variable (raw bytes, sha256) -> `efivars_pre.json`.
       BootOrder unreadable -> `F_EFI` (nothing changed yet; partitions stay; see recovery text). Re-verify the check-15
       Windows Boot Manager entry W (still present, byte-identical to the check-time read, active, in BootOrder); else
       `F_EFI` (nothing changed).
    2. `efibootmgr --create-only --disk /dev/<disk> --part <efi_partition_number> --label "Googlebook OS" --loader
       \EFI\BOOT\BOOTX64.EFI`; the new number = the one `Boot####` variable that did not exist before. Journal
       `efi_created`.
    3. `efibootmgr --bootorder <old order>,<new>` (4-digit upper-case hex, comma separated; old order unchanged).
       Journal `efi_ordered`.
    4. Verify: BootOrder == old + [new]; the index of W in BootOrder < the index of new; every pre-existing `Boot####`
       byte-identical; `BootNext` unchanged; the new variable is an EFI_LOAD_OPTION with Attributes == ACTIVE,
       description `Googlebook OS`, an HD node (partition number = `efi_partition_number`, start = ESP first LBA,
       size = ESP sectors, GUID = `dual_boot.esp_guid`, MBR type 2, signature type 2) followed by FilePath
       `\EFI\BOOT\BOOTX64.EFI` (case-insensitive) and End (nodes before HD accepted, logged).
    5. Any failure after 2 -> delete the new variable (`efibootmgr --bootnum <new> --delete-bootnum`, after
       re-verifying it is ours), restore BootOrder to the old value (`--bootorder <old>`), verify the restore (all
       variables byte-identical to `efivars_pre.json`), RESULT FAILED `F_EFI` (partitions stay installed: use mode 3 to
       remove them or retry later).
    `@@GB EFI phase=before|after ...` markers carry the orders.
11. State: verify the ESP node (4.0.13: kname of the new ESP partition, start = ESP first LBA, size, partition number,
    `PART_ENTRY_UUID` = `dual_boot.esp_guid`), then `mount -t vfat -o rw,flush <node> /run/gb/esp`; create
    `/gbinstall/` with the files of 4.5; `sync`; unmount; `fsck.vfat -n <node>` must exit 0 (else `F_ESP_STATE`; the
    install itself is complete). The same files stay in `/run/gb/state/`. Journal `state_written`.
12. `@@GB RESULT flow=dualboot status=OK`; the screen shows the ESP GUID (`dual_boot.esp_guid`) and the exact
    import command `Import-GooglebookIsoInstall.ps1 -EspId <that GUID> -Apply`, and explains: Windows stays the default; boot Googlebook once from
    Windows with `Restart-ToGooglebook.ps1` (it finds the entry by its name, no import needed) or via Surface UEFI;
    import the state on Windows (4.6) before using `uninstall_dualboot.ps1` / `install_dualboot.ps1 -UpdateEspOnly`;
    after the first Googlebook boot, check that Windows' partitions are unchanged (tools `c`, 4.4).

### 4.3 Mode 3: uninstall what mode 2 added

1. Scan all non-protected disks for partitions (either GPT copy) whose GUID is one of the 18
   `dual_boot.partitions[].guid`; also read all `Boot####` variables. List disks with matches (`n/18 partitions`)
   and, separately, "boot entry only" when no partition matches but our entry (4.2 definition) exists. Nothing found ->
   `R_NOT_FOUND` (message: installs made by `install_dualboot.ps1` or by another ISO build are removed with
   `uninstall_dualboot.ps1` on Windows).
2. The user picks (`un.disk`). Checks for a disk (all printed, first failure = RESULT code; "boot entry only" skips
   them all):

   | order | name | code | refuse when |
   |---|---|---|---|
   | 0 | `incomplete_run` | `R_INCOMPLETE_RUN` | as 4.1 |
   | 1 | `readonly` | `R_READONLY` | as 4.1 |
   | 2 | `in_use` | `R_IN_USE` | as 4.1 |
   | 3 | `sector_size` | `R_SECTOR_SIZE` | as 4.1 |
   | 4 | `no_gpt` | `R_NO_GPT` | as 4.2 |
   | 5 | `gpt_invalid` | `R_GPT_INVALID` | as 4.2, with ONE tolerance: if each copy is valid on its own (header CRC, its own entries CRC, geometry) and the two entry arrays differ only in attribute bits 48..56 (byte 54 of the entry, and bit 0 of byte 55) of the slots whose GUID is `vbmeta_a`'s or `vbmeta_b`'s dual-boot GUID (an interrupted Android A/B update), the check prints `result=warn code=-`, the flow uses the primary array, and step 6 writes both copies from it |
   | 6 | `gpt_geometry` | `R_GPT_GEOMETRY` | as 4.2 |
   | 7 | `mismatch` | `R_MISMATCH` | a GUID-matched partition whose name or type differs from the manifest entry for that GUID |
   | 8 | `foreign_between` | `R_FOREIGN_BETWEEN` | a partition with a GUID outside the set lies between the lowest matched offset and the highest matched end |
   | 9 | `hibernated` | `R_HIBERNATED` / `R_NTFS_UNCHECKED` | as 4.2 (ntfsls/ntfscat on every NTFS partition of the disk) |
   | 10 | `power` | `R_ON_BATTERY` | as 4.2 |
   | — | `bitlocker` | — | informational: `-FVE-FS-`/`TYPE=BitLocker` on any partition -> `result=warn code=-`, text "BitLocker is on: keep your recovery key at hand; Windows may ask for it at the next boot" |
   | — | `not_efi` | — | informational: no EFI -> `result=warn code=-`, the EFI step is skipped |

   Partitions with Googlebook names but other GUIDs are never candidates (listed as "kept").
3. Off-disk GPT copy: `gptbackup.part` as 4.2.5 (tag `pre_uninstall`). Then `un.confirm`: type exactly
   `REMOVE <kname>` (`REMOVE efi` for "boot entry only").
4. Open and identity (4.0.1; not for "boot entry only"), re-read (4.0.3). Journal `planned`. Backups to RAM
   (`gpt_pre_uninstall_*.bin`, `layout_pre_uninstall.json`, `efivars_pre_uninstall.json`); off-disk copy if chosen
   (journal `offdisk_backup_written`; failure -> `F_BACKUP`, nothing changed); if the matched `EFI-SYSTEM` holds
   `/gbinstall/` (mounted read-only after the read-only guard; unmounted before the O_EXCL open), copy it to
   `/run/gb/state/previous/` (its `gpt_pre_install_*.bin` is the reference for step 7).
5. EFI first (efivarfs rw for this step only, 4.0.12): for every `Boot####` that is our entry: re-verify it (read
   efivarfs again) right before `efibootmgr --bootnum X --delete-bootnum` (which also drops it from BootOrder); if
   `BootNext` equals X, `efibootmgr --delete-bootnext`. Verify: BootOrder == old minus the removed numbers (order
   kept), all other variables byte-identical. Entries named `Googlebook OS` with another GUID are reported and kept. No
   EFI -> warning, skip. Journal `efi_removed`.
6. GPT: re-read (4.0.3). Zero the 128 bytes of each matched slot; recompute `entries_crc32` and header CRCs; nothing
   else changes (with the 5-tolerance both copies are written from the primary array; the vbmeta slots are among the
   zeroed ones, so both arrays end up identical); write per 4.0.7 (journal `gpt_backup_written`,
   `gpt_primary_written`); verify: all other entries byte-identical, matched slots all-zero, sector 0 identical. No
   partition data is written.
7. Compare the resulting LBA 0..33 and last 33 LBAs with `gpt_pre_install_*.bin` from step 4, if available:
   `@@GB UNINSTALL gpt_matches_pre_install=yes|no|unknown` (`unknown` = no pre-install copy found; `no` lists the
   differing header fields/slots as a warning — changes Android made to our vbmeta entries vanish with the zeroed
   slots, so any remaining difference was made by something else, e.g. Windows, after the install).
8. `@@GB RESULT flow=uninstall status=OK`. The screen says: the space is unallocated again; in Windows use Disk
   Management -> D: -> Extend Volume to take it back; a state imported into `C:\GB-backup` is now stale
   (`uninstall_dualboot.ps1` will report "no Googlebook partitions").

### 4.4 Mode 4: tools

`tools` prompt replies:
* `s` shell (`@@GB SHELL`, busybox `sh -i` with `PS1='gbsh# '`, `exit` returns);
* `l` show the installer log (last 200 lines) and any journal files;
* `v` verify the whole payload (every chunk: zst sha, raw sha, object sha; -> `@@GB RESULT flow=verify
  status=OK|FAILED`);
* `c` **compare** (read-only): find disks with dual-boot GUID matches (as 4.3.1); none -> `@@GB RESULT flow=compare
  status=REFUSED code=R_NOT_FOUND`; several -> `cmp.disk`. Mount the matched `EFI-SYSTEM` read-only (after the
  read-only guard), read `/gbinstall/gpt_post_install_primary.bin`, `gpt_post_install_backup.bin`,
  `layout_pre_install.json` and check them against `/gbinstall/SHA256SUMS`; unmount. Compare the disk's current
  LBA 1..33 and last 33 LBAs with the post-install copies (header fields except the CRCs; each 128-byte slot), ignoring
  attribute bits 48..56 of the vbmeta_a/vbmeta_b slots (reported as info), and compare every partition of
  `layout_pre_install.json` with the current entry of the same GUID (offset, length, type, name, attributes). Print
  every other difference. `@@GB COMPARE disk=<k> result=<same|differs|unknown> diffs=<n>` (`unknown`: files missing or
  SHA256SUMS mismatch), then `@@GB RESULT flow=compare status=OK code=-` (a read error: `status=FAILED code=F_GPT`);
* `w` write logs to a stick (savelog, below);
* `r` rescan for the payload; `b` back; `reboot`, `poweroff` (`@@GB REBOOT` / `@@GB POWEROFF`, then 3.4 shutdown).

**Savelog / gptbackup eligibility** (the only places the installer writes to a disk other than the flow target): a
partition with `TYPE` `vfat` or `exfat` (never NTFS) on a disk that is USB (4.0.17), not protected (3.7), not the
current flow's target, without disk flags `windows`, `bitlocker`, `winmedia`, `gbbackup`, `ventoy`, `unscanned`,
`googlebook`, `usbimage`, `isodualboot`; the partition is not type ESP (`c12a7328-...`, MBR 0xEF) and has >= 64 MiB
free (savelog) / >= 1 MiB free (gptbackup). Listing: `savelog.part` / `gptbackup.part` prompt (number, disk, size,
free; label on screen only).
**Procedure**: record the partition's start, size, partition number and `PART_ENTRY_UUID` at listing; clear the
read-only guard for that partition only; verify the node (4.0.13); mount rw at `/run/gb/savelog`; write the directory
(savelog: `/gbsb3-logs/<build_id>-<UTC yyyymmdd-HHMMSS>/` with `log/` = `/run/gb/log`, `backup/` = `/run/gb/backup`,
`state/` = `/run/gb/state`, every `/run/gb/journal*.json`, and `gbinstall/` = a read-only copy of the `/gbinstall/`
folder of the ESP of a found install of this ISO's export, if exactly one is found; gptbackup: 4.2.7/4.3.4), a
`SHA256SUMS` over it, `sync`, read back and compare every file's sha256, unmount, set the read-only guard again.
`@@GB RESULT flow=savelog status=OK|FAILED code=-|F_BACKUP` (savelog) / `@@GB GPTBACKUP ...` (gptbackup).

### 4.5 State written to the new ESP `\gbinstall\` (INSTALLER writes; Windows Import and TEST read)

All JSON as in 1.4 (ints, lower-case GUIDs, `"0x%016x"` attributes). **Partition numbers**: `number` (in plan.json
`anchor`/`base`, the layout JSONs and `install_state.json` `anchor`) is **gbdisk's ordinal** = 1 + the count of used
entries in lower slots (gbdisk.cs `p.Number = ++num` over the entry array in slot order; also Windows' numbering).
The Linux/efibootmgr/HD() partition number slot + 1 is used only for `efibootmgr --part`, the HD node and
`iso.efi_partition_number`. On a table without holes both are equal (T3: anchor 4, ESP 6). Files:

| file | content |
|---|---|
| `install_state.json` | see below |
| `plan.json` | **gb-dualboot-plan/1**, exactly gbdisk's `Plan.ToJson` shape: `{"format":"gb-dualboot-plan/1","disk_id","disk_size","target":"iso:/dev/<kname>","existing":false,"test_truncated":false,"anchor":{"number","id","length"},"region":{"start","end","reserve"},"base":[pre-existing partitions sorted by offset: {"number","name","type","id","offset","length","attributes"}],"partitions":[placement order: {"name","type","id","offset","length","attributes","file","file_size","sha256","blank"}]}` — `file`/`file_size`/`sha256` from the export manifest (blank: `null`, `0`, `null`, `true`); `region.end` = region_end of 4.2.3 |
| `layout_pre_install.json` | gbdisk layout JSON + backup extras: `{"disk_id","disk_size","sector_size":512,"first_usable":first_usable_lba*512,"usable_length":(last_usable_lba-first_usable_lba+1)*512,"max_partition_count":<entry count>,"partitions":[...sorted by offset, each {"number","name","type","id","offset","length","attributes"}...],"target","time","primary_file":"gpt_pre_install_primary.bin","backup_file":"gpt_pre_install_backup.bin","backup_first_lba":N-33,"primary_sha256","backup_sha256"}` |
| `layout_post_install.json` | same for the state after step 9 (`gpt_post_install_*`) |
| `gpt_pre_install_primary.bin`, `gpt_pre_install_backup.bin`, `gpt_post_install_primary.bin`, `gpt_post_install_backup.bin` | raw LBA 0..33 (17408 B) / last 33 LBAs (16896 B) — the names gbdisk `backup --tag pre_install` / `--tag post_install` uses, so `gbdisk restore-gpt --from <dir> --tag pre_install` works |
| `manifest_installed.json` | verbatim copy of `gbsb3/dualboot/manifest.json` (the export manifest; `build_internal.py --reuse-guids` input) |
| `efivars_pre.json`, `efivars_post.json` | `{"BootOrder":"0000,0001","BootNext":null,"BootCurrent":"0002","entries":{"Boot0000":{"label":"...","sha256":"..."},...}}` (`BootNext`/`BootCurrent`: 4-digit hex string or null; `sha256` over the raw efivarfs file incl. its 4 attribute bytes) |
| `install.log` | the installer log of this run (+ `init.log`) |
| `README.txt` | how to import on Windows (4.6) |
| `SHA256SUMS` | sha256sum format over all files above |

`install_state.json` (gb-dualboot-state/1; paths relative to the `gbinstall` folder; Import rewrites them):

```json
{"format": "gb-dualboot-state/1", "mode": "iso", "target": null, "disk_number": null,
 "disk_id": "<disk guid>", "disk_serial": null,
 "run_dir": "gbinstall", "export_dir": null, "manifest": "manifest_installed.json",
 "phase": "complete", "time": "<UTC ISO 8601>",
 "anchor": {"number": <gbdisk ordinal>, "id": "<guid>", "offset": <bytes>, "original_length": <bytes>, "new_length": <bytes>},
 "plan": "plan.json", "pre_shrink_layout": "layout_pre_install.json",
 "esp_id": "<esp guid>", "super_id": "<super guid>", "entry_id": null, "entry_name": "Googlebook OS",
 "iso": {"build_id": "<id>", "payload_manifest_sha256": "<hex>", "efi_boot_number": "0005",
         "efi_partition_number": <slot+1>, "disk_kname": "nvme0n1", "gpt_written": true}}
```

`original_length` == `new_length` == the anchor's length at install time (the user shrank it in Windows). `phase` is
`complete`, or `no_boot_entry` after an `F_EFI` that kept the partitions.

### 4.6 Windows side (INSTALLER owns; T8 runs the tests)

* `Restart-ToGooglebook.ps1`: **no change**. Without a state pointer it finds the firmware entry by its description
  `Googlebook OS` and checks the path `\EFI\BOOT\BOOTX64.EFI` (case-insensitive), which the efibootmgr entry has.
* `install_dualboot.ps1`, `gb_common.ps1`, `gbdisk.cs/.exe`: **no change**. After an import, `-RepairBootEntry` and
  `-UpdateEspOnly` (with `build_internal.py --reuse-guids <imported manifest_installed.json>`) work as for a Windows
  install; note that `-UpdateEspOnly` rewrites the ESP and drops `\gbinstall\` (import first).
* `uninstall_dualboot.ps1`: **two guards**, nothing else changes.
  * Guard A, in step 4 ("Give the space back"), inserted between the `if ($NoExtend -or -not $anchor)` branch and the
    `elseif ($ImageMode)` branch:

```powershell
elseif ($targetLen -and [int64](@((Get-GbLayout $target).partitions | Where-Object { $_.id -eq $anchor.id })[0].length) -ge $targetLen) {
    Write-Info "$($ExtendDriveLetter): (partition #$($anchor.number)) already has its original size $(Format-MiB $targetLen): nothing to extend (an ISO-made install did not shrink it; Disk Management > Extend Volume gives the freed space back)"
}
```

    Behaviour for Windows-made installs is unchanged (their anchor is smaller than `original_length`).
  * Guard B, inserted immediately after the line that computes `$gbLeft`
    (`$gbLeft = @($layout.partitions | Where-Object { $script:GbNames -contains $_.name -and ... })`), i.e. before
    `$stillShrunk` and before the `$script:GbFailures` refusal:

```powershell
if ($state -and $state.plan -and (Test-Path $state.plan) -and $gbLeft.Count) {
    $ids = @((Read-Json $state.plan).partitions | ForEach-Object { [string]$_.id })
    $alien = @($gbLeft | Where-Object { $ids -notcontains [string]$_.id })
    if ($alien.Count) { Write-Bad "$($alien.Count) Googlebook partition(s) after #$($anchor.number) are not from $StateFile (made elsewhere, e.g. the ISO): run Import-GooglebookIsoInstall.ps1 first, or -StateFile/-Force" }
}
```

    With `-Apply` the existing `if ($script:GbFailures.Count) { ... exit 1 }` then exits 1 before step 1 (backups) and
    before the firmware-entry deletion of step 2, so a stale `C:\GB-backup` pointer can no longer half-uninstall an
    ISO-made install. In run_image_tests, run_edge_tests (E3, E8, E9) and test_bcd_parse every Googlebook partition
    after the anchor is in the state's plan (or there is no state / no partition left), so all of them still pass.
* New `C:\GB-build\native\dualboot\Import-GooglebookIsoInstall.ps1` — dry run unless `-Apply`; Windows PowerShell 5.1;
  dot-sources `gb_common.ps1`; every change via `Invoke-Change`:
  * Params: exactly one source: `-EspId <guid>` (real disk; elevated) or `-From <dir>` (a folder with the `gbinstall`
    files: test harness, or the `gbinstall\` copy that tools `w` wrote to a stick); `-DiskNumber 0`,
    `-TargetImage <file>` (only with `-From`; test harness, same conventions as the other scripts: pointer
    `<image>.gbstate.json`, run dir `<image>.gbbackup\<stamp>-iso`), `-BackupRoot C:\GB-backup`, `-Force`, `-Apply`.
  * `-EspId`: requires elevation (else exit 2). `gbdisk layout --disk N --json` (read-only) MUST contain a partition
    with `id` == the GUID, `type` == `c12a7328-f81f-11d2-ba4b-00a0c93ec93b` and `name` == `EFI-SYSTEM`, else exit 2.
    `$p = @(Get-Partition -DiskNumber N | Where-Object { $_.Guid -eq "{<guid>}" })` must have exactly one element;
    its volume path is the `AccessPaths` element matching `\\?\Volume{*}\` (none -> exit 2: "Windows assigned no
    volume path to this partition; boot the installer, use tools w, then -From <stick>\gbsb3-logs\...\gbinstall").
    The script reads every file of `<volume path>gbinstall\` with `[System.IO.Directory]::GetFiles` /
    `[System.IO.File]::ReadAllBytes` into memory (no temp files, no drive letter, never mountvol, never writes to the
    ESP) and continues with those bytes exactly as with `-From`.
  * Checks (any failure -> exit 2, nothing written): `SHA256SUMS` matches; `install_state.json` format
    `gb-dualboot-state/1` and `mode` `iso`; `plan.json` format `gb-dualboot-plan/1`; `gbdisk layout --json` of the
    target (read-only, no admin) has `disk_id` == state `disk_id`; every plan partition present with identical
    offset/length/type/id/name (attributes may differ only in vbmeta bits 48-56); `gbdisk verify --plan <plan>
    --layout-only` exits 0; an existing pointer is refused unless `-Force` (then it is renamed `.bak-<stamp>`).
  * Apply: write all files to `<BackupRoot>\<yyyyMMdd-HHmmss>-iso\`; rewrite `install_state.json` there with absolute
    `run_dir`, `plan`, `pre_shrink_layout`, `manifest`, `target` = `--disk N` (or `--image <file>`), `disk_number`;
    if elevated and not image mode, `entry_id` = `Find-GooglebookEntry 'Googlebook OS'` id (else stays null);
    write the pointer `<BackupRoot>\googlebook_install.json` `{state, updated}` exactly like install_dualboot.ps1.
  * Test: `C:\GB-build\native\dualboot\test\test_import_iso_state.ps1` (image mode only, no admin, `-From` only):
    `gpt_validate.py mkimage` (C 1024 / D 26624 MiB) -> `gbdisk resize-image --number 4 --length 2147483648 --apply`
    -> `gbdisk backup --tag pre_install` into a fixture `gbinstall` dir -> `gbdisk plan --manifest
    D:\gb-test\export_small\manifest.json --anchor 4 --allow-truncated --image IMG --out plan.json` -> `gbdisk create
    --plan plan.json --image IMG --apply` -> write an ISO-style `install_state.json` + `SHA256SUMS` -> dry-run import
    (image unchanged) -> `-Apply` -> `uninstall_dualboot.ps1 -TargetImage IMG -Apply` exit 0 -> `gpt_validate.py
    validate --expect uninstalled` PASS; plus negative cases (wrong disk id, tampered SHA256SUMS, missing partition,
    existing pointer without -Force, `-EspId` together with `-TargetImage` refused) and the **stale-pointer case**:
    on a fresh ISO-style install, point `IMG.gbstate.json` at a state whose `plan.json` lists other partition GUIDs
    (a copy with every `partitions[].id` replaced), run `uninstall_dualboot.ps1 -TargetImage IMG -Apply` -> exit 1,
    output contains `are not from`, image byte-identical (`gpt_validate.py validate --expect clean`), no
    `uninstall-*` run dir created. Prints `SUMMARY import-iso: <n> passed, <m> failed`.
  * README_DUALBOOT.md gets a section "Installed from the ISO" (import with -EspId, the two guards, -Force fallback).

---------------------------------------------------------------------------------------------------------------------

## 5. Logging, serial protocol, prompts

### 5.1 Logs

* `/run/gb/log/init.log` (init), `/run/gb/log/installer.log` (UI + flows; one line per event,
  `YYYY-mm-ddTHH:MM:SSZ <LEVEL> <text>`; every marker line is logged too), `/run/gb/log/dmesg-<step>.txt` snapshots at
  each RESULT other than OK. Commands run (via run_tool) are logged with argv, exit status and text output.
* Dual-boot: the log is copied into `<new ESP>\gbinstall\install.log` (4.2.11), also after a FAILED that happened
  after the GPT write. Whole-disk: never written to the target. Tools `w` copies logs to a stick of the user's choice.
* Never logged/printed: disk serial numbers, WWNs, user names, volume labels (labels appear on the screen only).
  Models and sizes are fine.

### 5.2 Serial UI and markers (INSTALLER emits, TEST consumes)

* With the UI on ttyS0 (`/run/gb/markers` exists) every marker is printed on its own line, at column 0, starting
  with `@@GB `, plain ASCII, no escapes. On tty1 markers are not printed (only logged).
* Before printing each `@@GB PROMPT` line, the UI calls `termios.tcflush(fd, TCIFLUSH)`. The harness MUST send its reply
  only after it has received that whole line, as `<reply>\r` (icrnl maps CR to NL). The tty echoes input.
* Grammar: `@@GB <TAG> key=value ...`; values with spaces in double quotes; no `"` inside values.

| marker | when |
|---|---|
| `@@GB BOOT build=<id> kernel=<krel> discover=<list> ui=<tty> font=<f>` | UI start |
| `@@GB PAYLOAD-SEARCH method=<m> pass=<n>` | discovery progress |
| `@@GB PAYLOAD kind=<iso9660\|marker\|isofile> dev=<dev> disk=<kname> file=<path\|-> build=<id> protected=<k1,k2>` | found (`protected` from 3.7) |
| `@@GB PAYLOAD-NONE` | 30 s without a payload |
| `@@GB JOURNAL flow=<f> disk=<k> phase=<last phase> result=<FAILED:<code>\|none>` | an open journal is shown (4.0.14) |
| `@@GB MENU id=<main\|tools\|nopayload>` | a menu is shown |
| `@@GB PROMPT id=<prompt id>` | input expected (5.3) |
| `@@GB DISK n=<i> kname=<k> size=<bytes> sector=<n> model="<m>" tran=<t> flags=<f1,f2\|->` | each listed disk; flags: `payload` (protected), `windows`, `bitlocker`, `googlebook` (has GB names), `usbimage` (USB image GUIDs), `isodualboot` (dual-boot GUIDs), `readonly`, `small`, `sector4k`, `inuse`, `winmedia`, `gbbackup`, `ventoy`, `unscanned`, `dynamic` (LDM/Storage Spaces types) |
| `@@GB REGION n=<i> anchor=<gbdisk ordinal> start=<bytes> end=<bytes> fill=<bytes>` | dual-boot candidates |
| `@@GB PLAN flow=<f> disk=<k> start=<bytes> userdata=<bytes> grow=<y\|n>` | before the confirmation |
| `@@GB CHECK name=<check> result=<ok\|refuse\|warn> code=<R_...\|->` | every check; `<check>` = the `name` column of 4.1/4.2/4.3 |
| `@@GB GPTBACKUP part=<kname> dir=<path on that partition> result=<ok\|fail>` | off-disk GPT copy (4.2.7/4.3.4) |
| `@@GB STEP flow=<f> step=<zero\|write\|verify\|gpt\|rescan\|efi\|state> part=<name\|->` | progress |
| `@@GB VERIFY part=<name> result=<ok\|fail> sha256=<hex\|zero>` | per partition after read-back (`sha256=zero` for blank) |
| `@@GB EFI phase=<before\|after> bootorder=<hhhh,...> new=<hhhh\|-> removed=<hhhh,...\|->` | mode 2/3 |
| `@@GB UNINSTALL gpt_matches_pre_install=<yes\|no\|unknown>` | mode 3 |
| `@@GB COMPARE disk=<k> result=<same\|differs\|unknown> diffs=<n>` | tools `c` |
| `@@GB RESULT flow=<wholedisk\|dualboot\|uninstall\|verify\|savelog\|compare> status=<OK\|REFUSED\|FAILED\|CANCELLED> code=<code\|->` | end of a flow |
| `@@GB SHELL` / `@@GB REBOOT` / `@@GB POWEROFF` | tools |

### 5.3 Prompt ids and accepted replies

| id | reply |
|---|---|
| `main` | `1` whole disk, `2` dual boot, `3` uninstall, `4` tools |
| `nopayload` | `r` rescan, `s` shell, `reboot`, `poweroff` |
| `journal.ack` | exactly `ACK <kname>`, or `b` |
| `wd.disk`, `db.disk`, `un.disk`, `cmp.disk` | a listed `n`, or `b` back |
| `wd.grow` | `y` or `n` |
| `wd.confirm` | exactly `ERASE <kname>` |
| `db.region` | a listed `n`, or `b` |
| `db.userdata` | empty (fill) or an integer GiB |
| `gptbackup.part` | a listed `n`, or `s` skip (shown only when an eligible partition exists, 4.4) |
| `power.ack` | exactly `AC-OK` (12.E4: asked after all checks passed, only when the power state is `unknown` on hardware); anything else -> `R_ON_BATTERY` |
| `hiber.ack` | exactly `FASTSTARTUP-OFF` (12.S2: mode 3 only, asked after all checks passed, when a BitLocker partition makes the hibernation check impossible); anything else -> `R_NTFS_UNCHECKED` |
| `db.confirm` | exactly `INSTALL <kname>` |
| `un.confirm` | exactly `REMOVE <kname>` (or `REMOVE efi` for "boot entry only") |
| `tools` | `s`, `l`, `v`, `c`, `w`, `r`, `b`, `reboot`, `poweroff` |
| `savelog.part` | a listed `n`, or `b` |
| `continue` | empty (after every RESULT, back to `main`) |

Invalid replies re-print the prompt (new `@@GB PROMPT` line). A wrong confirmation -> `status=CANCELLED code=-`.

### 5.4 Result codes

Refusals (`status=REFUSED`): `R_INCOMPLETE_RUN`, `R_PAYLOAD_DISK`, `R_READONLY`, `R_IN_USE`, `R_SECTOR_SIZE`,
`R_TOO_SMALL`, `R_BITLOCKER`, `R_WINDOWS_DISK`, `R_DUPLICATE_GUIDS`, `R_PROTECTED_MEDIA`, `R_INTERNAL_NOT_BLANK`,
`R_ON_BATTERY`, `R_NOT_EFI`, `R_NO_GPT`, `R_GPT_INVALID`, `R_GPT_GEOMETRY`, `R_HIBERNATED`, `R_NTFS_UNCHECKED`,
`R_ALREADY_INSTALLED`, `R_BOOT_ENTRY_EXISTS`, `R_TABLE_FULL`, `R_NO_REGION`, `R_NO_WINDOWS_ENTRY`,
`R_WINDOWS_DYNAMIC`, `R_NO_WINDOWS`, `R_NOT_FOUND`, `R_MISMATCH`, `R_FOREIGN_BETWEEN`, `R_DISK_CHANGED` (identity or
GPT bytes changed between listing and the first write: nothing written), `R_NO_PAYLOAD` (reserved: without a valid
payload the `nopayload` menu does not offer modes 1-3), `R_DISK_UNREADABLE` (12.S4: a listed disk could not be read,
so a cross-disk check is incomplete: reported by `duplicate_guids` (4.1), `already_installed` (4.2) and mode 3's
search).
Failures (`status=FAILED`): `F_PAYLOAD` (chunk/object hash mismatch, decompress or read error on the medium),
`F_WRITE` (I/O error writing the target), `F_VERIFY` (read-back differs), `F_GPT` (GPT changed before the GPT write —
nothing written —, GPT write or its re-read verification failed, sector 0 changed, or a GPT read error in tools `c`),
`F_KERNEL_RESCAN` (table on disk correct, kernel view not updated), `F_EFI` (boot entry step failed, rolled back),
`F_ESP_STATE` (`\gbinstall\` could not be written, node verification or FAT check failed; install itself complete),
`F_BACKUP` (off-disk GPT copy or savelog could not be written/verified; for gptbackup nothing was written to the
target), `F_INTERNAL` (assertion, e.g. a write outside the plan or a command outside the allow-list — nothing written
or started by that call), `F_INTERRUPTED` (12.S3: a KeyboardInterrupt reached the flow outside the destructive window;
the journal records the phase reached).
Process exit codes of `gbinstall.py` are not an interface; only markers are.

### 5.5 No auto-install mode

v1 has none: every destructive flow needs the typed confirmation, in tests too. If one is ever added, it MUST require
both the kernel parameter `gb.autotest=1` AND a GPT partition named `GBTEST-CONFIG` on a non-target, non-protected disk
whose first 4 KiB start with `GBTEST-CONFIG 1\n` followed by `key=value` lines (`action`, `target_size`, `grow`,
`userdata_gib`), and it MUST still print every marker; `gb.autotest` is ignored with a warning in v1.

---------------------------------------------------------------------------------------------------------------------

## 6. Build pipeline (BUILD)

### 6.1 Files

```
C:\GB-x86\iso\build\build_iso.py          Windows driver (python -I)
C:\GB-x86\iso\build\forbidden_scan.py     the 4.0.11 scan (python -I, read-only; also used by TEST in T0)
C:\GB-x86\iso\build\builder\setup.sh      apt-get install -y xorriso mtools exfatprogs (idempotent; Ubuntu archive only;
                                          already installed on the builder by the boot review)
C:\GB-x86\iso\build\builder\mk_initramfs.sh   staging + cpio (3.1-3.3), smoke test
C:\GB-x86\iso\build\builder\mk_grub.sh    BOOTX64.EFI + efiboot.img (2.1, 2.4)
C:\GB-x86\iso\build\builder\mk_iso.sh     ISO tree assembly + xorriso + reports
C:\GB-x86\iso\build\builder\psf_double.py 16x32 -> 32x64 PSF2 font
C:\GB-x86\iso\build\templates\early.cfg.in, grub.cfg.in, README.txt.in
C:\GB-x86\iso\README-ISO.md               user guide (BUILD)
C:\GB-x86\iso\work\                       BUILD scratch on Windows: export\, <build_id>\ (logs, staging copies)
```

`forbidden_scan.py <dir> [--layout installer|initrd]` (default `installer`): applies 4.0.11; prints
`FORBIDDEN <relpath>:<line>: <pattern>` per hit and `FORBIDDEN-SCAN OK` (exit 0) or exits 1. With `--layout initrd`
`<dir>` is an unpacked initramfs root, only `init`, `opt/gb/**` and `etc/gb/**` are scanned, and the exempt paths are
`opt/gb/lib/gb/run.py` (for `installer/lib/gb/run.py`) and `etc/gb/initramfs.list` (for
`installer/etc/initramfs.list`).

### 6.2 `build_iso.py` CLI and steps

`python -I C:\GB-x86\iso\build\build_iso.py [--out D:\GB-iso\googlebook-sb3-installer.iso]
[--usb-image C:\GB-x86\out\googlebook-sb3-usb.img] [--new-export] [--no-payload-cache]
[--keep-nx-compat] [--keep-work] [--dry-run]`

1. Preflight: builder reachable (`ssh -i C:/GB-build/builder_key -o StrictHostKeyChecking=no
   builder@192.168.115.132`); builder `/` free >= 25 GiB; D: free >= 7.5 GiB + 6 GiB + 2 GiB margin (+ the old ISO
   is replaced only after success); C: free >= 15 GiB if a fresh export is needed. Read-only: sha256 of the USB image ==
   the hash in `googlebook-sb3-usb.img.sha256` (cad193d6…0d45); GPT parse (18 entries, names in slot order, CRCs,
   backup at the end). The USB image and lapis.bin are opened read-only, nothing is written under `C:\GB-x86\out`.
   `forbidden_scan.py C:\GB-x86\iso\installer` must print `FORBIDDEN-SCAN OK`.
2. Export (1.5) and cross-checks.
3. Builder setup (`setup.sh`), copy `C:\GB-x86\iso\installer\` and `C:\GB-x86\iso\build\builder\` to
   `/var/tmp/gbiso/<build_id>/{installer,builder}` (scp -r), CR check.
4. Payload: cache dir `/var/tmp/gbiso/cache/<usb_sha256[:16]>-<esp_internal_sha256[:16]>/parts/` with `cache.json`
   (object -> chunks with all hashes). If valid (every file present, sizes and `zst_sha256` match), reuse; else for
   each object and chunk: Windows reads the raw chunk (USB image at partition offset + raw_offset, or the export ESP
   file), computes `raw_sha256`, and streams it into `ssh builder "zstd -q -9 -T3 --long=27 -c > <tmp> && mv <tmp>
   <final>"`; then the builder reports `stat -c %s`, `sha256sum` and `zstd -dcq --long=27 <final> | sha256sum`; the
   last MUST equal the Windows-side raw sha256 (end-to-end check). Compressed size MUST be < 1 GiB.
5. Small payload files on Windows: `gpt-primary.bin`, `gpt-backup.bin` (from the USB image), `dualboot/manifest.json`
   (export manifest, verbatim), `manifest.json` (1.4), the marker, `README.txt`; scp to the ISO staging tree
   `/var/tmp/gbiso/<build_id>/iso/`. Chunk files are hard-linked from the cache into `iso/gbsb3/parts/`.
6. Builder: System.map initcall check (2.5), `mk_initramfs.sh` (generated `/etc/gb/build.env` etc., module denylist,
   smoke test), `mk_grub.sh` (incl. the 2.1 checks), kernel copy + NX clear, `SHA256SUMS`, `mk_iso.sh` ->
   `/var/tmp/gbiso/<build_id>/out/googlebook-sb3-installer.iso`; reports (2.4); size <= 8.0 GiB.
7. Transfer: `scp` to `D:\GB-iso\googlebook-sb3-installer.iso.part`; Windows sha256 == builder sha256; clear the
   read-only attribute of an old ISO, `os.replace` the `.part` over it; set the read-only attribute on the new ISO;
   write `D:\GB-iso\googlebook-sb3-installer.iso.sha256` (`<hex>  googlebook-sb3-installer.iso`),
   `D:\GB-iso\googlebook-sb3-installer.manifest.json` (copy of 1.4), `D:\GB-iso\googlebook-sb3-installer.build.json`
   (6.3), `D:\GB-iso\build-<build_id>.log`.
8. Builder cleanup: keep the cache and `/var/tmp/gbiso/last/googlebook-sb3-installer.iso` (hard link to the new ISO,
   replacing the previous `last`; TEST builds T5/T6 media from it); delete `/var/tmp/gbiso/<build_id>/` unless
   `--keep-work`.

### 6.3 Build record `D:\GB-iso\googlebook-sb3-installer.build.json`

```json
{"format": "gbsb3-iso-build/1", "build_id": "<id>", "created_utc": "<...>",
 "iso": "D:\\GB-iso\\googlebook-sb3-installer.iso", "iso_size": 0, "iso_sha256": "<hex>",
 "payload_manifest": "D:\\GB-iso\\googlebook-sb3-installer.manifest.json",
 "usb_image": {"path": "C:\\GB-x86\\out\\googlebook-sb3-usb.img", "size": 28814885376, "sha256": "<hex>"},
 "export_dir": "C:\\GB-x86\\iso\\work\\export", "export_manifest_sha256": "<hex>",
 "builder_iso": "/var/tmp/gbiso/last/googlebook-sb3-installer.iso",
 "kernel_release": "6.8.0-147-generic", "kernel_dllcharacteristics": {"before": "0x0100", "after": "0x0000"},
 "grub_efi_sha256": "<hex>", "grub_efi_dllcharacteristics": "0x0000", "efiboot_img_bytes": 8388608,
 "initrd_sha256": "<hex>", "initrd_size": 0,
 "tools": {"xorriso": "<ver>", "grub": "2.12-1ubuntu7.3", "zstd": "1.5.5", "busybox": "1.36.1", "python": "3.12.3"}}
```

### 6.4 README-ISO.md (BUILD) must cover

* What the ISO is (personal use only, never share/upload).
* **Ventoy**: copy the .iso onto the Ventoy partition, which must be exFAT (Ventoy's default) or NTFS — a FAT32 Ventoy
  partition cannot hold it — with free space >= the ISO size (about 8.1 GB; "8 GB free" is not enough); ext4/XFS/UDF
  Ventoy partitions are not searched by the installer. Boot the stick; choose the ISO; if Ventoy's normal mode fails,
  try its GRUB2 mode; the installer finds the .iso file itself.
* **Rufus**: ISO mode recommended, DD mode alternative; >= 16 GB stick; GPT + UEFI target; Secure Boot must stay off.
  After writing a Rufus-DD or dd stick, Windows sees partition 1 as RAW and offers to format it: click **Cancel**.
* VMware (attach as CD).
* **Surface Book 3 boot**: Secure Boot off, "Enable boot from USB devices", Volume Down + Power; plug the stick (and a
  USB keyboard, optional) into the **keyboard base**, keep the clipboard **docked**, stay **on AC power**, and **never
  press the detach key** while the installer runs (all USB ports are on the base; detaching drops the stick and the
  keyboard mid-write). The installer refuses modes 2 and 3 (and mode 1 on the internal disk) without AC.
* The four menu options with their confirmations and refusals; mode 1 never erases a Windows disk, Windows install or
  recovery media, a GB-backup stick, a Ventoy stick, a BitLocker volume, or a non-blank internal disk.
* **Preparing Windows for dual boot**: BitLocker/Device Encryption off, Fast Startup off or use Restart, Windows Boot
  Manager present in the firmware boot order (otherwise `bcdboot C:\Windows /f UEFI`, elevated), shrink D: by
  >= 22,400 MB — 40,000+ recommended; make the Surface USB recovery drive (README_DUALBOOT step 2) and keep it
  unplugged during the install; take an off-PC GPT backup first (elevated, read-only on the disk):
  `gbdisk backup --disk 0 --tag pre_iso --out C:\GB-backup\pre-iso-<yyyyMMdd>`, then copy that folder to a USB stick;
  the installer can additionally save the pre-install GPT to a FAT/exFAT stick (prompt "gptbackup").
* **After the install**: Restart-ToGooglebook.ps1, firmware menu; import (elevated) with
  `Import-GooglebookIsoInstall.ps1 -EspId <ESP GUID shown by the installer> -Apply` (no drive letter, no mountvol);
  fallback: boot the installer, tools `w` (copies the `gbinstall` folder to a stick), then
  `Import-GooglebookIsoInstall.ps1 -From <stick>:\gbsb3-logs\<...>\gbinstall -Apply`. After the first Googlebook
  boot, confirm that Windows' partitions are unchanged: installer tools `c`, or on Windows `gbdisk layout --disk 0`
  plus `gbdisk verify --plan <imported plan.json> --layout-only`. If BitLocker is enabled later: keep the recovery key.
* Uninstall (option 3, or Windows after import); recovery (points to README_DUALBOOT Recovery and the journal texts);
  where the logs are.

---------------------------------------------------------------------------------------------------------------------

## 7. Test plan (TEST)

### 7.1 Harness conventions

* Files: `C:\GB-x86\iso\test\` — `run_all.py` (orchestrator; `--only T1a,T3,...`), `vmtest.py` (VM lifecycle +
  serial driver + pre-start guard), `mkdisks.py` (sparse raw disks + flat vmdk descriptors + pre-start guard),
  `builder_media.sh` (T2/T3n/T4n/T5/T6 media on the builder, loop devices over image files only),
  `validate_wholedisk.py`, `validate_dualboot.py` (imports `C:\GB-build\native\dualboot\tools\gpt_validate.py`
  functions by path), `check_esp_tree.sh` (builder, mtools), `logs\<build_id>\` (serial transcripts, per-test results,
  `summary.txt`).
* VM: `D:\GB-iso\test\vm\gbiso-test.vmx` (single VM, disks swapped per test):
  `firmware = "efi"`, `uefi.secureBoot.enabled = "FALSE"`, `memSize = "2048"`, `numvcpus = "2"`,
  `guestOS = "other6xlinux-64"`, `nvme0:0` = target disk (independent-persistent), `sata0:0` = CD
  (`deviceType = "cdrom-image"`, the ISO via a **hard link** `D:\GB-iso\test\iso\googlebook-sb3-installer.iso`,
  recreated per run, deleted afterwards), `sata0:1`, `sata0:2` = payload/extra disks (**independent-nonpersistent**),
  `serial0` = pipe `\\.\pipe\gbiso_con` (`pipe.endPoint = "server"`, `tryNoRxLoss = "TRUE"`,
  `yieldOnMsrRead = "TRUE"`, `startConnected = "TRUE"`), **no USB controller**: `usb.present = "FALSE"`,
  `usb_xhci.present = "FALSE"`, `ehci.present = "FALSE"`, `usb.generic.autoConnect = "FALSE"`, no
  `usb.autoConnect.device*` key; `ethernet0.present = "FALSE"`, `floppy0.present = "FALSE"`, `sound.present = "FALSE"`.
  vmrun: `"C:\Program Files\VMware\VMware Workstation\vmrun.exe" -T ws start|stop <vmx> nogui|hard`; every vmrun call
  MUST assert the vmx path starts with `D:\GB-iso\test\`; never pass any other vmx; `vmrun list` is read-only use only.
* **Pre-start guard** (MUST pass before every `vmrun start`; implemented in `vmtest.py`, also run by `mkdisks.py` after
  writing a descriptor): parse the .vmx and every .vmdk it references; abort (no start, test FAIL "guard") unless
  every `*.fileName` value and every extent path (relative paths resolved against the descriptor's directory) resolves
  with `os.path.realpath` to an existing **regular file** below `D:\GB-iso\test\` (`os.lstat` S_ISREG, no symlink,
  no reparse point/junction; hard links are regular files); no value anywhere contains `\\.\`, `\\?\`,
  `PhysicalDrive`, `GLOBALROOT`, `fullDevice`, `partitionedDevice`, `rawDevice` — except `serial0.fileName` which
  MUST equal `\\.\pipe\gbiso_con`; every vmdk has `createType="monolithicFlat"` and only extents of the form
  `RW|RDONLY <n> FLAT "<path>" 0`; `*.deviceType` values are only `cdrom-image` or `disk` (never `cdrom-raw`,
  `rawDisk`, `atapi-cdrom`); `serial0.fileType = "pipe"`; the USB/network keys above have exactly those values.
* **Runtime kill-switch**: any `@@GB DISK` line with `tran=usb`, or whose `size=` is not the size of one of the disks
  this test attached -> `vmrun stop hard` immediately, test FAIL "unexpected disk".
* **NVRAM**: `.nvram` is deleted at the start of each session group (7.2 names the groups); within a group it is kept.
  "Before"/"after" EFI comparisons always use snapshots taken in the same VM session (VMware's firmware may rewrite its
  own entries at boot).
* **BootNext-to-CD rule**: before powering off a session after which the target disk may hold a bootable ESP and the
  same NVRAM boots again, the harness runs in tools `s`: `mount -o remount,rw /sys/firmware/efi/efivars;
  efibootmgr --bootnext <n>; mount -o remount,ro,nosuid,nodev,noexec /sys/firmware/efi/efivars`, where `<n>` is the
  `Boot####` whose description contains `SATA CDROM` (taken after all EFI checks of that session). Otherwise VMware's
  disk entry could start the Googlebook ESP's fallback loader instead of the CD. (Throwaway VM only.)
* Serial: open the pipe after `start`, poll with `PeekNamedPipe` (technique of gbsh.py, own code), log every byte.
  GRUB: wait for `Googlebook SB3 installer` (timeout 60 s), then send the hotkey character (`s`, `v` or `t`, no CR).
  Then wait for `@@GB MENU id=main` (180 s). Replies only after the complete `@@GB PROMPT` line (5.2). An unexpected
  `@@GB PROMPT id=journal.ack` is answered `b` and fails the test. `gptbackup.part` never appears in the defined tests
  (no eligible USB stick); if it does, reply `s` and fail the test.
  Timeouts: whole-disk/dual-boot flows 45 min, uninstall 5 min, verify 30 min. End every VM session with tools
  `poweroff`; after 60 s without power-off: `vmrun stop hard`.
* Disks: sparse raw files (set `FSCTL_SET_SPARSE` before writing; extend by writing the last sector, never by CRT
  truncate) with a `monolithicFlat` descriptor (`RW <sectors> FLAT "<file>" 0`, `ddb.adapterType = "lsilogic"`), under
  `D:\GB-iso\test\disks\`. Before creating a disk: D: free - expected physical size >= 8 GiB, else skip with
  `SKIPPED (space)`. Delete every disk after its test (the T3 disk lives through groups G1-G4).
  "Byte-identical" in a pass criterion = sha256 of the whole raw file before == after (sparse-aware: unallocated
  ranges hashed as zeros without reading), plus, for GPT disks, `gpt_validate.py validate --expect clean` PASS.
* Builder media: built in `/var/tmp/gbiso/test/` from `/var/tmp/gbiso/last/googlebook-sb3-installer.iso` (its sha256
  must equal the build record's), transferred to Windows by streaming `cat` over ssh and writing sparse (all-zero
  MiB blocks skipped), then deleted on the builder.
* Control: before T1, run `python -I C:\GB-x86\verify_image.py` (default image) once; record its result. If it is not
  OK (lapis.bin changed), T1's verify_image step is reported INCONCLUSIVE and the manifest-hash checks decide.
* `@@GB CHECK name=power result=warn` (VM without an AC supply) is expected and never a failure.

### 7.2 Tests and exact pass criteria

**T0 static ISO checks** (builder, read-only): the 2.4 properties incl. El Torito `Ldsiz` == efiboot.img bytes / 512
and efiboot.img < 32 MiB; `gbsb3/SHA256SUMS` verifies; every manifest chunk: `zst_sha256`, decompressed `raw_sha256`,
object sha256; manifest `whole_disk.partitions` equal the USB image GPT (names, types, GUIDs, attrs, LBAs);
`gpt-primary.bin`/`gpt-backup.bin` equal the image's LBA 0..33 / last 33 LBAs; `dual_boot.partitions` equal the export
manifest; `/boot/vmlinuz` DllCharacteristics has 0x0100 clear; **BOOTX64.EFI DllCharacteristics has 0x0100 clear**;
BOOTX64.EFI contains the string `/gbsb3/GBSB3-<build_id>.marker` and none of `tpm.mod`, `shim_lock.mod`,
`gfxterm.mod`; grub.cfg has the 8 entries with the ids and hotkeys of 2.2, `gb_args` contains
`initcall_blacklist=init_tis,crb_acpi_driver_init` and `iommu.passthrough=1` and no `fbcon=`, and the TEST entries end
their console list with `console=tty0`; the initrd (unpacked in `/var/tmp/gbiso/test/`): `/bin` and `/sbin` are real
directories, `/usr/bin/ntfsls`, `/usr/bin/ntfscat`, the three fonts and `/lib/modules/6.8.0-147-generic/modules.builtin`
exist, no `.ko` whose name matches the 3.3 denylist, and `forbidden_scan.py` over the unpacked `/opt/gb` and `/init`
prints `FORBIDDEN-SCAN OK` (`--layout initrd`).

**T1a whole disk, exact size.** Target: blank sparse raw of exactly 28,814,885,376 bytes on nvme0:0; ISO as CD; entry
`s`; `1`; pick the disk with `size=28814885376`; no `wd.grow` prompt appears; `ERASE nvme0n1`. Pass: `@@GB RESULT
flow=wholedisk status=OK`, 18 `@@GB VERIFY ... result=ok`; `@@GB CHECK name=internal_not_blank result=ok`; after
power-off on the host: sha256 of the raw file == `cad193d629c6d05340841718bc569a51802878b96ed64fa58e7135cf49460d45`;
`python -I C:\GB-x86\verify_image.py <raw>` prints `RESULT: OK` (exit 0) [INCONCLUSIVE rule of 7.1].

**T1b whole disk, larger disk, grow.** Target: blank sparse raw of 34,359,738,368 bytes (N = 67,108,864); `wd.grow` =
`y`. Pass: RESULT OK; `verify_image.py <raw>` exit 0; GPT valid (gpt_validate `read_gpt` no problems); backup header at
N-1, entries at N-33, `last_usable_lba` = N-34; userdata `last_lba` = 67,104,767, name/GUID/type/attrs as the image;
the other 17 entries' 128 bytes identical to the image's; disk GUID identical; each sourced partition's sha256 ==
manifest object sha256; blank partitions zero per 4.0.5; LBA 34..2047 zero.

**T1c (optional) larger disk, no grow**: entries identical to the image, `last_usable_lba` = N-34, backup at the end.

**T2 whole-disk refusals** (each: byte-identical afterwards per 7.1, and the exact RESULT; all on nvme0:0 unless
stated): T2a host-shaped disk `gpt_validate.py mkimage t2a.img --c-mib 1024 --d-mib 28672` (about 30 GiB logical, so
the earlier `R_TOO_SMALL` check passes; sparse, ~0.25 GiB physical) -> `REFUSED code=R_WINDOWS_DISK`, listed with flag
`windows`; T2b 32 GiB disk with one Basic Data partition formatted by `mkntfs -Q` -> `R_WINDOWS_DISK`; T2c 32 GiB disk
whose partition starts with an NTFS-like boot sector carrying `-FVE-FS-` at offset 3 -> `R_BITLOCKER`; T2d blank 8 GiB
disk -> `R_TOO_SMALL`; T2e (inside T7) the dd-mode payload disk is listed with flag `payload`, choosing it ->
`R_PAYLOAD_DISK`; T2f wrong confirmation (`ERASE sdz`) on a blank 32 GiB disk -> `status=CANCELLED`, disk still all
zero. New (builder media, each a 32 GiB sparse disk so that `R_TOO_SMALL` passes, no NTFS, no `.iso` file, no `gbsb3`
directory): T2g GPT, one 1 GiB Basic Data FAT32 partition (`mkfs.vfat -F 32`) holding `/sources/boot.wim` (1 KiB
stub) and `/EFI/Microsoft/Boot/BCD` (stub; no bootmgfw.efi) -> `R_PROTECTED_MEDIA`, flag `winmedia`; T2h GPT, one
1 GiB exFAT partition holding `/GB-backup/x/gpt_pre_shrink_primary.bin` (17408 zero bytes) -> `R_PROTECTED_MEDIA`,
flag `gbbackup`; T2i Ventoy layout, MBR: partition 1 exFAT labelled `Ventoy` (1 GiB, from 1 MiB), partition 2 FAT16
labelled `VTOYEFI` (32 MiB, at the end of the disk) -> `R_PROTECTED_MEDIA`, flag `ventoy`. T2g-i also print
`@@GB CHECK name=windows_disk result=ok` (they reach check 9).

**Dual-boot session groups** (one T3 disk `t3.img` through G1-G4; built as follows): `gpt_validate.py mkimage t3.img
--c-mib 1024 --d-mib 26624`, then `gbdisk.exe resize-image --image t3.img --number 4 --length 2147483648 --apply`
(stands in for "Shrink D:"; leaves 24 GiB + 1 MiB unallocated before Recovery), then `gpt_validate.py snapshot t3.img
pre.json` and the whole-file sha256, then the expected plan with the Windows tool: `gbdisk.exe plan --manifest
C:\GB-x86\iso\work\export\manifest.json --anchor 4 --reserve 1073741824 --image t3.img --out expected_plan.json`
(read-only). **NVRAM preparation** (throwaway VM only, in tools `s`): `mount -o remount,rw /sys/firmware/efi/efivars`;
`efibootmgr --create-only --disk /dev/nvme0n1 --part 1 --label "Windows Boot Manager" --loader
'\EFI\Microsoft\Boot\bootmgfw.efi'`; `efibootmgr --bootorder <that number>,<previous order>`;
`mount -o remount,ro,nosuid,nodev,noexec /sys/firmware/efi/efivars`.

* **G1** (fresh `.nvram`): boot, `s`. **T3n(f)**: shell snapshot (`cat /sys/firmware/efi/efivars/BootOrder-*` and
  `sha256sum /sys/firmware/efi/efivars/Boot*`), then `2`, the disk -> `REFUSED code=R_NO_WINDOWS_ENTRY`, all earlier
  checks `ok`; shell snapshot again: identical. Then NVRAM preparation. **T3n(e)**: `2`, the disk (single region:
  no `db.region` prompt), `db.userdata` empty, a wrong `db.confirm` (`INSTALL sdz`) -> `CANCELLED`. Power off. Host: t3.img byte-identical to
  the state after the build steps (covers T3n(f) and T3n(e)).
* **G2** (same `.nvram`): boot, `s`; shell: `efibootmgr -v` and `sha256sum` of the `Boot*` variables (before). **T3**
  flow: `2`, the disk, the single region (no `db.region` prompt), `db.userdata` empty, `INSTALL nvme0n1`. Shell after:
  `efibootmgr -v`, `sha256sum` of the variables, base64-pull `/run/gb/state/*`. **T3n(d)**: a second `2` on the same
  disk -> `R_ALREADY_INSTALLED`. BootNext-to-CD rule. Power off. Host: T3 pass criteria.
* **G3** (same `.nvram`): **T4**.
* **G4** (fresh `.nvram`): **T4b**.

**T3 dual boot.** Pass, all of:
1. `@@GB RESULT flow=dualboot status=OK`; 17 `@@GB VERIFY result=ok` (ESP verified before state was added); the
   CHECK lines `no_windows_entry`, `windows_dynamic`, `no_windows`, `table_full` all `result=ok`.
2. GPT structurally valid; MBR, header disk GUID, first/last usable LBA, entry count/size, entries LBA unchanged.
3. The 5 pre-existing entries byte-identical in slots 0..4; the 18 new entries in slots 5..22 with names, types,
   GUIDs, attributes of the export manifest; offsets/lengths == `expected_plan.json` partitions exactly
   (start = D: end = 3,343,908,864 = 3189 MiB; userdata 10,411 MiB; exactly 1 GiB left unallocated before Recovery,
   which includes mkimage's stale-data 1 MiB gap — untouched).
4. plan.json from the ESP == expected_plan.json in `partitions` (name, type, id, offset, length, attributes),
   `region` and `anchor.number` (4); `gbdisk.exe verify --image t3.img --plan plan.json --layout-only` exit 0.
5. Contents: the 11 sourced non-ESP partitions' sha256 == manifest objects; blank partitions zero per 4.0.5.
6. ESP (`check_esp_tree.sh`, ESP region streamed to the builder): every file of the export `EFI-SYSTEM.img` present
   and identical; additions only under `/gbinstall/` with exactly the files of 4.5 and a valid `SHA256SUMS`;
   `fsck.vfat -n` exit 0; raw ESP bytes contain `androidboot.boot_part_uuid=<dual_boot.super_guid>` and
   `gb.boot=internal`.
7. Every MiB outside the 18 new partitions byte-identical to `pre.json` (gpt_validate digests).
8. EFI (G2 before/after): BootOrder after == before + [new]; first entry still the fake "Windows Boot Manager"; new
   entry `HD(6,GPT,<esp guid>,...)/File(\EFI\BOOT\BOOTX64.EFI)` labelled `Googlebook OS`, active; all other `Boot####`
   sha256 unchanged; `@@GB EFI` markers agree.
9. `install_state.json` matches 4.5 (mode iso, anchor number 4, anchor id = D:'s GUID, original_length = new_length =
   2,147,483,648, `iso.efi_partition_number` 6).

**T3n dual-boot refusals** (byte-identical disk and unchanged EFI variables afterwards, exact codes): (a) mkimage disk
without free space -> `R_NO_REGION`; (b) host-shaped disk built on the builder whose C: partition carries `-FVE-FS-`
-> `R_BITLOCKER`; (c) host-shaped disk whose C: is real NTFS (`mkntfs -Q`) with `/hiberfil.sys` starting `HIBR` ->
`R_HIBERNATED`; (d) in G2, a second install on the installed T3 disk -> `R_ALREADY_INSTALLED`; (e) in G1, wrong
`db.confirm` -> `CANCELLED`; (f) in G1, no Windows Boot Manager entry -> `R_NO_WINDOWS_ENTRY`. (a)-(c) each run in
their own session with a fresh `.nvram` (they fail before check 15, no NVRAM preparation needed).

**T4 uninstall (ISO)**, G3. Boot (BootNext from G2 starts the CD), `s`; shell snapshot of the EFI variables; `3`, the
disk, `REMOVE nvme0n1`. Pass: `@@GB RESULT flow=uninstall status=OK`; `@@GB UNINSTALL gpt_matches_pre_install=yes`;
`gpt_validate.py validate t3.img --snapshot pre.json --expect uninstalled --plan plan.json` RESULT PASS; BootOrder ==
this session's before-BootOrder minus the Googlebook number (== the G2 before-order); the Googlebook `Boot####` gone,
all other variables byte-identical to this session's snapshot.

**T4b uninstall (Windows tools) of an ISO install**, G4. Boot, `s`, NVRAM preparation; install again (T3 flow, same
pass criteria 1 and 3), pull `/run/gb/state/*` into `C:\GB-x86\iso\test\work\t4b\gbinstall\`; BootNext-to-CD rule;
power off. Then `Import-GooglebookIsoInstall.ps1 -TargetImage t3.img -From ...\gbinstall` (dry run: image unchanged),
`-Apply` (exit 0), `uninstall_dualboot.ps1 -TargetImage t3.img -Apply` (exit 0, step 4 prints the guard A text, no
guard B failure), `gpt_validate.py validate --expect uninstalled` PASS. Finally boot the ISO again (same `.nvram`):
`3` lists "boot entry only", `REMOVE efi` -> OK, BootOrder back to the order recorded right after the NVRAM
preparation.

**T4n uninstall refusal on a hibernated Windows disk.** Builder: a host-shaped disk whose C: is real NTFS with
`/hiberfil.sys` starting `HIBR` (as T3n(c)) plus, in free space after D:, 18 partitions of 1 MiB each created with
`sgdisk` (on a loop device over the image file) carrying the names, type GUIDs, partition GUIDs and attributes of the
export manifest in placement order, contiguous. Fresh `.nvram`, `s`, shell EFI snapshot, `3`, the disk ->
`REFUSED code=R_HIBERNATED` (checks `mismatch` and `foreign_between` `ok`); disk byte-identical; EFI snapshot
identical.

**T5 Rufus ISO-mode equivalent.** Builder: 9 GiB raw, GPT, partition 1 from 1 MiB, type 0700 (Basic Data), FAT32
`mkfs.vfat -F 32 -n GBSB3INST`, all files of the ISO tree copied (`cp -r` from a loop-mounted ISO). On sata0:1
(nonpersistent), no CD, nvme0:0 a blank 1 GiB disk. Pass: GRUB menu seen and `s` boots it (from the FAT32 disk);
`@@GB PAYLOAD kind=marker dev=/dev/sda1 disk=sda`; tools `v` -> `@@GB RESULT flow=verify status=OK`; in `1` the disk
`sda` carries flag `payload`. If VMware does not boot a Basic Data FAT32 partition, rerun with type EF00 and record
"VMware needed EF00" (open issue for real Rufus sticks).

**T6 Ventoy equivalent.** Builder: (1) 9 GiB raw, GPT, one exFAT partition `mkfs.exfat -L Ventoy`, the ISO copied to
`/ISO/googlebook-sb3-installer.iso`; (2) a second 1 GiB raw, GPT, one plain exFAT partition (label `DATA`) holding
`/old/googlebook-sb3-installer-old.iso` = a fake older build made with `xorriso -as mkisofs -V GBSB3INST` from a tree
with `gbsb3/GBSB3-00000000-000000-00000000.marker` and a 64 MiB zero file (>= 64 MiB in total). Disk (1) on sata0:1,
disk (2) on sata0:2 (both nonpersistent); CD = ISO (boot only); GRUB hotkey `d` (11.1). Pass: `@@GB BOOT ...
discover=marker,isofile`; `@@GB PAYLOAD kind=isofile dev=/dev/loop0 disk=<kname of disk (1)>
file=/ISO/googlebook-sb3-installer.iso` and its `protected=` contains the knames of disks (1) and (2) (and `sr0`); no
probe of sr0 is reported as the payload; verify OK; in `1` both disks carry flag `payload`.

**T6n Ventoy-NTFS equivalent.** As T6 disk (1) but the partition is NTFS made by `mkntfs -Q -L Ventoy` (ISO copied
with ntfs-3g on the builder's loop device); no disk (2); GRUB hotkey `t`. Pass: `@@GB BOOT ...
discover=marker,isofile`; `@@GB PAYLOAD kind=isofile ... file=/ISO/googlebook-sb3-installer.iso`; verify OK; the disk
carries flag `payload`.

**T7 dd mode.** `D:\GB-iso\test\disks\t7.vmdk` = flat descriptor onto the ISO hard link with the extent line
`RDONLY <iso_bytes/512> FLAT "<hardlink>" 0` (the ISO has the read-only attribute) on sata0:1,
independent-nonpersistent; no CD; nvme0:0 blank 1 GiB. Pass: the firmware boots the disk, GRUB `s`, `@@GB PAYLOAD
kind=iso9660 dev=/dev/sda1 disk=sda`; verify OK; T2e. After power-off the ISO's sha256 is unchanged.

**T8 Windows scripts.** In order, logs under `C:\GB-x86\iso\test\logs\<build_id>\t8\` (pass `-LogDir`), images on
the drive with more free space (>= 30 GiB free required, else SKIPPED): `test\test_bcd_parse.ps1` (`SUMMARY bcd-parse:
0 failed`); `test\run_edge_tests.ps1 -WorkDir <dir> -ExportDir D:\gb-test\export_small` (`SUMMARY edge cases: 56
passed, 0 failed`); `test\run_image_tests.ps1 -Image <dir>\cycle_small.img -ExportDir D:\gb-test\export_small -Tag
iso_t8 -DeleteImage` (`SUMMARY 'iso_t8': 57 passed, 0 failed`: 25 + the 32 cases of WAVE3_PLAN 5.4); `test\test_import_iso_state.ps1` (0 failed; includes
the stale-pointer case of 4.6). All run with `powershell -NoProfile -ExecutionPolicy Bypass -File ...`.

`summary.txt`: one line per test `PASS|FAIL|SKIPPED|INCONCLUSIVE <id> <reason>` and the build id; overall PASS only if
T0, T1a, T1b, T1d(n), T1d(y), T2a-j, T3, T3n(a-f), T4, T4b, T4n, T4nb, T5, T6, T6m, T6n, T6g, T7, T7b, T8, T9, T10 all
PASS (T1c optional). The tests added in v2.2 (T1d, T2j, T4nb, T6g, T6m, T7b, T9, T10) and their pass criteria are in
section 12.

---------------------------------------------------------------------------------------------------------------------

## 8. Ownership and shared interfaces

### 8.1 Who owns what

| owner | files | delivers |
|---|---|---|
| ARCHITECT | `C:\GB-x86\iso\DESIGN.md` | this spec (changes only by explicit revision) |
| INSTALLER | `C:\GB-x86\iso\installer\**`; `C:\GB-build\native\dualboot\uninstall_dualboot.ps1` (guards A and B only), `Import-GooglebookIsoInstall.ps1`, `test\test_import_iso_state.ps1`, README_DUALBOOT.md section | init, UI, flows, run_tool, state files, modules.list, initramfs.list, Windows import |
| BUILD | `C:\GB-x86\iso\build\**` (incl. `forbidden_scan.py`), `C:\GB-x86\iso\README-ISO.md`, `C:\GB-x86\iso\work\**`, `D:\GB-iso\*` (top level), `/var/tmp/gbiso/{cache,last,<build_id>}` | export, payload, initramfs, GRUB, ISO, build record, forbidden-token scan |
| TEST | `C:\GB-x86\iso\test\**`, `D:\GB-iso\test\**`, `/var/tmp/gbiso/test/` | harness incl. pre-start guard, media, T0-T8, summary |

Nobody edits another owner's files; mismatches are reported, not patched.

### 8.2 Interface index (where each contract is defined)

| interface | producer -> consumer | section |
|---|---|---|
| ISO tree paths, volume label, marker name/content, build id | BUILD -> INSTALLER, TEST | 1.1, 1.2 |
| `manifest.json` keys, object ids, chunk naming, hash definitions | BUILD -> INSTALLER, TEST | 1.3, 1.4 |
| dual-boot export location and checks | BUILD -> TEST (expected plan) | 1.5 |
| GRUB early config, menu ids/hotkeys/titles, SMBIOS gate, `gb_args` | BUILD -> TEST | 2.1, 2.2 |
| kernel parameters | BUILD (grub.cfg) -> INSTALLER; TEST via hotkeys | 2.3 |
| initramfs mapping, generated files, `--selftest`, environment guard | INSTALLER <-> BUILD | 3.1 |
| `initramfs.list` grammar, Python pruning, allowed stdlib, allowed ioctls | INSTALLER <-> BUILD | 3.2 |
| `modules.list`, resolution, denylist | INSTALLER -> BUILD | 3.3 |
| runtime paths `/run/gb/...`, UI start sequence | INSTALLER -> TEST (shell pulls) | 3.4, 3.6, 4.0.14, 4.5, 5.1 |
| discovery, protected scan, enumeration, media scan flags, read-only guard | INSTALLER -> TEST | 3.6-3.10 |
| run_tool allow-list (`installer/lib/gb/run.py`), forbidden tokens | INSTALLER <-> BUILD (scan), TEST (T0) | 4.0.10, 4.0.11 |
| journal format and phases | INSTALLER -> TEST | 4.0.14 |
| flows, checks order and names, invariants, EFI load-option rules | INSTALLER -> TEST | 4 |
| `\gbinstall\` files, JSON schemas, partition-number semantics | INSTALLER -> Windows scripts, TEST | 4.5 |
| Windows script changes | INSTALLER -> TEST (T4b, T8) | 4.6 |
| markers, prompt ids, replies, result codes | INSTALLER -> TEST | 5.2-5.4 |
| `build_iso.py` CLI, output files, build record | BUILD -> TEST | 6.2, 6.3 |
| test VM/pipe/disk conventions, pre-start guard, NVRAM groups | TEST | 7.1, 7.2 |

### 8.3 Constants (single source)

```
SECTOR = 512; ALIGN = 1048576; CHUNK = 536870912; RESERVE = 1073741824; USERDATA_MIN = 8589934592
HEAD_TAIL = 67108864; BIG_BLANK = 268435456; HEADTAIL_HASH = LBA 0..2047 + last 2081 LBAs
TYPE_ESP = c12a7328-f81f-11d2-ba4b-00a0c93ec93b   TYPE_LINUX = 0fc63daf-8483-4772-8e79-3d69d8477de4
TYPE_VBMETA = 88434509-d9d1-487d-b82c-15ef964cbd4b TYPE_BASIC = ebd0a0a2-b9e5-4433-87c0-68b6b72699c7
TYPE_MSR = e3c9e316-0b5c-4db8-817d-f92df00215ae    TYPE_WINRE = de94bba4-06d1-4d40-a16a-bfd50179d6ac
TYPE_LDM_META = 5808c8aa-7e8f-42e0-85d2-e1e90434cfb3  TYPE_LDM_DATA = af9b60a0-1431-4f62-bc68-3311714a69ad
TYPE_STORAGE_SPACES = e75caf8f-f680-4cee-afa3-b001e56efc2d
EFI_GLOBAL = 8be4df61-93ca-11d2-aa0d-00e098032b8c; LOAD_OPTION_ACTIVE = 0x00000001
GB_NAMES = EFI-SYSTEM boot_a boot_b init_boot_a init_boot_b vendor_boot_a vendor_boot_b vbmeta_a vbmeta_b pvmfw_a
           pvmfw_b misc metadata frp_persist desktop_security_persist desktop_security_storage super userdata
ALLOWED_NEW_ATTRS = 0x01FF000000000000 (vbmeta slot bits only = bits 48..56)
VOLUME_LABEL = GBSB3INST; BOOT_LABEL = "Googlebook OS"; LOADER = \EFI\BOOT\BOOTX64.EFI
WINDOWS_LOADER = \EFI\Microsoft\Boot\bootmgfw.efi; VMWARE_VENDOR = "VMware, Inc."
IOCTL (allowed): BLKGETSIZE64 0x80081272, BLKSSZGET 0x1268, BLKRRPART 0x125f, BLKFLSBUF 0x1261, BLKROSET 0x125d,
                 BLKROGET 0x125e, BLKGETDISKSEQ 0x80081280
IOCTL (forbidden): BLKDISCARD 0x1277, BLKSECDISCARD 0x127d, BLKZEROOUT 0x127f
INITCALL_BLACKLIST = init_tis,crb_acpi_driver_init
PIPE = \\.\pipe\gbiso_con; ISO = D:\GB-iso\googlebook-sb3-installer.iso
```

### 8.4 Host-side unit tests

INSTALLER may run unit tests on the Windows host (`python -I installer\tests\...`) only against pure modules that do
byte/dict computations (GPT build/parse, plan, manifest parsing, EFI load-option parsing, run_tool argument
validation tables, JSON writers) and contain no device, mount, ioctl or process-creation code. The flow modules,
`run.py`'s launcher and `gbinstall.py` main are never executed on the host (the environment guard of 3.1 also refuses).

---------------------------------------------------------------------------------------------------------------------

## 9. Open issues and risks

1. **Real hardware is untested by design** (VMware only): GRUB 2.12 + Ubuntu kernel (NX cleared) on Surface UEFI; the
   SAM keyboard stack in this initramfs (module order, pinctrl_icelake, timing); simpledrm at 3000x2000 and the
   32x64 font (needs the tall-font ioctl; kernel 6.8 has it and kbd 2.6.4 should use it; falls back to 16x32);
   efivarfs writes on the Surface firmware; whether Surface UEFI "Boot configuration" shows an efibootmgr-made entry
   (README_DUALBOOT already lists this); whether `surface_charger` reports `ADP1` online on the SB3 (if it does not,
   modes 2/3 refuse with "power state unknown" — fallback: the Windows scripts); whether Windows assigns a
   `\\?\Volume{}` path to the second ESP (else the tools-`w` fallback of README-ISO).
2. **Ventoy and Rufus themselves cannot be tested** (no downloads): T5/T6/T6n are equivalents. Ventoy normal mode is
   expected to use its Debian-family hook (our /proc/version says Ubuntu), exec our `/init` with /proc, /sys, /dev
   possibly mounted and may create `/dev/mapper/ventoy` (dm over the stick): 3.4 mounts conditionally, 3.6 probes
   `dm-*` and treats EBUSY as "held", 3.7 resolves dm slaves. Fallback: Ventoy's GRUB2 mode (runs our grub.cfg with
   root=(loop), no injection) or a Rufus stick (README-ISO). Rufus GPT targets use Basic Data FAT32; VMware (T5) and
   the Surface must boot that.
3. **lapis.bin is live**: an OTA in the running VM makes the export cross-check fail (by design): the USB image must
   then be rebuilt and re-verified before an ISO build. A fresh export reads ~12.6 GiB of lapis.bin.
4. **Whole-disk GUIDs = the USB image's**: every stick written from this ISO (or by dd from the .img) has the same
   disk/partition GUIDs; the installer refuses to write while another such stick is attached, but two sticks used on
   different occasions are fine.
5. **Dual-boot GUIDs are per export**: kept stable across ISO rebuilds by reusing `C:\GB-x86\iso\work\export`; an ISO
   built with `--new-export` cannot uninstall an install from an older export (Windows `uninstall_dualboot.ps1
   -Force` or the older ISO can). Cross-build uninstall from on-disk state is a possible v2 feature.
6. `\gbinstall\` on the Googlebook ESP is lost by `install_dualboot.ps1 -UpdateEspOnly`: import first (README-ISO).
7. **NTFS checks** use ntfs-3g's `ntfsls`/`ntfscat` read-only (libntfs-3g skips the hibernation/log checks for
   read-only opens); a volume they cannot read blocks modes 2/3 (`R_NTFS_UNCHECKED`, no override in v1). ntfs3 is
   still used read-only for payload discovery on USB disks; how it treats a dirty NTFS stick is irrelevant to Windows
   (worst case: such a Ventoy-NTFS stick is not found; use chkdsk on Windows).
8. TPM: the built-in TPM driver initcalls are blacklisted (2.2), so the kernel never talks to the Surface fTPM and IMA
   has no TPM. The kernel's EFI stub may still ask the firmware (TCG2 protocol) to measure the initrd into a PCR
   (volatile, reset at reboot; no persistent TPM change). BitLocker must be off for dual boot anyway.
9. Module autoload is limited to the included set: unusual USB keyboards needing other HID drivers will not work
   (PS/2-style SAM keyboard, generic USB HID, Microsoft and Logitech receivers are covered).
10. GRUB's menu uses the firmware text console (tiny on 3000x2000); acceptable for a 10 s menu with a default entry.
11. Disk space is tight: D: ~34 GB free today (ISO ~7.5 GiB + one test disk set <= 13.5 GiB); `D:\gb-usbtest` (20 GB)
    and `D:\gb-export` (12.6 GB, regenerated at 02:42 today by other work) are not ours to delete — the user may free
    them.
12. Builder packages needed: `xorriso` (1.5.6), `mtools`, `exfatprogs` (Ubuntu archive; `setup.sh`; installed by the
    boot review already).
13. RESOLVED: `--install-modules` of Ubuntu's grub-mkstandalone pulls in the dependency closure and leaves tpm.mod out
    (boot review test build: 1.4 MB, DllCharacteristics 0x0000); BUILD/T0 still check it (2.1).
14. The ISO carries Google's proprietary image: personal use only, never published (README-ISO and the ISO's
    application id say so).
15. The read-only guard (3.10) stops userspace writes and rw mounts, not I/O a mounted file system issues itself; the
    design therefore never mounts a Windows NTFS volume and mounts everything else read-only.

---------------------------------------------------------------------------------------------------------------------

## 10. Review decisions (v2)

S = safety review, B = boot review, A = architect's own corrections found while revising. "Accepted" means the fix is
in the sections named; "changed" means accepted with the stated deviation; "rejected" gives the reason.

| id | issue (short) | decision | where |
|---|---|---|---|
| S1 | mode 1 can erase recovery drive / Windows media / GB-backup stick / Ventoy stick / old BitLocker To Go | **Changed.** `R_PROTECTED_MEDIA` added after `R_DUPLICATE_GUIDS` (T2a-f codes unchanged) with flags `winmedia`, `gbbackup`, `ventoy` and an extra `unscanned` (a walk that hit its time limit must not pass as clean). NTFS partitions are not scanned: any disk with an NTFS boot sector is already refused by `R_WINDOWS_DISK` (check 7 < 9), so ntfscat-based scanning would never decide anything. Old-style BitLocker (blkid `TYPE=BitLocker`) is folded into `R_BITLOCKER` instead (T2c unchanged), flag `bitlocker`. Savelog/gptbackup refuse these disks. `wd.confirm` lists every partition (label on screen only — labels can be personal and never reach logs/markers). T2g, T2h, T2i added | 3.9, 4.1, 4.4, 7.2 |
| S2 | "Windows stays default" not enforced | **Accepted.** `R_NO_WINDOWS_ENTRY` (exact load-option rules), never creates BootOrder, re-verified before efibootmgr, index assertion after `--bootorder`, rollback. T3n(f) added; NVRAM preparation in G1 and G4 (T3, T4b) | 4.2, 7.2 |
| S3 | ntfs3 on Windows volumes; unverified rw-mount node | **Changed.** (1) Windows NTFS is read only with `ntfsls` + `ntfscat` (ntfsls added: ntfscat alone cannot tell "no hiberfil.sys" from "volume unreadable"); ntfs3 remains ONLY for read-only payload discovery/protected scan on USB disks (Ventoy-NTFS and Rufus-NTFS sticks need a kernel mount to loop-mount the ISO file), never on internal disks. (2) Read-only guard accepted and also applied before every probe mount, with its 6.8 semantics stated precisely: it blocks userspace writes and rw mounts but is not relied on against file-system-internal bios (block layer only warns), hence (1). (3) Node verification before both rw mounts accepted | 3.10, 4.0.8, 4.0.13, 4.2.2 |
| S4 | protected set stops at first hit; dm/loop not resolved | **Accepted.** Separate full scan, any build, recursive slaves/backing_file, EBUSY-held disks protected; T6 second disk with an older-build ISO file | 3.7, 7.2 |
| S5 | internal NVMe erasable if Windows detection fails | **Accepted.** Union enumeration (primary, backup, MBR/EBR, kernel) for all detections; `R_INTERNAL_NOT_BLANK` after the existing checks (T1a/T1b/T2f blank, codes unchanged) | 4.0.16, 4.1 |
| S6 | mode 3 lacks safety checks | **Accepted.** readonly, in_use, sector_size, GPT checks, hibernated (ntfs-3g), power; BitLocker warns. T4n added (mode-3 hibernated case; T3n(c)-style disk plus sgdisk-made Googlebook entries) | 4.3, 7.2 |
| S7 | no mains-power check | **Accepted** as proposed (surface_charger/surface_battery; unknown refuses on hardware, ignored in a VM; no override — the Windows scripts remain the fallback, open issue 9.1) | 3.3, 4.0.15, 6.4 |
| S8 | identity re-check weak; GPT not re-read before the GPT write; LBA 0 | **Accepted.** diskseq via BLKGETDISKSEQ 0x80081280 (number verified: `_IOR(0x12,128,__u64)`), head/tail hash, `R_DISK_CHANGED` (new code: previously the re-check had none), O_DIRECT re-read before the GPT write, GPT-only areas and sector-0 assert in modes 2/3 | 4.0.1-4.0.3 |
| S9 | destructive tool defaults outside the gate | **Changed.** run_tool allow-list and BUILD scan accepted; one deviation: `efibootmgr --delete-bootnext` is allowed, but only when BootNext equals a number this run deleted (or created and rolled back) — otherwise BootNext could keep pointing at a removed Googlebook number that a later entry might reuse. All long option forms are fixed; efivarfs ro except inside 4.2.10/4.3.5 | 4.0.10-4.0.12 |
| S10 | stale C:\GB-backup pointer half-uninstalls | **Accepted** with one robustness tweak: `$state.plan -and` before `Test-Path` (Test-Path $null throws under ErrorActionPreference Stop). Verified against uninstall_dualboot.ps1 (lines 99-113) and run_edge_tests E3/E4/E7/E8/E9 and run_image_tests: they still pass. Stale-pointer case added to test_import_iso_state.ps1 | 4.6 |
| S11 | harness could reach real disks / host USB | **Changed (stronger).** Pre-start guard accepted as proposed; instead of `usb_xhci` with auto-connect off the VM has no USB controller at all (no test needs one), plus the runtime kill-switch | 7.1 |
| S12 | interrupted Android vbmeta GPT write blocks mode 3; no post-boot check | **Accepted.** vbmeta-bit tolerance in mode 3, tools `c`, README steps | 4.3, 4.4, 6.4 |
| S13 | lowest-free-slot placement and `number` semantics | **Accepted.** Slots after the highest used slot (T3 slots 5..22 unchanged); `number` = gbdisk ordinal (verified gbdisk.cs:606); slot+1 only for efibootmgr/HD()/`iso.efi_partition_number`, asserted against sysfs | 4.2, 4.5 |
| S14 | no off-disk GPT backup, no phase memory | **Accepted** and extended to mode 3 (`pre_uninstall`): README preparation with `gbdisk backup --disk 0 --tag pre_iso --out ...` (CLI verified in gbdisk usage), optional `gptbackup.part` copy (`F_BACKUP` on failure, nothing written), journal with phases, `journal.ack`, `R_INCOMPLETE_RUN` | 4.0.14, 4.2, 4.3, 6.4 |
| S15 | dynamic/Storage Spaces and non-Windows disks accepted in mode 2 | **Changed.** `R_WINDOWS_DYNAMIC` as proposed. `R_NO_WINDOWS` uses GPT types only (ESP + MSR + Basic Data): the review's "NTFS Basic Data" (OEM ID) would make T3 fail, because `gpt_validate.py mkimage` fills partitions with random bytes, not NTFS boot sectors (checked in the tool). Both placed after `R_NO_WINDOWS_ENTRY`, so T3n codes stay | 4.2 |
| S16 | `--reuse-export` could import Windows-install GUIDs | **Changed.** `--reuse-export` removed (that alone closes the hole: every ISO export now comes from a fresh `build_internal.py` run, never `--reuse-guids`). The proposed abort on GUID overlap with `C:\GB-backup\**` / gb-export manifests is made informational (`guid_overlap` in export_check.json): after an import, `C:\GB-backup` legitimately holds this very export's manifest, so an abort would break every rebuild after the first ISO install | 1.5, 6.2 |
| S17 | manual mountvol for the import | **Changed.** `-EspId` accepted; the volume path is taken from `Get-Partition ... .AccessPaths` (not assumed to equal the partition GUID) and files are read into memory (no temp writes in a dry run). Fallback without mountvol: tools `w` copies `\gbinstall\` to a stick, then `-From` | 4.4, 4.6, 6.4 |
| S18 | Ubuntu TPM drivers / risky modules | **Accepted.** `initcall_blacklist=init_tis,crb_acpi_driver_init` (names re-verified read-only in System.map-6.8.0-147-generic), BUILD symbol check, module denylist | 2.2, 2.5, 3.3 |
| B1 | Ventoy normal mode hand-over and dm map | **Accepted** (a)-(e): dm probing, EBUSY = held, conditional mounts in /init, depth 6 / 10 s, init.log diagnostics | 3.4, 3.6, 3.7 |
| B2 | dd stick: whole disk mounted before sda1 | **Changed.** The skip rule is "the kernel created partition devices for this whole device", not "blkid shows PTTYPE": `sr0` and Ventoy's `dm-N` carry the ISO's hybrid GPT (PTTYPE) but have no kernel partitions, and the PTTYPE rule would skip the Ventoy map that B1 needs. T7 now expects `dev=/dev/sda1` | 3.6, 7.2 |
| B3 | forced 16x32 font breaks 80x24 | **Accepted.** `fbcon=font:` removed, W/H ladder with fit check, 8x16 font added, progress rate limit | 2.2, 3.1, 3.5 |
| B4 | console order in TEST entries | **Accepted** | 2.2 |
| B5 | early.cfg fall-through after Esc | **Accepted** | 2.1 |
| B6 | no VMD driver | **Accepted** | 3.3 |
| B7 | GRUB checks: NX only logged, shim_lock check vacuous | **Accepted** (NX_COMPAT clear is a MUST in BUILD and T0; check reworded as module files in the memdisk; open issue 13 resolved) | 2.1, 7.2, 9 |
| B8 | El Torito size limit | **Accepted** | 2.4, 7.2 |
| B9 | packaging: python ldd libs, modules.builtin, merged-usr /bin | **Accepted** | 3.1, 3.2, 3.3 |
| B10 | IOMMU on in the installer | **Accepted** (`iommu.passthrough=1`; `reboot=pci`, `efi_pstore.pstore_disable=1` kept) | 2.2 |
| B11 | README compatibility limits | **Accepted** | 1.6, 6.4 |
| B12 | T7 hard link of a read-only ISO | **Accepted** (`RDONLY` extent; the pre-start guard allows RDONLY) | 7.1, 7.2 |
| B13 | NTFS payload discovery untested | **Changed.** Instead of editing a GRUB entry at runtime, a third VMware-only entry `gb_serial_ntfs` (hotkey `t`) passes `gb.ntfs_any=1` (honoured only when `is_vmware`); T0 now expects 8 entries; T6n added | 2.2, 2.3, 7.2 |
| A1 | T4 could not find the Googlebook entry: v1 deleted `.nvram` before every test | NVRAM session groups G1-G4; T4 runs on T3's NVRAM | 7.1, 7.2 |
| A2 | after T3 the VMware disk entry may boot the Googlebook ESP's fallback loader instead of the CD | BootNext-to-CD rule | 7.1 |
| A3 | efivarfs is now ro: TEST's NVRAM preparation would fail | remount rw/ro in the preparation commands | 7.2 |
| A4 | `@@GB CHECK name=` values were never defined | check-name columns in 4.1/4.2/4.3; T tests may assert them | 4, 5.2 |
| A5 | EFI entry verification assumed efibootmgr writes an abbreviated HD() path | nodes before the HD node accepted and logged | 4.2 |

---------------------------------------------------------------------------------------------------------------------

## 11. Integration decisions (v2.1, INTEGRATOR, after the first full build and the VMware test runs)

Each item: what the first integrated run showed, the decision, and where it is implemented. Where this section and
an earlier section differ, this section wins.

1. **GRUB hotkey of `gb_serial_nocd` is `d`, not `v`.** GRUB 2.12's menu loop handles `v` (and `^`) as "move the
   highlight down/up" before it looks at hotkeys, so `--hotkey=v` can never fire: T6 moved the highlight to the second
   entry and stopped the countdown (no boot). Changed in `build/templates/grub.cfg.in`, 2.2, T0 (`t0_static.py`) and
   the harness (`gbtests.DISCOVER`, T6). Other reserved menu keys (`c`, `e`, Enter) are not used as hotkeys.
2. **FAT check of the new ESP (4.2.11, T3 criterion 6).** `build_internal.py`'s `EFI-SYSTEM.img` carries its volume
   label only in the boot sector. dosfstools 4.2 `fsck.vfat -n` reports "Label in boot sector is 'GBOOK_ESP', but
   there is no volume label in root directory" and exits 1 on the untouched export image too. The installer accepts
   exit 1 only when every reported line is that note or an informational line (`run.fat_check_verdict`, selftest
   `fat_verdict`); any other finding is still `F_ESP_STATE`. The initramfs has no iconv (gconv) modules, so dosfstools also prints "Cannot initialize conversion from codepage 850 ..." / "Using internal CP850 conversion table" on stderr; those three lines are informational too. The F_ESP_STATE message names the first unexpected line. T3 criterion 6 (`check_esp_tree.sh`) passes when the
   installed ESP gives exit 0 or exactly the reference image's findings. The ESP content stays byte-identical to the
   export (1.5 cross-check 5 is unchanged).
3. **T3 criterion 1 counts 18 `@@GB VERIFY result=ok` lines** (one per written partition, the ESP before
   `\gbinstall\` is added), like mode 1. "17" in 7.2 was a miscount.
4. **First marker at column 0.** GRUB leaves its menu screen on ttyS0 without a final newline, so the first UI line
   followed GRUB's escape sequences. The UI now prints an empty line before `@@GB BOOT`; the harness additionally
   accepts a marker that follows terminal residue containing ESC (any other mid-line `@@GB ` is still not a marker).
5. **Console font (3.5).** Ubuntu's kernel uses deferred framebuffer-console takeover: until text reaches the console,
   tty1 is the dummy console (80x25) and every `setfont` fails, so the ladder always ended at L0. `/init` now writes
   one line to tty1 and waits <= 3 s for fbcon to bind (`/sys/class/vtconsole/vtcon*/bind`) before the ladder. L3
   (32x64) can never work on 6.8: fbcon accepts glyphs at most 32 pixels high (`struct fb_pixmap` `blit_y` is a
   32-bit mask in the 6.8 headers, "supported bit block dimensions (1-32)"), and `setfont` gets
   `KDFONTOP: Invalid argument` (measured in VMware, T10); the ladder falls back to L2 16x32 (Surface: about 187x62
   characters). The 32x64 file is still shipped (3.1).
6. **ISO size**: 7,474,923,520 bytes (6.96 GiB) for every build so far; super compresses to about 6.3 GiB (several
   512 MiB chunks are incompressible, the last 8 are zero). The 8.0 GiB ceiling stays. A Ventoy partition needs about
   7.5 GB free.
7. **Harness**: `common.assert_test_path` accepts a TEST root itself (it refused `mkdir D:\GB-iso\test`); builder media
   and transfers use Git for Windows' `ssh` found first on PATH.
8. **T8 / run_edge_tests E10** passes the string `\.\PhysicalDrive0` to `gbdisk layout --image` and expects the
   refusal; gbdisk rejects the path by its text before any open (and the harness is not elevated). Kept: it is the
   test that proves the refusal, not a disk access. Since 12.E14 T8 asserts the non-elevated session before it runs
   run_edge_tests (and refuses to run them elevated).

---------------------------------------------------------------------------------------------------------------------

## 12. Audit-2 decisions (v2.2, INTEGRATOR, after the safety audit and the evidence audit of build 20261025-044111)

S = safety audit, E = evidence audit (numbered in the order of the audit reports). Where this section and an earlier
section differ, this section wins. Every HIGH/MEDIUM finding is applied; LOW findings are applied unless rejected
below with the reason.

| id | sev | finding (short) | decision | where |
|---|---|---|---|---|
| S1 | M | a whole-device FAT/exFAT stick ("superfloppy") skips mode 1's media scan | **Applied.** 3.9: a disk without kernel partitions whose whole-device blkid `TYPE` is vfat/exfat or whose sector 0 is FAT-like gets one pseudo-range (first LBA 0, the whole disk, kname = the disk) scanned exactly like a partition (`winmedia`, `gbbackup`, `bootmgfw` -> `windows`, `unscanned` if the read-only mount fails). The `wd.confirm` listing prints it as "whole-disk file system (no partition table)". T2j (whole-device exFAT with `GB-backup/` -> `R_PROTECTED_MEDIA`, flag `gbbackup`); builder_tests `media` (loop devices: exFAT/FAT32/FAT16 superfloppies with GB-backup, install.wim, bootmgfw.efi; a GPT control; a blank disk; the listing text) | disks.py, T2j |
| S2 | L | on BitLocker, mode 3 reports Fast Startup ok without having looked | **Applied.** `flowlib.hibernation()` returns the BitLocker partitions it could not check. Mode 2: `R_NTFS_UNCHECKED` (R_BITLOCKER refuses first anyway). Mode 3: `CHECK hibernated result=warn` and, after all checks passed, prompt `hiber.ack` (exactly `FASTSTARTUP-OFF`); anything else -> `R_NTFS_UNCHECKED`. T4nb (T4n-shaped disk, C: = BitLocker boot sector; reply `no` -> REFUSED R_NTFS_UNCHECKED, disk and EFI unchanged) | flowlib.py, uninstall.py, dualboot.py, 5.3, T4nb |
| S3 | L | Ctrl+C / Ctrl+\ / Ctrl+Z / Ctrl+Alt+Del not blocked during writes; KeyboardInterrupt skips the EFI rollback | **Applied.** `flowlib.destructive()` around every `execute()` (and mode 3's boot-entry-only removal): SIGINT, SIGQUIT, SIGTSTP = SIG_IGN, flag file `/run/gb/flow.running` with the UI's pid; `gbinstall.py --shutdown reboot|poweroff` (Ctrl+Alt+Del) refuses while the flag names a live UI process ("a disk write is in progress"); `efi_create()` rolls back on BaseException and re-raises non-Exceptions; `ui.run_flow()` turns KeyboardInterrupt into `FAILED F_INTERRUPTED` with the journal result written. builder_tests: signals ignored inside/restored after, SIGINT delivered inside is ignored, rollback once for RuntimeError and for KeyboardInterrupt | flowlib.py, journal.py, dualboot.py, ui.py, gbinstall.py, 5.4 |
| S4 | L | disks that cannot be read silently drop out of the cross-disk checks | **Applied.** They stay listed with flag `unreadable` and their error; `flowlib.pick_disk` refuses to choose them; `duplicate_guids` (mode 1) and `already_installed` (mode 2) report `R_DISK_UNREADABLE` while one is attached (unless the check already failed for its own reason), mode 3 refuses with it before listing installs; savelog/gptbackup skip them; tools `c` only notes them. builder_tests `media` (enumerate_disks with an unreadable loop disk) | disks.py, flowlib.py, wholedisk.py, dualboot.py, uninstall.py, stick.py, 5.4 |
| S5 | L | the installer's read-only list is keyed by kname only | **Applied.** `/run/gb/roguard.list` holds `<kname> <diskseq>`; a disk's read-only state counts as installer-set only for the same diskseq; `Target.prepare()` clears the whole-device flag only then and otherwise refuses with `R_READONLY` (nothing written). builder_tests `media`: the same loop kname with a new diskseq is not covered; a user-set flag -> R_READONLY and kept | sysfs.py, disks.py, blockio.py, 3.10 |
| E1 | H | "Ventoy/Rufus equivalents: the stick layouts boot" overstated: T6/T6n booted from the CD | **Applied.** (1) Wording fixed everywhere: T5 (FAT32 stick) and T7/T7b (dd) boot from the stick; T6/T6n test only the isofile discovery. (2) **T6g**: no CD; a stock `grub-mkstandalone` GRUB (Ubuntu archive) on a FAT ESP labelled VTOYEFI loop-mounts `/ISO/googlebook-sb3-installer.iso` from the exFAT "Ventoy" partition, sets `root=(loop)` and runs the ISO's grub.cfg (Ventoy's GRUB2-mode fallback); hotkey `s` (default discovery) -> `PAYLOAD kind=isofile`. (3) **T6m**: Ventoy normal mode's `/dev/mapper/ventoy` reproduced in the throwaway VM: archive `dmsetup` (+ libraries, from a separate FAT test disk, run through the initramfs's ld.so) creates a read-only dm-linear map of the .iso file's disk extent (FIBMAP on the builder, contiguity proven by hashing the disk range) over the whole Ventoy disk; tools `r` -> `PAYLOAD kind=iso9660 dev=/dev/dm-0 disk=<the stick>`, the exFAT partition is refused with EBUSY, the stick is protected, verify OK through the map. (4) **Rejected (optional)**: Ventoy's compatibility marker and the `VentoyOsParam` variable could not be checked against ventoy.net (no downloads); 1.1 still forbids `/ventoy` in the initramfs. (5) README-ISO: Rufus ISO mode recommended, Ventoy untested, keep `init.log` (tools l / w) if Ventoy fails; README.txt likewise | gbtests.py (t6g, t6m_body), builder_media.sh (t6a EXTENT, t6d, t6g), README-ISO, README.txt.in |
| E2 | M | the whole-disk zeroing was never exercised (T1 targets were blank) | **Applied.** **T1d(n)** and **T1d(y)**: a 32 GiB NVMe raw with deterministic pattern data (64 KiB spots every 8 MiB, the first/last MiB of every would-be partition of both layouts, all of every small blank partition, 64 MiB at both ends of userdata in both layouts, both gaps) and blank LBA 0..2047 / last 2081 LBAs (else R_INTERNAL_NOT_BLANK); mode 1 with grow n / y. Pass: the T1b/T1c criteria (GPT, contents, blank partitions zero per 4.0.5, LBA 34..2047 and the gap zero, verify_image) plus: every spot inside the written areas (4.0.2/4.1.6) was overwritten, every spot outside them (the middle of userdata; with grow n also the space between the image end and the zeroed tail) still holds the pattern, LBA N-2081..N-34 zero. (The head zeroing of a non-blank head cannot be shown on a non-USB VM disk: internal_not_blank refuses such a disk by design.) | mkdisks.t1d, validate_wholedisk.check_patterned |
| E3 | M | high-DPI font paths never ran; the 32x64 claim was untested | **Applied.** **T10**: VMware's EFI GOP offers at most 2048x1536 (svga.maxWidth/maxHeight/vramSize, `video=efifb:auto` for the kernel's EFI stub; GRUB's gfxpayload is not used by the EFI hand-over), booted through GRUB's command line. (a) auto -> L2 `Lat15-TerminusBold32x16.psf.gz`, 128x48; `setfont` of the 32x64 PSF2 font -> `KDFONTOP: Invalid argument`; 16x32 fits >= 80x24. (b) `gb.font=32x64` -> /init tries L3, it fails, L2 is used. 11.5 corrected (glyph height, not width). README-ISO font paragraph states the measured facts | gbtests.t10, 11.5, README-ISO |
| E4 | M | the AC gate never ran on hardware; "unknown" blocks modes 2/3 with no override | **Applied.** `util.power_verdict` (pure, selftest + builder_tests): AC also when no supply says offline, no battery discharges and a battery reports Charging/Full/Not charging; a discharging battery or an offline Mains/USB supply = battery (refused, no override); otherwise unknown -> `power.ack` (exactly `AC-OK`) after all checks passed. Every supply's type/online/status goes to installer.log at each check and to init.log at boot; tools `l` shows them. README tells the user to check tools `l` | util.py, sysfs.py, flowlib.py, tools.py, init, 4.0.15, 5.3 |
| E5 | M | 'wake'/'rstr' treated as hibernated with no evidence; signature not logged | **Applied.** Evidence: libntfs-3g.so.89 on the builder contains only the literals `hibr`/`HIBR` for its hibernation refusal (no `wake`). Decision: `wake`/`WAKE` (an image Windows already resumed from) is **not** hibernated; `hibr`/`HIBR` and, conservatively, `rstr`/`RSTR` (restore in progress) block. The R_HIBERNATED message names the signature and the remedy `powercfg /h off` (elevated) then Restart; the found signature is logged. builder_tests: HIBR/hibr/RSTR refuse, wake/WAKE/zeros pass; README "Preparing Windows" has the remedy | util.hiber_signature, flowlib.hibernation, 4.2 check 10, README-ISO |
| E6 | M | Surface keyboard untested; README called a USB keyboard optional | **Applied.** README/README.txt: have a wired USB keyboard ready; /init logs `/proc/bus/input/devices` N:/H: lines (never U:); the main screen shows `Keyboard devices: <n>` (handler `kbd`, buttons/switches excluded; names logged once and shown by tools `l`); first-boot step: tools `l`, then tools `w` | init, ui.py, flowlib.keyboards, util.py, README |
| E7 | M | -EspId, tools w and the gptbackup copy untested | **Applied (except -EspId).** `gb.stick_any=1` (2.3, VMware only, typed on GRUB's command line) lets a non-USB FAT/exFAT test disk stand in for the stick. **T9** in G4: two FAT32 test disks on the new persistent slots `sata0:3` (1 GiB, GPT copy) and `sata0:4` (2 GiB, logs); the reinstall answers `gptbackup.part` with the first (`@@GB GPTBACKUP result=ok`); afterwards that stick carries flag `gbbackup` (its `gpt_pre_install_primary.bin` is at depth 3) and tools `w` must not offer it — a stick holding GPT backups is never written again, by design (README: use a second stick for the logs); tools `w` writes `/gbsb3-logs/<build>-<time>/` to the second; host: both SHA256SUMS verify, the stick's `gbinstall/` == the state pulled over the serial shell, the off-disk copy == the pre-install GPT, `gbdisk restore-gpt --from <that copy> --tag pre_install` (dry run, --apply) on an image holding the post-install GPT gives exactly the pre-install areas; T4b's Windows import now runs `-From` the stick copy. **-EspId stays untested** (it needs a real disk, elevation and a `\\?\Volume` path, which the safety rules exclude): README-ISO recommends ISO option 3 for uninstalling and the stick import, and calls -EspId untested | sysfs.py, stick.py, gbtests (G4, t9_stick_checks), builder_media.sh (t9s, fatextract), vmtest (sata0:3) |
| E8 | L | T2 and T3n(e) had no EFI before/after comparison | **Applied.** Every T2 session snapshots the EFI variables before and after its refusal/cancel; T3n(e) compares after the cancelled run with the state right after the NVRAM preparation | gbtests t2, G1 |
| E9 | L | dd onto a larger device untested | **Applied.** **T7b**: two RDONLY FLAT extents (the ISO + a sparse zero file) = 32 GiB; the firmware boots it, `PAYLOAD kind=iso9660 dev=/dev/sda1`, mode 1: `payload_disk` refuse, `too_small` ok; `internal_not_blank` also refuses there because a VM disk is never USB (a real dd stick is USB) | gbtests.t7b_body |
| E10 | L | "hybrid GPT/MBR" overstates the ISO's table | **Applied.** It is a protective MBR + GPT ("isohybrid GPT"): 3 GPT partitions (ISO9660 Basic Data, the appended EF00 ESP, xorriso's Gap1). README-ISO DD section reworded | README-ISO, here |
| E11 | L | the firmware clock is labelled UTC | **Applied as documentation + test VMs.** README-ISO: times follow the firmware clock (local time on a Windows PC) despite the `Z`. The test VM now has `rtc.diffFromUTC = "0"`, so test logs are real UTC. The format is unchanged (the Windows tools read these files) | README-ISO, vmtest.make_vmx |
| E12 | L | FAT check whitelist; give the export ESP a root-directory label | **Rejected.** The ESP is the dual-boot export's `EFI-SYSTEM.img`, made by `build_internal.py` (not ours) and cross-checked byte for byte (1.5 check 5); its sha256 is also in the export manifest, `plan.json` and the Windows tools' verify path. Relabelling it would change all of them for a cosmetic fsck note. The whitelist accepts exactly that note and the iconv lines; any other finding still fails | — |
| E13 | L | evidence tidiness | **Applied.** Stale transcripts of the earlier partial G run moved to `logs\20261025-044111-ffa32946\prior-partial-run\`; T1c's disk is 32 GiB; content proofs cite the harness full-hash checks (verify_image.py only samples); `builder_media.sh clean` empties `/var/tmp/gbiso/test` of media at the end of each run, and the directory itself (harness scripts only) was removed after the final run; a full `run_all.py` run now moves the evidence of earlier partial runs of the same build to `prior-<UTC stamp>\` first (transcripts are appended to, so they would mix) | logs, report, run_all.archive_prior_runs |
| E14 | L | T8 passes `\\.\PhysicalDrive0` as a string | **Applied.** T8 asserts the session is not elevated before run_edge_tests and does not run it otherwise | gbtests.t8 |
| E15 | L | README gives no method to blank an internal disk | **Applied.** README-ISO option 1: use option 2 or a USB stick; otherwise diskpart `clean` from the Surface recovery drive, with a data-loss warning | README-ISO |

Harness conventions added (7.1): the VM may get a persistent slot `sata0:3` (test stick, T9) and extra vmx keys only
from `vmtest.EXTRA_KEYS` (`svga.*` for T10); sessions may boot through GRUB's command line (`c`, then `linux ...
${gb_args} ...`, `initrd ...`, `boot`) instead of a menu hotkey (T9, T10); `gptbackup.part` and `savelog.part` are
answered in T9 (elsewhere `gptbackup.part` is still answered `s` and fails the test). New result ids: T1d(n), T1d(y),
T2j, T4nb, T6g, T6m, T7b, T9, T10 (all required for an overall PASS, like the others; T1c stays optional).

## 13. Wave 2 integration decisions (v2.3: the dGPU stack and the k14 kernel)

Source: `C:\GB-build\native\dgpu\1b\WAVE2_PLAN_v2.md` (B-05..B-10). This section wins over every earlier one. The
ESP objects `esp-usb` and `esp-internal` keep their size (536,870,912 B) and their place in the payload; what they
hold changes: the k14 kernel, the `[NVIDIA test]` boot entry and the dGPU payload (INTERFACES 6.1, 16).

### 13.1 Installer module denylist (BUILD, TEST)

`build\builder\stage_initramfs.py` `MODULE_DENY` = `spi_intel*`, `mtd*`, `efi_test`, `efi_pstore`, `tpm*`,
`nouveau`, `nvidia*`, `drm_gpuvm`, `drm_exec`, `gpu_sched`, and since 13.9 also `drm_ttm_helper`, `mxm_wmi`,
`i2c_nvidia_gpu`, `nova_core`, `nova_drm`, `snd_hda_intel` (fnmatch, names with `-` read as `_`): the installer never
loads the NVIDIA stack or a helper of it. None of the new names is in the resolved set of the last build
(20261025-085013: 34 names -> 46 `.ko`; `installer\etc\modules.list` = storage, file systems, HID, Surface), so the
module set does not change. `build\builder\mk_initramfs.sh`'s own `.ko` gate names the same modules since 13.9. T0's
`DENY` (`test\t0_static.py`) is the same tuple; `C:\GB-x86\tests\w2b\test_iso_static.py` asserts the equality (both
read with `ast`). T0 records the list as `facts["module_deny"]`; its check "no denylisted .ko in the initrd" uses it.

### 13.2 verify_image preflight (BUILD)

Step 1 (6.2), right after the sidecar check: `python -I -B C:\GB-x86\verify_image.py <usb image>` (read-only; every
output line goes to the build log as a `[verify]` line). Anything but exit 0 with `RESULT: OK` is the problem
`verify_image.py reports problems on the USB image`: a build stops, a dry run lists it. The pre-Wave-2 image
(`cad193d6...`) fails it by design (no nvtest entry, no payload, backup files in the tree).

### 13.3 Export cross-check 8 (BUILD)

After checks 1-6 of 1.5, both ESPs (the USB image's EFI-SYSTEM partition and the export's `EFI-SYSTEM.img`) are read
with verify_image.py's FAT32 reader (imported by path; its module API). They must hold the same tree: the same file
set; `vmlinuz`, `gb/inject.gz`, `gb/bootconfig_extra`, `initrd-<slot>.img`, `gb/slot_<slot>.stamp` and every
`gb/dgpu-*` byte-identical; every `loader/entries/*.conf` (`gb-<slot>.conf`, `gb-<slot>-nvtest.conf`) with the same
lines except the title, and the same options tokens except `gb.boot=` and `androidboot.boot_part_uuid=`
(`verify_image.compare_export`, the rule of its check V17). Failure: `export cross-check 8 failed: the export ESP was
not built from the same tree as the USB image (<first difference>)`; for a reused export the 1.5 wrapper adds the
`--new-export` hint. `export_check.json` records `"8_esp_tree": "ok"`.

A reused export's ESP must also pass `verify_image.py --esp <export>\EFI-SYSTEM.img --boot internal --super-guid
<export super GUID>` (problem otherwise, with the `--new-export` hint). A fresh export is verified by
`build_internal.py` itself: it runs the same command after writing `manifest.json`, and on failure renames it
`manifest.json.unverified` and exits 1 (the build then stops with "build_internal.py failed").

### 13.4 Wave 2 refreshes the export

The Wave 2 USB image cannot pass check 8 against the k13 export, so the Wave 2 ISO is built with `--new-export`:
new dual-boot GUIDs (1.5: no other GUID set may be reused). An install made with an earlier ISO (`1718bcc1...` or
older) is removed with that ISO's option 3 or with `uninstall_dualboot.ps1 -Force`; the new ISO's option 3 does not
recognise it (other GUIDs). The previous ISO stays in `D:\GB-iso\prev\` until the user has decided
(WAVE2_PLAN_v2 section 13).

### 13.5 T3 criterion 6 additions (TEST)

`test\check_esp_tree.sh` also checks the installed ESP, with X = the reference (export) ESP's default slot from its
`loader/loader.conf`: `loader/entries/gb-X-nvtest.conf` contains `gb.nouveau=test` and
`androidboot.boot_part_uuid=<super>`; `gb-X.conf` exists and has no `gb.nouveau=`; there is no `*-gfxtest.conf`; no
entry contains `gb.gfxdriver=`; there is exactly one `gb/dgpu-*.erofs` with its `.sha256`, and that file is one line
`<sha256 of the image>  <image name>`. The script stays `bash -n` and shellcheck clean, LF only
(`C:\GB-x86\tests\w2b\test_check_esp_tree.sh` runs it on synthetic positive and negative ESPs on the builder).

### 13.6 USB image identity

A rebuilt USB image is passed to BUILD with `--usb-sha256 <hex>` until the integrator records its sha256 as
`build_iso.py` `VERIFIED_USB_SHA256` and `test\common.py` `USB_SHA` (WAVE2_PLAN_v2 R-11; `USB_SIZE`
28,814,885,376 is unchanged). T1a's exact-copy check compares against `USB_SHA`.

### 13.7 Measured sizes (filled in at WAVE2_PLAN_v2 R-13)

| item | before Wave 2 | after Wave 2 |
|---|---|---|
| ISO | 7,474,929,664 B (`1718bcc1...`, build 20261025-085013-37112350) | 7,471,792,128 B (`76fbb7ee...`, build 20261025-181336-456f4546; ceiling 8 GiB) |
| `esp-usb`, `esp-internal` objects (raw) | 536,870,912 B each | 536,870,912 B each |
| `esp-usb` / `esp-internal` chunk (zstd -9) | 118,013,841 / 118,013,834 B | 116,444,932 / 116,444,926 B |
| all payload chunks (zstd) | 7,437,701,240 B | 7,434,563,423 B |
| USB ESP used / free | 188.9 / 315.2 MiB | 160.5 / 343.6 MiB (export ESP the same); `native_min_free` 199.2 MiB |
| `initrd-<slot>.img` | 102,845,429 B | 57,833,745 B |
| `gb/dgpu-<krel>.erofs` | none | 60,260,352 B (sha256 `22080f8c...`) |
| USB image | `cad193d6...`, 28,814,885,376 B | `2d02f2b7...`, 28,814,885,376 B |

Measured on 2026-10-25 (WAVE2_PLAN_v2 R-13): both ESP objects extracted from the built ISO
(`gbsb3/parts/esp-*.000.zst`, `zstd -dcq --long=27`) are byte-identical to the USB image's EFI-SYSTEM partition and
to the export's `EFI-SYSTEM.img`; all four carry the identical native tree.

### 13.8 Review rebuild (2026-10-25, after the Wave 2 audits)

The safety review's medium finding: `gb_audiodiag.sh` staged its root-written files in shell-writable
`/data/local/tmp`. It now collects into `/data/vendor/gb/audiodiag/`, which must be a real directory owned by root
with mode 0700 (re-created otherwise, the `payload/` rule of INTERFACES 5.1), and it never copies a symlink to the ESP.
That changes one file of the native tree, so the USB image, the export (13.4 again: `--new-export`, new GUIDs) and the
ISO were rebuilt. The installer, GRUB, kernel and initramfs are unchanged.

| item | Wave 2 (`76fbb7ee...`) | review rebuild |
|---|---|---|
| ISO | 7,471,792,128 B, build 20261025-181336-456f4546 | 7,471,783,936 B (`e0067b12...`, build 20261025-205346-63e3bd38) |
| `esp-usb` / `esp-internal` raw sha256 | `9ee0ea47...` / `27f52603...` | `043a9a9c...` / `1ae31291...` (536,870,912 B each) |
| `esp-usb` / `esp-internal` chunk (zstd -9) | 116,444,932 / 116,444,926 B | 116,439,228 / 116,439,223 B |
| other payload objects | 11 | the same 11 (raw sha256 and size unchanged) |
| `initrd-<slot>.img` | 57,833,745 B | 57,833,741 B |
| USB image | `2d02f2b7...` | `db700318...`, 28,814,885,376 B |
| export | created 2026-10-25T20:14:33, new dual-boot GUIDs | created 2026-10-25T22:54:42, new dual-boot GUIDs |

Both ESP objects extracted from the built ISO have the sha256 of the USB image's EFI-SYSTEM partition and of the
export's `EFI-SYSTEM.img`; `verify_image.py` (with `--krel`) passes on both of those, and the USB and export native
trees are identical. ISO tests re-run and passed: T0 (64), T7 (8), T2e (8), T1a (10), T1b (38). Groups G1-G4
(T3-build, T3n(f), T3n(e), T3, T3n(d), T4, T4b, T9) need about 33 GB free on D: plus the 10 GB floor and wait for
disk space.

### 13.9 Wave 2b source fixes (2026-10-26; the outputs are rebuilt after this)

The low findings of the Wave 2 safety review, applied to the sources only (no output was rebuilt by this step):
* `MODULE_DENY` / T0's `DENY` (13.1) gain `drm_ttm_helper` (the fifth opt-in module of INTERFACES 6.2), `mxm_wmi`
  (nouveau's MXM helper), `i2c_nvidia_gpu` (the NVIDIA GPU's USB-C/I2C function driver), `nova_core`, `nova_drm` (the
  new NVIDIA drivers, absent from 6.8.0-147) and `snd_hda_intel` (its ID table binds the NVIDIA HDA function). The
  builder kernel's `modinfo -F depends nouveau` is `drm_display_helper, ttm, mxm-wmi, drm_gpuvm, i2c-algo-bit,
  gpu-sched, drm_exec, video, wmi, drm_ttm_helper`; the shared helpers (`ttm`, `drm_display_helper`, `i2c-algo-bit`,
  `video`, `wmi`) also serve i915 and stay allowed. None of the 46 resolved modules of build 20261025-205346-63e3bd38
  matches the new list (`C:\GB-x86\tests\w2b\test_iso_static.py` S1b), so the initramfs does not change. The second
  gate in `mk_initramfs.sh` (a `.ko` regex that still named only the five pre-Wave-2 patterns) now names the same
  16 entries (S1c), and the STAGE-JSON summary records `module_deny`, so a build log shows the list that ran.
* The native tree changes (ESP lock protocol INTERFACES 5.4 r3 in every lock user, gb_bootlog's lock fallback and
  free-space check, gb_slotsync's default-entry match, gb_audio.sh's legacy HDA load), so the USB image, the export and
  the ISO must be rebuilt (13.4 again) and the ISO suite re-run.

### 13.10 Wave 3 (WAVE3_PLAN; sources only, the outputs are rebuilt after it)

* Item 1: the native boot entries now keep `8250.nr_uarts=4` (`build_usb.native_cmdline`), so every export's
  `manifest.cmdline` and the export/USB entries change: a fresh export (`--new-export`) and a new ISO are required, and
  cross-check 8 compares them token for token as before. The installer itself needs no token (3.3): `remote.sh kernel`
  stops the build if `/boot/config-6.8.0-147-generic` has `CONFIG_SERIAL_8250_RUNTIME_UARTS=0`, the build record
  keeps `kernel_8250_runtime_uarts`, and T0 checks that the ISO's `/boot/vmlinuz` is `6.8.0-147-generic` (its
  setup-header version) and that this config value is >= 1 (`t0_static.kconfig_checks`, fact
  `installer_8250_runtime_uarts`; gbtests T0 compares it with the record when the record has it).
* Items 4, 8, 11, 12 and k15 change the native tree (native-only fstab with `fsverity`, the gb_bootlog summary,
  apgetty off, the gated `/gbbringup.sh`), so the USB image, the export and the ISO are rebuilt and the FULL suite is
  re-run (WAVE3 R-11). `verify_image.py` gains V25-V28 and the source comparison in V12; build_iso's preflight runs it
  as before.

### 13.11 Wave 3 rebuild (2026-10-26, WAVE3_PLAN R-05 .. R-11)

k15 was staged (R-05), the USB image rebuilt (R-07), and the ISO built with a fresh export (13.4 again:
`--new-export`, new GUIDs; a reused export fails cross-check 8 on the k15 payload names, as designed). The installer
itself needs no serial token: the record has `kernel_8250_runtime_uarts` 32, and T0 checks it (3.3, 13.10).

| item | review rebuild (`e0067b12...`) | Wave 3 |
|---|---|---|
| ISO | 7,471,783,936 B, build 20261025-205346-63e3bd38 | 7,471,886,336 B (`b1c355b0...`, build 20261026-062920-107b7e70) |
| `esp-usb` / `esp-internal` raw sha256 | `043a9a9c...` / `1ae31291...` | `5f87dbff...` / `41014cc2...` (536,870,912 B each) |
| `esp-usb` / `esp-internal` chunk (zstd -9) | 116,439,228 / 116,439,223 B | 116,490,289 / 116,490,283 B |
| other payload objects | 11 | the same 11 (raw sha256 and size unchanged; 27 of 29 chunks shared) |
| `initrd-<slot>.img` | 57,833,741 B | 57,845,993 B (k15 modules, `/gbbringup.sh`) |
| `gb/dgpu-<krel>.erofs` | 60,260,352 B (k14) | 60,260,352 B (k15, sha256 `dc9da255...`) |
| USB ESP used / free | 160.5 / 343.6 MiB | 160.6 / 343.5 MiB; `native_min_free` 199.2 MiB |
| USB image | `db700318...` | `377310ee...`, 28,814,885,376 B |
| export | created 2026-10-25T22:54:42, new dual-boot GUIDs | created 2026-10-26T08:30:11, new dual-boot GUIDs |
| installer initrd | 11,678,685 B | 11,678,562 B |

Both ESP objects extracted from the built ISO have the sha256 of the USB image's EFI-SYSTEM partition and of the
export's `EFI-SYSTEM.img`; `verify_image.py --krel` (k15) passes on the USB image (with `--compare-export`) and on
the export ESP, and the USB and export native trees are identical (`compare_native.py`). The builder's chunk cache
of the superseded image (`db7003187a168610-...`, 7.0 GiB) was removed before the build: the preflight needs 25 GiB
free on the builder's `/` and found 23.9 GiB; `prune-cache` would have removed it after the build anyway.

FULL suite (WAVE3 R-11, `run_all.py --with-t1c`): `OVERALL PASS` (2026-10-26 10:10-11:39, 89 min; every id: T0 67 checks, T1a 10, T1b 38, T1c 35, T1d(n) 41, T1d(y) 43, T2a-T2j, T3n(a/b/c), T4n, T4nb, G1-G4 with T3 110, T4 11, T4b 78, T9 30, T5, T6, T6m, T6n, T6g, T7, T2e, T7b, T10 17, T8 6; D: never below 15.3 GB free). The first full run failed only T8, on a stale
expectation: `run_image_tests.ps1` passed all 57 cases (the 25 of Wave 2 plus the 32 slot-aware `-UpdateEspOnly`
cases of WAVE3_PLAN 5.4), while gbtests T8 still required the text `25 passed`; T8 now requires `57 passed, 0 failed`
(7.x T8 text above). The rerun is the run of record.
