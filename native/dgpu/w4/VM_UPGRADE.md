> **Repository note.** A wave 4 working document (2026-10-26), copied from the development tree. Paths such
> as `C:\GB-build\...` and `C:\GB-x86\...` are the development layout (`C:\GB-build\native\<x>` =
> `native/<x>`, `C:\GB-x86` = the repository root, `D:\GB-vm-backup`, `D:\gb-w4test` = local backup and
> throwaway-VM folders, `builder:` / `~/gk` = the Linux builder); `evidence/` and the planning session's own
> files are not published, and details of the developer's own files were removed. For users:
> [docs/GATEKEEPER.md](../../../docs/GATEKEEPER.md).

# W4 -- back up the user's VMware VM, then upgrade it (plan)

User decisions (2026-10-26): **yes** to a software lock-screen PIN (a convenience lock, not a hardware security
boundary); **yes** to "safely back up the VMware VM, then upgrade it" (kernel k15, zram/zsmalloc in the VM tree,
fs-verity for /data, the software Gatekeeper, and the wave-3 fixes that apply to the VM).

This file is the plan only. Nothing here has been applied. While writing it this task wrote nothing outside
`C:\GB-build\native\dgpu\w4\` and its scratchpad. It did not start the VM, did not write `lapis.bin`, and did not edit
any pipeline file (`build_esp.py`, `build_usb.py`, `stage_kernel.py`, `verify_image.py`, `inject\**`, `iso\**`,
`dualboot\**`, `gb_slotsync.c`, `audio\**`), because wave 3 is editing those right now. Every measured fact below says
how it was measured. **INFERRED** marks anything not measured.

**Reviewed 2026-10-26 (`REVIEW.md` in this folder).** Corrected: the backup's origin (0), the budget (1.2), a
re-pairing of the backup with the live disk right before the write (2.4 step 0, P9), 2.2(c) and the checks that
still described a gbOS86-style Gatekeeper (2.2c, 2.4, 2.7, 4), the rehearsal scope (2.3), a new post-upgrade ESP
snapshot (2.9) and the rollback rules once a PIN exists (3.0-3.3). The user must not set a real PIN until
GATEKEEPER.md 7.8 W4-GK-1 is applied (or accepted) and the rehearsal ran GATEKEEPER.md 9.5.

**Rebased on wave 3's final pipeline and the 2.2 delta APPLIED (W4 integration, 2026-10-26, about 13:30-15:30
local).** Wave 3 is finished (k15 staged, USB image `377310ee...`, ISO `b1c355b0...`, repo `81de83e`). The pipeline now
carries 2.2(a)-(d): zram for mmd in the VM tree, the fsverity fstab in both trees, the software Gatekeeper 0.2.0
(`505b404d...3d51`) in both trees, the checks and tests, plus the two wave-3 audit leftovers in the native bring-up
(LOW-1, LOW-2) and a test-only rehearsal variant (2.3). Evidence: `C:\GB-build\native\dgpu\w4\evidence\integration\`.
Pre-edit copies of every changed file: `C:\GB-x86\archive\pre-w4\` (SHA256SUMS.txt inside). Nothing was built into an
image, `lapis.bin` was never opened for writing, no VM was started. Section 0 finding 2 is corrected below (lapis
itself sets the /data verity bit).

**Rehearsal 2.3 DONE (2026-10-26, about 14:35-17:35 local): PASS** on a throwaway VM below `D:\gb-w4test\` (P7 met;
results in 2.3 and GATEKEEPER.md 9.6). It found and fixed two pipeline defects, both in the VM tree the user's VM
gets: `gb_kmsgdbg` (no `stdio_to_kmsg` output reached kmsg on lapis's user build; both trees) and `gb_zram` (lapis's
own `modprobe zram` can win the early-init load race and leave the VM without zram, 2.2(a)). The rehearsed and final
VM tree is **`1e939bc047f6d4092aeb61b7705235ecada75c1c44c742fbc6ffcf94a5842934  gb/inject.gz`** (record:
`C:\GB-build\native\dgpu\w4\evidence\rehearsal\run2\w4-tree.img.vmtree.sha256`; 2.4's preview must match it by V30).
Corrections for 2.6/2.7: lapis runs in headless system user mode (the user's profile is user 10, so `cmd
lock_settings ... --user 10`), and `gbsh.py` must not connect before boot_completed. **Still to do, in this order:**
1.4 (re-validate `<BK>`, extras; extract the Appendix B/C tools to `w4\tools\` first, the folder does not exist yet),
2.1 (stale lock folders: the user's OK), then 2.4-2.9 on the user's VM.

**User-VM upgrade DONE (2026-10-26, 17:41-18:06 local): PASS.** Evidence: `D:\GB-vm-backup\w4-upgrade-20261026-1741\`
(`SHA256SUMS.txt`; `verify\` holds the serial log of the boot, adb/root check outputs, logcat, the scrcpy recording
and the microdroid KVM test). In order:

* **1.4:** the Appendix B/C tools are in `C:\GB-build\native\dgpu\w4\tools\` (`gb_vmbackup.py` sha256 `458514b8...2ae0`,
  checked; `vm_esp_preview.py` `d8796a94...`, `esp_tree_diff.py` `bcb4a993...`, `sb_probe.py` `483da44f...`, verbatim from
  Appendix C). `verify <BK>`: verify OK (94 s). `<BKX>` = `D:\GB-vm-backup\2026-10-26-pre-upgrade-extra` (105 s,
  verify OK): **PAIRED**; ESP `2f0afad4...26f510`, GPT `b4f41d76...`/`e411d8d1...`, slots a (0,0,0) b (15,0,1) = section 0.
* **2.1:** the two empty `lapis.vmdk.REDO_*.lck` folders (created 22/10, 0 items, VM off) were **moved** (not deleted) to
  `D:\GB-vm-backup\stale-locks-2026-10-26\`. Undo: move them back. No `*.lck` left in `C:\GB-x86`.
* **2.4:** preview `VM tree: 1e939bc0...2934` (= the rehearsed tree), verify_image `--esp-size 201326592
  --expect-inject-sha256 <rehearsed> --krel k15` **RESULT OK** (V1-V30), preview sha256 `cbcfcb17...8ba2d1`, used 95.2 MiB,
  free 93.8 MiB; tree diff exactly the expected list (gb.rc: gb_kmsgdbg, zsmalloc/zram insmod, gb_zram, Gatekeeper block;
  Trusty empty-VINTF bind still present). (0) at 17:46-17:47: live lapis.bin `640c8539...cc55` **PAIRED**, mtime
  unchanged; (2) at 17:47:45-57 `GB_WRITE_LAPIS=1 GB_NO_BUILDER=1 python -I -B build_esp.py`: `boot slot: b`, `wrote 192
  MiB ESP into lapis.bin at offset 142606336`.
* **2.5:** `ESP == preview`; `compare <BK>`: only `EFI-SYSTEM` differs.
* **2.6:** `vmrun -T ws start ... nogui` 17:50:32; `sys.boot_completed` at kernel 82.4 s (adb 1 at 17:52:06). Note:
  VMware **appends** to `serial0.log` (the new boot starts at the k15 `Linux version` line). serial: k15; `gb_gatekeeper
  0.2.0 starting`, both registrations, `shared HMAC key computed with 2 participants`, 0 `E ` lines; `gb_zram: zram0 present
  (early-init insmod)`; `mmd_setup ... exited with status 0`; no `Enabling ext4 verity`, no tune2fs line; gb_slotsync and
  gb_adb started. The same `gb_*` services as the pre-upgrade baseline, plus gb_gatekeeper, gb_kmsgdbg, gb_zram.
* **2.7** (the login screen is user 0; the user's profile 10 was entered with `am switch-user 10`, no credential on either
  user): V-1 k15; V-2 NexusLauncher resumed (u10); V-3 50 modules incl. our 7 + zram + zsmalloc, no native/dGPU module, 0
  unsigned; V-4 `/dev/block/zram0` 18,353,528 KiB (150 % of 11.67 GiB), lzo-rle, writeback `/dev/block/loop46`; V-5 0
  crash lines, `mmd.enabled_aconfig=true`, mmd running; V-6 `supported`, fstab `...,reservedsize=128M,fsverity`, tune2fs
  features incl. `verity`; V-7 IGatekeeper/default once, ISharedSecret/gatekeeper + /default, only gb_gatekeeper running
  (Trusty `.rc` whited out, fragment 40 B), `/data/vendor/gatekeeper` `drwx------ hsm hsm gatekeeper_vendor_data_file`
  **empty**, binary `505b404d...3d51`, `--selftest` PASS (keystore2's own negotiation line had already left logd's 256 KiB
  buffer; the HAL's line above is the evidence; keystore2 was not restarted); **V-8 not run: no PIN is ever set on the
  user's VM by an agent** (the rehearsal covered it); V-9 0 auth-bound-key errors, users 0 and 10 RUNNING_UNLOCKED; V-10
  Play 53.6.15-34, 0 INSTALL_FAILED (an update needs the user); V-11 `_b`; V-12 1 gb_slotsync (`--watch 60`), no HOLD, no
  lock; V-13 `irqon == emulated`; V-14 VM 95 % on battery = host composite 94.5 % (two packs), AC off on both, gb_hostbatt
  running; V-15 HDA card, audio HAL + audioserver running, `gb_vmaudio` master line (nobody listened); V-16 headless
  `scrcpy --no-window --require-audio --record` 10 s: H.264 1920x1200 + Opus; V-17 PORTS.xml `<int name="2222" value="1"
  />`, /dev/kvm 0666, vmx on 4 CPUs, kvm cmdline unchanged, and a throwaway microdroid VM (`vm run-microdroid`, own work
  dir, removed afterwards) booted under k15 nested KVM to microdroid_manager (the Terminal app itself was not opened);
  V-18 only the pre-existing `futility` CANNOT LINK; V-19 2560x1600, GB Absolute Mouse, ping 8.8.8.8 and google.com,
  adb 5555, gb_netwatch quiet; 0 `permissive=0` denials.
* **2.8:** `svc power shutdown` 18:01:06, VMX exit (0) 18:01:21, `cleanShutdown = "TRUE"`. `compare <BK>`: EFI-SYSTEM,
  metadata, userdata differ, GPT not; superblock `RO_COMPAT_VERITY = SET`, state clean, 18.19 GiB free; slots a (0,0,0)
  b (15,0,1); GPT hashes = section 0; the ESP after the boot = the preview + `loader/random-seed` only (`gb/slot_b.stamp`
  unchanged), verify_image RESULT OK.
* **2.9:** `<BKP>` = `D:\GB-vm-backup\2026-10-26-post-upgrade-esp` (ESP `3489343d...cb21`, live lapis.bin now
  `41c9b0ef...a541`), verify OK. `<BK>` no longer equals the live disk (expected); 3.3 still restores 26/10 00:18:46.

Left for the user: V-8/real PIN (Settings > Security; `<BKP>` is the fix-forward target), V-10 Play update, listening to
a sound, the scrcpy window, opening Terminal/Antigravity once.

Placeholders for outputs that do not exist yet:

| Placeholder | Producer | Expected value |
|---|---|---|
| `<K15_DIST>` | wave 3 R-04 (KERNEL) | `/build/dist15` on the builder (contract 2.1); **present** at the review (05:42) |
| `<K15_REL>` | wave 3 R-04 | `C:\GB-build\kernel\k15_release.txt`, one line; at the review (written 04:58:09): `6.18.21-android17-5-g3c2c2f588d3f-ab16302715`. Re-read at P2: wave 3 may rebuild |
| `<W3_ISO_SUMMARY>` | wave 3 R-11 | `C:\GB-x86\iso\test\logs\<build_id>\summary.txt`, every test id PASS |
| `<W3_R13>` | wave 3 R-13 | `C:\GB-build\native\dgpu\w3\evidence\...`: `GB_VNNI_KAT: PASS`, `gb_vnni_kat: zram swap on` |
| `<SLOTSYNC_SHA>` | wave 3 R-03 (AUX) | sha256 of the HOLD-capable `C:\GB-x86\inject\slotsync\gb_slotsync` |
| `<GK_DIR>` | Task A (software Gatekeeper) | `C:\GB-build\native\gatekeeper\` (exists): binary, rc stanza, VINTF fragment, `out\ACCEPTANCE.txt`; design `GATEKEEPER.md` |
| `<BK>` | this plan, step 1 | backup folder, `D:\GB-vm-backup\2026-10-26-pre-upgrade` (exists, see 1.1) |
| `<BKX>` | this plan, step 1.4 | `D:\GB-vm-backup\2026-10-26-pre-upgrade-extra` (ESP image, GPT, manifest) |
| `<BKP>` | this plan, step 2.9 (added by the review) | `D:\GB-vm-backup\<date>-post-upgrade-esp` (the upgraded ESP with gb_gatekeeper: the fix-forward target) |

---

## 0. State on 2026-10-26, 04:20-05:00 local (UTC+2), measured

| Item | Value | How it was measured |
|---|---|---|
| User VM | **off**. `vmrun -T ws list` lists only `C:\GB-build\builder2.vmx`. The `vmware.log` tail shows `VMX exit (0)` at 2026-10-25T22:18:52Z, and the vmx has `cleanShutdown = "TRUE"` | `"C:\Program Files\VMware\VMware Workstation\vmrun.exe" -T ws list`; `vmware.log` |
| Disk files | `lapis-boot.vmx` -> `nvme0:0.fileName = "lapis.vmdk"`, `independent-persistent` (no snapshots possible, hence the file copy); `lapis.vmdk` = `monolithicFlat`, `RW 95965361 FLAT "lapis.bin" 0`; `lapis.bin` 49,134,264,832 B, not sparse, mtime 26/10 00:18:46; `lapis-data.vmdk` (3.2 MB monolithicSparse) is **not** referenced by the vmx | read the vmx/vmdk; `fsutil sparse queryflag` |
| lapis.bin sha256 | `640c85398dabd434a63b31813065240526902868f04bbb0b7c0d521f02ebcc55` (mtime unchanged during the read) | read-only full read at background I/O priority, 179.6 s (scratchpad `w4vm\lapis_scan.py`) |
| Zero content | 23.91 GiB of 45.76 GiB are all-zero 64 KiB chunks; **21.85 GiB (23.46 GB) non-zero** (userdata 14.01 GiB, super 7.50 GiB, rest < 0.4 GiB) | same scan, per GPT partition |
| Current VM ESP | 192 MiB at byte 142,606,336; `loader.conf` default `gb-b`; kernel **`6.18.21-android17-5-g3938c8d97142-dirty-ab16302624`** (the k13 build: the VM never got k14); 91 cpio entries; free 94.2 MiB; ESP sha256 `2f0afad4...26f510` | ESP extracted read-only, `verify_image.py --esp ... --expect-tree vm`: V1 (size, expected for the 192 MiB VM ESP), V4 (not k14), V12/V25 (pre-wave-3 gbinit and gb_slotsync) are the only problems |
| Slot state | boot slot **b**; vbmeta_a (0,0,0), vbmeta_b (priority 15, tries 0, successful 1); primary GPT (LBA 0-33) sha256 `b4f41d76...c8aa81`, backup GPT (last 33 LBAs) `e411d8d1...6f788681` | `extract_slot.boot_slot()`; GPT read-only |
| /data superblock | ext4, 32.16 GiB, 18.21 GiB free at the last write; mkfs 2026-10-24 04:34:19 (guest clock), casefold; **`RO_COMPAT_VERITY` is already SET** (ro_compat 0x807b) | userdata superblock read from lapis.bin (scratchpad `w4vm\sb_probe.py`) |
| VM kernel config | `CONFIG_FS_VERITY=y`, `CONFIG_ZRAM=m`, `CONFIG_ZSMALLOC=m`, `CONFIG_ZRAM_WRITEBACK=y`, `CONFIG_ZRAM_MULTI_COMP=y`, `CONFIG_SWAP=y`, `CONFIG_BLK_DEV_LOOP=y` (current k13 VM kernel) | IKCONFIG extracted from the ESP's vmlinuz (scratchpad `w4vm\ikconfig.py`) |
| zram today | lapis `mmd_setup` aborts on every boot: `zram setup failed: failed to write zram disk size: No such file or directory (os error 2)` (42 lines in `serial0.log`; also AC-510 in wave-2 VM-2) | `serial0.log`; `1b\evidence\w2\vm2\10-logcat_crash.txt` |
| lapis mmd props | `mmd.zram.enabled=true`, `mmd.zram.size=150%`, `mmd.zram.writeback.enabled=true`, `...writeback.device_size=8589934592`, `...min_free_space_mib=8192`, `mmd.zram.recompression.enabled=true` | wave-2 native gblog `getprop` (same lapis build) |
| **Write guard blocker** | `GB_WRITE_LAPIS=1 python build_esp.py` would refuse today: `VMware has the user's VM open (lapis.vmdk.REDO_a09248.lck, lapis.vmdk.REDO_a27864.lck)`. These are two **empty** folders from 22/10 17:09 and 20:03 (old REDO-log runs). VMware is not running the VM | `_lapis_write_refused()` from build_esp.py, extracted with `ast` and run alone (scratchpad `w4vm\guard_probe.py`) |
| Pipeline preview | today's pipeline (k14) would write a VM ESP with used 94.9 MiB, free 94.2 MiB, largest initrd 53,530,813 B; `verify_image --esp --expect-tree vm --krel k14_release.txt`: only V1 (192 MiB size) | scratchpad `w4vm\vm_esp_preview.py` (imports build_esp; its write is under `__main__`), `GB_NO_BUILDER=1` |
| Tree diff now vs the VM's ESP | changed: `gbinit`, the 7 VM `.ko` (k13 -> k14 signatures), `gb_slotsync` (18,824 -> 27,256 B), `vmlinuz`. **`gb.rc` and the fstab are unchanged** | scratchpad `w4vm\esp_tree_diff.py` |
| Host disks | C: and D: are **two partitions of one NVMe** (disk 0). At 04:50: C: 87.70 GB free; D: 55.15 GB (51.36 GiB) free. Review re-measure at about 05:40: C: 87.60 GB, D: 54.08 GB (D: -1.07 GB, not in `D:\GB-iso\*`, `D:\gb-w2test`, `D:\gb-test`, `D:\gb-carve`; cause not identified) | `Get-Partition`, `Get-PhysicalDisk`, `Get-PSDrive` |
| Wave 3 progress | `kbuild15` started 02:11:51Z and ended `rc=0` at 02:56:15Z (44 min). `/build` 11 GB free (91 %). `D:\GB-iso\test` and `D:\gb-w2test` hold no test disks (ISO suite not started). `D:\GB-iso\prev` = 37.36 GB | builder `kbuild15.log`, `/build/kbuild15.done`; `Get-ChildItem` sums |

