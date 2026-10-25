# Wave 2 integration notes: ux-app (component 6, INTERFACES id `gpu-ui`)

This is the integration file of INTERFACES component 6 (`gpu-ui`, plan Phase 7 app side). The Wave 1 task named it
`ux-app.md`; `gpu-ui.md` next to it points here.

* Component directory: `C:\GB-build\native\gpu-ui\`. Component README: `C:\GB-build\native\gpu-ui\README.md`.
* Contract: `C:\GB-build\native\dgpu\INTERFACES.md` **v1.1**, sections 5.1, 8.3, 9, 12, 13.1, 13.2, 15.4 and 18.6 (v1.1
  changes in section 21: C1, C12).
* **(v1.2, wave 1b) Section 8 below supersedes sections 1-5 for Wave 2**: the artifacts, shas, shared-file edits and
  tests of the graphics-switching GbGpu (INTERFACES v1.2 sections 22-28, `1b\TASKS_1B.md` v1.2 section 4). Sections
  1-7 are kept as the wave-1 record.

**v1.1 fix pass (2026-10-25).** `/data/vendor/gb` is now 0751 root:shell (C1: shell may no longer create or rename
entries there), so the one-time tile marker moved to `/data/vendor/gb/ui/tile_added` in a shell-owned 0700 `ui/`
directory that `gb_gpu_ui.rc` creates in `post-fs-data` (15.4 v1.1). The app's Java code is unchanged; the APK was
rebuilt only because the build bumps versionCode. `test/mock_gb_power.py` follows gb_power 1.1 (enable `next-boot`
while a previous auto start still runs, after `noload`, or with nouveau.ko loaded without the device; disable
`next-boot` with `keep`; the compute-svc connection is exempt from the idle close). gb_notify.sh now broadcasts with
`--include-stopped-packages` (item 6.6 below is resolved).

## 1. What Wave 1 delivered

Nothing outside `C:\GB-build\native\gpu-ui\`, `C:\GB-build\keys\` (key backup) and this file was written.

* **GbGpu APK** (package `gb.sb3.gpu`, non-privileged system app for `/odm/app/GbGpu/GbGpu.apk`):
  * active toggleable QS tile `.GpuTileService` (8.3 mapping);
  * optional `.DetachTileService`;
  * injected Settings entry `.SettingsActivity` with a switch backed by `.SwitchProvider`;
  * `.StateReceiver` for gb_power's state broadcast;
  * close-apps dialog `.HoldersActivity`;
  * the two app notification channels of 12.3.
  * It talks only to `/dev/socket/gb_power` through `android.net.LocalSocket` and never needs root. Its only
    permission is `POST_NOTIFICATIONS`.
* **Boot setup:** `gb_gpu_ui.rc` (15.4, byte-identical) and `gb_gpu_ui_setup.sh` (`pm grant`; one-time
  `cmd statusbar add-tile` with the `ui/tile_added` marker; v1 used `ui_tile_added`).
* **`gpu_ui.json`:** draft component manifest, identical to W2-01.
* **Build:** `build_apk.sh` builds with the official SDK via `sdkmanager` (no Gradle or Maven). It generates a
  dedicated key once and bumps `versionCode` on every build. `build_on_builder.sh` is the Windows-side helper.
* **Tests:** `run_tests.sh` plus:
  * `test/mock_gb_power.py`, a mock of section 9 including the 9.6 goldens;
  * host JVM tests (UI-03/04/05 and the real-server cross-check);
  * `check_apk.py` (UI-01/02), Android Lint, the UI-06 shell test and the rc check.

## 2. Artifacts (sha256)

| Artifact | sha256 | Consumer |
|---|---|---|
| `C:\GB-build\native\gpu-ui\out\GbGpu.apk` (versionCode 29882061, 45480 bytes; v1.1 rebuild 2026-10-25 10:22 UTC) | `c53422bafef8a53bdb48623acfc47055dcc3f20c958bbe1820bfb9c443664119` | `gpu_ui.json` -> `app/GbGpu/GbGpu.apk` |
| `C:\GB-build\native\gpu-ui\gb_gpu_ui_setup.sh` (= `out\gb_gpu_ui_setup.sh`) | `312cf0c5ba38d4dce97d41681c9e70521f8c4a812b1c89764b1a703aae43b899` | `gpu_ui.json` -> `vup/bin/gb_gpu_ui_setup.sh` |
| `C:\GB-build\native\gpu-ui\gb_gpu_ui.rc` (= `out\gb_gpu_ui.rc`) | `1c8ca925e0c78f84a49c2451c3ab7132ddc00071ec4214765a3f95fce36307bd` | `gpu_ui.json` `rc_files` |
| `C:\GB-build\native\gpu-ui\gpu_ui.json` | `3f9ed61e56905bd4168651fa66962090af308695b0fea4cc842913c77fcc0d6d` | copy to `C:\GB-x86\inject\native\components\gpu_ui.json` |
| Signer certificate (APK v2/v3) | `686174802bdf4f4196682d030dc2649a8f4e35cb5bab4caad13cbe5cf4619ed1` | verify_image (optional) |

* `out\BUILD_INFO.txt` records versionCode, versionName, the sha256s, the signer digest, a source-tree digest and
  the toolchain versions.
* Rebuilding gives a new versionCode, and therefore a new APK sha256. Take the values from that file.
* Signing key: `builder:~/keys/gbgpu.p12` (alias `gbgpu`, password file `~/keys/gbgpu.pass`). Backup in
  `C:\GB-build\keys\gbgpu.p12` and `gbgpu.pass` (same sha256, checked). It is never inside a repository, a component
  directory, `/build/dist*` or an image.

## 3. Exact changes Wave 2 must make to shared files

### 3.1 `C:\GB-x86\inject\native\components\gpu_ui.json` (W2-01): new file

Copy `C:\GB-build\native\gpu-ui\gpu_ui.json` byte for byte. Its content:

```json
{
 "name": "gpu_ui",
 "source": "C:\\GB-build\\native\\gpu-ui",
 "files": [
  {"src": "C:\\GB-build\\native\\gpu-ui\\out\\GbGpu.apk", "dest": "app/GbGpu/GbGpu.apk", "mode": "0644"},
  {"src": "C:\\GB-build\\native\\gpu-ui\\gb_gpu_ui_setup.sh", "dest": "vup/bin/gb_gpu_ui_setup.sh", "mode": "0755"}
 ],
 "rc_files": ["C:\\GB-build\\native\\gpu-ui\\gb_gpu_ui.rc"],
 "rc_text": "",
 "build_prop": []
}
```

This is compatible with today's loader (`build_esp.py` ~1259-1271, read-only check):
* `int("0644", 8)` gets `0o100000` added;
* an empty `rc_text` is skipped;
* `build_prop: []` adds nothing.

No `symlinks` or `payload` keys are used, so it can land before W2-08.

Verify after `build_esp.py`:
1. The build log line `native components: ...` contains `gpu_ui`.
2. The native cpio has `gbnative/app/GbGpu/GbGpu.apk` with mode 0644 and parent directories 0755. Its sha256 equals
   section 2.
3. `gbnative/vup/bin/gb_gpu_ui_setup.sh` has mode 0755.
4. `gbnative/etc/init/gb.rc` contains `# ---- component gpu_ui ----`, then
   `service gb_gpu_ui_setup /system/bin/sh /vendor/bin/gb_gpu_ui_setup.sh`, the `on post-fs-data` block with
   `mkdir /data/vendor/gb 0751 root shell` and `mkdir /data/vendor/gb/ui 0700 shell shell`, and
   `start gb_gpu_ui_setup` under `on property:sys.boot_completed=1`.
