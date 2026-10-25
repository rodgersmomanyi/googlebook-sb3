# Wave 2 integration notes: gb-power (component 2)

Component directory: `C:\GB-build\native\power\`. Contract: `C:\GB-build\native\dgpu\INTERFACES.md` **v1.1**, sections 2, 4,
5.2, 8, 9, 10, 12, 15.2 and 18.2 (sections 1-6 of this file), and **v1.2** sections 22-26 with `1b\TASKS_1B.md` section 2
(section 7 of this file: the wave 1b graphics switching; **its artifacts replace the ones of section 2**; **section 8,
the v1.2 r2 review fix pass, replaces the artifacts of 7.2 and the results of 7.3**). Component
README: `C:\GB-build\native\power\README.md`.

## 1. What Wave 1 delivered

Everything below lives inside `C:\GB-build\native\power\`; no shared file was touched.

* **`gb_power.c`, plan Phase 3 deliverables 1-8:**
  * state model and `vendor.gb.dgpu.*` publication;
  * latch lifecycle;
  * slow-path detach: boot-disk guard, refcnt gate, holder naming from fd and maps, forked worker with a deadline
    and heartbeats;
  * staged rescan;
  * watchdog;
  * "never unbind nouveau on its own";
  * optional DMA-FQ domain (compiled out);
  * `--dtx-monitor`.
* **`gb_power.c`, beyond Phase 3:**
  * P1 item 7: non-VGA 10de functions are always driverless;
  * the P6 stop hook (`ctl.stop gb_gpu_compute`);
  * the P7 control socket server (`/dev/socket/gb_power`, 15 commands, roles, golden transcripts);
  * the notification queue writer (INTERFACES 12.1);
  * the `-S` test socket option.
* **Static asserts:** `SDTX_IOCTL_LATCH_LOCK == 0xa523`, `SDTX_IOCTL_LATCH_UNLOCK == 0xa524` and
  `SDTX_IOCTL_LATCH_REQUEST == 0xa525`, in addition to the existing ones.
* **`gb_power.rc`:** INTERFACES 15.2 (socket line; `mkdir /dev/gb_dgpu 0700 root root` before `start gb_power`).
* **Tests:**
  * `test/test_gb_power.c` (398 checks);
  * `test/test_socket.py` (94 checks against the real daemon loop);
  * `test/check_rc.py`.
* **`build.sh`:** the NDK build plus all tests, normal and ASan/UBSan.

### 1.1 v1.1 fix pass (INTERFACES 21: C1, C2, C5-C9, C11, C13)

* **Compute hold (C1):** the compute-svc connection is exempt from the 30 s idle close (watch connections already
  were); `loop_due` no longer schedules an idle deadline for it.
* **gfxdriver gate (C2):** render node 0666 and the status field `gfxdriver` need `vendor.gb.gfxdriver=on` AND
  `/dev/gb_dgpu/gfxdriver`.
* **enable/disable effects (C5):** `enable` answers `next-boot` while nouveau.ko is loaded but the dGPU is not bound to
  it, while `init.svc.gb_nouveau=running` and not bound, or with `/dev/gb_dgpu/noload`; `disable` answers `next-boot`
  with `/dev/gb_dgpu/keep`.
* **Rebind after off (C6):** nouveau bound + `/dev/gb_dgpu/off` + persist not 1 -> `ctl.start gb_nouveau_off`, once per
  bind.
* **Detach (C7, C8):** `/dev/gb_dgpu/detaching` from the request until `ec_ready()`; refusal with N15 while the ESP
  lock owner is a live `gb_nouveau` (also re-checked in the quick path's last look, during the refcnt wait, between the
  jobs and before the confirm); LATCH_LOCK at decision start; `ctl.stop` of `gb_dgpu_test`, `gb_nvk_probe`,
  `gb_gpu_compute` and waiting for them; nodes that appear during the decision are chmod 000; worker job 1 (unbind)
  and job 2 (remove) with a main-loop re-check between them (late re-bind: job 1 repeated at most 3 times; refcnt,
  services, load window); the child checks the gate right before every unbind/remove write (`op_gate`, -EBUSY);
  before LATCH_CONFIRM: no function left, refcnt gate 0, no load window, boot-disk guard again.
* **Releasing (C9):** only EC confirmed or latch open; a nouveau bind then is unbound only through the gate, with
  refcnt > 0 it stays bound and the latch is locked.
* **Files (C11):** `compute_allow.tmp` and the notification `.tmp` are created `O_CREAT|O_EXCL|O_NOFOLLOW` (+ fchmod),
  never followed or truncated; flag files `O_NOFOLLOW`.
* **Lows taken (C13):** an unreadable refcnt while nouveau is loaded counts as busy (`refcnt_busy()`); the latch is
  locked while a base drive is mounted (tick) and SIGTERM then keeps it locked; `d3cold_allowed` only for driverless
  functions; the confirm watchdog queries the EC afresh and sends LATCH_CANCEL before the cancel rescan; every
  `module_blacklist=` occurrence counts.
* **Tests:** 474 unit checks (76 new: `test_load_window`, `test_gate_split`, `test_release_window`, `test_off_rebind`,
  `test_bootdisk_latch`, plus new checks in the existing groups), unchanged `test_socket.py` (94).

## 2. Artifacts (sha256)

| Artifact | sha256 | Consumer |
|---|---|---|
| `C:\GB-build\native\power\bin\gb_power` (NDK clang, static bionic, x86_64, stripped, 611128 bytes; v1.1) | `05d9b2fc8ef0c9501dff53d4ec3ce34b328fb8e4e0082745d45eff9b8ece63ef` | `power.json` -> `vup/bin/gb_power` |
| `C:\GB-build\native\power\gb_power.rc` (unchanged in v1.1) | `395aad4dbe3d38859cbe5b8bc87d461d03af4b4f3ccf15d6d499ac06a91829a6` | `power.json` `rc_files` |
| `C:\GB-build\native\power\gb_power.c` (source of the binary above) | `25bddd1f349e59680d97cf430f40767d595960edabf468c7f7c3aa87d4cf4415` | |
| `C:\GB-build\native\power\test\test_gb_power.c` | `033307d33844c16a4dec95f2a7089c3bc6ae671d2ef5eeb2707863f2bf42cf1f` | tests only |
| `C:\GB-build\native\power\build.sh` (unchanged) | `27800d46c11c8c872bcf725db9fcfc80a7480b876c27b303bf9a82595924272a` | |

The binary is reproducible (two clean builds of the same source give the same sha256; v1.1 re-check in section 5).
The Wave 1 v1 binary was `ab67f2d5...e7ae` (source `24db4aa7...2f2b`).

The previous binary is kept as `bin\gb_power.pre-dgpu` (`9c126bbc320dc3fc89127cae44f815252e4047cb2fd9a4ac98b573f0bda98df7`)
for rollback, and the previous sources as `*.pre-dgpu`. None of the `*.pre-*` or `*.merged` files may be packed.
`power.json` lists only `bin\gb_power` and `gb_power.rc`.

## 3. Exact changes Wave 2 must make to shared files

### 3.1 `C:\GB-x86\inject\native\components\power.json` (W2-01): no change

The current content already points at `C:\GB-build\native\power\bin\gb_power` (`vup/bin/gb_power`, `0o100755`) and
`rc_files: [C:\GB-build\native\power\gb_power.rc]`, so the new files ship as they are.

Verify after `build_esp.py`:
1. The native `etc/init/gb.rc` contains, inside `# ---- component power ----`:
   * `socket gb_power stream 0666 root root` under `service gb_power`;
   * `mkdir /dev/gb_dgpu 0700 root root` directly before `start gb_power` in `on early-init`.