Two findings change this plan:

1. **A full backup already exists.** `D:\GB-vm-backup\2026-10-26-pre-upgrade\` was created at 04:34:42 and finished
   at 04:45:29. This task did not make it. **Origin (found by the review):** the orchestrating session of this
   workflow made it with its own `scratchpad\vm_backup.py` (copy with hashing, then every copy re-read and compared;
   its output ends `BACKUP OK in 647s`, all 9 files `VERIFIED`). The space on D: had been freed by the user: their
   screenshot at 04:33:43 shows D: 95.6 GB free (Explorer GB), and the session recorded "user freed more space". The
   top level of `<a personal folder on D:>` changed at 04:35:07; no delete of it by any agent was found in today's session
   transcripts (INFERRED: the user's own activity; confirm with the user). It holds lapis.bin (full, not sparse,
   45.76 GiB allocated), lapis.vmdk, lapis-data.vmdk, lapis.bin.gptbak, lapis-boot.{vmx,nvram,vmxf,vmsd,vmx.bak},
   SHA256SUMS and RESTORE.txt. **I re-hashed every file read-only (256 s): all 9 match their SHA256SUMS line.** The
   copy's lapis.bin hash equals the independent live hash above (`640c8539...cc55`), and the small files equal the
   live ones. A byte compare of the live lapis.bin against the copy (`gb_vmbackup.py compare`, read-only, 05:02:57 to
   05:05:13) printed `compare: identical`. It does not hold the raw ESP image, the GPT regions, `serial0.log`, a manifest or read-only
   attributes. Step 1 therefore **re-validates and extends** this copy instead of making a second 23-49 GB copy.
2. **/data already has the ext4 verity feature.** So adding `fsverity` to the VM fstab makes fs_mgr's `tune_verity()`
   return straight away ("superblock already has the feature"): there is **no** superblock change on the existing
   /data. Wave 3 feared exactly that change (WAVE3_PLAN 4.2). **Corrected 2026-10-26 (W4 integration, from the wave 3
   final-fix report and its evidence):** lapis's own fs_mgr sets the bit, with or without the `fsverity` flag. Every
   boot with the plain fstab logs `[libfs_mgr] Enabling ext4 verity on /dev/block/by-name/userdata` followed by a
   `tune2fs` run: `C:\GB-build\native\dgpu\1b\evidence\w2\vm1\gblog\boot1-t40.txt`, `1b\evidence\w2fix\vm1\gblog\boot1-t40.txt`
   (`[15.947226] init: [libfs_mgr] Enabling ext4 verity on /dev/block/by-name/userdata`), `w3\evidence\build\vm2\serial0.log`
   and `vm2-early\serial0.log` (VM-tree boots whose fstab userdata line ends `reservedsize=128M`, no `fsverity`). The
   likely trigger is AOSP's rule for `ro.product.first_api_level >= 30` (lapis reports `[37]`): INFERRED, not proven.
   So the flag is redundant on lapis but harmless; the VM tree ships it anyway (2.2(b), applied), so both trees carry
   the same fstab and the intent is explicit. The user's /metadata has the bit clear while throwaway boots set it on a
   freshly formatted /metadata (not investigated; the fstab change does not touch /metadata).

---

## 1. Backup

### 1.1 What is backed up and where

| File (in `C:\GB-x86\`) | Why | In `<BK>` (existing) | In `<BKX>` (step 1.4) |
|---|---|---|---|
| `lapis.bin` (49.13 GB) | the whole disk: GPT, ESP, both slots, super, userdata | yes, verified | no (hashed only, ties `<BKX>` to `<BK>`) |
| `lapis.vmdk` | flat descriptor (extent size = lapis.bin) | yes | yes |
| `lapis-boot.vmx`, `.vmxf`, `.vmsd`, `.vmx.bak` | VM config (cpuid masks, nvme slot 2208, serial, sound...) | yes | vmx, vmxf, vmsd |
| `lapis-boot.nvram` | EFI variables (boot order) | yes | yes |
| `lapis-data.vmdk` | unreferenced old data disk (asked for; 3.2 MB) | yes | yes |
| `lapis.bin.gptbak` | original GPT saved by grow_esp/grow_userdata | yes | yes |
| `serial0.log` | pre-upgrade boot log baseline (12.6 MB) | no | yes |
| raw EFI-SYSTEM (192 MiB), GPT LBA 0-33, last 33 LBAs | fast ESP-only rollback; proof that the GPT is untouched | no | yes |
| `manifest.json` (partitions, slot attrs, sizes, times, stale locks), `SHA256SUMS` | verification | SHA256SUMS only | yes |

Both folders are on D:, which is on the same NVMe as C:. They protect against a bad ESP write, a broken upgrade or a
corrupted /data. They do **not** protect against a failure of that SSD. An extra copy of `<BK>` on an external drive
is recommended but optional (it needs the user's drive; not planned here).

### 1.2 Disk budget (floors C: >= 15 GB, D: >= 3 GB)

Measured 04:50: C: 87.70 GB free, D: 55.15 GB free (with `<BK>` already present).

| Moment | C: | D: | Source |
|---|---|---|---|
| now | 87.7 | 55.2 | measured |
| wave 3 R-07 USB image (sparse) | about -8 | - | WAVE3_PLAN 8 |
| R-09 export, R-10 ISO into `D:\GB-iso\new\` | about -13 temp (released) | about -7.5 | WAVE3_PLAN 8 |
| R-11 ISO suite, test disks under `D:\GB-iso\test\` | - | <= -13.5 temp (its own `MIN_FREE_AFTER` = 8 GiB guard) | WAVE3_PLAN 8 |
| R-12/R-13 throwaway VMs under `D:\gb-w2test\` | - | about -8 temp, one at a time | WAVE3_PLAN 8 (listed under C: there, but the path is on D:) |
| **wave 3 worst moment** | about 66 | about 34 (during the suite) | sum |
| after wave 3 | about 79 | about 47.7 | sum |
| step 1.4 `<BKX>` | - | -0.22 | 192 MiB ESP + 13 MB small files |
| optional fresh sparse backup (1.4, only if lapis.bin changed) | - | -23.5 (non-zero bytes; measured NTFS sparse allocation 1.001 x written in a 2,000-chunk probe) | scan + probe |
| W4 rehearsal VM (2.3) under `D:\gb-w2test\` | - | about -8 temp | as wave 3 VM-2 |
| step 2.9 `<BKP>` post-upgrade ESP snapshot (review) | - | -0.22 | as `<BKX>` |
| **W4 worst moment** | about 79 | about 16 (fresh sparse backup + rehearsal VM at once); **about 15 with the review's re-measure** (54.08 GB now) and `<BKP>` | sum |
| optional: sparse backup of the *upgraded* VM once the user accepts it (review) | - | -23.5 or more (grows with the user's data) | scan |

Every floor holds with margin. A second **full** copy (49.13 GB) is not planned. After wave 3 it would leave
D: at about -1.4 GB, below the floor. That is why the fresh-backup fallback is sparse. The optional post-acceptance
backup fits only while the rehearsal VM is gone (about 46.6 - 0.44 - 23.5 = about 22.6 GB left on D:).

### 1.3 Schedule

* `<BK>` exists already (made 04:34-04:45, before the ISO suite). It is valid as long as `lapis.bin` is unchanged.
  The VM is off and nothing else writes lapis.bin (build_esp refuses without `GB_WRITE_LAPIS=1`; wave 3 only reads it).
  **But the user may start the VM between step 1.4 (about 07:15) and the write (about 12:00).** Each boot changes
  /data and the ESP's `loader/random-seed`. So the pairing is proved again right before the write (2.4 step 0, P9);
  if it fails, a fresh backup comes first (review VM-1).
* Step 1.4 (re-validate + extras) runs **after wave 3's R-11 FULL ISO suite has finished** (`<W3_ISO_SUMMARY>`, every
  id PASS) and **not while a wave-3 throwaway VM is booting** (`vmrun -T ws list` shows no `gbw2.vmx`). That keeps the
  backup's 2 x 46 GB of reads away from the suite's and VM tests' timing. Preferred: after R-14. Duration about
  7 min at background I/O priority: live hash about 3 min (measured 179.6 s), `<BK>` re-verify about 4.5 min
  (measured 256 s), extras under 10 s.

### 1.4 Procedure

All commands run in PowerShell. The VM must be off. Use `python -I -B` throughout.

```powershell
# 0. the backup tool (Appendix B) goes to C:\GB-build\native\dgpu\w4\tools\gb_vmbackup.py first (extraction + sha check there)
$T  = "C:\GB-build\native\dgpu\w4\tools\gb_vmbackup.py"
$BK = "D:\GB-vm-backup\2026-10-26-pre-upgrade"
$BKX = "D:\GB-vm-backup\2026-10-26-pre-upgrade-extra"

# 1. gate: VM off, wave-3 ISO suite done, no throwaway VM running
& "C:\Program Files\VMware\VMware Workstation\vmrun.exe" -T ws list    # must NOT list C:\GB-x86\lapis-boot.vmx (nor gbw2.vmx)
Get-Content <W3_ISO_SUMMARY>                                           # every test id PASS
Get-PSDrive C,D | ForEach-Object { "{0}: {1:N2} GB free" -f $_.Name, ($_.Free/1e9) }   # C >= 15, D >= 4

# 2. re-validate <BK>: every file against its SHA256SUMS (read-only, about 4.5 min)
python -I -B $T verify $BK                                             # last line "verify OK"

