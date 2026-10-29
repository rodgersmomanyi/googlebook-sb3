# MEASURE_W5.md: the VNNI libz patch, before and after (wave 5 Measure step)

2026-10-26, 20:12-20:51 local time. The measurement used two throwaway VMs only. The user's VM, its pipe and its adb
address were never touched. `lapis.bin` was not opened.

Labels: **MEASURED** means read from the guests' counters, kmsg or files. **INFERRED** means reasoned from measured
facts.

---

## 0. Result

The patch works as built. On the patched VM the k15 VEX AVX-VNNI emulation count drops by 4 to 5 orders of magnitude.
No `libz.so` emulation is left at all. Boot to `sys.boot_completed` takes about **47 % less time**. The binds are
applied and read-only, they carry the reviewed bytes, and the fingerprint triggers are accepted. Nothing new shows up
in SELinux, logcat, kmsg or crash records. Every adler32 user tested works.

**The INTEGRATION.md fallback (`gb_vnni.sh`) is not needed:** init accepted both property triggers, and they fired.

### 0.1 Before/after table (MEASURED)

Both VMs booted the same disk image. "off" adds `gb.vnni=0` to the boot entry, so the kill switch removes
`/odm/vnni`. "on" is the image exactly as built. Both started from fresh data in the same second and ran at the same
time.

**Run 1: first boot, fresh /data.** Counts are the `emulated` total from `/sys/kernel/gb_vnni/summary`.

