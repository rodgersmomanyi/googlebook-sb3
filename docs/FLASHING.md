# Flashing and booting the USB stick image

This guide takes the image that `build_usb.py` builds from **your own** `lapis.bin` onto a USB stick and boots it
on the Surface Book 3 13.5". No image is distributed with this repository: Google's OS and vendor files reach the
stick only through your copy of the lapis image. How the image is put together: [NATIVE.md](NATIVE.md).

**Status: untested on hardware.** The partition layout, slot handling, first-boot formatting and the loading of
every Surface driver were tested in VMware (`build_usb.py --vm-test`, and a throwaway VM booting a copy of the native
image); the drivers themselves can only be proven on the laptop. VMware has no NVIDIA GPU, so the NVIDIA driver and
graphics switching ([NVIDIA.md](NVIDIA.md)) are untested too; in VMware the graphics app correctly shows
`Unavailable`. Last build tested this way: CL3B.260622.276.R4 with kernel build #15 and the NVIDIA stack (wave 3,
2026-10-26): the native tree booted from a copy of the image, with the new boot-log summary, a second boot with the
opt-in bring-up config of section 8, and the 8250 serial driver registering with `8250.nr_uarts=4` (VMware's own
16550 UART; the Surface Aggregator itself needs the laptop).

## 0. Build and check the image

```powershell
python -I -B build_usb.py                       # -> out\googlebook-sb3-usb.img + .sha256 (~27 GiB, fits a 32 GB stick)
python -I -B build_usb.py --userdata-gib 40     # for a 64 GB stick
python -I verify_image.py                       # read-only check of out\googlebook-sb3-usb.img (or pass a path)
certutil -hashfile out\googlebook-sb3-usb.img SHA256   # must equal the first word of the .sha256 sidecar
```

`build_usb.py` runs `verify_image.py` itself before it renames the finished image. A failed check leaves only
`<out>.part`. `verify_image.py` checks, without writing anything:

* the protective MBR, the primary and backup GPT headers and their entry-array CRC32s, every partition inside the
  usable range and 1 MiB aligned, no overlaps;
* all expected partitions (`EFI-SYSTEM`, `boot_a/b`, `init_boot_a/b`, `vendor_boot_a/b`, `vbmeta_a/b`,
  `pvmfw_a/b`, `misc`, `metadata`, `frp_persist`, `desktop_security_persist`, `desktop_security_storage`, `super`,
  `userdata`) and the vbmeta slot attributes (at least one slot marked successful);