2. The unpacked `vup/bin/gb_power` has the sha256 above.
3. The VM cpio still contains no `gb_power` (it never did: component manifests are native-only).

The `/dev/gb_dgpu` mkdir also appears in `gb_nouveau.rc` (component 1); the two lines are identical by design.

### 3.2 `C:\GB-x86\verify_image.py` (W2-07): two optional extra checks

Add these to the W2-07 list. Both are read-only cpio checks:
* `gbnative/vup/bin/gb_power` sha256 == the value in section 2 (or the value in this file's latest revision);
* `gbnative/etc/init/gb.rc` contains `socket gb_power stream 0666 root root` and `mkdir /dev/gb_dgpu 0700 root root`.

### 3.3 `C:\GB-x86\inject\native\components\nvk.json` (W2-01/W2-11): stale comment only

The current `rc_text` says: "Prerequisite: persist.vendor.gb.nouveau=1 (gb_power leaves the dGPU on nouveau)".

With this gb_power, nouveau is left bound whatever the property says (INTERFACES 10.1). W2-01 replaces that
`rc_text` with component 4's `gb_nvk.rc`, so nothing extra is needed. If the `rc_text` is kept for any reason, drop
that sentence.

### 3.4 Repository docs (W2-14): `C:\GB-repo\googlebook-sb3\native\power\README.md`

Replace the **Detach (DTX)** and **dGPU** bullets (current lines 9-16) with:

```
* **Detach (DTX)**: driverless and nouveau never used this boot: the request is confirmed at once (as before).
  Otherwise a checked slow path: boot-disk guard (a mounted USB/SD drive in the base blocks the detach), "Preparing to
  detach" notification, the compute service stopped, nouveau's DRM nodes closed to new opens, a 30 s wait for
  /sys/module/nouveau/refcnt to reach 0 (the notification names the apps that still use the GPU), then a background
  worker unbinds and PCI-removes every NVIDIA function (20 s deadline; on a miss the latch stays locked and a restart is
  required), and only then LATCH_CONFIRM. The latch is locked whenever a driver is bound to an NVIDIA function (the EC
  then cancels instead of opening on timeout), re-locked after resume, unlocked at shutdown. After nouveau was used,
  re-attaching (or cancelling) rescans the root port with backoff. Handles abort/cancel/latch events like
  linux-surface's surface-dtx-daemon, publishes vendor.gb.dtx.{mode,base,latch}, inhibits the base keyboard/touchpad
  in studio mode. Patch 0003 fixes a mainline surface_dtx latch event bug. `gb_power --dtx-monitor` prints DTX events
  read-only (EC timeout measurement).
* **dGPU**: default (no nouveau): every NVIDIA function driverless with runtime PM auto, so the root port drops to
  D3cold. nouveau (loaded by gb_nouveau.sh) is never unbound by gb_power except by the detach worker; the HDA function
  (02:00.1) is always kept driverless. Live state in vendor.gb.dgpu.state (off / standby / busy:N / loading /
  detaching / unavailable / disabled / crashloop / wedged / fault), watchdog for runtime_status=error and idle D0.
* **Control socket** /dev/socket/gb_power: one text line in, one JSON line out, SO_PEERCRED roles (root, shell, the
  GbGpu app, the compute service, allowlisted compute clients); see native/dgpu/INTERFACES.md section 9.
```

In the **Files** paragraph:
* replace "`test/test_gb_power.c` (fake-sysfs unit tests, `-r <root>`, including the nouveau opt-in and detach
  races)" with "`test/test_gb_power.c` (fake sysfs/procfs unit tests with a mock EC and a fake clock),
  `test/test_socket.py` (protocol test against the host build), `test/check_rc.py`";
* append: "build.sh also runs the tests under ASan/UBSan".

Keep "Status: untested on hardware; unit-tested."

### 3.5 SELinux (W2-15): nothing to add while the domains stay permissive

* gb_power and every client run in permissive domains, as listed in `C:\GB-x86\inject\odm_sepolicy.cil`:
  * gb_power, gb_gpu_compute, gb_notify and gb_nouveau run in `u:r:shell:s0` (`typepermissive shell`);
  * GbGpu runs as `untrusted_app` (`typepermissive untrusted_app`);
  * `typepermissive socket_device` is present.
* The init-created socket gets the default `/dev/socket` label (`socket_device`).
* If `untrusted_app` is ever made enforcing, GbGpu needs both:
  * `allow untrusted_app shell:unix_stream_socket connectto;`
  * `allow untrusted_app socket_device:sock_file write;`

  A dedicated type for `/dev/socket/gb_power` would be cleaner then.
* Setting `ctl.start`/`ctl.stop`/`persist.vendor.gb.*` from the shell domain relies on `shell` being permissive.

### 3.6 Wave 2 test GP-24 (W2-16, **VM**, needs the user's approval)

There is no VM gb_power: `power.json` is native-only. So GP-24 runs on the VMware native-smoke path (`GB_NATIVE_TEST`
/ `gb-ntest`), where gb_power starts from the native gb.rc and finds no `/dev/surface/dtx`. Steps:
1. Boot `gb-ntest`, then, read-only over adb:
   `adb shell getprop vendor.gb.power.api` -> `1`; `adb shell getprop vendor.gb.dgpu.state` -> `unavailable`;
   `adb shell getprop vendor.gb.dgpu.latch` -> `unknown`.
2. `adb shell 'printf "hello\n" | toybox nc -q 1 -U /dev/socket/gb_power'`. The VM's toybox 0.8.13 lists `-U` (use
   a UNIX domain socket) and `-q` (quit after EOF on stdin), checked read-only with `toybox nc --help` on
   192.168.115.133.
   Expect `{"v":1,"ok":true,"cmd":"hello","api":1,"role":"shell","uid":2000,"server":"gb_power 1"}`.
3. `... "status\n" ...` -> `"state":"unavailable","dtx":"n/a","base":"unknown","refcnt":-1`.
4. `dmesg | grep gb_power` shows the usual battery lines and no `worker` lines.

## 4. Contract notes for the other components (no shared files)

* gb_power **reads**:
  * `vendor.gb.nouveau.{mode,result,booted,used,foreign,clean}`;
  * `vendor.gb.dgpu.payload`, `vendor.gb.gfxdriver`, `persist.vendor.gb.nouveau`;
  * `init.svc.gb_gpu_compute`, `init.svc.gb_nvk_probe`, `init.svc.gb_dgpu_test`, `init.svc.gb_nouveau`,
    `sys.boot_completed`, `sys.powerctl`;
  * `/dev/gb_dgpu/{used,foreign,wedged,gfxdriver,off,keep,noload}`, `/dev/gb_esp.lock/owner` (load window),
    `/data/system/packages.list`, `/odm/etc/gb_hw`;
  * `/odm/lib/modules/nouveau.ko`, `/odm/etc/gb/dgpu_payload.sha256`, `/odm/app/GbGpu/GbGpu.apk`.

  Unknown property values are treated as the default: an invalid `mode` counts as unset, and `result` is accepted
  only as `[a-z0-9:._-]`.
