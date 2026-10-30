# Changelog

What each wave changed, newest first. Details per device: [docs/DEVICE-STATUS.md](docs/DEVICE-STATUS.md) ("Changes
since the audit"). Nothing has run on the Surface Book 3 itself yet: every result below is from VMware VMs, QEMU, the
Linux build machine or disk-image files.

## Wave 6 (2026-10-30): the hardware-independent backlog

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
