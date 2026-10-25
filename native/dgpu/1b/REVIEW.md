# Review: wave 1b specs and the Wave 2 plan, checked against each other, INTERFACES, the plan and the requirement

Date 2026-10-25. Review step of the dGPU workflow. This is the only file this step wrote; no other file was changed.
Builder and adb access were read-only.

## 0. What was reviewed, and against what

| File | sha256 (first 8) | Size, time |
|---|---|---|
| `1b\INTERFACES_1B.md` ("1B") | `5462c51f` | 69,962 B, 11:23:47 |
| `1b\TASKS_1B.md` ("TASKS") | `9763d58b` | 41,353 B, 11:23:34 |
| `1b\WAVE2_PLAN.md` ("W2") | `a2604bc1` | 91,667 B, 11:20:37 |
| `dgpu\INTERFACES.md` ("I1.1", the copy read for this review) | `6aa0efce` | 125,469 B, 1580 lines, 11:28:05. **Still changing while this review ran** (107,768 B at 11:24, 110,598 B at 11:25). |
| scratchpad `nvresearch\NVIDIA_PLAN.md` ("PLAN") | `de08467e` | |
| scratchpad `nvresearch\GRAPHICS_SWITCHING_REQ.md` ("REQ") | `b48d2d83` | |

What was checked directly in the sources:
* Android 17: `a17 GraphicsEnvironment.java`, `a17 GpuService.java`, `a17 VulkanManager.cpp`, and ANGLE `vulkan_icd.cpp`.
* The lapis native build.prop inventory.
* The live `gb_shim.c` (`ef329edd`, the same file 1B cites).
* The delivered `gb_power.c@24db4aa7` on the builder.
* The live `gb_power.c` (`376b743e`, 10:51).
* The live `gb_nouveau.sh` (`aebf9189`), `gb_dgpu_report.sh` (`8c5f0b2a`), `gb_notify.sh` (`a1f465a9`) and `gb_nouveau.rc` (`633e8e91`).
* `build_esp.py` and the gpu-ui sources.
* The VMware VM over adb (read-only).

Line numbers below are for these exact copies. `1B:n`, `TASKS:n`, `W2:n`, `I1.1:n`, `PLAN:n` and `REQ:n` are lines in
those files.

---

## 1. Verdict in short

Most of 1B is careful, and its main Android claims hold (section 5). It cannot be implemented as written, though, for
three reasons:

1. **It is a delta against a spec that no longer exists.** 1B is written against INTERFACES "version 1". I1.1 (still
   being edited) has since added behaviour that collides with 1B, in these places:
   * notification id N15;
   * `refused:detach` against 1B's `refused:detaching`;
   * the `noload` flag;
   * the `/dev/gb_dgpu/off` flag and the live `off_rebind_check()`;
   * the `enable` reply rules.
   None of these is mentioned in 1B (findings B1-B6).
2. **Two safety gaps against PLAN invariant 4 and risk 1.** They come from making NVIDIA app graphics automatic for
   every third-party app:
   * the crash-loop guard does not cover crashes during app use (A1);
   * the new bind path can re-bind a driver that this boot already marked sick (A2).
3. **The self-test gate can switch itself off for good by mistake.** Any stop, reboot or detach during the test is
   recorded as a sticky "crash" failure (A3).

W2 has not absorbed 1B yet:
* it still lists the 1b questions as open;
* it lacks the 1b build items, so `gfx_build_id` is missing and NVIDIA app graphics would never switch on;
* it accepts a VMware result that under 1B means "on" (D1-D4).

The default mode (`auto` per REQ vs keep-off per PLAN section 10) is still undecided. Every document falls back to
`auto` when nobody decides, which is the less safe choice (A6).

Counts: 6 high (A1, A2, A3, A4, A6, D2), 14 medium, 16 low.

---

## 2. Findings

Severity: **High** = breaks a PLAN safety invariant or makes the feature fail on hardware; **Medium** = a real
contradiction or gap that an implementer will hit; **Low** = wording, stale text, or a hardening suggestion.

### A. Safety invariants (PLAN section 8) and REQ intent

**A1 High: crashes during app rendering are not covered by any crash-loop guard.**
* *Conflict.* PLAN inv. 4 (`PLAN:361-362`) and risk 1 (`PLAN:953-954`) rely on the crash-loop guard: a crash costs
  one boot, then nouveau stays off. 1B only writes markers around load, bind and rescan windows, and the self-test.
  * The load marker is removed at the clean mark (+120 s, I1.1 7.2 step 18).
  * Nothing marks the much longer phase in which third-party apps render on NVK.
  * 1B makes that phase automatic, on by default (`1B:42-43`) and universal (`all_apps=0` plus allowlist `*`,
    `1B:108-110`).
  * TASKS row 4 (`TASKS:295`) claims "a crash costs one boot, then `gfx_mode=intel`". That is true only for load
    windows.