5. The VM (gbstage) cpio contains none of these paths.

### 3.2 `C:\GB-x86\build_esp.py` (W2-10): the no-priv-app assert only

The manifest loader already places `app/GbGpu/GbGpu.apk`. Add the assert from W2-10: fail the build if any
`_entries` path starts with `gbnative/priv-app` or `gbstage/priv-app` (`ro.control_privapp_permissions=enforce`
aborts boot).

Nothing else is needed:
* no `/odm/etc/permissions` file, because the app has no privileged permissions;
* no `build.prop` line;
* no SELinux file_contexts change (section 3.5).

### 3.3 `C:\GB-x86\verify_image.py` (W2-07): extra checks

All read-only, on the unpacked native cpio:
1. `gbnative/app/GbGpu/GbGpu.apk` exists. It is a zip with `AndroidManifest.xml`, `classes.dex` and a STORED
   `resources.arsc`. Its sha256 equals `C:\GB-build\native\gpu-ui\out\GbGpu.apk.sha256`.
2. No cpio path starts with `gbnative/priv-app/` or `gbstage/priv-app/`. This is W2-07 item 8; GbGpu does not change
   it.
3. `gbnative/etc/init/gb.rc` contains `service gb_gpu_ui_setup /system/bin/sh /vendor/bin/gb_gpu_ui_setup.sh`, and
   `gbnative/vup/bin/gb_gpu_ui_setup.sh` is mode 0755.
4. The gbstage (VM) tree contains no `app/GbGpu` and no `gb_gpu_ui_setup.sh` (invariant 19).
5. Optional: if Wave 2 has the SDK, run `aapt2 dump badging` (package `gb.sb3.gpu`, `targetSdkVersion:'36'`, only
   `uses-permission: name='android.permission.POST_NOTIFICATIONS'`).

### 3.4 `C:\GB-x86\inject\odm_sepolicy.cil` (W2-15): no change

* GbGpu runs as `untrusted_app` (platform seinfo `default`, targetSdk 36). The domains it involves are all
  `typepermissive` in today's cil (verified by grep): `untrusted_app`, `system_server`, `installd`, `dex2oat`,
  `shell` (gb_gpu_ui_setup, gb_notify, gb_power).
* The APK lives on gbinit's `/odm` tmpfs, labelled `u:object_r:tmpfs:s0` (VM `ls -Z`, read-only).
* If `untrusted_app` is ever made enforcing, GbGpu needs to:
  * connect to gb_power's socket: `allow untrusted_app socket_device:sock_file write;` and
    `allow untrusted_app shell:unix_stream_socket connectto;` (same as `gb-power.md` 3.5);
  * read its APK from the tmpfs: `allow untrusted_app tmpfs:file { read open getattr map };`, or better a
    file_contexts entry `/odm/app(/.*)? u:object_r:vendor_app_file:s0` applied by gbinit.

### 3.5 Repository docs (W2-14)

Add to the new `docs\NVIDIA.md` a short "Controls" section:
* the **NVIDIA GPU** Quick Settings tile (added once at first boot; states as in the component README);
* Settings > System > **NVIDIA GPU** (switch = persistent opt-in; trial period; apps using the GPU; compute
  allowlist; diagnostic report);
* the optional **Detach screen** tile (add it from the tile editor);
* "Takes effect after restart" in test/once boots;
* the recovery hint: press `e` in the boot menu and add `gb.nouveau=0`.

## 4. Wave 2 tests

### 4.1 UI-07 (VM, needs the user's approval before anything is installed)

The VM image carries no dGPU stack, so this only proves the app installs, binds its tile and shows `Unavailable`.
1. `adb -s 192.168.115.133:5555 install C:\GB-build\native\gpu-ui\out\GbGpu.apk`. As a user-installed app it gets
   role `other` (gb_power requires `/odm/app/GbGpu/GbGpu.apk`). The VM has no gb_power socket anyway.
2. `adb shell cmd statusbar add-tile gb.sb3.gpu/.GpuTileService`. Open Quick Settings: **NVIDIA GPU** shows
   `Unavailable` and taps do nothing (SystemUI ignores taps on UNAVAILABLE tiles).
3. `adb shell am start -n gb.sb3.gpu/.SettingsActivity`. The page shows `Status: Unavailable`, with the switch
   disabled.
4. `adb shell am broadcast -a gb.sb3.gpu.action.STATE_CHANGED -p gb.sb3.gpu` as shell is delivered (shell holds
   DUMP), and `logcat -s GbGpu` shows no crash.
5. Clean up: `adb shell cmd statusbar remove-tile gb.sb3.gpu/.GpuTileService`, `adb uninstall gb.sb3.gpu`.

The injected Settings entry does **not** appear for a user-installed app, because TileUtils accepts system apps
only. Only a native image (or a VM image with the APK in `/odm/app`) can show it.

### 4.2 Hardware (UI-HW, plan P7, user-run on the Surface Book 3)

1. After the first native boot:
   * `dumpsys package gb.sb3.gpu | grep -E "codePath|flags"` shows `/odm/app/GbGpu` and `SYSTEM`;
   * the tile is present once;
   * `/data/vendor/gb/ui/tile_added` exists (`ls -ld /data/vendor/gb /data/vendor/gb/ui`: 0751 root:shell, 0700
     shell:shell);
   * notifications are allowed for GbGpu.