* gb_power **writes**:
  * the published properties `vendor.gb.dgpu.{state,holders,latch}`, `vendor.gb.compute.state`,
    `vendor.gb.power.api`, `vendor.gb.nouveau.used=1`;
  * on commands: `persist.vendor.gb.nouveau` (enable/disable), `vendor.gb.dgpu_report=1` (report) and
    `ctl.start` for `gb_gpu_compute`, `gb_nouveau` (hot attach, 7.9) and `gb_nouveau_off` (rebind after off, 10.1);
    `ctl.stop` for `gb_gpu_compute`, `gb_nvk_probe` and `gb_dgpu_test`;
  * the `vendor.gb.notify` trigger;
  * files: `/dev/gb_dgpu/{used,wedged}`, `/dev/gb_dgpu/detaching` (created and removed, not monotonic),
    `/data/vendor/gb/notify/*.msg` (0644, O_EXCL temp) and `/data/vendor/gb/compute_allow` (0644, atomic, O_EXCL temp).
* gb_power never writes `vendor.gb.nouveau.result`, never unbinds nouveau outside the detach worker, and never
  touches i915 nodes.
* **Component 1 (nouveau-policy):**
  * `gb_nouveau.sh off` keeps its own chmod 000 window. gb_power re-applies node permissions only to node inodes it
    has not handled yet (a rebind or ueventd re-creation), never to a node it already did.
  * When gb_nouveau.sh's `off` unbinds, gb_power sees a driverless dGPU and UNLOCKs the latch on the next tick
    (2 s) or the unbind uevent.
* **Component 1 (v1.1):** gb_nouveau.sh refuses insmod while `/dev/gb_dgpu/detaching` exists; gb_power refuses a
  detach while gb_nouveau.sh's load window is open. `off` creates `/dev/gb_dgpu/off` when the module stays loaded.
* **Component 4 (v1.1):** `gb_nvk_probe` runs as shell, so the chmod 000 of a decision keeps it out; gb_power also
  stops it.
* **Component 5 (gpu-compute):** send `compute-state <s>` on one long-lived connection. The power hold
  (`power/control=on`) is written by gb_power's worker and released on `idle`/`unloading` or when that connection
  closes. v1.1: that connection is never idle-closed; the service's 15 s keep-alive re-sends are harmless. gb_power
  stops the service (`ctl.stop`) before any slow-path detach and on `stop-compute`.
* **Component 6 (gpu-ui):**
  * the golden transcripts are byte-identical to INTERFACES 9.6;
  * replies keep the key order shown in 9.5;
  * `watch` sends a full status line on every change except `t_ms`, plus a ping every 30 s;
  * the state broadcast (`kind=broadcast`) is queued at most once per 2 s, only after boot and only when
    `/odm/app/GbGpu/GbGpu.apk` exists.

## 5. Tests run (all on builder 192.168.115.132, 2026-10-25, scratch `/build/native/dgpu/gb-power/`)

| Id | Result | Evidence |
|---|---|---|
| GP-01 | PASS | v1.1 build.sh step 1: static, stripped, sha256 `05d9b2fc8ef0c9501dff53d4ec3ce34b328fb8e4e0082745d45eff9b8ece63ef` (log `test\build_last_run.log`, 2026-10-25 10:19 UTC); reproducibility: a second clean NDK build in another directory gave `05d9b2fc...` again, `REPRO PASS` |
| GP-02 | PASS | v1.1 step 3 `474 checks, 0 failures`; step 4 (ASan+UBSan, no recover, leaks) `474 checks, 0 failures`, `sanitizers: no reports` |
| GP-25 .. GP-32 (v1.1) | PASS | in the 474: compute-svc quiet 60 s keeps hold:1 + power/control on while a root client is idle-closed; load window (live owner -> CANCEL + N15 at once, other owner / dead pid -> quick path, window during the wait -> N15 after 30 s, no unbind); detaching flag lifecycle incl. a stale flag at start; refcnt rising between the jobs -> refused before any remove; 10 `op_gate` cases; a gated job result -> cancel + 0660/0600 restored; waits while `gb_nvk_probe` runs, three `ctl.stop`, LOCK at decision start, a re-created node -> 0000, then UNBIND < REMOVE < CONFIRM; unreadable refcnt -> refused; echo wait after a busy refusal -> nouveau never unbound; late bind refcnt 1 on an open latch -> left bound + LOCK; rebind after off (start once per bind; not with persist 1 or without the flag); base drive -> LOCK, SIGTERM keeps it, plugged during a decision -> CANCEL + N10 after the remove; next-boot effects; gfx gate 0660/0666/0660; compute_allow.tmp symlink not followed; d3cold_allowed untouched while bound; `module_blacklist=nouveau module_blacklist=a` -> disabled; confirm watchdog CANCEL before the rescan; a press during the remove job -> cancel after it + rescan; quick-path LATCH_CONFIRM ioctl error -> not confirmed, detaching flag kept for the 10 s echo wait, then ready and the flag removed |
| clang -Wall -Wextra -Werror | PASS | step 2 `clang: clean` (daemon and tests, NDK clang host target) |
| GP-03 ... GP-23 | PASS | unit tests (map in `C:\GB-build\native\power\README.md`), plus `test_socket.py` `94 checks, 0 failures` |
| rc lint | PASS | `rc check: 2 services, 2 start/stop lines, 0 problems` (the old rc fails it: no socket line, no mkdir) |
| NDK binary smoke | PASS | the bionic static binary ran on the builder: `-o` exit 0; `-r -S -D` socket `hello` -> `"role":"other","uid":1000` (the test uid override is absent from the Android build); SIGTERM -> `exiting` |
| GP-24 | NOT RUN | Wave 2, VM (section 3.6) |
| GP-HW | NOT RUN | hardware only (README "Hardware-only checks") |

## 6. Spec questions and deviations (please resolve before Wave 2 edits)

1. **Fault blocks detach only for `rpm-error` and `d0-idle`.** N06 says "Detaching is blocked until restart", but
   10.3 does not list a fault step. I block the detach for the two faults where the GPU is bound but sick (an unbind
   would likely hang). I do not block it for `rescan` and `bar`: there is no usable GPU and blocking would force a
   restart for nothing. The latch is still never unlocked while a fault is latched.
2. **The rescan also runs when gb_power removed the functions** although nouveau was never used (e.g. HDA bound to
   `snd_hda_intel` gives the slow path and a remove). 10.4 says "when used". This is a superset: without it, a
   cancelled detach would leave the dGPU removed until reboot.
3. **New recovery timers (not in the spec), both 10 s:**
   * after one of our slow-path `LATCH_CANCEL`s, the EC's REQUEST echo is awaited at most 10 s, so a lost echo
     cannot swallow the next press;
   * after a slow-path `LATCH_CONFIRM`, if the latch has not opened within 10 s the detach is treated as cancelled,
     which triggers the cancel rescan.

   The quick path is unchanged (neither timer applies).
4. **Heartbeat period is 1 s** (spec <= 2.0 s). The decision steps every 0.5 s, and a heartbeat goes out once
   >= 1 s has passed. The tests assert a max gap <= 2 s over a 30 s refcnt wait and over a 20 s hung worker.
5. **Holder scan cost:** while busy, gb_power rescans holders (fd + maps) when refcnt changes or every 10 s; between
   scans it reuses the last result. A full maps scan on every 2 s tick would read tens of MB of `/proc/*/maps` per
   tick on Android. Fresh scans (rate limit 1/s) still run for `holders`, `close-holders` and the detach refusal.
6. **Cmdline kill switch in the state:** `gb.nouveau=0`, an unknown value, or `nouveau` in `module_blacklist`
   already gives state `disabled` before gb_nouveau.sh has set `vendor.gb.nouveau.mode`. INTERFACES 3 lists
   "gb_power (status)" as a consumer.
7. **Non-detach wedge notification:** if a worker job outside a detach (unbind of a foreign driver, rescan, compute
   hold) misses its deadline, gb_power posts N05 ("Restart required"). The 12.2 catalog lists only gb_nouveau.sh as
   N05's poster.
