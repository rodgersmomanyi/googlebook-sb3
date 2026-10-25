# dGPU (NVIDIA TU117) shared interface spec

Version 1.2, 2026-10-25. The source of truth for the six Wave 1 implementers and for the wave 1b (graphics
switching) implementers. It is derived from `NVIDIA_PLAN.md` (sections 1-13, same scratchpad `nvresearch/` folder) and,
for v1.2, from `GRAPHICS_SWITCHING_REQ.md` (same folder, "REQ") and the user's decisions D1-D8 of 2026-10-25 (quoted in
section 22.1). Where it differs from the plan, the reason is in section 19 (`D-n` numbers are cited inline). If this
spec and the plan disagree, **this spec wins**. If this spec is silent or self-contradictory, stop and record the
question in your `integration/<component>.md` under "Spec questions" instead of guessing an interface.

v1.1 is the post-review revision (two Wave 1 reviews: interface consistency and safety). Every change is listed in
section 21 with the review finding it answers; changed text is otherwise integrated in place. All six components
implement v1.1.

**v1.2** = v1.1 + Intel/NVIDIA graphics switching ("wave 1b"). It supersedes `1b\INTERFACES_1B.md` (rev 1b-1, written
against v1), corrected against the v1.1 code (`power\gb_power.c` sha256 `25bddd1f...`, `nouveau-kernel\native\
gb_nouveau.sh` `aebf9189...`, `gb_dgpu_report.sh` `8c5f0b2a...`, `gb_notify.sh` `a1f465a9...`, `gb_nouveau.rc`
`633e8e91...`, `gfx-driver-pkg\shim\gb_shim.c` `ef329edd...`, gpu-ui as of 2026-10-25) and the review
`1b\REVIEW.md`. The new behaviour is in sections 22-27; v1.1 text that v1.2 changes is edited in place and marked
"(v1.2)"; every change is listed in section 28 (`G-n`). The v1.1 text is kept byte for byte in
`C:\GB-build\native\dgpu\INTERFACES-v1.1.md` (sha256 `4ab73edc...`). The wave 1b work split, rebased tasks and
acceptance tests are in `1b\TASKS_1B.md` v1.2. Owners: nouveau-policy (1), gb-power (2), nvk-gfxdriver for
`gfx-driver-pkg\` (4), gpu-ui (6); k14 (3) and gpu-compute (5) have no v1.2 work.

Words: **MUST** / **MUST NOT** are binding; **SHOULD** means "do it unless you write down why not". `INFERRED` means
reasoned from source, not observed. `HW` marks a check that needs the real Surface Book 3 + base (nobody in Wave 1 can
run it). Paths are on the device unless prefixed `C:\` (Windows host) or `builder:` (Ubuntu builder VM).

---

## 0. Quick reference

| Item | Value |
|---|---|
| Kill switch (boot editor) | append `gb.nouveau=0` (v1.2: also pure-Intel app graphics for that boot) |
| Test boot entry | `loader/entries/gb-<x>-nvtest.conf`, options += `gb.nouveau=test` |
| Per-app graphics experiment entry (P8 only) | **retired in v1.2** (no `gb-<x>-gfxtest.conf`, no `gb.gfxdriver=`) |
| Persistent opt-in | v1.1 `persist.vendor.gb.nouveau=1`: **retired as an input in v1.2**; see "Graphics mode" |
| Graphics mode (v1.2, user setting) | `persist.vendor.gb.gfx_mode` = `auto` (unset = auto) \| `intel` \| `nvidia` |
| App policy (v1.2, user setting) | `persist.vendor.gb.gfx_apps` = `games` (unset = games) \| `all`; per-app choices in `/data/vendor/gb/gfx_app_prefs` |
| Effective app graphics (v1.2, single writer gb_power) | `vendor.gb.gfx_active` = `intel` \| `nvidia`, reason `vendor.gb.gfx_reason` |
| App driver selection (v1.2) | Settings.Global `updatable_driver_all_apps` `0`/`3` + `..._production_opt_in_apps` (computed) + `..._production_opt_out_apps` (Always Intel); written only by gb_notify from gb_power's queue (section 24) |
| Self-test (v1.2) | record `/data/vendor/gb/selftest/result` (truth), mirror `persist.vendor.gb.gfx_ok`, service `gb_gfx_selftest` |
| Live state (single writer gb_power) | `vendor.gb.dgpu.state` |
| Policy result (single writer gb_nouveau.sh) | `vendor.gb.nouveau.result` |
| Control socket | `/dev/socket/gb_power`, AF_UNIX stream, line request -> one-line JSON reply |
| Compute socket | `/dev/socket/gb_gpu_compute`, AF_UNIX stream, HTTP/1.1 (OpenAI-style subset) |
| Payload on ESP | `gb/dgpu-<krel>.erofs` + `gb/dgpu-<krel>.sha256` |
| Payload copy | `/data/vendor/gb/payload/dgpu.erofs` |
| Payload mount | `/odm/dgpu` (erofs, ro), bind `/odm/dgpu/firmware/nvidia` -> `/odm/firmware/nvidia` |
| k13 kernel release | `6.18.21-android17-5-g3938c8d97142-dirty-ab16302624` (verified, D10) |
| UX app | package `gb.sb3.gpu`, `/odm/app/GbGpu/GbGpu.apk`; v1.2: QS tile and Settings entry "Graphics" |
| Driver package (v1.2: every native image) | package `gb.sb3.gfxdriver`, `/odm/app/GbGfxDriver/GbGfxDriver.apk`, `ro.gfx.driver.0=gb.sb3.gfxdriver` in the native `/odm/etc/build.prop`; never in the VM image |
| Shim name | `vulkan.<ro.hardware.vulkan>.so` = `vulkan.intel.so` (native and VM) |
| Signing keys | `builder:~/keys/` (canonical), backup `C:\GB-build\keys\` |

---

## 1. Components, ownership, hand-off

### 1.1 Wave 1 components

| # | Id (integration file name) | Plan phases | Owns (may write) | Builder scratch |
|---|---|---|---|---|
| 1 | `nouveau-policy` | P1 items 1-4, 6 (entry text only), P0-6, P5 script side + payload tool | `C:\GB-build\native\nouveau-kernel\native\**`, `...\nouveau-kernel\test\**` (new), `...\nouveau-kernel\payload\**` (new), `...\nouveau-kernel\firmware\**`, `...\nouveau-kernel\README.md` | `builder:/build/native/dgpu/nouveau-policy/` |
| 2 | `gb-power` | P3, P1 item 7 (gb_power half), P6 stop hook, P7 control socket (server) | `C:\GB-build\native\power\**` | `builder:/build/native/dgpu/gb-power/` |
| 3 | `k14` | P4 | `C:\GB-build\kernel\kbuild14_native.sh`, `C:\GB-build\kernel\patches\k14\**`, `C:\GB-build\kernel\K14.md` | `builder:/build/k14/`, outputs `builder:/build/dist14/` (+ optional `/build/dist14-dyndbg/`) |
| 4 | `nvk-gfxdriver` | P0 items 1, 3, 4; P6 NVK build items; P8 patches, probe modes, shim, driver APK | `C:\GB-build\native\nvk-android\**`, `C:\GB-build\native\gfx-driver-pkg\**` (new) | existing `builder:/build/native/nvk-android/`, `builder:/build/mesa-android/`; new `builder:/build/native/dgpu/gfx-driver-pkg/` |
| 5 | `gpu-compute` | P6 | `C:\GB-build\native\gpu-compute\**` (new) | `builder:/build/native/dgpu/gpu-compute/` |
| 6 | `gpu-ui` | P7 (app side) | `C:\GB-build\native\gpu-ui\**` (new) | `builder:/build/native/dgpu/gpu-ui/` |

Every component also writes `C:\GB-build\native\dgpu\integration\<id>.md`: the exact edits Wave 2 must make to shared
files (diffs or precise instructions), its artifacts with sha256, the tests it ran with results, and its open "Spec
questions". Nobody edited this spec during the Wave 1 implementation; v1.1 was written by the single post-review fix
pass that owns all six components (section 21).

### 1.2 Shared files nobody touches in Wave 1

`C:\GB-x86\build_esp.py`, `build_usb.py`, `stage_kernel.py`, `verify_image.py`, `C:\GB-x86\inject\**` (including
`inject\native\components\*.json` and `inject\odm_sepolicy.cil`), `C:\GB-x86\iso\**`, `C:\GB-build\native\dualboot\**`,
`C:\GB-build\mouse\gb_slotsync.c`, `C:\GB-build\native\audio\**`, `builder:/build/dist/**`, `builder:/var/tmp/gbiso/**`,
the user's VM (`C:\GB-x86\lapis-boot.vmx`, `lapis.bin`), `C:\GB-x86\gbsh.py`. The Wave 2 checklist is
`C:\GB-build\native\dgpu\integration\README.md`.

### 1.3 Artifact hand-off

| Artifact | Producer | Path (Windows, after the component pulls it back) | Consumers |
|---|---|---|---|
| `gb_nouveau.sh`, `gb_nouveau.rc`, `gb_dgpu_report.sh`, `gb_dgpu_check.sh`, `gb_notify.sh` | 1 | `C:\GB-build\native\nouveau-kernel\native\` | Wave 2 (nouveau.json) |
| `make_dgpu_payload.sh` (+ `erofs_ls.py`, `test_make_dgpu_payload.sh`) | 1 | `C:\GB-build\native\nouveau-kernel\payload\` | Wave 2 (build_esp P5) |
| `firmware_staged_sha256.txt` (-> `/odm/etc/gb/tu117.sha256`), `WHENCE.tu117`, `LICENCE.nvidia` | 1 | `C:\GB-build\native\nouveau-kernel\` (sha list) and `...\firmware\` (WHENCE.tu117, LICENCE.nvidia; linux-firmware tag 20260916) | Wave 2, payload |
| `bin/gb_power` (static, stripped), `gb_power.rc` | 2 | `C:\GB-build\native\power\` | Wave 2 (power.json) |
| k14 bzImage + all modules + `kernel_release.txt` | 3 | stays on `builder:/build/dist14/`; `C:\GB-build\kernel\k14_release.txt` | Wave 2 (`stage_kernel.py --dist /build/dist14`) |
| `vulkan.nouveau.so` (payload ICD), `nvk_probe` | 4 | `C:\GB-build\native\nvk-android\bin\` (existing names) | 5 (compute), Wave 2 (nvk.json) |
| `GbGfxDriver.apk`, `gfxdriver.json` (v1.2: `gb_gfxdriver.sh` and `gb_gfxdriver.rc` are no longer shipped) | 4 | `C:\GB-build\native\gfx-driver-pkg\out\` | Wave 2: a normal native component in v1.2 (no `GB_GFXDRIVER=1` gate; `GB_GFXDRIVER=0` stays a build opt-out for bisecting) |
| `gfx_app_prefs.default` (v1.2, empty file) | 6 | `C:\GB-build\native\gpu-ui\` | Wave 2 (gpu_ui.json, `/odm/etc/gb/gfx_app_prefs.default`) |
| `/odm/etc/gb/gfx_build_id` (v1.2) | Wave 2 (build_esp) | written at image build from the bytes it packs | gb_power, `gb_dgpu_report.sh selftest` (25.1) |
| `gb_gpu_compute` (launcher), `lib64/gpu-compute/*.so`, `gb_gpu_compute.rc` | 5 | `C:\GB-build\native\gpu-compute\out\` | Wave 2 (gpu_compute.json) |
| `GbGpu.apk`, `gb_gpu_ui.rc`, `gb_gpu_ui_setup.sh` | 6 | `C:\GB-build\native\gpu-ui\out\` | Wave 2 (gpu_ui.json) |

Each artifact is listed with its sha256 in the producer's integration file.

### 1.4 Builder etiquette (all components)

* Heavy jobs: `nice -n 19 ionice -c3`. Before starting one: `pgrep -af gbiso` shows no running build and
  `cat /proc/loadavg` 1-min load < 2.
* Never touch `/var/tmp/gbiso` or its processes. Never modify or delete anything under `/build/dist`.
* Keep `/` and `/build` free space above 5 GB at all times (`df -BG / /build`); a Kleaf build needs 10 GB free on
  `/build` before it starts (component 3 aborts otherwise). Remove your own scratch when done.
* `/build/mesa-android/mesa` is a blobless partial clone: no `git log -S`, no `blame`.
* Python always with `-I`. Never print personal data. No `git push`, no `gh`.
* Allowed downloads only: Ubuntu archive packages; git from kernel.org, android.googlesource.com,
  gitlab.freedesktop.org (Mesa), github.com/ggml-org/llama.cpp (pinned release tag); Android SDK pieces from
  dl.google.com via `sdkmanager`. No proprietary NVIDIA binaries. No model weights from anywhere (section 18.5).
* Already present: `mksh`, `shellcheck`, `mkfs.erofs`/`fsck.erofs`, `openjdk-17-jdk-headless` (default `java` is 25),
  `apksigner`, `zipalign`, `aapt`, `aapt2`, `glslc`, `glslang-tools`, `spirv-tools`, `vulkan-tools`, `libvulkan-dev`,
  `mesa-vulkan-drivers` (lavapipe), `cmake`, `ninja`, NDK at `/build/ndk`. An SDK with build-tools (incl. `d8`) and
  `platforms/android-36/android.jar` exists read-only at `builder:/build/native/sensors/tools/sdk/` (dirs `android-16`
  = build-tools, `android-36` = platform); reuse it read-only, or install your own copy with `sdkmanager` under your
  scratch dir. Never modify the sensors copy.

---

## 2. Global constants

### 2.1 Kernel release (`krel`)

* `krel` is exactly what `uname -r` prints for the staged bzImage. It is also the first whitespace-separated token of
  `modinfo -F vermagic nouveau.ko` and the token after `Linux version ` in the bzImage banner.
* k13: `6.18.21-android17-5-g3938c8d97142-dirty-ab16302624` (builder `modinfo`, bzImage banner, 2026-10-25). The
  plan's short form `...g3938c8d97142` is not the real string (D10).
* k14: whatever its build produces. Component 3 writes it to `builder:/build/dist14/kernel_release.txt` (one line, no
  trailing spaces) and `C:\GB-build\kernel\k14_release.txt`. Nobody hard-codes a krel except tests that use a fake one.

### 2.2 Device discovery (no hard-coded BDFs in policy code)

* **dGPU function**: the first entry of `ls /sys/bus/pci/devices` (sorted) whose `vendor` is `0x10de` and whose
  `class` starts with `0x03` (VGA `0x0300xx` or 3D `0x0302xx`). On the SB3 this is `0000:02:00.0` (INFERRED from the
  existing scripts; recorded on HW in Phase 2).
* **Other NVIDIA functions** (e.g. HDA `0000:02:00.1`, class `0x0403xx`): vendor `0x10de`, class not `0x03*`. They
  MUST stay driverless in every mode.
* **Root port**: the parent directory of `realpath /sys/bus/pci/devices/<dgpu>` (basename is a BDF). gb_power caches it
  the first time it sees the dGPU in a boot and falls back to `0000:00:1d.4` when it never saw one (only used for
  rescan, section 10.4).
* **BAR check**: the first line of `/sys/bus/pci/devices/<dgpu>/resource` has a non-zero start address.
* **i915 check**: `/sys/class/drm/renderD128/device/vendor` and `/sys/class/drm/card0/device/vendor` are `0x8086`, and
  `basename $(readlink .../device/driver)` is `i915` for both.
* **nouveau nodes**: every `/sys/class/drm/{card,renderD}[0-9]*` whose `device/driver` basename is `nouveau`; the
  device node is `/dev/dri/<name>`.

### 2.3 Timing constants (all components use these numbers)

| Name | Value | Used by |
|---|---|---|
| ESP lock wait, loader | 120 s, then `refused:esp` | gb_nouveau.sh |
| ESP lock wait, log writers | 600 s, then proceed without the lock (bootlog: logs matter most; v1 said "steal", which would also break a live holder's lock) or skip this cycle (slotsync) | gb_bootlog, gb_slotsync (Wave 2) |
| Health window | 30 s after the last insmod returns | gb_nouveau.sh |
| Clean mark | 120 s after the last insmod returns | gb_nouveau.sh |
| Kernel panic timeout on opt-in boots | 30 s (`/proc/sys/kernel/panic`) | gb_nouveau.sh (pending user approval, section 20) |
| dmesg-live sync period / cap | 5 s / 16 MiB (then rotate to `.prev`) | gb_nouveau.sh dmesg-stream |
| `off`: GPU-user service stop wait / chmod-000 settle / unbind deadline | 5 s / 1 s / 20 s | gb_nouveau.sh |
| `auto`: wait for a detach in progress (`/dev/gb_dgpu/detaching`) | 15 s, then `refused:detach` | gb_nouveau.sh |
| Detach: refcnt poll / worker deadline per job (unbind job, remove job) / heartbeat period | 30 s / 20 s each / <= 2.0 s (invariant 7 allows 2.5 s) | gb_power |
| Detach: unbind job repeated for late re-binds | at most 3 times, then cancel | gb_power |
| Rescan: D0 wait / retries | 4 s / +2 s, +5 s, +15 s while base attached | gb_power |
| `power/control=on` write deadline (worker) | 10 s | gb_power |
| Watchdog: idle-D0 fault | D0 > 60 s with refcnt 0 and no compute hold | gb_power |
| nouveau autosuspend delay | 5000 ms default; 500 ms during PM stress only | gb_dgpu_report.sh |
| Status tick | 2 s while a nouveau driver is bound or a decision runs; 10 s otherwise; plus every uevent | gb_power |
| Holder scan rate limit | <= 1 per second | gb_power |
| Socket idle timeout (not for watch and compute-svc connections) / watch ping | 30 s / 30 s | gb_power |
| State broadcast coalescing | 2 s | gb_power |
| Compute idle exit / screen-off unload / client start wait | 120 s / 30 s off / 10 s | gb_gpu_compute, clients |
| Compute `compute-state` keep-alive (re-send of the current state) / reconnect retry | 15 s / 2 s | gb_gpu_compute |
| (v1.2) Self-test: per probe `timeout` / nvk_probe `-T` / ESP lock wait / gb_power deadline from service start / stop grace | 150 s / 60 s / 120 s / 360 s / 10 s | `gb_dgpu_report.sh selftest`, gb_power (25) |
| (v1.2) Rebind guard: wait for the dGPU after the trigger / health window after it appeared | 60 s / 30 s | gb_power (23.8) |
| (v1.2) Detach refusal when only closable app processes hold nouveau | 5 s (others: the 30 s refcnt wait) | gb_power (23.9) |
| (v1.2) Pending detach (`close-holders-and-detach`): refcnt-0 confirmation / deadline | 2 reads 500 ms apart / 15 s | gb_power (23.9) |
| (v1.2) `reconcile_off`: refcnt 0 on consecutive ticks / minimum gap between starts | 2 / 10 s | gb_power (23.6) |
| (v1.2) Hot attach starts | once per device appearance, 3 per boot | gb_power (23.7) |
| (v1.2) Unclean NVIDIA app-graphics sessions before Intel is forced | 2 in a row | gb_power (23.10) |
| (v1.2) `bind` write deadline in the bind path | 20 s (40 x 0.5 s) | gb_nouveau.sh (7.2a) |

### 2.4 Size limits

* System property values: <= 91 bytes (non-`ro.`); names: <= 31 characters (all names in section 4 comply).
* Control socket: request line <= 255 bytes including `\n`; reply line <= 4096 bytes including `\n`.
* Notification title <= 40 characters, text <= 200 characters, ASCII only.
* (v1.2) App lists: catalog <= 512 packages, per-app choices <= 128, computed opt-in list <= 512 packages and
  <= 64 KiB joined (section 24.4).

---

## 3. Kernel command line

Parsing rules for every consumer: split `/proc/cmdline` on whitespace; for a repeated key the **last occurrence wins**
(the systemd-boot editor appends); values are case-sensitive; an unknown value of a known key is logged.

| Token | Values | Effect | Consumers |
|---|---|---|---|
| `gb.nouveau=` | `0` | Kill switch for this boot: no nouveau load, no runtime enable, `mode=disabled`. Persistent settings are not changed. (v1.2) Also pure lapis app graphics for the boot: `gfx_active=intel` reason `kill`, gb_power queues `updatable_driver_all_apps=3` (23.4, review C6). | gb_nouveau.sh, gb_power (status, graphics) |
| | `once` | Load this boot, non-persistent; `off` requests are ignored this boot. (v1.2) The graphics mode still applies to app graphics; a mode change answers `next-boot`. | gb_nouveau.sh, gb_power |
| | `test` | As `once`, plus test extras (section 7.6) and the Phase 2B report sequence. (v1.2) App graphics stay Intel (reason `test`) and no self-test runs, so apps do not disturb the measurements (review C5). | gb_nouveau.sh, gb_dgpu_report.sh, gb_power |
| | anything else | Treated as `0` (fail safe, D17); logged `unknown gb.nouveau value`. | gb_nouveau.sh |
| `gb.nouveau.fw=` | `535` | Adds `NvGspFw=0` to the nouveau `config=` (forces the 535.113.01 GSP fwif; verified GKI `include/nvkm/core/firmware.h:53-58`, `nvkm/subdev/gsp/tu116.c:47-48`). | gb_nouveau.sh |
| | `570` | Accepted no-op (570.144 is the default). | gb_nouveau.sh |
| | other | Ignored, logged. | gb_nouveau.sh |
| `gb.gfxdriver=` | any | **(v1.2) Retired.** No component reads the token any more (`gb_gfxdriver.sh` is not shipped, the gfxtest entry is gone). The v1.1 text of this row follows for reference only. | none |
| | `1` (v1.1 only) | P8 experiment: at post-fs-data `gb_gfxdriver.sh` sets `ro.gfx.driver.0` and `vendor.gb.gfxdriver=on` and creates the root-only flag `/dev/gb_dgpu/gfxdriver`, but only if the driver APK exists. Nothing else reads the raw token. The P8 gate everywhere else is **`vendor.gb.gfxdriver=on` AND the flag file** (the property alone is untrusted, section 4): nouveau render node 0666 (gb_nouveau.sh and gb_power alike), status field `gfxdriver`. The shim keys on the property (it cannot read `/dev/gb_dgpu`; a spoofed property only lets that app try NVK, the node mode still refuses it). The `foreign` flag at load is set on the property OR the file (safe direction). Any other value = off. | gb_gfxdriver.sh (only reader of the token) |
| `module_blacklist=` | comma list | If **any** occurrence lists `nouveau`: same as `gb.nouveau=0` (and the kernel refuses the module anyway). Not last-wins, unlike the other keys (fail safe). | gb_nouveau.sh, gb_power (status) |
| `gb.hw=` | `native`/`vm` (existing) | Everything in this spec acts only when `/odm/etc/gb_hw` reads `native`; otherwise `refused:hw`. | all scripts |
| `gb.logpart=` | partition name (existing, default `EFI-SYSTEM`) | ESP block device = `/dev/block/by-name/<value>`. | lock users, gb_nouveau.sh stage |
| `gb.boot=` | `usb`/`internal` (existing) | `usb` selects the "running from a drive in the base" detach text. | gb_power |
| `gb.dgpu=keep` | existing | gb_power does no dGPU runtime-PM/unbind except at detach (unchanged). | gb_power |

---

## 4. System properties

All `vendor.gb.*` / `persist.vendor.gb.*` properties are **untrusted input**: SELinux is enforcing but `untrusted_app`
and `shell` are permissive, so any app can set them. Every reader validates values and treats unknown values as the
safe default. No component takes a destructive action because a property says so, except the explicit user settings
(v1.1: `persist.vendor.gb.nouveau`; v1.2: `persist.vendor.gb.gfx_mode` and `persist.vendor.gb.gfx_apps`) and
triggers whose handlers re-check real state. (v1.2) A spoofed user setting only selects a mode or an app policy the
user could pick in the GUI; every gate of section 23 still applies. Properties gb_power publishes (`vendor.gb.gfx_*`,
`persist.vendor.gb.gfx_ok`) are outputs only: no component reads them back as an input to a safety decision.

**Monotonic flags.** `used`, `foreign` and `wedged` are per-boot and only ever go from "no" to "yes". For `used` and
`foreign` a writer sets the property (`vendor.gb.nouveau.used` / `.foreign`) to `1` **and** creates the root-only file
`/dev/gb_dgpu/<flag>`; a reader treats the flag as set if the property is `1` **or** the file exists. `wedged` has no
property: it is the file `/dev/gb_dgpu/wedged` (plus gb_power's in-memory copy), and its public face is
`vendor.gb.dgpu.state=wedged`. Nobody writes `0` or deletes a flag file (D9).

| Name | Len | Values (default) | Writer(s) | Readers | Meaning |
|---|---|---|---|---|---|
| `persist.vendor.gb.nouveau` | 25 | v1.1: `1`, `0` (unset = `0`) | **(v1.2) nobody** | **(v1.2) nobody** | v1.1 persistent opt-in. **Retired in v1.2:** not read, not written, no rc trigger (replaced by `persist.vendor.gb.gfx_mode`). Wave 1 never shipped, so there is no migration (review E5). A value left on a test device is ignored. |
| `persist.vendor.gb.nouveau.args` | 30 | space-separated whitelist atoms (unset = none) | user (adb) | gb_nouveau.sh | Extra nouveau options, section 7.4. |
| `vendor.gb.nouveau.mode` | 22 | `default`, `disabled`, `once`, `test`, `auto` (v1.2; v1.1 `persist` is no longer produced) | gb_nouveau.sh | gb_power, report, bootlog | Policy mode this boot (latest auto run). v1.2: `auto` = loaded because `gfx_mode` is `auto` or `nvidia`; `default` = `gfx_mode` is `intel`. |
| `vendor.gb.nouveau.result` | 24 | section 7.8 | gb_nouveau.sh (only) | gb_power, UI via status, report | Outcome of the last policy action. Informational for gb_power (v1.2: never an input to a node-permission or graphics decision, review C4). |
| `vendor.gb.nouveau.loading` | 25 | `1`, `0` (unset = `0`) | gb_nouveau.sh | gb_bootlog, gb_slotsync, gb_power | Load window open (informational; mutual exclusion is the ESP lock, section 5.4). |
| `vendor.gb.nouveau.booted` | 24 | `1` (unset otherwise) | gb_nouveau.sh | init rc | Boot-time auto run finished; arms the runtime triggers (v1.2: the `gfx_mode` triggers, 15.1). Never set in kill-switch or crash-loop boots. (v1.2) Set only when it is not already `1`, so a refused run does not re-fire the triggers (review E2). |
| `vendor.gb.nouveau.used` | 22 | `1` | gb_nouveau.sh (when its load window opens, right after it took the ESP lock), gb_power (when it sees nouveau bound or loaded) | gb_power | Monotonic: a nouveau load window opened, or nouveau touched the dGPU, this boot (a detach then takes the checked slow path). |
| `vendor.gb.nouveau.foreign` | 25 | `1` | gb_nvk.rc (`nvk_probe` run), gb_dgpu_report.sh (before anb modes, and v1.2 before the self-test probes), v1.2: gb_power (before the first `gfx_active=nvidia` of the boot, 23.3); (v1.1 gb_nouveau.sh gfxdriver boots: retired) | gb_nouveau.sh (`off`), gb_power | Monotonic: nouveau buffers were shared with another device; `rmmod` forbidden this boot. |
| `vendor.gb.nouveau.argv` | 22 | final insmod args for nouveau.ko, e.g. `config=NvGspFw=0 debug=gsp=debug runpm=1 modeset=2` | gb_nouveau.sh | report, status | Effective arguments. |
| `vendor.gb.nouveau.clean` | 23 | decimal | gb_nouveau.sh | gb_power, UI | Mirror of `/data/vendor/gb/nouveau.clean_boots`. |
| `vendor.gb.dgpu.state` | 20 | section 8 | gb_power (only) | UI (via socket), report, bootlog | Live dGPU state. Unset = treat as `unavailable`. |
| `vendor.gb.dgpu.holders` | 22 | `comm:pid[,comm:pid...]`, truncated at an entry boundary with `,+N`; empty when none | gb_power (only) | report, bootlog | Holder summary. |
| `vendor.gb.dgpu.latch` | 20 | `locked`, `unlocked`, `unknown` | gb_power | report, status | Last LOCK/UNLOCK gb_power issued. |
| `vendor.gb.dgpu.payload` | 22 | `legacy`, `absent`, `ready`, `mounted`, `error:<step>` (section 6.5) | gb_nouveau.sh | gb_power, report | Payload status. |
| `vendor.gb.dgpu.busy` | 19 | `1`, `0` | gb_dgpu_report.sh (stress / suspend phases) | gb_bootlog, gb_power (status) | Stress phase running (informational; it also holds the ESP lock). |
| `vendor.gb.dgpu.dmesg` | 20 | `1` start, `0` stop | gb_nouveau.sh | init rc | Controls the `gb_dgpu_dmesg` service. |
| `vendor.gb.dgpu_report` | 21 | `1` read-only report, `2` test sequence; rc resets to `0` | gb_bootlog (t=40, Wave 2), gb_power (`report`), gb_nouveau.sh (`2` in test boots), user | init rc | Report trigger. `2` is refused by the script unless `mode=test`. |
| `vendor.gb.nvk_probe` | 19 | `1`; rc resets to `0` | user, gb_dgpu_report.sh | init rc | Existing probe trigger (component 4's `gb_nvk.rc`). |
| `vendor.gb.notify` | 16 | decimal sequence (epoch ms) | any queue writer (section 12) | init rc | Starts `gb_notify`. |
| `vendor.gb.compute.state` | 23 | `stopped`, `starting`, `idle`, `loading`, `loaded`, `serving`, `unloading` | gb_power (only; derived, section 11.6) | report, status | Compute service state. |
| `vendor.gb.gfxdriver` | 19 | v1.1: `on`, `off` | **(v1.2) nobody** | **(v1.2) nobody** | v1.1 P8 experiment flag. **Retired in v1.2:** the shim, gb_nouveau.sh and gb_power ignore it (the shim keys on `vendor.gb.gfx_active`, 14). |
| `persist.vendor.gb.gfx_mode` | 26 | `auto`, `intel`, `nvidia`; unset or any other value = `auto` (D1) | gb_power (`set-mode`, `enable` = auto, `disable` = intel, rebind failure and unclean NVIDIA sessions = intel), gb_nouveau.sh (`intel` on crash-loop, unhealthy load, killed insmod), user (adb) | gb_nouveau.sh (`gfx_mode()`), init rc (15.1), gb_power | (v1.2) The user's graphics mode (22.2). |
| `persist.vendor.gb.gfx_apps` | 26 | `games`, `all`; unset or other = `games` (D3) | gb_power (`set-app-policy`), user (adb) | gb_power | (v1.2) Which apps render on NVIDIA in auto/nvidia: games + the apps marked "Use NVIDIA", or every launchable non-system app (24.3). |
| `vendor.gb.gfx_active` | 20 | `intel`, `nvidia`; unset = `intel` | gb_power (only) | shim (per `vkCreateInstance`), report, bootlog | (v1.2) What newly created app Vulkan instances get (23.2). An app that sets it gains nothing: the render node stays 0660 unless gb_power itself derived `nvidia` (23.5). |
| `vendor.gb.gfx_reason` | 20 | `ok`, `nopkg`, `nodtx`, `kill`, `test`, `crashloop`, `wedged`, `fault`, `mode`, `detached`, `detaching`, `loading`, `nodriver`, `untested`, `testerror`, `selftest` | gb_power (only) | report, bootlog (GbGpu reads it from the status object) | (v1.2) Why `gfx_active` is `intel` (`ok` when `nvidia`), 23.2. |
| `vendor.gb.gfx_test` | 18 | `none`, `pending`, `running`, `pass`, `fail`, `error` | gb_power (only) | report, bootlog | (v1.2) Self-test progress for the current identity (25.4). |
| `persist.vendor.gb.gfx_ok` | 24 | `1`, `0`, empty | gb_power (only) | report, user | (v1.2) **Mirror** of the validated self-test record (25.4). Never an input: gb_power ignores it (D4, review D1B-3). |
| `vendor.gb.power.api` | 19 | `1` | gb_power | UI, tests | Control socket is listening, protocol version 1. |
| `vendor.gb.dtx.mode` / `.base` / `.latch` | <=19 | existing values | gb_power | existing | Unchanged. |
| `persist.vendor.gb.perf_profile` | 30 | existing | user | gb_power | Unchanged. |
| `ro.gfx.driver.0` | 15 | `gb.sb3.gfxdriver` | (v1.2) the native `/odm/etc/build.prop` (Wave 2, `gfxdriver.json` `build_prop`), on every native image; never in the VM image | Android GpuService (constructor, `a17 GpuService.java:91`), GraphicsEnvironment (`:938`) | (v1.2) Always set on native images; inert for an app unless the Settings of section 24 select the package for it. It MUST come from build.prop: GpuService reads it once in its constructor, and init loads `/odm/etc/build.prop` before any service could `setprop` a ro property. (v1.1: only `gb_gfxdriver.sh` in `gb.gfxdriver=1` boots.) |
| `ro.hardware.vulkan`, `ro.hardware.egl` | | lapis values (`intel`, `angle` native) | nobody | | MUST NOT be set or overridden by any component. |
| `debug.hwui.renderer`, `ro.hwui.use_vulkan` | | native lapis: unset / `true` (`vendor_native_build.prop:45`) | nobody (v1.2: the native rc and build.prop MUST NOT set or override them) | shim HWUI guard (14) | (v1.2) If HWUI ran on GL, its ANGLE instances could not be told from an app's own ANGLE (14). |
| `debug.mesa.nvk.anb.linear`, `debug.mesa.nvk.anb.waitidle` | 25, 27 | `0`/`1` (nvk-gfxdriver NV-D10) | user (adb, debugging) | NVK patches 0004/0006 | Unchanged. |

Settings (not properties): (v1.2) section 24 replaces the v1.1 P8 note. gb_power decides the values of exactly three
`Settings.Global` keys and gb_notify writes them; no other component and no other key.

---

## 5. Filesystem paths

### 5.1 `/data/vendor/gb` (persistent)

Every rc file that needs one of these directories creates the whole chain with exactly these modes (init `mkdir` does
not create parents and re-applies mode/owner on an existing directory, so all rc files MUST agree).

**Root-only state (D8, v1.1).** `/data/vendor/gb` is 0751: group shell can list and traverse it, but cannot create,
rename or delete entries in it, so the crash-loop marker, `clean_boots`, `bootseq`, `compute_allow` and the
`payload/` directory cannot be replaced or swapped by a shell-uid process. Inside a directory that group shell CAN
write (only `notify/`), a root writer never opens, chmods or chowns a path that may already exist: temporary files are
created with `O_CREAT|O_EXCL|O_NOFOLLOW` (C) or under `set -C` (mksh noclobber, which refuses existing files and
symlinks) with the mode set by `umask`, and then renamed. gb_nouveau.sh checks `payload/` and `payload/dgpu.erofs`
before use (6.5).

| Path | Type | Mode owner:group | Writer | Readers | Content |
|---|---|---|---|---|---|
| `/data/vendor/gb/` | dir | 0751 root:shell (D8) | init | all | |
| `nouveau.loading` | file | 0600 root:root | gb_nouveau.sh; (v1.2) gb_power (rebind guard, 23.8, through its worker) | gb_nouveau.sh | Crash-loop marker. v1.1: one line `mode=<mode> t=<uptime_s> argv=<args>`; only existence matters. Created + `sync`ed right before the first insmod; removed at the clean mark, on an unhealthy result, or by the crash-loop path. **(v1.2)** One line `mode=<mode> t=<uptime_s> boot=<boot_id> argv=<args>` (`mode` = `auto`, `once`, `test`, `bind` or `rebind`; `boot_id` = `/proc/sys/kernel/random/boot_id`). A marker whose `boot=` equals the current boot id is a window of **this** boot (another writer holds the ESP lock): not a crash-loop. A marker of another boot, or without `boot=` (v1.1 format), is a crash-loop (fail safe). Also written by the bind path (7.2a) and gb_power's rebind guard. |
| `nouveau.clean_boots` | file | 0644 root:root | gb_nouveau.sh | gb_nouveau.sh | Decimal + `\n`. Missing or non-numeric = 0. Incremented at the clean mark; reset to 0 on crash-loop and unhealthy. |
| `bootseq` | file | 0644 root:root | gb_nouveau.sh, gb_dgpu_report.sh | same | `<N> <boot_id>\n`. The first writer in a boot whose `/proc/sys/kernel/random/boot_id` differs increments N (lock: `mkdir /dev/gb_dgpu/bootseq.lock`). N names the report files. |
| `report/` | dir | 0750 root:shell | root only | shell (adb), bootlog | Retention: newest 20 files per prefix. |
| `report/dgpu-<N>.txt` | file | 0640 root:shell | gb_dgpu_report.sh `report` | gb_bootlog, user | Read-only report (section 7.10). |
| `report/load-<N>.txt` | file | 0640 root:shell | gb_nouveau.sh | gb_dgpu_report.sh, bootlog | Load record (section 7.7). |
| `report/test-<N>.txt` | file | 0640 root:shell | gb_dgpu_report.sh `test` | bootlog | Phase 2B sequence results. |
| `report/dmesg-live.txt`, `dmesg-live.prev.txt` | file | 0640 root:shell | gb_dgpu_dmesg | bootlog | `dmesg -w` stream, `sync` every 5 s, 16 MiB cap. |
| `report/gb_nouveau.log` | file | 0640 root:shell | gb_nouveau.sh | bootlog | Append log, 1 MiB cap then rotate to `.1`. |
| `notify/` | dir | 0770 root:shell | root (queue writers), shell (gb_notify deletes) | gb_notify | Notification queue (section 12.1). The only shell-writable directory root writes into: O_EXCL / noclobber creation only. |
| `payload/` | dir | 0700 root:root | | | Checked before use: a real directory (no symlink), owner root, mode 0700; else removed and re-created. |
| `payload/dgpu.erofs` | file | 0640 root:root | gb_nouveau.sh stage | kernel (mount) | Verified copy of the ESP payload (D8: moved from `/data/vendor/gb/dgpu.erofs`). Temp name `dgpu.erofs.tmp`. |
| `compute_allow` | file | 0644 root:root | gb_power (only) | gb_gpu_compute, gb_power | Compute client allowlist, section 11.3. |
| `gfx_app_prefs` (v1.2) | file | 0644 root:root | gb_power (only; tmp + rename with `O_CREAT\|O_EXCL\|O_NOFOLLOW`, like `allow_save()`) | gb_power | Per-app choices: lines `<pkg> intel` or `<pkg> nvidia` (`pkg_name_ok()` syntax), sorted, unique, <= 128, `#` comments. Seeded once from `/odm/etc/gb/gfx_app_prefs.default` when the file does not exist. 24.2. |
| `gfx_catalog` (v1.2) | file | 0644 root:root | gb_power (from GbGpu's `apps-commit`) | gb_power | App catalog: first line `# pkgs <mtime_ns> <size>` (the packages.list stamp at commit), then `<pkg> g` (game) or `<pkg> a` (other launchable non-system app), sorted, <= 512. Kept across boots so the opt-in list is right before GbGpu runs. 24.3. |
| `gfx_optin` (v1.2) | file | 0644 root:root | gb_power (only) | gb_notify (as shell, read only) | The computed `updatable_driver_production_opt_in_apps` list, one package per line, sorted, <= 512. 24.4. |
| `gfx_optout` (v1.2) | file | 0644 root:root | gb_power (only) | gb_notify (as shell, read only) | The "Always Intel" packages (`updatable_driver_production_opt_out_apps`), one per line, sorted, <= 128. 24.4. |
| `gfx_session` (v1.2) | file | 0600 root:root | gb_power (worker op, `fsync`) | gb_power | App-graphics session marker: `boot=<boot_id> t=<epoch_s>`. Exists exactly while `gfx_active=nvidia`; removed (+ directory `fsync`) on every nvidia -> intel transition and at shutdown. 23.10 (review A1). |
| `gfx_unclean` (v1.2) | file | 0644 root:root | gb_power | gb_power | Decimal + `\n`: consecutive boots that found a `gfx_session` of an earlier boot. Missing or non-numeric = 0. 23.10. |
| `selftest/` (v1.2) | dir | 0700 root:root | init (`gb_nouveau.rc`) | root | Self-test state. Checked like `payload/` (real directory, root, 0700) before use. |
| `selftest/running` (v1.2) | file | 0600 root:root | `gb_dgpu_report.sh selftest` | gb_power | Crash marker `boot=<boot_id> t=<epoch_s>`: created + `sync` before the first probe, removed + `sync` after the verdict. 25.3. |
| `selftest/result` (v1.2) | file | 0644 root:root | `gb_dgpu_report.sh selftest`; gb_power (only the `interrupted`, `crash` and `hang` verdicts) | gb_power | The self-test record (truth), 25.4. Written as `.tmp` (O_EXCL/noclobber), `sync`, rename. |
| `report/selftest-<N>.txt` (v1.2) | file | 0640 root:shell | `gb_dgpu_report.sh selftest` | gb_bootlog, user | Full probe output, identity and timing. Retention as `report/` (20 per prefix). |
| `models/` | dir | 0775 root:shell | user (`adb push`) | gb_gpu_compute | GGUF models `<id>.gguf`. |
| `compute/` | dir | 0700 nobody:graphics | gb_gpu_compute | | Private run dir: internal sockets, `cache/` (Mesa shader cache). |
| `ui/` | dir | 0700 shell:shell | init (`gb_gpu_ui.rc`) | | gb_gpu_ui_setup.sh's private directory (shell cannot create files in `/data/vendor/gb` itself). |
| `ui/tile_added` | file | 0644 shell:shell | gb_gpu_ui_setup.sh | same | One-time QS tile add marker (v1: `/data/vendor/gb/ui_tile_added`). |
| `/data/vendor/nvk/` | dir | 0770 shell:shell (v1: 0775 root:shell) | gb_nvk_probe (runs as shell, 15.5) | user (adb shell) | `probe.txt`, `probe.ppm`. No root process writes there any more. |

### 5.2 `/dev/gb_dgpu` (per boot, tmpfs, root only)

`/dev/gb_dgpu/` 0700 root:root, created by `gb_nouveau.rc` and `gb_power.rc` at `early-init` (both, identical) and by
gb_power itself if missing.

| File | Writer | Meaning |
|---|---|---|
| `used`, `foreign`, `wedged` | section 4 | Monotonic flags (section 4). Content irrelevant. |
| `noload` | gb_nouveau.sh | Monotonic. Created by the crash-loop, kill-switch and unhealthy paths and after an insmod that was killed or oopsed: `auto` refuses (`refused:noload`) for the rest of the boot whatever the properties say. gb_power answers `enable` with `next-boot`. |
| `keep` | gb_nouveau.sh | Monotonic. An unhealthy (or killed) load: the driver stays loaded until restart, `off` does nothing (D20). gb_power answers `disable` with `next-boot`. |
| `off` | gb_nouveau.sh | Monotonic. `off` unbound nouveau but kept `nouveau.ko` loaded (foreign, or a failed rmmod). If nouveau binds the dGPU again (rescan, pciehp) while `persist.vendor.gb.nouveau` is not `1`, gb_power starts `gb_nouveau_off` again (10.4). |
| `helpers` | gb_nouveau.sh | Helper modules this boot's `auto` insmodded (one name per line); `off` rmmods only these. |
| `gfxdriver` | v1.1: gb_gfxdriver.sh | **(v1.2) Retired** (nothing creates or reads it). v1.1: monotonic P8 flag, the root-only half of the gfxdriver gate. |
| `detaching` | gb_power | **Not monotonic.** Exists from the moment gb_power starts handling a detach request until the EC process is over (cancel echo, latch closed, timeouts). gb_nouveau.sh waits for it (15 s) before its load window and refuses insmod while it exists (7.2). |
| `probe.<pid>` | gb_dgpu_report.sh | Temporary nvk_probe output of the test sequence. |
| `bootseq.lock/` | gb_nouveau.sh, gb_dgpu_report.sh | mkdir lock for `bootseq`. |

### 5.3 `/odm` and `/vendor`

| Path | Origin | Notes |
|---|---|---|
| `/odm/etc/gb_hw` | gbinit (existing) | `native` / `vm`. |
| `/odm/etc/gb/tu117.sha256` | cpio (nouveau.json) | `firmware_staged_sha256.txt`, `sha256sum` format, paths `nvidia/tu117/...`. |
| `/odm/etc/gb/dgpu_payload.sha256` | cpio (build_esp P5) | Byte-identical copy of `ESP:/gb/dgpu-<krel>.sha256`. Present = payload mode; absent = legacy mode (section 6.4). |
| `/odm/etc/gb/gfx_build_id` (v1.2) | cpio 0644 (Wave 2, build_esp) | One line `apk=<sha256 of GbGfxDriver.apk> icd=<sha256 of the shipped vulkan.nouveau.so> nvk=<sha256 of lib/x86_64/libgb_nvk.so in the APK> probe=<sha256 of nvk_probe> pay=<sha256 field of dgpu_payload.sha256, or legacy>` (lower-case hex). Absent = the driver package is not configured (`gfx_cap` 0). Part of the self-test identity (25.1). |
| `/odm/etc/gb/gfx_app_prefs.default` (v1.2) | cpio 0644 (gpu_ui.json) | Seed for `gfx_app_prefs` (same format). Ships **empty**. |
| `/odm/dgpu/` | cpio empty dir 0755 (P5) | erofs mount point. |
| `/odm/firmware/nvidia/` | cpio: legacy = the 30 firmware files; P5 = empty dir 0755 | Bind target. `firmware_class.path=/odm/firmware,/vendor/firmware` (existing) finds `nvidia/tu117/...` there. |
| `/odm/lib/modules/{drm_exec,drm_gpuvm,gpu-sched,drm_ttm_helper,nouveau}.ko` | legacy only | Removed from the cpio by P5. Never in any `modules.load*`. |
| `/vendor/bin/gb_nouveau.sh`, `gb_dgpu_report.sh`, `gb_dgpu_check.sh`, `gb_notify.sh` | cpio `vup/bin/` | 0755. |
| `/vendor/bin/gb_power` | cpio `vup/bin/` | Existing. |
| `/vendor/bin/nvk_probe` | cpio `vup/bin/` (stays a real file in P5, D7) | 42 KB. Default ICD path stays `/vendor/lib64/hw/vulkan.nouveau.so`. |
| `/vendor/lib64/hw/vulkan.nouveau.so` | legacy: real file; P5: symlink -> `/odm/dgpu/lib64/hw/vulkan.nouveau.so` | Dangling until the payload is mounted; nothing but nvk_probe/compute ever opens it. |
| `/vendor/bin/gb_gpu_compute` | cpio `vup/bin/` (component 5) | Small launcher; real code in the payload (D7). |
| `/vendor/bin/gb_gpu_ui_setup.sh` | cpio `vup/bin/` (component 6) | |
| `/vendor/bin/gb_gfxdriver.sh` | v1.1: cpio `vup/bin/` (P8 builds only) | **(v1.2) Not shipped.** |
| `/odm/app/GbGpu/GbGpu.apk` | cpio `app/GbGpu/` (component 6) | Non-privileged system app. |
| `/odm/app/GbGfxDriver/GbGfxDriver.apk` | cpio `app/GbGfxDriver/` (v1.2: every native image; v1.1: P8 builds only) | Non-privileged system app. MUST NOT contain `assets/allowlist.txt` (24.5). |
| `/odm/etc/build.prop` (v1.2 line) | native build.prop (Wave 2) | `ro.gfx.driver.0=gb.sb3.gfxdriver` exactly once; not in the VM build.prop. |
| `/odm/priv-app/` | MUST NOT exist | `ro.control_privapp_permissions=enforce` would abort boot. |
| `/mnt/gb_esp_ro` | created at runtime by gb_nouveau.sh | Read-only ESP mount for payload staging only. |

Linker facts that constrain these paths (verified on the VM, `/linkerconfig/ld.config.txt`, 276.R4): executables get
the `vendor` linker section only under `/odm/bin/` or `/vendor/bin/` (there is no mapping for `/odm/dgpu/bin/`); the
vendor section's default namespace permits `/odm`, `/vendor`, `/system/vendor` (prefixes), so libraries whose realpath
is under `/odm/dgpu/` load there; the `sphal` namespace permits only `/odm/${LIB}`, `/vendor/${LIB}`,
`/vendor/odm/${LIB}`, `/system/vendor/${LIB}`, `/data`, so the payload ICD can never be loaded as a sphal HAL (by
design). Hence: payload holds libraries, modules and firmware only; every executable is a small file in `/vendor/bin`
(D7). Payload libraries that need sibling libraries link with `-Wl,-rpath,$ORIGIN` (DT_RUNPATH).

### 5.4 ESP lock (mutual exclusion around ESP mounts and nouveau load windows)

Lock = the directory `/dev/gb_esp.lock` (root only). Acquire: `mkdir /dev/gb_esp.lock` succeeds, then write
`/dev/gb_esp.lock/owner` = `<pid> <name> <uptime_s>` (remember this exact line). Release: only if `owner` still holds
the line you wrote, remove `owner`, `rmdir` (a lock stolen from you belongs to someone else now). Poll interval 1 s.

**Stale lock (v1.1, atomic steal).** A lock is stale when its owner pid no longer exists (`/proc/<pid>` missing), its
owner field is not a number, or it has had no `owner` file for 5 consecutive polls. A waiter that judges it stale
records the lock directory's inode (`stat -c %i`) together with the owner line it read, then:
1. renames the lock away: `mv -T /dev/gb_esp.lock /dev/gb_esp.lock.stale.<own pid>` (rename(2); only one waiter can
   win, a failed rename means somebody else stole or released it: retry `mkdir` at once);
2. if the renamed directory has the recorded inode **and** the recorded owner line, it is the stale lock: remove its
   `owner`, `rmdir` it, retry `mkdir`;
3. otherwise a live holder re-created the lock in between: put it back with `mv -T ... /dev/gb_esp.lock` (log it if
   that fails) and keep waiting.
Never `rm owner; rmdir` the lock in place to steal it (two stealers would both win). C users (gb_slotsync) do the same
with `rename(2)`/`stat(2)`. gb_power reads the owner file: a live `<pid> gb_nouveau ...` owner = the nouveau load
window is open (10.3).

| Holder | Holds it while | Max wait to acquire |
|---|---|---|
| gb_nouveau.sh `auto` | from before payload staging until the health check ends (`loading=1` .. `loading=0`) | 120 s, then `refused:esp` |
| gb_dgpu_report.sh `test` | PM stress and suspend-test phases (`vendor.gb.dgpu.busy=1`) | 120 s, then skip the phase |
| gb_bootlog (Wave 2) | each ESP mount + write + `sync` + umount | 600 s, then proceed without the lock (logs matter most) |
| gb_slotsync (Wave 2) | each `sync_once` ESP mount | 600 s, then skip this cycle (it retries every 60 s) |
| (v1.2) gb_nouveau.sh `auto`, bind path (7.2a) | from before the marker until the health check ends (owner name `gb_nouveau`, so it is a load window for gb_power) | 120 s, then `refused:esp` |
| (v1.2) gb_power, rebind guard (23.8) | from the rebind trigger until the rebind watch ends; owner line `<pid> gb_power <uptime_s>` | never waits (the kernel probe cannot be held back): it takes the lock if free (with the atomic stale steal of this section), else proceeds and logs who holds it |
| (v1.2) `gb_dgpu_report.sh selftest` (25.3) | the whole self-test; owner name `gb_dgpu_report` | 120 s, then `verdict=error reason=esp` |

(v1.2) gb_power's `load_window_open()` is also true while its own rebind guard is active (review B7), so a detach
during a reattach probe is refused with N15 like one during an insmod.

Additionally gb_nouveau.sh verifies right before every insmod that **no** vfat mount of the ESP exists: no
`/proc/mounts` line of type `vfat` whose source device (`readlink -f`) has the same major:minor as
`readlink -f /dev/block/by-name/<gb.logpart>`. If one exists, it is `refused:esp`.

---

## 6. Payload (Phase 5)

### 6.1 ESP files

| ESP path | Content |
|---|---|
| `gb/dgpu-<krel>.erofs` | The payload image (6.2). One per image; build_esp writes only the current krel. |
| `gb/dgpu-<krel>.sha256` | `<sha256 lowercase hex>  dgpu-<krel>.erofs\n` (`sha256sum` format, two spaces). |

The k13 name is 62 characters (FAT32 LFN; the project's FAT writer supports LFN). The kernel (`vmlinuz`) is shared by
both A/B slots, so gb_slotsync never touches the payload.

### 6.2 erofs tree (root of the image)

```
VERSION                                  "gb-dgpu-payload 1\nkrel <krel>\n"
MANIFEST.sha256                          sha256sum lines for every other regular file, sorted by path, paths relative, no "./"
firmware/nvidia/LICENCE.nvidia           linux-firmware LICENSES/LICENCE.nvidia at the pinned tag 20260916
firmware/nvidia/WHENCE.tu117             the WHENCE lines covering nvidia/tu117/* (whence_tu117.py excerpt)
firmware/nvidia/tu117/acr/{bl,ucode_ahesasc,ucode_asb,ucode_unload,unload_bl}.bin
firmware/nvidia/tu117/gr/{fecs_bl,fecs_data,fecs_inst,fecs_sig,gpccs_bl,gpccs_data,gpccs_inst,gpccs_sig,
                          sw_bundle_init,sw_ctx,sw_method_init,sw_nonctx,sw_veid_bundle_init}.bin
firmware/nvidia/tu117/gsp/{booter_load,booter_unload,bootloader,gsp}-{570.144,535.113.01}.bin
firmware/nvidia/tu117/nvdec/scrubber.bin
firmware/nvidia/tu117/sec2/{desc,image,sig}.bin
lib/modules/drm_exec.ko
lib/modules/drm_gpuvm.ko
lib/modules/gpu-sched.ko
lib/modules/drm_ttm_helper.ko
lib/modules/nouveau.ko
lib/modules/modules.dgpu                 the 5 file names above, one per line, in that order, no arguments
lib64/hw/vulkan.nouveau.so               component 4
lib64/gpu-compute/libgb_nvk_loader.so    component 5 (P6)
lib64/gpu-compute/libgb_gpu_compute.so   component 5 (P6)
lib64/gpu-compute/<other ggml/llama .so> component 5 (P6), DT_RUNPATH $ORIGIN
```

The 30 firmware files are exactly those in `firmware_staged_sha256.txt` (paths `nvidia/tu117/...` map to
`firmware/nvidia/tu117/...`) and their sha256 MUST match it. Modes: directories 0755, files 0644 (no executables in
the payload). `mxm-wmi.ko` is not included (unused without VGA_SWITCHEROO).

### 6.3 Build (component 1 provides the tool, Wave 2 calls it)

`builder:make_dgpu_payload.sh <staged_tree_dir> <out.erofs> <krel> [<firmware_staged_sha256.txt>]` (implemented in
v1.1: `C:\GB-build\native\nouveau-kernel\payload\`; the sha list defaults to `../firmware_staged_sha256.txt` next to
the tool, so copy both together):
1. Check the tree against the list in 6.2 (P6 compute files `lib64/gpu-compute/*.so` optional): every required file
   present and regular; refuse any symlink, special file or unexpected entry anywhere (not only top level); every
   module's vermagic starts with `<krel>` and ends with a module signature; `vulkan.nouveau.so` is ELF; the 30 firmware
   files are exactly those of the sha list with matching sha256; `WHENCE.tu117` and `LICENCE.nvidia` present.
2. On a private copy (the staged tree is never modified): normalise modes (dirs 0755, files 0644), write
   `lib/modules/modules.dgpu` (an existing one must already have the exact content), `VERSION` and `MANIFEST.sha256`.
3. `nice -n 19 ionice -c3 mkfs.erofs -zlz4hc,12 -T0 --all-root -x -1 -U 67622d64-6770-7500-0000-000000000001 <out> <tree>`
   (LZ4 only: k13 has `EROFS_FS_ZIP=y` with LZMA/DEFLATE/ZSTD off; no `-E` options).
4. `fsck.erofs <out>`, and with `--extract` support the extracted tree must match `MANIFEST.sha256`; write
   `<out>.sha256` in the 6.1 format (file name `dgpu-<krel>.erofs`).
5. Print one summary line `PAYLOAD ok size=<bytes> sha256=<hex> files=<n>`; on any failure print
   `PAYLOAD FAIL <reason>` and exit 1 (usage: exit 2).

Two runs on the same tree MUST give byte-identical images (NP-21: 60,260,352 bytes with the k14 modules, the 8a ICD
and the two compute libraries, sha256 `1d7277b1...`).

### 6.4 Modes on the device

* **Legacy mode** (`/odm/etc/gb/dgpu_payload.sha256` absent): modules from `/odm/lib/modules`, firmware already in
  `/odm/firmware/nvidia`, no mounts; `vendor.gb.dgpu.payload=legacy`. This is today's image layout.
* **Payload mode** (file present): the stage step (6.5) MUST succeed before any insmod (fail closed). Modules from
  `/odm/dgpu/lib/modules`, firmware via the bind mount.

### 6.5 Stage step (gb_nouveau.sh, inside the ESP lock, before the marker)

1. `E=/odm/etc/gb/dgpu_payload.sha256`. Its file name field MUST be `dgpu-$(uname -r).erofs`, else `error:krel`.
   `payload/` MUST be a real directory (no symlink) owned by root with mode 0700, else it is removed (`rm -rf` removes
   a symlink itself, never its target) and re-created; `dgpu.erofs` MUST be a plain root-owned file with one link
   (no symlink, no hard link), else it is deleted and re-copied. Both checks run again after the copy, right before
   the verify and the mount (step 4).
2. If `/data/vendor/gb/payload/dgpu.erofs` exists and its sha256 equals the one in `E`: skip to 4.
3. Copy: `mkdir -p /mnt/gb_esp_ro`; `mount -t vfat -o ro,nodev,nosuid,noexec <esp dev> /mnt/gb_esp_ro` (else
   `error:esp-mount`); source `/mnt/gb_esp_ro/gb/dgpu-<krel>.erofs` (missing = `absent`); copy to
   `dgpu.erofs.tmp`, `sync`; verify sha256 (else delete tmp, `error:sha`); `mv` to `dgpu.erofs`; `umount
   /mnt/gb_esp_ro` (always, also on error).
4. Verify the sha256 of `dgpu.erofs` again (it is re-verified before every mount).
5. `mount -t erofs -o ro,nodev,nosuid,context=u:object_r:vendor_file:s0 /data/vendor/gb/payload/dgpu.erofs /odm/dgpu`
   (else `error:mount`). File-backed erofs (`EROFS_FS_BACKED_BY_FILE=y`) is preferred; if toybox `mount` sets up a loop
   device instead, that is acceptable (`BLK_DEV_LOOP=y`); the report records which (D27).
6. `mount --bind /odm/dgpu/firmware/nvidia /odm/firmware/nvidia` (else `error:bind`).
7. Check `/odm/dgpu/VERSION` names this krel; `vendor.gb.dgpu.payload=mounted`.

Already mounted (a second `auto` run in the same boot): verify both mounts exist and skip. Unmount
(`umount /odm/firmware/nvidia`, then `umount /odm/dgpu`) happens **only** after a successful `rmmod nouveau` and after
`gb_gpu_compute` is stopped; then `payload=ready`. With `foreign` set (no rmmod) the payload stays mounted until reboot.

`<step>` values for `error:<step>`: `lock`, `krel`, `esp-mount`, `sha`, `mount`, `bind`, `version`.

---

## 7. nouveau policy contract (`gb_nouveau.sh`, component 1)

### 7.1 Invocation

`gb_nouveau.sh auto|off|dmesg-stream` (anything else: usage, exit 2). Runs as root in `u:r:shell:s0` from init.
Environment for tests: `GB_ROOT` (prefix for every absolute path: `/proc`, `/sys`, `/dev`, `/data`, `/odm`, `/mnt`,
`/vendor`) and `GB_TEST_PATH` (prepended to `PATH`). The script sets
`PATH=${GB_TEST_PATH:+$GB_TEST_PATH:}/system/bin:/vendor/bin` and calls every tool by bare name (`insmod`, `rmmod`,
`mount`, `umount`, `getprop`, `setprop`, `dmesg`, `sha256sum`, `sleep`, `chmod`, `chown`, `sync`, `uname`, `readlink`,
`stat`). It runs under `mksh` (`/system/bin/sh`) and MUST also pass `shellcheck -s sh`. `set -f` before handling any
argument text. Log lines go to `/dev/kmsg` with prefix `gb_nouveau: ` and to `report/gb_nouveau.log`.

Exit codes: 0 done or nothing to do; 1 refused or failed; 2 usage.

### 7.2 `auto` sequence

**(v1.2) The v1.2 sequence is 7.2-v12 right below; it replaces the v1.1 steps 1-18 that follow it** (kept for the
step numbers that other sections cite; where they differ, 7.2-v12 wins).

#### 7.2-v12 `auto` (v1.2)

Every "set `booted`" below means: `setprop vendor.gb.nouveau.booted 1` only if it does not already read `1`
(review E2: a repeated set re-fires the rc triggers).

1. Not native: `result=refused:hw`, exit 1 (no `booted`).
2. `/dev/gb_dgpu/wedged` exists: `result=refused:wedged`, exit 1.
3. Parse the cmdline (section 3). Kill switch: create `noload`, `mode=disabled`, `result=disabled`, exit 0 without
   `booted`.
4. `noload` exists: `result=refused:noload`, exit 1 (no `booted`).
5. `sys.boot_completed` is not `1`: `result=refused:boot`, exit 1 (no `booted`).
6. Crash-loop: the marker exists and its `boot=` field is missing or differs from the current boot id (5.1): create
   `noload`, remove the marker, `setprop persist.vendor.gb.gfx_mode intel`, `clean_boots` = 0,
   `mode=<cmdline mode or default>`, `result=crashloop`, queue N01 (v1.2 text, 12.2), exit 0 without `booted`. A
   marker of the current boot belongs to a window of this boot (gb_power's rebind guard, 23.8): leave it alone and go
   on (the ESP lock of step 10 serializes the windows).
7. Mode: `gb.nouveau=test` -> `test`; `once` -> `once`; else `gfx_mode()` (22.2) `auto` or `nvidia` -> `auto`; else
   (`intel`) `mode=default`, `result=idle`, set `booted`, exit 0. `persist.vendor.gb.nouveau` is not read.
   Probation: `mode=auto` and `clean_boots < 3` (D2 with `auto` in place of `persist`).
8. nouveau already loaded (`/sys/module/nouveau` exists), review A2 order (every check above has run):
   * the dGPU is bound to nouveau: permissions (7.5), set `booted`, exit 0;
   * `/dev/gb_dgpu/keep` exists: `result=refused:keep`, set `booted`, exit 1 (a driver this boot marked sick is never
     bound again, D20);
   * the dGPU is present and driverless: the **bind path** 7.2a;
   * no dGPU: `result=refused:nodgpu`, set `booted`, exit 1.
9. Guards as v1.1 step 7 (`i915`, `nodgpu`, `bar`, `module`); each failure sets `booted`.
10. Detach wait (15 s, `refused:detach`), ESP lock (120 s, `refused:esp`), `loading=1`, `result=loading`, `used`: as
    v1.1 step 8.
11. Payload stage, argument list, panic policy and extras: v1.1 steps 9-11 (`kernel.panic=30` on every load).
12. No ESP mount (else `refused:esp`); write + `sync` the marker in the v1.2 format (`mode=auto|once|test`); no
    `foreign` here any more (the v1.1 gfxdriver-boot rule is retired; gb_power sets `foreign` before the first
    `gfx_active=nvidia`, 23.3); MemAvailable.
13. insmod as v1.1 step 13. A killed or oopsing insmod additionally sets `persist.vendor.gb.gfx_mode=intel` (after
    `keep` and `noload`).
14.-18. As v1.1 steps 14-18: permissions (7.5: always 0660 from the script), health (7.7, unhealthy also sets
    `gfx_mode=intel`), `loading=0`, lock released, set `booted`, test-mode report trigger, clean mark (+120 s:
    marker removed, `clean_boots` incremented, `result=clean`).

#### 7.2a Bind path (v1.2: module loaded, dGPU driverless, mode wants a load; review A2, B3, C8)

Used after an `off-unbound` (Intel mode in a `foreign` boot kept the module) when the user picks Auto or NVIDIA again,
and after a failed or skipped kernel rebind. It is a load window exactly like an insmod:

1. `init.svc.gb_nouveau_off` reads `running`: wait up to 30 s for it to stop; still running: `result=refused:off`, set
   `booted`, exit 1 (gb_power re-issues the start later, 23.7). (The property only makes the script wait.)
2. Guards `i915`, `nodgpu`, `bar` (each `refused:<guard>`, set `booted`, exit 1).
3. Detach wait 15 s (`refused:detach`, the v1.1 name and wait, review B3); ESP lock 120 s (`refused:esp`, owner
   `gb_nouveau`); `loading=1`; `result=loading`; `used`.
4. No ESP vfat mount (else `refused:esp`). Marker `mode=bind t=<s> boot=<id> argv=-` + `sync`;
   `kernel.panic=30`.
5. Late checks right before the write: `detaching` (`refused:detach`), the dGPU still present, driverless, with its
   BAR (`refused:nodgpu` / `refused:bar`); each removes the marker.
6. Kernel log mark; write the BDF to `/sys/bus/pci/drivers/nouveau/bind` in a background subshell; poll every 0.5 s,
   at most 40 times (20 s), for `<dgpu>/driver` -> `nouveau`.
   * The write is still running after 20 s: the probe hangs in the kernel. Create `wedged`, `keep` and `noload`,
     `setprop persist.vendor.gb.gfx_mode intel`, keep the marker (a later freeze then ends in the crash-loop path),
     `result=bind-failed`, N05, `loading=0`, release the lock, set `booted`, exit 1. No further dGPU write this boot.
   * The write returned and the dGPU is not bound: `unhealthy bind` (7.7 handling).
7. Permissions (7.5), health check (7.7, oops and error lines since the bind mark), `result=healthy`, `loading=0`,
   release the lock, set `booted`.
8. Clean mark at +120 s after the bind returned: dmesg re-check, marker removed, `result=clean`. `clean_boots` is
   **not** incremented (probation counts boots, not binds; review C8).

#### 7.2 v1.1 steps (reference)

1. Not native (`/odm/etc/gb_hw` != `native`): `result=refused:hw`, exit 1 (no `booted`).
2. `/dev/gb_dgpu/wedged` exists: `result=refused:wedged`, exit 1.
3. nouveau already loaded (`/sys/module/nouveau` exists): make sure node permissions are right (7.5), set `booted=1`,
   exit 0 (idempotent; a runtime start after load is a no-op).
4. Parse the cmdline (section 3). Kill switch (`gb.nouveau=0`, unknown value, or `module_blacklist` containing
   `nouveau`): create `/dev/gb_dgpu/noload`, `mode=disabled`, `result=disabled`, exit 0 **without** setting `booted`.
4a. `/dev/gb_dgpu/noload` exists: `result=refused:noload`, exit 1 (no `booted`). Root-only, so a property spoof
   (`persist`=1 + `booted`=1, or `ctl.start`) cannot start a load in a crash-loop or kill-switch boot.
4b. `sys.boot_completed` is not `1`: `result=refused:boot`, exit 1 (no `booted`; the boot_completed trigger runs
   `auto` later). Comes before the crash-loop step, so an early start cannot consume the marker.
5. Crash-loop: if `/data/vendor/gb/nouveau.loading` exists: create `/dev/gb_dgpu/noload`, remove the marker,
   `setprop persist.vendor.gb.nouveau 0`, write `nouveau.clean_boots` = 0, `result=crashloop`, queue notification N01,
   exit 0 **without** setting `booted`.
6. Mode: `gb.nouveau=test` -> `test`; `once` -> `once`; else `persist.vendor.gb.nouveau=1` -> `persist`; else
   `default`: `result=idle`, set `booted=1`, exit 0.
7. Guards (each failure: log, `result=refused:<guard>`, set `booted=1`, exit 1):
   `i915` (2.2), `nodgpu` (no dGPU function), `bar` (BAR zero), `module` (nouveau.ko not found in the active mode).
8. Wait up to 15 s while `/dev/gb_dgpu/detaching` exists (gb_power is handling a detach), else
   `result=refused:detach`, set `booted=1`, exit 1. Acquire the ESP lock (else `result=refused:esp`, set `booted=1`,
   exit 1); `loading=1`; `result=loading`; set `used` (property + file) now: from here on a detach request is refused
   by gb_power while this load window is open (10.3).
9. Payload mode: stage (6.5); any failure: release lock, `loading=0`, `result=refused:payload`, set `booted=1`,
   exit 1.
10. Build the argument list (7.4); `setprop vendor.gb.nouveau.argv`.
11. Panic policy and extras (7.6).
12. Verify no ESP mount (5.4; else release lock, `loading=0`, `result=refused:esp`, set `booted=1`, exit 1). Write +
    `sync` the marker (cannot be written: `result=refused:marker`, nothing loads); if `vendor.gb.gfxdriver` is `on`
    or `/dev/gb_dgpu/gfxdriver` exists, set `foreign`. Record MemAvailable into the load record.
13. `/dev/gb_dgpu/detaching` exists: `result=refused:detach`. insmod `drm_exec`, `drm_gpuvm`, `gpu-sched`,
    `drm_ttm_helper` (skip if `/sys/module/<name with - as _>` exists). Right before `nouveau.ko`: again
    `detaching` (`refused:detach`), the dGPU function (2.2, `refused:nodgpu`) and its BAR (`refused:bar`); each of
    these late refusals removes the marker (nothing was probed). Then `nouveau.ko <args>`. A failed insmod:
    `result=insmod-failed:<module>`, remove the marker (no crash happened), `loading=0`, release lock, set
    `booted=1`, exit 1 -- except when insmod was killed by a signal (exit status >= 128) or, for `nouveau.ko`, an
    oops line (7.7 patterns) appeared after the insmod mark: then a crash happened, so the marker **stays** (the next
    boot takes the crash-loop path), `keep` and `noload` are created and `persist`=1 becomes 0.
14. Node permissions (7.5).
15. Health (7.7). Healthy: `result=healthy`. Unhealthy: section 7.7 handling.
16. `loading=0`; release the ESP lock; set `booted=1`.
17. In `test` mode: `setprop vendor.gb.dgpu_report 2`.
18. Sleep until 120 s after step 13 finished; if healthy: remove the marker, increment `clean_boots`, update
    `vendor.gb.nouveau.clean`, `result=clean`; exit 0.

Probation (D2): when `mode=persist` and `clean_boots < 3`, the load is a probation load: everything above applies, plus
the dmesg-live stream runs (as in test mode, but `panic_on_oops` stays 1), and the log says `probation <n>/3`. After 3
clean boots the stream is no longer started for persist loads. The crash-loop guard applies forever.

### 7.3 `off` sequence

(v1.2) `off` is started by the rc trigger `booted=1 && gfx_mode=intel` and by gb_power's `reconcile_off()` (23.6);
the v1.1 `persist.vendor.gb.nouveau=0` trigger is gone. Changes to the steps below:
* after step 1: `gfx_mode()` is not `intel`: `result=off-skipped`, exit 0 (the user switched back before `off` ran).
  The same check runs again right after the refcnt read of step 4; not `intel` then: permissions restored (0660),
  `off-skipped`, exit 0;
* step 3 also stops `gb_gfx_selftest` (`GPU_USERS` gains it; review E8);
* step 5 restores 0660 (7.5; gb_power raises the node again if it derives `nvidia`); N03 has the v1.2 text (12.2);
* step 7: N04 is not posted any more (in v1.2 `off` runs only in Intel mode, where the dGPU ends unbound in D3cold
  and a restart would only unload the module). `/dev/gb_dgpu/off` is still created (informational);
* `off` never writes `persist.vendor.gb.nouveau`.

0. `/dev/gb_dgpu/wedged` exists: `result=off-wedged`, exit 1 (no dGPU writes after a wedge).
1. `mode` is `once` or `test`: `result=off-ignored`, exit 0.
2. nouveau not loaded: set `power/control=auto` on every 10de function, `result=off-done`, exit 0.
3. `setprop ctl.stop` for `gb_dgpu_test`, `gb_nvk_probe` and `gb_gpu_compute` (harmless when not running; the first
   two run nvk_probe, and a root opener is not stopped by chmod 000); wait up to 5 s for every `init.svc.<name>` to
   read `stopped` (or be empty).
4. `chmod 000` every nouveau node; sleep 1 s; read `/sys/module/nouveau/refcnt`.
5. refcnt > 0: restore permissions (7.5); name holders (`/proc/*/fd` symlinks to the nouveau nodes, and
   `/proc/*/maps` lines containing `/dev/dri/card<n>` or `/dev/dri/renderD<n>` of the nouveau nodes);
   `result=off-busy:<n>`; queue N03 with the holder names; exit 1.
6. Unbind every 10de function with a driver (write the BDF to `driver/unbind`) in a background subshell; poll every
   0.5 s up to 20 s for all driver links to disappear. Timeout: create the flag file `/dev/gb_dgpu/wedged` (there is
   no wedged property; gb_power publishes `vendor.gb.dgpu.state=wedged`), `result=off-wedged`, queue N05, exit 1.
   Never retry a dGPU sysfs write in this boot afterwards.
7. `foreign` set: create `/dev/gb_dgpu/off`, `result=off-unbound`, queue N04, do not rmmod, do not unmount the payload.
   Else: `rmmod nouveau` then the helpers listed in `/dev/gb_dgpu/helpers` among `drm_ttm_helper gpu_sched drm_gpuvm
   drm_exec` (ignore "in use" for shared helpers); unmount the payload (6.5); `result=off-done`. A failed `rmmod
   nouveau`: create `/dev/gb_dgpu/off`, `result=off-unbound`, N04.
8. Set `power/control=auto` on every 10de function and its upstream bridges; exit 0.

### 7.4 nouveau arguments

Whitelist atoms (byte-exact tokens of `persist.vendor.gb.nouveau.args`, split on single spaces):
`config=NvGspRm=0`, `config=NvGspFw=0`, `debug=gsp=debug`, `debug=gsp=trace`. Every other token is dropped and logged
(sanitised: printable ASCII only, first 32 characters). Sources added by the script: `gb.nouveau.fw=535` adds
`NvGspFw=0`; `test` mode adds `gsp=debug` if no debug atom is present.

Assembly (nouveau's `config` and `debug` are single `charp` parameters, last one wins, comma-separated; verified
`nouveau_drm.c:90-95`, `nvkm/core/option.c`):
* config options in this order, deduplicated: `NvGspRm=0`, `NvGspFw=0`. If `NvGspRm=0` is present, `NvGspFw=0` is
  dropped and logged (forcing a GSP fwif while disabling GSP-RM makes the probe fail; `firmware.h:63-71`).
* debug: `gsp=trace` wins over `gsp=debug`.
* Final argv: `[config=<a,b>] [debug=<x>] runpm=1 modeset=2` (empty groups omitted; `runpm=1 modeset=2` always last).

### 7.5 DRM node permissions

**(v1.2) This table replaces the v1.1 table** (which had a 0666 column for P8 boots keyed on
`vendor.gb.gfxdriver=on` AND `/dev/gb_dgpu/gfxdriver`; retired):

| Node | Condition | Mode | Applied by |
|---|---|---|---|
| nouveau `renderD<n>` | gb_power's own in-memory `gfx_active` is `nvidia` (23.2; needs a valid self-test pass of this exact identity) | `0666`, root:graphics | gb_power only (23.5) |
| nouveau `renderD<n>` | otherwise | `0660`, root:graphics | gb_nouveau.sh `apply_perms()` after load/bind (always 0660), gb_power |
| nouveau `renderD<n>`, `card<n>` | detach decision, `off` chmod window, after a wedge | `0000` | gb_power `nodes_lock()`, gb_nouveau.sh `off` |
| nouveau `card<n>` | always otherwise | `0600`, root:root | both |
| i915 nodes | always | never touched | |

gb_nouveau.sh never opens the node to 0666 and reads no property for the node mode (it no longer has `gfx_open()`).
gb_power re-applies modes per 23.5 (stat only, never an open). During a detach decision gb_power keeps every nouveau
node at 0000, including nodes that appear during the decision. Ordering with `gfx_active` is 23.3. The refcnt gate
(10.3, 7.3) is unchanged, so an app that opened the node while it was 0666 blocks detach and `off` until it exits or
is closed.

### 7.6 Panic policy and test extras

* Every boot that loads nouveau (once, test, persist, runtime enable): `echo 30 > /proc/sys/kernel/panic` before the
  first insmod (an oops then reboots after 30 s instead of freezing, and the crash-loop guard disables nouveau).
  (v1.2) That is now every boot with the base attached in `auto`/`nvidia` mode, plus the bind path (7.2a); approved by
  the user (D2, 2026-10-25). gb_power's rebind guard (23.8) does not write it: the module is already loaded, so a boot
  that reaches a rebind has run an insmod or a bind of this script, which set it.
* `test` mode additionally: `echo 0 > /proc/sys/kernel/panic_on_oops`, `setprop vendor.gb.dgpu.dmesg 1`, and the
  `debug=gsp=debug` default.
* Probation persist loads: `setprop vendor.gb.dgpu.dmesg 1` (panic_on_oops unchanged). (v1.2: probation `auto`
  loads, the first 3 clean boots.)
* `gb_nouveau.sh dmesg-stream` (service `gb_dgpu_dmesg`): rotate an existing `dmesg-live.txt` to
  `dmesg-live.prev.txt`, then `dmesg -w >> dmesg-live.txt` with a `sync` every 5 s, stop at 16 MiB.

### 7.7 Health check and load record

Within 30 s after the nouveau insmod: the dGPU function's driver is `nouveau`, `power/runtime_status` is `suspended`
and `power_state` is `D3cold`; and the kernel lines added since just before the insmod (lines of `init:` and `gb_*:`
excluded) contain no match for `grep -iE 'nouveau.*(fail|error|timeout|fault)'` and none for the case-sensitive
`grep -E 'WARNING:|Oops|BUG:|general protection|Call Trace:|blocked for more than'` (oops patterns, v1.1). The same
check runs again at the clean mark.

Unhealthy (`result=unhealthy:<why>`, `<why>` = `bind`, `d3cold`, `dmesg`): create `keep` and `noload`,
`setprop persist.vendor.gb.nouveau 0` if it was 1, `clean_boots=0`, remove the marker (this boot survived), queue N02,
leave the driver loaded (never unload a sick driver automatically), `loading=0`, release the lock, set `booted=1`,
exit 1 (D20). (v1.2) In place of the `persist.vendor.gb.nouveau` write: `setprop persist.vendor.gb.gfx_mode intel`
right after `keep` and `noload` exist (the `gfx_mode=intel` rc trigger then starts `off`, which skips because of
`keep`); N02 has the v1.2 text. The same handling applies to the bind path (7.2a).

Load record `report/load-<N>.txt` (`key=value` lines): `mode`, `argv`, `krel`, `payload`, `mem_available_before_kb`,
`mem_available_after_kb`, `insmod_ms`, `bind_ms`, `d3cold_ms`, `rm_version` (from the `RM version:` dmesg line),
`fbsr_line`, `render_map` (`renderD128=0x8086:i915,renderD129=0x10de:nouveau`), `refcnt_baseline`, `health`.

### 7.8 `vendor.gb.nouveau.result` values

`idle`, `disabled`, `crashloop`, `refused:hw`, `refused:wedged`, `refused:noload`, `refused:boot`, `refused:i915`,
`refused:nodgpu`, `refused:bar`, `refused:module`, `refused:detach`, `refused:esp`, `refused:payload`,
`refused:marker`, `loading`, `insmod-failed:<module>`, `healthy`, `clean`, `unhealthy:bind`, `unhealthy:d3cold`,
`unhealthy:dmesg`, `off-ignored`, `off-busy:<n>`, `off-wedged`, `off-unbound`, `off-done`. (v1.1 added
`refused:noload`, `refused:boot`, `refused:detach`; `refused:marker` was NP-D5.) (v1.2 adds `refused:keep` and
`refused:off` (7.2-v12 step 8, 7.2a step 1), `bind-failed` (7.2a step 6) and `off-skipped` (7.3); `mode` gains
`auto`.)

### 7.9 Hot attach

**(v1.2) Replaced by 23.7** (generalised: keyed on the graphics mode, also with the module loaded, deferred while
`gb_nouveau` runs). v1.1 text:

If the boot's policy wanted to load (`mode` in once/test/persist) but refused with `refused:nodgpu`, gb_power starts
`gb_nouveau` (`setprop ctl.start gb_nouveau`) after a dGPU function appears, is driverless, has a non-zero BAR and the
base reports attached (D18). `auto` re-runs its guards.

### 7.10 `gb_dgpu_report.sh report|test`

`report` (read-only, safe on every boot): writes `report/dgpu-<N>.txt` with sections `== build` (`ro.build.id`,
`uname -r`, cmdline, `getenforce`), `== drm` (card/renderD -> vendor/driver), `== vulkan` (`cmd gpu vkjson` device
names), `== pci` (every 10de function and the root port: vendor/device/class/driver/`power/control`/`runtime_status`/
`power_state`/`runtime_active_time`/`runtime_suspended_time`/`iommu_group` members), `== acpi` (table list and
`/sys/firmware/acpi/tables/dynamic`), `== usb` (topology and the sysfs path of every mounted block device),
`== asound`, `== firmware` (sha256 of the active firmware dir vs `/odm/etc/gb/tu117.sha256`), `== power` (30 samples
of `gb_power -o` 10 s apart, only when `mode` is not `test`), `== dtx` (`vendor.gb.dtx.*`), `== props`
(`getprop | grep vendor.gb.`). It never opens a DRM node and never writes sysfs.

(v1.2) A third verb `selftest` (service `gb_gfx_selftest`, started only by gb_power) runs the automatic self-test of
section 25.3; usage text becomes `gb_dgpu_report.sh report|test|selftest`.

`test` (only when `mode=test`, else exit 1): the Phase 2B steps 2-7 of the plan, results in `report/test-<N>.txt`: D3cold
for every 10de function within 30 s; 30 x `gb_power -o` bound-idle; set `foreign`, run `nvk_probe -t all -n 200`;
PM stress (acquire the ESP lock, `vendor.gb.dgpu.busy=1`, `autosuspend_delay_ms`=500, 30 x `nvk_probe -t offscreen -n 1`
3 s apart, count `runtime_status=error`, `WARNING:` and `-110`, restore 5000, `busy=0`, release); suspend test
(post N13, record `/sys/power/suspend_stats`, `runtime_active_time` delta, MemAvailable around each resume, for 5 min);
post N14.

---

## 8. dGPU state model (`vendor.gb.dgpu.state`, single writer gb_power)

### 8.1 Values and derivation

gb_power derives the state on every tick, uevent and socket `status` request. The first matching rule wins:

| # | Condition | State |
|---|---|---|
| 1 | wedged flag set | `wedged` |
| 2 | fault latched (8.2) | `fault` |
| 3 | a slow-path detach decision is running | `detaching` |
| 4 | `vendor.gb.nouveau.mode` = `disabled` | `disabled` |
| 5 | `vendor.gb.nouveau.result` = `crashloop` | `crashloop` |
| 6 | no dGPU function present (base detached, or not enumerated, or no NVIDIA hardware/VM) | `unavailable` |
| 7 | result = `loading` and the dGPU is not yet bound | `loading` |
| 8 | dGPU bound to nouveau and `refcnt` > 0 | `busy:<N>`, N = distinct holder pids found, min 1, max 999 |
| 9 | dGPU bound to nouveau | `standby` |
| 10 | otherwise | `off` |

### 8.2 Fault

Latched (sticky for the boot) with `fault_reason`: `rpm-error` (a nouveau-bound function shows
`runtime_status=error`), `d0-idle` (D0 for > 60 s with refcnt 0 and no compute hold), `rescan` (rescan retries
exhausted with the base attached). `fault_reason=bar` (rescan found a zero BAR) clears on the next successful rescan.
On entering fault: notification N06, latch stays locked.
(v1.2) New reasons: `rebind` (the rebind guard saw the re-added dGPU fail to bind or to reach D3cold, 23.8) and
`selftest-hang` (the self-test service did not stop within 10 s after its 360 s deadline, 25.6). Only `rpm-error`
and `d0-idle` block a detach (`dgpu_fault_blocks()`, as implemented in v1.1); only they post N06. `rescan`, `bar` and
`selftest-hang` post N20; `rebind` posts N02 (`<why>` = `rebind`). Every fault makes `gfx_active=intel` (23.2).

### 8.3 UI mapping (normative for component 6)

**(v1.2) Superseded by 27.1** (the "Graphics" tile maps the graphics fields, not only the dGPU state). The v1.1
"NVIDIA GPU" tile table below is kept for reference and for the v1.1 host tests that still exercise `gpuTile()`.

| State | QS tile `Tile.state` | Subtitle (exact) | Tap action |
|---|---|---|---|
| `off` | INACTIVE | `Off` (`On at next restart` if `persist`=1) | `enable` |
| `crashloop` | INACTIVE | `Off (recovered)` (`On at next restart` if `persist`=1) | `enable` |
| `loading` | ACTIVE | `Starting` | none |
| `standby` | ACTIVE | `Standby` | `disable` |
| `busy:N` | ACTIVE | `Busy (N)` | dialog -> `close-holders` or `disable` |
| `detaching` | UNAVAILABLE | `Preparing to detach` | none |
| `disabled` | UNAVAILABLE | `Disabled for this boot` | none |
| `unavailable` | UNAVAILABLE | `Unavailable` | none |
| `wedged` | UNAVAILABLE | `Restart required` | none |
| `fault` | UNAVAILABLE | `Fault` | none |
| no socket / `hello` fails / role `other` / unknown value | UNAVAILABLE | `Unavailable` | none |

In `once`/`test` boots the tile shows the state but taps return `effect:"next-boot"` (the UI shows a toast
`Takes effect after restart`).

---

## 9. Control socket `/dev/socket/gb_power` (server: gb_power; clients: GbGpu, gb_gpu_compute, compute clients, tests)

### 9.1 Transport and framing

* Created by init: `socket gb_power stream 0666 root root` in the `gb_power` service; gb_power takes the fd from
  `ANDROID_SOCKET_gb_power`. Test builds (`-r <root>`) instead bind the path given by `-S <path>`.
* AF_UNIX `SOCK_STREAM`, all I/O non-blocking inside gb_power's poll loop. gb_power MUST never block on a client.
* Request: one ASCII line `<command>[ <arg>]\n`, at most 255 bytes including `\n`; single spaces; commands lower
  case. Longer line: reply `E2BIG` and close.
* Reply: exactly one line per request: a JSON object (RFC 8259, UTF-8, no floats, no NaN), at most 4096 bytes
  including `\n`. Non-ASCII or control bytes from `/proc` (e.g. `comm`) are replaced by `?`.
* Requests on one connection are answered in order. Max 8 simultaneous connections (the 9th gets an `EBUSY` reply and
  is closed); max 4 `watch` connections. Idle connections (no request for 30 s) are closed, except `watch`
  connections and the compute service's connection once it has sent `compute-state` (its power hold lives exactly as
  long as that connection, 9.3/D14; v1.1). Per-client output buffer 8 KiB; overflow closes the client.

### 9.2 Peer identity and roles (SO_PEERCRED)

On accept, gb_power reads `SO_PEERCRED` (uid, pid) and assigns a role, first match wins:

| Role | Rule |
|---|---|
| `root` | uid 0 |
| `shell` | uid 2000 |
| `ui` | `uid % 100000` == the appId of package `gb.sb3.gpu` **and** `/odm/app/GbGpu/GbGpu.apk` exists |
| `compute-svc` | uid 9999 (`nobody`) **and** `readlink /proc/<pid>/exe` == `/vendor/bin/gb_gpu_compute` |
| `compute-client` | `uid % 100000` is an appId listed in `/data/vendor/gb/compute_allow` |
| `other` | everything else |

**UI appId resolution:** parse `/data/system/packages.list`; the line whose first field is `gb.sb3.gpu`; the second
field is the appId (must be 10000-19999). Cache it keyed on the file's mtime and size, re-read at most every 5 s; if the
file or line is missing, no peer has role `ui`. The APK-existence condition prevents a user-installed app squatting the
name on an image without GbGpu; with the system app present, PackageManager guarantees the name.

### 9.3 Commands

| Command | Roles | Effect | Reply fields (besides `v`, `ok`, `cmd`) | Errors |
|---|---|---|---|---|
| `hello` | all | none | `api`:1, `role`, `uid`, `server`:"gb_power <build-id>" | |
| `status` | root, shell, ui | none | status object (9.5) | |
| `watch` | root, shell, ui | Sends a `status` reply now and again whenever any field except `t_ms` changes; a `{"v":1,"ok":true,"cmd":"ping"}` line every 30 s. Further input is ignored until EOF. | as `status` | `EBUSY` (watch limit) |
| `holders` | root, shell, ui | Fresh holder scan | `holders`: array of `{pid, comm, uid, user, pkg, closable}` | `ENODEV` (nouveau not loaded) |
| `enable` | root, shell, ui | v1.1: `persist.vendor.gb.nouveau=1`. **(v1.2) = `set-mode auto`** (26.2): writes `persist.vendor.gb.gfx_mode=auto` | `effect`: `now` or `next-boot`; (v1.2) the `set-mode` effect mapped: `next-attach` -> `now` (the 9.6 golden stays byte-identical) | `ENODEV` (not native, or neither `/odm/lib/modules/nouveau.ko` nor `/odm/etc/gb/dgpu_payload.sha256` exists), `EBUSY` (wedged) |
| `disable` | root, shell, ui | v1.1: `persist.vendor.gb.nouveau=0`. **(v1.2) = `set-mode intel`** | `effect`: `now` or `next-boot`; (v1.2) `pending` -> `now` | |
| (v1.2) `get-mode`, `set-mode`, `set-app-policy`, `set-app-intel`, `set-app-nvidia`, `get-apps`, `apps-begin`, `apps-add`, `apps-commit`, `run-selftest`, `list-holders`, `close-holders-and-detach`, `ui-caps` | see 26.1 | | | |
| `detach-request` | root, shell, ui | `SDTX_IOCTL_LATCH_REQUEST` (0xa525); the EC then raises the normal REQUEST event | | `ENODEV` (no DTX), `EBUSY` (a decision is running) |
| `report` | root, shell, ui | `vendor.gb.dgpu_report=1` | | |
| `stop-compute` | root, shell, ui | `ctl.stop gb_gpu_compute` | | |
| `close-holders <pid>[,<pid>...]` | root, shell, ui | For each pid that is a current holder and `closable`: queue a `force-stop` (section 12) for its package and user | `closed`: [{pid, pkg, user}], `refused`: [{pid, reason}] with reason `not-holder`, `system`, `unknown-pkg`, `self` | `EINVAL` (syntax, >16 pids), `ESTALE` (no pid accepted) |
| `compute-list` | root, shell, ui | none | `allow`: [{appId, pkg}] | |
| `compute-allow <pkg>` | root, shell, ui | Add `<appId> <pkg>` to `compute_allow` (atomic rewrite) | `appId` | `EINVAL` (pkg not `[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+`, <= 127), `ENOENT` (not in packages.list) |
| `compute-deny <pkg>` | root, shell, ui | Remove it | | `ENOENT` |
| `compute-start` | root, shell, ui, compute-client | If state is `standby` or `busy:N`: `ctl.start gb_gpu_compute` | `socket`:"/dev/socket/gb_gpu_compute", `state`: `starting` or `running` | `ENODEV` (state not standby/busy), `EBUSY` (detaching, wedged, fault) |
| `compute-state <s>` | compute-svc | `<s>` in `idle`, `loading`, `loaded`, `serving`, `unloading`. While `<s>` in loading/loaded/serving, gb_power holds `power/control=on` on the dGPU (written by its worker); `idle`/`unloading`/EOF on this connection restores `auto`. One compute-svc connection at a time (a second gets `EBUSY`). Idempotent: the service re-sends its current state every 15 s as a keep-alive (11.5); this connection is exempt from the idle close (9.1). | `hold`: 0/1 | `EINVAL`, `EBUSY` |

`closable` = holder uid % 100000 >= 10000, package resolved from packages.list, package is not `gb.sb3.gpu`, and the
pid is not the compute service (that is stopped with `stop-compute`). `user` = uid / 100000.

### 9.4 Errors

Error reply: `{"v":1,"ok":false,"cmd":"<cmd or empty>","err":"<CODE>","msg":"<short English text>"}`.

| Code | Meaning |
|---|---|
| `EPERM` | Role not allowed for this command. |
| `EINVAL` | Malformed request or argument. |
| `ENOSYS` | Unknown command. |
| `E2BIG` | Request line too long (connection closed after the reply). |
| `EBUSY` | Limit reached, or a detach decision/worker is running, or the state forbids it (wedged, fault). |
| `EAGAIN` | Transient (loading); retry after 1 s. |
| `ENODEV` | No NVIDIA stack or device: VM, no module/payload, base detached, no DTX. |
| `ENOENT` | Package not installed / not in the allowlist. |
| `ESTALE` | None of the given pids is a current closable holder. |
| `EIO` | Internal failure (property or file write failed). |

### 9.5 Status object

```json
{"v":1,"ok":true,"cmd":"status",
 "state":"busy:1","n":1,
 "holders":[{"pid":4242,"comm":"nvk_probe","uid":0,"user":0,"pkg":"","closable":false}],
 "mode":"persist","result":"clean","persist":1,"clean_boots":3,"probation":0,
 "used":1,"foreign":1,"wedged":0,"fault_reason":"",
 "bdf":"0000:02:00.0","driver":"nouveau","runtime_status":"active","power_state":"D0","refcnt":1,
 "latch":"locked","base":"attached","dtx":"ready",
 "compute":"stopped","payload":"mounted","gfxdriver":0,
 "t_ms":1234567}
```

(v1.2) The status object has these additional fields, in this order, after `gfxdriver` and before `t_ms`:

```json
 "gfx_mode":"auto","gfx_policy":"games","gfx_active":"nvidia","gfx_reason":"ok","gfx_ok":1,"gfx_test":"pass",
 "gfx_test_t":1761390000,"gfx_cap":1,"gfx_seq":3,"pending":"","detach_ref":0,"apps_sync":1,
```

Field rules: `n` = 0 unless busy; `holders` empty unless refcnt > 0 (scan rate-limited, 2.3); `refcnt` = -1 when the
module is not loaded; `bdf`/`driver`/`runtime_status`/`power_state` empty strings when no dGPU; `dtx` in `ready`,
`in-progress`, `confirmed`, `n/a`; `base` in `attached`, `detached`, `not-feasible`, `unknown`; `probation` = 1 while
`mode=persist` and `clean_boots < 3`; `gfxdriver` = 1 only with `vendor.gb.gfxdriver=on` AND `/dev/gb_dgpu/gfxdriver`
(v1.1); `t_ms` = CLOCK_MONOTONIC ms. Clients MUST ignore unknown fields.

(v1.2) Field rules for the new and redefined fields: `persist` = 1 when the effective `gfx_mode` is not `intel`;
`probation` = 1 while `mode=auto` and `clean_boots < 3`; `gfxdriver` = `gfx_cap`; `gfx_mode` = the effective mode
(`auto` for unset/invalid); `gfx_policy` = `games` or `all`; `gfx_active`/`gfx_reason` = 23.2 (gb_power's in-memory
values, not the properties); `gfx_ok` = 1 pass, 0 fail, -2 an `error` verdict for this identity, -1 no valid
record for this identity (untested or stale); `gfx_test` = 25.4; `gfx_test_t` = the record's `t` (epoch s), 0
without a valid record; `gfx_cap` = 23.1; `gfx_seq` = increments on every `gfx_active` change; `pending` = `""`,
`intel` (Intel requested while apps hold nouveau) or `detach` (`close-holders-and-detach` running); `detach_ref` = a
per-boot counter, incremented on every detach refusal because of holders and when a pending detach times out (GbGpu
posts its actionable notification when it grows, 27.3); `apps_sync` = 1 when `gfx_catalog`
was committed against the current packages.list stamp, else 0 (GbGpu then pushes the catalog, 24.3). The holder
reserve of `holders_json()` grows from 900 to 1200 bytes so the reply stays <= 4096 bytes.

### 9.6 Golden transcripts (both component 2's tests and component 6's mock MUST reproduce these)

```
C: hello\n
S: {"v":1,"ok":true,"cmd":"hello","api":1,"role":"ui","uid":10123,"server":"gb_power 1"}
C: frobnicate\n
S: {"v":1,"ok":false,"cmd":"frobnicate","err":"ENOSYS","msg":"unknown command"}
C: enable\n                       (role other)
S: {"v":1,"ok":false,"cmd":"enable","err":"EPERM","msg":"not allowed"}
C: enable\n                       (role ui, booted=1, mode=default)
S: {"v":1,"ok":true,"cmd":"enable","effect":"now"}
C: close-holders 1,abc\n
S: {"v":1,"ok":false,"cmd":"close-holders","err":"EINVAL","msg":"bad pid list"}
```

---

## 10. gb_power behaviour contract (component 2)

### 10.1 General

* Single-threaded poll loop (uevent netlink, DTX fd, listening socket, clients, worker pipe). The main process MUST
  NOT perform a dGPU sysfs write that can block: `unbind`, `remove`, `rescan`, `power/control=on` run in a forked
  worker with a deadline. Writing `power/control=auto`, `chmod`/`chown` of `/dev/dri` nodes and reading the status
  attributes are allowed in the main process; `d3cold_allowed` only for a **driverless** function (its store resumes
  the device synchronously, INFERRED from pci-sysfs; v1.1).
* The worker child re-checks the refcnt gate right before every `unbind` of a nouveau-bound function and every
  `remove` (v1.1): an unbind needs `/sys/module/nouveau/refcnt` = 0; a remove needs the function driverless and
  nouveau absent or at refcnt 0. A refused write reports `-EBUSY` (the decision then refuses, N08). An unreadable
  refcnt while nouveau is loaded counts as busy everywhere (fail closed).
* Status polling reads only `power_state`, `power/runtime_status`, `power/runtime_*_time`, `/sys/module/nouveau/refcnt`
  (plus `vendor`/`class`/`driver`/`resource` once per device appearance). It never reads config space or link
  attributes and never opens DRM nodes.
* gb_power never unbinds a nouveau-bound function on its own initiative (property flips included); that is
  `gb_nouveau.sh off`'s job. Every 10de function that is **not** class `0x03*` is kept driverless in every mode
  (unbind any driver from it). A 10de VGA function bound to a driver other than nouveau is unbound (as today).
  `-K <driver>` keeps working for tests. The one exception, a nouveau bind that appears while a detach is under way
  (EC confirmed, or latch open), is unbound only through the refcnt gate; with refcnt > 0 it stays bound and the latch
  is locked. "Under way" no longer includes `EC_IN_PROGRESS` outside a decision (the echo wait after a refusal or an
  abort, when nouveau is known to be in use; v1.1).
* Rebind after `off` (v1.1): when nouveau binds the dGPU while `/dev/gb_dgpu/off` exists and
  `persist.vendor.gb.nouveau` is not `1` (a rescan or pciehp re-created the function and the still-loaded driver
  probed it), gb_power issues `setprop ctl.start gb_nouveau_off` once per bind; the refcnt-gated unbind stays
  gb_nouveau.sh's job. **(v1.2) Replaced by `reconcile_off()` (23.6)**, keyed on `gfx_mode=intel` instead of the
  retired property (`off_rebind_check()` is removed; review B4).
* (v1.2) The graphics-switching behaviour of gb_power (derivation, node modes, Settings push, self-test scheduling,
  rebind guard, pending detach, session marker) is section 23; the new socket commands are section 26.
* Monotonic `used`: set when gb_power sees nouveau bound or `/sys/module/nouveau` present; kept in memory and mirrored to
  property + file.

### 10.2 Latch lifecycle

* `SDTX_IOCTL_LATCH_LOCK` (0xa523) / `SDTX_IOCTL_LATCH_UNLOCK` (0xa524) added to the static asserts. Both idempotent;
  `vendor.gb.dgpu.latch` mirrors the last one issued.
* LOCK when any driver is bound to any 10de function (checked after every pass and PCI uevent), when a base-side
  block device is mounted (the 10.3 step 2 test, evaluated on every tick; v1.1), and at the start of every slow-path
  decision (v1.1: the EC then cancels instead of opening if gb_power stalls mid-decision).
* UNLOCK when no driver is bound to any 10de function and no base drive is mounted; at daemon start only if
  driverless.
* Re-LOCK (if bound) after every system resume (`/sys/power/suspend_stats/success` changed) and every
  `SDTX_EVENT_BASE_CONNECTION`.
* SIGTERM: UNLOCK only if `sys.powerctl` is non-empty (shutdown/reboot), or if driverless and no base drive is
  mounted, then exit (D13). gb_power also polls `sys.powerctl` each tick and UNLOCKs as soon as it is non-empty.

### 10.3 Detach request (EC `SDTX_EVENT_REQUEST`)

1. Base not attached / not feasible: LATCH_CANCEL (unchanged).
1a. Create `/dev/gb_dgpu/detaching` (v1.1; removed when the EC process is over: cancel echo or echo timeout, latch
   closed, confirm watchdog). Then the refusals below; wedged and fault refuse as before.
1b. Load window (v1.1): if `/dev/gb_esp.lock/owner` names `gb_nouveau` with a live pid, nouveau is being staged,
   probed or health-checked: keep locked, LATCH_CANCEL, queue N15. (Flag first, then the window; gb_nouveau.sh takes
   the lock first, then looks at the flag: at least one of the two refuses.)
2. Boot-disk guard: any mounted block device (from `/proc/mounts`, resolved via `/sys/dev/block/<maj:min>`) whose
   sysfs path contains `/usb` or whose name starts with `mmcblk` counts as base-side until the Phase 2 mapping says
   otherwise. Found: keep locked, LATCH_CANCEL, notification N09 (if `gb.boot=usb`) else N10.
3. Driverless and not `used`: today's quick path (no notification); the last look before LATCH_CONFIRM also requires
   still not `used` and no load window, else the slow path. If the LATCH_CONFIRM ioctl itself fails, the detach is
   not confirmed: gb_power waits the 10 s echo time (an EC CANCEL or REQUEST event ends it earlier), then is ready
   again and removes `detaching`.
4. Otherwise (slow path; state `detaching`): LATCH_LOCK; queue N07; `ctl.stop gb_dgpu_test`, `gb_nvk_probe`,
   `gb_gpu_compute` (the first two run nvk_probe; v1.1); `chmod 000` the nouveau nodes, and every 500 ms again any
   nouveau node that appeared meanwhile; poll up to 30 s with LATCH_HEARTBEAT every <= 2 s until all three services
   read stopped, no load window is open and the refcnt gate reads 0. Not within 30 s: restore permissions, publish
   holders, queue N08 with the names (N15 if the load window is still open), LATCH_CANCEL.
   **(v1.2)** First, before the LATCH_LOCK and `nodes_lock()`: `gfx_force_intel("detaching")` (23.3: `gfx_active=intel`
   is published BEFORE any unbind, REQ:60); `gb_gfx_selftest` joins the stopped services (and the "all services
   stopped" condition; a stop of a running self-test is recorded as `interrupted`, 25.5). The refusal comes after 5 s
   instead of 30 s when every holder found is a closable app process (23.9). On a refusal that names app holders,
   gb_power increments `detach_ref` and queues the broadcast; it posts N08 only when GbGpu has not declared that it
   posts the actionable notification itself (`ui-caps notify`, 26.1; review E7). After the decision ends without a
   confirm, `gfx_active` returns to what 23.2 derives (no extra code).
5. Worker job 1 (only if a driver is bound): unbind every bound 10de function. Back in the main loop: a function that
   is bound again repeats job 1 (at most 3 times, then cancel); any GPU user service running, refcnt gate > 0 or a load
   window: refuse as in 4. Worker job 2: write `1` to each `remove` (ENOENT/ENODEV = success). The child re-checks the
   gate before every write (10.1); a refused write refuses as in 4. Parent keeps serving battery/DTX/clients and
   heartbeats every <= 2 s. A job missing its 20 s deadline: keep locked, LATCH_CANCEL, set wedged (property-less:
   file + in-memory, state `wedged`), queue N11, no further dGPU writes this boot; the stuck child is left alone.
6. Right before LATCH_CONFIRM (invariant 7, v1.1): no 10de device remains under `/sys/bus/pci/devices`, the refcnt
   gate reads 0, no load window, and the boot-disk guard (step 2) passes again; any failure refuses (N08/N15/N09/N10)
   and cancels. Then queue N12, LATCH_CONFIRM.

A second REQUEST while a decision runs is the user's abort: cancel the decision at the next safe point (never in the
middle of a worker job; the point between job 1 and job 2 counts, so an abort during job 1 removes nothing), restore
permissions, LATCH_CANCEL.

Confirm watchdog (slow path, v1.1): if the latch has not opened 10 s after LATCH_CONFIRM, gb_power queries the EC
afresh (GET_LATCH_STATUS, GET_BASE_INFO); still not open: LATCH_CANCEL and wait for its REQUEST echo (or the 10 s echo
timeout) before the cancel rescan.

### 10.4 Attach / cancel rescan (when `used` and no 10de device is present)

1. `echo on > /sys/bus/pci/devices/<port>/power/control` (worker). 2. Wait up to 4 s for the port's `power_state` D0.
3. `echo 1 > /sys/bus/pci/devices/<port>/rescan` (worker). 4. Retry at +2 s, +5 s, +15 s only while DTX reports the base
attached. 5. Restore `auto`. Never use `/sys/class/pci_bus/*/rescan`. A dGPU that reappears with a zero BAR: fault
`bar`. A dGPU that rebinds to nouveau: re-apply node permissions (7.5), and if `/dev/gb_dgpu/off` exists with
`persist` not `1`, start `gb_nouveau_off` (10.1). A driverless dGPU with result `refused:nodgpu`: section 7.9.
**(v1.2)** With `/sys/module/nouveau` present, the rescan (and any pciehp re-add) makes the kernel probe nouveau by
itself, so the rebind guard of 23.8 is armed before the `rescan` write; the "`off` flag" rule is replaced by
`reconcile_off()` (23.6) and the `refused:nodgpu` rule by the generalised hot attach (23.7).

### 10.5 Watchdog and compute hooks

Watchdog per 8.2. Before any detach slow path: `setprop ctl.stop` for `gb_dgpu_test`, `gb_nvk_probe` and
`gb_gpu_compute` (10.3 step 4); on `stop-compute`: `ctl.stop gb_gpu_compute`. The compute hold (9.3 `compute-state`)
exempts the `d0-idle` rule. (v1.2) `gb_gfx_selftest` is the fourth GPU user service (`g_gpu_users[]`); the self-test
hold (25.2) exempts the `d0-idle` rule like the compute hold.

### 10.6 Optional IOMMU domain (HW-gated)

The P3 optional `DMA-FQ` iommu group switch is implemented behind a compile-time default-off flag and a property is
**not** used to enable it. Shipping it needs the Phase 3 hardware run.

### 10.7 State broadcast to GbGpu

When the state enum or `n` changes (coalesced, at most one per 2 s, only after `sys.boot_completed=1`, only if
`/odm/app/GbGpu/GbGpu.apk` exists), gb_power queues a `broadcast` message (section 12). gb_power never forks Java tools
itself. (v1.2) Also when any of `gfx_mode`, `gfx_policy`, `gfx_active`, `gfx_reason`, `gfx_test`, `pending`,
`detach_ref`, `apps_sync`, `persist` changes (same coalescing; this also answers ux-app Q5), and once after
`sys.boot_completed=1` while `apps_sync` is 0 (24.3).

### 10.8 Options

Existing `-p -o -r -H -D -G -I -K -v` unchanged. New: `-S <path>` (test socket path, only with `-r`),
`--dtx-monitor` (read-only: enables events on its own fd, prints each event with a CLOCK_MONOTONIC timestamp, never
sends confirm/cancel/heartbeat/lock; for the EC-timeout measurement while the daemon is stopped).

---

## 11. Compute service (component 5)

### 11.1 Processes and files

| Piece | Path | Notes |
|---|---|---|
| Launcher | `/vendor/bin/gb_gpu_compute` (cpio) | Small; `dlopen("/odm/dgpu/lib64/gpu-compute/libgb_gpu_compute.so")`, calls `int gb_gpu_compute_main(int argc, char **argv)`. Exit 3 (`NO_DEVICE`) if the payload or a nouveau render node is missing. |
| Service library | `/odm/dgpu/lib64/gpu-compute/libgb_gpu_compute.so` | Front end + llama.cpp (Vulkan backend). DT_RUNPATH `$ORIGIN`. |
| Loader stub | `/odm/dgpu/lib64/gpu-compute/libgb_nvk_loader.so` | SONAME `libgb_nvk_loader.so`; never named or SONAMEd `libvulkan.so`. |
| ICD | `/odm/dgpu/lib64/hw/vulkan.nouveau.so` | Component 4's build. |

ELF NEEDED of every compute library: only LLNDK libraries of 276.R4 (`libc`, `libm`, `libdl`, `liblog`, `libnativewindow`,
`libsync`, `libvndksupport`, ...: check against the image's `/system/etc/llndk.libraries.*.txt` or equivalent),
plus `libhardware` (needed by the ICD, already verified exported on lapis), plus siblings in `gpu-compute/`.
`libc++` static.

### 11.2 Loader stub ABI

Exports every Vulkan 1.3 core command plus `vkGetInstanceProcAddr`, `vkGetDeviceProcAddr`,
`vkEnumerateInstanceVersion`, `vkEnumerateInstanceExtensionProperties`, `vkEnumerateInstanceLayerProperties`, as
trampolines (instance-level through the ICD's `vk_icdGetInstanceProcAddr`, device-level through `vkGetDeviceProcAddr`).
It negotiates with `vk_icdNegotiateLoaderICDInterfaceVersion` when exported. ICD path: env `GB_NVK_ICD` if set
(tests: lavapipe `libvulkan_lvp.so`), else `/odm/dgpu/lib64/hw/vulkan.nouveau.so`. No layers, no other ICDs.

### 11.3 Access control

* Peer uid via `SO_PEERCRED` on every accepted connection. Allowed: 0, 2000, the GbGpu appId (any user), and every
  appId in `/data/vendor/gb/compute_allow` (any user). Others: HTTP 403 and close.
* `compute_allow` format: UTF-8 lines `<appId> <package>\n`, `#` comments, written only by gb_power (atomic tmp +
  rename), re-read by the service when its mtime changes. gb_power drops lines whose appId/package pair no longer
  matches packages.list (at start and when packages.list changes).