2. Settings > System shows **NVIDIA GPU** with a working switch and a summary that matches the tile.
3. Enable from the tile: `Off`, then `Starting`, then `Standby`. Run `nvk_probe`: `Busy (1)` and the ongoing
   "NVIDIA GPU in use" notification. Tap: the dialog lists `nvk_probe` as not closable. Stop it: `Standby`. Tap:
   `Off`.
4. Detach (bound idle). The tile shows `Unavailable` within 2 s of the latch opening. Reattach: `Standby` again.
5. Once/test boot: taps give the toast **Takes effect after restart**.
6. Crash-loop recovery boot: `Off (recovered)`. Kill-switch boot (`gb.nouveau=0`): `Disabled for this boot`.

## 5. Tests run in Wave 1

All on builder 192.168.115.132, 2026-10-25, scratch `/build/native/dgpu/gpu-ui/`. Last full run: v1.1 fix pass,
`build_on_builder.sh all` (rebuild + every test), 10:21-10:24 UTC, every row PASS. Logs are in
`C:\GB-build\native\gpu-ui\out\test\` and the summary in `out\test\SUMMARY.txt`.

| Id | Result | Evidence |
|---|---|---|
| UI-01 | PASS | `check_apk.py` 63 checks, 0 failures. `package gb.sb3.gpu`; `versionCode = epoch minutes at build time`; `minSdkVersion 36`, `targetSdkVersion 36`; `Verified using v2 scheme ...: true`, `v3 ...: true`, v1 false; exactly one signer; `zipalign -c -p 4`; resources.arsc STORED and 4-aligned; no native code. |
| UI-02 | PASS | Manifest checks in `check_apk.py`: `exactly the 13.2 components`; `only SettingsActivity is exported without a permission`; `uses-permission is exactly POST_NOTIFICATIONS`. POST_NOTIFICATIONS is dangerous (0x1001) and not privileged. Guards are signature level: BIND_QUICK_SETTINGS_TILE 0x2000002, WRITE_SECURE_SETTINGS 0x4000132, DUMP 0x32, all read from android.jar's framework manifest. Android Lint 9.4.0-rc01: `No issues found.` Lint negative control (throwaway copy with minSdk 21 and an unused string): `44 errors, 10 warnings` (NewApi in all four Android classes, UnusedResources). |
| UI-03 | PASS | `unit: 160 checks, 0 failures`: all 8.3 rows, 14 unknown state values, persist edge cases, detach tile, exact texts, JSON, replies, validation. |
| UI-04 | PASS | `mock: 84 checks, 0 failures`: goldens byte-exact, roles, effects, errors, holders, close-holders, allowlist, malformed/oversized replies, watch push, reconnect after restart and after a garbage line, watch-limit polling, 9th connection, idle close. |
| UI-05 | PASS | `nosocket: 9 checks, 0 failures` (missing socket, stale socket, one onUnavailable while retrying), plus the stalled-server deadline group in UI-04. The measured times are the `INFO` lines in `nosocket.log` / `protocol-mock.log`. |
| MOCK | PASS | `test_mock.py` 58 checks. A 34-step session against the mock and the real gb_power v1.1 host build (`power\gb_power.c` sha256 `25bddd1f349e59680d97cf430f40767d595960edabf468c7f7c3aa87d4cf4415`, as recorded in `gb-power.md`): `all 34 replies identical (t_ms removed)`. |
| REAL | PASS | `real: 26 checks, 0 failures`: the app client against the real daemon (goldens, role ui, status mapping to Unavailable, enable/disable, ENODEV/ENOENT/ESTALE, watch push of `disabled`, next-boot effect). |
| UI-06 | PASS | `test_gb_gpu_ui_setup.sh` under mksh: 35 checks, 0 failures. `shellcheck -s sh`: 0 findings. |
| RC | PASS | `check_rc.py`: 16 checks (v1.1 adds the `post-fs-data` mkdirs), `gb_gpu_ui.rc is byte-identical to INTERFACES 15.4`. |
| Build | PASS | `javac -Xlint:all -Werror` clean (APK and host). d8, zipalign and apksigner ran without warnings. |
| UI-07 | NOT RUN | Wave 2, VM, needs approval (4.1). |
| UI-HW | NOT RUN | Hardware only (4.2). |

## 6. Spec questions and deviations (please resolve before Wave 2 edits)

1. **D-UI-1 `switch_uri` form (deviation).**
   * 13.2 gives `com.android.settings.switch_uri=content://gb.sb3.gpu.switches/nvidia_gpu`.
   * In settingslib `TileUtils.getCompleteUri()` (`nvresearch/ux/aosp/TileUtils.java:459-478`), a URI that already
     has a path segment is used as it is. `getMethodAndKey()` (`:641-651`) then takes the *first* segment as the
     provider method. So Settings would call method `nvidia_gpu` both for reading and for setting the switch.
   * The manifest therefore uses `switch_uri=content://gb.sb3.gpu.switches` plus `com.android.settings.keyhint=nvidia_gpu`.
     Settings then calls `isChecked` / `onCheckedChanged` with the key, as settingslib intends.
   * `SwitchProvider` also accepts the literal 13.2 form: method `nvidia_gpu`, told apart by the `checked_state`
     extra.
   * Authority and key are unchanged. Suggest updating 13.2 in spec v2.
2. **Additions beyond 13.2:**
   * an `android.service.quicksettings.action.QS_TILE_PREFERENCES` filter on SettingsActivity, so a tile long-press
     opens it;
   * `com.android.settings.summary_uri` (dynamic summary = tile subtitle) and `com.android.settings.icon`;
   * `uses-sdk 36/36` in the source manifest, so lint sees the levels (aapt2 sets the same);
   * `dataExtractionRules` excluding everything.
3. **Busy tap.** 8.3 says "dialog -> close-holders or disable".
   * Implemented: the dialog lists closable holders and the compute service. **Close apps** sends
     `close-holders <pids>` and `stop-compute`, waits up to 5 s for `busy` to end, then sends `disable`.
   * If apps still hold the GPU, gb_nouveau.sh refuses and posts N03 itself, so the app does not duplicate it.
   * Non-closable holders (root, system, `nvk_probe`) are listed as "Cannot be closed here".
   * Opened from Settings or from the in-use notification, the dialog only closes apps.
4. **`off` with persist=1.** The tile shows `On at next restart` and the tap is `enable` (literal 8.3). That changes
   nothing, because persist is already 1. The user cancels the pending enable in Settings (switch off).
   Suggest for v2: the tap on `off`+persist=1 should be `disable`.
