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
(`gb_slotsync --watch 60`, root, logs `gb_slotsync: ...` to kmsg; host test:
[guest/test/test_gb_slotsync.sh](../guest/test/test_gb_slotsync.sh)):

1. Reads the booted slot (`ro.boot.slot_suffix`).
2. Switches only for an update that `update_engine` **completed during this boot**:
   `/data/misc/update_engine/prefs/update-completed-on-boot-id` equals the current `boot_id` ("waiting for reboot").
   A stale GPT flag (an update rolled back by booting the old slot) is ignored.
3. Picks the target the way depthcharge would: from the vbmeta GPT attributes, the highest priority among slots that
   are successful or have tries left.
4. Takes the ESP lock (`/dev/gb_esp.lock`, shared with `gb_bootlog` and the NVIDIA policy, which must never load
   nouveau while the ESP is mounted; after 10 minutes of waiting it skips this cycle), mounts the ESP
   (`/dev/block/by-name/EFI-SYSTEM`, with `flush`) and builds `/initrd-<x>.img` for the target from **that slot's**
   `init_boot_<x>` and `vendor_boot_<x>` partitions plus the ESP's `/gb/inject.gz` (our cpio) and
   `/gb/bootconfig_extra`: the same format as `build_esp.py` (`gzip cpio | vendor ramdisk | generic ramdisk |
   bootconfig + androidboot.slot_suffix=_<x>`). A CRC-32 stamp (`/gb/slot_<x>.stamp`, computed like
   `build_esp.slot_stamp()`) skips the rebuild when nothing changed.
5. Writes `loader/entries/gb-<x>.conf` from the booted slot's entry (same kernel and options, new initrd and title)
   and, on native ESPs, `gb-<x>-nvtest.conf` from the booted slot's `[NVIDIA test]` entry, then sets
   `default gb-<x>` in `loader/loader.conf`. The old slot's entries stay in the menu as a fallback while they still
   match that slot. A rebuild first removes the slot's older entries and stamp, so an interrupted rebuild leaves no
   entry that would boot the slot's new system with its old ramdisk. On later boots (no update pending) the other
   slot's entries are checked once per boot and whenever its GPT bootable state changes: they are removed while an
   update is being written into that slot (it is marked unbootable) or when its initrd no longer matches its
   `init_boot`/`vendor_boot` (the stamp). The next update into that slot writes them again.
6. Otherwise it keeps the default on the slot that actually booted, so after a failed new slot (you pick the old one
   in the menu) the default falls back.

### Kernel check before the switch

(Wave 3.) Before step 5 moves the default, gb_slotsync reads the kernel version of the new slot's `boot_<x>` (boot
header v4, the bzImage setup header's `kernel_version` string, from the first 64 KiB) and compares its KMI generation
`<maj>.<min>-android<N>-<gen>` (for example `6.18-android17-5`) with the running kernel's (`uname -r`). lapis's vendor
modules for a build are made for the GKI in that build's `boot_<x>`, while this ESP keeps booting our kernel:

* **Same KMI** (also when the GKI sublevel or build number changed, as long as the KMI is the same): switch as
  above. The comparison is logged once.
* **Different KMI, or a version that cannot be read:** **HOLD.** The new slot's initrd and its `(slot X)` entries are
  still written, so it is in the boot menu and can be picked once by hand, but `loader.conf` keeps the old default.
  kmsg gets one line per (slot, release), `gb_slotsync: HOLD slot <x>: KMI <new> != running <ours>; default stays
  gb-<cur>`, again once on each later boot while the hold is in effect (so the `gblog\` summary shows it), and the
  notification **"System update held"** ("Kernel mismatch: ...") appears (natively, through `gb_notify`) once the
  new slot's initrd and entries are on the ESP, so "(slot X) is in the boot menu" is true when you read it. If the new slot is booted by hand and comes up, it becomes the default as usual. A matching kernel
  rebuild is the real fix.

Lead decision D-a of wave 3; the idea of checking the kernel interface before trusting a new slot comes from gbOS86's
porting notes (github.com/linuxkid473/gbos-x86 @f8a5c1a, porting.md); the code is ours.

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
  (android17-6.18), the new vendor_boot/vendor_dlkm modules load into it. A build that moves to another KMI is held
  by the kernel check above until a matching kernel rebuild exists. Watch dmesg for module load failures after an
  update.
* **fs-verity**: Android itself enables ext4 verity on `/data` at the first boot, natively and in the VM (the VM's
  `/data` already has it). The native fstab also names `fsverity` (wave 3), which adds nothing on top
  ([INPUTS.md](INPUTS.md#userdata-fs-verity)).
* Natively, native modules we ship that are copies of lapis vendor modules (the HDA codecs) must match the booted
  build; `gb_audio.sh` swaps in the vendor_dlkm copies when they do not.
* The ESP needs room for two initrds: 192 MiB in the VM, 512 MiB on USB/internal images.
* Observed in the VM, once: CL3B.260622.271 -> .276 installed by OTA, and the VM then booted .276 from slot B. This
  was confirmed afterwards from the slot state and the build number; the reboot into the new slot was not watched
  step by step. Untested on hardware.
