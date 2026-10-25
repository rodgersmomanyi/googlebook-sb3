# Wave 2 integration notes: graphics switching (INTERFACES v1.2, wave 1b)

Date 2026-10-25. Written with INTERFACES.md v1.2 (sections 22-28) and `1b\TASKS_1B.md` v1.2. This file lists the
edits to **shared** files that v1.2 needs and that no wave 1b component may make itself (I 1.2). It replaces
`1b\TASKS_1B-rev1.md` section 6 (W2B-01..08) and answers `1b\WAVE2_PLAN.md`'s 1b questions; the component-side
artifacts and their sha256 will be in the "v1.2" sections of `nouveau-policy.md`, `gb-power.md`, `nvk-gfxdriver.md` and
`ux-app.md` once the 1b implementation is done. Until then nothing here may be applied (the wave-1 artifacts do not
implement v1.2: for example the v1.1 `gb_nouveau.sh` still reads `persist.vendor.gb.nouveau`).

Precedence (REVIEW D3): REQ and the user's decisions D1-D8 (I 22.1) win on intent; INTERFACES v1.2 wins on mechanism.

## W2G-01 `C:\GB-x86\inject\native\components\gfxdriver.json` (new; W2-01)

Copy `C:\GB-build\native\gfx-driver-pkg\out\gfxdriver.json` (content: `1b\TASKS_1B.md` 3.3): the APK only, no rc, no
`gb_gfxdriver.sh`, `"build_prop": ["ro.gfx.driver.0=gb.sb3.gfxdriver"]`. It is a normal native component: the
"only with `GB_GFXDRIVER=1`" rule (`README.md` W2-01, `nvk-gfxdriver.md` 2.2) is dropped. `GB_GFXDRIVER=0` may stay as
a build opt-out for bisecting; such an image then also omits `gfx_build_id` (W2G-02), so `gfx_cap` = 0 and app
graphics stay off while nouveau still auto-loads for compute.

The manifest loader already appends `build_prop` lines (`build_esp.py:1268-1270`, REVIEW D2). `ro.gfx.driver.0` must
come from `/odm/etc/build.prop`: GpuService reads it once in its constructor (`a17 GpuService.java:91`) and init loads
the ODM build.prop before any service could set a ro property.

## W2G-02 `C:\GB-x86\build_esp.py`: `/odm/etc/gb/gfx_build_id` (new file in the native cpio)

One line, lower-case hex, mode 0644, native trees with the gfxdriver component only:

```
apk=<sha256 of the packed GbGfxDriver.apk> icd=<sha256 of the shipped vulkan.nouveau.so> nvk=<sha256 of the APK member lib/x86_64/libgb_nvk.so> probe=<sha256 of /vendor/bin/nvk_probe> pay=<first field of etc/gb/dgpu_payload.sha256, or legacy>
```

build_esp already holds all these bytes (the APK, the payload image or the legacy ICD, nvk_probe). Without this file
`gfx_cap` is 0 and NVIDIA app graphics never switch on (REVIEW D2). The line is the `gfx=` part of the self-test
identity (I 25.1).

## W2G-03 `build_esp.py`: no gfxtest entry; HWUI asserts

* No `loader/entries/gb-<x>-gfxtest.conf` and no `gb.gfxdriver=` anywhere (I 16). If the Wave 2 plan's
  `gfxtest_entry` manifest key (C-05, E-08) was implemented, it stays absent/false.
* Assert at build time that `GB_RC_NATIVE` sets no `debug.hwui.renderer` and that the native build.prop does not
  override `ro.hwui.use_vulkan` (I 14 guard; today `GB_RC_NATIVE` sets neither, the `skiavk` setprop at
  `build_esp.py:635` is in the VM rc).