### 11.4 Socket protocol (`/dev/socket/gb_gpu_compute`)

* Created by init (`socket gb_gpu_compute stream 0666 nobody graphics`). HTTP/1.1 over AF_UNIX stream. Requests need
  `Content-Length` for bodies (no chunked request bodies); the server answers with `Content-Length` or, for
  `"stream":true`, `text/event-stream` with `Connection: close`.
* Never TCP, also not loopback (invariant 18; D15). Internal helper sockets, if any, are AF_UNIX under
  `/data/vendor/gb/compute/`.

| Method, path | Behaviour |
|---|---|
| `GET /health` | 200 `{"status":"ok","gb_state":"idle|loading|loaded|serving"}`; 503 when no device. |
| `GET /v1/models` | 200 `{"object":"list","data":[{"id":"<id>","object":"model","owned_by":"local","gb_loaded":true|false,"size":<bytes>}]}` from `/data/vendor/gb/models/<id>.gguf`, id `[A-Za-z0-9._-]{1,64}` (others ignored). |
| `POST /v1/chat/completions` | OpenAI-compatible subset: `model` (required), `messages`, `max_tokens` (<= 4096), `temperature`, `top_p`, `stop`, `seed`, `stream`. Loads `model` on demand (unloading another one first); unknown fields ignored. |
| `POST /v1/completions` | Same with `prompt`. |
| `POST /gb/unload` | 200 `{"unloaded":true}`. |

