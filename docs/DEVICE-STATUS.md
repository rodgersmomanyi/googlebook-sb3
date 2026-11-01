# Surface Book 3 device status (native mode)

Every device of the Surface Book 3 13.5" (i7-1065G7) and what drives it when Googlebook OS ("lapis") boots natively
(`gb.hw=native`). This is the hardware audit from `native/hw-gap-audit` (2026-10-24, kernel build #11), updated for
**kernel build #13**, the **NVIDIA graphics switching** stack with **kernel build #14** (native images, 2026-10-25),
the **wave 3** fixes with **kernel build #15** (native images, 2026-10-26), and the native tree as `build_esp.py`
assembles it from `inject/native/components/*.json`. The audit covered every
device in the Windows inventory: the live PnP list, the HID hardware IDs and
the ACPI tables of a Surface Book 3 (read from `HKLM\HARDWARE\ACPI`, decompiled with iasl; the dumps are not part
of this repository).

**The first native boot happened on 2026-10-31** (the wave 8 image; results per item in
[First real-hardware results](#first-real-hardware-results-2026-10-31-the-wave-8-usb-image)): Android started, and the
display, touch and pen, Wi-Fi, Bluetooth, the sound card, the buttons and the NVIDIA power saving worked; the keyboard
base, touchpad, batteries, charger and detach did not (wave 9 fixes them in the image to test next, `e0825ee1…`).
Every row below that the table does not mark otherwise is still untested on hardware. The VMware VM cannot see this
hardware; a VMware boot of the native tree (`GB_NATIVE_TEST=1`, see [NATIVE.md](NATIVE.md)) only proves that the
modules load and the services start. "Ready" means the driver is built (or ships with lapis), the firmware is staged and the configuration exists.
It still needs a native boot to confirm. Per-subsystem detail: [DRIVERS.md](DRIVERS.md).

Status key:

| Status | Meaning |
|---|---|
| **Expected** | Expected to work with no extra file: built into our kernel, or a lapis module that binds by itself. Untested on hardware, like every row. |
| **Ready** | Module, firmware and config are all in place. Untested on hardware. |
| **New here** | A gap the audit filled with a file of its own (`native/hw-gap-audit`). |
| **Partial** | Expected to work only with a known limitation (listed). |
| **Gap** | Does not work, with the reason given. |
| **Off by design** | Must stay unused: TPM, BIOS flash, firmware updaters. |

Tags: **[V]** verified against the ACPI tables, the source or the inventory. **[I]** inferred.

## First real-hardware results (2026-10-31, the wave 8 USB image)

The first native boot: the owner flashed the wave 8 USB image (`35d6043d…`, kernel build #16) and started it three
times on the Surface Book 3 13.5" with the keyboard base attached. The boot-log analyser
([native/hwtest](../native/hwtest/), `analyze_gblog.py`) on the third start: **29 PASS, 10 FAIL, 2 UNKNOWN** of 41
items; Android reached `boot_completed` after 42.8 s. Personal values from those logs (serial numbers, network and
account names) are not reproduced here. The last column says what wave 9 changed; **the image to test next is the
wave 9 USB image `e0825ee1…`** (flash it with Rufus in DD mode; the installer ISO still carries the wave 8 system).

| # | Item (row; checklist) | On the laptop (wave 8 image) | Wave 9 |
|---|---|---|---|
| 1 | Kernel build #16 | PASS | - |
| 2 | Kernel command line (`8250.nr_uarts`, native tokens) | PASS | - |
| 3 | `gbinit` module loading | PASS: 63 modules, none failed | the IR camera's `ov7251` is no longer loaded (row 29) |
| 4 | 8250 serial driver (row 12; 19) | FAIL: the 8250 ports registered (4 ports), but the item counted the Surface Aggregator's own IRQ failure | the analyser judges that line under item 5, where it belongs |
| 5 | Surface Aggregator (rows 12, 18, 42; 19) | FAIL: the SAM answered (`SAM firmware version: 10.600.139`), but its first probe failed for good at 1.49 s (`error -EINVAL: failed to setup IRQ`, -22): on kernel build #16 it runs before lapis's `pinctrl-icelake` provides the GPIO controller (INT3455) of its wake-up interrupt | `gb_hw.sh sam` (service `gb_hw_sam`, `on boot`) binds it again once INT3455 is bound and waits for its devices (item 29); a kernel fix (patch 0114) waits for the next kernel build. Unverified on the laptop |
| 6-10 | SAM registry and hub, base keyboard 045E:09AE, touchpad 045E:09AF, batteries BAT1/BAT2 + charger ADP1, clipboard detach (rows 18, 19, 25, 42; 1, 2, 16) | FAIL: no SAM client device at all (the cause of item 5): no keyboard base, touchpad, battery level, charger or detach | the same re-bind; also the graphics switch, which needs the detach device, stayed on Intel (item 28) |
| 11 | Power button and volume keys (row 23; 25) | PASS: two `gpio-keys` devices (the wave 5 re-bind) | - |
| 12 | Posture (rows 18, 24, 25; 1) | PASS: `OPEN`, the built-in input enabled | - |
| 13 | CPU power limits (row 1; 3) | PASS: the firmware's limits kept (not locked) | - |
| 14 | Suspend and resume (row 46; 11) | UNKNOWN: no suspend in the logged time | close the lid once next time |
| 15, 16 | Intel GPU (i915 + DMC firmware), panel, backlight (rows 14, 15; 4, 21) | PASS | - |
| 17 | Touchscreen and pen (row 21) | PASS: IPTS touchscreen, iptsd's touch and stylus devices | - |
| 18, 19 | Wi-Fi AX201 and Bluetooth 8087:0026 (rows 37, 38; 23) | PASS: firmware loaded, controller started | - |
| 20 | Sound card (rows 39-41; 22) | PASS: SOF card `sofhdadsp` | - |
| 21 | DRAS audio HAL (row 39; 22) | UNKNOWN: its start line was not in the saved log window | play sound and report it (item 22) |
| 22 | Sensors (rows 27-28; 10) | FAIL in the third start only: the sensor hub's firmware did not start at power-on (`HBM haven't started`), so there was no accelerometer in that start; the first two starts had every sensor | `gb_hw.sh ish` (service `gb_hw_ish`, early-boot) resets and binds the hub once; whether that revives it is unproven (item 29: note cold start or restart) |
| 23 | Rotation per posture (row 27; 10) | PASS: `ok: laptop posture, rotation locked`; the Auto-rotate default came from our settings overlay (no first-boot write) | - |
| 24 | Cameras (row 29; 9) | FAIL: the IR camera's sensor (OV7251) never answers (`-121`); the rear camera's focus motor (DW9719) logged a write error in two starts | the IR driver is no longer loaded (Android never used it); DW9719: test autofocus on the rear camera |
| 25-28 | NVIDIA dGPU: D3cold when idle, nouveau (Auto mode), dGPU report, graphics switching state (row 17; 11, 14, 15, 17, 18) | PASS: D3cold seen, nouveau bound and healthy, Intel active (no detach device yet) | - |
| 29 | Lock-screen Gatekeeper | PASS: registered and running | - |
| 30-34 | zram, the VNNI emulator counter, the libz patch binds, fs-verity on /data, the serial login console off (24) | PASS | - |
| 35-38 | No held update, the OTA gate (the booted build is the one the image was made for), the bring-up config unused, no missing firmware (23, 24) | PASS | - |
| 39 | Crashes | FAIL: only lapis's `powertool set-soc-slider balanced 1 4` exits 1 at every start (Ice Lake has no SoC slider; it writes nothing) | harmless; the analyser no longer counts it when the log proves the refusal |
| 40 | SELinux: no enforced denial | PASS | - |
| 41 | Boot completed | PASS: after 42.8 s | - |

Also seen: lapis's `vpd` tool ran `/vendor/bin/flashrom` at boot; what ran is our refusal stub (the kernel has no path
to the firmware flash either). Wave 9 also replaces `elogtool` and `cbfstool`, the last two lapis programs that link
the flash library, by refusal stubs (row 10). The wave 9 analyser on the same logs gives 31 PASS, 8 FAIL; the SAM,
sensor-hub and camera fixes act only in the wave 9 image, which has not run on the laptop yet.

## Changes since the audit (kernel build #13)

* **Keyboard (row 18):** `hid-surface` is built (`CONFIG_HID_SURFACE=m`) and loaded by `gbinit`: Ready.
* **Cameras (row 29):** the IPU4P port of the ipu6 driver (`native/camera-kernel`) is built with the sensor drivers
  (`ov5693`, `ov8865`, `ov7251`), the `dw9719` focus motor, `ipu-bridge`, `intel_skl_int3472_*` and `v4l2-cci`, all
  loaded by `gbinit` before lapis's own copies (which do not know 8086:8A19). The camera HAL is libcamera's simple
  pipeline with the software ISP (`native/camera-userspace`, `camera.sb3.so`, `ro.hardware.camera=sb3`). It needs
  the IPU4P firmware, which is Intel's and must come from your own Windows driver package
  ([INPUTS.md](INPUTS.md#firmware-native-only)). Untested on hardware; expect modest image quality (CPU debayer,
  uncalibrated tuning).
* **Sensors (rows 27-28):** the two sensor patches are in the kernel build and the sensors component is in the
  default native tree: Ready.
* **Row 12 (LPSS / pinctrl / I2C):** build #13 compiles our own `pinctrl-icelake`, `intel-lpss(-pci)` and
  `i2c-designware` (from `camera.config`), but `stage_kernel.py` never loads our copies of lapis's first-stage
  modules (`mfd-core`, `pinctrl-intel`, `intel-lpss`, `intel-lpss-pci`), nor `pinctrl-icelake` / `i2c-designware`.
  The VMware test of the native tree showed why: with our copies preloaded, lapis's first-stage
  `pinctrl-intel-platform` was refused against their protected exports, and a module failure in first-stage init
  stops the boot. `stage_kernel.py` now refuses such a load list. The cameras only need the buses, which lapis's own
  modules provide.
* **NVIDIA dGPU (row 17), 2026-10-25:** graphics switching. With the base attached, nouveau now loads
  automatically after boot (graphics mode **Auto**, the default), with the safety rails of [NVIDIA.md](NVIDIA.md):
  the crash-loop guard, the `gb.nouveau=0` kill switch, `kernel.panic=30`, refcnt-gated detach. Games and the apps you
  choose render on NVK through the per-app driver package. The system UI always stays on Intel. A self-test gates
  NVIDIA app graphics, and a compute service runs llama.cpp on NVK. The firmware, modules and NVK ICD moved into the
  `gb/dgpu-<krel>.erofs` payload on the boot partition. Native images run kernel build #14 (nouveau backports and
  hardening). The v1 opt-in property `persist.vendor.gb.nouveau` is retired.
* **Audit files in the default build:** `gb_hw.sh` (CPU power limits), the keyboard/touchpad IDCs, the pen key
  layouts and `excluded-input-devices.xml` are now in the native tree (`inject/native/components/hw.json`).
* **Wave 3, 2026-10-26 (kernel build #15; findings from gbOS86, credited in the README):**
  * Row 12: the native command line keeps `8250.nr_uarts=4`. Without it the Surface Aggregator's UART never
    registered, so every earlier image would have had no base keyboard, touchpad, batteries, charger or detach
    ([FLASHING.md](FLASHING.md#images-built-before-wave-3-no-keyboard-base) has the one-boot workaround).
  * Row 39: DRAS's `<soundcard>` is now the ALSA card id `sofhdadsp`. Row 19: the touchpad IDC sets
    `gestureProp.Change_Timeout = 0.02`.
  * Row 14: kernel build #15 adds the opt-in "white veil" switch `i915.gb_ignore_user_gamma` (off). Build #15 also
    reworks the `#UD` AVX-VNNI emulator ([kernel/K15.md](../kernel/K15.md)); nouveau is unchanged from #14.
  * Every boot log starts with a one-screen `== summary` ([FLASHING.md](FLASHING.md#first-boot-checklist)); the
    serial login console (`apgetty`) is off natively (from wave 6 it is stopped at `on init`,
    about 0.1 s after lapis starts it; earlier images stop it at `on boot`, about 20 s later); the native fstab asks for fs-verity on userdata; the opt-in
    `gb\gb.cfg` bring-up config can change module options, properties and input/audio files for one boot.
  * Rows 37-38: `native/nouveau-kernel/tools/fw_coverage.py` pins every staged firmware file to its package
    ([INPUTS.md](INPUTS.md#firmware-native-only)).
* **Wave 5, 2026-10-29/30 (native readiness fixes; nothing has run on the hardware yet):**
  * Row 14: the native `/odm/etc/build.prop` now sets the stock composer's own colour defaults
    (`vendor.hwc.drm.force_color_mode=-1`, `persistent_hdr=false`, `use_log_gamma_lut=false`) in place of lapis's
    values for Google's BT.2020 panel. The composer binary shows that those values put a BT.709→BT.2020 gamut matrix
    into i915's CTM, the expected "white veil". Night Light keeps working. Older images: FLASHING.md section 8.
  * Row 15: the flicker advice now uses the bring-up config (`GB_MODOPTS_i915=enable_psr=0 enable_fbc=0`).
  * Row 23 (A-H1): `gb_hw.sh buttons` (`gb_hw_buttons`, started `on boot`) waits for lapis's `pinctrl-icelake` to bind
    the `INT3455:00` GPIO controller, then binds `MSHW0040:00` to `soc_button_array` and checks that the two `gpio-keys`
    input devices appear (`gbinit` loads `soc_button_array` before `pinctrl-icelake` exists; the driver ignores
    `-EPROBE_DEFER` and fails with `-ENODEV`, which the kernel never retries). `gb_hw.sh diag` retries once more if
    still unbound and logs the state to `dmesg` and `vendor.gb.buttons`.
  * Rows 18, 24, 25 (A-M1): `gb_hw.sh diag` logs lapis's `DeviceStateManager` posture (`mCommittedState`, `mBaseState`,
    `mOverrideState`) and the built-in keyboard/touchpad `Enabled:` lines from `dumpsys input` (with external/Bluetooth
    device names filtered out). `gb_hw.sh posture` (`gb_hw_posture`) adds an opt-in recovery override, off by default:
    `vendor.gb.posture=open|tablet|docked|closed|reset` (one boot via `gb\gb.cfg` `GB_PROP_1=vendor.gb.posture=open`
    with `gb.overlay=1`) or `persist.vendor.gb.posture` ([FLASHING.md](FLASHING.md#7-troubleshooting-symptom-log-line-boot-option)).
  * Row 21 (B-5): `native/iptsd/etc/iptsd/iptsd.conf` sets `[Touchscreen] DisableOnStylus = true` so palm contact on the
    touchscreen is suppressed while the Surface Pen hovers in proximity.
  * Rows 27, 28, 46 (A-L2, A-L3, A-L4): wake sources in row 46 exclude `surface_aggregator` (its wake IRQ is disabled
    by default upstream to avoid spurious wakeups, so keyboard keys do not wake); rows 27, 28 and 46 note that
    upstream linux-surface marks Surface Book 3 sensors and S0ix as `?`; row 27 describes the auto-rotate behaviour
    per posture; `gb_hw.sh diag` logs the ACPI LPIT S0ix residency counters
    (`low_power_idle_system_residency_us`, `low_power_idle_cpu_residency_us`).
  * **Row 29 (Cameras, C-1):** `native/camera-userspace/build-scripts/stage.sh` now runs `llvm-strip --strip-unneeded`
    **before** `patchelf` (with `--page-size` set to the file's largest `PT_LOAD` `p_align`) and checks `PT_LOAD`
    page-congruence (`p_offset == p_vaddr mod max(p_align, 4096)`) on every staged ELF before re-signing
    `ipa_soft_simple.so`. In wave 3 `llvm-strip` ran after `patchelf` and shifted the `PT_LOAD` segment that `patchelf`
    added for the grown `.dynstr` off its `p_vaddr` modulo `p_align`, so bionic refused `camera.sb3.so` (`dlopen failed:
    cannot find "n34_0_Box_C" from verneed[0]`) and the camera provider restarted every 5 s. `build_esp.py` and
    `verify_image.py` (`V12b`) now enforce `PT_LOAD` congruence on every x86-64 ELF in the image.
  * Boot-log summary (wave 5, B-1, C-3, A-L1, C-4): the white-veil count is real now (it searched for a string the
    composer never logs, so it was always 0) and the line shows the composer's colour switches; the first snapshot
    keeps a copy of the boot's first kernel messages without SELinux `avc:` lines, so later snapshots no longer lose
    the 8250, Surface Aggregator, pinctrl and firmware lines; new lines for deferred probes (`-517` is not an error),
    the power/volume buttons, posture, the sound driver mode (`dsp_driver`), panel self-refresh errors, Wi-Fi (with a
    start test of `wpa_supplicant` and `hostapd`), Bluetooth, the cameras (with a start test of `lcam-probe`) and
    programs that cannot load (`CANNOT LINK`). The hwtest analyzer reads them.
  * Row 37 (C-2): the native tree ships the KeyMint graft's Android 16 libraries under private sonames
    (`lib<x>_gbkm16.so`), so lapis's own `/vendor/lib64/libcrypto.so` stays in place and `libssl.so` users
    (`wpa_supplicant`, `hostapd`) link again; `build_esp.py` refuses a native tree in which a vendor program
    would no longer link (`link_closure.py`).
  * The software Gatekeeper (lock-screen PIN, wave 4), the VNNI libz patch ([PERFORMANCE.md](PERFORMANCE.md))
    and the OTA gate ([OTA.md](OTA.md#the-ota-gate-wave-5)) are in the native images from wave 5 on.
  * [Readiness audit](#readiness-audit-wave-5-what-the-image-proves-and-what-needs-the-laptop) below: what the
    wave 5 audits verified in the image, and what only the laptop can show.
* **Wave 6, 2026-10-30 (hardware-independent backlog; nothing has run on the hardware yet):**
  * Row 17: only a TU117 (the 13.5" model) is ever probed. On a 15" Surface Book 3 (TU116/TU106; the image has no
    firmware for them) `gb_nouveau.sh` stops before any write with `vendor.gb.nouveau.result=refused:model` and one
    log line; the dGPU stays driverless (expected to sleep in D3cold, as in Intel mode; not yet seen on hardware),
    there is no "NVIDIA GPU problem" notification, gb_power does not retry it, and the Graphics tile and page say
    `Intel (NVIDIA not supported)`. An unreadable device id is not gated.
  * Row 27 (A-L3): the laptop posture is rotation-locked by lapis's own WindowManager
    (`DisplayRotation.LaptopController`, enabled by our `config_supportAutoRotation=true` overlay on a PC-type device),
    the tablet posture follows the Auto-rotate setting. The per-device-state rotation lock of AOSP
    (`config_perDeviceStateRotationLockDefaults`) is **not** used: on lapis 276 it is only enabled for foldables.
    `gb_sensors.sh` now logs `GB_SENSORS: rotation: <trigger>: device_state=... fixed_to_user=... => ok|MISMATCH|WARN|info`
    after boot and after every DTX mode change (read-only, bounded). `GbSb3SettingsOverlay.apk` makes Auto-rotate
    default to on for every user (lapis's SettingsProvider default is off).
  * Notifications reach the person. lapis runs in headless system user mode: user 0 only shows the login screen,
    and you use user 10. Every notice of ours ("System update not validated", "System update held", the NVIDIA
    notices from `gb_notify`) is posted once for the signed-in user in front of the screen (wave 9; waves 6-8 posted
    it for all users), and held until the sign-in when the login screen is showing; before wave 6 these notices went to user 0 and were never shown. GbGpu's state
    updates, its notification permission and its Quick Settings tile now follow the signed-in user, and the native
    OTA gate's queued notice is readable by `gb_notify` (it was written 0600 and dropped). Shown in a VMware boot
    of the VM tree with the forced OTA gate: the update notice, a `gb_notify` notice and a GbGpu state change all
    appeared in user 10's notification panel. The native paths (GbGpu as a system app, the real gb_power) need the
    laptop (item 27).
  * VNNI patch (PERFORMANCE.md): the two patched `libz.so` copies sit on their own tmpfs, which init remounts
    read-only as a whole after the binds, so no mount of them accepts a write in any mount namespace (wave 5 left the
    propagated copies writable); the image build refuses a patch folder for another build or slot.
  * OTA gate (OTA.md): `verify_image.py` check V32 checks the gate inside an image; the boot-log summary has an
    `ota gate:` line and the analyser an `ota_gate` item; an unvalidated build logs the stock sha256 of each file our
    binds cover; `build_usb.py --vm-test --vm-test-otagate` makes a forced test boot (booted in VMware).
  * Lock-screen PIN safety (GATEKEEPER.md): `build_esp.py` and the ESP rollback refuse a change of the PIN path;
    frozen Gatekeeper test vectors; `build_usb.py` / `build_internal.py` refuse while the VMware VM runs. The serial
    login console (`apgetty`) is stopped at `on init`, within about 0.1 s of lapis starting it (VMware VM-1).
  * FLASHING.md section 7: a debug boot that draws the kernel's messages on the screen (`earlycon=efifb`) for a
    boot that stops before Android, tested in VMware; and the boot-menu editor opens with the cursor at the start of
    the line (press End before typing an option; FLASHING, NATIVE, OTA and PERFORMANCE now say so).
  * Tested in VMware on the rebuilt images: VM-1 on the USB image (every wave 2, 3, 5 and 6 check that VMware can show; the hardware-test analyser 17 PASS, 0 FAIL, 23 UNKNOWN), a forced OTA-gate boot of the VM build (the update notice, a `gb_notify` notice and a GbGpu state change in user 10's panel), and on the installer ISO T0, T1a/T1b (whole disk), T2b/T2c/T2h (refusals), the dual-boot groups G1-G4 (T3 install with its refusal cases, T4/T4b uninstall, T9 off-disk GPT copy) and T8 (the Windows scripts): 402 checks, 0 failed. The other 22 ISO tests passed the same day on the build before the notification fix, which has the same installer.
* **Wave 7, 2026-10-30 (the last hardware-independent items; nothing has run on the hardware yet):**
  * Settings reach your user. lapis runs in headless system user mode: user 0 only shows the login screen and you use
    user 10. Our per-user settings (display density, the launcher as HOME, "setup complete", the VM's screen
    timeout, the Auto-rotate default) used to be written for whichever user was current right after boot, i.e. user 0,
    or for you only if you signed in within a minute. They are now set for every full user when it is unlocked, at any
    time during the boot: density, HOME and "setup complete" at every boot, the one-time values only once per user
    (shortly after its first unlock), so your own later changes to them stay. The Graphics app's notification
    permission is granted once per user, so turning its notifications off now sticks. Tested in VMware on a fresh
    first boot (the new user 10), a late sign-in and a second user created later; user 0's settings did not change.
  * The update notice gets louder ([OTA.md](OTA.md#the-ota-gate-wave-5)): the image counts the boots of the same
    unvalidated build, and from the third one the notice reads "System update still not validated" and names the
    count and both remedies. The automatic fallback to the previous slot is designed but not built.
  * No silently lost notices (native): `cmd notification post` exits 0 even when it posted nothing; `gb_notify` now
    reads its output and posts once more in a form the build accepts. The VM build's direct post does not do this yet.
  * Row 27, item 10: the first-boot Auto-rotate write now runs only when the settings overlay is not applied.
  * Audit follow-ups ([GATEKEEPER.md](GATEKEEPER.md), [VMWARE.md](VMWARE.md), [NATIVE.md](NATIVE.md)): a new
    Gatekeeper build needs its frozen-vector results from the Android build on lapis's bionic and from the reference
    check; the VMware ESP rollback first saves what it overwrites; a dual-boot `-UpdateEspOnly` refuses an ESP that
    would change the lock-screen PIN path; `verify_image.py` V27 also checks the early `stop apgetty`.
  * Tested in VMware: a rehearsal of the VM build (a fresh first boot, 33 of 33 checks; three boots of a forced
    unvalidated update, 17 of 17 each, with the louder notice at the third in user 10's panel; a validated boot after
    them, 10 of 10), VM-1 on the rebuilt USB image (every wave 2, 3, 5, 6 and 7 check that VMware can show; the
    hardware-test analyser 17 PASS, 0 FAIL, 23 UNKNOWN), and on the installer ISO T0, T1a/T1b, the dual-boot groups
    G1-G4 and T8 (378 checks, 0 failed). The long-running VM now runs the wave 7 VM build (after a verified backup of
    its boot partition; its lock-screen PIN path is unchanged). On the native image the settings and notice changes act
    after Android has started, which the native tree never reaches in VMware: checklist item 28.
* **Wave 8, 2026-10-31 (kernel build #16, updates into slot A, your own settings kept; nothing has run on the
  hardware yet):**
  * Kernel build #16 ([kernel/K16.md](../kernel/K16.md)) in both trees: build #15 plus two patches. The VNNI
    emulator's counter prints task names escaped, so a program can no longer add lines to the boot-log summary,
    and the Realtek SD-card reader drivers (USB vendor class and PCIe) are built and loaded by modalias in the
    native image, so the base's reader works whichever type it is (row 34, item 6). The KMI symbol list is
    unchanged; lapis's vendor modules load as before. The emulator's known-answer test passed again on build #16
    (9,235 cases).
  * Updates into slot A now start ([OTA.md](OTA.md#gb_slotsync)). systemd-boot matches the `loader.conf` default
    against the entry's file name including `.conf`; the line `default gb-<x>` written so far matched no entry, so
    the loader always started slot B's entry. The images, `gb_slotsync` (it rewrites an older ESP's line once),
    `verify_image` (check V33) and the dual-boot `-UpdateEspOnly` check now use `default gb-<x>.conf`; a slot you
    pick in the boot menu stays the default (pick the older slot only while the newer build has not reached the
    lock screen).
  * Your own settings stay. `gb_ui.sh` used to put back its display density and the stock launcher at every boot,
    and its one-time values (the VM's screen timeout, the native Auto-rotate default) replaced whatever your user
    had. Now the density changes only while it is still the value it set itself (it follows a display change, never
    your Display size), the launcher is set up once (a launcher you pick stays), and a one-time value is written
    only when nobody chose the current one (`dumpsys settings` shows who wrote it: the system's default and our own
    values are replaced, a value from Settings, the quick settings, an app or `adb` is kept and logged) and it did
    not change since your first sign-in (a toggle such as Auto-rotate is written by the system for you). Tested in
    VMware on a fresh first boot, a second user whose screen timeout was changed right after the first sign-in, and
    a warm boot after a Display size change. `verify_image.py` now also checks the image's `gb_ui.sh` (V12).
  * The VM build's update notice no longer depends on `cmd notification post`'s exit status: like `gb_notify`, its
    direct post reads the output and falls back once (without the text style, without `-u` for the login screen
    only, or once per signed-in user), logging what happened.
  * Smaller items: item 10's one-time Auto-rotate flag is also set when the overlay applied; `build_esp.py` writes
    the VM's `lapis.bin` only with `GB_EXPECT_ESP_SHA256` = the sha256 of the preview that was checked
    ([VMWARE.md](VMWARE.md)); the VMware ESP rollback (`gb_vmbackup.py restore --esp-only`) refuses a backup of
    the other A/B slot, because after a Google update switched the VM's slot every older ESP backup is the wrong
    rollback ([GATEKEEPER.md](GATEKEEPER.md#upgrading-an-existing-vm)).
  * Tested in VMware: a rehearsal of the VM build on kernel build #16 (a fresh first boot, 42 of 42 checks; a forced
    unvalidated update, 25 of 25, with the notice in user 10's panel and your own Display size and screen timeout
    kept; the emulator's known-answer test and a hostile-task-name test, 15 of 15; a validated boot, 17 of 17; a
    switch to slot A, 11 of 11: `gb_slotsync` wrote `default gb-a.conf` and the next start ran slot A), VM-1 on the
    rebuilt USB image (every wave 2, 3, 5, 6, 7 and 8 check that VMware can show; the hardware-test analyser 17
    PASS, 0 FAIL, 23 UNKNOWN), and on the installer ISO T0, T1a/T1b, the dual-boot groups G1-G4 and T8 (378 checks,
    0 failed). The long-running VM now runs the wave 8 VM build with kernel build #16 (after a verified backup of
    its boot partition; its lock-screen PIN path is unchanged). The SD reader's type and its loading, the native
    `gb_ui.sh` branch and the rotation flag can only be checked on the laptop: items 6, 10, 24 and 28.
* **Wave 9, 2026-10-31/11-01 (the first native boot's fixes and the remaining hardware-independent items; the
  wave 9 image has not run on the hardware yet):**
  * The first native boot ([First real-hardware results](#first-real-hardware-results-2026-10-31-the-wave-8-usb-image)):
    the keyboard base, touchpad, batteries, charger and detach failed because the Surface Aggregator's first probe
    fails for good on kernel build #16 (row 12). `gb_hw.sh sam` (service `gb_hw_sam`, `on boot`) binds it again once
    lapis's GPIO driver is ready and waits for its devices; `gb_hw.sh ish` (service `gb_hw_ish`, early-boot) resets
    the sensor hub once when its firmware did not start; the IR camera's driver is no longer loaded; `elogtool` and
    `cbfstool` are refusal stubs; the boot-log summary gains `sam:` and `ish:` lines (item 29) and the analyser
    names these causes. A kernel fix for the SAM probe (patch 0114) waits for the next kernel build.
  * Notices go to the user in front of the screen and wait for the sign-in; the VM build shows "System update held"
    too; GbGpu sets itself up whenever a user comes to the front and shows a real app icon; the "still not
    validated" notice now says "Previous slot: only before a sign-in" ([OTA.md](OTA.md)).
  * Smaller items: `gb_slotsync` reads `loader.conf` as systemd-boot does (a lone CR ends a line, exactly one
    `default` line); the VM entry gets `efi_pstore.pstore_disable=1`; both trees restore the SELinux labels of our
    HAL files; `verify_image.py` checks the NVIDIA payload's own files (V4/V7) and the packed module index (V4b);
    the Gatekeeper binary's frozen-vector record is complete.
  * Tested in VMware: a rehearsal of the VM build (a fresh first boot 51 of 51, a forced unvalidated update with a
    later sign-in 40 of 40, a validated boot 23 of 23, a held update 36 of 36 with the notice in user 10's panel),
    VM-1 on the wave 9 USB image `e0825ee1…` (every wave 2, 3, 5, 6, 7, 8 and 9 check VMware can show: the two new
    services find no Surface hardware and exit cleanly; two checks judged after a project decision: init makes 2
    more short emulations in the new label-restoring step, accepted, and the key store's early-boot result fell
    outside that boot's log window; the analyser 17 PASS, 0 FAIL, 24 UNKNOWN). The installer ISO is not rebuilt
    yet (it still carries the wave 8 system), and the long-running VM stays on the wave 8 boot partition until
    the host has disk space for its check boot. The SAM and sensor-hub fixes need the laptop: item 29.

## 1. Platform, CPU, firmware interfaces

| # | Device (Windows / ID) | Linux driver (source) | Firmware | Android side | Status | Notes / evidence |
|---|---|---|---|---|---|---|
| 1 | CPU i7-1065G7, 4C/8T, Ice Lake-U | intel_pstate (HWP active), intel_idle: built in. RAPL: lapis `intel_rapl_common`/`intel_rapl_msr` (modalias) | CPU microcode from UEFI | libperfmgr power HAL (cpusets for 8 CPUs, fine); uclamp has no effect under HWP | Expected + **New here** (RAPL) | lapis `init.lapis.rc` writes PL1=25 W / PL2=55 W `on init` for its own board [V]. `gb_hw.sh` reads the Surface firmware PL1/PL2 from MSR 0x610 at early-init (using lapis's `msr.ko`) and restores them `on boot`. Choose with `persist.vendor.gb.rapl.mode=fw` (default), `lapis` or `custom`. The AVX-VNNI #UD emulator stays: Ice Lake has no AVX-VNNI. |
| 2 | Host bridge 8A12, LPC/eSPI 3482, PMC SRAM 34EF, root ports 34B0 (NVMe), 34B4 (dGPU) | PCI core / pcieport (built in) | – | – | Expected | – |
| 3 | Processor thermal device 8086:8A03 (00:04.0) | lapis `processor_thermal_device_pci_legacy` | – | – | Not present | Windows lists no PCI(0400) device, so the SB3 BIOS hides it [V]. lapis's `tcc_offset` write and the RAPL-MMIO zone simply do not exist. Harmless. |
| 4 | Intel Power Engine Plug-in INT33A1 (s0ix) | lapis `intel_pmc_core` (alias INT33A1) | – | lapis suspend services | Ready | FADT "Low Power S0 Idle = 1" [V], so suspend is s2idle. See row 46. |
| 5 | ACPI Processor Aggregator ACPI000C | (acpi_pad not built) | – | – | Not needed | `\_SB.PAGD` has a static `_PUR` and firmware never notifies it [V]. |
| 6 | CMOS RTC PNP0B00, HPET, timers, PIC | rtc_cmos, HPET: built in (RTC_HCTOSYS, RTC_SYSTOHC) | – | Network time | Partial | Windows keeps the RTC in **local time** (`RealTimeIsUniversal` unset by default). Android reads the RTC as UTC, so after switching from Windows its clock is off by the local UTC offset until network time corrects it. Optional fix on the Windows side (admin): DWORD `HKLM\SYSTEM\CurrentControlSet\Control\TimeZoneInformation\RealTimeIsUniversal = 1`. Not changed by this project. |
| 7 | ACPI Wake Alarm ACPI000E (`\_SB.SRTC`) | (ACPI_TAD not built) | – | Alarms use rtc0 | Not needed | Android's alarmtimer wakes through rtc0 (rtc_cmos). |
| 8 | WMI PNP0C14 "TBFP" | `wmi.ko` (ours) | – | – | Expected | No WMI client driver matches; harmless. |
| 9 | TPM 2.0 NTC0702 | none: CONFIG_TCG_TPM off; lapis tpm/tpm_tis* listed in `module_blacklist` | – | Software KeyMint + stubs | Off by design | `build_usb.py` NATIVE_BLACKLIST [V]. Keeps Android away from the Windows/BitLocker TPM. |
| 10 | SPI flash controller 34A4 | `spi_intel_pci`/`spi_intel`/`spi_nor`/`mtd` blacklisted | – | fwupd/flashrom/futility stubbed | Off by design | Protects the UEFI flash [V, build_usb.py, build_esp.py stubs]. **Wave 9:** lapis's `vpd` tool runs `/vendor/bin/flashrom` in the (permissive) `firmware_tool` domain at boot. On our image that file is the refusal stub, so the first native boot's SELinux lines `comm="vpd" ... /vendor/bin/flashrom ... tcontext=tmpfs` show the stub, not Google's flashrom. Every other lapis program that reaches libflashrom is a stub now as well: `futility`, `crossystem`, and new in wave 9 `elogtool` and `cbfstool` (both link libflashrom through libvboot_host; lapis's snapshot collector runs `elogtool`). The kernel itself has no path to the flash: no `/dev/mem` and no `/dev/port` (`CONFIG_DEVMEM`/`DEVPORT` off), no MTD, and the SPI-NOR drivers are blacklisted. |
| 11 | Intel ME 34E0 | `mei_me` (built in); lapis `mei_hdcp` (optional, i915 HDCP) | – | – | Expected | – |
| 12 | Serial IO: I2C 34C5/34E8-34EB, UART 34A8, GPIO INT3455 | lapis first stage: `intel-lpss-pci`, `8250_dw`, `pinctrl-intel`; ueventd: `pinctrl-icelake`, `idma64`, `i2c-designware-*` | – | – | Expected | The UART carries the Surface Aggregator. Our builds of these modules are never loaded (see "Changes since the audit"): loading ours natively breaks lapis's first stage and every lapis module linked to them. **Wave 3:** the native command line keeps `8250.nr_uarts=4`: our GKI has `CONFIG_SERIAL_8250_RUNTIME_UARTS=0`, so without it the 8250 driver never registers and `8250_dw` fails this UART with `-22` (no base keyboard, touchpad, batteries, charger, detach). Check: `Serial: 8250/16550 driver, 4 ports` in dmesg (checklist 19). Credit: gbOS86's Surface Laptop 7 finding. **Wave 9 (first native boot):** the UART registers and the SAM answers (`SAM firmware version: 10.600.139`), but on kernel k16 the Surface Aggregator's first probe runs in first-stage init, before lapis's `pinctrl-icelake` provides the INT3455 GPIO controller of the SAM's wakeup interrupt. It fails for good with `error -EINVAL: failed to setup IRQ` (an upstream regression since 6.12: `ssam_irq_setup()` no longer defers). `gb_hw.sh sam` (service `gb_hw_sam`, `on boot`) binds the device to `surface_serial_hub` again once INT3455 is bound, then waits for the keyboard, touchpad, batteries, charger and DTX. A kernel fix (patch 0114) is in the backlog for the next kernel build. |
| 13 | Motherboard resources (PNP0C02, INT3F0D); EPC INT0E0C; CoEx INT3519; 3x MAX34417 power monitors | – | – | – | N/A | All are disabled by `_STA`/NVS on this SKU and absent from Windows PnP [V]. |

## 2. Display and GPU

| # | Device | Linux driver | Firmware | Android side | Status | Notes |
|---|---|---|---|---|---|---|
| 14 | Intel Iris Plus 8A52 (00:02.0) | `i915.ko` + video/wmi/drm helpers (ours, gbinit) | `i915/icl_dmc_ver1_09.bin` staged (GuC/HuC optional) | lapis Mesa ANV (gfx11 + i915 backend), ANGLE, minigbm (i915 table includes 0x8a52), hwc3-drm | Ready | Uses lapis's stock graphics stack; the VMware-only overrides stay out of the native tree. **Wave 5:** the native build.prop sets the composer's own colour defaults (`vendor.hwc.drm.force_color_mode=-1`, `vendor.hwc.drm.persistent_hdr=false`, `vendor.hwc.use_log_gamma_lut=false`). lapis sets `13`/`true`/`true` for Google's BT.2020 panel, and according to the composer binary that makes it put a BT.709→BT.2020 gamut matrix into i915's CTM and tag the eDP as BT.2020: the expected grey, pale "white veil" (gbOS86 saw it on another i915 laptop). With `enable_color_pipeline=false` the composer clears the gamma LUT on every frame anyway; with the defaults the CTM carries only SurfaceFlinger's matrix, so Night Light works. The kernel switch `i915.gb_ignore_user_gamma` (build #15, default 0) stays as a fallback, set only through `gb\gb.cfg`: `=4` drops the CTM (Night Light off); `3` does nothing natively ([FLASHING.md](FLASHING.md#7-troubleshooting-symptom-log-line-boot-option)). INFERRED until the first boot on the laptop. Checklist 21. |
| 15 | Internal panel "Surface Calibrated Panel" MEI96A2, 3000x2000 eDP, backlight | i915 → `intel_backlight` | – | hwc3-drm drives `/sys/class/backlight/*` [V, strings]; lights HAL disabled (`ro.vendor.lights.backlight.enabled=false`) [V]; lapis `init.device-x86.rc` chowns `intel_backlight` [V] | Ready + **New here** | `gb_hw.sh boot` also chowns every other backlight to system (for example `acpi_video0`, if it ever appears). Brightness keys arrive only as keyboard HID consumer keys: ACPI `BRTN` has no caller, so there are no duplicate ACPI events [V, DSDT]. If the panel flickers or shows ghost images, put `GB_MODOPTS_i915=enable_psr=0 enable_fbc=0` into `gb\gb.cfg` and boot once with ` gb.overlay=1` ([FLASHING.md](FLASHING.md#7-troubleshooting-symptom-log-line-boot-option)). `i915.enable_psr=0` on the command line has no effect, because gbinit loads i915 with `insmod` (untested). |
| 16 | External display: base USB-C (DP alt mode) and Surface Connect / SurfaceDock DP | i915 DDI | – | – | Ready [I] | The USB-C port sits behind the base USB hub (`XHC.RHUB.HS02.PRT1`, `_UPC` type 0x09) [V], so DP comes through a mux in the base that the EC/PD controller drives. There is no OS UCSI (row 33). Expected to work as a plain DP connector. Unverified. |
| 17 | NVIDIA GTX 1650 Max-Q 10DE:1F91 (base, behind 00:1d.4; its HDA function stays driverless); MSHW0216 "IHV GPU Power Controller"; MSHW0153 hot-plug | **nouveau** (build #14 = #13 + 37 linux-6.18.y nouveau backports + hardening N1-N3; `runpm=1 modeset=2`, headless), in no boot-time load list: `gb_nouveau.sh` loads it after `sys.boot_completed` when the base is attached and the graphics mode is Auto/NVIDIA (default Auto), and binds it again on reattach. Intel mode: no driver, D3cold. `gb_power` owns detach (refcnt gate, unbind + PCI remove in a worker), rescan and the re-bind guard. `surface_hotplug`. MSHW0216: no Linux driver anywhere | `nvidia/tu117` (30 files, GSP-RM 570.144, 535.113.01 fallback; linux-firmware tag 20260916, with WHENCE + `LICENCE.nvidia`) in the `gb/dgpu-<krel>.erofs` payload on the ESP, mounted at `/odm/dgpu`; you fetch it | **Per-app NVIDIA graphics:** the driver package GbGfxDriver (`ro.gfx.driver.0`, Android's updatable-driver settings decided by gb_power) gives games and **Use NVIDIA** apps a shim that puts NVK (Mesa 26.3 + our Android patches) first. HWUI, SurfaceFlinger and the system UI stay on ANV. QS tile and Settings page "Graphics" (GbGpu). Automatic self-test (`nvk_probe`) before the first NVIDIA app. Compute service `gb_gpu_compute` (llama.cpp on NVK, UNIX socket). | Ready, untested on hardware | Everything runs headless: the dGPU has no display outputs, and its frames are composed by Intel. Apps switch GPU only when restarted, and detach is refused while apps hold the dGPU ("Close apps and detach"). Battery life depends on D3cold being reached when idle; check row 46 and the checklist. Recovery without a shell: `gb.nouveau=0` in the boot menu. **Wave 6:** only a TU117 (PCI `10DE:1F80`-`1FFF`, the 13.5" model) is ever probed. On a 15" Surface Book 3 (TU116 GTX 1660 Ti or TU106 Quadro RTX 3000; the image has no firmware for them) `gb_nouveau.sh` stops before any write with `vendor.gb.nouveau.result=refused:model` and one log line; the dGPU stays driverless (expected D3cold), graphics stay on Intel, gb_power does not retry, and GbGpu shows `Intel (NVIDIA not supported)` instead of the "NVIDIA GPU problem" notification. An unreadable device id is not gated. Tested off the device only. Details: [NVIDIA.md](NVIDIA.md). |

## 3. Input

| # | Device | Linux driver | Android side | Status | Notes |
|---|---|---|---|---|---|
| 18 | Base keyboard, SAM HID **045E:09AE** (keyboard, system control, consumer control) [V, Windows HW IDs] | surface_aggregator → registry → hub → `surface_hid` → `hid-surface` (build #13) | **New here:** `vup/usr/idc/Microsoft_Surface_045E_09AE_Keyboard.idc` (`device.internal=1`, `keyboard.builtIn=1`), matched by name so the consumer/system-control siblings do not grab keyboard id 0 | Ready (failed on the first native boot: no SAM link, see row 12; wave 9: needs the `gb_hw_sam` re-bind, unverified on the laptop) | The SAM firmware reports Fn as BTN_0 (linux-surface patch 0016, written for these exact IDs). It can stick, and on Android a key in [BTN_MISC, BTN_MOUSE) makes EventHub classify the keyboard as a gamepad. Fixed by `native/hw-gap-audit/kernel/0001-HID-surface-...patch`. Media, volume and brightness keys are standard HID usages that Generic.kl already maps, so no custom .kl is needed [V, Generic.kl]. The keyboard backlight is handled by the EC: no LED class device exists, so Android shows no keyboard-backlight control [I]. **Wave 5:** lapis's `DesktopDeviceStateProvider` disables built-in input devices when the posture state is `CLOSED`; `gb_hw.sh diag` logs the committed state and `Enabled:` flag, and `vendor.gb.posture=open` overrides it if needed ([FLASHING.md](FLASHING.md#7-troubleshooting-symptom-log-line-boot-option)). |
| 19 | Precision touchpad, SAM HID **045E:09AF** [V] | `surface_hid` → hid-multitouch (built in, Win8 PTP) | **New here:** `Microsoft_Surface_045E_09AF_Touchpad.idc` (`touch.deviceType=touchPad`, internal) → TouchpadInputMapper gestures | Ready + **New here** (failed on the first native boot: no SAM link, see row 12; wave 9: needs the `gb_hw_sam` re-bind, unverified on the laptop) | Palm rejection uses the PTP confidence bit (MT_TOOL_PALM). hid-multitouch (class WIN_8, `export_all_inputs`) also turns the two vendor collections (FF07:0001 "Navigation Accessory", FF05:0004 "USP Encapsulation") into "Microsoft Surface 045E:09AF UNKNOWN" inputs that carry only BTN_MISC/ABS_MISC codes, which Android sees as a gamepad. **New:** `vup/etc/excluded-input-devices.xml` hides them by exact name, keeping lapis's "Power Button" entry. The legacy "... Mouse" sibling should stay silent in PTP mode; it is not excluded. **Wave 3:** the IDC sets `gestureProp.Change_Timeout = 0.02` (Android's 200 ms default ignores quick flicks; gbos-x86 issue #17 confirmed 0.02 on lapis). Checklist 20. |
| 20 | Base vendor HID **045E:09A6**: "Surface HID Color", "Base v3 Firmware Update" (CFU), USP encapsulation, "Surface UCM UCSI HID Client" [V] | hid-generic, hidraw only | – | Gap (UCSI) / Off by design (CFU) | No Linux driver exists for UCSI-over-SAM-HID (see row 33). The firmware-update collection must never be used; fwupd is stubbed. |
| 21 | Touchscreen + pen, IPTS 8086:34E4 | `ipts.ko` (MEI 34E4 ID patched) | iptsd + IDCs (`native/iptsd`) | Ready | iptsd tested end-to-end on the builder against a fake IPTS uhid device. **Wave 5:** `iptsd.conf` sets `[Touchscreen] DisableOnStylus = true` so touchscreen touches are ignored while the stylus is in proximity (palm rejection while hovering). |
| 22 | Surface Pen top button: BLE HOGP **045E:0921** (Win+F20 click, F19 double-click, F18 hold) | Android BT HID host → uhid (built in) → hid-generic | **New here:** `vup/usr/keylayout/Vendor_045e_Product_0921.kl` (F20 → STYLUS_BUTTON_TAIL, F19 → SYSRQ, F18 → ASSIST; META dropped) + `Surface_Slim_Pen_2_Keyboard.kl` (name match) | Ready [I] | The pen must be paired in Android's Bluetooth settings. The 0921 ID comes from a published Linux uhid log of this pen model, not from a pen paired on the test machine. STYLUS_BUTTON_TAIL opens the default notes app (SystemUI). |
| 23 | Power + volume buttons, MSHW0040 "Surface Button" | `soc_button_array` + lapis `pinctrl-icelake` (`gb_hw.sh buttons` rebind) | Generic.kl POWER / VOLUME_* | Ready | DSDT `\_SB.MSBT`: GPIO 0x23 (power, SharedAndWake), 0xF6, 0xF7 (`\_SB.GPI0`, `INT3455`); the `_DSM` OMPR function exists [V]. There is no ACPI fixed power button (FADT: control-method power button; no PNP0C0C) [V], so lapis's "Power Button" exclusion is irrelevant. **Wave 5 (A-H1):** `gbinit` insmods `soc_button_array` before ueventd coldboot loads lapis's `pinctrl-icelake` (`INT3455:00`), and the 6.18 driver ignores `-EPROBE_DEFER` and fails with `-ENODEV` (never retried). `gb_hw.sh buttons` (`on boot`) waits up to 30 s for `INT3455:00` to bind, writes `MSHW0040:00` to `/sys/bus/platform/drivers/soc_button_array/bind`, verifies the two `gpio-keys` input devices, and records the result in `vendor.gb.buttons` (checklist 25). |
| 24 | Lid PNP0C0D (`\_SB.LID0`) | ACPI button (built in) → SW_LID; wake GPE 0x4D via `surface_gpe` | Lid switch → sleep | Ready | `_L4D` → `Notify(LID0)`; LID0 has no `_PRW`, so surface_gpe is what makes the lid a wake source [V]. |
| 25 | Clipboard detach, tablet/studio posture; MSHW0005 "Laptop or Slate Indicator" (PNP0C60) | `surface_dtx` SW_TABLET_MODE + `gb_power` DTX handler. MSHW0005: no Linux driver, not needed | Tablet-mode switch | Ready (failed on the first native boot: no SAM link, see row 12; wave 9: needs the `gb_hw_sam` re-bind, unverified on the laptop) | Without a daemon, detach should still complete through the EC timeout. |
| 26 | Windows virtual HIDs 045E:F001/F002/F005, HID\CONVERTEDDEVICE | – | – | N/A | Created by Surface Windows drivers. The Linux equivalents are rows 23, 25 and 28. |

## 4. Sensors and cameras

| # | Device | Linux driver | Android side | Status | Notes |
|---|---|---|---|---|---|
| 27 | ISH 34FC + "HID Sensor Collection V2" 8087:0AC2 (accelerometer, gyro, ...) | lapis `intel-ish-*` + our `hid-sensor-*` (late list) with sensors patch 0001 (`cros-ec-accel` / `cros-ec-gyro`) | sensors component (lapis Desktop IIO sub-HAL, auto-rotate overlay, `gb_sensors.sh`) | Ready (unverified upstream; linux-surface: `?`) | ISH firmware comes from BIOS. QEMU-tested with a fake ISH; upstream linux-surface marks Surface Book 3 sensors as `?`. Rotation follows the posture (Android's own laptop policy, not ours): with the base attached normally (device state `OPEN`) the screen stays in landscape whatever the Auto-rotate setting says; with the clipboard detached or the base reversed (`TABLET`) the **Auto-rotate** setting applies. Every user starts with Auto-rotate on (our SettingsProvider overlay, wave 6); turning it off in Quick Settings is remembered and only matters in tablet posture. `gb_sensors.sh` logs one `GB_SENSORS: rotation:` line at boot and at every attach/detach (first native boot: `ok: laptop posture, rotation locked`). **Wave 9:** on one of three first native boots, lapis's `intel-ish-ipc` probe timed out on the ISH firmware (`HBM haven't started`, `ISHTP: Protocol init failed`), so no accelerometer existed for that boot. `gb_hw.sh ish` (service `gb_hw_ish`, early-boot) then resets the ISH once and binds it again while `gb_sensors_wait` still holds the sensors HAL back (8 s). The two other boots had every sensor. |
| 28 | Ambient light MSHW0184 = APDS-9960 on I2C4 0x39, GpioInt pin 1 [V, DSDT] | `apds9960` with sensors patch 0002 (`cros-ec-light` companion) | sensors component | Ready (unverified upstream; linux-surface: `?`) | The IRQ needs pinctrl-icelake first; I2C probe deferral handles the order. Upstream linux-surface marks Surface Book 3 sensors as `?`. |
| 29 | Cameras: front OV5693 INT33BE, rear OV8865 INT347A (+ DW9719 focus), IR OV7251 INT347E, INT3472 x3, ISP IPU4P 8086:8A19 | build #13: IPU4P port on the ipu6 driver (`intel-ipu6`, `intel-ipu6-isys`), `ov5693`, `ov8865` (`ov7251` staged but not loaded since wave 9), `dw9719`, `ipu-bridge`, `intel_skl_int3472_*`, `v4l2-cci` (gbinit) | libcamera simple pipeline + software ISP as `camera.sb3.so` (`ro.hardware.camera=sb3`) | Ready, untested (needs your IPU4P firmware) | Mainline Linux has no IPU4 driver; this is the project's own port. The HAL was tested against vimc on the builder only. The IR camera (Windows Hello) is not used by Android. USB UVC webcams should work independently (USB_VIDEO_CLASS=y + lapis USB camera provider). **Wave 5 (C-1):** `stage.sh` now strips before `patchelf` and gates `PT_LOAD` alignment (`V12b`); the re-staged camera libraries, the HAL, `lcam-probe` and `soft_ipa_proxy` load under lapis 276's bionic `linker64` (builder test; first device test pending). Expect front and rear preview/photos at modest quality and frame rate (CPU software ISP, manual focus only on DW9719, no IR camera use in Android). If the IPU4P driver misbehaves at bring-up, `GB_SKIPMOD=intel_ipu6_isys,intel_ipu6` in `gb\gb.cfg` (one boot with `gb.overlay=1`) skips it. **Wave 9:** the IR camera's driver is no longer loaded. On the first native boot its probe always failed (`ov7251_write_reg: write reg error -121`, `error during global init`), and Android never used it. The front and rear cameras register on their own and are unaffected. The rear camera's focus motor logged `dw9719 ...: Error writing reg 0x0003: -121` in two boots: watch for autofocus on the first camera test. |

## 5. Storage, USB, docks

| # | Device | Linux driver | Status | Notes |
|---|---|---|---|---|
| 30 | NVMe SSD (Toshiba/Kioxia KXG60 series, 1179:011A) | nvme (built in) | Expected | The project fstab keeps vold off nvme0/1 (`nvme[2-9]`) [V]. |
| 31 | PCH xHCI 34ED + base hubs 045E:0C12/0C13 → 0C14/0C15 | xhci-pci, hub (built in) | Expected | Port map from ACPI `_UPC`/`_PLD` [V]: HS01/SS01 = proprietary visible port (Surface Connect); HS02/SS02 = base hub → PRT1 USB-C, PRT2 = second hub → PRT2/PRT3 the two USB-A ports, PRT1 internal/non-connectable; HS10 = Bluetooth. Detaching the base removes all base ports. |
| 32 | TCSS xHCI 8A13 (00:0d.0) | xhci-pci (built in) | N/A | Nothing attached. TCSS DMA and Thunderbolt root ports are disabled [V, PnP + SSDT `_STA`]. |
| 33 | USB-C / PD (base) | USB data: base hub. **UCSI:** ACPI `\_SB.UBTC` (USBC000/PNP0CA0) exists, but `_STA` returns 0 unless NVS `USTC==1`, and Windows does not enumerate it [V], so lapis `ucsi_acpi` never binds. Windows uses UCSI over SAM HID (row 20) | Partial | Data and charging are expected to work (the EC runs PD) [I]. Android gets no `/sys/class/typec` port, so there is no PD/role/partner information and no data-role switching (lapis USB HAL shows nothing). Writing a SAM-HID UCSI transport would mean reverse-engineering an undocumented protocol on the real device. Not feasible blind. |
| 34 | SD card reader (base) | usb-storage / uas (built in), vfat/exFAT (built in) | Ready [I] / Partial | There is no PCI SD host on the SB3, and on the audited unit Windows had never enumerated the reader, so it probably appears only with a card inserted, behind the base hub [I]. vold mounts it as a "USB" volume on internal installs. **On USB-stick boots** gbinit deletes the `voldmanaged=usb` fstab line (to protect the boot stick) [V, build_esp.py], so SD cards and USB sticks are not mounted then. Since kernel build #16 a Realtek vendor-class reader (0BDA:0129/0139/0140, the ids in the driver's table) or a Realtek PCIe reader is covered too: `rtsx_usb`/`rtsx_usb_sdmmc` and `rtsx_pci`/`rtsx_pci_sdmmc` are in the native image and lapis's ueventd loads them by modalias when the reader appears [V: VMware load test; the reader type itself is still unknown]. |
| 35 | Surface Connect + SurfaceDock 1/2 (USB, Ethernet, DP, audio) | Ethernet: `r8152` (in modules.load; handles 045E:07AB/07C6/0927/0C5E [V, r8152.c]); DP: i915; dock audio: SND_USB_AUDIO (built in) | Ready [I] + **New here** | `firmware/rtl_nic/rtl815[36]*.fw`: 7 files from linux-firmware, listed by sha256 in `native/hw-gap-audit/tree/firmware/rtl_nic.sha256` (you fetch them; not in this repository). r8152 works without them but loads these PHY patches when present. lapis declares `android.hardware.ethernet` [V]. |
| 36 | USB NIC / serial dongles | r8152, ax88179_178a, cdc_ether, cdc_ncm, cdc-acm (gbinit) | Ready | – |

## 6. Wireless and audio

| # | Device | Linux driver | Firmware | Status | Notes |
|---|---|---|---|---|---|
| 37 | Wi-Fi AX201 160 MHz 8086:34F0 | lapis `iwlwifi`/`iwlmvm` + our signed libarc4/ptp/pps_core/rfkill (gbinit) | iwlwifi-Qu-b0/Qu-c0/QuZ-a0-hr-b0-77 staged | Ready | dmesg shows which stepping loaded. `wpa_supplicant` / `hostapd` link again since wave 5 (the KeyMint graft's Android 16 `libcrypto` no longer replaces lapis's, which `libssl.so` needs; checked statically and with lapis's own linker); joining a network and the hotspot are untested on hardware. |
| 38 | Bluetooth 8087:0026 (USB, HS10) | lapis `btusb`/`btintel` + our `bluetooth.ko` | `intel/ibt-19-*` staged (expected ibt-19-32-4) | Ready | **Wave 3:** `ibt-19-*` is the legacy-mode name family (`19` = hw_variant 0x13, printed in decimal). If the controller boots in TLV mode it asks for an `intel/ibt-<cnvi>-<cnvr>.sfi` name instead (gbOS86 saw `ibt-0040-1050.sfi` on another laptop), which is not staged: the boot-log summary's firmware-failure lines then name the file to add (checklist 23; [INPUTS.md](INPUTS.md#firmware-native-only)). |
| 39 | SST DSP 34C8 + Realtek ALC274 + Intel display audio | lapis SOF modules, dsp_driver=3 | sof-icl.ri + topologies staged | Ready | `native/audio`. **Wave 3:** DRAS's `<soundcard>` is the ALSA card id `sofhdadsp` (gbOS86: DRAS matches the id, not the name), and the Mic section carries `CaptureMixerElem` like lapis's sof-soundwire config (INFERRED field set). Checklist 22. |
| 40 | Headphone jack (clipboard) with detection | ALC274 jack → "sof-hda-dsp Headphone" input | – | Ready | `native/audio` (JackDev). |
| 41 | DP/HDMI audio (dock, USB-C) | SOF iDisp PCMs 3/4/5 | – | Ready | Needs i915 bound. Without i915, SOF probe defers and no audio works at all. |

## 7. Power, thermal, suspend

| # | Function | Driver | Status | Notes |
|---|---|---|---|---|
| 42 | Two batteries (clipboard BAT1, base BAT2) + AC | `surface_battery`, `surface_charger` + `gb_power` combined battery | Ready (failed on the first native boot: no SAM link, see row 12; wave 9: needs the `gb_hw_sam` re-bind, unverified on the laptop) | `native/power` (unit-tested). |
| 43 | Battery charge limit | none: surface_battery has no `charge_control_*`; lapis charge-control HAL is cros_ec-only | Gap (Android) | Use Surface UEFI → "Enable Battery Limit" (50 % cap, set in firmware setup). |
| 44 | Performance mode (SAM) | `surface_platform_profile` + `gb_power -p` | Ready | Changes only take effect on AC (linux-surface wiki). |
| 45 | Thermal sensors / fans; MSHW0187 "Thermal Policy" | lapis `x86_pkg_temp_thermal` (zone x86_pkg_temp), iwlwifi zone. ACPI thermal is useless: the only ThermalZone `\TPOL` has no `_TMP` [V]. Fans are EC-controlled with no OS interface; MSHW0187 has no Linux driver | Partial | Safe: CPU TCC/PROCHOT and the EC fan are firmware-controlled and not affected. Android reports no thermal status, because lapis's thermal HAL config is selected only for product "lapis". A SB3 `thermal_info_config.json` is optional future work. |
| 46 | Suspend (s2idle / Modern Standby) and wake | LPS0 s2idle; wake sources: power-button GPIO (after `gb_hw.sh buttons`), lid GPE 0x4D (`surface_gpe`), XHC `_PRW` | Ready [I] (S0ix unverified upstream; linux-surface: `?`) | `surface_aggregator` sends the display-off and D0-exit notifications at suspend, but leaves its own wake IRQ disabled by default (upstream `core.c`: avoids spurious wakeups), so base keyboard keys and touchpad presses do not wake the device. Wake uses the power button or the lid. The dGPU must reach D3cold (`gb_power`), otherwise there is no S0ix; upstream linux-surface marks SB3 S0ix as `?`. `gb_hw.sh diag` logs `low_power_idle_system_residency_us` and `low_power_idle_cpu_residency_us` on its `mem_sleep` line to confirm S0ix residency. Screen-off without suspend does not tell the EC "display off" (Windows does), which costs some battery [I]. |
| 47 | Reboot / shutdown | `reboot=pci` in native cmdline | Ready | linux-surface SB3 wiki. |

## What the audit added (`native/hw-gap-audit/`)

| File | Destination | Purpose |
|---|---|---|
| `kernel/0001-HID-surface-drop-the-bogus-SAM-Fn-button.patch` | kernel tree (in build #13) | Adds `drivers/hid/hid-surface.ko`, CONFIG_HID_SURFACE=m (row 18) |
| `kernel/k12_kernel_changes.txt` | kernel build notes | Config line, BUILD.bazel path, modules.load position, rejected config options with reasons |
| `kernel/src/hid-surface.c`, `kernel/src/Makefile` | – | Source and out-of-tree compile test (build logs not included) |
| `tree/gb_hw.sh` → `/odm/gb_hw.sh` | native tree | RAPL firmware-limit snapshot/restore, backlight permissions, `MSHW0040` power/volume button binding (`buttons`), opt-in posture override (`posture`), boot hardware + posture + S0ix residency summary to kmsg (`diag`) |
| `gb_hw.rc` | native gb.rc (as `rc_text` of `hw.json`) | Starts `gb_hw.sh` at early-init (`rapl-snapshot`), `on boot` (`boot`, `buttons`), and `boot_completed` (`diag`, plus `posture` when `vendor.gb.posture` or `persist.vendor.gb.posture` is set) |
| `tree/usr/idc/Microsoft_Surface_045E_09AE_Keyboard.idc`, `..._09AF_Touchpad.idc` → `vup/usr/idc/` | native tree | Built-in keyboard and touchpad configuration |
| `tree/usr/keylayout/Vendor_045e_Product_0921.kl`, `Surface_Slim_Pen_2_Keyboard.kl` → `vup/usr/keylayout/` | native tree | Surface Pen top button |
| `tree/etc/excluded-input-devices.xml` → `vup/etc/` | native tree | Hides the SAM touchpad/keyboard vendor-collection inputs (BTN_MISC "gamepads"). Keeps lapis's entry; recheck against the booted vendor build |
| `tree/firmware/rtl_nic.sha256` (the `.fw` files are fetched by you) → `firmware/rtl_nic/` | native tree (/odm/firmware) | SurfaceDock / USB Ethernet PHY patches for r8152 |
| `hw-gap-audit.json` | `inject/native/components/hw.json` | Component manifest in the same format as iptsd/power/audio |
| `test/test_gb_hw.sh`, `test/check_input_files.py` | – | Host test of gb_hw.sh under mksh (25/25 pass; full platform + mutation suite in the wave 5 fix records); syntax check of the IDC/KL/XML files |

## Hardware validation checklist (first native boot; read `EFI-SYSTEM\gblog\boot*.txt` from Windows)

1. Keyboard: `getevent -lp` on "Microsoft Surface 045E:09AE Keyboard" must **not** list BTN_0 / BTN_MISC. `dumpsys input` should show it as keyboard id 0 (built-in) with no GAMEPAD source. Test Fn+F-row, media, volume and brightness keys. If the keyboard is listed in `== input` but does not type, check `posture:` in the summary (`mCommittedState=CLOSED` or `Enabled: false`) and see [FLASHING.md](FLASHING.md#7-troubleshooting-symptom-log-line-boot-option) (`GB_PROP_1=vendor.gb.posture=open`).
2. Touchpad: `dumpsys input` should show "Microsoft Surface 045E:09AF Touchpad" with TouchpadInputMapper, and the "... UNKNOWN" devices listed as excluded (they appear in `gb_hw: diag: inputN` lines). Test tap, two-finger scroll and palm rejection. Check whether the "...Mouse" sibling ever sends events.
3. RAPL: dmesg should contain `gb_hw: rapl-snapshot: firmware MSR_PKG_POWER_LIMIT pl1_uw=... locked=...` and `gb_hw: rapl: mode=fw PL1/PL2 uW 25000000/55000000 -> <fw values>`. If locked=1, lapis's writes failed too, which is fine.
4. Backlight: `gb_hw: backlight: intel_backlight max=...`. Brightness slider and Fn brightness keys both change the panel.
5. USB-C: plug in a USB-C hub or monitor. Check that data and charging work and that DP shows a picture. `/sys/class/typec` is expected to be empty.
6. SD card: insert a card **while booted from the internal install**, then note the reader's VID:PID (`cat /sys/bus/usb/devices/*/idVendor /sys/bus/usb/devices/*/idProduct`; it is also visible in Windows Device Manager once a card is inserted there). With kernel build #16: a Realtek `0bda:0129`/`0139`/`0140` reader must show `rtsx_usb` and `rtsx_usb_sdmmc` in `lsmod` and a `/dev/block/mmcblk0`; any other reader must be bound by `usb-storage` (`ls /sys/bus/usb/drivers/usb-storage/`) and give a `/dev/block/sd*`. Either way vold should offer the card. If neither happens, keep `dmesg | grep -i -E 'rtsx|mmc|usb-storage|sd '` and the VID:PID. If a boot hangs once the reader appears, keep the four drivers off for one boot: press `e` at the boot menu, press End and type ` module_blacklist=tpm,tpm_tis,tpm_tis_core,tpm_tis_spi,tpm_tis_i2c_cr50,spi_intel_pci,spi_intel,spi_nor,mtd,gsmi,rtsx_usb,rtsx_usb_sdmmc,rtsx_pci,rtsx_pci_sdmmc` (the kernel keeps only the last `module_blacklist=`, so the line must repeat the entry's own list; `module_blacklist=rtsx_usb` alone would drop the TPM, SPI-flash and GSMI protection for that boot).
7. Dock: SurfaceDock Ethernet (r8152 firmware lines in dmesg), external DP and dock audio.
8. Pen: pair the Surface Pen in Android Bluetooth settings. A top-button click should open the notes app; double-click takes a screenshot.
9. Cameras: the boot-log summary shows `vendor.camera-provider-2-7-legacy-wrapper` `running` with `HAL load failures: 0`; `dmesg | grep -i ipu` shows firmware `intel/ipu/ipu4p_fw.bin` loaded and ISYS registered; `/odm/bin/lcam-probe -l` lists the front (`ov5693`) and rear (`ov8865`) sensors without `CANNOT LINK EXECUTABLE`; then test the Camera app (front and rear).
10. Sensors: auto-brightness, and rotation per posture (note that upstream linux-surface marks SB3 sensors as `?`).
    Within 14 minutes after boot (the last boot-log snapshot is taken at 15 minutes): with the base attached, tilt
    the laptop (the screen must stay landscape); detach the clipboard, wait 10 s, turn it to portrait (it must
    rotate if **Auto-rotate** is on in Quick Settings); reattach (back to landscape). The next boot-log snapshot
    then holds one `GB_SENSORS: rotation:` line per step: `ok: laptop posture, rotation locked` and `ok: tablet
    posture, auto-rotate on|off ...` are the expected verdicts. `MISMATCH` means Android's posture policy did not
    follow the hardware (the line shows the device state and whether the display is fixed to the user rotation);
    `WARN: config_supportAutoRotation is off` means the sensors overlay did not apply (`cmd overlay list | grep
    gb_sb3`); a `note: DTX mode ... disagrees with the device state` points at the lid switch or `SW_TABLET_MODE`
    (item 25). The `GB_SENSORS: rotation: diag:` line shows the state at boot (usually the login screen; its
    `user=` field says which user).
    At the first boot the log also shows how Auto-rotate got its default (wave 7):
    `GB_SENSORS: auto-rotate default from GbSb3SettingsOverlay (enabled): no first-boot write` is expected (the
    overlay applied, and nothing writes a rotation setting; since wave 8 that boot also sets the one-time flag
    `persist.gb.sensors.rotinit=1`, so a later boot that does not see the overlay enabled writes nothing either);
    `GB_SENSORS: auto-rotate enabled (first boot)` means the
    overlay is not applied (check `cmd overlay list --user 0 | grep gb_sb3`), and the old one-time write ran as a
    fallback.
11. Suspend: lid close → s2idle → lid open wakes. Power button sleeps and wakes (also with the clipboard detached, once `gb_hw.sh buttons` bound `MSHW0040:00`: item 25). Base keyboard keys are **not** expected to wake (`surface_aggregator` leaves wake off by default). After a sleep, check the battery drop, `/sys/power/suspend_stats` and `lpi_residency_us system=` on the `gb_hw: diag: mem_sleep` line (growing > 0 confirms S0ix residency), and check that the dGPU is in D3cold (`gb_hw: diag: dGPU ... power_state=D3cold`).
12. Clock: compare Android time before and after Wi-Fi connects (row 6).
13. Reboot and shutdown complete without hanging at the Surface logo.
14. NVIDIA, first boot with the base attached: about 2 minutes after boot, `vendor.gb.nouveau.result=clean`
    (`getprop`, or the `gblog\boot<N>-t420.txt` report). While idle the dGPU reads `power_state=D3cold`
    (`gb_dgpu_check.sh`). The Graphics tile goes `Intel (testing NVIDIA)` → `NVIDIA`, and Settings → System →
    Graphics shows the self-test as passed. If the boot hangs once Android is up, hold Power for 10 s and boot again:
    the next boot must come up on Intel with "Switched to Intel graphics". The manual override is `gb.nouveau=0` in the
    boot menu. (Wave 6) `dmesg` has no `gb_nouveau: guard: ... not the 13.5-inch` line and no `gb_nouveau: model
    gate:` line; `== dgpu` shows the dGPU as `0x10de` with a device id in `0x1f80`-`0x1fff`.
15. NVIDIA apps: start a Vulkan game (or vkcube). `logcat -s gb_gfxdriver` shows `NVK: 1 NVIDIA physical device(s)
    first`, and `dumpsys gfxinfo` keeps HWUI on Intel. An **Always Intel** app does not map the driver package.
16. Detach and reattach: idle → released within seconds, "Tablet mode - Intel graphics". With the game open →
    "Can't detach yet", then **Close apps and detach** completes it. Reattach → "Performance base connected", and the
    tile shows `NVIDIA` again. Repeat a few times, then suspend/resume with the base attached.
17. Intel only mode: the dGPU stays in D3cold across a reboot. Compare idle battery drain with Auto mode.
18. The `[NVIDIA test]` boot entry once (follow its on-screen suspend prompts), then keep the whole `gblog\` folder:
    it holds the dGPU power and PM-stress report.
19. (Wave 3, row 12) Serial / Surface Aggregator: the boot-log summary shows `Serial: 8250/16550 driver, 4 ports`,
    `dw-apb-uart -22: 0`, `serial surface_serial_hub bound: serial0-0` and (wave 9) `sam: MSHW0084
    serial0-0=surface_serial_hub; keyboard 1, touchpad 1; supplies: BAT1 BAT2 ADP1 ; dtx present; ...`. On k16,
    `failed to setup IRQ: 1` in `serial errors:` is the SAM's known first-stage failure that gb_hw repairs. Base
    keyboard, touchpad and both batteries work. (Wave 5) The `-517` line of `surface_hotplug` under `deferred probes`
    is expected, not an error.
20. (Wave 3, row 19) Touchpad flicks: a short, fast swipe moves the pointer at once (`gestureProp.Change_Timeout`).
21. (Wave 3, row 14; wave 5) White veil: the picture should look like Windows on this panel, and Night Light should
    tint it. The summary's `i915 white veil:` line shows `drmhwc LOG_GAMMA_LUT lines: 0` and `hwc: use_log_gamma_lut=false
    persistent_hdr=false force_color_mode=-1`. The `== getprop` section also shows `[vendor.hwc.drm.force_color_mode]: [-1]` and `persistent_hdr: [false]`.
    If the picture is washed out, try `GB_MODOPTS_i915=gb_ignore_user_gamma=4` with `gb.overlay=1` once and compare
    (Night Light stops with it). On images built before wave 5, first try the three `GB_PROP_<n>` lines of
    FLASHING.md section 8.
22. (Wave 3, row 39) Sound: `audio cards:` lists `sofhdadsp`, and DRAS's logcat opens it. (Wave 5) `audio
    dsp_driver=3`. If there is no card, test the legacy HDA driver once (`snd_intel_dspcfg.dsp_driver=1` at the boot
    menu, FLASHING.md section 7 "No sound") and report which one works.
23. (Wave 3, rows 37-38) Firmware: the summary's `-- firmware load failures (dmesg)` part is empty. Note every
    `Direct firmware load for ... failed` name (Bluetooth `intel/ibt-...` especially) in your report. (Wave 5) The
    `wifi:` line shows `wlan0` and `link: wpa_supplicant ok; hostapd ok`; `bluetooth:` shows `hci0` and
    `hal=running`; `linker:` shows `CANNOT LINK lines: 0 logcat, 0 kernel`.
24. (Wave 3) The rest of the summary as in [FLASHING.md, First-boot checklist](FLASHING.md#first-boot-checklist):
    fs-verity present, apgetty off, the `-- gb_vnni` counter (`irqon` equals `emulated`), no HWUI abort lines, no
    `gb_slotsync HOLD:` line. (Wave 8) On the stick, `loader\loader.conf` starts with `default gb-b.conf`; in the
    boot menu the `(slot B)` entry is highlighted; `ro.boot.slot_suffix` in `== getprop` is `_b`. Every line of
    the `-- gb_vnni` block is one `comm=... rip=... count=...` record (kernel build #16 escapes the task names).
25. (Wave 5, row 23) Power button and volume keys: the summary's `buttons:` line (and `gb_hw: buttons:` in `dmesg`, `[vendor.gb.buttons]` in `== getprop`) shows `MSHW0040:00` bound to `soc_button_array` with `2 gpio-keys input(s)`. A short Power press turns the screen off and wakes it again (including with the clipboard detached), and the volume rocker changes the volume.
26. (Wave 6, row 17) 15" Surface Book 3 only: `vendor.gb.nouveau.result=refused:model`, one `gb_nouveau: guard:`
    line naming `TU116` or `TU106` per gb_nouveau run (at most two per boot, plus one per base reattach), the dGPU
    `D3cold` in `gb_dgpu_check.sh`, no "NVIDIA GPU problem" notification, and the Graphics tile `Intel (NVIDIA not
    supported)`. The analyser shows `nouveau UNKNOWN` and `dgpu_power PASS`.
27. (Wave 6) Notifications after you sign in: attach and detach the base and check that the notices appear in your
    notification shade. `dumpsys notification` lists our notices as `10|com.android.shell|2020|<tag>` (wave 9: your
    user only; waves 6-8 used `-1`, all users) and GbGpu's as `10|gb.sb3.gpu|...` (your user), never as user 0;
    `dumpsys package gb.sb3.gpu` shows versionCode 29890777 (wave 9) and POST_NOTIFICATIONS granted for user 10;
    (wave 9) the notification shade shows each notice once, in your user only; GbGpu's notifications show a coloured
    chip icon; the Graphics tile is added when you first sign in, however late after the boot; the Graphics tile is in your Quick Settings
    (`/data/vendor/gb/ui/tile_added.10`) and shows the current state each time you open the panel; logcat has
    `GbGpu: user unlocked: refreshing state` after the sign-in.
28. (Wave 7) Your user's settings after the first sign-in: `settings get --user 10 system accelerometer_rotation`
    is `1` and the boot log has `GB_SENSORS: user 10: auto-rotate already on` (the Auto-rotate default came from
    our settings overlay) or `... auto-rotate enabled at its first unlock` (it did not); `GB_UI user=10` shows
    density 320. Turn Auto-rotate off, reboot, sign in: it stays off. Turn the Graphics app's notifications off,
    reboot: they stay off. (Wave 8) Change Display size in Settings, reboot, sign in: it stays, and the boot log's
    `GB_UI user=10` line says `the person's own Display size kept`.
29. (Wave 9) `getprop vendor.gb.sam` starts with `bound` (or `already` / `kernel` on a later kernel) and shows
    `kbd=1 tp=1 bat=11 adp=1 dtx=1` with the base attached; `getprop vendor.gb.ish` is `already accel=1` (or
    `rebound ... accel=1`). Write down whether the boot was a cold start (power off, then on) or a restart (from
    Android or from Windows). Start once with the clipboard detached: the posture must be `TABLET`.

## Readiness audit (wave 5): what the image proves and what needs the laptop

Before the wave 5 images were built, three read-only audits went through every Surface Book 3 device against the
kernel config, the shipped native tree, lapis's vendor build .276, the laptop's ACPI tables, the VMware boot logs and
upstream linux-surface. They found three blocking problems (the power and volume buttons, the camera HAL, Wi-Fi) and
three medium ones (the white-veil counter and composer settings, the posture policy, the boot-log summary); all of
them were fixed in the image (the Wave 5 group above). The native image was then booted once more in VMware ("VM-1",
2026-10-30, no Surface hardware there) and its file tree was audited byte for byte.

**Verified without the laptop** (in the image, by its build checks, or in the VMware boot):

* Platform: `8250.nr_uarts=4` in every native entry (USB, dual-boot export, ISO); the Surface Aggregator setup and its
  probe deferral until the GPIO controller is up; keyboard and touchpad input names match the IDC files; the detach
  latch fix and `gb_power`'s retries; the battery patches (checked with `modinfo`; the health HAL path works in the
  VM); the lid wake GPE `0x4D` for "Surface Book 3"; the platform profile loaded first; the ISH and ambient-light
  aliases and their ueventd rules; s2idle (`intel_pmc_core`) and `reboot=pci`.
* Display and dGPU: i915 and its DMC/GuC/HuC firmware names; 320 dpi on the 13.5" panel; the lid and tablet-mode
  switches that drive lapis's posture policy; `intel_backlight` permissions; the composer's colour pipeline off and
  external displays on; ANV supports Gen11; the IPTS touch path, `ipts.ko`, the iptsd preset and IDCs match
  upstream (tilt and pressure ranges tested end to end); the NVIDIA power-off path, Auto-mode safety rails and the
  pinned payload; every display, internal or external, is driven by i915.
* Audio, network, cameras, ports: the SOF Ice Lake modules link (0 CRC mismatches), its firmware and all 12
  topologies are in the image, the card id is `sofhdadsp`; the Wi-Fi stack loads, `iwlwifi` matches `34F0` and the
  `-77` firmware is staged; Bluetooth's `ibt-19-*` firmware is staged and its HAL runs; the IPU4P modules and sensor
  drivers load and the staged `ipu4p_fw.bin` matches upstream; xHCI, USB storage, the charger stack and `r8152` load.
* The wave 5 fixes in the VMware boot: the camera provider stays up (0 HAL load failures) and `lcam-probe` links;
  `wpa_supplicant` and `hostapd` link (0 `CANNOT LINK` lines); KeyMint registers and keystore finishes early boot;
  the Gatekeeper registers and agrees its shared key; the button step runs (and reports no `MSHW0040` device, as
  expected in VMware); the composer properties are `-1`/`false`/`false`; the OTA gate reports the build as
  validated; 0 enforced SELinux denials; the hardware-test analyser reports 0 FAIL (every hardware item UNKNOWN in
  VMware).
* (Wave 6 prep, B-6) The [debug boot](FLASHING.md#debug-boot-kernel-messages-on-the-screen)
  (` printk.ignore_loglevel=0 loglevel=5 earlycon=efifb keep_bootcon`, typed in the systemd-boot editor): kernel
  text on the firmware frame buffer from the first second, kept through `gbinit`'s module loading; Android still
  starts; a panic forced while `gbinit` loaded modules (right after `i915.ko`) stayed on the screen with its call
  trace and left no `gblog\` file, as on the laptop.

**Needs the Surface Book 3** (checklist items above):

* Power and volume keys after the rebind (item 25), and whether the lid switch reads "open" while the clipboard is
  detached (the posture, items 1 and 25).
* The picture: the white-veil result of the composer settings (item 21), Panel Self Refresh, which output feeds the
  base's USB-C port.
* The dGPU reaching D3cold, and nouveau and NVK on the real GPU (items 14-18).
* Sensors and S0ix (upstream linux-surface marks both `?` for this model); rotation per posture: locked in laptop
  posture, Auto-rotate in tablet posture (item 10; it depends on the lid switch reading "open" while the clipboard
  is detached, item 25).
* The number of internal microphones and the headset jack's input name (item 22).
* Joining a Wi-Fi network, the hotspot and Bluetooth pairing (item 23).
* The cameras on the IPU4P hardware (item 9): libcamera's software ISP has never run on the device; expect modest
  quality and no autofocus.
* The SD card reader type (item 6), the dock and USB-C (items 5, 7).
* The PIN round trip, the VNNI patch's native effect and the OTA gate on the native install.
* The debug boot on the 3000x2000 panel: whether its text stays readable while i915 loads, and how much slower that
  boot is (only needed if a boot stops before Android).
* Your user's settings after the first sign-in (Auto-rotate default, density, the Graphics app's notification
  permission kept off once you turn it off: item 28).

## Cross-component notes

- No other component overlays `excluded-input-devices.xml`, ships keyboard/touchpad IDCs or keylayouts, or touches
  RAPL (checked across `native/*`). If one later does, merge it with these files.
- `gb_hw.sh` uses lapis's vendor `msr.ko` only at early-init, to read MSR 0x606/0x610. It never writes MSRs; limits
  are written through the normal powercap sysfs.

## Not possible (or not without the real device)

- **NVIDIA dGPU for the system UI or a display:** SurfaceFlinger, HWUI and every system app stay on Intel. Only
  third-party apps can render on NVIDIA (per-app driver package), and the dGPU has no display outputs. Also absent:
  NVIDIA video decode/encode, temperature or clock readout (nouveau on 6.18), CUDA, the proprietary driver
  ([NVIDIA.md](NVIDIA.md#what-will-not-work)).
- **USB-C PD / role / alt-mode status in Android (UCSI):** UCSI is tunnelled through an undocumented SAM HID
  collection (045E:09A6, usage page FF00 usage F900). No Linux driver exists, and the ACPI UCSI device is disabled on
  this SKU.
- **Battery charge limit from Android:** no kernel interface on SAM batteries. Use the UEFI battery limit instead.
- **Keyboard-backlight control from Android:** firmware/EC only. No OS-visible LED.
- **Fan control and Surface thermal policy (MSHW0187):** EC/Windows only.
- **Windows Hello face login (IR camera):** no Android counterpart.
- **TPM / hardware-backed keys:** deliberately unused.