5. **State broadcast trigger (component 2).** gb_power broadcasts only when the state enum or `n` changes (10.7). A
   `persist` change alone (e.g. `setprop` over adb) therefore does not refresh the active tile's subtitle until it
   is next bound.
   * The app's own actions refresh the tile themselves (`requestListeningState`).
   * Suggest for v2: also broadcast on a `persist` change.
6. **Stopped-package delivery (component 1; resolved in v1.1: gb_notify.sh has the flag).**
   * A system app is not in the stopped state after install, and binding its TileService clears the stopped state
     again after a user's force-stop.
   * Until then, the explicit broadcast is not delivered to a force-stopped GbGpu.
   * Adding `--include-stopped-packages` to gb_notify.sh's `am broadcast` line closes that gap. The intent stays
     explicit (`-p gb.sb3.gpu`), so it is harmless.
7. **Locked device.** Tile taps run through `unlockAndRun` when the keyguard is secure, which is not specified. The
   GPU is not switched, apps are not closed and detach is not requested from the lock screen.
8. **Holder labels.** Without `QUERY_ALL_PACKAGES` (protection level normal, not in 13.2), package visibility can
   hide other apps' labels, so the dialog may show package names.
9. **Detach tile subtitles** are component-defined: `In progress`, `Ready to detach`, none when idle.
10. **The watch is capped at 5 min per listening period** for the active GPU tile. SystemUI may not call
    `onStopListening` promptly for active tiles; the cap avoids holding one of gb_power's 4 watch slots
    indefinitely. The state broadcast rebinds the tile on every change.

## 7. Builder state left behind

* `/build/native/dgpu/gpu-ui/sdk/` (470 MB) is the official SDK, kept for rebuilds. `build_apk.sh` reinstalls it
  if removed.
* `/build/native/dgpu/gpu-ui/src/` is the last synced copy of the component, with `out/` and `build/`
  intermediates, a few MB.
* `spec/` and `ext/power/` are read-only input copies.
* The download cache (`dl/`, 181 MB), the lint experiments and the test work directories were removed.
  `run_tests.sh` removes its own `testwork/`.
* `/build` free space stayed above 10 GB throughout (11 GB at the end).

## 8. v1.2 (wave 1b): graphics switching