Error body: `{"error":{"code":<http>,"type":"<type>","message":"<text>"}}`; types `invalid_request` (400), `forbidden`
(403), `not_found` (404, unknown model or path), `conflict` (409, other model requested while a request runs),
`too_large` (413: body > 1 MiB or model > 3584 MiB), `busy` (429), `gb_unavailable` (503), `internal` (500).

### 11.5 Limits and lifecycle

* One model resident; context 4096 (configurable to <= 8192 in the service config, not per request); `n_gpu_layers`
  all; CPU threads 4; 1 generation in flight, queue <= 3 more, then 429; max 4 open connections; 600 s per request;
  rc `oom_score_adjust 800`.
* Start: only via gb_power `compute-start` (clients connect to gb_power first; on ECONNREFUSED/ENOENT from the compute
  socket they call `compute-start` and retry the compute socket for up to 10 s).
* On start the service connects to gb_power and sends `compute-state idle`; `loading` before loading a model, `loaded`
  after, `serving` while a request runs, `unloading`, then `idle`. It keeps that connection open for its lifetime,
  re-sends its current state every 15 s (keep-alive; gb_power also exempts this connection from the idle close, 9.1)
  and, while the connection is down, reconnects every 2 s in every state (v1.1; before, only while a hold was
  wanted).
