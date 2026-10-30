# NVIDIA GTX 1650 Max-Q (dGPU) on Googlebook OS

The Surface Book 3 13.5" has an NVIDIA GTX 1650 Max-Q (TU117, PCI `10DE:1F91`) in its keyboard base, behind root
port `00:1d.4`. The 15" model's GTX 1660 Ti (TU116) or Quadro RTX 3000 (TU106) is not supported: the
image has no firmware for those chips, so a model gate never loads the driver there. The dGPU stays off, graphics
stay on Intel, and the Graphics tile and page say `Intel (NVIDIA not supported)` (see "Safety rails"; tested off the
device only). Native Googlebook OS images can use it for apps, the way Optimus works on Windows: games and the apps
you choose render on NVIDIA, while the screen and the system UI always stay on the Intel GPU. The images also include
a local compute service (LLM inference on the GPU). This page covers what you get, how switching works, what happens
when you detach the screen, and how to recover from a bad driver start.

> **Untested on hardware.** None of this has run on a real GTX 1650 yet. Every component was built and tested off
> the device: unit and protocol tests, fake sysfs trees, a bionic chroot of lapis 276.R4 with Mesa's drm-shim
> standing in for the TU117, lavapipe, and VMware boots of the native tree (VMware has no NVIDIA GPU, so there
> everything correctly reports `Unavailable`). The first boots on the laptop are the real test. The safety rails
> below are there for that reason.

The VMware image contains none of this. The VM has no NVIDIA device, and VMware already renders the VM on the host
GPU under Windows ([VMWARE.md](VMWARE.md)).

## What works (as built)

| Feature | What it does |
|---|---|
| **Automatic driver start** | When the base is attached, `nouveau` loads by itself after Android has finished booting (`sys.boot_completed`). This also happens on reattach. It never loads during boot, and the boot never depends on it. |
| **Power-off when idle** | With no driver, or with nouveau loaded but idle, the dGPU sits in **D3cold**. nouveau's runtime PM wakes it on demand and lets it sleep again 5 s after the last use. |
| **Per-app NVIDIA graphics** | In Auto mode, games (`CATEGORY_GAME` or the legacy `isGame` flag) and the apps you mark **Use NVIDIA** render their Vulkan, and their GLES through ANGLE, on NVK (Mesa's open NVIDIA Vulkan driver). The frames are shown through the Intel compositor. |
| **Graphics modes** | **Auto** (default), **Intel only** and **NVIDIA**, set from a Quick Settings tile and a Settings page. |
| **Detach handling** | Detaching works like on Windows. With the dGPU idle it takes a few seconds. While apps use it, the latch stays locked and a notification offers **Close apps and detach**. |
| **Self-test gate** | Before the first app may use NVIDIA, a background self-test checks that NVK can render into Intel-allocated buffers and read the result back bit-exactly. It runs again after an OTA or a new kernel/driver build. |
| **Compute service** | `gb_gpu_compute`: llama.cpp with the Vulkan backend on NVK, OpenAI-style HTTP over a local UNIX socket. Never on TCP. |
| **Recovery** | A crash-loop guard switches to Intel after a bad start. A boot-menu kill switch `gb.nouveau=0` turns the driver off without a shell. Boots that load nouveau reboot by themselves 30 s after a kernel panic. |

## How it fits together

```
 display, SurfaceFlinger, system UI, launcher, HWUI  ->  i915 + Mesa ANV + ANGLE + minigbm   (always Intel)
 an app process that Android gives the driver package:
     Vulkan / ANGLE-on-Vulkan -> shim vulkan.intel.so (GbGfxDriver)
                                   |- vendor.gb.gfx_active=nvidia: NVK device first, ANV second
                                   '- otherwise (and HWUI's own Vulkan): ANV only
 NVK (libgb_nvk.so) -> nouveau render node -> TU117     frames: LINEAR buffers from minigbm/i915, sync_fd release
```

* **Kernel:** native images run kernel build #15 since wave 3 ([kernel/README.md](../kernel/README.md)): build #14
  (k13 plus 40 nouveau-only patches) plus two patches that do not touch nouveau (the AVX-VNNI emulator rework and the
  opt-in i915 gamma switch), so the nouveau source is the same as in #14 ([kernel/K15.md](../kernel/K15.md)). The
  payload file name carries the build #15 kernel release. nouveau and its helpers (`drm_exec`, `drm_gpuvm`,
  `gpu-sched`, `drm_ttm_helper`) are in no boot-time load list.