# 3. extras + live hash in one go (writes only <BKX>; lapis.bin is read "rb")
python -I -B $T backup $BKX --no-disk
#    -> manifest.json lapis_bin.sha256 must equal the lapis.bin line of $BK\SHA256SUMS:
python -I -c "import json,sys; m=json.load(open(sys.argv[1]+'\\manifest.json')); s=open(sys.argv[2]+'\\SHA256SUMS').read(); print('PAIRED' if m['lapis_bin']['sha256'] in s else 'MISMATCH: lapis.bin changed since <BK>')" $BKX $BK
```

If step 3 says `MISMATCH` (lapis.bin changed after 04:45 today, for example because the user booted the VM), `<BK>`
is out of date for a full restore. Take a fresh sparse backup **next to** it, and do not delete `<BK>`:

```powershell
$BK = "D:\GB-vm-backup\$(Get-Date -Format yyyy-MM-dd)-pre-upgrade-2"
python -I -B $T backup $BK                 # refuses unless D: has 26 + 3 + 1 GB free; aborts if D: < 4 GB mid-copy
python -I -B $T verify $BK
```

Pass criteria for step 1: `verify OK` on `<BK>`; `PAIRED`. `<BKX>\manifest.json` must show `slot_attrs` b =
(15,0,1), EFI-SYSTEM at offset 142606336 size 201326592, and both GPT sha256 values equal those in section 0. If
any of this differs, stop and re-read section 0: something changed the VM since this plan.

### 1.5 The tool (`gb_vmbackup.py`, Appendix B)

`backup` (full sparse copy, or `--no-disk` extras), `verify`, `compare` (which partitions differ, read-only) and
`restore` (`--esp-only` or full, differential, in place). Guards:

* `vmrun -T ws list` must not list the vmx;
* no `lapis-boot.vmx.lck`, `lapis.vmdk.lck` or `lapis.bin.lck` folder may exist (stale `*.REDO_*.lck` folders are
  only reported);
* lapis.bin size and mtime must not change during the backup;
* the source is hashed while it is copied, and the copy is then re-read and compared;
* the space floors above are checked;
* restore needs `--yes`, verifies the backup first, keeps the current small files in `pre-restore-<time>\`, and makes
  the restored files writable again (backup files are read-only, and `copy2` would carry that over to the vmx).

Host test on a synthetic 64 MiB GPT disk (scratchpad `w4vm\test_gb_vmbackup.py`): **35 PASS, RESULT OK**. The test
covers:

* the refusals: a lock folder, too little space, a DEST that is not empty, no `--yes`, a tampered backup;
* the sparse allocation;
* the slot attributes;
* the read-only flags;
* compare after a simulated upgrade (EFI-SYSTEM and userdata named);
* `--esp-only` (only userdata still differs) and a full restore (identical, sha256 equal, vmx restored and writable);
* `--no-disk` extras;
* a full restore from a plain `Copy-Item` folder with `<sha>  <name>` sums, which is the format `<BK>` uses.

On the real data: `compare <BK>` against the live 45.76 GiB lapis.bin printed `compare: identical` in 136 s, at
background priority and read-only.

---

## 2. Upgrade

### 2.0 Preconditions (all must hold; the user VM stays off until 2.6)

| # | Precondition | Evidence |
|---|---|---|
| P1 | step 1 passed (`verify OK`, `PAIRED`) | console output, kept in `<BKX>\..\w4-upgrade-<date>\` |
| P2 | wave 3 finished: R-14 PASS, R-15 done (the pipeline files are no longer being edited), k15 staged by R-05 (`stage_kernel.py k15 --dist <K15_DIST>`): `boot\kernel.bin` = k15, `inject\modules\*.ko` and `inject\native\modules\{zsmalloc,zram}.ko` k15-signed, `inject\native\modules\modules.load` line 1 `# kernel build k15 ...` | **met (2026-10-26):** wave 3 final report (repo `81de83e` pushed); `kernel.bin` md5 `dd060ea5...`, both load lists `# kernel build k15 (bzImage md5 dd060ea5b74d5961eb2e6aa09ebfb9f8)`; `k15_release.txt` = `6.18.21-android17-5-g3c2c2f588d3f-ab16302715` (tests\w2a AC-102) |
| P3 | wave 3 R-13 (VM-2) proved k15 in VMware: `GB_VNNI_KAT: PASS` and `gb_vnni_kat: zram swap on` under k15 (zram swap in a VMware guest with the k15 modules) | **met:** `C:\GB-build\native\dgpu\w3\evidence\build\vm2\` (wave 3 build report). Note: since W4 the KAT tree no longer sets up zram itself; it waits for mmd's zram swap of the VM tree (2.2(a)) |
| P4 | the HOLD-capable gb_slotsync is in `inject\slotsync\` | **met** (wave 3 R-03; V12 compares the packed copy with it in every build) |
| P5 | Task A accepted its software Gatekeeper; for the user's **real PIN** (not for the upgrade itself) also GATEKEEPER.md 7.8 W4-GK-1 applied and tested, or accepted by the user in writing | `<GK_DIR>\out\ACCEPTANCE.txt` (0.2.0, `505b404d...3d51`; build_esp refuses an ACCEPTANCE version below 0.2.0); W4-GK-1 still needs its device result (9.2 step 8) before the real PIN |
| P6 | the W4 pipeline delta (2.2) is applied by the pipeline owner and its tests pass | **met (2026-10-26):** 2.2(a)-(d) applied; tests in 2.2(d) all pass (evidence `w4\evidence\integration\`) |
| P7 | the rehearsal (2.3) passed on a throwaway VM | **met (2026-10-26):** `C:\GB-build\native\dgpu\w4\evidence\rehearsal\` (run 2 `SUMMARY.txt` + `EVAL.txt` + `run2_zramrace\`: all pass), rehearsed tree `1e939bc0...2934` |
| P8 | the two stale lock folders are moved away (2.1), so the build_esp guard passes | `Get-ChildItem C:\GB-x86 -Filter *.lck -Force` lists nothing |
| P9 | (review) immediately before the write: the live lapis.bin still equals `<BK>` (`PAIRED`), or a fresh backup was taken after the last VM use | 2.4 step (0) output |

### 2.1 Clear the stale VMware REDO lock folders (needs the user's OK: it touches `C:\GB-x86`)

`build_esp.py`'s guard refuses any `lapis*.lck` in `C:\GB-x86`. It has no way to tell the two empty leftovers from a
real lock, so today `GB_WRITE_LAPIS=1 build_esp.py` cannot write lapis.bin at all. Don't relax the guard. Move the
folders instead, only after confirming the VM is off and the folders are empty:

```powershell
& "C:\Program Files\VMware\VMware Workstation\vmrun.exe" -T ws list          # no lapis-boot.vmx
Get-ChildItem -Force C:\GB-x86\lapis.vmdk.REDO_a09248.lck, C:\GB-x86\lapis.vmdk.REDO_a27864.lck   # both empty
New-Item -ItemType Directory -Force D:\GB-vm-backup\stale-locks-2026-10-26 | Out-Null
Move-Item C:\GB-x86\lapis.vmdk.REDO_a09248.lck, C:\GB-x86\lapis.vmdk.REDO_a27864.lck D:\GB-vm-backup\stale-locks-2026-10-26\
Get-ChildItem C:\GB-x86 -Filter *.lck -Force                                 # nothing
```

Undo: move them back (they have no content). VMware recreates `.lck` folders itself whenever it opens the disk.

### 2.2 W4 pipeline delta (APPLIED 2026-10-26 by the W4 integration; below: the reviewed proposal, each part followed by an "As built" note)

All changes are in the **VM tree** (`gbstage`), except the shared verify rules, the Gatekeeper (both trees) and the
bring-up fixes (native, (f)). Line numbers in the proposal text refer to `C:\GB-x86\build_esp.py` as of 26/10 04:17;
the "As built" notes name functions instead. Pre-edit copies: `C:\GB-x86\archive\pre-w4\` (SHA256SUMS.txt).

**(a) zram/zsmalloc in the VM tree.** These are the k15-signed copies that `stage_kernel.py` already stages for the
native tree (`stage_kernel.py` NATIVE_EARLY), so stage_kernel needs no change. Insert after the
test_power block (about line 1100):

```python
# W4 (user decision 2026-10-26, dgpu\w4\VM_UPGRADE.md 2.2a): zram for lapis's mmd in the VM. The modules only: lapis's
# mmd_setup (after boot_completed, mmd.zram.size=150%) writes disksize itself, runs mkswap and swapon. A disksize write
# here would make its own write fail (zram: EBUSY on an initialised device) and abort it again.
_ZRAM = [(n, os.path.join(HERE, "inject", "native", "modules", n)) for n in ("zsmalloc.ko", "zram.ko")]
VM_ZRAM = all(os.path.exists(p) for _, p in _ZRAM)
if VM_ZRAM:
    for _n, _p in _ZRAM:
        MODS.append((_n, open(_p, "rb").read()))
    _vm_insmod = b"    insmod /odm/lib/modules/vmwgfx.ko\n"
    GB_RC = GB_RC.replace(_vm_insmod, _vm_insmod + b"    insmod /odm/lib/modules/zsmalloc.ko\n"
                                                    b"    insmod /odm/lib/modules/zram.ko\n", 1)
print(f"VM zram (mmd): {'ON' if VM_ZRAM else 'off'}", flush=True)
```

Evidence for the design:

* The VM's `/system/lib/modules` has a lapis `zram.ko` (wave-2 `vm2\12-zram_check.txt`: "1 zram, 104 files"), and
  our kernel refuses it: it carries Google's signature, and the kernel enforces module signatures
  (`MODULE_SIG_PROTECT`, `stage_kernel.py` docstring). **Measured on the rehearsal (2026-10-26):** lapis's vendor
  `insmod_sh_desktop_earlyinit` does try it at every boot (`modprobe zram`), and the kernel answers `zram: Cannot use
  protected symbol ...` / `Unresolved symbol ... (err -13)`; that attempt can race our insmod (see "As built (a)").
* The abort literal `failed to write zram disk size` is AOSP `system/memory/mmd/src/zram/setup.rs` (main). That code
  also runs `mkswap /dev/block/zram0` and `swapon` itself, and AOSP `mmd.rc` does the setup only after
  `boot_completed`, so an early-init insmod is in time.
* lapis ships `/system/bin/mmd_setup` instead of `mmd --setup-zram`, which is a different build of the same code
  (INFERRED from the identical message).
* The `EBUSY` behaviour is Linux `zram_drv.c` `disksize_store()` (INFERRED, not re-read for 6.18).
* Do **not** reuse the wave-3 KAT rc fragment (`build_esp.py:2274-2276`): it writes `disksize` itself.

Side effects to accept or tune:

* zram disksize 150 % of about 11.6 GiB guest RAM gives about 17 GiB of virtual swap. Only the compressed pages use
  RAM.
* mmd writeback creates an **8 GiB fallocated backing file on /data**, and only when /data has >= 8192 MiB free
  (INFERRED from the property names and `create_zram_writeback_device()` in setup.rs). /data had 18.21 GiB free. If
  that is too much, add `mmd.zram.writeback.enabled=false` to the VM's `ODM_BUILD_PROP`. Leave it as shipped unless
  the user objects.

*As built (a):*

* `stage_kernel.py` `VM_MODS` += `zsmalloc`, `zram` (zram depends only on zsmalloc: `modinfo` on
  `/build/dist15`), so every future kernel staging puts them into `inject\modules` (the VM module set) and
  vermagic/dependency-checks them with the VM set; the proposal had read them from `inject\native\modules`. For the
  staged k15 they were copied from `inject\native\modules` into `inject\modules`: byte-identical, and equal to the
  builder's `/build/dist15/zsmalloc.ko` (`040f2372...e0c0`) and `zram.ko` (`b183ecb1...a861`) by sha256.
  `stage_kernel.py k15 --dist /build/dist15 --dry-run` (read-only) with the new list: `77 modules found, missing:
  none`, one vermagic, no PROBLEM.
* `build_esp.py`, block `VM_ZRAM` after the host-battery block: both modules **required** (a VM build without
  them stops), `insmod zsmalloc.ko` then `zram.ko` right after `vmwgfx.ko` at early-init, nothing else; prints `VM
  zram (mmd): ON (...)`. `_check_vm` and verify_image V28 refuse any `disksize`, `mkswap` or `swapon` in the VM
  gb.rc, check the staged bytes, the k15 vermagic and signature, and the insmod order.
* **Rehearsal finding (2026-10-26), fixed: a load race with lapis's own `modprobe zram`.** The evidence bullet above
  ("nothing loads it") was wrong: lapis's vendor service `insmod_sh_desktop_earlyinit` (`/vendor/etc/init/init.insmod.rc`,
  early-init, its action runs before gb.rc's) runs `modprobe -a -d /system/lib/modules -d /vendor/lib/modules zram`
  (`/vendor/etc/init.insmod.common.cfg`). Our kernel refuses that Google-signed module (`zram: Cannot use protected
  symbol ...`, err -13). In 1 of 17 rehearsal boots its attempt was in flight when gb.rc's `insmod
  /odm/lib/modules/zram.ko` ran, which then failed with `Device or resource busy`: no zram0, and mmd_setup aborted
  exactly as before W4 (`zram setup failed: failed to write zram disk size`, SIGABRT). Fix in the VM tree: the
  `gb_zram` oneshot at post-fs-data (`/odm/gb_zram.sh`, `build_esp.GB_ZRAM_SH/GB_ZRAM_RC`) waits up to 10 s for that
  vendor service to end, then loads our zsmalloc.ko / zram.ko only if they are missing; one kmsg line either way
  (`gb_zram: zram0 present (early-init insmod)` normally). It never sizes or enables zram. Checked by `_check_vm`,
  verify_image V28, `tests\w4` ZRAM; on the rehearsal VM the race's failure mode was reproduced on purpose (swapoff,
  `rmmod zram`, the vendor's exact `modprobe` refused, then `gb_zram.sh` loaded ours: zram0 back), evidence
  `w4\evidence\rehearsal\run2\`.
* The test-only KAT tree (`--vm-test-kat`) inherits zram from the VM tree now: it adds only `vnni_kat` and
  `gb_vnni_kat.sh`; its rc no longer insmods or writes disksize (that would race mmd), and its script waits up to
  300 s for mmd's `zram0` in `/proc/swaps`, then logs `gb_vnni_kat: zram swap on (mmd, after N s)` (it still contains
  P3's `zram swap on`) or `... zram swap FAILED (...)`. Tested on the builder: `tests\w4\kat_sh_test_w4.py`
  (busybox sh and bash; zram at once / 3 s late / never).

**(b) fs-verity on /data in the VM.** In the VM tree entry (about line 1650) use `FSTAB_NATIVE` (the same
`_with_fsverity(FSTAB)` bytes the native tree uses; rename it `FSTAB_VERITY`) instead of `FSTAB`, and change the
`_with_fsverity` docstring. Effect on the **existing** /data:

* At the first boot fs_mgr runs `prepare_fs_for_mount()` -> `tune_verity()` before the mount. It returns at once,
  because `RO_COMPAT_VERITY` is already set (section 0). There is no `tune2fs` run, no superblock change, and no
  `Enabling ext4 verity on` line.
* If the bit were missing (for example a future fresh /data after `wipe_data.py`), fs_mgr would run
  `/system/bin/tune2fs -O verity /dev/block/by-name/userdata` once and log `Enabling ext4 verity on ...`; a failure
  logs `Failed to run /system/bin/tune2fs ...` and sets `FS_STAT_ENABLE_VERITY_FAILED`. That is AOSP `fs_mgr.cpp`
  (main). It needs `/sys/fs/ext4/features/verity` = `supported`, which `CONFIG_FS_VERITY=y` gives.
* **It is one-way:** `tune2fs 1.47.0` refuses `-O ^verity` ("Clearing filesystem feature 'verity' not supported"),
  as tested on a 64 MiB scratch image on the builder in `/tmp`, removed afterwards. Rollback is a backup restore,
  never a clear.
* The old k13 kernel also has `CONFIG_FS_VERITY=y`, so an ESP-only rollback still mounts this /data.
* Why the user wants it: gbOS86 HANDOFF.md:478 (reporters' analysis, untested on hardware) blames Play Store
  `INSTALL_FAILED_BAD_SIGNATURE` on a /data without `-O verity`. Our VM's /data already has the feature, so if Play
  updates fail in the VM, that is **not** the cause (INFERRED). The Play Store check in 2.7 is a regression check.

*As built (b):* `FSTAB_VERITY = _with_fsverity(FSTAB)` (`FSTAB_NATIVE` kept as its alias) is the fstab of **both**
trees, byte-identical; `_check_vm`, `_check_native` and verify_image V26 (both kinds) require it. As section 0 now
records, lapis's fs_mgr sets the verity bit on /data without the flag too, so the flag is redundant but harmless and
makes the intent explicit; on the user's /data (bit set) fs_mgr changes nothing.

**(c) the software Gatekeeper.** Rewritten by the review to match what Task A built; the authority is
GATEKEEPER.md section 7 (files 7.1, VINTF 7.2, rc 7.3, SELinux 7.4, gb.rc/build_esp 7.5, checks 7.5). What the VM tree
must end up with:

* `gbstage/vup/bin/hw/android.hardware.gatekeeper-service.gb` (0755, sha256 = `<GK_DIR>\out\android\SHA256SUMS`, or
  the W4-GK-1 rebuild's) and `gbstage/etc/vintf/manifest/android.hardware.gatekeeper-service.gb.xml` (0644,
  byte-identical to `<GK_DIR>\vintf\`: `override="true"` on both HALs) in `_VM_ENTRIES`, and
  `<GK_DIR>\rc\gb_gatekeeper.rc` appended to the VM `GB_RC` (`class early_hal`, `user hsm`, `group hsm`,
  `seclabel u:r:hal_gatekeeper_default:s0`, two `interface aidl` lines, `onrestart restart gatekeeperd`,
  `mkdir /data/vendor/gatekeeper 0700 hsm hsm` at post-fs-data). Not `class hal` / `user system` (system_server and
  gatekeeperd would then be able to read the device key K_dev).
* **Keep** the VM `gb.rc` line `mount none /odm/empty_vintf.xml /vendor/etc/vintf/manifest/android.hardware.gatekeeper-service.trusty.xml bind`
  (about line 909): one declaration only (ours); `override` covers the case that an OTA renames the Trusty fragment.
* **Keep** GBINIT's whiteout of `android.hardware.gatekeeper-service.trusty.rc` (about line 267) and the `SK_STUB` over
  the Trusty binary (about line 1671), so the Trusty service never starts (it crash-looped about 81 times in the VM
  before).
* Weaver stays undeclared; LockSettingsService uses its Gatekeeper branch (GATEKEEPER.md 2).
* The binary, the fragment and the rc stanza ship together or not at all: a fragment without the service hangs boot.
* What the user gets (GATEKEEPER.md 3-6): a per-device random key in `/data/vendor/gatekeeper` (not a compile-time
  key), persistent throttling, correct auth tokens (user-auth-bound Keystore keys work; the synthetic-password key
  itself is one, so a working unlock proves it). A convenience lock: root, the COM2 console and the host can read
  /data (no file-based encryption).

*As built (c):* GATEKEEPER.md 7.9. In short: `inject\native\components\gatekeeper.json` (= the reviewed draft except its
comment) is the one source for both trees; the native tree gets it through the component loader, the VM tree gets
the same binary (`505b404d...3d51`, 0.2.0) and fragment (`48cd680b...`) through `_add_entry` and the same rc block
(`# ---- component gatekeeper ----` + `rc\gb_gatekeeper.rc`) appended at the end of the VM `GB_RC`. The Trusty
fragment bind, GBINIT's whiteout, `SK_STUB` and the Weaver bind are unchanged and now checked. No SELinux change
(GATEKEEPER.md 7.4: the types are already `typepermissive`).

