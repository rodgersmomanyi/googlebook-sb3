# nouveau-policy: Wave 2 integration notes

**v1.2 (wave 1b, graphics switching) is section 7 at the end of this file: it supersedes the artifact shas of
section 1 and the test results of section 5.** **Section 8 (v1.2 r2, the review fix pass) supersedes the shas of
7.2 and the results of 7.6.** Sections 1-6 describe the v1.1 deliverables (kept for reference).

Component 1 (INTERFACES.md **v1.1**, 1.1). Covered:

* plan Phase 1 items 1-4: `gb_nouveau.sh`, `gb_nouveau.rc`, `gb_dgpu_report.sh`, `gb_dgpu_check.sh`, the nvk probe rc
  change (component 4 owns `gb_nvk.rc`, see 2.2);
* the Phase 5 stage step inside `gb_nouveau.sh`, and (v1.1) the payload build tool `payload/make_dgpu_payload.sh`
  with NP-21;
* (v1.1) plan P0-6: `firmware/fetch_tu117_firmware.sh` pinned to linux-firmware tag 20260916 + sha list, and
  `firmware/WHENCE.tu117` + `firmware/LICENCE.nvidia` produced;
* `gb_notify.sh`, and the mksh test suite.

v1.1 is the post-review fix pass (INTERFACES 21: C2, C4, C6-C8, C10-C13). Nothing in the shared image pipeline was
edited. The component README is `C:\GB-build\native\nouveau-kernel\README.md`.

## 1. Artifacts (sha256 of the tested files)

| File | sha256 |
|---|---|
| `C:\GB-build\native\nouveau-kernel\native\gb_nouveau.sh` | `aebf918971af8f3a4887fa5fbc6b4034eb44abaa617288b3c100b612cc93049b` |
| `...\native\gb_nouveau.rc` | `633e8e91fa6ba65fe0fbcaeae7a87eb304799554a467d235428e8b05f2b9f61e` |
| `...\native\gb_dgpu_report.sh` | `8c5f0b2a99fb1ceeee3015e92c86d2a2e285afdc51f24b509c7e2748b79f0fa9` |
| `...\native\gb_notify.sh` | `a1f465a90aa5b3603a15a59609fbb409e32c1972fde551bd4d419309b06d0a4d` |
| `...\native\gb_dgpu_check.sh` (unchanged in v1.1) | `91780d74ab78b6c054ec3360c0b6e7eae2fd6a6e324f7ce31e80659c3270f70f` |
| `...\firmware_staged_sha256.txt` (unchanged, becomes `/odm/etc/gb/tu117.sha256`) | `fa0699d77be0dc373787bb6e460da2e520aa8112611f34bac69cea31e5176ea7` |
| `...\firmware\WHENCE.tu117` (payload `firmware/nvidia/WHENCE.tu117`) | `5de89a22c3ff211fdd8d21062770cbf238b4cfb4125fb3df2df0763a3af9b73a` |
| `...\firmware\LICENCE.nvidia` (payload `firmware/nvidia/LICENCE.nvidia`) | `bc5225a57f49c5249dcf238e4ae6437811677a2a8a7f579c3d839e058653ee44` |
| `...\firmware\fetch_tu117_firmware.sh` | `9c1e83e34c0ff4316d16527c4342a58db1005da53cee9f43fd9c2aceec66e7b7` |
| `...\firmware\whence_tu117.py` | `32798367599db85d83c8958edd270a2c81f538b4892314f5d06ae072f8aaa918` |
| `...\firmware\tu117_fw_table.txt` (unchanged) | `feb2ef4651e23d837f58e6f961bd82da41d917df03220bd2d255f31d3b3d0e33` |
| `...\payload\make_dgpu_payload.sh` | `45de8e5d08510291e7b15e79f76391f899100f995ee680833a1909776852b28c` |
| `...\payload\erofs_ls.py` | `90f96583b8460ed3fc7772a2061e7227499dd26e55a8e0c28233a92a5fd8fcdc` |
| `...\payload\test_make_dgpu_payload.sh` (NP-21) | `41a3b177dddf358d09264489abbe3e3aaa2df38145db0cc858a9a25492db73fe` |
| `...\test\test_gb_nouveau.sh` | `5370c8bf16c14172c4237c26e92bbe9f351310a26757ea501a0e6f2805c573c2` |
| `...\test\shims.sh` | `86c7f9eeef7bb03bd17aed27b2ee399904305ecd401d0fa46c2713738a51d533` |
| `...\test\rc_lint.sh` (unchanged) | `1c2bad76428ffcde713d1b6e604ad37a59e54b462e9b9f6c20046ac0ee9b42a2` |
| `...\test\run_tests.sh` (unchanged) | `e1bab8dc31f01cbdad192853d11f00f473f500d1abb17554e3d7136f0baf6c6f` |

All files are plain ASCII with LF line endings. The pre-Wave-1 versions are kept as `native\*.pre-dgpu` (not in any
manifest; see README W2-00 for the live-manifest situation).

## 2. Edits to shared files

### 2.1 `C:\GB-x86\inject\native\components\nouveau.json` (W2-01)

Exactly as in `README.md` W2-01 (four scripts + the firmware manifest; `gb_nouveau.rc` as `rc_files`). Verify after
the build:

* the native `gb.rc` contains `service gb_nouveau `, `service gb_nouveau_off `, `service gb_dgpu_dmesg `,
  `service gb_dgpu_report `, `service gb_dgpu_test `, `service gb_notify ` and
  `mkdir /data/vendor/gb 0751 root shell`;
* the cpio has `gbnative/vup/bin/gb_{nouveau,dgpu_check,dgpu_report,notify}.sh` at 0755; the VM cpio has none.

The old manifest triggered on `sys.boot_completed=1 && persist...`. The new rc starts `gb_nouveau` on every native
boot at `sys.boot_completed=1`; that run exits at once with `result=idle` unless something enables nouveau.

### 2.2 `nvk.json`: the nvk probe rc (W2-01 / W2-11)

Use `rc_files` = `C:\GB-build\native\nvk-android\gb_nvk.rc` (component 4; v1.1 runs the probe as user shell in group
graphics and creates `/data/vendor/nvk` 0770 shell shell, INTERFACES 15.5). The v1 "Option A" `rc_text` variant is
withdrawn: it ran the probe as root, which bypasses the chmod 000 of the refcnt gate (review R2-1).

### 2.3 NVIDIA test boot entry text (plan P1 item 6; W2-03 / W2-04)

`loader/entries/gb-<x>-nvtest.conf` is identical to `gb-<x>.conf` except:

```
title   <base title> (slot <X>) [NVIDIA test]
linux   /vmlinuz
initrd  /initrd-<x>.img
options <base options> gb.nouveau=test
```

`gb_nouveau.sh` reads only the **last** `gb.nouveau=` token; `test` gives a non-persistent load with
`debug=gsp=debug`, `kernel.panic=30`, `panic_on_oops=0`, the dmesg-live stream and then `vendor.gb.dgpu_report=2`.
535 fallback: append `gb.nouveau.fw=535` with the boot editor. Recovery: append `gb.nouveau=0`. Never the default.

### 2.4 gb_bootlog (`build_esp.py GB_BOOTLOG_SH`, W2-02): what it must match