Contract: INTERFACES.md **v1.2** (12.3 v1.2 rows, 13.2 "(v1.2) GbGpu changes", 22, 24.3, 26, 27) and
`1b\TASKS_1B.md` v1.2 section 4 (T4.1-T4.13, UI-1B-01..13). Baseline: the TASKS 0.1 shas (`AndroidManifest.xml`
`40264996`, `GpuTileService.java` `c3cd7d6f`, `Status.java` `66b2071d`, `mock_gb_power.py` `3c26338d`, ...; all
re-verified before the change). Rollback copy: `C:\GB-build\native\gpu-ui\v1.1-sources.tar` (sha256
`84fcdf1b048f7a4dded69ace5994cf32f91ffc7cd1690cede6923fa7a5e19f0b`, 56 members). Real-server tests used the power
component's v1.2 source `power\gb_power.c` sha256 `c1986aad5e8340b3e816026d4ce015c645b6fceba2cd9c37e09faaec2ac79496`
(the one `gb-power.md` 7.2 lists). Date 2026-10-25. Nothing outside `C:\GB-build\native\gpu-ui\` and this directory
was written; no shared image file was touched.

### 8.1 What changed (by TASKS_1B task)

| Task | Change |
|---|---|
| T4.1 | `core/Status`: the 12 v1.2 fields of 9.5 (`gfx_mode` .. `apps_sync`), in `sameAs()`; a v1.1 status reads as `gfx_cap` 0 and `apps_sync` 1 (Unavailable, no push); an optional `gfx_test_reason` (Q1). |
| T4.2 | `core/GbPowerClient`: the 13 commands of 26.1 in the whitelist with their argument rules (validated before the socket); `exchangeAll(lines, timeout)` (one `hello`, lock-step replies, stop at the first error; 6 s for the catalog push); `getAllChoices()` (`get-apps` paging); `Outcome.replied()` and `validLine()` for tests. |
| T4.3 | `core/StateMapper`: `graphicsTile()` = the 17 rows of 27.1 (records the matching row), `nextMode()`, `modeLabel()`, `effectToast()`, `summary()` = `<Mode label>: <subtitle>`; `switchChecked()` and `nextBoot()` removed; the v1.1 `gpuTile()` kept only for the v1.1 host tests (8.3 note). |
| T4.4 | `core/Texts`: `TILE_LABEL = "Graphics"`, every exact string of 27.1, 27.2, 27.4, 27.6, 12.3 v1.2; error texts of the new commands. |
| T4.5 | New pure-Java models: `GraphicsPage` (27.2), `AppRows` (24.4, one choice per package, revert on failure, hints), `CatalogBuilder` (24.3 filter and classes, games first, <= 512, lines <= 255 bytes), `NotifModel` (in-use text, detach post/cancel, `ui-caps`), `HoldersModel` (27.4 modes, plan, detach progress), `Labels`. |
| T4.6 | `GpuTileService`: renders `graphicsTile()`; tap = `set-mode <nextMode(gfx_mode)>` with the 27.1 toasts; never a dialog; `unlockAndRun` on a secure keyguard (as v1.1). |
| T4.7 | `SettingsActivity`: the Graphics page (all 9 items of 27.2); the v1.1 switch (`setPersist()`, `bindToggle()`) removed; `loadAllowList()` kept; catalog push on open when `apps_sync` = 0; a runtime PACKAGE_ADDED/REMOVED/REPLACED receiver while the page is visible (D13). |
| T4.8 | `HoldersActivity`: `EXTRA_MODE` (`close` / `intel` / `detach`) replaces `EXTRA_THEN_DISABLE`; `disableNow()` removed; a fresh `list-holders` scan; detach progress `Preparing to detach...` -> closes on `dtx=confirmed`, failure text on a new `detach_ref`; `onNewIntent` restarts with the new mode (singleTask). |
| T4.9 | `Notifier`: in-use text `NVIDIA in use by: <labels>` + **Close apps**; channel `gb_gpu_detach` (Detach, HIGH), id 3 on a growing `detach_ref` (stored in SharedPreferences with `Settings.Global.BOOT_COUNT`), **Close apps and detach** + **Cancel**, cancel rules; `uiCaps()`. `StateReceiver`: status -> notifications, summary, tile; `ui-caps notify|none`; catalog push when `apps_sync` = 0. New `ActionReceiver` (not exported). |
| T4.10 | `SwitchProvider`: only `getDynamicSummary` / `getDynamicTitle`, key `graphics`. |
| T4.11 | Manifest: `QUERY_ALL_PACKAGES` (justified in a comment); tile and Settings labels `Graphics`; injection meta-data category `ia.system`, title `Graphics`, keyhint `graphics`, `summary_uri`, icon, **no** `switch_uri`; `.ActionReceiver` `exported=false`. `res/values/strings.xml`: labels and page strings. |
| T4.12 | `gpu_ui.json` gains the seed entry; new empty `gfx_app_prefs.default`. |
| T4.13 | `test/mock_gb_power.py` (v1.2: 23.2 derivation with a simulated OP_MARK delay, 26.2 effects, 24.4 opt-in, per-connection catalog staging, simulated self-test and pending detach, the real daemon's error strings and order), `test_mock.py` (26.3 goldens, the two mock == real sessions, `--gfx-tree`), `UnitTests`, new `SpecTests` and `Fx`, `ProtocolTests` (rewritten groups + `real12`), `check_apk.py` (UI-1B-10), `lint.xml`, `run_tests.sh`. |

`gb_gpu_ui.rc`, `gb_gpu_ui_setup.sh`, `build_apk.sh`, `WatchSession`, `Conn`, `Json`, `Reply`, `Transport`,
`LocalSocketTransport`, `DetachTileService` are unchanged.

### 8.2 Artifacts (sha256; these replace section 2 for Wave 2)

| Artifact | sha256 | Consumer |
|---|---|---|
| `C:\GB-build\native\gpu-ui\out\GbGpu.apk` (versionCode 29882262, 65960 bytes, built 2026-10-25 13:43 UTC) | `87a71359b15837e44d1435239eabfd8984e7dd483d62d089daa6bbe0205792f7` | `gpu_ui.json` -> `app/GbGpu/GbGpu.apk` |
| `C:\GB-build\native\gpu-ui\gfx_app_prefs.default` (0 bytes) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | `gpu_ui.json` -> `etc/gb/gfx_app_prefs.default` (0644) |
| `C:\GB-build\native\gpu-ui\gpu_ui.json` | `ef867551a09bbcad58f2cd89d99d4a55bbd8f1d3622b775b8e6b80ab14427be1` | copy to `C:\GB-x86\inject\native\components\gpu_ui.json` (W2G-05) |
| `C:\GB-build\native\gpu-ui\gb_gpu_ui_setup.sh` (unchanged) | `312cf0c5ba38d4dce97d41681c9e70521f8c4a812b1c89764b1a703aae43b899` | `vup/bin/gb_gpu_ui_setup.sh` |
| `C:\GB-build\native\gpu-ui\gb_gpu_ui.rc` (unchanged, = INTERFACES 15.4) | `1c8ca925e0c78f84a49c2451c3ab7132ddc00071ec4214765a3f95fce36307bd` | `rc_files` |
| Signer certificate (v2/v3, same key as wave 1) | `686174802bdf4f4196682d030dc2649a8f4e35cb5bab4caad13cbe5cf4619ed1` | verify_image (optional) |
| Sources: `AndroidManifest.xml` `7254cee5`, `Gb.java` `c4ac9fae`, `GpuTileService.java` `7e3c3830`, `SettingsActivity.java` `21b72382`, `HoldersActivity.java` `cc0e0b6d`, `Notifier.java` `12fb071c`, `StateReceiver.java` `4392b3c1`, `SwitchProvider.java` `96fcb826`, `ActionReceiver.java` `f7c27973`, `core/Status.java` `e1c6b355`, `core/GbPowerClient.java` `60272928`, `core/StateMapper.java` `d2d8552e`, `core/Texts.java` `ffe7cdc6`, `core/GraphicsPage.java` `6001930d`, `core/AppRows.java` `60d4f378`, `core/CatalogBuilder.java` `d6770054`, `core/NotifModel.java` `b939e393`, `core/HoldersModel.java` `f6908e11`, `core/Labels.java` `17d62282`, `res/values/strings.xml` `2a6b8c80`; tests `mock_gb_power.py` `7824ed71`, `test_mock.py` `5ef72d36`, `check_apk.py` `61f50ae5`, `lint.xml` `7f92078b`, `UnitTests.java` `20872453`, `SpecTests.java` `6bae2676`, `ProtocolTests.java` `6a809518`, `Fx.java` `d14d4061`, `AllTests.java` `86dd62ad`, `run_tests.sh` `6fed02c7` | (first 8 hex) | `out\BUILD_INFO.txt` `sources_sha256` = `aee685ea...` |

Rebuilding gives a new versionCode and APK sha256; take the values from `out\BUILD_INFO.txt`. The 1b APK replaces the
wave-1 APK in place (same package, same key, higher versionCode), so a device that had the v1.1 APK updates cleanly;
the QS tile component name is unchanged, so an added tile stays.

### 8.3 Tests run (builder 192.168.115.132, 2026-10-25 13:41-13:46 UTC, `build_on_builder.sh all`; logs in `C:\GB-build\native\gpu-ui\out\test\`)

| Id | Result | Evidence (log) |
|---|---|---|
| UI-1B-10 (+ UI-01) | PASS | `check_apk.log`: `96 checks, 0 failures`: `uses-permission is exactly POST_NOTIFICATIONS + QUERY_ALL_PACKAGES`, `QUERY_ALL_PACKAGES is protection level normal (granted at install): 0x0`, `POST_NOTIFICATIONS ... (protectionLevel 0x1001)`, guards `BIND_QUICK_SETTINGS_TILE` 0x2000002, `WRITE_SECURE_SETTINGS` 0x4000132, `DUMP` 0x32; `exactly the 13.2 (v1.2) components: [... '.ActionReceiver' ...]`; `tile label 'Graphics'`, `Settings title 'Graphics'`, `no switch_uri`, `summary_uri content://gb.sb3.gpu.switches without a path`, `keyhint graphics`, `no launcher icon`, `ActionReceiver not exported`; v2 + v3, no v1, one signer; dex has the 13 new command strings, `gb.sb3.gpu.action.CANCEL_DETACH`, `gb.sb3.gpu.extra.MODE`, `gb_gpu_detach`, and none of `checked_state`, `set_checked_error`, `nvidia_gpu`, `THEN_DISABLE`; QUERY_ALL_PACKAGES justification present (manifest + lint.xml). |
| UI-1B-13 | PASS | `lint.log`: `No issues found.` (Android Lint 9.4.0-rc01 over manifest, res, src, classes); host `javac --release 17 -Xlint:all -Werror` of 23 files clean; APK `javac -source 8 -target 8 -Xlint:all -Werror` clean. Lint negative control (QueryAllPackagesPermission suppression removed, throwaway copy): `AndroidManifest.xml:24: Error: A <queries> declaration should generally be used instead of QUERY_ALL_PACKAGES ... [QueryAllPackagesPermission]`, `lint exit 1`. |
| UI-1B-01, -02, -03, -05..-09 (+ UI-03) | PASS | `unit.log`: `unit: 535 checks, 0 failures` (groups UI-1B-01 graphicsTile, -02 nextMode, -03 tap mapping, -05 GraphicsPage, -06 AppRows, -07 CatalogBuilder, -08 NotifModel, -09 HoldersActivity logic, UI-03 legacy 8.3, detach tile, texts, JSON, replies, validation). |
| SPEC | PASS | `spec.log`: `spec: 147 checks, 0 failures`: 27.1 (17 rows, both columns), toasts and cycle, 27.6 (16 + other), 27.4 (3 modes), 27.2 labels/templates/help text, 12.3 and 13.2 v1.2, all read from INTERFACES.md v1.2. Negative control with 3 edited strings in a scratch copy: `spec: 147 checks, 4 failures`. |
| UI-1B-11 (+ UI-05) | PASS | `nosocket.log`: `16 checks, 0 failures`; `missing socket: NO_SERVER after 39 ms`, `watch without a socket: first onUnavailable after 2 ms`; stalled server `TIMEOUT after 2002 ms` (`protocol-mock.log`). |
| UI-1B-04 (+ UI-04) | PASS | `protocol-mock.log`: `mock: 182 checks, 0 failures` (18 groups: 9.6 and 26.3 goldens through the app's framing, roles, every 26.2 effect, error texts, run-selftest, set-app-* and 128 paged choices, list-holders and both dialogs incl. the failing detach, ui-caps, catalog push of 5 and 512 apps and a count-mismatch stop, then the wave-1 groups). |
| UI-1B-12 (+ MOCK) | PASS | `mock-conformance.log`: `178 checks, 0 failures`; `A: all 85 replies identical (t_ms removed)` (wave-1 34 steps + 51 v1.2 lines, wave-1 tree, `-D`); `B: all 47 replies identical` (26.3 tree, `GB_POWER_TEST_DTX=1`); `B: real golden 26.3 get-mode` and `B: mock golden 26.3 get-mode` byte-exact. |
| REAL | PASS | `protocol-real.log`: `real: 38 checks, 0 failures` (wave-1 tree; tile, summary `Auto: Unavailable`, page cap-0 rules; `next-attach`, `next-boot` with `booted=0`; catalog push; watch push). |
| REAL12 | PASS | `protocol-real12.log`: `real12: 34 checks, 0 failures` (26.3 tree; tile `NVIDIA`, page lines from the real record `Self-test: Passed on D1761390000`, catalog push `n 1, dropped 1`, Always Intel removes the game from the opt-in list, tap cycle with `NVIDIA (manual)` after the daemon's worker re-enabled NVIDIA). |
| UI-06, RC, SC-SH, SC-BASH, PY, SEED | PASS | `setup-script.log` 35 checks; `rc.log` 16 checks, `gb_gpu_ui.rc is byte-identical to INTERFACES 15.4`; shellcheck 0 findings; py_compile; `gfx_app_prefs.default is empty (24.2) and gpu_ui.json installs it as etc/gb/gfx_app_prefs.default 0644 (T4.12)`. |
| Read-only VM | PASS | `adb -s 192.168.115.133:5555 shell`: `settings get global boot_count` -> `57`; `dumpsys package permission android.permission.QUERY_ALL_PACKAGES` -> `prot=normal`; POST_NOTIFICATIONS `prot=dangerous\|instant`; `cmd package query-activities -a android.service.quicksettings.action.QS_TILE_PREFERENCES` -> 16 activities; `updatable_driver_all_apps` -> `null`, `ro.gfx.driver.0` empty (VM-1B-02 baseline unchanged). Nothing installed or changed. |
| VM-1B-05, UI-1B-HW-1, H-1B-* (app parts) | NOT RUN | need the user's approval / hardware (8.7). |

Builder etiquette: load below 0.3 at every start, no `gbiso` job running, `/` 27 GB and `/build` 12 GB free throughout,
heavy steps at nice 19 / ionice idle, scratch only under `/build/native/dgpu/gpu-ui/` (`testwork-dev/` removed).

### 8.4 Deviations and interpretations (UI-1B-Dn)

1. **D1 `gfx_cap` 0 with a working socket.** 27.2: "No socket or `gfx_cap` 0: the page shows `Unavailable` and every
   control is disabled." The page disables every *graphics* control (radios, self-test, all-apps switch, per-app
   switches) and shows `Unavailable`, but keeps the compute allowlist and the diagnostic report usable while gb_power
   answers. Reason: W2G-01 allows `GB_GFXDRIVER=0` images (no package, `gfx_cap` 0) on which nouveau still loads for
   compute; disabling the wave-1 compute allowlist there would regress wave 1. Without a socket (VM) everything is
   disabled, as specified.
2. **D2 summary without a status** is `Unavailable` (no mode label is known); with a status it is always
   `<Mode label>: <tile subtitle>` (e.g. `Auto: Unavailable` on an image without the package).
3. **D3 `intel` dialog and the compute service.** 27.4 lists `close-holders` for mode `intel`; when the compute service
   runs, the dialog also lists it and sends `stop-compute` (as mode `close`), because the Intel switch cannot finish
   while the service holds nouveau.
4. **D4 detach with only the compute service holding** (no closable app pid): `close-holders-and-detach` needs at
   least one pid (ESTALE otherwise), so the dialog sends `stop-compute` and then `detach-request`; gb_power's normal
   decision applies every gate.
5. **D5 "the value the app saw last"** is stored together with `Settings.Global.BOOT_COUNT` (readable on lapis: 57):
   `detach_ref` is a per-boot counter, so a value from an earlier boot counts as 0; a value that went backwards in the
   same boot (gb_power restarted) also restarts the count. Without this the first refusal after a reboot would be
   missed.
6. **D6** The in-use and detach texts use `<N> app(s)` while the holder list is still empty (gb_power rate-limits the
   holder scan, 2.3); otherwise the 12.3 label rule (label, else package, else comm; 5 names, then `and N more`). The
   compute service's process is shown as `NVIDIA compute service`.
7. **D7 self-test reason.** `Failed on <date>: <reason>` / `Could not run: <reason>` need the record's `reason`, which
   neither the 9.5 status nor `get-mode` carries (the record is in a root-only 0700 directory). The page shows
   `Failed on <date>` / `Could not run` and appends `: <reason>` only if gb_power reports `gfx_test_reason` (Q1).
   The date is the device's medium date format of `gfx_test_t`.
8. **D8** The per-app list shows the catalog apps (launchable, non-system); choices that gb_power keeps for packages
   outside the catalog (uninstalled apps, 24.2) are not listed. The effective graphics per row is the 24.4 formula as
   UI-1B-06 says, independent of the current mode; the status line above it tells what apps get right now.
9. **D9** The application label stays `NVIDIA GPU` (13.1: descriptive; v1.2 renames only the tile and the Settings
   entry to `Graphics`).
10. **D10** The v1.1 `gpuTile()` mapping and its texts are kept for the v1.1 host tests, as the 8.3 note says; the app
    does not use them.
11. **D11** The detach notification is evaluated on every status the app receives (broadcast, watch, page), not only
    on broadcasts, and de-duplicated by the stored `detach_ref`; `ui-caps` is sent on every handled broadcast (27.3).
12. **D12** `run-selftest` errors: `EBUSY` shows gb_power's message (`The self-test cannot run now (Intel only mode)`),
    any `EAGAIN` shows "try again in a moment" (gb-power Q3 / GP-1B-D11).
13. **D13 PACKAGE_* receivers.** The wave-1b task text asks for PACKAGE_* receivers; INTERFACES 24.3 instead drives
    the push from `apps_sync` (gb_power watches packages.list, sets `apps_sync` = 0 and broadcasts), because
    ACTION_PACKAGE_ADDED/REMOVED/REPLACED are implicit broadcasts that manifest receivers of a targetSdk 36 app do not
    get. Both are done: the background path is the spec's; while the Graphics page is visible a runtime receiver
    (`RECEIVER_NOT_EXPORTED`) for those three actions refreshes the list and pushes at once. A push that races
    PackageManager's packages.list write only drops the new package; the next `apps_sync` = 0 broadcast pushes again.
14. **Obsolete:** D-UI-1 (the `switch_uri` form) no longer applies (no switch); `summary_uri` uses the same no-path form
    with keyhint `graphics`, so Settings calls `getDynamicSummary` with key `graphics` (settingslib
    `TileUtils.getCompleteUri()` `:459-478`). Wave-1 items 6.3 (busy tap), 6.4 (`off` with persist=1) and 6.8
    (holder labels) are resolved by v1.2 (tap = mode cycle; QUERY_ALL_PACKAGES).

### 8.5 Spec questions

* **Q1** Add the record's `reason` to the status or `get-mode` (e.g. `gfx_test_reason`, <= 40 characters of
  `[a-z0-9 :._-]`), so the page can show `Failed on <date>: <reason>` as 27.2 asks (D7)? The app already reads it if
  present.
