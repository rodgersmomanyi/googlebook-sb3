# dGPU Wave 2 integration plan v2 (final, ordered, executable)

Status: plan only, 2026-10-25. Nothing in this file has been executed. Every number below was measured read-only on
2026-10-25 (local files, the current USB image, read-only ssh to the builder) and is quoted with its source.

This file supersedes `1b\WAVE2_PLAN.md` (v1, kept for reference). It reconciles:

* `1b\WAVE2_PLAN.md` v1 (written against the v1 component code: its component line numbers are stale; its pipeline
  line numbers still hold, because `build_esp.py`, `build_usb.py`, `stage_kernel.py` and `verify_image.py` have not
  changed since it was written: mtimes 2026-10-25 00:21, 2026-10-24 20:49, 2026-10-25 00:27 and 00:49, all before
  WAVE2_PLAN.md at 11:20; the line numbers cited here were re-read on 2026-10-25);
* `integration\README.md` (W2-00 .. W2-16), `integration\gfx-switching.md` (W2G-01 .. W2G-12 and its r2 delta), and
  the r2 sections of `nouveau-policy.md` (8), `gb-power.md` (8), `nvk-gfxdriver.md` (8), `ux-app.md` (9), plus
  `gpu-compute.md` and `k14-kernel.md`;
* `INTERFACES.md` v1.2 r2 (sections 1-29), `1b\TASKS_1B.md` v1.2, `1b\REVIEW.md`;
* `NVIDIA_PLAN.md` section 8 (safety invariants) and `GRAPHICS_SWITCHING_REQ.md` (REQ), both in `<SCRATCH>\nvresearch\`;
* the final ISO pipeline as it is now: `C:\GB-x86\iso\` (DESIGN.md v2.2, README-ISO.md, `build\build_iso.py`,
  `test\run_all.py`; current ISO `D:\GB-iso\googlebook-sb3-installer.iso`, 38/38 tests PASS).

Precedence: the user's decisions (D1-D8, restated in 0.2) > REQ (intent) > INTERFACES v1.2 r2 (mechanism) > the r2
integration sections > the v1.2 sections > the v1.1 checklist (`gfx-switching.md` wins over `README.md` where they
disagree) > WAVE2_PLAN v1. For files under `C:\GB-x86\iso\`, DESIGN.md is normative; this plan adds its section 13
(B-09).

Conventions:

* `A-nn` = Set A edit, `B-nn` = Set B edit, `R-nn` = build/test step, `AC-nnn` = acceptance check (section 10),
  `VM-1/2/3` = the throwaway VMware checks (section 7), `V<n>` = a `verify_image.py` check (B-03).
* `<SCRATCH>` = the session scratchpad directory named in the task (its `nvresearch\` holds NVIDIA_PLAN.md and REQ).
  This plan never spells out user-profile paths.
* INFERRED = reasoned from source, not observed.
* Python always runs as `python -I -B`. Commands are PowerShell unless marked **(Git Bash)** or **(builder)**.
  Builder commands run through `ssh -i C:/GB-build/builder_key -o StrictHostKeyChecking=no builder@192.168.115.132`,
  heavy ones under `nice -n 19 ionice -c3`. Inside table cells `\|` stands for a literal `|` (Markdown escaping):
  type `|`.
* "ISO job running" is tested with the self-excluding pattern `pgrep -fa '[/]var/tmp/gbiso/[0-9]'` (a plain
  `pgrep -f gbiso` matches its own ssh command line: on 2026-10-25 it reported 1 job that was itself).

---

## 0. One-page summary

### 0.1 What gets done, in what order

1. **R-00 pre-flight** (read-only, 10 min): artifact shas, builder, disk, RAM, current outputs. All green today
   (section 1).
2. **R-02 two parallel editors** on disjoint file sets, each with its own tests (sections 4-5):
   * **Set A (core):** `stage_kernel.py` + the k14 staging run + the module-backup move, `build_esp.py` (manifest
     loader, lazy erofs payload, `gfx_build_id`, `/odm/app` packing and asserts, odm build.prop, nvtest entries,
     gb_bootlog incl. gblog rotation, ESP free-space guard, duplicate-path rule, a test-only VM gfx tree),
     `build_usb.py` (`.part` + verify + sidecar, `--sparse`, `--esp-only`, `--vm-test-gfx`), and every
     `inject\native\components\*.json`.
   * **Set B (aux):** `gb_slotsync.c` (realloc/strtok fix, ESP lock, `-nvtest` copy, NDK rebuild, unit test), the
     audio scripts (`gb_audiodiag.sh` lock + flush, `gb_audio.sh` HDA `driver_override` for 0x8086 only),
     `verify_image.py` (ESP/cpio/erofs/signature readers and all new checks), the ISO side (MODULE_DENY, static test,
     `build_iso.py` verify preflight + export cross-check 8, `check_esp_tree.sh`, DESIGN section 13), and
     `build_internal.py`.
   * The two sets meet only through the contracts of section 3.
3. **R-04/R-05 ESP-only checkpoints** (payload built once on the builder, then cached; determinism proven).
4. **R-06 USB image** to a new name, sparse, verified before rename. **R-07** tree diff and size budgets.
5. **R-08..R-10 throwaway VMware checks** (builder suspended for RAM): VM-2 k14 boot of a `--vm-test` image, VM-3 the
   Android runtime check of GbGpu + the gfx driver package on a test-only `--vm-test-gfx` image, VM-1 native smoke of
   a sparse copy of the new USB image (gblog evidence).
6. **R-11** builder resumed, USB swap (old image to `C:\GB-x86\out\prev\`), the two USB-sha constants updated.
7. **R-12/R-13 ISO** with a fresh export (`--new-export`, DESIGN 1.5) to `D:\GB-iso\new\`, verified. **R-14** ISO swap
   (old ISO to `D:\GB-iso\prev\`).
8. **R-15 ISO test subset** (12 ids, about 34 min; section 8).
9. **R-16** every acceptance check, **R-17** one user question (section 13), then delete the `prev` copies.
   **R-18** hardware hand-off.

### 0.2 User decisions this plan implements (not re-opened)

* Default graphics mode **Auto**: detached -> Intel; attached -> NVIDIA for games and user-selected apps; an "all
  apps" switch; per-app "Always Intel". No build-time default is written (W2G-03: no `ro.vendor.gb.gfx_default`, no
  `persist.vendor.gb.gfx_mode` in any build.prop or rc).
* nouveau **auto-loads on attach** with every rail; `kernel.panic=30` on nouveau boots (`GB_PANIC_TIMEOUT=30` in
  `gb_nouveau.sh`, checked by V21).
* The VMware image (`C:\GB-x86\lapis.bin` + `lapis-boot.vmx`) is **never** changed: no step runs `build_esp.py` as
  `__main__` (it writes `lapis.bin`, `build_esp.py:1530-1534`), no step uses `GB_NATIVE_TEST`, no `vmrun` call names
  `lapis-boot.vmx`. `lapis.bin` is only read (as the pipeline already does).
* Graphics switching per v1.2 r2: `ro.gfx.driver.0` in the native `/odm/etc/build.prop`, `GbGfxDriver.apk` and
  `GbGpu.apk` under `/odm/app` (no priv-app), Settings written only through the gb_notify `kind=settings` queue, the
  `-nvtest` boot entry. The `-gfxtest` entry and the `gb.gfxdriver=` token are **retired** (INTERFACES 3, 16).

### 0.3 Budgets at a glance (details: section 9)

| Item | Today | After Wave 2 (projected; the ACs use the measured value) | Limit |
|---|---|---|---|
| USB ESP used / free (FAT data area 504.1 MiB) | 188.9 / 315.2 MiB | about 160 / 344 MiB | free >= max(initrd) + 144 MiB (about 199 MiB) |
| Export ESP `EFI-SYSTEM.img` | 536,870,912 B (= 512 MiB, same content class) | same as USB | 536,870,912 B exactly |
| `initrd-<x>.img` | 102,845,429 B | about 57.4 MB | |
| `gb/dgpu-<krel>.erofs` | n/a | 60,260,352 B expected (NP-21) | 80 MiB |
| ISO | 7,474,929,664 B | about 7.40-7.48 GB | 8 GiB (`build_iso.py:71`) |
| USB image | 28,814,885,376 B | unchanged size; about 8 GiB allocated with `--sparse` | |

### 0.4 Time

Engineering: Set A about 8-11 h, Set B about 8-11 h, in parallel. Machine: about 4-5 h serial (R-04 10 min, R-06
25 min, R-08..R-10 about 100 min, R-12 40 min, R-15 34 min, the rest small).

---

## 1. State on 2026-10-25 (read-only evidence)

### 1.1 Component artifacts (W2G-11 r2 pre-flight): all match

`sha256sum` (Git Bash) of the files the manifests will name, first 8 hex, against the r2 sections:

| File (`C:\GB-build\native\...`) | sha256 | Expected (source) |
|---|---|---|
| `nouveau-kernel\native\gb_nouveau.sh` | `6a754d42` | nouveau-policy 8.2 |
| `nouveau-kernel\native\gb_dgpu_report.sh` | `ccf3ddfb` | 8.2 |
| `nouveau-kernel\native\gb_notify.sh` | `a4bc8d6e` | 8.2 |
| `nouveau-kernel\native\gb_nouveau.rc` | `4a8c438a` | 8.2 (= INTERFACES 15.1-v12) |
| `nouveau-kernel\native\gb_dgpu_check.sh` | `045fe80d` | 8.2 |
| `nouveau-kernel\firmware_staged_sha256.txt` | `fa0699d7` | section 1 |
| `nouveau-kernel\firmware\WHENCE.tu117`, `LICENCE.nvidia` | `5de89a22`, `bc5225a5` | section 1 |
| `nouveau-kernel\payload\make_dgpu_payload.sh`, `erofs_ls.py` | `45de8e5d`, `90f96583` | section 1 |
| `power\bin\gb_power` (source `power\gb_power.c` `a8bf3f05`) | `0d6a0b28` | gb-power 8.2 |
| `power\gb_power.rc` | `87347a99` | 8.2 |
| `nvk-android\bin\nvk_probe` | `3d53a7e9` | nvk-gfxdriver 7.1 |
| `nvk-android\bin\vulkan.nouveau.so` | `625314e0` | 7.1 |
| `nvk-android\gb_nvk.rc` | `748963f3` | 7.1 |
| `gfx-driver-pkg\out\GbGfxDriver.apk` | `15a22bfd` | 8.2 |
| `gfx-driver-pkg\out\gfxdriver.json` | `840d0666` | 8.4 (= TASKS_1B 3.3) |
| `gfx-driver-pkg\out\apk-libs\libgb_nvk.so` | `6bb6dc80` | 7.1 |
| `gpu-ui\out\GbGpu.apk` | `d653940a` | ux-app 9.2 |
| `gpu-ui\gpu_ui.json`, `gfx_app_prefs.default` (0 bytes), `gb_gpu_ui.rc`, `gb_gpu_ui_setup.sh` | `ef867551`, `e3b0c442`, `1c8ca925`, `312cf0c5` | 8.2 |
| `gpu-compute\out\gb_gpu_compute`, `out\lib64\libgb_nvk_loader.so`, `out\lib64\libgb_gpu_compute.so`, `gb_gpu_compute.rc`, `gpu_compute.json` | `f275dae6`, `576193fa`, `8042d365`, `e1eaf3b9`, `b29ec2fa` | gpu-compute 1 |
| `C:\GB-x86\inject\odm_sepolicy.cil` | `8265b908` | INTERFACES 17 r2 (no change) |
| `C:\GB-build\kernel\k14_release.txt` | `551a3287` = `6.18.21-android17-5-g9629ced3e50e-ab16302714` | k14-kernel 2 |

APK members (Python `zipfile`, read-only): GbGfxDriver.apk = `AndroidManifest.xml` (deflated), `resources.arsc`,
`assets/sphal_libraries.txt` (0 B), `lib/x86_64/libgb_nvk.so` (STORED, sha `6bb6dc80`), `lib/x86_64/vulkan.intel.so`
(STORED, `7675b988`); no member named `allowlist.txt`. GbGpu.apk: `resources.arsc` STORED, `classes.dex` and
`AndroidManifest.xml` deflated.

### 1.2 Shared files to edit: baseline sha256 (first 16 hex)

Each editor re-checks its files against this table before the first edit; a mismatch stops that editor (someone
else changed the file).

| File | sha256 | Set |
|---|---|---|
| `C:\GB-x86\build_esp.py` (1534 lines) | `388e2ed49d665391` | A |
| `C:\GB-x86\build_usb.py` (190) | `2443f47fd7498a91` | A |
| `C:\GB-x86\stage_kernel.py` (184) | `581b96b136a2e801` | A |
| `C:\GB-x86\inject\native\components\nouveau.json` / `nvk.json` / `power.json` | `fae612faaa70e7dd` / `8af726e30ef7cac1` / `5a5da6e7b3646ba1` | A |
| `...\components\audio.json`, `camera.json`, `hw.json`, `iptsd.json`, `sensors.json` (not edited) | `a57362f3...`, `0342d6fc...`, `e5c0ad3b...`, `d43e739f...`, `8bf62657...` | (A, read only) |
| `C:\GB-x86\verify_image.py` (147) | `3bc808b241f35784` | B |
| `C:\GB-build\mouse\gb_slotsync.c` (327) | `3670f1a75eaaf46d` | B |
| `C:\GB-x86\inject\slotsync\gb_slotsync` (18,824 B) | `9904678376f7fa57` | B |
| `C:\GB-build\native\audio\tree\gb_audio.sh` (118) / `gb_audiodiag.sh` (102) | `4c53e3a035f8e83c` / `24a745c0bd89f25a` | B |
| `C:\GB-build\native\audio\rc\gb_audio.rc` (not edited) | `13015ffcd8479195` | (B, read only) |
| `C:\GB-x86\iso\build\build_iso.py` | `c1c5cf30b59b12d7` | B |
| `C:\GB-x86\iso\build\builder\stage_initramfs.py` | `389533f57339e8bf` | B |
| `C:\GB-x86\iso\test\t0_static.py` / `check_esp_tree.sh` / `common.py` | `f0ace901f72afd02` / `bef958e354228c5f` / `7736b843347b673a` | B |
| `C:\GB-x86\iso\DESIGN.md` / `README-ISO.md` | `9b40b8ef2bbdc456` / `ab03baeb10def97a` | B |
| `C:\GB-build\native\dualboot\build_internal.py` (164) | `b3f9dc3790ed24ca` | B |

### 1.3 Builder, host, outputs

| Item | Value (2026-10-25) | How |
|---|---|---|
| k14 dist | `/build/dist14` (the task's "dist-k14"; `ls -d /build/dist*` lists only `/build/dist` and `/build/dist14`; k14-kernel.md 5 item 7); `kernel_release.txt` = the k14 krel; bzImage 26,764,288 B md5 `92bf19d202132b56cce6d5c6786ec988`; `/build/kbuild14.done` present | ssh |
| k13 dist | `/build/dist` bzImage md5 `c970513058b26325fea85728eaec4fe1` = `C:\GB-x86\boot\kernel.bin` (k13 staged 00:27); `find /build/dist -newer /build/kbuild13.done` 0 files; `find /build/dist14 -newer /build/k14/accept/summary.txt` 0 files | ssh, md5sum |
| k14 opt-in modules | drm_exec 23,049 + drm_gpuvm 90,585 + gpu-sched 143,473 + drm_ttm_helper 15,369 + nouveau 7,155,457 = 7,427,933 B | `stat` |
| Builder free | `/` 28,803,956,736 B (= `/var/tmp`, the ISO's 25 GiB preflight floor applies), `/build` 12,254,416,896 B; load 0.01; RAM 7.9 GB, 3 vCPU; no ISO job | `df -B1`, `uptime` |
| Builder tools | NDK r27c (`/build/ndk`, `Pkg.Revision = 27.2.12479018`, clang 18.0.3); gcc; erofs-utils 1.7.1 (`mkfs/fsck/dump.erofs`; `fsck.erofs --extract[=X]` supported); mksh; shellcheck; ISO chunk cache `/var/tmp/gbiso/cache` 7.0 GiB; `/var/tmp/gbiso/last/` holds the current ISO; `/build/native/dgpu/payload-stage` does not exist yet | ssh |
| lapis toybox (276.R4 rootfs copy `/build/native/dgpu/nvk-sync/rootfs-276R4/system/bin/toybox`) | `netcat [-46ELlntUu] [-pqWw #]`, `-U Use a UNIX domain socket`, `-q Quit SECONDS after EOF`; `mv [-FfinTvx]`, `stat [-tfL] [-c FORMAT]`, `timeout`, `df` | `strings -a` |
| Host free | C: 39,014,432,768 B; D: 49,683,824,640 B | `Get-PSDrive` |
| Host RAM | 3,338 MB free of 32,354 MB; running VMs: `C:\GB-x86\lapis-boot.vmx`, `C:\GB-build\builder2.vmx` | CIM, `vmrun -T ws list` |
| adb client | `C:\GB-x86\host\scrcpy-win64-v5.0.1\adb.exe` (not on PATH; shared with the user's scrcpy session) | `ls` |
| USB image | `C:\GB-x86\out\googlebook-sb3-usb.img` 28,814,885,376 B, sidecar `cad193d6...0d45`, built 01:58 | `.sha256` |
| ISO | `D:\GB-iso\googlebook-sb3-installer.iso` 7,474,929,664 B (read-only attribute), sha `1718bcc1...4b71`, build `20261025-085013-37112350`; `iso\test\logs\20261025-085013-37112350\summary.txt`: 38 PASS, `OVERALL PASS`, 10:49:52Z-13:01:04Z | files |
| Export | `C:\GB-x86\iso\work\export\` created `2026-10-25T04:21:05`, slot b, `EFI-SYSTEM.img` `6c6ca051...` | manifest |
| USB native tree (read-only cpio parse of `gb/inject.gz`) | 507 entries, 191,706,300 B; **186 `*.pre-*` files (59,735,154 B) ship today**; 30 `firmware/nvidia/**` files; **4 duplicate paths** (`firmware/intel/sof/sof-icl.ri`, `firmware/intel/sof-tplg/sof-hda-generic{,-2ch,-4ch}.tplg`: in `NATIVE_FW` and again in `audio.json`, identical bytes); `gb.rc` has `service gb_nouveau ` but no `service gb_notify `, no `service gb_dgpu_report `, no `socket gb_power`; build.prop has no `ro.gfx.driver` | Python |
| USB ESP vs export ESP | every file byte-identical except `loader/entries/gb-b.conf` (799 vs 798 B: title, `gb.boot=`, `boot_part_uuid`); native trees identical (scratchpad `compare_native.py`: `RESULT: IDENTICAL native tree`) | Python |
| gb_power `gfx_build_id` parser | one printable line; trailing `\n` stripped by `read_text()` (`gb_power.c:205-219`); `gfx_build_id()` `:3325-3335`; buffer `gfx[520]` (`:1021`); the line is 346 characters | source |

---

## 2. Reconciliation: what v2 decides

### 2.1 WAVE2_PLAN v1 conflicts, final state

| v1 item | v2 decision | Source |
|---|---|---|
| C-01 nvk rc | `rc_files` = `gb_nvk.rc` (Option B) | nouveau-policy 2.2 |
| C-02 gfxdriver.json location | `gfx-driver-pkg\out\gfxdriver.json` (`840d0666`) | gfx-switching W2G-01 |
| C-03 `ro.gfx.driver.0` | from the native `/odm/etc/build.prop`, every native image; `gfxdriver.json` is a normal component; `GB_GFXDRIVER=0` stays only as a bisect opt-out (then also no `gfx_build_id`) | W2G-01, INTERFACES 4 |
| C-04 renderD 0666 | gb_power's own derivation (INTERFACES 23.5); no flag file; no pipeline impact | W2G-10 |
| C-05 gfxtest entry | **retired**: no `-gfxtest` entry, no `gb.gfxdriver=`, no `gfxtest_entry` key (the loader refuses it) | W2G-03, INTERFACES 16 |
| C-06 report timing | `report/dgpu-<N>.txt` checked at the t=420 snapshot | nouveau-policy 2.4 |
| C-07 ESP lock | INTERFACES 5.4 rename-based steal for every user. **Correction to v1 E-07:** after 600 s gb_bootlog **proceeds without the lock** (INTERFACES 2.3); v1's script stole a live holder's lock at 600 s | INTERFACES 2.3, 5.4 |
| C-08 krel | first token of the bzImage setup-header `kernel_version` (offset `u16@0x20E + 0x200`) | v1, INTERFACES 2.1 |
| C-09 signing key | verify_image compares each module's PKCS#7 signer with the certificates inside this image's `vmlinuz` | k14-kernel 5 item 10 |
| C-10 VM tests | throwaway VMs only (section 7); VM-1 evidence from gblog; boot_completed-gated parts are conditional or HW | REVIEW D4, W2G-10 |
| C-11 artifact pins | V12 (cpio bytes == manifest `src` bytes) plus the r2 shas of 1.1 | W2G-11 r2 |
| C-12 module backups | moved to `C:\GB-x86\archive\module-backups\`; `stage_kernel.py` writes future backups there; `build_esp` packs only `*.ko` + `modules.load*` | task, W2-13 |
| C-13/C-14 UI naming, defaults | decided by the user (0.2); docs deferred (section 12) | INTERFACES 20 |
| C-15 gb_audiodiag | ESP lock + `flush` + collect-then-copy (B-02); a precondition for Auto, not hygiene | W2G-10 |
| C-16 firmware licence | shipped in the payload (`WHENCE.tu117`, `LICENCE.nvidia`) | INTERFACES 6.2 |
| C-17 stale export | export cross-check 8 (B-07) + `--new-export` (DESIGN 1.5 forbids GUID reuse) | DESIGN 1.5 |
| C-18 USB sha constants | `--usb-sha256` at build time; `common.py` `USB_SHA` and `build_iso.py` `VERIFIED_USB_SHA256` updated at R-11 | DESIGN 6.2 |
| C-19 gb_slotsync bug | fixed first (B-01) | v1 |
| C-20 gblog growth | retention (newest 8 boots) + 128 MiB floor (A-09); build-time `min_free` (A-10) | v1 |
| C-21 quick reattach | gb_power deferred start / `reconcile_on()` (INTERFACES 23.7, 23.7a); no pipeline impact | gb-power 8.1 R-3 |
| C-22..C-25 | accepted / cosmetic / INTERFACES 4 already says build.prop (v1.2) | |
| C-26 k14 first | k14 staged by Set A (A-03) before any payload build (the payload is per krel) | k14-kernel 1.3 |
| C-27 freeze | holds: no native build since 01:58 (`out\` mtimes); the first native build is R-04, after A-12 landed atomically | W2-00 |
| v1 Q-1b-1..5 | answered by INTERFACES v1.2 (16, 23.5, 25.1/25.3, 24.6/12.1, 23.7) | W2G-10 |
| v1 Q-U1..U3 | decided by the user / authorised by the task (throwaway VMs, builder suspension) | task |
| v1 Q-U4 export GUIDs | `--new-export` (DESIGN 1.5 v2 has no reuse); the consequence goes to the user before cleanup (section 13) | DESIGN 1.5 |
| v1 `GB_DGPU_LAYOUT=legacy` knob | **dropped**: payload layout only; rollback = the archived pre-Wave-2 files (section 11) | simplification |
| v1 `ro.vendor.gb.gfx_default` (REVIEW D2) | not written (W2G-03, decision D1) | W2G-03 |
| v1 6.2 criterion 10 (`all_apps` 0 or null) | `null` only (gb_power never writes Settings without DTX) | REVIEW D3, W2G-10 |
| W2G-09 SELinux | dropped (old-targetSdk domains already permissive); `odm_sepolicy.cil` unchanged | r2 |
| W2G-12 / W2-12 ueventd | deferred (optional defense in depth; gb_power clamps a 0666 node within one tick) | r2 |

### 2.2 New findings that v1 did not have

1. **Duplicate cpio paths today** (1.3). A naive "no duplicate path" assert would break the build. v2 rule (A-08):
   duplicates with identical bytes and mode are dropped (first kept, one log line); differing duplicates fail the
   build. verify_image V3 then requires no duplicates in new images.
2. **V6 fails on today's image** (186 `*.pre-*` files ship). v1 listed V6 as passing; the expected-result list for
   the old image is corrected in B-03.
3. **gb_bootlog 600 s rule** corrected (2.1 C-07).
4. **C: budget.** A non-sparse 26.84 GiB image next to the kept old one leaves 9.5 GiB on C:, and the ISO's fresh
   export needs 12.6 GiB more on C:. v2 writes images sparse (`--sparse`, A-13): content and sha256 identical,
   about 8 GiB allocated (INFERRED from DESIGN 1.6: super is 37 % zero blocks; userdata is all zero).
5. **The ISO test harness uses fixed paths** (`iso\test\common.py:52-60`: the ISO, its build record and the USB image
   at their default names). So the order is: build to new names, verify, swap (old -> `prev\`), then run the ISO
   tests; the `prev\` copies stay until every check passed (R-11, R-14, R-17).
6. **`build_iso.py --out`** must be below `D:\GB-iso\` and not in `D:\GB-iso\test\` (`build_iso.py:864-868`):
   `D:\GB-iso\new\` is allowed. Its build record names that path; R-14 rewrites the two path fields after the move.
7. **VM-1 cannot observe the nouveau refusal unless `sys.boot_completed` fires.** `gb_nouveau` starts only at
   `sys.boot_completed=1` (15.1-v12), and the native tree has no DRM driver in VMware (no vmwgfx; `docs\NATIVE.md`:
   "It cannot reach a desktop"), so INFERRED: boot_completed never fires. The i915 guard's input is still recorded:
   gb_bootlog runs the read-only `gb_dgpu_check.sh` and copies `report/dgpu-<N>.txt` (its `== drm` section), which
   are not boot_completed-gated (`on property:vendor.gb.dgpu_report=1` has no such condition). VM-1's criteria are
   two-tier (7.4).
8. **toybox `nc -U` exists on lapis** (1.3), so gb_bootlog can ask gb_power `hello`/`status`/`get-mode` read-only.
9. **Checkpoint determinism needs a fixed super GUID**: every `build_usb.py` run makes a new one, which changes the
   entries. `--esp-only` takes `--super-guid G` (A-13).
10. **VM-only builds must never need the builder**: the native cpio becomes lazy (A-06) and `GB_NO_BUILDER=1` makes
    any builder call fail, which TA-4 uses to prove it.
11. **adb**: use the scrcpy-bundled `adb.exe` with `-s <ip>:5555` on every call and `adb disconnect <ip>:5555` at the
    end; never `kill-server` (the user's scrcpy session shares the server).

---

## 3. Contracts between Set A and Set B (both editors code to these; neither edits the other's files)

### 3.1 `build_esp.py` API (Set A provides; Set B's `build_internal.py` and `verify_image.py` tests consume)

| Name | Contract |
|---|---|
| `slot_files(cmdline, inject_cpio, title, default=None, timeout=3, native=False)` | `native=True`: also `loader/entries/gb-<x>-nvtest.conf` per slot and `gb/dgpu-<krel>.erofs` + `gb/dgpu-<krel>.sha256`. Asserts `gb.nouveau=` and `gb.gfxdriver=` are not in `cmdline`. |
| `make_esp(files, size=ESP_BYTES, min_free=0)` | Asserts the free bytes after `write_tree` are `>= min_free`; prints `ESP built: used=... free=...`. |
| `native_min_free(files)` | `max(len(v) for initrd-*) + 144 MiB` (16 MiB gb_slotsync margin + the 128 MiB gblog floor). |
| `native_cpio()` and module attribute `INJECT_CPIO_NATIVE` | Lazy, memoized (PEP 562 module `__getattr__`); the first call may build the payload (cache first, then builder). |
| `kernel_release(bz=KERNEL)` | Setup-header krel (C-08). |
| `vm_test_gfx_cpio()` | Test-only VM tree variant (A-11); never called by `build_esp.py` itself. |
| `GB_NO_BUILDER=1` | Every builder call raises `RuntimeError("GB_NO_BUILDER")`. |
| `GB_GFXDRIVER=0` | Skips `gfxdriver.json` (no APK, no `ro.gfx.driver.0`, no `gfx_build_id`). |

### 3.2 `verify_image.py` CLI (Set B provides; Set A's `build_usb.py` and the integrator consume)

```
verify_image.py IMG [--expect-tree auto|native|vm|vmtest-gfx] [--krel FILE] [--no-gfxdriver] [--compare-export ESP]
verify_image.py --esp FILE [--boot usb|internal] [--super-guid G] [--expect-tree ...] [--krel FILE] [--compare-export ESP]
verify_image.py --extract IMG ESP_PATH OUTDIR      (read-only on IMG; writes only into OUTDIR, which must be empty)
```

* Exit 0 and last line `RESULT: OK`, else exit 1 and `RESULT: <n> problem(s)`; one `PROBLEM: V<n> ...` line each.
  Exit 2 = usage.
* `--expect-tree auto` (default) = from the default entry's `gb.hw=` (`native` -> native checks; `vm` -> VM checks).
  `build_usb.py` passes `vmtest-gfx` for `--vm-test-gfx` images.
* `--super-guid` absent in `--esp` mode: every `androidboot.boot_part_uuid=` must merely be equal.
* Module importable by path (Set B's `build_iso.py` uses it): `esp_of_disk(path) -> bytes`,
  `fat_files(esp_bytes) -> {path: bytes}`, `cpio_entries(buf) -> [(path, mode, data)]`, `native_tree(esp_files)`.
* Read-only always (it opens images and `lapis.bin` with `"rb"`); 5 min at most on a 27 GiB image.

### 3.3 `build_usb.py` CLI (Set A)

```
build_usb.py [--out FILE] [--userdata-gib N] [--esp-mib 512] [--export DIR] [--sparse]
             [--vm-test [--vm-test-gfx]] [--esp-only FILE [--super-guid G]]
```

* Writes `<out>.part`, hashes while writing, runs `verify_image.py <out>.part --expect-tree <kind>`; on exit 0:
  `os.replace` to `<out>` and writes `<out>.sha256` = `<hex>  <basename>\n`; on failure keeps `.part`, no sidecar,
  exit 1. Prints `super GUID: <g>` and `image sha256: <hex>`.
* `--esp-only FILE`: builds only the ESP (same code path as the image), writes FILE, runs `verify_image.py --esp FILE
  --boot usb --super-guid <g>`, exits. `--super-guid` is accepted only with `--esp-only`.
* `--vm-test-gfx`: only with `--vm-test`, and `--out` must be below `D:\gb-w2test\` (else exit 2).

### 3.4 ESP lock and gblog conventions (A: gb_bootlog; B: gb_slotsync, gb_audiodiag)

* INTERFACES 5.4 exactly (lock dir `/dev/gb_esp.lock`, owner line `<pid> <name> <uptime_s>`, 1 s polls, stale =
  missing `/proc/<pid>`, non-numeric owner, or no owner file for 5 polls; steal = record inode + owner line, rename to
  `/dev/gb_esp.lock.stale.<pid>`, compare, then remove or put back; release only if the owner file still holds our
  line). Reference implementation: `gb_nouveau.sh:364-430` (r2 `6a754d42`).
* Owner names: `gb_bootlog`, `gb_slotsync`, `gb_audiodiag`. Max wait 600 s; then gb_bootlog proceeds without the lock,
  gb_slotsync skips the cycle, gb_audiodiag gives up and keeps its files in `/data/local/tmp/gb_audiodiag/`.
* ESP mounts for writing use `-o rw,flush,nosuid,nodev,noexec` and last only for the copy.
* gblog names: `boot<N>-t<T>.txt` (gb_bootlog), `audio-b<N>-<name>` (gb_audiodiag; `<N>` = `gblog/bootcount`).
  gb_bootlog prunes both to the newest 8 boots. Nobody writes into `gblog/` while the ESP has < 128 MiB
  (131,072 KiB) free.

### 3.5 Test-only VM gfx tree (A builds, B verifies)

`vm_test_gfx_cpio()` = the VM (`gbstage`) cpio plus exactly: `gbstage/app/GbGpu/GbGpu.apk`,
`gbstage/app/GbGfxDriver/GbGfxDriver.apk`, `gbstage/etc/gb/gfx_app_prefs.default`,
`gbstage/vup/bin/gb_gpu_ui_setup.sh` (0755), the `gb_gpu_ui.rc` fragment appended to `gbstage/etc/init/gb.rc` under
`# ---- component gpu_ui (VM test) ----`, and `ro.gfx.driver.0=gb.sb3.gfxdriver` appended to
`gbstage/etc/build.prop`. Nothing else of the dGPU stack (no gb_power, no gb_nouveau, no `gfx_build_id`). Title
`Googlebook OS - USB image layout (VM test, gfx components)`. verify_image `--expect-tree vmtest-gfx` requires exactly
this set (V11).

---

## 4. Set A (core) edits, in order

Before the first edit of each file: copy it to `C:\GB-x86\archive\pre-w2\GB-x86\<path>` (a backup inside
`inject\native\modules\` would be packed, one inside `iso\build\builder\` would be uploaded; hence the archive).
LF line endings for every shell text. Set A's own tests live in `C:\GB-x86\tests\w2a\` (new).

### A-01 Move the module backups out of `inject\native\modules` (W2-13, C-12)

Measured: 266 files = 78 `.ko` + `modules.load` + `modules.load.late` + 54 `*.pre-k11` + 54 `*.pre-k12` + 78
`*.pre-k13` (186 files, 59,735,154 B).

```powershell
$src = 'C:\GB-x86\inject\native\modules'
foreach ($t in 'k11','k12','k13') {
  $dst = "C:\GB-x86\archive\module-backups\pre-$t\inject\native\modules"
  New-Item -ItemType Directory -Force $dst | Out-Null
  Get-ChildItem -LiteralPath $src -Filter "*.pre-$t" | ForEach-Object {
    Move-Item -LiteralPath $_.FullName -Destination (Join-Path $dst ($_.Name -replace "\.pre-$t$", '')) -WhatIf } }
# review the 186 -WhatIf lines, then run again without -WhatIf
```

Pass (AC-101): `Get-ChildItem $src | ? Name -notmatch '^(.+\.ko|modules\.load(\.late)?)$'` is empty; 78 `.ko`;
the archive holds 186 files, 59,735,154 B. Same volume (rename): no C: space change. `C:\GB-x86\inject\modules\*.pre-*`
(33 VM backups) and `boot\kernel.bin.pre-*` stay: they are never packed (the VM tree loads modules by name,
`build_esp.py:37-48`; `KERNEL` reads `boot\kernel.bin` only).

### A-02 `C:\GB-x86\stage_kernel.py`: `--dist`, `--archive`, load-list backups, TAG guard (W2-09)

k14-kernel.md 1.1's verified diff plus the backup redirection of v1 E-03, with the archive root of A-01:

* Usage `stage_kernel.py TAG [--dist DIR] [--archive DIR] [--dry-run] [--force]`; `TAG` must match
  `k[0-9]+[a-z0-9]*`; `--dist` must be `/build/dist*` with no further `/` (k14 note's guard).
* `ARCHIVE = C:\GB-x86\archive\module-backups`; `backup(path)` copies an existing file once to
  `<ARCHIVE>\pre-<TAG>\<path relative to C:\GB-x86>` (never next to the live file); `put()` calls it.
* Back up `modules.load` and `modules.load.late` before overwriting them (`stage_kernel.py:174-177`).
* Comment at `:43-44`: the 5 `NATIVE_OPTIN` modules are staged in `inject\native\modules` as the payload's source;
  build_esp puts them into `ESP:/gb/dgpu-<krel>.erofs`, never into the cpio.

Tests (TA-2): `--dist /tmp/x` and TAG `k14!` refused; `--dry-run --dist /build/dist14` prints `bzImage md5
92bf19d202132b56cce6d5c6786ec988; 77 modules found, missing: none` and one vermagic
`6.18.21-android17-5-g9629ced3e50e-ab16302714 SMP preempt mod_unload modversions `, no `PROBLEM:` line.

### A-03 Stage k14 (W2-09) - an action, after A-01 and A-02

```powershell
ssh -i C:/GB-build/builder_key -o StrictHostKeyChecking=no builder@192.168.115.132 "test -f /build/kbuild14.done && bash /build/k14/tools/k14_accept.sh"
python -I -B C:\GB-x86\stage_kernel.py k14 --dist /build/dist14 --dry-run
python -I -B C:\GB-x86\stage_kernel.py k14 --dist /build/dist14
```

Run from PowerShell (Git Bash rewrites `/build/dist14`; there use `MSYS_NO_PATHCONV=1`). Only reads the builder.

Pass (AC-102): `k14_accept.sh` prints only PASS/info lines (K-01..K-09); `boot\kernel.bin` md5 `92bf19d2...`; 70
native `.ko` from dist14 + the 8 lapis HDA codec modules (copied unchanged from `C:\GB-build\native\audio\modules`) =
78 `.ko`; 9 VM `.ko` in `inject\modules`; first line of both load lists `# kernel build k14 (bzImage md5 92bf19d2...)`;
`archive\module-backups\pre-k14\` holds the k13 `boot\kernel.bin`, 87 `.ko` and both load lists; no `*.pre-k14`
anywhere under `C:\GB-x86\inject`. Note for the user: the next VM ESP rebuild (outside Wave 2) would boot k14 and the
new gb_slotsync; nothing in this plan does that.

### A-04 `build_esp.py`: native module filter (W2-13) - at `:910-911`

`_NATIVE_MODS_ALL = _tree(NATIVE_MOD_DIR)`; `NATIVE_MODS` = only `*.ko`, `modules.load`, `modules.load.late`; log the
count of ignored files; assert the directory is flat. After A-01 the ignored count is 0.

### A-05 `build_esp.py`: component loader (W2-01, W2G-01, W2G-03) - replaces `:1258-1272`

```python
NATIVE_COMPONENTS, _COMP_FILES, _COMP_RC, PAYLOAD_FILES, _COMP_LINKS = [], [], b"", [], []
_KNOWN = {"name", "source", "comment", "files", "payload", "symlinks", "rc_files", "rc_text", "build_prop"}
_BAD_PROP = ("ro.hardware.vulkan", "ro.hardware.egl", "ro.hwui.use_vulkan", "debug.hwui.renderer",
             "ro.vendor.gb.gfx_default", "persist.vendor.gb.gfx_mode", "persist.vendor.gb.nouveau")
for _mf in sorted(_glob.glob(os.path.join(NATIVE_DIR, "components", "*.json"))):
    _m = _json.load(open(_mf, encoding="utf-8"))
    if _m["name"] == "gfxdriver" and os.environ.get("GB_GFXDRIVER") == "0":
        print("native component gfxdriver: skipped (GB_GFXDRIVER=0)", flush=True); continue
    assert not set(_m) - _KNOWN, f"{_mf}: unknown keys {sorted(set(_m) - _KNOWN)} (gfxtest_entry is retired)"
    for _e in _m.get("files", []):                     # unchanged semantics (:1262-1264)
        ...
        assert "gb_gfxdriver" not in _e["dest"], f"{_mf}: gb_gfxdriver.* is retired (v1.2)"
    for _e in _m.get("payload", []):
        assert int(str(_e["mode"]).replace("0o", ""), 8) & 0o7777 == 0o644, f"{_mf}: payload files are 0644 (6.2)"
        assert _e["dest"].startswith(("lib64/hw/", "lib64/gpu-compute/")), _e
        PAYLOAD_FILES.append((_e["dest"], open(_e["src"], "rb").read()))
    for _s in _m.get("symlinks", []):
        assert _s["target"].startswith("/odm/dgpu/"), _s
        _COMP_LINKS.append((_s["dest"], _s["target"].encode(), 0o120777))   # newc symlink: data = target
    ...                                                 # rc_files / rc_text unchanged (:1265-1268)
    for _l in _m.get("build_prop", []):
        assert not _l.startswith(_BAD_PROP), f"{_mf}: {_l} is not allowed (invariant 1, W2G-03)"
        ODM_BUILD_PROP_NATIVE += _l.encode() + b"\n"
    NATIVE_COMPONENTS.append(_m["name"])
```

Then assert: `ODM_BUILD_PROP_NATIVE.count(b"ro.gfx.driver.0=") == (1 if "gfxdriver" in NATIVE_COMPONENTS else 0)`;
`b"debug.hwui.renderer" not in GB_RC_NATIVE + _COMP_RC`; `b"gb.gfxdriver" not in GB_RC_NATIVE + _COMP_RC`;
`b"persist.vendor.gb.nouveau=" not in _COMP_RC` (the retired v1.1 trigger; today only the old `nvk.json` `rc_text`
contains that string, in a comment, and A-12 removes it, so this assert also refuses a half-integrated manifest set;
checked 2026-10-25: no component rc file contains any of the four strings). The build log line becomes
`native components: audio, camera, gfxdriver, gpu_compute, gpu_ui, hw, iptsd, nouveau, nvk, power, sensors (... files)`.

### A-06 `build_esp.py`: lazy erofs dGPU payload (W2-08, INTERFACES 6) - new code after the native tree

Constants: `OPTIN = [m + ".ko" for m in stage_kernel.NATIVE_OPTIN]` (loaded by path, `__main__`-guarded: constants
only); `NK = C:\GB-build\native\nouveau-kernel`; tool `NK\payload\make_dgpu_payload.sh`; sha list
`NK\firmware_staged_sha256.txt`; licence texts `NK\firmware\WHENCE.tu117`, `NK\firmware\LICENCE.nvidia`; cache
`C:\GB-x86\cache\dgpu\`; ssh constants as `stage_kernel.py:14-16`.

`kernel_release()`: `assert KERNEL[0x202:0x206] == b"HdrS"`; `off = u16(KERNEL, 0x20E) + 0x200`; first token of the
NUL-terminated string. Assert it equals the first vermagic token of `inject\native\modules\nouveau.ko`.

`dgpu_payload()` (memoized):

1. **Tree** (in memory, then a deterministic tar: sorted, mtime 0, uid/gid 0, dirs 0755, files 0644):
   * `firmware/nvidia/tu117/...` = every `NATIVE_FW` entry under `nvidia/tu117/`; assert exactly the 30 paths of the
     sha list, each sha matching;
   * `firmware/nvidia/WHENCE.tu117`, `firmware/nvidia/LICENCE.nvidia` (missing = build error);
   * `lib/modules/<m>` for every `OPTIN` from `_NATIVE_MODS_ALL`; assert each vermagic's first token ==
     `kernel_release()` and each ends with `~Module signature appended~\n`;
   * `lib/modules/modules.dgpu` = `drm_exec.ko\ndrm_gpuvm.ko\ngpu-sched.ko\ndrm_ttm_helper.ko\nnouveau.ko\n`;
   * every `PAYLOAD_FILES` entry (`lib64/hw/vulkan.nouveau.so`, `lib64/gpu-compute/libgb_nvk_loader.so`,
     `lib64/gpu-compute/libgb_gpu_compute.so`);
   * assert no top-level entry other than `firmware/`, `lib/`, `lib64/`.
2. **Cache**: `key = sha256(b"gb-dgpu-cache-1\0" + krel + sha256(tool) + sha256(sha list) + sorted(path + sha256(data)))`;
   directory `C:\GB-x86\cache\dgpu\<key[:16]>\` with `dgpu-<krel>.erofs`, `dgpu-<krel>.sha256`, `inputs.json`
   (paths, sizes, sha256). A hit is used only if both files verify against each other.
3. **Miss -> builder** (fails at once with `GB_NO_BUILDER=1`):
   * refuse if builder `/` or `/build` would drop below 6 GB; warn (do not block) if
     `pgrep -fa '[/]var/tmp/gbiso/[0-9]'` is non-empty or the 1-min load is >= 2;
   * `S=/build/native/dgpu/payload-stage/<krel>-<key[:12]>`: `rm -rf $S; mkdir -p $S/tree $S/out`;
   * stream the tar into `tar -x -C $S/tree`; copy the tool and the sha list into `$S/`;
   * `cd $S && nice -n 19 ionice -c3 sh ./make_dgpu_payload.sh tree out/dgpu-<krel>.erofs <krel> firmware_staged_sha256.txt`;
     the last line must be `PAYLOAD ok size=<n> sha256=<h> files=<f>`;
   * content proof: `mkdir $S/x && nice -n 19 ionice -c3 fsck.erofs --extract=$S/x out/dgpu-<krel>.erofs && diff -r
     -x VERSION -x MANIFEST.sha256 $S/tree $S/x && (cd $S/x && sha256sum -c --quiet MANIFEST.sha256) && echo CONTENT-OK`;
   * fetch the image and `.sha256` over ssh stdout; local sha256 == `PAYLOAD ok` sha == the `.sha256` line;
   * `rm -rf $S`; store in the cache.
4. Return `{"name": "dgpu-<krel>.erofs", "image": ..., "sha_name": "dgpu-<krel>.sha256", "sha_line": "<hex>  dgpu-<krel>.erofs\n"}`.

Native tree in payload mode (replaces `:1301-1305`): skip `NATIVE_FW` entries under `nvidia/`; skip `OPTIN` from
`NATIVE_MODS`; add the empty directories `dgpu` and `firmware/nvidia`; add `_COMP_LINKS`; the real
`vup/lib64/hw/vulkan.nouveau.so` is gone because `nvk.json` no longer lists it in `files` (A-12).

`native_cpio()` (memoized): adds `etc/gb/dgpu_payload.sha256` (= `sha_line`, byte-identical to the ESP file), then
`etc/gb/gfx_build_id` (A-07), runs the A-08 asserts, returns `cpio_newc(...)`. Module `__getattr__("INJECT_CPIO_NATIVE")`
returns it, so `build_usb.py` and `build_internal.py` keep the attribute name; `build_esp.py`'s own `GB_NATIVE_TEST`
branch (`:1525`) calls `native_cpio()` (bare names bypass `__getattr__`; that branch is never run in Wave 2). A VM-only
build (`--vm-test`, `build_esp.py` `__main__`) never calls it.

Expected first build: `PAYLOAD ok size=60260352 sha256=1d7277b1...` (NP-21 built from the same inputs: k14 modules,
the 8a ICD `625314e0`, compute libraries `576193fa`/`8042d365`). A different sha is not a failure by itself; it must be
explained by an input difference in `inputs.json`.

### A-07 `build_esp.py`: `/odm/etc/gb/gfx_build_id` (W2G-02, INTERFACES 5.3, 25.1)

Only when `gfxdriver` is loaded; mode 0644; exactly one line plus `\n`:

```
apk=<sha256 of the tree's app/GbGfxDriver/GbGfxDriver.apk> icd=<sha256 of the payload's lib64/hw/vulkan.nouveau.so>
nvk=<sha256 of the APK member lib/x86_64/libgb_nvk.so (must be ZIP_STORED)> probe=<sha256 of vup/bin/nvk_probe>
pay=<first field of etc/gb/dgpu_payload.sha256>
```

(one line in the file; wrapped here). Computed from the bytes going into this tree, never copied from notes. Build
error if any input is missing. Expected for this build: `apk=15a22bfd19180b45943c68fd5fdae31129d5b94d748fb94152a3e980d20271cd
icd=625314e09c8f9adb82d57b8abfd6ea2e855308a1ea79c59f6670783e52f2409b
nvk=6bb6dc80afac7a73c3131b6c70e322bf3c322f0c0057adf2b0581152f673cfb9
probe=3d53a7e942ca67a82c1b4934299b5e60524eb7a7f1f10cc8d14b089cb06addbd pay=<payload sha>` (346 characters).

### A-08 `build_esp.py`: `/odm/app` packing, duplicate rule and tree asserts (W2-10, invariants 1, 17, 19)

In `native_cpio()` and for `INJECT_CPIO`:

* **Duplicates**: equal path + equal mode + equal bytes -> keep the first, log `native tree: <n> identical duplicate
  path(s) dropped (e.g. ...)` (today: the 4 SOF files); any other duplicate -> build error.
* No path starts with `gbnative/priv-app` or `gbstage/priv-app`.
* No path contains `.pre-`, `.merged`, `.orig`, `.v1.1`, `.v1.2r1`, `superseded/` or ends in `.bak`/`.part`.
* Every `gbnative/app/<D>/<F>.apk` has `<F> == <D>`, is a zip, and is mode 0644; GbGfxDriver.apk has no member whose
  basename is `allowlist.txt`, has `assets/sphal_libraries.txt`, and its two `lib/x86_64/*.so` members are STORED.
* Payload: none of `OPTIN` and no regular file under `gbnative/firmware/nvidia/` in the native cpio (the VM tree
  keeps its own `drm_ttm_helper.ko` for vmwgfx); every payload, placeholder, `gfx_build_id` and
  `dgpu_payload.sha256` entry is below `gbnative/`.
* `modules.load*` names no `OPTIN` module.
* The gbstage tree (`INJECT_CPIO`) has none of: `nouveau.ko`, `firmware/nvidia`, `vulkan.nouveau.so`, `gb_nouveau`,
  `nvk_probe`, `gb_gpu_compute`, `app/GbGpu`, `GbGfxDriver`, `gb_gfxdriver`, `gfx_build_id`,
  `gfx_app_prefs.default`, `gb_gfx_selftest`, `dgpu`, and its build.prop has no `ro.gfx.driver.` (VM-1B-01).

### A-09 `build_esp.py`: `GB_BOOTLOG_SH` (W2-02, W2G-07 + r2, k14 Q11, gblog rotation) - replaces `:935-967`

Replace the bytes literal with the script of **Appendix A**, as `GB_BOOTLOG_SH = rb"""..."""` followed by
`assert b"\r" not in GB_BOOTLOG_SH`. The service stays as it is (`:992-997`, started at post-fs-data `:1059-1060`).
Changes against today's script:

* collect each snapshot into `/dev/gb_bootlog.txt` first; lock (3.4) + mount `rw,flush` only for the copy;
* snapshots at t = 40, 90, 180, 420, **900** s; `setprop vendor.gb.dgpu_report 1` at t=40;
* a failed mount skips that snapshot (today it ends the script);
* retention: newest 8 boots of `boot*`/`audio-b*`; floor: nothing written below 128 MiB free;
* `timeout` around every binder client (`dumpsys`, `cmd`, `settings`);
* the `== dgpu` section: NVIDIA/root-port PCI state (cached attributes only), refcnt, DRM nodes with vendor, driver
  and node mode, the read-only `gb_dgpu_check.sh`, filtered properties, gb_power `hello`/`status`/`get-mode` over
  `toybox nc -q 1 -U`, the 5 `updatable_driver_*` keys, package flags of `gb.sb3.gpu`/`gb.sb3.gfxdriver`, the notify
  queue listing, the state files (`nouveau.clean_boots`, `gfx_unclean`, `dgpu_unclean`, `selftest/result`; presence of
  `gfx_session`, `dgpu_session`, `nouveau.loading`, `selftest/running`), the dGPU mounts, the dmesg grep (adds `_OSC`,
  `pciehp`, N1-N3 lines, `gb_slotsync`, `gb_audiodiag`, `Do not have permissions to set`), the logcat tags
  (`gb_notify gb_gpu_ui_setup gb_gpu_compute gb_gfxdriver GbGpu`), `report/{dgpu,load,bind,test,selftest}-<N>.txt`,
  the dmesg-live tails, the `gb_nouveau.log` tails.
* Test-only overrides (init never sets them): `GB_BOOTLOG_LOCK`, `GB_BOOTLOG_TMP`, `GB_BOOTLOG_MNT`. The function block
  is between the markers `#@@functions-begin` and `#@@functions-end` so the harness can source it.

### A-10 `build_esp.py`: entries, ESP guard, HWUI asserts (W2-03, W2G-03) - replaces `:1373-1392`, `:1501-1508`

```python
def slot_files(cmdline, inject_cpio, title, default=None, timeout=3, native=False):
    assert "gb.nouveau=" not in cmdline and "gb.gfxdriver=" not in cmdline, "the default entry never selects NVIDIA"
    ...                                                       # files dict unchanged (:1380-1386)
    def entry(x, suffix, opts):
        return (f"title   {title} (slot {x.upper()}){suffix}\nlinux   /vmlinuz\n"
                f"initrd  /initrd-{x}.img\noptions {opts}\n").encode()
    for x in slots:
        files[f"loader/entries/gb-{x}.conf"] = entry(x, "", cmdline)
        if native:
            files[f"loader/entries/gb-{x}-nvtest.conf"] = entry(x, " [NVIDIA test]", cmdline + " gb.nouveau=test")
        files[f"initrd-{x}.img"] = make_initrd(inject_cpio, x)
        files[f"gb/slot_{x}.stamp"] = slot_stamp(gz, x)
    if native:
        p = dgpu_payload()
        files["gb/" + p["name"]] = p["image"]
        files["gb/" + p["sha_name"]] = p["sha_line"].encode()
    return files

def native_min_free(files):
    return max(len(v) for k, v in files.items() if k.startswith("initrd-")) + 144 * MiB

def make_esp(files, size=ESP_BYTES, min_free=0):
    fat = Fat32(size, spc=1); fat.write_tree(build_tree(files)); img = fat.image()
    free = (fat.clusters - (fat.nxt - 2)) * fat.csize
    assert len(img) == size and free >= min_free, (len(img), size, free, min_free)
    print(f"ESP built: used={(fat.nxt-2)*fat.csize/2**20:.1f} MiB free={free/2**20:.1f} MiB of {size/2**20:.0f} MiB, "
          f"{fat.clusters:,} clusters", flush=True)
    return img
```

`loader.conf` keeps `default gb-<slot>` and `editor yes`. The title keeps `(slot X)` before the suffix (gb_slotsync's
rewrite keys on it, `gb_slotsync.c:244-247`). The VM path (`:1511`) and `GB_NATIVE_TEST` keep `native=False`.

### A-11 `build_esp.py`: `vm_test_gfx_cpio()` (test-only, 3.5)

Builds the 3.5 tree from the `gbstage` entries plus the `gpu_ui` and `gfxdriver` manifests (re-rooted under
`gbstage/`), memoized; asserts the result differs from `INJECT_CPIO` exactly by the 3.5 paths and the two text
appends. Never used by `build_esp.py` itself, never written to `lapis.bin`.

### A-12 `C:\GB-x86\inject\native\components\*.json` - one atomic change (W2-00, W2-01, W2-11, W2G-01, W2G-05)

| File | Content |
|---|---|
| `nouveau.json` | README W2-01 exactly: `gb_nouveau.sh`, `gb_dgpu_check.sh`, `gb_dgpu_report.sh`, `gb_notify.sh` -> `vup/bin/` 0755, `firmware_staged_sha256.txt` -> `etc/gb/tu117.sha256` 0644; `rc_files` `gb_nouveau.rc`; `rc_text` ""; `build_prop` [] |
| `nvk.json` | `files`: `bin\nvk_probe` -> `vup/bin/nvk_probe` 0755; `payload`: `bin\vulkan.nouveau.so` -> `lib64/hw/vulkan.nouveau.so` 0644; `symlinks`: `vup/lib64/hw/vulkan.nouveau.so` -> `/odm/dgpu/lib64/hw/vulkan.nouveau.so`; `rc_files` `gb_nvk.rc`; `rc_text` "" (v1 E-21 text) |
| `power.json` | unchanged (the files changed in place: `gb_power` `0d6a0b28`, rc `87347a99`) |
| `gpu_compute.json` (new) | byte copy of `C:\GB-build\native\gpu-compute\gpu_compute.json` (`b29ec2fa`) |
| `gpu_ui.json` (new) | byte copy of `C:\GB-build\native\gpu-ui\gpu_ui.json` (`ef867551`, with the `gfx_app_prefs.default` seed) |
| `gfxdriver.json` (new) | byte copy of `C:\GB-build\native\gfx-driver-pkg\out\gfxdriver.json` (`840d0666`) |
| `audio.json`, `camera.json`, `hw.json`, `iptsd.json`, `sensors.json` | unchanged |

Check: each file `json.load`s, every `src` exists, every new file's sha256 equals its source's.

### A-13 `C:\GB-x86\build_usb.py` (3.3)

* Native branch (`:108-111`): `files = be.slot_files(usb_cmdline, be.INJECT_CPIO_NATIVE, title, timeout=4,
  native=True)`; `esp = be.make_esp(files, a.esp_mib * MiB, min_free=be.native_min_free(files))`.
* `--vm-test` branch (`:105-107`): `native=False`, `min_free=0`; with `--vm-test-gfx` the tree is
  `be.vm_test_gfx_cpio()` and the 3.5 title.
* `--esp-only FILE [--super-guid G]`; `--sparse` (FSCTL_SET_SPARSE via `DeviceIoControl` before the first write, the
  same technique as `iso\test\common.py:174-193`; every all-zero 1 MiB block is skipped with `seek`; the backup GPT
  written last fixes the size); `.part` + running sha256 (skipped blocks hashed as zeros) + `verify_image.py` +
  `os.replace` + sidecar.
* Print `super GUID:` and `image sha256:`. Keep the slot/OTA guard (`:115-119`) before anything is written.

### A-14 Set A tests (`C:\GB-x86\tests\w2a\`; builder scratch `/build/native/dgpu/w2a/`, removed afterwards)

| Id | Test | Pass |
|---|---|---|
| TA-1 | `python -I -B -m py_compile` of the three scripts; every manifest loads; every `src` exists; no CR in `GB_BOOTLOG_SH` | all ok |
| TA-2 | A-02 guard and dry-run (above); after A-03 the AC-102 counts | as stated |
| TA-3 | gb_bootlog: extract `GB_BOOTLOG_SH` with `ast` (no import), `shellcheck -s sh` (0 findings; only justified `# shellcheck disable=` for fixed tokens), mksh harness on the builder in two passes (GNU coreutils; toybox 0.8.9 + BWK awk, as nouveau-policy's `run_tests.sh`), sourcing the function block with the three overrides and PATH shims (`mount`, `umount`, `sync`, `df`, `getprop`, `setprop`, `dumpsys`, `cmd`, `settings`, `logcat`, fake `sleep` clock): L1 free lock; L2 live holder: waits, then takes it after release; L3 dead pid: stolen once; L4 no owner file: stolen after 5 polls; L5 non-numeric owner: stolen; L6 live holder for 600 fake s: proceeds without the lock, the holder's lock untouched; L7 two instances against one stale lock: exactly one steal; L8 owner replaced while held: not removed at release; P1 N=13 with boots 1..12 present: files of boots <= 5 removed, 6..12 kept, `bootcount` and foreign files kept; P2 `df` 100,000 KiB free: nothing written, one kmsg line; F1 mount options contain `rw,flush`; F2 the ESP is mounted only between collection end and copy end; S1 a snapshot contains `== dgpu`, `-- gb_power`, `-- settings`, `-- state` | every case ok in both passes |
| TA-4 | `GB_NO_BUILDER=1`: `build_usb.py --vm-test --esp-only D:\gb-w2test\esp\vm.img` succeeds (VM-only never needs the builder) | exit 0 |
| TA-5 | after R-04 (cache present): inspect `native_cpio()`: payload-mode removals, the symlink (mode `0120777`, target), empty dirs, `gfx_build_id` == the A-07 expected line (with `pay=` from the payload), the duplicate log line names 4 paths; in-memory mutations each raise: a `gbnative/priv-app/x` entry, a `ro.hardware.vulkan` build_prop, a `gfxtest_entry` key, a payload entry with mode 0755, an APK with `assets/allowlist.txt`, a differing duplicate | all as stated |
| TA-6 | determinism = R-05 | identical sha256 |
| TA-7 | `GB_GFXDRIVER=0` ESP-only build: no `GbGfxDriver.apk`, no `gfx_build_id`, no `ro.gfx.driver.0`; `verify_image.py --esp ... --no-gfxdriver` OK | exit 0 |
| TA-8 | `--vm-test-gfx` without `--vm-test`, or with `--out` outside `D:\gb-w2test\`, exits 2 | exit 2 |

---

## 5. Set B (aux) edits, in order

Backups to `C:\GB-x86\archive\pre-w2\<GB-x86|GB-build>\<path>` before the first edit. Set B's test files:
`C:\GB-build\mouse\test\`, `C:\GB-build\native\audio\test\`, `C:\GB-x86\tests\w2b\`,
`C:\GB-x86\iso\build\pipeline_test\test_export_check8.py`. Builder scratch `/build/native/dgpu/w2b/` (removed after).

### B-01 `C:\GB-build\mouse\gb_slotsync.c` (W2-04, W2G-04, C-19)

1. **Bug fix** (`select_entry`, `:238-255`): today `strtok((char *)realloc(t, tl + 1), "\n")` tokenises an
   unterminated buffer, then writes `((char *)t)[tl] = 0` through the pre-realloc pointer and later `free(t)`s it. When
   realloc moves the block (always under ASan; possible under Scudo, INFERRED) that is a write-after-free plus a double
   free on the OTA slot-switch path. Replace it with `copy_entry(s, cur, sfx, required)` (v1 E-11 code): copy into a
   fresh `malloc(tl + 1)` buffer, NUL-terminate, `strtok_r`, write into an output buffer sized `2 * tl + 512` with
   `snprintf` bounds checks, free each buffer exactly once, check every `malloc`/`realloc` (also `read_all`).
2. `select_entry` = `copy_entry(s, cur, "", 1)` (failure returns -1), then `copy_entry(s, cur, "-nvtest", 0)` (a
   missing template is not an error; a failure is logged and never blocks the switch). **No `-gfxtest`.** The
   `loader.conf` part is unchanged.
3. **ESP lock** around `mount` .. `umount2` in `sync_once` (`:307-314`), INTERFACES 5.4 in C (3.4): `mkdir(LOCK,
   0700)`; owner file created with `open(O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0600)` + `dprintf` (not
   `write_file`, whose `.tmp` rename could leave a file that blocks `rmdir`); stale = `/proc/<pid>` missing,
   non-numeric owner, or no owner file for 5 polls; steal = `stat` (inode) + owner line, remove a leftover
   `LOCK.stale.<pid>`, `rename(LOCK, LOCK.stale.<pid>)`, `stat` + read again, same inode and line -> unlink owner,
   `rmdir`, retry; otherwise `rename` back (log if that fails). 600 s -> log and return -1 (`settled` stays unset, the
   next 60 s tick retries). Release only if the owner file holds our line. `--dry-run` takes the lock too (it mounts).
4. Test build only (`-DGB_SLOTSYNC_TEST`): `MNT`, the lock path, the `/proc` root, `ro.boot.slot_suffix` and the boot
   id come from environment variables, `mount`/`umount2` are no-ops, `sleep` is a fake clock, and two test entry
   points exist (`--test-copy-entry S CUR`, `--test-lock`). None of it is compiled into the NDK binary.
5. **NDK build (builder)**: `/build/ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang --target=x86_64-linux-android34
   -O2 -Wall -Wextra -Werror -o gb_slotsync gb_slotsync.c` under `nice -n 19 ionice -c3`; update the build comment at
   `:16`. Old binary -> `C:\GB-x86\archive\pre-w2\GB-x86\inject\slotsync\gb_slotsync`; new one ->
   `C:\GB-x86\inject\slotsync\gb_slotsync`. It is packed into both trees (`build_esp.py:1217-1218`, `:1296`).

Tests (TB-1, `C:\GB-build\mouse\test\test_gb_slotsync.sh`, run on the builder; host `gcc -DGB_SLOTSYNC_TEST -O1 -g
-fsanitize=address,undefined -fno-sanitize-recover=all -Wall -Wextra -Werror`):

| Case | Pass |
|---|---|
| S1 copy `gb-a.conf` + `gb-a-nvtest.conf` to b | `gb-b.conf` and `gb-b-nvtest.conf` byte-equal to the templates except `(slot B)` and `initrd  /initrd-b.img`; no sanitizer report |
| S2 no nvtest template | only `gb-b.conf`, rc 0 |
| S3 template without a final newline, CRLF lines, a 4 KiB line | correct output, no sanitizer report |
| S4 mutation: the old source (`3670f1a7`) with ASan on S1 | ASan reports `heap-use-after-free` or `attempting double-free` (proves S1 catches the bug) |
| S5 lock: free / live holder (a `sleep` child) -> wait, then 600 fake s -> skip, holder untouched / dead pid -> stolen once / non-numeric -> stolen / ownerless 5 polls -> stolen / two concurrent instances against a stale lock -> exactly one steal / owner replaced while held -> not removed | each as stated |
| S6 `--dry-run` end-to-end on a fake ESP dir, no OTA pending | no write, lock taken and released, `settled` set |
| S7 NDK binary | builds with `-Werror`; `readelf -d` NEEDED only bionic libraries; `strings` has no `GB_SLOTSYNC_TEST`/`--test-` |

### B-02 `C:\GB-build\native\audio\` (W2-05, C-15)

1. `tree\gb_audiodiag.sh`: collect into `/data/local/tmp/gb_audiodiag/t<T>/` (`t<T>.txt`, `t<T>-dmic.wav`,
   `t<T>-hsmic.wav`); at the end take the ESP lock (3.4, owner `gb_audiodiag`, 600 s, then give up); mount
   `rw,flush,nosuid,nodev,noexec`; `B` = `gblog/bootcount` (non-numeric -> 0); skip the copy below the 128 MiB floor;
   copy each file as `gblog/audio-b<B>-<name>`; `sync`; umount; unlock; remove the temp dir only after a successful
   copy (keep at most 3 old temp dirs). Test-only overrides `GB_ADIAG_LOCK`, `GB_ADIAG_MNT`, `GB_ADIAG_TMP`.
   Unchanged: the 60 s delay of `auto`, the sections, `gb_audio.rc`.
2. `tree\gb_audio.sh`, legacy `dsp_driver=1` path only, before `$MP -d $VM snd_hda_intel` (`:65-66`): for every PCI
   function with vendor `0x8086` and class `0x0403*`, write `snd_hda_intel` to its `driver_override`; never write any
   attribute of a `0x10de` function (gb_power keeps every non-0x03 NVIDIA function driverless, INTERFACES 2.2/10.1;
   INFERRED: `snd_hda_intel` may still probe 02:00.1 at registration in legacy mode, and gb_power unbinds it within a
   tick; legacy mode is not the default, which is SOF `dsp_driver=3`). `SYS=${GB_ROOT:-}/sys` for the test.

Tests (TB-2, `C:\GB-build\native\audio\test\run_tests.sh`, builder, mksh GNU + toybox passes): `shellcheck -s sh` both
scripts 0 findings; A1 lock held by a live pid -> waits, copies after release; A2 600 fake s -> no copy, files kept,
kmsg line; A3 mount options contain `rw,flush`; A4 ESP mounted only during the copy; A5 floor -> no copy; A6 fake
sysfs (`0000:00:1f.3` 0x8086 0x040380, `0000:02:00.1` 0x10de 0x040300), `dsp_driver=1` -> only 00:1f.3's
`driver_override` = `snd_hda_intel`, 02:00.1 untouched; `dsp_driver=3` -> no `driver_override` write.

### B-03 `C:\GB-x86\verify_image.py` (W2-07, W2G-06, ux-app 8.6, gpu-compute 2.3, gb-power 8.5, nvk-gfxdriver 7.4)

Readers to add (read-only, stdlib only): FAT32 + LFN (the WAVE2_PLAN Appendix A lister, extended to return bytes),
gzip + newc cpio (incl. symlink entries), the erofs directory walk (logic of `NK\payload\erofs_ls.py`, names/modes/
sizes only), a pure-Python LZ4 legacy-frame decoder for the bzImage payload (magic `02 21 4c 18` at
`(setup_sects + 1) * 512 + payload_offset`), a minimal DER walk (PKCS#7 SignerInfo issuer + serial; X.509 certificates
in the decompressed kernel), `zipfile` for the APKs. The checks are pure functions over (`esp_files`, `cpio_entries`,
`payload_listing`) so they can be unit-tested with in-memory mutations.

Existing checks stay (GPT, slot attributes, copied partitions vs `lapis.bin`, blank partitions); the native needles
(`:105-107`) apply only to native trees. New checks (native image unless stated):

| V | Check |
|---|---|
| V1 | ESP parses as FAT32; size == the partition size (512 MiB USB, 536,870,912 B export); free >= `max(initrd-*.img)` + 144 MiB |
| V2 | `loader.conf`: `default gb-<slot>`, `editor yes`; entries exactly `gb-<slot>.conf` and `gb-<slot>-nvtest.conf` (no `-gfxtest`); nvtest == base + title suffix ` [NVIDIA test]` + options suffix ` gb.nouveau=test`; no `gb.nouveau=` in the base entry; no `gb.gfxdriver=` anywhere; **every** `androidboot.boot_part_uuid=` == the super GUID (today only the first is checked, `:97`) |
| V3 | `gb/inject.gz` gunzips, == the first segment of `initrd-<slot>.img`; the cpio parses; no duplicate path |
| V4 | krel from the setup header; every `.ko` (cpio + payload listing sizes; vermagic from the cpio copies and from `inject\native\modules` for the payload ones) has vermagic first token == krel and the signature trailer; every cpio module's signer serial is in the vmlinuz certificate list; with `--krel FILE`: krel == the file's line |
| V5 | none of `drm_exec`, `drm_gpuvm`, `gpu-sched`/`gpu_sched`, `drm_ttm_helper`, `nouveau` in any `modules.load*`; none of the 5 `.ko` in the cpio |
| V6 | no cpio path with `.pre-`, `.merged`, `.orig`, `.v1.1`, `.v1.2r1`, `superseded/`, or ending in `.bak`/`.part` |
| V7 | `gb/dgpu-<krel>.erofs` + `.sha256` (exactly one each, krel == V4); sha256 matches; `gbnative/etc/gb/dgpu_payload.sha256` byte-identical; erofs magic `0xE0F5E1E2` at 1024; listing == INTERFACES 6.2 (VERSION, MANIFEST.sha256, LICENCE/WHENCE, the 30 firmware files, 5 modules + `modules.dgpu`, `lib64/hw/vulkan.nouveau.so`, the 2 compute libraries), dirs 0755, files 0644, no symlinks; payload file sizes == the manifest `src` sizes and the staged module sizes |
| V8 | native `gb.rc` contains: the 15.1-v12 body (7 services incl. `service gb_gfx_selftest /system/bin/sh /vendor/bin/gb_dgpu_report.sh selftest`, `mkdir /data/vendor/gb/selftest 0700 root root`, the three `vendor.gb.nouveau.booted=1 && property:persist.vendor.gb.gfx_mode=<m>` triggers (`<m>` = `auto`, `nvidia`, `intel`), `mkdir /data/vendor/gb/notify 0770 root shell`, `service gb_notify` with `user shell`); `socket gb_power stream 0666 root root`; `mkdir /dev/gb_dgpu 0700 root root`; `service gb_nvk_probe` with `user shell` and the `setprop vendor.gb.nouveau.foreign 1` line inside `on property:vendor.gb.nvk_probe=1`; `service gb_gpu_compute /vendor/bin/gb_gpu_compute` with `user nobody` next and `socket gb_gpu_compute stream 0666 nobody graphics`; `service gb_gpu_ui_setup /system/bin/sh /vendor/bin/gb_gpu_ui_setup.sh` and `mkdir /data/vendor/gb/ui 0700 shell shell`; `service gb_slotsync`; every `mkdir /data/vendor/gb ` reads `0751 root shell`, every `mkdir /data/vendor/gb/report ` `0750 root shell`. It contains no `property:persist.vendor.gb.nouveau=` trigger, no `gb_gfxdriver`, no `vendor.gb.gfxdriver`, no `debug.hwui.renderer` |
| V8b | every `/odm/<p>` named in a `service` line exists as `gbnative/<p>`; every `/vendor/bin/<p>` whose basename starts with `gb_`, `nvk_` or `iptsd` exists as `gbnative/vup/bin/<p>` (catches a half-integrated set) |
| V9 | native build.prop: exactly one `ro.gfx.driver.0=gb.sb3.gfxdriver` (none with `--no-gfxdriver`); no `ro.hardware.vulkan`, `ro.hardware.egl`, `ro.hwui.use_vulkan`, `debug.hwui.renderer`, `ro.vendor.gb.gfx_default`, `persist.vendor.gb.gfx_mode`; VM build.prop: no `ro.gfx.driver.` (except `vmtest-gfx`) |
| V10 | no path starts with `gbnative/priv-app` or `gbstage/priv-app` |
| V11 | gbstage (VM) tree: none of the A-08 VM list; with `--expect-tree vmtest-gfx`: exactly the 3.5 additions |
| V12 | for every component manifest under `C:\GB-x86\inject\native\components\`: each `files` dest in the cpio with bytes == `src` and the mode; `payload` names/sizes in the listing; `symlinks` as `0120777` with the target; every `rc_files` text appears in `gb.rc`; every `build_prop` line in build.prop |
| V13 | `gbnative/vup/lib64/hw/vulkan.nouveau.so` is a symlink to `/odm/dgpu/lib64/hw/vulkan.nouveau.so`; empty dirs `gbnative/dgpu` and `gbnative/firmware/nvidia` |
| V14 | no cpio path ends in `/libvulkan.so` |
| V15 | GbGpu.apk: zip with `AndroidManifest.xml`, `classes.dex`, STORED `resources.arsc`, sha == `gpu-ui\out\GbGpu.apk.sha256`; GbGfxDriver.apk: members exactly the 5 of 1.1, both libraries STORED, no member basename `allowlist.txt` |
| V16 | the default entry keeps `gb.hw=native`, `efi_pstore.pstore_disable=1`, `module_blacklist=`, `reboot=pci`, `gb.logpart=EFI-SYSTEM` |
| V17 | `--compare-export ESP`: `vmlinuz`, `gb/inject.gz`, `gb/bootconfig_extra`, `initrd-<slot>.img`, `gb/slot_<slot>.stamp`, every `gb/dgpu-*` byte-identical; the same file set; entries differ only in the title, `gb.boot=` and `androidboot.boot_part_uuid=` |
| V18 | `gb/slot_<slot>.stamp` (`%08x\n`) and `initrd-<slot>.img` exist |
| V19 | `modules.load` line 1 is `# kernel build <tag> (bzImage md5 <m>)` with `<m>` == md5(`vmlinuz`) |
| V20 | `gbnative/etc/gb/gfx_build_id`: one line `apk=<64> icd=<64> nvk=<64> probe=<64> pay=<64>` + `\n`, 0644; `apk` == sha of the cpio APK; `nvk` == sha of its STORED `lib/x86_64/libgb_nvk.so`; `probe` == sha of `gbnative/vup/bin/nvk_probe`; `icd` == sha of the `nvk.json` payload `src` (the payload content itself was proven on the builder, A-06); `pay` == the first field of `dgpu_payload.sha256`. Absent with `--no-gfxdriver` |
| V21 | the packed `gb_nouveau.sh` contains `GB_PANIC_TIMEOUT=30` (user decision) |
| V22 | `gbnative/etc/gb/gfx_app_prefs.default` regular, 0 B, 0644; `gbnative/etc/gb/tu117.sha256` == `firmware_staged_sha256.txt` |
| V23 | no `gb_gfxdriver.sh` anywhere |
| V24 | `--expect-tree vm` or `vmtest-gfx`: native-only checks skipped; the entry has `gb.hw=vm` and `console=ttyS0` |

Tests (TB-3, `C:\GB-x86\tests\w2b\test_verify_image.py`): py_compile; a mutation table (one in-memory mutation per
check; each fails exactly its check and passes when reverted); runtime on today's image <= 5 min. Expected on today's
k13 image `cad193d6...`: **pass** GPT/slots/copied/blank, V1, V4, V5, V8b (every named `/odm`/`gb_*` file is
packed; checked 2026-10-25), V10, V11 (vacuous), V14, V16, V18, V19, V23; **fail** V2 (no nvtest), V3 (4 duplicates),
V6 (186 `.pre-*`), V7 (no payload), V8 (no `gb_notify`, `gb_dgpu_report`, socket line, no `mkdir /data/vendor/gb`),
V9 (no `ro.gfx.driver.0`), V12 (stale component bytes), V13 (a regular `vulkan.nouveau.so`), V15 (no APKs), V20,
V21 (the packed pre-Wave-1 `gb_nouveau.sh` has no `GB_PANIC_TIMEOUT=30`), V22 (no `etc/gb/`). Any other result must
be explained before R-04.

### B-04 `C:\GB-build\native\dualboot\build_internal.py` (affected: yes)

* `:113` `files = be.slot_files(cmdline, be.INJECT_CPIO_NATIVE, TITLE, timeout=4, native=True)`;
  `:119` `be.make_esp(files, a.esp_mib * MiB, min_free=be.native_min_free(files))`.
* After `manifest.json` is written: `verify_image.py --esp <out>\EFI-SYSTEM.img --boot internal --super-guid <guid>`;
  non-zero exit = exit 1, unless `--clear-nx-compat` or a `--test-*` option is used (those change or truncate the
  export and are never used by the ISO).
* `manifest.json` `esp_files` picks up the new entries automatically. Test (TB-4): py_compile; `--dry-run` prints
  the layout; the full run happens inside R-12.

### B-05 `C:\GB-x86\iso\build\builder\stage_initramfs.py:26` (W2-06)

`MODULE_DENY = ["spi_intel*", "mtd*", "efi_test", "efi_pstore", "tpm*", "nouveau", "nvidia*", "drm_gpuvm", "drm_exec",
"gpu_sched"]` (names are matched after `-` -> `_`). None of them is in today's resolved set (build log 085013:
`modules: 34 names -> 46 .ko files`; `installer\etc\modules.list` = storage, fs, hid, Surface), so the initramfs
module set does not change.

### B-06 `C:\GB-x86\iso\test\t0_static.py:31`

The same `DENY` tuple (the check at `:376-380` already normalises); plus a host-side static assertion that `DENY` ==
`stage_initramfs.MODULE_DENY` (read with `ast`), and a `facts["module_deny"]` line.

### B-07 `C:\GB-x86\iso\build\build_iso.py` (C-17, C-18)

1. Preflight, after the sidecar check (`:930-937`): `verify_image.py <usb image>`; exit != 0 -> `problem()`
   ("verify_image.py reports problems on the USB image"), so a real build fails and a dry run lists it.
2. `export_checks()` (`:522-581`) gets check `8_esp_tree`: both ESPs read with the verify_image module (imported by
   path, 3.2): byte-identical `vmlinuz`, `gb/inject.gz`, `gb/bootconfig_extra`, `initrd-<slot>.img`,
   `gb/slot_<slot>.stamp`, every `gb/dgpu-*`; the same file set; `gb-<slot>.conf` and `gb-<slot>-nvtest.conf` tokens
   equal except the title text, `gb.boot=` and `androidboot.boot_part_uuid=`. Failure: `export cross-check 8 failed:
   the export ESP was not built from the same tree as the USB image (<first difference>)`; for a reused export the
   existing wrapper (`:1020-1022`) adds the `--new-export` hint.
3. For a reused export also run `verify_image.py --esp <export ESP> --boot internal --super-guid <export super>`
   (`build_internal.py` runs it for fresh ones, B-04).
4. `VERIFIED_USB_SHA256` (`:54`) is updated by the integrator at R-11 (one line).

Tests (TB-5): `test_export_check8.py` (read-only): the current pair passes check 8 (1.3: only the entry differs);
in-memory mutations fail it (one byte of `vmlinuz`; a missing `gb/dgpu-*` on one side; an extra token in the nvtest
entry). `python -I -B -m py_compile build_iso.py t0_static.py`; no CR bytes in `iso\build\builder\*`,
`iso\build\templates\*`, `iso\test\*.sh`; `build_iso.py --dry-run` on today's image reports the verify_image problem
(negative test of the new gate; read-only).

### B-08 `C:\GB-x86\iso\test\check_esp_tree.sh` (T3 criterion 6)

After the raw greps (`:56-57`), with `X` = the slot from the reference ESP's `loader/loader.conf` `default gb-<x>`:
`loader/entries/gb-$X-nvtest.conf` contains `gb.nouveau=test` and `androidboot.boot_part_uuid=$SUPER`;
`gb-$X.conf` has no `gb.nouveau=`; no `*-gfxtest.conf`; no `gb.gfxdriver=` in any entry; exactly one
`gb/dgpu-*.erofs` and its `.sha256`, `sha256sum` of the image == the line's first field and the line names the file.
`bash -n` and `shellcheck` clean; LF only.

### B-09 `C:\GB-x86\iso\DESIGN.md` section 13 and `README-ISO.md`

Add "## 13. Wave 2 integration decisions (v2.3)" (this section wins over earlier ones): 13.1 MODULE_DENY (B-05/B-06);
13.2 the verify_image preflight (B-07.1); 13.3 export cross-check 8 (B-07.2); 13.4 Wave 2 refreshes the export with
`--new-export` (new dual-boot GUIDs: an install made with an earlier ISO is removed with that ISO or with
`uninstall_dualboot.ps1 -Force`); 13.5 the T3 criterion 6 additions (B-08); 13.6 the USB image identity is passed with
`--usb-sha256` and recorded in `common.py`; 13.7 the measured sizes after R-13. README-ISO: one paragraph on the
nvtest entry, the payload on the ESP and 13.4.

### B-10 `C:\GB-x86\iso\test\common.py:60` (at R-11, by the integrator)

`USB_SHA` = the new image's sha256. `USB_SIZE` (`:61`) is unchanged (28,814,885,376).

---

## 6. Build and test order

Every step names its pass condition; a failed step stops the chain (fix, then repeat that step). Times are machine
time. Builder rule throughout: `/` and `/build` > 5 GB free, and `/` >= 25 GiB until R-12 (ISO preflight floor): all
Wave 2 builder work goes to `/build`.

| Step | What | Command / action | Pass | Time |
|---|---|---|---|---|
| R-00 | Pre-flight (read-only) | the commands of 1.1-1.3 again; `vmrun -T ws list`; free space and RAM | AC-001..AC-010 | 10 min |
| R-01 | Backups | each editor: its files -> `C:\GB-x86\archive\pre-w2\...` | copies' sha == 1.2 | 1 min |
| R-02 | **Set A and Set B in parallel** | sections 4 and 5, with A-03 (k14 staging) inside Set A | TA-1..TA-4, TA-8; TB-1..TB-5 | engineering |
| R-03 | Integration gate | each editor lists the files it changed with sha256; intersection empty; every other file of 1.2 and every artifact of 1.1 unchanged; `odm_sepolicy.cil` `8265b908` | AC-011..AC-013 | 5 min |
| R-04 | **CP-1: ESP-only + first payload build** | `New-Item -ItemType Directory D:\gb-w2test\esp -Force`; `python -I -B C:\GB-x86\build_usb.py --esp-only D:\gb-w2test\esp\cp1.img --super-guid 11111111-2222-4333-8444-555555555555 2>&1 \| Tee-Object D:\gb-w2test\esp\cp1.log`; then `python -I -B C:\GB-x86\verify_image.py --esp D:\gb-w2test\esp\cp1.img --boot usb --super-guid 11111111-2222-4333-8444-555555555555 --krel C:\GB-build\kernel\k14_release.txt` | `PAYLOAD ok ...`, `CONTENT-OK`; `RESULT: OK`; TA-5; the ESP-applicable checks of AC-301..AC-325; AC-331..AC-334 | 10 min |
| R-05 | **CP-2: determinism** (+ optional TA-7) | the same build to `cp2.img` with `$env:GB_NO_BUILDER='1'` (cache hit required); `certutil -hashfile` both; remove both images after | identical sha256; no builder access | 5 min |
| R-06 | **USB image** (new name, sparse) | `python -I -B C:\GB-x86\build_usb.py --out C:\GB-x86\out\googlebook-sb3-usb.new.img --sparse 2>&1 \| Tee-Object C:\GB-x86\out\build_usb.new.log` | `.part` -> verify `RESULT: OK` -> renamed; sidecar; size 28,814,885,376; C: free drop <= 10 GiB (sparse); AC-327..AC-330 | 25 min |
| R-07 | Tree diff and budgets | `python -I -B <SCRATCH>\w2\tree_diff.py C:\GB-x86\out\googlebook-sb3-usb.img C:\GB-x86\out\googlebook-sb3-usb.new.img` (integrator tool from `compare_native.py`, full lists) | exactly the 9.3 expected differences; the 9.2 budgets (AC-401..AC-404) | 5 min |
| R-08 | **VM-2** k14 boot of a `--vm-test` image | 7.2 (builder suspended at the start of R-08) | AC-501..AC-512 | 35 min |
| R-09 | **VM-3** GbGpu + gfx driver package on the `--vm-test-gfx` image | 7.3 | AC-601..AC-615 | 35 min |
| R-10 | **VM-1** native smoke of a sparse copy of the new USB image | 7.4 | AC-701..AC-718 | 30 min |
| R-11 | Resume builder; **USB swap**; constants | `vmrun -T ws start C:\GB-build\builder2.vmx nogui`; `New-Item -ItemType Directory C:\GB-x86\out\prev`; move `googlebook-sb3-usb.img`, its `.sha256`, `build_usb.log` to `prev\`; rename `googlebook-sb3-usb.new.img` -> `googlebook-sb3-usb.img`, `build_usb.new.log` -> `build_usb.log`; rewrite the sidecar as `<hex>  googlebook-sb3-usb.img`; set `iso\test\common.py` `USB_SHA` and `build_iso.py` `VERIFIED_USB_SHA256` to `<hex>`; `python -I -B C:\GB-x86\verify_image.py` (default path) | `RESULT: OK`; sidecar == recomputed sha256 | 10 min |
| R-12 | **Export backup + ISO** | `New-Item -ItemType Directory D:\GB-iso\new, D:\GB-iso\prev\export-20261025T042105 -Force`; copy `C:\GB-x86\iso\work\export\*` into the second; (a) `python -I -B C:\GB-x86\iso\build\build_iso.py --dry-run --usb-sha256 <hex> --out D:\GB-iso\new\googlebook-sb3-installer.iso` (reuses the k13 export); (b) the same with `--new-export`; (c) (b) without `--dry-run` | (a) `BUILD FAILED: export cross-check 8 failed ...` with the `--new-export` hint (the stale export is refused); (b) `DRY RUN OK`; (c) build `done`; cross-checks 1-7 and `8_esp_tree` `ok` in `export_check.json` (AC-800..AC-802) | 40 min |
| R-13 | ISO artifact checks | `verify_image.py --esp C:\GB-x86\iso\work\export\EFI-SYSTEM.img --boot internal --super-guid <export super>`; `verify_image.py C:\GB-x86\out\googlebook-sb3-usb.img --compare-export C:\GB-x86\iso\work\export\EFI-SYSTEM.img`; ISO size; `.sha256` == recomputed | AC-803..AC-806 | 10 min |
| R-14 | **ISO swap** | `New-Item -ItemType Directory D:\GB-iso\prev -Force`; move the current ISO (read-only attribute stays), `.iso.sha256`, `.manifest.json`, `.build.json` to `D:\GB-iso\prev\`; move the four new files and `build-<bid>.log` from `D:\GB-iso\new\` to `D:\GB-iso\`; rewrite `iso` and `payload_manifest` in the moved build.json to the `D:\GB-iso\` paths (load, set, `build_iso.json_bytes()` format) | `run_all.py --preflight` passes | 5 min |
| R-15 | **ISO tests (subset)** | `python -I -B C:\GB-x86\iso\test\run_all.py --preflight`; `python -I -B C:\GB-x86\iso\test\run_all.py --only "T0,T1a,T1b,T3nf,T3ne,T3,T3nd,T4,T4b,T9,T7,T2e"` | exit 0; the 12 ids PASS; `control_verify_image.json` ok (AC-807..AC-812) | 34 min |
| R-16 | Final acceptance | walk section 10; copy the evidence to `C:\GB-build\native\dgpu\1b\evidence\w2\` | every AC PASS (or a recorded, accepted CONDITIONAL in 7.4) | 30 min |
| R-17 | Question + cleanup | ask section 13; then delete `C:\GB-x86\out\prev\`, `D:\GB-iso\prev\` (clear the ISO's read-only attribute first), `D:\gb-w2test\`, `D:\GB-iso\new\`; builder `/build/native/dgpu/{payload-stage,w2a,w2b}`; optionally the superseded chunk cache `/var/tmp/gbiso/cache/cad193d629c6d053-*` | C: and D: free back up (9.1) | 5 min |
| R-18 | Hand-off | the HW lists of section 12 to the user | | |

---

## 7. Throwaway VMware checks (never the user's VM, never `lapis.bin`)

### 7.0 Common rules

* **Location**: everything under `D:\gb-w2test\` (allowed by the task): `vm\` (vmx, vmdk, disk, serial log; recreated
  per check), `evidence\vm<k>\`. Delete `vm\` after each check. Copy the evidence (text, PNG) to
  `C:\GB-build\native\dgpu\1b\evidence\w2\vm<k>\` at R-16.
* **VM**: 4096 MB, 2 vCPU, EFI, Secure Boot off, NVMe disk = a `monolithicFlat` descriptor over a regular file in
  `D:\gb-w2test\vm\`, no USB controller, no sound, no floppy (Appendix B). VM-2/VM-3: NIC NAT `vmxnet3` (the VM tree's
  adb needs it, `build_esp.py:846-869`). VM-1: no NIC.
* **Pre-start guard** (integrator tool `<SCRATCH>\w2\vmguard.py <vmx>`, the DESIGN 7.1 rules re-rooted at
  `D:\gb-w2test\`): every `*.fileName` and extent resolves to an existing regular file below `D:\gb-w2test\` (no
  symlink/junction); no `\\.\`, `\\?\`, `PhysicalDrive`, `GLOBALROOT`, `rawDevice`, `fullDevice`,
  `partitionedDevice`; extents only `RW <n> FLAT "<file>" 0`; USB keys `FALSE`. A failed guard = no start.
* **RAM**: host free was 3.3 GB with the user's VM (12 GB) and the builder (8 GB). Before R-08: make sure no builder job
  runs (`pgrep -fa '[/]var/tmp/gbiso/[0-9]|[m]ake_dgpu_payload|[c]lang'` empty, no open build ssh), then `vmrun -T ws
  suspend C:\GB-build\builder2.vmx soft`; free must then be >= 5 GB. The builder stays suspended for R-08..R-10 (none
  of them needs it: `--vm-test` builds use the VM tree only, TA-4) and is resumed at R-11.
* **vmrun**: only `-T ws start D:\gb-w2test\vm\gbw2.vmx nogui`, `-T ws stop D:\gb-w2test\vm\gbw2.vmx hard` (the guest
  has no VMware Tools, so `soft` is not available, INFERRED), `-T ws list` (read-only), and the builder
  suspend/start. `vmrun -T ws list` while a check runs shows exactly `lapis-boot.vmx` and `gbw2.vmx`.
* **adb** (VM-2, VM-3): `$ADB = 'C:\GB-x86\host\scrcpy-win64-v5.0.1\adb.exe'`; the guest prints `gb_adb: adbd on
  tcp:5555 (inet <ip>/<n>)` to the serial log (`console=ttyS0` is in the VM cmdline); `& $ADB connect <ip>:5555`; every
  call with `-s <ip>:5555`; `& $ADB disconnect <ip>:5555` at the end; never `kill-server`, never the user's VM address.
  The host key is the one in `inject\adb\adb_keys`, so no prompt.
* **Image size**: VM-2/VM-3 images use `--userdata-gib 8 --sparse` (about 8 GiB allocated). One image at a time on D:.

### 7.1 Shared VM checks script

Each check is a list of adb/gblog checks with exact expected output; record every command's output into
`evidence\vm<k>\<n>-<name>.txt` and the pass/fail per AC in `evidence\vm<k>\result.txt`.

### 7.2 VM-2: k14 boot via a `--vm-test` image (K-10, VM-1B-03, gb_slotsync on Android)

Build: `python -I -B C:\GB-x86\build_usb.py --vm-test --sparse --userdata-gib 8 --out D:\gb-w2test\vm\disk.img`
(verify_image `--expect-tree vm` runs inside: `RESULT: OK`). Descriptor sectors = file size / 512. Boot; wait for the
`gb_adb` line (first boot formats userdata and runs dexopt: allow 20 min).

| AC | Check (adb unless stated) | Pass |
|---|---|---|
| AC-501 | serial log | `Linux version 6.18.21-android17-5-g9629ced3e50e-ab16302714` |
| AC-502 | `uname -r` | the k14 krel |
| AC-503 | `getprop sys.boot_completed` | `1` |
| AC-504 | `cat /proc/modules` | `vmwgfx`, `vmxnet3`, `drm_ttm_helper`, `efidrm`, `drm_sysfb_helper` present (the VM set); no `nouveau`, `drm_exec`, `drm_gpuvm`, `gpu_sched` |
| AC-505 | `dmesg \| grep -ciE 'module verification failed\|Unknown symbol\|disagrees about version\|Key was rejected'` | `0` |
| AC-506 | `dumpsys activity activities \| grep -E 'ResumedActivity'`; `exec-out screencap -p` | the launcher is resumed; screenshot saved |
| AC-507 | VM-1B-02 baseline: `getprop ro.gfx.driver.0`; `settings get global` of the 5 `updatable_driver_*` keys; `getprop \| grep -c 'gb\.gfx'`; `dumpsys gfxinfo com.android.systemui \| grep Pipeline`; `pm list packages -f gb.sb3` | empty; all `null`; `0`; `Skia (OpenGL)`; none |
| AC-508 | `ls /odm/dgpu /odm/app /vendor/bin/gb_nouveau.sh /vendor/bin/gb_power` | all absent |
| AC-509 | the rebuilt gb_slotsync on Android: `ps -A -o NAME \| grep -c gb_slotsync`; `dmesg \| grep gb_slotsync`; `ls -d /dev/gb_esp.lock*` | `1` (the 60 s watch loop runs; without a pending OTA a cycle logs nothing); no error line (`mount`, `ESP lock held over 600 s`, `writing ... failed`); no lock directory left |
| AC-510 | `logcat -b crash -d` | no entry from `gb_*` or a system process |
| AC-511 | `toybox nc --help 2>&1 \| grep -c -- '-U'` | `1` (the gb_bootlog dependency, 2.2 item 8) |
| AC-512 | `vmrun stop hard`; delete `D:\gb-w2test\vm\` | done; `lapis-boot.vmx` still running |

### 7.3 VM-3: GbGpu + gfx driver package at runtime (VM-1B-05/UI-07 extended, D8, GD-05 replacement)

The VM tree can host both packages: gbinit copies `gbstage` to `/odm` (a tmpfs) and PackageManager scans `/odm/app`
as an ODM system-app directory on Android (the native image relies on the same, ux-app 4.2). **Skip rule:** if VM-2
passed and VM-3's image does not reach the launcher, that is a FAIL of the gfx components; VM-3 is SKIPPED (with the
reason) only if VM-2 itself did not reach the launcher.

Build: `python -I -B C:\GB-x86\build_usb.py --vm-test --vm-test-gfx --sparse --userdata-gib 8 --out
D:\gb-w2test\vm\disk.img` (verify_image `--expect-tree vmtest-gfx`: `RESULT: OK`). Boot as VM-2.

| AC | Check | Pass |
|---|---|---|
| AC-601 | `pm path gb.sb3.gpu`; `pm path gb.sb3.gfxdriver` | `package:/odm/app/GbGpu/GbGpu.apk`; `package:/odm/app/GbGfxDriver/GbGfxDriver.apk` |
| AC-602 | `dumpsys package gb.sb3.gpu \| grep -E 'codePath\|pkgFlags\|privateFlags'` (and for gb.sb3.gfxdriver) | `codePath=/odm/app/...`; `pkgFlags` contains `SYSTEM`; `privateFlags` has no `PRIVILEGED` |
| AC-603 | `ls /odm/priv-app`; `pm list packages -s \| grep gb.sb3` | absent; both packages listed |
| AC-604 | `getprop ro.gfx.driver.0` | `gb.sb3.gfxdriver` (no "Do not have permissions to set" in `dmesg`) |
| AC-605 | `settings get global` `updatable_driver_all_apps`, `..._production_opt_in_apps`, `..._production_opt_out_apps` | `null`, `null`, `null` (nothing writes them in VMware) |
| AC-606 | `settings get global updatable_driver_production_allowlist` and `..._denylist` | empty or `null`; never `*` or a package name (GpuService resets them when a driver package is configured, INTERFACES 24.7 b) |
| AC-607 | `logcat -d -s gb_gfxdriver`; `dumpsys gfxinfo com.android.systemui \| grep Pipeline` | no line (no app process loaded the shim); `Skia (OpenGL)` unchanged |
| AC-608 | `settings get secure sysui_qs_tiles` | contains `custom(gb.sb3.gpu/.GpuTileService)` once (`gb_gpu_ui_setup` ran) |
| AC-609 | `cmd statusbar expand-settings`; `uiautomator dump /sdcard/w2.xml`; `cat /sdcard/w2.xml`; screencap | the tile shows `Graphics` and `Unavailable` |
| AC-610 | `cmd statusbar click-tile gb.sb3.gpu/.GpuTileService`; wait 2 s; dump again | still `Unavailable`; no toast, no dialog, no crash |
| AC-611 | `am start -n gb.sb3.gpu/.SettingsActivity`; dump; screencap | page title `Graphics`, status `Unavailable`; every radio and switch `enabled="false"` (no socket in the VM, UI-1B-11) |
| AC-612 | Settings > System: `am start -a android.settings.SYSTEM_SETTINGS` (if the action exists on lapis, else record SKIP for this AC); dump | an entry `Graphics` with summary `Unavailable` (injected; system apps only) |
| AC-613 | `am broadcast -a gb.sb3.gpu.action.STATE_CHANGED -p gb.sb3.gpu` | delivered; no crash |
| AC-614 | `logcat -b crash -d`; `logcat -d \| grep -E 'FATAL EXCEPTION\|ANR in' \| grep -c gb.sb3` | no `gb.sb3.*` crash; `0` |
| AC-615 | AC-504, AC-505 and AC-510 on this boot; stop hard; delete `vm\` | as VM-2 |

### 7.4 VM-1: native smoke of the new USB image (NP-25, GP-24, VM-1B-04, VM-1B-06; gblog evidence)

Disk: `python -I -B <SCRATCH>\w2\sparse_copy.py C:\GB-x86\out\googlebook-sb3-usb.new.img D:\gb-w2test\vm\disk.img`
(integrator tool: FSCTL_SET_SPARSE, copy only non-zero 1 MiB blocks, write the last sector; the copy's sparse-aware
sha256 must equal the source sidecar). The default entry `gb-<slot>` boots automatically (timeout 4 s). Let it run
18 min after power-on (uptime >= 960 s covers the t=900 snapshot), then `vmrun stop hard` (the ESP is unmounted between
snapshots). Extract: `python -I -B C:\GB-x86\verify_image.py --extract D:\gb-w2test\vm\disk.img gblog
D:\gb-w2test\evidence\vm1\gblog`. The nvtest entry (manual `Down`, `Enter` within 4 s) is optional and not required.

Two tiers, because `gb_nouveau` runs only after `sys.boot_completed` (2.2 item 7):

| AC | Check (from `gblog\`) | Pass |
|---|---|---|
| AC-701 | `bootcount` and the snapshots | `bootcount` = 1; `boot1-t40.txt`, `-t90`, `-t180`, `-t420`, `-t900` exist; each header `hw=native`; record `boot_completed=` of each |
| AC-702 | every snapshot | has `== dgpu`, `-- gb_power`, `-- settings`, `-- state` |
| AC-703 | `== gbinit module loading` | no `FAILED` line whose error is `Invalid module format`, `Unknown symbol`, `disagrees about version`, `Key was rejected`, `Required key not available` or `Exec format error`; other `FAILED` lines listed and each explained by absent hardware |
| AC-704 | `== modules` | none of `nouveau`, `drm_exec`, `drm_gpuvm`, `gpu_sched`, `drm_ttm_helper`; `GB_MODULES: loaded N, failed M` present in `== dmesg` |
| AC-705 | `== dgpu` PCI lines, `nouveau refcnt=` | no `0x10de` function (VMware); `nouveau refcnt=-` |
| AC-706 | `-- gb_dgpu_check` and the DRM node lines | no `card0`/`renderD128` driven by `0x8086 i915` (the i915 guard's input: the guard would refuse) |
| AC-707 | `-- gb_power` | `hello` reply with `"ok":true`, `"role":"root"`, `"api":1`; `status` with `"state":"unavailable"`; `get-mode` with reason `nodtx` |
| AC-708 | `-- properties` | `vendor.gb.power.api=1`, `vendor.gb.dgpu.state=unavailable`, `vendor.gb.gfx_reason=nodtx`, `vendor.gb.gfx_active` `intel` or unset; `ro.gfx.driver.0=gb.sb3.gfxdriver` in the full getprop dump |
| AC-709 | `-- settings` | each of the 5 keys `null`, or the command timed out (no system_server); never `0`, `1` or `3` (REVIEW D3) |
| AC-710 | `-- state` notify listing | no `kind=settings` message ever queued (directory empty) |
| AC-711 | the t=420 and t=900 snapshots | contain `== /data/vendor/gb/report/dgpu-1.txt` with `== drm`, `== power`, `== end` |
| AC-712 | `-- state`, mounts | no `nouveau.loading`, no `gfx_session`, no `dgpu_session`; no `/odm/dgpu` or `/odm/firmware/nvidia` mount; `vendor.gb.dgpu.payload` unset (the stage step never ran) |
| AC-713 | properties, reports | `init.svc.gb_gfx_selftest` absent; no `report/selftest-*`; `selftest/result` absent |
| AC-714 | dmesg in the snapshots | no `gb_bootlog: ESP lock held over 600 s`; no `Do not have permissions to set`; `/dev/gb_esp.lock` absent in every `-- state` listing |
| AC-715 | **tier 1 (boot_completed never 1, the INFERRED case)** | `vendor.gb.nouveau.result` unset and no `gb_nouveau.log` load line (the boot_completed gate held) -> **PASS-CONDITIONAL**: the refusal itself is covered by `NP-1B-VM` (component suite, 1083/1083) and stays on the HW list |
| AC-716 | **tier 2 (boot_completed=1 in any snapshot)** | `vendor.gb.nouveau.result=refused:i915`, a `gb_nouveau.log` line naming the i915 guard, `vendor.gb.nouveau.booted=1`; `dumpsys package gb.sb3.gpu` `codePath=/odm/app/GbGpu`; else FAIL |
| AC-717 | `gblog/audio-b1-*` | absent in tier 1; in tier 2 present and copied under the lock (kmsg) |
| AC-718 | cleanup | stop hard, delete `vm\`; the source image is untouched (sidecar re-check) |

GC-11 (`/vendor/bin/gb_gpu_compute` exits 3) needs a shell in the guest, which the native image does not have in
VMware; `vendor.gb.compute.state` from the getprop dump (`stopped` or unset) is recorded, the exit-3 behaviour is
covered by the component's host/bionic GC-11 and stays a HW item.

---

## 8. ISO tests: what to re-run and why (R-15)

Durations from the last full run (`iso\test\logs\20261025-085013-37112350\report.json` start times; full suite 2h11m).

| Test | Run? | Why | Time |
|---|---|---|---|
| T0 static | **yes** | new `esp-usb`/`esp-internal` objects, manifest and chunks; the installer initramfs rebuilt with the new MODULE_DENY; the new DENY check (B-06) | 2m17 |
| T1a whole disk, exact | **yes** | proves the new `esp-usb` object writes the new image bit-exact (`USB_SHA`, B-10) and runs the new verify_image on the written disk (`validate_wholedisk.py:73-96`) after the control run | 4m02 |
| T1b grow | **yes** | the new ESP on a larger disk plus verify_image (V1, GPT size independence) | 3m31 |
| T3-build, G1 = T3n(f) + T3n(e) | **yes** | G1 is the prerequisite of G2 (same disk and NVRAM) | 1m18 + 1m24 |
| G2 = T3 + T3n(d) | **yes** | new `esp-internal` (k14, payload, nvtest) and a fresh export (new GUIDs, new expected plan); criterion 6 now runs B-08 | 5m04 |
| G3 = T4 | **yes** | uninstall with the new ESP size/content and GUIDs | 1m09 |
| G4 = T4b + T9 | **yes** | Windows import and uninstall against the new export manifest; T9 runs in the same group | 12m54 |
| T7 + T2e | **yes** | the hybrid ISO as a raw disk with the new ISO size; cheap | 1m55 |
| T1c, T1d(n), T1d(y) | no | zeroing/no-grow logic is installer code, unchanged; the ESP object is covered by T1a/T1b | |
| T2a-T2j | no | refusals before any write, payload-independent, installer unchanged | |
| T3n(a-c), T4n, T4nb | no | refusal paths, payload-independent | |
| T5, T6, T6m, T6n, T6g | no | media discovery and GRUB unchanged; the ISO stays below 8 GiB and far below the 9 GiB test media | |
| T7b, T10 | no | multi-extent dd and fonts: installer, GRUB and fonts unchanged | |
| T8 | no | Windows scripts unchanged (`export_small` is fixed) | |

Total about 34 min. **Full-suite rule:** if any file under `iso\installer\`, `iso\build\templates\`,
`iso\build\builder\` (other than the B-05 line), `iso\test\gbtests.py`, `vmtest.py` or `mkdisks.py` changed, or the
ISO exceeds 8,000,000,000 B, run the full suite (`run_all.py`, 2h11m, overall PASS required).

---

## 9. Size budgets

### 9.1 Disk space timeline (host and builder)

| Moment | C: free | D: free | Builder `/` | Builder `/build` |
|---|---|---|---|---|
| now | 36.3 GiB | 46.3 GiB | 26.8 GiB | 11.4 GiB |
| R-04/R-05 (payload cache, checkpoints on D:) | -0.06 | -1.5, back after R-05 | 0 | -0.3 GiB during the payload build |
| R-06 new USB image, sparse | about -8 (28.3) | | | |
| R-08..R-10 (one VM image at a time) | | about -8, back after each | | |
| R-12 fresh export (12.6 GiB temp), ISO to `D:\GB-iso\new\` | about 15.7 at the low point, then about 27.8 | -7.0 ISO, -0.5 export backup (38.8) | about -14 during the build (12.8), about 19.8 after | 0 |
| R-15 ISO test disks (<= 13.5 GiB physical at a time) | | about 25.3 at the low point | test media in `/var/tmp/gbiso/test`, removed after | |
| R-17 cleanup | +26.8 (old image) -> about 54.6 | +7.5 | +7.0 if the old cache is deleted | |

All floors hold: C: and D: > 10 GB; builder `/` and `/build` > 5 GB; builder `/` >= 25 GiB at R-12's preflight
(no Wave 2 builder work touches `/` before it).

### 9.2 Image content

| Item | Today | After Wave 2 (projected) | Limit / check |
|---|---|---|---|
| `vmlinuz` | 26,764,288 B (k13) | 26,764,288 B (k14, `stat`) | md5 `92bf19d2...` (AC-331) |
| `gb/inject.gz` | 68,322,276 B | about 22.9 MB: minus backups 13.60 MB, NVIDIA firmware 30.34 MB, 5 modules 1.55 MB, ICD 3.49 MB (gzip-9 per file); plus GbGfxDriver.apk 3,504,156 B, GbGpu.apk 60,531 B, scripts and binaries about 0.06 MB (measured gzip-9) | |
| native cpio (= `/odm` tmpfs RAM) | 191,706,300 B | about 72 MB (-59.7 backups, -52.7 firmware, -7.4 modules, -16.4 ICD, +16.5 APK, +0.1) | measure `df /odm` on HW |
| `initrd-<x>.img` | 102,845,429 B (vendor + generic + bootconfig 34,523,153 B) | about 57.4 MB | |
| `gb/dgpu-<krel>.erofs` | n/a | 60,260,352 B expected (NP-21) | <= 80 MiB (AC-332) |
| USB ESP used / free | 188.9 / 315.2 MiB of a 504.1 MiB data area | about 160 / 344 MiB | free >= max(initrd) + 144 MiB, about 199 MiB (A-10, V1) |
| USB ESP after one OTA (gb_slotsync builds the other initrd) | about 287 MiB used | about 215 MiB used | |
| gblog | unbounded | newest 8 boots, <= about 120 MiB worst case; floor 128 MiB | A-09 |
| Export `EFI-SYSTEM.img` | 536,870,912 B | same size, same content class as the USB ESP (V17) | exactly 536,870,912 B (`build_iso.py:556`) |
| VM ESP in `lapis.bin` | 192 MiB | **untouched** | |
| USB image | 28,814,885,376 B | unchanged size; about 8 GiB allocated (sparse) | T1a size (`common.py:61`) |
| `/data/vendor/gb/payload/dgpu.erofs` (device) | n/a | 60,260,352 B after the first load | |
| ISO | 7,474,929,664 B | about 7.40-7.48 GB (each ESP object loses about 30-40 MB of incompressible gzip data and gains the erofs, which zstd -9 compresses a little) | 8 GiB ceiling |

### 9.3 Expected native tree differences (R-07, old image vs new)

* **Removed**: the 186 `gbnative/lib/modules/*.pre-*`; the 30 `gbnative/firmware/nvidia/tu117/**` files (the
  directory stays, empty); the 5 opt-in `.ko`; the regular file `gbnative/vup/lib64/hw/vulkan.nouveau.so` (now a
  symlink); the 4 duplicate entries (one copy each stays).
* **Added**: `gbnative/vup/bin/gb_dgpu_report.sh`, `gb_notify.sh`, `gb_gpu_compute`, `gb_gpu_ui_setup.sh`;
  `gbnative/etc/gb/tu117.sha256`, `dgpu_payload.sha256`, `gfx_build_id`, `gfx_app_prefs.default`;
  `gbnative/app/GbGpu/GbGpu.apk`, `gbnative/app/GbGfxDriver/GbGfxDriver.apk`; the directories `gbnative/dgpu`,
  `gbnative/etc/gb`, `gbnative/app/**`; the symlink.
* **Changed**: `gbnative/gb_bootlog.sh`, `gbnative/gb_audio.sh`, `gbnative/gb_audiodiag.sh`, `gbnative/etc/init/gb.rc`,
  `gbnative/etc/build.prop` (one line added), the 70 dist-built `.ko` (k14), both load lists (header), `gb_power`,
  `gb_nouveau.sh`, `gb_dgpu_check.sh`, `nvk_probe`, `gb_slotsync`.
* **Unchanged**: everything else, including the 8 lapis HDA codec modules and all non-NVIDIA firmware.

ESP files: `vmlinuz`, `gb/inject.gz`, `initrd-<slot>.img`, `gb/slot_<slot>.stamp` and `loader/entries/gb-<slot>.conf`
(new super GUID) change; `loader/entries/gb-<slot>-nvtest.conf`, `gb/dgpu-<krel>.erofs` and `.sha256` are new;
`EFI/BOOT/BOOTX64.EFI`, `loader/loader.conf`, `gb/bootconfig_extra` are unchanged.

---

## 10. Acceptance checks (complete list)

Every check records PASS/FAIL with the real output under `evidence\w2\`. "CONDITIONAL" exists only for AC-715.

**Pre-flight and gate**

* AC-001 every artifact sha of 1.1 matches.
* AC-002 every shared file sha of 1.2 matches before its first edit.
* AC-003 `/build/dist14` exists with the k14 krel and bzImage md5 `92bf19d2...`; `/build/kbuild14.done` exists.
* AC-004 no ISO job on the builder (self-excluding pattern); load < 2.
* AC-005 builder `/` >= 25 GiB and `/build` > 6 GB.
* AC-006 C: and D: free as in 9.1 "now" (>= the planned peaks + 10 GB).
* AC-007 `vmrun -T ws list` = the user's VM and the builder only.
* AC-008 the current USB image's sidecar is `cad193d6...`; the current ISO's is `1718bcc1...`; its last run 38/38.
* AC-009 the user's VM has no pending OTA (`build_usb.py` slot guard passes at R-04/R-06: `system slot: b ...`).
* AC-010 the archive `C:\GB-x86\archive\pre-w2\` holds a copy of every file before its first edit.
* AC-011 Set A and Set B changed disjoint file sets, each exactly its list in 4/5.
* AC-012 no other shared file changed (1.2 read-only rows, `odm_sepolicy.cil` `8265b908`, `gb_audio.rc`).
* AC-013 no command so far named `lapis.bin` for writing or `lapis-boot.vmx` at all (run logs).

**Set A**

* AC-101 A-01 counts (186 moved, 59,735,154 B; 78 `.ko` + 2 load lists left).
* AC-102 A-03 staging result (k14_accept, md5, 78 + 9 `.ko`, headers, archive, no `*.pre-k14` under `inject`).
* AC-103 TA-1. AC-104 TA-2. AC-105 TA-3 (every bootlog case, both passes, shellcheck). AC-106 TA-4 (VM-only build
  without the builder). AC-107 TA-5 (tree inspection and the 6 in-memory mutations). AC-108 TA-7 (optional).
  AC-109 TA-8.

**Set B**

* AC-201 TB-1 S1-S3, S5, S6 pass with no sanitizer report.
* AC-202 TB-1 S4: the old source fails under ASan.
* AC-203 TB-1 S7: the NDK binary, installed at `inject\slotsync\gb_slotsync`, old one archived.
* AC-204 TB-2 (shellcheck, A1-A6, both passes).
* AC-205 TB-3 mutation table (every V fails on its mutation).
* AC-206 TB-3 today's image gives exactly the expected pass/fail set (B-03); runtime <= 5 min.
* AC-207 TB-4. AC-208 B-05/B-06 static assertion (DENY == MODULE_DENY). AC-209 TB-5 (check 8 positive on the current
  pair, negative on the 3 mutations; py_compile; no CR; dry-run gate). AC-210 B-08 `bash -n` + shellcheck.

**ESP and USB image (R-04..R-07)**

* AC-301..AC-325 = verify_image V1..V24 and V8b, one AC per check, `ok` on CP-1 (the ESP-applicable ones), on the new
  USB image and (R-13) on the export ESP.
* AC-326 verify_image on the 27 GiB image finishes in <= 5 min.
* AC-327 R-06: `.part` verified before the rename, sidecar written by `build_usb.py`, size 28,814,885,376.
* AC-328 the new image's sha256 recomputed independently (`certutil -hashfile`) == the sidecar.
* AC-329 the C: free drop of R-06 <= 10 GiB (sparse write worked).
* AC-330 R-05: CP-1 and CP-2 byte-identical; CP-2 made with `GB_NO_BUILDER=1`.
* AC-331 `vmlinuz` md5 `92bf19d2...`; krel == `k14_release.txt`.
* AC-332 `PAYLOAD ok`, `CONTENT-OK`, payload <= 80 MiB; size and sha recorded (expected NP-21's 60,260,352 B).
* AC-333 the build log line `native components: audio, camera, gfxdriver, gpu_compute, gpu_ui, hw, iptsd, nouveau,
  nvk, power, sensors`; `4 identical duplicate path(s) dropped`; `ESP built: ... free=` >= the `min_free`.
* AC-334 `gfx_build_id` == the A-07 expected line with the measured `pay=`.

**Tree diff and budgets (R-07)**

* AC-401 the old/new native tree difference equals 9.3 exactly (no unexpected path).
* AC-402 the ESP file differences equal 9.3's ESP list.
* AC-403 ESP used/free and `initrd` size inside 9.2's limits; the free margin >= `native_min_free`.
* AC-404 the native cpio size recorded (projected about 72 MB).

**VMware (R-08..R-10)**: AC-501..AC-512 (VM-2), AC-601..AC-615 (VM-3), AC-701..AC-718 (VM-1), plus:

* AC-719 the builder was idle when suspended and resumed afterwards (`uptime` on the builder after R-11, no job lost).
* AC-720 during every check `vmrun -T ws list` showed only `lapis-boot.vmx` and `gbw2.vmx`; every guard run passed.
* AC-721 every throwaway file under `D:\gb-w2test\vm\` deleted after its check; adb disconnected; the adb server kept.

**ISO (R-12..R-15)**

* AC-800 R-12 (a): a dry run that reuses the k13 export stops at export cross-check 8 (stale export refused).
* AC-801 R-12 (b): `build_iso.py --dry-run --new-export` prints `DRY RUN OK` with the new image (its verify_image
  preflight ok).
* AC-802 the build finished; `export_check.json` has `1_manifest` .. `6_guid_overlap_info` and `8_esp_tree` `ok`.
* AC-803 R-13 verify_image `--esp` on the export ESP `RESULT: OK` (V2 with the export super GUID, `gb.boot=internal`).
* AC-804 R-13 `--compare-export` `RESULT: OK` (USB vs export ESP).
* AC-805 ISO size <= 8 GiB and recorded; `.sha256` == recomputed; read-only attribute set.
* AC-806 the old export is in `D:\GB-iso\prev\export-20261025T042105\` with its `EFI-SYSTEM.img` sha `6c6ca051...`.
* AC-807 `run_all.py --preflight` passes after the swap (the build record, ISO, manifest and export agree).
* AC-808 T0 PASS (incl. "no denylisted .ko in the initrd" with the new list).
* AC-809 T1a PASS (sha == the new `USB_SHA`; verify_image `RESULT: OK` on the written disk; the control run ok).
* AC-810 T1b PASS.
* AC-811 T3-build, T3n(f), T3n(e), T3 (criterion 6 with the B-08 lines), T3n(d), T4, T4b, T9 PASS.
* AC-812 T7 and T2e PASS; `run_all.py` exit 0.

**Safety invariants and rules (R-16)**

* AC-901 `lapis.bin` was never opened for writing and `lapis-boot.vmx` never passed to any tool (logs); the user's VM
  ran throughout.
* AC-902 no `PhysicalDrive`, `diskpart`, `bcdedit`, `mountvol` anywhere in the run logs; every VM disk was a file under
  `D:\gb-w2test\` or `D:\GB-iso\test\`.
* AC-903 `find /build/dist -newer /build/kbuild13.done` = 0 files; `/build/dist14` present and
  `find /build/dist14 -newer /build/k14/accept/summary.txt` = 0 files.
* AC-904 C: and D: never below 10 GB, builder `/` and `/build` never below 5 GB (free-space log at every step).
* AC-905 invariant 1: no `ro.hardware.vulkan`/`ro.hardware.egl`, nouveau in no load list, the nvtest entry is the
  only NVIDIA entry and never the default (V2, V5, V9).
* AC-906 invariant 13/17/19: no priv-app (V10); renderD rules are runtime (HW); the VM tree carries no dGPU stack
  (V11 on every `--vm-test` image; the VM image itself untouched).
* AC-907 invariant 11: every ESP writer uses the 5.4 lock and `flush` (TA-3, TB-1, TB-2).
* AC-908 invariant 16: TU117 firmware ships only inside the payload with `WHENCE.tu117` and `LICENCE.nvidia` (V7).
* AC-909 decision D1: no build-time graphics default (V9); decision D2: `GB_PANIC_TIMEOUT=30` (V21).
* AC-910 nothing was published (no `git push`, no `gh`); downloads: none.
* AC-911 the `prev` copies were deleted only after AC-001..AC-910 passed and section 13 was answered.

---

## 11. Rollback

| What | Rollback | Kept where |
|---|---|---|
| Kernel (A-03) | `python -I -B C:\GB-x86\stage_kernel.py k13r --dist /build/dist` (restages k13 from the untouched dist), or copy `archive\module-backups\pre-k14\**` back | builder `/build/dist`; archive |
| Module backups (A-01) | copy back from `archive\module-backups\pre-k1x\` (never needed: they were never used) | archive |
| Any edited shared file | copy its `archive\pre-w2\...` version back | archive |
| gb_slotsync binary | `archive\pre-w2\GB-x86\inject\slotsync\gb_slotsync` | archive |
| Payload layout / graphics package | restore the pre-Wave-2 `build_esp.py` + manifests from `archive\pre-w2\` (there is no layout knob); `GB_GFXDRIVER=0` drops only the graphics package | archive |
| USB image (before R-17) | move `C:\GB-x86\out\prev\*` back; restore `common.py`/`build_iso.py` constants from the archive | `out\prev\` |
| ISO (before R-17) | move `D:\GB-iso\prev\*` back; restore `C:\GB-x86\iso\work\export\` from `D:\GB-iso\prev\export-20261025T042105\`; the builder's `last` then holds the new ISO: copy the old ISO back to `/var/tmp/gbiso/last/` before any ISO test of the old one (TEST builds media from `last`) | `D:\GB-iso\prev\` |
| After R-17 | rebuild from the archived inputs (new GUIDs, functionally the same image) | archive |
| Device (hardware, later) | boot menu `e`, append `gb.nouveau=0` (kill switch); `adb shell setprop persist.vendor.gb.gfx_mode intel`; the crash-loop guard forces Intel; reflash | docs (deferred) |
| ESP-only update of a USB stick | not supported (the entry names its own super GUID) | |

---

## 12. Deferred and out of scope (no Set A/B file)

* **Docs and repository** (W2-14, W2G-08): `docs\NVIDIA.md` and the other docs, the repo port (ports, not copies),
  `.gitignore` additions and the PII/binary audit of WAVE2_PLAN 10. A separate task after R-18; nothing is
  published.
* **INTERFACES errata** for the spec owner: gb_audiodiag in the 5.4 holder table (owner `gb_audiodiag`, 600 s, give
  up); 2.1 krel source = setup header (C-08); 5.3 `nvk_probe` size (64,984 B now).
* **W2-12 / W2G-12 ueventd** (optional hardening): not in this wave.
* **Hardware hand-off (R-18)**: H-1B-01..H-1B-13 (TASKS_1B 7), H-R-1..H-R-5 (TASKS_1B 12.5), K-HW (k14-kernel 4),
  NP/GP/GD/GC/UI hardware rows (INTERFACES 18 and the integration files), plus AC-715's refusal path, GC-11 on
  hardware, and `df /odm` for 9.2. Nothing in Wave 2 runs on the GTX 1650.

---

## 13. Open question for the user (asked at R-17, before deleting `D:\GB-iso\prev\`)

The new ISO carries a fresh dual-boot export (new GUIDs, DESIGN 1.5). Has any computer a dual-boot install made from
the current ISO (`1718bcc1...`, export created 2026-10-25T04:21:05)? If yes, keep that ISO (it is the tool that
removes such an install; otherwise `uninstall_dualboot.ps1 -Force`) instead of deleting it; if no, delete it with the
other `prev` copies. Everything else in this plan is decided (0.2).

---

## Appendix A: gb_bootlog script (A-09; the content of `GB_BOOTLOG_SH`)

```sh
#!/system/bin/sh
# gb_bootlog (native): kernel + Android logs to <boot partition>\gblog\boot<N>-t<T>.txt at T = 40, 90, 180, 420, 900 s.
# Each snapshot is collected in /dev first; the ESP is locked (INTERFACES 5.4) and mounted rw,flush only for the copy.
# Retention: the newest 8 boots of boot*/audio-b* files; nothing is written while the ESP has less than 128 MiB free.
# The dgpu section reads cached sysfs attributes and node metadata only (PLAN invariant 12) and sends only the
# read-only socket commands hello, status and get-mode. GB_BOOTLOG_* overrides exist for the test harness only.
P=EFI-SYSTEM
for a in $(cat /proc/cmdline); do case $a in gb.logpart=*) P=${a#gb.logpart=};; esac; done
D=/dev/block/by-name/$P; M=${GB_BOOTLOG_MNT:-/mnt/gb_esp}; L=${GB_BOOTLOG_LOCK:-/dev/gb_esp.lock}
T=${GB_BOOTLOG_TMP:-/dev/gb_bootlog.txt}; G=/data/vendor/gb; N=; OWN=; KEEP=8; FLOOR_KB=131072
mkdir -p "$M"
#@@functions-begin
steal() {   # $1 = owner line seen, $2 = inode seen (gb_nouveau.sh steal_lock)
  s=$L.stale.$$; rm -rf "$s" 2>/dev/null
  mv -T "$L" "$s" 2>/dev/null || return 1
  o2=; [ -r "$s/owner" ] && IFS= read -r o2 < "$s/owner"
  if [ "$(stat -c %i "$s" 2>/dev/null)" = "$2" ] && [ "$o2" = "$1" ]; then rm -f "$s/owner"; rmdir "$s" 2>/dev/null; return 0; fi
  mv -T "$s" "$L" 2>/dev/null || echo "gb_bootlog: ESP lock changed while stealing it, left as ${s##*/}" > /dev/kmsg
  return 1
}
lock() {    # 1 s polls; stale = dead or non-numeric owner, or 5 polls without an owner file; after 600 s: no lock
  w=0; miss=0; OWN=
  while ! mkdir "$L" 2>/dev/null; do
    i=$(stat -c %i "$L" 2>/dev/null); ol=; [ -r "$L/owner" ] && IFS= read -r ol < "$L/owner"; o=${ol%% *}; st=0
    if [ -n "$ol" ]; then miss=0; case $o in ''|*[!0-9]*) st=1;; *) [ -d "/proc/$o" ] || st=1;; esac
    else miss=$((miss+1)); [ $miss -ge 5 ] && st=1; fi
    if [ $st = 1 ] && [ -n "$i" ]; then miss=0; steal "$ol" "$i" && continue; fi
    if [ $w -ge 600 ]; then echo "gb_bootlog: ESP lock held over 600 s ($ol), proceeding without it" > /dev/kmsg; return 0; fi
    sleep 1; w=$((w+1))
  done
  OWN="$$ gb_bootlog $(cut -d. -f1 /proc/uptime)"; echo "$OWN" > "$L/owner"
}
unlock() {  # only our own lock (a lock stolen from us belongs to someone else now)
  [ -n "$OWN" ] || return 0
  ol=; [ -r "$L/owner" ] && IFS= read -r ol < "$L/owner"
  [ "$ol" = "$OWN" ] && { rm -f "$L/owner"; rmdir "$L" 2>/dev/null; }
  OWN=
}
prune() {   # once per boot, right after N is known
  for f in "$M"/gblog/boot*-t*.txt "$M"/gblog/audio-b*; do
    [ -f "$f" ] || continue
    b=${f##*/}; b=${b#boot}; b=${b#audio-b}; b=${b%%[!0-9]*}
    case $b in ''|*[!0-9]*) continue;; esac
    [ "$b" -le $((N-KEEP)) ] && rm -f "$f"
  done
}
rdf() { v=-; [ -r "$1" ] && v=$(head -c 600 "$1"); echo "$v"; }
snapshot() {
  echo "== uptime $(cat /proc/uptime) boot_completed=$(getprop sys.boot_completed) hw=$(cat /odm/etc/gb_hw)"
  echo '== cmdline'; cat /proc/cmdline
  echo '== gbinit module loading'; cat /odm/gbinit_modules.log
  echo '== dmi'; cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name
  echo '== block'; ls -l /sys/block; ls -l /dev/block/by-name
  echo '== input devices'; cat /proc/bus/input/devices
  echo '== iio'; for i in /sys/bus/iio/devices/*/name; do echo "$i $(cat "$i")"; done
  echo '== asound'; cat /proc/asound/cards /proc/asound/pcm; timeout 10 tinymix -D 0 2>&1 | head -n 80
  echo '== gpu'; timeout 20 dumpsys gpu 2>&1 | head -n 60; timeout 20 cmd gpu vkjson 2>&1 | head -c 4000
  echo '== modules'; cat /proc/modules
  echo '== drm/input/power'; ls -l /dev/dri /dev/input; for u in /sys/class/power_supply/*/uevent; do echo "$u"; cat "$u"; done
  echo '== pci'; for d in /sys/bus/pci/devices/*; do echo "$d $(cat "$d/vendor"):$(cat "$d/device") driver=$(basename "$(readlink "$d/driver")" 2>/dev/null)"; done
  echo '== getprop'; getprop
  echo '== dgpu'
  for d in /sys/bus/pci/devices/*; do
    v=$(cat "$d/vendor")
    [ "$v" = 0x10de ] || [ "${d##*/}" = 0000:00:1d.4 ] || continue
    echo "${d##*/} $v:$(cat "$d/device") class=$(cat "$d/class") driver=$(basename "$(readlink "$d/driver")" 2>/dev/null)" \
         "control=$(cat "$d/power/control") runtime=$(cat "$d/power/runtime_status") state=$(cat "$d/power_state")" \
         "active_ms=$(cat "$d/power/runtime_active_time") suspended_ms=$(cat "$d/power/runtime_suspended_time")" \
         "iommu_group=$(ls "$d/iommu_group/devices" 2>/dev/null | tr '\n' ' ')"
  done
  echo "nouveau refcnt=$(cat /sys/module/nouveau/refcnt 2>/dev/null || echo -)"
  for r in /sys/class/drm/card[0-9]* /sys/class/drm/renderD*; do
    [ -e "$r/device/vendor" ] && echo "${r##*/} -> $(cat "$r/device/vendor") $(basename "$(readlink "$r/device/driver")") $(stat -c '%a %U:%G' "/dev/dri/${r##*/}" 2>/dev/null)"
  done
  echo '-- gb_dgpu_check'; timeout 30 sh /vendor/bin/gb_dgpu_check.sh 2>&1 | head -n 150
  echo '-- properties'; getprop | grep -E 'vendor\.gb\.|persist\.vendor\.gb\.|init\.svc\.gb_|ro\.gfx\.driver|ro\.hardware\.(vulkan|egl)|hwui'
  echo '-- gb_power (read-only: hello, status, get-mode)'
  printf 'hello\nstatus\nget-mode\n' | timeout 5 toybox nc -q 1 -U /dev/socket/gb_power 2>&1 | head -c 8192; echo
  echo '-- settings'
  for k in all_apps production_opt_in_apps production_opt_out_apps production_allowlist production_denylist; do
    echo "updatable_driver_$k=$(timeout 10 settings get global updatable_driver_$k 2>&1 | head -c 600)"
  done
  for p in gb.sb3.gpu gb.sb3.gfxdriver; do echo "pkg $p:"; timeout 10 dumpsys package $p 2>&1 | grep -E 'codePath|pkgFlags|privateFlags|versionCode' | head -n 8; done
  echo '-- state'; ls -la /dev/gb_dgpu "$L" $G/notify 2>&1
  for s in nouveau.clean_boots gfx_unclean dgpu_unclean selftest/result; do echo "$s: $(rdf $G/$s)"; done
  for s in gfx_session dgpu_session nouveau.loading selftest/running; do
    if [ -e $G/$s ]; then echo "$s: present $(rdf $G/$s)"; else echo "$s: absent"; fi
  done
  grep -E ' /odm/dgpu | /odm/firmware/nvidia | /mnt/gb_' /proc/mounts
  dmesg | grep -iE 'nouveau|gsp|pciehp|_OSC|sdtx|surface_hotplug|acpiphp|firmware|gb_nouveau|gb_power|gb_notify|gb_slotsync|gb_audiodiag|runtime PM through ACPI _PR3|no longer present on the bus|runtime suspend failed|failed to evict VRAM|Do not have permissions to set'
  logcat -d -s gb_notify gb_gpu_ui_setup gb_gpu_compute gb_gfxdriver GbGpu 2>/dev/null | tail -n 60
  getenforce
  for k in dgpu load bind test selftest; do
    f=$(ls -t $G/report/$k-*.txt 2>/dev/null | head -n 1); [ -n "$f" ] && { echo "== $f"; cat "$f"; }
  done
  echo '== dmesg-live (tail)'; tail -c 262144 $G/report/dmesg-live.txt 2>/dev/null
  echo '== dmesg-live.prev (tail)'; tail -c 262144 $G/report/dmesg-live.prev.txt 2>/dev/null
  echo '== gb_nouveau.log (tail)'; tail -c 32768 $G/report/gb_nouveau.log.1 2>/dev/null; tail -c 65536 $G/report/gb_nouveau.log 2>/dev/null
  echo '== dmesg'; dmesg
  echo '== logcat'; logcat -d -b all -v threadtime | tail -n 6000
  echo '== dumpsys SurfaceFlinger'; timeout 20 dumpsys SurfaceFlinger | head -n 120
  echo '== dumpsys input'; timeout 20 dumpsys input | head -n 200
}
#@@functions-end
for t in 40 90 180 420 900; do
  while [ "$(cut -d. -f1 /proc/uptime)" -lt $t ]; do sleep 5; done
  [ $t = 40 ] && setprop vendor.gb.dgpu_report 1
  snapshot > "$T" 2>&1
  lock
  if ! mount -t vfat -o rw,flush,nosuid,nodev,noexec "$D" "$M"; then
    unlock; rm -f "$T"; echo "gb_bootlog: cannot mount $D, t=$t skipped" > /dev/kmsg; continue
  fi
  mkdir -p "$M/gblog"
  if [ -z "$N" ]; then
    c=$(cat "$M/gblog/bootcount" 2>/dev/null); case $c in ''|*[!0-9]*) c=0;; esac
    N=$((c+1)); echo $N > "$M/gblog/bootcount"; prune
  fi
  # shellcheck disable=SC2046
  set -- $(df -k "$M" 2>/dev/null | tail -n 1); fr=$4
  case $fr in ''|*[!0-9]*) fr=;; esac
  if [ -n "$fr" ] && [ "$fr" -lt $FLOOR_KB ]; then
    echo "gb_bootlog: ESP free ${fr} KiB below the 128 MiB floor, t=$t not written" > /dev/kmsg
  else
    cp "$T" "$M/gblog/boot$N-t$t.txt"
  fi
  sync; umount "$M"; unlock; rm -f "$T"