* **ESP lock** `/dev/gb_esp.lock`: the v1.1 algorithm of INTERFACES 5.4 (rename-based steal with inode + owner check,
  release only one's own lock). README W2-02 has the exact shell text. `gb_nouveau.sh` holds the lock from payload
  staging until the health check ends (normally < 40 s); `gb_dgpu_report.sh test` holds it for the PM stress
  (2-3 min) and the suspend test (5 min).
* **Report trigger** at t=40: `report/dgpu-<N>.txt` appears at about t=340 (the `== power` section samples for 5 min).
  Check for it at the t=420 snapshot (W2-02).
* **Files to include:** `report/{dgpu,load,test}-<N>.txt`, `report/dmesg-live.txt` and `.prev.txt` (the previous
  boot's stream = freeze evidence), `report/gb_nouveau.log` and `.1`; and `logcat -d -s gb_notify gb_gpu_ui_setup
  gb_gpu_compute | tail` (gb_notify logs to logcat, NP-D11).
* `report/` is 0750 root:shell now: gb_bootlog runs as root, unaffected.

### 2.5 Phase 5 payload (`build_esp.py`, W2-08)

The tool exists now (v1.1, INTERFACES 6.3). Wave 2 copies **`payload/make_dgpu_payload.sh` together with
`firmware_staged_sha256.txt`** (default path `../firmware_staged_sha256.txt` relative to the tool, or pass it as the
4th argument) to the builder, stages the tree of INTERFACES 6.2 (firmware from `NATIVE_FW` `nvidia/tu117/*`, plus
`firmware\WHENCE.tu117` and `firmware\LICENCE.nvidia` from this component; the 5 modules of the k14 dist; the ICD; the
optional compute libraries), and runs

```
nice -n 19 ionice -c3 sh make_dgpu_payload.sh <tree> <out>/dgpu-<krel>.erofs <krel> <fw_sha_list>
```

Expect the single line `PAYLOAD ok size=... sha256=... files=...` and `<out>/dgpu-<krel>.erofs.sha256` (copy it to
the ESP as `gb/dgpu-<krel>.sha256` and byte-identically into the cpio as `gbnative/etc/gb/dgpu_payload.sha256`).
Requirements on the builder: erofs-utils (present), kmod `modinfo`, coreutils, `python3` only for the test's lister.
The tool refuses (exit 1, `PAYLOAD FAIL <reason>`) an extra/missing entry, a symlink, a firmware sha mismatch, a
module of another krel or without a signature, a non-ELF ICD, a bad `modules.dgpu`, a missing licence file.

What the device's stage step expects (unchanged): one-line descriptor `<64 hex>  dgpu-<krel>.erofs`; the ESP file
`gb/dgpu-<krel>.erofs` on `/dev/block/by-name/<gb.logpart>`; `VERSION` with `gb-dgpu-payload 1` and `krel <krel>`; the 5
modules; `firmware/nvidia/...`; empty cpio dirs `gbnative/dgpu` and `gbnative/firmware/nvidia`. v1.1 adds the
ownership checks of 6.5 (a non-root or symlinked `payload/` or copy is replaced, never used). Legacy mode (no
descriptor) is unchanged, so W2-01 can ship before W2-08.

### 2.6 Builder use by this component

* Packages installed in Wave 1 (Ubuntu archive): `toybox` 0.8.9 and `original-awk` (Android-like test pass).
  Nothing new in v1.1.
* v1.1 downloads (kernel.org only, allowed): a depth-1 `git fetch` of linux-firmware tags 20260916 and 20261019 and
  commit bf992df7 (to prove the 30 TU117 files and LICENCE.nvidia identical at all three; the server ignored
  `--filter=blob:none`, 833 MB, deleted afterwards), and the cgit `plain/` WHENCE and LICENSES/LICENCE.nvidia at the
  tag (`?h=refs/tags/20260916`).
* Scratch `builder:/build/native/dgpu/nouveau-policy/` (sources, work dirs, NP-21 inputs: firmware copy, the 5 k14
  modules copied read-only from `/build/dist14`, the ICD, the compute libs). Removed at the end of the fix pass (see
  the final report for the state).

## 3. Deviations and interpretations (NP-Dn)

v1 entries NP-D1 .. NP-D19 still hold except where noted; v1.1 adds NP-D21 .. NP-D30.

| # | Spec text | Implemented | Reason |
|---|---|---|---|
| NP-D1 | 7.3 step 1: `off` in once/test sets `result=off-ignored` | Same, except while `init.svc.gb_nouveau=running`: then `off` leaves the result alone. | The `booted=1 && persist=0` trigger fires at the end of every once/test load when persist is explicitly 0. |
| NP-D2 | 7.3 step 1 "mode is once or test" | From `/proc/cmdline`, not from `vendor.gb.nouveau.mode`. | Properties are untrusted. |
| NP-D3 | 7.7 / D20 | `/dev/gb_dgpu/keep` before persist 0; `off` then does nothing for the rest of the boot. Now in INTERFACES 5.2 (v1.1), and gb_power answers `disable` with `next-boot` while it exists. | Otherwise the rc trigger would unload the sick driver. |
| NP-D4 | 7.2 step 18 | dmesg re-checked at the clean mark (now in 7.7). | A clean count means the load stayed clean. |
| NP-D5 | 7.8 | `refused:marker` (now in 7.8). | Fail closed. |
| NP-D6 | v1: renderD 0666 on property + cmdline token | **Superseded by v1.1 (C2):** property AND the root-only flag `/dev/gb_dgpu/gfxdriver` (created only by gb_gfxdriver.sh); gb_nouveau.sh no longer reads `gb.gfxdriver=`; `foreign` on the property OR the flag. | One gate in gb_nouveau.sh and gb_power; resolves Q2. |
| NP-D7 | 3 "last occurrence wins" | Any `module_blacklist=` listing nouveau (now in 3). | Fail safe. |
| NP-D8 | 7.3 step 7 helper rmmod | Only helpers in `/dev/gb_dgpu/helpers` (now in 7.3/5.2). | Never unload another driver's helper. |
| NP-D9 | 7.7 dmesg window | Lines after the insmod mark, `init:`/`gb_*:` excluded (now in 7.7). | Own and init log lines. |
| NP-D10 | 2.3 vs 7.6 dmesg cap | Stop at 16 MiB, `.prev` = previous boot. | Keeps the freeze evidence. |
| NP-D11 | 12.1 kmsg | gb_notify falls back to `log -t gb_notify`. | `/dev/kmsg` is 0600 root. |
| NP-D12 | 7.2 step 7 `module` guard | Payload mode checks the modules inside the stage step. | Not visible before the mount. |
| NP-D13 | 6.5 step values | cp/ENOSPC/rename and (v1.1) "cannot create a root-only payload/" map to `error:sha`. | Closest listed value. |
| NP-D14 | 7.10 `test` | cmdline `gb.nouveau=test` and nouveau bound. | Spoofable mode property. |
| NP-D15 | 2B step 6 state | N13 instead of `ready-suspend-test`. | gb_power is the single writer. |
| NP-D16 | 7.3 vs a running load | `off` waits <= 150 s for a live `gb_nouveau` load window. | Avoids a false `unhealthy:bind`. |
| NP-D17 | 7.7 load record | Extra keys `payload_backing`, `probation`, `dmesg_hit`. | Evidence. |
| NP-D18 | 12.2 `<holders>` | comm names, max 5 + `and N more`; "the apps using it" when none found. | Readable. |
| NP-D19 | crash-loop mode | `mode` = cmdline mode or `default`. | persist just forced to 0. |
| NP-D20 | v1: tool and licence files not produced | **Resolved in v1.1:** `make_dgpu_payload.sh` + NP-21, pinned fetch, `WHENCE.tu117`, `LICENCE.nvidia`. | Review R1-M1. |
| NP-D21 | 7.2 4a/4b (v1.1) | `noload` is checked after the "already loaded" exit (so a second `auto` after an unhealthy load still re-applies permissions) and before the boot_completed check; `refused:noload`/`refused:boot` do not set `booted`. | Review R2-5; an early `ctl.start` must not consume the crash-loop marker. |
| NP-D22 | 7.2 step 13 (v1.1) | The oops/killed classification checks oops lines only for the `nouveau.ko` insmod (after its mark); a helper insmod is classified by its exit status alone. | The ring before the nouveau mark may hold unrelated old oopses. |
| NP-D23 | 7.2 step 8 (v1.1) | The detaching wait happens before the ESP lock (so a refusal never holds the lock); the late checks remove the marker. | Nothing was probed yet. |
| NP-D24 | 5.4 (v1.1) | The stale-lock inode is read in the same poll as the owner line; a lock re-created by a live holder that cannot be put back is left as `gb_esp.lock.stale.<pid>` and logged (its holder then does not remove the new lock: release checks the owner line). | A residual three-party race; logged rather than hidden. |
| NP-D25 | 5.1 (v1.1) | The ownership checks compare with the script's own uid (`id -u`, root on the device) so the suite can run unprivileged; the owner itself is not testable without root (HW/VM item). | Test hook without a new env variable. |
| NP-D26 | 12.1 (v1.1) | `qwrite`: `(umask 022 && set -C && cat > x.tmp)` then `mv -f -T x.tmp x.msg`; a planted entry makes the notification drop with a log line. | Verified on the VM: mksh noclobber refuses existing files, symlinks and dangling symlinks; toybox `mv -T` (0.8.9 builder, 0.8.13 VM) exists. |
| NP-D27 | 7.3 step 3 (v1.1) | `stop_gpu_users` waits up to 5 s for all three services together, then proceeds (the refcnt gate still decides). | Same bound as v1's compute wait. |
| NP-D28 | 6.3 tool (v1.1) | Refuses unexpected entries anywhere (not only top level), checks module signatures and the ICD's ELF magic, and compares the image extracted by `fsck.erofs --extract` with `MANIFEST.sha256`. | Stricter than the spec; cheap. |
| NP-D29 | P0-6 pin (v1.1) | Tag 20260916 (2026-09-16), not the newest tag 20261019 nor main: the 30 resolved files and LICENCE.nvidia are byte-identical at all three; the older tag is the conservative pin. The download URLs use `?h=refs/tags/20260916` (cgit). | Reproducible pin; the table did not change. |
| NP-D30 | 7.10 test sequence (v1.1) | `gb_dgpu_report.sh test` also skips/stops its nvk_probe runs while `/dev/gb_dgpu/detaching` exists (gb_power stops the service anyway). | Defence in depth. |

## 4. Spec questions

* **Q1 (v1)** `refused:marker`: resolved, it is a 7.8 value in v1.1.
* **Q2 (v1)** second reader of `gb.gfxdriver`: resolved by C2 (root-only flag).
* **Q3 (v1)** enable lost while a previous `auto` is still in its 120 s wait: v1.1 makes gb_power answer `next-boot`
  in that state (and while nouveau.ko stays loaded without the device), so the UI is truthful; no automatic re-run.
* **Q4 (v1)** dmesg cap stop vs rotate: unchanged (NP-D10).
* **Q5 (v1)** W2-02 t=90: fixed in the README (t=420).
* **Q6 (v1.1)** Any app can still set `persist.vendor.gb.nouveau=1` (INTERFACES 20, v2 item).

## 5. Tests run (builder, 2026-10-25, fix pass)

Logs: `C:\GB-build\native\nouveau-kernel\test\last_run.log` (suite), `...\payload\np21_last_run.log` (NP-21).
Command: `GB_INTERFACES=<INTERFACES.md v1.1> sh test/run_tests.sh <work>` at nice 19 / ionice idle.

```
== mksh: @(#)MIRBSD KSH R59 2024/02/01 +Debian
== shellcheck: 0.9.0
shellcheck exit=0
rc_lint: .../native/gb_nouveau.rc: 6 services, 0 findings
== mksh suite (GNU coreutils)                                      RESULT pass=629 fail=0
== mksh suite (toybox 0.8.9 + awk version 20231127 + busybox tr)   RESULT pass=629 fail=0
== summary shellcheck=0 rc_lint=0 suite_gnu=0 suite_toybox=0
NP-21 (test_make_dgpu_payload.sh, k14 modules, 8a ICD, compute libs): RESULT pass=31 fail=0
```

| Id | Status | Evidence |
|---|---|---|
| NP-01 | PASS | `shellcheck -s sh` 0 findings on the device scripts, the suite, the shims, the payload tool and its test, the fetch script. |
| NP-02 .. NP-20, NP-22 .. NP-24, NP-DS | PASS | as in v1 (556 assertions), with v1.1 expectations: N03/N04 texts unchanged; NP-13 now tests the two-part gfx gate (`-gfx` 0666 with property + flag, `-spoof` 0660 with property + cmdline token but no flag, `-flagonly` 0660); NP-14 also expects `ctl.stop` of `gb_nvk_probe` and `gb_dgpu_test`; NP-16 expects `/dev/gb_dgpu/off` after `off-unbound` and none after `off-done`; NP-20 expects `--include-stopped-packages`; NP-11-corrupt expects `used` set from the window start; NP-24 compares with the v1.1 15.1 text (`0751`, `report/` `0750`). |
| NP-10-race | PASS | A live holder re-creates the lock right before our rename (mv shim): put back (`4243 gb_bootlog 30` survives), logged, then `refused:esp` after 120 s, no stale directory left. |
| NP-11-symdir/-symfile/-hardlink/-dirmode | PASS | A symlinked `payload/` is replaced by a real 0700 dir (target untouched) and re-copied from the ESP; a symlinked or hard-linked copy is deleted and re-copied; a 0777 `payload/` is recreated 0700; all load. |
| NP-21 | PASS | Two builds byte-identical (`1d7277b17ce5bef90420741e10f8c45284993bde2c45458f3a52bd29d04ab9aa`, 60,260,352 bytes, 43 files); input tree unchanged; `.sha256` format; fsck; `erofs_ls.py` listing == 6.2 (+ 2 compute libs) exactly, dirs 0755 / files 0644, all 0:0; 30 firmware files == `firmware_staged_sha256.txt`; MANIFEST matches the `--extract`ed image; VERSION, modules.dgpu, WHENCE/LICENCE present, fixed UUID; 11 refusals (extra top-level, extra firmware, symlink, corrupted byte, missing licence, missing nouveau.ko, bad modules.dgpu, unsigned module, non-ELF ICD, bad library name, wrong krel). |
| NP-26 | PASS | crash-loop boot: `noload`; a spoofed `persist=1` + `booted=1` then gives `refused:noload`, exit 1, no insmod; next boot loads. Kill switch creates `noload`. Unhealthy: `keep` + `noload`; a second `auto` only re-applies permissions. Before `sys.boot_completed`: `refused:boot`, no insmod, booted unset, the crash-loop marker untouched. |
| NP-28 | PASS | detaching flag for 15 s: `refused:detach` after >= 15 fake s, no lock taken, booted=1; flag gone after 5 s: loads; flag appears during the load window (after the helpers): no nouveau.ko insmod, marker removed, lock released, used=1; dGPU pulled during the window: `refused:nodgpu`, marker removed. |
| NP-29 | PASS | BUG line during the probe and insmod killed by a signal: `insmod-failed:nouveau`, marker kept, `noload`, persist 0, next boot `crashloop`; a `BUG:` line without "nouveau" gives `unhealthy:dmesg`; a plain helper failure removes the marker. |
| P0-6 | PASS | `fetch_tu117_firmware.sh --licence` re-downloads WHENCE + LICENCE.nvidia at the tag, checks their sha256 and the excerpt's (`5de89a22...`), output identical to the committed files; `CHECK_ONLY=1` on the staged firmware copy: 30 ok. |
| NP-25 | Wave 2 (VM) | VMware `gb-ntest` boot with `gb.nouveau=test` appended in the boot editor (the VM ESP has no nvtest entry, W2-03): expect `refused:i915` and `report/dgpu-<N>.txt`. Needs the user's approval. |

Read-only VM facts used by v1.1 (adb 192.168.115.133, 276.R4, toybox 0.8.13): `mv -T` and `mv -x` exist; `flock`
exists; `stat -c '%u %g %a %h %F %i'` works; mksh `set -C` refuses an existing file, a symlink and a dangling symlink
("File exists").

## 6. Hardware-only checks (HW, not run)

* NP-HW Phase 2A/2B: report and test sequence, GSP 570 / 535 fallback, D3cold while bound, PM stress, suspend and
  reboot with nouveau bound; `kernel.panic=30` reboots after an oops (INFERRED) and the next boot is `crashloop`
  (now also when the oops killed insmod in test mode, NP-29).
* Health thresholds against real nouveau/GSP output (v1.1 adds the oops patterns; benign matches fail safe).
* `off` with a real holder; real unbind; D3cold after `power/control=auto`; the `off` flag path when nouveau stays
  loaded (foreign) and a reattach re-binds it (gb_power then starts `gb_nouveau_off`).
* Phase 5 payload on the device: file-backed or loop mount of the NP-21 image format, firmware through the bind
  mount, the root ownership checks with real uids, unmount on off, suspend with the payload mounted.
* The detaching flag against a real EC: a load started right after a hot attach while the user presses detach.
* gb_notify on the device (shell-posted notifications, logcat fallback).

## 7. v1.2 (wave 1b)

Implements INTERFACES.md **v1.2** (7.2-v12, 7.2a, the v1.2 notes of 7.3/7.5/7.7, 12.1, 12.2, 15.1-v12, 22.2, 25.3)
and `1b\TASKS_1B.md` v1.2 section 1 (T1.1-T1.11), rebased on the v1.1 files of TASKS 0.1 (byte copies kept in
`nouveau-kernel\native\v1.1\` and `test\v1.1\`: `gb_nouveau.sh` `aebf9189`, `gb_dgpu_report.sh` `8c5f0b2a`,
`gb_notify.sh` `a1f465a9`, `gb_nouveau.rc` `633e8e91`, `test_gb_nouveau.sh` `5370c8bf`, `shims.sh` `86c7f9ee`,
`rc_lint.sh` `1c2bad76`, `run_tests.sh` `e1bab8dc`). Nothing outside `C:\GB-build\native\nouveau-kernel\` and this
file was edited.

### 7.1 What changed

| Task | Where | What |
|---|---|---|
| T1.1 | `gb_nouveau.sh` `gfx_mode()`, `set_booted()`, `boot_id()` | The 22.2 mode rule; `booted=1` set only when not already 1 (every former direct set, REVIEW E2). |
| T1.2 | `do_auto()` | 7.2-v12 order: hw, wedged, kill switch, `noload`, `boot_completed`, crash-loop (marker `boot=` vs `/proc/sys/kernel/random/boot_id`: another boot or no `boot=` = crash-loop, `gfx_mode=intel`, N01 v1.2; this boot = left alone), mode (`test`/`once`/`gfx_mode()`; `MODE=auto`; `intel` = `idle`), then the 4-way "already loaded" branch (bound: perms; `keep`: `refused:keep`; driverless: bind path; none: `refused:nodgpu`), probation for `auto`, the insmod path. Marker `mode=<m> t=<s> boot=<id> argv=<args>` (`write_marker()`); the gfxdriver `foreign` block is gone. |
| T1.3 | new `do_bind()`, `open_window()`, `detach_wait()`, `clean_mark()` | 7.2a exactly (wait for `gb_nouveau_off` 30 s, guards, detach wait, ESP lock owner `gb_nouveau`, `loading`, `used`, no ESP mount, marker `mode=bind ... argv=-` + sync, `kernel.panic=30`, late checks, `bind-mark`, background write + 40 x 0.5 s poll, hang -> `wedged`+`keep`+`noload`+`gfx_mode=intel`, marker kept, `bind-failed`, N05; returned unbound -> `unhealthy:bind`; health; clean mark without `clean_boots++`). |
| T1.4 | `unhealthy()`, `insmod_failed()` | `setprop persist.vendor.gb.gfx_mode intel` after `keep` and `noload`; N02 v1.2. |
| T1.5 | `apply_perms()` | `gfx_open()` deleted; renderD always 0660; `parse_cmdline()` unchanged. |
| T1.6 | `do_off()`, `GPU_USERS` | `off-skipped` after the once/test block and again right after the refcnt read (0660 restored); `gb_gfx_selftest` stopped; N03 v1.2; no N04; `/dev/gb_dgpu/off` still created. |
| T1.7 | `gb_nouveau.rc` | Body byte-identical to 15.1-v12 (7 services); header describes the modes. |
| T1.8 | `gb_dgpu_report.sh` `do_selftest()` | 25.3: refusals, ESP lock owner `gb_dgpu_report` (busy: `verdict=error reason=esp`), marker `selftest/running` (noclobber + sync), `foreign` first, `timeout 150 nvk_probe -t <mode> -T 60` x 2, verdict mapping, record 25.4 (`.tmp` noclobber, sync, rename; attempts/crashes per identity), TERM/INT/HUP trap -> `interrupted`. Usage `report\|test\|selftest`. |
| T1.9 | `gb_notify.sh` | `key=`/`value=`/`src=`; `kind=settings` with the three fixed pairs only; src file checks (regular, not a symlink, `stat -c %u` = 0, <= 64 KiB, <= 512/128 lines), `valid_pkg` per line, de-dup, join, put or delete; tag `gb_gfx`. |
| T1.10 | `test/test_gb_nouveau.sh`, `test/shims.sh` | NP-1B-01..23 + NP-1B-VM; shims: `_kbind` (fake probe shared by insmod and the bind write), bind processing and `ctl/prop_at` / `ctl/at` (timed property / file / kernel-bind events) in `sleep`, `cmd settings` logged (existing `cmd` shim), `nvk_probe` exit code per `-t` mode + detach/TERM injection, real `timeout` (passes 124/137 through), FIFO `drivers/nouveau/bind`, fake `boot_id`, `ro.build.*` props, a `stat -c %u` owner hook, an ordered `gfx_mode` setprop log. Wave-1 assertions rewritten per TASKS 9 (marked "v1.2" in the suite). |
| T1.11 | `test/rc_lint.sh` (unchanged, lints 7 services), `test/run_tests.sh`, `README.md` | `GB_INTERFACES` = INTERFACES v1.2; README "Graphics modes". |
| (extra) | `gb_dgpu_check.sh` | Lists the v1.2 graphics properties instead of the retired opt-in (NP-1B-D15). |

### 7.2 Artifacts (sha256 of the tested files)

| File | sha256 |
|---|---|
| `C:\GB-build\native\nouveau-kernel\native\gb_nouveau.sh` (1515 lines) | `0e8db7952f09872f0135b99894bd4fca2a2c05b51e855a4d9d967fa6a30c330c` |
| `...\native\gb_nouveau.rc` | `4a8c438a6d3a76159a42ef485538774051f742a6310b1438facae4348c4863ec` |
| `...\native\gb_dgpu_report.sh` (843 lines) | `7a4b993eb98bc77d63b29d3dda5aeaeb17236a269f976a54173670bf76cd810f` |
| `...\native\gb_notify.sh` (210 lines) | `a4bc8d6e91985807686edf26c1d5e7753ad72316aa4aaf879085e1926005520e` |
| `...\native\gb_dgpu_check.sh` | `045fe80d338fac643ae82528079b2ee2ab773b22ff338581a29648eea7f12056` |
| `...\test\test_gb_nouveau.sh` | `b8738e8d73aebea4989c73c41124196efd6ad6a0fd580e25666b3ac76bcde9e0` |
| `...\test\shims.sh` | `c4643e48c0932dfb090cdc46e33d3f928247d78bd3e9063171cf580041cf184f` |
| `...\test\rc_lint.sh` (unchanged) | `1c2bad76428ffcde713d1b6e604ad37a59e54b462e9b9f6c20046ac0ee9b42a2` |
| `...\test\run_tests.sh` | `a22c8fbb62e012c838f1cc2fc18751456ffcd8084f55393d687c5b44c8cf3add` |

Unchanged in v1.2: `firmware_staged_sha256.txt`, `firmware\*`, `payload\*` (shas in section 1). All files plain ASCII,
LF line endings.

### 7.3 Wave 2 edits (shared files)

* **`nouveau.json` (W2-01, W2G-11):** the same five `files` and the same `rc_files` as section 2.1; only the bytes
  changed (shas above). No new file: the self-test is the `selftest` verb of `gb_dgpu_report.sh`. After the build the
  native `gb.rc` must contain `service gb_gfx_selftest /system/bin/sh /vendor/bin/gb_dgpu_report.sh selftest`,
  `mkdir /data/vendor/gb/selftest 0700 root root` and the three `persist.vendor.gb.gfx_mode=auto|nvidia|intel`
  triggers, and no `property:persist.vendor.gb.nouveau=` trigger (this is W2G-06 item 4). The VM cpio has none of it.
* **gb_bootlog (W2-02 / W2G-07):** in the report copy loop `for k in dgpu load test; do` add `bind` and `selftest`
  (`report/bind-<N>.txt` is the bind path's record, NP-1B-D4; `report/selftest-<N>.txt` is the self-test report), and
  print `/data/vendor/gb/selftest/result`. `report/gb_nouveau.log` already has every bind and self-test line.
* **ESP lock users (W2G-10):** with Auto, `gb_nouveau.sh` takes the ESP lock about a minute after every boot with the
  base (normally < 40 s), `gb_dgpu_report.sh selftest` for up to about 5 min on the first boot of each identity, and
  gb_power's rebind guard on reattach. gb_bootlog / gb_slotsync / gb_audiodiag must use the 5.4 algorithm.
* Nothing else: the rc text, the scripts and the notification strings are self-contained.

### 7.4 Deviations and interpretations (NP-1B-Dn)

| # | Spec text | Implemented | Reason |
|---|---|---|---|
| NP-1B-D1 | 7.2-v12 step 13, 7.7, 7.2a step 6 "sets `gfx_mode=intel`" | Written in every boot mode, `once`/`test` included (v1.1 wrote `persist=0` only when it was 1). | With Auto as the default the next normal boot would load the driver this boot found sick. Safe direction; the N02 text says so. |
| NP-1B-D2 | 7.2 step 18 / 7.2a step 8 clean mark | When the dGPU is no longer bound to nouveau at +120 s (detached, or switched to Intel, meanwhile), the marker is removed, the boot is not counted and no dmesg re-check runs. | Detach within 2 min of boot is now common; removal-time kernel lines must not turn into a false `unhealthy:dmesg` (which would force Intel). The marker must go either way. |
| NP-1B-D3 | 7.2a (no payload step) | In payload mode the bind path runs `stage_payload()` inside the lock before the marker; with the payload in place this only verifies the mounts (NP-1B-08-payload: no mount). | Binding without the firmware mount would fail the probe; fail closed like the insmod path. |
| NP-1B-D4 | 7.7 load record | The bind path writes `report/bind-<N>.txt` (same keys, `mode=bind`, `argv=-`), prune 20. | Keeps the boot's insmod record `load-<N>.txt`. Bootlog: 7.3. |
| NP-1B-D5 | 7.2a step 2 | After the `off` wait: bound to nouveau now -> permissions only, exit 0; bound to another driver -> `refused:nodgpu` (logged). | The 4-way branch decided on an older state. |
| NP-1B-D6 | 25.3 step 5 after the lock | `bootseq` and `report/selftest-<N>.txt` before the ESP lock (after the step-1 refusals). | The `esp` verdict then names an existing report. Only `/data` is touched before the lock. |
| NP-1B-D7 | 7.2-v12 / 7.2a (no re-check after the lock) | Right after the ESP lock is taken: `wedged` -> `refused:wedged`, `noload` -> `refused:noload` (both paths, no `booted`); bind path also `keep` -> `refused:keep` and "bound to nouveau now" -> permissions only, exit 0 (gb_power's marker untouched). | gb_power's rebind guard holds the lock during the kernel's re-probe; a failed guard creates `keep` + `noload` and then releases it, so a bind path that started meanwhile (a mode-change trigger) would bind the sick driver (REVIEW A2 intent). Tests NP-1B-10-guardfail/-kbound. |
| NP-1B-D8 | 25.3 step 6 | A detach present before the first probe (it can appear during the lock wait) also gives `verdict=error reason=interrupted`. | Same rule as between the probes (review E8). |
| NP-1B-D9 | 25.3 step 3 (noclobber) | If `selftest/running` already exists, the run logs it, releases the lock and exits 1 without a record; the marker is left for gb_power (25.5). | Never destroy crash evidence. `selftest/` is root-only, so only a hard-killed earlier run can leave it. |
| NP-1B-D10 | 25.4 `reason` | A pass record carries `reason=ok`. | 25.4 requires every key, well-formed; the pass reason is not named. gb-power please accept `ok` (Q-1B-3). |
| NP-1B-D11 | 25.3 step 9 | The probe runs in the background with `wait`, so the trap runs at once (mksh: a trapped TERM interrupts `wait`, checked on the builder). `ctl.stop` of a service without `gentle_kill` is a SIGKILL of its process group (AOSP init `Service::StopOrReset()`, INFERRED from AOSP source, not checked on lapis), which no trap sees: then gb_power's 10 s cleanup writes `interrupted` (25.5). The trap covers shutdown (init's `Service::Terminate()` sends SIGTERM first, also INFERRED). | The rc is exact (15.1-v12), so no `gentle_kill`. |
| NP-1B-D12 | 12.1 `valid_pkg` | Implemented with `case` patterns (same language as the 9.3 regex), no `grep` fork per line. | Up to 512 lines per message. |
| NP-1B-D13 | 12.1 settings file checks | A rejected or missing src file writes nothing (neither `put` nor `delete`): the previous value stays. Empty lines are skipped silently; other bad lines are logged. | "dropped" = no write. |
| NP-1B-D14 | 5.1 marker `boot=` | An unreadable or malformed current boot id makes every marker a crash-loop marker. | Fail safe (an empty id could otherwise match an empty `boot=` across boots). |
| NP-1B-D15 | (none) | `gb_dgpu_check.sh` lists `persist.vendor.gb.gfx_mode`, `gfx_apps`, `vendor.gb.gfx_active`, `gfx_reason`, `gfx_test`, `persist.vendor.gb.gfx_ok`. | The retired opt-in property was listed. |
| NP-1B-D16 | 7.2a step 6 | When the background write has returned and the dGPU is not bound, one more 0.5 s poll before `unhealthy:bind`. | Cheap; also closes the fake kernel's asynchronous window in the suite. |

### 7.5 Spec questions

* **Q-1B-1 (reboot inside the clean-mark window).** A reboot or shutdown within 120 s after a load or bind leaves
  `nouveau.loading` (5.1 removes it only at the clean mark, on an unhealthy result or by the crash-loop path; init's
  SIGTERM or SIGKILL at shutdown ends the clean-mark wait, INFERRED). The next boot then takes the crash-loop path: `gfx_mode=intel` + N01,
  although nothing crashed. v1.1 needed an opt-in for this; with Auto it can happen on any boot with the base (boot,
  then restart within about 2.5 min of `sys.boot_completed`). gb_power's rebind guard (+120 s phase) has the same
  window. Proposal: on SIGTERM while `sys.powerctl` is set and the load already passed its 30 s health check, remove
  the marker without counting the boot (as 23.10 does for `gfx_session`). Not implemented: 5.1 is explicit.
* **Q-1B-2 (Auto <-> NVIDIA while bound).** The rc triggers start `gb_nouveau` on every switch to `auto` or
  `nvidia`; with the dGPU bound, step 8 re-applies 0660 to the render node (7.5), and gb_power raises it to 0666 again
  at its next fast tick (<= 2 s, 23.5). An app instance created in that window gets ANV (never fatal). If that matters,
  7.5 could let the bound branch skip lowering a render node that gb_power raised (it would need a root-only signal).
* **Q-1B-3 (pass reason).** See NP-1B-D10: `reason=ok` in pass records.

### 7.6 Tests run (builder, 2026-10-25)

Log: `C:\GB-build\native\nouveau-kernel\test\last_run.log` (the v1.1 log is `test\v1.1\last_run.log`). Command:
`GB_INTERFACES=<INTERFACES.md v1.2> sh test/run_tests.sh <work>` at nice 19 / ionice idle, mksh R59, shellcheck
0.9.0, GNU coreutils pass and toybox 0.8.9 + BWK awk + busybox tr pass. Log sha256 `abe00202...`.

```
== shellcheck: 0.9.0
shellcheck exit=0
rc_lint: .../native/gb_nouveau.rc: 7 services, 0 findings
== mksh suite (GNU coreutils)                                      RESULT pass=1028 fail=0
== mksh suite (toybox 0.8.9 + awk version 20231127 + busybox tr)   RESULT pass=1028 fail=0
== summary shellcheck=0 rc_lint=0 suite_gnu=0 suite_toybox=0
EXIT=0
```

1028 assertions per pass = 640 wave-1 (NP-01..NP-29, NP-DS; v1.1 had 629, the v1.2 rewrites added 11) + 388 v1.2.

| Id | Status | Evidence (assertions per pass) |
|---|---|---|
| NP-1B-01 | PASS | (13) gfx_mode unset: one insmod, `mode=auto`, `probation 1/3`, `dgpu.dmesg=1`, `loading healthy clean`, renderD 0660, marker at the insmod `mode=auto t=.. boot=<fake boot id> argv=runpm=1 modeset=2`, removed at the clean mark, `kernel.panic=30`, no `foreign`, `booted` set once. |
| NP-1B-02 | PASS | (12) `garbage`, `nvidia`, `AUTO`: each loads like NP-1B-01, `mode=auto`, the setting not rewritten. |
| NP-1B-03 | PASS | (11) `intel`: `idle`, `mode=default`, `booted=1`, no insmod, no marker; `off` -> `off-done`, `power/control=auto`, no unbind/rmmod. |
| NP-1B-04 | PASS | (5) retired opt-in `=1` + intel: no load; `=0` + unset: loads; never written; static check: no script or rc names `persist.vendor.gb.nouveau` (except `.args`) or `vendor.gb.gfxdriver`. |
| NP-1B-05 | PASS | (35) marker of another boot and v1.1 format: crash-loop, marker removed, setprop log `persist.vendor.gb.gfx_mode=intel`, never `persist.vendor.gb.nouveau=`, `clean_boots=0`, N01 exact v1.2 title/text/tag, no insmod, `booted` unset, `noload`; marker of this boot with gb_power holding the lock: `refused:esp` after >= 120 fake s, marker byte-unchanged, no N01; with the lock free: loads (own marker, removed at the clean mark). |
| NP-1B-06 | PASS | (18) d3cold, dmesg, killed insmod, oops: ordered log `setprop persist.vendor.gb.gfx_mode=intel keep=1 noload=1 wedged=0`; N02 exact v1.2; the following `off` leaves `/sys` byte-identical; no unbind/rmmod. |
| NP-1B-07 | PASS | (5) no dGPU: `refused:nodgpu` + `booted=1`; dGPU added: second run loads; `booted` set once. |
| NP-1B-08 | PASS | (30) bind path: no insmod, `loading healthy clean`, one write `0000:02:00.0` into `drivers/nouveau/bind`, at the write: marker `mode=bind boot=<id> argv=-`, >= 1 sync, lock owner `gb_nouveau`, `panic=30`; bound, HDA driverless, 0660/0600, `clean_boots` 2 -> 2, marker removed, `used`, loading 1 then 0, lock released, `bind-1.txt` `mode=bind health=clean`, no `load-1.txt`, no `foreign`. Probe failure: `unhealthy:bind`, keep+noload, intel, N02 (bind), marker removed, not wedged. Payload mode: no remount, binds. |
| NP-1B-09 | PASS | (12) FIFO bind: `bind-failed`, exit 1, <= 21 fake s, wedged+keep+noload, intel, marker kept (`mode=bind`), N05, loading 0, lock released, `booted=1`, the hung write carried the BDF; then `auto` `refused:wedged`, `off` `off-wedged`, `/sys` unchanged. |
| NP-1B-10 | PASS | (49) i915, BAR, live lock, ESP vfat mount, `detaching` 15 s, `detaching` after the bind marker (marker removed): the expected `refused:*`, never a bind write, `booted=1`, no marker, lock released. NP-1B-D7: gb_power's guard fails during the lock wait -> `refused:noload`, no window; the kernel binds during the wait -> permissions only, gb_power's marker untouched. |
| NP-1B-11 | PASS | (20) module loaded + driverless with `noload` / `keep` / kill switch / no `boot_completed` / marker of another boot: `refused:noload`, `refused:keep`, `disabled`, `refused:boot`, `crashloop`; never a bind write, no insmod. |
| NP-1B-12 | PASS | (8) `gb_nouveau_off` running 10 fake s: the bind write after the 10 s, `clean`; running throughout: `refused:off` after 30-32 s, no write, no lock, `booted=1`. |
| NP-1B-13 | PASS | (5) gfxdriver property + token + flag: loads, renderD 0660, no `foreign` (property or file), no chmod 0666 by the script. |
| NP-1B-14 | PASS | (9) `off` with `gfx_mode=auto`: `off-skipped`, no unbind/rmmod/chmod/ctl.stop; mode flipping to auto in the chmod-000 second: `off-skipped`, 000 seen, back to 0660/0600, no unbind. |
| NP-1B-15 | PASS | (7) foreign + intel: `off-unbound`, no rmmod, no notification (no N04), `/dev/gb_dgpu/off`; ctl.stop order `gb_dgpu_test gb_nvk_probe gb_gpu_compute gb_gfx_selftest`; Auto again -> the bind path binds the kept module. |
| NP-1B-16 | PASS | (11) rc_lint `7 services, 0 findings`; selftest service, `selftest/` mkdir, the three triggers, no v1.1 trigger, no gfxdriver; body byte-identical (`cmp`) to the INTERFACES v1.2 15.1-v12 block (7 services). NP-24 also passes against v1.2. |
| NP-1B-17 | PASS | (26) `all_apps` 0 and 3 put; 1, `0;reboot`, `adb_enabled`, key/src mismatch, all_apps+src, a path as src, opt-in+value, value+src: dropped and logged; valid list joined in file order; bad lines (6) dropped and logged, duplicate removed; empty -> `delete`; symlink, missing, uid 1000 (hook), 513 lines, > 64 KiB, opt-out 129 lines: nothing written; 512 lines written; opt-out list written. |
| NP-1B-18 | PASS | (2) N16 and N17 posted with tag `gb_gfx`, exact strings. |
| NP-1B-19 | PASS | (23) both probes in order with `-T 60`; at the first probe `foreign=1/1`, marker `boot=<this boot>`, lock `gb_dgpu_report`; lock released, marker removed; record keys `verdict reason build krel gfx t attempts crashes report`, `pass/ok`, identity exact, `t` epoch, `attempts=1 crashes=0`, `report=selftest-1.txt`, 0644, no `.tmp`; report 0640 root:shell with identity, argv/result, both outputs and exits; `/sys` unchanged; second run `attempts=2`. |
| NP-1B-20 | PASS | (18) (1,0) `fail:ahb-i915-readback`, (0,124) and (0,137) `fail:a17-swapchain-model timeout`, (3,3) `error:nodevice`, (2,0) `error:icd`, (0,5) `fail:a17-swapchain-model rc 5`, (0,0) `pass:ok`, (2,1) `fail:a17-swapchain-model`; attempts 1..8 for one identity; crashes carried; a new identity -> `attempts=1 crashes=0`. |
| NP-1B-21 | PASS | (49) not native, `gb.nouveau=test`, unbound, no `selftest/`, `selftest/` a symlink, `selftest/` 0755, `detaching`, no `gfx_build_id`: exit 1, `/data` byte-identical, no probe, no `foreign`, no lock; ESP lock busy: `error/esp` after >= 120 s, attempts 2 and crashes 1 kept, no probe, no marker, lock untouched. |
| NP-1B-22 | PASS | (14) SIGTERM during the first probe (sent by the fake probe to the script): `error/interrupted`, attempts unchanged, marker removed, lock released, second probe not run; `detaching` between the probes: the same (attempts 0). |
| NP-1B-23 | PASS | (2) two refused runs: `vendor.gb.nouveau.booted` set once. |
| NP-1B-24 | PASS | = NP-01 (shellcheck 0 findings on all `.sh`, justified disables only). |
| NP-1B-25 | PASS | Wave-1 suite 640 assertions per pass, all PASS, with the TASKS 9 rewrites (NP-02 mode unchanged, NP-03/05/12/26/29 `gfx_mode=intel` + N01/N02 v1.2, NP-04/19 `mode=auto` probation, NP-13 0660 and no foreign, NP-14 N03 v1.2 + selftest stop, NP-15/16 `off` in Intel mode and no N04, NP-24 15.1-v12, NP-26-unhealthy `refused:noload`) and the extra `NP-1B-VM` (4: VMware native-smoke `refused:i915`, nothing queued, no `gfx_mode` write, self-test refused). |
| NP-21 | not re-run | `payload\` unchanged since v1.1 (31/31). |

Builder scratch: `/build/native/dgpu/nouveau-policy/v12/` (sources, `run_tests.log`, work dirs); free space during
the run `/` 27 GB, `/build` 12 GB.

### 7.7 Hardware-only checks (HW)

* Auto load on every boot with the base: boot time and MemAvailable against an Intel-only boot (H-1B-01).
* The bind path on the real kernel: Auto after an `off-unbound` (foreign) boot; a reattach whose kernel re-bind did
  not happen; the bind write's real return status and probe time (H-1B-05, H-1B-07).
* `selftest` on the TU117: real exit codes and timing of both probes against `timeout 150` / `-T 60`; the trap on a real
  shutdown during the test; `ctl.stop` behaviour (SIGKILL, NP-1B-D11) (H-1B-01, H-1B-11).
* `gb_notify` as user shell: `cmd settings put|delete global` for the three keys, a 512-package list; N16/N17/N01-N05
  as shell notifications (H-1B-04, H-1B-05).
* Q-1B-1 on the device: a reboot within 2 minutes of `sys.boot_completed` with the base attached.

## 8. v1.2 r2: review fix pass (INTERFACES 29)

**This section supersedes the artifact shas of 7.2 and the test results of 7.6.** Applies R-1, R-2, R-7, R-10 and
R-11 (INTERFACES 7.2b, 7.3, 25.3) to the 7.2 files (byte copies of them in `native\v1.2r1\` and `test\v1.2r1\`).
`gb_notify.sh`, `gb_nouveau.rc` (= 15.1-v12) and `gb_dgpu_check.sh` are unchanged.

### 8.1 What changed

| Fix | Where | What |
|---|---|---|
| R-1 | `gb_nouveau.sh` `on_signal()` (the `auto` HUP/INT/TERM trap), `write_marker()` (`MARKED`, `MARKLINE`), `do_bind()` (`BINDING`) | With `sys.powerctl` set and our own marker line on disk and no bind write pending (or the dGPU already bound): remove the marker, `sync`, log `clean shutdown (sys.powerctl=...)`, exit 0; else keep it (log why), exit 1. The EXIT trap keeps the exit status (`trap '_x=$?; finish_window; exit $_x' EXIT`; mksh otherwise exits with the EXIT trap's status after an `exit` inside a signal trap). |
| R-2 | `wait_for_loader()` | Also waits for a live ESP lock owner named `gb_power`. |
| R-7 | `health_check()`, `clean_mark()` (`HC_S`); new `wake_lock()`/`wake_unlock()` (`open_window()`, `finish_window()`) | Windows counted in seconds slept; wakelock `gb_nouveau` from `loading=1` to the verdict. |
| R-7 | `gb_dgpu_report.sh` `wake_lock()`/`wake_unlock()`/`st_exit()` | Wakelock `gb_gfx_selftest` from the marker write to the exit (EXIT trap `st_exit`). |
| R-10 | `apply_perms()` (`bound` argument), `do_auto()` bound branch, insmod and bind paths, `unhealthy()` | 7.2b. |
| R-11 | new `rmmod_nouveau()` (`RMST`), `do_off()` | Background `rmmod nouveau`, 40 x 0.5 s polls of `/dev/gb_dgpu/rmmod.<pid>` (non-empty = complete); `hang` -> `wedged`, `off-wedged`, N05, exit 1. |
| R-11 | `gb_dgpu_report.sh` `st_interrupted()` | Record first; then `pgrep -x nvk_probe` polls (12 x 0.5 s; 2 while `sys.powerctl` is set; `/system/bin/pgrep -> toybox` on lapis, read on the VM); then unmark and release. |
| tests | `test\test_gb_nouveau.sh` block NP-R (TASKS_1B 12.1), `test\shims.sh` | New shim controls `ctl/term_at` (SIGTERM to the script under test at a fake time, optionally setting `sys.powerctl`), `ctl/suspend_at` (CLOCK_BOOTTIME jump + `suspend_stats` bump), `ctl/at` ops `d3cold`/`node`/`copy`, `ctl/rmmod_hang_<m>`, a `pgrep` shim (`ctl/pgrep_n`), wakelock state in `insmod_state.log`, `bind.log`, `nvk_wake.log`; `fp_tree()` ignores `/sys/power/wake_*`; NP-18 rewritten (R-10). |

### 8.2 Artifacts (sha256)

| File | sha256 |
|---|---|
| `native\gb_nouveau.sh` | `6a754d4228eea8dfd45315269195bc915bb38539d0878b80e55c0bf113aca392` |
| `native\gb_dgpu_report.sh` | `ccf3ddfb0d8f1f1843a3aab8f50f0971fc4a84903bd5b7fc07538bde6c713dfe` |
| `native\gb_notify.sh` (unchanged) | `a4bc8d6e91985807686edf26c1d5e7753ad72316aa4aaf879085e1926005520e` |
| `native\gb_nouveau.rc` (unchanged) | `4a8c438a6d3a76159a42ef485538774051f742a6310b1438facae4348c4863ec` |
| `native\gb_dgpu_check.sh` (unchanged) | `045fe80d338fac643ae82528079b2ee2ab773b22ff338581a29648eea7f12056` |
| `test\test_gb_nouveau.sh` | `9770273da9553f57d741c8ba0220b4ee1941ef739fe17ffa40d5fd2126af3754` |
| `test\shims.sh` | `7c207eea863d8a24007157e8593d3c0b88856d8f9dac5f87e276e27e3dcdb309` |
| `test\run_tests.sh`, `test\rc_lint.sh` (unchanged) | `a22c8fbb...`, `1c2bad76...` |

### 8.3 Wave 2 edits (shared files) - delta to 7.3

* `nouveau.json`: the same five files and `rc_files`; new bytes for `gb_nouveau.sh` and `gb_dgpu_report.sh` (shas
  above). The native `gb.rc` checks of 7.3 are unchanged (the rc text did not change).
* gb_bootlog: as 7.3, plus `gfx-switching.md` r2 W2G-07 (state files).
* Nothing else.

### 8.4 Deviations and interpretations (NP-R-Dn)

* NP-R-D1 (R-1): the marker is also removed on a clean shutdown inside the 30 s health window (after the insmod or bind
  returned), not only after the health verdict as review 2 proposed: a clean shutdown means init and the kernel were
  alive, an unhealthy load would have removed the marker as well, and the next boot repeats the health check. Kept
  while a bind write has not returned (it may be inside the probe).
* NP-R-D2 (R-7): the windows count only the sleeps (the commands between them add real time), so in real time they get
  slightly longer, never shorter.
* NP-R-D3 (R-11): the self-test's stop path writes the record before the wait (a SIGKILL during the wait then leaves no
  false `crash`), and caps the wait at 1 s while `sys.powerctl` is set.

### 8.5 Tests (builder 192.168.115.132, scratch `/build/native/dgpu/fix1b/nouveau/`; `test\last_run.log`)

Final run 2026-10-25 14:50:36-14:57:42 UTC (load 0.68 at start, no gbiso job), `GB_INTERFACES` = INTERFACES v1.2 r2:

| Test | Result | Evidence (`test\last_run.log`) |
|---|---|---|
| NP-01 / NP-1B-24 shellcheck -s sh (native\*.sh, test\*.sh) | PASS | `shellcheck exit=0` |
| NP-24 / NP-1B-16 rc lint + 15.1-v12 byte comparison | PASS | `7 services, 0 findings`; `rc body byte-identical to INTERFACES 15.1-v12` |
| mksh suite, GNU coreutils | PASS | `RESULT pass=1083 fail=0` (the 1028 earlier assertions + 55 NP-R) |
| mksh suite, toybox 0.8.9 + BWK awk + busybox tr | PASS | `RESULT pass=1083 fail=0`; `== summary shellcheck=0 rc_lint=0 suite_gnu=0 suite_toybox=0`, `EXIT=0` |
| NP-R block (110 `ok` lines over both passes) | PASS | e.g. `ok NP-R1-clean: crash-loop marker removed`, `ok NP-R7-health: D3cold 6 s after the resume: healthy, then clean`, `ok NP-R11-rmmod: rmmod never returns: off-wedged, exit 1`, `ok NP-R11-probe: waited for nvk_probe (3 found, then none)` |
| Mutation: the NP-R block against the r1 scripts | PASS (r1 fails as required) | 8.6 |
| NP-21 payload tool | SKIP | `payload\` unchanged since v1.1 (31/31 recorded there) |
| NV-R11 S10 (component 4's chroot check of this script's new TERM path with the real probe) | PASS | `nvk-gfxdriver.md` 8.3: `S10 R-11 wait: polls=1 (max 12) left=0` |

### 8.6 Mutation check

`GB_TEST_ONLY=NP-R mksh test/test_gb_nouveau.sh` with the r1 scripts (`gb_nouveau.sh` `0e8db795`,
`gb_dgpu_report.sh` `7a4b993e`) in `native\` (log `test\mutation_r2.log`): `RESULT pass=20 fail=35`; every NP-R case
fails at least once except NP-R2-other (a no-change control): NP-R1-clean/-health (marker kept, next boot crash-loop),
-nopc/-notours/-bind-pending (r1 exits 0 after a signal), -bind-clean, NP-R2-wait (no wait), NP-R7-health (false
`unhealthy:d3cold`), NP-R7-clean (marker removed at the first poll after the resume), NP-R7-selftest (no wakelock),
NP-R10-bound/-late, NP-R11-rmmod (the foreground rmmod hangs until the harness timeout), NP-R11-probe/-probe-pc (no
wait).

### 8.7 Hardware-only (HW)

H-R-1, H-R-2, H-R-4, H-R-5 of `1b\TASKS_1B.md` 12.5; and whether `/sys/power/wake_lock` writes from `u:r:shell:s0`
are logged as denials (permissive, so they work either way).