**(d) the checks that guard the user's VM tree today must be updated to the new rules, not removed.**

* `build_esp.py` `_check_vm()` (about lines 2111-2125):
  * the VM fstab must equal `FSTAB_VERITY`;
  * remove `zram.ko`/`zsmalloc.ko` from the KAT-only list, but keep `vnni_kat`/`gb_vnni_kat.sh` forbidden;
  * add `b"zram0/disksize" not in gb.rc` (only mmd sizes zram);
  * add one `IGatekeeper` service and no Trusty Gatekeeper `.rc`; the Trusty fragment bind present; the
    Gatekeeper binary, fragment (byte-identical) and rc stanza all present or all absent (GATEKEEPER.md 7.5).
* `verify_image.py`:
  * **V26**: the VM fstab = inject fstab plus `,fsverity` on the userdata line, the same rule as native;
  * **V28**: zram/zsmalloc allowed in kind `vm` when both are present, staged copies, insmodded in gb.rc, with no
    disksize write; the KAT stays `vmtest-kat` only;
  * **V1**: accept 201,326,592 B for the lapis ESP. Suggest `--esp-size 201326592`; today V1 always reports the 192 MiB
    VM ESP as a problem (section 0);
  * a Gatekeeper rule as in `_check_vm`.
* Tests in `C:\GB-x86\tests\w4\`: fixtures for each of these, and the wave-2/3 VM fixtures updated.
* Docs (after R-15, AUX owner):
  * `docs/VMWARE.md` "Limitations" (lock screen: software, convenience only; zram; fs-verity);
  * README "Screen lock PIN" row;
  * `docs/ARCHITECTURE.md` (both-trees list);
  * `docs/OTA.md` (the ESP headroom number stays 192 MiB).

*As built (d):*

* `build_esp.py`: `_check_gatekeeper(ents, tree, label, present)` (binary 0755 and the accepted bytes; the reviewed
  fragment 0644 and the only ODM VINTF fragment; the rc block once; exactly one `interface aidl
  ...IGatekeeper/default`; Trusty fragment bind and Weaver bind once each; the Trusty binary = `SK_STUB`; GBINIT's
  Trusty `.rc` whiteout) used by `_check_vm` and `_check_native`; `_check_vm` as listed in the proposal above (fstab,
  zram, KAT, Gatekeeper); `_gatekeeper_inputs()` refuses at build time a binary that is not its `SHA256SUMS` line, an
  `ACCEPTANCE.txt` below version 0.2.0 or without that sha256, a NEEDED library outside LLNDK
  (`libbinder_ndk libc libdl liblog libm`), a non-reviewed fragment, a CRLF rc or one without a GATEKEEPER.md 7.3
  line, unknown manifest keys or dests. The lapis.bin write in `__main__` now builds the VM ESP with
  `min_free = max(initrd) + 16 MiB` (the OTA's second initrd) and prints the tree record (`vm_tree_record()`).
* `verify_image.py`: V1 `--esp-size BYTES` (the lapis ESP: 201326592) and, for every VM kind, free >= max(initrd) +
  16 MiB; V26 both kinds; V28 as proposed (plus the insmod order); **V29** the Gatekeeper rules of
  `_check_gatekeeper` (component sources read from the manifest; ELF interpreter and NEEDED parsed); **V30**
  `--expect-inject-sha256 HEX` (gb/inject.gz identity); new kind `vmtest-nogk` (2.3).
* Tests (all pass, logs in `w4\evidence\integration\`): `tests\w4\test_w4.py` (new, 93 checks: both trees, build-time
  refusals, the ELF reader, zram/fstab, the rehearsal variants incl. verify_image subprocess runs on in-memory ESPs
  written to a scratch file, the lapis write guard, the tree diff against `archive\pre-w4`); `tests\w4\kat_sh_test_w4.py`
  (builder); `tests\w2a\ta_set_a.py` (8/8 groups, k15 expectations); `tests\w2b\test_verify_image.py` (TB-3, incl. today's
  image 377310ee: fails exactly the expected V12 + V29, see 2.4 note); `tests\w2b\test_trees_from_build_esp.py`;
  `tests\w2b\test_iso_static.py --dry-run`; `tests\w3a\test_w3a.py`; the bring-up harness on the builder
  (`tests\w3a\bringup\test_bringup.sh`, 76/76; the pre-W4 script fails 7 of them, the LOW-1/LOW-2 cases among them).
* Not done here (other owners): the repo docs listed above, and the repo sync (`collect.py` still excludes
  `native/gatekeeper/`; publishing it needs its own audit; the binary itself is never committed).

**(e) wave-3 fixes that reach the VM through the normal rebuild.**

* **Kernel and modules:** k15 = item 3, the #UD VNNI emulator fix and `/sys/kernel/gb_vnni/summary`. This applies
  directly: the vmx masks AVX-VNNI (`cpuid.7.1.eax`), so VEX VNNI code in the guest traps and is emulated.
* **gb_slotsync with the item 13 HOLD rule.**
* **The item 12 GBINIT bytes.** The bring-up gate needs `gb.hw=native` and `gb.overlay=1`, so it is inert in the VM.
* **Item 1:** the VM entry keeps `8250.nr_uarts=4 console=ttyS0,115200` (V24).
* **Unchanged for the VM:** items 2, 4 (native part), 8, 9, 10, 11, 14 and 18 are native-only.
* **No vmx change** is needed.

**(f) wave-3 audit leftovers in the pipeline (native bring-up, inert in the VM).** Applied in `GB_BRINGUP_SH`:

* LOW-1: the boot-disk scan rescans every 0.5 s until the whole-disk set (names, device numbers, sizes, GUID
  matches) has not changed over 4 rescans (2 s), and only then requires exactly one match; at most 30 rescans (15 s),
  under GBINIT's `timeout 40`. A disk still changing after 15 s: "did not settle ... skipped".
* LOW-2: modules.load, build.prop and every overlay file are written whole to `<file>.gbbu` and renamed over the target
  with `mv -f -T` (a stale `<file>.gbbu`, even a symlink, is removed first, never written through); a failed rename
  leaves the old file and is logged ("rewrite FAILED (left unchanged)", "overlay: FAILED").
* Harness (builder, shipped busybox): new cases B3s (a cloned boot GUID appearing 1 s late -> skipped; the boot disk
  appearing 2 s late -> bound and applied; another disk late -> no effect), B10 (every rename failing -> nothing
  changed) and the rename checks in B2: 76/76 pass; the pre-W4 script fails 7 checks (the late clone is bound by the
  old first-scan rule). The ISO/USB/export images do not carry this yet (no image was built: T5 lists it as stale).

### 2.3 Rehearsal on a throwaway VM first (never `lapis.bin`)

Same rules as wave 2/3 VM tests, rebased for W4 (2026-10-26):

* everything below `D:\gb-w4test\` (the W4 throwaway root; `build_usb.py` refuses the rehearsal variants anywhere
  else);
* a throwaway VM of at most 4 GB RAM and 2 vCPUs, made with `C:\GB-build\native\dgpu\1b\evidence\w2\tools\mkvm.ps1`
  (pointed at `D:\gb-w4test\vm\`) + `vmguard.py`;
* one VM at a time;
* `vmrun` only on the throwaway vmx below `D:\gb-w4test\vm\`; never `lapis-boot.vmx`, never `gbsh.py` / the user VM's adb.

Build the rehearsal disk with the **test-only variant that contains exactly the new VM tree** (`build_esp.INJECT_CPIO`,
the bytes `GB_WRITE_LAPIS=1 build_esp.py` will later write into lapis.bin):

```powershell
python -I -B C:\GB-x86\build_usb.py --vm-test --vm-test-rehearsal --sparse --userdata-gib 8 --out D:\gb-w4test\vm\disk.img
#   -> disk.img, disk.img.sha256 and disk.img.vmtree.sha256 ("<sha256>  gb/inject.gz"): the tree identity 2.4 must match;
#      verify_image ran with --expect-inject-sha256 already; note the "super GUID: ..." line for the ESP builds below
# the two ESPs for the switches of GATEKEEPER.md 9.5 (same super GUID, so either ESP boots this disk):
python -I -B C:\GB-x86\build_usb.py --vm-test --vm-test-rehearsal --esp-only D:\gb-w4test\esp\w4-tree.img --super-guid <G>
python -I -B C:\GB-x86\build_usb.py --vm-test --vm-test-nogk      --esp-only D:\gb-w4test\esp\old-tree.img --super-guid <G>
#   old-tree.img = the same VM tree without the Gatekeeper (binary, fragment, rc block): the pre-W4 stand-in
```

Space: the sparse disk takes about 8-9 GB on D: (35 GB free on 26/10, floor 3 GB), the two ESPs 1 GB. Its /data is
blank, so this boot formats /data and fs_mgr then sets the verity bit (lapis does that on a fresh /data with or without
the flag, section 0): expect `Enabling ext4 verity on /dev/block/by-name/userdata` exactly once. That is the one path
the user's VM will not take. Run every check of 2.7 that does not need the user's Google account. Then set a test PIN,
reboot, verify it, and clear it. Pass = every 2.7 row except Play Store, plus that verity line exactly once and no
`Failed to run /system/bin/tune2fs`; V-4/V-5 must show mmd's zram swap (`/proc/swaps` zram0; no `zram setup failed`;
the 8 GiB rehearsal /data is below mmd's 8 GiB writeback floor, so no writeback file there).

Added by the review (VM-4): the rehearsal also runs, on the same throwaway VM,

* GATEKEEPER.md 9.2 steps 1-10 (record which of the two step-8 outcomes happened) and every test of 9.5, including
  the review's recovery tests: error counting and the counter clearing of GATEKEEPER.md 8.2, the reboot escalation,
  **fix-forward** (PIN set -> ESP-only rollback to an "old tree" ESP -> locked out -> write the W4 ESP back -> 8.2 ->
  the PIN unlocks, `gk_key.v1` unchanged) and **rollback after a clear** (PIN cleared -> ESP-only rollback -> boots
  without a lock screen, data present). These prove 3.1 and 3.2 before the user's VM depends on them. The "old
  tree" stand-in is an ESP built by the same pipeline without the 2.2(c) Gatekeeper delta (`old-tree.img` above,
  `--vm-test-nogk`, verify_image kind `vmtest-nogk`), written only into the throwaway disk's EFI-SYSTEM partition
  (never `lapis.bin`); with `w4-tree.img` saved too, each switch is a raw write of one saved image.
* The two 9.5 negative tests that need a tree breaking the pairing rule (service without fragment, fragment without
  service) cannot be built by the pipeline any more (`_check_gatekeeper` refuses them): GATEKEEPER.md 9.5 says how
  to run them by hand on the throwaway disk, or skip them (they confirm the documented failure modes only).
* `gb_vmbackup.py` cannot run against the throwaway disk (it expects `lapis.bin`, `lapis-boot.vmx`, `lapis.vmdk`,
  `lapis-boot.nvram` by name); its `--esp-only` path is covered by its host test (1.5).

Delete the throwaway VM afterwards. Evidence goes to `C:\GB-build\native\dgpu\w4\evidence\rehearsal\`.

**Result (2026-10-26): PASS.** Tools in `evidence\rehearsal\tools\` (`w4vm.py`: the one throwaway VM, its own pipe
`\\.\pipe\gbw4_con` and adb address, ESP switches, userdata wipe; `run_rehearsal.py`: the whole run with automatic
checks; `mk_negative_esp.py`: the two hand-made negative ESPs; guest scripts `g_*.sh`; `fsv\gb_fsv_test`, a static
fs-verity test tool built with the NDK). Run 1 (`boot1\`..`boot17\`) found the two defects (2.2(a) "As built", and
`gb_kmsgdbg`, GATEKEEPER.md 7.3); after both fixes all host suites passed again (`fix1\`, `fix2\`: w4 117, w3a 106,
TB-3, trees, w2a, ISO static dry run) and run 2 re-did everything on a fresh disk with the final tree
`1e939bc0...2934` (`run2\SUMMARY.txt`: 65 PASS; the 3 FAIL lines are 2 evaluator errors re-evaluated as PASS in
`run2\EVAL.txt` and 1 first test version, redone as `run2_zramrace\`: PASS). Per 2.3's pass rule: every 2.7 row that
needs no Google account, the verity line exactly once on the fresh /data (twice: first boot and after the 8.5 wipe),
no `Failed to run /system/bin/tune2fs`, V-4/V-5 mmd's zram swap and mmd_setup exit 0 in all 16 completed boots of run 2 (the 17th is the deliberate hang);
GATEKEEPER.md 9.2 steps 1-10 (step 8: the 0.2.0 outcome, both rounds) and every 9.5 test incl. fix-forward and
rollback-after-clear, with the user data intact after each recovery (GATEKEEPER.md 9.6). Host after all boots: slot
attributes unchanged, GPT valid, boot/vbmeta/super identical to lapis.bin, ESP == the built image except
`loader/random-seed`. Not covered by a throwaway VM (2.7): V-10 Play Store (no account), V-14 host battery, V-15
audio (the throwaway has no sound device), V-16 scrcpy, V-17 Terminal (no vhv): unchanged by W4, checked on the user's
VM. The throwaway VM was powered off; its folder `D:\gb-w4test\` (VM files, the 20.8 GiB sparse disk, the test ESPs)
still has to be deleted by the user (an automatic safety check refused the removal; it holds no user data).

### 2.4 Preview the VM ESP, then write it

```powershell
# $T, $BK, $BKX as set in 1.4
$U = "D:\GB-vm-backup\w4-upgrade-$(Get-Date -Format yyyyMMdd-HHmm)"; New-Item -ItemType Directory $U | Out-Null
# (0) review VM-1 / P9: the backup must still match the live disk NOW (the user may have used the VM since 1.4).
#     VM off first; then a live hash (read-only, about 3 min) compared with <BK>'s lapis.bin line:
& "C:\Program Files\VMware\VMware Workstation\vmrun.exe" -T ws list          # no lapis-boot.vmx
python -I -c "import hashlib,sys; h=hashlib.sha256(); f=open(r'C:\GB-x86\lapis.bin','rb'); [h.update(b) for b in iter(lambda: f.read(4<<20), b'')]; s=open(sys.argv[1]+'\\SHA256SUMS').read(); print('PAIRED' if h.hexdigest() in s else 'CHANGED '+h.hexdigest())" $BK
#   PAIRED -> go on. CHANGED -> STOP and report to the user (W4 hard rule, 2026-10-26: lapis.bin is written only while it
#   still equals <BK>'s lapis.bin, sha256 640c8539...cc55). Only on the user's word: a fresh sparse backup next to <BK>
#   (1.4, "If step 3 says MISMATCH"), a new <BKX> with "backup <new> --no-disk", those two as $BK/$BKX below and in 3.x.
# (1) preview: the exact ESP build_esp.py would write, into a file (Appendix C; lapis.bin only read; no builder)
$env:GB_NO_BUILDER = "1"
python -I -B C:\GB-build\native\dgpu\w4\tools\vm_esp_preview.py $U\esp-preview.img
$R = (Get-Content C:\GB-build\native\dgpu\w4\evidence\rehearsal\run2\w4-tree.img.vmtree.sha256 -TotalCount 1).Split()[0]
#   = the tree rehearsed in 2.3 run 2 (1e939bc047f6d4092aeb61b7705235ecada75c1c44c742fbc6ffcf94a5842934; the throwaway
#     disk and its sidecar below D:\gb-w4test were deleted after the rehearsal, this copy is the record)
python -I -B C:\GB-x86\verify_image.py --esp $U\esp-preview.img --expect-tree vm --esp-size 201326592 --expect-inject-sha256 $R --krel C:\GB-build\kernel\k15_release.txt
#   pass: RESULT OK (V1 with --esp-size checks the 192 MiB size and free >= max(initrd) + 16 MiB; V30: the preview's
#   gb/inject.gz IS the rehearsed tree; any difference = the pipeline changed after the rehearsal: stop, rehearse again)
#   and V4 krel == <K15_REL>; V12 ok (gbinit, gb_slotsync byte-identical); V18 ok; V24 ok; V26/V28/V29 ok
#   and the preview's printout: "VM zram (mmd): ON", "software Gatekeeper: ON", "VM tree: <sha>  gb/inject.gz; ..."
#   (in-memory preview after the rehearsal fixes, 26/10 16:30, k15 + W4: used 95.2 MiB, free 93.8 MiB, min_free
#   67.2 MiB, 98 cpio entries; gb/inject.gz 1e939bc0...2934 = the rehearsed tree, verify_image RESULT OK with
#   --esp-size 201326592 --expect-inject-sha256 <that> --krel k15; the OTA's second initrd must fit. The pre-rehearsal
#   value beb72fd6...0e19 is superseded: the rehearsal added gb_kmsgdbg and gb_zram, 2.3)
python -I -B C:\GB-build\native\dgpu\w4\tools\esp_tree_diff.py $BKX\lapis-esp.img $U\esp-preview.img > $U\tree-diff.txt
#   expected diff only: vmlinuz, gb/inject.gz, initrd-b.img, gb/slot_b.stamp, loader/random-seed (dropped);
#   TREE: gbinit, the VM .ko set (k15) + zsmalloc.ko/zram.ko (new), gb_slotsync, gb_zram.sh (new, rehearsal),
#   gb.rc (gb_kmsgdbg service + its 2 lines first at early-init (rehearsal), insmod zsmalloc/zram, the gb_zram
#   service + post-fs-data start (rehearsal), the Gatekeeper service stanza + post-fs-data mkdir; the Trusty
#   gatekeeper empty-VINTF bind STILL PRESENT),
#   etc/fstab.android-desktop (+fsverity), the Gatekeeper files (+ vup/bin/hw/android.hardware.gatekeeper-service.gb,
#   + etc/vintf/manifest/android.hardware.gatekeeper-service.gb.xml, both sha256 as in <GK_DIR>; + the two new
#   directories etc/vintf and etc/vintf/manifest)
#   (the pipeline side of this diff is tests\w4\test_w4.py DIFF: pre-W4 build_esp vs W4 on the same inputs)