done
```

Notes: `$G/$s` and the fixed device paths contain no spaces; `ls -t` runs on fixed names. The two-tier VM-1 evidence
(AC-706, AC-711) comes from `gb_dgpu_check` and the report copy, which do not depend on `sys.boot_completed`.

## Appendix B: throwaway VM files

`D:\gb-w2test\vm\gbw2.vmx`:

```
.encoding = "windows-1252"
config.version = "8"
virtualHW.version = "21"
displayName = "gb-w2test (throwaway)"
guestOS = "other6xlinux-64"
firmware = "efi"
uefi.secureBoot.enabled = "FALSE"
memSize = "4096"
numvcpus = "2"
uuid.action = "create"
rtc.diffFromUTC = "0"
nvme0.present = "TRUE"
nvme0:0.present = "TRUE"
nvme0:0.fileName = "disk.vmdk"
nvme0:0.mode = "independent-persistent"
serial0.present = "TRUE"
serial0.fileType = "file"
serial0.fileName = "serial0.log"
svga.present = "TRUE"
usb.present = "FALSE"
usb_xhci.present = "FALSE"
ehci.present = "FALSE"
sound.present = "FALSE"
floppy0.present = "FALSE"
ethernet0.present = "FALSE"
```

VM-2/VM-3 replace the last line with `ethernet0.present = "TRUE"`, `ethernet0.connectionType = "nat"`,
`ethernet0.virtualDev = "vmxnet3"`, `ethernet0.addressType = "generated"`.

`D:\gb-w2test\vm\disk.vmdk` (sectors = the image size / 512; 56,279,073 for the USB image):

```
# Disk DescriptorFile
version=1
CID=fffffffe
parentCID=ffffffff
createType="monolithicFlat"