* 120 s with no request in flight and no open client connection: unload, `compute-state idle`, exit 0.
* Screen off for >= 30 s (i915 eDP connector `dpms` sysfs attribute, `/sys/class/drm/card0-eDP-*/dpms` = `Off`;
  INFERRED, D16; disabled if the attribute is missing): unload the model (stay running until the idle exit).
* SIGTERM (gb_power `ctl.stop` before detach/off): stop accepting, abort generation, free Vulkan objects, exit 0
  within 5 s.
* Environment (rc `setenv`): `MESA_SHADER_CACHE_DIR=/data/vendor/gb/compute/cache`.
* Exit codes: 0 normal, 1 error, 3 `NO_DEVICE`, 4 configuration error.

### 11.6 `vendor.gb.compute.state` (gb_power derives it)

`stopped` if `init.svc.gb_gpu_compute` is not `running`; `starting` if running and no compute-svc connection;
otherwise the last `compute-state` value.

---

## 12. Notifications

### 12.1 Queue and `gb_notify` (component 1 provides the service; all daemons use it)

Daemons never run `cmd`/`am` themselves (D6). They drop a message and set a trigger property; the `gb_notify` service
runs as **user shell** (the identity `adb shell cmd notification post` uses) and delivers.

* Message file: `/data/vendor/gb/notify/<epoch_ms 13 digits>-<pid>-<n>.msg`, mode 0644, written as `.tmp` then
  renamed. `notify/` is group-shell writable (gb_notify deletes): the `.tmp` is created exclusively without following
  a symlink (`O_CREAT|O_EXCL|O_NOFOLLOW` + `fchmod`; mksh `set -C` with `umask 022`), never chmodded by path, and
  renamed with `mv -f -T` (5.1). Content: `key=value` lines, one value per line, values <= 240 bytes, no newlines
  inside values:
  * `kind=notify` with `tag=`, `title=`, `text=`;
  * `kind=broadcast` (no other keys);
  * `kind=force-stop` with `pkg=` and `user=` (decimal);
  * (v1.2) `kind=settings` with `key=` and either `value=` or `src=` (24.6). Only these three pairs exist:
    `key=updatable_driver_all_apps` + `value=0` or `value=3`; `key=updatable_driver_production_opt_in_apps` +
    `src=gfx_optin`; `key=updatable_driver_production_opt_out_apps` + `src=gfx_optout`.
* Then `setprop vendor.gb.notify <epoch_ms>`. gb_power queues in memory until `sys.boot_completed=1` (max 8, newest
  per tag kept) and until `/data/vendor/gb/notify` exists. (v1.2) The in-memory queue grows to 16 entries; settings
  messages use the keys `set:all_apps`, `set:opt_in`, `set:opt_out` (newest wins, like a tag).
* `gb_notify.sh`: process files in name order until the directory is empty (re-scan after each batch), deleting each
  after the attempt. Validate: `tag` in {`gb_dgpu`, `gb_dtx`, `gb_dgpu_test`}; `pkg` regex as in 9.3; titles/texts
  printable ASCII. Delivery:
  * notify: `cmd notification post -t "<title>" "<tag>" "<text>"` (same tag replaces the previous one);
  * broadcast: `am broadcast --user 0 --include-stopped-packages -a gb.sb3.gpu.action.STATE_CHANGED -p gb.sb3.gpu`
    (v1.1: also reaches a force-stopped GbGpu; the intent stays explicit);
  * force-stop: `am force-stop --user <user> <pkg>`;
  * (v1.2) settings: `value=` -> `timeout 20 cmd settings put global updatable_driver_all_apps <0|3>`; `src=` -> read
    `/data/vendor/gb/<src>` only if it is a regular file (not a symlink), owned by uid 0, <= 64 KiB, with <= 512 lines
    (`gfx_optin`) or <= 128 lines (`gfx_optout`); drop invalid lines (`valid_pkg`) with a log line, de-duplicate, join
    with `,`; non-empty -> `timeout 20 cmd settings put global <key> <list>`, empty -> `timeout 20 cmd settings delete
    global <key>`. Any other key, value, src or pairing: logged and dropped. No other Settings key can ever be written
    through the queue.
  (v1.2) The tag whitelist gains `gb_gfx`.
  Failures are logged to kmsg (`gb_notify: `) and dropped.

### 12.2 Catalog (exact strings; `<...>` substituted, truncated to fit 200 characters)

| Id | Tag | Title | Text | Posted by |
|---|---|---|---|---|
| N01 | gb_dgpu | NVIDIA GPU switched off | The NVIDIA driver did not start cleanly last time, so it was switched off. You can turn it on again in Quick Settings. | gb_nouveau.sh (crash-loop) |
| N02 | gb_dgpu | NVIDIA GPU problem | The NVIDIA driver started with errors (<why>). It stays off after the next restart. | gb_nouveau.sh (unhealthy) |
| N03 | gb_dgpu | NVIDIA GPU is in use | Close <holders> to switch the NVIDIA GPU off. | gb_nouveau.sh (off refused) |
| N04 | gb_dgpu | Restart to finish | The NVIDIA GPU is off. Restart to unload its driver completely. | gb_nouveau.sh (off, foreign) |
| N05 | gb_dgpu | Restart required | The NVIDIA GPU stopped responding. Restart before detaching the screen. | gb_nouveau.sh (off wedged) |
| N06 | gb_dgpu | NVIDIA GPU fault | The NVIDIA GPU is not responding to power management (<reason>). Detaching is blocked until restart. | gb_power |
| N07 | gb_dtx | Preparing to detach | Getting the NVIDIA GPU ready. Keep the screen attached. | gb_power |
| N08 | gb_dtx | Can't detach yet | The NVIDIA GPU is in use by <holders>. Close them, then press detach again. | gb_power |
| N09 | gb_dtx | Can't detach | The system is running from a drive in the keyboard base. Shut down before detaching. | gb_power |
| N10 | gb_dtx | Can't detach | A USB or SD drive in the keyboard base is in use. Eject it, then press detach again. | gb_power |
| N11 | gb_dtx | Restart required to detach | The NVIDIA GPU did not shut down in time. Restart, then detach. | gb_power |
| N12 | gb_dtx | Ready to detach | You can remove the screen now. | gb_power (slow path only) |
| N13 | gb_dgpu_test | NVIDIA test: suspend | Close the lid for 30 s, then open it. Do this 3 times within 5 minutes. | gb_dgpu_report.sh test |
| N14 | gb_dgpu_test | NVIDIA test finished | Wait until 15 minutes after start-up, then restart. Results are in the gblog folder on the boot partition. | gb_dgpu_report.sh test |
| N15 | gb_dtx | Can't detach yet | The NVIDIA driver is starting. Wait a minute, then press detach again. | gb_power (load window open, v1.1; v1.2 also during the rebind guard) |

(v1.2) Changed and new rows (they replace the rows of the same id above; ids N16-N20 are new, so N15 keeps its v1.1
meaning; review B2):

| Id | Tag | Title | Text | Posted by |
|---|---|---|---|---|
| N01 | gb_dgpu | Switched to Intel graphics | The NVIDIA driver did not start cleanly last time, so graphics were set to Intel only. To try again, choose Auto in Settings > System > Graphics. | gb_nouveau.sh (crash-loop) |
| N02 | gb_dgpu | NVIDIA GPU problem | The NVIDIA driver started with errors (<why>). Graphics are set to Intel only. | gb_nouveau.sh (unhealthy, bind path), gb_power (`<why>` = `rebind`, 23.8) |
| N03 | gb_dgpu | NVIDIA GPU is in use | Close <holders> to finish switching to Intel graphics. | gb_nouveau.sh (off refused) |
| N04 | | | (not posted in v1.2; 7.3) | |
| N06 | gb_dgpu | NVIDIA GPU fault | unchanged; posted only for the faults that block a detach (`rpm-error`, `d0-idle`; gb-power Q1) | gb_power |
| N08 | gb_dtx | Can't detach yet | unchanged | gb_power, only while GbGpu has not declared `ui-caps notify` this boot (10.3 step 4) |
| N16 | gb_gfx | Performance base connected | NVIDIA graphics on for apps you open now. Restart apps that are already open to switch them. | gb_power: the first `gfx_active=nvidia` after a base attach event of this boot, or right after a self-test pass (23.3) |
| N17 | gb_gfx | Tablet mode - Intel graphics | Apps use Intel graphics while the keyboard base is detached. Reattach it to use NVIDIA graphics again. | gb_power: base attached -> detached after a confirmed detach, mode `auto`/`nvidia`, `gfx_cap` 1 (23.9) |
| N18 | gb_gfx | NVIDIA app graphics off | The NVIDIA graphics self-test failed (<reason>), so apps stay on Intel graphics. The NVIDIA compute service still works. See Settings > System > Graphics. | gb_power: a `fail` verdict, once per identity (25.4) |
| N19 | gb_gfx | Switched to Intel graphics | The system restarted unexpectedly twice while apps used NVIDIA graphics, so graphics were set to Intel only. Choose Auto in Settings > System > Graphics to try again. | gb_power (23.10) |
| N20 | gb_dgpu | NVIDIA GPU not available | The NVIDIA GPU did not start correctly (<reason>). Apps use Intel graphics until restart. | gb_power: non-blocking faults `rescan`, `bar`, `selftest-hang` (8.2, 25.6) |

`<holders>` = comma-separated app labels or `comm` names (max 5, then `and N more`). `<why>` = `bind`, `d3cold` or
`dmesg`. `<reason>` = `fault_reason`. (v1.2) `<why>` adds `rebind`; N18's `<reason>` is the record's `reason`
(<= 40 characters). All `gb_gfx` posts replace each other (one tag), so attach/detach cycles never stack.

Shell-posted notifications appear under the Shell app's notification channel (platform behaviour); that is accepted.

### 12.3 GbGpu's own notifications

| Channel id | Name | Importance | Notification |
|---|---|---|---|
| `gb_gpu_status` | NVIDIA GPU status | LOW | id 1, ongoing while state is `busy:N`: title `NVIDIA GPU in use`, text `<N> app(s) are using the NVIDIA GPU`; cancelled otherwise. |
| `gb_gpu_alerts` | NVIDIA GPU alerts | HIGH | id 2, only for results of UI actions (e.g. `close-holders` refused). |
| (v1.2) `gb_gpu_status` (changed text) | NVIDIA GPU status | LOW | id 1, ongoing while `state` is `busy:N`: title `NVIDIA GPU in use`, text `NVIDIA in use by: <labels>` (app label, else package, else comm; max 5, then `and N more`), action **Close apps** (HoldersActivity mode `close`). REQ:71. |
| (v1.2) `gb_gpu_detach` (new) | Detach | HIGH | id 3, posted when `detach_ref` grows beyond the value the app saw last (kept in its SharedPreferences): title `Can't detach yet`, text `<labels> are using the NVIDIA GPU. Close them to detach the screen.`, actions **Close apps and detach** (HoldersActivity mode `detach`) and **Cancel** (non-exported `.ActionReceiver`, cancels id 3 only). Cancelled when `base` is not `attached` or `state` is no longer `busy:N`. D6. |

Triggered by the state broadcast (10.7): the receiver queries `status` and updates. The app never duplicates N01-N14.
(v1.2) Except: when the app has declared `ui-caps notify` (26.1), its `gb_gpu_detach` notification replaces N08.

---

## 13. Android packages

### 13.1 Common rules

* No `com.google.*`/`com.android.*`/`com.nvidia.*` names, no NVIDIA logos or vendor artwork; label text "NVIDIA GPU"
  is descriptive only.
* Build without Gradle or Maven (no such downloads are allowed): `aapt2` + `javac` (JDK 17, compiling against
  `android.jar` as the bootclasspath, class-file level accepted by the `d8` in use) + `d8` + `zipalign` + `apksigner`,
  against `android.jar` API 36 (or 37 if installed via `sdkmanager`). `minSdkVersion 36`, `targetSdkVersion 36`.
  No AndroidX.
* `versionCode` = minutes since the Unix epoch at build time (UTC); `versionName` = `1.0.<versionCode>`.
* Signing (APK Signature Scheme v2 + v3): keys live in `builder:~/keys/` (dir 0700, files 0600), generated once by
  the component's build script if missing (`keytool -genkeypair -storetype PKCS12 -keyalg RSA -keysize 3072 -validity
  10000`), never regenerated if present, backed up to `C:\GB-build\keys\` (same file names). Never inside any
  repository, component directory, `/build/dist*` or an image.

| Package | APK | Key store | Alias | Password file |
|---|---|---|---|---|
| `gb.sb3.gpu` | `/odm/app/GbGpu/GbGpu.apk` | `~/keys/gbgpu.p12` | `gbgpu` | `~/keys/gbgpu.pass` (32 random hex chars) |
| `gb.sb3.gfxdriver` | `/odm/app/GbGfxDriver/GbGfxDriver.apk` | `~/keys/gbgfxdriver.p12` | `gbgfxdriver` | `~/keys/gbgfxdriver.pass` |

### 13.2 GbGpu (component 6)

* Non-privileged system app on `/odm/app` (SELinux domain `untrusted_app`, permissive). Permissions:
  `android.permission.POST_NOTIFICATIONS` (granted by `gb_gpu_ui_setup.sh` via `pm grant`), nothing privileged.
* Components (class names relative to `gb.sb3.gpu`):
  * `.GpuTileService`: QS tile, label `NVIDIA GPU`, meta-data `android.service.quicksettings.TOGGLEABLE_TILE=true`
    and `android.service.quicksettings.ACTIVE_TILE=true`; uses `watch` while listening; state per 8.3.
  * `.DetachTileService` (optional): label `Detach screen`; tap -> `detach-request`; UNAVAILABLE when `base` is not
    `attached` or `dtx` is `n/a`.
  * `.SettingsActivity`: exported, intent filter `com.android.settings.action.IA_SETTINGS`, meta-data
    `com.android.settings.category=com.android.settings.category.ia.system`, `com.android.settings.title=NVIDIA GPU`,
    `com.android.settings.switch_uri=content://gb.sb3.gpu.switches/nvidia_gpu` (injected Settings switch; system apps
    only, `TileUtils.java:324-342`). Shows state, probation, holders, compute allowlist.
  * `.SwitchProvider`: authority `gb.sb3.gpu.switches`, exported, `android:permission="android.permission.WRITE_SECURE_SETTINGS"`.
  * `.StateReceiver`: exported, `android:permission="android.permission.DUMP"` (only shell/root can send),
    action `gb.sb3.gpu.action.STATE_CHANGED`.
  * `.HoldersActivity`: dialog title `Close apps using the NVIDIA GPU?`, text
    `These apps will be closed. Unsaved work in them may be lost:` + list, buttons `Close apps` / `Cancel`.
* Transport: `android.net.LocalSocket` to `/dev/socket/gb_power` (`LocalSocketAddress.Namespace.FILESYSTEM`), behind
  a `Transport` interface so host tests can use `java.nio` UNIX sockets.
