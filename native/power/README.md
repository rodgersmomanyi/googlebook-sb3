> **Repository note.** This is the component's working README, copied from the development tree. Paths
> such as `C:\GB-build\native\power` mean `native/power` in this repository, `C:\GB-build\native\dgpu`
> is [`native/dgpu`](../dgpu/), `C:\GB-x86` is the workspace (the repository root) and `builder:` paths are
> on the Linux builder. Build outputs, logs and older versions (`out/`, `logs/`, `superseded/`, `*.v1.1`, ...)
> are not published. What the NVIDIA support does for a user: [docs/NVIDIA.md](../../docs/NVIDIA.md).

# gb_power: battery, detach (DTX), dGPU hot-plug and graphics-switching daemon

**v1.2 r2 (review fix pass, INTERFACES section 29, 2026-10-25):** clean-shutdown handling of `gfx_session`,
`dgpu_session` and the rebind guard's marker from the SIGTERM handler (R-1); no false `rebind` fault on an Intel switch
during the watch (R-2); `reconcile_on()` re-starts gb_nouveau for a driverless dGPU in Auto/NVIDIA (R-3); the
`dgpu_session` count of unclean endings while nouveau is bound, N21 (R-4); node modes always lowered, 0000 after a wedge
(R-5); no compute start or hold in a load window (R-6); blocking faults set Intel mode (R-8); N08 for service-only
refusals (R-9); `OP_UNMARK` content check and resume clears a pending detach (R-12); persistent-properties gate (R-15).
Binary of r2: `0d6a0b28...` (source `a8bf3f05...`); details and tests: `integration\gb-power.md` section 8.
The pre-fix files are kept as `*.v1.2r1`.