| point (guest uptime) | off (gb.vnni=0) | on (patch) | on / off |
|---|---:|---:|---:|
| t = 60 s (read at 62.3 / 62.1 s, see 1.3) | 39,117 | 16 | 4.1e-4 |
| t = 200 s | 48,428,050 | 752 | 1.6e-5 |
| t = 420 s | 126,326,002 | 9,816 | 7.8e-5 |
| at `sys.boot_completed` (off 587.5 s / on 313.9 s) | 211,368,597 | 756 | 3.6e-6 |
| t = 600 s | 211,379,858 | 51,028 | 2.4e-4 |
| t = 900 s | 223,694,019 | 169,466 | 7.6e-4 |
| t = 1200 s | 229,191,430 | 198,420 | 8.7e-4 |
| end, after KAT + install + compile (off 1675.6 s / on 1647.2 s) | 262,236,482 | 433,498 | 1.7e-3 |
| **boot time to `sys.boot_completed`** (init's kmsg time) | **587.7 s** | **313.6 s** | **-274.1 s (-46.6 %)** |
| peak 10-s emulation rate | 1,042,958 /s (t = 540 s) | 10,087 /s (t = 1380 s) | |
| boot-time `gb_dexopt` (`compile -m speed -f`, 4 UI packages, nice 10), wall | 643.5 s | 480.3 s | -25.4 % (see 2.3) |
| dex2oat-heavy action: `cmd package compile -m speed -f` on 4 packages, wall (sum) | 255.86 s | 239.88 s | -6.2 % (see 2.3) |
| emulations during that action | 19,558,757 | 66,668 | 3.4e-3 |
| adler32 throughput of `/system` / `/vendor` libz (16 MiB, on-device) | 5.9 / 5.4 MiB/s | 538.3 / 1229.1 MiB/s | about 90x / 230x |
| emulations caused by the adler32 KAT (both libraries, 3,547 cases + bench) | 3,143,919 | **0** | |

**Boot 2: second boot of the same two disks, warm data, no reset.**

| point (guest uptime) | off | on | on / off |
|---|---:|---:|---:|
| t = 60 s (read at 61.2 / 60.5 s) | 305,760 | 752 | 2.5e-3 |
| t = 200 s | 93,316,268 | 18,196 | 2.0e-4 |
| at `sys.boot_completed` (off 225.5 s / on 118.1 s) | 119,617,835 | 756 | 6.3e-6 |
| t = 420 s | 141,358,142 | 18,676 | 1.3e-4 |
| **boot time to `sys.boot_completed`** | **226.0 s** | **118.0 s** | **-108.1 s (-47.8 %)** |
| peak 10-s emulation rate | 1,160,110 /s (t = 210 s) | 1,299 /s (t = 130 s) | |

The off VM's numbers reproduce the wave-3 picture. Run 1's count at boot_completed was 211 M, against vm2's 248 M by
434 s. All of it is word-form VEX.128 with `ymm_init` close to `emulated`, and the LRU is entirely libz
`adler32_simd_` (2.4).

---

## 1. Setup

### 1.1 Inputs (from the integrate step; checked again here)

* Disk `D:\gb-w5test\vm\disk.img`. Its whole-file sha256 is `dd88458a7633ae875a6ac7e10dd6d9710efd18f55085f7d545a4f31e8afbc516`
  (re-hashed while it was cloned: `evidence\measure\00-clone-on.txt`). It was built by
  `build_usb.py --vm-test --vm-test-w5 --sparse --userdata-gib 8`, and its pristine region record is
  `D:\gb-w5test\state\pristine.json`.
* The "on" VM uses `D:\gb-w5test\vmon\disk.img`, a sparse clone. Its sha256 was computed on the source while copying
  and again on the copy read back; both equal `dd88458a...c516`.
* ESPs written into EFI-SYSTEM (written, then read back to MATCH: `evidence\measure\01-prepare-vms.txt`):
  * off: `esp\vnni-off.img` `9c303f64...fe25`, the same tree plus `gb.vnni=0` in the entry titled "(VM test, W5, VNNI off)";
  * on: `esp\vnni-on.img` `242691c7...17e8`, which is the disk's own ESP.

  After every boot, systemd-boot's `loader/random-seed` was the only ESP change
  (`evidence\measure\02-esp-diff-after-boot1.txt`). That matches the W4 rehearsal.
* Image identity, read in each guest: `ro.system.build.fingerprint` =
  `Android/desktop_system/desktop:17/CL3B.260622.276.R4/16658739:user/release-keys`. `ro.vendor.build.fingerprint` =
  `ASUS/lapis/lapis:17/CL3B.260622.276.R4/16658739:user/release-keys`. Kernel `6.18.21-android17-5-g3c2c2f588d3f-ab16302715`
  (k15). Slot `_b`.

### 1.2 The two throwaway VMs (run concurrently)

* Each VM has 4096 MiB and 2 vCPUs. CPUID masks are the user VM's (`cpuid.7.1.eax` bit 4 cleared, so AVX-VNNI is hidden
  and VEX VNNI traps into k15's emulator). NVMe disk, NAT, no USB.
* Each VM has its own root-console pipe (`\\.\pipe\gbw5_con`, `\\.\pipe\gbw5on_con`). The two vmx files differ only
  in `displayName` and that pipe name (diff in `01-prepare-vms.txt`).
* Tool: `w5\tools\w5vm.py --inst off|on`, an instance-aware version of the W4 rehearsal's `w4vm.py`. Its guard
  refuses the user's pipe, any device path, more than 4096 MiB or 2 vCPUs, and any other VM below D:\gb-w5test.
* Both were started by `vmrun start` in the same second (20:12:24 for run 1, 20:43:34 for boot 2). Each ran in its
  own process, `w5\tools\run_w5.py`, so they shared the host's contention. File barriers synchronised two points:
  "after boot" (before the checks) and the start of the compile action.
* Host: Surface Book 3, i7-1065G7 (4C/8T, SMT on), 31.6 GB. The builder VM (6 vCPUs) was running but idle. Other
  workflow agents were active on the host during the runs.

### 1.3 How each number was taken

* **Counter.** `w5\tools\gbw5_sampler.sh` runs as root and is started detached from the guest's root console. It
  reads the summary at uptime 60, 200, 420, 600, 900, 1200 s, right after `sys.boot_completed=1`, and every 10 s
  (first line only). Key points go to kmsg as `GBW5S <label> t=<uptime> ...`, and the sampler's files are copied out
  (`*-sampler-series.txt`, `*-sampler-full.txt` including the comm/RIP LRU).
* **No console client connects before init has started `gb_console`.** The W4 rehearsal found that a client
  connecting during UART init kills the pipe. Under the load of two concurrent boots, `gb_console` started at
  k = 55.5 / 56.4 s, so the first "t = 60" reading came at 62.1 / 62.3 s (run 1). In boot 2 it came at 60.5 / 61.2 s.
* **Boot time** is the kernel timestamp of init's `processing action (sys.boot_completed=1)` in serial0.log.
* **dex2oat wall time** comes from two sources:
  * (a) the image's own `gb_dexopt` service (90 s after boot_completed it runs
    `nice -n 10 cmd package compile -m speed -f` on systemui, nexuslauncher, settings and latin), timed by its
    `GB_DEXOPT start` lines in kmsg;
  * (b) an explicit action that starts in both VMs at the same moment (barrier) after both `gb_dexopt` runs ended:
    `cmd package compile -m speed -f` on settings, systemui, nexuslauncher and our own `gb.sb3.gpu`, timed with
    `/proc/uptime` around each call.
