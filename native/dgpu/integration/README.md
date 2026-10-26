# dGPU Wave 2 integration checklist (v1.1)

**(v1.2 note, 2026-10-25)** INTERFACES.md is now **version 1.2** (graphics switching, sections 22-28; v1.1 text kept in
`INTERFACES-v1.1.md`). The extra shared-file edits v1.2 needs are in `gfx-switching.md` (W2G-01 .. W2G-11); they apply
only after the wave 1b components are implemented and their integration files carry the v1.2 artifact sha256s. Where
`gfx-switching.md` and the items below disagree (W2-01 `gfxdriver.json` gate, W2-03 gfxtest entry, W2-04 `-gfxtest`
copy, W2-07 item 6), `gfx-switching.md` wins.

**(v1.2 r2 note, 2026-10-25)** The review fix pass (INTERFACES section 29, R-1..R-16) changed four components; their
r2 sections supersede the v1.2 artifact shas: `nouveau-policy.md` 8, `gb-power.md` 8, `nvk-gfxdriver.md` 8,
`ux-app.md` 9; the shared-file delta is the "r2" section at the end of `gfx-switching.md` (W2G-02 `apk=`, W2G-07
additions, W2G-09 dropped, optional W2G-12, W2G-11 shas). The rows below are the v1.1 state, kept for reference. r2
state (sha256, first 8): `gb_power` `0d6a0b28` (source `a8bf3f05`), `gb_nouveau.sh` `6a754d42`, `gb_dgpu_report.sh`
`ccf3ddfb`, `gb_notify.sh` `a4bc8d6e`, `gb_nouveau.rc` `4a8c438a`, `gb_dgpu_check.sh` `045fe80d`, `nvk_probe`
`3d53a7e9`, ICD `625314e0`, GbGfxDriver.apk `15a22bfd`, GbGpu.apk `d653940a`, `gfxdriver.json` `840d0666`,
`gpu_ui.json` `ef867551`; gpu-compute and k14 unchanged.

Normative interface: `C:\GB-build\native\dgpu\INTERFACES.md` **version 1.1** (post-review; section 21 lists every
change since v1). This file lists every change to **shared** files that Wave 1 was not allowed to make, in the order
Wave 2 should apply them. Each component's own integration file adds artifact sha256s, test results, exact diffs and
its open "Spec questions":

| Component | File |
|---|---|
| 1 nouveau-policy | `nouveau-policy.md` |
| 2 gb-power | `gb-power.md` |
| 3 k14 | `k14-kernel.md` (pointer `k14.md`) |
| 4 nvk-gfxdriver | `nvk-gfxdriver.md` |
| 5 gpu-compute | `gpu-compute.md` |
| 6 gpu-ui | `ux-app.md` (pointer `gpu-ui.md`) |

Read all of them first and resolve their spec questions before editing anything.

Component state at the end of the v1.1 fix pass (2026-10-25 ~10:25 UTC; every test below re-run after the last
source change, on the builder; nothing ran on the GTX 1650):

| Component | Key artifacts (sha256, first 8) | Last test run |
|---|---|---|
| 1 nouveau-policy | `gb_nouveau.sh` aebf9189, `gb_dgpu_report.sh` 8c5f0b2a, `gb_notify.sh` a1f465a9, `gb_nouveau.rc` 633e8e91, `payload/make_dgpu_payload.sh` 45de8e5d, firmware fetch 9c1e83e3 (tag 20260916) | mksh suite 629/629 (GNU) and 629/629 (toybox + BWK awk), shellcheck 0, NP-24 rc == 15.1; NP-21 31/31 |
| 2 gb-power | `bin\gb_power` 05d9b2fc (source 25bddd1f), `gb_power.rc` 395aad4d | unit 474/474 plain and ASan+UBSan (no reports), socket 94/94, rc lint 0, reproducible |
| 3 k14 | unchanged (`/build/dist14/bzImage` 0f7db715) | no input changed; read-only re-check only |
| 4 nvk-gfxdriver | ICD 625314e0 (unchanged), `gb_nvk.rc` 748963f3 | GD-01 39/39 (+ASan), 44/44 + 16/16 bionic, GD-03, GD-04 14/14, NV-02/03/06, rc lint 0 |
| 5 gpu-compute | `libgb_gpu_compute.so` 8042d365, launcher f275dae6 and stub 576193fa (unchanged), rc e1eaf3b9 | GC-01..GC-09, GC-11: `failures: 0` (60 end-to-end checks); GC-10 REPRO PASS |
| 6 gpu-ui | `GbGpu.apk` c53422ba (versionCode 29882061), rc 1c8ca925, setup 312cf0c5 | UI-01..06, MOCK (34 replies identical to the real gb_power 25bddd1f), REAL, RC 16/16: all PASS |

Ground rules for Wave 2:

* Start only after the installer-ISO workflow that owns these files has finished (`pgrep -af gbiso` on the builder
  shows nothing, and the ISO owner has released `C:\GB-x86\iso\**`). Back up each shared file as
  `<name>.pre-dgpu` before the first edit.
* Every item ends with its verification. Nothing here touches the user's VM; items marked **VM** need the user's
  approval because they write `lapis.bin` or install onto the VM.
* Keep the VM image free of the dGPU stack (invariant 19): every new cpio entry goes under `gbnative/` only.

Order: **W2-00 now**, then W2-01 (atomically, before any native rebuild), W2-02 to W2-07 (Phase 1 rails, legacy
payload layout), W2-09 (k14), W2-08 (P5 payload; unblocked in v1.1), W2-10/W2-11 (P6/P7 components), W2-12 to
W2-16. Each step leaves a buildable, bootable image.

---

## W2-00 Pre-flight: the live manifests already consume rewritten Wave 1 files (review R1-H3)

The shared manifests that `build_esp.py` reads today (`C:\GB-x86\inject\native\components\{nouveau,power,nvk}.json`)
pack component files **by path**, and Wave 1 rewrote those files in place. Until W2-01 lands, the next native ESP
build (build_esp, build_usb, build_internal, or the ISO workflow re-exporting an ESP) ships a half-integrated set.
The ISO export that existed during Wave 1 (`C:\GB-x86\iso\work\export\EFI-SYSTEM.img`, 03:20 +0200) predates every
Wave 1 edit and is not affected.

What a native build before W2-01 would contain, and what it would miss:

| Live manifest input (path packed today) | Content now (v1.1) | sha256 now | Fallback kept next to it | sha256 fallback |
|---|---|---|---|---|
| `nouveau-kernel\native\gb_nouveau.sh` | v1.1 policy | `aebf9189...049b` | `gb_nouveau.sh.pre-dgpu` | `ed6ea50f...2b86e` |
| `nouveau-kernel\native\gb_dgpu_check.sh` | v1.1 | `91780d74...f70f` | `gb_dgpu_check.sh.pre-dgpu` | `ec3abc3b...7570` |
| `nouveau-kernel\native\gb_nouveau.rc` | v1.1 15.1 (declares `gb_dgpu_report`, `gb_dgpu_test`, `gb_notify`) | `633e8e91...f61e` | `gb_nouveau.rc.pre-dgpu` | `c167e952...4b32` |
| `power\bin\gb_power` | v1.1 daemon | `05d9b2fc...63ef` | `bin\gb_power.pre-dgpu` | `9c126bbc...8df7` |
| `power\gb_power.rc` | v1.1 15.2 (socket line) | `395aad4d...29a6` | `gb_power.rc.pre-dgpu` | `43c2c915...4b16` |
| `nvk-android\bin\vulkan.nouveau.so` | 8a ICD | `625314e0...409b` | `superseded\vulkan.nouveau.so.f7e9154f` | `f7e9154f...0723` |
| `nvk-android\bin\nvk_probe` | AHB modes, default `-t all` | `412e080a...3054` | `superseded\nvk_probe.16383c88` | `16383c88...01b7` |

Missing in such a build: `gb_dgpu_report.sh` and `gb_notify.sh` (no notification is ever delivered, incl. N01 and
gb_power's detach refusals; the queue just grows), the shell-user `gb_nvk.rc` (the old `rc_text` runs the probe as
root without setting `foreign`). The default state stays safe (nouveau never loads unless enabled), but such an
image is not a valid test of anything.