* **Q2** Confirm D1 (compute section usable with `gfx_cap` 0 when gb_power answers).
* **Q3** Confirm D5 (per-boot `detach_ref` keyed by BOOT_COUNT); an alternative is a boot id in the status object.

### 8.6 Wave 2 edits to shared files (replace sections 3.1, 3.3 and 3.5 for v1.2)

1. **`C:\GB-x86\inject\native\components\gpu_ui.json` (W2-01 / W2G-05):** copy `C:\GB-build\native\gpu-ui\gpu_ui.json`
   (sha256 `ef867551...`) byte for byte:
   ```json
   {
    "name": "gpu_ui",
    "source": "C:\\GB-build\\native\\gpu-ui",
    "files": [
     {"src": "C:\\GB-build\\native\\gpu-ui\\out\\GbGpu.apk", "dest": "app/GbGpu/GbGpu.apk", "mode": "0644"},
     {"src": "C:\\GB-build\\native\\gpu-ui\\gb_gpu_ui_setup.sh", "dest": "vup/bin/gb_gpu_ui_setup.sh", "mode": "0755"},
     {"src": "C:\\GB-build\\native\\gpu-ui\\gfx_app_prefs.default", "dest": "etc/gb/gfx_app_prefs.default", "mode": "0644"}
    ],
    "rc_files": ["C:\\GB-build\\native\\gpu-ui\\gb_gpu_ui.rc"],
    "rc_text": "",
    "build_prop": []
   }
   ```
   Still no `symlinks`/`payload` keys, no build.prop line (`ro.gfx.driver.0` comes from `gfxdriver.json`, W2G-01).
   Check after `build_esp.py`: the native cpio has `gbnative/etc/gb/gfx_app_prefs.default` (0 bytes, 0644) next to
   section 3.1's items 1-4; the VM cpio has none of them.