* **Payload:** the NVIDIA firmware (30 `nvidia/tu117` files with their WHENCE excerpt and `LICENCE.nvidia`), the five
  modules, the NVK ICD and the compute libraries live in one erofs image on the boot partition,
  `gb/dgpu-<kernel release>.erofs` (about 60 MB) plus `.sha256`. Before the first load, `gb_nouveau.sh` copies the
  image to `/data/vendor/gb/payload/`, checks it and mounts it at `/odm/dgpu`. None of these files sits in the
  RAM-backed `/odm` tree any more, so the native initrd shrank from about 103 MB to about 58 MB. Do not delete or edit
  the two payload files on the boot partition.
* **Policy** (`native/nouveau-kernel`, `gb_nouveau.sh`) decides whether nouveau may load. It loads or binds the
  driver, switches it off again and runs the self-test.
* **gb_power** (`native/power`), the root daemon that already handles the batteries and detach, is the single owner of
  the graphics state. It derives `vendor.gb.gfx_active` (`intel`/`nvidia`) and its reason, sets the render node
  permissions, decides the three Android `updatable_driver_*` settings, runs the detach decision and serves the
  control socket `/dev/socket/gb_power`.
* **App selection** uses Android's own updatable-driver mechanism, so APKs are not changed. `ro.gfx.driver.0=gb.sb3.gfxdriver`
  is set in the native `/odm/etc/build.prop`. The package (`GbGfxDriver`, label "NVIDIA app graphics") holds the shim
  and NVK. Android loads it only into the apps that the settings name. The shim decides per Vulkan instance and never
  fails an app: if NVK cannot be used, the app gets ANV.
