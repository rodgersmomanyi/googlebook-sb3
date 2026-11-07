# Googlebook OS on the Surface Book 3: portable handoff for any agent

Edition: 2026-11-08, fifth edition (Googlebook is installed on the internal SSD next to Windows and starts; the work
continues with Claude Code inside Googlebook: audio first, then NVIDIA). Keep it current. Every agent that changes
something adds a line to the [state log](#6-state-log) at the end.

This file lets an agent continue the project with no other context. It works for:

* Claude Code on the owner's Windows PC (the Surface Book 3 itself),
* Claude Code inside Googlebook OS (in the Terminal app's Debian VM),
* Google Antigravity's agent inside Googlebook OS.

It is written to be safe to publish: no personal data, no secrets, no binaries. Paths such as `C:\GB-build\...`
are the Windows layout on the laptop. Section 2.3 maps them to the public repository.

On Windows there is also a richer, **private** handoff: `C:\GB-build\HANDOFF.md`. It is local only and never
published. Its section 0 is the latest state. Read it after this file.

Two placeholders stand for the owner's own locations on Windows: **`<owner folder>`** (the owner's working folder on
D:) and **`<owner share>`** (the owner's network share, mapped as drive `Y:` on the laptop; the `.MOVED-TO-Y.txt`
notes refer to it). Their real paths are in private `HANDOFF.md` section 0 (the 2026-11-07 dual-boot bullet).

Tags used below: **[checked]** = seen working in a record or a test. **[not checked]** = nobody has tried it on this
laptop yet. **[inference]** = a reasoned guess, not tested.

---------------------------------------------------------------------------------------------------------------------

## 1. The project and its final state

### 1.1 What it is

* Google's **Googlebook OS**: the Android 17 desktop build "lapis", **CL3B.260622.276.R4**, x86_64.
* Made to run on a **Microsoft Surface Book 3 13.5"**: Intel Core i7-1065G7 (Ice Lake), NVIDIA GTX 1650 Max-Q in
  the detachable keyboard base, 32 GB RAM, one 1 TB NVMe SSD.
* It runs in four ways:

| way | what | where |
|---|---|---|
| VMware VM (daily use on Windows) | the owner's VM, VM tree `5663d3ce` | `C:\GB-x86\lapis-boot.vmx`, disk `C:\GB-x86\lapis.bin` |
| USB stick | a disk image written with Rufus (DD mode) | `C:\GB-x86\out\googlebook-sb3-usb.img` |
| Dual boot on the internal SSD | Googlebook next to Windows; Windows stays the default. **Installed 2026-11-08** with the installer ISO's option 2, and it starts (section 1.4) | `C:\GB-build\native\dualboot\` (the scripts, the laptop kit) + the install state `C:\GB-backup\20261108-000125-iso\` |
| Installer ISO | a bootable installer stick (whole disk / dual boot / uninstall / tools) | `D:\GB-iso\googlebook-sb3-installer.iso` |

* The project's own kernel is **k16**: `6.18.21-android17-5-gace3ce479bb5-ab16302716` (Google's GKI android17-6.18
  plus the project's patches).

### 1.2 Final outputs (wave 11, FINAL, released 2026-11-07)

| item | identity | where |
|---|---|---|
| USB image | sha256 `e43a463bcd259797d26d6c02ea9fbad93a54b43daeedff978e1823c2d214fb85`, 28,814,885,376 bytes | `C:\GB-x86\out\googlebook-sb3-usb.img` (+ `.sha256`, `README-FLASH.md`); since 2026-11-07 ~09:08 the image, `.sha256` and `build_usb.log` are in `<owner share>\Release\` (section 2.1) |
| Installer ISO | sha256 `4f4164a61b562fd85e059a3205c79f710bd44a82cc3a936ea6067b76aa062bdf`, 7,472,865,280 bytes, build 20261107-023858-ee3ff99d | `D:\GB-iso\` (+ `.sha256`, `README-ISO.md`) |
| Dual-boot export | `manifest.json` sha256 `80f62559697e8837259c25a926b28203aa0558e5ae264e33c051524eb22d3c21`, slot b | `D:\gb-export\` |
| Public repository | branch `main` at commit `b1146d4` | GitHub (local working copy `C:\GB-repo\googlebook-sb3`) |
| Manual 1.3 | 304 pages, local document, not published | `<owner folder>\Googlebook-OS-Surface-Book-3-Guide.docx` / `.pdf` |
| Owner's VM | tree `5663d3ce` (unchanged by wave 11) | `C:\GB-x86\lapis.bin` |

Tests of the final release (records in `C:\GB-build\native\backlog\BACKLOG.md` section 17.1-17.2):

* VM-1 on a throwaway VM: every check passed.
* ISO tests in VMware: 12 test ids, 378 checks, 0 failed.
* Two independent audits (image, ISO): 0 HIGH, 0 MEDIUM.

### 1.3 The owner's decisions on record

1. **No more feature waves** (2026-11-06 17:05). Wave 11 was the last one. From now on only small, targeted fixes
   for real hardware findings. Each fix gets its own review, rebuild, VM-1 test and, if the ISO changes, the ISO
   tests.
2. **Follow Google updates automatically** (2026-11-06 ~19:45). Native Googlebook installs Google system updates
   (OTAs) by itself. `gb_slotsync` stays as it is. The risk is documented: after an update, the next start runs a
   Google build that nobody tested with this image, and nothing falls back by itself.
3. **Public repository: source only.** Apache-2.0 for the project's own work, GPL-2.0 for kernel parts. A push
   happens only after the audit in rule 3.2.4.
4. **The lock-screen PIN is the owner's.** Agents never set, clear or test a PIN.
5. **2026-11-07: dual boot with 120 GiB after a rebuilt, smaller D: (Route A), and agents inside Googlebook.**
   * Why a rebuild: Windows cannot shrink C: or D:. An NTFS file that cannot be moved (`$Mft::$BITMAP`) sits at the
     end of both (Windows' Defrag event 259; for D: Windows offered only 636 MB). So the two earlier plans (120 GiB
     off the end of D:, then off the end of C:) are given up. **C: is not shrunk.**
   * The owner's decision: D:'s own data is copied to `C:\D-staging` and checked file by file (SHA-256), D: is
     deleted and created again at the same start with 278,120 MiB (271.6 GiB), the data is copied back and checked
     again, and Googlebook is installed with the installer ISO's option 2 (Route A) into the 122,881 MiB then left
     between D: and Recovery. Before that, every Googlebook item on D: moved to `<owner share>` (decision 6), and a
     checked safety copy of D:'s own data was made in `<owner share>\Backups\`. Only Windows' own tools change the
     disk (robocopy, `Remove-Partition`, `New-Partition`, `Format-Volume`, Disk Management), and the owner runs every
     step. Route B (`install_dualboot.ps1`) is not used: it would shrink D: itself.
   * Expected result (the installer's own planning code, run in memory on the laptop's real partition table with
     the new D:): one region "after partition #4" (or #5 if Windows puts the new D: into a later table slot) from
     byte 894,415,405,056; 18 Googlebook partitions (#6 to #23) between D: and Recovery; userdata 108,715 MiB
     (106.17 GiB); 1 GiB left free before Recovery. C:, D: and Recovery keep #3, #4 and #5. Nothing selects a
     partition by "D:" or by position.
   * **Result: done by the owner between 2026-11-07 ~18:05 and 2026-11-08 ~00:40** [checked]. D: was
     copied, checked, rebuilt at 278,120 MiB as #4 and restored (the file-by-file comparison of the source and the
     restored D: passed). Googlebook was installed into the gap with ISO option 2 ("region 1: after partition #4",
     18 partitions #6-#23, 1 GiB left free, Recovery #5 unchanged). The import ran (`C:\GB-backup\20261108-000125-iso\`
     plus the pointer `googlebook_install.json`). The owner's partition check (GUIDE 6.4) after the first start:
     #1-#5 unchanged, `gbdisk verify ... --layout-only` "all 18 partitions OK". Hibernation and Fast Startup are off.
     `C:\D-staging` has been deleted, the Defender exclusions are removed and D:'s shares are back.
   * The owner's guide: `C:\GB-REBUILD\REBUILD-D-AND-INSTALL-GOOGLEBOOK.md` (local only; copies in `<owner share>`
     and `<owner folder>`). It covers the rebuild step by step; the install follows the dual-boot guide
     `C:\GB-build\native\dualboot\GUIDE-DUALBOOT-120GB.md` (third edition, written for the C: route), section 4
     A5-A10, with the numbers in the rebuild guide's section 5 (the second edition's layout: Googlebook after D:).
   * **One USB stick**: it holds the installer ISO. So: `s` at the installer's partition-table-copy prompt, no tools
     `w`, and the import with `Import-GooglebookIsoInstall.ps1 -EspId <GUID>` (dry run first). The owner's
     read-only GPT backups are in `C:\GB-backup\pre-iso-20261107\`: `pre_iso` and `pre_rebuild` (the old D:;
     `pre_rebuild` brings the old D: back only until the new D: is formatted) and `post_rebuild` (the new D:; an
     interrupted install is restored from this one). An agent copies them to `<owner share>\Backups\`. There is no
     Googlebook USB stick and no Surface recovery drive in this plan.
   * With one stick the installer's journal and its own partition-table copies exist only in memory: a power-off or
     restart erases them. After a failed install the owner photographs the `FAILED:` line, the journal screen and
     tools `l`. After a power cut the dual-boot guide (A8) decides from the import's dry run and from option 2's
     read-only checks: `[FAIL] gpt_invalid` -> restore `post_rebuild` with `gbdisk restore-gpt` (dry run first);
     `[FAIL] already_installed` -> option 3. Never restore `pre_iso` or `pre_rebuild` once the new D: is formatted.
   * Never change D:'s size while Googlebook is installed (the import and `uninstall_dualboot.ps1` start from D:),
     and never create a partition in the 1 GiB gap before Recovery. [inference] A Windows feature update or WinRE
     update that shrinks C: and adds a Recovery partition right after it would put that partition between C: and D:
     and shift the partition numbers; option 3 still works. The owner runs the guide's partition check after such
     updates.
   * Undo: ISO option 3, or `uninstall_dualboot.ps1` (its default `-ExtendDriveLetter D`) after the import; then the
     owner gives the space back to D: (`Resize-Partition -DriveLetter D -Size 420478976000`, the old 401,000 MiB) or
     leaves it free. The project texts that say "extend D:" (the installer's option 3, `README-ISO.md`,
     `README_DUALBOOT.md`, `uninstall_dualboot.ps1` step 4) are right for this install; the third edition of the
     dual-boot guide says C: in those places.
   * The owner goes **straight to the internal install**. There is no separate stick test first.
   * The checks of the old "final hardware test" (battery level, camera test, lid, charger) are done after the first
     internal start.
   * The owner wants to run Claude Code (or Antigravity) inside Googlebook OS, so that an agent can look at problems
     while the OS runs. This file supports that (section 4).
   * This file goes to `<owner folder>` and the top level of `<owner share>` (private copies) and, after the lead's
     audit, to the public repository as `docs/HANDOFF-PORTABLE.md` (`collect.py` has that rule since 2026-11-07: rule
     3.2.4). Until it is published, the owner copies it into Googlebook with a USB data stick (section 4.4).
6. **2026-11-07: project files to `<owner share>`.** The owner asked to move the project's files off the laptop to
   the share, to free space. Every move copies, checks the copy (the SHA-256 of every file, read back from the
   share), removes the source and leaves a `.MOVED-TO-Y.txt` note in the old place. This is the only sanctioned
   removal of files that an agent did not create (rule 3.3.2). When Claude Code's permission check blocks an
   agent's delete, the owner runs it; the agent never works around the check.
   * The layout of `<owner share>` (the map with the state of each item: `<owner share>\INDEX.md`):
     * the top level: the checked copy of the owner's VM with its spare-PC guide `HOW-TO-RUN-ON-ANOTHER-PC.md`, the
       map `INDEX.md`, and a copy of the owner's rebuild guide;
     * `Release\`: the release USB image (section 2.1);
     * `Workspace\C\<path>` and `Workspace\D\<path>`: working files under their laptop paths. To bring an item back,
       copy `Workspace\C\X` to `C:\X` (or `Workspace\D\X` to `D:\X`), then run `Workspace\restore_fixups.py`: it
       recreates the hard links and symbolic links that were stored as notes (`<name>.HARDLINK.txt`,
       `<name>.SYMLINKS.tsv`) and checks every SHA-256 against `<name>.MANIFEST.sha256`;
     * `Archive\`: unused files (a separate Surface Pro 11 project that was in `<owner folder>`);
     * `Backups\`: copies of `C:\GB-backup\` (the dual-boot GPT backups, later the install state), and
       `Backups\D-safety-copy-20261107\`, the checked safety copy of D:'s own data for the rebuild (its own
       `README.txt`, `restore_safety.py`, MEMBERS / MANIFEST lists and `LINKS.txt`).
   * Done on 2026-11-07: the builder VM's disks (168 GB) -> `Workspace\C\GB-build\`; every Googlebook item on D:
     (`D:\GB-iso`, `D:\GB-vm-backup`, the `D:\gb-w*` test and prep folders, `D:\gb-test`, `D:\gb-export.build.log`;
     311 GB of data, 112 GB stored because sparse files stay sparse) -> `Workspace\D\`; the Surface Pro 11 project
     -> `Archive\`. The owner also deleted five superseded copies (the wave-8 and wave-10 ISO sets, the unreleased
     wave-11 candidate on D: and C:, and the full VM backup of 2026-10-26). `D:\gb-export` stays on D: (laptop kit).
     On 2026-11-08, after the install: `C:\GB-backup\` with the install state -> `Backups\GB-backup-20261108\` (25
     files, each SHA-256 read back from the share).
   * **The Googlebook VM does not run on the share's host PC** [checked 2026-11-07]: its AMD Zen 3 CPU has no GFNI,
     and the VM's first-stage init stops with SIGILL after about 4 s (kernel panic). The VM runs only on a CPU with
     GFNI, such as this laptop. The copy on the share stays the master for a PC that has GFNI.
   * **SSH to the share's host PC.** From Windows on the laptop, key-only SSH to that PC works through the SSH alias
     `gbhost` (in the Windows account's own SSH configuration; no key, account name or address belongs in any project
     file). That PC can host the Ubuntu **builder VM**, which does not need GFNI [inference: not run there yet]. Before
     the first run there: update VMware Workstation on that PC to its current release, and fix the absolute `serial0`
     path in `builder2.vmx`. The owner signs in there personally. Agents inside Googlebook have no key and never get
     one.
   * Still to move (the install is done, so this is next): `C:\GB-build` (except the laptop kit), `C:\GB-x86` (its
     `lapis.bin` is already on the share as the VM copy: never replace that master) and `C:\GB-repo`. The owner's
     list also names `C:\Googlebook-logs`, but rule 3.2.3 says agents never copy those logs: ask the owner first.
   * Building resumes by copying the workspace back to the same paths, on this laptop or on a spare PC (rule 3.3.11
     has the space limits).
   * A small **laptop kit** stays: `C:\GB-build\native\dualboot\` with what its scripts need (`gbdisk.exe`,
     `gb_common.ps1`, and a complete export: `install_dualboot.ps1 -RepairBootEntry` and `-UpdateEspOnly` refuse
     without `manifest.json` and all of its images; `D:\gb-export\` serves `-RepairBootEntry` only, because its
     partition GUIDs differ from an ISO install's), the log collector (`C:\GB-build\native\hwtest\`) and
     `C:\GB-backup\`. The owner's dual-boot guide also reads
     `C:\GB-build\native\w11prep\camera-hw3\NEXT_HARDWARE_CHECKS.md`; `README-ISO.md` is now in
     `<owner share>\Workspace\D\GB-iso\`.
   * Before `C:\GB-build` and `C:\GB-repo` move, this file is to be published to the repository (rule 3.2.4).
7. **2026-11-08: follow the Google update now, and continue inside Googlebook.**
   * The first internal start installed Google's update R4 -> **R5** (`CL3B.260622.276.R5`) into slot A by itself,
     and slot A became the boot default (decision 2 working as built). The owner chose (~01:15) to start R5 now
     rather than finish the tests on R4 first. Fallback: if R5 does not reach the lock screen within about 5
     minutes, hold Power for 10 s, start Googlebook again (section 2.4) and choose the entry ending `(slot B)`. This
     works only **before** R5 has completed one start. After that, the only way back to R4 is an uninstall plus a
     fresh install, which erases Googlebook's data (the Terminal's Debian VM and any agent work in it included).
   * After the first R5 start, and after every later Google update: the partition check (GUIDE 6.4, [Admin]) and a
     log collection (GUIDE 6.5, always with `-OutputRoot C:\Googlebook-logs`).
   * The owner continues with Claude Code inside Googlebook (section 4.2): first the **audio** (Windows sounds richer
     than Googlebook on the same speakers: section 5.1), then the **NVIDIA check** (section 5.2). Fixes stay small and
     only for real hardware findings (decision 1); they are built and tested on Windows (section 5).

### 1.4 Hardware results so far

* Three native test sessions ran with **earlier** images: wave 8 (2026-10-31), wave 9 (2026-11-01) and wave 10
  (2026-11-06).
* The third one (wave-10 image `3b6330d9`) gave 41 PASS / 0 FAIL / 1 UNKNOWN (suspend) in the log analyser:
  * the battery level was right;
  * both cameras showed a black preview;
  * a 34.25 MB Google update installed itself into slot A;
  * Settings and the Play Store showed "not responding" during the first start (the stick's write speed);
  * the Terminal app showed "Unrecoverable error" once, then Debian 13 worked.
* **The fourth session (2026-11-08) is the first start of the final image `e43a463b`, from the internal SSD**
  (ISO option 2; slot B, R4; one start that ended before 15 minutes). The owner saw: Play Store works, signed in;
  "Android Switch keeps stopping"; a 34.25 MB Google system update. The triage of the redacted logs
  (`TRIAGE-HW4.md`; where it is: private `HANDOFF.md` section 0), in short:
  * **The internal install works**, and the SSD cured the stick's slowness (`boot_completed` at 44.9 s, the stick
    88.7 s; no ANR, tombstone or SIGILL in 0-452 s; SELinux enforcing, 0 enforced denials). Analyser 38 PASS / 2
    FAIL / 2 UNKNOWN, and neither FAIL is a hardware defect. Windows' partitions were unchanged. The first NVK
    self-test pass on the hardware: app graphics on NVIDIA from 181 s.
  * FAIL `slotsync` = the R4 -> R5 update installed itself into slot A and is the default (decision 7). FAIL
    `dgpu_power` = all four snapshots caught the dGPU in D0, though it slept about 70 % of the time: most likely an
    analyser timing artefact (the live check: section 5.2). UNKNOWN: suspend (no lid test yet) and `audio_dras` (the
    log was cut; lapis's audio HAL runs on the card `sofhdadsp`).
  * "Android Switch keeps stopping" = Google's app `com.google.android.apps.restore`, three short crashes at about
    381-387 s. Record only: tap Close app, note the time, do not disable it.
  * The redacted log copy of this session still holds one account-linked value in 2 lines: never publish it or pass
    it on before fix F2 (section 1.5).
  * Not tested yet: lid and suspend, a camera picture, charger unplug, a detached start, idle on battery,
    HW-LOWSHUT, anything after about 7.5 minutes of uptime, and R5 itself. "About device" shows lapis's own model
    name and rounded sizes: cosmetic.
* The Googlebook **VM** fails on a PC with an AMD Zen 3 CPU (no GFNI: the first-stage init stops with SIGILL)
  [checked 2026-11-07, decision 6]. It runs on this laptop.

### 1.5 Still open (BACKLOG section 17.4, in short)

Every open item is HARDWARE (needs the laptop), FUTURE-OTA (needs a Google build newer than 276.R4), USER-ACTION
(only the owner can do or decide it) or, since 2026-11-08, HOST-FIX (a small fix of a Windows-side tool, no image
change). The full table, with the trigger for each row, is `C:\GB-build\native\backlog\BACKLOG.md` section 17.4. The
rows marked "new" come from the first internal start (section 1.4) and are not in the BACKLOG yet.

| id | severity | what is open | label |
|---|---|---|---|
| HW-FINAL-TEST | - | The one hardware test of `e43a463b`, on the internal install (decision 5). **Started 2026-11-08**: the first start passed its triage (1.4). Left: the rest of GUIDE 6.2: a start kept on for 16 minutes with the battery % compared with Windows' (within 1 %), the cameras, the lid, the charger unplug and replug, optionally a detached start and an idle start on battery, and HW-LOWSHUT at the very end. (The BACKLOG row still says "on a freshly written stick".) | USER-ACTION + HARDWARE |
| HW4-audio-thin | LOW | new: Googlebook sounds thinner than Windows on the same speakers. Windows adds Dolby and Realtek speaker processing; Googlebook plays flat (section 5.1). | HARDWARE |
| HW4-dgpu-idle | LOW | new: analyser item 25 FAIL (the dGPU was seen in D0 in every snapshot, but it slept about 70 % of the time). The live check is in section 5.2; the analyser fix is F1. | HARDWARE + HOST-FIX |
| HW4-mic | LOW | new [inference]: the built-in microphone's route points at a PCM device that did not exist on that start, so the built-in mic may record nothing (section 5.1, side finding). Check it with any recorder app. | HARDWARE |
| HW4-switch | - | new: "Android Switch keeps stopping" (Google's app). Record only, unless it repeats. | HARDWARE |
| F1, F2, F3 | LOW | new: F1 the analyser judges `dgpu_power` by its counters; F2 the log collector redacts Play Store's per-account value; F3 (optional) the analyser reads `audio_dras` from the audio diagnostics. All are host-only: no image rebuild. | HOST-FIX |
| HW-LOWSHUT | MEDIUM | The low-battery clean shutdown at 3 % has not been seen on the laptop. Until it passes: **do not run the battery below about 10 %**. | HARDWARE |
| HW3-camera | MEDIUM | The black camera preview. The opt-in camera self-test and the Camera app check on a second start decide where the fault is. A kernel-side fix would need k17, which is not planned. | HARDWARE |
| HW3-ota | MEDIUM | The first start into a Google build after an update is untested on this image. **Due now**: R5 is in slot A and is the default (decision 7). After that start: GUIDE 6.4 and 6.5. | HARDWARE + FUTURE-OTA |
| HW-SUSPEND, HW-S0IX | MEDIUM | Sleep and resume, and S0ix, never tried on the laptop. | HARDWARE |
| W11-SR-1 | MEDIUM | All app SELinux domains are permissive. Prepared only, on the shelf. | HARDWARE |
| HW3-anr | LOW | "Not responding" on a first start. A start without it closes the item. The first internal start had none in 0-452 s, but the stick's came later (7-15 minutes): a 16-minute start decides it. | HARDWARE |
| HW-PWR-IDLE, HW-HWCHECK, HW-SR2-SCRUB, HW-SR3 | LOW | The idle power draw; `gb_hwcheck.sh` once over Wireless debugging; the log privacy scrub on real logs; the collector folder's access rights. | HARDWARE (+ USER-ACTION) |
| W11-bt-usbdelay, W11-shelf, K17-items, older hardware rows | LOW / - | Small items that only matter if a hardware test asks for them. | HARDWARE |
| FUTURE-OTA rows | - | Checks that need a newer Google build. | FUTURE-OTA |
| First sign-in, VM automatic updates | - | The owner signs in to the VM once with the PIN; keep the VM's automatic updates off. | USER-ACTION |
| W11-upstream-drafts, W11-builder-upgrades, W11-move-rule, W11-shadow-copies, W11-builder-space, W8-dgpu-floor, superseded copies | LOW / - | Owner decisions and clean-ups. Agents never delete what they did not create. | USER-ACTION |

### 1.6 What happens next

Done (2026-11-07/08): the D: rebuild, the internal install and its import, the copy of `C:\GB-backup\` to
`<owner share>\Backups\`, the first internal start, the partition check, the log collection and the triage (1.4).
Next:

1. **The next Googlebook start runs R5** (decision 7). Charger in, nothing else planned for that start. Check that
   About shows `CL3B.260622.276.R5`, then the keyboard, touchpad, touch, Wi-Fi, sound and Play Store, and keep it on
   for 16 minutes. Do not tap "Restart now" in Android. Expected on that start, by design: the analyser's `ota_gate`
   item FAILs with "unvalidated", the AVX-VNNI libz patch is skipped, and a "System update not validated" notice
   appears (its advice to update the ESP from the host does not apply here: an ESP update from Windows refuses once
   the active slot changed). Then, on Windows: the partition check (GUIDE 6.4, [Admin]) and the log
   collection (GUIDE 6.5, with `-OutputRoot C:\Googlebook-logs`). Repeat 6.4 after **every** Google update.
2. **Claude Code inside Googlebook** (section 4.2; the starter prompt is 4.2 G): first the audio (section 5.1), then
   the NVIDIA check (section 5.2). The agent there diagnoses and records; image fixes come back to Windows (step 6).
3. **The remaining GUIDE 6.2 checks** (1.5, HW-FINAL-TEST), each on a start kept on for 16 minutes: the battery % next
   to Windows', the cameras, the lid, the charger, optionally a detached start and a quiet idle start on battery
   (Wi-Fi off, no apps: it also settles the dGPU's idle share and HW3-anr). Stay above about 10 % battery.
4. "Android Switch keeps stopping" again: tap Close app and note the time. Do not disable the app.
5. On Windows: the host-only fixes F1 and F2 (F3 optional) through the fix loop (section 5); then re-collect or
   re-redact the 2026-11-08 logs. Publish this file (rule 3.2.4); then the remaining C: project files move to
   `<owner share>` (decision 6).
6. Real hardware findings from steps 1-3 get small fixes, built and tested on Windows (section 5).

---------------------------------------------------------------------------------------------------------------------

## 2. Where things live

Since 2026-11-08 the project lives in three places:

| place | what is there | who reaches it |
|---|---|---|
| **Windows on the laptop** (C:, D:) | the private handoff, the backlog, the pipeline and native sources (until they move, decision 6), the owner's VM, the private logs, and the **laptop kit**: `C:\GB-build\native\dualboot\` (with `gbdisk.exe`, `Restart-ToGooglebook.ps1`), `D:\gb-export\`, the log collector `C:\GB-build\native\hwtest\`, `C:\GB-backup\` (the GPT backups and the install state `20261108-000125-iso\`) | agents on Windows (section 4.1) |
| **`<owner share>`** (the network share, drive `Y:` on the laptop; map: its `INDEX.md`) | top level: the checked copy of the owner's Googlebook VM (the master for another PC with GFNI; the original stays in `C:\GB-x86`) with `HOW-TO-RUN-ON-ANOTHER-PC.md`, `INDEX.md`, a copy of the rebuild guide and of this file; `Release\` = the release USB image; `Workspace\C\GB-build\` = the builder VM's disks; `Workspace\D\` = the old D: test, prep and release folders (`GB-iso`, `GB-vm-backup`, `gb-test`, `gb-w*`) with `Workspace\restore_fixups.py`; `Backups\` = `pre-iso-20261107\`, `GB-backup-20261108\`, `D-safety-copy-20261107\`; `Archive\` = an unrelated older project | Windows only, from a non-elevated window (an elevated window does not see the mapped drive). The share's host PC also by SSH (`gbhost`, decision 6); it cannot run the Googlebook VM |
| **Inside Googlebook** (the Terminal app's Debian VM) | `~/work/HANDOFF-PORTABLE.md` (this file, copied in), `~/work/googlebook-sb3` (the public repository clone), `~/work/notes/` (notes and patches that go out), `~/work/private/` (raw output that never goes out) | the agent inside Googlebook (section 4.2). An uninstall or reinstall of Googlebook erases all of it: copy the notes out often (4.4) |

How to switch between Windows and Googlebook: section 2.4.

### 2.1 On the Windows side of the laptop

D:'s Googlebook folders and the builder VM's disks are already in `<owner share>\Workspace\` under the same path;
now that the dual-boot install is done, most of the other folders follow (decision 6). Only the laptop kit stays.
Before you look for a file, check its old place for a `.MOVED-TO-Y.txt` note.

| item | Windows path | notes |
|---|---|---|
| Private handoff | `C:\GB-build\HANDOFF.md` | Local only, never published. Section 0 = latest state. |
| This file | `C:\GB-build\HANDOFF-PORTABLE.md` | Byte-identical copies in `<owner folder>` and at the top of `<owner share>`. The Windows file is the master (section 4.2 E). |
| Backlog | `C:\GB-build\native\backlog\BACKLOG.md` | Section 17 = the final release and everything still open. Local only. |
| Native components | `C:\GB-build\native\<component>\` | power, audio, sensors, iptsd, camera-*, dgpu, gatekeeper, ota-gating, dualboot, hwtest, ... |
| Prep areas (work folders) | `C:\GB-build\native\w11prep\<area>\` | Patches, tests and reviews before integration. Not published. |
| Dual-boot scripts | `C:\GB-build\native\dualboot\` | `install_dualboot.ps1`, `uninstall_dualboot.ps1`, `Import-GooglebookIsoInstall.ps1`, `Restart-ToGooglebook.ps1` (the owner's desktop shortcut "Restart to Googlebook" runs it: section 2.4), `gbdisk.exe`, `README_DUALBOOT.md`, `GUIDE-DUALBOOT-120GB.md`. Laptop kit: stays. |
| Hardware-test kit | `C:\GB-build\native\hwtest\` | `Get-GooglebookLogs.ps1` (log collector), `analyze_gblog.py` (log analyser), `check_stick_ota.py`. |
| Helper tools | `C:\GB-build\tools\` | Index: `C:\GB-build\tools\README.md`. Area tests: `tools\area_tests\`. Wave 11 tools and hardware checks: `tools\w11final\` (`hwchecks\` holds `camtest_check.py`, `power_report.py`). Never published (except `frametime.cs`). |
| Kernel | `C:\GB-build\kernel\` | Build scripts `kbuild<NN>_native.sh`, patches, `K16.md`. |
| Manual sources | `C:\GB-build\docs\manual\content\` | Chapter 07 = build and test, chapter 08 = install and operate, chapter 10 = appendix. |
| Builder VM (Ubuntu 24.04) | `C:\GB-build\builder2.vmx` | **The one to use.** 6 vCPU, 8 GB RAM. Disks `builder-root.vmdk` + `builder.vmdk` (168 GB together); since 2026-11-07 ~11:45 they are in `<owner share>\Workspace\C\GB-build\` (decision 6; note `C:\GB-build\BUILDER-VM-MOVED-TO-Y.txt`, sums `SHA256SUMS-builder.txt` next to them). To build, copy them back first and check the hashes; they fit on C: again, because C: is not shrunk (rule 3.3.11). Kernel tree `/build/gki`, outputs `/build/dist*`. |
| Old builder config | `C:\GB-build\builder.vmx` | The original install-time config. It points at the same `builder.vmdk`. **Never start it.** |
| Image pipeline | `C:\GB-x86\` | `build_esp.py`, `build_usb.py`, `verify_image.py`, `stage_kernel.py`, `inject\` (VM and native trees), `tests\`, `archive\` (pre-images). |
| Owner's VM | `C:\GB-x86\lapis-boot.vmx`, `C:\GB-x86\lapis.bin` | Never written by an agent (rule 3.1.3). |
| Release USB image | `C:\GB-x86\out\` | `README-FLASH.md`; older images in `out\prev\`. Since 2026-11-07 ~09:08 the image, its `.sha256` and `build_usb.log` are in `<owner share>\Release\` (moved at the owner's request, decision 6; the note `googlebook-sb3-usb.img.MOVED-TO-Y.txt` is left here). Copy them back and check the sha256 **before** a re-flash, `verify_image.py`, `compare_native.py`, the TB-3 / image area tests or an ISO build (`build_iso.py` reads this path). The note has the commands. |
| ISO sources and tests | `C:\GB-x86\iso\` | `build\build_iso.py`, `installer\`, `test\run_all.py`, `DESIGN.md`. |
| Installer ISO | `D:\GB-iso\`, since 2026-11-07 `<owner share>\Workspace\D\GB-iso\` | ISO, sidecar, `README-ISO.md`, `prev\` (note `D:\GB-iso.MOVED-TO-Y.txt`). The owner's one USB stick holds this ISO (decision 5). An ISO build writes below `D:\GB-iso\`: copy the folder back first. |
| Dual-boot export | `D:\gb-export\` | `manifest.json` + partition images. Laptop kit: stays, complete (`install_dualboot.ps1 -RepairBootEntry` needs it; decision 6). Its `super.img` is one file with several hard-linked names (`HARDLINKS.txt`). |
| VM backups | `D:\GB-vm-backup\`, since 2026-11-07 `<owner share>\Workspace\D\GB-vm-backup\` | Keep every folder. (The owner deleted the full backup of 2026-10-26 on 2026-11-07.) |
| Release records | `D:\gb-w11rel\`, since 2026-11-07 `<owner share>\Workspace\D\gb-w11rel\` | Build, test, ISO and final-step records of wave 11. The other `D:\gb-w*` test and prep folders moved the same way. |
| D: rebuild | `C:\GB-REBUILD\` | The owner's guide `REBUILD-D-AND-INSTALL-GOOGLEBOOK.md` (and `.txt`), its checker `treecheck.py` (one SHA-256 list per top-level folder, `compare`, `links`, `unlink`), its logs and SHA-256 lists. Local only. |
| Repository working copy | `C:\GB-repo\googlebook-sb3` | Generated by `collect.py` (section 2.3). Has the repository-local commit identity. |
| Owner's working folder | `<owner folder>` (real path: private `HANDOFF.md` section 0) | Manual 1.3 (local only), copies of this file and of the dual-boot guide. |
| Hardware-test logs | `C:\Googlebook-logs\<stamp>\` | **Private.** Only the `share\` subfolder is redacted. Rule 3.2.3. Always run the collector with `-OutputRoot C:\Googlebook-logs`: its default (`Documents\Googlebook-logs\` or `%USERPROFILE%\Googlebook-logs\`) can be a folder that is synced off the laptop. |
| Dual-boot install state | `C:\GB-backup\` | Laptop kit: stays. `pre-iso-20261107\` holds the owner's read-only GPT backups (`pre_iso` and `pre_rebuild`: the old D:; `post_rebuild`: the new D:, taken right before the install). The import of 2026-11-08 added `20261108-000125-iso\` (`install_state.json`, `plan.json`, `manifest_installed.json`, ...) and the pointer `googlebook_install.json`. Copy in `<owner share>\Backups\GB-backup-20261108\`. |
| Network share | `<owner share>` (real path: private `HANDOFF.md` section 0) | Layout (decision 6; map: `INDEX.md` at its top): top level = the checked **copy** of the owner's VM with `HOW-TO-RUN-ON-ANOTHER-PC.md`, for a spare Windows PC with GFNI (done 2026-11-07 ~09:10; the original VM stays in `C:\GB-x86`; it does not run on the share's own host PC), `INDEX.md` and a copy of the rebuild guide; `Release\` = the release USB image; `Archive\` = unused files; `Workspace\C\`, `Workspace\D\` = working files under their laptop paths, with `Workspace\restore_fixups.py` and a `<name>.MANIFEST.sha256` per item; `Backups\` = copies of `C:\GB-backup\` (`pre-iso-20261107\`, `GB-backup-20261108\`) and the D: safety copy `D-safety-copy-20261107\`; a copy of this file at the top. SSH to the share's host PC: decision 6. Look for `.MOVED-TO-Y.txt` notes in the old places before you look for an old file. |
| Laptop kit | see decision 6 | What stays on the laptop after the move: `C:\GB-build\native\dualboot\` with what its scripts need (including a complete `D:\gb-export\`), the log collector in `C:\GB-build\native\hwtest\`, and `C:\GB-backup\`. |

### 2.2 What an agent inside Googlebook OS can and cannot reach

Not reachable from inside Googlebook:

* The Windows partitions C: and D:. Android does not mount them. Nothing on this list can be opened there.
* The builder VM and the owner's VMware VM (they run on Windows).
* `<owner share>` (a Windows network share) and its host PC (the SSH key stays on Windows; never copy it in).
* The Googlebook ESP (`gblog\`, `gb\gb.cfg`). Only root services of the image mount it, and adb runs as the shell
  user.

Reachable:

* The public GitHub repository (`git clone` over Wi-Fi).
* Files the owner copies over (section 4.4), such as this file at `~/work/HANDOFF-PORTABLE.md`.
* The running Android system itself, as the shell user, through Wireless debugging (section 4.2 B) [not checked on
  the internal install yet].

So inside Googlebook an agent **diagnoses and prepares**. Building, testing and releasing happen on Windows.

### 2.3 Repository layout versus the Windows layout

The repository is **generated** from the Windows folders by `C:\GB-build\tools\repo_stage\collect.py`. It copies
sources only, applies path and privacy transforms, and refuses binaries and personal paths.

| Windows | repository |
|---|---|
| the pipeline scripts in `C:\GB-x86\` (`build_esp.py`, `build_usb.py`, `verify_image.py`, `stage_kernel.py`, ...) | the repository root |
| `C:\GB-x86\iso\` | `iso/` |
| `C:\GB-x86\iso\README-ISO.md` (the same text as `D:\GB-iso\README-ISO.md`) | `iso/README-ISO.md` (with a transform) |
| `C:\GB-x86\out\README-FLASH.md` | not copied; its public form is `docs/FLASHING.md` (written by hand) |
| `C:\GB-build\native\hw-gap-audit\STATUS.md` | not copied; its public form is `docs/DEVICE-STATUS.md` (edited by hand) |
| `C:\GB-build\native\<component>\` | `native/<component>/` (not `backlog`, `fixprep`, `readiness`, `w6prep` ... `w11prep`) |
| `C:\GB-build\native\hwtest\` | `native/hwtest/`: only the collector, the analyser, `check_stick_ota.py` and their README |
| `C:\GB-build\native\dualboot\GUIDE-DUALBOOT-120GB.md` | must NOT be published (the owner's laptop-specific guide); `collect.py` still copies it today (rule 3.2.4) |
| `C:\GB-build\HANDOFF-PORTABLE.md` | planned: `docs/HANDOFF-PORTABLE.md`; `collect.py` has no rule for it yet |
| `C:\GB-build\tools\` | only `tools/frametime.cs` |
| `HANDOFF.md`, `BACKLOG.md`, the manual, `w11prep\` | never published |

Other repository documents an agent should know: `docs/NVIDIA.md` (the NVIDIA mode properties), `docs/OTA.md`,
`docs/NATIVE.md`, `docs/INSTALLER-ISO.md`.

Consequences:

* Do not treat the repository as the source of truth. A change made in a clone must be carried back to the
  matching Windows file by hand, then `collect.py` is run again.
* A new top-level folder under `C:\GB-build\native\` would be published by the next `collect.py` run. Put work
  folders under `C:\GB-build\native\w11prep\` (excluded), or add the folder to `EXCL_COMPONENTS` first.

### 2.4 Switching between Windows and Googlebook (the owner)

* **Windows stays the default.** A normal power-on or restart starts Windows.
* **Windows -> Googlebook, once:** the owner's desktop shortcut **"Restart to Googlebook"**. It runs as administrator,
  asks for `Y`, then runs `C:\GB-build\native\dualboot\Restart-ToGooglebook.ps1`. The script sets the firmware's
  one-time next-boot entry to "Googlebook OS" (`bcdedit /set {fwbootmgr} bootsequence ...`) and **restarts at once**.
  Any other answer cancels. Without the shortcut: the same script from an elevated PowerShell, `-WhatIf` first
  (GUIDE 6.1 and 7). Agents never run the script or the shortcut (rule 3.1.1).
* Before switching: save your work, let agents on Windows stop their workflows so that they can resume, shut the
  builder VM down, and shut the VMware Googlebook VM down from inside Android.
* **The boot menu** shows for 4 seconds and then starts its default entry, `Googlebook OS - Surface Book 3 (slot A)` or
  `(slot B)`. Each slot also has a `[NVIDIA test]` entry: pick it only when an agent asks (section 5.2). An arrow key
  stops the countdown. `e` edits the highlighted entry for this start only (the one-boot tokens such as
  `gb.nouveau=0`). Never press `d` or `D` (it changes the default entry).
* **Googlebook -> Windows:** restart or shut down from Googlebook. The clock may be off by the time-zone offset until
  it syncs.
* **A hang:** hold Power for 10 seconds. The next power-on starts Windows.

---------------------------------------------------------------------------------------------------------------------

## 3. Hard rules for every agent

These rules hold for every tool and every environment. If a request breaks one of them, stop and ask the owner.

### 3.1 Disks, boot and VMs

1. **Never write the laptop's real disks or boot configuration.**
   * For example (the list is not complete: anything that writes a disk, a partition or a boot setting is out): no
     raw disk writes (`\\.\PhysicalDrive*`), no `diskpart`, no `bcdedit /set`, `/create`, `/copy`, `/delete`,
     `/deletevalue`, `/import`, `/default` or `/bootsequence`, no `bcdboot`, no `mountvol`, no `efibootmgr`, no
     `Resize-Partition`, `New-Partition`, `Remove-Partition`, `Set-Partition`, `Format-Volume`, `Clear-Disk` or
     `Initialize-Disk`, no `manage-bde -on` / `-off`, no `vssadmin resize` / `delete`, no `powercfg /h`, no `gbdisk`
     operation that writes (`create`, `write`, `remove`, `restore-gpt ... --apply`).
   * Never run `install_dualboot.ps1`, `uninstall_dualboot.ps1`, `Import-GooglebookIsoInstall.ps1` or
     `Restart-ToGooglebook.ps1` (nor the owner's "Restart to Googlebook" shortcut), in any mode. Read them, and give
     the owner the exact command lines, the dry run and the real run as separate blocks with a STOP between them.
   * **The owner runs every disk and boot step**, dry run first.
   * Read-only queries are fine: `Get-Disk`, `Get-Partition`, `Get-Volume`, `gbdisk layout --disk 0`, registry
     reads. If a query needs administrator rights, give the owner the exact elevated command instead of working
     around it.
   * Inside Googlebook: never write a block device, a partition, the ESP, or the A/B slot and update state.
2. **Test only on image files or throwaway VMware VMs.** A throwaway VM gets at most 4 GB RAM and 2 vCPUs.
3. **The owner's VM is off limits.**
   * Never write `C:\GB-x86\lapis.bin`. It is only read, by `build_usb.py` / `build_internal.py`, with the VM off.
     Both refuse while `lapis-boot.vmx` runs or a `lapis*.lck` exists; never pass `--allow-running-vm`. A VM update
     (`GB_WRITE_LAPIS=1 build_esp.py`) is its own reviewed step with the owner's go (below).
   * Never start, stop or hard-power-off the owner's VM. Starting it makes a 12 GiB `.vmem` on C:: the owner starts
     it only with C: at least 28.4 GB free.
   * Never open the VMware GUI or act on `lapis.bin` while a `lapis*.lck` file exists.
   * A future VM update would be its own reviewed step, with the owner's explicit go and a fresh verified backup
     (private `HANDOFF.md` section 5). This file does not cover it.
4. **Never touch `/data`, the lock-screen PIN, Gatekeeper or KeyMint state**, on any install or VM. Never set or
   clear a PIN. Never run `cmd lock_settings` or `locksettings`.
5. **Never run Googlebook's own "install to disk".** Never let Windows format a Googlebook partition or a
   Googlebook stick. Never format a stick that Android offers to format. Never start the old wave-10 stick (it holds
   a Google update in slot A), and never use a stick made with the wave-8 ISO.
6. **First-stage module rule (native):** never preload the project's copies of lapis first-stage modules
   (`8250_dw`, `intel-lpss(-pci)`, `mfd-core`, `pinctrl-intel(-platform)`, `intel_iommu_vendor_hooks`).
   `stage_kernel.py` enforces it.

### 3.2 Data, privacy and publishing

1. **Never publish Google, Intel or NVIDIA binaries or firmware**, nor images, keys or logs.
2. **No personal data** in anything published or shared: no names, e-mail addresses, serial numbers, this PC's
   GUIDs, MAC addresses, Wi-Fi names, account names, or `C:\Users\<name>` paths. Write `%USERPROFILE%` or `<you>`.
   Write `<owner folder>` and `<owner share>` for the owner's own folder and share.
3. **Logs are private.**
   * `C:\Googlebook-logs\` and every collector output folder are read-only for agents. Never copy them.
   * Quote only from the redacted `share\` copy.
   * The same holds for `logcat`, `dumpsys` and `getprop` output captured inside Googlebook: save it only under
     `~/work/private/`, never copy it to shared storage or a stick, quote only lines with no personal values. If raw
     output must reach Windows, treat it like `C:\Googlebook-logs\` (read-only, never quoted, never published, never
     archived to `<owner share>`).
4. **Push only after an audit.**
   * Run `collect.py` into a staging clone, always with an explicit DEST argument (without one it writes into the
     working copy `C:\GB-repo\googlebook-sb3`). Then the PII, secret and binary audits:
     `C:\GB-build\tools\w6final\tools\audit_staged.py`, `C:\GB-build\tools\w7final\final\audit_extra.py` and
     `C:\GB-build\tools\w11final\rel-final\audit_maps_selfcontained.py` (it replaces `audit_maps_w11f.py`); how the
     last push ran them is in `<owner share>\Workspace\D\gb-w11rel\final\logs\`.
   * **Push status (2026-11-07).** The three items that blocked the next push were put in place on 2026-11-07
     ~11:20-11:30: the self-contained maps audit `audit_maps_selfcontained.py` (it replaces `audit_maps_w11f.py`, which
     loaded `drafts.py` from a deleted folder; it takes every `C:\Googlebook-logs\<stamp>\redaction-map.csv` as an
     argument, reads them in memory only and prints only tokens and counts), and two `collect.py` rules (it leaves
     out `dualboot/GUIDE-DUALBOOT-120GB.md`, and it copies this file to `docs/HANDOFF-PORTABLE.md` with the two
     placeholders kept). A publish of this file alone was then prepared in a temporary staging clone (one added
     file; `audit_staged.py` CLEAN; `audit_extra.py`: only the `C:\Users\<name>` placeholder of rule 3.2.2; the maps
     audit: 8 findings, all one short map value inside a common English word, accepted as at the wave 11 push). That
     staging clone was temporary and is gone, and it held an older edition of this file: after every change of this
     file, run `collect.py` and the three audits again in a new staging clone. Then commit, and push only with the owner's OK. The record is in private
     `HANDOFF.md` section 0.
   * Commit with the **repository-local GitHub noreply identity** that is already set in `C:\GB-repo\googlebook-sb3`.
     Check it: `git -C C:\GB-repo\googlebook-sb3 config --local user.email` must end in
     `@users.noreply.github.com`. This file does not repeat the account name on purpose: `collect.py` refuses
     files that contain it.
   * Commit and author dates in UTC (`TZ=UTC`, see section 4.1).
   * Push to `main` only with the owner's OK. Never push from inside Googlebook.
   * `HANDOFF.md` and `BACKLOG.md` are never published.
5. **Never enter credentials.** No passwords, PINs, pairing codes, tokens or API keys. The owner signs in (Claude,
   Google, GitHub), pairs adb and types any `sudo` password. Never write a token into a file, a note or a commit.
6. **Third-party code:** gbos-x86 (gbOS86) has no licence on most files. Use ideas only and credit them. Only its 8
   SPDX GPL-2.0 `isa-emu` files and the Apache-2.0 `soft_gk.c` may be reused, with their headers kept.
7. **Never send anything upstream.** The local upstream drafts are the owner's decision.

### 3.3 Space, power and the builder

1. **Disk floors:** C: keeps at least 15 GB free, D: at least 3 GB. Check before any big job. Do not create large
   files without checking.
2. **Never delete outputs, fallbacks or backups you did not create.** Move superseded outputs into a `prev\` folder
   on the same volume (a rename) and tell the owner. The owner deletes. Keep every VM backup folder (since
   2026-11-07 in `<owner share>\Workspace\D\GB-vm-backup\`). The
   one exception is the owner's archive job (decision 6, section 1.3): a checked copy to `<owner share>`, then the
   source removed, with a note left in its place.
3. **Power:** keep the laptop on AC for long jobs. On battery at 25 % or less, pause cleanly: stop running workflows
   so that they can resume, shut the builder down, tell the owner. Inside Googlebook, keep the battery above about
   10 % (HW-LOWSHUT).
4. **Builder etiquette:**
   * One session uses the builder at a time.
   * Heavy jobs run as `nice -n 19 ionice -c3 ...`.
   * Never delete `/build/dist*`.
   * The builder's `/` needs 25 GiB free for an ISO build; `/build` keeps at least 2 GB.
   * Shut it down cleanly when done.
5. **Python always with `-I`** (and `-B` where a tool says so).
6. **Patches:** `git -c core.autocrlf=false apply --check <patch>` first, then the same without `--check`.
7. **Before you change a pipeline or source file**, copy it to `C:\GB-x86\archive\pre-<tag>\` with a
   `SHA256SUMS.txt`.
8. **One writer per tree:** take the lock file the step uses (`C:\GB-x86\out\<name>.lock`) and release it at the end.
9. **Shadow copies and restore points** are the owner's. Never run `vssadmin delete` or change System Protection.
10. **Sparse images and hard links.**
    * Reading the holes of an NTFS sparse image (a disk image) through the Windows cache holds that much free space
      until the file is closed. Never read a sparse image whole on a nearly full volume; use allocated-range readers
      (`C:\GB-x86\iso\test\common.py` `sparse_sha256`, `C:\GB-build\tools\w6final\gptv\gpt_validate.py`).
    * Never size a new file with Python's `f.truncate(N)` or a seek past its end on C: or D:: it zero-fills.
    * Moving a sparse image to another volume or a share needs room for its full logical size there.
    * A hard-linked file (for example `super.img`, one file under six names: `D:\gb-export\HARDLINKS.txt`) frees
      space only when every name is gone, and copying each name copies the data again.
11. **Space after the D: rebuild and the dual-boot install.** C: is not shrunk (decision 5): about 241 GB free on
    2026-11-07 (once the owner has deleted the rebuild's `C:\D-staging`), with the page file. D: is 278,120 MiB
    (271.6 GiB) after the rebuild, about 184 GB free with the owner's data and `D:\gb-export`. Keep the floors (rule
    3.3.1).
    * Always pass `--out` to `build_internal.py` (a new folder; never its `D:\gb-export` default, which the laptop
      kit needs complete).
    * An ISO build must write below `D:\GB-iso\` (copy that folder back from `<owner share>\Workspace\D\GB-iso\`
      first) and needs about 15.5 GiB free on D:; the ISO's dual-boot test groups need about 26 GB on D:.
    * The builder VM's disks (168 GB) fit back on C: with its floor [inference from the measured sizes]; check the
      space right before the copy.

---------------------------------------------------------------------------------------------------------------------

## 4. How to continue in each environment

### 4.1 Windows + Claude Code

1. Open Claude Code in `<owner folder>` (the folder the project was run from; its path is in private `HANDOFF.md`
   section 0) or in `C:\GB-build`.
2. Read, in this order:
   1. this file;
   2. `C:\GB-build\HANDOFF.md` section 0 (the newest bullet is on top);
   3. `C:\GB-build\native\backlog\BACKLOG.md` section 17.4;
   4. the device status `C:\GB-build\native\hw-gap-audit\STATUS.md` (public form: `docs/DEVICE-STATUS.md`);
   5. the README of the area you will touch.
3. Look at the PC (read only):

   ```powershell
   Get-Volume -DriveLetter C,D | Format-Table DriveLetter,SizeRemaining,Size
   & "C:\Program Files\VMware\VMware Workstation\vmrun.exe" -T ws list
   Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SystemInformation]::PowerStatus
   python -I C:\GB-build\tools\verify_tools.py
   ```

4. The builder VM:
   * Start: `& "C:\Program Files\VMware\VMware Workstation\vmrun.exe" -T ws start C:\GB-build\builder2.vmx nogui`
   * Connect: `ssh -i C:\GB-build\builder_key builder@<address>`. The address is in `C:\GB-build\builder_ip.txt`
     (the scripts read it from the `GB_BUILDER` setting).
   * Stop: `ssh -i C:\GB-build\builder_key builder@<address> sudo -n poweroff`, or
     `& "C:\Program Files\VMware\VMware Workstation\vmrun.exe" -T ws stop C:\GB-build\builder2.vmx soft`.
     Afterwards `vmrun -T ws list` must not show it.
   * Never start `C:\GB-build\builder.vmx` (section 2.1).
5. Building and testing. The summary is below. The exact flags are in private `HANDOFF.md` section 6, in
   `README-FLASH.md`, `README-ISO.md`, the manual's chapter 07 (`C:\GB-build\docs\manual\content\07-build-test.md`)
   and `C:\GB-build\tools\README.md`.

   | step | command |
   |---|---|
   | kernel | on the builder `kbuild16_native.sh`; then `python -I C:\GB-x86\stage_kernel.py k16 --dist /build/dist16` |
   | USB image | `python -I C:\GB-x86\build_usb.py --out <new name>`, then `python -I C:\GB-x86\verify_image.py <img>` (VM off: rule 3.1.3) |
   | dual-boot export | `python -I -B C:\GB-build\native\dualboot\build_internal.py --out <new folder on C:>` (always `--out`: rule 3.3.11; for an installed system add `--reuse-guids C:\GB-backup\<ts>\manifest_installed.json`, for an ISO install `C:\GB-backup\<stamp>-iso\manifest_installed.json`) |
   | ISO | `python -I C:\GB-x86\iso\build\build_iso.py` (C: at least 45 GB free), then `cd C:\GB-x86\iso\test; python -I run_all.py --with-t1c` |
   | same native tree? | `python -I C:\GB-build\tools\compare_native.py <usb.img> <EFI-SYSTEM.img>` |
   | host suites | private `HANDOFF.md` section 6 step 7 |
   | area tests | `python -I C:\GB-build\tools\area_tests\run_area_tests.py --list`, then `host`, `builder` or a test id |
   | VM-1 | a throwaway VM (4 GB / 2 vCPU) boots the new image; the wave 11 toolkit is `C:\GB-build\tools\w11final\rel-vm1\` (rooted at its D: folder: copy and re-root before reuse) |

6. Committing in the repository working copy (after the audit, rule 3.2.4), from Git Bash:

   ```bash
   cd /c/GB-repo/googlebook-sb3
   git config --local user.email        # must end in @users.noreply.github.com
   TZ=UTC git commit -m "<message>"
   ```

7. Tips:
   * Before the owner restarts the PC (for example into Googlebook), stop running workflows so that they can resume,
     and shut the builder down.
   * Claude Code workflows: never send a message to an agent inside a running workflow. It starts a second copy of
     that agent (this happened on 2026-11-06).

**First prompt to paste (Windows):**

```text
Continue the Googlebook OS on Surface Book 3 project. First read C:\GB-build\HANDOFF-PORTABLE.md completely,
then section 0 of C:\GB-build\HANDOFF.md and section 17.4 of C:\GB-build\native\backlog\BACKLOG.md.
Follow every hard rule in HANDOFF-PORTABLE.md section 3. You never write a real disk or a boot setting: I run
those steps. Never touch lapis.bin, /data, PINs or Gatekeeper. Use python -I. Keep C: >= 15 GB and D: >= 3 GB free.
Then tell me in a few lines: the current state, what is open, and what you suggest next. Change nothing until I say so.
```

### 4.2 Googlebook OS + Claude Code

**The route:** Claude Code runs inside the Debian VM of Googlebook's built-in **Terminal** app ("Linux development
environment"), installed with Anthropic's official Linux installer.

Why this route:

* Anthropic supports Debian 10 or later on x64 with 4 GB RAM or more.
* Google names Claude Code as a tool for the Googlebook terminal.
* lapis ships the Terminal app. In the third native test it started Debian 13 on this laptop [checked], so the VM
  support (KVM) works natively.
* The other route, Termux, is broken for Claude Code (since version 2.1.113 it reports the platform `android`; an
  open upstream issue). Use it only as a last resort, through `proot-distro` Debian, and expect it to be slow
  (section 4.2 F).

Since 2026-11-08 Googlebook is installed on the **internal SSD** and starts (section 1.4): do all of this there,
never on a stick (a stick's data partition is only 14 GiB, and a fresh stick is very slow on its first start). The
setup below has not been done on the internal install yet; its [checked] tags come from the third native test, on a
stick. Googlebook's data,
the Debian VM included, is erased by an uninstall or reinstall (decision 7), so keep the clone's work in git branches
and copy the notes out often (section 4.4).

#### A. Set up (the owner does these steps)

1. Start Googlebook (section 2.4), sign in, connect Wi-Fi, plug in the charger. With Wi-Fi on, Google updates can
   download and install by themselves (decision 2); after each one the owner runs GUIDE 6.4 on Windows.
2. Turn on Developer options: Settings > About > tap **Build number** 7 times.
3. Open the **Terminal** app and let it download Debian over Wi-Fi.
   * If it shows "Unrecoverable error", close it and open it again (seen once in the third test).
   * If there is no Terminal app: Developer options > **Linux development environment** on [not checked on lapis].
4. In the Terminal's settings (names from a community guide; they may differ here [not checked]):
   * memory size at least 4096 MB (Claude Code needs 4 GB);
   * "Keep awake" on for long sessions;
   * share only one folder with the VM.
5. In the Debian shell, check the basics:

   ```bash
   uname -m                          # x86_64
   cat /etc/debian_version           # 13.x
   grep -m1 -ow avx /proc/cpuinfo    # must print: avx   (empty = Claude Code stops with "Illegal instruction")
   free -h; df -h ~
   ```

   Whether the VM passes AVX through on this laptop is [not checked]. If the third command prints nothing, stop
   here: the Windows side has no fix for that. Use the Termux fallback (section 4.2 F).
6. Install the tools and Claude Code. If `sudo` asks for a password, the owner types it.

   ```bash
   sudo apt update && sudo apt install -y curl git ripgrep tmux unzip bubblewrap socat adb netcat-openbsd
   adb version                       # needs 30.0.0 or newer for 'adb pair'
   curl -fsSL https://claude.ai/install.sh | bash
   echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && . ~/.bashrc
   claude --version && claude doctor
   ```

   Debian 13 should ship a new enough adb [inference]. If `adb version` is older than 30.0.0, install Google's
   platform-tools zip into `~/platform-tools` instead (from Google's Android developer site) and use that `adb`.

7. Get the repository. The owner types its URL. On Windows it is printed by
   `git -C C:\GB-repo\googlebook-sb3 remote get-url origin`. One folder layout for every route (4.2, 4.3, 4.2 F):
   the clone in `~/work/googlebook-sb3`, notes in `~/work/notes/`, raw private output in `~/work/private/`.

   ```bash
   mkdir -p ~/work/notes ~/work/private && chmod 700 ~/work/private
   cd ~/work && git clone <REPO-URL> googlebook-sb3
   ```

   Until this file is published in the repository, the owner copies `HANDOFF-PORTABLE.md` in with a USB data stick
   (section 4.4) and puts it at `~/work/HANDOFF-PORTABLE.md`.

8. Safety settings. Create `~/.claude/settings.json` in the VM:

   ```json
   {
     "permissions": {
       "defaultMode": "default",
       "ask": ["Bash(adb *)"],
       "deny": [
         "Bash(adb root*)", "Bash(adb tcpip*)", "Bash(adb reboot*)", "Bash(adb disable-verity*)",
         "Bash(adb sideload*)", "Bash(adb uninstall*)", "Bash(adb shell pm uninstall*)", "Bash(adb shell pm clear*)",
         "Bash(adb shell cmd lock_settings*)", "Bash(adb shell locksettings*)", "Bash(adb shell rm *)",
         "Bash(adb shell svc *)", "Bash(adb shell reboot*)", "Bash(adb shell bootctl*)",
         "Bash(adb shell update_engine_client*)", "Bash(adb shell su*)",
         "Bash(adb pair*)", "Bash(adb shell input*)", "Bash(git push*)"
       ]
     }
   }
   ```

   Deny rules only match the command as written. A reworded command can slip past them, so still read every
   command before you allow it.
9. Start a session that survives a closed window:

   ```bash
   tmux new -s gb
   cd ~/work/googlebook-sb3 && claude --permission-mode default
   ```

   Recent Claude Code versions start terminal sessions in "auto" mode by default (Anthropic's docs, read on
   2026-11-07). `--permission-mode default` makes it ask first. Never use `bypassPermissions` or
   `--dangerously-skip-permissions` here.
10. **Sign in: the owner only.** If no browser opens, press `c` to copy the sign-in link, open it in Chrome on
    Googlebook, sign in, and paste the code back at "Paste code here" (or run `claude auth login`).

#### B. Give the agent a shell on Android (Wireless debugging) [not checked on this install]

The laptop has no USB device controller, so USB debugging cannot work. Wireless debugging is the only adb route. adb
then runs as the **shell user** (uid 2000). There is no root: lapis is a `user` build (`ro.debuggable=0`), and
there is no `su`.

1. The owner: Settings > System > Developer options > **Wireless debugging** on (Wi-Fi must be on) > **Pair device
   with pairing code**. Split the screen so the code stays visible.
2. **The owner pairs, never the agent.** In a second tmux window (Ctrl-b c), the owner runs:

   ```bash
   ip route                                  # the gateway may be the Android side of the VM link
   adb pair <ip>:<pairing-port>              # adb asks for the 6-digit code: the owner types it here
   adb connect <ip>:<port>                   # the port on the main Wireless debugging screen
   ```

   The agent never sees the pairing code and never runs `adb pair` (it is on the deny list). Then the agent may
   check:

   ```bash
   adb devices -l
   adb shell id                              # expect uid=2000(shell)
   ```

   `<ip>` is the address on the Wireless debugging screen. If the VM cannot reach it, try the gateway that
   `ip route` shows. Whether the VM can reach Android's adb at all is [not checked].
3. **The network.** Turn Wireless debugging on only on a trusted Wi-Fi network (the owner's own), and turn it off
   after each session.
4. Never run `adb tcpip`.
5. When done: Wireless debugging off. To withdraw all keys: Developer options > Revoke USB debugging authorizations.

#### C. What the agent may do on the running OS

Reading is free. The output is private (rule 3.2.3): save raw output only under `~/work/private/`, which is never
copied out.

```bash
adb shell getprop ro.build.fingerprint
adb logcat -d -b main,system,crash,events -t 5000 > ~/work/private/logcat-$(date -u +%Y%m%dT%H%M%SZ).txt
adb shell dumpsys battery
adb shell sh /vendor/bin/gb_hwcheck.sh -n         # the image's read-only self-test, state checks only (about 1 minute)
adb shell sh /vendor/bin/gb_hwcheck.sh battery ac # only these items
adb shell sh /vendor/bin/gb_dgpu_check.sh         # read-only NVIDIA snapshot
adb shell ls -l /dev/kvm
adb shell getprop ro.boot.hypervisor.vm.supported
```

* `gb_hwcheck.sh` without `-n` asks the person at the laptop for physical steps (close the lid, unplug the charger,
  ...). Run it that way only with the owner present. Backlog item HW-HWCHECK asks for one such run. As the shell
  user it keeps no record of its own, so save its output in `~/work/private/` and put only the result lines (with no
  personal values) into the notes.
* As the shell user it writes nothing. `dmesg` and some `logcat` options may be refused on this build.

Changes only after the owner says yes, one at a time, each written into the notes:

* the documented properties (in the repository: `docs/FLASHING.md`, `docs/NVIDIA.md`, `docs/DEVICE-STATUS.md`; on
  Windows also the manual appendix `C:\GB-build\docs\manual\content\10-appendix.md` and `README-FLASH.md`), for example `adb shell setprop persist.vendor.gb.gfx_mode intel` (or `auto`, `nvidia`),
  `vendor.gb.posture` / `persist.vendor.gb.posture`, `persist.vendor.gb.power.measure 0` (turns the every-start
  power measurement off), `vendor.gb.power.now 1`;
* `settings put ...`;
* `pm` / `cmd package`, and `adb install` of an APK the owner chose.

What it cannot do there (these go back to Windows, section 5):

* root, or any change to `/odm`, `/vendor`, `/system`, the kernel or the modules;
* `gb\gb.cfg` or anything else on the ESP;
* the one-boot tokens `gb.power.lowshut=0`, `gb.nouveau=0`, `gb.vnni=0`, `gb.overlay=1`: only the boot menu (`e`)
  sets them;
* reading `gblog\` (the logs are collected from Windows).

#### D. What it must not do

Everything in section 3, and in particular:

* no PIN, Gatekeeper or lock-screen commands; nothing under `/data/vendor/gb`; no `adb shell input` (it could type
  the PIN) and no `adb pair` (the owner pairs);
* nothing that changes the A/B slot or a Google update (`bootctl`, `update_engine_client`, the system update screen);
* no `adb root`, `tcpip`, `reboot`, `disable-verity`, `sideload`; no `pm clear` or uninstall without the owner;
* no credentials or tokens in notes or files;
* no push to GitHub.

#### E. Recording findings

* One notes file per day: `~/work/notes/NOTES-<yyyymmdd>.md`. For each finding write:
  * the date and time in UTC, and the build fingerprint;
  * what the owner saw, and what the agent checked (the commands);
  * where the raw output is (private files in `~/work/private/`; only the file names go into the notes);
  * the matching backlog row (section 1.5), or "new";
  * a proposed small fix, or "record only".
* Source edits go on a local branch in the clone, for example `gbos/<yyyymmdd>-<topic>`. Export them as patches
  with `git format-patch main -o ~/work/notes/patches/`. Never push.
* Write the state-log line (section 6 format) into the day's NOTES file. The Windows side appends that line to
  section 6 of `C:\GB-build\HANDOFF-PORTABLE.md`. Never copy a whole edited copy of this file back: the Windows file
  is the master.
* How the notes get to Windows: section 4.4.

**General first prompt (Claude Code inside Googlebook).** For the current work (audio, then NVIDIA) use the starter
prompt in 4.2 G instead.

```text
You run inside the Debian VM of the Terminal app on Googlebook OS, on my Surface Book 3 (internal install).
Read HANDOFF-PORTABLE.md (docs/HANDOFF-PORTABLE.md in ~/work/googlebook-sb3 once published, until then
~/work/HANDOFF-PORTABLE.md), especially sections 3 and 4.2, then docs/DEVICE-STATUS.md in the clone.
You cannot reach Windows or the builder; you diagnose and prepare only. I pair adb myself; never run adb pair,
never ask for the pairing code or any password. Over adb you may read freely (getprop, logcat -d, dumpsys,
gb_hwcheck.sh -n, gb_dgpu_check.sh); save raw output only in ~/work/private/. Ask me before any change, one at
a time. Never touch PINs, /data, the A/B slot or Google updates. Never push. Write every finding to
~/work/notes/NOTES-<today>.md. Start with: adb devices -l and adb shell id. Then wait for my description of
the problem.
```

#### F. Fallback: Termux, only if step A5 prints no `avx` [not checked]

Termux runs on the laptop's CPU directly, so AVX is there. Claude Code must run inside a `proot-distro` Debian in
Termux, not in Termux itself (see "Why this route"). Expect it to be slow. Every step here is [not checked].

1. The owner installs Termux (the maintained builds are on F-Droid and on Termux's GitHub releases page; the owner
   chooses the source).
2. In Termux:

   ```bash
   pkg update && pkg install proot-distro
   termux-setup-storage              # Android asks for storage access; shared storage is then ~/storage/shared
   proot-distro install debian
   proot-distro login debian
   ```

3. Inside that Debian, do steps A6-A10 of this section (inside proot you are root by default: leave out `sudo`). The
   same folder layout (`~/work/googlebook-sb3`, `~/work/notes/`, `~/work/private/`), the same settings file and deny
   list.
4. adb: the owner pairs with the address on the Wireless debugging screen, as in B step 2 (`localhost:<port>` may
   also work from Termux).
5. Section 4.4 applies, with Termux's shared storage in place of the Terminal's shared folder.

#### G. Starter prompt (the current work: audio, then NVIDIA)

The owner pastes this as the first message, once steps A1-A10 and B1-B2 are done and `claude --permission-mode
default` runs in the tmux session (A9). The work it starts is described in section 5.1 (audio) and section 5.2
(NVIDIA). For Antigravity (section 4.3) the same text works with "4.3" in place of "4.2".

```text
You run inside the Debian VM of the Terminal app on Googlebook OS, on my Surface Book 3. Googlebook is installed
on the internal SSD next to Windows. First read HANDOFF-PORTABLE.md completely (~/work/HANDOFF-PORTABLE.md; once
published, docs/HANDOFF-PORTABLE.md in ~/work/googlebook-sb3), above all sections 1.4, 1.6, 3, 4.2, 5.1 and 5.2,
then docs/DEVICE-STATUS.md in the clone. Every hard rule in section 3 applies.
The work, in this order:
1. Audio first. Windows sounds richer than Googlebook on the same speakers. Follow section 5.1: run the read-only
   adb diagnostics of its part C3 (idle first, then while I play music on the speakers), then tell me which
   part-A options can work on this image and whether a part-B image change is worth proposing.