# (2) write it: the one invocation that writes lapis.bin; VM off; guard needs GB_WRITE_LAPIS=1 and no lapis*.lck
& "C:\Program Files\VMware\VMware Workstation\vmrun.exe" -T ws list          # no lapis-boot.vmx
$env:GB_WRITE_LAPIS = "1"
cmd /c "python -I -B C:\GB-x86\build_esp.py > $U\build_esp.out 2>&1"; "exit $LASTEXITCODE"
Remove-Item Env:GB_WRITE_LAPIS, Env:GB_NO_BUILDER
Get-Content $U\build_esp.out -Tail 6      # "boot slot: b; entries: ['loader/entries/gb-b.conf']",
                                          # "wrote 192 MiB ESP into lapis.bin at offset 142606336"
```

`GB_SLOT` is not set: build_esp reads the slot from lapis.bin (b). Do **not** use `boot_live.ps1` or `cycle.ps1`:
they rebuild and start the VM in one step.

### 2.5 Host checks right after the write (before booting)

```powershell
# the written region is byte-identical to the verified preview (the FAT writer has no timestamps/random fields;
# systemd-boot adds loader/random-seed only at boot)
python -I -c "import hashlib,sys; f=open(r'C:\GB-x86\lapis.bin','rb'); f.seek(142606336); a=hashlib.sha256(f.read(201326592)).hexdigest(); b=hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest(); print('ESP == preview' if a==b else 'DIFFERENT '+a)" $U\esp-preview.img
# only EFI-SYSTEM differs from the backup; GPT and every other partition untouched (read-only; 136 s measured on 26/10)
python -I -B C:\GB-build\native\dgpu\w4\tools\gb_vmbackup.py compare $BK     # exactly: "differs: EFI-SYSTEM ..."
```

If the region differs from the preview, or anything besides EFI-SYSTEM differs, **do not boot**. Roll back with 3.2
(ESP-only) and investigate.

### 2.6 First boot

The user starts the VM as usual (VMware Workstation, or `vmrun -T ws start C:\GB-x86\lapis-boot.vmx`). Start
`gb_hostbatt.exe` if it is not running. Expect about 3-6 min to `sys.boot_completed`, with no /data format: /data
persists, and dexopt already ran.

`serial0.log` (written live by `serial0`) should show:

* `Linux version <K15_REL>`;
* no `Enabling ext4 verity` (the feature is already set) and no `Failed to run /system/bin/tune2fs`;
* `init: starting service 'gb_slotsync'`;
* no `zram setup failed`; `gb_zram: zram0 present (early-init insmod)` (or, if lapis's own `modprobe zram` won the
  early-init race, `gb_zram: zram0 missing at post-fs-data ...` then `gb_zram: zram0 present now`: also a pass);
* (review; measured by the rehearsal, 2.3) `gb_gatekeeper:` lines (stderr through `stdio_to_kmsg`, which reaches
  kmsg only since the rehearsal fix `gb_kmsgdbg`; the console log level lets them through: they were in the
  rehearsal's serial0.log): `gb_gatekeeper 0.2.0 starting`, `registered android.hardware.gatekeeper.IGatekeeper/default`,
  then `registered android.hardware.security.sharedsecret.ISharedSecret/gatekeeper` and `shared HMAC key computed with
  2 participants`, no `E ` line, no `crypto self-test FAILED`, no `addService(...) failed`; boot reaches
  `sys.boot_completed` in the usual time (a hang here with the Gatekeeper lines missing is the boot hang of
  GATEKEEPER.md 4: roll back with 3.2, no PIN exists yet).
* (rehearsal) **Do not connect `gbsh.py` (or any client of the console pipe) before `sys.boot_completed`.** On the
  rehearsal VM a client connected while the guest initialised its UARTs (VMware logged `serial1: Failed to enforce the
  noRxLoss option`) left the console dead for that whole boot; after a boot without an early client it worked every
  time. Wait with adb (`getprop sys.boot_completed`) or for `gb_adb: adbd on tcp:5555` in serial0.log.
* lapis runs in headless system user mode: after boot the login screen ("User" / "Use as guest" / "Add user") belongs
  to user 0; the user's own profile is user 10 (`am get-current-user` after signing in). Every `cmd lock_settings`
  call in 2.7 takes `--user <that id>`.

### 2.7 Verification after boot

adb: `$A = "C:\GB-x86\host\scrcpy-win64-v5.0.1\adb.exe"; & $A connect 192.168.115.133:5555`, then
`& $A -s 192.168.115.133:5555 shell <cmd>`. Root-only checks run in the VM's console (`gbsh.py`, the user's tool).
Save every output to `$U\verify\`.

| # | Check | Command (adb shell unless noted) | Pass |
|---|---|---|---|
| V-1 | kernel | `uname -r` | == `<K15_REL>` |
| V-2 | boot | `getprop sys.boot_completed`; `dumpsys activity activities \| grep -E 'topResumedActivity'` | `1`; the launcher is resumed (as wave-2 AC-506) |
| V-3 | modules | `cat /proc/modules` | our 7 VM modules + `zram` + `zsmalloc` present; no native-only/dGPU module (the wave-2 AC-504 rule; 37 modules then, with lapis's own); `dmesg \| grep -ci 'module verification failed\|Loading of unsigned'` = 0 |
| V-4 | zram active | `cat /proc/swaps`; `cat /sys/block/zram0/disksize /sys/block/zram0/comp_algorithm` | `/dev/block/zram0  partition  ~17 GiB` (150 % of MemTotal); zram refcount >= 1 in `/proc/modules` while swap is on (INFERRED) |
| V-5 | mmd_setup no longer aborting | `logcat -b crash -d \| grep -c mmd_setup`; `getprop mmd.enabled_aconfig`; `getprop init.svc.mmd` | 0; the property values recorded; mmd `running` if `mmd.enabled_aconfig=true` (INFERRED from AOSP mmd.rc) |
| V-6 | fs-verity | `cat /sys/fs/ext4/features/verity`; `grep userdata /vendor/etc/fstab.android-desktop`; `dmesg \| grep -E 'ext4 verity\|tune2fs'` | `supported`; the line ends `,reservedsize=128M,fsverity`; no failure line. Host check after shutdown (2.8): superblock `RO_COMPAT_VERITY` SET |
| V-7 | Gatekeeper HAL | `service list \| grep -i -E 'gatekeeper\|sharedsecret'`; `dmesg \| grep -i 'gatekeeper' \| tail`; `getprop \| grep -i 'init.svc.*gatekeeper'`; root console: `ls -ldZ /data/vendor/gatekeeper`; `/vendor/bin/hw/android.hardware.gatekeeper-service.gb --selftest`; `logcat -d -s keystore2 \| grep -i negotiation` | `IGatekeeper/default` listed once, `ISharedSecret/gatekeeper` and `/default` listed; only `init.svc.gb_gatekeeper` running (the Trusty one never starts: its .rc is whited out); no crash loop; the directory `drwx------ hsm hsm ... gatekeeper_vendor_data_file`, empty; selftest PASS on R4; negotiation "concluded successfully" (GATEKEEPER.md 9.2 steps 1-3) |
| V-8 | PIN works and survives a reboot | **before the user puts any new data in the VM** (so 3.3 would lose nothing), signed in as the user's profile (`U=$(am get-current-user)`, 10 on lapis; never 0, the headless system user): `cmd lock_settings set-pin --user $U 1357`; `cmd lock_settings verify --user $U --old 1357`; reboot (`reboot`), pick the user on the login screen and enter 1357; `cmd lock_settings verify --user $U --old 1357`; `cmd lock_settings clear --user $U --old 1357` | set/verify succeed before and after the reboot (`Pin set to '1357'`, `Lock credential verified successfully`, `Lock credential cleared`: measured on the rehearsal, 2.3); the lock screen accepts 1357 and rejects 1358 (one counted failure, cleared by the next unlock); dmesg `gb_gatekeeper: verify uid=1000<U> -> ok` and `verify uid=<U> -> ok`. Then take 2.9. The **user sets their own PIN in Settings > Security only after 2.9 and P5's real-PIN condition** |
| V-9 | Keystore after unlock | `logcat -d \| grep -iE 'KEY_USER_NOT_AUTHENTICATED\|HardwareAuthToken\|LockSettingsService.*(fail\|error)\|Failed to unwrap'`; `dumpsys user \| grep -i RUNNING_UNLOCKED` | user 0 `RUNNING_UNLOCKED`; **no** auth-bound-key error after the V-8 unlocks (the synthetic-password key is itself auth-bound: an error here is a FAIL to investigate before any real PIN, review VM-3) |
| V-10 | Play Store update | before/after `dumpsys package com.android.vending \| grep versionName`; in Play: Manage apps > Update all (needs the user's account; else N/A); `logcat -d \| grep -c INSTALL_FAILED` | updates install; 0 `INSTALL_FAILED_BAD_SIGNATURE` |
| V-11 | OTA slot state | `getprop ro.boot.slot_suffix`; Settings > System > Software update shows no error; host (2.8): slot attrs + GPT hashes | `_b`; GPT primary/backup sha256 == section 0; vbmeta_b (15,0,1), vbmeta_a (0,0,0) |
| V-12 | gb_slotsync | `ps -A -o NAME \| grep -c gb_slotsync`; `dmesg \| grep gb_slotsync`; `ls /dev/gb_esp.lock*` | 1; started, no error; **no** `gb_slotsync: HOLD slot` (target == current); no lock left. Host (2.8): V18 ok, `gb/slot_b.stamp` unchanged from the preview |
| V-13 | VNNI counter (k15) | `cat /sys/kernel/gb_vnni/summary` | readable (contract 2.2), `irqon == emulated` |
| V-14 | battery | `dumpsys battery` vs Windows' battery flyout; unplug AC for 1 min | level matches and AC follows (`gb_hostbatt.exe` running: `Get-Process gb_hostbatt` on the host) |
| V-15 | audio | `dmesg \| grep gb_vmaudio`; play a notification sound | the gb_vmaudio lines as before; sound through VMware |
| V-16 | scrcpy | host: `C:\GB-x86\host\Googlebook-scrcpy.cmd` | window, input, clipboard, sound in scrcpy |
| V-17 | Terminal (Linux VM app) | `pm list packages \| grep -i terminal`; `ls -l /dev/kvm`; open Terminal, run `uname -a` | the app starts its VM as before (vhv + `kvm.enable_virt_at_load=0` unchanged) |
| V-18 | no new crashes | `logcat -b crash -d` vs the backup's `serial0.log` baseline | only pre-existing entries (e.g. `futility` CANNOT LINK); no gb_*/system_server crash |
| V-19 | display/input/net | screen 2560x1600, absolute mouse, `ping -c1 8.8.8.8`, `gb_netwatch:` quiet | as before |

### 2.8 Host checks after a clean shutdown (VM > Power > Shut Down Guest)

```powershell
python -I -B C:\GB-build\native\dgpu\w4\tools\gb_vmbackup.py compare $BK    # EFI-SYSTEM (random-seed), userdata, metadata, misc may differ; GPT must NOT
python -I -B C:\GB-build\native\dgpu\w4\tools\sb_probe.py                    # "RO_COMPAT_VERITY(0x8000) = SET"
python -I -c "import importlib.util as u; s=u.spec_from_file_location('x',r'C:\GB-x86\extract_slot.py'); x=u.module_from_spec(s); s.loader.exec_module(x); print(x.slot_attrs(r'C:\GB-x86\lapis.bin'))"
#   {'a': (0, 0, 0), 'b': (15, 0, 1)}
```

Done = every V-row passes **and 2.9 is taken**. Keep `<BK>`/`<BKX>` until the user has used the upgraded VM for some
days, and delete them only on the user's word.

### 2.9 Post-upgrade ESP snapshot `<BKP>` (added by the review, VM-2)

Right after V-8 and 2.8 (PIN cleared, VM shut down cleanly), **before the user sets a real PIN**:

```powershell
$BKP = "D:\GB-vm-backup\$(Get-Date -Format yyyy-MM-dd)-post-upgrade-esp"
python -I -B $T backup $BKP --no-disk          # raw upgraded ESP + GPT + small files + manifest (live hash, about 3 min)
python -I -B $T verify $BKP                    # "verify OK"
```

`<BKP>\lapis-esp.img` is the upgraded tree **with gb_gatekeeper**. It is the target of every ESP-only rollback once the
user has a PIN (3.0, 3.1) and the fix-forward of GATEKEEPER.md 8.3/8.4: K_dev and the handles live on /data, so any ESP
with the same gb_gatekeeper scheme verifies the user's PIN. D: cost 0.22 GB. Retake it (new folder) after every
later ESP change that the user accepts. Optional, when the user has accepted the upgrade and D: allows it (1.2): a
sparse full backup of the upgraded VM, so that a later full restore does not go back to 26/10 00:18.

---

## 3. Rollback

### 3.0 Which one

Rewritten by the review (VM-2, GK-3). A full restore (3.3) goes back to 26/10 00:18:46 and loses everything done in
the VM since; once the user has used the upgraded VM, it is a data loss and comes last.

| Situation | Use |
|---|---|
| 2.5 fails (bad write), or the first boot fails, **no PIN set yet**, no OTA since `<BKX>` | 3.2 ESP-only to `<BKX>` (keeps /data) |
| a later boot-level regression **with a PIN set**, no OTA since the snapshot | 3.2 ESP-only to `<BKP>` (2.9; keeps /data and the PIN). Only if the pre-upgrade tree is really wanted: clear the PIN first (3.1), then `<BKX>` |
| locked out with a PIN (errors, countdowns, "Could not find Gatekeeper device") | GATEKEEPER.md 8.1-8.4 (fix-forward to `<BKP>`, repair, clear counters); not 3.3 |
| forgotten PIN | copy the data out (GATEKEEPER.md 8.5 step 1), then `wipe_data.py` or 3.3 |
| /data damaged, an OTA happened since the ESP image you would restore, or 3.2 did not help | copy out what can be copied, then 3.3 full restore |

### 3.1 Before an ESP-only rollback to the pre-upgrade tree (`<BKX>`): clear the PIN

The old tree undeclares Gatekeeper. A PIN set under the new tree then **cannot be verified anymore**: every attempt
is an error, and LockSettingsService counts each one (GATEKEEPER.md 5). So, while the new tree still runs: Settings >
Security > Screen lock > None, or `adb shell cmd lock_settings clear --old <PIN>`. If the new tree no longer boots
and a PIN was set, do not use `<BKX>`: roll back to `<BKP>` (the upgraded tree that last worked, 2.9), or fix forward
with a corrected ESP. Should it happen anyway (old tree booted with a PIN): no wipe needed, write `<BKP>` back, boot,
then GATEKEEPER.md 8.2 (INFERRED; tested in the rehearsal 2.3).

### 3.2 ESP-only rollback (fast, keeps /data)

```powershell
& "C:\Program Files\VMware\VMware Workstation\vmrun.exe" -T ws list         # VM off
python -I -B C:\GB-build\native\dgpu\w4\tools\gb_vmbackup.py verify $BKX
python -I -B C:\GB-build\native\dgpu\w4\tools\gb_vmbackup.py restore $BKX --esp-only --yes
#   -> "restore --esp-only OK: 201,326,592 B at 142606336, sha256 2f0afad4da0b7188bfac314a40bdb39409f2dec629d121aaac0263153926f510"
```

Effects:

* The old k13 kernel and tree come back. The k13 kernel has `CONFIG_FS_VERITY=y`, so the verity /data mounts.
* `mmd_setup` aborts again as before. That is harmless, and it was always the case.
* gb_slotsync goes back to its old binary.
* If an OTA happened under the new tree, the GPT now points to the other slot and the restored ESP has only `gb-b`, so
  use 3.3 instead (check `slot_attrs` first; see 2.8).
* To the upgraded tree instead: `verify $BKP`, then `restore $BKP --esp-only --yes` (same checks; the expected sha256
  is the one in `<BKP>\manifest.json`).
* (review) After a rollback to `<BKX>`, mmd's writeback file (up to 8 GiB, 2.2a) stays on /data unused (INFERRED);
  it is reused after a re-upgrade, or can be deleted as root.

### 3.3 Full restore (exact state of 26/10 00:18:46)

Everything done in the VM after 26/10 00:18:46 is lost. First copy out what the user wants to keep: while /data has
no file-based encryption this works even when locked out (`adb pull /sdcard/...` with the pre-authorised host key, or
as root on the COM2 console; INFERRED), and `compare $BK` shows which partitions differ.

```powershell
& "C:\Program Files\VMware\VMware Workstation\vmrun.exe" -T ws list         # VM off; also close the VM tab in Workstation
python -I -B C:\GB-build\native\dgpu\w4\tools\gb_vmbackup.py verify $BK
python -I -B C:\GB-build\native\dgpu\w4\tools\gb_vmbackup.py restore $BK --yes
#   rewrites only differing 4 MiB blocks of C:\GB-x86\lapis.bin in place (no extra C: space), re-hashes it:
#   "restore OK: ... (sha256 640c85398dabd434a63b31813065240526902868f04bbb0b7c0d521f02ebcc55)"
#   and restores lapis-boot.vmx, .vmxf, .nvram, lapis.vmdk (current ones kept in D:\GB-vm-backup\pre-restore-<time>\)
```

Without the tool (`<BK>\RESTORE.txt` method; it writes all 49 GB):

```powershell
Copy-Item -Force D:\GB-vm-backup\2026-10-26-pre-upgrade\* C:\GB-x86\ -Exclude SHA256SUMS,RESTORE.txt
attrib -R C:\GB-x86\lapis*                                                   # in case a read-only flag came along
(Get-FileHash C:\GB-x86\lapis.bin -Algorithm SHA256).Hash                   # 640C85398DABD434A63B31813065240526902868F04BBB0B7C0D521F02EBCC55
```

### 3.4 Verification after a rollback

* `compare $BK` says `identical` (full) or only non-EFI regions differ (ESP-only).
* The ESP sha256 is `2f0afad4...26f510` (rollback to `<BKX>`), or the `esp.sha256` of `<BKP>\manifest.json`
  (rollback to `<BKP>`).
* Boot; `uname -r` = `6.18.21-android17-5-g3938c8d97142-dirty-ab16302624` (`<BKX>`) or `<K15_REL>` (`<BKP>`);
  `sys.boot_completed` = 1; the launcher resumed; the user's apps and files are present; with `<BKP>` and a PIN set,
  the PIN unlocks.
* `getprop ro.boot.slot_suffix` = `_b`.
* Battery, sound and scrcpy work as before.
* If the pipeline should also go back, that is wave 3's own `archive\pre-w3` / `archive\module-backups\pre-k15`. It is
  not needed for the VM: the VM's ESP is restored as a raw image.

---

## 4. Risks and what limits them

| Risk | Limit |
|---|---|
| a write to the wrong place in lapis.bin | build_esp writes only `ESP_OFFSET`..+192 MiB; 2.5 proves that only EFI-SYSTEM changed before the first boot |
| the k15 kernel does not boot in VMware | P3 (wave-3 VM-2 boots k15 in VMware) + rehearsal 2.3 + 3.2 |
| a lock-screen lockout | rehearsal 2.3 runs the lock-out and recovery tests (GATEKEEPER.md 9.5); V-8 tests set/verify/reboot/verify/clear before the user sets a real PIN; 2.9 keeps an ESP with gb_gatekeeper for rollbacks; 3.1 before a rollback to `<BKX>`; recovery without a wipe in GATEKEEPER.md 8; 3.3 always works but loses everything since 26/10 00:18 |
| throttling escalates after reboots or while the HAL errors (LockSettingsService counts every failure, GATEKEEPER.md 5) | W4-GK-1 before the user's real PIN (P5); GATEKEEPER.md 8.2 clears both counters as root |
| the PIN is only a convenience lock | user decision: a per-device key on /data (not a compile-time key) and persistent throttling, but root, the COM2 console and the host can read the plain (unencrypted) /data |
| the backup no longer matches the disk at write time (the user used the VM after 1.4) | 2.4 step (0) / P9 re-pairs right before the write |
| fs-verity cannot be cleared | already set on this /data (no change); 3.3 restores the old superblock anyway |
| zram writeback uses 8 GiB of /data | optional `mmd.zram.writeback.enabled=false` (2.2a) |
| OTA after the upgrade | gb_slotsync HOLD rule (wave 3 item 13) keeps the default on the booted slot when the new slot's GKI KMI differs; the 192 MiB ESP keeps about 43 MiB spare after a second initrd (preview numbers) |
| backup on the same SSD as the VM | an optional external copy of `<BK>` |
| a stale `.lck` blocks the guard | 2.1 (moved, not deleted; guard unchanged) |

---

## 5. Timeline and ETAs (INFERRED from the measured durations; local time UTC+2, 2026-10-26)

| Step | Duration basis | ETA |
|---|---|---|
| k15 build | measured: done 04:56 (`kbuild15 end rc=0`); review at 05:42: `k15_release.txt` written 04:58, `/build/dist15` present | done |
| k15 acceptance K-01..K-14, R-05 stage, R-06 ESP-only + determinism, R-07 USB | about 30-45 min (INFERRED) | about 05:30-05:45 |
| R-09 export, R-10 ISO | about 20-30 min (INFERRED) | about 06:00-06:15 |
| R-11 FULL ISO suite | wave-2 runs: 20:29-20:49 (6 tests) + 20:52-21:16 (G1-G4b): about 45-60 min | about 07:00-07:15 |
| W4 step 1.4 (re-validate `<BK>` + extras) | 3 + 4.5 min measured | can start after R-11: about 07:15-07:25 |
| R-12, R-12b, R-13 VM tests, R-14, R-15 | about 1-1.5 h (VM-1 alone is about 18 min of boot) | about 08:30-09:00 |
| W4 pipeline delta 2.2 + tests (needs `<GK_DIR>` accepted) | implementer time, about 1-2 h | about 10:00-11:00 |
| rehearsal 2.3 | build about 10 min + boot/format about 15 min + checks about 20 min | about 11:00-11:45 |
| user VM: 2.1, 2.4, 2.5 | about 10 min | |
| first boot + 2.7 + 2.8 + 2.9 | about 50 min with the user (V-8, V-10, V-16, V-17 are interactive; 2.9 about 4 min) | **about 12:00-13:00** |
| (review) rehearsal additions of 2.3 (GATEKEEPER.md 9.2 + 9.5 incl. recovery tests) | about 1-1.5 h more, several reboots and two ESP switches | moves the rehearsal end to about 12:45 and the user's VM to about 13:00-14:00 (INFERRED) |
| (review) W4-GK-1 rebuild + tests | about 1 h implementer time | needed only before the user's real PIN, not before the upgrade |

Any wave-3 failure that forces a kernel rebuild (R-13 KAT) moves every row after it by at least one k15 build (44 min)
plus the USB/ISO chain.

---

## Appendix A -- evidence (all read-only toward the user's VM)

Scratchpad: `<planning session scratchpad>\w4vm\`

| File | What |
|---|---|
| `lapis_scan.py` / `lapis_scan.json` | sha256 + per-partition zero census of lapis.bin |
| `esp_probe.py` | GPT table and ESP file list of lapis.bin |
| `lapis_esp_pre.img` | the current VM ESP (192 MiB, sha256 `2f0afad4...`) |
| `guard_probe.py` | build_esp's `_lapis_write_refused()` run alone (the REDO lock refusal) |
| `sb_probe.py` | userdata ext4 superblock (verity SET) |
| `ikconfig.py` | VM kernel config from vmlinuz |
| `vm_esp_preview.py` / `vm_esp_preview_now.img` | today's (k14) pipeline VM ESP preview; verify_image: only V1 |
| `esp_tree_diff.py` | ESP + cpio tree diff |
| `verify_existing.py` | re-hash of `<BK>` (all ok, 256 s) |
| (`gb_vmbackup.py compare <BK>`) | live lapis.bin vs `<BK>\lapis.bin`: identical (136 s) |
| `gb_vmbackup.py`, `test_gb_vmbackup.py`, `test_out.txt` | the tool and its 35-check host test |
| `sparse_probe.py` | NTFS sparse allocation probe (ratio 1.001 after fsync) |

The builder was used read-only, apart from one `tune2fs` set/clear test on a 64 MiB image in a `mktemp -d` folder under
`/tmp`, which was removed afterwards. `/build/dist*`, `/var/tmp/gbiso` and the k15 work dirs were not touched.
AOSP sources read on android.googlesource.com (main):

* `platform/system/memory/mmd/+/main/mmd.rc`
* `platform/system/memory/mmd/+/main/src/zram/setup.rs`
* `platform/system/core/+/main/fs_mgr/fs_mgr.cpp` (`tune_verity`)
* `platform/system/core/+/main/fs_mgr/fs_mgr_format.cpp`

The tools `esp_tree_diff.py`, `sb_probe.py` and `vm_esp_preview.py` that 2.4/2.8 call go into
`C:\GB-build\native\dgpu\w4\tools\` together with `gb_vmbackup.py`. Their tested sources are in Appendix C (the
scratchpad copies are session-temporary).

## Appendix B -- `gb_vmbackup.py` (sha256 `458514b857886f3a9888908ae95686e0178a457829a4c200bb87080a43932ae0`, LF line ends)

Extract it with its sha check (Git Bash):

```bash
python -I - "C:/GB-build/native/dgpu/w4/VM_UPGRADE.md" "C:/GB-build/native/dgpu/w4/tools/gb_vmbackup.py" <<'EOF'
import hashlib, os, sys
t = open(sys.argv[1], encoding="utf-8").read().replace("\r\n", "\n")
s = t.split("<!-- BEGIN gb_vmbackup.py -->\n", 1)[1].split("<!-- END gb_vmbackup.py -->", 1)[0]
code = s[s.index("\n") + 1:s.rindex("`" * 3)]
h = hashlib.sha256(code.encode()).hexdigest()
assert h == "458514b857886f3a9888908ae95686e0178a457829a4c200bb87080a43932ae0", h
os.makedirs(os.path.dirname(sys.argv[2]), exist_ok=True)
open(sys.argv[2], "w", encoding="utf-8", newline="\n").write(code)
print("extracted", sys.argv[2], h)
EOF
```

<!-- BEGIN gb_vmbackup.py -->
```python
"""gb_vmbackup.py -- back up, verify, compare and restore the user's Googlebook VMware VM (W4 VM_UPGRADE.md).

  python -I -B gb_vmbackup.py backup  DEST [--src DIR] [--vmx NAME] [--need-gb N] [--foreground] [--no-disk]
  python -I -B gb_vmbackup.py verify  DEST [--foreground]
  python -I -B gb_vmbackup.py compare DEST [--src DIR] [--foreground]
  python -I -B gb_vmbackup.py restore DEST --yes [--esp-only] [--src DIR] [--vmx NAME]

DIR defaults to C:\\GB-x86, NAME to lapis-boot.vmx. DEST is a new or empty folder (D:\\GB-vm-backup\\<date>).
backup   refuses while VMware runs the VM (vmrun list, VMware lock folders); refuses unless DEST's drive has N GB
         (default 26) + 3 GB floor + 1 GB margin free, and aborts (deleting the partial copy) if that drive falls
         below 4 GB during the copy. Copies the small VM files, the raw EFI-SYSTEM partition and both GPT regions,
         then lapis.bin as an NTFS sparse file (only non-zero 64 KiB chunks are written); sha256 of the source is
         taken while copying and the copy is re-read and compared; lapis.bin must not change during the copy.
         Writes SHA256SUMS + manifest.json and makes every backup file read-only. Runs in Windows background
         processing mode (very low I/O priority) unless --foreground. --no-disk: everything except the lapis.bin
         copy (the live lapis.bin is still hashed into manifest.json, to pair these extras with an existing full copy).
verify   re-hashes every file against SHA256SUMS ("<sha> *<name>" or "<sha>  <name>" lines). verify and compare also
         run in background processing mode unless --foreground.
compare  lists which GPT partitions of the live lapis.bin differ from DEST\\lapis.bin (read-only).
restore  VM must be off. Verifies the backup first. --esp-only (needs DEST\\manifest.json + lapis-esp.img) writes only
         the saved EFI-SYSTEM image back (checks the partition still has the saved offset/size). Full (needs
         DEST\\SHA256SUMS + lapis.bin, so it also works on a plain copy): rewrites only the 4 MiB blocks of lapis.bin
         that differ from the backup (in place: no extra space on C:), re-hashes lapis.bin against SHA256SUMS, restores the small
         files (the current ones are kept in DEST\\..\\pre-restore-<time>\\ first) and makes them writable.
Reads lapis.bin "rb" except restore. Standard library only; Windows only (sparse files, background mode).
"""
import ctypes, hashlib, json, msvcrt, os, shutil, stat, struct, subprocess, sys, time, zlib
from ctypes import wintypes