* **adler32 KAT.** `w5\tools\adler_kat\adler_kat.c` is our own code, built with NDK r27c clang
  `--target=x86_64-linux-android34 -O2`, sha256 `eca0139d...bca9`. `llvm-objdump` shows 0 `vpdpw*` / `vpdpb*`. The
  same source was checked on the builder against glibc's zlib 1.3 (PASS 1773 cases).
  * It loads `/system/lib64/libz.so` and `/vendor/lib64/libz.so` by path with `dlopen` and checks `dladdr` that
    `adler32` really came from that file.
  * `adler32` and `adler32_z` are compared with a scalar reference at 30 lengths x 6 misalignments, on random and
    all-0xff data, plus incremental updates.
  * zlib round trips: a flipped adler32 trailer must give `Z_DATA_ERROR`, and libraries must decode each other's
    streams.
  * The `classes.dex` header checksum of `GbGpu.apk`, and a 3 MB zlib stream made on the host by Python's zlib.
  * A 16 MiB throughput loop.

---

## 2. Results in detail

### 2.1 Emulation counts

See the tables in 0.1. Full time series (10 s): `evidence\measure\{run1,boot2}\{off,on}\*-sampler-series.txt`.

* **Before boot_completed the patched VM emulates only 752-756 instructions in both runs.** Of these, 20 are
  VEX.128: 16 come from init's own static zlib copy (the LRU at t = 60), which no bind can replace (INTEGRATION.md 4),
  and 4 more appear by boot_completed. The other 736 are VEX.256 from one ML-KEM run (`main`, the tethering APEX's
  `libcrypto_httpengine.so`).
* The off VM emulates 211 M (run 1) and 120 M (boot 2) by the same point.

### 2.2 Boot time

* Run 1: 587.7 s off against 313.6 s on.
* Boot 2: 226.0 s off against 118.0 s on.
* Both pairs show about -47 %. The absolute values are slower than a single VM on an idle host (the W4 rehearsal's
  single VM: 199.6 s with emulation), because two VMs booted at once. Only the within-pair comparison is meaningful.
* **INFERRED cross-check.** The KAT puts the cost of one emulated instruction at about 2.8 us: (9.41 - 0.63) s of KAT
  wall time over 3,143,919 emulations. 211.4 M emulations x 2.8 us is about 590 vCPU-s, or about 296 s on 2 vCPUs.
  That is close to the measured 274 s difference in run 1.
* In boot 2 most of the off VM's emulations come from `TextureLoadThre`, the boot animation's PNG decoding through
  libz (2.4).

### 2.3 dex2oat

The image's own `gb_dexopt` (run 1, seconds per package from kmsg):

| package | off | on |
|---|---:|---:|
| com.android.systemui | 205.9 | 164.2 |
| com.google.android.apps.nexuslauncher | 116.1 | 74.7 |
| com.android.settings | 197.6 | 153.6 |
| com.google.android.inputmethod.latin | 123.9 | 87.9 |
| total | **643.5** | **480.3** |

The on VM's `gb_dexopt` ran while the off VM was still booting under heavy emulation. The off VM's ran mostly while
the on VM sat idle at the barrier. So the -25 % favours "off", if anything.

The synchronised explicit action, `cmd package compile -m speed -f` (seconds; every call returned `Success`):

| package | off | on |
|---|---:|---:|
| com.android.settings | 107.63 | 129.87 |
| com.android.systemui | 99.53 | 72.64 |
| com.google.android.apps.nexuslauncher | 47.82 | 36.74 |
| gb.sb3.gpu | 0.88 | 0.63 |
| sum | **255.86** | **239.88** |
| emulations during the action | 19,558,757 | 66,668 |