2. Then NVIDIA. Follow section 5.2, "Agent inside Googlebook": the D3cold checks first, with nothing else running,
   then the other read-only checks; tell me when to do the owner steps there.
How you work:
- Reach Android only through adb, as the shell user (uid 2000), after I have paired it myself. Never run adb pair,
  adb root, adb tcpip or adb reboot; never ask for a pairing code, a password or a PIN.
- Reading is free. Save raw output only under ~/work/private/. Any change (setprop, settings put, pm grant, an app
  install) only after my yes, one at a time. Never touch /data, the lock screen, the A/B slot or Google updates.
- Never push, and never enter credentials. Source edits only on a local branch, exported as patches to
  ~/work/notes/patches/.
- Record every finding in ~/work/notes/NOTES-<today>.md as in section 4.2 E, with no personal values.
- You cannot reach Windows, the share or the builder. A fix that needs an image change, a rebuild or a test goes back
  to the Windows side as a note plus a patch: it is built and tested there (section 5).
Start with: adb devices -l, adb shell id and adb shell getprop ro.build.fingerprint. Then tell me in a few lines
what you will run first, and wait for my go.
```

### 4.3 Googlebook OS + Google Antigravity

**The route:** the Antigravity Android app. Its agents run their commands inside the same Terminal Debian VM.

* Google's documentation says Antigravity comes pre-installed on Googlebooks. In the owner's VMware VM the
  Antigravity app opened and worked [checked in the VM only]. On the native install it is [not checked]. If it is
  missing, install "Google Antigravity" from the Play Store.
* Requirements: a Google Account and, per Google's help page, a Google AI plan. Antigravity's pricing page also
  lists a free tier. The two sources disagree; the app's own setup will show what applies.

Set up (the owner):

1. Do steps A1-A3 and A5 of section 4.2 first (Terminal and Debian working).
2. Open Antigravity > Get started. Allow notifications, finish the Terminal setup, choose one app folder and give
   both Antigravity and Terminal access to it.
3. Sign in with your Google Account yourself.
4. Choose the permission preset **Request Review** (it asks before every command). Never use **Turbo** (it turns the
   sandbox off). The **Default** preset runs commands in a sandbox without network, so adb probably will not work in
   it [inference]. These preset names come from Google's documentation [not checked]: confirm them on Google's own
   Antigravity page before you rely on them.
5. In Antigravity's permission settings, put the same commands on the Deny list as in section 4.2 A8, in its
   `command(...)` form, for example `command(adb root)`, `command(adb tcpip)`, `command(adb shell cmd lock_settings)`,
   `command(adb pair)`, `command(adb shell input)`, `command(git push)` [not checked: confirm the syntax in the app].
6. Keep the Terminal app open while agents work. Closing the Antigravity app stops all its agents.
7. Clone as in section 4.2 A7 (the same layout: `~/work/googlebook-sb3`, `~/work/notes/`, `~/work/private/`), and
   give Antigravity access to `~/work`. If the app can only open its own shared app folder, put the clone there
   instead and tell the agent the path [not checked].

The command-line agent `agy` is the other way [not checked]. Confirm its install command on Google's own Antigravity
page first; never pipe a URL to bash that you have not confirmed there. In the Debian VM:

```bash
curl -fsSL https://antigravity.google/cli/install.sh | bash
agy --version
cd ~/work/googlebook-sb3 && agy
```

Stay in the default mode (Shift+Tab switches modes). Never use `--dangerously-skip-permissions`. `agy` can use the
same paired adb as Claude Code (section 4.2 B).

Limits: the same as section 4.2 C and D. Do not run the Antigravity desktop IDE (the Linux tar.gz) inside the VM:
Linux GUI apps and GPU acceleration are still on Google's roadmap for this VM.

**First prompt to paste (Antigravity):**

```text
You are working on the Googlebook OS port for my Surface Book 3. Read HANDOFF-PORTABLE.md first
(docs/HANDOFF-PORTABLE.md in ~/work/googlebook-sb3 once published, until then ~/work/HANDOFF-PORTABLE.md),
especially sections 3 and 4.3, then docs/DEVICE-STATUS.md. Your commands run in the Terminal's Debian VM. You
cannot reach Windows or the build machine: diagnose and prepare only. I pair adb myself; never run adb pair.
Over adb you may only read (getprop, logcat -d, dumpsys, gb_hwcheck.sh -n) unless I approve a change; save raw
output only in ~/work/private/. Never touch PINs, /data, the A/B slot or Google updates, never push, never enter
credentials. Write findings to ~/work/notes/NOTES-<today>.md and wait for my problem description.
```

### 4.4 Moving files between Googlebook and Windows

Into Googlebook:

* The public repository: `git clone` / `git pull`. This file will be there as `docs/HANDOFF-PORTABLE.md` once the
  lead has published it (planned, section 2.3). Until then it comes in on the data stick.
* A USB data stick (FAT32 or exFAT): copy files with the Files app into **the shared folder**: the one Android folder
  that the Terminal app shares with the VM (section 4.2 A4). A community guide says the VM sees it at `/mnt/shared`
  [not checked here]. Check with `ls /mnt/shared` and write the real path into the notes once.
* The project docs say Android mounts USB sticks as "USB" volumes on internal installs (on stick boots it does not)
  [not checked on this laptop].

Out of Googlebook: the same way back, but **only** `~/work/notes/NOTES-*.md` and `~/work/notes/patches/`. Copy them
into the shared folder, then with the Files app onto the USB data stick, then read the stick on Windows. Never copy
`~/work/private/` out: Android shared storage can be read by other apps (and every app SELinux domain is permissive,
W11-SR-1). If raw output is really needed on Windows, the owner decides, and it is then handled as in rule 3.2.3.

Never use the Googlebook stick or the installer stick as the data stick.

On Windows, the agent:

1. puts the notes into a new prep area, for example `C:\GB-build\native\w11prep\hwfix-<yyyymmdd>-<topic>\incoming\`;
2. checks a patch in a staging clone first: `git -c core.autocrlf=false apply --check <patch>`;
3. carries the change to the matching Windows source file (section 2.3), following section 5.

---------------------------------------------------------------------------------------------------------------------

## 5. The fix loop for hardware findings

The loop below is the same for every finding. Sections 5.1 and 5.2 are the two investigations open now (decision
7): what to check, how, and what each answer decides.

1. **Collect** (the owner, on Windows).
   * Googlebook writes boot logs to its ESP: `gblog\boot<N>-t<T>.txt` at 40 s, 90 s, 3, 7 and 15 minutes. Keep a
     start running for 16 minutes so that all of them exist.
   * Stick: `powershell -NoProfile -ExecutionPolicy Bypass -File C:\GB-build\native\hwtest\Get-GooglebookLogs.ps1`
   * Internal install: the collector refuses internal disks on its own. Give it the ESP's volume path with
     `-SourcePath` (elevated), and always `-OutputRoot C:\Googlebook-logs`. `GUIDE-DUALBOOT-120GB.md` 6.5,
     "Collect the logs", has the exact commands.
   * The collector makes a private copy and a redacted `share\` copy with a zip. Agents use only the `share\` copy.
2. **Analyse** (an agent, on the `share\` copy only).
   * `python -I C:\GB-build\native\hwtest\analyze_gblog.py <logs>\share\gblog -o <work>\TRIAGE.md --json <work>\triage.json`
   * Cameras: `C:\GB-build\tools\w11final\hwchecks\camtest_check.py`. Power: `power_report.py` in the same folder.
     Run each with `python -I` and read its `--help` first.
   * Inside Googlebook: the live checks of section 4.2 C. Inside the Debian VM `analyze_gblog.py` should also run on
     a copied `share\` folder (Python 3 standard library only) [not checked].
3. **Decide.** Is it a real hardware finding? Which backlog row (section 1.5)? What is the smallest fix? Or is it a
   record only? **The owner decides.** No new features. Write the decision into private `HANDOFF.md` section 0 and
   `BACKLOG.md`.
4. **Prepare** in a prep area under `C:\GB-build\native\w11prep\<area>\`: the patch, its tests, and a review by a
   second agent (a `REVIEWED.flag` when it is ready).
5. **Integrate** under the step's lock file, with pre-images in `C:\GB-x86\archive\pre-<tag>\` (rules 3.3.7-3.3.8).
6. **Rebuild and test on Windows** (first copy the release USB image back from `<owner share>\Release\` if a step
   needs it: section 2.1; after the move of decision 6, the workspace from `<owner share>\Workspace\` too). Mind the
   space after the dual-boot install (rule 3.3.11), and always pass `--out`.
   1. the USB image, `verify_image.py`, `compare_native.py`;
   2. the export (`build_internal.py --out <folder on C:>`);
   3. VM-1 on a throwaway VM (4 GB / 2 vCPU);
   4. the host suites and the area tests;
   5. the ISO and its tests only if the ISO must change (C: at least 45 GB free; D: about 15.5 GiB for the build and
      about 26 GB for the dual-boot groups; the builder's `/` at least 25 GiB);
   6. an independent audit.
7. **Release.**
   * Swap the new image in. The old one moves to `C:\GB-x86\out\prev\` by a rename; it is never deleted.
   * Write the sidecar and update `README-FLASH.md`.
   * Repository: `collect.py` into a staging clone, the audits, a commit (noreply identity, UTC), then the push with
     the owner's OK.
   * Update private `HANDOFF.md`, `BACKLOG.md`, and section 6 of this file.
8. **Deliver** (the owner).
   * Stick: write the new image to the stick.
   * Internal install, a change of the ESP only (kernel, initrd, the native tree): build the export with
     `--reuse-guids C:\GB-backup\<ts>\manifest_installed.json` (an ISO install: `C:\GB-backup\<stamp>-iso\`; its
     partition GUIDs differ from those of `D:\gb-export`), then, elevated,
     `install_dualboot.ps1 -ExportDir <new export> -UpdateEspOnly` (dry run), then the same with `-Apply`.
     * An install made by the ISO needs `Import-GooglebookIsoInstall.ps1` first.
     * It **refuses** once a Google update has changed the active slot on the device. The way back is then a fresh
       install, which erases Googlebook's data.
   * A change of the system itself (`super`): uninstall, then install again (Googlebook's data is erased).

### 5.1 Audio: why Windows sounds richer, and what to do

**Short answer.** The speakers, the codec and the sample rate are the same in both systems. The difference is
processing. Windows sends the sound through Dolby's speaker processing (Dolby DAX3, the "Dolby Atmos" of the spec
sheet) and Realtek's own processing before it reaches the speakers. Googlebook sends it to the speakers flat: no
equalizer, no bass enhancement, no loudness or limiter stage, every gain at 0 dB. Small laptop speakers sound thin
without that tuning. Confirm this first with the 5-minute Windows test in step C1. The quickest fix without a
rebuild is an equalizer app (option A). An image change is only worth it after the measurements in part C.

Sources: the first internal-SSD start (2026-11-08) through the redacted log `gblog\audio-b1-t105.txt` and the boot
snapshots `gblog\boot1-t40.txt` / `boot1-t420.txt`, the native audio tree `C:\GB-build\native\audio\`, and a
read-only look at the Windows audio drivers on this laptop on 2026-11-08.

#### What the hardware is

* Microsoft lists "Front-facing stereo speakers with Dolby Atmos" and "Dual far-field Studio Mics" for the Surface
  Book 3 ([Microsoft: Surface Book 3 specs and features](https://support.microsoft.com/en-US/surface/models/surface-book-3-specs-and-features)).
* The chain under Googlebook [checked in the log]:
  Intel Ice Lake audio DSP with Sound Open Firmware (`sof-icl.ri` 2.2) -> HDA link -> **Realtek ALC274** codec
  (`audio-b1-t105.txt:160`, Subsystem Id `0x10ec11f0`, a generic Realtek id, so no model-specific kernel fix-up
  applies, `:164`) -> speaker pin `0x1b`, one stereo line out of type "speaker" (`:907`). The headphone jack is pin
  `0x21`, the built-in mics come in through the codec's pin `0x12` (`:912`).
* Whether the speakers use an amplifier inside the codec or a separate one is [not checked] (no schematic). The
  spec says two speakers. That Linux drives every speaker the board has is [not checked]: the full codec dump in C3
  answers it (look for a second speaker pin marked `[N/A]`).

#### What Windows applies [checked on this laptop, read only, 2026-11-08]

| item | what is installed |
|---|---|
| Speaker endpoint | "Speakers (Realtek High Definition Audio(SST))", 2 ch, 48 kHz, 16 bit; Audio enhancements **on** |
| Drivers | Realtek High Definition Audio(SST) 6.0.9249.2; Intel Smart Sound Technology 10.24.0.7927; DolbyAPO Software Device (HSA) 3.30201.210.0; DolbyAPO SWC Device 3.30508.581.0 |
| App | Dolby Access OEM 3.27 |
| Processing chain on the speaker | `DolbyDax3Apo.dll` in the stream-effect and offload-stream slots, and `DolbyDax3Apo.dll` + Realtek `RltkAPOU64.dll` in the endpoint-effect slot (slot 15 = endpoint effects per [Microsoft's PKEY_CompositeFX_EndpointEffectClsid page](https://learn.microsoft.com/en-gb/windows-hardware/drivers/audio/pkey-compositefx-endpointeffectclsid); slots 13 and 19 as stream / offload stream are [inference]) |

What these stages do in general: Dolby describes its PC speaker processing as an "audio optimizer" (corrects the
frequency response of built-in speakers), an "audio regulator" (keeps overdriven speakers and amplifiers from
distorting) and a "volume leveler" ([Dolby Advanced Audio v2](https://professional.dolby.com/tv/dolby-advanced-audio-v2/)).
Dolby tunes this per laptop model. The Surface Book 3 tuning itself is not public [not checked]. The Linux
community sees the same gap on other Dolby laptops and closes part of it with an equalizer or a measured impulse
response ([linux-thinkpad-speaker-improvements](https://github.com/shuhaowu/linux-thinkpad-speaker-improvements)).

The commands that show this (Windows PowerShell, read only, they print no ids). Run them with only the built-in
speakers as output (no headset or Bluetooth audio connected). The enhancements state is in Settings > System >
Sound > Speakers > Audio enhancements.

```powershell
Get-CimInstance Win32_PnPSignedDriver | Where-Object { $_.Manufacturer -match 'Dolby|Realtek' -or $_.DeviceName -match 'Smart Sound' } |
  Select-Object DeviceName, DriverVersion, DriverDate