**v1.2 r3 (Wave 2b, INTERFACES section 30 E-1, 2026-10-26):** the ESP lock follows 5.4 r3: no lock while a steal is
in progress (a live `/dev/gb_esp.lock.stale.<pid>`), the owner file of a new lock is created `O_EXCL` and an existing
one is never removed (`esp_claim()`), a put-back after a steal never replaces an entry (`RENAME_NOREPLACE`, up to 10
tries 20 ms apart; an ownerless moved directory is removed), and a release that finds its lock moved away by a steal in
progress is retried from the status tick for up to 6 s. Tests: group GP-W2B-1 (14 checks; 1009 checks, 0 failures, also
under ASan/UBSan). Binary `bin\gb_power` `acc23c89...` (source `49d0c40a...`, NDK build reproduced twice); the r2 binary
and source are in `C:\GB-x86\archive\pre-w2b\GB-build\native\power\`.

**v1.2 r4 (Wave 3, INTERFACES section 31 E-10/E-11, 2026-10-26):** ESP lock r4 (wave 2b review L1, L2, L4): no `mkdir`
while a steal is in progress (`esp_lock_try()` checks "busy" first), a lock naming gb_power's own pid is stale
(`esp_owner()`), the deferred release retries for 8 s (`ESP_REL_MS`, longer than a sleeping stealer's 5 s put-back
budget), and `nouveau_window_open()` also reads the owner of a live stealer's `.stale.<pid>` directory, so a detach
during a steal of gb_nouveau's lock is refused with N15. Tests: group GP-W3-1 (10 checks; 1019 checks, 0 failures, also
under ASan/UBSan; socket 146/146; rc lint clean). Binary `bin\gb_power` `0ac03a00...` (source `e5a0055a...`); the r3
binary is kept as `bin\gb_power.pre-w3` and the r3 source in `C:\GB-x86\archive\pre-w3\AUX\GB-build\native\power\`.

`gb_power` is the Surface Book 3 power daemon for Googlebook OS native images (`gb.hw=native`). It is a static
bionic binary started by init at `early-init` and runs as root in `u:r:shell:s0`. It implements plan Phase 3
(gb_power hot-plug hardening, deliverables 1-8), the server side of the Phase 7 control socket, plan P1 item 7
(non-VGA NVIDIA functions always driverless), the P6 compute stop hook and, since v1.2, the gb_power half of the
Intel/NVIDIA graphics switching ("wave 1b"). The contract is `C:\GB-build\native\dgpu\INTERFACES.md` **v1.2**:
sections 2, 4, 5, 8, 9, 10, 12 and 15.2, and for graphics switching 22-26 (the change list is section 28; the
rebased task list and the acceptance tests are `C:\GB-build\native\dgpu\1b\TASKS_1B.md` section 2). Integration
notes: `C:\GB-build\native\dgpu\integration\gb-power.md` (section 7 is v1.2).

## What it does

| Area | Behaviour |
|---|---|
| A. Battery (unchanged) | Sums BAT1 + BAT2 into `test_power`'s `test_battery`, mirrors ADP1, hides BATn from the health HAL. |
| B. Detach, quick path (unchanged) | No driver on any NVIDIA function and nouveau not used this boot: `HB`, then `CONFIRM`. |
| B. Detach, slow path | `/dev/gb_dgpu/detaching` set, load-window check (a live `gb_nouveau` ESP lock owner, or v1.2 gb_power's own rebind watch, refuses with N15), boot-disk guard, **(v1.2) app graphics forced to Intel (`vendor.gb.gfx_active=intel`) before anything else**, LATCH_LOCK, N07, `ctl.stop` of `gb_dgpu_test`, `gb_nvk_probe`, (v1.2) `gb_gfx_selftest` and `gb_gpu_compute`, nouveau DRM nodes `chmod 000` (also nodes that appear later), wait for the services, the load window and the `/sys/module/nouveau/refcnt` gate (30 s; **v1.2: refused after 5 s when every holder is a closable app process**), worker job 1 unbinds, main-loop re-check, worker job 2 PCI-removes every 10de function (the child re-checks the gate before each write), last checks, N12, `CONFIRM`. A refusal because of holders increments `detach_ref` (v1.2) and posts N08 unless GbGpu declared `ui-caps notify`. |
| B. Latch lifecycle | `LATCH_LOCK` while any driver is bound to any 10de function or a base drive is mounted, re-issued after every resume and every `BASE_CONNECTION`; `LATCH_UNLOCK` when driverless, at start only if driverless, at shutdown, on SIGTERM only if driverless or shutting down. |
| B. Refusals | `LATCH_CANCEL` plus a notification: busy (N08 or GbGpu's own), load window (N15), boot disk on the base (N09/N10), wedged (N11), blocking fault (N06). A second press during a decision aborts at the next safe point. |
| C. dGPU policy | nouveau is never unbound by gb_power on its own initiative (only by the detach worker). Any other driver on the VGA function and any driver on a non-VGA function (HDA) is unbound by the worker. Runtime PM `auto` on driverless functions and the bridges. |
| C. Node permissions (v1.2) | `stat()` on every fast tick and after PCI/drm uevents: render `0666` only while gb_power's own derivation says `nvidia`, else `0660` root:graphics; card `0600` root:root. A new inode is set, a too permissive node is lowered, a `0000` node is never raised (decision, `off` window, wedge); skipped during a decision, after a wedge, while a live `gb_nouveau` owns the ESP lock, and 30 s after gb_power's own `gb_nouveau_off` start. The v1.1 P8 gate (`vendor.gb.gfxdriver` + flag) is retired. |
| C. Staged rescan | On attach/cancel/latch close when nouveau was used (or gb_power removed the functions) and no 10de device is present: port `on`, D0 wait, `rescan`, retries at +2/+5/+15 s, `auto` again; fault `rescan` / `bar`. (v1.2) With nouveau.ko loaded the rebind guard's crash marker is the first op of the rescan job. |
| C. Watchdog | `runtime_status=error` -> fault `rpm-error`; D0 > 60 s with refcnt 0 and no power hold (compute or, v1.2, self-test) -> `d0-idle`. These two block detach and post N06; (v1.2) `rescan`, `bar` and `selftest-hang` post N20; `rebind` posts N02. Every fault makes app graphics Intel. |
| C. Hot attach (v1.2, 23.7) | A driverless dGPU with a BAR newly appears (PCI add uevent of class 03xxxx, or newly in the inventory) while the mode wants a driver: `ctl.start gb_nouveau` (insmod, or its bind path when the module is loaded); once per appearance, 3 per boot; deferred (not counted) while `gb_nouveau` runs; `set-mode auto/nvidia` records a deferred start when it finds `gb_nouveau` running. |
| C. `reconcile_off` (v1.2, 23.6) | Intel mode, nouveau bound, refcnt 0 on two ticks, no `keep`, not a once/test boot, no decision/rebind guard/wedge, `gb_nouveau_off` and `gb_nouveau` not running, 10 s since the last start: `ctl.start gb_nouveau_off` (whose refcnt gate decides). Replaces v1.1's `off_rebind_check()`. |
| C. Rebind guard (v1.2, 23.8) | The kernel re-probes a re-added dGPU itself while nouveau.ko is loaded. Armed on the DTX attach event, a 10de VGA PCI add uevent, or as the first op of gb_power's rescan: ESP lock (owner `<pid> gb_power <uptime_s>`, atomic stale steal; a live holder is logged), crash marker `nouveau.loading` = `mode=rebind t=<s> boot=<id> argv=-` through the worker (fsync file + directory), detach refused (N15) during the watch. Healthy (bound + `suspended` + `D3cold` within 30 s): lock released, NVIDIA allowed, marker removed at +120 s (`clean_seen`). Failure: marker removed, `keep` + `noload`, fault `rebind`, `gfx_mode=intel`, N02 (`rebind`). No dGPU in 60 s or the base gone: ended quietly. |
| G. Graphics derivation (v1.2, 23.2) | `vendor.gb.gfx_active` (`intel`/`nvidia`) and `vendor.gb.gfx_reason` from the 16-row table (`nopkg`, `nodtx`, `kill`, `test`, `crashloop`, `wedged`, `fault`, `mode`, `detached`, `detaching`, `loading`, `nodriver`, `untested`, `testerror`, `selftest`, `ok`). gb_power never reads its outputs back. |
| G. Transitions (v1.2, 23.3) | intel -> nvidia: `foreign` (property + file), app-graphics session marker `gfx_session` through the worker (fsync), render node 0666, `gfx_active=nvidia`, N16 after an attach event or a self-test pass, broadcast. nvidia -> intel: `gfx_active=intel` first, node 0660, marker removed, `gfx_unclean=0`. |
| G. Session marker (v1.2, 23.10) | A `gfx_session` of an earlier boot at start = that boot ended with apps on NVIDIA: `gfx_unclean` += 1; two in a row: `gfx_mode=intel`, N19, reason `crashloop`. Removed at once at `sys.powerctl`. |
| G. App driver selection (v1.2, 24) | Per-app choices `gfx_app_prefs` (`set-app-intel` / `set-app-nvidia`, <= 128, seeded once from `/odm/etc/gb/gfx_app_prefs.default`), the app catalog GbGpu pushes (`apps-begin` / `apps-add` / `apps-commit`, <= 512, stamped with the packages.list it was checked against), the computed `gfx_optin` (policy `games`: games + Use NVIDIA; `all`: every catalog app + Use NVIDIA; minus Always Intel; installed only; <= 512, class a cut first) and `gfx_optout`. |
| G. Settings push (v1.2, 23.4) | `kind=settings` messages for gb_notify: `updatable_driver_all_apps` 0/3 and the two lists by `src=`; all three once per boot after `sys.boot_completed`, then on every change; never without the driver package or DTX (VMware: the keys stay null). |
| G. Self-test (v1.2, 25) | Starts `gb_gfx_selftest` on the first healthy binding (clean mark reached) without a valid record for the identity (`ro.build.id/ro.build.version.incremental`, `uname -r`, `/odm/etc/gb/gfx_build_id`), holds the dGPU awake (`power/control=on` through the worker), validates the root-owned record `selftest/result` (truth; mirrored to `persist.vendor.gb.gfx_ok` and `vendor.gb.gfx_test`), retries `error` once per boot (<= 3 attempts per identity), writes `interrupted` (killed hard), `crash` (a marker of an earlier boot; two crashes = `fail`) and `hang` (360 s deadline + 10 s: fault `selftest-hang`, N20) itself, N18 on a new `fail`. |
| G. Pending detach (v1.2, 23.9) | `close-holders-and-detach`: force-stops the closable holders, stops the GPU user services, then `SDTX_IOCTL_LATCH_REQUEST` once the refcnt gate reads 0 twice 500 ms apart (15 s deadline, then `detach_ref`+1). |
| State | `vendor.gb.dgpu.state` (8.1, unchanged), `.holders`, `.latch`, `vendor.gb.compute.state`, `vendor.gb.power.api`, (v1.2) `vendor.gb.gfx_active`, `vendor.gb.gfx_reason`, `vendor.gb.gfx_test`, `persist.vendor.gb.gfx_ok`. |
| E. Control socket | `/dev/socket/gb_power`: one line in, one JSON line out, roles from `SO_PEERCRED`; 15 v1.1 commands plus (v1.2) `get-mode`, `set-mode`, `set-app-policy`, `set-app-intel`, `set-app-nvidia`, `get-apps`, `apps-begin`, `apps-add`, `apps-commit`, `run-selftest`, `list-holders`, `close-holders-and-detach`, `ui-caps` (root, shell, ui only). `enable` = `set-mode auto`, `disable` = `set-mode intel` (they write `persist.vendor.gb.gfx_mode`; the retired `persist.vendor.gb.nouveau` is never written). The status object has the 12 v1.2 fields after `gfxdriver`. |
| F. Notifications | Message files in `/data/vendor/gb/notify` plus `vendor.gb.notify`, queued in memory (16, newest per tag/settings key) until boot completes; gb_power never forks Java tools. |
| D. Platform profile (unchanged) | `gb_power -p <profile>`. |
| `--dtx-monitor` (unchanged) | Read-only DTX event printer for the EC-timeout measurement. |

The main process never performs a dGPU sysfs write that can block: `unbind`, `remove`, `rescan` and
`power/control=on` (rescan port, compute and self-test hold) run in a forked worker with a deadline. (v1.2) The
crash and session markers on `/data` (`OP_MARK`/`OP_UNMARK`, with `fsync` of the file and the directory) also run in
the worker; they are not dGPU writes, so they still run after a wedge and a missed marker deadline is not a wedge.
If a dGPU job misses its deadline the dGPU is **wedged**: the latch stays locked, no further dGPU writes happen this
boot, the stuck child is left alone. Status polling reads only cached sysfs attributes (`power_state`,
`runtime_status`, `refcnt`, plus `vendor`/`class`/`device`/`resource` once per device appearance); it never reads
config space and never opens DRM nodes.

## Files

| File | Purpose |
|---|---|
| `gb_power.c` | The daemon (single file; section G is the v1.2 graphics switching). |
| `bin/gb_power` | NDK static, stripped build (shipped as `/vendor/bin/gb_power` by `inject\native\components\power.json`). |
| `gb_power.rc` | init fragment (15.2): service with `socket gb_power stream 0666 root root`, `mkdir /dev/gb_dgpu 0700 root root` at early-init. v1.2: comment only (the graphics triggers and `gb_gfx_selftest` are in `gb_nouveau.rc`). |
| `uapi/linux/surface_aggregator/dtx.h` | Verbatim copy of the kernel uAPI header. |
| `build.sh` | Builds and runs every test (unchanged). |
| `test/test_gb_power.c` | Unit tests (includes `gb_power.c`; fake sysfs/procfs tree, fake clock, mock EC, fake kernel in the forked worker). |
| `test/test_gfx.c` | (v1.2) The graphics-switching groups GP-1B-01..23, included by `test_gb_power.c`. |
| `test/test_socket.py` | Protocol test against the real daemon loop (host build) over AF_UNIX: 9.6 and (v1.2) 26.3 goldens. |
| `test/check_rc.py` | Static check of `gb_power.rc` (v1.2: no graphics triggers or `gb_nouveau*` services in this file). |
| `test/build_last_run.log` | The last build.sh run and the reproducibility check. |
| `modules.load.power`, `patches/` | Unchanged (kernel modules and patches 0001-0003). |
| `*.v1.1`, `*.pre-dgpu`, `*.pre-nouveau`, `*.merged` | Local backups of earlier versions (`*.v1.1` = the wave-1 v1.1 deliverables `25bddd1f`/`05d9b2fc`). Not shipped. |

## Build and test

On the builder, from a scratch copy (never inside `/build/dist`):

```sh
mkdir -p /build/native/dgpu/gb-power/v12/src && cd /build/native/dgpu/gb-power/v12/src
# copy gb_power.c, gb_power.rc, build.sh, uapi/ and test/ (with test_gfx.c) here, then:
WORK=/build/native/dgpu/gb-power/v12/work sh ./build.sh
```

`build.sh` steps (heavy steps at `nice -n 19 ionice -c3`):
1. NDK clang `--target=x86_64-linux-android34 -O2 -Wall -Wextra -Werror -static`, stripped; `file` must say
   statically linked; prints the sha256.
2. clang (NDK toolchain, host target) `-Wall -Wextra -Werror -fsyntax-only` on `gb_power.c` and the unit tests.
3. Unit tests built with gcc `-Wall -Wextra -Werror` (wave-1 groups, then the v1.2 groups with one summary line each).
4. The same unit tests with `-fsanitize=address,undefined -fno-sanitize-recover=all`; any report fails the build.
5. Host build of the daemon and `python3 -I test/test_socket.py`.
6. `python3 -I test/check_rc.py gb_power.rc`.

The numbers of the last run are in `test/build_last_run.log` and in `integration\gb-power.md` section 7.

Test-only hooks exist only in host builds (`#ifndef __ANDROID__`), and only together with `-r`:
* `GB_POWER_TEST_UID`: overrides the peer uid, to pick a role;
* `GB_POWER_PROPS_DIR`: stores system properties as one file each;
* (v1.2) `GB_POWER_TEST_DTX`: answers the DTX ioctls like an EC with the base attached (the tree's
  `/dev/surface/dtx` is a FIFO), for the 26.3 golden transcripts over a real socket.