* the copied partitions are identical to `lapis.bin` (full compare; `super` sampled), the blank ones are zero;
* the ESP (checks V1-V28):
  * the FAT32 layout and its free space;
  * exactly the default entry and its `[NVIDIA test]` twin, whose `androidboot.boot_part_uuid` names this image's
    `super`, with the native options (`gb.hw=native`, `efi_pstore.pstore_disable=1`, `module_blacklist=`,
    `reboot=pci`, and `8250.nr_uarts=` of 1 or more), and no `console=ttyS*` or `earlycon`;
  * the initrd and its cpio;
  * every module's vermagic and signature against the kernel;
  * nouveau and its helpers in no load list;
  * the dGPU payload and its checksum;
  * the native `gb.rc` and `build.prop` rules (apgetty off natively);
  * every component manifest's files packed unchanged, and `gbinit`, the boot-log script, the bring-up script and
    the `gb_slotsync` binary byte-identical to their sources;
  * the two NVIDIA packages;
  * the native fstab asks for `fsverity` on userdata, while the VM tree keeps the shared fstab unchanged;
  * no `gb\gb.cfg`, no `gb\overlay\` and no `gb.overlay=` in any entry (the bring-up config of section 8 is
    something you add yourself, for one boot);
  * no VMware-only, test-only or backup file in the native tree.

Pass `--krel FILE` to also check the kernel release against a file. Use `--esp FILE` for an ESP image alone. It ends
with `RESULT: OK` or the list of problems (exit code 1). Do not flash an image that fails it.

## 1. Flash it (any USB 3 stick of 32 GB or more: everything on it is erased)

1. Open **Rufus** (or balenaEtcher), select the stick, select `googlebook-sb3-usb.img`.
2. Rufus asks how to write a disk image: choose **DD Image mode**. Do not use ISO mode, and do not use any
   "quick / skip zeros" writer: the first boot formats the data partitions only because they are blank.
3. Check that the target is the USB stick, **not your internal disk**, then write.
4. Afterwards Windows may say "You need to format the disk in drive X:". **Click Cancel. Never format it**:
   Windows simply cannot read Android's partitions.

On Linux, `dd if=googlebook-sb3-usb.img of=/dev/sdX bs=4M conv=fsync status=progress` does the same (check `sdX`
twice).

## 2. Boot it on the Surface Book 3

Once, in Surface UEFI (hold **Volume Up**, press Power): Security → Secure Boot → **None/Disabled** (our loader and
kernel are unsigned; the red bar at boot is normal then), and Boot configuration → Advanced options → enable boot
from USB devices.

1. Shut down Windows completely: hold **Shift** while clicking *Shut down* (a normal shutdown with Fast Startup is
   really a hibernation).
2. Plug in the stick (USB-A on the keyboard base, or the clipboard's port through an adapter).
3. Hold **Volume Down**, press and release **Power**, keep holding Volume Down until the Surface logo goes away.
   (Alternatively: Surface UEFI → *Boot configuration* → drag *USB Storage* to the top.)
4. A boot menu appears for 4 seconds. **Googlebook OS - Surface Book 3 (USB) (slot X)** starts automatically. The
   second entry, `... [NVIDIA test]`, is only for a deliberate NVIDIA test run and is never the default
   ([NVIDIA.md](NVIDIA.md#safety-rails-and-recovery)).
5. The first boot formats the data partitions and prepares apps: allow **10 minutes**. Later boots took 1-2 minutes
   in the VM; the laptop's times are not known yet.

To go back to Windows: shut down, remove the stick, power on. The stick is built not to touch Windows or the
internal disk (section 5).

## 3. What to try

| Area | How to check |
|---|---|
| Display / brightness | picture appears; brightness keys or Settings → Display |
| Base keyboard and touchpad | type, click, two-finger scroll, quick flicks (a short fast swipe should move the pointer at once) |
| Touchscreen and pen | tap, swipe, draw with the Surface Pen |
| Battery | the status bar shows one combined level for both batteries; plug/unplug the charger |
| Detach | press the detach key on the keyboard: the base should release after a moment |
| Wi-Fi / Bluetooth | connect to Wi-Fi, pair a device |
| Sound | play a video; headphone jack; microphone in the Recorder app |
| Cameras | Camera app: front and rear (needs the IPU4P firmware in your build, see [INPUTS.md](INPUTS.md#firmware-native-only); software image processing, so expect modest quality) |
| Auto-rotate / light | detach the screen, rotate it; auto-brightness |
| NVIDIA graphics | base attached, default mode Auto: the Quick Settings tile **Graphics** shows `Intel (testing NVIDIA)`, then `NVIDIA`; start a game; detach (tile → `Unavailable (base detached)`) and reattach. Details and recovery: [NVIDIA.md](NVIDIA.md) |

The per-device checklist with the log lines to look for is the "Hardware validation checklist" in
[DEVICE-STATUS.md](DEVICE-STATUS.md).

**If a boot with the base attached hangs once Android is up**, hold Power for 10 s and boot again: the crash-loop
guard switches graphics to Intel by itself. The manual override is to press `e` on the normal entry in the boot menu
and add ` gb.nouveau=0` (this boot only), and Settings → System → Graphics → **Intel only** keeps it off.

## 4. Logs

There is no serial console natively. Every boot writes logs to the stick's ESP at 40 s, 90 s, 3 min, 7 min and
15 min, even if the screen stays black: `gblog\bootN-tSECONDS.txt` (plus `audio-*` and, after an NVIDIA start, the
dGPU report). If something fails, wait 15 minutes (or at least 7), hold Power for 10 s to switch off, put the stick
into a Windows PC and read `gblog\` from its FAT32 partition. Include those files when you report a result.

Each snapshot starts with a one-screen `== summary` block for triage: the 8250 / Surface Aggregator lines, the
Aggregator input devices, the ALSA cards and DRAS lines, firmware load failures, swap, PCI functions without a
driver, the `#UD` emulator counter (`/sys/kernel/gb_vnni/summary`), the i915 "white veil" and HWUI-on-Vulkan greps,
a held system update (`gb_slotsync HOLD:`), the apgetty and fs-verity state and the ESP bring-up log. Section 7 maps
symptoms to those lines.