* *Failure.* An app that starts by itself renders through ANGLE on NVK and oopses nouveau. Examples: a third-party
  live wallpaper or launcher, or a widget host. With `kernel.panic=30`, which is now on in every boot with the base
  attached, the device reboots after 30 s. It boots, loads nouveau (no marker), starts the same app and crashes again:
  a reboot loop with no automatic way out. The only exit is the boot editor (`gb.nouveau=0`).
* *Fix.* Add an "app graphics session" marker in gb_power:
  * create and fsync it when `gfx_active` goes to `nvidia`;
  * remove it when it goes to `intel` and when `sys.powerctl` is set (clean shutdown);
  * if it is still there at the next boot, count an unclean NVIDIA session;
  * after 2 in a row, force `gfx_mode=intel` and post N01-style text.
  Add tests for it to GP-1B and H-1B.

**A2 High: the new bind path ignores `noload` and `keep`, so it can re-bind a driver this boot marked sick.**
* *Conflict.*
  * I1.1 5.2 says `noload` makes `auto` refuse "for the rest of the boot whatever the properties say" (`I1.1:280`),
    and `keep` keeps a sick driver as it is (`I1.1:281`, D20 `I1.1:1523`).
  * In the live `do_auto()` the "already loaded" branch runs **before** the kill-switch, `noload`, `boot_completed`
    and crash-loop checks (`gb_nouveau.sh:928-950`).
  * 1B 6.3 puts the bind path into that branch with only the guards `i915`, `nodgpu`, `bar` and `detaching`
    (`1B:376-381`), and 1B never mentions `noload`.
  * The rc trigger `booted=1 && gfx_mode=auto` (`1B:828-832`) starts `gb_nouveau` directly, so gb_power's hot-attach
    exclusions (`1B:479-481`) do not protect this path.
* *Failure.*
  1. A load is unhealthy, which creates `keep` and `noload` and forces `gfx_mode=intel`.
  2. The user detaches and reattaches, and the rebind fails, so the dGPU is driverless.
  3. The user picks Auto. The trigger runs `do_auto()`, which takes the bind path and binds the sick driver again.
* *Fix.* The bind path checks `wedged`, kill switch, `noload`, `keep` and `sys.boot_completed` before anything else.
  Add these cases to NP-1B-11.

**A3 High: the self-test's crash marker turns ordinary interruptions into a sticky "failed (crash)".**
* *Conflict.*
  * 1B adds `gb_gfx_selftest` to the services stopped at every detach decision (`1B:250-252`, `:499-500`).
  * A stopped test leaves `selftest/running` behind (6.4 removes it only after a verdict, `1B:399-400`).
  * At the next boot, gb_power turns any leftover marker into `verdict=fail reason=crash`, "never re-run automatically
    for this identity" (`1B:334-336`, `:688-689`).
  * The same happens on a reboot or shutdown during the test, a mode switch to Intel, or a gb_power restart.
  * GP-1B-15 (`TASKS:113`) tests exactly this behaviour.
* *Failure.* On the first boot with the base, the test runs from about 2 minutes after boot_completed for up to about
  5 minutes. If the user restarts or detaches in that window, NVIDIA app graphics stay off on this build until the
  user finds "Run self-test again". N17 then says the test failed, which is wrong.
* *Fix.*
  * When gb_power stops the service itself, or sees `sys.powerctl`, it removes the marker or writes
    `verdict=error reason=interrupted` (retryable).
  * Treat a leftover marker as `error reason=crash` (counted towards the 3 attempts). Make it `fail` only after 2
    crashes for the same identity.

