# dGPU (NVIDIA TU117) shared interface spec

Version 1.1, 2026-10-25. The source of truth for the six Wave 1 implementers. It is derived from
`NVIDIA_PLAN.md` (sections 1-13, same scratchpad `nvresearch/` folder). Where it differs from the plan, the reason is
in section 19 (`D-n` numbers are cited inline). If this spec and the plan disagree, **this spec wins**. If this
spec is silent or self-contradictory, stop and record the question in your `integration/<component>.md` under
"Spec questions" instead of guessing an interface.

v1.1 is the post-review revision (two Wave 1 reviews: interface consistency and safety). Every change is listed in
section 21 with the review finding it answers; changed text is otherwise integrated in place. All six components
implement v1.1.

Words: **MUST** / **MUST NOT** are binding; **SHOULD** means "do it unless you write down why not". `INFERRED` means
reasoned from source, not observed. `HW` marks a check that needs the real Surface Book 3 + base (nobody in Wave 1 can
run it). Paths are on the device unless prefixed `C:\` (Windows host) or `builder:` (Ubuntu builder VM).

---

## 0. Quick reference

| Item | Value |
|---|---|
| Kill switch (boot editor) | append `gb.nouveau=0` |
| Test boot entry | `loader/entries/gb-<x>-nvtest.conf`, options += `gb.nouveau=test` |
| Per-app graphics experiment entry (P8 only) | `loader/entries/gb-<x>-gfxtest.conf`, options += `gb.nouveau=once gb.gfxdriver=1` |
| Persistent opt-in | `persist.vendor.gb.nouveau=1` |
| Live state (single writer gb_power) | `vendor.gb.dgpu.state` |
| Policy result (single writer gb_nouveau.sh) | `vendor.gb.nouveau.result` |
| Control socket | `/dev/socket/gb_power`, AF_UNIX stream, line request -> one-line JSON reply |
| Compute socket | `/dev/socket/gb_gpu_compute`, AF_UNIX stream, HTTP/1.1 (OpenAI-style subset) |
| Payload on ESP | `gb/dgpu-<krel>.erofs` + `gb/dgpu-<krel>.sha256` |
| Payload copy | `/data/vendor/gb/payload/dgpu.erofs` |
| Payload mount | `/odm/dgpu` (erofs, ro), bind `/odm/dgpu/firmware/nvidia` -> `/odm/firmware/nvidia` |
| k13 kernel release | `6.18.21-android17-5-g3938c8d97142-dirty-ab16302624` (verified, D10) |
| UX app | package `gb.sb3.gpu`, `/odm/app/GbGpu/GbGpu.apk` |
| Driver package (P8, gated) | package `gb.sb3.gfxdriver`, `/odm/app/GbGfxDriver/GbGfxDriver.apk` |
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
| `GbGfxDriver.apk`, `gb_gfxdriver.sh`, `gb_gfxdriver.rc`, `gfxdriver.json` | 4 | `C:\GB-build\native\gfx-driver-pkg\out\` | Wave 2, only behind `GB_GFXDRIVER=1` (P8 gate) |
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

### 2.4 Size limits

* System property values: <= 91 bytes (non-`ro.`); names: <= 31 characters (all names in section 4 comply).
* Control socket: request line <= 255 bytes including `\n`; reply line <= 4096 bytes including `\n`.
* Notification title <= 40 characters, text <= 200 characters, ASCII only.

---

## 3. Kernel command line

Parsing rules for every consumer: split `/proc/cmdline` on whitespace; for a repeated key the **last occurrence wins**
(the systemd-boot editor appends); values are case-sensitive; an unknown value of a known key is logged.

| Token | Values | Effect | Consumers |
|---|---|---|---|
| `gb.nouveau=` | `0` | Kill switch for this boot: no nouveau load, no runtime enable, `mode=disabled`. Persistent settings are not changed. | gb_nouveau.sh, gb_power (status) |
| | `once` | Load this boot, non-persistent; `off` requests are ignored this boot. | gb_nouveau.sh |
| | `test` | As `once`, plus test extras (section 7.6) and the Phase 2B report sequence. | gb_nouveau.sh, gb_dgpu_report.sh |
| | anything else | Treated as `0` (fail safe, D17); logged `unknown gb.nouveau value`. | gb_nouveau.sh |
| `gb.nouveau.fw=` | `535` | Adds `NvGspFw=0` to the nouveau `config=` (forces the 535.113.01 GSP fwif; verified GKI `include/nvkm/core/firmware.h:53-58`, `nvkm/subdev/gsp/tu116.c:47-48`). | gb_nouveau.sh |
| | `570` | Accepted no-op (570.144 is the default). | gb_nouveau.sh |
| | other | Ignored, logged. | gb_nouveau.sh |
| `gb.gfxdriver=` | `1` | P8 experiment: at post-fs-data `gb_gfxdriver.sh` sets `ro.gfx.driver.0` and `vendor.gb.gfxdriver=on` and creates the root-only flag `/dev/gb_dgpu/gfxdriver`, but only if the driver APK exists. Nothing else reads the raw token. The P8 gate everywhere else is **`vendor.gb.gfxdriver=on` AND the flag file** (the property alone is untrusted, section 4): nouveau render node 0666 (gb_nouveau.sh and gb_power alike), status field `gfxdriver`. The shim keys on the property (it cannot read `/dev/gb_dgpu`; a spoofed property only lets that app try NVK, the node mode still refuses it). The `foreign` flag at load is set on the property OR the file (safe direction). Any other value = off. | gb_gfxdriver.sh (only reader of the token) |
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
(`persist.vendor.gb.nouveau`) and triggers whose handlers re-check real state.

**Monotonic flags.** `used`, `foreign` and `wedged` are per-boot and only ever go from "no" to "yes". For `used` and
`foreign` a writer sets the property (`vendor.gb.nouveau.used` / `.foreign`) to `1` **and** creates the root-only file
`/dev/gb_dgpu/<flag>`; a reader treats the flag as set if the property is `1` **or** the file exists. `wedged` has no
property: it is the file `/dev/gb_dgpu/wedged` (plus gb_power's in-memory copy), and its public face is
`vendor.gb.dgpu.state=wedged`. Nobody writes `0` or deletes a flag file (D9).

| Name | Len | Values (default) | Writer(s) | Readers | Meaning |
|---|---|---|---|---|---|
| `persist.vendor.gb.nouveau` | 25 | `1`, `0` (unset = `0`) | user (adb), gb_power (`enable`/`disable`), gb_nouveau.sh (sets `0` on crash-loop/unhealthy) | gb_nouveau.sh, init rc, gb_power | Persistent opt-in. |
| `persist.vendor.gb.nouveau.args` | 30 | space-separated whitelist atoms (unset = none) | user (adb) | gb_nouveau.sh | Extra nouveau options, section 7.4. |
| `vendor.gb.nouveau.mode` | 22 | `default`, `disabled`, `once`, `test`, `persist` | gb_nouveau.sh | gb_power, report, bootlog | Policy mode this boot (latest auto run). |
| `vendor.gb.nouveau.result` | 24 | section 7.8 | gb_nouveau.sh (only) | gb_power, UI via status, report | Outcome of the last policy action. |
| `vendor.gb.nouveau.loading` | 25 | `1`, `0` (unset = `0`) | gb_nouveau.sh | gb_bootlog, gb_slotsync, gb_power | Load window open (informational; mutual exclusion is the ESP lock, section 5.4). |
| `vendor.gb.nouveau.booted` | 24 | `1` (unset otherwise) | gb_nouveau.sh | init rc | Boot-time auto run finished; arms the runtime enable/disable triggers. Never set in kill-switch or crash-loop boots. |
| `vendor.gb.nouveau.used` | 22 | `1` | gb_nouveau.sh (when its load window opens, right after it took the ESP lock), gb_power (when it sees nouveau bound or loaded) | gb_power | Monotonic: a nouveau load window opened, or nouveau touched the dGPU, this boot (a detach then takes the checked slow path). |
| `vendor.gb.nouveau.foreign` | 25 | `1` | gb_nvk.rc (`nvk_probe` run), gb_dgpu_report.sh (before anb modes), gb_nouveau.sh (gfxdriver boots) | gb_nouveau.sh (`off`), gb_power | Monotonic: nouveau buffers were shared with another device; `rmmod` forbidden this boot. |
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
| `vendor.gb.gfxdriver` | 19 | `on`, `off` | gb_gfxdriver.sh | shim, gb_nouveau.sh, gb_power | P8 experiment effective this boot. Unset = `off`. gb_nouveau.sh and gb_power honour it only together with `/dev/gb_dgpu/gfxdriver` (section 3). |
| `vendor.gb.power.api` | 19 | `1` | gb_power | UI, tests | Control socket is listening, protocol version 1. |
| `vendor.gb.dtx.mode` / `.base` / `.latch` | <=19 | existing values | gb_power | existing | Unchanged. |
| `persist.vendor.gb.perf_profile` | 30 | existing | user | gb_power | Unchanged. |
| `ro.gfx.driver.0` | 15 | `gb.sb3.gfxdriver` | gb_gfxdriver.sh, only in `gb.gfxdriver=1` boots | Android GraphicsEnvironment | P8 only. Never in any build.prop until the 8a gates pass. |
| `ro.hardware.vulkan`, `ro.hardware.egl` | | lapis values (`intel`, `angle` native) | nobody | | MUST NOT be set or overridden by any component. |

Settings (not properties), P8 only: `Settings.Global updatable_driver_production_opt_in_apps` (comma list of app
packages that use the driver package), set by the user via `adb shell settings put global ...` or later by GbGpu.

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
| `nouveau.loading` | file | 0600 root:root | gb_nouveau.sh | gb_nouveau.sh | Crash-loop marker. One line `mode=<mode> t=<uptime_s> argv=<args>`; only existence matters. Created + `sync`ed right before the first insmod; removed at the clean mark, on an unhealthy result, or by the crash-loop path. |
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
| `gfxdriver` | gb_gfxdriver.sh | Monotonic. P8 boot: the root-only half of the gfxdriver gate (section 3). |
| `detaching` | gb_power | **Not monotonic.** Exists from the moment gb_power starts handling a detach request until the EC process is over (cancel echo, latch closed, timeouts). gb_nouveau.sh waits for it (15 s) before its load window and refuses insmod while it exists (7.2). |
| `probe.<pid>` | gb_dgpu_report.sh | Temporary nvk_probe output of the test sequence. |
| `bootseq.lock/` | gb_nouveau.sh, gb_dgpu_report.sh | mkdir lock for `bootseq`. |

### 5.3 `/odm` and `/vendor`

| Path | Origin | Notes |
|---|---|---|
| `/odm/etc/gb_hw` | gbinit (existing) | `native` / `vm`. |
| `/odm/etc/gb/tu117.sha256` | cpio (nouveau.json) | `firmware_staged_sha256.txt`, `sha256sum` format, paths `nvidia/tu117/...`. |
| `/odm/etc/gb/dgpu_payload.sha256` | cpio (build_esp P5) | Byte-identical copy of `ESP:/gb/dgpu-<krel>.sha256`. Present = payload mode; absent = legacy mode (section 6.4). |
| `/odm/dgpu/` | cpio empty dir 0755 (P5) | erofs mount point. |
| `/odm/firmware/nvidia/` | cpio: legacy = the 30 firmware files; P5 = empty dir 0755 | Bind target. `firmware_class.path=/odm/firmware,/vendor/firmware` (existing) finds `nvidia/tu117/...` there. |
| `/odm/lib/modules/{drm_exec,drm_gpuvm,gpu-sched,drm_ttm_helper,nouveau}.ko` | legacy only | Removed from the cpio by P5. Never in any `modules.load*`. |
| `/vendor/bin/gb_nouveau.sh`, `gb_dgpu_report.sh`, `gb_dgpu_check.sh`, `gb_notify.sh` | cpio `vup/bin/` | 0755. |
| `/vendor/bin/gb_power` | cpio `vup/bin/` | Existing. |
| `/vendor/bin/nvk_probe` | cpio `vup/bin/` (stays a real file in P5, D7) | 42 KB. Default ICD path stays `/vendor/lib64/hw/vulkan.nouveau.so`. |
| `/vendor/lib64/hw/vulkan.nouveau.so` | legacy: real file; P5: symlink -> `/odm/dgpu/lib64/hw/vulkan.nouveau.so` | Dangling until the payload is mounted; nothing but nvk_probe/compute ever opens it. |
| `/vendor/bin/gb_gpu_compute` | cpio `vup/bin/` (component 5) | Small launcher; real code in the payload (D7). |
| `/vendor/bin/gb_gpu_ui_setup.sh` | cpio `vup/bin/` (component 6) | |
| `/vendor/bin/gb_gfxdriver.sh` | cpio `vup/bin/` (component 4, P8 builds only) | |
| `/odm/app/GbGpu/GbGpu.apk` | cpio `app/GbGpu/` (component 6) | Non-privileged system app. |
| `/odm/app/GbGfxDriver/GbGfxDriver.apk` | cpio `app/GbGfxDriver/` (P8 builds only) | |
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

| Node | Normal | P8 boots (`vendor.gb.gfxdriver=on` AND `/dev/gb_dgpu/gfxdriver`) |
|---|---|---|
| nouveau `renderD<n>` | `chown root:graphics`, `chmod 0660` | `chmod 0666` |
| nouveau `card<n>` | `chown root:root`, `chmod 0600` | same |

Applied by gb_nouveau.sh after load and re-applied by gb_power after every nouveau bind it observes (rebind after
rescan, D19); both use the same two-part gate (section 3). During a detach decision gb_power instead keeps every
nouveau node at 0000, including nodes that appear during the decision. i915 nodes are never touched.

### 7.6 Panic policy and test extras

* Every boot that loads nouveau (once, test, persist, runtime enable): `echo 30 > /proc/sys/kernel/panic` before the
  first insmod (an oops then reboots after 30 s instead of freezing, and the crash-loop guard disables nouveau).
* `test` mode additionally: `echo 0 > /proc/sys/kernel/panic_on_oops`, `setprop vendor.gb.dgpu.dmesg 1`, and the
  `debug=gsp=debug` default.
* Probation persist loads: `setprop vendor.gb.dgpu.dmesg 1` (panic_on_oops unchanged).
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
exit 1 (D20).

Load record `report/load-<N>.txt` (`key=value` lines): `mode`, `argv`, `krel`, `payload`, `mem_available_before_kb`,
`mem_available_after_kb`, `insmod_ms`, `bind_ms`, `d3cold_ms`, `rm_version` (from the `RM version:` dmesg line),
`fbsr_line`, `render_map` (`renderD128=0x8086:i915,renderD129=0x10de:nouveau`), `refcnt_baseline`, `health`.

### 7.8 `vendor.gb.nouveau.result` values

`idle`, `disabled`, `crashloop`, `refused:hw`, `refused:wedged`, `refused:noload`, `refused:boot`, `refused:i915`,
`refused:nodgpu`, `refused:bar`, `refused:module`, `refused:detach`, `refused:esp`, `refused:payload`,
`refused:marker`, `loading`, `insmod-failed:<module>`, `healthy`, `clean`, `unhealthy:bind`, `unhealthy:d3cold`,
`unhealthy:dmesg`, `off-ignored`, `off-busy:<n>`, `off-wedged`, `off-unbound`, `off-done`. (v1.1 added
`refused:noload`, `refused:boot`, `refused:detach`; `refused:marker` was NP-D5.)

### 7.9 Hot attach

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

### 8.3 UI mapping (normative for component 6)

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
| `enable` | root, shell, ui | `persist.vendor.gb.nouveau=1` | `effect`: `now` when booted=1, mode not disabled/once/test, result not crashloop, no `/dev/gb_dgpu/noload`, and the runtime start can actually load (not: `nouveau.ko` loaded while the dGPU is not bound to it, or `init.svc.gb_nouveau=running` while not bound -- `auto` is then a no-op); else `next-boot` | `ENODEV` (not native, or neither `/odm/lib/modules/nouveau.ko` nor `/odm/etc/gb/dgpu_payload.sha256` exists), `EBUSY` (wedged) |
| `disable` | root, shell, ui | `persist.vendor.gb.nouveau=0` (gb_nouveau_off then runs via rc) | `effect`: `now` (booted=1, mode not once/test, no `/dev/gb_dgpu/keep`) or `next-boot` | |
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

Field rules: `n` = 0 unless busy; `holders` empty unless refcnt > 0 (scan rate-limited, 2.3); `refcnt` = -1 when the
module is not loaded; `bdf`/`driver`/`runtime_status`/`power_state` empty strings when no dGPU; `dtx` in `ready`,
`in-progress`, `confirmed`, `n/a`; `base` in `attached`, `detached`, `not-feasible`, `unknown`; `probation` = 1 while
`mode=persist` and `clean_boots < 3`; `gfxdriver` = 1 only with `vendor.gb.gfxdriver=on` AND `/dev/gb_dgpu/gfxdriver`
(v1.1); `t_ms` = CLOCK_MONOTONIC ms. Clients MUST ignore unknown fields.

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
  gb_nouveau.sh's job.
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

### 10.5 Watchdog and compute hooks

Watchdog per 8.2. Before any detach slow path: `setprop ctl.stop` for `gb_dgpu_test`, `gb_nvk_probe` and
`gb_gpu_compute` (10.3 step 4); on `stop-compute`: `ctl.stop gb_gpu_compute`. The compute hold (9.3 `compute-state`)
exempts the `d0-idle` rule.

### 10.6 Optional IOMMU domain (HW-gated)

The P3 optional `DMA-FQ` iommu group switch is implemented behind a compile-time default-off flag and a property is
**not** used to enable it. Shipping it needs the Phase 3 hardware run.

### 10.7 State broadcast to GbGpu

When the state enum or `n` changes (coalesced, at most one per 2 s, only after `sys.boot_completed=1`, only if
`/odm/app/GbGpu/GbGpu.apk` exists), gb_power queues a `broadcast` message (section 12). gb_power never forks Java tools
itself.

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
  * `kind=force-stop` with `pkg=` and `user=` (decimal).
* Then `setprop vendor.gb.notify <epoch_ms>`. gb_power queues in memory until `sys.boot_completed=1` (max 8, newest
  per tag kept) and until `/data/vendor/gb/notify` exists.
* `gb_notify.sh`: process files in name order until the directory is empty (re-scan after each batch), deleting each
  after the attempt. Validate: `tag` in {`gb_dgpu`, `gb_dtx`, `gb_dgpu_test`}; `pkg` regex as in 9.3; titles/texts
  printable ASCII. Delivery:
  * notify: `cmd notification post -t "<title>" "<tag>" "<text>"` (same tag replaces the previous one);
  * broadcast: `am broadcast --user 0 --include-stopped-packages -a gb.sb3.gpu.action.STATE_CHANGED -p gb.sb3.gpu`
    (v1.1: also reaches a force-stopped GbGpu; the intent stays explicit);
  * force-stop: `am force-stop --user <user> <pkg>`.
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
| N15 | gb_dtx | Can't detach yet | The NVIDIA driver is starting. Wait a minute, then press detach again. | gb_power (load window open, v1.1) |

`<holders>` = comma-separated app labels or `comm` names (max 5, then `and N more`). `<why>` = `bind`, `d3cold` or
`dmesg`. `<reason>` = `fault_reason`.

Shell-posted notifications appear under the Shell app's notification channel (platform behaviour); that is accepted.

### 12.3 GbGpu's own notifications

| Channel id | Name | Importance | Notification |
|---|---|---|---|
| `gb_gpu_status` | NVIDIA GPU status | LOW | id 1, ongoing while state is `busy:N`: title `NVIDIA GPU in use`, text `<N> app(s) are using the NVIDIA GPU`; cancelled otherwise. |
| `gb_gpu_alerts` | NVIDIA GPU alerts | HIGH | id 2, only for results of UI actions (e.g. `close-holders` refused). |

Triggered by the state broadcast (10.7): the receiver queries `status` and updates. The app never duplicates N01-N14.

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

### 13.3 GbGfxDriver (component 4, P8 gated)

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
| `gb_nouveau` | root | boot_completed; runtime enable; gb_power hot-attach (`ctl.start`) | |
| `gb_nouveau_off` | root | runtime disable; gb_power after a re-bind with `/dev/gb_dgpu/off` (10.1) | |
| `gb_dgpu_dmesg` | root | `vendor.gb.dgpu.dmesg` | |
| `gb_dgpu_report` / `gb_dgpu_test` | root | `vendor.gb.dgpu_report=1/2` | |
| `gb_notify` | shell | `vendor.gb.notify` | |
| `gb_nvk_probe` | shell (group graphics; v1.1) | `vendor.gb.nvk_probe=1` (existing) | stopped by gb_power (detach) and `off` |
| `gb_gpu_compute` | nobody | gb_power `ctl.start` | payload only |
| `gb_gpu_ui_setup` | shell | boot_completed | |
| `gb_gfxdriver_setup` | root | post-fs-data exec | P8 builds only |

---

## 16. Boot entries

* `loader/entries/gb-<x>-nvtest.conf` (x = slot letter, lower case): identical to `gb-<x>.conf` except
  `title   <base title> (slot <X>) [NVIDIA test]` and `options <base options> gb.nouveau=test`. Written by build_esp for
  native ESPs (USB, internal, ISO payloads) and replicated for the other slot by gb_slotsync (whose title rewrite keys
  on `(slot `). Never the default entry. Not in the VM ESP.
* `loader/entries/gb-<x>-gfxtest.conf` (P8 builds only): title suffix `[NVIDIA app graphics test]`, options
  `<base options> gb.nouveau=once gb.gfxdriver=1`.
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

---

## 18. Per-component scope and acceptance tests

Test ids are used in the integration files and the final report (`PASS`/`FAIL`/`SKIP` + evidence path). `HW` tests are
listed so that the README can mark them; they are not run in Wave 1. "Wave 2" tests need the shared pipeline or the VM
(VM boots touch `lapis.bin` and need the user's approval).

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
| D33 | (v1.1) render 0666 on the property | Property AND `/dev/gb_dgpu/gfxdriver` in gb_nouveau.sh and gb_power | Any app can set vendor.* properties (review R1-H2/R2-4). |

---

## 20. Open decisions (user) and assumptions

* Assumed yes (plan 12.1): "all images" = all **native** images; the VM image carries none of this.
* Pending (plan 12.2): `kernel.panic=30` on nouveau boots. Implemented as the constant `GB_PANIC_TIMEOUT=30` in
  `gb_nouveau.sh`, so Wave 2 can change it in one place.
* Builder additions (plan 12.3): mksh, erofs-utils, apksigner/zipalign/aapt2 are installed; any `sdkmanager` install
  stays inside the component's scratch dir. A VK-GL-CTS build is not part of Wave 1.
* Phase 2 boot medium (12.4) and research scratch retention (12.5): unchanged, user decisions.
* Base-port mapping for the detach guard is unknown until Phase 2: every USB/SD storage device counts as base-side.
* Open for v2 (review R2-5, optional part): `persist.vendor.gb.nouveau` itself is a property any app can set, so any
  app can opt nouveau in (probation, the crash-loop guard and the kill switch still apply). A v2 could honour persist
  only together with a root-only opt-in file written by gb_power's `enable` (roles root, shell, ui). Not done in v1.1
  because it changes the adb workflow (`setprop persist.vendor.gb.nouveau 1`) that the docs and tests use.

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