Reading these results:

* dex2oat's wall time is dominated by compilation. Its adler32 share (OatWriter and dex checksums) cost the off VM
  19.6 M emulations, about 55 vCPU-s at 2.8 us (INFERRED).
* With one pair, the per-package results scatter: settings came out slower on the patched VM, while systemui and
  the launcher came out faster. The robust effect is the boot-time `gb_dexopt` total and the emulation count.

### 2.4 What is still emulated with the patch (LRU mapped with `vnni-perf\tools\match_rips.py` + the wave-3 scan)

`evidence\measure\{run1,boot2}\analysis.json`, `rip_matches.json`.

* **on: no RIP maps to `/system/lib64/libz.so` or `/vendor/lib64/libz.so` in any snapshot.** What remains:
  * AppSearch's static zlib copy `libicing_anywhere.so` `adler32_simd_` (`pool-*-thread`, the largest share after
    boot, e.g. 1,416 per RIP at the install snapshot). It is not deployed (INTEGRATION.md 4, private `/apex`
    namespace).
  * ML-KEM in the tethering APEX's `libcrypto_httpengine.so` (`main`, `CronetNet`). VEX.256 totals 13,856 by the end
    of run 1, and ML-KEM is the only VEX.256 source in the mapped LRU. It has no gate and is not zlib.
  * `init`'s static copy (16 at t = 60).
* **off:** every libz cluster is `adler32_simd_` of the system or vendor `libz.so`. The comms seen:
  * run 1: `surface:disk$0-3`, `dex2oat64`/`dex2oat`, `Profile Saver`, `com.goo:disk$0/1`, `top.setupwizard`,
    `peration loader`, `launcher-loader`, `adler_kat`;
  * boot 2: `TextureLoadThre` (LRU sum 186 M), `BootAnimation`, `ndroid.systemui`, `EATURES_CHANGED`;
  * plus the same AppSearch / ML-KEM / init entries as "on".
* The off VM's KAT LRU entries sit at libz page offsets `0x067` and `0x145`-`0x1ea`, the 9 `vpdpwssd` of
  `adler32_simd_`. **The on VM's KAT left no `adler_kat` entry and added 0 to the counter.**

### 2.5 adler32 KAT (MEASURED, `run1\{off,on}\11-adler-kat.txt`)

| | off | on |
|---|---|---|
| result | PASS 3547 cases, 0 fail | PASS 3547 cases, 0 fail |
| `adler32` resolved from | `/system/lib64/libz.so`, `/vendor/lib64/libz.so` (dladdr) | same |
| dex header checksum `ac8382a5` | MATCH (both libs) | MATCH (both libs) |
| host zlib stream (3,000,926 B -> 3,000,000 B) | rc 0 (both) | rc 0 (both) |
| 16 MiB bench result (same input) | `5d210803` | `5d210803` |
| KAT wall (uptime) | 9.41 s | 0.63 s |

---

## 3. On-device verification