**A4 High: the scope "every third-party app, GLES included, on NVK 8a" goes beyond what PLAN 8a covers, and TASKS
overstates which invariants still hold.**
* *Conflict.*
  * PLAN 8a covers Vulkan-native and ANGLE-GLES window apps for opted-in apps. WebView, Chrome and SurfaceTexture need
    AHB (8b, `PLAN:913-916`).
  * PLAN inv. 2 (`PLAN:353-355`) says "privileged or system apps never load it".
  * PLAN inv. 14 (`PLAN:385-386`) says an NVK failure is never fatal to the app.
  * 1B sends every non-privileged app that is not a stock system app to the driver package (`1B:108-110`). That
    includes **updated** system apps: GraphicsEnvironment only excludes privileged and non-updated system apps
    (`a17 GraphicsEnvironment.java:952`), and 1B 14.2 lists updated system apps on purpose (`1B:777-778`).
  * ANGLE then picks the discrete GPU (`angle vulkan_icd.cpp:371-375`).
  * TASKS rows 2 and 14 (`TASKS:293, 305`) narrow inv. 2 to "non-updated system apps" and call inv. 14 "unchanged".
    The shim's "never fatal" only covers loading, instance creation and enumeration (`gb_shim.c:12-14`). It cannot
    catch an app that gets NVK as device 0 and then fails on a missing AHB import or a missing format
    (ETC2/ASTC, `PLAN:968-970`).
  * The self-test (G0) proves neither ANGLE on NVK nor the APK's `libgb_nvk.so` and shim. It loads the payload ICD
    directly (`nvk_probe.c:31-33`, default `-d` path).
* *Consequences.*
  * Updated Chrome or WebView-provider processes and GLES games land on an untested path. The only protection is a
    manual, empty "Always Intel" list (`1B:886-888`).
  * Detach will often be refused, because any app with a live Vulkan instance holds a nouveau fd: ANGLE keeps its
    instance for the whole process lifetime, and Flutter and Unity apps do the same. The user's "seamless" wording
    (`REQ:3-6`) will not match daily use.
* *Fix (user decision, see section 4).* Either:
  * keep REQ's scope but seed "Always Intel" with the updated system browsers and WebView provider, add an ANGLE GLES
    mode to the self-test, and correct TASKS rows 2 and 14; or
  * by default, give NVK only to Vulkan instances whose engine is not `ANGLE`, and make GLES-on-NVIDIA a per-app
    opt-in.

**A5 Medium: a failed rescan rebind does not set `keep`/`noload`, so `reconcile_off()` unloads a sick driver.**
* *Conflict.* 1B S6 step 4 (`1B:290-293`) sets fault `rebind` and `gfx_mode=intel`, but no `keep`. 7.5
  (`1B:471-474`) then starts `gb_nouveau_off` (mode intel, bound, refcnt 0). The rule "never unload a sick driver
  automatically" (D20, `I1.1:1523`) applies to S10 unhealthy loads (`1B:343-345`) but not to rebinds.
* *Fix.* On a rebind failure, gb_power creates `/dev/gb_dgpu/keep` and `noload`, matching `unhealthy()`
  (`gb_nouveau.sh:841-845`).

**A6 High (decision gap): the default-on decision is unmade, and every document falls back to the less safe value.**
* *Conflict.*
  * REQ wants `auto` by default (`REQ:22, 29`).
  * PLAN section 10 keeps nouveau opt-in until k14 with N1-N3, 100 freeze-free cycles, idle power and 30 days of use
    (`PLAN:938-947`).
  * `kernel.panic=30` still waits for the user's approval (`PLAN:985`, `I1.1:140`).
  * 1B adds `ro.vendor.gb.gfx_default` but defines "absent" as `auto` (`1B:43, 88`).
  * W2 has no edit that sets it, and no `verify_image` check for it. W2 says "REQ wins" (`W2:17`) and plans for
    auto-load on the first hardware boot (`W2:96`, Q-U3 `W2:1110`).
  * TASKS W2B-02 makes it a user decision (`TASKS:233`), and 1B Q3 and W2 Q-U3 ask the same question in two different
    ways.
* *Fix.* Ask the user once (section 4). Until they answer:
  * W2 writes `ro.vendor.gb.gfx_default=intel` explicitly;
  * `verify_image` requires exactly one value;
  * when the driver package is absent (`GB_GFXDRIVER=0`), the default is `intel`. Otherwise an image without app
    graphics still auto-loads nouveau on every boot just for compute (`1B:87-88` and 6.2 key only on the mode, not on
    `cap`).

### B. 1B against INTERFACES v1.1 and the live wave-1 code

**B1 Medium: the citation baseline is gone and INTERFACES has moved on.**
* 1B calls itself a delta to INTERFACES "version 1" (`1B:3`), which no longer exists. I1.1 now has a section 21 change
  list (`I1.1:1556-1575`) and is still being edited.
