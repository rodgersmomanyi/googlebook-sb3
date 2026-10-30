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
* the ESP (checks V1-V31 and V12b):
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
  * no VMware-only, test-only or backup file in the native tree;
  * (wave 4) the software Gatekeeper's binary, VINTF fragment and init block (V29);
  * (wave 5) the VNNI libz patch: `/odm/vnni` equal to `inject/vnni`, gated on the exact build fingerprint (V31,
    [PERFORMANCE.md](PERFORMANCE.md#checking-it));
* every x86-64 ELF executable and shared object in the initrd cpio has bionic-compatible `PT_LOAD` segments (`p_offset == p_vaddr mod max(p_align, 4 KiB)`, check `V12b`).

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
| Touchscreen and pen | tap, swipe, draw with the Surface Pen; rest your palm while the pen hovers (touch is suppressed while the pen is in proximity) |
| Power button and volume keys | short press of the power button sleeps and wakes the screen (also with the clipboard detached); Volume Up / Down move the volume slider |
| Battery | the status bar shows one combined level for both batteries; plug/unplug the charger |
| Detach | press the detach key on the keyboard: the base should release after a moment |
| Wi-Fi / Bluetooth | connect to Wi-Fi, pair a device |
| Sound | play a video; headphone jack; microphone in the Recorder app |
| Cameras | Camera app: front and rear (needs the IPU4P firmware in your build, see [INPUTS.md](INPUTS.md#firmware-native-only); software image processing, so expect modest quality) |
| Auto-rotate / light | base attached: tilting does not rotate the screen; detach the screen and rotate it (Quick Settings **Auto-rotate** on); auto-brightness |
| NVIDIA graphics | base attached, default mode Auto: the Quick Settings tile **Graphics** shows `Intel (testing NVIDIA)`, then `NVIDIA`; start a game; detach (tile → `Unavailable (base detached)`) and reattach. Details and recovery: [NVIDIA.md](NVIDIA.md) |

The per-device checklist with the log lines to look for is the "Hardware validation checklist" in
[DEVICE-STATUS.md](DEVICE-STATUS.md).

**If a boot with the base attached hangs once Android is up**, hold Power for 10 s and boot again: the crash-loop
guard switches graphics to Intel by itself. The manual override is to press `e` on the normal entry in the boot menu,
press End and add ` gb.nouveau=0` (this boot only), and Settings → System → Graphics → **Intel only** keeps it off.

## 4. Logs

There is no serial console natively. Every boot writes logs to the stick's ESP at 40 s, 90 s, 3 min, 7 min and
15 min, even if the screen stays black: `gblog\bootN-tSECONDS.txt` (plus `audio-*` and, after an NVIDIA start, the
dGPU report). If something fails, wait 15 minutes (or at least 7), hold Power for 10 s to switch off, put the stick
into a Windows PC and read `gblog\` from its FAT32 partition. Include those files when you report a result.

Each snapshot starts with a one-screen `== summary` block for triage: the 8250 / Surface Aggregator lines, the
Aggregator input devices, the ALSA cards and DRAS lines, firmware load failures, swap, PCI functions without a
driver, the `#UD` emulator counter (`/sys/kernel/gb_vnni/summary`), the i915 "white veil" and HWUI-on-Vulkan greps,
a held system update (`gb_slotsync HOLD:`), the apgetty and fs-verity state and the ESP bring-up log. Images from
wave 5 on add: the power and volume buttons, the posture (whether Android switched the built-in keyboard off), the
sound driver mode, Wi-Fi, Bluetooth, the cameras, programs that cannot start (`linker:`), deferred driver probes and
panel self-refresh errors. They also keep a copy of the boot's first kernel messages (without SELinux `avc:` lines),
so the later snapshots still show them (`kernel log:` line). Section 7 maps symptoms to those lines.

### First-boot checklist

After the first boot (wait for the 15-minute snapshot), open the newest `gblog\boot1-t*.txt` on the stick from
Windows and check its `== summary` block:

| Check | Line in the summary | Good result |
|---|---|---|
| Keyboard base, touchpad, batteries, charger, detach (the Surface Aggregator link) | `serial 8250:`, `serial errors:`, `serial surface_serial_hub bound:` | `Serial: 8250/16550 driver, 4 ports`; `dw-apb-uart -22: 0`; `failed to setup IRQ: 0`; a device name after `bound:`. On images built before wave 5, read `serial 8250:` in the earliest file (`t40`): their later snapshots can say `no 8250 banner` once the early kernel messages have left the log buffer |
| Base keyboard and touchpad as input devices | `inputs (Surface Aggregator HID, bus 0019):` | the keyboard and the touchpad are listed |
| Power button and volume keys | `buttons:` (and `[vendor.gb.buttons]` in `== getprop`) | `MSHW0040 bound to soc_button_array`; `INT3455 pinctrl bound`; `gpio-keys inputs: 2`; `bound inputs=2` |
| Posture (built-in keyboard/touchpad enabled) | `posture:` (and `gb_hw: diag: posture:` in `== dmesg`) | `mCommittedState=OPEN` (laptop) or `TABLET` (detached); `disabled by Android: none` |
| Sound | `audio cards:`, `audio DRAS:` | the card id `sofhdadsp`; more than 0 DRAS lines naming `sofhdadsp` |
| Sound driver mode (wave 5) | `audio dsp_driver=` | `3` (SOF); `gb_audio:` ends with `card sofhdadsp = card0` and `done:`. `1` only if you chose the legacy driver (section 7, "No sound") |
| Deferred driver probes (wave 5) | `deferred probes (-517, ...)` | at most `surface_hotplug MSHW0153:00`: a driver that waited for another one and was retried. Not an error |
| Wi-Fi (wave 5) | `wifi:` and the `iwlwifi` line under it | `wlan0`; `link: wpa_supplicant ok; hostapd ok`; `loaded firmware version ...`. `FAILED: CANNOT LINK EXECUTABLE ...` means Wi-Fi cannot connect on this image: report it |
| Bluetooth (wave 5) | `bluetooth:` and the `Bluetooth: hci0:` lines under it | `hci0`; `hal=running`; `Firmware loaded in ...` or `Firmware already loaded` |
| Programs that cannot start (wave 5) | `linker:` and `-- linker failures` | `CANNOT LINK lines: 0 logcat, 0 kernel`. `dlopen failed` lines can be normal for optional libraries; note the top ones in your report |
| Firmware (Wi-Fi, Bluetooth, display, sound) | `-- firmware load failures (dmesg)`, `firmware i915 DMC:` | no `Direct firmware load for ... failed` line (note any names, for example Bluetooth `ibt-...`); a DMC line |
| Data partition fs-verity | `fs-verity:` | `ext4 feature present`; no `FS_STAT_ENABLE_VERITY_FAILED` or `Failed to run tune2fs` |
| Serial login console off | `apgetty:` | `desktop.apgetty.enable=0`, `init.svc.apgetty` not `running` |
| AVX-VNNI emulator | `-- gb_vnni` | `emulated <n> ... irqon <n> ...` with both numbers equal |
| Grey, washed-out picture | `i915 white veil:` | images from wave 5 on: `drmhwc LOG_GAMMA_LUT lines: 0` and `hwc: use_log_gamma_lut=false persistent_hdr=false force_color_mode=-1` (the composer's sRGB output; the same values are in the `== getprop` section). Section 7 if the picture is washed out |
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
boot option is added for one boot by pressing `e` on the entry in the systemd-boot menu. The editor opens with the
cursor at the start of the line: press End, type a space and the option, then press Enter (Esc leaves the editor
without booting). Typed at the start, the option runs into the line's first word, `rdinit=/gbinit`, and the boot
skips `gbinit`, the image's own start-up step: if your text is at the start, press Esc and try again.

| Symptom | Look for | Then |
|---|---|---|
| Black screen right after the systemd-boot menu (no `gblog\` file for that boot at all) | nothing is written: the kernel never ran | The firmware may hang on the kernel's `NX_COMPAT` flag: [NATIVE.md, Known risks](NATIVE.md#known-risks--open-points) (`build_internal.py --clear-nx-compat` clears it for an internal install). |
| The boot logo stays and nothing happens; no `gblog\` file for that boot | nothing is written: the boot stopped before Android | Either the firmware hangs on `NX_COMPAT` (row above), or the kernel oopsed while `gbinit` loaded i915, IPTS (touch), the camera or the Surface modules. With `panic=0` the boot stops there, and natively there is no screen console. The [debug boot](#debug-boot-kernel-messages-on-the-screen) draws the kernel's messages on the screen for one boot: the error usually names the module (`[name]` after a function in the call trace; no text at all points to `NX_COMPAT`). Then turn that module off for one boot with `GB_SKIPMOD=` ([section 8](#8-bring-up-configuration-gbgbcfg)), i915 last: without it there is no picture. |
| No base keyboard or touchpad, no battery level, charger or detach | `serial 8250:` must show `Serial: 8250/16550 driver, 4 ports`; `serial errors: dw-apb-uart -22: 0`; `serial surface_serial_hub bound:` names a device; the `== cmdline` section contains `8250.nr_uarts=4` | On the Surface Book 3 the base keyboard, touchpad, batteries, charger and detach all go through the Surface Aggregator, whose link is a UART. Our kernel is built with no 8250 ports at run time, so without `8250.nr_uarts=` that UART never registers (`-22`). Images built before wave 3 dropped the token natively: see [below](#images-built-before-wave-3-no-keyboard-base). A `-517` line (`deferred probes` in the summary) is a driver that waited for another one and was retried: not an error. |
| No keyboard or touchpad input in Android although `inputs (Surface Aggregator HID, bus 0019):` lists `Microsoft Surface 045E:09AE Keyboard`, or the device immediately goes to sleep ("no keyboard") | `posture:` in the summary (or `gb_hw: diag: posture:` in `== dmesg`): `mCommittedState=CLOSED` or `disabled by Android: Microsoft Surface 045E:09AE Keyboard` (`Enabled: false`) | lapis's `DesktopDeviceStateProvider` puts the device into `CLOSED` when the lid switch reads closed, which disables built-in input devices and forces sleep. For one boot without a shell, add `GB_PROP_1=vendor.gb.posture=open` to `gb\gb.cfg` and boot once with `gb.overlay=1` added to the boot entry ([section 8](#8-bring-up-configuration-gbgbcfg)). `gb_hw.sh posture` resolves the `OPEN` state id after `sys.boot_completed=1` and runs `cmd device_state state <id>`. To keep the override across boots, use `persist.vendor.gb.posture=open` (`reset` cancels it). While a posture override is active, closing the lid does not put the device in `CLOSED` (use the power button to sleep). |
| Power button and volume keys do nothing; a detached clipboard cannot wake from sleep | `buttons:` in the summary, `[vendor.gb.buttons]` in `== getprop`, and `gb_hw: buttons:` in `== dmesg` | `MSHW0040:00` (`soc_button_array`) needs lapis's `pinctrl-icelake` (`INT3455:00`), which loads at coldboot after `gbinit`. Images from wave 5 on rebind `MSHW0040:00` in `gb_hw.sh buttons` `on boot` (and retry once in `gb_hw.sh diag`) so two `gpio-keys` input devices appear. If `buttons:` still reports `FAILED`, include the full `gblog\` folder in your report. |
| No sound | `audio cards:` must list the card id `sofhdadsp`; `audio DRAS: logcat lines naming sofhdadsp:` above 0 | DRAS matches `<soundcard>` in `/vendor/etc/data/hal_configs/audio_default.xml` against the ALSA card **id** (`sofhdadsp`, not the name `sof-hda-dsp`). The `gblog\audio-*` report has the details ([native/audio](../native/audio/)). If `audio cards:` stays empty and the log shows SOF errors (`sof-audio-pci-intel-icl ... error`), try the older HDA driver for one boot: press `e` on the entry and change `snd_intel_dspcfg.dsp_driver=3` to `snd_intel_dspcfg.dsp_driver=1`. Speakers, the headphone jack and HDMI/DP audio then use the legacy driver; the internal microphones need SOF and do not work with it. The summary then shows `audio dsp_driver=1` and the card id `PCH`. This setting cannot go into `gb\gb.cfg`: the kernel reads it once at boot, from the command line only. Report which driver worked. |
| Hang or reboot loop with the keyboard base attached, once Android is up | the dGPU report, `vendor.gb.nouveau.result` | Add ` gb.nouveau=0` (this boot only). Settings > System > Graphics > **Intel only** keeps the NVIDIA driver off ([NVIDIA.md](NVIDIA.md#safety-rails-and-recovery)). |
| Grey, washed-out, pale picture ("white veil") | the summary's `i915 white veil:` line: `drmhwc LOG_GAMMA_LUT lines:` above 0 means the composer took its log-gamma fallback (images before wave 5 always print 0 there: count `Missing optional LOG_GAMMA_LUT` in the `== logcat` section instead); its `hwc:` values (`use_log_gamma_lut`, `persistent_hdr`, `force_color_mode`, also in the `== getprop` section); `gb_ignore_user_gamma=` | Expected cause (from the composer binary, wave 5): lapis's vendor build.prop puts the stock composer in the BT.2020 colour mode of Google's own panel (`force_color_mode=13`). The composer then adds a BT.709→BT.2020 gamut matrix to i915's colour matrix (CTM) and tags the eDP output as BT.2020, so colours look pale on this sRGB panel. Images from wave 5 on use the composer's own defaults natively (`-1`, `false`, and `use_log_gamma_lut=false`), and Night Light keeps working. On older images, boot once with the three `GB_PROP_<n>` lines of [section 8](#8-bring-up-configuration-gbgbcfg). If the picture is still pale: `GB_MODOPTS_i915=gb_ignore_user_gamma=4` drops the CTM. Night Light and the colour-correction accessibility modes then stop working, because they use the same matrix. `3` changes nothing on this image: natively the composer already clears the gamma LUT on every frame and never sets a degamma LUT. Report what you see either way. |
| Flicker, ghost images ("3D glasses"), short freezes of the picture | the summary's `i915 PSR/underrun lines:` above 0 (images from wave 5 on), or the `== dmesg` part of the log: `CPU pipe A FIFO underrun`, `Selective fetch area calculation failed`, PSR errors | These usually come from Panel Self Refresh (PSR2) or frame-buffer compression on the eDP panel. Add `GB_MODOPTS_i915=enable_psr=0 enable_fbc=0` to `gb\gb.cfg` and boot once with ` gb.overlay=1` ([section 8](#8-bring-up-configuration-gbgbcfg)). Typing `i915.enable_psr=0` in the boot editor does nothing (see below). gbOS86 needed exactly these two options on another Intel Surface panel. |
| Quick flicks on the touchpad do not move the pointer | `inputs (Surface Aggregator HID, bus 0019):` lists the touchpad | The touchpad IDC sets `gestureProp.Change_Timeout = 0.02` (Android's default holds motion for 200 ms after touchdown). Report it if flicks still fail. |
| The boot hangs or misbehaves after you changed `gb\gb.cfg` | `-- ESP bring-up (/odm/gbinit_bringup.log)` | Boot the normal entry (it never carries `gb.overlay=1`), or rename `gb\gb.cfg` from Windows. A format error refuses the whole file (nothing in it is applied); a single refused setting (unknown key, denied property, module not in `modules.load`, overlay path not allowed) is skipped with its own log line and the rest is applied. |
| Notification "System update held: Kernel mismatch" | `gb_slotsync HOLD:` | The update's kernel interface (KMI) differs from our kernel's, so its vendor modules would not load: the default stays on the old slot ([OTA.md](OTA.md#kernel-check-before-the-switch)). |
| Notification "System update not validated" (wave 5 images) | `gb_otagate: UNVALIDATED build: ...` in `== dmesg`; `[ro.vendor.gb.ota_gate]: [unvalidated]` in `== getprop`; from wave 6 also `ota gate: state=unvalidated ...` in the summary | Android was updated to a build this image was not made for. The image then skips its build-specific libz patch and keeps everything else; most things are expected to work, but nothing was tested on that build. Rebuild the boot partition for the new build on the host, or pick the previous slot's `(slot X)` entry in the boot menu. Boot options for one boot: ` gb.otagate=0` (gate off), ` gb.otagate=force` (test the "not validated" path on the validated build) ([OTA.md](OTA.md#the-ota-gate-wave-5)). |
| Wi-Fi or Bluetooth missing | `-- firmware load failures (dmesg)` (`Direct firmware load for ... failed`); from wave 5 on also `wifi:` and `bluetooth:` | Note the file name in your report; [INPUTS.md](INPUTS.md#firmware-native-only) lists what is staged and from where. `wifi: ... link: wpa_supplicant FAILED: CANNOT LINK EXECUTABLE ...` means the Wi-Fi supplicant of this image cannot start, so Wi-Fi cannot scan or connect even though the card works (`wlan0` present, firmware loaded): report it with the `gblog\` folder. |
| A feature never starts; a service keeps restarting (wave 5 images) | `linker:` and the `-- linker failures (most frequent first)` lines below it | `CANNOT LINK EXECUTABLE "<program>"` means Android cannot load that program: one of its libraries is missing or does not match. Report the line. `dlopen failed` lines from optional plugins can be normal; the summary leaves out lines from `/data` (app names). |
| Camera app shows no cameras, or `vendor.camera-provider-2-7-legacy-wrapper` keeps restarting | `camera:` line in `== summary` (`HAL load failures:` must be `0`), `CANNOT LINK lines:` (`0`), and `intel-ipu6` firmware lines in dmesg | Images built before wave 5 had misaligned `PT_LOAD` headers in the staged camera ELFs (`dlopen failed: cannot find "n34_0_Box_C"`), fixed in wave 5 (`V12b`). On hardware, verify that `intel/ipu/ipu4p_fw.bin` was staged when building the image ([INPUTS.md](INPUTS.md#firmware-native-only)). If the IPU4P kernel driver itself hangs or errors during bring-up, add `GB_SKIPMOD=intel_ipu6_isys,intel_ipu6` to `gb\gb.cfg` and boot once with ` gb.overlay=1`. |
| HWUI aborts (apps crash on start) | `HWUI Vulkan queueCount abort lines:` above 0 | HWUI landed on a Vulkan device with one queue per family. Report it with the `gblog\` folder; the supported workaround is Settings > System > Graphics > **Intel only** ([NVIDIA.md](NVIDIA.md#status-and-what-to-test-first)). |

Typing `<module>.<parameter>=` on the kernel command line (for example `i915.enable_psr=0`) does nothing for our own
modules (i915, the Surface modules, ...): `gbinit` loads them with `insmod`, which never reads the command line. The ESP bring-up config's
`GB_MODOPTS_<module>=` is the way to pass a module option for testing. The table's structure follows gbOS86's
[logs](https://gbos86.com/docs/logs) and [install](https://gbos86.com/docs/install) pages.

### Images built before wave 3: no keyboard base

Sticks, dual-boot exports and installer ISOs built before wave 3 (2026-10-26) removed `8250.nr_uarts=4` from the
native boot entries, so on the laptop the base keyboard, touchpad, batteries, charger and detach could not work with
them. Check the `options` line of `loader\entries\gb-a.conf` / `gb-b.conf` on the stick's FAT32 partition: if it has
no `8250.nr_uarts=`, the image is affected.

* **For one boot:** press `e` on the entry in the boot menu (use a USB keyboard; the base keyboard is what is
  missing), press End, append ` 8250.nr_uarts=4` and press Enter. Unlike module options (above), this one works on the command
  line: the 8250 serial driver is built into the kernel, not loaded with `insmod`.
* **For good:** rebuild the stick with the current `build_usb.py` and flash it again. An internal install made from
  an older export or ISO gets the current ESP with a new export built with `--reuse-guids` and
  `install_dualboot.ps1 -UpdateEspOnly` ([README_DUALBOOT.md](../native/dualboot/README_DUALBOOT.md), "Updating
  Googlebook's kernel/initrd later"; import an ISO install's state first), or is uninstalled and installed again
  from a current ISO. Editing the entry files by hand is not recommended: gb_slotsync writes the next slot's entry
  from the default entry, so a hand edit is easy to lose or to spread by mistake.

### Debug boot: kernel messages on the screen

Natively the kernel has no screen console, and `gblog\` is written only once Android is up. If a boot stops before
that (the boot logo stays, no `gblog\` file), make the kernel's messages visible for one boot:

1. In the boot menu, with the normal entry highlighted (the default, not `[NVIDIA test]`), press **e** before the
   countdown ends.
2. The editor opens with the cursor at the start of the line. Press **End**, then type, with the leading space:

   ```
    printk.ignore_loglevel=0 loglevel=5 earlycon=efifb keep_bootcon
   ```

3. Check that the line now ends with these words. If they are at the start, in front of `rdinit=/gbinit`, press
   **Esc** and start again: typed there, they break that first word and the boot skips `gbinit`.
4. Press **Enter**. The change is for this boot only (**Esc** leaves the editor without booting).

What the four words do:

* `earlycon=efifb` draws the kernel's messages on the firmware's frame buffer (the UEFI picture), in a small 8x16
  font. It is not a serial console: the native entries never use the UART, which belongs to the Surface Aggregator.
* `keep_bootcon` keeps it drawing after the kernel registers its own (invisible) console a second or two in, so it
  covers `gbinit`'s module loading and the rest of the boot.
* `loglevel=5` draws errors and warnings only. `printk.ignore_loglevel=0` switches off the entry's `ignore_loglevel`,
  which would draw every message. It takes effect about half a second in, so the first lines (about 110 in VMware)
  are drawn in full right after the menu: if no text appears at all, the kernel never started (most likely the
  `NX_COMPAT` hang above).
* An oops or a panic is always drawn in full, and with `panic=0` the boot stops there and the text stays. Drawing
  can be slow on the laptop, so wait until the text has not changed for a minute. Then take a sharp photo of the
  whole screen, above all the lines from `BUG:` or `Oops:` (or `Kernel panic - not syncing:`) down to
  `---[ end Kernel panic`, with the `Comm:` line (the program that was running, `busybox` while `gbinit` loads
  modules) and, if shown, `Modules linked in:`.

Tested in VMware with the wave 5 image (typed in this editor): the text appears right after the menu, Android still
starts, and a panic forced while `gbinit` loaded modules stayed on the screen with its call trace (and, as on the
laptop, left no `gblog\` file). Not yet run on the Surface Book 3:

* Once Android runs, its own error and warning lines keep scrolling (about 550 lines in the first 2.5 minutes in
  VMware). Each new line moves the whole picture up, which is slow on the laptop, so this boot is slower.
* i915 is expected to keep the firmware's picture until Android draws its first frame (inferred). After that the
  kernel still writes into the firmware's old picture memory, which the graphics driver can reuse: stray text, a
  garbled picture or a graphics hang are possible. If the boot gets as far as the Android picture, the debug boot has
  nothing to show: restart and boot normally.

To find the module, put `GB_SKIPMOD=<module>` into `gb\gb.cfg` ([section 8](#8-bring-up-configuration-gbgbcfg)) and
type ` gb.overlay=1` after the four words in the same edit. Try one module at a time, i915 last: without it there is
no picture after the boot logo. One exception in the debug boot: if the text stops, or the screen goes dark, right
after an `i915` line, skip i915 first. Without i915 nothing takes the firmware's picture over, so the text stays
visible while the other modules load (as in VMware, where i915 finds no device).

The same result without the first ~110 lines, if you would rather edit inside the line: delete `ignore_loglevel`,
change `loglevel=8` to `loglevel=5`, and type only ` earlycon=efifb keep_bootcon` at the end. Keep it a boot-menu
edit: do not write these words into the entry files on the stick (every boot would be slower, and gb_slotsync copies
the default entry to the other slot after an update).

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

Example 1, the "white veil" of section 7 on an image built before wave 5. These are the composer properties that
wave-5 images set by themselves:

```
# gb.cfg v1
GB_PROP_1=vendor.hwc.drm.force_color_mode=-1
GB_PROP_2=vendor.hwc.drm.persistent_hdr=false
GB_PROP_3=vendor.hwc.use_log_gamma_lut=false
```

Night Light keeps working with these. If the picture is still pale, the kernel switch drops i915's colour matrix
(Night Light and the colour-correction accessibility modes then stop working): `GB_MODOPTS_i915=gb_ignore_user_gamma=4`.
To compare a wave-5 image with the old behaviour, set the lapis values for one boot instead: `13`, `true`, `true`.

Example 2, flicker or ghost images (section 7):

```
# gb.cfg v1
GB_MODOPTS_i915=enable_psr=0 enable_fbc=0
```

A key may appear only once, so put every i915 option on the one `GB_MODOPTS_i915=` line, for example
`GB_MODOPTS_i915=enable_psr=0 enable_fbc=0 gb_ignore_user_gamma=4`. Report what you see either way.

Example 3, posture recovery (section 7), if the built-in keyboard is listed in the summary's `inputs` line but
disabled because Android selected `CLOSED`:

```
# gb.cfg v1
GB_PROP_1=vendor.gb.posture=open
```

Allowed values are `open`, `tablet`, `docked`, `closed` and `reset` (`off` or `none` does nothing). While an override is
active, closing the lid does not put the device into `CLOSED`; use the power button to sleep.

