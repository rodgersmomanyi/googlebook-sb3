> **Repository note.** This is the component's working README, copied from the development tree. Paths
> such as `C:\GB-build\native\gpu-ui` mean `native/gpu-ui` in this repository, `C:\GB-build\native\dgpu`
> is [`native/dgpu`](../dgpu/), `C:\GB-x86` is the workspace (the repository root) and `builder:` paths are
> on the Linux builder. Build outputs, logs and older versions (`out/`, `logs/`, `superseded/`, `*.v1.1`, ...)
> are not published. What the NVIDIA support does for a user: [docs/NVIDIA.md](../../docs/NVIDIA.md).

# gpu-ui: the GbGpu app (Graphics tile, Graphics page, notifications)

**Wave 6 (notify, headless system user mode, 2026-10-30):** lapis runs in headless system user mode: user 0 is the
headless "System User" (login screen), the person uses a full user (10). Measured on lapis 276.R4: a plain `pm grant`
granted POST_NOTIFICATIONS to user 0 only, `cmd statusbar add-tile` at boot went to user 0's tiles, gb_notify's
`am broadcast --user 0` reached only user 0's GbGpu (whose `ui-caps notify` then suppressed gb_power's N08 while its
detach notification was invisible), and lapis's SystemUI drops `requestListeningState` for the tile ("Couldn't find
tile ...(10)"). Changes: `gb_gpu_ui_setup.sh` grants every full user and adds the tile once per user while a full user
is in the foreground (markers `tile_added.<u>`); new non-exported `.UserReceiver` (BOOT_COMPLETED, permission
RECEIVE_BOOT_COMPLETED) refreshes at each user's unlock; `.StateReceiver`/`.UserReceiver` do nothing in the headless
system user (`core/UserPolicy`); the tile is no longer an ACTIVE_TILE (bound whenever the QS panel is shown). gb_notify
now broadcasts to every running full user (nouveau-kernel). INTERFACES 12.1a, 13.2. APK `7888b882...` (versionCode
29889106, same signer `68617480...`); proof: `C:\GB-build\native\w6prep\notify\RESULT.md`.

