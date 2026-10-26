---
name: Native boot report (Surface Book 3)
about: Report how a native boot of Googlebook OS went on the Surface Book 3, with its logs
title: "[native] <short description>"
labels: native-boot
---

<!--
Before you attach anything: the gblog files hold full getprop and logcat dumps. They can contain serial numbers
(ro.serialno and similar), Wi-Fi network names, Bluetooth device names and your Google account e-mail address.
Search the files for them and redact them first. The "== summary" block at the top of each file prints none of these.
-->

## What happened

<!-- What you did, what you expected, what you saw. One problem per report. -->

## Which boot

- Boot medium: USB stick / internal (dual boot) / installer ISO install
- Boot entry used (the title shown in the systemd-boot menu, e.g. "Googlebook OS - Surface Book 3 (USB) (slot B)"
  or "... [NVIDIA test]"):
- Options you added for this boot with `e` (e.g. ` gb.nouveau=0`, ` gb.overlay=1`), or "none":
- Keyboard base attached during the boot: yes / no
- Slot (the "(slot X)" in the entry title):
- Kernel build (`kernel_release` / the `uname_r=` or `krel` line in `gblog\`, e.g. build #15):
- Googlebook build (`ro.build.fingerprint` without the serial-number properties, e.g. CL3B.260622.276.R4):
- Image or export checksum (the first word of the `.sha256` sidecar), if you built it yourself:
- Does the entry's `options` line (`loader\entries\gb-<slot>.conf`, or the summary's `== cmdline`) contain
  `8250.nr_uarts=4`? (Images built before wave 3 lack it: docs/FLASHING.md, "Images built before wave 3".)

## Logs

Attach the whole `gblog\` folder from the boot partition (`EFI-SYSTEM`; readable from Windows), or at least:

- `gblog\boot<N>-t420.txt` and `gblog\boot<N>-t900.txt` of the affected boot (wait at least 7 minutes before you
  power off; hold Power for 10 s to switch off)
- `gblog\audio-*` for sound problems, `gblog\dgpu-*` / `test-*` / `selftest-*` reports for NVIDIA problems
- for a held system update, the `gb_slotsync HOLD:` line of the summary

Paste the `== summary` block of the most recent snapshot here:

```
(paste here)
```

## Checklist

- [ ] I reviewed the attached files and redacted serial numbers, network names and account names.
- [ ] I looked up the symptom in docs/FLASHING.md, section 7 (Troubleshooting), and say above what I tried.
- [ ] Secure Boot is off, and the image passed `python -I verify_image.py`.

<!-- Structure inspired by gbOS86's logs and install pages (gbos86.com/docs/logs, gbos86.com/docs/install). -->