8. **Nodes stay 000 after a wedge** during a detach, so nothing can open a GPU that did not shut down.
9. **`-K` semantics:**
   * the default keep driver is `nouveau`;
   * `-K ''` unbinds every driver;
   * `-K <other>` would unbind a nouveau-bound VGA function.

   These are test overrides. Non-VGA functions are driverless regardless of `-K`.
10. **`close-holders` reasons:**
    * `self` = the requesting peer's own pid, or package `gb.sb3.gpu`;
    * `system` = appId < 10000 or the compute service;
    * `unknown-pkg` = appId shared by several packages, or not in packages.list.
11. **`enable` in a crash-loop boot** returns `next-boot` (`vendor.gb.nouveau.booted` is never set in that boot
    anyway).
12. **Connection starvation:** any local app can occupy all 8 connection slots (each closed after 30 s idle, then
    reopened). The spec's "9th gets EBUSY" was implemented as written. Suggestion for v2: reserve 2 slots for
    root/shell/ui, or evict the oldest `other` connection.
13. **DMA-FQ:** implemented as a compile-time flag (`-DGB_POWER_IOMMU_DMAFQ=1`; a static variable the tests flip; no
    property). It switches a pure-NVIDIA group to `DMA-FQ` while driverless. "Revert after unbind" is not
    implemented, because the group disappears with the PCI remove.
14. **EC timeout:** unknown. The test model uses 5 s; Phase 3 HW check 2 measures it with `--dtx-monitor`. Only the
    behaviour (cancel when locked, open when unlocked) is asserted.
15. **Fallback socket:** if init did not pass `ANDROID_SOCKET_gb_power` (e.g. a manual start), gb_power creates
    `/dev/socket/gb_power` itself (unlink + bind + chmod 0666).

### 6.1 v1.1 deviations (GP-D16 ..)

16. **Two worker jobs, each with its own 20 s deadline** (v1: one job, 20 s for unbind + remove). The EC sees
    heartbeats throughout; a missed deadline of either job is a wedge.
17. **A refcnt that rises after the unbind (or a gated write) refuses with N08 and cancels; no new fault reason**
    (review R2-1 suggested "set fault" for a refcnt > 0 right before the confirm). The functions are present again
    after the cancel rescan, so the normal busy handling applies; a fault would block detach until restart for a
    transient holder.
18. **No LATCH_CANCEL for a late nouveau bind on an open latch** (review R2-3 suggested it). dtx.rst: after
    LATCH_CONFIRM the detachment process has concluded, and LATCH_CANCEL "cancels detachment in progress (if any)",
    so it would do nothing. gb_power locks the latch (for the next process) and unbinds only through the gate.
19. **The holder scan stays in the main loop** (review R2 low "forked scanner" deferred to v2): it runs at most every
    10 s while busy, plus explicit requests (rate limit 1/s).
20. **The bootdisk lock check runs only while DTX is open** (no DTX = nothing to lock).
21. **After a refused request the detaching flag stays until the EC's echo (<= 10 s)**; gb_nouveau.sh waits up to 15 s
    for it, so a load requested in that moment is delayed, not lost.