**Wave 6 (readiness B-4, 2026-10-30):** gb_power reports `gfx_reason` = `model` when gb_nouveau refused a dGPU this
system has no driver for (`vendor.gb.nouveau.result=refused:model`: a 15-inch Surface Book 3's TU116/TU106). The app
shows it: tile row 17 `Intel (NVIDIA not supported)` / `NVIDIA unavailable - not supported` (the old row 17
"otherwise" is now row 18), status line `Apps you open now use: Intel (this NVIDIA GPU is not supported)`, GPU line
`NVIDIA GPU: not supported (no driver)` (INTERFACES 27.1, 27.2, 27.6). The mock derives it too. APK `b5108d54...`
(versionCode 29888620); the wave-5 APK `d653940a...` is kept as `out\prev-w6\GbGpu.apk`.

**v1.2 r2 (review fix pass, INTERFACES section 29, 2026-10-25):** HoldersActivity's detach mode ignores
`not-holder` refusals and falls back to `detach-request` on `ESTALE` (R-16); the mock follows gb_power's new
`compute-start` `EAGAIN` rule (R-6). APK `d653940a...` (versionCode 29882330); every test re-run PASS
(`integration\ux-app.md` section 9). The pre-fix sources and APK are in `v1.2r1-sources.tar`.

Component 6 of the dGPU work. Contract: `C:\GB-build\native\dgpu\INTERFACES.md` **v1.2** (graphics switching:
sections 22-28, mainly 24.3, 26, 27, 12.3 and 13.2) and `C:\GB-build\native\dgpu\1b\TASKS_1B.md` v1.2 section 4. Wave 2
notes: `C:\GB-build\native\dgpu\integration\ux-app.md` (section 8 is v1.2). The v1.1 sources (the TASKS_1B 0.1
baseline) are kept as `v1.1-sources.tar` for rollback; nothing in it is built or shipped.

Status: built and tested without hardware (host JVM, the mock server, the **real gb_power v1.2 host build** on two fake
trees, the spec text itself, Android Lint, static APK checks). Nothing has run on a device yet: see "Hardware-only and
VM checks" below.

## What the user gets (the requirement)

"Seamless transition between the intel graphics and NVIDIA graphics ... with a GUI for manual change": the decisions
D1-D8 (INTERFACES 22.1) are implemented by gb_power and the other components; this app is the GUI.

* **Quick Settings tile "Graphics"** (`.GpuTileService`, same component name as v1.1, so a tile the user already added
  stays). It shows what apps you open now get: `NVIDIA`, `NVIDIA (busy 2)`, `Intel`, `Unavailable (base detached)`,
  `Off (Intel mode)`, `Fault`, ... (the 18 rows of INTERFACES 27.1, with separate texts in the manual `NVIDIA` mode,
  e.g. `NVIDIA unavailable - base detached`). A **tap cycles the mode Auto -> Intel -> NVIDIA -> Auto** (`set-mode`,
  never a dialog; toasts `Takes effect after restart`, `Takes effect when the keyboard base is attached`, `Switching
  to Intel when apps on NVIDIA close`). A **long-press opens the Graphics page**. Taps need an unlocked device.
* **Settings > System > Graphics** (`.SettingsActivity`, injected; the summary is e.g. `Auto: NVIDIA (busy 2)`):
  1. mode radios **Auto - recommended** / **Intel only (battery)** / **NVIDIA** with their secondary texts; an Intel
     switch while apps still use NVIDIA shows `<apps> still use the NVIDIA GPU.` and **Close apps**;
  2. `Apps you open now use: NVIDIA` or `... Intel (<reason>)` with the 16 reason texts of 27.6;
  3. `NVIDIA GPU: asleep (D3cold)` / `active` / `no driver` / `not connected`, plus the apps using it;
  4. the self-test result (`Passed on <date>`, `Failed on <date>`, `Not run yet`, `Running...`, `Could not run`) and
     **Run self-test again** (enabled exactly per 27.2);
  5. **Use NVIDIA for all apps** (policy `all` / `games`);
  6. the per-app list: every app you installed that has a launcher entry, games first, with a `Game` badge, the
     effective graphics (`NVIDIA` / `Intel`, the 24.4 formula) and two switches **Use NVIDIA** and **Always Intel**
     (one choice per app; Always Intel always wins; a failed change reverts the switch); hints for apps that use
     NVIDIA right now;
  7. the NVIDIA compute service state and the wave-1 compute allowlist;
  8. the exact help text "How graphics switching works" (system UI always Intel; open apps switch on restart);
  9. the wave-1 diagnostic report button and the `gb.nouveau=0` recovery hint.
  Without a socket, or with `gfx_cap` 0 (VM, image without the NVIDIA app-graphics package), the page shows
  `Unavailable` and the graphics controls are disabled.
* **Notifications** (12.3 v1.2): ongoing **NVIDIA GPU in use** / `NVIDIA in use by: <apps>` with **Close apps**; when a
  detach is refused because apps hold the GPU (`detach_ref` grew): **Can't detach yet** / `<apps> are using the NVIDIA
  GPU. Close them to detach the screen.` with **Close apps and detach** and **Cancel** (channel `gb_gpu_detach`, id 3).
  The app declares `ui-caps notify` on every state broadcast while its notifications are allowed, so gb_power does not
  also post its N08; `ui-caps none` otherwise. Tablet/base messages (N16, N17, N18, N19) come from gb_power.
* **Close-apps dialog** (`.HoldersActivity`, modes `close`, `intel`, `detach`, 27.4): lists a fresh `list-holders`
  scan; **Close apps** sends `close-holders` (gb_power force-stops only verified closable holders); in detach mode
  **Close apps and detach** sends `close-holders-and-detach` and shows `Preparing to detach...` until the EC confirms
  (the dialog closes) or the refusal repeats (`Some apps could not be closed: <apps>`).
* **App catalog** (24.3, 27.5): on every state broadcast (and when the page opens) with `apps_sync` = 0 the app lists
  the launchable apps of user 0 without `FLAG_SYSTEM` (stock and updated system apps excluded, `gb.sb3.*` excluded),
  classes `g` (CATEGORY_GAME or the legacy FLAG_IS_GAME) and `a`, at most 512 games first, and pushes it on one
  connection (`apps-begin`, `apps-add` lines <= 255 bytes, `apps-commit`). gb_power turns it into the
  `updatable_driver_*` Settings (only gb_power decides, 24); the app never writes Settings. `apps_sync` is 0 at every
  boot and after every packages.list change (install, update, removal), so this covers "on boot and on package
  add/remove/replace" (D3) without a manifest package receiver (those implicit broadcasts do not reach one); while the
  Graphics page is visible a runtime PACKAGE_ADDED/REMOVED/REPLACED receiver also refreshes the list and pushes at once.

## Permissions and exported components (UI-1B-10)

* `POST_NOTIFICATIONS` (dangerous; granted by `gb_gpu_ui_setup.sh`) and `QUERY_ALL_PACKAGES` (**normal**, granted at
  install; `prot=normal` read on the lapis VM). Nothing privileged; no `/odm/priv-app`.
* QUERY_ALL_PACKAGES justification (also in the manifest comment and `test/lint.xml`): the catalog must see every
  launchable app with its `ApplicationInfo.flags`/`category` (24.3), and the holder names come from any package that
  holds the GPU (12.3, 27.4). The list goes only to the local gb_power socket, which re-checks every package against
  packages.list. A lint negative control (suppression removed) reports exactly `QueryAllPackagesPermission` on the
  manifest line.
* Exported: the two tiles (BIND_QUICK_SETTINGS_TILE), `.SettingsActivity` (unguarded: Settings must start it; it only
  shows state, every change is a user action), `.SwitchProvider` (WRITE_SECURE_SETTINGS; serves only
  `getDynamicSummary`/`getDynamicTitle`, no switch any more), `.StateReceiver` (DUMP). Not exported: `.HoldersActivity`,
  the new `.ActionReceiver` (the notification's Cancel).

## Protocol client (INTERFACES 9, 26)

* `src/gb/sb3/gpu/core/` is pure Java with no `android.*` imports, so the same code runs on the host JVM.
  * `GbPowerClient`: one connection per call (`hello`, role check, one command, one reply, 2 s deadline);
    v1.2 adds `exchangeAll(lines)` (one `hello`, then the lines in lock-step, stop at the first error reply; used for
    the catalog push, 6 s deadline) and `getAllChoices()` (`get-apps` paging via `next`).
  * Every request is validated before the socket: the 26.1 commands and their argument rules (`set-mode
    auto|intel|nvidia`, `set-app-policy games|all`, `set-app-intel|nvidia <pkg>:0|1`, `get-apps [<0..128>]`,
    `apps-begin|commit <0..512>`, `apps-add g|a:<pkg>[,<pkg>...]`, `close-holders-and-detach <pids>`, `ui-caps
    notify|none`); package names `[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+` <= 127.
  * `Status` reads the 9.5 object with the 12 v1.2 fields (a v1.1 server reads as `gfx_cap` 0 -> Unavailable).
  * `StateMapper` (27.1 tile, cycle, toasts, summary; the v1.1 8.3 mapping is kept only for the v1.1 host tests),
    `GraphicsPage` (27.2), `AppRows` (24.4 per app), `CatalogBuilder` (24.3), `NotifModel` (12.3/27.3),
    `HoldersModel` (27.4), `Labels` (label, else package, else process name; 5 names then `and N more`), `Texts`
    (every exact string).
* `WatchSession`, `Conn`, `Json`, `LocalSocketTransport` are unchanged from v1.1.

## Files

| Path | Purpose |
|---|---|
| `AndroidManifest.xml`, `res/` | Manifest (v1.2 permissions, components, injection meta-data); strings, themes, two vector icons, data-extraction rules. |
| `src/gb/sb3/gpu/core/*.java` | Protocol client and the host-tested models. |
| `src/gb/sb3/gpu/*.java` | Android layer: tiles, Graphics page, summary provider, receivers (state, Cancel), dialog, notifications, LocalSocket transport, catalog query (`Gb`). |
| `gb_gpu_ui.rc`, `gb_gpu_ui_setup.sh` | rc unchanged (INTERFACES 15.4). (Wave 6) setup per user: `pm grant --user <u>` for every full user, `add-tile` once per user while that full user is in the foreground (`tile_added.<u>`). |
| `gpu_ui.json` | Wave 2 component manifest: APK, setup script, rc, and (v1.2) the empty `gfx_app_prefs.default` seed -> `/odm/etc/gb/gfx_app_prefs.default` 0644. |
| `gfx_app_prefs.default` | The per-app choices seed, empty (INTERFACES 24.2: "ships empty"). |
| `build_apk.sh`, `build_on_builder.sh`, `run_tests.sh` | Build (unchanged toolchain) and the test runner. |
| `test/` | `check_apk.py`, `run_lint.sh` + `lint.xml`, `java/` (UnitTests, SpecTests, ProtocolTests, fixtures), `mock_gb_power.py` (v1.2 mock), `test_mock.py` (goldens + mock == real), `test_gb_gpu_ui_setup.sh`, `check_rc.py`. |
| `out/` | `GbGpu.apk` (+ `.sha256`), `BUILD_INFO.txt`, the rc and setup script copies, `test/` logs. |
| `v1.1-sources.tar` | Rollback copy of the v1.1 sources (manifest, res, src, test, runner; sha256 `84fcdf1b...`). Not built, not shipped. |

## Build

From Windows (Git Bash): `bash C:/GB-build/native/gpu-ui/build_on_builder.sh` (or `build` / `test`). It copies the
component, `dgpu\INTERFACES.md` (for the rc and spec tests) and the power component's `gb_power.c` + `uapi/` (for the
real-server tests) to `builder:/build/native/dgpu/gpu-ui/`. Toolchain, signing and versioning are unchanged from wave 1:
official SDK via `sdkmanager` in `builder:/build/native/dgpu/gpu-ui/sdk` (platform 36, build-tools 36.1.0, lint 9.4),
JDK 17, `javac -source 8 -target 8 -Xlint:all -Werror`, d8 `--min-api 36`, v2+v3 signature with
`builder:~/keys/gbgpu.p12` (backup `C:\GB-build\keys\`; signer SHA-256 `686174802bdf4f4196682d030dc2649a8f4e35cb5bab4caad13cbe5cf4619ed1`),
versionCode = epoch minutes. No Gradle, Maven or AndroidX.

## Tests (latest run 2026-10-25 13:43 UTC, `out/test/SUMMARY.txt`; all PASS)

| Id | What | Result |
|---|---|---|
| UI-01, UI-1B-10 | `check_apk.py`: badging, versionCode, v2+v3 only, one signer, alignment; exactly the v1.2 components; tile and Settings title `Graphics`, keyhint `graphics`, `summary_uri` without a path, **no `switch_uri`**, no launcher icon, `ActionReceiver` not exported; permissions exactly POST_NOTIFICATIONS (0x1001) + QUERY_ALL_PACKAGES (0x0, normal) judged by android.jar's framework manifest; guards signature level; the QUERY_ALL_PACKAGES justification present; dex has the v1.2 commands and intents and none of the v1.1 switch strings | 96 checks, 0 failures |
| UI-02, UI-1B-13 | Android Lint 9.4 over sources, resources and classes; `javac -Xlint:all -Werror` (APK and host) | `No issues found.`; lint negative control finds the QUERY_ALL_PACKAGES line |
| UNIT (UI-1B-01..03, 05..09, UI-03) | 27.1 every row in both mode columns, busy 1..999, 15 unknown values; the cycle; tap mapping and toasts; GraphicsPage (radio, 18 status texts, 13 self-test cases, the rerun rule over 1728 combinations, pending line, GPU line, no-socket and cap-0 pages); AppRows against an independent 24.4 oracle (12 single cases + 4800 random rows), mutual exclusion, revert, hints; CatalogBuilder (FLAG_SYSTEM stock and updated, gb.sb3.*, game classes, 600 -> 512, every line <= 255 bytes, counts); NotifModel (5 names + `and N more`, post/keep/cancel for `detach_ref` incl. new boot and gb_power restart, `ui-caps`); HoldersModel (3 modes, commands, progress); plus the kept v1.1 groups | 535 checks, 0 failures |
| SPEC | The 27.1 table (17 rows x both columns), toasts, 27.2 labels and help text, 27.4 table, 27.6 table, 12.3 and 13.2 v1.2 strings, read from INTERFACES.md itself; negative control (3 edited strings) gives 4 failures | 147 checks, 0 failures |
| UI-05, UI-1B-11 | No socket (NO_SERVER after 39 ms), stale socket, stalled server (TIMEOUT after 2002 ms): tile Unavailable, page Unavailable with every control disabled, catalog push and `get-apps` fail cleanly | 16 checks, 0 failures |
| UI-04, UI-1B-04 | Against the v1.2 mock over a real UNIX socket: 9.6 and 26.3 goldens byte-exact; roles; every 26.2 `set-mode` effect with its toast; `enable`/`disable` mapping; EPERM/ENODEV/EINVAL/EBUSY/EAGAIN/ENOENT texts; run-selftest and the self-test line; `set-app-*` through AppRows, 128 choices paged; `list-holders`; close and detach dialogs incl. the failing detach; `ui-caps`; catalog push (5 apps, 512 apps in one connection, count-mismatch stop); plus the wave-1 groups (holders, compute allowlist, malformed replies, deadline, watch, reconnect, watch limit, connection limit) | 182 checks, 0 failures |
| MOCK, UI-1B-12 | Mock conformance; then the mock and the **real gb_power v1.2 host build** (`power\gb_power.c` sha256 `c1986aad...`) answer A: the wave-1 34-step session plus 51 v1.2 lines on the wave-1 tree, and B: 47 v1.2 lines on the 26.3 tree (DTX attached through the host-only EC) **identically** (`A: all 85 replies identical`, `B: all 47 replies identical`) | 178 checks, 0 failures |
| REAL | The app client against the real daemon, wave-1 tree: goldens, role ui, v1.2 status fields, tile/summary/page Unavailable (`gfx_cap` 0), next-attach and next-boot effects, errors, catalog push, watch push | 38 checks, 0 failures |
| REAL12 | The app client against the real daemon, 26.3 tree: golden get-mode, tile `NVIDIA`, summary `Auto: NVIDIA`, page lines from the real record, catalog push (gb_power drops the uninstalled package), Always Intel / Use NVIDIA, policy, `list-holders`, ESTALE text, the full tap cycle Auto -> Intel -> NVIDIA (tile `NVIDIA (manual)` once gb_power re-enabled NVIDIA) -> Auto | 34 checks, 0 failures |
| UI-06, RC, SC-SH, SC-BASH, PY, SEED | setup script under mksh (35 checks), rc byte-identical to 15.4 (16 checks), shellcheck, py_compile, the empty seed listed in `gpu_ui.json` | PASS |

Read-only VM facts (192.168.115.133, lapis CL3B.260622.276.R4, SDK 37), nothing installed: `settings get global
boot_count` = 57 (readable; the app keys its stored `detach_ref` by it); QUERY_ALL_PACKAGES `prot=normal`;
POST_NOTIFICATIONS `prot=dangerous|instant`; 16 activities already declare `QS_TILE_PREFERENCES`; VM-1B-02 baseline
unchanged (`updatable_driver_all_apps` null, `ro.gfx.driver.0` empty).

## Hardware-only and VM checks (not run)

* **UI-1B-HW-1:** Settings > System shows **Graphics** with the dynamic summary; Settings search finds it; the tile's
  long-press opens it (INFERRED from settingslib; the category was verified on the VM, evidence E2).
* **VM-1B-05 / UI-07 (needs the user's approval):** `adb install` on a throwaway VM: tile `Graphics` / `Unavailable`,
  taps do nothing, page `Unavailable` with disabled controls, clean uninstall (steps in `ux-app.md` 8.6).
* **H-1B-04, -06, -07 (app parts):** the catalog reaches gb_power at first boot and after an install (Settings keys);
  `Can't detach yet` with **Close apps and detach** ends with the latch open; the tile cycle and the page radios;
  QS long-press; the in-use notification's **Close apps**.
* The first native boot facts of wave 1 still apply (PackageManager scans `/odm/app/GbGpu` as a system app, role `ui`,
  tile added once, POST_NOTIFICATIONS granted).

## Deviations and interpretations (details in `integration/ux-app.md` 8.4)

UI-1B-D1 compute allowlist and report stay usable with `gfx_cap` 0 while the socket works; D2 summary without any
status is `Unavailable`; D3 the `intel` dialog also stops the compute service; D4 detach with only the compute
service holding: `stop-compute` + `detach-request`; D5 the stored `detach_ref` is per boot (BOOT_COUNT) and resets
when gb_power restarted; D6 `N app(s)` while the holder list is still empty; D7 the self-test reason is left out (not
in the socket API); D8 the page lists catalog apps only; D9 app label stays `NVIDIA GPU`; D10 legacy `gpuTile()` kept
for the v1.1 tests; D11 the detach notification is evaluated on every status the app sees, de-duplicated by
`detach_ref`; D13 package changes: the spec's apps_sync path plus a runtime receiver while the page is visible. D-UI-1 (switch_uri form) is obsolete: there is no switch; `summary_uri` uses the same no-path form.