* Every `INTERFACES.md:n` citation in 1B and TASKS uses v1 numbers, and the offset grows through the file. For example:
  * `persist.vendor.gb.nouveau` is cited at `:195`; it is now at `I1.1:203`;
  * `ro.gfx.driver.0` is cited at `:219`; it is now at `:227`;
  * D24 is cited at `:1392`; it is now at `:1527`.
* The script versions 1B cites by line number exist nowhere any more:
  * `gb_nouveau.sh@4aaad009`, `gb_dgpu_report.sh@4454c7c2`, `gb_notify.sh@b15e0ccd`, `gb_nouveau.rc@98a85885`;
  * the host and the builder (`/build/native/dgpu/nouveau-policy/src/native/`) both hold `aebf9189`, `8c5f0b2a`,
    `a1f465a9` and `633e8e91`.
  * Only `gb_power.c@24db4aa7` survives, on the builder. Its function lines match 1B; this review checked about 40 of
    them.
* *Fix.* Once I1.1 is frozen, publish 1B rev 2 against I1.1. The function names 1B uses do exist in the live scripts:
  `do_auto`, `unhealthy`, `apply_perms`, `gfx_open`, `do_off`, `unbind_all`, `phase_probe`, `lock_esp`, `handle`.

**B2 Medium: notification id N15 is used twice.**
I1.1 defines N15 as `gb_dtx` "Can't detach yet / The NVIDIA driver is starting..." (`I1.1:1012`, also GP-26 and 10.3
step 1b at `I1.1:825-827`). 1B defines N15 as "Performance base connected" and adds N16 and N17 (`1B:703-705`), and
GP-1B-10 tests them. *Fix:* renumber 1B's notifications (N16-N18, or the next free ids in the frozen I1.1).

**B3 Medium: the detach guard has two names and two behaviours.**
* I1.1 and the live script wait up to 15 s for `/dev/gb_dgpu/detaching`, then return `refused:detach`
  (`I1.1:143, 586-590`; `gb_nouveau.sh:49, 996-999`). They check again late in the window (`:1042, :1051`).
* 1B adds a separate guard with no wait, returning `refused:detaching` (`1B:100, 369-370`), and NP-1B-11 expects that
  name (`TASKS:58`).
* *Fix.* Drop 1B's guard, use I1.1's `refused:detach`, and add the same wait and late check to the bind path.

**B4 Medium: 1B does not retire the live `off_rebind_check()` or the `off` flag.**
* *The live mechanism.* I1.1 5.2 (`I1.1:282`) and the live gb_power (`gb_power.c:2663-2669`) start `gb_nouveau_off`
  whenever nouveau binds again after an `off`, as long as `persist.vendor.gb.nouveau` is not `1`.
* *Why it matters.* 1B stops writing that property (7.12, GP-1B-17 `TASKS:115`), so the condition is always true.
* *Where 1B misses it.* Section 0 lists other live additions (`1B:18-20`) but not this one, and T2.6 (`TASKS:85`) does
  not mention it.
* *Failure.* After a switch from Intel back to Auto in a `foreign` boot, the bind path binds the dGPU, and gb_power
  immediately starts `gb_nouveau_off`. The `off-skipped` check (`1B:404-405`) prevents the unbind. But `off` still
  rewrites `vendor.gb.nouveau.result` in the middle of the bind path's health and clean-mark window, and the self-test
  scheduler keys on that result (`1B:226-227, 669-670`).
* *Fix.* Merge `off_rebind_check()` into `reconcile_off()`, keyed on `gfx_mode=intel`.

**B5 Medium: the `set-mode` reply rules undo the I1.1 C5 fix.**
* I1.1 9.3 and C5 (`I1.1:707, 1566`) and the live `cmd_enable()`/`cmd_disable()` (`gb_power.c:2986, 2997`) answer
  `next-boot` when `noload` exists, when `keep` exists (for disable), or when the runtime start cannot load.
* 1B's rules (`1B:555-556`) and GP-1B-16 (`TASKS:114`) know only `booted`, once/test and wedged. Since `enable` is
  remapped to `set-mode auto` (`1B:526-528`), it would answer `now` again after an unhealthy load.
* *Fix.* Add `noload`, `keep` and "a previous auto still runs" to the effect rules.

**B6 Low: gb_notify's `--include-stopped-packages` is already live** (`gb_notify.sh:72`). 1B 6.8 (`1B:432`), T1.9 and
NP-1B-18 list it as new work. It is harmless, but strike it from the work list.

**B7 Medium: during gb_power's own rescan rebind, nothing marks the window as a load window.**
* I1.1 refuses a detach while the ESP lock owner is a live `gb_nouveau` (`I1.1:825-827`; live `load_window_open()`,
  `gb_power.c:972-978`).
