# Changelog

What each wave changed, newest first. Details per device: [docs/DEVICE-STATUS.md](docs/DEVICE-STATUS.md) ("Changes
since the audit"). Nothing has run on the Surface Book 3 itself yet: every result below is from VMware VMs, QEMU, the
Linux build machine or disk-image files.

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