* `gb_gpu_ui_setup.sh` (service `gb_gpu_ui_setup`, user shell, at `sys.boot_completed=1`): `pm grant gb.sb3.gpu
  android.permission.POST_NOTIFICATIONS` (ignore failure); if `/data/vendor/gb/ui/tile_added` is absent (v1.1 path; the
  directory is shell's own, created by `gb_gpu_ui.rc`): `cmd statusbar add-tile gb.sb3.gpu/.GpuTileService` and create
  the marker (one time only; a user removal is respected).

**(v1.2) GbGpu changes** (details in section 27; the component names stay, so the QS tile a user added stays added):
* Permissions: `POST_NOTIFICATIONS` (as v1.1) plus `android.permission.QUERY_ALL_PACKAGES` (normal protection level,
  granted at install; D3) for the app catalog (24.3) and holder labels (ux-app Q8). Still nothing privileged.
* `.GpuTileService`: label `Graphics` (was `NVIDIA GPU`), mapping 27.1; tap cycles the mode (no dialog); long-press
  opens the Graphics page (the existing `QS_TILE_PREFERENCES` filter).
* `.SettingsActivity`: the Graphics page (27.2). Injection meta-data: category
  `com.android.settings.category.ia.system` (**verified on lapis 276.R4**: Settings has the key, and lapis's own
  `com.google.android.factory.diagnostics/.SettingsDiagnosticsActivity` injects into it; `1b\evidence\
  vm_readonly_2026-10-25.md` E2), `com.android.settings.title=Graphics`, `com.android.settings.keyhint=graphics`,
  `com.android.settings.summary_uri=content://gb.sb3.gpu.switches` (dynamic summary `<Mode label>: <tile subtitle>`),
  `com.android.settings.icon`; **no** `switch_uri` (three modes do not fit a switch). No launcher icon (the fallback
  of D7 is not needed because the category exists); Settings search finds the entry by its title (settingslib indexes
  injected tiles; INFERRED, UI-1B-HW-1).
* `.SwitchProvider`: serves only `getDynamicSummary` (and `getDynamicTitle`); `isChecked`/`onCheckedChanged` go.
* `.HoldersActivity`: modes `close`, `intel`, `detach` (27.4).
* New `.ActionReceiver` (`android:exported="false"`): the notification's **Cancel** action.
* The app pushes its app catalog to gb_power (24.3) whenever the status says `apps_sync` = 0.

### 13.3 GbGfxDriver (component 4, P8 gated)

**(v1.2)** No longer gated: shipped on every native image (`ro.gfx.driver.0` from build.prop). Label becomes
`android:label="NVIDIA app graphics"`. The APK **MUST NOT** contain `assets/allowlist.txt` (24.5: GpuService would
otherwise fill `updatable_driver_production_allowlist` from it, and a `*` would hand the driver to every app that is
not opted out). Contents otherwise unchanged; only the shim changes (14).

* `AndroidManifest.xml`: package `gb.sb3.gfxdriver`, `<application android:hasCode="false"
  android:extractNativeLibs="false" android:label="NVIDIA app graphics (experimental)">` with
  `<meta-data android:name="com.android.graphics.driver.build_time" android:value="L<epoch seconds>"/>`.
* Contents: `lib/x86_64/vulkan.intel.so` (shim, SONAME `vulkan.intel.so`), `lib/x86_64/libgb_nvk.so` (NVK, SONAME
  `libgb_nvk.so`), `assets/sphal_libraries.txt` (one library name per line; empty if the shim needs none). Libraries
  stored uncompressed, page aligned (`zipalign -P 16` if the build-tools support it, else `-p`; x86_64 pages are
  4 KiB), linked with `-Wl,-z,max-page-size=16384`.
* GraphicsEnvironment requires `MATCH_SYSTEM_ONLY` (system app), targetSdk >= 26 and a compatible ABI
  (`a17 GraphicsEnvironment.java:1046-1072`); the per-app selection never applies to privileged or non-updated system
  apps (`:952`).

---

## 14. Per-app driver shim and NVK bundling (P8, component 4)

**(v1.2) Shim rules (D5); they replace the third and the last bullet below:**
1. **Read point.** `gb_CreateInstance()` reads `vendor.gb.gfx_active` once with `gb_get_prop()` and keeps
   `want_nvk` = (value is exactly `nvidia`) in the instance. A later property change never affects an existing
   instance; a new instance reads again. `vendor.gb.gfxdriver` is no longer read.
2. **Engine rule (unchanged, mandatory).** `pApplicationInfo` NULL, or `pEngineName` or `pApplicationName` equal to
   `android framework` (HWUI sets both, `a17 VulkanManager.cpp:133-136`): platform driver (ANV) only, NVK never loaded.
3. **ANGLE-HWUI guard (new, mandatory).** If `debug.hwui.renderer` is set and does not start with `skiavk`, or it is
   unset and `ro.hwui.use_vulkan` is not `true`, no instance of the process ever gets NVK (logged once). With HWUI on
   GL, HWUI reaches Vulkan through ANGLE with engine `ANGLE`, which the engine rule cannot tell from the app's own
   ANGLE, and ANGLE takes the first discrete GPU when no device is preferred (`angle vulkan_icd.cpp:371-376`). Native
   lapis has `ro.hwui.use_vulkan=true` (`vendor_native_build.prop:45`), so the guard is inert there; the VM has
   `false` / `skiagl` (evidence E1), where the package never exists anyway.
4. **Gate.** At the first `vkEnumeratePhysicalDevices*` of the instance: `want_nvk`, the guard of 3, then
   `gb_nouveau_ready()` (unchanged: a `/sys/bus/pci/drivers/nouveau/0000:*` entry exists and the nouveau render node
   opens as the app's uid). The node opens only while gb_power raised it to 0666 (7.5), i.e. only while gb_power
   itself derived `nvidia`, so a spoofed property gains nothing.
5. **Order.** `gfx_active=nvidia` and NVK enumerated >= 1 device: NVK devices FIRST, then the platform devices (D5,
   REQ:47-49); `vkEnumeratePhysicalDeviceGroups` lists the NVK groups first. Otherwise: platform devices only. This
   replaces "ANV ... always physical device 0".
6. **Never fatal (unchanged).** Any NVK failure leaves the instance with the platform devices only and no error.
7. `ro.gfx.driver.0` comes from the native build.prop (4); `gb_gfxdriver.sh`/`.rc` are not shipped. Which apps get
   the package at all is section 24 (Settings), not the shim.

* Name: `vulkan.<ro.hardware.vulkan>.so`. Native lapis and the VM both report `ro.hardware.vulkan=intel` (VM getprop
  276.R4), so one build `vulkan.intel.so` serves both (D12). Exports `HMI` (`HAL_MODULE_INFO_SYM`) with
  `common.id = "vulkan"`.
* Loads the platform HAL with `android_load_sphal_library("vulkan.intel.so", RTLD_LOCAL | RTLD_NOW)` (libvndksupport,
  LLNDK). The shim's own soname shadows that name inside the driver namespace, so `dlopen` by name is wrong.
* ANV (or the VM HAL) is always present and always physical device 0. NVK (`libgb_nvk.so` from the APK lib dir) is
  added only when all hold: `vendor.gb.gfxdriver` = `on`; the instance's `pApplicationInfo` is non-NULL and neither
  `pEngineName` nor `pApplicationName` equals `android framework` (HWUI uses both, `a17 VulkanManager.cpp:133-135`);
  a directory entry `/sys/bus/pci/drivers/nouveau/0000:*` exists; the nouveau render node (2.2) opens. NVK is
  enumerated lazily at the first `vkEnumeratePhysicalDevices*` and appended after ANV.
* Every physical-device-level entry point is a trampoline dispatching by handle owner. Any NVK failure (load, instance,
  enumeration, device creation) silently leaves the app on ANV; the shim never aborts, never returns an error caused
  only by NVK. The NVK instance is destroyed (and `libgb_nvk.so` unloaded) when the last app instance is destroyed.
* NVK build: one Mesa tree, patches 0001, 0002, 0004, 0005, 0006 (0003 AHB only for 8b). The payload ICD
  (`vulkan.nouveau.so`, SONAME `vulkan.nouveau.so`) and `libgb_nvk.so` come from the same build; the second soname via
  a second link step or `patchelf --set-soname`, verified with `readelf -d`.
* `ro.gfx.driver.0` is set only by `gb_gfxdriver.sh` (exec at `post-fs-data`, before zygote) when the cmdline has
  `gb.gfxdriver=1` and the APK exists; it also sets `vendor.gb.gfxdriver=on` (else `off`). Apps opt in via
  `Settings.Global updatable_driver_production_opt_in_apps`.

---

## 15. Init services and rc triggers

All services: `seclabel u:r:shell:s0` (existing permissive domain; no new SELinux types, invariant 20).

### 15.1 `gb_nouveau.rc` (component 1, exact)

**(v1.2) The exact v1.2 text is 15.1-v12 below; the v1.1 block after it is superseded** (its two
`persist.vendor.gb.nouveau` triggers are removed; v1.2 adds the `selftest/` directory, the `gb_gfx_selftest`
service and three `gfx_mode` triggers).

#### 15.1-v12 (exact; the content below the file's comment header)

```
on early-init
    mkdir /dev/gb_dgpu 0700 root root

on post-fs-data
    mkdir /data/vendor/gb 0751 root shell
    mkdir /data/vendor/gb/report 0750 root shell
    mkdir /data/vendor/gb/notify 0770 root shell
    mkdir /data/vendor/gb/payload 0700 root root
    mkdir /data/vendor/gb/selftest 0700 root root

service gb_nouveau /system/bin/sh /vendor/bin/gb_nouveau.sh auto
    user root
    group root shell
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_nouveau_off /system/bin/sh /vendor/bin/gb_nouveau.sh off
    user root
    group root shell
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_dgpu_dmesg /system/bin/sh /vendor/bin/gb_nouveau.sh dmesg-stream
    user root
    group root shell log
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_dgpu_report /system/bin/sh /vendor/bin/gb_dgpu_report.sh report
    user root
    group root shell log readproc graphics
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_dgpu_test /system/bin/sh /vendor/bin/gb_dgpu_report.sh test
    user root
    group root shell log readproc graphics
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_gfx_selftest /system/bin/sh /vendor/bin/gb_dgpu_report.sh selftest
    user root
    group root shell log readproc graphics
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_notify /system/bin/sh /vendor/bin/gb_notify.sh
    user shell
    group shell
    seclabel u:r:shell:s0
    oneshot
    disabled

on property:sys.boot_completed=1
    start gb_nouveau

on property:vendor.gb.nouveau.booted=1 && property:persist.vendor.gb.gfx_mode=auto
    start gb_nouveau

on property:vendor.gb.nouveau.booted=1 && property:persist.vendor.gb.gfx_mode=nvidia
    start gb_nouveau

on property:vendor.gb.nouveau.booted=1 && property:persist.vendor.gb.gfx_mode=intel
    start gb_nouveau_off

on property:vendor.gb.dgpu_report=1
    setprop vendor.gb.dgpu_report 0
    start gb_dgpu_report

on property:vendor.gb.dgpu_report=2
    setprop vendor.gb.dgpu_report 0
    start gb_dgpu_test

on property:vendor.gb.dgpu.dmesg=1
    start gb_dgpu_dmesg

on property:vendor.gb.dgpu.dmesg=0
    stop gb_dgpu_dmesg

on property:vendor.gb.notify=*
    start gb_notify
```

7 services. An unset `gfx_mode` (the default, auto) needs no trigger: the boot-time `start gb_nouveau` reads it as
`auto`, and `set-mode` always writes an explicit value. `gb_gfx_selftest` is started only by gb_power
(`ctl.start`) and stopped by gb_power and `gb_nouveau.sh off` (`ctl.stop`).

#### 15.1 v1.1 text (superseded)

```
on early-init
    mkdir /dev/gb_dgpu 0700 root root

on post-fs-data
    mkdir /data/vendor/gb 0751 root shell
    mkdir /data/vendor/gb/report 0750 root shell
    mkdir /data/vendor/gb/notify 0770 root shell
    mkdir /data/vendor/gb/payload 0700 root root

service gb_nouveau /system/bin/sh /vendor/bin/gb_nouveau.sh auto
    user root
    group root shell
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_nouveau_off /system/bin/sh /vendor/bin/gb_nouveau.sh off
    user root
    group root shell
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_dgpu_dmesg /system/bin/sh /vendor/bin/gb_nouveau.sh dmesg-stream
    user root
    group root shell log
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_dgpu_report /system/bin/sh /vendor/bin/gb_dgpu_report.sh report
    user root
    group root shell log readproc graphics
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_dgpu_test /system/bin/sh /vendor/bin/gb_dgpu_report.sh test
    user root
    group root shell log readproc graphics
    seclabel u:r:shell:s0
    oneshot
    disabled

service gb_notify /system/bin/sh /vendor/bin/gb_notify.sh
    user shell
    group shell
    seclabel u:r:shell:s0
    oneshot
    disabled

on property:sys.boot_completed=1
    start gb_nouveau

on property:vendor.gb.nouveau.booted=1 && property:persist.vendor.gb.nouveau=1
    start gb_nouveau

on property:vendor.gb.nouveau.booted=1 && property:persist.vendor.gb.nouveau=0
    start gb_nouveau_off

on property:vendor.gb.dgpu_report=1
    setprop vendor.gb.dgpu_report 0
    start gb_dgpu_report

on property:vendor.gb.dgpu_report=2
    setprop vendor.gb.dgpu_report 0
    start gb_dgpu_test

on property:vendor.gb.dgpu.dmesg=1
    start gb_dgpu_dmesg

on property:vendor.gb.dgpu.dmesg=0
    stop gb_dgpu_dmesg

on property:vendor.gb.notify=*
    start gb_notify
```

The dmesg stream is its own service because init kills a oneshot service's whole process group when its main process
exits (INFERRED from init's process-group handling; D21), so a `dmesg -w &` inside `gb_nouveau.sh` would die with it.

### 15.2 `gb_power.rc` (component 2): changes to the existing file

* Add `socket gb_power stream 0666 root root` to `service gb_power`.
* In `on early-init`, before `start gb_power`: `mkdir /dev/gb_dgpu 0700 root root`.
* Everything else unchanged.

### 15.3 `gb_gpu_compute.rc` (component 5, exact)

```
on post-fs-data
    mkdir /data/vendor/gb 0751 root shell
    mkdir /data/vendor/gb/models 0775 root shell
    mkdir /data/vendor/gb/compute 0700 nobody graphics

service gb_gpu_compute /vendor/bin/gb_gpu_compute
    user nobody
    group graphics
    seclabel u:r:shell:s0
    socket gb_gpu_compute stream 0666 nobody graphics
    setenv MESA_SHADER_CACHE_DIR /data/vendor/gb/compute/cache
    oom_score_adjust 800
    oneshot
    disabled
```

Started only by gb_power (`ctl.start`), stopped by gb_power or gb_nouveau.sh (`ctl.stop`).

### 15.4 `gb_gpu_ui.rc` (component 6, exact)

```
service gb_gpu_ui_setup /system/bin/sh /vendor/bin/gb_gpu_ui_setup.sh
    user shell
    group shell
    seclabel u:r:shell:s0
    oneshot
    disabled

on post-fs-data
    mkdir /data/vendor/gb 0751 root shell
    mkdir /data/vendor/gb/ui 0700 shell shell

on property:sys.boot_completed=1
    start gb_gpu_ui_setup
```

### 15.5 `gb_nvk.rc` (component 4): change to the existing file

In `on property:vendor.gb.nvk_probe=1`, before `start gb_nvk_probe`, add `setprop vendor.gb.nouveau.foreign 1` and
`write /dev/gb_dgpu/foreign 1`. v1.1: `service gb_nvk_probe` runs as `user shell` with `group shell graphics` (not
root: the nouveau render node is 0660 root:graphics, and gb_power's / `off`'s chmod 000 only keeps a non-root opener
out), and `on post-fs-data` creates `mkdir /data/vendor/nvk 0770 shell shell` (the probe writes its output there, so no
root process writes through a shell-controlled path). Wave 2 switches `nvk.json` from `rc_text` to `rc_files`
pointing at this file.

### 15.6 `gb_gfxdriver.rc` (component 4, P8 only, exact)

**(v1.2) Retired: not shipped** (`ro.gfx.driver.0` comes from build.prop, which init loads before any service could
set a ro property; `vendor.gb.gfxdriver` and `/dev/gb_dgpu/gfxdriver` are retired). v1.1 text:

```
service gb_gfxdriver_setup /system/bin/sh /vendor/bin/gb_gfxdriver.sh
    user root
    group root
    seclabel u:r:shell:s0
    oneshot
    disabled

on post-fs-data
    exec_start gb_gfxdriver_setup
```

### 15.7 Service summary

| Service | User | Started by | Notes |
|---|---|---|---|
| `gb_power` | root | early-init (existing) | + control socket |
| `gb_nouveau` | root | boot_completed; runtime enable; gb_power hot-attach (`ctl.start`); (v1.2) `booted=1 && gfx_mode=auto` or `=nvidia`; gb_power deferred hot attach (23.7) | |
| `gb_nouveau_off` | root | runtime disable; gb_power after a re-bind with `/dev/gb_dgpu/off` (10.1); (v1.2) `booted=1 && gfx_mode=intel`; gb_power `reconcile_off()` (23.6) | |
| `gb_gfx_selftest` (v1.2) | root | gb_power `ctl.start` only (25.2) | ESP lock, crash marker; stopped by gb_power (detach, deadline, Intel switch) and `off` |
| `gb_dgpu_dmesg` | root | `vendor.gb.dgpu.dmesg` | |
| `gb_dgpu_report` / `gb_dgpu_test` | root | `vendor.gb.dgpu_report=1/2` | |
| `gb_notify` | shell | `vendor.gb.notify` | |
| `gb_nvk_probe` | shell (group graphics; v1.1) | `vendor.gb.nvk_probe=1` (existing) | stopped by gb_power (detach) and `off` |
| `gb_gpu_compute` | nobody | gb_power `ctl.start` | payload only |
| `gb_gpu_ui_setup` | shell | boot_completed | |
| `gb_gfxdriver_setup` | root | post-fs-data exec | P8 builds only; **(v1.2) retired, not shipped** |

---

## 16. Boot entries

* `loader/entries/gb-<x>-nvtest.conf` (x = slot letter, lower case): identical to `gb-<x>.conf` except
  `title   <base title> (slot <X>) [NVIDIA test]` and `options <base options> gb.nouveau=test`. Written by build_esp for
  native ESPs (USB, internal, ISO payloads) and replicated for the other slot by gb_slotsync (whose title rewrite keys
  on `(slot `). Never the default entry. Not in the VM ESP.
* `loader/entries/gb-<x>-gfxtest.conf` (P8 builds only): title suffix `[NVIDIA app graphics test]`, options
  `<base options> gb.nouveau=once gb.gfxdriver=1`. **(v1.2) Retired:** build_esp writes no gfxtest entry and
  gb_slotsync copies only `-nvtest` (integration `gfx-switching.md` W2G-04). App graphics are gated by the automatic
  self-test (25) instead.
* Recovery without a shell: in the systemd-boot menu press `e` on the normal entry and append `gb.nouveau=0` (kill
  switch) or `gb.nouveau=once` (one-time load). `loader.conf` keeps `editor yes`.

---

## 17. SELinux and DAC summary

* No new SELinux types or domains. All services run in `u:r:shell:s0`; GbGpu runs as `untrusted_app` and GbGfxDriver
  users as their own app domains; all of these are permissive in `inject\odm_sepolicy.cil` (verified:
  `typepermissive shell`, `untrusted_app`, `platform_app`, `surfaceflinger`, `system_server`). If a component needs
  anything else it MUST say so in its integration file instead of assuming it.
* The kernel enforces DAC: `/dev/dri` nouveau nodes per 7.5; sockets 0666 with SO_PEERCRED checks; `/data/vendor/gb`
  per 5.1.
* (v1.2) Every third-party app the Settings of section 24 select loads the shim and, while `gfx_active=nvidia`, opens
  the nouveau render node and reads `vendor.gb.gfx_active`. Apps with an old targetSdk run in
  `untrusted_app_25/27/29/30/32`, which are **not** listed as permissive; there the property read or the node open
  may be denied (the shim then stays on ANV: never fatal). Checking this is the HW item H-1B-12 (review E4); widening
  the permissive set is a Wave 2 decision (W2-15). `gb_gfx_selftest` runs in `u:r:shell:s0`; Settings are written by
  `gb_notify` (shell, which holds `WRITE_SECURE_SETTINGS`). No new type.

---

## 18. Per-component scope and acceptance tests

Test ids are used in the integration files and the final report (`PASS`/`FAIL`/`SKIP` + evidence path). `HW` tests are
listed so that the README can mark them; they are not run in Wave 1. "Wave 2" tests need the shared pipeline or the VM
(VM boots touch `lapis.bin` and need the user's approval).

(v1.2) The wave 1b scope, tasks and acceptance tests (`NP-1B-*`, `GP-1B-*`, `GD-1B-*`, `UI-1B-*`, `VM-1B-*`,
`H-1B-*`) are in `1b\TASKS_1B.md` v1.2, rebased onto the v1.1 code by function name and current line. The wave-1
tests below keep running; the assertions v1.2 changes are listed per test there (persist triggers, `MODE=persist`,
gfxdriver 0666/foreign, the 8.3 tile mapping, the `enable`/`disable` property).

### 18.1 nouveau-policy (component 1)

Scope: rewrite `gb_nouveau.sh` (auto/off/dmesg-stream) and `gb_nouveau.rc` (15.1); new `gb_dgpu_report.sh`,
`gb_notify.sh`; update `gb_dgpu_check.sh` (no hard-coded BDFs, read-only); `payload/make_dgpu_payload.sh` (+
`erofs_ls.py`, `test_make_dgpu_payload.sh`); pin `firmware/fetch_tu117_firmware.sh` to a linux-firmware tag + sha list
(v1.1: tag 20260916, `tu117_fw_table.txt`) and produce `WHENCE.tu117` (`whence_tu117.py`), `LICENCE.nvidia` (P0-6); `test/test_gb_nouveau.sh` with a fake `GB_ROOT` tree and PATH shims (insmod, rmmod, mount, umount, getprop,
setprop, cmd, am, dmesg, sleep with a fake clock, sync, chown); README; `integration/nouveau-policy.md` with the
nouveau.json/nvk.json edits and the nvtest entry text.

| Id | Test (mksh on the builder unless noted) | Pass criterion |
|---|---|---|
| NP-01 | `shellcheck -s sh` on every `.sh` | 0 findings (justified `# shellcheck disable=` lines allowed) |
| NP-02 | Kill switches: `gb.nouveau=0`, `gb.nouveau=bogus`, `module_blacklist=a,nouveau,b`, last-wins (`gb.nouveau=once gb.nouveau=0`) | no insmod; `mode=disabled`, `result=disabled`; `booted` unset |
| NP-03 | Crash-loop marker present | marker removed, persist set to 0, clean_boots 0, N01 queued, no insmod, `booted` unset |
| NP-04 | Probation counting: 3 persist boots reaching +120 s, then a 4th | clean_boots 1,2,3; dmesg stream requested on boots 1-3 only; marker removed each time |
| NP-05 | Crash after insmod (boot ends before +120 s) then next boot | next boot = crash-loop path |
| NP-06 | renderD128 or card0 not i915 (vmwgfx fake) | `refused:i915`, no insmod |
| NP-07 | dGPU absent; BAR zero | `refused:nodgpu`; `refused:bar` |
| NP-08 | Args injection: `runpm=0`, `modeset=1`, `$(touch /x)`, `*`, `config=NvGspRm=0;x`, 300-char token | all dropped and logged; argv ends `runpm=1 modeset=2`; no file created |
| NP-09 | Arg assembly: `config=NvGspFw=0 debug=gsp=debug debug=gsp=trace` + `gb.nouveau.fw=535`; and `config=NvGspRm=0 config=NvGspFw=0` | `config=NvGspFw=0 debug=gsp=trace runpm=1 modeset=2`; `config=NvGspRm=0 runpm=1 modeset=2` |
| NP-10 | ESP lock held by a live pid for > 120 s; stale lock (dead pid); vfat ESP mount present at insmod time | `refused:esp`; stale lock stolen; `refused:esp` |
| NP-11 | Payload: sha mismatch -> copy; copy with corrupted bytes; mount failure; krel mismatch; legacy mode | copy+verify then mounts; `error:sha` and no insmod; `error:mount` and no insmod; `error:krel`; legacy loads from `/odm/lib/modules` |
| NP-12 | Health: bound+suspended+D3cold; never D3cold; dmesg `nouveau ... timeout` | `healthy` then `clean`; `unhealthy:d3cold` (persist 0, clean_boots 0, N02, no rmmod); `unhealthy:dmesg` |
| NP-13 | Node permissions normal and gfxdriver boot | render 0660 root:graphics / 0666; card 0600 root:root |
| NP-14 | `off` with refcnt > 0 (holder by fd; holder by maps only) | permissions restored, `off-busy:1`, N03 names the holder, no unbind |
| NP-15 | `off` with an unbind that never returns | `off-wedged` within 21 s, wedged file created, N05 queued |
| NP-16 | `off` with `foreign` (property only; file only) | unbind, no rmmod, payload stays mounted, `off-unbound`, N04 |
| NP-17 | `off` in `once`/`test` boots | `off-ignored`, nothing written to sysfs |
| NP-18 | `auto` when nouveau already loaded | exit 0, perms re-applied, no second insmod |
| NP-19 | Panic policy: test mode, probation, normal persist, default | panic=30 + panic_on_oops=0 + dmesg=1; panic=30 + dmesg=1; panic=30 only; nothing |
| NP-20 | `gb_notify.sh` with 3 queued messages incl. an invalid tag and a bad pkg | 1 `cmd notification post` per valid notify (exact title/text), invalid ones dropped, directory empty afterwards |
| NP-21 | `make_dgpu_payload.sh` twice on the same tree | identical sha256; `fsck.erofs` ok; `erofs_ls.py` (with `python3 -I`) lists exactly 6.2; MANIFEST matches; firmware sha matches `firmware_staged_sha256.txt` |
| NP-22 | `gb_dgpu_report.sh report` against the fake tree | no sysfs writes, no DRM open (shim logs), all sections present |
| NP-23 | `gb_dgpu_report.sh test` with `mode` != test | exit 1, nothing done |
| NP-24 | rc lint: `gb_nouveau.rc` parsed by a small checker (every service has user/seclabel/disabled; every `start` names a defined service) | passes |
| NP-25 (Wave 2) | VMware native-smoke via `gb-ntest`: boot it, press `e` in the menu and append `gb.nouveau=test` (the VM ESP has no nvtest entry, W2-03) | `refused:i915`; `report/dgpu-<N>.txt` written |
| NP-26 (v1.1) | noload / boot_completed: crash-loop boot then a spoofed `persist=1` + `booted=1`; kill switch; unhealthy; `auto` before `sys.boot_completed` | `refused:noload`, no insmod; noload created; keep + noload; `refused:boot`, marker untouched |
| NP-27 (v1.1; suite cases NP-13-gfx, -spoof, -flagonly) | gfxdriver gate: property + flag; property (and cmdline token) without the flag; flag without the property | 0666; 0660; 0660 (foreign set in all P8 cases) |
| NP-28 (v1.1) | detach in progress: flag for 15 s; flag clears after 5 s; flag appears during the load window; dGPU pulled during the window | `refused:detach`, no lock taken; loads; `refused:detach` before nouveau.ko, marker removed; `refused:nodgpu`, marker removed |
| NP-29 (v1.1) | oops / killed insmod: BUG line during the probe; insmod killed by a signal; BUG: line without "nouveau"; plain helper failure | marker kept, noload, persist 0, next boot crash-loop; same; `unhealthy:dmesg`; marker removed |
| NP-30 (v1.1; suite cases NP-10-race, NP-11-symdir/-symfile/-hardlink/-dirmode, NP-14, NP-16) | root-only state: payload/ is a symlink; dgpu.erofs is a symlink / has 2 links; payload/ mode 0777; ESP lock re-created by a live holder during a steal; GPU users stopped by `off`; `off` keeps the module -> `/dev/gb_dgpu/off` | replaced / re-copied; recreated 0700; lock put back, `refused:esp`; three ctl.stop; flag present |
| NP-HW | Phase 2A/2B on the SB3 | HW |

### 18.2 gb-power (component 2)

Scope: section 10 behaviour, section 9 server, HDA driverless, compute hooks, `--dtx-monitor`, `-S`; static asserts
for LOCK/UNLOCK/REQUEST; `gb_power.rc` changes (15.2); host tests (`test/test_gb_power.c` extended) and a protocol test
client `test/test_socket.py` (python3 -I) against the host build; `build.sh` builds the NDK binary (`-Werror`, static,
stripped) and host tests normally and with `-fsanitize=address,undefined`.

| Id | Test | Pass criterion |
|---|---|---|
| GP-01 | NDK static build with `-Wall -Wextra -Werror`; `file` says static | builds; `bin/gb_power` updated, sha recorded |
| GP-02 | Host tests plain + ASan/UBSan | 0 failures, no sanitizer reports |
| GP-03 | Locked latch + EC timeout model | CANCEL, never open |
| GP-04 | Holder by maps only | refuse, perms restored, N08 queued with the name |
| GP-05 | Hung unbind (pipe-driven fake) | `wedged` within 21 s; battery bridge keeps updating during the hang; no further dGPU writes |
| GP-06 | Restart with driver bound / driverless | stays locked / unlocks |
| GP-07 | Re-lock after resume (suspend_stats change) and after BASE_CONNECTION | LOCK issued |
| GP-08 | SIGTERM bound without powerctl; with `sys.powerctl` set | stays locked; unlocks |
| GP-09 | Boot disk on USB; mmcblk mounted | refuse + N09 / N10, CANCEL |
| GP-10 | Used-but-driverless detach | remove written for every 10de function before CONFIRM |
| GP-11 | Attach with empty bus: staged rescan with backoff; base detached mid-way | writes in order, retries at 2/5/15 s, abort when detached |
| GP-12 | Double press during the worker | cancel after the worker, never mid-worker |
| GP-13 | Property flip persist 1 -> 0 while bound | no unbind by gb_power |
| GP-14 | HDA function bound to a fake driver in every mode | unbound |
| GP-15 | Watchdog: `runtime_status=error`; D0 61 s refcnt 0; D0 with compute hold | `fault rpm-error`; `fault d0-idle`; no fault |
| GP-16 | State derivation table 8.1 (every row) | exact `vendor.gb.dgpu.state` values |
| GP-17 | Socket: golden transcripts 9.6, every command x every role (peer uid injected through a test hook) | exact JSON (key order free), roles per 9.3 |
| GP-18 | Socket robustness: 300-byte line, 9 clients, 5 watchers, slow reader (never reads), client sending garbage bytes | E2BIG+close; 9th EBUSY; 5th watcher EBUSY; slow reader dropped at 8 KiB, main loop never blocks (heartbeat timing unaffected) |
| GP-19 | `watch` emits on change and pings every 30 s (fake clock) | as specified |
| GP-20 | UI uid resolution from a fake packages.list; APK absent | role ui only with APK present; mtime cache |
| GP-21 | compute-state hold and release on EOF | power/control on via worker, auto on EOF |
| GP-22 | Notification queue files (format 12.1) and in-memory queue before boot_completed | exact files; flushed after boot_completed |
| GP-23 | `--dtx-monitor` against the fake DTX | prints events, issues no write ioctls |
| GP-24 (Wave 2) | VMware smoke: `gb_power -D` idles, socket answers `hello`, state `unavailable` | |
| GP-25 (v1.1) | compute-svc connection quiet 60 s in state loaded; a normal client quiet 30 s | hold kept, power/control on; closed |
| GP-26 (v1.1) | load window: owner `<live pid> gb_nouveau`; owner of another name; dead owner pid; window opening during a slow-path wait | CANCEL + N15 at once; quick path; quick path; N15 after 30 s, no unbind |
| GP-27 (v1.1) | detaching flag: set for a request, kept from a quick-path confirm until the latch closes, removed by the cancel echo; a stale flag at start; a failing quick-path LATCH_CONFIRM ioctl | as described; not confirmed, flag kept for the 10 s echo wait, then ready and removed |
| GP-28 (v1.1) | split detach: refcnt rises between the jobs; op_gate cases (nouveau refcnt 0/1/unreadable, other driver, remove bound / held / free / module absent); a gated job result; services still running; a node re-created during the decision; unreadable refcnt | refused before any remove; per case; cancel + perms restored; waits; node 0000; refused |
| GP-29 (v1.1) | EC_IN_PROGRESS echo wait after a busy refusal; late nouveau bind with refcnt 1 while the latch is open | never unbound; never unbound, LOCK |
| GP-30 (v1.1) | rebind after `off` (flag + persist 0 / persist 1 / no flag) | `ctl.start gb_nouveau_off` once per bind / nothing / nothing |
| GP-31 (v1.1) | base drive mounted: tick, SIGTERM, plugged in during a slow-path decision | LOCK; stays locked; refused after the remove (N10), no CONFIRM |
| GP-32 (v1.1) | enable/disable effects: module loaded unbound, auto running, noload, keep; gfxdriver gate; compute_allow.tmp symlink; d3cold_allowed with a driver bound; module_blacklist in two tokens; confirm watchdog | next-boot cases; 0660 without the flag; not followed; untouched; disabled; CANCEL before the rescan |
| GP-HW | Phase 3 hardware checks 1-9 | HW |

### 18.3 k14 (component 3)

Scope: `kbuild14_native.sh` (copy of kbuild13 with `BUILD_NUMBER=16302714`, log `/build/kbuild14.log`, markers
`/build/kbuild14.{done,failed}`, `--destdir=/build/dist14`); the 38 nouveau commits of
`nvresearch/web/stable_618_nouveau_after21.tsv` minus "drm/client: Remove holds_console_lock" = **37** stable patches
`patches/k14/0001-...0037-*.patch` (order of the stable branch; v1 said 38 / 0038 by mistake); N1-N3 as `0101-*`,
`0102-*`, `0103-*`; `K14.md`.

Workspace rule: the Kleaf tree `builder:/build/gki/common` carries k13's uncommitted changes. Before anything, save
`git status --porcelain`, `git diff` and the untracked list to `/build/k14/k13-state/`, commit the k13 working tree to a
new branch `k13-native` (metadata only), branch `k14-nouveau` from it, `git am -3 /build/k14/patches/*.patch`, build,
then `git checkout k13-native` and prove the working tree equals the saved state (empty diff). `/build/dist` is never
written. Free space on `/build` >= 10 GB before the build. The optional `CONFIG_DYNAMIC_DEBUG=y` variant
(`/build/dist14-dyndbg`, branch `k14-nouveau-dyndbg`) only if >= 12 GB stay free; never staged.

| Id | Test | Pass criterion |
|---|---|---|
| K-01 | Every patch's `git show --stat` touches only `drivers/gpu/drm/nouveau/` | 0 exceptions |
| K-02 | `kernel_x86_64_dot_config` diff `/build/dist` vs `/build/dist14` | empty |
| K-03 | `vmlinux.symvers` diff | empty |
| K-04 | modpost unresolved for the nouveau set (existing analyzer) | 0 |
| K-05 | Every staged `.ko` signer key id == the bzImage built-in certificate | all match. Each build *may* have its own key (k14 in fact reuses k13's: Kleaf's `--config=local` cache kept `certs/signing_key.pem`), so every native and VM module is always restaged from one dist (D11) and compared with the certificate in that image's own bzImage, never with a remembered key id |
| K-06 | CRC check of all lapis `vendor_ramdisk` and `vendor_dlkm` modules against k14 `vmlinux.symvers` (refresh_modules.sh style) | no mismatch |
| K-07 | N1/N2/N3 reviewed against mainline 7.3 (same functions), notes in K14.md | written |
| K-08 | `kernel_release.txt` written; differs from k13's krel | yes |
| K-09 | k13 working tree restored | empty diff vs saved state |
| K-10 (Wave 2) | VMware boot of k14 first, then native image builds | VM reaches launcher |
| K-HW | P4 hardware checks | HW |

### 18.4 nvk-gfxdriver (component 4)

Scope: P0-1 (sync `apply_patches.py` + patches 0001/0002 to `builder:/build/native/nvk-android/src/mesa`), P0-3
(re-run import/loader checks vs 276.R4, record the build id), P0-4 (INTEGRATION_NOTES: B4 correction, B8, Android 16+
fall-through, no B9, ETC2/ASTC, property prefixes, platform-sdk 35 vs 37, 31-char names); NVK build changes
(`-Dplatform-sdk-version=37`, shader cache on, a ready-to-run rebase script for 26.3.0 final, not executed before the
tag exists and is 14 days old); patches 0004-0006 + `apply_patches.py`; `nvk_probe` modes `ahb-i915-readback` and
`a17-swapchain-model` (`-t` accepts them; `all` runs every mode; exit codes 0/1/2/3 unchanged); `gb_nvk.rc` (15.5);
`C:\GB-build\native\gfx-driver-pkg\`: shim (section 14), APK (13.3), `gb_gfxdriver.sh`, `gb_gfxdriver.rc` (15.6),
`gfxdriver.json`, tests.

| Id | Test | Pass criterion |
|---|---|---|
| NV-01 | Bit-identical NVK rebuild after the P0-1 sync (0001+0002 only) | sha256 equals `f7e9154f2e049cdfbb8fc3c1d92815eb96a4b22b5105a04f625eeb41fadf0723` (current `bin\vulkan.nouveau.so`, re-hashed 2026-10-25) |
| NV-02 | bionic chroot of 276.R4: `nvk_probe` on both entry paths | exit 3 `NO_DEVICE` |
| NV-03 | drm-shim TU117: device creation, NAK compile, every probe mode reaches its first device call | pass |
| NV-04 | Patches 0004-0006 apply on 26.3.0-rc2 with `apply_patches.py --check` and build | clean |
| NV-05 | Reproducible build of the patched NVK (two builds) | identical sha256 |
| NV-06 | ELF NEEDED of `vulkan.nouveau.so` and `libgb_nvk.so` | within 276.R4 LLNDK + `libhardware`; sonames as specified |
| GD-01 | Shim unit tests on the builder (glibc build of the shim logic against lavapipe as "ANV" and drm-shim NVK) | ANV device first; NVK appended only when gated in; `android framework` engine or app name -> no NVK; NVK load/instance failure -> ANV only, no error |
| GD-02 | Shim with `vendor.gb.gfxdriver` unset (property shim) | ANV only |
| GD-03 | APK: `aapt2 dump badging`, `zipalign -c`, `apksigner verify -v --min-sdk-version 36` | package, targetSdk 36, meta-data build_time `L<digits>`, extractNativeLibs false, libs stored and aligned, v2+v3 |
| GD-04 | `gb_gfxdriver.sh` with fake cmdline/APK presence (mksh, GB_ROOT) | sets `ro.gfx.driver.0` and `vendor.gb.gfxdriver=on` only when both hold |
| GD-05 (Wave 2) | VM: opted-in app falls back to the VM HAL, no crash; zygote preload/unload/reload | needs approval |
| NV-HW / GD-HW | Gate G0, 8a/8b checks | HW |

### 18.5 gpu-compute (component 5)

Scope: section 11. `loader/` (stub), `launcher/`, `service/` (llama.cpp at a pinned release tag: the newest `bNNNN`
tag that is >= 14 days old at fetch time; record tag + full commit sha in `LLAMA_PIN`), front end, `gb_gpu_compute.rc`
(15.3), `gpu_compute.json` draft, NDK build scripts, tests. No model downloads: tests use `test-backend-ops` (Vulkan
backend) and a synthetic random-weight tiny GGUF generated with the pinned tree's `gguf-py` from its bundled vocab
file.

| Id | Test | Pass criterion |
|---|---|---|
| GC-01 | NDK cross-build; ELF NEEDED check (11.1) | pass |
| GC-02 | Stub + llama.cpp Vulkan backend against lavapipe on the builder (`GB_NVK_ICD`=lavapipe ICD), `test-backend-ops -b Vulkan0` subset | pass |
| GC-03 | End-to-end over the UNIX socket with the synthetic model (glibc build of the same code) | `/health`, `/v1/models`, chat completion (stream and non-stream) succeed |
| GC-04 | Peer-credential rejection (uid not allowed, via a test hook or a second uid with `sudo -n -u`) | 403 |
| GC-05 | Limits: body 2 MiB, 5th connection, concurrent generations | 413, refused, 429 after the queue |
| GC-06 | Idle exit after 120 s (fake clock or env override) and SIGTERM within 5 s | exit 0 |
| GC-07 | No TCP: `strace -f -e trace=socket` or `/proc/<pid>/net/tcp*` during GC-03 | no AF_INET/AF_INET6 sockets |
| GC-08 | gb_power interplay against a mock gb_power socket (the mock idle-closes like gb_power, 9.1) | `hello`, `compute-state idle/loading/loaded/serving/idle` sequence; v1.1: a loaded model keeps its hold for 8 s without requests (exempt connection), and against a stricter mock that idle-closes every connection after 2.5 s the 15 s keep-alive (1 s in the test) keeps it too |
| GC-09 | drm-shim TU117 smoke (bionic chroot or glibc) | device enumerated, backend init |
| GC-10 | Reproducible build | identical sha256 of all payload libs |
| GC-11 (Wave 2) | VMware: launcher refuses cleanly with no NVIDIA device | exit 3 |
| GC-HW | P6 hardware checks | HW |

### 18.6 gpu-ui (component 6)

Scope: section 13.2 app, `build_apk.sh` (13.1 toolchain and signing), `gb_gpu_ui.rc` (15.4), `gb_gpu_ui_setup.sh`,
`gpu_ui.json` draft, `test/mock_gb_power.py` (python3 -I, implements section 9 incl. golden transcripts 9.6), host
unit tests.

| Id | Test | Pass criterion |
|---|---|---|
| UI-01 | Build: aapt2/javac/d8/zipalign/apksigner, `aapt2 dump badging`, `apksigner verify` | package `gb.sb3.gpu`, min/target 36, versionCode = epoch minutes, v2+v3, no privileged permissions |
| UI-02 | Manifest static checks (lint if the SDK `lint` is installed via sdkmanager, else a script) | components and meta-data exactly as 13.2; receiver protected by DUMP; provider by WRITE_SECURE_SETTINGS |
| UI-03 | `StateMapper` unit tests (pure Java, host JVM) for every row of 8.3 incl. unknown values and `persist` | exact subtitles and tile states |
| UI-04 | Protocol client tests against `mock_gb_power.py` over a UNIX socket (host JVM, `java.nio` transport) | golden transcripts; EPERM/ENODEV handling; reconnect after server restart; `watch` updates |
| UI-05 | No socket present | client reports `Unavailable` within 2 s, no crash |
| UI-06 | `gb_gpu_ui_setup.sh` with shims | `pm grant` once per boot; `add-tile` only once ever; marker `/data/vendor/gb/ui/tile_added` (v1.1) |
| UI-07 (Wave 2) | VM `adb install` (needs approval): tile shows Unavailable | |
| UI-HW | P7 hardware checks | HW |

---

## 19. Resolved ambiguities and deviations from the plan

| # | Plan text | Resolution | Reason |
|---|---|---|---|
| D1 | gb_nouveau.sh sets `vendor.gb.dgpu.state` (P1) and gb_power publishes it (P3) | Single writer gb_power; gb_nouveau.sh writes `vendor.gb.nouveau.result`, gb_power folds it in (8.1) | Two writers race; gb_power would overwrite `crashloop` with `off`. |
| D2 | "persistent enable honoured only after 3 clean boots" (L3, inv. 4) vs "persist=1: load, log probation n/3" (P1) | Persist is honoured; probation = first 3 clean persist boots get the dmesg-live capture and a UI label; the crash-loop guard is permanent | The strict reading makes the UI switch useless across reboots; the crash-loop guard already bounds the damage to one boot. |
| D3 | ESP exclusion = "wait until /proc/mounts shows no /mnt/gb_esp", bootlog waits while loading/busy (R4) | Lock directory `/dev/gb_esp.lock` used by gb_nouveau.sh, gb_dgpu_report.sh, gb_bootlog and gb_slotsync, plus a device-based vfat check before insmod | Check-then-set is racy; gb_slotsync also mounts the ESP (rw, at `/mnt/gb_slotsync`, at boot_completed, the same moment gb_nouveau starts). |
| D4 | Whitelist atoms appended as separate args | Merged into one `config=` and one `debug=`; `NvGspRm=0` drops `NvGspFw=0` | nouveau `config`/`debug` are single charp params (last wins); forced fwif + disabled RM fails the probe (verified in GKI source). |
| D5 | `gb.nouveau.fw=535` -> `config=NvGspFw=0` | Kept; verified | `firmware.h:53-58` builds option `Nv<Gsp>Fw`; `tu116.c:47-48` version 0 = 535.113.01. |
| D6 | Scripts/gb_power run `cmd notification post` asynchronously | Queue + `gb_notify` service as user shell | Posting from uid 0 is unverified; gb_power must not fork Java tools; a queue never loses messages. |
| D7 | P5 moves `nvk_probe` into the payload | `nvk_probe` (42 KB) stays in `/vendor/bin`; compute uses a `/vendor/bin` launcher + payload libraries | linkerconfig maps executables only under `/odm/bin/`, `/vendor/bin/` (VM ld.config.txt); `/odm/dgpu/bin` would get no linker section. |
| D8 | `/data/vendor/gb` 0770 root:shell; copy at `/data/vendor/gb/dgpu.erofs` | v1.1: 0751 (v1: 0771); copy in `payload/` 0700 root, ownership checked before use; `report/` 0750; shell's own files in `ui/` | The nobody compute service must traverse to `compute_allow`/`models`; group shell must not be able to create, rename or delete root's state (marker, counters, `compute_allow`) or swap the payload between verify and mount. With 0771 it could (review R2). |
| D9 | `used`/`foreign` as properties | Properties plus root-only files `/dev/gb_dgpu/*`, monotonic | Any app can set vendor props (permissive untrusted_app); resetting `used` would skip the mandatory remove/rescan. |
| D10 | k13 krel `6.18.21-android17-5-g3938c8d97142` | Real string ends `-dirty-ab16302624`; never hard-code | Builder `modinfo` vermagic and bzImage banner. |
| D11 | k14 = nouveau backports only | Staging k14 means restaging every module (VM and native) from `/build/dist14` | Each Kleaf build *may* sign with its own key (k14 in fact reuses k13's key from the `--config=local` cache), MODULE_SIG_PROTECT needs all modules from one build, and k14 has `MODULE_SIG_FORCE` unset, so the signature check in verify_image compares every module with the certificate embedded in that image's bzImage (never a remembered key id). |
| D12 | VM-specific GbGfxDriver build | Not needed | The VM's `ro.hardware.vulkan` is also `intel` (VM getprop). |
| D13 | UNLOCK on SIGTERM | Only if driverless or `sys.powerctl` set | `stop gb_power` with nouveau bound must not leave an unlocked latch the EC may open. |
| D14 | "While a model is loaded: power/control=on" | gb_power writes it (worker) on the compute service's request, released when its connection closes | The service is non-root; tying the hold to a connection makes a crash release it. |
| D15 | llama-server behind a loopback port in a private netns if no UNIX listener | AF_UNIX only, never TCP | Invariant 18; a netns needs root. |
| D16 | "Screen-off: unload" | Signal = i915 eDP `dpms` sysfs (INFERRED) | No verified screen-state property on lapis (VM getprop has none). |
| D17 | `gb.nouveau=` values `0|once|test` | Unknown value = `0`; last occurrence wins | Fail safe; the boot editor appends. |
| D18 | Guard "dGPU absent -> refuse" (P1) and P3 HW check "boot detached, then attach (bound mode)" | gb_power starts `gb_nouveau` after a hot-attach when the policy wanted to load | Otherwise bound mode never starts after a detached boot. |
| D19 | Node permissions set once after load | Re-applied by gb_power after every nouveau bind | Rescan recreates the nodes as 0666 (ueventd). |
| D20 | Unhealthy load handling unspecified | persist 0, clean_boots 0, marker removed, N02, driver left loaded | Never auto-unload a sick driver; do not re-load it next boot. |
| D21 | `dmesg -w` "in the background" | Separate service `gb_dgpu_dmesg` | init kills a finished oneshot's process group (INFERRED). |
| D22 | `dgpu-<bootcount>.txt` | `bootseq` in `/data/vendor/gb` | The ESP bootcount needs an ESP mount. |
| D23 | Package names unspecified | `gb.sb3.gpu`, `gb.sb3.gfxdriver` | No Google/NVIDIA namespaces. |
| D24 | App notifications "in use, before detach, crash-loop, wedged" (P7) | In-use = app (ongoing); the others = daemon-posted N01-N14 | Daemon alerts must work without the app (P1-P6 ship first) and never duplicate. |
| D25 | `gb.gfxdriver=1` boot entry mechanics unspecified | post-fs-data exec sets `ro.gfx.driver.0` before zygote; per-app opt-in via Settings.Global | ro props are write-once; the official per-app knob. |
| D26 | P0 owners unspecified | P0-1/3/4 component 4; P0-6 component 1; P0-2/5 docs in Wave 2 | Fits existing directories. |
| D27 | "file-backed erofs mount" | Preferred; loop accepted | toybox `mount` behaviour on a regular file is unverified. |
| D28 | "renderD128 not i915" only | Both renderD128 and card0 checked; vendor and driver | Plan section 8 inv. 3 names both. |
| D29 | `off` stops nothing first | `off` stops `gb_gpu_compute`, `gb_nvk_probe` and `gb_dgpu_test` before the refcnt gate (v1.1: the last two too) | Same as the detach path; otherwise compute always blocks `off`, and a root nvk_probe ignores chmod 000. |
| D30 | (v1.1) single refcnt read before one unbind+remove job | Two worker jobs (unbind, remove) with a main-loop re-check between them and a gate re-check in the child before every write; another check of refcnt, load window and boot disk right before LATCH_CONFIRM | A root opener (or a late re-bind) after the first read was never re-checked (review R2-1). |
| D31 | (v1.1) gb_nouveau.sh and gb_power each check the other's activity | `/dev/gb_dgpu/detaching` (gb_power) vs the ESP lock owner `gb_nouveau` (load window); flag first, then look | A quick-path confirm could open the latch while nouveau was probing (review R2-2). |
| D32 | (v1.1) "never unbind nouveau on its own" vs "a late bind on an open latch is unbound" | The late-bind unbind goes through the refcnt gate; EC_IN_PROGRESS outside a decision is not "releasing"; LATCH_CANCEL is not sent after a confirm (dtx.rst: the process has concluded, a cancel does nothing) | Review R2-3. |
| D33 | (v1.1) render 0666 on the property | Property AND `/dev/gb_dgpu/gfxdriver` in gb_nouveau.sh and gb_power | Any app can set vendor.* properties (review R1-H2/R2-4). **(v1.2) Superseded by D48.** |
| D34 | (v1.2) REQ:44-45 `updatable_driver_all_apps 1` when on, else `0`; 1B D1B-1 `0`/`3` + APK `assets/allowlist.txt` = `*` | `all_apps` `0` (on) / `3` (off) + a gb_power-computed `updatable_driver_production_opt_in_apps` + the "Always Intel" list in `..._opt_out_apps`; the APK ships **no** `allowlist.txt` | Proof in 24.7: `1` returns the driver before the opt-out list is read; an allowlist `*` cannot express "games + selected" and the allowlist belongs to GpuService. Both D3 policies are then pure Settings values with one fixed APK. |
| D35 | (v1.2) REQ:44-46 Settings "written by root (gb_power) only" | gb_power is the only decider; `gb_notify` (user shell) writes, from the queue, three whitelisted keys | gb_power never forks Java tools (D6); shell holds `WRITE_SECURE_SETTINGS`; the inputs are root-owned files (D1B-2). |
| D36 | (v1.2) REQ:41-42 `persist.vendor.gb.gfx_ok` as the verdict | Truth = root-owned record with identity; the property is a mirror | Properties are untrusted (4); an app setting `gfx_ok=1` must not open the render node (D4, D1B-3). |
| D37 | (v1.2) REQ:60 "gfx_active=intel BEFORE unbind" | At the start of every slow-path decision, before `nodes_lock()` | Earlier is strictly safer; restored automatically when the detach is refused (D1B-4). |
| D38 | (v1.2) REQ:47 "reads gfx_active at vkCreateInstance"; REQ:49 engine rule only | Read per instance, snapshot kept; plus the ANGLE-HWUI guard | Same for one-instance apps; with HWUI on GL the engine rule cannot see HWUI's ANGLE instances (D1B-5, D1B-6). |
| D39 | (v1.2) REQ:59 notification with actions; I1 D24 "never duplicate" | App-posted `gb_gpu_detach` (actions need an app); daemon N08 only while GbGpu has not declared `ui-caps notify` | `cmd notification post` has no actions; a missing POST_NOTIFICATIONS grant must not leave a refusal silent (review E7). |
| D40 | (v1.2) REQ:39 "when nouveau first binds ... runs the self-test"; D4 | After the binding reached its clean mark (`result=clean` of this binding, or a passed rebind watch) | A crash in the test must not be counted as a driver-load crash; the test has its own crash marker (D1B-8). |
| D41 | (v1.2) REQ:72-73 `list-holders`, `set-app-intel` | `list-holders` = alias of `holders`; one argument `<pkg>:1`/`<pkg>:0` | The 9.1 framing allows one argument (`cl_line()`, `gb_power.c@25bddd1f:3178`). |
| D42 | (v1.2) REQ:66 / D7 tile "Unavailable (base detached)" | INACTIVE with that subtitle (taps still cycle the mode); UNAVAILABLE only without the package or DTX (VM) and while preparing a detach | SystemUI ignores taps on UNAVAILABLE tiles (ux-app); review C7. |
| D43 | (v1.2) REQ:33-34 probation "non-persistent marker semantics" | Every load window (insmod, bind, rebind) writes the crash marker; probation = dmesg capture for the first 3 clean `auto` boots; binds do not count | D2 with the new failure action (`gfx_mode=intel`); review C8. |
| D44 | (v1.2) I1 7.2 step 3 "already loaded = idempotent" | Bind path 7.2a, with the full guard order | `off-unbound` in a `foreign` boot leaves the module loaded and the dGPU driverless (D1B-13; review A2). |
| D45 | (v1.2) I1 10.4 rescan rebinding unguarded; 1B S6 guarded only gb_power's own rescan | The rebind guard (crash marker + ESP lock + 30 s watch) is armed at the base attach event, at a 10de PCI add, and before gb_power's rescan, whenever the module is loaded and no 10de function is present | The kernel probes a re-added function by itself (`device_initial_probe()` ignores `drivers_autoprobe`; evidence E6), so the probe cannot be prevented, only marked. Residual: a probe that crashes before the marker is synced costs one reboot (23.8). |
| D46 | (v1.2) D3 "games + selected" / "all apps" | Default: `CATEGORY_GAME` or `FLAG_IS_GAME` apps plus "Use NVIDIA" apps; switch "Use NVIDIA for all apps" = every launchable app without `FLAG_SYSTEM`; "Always Intel" always wins; updated system apps only by an explicit "Use NVIDIA" through adb | The platform's own game test (`a17 GraphicsEnvironment.java:171`); keeps updated Chrome/WebView providers off the automatic sets (review A4). |
| D47 | (v1.2) PLAN section 10 default-on criteria; 1B `ro.vendor.gb.gfx_default` | Default mode `auto` with nouveau auto-loading on attach (user decisions D1, D2, 2026-10-25); no build-time default knob | The user decided; the knob was only a stand-in for that decision (review A6). |
| D48 | (v1.2) PLAN inv. 13 render 0666 only in P8 boots; D33 | Render 0666 only while gb_power's own derivation says `nvidia` (a valid pass of this exact identity, attached, healthy, mode not intel) | No property or flag file can raise the node (Q-1b-2). |
| D49 | (v1.2) review A1: crashes while apps render on NVK are not covered | App-graphics session marker; two unclean NVIDIA sessions in a row force `gfx_mode=intel` (N19) | Otherwise an auto-started app that oopses nouveau gives a reboot loop with `kernel.panic=30`. |
| D50 | (v1.2) 1B notification ids N15-N17 | N16-N20 | N15 is the v1.1 load-window refusal (review B2). |
| D51 | (v1.2) 1B `get-mode` returns the Always-Intel array | Paged `get-apps [<start>]` | 128 package names do not fit a 4096-byte reply (9.1). |

---

## 20. Open decisions (user) and assumptions

* Assumed yes (plan 12.1): "all images" = all **native** images; the VM image carries none of this.
* Pending (plan 12.2): `kernel.panic=30` on nouveau boots. Implemented as the constant `GB_PANIC_TIMEOUT=30` in
  `gb_nouveau.sh`, so Wave 2 can change it in one place. **(v1.2) Decided: yes** (user decision D2, 2026-10-25: all
  wave-1 rails including `kernel.panic=30` on nouveau boots), now on most boots with the base attached.
* (v1.2) Decided by the user on 2026-10-25 (section 22.1): default mode `auto` (D1), nouveau auto-load on attach with
  all rails (D2), the app policy (D3), the self-test gate (D4), the shim rules (D5), the detach flow (D6), the GUI
  (D7), VMware behaviour (D8). These replace 1B Q2, Q3, Q7 and review section 4 items 1-4.
* (v1.2) Still open, for the user or the hardware run: (a) whether the "Always Intel" seed
  (`/odm/etc/gb/gfx_app_prefs.default`) should list apps that need AHardwareBuffer import, which NVK 8a lacks (1B
  Q5, review A4; ships empty, games are the default scope); (b) the boot-time cost of loading nouveau + GSP on every
  boot with the base (1B Q6, H-1B-01); (c) the PLAN 8a kernel fence gate (1B Q4, H-1B-09); (d) whether
  `untrusted_app_<n>` domains must become permissive (17, H-1B-12); (e) an ANGLE-on-NVK leg in the self-test (the
  automatic test proves the ICD of the same Mesa build, not ANGLE; review A4 residual, H-1B-03).
* Builder additions (plan 12.3): mksh, erofs-utils, apksigner/zipalign/aapt2 are installed; any `sdkmanager` install
  stays inside the component's scratch dir. A VK-GL-CTS build is not part of Wave 1.
* Phase 2 boot medium (12.4) and research scratch retention (12.5): unchanged, user decisions.
* Base-port mapping for the detach guard is unknown until Phase 2: every USB/SD storage device counts as base-side.
* Open for v2 (review R2-5, optional part): `persist.vendor.gb.nouveau` itself is a property any app can set, so any
  app can opt nouveau in (probation, the crash-loop guard and the kill switch still apply). A v2 could honour persist
  only together with a root-only opt-in file written by gb_power's `enable` (roles root, shell, ui). Not done in v1.1
  because it changes the adb workflow (`setprop persist.vendor.gb.nouveau 1`) that the docs and tests use.
  (v1.2) The property is retired; its successor `persist.vendor.gb.gfx_mode` is equally settable by any app, and the
  default is now "on" (`auto`), so a spoof can at most select Intel or NVIDIA mode; every rail still applies.

---

## 21. Changes in v1.1 (post-review fix pass, 2026-10-25)

Review 1 = interface consistency, review 2 = safety. Severity in brackets. "Rejected" items are listed with the reason.

| # | Finding | Change (sections) | Components |
|---|---|---|---|
| C1 | [R1 high] gb_power idle-closed the compute service's connection after 30 s, dropping the power hold of a loaded model | compute-svc connection exempt from the idle close; the service re-sends its state every 15 s and reconnects in every state (2.3, 9.1, 9.3, 11.5) | gb-power, gpu-compute |
| C2 | [R1 high, R2 medium] render node 0666 on the spoofable property alone in gb_power | one gate: property AND `/dev/gb_dgpu/gfxdriver`, also in gb_nouveau.sh (which no longer reads `gb.gfxdriver=`) (3, 4, 5.2, 7.5, D33) | gb-power, nouveau-policy |
| C3 | [R1 high] the live shared manifests already point at the rewritten Wave 1 files | no shared-file edit possible in Wave 1: W2-01 is now the first, atomic Wave 2 step, with a pre-flight table of the 7 live inputs and their `.pre-dgpu` fallbacks, and a verify_image rule (integration/README.md) | docs |
| C4 | [R1 medium] payload tool and firmware licence files missing | `make_dgpu_payload.sh` + `erofs_ls.py` + NP-21; fetch pinned to linux-firmware tag 20260916 with the sha list; `WHENCE.tu117` and `LICENCE.nvidia` produced (1.3, 6.2, 6.3) | nouveau-policy |
| C5 | [R1 medium] `enable` answered "now" when `auto` could not rebind | `enable` answers next-boot while nouveau.ko is loaded without the device, a previous auto runs, or noload; `disable` answers next-boot with `keep` (9.3) | gb-power, gpu-ui mock |
| C6 | [R1 medium] a rescan after `off` (module kept) re-bound nouveau with persist 0 | `/dev/gb_dgpu/off` + gb_power starts `gb_nouveau_off` again (5.2, 7.3, 10.1, 10.4) | nouveau-policy, gb-power |
| C7 | [R2 high] refcnt gate read once; root openers; late binds; new nodes unlocked | split unbind/remove jobs, child gate, re-checks before CONFIRM, nodes locked during the decision, nvk_probe/gb_dgpu_test stopped, gb_nvk_probe runs as shell (10.1, 10.3, 15.5, D29, D30) | gb-power, nouveau-policy, nvk-gfxdriver |
| C8 | [R2 medium] confirm during a nouveau load window | `used` from the window start; gb_power refuses while the window is open (N15); `/dev/gb_dgpu/detaching`; late checks before nouveau.ko (4, 5.2, 7.2, 10.3, 12.2, D31) | gb-power, nouveau-policy |
| C9 | [R2 medium] "releasing" unbound nouveau during the echo wait | releasing = EC confirmed or latch open only; nouveau only through the gate (10.1, D32) | gb-power |
| C10 | [R2 medium] crash-loop guard did not block a later spoofed load; no boot_completed check | `/dev/gb_dgpu/noload`, `refused:noload`, `refused:boot` (5.2, 7.2, 7.8) | nouveau-policy |
| C11 | [R2 medium] root state in shell-writable directories | `/data/vendor/gb` 0751, `report/` 0750, payload ownership checks, O_EXCL/noclobber writes in `notify/`, `ui/` for shell's marker, `/data/vendor/nvk` owned by shell (5.1, 6.5, 12.1, 15.x, D8) | all |
| C12 | [R2 medium] non-atomic stale ESP lock steal | rename-based steal with inode + owner check and put-back; release only one's own lock (5.4) | nouveau-policy; Wave 2 bootlog/slotsync |
| C13 | [R2 low, taken] refcnt fail-open, decision not locked, boot disk only at request, d3cold_allowed while bound, confirm watchdog without a fresh query, oops patterns, module_blacklist last-wins in gb_power, broadcast to a stopped GbGpu | fixed as described in 3, 7.2, 7.7, 10.1-10.3, 12.1 | gb-power, nouveau-policy |
| C14 | [R1 low, docs] keep flag undocumented; NP-25 unreachable as written; k14 "38 patches / new key"; W2-02 timing and gb_notify logcat | 5.2, 18.1, 18.3, D11; integration/README.md | docs |
| C15 | [fix pass, own finding] a failing quick-path LATCH_CONFIRM ioctl left gb_power "in progress" with `detaching` set until the next DTX event | the 10 s echo wait also applies there, then ready and `detaching` removed (10.3 step 3, GP-27) | gb-power |

Not taken (reasons in the integration notes): R2 low "holder scan in a forked child" (deferred: the scan is
rate-limited to once per 10 s while busy and to explicit requests; a forked scanner is a v2 item), R2 low "ueventd
rules" (kept as the optional W2-12, now recommended), R2 medium optional part "persist only with a root-only opt-in
file" (section 20, v2).

---

## 22. Graphics switching (v1.2): model, decisions, modes

### 22.1 Requirement and decisions

REQ (the user, verbatim): "I want seamless transition between the intel graphics and NVIDIA graphics to be built in
with a GUI for manual change....when I disconnect the NVIDIA performance base on the Surface book Graphics defaults to
intel since I will be using it as a tablet, when I reconnect the base the graphics default to NVIDIA and a manual
toggle for manual switchover as well."

Decisions made by the user on 2026-10-25 (normative; implemented by this spec, not re-asked):

| # | Decision |
|---|---|
| D1 | Default mode `auto` (unset = `auto`). Auto: base attached + healthy dGPU -> NVIDIA for apps; detached -> Intel. Manual `intel` (dGPU driverless in D3cold, no NVIDIA apps) and `nvidia` (override; while detached the effective state is Intel with reason "base detached"). A manual choice persists until the user picks auto. System UI, SurfaceFlinger and HWUI are always Intel. |
| D2 | nouveau auto-loads on base attach (boot with the base, or reattach) in `auto`/`nvidia`, after `sys.boot_completed`, with all wave-1 rails (kill switch, crash-loop marker -> Intel + notification, `kernel.panic=30`, argument whitelist, i915 card0 guard, ESP lock, refcnt-gated unbind, forked worker + deadline, latch lifecycle, staged rescan). |
| D3 | Apps on NVIDIA in auto/nvidia: by default games (`ApplicationInfo.CATEGORY_GAME` or the legacy `isGame` flag) plus the apps marked "Use NVIDIA"; a global switch "Use NVIDIA for all apps" (every launchable non-system app); per-app "Always Intel" always wins. Implemented with Android's updatable-driver mechanism, both policies working at run time without changing the APK (section 24). GbGpu computes the game/launchable lists and pushes them to gb_power; only gb_power decides Settings.Global, through the gb_notify queue with a fixed key whitelist. In Intel mode or when `gfx_ok` != 1 the opt-in is fully off. |
| D4 | Automatic self-test on the first healthy bind per identity (build id, kernel release, NVK hash): `ahb-i915-readback` + `a17-swapchain-model` in the background with the dGPU held awake; result in a root-owned record mirrored to `persist.vendor.gb.gfx_ok`; fail = NVIDIA app rendering off (compute allowed), GUI explains; "Run self-test again". Section 25. |
| D5 | Shim: reads `vendor.gb.gfx_active` at `vkCreateInstance`; `nvidia` -> NVK first (only if nouveau is bound and the render node opens), ANV second; `intel` -> ANV only; engine `android framework` and the ANGLE-HWUI guard -> ANV only; never fatal. Section 14. |
| D6 | Detach with NVIDIA in use: the latch stays locked; a notification lists the holders with "Close apps and detach" (GUI confirms, gb_power force-stops the verified holders and re-runs the decision) and "Cancel". A successful detach sets `gfx_active=intel` before the unbind and posts "Tablet mode - Intel graphics". Reattach posts "Performance base connected - NVIDIA graphics on for apps you open now" with a hint to restart open apps. Sections 23.3, 23.9, 27.3. |
| D7 | GUI: QS tile "Graphics" (Intel / NVIDIA / NVIDIA (busy N) / Unavailable (base detached) / Fault / Off (Intel mode)); tap cycles Auto -> Intel -> NVIDIA; long-press opens the page. Injected Settings page "Graphics" (category verified on lapis, 13.2) with mode radios, status, self-test + rerun, per-app "Use NVIDIA" / "Always Intel", "Use NVIDIA for all apps", compute status, help text. Section 27. |
| D8 | VMware (no dGPU): everything reports Unavailable, the opt-in stays off (Settings never written), no regressions. |

### 22.2 Modes

`gfx_mode()` (the same rule in gb_nouveau.sh and gb_power): `getprop persist.vendor.gb.gfx_mode`; exactly `auto`,
`intel` or `nvidia` is that mode; anything else, including unset, is `auto`. `persist.vendor.gb.nouveau` and
`ro.vendor.gb.gfx_default` (1B) are not read (D47).

* `auto` and `nvidia` give identical results everywhere in this spec (REQ:23-26); they differ only in UI texts, so
  that a future `auto` heuristic (for example battery) can never affect a user who chose `nvidia`.
* `intel`: nouveau is not loaded at boot; a loaded nouveau is unbound by `off` (refcnt-gated; `rmmod` unless
  `foreign`), the dGPU goes to D3cold; Settings `updatable_driver_all_apps=3`.

### 22.3 Layers and owners

| Layer | What | Owner |
|---|---|---|
| Display and system graphics | SurfaceFlinger, system_server, SystemUI, launcher, every privileged or non-updated system app: always ANV. GraphicsEnvironment never gives them a driver package (`a17 GraphicsEnvironment.java:952`), and the shim never creates NVK for `android framework`. | (platform) |
| Mode | `persist.vendor.gb.gfx_mode`, `persist.vendor.gb.gfx_apps`, `gfx_app_prefs` | the user via GbGpu -> gb_power (adb also works for the properties) |
| Kernel driver | nouveau loaded/bound or not: `gb_nouveau.sh auto` (insmod, bind path) and `off`, started by the rc triggers (15.1) and by gb_power (23.6, 23.7); kernel re-probe on reattach guarded by gb_power (23.8) | nouveau-policy, gb-power |
| Effective app graphics | `vendor.gb.gfx_active` + reason, render node mode, Settings values, self-test scheduling | gb_power only (23) |
| Per process | which driver package a new app process gets: GraphicsEnvironment at process start from the Settings (24); which GPU each Vulkan instance gets: the shim (14) | platform, shim |

Apps that are already running keep their GPU until they are restarted (REQ:17-18): GraphicsEnvironment decides only
in `setup()` at process start (`a17 GraphicsEnvironment.java:52-54, 140`), and the shim decides per instance.

### 22.4 Matrix (summary; 23.2 is normative)

"healthy" = nouveau bound, state `standby`/`busy:N`, no load window and no rebind guard.

| mode | base | dGPU | no valid record | record fail | record pass | nouveau action |
|---|---|---|---|---|---|---|
| auto / nvidia | attached | healthy | intel `untested`, self-test runs (25.2) | intel `selftest` | **nvidia** `ok` | keep bound |
| auto / nvidia | attached | present, not loaded | intel `nodriver` | same | same | rc start at boot; hot attach (23.7) |
| auto / nvidia | attached | loading / bind / rebind guard | intel `loading` | same | same | in progress |
| auto / nvidia | attached | crash-loop / unhealthy / rebind failure this boot | intel `crashloop` (mode forced to `intel`) | same | same | none |
| auto / nvidia | detached | absent | intel `detached` | same | same | none; the module may stay loaded |
| intel | any | bound | intel `mode` | same | same | `off` (rc trigger, `reconcile_off`) |
| intel | any | driverless / absent | intel `mode` | same | same | none (D3cold) |
| any | any | wedged / fault | intel `wedged` / `fault` | same | same | none |
| any (kill switch boot) | any | not loaded | intel `kill`, `all_apps=3` | same | same | none this boot |
| any (`gb.nouveau=test` boot) | any | any | intel `test`, `all_apps=3` | same | same | test sequence |
| any | VM image / no DTX | | intel `nopkg` / `nodtx`, Settings never written | | | |

---

## 23. gb_power graphics behaviour (v1.2, component 2)

### 23.1 Inputs (re-read on every status tick and every command that changes one)

| Input | Source | Notes |
|---|---|---|
| `cap` | `/odm/etc/gb_hw` = `native` AND `stack_present()` AND `ro.gfx.driver.0` = `gb.sb3.gfxdriver` AND `/odm/app/GbGfxDriver/GbGfxDriver.apk` exists AND `/odm/etc/gb/gfx_build_id` is a readable one-line file | 0 on the VM image and on any image without the package. `ro.*` cannot change after boot. |
| `dtx` | `dtx_ready()` | 0 on VMware (no `/dev/surface/dtx`) |
| `mode`, `policy` | 22.2; `persist.vendor.gb.gfx_apps` (`games`/`all`, else `games`) | |
| `attached` | `g_dtx.base` is `attached` or `not-feasible` | `not-feasible` = the clipboard battery is too low to detach; the base is still there |
| `state` | `derive_state()` (8.1), unchanged | |
| `kill`, `test_boot` | `read_cmdline()`: `g_cmd_kill`; new: the last `gb.nouveau=` value is `test` | |
| `recovered` | `/dev/gb_dgpu/keep` or `/dev/gb_dgpu/noload` exists (root-only; the kill switch also creates `noload` but row 3 comes first), or gb_power forced Intel this boot (rebind failure 23.8, unclean sessions 23.10), or `state` = `crashloop` | |
| `loading` | `state` = `loading`, or `rebind_pending` (the watch phase of the rebind guard, 23.8), or `load_window_open()` (a live `gb_nouveau` owns the ESP lock), or gb_power started `gb_nouveau` itself and `init.svc.gb_nouveau` reads `running` while the dGPU is not bound | the property part only selects a reason, never a permission |
| `bound` | `g_inv.nouveau_bound` and `state` in `standby`, `busy:N` | |
| `gfx_ok` | the validated record (25.4): 1 pass, 0 fail, -2 error, -1 none or stale | the property mirror is never read |
| `ec_ready`, `pending` | `g_dtx.ec == EC_READY`; `g_pend.kind` (23.9) | |

### 23.2 Derivation of `gfx_active` and `gfx_reason` (first matching row wins)

| # | Condition | `gfx_active` | `gfx_reason` |
|---|---|---|---|
| 1 | `cap` = 0 | intel | `nopkg` |
| 2 | `dtx` = 0 | intel | `nodtx` |
| 3 | `kill` | intel | `kill` |
| 4 | `test_boot` | intel | `test` |
| 5 | `recovered` | intel | `crashloop` |
| 6 | `state` = `wedged` | intel | `wedged` |
| 7 | `state` = `fault` (any `fault_reason`) | intel | `fault` |
| 8 | `mode` = `intel` | intel | `mode` |
| 9 | not `attached` | intel | `detached` |
| 10 | `state` = `detaching`, or not `ec_ready`, or `pending` = `detach` | intel | `detaching` |
| 11 | `loading` | intel | `loading` |
| 12 | not `bound` | intel | `nodriver` |
| 13 | `gfx_ok` = -1 | intel | `untested` (the self-test is or will be scheduled, 25.2) |
| 14 | `gfx_ok` = -2 | intel | `testerror` |
| 15 | `gfx_ok` = 0 | intel | `selftest` |
| 16 | otherwise (mode `auto`/`nvidia`, attached, healthy, a pass of this identity) | **nvidia** | `ok` |

Row 5 comes before row 8 so that a forced Intel after a crash shows as "recovered" for the rest of that boot (review
C1). gb_power keeps the result in memory (`g_gfx.active`, `g_gfx.reason`) and publishes both properties and
`vendor.gb.gfx_test` with `prop_put()` (its table grows from 24 to 32 entries). It never reads `vendor.gb.gfx_*` or
`persist.vendor.gb.gfx_ok` back.

### 23.3 Transitions (`gfx_eval()` at the end of `status_tick()`, before `publish_state()`, and after every command that changes an input)

**intel -> nvidia**, in this order (main process; nothing blocks):
1. `foreign` (property + `/dev/gb_dgpu/foreign`) if not set: apps will import i915 buffers into nouveau, so `rmmod`
   is forbidden from now on this boot (PLAN inv. 10).
2. Session marker `gfx_session` (23.10) through a worker op (`OP_MARK`, `fsync` of file and directory). Steps 3-6
   run only after that op reported success; until then the reason stays `loading`. A failed write: log, stay Intel,
   retry at the next tick.
3. Raise the nouveau render node to 0666 (23.5).
4. `setprop vendor.gb.gfx_active nvidia`, `vendor.gb.gfx_reason ok`; `gfx_seq++`.
5. N16, if this is the first `nvidia` after a base attach event of this boot, or the first one after a self-test pass
   (not on ordinary boots: they stay quiet).
6. Queue the state broadcast.

**nvidia -> intel** (also `gfx_force_intel(<reason>)`, which applies this at once with the given reason):
1. `setprop vendor.gb.gfx_active intel` and the reason FIRST (new instances stop asking for NVK).
2. Lower the render node to 0660 root:graphics, unless a detach decision or an `off`/load window owns it (23.5).
3. Remove `gfx_session` (worker op `OP_UNMARK`: unlink + `fsync` of the directory) and write `gfx_unclean` = 0: a
   clean end of an NVIDIA session.
4. `gfx_seq++`; queue the broadcast. Open file descriptors are not revoked: running apps keep NVK until they exit;
   the refcnt gate still protects detach and `off`.

`gfx_force_intel()` is called by `decision_start()` ("detaching", before `nodes_lock()`), by `set-mode intel`
("mode"), by the rebind failure ("crashloop") and by `set_fault()` ("fault").

### 23.4 Settings push (`gfx_settings_eval()`)

* Desired `updatable_driver_all_apps` = `0` when `cap`, `dtx`, not `kill`, not `test_boot`, not `recovered`, `mode`
  != `intel` and `gfx_ok` = 1; else `3`. It is not tied to the base or to transient reasons: while detached, opted-in
  apps load the shim, which passes through to ANV because `gfx_active` is `intel` (PLAN inv. 14), and the first app
  opened after a reattach gets NVIDIA without waiting for a Settings write.
* The opt-in and opt-out files are recomputed per 24.4 whenever an input changes.
* Queue (`nq_put()`, keys `set:all_apps`, `set:opt_in`, `set:opt_out`, newest wins): once per boot at the first tick
  after `sys.boot_completed=1` (all three); `set:all_apps` whenever the desired value differs from the last one
  queued this boot; `set:opt_in` / `set:opt_out` whenever the file content changed.
* Nothing is queued while `cap` = 0 or `dtx` = 0 (VMware, D8: the keys stay `null`).
* Writes are idempotent; a failed write is logged by gb_notify and repeated at the next boot or change.

### 23.5 Render node permissions (replaces `nodes_apply_new()`'s "new inodes only" rule; review C4)

On every fast tick and after every PCI or drm uevent, for each nouveau node, `stat()` only (PLAN inv. 12):
* expected mode E: render 0666 when `g_gfx.active` is `nvidia`, else 0660; card 0600; owner root:graphics (render) /
  root:root (card);
* a node whose mode is 0000 is never raised by this rule (it belongs to a decision, an `off` window or a wedge); only
  gb_power's own `nodes_restore()` at the end of its own decision raises it;
* a new inode (a rescan or ueventd re-creation, 0666 by default, D19) is set to E; a node more permissive than E is
  lowered to E; a render node at 0660 is raised to 0666 only by this rule while E is 0666;
* skipped while a detach decision runs, after a wedge, while a live `gb_nouveau` owns the ESP lock (its own
  `apply_perms()` window), and for 30 s after gb_power itself started `gb_nouveau_off` while `init.svc.gb_nouveau_off`
  reads `running` (root-only evidence first; the property alone never causes a skip);
* `node_perms()` takes E from `g_gfx.active`; `gfxdriver_on()` is removed.

### 23.6 `reconcile_off()` (replaces `off_rebind_check()`; review B4)

gb_power still never writes `unbind` for a nouveau-bound function outside the detach worker. It starts
`gb_nouveau_off` (whose own refcnt gate decides) when all hold: `mode` = `intel`; the dGPU bound to nouveau; refcnt 0
on two consecutive ticks; no `keep`; not a `gb.nouveau=once|test` boot; no decision, no rebind guard, not wedged;
`init.svc.gb_nouveau_off` and `init.svc.gb_nouveau` not `running`; at least 10 s since its last start. This finishes
an Intel switch after the holders exit and undoes the kernel's own re-bind after a reattach in Intel mode. While
`mode` = `intel`, nouveau is bound and refcnt > 0, the status reports `pending` = `intel` (`n` = holders).

### 23.7 Hot attach, generalised (replaces 7.9 and `hotattach_check()`)

`setprop ctl.start gb_nouveau` when all hold: the dGPU is present, driverless and has a BAR; `attached`; the mode
wants a driver (`mode` != `intel`, or a `once`/`test` boot); not `kill`, not `recovered`, not wedged, no fault;
`vendor.gb.nouveau.booted` = 1; not `rebind_pending`; and either a new appearance of the dGPU function since
gb_power's last start (PCI `add` uevent, or the function newly present in the inventory) or a deferred start (below).
At most once per appearance and 3 times per boot. If `init.svc.gb_nouveau` reads `running` (for example still waiting
for the clean mark of an earlier load), the start is **deferred, not counted**, and issued when the service reads
`stopped`. `set-mode auto|nvidia` also records a deferred start when it finds `gb_nouveau` running (the rc trigger's
`start` is then a no-op). So a quick detach + reattach or a fast mode change is never lost (nouveau-policy Q3, C-21).
With `/sys/module/nouveau` present this lands in the bind path (7.2a).

### 23.8 Rebind guard (reattach with the module loaded; D45, reviews B7 and A5)

The detach worker removes but never unloads nouveau (and `foreign` forbids `rmmod` anyway), so on reattach the kernel
probes the re-added dGPU by itself, from pciehp/surface_hotplug or from gb_power's rescan; `drivers_autoprobe` cannot
stop that for PCI (evidence E6). gb_power marks that probe as a load window:

* **Arm** when `/sys/module/nouveau` exists, no 10de function is present, the guard is not active, not wedged, and one
  of: the DTX base goes detached -> attached; a 10de PCI `add` uevent; gb_power's own rescan is about to write
  `rescan` (then `OP_MARK` is the first op of the `J_RESCAN` job itself).
* **Arming** (main loop, non-blocking): take the ESP lock if it is free (owner `<pid> gb_power <uptime_s>`, the 5.4
  stale steal included); if a live process holds it, log the owner and go on (the kernel probe cannot be held back);
  submit `OP_MARK` (a small `J_MARK` job unless it rides on `J_RESCAN`) that writes `/data/vendor/gb/nouveau.loading` =
  `mode=rebind t=<uptime_s> boot=<boot_id> argv=-` (`O_WRONLY|O_CREAT|O_TRUNC|O_NOFOLLOW`, 0600, `fsync` file and
  directory; a marker of the current boot already there is left alone); `rebind_pending` = 1 (the watch phase);
  `load_window_open()` returns true while `rebind_pending` (detach refused with N15).
* **Watch**: the dGPU function appears (time `t_seen`); within 30 s of `t_seen` it MUST be bound to nouveau with
  `runtime_status` = `suspended` and `power_state` = `D3cold`, and no nouveau function may show
  `runtime_status=error`. gb_power does not read the kernel log, so this watch is weaker than 7.7 (no dmesg patterns;
  HW check H-1B-05).
* **Healthy**: release the ESP lock, `rebind_pending` = 0, re-apply node modes; keep the marker until `t_seen` + 120 s
  and check bind + D3cold again then (the clean mark of this binding); then `OP_UNMARK` (unlink + directory `fsync`) and
  `clean_seen` = 1 (25.2). `gfx_active` may become `nvidia` after the 30 s check (N16 as the first `nvidia` after the
  attach).
* **Failure** (not bound, or no D3cold within 30 s, or `runtime_status=error`, also at the +120 s check): `OP_UNMARK`,
  release the lock, `rebind_pending` = 0, create `keep` and `noload` (as `unhealthy()`; review A5), `set_fault("rebind")`
  (does not block detach, posts nothing itself), `setprop persist.vendor.gb.gfx_mode intel`, N02 with `<why>` =
  `rebind`, `recovered` = 1.
* **No dGPU** within 60 s of arming, or the base detached again: `OP_UNMARK`, release the lock, `rebind_pending` = 0,
  no fault (the staged rescan of 10.4 may run next and re-arm).
* **Crash** during the guard: the marker stays; the next boot's `auto` takes the crash-loop path (Intel + N01).
* **Residual race** (INFERRED, HW H-1B-05): a pciehp re-add can start the nouveau probe before `OP_MARK` is synced
  (milliseconds to about a second). A crash inside that gap reboots without a crash-loop count; the next boot's load is
  fully marked, so a deterministic crash then ends in the crash-loop path one boot later. Arming on the DTX attach event
  keeps the gap small when that event comes first.
* In Intel mode the same guard runs (the kernel binds anyway); `reconcile_off()` unbinds after the guard ends.

### 23.9 Detach integration (D6; reviews E7, B7)

* `decision_start()`: `gfx_force_intel("detaching")` first, then the v1.1 steps; `gpu_users_stop()` and
  `gpu_user_running()` cover `gb_gfx_selftest` too.
* Early refusal: in `decision_step()` (`DEC_REFCNT`), after 5 s with refcnt > 0, no GPU user service running, no load
  window, and a fresh holder scan that finds at least one holder, all of them `closable`: refuse now
  (`decision_busy()`), instead of after 30 s. Any other holder keeps the 30 s wait.
* `decision_busy()`: holders published as before; `detach_ref++`; broadcast; N08 only while GbGpu is absent or has not
  sent `ui-caps notify` this boot.
* **Pending detach** (`close-holders-and-detach`, 26.1): after the force-stops are queued and `gpu_users_stop()` ran,
  `g_pend = {detach, deadline 15 s}`; `pending_detach_step()` (called from `run_due()`, fast tick while pending): when
  the refcnt gate reads 0 on two reads 500 ms apart, issue `SDTX_IOCTL_LATCH_REQUEST` (as `cmd_detach_request()`) and
  clear `pending`; the EC raises REQUEST and the normal decision runs with every gate. At the deadline with refcnt > 0:
  clear `pending`, `detach_ref++` (the app shows the remaining holders). A REQUEST event, a base detach or a decision
  clears it. **Cancel** in the app only dismisses its notification (the EC process already ended at the refusal).
* `dtx_set_base()`: attached -> detached after a confirmed detach (slow or quick path) with `mode` `auto`/`nvidia` and
  `cap` 1: N17. Detached -> attached: arm the rebind guard (23.8) when the module is loaded, and remember "attach
  event" for N16.

### 23.10 App-graphics session marker (review A1, D49)

* `/data/vendor/gb/gfx_session` exists exactly while `gfx_active` is `nvidia` (23.3) and holds the boot id.
* At gb_power start (the first tick at which `/data/vendor/gb` exists): a `gfx_session` with another boot id means the
  previous boot ended while apps could use NVK (crash, panic, power loss): `gfx_unclean` += 1, remove `gfx_session`.
  With `gfx_unclean` >= 2: `setprop persist.vendor.gb.gfx_mode intel`, `gfx_unclean` = 0, `recovered` = 1 for this
  boot, N19 (queued until `sys.boot_completed`). A `gfx_session` with the current boot id (gb_power restarted) is
  adopted.
* `powerctl_check()` (shutdown or reboot under way): remove `gfx_session` at once in the main process (unlink + directory
  `fsync`; the system is going down) and write `gfx_unclean` = 0.
* Every nvidia -> intel transition is a clean end (23.3 step 3).

### 23.11 Package and status changes

`pkgs_refresh()` already re-reads `/data/system/packages.list` when its mtime or size changes; v1.2 then recomputes
`apps_sync` and the opt-in list (installed packages only) and queues the broadcast (coalesced 2 s) so that GbGpu pushes
a fresh catalog. `publish_state()` sets `g_bcast_pending` on every change listed in 10.7.

### 23.12 Untrusted inputs (summary)

* `persist.vendor.gb.gfx_mode` / `gfx_apps`: user settings; a spoof selects a mode or policy, every gate still applies.
* `gfx_ok` only from the root-owned record; `vendor.gb.gfx_active` is output only (an app that sets it to `nvidia`
  gains nothing: the node stays 0660 unless gb_power derived `nvidia`).
* `vendor.gb.nouveau.result`, `init.svc.*`: may select a reason or make gb_power wait or start a service whose own
  guards re-check; never a node mode, never a Settings value, never an unbind.
* App lists from `apps-*` (role ui/shell/root): only select which installed apps may receive the package; every
  package is re-checked against packages.list.

---

## 24. App driver selection (v1.2, D3): the exact updatable-driver mechanism

### 24.1 Mechanism in one paragraph

`ro.gfx.driver.0=gb.sb3.gfxdriver` is always set on native images. gb_power decides three `Settings.Global` keys and
gb_notify writes them:

| Key | Value | Meaning |
|---|---|---|
| `updatable_driver_all_apps` | `0` = app graphics allowed (fall through to the per-app rules); `3` = off for every app | 23.4 decides; `1` and `2` are never written |
| `updatable_driver_production_opt_out_apps` | the "Always Intel" packages | checked before the opt-in list, so Always Intel always wins |
| `updatable_driver_production_opt_in_apps` | the computed list: policy `games` = games + "Use NVIDIA" apps; policy `all` = every launchable non-system app + games + "Use NVIDIA" apps; minus "Always Intel"; installed packages only | an opted-in app skips the allowlist and the denylist |

The driver APK ships **no** `assets/allowlist.txt`, so GpuService keeps `updatable_driver_production_allowlist`
empty and every app that is not opted in gets the system driver (ANV) directly. Switching between the two D3 policies,
or turning app graphics off, changes Settings values only; the APK never changes. Proof: 24.7.

### 24.2 Per-app choices (`/data/vendor/gb/gfx_app_prefs`)

* One choice per package: `intel` ("Always Intel") or `nvidia` ("Use NVIDIA"); no line = default. Lines
  `<pkg> <choice>`, `pkg_name_ok()` syntax, sorted, unique, at most 128.
* Commands `set-app-intel <pkg>:1|0` and `set-app-nvidia <pkg>:1|0` (26.1). Setting one choice replaces the other for
  that package; `:0` clears only that choice. Adding requires the package in packages.list (`ENOENT` otherwise);
  removing works for any package. A choice is kept when the app is uninstalled (it applies again after a reinstall)
  and is simply not in the opt-in list while the package is absent.
* Seed: `/odm/etc/gb/gfx_app_prefs.default` (same format, ships empty) is copied once when the file does not exist.
* Writer: gb_power only, `allow_save()` pattern (unlink the `.tmp`, `O_CREAT|O_EXCL|O_NOFOLLOW`, `fchmod 0644`,
  rename).

### 24.3 App catalog (GbGpu computes, gb_power stores)

* GbGpu (holds `QUERY_ALL_PACKAGES`) lists, for user 0, every package with an activity for `ACTION_MAIN` +
  `CATEGORY_LAUNCHER` whose `ApplicationInfo.flags` has **no** `FLAG_SYSTEM` (so neither stock nor updated system apps,
  review A4), excluding `gb.sb3.*`. Class `g` when `ai.category == ApplicationInfo.CATEGORY_GAME` (the platform's own
  test, `a17 GraphicsEnvironment.java:171`) or `(ai.flags & ApplicationInfo.FLAG_IS_GAME) != 0` (legacy `isGame`),
  else `a`. At most 512 packages (games first).
* It pushes the catalog when a status reply has `apps_sync` = 0: on one connection, `apps-begin <n>`, then
  `apps-add g:<pkg>[,<pkg>...]` and `apps-add a:<pkg>[,<pkg>...]` lines (each <= 255 bytes), then `apps-commit <n>`
  (26.1). `apps_sync` is 0 after every packages.list change (install, update, removal: PackageManager rewrites the
  file) and at every boot (gb_power compares the stamp stored in the catalog header with the current file), and
  gb_power then broadcasts (23.11), so the push happens "on boot and on package add/remove/replace" (D3) without a
  package-broadcast receiver in GbGpu (those implicit broadcasts do not reach manifest receivers).
* gb_power keeps the staged list per connection (discarded on close or a new `apps-begin`); at `apps-commit` it
  re-reads packages.list (`pkgs_refresh(1)`), drops unknown, invalid and duplicate packages (counted as `dropped`),
  writes `gfx_catalog` (header `# pkgs <mtime_ns> <size>` of the packages.list it checked against) and recomputes 24.4.
* Until the first push (first boot) the catalog is empty: only "Use NVIDIA" apps are opted in; games follow seconds
  later.

### 24.4 Computation (`gfx_apps_eval()`)

```
base    = policy == all ? {p in catalog} : {p in catalog | class(p) == g}
opt_in  = (base  U  {p | prefs[p] == nvidia})  \  {p | prefs[p] == intel},  restricted to packages.list, sorted
opt_out = {p | prefs[p] == intel}, sorted
```

Limits: `opt_in` <= 512 packages and <= 64 KiB joined (over the limit: class `a` entries are dropped first, then the
list is cut; logged); `opt_out` <= 128. gb_power rewrites `gfx_optin` / `gfx_optout` only when the content changes and
then queues the matching settings message (23.4). Inputs: `apps-commit`, `set-app-*`, `set-app-policy`, a
packages.list change, boot.

### 24.5 Driver APK constraint

`GbGfxDriver.apk` MUST NOT contain `assets/allowlist.txt` (checked by GD-1B-11 and Wave 2's verify_image). Today's APK
has none (evidence E5). Reason: GpuService copies that asset into `updatable_driver_production_allowlist` at every
boot and on every change of the driver package (24.7 b); an entry `*` would give the driver to every app that is not
opted out, and the games policy could not be expressed.

### 24.6 Writes (who, what, when)

| When | `updatable_driver_all_apps` | `..._opt_in_apps` | `..._opt_out_apps` |
|---|---|---|---|
| Boot, first tick after `sys.boot_completed=1`, `cap` and `dtx` 1 | desired value (23.4) | from `gfx_optin` | from `gfx_optout` |
| `set-mode` crossing `intel` | `3` or desired | | |
| self-test verdict / identity change / kill / test / recovered | desired | | |
| `set-app-policy`, `apps-commit`, packages.list change | | if changed | |
| `set-app-intel`, `set-app-nvidia` | | if changed | if changed |
| VMware (any image), image without the package (`cap` 0 or `dtx` 0) | never | never | never |

Path: gb_power `nq_put()` -> `/data/vendor/gb/notify/*.msg` (`kind=settings`) -> `vendor.gb.notify` -> `gb_notify`
(user shell) -> `cmd settings put|delete global ...` (12.1). Effect: only processes started after the write
(24.7 d); the GUI says so.

First boot after install: until the first write, `all_apps` is unset (= `0`), the opt-in list is unset and GpuService
has reset the allowlist to "" (the package is configured), so no app gets the driver in those seconds.

### 24.7 Source evidence (Android 17 sources in `nvresearch\mgpu\a17\`, sha256 GraphicsEnvironment.java `fa367051...`, GpuService.java `ae26ba16...`, ActivityThread.java `2d7bd7d9...`; lapis 276.R4 binaries on the VM)

**(a) The decision order, `GraphicsEnvironment.chooseDriverInternal()` (`a17 GraphicsEnvironment.java:937-1031`):**
* `:938-947` no `ro.gfx.driver.0`/`.1` -> system driver. Set in the native build.prop.
* `:952-958` `ai.isPrivilegedApp() || (ai.isSystemApp() && !ai.isUpdatedSystemApp())` -> system driver, before any
  setting: SystemUI, launcher and every stock system app stay on ANV in every mode (D1).
* `:971-985` `coreSettings.getInt(UPDATABLE_DRIVER_ALL_APPS, 0)`: `3` (`UPDATABLE_DRIVER_GLOBAL_OPT_IN_OFF`, `:118`)
  -> `return null` at `:972-974` (**fully off**, D3); `1` -> the production driver for every app at `:975-977`,
  *before* the opt-out list is read; `0`/default -> continue (`:981-984`). Values documented at `:110-118`.
* `:986-992` the package is in `UPDATABLE_DRIVER_PRODUCTION_OPT_OUT_APPS` -> `return null` (**Always Intel wins**:
  checked before the opt-in list).
* `:994-999` prerelease opt-in (never written by us).
* `:1007-1010` `isOptIn` = the package is in `UPDATABLE_DRIVER_PRODUCTION_OPT_IN_APPS`.
* `:1011-1018` `if (!isOptIn && allowlist.indexOf("*") != 0 && !allowlist.contains(pkg)) return null` (`"*"` at
  `:96`): with an empty allowlist every app that is not opted in gets the system driver.
* `:1020-1028` the denylist applies only `!isOptIn`.
* `:1030` `return productionDriver`.
* The lists are `split(",")` into a `List` and matched with `contains()` (exact element match, `:351-370`). Package
  names contain no comma.

**(b) GpuService owns the allowlist and denylist, not the opt-in/opt-out/all_apps keys (`a17 GpuService.java`):**
* `:91` reads `ro.gfx.driver.0` once in the constructor (so the property MUST come from build.prop).
* `:113-131` at `PHASE_BOOT_COMPLETED`: `fetchProductionDriverPackageProperties()` and `setDenylist()`;
  `:193-224` again on every `PACKAGE_ADDED/CHANGED/REMOVED` of the driver package.
* `:245-283` resets `updatable_driver_production_allowlist` to "" (`:266-268`) and then copies the APK's
  `assets/allowlist.txt` into it (`:271-277`, via `assetToSettingsGlobal()` `:226-243`); a missing asset throws
  `IOException`, caught at `:238-242`, so the allowlist stays "".
* `:314-331` resets the denylist to "" and fills it only from DeviceConfig denylists for the driver's version code.
* Nothing in GpuService writes `updatable_driver_all_apps`, `..._opt_in_apps` or `..._opt_out_apps`: gb_power's
  values are never overwritten by the platform.

**(c) The keys reach every app process.** `ActivityThread.setupGraphicsSupport()` calls
`GraphicsEnvironment.getInstance().setup(context, getCoreSettingsForDefaultDeviceLocked())`
(`a17 ActivityThread.java:7896-7944`, call at `:7942`; from `handleBindApplication`, `:8200`); `setup()` calls
`chooseDriver()` at `a17 GraphicsEnvironment.java:160`, and the lists are read from that bundle
(`getGlobalSettingsString(null, coreSettings, ...)`, bundle branch at `:357-358`). On lapis 276.R4 the system server
puts exactly these keys into the core-settings bundle: a scan of `/system/framework/services.jar` `classes.dex` from
the VM finds `updatable_driver_all_apps`, `..._production_opt_in_apps`, `..._production_opt_out_apps`,
`..._production_allowlist`, `..._production_denylist` and `..._prerelease_opt_in_apps` loaded only in
`Lcom/android/server/am/CoreSettingsObserver;-><clinit>`, with `updatable_driver_all_apps` registered as
`Ljava/lang/Integer;->TYPE` (evidence E3). This closes 1B Q1 at code level; the run-time effect is H-1B-04.

**(d) Timing.** "the change of settings global variables will only take effect before setup() is called, and any
subsequent change will not impact the current running processes" (`a17 GraphicsEnvironment.java:52-54`): open apps
keep their driver package until restarted, which is what the GUI tells the user.

**(e) Why the other combinations were rejected.**

| Alternative | Why not |
|---|---|
| `all_apps=1` for "all apps" (REQ:44 literal) | returns the driver at `:975-977` before the opt-out list (`:986-992`) is read: "Always Intel" could not win. |
| APK `assets/allowlist.txt` = `*` (1B D1B-1) for "all apps" | the allowlist is GpuService's (`:266-277`, rewritten at every boot and package change); with `*` every app that is not opted out passes `:1014`, so "games + selected" would need an inverse opt-out list of every non-game, which is fail-open for apps installed later; the APK (not Settings) would decide the policy. |
| gb_power writing the allowlist itself | races GpuService's reset at `PHASE_BOOT_COMPLETED` and after driver package changes. |
| the denylist | reset by GpuService (`:314-316`) and skipped for opted-in apps (`:1020-1028`). |
| prerelease driver (`ro.gfx.driver.1`) | needs per-app developer meta-data or a debuggable build (`:960-962`, `:978-980`, `:994-998`). |

**(f) Package checks the platform does at process start** (`chooseDriver()`, `a17 GraphicsEnvironment.java:1036-1101`):
`MATCH_SYSTEM_ONLY` lookup of the driver package (`:1046-1047`; `/odm/app` is a system app), `targetSdkVersion` >= O
(`:1056`), an ABI that matches the process (`:1063`, `chooseAbi()` `:1103-1114`: a 32-bit app process gets no
package because the APK has only `x86_64` libraries), `sphal_libraries.txt` (`:1116-1135`), and
`com.android.graphics.driver.build_time` in `L<digits>` form (`:1090-1097`).

### 24.8 Developer options interplay

With `ro.gfx.driver.0` set, Settings > Developer options shows "Graphics Driver Preferences"
(`nvresearch\mgpu\GDAllPC.java:92-97` availability; `:159-160` writes `updatable_driver_all_apps`;
`GDAppPC.java:179-187` writes the opt-in, prerelease opt-in and opt-out lists). gb_power owns these keys: its next push
(boot or any change) overwrites developer-options edits. Documented for the user (W2G-08); not detected at run time
(review E3).

---

## 25. Automatic self-test gate (v1.2, D4)

### 25.1 Identity

`build=<ro.build.id>/<ro.build.version.incremental>` (lapis today `CL3B.260622.276.R4/16658739`), `krel=<uname -r>`,
`gfx=<first line of /odm/etc/gb/gfx_build_id>`. The `gfx` line carries the NVK hashes (`icd=` the payload ICD the probe
loads, `nvk=` the APK copy apps load; both from one Mesa build, 14) plus `apk=`, `probe=` and `pay=` (a firmware-only
restage changes `pay`; review E1). D4's "(build id, kernel release, NVK hash)" is a subset, so every D4 identity change
re-tests. `ro.build.fingerprint` is not used (65 characters on lapis today, but not bounded by the 91-byte property read
limit, `PROP_VALUE_MAX`). Any change of the identity makes the stored record stale (`gfx_ok` = -1).

### 25.2 Scheduling (`selftest_check()` in `status_tick()`) and the hold

Start `gb_gfx_selftest` (`setprop ctl.start gb_gfx_selftest`, `vendor.gb.gfx_test=running`, remember the start time)
when all hold:
* `cap`, `dtx`, not `kill`, not `test_boot`, not `recovered`, `mode` != `intel`, `attached`;
* the dGPU bound, `state` `standby` or `busy:N`, not `rebind_pending`, no load window;
* `clean_seen`: this binding reached its clean mark (gb_power saw `vendor.gb.nouveau.result=clean` after the bind,
  or the +120 s check of its own rebind guard passed); cleared when the dGPU unbinds or disappears; later result values
  such as `off-skipped` do not clear it (a spoofed `clean` can only start the test early, inside the normal guards);
* no decision, no pending detach, not wedged, no fault; `gb_gfx_selftest` not running;
* and one of: no valid record for this identity; a record with `verdict=error` and `attempts` < 3 that was not yet
  retried in this boot; a `run-selftest` request.

The hold: `hold_eval()` gets a second want bit (`g_st.want`): `power/control=on` on the dGPU through the worker
(`J_HOLD_ON`, as the compute hold), released (main process, `auto`) when `init.svc.gb_gfx_selftest` no longer reads
`running` after the start. `watchdog()` exempts the `d0-idle` rule while either hold is wanted or applied.

### 25.3 `gb_dgpu_report.sh selftest` (service `gb_gfx_selftest`, root)

1. Refuse (exit 1, no file written) unless: native; the cmdline `gb.nouveau` is not `test`; the dGPU is bound to
   nouveau and `/sys/module/nouveau` exists; `/data/vendor/gb/selftest` is a real directory owned by root with mode
   0700; `/dev/gb_dgpu/detaching` does not exist (review E8); `/odm/etc/gb/gfx_build_id` is readable.
2. ESP lock (`lock_esp 120`, owner `gb_dgpu_report`); timeout: write `verdict=error reason=esp` (attempts unchanged),
   exit 0.
3. Crash marker `selftest/running` = `boot=<boot_id> t=<epoch_s>` (noclobber, umask 077) + `sync`.
4. `foreign` (property + `/dev/gb_dgpu/foreign`) BEFORE any probe, exactly as `phase_probe()` (both modes import i915
   buffers).
5. `bootseq`; `report/selftest-<N>.txt` (0640 root:shell, `prune selftest`) with the identity, `uname -r`, the
   nouveau argv and result.
6. `timeout 150 nvk_probe -t ahb-i915-readback -T 60`, then (unless `detaching` appeared: `verdict=error
   reason=interrupted`) `timeout 150 nvk_probe -t a17-swapchain-model -T 60`; default ICD (`/vendor/lib64/hw/
   vulkan.nouveau.so` -> the payload ICD) and ANV leg (`-A` default); output appended, printable ASCII only.
7. Verdict per run (`nvk_probe.c:29-30` exit codes): 0 pass; 1 fail `<mode>`; 124 or 137 fail `<mode> timeout`; 2
   error `icd`; 3 error `nodevice`; other fail `<mode> rc <n>`. Overall: both pass -> `pass`; any fail -> `fail`
   (first failing run, <= 40 characters); else `error`.
8. Write the record (25.4) as `.tmp` (noclobber), `sync`, `mv`; `attempts` = previous attempts for the same identity
   + 1 (a new identity starts at 1); `crashes` carried over for the same identity; remove the marker; `sync`; release
   the lock; exit 0.
9. `trap` on TERM/INT/HUP (ctl.stop, shutdown): kill the running probe, write `verdict=error reason=interrupted`
   (attempts unchanged), remove the marker, release the lock, exit 0.

### 25.4 Record, validation, mirror

Record `/data/vendor/gb/selftest/result` (0644 root:root), `key=value` lines: `verdict` (`pass`, `fail`, `error`),
`reason` (<= 40 characters of `[a-z0-9 :._-]`), `build`, `krel`, `gfx`, `t` (epoch s), `attempts`, `crashes`, `report`
(`selftest-<N>.txt`).

gb_power validates it at start (first tick at which `selftest/` exists), when the service stops, and after writing a
verdict itself: opened with `O_NOFOLLOW`, `fstat` regular file owned by uid 0 (test hook for the uid), <= 2 KiB, every
key present and well-formed, `build`/`krel`/`gfx` equal to the current identity. Then `gfx_ok` = 1 for `pass`, 0 for
`fail`, -2 for `error`; anything else -1. Mirror: `persist.vendor.gb.gfx_ok` = `1` / `0` / empty; `vendor.gb.gfx_test`
= `running` while the service runs, else `pass`/`fail`/`error` for a valid record, else `pending` when 25.2 would start
it once its conditions hold (mode not intel, attached, cap), else `none`. A `fail` posts N18 once per identity.

### 25.5 Interruptions and crashes (review A3)

* Stopped by gb_power (detach decision, `set-mode intel`, the deadline) or by `off`: the script's trap writes
  `interrupted`. If the service has stopped and the marker is still there 10 s later (killed hard), gb_power writes
  `verdict=error reason=interrupted` itself (attempts unchanged) and removes the marker. Interrupted runs are retried
  at the next eligible point (at most once per boot).
* A marker found at gb_power's first tick with another boot id (the boot ended inside the test: panic, freeze, power
  loss): `crashes` += 1 for this identity; `crashes` >= 2 -> `verdict=fail reason=crash` (sticky, N18); else
  `verdict=error reason=crash` (one more automatic try). A marker with the current boot id (gb_power restarted) =
  `interrupted`.
* A clean shutdown during the test goes through the trap (init stops services), so it is not a crash.

### 25.6 Deadline (review C3)

360 s after the start, gb_power sends `ctl.stop gb_gfx_selftest`. If `init.svc.gb_gfx_selftest` still reads
`running` 10 s later (a probe stuck in a kernel ioctl), gb_power latches fault `selftest-hang` (N20; detach is not
blocked by the fault, but the stuck probe holds nouveau, so the refcnt gate refuses a detach until restart and N08
names `nvk_probe`), releases the hold, and writes `verdict=fail reason=hang` (sticky for the identity).

### 25.7 Re-test triggers

Identity change (OTA, kernel, NVK/shim/probe/payload); `run-selftest` (bypasses `attempts` and a stored `fail`; the old
verdict stays in force until the new one is written); `error` (automatic, once per boot, at most 3 attempts per
identity). A `fail` is sticky until the identity changes or the user re-runs it. NVIDIA compute (`compute-start`)
does not depend on the verdict (it needs only `standby`/`busy:N`, 9.3).

---

## 26. Control socket additions (v1.2; framing, roles and errors as 9.1-9.4)

### 26.1 New commands (appended to `g_cmds[]`; one argument at most, as `cl_line()` enforces)

| Command | Roles | Effect | Reply fields (besides `v`, `ok`, `cmd`) | Errors |
|---|---|---|---|---|
| `get-mode` | root, shell, ui | none (fresh tick, `status_fresh()`) | `mode`, `policy`, `active`, `reason`, `gfx_ok` (1, 0, -1, -2), `test`, `test_t`, `cap` (0/1), `base`, `pending`, `apps_sync`, `prefs_n`, `opt_in_n` | |
| `set-mode <m>`, m in `auto`, `intel`, `nvidia` | root, shell, ui | writes `persist.vendor.gb.gfx_mode=<m>`. `intel`: `gfx_force_intel("mode")`, settings re-evaluated (`all_apps=3`); the rc trigger then starts `off` (and `reconcile_off()` finishes it later). `auto`/`nvidia`: settings re-evaluated; the rc trigger starts `gb_nouveau` (a deferred start is recorded if it is running, 23.7). A running self-test is stopped when switching to `intel` (25.5). | `mode`, `active`, `effect` (`now`, `pending`, `next-attach`, `next-boot`), `n` (holders still on NVIDIA; 0 unless `pending`) | `EINVAL` `bad mode`; `ENODEV` `no NVIDIA driver on this image` (not native or `stack_present()` false, as `cmd_enable()`) |
| `set-app-policy <p>`, p in `games`, `all` | root, shell, ui | writes `persist.vendor.gb.gfx_apps=<p>`; recompute 24.4 | `policy`, `opt_in_n`, `effect` (`restart-apps`) | `EINVAL` `bad argument`; `ENODEV` `no NVIDIA app graphics on this image` (`cap` 0) |
| `set-app-intel <pkg>:1`, `set-app-intel <pkg>:0` | root, shell, ui | `:1` sets the choice `intel` (replacing `nvidia`), `:0` clears `intel`; rewrite `gfx_app_prefs`; recompute | `pkg`, `choice` (`intel`, `nvidia`, `default`), `effect` (`restart-app`) | `EINVAL` `bad argument` (`pkg_name_ok()` + `:0`/`:1`); `ENOENT` (`:1` for a package not in packages.list); `EBUSY` (128 choices); `ENODEV` (`cap` 0); `EIO` |
| `set-app-nvidia <pkg>:1`, `set-app-nvidia <pkg>:0` | root, shell, ui | the same with the choice `nvidia` | as `set-app-intel` | as `set-app-intel` |
| `get-apps [<start>]` | root, shell, ui | none | `policy`, `total`, `start`, `next` (index of the next entry, -1 at the end), `prefs`: [{`pkg`, `choice`}] (as many as fit 3500 bytes) | `EINVAL` (start not a decimal <= 128) |
| `apps-begin <n>`, n 0..512 | root, shell, ui | starts (or restarts) a staged catalog on this connection | `n` | `EINVAL` |
| `apps-add <c>:<pkg>[,<pkg>...]`, c in `g`, `a` | root, shell, ui | appends to the staged catalog | `staged` | `EINVAL` (syntax, no `apps-begin`, more than `n`) |
| `apps-commit <n>` | root, shell, ui | `n` MUST equal the staged count; validate (24.3), write `gfx_catalog`, recompute 24.4, queue settings | `n` (accepted), `dropped`, `opt_in_n`, `apps_sync` | `EINVAL` (`no apps-begin`, count mismatch), `EIO` |
| `run-selftest` | root, shell, ui | start `gb_gfx_selftest` now (25.2 conditions except the record ones; bypasses `attempts` and a stored `fail`) | `test` (`running`) | `ENODEV` (`cap` 0, or nouveau not bound), `EBUSY` (already running, detaching, pending detach, wedged, fault, mode `intel`), `EAGAIN` (loading, rebind guard) |
| `list-holders` | root, shell, ui | alias of `holders` (`cmd_holders()`): fresh scan; reply `cmd` is `list-holders` | `holders` | `ENODEV` (nouveau not loaded) |
| `close-holders-and-detach <pid>[,<pid>...]` | root, shell, ui | as `close-holders` (same parser and checks, `cmd_close_holders()`), then the pending detach of 23.9 | `closed`, `refused` (as `close-holders`), `detach` (`pending`) | as `close-holders`, plus `ENODEV` (no DTX, or base not attached), `EBUSY` (a decision runs or a detach is already pending) |
| `ui-caps <c>`, c in `notify`, `none` | ui, root, shell | `notify`: GbGpu posts the actionable detach notification itself this boot, so gb_power suppresses N08 (23.9); `none` revokes it. Not remembered across a gb_power restart | `notify` (0/1) | `EINVAL` |

`compute-svc`, `compute-client` and `other` get `EPERM` for every new command.

### 26.2 `set-mode` effect rules (review B5)

1. `next-boot`: `vendor.gb.nouveau.booted` is not `1`; or a `gb.nouveau=once|test` boot; or `kill`; or wedged; or, for
   `auto`/`nvidia`, `/dev/gb_dgpu/noload` or `keep` exists; or, for `intel`, `keep` exists (the sick driver stays
   loaded until restart, D20).
2. else `next-attach` for `auto`/`nvidia` when the base is not attached (or there is no DTX);
3. else `pending` for `intel` when nouveau is bound and refcnt > 0 (`n` = holder count);
4. else `now`.

`enable` = `set-mode auto` and `disable` = `set-mode intel`; their replies keep the v1.1 `effect` values: `next-attach`
and `pending` are reported as `now`. The 9.6 golden transcript stays byte-identical.

### 26.3 Golden transcripts (v1.2; gb-power tests and the GbGpu mock MUST reproduce them byte for byte)

Fake tree for the first and the last two `set-mode` lines: native, stack, package, `gfx_build_id`, DTX attached,
nouveau bound, D3cold, refcnt 0, `result=clean`, a valid `pass` record with `t=1761390000`, `gfx_mode` and
`gfx_apps` unset, empty catalog and choices, catalog stamp equal to packages.list.

```
C: get-mode\n                     (role ui)
S: {"v":1,"ok":true,"cmd":"get-mode","mode":"auto","policy":"games","active":"nvidia","reason":"ok","gfx_ok":1,"test":"pass","test_t":1761390000,"cap":1,"base":"attached","pending":"","apps_sync":1,"prefs_n":0,"opt_in_n":0}
C: set-mode turbo\n
S: {"v":1,"ok":false,"cmd":"set-mode","err":"EINVAL","msg":"bad mode"}
C: set-mode intel\n               (role other)
S: {"v":1,"ok":false,"cmd":"set-mode","err":"EPERM","msg":"not allowed"}
C: set-app-intel com.example.game\n
S: {"v":1,"ok":false,"cmd":"set-app-intel","err":"EINVAL","msg":"bad argument"}
C: apps-commit 3\n                (no apps-begin on this connection)
S: {"v":1,"ok":false,"cmd":"apps-commit","err":"EINVAL","msg":"no apps-begin"}
C: ui-caps notify\n               (role ui)
S: {"v":1,"ok":true,"cmd":"ui-caps","notify":1}
C: set-mode intel\n               (role ui; a tree whose /odm/etc/gb_hw reads vm)
S: {"v":1,"ok":false,"cmd":"set-mode","err":"ENODEV","msg":"no NVIDIA driver on this image"}
C: set-mode intel\n               (role ui; the first tree, no holders)
S: {"v":1,"ok":true,"cmd":"set-mode","mode":"intel","active":"intel","effect":"now","n":0}
```

### 26.4 Error messages (9.4 codes unchanged)

`bad mode`, `bad argument`, `no NVIDIA driver on this image`, `no NVIDIA app graphics on this image`,
`self-test running`, `a detach is pending`, `no apps-begin`, `count mismatch`, `too many choices`.

---

## 27. GUI (v1.2, D7; component 6)

### 27.1 Quick Settings tile "Graphics" (`.GpuTileService`, same component name)

Label `Graphics`. First matching row wins (status fields, 9.5):

| # | Condition | `Tile.state` | Subtitle (exact), `auto` mode | Subtitle (exact), `nvidia` mode |
|---|---|---|---|---|
| 1 | no socket / `hello` fails / role `other` / `gfx_cap` 0 / `dtx` = `n/a` | UNAVAILABLE | `Unavailable` | same |
| 2 | `state` = `detaching` or `pending` = `detach` | UNAVAILABLE | `Preparing to detach` | same |
| 3 | `gfx_mode` = `intel`, `state` = `busy:N` | INACTIVE | `Off (Intel mode, N on NVIDIA)` | (n/a) |
| 4 | `gfx_mode` = `intel` | INACTIVE | `Off (Intel mode)` | (n/a) |
| 5 | `state` = `wedged` | INACTIVE | `Restart required` | same |
| 6 | `state` = `fault` | INACTIVE | `Fault` | same |
| 7 | `gfx_reason` = `kill` or `state` = `disabled` | INACTIVE | `Disabled for this boot` | same |
| 8 | `gfx_reason` = `crashloop` | INACTIVE | `Intel (recovered)` | `NVIDIA unavailable - recovered` |
| 9 | `gfx_reason` = `test` | INACTIVE | `Intel (test boot)` | same |
| 10 | `base` not `attached` (`not-feasible` counts as attached) | INACTIVE | `Unavailable (base detached)` | `NVIDIA unavailable - base detached` |
| 11 | `gfx_active` = `nvidia`, `state` = `busy:N` | ACTIVE | `NVIDIA (busy N)` | `NVIDIA (manual, busy N)` |
| 12 | `gfx_active` = `nvidia` | ACTIVE | `NVIDIA` | `NVIDIA (manual)` |
| 13 | `gfx_reason` = `loading` | INACTIVE | `Intel (starting NVIDIA)` | `NVIDIA unavailable - starting` |
| 14 | `gfx_reason` = `untested` | INACTIVE | `Intel (testing NVIDIA)` | `NVIDIA unavailable - testing` |
| 15 | `gfx_reason` = `testerror` | INACTIVE | `Intel (NVIDIA test not run)` | `NVIDIA unavailable - test not run` |
| 16 | `gfx_reason` = `selftest` | INACTIVE | `Intel (NVIDIA test failed)` | `NVIDIA unavailable - test failed` |
| 17 | otherwise | INACTIVE | `Intel` | `NVIDIA unavailable - not ready` |

* **Tap** (rows 3-17; rows 1-2 are UNAVAILABLE, which SystemUI does not deliver): `set-mode <next>` with the cycle
  Auto -> Intel -> NVIDIA -> Auto, starting from `gfx_mode` (unset/unknown = auto). Never a dialog. Toasts (exact):
  `next-boot` -> `Takes effect after restart`; `next-attach` -> `Takes effect when the keyboard base is attached`;
  `pending` -> `Switching to Intel when apps on NVIDIA close`; `now` -> none. Locked device: `unlockAndRun` (as v1.1).
* **Long-press**: the Graphics page (`QS_TILE_PREFERENCES` filter on `.SettingsActivity`).
* Rows 5-9 are INACTIVE (not UNAVAILABLE as in v1.1) so the user can still pick the mode for the next boot (review C7).

### 27.2 Settings page "Graphics" (`.SettingsActivity`, injected into Settings > System; 13.2)

Top to bottom, exact labels:
1. **Mode** radio group: `Auto - recommended` (secondary `NVIDIA for apps while the keyboard base is attached, Intel in
   tablet mode`), `Intel only (battery)` (`The NVIDIA GPU stays off`), `NVIDIA` (`Use the NVIDIA GPU for apps
   whenever the keyboard base is attached`). Selecting sends `set-mode`; effects as the tile's toasts; `pending` shows
   the line `<labels> still use the NVIDIA GPU.` and a `Close apps` button (HoldersActivity mode `intel`).
2. **Status:** `Apps you open now use: NVIDIA` or `Apps you open now use: Intel (<reason text>)` (27.6).
3. **NVIDIA GPU:** `asleep (D3cold)` / `active` / `no driver` / `not connected`, plus `, used by <N> app(s): <labels>`.
4. **Self-test:** `Passed on <date>` / `Failed on <date>: <reason>` / `Not run yet` / `Running...` / `Could not run:
   <reason>`; button `Run self-test again`, enabled when `gfx_cap` 1, `gfx_mode` not `intel`, `state` `standby` or
   `busy:N`, `gfx_test` not `running`, no `pending`, not wedged or fault.
5. **Use NVIDIA for all apps** switch (`set-app-policy all|games`); secondary text off: `Games and the apps marked Use
   NVIDIA run on NVIDIA`; on: `Every app you installed runs on NVIDIA, except the ones marked Always Intel`.
6. **Apps**: hint `Changes apply the next time an app starts. Open apps keep their graphics until you restart them.`
   One row per catalog app (games first, then by label): icon, label, package (small), a `Game` badge for class `g`,
   the effective graphics (`NVIDIA` / `Intel`, computed with 24.4 from the catalog, the choices and the policy), and two
   switches `Use NVIDIA` and `Always Intel`; turning one on turns the other off (one choice per package). Row hints: a
   holder whose effective graphics is Intel -> `Using NVIDIA now - restart the app to switch`; any other holder ->
   `Using NVIDIA now`. A failed `set-app-*` reverts the switch and shows the error (`Texts.error()`). Choices come from
   `get-apps` (paged); the app never caches them beyond the page's lifetime.
7. **NVIDIA compute service:** `<compute state>` and the wave-1 compute allowlist section.
8. **How graphics switching works** (help text, exact): `The screen, the system UI and the launcher always use Intel
   graphics. In Auto, apps you open use NVIDIA graphics while the keyboard base is attached, and Intel graphics in
   tablet mode. Apps that are already open keep their graphics until you restart them. Games use NVIDIA by default;
   mark other apps with Use NVIDIA, or turn on Use NVIDIA for all apps. Always Intel keeps an app on Intel graphics.`
9. The wave-1 `Diagnostic report` button.

No socket or `gfx_cap` 0 (VM, D8): the page shows `Unavailable` and every control is disabled.

Injected entry summary (`getDynamicSummary`): `<Mode label>: <tile subtitle>`, mode labels `Auto`, `Intel only`,
`NVIDIA`; e.g. `Auto: NVIDIA (busy 2)`.

### 27.3 Notifications

12.3 (app) and 12.2 (daemon). GbGpu sends `ui-caps notify` on each state broadcast it handles while
`NotificationManager.areNotificationsEnabled()` is true and the `gb_gpu_detach` channel's importance is not NONE, and
`ui-caps none` otherwise. When `detach_ref` grows it fetches `list-holders` and posts the `gb_gpu_detach`
notification (12.3).

### 27.4 HoldersActivity modes (extends v1.1 `EXTRA_THEN_DISABLE`)

| Mode | Title | Text | Positive | Command |
|---|---|---|---|---|
| `close` | `Close apps using the NVIDIA GPU?` | `These apps will be closed. Unsaved work in them may be lost:` | `Close apps` | `close-holders` (+ `stop-compute` when the compute service holds it) |
| `intel` | `Close apps to switch to Intel?` | `These apps still use the NVIDIA GPU. They will be closed. Unsaved work in them may be lost:` | `Close apps` | `close-holders` (gb_power then finishes the switch, 23.6) |
| `detach` | `Close apps and detach?` | `These apps are using the NVIDIA GPU. They will be closed so the screen can be detached. Unsaved work in them may be lost:` | `Close apps and detach` | `close-holders-and-detach` |

Negative button `Cancel` everywhere; non-closable holders under `Cannot be closed here:` (v1.1 text). After
`close-holders-and-detach` the dialog shows `Preparing to detach...` until `dtx` is `confirmed` (then it closes) or
`detach_ref` grows again (then `Some apps could not be closed` with the remaining labels).

### 27.5 Catalog push (GbGpu side)

`StateReceiver` (and the Graphics page on open) checks `apps_sync` in the status; when it is 0 it builds the catalog
(24.3) and sends `apps-begin` / `apps-add` / `apps-commit` on **one** connection (new `GbPowerClient` call that sends a
`hello` and then several request lines, reading one reply per line). A failure is retried at the next broadcast.

### 27.6 Reason texts (exact; review E9)

| `gfx_reason` | Page text |
|---|---|
| `ok` | `NVIDIA` |
| `nopkg` | `Intel (no NVIDIA app graphics on this system)` |
| `nodtx` | `Intel (no keyboard base support)` |
| `kill` | `Intel (NVIDIA disabled for this boot)` |
| `test` | `Intel (NVIDIA test boot)` |
| `crashloop` | `Intel (NVIDIA switched off after a problem)` |
| `wedged` | `Intel (restart required)` |
| `fault` | `Intel (NVIDIA GPU fault)` |
| `mode` | `Intel (Intel only mode)` |
| `detached` | `Intel (keyboard base detached)` |
| `detaching` | `Intel (preparing to detach)` |
| `loading` | `Intel (starting NVIDIA)` |
| `nodriver` | `Intel (NVIDIA driver not running)` |
| `untested` | `Intel (testing NVIDIA)` |
| `testerror` | `Intel (NVIDIA test could not run)` |
| `selftest` | `Intel (NVIDIA self-test failed)` |
| other | `Intel` |

---

## 28. Changes in v1.2 (graphics switching, 2026-10-25)

Sources: REQ, the user's decisions D1-D8 (22.1), `1b\INTERFACES_1B.md` rev 1b-1 (superseded), `1b\REVIEW.md`
(findings A1-E9), read-only checks on the VM and the builder (`1b\evidence\vm_readonly_2026-10-25.md`).

| # | Change (sections) | Components |
|---|---|---|
| G1 | Graphics modes `persist.vendor.gb.gfx_mode` (`auto` default) replace the opt-in `persist.vendor.gb.nouveau` (0, 4, 7.2-v12, 15.1-v12, 22.2) | nouveau-policy, gb-power, gpu-ui |
| G2 | Effective app graphics `vendor.gb.gfx_active`/`gfx_reason`, derived only by gb_power, with transition ordering (23.2, 23.3) | gb-power |
| G3 | App driver selection: `all_apps` 0/3 + computed opt-in + Always-Intel opt-out; policies `games`/`all`; no `allowlist.txt` in the APK; proof from the a17 sources and the lapis dex (24) | gb-power, nouveau-policy (gb_notify), gfx-driver-pkg, gpu-ui |
| G4 | App catalog pushed by GbGpu (`apps-*`), per-app choices `gfx_app_prefs`, `QUERY_ALL_PACKAGES` (24.2, 24.3, 26.1) | gb-power, gpu-ui |
| G5 | Automatic self-test gate with a root-owned record, identity, hold, deadline, interruption/crash semantics (25) | gb-power, nouveau-policy |
| G6 | Render node 0666 only from gb_power's own derivation; script always 0660; enforcement rule without property trust (7.5, 23.5) | gb-power, nouveau-policy |
| G7 | Bind path for a loaded module (7.2a); `refused:keep`, `refused:off`, `bind-failed`, `off-skipped` (7.8) | nouveau-policy |
| G8 | Rebind guard for the kernel's own re-probe on reattach (23.8; evidence E6) | gb-power |
| G9 | `reconcile_off()` replaces `off_rebind_check()`; generalised, deferred hot attach (23.6, 23.7) | gb-power |
| G10 | Detach: Intel before unbind, early refusal for app holders, `detach_ref`, `close-holders-and-detach`, N08 fallback via `ui-caps` (10.3, 23.9) | gb-power, gpu-ui |
| G11 | App-graphics session marker; two unclean NVIDIA sessions force Intel (23.10, N19) | gb-power |
| G12 | Shim: `gfx_active` snapshot per instance, NVK first, ANGLE-HWUI guard, `vendor.gb.gfxdriver` ignored (14) | gfx-driver-pkg |
| G13 | `ro.gfx.driver.0` in the native build.prop; `gb_gfxdriver.sh`/`.rc`, `gb.gfxdriver=`, the gfxtest entry and `/dev/gb_dgpu/gfxdriver` retired (3, 4, 5, 15.6, 16) | gfx-driver-pkg, Wave 2 |
| G14 | Notifications N01/N02/N03 texts, N04 dropped, N06 only for blocking faults, new N16-N20, `kind=settings`, tag `gb_gfx`, queue 16 (12) | nouveau-policy, gb-power, gpu-ui |
| G15 | GUI: tile "Graphics" (27.1), page "Graphics" (27.2), HoldersActivity modes, `gb_gpu_detach` notification, injection category verified on lapis (13.2) | gpu-ui |
| G16 | Status fields and socket commands (9.5, 26) | gb-power, gpu-ui |
| G17 | rc: `selftest/`, `gb_gfx_selftest`, three `gfx_mode` triggers (15.1-v12) | nouveau-policy |
| G18 | Timing constants and size limits (2.3, 2.4); file paths (5.1-5.4) | all |

Review dispositions (`1b\REVIEW.md`):

| Finding | Disposition in v1.2 |
|---|---|
| A1 crashes during app rendering | fixed: session marker (23.10) |
| A2 bind path ignores noload/keep | fixed: guard order 7.2-v12 step 8, `refused:keep` |
| A3 interruptions become a sticky crash | fixed: `interrupted`, two crashes before `fail` (25.5) |
| A4 scope beyond 8a | narrowed by D3 (games + selected, non-system apps only, Always Intel); residual (ANGLE not covered by the self-test, AHB-needing apps) recorded in 20 and H-1B-03/06 |
| A5 rebind failure without keep/noload | fixed (23.8) |
| A6 default undecided | decided by the user: `auto` (D1, D47) |
| B1 stale baseline | fixed: this spec is written against the v1.1 code; TASKS_1B v1.2 cites current lines |
| B2 N15 twice | fixed: N16-N20 (D50) |
| B3 `refused:detaching` | fixed: the v1.1 `refused:detach` with its 15 s wait in the bind path |
| B4 `off_rebind_check()` | fixed: merged into `reconcile_off()` |
| B5 `set-mode` effects | fixed (26.2) |
| B6 `--include-stopped-packages` already live | struck from the work list |
| B7 rebind not a load window | fixed: `load_window_open()` covers the rebind watch |
| C1 tile row 7 unreachable | fixed: row 5 before row 8 (23.2), tile row 8 |
| C2 error retries | fixed: once per boot, `testerror` reason and tile row |
| C3 hung self-test | fixed: 360 s deadline, fault `selftest-hang` (25.6) |
| C4 enforcement trusts properties | fixed (23.5) |
| C5 test boots | fixed: reason `test`, `all_apps=3` |
| C6 kill switch leaves the package active | fixed: `all_apps=3` |
| C7 UNAVAILABLE rows ignore taps | fixed: INACTIVE (27.1) |
| C8 probation counts binds | fixed: binds do not count |
| D1-D6 Wave 2 plan | recorded for Wave 2 in `integration\gfx-switching.md` |
| E1 identity without firmware | fixed: `pay=` in `gfx_build_id` |
| E2 `booted` re-fires triggers | fixed: set only when not already 1 |
| E3 developer options | documented (24.8) |
| E4 `untrusted_app_<n>` | recorded (17, H-1B-12) |
| E5 migration | not needed: wave 1 never shipped |
| E6 zygote path | H-1B-13 |
| E7 single notification path | fixed: `ui-caps` (23.9) |
| E8 self-test vs detaching / off | fixed (25.3, 7.3) |
| E9 reason texts | fixed (27.6) |