* In 1B, gb_power takes the lock as `gb_power` (`1B:283-284`), and the self-test takes it as `gb_dgpu_report`
  (`gb_dgpu_report.sh:241-242`).
* So the new reattach probe (S6, the normal reattach path in Auto) and its 30 s health window are not covered by the
  v1.1 load-window refusal.
* There is also a UX difference. A detach right after an attach is refused with N15 on the insmod path, but follows
  the slow path during a rescan rebind.
* *Fix.* `load_window_open()` also returns true while gb_power's own `rebind_pending` is set. Document the "no detach
  for about a minute after attach" behaviour in the REQ-facing docs.

### C. Contradictions inside 1B

**C1 Medium: tile row 7 can never show.**
* 4.2 checks row 3 (`mode=intel` -> reason `mode`, `1B:155`) before row 5 (`crashloop`, `1B:157`).
* Crash-loop and unhealthy runs force `gfx_mode=intel`, so the reason is always `mode`.
* As a result:
  * tile row 7 "Intel only (recovered)" (`1B:734`) can never match;
  * matrix row `1B:181` is wrong;
  * UI-1B-01 cannot pass for that row.
* *Fix.* Move the crashloop/keep row above row 3, or have tile row 7 key on `state=crashloop`.

**C2 Medium: self-test retries of `error` disagree.**
* S9 says an `error` "is retried at the next healthy bind", at most 3 times (`1B:332-333`).
* 12.2 makes the test eligible on every tick while bound and clean, with `attempts < 3` (`1B:668-672`). So the retries
  run back to back in the same boot.
* After 3 errors the reason stays `untested`, and the tile shows "Intel (testing NVIDIA)" forever (`1B:741`). 14.1 has
  no row for `gfx_test=error`.
* *Fix.* Wait for an unbind/rebind or the next boot before retrying, and add a tile row for "NVIDIA not tested" on
  `gfx_test=error`.

**C3 Medium: a hung self-test has no deadline.**
* `timeout 150` (`1B:394`) cannot kill an nvk_probe stuck in a kernel ioctl (D state).
* While it is stuck:
  * the ESP lock stays held;
  * the hold keeps the dGPU in D0, and the `d0-idle` watchdog is exempt while the hold is on (`1B:515-517`);
  * every detach waits for `gb_gfx_selftest` to stop and ends refused (`I1.1` 10.3 step 4);
  * "Close apps and detach" cannot close a root holder.
* PLAN inv. 8 (`PLAN:372-374`) requires a deadline that ends in a known state.
* *Fix.* gb_power gives the test a deadline (for example 360 s from start). When it passes:
  * latch fault `selftest-hang`;
  * release the hold;
  * post N06 text;
  * write `verdict=fail reason=hang`.

**C4 Low: node-permission enforcement trusts spoofable properties.**
* 7.4 skips enforcement while `init.svc.gb_nouveau` or `init.svc.gb_nouveau_off` reads `running` (`1B:466-467`).
* 4.2 row 5 acts on `vendor.gb.nouveau.result` (`1B:157`), which 4.1 calls informational only (`1B:144`).
* I1.1 4 calls every such property untrusted (`I1.1:190-193`). The live gb_power uses the root-only lock owner file
  instead "because properties are untrusted" (`gb_power.c:970-971`).
* *Failure.* After a rescan, ueventd creates the nodes as 0666 (D19). A spoofed `init.svc.*` keeps them 0666 while
  `gfx_active=intel`.
* *Fix.* Key the skip on root-only evidence (the ESP lock owner, or gb_power's own record of having started the
  service).

**C5 Low: in test boots, app graphics can still turn on.**
In `gb.nouveau=test` boots, panic_on_oops is 0 (`I1.1` 7.6) and the Phase 2B sequence measures idle power and D3cold.
4.2 still lets `gfx_active` become `nvidia`, so third-party apps interfere with the measurements. *Fix:* force Intel
with reason `test` in test boots.

**C6 Low: the kill switch leaves the driver package active.**
In a `gb.nouveau=0` boot, `all_apps` stays `0` (7.2 only looks at `cap`, mode and `gfx_ok`), so the shim still loads
in every app. *Fix:* queue `3` in kill-switch boots, so "kill switch" means pure lapis graphics.