VMRUN = r"C:\Program Files\VMware\VMware Workstation\vmrun.exe"
DISK = "lapis.bin"
SMALL = ["lapis-boot.vmx", "lapis-boot.vmxf", "lapis-boot.nvram", "lapis-boot.vmsd", "lapis.vmdk", "lapis-data.vmdk",
         "lapis.bin.gptbak", "serial0.log"]
REQUIRED = {"lapis-boot.vmx", "lapis-boot.nvram", "lapis.vmdk"}
RESTORE_SMALL = ["lapis-boot.vmx", "lapis-boot.vmxf", "lapis-boot.nvram", "lapis.vmdk"]
CH, BLK = 64 << 10, 4 << 20            # NTFS sparse granularity (16 x 4 KiB clusters); read block
GB = 10 ** 9
FLOOR, MARGIN = 3 * GB, 1 * GB
K32 = ctypes.WinDLL("kernel32", use_last_error=True)
K32.GetCurrentProcess.restype = wintypes.HANDLE
K32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
K32.GetCompressedFileSizeW.restype = wintypes.DWORD
K32.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
K32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]


def die(msg):
    sys.exit(f"gb_vmbackup: {msg}")


def opt(args, name, default=None):
    if name not in args:
        return default
    i = args.index(name)
    if i + 1 >= len(args):
        die(f"{name} needs a value")
    return args[i + 1]