Get-AppxPackage -Name '*Dolby*' | Select-Object Name, Version
Get-PnpDevice -Class AudioEndpoint -PresentOnly | Where-Object Status -eq OK | Select-Object -ExpandProperty FriendlyName
$r = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render'
Get-ChildItem $r | Where-Object { (Get-ItemProperty $_.PSPath).DeviceState -eq 1 } | ForEach-Object {
  "-- active output endpoint"
  $fx = Get-ItemProperty "$($_.PSPath)\FxProperties" -ErrorAction SilentlyContinue
  if ($fx) { $fx.PSObject.Properties | Where-Object { $_.Name -match ',\d+$' } | ForEach-Object {
    $slot = $_.Name.Split(',')[1]
    foreach ($c in @($_.Value)) { if ($c -is [string] -and $c.StartsWith('{')) {
      $d = (Get-ItemProperty "Registry::HKEY_CLASSES_ROOT\CLSID\$c\InprocServer32" -ErrorAction SilentlyContinue).'(default)'
      if ($d) { "FX slot {0}: {1}" -f $slot, (Split-Path $d -Leaf) } } } } }
}
```

On this laptop the last loop printed `FX slot 13: DolbyDax3Apo.dll`, `FX slot 15: DolbyDax3Apo.dll`,
`FX slot 15: RltkAPOU64.dll` and `FX slot 19: DolbyDax3Apo.dll`.

#### What Googlebook does now

What the image injects (`C:\GB-x86\inject\native\components\audio.json:41-79`): the HAL pointer
`audio_default.xml` (card id `sofhdadsp`), and per card `audio_config.xml`, `mixer_paths.xml` and
`volume_curves.xml`, plus the SOF firmware and topologies. **No effect configuration, no EQ, no Dolby setting.**
lapis's own audio HAL (DRAS, "desktop audio HAL") does the rest. Audio policy comes from the HAL
("Config source: AIDL HAL", `audio-b1-t105.txt:1404`), so no `audio_policy_*.xml` file is involved.

| stage | state on the first SSD start | where |
|---|---|---|
| Output stream | 48 kHz, 2 ch, float, speaker (`AUDIO_DEVICE_OUT_SPEAKER`); the speaker PCM accepts 48 kHz only | `audio-b1-t105.txt:1133-1142`, `:299-300` |
| Routes | speaker = PCM 0, headphone = PCM 31, built-in mic = PCM 6, headset mic = PCM 0 | `sof-hda-dsp_sb3/audio_config.xml:24-41`, `audio-b1-t105.txt:1079` |
| Android effects | **0 effect chains** on both output threads (idle: nothing was playing) | `audio-b1-t105.txt:1193`, `:1246` |
| HAL effect for this card | DRAS reports `audio_effect: None` for our card | `audio-b1-t105.txt:1079` |
| Dolby in the image | lapis ships Dolby DAX3 (`ro.vendor.dolby.dax.version` DAX3_3.13.0.9_r1), the Dolby service `dms-hal` and a Dolby codec service run, and DRAS says `should_enable_dolby_service: true`. This is lapis's tuning for lapis's own speakers. Whether DRAS applies any Dolby stage to our card is [not checked]; the `None` above suggests it does not [inference]. | `boot1-t420.txt:2427`, `:1455`, `:1556`; `audio-b1-t105.txt:1063` |
| DSP (SOF) | the playback pipelines have a volume stage only (PGA); the only EQ in the topology is the capture high-pass (`EQIIR2.0`, `:283`). Loaded topology: `sof-hda-generic.tplg` (`:842`) | `reference\sof-hda-generic-2ch.tplg.decoded.conf:252-304`, `:590-642` |
| Gains | SOF PGAs 32/32 = 0 dB; codec Speaker / Headphone / Master 87/87 = 0 dB (87 steps of 0.75 dB, the top step is 0 dB, there is no boost above it); Android master volume 1.0 | `audio-b1-t105.txt:245-254`, `:281-288`, `:184`, `:196`, `:1147`; `mixer_paths.xml:8-22`; `gb_audio.sh:154-165` |
| Volume curve (speaker) | 16 points, index 0 = -49 dB ... index 15 = -0.15 dB (headphone: 101 points, -70 dB ... -0.25 dB). How DRAS maps these to Android's media volume steps is [not checked]. | `sof-hda-dsp_sb3/volume_curves.xml:18-35`, `:36-138` |
| Media volume index | **not in the logs**: the diag cuts `dumpsys media.audio_policy` at 300 lines, before the volume part | `gb_audiodiag.sh:201-202` |

So nothing is wrong or clipped. The chain is clean and flat, and that is the problem. The gains cannot go up:
every stage is already at its 0 dB maximum. Louder would mean digital clipping.

Side finding (not the speakers, for the triage): the built-in mics are on the codec (pin `0x12`,
`audio-b1-t105.txt:912`; Windows also shows them as a Realtek "Microphone Array"), the firmware table lists no
DSP microphones ("DMICs detected in NHLT tables: 0", `:839`), and PCM 6 does not exist on this start
(`:153-157`). But `audio_config.xml:32-36` points the built-in mic at PCM 6. So the built-in mic may record
nothing [inference]. Check with any recorder app. The likely small fix is to point `InternalMic` at PCM 0
(the codec switches between the built-in and the headset mic by itself); this is untested.

#### Options, ranked

**A. No rebuild (try these first).**

1. **An equalizer app from the Play Store** (the owner installs it; settings such as `pm grant` only after the
   owner's yes, section 4.2 C). Android lets an app put an effect on a session or on the whole output. The
   whole-output way ("session 0") is deprecated but still works on some devices
   ([Android AudioEffect](https://developer.android.com/reference/android/media/audiofx/AudioEffect),
   [Esper: why Android equalizer apps are inconsistent](https://www.esper.io/blog/android-equalizer-apps-inconsistent)).

   | app | how it works | notes |
   |---|---|---|
   | **Wavelet** (`com.pittvandewitt.wavelet`) | Android's DynamicsProcessing effect (EQ, compressor, limiter). Per player session; "legacy mode" uses the whole output | Optional: `adb shell pm grant com.pittvandewitt.wavelet android.permission.DUMP` + notification access to find more players (undo: `pm revoke ...`) ([README copy](https://github.com/tobychidi/Wavelet); [Esper](https://www.esper.io/blog/android-equalizer-apps-inconsistent)). Try legacy mode so that Chrome is covered too. |
   | **Poweramp Equalizer** (`com.maxmpz.equalizer`) | the same Android effects, per known player | The same DUMP grant from adb unlocks more players (Poweramp forum). Android 9+. |
   | **RootlessJamesDSP** (`me.timschneeberger.rootlessjamesdsp`, GPL-3.0, Play and F-Droid) | records the internal audio and plays it again processed: limiter, compressor, dynamic bass boost, graphic / arbitrary EQ, **convolver** (impulse responses) | Setup needs Shizuku or adb once. Apps that block internal capture stay unprocessed; **Chrome and Spotify are named**. Android 10+. ([F-Droid](https://f-droid.org/en/packages/me.timschneeberger.rootlessjamesdsp/)) |

   Caveats for all three:
   * They need the effects to exist in lapis's effect service. On this image the usual AOSP effect service is not
     installed under its usual name (`boot1-t40.txt:7662`); another process serves the effect factory. Whether
     Equalizer and DynamicsProcessing are offered is the **first check** (C3).
   * Use one effects app at a time. RootlessJamesDSP cannot run next to DynamicsProcessing apps such as Wavelet.
   * An equalizer alone gives back the tone, not the loudness. Add the compressor or limiter stage.
   * Protect the speakers: with a bass boost, set the pre-gain to about -6 dB, keep the boost at or below +6 dB,
     keep the limiter on, and do not boost below about 100 Hz [inference: small laptop drivers]. Windows' Dolby
     "regulator" does this job there; Googlebook has nothing like it.
   * A first curve to try by ear (not measured): +4 to +6 dB from 150 to 400 Hz, -2 dB around 3 kHz, +2 dB at
     10 kHz, limiter on. Part C2 replaces it with a measured curve.
2. **Android's own settings.** Settings > Sound: there is no equalizer in Android's settings [inference]. If a
   "Spatial audio" switch appears, try it. The spatializer is on in this image (`audio-b1-t105.txt:740-742`), but
   it mostly works on multichannel content, so stereo music will change little [inference]. Keep the media volume
   at the same step for every comparison.

**B. Small image changes (built and tested on Windows, section 5; only after part C).**

| # | change | file, size | risk | expected gain |
|---|---|---|---|---|
| B1 | Turn on lapis's own HAL-side effect / Dolby path for our card, **if** lapis's own card configs show how (an effect element in `/vendor/etc/audio/<lapis card>/audio_config.xml`) | `sof-hda-dsp_sb3/audio_config.xml`, a few lines | DRAS refuses the file = no sound until the next image; the tuning is lapis's, not the Surface's | unknown, maybe the largest; investigate first (C3, read only) |
| B2 | A louder speaker curve at the middle steps, so the same slider position plays as loud as Windows | `sof-hda-dsp_sb3/volume_curves.xml` (16 points, file 4.4 KB) | low | loudness only, no tone; the top step stays -0.15 dB |
| B3 | Mixer default gains or "boost" | `mixer_paths.xml` / `gb_audio.sh` | - | **none**: every gain is already at its 0 dB maximum and there is no speaker boost control |
| B4 | A static speaker effect in `audio_effects.xml` (`<deviceEffects>`, Android 11+, [AOSP audio effects](https://source.android.com/docs/core/audio/audio-effects)) | new XML, about 1 KB | low | low: the file only switches an effect on with its defaults; DynamicsProcessing's defaults are flat, and DRAS may not read this file |
| B5 | A speaker EQ and DRC in the DSP, in the speaker-only PCM 0 pipeline (pipeline 30), so the headphones stay flat | a rebuilt `sof-hda-generic.tplg` (about 45 KB) from SOF 2.2 sources, plus coefficients | medium to high: a bad topology means no sound card (fallback: `snd_intel_dspcfg.dsp_driver=1` at the boot menu, manual C-4). Larger than a "small fix" | high, and it covers every app including Chrome |

**C. What to measure, and how.**

1. **The 5-minute proof (the owner, on Windows).** Play the same song at the same volume. Then Settings > System >
   Sound > Speakers > Audio enhancements: **Off**
   (Microsoft Support, "Fix sound or audio problems in Windows", Windows 11 step 3, on support.microsoft.com).
   If Windows now sounds as thin as Googlebook, processing is the whole difference. Set it back to "Device Default
   Effects" afterwards.
2. **A measured curve (optional, the owner).** A phone with a spectrum or SPL app, fixed about 50 cm in front of the
   screen, the same pink-noise file at the same loudness. Measure Windows (enhancements on), Windows (off) and
   Googlebook. The phone microphone's own colour cancels out in the difference: "Windows on" minus "Googlebook" is
   the EQ curve to enter in the app (A1) or later in B5. Measuring Dolby's output by Windows loopback (the ThinkPad
   method) also works, but that result is derived from Dolby's processing: keep it private, never in the
   repository.
3. **The adb diagnostics an agent inside Googlebook runs first** (Wireless debugging, the shell user; everything
   below only reads; raw output stays in `~/work/private/`, rule 3.2.3):

   ```bash
   adb devices -l && adb shell id                    # expect uid=2000(shell)
   D=~/work/private/audio-$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$D"
   adb shell getprop ro.build.fingerprint > "$D/fingerprint.txt"

   # --- 1. idle (nothing playing)
   adb shell dumpsys audio                    > "$D/dumpsys-audio-idle.txt"
   adb shell dumpsys media.audio_policy       > "$D/audio_policy-idle.txt"
   adb shell dumpsys media.audio_flinger      > "$D/audio_flinger-idle.txt"
   adb shell dumpsys android.hardware.audio.core.IModule/default > "$D/dras-noargs.txt" 2>&1
   for a in options mixer streams; do
     adb shell dumpsys android.hardware.audio.core.IModule/default $a > "$D/dras-$a.txt" 2>&1
   done
   adb shell dumpsys android.hardware.audio.effect.IFactory/default > "$D/effect-factory.txt" 2>&1
   adb shell dumpsys vendor.dolby.dms.IDms/default > "$D/dolby-dms.txt" 2>&1
   adb shell service list                     > "$D/service-list.txt"
   adb shell pm list packages                 > "$D/packages.txt"
   adb shell getprop | grep -i -E 'audio|dolby|dax|spatial' > "$D/props-audio.txt"
   adb shell 'ls -lR /vendor/etc/audio /vendor/etc/data/hal_configs' > "$D/vendor-etc-audio.txt" 2>&1
   adb shell 'ls -l /vendor/etc /odm/etc /apex/com.android.hardware.audio.desktop/etc' 2>&1 |
     grep -i -E 'effect|dolby|dax|audio|dras' > "$D/etc-effect-files.txt"
   adb shell 'ls -lR /vendor/etc/dolby' > "$D/vendor-etc-dolby.txt" 2>&1
   adb shell 'cat /vendor/etc/audio_effects_config.xml /vendor/etc/audio_effects.xml' > "$D/effects-config.txt" 2>&1
   adb shell 'grep -n -i -E "effect|dolby|dax" /vendor/etc/audio/*/audio_config.xml' > "$D/card-effect-lines.txt" 2>&1
   adb shell 'cat /proc/asound/cards /proc/asound/pcm'  > "$D/asound.txt" 2>&1
   adb shell 'cat "/proc/asound/card0/codec#0"'         > "$D/codec0.txt" 2>&1

   # --- 2. while music plays on the speaker (the owner starts a song; note the media volume step)
   adb shell dumpsys media.audio_flinger      > "$D/audio_flinger-play.txt"
   adb shell dumpsys audio                    > "$D/dumpsys-audio-play.txt"
   adb shell 'cat /proc/asound/card0/pcm0p/sub0/hw_params /proc/asound/card0/pcm0p/sub0/status' > "$D/pcm0p-play.txt" 2>&1
   adb logcat -d -b main,system -v threadtime | grep -i -E 'dras|dolby|dax|effect|spatializ' > "$D/logcat-audio.txt"

   # --- 3. the result lines for the notes (check them for personal values before copying)
   grep -n -E 'STREAM_MUSIC|Max:|Current:' "$D/dumpsys-audio-play.txt" | head -n 20
   grep -n -i -E 'Effect Chains|Sample rate|HAL format|Output devices|PortVol' "$D/audio_flinger-play.txt" | head -n 40
   grep -n -i -E 'curve|index|speaker' "$D/audio_policy-idle.txt" | head -n 80
   grep -n -i -E 'dynamics|equali|bass|loudness|virtuali|spatial|dolby|dap' \
     "$D/effect-factory.txt" "$D/audio_flinger-idle.txt" "$D/effects-config.txt" "$D/card-effect-lines.txt" | head -n 60
   grep -n -E 'Pin Default|Speaker|N/A|Fixed' "$D/codec0.txt" | head -n 60
   ```

   What each answer decides:
   * the effect list (`effect-factory.txt`, `effects-config.txt`): DynamicsProcessing / Equalizer present = option A
     can work; absent = only RootlessJamesDSP (capture-based) or B5;
   * `card-effect-lines.txt`, `vendor-etc-dolby.txt`, `dras-noargs.txt`: whether lapis's own cards use an effect or
     Dolby element in their `audio_config.xml`, and with which fields = whether B1 is possible;
   * `audio_policy-idle.txt` + `dumpsys-audio-play.txt`: the media steps and the dB per step = whether B2 is
     worth it;
   * `audio_flinger-play.txt`: effects active during playback, the track gains, the real sample rate;
   * `codec0.txt`: the number of speaker pins (rules out a second, silent speaker pair).

   As the shell user `tinymix` cannot open the sound card (`/dev/snd` belongs to the audio group) [inference], and
   some HAL dumps may be refused; then write "refused" into the notes. The shell domain looked permissive in the
   boot logs (`audio-b1-t105.txt:925`, `:954`) [inference]. Nothing here changes a setting.

Record it as a new finding ("HW4-audio-thin", LOW, HARDWARE) in the day's NOTES file (section 4.2 E), with the
answers above and the owner's result of C1.

### 5.2 NVIDIA: how to check it

The GTX 1650 Max-Q (TU117, PCI `10de:1f91` at `0000:02:00.0`, behind root port `0000:00:1d.4`) sits in the keyboard
base. Googlebook uses it for **apps only**: the screen, the system UI and the launcher always stay on Intel [S1, S2].
Default mode **Auto**: with the base attached, nouveau loads after Android has started, a self-test runs once per
build, and then games and the apps marked **Use NVIDIA** render on NVIDIA (NVK) [S1, S3]. When nothing uses it, the
dGPU must sleep in **D3cold**, or the laptop gets no S0ix and drains its battery [S4].

#### What the first start from the SSD already shows (2026-11-08, redacted logs)

| Item | Seen on the first internal start | Source |
|---|---|---|
| Keyboard base | attached (`vendor.gb.dtx.base=attached`, DTX ready) | [L1] |
| nouveau, Auto mode | `boot_completed` at 44.9 s; `gb_nouveau` started 45.8 s, insmod 49.7 s (loaded in 2050 ms, GSP-RM 570.144), `result=healthy` 66.8 s, `result=clean` 177.3 s; D3cold reached 16.2 s after the insmod; `argv=runpm=1 modeset=2` | [L2] |
| Firmware payload | mounted, 30 of 30 TU117 firmware files match | [L3] |
| **Self-test** | started 179.3 s, **PASS** (attempt 1): NVK sees "NVIDIA GeForce GTX 1650 with Max-Q Design (NVK TU117)", Mesa 26.3.0-rc2; `ahb-i915-readback` PASS (65,536 texels bit-exact on the CPU and on ANV), `a17-swapchain-model` PASS; HWUI queueCount aborts 0 | [L4] |
| App graphics | `vendor.gb.gfx_active=nvidia`, reason `ok`, from 181.3 s; the render node `renderD129` became `0666` | [L5] |
| Not tried yet | no NVIDIA app ran (holders empty, 0 apps opted in), no detach, no `[NVIDIA test]` | [L5, T1] |
| **Open finding (analyser item 25 FAIL)** | in all four snapshots (t40, t90, t180, t420) the dGPU was **D0, runtime active**, refcnt 0, state `standby`. At t420 it had been active 126.8 s and suspended 312.5 s, so it does sleep but keeps waking up: five short `standby -> busy:1` episodes (0.1-2 s, at 99, 135, 396, 411 and 424 s). No `d0-idle` fault was latched (that needs more than 60 s of D0 in a row). The snapshot code runs `dumpsys gpu` and `cmd gpu vkjson` a few seconds before it prints the `== dgpu` lines, which may itself wake the dGPU [inference, not proven]. The live check below decides it | [L6, T1, S5] |

This is the first real self-test pass. Before it, app graphics stayed on Intel on every hardware start: wave 8 stick
(no detach device yet, `nodtx`), wave 9 (`untested`), wave 10 stick (`testerror`: the self-test was stuck in a global
`sync` on the slow stick, `reason=esp`). D3cold was seen on all three earlier images [H1-H4].

**After the Google update.** The 34.25 MB Google update completed into slot A at 345 s and `gb_slotsync` made
`(slot A)` the boot default [L7]. The next start runs that new Google build. Its build id is part of the self-test
identity, so the self-test runs **once more** on that start [S6].

#### Owner steps (no computer needed)

1. Base attached, charger in. Start Googlebook with the **Restart to Googlebook** shortcut. Let the 4-second menu
   start its default entry by itself (after the update that is `Googlebook OS - Surface Book 3 (slot A)`,
   decision 7). Never pick
   `[NVIDIA test]` unless an agent asks for it [G1].
2. After sign-in, open Quick Settings and watch the **Graphics** tile [S2, S7]:
   * first start of a build: `Intel` -> `Intel (starting NVIDIA)` -> `Intel (testing NVIDIA)` -> **`NVIDIA`**, about
     3 minutes after power-on, with the notice "Performance base connected" [L2, L4, S8];
   * later starts of the same build: `Intel (starting NVIDIA)` -> **`NVIDIA`** about 1-2 minutes after power-on,
     once the load is healthy (the stored pass is reused) [S9].
3. Long-press the tile: **Settings > System > Graphics** [S7]. Good:
   * Mode `Auto - recommended`;
   * `Apps you open now use: NVIDIA`;
   * NVIDIA GPU: **`asleep (D3cold)`** while no game is open. Reopen the page after a minute of idling. If it still
     says `active` with nothing open, note the clock time: that is the open finding above;
   * Self-test: `Passed on <date>`.
4. **One NVIDIA app** (checklist 15, never done yet): open a Vulkan game from the Play Store. The tile should show
   `NVIDIA (busy 1)`, and the page `used by 1 app(s)`. Close the game: after about 10 s the page shows
   `asleep (D3cold)` again [S10, S11].
5. Optional, later starts: detach and reattach the screen with and without the game open (checklist 16); one start in
   **Intel only** mode, where the dGPU must stay in D3cold (checklist 17) [S10].
6. Keep Googlebook running 16 minutes (the last log is written at 15 minutes), then collect the logs from Windows as
   in GUIDE-DUALBOOT-120GB.md 6.5 [G2].

Bad signs, tell an agent: "NVIDIA app graphics off" (self-test failed), "NVIDIA GPU problem", "Switched to Intel
graphics" (crash guard), "NVIDIA GPU fault", "NVIDIA GPU not available", tile `Fault` or `Restart required`, or a hang
or a broken picture after Android started (then: Recovery below) [S12].

#### Agent inside Googlebook (adb shell as the shell user, no root)

Set up Wireless debugging as in section 4.2 B (the owner pairs). `adb shell id` must print `uid=2000`.
Everything below only reads. Save raw output in `~/work/private/`; put only result lines into the notes [P1, P2].
Run the D3cold check first and with nothing else running: no game, no `dumpsys gpu` or `cmd gpu vkjson` just before.

```bash
# 1. Driver, power and policy snapshot (read-only, made for the shell user)
adb shell sh /vendor/bin/gb_dgpu_check.sh
# 2. The image's own verdict: waits up to 60 s for D3cold, prints PASS/FAIL/UNKNOWN
adb shell sh /vendor/bin/gb_hwcheck.sh -n dgpu
# 3. Sample the power state every 2 s for 60 s (reads cached sysfs only; never wakes the dGPU)
adb shell 'd=/sys/bus/pci/devices/0000:02:00.0; for i in $(seq 30); do echo "$(cat $d/power_state) $(cat $d/power/runtime_status) $(getprop vendor.gb.dgpu.state)"; sleep 2; done | sort | uniq -c'
# 4. The properties
adb shell getprop | grep -E 'vendor\.gb\.(gfx|nouveau|dgpu|dtx)|persist\.vendor\.gb\.gfx'
# 5. gb_power's own view (read-only commands, the same query the boot logs make; role "shell" is allowed)
adb shell "printf 'hello\nstatus\nget-mode\n' | timeout 5 toybox nc -q 1 -U /dev/socket/gb_power"
# 6. Kernel log lines (may be refused for the shell user on this build: then rely on 1-5)
adb shell "dmesg 2>/dev/null | grep -E 'gb_nouveau: (auto:|result|kill switch)|gb_power: (self-test|app graphics|dGPU state|dGPU .*still in D0)|nouveau 0000:02:00.0: gsp: RM version'"
# 7. Only the gb.nouveau token of the kernel command line (never print the whole line: it holds the boot partition id)
adb shell "grep -o 'gb\.nouveau=[^ ]*' /proc/cmdline || echo 'no gb.nouveau token (normal entry)'"
# 8. With a game open (owner step 4)
adb logcat -d -s gb_gfxdriver | tail -n 20
adb shell getprop vendor.gb.dgpu.state      # busy:1 while the game runs, standby after it closed
```

What PASS looks like (right column: the first internal start, from its logs):

| Check | PASS | 2026-11-08 |
|---|---|---|
| `vendor.gb.dtx.base` | `attached` | `attached` |
| `vendor.gb.nouveau.mode` / `.result` | `auto` / `clean` (healthy about 15-20 s after the load, clean about 2 min later) | `auto` / `clean` |
| `vendor.gb.nouveau.argv` | `runpm=1 modeset=2` | same |
| `vendor.gb.dgpu.payload` | `mounted` | `mounted` |
| `vendor.gb.gfx_test`, `persist.vendor.gb.gfx_ok` | `pass`, `1` | `pass`, `1` |
| `vendor.gb.gfx_active` / `.gfx_reason` | `nvidia` / `ok` | `nvidia` / `ok` |
| `vendor.gb.dgpu.state` | `standby` idle, `busy:N` while an NVIDIA app runs | `standby` |
| gb_dgpu_check.sh | `0000:02:00.0: vendor=0x10de device=0x1f91 class=0x030200 driver=nouveau`; idle: `runtime=suspended state=D3cold` for the dGPU and `0000:00:1d.4`; `renderD128 -> 0x8086 i915`, `renderD129 -> 0x10de nouveau` | nodes right; **D0 / active** in every snapshot (open) |
| gb_hwcheck.sh `dgpu` | `PASS dgpu 0000:02:00.0 in D3cold ...` (FAIL text: `stayed D0 ... for 60 s`) | not run yet |
| Sampling loop (3) | mostly `D3cold suspended standby` | not run yet |
| `get-mode` | `"mode":"auto"`, `"active":"nvidia"`, `"reason":"ok"`, `"gfx_ok":1`, `"test":"pass"`, `"cap":1`, `"base":"attached"` | exactly that |
| `status` | `"driver":"nouveau"`, `"wedged":0`, `"fault_reason":""`, idle `"power_state":"D3cold"` | `"power_state":"D0"` at 443 s |
| NVIDIA app | logcat `NVK: 1 NVIDIA physical device(s) first, then 1 platform device(s)`; state `busy:1` | not tried |

A FAIL of check 2 or 3 with refcnt 0 and no game open confirms the open finding. Then record the times of the
`busy:1` episodes and the native holder names (`getprop vendor.gb.dgpu.holders` while busy; write app names as `app`)
and hand it to the Windows side: it is a real hardware finding (small-fix scope; row HW4-dgpu-idle in
section 1.5) [P3].

Changes need the owner's yes first, one at a time, each written into the notes [P2]:
`adb shell setprop persist.vendor.gb.gfx_mode intel` (or `auto`), and the socket commands `run-selftest` or
`set-mode <m>` (the same ones the Graphics page sends) [S13]. Never: anything under `/data/vendor/gb`, the boot tokens
(`gb.nouveau=0` is set only in the boot menu), the A/B slot [P2].

#### The `[NVIDIA test]` start (only when an agent asks for it)

* **What it is:** the entry `... (slot X) [NVIDIA test]` adds `gb.nouveau=test`. It loads nouveau once with extra
  diagnostics (kernel log streamed to `/data`, `debug=gsp=debug`, no panic on an oops) and runs a scripted sequence:
  D3cold of every NVIDIA function within 30 s, 30 power samples 10 s apart, `nvk_probe -t all -n 200`, a PM stress
  run (30 probes, autosuspend 500 ms), then a 5-minute suspend test. Apps stay on Intel (tile `Intel (test boot)`),
  and no self-test runs. The next normal start is unaffected [S14, S15].
* **Which entry:** the one of the slot the normal start uses. After the update that is
  `Googlebook OS - Surface Book 3 (slot A) [NVIDIA test]`. A `(slot B)` entry would make slot B the default again
  (`gb_slotsync` keeps the default on the slot that booted), so it would leave the Google update [G3, S16]. Not on
  the first start of a new build.
* **How:** base attached, charger in. From Windows, **Restart to Googlebook**. In the 4-second menu press an arrow key,
  choose the `[NVIDIA test]` entry, press Enter. Sign in and leave it alone. The sequence starts once the load is
  healthy (about 1 minute after power-on). Roughly 8-14 minutes after power-on (an estimate from the script's steps)
  the notice **"NVIDIA test: suspend"** asks: close the lid for 30 s, then open it, 3 times within 5 minutes. Then
  **"NVIDIA test finished"**: wait until 15 minutes after start-up, then restart [S15, S17].
* **Where the results land:** `/data/vendor/gb/report/test-<N>.txt`. Each log snapshot copies the newest one, so it
  appears in `gblog\boot<N>-t420.txt` / `-t900.txt` of that start under the header
  `== /data/vendor/gb/report/test-<N>.txt`. Collect from Windows (GUIDE-DUALBOOT-120GB.md 6.5). If the copy in the
  last file has no `== end uptime_s=` line, the next normal start's files carry the complete copy [S18, G2].
* **Read it for:** `0000:02:00.0 d3cold_s=<n>` (not `d3cold=timeout`); `nvk_probe_exit=0`;
  `pm_stress runs=30 probe_failures=0 runtime_status_error=0 warnings=0 etimedout=0`;
  `suspends_seen=3` with a `resume success=...` line per lid close; `== end uptime_s=...` [S15].

#### Recovery: NVIDIA off for one start (`gb.nouveau=0`) on the internal install

Use it if a start with the base attached hangs or the picture breaks once Android is up.

1. Hold Power for 10 s. The next power-on starts **Windows** (the default). Usually the crash guard alone fixes it:
   the next Googlebook start keeps the driver off, sets **Intel only** and shows "Switched to Intel graphics" [S3, S12].
2. For the manual override: in Windows, **Restart to Googlebook**. In the 4-second menu press an arrow key (this stops
   the countdown) and highlight the **normal** entry, the one without `[NVIDIA test]` (after the update
   `... (slot A)`). Press **e**, then **End** (the editor opens with the cursor at the start of the line), type a space
   and `gb.nouveau=0`, check that the line now ends with it, press **Enter**. This start only: nouveau is not loaded,
   and every app uses Intel [S19, S20].
3. Check it: tile `Disabled for this boot`; agent: `vendor.gb.nouveau.mode=disabled`, `vendor.gb.nouveau.result=disabled`,
   `vendor.gb.gfx_reason=kill`, check 7 prints `gb.nouveau=0`, and the dGPU is driverless and in D3cold [S21].
4. To stay on Intel: tile until `Off (Intel mode)`, or Settings > System > Graphics > **Intel only** (an agent:
   `setprop persist.vendor.gb.gfx_mode intel`, with the owner's yes). To try NVIDIA again, choose **Auto** [S19].
5. Do not type `module_blacklist=nouveau`: the kernel keeps only the last `module_blacklist=`, so it would drop the
   entry's own list (TPM, SPI flash, GSMI) for that start [S20].

#### Sources

Logs: the redacted `share\` copy of the first SSD start's logs (`gblog\boot1-t*.txt`), its analyser output
`TRIAGE.md` and the triage report `TRIAGE-HW4.md` (2026-11-08; where they are: private `HANDOFF.md`
section 0). The line numbers marked `TRIAGE.md` are in the analyser output.

* [L1] boot1-t420.txt:2529-2531; TRIAGE.md:13, 28.
* [L2] boot1-t420.txt:2889-2954 (2889 auto start, 2910 insmod, 2913 RM 570.144, 2920 loaded, 2954 healthy),
  3132-3133 (clean), 3746-3763 (load record, `d3cold_ms=16220`, `health=clean`); TRIAGE.md:59 (boot_completed).
* [L3] boot1-t420.txt:2526, 3678.
* [L4] boot1-t420.txt:3140-3175, 3764-3847 (3781 device, 3797/3803 ahb-i915-readback, 3840-3842 a17-swapchain-model,
  3846 hwui), 2773-2781 (record).
* [L5] boot1-t420.txt:2532-2534, 3158-3159, 2736-2737 (status, get-mode: holders `[]`, `opt_in_n:0`).
* [L6] boot1-t420.txt:2582-2584, 2589-2621, 2650-2660, 3010-3016, 3067-3071, 3401-3469; boot1-t40.txt:2359;
  boot1-t90.txt:2386; boot1-t180.txt:2506; boot1-t420.txt:3531-3532 (driverless D3cold at 43 s, before the load).
* [L7] boot1-t420.txt:3365-3369; TRIAGE.md:53.
* [T1] TRIAGE.md:43-46, 374-429, 606-608.
* [S1] C:\GB-x86\out\README-FLASH.md:459-487. [S2] README-FLASH.md:472-475.
* [S3] docs/NVIDIA.md:85-103, 131, 197 (repository working copy `C:\GB-repo\googlebook-sb3`).
* [S4] docs/DEVICE-STATUS.md:501 (row 17), 555 (row 46).
* [S5] C:\GB-x86\build_esp.py:2937, 2942-2952 (the snapshot order); C:\GB-build\native\dgpu\INTERFACES.md:184-185
  (idle-D0 fault after 60 s, autosuspend 5000 ms), 931.
* [S6] INTERFACES.md:2884-2889, 2970-2972; docs/NVIDIA.md:146-148.
* [S7] INTERFACES.md:3048-3077 (tile), 3080-3092 (page), 3144-3165 (reason texts).
* [S8] INTERFACES.md:1543 (N16); README-FLASH.md:478-479.
* [S9] INTERFACES.md:2466-2485 (rows 11-16).
* [S10] docs/DEVICE-STATUS.md:607-621 (checklist 14-18); docs/NVIDIA.md:273-286.
* [S11] INTERFACES.md:911-926 (states), 185 (autosuspend).
* [S12] INTERFACES.md:1537-1547 (notices N01-N20); docs/NVIDIA.md:187-201.
* [S13] INTERFACES.md:972, 993-1000 (socket 0666, role `shell`), 1009-1019, 1048-1085 (status), 2985-2994;
  build_esp.py:2958-2959 (the boot logs' own query).
* [S14] INTERFACES.md:44, 222-224, 827-839, 2034-2037; C:\GB-build\native\nouveau-kernel\native\gb_nouveau.sh:1442,
  1477-1484; C:\GB-build\native\nouveau-kernel\native\gb_nouveau.rc:54, 91.
* [S15] C:\GB-build\native\nouveau-kernel\native\gb_dgpu_report.sh:7-10, 529-689 (606 N13, 683 N14, 685 `== end`);
  INTERFACES.md:900-905.
* [S16] C:\GB-build\mouse\gb_slotsync.c:9-10, 702-706, 1050-1053; C:\GB-x86\build_esp.py:4333-4339 (nvtest entry);
  C:\GB-build\native\dualboot\build_internal.py:42, 154 (title, 4-second menu, NVIDIA test entry).
* [S17] INTERFACES.md:1528-1529 (N13, N14 texts); README-FLASH.md:489-494.
* [S18] build_esp.py:2975-2977 (newest `test-<N>.txt` copied into every snapshot).
* [S19] README-FLASH.md:496-508; docs/NVIDIA.md:203-210.
* [S20] INTERFACES.md:2049-2050; build_esp.py:4328 (`editor yes`); docs/NVIDIA.md:212-216.
* [S21] gb_nouveau.sh:1343-1346; INTERFACES.md:920, 2472, 3060.
* [G1] C:\GB-build\native\dualboot\GUIDE-DUALBOOT-120GB.md:573-578, 746-750. [G2] GUIDE-DUALBOOT-120GB.md:638-665.
  [G3] GUIDE-DUALBOOT-120GB.md:746-750.
* [P1] this file, section 4.2 B. [P2] sections 4.2 C and 4.2 D. [P3] section 4.2 E.
* gb_dgpu_check.sh: C:\GB-build\native\nouveau-kernel\native\gb_dgpu_check.sh:1-10, 63-96. gb_hwcheck.sh:
  C:\GB-build\native\hw-gap-audit\tree\gb_hwcheck.sh:6-12, 28-35, 650-674.
* [H1] docs/DEVICE-STATUS.md:173 (wave 8). [H2] docs/DEVICE-STATUS.md:127-130 (wave 9). [H3] docs/DEVICE-STATUS.md:70
  (wave 10). [H4] C:\GB-build\native\w11prep\anr-battery-hw3\RESULT.md:131, 421-424;
  C:\GB-build\native\w10prep\misc-suspend\evidence\triage\first-boot3.md:46 (`nodtx`), second-boot1.md:45 (`untested`).

---------------------------------------------------------------------------------------------------------------------

## 6. State log

Append one line per change, newest last. Format: date (UTC or local, say which) | environment and agent | what
changed | where it is recorded. Never put personal values here.

| date | environment | what changed | record |
|---|---|---|---|
| 2026-11-07 | Windows, Claude Code | First edition of this file. The owner's guide `C:\GB-build\native\dualboot\GUIDE-DUALBOOT-120GB.md` written. The owner's plan: dual boot with 120 GiB from D:, straight to the internal install, agents inside Googlebook. Nothing was installed or changed on the disks. | private `HANDOFF.md` section 0 (2026-11-07 bullet) |
| 2026-11-07 (local) | Windows, Claude Code | Second edition of this file and of the guide, after a safety review and a privacy / portability review: dry runs and real runs split into separate blocks with a STOP; a `post_shrink` GPT backup; the shadow-copy warning; space on D: after the install; adb pairing by the owner only; raw output in `~/work/private/`; placeholders for the owner's folder and share; the push gate (rule 3.2.4) blocked until the maps audit is replaced and `collect.py` leaves the guide out; the release USB image now on `<owner share>\Release\`. Nothing was installed or changed on the disks. | private `HANDOFF.md` section 0 (2026-11-07 dual-boot bullet) |
| 2026-11-07 (local) | Windows, Claude Code | Third edition of this file and of the guide: the C: route. D: cannot shrink (`$Mft::$BITMAP` at its end), so the owner shrinks C: by 120 GiB and installs by Route A (ISO option 2) with one stick (`s` at the GPT-copy prompt, no tools `w`, import with `-EspId`); Route B marked not usable; expected layout "region after partition #3", Googlebook #6-#23, userdata 108,714 MiB; undo = option 3 then extend C:; decision 6 now describes the share layout (`Workspace\`, `Archive\`, `Release\`, `Backups\`, VM copy at the top), the builder VM's move off C: and the laptop kit; rules 3.3.11 and 5.6 updated for the space. Docs only: nothing was installed or changed on the disks. | private `HANDOFF.md` section 0 (2026-11-07 third-edition bullet) |
| 2026-11-07 (local) | Windows, Claude Code | Third edition revised after a review of the guide (0 HIGH; the 2 MEDIUM findings checked against the code and fixed): the guide's A8 now covers a `FAILED:` result (photos first; the journal and the installer's own GPT copies live only in memory) and a power cut (the import's dry run, then option 2's read-only checks decide between restoring `post_shrink` and option 3); 6.4, 7.1 and 10 cover a Windows update that adds a Recovery partition after C:; decision 5 notes both. Docs only: nothing was installed or changed on the disks. | private `HANDOFF.md` section 0 (2026-11-07 third-edition bullet) |
| 2026-11-07 (local) | Windows, Claude Code | Fourth edition: the D: rebuild. Windows cannot shrink C: or D: (`$Mft::$BITMAP` at the end of both), so the owner rebuilds D: smaller (a copy to `C:\D-staging` checked file by file, D: deleted and created again at 278,120 MiB, the data copied back and checked) and installs by Route A into the 122,881 MiB after D: (region after #4, userdata 108,715 MiB; the numbers come from the installer's own planning code run in memory); guide `C:\GB-REBUILD\REBUILD-D-AND-INSTALL-GOOGLEBOOK.md`. Decision 5 rewritten; decision 6 lists the share layout as built (`Workspace\C\`, `Workspace\D\`, `Archive\`, `Backups\` with the D: safety copy, `INDEX.md`, `restore_fixups.py`), what moved, what is still to move, and that the Googlebook VM does not run on the share's AMD Zen 3 host (no GFNI, SIGILL); 1.4, 1.6, 2.1, rules 3.2.4 (push status), 3.3.2 and 3.3.11 updated. Docs only: nothing was installed or changed on the disks. | private `HANDOFF.md` section 0 (2026-11-07 "NEXT SESSION STARTS HERE" bullet) |
| 2026-11-08 (local) | Windows, Claude Code | Fifth edition: the internal install is done and starts (the first SSD start on 2026-11-08: triage 38 PASS / 2 FAIL / 2 UNKNOWN, no hardware defect; Google's R4 -> R5 update installed itself into slot A, and the owner chose to start it). Decision 5 gets its result; decision 6 the `Backups\GB-backup-20261108\` copy and the SSH access to the share's host (alias only); new decision 7 (start R5 now; then audio and NVIDIA with Claude Code inside Googlebook). 1.1, 1.4 (the triage verdict), 1.5 (new rows HW4-*, F1-F3, the label HOST-FIX) and 1.6 updated; section 2 gets the three-places table and 2.4 (switching: the "Restart to Googlebook" shortcut; back to Windows = restart or shut down); 4.2 adjusted to the internal install, with the new 4.2 G (starter prompt); new 5.1 (audio) and 5.2 (NVIDIA) from two read-only research steps; the adb network check in 4.2 B replaced by a trusted-network rule; the log collector always with `-OutputRoot`. Docs only: nothing was installed or changed on the disks. | private `HANDOFF.md` section 0 (2026-11-08 fifth-edition bullet) |