**C7 Low: tile rows 2-5 ignore taps.**
Rows 2-5 are UNAVAILABLE, so a tap does nothing (`1B:729-732, 748`). That undoes D1B-10's own reasoning
(`1B:867`): in a kill-switch boot, the user cannot pick Intel for the next boot from the tile. *Fix:* use INACTIVE
for `disabled`.

**C8 Low: probation counts binds as well as boots.**
The bind path runs "the clean mark exactly as a fresh load" (`1B:381`), so `clean_boots` also counts binds. Probation
then means "3 clean loads", not "3 clean boots" (D2, `I1.1:1505`). *Fix:* say so, or skip the increment for binds.

### D. W2 against 1B and TASKS

**D1 Medium: W2 still treats the 1b questions as open, and 1B 19 has answered them** (`1B:895-903`). W2 needs these
changes:
* C-03, C-04, C-05 and C-21 (`W2:85-87, 103`), and Q-1b-1..5 (`W2:1113-1117`).
* V8 (`W2:265`): drop `gb_gfxdriver_setup`, add `service gb_gfx_selftest`, and check that the `persist.vendor.gb.nouveau`
  triggers are gone.
* E-07 placeholder (`W2:421`): use `report/selftest-<N>.txt`, and also print `selftest/result`.
* E-08: `GFXTEST_ENTRY` stays false (`W2:446-448, 459`).
* E-11: drop the `-gfxtest` copy (`W2:530`). TASKS W2B-04 (`TASKS:235`) already says "no -gfxtest".
* W2 C-04 recommends a flag file, and 1B chose gb_power's in-memory state. 1B's choice stands.

**D2 High (functional): W2 has no edit for the 1b build items.**
* TASKS W2B-02 and W2B-03 (`TASKS:233-234`) need three things:
  * `etc/gb/gfx_build_id`;
  * `ro.vendor.gb.gfx_default`;
  * asserts that the native rc does not set `debug.hwui.renderer` and the native build.prop does not set
    `ro.hwui.use_vulkan`.
* W2 E-10, E-14 and E-22 have none of them, and V9 (`W2:266`) does not check `gfx_build_id`.
* *Failure.* Without that file, `cap` is 0 (`1B:139`), so `gfx_reason=nopkg` on every boot and NVIDIA app graphics
  never switch on.
* *Fix.* Add an E-item that writes the file from the bytes build_esp already holds (APK, ICD, nvk_probe). V9 then
  compares the three sha256 values.
* For reference: `build_esp.py:1268-1269` already appends `build_prop` lines, as TASKS says, and `GB_RC_NATIVE`
  (`build_esp.py:969-1078`) sets no HWUI property today. The `skiavk` setprop at `build_esp.py:635` is in the VM rc.

**D3 Medium: W2's VMware pass criterion accepts a value that now means "on".**
* W2 6.2 criterion 10 accepts `updatable_driver_all_apps` = `0` or `null` (`W2:882-884`), citing REQ:82 ("stays 0").
* Under D1B-1, `0` means app graphics are allowed. 1B says VMware never writes the setting (`1B:452, 599`), and
  VM-1B-04 expects `null` (`TASKS:222`).
* *Fix.* Require `null`.
* More generally, W2 says "REQ wins" (`W2:17`) and 1B says "1B wins, with written deviations" (`1B:6-7`). State once:
  REQ wins on intent, and 1B's D1B rows win on mechanism.

**D4 Medium: TASKS VM checks need things W2 and I1.1 rule out.**
* VM-1B-04 and VM-1B-06 (`TASKS:222, 224`) need a `gb-ntest` boot, adb, boot_completed and NP-25/GP-24/GC-11.
* W2 C-10 (`W2:92`) says:
  * `GB_NATIVE_TEST` writes `lapis.bin`, which is forbidden;
  * the native USB image in VMware has no adb;
  * it probably never reaches boot_completed.
* I1.1 itself says "NP-25 unreachable as written" (`I1.1:1575`), and its NP-25 still uses `gb-ntest` (`I1.1:1352`).
* VM-1B-03 "boots a rebuilt VM image" (`TASKS:221`) conflicts with "the VM image is never written" (`W2:47-50`)
  unless it means the throwaway `--vm-test` image (R-03/R-04).
* *Fix.* Map VM-1B-03 to R-04. Map VM-1B-04 to the R-13 gblog evidence (bootlog `hello/status` now carries
  `gfx_reason`, 1B 8.5). Mark the parts that need boot_completed as HW.

**D5 Low: wording mismatches.**
* "drop `GB_GFXDRIVER`" (`TASKS:233`) vs W2 keeping `GB_GFXDRIVER=0` as a build opt-out (`W2:85`). Both can be true;
  say "drop the `=1` gate".
