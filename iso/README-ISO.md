# Googlebook SB3 installer ISO

`D:\GB-iso\googlebook-sb3-installer.iso` is one bootable file that installs Googlebook OS (Google's Android 17
"lapis" desktop image, build CL3B.260622.276.R4) on the Microsoft Surface Book 3. It can be used from a Rufus stick
(ISO mode, recommended, or DD mode), a Ventoy stick, or as a VMware CD. Rufus and Ventoy themselves were never run
here; equivalent stick layouts were tested in VMware (see [Known limits](#known-limits)).

> **Personal use only.** The ISO contains Google's proprietary disk image. Keep it on this PC and on your own
> sticks. Never share, upload or publish it.

It boots a small Linux installer (GRUB, Ubuntu kernel 6.8.0-147-generic, busybox, Python). The installer offers four
options:

1. **Write Googlebook to a whole disk**, usually a USB stick. The result is the verified USB image, byte for byte.
2. **Install next to Windows** into unallocated space that you created in Windows beforehand. This is the same layout
   as `install_dualboot.ps1`, plus a UEFI boot entry "Googlebook OS" placed **after** Windows Boot Manager.
3. **Uninstall** what option 2 added.
4. **Tools**: shell, logs, payload check, compare, save logs, rescan, reboot.

Every destructive step asks you to type a confirmation, and the installer refuses disks it must not touch (see
[The menu](#the-installer-menu)).

**Current build** (2026-10-31, build `20261031-012525-c2e567c5`): 7,472,746,496 bytes, SHA-256
`547b454e6dcf72bac62544daeaec55abf787f7b1ffc2821c50a1de509d9146ce`. It carries the Wave 8 USB image `35d6043d…`
(kernel build #16 with the Realtek SD-card reader drivers and an escaped VNNI summary, the person's own Display size
and launcher kept, updates into slot A that really start; on top of Wave 7's settings that reach the signed-in user
after any sign-in and louder update notice, Wave 6's notifications that reach the signed-in user, NVIDIA only on the
13.5" model, auto-rotate per posture and read-only VNNI libraries, and the Waves 4 and 5 software lock-screen
Gatekeeper, VNNI `libz` patch, OTA gate and native-readiness fixes: README-FLASH.md, "What is new") and a fresh
dual-boot export (new GUIDs; made with `build_internal.py` and then reused by `build_iso.py`,
which ran all its export cross-checks and `verify_image.py` on it). Only its two ESP objects differ from the previous
ISO `e223fdc9…`; its 11 other payload objects are byte-identical, and the installer's files are unchanged since
`afb68d38…`.
**Boot-menu edits** on an installed system (Wave 6 finding): the editor that **e** opens starts with the cursor at
the start of the line; press **End** before typing an option (README-FLASH.md, "What is new").
**Performance:** the system it installs started about 47 % faster in VMware with the VNNI patch (first boot until
"boot completed" 587.7 s → 313.6 s, later boots 226.0 s → 118.0 s; README-FLASH.md). **Tests:** this build passed T0, T1a/T1b (option 1 wrote a disk byte-identical to the USB image `35d6043d…`), the dual-boot groups G1-G4 (T3 with its refusal cases, T4, T4b, T9) and T8 (the Windows scripts; since Wave 8 the `-UpdateEspOnly` check reads either form of the boot menu's default line) on 2026-10-31 (11 test ids, 378 checks, 0 failed). The previous ISO `e223fdc9…` (Wave 7; kept in
`D:\GB-iso\prev\iso-w7-e223fdc9\`) passed T0, T1a/T1b (option 1 wrote a disk byte-identical to the USB image
`7ac6ad52…`), the dual-boot groups G1-G4 (T3 with its refusal cases, T4, T4b, T9) and T8 (the Windows scripts;
`-UpdateEspOnly` checks the lock-screen PIN path) on 2026-10-30 (11 test ids, 378 checks, 0 failed). The ISO
`afb68d38…` (Wave 6; kept in `D:\GB-iso\prev\iso-w6b-afb68d38\`) passed T0, T1a/T1b, T2b/T2c/T2h, the dual-boot
groups G1-G4 and T8 on 2026-10-30 (15 test ids, 402 checks, 0 failed); the other 22 test ids of the full suite
passed the same day on the Wave 6 ISO `ad67f040…` (before the notification fix, same installer; no longer kept).
The ISOs `8aa73f17…` (Waves 4 and 5; its test subset, 264 checks, 0 failed) and `b1c355b0…` (Wave 3; the full
suite, all 38 tests, `OVERALL PASS`) are no longer kept. Everything was still tested only in VMware and on disk
image files, never on the real laptop.

---------------------------------------------------------------------------------------------------------------------

## Contents

* [Before you start](#before-you-start)
* [Putting the ISO on a stick](#putting-the-iso-on-a-stick): [Ventoy](#ventoy), [Rufus ISO mode](#rufus-iso-mode-recommended),
  [Rufus DD mode / dd](#rufus-dd-mode-or-dd), [VMware](#vmware-testing)
* [Booting the Surface Book 3 from the stick](#booting-the-surface-book-3-from-the-stick)
* [The installer menu](#the-installer-menu)
* [Preparing Windows for dual boot (option 2)](#preparing-windows-for-dual-boot-option-2)
* [After a dual-boot install](#after-a-dual-boot-install)
* [Boot entries and the NVIDIA payload](#boot-entries-and-the-nvidia-payload)
* [Uninstall](#uninstall)
* [Recovery](#recovery)
* [Logs](#logs)
* [Building the ISO](#building-the-iso)
* [Known limits](#known-limits)

---------------------------------------------------------------------------------------------------------------------

## Before you start

* **Size.** The ISO is about 7.5 GB (the hard limit is 8.0 GiB). Every file in it is smaller than 1 GiB, so FAT32
  can hold all of them.
* **Check the file** before writing it to a stick. In PowerShell:
  `(Get-FileHash D:\GB-iso\googlebook-sb3-installer.iso -Algorithm SHA256).Hash` must equal the first word of
  `D:\GB-iso\googlebook-sb3-installer.iso.sha256`.
* **Secure Boot must be off.** The ISO's GRUB and kernel are not signed for Secure Boot. On the Surface: Surface UEFI
  → Security → Secure Boot → Disabled. The red bar on the Surface boot screen is normal with Secure Boot off.
* **UEFI only.** The ISO has no legacy BIOS boot code.
* **Back up your data.** Option 2 changes the partition table of the Windows disk. Back up C: and D: before you
  start, and also make the GPT backup described in
  [Preparing Windows](#preparing-windows-for-dual-boot-option-2).

---------------------------------------------------------------------------------------------------------------------

## Putting the ISO on a stick

### Ventoy

Ventoy keeps `.iso` files on a normal data partition and boots them from its own menu (ventoy.net).

> **Not tested with real Ventoy.** Prefer [Rufus ISO mode](#rufus-iso-mode-recommended). What was tested in VMware:
> the installer finds the `.iso` file on an exFAT or NTFS "Ventoy" partition by itself (T6, T6n); a stock GRUB that
> loop-mounts the `.iso` file from the stick and runs its menu, like Ventoy's GRUB2 mode, boots it with no CD present
> (T6g); and a read-only device-mapper map of the `.iso` file over the whole stick, like Ventoy's normal-mode
> `/dev/mapper/ventoy`, is used as the payload while the stick stays protected (T6m). Ventoy's own boot code and
> its normal-mode hand-over to the installer were never run.

1. Install Ventoy on a stick with Ventoy's own tool. The Ventoy data partition must be **exFAT** (Ventoy's default)
   **or NTFS**. A FAT32 Ventoy partition cannot hold a file this large.
2. The data partition needs **free space of at least the ISO size, about 7.5 GB** (check the exact byte size of
   the file in Explorer; keep some margin).
3. Copy `googlebook-sb3-installer.iso` onto the Ventoy partition. Any folder works, for example `\ISO\`.
4. Boot the stick and choose the ISO in Ventoy's menu.
5. If Ventoy's **normal mode** does not reach the installer menu, choose the image again and use Ventoy's
   **GRUB2 mode** (see "GRUB2 Mode" on ventoy.net). Do not use Ventoy's memdisk mode.
6. If the installer starts but behaves oddly, keep its log before you reboot: tools `l` shows `init.log`
   (including the `/dev/mapper` and `/ventoy` listings), tools `w` saves all logs to a second FAT/exFAT stick.

The installer finds the `.iso` file on the stick by itself, so Ventoy does not need to tell it anything. It searches
exFAT, FAT and NTFS partitions of USB sticks up to 6 folder levels deep. It does **not** search ext4, XFS or UDF Ventoy
partitions.

While the installer runs, the Ventoy stick is protected: it is never offered as a target and is never written. That
includes logs; save logs to a different stick.

### Rufus ISO mode (recommended)

1. Use a stick of **16 GB or more**. Everything on it is erased.
2. In Rufus, select the ISO. Set **Partition scheme: GPT** and **Target system: UEFI (non CSM)**. The file system is
   FAT32 (Rufus may call it "Large FAT32").
3. When Rufus says the image is an "ISOHybrid" image, choose **Write in ISO Image mode**.
4. If Rufus offers to download GRUB or other files, choose **No**. The ISO is UEFI-only and needs nothing extra.
5. The stick then holds the ISO's files. GRUB finds them through the marker file `\gbsb3\GBSB3-<build>.marker`, not
   through the volume label. The whole `gbsb3` folder must stay on the stick, unchanged.

### Rufus DD mode, or dd

1. Use a stick of **16 GB or more**. Everything on it is erased.
2. In Rufus, choose **Write in DD Image mode**. On Linux, `dd` the ISO to the whole stick.
3. The stick now has the ISO's own partition table, a protective MBR plus a GPT ("isohybrid GPT", not a hybrid
   MBR): partition 1 is the ISO9660 volume, partition 2 is a small EFI system partition, and partition 3 is xorriso's
   tiny padding partition ("Gap1"). The GPT's backup copy sits at the end of the ISO data, not at the end of the
   stick; UEFI firmware boots it anyway (tested in VMware with a 32 GiB disk, T7b).
4. Afterwards, Windows sees partition 1 (and maybe partition 3) as RAW and offers to format it. Click **Cancel**
   every time. Formatting destroys the installer.

### VMware (testing)

Attach the ISO as a CD/DVD image to a VM with **EFI firmware** and Secure Boot off. Only under VMware (SMBIOS vendor
"VMware, Inc."), the GRUB menu also shows three `TEST:` entries that put the installer on the serial port ttyS0.
These entries are for the automated tests. In a VM, the installer sees only the VM's virtual disks.

---------------------------------------------------------------------------------------------------------------------

## Booting the Surface Book 3 from the stick

1. Surface UEFI (hold **Volume Up**, press **Power**):
   * Security → Secure Boot: **Disabled**
   * Boot configuration → Advanced options: **Enable boot from USB devices** on
2. **Dock the clipboard on the keyboard base, and leave it docked.** All USB ports are on the base.
3. Plug the stick into the base. **Have a wired USB keyboard ready** and plug it into the base too if the base
   keyboard does not respond: the installer loads the Surface keyboard drivers (Surface Aggregator, HID), but they
   were never tested on the real machine. The main screen shows `Keyboard devices: <n>`; tools `l` lists them.
4. Connect the **charger** and keep it connected. The installer refuses options 2 and 3 on battery, and it also
   refuses option 1 on the internal disk. If the firmware reports nothing about the charger ("power state
   unknown"), the installer asks you to type `AC-OK` to confirm that the charger is plugged in; tools `l` shows what
   the firmware reports (`power: ...`).
5. Shut down. Hold **Volume Down**, then press **Power** to boot from USB.
6. **Never press the detach key while the installer runs.** Detaching drops the stick and the keyboard in the middle
   of a write.
7. **First boot on the Surface:** before you choose 1, 2 or 3, open tools `l` and check the keyboard and power lines;
   if a USB stick is at hand, save the logs with tools `w`.

The GRUB menu is small on the 3000x2000 screen. It waits 10 seconds and then starts the default entry:

| entry | use |
|---|---|
| Googlebook SB3 installer | normal start (default, key `n`) |
| … (basic display: nomodeset, kernel font) | if the screen stays black or garbled (key `b`) |
| UEFI firmware setup / Reboot / Power off | keys `f`, `r`, `p` |

The installer picks the largest console font that works and fits. Kernel 6.8's framebuffer console cannot use
glyphs taller than 32 pixels: the shipped 32x64 font is refused (`setfont: ... KDFONTOP: Invalid argument`, tested in
VMware, T10), so the installer falls back to the 16x32 Terminus font. On the 3000x2000 panel that gives about 187x62
characters (small but sharp); in VMware at 2048x1536 it gave 128x48. The real panel was not tested.

If GRUB prints "`/gbsb3/GBSB3-....marker was not found on any disk`", the stick is incomplete. For example, the
`gbsb3` folder was not copied. GRUB then opens firmware setup after 60 seconds; press Esc for a GRUB prompt. Rewrite
the stick.

---------------------------------------------------------------------------------------------------------------------

## The installer menu

The installer first looks for its payload, retrying for up to 30 seconds while USB sticks appear. It then shows the
main menu. Every disk is listed with its number, kernel name (`sda`, `nvme0n1`), size and model.

### 1 — Write Googlebook to a whole disk

* Writes the exact verified USB image (`googlebook-sb3-usb.img`, 26.84 GiB) to the chosen disk. The disk must be at
  least 28,814,885,376 bytes, for example a 32 GB stick.
* On a disk at least 1 GiB larger than that, it asks whether to grow `userdata` to fill the disk.
* It lists every partition that is on the disk now. You must then type exactly **`ERASE <name>`**, for example
  `ERASE sda`. Anything else cancels.
* Every partition is read back and checked against the payload hashes.
* It **refuses**:
  * a disk with Windows: Microsoft reserved, recovery, dynamic or Storage Spaces partitions, an NTFS volume, or a
    Windows boot manager
  * a BitLocker volume, including old BitLocker To Go sticks
  * Windows installation or recovery media, such as the Surface USB recovery drive
  * a stick that holds a `GB-backup` folder or GPT backup files
  * a Ventoy stick
  * the stick or disk the installer itself runs from, and any other disk holding an installer ISO
  * a disk it could not fully scan
  * an internal (non-USB) disk that is not blank (its first 2048 and last 2081 sectors must be zero). For the
    Surface's own disk use option 2 instead; for a whole-disk install use a USB stick. If you really want to give
    an internal disk to Googlebook alone, it must be wiped on purpose first, for example with `diskpart`
    (`list disk`, `select disk <n>`, `clean`) from the command prompt of the Surface USB recovery drive. **`clean`
    destroys every partition on that disk, Windows included, and cannot be undone**: check the disk number twice.
  * a disk that is too small, read-only (also a read-only flag you set yourself), in use, or not using 512-byte
    sectors
  * another plugged-in disk that already carries the image's GUIDs (an older Googlebook stick): unplug it
  * while any plugged-in disk cannot be read (listed with flag `unreadable`; such a disk can never be chosen, and
    options 1-3 refuse with `R_DISK_UNREADABLE` while it is attached): unplug it or rescan
  * on an internal disk: running on battery
* A stick formatted without a partition table (FAT/exFAT on the whole device) is scanned like a partition: the
  listing shows it as "whole-disk file system (no partition table)", and the refusals above apply to it.
* No UEFI boot entry is created. Sticks boot through the removable-media path (Volume Down + Power).

### 2 — Install next to Windows (dual boot)

See [Preparing Windows](#preparing-windows-for-dual-boot-option-2) first.

* It writes the same partitions as `install_dualboot.ps1`: an `EFI-SYSTEM` partition of its own, then
  boot/init_boot/vendor_boot/vbmeta/pvmfw A/B, misc, metadata, frp_persist, desktop_security_*, super (12 GiB) and
  userdata (the rest, at least 8 GiB). They go into the unallocated space after D:. It keeps 1 GiB free before the
  Windows Recovery partition.
* C:, D:, the Windows ESP, MSR and Recovery are never written. Existing partition-table entries stay byte-identical.
  All data is written and verified first; the partition table is written last.
* It creates the UEFI entry **"Googlebook OS"** and appends it **after** Windows Boot Manager, so Windows stays the
  default.
* It asks for the userdata size (Enter = all of the free space). It may also offer to save the current partition
  table to a FAT/exFAT USB stick first (prompt `gptbackup`). Then you must type exactly **`INSTALL <name>`**, for
  example `INSTALL nvme0n1`.
* It **refuses** if:
  * BitLocker is on
  * Windows is hibernated or was shut down with Fast Startup (the message shows the `hiberfil.sys` signature it
    found; `HIBR`/`hibr` or `RSTR`/`rstr` block, `wake` - an image Windows already resumed from - does not)
  * Windows Boot Manager is not in the firmware boot order
  * the disk is dynamic or uses Storage Spaces
  * there is not enough unallocated space (22,358 MiB needed)
  * Googlebook partitions or a "Googlebook OS" entry already exist, including on a plugged-in Googlebook stick: unplug
    it
  * the partition table is damaged or unusual
  * it is running on battery
* At the end it shows the **ESP GUID** and the import command for Windows. Write them down.

### 3 — Uninstall what option 2 added

* It finds the dual-boot partitions of this ISO's export by their GUIDs and lists the disk. If only a leftover boot
  entry exists, it lists "boot entry only".
* It first removes the "Googlebook OS" UEFI entry, then clears the 18 partition-table entries. No partition data and
  no other entry is touched.
* Type exactly **`REMOVE <name>`**, or **`REMOVE efi`** for "boot entry only".
* It refuses on a hibernated Windows (Fast Startup), on battery, on a damaged table, or when foreign partitions lie
  between the Googlebook ones. With BitLocker on, it warns (keep your recovery key at hand) and, because it cannot
  read `hiberfil.sys` on the encrypted C:, it asks you to type `FASTSTARTUP-OFF` to confirm that you came from
  Windows with **Restart** (not Shut down) or that Fast Startup is off; anything else stops with
  `R_NTFS_UNCHECKED`.
* Afterwards: in Windows, open Disk Management → D: → **Extend Volume** to take the space back.
* Installs made by `install_dualboot.ps1`, or by an ISO built from a different export, are removed on Windows with
  `uninstall_dualboot.ps1`.

### 4 — Tools

| key | does |
|---|---|
| `s` | busybox shell (`exit` returns) |
| `l` | show the power state and keyboard devices, the last 200 log lines and any journal |
| `v` | verify the whole payload on the stick (every chunk and object hash) |
| `c` | compare, read-only: checks an installed disk's current partition table against the copy saved at install time (use after the first Googlebook boot) |
| `w` | save logs (and the install state folder `gbinstall`) to a FAT/exFAT USB stick |
| `r` | search for the payload again |
| `b`, `reboot`, `poweroff` | back, reboot, power off |

**Interrupted runs.** If a write fails or the installer crashes, the next menu shows a journal with the last phase
reached and what it means. Until you type **`ACK <name>`**, the installer refuses every further action on that disk.
From the typed confirmation until the result, Ctrl+C, Ctrl+\ and Ctrl+Z are ignored and Ctrl+Alt+Del is refused
("a disk write is in progress"); wait for the result line.

---------------------------------------------------------------------------------------------------------------------

## Preparing Windows for dual boot (option 2)

Do these in Windows, in this order:

1. **BitLocker / Device Encryption off** for C: and D:: Settings → Privacy & security → Device encryption, or Control
   Panel → BitLocker. Wait until decryption has finished.
2. **Fast Startup off**: Control Panel → Power Options → *Choose what the power buttons do* → untick *Turn on fast
   startup*. Alternatively, always reach the installer with **Restart**, never Shut down. The installer reads
   `hiberfil.sys` (read-only) and refuses while Windows is hibernated. If it keeps refusing with `R_HIBERNATED`,
   the definitive fix is an elevated `powercfg /h off` in Windows (it turns hibernation and Fast Startup off and
   deletes `hiberfil.sys`), then **Restart** into the installer.
3. **Windows Boot Manager must be in the firmware boot order.** It normally is. If the installer reports
   `R_NO_WINDOWS_ENTRY`, run `bcdboot C:\Windows /f UEFI` in an elevated prompt.
4. **Make the Surface USB recovery drive** (README_DUALBOOT, "Before you start" step 2) on a separate stick. Keep it
   **unplugged** during the install. The installer would refuse to write it anyway.
5. **Take an off-PC GPT backup** (elevated PowerShell; read-only on the disk):

   ```powershell
   C:\GB-build\native\dualboot\gbdisk.exe backup --disk 0 --tag pre_iso --out C:\GB-backup\pre-iso-<yyyyMMdd>
   ```

   Copy that folder to a USB stick. The installer can also save the pre-install GPT to a FAT/exFAT stick (prompt
   `gptbackup`).
6. **Shrink D:**: Disk Management (Win+X → Disk Management) → right-click **D:** → **Shrink Volume**. Enter at least
   **22,400** MB; **40,000 or more is recommended**. Leave the space **unallocated**: do not create a volume in it.
   With 40,000 MB, userdata gets about 25,800 MiB.
   * If Windows offers less, run `defrag D: /X` and try again.
   * The installer needs 13,142 MiB for the fixed partitions, plus at least 8 GiB of userdata, plus 1 GiB kept free.
7. Restart (not Shut down) into the installer stick. On AC power, choose **2**.

---------------------------------------------------------------------------------------------------------------------

## After a dual-boot install

* **First boot: what to check.** The installed system is the same as the USB stick's (option 1 writes that stick
  image itself), so the first-boot checklist of README-FLASH.md section 6 applies to both. After the first boot
  (wait for the 15-minute log), open `\gblog\boot1-t900.txt` on the Googlebook ESP (for a dual-boot install,
  `C:\GB-build\native\dualboot\README_DUALBOOT.md`, "First boot, logs, checks", says how to read it from Windows) and check its first screen, the `== summary` block:
  * `serial 8250:` shows `Serial: 8250/16550 driver, 4 ports`, `serial errors: dw-apb-uart -22: 0`, and
    `serial surface_serial_hub bound:` names a device (keyboard base, touchpad, batteries, charger, detach);
  * `inputs (Surface Aggregator HID, bus 0019):` lists the keyboard and the touchpad;
  * `audio cards:` lists `sofhdadsp` and `audio DRAS:` counts more than 0 lines naming it (sound);
  * `-- firmware load failures (dmesg)` is empty (note any `Direct firmware load for … failed` name, for example
    Bluetooth `ibt-…`) and `firmware i915 DMC:` names a DMC file;
  * `fs-verity:` says `ext4 feature present` with no `FS_STAT_ENABLE_VERITY_FAILED` / `Failed to run tune2fs`
    (the first boot sets fs-verity on the Googlebook data partition once, permanently);
  * `apgetty:` says `desktop.apgetty.enable=0` and `init.svc.apgetty` is not `running`;
  * `-- gb_vnni` shows `emulated <n> … irqon <n> …` with both numbers equal (the kernel's instruction emulator);
  * `i915 white veil:` and `HWUI Vulkan queueCount abort lines:` (report the counts; a washed-out picture has an
    opt-in fix, README-FLASH.md section 8), `gb_slotsync HOLD:` empty, `-- ESP bring-up` says `absent`.

  Before you share a log anywhere, remove serial numbers, Wi-Fi names and account addresses from its full `getprop`
  and `logcat` parts (the summary has none).
* **Windows stays the default.** To boot Googlebook once:
  * from Windows: elevated `C:\GB-build\native\dualboot\Restart-ToGooglebook.ps1` (it finds the entry by its name
    "Googlebook OS"), or
  * Surface UEFI (Volume Up + Power) → Boot configuration: swipe "Googlebook OS" to the left. Whether the Surface
    firmware lists the entry is not verified; if it does not, use the script.

  The next restart goes back to Windows.
* **Import the install state on Windows** if you want to manage the install from Windows. Do this before you use
  `uninstall_dualboot.ps1` or `install_dualboot.ps1 -UpdateEspOnly`. (To uninstall, the **tested** way is the
  installer's option 3; it needs no import.) Two ways:

  * **From a stick (tested in VMware, T9):** boot the installer and use tools `w`; it copies the logs and the
    `gbinstall` folder of the install to a FAT/exFAT stick (not the one holding the off-disk GPT copy: the installer
    never writes into a stick with GPT backups). Then, in Windows:

    ```powershell
    C:\GB-build\native\dualboot\Import-GooglebookIsoInstall.ps1 -From <stick>:\gbsb3-logs\<build>-<time>\gbinstall          # dry run
    C:\GB-build\native\dualboot\Import-GooglebookIsoInstall.ps1 -From <stick>:\gbsb3-logs\<build>-<time>\gbinstall -Apply
    ```

  * **Directly from the Googlebook ESP (untested):** in an elevated PowerShell,

    ```powershell
    C:\GB-build\native\dualboot\Import-GooglebookIsoInstall.ps1 -EspId <ESP GUID shown by the installer>          # dry run
    C:\GB-build\native\dualboot\Import-GooglebookIsoInstall.ps1 -EspId <ESP GUID shown by the installer> -Apply
    ```

    The script reads `\gbinstall\` from the Googlebook ESP without a drive letter and without mountvol. This path
    needs a real disk and elevation and was never run; if it reports that Windows gave the ESP no volume path, use
    the stick way above.

  Either way the state is stored in `C:\GB-backup\<timestamp>-iso\`.
* `install_dualboot.ps1 -UpdateEspOnly` rewrites the Googlebook ESP and drops `\gbinstall\`, so import first.
* **After the first Googlebook boot**, confirm that Windows' partitions are unchanged. Android rewrites the GPT when
  it updates its A/B slot bits. Either:
  * installer tools `c`, or
  * on Windows: `gbdisk layout --disk 0` plus
    `gbdisk verify --plan C:\GB-backup\<timestamp>-iso\plan.json --layout-only`.
* **If you turn BitLocker on later**, keep the recovery key. Changes to the firmware boot order can trigger
  recovery.

---------------------------------------------------------------------------------------------------------------------

## Boot entries and the NVIDIA payload

Since Wave 2 (DESIGN 13), the Googlebook ESP of both options (the stick of option 1 and the dual-boot ESP of option 2)
has two systemd-boot entries for the system slot (today B): the default `... (slot B)` (since Wave 8
`loader\loader.conf` names it as `default gb-b.conf`) and
`... (slot B) [NVIDIA test]`, which adds `gb.nouveau=test` (a test load of the NVIDIA driver; it is never the
default, choose it in the menu only when you want that test). The ESP also holds `gb\dgpu-<kernel release>.erofs` and its `.sha256` (the NVIDIA firmware, the
NVIDIA kernel modules and the Vulkan driver, about 60 MB): Googlebook copies and checks it before it ever loads the
NVIDIA driver, so leave both files where they are. Every ISO built with a fresh export (Wave 2, and its review
rebuild, DESIGN 13.4 and 13.8) carries **new** dual-boot GUIDs: it cannot remove a dual-boot install made with an
earlier ISO; remove such an install with that earlier ISO's option 3 or with `uninstall_dualboot.ps1 -Force`. The
earlier ISOs are kept in `D:\GB-iso\prev\iso-w7-e223fdc9\` (`e223fdc9…`, Wave 7),
`D:\GB-iso\prev\iso-w6b-afb68d38\` (`afb68d38…`, Wave 6), `D:\GB-iso\prev\` (`1718bcc1…`, before Wave 2) and
`C:\GB-x86\out\prev\iso-w2fix-e0067b12\` (`e0067b12…`, the Wave 2 review ISO; moved to C: for disk space). The
Wave 2 ISO `76fbb7ee…`, the Wave 3 ISO `b1c355b0…`, `8aa73f17…` (Waves 4 and 5) and `ad67f040…` (Wave 6 before the
notification fix) are no longer kept. Since Waves 4 and 5 the ESP also carries the software
Gatekeeper (lock-screen PIN): if a PIN is set on an install, clear it before an ESP update from an older ISO puts a
tree without it on that install (README-FLASH.md, "What is new").

---------------------------------------------------------------------------------------------------------------------

## Uninstall

* **From the ISO (recommended, tested in VMware: T4):** option 3 (see above). Then Disk Management → D: → Extend
  Volume.
* **From Windows, after the import:** in an elevated prompt, `uninstall_dualboot.ps1` (dry run), then
  `uninstall_dualboot.ps1 -Apply`. (Tested in VMware on an image file after an import from the stick copy, T4b/T9;
  not on a real disk.)
  * It removes the boot entry and the partitions.
  * For an ISO install, D: was never shrunk by the script. It tells you this and leaves the extend to you: Disk
    Management → Extend Volume.
  * If `C:\GB-backup` points to an older, different install, it refuses ("are not from …"). Import the ISO install
    first, or use `-StateFile` or `-Force`.
* A whole-disk stick (option 1) is simply re-formatted or re-written when you no longer need it.

---------------------------------------------------------------------------------------------------------------------

## Recovery

* **Windows does not start, or Googlebook starts instead:** see the "Recovery" table in
  `C:\GB-build\native\dualboot\README_DUALBOOT.md`. It covers:
  * the boot order (Windows Boot Manager first)
  * `bcdboot C:\Windows /f UEFI` from the Surface recovery drive
  * restoring a GPT from the `C:\GB-backup` copies with `gbdisk restore-gpt`
  * the Linux `dd` method when Windows does not boot
* **An interrupted installer run:** the journal screen tells you what happened. The main cases:

  | journal phase | meaning |
  |---|---|
  | `planned` | nothing was written |
  | whole disk, `head_zeroed`…`gpt_written` | the disk was partly overwritten and has no valid partition table: run option 1 again or discard it |
  | dual boot, `data_written` / `data_verified` | only unallocated space was written; the table is unchanged |
  | dual boot, `gpt_backup_written` | the two GPT copies differ: restore both from a pre-install copy with `gbdisk restore-gpt --disk 0 --from <dir> --tag <tag>` (dry run, then add `--apply`): your Windows backup is `--from C:\GB-backup\pre-iso-<date> --tag pre_iso`, the installer's off-disk copy is `--from <stick>:\gbsb3-gptbackup\<time>-<disk> --tag pre_install` (README_DUALBOOT Recovery) |
  | dual boot, `gpt_primary_written` … `efi_ordered` | the partitions exist, but the boot entry or the state is missing: option 3 removes everything |
  | uninstall, `efi_removed` | the boot entry is gone but the partitions are still there: run option 3 again |
  | uninstall, `gpt_backup_written` | restore from the `pre_uninstall` copy |

* `F_KERNEL_RESCAN`: the table on disk is correct, but the kernel did not pick it up. Reboot the installer.

---------------------------------------------------------------------------------------------------------------------

## Logs

* While the installer runs, the logs are in RAM under `/run/gb/log/`: `init.log` and `installer.log`. Tools `l` shows
  them.
* Tools `w` copies the logs, RAM backups, state and journals to a FAT/exFAT USB stick of your choice, in
  `\gbsb3-logs\<build>-<time>\`. It never writes the installer stick, a Ventoy stick, a GB-backup stick, Windows media
  or a BitLocker volume. A stick that holds GPT backup files counts as a GB-backup stick, **including the stick that
  received the installer's own off-disk GPT copy** (prompt `gptbackup`): use a second stick for the logs.
* After a dual-boot install, the log is also in `\gbinstall\install.log` on the new Googlebook ESP.
* With an off-disk GPT copy (prompt `gptbackup`), the stick gets `\gbsb3-gptbackup\<time>-<disk>\` with
  `gpt_pre_install_primary.bin`, `gpt_pre_install_backup.bin`, `layout_pre_install.json` and `SHA256SUMS`;
  `gbdisk restore-gpt --from <that folder> --tag pre_install` writes it back (tested on an image, T9).
* **Times** in the logs, journals and state files come from the firmware clock and carry a `Z`. Windows usually
  keeps that clock in **local** time, so on the Surface they are local times despite the `Z`.
* No log contains disk serial numbers, user names or volume labels.

---------------------------------------------------------------------------------------------------------------------

## Building the ISO

You only need this to rebuild the ISO yourself. The build runs on this PC plus the Linux builder VM.

```powershell
python -I C:\GB-x86\iso\build\build_iso.py --dry-run     # read-only checks and plan
python -I C:\GB-x86\iso\build\build_iso.py               # full build
```

* **Inputs**
  * the verified USB image `C:\GB-x86\out\googlebook-sb3-usb.img`. Its sha256 must match the `.sha256` file and the
    verified value, and `C:\GB-x86\verify_image.py` must report `RESULT: OK` on it (DESIGN 13.2).
  * the dual-boot export `C:\GB-x86\iso\work\export\`. It is made once with `build_internal.py` and then reused, so
    that every rebuilt ISO keeps the same dual-boot GUIDs and can uninstall an older ISO's install. Its ESP must hold
    the same tree as the USB image's (export cross-check 8, DESIGN 13.3); a new kernel or driver tree therefore needs
    `--new-export` (Wave 2 did that, 13.4).
  * the installer tree `C:\GB-x86\iso\installer\`
* **Outputs** in `D:\GB-iso\`:
  * `googlebook-sb3-installer.iso` (set read-only)
  * `.iso.sha256`
  * `googlebook-sb3-installer.manifest.json` (payload manifest)
  * `googlebook-sb3-installer.build.json` (build record)
  * `build-<build_id>.log`
* **Options**
  * `--new-export` makes a fresh export with **new** dual-boot GUIDs. An ISO built that way cannot uninstall an install
    made from an older ISO; use the older ISO or `uninstall_dualboot.ps1 -Force` for that.
  * `--no-payload-cache` recompresses everything.
  * `--keep-nx-compat` leaves the kernel's NX flag set.
  * `--keep-work` keeps the builder's work directory.
* **Disk space:** D: needs about 15.5 GiB free during a build. A fresh export needs about 15 GiB on C: for a while.
  When C: cannot spare that, make the export elsewhere (`set GB_SLOT=<the image's slot>` and
  `python -I -B C:\GB-build\native\dualboot\build_internal.py --out <new folder on D:>`), move the old
  `work\export\` aside, copy only that export's `manifest.json` and `EFI-SYSTEM.img` into a new `work\export\` and
  build without `--new-export`: the reused export then gets every export cross-check and `verify_image.py` (the
  2026-10-29 build was made this way).
  The builder keeps a chunk cache (about 7 GiB) and `/var/tmp/gbiso/last/googlebook-sb3-installer.iso`. The build
  needs 25 GiB free on the builder's `/`. The cache is keyed by the USB image's sha256, so a new USB image cannot
  reuse an older image's cache (`/var/tmp/gbiso/cache/<old sha>-*`); a successful build removes the older caches, and
  removing one by hand beforehand frees the space when the preflight reports less than 25 GiB. If that is still not
  enough (2026-10-29: 23.9 GiB), the builder's `last/` copy of the previous ISO can go too, after checking that its
  sha256 equals the ISO kept on Windows: a successful build replaces it anyway.
  The 2026-10-30 (Wave 7) build removed nothing instead: the builder's `last/` copy was moved to
  `D:\GB-iso\prev\iso-w6b-afb68d38\builder-last\` (sha256 and byte comparison, then stored as an NTFS hard link to
  the identical kept ISO), six chunk files of the old cache were moved to the builder's `/build/w7build/`, and the old
  cache directory was renamed to the new image's key. `build_iso.py` then reused the 30 chunks whose raw sha256
  matched its fresh scan (and whose compressed files matched the cache record) and recompressed 6.
  The 2026-10-31 (Wave 8) build did the same: the builder's `last/` copy went to
  `D:\GB-iso\prev\iso-w7-e223fdc9\builder-last\` (an NTFS hard link to the identical kept ISO), seven chunk files of
  the old cache and the older chunks parked on the builder were moved to the PC (`D:\gb-w8build\builder-moved\`,
  sha256 checked on both ends), the old cache directory was renamed to the new key, and `build_iso.py` reused 29
  chunks and recompressed 7. Its dual-boot export was made on D: and only its `manifest.json` and
  `EFI-SYSTEM.img` were copied into a new `work\export\`, as described above.
* `--test-tiny-payload` is a pipeline test only. It writes `D:\GB-iso\pipeline-test\` and its ISO cannot install
  Googlebook.

---------------------------------------------------------------------------------------------------------------------

## Known limits

* **Not yet tested on the real Surface Book 3.** Everything was tested in VMware and in equivalents of Ventoy and
  Rufus. Untested on the real hardware:
  * the Surface keyboard driver stack in the installer (have a wired USB keyboard ready). The installer's Ubuntu
    kernel starts the 8250 serial driver that the Surface Aggregator needs by itself
    (`CONFIG_SERIAL_8250_RUNTIME_UARTS=32`, checked when the ISO is built and by T0), so, unlike the Googlebook
    kernel, it needs no `8250.nr_uarts=` boot option
  * the console font size on the real panel (16x32 is expected; 32x64 cannot work on this kernel)
  * whether `surface_charger`/`surface_battery` report the charger. If they report nothing, the installer asks you
    to type `AC-OK`; if they report "on battery" while the charger is plugged in, use the Windows scripts.
  * whether Surface UEFI shows the "Googlebook OS" entry
  * `Import-GooglebookIsoInstall.ps1 -EspId` (use the stick way, or option 3 to uninstall)
* Ventoy and Rufus themselves were not run; the tests use equivalent stick layouts. **Rufus ISO mode is the
  recommended medium.** If one way does not work, try another (Ventoy GRUB2 mode, Rufus ISO mode, Rufus DD mode).
* In VMware every disk is non-USB, so whole-disk mode (option 1) was only tested on internal-disk-like targets;
  on a used internal disk only after LBA 0..2047 and the last 2081 sectors were blank (T1d), as the installer
  requires.
* Only the Surface base keyboard, generic USB keyboards and Microsoft or Logitech receivers are supported in the
  installer.
* Every stick written with option 1 has the same disk and partition GUIDs. Never connect two of them at once while
  Googlebook or the installer runs.