* No build-time default mode: the user decided `auto` (D1). The 1B knob `ro.vendor.gb.gfx_default` is **not** written
  (supersedes `TASKS_1B-rev1.md` W2B-02 and REVIEW A6's interim `intel`).

## W2G-04 `C:\GB-build\mouse\gb_slotsync.c` (W2-04)

Copy only the `-nvtest` entry to the other slot (no `-gfxtest`).

## W2G-05 `C:\GB-x86\inject\native\components\gpu_ui.json` (W2-01)

Copy `C:\GB-build\native\gpu-ui\gpu_ui.json` of the 1b build: it adds
`{"src": "C:\\GB-build\\native\\gpu-ui\\gfx_app_prefs.default", "dest": "etc/gb/gfx_app_prefs.default", "mode": "0644"}`
(an empty file).

## W2G-06 `C:\GB-x86\verify_image.py` (W2-07)

Native trees:
1. `/odm/etc/build.prop` has exactly one `ro.gfx.driver.0=gb.sb3.gfxdriver`.
2. `/odm/app/GbGfxDriver/GbGfxDriver.apk` exists and has **no** member `assets/allowlist.txt` (I 24.5).
3. `/odm/etc/gb/gfx_build_id` exists, one line, and its `apk`, `icd`, `nvk`, `probe`, `pay` values equal the hashes of
   the bytes in the tree.
4. `gb.rc` contains `service gb_gfx_selftest /system/bin/sh /vendor/bin/gb_dgpu_report.sh selftest`,
   `mkdir /data/vendor/gb/selftest 0700 root root`, and the three triggers
   `on property:vendor.gb.nouveau.booted=1 && property:persist.vendor.gb.gfx_mode=auto|nvidia|intel`; it contains no
   `property:persist.vendor.gb.nouveau=` trigger and no `gb_gfxdriver_setup`.
5. No `/vendor/bin/gb_gfxdriver.sh`; no `/odm/priv-app` (unchanged rule).

VM trees: none of `GbGfxDriver`, `gb_gfx_selftest`, `gfx_build_id`, `gfx_app_prefs.default`, `ro.gfx.driver.`
(PLAN inv. 19; TASKS VM-1B-01).

## W2G-07 gb_bootlog (`build_esp.py` `GB_BOOTLOG_SH`, W2-02)

The report copy loop adds `report/selftest-*.txt`; the bootlog also prints `/data/vendor/gb/selftest/result` (root
0644 in a 0700 directory; bootlog runs as root) and the `get-mode` reply next to `hello`/`status`. The existing
`getprop | grep vendor.gb.` line already covers the new properties.

## W2G-08 Repository docs (`docs\NVIDIA.md`, W2-14)

Graphics modes (Auto / Intel only / NVIDIA), the "Graphics" tile and Settings page, the app policy (games + Use NVIDIA,
or all apps; Always Intel), the self-test and "Run self-test again", the detach flows (refusal with "Close apps and
detach"), the notifications N16-N20, recovery (boot editor `gb.nouveau=0`; `adb shell setprop
persist.vendor.gb.gfx_mode intel`), and that Developer options > Graphics Driver Preferences is overwritten by gb_power
(I 24.8).

## W2G-09 SELinux (W2-15)

No new type. Open question for the hardware run (H-1B-12): apps with an old targetSdk run in `untrusted_app_25/27/29/
30/32`, which `inject\odm_sepolicy.cil` does not make permissive; there the shim may be denied the property read or
the node open (it then stays on ANV). Decide after H-1B-12 whether to add `typepermissive` lines. **(r2: wrong premise, dropped; those domains are
permissive already, see the r2 section below.)**

## W2G-10 Corrections to `1b\WAVE2_PLAN.md` (REVIEW D1-D6)

* C-03 (driver package always on): as W2G-01. C-04 (0666 mechanism): gb_power's in-memory derivation, no flag file
  (I 23.5, D48). C-05 (gfxtest entry): retired (W2G-03). C-21 (lost re-enable): gb_power's deferred start (I 23.7).
  Q-1b-1..5: answered by I v1.2 (sections 16, 23.5, 25.1/25.3, 24.6/12.1, 23.7).
* V8 (`gb.rc` contents): drop `service gb_gfxdriver_setup`; add `service gb_gfx_selftest` and the `gfx_mode` triggers;
  check that the `persist.vendor.gb.nouveau` triggers are gone.
* V9: inverted as W2G-06 items 1-3.
* E-07 placeholder: `report/selftest-<N>.txt` and `selftest/result` (W2G-07).
* E-08: `GFXTEST_ENTRY` stays false. E-11: no `-gfxtest` copy.
* VMware criterion (W2 6.2 item 10): `updatable_driver_all_apps` MUST read `null` (not `0`): gb_power never writes
  Settings without DTX (D8, I 23.4).
* VM checks: map TASKS VM-1B-03 to the throwaway `--vm-test` image (R-03/R-04), VM-1B-04 to the R-13 gblog evidence;
  parts that need `sys.boot_completed` in VMware are HW.
* ESP lock users: with `auto` as the default, nouveau loads about a minute into every boot with the base, so the lock
  edits for `gb_audiodiag.sh` and gb_slotsync (W2 E-11, E-12, C-15) are **preconditions** for shipping, not hygiene.

## W2G-11 Pre-flight (W2-00/W2-01)

`power.json`, `nouveau.json`, `nvk.json`, `gpu_ui.json`, `gfxdriver.json` must point at the 1b artifacts whose sha256
the four component integration files list in their "v1.2" sections; the wave-1 shas in those files are stale for v1.2.

## r2 (review fix pass, INTERFACES 29): delta to W2G-01 .. W2G-11

The component files' r2 sections (`nouveau-policy.md` 8, `gb-power.md` 8, `nvk-gfxdriver.md` 8, `ux-app.md` 9) hold
the artifacts that supersede their v1.2 sections. For the shared files:

* **W2G-01** unchanged (`gfxdriver.json` `840d0666...`); the APK it names is now `15a22bfd...`.
* **W2G-02** `gfx_build_id`: `apk=15a22bfd19180b45943c68fd5fdae31129d5b94d748fb94152a3e980d20271cd`; `icd=625314e0...`,
  `nvk=6bb6dc80...`, `probe=3d53a7e9...` unchanged; `pay=` as before.
* **W2G-05** unchanged (`gpu_ui.json` `ef867551...`); the APK is now `d653940a...`.
* **W2G-06** unchanged (item 3 recomputes from the tree). Add to item 4: nothing (the rc text is unchanged in r2).
* **W2G-07 gb_bootlog**: also copy `report/bind-*.txt` (the bind path's record, `nouveau-policy.md` 7.3; it was not in
  W2G-07 yet) and print the small state files `/data/vendor/gb/gfx_unclean`, `dgpu_unclean` and the existence of
  `gfx_session`, `dgpu_session`, `nouveau.loading` (root-owned; bootlog runs as root). A `clean shutdown` line in
  `report/gb_nouveau.log` and `gb_power: shutdown (...)` in kmsg are the H-R-1/H-R-2 evidence.
* **W2G-09 SELinux: dropped.** `inject\odm_sepolicy.cil` (sha256 `8265b908...`, read only) already has
  `typepermissive` for `untrusted_app_25`, `_27`, `_29`, `_30`, `_32`, `_34` and their variants (lines 2372-2402);
  there is no permissive-set decision left. H-1B-12 becomes a plain functional check (I 17 r2, R-14). The wakelock
  writes of R-7 (`/sys/power/wake_lock`, 0660 radio:wakelock, written by root in `u:r:shell:s0`, which is permissive)
  need nothing either.
* **W2G-10** unchanged; the ESP lock edits for `gb_bootlog`, `gb_slotsync` and `gb_audiodiag.sh` stay preconditions.
* **W2G-11** pre-flight: the shas to point at are `gb_power` `0d6a0b28...`, `gb_nouveau.sh` `6a754d42...`,
  `gb_dgpu_report.sh` `ccf3ddfb...`, `gb_notify.sh` `a4bc8d6e...` (unchanged), `gb_nouveau.rc` `4a8c438a...`
  (unchanged), `gb_dgpu_check.sh` `045fe80d...` (unchanged), `nvk_probe` `3d53a7e9...` (unchanged), GbGfxDriver.apk
  `15a22bfd...`, GbGpu.apk `d653940a...`.
* **W2G-12 (new, optional): `/odm/etc/ueventd.rc`** (inject tree, native only): `/dev/dri/renderD129 0660 root
  graphics` and `/dev/dri/card1 0600 root root`, so a nouveau node never starts at ueventd's 0666 (INFERRED: ueventd
  parses `/odm/etc/ueventd.rc` after `/vendor` and `/system`, and the last matching rule wins; the VM's rule is
  `/dev/dri/* 0666 root graphics`). The numbering holds because the i915 card0/renderD128 guard of 7.2 runs before
  every nouveau load; `verify_image.py` may assert that no rc renames these nodes. gb_power already clamps a 0666
  nouveau node within one fast tick (R-5), so this is defense in depth, not a precondition.
* No other shared file changes for r2.