Actions:
1. **Tell the ISO workflow owner now** (the fix pass could not: it has no channel to that workflow and must not edit
   its files). The owner decides whether a rebuild before W2-01 is needed; if so, that owner points the three live
   manifests at the fallback copies above (a shared-file edit, owner's call), or simply rebuilds after W2-01.
2. Apply W2-01 as **one atomic change** (all manifests in one go) before the next native build.
3. W2-07 check 11 (below) makes a half-integrated set fail the build from then on.

## W2-01 Component manifests and the manifest loader

**`C:\GB-x86\build_esp.py`, native component loader (currently ~lines 1254-1272).** Add two optional manifest keys:

* `symlinks`: list of `{"dest": "<path relative to /odm>", "target": "<absolute target>"}` -> `_add_native(dest,
  target.encode(), 0o120777)` (the cpio writer stores the target as the file data; busybox `cp -a` in gbinit keeps the
  symlink).
* `payload`: list of `{"src", "dest" (relative to the erofs root), "mode"}` -> collected into `PAYLOAD_FILES` for W2-08.
  Before W2-08 lands, a manifest MUST NOT use `payload` (fail the build if it does).

**`C:\GB-x86\inject\native\components\`** (exact content; `source` keys informational):

`nouveau.json`
```json
{
 "name": "nouveau",
 "source": "C:\\GB-build\\native\\nouveau-kernel",
 "files": [
  {"src": "C:\\GB-build\\native\\nouveau-kernel\\native\\gb_nouveau.sh", "dest": "vup/bin/gb_nouveau.sh", "mode": "0755"},
  {"src": "C:\\GB-build\\native\\nouveau-kernel\\native\\gb_dgpu_check.sh", "dest": "vup/bin/gb_dgpu_check.sh", "mode": "0755"},
  {"src": "C:\\GB-build\\native\\nouveau-kernel\\native\\gb_dgpu_report.sh", "dest": "vup/bin/gb_dgpu_report.sh", "mode": "0755"},
  {"src": "C:\\GB-build\\native\\nouveau-kernel\\native\\gb_notify.sh", "dest": "vup/bin/gb_notify.sh", "mode": "0755"},
  {"src": "C:\\GB-build\\native\\nouveau-kernel\\firmware_staged_sha256.txt", "dest": "etc/gb/tu117.sha256", "mode": "0644"}
 ],
 "rc_files": ["C:\\GB-build\\native\\nouveau-kernel\\native\\gb_nouveau.rc"],
 "rc_text": "",
 "build_prop": []
}
```

`power.json`: unchanged (`bin\gb_power` -> `vup/bin/gb_power`, `gb_power.rc`); the files themselves changed.

`nvk.json` (legacy layout, before W2-08)
```json
{
 "name": "nvk",
 "source": "C:\\GB-build\\native\\nvk-android",
 "files": [
  {"src": "C:\\GB-build\\native\\nvk-android\\bin\\vulkan.nouveau.so", "dest": "vup/lib64/hw/vulkan.nouveau.so", "mode": "0644"},
  {"src": "C:\\GB-build\\native\\nvk-android\\bin\\nvk_probe", "dest": "vup/bin/nvk_probe", "mode": "0755"}
 ],
 "rc_files": ["C:\\GB-build\\native\\nvk-android\\gb_nvk.rc"],
 "rc_text": "",
 "build_prop": []
}
```
`nvk.json` (payload layout, with W2-08): move `vulkan.nouveau.so` to
`"payload": [{"src": "...\\bin\\vulkan.nouveau.so", "dest": "lib64/hw/vulkan.nouveau.so", "mode": "0644"}]` and add
`"symlinks": [{"dest": "vup/lib64/hw/vulkan.nouveau.so", "target": "/odm/dgpu/lib64/hw/vulkan.nouveau.so"}]`.
`nvk_probe` stays in `files` (INTERFACES D7).

`gpu_compute.json` (new, **only together with W2-08**): component 5's `C:\GB-build\native\gpu-compute\gpu_compute.json`
(launcher in `files`, the two libraries in `payload`, `gb_gpu_compute.rc` in `rc_files`).

`gpu_ui.json` (new): component 6's `C:\GB-build\native\gpu-ui\gpu_ui.json` (`out\GbGpu.apk` -> `app/GbGpu/GbGpu.apk`,
`gb_gpu_ui_setup.sh` -> `vup/bin/gb_gpu_ui_setup.sh` 0755, `gb_gpu_ui.rc`).

`gfxdriver.json`: **not** copied into `components\` until the Phase 8a gates pass. It stays at
`C:\GB-build\native\gfx-driver-pkg\gfxdriver.json`; build_esp loads it only when `GB_GFXDRIVER=1` is set (and then
also emits the gfxtest entry, W2-03).

Verify: build log `native components: ...` lists `gpu_ui, iptsd, nouveau, nvk, power, ...` (`gpu_compute` with W2-08);
the native cpio has the files at the listed paths with the listed modes; `etc/init/gb.rc` contains
`service gb_nouveau `, `service gb_notify `, `socket gb_power stream 0666 root root`,
`mkdir /data/vendor/gb 0751 root shell` and **no** `/data/vendor/gb 0771`; `service gb_nvk_probe` is followed by
`user shell`; the VM cpio contains none of them.

## W2-02 gb_bootlog (`build_esp.py` `GB_BOOTLOG_SH`, ~lines 937-967; plan P1 item 5)

Replace the script body with the following (as a bytes literal in the same style). v1.1: the ESP lock uses the atomic
steal of INTERFACES 5.4, releases only its own lock, proceeds **without** the lock after 600 s (never breaking a live
holder's lock), and the dgpu section reads gb_notify's logcat lines.

> **Superseded (Wave 2b, 2026-10-26):** the shipped script (`build_esp.py` `GB_BOOTLOG_SH`) follows INTERFACES 5.4 r3
> (back-off while a steal is in progress, exclusive owner file, put-back without replacing, release waits for a
> put-back), goes on without the lock after 600 s only when the holder is another log writer, and fails closed when
> `df` gives no number (INTERFACES section 30, E-1..E-3). The snippet below is the Wave 2 integration text, kept as it
> was.

```sh
#!/system/bin/sh
P=EFI-SYSTEM
for a in $(cat /proc/cmdline); do case $a in gb.logpart=*) P=${a#gb.logpart=};; esac; done
D=/dev/block/by-name/$P; M=/mnt/gb_esp; N=; L=/dev/gb_esp.lock; OWN=
mkdir -p $M
lock() {   # INTERFACES 5.4 v1.1: wait up to 600 s; a stale lock is stolen atomically (rename, then check)
  w=0; miss=0
  while ! mkdir $L 2>/dev/null; do
    ino=$(stat -c %i $L 2>/dev/null); ol=$(cat $L/owner 2>/dev/null); o=${ol%% *}; stale=0
    if [ -n "$ol" ]; then miss=0; case $o in ''|*[!0-9]*) stale=1;; *) [ -d /proc/$o ] || stale=1;; esac
    else miss=$((miss+1)); [ $miss -ge 5 ] && stale=1; fi
    if [ $stale = 1 ] && [ -n "$ino" ] && mv -T $L $L.stale.$$ 2>/dev/null; then
      if [ "$(stat -c %i $L.stale.$$ 2>/dev/null)" = "$ino" ] && [ "$(cat $L.stale.$$/owner 2>/dev/null)" = "$ol" ]; then
        rm -f $L.stale.$$/owner; rmdir $L.stale.$$ 2>/dev/null; miss=0; continue
      fi
      mv -T $L.stale.$$ $L 2>/dev/null   # a live holder re-created it meanwhile: put it back
    fi
    w=$((w+1))
    if [ $w -ge 600 ]; then
      echo "gb_bootlog: ESP lock held over 600 s ($ol), proceeding without it" > /dev/kmsg
      OWN=; return 0
    fi
    sleep 1
  done
  OWN="$$ gb_bootlog $(cut -d. -f1 /proc/uptime)"; echo "$OWN" > $L/owner
}
unlock() { if [ -n "$OWN" ] && [ "$(cat $L/owner 2>/dev/null)" = "$OWN" ]; then rm -f $L/owner; rmdir $L 2>/dev/null; fi; OWN=; }
for t in 40 90 180 420 900; do
  while [ $(cut -d. -f1 /proc/uptime) -lt $t ]; do sleep 5; done
  [ $t = 40 ] && setprop vendor.gb.dgpu_report 1
  lock
  mount -t vfat -o rw,flush,nosuid,nodev,noexec $D $M || { unlock; echo "gb_bootlog: cannot mount $D" > /dev/kmsg; exit 1; }
  ... (existing body unchanged up to the getprop section) ...
    echo '== dgpu'
    for d in /sys/bus/pci/devices/*; do
      v=$(cat $d/vendor)
      [ "$v" = 0x10de ] || [ "${d##*/}" = 0000:00:1d.4 ] || continue
      echo "${d##*/} $v:$(cat $d/device) class=$(cat $d/class) driver=$(basename "$(readlink $d/driver)" 2>/dev/null)" \
           "control=$(cat $d/power/control) runtime=$(cat $d/power/runtime_status) state=$(cat $d/power_state)" \
           "active_ms=$(cat $d/power/runtime_active_time) suspended_ms=$(cat $d/power/runtime_suspended_time)" \
           "iommu_group=$(ls $d/iommu_group/devices 2>/dev/null | tr '\n' ' ')"
    done
    echo "nouveau refcnt=$(cat /sys/module/nouveau/refcnt 2>/dev/null || echo -)"
    for r in /sys/class/drm/card[0-9]* /sys/class/drm/renderD*; do
      [ -e $r/device/vendor ] && echo "${r##*/} -> $(cat $r/device/vendor) $(basename "$(readlink $r/device/driver)")"
    done
    ls /dev/gb_dgpu 2>/dev/null | tr '\n' ' '; echo
    getprop | grep 'vendor\.gb\.'
    dmesg | grep -iE 'nouveau|gsp|pciehp|_OSC|sdtx|surface_hotplug|acpiphp|firmware|gb_nouveau|gb_power|gb_notify|runtime PM through ACPI _PR3|no longer present on the bus|runtime suspend failed|failed to evict VRAM'
    logcat -d -s gb_notify gb_gpu_ui_setup gb_gpu_compute 2>/dev/null | tail -n 40
    cat /proc/asound/cards; getenforce
    for k in dgpu load test; do f=$(ls -t /data/vendor/gb/report/$k-*.txt 2>/dev/null | head -n 1); [ -n "$f" ] && { echo "== $f"; cat "$f"; }; done
    echo '== dmesg-live (tail)'; tail -c 262144 /data/vendor/gb/report/dmesg-live.txt 2>/dev/null
    echo '== dmesg-live.prev (tail)'; tail -c 262144 /data/vendor/gb/report/dmesg-live.prev.txt 2>/dev/null
    echo '== gb_nouveau.log (tail)'; tail -c 65536 /data/vendor/gb/report/gb_nouveau.log 2>/dev/null
  ... (existing dmesg/logcat/dumpsys sections) ...
  } > $F 2>&1
  sync; umount $M; unlock