def background():
    K32.SetPriorityClass(K32.GetCurrentProcess(), 0x00100000)        # PROCESS_MODE_BACKGROUND_BEGIN


def set_sparse(f):
    br = wintypes.DWORD()
    if not K32.DeviceIoControl(msvcrt.get_osfhandle(f.fileno()), 0x000900C4, None, 0, None, 0, ctypes.byref(br), None):
        raise OSError(ctypes.get_last_error(), "FSCTL_SET_SPARSE failed")


def allocated(path):
    hi = wintypes.DWORD()
    lo = K32.GetCompressedFileSizeW(path, ctypes.byref(hi))
    if lo == 0xFFFFFFFF and ctypes.get_last_error():
        return None
    return (hi.value << 32) + lo


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb", buffering=0) as f:
        for b in iter(lambda: f.read(BLK), b""):
            h.update(b)
    return h.hexdigest()


def gpt(path):
    """{name: (start_byte, end_byte, attrs)} from the primary GPT (header and entry CRCs checked)."""
    with open(path, "rb") as f:
        f.seek(512)
        h = f.read(512)
        if h[:8] != b"EFI PART":
            die(f"{path}: no GPT header at LBA 1")
        hsz, crc = struct.unpack_from("<II", h, 12)
        hh = bytearray(h[:hsz]); hh[16:20] = bytes(4)
        if zlib.crc32(hh) != crc:
            die(f"{path}: GPT header CRC mismatch")
        ent_lba, n, esz, ecrc = struct.unpack_from("<QIII", h, 72)
        f.seek(ent_lba * 512)
        ents = f.read(n * esz)
        if zlib.crc32(ents) != ecrc:
            die(f"{path}: GPT entry array CRC mismatch")
    parts = {}
    for i in range(n):
        e = ents[i * esz:(i + 1) * esz]
        if e[:16] == bytes(16):
            continue
        first, last, attrs = struct.unpack_from("<QQQ", e, 32)
        parts[e[56:128].decode("utf-16-le").split("\0")[0]] = (first * 512, (last + 1) * 512, attrs)
    return parts


def slot_attrs(parts):
    """vbmeta_<x> GPT attributes as (priority, tries, successful), the android-desktop boot_control encoding."""
    out = {}
    for x in "ab":
        a = parts.get("vbmeta_" + x, (0, 0, 0))[2]
        out[x] = ((a >> 48) & 15, (a >> 52) & 15, (a >> 56) & 1)
    return out