| check | result | evidence |
|---|---|---|
| bind applied (on) | `/proc/1/mountinfo`, the console shell's and system_server's each show `/vnni/<id>/system/lib64/libz.so /system/lib64/libz.so ro,relatime` and `/vnni/<id>/vendor/lib64/libz.so /vendor/lib64/libz.so ro,relatime` (tmpfs 0:19). The vendor bind also propagates to `/odm/vlower/lib64/libz.so` (rw, see 5). | `run1\on\06-checks-device.txt`, `boot2\on\*-checks-device.txt` |
| processes use the patched files (on) | system_server, zygote64 and surfaceflinger map `/vendor/lib64/libz.so` from dev `00:13` inode 7; logd maps `/system/lib64/libz.so` from dev `00:13` inode 10. Those are the `/odm/vnni` copies (`stat`: dev 19, inodes 7 / 10). Off: `00:21` (vendor overlay) and `fe:0c` (system dm). | same |
| bytes (sha check) | on: `/system/lib64/libz.so` `aa60fb2b...1047`, `/vendor/lib64/libz.so` `fc4987b2...5350`, equal to the manifest's `patched_sha256` and to the `/odm/vnni` copies. off: `371f70d6...683b` / `e45aecb6...2590`, the originals; `/odm/vnni` is absent, `gb.vnni=0` is on the cmdline. | same |
| vendor_init / init accepted the fingerprint triggers | on: `init: processing action (ro.system.build.fingerprint=Android/desktop_system/... && early-init) from (/odm/etc/init/gb.rc:329)` at 16.235 s and `(ro.vendor.build.fingerprint=ASUS/lapis/... && early-init) from (/odm/etc/init/gb.rc:335)` at 16.253 s. No command failed. 0 `unexported property trigger`, 0 gb.rc parse errors. The only "Invalid" parse line is stock `/vendor/etc/ueventd.rc: 167`, the same in off. | `run1\*\08-kmsg-vnni-actions.txt`, serial0.log.gz |
| kill switch (off) | Both actions fired too (16.36 / 16.39 s). Their final `remount bind ro` failed harmlessly (`mount() failed: Invalid argument`) because gbinit had deleted `/odm/vnni`. No libz line in mountinfo. | `run1\off\06`, `08` |
| SELinux | 0 enforced (`permissive=0`) denials in any boot. No denial names `libz.so`. The only one naming `/odm/vnni` is the check's own `ls -lZ` (shell getattr). The `mounton`/`relabel*` denial set is identical in off and on (normalised diff empty), so the binds and restorecon add none. `getenforce` printed `Enforcing`; every logged denial is `permissive=1`. | `run1\*\09-kmsg-avc.txt` |
| apps install | `adb install -r GbGpu.apk` returned `Success` in both, and `pm path gb.sb3.gpu` works. | `run1\*\15-adb-install.txt`, `16` |
| dex / zip checksums | KAT (2.5). 4 + 4 `compile -m speed -f` calls returned `Success`. Boot's own package scan and `gb_dexopt` ran in both. | `run1\*\19-22` |
| logcat / crashes | No tombstones. The crash buffer has the same GMS crashes in both VMs: `BackgroundServiceStartNotAllowedException` and a chimera "Expected existing service to be null". First-boot ANRs: off 1 (Play Store DseService), on 4 (GMS broadcasts and an `com.google.android.as` job, 20:19-20:22, while its dexopt overlapped the off VM's emulation-heavy boot). Boot 2 adds no ANR. Dropbox (boot 2, every crash and ANR record from both boots): **0 mentions** of zip/Adler32/Inflater/Deflater/DataFormat/checksum/libz. The logcat grep for `checksum|adler|zlib|inflate|deflate|dex2oat error|Failed to open/verify dex|INSTALL_FAILED` has no zlib or checksum error. Its only hit containing "adler" is the permissive `dac_read_search` audit line of our own `adler_kat` (on). `config_install_failed: FileNotFoundException` appears in both VMs and is unrelated. | `run1\*\25-logcat-checks.txt`, `boot2\*\*-boot2-dropbox.txt`, `*-boot2-anr.txt` |
| kernel | 0 `BUG:` / `WARNING: CPU` / `Oops` / `Bad FPU state` / `sleeping function` in all 4 boots. `efault` 0, `byte` 0, `irqon == emulated` in all 50 snapshot reads (17 + 17 + 8 + 8). | serial0.log.gz |

---

## 4. Fallback (INTEGRATION.md 3, `gb_vnni.sh`)

Not needed and not built. The property-trigger actions in `/odm/etc/init/gb.rc` were parsed and executed on this
init, and both binds were made at 16.2 s. Nothing in the pipeline was changed by this step.

## 5. Observations and caveats

* **n = 1 per regime** (one fresh-data pair and one warm second-boot pair), with the VMs running concurrently.
  * The emulation counts are deterministic in kind: zero libz emulation with the patch.
  * The two boot-time pairs agree (-46.6 % / -47.8 %).
  * The explicit compile results scatter per package (2.3).
* Under concurrency the patched VM leaves the race early and frees CPU for the baseline. That makes the measured
  gains conservative (INFERRED).
* The vendor bind propagates into `/odm/vlower/lib64/libz.so`, the vendor overlay's lower dir, which is a shared bind
  of `/vendor`. That copy stays **rw**, because the `remount bind ro` applies only to `/vendor/lib64/libz.so`. This
  is no new exposure: the source `/odm/vnni/...` on the `/odm` tmpfs is root-writable anyway (INTEGRATION.md 5).
  Noted for the review (LOW).
* Not covered here: native hardware (the gblog `-- gb_vnni` line on the Surface Book 3 gives the real figure), an
  OTA/other-fingerprint boot, and enforcing-mode policy work.
* Residual emulation with the patch: AppSearch's static zlib (`libicing_anywhere.so`, private `/apex` namespace)
  and ML-KEM in the tethering APEX. These are the next candidates if they matter (INTEGRATION.md 4).

## 6. Evidence and reproduction

* `w5\evidence\measure\`:
  * `00-clone-on.txt`, `01-prepare-vms.txt`, `02-esp-diff-after-boot1.txt`, `03-restore-{off,on}.txt`;
  * `run1\{off,on}\` and `boot2\{off,on}\`: numbered command outputs, `RESULT.json`, `run.log`, `serial0.log.gz`;
  * `run1\analysis.json` / `ANALYSIS.md`, `boot2\...`, `records.json`, `rip_matches.json`;
  * `run*-{off,on}.console.txt`.
* `w5\tools\`: `w5vm.py` (VM control; `prev\` holds the killed attempt's version), `run_w5.py` (orchestrator;
  `prev\run_w5.run1.py` is the exact run-1 version), `gbw5_sampler.sh`, `adler_kat\` (source, binary, dex/zlib
  inputs), `analyze_w5.py`, `sparsify.py`.
* To reproduce, with both VMs off:
  1. `w5vm.py --inst on clone`, unless `vmon\disk.img` exists.
  2. `w5vm.py --inst off esp D:\gb-w5test\esp\vnni-off.img`.
  3. `w5vm.py --inst {off,on} mkvm`.
  4. Start `run_w5.py --inst off OUT` and `run_w5.py --inst on OUT` at the same time.
  5. `analyze_w5.py OUT`.
  6. For a warm boot, run step 4 again with `--boot2` after rotating serial0.log.
  7. `w5vm.py --inst X reset`, then `esp vnni-on.img`, to get back to the pristine image.

## 7. Rules kept

* Throwaway VMs only: 4096 MiB / 2 vCPUs each, under `D:\gb-w5test`. The user's VM was off throughout, and its vmx,
  pipe and adb address (`.133`) were never used. The throwaway VMs got `.138` / `.139`.
* No lock-screen PIN anywhere.
* `lapis.bin` was not opened. No real disk, no PhysicalDrive, diskpart, bcdedit, mountvol or efibootmgr. No
  `GB_WRITE_LAPIS`. No pipeline file under `C:\GB-x86` was changed. HANDOFF.md was not edited.
* Builder: only `/var/tmp/w5measure`, under `nice -n 19 ionice -c3`. `/build/dist*` was untouched.
* Disk floors were held:
  * C: stayed at 53 GB or more.
  * D: fell to 4.1 GB during boot 2: VMware's two 4 GiB `.vmem` files plus other agents' writes. A watchdog was armed
    to hard-stop only the throwaway VMs below 3.3 GB; it did not need to act.
  * D: was at 12 GB at the end. Other agents keep writing there.
* **Both test disks are back to the integrate step's image.** `w5vm.py reset` zeroed the blank partitions and
  restored the GPT; `vnni-on.img` was written back to EFI-SYSTEM; then `state` gave whole-file sha256 `dd88458a...c516`
  with every region equal to pristine, for both `vm\disk.img` and `vmon\disk.img` (`03-restore-{off,on}.txt`).
  The clone was re-sparsified from 12.72 to 7.72 GiB allocated, sha256 unchanged.
* Nothing was deleted. Superseded files were kept: `w5\tools\prev\`, `D:\gb-w5test\{vm,vmon}\serial0.run1-boot1.log`
  (boot 1 serial logs; gzip copies are in the evidence).
* Deletable test leftovers, for the user to decide on:
  * `D:\gb-w5test\vmon\` (the clone, 7.7 GiB allocated);
  * `D:\gb-w5test\{vm,vmon}\serial0.run1-boot1.log`;
  * `D:\gb-w5test\prev\first-attempt-20261026-1829\` (integrate's stale disk).
