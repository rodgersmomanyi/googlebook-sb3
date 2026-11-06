# Changelog

What each wave changed, newest first. Details per device: [docs/DEVICE-STATUS.md](docs/DEVICE-STATUS.md) ("Changes
since the audit"). The first native boot on the Surface Book 3 happened on 2026-10-31 (the wave 8 image; results
per item in DEVICE-STATUS, "First real-hardware results"), the second on 2026-11-01 (the wave 9 image; "Second
real-hardware results"); every other result below is from VMware VMs, QEMU, the Linux build machine or disk-image
files.

## Wave 10 (2026-11-05/06): the second native boot, the battery level, an installer ISO for the current system

* **The second native boot** (the wave 9 image `e0825ee1…`, one start on the Surface Book 3 13.5" with the keyboard
  base attached, 2026-11-01; analyser 38 PASS, 2 FAIL, 1 UNKNOWN): the Surface Aggregator re-bind of wave 9 works.
  The keyboard base, touchpad, both batteries, the charger and the detach device appeared about 31 s after the start
  (the first probe still fails on kernel build #16, as expected), and the keyboard, the touchpad and sound worked;
  Wi-Fi associated, Bluetooth started, every sensor was there and the laptop posture stayed rotation-locked;
  `boot_completed` after 92.1 s (the first start of a freshly written stick). Failed: the rear camera's focus motor
  (DW9719, the same `-121` write error as on the first boot) and the dGPU report (no report file yet: it was still being written when that start's logs end, see below); no suspend
  happened in the logged time. **New bug (HIGH):** Android's battery level read 0 % with the charging symbol, also on the charger,
  while the combined battery read about 55 %, and after unplugging the charger animation kept flickering.
* **Battery level:** Cause (a disassembly of lapis's desktop health HAL and the second boot's log): the HAL takes its charge counter from the combined battery's `charge_now`, which the kernel's test_power ties to the capacity (a percentage), and shows charge_now × 100 / charge_full; `gb_power` published charge_full in µAh, so the level came out as 0 %. The flicker: the dGPU report runs `gb_power -o` 30 times 10 s apart, and every run briefly rewrote the combined battery's start values (no battery, on mains). Fix (`gb_power` only, native image): the combined battery in percent units (charge_full 100, so the HAL's level equals the combined level), `gb_power` the only writer, `gb_power -o` read-only, a fixed write order. Tested on the build machine (the gb_power unit tests, 1,081 checks, also under the address and undefined-behaviour sanitisers; a battery harness against a model of the HAL, 28 runs without a violation, 10 of 10 mutants killed), with the hwtest analyser's new `battery_hal` item (it finds the bug in the second boot's log) and in VMware (the HAL's level equals the battery's). Known limit until wave 11: Android does not yet shut down by itself at a real 0 % (the combined battery always reports a normal level band), so do not run the battery below about 10 %. Untested on the laptop.
* **dGPU report:** The second boot's FAIL came from the analyser's 120 s rule: the report is requested at 40 s and samples the power for about 5 minutes (30 runs of `gb_power -o`, 10 s apart), and that start's last log file was taken at 237 s, so the report was still being written. The wave 10 analyser judges the run time (PASS for a report of this boot, UNKNOWN while the run is inside its time, FAIL for a refusal, a bad exit, a hang or a report that was written but not collected); on those logs it gives 38 PASS, 2 FAIL (the cameras, and the new `battery_hal` item on the 0 % level), 2 UNKNOWN (suspend, the dGPU report). Keep one start running for 15 minutes (at least 7) so the report is collected.
* **Rear camera focus motor:** The motor gets its power from the rear camera module, through a runtime power link that the camera bridge driver adds only after the motor's device appears. `gbinit` loaded the motor's driver `dw9719` first, so it probed before that link existed, lost power with the sensor and failed to park its lens (`Error writing reg 0x0003: -121` about 1 s after its probe, in every native start); pictures were not affected. The wave 10 image stages `dw9719` but loads it from no list; `gb_hw.sh vcm` (service `gb_hw_vcm`, early-init) loads it once the link exists and reports `vendor.gb.vcm`. Tested on the build machine (128 cases, 17 of 17 guard mutants) and in VMware (no camera there: the loader reports none and exits cleanly).
* **Suspend:** No lid close or sleep request happened in the logged time (the start ended between the 4- and the 7-minute log files); the kernel side is ready (s2idle by default, the lid switch and its wake event registered). The wave 10 analyser judges suspend from more sources (the kernel's `PM:` lines, gb_power's and the touch launcher's resume lines, a closed lid, and the next start's copy of the kernel log, which shows a suspend that never resumed) and counts each SELinux record once; the image's touch launcher logs the deep-sleep (S0ix) time after every resume (`gb_iptsd: resume diag: ... last_hw_sleep=`).
* **Stale notices**: gb_notify no longer shows a notice that is no longer true (left over from an earlier start, the
  update validated meanwhile, another slot, a graphics state that changed, an old detach answer); lasting graphics
  notices are kept until you have seen them (INTERFACES 12.1b, BACKLOG I-W9-1).
* Tooling (no image change): `verify_image.py` checks the wave-9 items that the build generates outside the component
  manifests (V19 whole load lists, V24 the VM's efi_pstore token, V34-V37: the C-6 label line, the refusal stubs, the
  SAM/ISH services, the VM notification queue). The wave-9 outputs pass; the wave-8 image fails exactly these new
  checks.
* **Installer ISO for the current system:** the ISO is rebuilt (`e0bb15d5…`): option 1 writes the wave 10 USB
  image `3b6330d9…` and option 2 installs the wave 10 dual-boot export; wave 9 had no ISO of its own. Tested in
  VMware: T0, T1a/T1b (option 1 wrote a disk byte-identical to the wave 10 USB image), the dual-boot groups G1-G4 (T3 with its refusal cases, T4, T4b, T9) and T8 passed on 2026-11-06 (12 test ids, 378 checks, 0 failed; the same counts as the wave 8 ISO's).
* Tested in VMware: VM-1 on the wave 10 USB image (the analyser 17 PASS, 0 FAIL, 25 UNKNOWN; every wave 2-10 check VMware can show, among them the desktop health HAL's level equal to the combined battery's), a rehearsal of the VM build on a throwaway VM (a fresh first boot, forced and validated update boots: the planted stale notices dropped, this start's notice shown once after the sign-in), `verify_image.py` on the USB image, the export and the VM build, the host suites and every post area test. The long-running VM now runs the wave 10 VM build (written 2026-11-06 after a verified backup of its boot partition; its lock-screen PIN path is unchanged; its check boot passed without a sign-in).

## Wave 9 (2026-10-31/11-01): the first native boot's fixes, notices for the user in front, the last hardware-independent items

* **The first native boot** (the wave 8 image, three starts on the Surface Book 3 13.5"; analyser 29 PASS, 10 FAIL,
  2 UNKNOWN): Android started in 42.8 s; the display, touch and pen, Wi-Fi, Bluetooth, the sound card, the buttons,
  the NVIDIA power saving, the lock-screen service, the VNNI patch and the update check worked. The keyboard base,
  touchpad, batteries, charger and detach did not: on kernel build #16 the Surface Aggregator's first probe runs
  before lapis's GPIO driver is ready and fails for good (`failed to setup IRQ`, an upstream regression since 6.12).
* **Fixes in the image (untested on the laptop):** `gb_hw.sh sam` (service `gb_hw_sam`) binds the Surface Aggregator
  again once the GPIO controller is bound and waits for its devices (`vendor.gb.sam`, a `sam:` line in the boot-log
  summary); `gb_hw.sh ish` (service `gb_hw_ish`) resets and binds the sensor hub once when its firmware did not start
  (`vendor.gb.ish`, an `ish:` line); the unused IR camera driver (`ov7251`, whose probe always failed) is staged but no
  longer loaded; `elogtool` and `cbfstool` are refusal stubs like flashrom; the analyser names these causes, ignores
  the harmless `powertool set-soc-slider` exit and skips a stale previous-boot kernel log. A kernel fix for the probe
  (patch 0114) is prepared for the next kernel build; there is no kernel change in wave 9.
* **Notices** go to the user in front of the screen and wait for the sign-in; the VM build shows "System update held"
  too; GbGpu: tile set up at any sign-in, a real app icon, "Can't detach yet" for the user in front; the louder
  "still not validated" notice says "Previous slot: only before a sign-in". `gb_slotsync` reads `loader.conf` lines
  the way systemd-boot does (a line also ends at a lone CR; exactly one `default` line is kept);
  `efi_pstore.pstore_disable=1` in the VM entry; HAL file labels restored. Test-only: the TU117 cases of the nouveau
  suite also assert that no "model gate" line is logged.
* Tooling (no image change): `verify_image.py` reads the NVIDIA payload's own files (an EROFS/LZ4 reader) and
  compares every one with its input, and checks the packed `modules.alias`/`.dep` (V4b); TB-3 judges the wave-8
  build and the kept wave-6/7 fallback images from a record of what they pack (`tests/w2b/prev_images.json`,
  `--image`), so their expected failures follow later source changes by themselves; the shipped
  Gatekeeper 0.2.0 now has the frozen-vector acceptance record (rebuilt byte for byte) and is no longer exempt;
  `build_esp.py`'s builder floor for an NVIDIA payload rebuild is the project's 1 GB/4 GB rule + margin, with an
  actionable message; the dual-boot `-UpdateEspOnly` reads `loader.conf` default lines as systemd-boot does.
* Tested in VMware: a rehearsal of the VM build (a fresh first boot 51 of 51; a forced unvalidated update 40 of 40:
  the notice held at the login screen, then exactly one for the signed-in user; a validated boot 23 of 23; a held
  update 36 of 36 with the notice in user 10's panel), VM-1 on the wave 9 USB image `e0825ee1…` (every wave 2-9
  check VMware can show; two checks judged after a project decision: 2 more short instruction emulations by init in
  the new label-restoring step, accepted, and a key-store result line outside that boot's log window; the analyser
  17 PASS, 0 FAIL, 24 UNKNOWN), the host suites and every area test. **Not done in wave 9:** the installer ISO is not
  rebuilt (it still installs the wave 8 system; the build needs more free disk space on the host), and the
  long-running VM keeps the wave 8 boot partition until the host has room for its check boot.

## Wave 8 (2026-10-31): kernel build #16, updates into slot A, your own settings kept

* Kernel build #16 (k16): the `gb_vnni` summary escapes task names, so a program can no longer forge lines in the
  boot-log summary (LOW-3), and the Realtek SD-card reader drivers (USB vendor class and PCIe) are built and loaded
  by modalias in the native image, so the base's SD reader works whichever type it is. The KMI symbol list is
  unchanged; lapis's vendor modules load as before. Tested on the build machine and in a throwaway VMware VM (module
  loads, the VNNI emulator known-answer test, the hostile-name test). Published as patches `kernel/patches/k16/`
  (0112 also as `kernel/patches/0006`), the `.config` difference `kernel/config/k16_card_readers.fragment` and
  [kernel/K16.md](kernel/K16.md); no binaries.
* **Updates into slot A now start (finding SLOT-DEFAULT).** systemd-boot matches the `loader.conf` default against
  the entry's file name including `.conf`. The line `default gb-<x>` written so far matched no entry, so the loader
  always started slot B's entry when one existed: an update into slot A was prepared but never started, and a
  boot-menu pick of slot A did not stick. Images, `gb_slotsync` (it rewrites an existing ESP's line once, for the slot
  that booted), `verify_image` (new check V33), the dual-boot `-UpdateEspOnly` check and the installer tests now use
  `default gb-<x>.conf`. Tested on a VMware VM: the default line picks slot A or B, a switch and a menu pick stick.
  Pick an older slot by hand only while the newer build has not reached the lock screen ([OTA.md](docs/OTA.md)).
* **Your own settings stay.** `gb_ui.sh` keeps your own Display size, launcher and one-time values (safety audit W7-S1, W7-S2);
  `build_esp.py` writes `lapis.bin` only with `GB_EXPECT_ESP_SHA256` = the reviewed preview (W7-S4);
  `verify_image.py` V12 checks `gb_ui.sh` in both trees (IA7-1); the VM build's update notice judges
  `cmd notification post` by its output with `gb_notify`'s fallbacks (HS-O5-otagate) and its stale "changes nothing
  else" comment is corrected.
* Sensors: once the Auto-rotate overlay is seen enabled, the one-time first-boot flag is recorded too, so a later
  boot never writes the login screen's Auto-rotate (BACKLOG AL-F2); header wording (AL-X1). Also: a corrected header
  comment in the audio module list, and `build_esp.py` caches its compressed `inject.gz` by the content's sha256
  instead of a Python object id (the output is byte-identical).
* Audit follow-ups (wave 8 safety audit): the VMware ESP rollback (`gb_vmbackup.py restore --esp-only`) refuses a
  backup of the other A/B slot, since after a Google update switched the VM's slot every older ESP backup is the
  wrong rollback (W8-S1); the docs say to boot the previous slot only before a sign-in on the new build (W8-S2) and
  never to type a second `module_blacklist=` at the boot menu (it would replace the TPM / SPI-flash list, W8-S4);
  the dual-boot image test's loader edit accepts both `default` forms, so the ISO test T8 passes with an export made
  before wave 8 as well; the evidence extractor `native/vnni-perf/tools/extract_gbvnni.py` records escaped task
  names (LOW-3 F-2).
* Tested in VMware: a rehearsal of the VM build on kernel build #16 (a fresh first boot 42 of 42; a forced
  unvalidated update 25 of 25, with the notice in user 10's panel and the person's own Display size and screen
  timeout kept; the emulator's known-answer test, 9,235 cases, and a hostile-task-name test, 15 of 15; a validated
  boot 17 of 17; a switch to slot A 11 of 11), VM-1 on the rebuilt USB image (every wave 2, 3, 5, 6, 7 and 8 check
  that VMware can show; the hardware-test analyser 17 PASS, 0 FAIL, 23 UNKNOWN), and on the installer ISO T0,
  T1a/T1b, the dual-boot groups G1-G4 and T8: 378 checks, 0 failed. The long-running VM was updated to the wave 8 VM
  build (kernel build #16) after a verified backup of its boot partition; its check boot passed without a sign-in.

## Wave 7 (2026-10-30), commit a7d8d1e: the last hardware-independent items

* **Settings reach your user (headless system user mode).** Density, HOME, setup-complete and the one-time defaults
  (VM screen timeout, Auto-rotate) are applied to every full user when it is unlocked, also after a late sign-in;
  your later changes to the one-time values are kept (density and HOME are set again at every boot). The Graphics
  app's notification permission is granted once per user. Notices whose post `cmd notification` silently refused
  are now detected and retried once (native) ([ARCHITECTURE.md](docs/ARCHITECTURE.md), [OTA.md](docs/OTA.md)).
* OTA gate: the "System update not validated" notice counts the boots of the same unvalidated build and gets louder
  from the third one ("System update still not validated", naming the count and both remedies); test harness
  follow-ups (the rotation emulation test, the F3 harness's notify post command, safety-misc and tooling tests
  runnable after an integration).
* Audit follow-ups (wave 6 safety and image audits):
  - A new Gatekeeper build now also needs its frozen-vector results from the Android build on lapis's bionic and
    from the reference check.
  - The VMware ESP rollback keeps a copy of what it overwrites.
  - A dual-boot `-UpdateEspOnly` refuses an ESP that would change the lock-screen PIN path.
  - The first-boot Auto-rotate write runs only when the settings overlay is not applied.
  - The test pins for the NVIDIA model-gate files now use their sha256.
  - `build_esp.py` makes the PIN-path check once.
  - `verify_image` V27 now also checks the early `stop apgetty`.
  - The records no longer claim that init logs an ENOENT line for the gbvnni remount.
* Tested in VMware: a rehearsal of the VM build (a fresh first boot, 33 of 33 checks: the new user 10 got its
  settings at its first unlock and user 0 kept its own; three boots of a forced unvalidated update, 17 of 17 each,
  with the boot count and the louder notice at the third in user 10's panel; a validated boot after them, 10 of
  10), VM-1 on the rebuilt USB image (every wave 2, 3, 5, 6 and 7 check that VMware can show; the hardware-test
  analyser 17 PASS, 0 FAIL, 23 UNKNOWN), and on the installer ISO T0, T1a/T1b (whole disk), the dual-boot groups
  G1-G4 (T3 install with its refusal cases, T4/T4b uninstall, T9 off-disk GPT copy) and T8 (the Windows scripts):
  378 checks, 0 failed. The long-running VM was updated to the wave 7 VM build after a verified backup of its boot
  partition.

## Wave 6 (2026-10-30), commit 0c1d750: the hardware-independent backlog

* **Notifications reach the signed-in user.** lapis runs in headless system user mode (user 0 = the login screen, you =
  user 10). Our notices ("System update not validated", "System update held", the NVIDIA notices) were posted for
  user 0 and never shown; they are now posted once for all users. GbGpu's state updates, notification permission and
  Quick Settings tile follow your user; the OTA gate's queued native notice is readable again
  ([OTA.md](docs/OTA.md), [NVIDIA.md](docs/NVIDIA.md)).
* **NVIDIA only on the 13.5" model** (TU117). On a 15" Surface Book 3 the driver is never loaded, the dGPU stays off
  and the Graphics tile says `Intel (NVIDIA not supported)`; boot-menu override `gb.nouveau.model=any`.
* **Rotation per posture:** landscape-locked with the base attached (Android's own laptop policy), Auto-rotate with the
  clipboard detached; Auto-rotate on by default; one `GB_SENSORS: rotation:` log line per posture change.
* **VNNI libz patch hardened:** the patched copies are read-only in every mount namespace; the build refuses a patch
  made for another build or slot ([PERFORMANCE.md](docs/PERFORMANCE.md)).
* **OTA gate follow-ups:** image check V32, an `ota gate:` boot-log line and analyser item, a stock-file report on
  unvalidated builds, and a forced test boot (`build_usb.py --vm-test --vm-test-otagate`), booted in VMware.
* **PIN-path safety:** ESP writes and ESP rollbacks refuse a change of the lock-screen PIN path; frozen Gatekeeper
  test vectors; native builds refuse while the VMware VM runs ([GATEKEEPER.md](docs/GATEKEEPER.md)).
* **Debug boot** for a start that stops before Android (`earlycon=efifb`), and every boot-menu edit now says "press End
  first" ([FLASHING.md](docs/FLASHING.md)). The serial login console is stopped at `on init`.
* **Installer test tooling:** `native/dualboot/tools/gpt_validate.py` no longer reads the holes of sparse test disks
  (on NTFS that held as much free space as the hole until the file was closed, and stopped the wave 6 test run); its
  results are unchanged.
* Tested in VMware: VM-1 on the USB image (every wave 2, 3, 5 and 6 check that VMware can show; the hardware-test analyser 17 PASS, 0 FAIL, 23 UNKNOWN), a forced OTA-gate boot of the VM build (the update notice, a `gb_notify` notice and a GbGpu state change in user 10's panel), and on the installer ISO T0, T1a/T1b (whole disk), T2b/T2c/T2h (refusals), the dual-boot groups G1-G4 (T3 install with its refusal cases, T4/T4b uninstall, T9 off-disk GPT copy) and T8 (the Windows scripts): 402 checks, 0 failed. The other 22 ISO tests passed the same day on the build before the notification fix, which has the same installer.

## Waves 4 and 5 (2026-10-26 to 2026-10-30), commit 870e0c9

Software Gatekeeper (lock-screen PIN) in the VM and both native trees; the build-fingerprint-gated libz patch that
takes zlib's share of the AVX-VNNI emulation off the trap path (VM boot 587.7 s -> 313.6 s); the OTA gate; the fixes
of the native-readiness audit (power and volume buttons, camera HAL linking, Wi-Fi's libcrypto, composer colour
settings, posture diagnostics, a longer boot-log summary).

## Wave 3 (2026-10-26), commit 81de83e

Keyboard-base serial fix (`8250.nr_uarts=4`), kernel build #15 with the reworked `#UD` AVX-VNNI emulator, boot-log
triage summary, the opt-in ESP bring-up configuration `gb\gb.cfg`.

## Wave 2 (2026-10-25), commit 20b6efc

NVIDIA graphics switching (Auto / Intel only / NVIDIA, detach handling, per-app driver package), kernel build #14 and
the installer ISO.

## Wave 1 (2026-10-24), commits 07395fa to 0d94a07

Googlebook OS (lapis) in a VMware VM and the native USB image; kernel build #13 (cameras, sensors, hid-surface,
nouveau opt-in), the image check, flashing and device-status docs; licences.