def vm_guard(src, vmx):
    """(problems, stale REDO lock folders). Problems = VMware runs the VM or holds its locks."""
    probs = []
    try:
        out = subprocess.run([VMRUN, "-T", "ws", "list"], capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        probs.append(f"vmrun list failed ({e}); cannot prove the VM is off")
    else:
        if os.path.join(src, vmx).lower() in out.lower():
            probs.append(f"VMware lists {os.path.join(src, vmx)} as running")
    for n in (vmx + ".lck", "lapis.vmdk.lck", DISK + ".lck"):
        if os.path.exists(os.path.join(src, n)):
            probs.append(f"VMware lock {n} present")
    stale = sorted(n for n in os.listdir(src) if n.lower().endswith(".lck") and ".redo_" in n.lower())
    return probs, stale


def sparse_copy(src, dst, guard_dir):
    """Copy src to a new NTFS sparse dst writing only non-zero 64 KiB chunks. Returns (sha256 of src, bytes written)."""
    h, size, written, last = hashlib.sha256(), os.path.getsize(src), 0, 0
    zero, zblk = bytes(CH), bytes(BLK)
    with open(src, "rb", buffering=0) as fi, open(dst, "wb") as fo:
        set_sparse(fo)
        off = 0
        for b in iter(lambda: fi.read(BLK), b""):
            h.update(b)
            if b != zblk[:len(b)]:
                mv, i = memoryview(b), 0
                while i < len(b):
                    if mv[i:i + CH] == zero[:len(mv[i:i + CH])]:
                        i += CH
                        continue
                    j = i + CH
                    while j < len(b) and mv[j:j + CH] != zero[:len(mv[j:j + CH])]:
                        j += CH
                    j = min(j, len(b))
                    fo.seek(off + i); fo.write(mv[i:j]); written += j - i
                    i = j
            off += len(b)
            if written - last >= GB:
                last = written
                if shutil.disk_usage(guard_dir).free < FLOOR + MARGIN:
                    raise RuntimeError("destination free space fell below the 3 GB floor + 1 GB margin")
        if off != size:
            raise RuntimeError(f"read {off} bytes, expected {size}")
        fo.truncate(size)
        fo.flush(); os.fsync(fo.fileno())
    return h.hexdigest(), written


def read_sums(dest):
    sums = {}
    for ln in open(os.path.join(dest, "SHA256SUMS"), encoding="ascii"):
        if ln.strip():
            s, n = ln.split(None, 1)
            sums[n.strip().lstrip("*")] = s
    return sums


def cmd_backup(dest, src, vmx, need_gb, fg, no_disk=False):
    if not fg:
        background()
    probs, stale = vm_guard(src, vmx)
    if probs:
        die("refused: " + "; ".join(probs))
    lapis = os.path.join(src, DISK)
    if os.path.exists(dest) and os.listdir(dest):
        die(f"{dest} exists and is not empty")
    os.makedirs(dest, exist_ok=True)
    free0 = shutil.disk_usage(dest).free
    if free0 < need_gb * GB + FLOOR + MARGIN:
        os.rmdir(dest)
        die(f"refused: {dest} drive has {free0 / GB:.2f} GB free, need {need_gb} GB + 3 GB floor + 1 GB margin")
    t0 = time.time()
    st0 = os.stat(lapis)
    parts = gpt(lapis)
    man = {"tool": "gb_vmbackup.py", "started_local": time.strftime("%Y-%m-%d %H:%M:%S"), "src": src, "vmx": vmx,
           "stale_redo_locks": stale, "dest_free_before": free0, "files": {}}
    sums = {}
    for n in SMALL:
        p = os.path.join(src, n)
        if not os.path.exists(p):
            if n in REQUIRED:
                die(f"missing {p}")
            continue
        shutil.copy2(p, os.path.join(dest, n))
        sums[n] = sha_file(os.path.join(dest, n))
        if sums[n] != sha_file(p):
            die(f"{n}: copy differs from the source")
        man["files"][n] = {"bytes": os.path.getsize(p), "mtime": os.stat(p).st_mtime}
    es, ee, _ = parts["EFI-SYSTEM"]
    with open(lapis, "rb") as f:
        f.seek(es); esp = f.read(ee - es)
        f.seek(0); g1 = f.read(34 * 512)
        f.seek(st0.st_size - 33 * 512); g2 = f.read(33 * 512)
    for n, d in (("lapis-esp.img", esp), ("gpt-primary.bin", g1), ("gpt-backup.bin", g2)):
        with open(os.path.join(dest, n), "wb") as f:
            f.write(d)
        sums[n] = hashlib.sha256(d).hexdigest()
    part = os.path.join(dest, DISK + ".part")
    if no_disk:
        s_src, written = sha_file(lapis), 0
    else:
        try:
            s_src, written = sparse_copy(lapis, part, dest)
        except BaseException as e:
            if os.path.exists(part):
                os.remove(part)
            die(f"copy aborted, partial copy deleted: {e}")
    st1 = os.stat(lapis)
    if (st0.st_size, st0.st_mtime_ns) != (st1.st_size, st1.st_mtime_ns):
        if os.path.exists(part):
            os.remove(part)
        die("lapis.bin changed during the backup (is the VM running?); partial copy deleted")
    if not no_disk:
        s_dst = sha_file(part)
        if s_dst != s_src:
            die(f"verification failed: source {s_src} != copy {s_dst} (copy kept as {part})")
        os.replace(part, os.path.join(dest, DISK))
        sums[DISK] = s_src
    man.update({"lapis_bin": {"bytes": st0.st_size, "mtime": st0.st_mtime, "sha256": s_src, "copied": not no_disk,
                              "nonzero_written": written,
                              "allocated_on_dest": None if no_disk else allocated(os.path.join(dest, DISK))},
                "esp": {"offset": es, "bytes": ee - es, "sha256": sums["lapis-esp.img"]},
                "gpt_sha256": {"primary": sums["gpt-primary.bin"], "backup": sums["gpt-backup.bin"]},
                "partitions": {k: [v[0], v[1] - v[0]] for k, v in parts.items()}, "slot_attrs": slot_attrs(parts),
                "seconds": round(time.time() - t0, 1), "finished_local": time.strftime("%Y-%m-%d %H:%M:%S"),
                "dest_free_after": shutil.disk_usage(dest).free})
    with open(os.path.join(dest, "manifest.json"), "w", encoding="ascii") as f:
        json.dump(man, f, indent=1)
    sums["manifest.json"] = sha_file(os.path.join(dest, "manifest.json"))
    with open(os.path.join(dest, "SHA256SUMS"), "w", encoding="ascii", newline="\n") as f:
        f.write("".join(f"{s} *{n}\n" for n, s in sorted(sums.items())))
    for n in os.listdir(dest):
        os.chmod(os.path.join(dest, n), stat.S_IREAD)
    print(f"backup OK: {dest}\n  lapis.bin {st0.st_size:,} B sha256 {s_src}\n  written {written:,} B, allocated "
          f"{man['lapis_bin']['allocated_on_dest'] or 0:,} B; ESP {ee - es:,} B at {es}; slots {man['slot_attrs']}\n"
          f"  {man['seconds']} s; dest free {man['dest_free_after'] / GB:.2f} GB"
          + (f"\n  NOTE stale VMware REDO lock folders in {src}: {stale}" if stale else ""))


def cmd_verify(dest):
    bad = 0
    for n, s in sorted(read_sums(dest).items()):
        got = sha_file(os.path.join(dest, n))
        print(f"  {'ok ' if got == s else 'BAD'} {n}")
        bad += got != s
    if os.path.exists(os.path.join(dest, DISK)):
        a = allocated(os.path.join(dest, DISK))
        print(f"lapis.bin allocated on disk: {a or 0:,} B of {os.path.getsize(os.path.join(dest, DISK)):,} B")
    if bad:
        die(f"verify: {bad} file(s) differ")
    print("verify OK")


def cmd_compare(dest, src):
    live, bak = os.path.join(src, DISK), os.path.join(dest, DISK)
    if os.path.getsize(live) != os.path.getsize(bak):
        die("lapis.bin size differs from the backup")
    parts = sorted((s, e, n) for n, (s, e, _) in gpt(live).items())
    diff, off = {}, 0
    with open(live, "rb", buffering=0) as a, open(bak, "rb", buffering=0) as b:
        for x in iter(lambda: a.read(BLK), b""):
            y = b.read(len(x))
            if x != y:
                for i in range(0, len(x), CH):
                    if x[i:i + CH] != y[i:i + CH]:
                        o = off + i
                        n = next((n for s, e, n in parts if s <= o < e), "(GPT / outside partitions)")
                        diff[n] = diff.get(n, 0) + min(CH, len(x) - i)
            off += len(x)
    for n, v in sorted(diff.items()):
        print(f"  differs: {n:28s} {v / 2 ** 20:10.2f} MiB")
    print("compare: identical" if not diff else f"compare: {len(diff)} region(s) differ")
    return diff


def cmd_restore(dest, src, vmx, esp_only):
    probs, _ = vm_guard(src, vmx)
    if probs:
        die("refused: " + "; ".join(probs))
    sums, lapis = read_sums(dest), os.path.join(src, DISK)
    if esp_only:
        man = json.load(open(os.path.join(dest, "manifest.json"), encoding="ascii"))
        img = os.path.join(dest, "lapis-esp.img")
        if sha_file(img) != sums["lapis-esp.img"]:
            die("lapis-esp.img does not match SHA256SUMS")
        es, ee, _ = gpt(lapis)["EFI-SYSTEM"]
        if (es, ee - es) != (man["esp"]["offset"], man["esp"]["bytes"]):
            die(f"EFI-SYSTEM is now {es}+{ee - es}, the backup has {man['esp']['offset']}+{man['esp']['bytes']}")
        data = open(img, "rb").read()
        with open(lapis, "r+b") as f:
            f.seek(es); f.write(data); f.flush(); os.fsync(f.fileno())
        with open(lapis, "rb") as f:
            f.seek(es); back = hashlib.sha256(f.read(ee - es)).hexdigest()
        if back != sums["lapis-esp.img"]:
            die("ESP read-back does not match the backup")
        print(f"restore --esp-only OK: {ee - es:,} B at {es}, sha256 {back}")
        return
    if sha_file(os.path.join(dest, DISK)) != sums[DISK]:
        die("the backup lapis.bin does not match SHA256SUMS")
    if os.path.getsize(lapis) != os.path.getsize(os.path.join(dest, DISK)):
        die("lapis.bin size differs from the backup")
    keep = os.path.join(os.path.dirname(os.path.abspath(dest)), "pre-restore-" + time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(keep)
    for n in RESTORE_SMALL:
        if os.path.exists(os.path.join(src, n)):
            shutil.copy2(os.path.join(src, n), os.path.join(keep, n))
    changed, off = 0, 0
    with open(lapis, "r+b", buffering=0) as a, open(os.path.join(dest, DISK), "rb", buffering=0) as b:
        for y in iter(lambda: b.read(BLK), b""):
            a.seek(off)
            if a.read(len(y)) != y:
                a.seek(off); a.write(y); changed += len(y)
            off += len(y)
        os.fsync(a.fileno())
    got = sha_file(lapis)
    if got != sums[DISK]:
        die(f"lapis.bin after restore {got} != backup {sums[DISK]}")
    for n in RESTORE_SMALL:
        if n in sums:
            p = os.path.join(src, n)
            if os.path.exists(p):
                os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
            shutil.copy2(os.path.join(dest, n), p)
            os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
            if sha_file(p) != sums[n]:
                die(f"{n}: restored copy does not match the backup")
    print(f"restore OK: rewrote {changed:,} B of lapis.bin (sha256 {got}); small files restored; previous ones kept in "
          f"{keep}")


def main():
    a = sys.argv[1:]
    if len(a) < 2 or a[0] not in ("backup", "verify", "compare", "restore"):
        sys.exit(__doc__)
    cmd, dest = a[0], a[1]
    src, vmx = opt(a, "--src", r"C:\GB-x86"), opt(a, "--vmx", "lapis-boot.vmx")
    if cmd == "backup":
        nd = "--no-disk" in a
        cmd_backup(dest, src, vmx, float(opt(a, "--need-gb", "0.5" if nd else "26")), "--foreground" in a, nd)
    elif cmd == "verify":
        if "--foreground" not in a:
            background()
        cmd_verify(dest)
    elif cmd == "compare":
        if "--foreground" not in a:
            background()
        cmd_compare(dest, src)
    else:
        if "--yes" not in a:
            die("restore overwrites the VM image: pass --yes")
        cmd_restore(dest, src, vmx, "--esp-only" in a)


if __name__ == "__main__":
    main()
```
<!-- END gb_vmbackup.py -->

## Appendix C -- small helpers (save each into `C:\GB-build\native\dgpu\w4\tools\`)

### `vm_esp_preview.py`

```python
# Build the VM ESP that `GB_WRITE_LAPIS=1 build_esp.py` would write, into a file (lapis.bin is only read: build_esp's
# write is under its __main__ guard). argv[1] = output ESP image. Run with GB_NO_BUILDER=1.
# (W4 integration, 2026-10-26: the same min_free as build_esp's __main__, and the tree record for 2.4's V30 check)
import importlib.util, sys
spec = importlib.util.spec_from_file_location("build_esp", r"C:\GB-x86\build_esp.py")
be = importlib.util.module_from_spec(spec); spec.loader.exec_module(be)
files = be.slot_files(be.CMDLINE_VM, be.INJECT_CPIO, "Googlebook lapis x86")
img = be.make_esp(files, min_free=be.vm_min_free(files))
open(sys.argv[1], "wb").write(img)
ini = max(len(v) for k, v in files.items() if k.startswith("initrd-"))
print(f"preview ESP {len(img):,} B; slot {be.SLOT}; krel {be.kernel_release()}; largest initrd {ini:,} B")
print("VM tree:", be.vm_tree_record().decode().replace("\n", "; "))
```

### `esp_tree_diff.py`

ESP file and gb-cpio tree diff of two ESP images (read-only; uses verify_image.py's parsers).

```python
# Diff the gb cpio (gb/inject.gz) and ESP files of two ESP images. argv: OLD NEW
import hashlib, importlib.util, sys
spec = importlib.util.spec_from_file_location("vi", r"C:\GB-x86\verify_image.py")
vi = importlib.util.module_from_spec(spec); spec.loader.exec_module(vi)
def load(p):
    f = vi.fat_files(open(p, "rb").read())
    t = {n: (m, d) for n, m, d in vi.cpio_entries(vi.gunzip_padded(f["gb/inject.gz"]))}
    return f, t
fo, to = load(sys.argv[1]); fn, tn = load(sys.argv[2])
h = lambda d: hashlib.sha256(d).hexdigest()[:12] if isinstance(d, bytes) else str(d)
for k in sorted(set(fo) | set(fn)):
    a, b = fo.get(k), fn.get(k)
    if a != b: print(f"ESP  {'+' if a is None else '-' if b is None else '~'} {k}  {len(a) if a else '':>10} -> {len(b) if b else ''}")
for k in sorted(set(to) | set(tn)):
    a, b = to.get(k), tn.get(k)
    if a != b:
        print(f"TREE {'+' if a is None else '-' if b is None else '~'} {k}  " + (f"{len(a[1]) if a and a[1] else ''} -> {len(b[1]) if b and b[1] else ''}"))
```

### `sb_probe.py`

userdata ext4 superblock of lapis.bin: size, free, feature bits incl. `RO_COMPAT_VERITY` (read-only).

```python
# Read-only: the ext4 superblock of lapis.bin's userdata (feature bits incl. RO_COMPAT_VERITY 0x8000, free space).
import struct, time, importlib.util, sys
spec = importlib.util.spec_from_file_location("vi", r"C:\GB-x86\verify_image.py")
vi = importlib.util.module_from_spec(spec); spec.loader.exec_module(vi)
L = sys.argv[1] if len(sys.argv) > 1 else r"C:\GB-x86\lapis.bin"
with open(L, "rb") as f:
    p = vi.gpt_read(f, 1)["parts"]["userdata"]
    f.seek(p["first"] * 512 + 1024); sb = f.read(1024)
u32 = lambda o: struct.unpack_from("<I", sb, o)[0]
assert struct.unpack_from("<H", sb, 0x38)[0] == 0xEF53, "no ext4 magic"
bs = 1024 << u32(0x18)
inc = u32(0x60); roc = u32(0x64)
blocks = u32(0x04) | ((u32(0x150) << 32) if inc & 0x80 else 0)
free = u32(0x0C) | ((u32(0x158) << 32) if inc & 0x80 else 0)
print(f"userdata ext4: block {bs}, {blocks*bs/2**30:.2f} GiB, free {free*bs/2**30:.2f} GiB (as of last write)")
print(f"compat 0x{u32(0x5C):x} incompat 0x{inc:x} ro_compat 0x{roc:x}; RO_COMPAT_VERITY(0x8000) = {'SET' if roc & 0x8000 else 'clear'}")
print(f"state 0x{struct.unpack_from('<H', sb, 0x3A)[0]:x} (1=clean), mount count {struct.unpack_from('<H', sb, 0x34)[0]}, "
      f"last mount {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(u32(0x2C)))}, last write {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(u32(0x30)))}")
```