RW 56279073 FLAT "disk.img" 0

ddb.adapterType = "lsilogic"
ddb.virtualHWVersion = "21"
```

## Appendix C: traceability

| Item | v2 |
|---|---|
| W2-00 pre-flight / half-integration | R-00 (AC-001), A-12 atomic, V8b |
| W2-01 manifests + loader | A-05, A-12 |
| W2-02 gb_bootlog | A-09, Appendix A |
| W2-03 nvtest entries (gfxtest part retired) | A-10, A-13, B-04, B-08, V2 |
| W2-04 gb_slotsync | B-01 (nvtest only, W2G-04) |
| W2-05 HDA function | B-02 item 2 |
| W2-06 installer deny | B-05, B-06 |
| W2-07 verify_image | B-03 |
| W2-08 lazy payload | A-06 |
| W2-09 k14 staging | A-02, A-03 |
| W2-10 `/odm/app` | A-08, V10, V15, VM-3 |
| W2-11 nvk probe rc | A-12 (`gb_nvk.rc`), V8 |
| W2-12 ueventd | deferred (12) |
| W2-13 cpio hygiene | A-01, A-04, A-08, V6 |
| W2-14 docs | deferred (12) |
| W2-15 SELinux | no change (AC-012) |
| W2-16 tests | VM-1..VM-3, R-15; NP-25/GP-24 -> VM-1, K-10 -> VM-2, UI-07/VM-1B-05/GD-05 -> VM-3, GC-11 -> VM-1 (partial) + HW, VM-1B-01 -> V11, VM-1B-02/03 -> VM-2, VM-1B-04/06 -> VM-1 |
| W2G-01 gfxdriver.json | A-12 |
| W2G-02 gfx_build_id | A-07, V20 |
| W2G-03 no gfxtest, HWUI asserts, no default | A-05, A-10, V2, V9 |
| W2G-04 slotsync nvtest only | B-01 |
| W2G-05 gpu_ui.json seed | A-12, V22 |
| W2G-06 verify_image | B-03 (V9, V15, V20, V8, V23, V11) |
| W2G-07 + r2 bootlog | A-09 |
| W2G-08 docs | deferred |
| W2G-09 SELinux | dropped (r2) |
| W2G-10 v1 corrections | section 2.1 |
| W2G-11 pre-flight shas | 1.1, AC-001 |
| W2G-12 ueventd | deferred |
| WAVE2_PLAN E-01..E-05 | A-01, A-02, A-13, B-03 (E-01's rollback ISO replaced by the `prev` swaps) |
| E-06..E-14, E-20..E-25 | A-04..A-12, B-04; E-25 no change |
| E-30..E-36 | B-05..B-10 |
| R-00..R-15 (v1) | R-00..R-18 (v2) |
