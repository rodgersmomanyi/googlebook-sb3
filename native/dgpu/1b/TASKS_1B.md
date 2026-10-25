# dGPU wave 1b: task lists, acceptance tests, changed defaults, safety argument (v1.2)

Version 1.2, 2026-10-25. Companion of `C:\GB-build\native\dgpu\INTERFACES.md` **v1.2** ("I" below; section numbers
`I n.m`), which now contains the whole graphics-switching contract (I 22-28) and supersedes `1b\INTERFACES_1B.md`.
"REQ" is `GRAPHICS_SWITCHING_REQ.md`, "PLAN" is `NVIDIA_PLAN.md` (scratchpad `nvresearch\`), "REVIEW" is
`1b\REVIEW.md`. The previous revision of this file (written against INTERFACES v1) is kept as `1b\TASKS_1B-rev1.md`;
its line numbers are stale. Every citation below is to the files of section 0.1 (the v1.1 wave-1 deliverables), by
function name **and** current line.

---

## 0. Rules for wave 1b

* **Owners** (I 1.1): nouveau-policy (component 1), gb-power (2), nvk-gfxdriver for `gfx-driver-pkg\` (4), gpu-ui (6).
  k14 (3) and gpu-compute (5) have no 1b work (compute only needs `standby`/`busy:N`, I 9.3; it keeps working when the
  self-test fails).
* **Edit only** the four component folders (`C:\GB-build\native\nouveau-kernel\`, `...\power\`,
  `...\nvk-android\` + `...\gfx-driver-pkg\`, `...\gpu-ui\`) and `C:\GB-build\native\dgpu\` docs. Shared image files
  (`C:\GB-x86\build_esp.py`, `build_usb.py`, `stage_kernel.py`, `verify_image.py`, `inject\**`, `iso\**`,
  `dualboot\**`, `gb_slotsync.c`) stay untouched; their edits are listed in `integration\gfx-switching.md`.
* **No hardware.** Every acceptance test runs on the builder or the Windows host with fakes (fake sysfs/proc trees,
  mock DTX, fake clocks, worker fakes, mock sockets, lavapipe, drm-shim). `HW` items (section 7) are for the user. VM
  items that boot or install need the user's approval (section 5); read-only adb is allowed.
* **Builder etiquette** as I 1.4: `ssh -i C:/GB-build/builder_key builder@192.168.115.132`, `nice -n 19 ionice -c3`,
  `pgrep -af gbiso` empty and 1-min load < 2 before heavy jobs, `/` and `/build` > 5 GB free (2026-10-25: 27 GB and
  12 GB), never touch `/build/dist` or `/var/tmp/gbiso`, Python with `-I`, no downloads beyond the Ubuntu archive and
  the already-configured SDK.
* **Hand-off:** each component adds a section "v1.2 (wave 1b)" to its existing integration file
  (`integration\nouveau-policy.md`, `gb-power.md`, `nvk-gfxdriver.md`, `ux-app.md`) with artifacts (sha256), the tests
  run (PASS/FAIL/SKIP + log path), deviations (`<ID>-1B-Dn`) and spec questions. Nobody edits INTERFACES.md during the
  implementation; questions go to the integration file.

### 0.1 Code baseline (the files every line number below refers to; sha256 first 8 hex)

| Component | Files |
|---|---|
| gb-power | `power\gb_power.c` `25bddd1f` (3726 lines; `bin\gb_power` `05d9b2fc`), `power\gb_power.rc` `395aad4d`, `power\test\test_gb_power.c` `033307d3` (2534 lines), `power\test\test_socket.py` `3e658ef1` |
| nouveau-policy | `nouveau-kernel\native\gb_nouveau.sh` `aebf9189` (1289), `gb_dgpu_report.sh` `8c5f0b2a` (620), `gb_notify.sh` `a1f465a9` (116), `gb_nouveau.rc` `633e8e91` (86); `nouveau-kernel\test\test_gb_nouveau.sh` `5370c8bf`, `shims.sh` `86c7f9ee`, `rc_lint.sh` `1c2bad76`, `run_tests.sh` `e1bab8dc` |
| gfx-driver-pkg | `gfx-driver-pkg\shim\gb_shim.c` `ef329edd` (840), `apk\AndroidManifest.xml.in` `5dd76211`, `apk\apk_tool.py` `c4e6bc5f`, `apk\build_apk.sh` `29e2d1cd`, `apk\check_apk.sh` `e38b6bce`, `gfxdriver.json` `d87a9bab`, `gb_gfxdriver.sh` `c28f4d8c`, `gb_gfxdriver.rc` `a49ed94b`, `test\test_shim.c` `b5fbce93`, `test\fake_icd.c` `14742e95`, `test\run_chroot_tests.sh` `4cbd7ee7`, `test\test_gb_gfxdriver.sh` `1e86dd04`; `nvk-android\src\nvk_probe.c` `5c7a47ac` |
| gpu-ui | `gpu-ui\AndroidManifest.xml` `40264996`, `src\gb\sb3\gpu\GpuTileService.java` `c3cd7d6f`, `SettingsActivity.java` `27d1236e`, `HoldersActivity.java` `bc7b1bb2`, `Notifier.java` `6c22b8a9`, `StateReceiver.java` `cae93ab8`, `SwitchProvider.java` `01276264`, `Gb.java` `34ac8315`, `core\GbPowerClient.java` `60612187`, `core\StateMapper.java` `873e3803`, `core\Status.java` `66b2071d`, `core\Texts.java` `e4e0b618`, `res\values\strings.xml` `4ee79450`, `gpu_ui.json` `3f9ed61e`, `gb_gpu_ui.rc` `1c8ca925`, `test\mock_gb_power.py` `3c26338d`, `test\test_mock.py` `d84782e7`, `test\check_apk.py` `f7561714`, `test\java\...\UnitTests.java` `231a0e7d`, `ProtocolTests.java` `f539ce37`, `run_tests.sh` `141414b4` |

---

## 1. nouveau-policy (component 1)

### 1.1 Tasks

| # | File / function (line in 0.1) | Change (I section) |
|---|---|---|
| T1.1 | `gb_nouveau.sh`: new `gfx_mode()` next to `gp()`/`sp()` (`:151-153`); new `set_booted()` | `gfx_mode()` per I 22.2 (no `ro.vendor.gb.gfx_default`). `set_booted()` = `setprop vendor.gb.nouveau.booted 1` only when it is not `1`; it replaces every direct set (`refuse()` `:807`, `do_auto()` `:931`, `:974`, `:1071`, `unhealthy()` `:853`) (REVIEW E2). |
| T1.2 | `do_auto()` (`:924-1095`) | Reorder per I 7.2-v12: the "already loaded" block (`:928-933`) moves after the mode decision (`:966-976`) and becomes the 4-way branch of step 8 (bound / `keep` -> `refused:keep` / driverless -> bind path / absent -> `refused:nodgpu`). Crash-loop branch (`:952-965`): compare the marker's `boot=` with `/proc/sys/kernel/random/boot_id` (same boot: go on); `sp persist.vendor.gb.gfx_mode intel` replaces `sp persist.vendor.gb.nouveau 0` (`:959`); N01 v1.2 text (`:963`). Decide step (`:966-976`): `gfx_mode()` instead of `persist.vendor.gb.nouveau` (`:967`), `MODE=auto`. Probation (`:977-981`): `MODE=auto`. Marker write (`:1029-1035`): v1.2 line with `boot=`. Remove the gfxdriver `foreign` block (`:1036-1040`). |
| T1.3 | new `do_bind()` (bind path I 7.2a), modelled on `unbind_all()` (`:1133-1155`); reuses `i915_ok()` `:244`, `find_dgpu()` `:227`, `bar_ok()` `:237`, the detach wait of `do_auto()` (`:995-1003`), `lock_esp()` `:315`, `esp_mounted()` `:360`, `late_refuse()` `:880`, `health_check()` `:887-922`, `unhealthy()` `:841-855` | Wait for a running `gb_nouveau_off` (30 s, `refused:off`); guards; detach wait (`refused:detach`); ESP lock; marker `mode=bind`; `kernel.panic`; late checks; background `bind` write + 40 x 0.5 s poll; hang -> `wedged`+`keep`+`noload`+`gfx_mode=intel`, marker kept, `bind-failed`, N05; not bound -> `unhealthy bind`; health; clean mark without `clean_boots++` (REVIEW C8). |
| T1.4 | `unhealthy()` (`:841-855`), `insmod_failed()` (`:857-878`) | `sp persist.vendor.gb.gfx_mode intel` after `flag_set keep`/`noload` (replaces `:845` and `:870`); N02 v1.2 text (`:849`). |
| T1.5 | `gfx_open()` (`:272-274`), `apply_perms()` (`:276-288`), `parse_cmdline()` (`:184-214`) | Delete `gfx_open()`; `apply_perms()` always 0660 (I 7.5); `parse_cmdline()` unchanged (it never read `gb.gfxdriver=`). |
| T1.6 | `do_off()` (`:1157-1228`), `GPU_USERS` (`:52`), `stop_gpu_users()` (`:1118-1131`) | `off-skipped` after the once/test block (`:1161-1168`) and again after the refcnt read (`:1183-1185`, permissions restored); `GPU_USERS` gains `gb_gfx_selftest`; N03 v1.2 text (`:1194`); no N04 (`:1208`, `:1224`); `/dev/gb_dgpu/off` still created. |
| T1.7 | `gb_nouveau.rc` (`:1-10` header, `:14-18`, `:65-69`) | Exactly I 15.1-v12 below the header: `selftest/` mkdir, service `gb_gfx_selftest`, the three `gfx_mode` triggers replace the two `persist.vendor.gb.nouveau` triggers; header comment describes the modes. |
| T1.8 | `gb_dgpu_report.sh`: new `do_selftest()`; `case $CMD` (`:613-620`, usage `:617`); reuse `lock_esp()` (`:215-245`, owner line `:241`), `unlock_esp()` `:247`, `detaching()` `:258`, `bootseq()` `:127`, `prune()` `:154`, `cl_nouveau()` `:99`, `find_dgpu()` `:117`, `drv_of()` `:86`, the foreign code of `phase_probe()` (`:479-481`) | Verb `selftest` exactly I 25.3 (refusals incl. `detaching`, ESP lock, `boot=` marker, foreign first, two probes with `timeout 150 ... -T 60`, verdict mapping, record I 25.4, TERM trap -> `interrupted`). |
| T1.9 | `gb_notify.sh` `handle()` (`:42-84`): read loop `:51-62`, tag whitelist `:65`, cases `:63-82` | Read `key=`, `value=`, `src=`; tag `gb_gfx`; `kind=settings` exactly I 12.1 (three fixed pairs; file checks: regular, not a symlink, uid 0, <= 64 KiB, <= 512/128 lines; `valid_pkg` per line; de-dup; join; put or delete). `--include-stopped-packages` is already live (`:72`, REVIEW B6). |
| T1.10 | `test/test_gb_nouveau.sh` (`new_case()` `:159`, `mk_pci()` `:78`, `mk_drm()` `:101`, `mk_tree()` `:112`, `reboot_dev()` `:189`, `run()` `:214`), `test/shims.sh` (`getprop` `:14`, `setprop` `:25`, `cmd` `:236`, `nvk_probe` `:276`) | Cases of 1.2. Shims: `cmd settings put/delete` logged; `nvk_probe` exit code per `-t` mode; `timeout` passing 124; a FIFO `drivers/nouveau/bind` for the hang; a fake `boot_id`; props `ro.build.id`, `ro.build.version.incremental`; `stat -c %u` for the owner check. |
| T1.11 | `test/rc_lint.sh`, `test/run_tests.sh`, `README.md` | Lint the 7-service rc; compare with I 15.1-v12 (`GB_INTERFACES` points at v1.2); README "Graphics modes". |

### 1.2 Acceptance tests (mksh suite on the builder, GNU and toybox passes as in wave 1)

| Id | Test | Pass criterion |
|---|---|---|
| NP-1B-01 | Boot, `gfx_mode` unset, dGPU in the fake tree | insmod nouveau; `mode=auto`; `probation 1/3`, `vendor.gb.dgpu.dmesg=1`; `healthy` then `clean`; renderD 0660; marker line has `boot=<fake id>` |
| NP-1B-02 | `gfx_mode` = `garbage`; `= nvidia` | both load exactly like NP-1B-01 (`mode=auto`) |
| NP-1B-03 | `gfx_mode=intel`: boot, then the rc trigger `booted=1 && gfx_mode=intel` | `result=idle`, no insmod; `off` -> `off-done` with `ctl_auto`; no insmod in any path |
| NP-1B-04 | Legacy `persist.vendor.gb.nouveau=1` with `gfx_mode=intel`; `=0` with `gfx_mode` unset | no load; load (the old property is never read) |
| NP-1B-05 | Crash-loop marker of another boot; a v1.1-format marker (no `boot=`); a marker of the current boot | crash-loop path (marker removed, setprop log `persist.vendor.gb.gfx_mode=intel`, never `persist.vendor.gb.nouveau`, `clean_boots=0`, N01 exact v1.2 text, no insmod, `booted` unset); same; no crash-loop, the run waits for the ESP lock |
| NP-1B-06 | Unhealthy (`d3cold`, `dmesg`) and killed insmod | `keep` and `noload` created before the `gfx_mode=intel` setprop (one ordered log); N02 exact v1.2 text; the following `off` writes nothing to sysfs |
| NP-1B-07 | Hot attach script side: `auto` with no dGPU, then the dGPU added and `auto` again | `refused:nodgpu` + `booted=1`; second run loads |
| NP-1B-08 | Bind path: `/sys/module/nouveau` present, dGPU driverless, mode auto | marker `mode=bind` synced before the bind write; the write is the BDF into `drivers/nouveau/bind`; ESP lock owner `gb_nouveau`; `healthy` then `clean`; `clean_boots` unchanged; marker removed |
| NP-1B-09 | Bind path with a bind that never returns (FIFO) | `bind-failed` within 21 fake s; `wedged`, `keep`, `noload`; `gfx_mode=intel`; marker kept; N05; no further sysfs write |
| NP-1B-10 | Bind path refusals: renderD128 not i915, zero BAR, ESP lock held by a live pid > 120 s, ESP vfat mount, `detaching` for 15 s, `detaching` appearing after the marker | `refused:i915`, `refused:bar`, `refused:esp`, `refused:esp`, `refused:detach`, `refused:detach` with the marker removed; never a bind write |
| NP-1B-11 | Guard order (REVIEW A2): module loaded + dGPU driverless with `noload`; with `keep`; kill switch; `sys.boot_completed` unset; a marker of another boot | `refused:noload`; `refused:keep`; `disabled`; `refused:boot`; crash-loop path; never a bind write in any case |
| NP-1B-12 | `auto` while `init.svc.gb_nouveau_off=running` for 10 fake s; for 40 s | waits, then binds; `refused:off` after 30 s |
| NP-1B-13 | `vendor.gb.gfxdriver=on`, `gb.gfxdriver=1`, `/dev/gb_dgpu/gfxdriver` all present | renderD 0660; `auto` does not set `foreign` |
| NP-1B-14 | `off` with `gfx_mode=auto`; mode flipping to `auto` during the chmod-000 second | `off-skipped`, no unbind; `off-skipped`, permissions back to 0660 |
| NP-1B-15 | `off` in a `foreign` boot with `gfx_mode=intel`; `off` stops services | `off-unbound`, no rmmod, **no** N04 file; `ctl.stop` log has `gb_gfx_selftest` too |
| NP-1B-16 | rc lint; rc body vs I 15.1-v12 | 7 services, 0 findings; byte-identical |
| NP-1B-17 | `gb_notify.sh` `kind=settings`: `all_apps` 0 and 3; value 1; value `0;reboot`; unknown key; key/src mismatch; opt-in file valid, with bad lines, empty, a symlink, owned by uid 1000 (hook), 513 lines; opt-out file with 129 lines | `cmd settings put global updatable_driver_all_apps 3` (and `0`) in the shim log; the bad ones dropped and logged; list joined in file order without bad lines; empty -> `cmd settings delete global updatable_driver_production_opt_in_apps`; symlink, wrong owner, 513/129 lines dropped |
| NP-1B-18 | `gb_notify.sh` tag `gb_gfx` | posted |
| NP-1B-19 | `selftest` pass (both probes exit 0) | ESP lock taken (owner `gb_dgpu_report`) and released; marker `boot=` before the first probe and removed after; `foreign` set before the first probe (log order); record fields exact (`verdict=pass`, identity, `attempts=1`, `crashes=0`, `report=`); `report/selftest-<N>.txt` 0640 root:shell |
| NP-1B-20 | Verdict mapping: (1,0), (0,124), (0,137), (3,3), (2,0), (0,5) | `fail ahb-i915-readback`, `fail a17-swapchain-model timeout`, same, `error nodevice`, `error icd`, `fail a17-swapchain-model rc 5`; `attempts` +1 per run for one identity, 1 for a new identity |
| NP-1B-21 | `selftest` refusals: not native, `gb.nouveau=test`, nouveau not bound, no `selftest/` dir, `selftest/` a symlink, `detaching` present, no `gfx_build_id`; ESP lock busy 120 s | exit 1 and no files; `verdict=error reason=esp`, attempts unchanged |
| NP-1B-22 | `selftest` TERM during the first probe; `detaching` appearing between the probes | `verdict=error reason=interrupted`, attempts unchanged, marker removed, lock released; same |
| NP-1B-23 | `booted` set once (REVIEW E2): mode auto, base detached, `auto` run twice | `setprop vendor.gb.nouveau.booted` appears once in the setprop log |
| NP-1B-24 | `shellcheck -s sh` on every `.sh` | 0 findings (justified disables only) |
| NP-1B-25 | Wave-1 suite NP-01..NP-30 | all pass, except the assertions listed in section 9 (rewritten, listed in the integration file) |

---

## 2. gb-power (component 2)

### 2.1 Tasks (`gb_power.c@25bddd1f`)

| # | Function (line) | Change (I section) |
|---|---|---|
| T2.1 | new state: `g_gfx` (mode, policy, cap, active, reason, seq, session, attach_event, forced), `g_st` (self-test: want, applied, pending, started_at, record, attempts, tried_this_boot, clean_seen), `g_pend` (kind, deadline, ok_reads), `g_rb` (rebind guard: phase, armed_at, seen_at, locked), `g_nv` (hot attach: deferred, starts, last_appear), `g_ui_notify`; `prop_put()` table `g_pub[24]` (`:307-322`) -> 32 | I 23 |
| T2.2 | `read_cmdline()` (`:3605-3623`) | Also record `g_cmd_test` (last `gb.nouveau=` is `test`) and `g_cmd_once`. |
| T2.3 | new `gfx_mode_get()`, `gfx_policy_get()`, `gfx_cap()` (uses `stack_present()` `:2970-2974`, `prop_get()` `:261`/`:296`, `path_exists()` `:235`), `gfx_identity()` (`uname()` behind a test hook) | I 22.2, 23.1, 25.1 |
| T2.4 | new `gfx_eval()` and `gfx_force_intel()`, called at the end of `status_tick()` (`:2676-2716`, before `publish_state(t)` at `:2711`) and from the command handlers | I 23.2, 23.3 (ordering incl. the `OP_MARK` of `gfx_session` before the node is raised) |
| T2.5 | `node_perms()` (`:1227-1238`), `nodes_apply_new()` (`:1241-1251`, call `:2689`) -> `nodes_enforce()`; delete `gfxdriver_on()` (`:1008`) and its uses (`:1232`, `:2824`) | I 23.5 (stat only; never raise 0000; skip rules on root-only evidence) |
| T2.6 | worker: ops enum (`:1029`) + `OP_MARK`, `OP_UNMARK`; jobs enum (`:1030`) + `J_MARK`; `job_name()` (`:1031-1035`); child loop in `worker_start()` (`:1086-1093`; `op_gate()` `:1062-1069` returns 0 for the new ops); `job_done()` (`:2547-2556`) | `fsync` of file and directory in the child (I 23.3, 23.8) |
| T2.7 | `nq_put()` (`:772-788`, keys `set:*`), `NQ_MAX` (`:732`) -> 16; new `prefs_load()`/`prefs_save()` and `catalog_load()`/`catalog_save()` modelled on `allow_load()` (`:1340-1363`)/`allow_save()` (`:1364-1380`); new `gfx_apps_eval()`, `gfx_settings_eval()`; `pkgs_refresh()` (`:1278-1311`): recompute `apps_sync` and the opt-in list on a change, queue the broadcast | I 24.2-24.4, 23.4, 23.11 |
| T2.8 | replace `off_rebind_check()` (`:2665-2671`, call `:2688`) by `reconcile_off()`; rewrite `hotattach_check()` (`:2646-2659`, call `:2705`); `mode_valid()` (`:2565-2567`) accepts `auto` | I 23.6, 23.7 (deferral, per appearance, 3 per boot) |
| T2.9 | rebind guard: arm in `dtx_set_base()` (detached -> attached, `:1798-1803`), `uev_handle()` (10de `add`, `:3356-3358`), `rescan_submit()` (`:2409-2419`, `OP_MARK` first in `J_RESCAN`); watch in `status_tick()`; `rescan_check()` (`:2475-2486`) and `rescan_job_done()` (`:2443-2474`) unchanged otherwise; new ESP lock helpers (I 5.4 algorithm, as `steal_lock()`/`lock_esp()`/`unlock_esp()` in `gb_nouveau.sh:292-358`, non-blocking); `load_window_open()` (`:972-978`) also true while `rebind_pending`; `set_fault()` (`:2386-2395`) learns `rebind`/`selftest-hang` and posts N06 / N20 / nothing per I 8.2; `dgpu_fault_blocks()` (`:2024`) unchanged | I 23.8 (REVIEW A5, B7) |
| T2.10 | detach: `decision_start()` (`:1941-1955`): `gfx_force_intel("detaching")` before `latch_set()` (`:1948`) and `nodes_lock()` (`:1952`); `g_gpu_users[]` (`:1012`) + `gb_gfx_selftest`; `decision_step()` (`:1922-1940`): 5 s app-holder refusal; `decision_busy()` (`:1900-1918`): `detach_ref`, N08 rule; `dtx_set_base()` (`:1785-1805`): N17, attach event; pending detach in `run_due()` (`:3418-3468`), LATCH_REQUEST as `cmd_detach_request()` (`:3003-3010`) | I 23.9 |
| T2.11 | self-test: new `selftest_check()` in `status_tick()`; `hold_eval()` (`:2489-2507`) second want bit; `hold_job_done()` (`:2508`); `dgpu_pm_pass()` (`held` at `:2310`) and `watchdog()` (`:2625-2642`, exemption at `:2636`) honour the self-test hold; record validation; deadline; interrupted/crash/hang verdicts; N18 | I 25.2-25.7 (REVIEW A3, C2, C3) |
| T2.12 | session marker: transitions (T2.4), first-tick check, `powerctl_check()` (`:1743-1750`) | I 23.10 (REVIEW A1) |
| T2.13 | commands: `cmd_enable()` (`:2975-2992`) / `cmd_disable()` (`:2993-3002`) -> `set-mode` mapping; new `cmd_get_mode`, `cmd_set_mode`, `cmd_set_app_policy`, `cmd_set_app_intel`, `cmd_set_app_nvidia`, `cmd_get_apps`, `cmd_apps_begin/add/commit` (staging in `struct client`, `:2835-2840`), `cmd_run_selftest`, `cmd_list_holders` (`cmd_holders()` `:2959-2969` with the reply `cmd` as a parameter), `cmd_close_holders_detach` (parser of `cmd_close_holders()` `:3021-3061` moved into a helper), `cmd_ui_caps`; `g_cmds[]` (`:3151-3167`) | I 26 (REVIEW B5) |
| T2.14 | `status_json()` (`:2778-2828`): new fields after `gfxdriver` (`:2824`), `persist` (`:2795`) and `probation` (`:2799`) redefined, holder reserve 900 -> 1200 (`:2791`); `publish_state()` (`:2601-2621`) broadcast triggers | I 9.5, 10.7 |
| T2.15 | tests: `test/test_gb_power.c` (helpers `fake_pci()` `:744`, `bind_nouveau()` `:731`, `pkgs()` `:814`, `proc_holder()` `:822`, `cl_pair()` `:841`, `req()` `:861`, `mock_ioctl()` `:528`, `mock_op()` `:630`, `pump()` `:703`; new test hooks for `uname()`, file uid and `boot_id`), `test/test_socket.py` (`make_tree()` `:45`, `main()` `:138`) | section 2.2 |
| T2.16 | `README.md` (component), integration `gb-power.md` "v1.2" section | |

### 2.2 Acceptance tests (host build plain and ASan/UBSan; NDK static build `-Werror`)

| Id | Test | Pass criterion |
|---|---|---|
| GP-1B-01 | Derivation matrix: mode {unset, auto, intel, nvidia, garbage} x base {attached, detached, not-feasible} x dGPU {absent, driverless, bound idle, bound busy, loading, rebind watch} x record {none, pass, fail, error, stale build, stale krel, stale gfx} x special {none, kill, test boot, keep, noload, crashloop, wedged, fault, detaching, pending detach, no DTX, no package} | `vendor.gb.gfx_active`/`gfx_reason` equal an independent oracle written as data from the I 23.2 table, for every combination |
| GP-1B-02 | Transition order (one ordered event log) | intel -> nvidia: foreign property + file, `OP_MARK gfx_session` result, chmod 0666, `gfx_active` setprop; nvidia -> intel: setprop first, chmod 0660, `OP_UNMARK`, `gfx_unclean=0` |
| GP-1B-03 | Spoofing | `persist.vendor.gb.gfx_ok=1` without a record -> `untested`, node 0660; an external `vendor.gb.gfx_active=nvidia` is overwritten and the node stays 0660; a record that is a symlink, not root-owned (hook), > 2 KiB or missing a key is invalid; spoofed `init.svc.gb_nouveau_off=running` does not stop the node enforcement |
| GP-1B-04 | Settings push | nothing before `sys.boot_completed=1`; once per boot after it (three messages, exact `.msg` content); on every change of the desired value; newest-wins coalescing; `all_apps=3` in kill-switch, test, recovered and Intel boots; never with `cap` 0 or no DTX |
| GP-1B-05 | App choices | `set-app-intel`/`set-app-nvidia` add, replace each other, clear; file sorted, unique, 0644, atomic (`O_EXCL`); 129th choice -> `EBUSY`; adding an unknown package -> `ENOENT`; clearing an uninstalled one works; bad syntax -> `EINVAL`; the seed is used only when the file is absent |
| GP-1B-06 | Catalog protocol | `apps-begin 3`, two `apps-add` lines, `apps-commit 3` -> catalog with header stamp; `apps-commit` without begin -> `EINVAL no apps-begin`; count mismatch -> `EINVAL`; > 512 -> `EINVAL`; unknown and duplicate packages counted as `dropped`; staging discarded on close; `apps_sync` 0 after a packages.list change and 1 after the commit |
| GP-1B-07 | Opt-in computation (oracle = the I 24.4 formula) | policy `games`: games + nvidia choices - intel choices; policy `all`: all catalog + nvidia - intel; uninstalled packages excluded; over 512 / 64 KiB: class `a` dropped first; `gfx_optin`/`gfx_optout` rewritten only on change |
| GP-1B-08 | Node enforcement | a node drifted to 0660 while `nvidia` is back at 0666 within one fast tick; a 0666 node while `intel` is lowered; a 0000 node is never raised; untouched during a decision, after a wedge, while a live `gb_nouveau` owns the ESP lock, within 30 s of gb_power's own `gb_nouveau_off` start |
| GP-1B-09 | `reconcile_off()` | intel mode, bound, refcnt 0 on two ticks -> exactly one `ctl.start gb_nouveau_off`; none with `keep`, in once/test boots, during a rebind guard, while off or auto runs, within 10 s of the last start; none in auto mode after a bind (REVIEW B4); the write log never shows an unbind by gb_power |
| GP-1B-10 | Hot attach | auto, module absent, dGPU appears driverless -> one `ctl.start gb_nouveau` per appearance, at most 3 per boot; none in intel mode, with kill/keep/noload/wedged/fault, or during the rebind watch; while `init.svc.gb_nouveau=running` deferred (not counted) and issued on `stopped`; `set-mode auto` while running records a deferred start |
| GP-1B-11 | Rebind guard | arming on DTX attach, on a 10de `add` uevent, and as the first op of `J_RESCAN`; ESP lock owner `<pid> gb_power <uptime>`; a held lock -> logged, guard continues; marker line exact; healthy (bound + suspended + D3cold within 30 s) -> lock released, nvidia allowed, marker until +120 s then unlinked, `clean_seen`; failure -> unmark, `keep` + `noload`, fault `rebind` (detach still allowed), `gfx_mode=intel`, N02 `rebind`; no dGPU in 60 s -> unmark, no fault; detach during the watch -> N15 |
| GP-1B-12 | Detach integration | `gfx_active=intel` setprop logged before the LATCH_LOCK of the decision and before `nodes_lock()`'s chmod 000; `ctl.stop gb_gfx_selftest` at decision start; app holders only -> refused after 5 s; a root holder (`nvk_probe`) -> refused after 30 s; refused -> `nvidia` again after the decision; N17 after a confirmed detach in auto/nvidia; N16 after reattach + `nvidia` |
| GP-1B-13 | N08 rule | GbGpu absent -> N08 exact text; GbGpu present without `ui-caps notify` -> N08; with it -> no N08, `detach_ref` +1, broadcast queued; `ui-caps none` restores N08 |
| GP-1B-14 | `close-holders-and-detach` | force-stop messages for closable holders; `pending=detach`; refcnt 0 on two reads 500 ms apart -> `SDTX_IOCTL_LATCH_REQUEST` in the mock DTX log; refcnt > 0 at 15 s -> pending cleared, `detach_ref` +1; `EBUSY` during a decision or with a pending detach; `ENODEV` without DTX or with the base detached |
| GP-1B-15 | Self-test scheduling | not before `clean_seen`; `ctl.start gb_gfx_selftest` + hold job (`power/control=on` through the worker); 90 s idle D0 during the test gives no `d0-idle` fault; hold released on stop; record -> `gfx_ok`; `error` retried once per boot and at most 3 times per identity; `run-selftest` bypasses the cap and a stored `fail`; never in a test boot, during a decision, a pending detach, a rebind watch or in Intel mode |
| GP-1B-16 | Identity | changing `ro.build.id`, `ro.build.version.incremental`, `uname -r` (hook) or `gfx_build_id` makes the record stale -> `untested` -> eligible |
| GP-1B-17 | Interruptions and crashes (REVIEW A3) | gb_power stops the test (decision, `set-mode intel`) and the script was killed hard -> `verdict=error reason=interrupted` after 10 s, attempts unchanged; a marker of another boot at the first tick -> `error crash` (crashes 1), a second time -> `fail crash` + one N18; a marker of the current boot -> `interrupted` |
| GP-1B-18 | Deadline (REVIEW C3) | service still `running` 360 s after the start -> `ctl.stop`; still running 10 s later -> fault `selftest-hang`, N20, hold released, `verdict=fail reason=hang` |
| GP-1B-19 | Session marker (REVIEW A1) | `gfx_session` created (worker, fsync) before the node is raised and removed on nvidia -> intel; a session of another boot at start -> `gfx_unclean` 1; twice in a row -> `gfx_mode=intel`, N19, reason `crashloop`, counter 0; `sys.powerctl` set -> file removed, counter 0; a session of the current boot is adopted |
| GP-1B-20 | Socket | I 26.3 goldens byte-exact (fixed key order); I 9.6 goldens byte-identical; every new command x every role (`EPERM` for compute-svc/compute-client/other); `set-mode` effect matrix (I 26.2: booted unset, once, test, kill, wedged, noload, keep -> `next-boot`; detached -> `next-attach`; busy intel -> `pending` with `n`; else `now`); `enable`/`disable` write `persist.vendor.gb.gfx_mode` (`auto`/`intel`), never `persist.vendor.gb.nouveau`, and map `next-attach`/`pending` to `now`; `get-apps` paging with 128 choices of 127-character names stays <= 4096 bytes per reply; `list-holders` = `holders` except `cmd` |
| GP-1B-21 | Status object | new fields in the I 9.5 order; a reply with 48 holders stays <= 4096 bytes |
| GP-1B-22 | Broadcast | queued on changes of `gfx_mode`, `gfx_policy`, `gfx_active`, `gfx_reason`, `gfx_test`, `pending`, `detach_ref`, `apps_sync`, `persist`; on a packages.list change; at most one per 2 s |
| GP-1B-23 | VMware model (`-D`, no DTX, native tree) | `gfx_reason=nodtx`, `get-mode` works, no settings message in 20 fake minutes |
| GP-1B-24 | Sanitizers and build | ASan/UBSan 0 reports; `-Wall -Wextra -Werror` clean; NDK static stripped binary reproducible (two builds, same sha256) |
| GP-1B-25 | Wave-1 tests GP-01..GP-32 | pass, except the assertions of section 9 (rewritten) |

---

## 3. gfx-driver-pkg (component 4)

### 3.1 Tasks (`gb_shim.c@ef329edd` and the APK tooling)

| # | File / function (line) | Change (I 14, 13.3, 24.5) |
|---|---|---|
| T3.1 | `struct gb_instance` (`:286-304`) | add `bool want_nvk` next to `nvk_allowed` (`:292`) |
| T3.2 | `gb_CreateInstance()` (`:571-618`) | after the base `CreateInstance` (`:584`): `want_nvk` = `gb_get_prop("vendor.gb.gfx_active")` (`gb_get_prop()` `:77`) is exactly `nvidia` and `gb_hwui_vulkan()`; framework rule (`:591-615`) unchanged |
| T3.3 | new `gb_hwui_vulkan()` (process-wide, `pthread_once` like `gb_base_load_once()` `:212`) | ANGLE-HWUI guard from `debug.hwui.renderer` / `ro.hwui.use_vulkan`; log once |
| T3.4 | `gb_nvk_enumerate()` (`:459-528`) | the `vendor.gb.gfxdriver` check (`:462-465`) becomes `if (!si->want_nvk) return;`; `gb_nouveau_ready()` (`:411-456`) stays |
| T3.5 | `gb_enumerate_locked()` (`:530-546`), NVK append (`:523-526`), `gb_EnumeratePhysicalDevices()` (`:648-666`), `gb_EnumeratePhysicalDeviceGroups()` (`:668-716`, NVK loop `:707-712`, base groups `:691-706`) | `si->pds` ordered NVK first, platform after; groups NVK first (respecting `*count` and `VK_INCOMPLETE`); the registry stays as is |
| T3.6 | header comment (`:1-26`) | v1.2 gate and order |
| T3.7 | `apk\AndroidManifest.xml.in` (label `:17`, comment `:2-6`); `apk\build_apk.sh` (`:42-44`); `apk\apk_tool.py` (`REQUIRED` `:16`); `apk\check_apk.sh` | label `NVIDIA app graphics`; the build never creates `assets/allowlist.txt`; `apk_tool.py` gains a `FORBIDDEN` list with it; `check_apk.sh` asserts it is absent |
| T3.8 | `gfxdriver.json` and `out\gfxdriver.json` | exactly section 3.3 |
| T3.9 | `gb_gfxdriver.sh`, `gb_gfxdriver.rc`, `test\test_gb_gfxdriver.sh` | not shipped: removed from `out\` (sources kept under `superseded\` with a README line) |
| T3.10 | `test\test_shim.c` (`fake_tree()` `:211`, `platform_mode()` `:282`, `main()` `:304`; props through `GB_SHIM_PROPS`), `test\fake_icd.c`, `test\run_chroot_tests.sh` | cases of 3.2 |
| T3.11 | `README.md`, integration `nvk-gfxdriver.md` "v1.2" section | modes, order, guard, retirement |

### 3.2 Acceptance tests (host glibc build with test hooks, plus the bionic 276.R4 chroot)

| Id | Test | Pass criterion |
|---|---|---|
| GD-1B-01 | `gfx_active=nvidia`, fake NVK (0x10de, discrete) + lavapipe as the platform driver | `vkEnumeratePhysicalDevices` returns NVK first, platform second; count query; `VK_INCOMPLETE` with capacity 1 returns the NVK device; groups NVK first |
| GD-1B-02 | `intel`, unset, `NVIDIA`, `nvidia ` (trailing space), 91-character garbage | platform devices only; `libgb_nvk.so` never mapped |
| GD-1B-03 | `nvidia` with engine `android framework`, with app name `android framework`, with NULL `pApplicationInfo` | platform only; NVK never loaded |
| GD-1B-04 | Snapshot | instance A created with `nvidia`, property then `intel`, A enumerated -> NVK first; instance B created after -> platform only; and the reverse |
| GD-1B-05 | ANGLE-HWUI guard | `debug.hwui.renderer=skiagl` -> platform only for an `ANGLE` engine instance; unset + `ro.hwui.use_vulkan=false` -> platform only; unset + `true` -> NVK first; `skiavkthreaded` -> NVK first |
| GD-1B-06 | NVK failures with `nvidia`: library missing, `vkCreateInstance` fails, 0 devices, no `/sys/bus/pci/drivers/nouveau/0000:*`, render node not openable (0660 as a non-root uid) | platform devices only, `VK_SUCCESS`, no fd left open |
| GD-1B-07 | `vendor.gb.gfxdriver=on` with `intel`; `off` with `nvidia` | platform only; NVK first (the old property is ignored) |
| GD-1B-08 | `vkCreateDevice` on device 0 (NVK) and on the platform device; destroy; last instance destroyed | both through the trampolines; NVK unloaded after the last instance (as wave 1) |
| GD-1B-09 | Bionic chroot runs 1 and 2 (`nvk-gfxdriver.md` GD-01) with the v1.2 properties | real `libgb_nvk.so` on drm-shim TU117 listed first; HWUI engine gets the platform HAL only; fd count unchanged |
| GD-1B-10 | ASan/UBSan host run | 0 reports |
| GD-1B-11 | APK | **no** `assets/allowlist.txt`; `sphal_libraries.txt` present; `zipalign -c -P 16 4`; `apksigner verify` v2+v3; badging `gb.sb3.gfxdriver`, min/target 36, label `NVIDIA app graphics` |
| GD-1B-12 | Manifest and reproducibility | `gfxdriver.json` equals 3.3 byte for byte; two shim builds identical; `out\` has no `gb_gfxdriver.*` |
| GD-1B-13 | Wave-1 GD-01..GD-03 | pass, except the gfxdriver-gate assertions rewritten for `gfx_active` (section 9); GD-04 retired with `gb_gfxdriver.sh` |

### 3.3 New `gfxdriver.json` (Wave 2 copies it into `inject\native\components\`, `integration\gfx-switching.md` W2G-01)

```json
{
 "name": "gfxdriver",
 "source": "C:\\GB-build\\native\\gfx-driver-pkg",
 "files": [
  {"src": "C:\\GB-build\\native\\gfx-driver-pkg\\out\\GbGfxDriver.apk", "dest": "app/GbGfxDriver/GbGfxDriver.apk", "mode": "0644"}
 ],
 "rc_files": [],
 "rc_text": "",
 "build_prop": ["ro.gfx.driver.0=gb.sb3.gfxdriver"]
}
```

---

## 4. gpu-ui (component 6)

### 4.1 Tasks

| # | File / function (line) | Change (I 27, 13.2) |
|---|---|---|
| T4.1 | `core/Status.java`: fields (`:13-36`), constructor (`:38-68`), `sameAs()` (`:84-93`) | the v1.2 status fields (I 9.5) |
| T4.2 | `core/GbPowerClient.java`: `NO_ARG` (`:92`), `WITH_ARG` (`:95`), `validRequest()` (`:133-139`), `exchange()` (`:184-232`) | the new commands and argument patterns (mode, policy, `<pkg>:0` / `<pkg>:1`, `g:<pkgs>` / `a:<pkgs>` <= 255 bytes, counts); new `exchangeAll(List<String>)` (one `hello`, then one reply per line) for the catalog push |
| T4.3 | `core/StateMapper.java`: `gpuTile()` (`:79-104`), `switchChecked()` (`:122-124`), `summary()` (`:127-129`), `nextBoot()` (`:132-134`) | new `graphicsTile(Status)` (I 27.1), `nextMode()`, `effectToast()`, `summary()` = `<Mode label>: <subtitle>`; `switchChecked()` removed; `gpuTile()` kept only if the Detach tile or tests still need it |
| T4.4 | `core/Texts.java` (`:12-61`, `error()` `:64-85`) | `TILE_LABEL = "Graphics"`; every exact string of I 27 and 12.3; error texts for the new commands |
| T4.5 | new pure-Java models in `core/`: `GraphicsPage`, `AppRows` (the I 24.4 formula, mutual exclusion of the two switches), `CatalogBuilder` (24.3 filter, classes, <= 255-byte `apps-add` lines, games first), `NotifModel` | host-testable logic |
| T4.6 | `GpuTileService.java`: `render()` (`:86-96`), `onClick()` (`:99-123`), `act()` (`:125-145`), `toggle()` (`:146-155`) | `graphicsTile()`; tap = `set-mode <next>`; toasts; never a dialog |
| T4.7 | `SettingsActivity.java`: `onCreate()` (`:98-183`), `setPersist()` (`:199-212`) and `bindToggle()` (`:214`) removed, `render()` (`:224-260`), `loadAllowList()` (`:262`) kept | the Graphics page (I 27.2) |
| T4.8 | `HoldersActivity.java`: `EXTRA_THEN_DISABLE` (`:30`, read `:39`) -> `EXTRA_MODE`; `show()` (`:67-129`); `disableNow()` (`:149-157`) removed; `closeApps()` (`:160`) | modes `close`, `intel`, `detach` (I 27.4) |
| T4.9 | `Notifier.java`: `ensureChannels()` (`:28-37`), `update()` (`:40-67`); `StateReceiver.java` `onReceive()` (`:19-35`); new `ActionReceiver.java` (`exported=false`) | in-use text + **Close apps**; channel `gb_gpu_detach`, id 3 on a growing `detach_ref` (last value in SharedPreferences), cancel rules; `ui-caps`; catalog push when `apps_sync` = 0 |
| T4.10 | `SwitchProvider.java`: constants (`:32-40`), `call()` (`:71-113`) | only `getDynamicSummary`/`getDynamicTitle`; key `graphics` |
| T4.11 | `AndroidManifest.xml`: permissions (`:16`), tile (`:28-43`), SettingsActivity meta (`:72-89`: title, keyhint, `switch_uri` at `:84-86` removed), receivers (`:108-115`); `res/values/strings.xml` (`:4-7`) | `QUERY_ALL_PACKAGES`; labels `Graphics`; `.ActionReceiver`; category `ia.system` kept (verified, evidence E2) |
| T4.12 | `gpu_ui.json`; new empty file `gpu-ui\gfx_app_prefs.default` | add `{"src": "C:\\GB-build\\native\\gpu-ui\\gfx_app_prefs.default", "dest": "etc/gb/gfx_app_prefs.default", "mode": "0644"}` |
| T4.13 | `test/mock_gb_power.py` (`status_obj()` `:109-124`, `line()` `:271`, `cmd_*` `:303-475`), `test/test_mock.py`, `test/java/.../UnitTests.java`, `ProtocolTests.java`, `test/check_apk.py` (`main()` `:136`), `run_tests.sh` | new commands and status fields, I 26.3 goldens, manifest asserts |

### 4.2 Acceptance tests (host JVM, mock over UNIX sockets, SDK tools on the builder)

| Id | Test | Pass criterion |
|---|---|---|
| UI-1B-01 | `graphicsTile()` | every row of I 27.1 including the nvidia-mode variants, busy N 1..999 and 14 unknown values -> exact `Tile.state` and subtitle |
| UI-1B-02 | `nextMode()` | auto -> intel -> nvidia -> auto; unset/unknown counts as auto |
| UI-1B-03 | Tap mapping | rows 3-17 send `set-mode <next>`; rows 1-2 send nothing; `next-boot`/`next-attach`/`pending` -> the exact toasts; `now` -> none |
| UI-1B-04 | Protocol vs mock | I 26.3 goldens byte-exact; `EPERM`/`ENODEV`/`EINVAL`/`EBUSY`/`EAGAIN` mapped to texts; `set-app-*` argument format; `get-apps` paging; `list-holders` parsing |
| UI-1B-05 | `GraphicsPage` | radio = `gfx_mode`; status text per `gfx_reason` (I 27.6, every reason); self-test line per `gfx_test`/`gfx_test_t`/`gfx_ok`; `Run self-test again` enabled exactly per I 27.2; `pending` line and `Close apps` button; all controls disabled without a socket |
| UI-1B-06 | `AppRows` | effective graphics equals an oracle of the I 24.4 formula for both policies; turning `Use NVIDIA` on turns `Always Intel` off and vice versa; holder hints; a failed `set-app-*` reverts the switch |
| UI-1B-07 | `CatalogBuilder` | `FLAG_SYSTEM` apps excluded (stock and updated), `gb.sb3.*` excluded, `CATEGORY_GAME` or `FLAG_IS_GAME` -> `g`, others `a`; every `apps-add` line <= 255 bytes; games first; at most 512; `apps-begin`/`apps-commit` counts match |
| UI-1B-08 | `NotifModel` | in-use text with at most 5 labels then `and N more`; detach notification only when `detach_ref` exceeds the stored value; cancelled when the base is not attached or not busy; action targets (HoldersActivity `detach`, ActionReceiver); `ui-caps notify` only when notifications are enabled and the channel is not blocked |
| UI-1B-09 | HoldersActivity logic | command per mode; exact texts; detach mode progress `Preparing to detach...` -> closes on `dtx=confirmed`, failure text on a new `detach_ref` |
| UI-1B-10 | Manifest / APK (`check_apk.py`, `aapt2 dump badging`, `apksigner`) | `QUERY_ALL_PACKAGES` and `POST_NOTIFICATIONS` only (no privileged permission); IA_SETTINGS meta exact (category `com.android.settings.category.ia.system`, title `Graphics`, keyhint `graphics`, `summary_uri`), **no** `switch_uri`; `ActionReceiver` not exported; tile label `Graphics`; v2+v3 |
| UI-1B-11 | No socket | tile `Unavailable` within 2 s; page `Unavailable` with every control disabled |
| UI-1B-12 | Mock cross-check | the wave-1 34-step session plus the v1.2 commands give identical replies from the mock and the real gb_power host build of section 2 (`t_ms` removed) |
| UI-1B-13 | Build hygiene | `javac -Xlint:all -Werror` clean; Android Lint `No issues found`; UI-01..UI-06 pass with the rewritten assertions of section 9 |

---

## 5. VMware no-regression checks (D8)

The VM image carries no dGPU stack (PLAN inv. 19); v1.2 must not change that or the VM's graphics.

| Id | Needs approval | Check | Pass criterion |
|---|---|---|---|
| VM-1B-01 | no (offline) | unpack the VM (gbstage) cpio of a 1b-era build | no `GbGfxDriver`, `gb_gfx_selftest`, `gfx_build_id`, `gfx_app_prefs.default`, `ro.gfx.driver.` |
| VM-1B-02 | no (read-only adb, done 2026-10-25) | `getprop ro.gfx.driver.0`; `settings get global` for `updatable_driver_all_apps`, `..._production_opt_in_apps`, `..._production_opt_out_apps`, `..._production_allowlist`, `..._production_denylist`; `getprop \| grep -c 'gb\.gfx'`; `dumpsys gfxinfo com.android.systemui` Pipeline; `pm list packages -f gb.sb3` | **baseline recorded** (evidence E4): empty; all `null`; 0; `Skia (OpenGL)`; none. After any 1b-era change: the same values |
| VM-1B-03 | yes (throwaway `--vm-test` image, never the user's VM) | VM-1B-02 on the rebuilt image; boot to launcher; `logcat -b crash -d` | same values; launcher reached; no new crash entries |
| VM-1B-04 | yes (native-smoke image in a throwaway VM) | gblog `hello`/`status` (bootlog) after 10 min | `gfx_reason` `nodtx`; no settings message ever queued (the keys stay `null`); `gb_nouveau` `refused:i915`; `gb_gfx_selftest` never started. Parts that need `sys.boot_completed` in VMware are HW if the smoke image does not reach it |
| VM-1B-05 | yes (`adb install` GbGpu on a throwaway VM) | UI-07 extended | tile label `Graphics`, subtitle `Unavailable`, taps do nothing; page `Unavailable` with disabled controls; clean uninstall |
| VM-1B-06 | yes | wave-1 VM tests NP-25, GP-24, GC-11 | pass unchanged |

Settings injection category: verified read-only on this VM (`ia.system` exists and is used by lapis's own
`DiagnosticsPrebuilt`; evidence E2), so no launcher-shortcut fallback is planned (D7).

---

## 6. Wave 2 items (shared files; not done in 1b)

All in `C:\GB-build\native\dgpu\integration\gfx-switching.md` (W2G-01 .. W2G-10): `gfxdriver.json` as a normal
native component with the `ro.gfx.driver.0` build_prop; build_esp writes `/odm/etc/gb/gfx_build_id`, no gfxtest
entry, the HWUI asserts; `gpu_ui.json` with the prefs seed; verify_image checks; bootlog `selftest-*.txt` and
`selftest/result`; gb_slotsync only `-nvtest`; docs; SELinux note; the Wave 2 plan corrections of REVIEW D1-D6.

---

## 7. Hardware checks (user; REQ:84-89 plus the 1b-specific ones)

| Id | Check |
|---|---|
| H-1B-01 | Boot with the base (first boot after install): `selftest-<N>.txt` in gblog with both modes PASS; N16 after the pass; boot time and MemAvailable against an Intel-only boot (I 20 (b)). |
| H-1B-02 | `getprop debug.hwui.renderer` empty, `ro.hwui.use_vulkan` true; `dumpsys gfxinfo <app>` shows the Vulkan pipeline; HWUI stays on Intel while the app renders on NVIDIA. |
| H-1B-03 | vkcube and an ANGLE GLES game render on NVIDIA (`vendor.gb.dgpu.state` `busy:N`, logcat `gb_gfxdriver: NVK: ... first`), system UI smooth; an ANGLE-on-NVK app works (the self-test does not cover ANGLE). |
| H-1B-04 | `settings get global updatable_driver_all_apps` is `0` with NVIDIA allowed and `3` in Intel mode; `..._opt_in_apps` lists the installed games; an Always-Intel app restarted holds no nouveau fd; a non-game app is on ANV directly (no shim in `/proc/<pid>/maps`). |
| H-1B-05 | Detach idle -> N12, N17, tile `Unavailable (base detached)`; reattach -> rebind guard log (order of the DTX attach event and the PCI add, marker before the probe), N16 and NVIDIA again; time from attach to `nvidia`. |
| H-1B-06 | Detach with an NVIDIA app open -> refused after about 5 s, `Can't detach yet` with actions; **Close apps and detach** ends with the latch open. Chrome/WebView-based apps with `Use NVIDIA` (AHB, I 20 (a)). |
| H-1B-07 | Tile cycle Auto -> Intel -> NVIDIA; Intel only: nouveau unbound/unloaded, `02:00.0` and `00:1d.4` in D3cold, new apps on Intel; an Intel switch while busy completes after the app closes. |
| H-1B-08 | Kill switch boot (`gb.nouveau=0`): tile `Disabled for this boot`; `all_apps=3`; nothing loads. |
| H-1B-09 | PLAN 8a kernel fence gate: stall an NVK job for 30 s; SurfaceFlinger pacing and input unaffected. |
| H-1B-10 | Suspend/resume with an NVIDIA app open; reboot and power-off with apps on NVK (no unclean-session count after a clean power-off); detach after resume. |
| H-1B-11 | After a kernel or OTA update the self-test runs again automatically. |
| H-1B-12 | An app with an old targetSdk (`untrusted_app_29` etc.) with `Use NVIDIA`: does the shim read `vendor.gb.gfx_active` and open the node, or stay on ANV (I 17)? (r2: those domains are permissive in `odm_sepolicy.cil`, R-14: a plain functional check, expected to reach NVK) |
| H-1B-13 | Zygote path: repeated start/stop of opted-in apps; an app process that preloads and unloads the driver namespace (PLAN GD-05). |
| UI-1B-HW-1 | Settings > System shows "Graphics" with the dynamic summary; Settings search for "Graphics" finds it; tile long-press opens it. |

---

## 8. Wave-1 defaults that change in v1.2

| # | Wave-1 default (I v1.1 section) | v1.2 | Changed by |
|---|---|---|---|
| C1 | nouveau opt-in only; unset = no driver (4, PLAN inv. 6, PLAN 10) | unset mode = `auto`: nouveau loads after `sys.boot_completed` whenever the base is attached (D1, D2) | 1 |
| C2 | `persist.vendor.gb.nouveau` user knob and its rc triggers (4, 15.1) | retired; `persist.vendor.gb.gfx_mode` + three triggers | 1, 2, 6 |
| C3 | `ro.gfx.driver.0` only in `gb.gfxdriver=1` boots via `gb_gfxdriver.sh` (4, 15.6, D25) | always in the native build.prop; `gb_gfxdriver.sh/.rc`, `gb.gfxdriver=1`, the gfxtest entry retired | 4, Wave 2 |
| C4 | GbGfxDriver only with `GB_GFXDRIVER=1` | normal native component | Wave 2 |
| C5 | per-app opt-in by hand (`updatable_driver_production_opt_in_apps`) | gb_power-computed opt-in (games + Use NVIDIA, or all) + Always-Intel opt-out + `all_apps` 0/3; APK without allowlist (I 24) | 2, 1 (gb_notify), 4, 6 |
| C6 | shim: platform device 0 always, gate `vendor.gb.gfxdriver` (14) | NVK first when `gfx_active=nvidia`; ANGLE-HWUI guard | 4 |
| C7 | renderD 0666 in gfxdriver boots (7.5) | 0666 only while gb_power's own derivation is `nvidia` | 1, 2 |
| C8 | gb_power starts `gb_nouveau_off` only after a re-bind with the `off` flag; hot attach only after `refused:nodgpu` (7.9, 10.1) | `reconcile_off()`; generalised, deferred hot attach | 2 |
| C9 | `auto` with the module loaded is a no-op (7.2 step 3) | bind path | 1 |
| C10 | N08 always daemon-posted (12.2, D24) | app notification with actions when GbGpu declared `ui-caps notify`; N08 otherwise | 2, 6 |
| C11 | tile "NVIDIA GPU" on/off and an injected switch (8.3, 13.2) | tile "Graphics" with the mode cycle; page without a switch | 6 |
| C12 | `foreign` only after probe/test/gfxdriver boots | also before the first `gfx_active=nvidia` and before the self-test | 1, 2 |
| C13 | crash-loop/unhealthy set `persist.vendor.gb.nouveau=0` (7.2, 7.7) | set `gfx_mode=intel` | 1 |
| C14 | `kernel.panic=30` only on opt-in boots (7.6) | on every boot that loads nouveau (approved, D2) | none (consequence of C1) |
| C15 | rescan with nouveau loaded rebinds unguarded (10.4) | rebind guard: marker, ESP lock, watch, fault `rebind` | 2 |
| C16 | ESP lock holders (5.4) | plus gb_power (rebind guard) and `gb_gfx_selftest` | 1, 2 |
| C17 | `vendor.gb.nouveau.mode` values (4) | plus `auto`; `persist` no longer produced | 1, 2 |
| C18 | broadcast only on state enum / `n` changes (10.7) | also on the gfx fields and package changes | 2 |
| C19 | probation for persist loads (D2) | probation for `auto` boots; binds do not count | 1 |
| C20 | status `persist` = `persist.vendor.gb.nouveau`, `gfxdriver` = P8 gate (9.5) | `persist` = mode not intel; `gfxdriver` = `gfx_cap` | 2, 6 |
| C21 | N04 after an `off` in a foreign boot | not posted (off runs only in Intel mode) | 1 |
| C22 | N06 for every fault | N06 only for blocking faults; N20 for the others | 2 |

---

## 9. Wave-1 test assertions that v1.2 rewrites

| Component | Test | Old assertion | New assertion |
|---|---|---|---|
| nouveau-policy | NP-03, NP-26 | crash-loop sets `persist.vendor.gb.nouveau=0` | sets `persist.vendor.gb.gfx_mode=intel` |
| nouveau-policy | NP-04, NP-19 | `MODE=persist`, probation for persist | `MODE=auto` (gfx_mode unset), probation for auto |
| nouveau-policy | NP-12, NP-29 | unhealthy/killed insmod set persist 0 | set `gfx_mode=intel` |
| nouveau-policy | NP-13 (`-gfx`, `-spoof`, `-flagonly`), NP-27 | 0666 with property + flag | 0660 in every case; `foreign` not set by `auto` |
| nouveau-policy | NP-16, NP-30 | N04 queued on `off-unbound` | no N04 |
| nouveau-policy | NP-18 | `auto` with the module loaded = perms only | bound: perms only; driverless: bind path |
| nouveau-policy | NP-24 | rc = v1.1 15.1 | rc = v1.2 15.1-v12 (7 services) |
| nouveau-policy | NP-20 | tags `gb_dgpu`, `gb_dtx`, `gb_dgpu_test` | plus `gb_gfx`; `kind=settings` |
| gb-power | GP-17 | `enable`/`disable` write `persist.vendor.gb.nouveau` | write `persist.vendor.gb.gfx_mode`; replies unchanged on the 9.6 goldens |
| gb-power | GP-30 | `ctl.start gb_nouveau_off` after a re-bind with the `off` flag and persist != 1 | `reconcile_off()` rules (GP-1B-09) |
| gb-power | GP-32 (gfxdriver gate part) | 0666 with property + flag | 0660 unless `gfx_active=nvidia` |
| gb-power | GP-16 | (unchanged derivation of `vendor.gb.dgpu.state`) | unchanged |
| gfx-driver-pkg | GD-01, GD-02 | NVK appended after ANV when `vendor.gb.gfxdriver=on` | NVK first when `gfx_active=nvidia`; the old property ignored |
| gfx-driver-pkg | GD-03 | label `NVIDIA app graphics (experimental)` | `NVIDIA app graphics`; no `allowlist.txt` |
| gfx-driver-pkg | GD-04 | `gb_gfxdriver.sh` | retired |
| gpu-ui | UI-02 | `switch_uri`, title `NVIDIA GPU` | no `switch_uri`, title `Graphics`, `QUERY_ALL_PACKAGES` |
| gpu-ui | UI-03 | 8.3 table (`gpuTile()`) | I 27.1 (`graphicsTile()`) |
| gpu-ui | UI-04 | `enable`/`disable` toggles | `set-mode`; the 9.6 goldens unchanged |

---

## 10. PLAN safety invariants in v1.2 (`NVIDIA_PLAN.md:346-398`)

| Inv. | Status | How it is preserved | Tests |
|---|---|---|---|
| 1 Boot and display never depend on the dGPU | unchanged | i915 guard on every load path (insmod, bind); nouveau in no load list; `ro.hardware.vulkan`/`ro.hardware.egl` untouched; nvtest the only test entry | NP-06, NP-1B-10 |
| 2 No system process loads NVK | unchanged in effect | GraphicsEnvironment never gives the package to privileged or non-updated system apps (`a17 GraphicsEnvironment.java:952`); the catalog excludes every `FLAG_SYSTEM` app (updated ones too); the shim never creates NVK for `android framework` and never in a process whose HWUI runs on GL | GD-1B-03, GD-1B-05, UI-1B-07, H-1B-02 |
| 3 nouveau loads only after boot_completed, with i915, without ESP mount, without crash-loop/kill | unchanged, extended | insmod path unchanged; the bind path has the same guards and order (REVIEW A2); a kernel re-probe on reattach happens only with a module that a guarded load of this boot inserted, and is marked by the rebind guard (residual race in I 23.8) | NP-1B-08..11, GP-1B-11 |
| 4 First loads non-persistent | changed by D1/D2 | every load window (insmod, bind, rebind) writes the crash marker first; a crash costs one boot, then `gfx_mode=intel` + N01; probation for the first 3 clean auto boots; crashes during app use are caught by the session marker (two in a row -> Intel) | NP-1B-01, -05, -06, GP-1B-11, GP-1B-19 |
| 5 Only whitelisted module arguments | unchanged | insmod argument assembly unchanged; the bind path loads nothing | NP-08, NP-09 |
| 6 Default state: no driver, D3cold, immediate confirm | changed by D1 | Intel mode is exactly the wave-1 default; in Auto the health check requires D3cold within 30 s and the `d0-idle` watchdog stays; an idle bound detach completes in seconds | NP-1B-03, GP-1B-09, H-1B-07 |
| 7 No LATCH_CONFIRM while bound, refcnt > 0, base disk mounted | unchanged | `close-holders-and-detach` only sends LATCH_REQUEST; the normal decision applies every gate; the early refusal only shortens a refusal | GP-03..GP-12, GP-1B-12, GP-1B-14 |
| 8 gb_power's main process never blocks on dGPU sysfs writes | unchanged | new main-process actions are chmod, setprop, the non-blocking ESP lock and queue files; marker writes with `fsync` and the self-test hold run in the worker; the bind runs in gb_nouveau.sh's background subshell with a deadline | GP-05, GP-18, GP-1B-11 |
| 9 After use: remove before confirm, rescan on attach/cancel | unchanged | detach worker and staged rescan unchanged; the rebind guard wraps the rescan | GP-10, GP-11 |
| 10 Off never unbinds at refcnt > 0; no rmmod after foreign use | unchanged | `reconcile_off()` only starts the refcnt-gated `off`; `foreign` set before the first `nvidia` and before the self-test | NP-14, NP-1B-15, GP-1B-02, GP-1B-09 |
| 11 Payload mounted while loaded; ESP never mounted during load or stress | unchanged, tightened | bind path, rebind guard (when the lock is free) and self-test hold the ESP lock | NP-10, NP-1B-10, NP-1B-19, GP-1B-11 |
| 12 Status polling reads only cached attributes, never opens DRM | unchanged | node checks use `stat()`; the self-test is a separate service | GP-1B-08 |
| 13 Nodes 0660/0600 unless P8 enabled | changed (P8 automatic) | renderD 0666 only while gb_power itself derived `nvidia` (a passed self-test of this exact identity, mode not intel, attached, healthy); 0660 otherwise; 000 during detach, off, after a wedge; card always 0600 | GP-1B-02, -03, -08, NP-1B-13 |
| 14 Per-app library works with the dGPU absent; never fatal | unchanged | shim pass-through whenever `gfx_active` is not `nvidia`; every NVK failure falls back | GD-1B-02, GD-1B-06, H-1B-05 |
| 15 Kernel security configuration unchanged | unchanged | no kernel change in 1b | |
| 16 No proprietary NVIDIA binaries | unchanged | | |
| 17 No `/odm/priv-app`; sockets check the peer UID with a fixed command set | unchanged | both APKs in `/odm/app`; new commands in the fixed table with roles root/shell/ui; gb_notify writes three whitelisted Settings keys | GP-1B-20, NP-1B-17 |
| 18 Compute never root, never TCP | unchanged | no compute change | |
| 19 VM images carry no dGPU stack | unchanged | `gfxdriver.json`, `gpu_ui.json`, the rc changes are native-only; Settings never written on VMware | VM-1B-01..06, GP-1B-23 |
| 20 SELinux: existing permissive domains | unchanged | `gb_gfx_selftest` in `u:r:shell:s0`; settings by `gb_notify` (shell); apps in `untrusted_app` (old-targetSdk domains: H-1B-12) | W2-15 |

---

## 11. Order of work

1. gb-power (section 2) and nouveau-policy (section 1) in parallel: both depend only on I v1.2.
2. gfx-driver-pkg (section 3) in parallel: property-driven, independent of the daemons.
3. gpu-ui (section 4) against `mock_gb_power.py` with the I 26.3 goldens; then UI-1B-12 against the gb-power host
   build of step 1.
4. VM-1B-01 on the first 1b-era build; VM-1B-03..06 only with the user's approval (VM-1B-02 baseline is recorded).
5. Integration sections "v1.2" in the four component files; then Wave 2 (`integration\gfx-switching.md`); then the
   hardware checks (section 7).

---

## 12. Review fix pass (v1.2 r2, INTERFACES 29) - acceptance tests

Code baseline of the pass: `gb_power.c@c1986aad`, `gb_nouveau.sh@0e8db795`, `gb_dgpu_report.sh@7a4b993e`,
`gb_shim.c@c49560ed`, GbGpu APK `87a71359`. Byte copies: `nouveau-kernel\native\v1.2r1\`, `nouveau-kernel\test\v1.2r1\`,
`power\*.v1.2r1`, `gfx-driver-pkg\superseded\v1.2r1\`, `gpu-ui\v1.2r1-sources.tar`, `nvk-android\verify\
selftest_invocation.sh.v1.2r1`, `dgpu\INTERFACES-v1.2r1.md`.

### 12.1 nouveau-policy (mksh suite, GNU and toybox passes; `test_gb_nouveau.sh` block NP-R)

| Id | Check |
|---|---|
| NP-R1-clean | insmod path: SIGTERM with `sys.powerctl` during the clean-mark wait -> exit 0, marker removed, results `loading healthy` (no `clean`), `clean_boots` not counted, lock released; the next boot loads normally (no crash-loop) |
| NP-R1-health | the same inside the 30 s health window (no D3cold yet) -> marker removed, no unhealthy verdict, no Intel switch, wakelock released |
| NP-R1-nopc | SIGTERM without `sys.powerctl` -> exit 1, marker kept, next boot crash-loop (as before) |
| NP-R1-notours | the marker was replaced by another window's line -> kept, exit 1 |
| NP-R1-bind-pending | bind path, the bind write hangs (FIFO) -> marker kept at the shutdown SIGTERM |
| NP-R1-bind-clean | bind path, SIGTERM + `sys.powerctl` in its clean-mark wait -> marker removed, `clean_boots` unchanged, the bind write was made with the wakelock held |
| NP-R2-wait / -other | `off` waits for a live `gb_power` ESP lock owner, then unbinds; any other owner name: no wait |
| NP-R7-health | a 600 s suspend 3 s into the health window, D3cold 6 s after the resume -> `healthy`, `clean` (v1.2 r1: `unhealthy:d3cold`); insmod with wakelock `gb_nouveau` held, released at the verdict |
| NP-R7-clean | a 600 s suspend in the clean-mark wait, crash 40 s after the resume -> the marker is still there, next boot crash-loop (v1.2 r1 removed it at the first poll after the resume) |
| NP-R7-selftest | both probes run with wakelock `gb_gfx_selftest`, released at the end |
| NP-R10-bound / -late | already bound: 0666 kept, 0644 -> 0660; a node re-created at 0666 during the health window is 0660 before the window closes (applied twice) |
| NP-R11-rmmod | `rmmod nouveau` never returns -> `off-wedged`, `wedged`, N05, within 25 fake s |
| NP-R11-probe / -probe-pc | self-test TERM: record first, then waits while `pgrep -x nvk_probe` finds one (3 found -> 4 calls); during a shutdown at most 2 polls |

Rewritten earlier assertion: NP-18 "perms re-applied" now starts from 0644 (R-10: a bound 0660/0666 node is gb_power's).
`fp_tree()` ignores `/sys/power/wake_*` (the wakelock is no dGPU write; NP-1B-19 "no sysfs writes").

### 12.2 gb-power (unit plain + ASan/UBSan; `test_fix_all()` groups; socket test; NDK build)

| Id | Check |
|---|---|
| GP-R1 | `term_handle()` with `sys.powerctl` and no tick in between removes `gfx_session` and `dgpu_session` (counters 0), the next boot counts nothing; without `sys.powerctl` both are kept and adopted; the rebind marker is removed in `RB_CLEAN` and `RB_SEEN` bound, kept in `RB_SEEN` driverless and when the line is not gb_power's; the tick path does the same once |
| GP-R2 | `set-mode intel` (or an off seen running, then auto) during the rebind watch: the guard ends without fault, keep, noload or N02; without either, fault `rebind` as before |
| GP-R3 | `reconcile_on()`: 10 s from the first tick and from the last tick that saw `gb_nouveau_off` running, one start, 10 s gap, 3 per boot; 14 blocking cases; the tile race (set-mode intel, nvidia while off runs) issues the deferred start once off stopped |
| GP-R4 | `dgpu_session`: not while `nouveau.loading` exists, written 0600 by the worker, kept on an Intel switch while bound, removed when unbound (`dgpu_unclean` 0); compute-only boots count, two in a row -> Intel + N21 + reason crashloop; both sessions stale -> N19 only; R-15: no start check before `ro.persistent_properties.ready` |
| GP-R6 | `compute-start` -> `EAGAIN` during the rebind watch, a live gb_nouveau lock owner and state `loading`; `compute-state loaded` applies no hold until D3cold was reached |
| GP-R8 | `rpm-error` and `d0-idle`: keep + noload, `gfx_mode=intel`, N06 r2 text, the same text on a detach press; `rescan` does not set Intel; R-9: a service-only refusal with `ui-caps notify` posts N08 naming `gb_gpu_compute` |
| GP-R12 | the rebind marker of another window survives the guard's end; its own line is removed; a resume clears a pending detach (no LATCH_REQUEST afterwards) |

Rewritten earlier assertions: GP-15 "N06 text" (R-8 text); GP-1B-08 "untouched after a wedge" -> every node 0000,
"untouched while a live gb_nouveau owns the ESP lock" / "within 30 s of gb_power's own off start" -> lowered, not
raised, "enforced again after 30 s" -> raised, then lowered again (R-5). `gfx_props()` sets
`ro.persistent_properties.ready=true` (R-15).

### 12.3 gfx-driver-pkg and nvk-android

| Id | Check |
|---|---|
| GD-R13 | `debug.hwui.renderer=skiavkthreaded` -> platform only; exact `skiavk` -> NVK first (host, ASan, chroot); mutation: the r1 shim fails exactly that case (67/68) |
| GD-R-all | GD-1B-01..13 re-run on the rebuilt shim and APK (host 68/68, ASan 68/68, chroot 69/69 + 18/18, APK checks, negative 8/8, manifest, two identical shim builds) |
| NV-R11 (S10) | `selftest_invocation.sh` S10: the r2 TERM path (`kill $!`, then the `pgrep -x nvk_probe` wait) with the real bionic probe in the 276.R4 chroot: polls needed, nothing left |

### 12.4 gpu-ui

| Id | Check |
|---|---|
| UI-R16 | `HoldersModel.refusalShown()` / `detachFallback()` (unit); the full gpu-ui suite on the rebuilt APK; mock `compute-start` `EAGAIN` rule (R-6) and mock == real gb_power r2 |

### 12.5 Hardware-only additions (user)

* H-R-1: `adb reboot` within 2 min of `sys.boot_completed` with the base attached, and within 2 min of a reattach:
  the next boot loads nouveau (no N01), `gfx_unclean`/`dgpu_unclean` stay 0; logcat/kmsg show `clean shutdown` lines.
* H-R-2: whether init really delivers SIGTERM to gb_power and to the `gb_nouveau` process group at shutdown before its
  SIGKILL (INFERRED from AOSP `reboot.cpp`): `gb_power: shutdown (SIGTERM during shutdown)` in the last kmsg.
* H-R-3: tile Auto -> Intel -> NVIDIA within 2 s with nouveau bound: NVIDIA graphics again within about 20 s.
* H-R-4: reattach, then Intel within 10 s: no fault `rebind`, no N02.
* H-R-5: lid close during the first 30 s after a load with the base attached: no `unhealthy:d3cold`; a suspend during
  the self-test is blocked (`/sys/kernel/debug/wakeup_sources` or `dumpsys suspend_control_internal` shows the
  wakelock).
