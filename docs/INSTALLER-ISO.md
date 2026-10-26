# Installer ISO

`iso/build/build_iso.py` builds one bootable file, `googlebook-sb3-installer.iso` (about 7.5 GB). It installs Googlebook
OS (Google's Android 17 "lapis" desktop image) on the Microsoft Surface Book 3. You can put it on a Rufus stick (ISO
mode, recommended, or DD mode) or a Ventoy stick, or attach it to a VMware VM as a CD.

> **Personal use only.** The ISO contains Google's proprietary disk image: the partitions of the USB image that
> `build_usb.py` makes from **your own** `lapis.bin`. Build it yourself, keep it on your own PC and sticks, and never
> share, upload or publish it. This repository contains only the build scripts, the installer and its tests.

The ISO boots a small Linux installer: GRUB, an Ubuntu kernel (6.8.0-147-generic), busybox and Python. The installer
offers four options:

1. **Write Googlebook to a whole disk**, usually a USB stick. The result is the verified USB image, byte for byte.
2. **Install next to Windows** into unallocated space that you created in Windows beforehand. This uses the same
   partition layout as `native/dualboot/install_dualboot.ps1`, plus a UEFI boot entry "Googlebook OS" placed **after**
   Windows Boot Manager.
3. **Uninstall** what option 2 added.
4. **Tools**: shell, logs, payload check, compare, save logs, rescan, reboot.