22. **The worker's op lists are still built in the parent** (from an inventory taken right before the submit), not
    computed in the child (review R2-1 suggested the child). Instead the child re-checks each op's precondition at
    write time (`op_gate`: nouveau refcnt 0 before a nouveau unbind; driverless and nouveau refcnt 0 before a remove),
    and the main loop re-inventories between the jobs (a function bound after job 1's submit repeats job 1). A
    function that appears after job 2's submit is not removed by it; the final inventory before LATCH_CONFIRM then
    cancels the detach ("NVIDIA functions still present").
23. **Quick path LATCH_CONFIRM failure** (ioctl error): the 10 s echo wait now also applies, so the EC state and the
    detaching flag recover without a second press.

## 7. v1.2 (wave 1b): graphics switching

Contract: INTERFACES.md v1.2 (sections 22-26, plus the "(v1.2)" edits in 2.3, 2.4, 4, 5, 7.5, 8.2, 9, 10, 12) and
`1b\TASKS_1B.md` v1.2 section 2 (tasks T2.1-T2.16, acceptance GP-1B-01..25). Baseline: `gb_power.c@25bddd1f`,
`bin\gb_power@05d9b2fc`, `gb_power.rc@395aad4d`, `test_gb_power.c@033307d3`, `test_socket.py@3e658ef1` (kept as
`*.v1.1` next to the new files). Builder scratch: `/build/native/dgpu/gb-power/v12/` (v1.1's `.../gb-power/src` left
alone). Date 2026-10-25.

### 7.1 What changed (by TASKS_1B task)

* **T2.1 state.** `g_gfx` (cap, mode, policy, active, seq, n16, forced, identity, detach_ref, ui_notify), `g_sess`
  (session marker on disk / job / earlier boot), `g_st` (self-test: running, hold want, retried, clean_seen, the
  validated record), `g_rb` (rebind guard: phase, lock, marker), `g_pend` (pending detach), `g_nv` (hot attach),
  `g_off` (reconcile_off); `prop_put()` table 24 -> 32 (`NPUB`).
* **T2.2** `read_cmdline()` also sets `g_cmd_test` / `g_cmd_once` (the last `gb.nouveau=`).
* **T2.3** `gfx_mode_get()`, `gfx_policy_get()`, `gfx_inputs()` (cap = native + `stack_present()` +
  `ro.gfx.driver.0=gb.sb3.gfxdriver` + the APK + a one-line `gfx_build_id`), identity (`uname()`; `-r` test trees read
  `<root>/proc/sys/kernel/osrelease`).
* **T2.4** `gfx_derive()` = the 16 rows of 23.2; `gfx_eval()` at the end of `status_tick()` (before
  `publish_state()`) and after every input-changing command; `gfx_force_intel()` from `decision_start()`, `set-mode
  intel`, the rebind failure and `set_fault()`. Ordering exactly 23.3 (foreign, `OP_MARK` result, node 0666,
  setprop, N16, broadcast / setprop first, node 0660, `OP_UNMARK`, `gfx_unclean=0`).
* **T2.5** `node_perms()` takes the mode from `g_gfx.active`; `nodes_apply_new()` -> `nodes_enforce()` (23.5);
  `gfxdriver_on()` removed.
* **T2.6** worker ops `OP_MARK`/`OP_UNMARK` (file + directory `fsync` in the child), job `J_MARK` with a purpose tag,
  `op_gate()` returns 0 for them; a failed `OP_MARK` cancels the rest of its job.
* **T2.7** queue 16 entries, keys `set:all_apps|set:opt_in|set:opt_out`; `prefs_load()/prefs_save()`,
  `catalog_load()`, `gfx_apps_eval()` (24.4), `gfx_settings_eval()` (23.4); `pkgs_refresh()` keeps a sorted index,
  bumps a generation (apps re-evaluated, `apps_sync` recomputed) and queues the broadcast.
* **T2.8** `reconcile_off()` replaces `off_rebind_check()`; `hotattach_check()` rewritten (23.7); `mode_valid()`
  accepts `auto`.
* **T2.9** rebind guard (23.8): armed in `dtx_set_base()` (detached -> attached), `uev_handle()` (10de add, class
  03xxxx) and `rescan_submit(J_RESCAN)` (marker = first op); non-blocking ESP lock helpers with the 5.4 atomic
  steal; `load_window_open()` = gb_nouveau's window OR the watch; `set_fault()` learns `rebind`/`selftest-hang` and
  posts N06 / N20 / nothing per 8.2.
* **T2.10** detach: `gfx_force_intel("detaching")` before `latch_set()`/`nodes_lock()`; `g_gpu_users[]` +
  `gb_gfx_selftest`; the 5 s early refusal in `decision_step()`; `decision_busy()`: `detach_ref`, N08 rule; N17 and
  the attach event in `dtx_set_base()`; `pending_detach_step()` in `run_due()`.
* **T2.11** self-test (25): `selftest_check()`, the second hold want bit (`hold_wanted()`; `dgpu_pm_pass()` and
  `watchdog()` honour it), record validation and mirror, interrupted / crash / hang verdicts, deadline, N18.
* **T2.12** session marker (23.10): start check, `powerctl_check()` removal.
* **T2.13** commands `get-mode`, `set-mode`, `set-app-policy`, `set-app-intel`, `set-app-nvidia`, `get-apps`,
  `apps-begin/add/commit` (staging per connection), `run-selftest`, `list-holders`, `close-holders-and-detach`
  (shared parser `pid_list_parse()` + `close_holders_do()`), `ui-caps`; `enable`/`disable` = `set-mode`.
* **T2.14** status object: the 12 fields after `gfxdriver`, `persist` = mode not intel, `probation` for `auto`,
  `gfxdriver` = cap, holder reserve 1200; `publish_state()` broadcast triggers of 10.7 (v1.2).
* **T2.15** tests: `test_gb_power.c` (harness + rewritten wave-1 assertions), new `test/test_gfx.c`
  (GP-1B-01..23), `test_socket.py` (26.3 goldens over a real socket, v1.2 fields, VMware model), `check_rc.py`.
* **T2.16** README and this section.
* `gb_power.rc`: comment only (15.2 says unchanged); `build.sh` unchanged.

### 7.2 Artifacts (sha256; these replace section 2 for Wave 2)

| Artifact | sha256 | Consumer |
|---|---|---|
| `C:\GB-build\native\power\bin\gb_power` (NDK clang, static bionic, x86_64, stripped, 759768 bytes) | `9b99252590118263fc4b21f460048d6f95d295917ae964d2e41ac6da927fd8ba` | `power.json` -> `vup/bin/gb_power` |
| `C:\GB-build\native\power\gb_power.rc` (v1.2 comment lines only) | `87347a9918d540fc42b7d33193735e139db0120dced312543619ecd45cd8769f` | `power.json` `rc_files` |
| `C:\GB-build\native\power\gb_power.c` (source of the binary) | `c1986aad5e8340b3e816026d4ce015c645b6fceba2cd9c37e09faaec2ac79496` | |
| `C:\GB-build\native\power\test\test_gb_power.c` | `ae25a5b8a1229f6b85f49d7aa7164606520d95c85b4f6c48ba8a49fe5055da9b` | tests only |
| `C:\GB-build\native\power\test\test_gfx.c` (new) | `311f542e8cc56b2894395f522c490efff7c181816ed6832664dfd20de35aa864` | tests only |
| `C:\GB-build\native\power\test\test_socket.py` | `7ee95ea435503af375af22b8c86f1dda5cfad023800b2f0b9509c5b329396725` | tests only |
| `C:\GB-build\native\power\test\check_rc.py` | `7cf6baa465248bfad542fbc0d3df4352ddd9f26aee027885e5e60aca1dfd23b4` | tests only |
| `C:\GB-build\native\power\build.sh` (unchanged) | `27800d46c11c8c872bcf725db9fcfc80a7480b876c27b303bf9a82595924272a` | |
| `C:\GB-build\native\power\test\build_last_run.log` (the run below) | `7e01eb56da1d13c7b23d8acabbf6f78047cc775c27f3a654d344cbe16039d3e6` | evidence |

Rollback: `bin\gb_power.v1.1` (`05d9b2fc...`), `gb_power.c.v1.1` (`25bddd1f...`), `gb_power.rc.v1.1` (`395aad4d...`).
None of the `*.v1.1`, `*.pre-*` or `*.merged` files may be packed.

### 7.3 Tests run (builder 192.168.115.132, 2026-10-25 12:45-12:48 UTC, `test\build_last_run.log`)

| Id | Result | Evidence |
|---|---|---|
| GP-1B-01 | PASS | `GP-1B-01 matrix: 7560 combinations (mode 5 x base 3 x dGPU 6 x record 7 x special 12), 16 nvidia, 0 mismatches` (plain and ASan) |
| GP-1B-02 .. GP-1B-23 | PASS | one line per group in build.sh steps 3 and 4, e.g. `GP-1B-11 test_gfx_rebind: 29 checks, 0 failures`, `GP-1B-15 test_gfx_selftest: 34 checks, 0 failures`, `GP-1B-20 test_gfx_socket: 58 checks, 0 failures`; total `v1.2 groups: 396 checks, 0 failures`; 46 of 46 group lines (plain + ASan) end in `0 failures` |
| GP-1B-20 (real socket) | PASS | `test_socket.py`: `146 checks, 0 failures` (v1.1: 94): the 8 lines of 26.3 byte-exact over AF_UNIX against the host daemon (get-mode after the worker wrote `gfx_session`; render node 0666 then 0660; `gfx_seq` 2), the 9.6 goldens, 28 commands as role `other` -> EPERM, v1.2 status field order |
| GP-1B-23 | PASS | unit (`test_gfx_vmware`: 20 fake minutes, no `kind=settings`) and `test_socket.py` (`-D` daemon: `get-mode` intel/`nodtx`, base `unknown`, no settings file) |
| GP-1B-24 | PASS | plain and ASan+UBSan `874 checks, 0 failures`, `sanitizers: no reports`; `clang: clean` (host) and the NDK build with `-Wall -Wextra -Werror`; a second clean NDK build in another directory: `src 9b992525...` = `r2 9b992525...`, `REPRO PASS` |
| GP-1B-25 | PASS | `wave-1 groups: 478 checks, 0 failures` (v1.1 had 474; +4 from the rewritten assertions of 7.4) |
| rc lint | PASS | `rc check: 2 services, 2 start/stop lines, 0 problems` (v1.2 rule: no `persist.vendor.gb.nouveau`, `gfx_mode`, `gb_gfx_selftest` or `gb_nouveau` in this rc) |
| NDK binary smoke | PASS | the bionic static binary on the builder: `-o` exit 0; `-r -S -D`: `hello` -> `"role":"other","uid":1000`, `get-mode` -> `EPERM`; SIGTERM -> `exiting`; `strings bin/gb_power \| grep -c GB_POWER_TEST` -> 0 |
| GP-24 (Wave 2), VM-1B-04 | NOT RUN | VM, needs the user's approval (section 3.6; v1.2 adds: `get-mode` reason `nodtx`, no settings message) |
| H-1B-* | NOT RUN | hardware only (7.9) |

Builder etiquette: every heavy step ran at nice 19 / ionice idle; free space stayed above 20 GB on `/` and 12 GB on
`/build`. One intermediate run (12:21 UTC) started at a 1-min load of 2.51 while another workflow's `/var/tmp/gbiso` job had
just started (left alone); the final run above waited until no such job ran (load 0.89, `gbiso jobs: 0`).

### 7.4 Wave-1 test assertions rewritten for v1.2 (TASKS_1B section 9 and the v1.2 default changes)

| Test (file) | v1.1 assertion | v1.2 assertion |
|---|---|---|
| GP-17 `test_socket_golden`, `test_socket.py` | `enable` sets `persist.vendor.gb.nouveau=1` | sets `persist.vendor.gb.gfx_mode=auto`; `persist.vendor.gb.nouveau` stays unset; the 9.6 replies unchanged |
| GP-17 `test_socket_roles` | 15 commands x 6 roles | 28 commands x 6 roles |
| GP-30 `test_off_rebind` | `ctl.start gb_nouveau_off` once per bind with `/dev/gb_dgpu/off` and persist != 1 | `reconcile_off()`: in Intel mode after two idle ticks, again after 10 s; nothing in auto (B4); never an unbind |
| GP-32 `test_used_detach_and_rescan` | render 0666 with `vendor.gb.gfxdriver=on` + `/dev/gb_dgpu/gfxdriver` | 0660 (the gate is retired; 0666 only from gb_power's own `nvidia`) |
| GP-32 `test_socket_cmds` | once/test from `vendor.gb.nouveau.mode`; "module loaded without a device" and "a previous auto still runs" -> `next-boot`; disable writes persist 0 | once/test from the command line; those two -> `now` (bind path 7.2a; deferred start 23.7); disable writes `gfx_mode=intel` |
| `test_socket_cmds` status | `"persist":0` with nothing set; probation for mode `persist` | `"persist":1` (unset mode = auto); probation for mode `auto`, none for `persist` |
| GP-11 `test_used_detach_and_rescan` | fault `rescan` posts N06 | posts N20 (C22, 8.2) |
| GP-22 `test_notify` | in-memory queue max 8 | max 16 (G14, 12.1) |
| `test_misc` hot attach | 7.9: `ctl.start gb_nouveau` with mode persist + `refused:nodgpu` at start | 23.7: only for a new appearance (absent at start, then present); none with `gfx_mode=intel` |

### 7.5 Deviations (GP-1B-Dn) and their reasons

1. **D1 rebind clean mark.** 23.8 says the +120 s check repeats "bind + D3cold". gb_power accepts D0 at that point
   while nouveau is held (refcnt > 0) or a power hold is applied, and otherwise allows 30 s more for D3cold
   (`REBIND_CLEAN_GRACE_MS`) before it declares a failure: the binding may already serve an app on NVIDIA by then
   (nvidia is allowed after the 30 s check), and a literal check would fault a healthy GPU.
2. **D2** In the clean phase, an unbind in Intel mode (the rc's `gb_nouveau_off`) or a detach decision ends the guard
   without a fault (they unbind on purpose); `reconcile_off()` itself waits for the guard to end.
3. **D3 `selftest-hang`** posts N20 only, not N18 as well: N18 says "the NVIDIA compute service still works", which is
   not true while a probe is stuck in the kernel holding nouveau. The record is `fail hang` (sticky) all the same.
4. **D4 N18 "once per identity"** is kept in memory (per gb_power run): a stored `fail` found at start posts nothing,
   a new `fail` verdict posts N18 once; after a reboot a user re-run that fails again posts it again (5.1 has no file
   for it; spec question Q1).
5. **D5** Records written by gb_power (`interrupted`, `crash`, `hang`) carry `report=-`; the validation accepts `-`
   or `selftest-<N>.txt` (gb_power does not know the script's report number; Q2).
6. **D6 automatic starts.** "No valid record" starts at most once per boot per identity, the `error` retry at most
   once per boot (both also cover `interrupted`); a run that ends without a valid record is not restarted in the same
   boot, so a refusing script (exit 1, no file) cannot loop. `run-selftest` is not limited.
7. **D7** For 10 s after the service stopped there is no automatic start and `run-selftest` answers `EAGAIN`
   ("self-test finishing"), so the killed-hard marker check of 25.5 runs before the next test.
8. **D8** `J_MARK` jobs (markers on `/data`) run after a wedge (they are not dGPU writes; a session must stay
   closable) and a missed `J_MARK` deadline (20 s) is logged and treated as "not written", not as a wedge.
9. **D9** A failed `OP_MARK` in `J_RESCAN` cancels that job's `rescan` write (the guard ends; the staged rescan retries
   and re-arms).
10. **D10 apps-add / apps-commit.** `apps-add` checks the element syntax (`[A-Za-z0-9._]`, 1-127 characters, no empty
    element) and the count (more than `n` -> `EINVAL count mismatch`); `pkg_name_ok()` and packages.list are applied at
    `apps-commit` (dropped). A bad or mismatching `n` at `apps-commit` discards the staging.
11. **D11 run-selftest errors** beyond 26.1's list: `ENODEV` also without DTX or with the base detached; `EBUSY` also
    for kill, test boot, recovered; `EAGAIN` also before this binding's clean mark and during the 10 s of D7.
12. **D12 appearance** (23.7) = the dGPU function newly in the inventory, or a 10de PCI `add` uevent whose
    `PCI_CLASS` is 03xxxx (or absent); the HDA function's add is not one. A function present at gb_power's start is
    not an appearance (the boot-time rc start covers it). Deferred starts are dropped when the mode becomes intel or
    nouveau is bound.
13. **D13** `gfx_optin` is limited as a file to 64 KiB (gb_notify's limit), i.e. the joined list to 65535 bytes; with
    names <= 127 characters the 512-package cut always comes first.
14. **D14** `gfx_optin`/`gfx_optout` are written only with `cap` 1 (D8: nothing without the package); with the package
    but without DTX (a native-smoke image in VMware) the files are written, but no settings message is ever queued.
15. **D15** `prop_put()` no longer writes a property that already reads as wanted, also on the first publish (no empty
    `persist.vendor.gb.gfx_ok` is created on an image without the graphics stack).
16. **D16** The 5 s early refusal re-scans holders at most once per second.
17. **D17** Every clean shutdown (`sys.powerctl`) writes `gfx_unclean=0`, with or without a session: "two in a row"
    counts consecutive boots that ended inside an NVIDIA session.
18. **D18** The rebind guard treats an ESP lock directory without an owner file as held (it never waits, so the
    5-poll staleness rule of 5.4 cannot apply); only a dead or garbage owner is stolen.
19. **D19** The N16 flag of an attach event stays set until the next intel -> nvidia transition of this boot.
20. **D20** `set-mode intel` applies `gfx_force_intel("mode")` and stops a running self-test before it writes the
    property (the rc trigger may start `off` at once).
21. **D21** intel -> nvidia also waits (reason `loading`) while the node rule is skipped, which with a derived
    `nvidia` can only be gb_power's own 30 s `gb_nouveau_off` window: `gfx_active=nvidia` is never published before
    the render node could be raised to 0666 (23.3 step 3 before step 4).

### 7.6 Spec questions

* **Q1** N18 "once per identity" across reboots needs a persistent per-identity flag (for example in
  `selftest/`); none is listed in 5.1 (D4).
* **Q2** Should component 1 (and GbGpu, if it shows `report`) accept `report=-` in records written by gb_power (D5)?
* **Q3** 26.1 lists few `run-selftest` errors; the mapping of D11 is what gb_power answers. GbGpu should treat any
  `EAGAIN` as "try later".

### 7.7 Wave 2 edits to shared files

* `C:\GB-x86\inject\native\components\power.json` (W2-01): **no change** (still `bin\gb_power` -> `vup/bin/gb_power` and
  `gb_power.rc`); the files changed in place (7.2).
* `C:\GB-x86\verify_image.py` (W2-07, section 3.2's optional check): the expected `vup/bin/gb_power` sha256 is now
  `9b99252590118263fc4b21f460048d6f95d295917ae964d2e41ac6da927fd8ba`; the `gb.rc` checks of 3.2 are unchanged.
* Everything else that v1.2 needs in shared files (`gfxdriver.json`, `gfx_build_id`, the gb.rc checks of
  `gb_nouveau.rc` 15.1-v12, the bootlog `get-mode` line, docs) is in `integration\gfx-switching.md` W2G-01..11;
  gb_power adds nothing to it. W2G-07's bootlog `get-mode` reply needs no gb_power change (root and shell may send it).
* Preconditions gb_power relies on at run time (all from other components or Wave 2): `/odm/etc/gb/gfx_build_id`
  (W2G-02; without it `gfx_cap` is 0 and app graphics stay Intel), `ro.gfx.driver.0` in the native build.prop
  (W2G-01), `/data/vendor/gb/selftest` 0700 root from `gb_nouveau.rc`, the `gb_gfx_selftest` service, gb_notify's
  `kind=settings` handling.

### 7.8 Contract notes for the other components (v1.2 additions to section 4)

* gb_power **reads** (new): `persist.vendor.gb.gfx_mode`, `persist.vendor.gb.gfx_apps`, `ro.gfx.driver.0`,
  `ro.build.id`, `ro.build.version.incremental`, `init.svc.gb_gfx_selftest`, `init.svc.gb_nouveau_off`;
  `/odm/app/GbGfxDriver/GbGfxDriver.apk`, `/odm/etc/gb/gfx_build_id`, `/odm/etc/gb/gfx_app_prefs.default`;
  `/data/vendor/gb/{gfx_app_prefs,gfx_catalog,gfx_optin,gfx_optout,gfx_session,gfx_unclean,nouveau.loading}`,
  `/data/vendor/gb/selftest/{result,running}`; `/proc/sys/kernel/random/boot_id`, `/proc/uptime`; the ESP lock owner.
  It **never** reads `vendor.gb.gfx_*` or `persist.vendor.gb.gfx_ok` (outputs) and no longer reads
  `persist.vendor.gb.nouveau` or `vendor.gb.gfxdriver`.
* gb_power **writes** (new): `vendor.gb.gfx_active`, `vendor.gb.gfx_reason`, `vendor.gb.gfx_test`,
  `persist.vendor.gb.gfx_ok`, `persist.vendor.gb.gfx_mode` (set-mode/enable/disable, rebind failure, two unclean
  sessions), `persist.vendor.gb.gfx_apps` (set-app-policy), `vendor.gb.nouveau.foreign=1` + `/dev/gb_dgpu/foreign`
  (before the first nvidia), `/dev/gb_dgpu/{keep,noload}` (rebind failure); `ctl.start` of `gb_nouveau` (hot attach),
  `gb_nouveau_off` (reconcile_off) and `gb_gfx_selftest`; `ctl.stop gb_gfx_selftest` (decision, Intel switch,
  deadline); the files of 5.1 v1.2 it owns (`gfx_app_prefs`, `gfx_catalog`, `gfx_optin`, `gfx_optout`, `gfx_session`,
  `gfx_unclean`), `nouveau.loading` (rebind, `mode=rebind`), `selftest/result` (only `interrupted`, `crash`, `hang`)
  and removes `selftest/running` after a crash or a hard kill; the ESP lock with owner `<pid> gb_power <uptime_s>`.
* **Component 1 (nouveau-policy):** a `nouveau.loading` marker of the current boot (`boot=` = boot id) may be gb_power's
  (`mode=rebind`) while gb_power holds the ESP lock or the lock is busy; `gb_nouveau.sh` must leave it alone (7.2-v12
  step 6). `kind=settings` messages are exactly the three pairs of 12.1; `gfx_optin`/`gfx_optout` are plain lists (one
  package per line, no comments) and may be empty (0 bytes) -> `cmd settings delete`. Records written by gb_power
  have `report=-` (Q2). gb_power stops `gb_gfx_selftest` with `ctl.stop` and expects the TERM trap to write
  `interrupted`; if the marker is still there 10 s after the stop it writes `interrupted` itself.
* **Component 6 (gpu-ui):** the 26.3 goldens are reproduced byte for byte by the C tests and over a real socket; the
  status object's v1.2 fields come in the 9.5 order; `get-apps` replies are <= 3500 bytes with `next` = the index of
  the next entry or -1; `detach_ref` grows on every refusal because of holders and when a pending detach times out;
  N08 is suppressed only while `ui-caps notify` was sent in this gb_power run and `GbGpu.apk` exists (re-send it after
  every broadcast, as 27.3 says); `apps_sync` is 0 at every boot until a commit against the current packages.list.
  Errors of `run-selftest`: D11.
* **Component 4 (gfx-driver-pkg):** `vendor.gb.gfx_active` is exactly `intel` or `nvidia` (unset = intel at early
  boot); it becomes `nvidia` only after the render node is 0666 and returns to `intel` before the node is lowered.

### 7.9 Hardware-only (not run)

H-1B-01 (first boot: self-test pass in gblog, N16, cost of nouveau + GSP on every boot), H-1B-04 (the Settings keys at
run time: `all_apps` 0/3, the opt-in list, an Always Intel app has no nouveau fd), H-1B-05 (detach -> N17; reattach ->
rebind guard log, the order of the DTX attach event and the PCI add, the marker before the probe, N16, time to
NVIDIA; the residual race of 23.8), H-1B-06 (refused after about 5 s with an app on NVIDIA; Close apps and detach ends
with the latch open), H-1B-07 (Intel only: `reconcile_off`, 02:00.0 and 00:1d.4 in D3cold; an Intel switch while
busy finishes after the app closes), H-1B-08 (kill switch: `all_apps=3`, nothing loads), H-1B-10 (suspend/resume,
reboot, power-off with apps on NVK: no unclean count after a clean power-off), H-1B-11 (a kernel/OTA update re-tests),
plus the wave-1 GP-HW list (section 5, README).

## 8. v1.2 r2: review fix pass (INTERFACES 29)

**This section supersedes the artifact shas of 7.2 and the test results of 7.3.** Applies INTERFACES v1.2 r2
(section 29) R-1, R-2, R-3, R-4, R-5, R-6, R-8, R-9, R-12 and R-15 to `gb_power.c` (baseline `c1986aad`, kept as
`power\gb_power.c.v1.2r1`; binary `bin\gb_power.v1.2r1` = `9b992525`). `gb_power.rc` unchanged.

### 8.1 What changed

| Fix | Where (function) | What |
|---|---|---|
| R-1 | new `shutdown_markers()`, `powerctl_check()`, `term_handle()` | Once per run, from the tick that sees `sys.powerctl` **or** from the SIGTERM handler when `sys.powerctl` is set: unlink `gfx_session` and `dgpu_session`, unlink the rebind guard's own `nouveau.loading` when the guard is `RB_CLEAN` or `RB_SEEN` with nouveau bound and the file holds exactly the guard's line, `dir_sync`, `gfx_unclean` = `dgpu_unclean` = 0. A SIGTERM without `sys.powerctl` keeps everything (adopted after a restart). |
| R-2 | `rb_check()`, `g_rb.off_seen` | `off_seen` set on every watch tick that reads `init.svc.gb_nouveau_off` running; at the 30 s point a driverless dGPU with mode `intel` or `off_seen` ends the guard (`unbound by off (Intel mode)`, marker removed, no fault); the +120 s check uses `off_seen` instead of the instantaneous property. |
| R-3 | new `reconcile_on()` (status tick, after `hotattach_check()`); `hotattach_check()`, `set_mode_apply()` | 23.7a; deferred starts also wait for `gb_nouveau_off` and survive the ticks where an off has not unbound yet; `g_nv.last_start`/`busy_at` gaps (10 s, `RECONCILE_ON_GAP_MS`). |
| R-4 | new `dsess_job()`, `dsess_start_check()`, `dsess_eval()`, `dunclean_*()`, `MK_DSESS*`; N21 | 23.10a. |
| R-5 | `nodes_enforce()` | Decision: untouched; wedge: `nodes_lock()` every tick (log `dGPU wedged: N nouveau node(s) chmod 000`); otherwise lower/new-inode always, the raise only when `nodes_enforce_skip()` is false. `nodes_lock()` logs its "detach:" line only in a decision. |
| R-6 | `cmd_compute_start()`, `hold_eval()` | `EAGAIN` `NVIDIA driver starting` while `load_window_open()` or state `loading` (after `EBUSY`, before `ENODEV`); no `J_HOLD_ON` while `load_window_open()`. |
| R-8 | `set_fault()`, `dtx_on_request()`, `N06_FMT` | Blocking faults: `keep`, `noload`, `forced`, `persist.vendor.gb.gfx_mode=intel` (if not already, with `stack_present()`), N06 r2 text, also on the refused detach press. |
| R-9 | `decision_busy()` | N08 left to GbGpu only when `g_hold.n > 0`. |
| R-12 | `real_mark_op()`, `rb_arm()`/`rb_end()` (`g_rb.mark_line`), `mark_job_done()` `MK_UNRB`, `resume_check()` | `OP_UNMARK` with a value unlinks only a file holding exactly that line (`-ESTALE` otherwise, logged as "belongs to another window"); `pend_clear("system resumed")`. |
| R-15 | `gfx_tick()` (`g_gfx.sess_checked`), new `persist_ready()` | The gfx_session and dgpu_session start checks wait for `ro.persistent_properties.ready=true` or `sys.boot_completed=1`; prefs and catalog still load at the first data tick. |

### 8.2 Artifacts (sha256; these replace 7.2)

| File | sha256 |
|---|---|
| `power\gb_power.c` | `a8bf3f057bd2c33b841afa4cfed7f4ccb6a1337e41b3b5de2d4ffc4d9818d21c` |
| `power\bin\gb_power` (NDK r27c static, stripped, reproducible) | `0d6a0b289ee37b8c0cb16b9d8201dfcb64a3491c508f292ac92dafdb25703a2f` |
| `power\gb_power.rc` (unchanged) | `87347a9918d540fc42b7d33193735e139db0120dced312543619ecd45cd8769f` |
| `power\test\test_gb_power.c` | `4f4601c2363976ab13a3ac4ff9741cf5ca2a67f124520f361a16fb610054a779` |
| `power\test\test_gfx.c` | `423f2219462fb7852a9e1e90418baa11ec9c106e286f10ad2bd90252e5c34bbe` |
| `power\test\test_socket.py` (unchanged) | `7ee95ea435503af375af22b8c86f1dda5cfad023800b2f0b9509c5b329396725` |

### 8.3 Tests (builder 192.168.115.132, scratch `/build/native/dgpu/fix1b/gb-power/`; log `test\build_last_run.log`)

`build.sh` (NDK static `-Werror`, clang host `-Werror`, unit plain, unit ASan+UBSan, host daemon socket test, rc lint),
then a second NDK build in another directory and a socket smoke of the bionic binary. Unit groups: wave-1 478/478,
v1.2 399/399 (GP-1B-08 grew from 16 to 19 checks with the R-5 rewrite), review-fix 118/118 (GP-R1 24, GP-R2 9, GP-R3 40,
GP-R4 17, GP-R6 9, GP-R8 10, GP-R12 9), 995/995 plain and under ASan+UBSan with no sanitizer report; socket 146/146;
rc lint 0 problems; REPRO PASS; smoke `hello` role other, `get-mode`/`compute-start` EPERM, SIGTERM `exiting`,
`GB_POWER_TEST` strings 0. Mutation check (13 mutations each reverting one fix, all KILLED): see 8.6.

Rewritten earlier assertions: GP-15 "N06 text" (R-8 text); GP-1B-08 four node assertions (R-5): wedge -> 0000, the
two skip windows -> lowered but not raised, after 30 s -> raised, then lowered. `gfx_props()` also sets
`ro.persistent_properties.ready=true`.

### 8.4 Deviations and interpretations (GP-R-Dn)

* GP-R-D1 (R-1): the rebind marker is also removed in `RB_SEEN` when nouveau is already bound (the probe returned),
  not only in `RB_CLEAN` as review 1 proposed; in `RB_WAIT` and driverless `RB_SEEN` it stays.
* GP-R-D2 (R-2): the Intel-switch exception applies at the 30 s deadline, not on the first driverless tick: a probe
  that is still running when the user switches keeps its crash marker until the deadline.
* GP-R-D3 (R-3): `reconcile_on()` skips results a retry cannot change (`insmod-failed:*`, `refused:i915|hw|module`)
  and waits 10 s after gb_power's first tick; review 1's list did not have these.
* GP-R-D4 (R-4): separate `dgpu_session`/`dgpu_unclean` instead of widening `gfx_session` (whose 23.3 ordering and
  GP-1B-02/19 tests stay as they are); the marker starts where `nouveau.loading` ends, so a crash is counted by
  exactly one of the crash-loop marker and `dgpu_session`. A crash inside the self-test also counts here (and in the
  self-test's own `crashes`): two such crashes post N18 and N21.
* GP-R-D5 (R-5): during a decision the nodes are still not touched by `nodes_enforce()` (`nodes_lock()` owns them);
  the review's "always clamp" applies outside decisions.
* GP-R-D6 (R-8): the blocking faults also create `keep` + `noload` (not only `gfx_mode=intel`), so the rc's
  `gb_nouveau_off` does not try to unbind a GPU in runtime-PM error; `gfx_reason` becomes `crashloop` (as after
  `rb_fail()`).
* GP-R-D7 (R-15, own finding): see INTERFACES 23.10.

### 8.5 Wave 2 edits (shared files) - delta to 7.7

* `power.json`: unchanged (`bin\gb_power` -> `vup/bin/gb_power`, `gb_power.rc`).
* `verify_image.py` optional sha check (W2-07): now `0d6a0b289ee37b8c0cb16b9d8201dfcb64a3491c508f292ac92dafdb25703a2f`.
* Nothing else; the optional ueventd rule of `gfx-switching.md` W2G-12 only narrows a window gb_power already closes.

### 8.6 Mutation check

Each mutation reverts one fix in a copy of `gb_power.c`; the unit tests must fail. Script `power\test\mutation_r2.py`
(builder: `python3 -I mutation_r2.py <src> <work>`), log `power\test\mutation_r2.log`: **13 of 13 KILLED**: R-1
term_handle (GP-R1, 4 failures), R-2 (GP-R2, 2), R-3 (GP-R3, 3), R-4 (GP-R1 + GP-R4, 7), R-5 clamp (GP-1B-08, 4), R-5
wedge 000 (GP-1B-08, 1), R-6 compute-start (GP-R6, 3), R-6 hold gate (GP-R6, 1), R-8 (GP-R8, 2), R-9 (GP-R8, 1), R-12
unmark compare (GP-R12, 1), R-12 resume clear (GP-R12, 2), R-15 (GP-R4, 1).

### 8.7 Hardware-only (not run)

H-R-1 .. H-R-5 of `1b\TASKS_1B.md` 12.5, in addition to 7.9. In particular whether init's shutdown SIGTERM reaches
gb_power before its SIGKILL (INFERRED from AOSP `reboot.cpp`; R-1 also keeps the tick path).