With `-r` (both builds, test mode only) `uname -r` is read from `<root>/proc/sys/kernel/osrelease` and files owned by
the test user count as root-owned. The Android binary has none of the environment hooks: on the builder it reports
the real uid 1000 as role `other`, answers `get-mode` with `EPERM`, and `strings` finds no `GB_POWER_TEST`.

## Options

`-p <profile>` | `-o` | `-r <root>` | `-S <path>` (test socket, only with `-r`) | `-H` | `-D` | `-G` | `-I` | `-K <driver>`
| `-v` | `--dtx-monitor` (unchanged in v1.2)

`-K` changes which driver gb_power leaves bound on the VGA function (default `nouveau`; `-K ''` unbinds every
driver, tests only). Non-VGA functions are always kept driverless (except with `-G`/`gb.dgpu=keep`).

## Test map

### v1.2 (TASKS_1B 2.2)

| Id | Where (`test/test_gfx.c` unless noted) | What is checked |
|---|---|---|
| GP-1B-01 | `test_gfx_matrix` | 7560 combinations (mode 5 x base 3 x dGPU 6 x record 7 x special 12): published `gfx_active`/`gfx_reason` (and render 0666 for nvidia) equal an oracle written as data from the 23.2 table. |
| GP-1B-02 | `test_gfx_transitions` | One ordered log: foreign, `MARK:gfx_session`, `chmod 0666`, `gfx_active=nvidia`; back: `gfx_active=intel`, reason, `chmod 0660`, `UNMARK`, `gfx_unclean=0`. A failed marker write stays Intel and is retried. |
| GP-1B-03 | `test_gfx_spoof` | `gfx_ok=1` without a record, an external `gfx_active=nvidia`, record symlink / not root-owned / > 2 KiB / each key missing / malformed values / duplicate key, a spoofed `init.svc.gb_nouveau_off=running`. |
| GP-1B-04 | `test_gfx_settings` | Nothing before boot; the three exact messages once; on changes; newest wins while queued; `all_apps=3` in kill, test, recovered (keep and forced), Intel and fail boots; never with cap 0 or without DTX. |
| GP-1B-05 | `test_gfx_prefs` | Add/replace/clear, sorted unique 0644 file, symlinked `.tmp` not followed, 129th -> EBUSY, ENOENT, clearing an uninstalled one, EINVAL syntax, ENODEV cap 0, the seed (absent file only). |
| GP-1B-06 | `test_gfx_catalog` | Exact replies, the stamped catalog file, no apps-begin, count mismatch, > 512, syntax, unknown/duplicate dropped, per-connection staging discarded on close, restart, `apps_sync` 0 after a packages.list change and 1 after the commit. |
| GP-1B-07 | `test_gfx_optin` | Both policies, Always Intel wins, installed only, rewrite only on change (same inode), 60 random scenarios against the 24.4 oracle, 700 candidates -> 512 (every game, the first 112 other apps). |
| GP-1B-08 | `test_gfx_nodes` | Drift 0660 -> 0666 in one tick, 0000 never raised, lowered in Intel mode, 0644 -> 0660, 0600 left, skipped during a decision / after a wedge / during a gb_nouveau ESP lock / 30 s after the own off start (no `nvidia` before the node can be raised); re-created nodes. |
| GP-1B-09 | `test_gfx_reconcile` | One start after two idle ticks, none within 10 s; none with keep, once, test, rebind guard, off or auto running, decision, in use (`pending=intel`), auto; never an unbind. |
| GP-1B-10 | `test_gfx_hotattach` | One start per appearance, 3 per boot; none in intel mode, kill, keep, noload, wedged, fault, rebind watch, booted unset, detached; deferred while gb_nouveau runs; `set-mode auto` deferred start. |
| GP-1B-11 | `test_gfx_rebind` | DTX attach / 10de VGA add uevent / J_RESCAN first op; owner line; marker line exact (0600); N15 during the watch; healthy -> lock released, NVIDIA + N16, marker until +120 s, `clean_seen`; not bound / no D3cold -> keep, noload, fault `rebind`, `gfx_mode=intel`, N02, no N06; no dGPU in 60 s; held, stale and own-boot cases. |
| GP-1B-12 | `test_gfx_detach` | `gfx_active=intel` before LATCH_LOCK and chmod 000; `ctl.stop gb_gfx_selftest`; app holders refused at 5 s, a root holder at 30 s; NVIDIA again after the refusal; N17 after a confirmed detach, none in Intel mode. |
| GP-1B-13 | `test_gfx_n08` | N08 exact without GbGpu, without `ui-caps notify`; suppressed (detach_ref + broadcast) with it; restored by `ui-caps none`. |
| GP-1B-14 | `test_gfx_close_detach` | Force-stops, `pending=detach`, LATCH_REQUEST after two zero reads, the normal decision to CONFIRM; 15 s deadline; EBUSY, ENODEV, EINVAL. |
| GP-1B-15 | `test_gfx_selftest` | Not before the clean mark; start + worker hold; no d0-idle for 90 s; release on stop; pass -> NVIDIA + N16; error retried once per boot, <= 3 attempts; run-selftest bypasses both; one N18 per identity; never in a test boot, decision, pending detach, rebind watch, Intel mode, kill, fault. |
| GP-1B-16 | `test_gfx_identity` | Each of `ro.build.id`, `ro.build.version.incremental`, `uname -r`, `gfx_build_id` makes the record stale -> untested -> test started. |
| GP-1B-17 | `test_gfx_interrupt` | Killed hard after a stop: `error interrupted` after 10 s, attempts kept, marker removed; crash 1 -> `error crash`, crash 2 -> `fail crash` + one N18; own-boot marker -> interrupted; a running test adopted. |
| GP-1B-18 | `test_gfx_deadline` | ctl.stop at 360 s, fault `selftest-hang` + N20 at +10 s, hold released, `fail hang`, detach not blocked. |
| GP-1B-19 | `test_gfx_session` | Another boot's session -> unclean 1 (kept by the new session); twice -> intel, N19, crashloop, 0; `sys.powerctl` -> removed, 0; own-boot session adopted. |
| GP-1B-20 | `test_gfx_socket`, `test_socket_roles`, `test_socket.py` | The 8 lines of 26.3 byte-exact (C and real socket), the 9.6 goldens unchanged, 28 commands x 6 roles, the 26.2 effect matrix (20 cases), enable/disable mapping, get-apps paging (128 choices of 127 characters, <= 3500 bytes), list-holders. |
| GP-1B-21 | `test_gfx_status`, `test_socket.py` | v1.2 fields in order after `gfxdriver`; 48 holders <= 4096 bytes. |
| GP-1B-22 | `test_gfx_broadcast` | Broadcast on mode, policy, active, reason, test, detach_ref, packages.list, pending; coalescing 2 s; once after boot with `apps_sync` 0. |
| GP-1B-23 | `test_gfx_vmware`, `test_socket.py` | `-D`: `nodtx`, get-mode works, no settings message in 20 fake minutes; the VM image: `nopkg`, set-mode ENODEV. |
| GP-1B-24 | `build.sh` | ASan/UBSan clean, `-Werror` clean, NDK static stripped binary reproducible. |
| GP-1B-25 | `test_gb_power.c` | Wave-1 GP-01..GP-32 pass with the rewritten assertions of TASKS_1B section 9 (integration file 7.4). |