Every destructive step asks you to type a confirmation. The installer refuses disks it must not touch (see
[The installer menu](#the-installer-menu)).

**Status.** Tested in VMware only, never on the real Surface Book 3. Rufus and Ventoy themselves were never run;
equivalent stick layouts were tested in VMware. The last ISO built and tested is the wave 3 build (2026-10-26):
kernel build #15, the native tree with the wave 3 fixes (the keyboard base's `8250.nr_uarts=4` among them, see
[Boot entries](#boot-entries-and-the-nvidia-payload)) and a fresh dual-boot export with new GUIDs. The full test suite
(`iso/test/run_all.py`, all 38 test ids: static, whole disk, dual boot install and uninstall, media and the Windows
side) passed on it, so all four options were exercised in VMware. Run the suite again on every ISO you build before
you use options 2 and 3 on a real disk.

A dual-boot install made with an older ISO (built from another export, with other GUIDs) is removed with that older
ISO or with `uninstall_dualboot.ps1 -Force`, not with a newer one. ISOs built before wave 3 also lack the
`8250.nr_uarts=4` entry option: see [FLASHING.md](FLASHING.md#images-built-before-wave-3-no-keyboard-base).

The full design (payload format, discovery, every refusal, the test plan and the review history) is
[iso/DESIGN.md](../iso/DESIGN.md). The working README of the development setup is
[iso/README-ISO.md](../iso/README-ISO.md).

## Building the ISO

You need:

* a USB image built and verified as in [NATIVE.md](NATIVE.md) and [FLASHING.md](FLASHING.md): `out\googlebook-sb3-usb.img`
  with its `.sha256` sidecar. `verify_image.py` must report `RESULT: OK` on it.
* the Linux builder ([builder/README.md](../builder/README.md)). The ISO's initramfs, GRUB image, payload chunks
  (zstd) and the ISO itself are made there, below `/var/tmp/gbiso/`.
* about 15.5 GiB free on the output drive during a build, plus about 15 GiB on the workspace drive for a while when a
  fresh dual-boot export is made. The builder keeps a chunk cache of about 7 GiB.

```powershell
python -I iso\build\build_iso.py --dry-run     # read-only checks and plan
python -I iso\build\build_iso.py               # full build
```

* **Inputs:**
  * the verified USB image, checked against its sidecar and the value recorded as `VERIFIED_USB_SHA256` in
    `build_iso.py`. Pass `--usb-sha256 <hex>` for a new image until you record it.
  * the dual-boot export `iso\work\export\`, made once by `native/dualboot/build_internal.py` and then reused, so
    that rebuilt ISOs keep the same dual-boot GUIDs. Its ESP must hold the same native tree as the USB image's
    (export cross-check 8). A new kernel or driver tree therefore needs `--new-export`.
  * the installer tree `iso\installer\`.
* **Outputs:** `googlebook-sb3-installer.iso` (set read-only), `.iso.sha256`, the payload manifest `.manifest.json`, the
  build record `.build.json` and `build-<build_id>.log`.
* **Options:**
  * `--new-export` makes a fresh export with **new** dual-boot GUIDs. An ISO built that way cannot uninstall an
    install made from an older ISO; use the older ISO or `uninstall_dualboot.ps1 -Force` for that.
  * `--no-payload-cache` recompresses everything. `--keep-nx-compat` leaves the kernel's NX flag set.
    `--keep-work` keeps the builder's work directory.
  * `--test-tiny-payload` is a pipeline test only. Its ISO cannot install Googlebook.

**Fixed paths.** `iso/` is copied unchanged from the development workspace, and its defaults name that layout:

* `C:\GB-x86` is the workspace (the repository root here): `out\`, `verify_image.py`, `iso\work\export\`;
* `C:\GB-build\native\dualboot` holds the dual-boot tools;
* `D:\GB-iso\` holds the outputs, with `D:\GB-iso\test\` as the test area;
* the builder is `builder@192.168.115.132`, reached with the key `C:\GB-build\builder_key`.

The test harness's safety guards are keyed on these paths. It starts only a VM below `D:\GB-iso\test\`, writes only
there, and refuses `lapis.bin`, the USB image and any physical drive. Keep that layout, or change the constants in
`iso/build/build_iso.py` and `iso/test/common.py` together and run `iso/test/test_harness.py` before anything else.

## Testing

`iso/test/run_all.py` boots the ISO in one throwaway VMware VM (UEFI, 2 GB, 2 vCPUs, no USB or network) and drives the
installer over a serial console. The VM's disks are sparse files that the harness creates (`mkdisks.py`, and
`builder_media.sh` for NTFS/exFAT/Ventoy-like media). Afterwards it checks every written disk against the payload
(`validate_wholedisk.py`, `validate_dualboot.py`).

```powershell
python -I iso\test\run_all.py --preflight      # build record, ISO, manifest, tools
python -I iso\test\run_all.py --list           # the test ids
python -I iso\test\run_all.py --only T1a,T7    # some tests; no argument = all
```

| Group | Tests |
|---|---|
| static | T0: payload, manifest, GRUB config, initramfs contents (no denylisted module such as nouveau), installer source scan for forbidden tools |
| whole disk | T1a (bit-exact copy), T1b (onto a larger disk, userdata grown), T1d (used internal-like disk), T2* refusals (Windows disk, BitLocker, recovery media, Ventoy, protected sticks, duplicate GUIDs, ...) |
| dual boot | T3-build, T3 (install next to a host-shaped Windows disk), T3n(*) refusals (hibernated Windows, no region, BitLocker), T4/T4b (uninstall from the ISO and from Windows), T9 (state import, GPT restore) |
| media | T5 (Rufus ISO-mode equivalent), T6* (Ventoy equivalents: the ISO file on exFAT/NTFS, GRUB loop-mount, device-mapper map), T7/T7b/T2e (raw ISO as a CD and as a DD-written disk), T10 (console fonts) |
| Windows side | T8: the dual-boot PowerShell scripts against image files |

Logs and a `report.json` per build go to `iso\test\logs\<build_id>\`. Unit tests of the harness itself:
`python -I iso\test\test_harness.py`. Installer unit tests: `iso/installer/tests/` (`test_host.py` on Windows,
`builder_tests.py` on the builder).

## Putting the ISO on a stick

* **Rufus, ISO mode (recommended).** Use a stick of 16 GB or more; everything on it is erased. Select the ISO, set
  **GPT** and **UEFI (non CSM)**, choose **Write in ISO Image mode**, and decline any offer to download GRUB. The
  stick then holds the ISO's files. GRUB finds them through the marker file `\gbsb3\GBSB3-<build>.marker`, so the
  `gbsb3` folder must stay on the stick unchanged.
* **Rufus DD mode, or `dd`.** The stick gets the ISO's own partition table (protective MBR plus GPT: the ISO9660
  volume, a small EFI partition and xorriso's padding partition). Windows then offers to format the partitions it
  cannot read. Click **Cancel** every time.
* **Ventoy (not tested with real Ventoy).** The data partition must be exFAT or NTFS, with free space of at least the
  ISO size. Copy the `.iso` anywhere on it. The installer finds the file by itself, up to 6 folder levels deep, on
  exFAT, FAT and NTFS. If Ventoy's normal mode does not reach the installer menu, use its GRUB2 mode, never memdisk.
  While the installer runs, the Ventoy stick is protected and never written; save logs to another stick.
* **VMware (testing).** Attach the ISO as a CD to a VM with EFI firmware and Secure Boot off. Only under VMware
  (SMBIOS vendor "VMware, Inc.") does GRUB also show three `TEST:` entries that put the installer on the serial port.

## Booting the Surface Book 3 from the stick

1. Surface UEFI (hold **Volume Up**, press **Power**): Security → Secure Boot **Disabled** (the ISO's GRUB and kernel
   are unsigned); Boot configuration → Advanced options → **Enable boot from USB devices**.
2. **Dock the clipboard on the base and leave it docked.** All USB ports are on the base. **Never press the detach
   key while the installer runs.**
3. Plug the stick into the base, and keep a **wired USB keyboard** ready. The installer loads the Surface keyboard
   drivers, but they were never tested on the real machine. The main screen shows `Keyboard devices: <n>`.
4. Connect the **charger**. Options 2 and 3, and option 1 on an internal disk, refuse to run on battery. If the
   firmware reports nothing about the charger, the installer asks you to type `AC-OK`.
5. Shut down, hold **Volume Down**, press **Power**. GRUB waits 10 s: `Googlebook SB3 installer` (key `n`), a basic
   display variant (`b`: nomodeset, kernel font) if the screen stays black, then firmware setup, reboot and power off.
6. On the first boot, open tools `l` before choosing 1, 2 or 3, and check the keyboard and power lines.

The console uses the 16x32 Terminus font. Kernel 6.8's framebuffer console cannot use taller glyphs. On the 3000x2000
panel that gives about 187x62 characters (not seen on the real panel yet).

## The installer menu

The installer looks for its payload for up to 30 s, then lists every disk with its number, kernel name, size and
model.

### 1 — Write Googlebook to a whole disk

* It writes the verified USB image (26.84 GiB) to the chosen disk, which must be at least 28,814,885,376 bytes (a
  32 GB stick). On a disk at least 1 GiB larger, it offers to grow `userdata`. Every partition is read back and
  checked against the payload hashes. No UEFI entry is created: sticks boot through the removable-media path.
* You must type exactly **`ERASE <name>`**, for example `ERASE sda`.
* It **refuses**:
  * a disk with Windows (MSR, recovery, dynamic or Storage Spaces partitions, NTFS, a Windows boot manager);
  * BitLocker volumes (also BitLocker To Go), Windows installation or recovery media;
  * a stick with a `GB-backup` folder or GPT backups, a Ventoy stick, the installer's own stick;
  * a disk it could not fully scan;
  * an internal disk that is not blank (first 2048 and last 2081 sectors zero). Use option 2 for the Surface's own
    disk;
  * a disk that is too small, read-only, in use or not 512-byte-sector;
  * while another plugged-in disk carries the image's GUIDs or cannot be read.

### 2 — Install next to Windows (dual boot)

* It writes its own `EFI-SYSTEM` partition, the A/B boot partitions, misc, metadata, frp_persist, the
  desktop_security partitions, `super` (12 GiB) and `userdata` (the rest, at least 8 GiB). All of them go into the
  unallocated space after D:, keeping 1 GiB free before the Windows Recovery partition.
* C:, D:, the Windows ESP, MSR and Recovery are never written, and existing partition-table entries stay
  byte-identical. Data is written and verified first; the partition table is written last.
* It creates the UEFI entry **"Googlebook OS"** after Windows Boot Manager, so Windows stays the default.
* You may save the current partition table to a FAT/exFAT stick first (prompt `gptbackup`). Then type exactly
  **`INSTALL <name>`**.
* It **refuses** if:
  * BitLocker is on, or Windows is hibernated or was shut down with Fast Startup;
  * Windows Boot Manager is not in the boot order;
  * the disk is dynamic or uses Storage Spaces;
  * there is less than 22,358 MiB of unallocated space;
  * Googlebook partitions or an entry already exist;
  * the partition table is damaged or unusual;
  * it runs on battery.
* At the end it shows the **ESP GUID** and the import command for Windows. Write them down.

### 3 — Uninstall what option 2 added

* It finds the dual-boot partitions of **this ISO's export** by their GUIDs. It first removes the "Googlebook OS"
  entry, then clears the 18 partition-table entries. No partition data and no other entry is touched.
* Type **`REMOVE <name>`** (or **`REMOVE efi`** for a leftover boot entry only). Afterwards, extend D: again in Disk
  Management.
* It refuses on a hibernated Windows, on battery, on a damaged table, or when foreign partitions lie between the
  Googlebook ones.
* Installs made by `install_dualboot.ps1`, or by an ISO built from another export, are removed on Windows with
  `uninstall_dualboot.ps1` (or with the ISO that made them).

### 4 — Tools

`s` shell, `l` power state, keyboards and logs, `v` verify the whole payload, `c` compare an installed disk's
partition table with the copy saved at install time, `w` save logs (and the install state `gbinstall`) to a FAT/exFAT
stick, `r` search for the payload again, `b` back, `reboot`, `poweroff`.

**Interrupted runs.** If a write fails or the installer crashes, the next menu shows a journal with the last phase
reached and what it means. Until you type **`ACK <name>`**, every further action on that disk is refused. During a
write, Ctrl+C, Ctrl+\ and Ctrl+Z are ignored and Ctrl+Alt+Del is refused.

## Preparing Windows for dual boot (option 2)

1. BitLocker / Device Encryption **off** for C: and D:. Wait until decryption has finished.
2. Fast Startup **off** (Control Panel → Power Options), or always reach the installer with **Restart**. If the
   installer keeps refusing with `R_HIBERNATED`, run an elevated `powercfg /h off`.
3. Windows Boot Manager must be in the firmware boot order. If it is not: `bcdboot C:\Windows /f UEFI`.
4. Make the Surface USB recovery drive on a separate stick, and keep it unplugged during the install.
5. Take an off-PC GPT backup (elevated, read-only on the disk), then copy that folder to a USB stick:
   `native\dualboot\gbdisk.exe backup --disk 0 --tag pre_iso --out C:\GB-backup\pre-iso-<yyyyMMdd>`. `gbdisk.exe` is
   built from `gbdisk.cs` by `build_gbdisk.ps1`.
6. Shrink D: in Disk Management by at least 22,400 MB (40,000 MB or more recommended), and leave the space
   **unallocated**.
7. Restart into the installer stick, on AC power, and choose **2**.

## After a dual-boot install

* Windows stays the default. To boot Googlebook once, run an elevated `native\dualboot\Restart-ToGooglebook.ps1`, or
  pick "Googlebook OS" in Surface UEFI's boot configuration (whether the Surface lists it is not verified).
* To manage the install from Windows, import its state first:
  * from a stick written with tools `w` (tested in VMware):
    `Import-GooglebookIsoInstall.ps1 -From <stick>:\gbsb3-logs\<build>-<time>\gbinstall` (dry run, then `-Apply`);
  * or `-EspId <ESP GUID>` (reads the Googlebook ESP directly; untested).
* After the first Googlebook boot, confirm that Windows' partitions are unchanged: Android rewrites the GPT when it
  updates its A/B slot bits. Use installer tools `c`, or `gbdisk layout --disk 0` plus
  `gbdisk verify --plan C:\GB-backup\<timestamp>-iso\plan.json --layout-only`.

## Boot entries and the NVIDIA payload

The Googlebook ESP of options 1 and 2 has two systemd-boot entries for the system slot: the default `... (slot X)` and
`... (slot X) [NVIDIA test]` (adds `gb.nouveau=test`; see [NVIDIA.md](NVIDIA.md)). It also holds
`gb\dgpu-<kernel release>.erofs` and its `.sha256` (the NVIDIA firmware, modules and Vulkan driver, about 60 MB).
Leave both files in place. The installer's own initramfs never loads nouveau or any NVIDIA module (a denylist that
T0 checks).

Both entries carry `8250.nr_uarts=4`. Our Googlebook kernel is built with `CONFIG_SERIAL_8250_RUNTIME_UARTS=0`, and
without that token the serial UART that links the keyboard base (keyboard, touchpad, batteries, charger, detach) to
the Surface Aggregator never registers. Credit: gbOS86 found this on the Surface Laptop 7
([github.com/linuxkid473/gbos-x86](https://github.com/linuxkid473/gbos-x86), HANDOFF.md). The installer's Ubuntu
kernel ships `RUNTIME_UARTS=32`, so it needs no token. The ISO build stops if that ever changes, and T0 checks it
again.

## Recovery

* **Windows does not start, or Googlebook starts instead:** see the Recovery table in
  [native/dualboot/README_DUALBOOT.md](../native/dualboot/README_DUALBOOT.md) (boot order, `bcdboot`, restoring a GPT
  from the `C:\GB-backup` copies with `gbdisk restore-gpt`, the Linux `dd` method).
* **An interrupted run:**

  | journal phase | meaning |
  |---|---|
  | `planned` | nothing was written |
  | whole disk, `head_zeroed` … `gpt_written` | the disk was partly overwritten and has no valid table: run option 1 again or discard it |
  | dual boot, `data_written` / `data_verified` | only unallocated space was written; the table is unchanged |
  | dual boot, `gpt_backup_written` | the two GPT copies differ: restore both from a pre-install copy with `gbdisk restore-gpt --disk 0 --from <dir> --tag <tag>` (dry run, then `--apply`) |
  | dual boot, `gpt_primary_written` … `efi_ordered` | the partitions exist but the entry or state is missing: option 3 removes everything |
  | uninstall, `efi_removed` | the entry is gone but the partitions remain: run option 3 again |
  | uninstall, `gpt_backup_written` | restore from the `pre_uninstall` copy |

* `F_KERNEL_RESCAN`: the table on disk is correct but the kernel did not pick it up. Reboot the installer.

## Logs

* While it runs, the installer logs to RAM (`/run/gb/log/init.log`, `installer.log`). Tools `l` shows them, and tools
  `w` copies them to `\gbsb3-logs\<build>-<time>\` on a FAT/exFAT stick of your choice. It never writes the installer
  stick, a Ventoy stick, a GB-backup stick (including the stick that received the installer's off-disk GPT copy),
  Windows media or a BitLocker volume.
* After a dual-boot install, the log is also in `\gbinstall\install.log` on the new Googlebook ESP.
* Times come from the firmware clock and carry a `Z`. Windows usually keeps that clock in local time.
* No log contains disk serial numbers, user names or volume labels.

## Known limits

* Not tested on the real Surface Book 3: the Surface keyboard stack in the installer, the console font on the real
  panel, whether `surface_charger`/`surface_battery` report the charger, whether Surface UEFI shows "Googlebook OS",
  and `Import-GooglebookIsoInstall.ps1 -EspId`.
* Ventoy and Rufus themselves were not run. If one way does not work, try another (Rufus ISO mode, Ventoy GRUB2 mode,
  Rufus DD mode).
* In VMware every disk is non-USB, so option 1 was tested only on internal-disk-like targets.
* Only the Surface base keyboard, generic USB keyboards and Microsoft or Logitech receivers are supported in the
  installer.
* Every stick written with option 1 has the same disk and partition GUIDs. Never connect two of them at once while
  Googlebook or the installer runs.