### First-boot checklist

After the first boot (wait for the 15-minute snapshot), open the newest `gblog\boot1-t*.txt` on the stick from
Windows and check its `== summary` block:

| Check | Line in the summary | Good result |
|---|---|---|
| Keyboard base, touchpad, batteries, charger, detach (the Surface Aggregator link) | `serial 8250:`, `serial errors:`, `serial surface_serial_hub bound:` | `Serial: 8250/16550 driver, 4 ports`; `dw-apb-uart -22: 0`; a device name after `bound:`. Read `serial 8250:` in the earliest file (`t40`): later snapshots can say `no 8250 banner` once the early kernel messages have left the log buffer |
| Base keyboard and touchpad as input devices | `inputs (Surface Aggregator HID, bus 0019):` | the keyboard and the touchpad are listed |
| Sound | `audio cards:`, `audio DRAS:` | the card id `sofhdadsp`; more than 0 DRAS lines naming `sofhdadsp` |
| Firmware (Wi-Fi, Bluetooth, display, sound) | `-- firmware load failures (dmesg)`, `firmware i915 DMC:` | no `Direct firmware load for ... failed` line (note any names, for example Bluetooth `ibt-...`); a DMC line |
| Data partition fs-verity | `fs-verity:` | `ext4 feature present`; no `FS_STAT_ENABLE_VERITY_FAILED` or `Failed to run tune2fs` |
| Serial login console off | `apgetty:` | `desktop.apgetty.enable=0`, `init.svc.apgetty` not `running` |
| AVX-VNNI emulator | `-- gb_vnni` | `emulated <n> ... irqon <n> ...` with both numbers equal |
| Grey, washed-out picture | `i915 white veil:` | note the count; section 7 if the picture is washed out |
| Apps drawing with the wrong GPU | `HWUI Vulkan queueCount abort lines:` | `0` |
| System update held | `gb_slotsync HOLD:` | empty |
| Bring-up configuration | `-- ESP bring-up` | `absent (gb.overlay=1 not set)`, unless you used section 8 on purpose |

Then try the hardware itself (section 3), and go through the per-device "Hardware validation checklist" in
[DEVICE-STATUS.md](DEVICE-STATUS.md) as far as you can. To report a result, open an issue with the
[native boot report template](../.github/ISSUE_TEMPLATE/native-boot-report.md).

**Review before you share.** The summary prints no serial numbers, Wi-Fi network names or account names, but the full
`getprop` and `logcat` dumps below it in the same file can contain them (`ro.serialno` and other serial
properties, SSIDs, Bluetooth device names, Google account e-mail addresses). Search the files for them and redact
before you post them anywhere, and do the same with the `dgpu-*` and `audio-*` reports. The issue template
([.github/ISSUE_TEMPLATE/native-boot-report.md](../.github/ISSUE_TEMPLATE/native-boot-report.md)) repeats this.

## 5. Safety built into the image

These are design measures; like everything native, they have not been checked on the laptop yet.

* Google's Googlebook firmware updater, flashrom, futility, fwupd and the Googlebook disk installer are disabled,
  and the TPM and SPI-flash drivers are blacklisted, so that nothing in Android writes the Surface's firmware, its
  TPM or your internal disk.
* Crash logs are not written into the Surface's UEFI variable store (`efi_pstore.pstore_disable=1`).
* The stick selects only its own partitions (`androidboot.boot_part_uuid`) and is set up not to mount the internal
  disk.
* The NVIDIA driver is never part of the boot itself. It starts only after Android is up, a kernel panic on such a
  boot reboots after 30 s, and the next boot falls back to Intel. Do not delete or edit `gb\dgpu-*.erofs` on the
  stick's boot partition: it holds the NVIDIA firmware and driver files.

## 6. Dual boot (install next to Windows)