### Wave 1 (INTERFACES 18.2, unchanged ids)

| Id | Where | What is checked |
|---|---|---|
| GP-01 | build.sh step 1 | `-Werror` static build, sha recorded |
| GP-02 | build.sh steps 3-4 | plain and under ASan/UBSan |
| GP-03 | `test_busy_refuse` | Heartbeats <= 2 s for the whole 30 s decision; a stopped gb_power makes the locked EC cancel, never open. |
| GP-04 | `test_busy_refuse` | Holders by maps and fd: refused after 30 s, nodes 000 then 0660/0600, N08 names both. |
| GP-05 | `test_hung_unbind` | Pipe-blocked unbind: wedged 20.0-20.5 s after the start, battery updated during the hang, no further writes. |
| GP-06..08 | `test_latch`, `test_dtx` | Latch at start, after resume / BASE_CONNECTION, SIGTERM rules. |
| GP-09 | `test_bootdisk` | N10 / N09 / internal disk quick path. |
| GP-10, GP-11 | `test_used_detach_and_rescan`, `test_misc` | Remove before confirm; staged rescan; (v1.2) the `rescan` fault posts N20. |
| GP-12 | `test_double_press` | Abort never inside a worker job. |
| GP-13, GP-14 | `test_never_unbind_nouveau` | No unbind on a property flip; HDA always driverless; `-K`. |
| GP-15 | `test_watchdog` | `rpm-error`, `d0-idle`, no fault in use or under a hold. |
| GP-16 | `test_state` | Every row of 8.1 (unchanged in v1.2). |
| GP-17 | `test_socket_roles`, `test_socket_golden`, `test_socket.py` | Command x role matrix (v1.2: 28 commands); 9.6 goldens byte-exact; (v1.2) enable writes `gfx_mode=auto`. |
| GP-18..23 | `test_socket_limits`, `test_socket_watch`, `test_socket_ui_role`, `test_socket_compute`, `test_notify`, `test_monitor` | Limits, watch, ui role, compute hold, notification queue (v1.2: 16), `--dtx-monitor`. |
| GP-24 | Wave 2 | VMware smoke. |
| GP-25..32 | `test_load_window`, `test_gate_split`, `test_release_window`, `test_off_rebind`, `test_bootdisk_latch`, `test_misc`, `test_socket_cmds` | v1.1 additions; (v1.2) GP-30 rewritten for `reconcile_off()`, GP-32 for the retired gfxdriver gate and the 26.2 effects. |