* **GbGpu** (`native/gpu-ui`, package `gb.sb3.gpu`) is the user interface: the tile, the Settings page, the
  notifications and the close-apps dialog. It holds no privileges. Every change goes through gb_power's socket.
  (Wave 6) It follows the signed-in user: lapis's user 0 only shows the login screen, so state updates go to every
  running full user, the notification permission and the tile are set up for your user, the app refreshes itself
  when your user is unlocked, and the tile refreshes each time you open Quick Settings (it is no longer an "active"
  tile, whose update requests lapis's SystemUI dropped). Notices from `gb_notify` are posted for all users.

The full interface (properties, files, socket protocol, notification texts, state tables) is
[native/dgpu/INTERFACES.md](../native/dgpu/INTERFACES.md), version 1.2 r2.

## Graphics modes

| Mode | Base attached | Base detached (tablet) |
|---|---|---|
| **Auto** (default; recommended) | nouveau loaded. Games and **Use NVIDIA** apps get NVIDIA once the self-test has passed. Every other app gets Intel. | Intel for everything; dGPU absent |
| **Intel only** (battery) | No driver, dGPU in D3cold, every app on Intel (plain lapis behaviour) | Intel |
| **NVIDIA** | The same as Auto, kept as a manual choice. The tile says `NVIDIA (manual)`. | Intel. The tile says `NVIDIA unavailable - base detached`. |

* **Which apps:** by default games plus the apps you mark **Use NVIDIA**. The switch **Use NVIDIA for all apps** adds
  every app you installed that has a launcher entry. **Always Intel** on an app always wins. System apps and the
  system UI never get the driver package.
* **When it takes effect:** Android picks the driver when an app process starts. Apps that are already open keep
  their GPU until you restart them. After an attach, the notification says so.
* **Settings behind it** (normally changed from the UI):
  * `persist.vendor.gb.gfx_mode` = `auto` | `intel` | `nvidia` (unset or anything else = `auto`);
  * `persist.vendor.gb.gfx_apps` = `games` | `all`;
  * the per-app choices in `/data/vendor/gb/gfx_app_prefs` (root-owned, written by gb_power).
  * From an adb shell, `setprop persist.vendor.gb.gfx_mode intel` also works. Every safety gate still applies to a
    value set this way.

### Quick Settings tile "Graphics"

The tile shows what an app you open **now** gets. It shows `NVIDIA`, `NVIDIA (busy 2)`, `Intel`,
`Intel (testing NVIDIA)`, `Intel (starting NVIDIA)`, `Intel (NVIDIA test failed)`, `Unavailable (base detached)`,
`Off (Intel mode)`, `Intel (recovered)`, `Disabled for this boot`, `Fault`, `Restart required`,
`Intel (NVIDIA not supported)` (a 15" Surface Book 3) or `Unavailable` (VMware, or an image without the package). **Tap** cycles Auto → Intel → NVIDIA → Auto. A toast says when a change waits for
the base or for a restart. **Long-press** opens the Graphics page.

### Settings → System → Graphics

The page has:

* the mode radios;
* `Apps you open now use: ...`, with the reason when the answer is Intel;
* the GPU line (`asleep (D3cold)`, `active`, `no driver`, `not supported (no driver)`, `not connected`) and the apps
  using it;
* the self-test result with **Run self-test again**;
* **Use NVIDIA for all apps**;
* the app list (games first, each with **Use NVIDIA** / **Always Intel**);
* the compute service status and its app allowlist;
* the help text and the `gb.nouveau=0` recovery hint.

## Attaching and detaching the base

| Situation | What happens |
|---|---|
| Boot with the base attached (Auto/NVIDIA) | After boot completes, nouveau loads. For the first 3 such boots the kernel log is streamed to `/data` (probation), so that a hang leaves a trace. A 30 s health check follows (bound, runtime-suspended, D3cold, no errors). The boot is counted clean after 120 s. The self-test runs on the first healthy load of a new build. |
| Detach, dGPU driverless | Immediate (the old quick path). |
| Detach, nouveau bound but idle | gb_power switches app graphics to Intel **first**, locks the latch and posts "Preparing to detach". It stops the GPU services (self-test, probe, compute), closes the render nodes, waits until nothing holds the driver, unbinds and PCI-removes every NVIDIA function, then confirms. This takes a few seconds; "Tablet mode - Intel graphics" follows. |
| Detach while apps render on NVIDIA | The latch stays locked. After 5 s, **Can't detach yet** lists the apps, with **Close apps and detach** (gb_power force-stops exactly those verified apps, then detaches) and **Cancel**. |
| Detach while a USB/SD drive in the base is mounted, or while the system runs from a stick in the base | Refused ("Can't detach"): eject the drive, or shut down first. |
| Driver starting (load window) | Refused ("The NVIDIA driver is starting. Wait a minute, ..."). |
| The dGPU stopped answering (`wedged`) | The latch stays locked, no further dGPU writes happen this boot, "Restart required". |
| Reattach | The kernel re-probes the dGPU. gb_power guards that re-bind (marker, 60 s wait, 30 s health check). In Auto/NVIDIA app graphics return to NVIDIA with "Performance base connected - NVIDIA graphics on for apps you open now". |

Press the detach key a second time during a decision to abort it. Detach always fails safe: a timeout or an unclear
state cancels the detach rather than removing a GPU that is still in use. Android's compositor and HWUI do not
survive the loss of a GPU they render on. That is why detach is refused instead of being forced.

## The self-test

* **When:** on the first healthy nouveau load for each identity. The identity is the lapis build
  (`ro.build.id/incremental`), the kernel release and `/odm/etc/gb/gfx_build_id` (hashes of the NVK ICD, the APK copy
  of NVK, the APK, `nvk_probe` and the payload). An OTA, a new kernel or a new driver build therefore re-tests.
* **What:** service `gb_gfx_selftest` (root) runs `nvk_probe -t ahb-i915-readback` and then
  `nvk_probe -t a17-swapchain-model`, each with a 60 s watchdog and a 150 s hard limit. gb_power keeps the dGPU
  awake meanwhile.
  * `ahb-i915-readback` renders into a buffer that minigbm/i915 allocated, then reads it back bit-exactly on the CPU
    and on ANV.
  * `a17-swapchain-model` replays Android 17's swapchain sequence.
* **Result:** a root-owned record `/data/vendor/gb/selftest/result`, mirrored to `persist.vendor.gb.gfx_ok` and shown
  on the Graphics page.
  * **Pass:** apps may use NVIDIA ("Performance base connected").
  * **Fail:** apps stay on Intel ("NVIDIA app graphics off"). The compute service still works. The fail stays until
    the identity changes or you press **Run self-test again**.
  * **Error** (could not run): retried once per boot, at most 3 times per identity.
* Not run in kill-switch boots or `[NVIDIA test]` boots. A shutdown during the test records it as interrupted.

## The compute service

`gb_gpu_compute` serves local LLM inference: llama.cpp `b11552` with the ggml Vulkan backend, running on NVK through a
private loader stub. The stub is never the system `libvulkan`, so NVK never becomes a system driver.

* **Socket:** `/dev/socket/gb_gpu_compute` (AF_UNIX, HTTP/1.1, an OpenAI-compatible subset): `GET /health`,
  `GET /v1/models`, `POST /v1/chat/completions`, `POST /v1/completions` (both with `"stream": true` as server-sent
  events) and `POST /gb/unload`. No TCP, not even loopback.
* **Models:** you supply them as `/data/vendor/gb/models/<id>.gguf`. That folder is writable by the adb shell user,
  so `adb push model.gguf /data/vendor/gb/models/` works. One model is resident at a time. Models may be up to
  3584 MiB, with a context of 4096 by default and up to 8192. All layers run on the GPU and there is no CPU fallback.
* **Who may connect:** root, the adb shell user, GbGpu, and the apps on the compute allowlist (managed on the Graphics
  page). Everyone else gets HTTP 403.
* **Lifecycle:** gb_power starts the service on request (`compute-start` on its socket, or from GbGpu), only while
  the dGPU is bound and healthy. The GPU is held awake while a model is loaded. The model is unloaded after 30 s of
  screen-off. The service exits after 120 s with no request and no client, and it stops within 5 s when you detach or
  switch to Intel. It does not depend on the self-test result.

Example, from an adb shell:

```sh
printf 'GET /v1/models HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n' | toybox nc -U /dev/socket/gb_gpu_compute
```

## Safety rails and recovery

| Rail | Effect |
|---|---|
| Late start | nouveau never loads before `sys.boot_completed`, and never from a boot-time module list. |
| i915 guard | It loads only if `card0`/`renderD128` belong to i915 (Intel always owns the display). |
| Model gate | Only a TU117 (PCI device `0x1f80`-`0x1fff`, the 13.5" model) is ever probed: the image has firmware for no other chip. Any other NVIDIA device id (the 15" model's TU116/TU106) gets `refused:model` before any marker, lock, payload, insmod or bind. The dGPU stays driverless and powered off, with one log line and no notification; the Graphics tile and page say `Intel (NVIDIA not supported)`, and gb_power does not retry it. An unreadable id is not gated (one warning line; the driver is tried as before), and ` gb.nouveau.model=any` in the boot menu turns the gate off for one boot. |
| Argument whitelist | Only known nouveau options reach `insmod`. `runpm=1 modeset=2` (runtime PM, no NVIDIA display) always come last. |
| ESP lock | The boot partition is never mounted while nouveau loads. Log writers and the slot follower take the same lock. |
| Panic reboot | `kernel.panic=30` on every boot that loads nouveau. The kernel panics on an oops (`CONFIG_PANIC_ON_OOPS=y`), so a driver crash reboots after 30 s instead of freezing. |
| Crash-loop guard | A marker is written before every load and removed once the load is healthy, or on a clean shutdown. If the next boot finds it, nouveau stays off, the mode becomes **Intel only** and "Switched to Intel graphics" is posted. Two unexpected restarts in a row while apps used NVIDIA (or while nouveau was bound) do the same. |
| Unhealthy load | The driver stays loaded until restart, but unused. The mode becomes Intel and "NVIDIA GPU problem" is posted. |
| Blocking work in a worker | gb_power never does a dGPU sysfs write that can hang in its main process (it also feeds the battery data). A missed deadline marks the dGPU `wedged` for the boot. |
| Node permissions | The nouveau render node is `0666` only while gb_power itself has decided `nvidia`, otherwise `0660 root:graphics`. The card node is always `0600`. A spoofed property gains nothing. |
| HDA function | The dGPU's HDMI audio function (`10de`, class `0403`) stays driverless in every mode. |

**Turning NVIDIA off without a shell** (for example when a boot with the base attached hangs once Android is up):

1. Hold Power for 10 s, then boot again. The crash-loop guard alone usually brings you back on Intel.
2. Or: in the 4-second systemd-boot menu press an arrow key, select the **normal** entry, press **e**, add
   ` gb.nouveau=0` at the end of the line and press Enter. That boot does not load nouveau and every app uses Intel.
   The edit lasts for this boot only.
3. To stay on Intel: tile → `Off (Intel mode)`, or Settings → System → Graphics → **Intel only**. To try again later,
   choose Auto.

Other boot-menu options: `gb.nouveau=once` (load once, whatever the mode), `gb.nouveau.fw=535` (use the GSP-RM
535.113.01 firmware instead of the default 570.144), `gb.nouveau.model=any` (turn the model gate off for this boot,
see "Model gate" above; only for a report about a wrongly refused GPU), `module_blacklist=nouveau` (the same as
`gb.nouveau=0`). Any other `gb.nouveau=` value counts as `0`.

**The `[NVIDIA test]` boot entry** (`gb-<slot>-nvtest.conf`, never the default) adds `gb.nouveau=test`. It loads
nouveau once with extra diagnostics and runs a scripted measurement sequence (power states, PM stress, a suspend test
with on-screen prompts). Apps stay on Intel during that boot. The results go to the boot partition's `gblog\` folder.
The next normal boot is unaffected. `gb_slotsync` keeps this entry in step with the A/B slot after an OTA.

## What will not work

* **The system UI on NVIDIA.** SurfaceFlinger, system_server, SystemUI, the launcher, the lock screen and every app's
  HWUI rendering always use Intel. Only third-party app content can use NVIDIA. Android allows one Vulkan HAL per
  process for the system, and system apps never get an updatable driver.
* **Switching a running app.** An app keeps its GPU until it is restarted.
* **The 15" Surface Book 3's dGPU** (GTX 1660 Ti TU116 or Quadro RTX 3000 TU106). The image stages TU117 firmware
  only, so the dGPU stays off and every app uses Intel. **Intel only** mode gives the same result without the
  refusal line in the log.
* **NVIDIA driving a display.** The dGPU has no display outputs. Its frames cross PCIe into system memory and are
  composed by Intel.
* **Forced ("surprise") detach while the GPU is in use.** It is refused. See above.
* **Video decode/encode, temperatures or clock readout on the NVIDIA GPU** (nouveau on 6.18 offers none of these; no
  hwmon), and **CUDA** or the **proprietary NVIDIA driver**. Neither is redistributable for Android, and neither is
  used.
* **Windows-level performance.** In a public Linux 6.18 benchmark on an RTX 2080 SUPER, NVK reached about 42 % of the
  proprietary driver. Desktop NVK also has no ETC2/ASTC texture compression, which many Android games use. Mark such
  a game **Always Intel** if it fails on NVIDIA.
* **Android hardware buffers in NVK.** The shipped NVK has no `VK_ANDROID_external_memory_android_hardware_buffer`.
  The patch (`native/nvk-android/patches/0003`) is compile-checked only. ANGLE GLES apps that import hardware buffers
  (camera or video frames as textures, WebView/Chrome content) may therefore misrender or fail on NVIDIA (inferred,
  untested). Use **Always Intel** for them.
* **Conformance.** No dEQP/CTS run on the TU117 exists yet.
* **The compute service from Linux (Terminal) apps.** The service listens on a UNIX socket only, and TCP is excluded
  by design.
* **VMware.** There is no NVIDIA device there: the tile and page show `Unavailable`, and the settings are never
  written.

## Status and what to test first

Built and tested off-device (counts from the last runs, 2026-10-25):

* the nouveau policy: 1083/1083 mksh assertions, with GNU and with toybox userland;
* gb_power: 995/995 unit checks, including the graphics-switching matrix (plain and under ASan/UBSan), and 146/146
  socket golden-transcript checks;
* the shim: host, ASan and bionic-chroot runs against lavapipe and drm-shim NVK;
* NVK: ELF/import checks against lapis 276.R4, and the self-test invocations in the chroot;
* the compute service: llama.cpp backend ops on lavapipe through the stub, the service and its limits, and
  reproducible builds;
* GbGpu: 540/540 unit checks, 147/147 checks of its strings against the spec, and its mock compared reply by reply
  with the real gb_power;
* k14: 9 acceptance checks (config/symvers identical to k13, signatures, CRCs against lapis's modules).

Wave 3 re-runs (2026-10-26, after the ESP lock r4 changes and the HWUI check): the nouveau policy 1119/1119
assertions, gb_power 1019 unit checks with 0 failures, and kernel build #15's acceptance checks K-01 to K-14 (among
them: `.config` and `vmlinux.symvers` identical to k14, nouveau source unchanged, every module signed).

The native image boots in VMware with all of this inert (`refused:i915`, tile `Unavailable`). Details:
[native/dgpu/integration/](../native/dgpu/integration/).

Hardware checks for the first sessions with the base attached:

1. Boot with the base. `gblog\boot1-t420.txt` should show `vendor.gb.nouveau.result=clean` and the dGPU in
   `D3cold` when idle. The tile goes `Intel (testing NVIDIA)` → `NVIDIA`, and the Graphics page shows the self-test
   as passed.
2. Start a Vulkan game (or vkcube). `logcat -s gb_gfxdriver` should print
   `NVK: 1 NVIDIA physical device(s) first, then 1 platform device(s)`. The app renders, and `dumpsys gfxinfo` still
   shows HWUI on Intel.
3. An app that is not opted in (or is **Always Intel**) has no `vulkan.intel.so` from the GbGfxDriver APK in its
   `/proc/<pid>/maps`.
4. Detach while idle: a few seconds, then "Tablet mode - Intel graphics". Detach with the game open: "Can't detach
   yet". **Close apps and detach** then completes the detach. Reattach: "Performance base connected".
5. Intel only: the dGPU stays in D3cold (`gb_dgpu_check.sh`). Compare battery drain with Auto, both idle.
6. Boot the `[NVIDIA test]` entry once and keep its `gblog` reports.
7. Compute: push a 1-3B Q4 GGUF model and run a chat completion. Note the tokens/s.
8. (Wave 3) HWUI: the self-test report (`report/selftest-<N>.txt`, section `== hwui_vulkan`) and the boot-log
   summary count the logcat line `queueFamilyProperties.queueCount < kRequestedQueueCount`. It must stay 0. A hit
   means HWUI landed on a Vulkan backend with one queue per family, which on this stack must never happen (HWUI
   never gets NVK): report it with the `gblog\` folder. Do not set `ro.hwui.use_vulkan` or `debug.hwui.renderer`
   yourself (the image must not override them, and the bring-up config refuses them); the supported workaround is
   **Intel only**. Signature: gbOS86 (github.com/linuxkid473/gbos-x86 @f8a5c1a, usb/hook.sh:489-492).
9. (Wave 3; wave 5) Colours: the screen is always driven by Intel, also in NVIDIA mode. A grey, washed-out picture
   ("white veil") is therefore an i915/composer matter, not an NVIDIA one. Wave-5 images set the composer's sRGB
   defaults natively. See [FLASHING.md](FLASHING.md#7-troubleshooting-symptom-log-line-boot-option).

Diagnostics: `adb shell gb_dgpu_check.sh` (read-only snapshot, works as the shell user),
`getprop | grep -E 'vendor.gb.(gfx|nouveau|dgpu|compute)'`, and the `gblog\` boot reports (with the nouveau load
record, the self-test record and the dGPU report). There is no root shell on the native user build. Every check
above works as the shell user, for example over Wireless debugging in Developer options.

## Components

| Path | What |
|---|---|
| [kernel/patches/k14/](../kernel/patches/k14/), [kernel/K14.md](../kernel/K14.md) | 37 linux-6.18.y nouveau backports and the hardening patches N1-N3 |
| [kernel/patches/k15/](../kernel/patches/k15/), [kernel/K15.md](../kernel/K15.md) | build #15 on top of k14 (no nouveau change): the AVX-VNNI emulator rework and the i915 gamma switch |
| [native/nouveau-kernel/](../native/nouveau-kernel/) | `gb_nouveau.sh` (auto/off/bind path), `gb_nouveau.rc`, `gb_dgpu_report.sh` (report, test sequence, self-test), `gb_notify.sh`, `gb_dgpu_check.sh`, the payload builder, the TU117 firmware fetcher with WHENCE/licence |
| [native/power/](../native/power/) | `gb_power` (batteries, DTX detach, dGPU hot-plug, graphics derivation, control socket) |
| [native/nvk-android/](../native/nvk-android/) | Mesa NVK for Android (patch set 8a), `nvk_probe`, chroot/ELF checks |
| [native/gfx-driver-pkg/](../native/gfx-driver-pkg/) | the GbGfxDriver package: the per-instance shim and the APK build |
| [native/gpu-compute/](../native/gpu-compute/) | the compute service, the loader stub, the tests |
| [native/gpu-ui/](../native/gpu-ui/) | the GbGpu app |
| [native/dgpu/](../native/dgpu/) | the interface spec and the integration records |
| `inject/native/components/{nouveau,power,nvk,gfxdriver,gpu_compute,gpu_ui}.json` | how each piece reaches the native tree or the payload |

Inputs you build or fetch yourself: [INPUTS.md](INPUTS.md#nvidia-dgpu-native-only).