2. **`C:\GB-x86\verify_image.py` (W2-07, extends 3.3 and W2G-06):** native tree: the APK sha256 equals
   `out\GbGpu.apk.sha256`; `/odm/etc/gb/gfx_app_prefs.default` exists, is a regular 0-byte file, mode 0644; optional
   badging check now expects exactly `uses-permission: name='android.permission.POST_NOTIFICATIONS'` and
   `uses-permission: name='android.permission.QUERY_ALL_PACKAGES'`. VM tree: no `app/GbGpu`, no `gb_gpu_ui_setup.sh`,
   no `gfx_app_prefs.default` (W2G-06 already lists the last one).
3. **`build_esp.py`:** nothing for this component beyond 3.2's no-priv-app assert (QUERY_ALL_PACKAGES is a normal
   permission; no `/odm/etc/permissions` file).
4. **SELinux (`odm_sepolicy.cil`, W2-15):** no change (3.4 still holds; the app reads PackageManager through binder).
5. **Docs (`docs\NVIDIA.md`, W2-14 / W2G-08):** replace 3.5's "Controls" with: the **Graphics** tile (tap cycles Auto
   -> Intel only -> NVIDIA, long-press opens the page; the subtitles of the component README); Settings > System >
   **Graphics** (modes, status, self-test and Run self-test again, Use NVIDIA for all apps, per-app Use NVIDIA /
   Always Intel, compute allowlist, help text, diagnostic report); the **Can't detach yet** notification with Close
   apps and detach; the optional **Detach screen** tile; recovery (`gb.nouveau=0`, `setprop persist.vendor.gb.gfx_mode
   intel`).

### 8.7 Hardware-only and VM checks (not run)

