# Flashing and booting the USB stick image

This guide takes the image that `build_usb.py` builds from **your own** `lapis.bin` onto a USB stick and boots it
on the Surface Book 3 13.5". No image is distributed with this repository: Google's OS and vendor files reach the
stick only through your copy of the lapis image. How the image is put together: [NATIVE.md](NATIVE.md).

**Status: untested on hardware.** The partition layout, slot handling, first-boot formatting and the loading of
every Surface driver were tested in VMware (`build_usb.py --vm-test`, and `GB_NATIVE_TEST=1` for the native tree);
the drivers themselves can only be proven on the laptop. Last build tested this way: CL3B.260622.276.R4 with kernel
build #13.

## 0. Build and check the image

```powershell
python -I -B build_usb.py                       # -> out\googlebook-sb3-usb.img (~27 GiB, fits a 32 GB stick)
python -I -B build_usb.py --userdata-gib 40     # for a 64 GB stick
python -I verify_image.py                       # read-only check of out\googlebook-sb3-usb.img (or pass a path)
Get-FileHash out\googlebook-sb3-usb.img -Algorithm SHA256   # note it if you copy the image elsewhere
```

`verify_image.py` checks, without writing anything:

* the protective MBR, the primary and backup GPT headers and their entry-array CRC32s, every partition inside the
  usable range and 1 MiB aligned, no overlaps;
* all expected partitions (`EFI-SYSTEM`, `boot_a/b`, `init_boot_a/b`, `vendor_boot_a/b`, `vbmeta_a/b`,
  `pvmfw_a/b`, `misc`, `metadata`, `frp_persist`, `desktop_security_persist`, `desktop_security_storage`, `super`,
  `userdata`) and the vbmeta slot attributes (at least one slot marked successful);
* the ESP's boot entry: `androidboot.boot_part_uuid` names this image's `super` partition, and `gb.hw=native`,
  `efi_pstore.pstore_disable=1`, `module_blacklist=` and `reboot=pci` are present;
* the copied partitions are identical to `lapis.bin` (full compare; `super` sampled), the blank ones are zero.

It ends with `RESULT: OK` or the list of problems (exit code 1). Do not flash an image that fails it.

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
4. A boot menu appears for 4 seconds: **Googlebook OS - Surface Book 3 (USB) (slot X)** starts automatically.
5. The first boot formats the data partitions and prepares apps: allow **10 minutes**. Later boots took 1-2 minutes
   in the VM; the laptop's times are not known yet.

To go back to Windows: shut down, remove the stick, power on. The stick is built not to touch Windows or the
internal disk (section 5).

## 3. What to try

| Area | How to check |
|---|---|
| Display / brightness | picture appears; brightness keys or Settings → Display |
| Base keyboard and touchpad | type, click, two-finger scroll |
| Touchscreen and pen | tap, swipe, draw with the Surface Pen |
| Battery | the status bar shows one combined level for both batteries; plug/unplug the charger |
| Detach | press the detach key on the keyboard: the base should release after a moment |
| Wi-Fi / Bluetooth | connect to Wi-Fi, pair a device |
| Sound | play a video; headphone jack; microphone in the Recorder app |
| Cameras | Camera app: front and rear (needs the IPU4P firmware in your build, see [INPUTS.md](INPUTS.md#firmware-native-only); software image processing, so expect modest quality) |
| Auto-rotate / light | detach the screen, rotate it; auto-brightness |
| NVIDIA GPU | off by default (saves power). Optional experiment: `setprop persist.vendor.gb.nouveau 1` |

The per-device checklist with the log lines to look for is the "Hardware validation checklist" in
[DEVICE-STATUS.md](DEVICE-STATUS.md).

## 4. Logs

There is no serial console natively. Every boot writes logs to the stick's ESP at 40 s, 90 s, 3 min and 7 min,
even if the screen stays black: `gblog\bootN-tSECONDS.txt` (plus `audio-*` and other diagnostics). If something
fails, wait 7 minutes, hold Power for 10 s to switch off, put the stick into a Windows PC and read `gblog\` from its
FAT32 partition. Include those files when you report a result.

## 5. Safety built into the image

These are design measures; like everything native, they have not been checked on the laptop yet.

* Google's Googlebook firmware updater, flashrom, futility, fwupd and the Googlebook disk installer are disabled,
  and the TPM and SPI-flash drivers are blacklisted, so that nothing in Android writes the Surface's firmware, its
  TPM or your internal disk.
* Crash logs are not written into the Surface's UEFI variable store (`efi_pstore.pstore_disable=1`).
* The stick selects only its own partitions (`androidboot.boot_part_uuid`) and is set up not to mount the internal
  disk.

## 6. Dual boot (install next to Windows)

[native/dualboot/README_DUALBOOT.md](../native/dualboot/README_DUALBOOT.md) describes a Boot Camp-style installer
(`install_dualboot.ps1`, a dry run unless `-Apply`). It shrinks D:, adds the Googlebook partitions and a boot entry,
and keeps Windows the default; `Restart-ToGooglebook.ps1` restarts straight into Googlebook. It has been tested on
disk image files only. Boot the USB stick on the laptop first; once that works, rehearse the installer on a virtual
disk (VHDX) before touching the real one.
