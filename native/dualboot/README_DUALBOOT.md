# Googlebook OS + Windows dual boot on the Surface Book 3 (Boot Camp style)

This puts Googlebook OS ("lapis", Android 17 desktop) on the internal NVMe next to Windows 11. Windows stays the
default. You boot Googlebook on purpose, one boot at a time, the way Boot Camp's "Restart in macOS" works.

* **Space.** D: is shrunk. The Googlebook partitions go into the freed space between D: and the Windows
  Recovery partition. C:, the Windows ESP, MSR and Recovery are never moved, resized or written.
* **Its own ESP.** Googlebook gets a separate EFI system partition, `EFI-SYSTEM` (systemd-boot, our GKI kernel,
  initrds). It also gets its own UEFI boot entry, "Googlebook OS". The Windows ESP and `\EFI\Microsoft` are left alone.
* **One-time boot.** `Restart-ToGooglebook.ps1` sets UEFI `BootNext` and restarts. Any later boot, or a power
  cycle, goes back to Windows Boot Manager.

Everything that changes something is a **dry run unless `-Apply`** is given. Every step was first run against
image files (see [Test harness](#test-harness)).

---------------------------------------------------------------------------------------------------------------

## Files

| file | what it is |
|---|---|
| `build_internal.py` | Exports the partition set for the internal install (`<out>\*.img` + `manifest.json`). Reads the workspace's `lapis.bin` (`GB_HOME`, default: the repository root) and does not write it. Imports `build_esp.py`/`build_usb.py` without changing them. |
| `install_dualboot.ps1` | The installer: checks, backups, D: shrink, partitions, raw writes, boot entry. It also has two maintenance modes: `-UpdateEspOnly` and `-RepairBootEntry`. |
| `uninstall_dualboot.ps1` | Removes the boot entry and the Googlebook partitions, then extends D: back to its original size. |
| `Import-GooglebookIsoInstall.ps1` | Imports the state of an install made by the installer ISO (`\gbinstall\` on its ESP) into `C:\GB-backup`, so the scripts below work for it. See [Installed from the ISO](#installed-from-the-iso). |
| `Restart-ToGooglebook.ps1` | One-time boot into Googlebook: `bcdedit /set {fwbootmgr} bootsequence {id}` + `shutdown /r /t 0`. |
| `gbdisk.cs` / `gbdisk.exe` | C# helper (.NET 4, built by `build_gbdisk.ps1` with the in-box `csc.exe`). Handles the GPT through `IOCTL_DISK_GET/SET_DRIVE_LAYOUT_EX`, raw I/O, volume lock/dismount, and the image backend. |
| `gb_common.ps1` | Shared PowerShell helpers. Every change goes through `Invoke-Change`, which only acts with `-Apply`. |
| `tools/gpt_validate.py` | Test tool, independent of gbdisk. It builds synthetic host-shaped disk images, takes snapshots, and does full GPT validation. |
| `test/run_image_tests.ps1` | Install + uninstall cycle plus negative tests, run on an image file. Logs go to `test/logs/`. |

## What gets created on disk 0

The partitions go after D:, 1 MiB aligned, in this order. 1 GiB is left unallocated before Recovery (`-ReserveMiB`)
so a later WinRE enlargement can recreate Recovery in place.

| GPT name | size | type GUID | content |
|---|---|---|---|
| `EFI-SYSTEM` | 512 MiB | c12a7328-... (ESP) | FAT32 holding systemd-boot `\EFI\BOOT\BOOTX64.EFI`, `vmlinuz`, `initrd-<slot>.img`, the `gb-<slot>` entry (`gb.hw=native gb.boot=internal`). Boot logs go to `\gblog\`. |
| `boot_a/b`, `init_boot_a/b`, `vendor_boot_a/b`, `pvmfw_a/b` | 64/8/32/4 MiB | 0fc63daf-... (Linux data) | copied from lapis.bin |
| `vbmeta_a/b` | 4 MiB | 88434509-d9d1-487d-b82c-15ef964cbd4b (as lapis) | copied. **GPT attributes copied too:** the boot_control HAL keeps the A/B slot state in bits 48-51 (priority), 52-55 (tries) and 56 (successful) |
| `misc`, `metadata`, `frp_persist`, `desktop_security_persist`, `desktop_security_storage` | 4/64/2/16/32 MiB | Linux data | zeroed. The first boot formats metadata. |
| `super` | 12 GiB | Linux data | copied (system/vendor/product/... dynamic partitions) |
| `userdata` | the rest of the shrink (minimum 8 GiB) | Linux data | the first and last 64 MiB are zeroed, which removes old D: data. The first boot formats it (`formattable`). Use `-ZeroFullUserdata` to zero all of it. |

Android finds partitions by **GPT name** (`/dev/block/by-name/<name>`), so the names are exact.

Every partition gets a fresh GUID in `build_internal.py`, and the installer creates the partitions with exactly
those GUIDs. The ESP's boot entry passes `androidboot.boot_part_uuid=<super GUID>`, so only the disk that holds this
super gets by-name links. This is the same strict selection the USB stick uses, so an inserted Googlebook stick and
the internal install never mix. `build_internal.py --boot-devices` uses the PCI path
`androidboot.boot_devices=pci0000:00/0000:00:1d.0` instead.

None of these types is "basic data", so Windows assigns no drive letters. The GPT "no drive letter" bit (63) only
applies to basic data partitions, so it is not set. Linux tools read bits 60-63 as read-only/hidden/no-auto, so no
new partition carries them. The only attribute bits written are the vbmeta slot bits.

## Before you start (once)

1. **Back up** C: and D: user data to an external disk or the cloud.
2. Create a **Surface USB recovery drive** on a separate stick. Microsoft: "Creating and using a USB recovery drive for
   Surface". It needs the serial number.
3. **BitLocker / Device Encryption off** on C: and D:. The installer checks it.
4. **Secure Boot off**: Surface UEFI → Security → Secure Boot → *None/Disabled*. Our systemd-boot and kernel are
   not signed. The red bar at the Surface boot screen is normal with Secure Boot off.
5. Stay **on AC power** for the whole install. The installer refuses to run on battery.
5a. **Fast Startup off** (Control Panel → Power Options → *Choose what the power buttons do* → untick *Turn on fast
   startup*). With it on, "Shut down" hibernates the Windows kernel with the disk layout cached, and Googlebook
   rewrites the GPT (vbmeta slot bits) and its own ESP while Windows sleeps. A restart is never a hybrid shutdown,
   so `Restart-ToGooglebook.ps1` is safe either way; the installer warns while Fast Startup is on.
5b. **Rehearse on a VHDX first (recommended).** The live-disk paths (IOCTL_DISK_SET_DRIVE_LAYOUT_EX through
   partmgr, volume lock/dismount, raw writes) have only run against image files. In an elevated prompt, create and
   attach a ~40 GB dynamic VHDX in Disk Management, initialise it GPT, make an NTFS volume (e.g. `V:`, 30 GB),
   then run the real code path against that disk with its own backup root and no boot entry:
   `.\install_dualboot.ps1 -DiskNumber <vhd disk#> -ShrinkDriveLetter V -ShrinkGB 22 -BackupRoot C:\GB-backup-vhd -ExportDir C:\gb-export -SkipBootEntry -Apply`,
   then `gbdisk verify --disk <vhd disk#> --plan C:\GB-backup-vhd\<ts>\plan.json --source-dir C:\gb-export`, then
   `.\uninstall_dualboot.ps1 -DiskNumber <vhd disk#> -ExtendDriveLetter V -BackupRoot C:\GB-backup-vhd -KeepBootEntry -Apply`.
   Detach and delete the VHDX afterwards.
6. Build the kernel and the native tree (`<workspace>\inject\native`) first. The export's ESP holds the kernel
   and initrd that are current at export time. Later kernels can be pushed with `-UpdateEspOnly` (see below).

## Install

```powershell
# 1. Export (no admin), from the repository root. Takes ~5 min plus the 12 GiB copy. Use -B: no __pycache__
#    next to the images.
python -I -B native\dualboot\build_internal.py --out C:\gb-export
#    NOTE: keep the export (~12.6 GiB) off D:, because it would use free space that the D: shrink needs
#    (the script's default --out is D:\gb-export, so pass --out). Use C: or an external disk.

# 2. Dry run (any PowerShell. Elevated shows the shrink limit, BitLocker and firmware entries too)
cd native\dualboot
.\install_dualboot.ps1 -ExportDir C:\gb-export -ShrinkGB 40

# 3. Install (elevated PowerShell: Win+X → Terminal (Admin))
Set-ExecutionPolicy -Scope Process Bypass
.\install_dualboot.ps1 -ExportDir C:\gb-export -ShrinkGB 40 -Apply
```

`-ShrinkGB` is in GiB. The value must leave D: above its shrink minimum: `Get-PartitionSupportedSize -DriveLetter D`.
If the minimum is too high, run `defrag D: /X` first. With 40 GiB, userdata gets 26,795 MiB (about 26.2 GiB). That
is the result of a real-disk dry run: 0.9 GiB goes to the ESP and the small partitions, 12 GiB to super, and
1 GiB is kept free before Recovery.

The shrink can never exceed D:'s free space, so keep the export off D:. Below about 22 GiB of shrink, userdata
would fall under its 8 GiB minimum, and the installer refuses.

What `-Apply` does, in order (the dry run prints the exact commands):

1. **Checks.** The script must be elevated. The firmware must be UEFI and disk 0 must be GPT with 512-byte
   sectors. The disk GUID from Get-Disk must match the IOCTL value. BitLocker must be off on D: and C:.
   `Get-PartitionSupportedSize` must allow the shrink. Secure Boot must be known and off. The laptop must be on AC.
   There must be no Googlebook partitions and no "Googlebook OS" entry yet, and the export images must match the
   manifest (sha256).
2. **Backups**, into `C:\GB-backup\<timestamp>\`:
   - `bcd.bak` (`bcdedit /export`)
   - `fw_entries.txt` (`bcdedit /enum firmware /v`)
   - `bcd_all.txt`
   - `storage.json` (Get-Disk/Get-Partition/Get-Volume)
   - `gpt_pre_shrink_primary.bin` (LBA 0-33), `gpt_pre_shrink_backup.bin` (last 33 LBAs), and
     `layout_pre_shrink.json` (names, GUIDs, attributes)
   - `manifest_installed.json`

   A second copy of the GPT backups goes to `<export>\gpt-backups\<timestamp>\`. **Copy the folder to a USB stick
   as well.**
3. **Shrink D:** with `Resize-Partition`, then check the result. The GPT is backed up again (`post_shrink`).
4. **Create partitions** with one `IOCTL_DISK_SET_DRIVE_LAYOUT_EX`. The five Windows entries are passed back
   verbatim, and the new entries carry exact names, types, GUIDs and attributes. gbdisk refuses a layout in which
   any existing entry would change, any partitions overlap, or anything lies outside the usable area or the freed
   region. Afterwards it re-reads the layout and verifies every entry.
5. **Write** each image raw through `\\.\PhysicalDrive0`. Each partition is first checked again against a fresh
   layout read, any volume on it is locked and dismounted, and every write is bounds-checked to that partition.
   After writing, the sha256 of the source is compared against the read-back and the manifest. Zero ranges are read
   back too.
6. **Boot entry**, in this order:
   1. `bcdedit /copy {bootmgr} /d "Googlebook OS"`
   2. `bcdedit /set {id} device partition=\Device\HarddiskVolumeN` (falls back to a temporary drive letter)
   3. `bcdedit /set {id} path \EFI\BOOT\BOOTX64.EFI`
   4. `bcdedit /set {fwbootmgr} displayorder {id} /addlast`
   5. A check that the first boot-order entry (Windows Boot Manager) is unchanged

   If any of these steps fails, the new entry is deleted again. Before any of them runs, the current firmware boot
   order must be readable (`bcdedit /v` prints `{fwbootmgr}`/`{bootmgr}` as their GUIDs; gb_common maps them back),
   and every `/set` is given an explicit, non-well-known GUID: `bcdedit /set` without an id would edit `{current}`,
   the running Windows loader. systemd-boot ignores the Windows-style load options that a copied `{bootmgr}` entry
   carries (checked in its source: it only sets LoadOptions for the images it starts).

The state of the run (phase, GUIDs, original D: size, plan, boot entry id) goes to
`C:\GB-backup\<timestamp>\install_state.json`. The pointer to it is `C:\GB-backup\googlebook_install.json`.

## Booting Googlebook and returning to Windows

* **From Windows:** in an elevated PowerShell, run `.\Restart-ToGooglebook.ps1`. Use `-NoRestart` to only set it, or
  `-WhatIf` to preview. This sets UEFI BootNext for one boot. systemd-boot shows its menu for 4 s and starts
  `Googlebook OS - Surface Book 3 (slot B)`.
* **From the firmware:** shut down, then hold **Volume Up** and press **Power** to open Surface UEFI. Go to **Boot
  configuration**. Swiping left on an entry boots it once without changing the order. The page lists Windows Boot
  Manager, USB Storage, PXE and Internal Storage. Whether it also lists "Googlebook OS" is **not verified** on this
  firmware (23.101.140). If it does not, use `Restart-ToGooglebook.ps1`.
* **USB stick:** holding **Volume Down** and pressing **Power** boots the USB stick. This is the stick image, not
  the internal install. Surface UEFI → Boot configuration → Advanced options must have "Enable boot from USB
  devices" on.
* **Back to Windows:** simply restart or shut down from Googlebook. Windows Boot Manager is first in BootOrder and
  BootNext is consumed by the one boot. Nothing else is needed.
* **Optional Android-side "restart into Windows" tool (described, not implemented).** It is only useful if
  Googlebook were ever made the default. From a root shell (SELinux is permissive):
  1. `mount -t efivarfs none /sys/firmware/efi/efivars`. This needs `CONFIG_EFIVAR_FS`, which is **not set** in
     the GKI x86 defconfig or the lapis config (checked). Our build would need `CONFIG_EFIVAR_FS=m`.
  2. Read `BootOrder-8be4df61-93ca-11d2-aa0d-00e098032b8c` and the `Boot####-8be4df61-...` variables. Find the one
     whose description (UTF-16 after the 4-byte attributes, 4-byte Attributes field and 2-byte FilePathListLength)
     is "Windows Boot Manager".
  3. Write `BootNext-8be4df61-...` as the 4-byte attributes `07 00 00 00` (NV|BS|RT) plus the little-endian UINT16
     boot number, e.g. `printf '\x07\x00\x00\x00\x00\x00' > .../BootNext-...` for Boot0000. Run `chattr -i` on the
     file first if efivarfs marks it immutable.
  4. `reboot`. Keep `efi_pstore.pstore_disable=1`, which is already on the native cmdline, so that panics never go
     to NVRAM.

## First boot, logs, checks

* The first boot formats `metadata` and `userdata`, and dexopt runs, so it is slow.
* Logs are written to the Googlebook ESP: `EFI-SYSTEM\gblog\bootN-t<seconds>.txt` (gb_bootlog). That ESP has no
  drive letter. To read it from an elevated prompt:
  1. `mountvol G: \\?\Volume{<ESP partition GUID>}\` (the GUID is `esp_id` in install_state.json; on GPT the volume
     GUID equals the partition GUID)
  2. read `G:\gblog\`
  3. `mountvol G: /D`
* Back in Windows, a read-only check is `gbdisk verify --disk 0 --plan C:\GB-backup\<ts>\plan.json --layout-only`.
  Android's boot_control HAL changes the vbmeta slot bits, and verify reports that as a note.
  `gbdisk layout --disk 0` works without admin.

## Updating Googlebook's kernel/initrd later (userdata and system untouched)

```powershell
python -I -B build_internal.py --out C:\gb-export2 --reuse-guids C:\GB-backup\<ts>\manifest_installed.json
.\install_dualboot.ps1 -ExportDir C:\gb-export2 -UpdateEspOnly          # dry run
.\install_dualboot.ps1 -ExportDir C:\gb-export2 -UpdateEspOnly -Apply   # rewrites only EFI-SYSTEM, verified
```

The GUIDs must stay the same, because the boot entry names the installed super. Without `--reuse-guids` the
installer refuses.

**Checks before anything is written (wave 3).** Each refusal exits with code 2 and changes nothing on the disk:
1. **Active slot.** The installer decodes the installed `vbmeta_a`/`vbmeta_b` GPT attributes exactly like
   gb_slotsync does on the device (priority bits 48-51, tries 52-55, successful 56; the highest priority among
   successful slots or slots with tries left). It refuses when that slot is not the export's `manifest.slot`, when no
   slot is bootable, and when both slots are bootable with the same priority (ambiguous). An ESP for the wrong slot
   would point the default entry at an old system over a `/data` that the other slot has already upgraded.
2. **Same build.** `gbdisk verify` (read-only) compares `boot_<s>`, `init_boot_<s>`, `vendor_boot_<s>` and
   `vbmeta_<s>` on the disk with the export (sha256). The export's `initrd-<s>.img` is made from those images, so if
   the slot was updated on the device since the export (an OTA a->b->a, or b refreshed again) the letters still match
   but the content does not, and the update is refused. Remedy: boot Googlebook instead (gb_slotsync rebuilds the ESP
   for slot changes), or rebuild the export from a source whose slot `<s>` holds that same build (`GB_SLOT=<s>` +
   `build_internal.py --reuse-guids <installed manifest>`), then retry. `--reuse-guids` alone would rebuild the same
   stale content.
3. **Loader default.** When the installed ESP is readable, its `loader\loader.conf` default must be `gb-<s>`.

**What is saved first.** With `-Apply` the whole installed EFI-SYSTEM is saved raw before the rewrite (the
installer's run folder, `esp-before-<stamp>.img` + `.sha256`, 512 MiB, read back once; `gbdisk dump`, read-only).
`gblog\`, `gbinstall\`, `gb\gb.cfg` and `gb\overlay\` are copied out as files into `gblog-preserved-<stamp>\`
with a `SHA256SUMS.txt` (on a real disk through a temporary drive letter that is removed again before the write; with
`-TargetImage` from the saved image, `tools\esp_extract.py`, `python -I`). The rewrite removes `gb\gb.cfg` and
`gb\overlay\` (the opt-in bring-up files) from the ESP; the installer says so and names the folder with the copies.
If the raw save fails, the update is refused; `-NoEspBackup` goes on without it (only for an ESP that cannot be read).
Keep 1 GiB free on the backup drive per update.

**Rollback** (the installer prints both lines; the run folder also holds `manifest_esp_rollback_<stamp>.json`, a copy
of the export manifest whose EFI-SYSTEM entry names the saved image and its sha256):

```powershell
.\gbdisk.exe plan --manifest C:\GB-backup\<ts>\manifest_esp_rollback_<stamp>.json --existing --disk 0 --out C:\GB-backup\<ts>\plan_esp_rollback.json
.\gbdisk.exe write --plan C:\GB-backup\<ts>\plan_esp_rollback.json --only EFI-SYSTEM --source-dir C:\GB-backup\<ts> --disk 0 --apply
```

Notes:
* `super` is not rewritten by `-UpdateEspOnly`. A new system build means uninstall + install, or an OTA inside
  Googlebook.
* gb_slotsync handles OTA slot switches on the ESP.
* **Not offered on purpose:** replacing only `vmlinuz` and `gb\inject.gz` (and invalidating the slot stamp).
  `initrd-<x>.img` embeds `inject.gz` (it is `[inject | vendor | generic | bootconfig]`), so the first boot would run
  the new kernel with the old initrd's modules (vermagic and signature mismatch) before gb_slotsync could rebuild
  anything. Only the whole verified ESP image is written.
* A later ISO "update" or "harvest gblog" tool should reuse the same three checks and the raw save.
* Idea credit: the gbOS86 update flow (`gbos86.com/docs/updating`; github.com/linuxkid473/gbos-x86 @f8a5c1a,
  `install.sh`); no code was taken.

## Uninstall

```powershell
.\uninstall_dualboot.ps1           # dry run: lists exactly what would be removed
.\uninstall_dualboot.ps1 -Apply    # elevated
```

The steps:
1. Back up the BCD and the GPT (`pre_uninstall`).
2. `bcdedit /delete {id}` on the Googlebook entry.
3. Delete the Googlebook partitions. A partition is deleted only if all of these hold:
   * it has a Googlebook name;
   * it has a Googlebook type;
   * it is not in the pre-install layout;
   * its GUID is in the install plan, and it is still exactly at the offset and length the plan gave it, inside
     the region the install freed (otherwise the whole removal is refused);
   * it lies after D: (the shrunk region), and no other partition lies between D: and the Googlebook run.

   Every other entry must come through the change unchanged.
4. `Resize-Partition` D: back to its **original size**. This also runs when no Googlebook partition is left but D:
   is still smaller than before (an install that stopped after the shrink).
5. Compare every partition with `layout_pre_shrink.json`.

If the state is lost, `-Force` removes by name + type + "after D:" + contiguity only, and D: is extended to its
maximum. A Googlebook-named partition anywhere before D: (another Linux/Android install, say) is never a candidate.
Manual alternative: Disk Management cannot delete these partition types. Use `diskpart` → `select disk 0` →
`list partition`. Check offsets and sizes against `layout_pre_shrink.json`, never touch partitions 1-5, run
`select partition N` → `delete partition override` for each one, and then **Extend Volume** on D:.

## Installed from the ISO

The installer ISO (`googlebook-sb3-installer.iso`, option 2 "install next to Windows") creates the same partitions
as `install_dualboot.ps1`, in unallocated space you made with Disk Management (it never shrinks D: itself), and a
firmware boot entry "Googlebook OS" after Windows Boot Manager. It writes its state to `\gbinstall\` on the new
Googlebook ESP in the formats these scripts use (`install_state.json` with `mode` `iso`, `plan.json`,
`layout_pre_install.json`, the `gpt_pre_install_*`/`gpt_post_install_*` copies, `manifest_installed.json`,
`SHA256SUMS`).

* `Restart-ToGooglebook.ps1` works without an import: it finds the entry by its name.
* Before `uninstall_dualboot.ps1`, `install_dualboot.ps1 -UpdateEspOnly` or `-RepairBootEntry`, import the state
  (elevated; it reads the ESP through its volume path, no drive letter, no mountvol, nothing written to the ESP):

  ```powershell
  .\Import-GooglebookIsoInstall.ps1 -EspId <ESP GUID shown by the installer>          # dry run
  .\Import-GooglebookIsoInstall.ps1 -EspId <ESP GUID shown by the installer> -Apply
  ```

  It checks `SHA256SUMS`, that the state is an ISO state for this disk (disk GUID), that every planned partition is
  on the disk as planned (only the vbmeta slot bits may differ) and that `gbdisk verify --layout-only` accepts the
  plan, then copies the files to `C:\GB-backup\<yyyyMMdd-HHmmss>-iso\` and writes the pointer
  `C:\GB-backup\googlebook_install.json`. An existing pointer is refused unless `-Force` (it is kept as
  `.bak-<stamp>`).
* If Windows gives the ESP no volume path, boot the installer, use tools `w` (it copies `\gbinstall\` to a USB
  stick), then `.\Import-GooglebookIsoInstall.ps1 -From <stick>:\gbsb3-logs\<...>\gbinstall -Apply`.
* `install_dualboot.ps1 -UpdateEspOnly` rewrites the ESP and drops `\gbinstall\` (since wave 3 it keeps a copy in
  its run folder, `gblog-preserved-<stamp>\gbinstall\`): import first.
* `uninstall_dualboot.ps1` has two guards for ISO installs:
  * **Guard A** (step 4): when D: already has its original size (an ISO install never shrank it), nothing is extended;
    give the freed space back with Disk Management > Extend Volume.
  * **Guard B** (before any change): if Googlebook partitions after D: are not in the plan of the state it uses
    (e.g. a stale `C:\GB-backup` pointer from an older Windows install, while the partitions were made by the ISO),
    it refuses with "... are not from ...": run `Import-GooglebookIsoInstall.ps1` first, or pass `-StateFile` with
    the right `install_state.json`. (`-Force` alone does not bypass this guard while a pointer exists; it only
    covers a lost state: removal by name + type + contiguity.)
* The installer's option 3 removes an ISO install without Windows (boot entry first, then the partition table
  entries; the data is not overwritten), and its tools `c` compares the disk with the post-install copy after the
  first Googlebook boot (read-only).

## Recovery

| situation | what to do |
|---|---|
| The install stops part-way | The phase is in `install_state.json` and the error names the step. Nothing after the failed step ran. `uninstall_dualboot.ps1 -Apply` removes whatever was created and extends D: back to `original_length`, also when the failure came right after the shrink and no partition was created. By hand: Disk Management → Extend Volume, or `Resize-Partition -DriveLetter D -Size <original_length>`. |
| Surface starts Googlebook (or the Surface logo loops) instead of Windows | Hold Power 10 s. Hold Volume Up + Power → Surface UEFI → Boot configuration: put Windows Boot Manager first, or swipe it to boot once. If Windows Boot Manager is missing there (firmware entries reset), the "Internal Storage" fallback may start our ESP's `\EFI\BOOT\BOOTX64.EFI`, and systemd-boot on that ESP has no Windows entry: boot the Surface recovery drive → Troubleshoot → Command Prompt → `bcdboot C:\Windows /f UEFI` (check the Windows drive letter with `dir` first) to recreate the Windows Boot Manager entry. |
| Googlebook does not start / hangs after the systemd-boot menu | Hold Power for 10 s to switch off. The next boot is Windows, because BootNext is used up. See the NX_COMPAT note below. |
| Windows Boot Manager is no longer first | Surface UEFI → Boot configuration: drag Windows Boot Manager to the top. Or, elevated: `bcdedit /set {fwbootmgr} displayorder {bootmgr} /addfirst`. |
| The Googlebook boot entry vanished (e.g. after a Windows feature update) | `.\install_dualboot.ps1 -RepairBootEntry -Apply` |
| BCD damaged | Elevated: `bcdedit /import C:\GB-backup\<ts>\bcd.bak`. Without Windows: Surface recovery drive → Troubleshoot → Command Prompt → `bcdboot C:\Windows`. |
| GPT damaged, Windows still boots | `gbdisk restore-gpt --disk 0 --from C:\GB-backup\<ts> --tag post_shrink` (dry run), then add `--apply`. It writes back LBA 0-33 and the last 33 LBAs (tags: `pre_shrink`, `post_shrink`, `post_install`, `pre_uninstall`), then re-reads the table. `post_shrink` is the shrunk D: without Googlebook partitions. `pre_shrink` restores D:'s original partition size, but its NTFS stays shrunk: run Extend Volume afterwards. |
| GPT damaged, Windows does not boot | WinRE and the recovery drive have no .NET, so gbdisk cannot run there. From any Linux live USB (copy the backup folder onto it first), with `D=/dev/nvme0n1`: `dd if=gpt_post_shrink_primary.bin of=$D bs=512 count=34 conv=fsync` and `dd if=gpt_post_shrink_backup.bin of=$D bs=512 seek=$(( $(blockdev --getsz $D) - 33 )) conv=fsync`. The disk size must be unchanged; the backup files were taken from this same disk. |
| Nothing helps | Surface recovery drive → reinstall Windows (data loss), restore from your backups. |

## Surface Book 3 notes and risks

* **Boot loader choice.** linux-surface reverts the kernel's PE `NX_COMPAT` flag (patch "Revert efi/x86: Set the
  PE/COFF header's NX compat flag unconditionally"). Their reason is that Surface devices get stuck on boot with NX
  advertised, a problem seen with GRUB. We do not use GRUB: our loader is systemd-boot 262.1, whose own PE has
  NX_COMPAT. Our GKI bzImage has `DllCharacteristics = 0x0100` (NX_COMPAT set, verified). **If boot freezes right
  after the systemd-boot menu**, rebuild with `build_internal.py --clear-nx-compat --reuse-guids ...`, which clears
  the flag in the ESP's copy of the kernel only, and use `-UpdateEspOnly -Apply`. The USB stick shows the same
  behaviour, so test on the stick first.
* **Two ESPs on one disk.** The UAPI Boot Loader Specification expects a single ESP per disk. Windows uses only the
  ESP its boot manager was started from, and Surface UEFI starts the firmware entry it is given, so this works in
  practice. It is still the main design risk.
  * The "Internal Storage" fallback entry in Surface UEFI might pick either ESP's `\EFI\BOOT\BOOTX64.EFI`.
  * If the firmware misbehaves, the alternative from the boot-design research is systemd-boot on the Windows ESP
    with an XBOOTLDR partition holding the kernel and initrds. That is not implemented.
* **Android writes the Windows disk's GPT.** The android-desktop boot_control HAL rewrites the primary and backup GPT
  of the disk holding `vbmeta_a/b` when it updates the slot bits (`bootctl mark-boot-successful`, OTA). In the VM
  these writes were CRC-correct and only touched the vbmeta entries. After the first internal boot, run
  `gbdisk layout --disk 0`, or uninstall's verify logic, to confirm that partitions 1-5 are unchanged.
* **Never run Googlebook's own "install to disk".** `android-desktop-install` zaps the whole disk; the native tree
  stubs it out (GB_REFUSE). Also never let Disk Management format the "unknown" partitions.
* **TPM / SPI flash / NVRAM.** The native cmdline blacklists the TPM, SPI-NOR and gsmi modules and sets
  `efi_pstore.pstore_disable=1`, so Android never touches the Surface TPM, the BIOS flash or the UEFI NVRAM (except
  the optional BootNext tool above).
* **BitLocker.** The install needs BitLocker off. If you enable it later on C: (with Secure Boot off it seals to
  PCRs 0/2/4/11), keep the recovery key: firmware boot-order changes can trigger recovery.
* **Clock.** Windows keeps the RTC in local time and Android keeps it in UTC, so the clock is off by your UTC offset
  after switching until NTP syncs.
* **Windows updates.** They may move Windows Boot Manager back to first place, which is harmless (it is already
  first), or drop foreign entries; then run `-RepairBootEntry`. The 1 GiB left before Recovery lets Windows/WinRE
  servicing recreate a bigger Recovery partition.
* **Not tested on the real disk.** Everything here was exercised only against image files and in read-only dry runs
  against disk 0 (see below). The first real run is yours. Do it with the backups above, and do the USB stick bring-up
  first.

## Test harness

`-TargetImage <file>` runs the same gbdisk partitioning, naming, attribute and raw-write code against a raw disk image
(no admin):
* "Shrink D:" is simulated on partition `-ImageShrinkPartition` (default 4) with `gbdisk resize-image`.
* bcdedit is never run; the commands are printed.
* Backups and state go to `<image>.gbbackup\` and `<image>.gbstate.json`.

```powershell
# small: real ESP + real boot images, super truncated to 64 MiB (manifest marked test_truncated, refused on a disk)
python -I -B build_internal.py --out D:\gb-test\export_small --test-super-mib 64 --test-userdata-min-mib 512
.\test\run_image_tests.ps1 -Image D:\gb-test\cycle_small.img -ExportDir D:\gb-test\export_small -Tag small
# full size: the real export, D: 40 GiB in the image, shrink 28 GiB
.\test\run_image_tests.ps1 -Image D:\gb-test\cycle_full.img -ExportDir D:\gb-export -CMiB 1024 -DMiB 40960 -ShrinkGB 28 -Tag full -DeleteImage
```

The image is created sparse, but it ends up allocated at its full logical size on NTFS (42 GiB for the full run),
and the space comes back only when it is deleted. Both test scripts refuse to start unless the image's volume has
the image size + 10 GiB free (`run_edge_tests.ps1`: 22 GiB; it deletes each ~9 GiB image right after its case).
`-DeleteImage` removes the cycle image at the end. Never run them on a nearly full C:.

More tests:
* `test\run_edge_tests.ps1 [-WorkDir DIR] [-ExportDir D:\gb-test\export_small]`: refusals and edge shapes, each
  followed by a byte-identity check of the image: not enough space, shrink larger than D:, too much userdata,
  D: not the last data partition (E: after it: install + uninstall), a foreign Googlebook-named partition before
  D: (install refused, forced remove leaves it alone), image too small / no GPT / D: too small, a truncated and a
  corrupted export image, tampered plans for write and remove, an install that stopped after the shrink (uninstall
  gives the space back), a forced remove anchored at the wrong partition, device paths given as `--image`.
* `test\test_bcd_parse.ps1`: offline test of the `bcdedit /enum firmware /v` parser (well-known GUIDs mapped to
  `{fwbootmgr}`/`{bootmgr}`) and of the guard that refuses an empty or well-known id for `bcdedit /set` / `/delete`.

The cycle runs these steps:
1. `gpt_validate.py mkimage` builds a sparse image shaped like disk 0. It has the same names, types, GUIDs and
   attributes, including Recovery's `0x8000000000000001` and the 1 MiB gap before Recovery, plus random data
   blocks.
2. Take a snapshot: per-MiB digests plus the GPT.
3. Install dry run: the image must stay byte-identical.
4. `-Apply`, then validate:
   - protective MBR, primary and backup headers and the CRC32s, identical entry arrays;
   - names, types, GUIDs and attributes, 1 MiB alignment, no overlaps;
   - pre-existing entries byte-identical apart from the shrunk D:;
   - content sha256 equal to the export files and the blanks zeroed;
   - **every MiB outside the new partitions byte-identical**.
5. Negative tests: a second install is refused, a TEST export is refused for a real disk, a mismatched plan is
   refused, remove without state is refused, and growing D: into Googlebook is refused. The image must still be
   unchanged afterwards.
6. `-UpdateEspOnly`.
6b. (wave 3) `-UpdateEspOnly` refusals and the backup, using the test helpers `test\set_attrs.py` (GPT attributes +
   CRCs) and `test\fat_add.py` (files into the image's ESP), both `python -I`, image files only. Each refusal must
   exit 2 and leave the image byte-identical: slot a active for an export of slot b; both slots bootable with the
   same priority; no slot bootable; one byte of `vendor_boot_<s>` changed; a loader.conf default of the other slot.
   Then an ESP holding `gblog\`, `gbinstall\`, `gb\gb.cfg` and `gb\overlay\`: a dry run changes nothing; with
   `-Apply` the raw backup equals the ESP before the update (sha256), the copies match the files that were put there,
   the new ESP equals the export; the printed rollback restores the saved ESP; a normal update follows; and
   `test\test_wanted_slot.ps1` runs the wanted_slot truth table shared with gb_slotsync's `test_gb_slotsync.sh`.
7. Uninstall dry run (image unchanged), then `-Apply`. The GPT must be **byte-identical to before the install**.
8. A plan that does not fit is refused.
9. Re-install, then `gbdisk restore-gpt --tag pre_shrink`. The GPT must be byte-identical again.

**Results (2026-10-24; image files and read-only dry runs only, the logs are not part of the repository):**
* Small cycle: 25/25 steps pass. Validator: 88/88 checks after install, the GPT is byte-identical after uninstall
  and after `restore-gpt`, and 8187 MiB units compared.
* Full-size cycle: a real export (12 GiB super) in a 42 GiB image with D: shrunk 40 → 12 GiB. 25/25 steps pass,
  88/88 checks, 43003 MiB units compared.
* Real-disk dry runs: install/uninstall dry runs against disk 0 without elevation. The layout is unchanged
  afterwards and no backup folder was created. A `-ShrinkGB` larger than D:'s free space is refused; a smaller one
  that leaves userdata at least 8 GiB is accepted.

**Wave 3 (2026-10-26):** small cycle with the 6b cases 57/57 (every refusal exit 2 for the intended reason, the
image byte-identical after each), wanted_slot truth table 20/20 against the gb_slotsync table.

**Safety review (2026-10-25):** small cycle 25/25, edge cases 56/56, BCD parser 19/19, real-disk dry runs
read-only (layout byte-identical before/after, no backup folders created). Fixed in that review: the firmware
boot-order check could never pass on a real system (`/v` prints GUIDs, the code compared with `{fwbootmgr}`); a
forced uninstall could delete any Linux-typed partition with a Googlebook name anywhere on the disk; the
BitLocker check treated an unreadable state as "off"; `gbdisk backup` did not prove that its two files hold the
whole GPT; `gbdisk write` did not itself refuse non-Googlebook or pre-existing partitions in a plan; no identity
re-check right before the shrink; the uninstaller did not give the space back after an install that stopped
after the shrink. The full-size cycle was not re-run in the review (disk space); the changed code is
size-independent.