* W2B-08 "MODULE_DENY unchanged" (`TASKS:239`) vs W2 E-30, which does change it. Say "no further 1b change".
* TASKS C8 says wave 1 "never starts `gb_nouveau_off`" (`TASKS:272`). That is stale: see B4.

**D6 Medium: ESP-lock interactions on every Auto boot.**
* With Auto as the default, nouveau loads about 60 s into every boot with the base attached. That is the moment when:
  * `gb_audiodiag.sh` mounts the ESP read-write with no lock (W2 C-15, `W2:97`);
  * gb_slotsync mounts the ESP.
* W2 fixes this in E-11 and E-12. Under 1B, those two edits become **preconditions** for shipping any Auto default,
  not hygiene.
* TASKS T2.7 (`TASKS:86`) also gives gb_power lock helpers without the I1.1 5.4 steal. A stale lock would then defer
  the reattach rescan forever.
* *Fix.* gb_power implements the rename-based steal (`I1.1:325-336`).

### E. Smaller gaps (Low)

* **E1. The self-test identity does not track firmware or module changes.** 1B uses the build id, `uname -r` and
  `gfx_build_id` (`1B:901`), but not the payload sha. W2's suggestion `dgpu_payload.sha256` (`W2:1115`) covers a
  firmware-only restage that leaves krel unchanged. Add the payload sha (or `tu117.sha256`) to `gfx=`.
* **E2. The rc triggers can re-fire on a repeated `booted=1` (INFERRED from AOSP init).**
  * AOSP init calls `PropertyChanged` on every successful set, even when the value is the same.
  * `do_auto()` re-sets `booted=1` on every run (`gb_nouveau.sh:807, 931, 974`).
  * With `gfx_mode=auto` and the base detached, `refuse refused:nodgpu` then re-arms `booted=1 && gfx_mode=auto`.
  * If init reaps the shell before it runs `start`, this loops in tablet mode. Wave 1 had the same race, but only with
    `persist=1`.
  * *Fix.* Set `booted` only when it is not already 1. Add an NP test that counts the runs.
* **E3. Android's own setting can overwrite gb_power's list (INFERRED).** Developer options "Graphics Driver
  Preferences" appears when `ro.gfx.driver.0` is set and writes the same `updatable_driver_*` keys. gb_power overwrites
  them at the next push. Note it in the docs, or check it on HW.
* **E4. Older apps may silently stay on Intel (INFERRED).** Apps with an old targetSdk run in
  `untrusted_app_25/27/29/30/32`. Only `untrusted_app` is permissive (`I1.1` 17), so the shim may fail to read
  `vendor.gb.gfx_active` there. Add it to the W2 E-25 hardware audit.
* **E5. Earlier opt-outs are not migrated.** An explicit `persist.vendor.gb.nouveau=0` (the user, or an earlier
  crash-loop) is ignored, so the mode becomes Auto. *Fix:* migrate once: `persist=0` and `gfx_mode` unset ->
  `gfx_mode=intel`.
* **E6. The zygote path is untested.** PLAN P8's zygote preload, unload and reload test (GD-05, `PLAN:924-925`) is now
  the hot path for every app, but no 1b test covers it. Add an H-1B check.
* **E7. A single notification path for refused detaches.** N08 is suppressed whenever GbGpu exists (`1B:261-263`,
  D1B-7). `pm grant POST_NOTIFICATIONS` is "ignore failure" (`I1.1` 13.2), so a refused detach can produce no message
  at all. D24's own reason, "daemon alerts must work without the app" (`I1.1:1527`), argues for keeping N08. *Fix:*
  keep N08 and let the app add the action notification, or have the app report that it can post.
* **E8. Self-test refusals miss the detach flag.** 6.4's refusals (`1B:388-389`) lack the live `detaching()` check
  (`gb_dgpu_report.sh:258-260`). `gb_nouveau.sh off` does not stop `gb_gfx_selftest` (`GPU_USERS`,
  `gb_nouveau.sh:52`), so Intel mode during a test ends `off-busy` naming nvk_probe.
* **E9. Some reasons have no text.** 14.2's reason texts (`1B:765-767`) have no wording for `crashloop`, `nopkg`,
  `nodtx` or `kill`.

---

## 3. REQ coverage