[native/dualboot/README_DUALBOOT.md](../native/dualboot/README_DUALBOOT.md) describes a Boot Camp-style installer
(`install_dualboot.ps1`, a dry run unless `-Apply`). It shrinks D:, adds the Googlebook partitions and a boot entry,
and keeps Windows the default; `Restart-ToGooglebook.ps1` restarts straight into Googlebook. It has been tested on
disk image files only. Boot the USB stick on the laptop first; once that works, rehearse the installer on a virtual
disk (VHDX) before touching the real one. The bootable installer ISO ([INSTALLER-ISO.md](INSTALLER-ISO.md)) offers
the same install from a stick, tested in VMware only.

## 7. Troubleshooting: symptom, log line, boot option

The lines named here are in the `== summary` block at the top of every `gblog\boot<N>-t<T>.txt` (section 4). A
boot option is added for one boot by pressing `e` on the entry in the systemd-boot menu.

| Symptom | Look for | Then |
|---|---|---|
| Black screen right after the systemd-boot menu (no `gblog\` file for that boot at all) | nothing is written: the kernel never ran | The firmware may hang on the kernel's `NX_COMPAT` flag: [NATIVE.md, Known risks](NATIVE.md#known-risks--open-points) (`build_internal.py --clear-nx-compat` clears it for an internal install). |
| No base keyboard or touchpad, no battery level, charger or detach | `serial 8250:` must show `Serial: 8250/16550 driver, 4 ports`; `serial errors: dw-apb-uart -22: 0`; `serial surface_serial_hub bound:` names a device; the `== cmdline` section contains `8250.nr_uarts=4` | On the Surface Book 3 the base keyboard, touchpad, batteries, charger and detach all go through the Surface Aggregator, whose link is a UART. Our kernel is built with no 8250 ports at run time, so without `8250.nr_uarts=` that UART never registers (`-22`). Images built before wave 3 dropped the token natively: see [below](#images-built-before-wave-3-no-keyboard-base). |
| No sound | `audio cards:` must list the card id `sofhdadsp`; `audio DRAS: logcat lines naming sofhdadsp:` above 0 | DRAS matches `<soundcard>` in `/vendor/etc/data/hal_configs/audio_default.xml` against the ALSA card **id** (`sofhdadsp`, not the name `sof-hda-dsp`). The `gblog\audio-*` report has the details ([native/audio](../native/audio/)). |
| Hang or reboot loop with the keyboard base attached, once Android is up | the dGPU report, `vendor.gb.nouveau.result` | Add ` gb.nouveau=0` (this boot only). Settings > System > Graphics > **Intel only** keeps the NVIDIA driver off ([NVIDIA.md](NVIDIA.md#safety-rails-and-recovery)). |
| Grey, washed-out picture ("white veil"); Night Light changes nothing | `i915 white veil: drmhwc LOG_GAMMA_LUT lines:` above 0, and the current `gb_ignore_user_gamma=` | Kernel build #15 has an opt-in switch, `i915.gb_ignore_user_gamma` (default 0 = unchanged). It is set **only** through the ESP bring-up config ([NATIVE.md](NATIVE.md#bring-up-configuration-on-the-esp-gbgbcfg)): `GB_MODOPTS_i915=gb_ignore_user_gamma=3` in `gb\gb.cfg`, then boot once with ` gb.overlay=1`. `3` makes the gamma and degamma tables linear (Night Light stops working; the CTM-based colour-correction accessibility modes keep working); `7` also drops the CTM (gbOS86's fix). Whether the SB3 panel needs it at all is not known yet. |
| Quick flicks on the touchpad do not move the pointer | `inputs (Surface Aggregator HID, bus 0019):` lists the touchpad | The touchpad IDC sets `gestureProp.Change_Timeout = 0.02` (Android's default holds motion for 200 ms after touchdown). Report it if flicks still fail. |
| The boot hangs or misbehaves after you changed `gb\gb.cfg` | `-- ESP bring-up (/odm/gbinit_bringup.log)` | Boot the normal entry (it never carries `gb.overlay=1`), or rename `gb\gb.cfg` from Windows. A format error refuses the whole file (nothing in it is applied); a single refused setting (unknown key, denied property, module not in `modules.load`, overlay path not allowed) is skipped with its own log line and the rest is applied. |
| Notification "System update held: Kernel mismatch" | `gb_slotsync HOLD:` | The update's kernel interface (KMI) differs from our kernel's, so its vendor modules would not load: the default stays on the old slot ([OTA.md](OTA.md#kernel-check-before-the-switch)). |
| Wi-Fi or Bluetooth missing | `-- firmware load failures (dmesg)` (`Direct firmware load for ... failed`) | Note the file name in your report; [INPUTS.md](INPUTS.md#firmware-native-only) lists what is staged and from where. |
| HWUI aborts (apps crash on start) | `HWUI Vulkan queueCount abort lines:` above 0 | HWUI landed on a Vulkan device with one queue per family. Report it with the `gblog\` folder; the supported workaround is Settings > System > Graphics > **Intel only** ([NVIDIA.md](NVIDIA.md#status-and-what-to-test-first)). |

Typing `<module>.<parameter>=` on the kernel command line does nothing for our own modules (i915, the Surface
modules, ...): `gbinit` loads them with `insmod`, which never reads the command line. The ESP bring-up config's
`GB_MODOPTS_<module>=` is the way to pass a module option for testing. The table's structure follows gbOS86's
[logs](https://gbos86.com/docs/logs) and [install](https://gbos86.com/docs/install) pages.

### Images built before wave 3: no keyboard base

Sticks, dual-boot exports and installer ISOs built before wave 3 (2026-10-26) removed `8250.nr_uarts=4` from the
native boot entries, so on the laptop the base keyboard, touchpad, batteries, charger and detach could not work with
them. Check the `options` line of `loader\entries\gb-a.conf` / `gb-b.conf` on the stick's FAT32 partition: if it has
no `8250.nr_uarts=`, the image is affected.

* **For one boot:** press `e` on the entry in the boot menu (use a USB keyboard; the base keyboard is what is
  missing), append ` 8250.nr_uarts=4` and press Enter. Unlike module options (above), this one works on the command
  line: the 8250 serial driver is built into the kernel, not loaded with `insmod`.
* **For good:** rebuild the stick with the current `build_usb.py` and flash it again. An internal install made from
  an older export or ISO gets the current ESP with a new export built with `--reuse-guids` and
  `install_dualboot.ps1 -UpdateEspOnly` ([README_DUALBOOT.md](../native/dualboot/README_DUALBOOT.md), "Updating
  Googlebook's kernel/initrd later"; import an ISO install's state first), or is uninstalled and installed again
  from a current ISO. Editing the entry files by hand is not recommended: gb_slotsync writes the next slot's entry
  from the default entry, so a hand edit is easy to lose or to spread by mistake.

## 8. Bring-up configuration (`gb\gb.cfg`)

For hardware bring-up without a new image, a small file on the stick's boot partition can add module options,
Android properties and input or audio configuration files, for **one boot**. It is off unless you ask for it. The
full rules (allowed keys, sizes, refused paths, which ESP is read) are in
[NATIVE.md](NATIVE.md#bring-up-configuration-on-the-esp-gbgbcfg).

1. In Windows, create `gb\gb.cfg` on the stick's FAT32 partition (the `gb` folder already exists). Its first line
   must be exactly `# gb.cfg v1`; then one `KEY=VALUE` per line.
2. In the 4-second boot menu press an arrow key, highlight the normal entry, press **e**, add a space and
   `gb.overlay=1` at the end of the line, press **Enter**. Only that boot reads the file.
3. What was applied or refused is in the `-- ESP bring-up` part of the log summary (`/odm/gbinit_bringup.log`).

If such a boot misbehaves, boot the normal entry again (it never reads the file), or rename `gb\gb.cfg` from
Windows. Never add `gb.overlay=1` to an entry file: gb_slotsync copies the default entry's options into the next
slot's entry.

Example, the "white veil" switch of section 7 (only if the picture looks washed out and Night Light changes
nothing):

```
# gb.cfg v1
GB_MODOPTS_i915=gb_ignore_user_gamma=3
```

`3` makes the gamma and degamma tables linear: Night Light stops working, the CTM-based accessibility colour modes
keep working. Whether the Surface Book 3's panel needs it at all is not known yet; report what you see either way.