## Hardware-only checks (nothing here ran on the real SB3)

Wave 1 (GP-HW, plan Phase 3): driverless detach/attach timing; the EC timeout with `--dtx-monitor`; bound and idle
detach, rescan and rebind; an `nvk_probe` holder refused; gb_power killed mid-decision; detach while suspended;
shutdown with nouveau bound; boot detached then attach (v1.2: through the 23.7 hot attach); a rescan that recreates
02:00.1; whether the latch lock survives s2idle/EC reset/S5; the holder-scan cost; the refcnt baseline with nouveau
bound (expected 0, else the state is permanently `busy:1`, which keeps app graphics working but makes detach refuse).
The DMA-FQ IOMMU domain must not ship before a hardware run.

v1.2 (TASKS_1B section 7; the gb_power parts): H-1B-01 (first boot: self-test pass, N16), H-1B-04 (the Settings
keys at run time), H-1B-05 (detach/reattach: N17/N16, the order of the DTX attach event and the PCI add, the rebind
marker before the probe, time to NVIDIA), H-1B-06 (refused after about 5 s with an app on NVIDIA, Close apps and
detach), H-1B-07 (the Intel switch: `reconcile_off`, D3cold), H-1B-08 (kill switch: `all_apps=3`), H-1B-10
(suspend/resume, reboot and power-off with apps on NVK: no unclean count after a clean power-off), H-1B-11 (a new
identity re-tests).