Most of REQ is covered: modes, the base events, the QS tile and Settings page, the commands, the self-test gate and
the tests (`1B` 4-14, TASKS 1-7). Where 1B departs from REQ, it says so in D1B-1..15 (`1B:854-872`), and the reasons
were checked:
* D1B-1 is correct. The code order is `all_apps` (`GraphicsEnvironment.java:971-985`), then the opt-out list
  (`:986-991`), then the allowlist, where `*` must be element 0 (`:1010-1018`). GpuService resets the allowlist and
  refills it from `assets/allowlist.txt` (`GpuService.java:266-277`) at `PHASE_BOOT_COMPLETED` (`:113-131`). It reads
  `ro.gfx.driver.0` in its constructor (`:87-91`).
* D1B-6 holds. HWUI sets both names to "android framework" (`VulkanManager.cpp:130-137`). Native lapis has
  `ro.hwui.use_vulkan=true` (`vendor_native_build.prop:45`) and `ro.hardware.egl=angle` (`:109`).

Gaps against REQ:
* REQ:82 reads "stays 0" (see D3).
* REQ:3-6 asks for "seamless" switching, which detach refusals by background apps will not give (see A4).
* REQ:39-43 says a failing test leaves app graphics off, but interruptions should not count as failures (see A3).

## 4. Decisions for the user (merged from 1B section 18 and W2 section 12)

1. **Default mode on the first hardware images.**
   * Choice 1: `auto` (REQ).
   * Choice 2: `intel` until PLAN section 10 and the H-1B checks pass. This replaces 1B Q3 and W2 Q-U3.
   * Recommendation: `intel` (A6).
2. **`kernel.panic=30` on every boot that loads nouveau** (1B Q2, W2 Q-U2). With A1 unfixed it can produce reboot
   loops.
3. **App scope** (A4). Choose one:
   * every third-party app, GLES through ANGLE included (REQ as written);
   * Vulkan apps by default, plus a per-app opt-in for GLES;
   * Recommendation: whichever is chosen, seed "Always Intel" with the updated system browsers and the WebView
     provider.
4. **Detach messages** (E7). Keep the daemon N08 as well as the app notification (a duplicate), or rely on the app
   alone.

## 5. Claims checked and found correct

* 1B's citations of `gb_power.c@24db4aa7` match the builder copy (sha `24db4aa7`, 3515 lines). Examples:
  * `nq_put` :756, `gfxdriver_on` :964, `node_perms` :1153;
  * `dtx_set_base` :1699, `decision_start` :1825, `rescan_step` :2254, `hold_eval` :2322;
  * `derive_state` :2407, `hotattach_check` :2479, `status_tick` :2497;
  * `cmd_close_holders` :2829, `run_due` :3222, `read_cmdline` :3397.
* The `gb_shim.c` citations match: header :8-14, `gb_get_prop` :77, `gb_is_framework` :405, `gb_nouveau_ready` :411,
  the gate :462-465, `gb_CreateInstance` :571, the enumerators :648 and :668.
* nvk_probe has `-t`, `-T` and the exit codes 0/1/2/3 as 1B 6.4 uses them (`nvk_probe.c:28-41`).
* The VMware baseline (adb, read-only, during this review) matches TASKS VM-1B-02 (`TASKS:220`):
  * `ro.gfx.driver.0` is empty;
  * the `updatable_driver_*` settings are `null`;
  * SystemUI `Pipeline=Skia (OpenGL)`;
  * the VM also has `ro.hwui.use_vulkan=false` and `debug.hwui.renderer=skiagl`, so the HWUI guard would be active
    there, which is correct for an image that must never carry the package.

## 6. Fix plan, split so several agents can work at once

Step 1 has to come first. After it, the owners can work in parallel; Wave 2 integration then stays serial, as
W2 section 11 says.

| Order | Owner | Findings |
|---|---|---|
| 1 (first, serial) | spec owner | Freeze I1.1. Publish 1B rev 2 against it: B1, B2, B3, C1, C2, D3 precedence |
| 2 (parallel) | nouveau-policy | A2, B3, B6, C8, E2, E8 |
| 2 (parallel) | gb-power | A1, A3, A5, B4, B5, B7, C2, C3, C4, C5, C6, D6, E1, E5 |
| 2 (parallel) | gfx-driver-pkg | A4 (ANGLE rule, if chosen), self-test coverage of the APK's NVK (A4) |
| 2 (parallel) | gpu-ui | C1, C2 (tile rows), C7, E7, E9 |
| 2 (parallel) | Wave 2 planner | D1, D2, D3, D4, D5, D6 |
| 3 | user | section 4 |