* **VM-1B-05 (needs the user's approval; extends 4.1 / UI-07):** on a throwaway VM, `adb install
  C:\GB-build\native\gpu-ui\out\GbGpu.apk`; `cmd statusbar add-tile gb.sb3.gpu/.GpuTileService`: the tile is
  **Graphics** / `Unavailable` and taps do nothing; `am start -n gb.sb3.gpu/.SettingsActivity`: `Unavailable`, every
  control disabled; `am broadcast -a gb.sb3.gpu.action.STATE_CHANGED -p gb.sb3.gpu` causes no crash (`logcat -s GbGpu`);
  `settings get global updatable_driver_all_apps` stays `null`; remove the tile, `adb uninstall gb.sb3.gpu`.
* **UI-1B-HW-1:** Settings > System shows **Graphics** with the dynamic summary; Settings search finds it; the tile's
  long-press opens the page.
* **App parts of H-1B-04/-06/-07:** after the first boot gb_power's log shows the push (`control: app catalog
  committed by ui uid <appId>: <n> apps (<d> dropped), <k> opted in`) and `updatable_driver_production_opt_in_apps` lists the installed games; after
  installing an app the catalog is pushed again; a detach refused with an app on NVIDIA posts **Can't detach yet**,
  **Close apps and detach** ends with `Ready to detach`/the latch open, **Cancel** only dismisses; the tile cycle and
  the radios; the in-use notification's **Close apps**; HoldersActivity `Preparing to detach...` closes on confirm.
* Wave-1 UI-HW items 1, 5, 6 of 4.2 still apply in their v1.2 form (tile texts of 27.1 instead of 8.3).

### 8.8 Contract notes for the other components

* **gb-power:** GbGpu sends, as role `ui`: `status`/`watch` (tile, page, notifications), `get-apps` (page), `set-mode`
  (tile tap, radios), `set-app-policy`, `set-app-intel`, `set-app-nvidia` (page), `run-selftest` (page),
  `list-holders` + `close-holders` / `close-holders-and-detach` (dialog), `stop-compute`, `detach-request` (D4 and the
  Detach tile), `ui-caps notify|none` (each broadcast), `apps-begin`/`apps-add`/`apps-commit` on one connection when
  `apps_sync` = 0 (each broadcast and the page's open), `report`, `compute-*`. It never writes properties or Settings.
  It relies on: the 26.3 replies, the 9.5 field order, `detach_ref` growing on every refusal because of holders,
  `apps_sync` = 0 after every packages.list change, and the state broadcast after such a change (23.11).
* **nouveau-policy (gb_notify):** the broadcast stays `am broadcast --user 0 --include-stopped-packages -a
  gb.sb3.gpu.action.STATE_CHANGED -p gb.sb3.gpu` (DUMP-guarded receiver).

### 8.9 Builder state left behind

As section 7, plus: `/build/native/dgpu/gpu-ui/src/` holds the 1b copy with `out/` and `build/`; `ext/power/` now holds
the v1.2 `gb_power.c` (`c1986aad...`); `spec/INTERFACES.md` is v1.2. `testwork/` and `testwork-dev/` were removed.

## 9. v1.2 r2: review fix pass (INTERFACES 29)

**This section supersedes the APK sha256 of 8.2 and the test results of 8.3.** Applies R-16 (and follows R-6 in the
mock). Pre-fix sources: `C:\GB-build\native\gpu-ui\v1.2r1-sources.tar` (sha256 `5f054b64...`; holds the r1 APK
`87a71359...` and its `BUILD_INFO.txt`).

### 9.1 What changed

| Fix | Where | What |
|---|---|---|
| R-16 | `core/HoldersModel.java` `refusalShown()`, `detachFallback()`; `HoldersActivity.send()` | Detach mode: `refused` entries with reason `not-holder` (the app exited after `list-holders`) are not turned into the "could not be closed" problem, so the "Preparing to detach..." watch goes on; `ESTALE` from `close-holders-and-detach` (every listed app exited) sends a plain `detach-request` after `stop-compute` (when planned). Close and Intel modes are unchanged. |
| R-6 (mock) | `test/mock_gb_power.py` `cmd_compute_start()` | `EAGAIN` `NVIDIA driver starting` for state `loading` or the rebind watch, as the real gb_power r2. |
| tests | `test/java/.../UnitTests.java` | 5 R-16 checks in the HoldersModel group (UNIT 535 -> 540). |

No string, permission, manifest or component change; Settings/tile behaviour unchanged.

### 9.2 Artifacts (sha256; these replace 8.2's APK row)

| Artifact | sha256 |
|---|---|
| `out\GbGpu.apk` (versionCode 29882330, 65960 bytes, built 2026-10-25 14:51 UTC, same key and package; updates the r1 APK in place) | `d653940ac62898553deb77484e6238df3f18cf325ffb8462cde18497be414edf` |
| `out\BUILD_INFO.txt` `sources_sha256` | `52fd8406b1a158ffa41f5c5acbb71509da05de70132acff1228b743688a22dba` |
| `src\gb\sb3\gpu\HoldersActivity.java` | `1b567137d3ffc42a6b8b0612e75f661a65fee0d31ce37043dc45db2b13e488ae` |
| `src\gb\sb3\gpu\core\HoldersModel.java` | `075909725474aaae50f141be8b536f0d355fbd3a33092f916cc49a38931c4a13` |
| `test\mock_gb_power.py` | `85d1a21d408f3cae0d692b8ad2a2541e5d61af363067dbd7aeebd8addbe0b399` |
| `gpu_ui.json`, `gfx_app_prefs.default`, `gb_gpu_ui.rc`, `gb_gpu_ui_setup.sh`, signer | unchanged (8.2) |

### 9.3 Tests (builder 192.168.115.132, 2026-10-25 14:50-14:53 UTC, `build_on_builder.sh all`; `out\test\SUMMARY.txt`)

UI-01/1B-10 check_apk 96/96; lint exit 0; javac `-Xlint:all -Werror` 23 files; UNIT 540/540; SPEC 147/147 (against
INTERFACES v1.2 r2); UI-05 nosocket 16/16; UI-04 mock 182/182; MOCK 1B-12 178/178 (mock == the real gb_power r2 host
build `a8bf3f05...` on both trees); REAL 38/38; REAL12 34/34; UI-06 35/35; RC 16/16; shellcheck 0; py_compile; SEED.

### 9.4 Wave 2 delta

W2G-05 unchanged (`gpu_ui.json` byte copy). The APK the manifest points at is the rebuilt `out\GbGpu.apk`
(`d653940a...`). Hardware/VM checks of 8.7 unchanged; add the detach-mode case of H-1B-06: an app that exits between
the notification and the tap does not produce "Some apps could not be closed".