done
```

Notes: the `== dgpu` section reads only cached sysfs attributes (no config space, no DRM open; invariant 12). The
`_OSC` and the three quoted strings are k14's N1-N3 log lines (k14-kernel.md 1.5). Verify on a VMware native-smoke boot
(**VM**): the t=40..900 files exist and contain `== dgpu`; `ls /data/vendor/gb/report/dgpu-*.txt` is non-empty **at the
t=420 snapshot** (the report samples power for 5 min, so it appears at about t=340; N is the boot sequence number,
1 only on a fresh `/data`).

## W2-03 NVIDIA test boot entries (`build_esp.py` `slot_files`, ~lines 1373-1391; plan P1 item 6)

* New keyword arguments `nvtest=False, gfxtest=False`. For every slot `x` written, when `nvtest`:
  `loader/entries/gb-{x}-nvtest.conf` =
  `title   {title} (slot {X}) [NVIDIA test]\nlinux   /vmlinuz\ninitrd  /initrd-{x}.img\noptions {cmdline} gb.nouveau=test\n`.
  When `gfxtest` (only if `GB_GFXDRIVER=1`): `gb-{x}-gfxtest.conf`, title suffix `[NVIDIA app graphics test]`,
  options suffix ` gb.nouveau=once gb.gfxdriver=1`.
* `loader.conf` `default gb-{default}` is unchanged (the test entries are never the default).
* Callers: `C:\GB-x86\build_usb.py` line ~111, native branch only (not `--vm-test`): `nvtest=True`;
  `C:\GB-build\native\dualboot\build_internal.py` line ~113: `nvtest=True`. The VM path in `build_esp.py __main__`
  (incl. `GB_NATIVE_TEST`) stays without test entries; NP-25 therefore appends `gb.nouveau=test` in the boot editor
  (INTERFACES 18.1). The ISO objects `esp-usb`/`esp-internal` inherit the entries.
* `C:\GB-x86\iso\test\check_esp_tree.sh`: expect `loader/entries/gb-<x>-nvtest.conf` in the installed ESP and in the
  export, with `gb.nouveau=test` and the same `androidboot.boot_part_uuid`.

Verify: unpack the ESP (or grep the raw image) for both entries; `gb-<x>.conf` contains no `gb.nouveau=`.

## W2-04 gb_slotsync (`C:\GB-build\mouse\gb_slotsync.c`, `select_entry` ~lines 234-282, `sync_once` ~lines 287-310)

1. In `select_entry(s, cur)`: after writing `gb-<s>.conf`, repeat the same template copy for each suffix in
   `{"-nvtest", "-gfxtest"}`: if `MNT/loader/entries/gb-<cur><sfx>.conf` exists and (`s != cur` or the target is
   missing), write `gb-<s><sfx>.conf` with the same `(slot ` title rewrite and `initrd  /initrd-<s>.img` line. A
   missing template is not an error.
2. In `sync_once()` around the ESP `mount(...)` .. `umount2(...)`: acquire `/dev/gb_esp.lock` exactly as INTERFACES
   5.4 v1.1: `mkdir`; owner line `"<pid> gb_slotsync <uptime>"` (remember it); stale = dead owner pid, non-numeric
   owner, or no owner file for 5 polls; steal = `stat()` the lock dir (inode) and read the owner line, `rename(LOCK,
   LOCK ".stale.<pid>")`, then `stat()` + read again: same inode and same line -> `unlink(owner)`, `rmdir`, retry
   `mkdir`; otherwise `rename()` it back. Wait at most 600 s, then skip this cycle with a log line. Release only when
   the owner file still holds our line.
3. Rebuild with the existing command (`clang --target=x86_64-linux-android34 -O2 -Wall`), place the binary at
   `C:\GB-x86\inject\slotsync\gb_slotsync`.

Verify: `gb_slotsync --dry-run` on a test ESP image with a fake second slot logs `would write` for both `gb-b.conf`
and `gb-b-nvtest.conf`; holding `/dev/gb_esp.lock` from a shell makes it wait, then skip; a lock whose owner pid is
dead is stolen once (two concurrent instances: exactly one wins).

## W2-05 HDA function (`C:\GB-build\native\audio\tree\gb_audio.sh`, legacy `dsp_driver=1` path; plan P1 item 7)

Bind `snd_hda_intel` only to Intel functions: before loading/binding, for every PCI function with vendor `0x8086` and
class `0x0403*`, write `snd_hda_intel` to its `driver_override`; never to `0x10de` functions. gb_power additionally
keeps every non-`0x03` 10de function driverless (component 2). Verify: shellcheck; on the SOF default path
(`dsp_driver=3`) nothing changes.

## W2-06 Installer module deny (`C:\GB-x86\iso\build\builder\stage_initramfs.py:26`; plan P1 item 8)

`MODULE_DENY = ["spi_intel*", "mtd*", "efi_test", "efi_pstore", "tpm*", "nouveau", "nvidia*"]`. Add to
`C:\GB-x86\iso\test\t0_static.py` a check that the staged installer module list contains no `nouveau*`, `nvidia*`,
`drm_gpuvm`, `drm_exec`, `gpu-sched` (unless another installer module needs the last three; then only nouveau/nvidia).
Verify: t0_static passes; the installer initramfs listing has no nouveau.

## W2-07 verify_image.py checks (`C:\GB-x86\verify_image.py`; plan P1 item 9)

verify_image today only greps the raw ESP. Add a small read-only FAT32 reader (the ESP is written by our own writer:
FAT32, LFN entries) to extract files, then:

1. `gb/inject.gz` gunzips; it is byte-identical to the first segment of `initrd-<slot>.img`; parse the newc cpio.
2. Firmware: every `gbnative/firmware/nvidia/tu117/*` (legacy) matches `gbnative/etc/gb/tu117.sha256`; with the
   payload (W2-08) instead: `gb/dgpu-<krel>.sha256` matches the sha256 of `gb/dgpu-<krel>.erofs` and equals
   `gbnative/etc/gb/dgpu_payload.sha256`.
3. krel and signer (v1.1 wording, D11): the token after `Linux version ` in `vmlinuz`; legacy:
   `gbnative/lib/modules/nouveau.ko` vermagic first token equals it, the file ends with `~Module signature
   appended~\n`, and its signer matches the X.509 certificate embedded in **this image's** `vmlinuz` (the k14check.py
   `sig` method); never compare with a remembered key id (each Kleaf build may have its own key; k14 reuses k13's).
4. `nouveau` appears in no `gbnative/lib/modules/modules.load*` and no other load list.
5. `gbnative/etc/init/gb.rc` contains `service gb_nouveau ` and `on property:sys.boot_completed=1`.
6. `gbnative/etc/build.prop` (and the gbstage one) contain no `ro.hardware.vulkan`, `ro.hardware.egl`,
   `ro.gfx.driver.` lines.
7. `loader/entries/gb-<slot>-nvtest.conf` exists with `gb.nouveau=test`; the default entry has no `gb.nouveau=`.
8. No cpio path starts with `gbnative/priv-app/` or `gbstage/priv-app/`.
9. The gbstage (VM) tree contains no `nouveau.ko`, no `firmware/nvidia`, no `vulkan.nouveau.so`, no `gb_nouveau`.
10. ESP free space >= size of one `initrd-<x>.img` + 16 MiB (gb_slotsync must fit the other slot's initrd).
11. (v1.1, R1-H3) Every `/vendor/bin/<x>` named in a `service` line of the native `gb.rc` exists in the native cpio
    as `gbnative/vup/bin/<x>` (catches a half-integrated set: e.g. `gb_notify.sh`, `gb_dgpu_report.sh`).
12. (v1.1) Every `mkdir /data/vendor/gb ` line in the native `gb.rc` reads `0751 root shell` (the rc files must
    agree, INTERFACES 5.1); `mkdir /data/vendor/gb/report ` reads `0750 root shell`; `service gb_nvk_probe` has
    `user shell`.

Run it at the end of `build_usb.py` (fail the build on problems) and before `build_iso.py` accepts an image (it already
asks for `--usb-sha256`; call verify_image on that image). Verify: run on the current image (expected to pass checks
1-6, 8-10 and fail 7, 11, 12 until W2-01/W2-03 land).

## W2-08 Lazy payload (Phase 5; `build_esp.py`, plan P5) -- unblocked in v1.1

1. New `dgpu_payload()` in build_esp (native builds only):
   * Stage a tree per INTERFACES 6.2 in a temp dir: firmware from `NATIVE_FW` entries under `nvidia/tu117/` (path
     `firmware/nvidia/tu117/...`) + `C:\GB-build\native\nouveau-kernel\firmware\WHENCE.tu117` and `LICENCE.nvidia`
     (path `firmware/nvidia/`); the 5 modules of `stage_kernel.NATIVE_OPTIN` from `NATIVE_MODS` (path
     `lib/modules/<name>.ko`; `modules.dgpu` may be omitted, the tool writes it); every manifest `payload` entry
     (`lib64/hw/vulkan.nouveau.so`, `lib64/gpu-compute/*.so`). No `VERSION`/`MANIFEST.sha256` (the tool writes them).
   * Copy the tree, `C:\GB-build\native\nouveau-kernel\payload\make_dgpu_payload.sh` and
     `C:\GB-build\native\nouveau-kernel\firmware_staged_sha256.txt` to `builder:/build/native/dgpu/payload-stage/<krel>/`
     (scp), run `nice -n 19 ionice -c3 sh make_dgpu_payload.sh <tree> <out>/dgpu-<krel>.erofs <krel>
     firmware_staged_sha256.txt`, require the line `PAYLOAD ok ...`, fetch `dgpu-<krel>.erofs` + `.sha256`, delete the
     builder copies.
   * ESP files: `gb/dgpu-<krel>.erofs`, `gb/dgpu-<krel>.sha256`; cpio: `gbnative/etc/gb/dgpu_payload.sha256` (same
     bytes), empty dirs `gbnative/dgpu` and `gbnative/firmware/nvidia`.
2. Remove from the native cpio: `firmware/nvidia/**`, the 5 opt-in modules (and any `*.pre-*` backups, see W2-13),
   and `vup/lib64/hw/vulkan.nouveau.so` (replaced by the W2-01 symlink).
3. Asserts: krel of `vmlinuz` == krel of `nouveau.ko`; every payload firmware sha matches `tu117.sha256`; no payload
   path and no `dgpu` placeholder entry outside `gbnative/`; ESP free space per W2-07 item 10 for the USB ESP
   (`--esp-mib`, default 512) and the internal `EFI-SYSTEM.img` (536,870,912 bytes). Size reference (NP-21, k14
   modules + 8a ICD + compute libs): 60,260,352 bytes.
4. `C:\GB-x86\iso\test\check_esp_tree.sh`, `validate_dualboot.py`, `validate_wholedisk.py`: expect the two payload
   files in the ESP and compare their sha with the export.
5. Update path: `install_dualboot.ps1 -UpdateEspOnly` and USB rebuilds carry the new ESP; the device refreshes
   `/data/vendor/gb/payload/dgpu.erofs` on sha mismatch (INTERFACES 6.5), nothing else to migrate. Add
   `gpu_compute.json` (W2-01) in the same change.

Verify: `fsck.erofs`; `python3 -I C:\GB-build\native\nouveau-kernel\payload\erofs_ls.py <image>` lists exactly 6.2
(+ the compute libraries); NP-21 (`test_make_dgpu_payload.sh`) on the builder; native cpio size drops by the
firmware+module size; **VM** native-smoke runs the stage path (mounts and unmounts, guard refuses insmod).

## W2-09 k14 staging (Phase 4; `C:\GB-x86\stage_kernel.py`)

1. Add `--dist <dir>` (default `/build/dist`) used instead of the `DIST` constant; nothing else changes
   (`NATIVE_OPTIN` unchanged). The verified diff is in `k14-kernel.md`.
2. Run `python -I C:\GB-x86\stage_kernel.py k14 --dist /build/dist14 --dry-run`, then without `--dry-run`. This
   restages **every** module (VM and native) plus the bzImage from one dist (MODULE_SIG_PROTECT; INTERFACES D11 v1.1:
   each build *may* have its own key, k14 happens to reuse k13's); previous files become `*.pre-k14`.
3. **VM**: boot the VMware image on k14 first (exercises lapis first stage), then build the native images.

Verify: K-02..K-06 results from `k14-kernel.md`; `verify_image.py` check 3 with the k14 krel
(`C:\GB-build\kernel\k14_release.txt`). k14 carries 37 stable patches (0001-0037) + N1-N3 (0101-0103).

## W2-10 `/odm/app` packing (Phase 7; `build_esp.py`)

`gpu_ui.json` places `app/GbGpu/GbGpu.apk` through `_add_native` (parents created automatically). Add the assert: no
`_entries` path starts with `gbnative/priv-app` or `gbstage/priv-app` (`ro.control_privapp_permissions=enforce`).
`gb_gpu_ui.rc` (v1.1) creates `/data/vendor/gb` (0751) and `/data/vendor/gb/ui` (0700 shell). Verify: **VM**
(optional UI test only): with approval, `adb install` of GbGpu.apk on the VM shows the tile as Unavailable (UI-07).

## W2-11 NVK probe rc

Covered by W2-01 (`nvk.json` switches from `rc_text` to `rc_files` = `gb_nvk.rc`). v1.1 `gb_nvk.rc`: the probe runs
as `user shell`, `group shell graphics`; `/data/vendor/nvk` is `0770 shell shell`; the trigger sets `foreign` first.
Verify: the native `gb.rc` contains `setprop vendor.gb.nouveau.foreign 1` inside `on property:vendor.gb.nvk_probe=1`,
and `user shell` under `service gb_nvk_probe`. Existing installs have `/data/vendor/nvk` 0775 root:shell; init's
`mkdir` re-applies owner and mode, so nothing to migrate (old `probe.txt` files stay readable).

## W2-12 Hardening: ueventd (recommended in v1.1)

A native-only `/odm/etc/ueventd.rc` with `/dev/dri/renderD129 0660 root graphics` and `/dev/dri/card1 0600 root root`
(last matching rule wins over `/system/etc/ueventd.rc:60`, INFERRED; the i915 guard guarantees renderD128/card0 are
Intel). It closes the window between the probe creating 0666 nodes and the post-load chmod (review R2 low).
gb_nouveau.sh and gb_power still set the permissions (INTERFACES 7.5); P8 gfxtest boots still chmod 0666. Only valid
while nouveau is the only second DRM device. Verify on the VM image (no nouveau): the file is absent from the VM
cpio; native: `ls -l /dev/dri` after a load (HW).

## W2-13 Cpio hygiene (observed during Wave 1)

`build_esp._tree(NATIVE_MOD_DIR)` packs every file, so `inject\native\modules\*.ko.pre-k13` backups (e.g.
`nouveau.ko.pre-k13`) currently ship in the native initrd. Filter `NATIVE_MODS` to `*.ko` plus `modules.load*`. Needed
for W2-08 item 2 regardless.

## W2-14 Documentation (plan P0 items 2 and 5)

In `C:\GB-repo\googlebook-sb3`: replace "kernel build #12" with k13/k14 where relevant; `docs\DEVICE-STATUS.md` row 17
and checklist, `docs\DRIVERS.md`, `docs\NATIVE.md`, `docs\ARCHITECTURE.md:102-107`, `docs\FLASHING.md:54` (never detach
while booted from a stick in the base) and `:77`, `docs\INPUTS.md:37,74`, `docs\OTA.md`, `README.md:30`, `LICENSES.md`
(TU117 firmware row: linux-firmware tag 20260916, `WHENCE.tu117` + `LICENCE.nvidia` shipped in the payload), component
READMEs, `iso\README-ISO.md`; new `docs\NVIDIA.md` (what works, cost, on/off, recovery with `gb.nouveau=0`, the
nvtest entry, detach rules incl. "Can't detach yet: the NVIDIA driver is starting" (N15), what will not work, "the VM
cannot test this"). Record the Windows baseline (VBIOS 90.17.3F.00.0E, NVIDIA 31.0.15.3892, UEFI 23.101.140), native
IOMMU passthrough, and SELinux enforcing with per-type permissive. Verify: markdown link check.

## W2-15 SELinux

No policy change is expected (INTERFACES 17): gb_nvk_probe moving to user shell keeps the `u:r:shell:s0` seclabel.
Check every component integration file for a declared SELinux need; if one appears, add it to
`C:\GB-x86\inject\odm_sepolicy.cil` as `typepermissive` and record it.

## W2-16 Wave 2 tests and the image matrix

Run, in order: NP-25, GP-24, K-10, GC-11, UI-07, GD-05 (all **VM**, each with the user's approval), then
`verify_image.py` on the USB image, `iso/test` (t0_static, check_esp_tree, validate_dualboot, validate_wholedisk).
NP-25 v1.1: boot `gb-ntest`, press `e`, append `gb.nouveau=test`; expect `vendor.gb.nouveau.result=refused:i915` and
`report/dgpu-<N>.txt` (GC-11 on the same boot: `/vendor/bin/gb_gpu_compute` exits 3).
Check plan section 7 row by row:

| Deliverable | VM | USB | Internal | ISO |
|---|---|---|---|---|
| k14 bzImage + signed modules | bzImage only | yes | yes | yes |
| tu117 firmware + WHENCE/LICENCE | no | payload (initrd before W2-08) | same | same |
| NVK ICD, compute libs | no | payload | payload | payload |
| `nvk_probe`, scripts, rc | no | cpio | cpio | cpio |
| gb_power (socket) | VM build flags, no DTX | yes | yes | yes |
| `gb/dgpu-<krel>.erofs` | no | yes | yes | yes |
| nvtest entry | no | yes | yes | yes |
| GbGpu.apk | optional test only | yes | yes | yes |
| GbGfxDriver.apk + gfxtest entry | no | only with `GB_GFXDRIVER=1` after 8a | same | same |
| Installer MODULE_DENY | n/a | n/a | n/a | yes |

Hardware-only checks (Phase 2-8 `HW` rows in INTERFACES 18 and each integration file) are handed to the user with the
docs from W2-14; nothing in Wave 2 runs on the real GPU.

---

## Appendix: v1.1 interface changes Wave 2 must know (INTERFACES 21)

* `/data/vendor/gb` is **0751** root:shell (was 0771) in every rc that creates it (`gb_nouveau.rc`,
  `gb_gpu_compute.rc`, `gb_gpu_ui.rc`); `report/` 0750; GbGpu's tile marker moved to `/data/vendor/gb/ui/tile_added`;
  `/data/vendor/nvk` is 0770 shell:shell.
* New root-only flags in `/dev/gb_dgpu`: `noload`, `keep`, `off`, `helpers`, `gfxdriver`, and the non-monotonic
  `detaching` (INTERFACES 5.2).
* New results `refused:noload`, `refused:boot`, `refused:detach`; new notification N15.
* The ESP lock steal is rename-based (W2-02, W2-04).
* gb_notify broadcasts with `--include-stopped-packages`.
* The payload tool and the firmware licence files exist; W2-08 is no longer blocked.
