# System updates (OTA) and the A/B slot follower

## The problem

lapis updates like any A/B Android device: `update_engine` writes the new system into the **inactive slot**
(`super` logical partitions, `boot_x`, `init_boot_x`, `vendor_boot_x`, `vbmeta_x`) and the android-desktop
boot_control HAL then marks that slot active, ChromeOS-style, in the **GPT attributes of `vbmeta_a` / `vbmeta_b`**:
bits 48-51 priority, 52-55 tries left, 56 successful. On a real Googlebook the firmware (depthcharge) reads those bits.
Here nothing does: systemd-boot just starts its default entry, and our initrd embeds one slot's ramdisks and
`androidboot.slot_suffix`. Without help, every update would reboot into the old slot and roll itself back.

## gb_slotsync

[guest/gb_slotsync.c](../guest/gb_slotsync.c) runs in the guest (VM and native) from `sys.boot_completed`, every 60 s
(`gb_slotsync --watch 60`, root, logs `gb_slotsync: ...` to kmsg):

1. Reads the booted slot (`ro.boot.slot_suffix`).
2. Switches only for an update that `update_engine` **completed during this boot**:
   `/data/misc/update_engine/prefs/update-completed-on-boot-id` equals the current `boot_id` ("waiting for reboot").
   A stale GPT flag (an update rolled back by booting the old slot) is ignored.
3. Picks the target the way depthcharge would: from the vbmeta GPT attributes, the highest priority among slots that
   are successful or have tries left.
4. Mounts the ESP (`/dev/block/by-name/EFI-SYSTEM`) and builds `/initrd-<x>.img` for the target from **that slot's**
   `init_boot_<x>` and `vendor_boot_<x>` partitions plus the ESP's `/gb/inject.gz` (our cpio) and
   `/gb/bootconfig_extra`: the same format as `build_esp.py` (`gzip cpio | vendor ramdisk | generic ramdisk |
   bootconfig + androidboot.slot_suffix=_<x>`). A CRC-32 stamp (`/gb/slot_<x>.stamp`, computed like
   `build_esp.slot_stamp()`) skips the rebuild when nothing changed.
5. Writes `loader/entries/gb-<x>.conf` from the booted slot's entry (same kernel and options, new initrd and title)
   and sets `default gb-<x>` in `loader/loader.conf`. The old slot's entry stays in the menu as a fallback.
6. Otherwise it keeps the default on the slot that actually booted, so after a failed new slot (you pick the old one
   in the menu) the default falls back.

Then reboot as Android asks. The new slot boots with **our kernel** and the new slot's ramdisks, vendor modules and
system; Android marks it successful (`gb_bootok` -> `bootctl mark-boot-successful`, helped by the `crossystem` stub).

## Host-side rebuilds follow the slot

`build_esp.py` and `build_usb.py` never assume slot A: `extract_slot.boot_slot()` reads the same vbmeta GPT
attributes from `lapis.bin` (successful slots first, then slots with tries left) and takes that slot's ramdisks from
the image. `GB_SLOT=a|b` overrides it. `build_usb.py` refuses to copy a system while the other slot has an update that
is activated but not yet successful.

## Postinstall

The OTA's vendor postinstall (`fw-updater-wrapper`) runs `google_desktop_firmware_updater vendor`, which would flash the
Googlebook's AP/EC firmware. Its non-zero exit on other hardware aborted every update ("Couldn't update - Installation
problem"). It is bind-mounted with `gb_fw_stub.sh`, which logs "firmware update skipped" and exits 0, so updates
complete and no firmware is touched.

## Caveats

* **Kernel compatibility**: we keep booting our kernel. As long as Google stays on the same GKI branch/KMI
  (android17-6.18), the new vendor_boot/vendor_dlkm modules load into it. A build that moves to another kernel
  branch would need a matching kernel rebuild before rebooting into it. Watch dmesg for module load failures after an
  update.
* Natively, native modules we ship that are copies of lapis vendor modules (the HDA codecs) must match the booted
  build; `gb_audio.sh` swaps in the vendor_dlkm copies when they do not.
* The ESP needs room for two initrds: 192 MiB in the VM, 512 MiB on USB/internal images.
* Observed in the VM, once: CL3B.260622.271 -> .276 installed by OTA, and the VM then booted .276 from slot B. This
  was confirmed afterwards from the slot state and the build number; the reboot into the new slot was not watched
  step by step. Untested on hardware.
