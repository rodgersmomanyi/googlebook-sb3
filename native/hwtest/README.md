# hwtest: boot-log triage for the native Surface Book 3 image (`analyze_gblog.py`)

Every native boot of the Googlebook OS (Android 17, lapis) USB image writes timed snapshots to the stick's
EFI-SYSTEM partition (FAT32): `gblog\boot<N>-t<T>.txt` at T = 40, 90, 180, 420 and 900 s, plus
`gblog\audio-b<N>-t<T>.txt` from `gb_audiodiag` (format: `GB_BOOTLOG_SH` in `C:\GB-x86\build_esp.py`; wave 3 added the
`== summary` triage block, the `-- gb_vnni` counter and the embedded `gb_dgpu_report`; wave 5, `GB_BOOTLOG_SH` sha256
`cd94022f...`, added the summary lines in [Wave-5 summary lines](#wave-5-summary-lines)). `analyze_gblog.py` reads one
boot's snapshots and writes **TRIAGE.md**. Each item of the DEVICE-STATUS "Hardware validation checklist" and the
software around it gets PASS, FAIL or UNKNOWN, together with the exact log lines (`file:line  text`) behind the
decision. Logs of older images still work: a missing wave-5 line changes nothing.

* Python 3, standard library only. Run it with `python -I`.
* Read-only. It never writes into the gblog folder. The default output is `TRIAGE.md` in the current directory.
* Personal data is redacted from every printed line: serial numbers (`POWER_SUPPLY_SERIAL_NUMBER`, `ro.serialno`, ...),
  MAC addresses, e-mail addresses, SSIDs, account names, IMEI-type ids, input `Uniq=` values and the boot partition
  UUID (`androidboot.boot_part_uuid`). The full `getprop`/`logcat` dumps in the snapshots still contain such data
  (FLASHING.md section 4), so share TRIAGE.md, not the raw files.

## Usage

```bat
rem the stick's FAT32 partition in Windows, e.g. E:
python -I C:\GB-build\native\hwtest\analyze_gblog.py E:\gblog -o TRIAGE.md --json triage.json
```

| Option | Meaning |
|---|---|
| `src` | the `gblog` folder, a folder that contains `gblog\`, or a single `boot<N>-t<T>.txt` |
| `-o FILE` | Markdown output (default `TRIAGE.md` in the current directory) |
| `--json FILE` | the same result as JSON (`items[]` with `id`, `status`, `reason`, `notes`, `evidence[]`) |
| `--boot N` | the boot to analyze (default: the newest; the newest 8 boots stay on the stick) |
| `--env surface\|vmware\|other\|unknown` | override the platform detected from `== dmi` |
| `--expect-kernel REL` | expected `uname -r` (default k15: `6.18.21-android17-5-g3c2c2f588d3f-ab16302715`) |
| `--strict` | exit 1 if any item is FAIL (exit 2 = no snapshot / unknown boot) |

The console prints one summary line and each FAIL. TRIAGE.md has a header (result counts, platform, kernel,
snapshots, whether the kernel log still reaches back to 0 s, keyboard-base state), the checklist table, an evidence
section per item and a table of the checklist steps that only a person at the laptop can check.

## How items are judged

* **Snapshots are merged.** Kernel-log and logcat lines from every snapshot of the boot are de-duplicated, and the
  earliest file wins in the `file:line` reference. Read `t40` for boot messages (later snapshots lose them once the
  1 MiB kernel buffer wraps). Current state comes from the newest complete snapshot: properties, PCI drivers, dGPU
  power, swap and the `gb_vnni` counter. A newest file that is empty or cut short (power cut during the copy) is
  flagged in the header and does not replace the previous one. When every file is cut short (no `getprop`, no
  `== dgpu`, no kernel log from the boot), a line that is missing only because of the cut is no evidence: dtx,
  display, cameras and i915 are then UNKNOWN, not FAIL. A PCI function (00:02.0 for i915, 00:05.0 for cameras), an
  input device (keyboard, touchpad, the two `gpio-keys`) or a power supply (BAT1, BAT2, ADP1) counts as missing only
  in a whole list (`== pci`, `== input devices`, `== drm/input/power`), i.e. one that another section follows in the
  same snapshot; each list comes from the newest snapshot in which it is whole (review 2026-10-29), so a newest file
  cut inside a list does not hide an older snapshot's whole one. The summary's raw kernel lines (wave 5: the iwlwifi,
  Bluetooth and IPU lines after `wifi:`, `bluetooth:`, `camera:`) count as kernel log, so those items are still
  judged when the full `== dmesg` is cut off or has wrapped.
  `== dmesg-live.prev (tail)` belongs to the previous boot and is never used as evidence.
* **HW / SW.** HW items need the Surface Book 3 hardware. They are judged only when `== dmi` says `Surface Book 3`
  (or when there is no dmi section). On VMware or another machine they are **UNKNOWN** and still show what was
  found. Exceptions, because they fail the same way on the laptop:
  * a module that fails to insmod for a reason other than `No such device` is FAIL;
  * (wave 5, C-1) `cameras` is FAIL on any machine when logcat has `Could not load /odm/lib64/hw/camera.sb3.so`
    (or `... camera HAL module`), or (review 2026-10-29) when any snapshot's summary `camera:` line counts `HAL load
    failures: N` > 0 (that count covers the whole logcat, the snapshot keeps only its last 6000 lines): the camera HAL
    cannot be dlopen'ed, the camera provider exits, and no camera works;
  * (wave 5, C-2) `wifi` is FAIL on any machine when the summary's start probe says `wpa_supplicant FAILED...` (no
    scan or connect) or `hostapd FAILED...` (no hotspot): a `CANNOT LINK` line, a crash or any other unexpected
    answer. (review 2026-10-29) A probe that gave no answer, `FAILED (exit 124)` (cut off after 5 s) or `FAILED (exit
    126): ... Permission denied` (the boot logger was not allowed to start it: SELinux, noexec), says nothing about
    the binary: it is only noted, and the hardware decides; `absent` is noted too.

  The crash of the camera provider those cause stays UNKNOWN in `crashes` in VMware, with a note that points at
  `cameras`, so one cause gives one FAIL. SW items are judged everywhere.
* **Deferred probes.** `-517` / `EPROBE_DEFER` lines (a probe that is retried later; on the SB3 surface_hotplug logs
  one before lapis's pinctrl-icelake loads) are never counted as serial or SAM errors (wave 5, A-L1).
* **Keyboard base.** When it is detached (`vendor.gb.dtx.base`, the gb_power `get-mode` reply, or `DTX ready: base
  ...`), the keyboard, touchpad, BAT2, dGPU and (review 2026-10-29) posture items are UNKNOWN instead of FAIL.

| # | id | DEVICE-STATUS | PASS when | FAIL when (UNKNOWN otherwise) |
|---|---|---|---|---|
| 1 | kernel | header, K15.md | `Linux version` / report `uname_r=` is the k15 release | another release (k14 is named) |
| 2 | cmdline | rows 9-12, 39, 47; cl. 19 | `8250.nr_uarts>=1`, `gb.hw=native`, `snd_intel_dspcfg.dsp_driver=3`, `reboot=pci`, `module_blacklist` with tpm + spi_intel_pci, `efi_pstore.pstore_disable=1` | any of them missing (notes: `gb.nouveau=0`, `gb.vnni=0`, `gb.overlay=1`) |
| 3 | gbinit | modules.load | no `FAILED x.ko` (off the laptop: only `No such device`, e.g. surface_gpe's DMI check) | any other insmod failure |
| 4 | serial8250 | row 12; cl. 19 | `Serial: 8250/16550 driver, N ports` (N>=1), no `dw-apb-uart ... -22`; on the laptop also an MMIO UART in `/proc/tty/driver/serial` | `-22`, a serial IRQ failure (not a `-517` deferral), or no banner although the log starts at 0 s |
| 5 | ssam | rows 12, 18, 42; cl. 19 | `serial surface_serial_hub bound: <dev>` or `SAM firmware version`, no SAM errors (`-517` lines are deferrals) | bound `none` (cause hints: no `8250.nr_uarts`, `-22`, module) |
| 6 | ssam_registry | rows 18, 19, 25, 42 | registry + hub loaded and at least one SAM client (045E HID inputs, BAT1/BAT2/ADP1, DTX) | module missing, or no client |
| 7 | keyboard | row 18; cl. 1 | `Microsoft Surface 045E:09AE Keyboard` present, not GAMEPAD in `dumpsys input` | absent with the base attached, or classified GAMEPAD (Fn BTN_0) |
| 8 | touchpad | row 19; cl. 2, 20 | `Microsoft Surface 045E:09AF Touchpad` present | absent with the base attached |
| 9 | batteries | row 42 | BAT1 + ADP1 (+ BAT2 unless detached) in the power_supply uevents | missing supplies (in a whole list; a cut list: UNKNOWN), `gb_power: no battery present` |
| 10 | dtx | row 25; cl. 16 | `gb_power: DTX ready` or a `vendor.gb.dtx.base` value | neither, or the DTX device failed (UNKNOWN when there is neither a getprop section nor a kernel log from the boot) |
| 11 | buttons | row 23; cl. 11 | two `gpio-keys` input devices (power button, volume rocker; one is noted); evidence: gb_hw's `gb_hw: buttons: ...` result, `vendor.gb.buttons`, the diag line `gb_hw: diag: buttons MSHW0040:00 driver=...` and the summary's `buttons:` line (wave 5, A-H1); without a whole `== input devices` list the summary's `gpio-keys inputs: N` (N >= 1) counts | gb_hw's `FAILED` result (kmsg line, `vendor.gb.buttons=FAILED ...` or the summary's copy; it names the GPIO controller INT3455 and whether pinctrl_icelake loaded), `absent` (no MSHW0040:00 platform device: property or summary), or no gpio-keys device in a whole input list; UNKNOWN without any `gb_hw: buttons:` line or property (an image before wave 5, or a log that ends before `on boot`) or with a cut input list |
| 12 | posture | rows 18, 24, 25; cl. 1 | gb_hw diag's `gb_hw: diag: posture: device_state mCommittedState=...name='OPEN'` (or another state but CLOSED) and the built-in keyboard/touchpad (`045E:09AE`/`09AF`) `Enabled: true`; (review 2026-10-29) without those kmsg lines (an avc-flooded ring loses them) the summary's `posture:` line of the first snapshot after boot_completed (`mCommittedState=OPEN ... \| disabled by Android: none`); an applied override (`gb_hw: posture: vendor.gb.posture=open -> cmd device_state state 2: ok`) is noted: closing the lid then no longer enters CLOSED (wave 5, A-M1); `reset` is noted as cancelled; a later snapshot with another state, and a keyboard/touchpad the kernel inhibited, are noted, not judged | Android disabled the built-in keyboard or touchpad (`Enabled: false`, or named after `disabled by Android:`), or the device state is CLOSED at diag time / in that summary; both name the one-boot recovery `GB_PROP_1=vendor.gb.posture=open` (FLASHING.md); UNKNOWN without either source (an image before wave 5, Android not up, or a log that ends before boot_completed + 45 s), with the base detached, or with the base in tablet/studio mode (`vendor.gb.dtx.mode`: the keyboard is off there by design) |
| 13 | rapl | row 1; cl. 3 | `gb_hw: rapl-snapshot: firmware ...` + `gb_hw: rapl: mode=...` | `cannot read MSR`, `no intel-rapl`, bad values |
| 14 | suspend | row 46; cl. 11 | `PM: suspend exit` (or suspend_stats success>0) | `Some devices failed to suspend`, freezing failed, suspend_stats fail>0 |
| 15 | i915 | row 14; cl. 21, 23 | 00:02.0 8086:8A52 bound to i915 + `Finished loading DMC firmware` | not bound or missing from a whole `== pci` list, DMC failed (00:02.0 absent from a cut list: UNKNOWN) |
| 16 | display | row 15; cl. 4, 21 | `gb_hw: backlight: intel_backlight`, i915 owns card0, SurfaceFlinger not crashing (notes: the [white-veil counter](#white-veil-counter-b-1-b-2), PSR/underrun hint) | any of them missing (a line whose section is missing: UNKNOWN) |
| 17 | ipts | row 21 | `IPTS 045E:xxxx` HID + `IPTSD Virtual Touchscreen` (+ Stylus) | no IPTS HID, no iptsd devices, 3+ iptsd stops/reloads |
| 18 | wifi | row 37; cl. 23 | `iwlwifi ...: loaded firmware version ... op_mode`; evidence also the summary's `wifi:` line | `no suitable firmware found` etc., no load line; **on any machine** a `FAILED` start probe in the summary's `wifi: ... link:` part (`wpa_supplicant FAILED: CANNOT LINK ...`: Wi-Fi cannot scan or connect; `hostapd FAILED`: no hotspot; wave 5, C-2/C-3) |
| 19 | bluetooth | row 38; cl. 23 | `Firmware loaded in` / `Device booted in` / `Firmware revision`; evidence also the summary's `bluetooth:` line | `.sfi` not found (named), firmware errors, no hci0 |
| 20 | audio_card | rows 39-41; cl. 22 | ALSA card id `sofhdadsp` (`/proc/asound/cards`, summary, audio-b* file); with `snd_intel_dspcfg.dsp_driver=1` (summary `audio dsp_driver=1` or the command line) the legacy HDA card id `PCH` (wave 5, C-4) | no such card (hints: SOF waits for i915; the one-boot legacy fallback `dsp_driver=1`) |
| 21 | audio_dras | row 39; cl. 22 | DRAS `primary_soundcard_name: Some("sofhdadsp")` (`Some("PCH")` with `dsp_driver=1`) | another name; on the laptop also 4+ DRAS starts with the card present |
| 22 | sensors | rows 27-28; cl. 10 | `GB_SENSORS: waited ... missing: none` or IIO names cros-ec-accel + cros-ec-light | missing devices, `no ISH` |
| 23 | cameras | row 29; cl. 9 | 00:05.0 8086:8A19 bound to intel-ipu6, no firmware error, sensors found / `Connected N cameras` | **on any machine** the camera HAL does not load (logcat `Could not load /odm/lib64/hw/camera.sb3.so ...`, e.g. `cannot find "n34_0_Box_C" from verneed[0]`: wave 5, C-1); on the laptop: 00:05.0 missing from a whole `== pci` list or not intel-ipu6, `intel/ipu/ipu4p_fw.bin` failed, no sensor (with the boot's kernel log; else UNKNOWN), sensor errors |
| 24 | dgpu_power | rows 17, 46; cl. 11, 14, 17 | the 10de VGA function `state=D3cold` (in any snapshot) | D0 while idle, `runtime=error`, no dGPU with the base attached |
| 25 | nouveau | row 17; cl. 14, 17 | Auto/NVIDIA: nouveau bound and `vendor.gb.nouveau.result=clean/healthy`; Intel only: no driver | `unhealthy:*`, `insmod-failed:*`, `bind-failed`, `crashloop`, `refused:*`, a driver in Intel mode. Exception (wave 5, B-4): `refused:model` with a driverless dGPU whose id is not a TU117 (`0x1f80`-`0x1fff`), i.e. a 15-inch SB3, is UNKNOWN by design (dgpu_power must still show D3cold); on a TU117 it stays FAIL (a gate bug) |
| 26 | dgpu_report | row 17; cl. 14, 18 | `== gb_dgpu_report report n=` present (notes: payload state, other reports) | missing after 120 s, firmware mismatch, payload mounted but files missing |
| 27 | gfx_switching | row 17; cl. 15 | `ro.gfx.driver.0=gb.sb3.gfxdriver`, vulkan intel, egl angle, gb_power api 1 + running + `get-mode` ok, `gfx_active` set, 0 HWUI queueCount aborts | any of them wrong |
| 28 | gatekeeper | wave 4 | `gb_gatekeeper: registered ...IGatekeeper/default` | never registered, 3+ exits, `Could not find Gatekeeper device` |
| 29 | zram | fstab / mmd | `Adding ...k swap on /dev/block/zram0` / summary `swaps:` | zram/swapon errors, or none after boot completed (mmd starts it after completion) |
| 30 | vnni | cl. 24, K15.md | `-- gb_vnni`: `irqon == emulated` (notes: counts per snapshot, top comms, adler32 RIPs) | not equal; k15 without the node |
| 31 | libz_bind | wave 5, vnni-perf | `processing action (ro.{system,vendor}.build.fingerprint=... && early-init) from (/odm/...)`, no failure | a `/odm/vnni` / libz.so command failed or the trigger was rejected (`gb.vnni=0` = UNKNOWN) |
| 32 | fsverity | cl. 24 | summary `ext4 feature present` (or `Enabling ext4 verity on .../userdata`) | absent, `FS_STAT_ENABLE_VERITY_FAILED`, tune2fs failure |
| 33 | apgetty | cl. 24 | `desktop.apgetty.enable` not 1 and the service not running | on |
| 34 | slotsync | cl. 24, OTA.md | no `gb_slotsync: HOLD slot` | a HOLD |
| 35 | esp_bringup | cl. 24, FLASHING 8 | `absent (gb.overlay=1 not set)`, or applied with rc=0 (refused single settings noted) | `refused, nothing applied`, a rewrite/overlay FAILED, rc != 0 |
| 36 | firmware | cl. 23 | no required `Direct firmware load ... failed` (optional: i915 GuC/HuC, regulatory.db, iwl-debug-yoyo, .pnvm, rtl_nic, BT .ddc, iwlwifi `.ucode` API fallbacks unless iwlwifi logged `no suitable firmware found`) | any required file missing (named) |
| 37 | crashes | all | no tombstone, fatal signal, Java fatal, ANR, 3+ abnormal init-service exits, kernel BUG/Oops/WARNING/panic | any; off the laptop, crashes of services that need the missing hardware (SurfaceFlinger, allocator, camera, audio, ...) are UNKNOWN, but kernel warnings always FAIL (a camera provider that exits because its HAL does not load gets a note pointing at `cameras`, C-1) |
| 38 | selinux | all | no enforced (`permissive=0`) denial touching our components (counts and top denials as notes) | an enforced denial of gb_*, /odm, iptsd, vnni, gatekeeper, nouveau, dgpu, gfxdriver, sb3, surface, vendor_init contexts |
| 39 | boot_time | all | `processing action (sys.boot_completed=1)` (time given; slow above 120 s) | not completed by a snapshot at >= 180 s (VMware: UNKNOWN, the native tree never completes there) |

Steps that are not visible in the log are listed at the end of TRIAGE.md: checklist 5, 6, 7, 8, 12, 13, 15, 16, 18
and 20, plus the interactive parts of 1 and 2.

**What is inferred.** Nothing has run on the laptop yet. The hardware patterns come from the kernel drivers'
messages and our own scripts and daemons, and none of them has been seen from this machine: i915 DMC, iwlwifi,
btintel, SOF, ipu-bridge/IPU4P, `SAM firmware version`, `PM: suspend entry/exit`, gb_power, gb_hw, gb_iptsd,
gb_sensors, gb_gatekeeper, gbbringup and the init `processing action` format. Expect to adjust a few patterns after
the first real `gblog\`. The tests pin the current behavior, so any change shows up there. The wave-5 summary lines
were checked against the real VM-1 logs replayed through the new `GB_BOOTLOG_SH` (fixprep bootlog), not yet against a
boot of a wave-5 image; gb_hw's `buttons`/`posture` lines come from platform's fake-sysfs harness. (review 2026-10-29)
`make_fixtures.py` writes the whole `cd94022f` summary (every wave-5 line, derived from the scenario's own logs and
properties), and `test_fixture_summary_follows_script` checks its line order against the script's top-level `echo`s
in `C:\GB-x86\build_esp.py` (read-only; skipped when that file is missing).

## Wave-5 summary lines

`GB_BOOTLOG_SH` `cd94022f...` (fixprep bootlog, wave 5) adds these lines to `== summary`. Every prefix is a summary key,
so no `-- ` block can swallow them. All wave-3 lines keep their text and order. An older log simply lacks them.

| Summary line (example) | From | What the analyzer does with it |
|---|---|---|
| `kernel log: this dump from 789.187255 s (1343 avc lines), the first snapshot's copy without avc lines 0.000000-49.022414 s: ...` | A-L1 | key only; it says how far back the summary's kernel lines reach (early lines are no longer lost to the avc flood) |
| `deferred probes (-517, retried later; expected on the SB3): N line(s): <dev>;` | A-L1 | key; serial8250 and ssam never count `-517` lines as errors |
| `buttons: MSHW0040 bound to soc_button_array; INT3455 pinctrl bound; gpio-keys inputs: 2; <gb_hw line>` | A-H1 | evidence of `buttons`; (review 2026-10-29) its `FAILED` gb_hw result and `MSHW0040 absent` FAIL, its `gpio-keys inputs: N` counts when the input list is cut |
| `posture: mCommittedState=.. mBaseState=.. mOverrideState=.. \| disabled by Android: .. \| inhibited by the kernel: ..` | A-M1 | (review 2026-10-29) `posture` judges it (first snapshot after boot_completed) when gb_hw's diag kmsg lines are missing; later snapshots and kernel inhibits are noted |
| `posture override: vendor.gb.posture=<v> persist.vendor.gb.posture=<v>; <gb_hw's last action line>` | A-M1 recovery | not parsed (see below) |
| `audio dsp_driver=N (3 SOF, 1 legacy HDA); gb_audio: <last 2 GB_AUDIO lines>` | C-4 | `audio_card` / `audio_dras` expect card id `PCH` when N is 1 |
| `i915 white veil: drmhwc LOG_GAMMA_LUT lines: N, gb_ignore_user_gamma=.., hwc: use_log_gamma_lut=.. persistent_hdr=.. force_color_mode=..` | B-1, B-2 | notes in `display` (below) |
| `i915 PSR/underrun lines: N` | B-3 | N > 0 (or raw i915 FIFO underrun / PSR error lines in an older log): `display` notes `GB_MODOPTS_i915=enable_psr=0 enable_fbc=0` with `gb.overlay=1` |
| `wifi: wlan0 \| wpa_supplicant=.. wifi_hal=.. \| link: wpa_supplicant ok; hostapd ok` | C-3, C-2 | evidence of `wifi`; a `FAILED` probe FAILs `wifi` on any machine (below) |
| `bluetooth: hci0 \| hal=..` | C-3 | evidence of `bluetooth` |
| `camera: provider=.., exits seen: N, HAL load failures: N \| lcam-probe link: ok` | C-3, C-1 | `cameras` FAILs on any machine when N > 0 in any snapshot (review 2026-10-29), like the logcat line itself; an `lcam-probe link: FAILED: CANNOT LINK ...` is noted (below) |
| `linker: CANNOT LINK lines: N logcat, N kernel; dlopen failed lines outside /data: N` + `-- linker failures` | C-3 | key and block only (read it when a service crash-loops) |

The collector (`Get-GooglebookLogs.ps1`) leaves the whole summary block unchanged in the redacted share zip: on the
replayed VM-1 boot all 12 lines survive and the analyzer gives the same 39 statuses on the private and the shared
copy (2026-10-29 integration check); the same holds for the regenerated `sb3_pass`, `sb3_fail` and `sb3_detached`
fixtures, which now carry every line (review 2026-10-29: 0 of 332 summary lines changed, 0 of 72 wave-5 lines, no
synthetic secret in the share copy's triage).

### Camera HAL (C-1)

The wave-3 camera HAL `/odm/lib64/hw/camera.sb3.so` and its libcamera libraries were laid out so that bionic could
not map them (`cannot find "n34_0_Box_C" from verneed[0]`), so the camera provider exited on every native boot. That is
software, the same on any machine. Since wave 5, `cameras` is FAIL on VMware too when logcat has
`Could not load /odm/lib64/hw/camera.sb3.so` or `Could not load camera HAL module`; the first two such lines are the
evidence. (review 2026-10-29) The summary's `HAL load failures: N` counts the same line in the whole logcat, so N > 0
in any snapshot FAILs too (with that summary line as evidence) when the snapshot's last 6000 logcat lines no longer
hold it. `lcam-probe link: FAILED: CANNOT LINK ...` alone is only noted (the probe tool links the same libcamera
libraries, but it is not the HAL). With the camera fix the expected VM-1 result is: no such line, `cameras` UNKNOWN
(no IPU4P in VMware), the summary's `camera: provider=running, exits seen: 0, HAL load failures: 0 | lcam-probe link:
ok`.

### Wi-Fi link (C-2, C-3)

Every summary probes `wpa_supplicant -v`, `hostapd -v` (and `lcam-probe -h`) under bionic, 5 s cap. A
`wpa_supplicant FAILED: CANNOT LINK EXECUTABLE ...` (before the keymint-wifi fix: `cannot locate symbol
"CRYPTO_BUFFER_dup_ref"`) means Wi-Fi
cannot scan or connect on any machine, so `wifi` is FAIL even in VMware; a failure of `hostapd` alone is reported as "the
Wi-Fi hotspot cannot start". `link: wpa_supplicant ok; hostapd ok` changes nothing (in VMware `wifi` stays UNKNOWN).
(review 2026-10-29) Not every `FAILED` is a broken binary: `FAILED (exit 124): ` means the probe got no answer within
5 s, and `FAILED (exit 126): ... Permission denied` means the boot logger (`u:r:shell:s0`) was not allowed to run it
(an enforcing SELinux policy or a noexec mount). Both are only noted ("the ... start probe gave no answer ... not
judged"), so check them by hand (`adb shell /vendor/bin/hw/wpa_supplicant -v`). `CANNOT LINK`, a crash (`exit 139`,
`exit 134`), `Exec format error` and every other answer stay FAIL; `absent` is noted.

### Power button and volume keys (gpio-keys, A-H1)

gb_hw's `gb_hw_buttons` service binds `MSHW0040:00` to `soc_button_array` once lapis's `pinctrl-icelake` drives the
INT3455 GPIO controller, and logs one `gb_hw: buttons: ...` kmsg line (`vendor.gb.buttons` holds the short result).
Expected on the laptop: `soc_button_array bound MSHW0040:00 after <N> ms waiting for INT3455:00, 1 bind write(s): 2
gpio-keys input(s)` and two `gpio-keys` devices in `== input devices`. In VMware: `no MSHW0040:00 device ... nothing to
do`, `vendor.gb.buttons=absent`, item UNKNOWN. On the laptop `absent` is a FAIL (ACPI `\_SB.MSBT` missing).
(review 2026-10-29) The summary's `buttons:` line is evidence too, and decides when the kmsg line and the property are
gone: its gb_hw result with `FAILED` FAILs, `MSHW0040 absent` FAILs on the laptop, and its `gpio-keys inputs: N`
(N >= 1) passes the item when the snapshot is cut inside `== input devices`.

### Posture override (A-M1)

`posture` reads gb_hw diag's report (`gb_hw: diag: posture: device_state ...` and the built-in devices' `input ...
Enabled:` lines, written once at boot_completed + 45 s). (review 2026-10-29) A permissive boot floods the kernel ring
with avc lines, so those kmsg lines can be gone from every snapshot's `== dmesg`; then the summary's `posture:` line
of the first snapshot taken after boot_completed decides (`mCommittedState=CLOSED`, or a built-in `Microsoft Surface
045E:09AE Keyboard` / `045E:09AF Touchpad` after `disabled by Android:`, FAILs). A snapshot before boot_completed never
judges. Later snapshots with another state (the lid closed, the base flipped) are noted, not judged, and so is a
built-in device the kernel inhibited (lapis's touchpad inhibitor while typing, gb_power's studio mode). With the base
detached, or with `vendor.gb.dtx.mode` tablet/studio and the keyboard disabled, the item is UNKNOWN: the keyboard is
off there by design. The recovery for "keyboard and touchpad dead with the lid open" is one boot with
`GB_PROP_1=vendor.gb.posture=open` and `gb.overlay=1` (or `setprop vendor.gb.posture open`). The summary's
`posture override:` line is not parsed. Read it by hand after the recovery:

* `posture override: vendor.gb.posture=open persist.vendor.gb.posture=; gb_hw: posture: vendor.gb.posture=open -> cmd
  device_state state 2: ok`: the override is active (the `posture` item also notes it from gb_hw's kmsg line; a
  `... -> cmd device_state state reset: ok` is noted as "posture override cancelled");
* `posture override: vendor.gb.posture= persist.vendor.gb.posture=; ` (nothing after `;`): no property set, so gb_hw's
  posture service never ran; the normal case;
* `... no override requested ...` after `;`: the property is `off`/`none`, nothing applied;
* `... no such device state (have: ...)` or `... is not a device-state name, ignored`: the override did not apply
  (check the value: letters and `_` only, e.g. `open`).

While an override is active, closing the lid does not enter CLOSED (no sleep on lid close). `setprop
vendor.gb.posture reset` undoes it; the `persist.` variant repeats on every boot until it is `reset`/`off`.

### White-veil counter (B-1, B-2)

Before wave 5 the summary counted `drmhwc: Missing`, which the composer's real threadtime line (`V drmhwc  : Missing
optional LOG_GAMMA_LUT property, ...`) never contains, so the count was always 0. `display` therefore also counts the
raw logcat lines with tag `drmhwc` and uses the larger number. The line appears only with
`vendor.hwc.use_log_gamma_lut=true`, i.e. while lapis's composer colour settings are active. When the count is above 0,
`display` notes the recovery from display's B-2 analysis: one boot with `GB_PROP_1=vendor.hwc.drm.force_color_mode=-1`,
`GB_PROP_2=vendor.hwc.drm.persistent_hdr=false`, `GB_PROP_3=vendor.hwc.use_log_gamma_lut=false` and `gb.overlay=1` (keeps
Night Light), and if it is still pale `GB_MODOPTS_i915=gb_ignore_user_gamma=4` (drops i915's colour matrix, so Night
Light stops working). `=3` changes nothing on the native image, because the composer clears the gamma LUT on every
commit there. The wave-5 line's `hwc:` part is noted as the three composer switches; a wave-5 image should show
`lines: 0` and `use_log_gamma_lut=false persistent_hdr=false force_color_mode=-1`. The status never depends on it
(whether the panel looks pale is a checklist step for a person).

### OTA gate

`GB_BOOTLOG_SH` `cd94022f` has no summary line for the OTA gate (wave 5b), and the analyzer has no item for it. On a
build that is not the one the ESP was made for, the gate shows up elsewhere in the snapshot: `gb_otagate: UNVALIDATED
build: booted ..., ESP built for ... (token ...); skipped: ...` in the kernel log and `ro.vendor.gb.ota_gate=unvalidated`
in `== getprop`. On such a build read that line before the items that use the skipped `/odm` parts (e.g.
`libz_bind`); the analyzer does not relate them to the gate. Update the ESP to re-validate.

## Tests

```bat
python -I C:\GB-build\native\hwtest\tests\test_analyze_gblog.py
```

47 tests take about 30 s (longer while the machine is busy). Their outputs go to `out\tests\<name>\TRIAGE.md` and
`triage.json`.

1. **Real VMware native-smoke evidence**, read in place and never modified (a test is skipped when its folder is
   missing). These are all the gblog folders there are (2026-10-29): `dgpu\w4`, `vnni-perf\w5\evidence` and
   `D:\gb-w5c` hold serial-console logs only, which the analyzer does not read.

   | Evidence | Image | Result | Notes |
   |---|---|---|---|
   | `dgpu\w3\evidence\build\vm1\gblog` (t40-t900) | k15, wave 3 | 14 PASS, 1 FAIL, 24 UNKNOWN | 19 of the 20 HW items UNKNOWN; `cameras` FAIL: the camera HAL does not load (`cannot find "n34_0_Box_C"`, C-1), a real software defect of the wave-3 image, fixed in wave 5; SW PASS except 5 justified UNKNOWNs: gatekeeper (image before wave 4), libz_bind (before wave 5), zram and boot_time (the native tree never completes a boot in VMware), crashes (only SurfaceFlinger, the gralloc allocator and the camera provider crash; the camera provider's crash is noted as C-1) |
   | `dgpu\w3\evidence\build\vm1b\gblog` | k15, `gb.overlay=1` | 14 / 1 / 24 | as vm1; esp_bringup PASS: 3 changes applied, 2 single settings refused, all as designed by that test |
   | `dgpu\1b\evidence\w2fix\vm1\gblog`, `w2\vm1\gblog` | k14, before wave 3 | 8 / 6 / 25 | real FAILs: kernel k14, no `8250.nr_uarts`, no 8250 banner, apgetty on and crash-looping (the reasons wave 3 changed both), cameras (C-1, as in wave 3); vnni and esp_bringup UNKNOWN (no summary block yet) |
   | w3 vm1 with `--env surface` | | 12 / 19 / 8 | the same log judged as if it came from the laptop: the missing hardware FAILs, including serial8250 (no MMIO UART); buttons and posture UNKNOWN (an image without gb_hw's wave-5 lines) |

   Every evidence line of the serial item is checked to be the exact line at `file:line`. The CLI results of the same
   four folders are in `out\evidence-20261029\<name>\` (`TRIAGE.md`, `triage.json`, `statuses.json`).
2. **Synthetic Surface Book 3 fixtures** (`fixtures\`, written by `make_fixtures.py` in the GB_BOOTLOG_SH layout;
   their summary blocks are derived from the scenario's own logs the way the shell script derives them, with every
   `cd94022f` line since the 2026-10-29 review):

   | Fixture | Result |
   |---|---|
   | `sb3_pass` (boot 3: t40, t420 + audio diag; boot 2 is an older boot) | 39 PASS |
   | `sb3_fail` (boot 1: t40, t420) | 38 FAIL (incl. buttons: gb_hw's FAILED bind; posture: CLOSED at diag time and in the summary; the white-veil note from 4 real drmhwc lines with lapis's composer values true/true/13); boot_time PASS (completed late, noted as slow) |
   | `sb3_hang` (t40, t180, t900; SurfaceFlinger crash loop) | display, crashes, boot_time FAIL; nouveau, zram, suspend, posture (no boot_completed: no gb_hw diag, and the summary's `posture: device_state n/a`) UNKNOWN; the rest PASS |
   | `sb3_detached` (base detached) | keyboard, touchpad, dgpu_power, nouveau, posture (state TABLET, no built-in keyboard to judge) UNKNOWN; batteries PASS with BAT2 noted; the rest PASS |
   | `sb3_pass` with `--env vmware` | 20 HW UNKNOWN, 19 SW PASS |

   Further cases: the keyboard classified as GAMEPAD, a btintel failure line with "Failed" before "firmware", a
   missing optional BT `.ddc`, cut and empty snapshots (a cut before or inside `== pci` is no evidence for i915 or
   cameras; a whole list without 00:02.0 / 00:05.0 still FAILs), `--boot`, single-file and parent-folder input, report
   sub-sections (`== pci`, `== asound`) inside the dGPU report, the previous boot's `dmesg-live.prev` (a fake panic
   there must not count), redaction (fixture serials, a MAC, an account e-mail and the partition UUID must not appear
   in any output), and the CLI (`--strict` exit codes, `--json`, default output in the current directory, errors).
   Wave 5: the camera HAL load failure on the real w3 log (and the C-1 note in `crashes`); buttons with one input,
   posture CLOSED then recovered by the override; `TestWave5Bootlog` (summary keys, `-517` deferrals, the real
   drmhwc line and the B-2 recovery text, PSR/underrun counts, the Wi-Fi start probe in both environments, the legacy
   HDA card `PCH`). Review 2026-10-29: the fixture summary's line order against the script's `echo`s
   (`test_fixture_summary_follows_script`); posture from the summary line (OPEN, keyboard disabled, CLOSED, a
   Bluetooth keyboard, no input service, before boot_completed only, kernel inhibit, a later change, tablet/studio
   mode, the EventHub `(aka ...)` suffix, override `reset`); buttons from the summary line (FAILED, absent, an image
   without the fix); the camera summary count (also from an older snapshot only) and an lcam-probe link failure; the
   Wi-Fi probe's timeout / permission-denied (noted) vs crash / `Exec format error` (FAIL); `dsp_driver=-` falling
   back to the command line; a newest file cut inside `== pci` or `== input devices` next to a whole older snapshot,
   and a lone cut list (UNKNOWN, never FAIL; also `== drm/input/power` for batteries).
3. `python -I make_fixtures.py --check` (also run by the tests) fails when the committed fixtures differ from the
   generator. Regenerate them with `python -I make_fixtures.py`.
4. The fixprep areas' own analyzer checks also pass against these files (2026-10-29): display
   `tests\test_b4_analyzer.py` 18/18, platform `tests\test_gblog_platform_checks.py` 13/13 (10 mutants killed),
   bootlog `tests\test_analyzer_on_new_summary.py` 21/21 and `tests\mutate_analyzer.py` 15/15 killed. After the
   review (2026-10-30): display 18/18, bootlog 21/21 and 15/15 killed (unchanged); platform's scenario matrix 9 of 10
   as before, `posture_none` (diag lines removed) is now PASS by design because the fixture's summary has a
   `posture:` line; platform's patch-04 reverse-apply control and 6 of its 10 mutant anchors (G03, G04, G06, G07, G09,
   G10) no longer match the reworked `c_buttons`/`c_posture`. With those anchors moved to the new code its matrix kills
   9 of 10; G10 (no UNKNOWN without posture lines) is equivalent there, because `posture_none` now has a summary
   `posture:` line, and this suite kills it. The review's own 28 mutants of the new code are all killed.

## Files

| File | Purpose |
|---|---|
| `analyze_gblog.py` | the analyzer (parser, 39 checks, Markdown/JSON output, redaction) |
| `make_fixtures.py` | generator of the synthetic fixtures (scenarios sb3_pass, sb3_fail, sb3_hang, sb3_detached) |
| `fixtures\<scenario>\gblog\` | the generated fixtures (no real data: SYNTH-* values only) |
| `tests\test_analyze_gblog.py` | the test suite |
| `out\tests\` | test outputs (regenerated on every run) |
| `out\evidence-<date>\` | the analyzer's results on the real VMware gblog folders |
| `prev\pre-analyzer-patches-20261029\` | every file as it was before the wave-5 analyzer patches (camera 04, platform 04, bootlog 03, display B4), with `SHA256SUMS` |

Wave-5 history (2026-10-29): the four fixprep analyzer patches were applied in the order camera
`04-hwtest-analyze_gblog-camera-hal.patch`, platform `04-hwtest-analyze_gblog-buttons-posture.patch`, bootlog
`03-hwtest-analyze_gblog.patch`, display `B4-analyze_gblog-refused-model.patch` (each `git -c core.autocrlf=false apply
--whitespace=nowarn`, checked first; any order gives the same files), then `python -I make_fixtures.py`. Integration
fixes on top: the `pci_whole` rule for i915 and cameras (platform's larger `sb3_pass` fixture moved `== pci` out of the
300-line cut of `test_truncated_and_empty_snapshots`), the C-1 note in `crashes`, and the white-veil recovery text
aligned with display's B-2 analysis (bootlog's text recommended `gb_ignore_user_gamma=3`, which does nothing natively).

---

## Proposed section for the repository docs

This section was not added to the repository (it was off limits for this task). It fits FLASHING.md as a new
subsection at the end of section 4 "Logs", after "First-boot checklist". It assumes the script is published as
`tools/analyze_gblog.py`; change the path to wherever it ends up.

> ### Automatic triage (`analyze_gblog.py`)
>
> `tools/analyze_gblog.py` reads one boot's `gblog\boot<N>-t<T>.txt` snapshots (and `audio-b<N>-*`) from the stick
> and writes `TRIAGE.md`. Each item of the [hardware validation checklist](DEVICE-STATUS.md) and the software around
> it gets PASS, FAIL or UNKNOWN with the exact log lines (`file:line`) behind it: kernel and command line
> (`8250.nr_uarts`), the 8250 ports, the Surface Aggregator and its keyboard, touchpad, batteries, charger and detach,
> the power button and volume keys (`gpio-keys`), the posture Android reports (a CLOSED state disables the keyboard),
> i915 and DMC, panel and backlight (with the white-veil hint), IPTS touch and pen, Wi-Fi and Bluetooth firmware and
> whether `wpa_supplicant`/`hostapd` can start, the `sofhdadsp` card (or `PCH` with the legacy HDA fallback) and DRAS,
> sensors, cameras (the camera HAL loads, IPU4P firmware), the dGPU (D3cold, nouveau result, the dGPU report, graphics
> switching), Gatekeeper, zram, the AVX-VNNI counter and libz binds, missing firmware, crashes, SELinux denials and
> the boot time.
>
> ```bat
> python -I tools\analyze_gblog.py E:\gblog -o TRIAGE.md
> ```
>
> It needs only Python 3, writes nothing to the stick and redacts serial numbers, MAC and e-mail addresses, SSIDs,
> account names and the boot partition UUID from what it prints. That makes `TRIAGE.md` safer to attach to a report
> than the raw files, but read it before you post it. Items that need the laptop's hardware are UNKNOWN when the log
> comes from VMware, except two software failures that break the laptop too: a camera HAL that cannot be loaded and
> a `wpa_supplicant` that cannot start. With the keyboard base detached, the keyboard, touchpad, base battery and
> dGPU items are UNKNOWN rather than FAIL. Steps the log cannot show (USB-C, SD card, dock, pen pairing, clock,
> reboot, detach with an app open) are listed at the end for you to try by hand.
