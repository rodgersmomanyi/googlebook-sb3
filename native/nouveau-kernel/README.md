> **Repository note.** This is the component's working README, copied from the development tree. Paths
> such as `C:\GB-build\native\nouveau-kernel` mean `native/nouveau-kernel` in this repository, `C:\GB-build\native\dgpu`
> is [`native/dgpu`](../dgpu/), `C:\GB-x86` is the workspace (the repository root) and `builder:` paths are
> on the Linux builder. Build outputs, logs and older versions (`out/`, `logs/`, `superseded/`, `*.v1.1`, ...)
> are not published. What the NVIDIA support does for a user: [docs/NVIDIA.md](../../docs/NVIDIA.md).

# nouveau-kernel: nouveau policy for the Surface Book 3 dGPU (component `nouveau-policy`)

**v1.2 r2 (review fix pass, INTERFACES section 29, 2026-10-25):** `auto` removes its own crash-loop marker on a
clean shutdown (SIGTERM with `sys.powerctl`) once the probe returned (R-1); the health window and clean mark count
seconds slept, and a `gb_nouveau` wakelock covers the load window (R-7); `off` waits for gb_power's rebind guard (R-2)
and has a 20 s `rmmod` deadline (R-11); bound nodes at 0660/0666 are left to gb_power and permissions are re-applied
before the window closes (R-10); the self-test holds a `gb_gfx_selftest` wakelock and waits for `nvk_probe` on a stop
(R-7, R-11). Shas and tests: `integration\nouveau-policy.md` section 8; pre-fix files in `native\v1.2r1\` and
`test\v1.2r1\`.

The scripts in this folder decide whether nouveau may drive the NVIDIA GTX 1650 Max-Q (TU117) in the Surface Book 3
keyboard base, load (or bind) it safely after boot, switch it off again, run the NVIDIA app-graphics self-test, report
on the hardware, and deliver the notifications and the Settings writes that gb_power queues. They run on native
Googlebook OS images only. The VMware image ships none of this.

Contract: `C:\GB-build\native\dgpu\INTERFACES.md` **v1.2** (sections 2-7, 12, 15.1-v12, 18.1, 22.2, 25.3; the v1.2
changes are listed in 28) and `C:\GB-build\native\dgpu\1b\TASKS_1B.md` v1.2 section 1 (tasks T1.1-T1.11, tests
NP-1B-01..25). Plan: `NVIDIA_PLAN.md` Phase 1 items 1-4, P0-6 and Phase 5 (stage step and payload tool). Wave 2
integration: `C:\GB-build\native\dgpu\integration\nouveau-policy.md` (section 7 is v1.2) and `gfx-switching.md`.

## Files

| File | Installed as | What it does |
|---|---|---|
| `native/gb_nouveau.sh` | `/vendor/bin/gb_nouveau.sh` (cpio `vup/bin/`, 0755) | `auto` (insmod path and, v1.2, the bind path), `off`, `dmesg-stream` (INTERFACES 7.1-7.8). |
| `native/gb_nouveau.rc` | appended to the native `gb.rc` | Services and triggers, exactly INTERFACES 15.1-v12 (7 services). |
| `native/gb_dgpu_report.sh` | `/vendor/bin/gb_dgpu_report.sh` | `report`: read-only report (every boot from gb_bootlog). `test`: the Phase 2B test sequence (`gb.nouveau=test` boots only). `selftest` (v1.2): the automatic NVIDIA app-graphics self-test (INTERFACES 25.3). |
| `native/gb_notify.sh` | `/vendor/bin/gb_notify.sh` | Delivers the queue as user shell (INTERFACES 12.1): notifications (tags `gb_dgpu`, `gb_dtx`, `gb_dgpu_test`, v1.2 `gb_gfx`), the GbGpu broadcast, force-stops, and (v1.2) the three whitelisted Settings.Global writes. |
| `native/gb_dgpu_check.sh` | `/vendor/bin/gb_dgpu_check.sh` | Read-only snapshot for the shell user (v1.2 lists the graphics properties). |
| `native/modules.nouveau` | (not installed) | Documentation of the module set. |
| `firmware_staged_sha256.txt` | `/odm/etc/gb/tu117.sha256` | sha256 of the 30 TU117 firmware files. Unchanged. |
| `payload/make_dgpu_payload.sh` | builder tool (Wave 2 W2-08) | Builds `dgpu-<krel>.erofs` + `.sha256` (INTERFACES 6.3). Unchanged in v1.2. |
| `payload/erofs_ls.py`, `payload/test_make_dgpu_payload.sh` | (not installed) | Image lister (python3 -I) and NP-21. |
| `firmware/fetch_tu117_firmware.sh`, `firmware/WHENCE.tu117`, `firmware/LICENCE.nvidia` | (not installed / payload) | P0-6, unchanged. |
| `test/test_gb_nouveau.sh`, `test/shims.sh`, `test/rc_lint.sh`, `test/run_tests.sh` | (not installed) | Test suite (wave 1 + v1.2). |
| `native/v1.1/`, `test/v1.1/` | (not installed) | Byte copies of the v1.1 files this version was built from (shas in the integration note). |
| `native/*.pre-dgpu`, `*.pre-merge` | (not installed) | Older versions, kept for reference. |

## Graphics modes (v1.2)

The user's setting is `persist.vendor.gb.gfx_mode` (INTERFACES 22.2). GbGpu's tile and Settings page change it
through gb_power; `adb shell setprop persist.vendor.gb.gfx_mode intel` also works. The v1.1 opt-in
`persist.vendor.gb.nouveau` is retired: nothing reads or writes it.

| Mode | At boot (`sys.boot_completed=1`) | When the user switches to it later (`vendor.gb.nouveau.booted=1`) |
|---|---|---|
| `auto` (default; also unset or any other value) | nouveau is loaded whenever the base with the dGPU is attached | `gb_nouveau.sh auto`: insmod, or the bind path when the module is still loaded |
| `nvidia` | the same as `auto` here (they differ only in the GUI texts) | the same |
| `intel` | nothing is loaded (`result=idle`, `mode=default`) | `gb_nouveau.sh off`: refcnt-gated unbind, then `rmmod` unless `foreign` |

`auto` and `nvidia` only decide whether the **driver** may run. Which **apps** render on NVIDIA, and when, is gb_power's
derivation (`vendor.gb.gfx_active`, INTERFACES 23) plus the Settings.Global keys that gb_notify writes for it
(INTERFACES 24). System UI, SurfaceFlinger and HWUI stay on Intel in every mode. The render node is always 0660 from
these scripts; only gb_power raises it to 0666, while its own derivation is `nvidia`.

`gb.nouveau=once` and `gb.nouveau=test` on the command line load nouveau whatever the mode; `gb.nouveau=0` (or any
unknown value, or `module_blacklist=...nouveau...`) is the kill switch for the boot.

## What happens on the device

**`auto`** (INTERFACES 7.2-v12, in this order; the review A2 order puts every "never load" check first):

1. Native check (`refused:hw`), then the wedged check (`refused:wedged`).
2. Kill switch: `result=disabled`, root-only `/dev/gb_dgpu/noload`, `booted` not set.
3. `noload` present: `refused:noload`. Before `sys.boot_completed=1`: `refused:boot` (the marker is not touched).
4. Crash-loop marker `/data/vendor/gb/nouveau.loading` (v1.2 line `mode=.. t=.. boot=<boot_id> argv=..`):
   * from an earlier boot, or without `boot=` (v1.1 format): create `noload`, remove the marker,
     `persist.vendor.gb.gfx_mode=intel`, `clean_boots=0`, `result=crashloop`, N01 ("Switched to Intel graphics");
     `booted` is not set;
   * of this boot (gb_power's rebind guard, INTERFACES 23.8): left alone; the ESP lock of step 8 serializes the windows.
5. Mode: `test`/`once` from the command line; else the graphics mode: `intel` gives `result=idle` and stops here;
   `auto`/`nvidia` give `mode=auto`.
6. nouveau already loaded:
   * the dGPU is bound to nouveau: re-apply the node permissions (0660 / 0600) and stop;
   * `/dev/gb_dgpu/keep` (this boot's driver was unhealthy): `refused:keep`;
   * the dGPU is present and driverless: the **bind path** below;
   * no dGPU (base detached): `refused:nodgpu`.
7. Probation (`mode=auto` and fewer than 3 clean boots: the dmesg-live stream runs, `probation n/3` is logged).
8. Guards (`i915`: renderD128 and card0 are 0x8086/i915; `nodgpu`; `bar`; `module`), the 15 s wait for a detach in
   progress (`refused:detach`), the ESP lock (120 s, `refused:esp`), `loading=1`, the `used` flag.
9. Payload stage (payload mode), whitelisted arguments, `kernel.panic=30` on every load, no ESP vfat mount
   (`refused:esp`), the marker written and synced. No `foreign` here any more (gb_power sets it before the first
   `gfx_active=nvidia`).
10. insmod of the helpers, the late checks (detach, dGPU, BAR; each removes the marker), insmod `nouveau.ko`. An insmod
    killed by a signal, or an oops after the mark, keeps the marker, creates `keep` + `noload` and then sets
    `gfx_mode=intel`.
11. Node permissions, the 30 s health check (bound, `suspended`, `D3cold`, no error or oops lines since the mark).
    Unhealthy: `keep` + `noload`, then `gfx_mode=intel`, `clean_boots=0`, marker removed, N02 ("Graphics are set to
    Intel only"), the driver stays loaded.
12. `loading=0`, lock released, `booted=1` (set only when it is not already 1, review E2). Test mode starts the test
    sequence.
13. Clean mark at +120 s: dmesg re-checked, marker removed, `clean_boots+1`, `result=clean`. If the dGPU is no longer
    bound by then (detached or switched to Intel meanwhile), the marker is removed and the boot is not counted.

**Bind path** (INTERFACES 7.2a; the module is loaded but the dGPU is driverless, e.g. after `off-unbound` in a
`foreign` boot, or after a failed or skipped kernel re-bind on reattach):

1. Wait up to 30 s while `init.svc.gb_nouveau_off` reads `running` (`refused:off`).
2. Guards `i915`, `nodgpu`, `bar` (bound to nouveau meanwhile: permissions only).
3. Detach wait (`refused:detach`), ESP lock (owner `gb_nouveau`, `refused:esp`), `loading=1`, `used`; in payload mode
   the mounted payload is verified (nothing is remounted when it is in place).
4. No ESP vfat mount; marker `mode=bind t=<s> boot=<id> argv=-` + sync; `kernel.panic=30`.
5. Late checks right before the write (detaching, dGPU present, driverless, BAR); each removes the marker.
6. `bind-mark` in the kernel log, the BDF written to `/sys/bus/pci/drivers/nouveau/bind` from a background subshell,
   40 polls of 0.5 s:
   * still writing after 20 s: `wedged` + `keep` + `noload`, `gfx_mode=intel`, the marker stays, `bind-failed`, N05;
     no further dGPU write this boot;
   * the write returned and the dGPU is not bound: `unhealthy:bind`.
7. Permissions, health check (since the bind mark), `result=healthy`, lock released.
8. Clean mark at +120 s: dmesg re-check, marker removed, `result=clean`; `clean_boots` is **not** incremented
   (probation counts boots, not binds; review C8). The record is `report/bind-<N>.txt` (same keys as `load-<N>.txt`,
   `mode=bind`), so the boot's insmod record is kept.

**`off`** (INTERFACES 7.3 with the v1.2 notes):

* `wedged`: `off-wedged`. `once`/`test` boots: `off-ignored`.
* The graphics mode is not `intel` (the user switched back before `off` ran): `off-skipped`, nothing touched.
* It waits for a running load window, then: not loaded gives `off-done` (`power/control=auto`); `keep` skips.
* It stops `gb_dgpu_test`, `gb_nvk_probe`, `gb_gpu_compute` and (v1.2) `gb_gfx_selftest`, sets the nouveau nodes to
  000, waits 1 s and reads `refcnt`. Right after that read the mode is checked again: not `intel` restores 0660 and
  gives `off-skipped`.
* `refcnt > 0`: permissions restored, `off-busy:<n>`, N03 ("Close <holders> to finish switching to Intel graphics").
* Otherwise the background unbind with a 20 s deadline (`off-wedged` + N05 on a hang); `foreign` keeps the module
  (`off-unbound`, `/dev/gb_dgpu/off`; v1.2: no N04), else `rmmod` of nouveau and the helpers this boot loaded, payload
  unmounted, `off-done`.

**`selftest`** (`gb_dgpu_report.sh selftest`, service `gb_gfx_selftest`, INTERFACES 25.3; gb_power starts it on the
first healthy binding of each identity and holds the dGPU awake):

1. Refused (exit 1, no file written) unless: native, not a `gb.nouveau=test` boot, the dGPU bound to nouveau,
   `/data/vendor/gb/selftest` a real root-owned 0700 directory, no detach under way, `/odm/etc/gb/gfx_build_id`
   readable.
2. `report/selftest-<N>.txt` (0640 root:shell) with the identity (`build=<ro.build.id>/<incremental>`, `krel`, the
   `gfx_build_id` line), the nouveau argv and result.
3. ESP lock (owner `gb_dgpu_report`, 120 s; busy: `verdict=error reason=esp`, attempts unchanged).
4. Crash marker `selftest/running` (`boot=<id> t=<epoch>`, noclobber, synced); `foreign` before any probe.
5. `timeout 150 nvk_probe -t ahb-i915-readback -T 60`, then (unless a detach started) `-t a17-swapchain-model`.
6. Verdict: exit 0 pass, 1 fail `<mode>`, 124/137 fail `<mode> timeout`, 2 error `icd`, 3 error `nodevice`, other fail
   `<mode> rc <n>`; both pass gives `pass` (`reason=ok`), any fail gives `fail` (the first failing run), else `error`.
7. The record `selftest/result` (0644; `verdict reason build krel gfx t attempts crashes report`) is written as
   `.tmp` (noclobber), synced and renamed; `attempts` counts per identity, `crashes` is carried over. The marker is
   removed and the lock released.
8. A TERM/INT/HUP (shutdown) kills the probe and writes `verdict=error reason=interrupted` (attempts unchanged); a
   detach between the probes does the same. A hard kill (`ctl.stop`) leaves the marker for gb_power (25.5).

**`gb_notify` Settings writes** (v1.2, INTERFACES 12.1): only `updatable_driver_all_apps` with `value=0|3`, and the
opt-in / opt-out lists from `/data/vendor/gb/gfx_optin` / `gfx_optout` (regular file, not a symlink, owned by uid 0,
<= 64 KiB, <= 512 / 128 lines; bad lines dropped and logged, duplicates removed, joined with `,` in file order; an
empty list deletes the key). Every other key, value, `src` or pairing is logged and dropped.

Each load writes `report/load-<N>.txt` (bind: `bind-<N>.txt`). `N` comes from `/data/vendor/gb/bootseq`.

**Recovery without a shell.** In the systemd-boot menu, press `e` and append `gb.nouveau=0`. Or simply boot again
after a freeze: the crash-loop guard sets the graphics mode to Intel. To try NVIDIA again, choose Auto in
Settings > System > Graphics.

**Root-only state.** `/data/vendor/gb` is 0751 root:shell and `report/` 0750, so a shell-uid process cannot create,
rename or delete the marker, the counters, the payload directory or (v1.2) `selftest/`. Notification files in the
group-shell writable `notify/` are created with noclobber and umask 022, then renamed.

## Build and test

There is nothing to compile. Wave 2 packs the scripts through `inject\native\components\nouveau.json`.

Tests run on the builder (Ubuntu 24.04, mksh R59, shellcheck 0.9.0; the Android-like run also needs `toybox` and
`original-awk` from the Ubuntu archive, which are installed). From Windows (Git Bash):

```sh
K="ssh -i C:/GB-build/builder_key -o StrictHostKeyChecking=no builder@192.168.115.132"
cd C:/GB-build/native/nouveau-kernel
tar cf - native test -C C:/GB-build/native/dgpu INTERFACES.md | $K 'mkdir -p /build/native/dgpu/nouveau-policy/src &&
  cd /build/native/dgpu/nouveau-policy/src && rm -rf native test && tar xf - && cd .. &&
  GB_INTERFACES=$PWD/src/INTERFACES.md sh src/test/run_tests.sh /build/native/dgpu/nouveau-policy/work'
```

`run_tests.sh` runs at nice 19 and in the idle I/O class. It does four things:

1. `shellcheck -s sh` on every `.sh` file (NP-01 = NP-1B-24).
2. The rc linter (NP-24).
3. The mksh suite with GNU coreutils.
4. The same suite again with toybox applets, BWK awk and busybox `tr` ahead of GNU in the scripts' PATH, which is
   close to the Android userland.

A single test: `GB_TEST_ONLY="NP-11 NP-1B-08" mksh test/test_gb_nouveau.sh /tmp/w`.

How the suite works:

* It builds a fake device tree under `GB_ROOT` for each case: sysfs PCI devices with drivers, DRM class nodes, procfs,
  `/dev`, `/data`, `/odm`, and a fake ESP and erofs image.
* PATH shims stand in for `getprop`, `setprop`, `insmod`, `rmmod`, `mount`, `umount`, `dmesg`, `sleep`, `sync`,
  `chown`, `chmod`, `stat` (v1.2: `-c %u` owner hook), `uname`, `cmd`, `am`, `log`, `getenforce`, `dumpsys`,
  `gb_power` and `nvk_probe` (v1.2: exit code per `-t` mode, a detach or a SIGTERM during a run).
* The clock is fake: `sleep` advances `/proc/uptime` and also plays the kernel's part. Unbind and (v1.2) nouveau
  bind writes take effect (`_kbind`, shared with the insmod shim), timed dmesg lines appear, timed property changes
  happen (`ctl/prop_at`), resumes are counted, and crashes are simulated with SIGKILL.
* A hung unbind or bind is a FIFO with no reader. A DRM-node open is detected because every `/dev/dri` node is a FIFO
  in the report tests. The setprop shim records which root-only flags exist at each `gfx_mode` write (ordering).

Coverage (wave 1; the v1.2 rewrites of `TASKS_1B.md` section 9 are marked "v1.2" in the suite):

| Test | Covers |
|---|---|
| NP-02 | Kill switches (the graphics mode is not changed). |
| NP-03 | Crash-loop marker (v1.1 format): `gfx_mode=intel`, N01 v1.2. |
| NP-04 | Probation over 4 auto boots. |
| NP-05 | Crash after insmod, then the next boot (marker names boot 1). |
| NP-06, NP-07 | i915 guard; dGPU absent, BAR zero. |
| NP-08, NP-09 | Argument injection and assembly. |
| NP-10 | ESP lock (live, stale, ownerless, re-created during a steal) and ESP vfat mounted. |
| NP-11 | Payload: copy, refresh, corrupt, mount, bind, krel, VERSION, ESP mount, absent, loop-backed, remount, legacy, ownership checks. |
| NP-12 | Health checks: bind, D3cold, dmesg (`gfx_mode=intel`, N02 v1.2), late dmesg, noise lines, ring scroll. |
| NP-13 | Node permissions; v1.2: 0660 in every case, no `foreign` from `auto`. |
| NP-14 | Off while busy (N03 v1.2; `gb_gfx_selftest` stopped too). |
| NP-15 | Hung unbind, then wedged. |
| NP-16 | Foreign flag (no N04 in v1.2), full off with payload unmount, shared helper, not loaded, waiting for the loader. |
| NP-17 | Off in once/test boots. |
| NP-18 | Auto when nouveau is already loaded and bound. |
| NP-19 | Panic policy matrix (auto probation, auto, intel), VM refusal, usage, insmod failures, marker cannot be written. |
| NP-DS | dmesg stream: rotation and cap. |
| NP-20 | gb_notify (notify, broadcast, force-stop). |
| NP-22, NP-23 | Read-only report; test refusal and a full test-mode sequence. |
| NP-24 | rc lint and a byte-exact comparison with INTERFACES 15.1(-v12). |
| NP-26, NP-28, NP-29 | noload / boot_completed (v1.2: refused:noload before the loaded branch); detach in progress; oops / killed insmod. |
| NP-21 | `payload/test_make_dgpu_payload.sh` (unchanged in v1.2, not re-run). |

v1.2 (`TASKS_1B.md` 1.2):

| Test | Covers |
|---|---|
| NP-1B-01, -02 | Default auto load (`gfx_mode` unset, `garbage`, `nvidia`, `AUTO`): probation, v1.2 marker with the boot id. |
| NP-1B-03, -04 | Intel mode: idle, then `off-done`; the retired opt-in property is neither read nor written (static check too). |
| NP-1B-05 | Crash-loop markers of another boot and in the v1.1 format; a marker of this boot waits for the ESP lock. |
| NP-1B-06 | `keep` and `noload` exist before the `gfx_mode=intel` write (unhealthy d3cold/dmesg, killed insmod, oops). |
| NP-1B-07, -23 | Hot attach, script side; `booted` set once. |
| NP-1B-08 | Bind path: marker, lock owner, panic, BDF write, healthy/clean, `clean_boots` unchanged, `bind-<N>.txt`; probe failure; payload mode. |
| NP-1B-09 | Bind that never returns (FIFO): `bind-failed` in 20 s, wedged/keep/noload, marker kept, N05, no further writes. |
| NP-1B-10, -11, -12 | Bind refusals (i915, BAR, ESP lock, ESP mount, detach, late detach); guard order (noload, keep, kill, boot, marker); waiting for `off`. |
| NP-1B-13 | Retired gfxdriver gate: 0660, no `foreign`. |
| NP-1B-14, -15 | `off-skipped` (mode auto; mode flipping during the chmod-000 second); foreign `off` without N04, the four GPU users stopped, then the bind path. |
| NP-1B-16 | rc: 7 services, lint clean, byte-identical to 15.1-v12. |
| NP-1B-17, -18 | gb_notify `kind=settings` (values, pairings, file checks, limits) and the `gb_gfx` tag. |
| NP-1B-19 .. -22 | Self-test: pass record and order of lock/marker/foreign, verdict mapping and attempts, refusals and ESP busy, TERM and detach interruptions. |
| NP-1B-VM | D8: the native-smoke image in VMware: `refused:i915`, nothing queued, self-test refused. |

Last results (2026-10-25, v1.2): 1028/1028 assertions with GNU coreutils and 1028/1028 with toybox + BWK awk
(640 wave-1 + 388 v1.2 per pass); shellcheck 0; rc lint 7 services, 0 findings; rc body byte-identical to
15.1-v12 (`test/last_run.log`; details in `integration/nouveau-policy.md` 7.6). NP-21 not re-run (payload tool
unchanged, 31/31 in v1.1).

## Hardware-only checks (not run; nothing here has touched the real GPU)

* **NP-HW, Phase 2A/2B:** the report on the SB3 (renderD128 is i915, all 10de functions D3cold, iommu group,
  base-port mapping, SELinux mode), 30 power samples, the `[NVIDIA test]` entry (insmod/bind time, `RM version`,
  D3cold while bound, MemAvailable, the 535 fallback, PM stress, suspend, reboot with nouveau bound), `kernel.panic=30`
  really rebooting after an oops (INFERRED).
* **v1.2:**
  * the auto load on every boot with the base (boot time and MemAvailable against an Intel-only boot, H-1B-01);
  * the bind path against the real kernel: an `off-unbound` (foreign) boot switched back to Auto, and a reattach whose
    kernel re-bind did not happen (H-1B-05, H-1B-07); the bind write's real return status;
  * the self-test probes on the TU117 (`ahb-i915-readback`, `a17-swapchain-model`), their timing against the 150 s /
    60 s limits, the trap on a real shutdown during the test, and the real exit codes (H-1B-01, H-1B-11);
  * `cmd settings put|delete global` as user shell from gb_notify, a 512-package list (H-1B-04);
  * notifications N01/N02/N03/N05 v1.2 and N16/N17 (tag `gb_gfx`) as shell.
* **Health thresholds:** the 30 s D3cold window and the dmesg patterns are untested against real nouveau output.
* **Phase 5 payload, on the device:** file-backed or loop mount, firmware through the bind mount, unmount on off,
  suspend with the payload mounted.

## Known limitations

* A reboot or shutdown within the 120 s clean-mark window after a load (or a bind) leaves the marker, so the next boot
  takes the crash-loop path and switches to Intel (N01). In v1.1 this needed an opt-in; with Auto it can happen on
  any boot with the base. Recorded as spec question Q-1B-1 in the integration note (INTERFACES 5.1 lists exactly when
  the marker is removed, so it is not changed here).
* A switch between Auto and NVIDIA while nouveau is bound re-runs `auto`, which re-applies 0660 to the render node;
  gb_power raises it to 0666 again at its next fast tick (<= 2 s). An app instance created in that window gets Intel.
* After an unhealthy load the driver stays loaded until restart (D20); `off` is skipped and `auto` refuses
  (`refused:noload`) for that boot.
* Any app can set `persist.vendor.gb.gfx_mode` (properties are untrusted); it only selects a mode the user could pick,
  every guard still applies.

## Other folders (unchanged)

`kernel/` (Kleaf nouveau fragment), `build/` (compile test and outputs), `firmware/` (TU117 fetch script and table),
`acpi/` (SB3 dGPU ACPI excerpt), `coordination/` (earlier gb_power opt-in patch).
