> **Repository note.** The design and test record of the OTA gate, copied from the development tree. It was
> integrated into `build_esp.py` in wave 5b; `ota_gating.patch`, `base/`, `work/`, `evidence/` and `prev/` are
> not published. `C:\GB-x86` = the repository root, `C:\GB-build\native\<x>` = `native/<x>`, `builder:`
> = the Linux builder. For users: [docs/OTA.md](../../docs/OTA.md).

# OTA gate for the /odm injections (wave 5b)

Design, classification, implementation, tests and risk note for "fingerprint gating for the other /odm injections on
OTA". Everything was made and tested in copies only:

* `C:\GB-x86` was only read.
* `lapis.bin` was only read (the slot a/b init_boot ramdisks and the super partition for evidence).
* The user's VM was not touched, and nothing was booted.
* On the builder, only `/build/otagate-*` scratch was used, and it was removed afterwards.

This revision (2026-10-29) rebases the 2026-10-26 attempt onto the **current** `C:\GB-x86` (after the integrate-review
VNNI fixes), re-verifies everything, and changes a few points. Section 9 lists what was kept and what changed.

| Deliverable | Path |
|---|---|
| This document | `C:\GB-build\native\ota-gating\DESIGN.md` |
| Patch: git-style, against the current `C:\GB-x86` files; 4 files, 10 hunks; `build_esp.py` hunks are insertions only | `ota_gating.patch` (sha256 `edef74e6088074a53ec583a10f36f41ee0637b807f4335c4d4538db5ba4385d6`) |
| Unpatched copies (== current `C:\GB-x86`, see `base\BASE.sha256`) | `base\GB-x86\build_esp.py` (`8367b322...598a`), `base\GB-x86\tests\{w3a,w4,w5}\` |
| Patched copies that were tested (== the patch applied) | `work\GB-x86\build_esp.py` (`997812d8db09a98aa911c6053ce41aa0fe6a53ac758915929306211a5eafb274`), `work\GB-x86\tests\{w3a,w4,w5}\` |
| Host suite: 106 checks, including the builder harness and secilc | `tests\test_ota_gating.py` |
| Builder harness: GBINIT under the shipped busybox, gb_otagate.sh in both service roles, the real gb_notify.sh (54 checks) | `tests\gate_harness.sh` |
| SELinux compile proof (lapis .276's own secilc) | `tests\sepolicy\` |
| Tools | `tools\` (`make_patch.py`, `apply_order.py`, `run_existing_suites.py`, `w4_residual.py`, plus the read-only evidence tools `stock_scan.py`, `elf_deps.py`, `ramdisk_props.py`, `lp_list.py`, `dump_consts.py`) |
| Evidence | `evidence\` (section 6) |
| Superseded outputs of the 10-26 attempt (moved, not deleted) | `prev\attempt-20261026\` |

## 0. Summary

**Problem.** After a Google A/B OTA, `gb_slotsync` builds the new slot's initrd from the same `/gb/inject.gz`. So
every `/odm` injection that was built and tested for build N is also applied to build N+1. Until now, only the
wave-5 VNNI libz binds were gated on the exact build fingerprint.

**Finding: very little can be skipped safely.** Every injection of both trees was classified: 136 tree files and bind
sources, with evidence from the real .276 partitions (section 3).
* Only one item is both build-specific and not needed to boot: the patched Google libz (VNNI).
* The other build-specific items are needed to boot or to show the VM's display:
  * the KeyMint graft;
  * the system_ext VINTF manifest replacement;
  * the fstab;
  * the SELinux cil;
  * the VM graphics stack.

  Skipping any of them would turn a possibly stale injection into a certain boot failure. So they keep the old
  behaviour, and the one item whose staleness alone breaks boot, the cil, was made OTA-robust instead.
* Everything else is build-independent:
  * our own drivers, HALs, daemons and configuration;
  * LLNDK/NDK-only binaries;
  * neutralising stubs over Google binaries;
  * masks.

**What the patch adds** (`build_esp.py`, both trees):
1. **An exact build gate in GBINIT.**
   * At build time, `build_esp` packs the init_boot ramdisk fingerprint of the boot slot as
     `/odm/etc/gb/ota_validated`.
   * At rdinit, GBINIT compares it with the fingerprint in this boot's own init_boot ramdisk.
   * Same build: nothing changes.
   * Another build, or an unreadable one:
     * `/odm/vnni` is removed;
     * `ro.vendor.gb.ota_gate=unvalidated` is appended to `/odm/etc/build.prop`;
     * everything needed to boot stays as built.
   * Tokens: `gb.otagate=0` (off) and `gb.otagate=force` (the unvalidated path on the validated build, for tests).
2. **A notification**, `/odm/etc/init/gb_otagate.rc` + `/odm/gb_otagate.sh`, through the existing `gb_update` tag.
   * At `boot_completed`, only when the build is unvalidated:
     * `gb_otagate` (root) writes one kmsg line and queues the message for gb_notify where gb_notify ships and its
       queue exists (native);
     * `gb_otagate_post` (user shell, gb_notify's own identity) posts it directly everywhere else (VM).
   * `gb.rc` is not changed at all.
3. **An OTA-robust SELinux cil.** Every `(typepermissive T)` becomes `(optional gb_ota_T (typepermissive T))`.
   * Today, a type that a later build removes makes init's boot-time policy compile fail. That means
     `LOG(FATAL)` and a reboot loop.
   * The new form compiles to the **byte-identical** policy on .276 (lapis's own secilc). It still compiles when a
     type is missing.
4. **A build-time classification table `OTA_CLASSES`, enforced by `_check_otagate`.**
   * Every file below `/odm/vup`, `/odm/vnni`, `/odm/etc/selinux` and `/odm/etc/vintf`, and every rc bind source below
     `/odm`, must have a class.
   * An unclassified new injection stops the build.
   * GBINIT's unvalidated branch must remove exactly the "skip" roots.

**It never makes things worse.**
* On the validated build, the trees differ from today's only in the following (test DIFF):
  * GBINIT: two insertions;
  * the cil text, which compiles to the identical policy;
  * three new files.

  `gb.rc` and everything else are byte-identical.
* On an unvalidated build, the only boot-relevant change is the removal of `/odm/vnni`. Its binds are
  fingerprint-gated and would not fire there anyway.
* Every gate step may fail without consequence: each failure path reaches `exec /init` (busybox harness).

**Integration-friendly.**
* The `build_esp.py` hunks are pure insertions: no existing line is changed.
* The patch and every fixprep `build_esp.py` patch apply in either order to the same file (`evidence\apply_order.txt`).
* The 10-26 version conflicted with fixprep keymint-wifi. That was shown on the current file:
  `error: patch failed: build_esp.py:2189`.
* `OTA_CLASSES` already classifies keymint-wifi's private-soname native graft (`lib<x>_gbkm16.so`).

**Tests.** All of these pass:
* 106/106 host checks (BASE, FP, FPF, CIL, TREE, DIFF, CHECK, 25 MUT, 3 MODMUT, CLASS, NOTE, VI, APPLY, ORDER,
  BUILDER 54/54, SECILC);
* `git apply --check` on a fresh copy of the current files;
* verify_image RESULT OK on the in-memory user-VM ESP and on the native USB ESP.

The existing suites show no new failure with the patch (section 7.3).

**Still needs a boot.** See section 4.3: one throwaway VM boot, normal and with `gb.otagate=force`.

## 1. Problem

* `gb_slotsync` (`C:\GB-build\mouse\gb_slotsync.c`) follows an A/B OTA.
  * It rebuilds `initrd-<x>.img` from `/gb/inject.gz` (our cpio, unchanged) plus the new slot's
    init_boot/vendor_boot ramdisks.
  * It makes the new slot the default.
* Its only build check is the kernel KMI. On a different KMI, it does a HOLD and posts a `gb_update` notification.
* So a same-KMI OTA (for example a monthly .271 -> .276) boots the new build with every `/odm` injection of the old
  one:
  * the `/vendor` overlay upper layer `/odm/vup`;
  * the rc binds over Google files;
  * the SELinux cil;
  * the VINTF changes;
  * the VNNI binds.
* Only the VNNI binds are gated on the exact build, since wave 5.

## 2. Mechanism

### 2.1 The validated build (build time)

* `build_esp.ota_build_fingerprint(GENERIC)` reads `ro.bootimage.build.fingerprint` from `system/etc/ramdisk/build.prop`
  in the boot slot's init_boot ramdisk. That is the generic ramdisk `build_esp` already reads. It is legacy LZ4;
  gzip and a plain newc cpio are also accepted.
* It uses GBINIT's rule: the last assignment wins, and CRs are dropped.
* It fails closed (the build stops) if the value is missing, empty, or has a character outside
  `[A-Za-z0-9._-/:+,@~]`.
* The result is packed in both trees as `/odm/etc/gb/ota_validated` (0644):
  `ro.bootimage.build.fingerprint=ASUS/lapis/lapis:17/CL3B.260622.276.R4/16658739:user/release-keys`.
* **Why this property.**
  * It is the only build identity readable at rdinit, before init mounts system/vendor. The kernel unpacks all
    initrd segments (gb cpio | vendor | generic) before rdinit, so it is at `/system/etc/ramdisk/build.prop`.
  * It belongs to the slot that systemd-boot actually started.
  * For lapis .276, it equals `ro.vendor.build.fingerprint`, which vnni_prepare read from vendor_b (test FP).
* **Two real builds exist and both are used in the tests:**
  * slot a still holds .271 `CL3B.260622.271.R1/16452207`;
  * slot b holds .276 `CL3B.260622.276.R4/16658739`.
* **The next ESP update re-validates.** `build_esp`, `build_usb` and `build_internal` read the boot slot of
  `lapis.bin` again. `gb_slotsync`'s in-guest rebuild never re-validates, because it reuses the packed
  `ota_validated`.

### 2.2 GBINIT (rdinit, both trees): two insertions, no existing line changed

**(a) The token scan.** It runs while `/proc` is mounted, right after the existing token loop. Only exact tokens count.
```
OG=; for a in $(/busybox cat /proc/cmdline); do case "$a" in gb.otagate=0) OG=0;; gb.otagate=force) OG=force;; esac; done
```
**(b) The gate.** It sits right after `cp -a $SRC/. /odm/` and the VNNI kill switch. It runs before:
* the `/odm/etc/gb_hw` line;
* the USB fstab edit;
* the whiteouts;
* the bring-up;
* the module loading;
* `exec /init`.
```
if [ -f /odm/etc/gb/ota_validated ]; then
  FV=$(sed -n 's/^ro\.bootimage\.build\.fingerprint=//p' /odm/etc/gb/ota_validated | tail -n 1)
  FB=$(sed -n 's/^ro\.bootimage\.build\.fingerprint=//p' /system/etc/ramdisk/build.prop 2>/dev/null | tr -d '\r' | tail -n 1)
  OS=unvalidated
  [ -n "$FV" ] && [ "$FB" = "$FV" ] && OS=validated
  [ "$OG" = force ] && OS=unvalidated
  [ "$OG" = 0 ] && OS=off
  if [ "$OS" = unvalidated ]; then
    rm -rf /odm/vnni
    echo >> /odm/etc/build.prop
    echo "ro.vendor.gb.ota_gate=unvalidated" >> /odm/etc/build.prop
  fi
  { echo "state=$OS"; echo "validated=$FV"; echo "booted=${FB:-unknown}"; echo "token=${OG:-none}"; } > /odm/etc/gb/ota_state
fi
```
(All commands are `/busybox` applets in the real text.)

**States:**

| State | When | Effect |
|---|---|---|
| `validated` | exact equality, `FV` not empty | none (state file only) |
| `unvalidated` | another build, an unreadable or missing ramdisk prop, an empty `FV`, or `gb.otagate=force` | skip roots removed, the property appended, notification at boot_completed |
| `off` | `gb.otagate=0` | everything as built (state file only) |
| (none) | a tree without `ota_validated` (an old ESP, the existing harness fixtures) | the block does nothing |

**Fail-safety.**
* There is no `set -e`. Every command is a busybox applet or an ash builtin.
* Each failure path was run under the shipped busybox 1.35.0, and each one reached `exec /init` (harness G7, G8, G13):
  * a missing ramdisk prop;
  * a prop path that is a directory, so sed fails;
  * an empty validated file.
* If `rm` failed, `/odm/vnni` would stay. Its binds are still gated on the exact `ro.system/vendor.build.fingerprint`.
* If the `build.prop` append failed, there would be no notification and nothing else would change.
* "Unknown" means unvalidated, because the unvalidated branch only removes things that are not needed to boot.

### 2.3 `/odm/etc/init/gb_otagate.rc` + `/odm/gb_otagate.sh` (both trees)

```
service gb_otagate /system/bin/sh /odm/gb_otagate.sh            (user root, group root shell, seclabel u:r:shell:s0,
service gb_otagate_post /system/bin/sh /odm/gb_otagate.sh post   (user shell, group shell, ...)   oneshot, disabled)
on property:sys.boot_completed=1 && property:ro.vendor.gb.ota_gate=unvalidated
    start gb_otagate
    start gb_otagate_post
```
* **Its own rc file, not gb.rc.**
  * init parses every file in `/odm/etc/init`.
  * `gb.rc` stays byte-identical, so the existing gb.rc checks and tests are untouched.
  * The patch no longer edits the native `gb.rc` line, which fixprep keymint-wifi also edits.
  * `_check_otagate` requires that no other rc file names the gate's services or property.
* **The trigger.**
  * `/odm/etc/init/*.rc` is parsed in the vendor_init subcontext. `ro.vendor.*` is one of init's partner prefixes,
    so it is always actionable there.
  * `sys.boot_completed` is readable by vendor_init. gb.rc already uses it (both trees: gb_slotsync, the gscd stop;
    VM: gb_adb), and so do the hw and sensors components.
  * If the trigger were ever refused, init would skip the action. That is the safe failure: no notification and
    nothing else. The W5 integrate review covers the same mechanism for the VNNI triggers.
  * The property comes from `/odm/etc/build.prop`, which init loads after `/vendor`, in the `vendor_init` context.
    That context is permissive (`(typepermissive vendor_init)` in our cil). The W5 integrate review F4 cites gblog
    lines that show this file's lines being applied (`Overriding previous property ...`).
  * On the validated build, the trigger never fires. Init only parses two more service stanzas.
* **`gb_otagate.sh`** exits at once unless `/odm/etc/gb/ota_state` says `unvalidated`.
  * **`gb_otagate` (root).**
    * It writes one kmsg line:
      `gb_otagate: UNVALIDATED build: booted <fp>, ESP built for <fp> (token ...); skipped: VNNI libz patch; kept as built (needed to boot): KeyMint graft, SELinux, VM graphics, fstab, system_ext VINTF; update the ESP to re-validate`.
      Each field is printable ASCII and at most 120 characters.
    * If gb_notify ships (`/odm/vup/bin/gb_notify.sh`) and its queue `/data/vendor/gb/notify` is a real directory
      (native; `qok`), it writes
      `kind=notify / tag=gb_update / title=System update not validated / text=Build <id> is not the build this boot setup was made for (<id>). Paused until the next ESP update: VNNI libz patch.`
      as `<epoch ms>-<pid>-1.msg`: `.tmp` first, noclobber, then a rename. Then it sets `vendor.gb.notify`, as
      every other producer does.
    * Otherwise it logs that `gb_otagate_post` posts. A stray queue directory without gb_notify is never written,
      because nobody would deliver from it.
    * It never runs `cmd` as root: INTERFACES D6 says "Posting from uid 0 is unverified".
  * **`gb_otagate_post` (user shell).**
    * Where `qok` holds, it exits.
    * Otherwise (the VM has no gb_notify; or natively, no queue directory yet), it runs
      `timeout 20 cmd notification post -t <title> gb_update <text>`. That is the same identity and command that
      gb_notify uses.
    * A failure is logged; with shell's uid, the log goes to logcat, because kmsg is 0600 root.
    * Notifications with the same tag replace each other, so a double post would show once.
* **Limits of the message.**
  * The ids are the build-id field of each fingerprint: `[A-Za-z0-9._-]` only, at most 24 characters, `unknown` when
    empty.
  * The worst-case text is 155 characters (the limit is 200), and the title is 27 characters (the limit is 40).
* The script changes nothing else (test NOTE).

### 2.4 The OTA-robust SELinux cil

* **Why it matters.** `inject\precompiled_sepolicy` is 1 byte and has no `.sha256` companions. So init compiles the
  split policy with secilc at every boot, and our `odm_sepolicy.cil` (2694 `typepermissive` lines) is one of the
  inputs.
  * One type that a later build removes fails the whole compile: `Failed to resolve typepermissive statement`, rc 254.
    Init then calls `LOG(FATAL)`, and that build loops.
  * Skipping the cil would boot that build enforcing, because the user build ignores `androidboot.selinux`. That is
    worse.
* **The transform.** `build_esp.ota_safe_cil` wraps each line in its own `optional` block. It refuses anything that is
  not a `(typepermissive T)` line, and it refuses repeated types.
  * In the patch it is defined in the OTA block, and `ODMCIL` is reassigned there, before either tree uses it. That
    keeps the original `ODMCIL = open(...)` line untouched.
* **Proof** (`evidence\sepolicy_secilc.txt`, re-run 2026-10-29): lapis .276's own `/system/bin/secilc` ran through its
  bootstrap linker on the builder, with init's `LoadSplitPolicy` arguments and file order, on the extracted .276
  partitions. The four stored inputs are checked against today's `inject\odm_sepolicy.cil` (`8265b908...`).

  | odm cil | secilc | policy sha256 |
  |---|---|---|
  | none (stock) | rc 0 | `25725580...` |
  | plain (today) | rc 0 | `ab621323...` |
  | wrapped | rc 0 | `ab621323...` (byte-identical) |
  | plain + 1 unknown type (an OTA that removed a type) | **rc 254, no policy** | - |
  | wrapped + 1 unknown type | rc 0 | `ab621323...` (byte-identical) |

* **Compile time** (10-26, 5 interleaved runs at nice 19 on the shared builder): plain 982-1440 ms, wrapped
  965-1582 ms. That is noise level (`evidence\sepolicy_compile_time.txt`).

### 2.5 Build-time enforcement (`OTA_CLASSES`, `_check_otagate`)

* **The table.** `OTA_CLASSES` lists (fnmatch pattern below the tree root, class, label), and the first match wins.
  * `skip`: removed on an unvalidated build.
  * `keep`: build-specific and needed to boot.
  * `always`: build-independent.
* **The check.** `_check_otagate(ents, tree, label)` runs inside `_check_vm` and `_check_native`. It refuses the build
  in each of these cases:
  * `ota_validated` is missing, has the wrong mode, or is for another build;
  * `ota_state` is packed;
  * `gb_otagate.sh` or the rc file is missing or changed;
  * another rc file names the gate;
  * the token scan is not before `umount /proc`;
  * the gate is missing, duplicated, or not between the VNNI kill switch and the bring-up, the module loading and
    `exec /init`;
  * the unvalidated `rm` list differs from the `skip` roots;
  * an unclassified file or rc bind source exists;
  * the cil is not in the OTA-robust form.
* **Tests.**
  * Each refusal is a mutation test (MUT, 25 cases).
  * The unpatched trees fail the check (CHECK).
  * Three mutated copies of the patched `build_esp.py` do not even build (MODMUT): the gate removed from GBINIT, the
    `rm` removed, and an unclassified `/odm/vup` graft added.
* A future component that adds a build-specific graft therefore cannot ship without a conscious decision.
* **The C-2 names.** `OTA_KM_NATIVE` classifies the C-2 private sonames `vup/lib64/lib<x>_gbkm16.so` as
  keep/KeyMint graft. That is keymint-wifi's own name rule `"lib64/" + n[:-3] + "_gbkm16.so"`. Test CLASS renames
  the native graft that way: `_check_otagate` passes, and any other name is refused.

## 3. Classification (both trees)

**Evidence.**
* `evidence\classification.tsv`: one row per injection, 136 rows, written by the test.
* `evidence\stock_scan_slot_b.tsv`: whether the .276 stock partition (vendor_b, system_b, system_ext_b of lapis.bin's
  super, read-only) has that path, with stock size and sha256 for uncompressed files.
* `evidence\elf_deps.tsv`: interpreter and DT_NEEDED of every ELF.

Both evidence files were re-run on 2026-10-29 against the patched copy, and both are identical to the 10-26 run.
The counts are skip 10, keep 34, always 92.

| Class | Injection (tree) | Over a stock Google file? | Why this class / what skipping would do |
|---|---|---|---|
| **skip** | `/odm/vnni/<id>/` patched libz plus its 2 binds (both trees) | binds over `/system/lib64/libz.so` and `/vendor/lib64/libz.so` | A patched Google binary, valid for one exact build. Without it, the stock libz runs under k15's VNNI emulation: correct, only slower. The binds are also gated on the exact fingerprint, so skipping drops only inert files. |
| **keep** | KeyMint graft: `vup/bin/hw/android.hardware.security.keymint-service` + 9 libs in `vup/lib64` (both trees; natively `lib<x>_gbkm16.so` once C-2 lands) | today `libcrypto.so`, `lib_android_keymaster_keymint_utils.so`, `libkeymaster_messages.so` replace stock vendor libs | android16 binaries linked against stock `android.hardware.security.keymint-V4-ndk`, `libbase`, `libc++`, `libcutils`, `libhardware`, `libhidlbase`. Without them there is no KeyMint, keystore2's `earlyBootEnded` hangs, and boot hangs. Residual risk: other stock vendor binaries also load the stale android16 `libcrypto.so`. C-2 (fixprep keymint-wifi) removes that risk natively. |
| **keep** | `/odm/etc/se_manifest.xml` bind over `/system_ext/etc/vintf/manifest.xml` (both) | yes: stock `e111dd3c...` (2164 B) replaced by ours (1111 B) | Declares software KeyMint V4 and drops the Trusty-VM KeyMint V5. Without it, KeyMint V5 is expected from a VM that does not exist, and boot hangs. New system_ext HALs of a later build would be hidden; that is not detectable at rdinit. |
| **keep** | `/odm/etc/fstab.android-desktop` bind over the vendor fstab (both) | yes: stock `582317c4...` | Encryption bypass plus fsverity. With the stock fstab, FBE would apply to the existing unencrypted /data: a wipe prompt or a boot failure. |
| **keep** | `/odm/etc/selinux/odm_sepolicy.cil` + the 1-byte `precompiled_sepolicy` (both) | no (odm files) | Everything is permissive (the user build ignores `androidboot.selinux`). Without them the build boots enforcing, and the overlay, the binds and our services fail. Now OTA-robust (2.4). |
| **keep** | VM graphics: `vup/lib64/libminigbm_gralloc.so`, `libgbm.so` (patched minigbm, vmwgfx backend), `vup/lib64/egl/*` (Mesa svga) (VM) | minigbm/libgbm replace stock | Stock minigbm has no vmwgfx backend: the allocator aborts, there is no default display, and system_server blocks. Mesa is the system EGL. |
| always | Stubs over Google binaries: the Trusty secretkeeper/gatekeeper, touchpad-inhibitor, the charge-control HAL, storage_metrics_service, crossystem (both); fwupd*, flashrom, futility, ectool, futility-executor, init.adbd-killer.sh, android-desktop-install (native); the noop/sleeper/fw_stub binds over `/system/bin/{preloads_copy.sh,timberslide,udsattestation,google_desktop_firmware_updater}` (both) | yes, all | Content ours (sleep, refusal, no-op). The reason ("this Google service cannot work or must not run here") does not depend on the build. They are boot-critical: crash storms, the checkpoint reboot loop, RescueParty. Natively they are safety-critical: no firmware flashing. Never skipped. If a path vanishes, the stub or bind is inert. |
| always | Masks: the empty-VINTF binds over the strongbox apex, Weaver and Trusty-gatekeeper fragments (both); GBINIT's 4 whiteouts | yes | Same reasoning: ours, needed to boot, and inert if the path vanishes. |
| always | Software Gatekeeper: binary + ODM VINTF fragment (both) | no (new paths) | LLNDK only (`libbinder_ndk`, `libc`, `libdl`, `liblog`, `libm`), stable AIDL. Must never be skipped: with a PIN set, the lock screen would become unverifiable. |
| always | Our binaries: `gb_*`, the tinyalsa tools, iptsd, nvk_probe, egl_probe, `vulkan.intel.so` (pastel, VM), the `vulkan.nouveau.so` symlink, the camera stack in `/odm/lib64` | only pastel | Static, or LLNDK/NDK-only (`elf_deps.tsv`). These are the build-independent drivers and HALs that keep working after an OTA (harness G4: native modules still load on an unvalidated build). |
| always | Our configuration: IDC, keylayouts, permissions, the sensors RRO, the audio configs and `audio_default.xml` (both), `excluded-input-devices.xml` (native) | `audio_default.xml`, the VM keyboard IDC, `excluded-input-devices.xml` | Our values in stable formats. Skipping them would make every build worse: the wrong audio card, the VM keyboard disabled by posture, the Surface HID devices shown as gamepads. |
| (outside the `OTA_CLASSES` roots) | Kernel modules, firmware, `/odm/etc/build.prop` overrides, gb.rc setprops/services, our own files outside `vup` (for example the camera stack in `/odm/lib64`) | - | Kernel-bound (gb_slotsync's KMI hold covers a kernel change) or ours and build-independent. Every rc `mount` whose source is not below `/odm` is the overlay setup or a VNNI remount (checked on both trees). |

**Why there is no per-file content (hash) check on the device:**
* The only build-specific injection that can be skipped safely (VNNI) is already content-pinned:
  * `orig_sha256` and `patched_sha256` are checked at prepare time;
  * its binds are gated on the exact fingerprint.
* For the "keep" items, a hash check of the stock file they shadow cannot change the decision: skipping them breaks
  boot.
* A check at rdinit is impossible: `/vendor` is not mounted yet.
* An early-init check before the overlay mount would add a new boot-path step for no change in outcome.
* The one "keep" item whose staleness alone breaks boot, the cil, was made content-robust instead (2.4).
* An informational on-device report of the stock hashes is follow-up F3.

## 4. What changes at boot (risk note)

### 4.1 Validated build (today's VM and every image built from this tree)

* **GBINIT, at rdinit (no SELinux yet):**
  * one extra `cat /proc/cmdline` loop;
  * two sed/tail reads;
  * the state file `/odm/etc/gb/ota_state` (tmpfs).
* **`/odm` gains:**
  * `etc/gb/ota_validated`, `etc/gb/ota_state`, `gb_otagate.sh` and `etc/init/gb_otagate.rc`;
  * the VM also gains the `etc/gb/` directory.
* **init:** parses one more rc file with two disabled services and one trigger. The trigger never fires, because no
  `ro.vendor.gb.ota_gate` property exists.
* **SELinux cil:** the text changes, but the compiled policy is byte-identical (lapis's own secilc). The compile time
  changes only by noise.
* **The VM tree record changes:**
  * the current pipeline: `5df55fe0dcc8fbe3d5544b3f58d48e68f082cb263d63be4d18c4e23804f32eba`;
  * patched: `56df888dc0b51885b39b3c54253f0d0d5d16ff77191f4ce8c2f7804a5d5fbd32`.

  The user-VM preview check (`--expect-inject-sha256`) must use the new record once this is integrated.
* **Unchanged:** `gb.rc`, VNNI, the Gatekeeper, zram and everything else (test DIFF).

### 4.2 Unvalidated build (after a same-KMI Google OTA, or `gb.otagate=force`)

* `/odm/vnni` is removed. Its binds fail harmlessly: that is the proven `gb.vnni=0` path. On a real other build they
  would not fire anyway.
* `ro.vendor.gb.ota_gate=unvalidated` is set.
* At `boot_completed`, the kmsg line is written and the `gb_update` notification "System update not validated" is
  posted:
  * native: through gb_notify;
  * VM: `gb_otagate_post` as user shell.
* Everything else is applied exactly as today, the "keep" items included. They carry the same risk as today, minus
  the cil reboot loop.
* The previous slot's systemd-boot entry is still built for the validated build. It stays the manual fallback.

### 4.3 What was tested, and what still needs a boot

**Tested on the host and on the builder:**
* the gate logic: GBINIT under the shipped busybox 1.35.0 with the real .271 and .276 ramdisk props (G1-G16);
* every token, CRLF and near-miss case;
* every failure path; each one reaches `exec /init`;
* native module loading after the gate;
* both service roles of gb_otagate.sh (N1-N11):
  * native: queue present, the post role silent;
  * VM: no queue, the root role silent, the post role posts;
  * a stray queue directory without gb_notify, and gb_notify without a queue directory: one direct post each;
  * a failing `cmd`;
  * a symlinked queue;
  * a non-writable kmsg;
  * hostile fingerprints;
* the real gb_notify.sh accepting and posting the message (N3, N6);
* the compiled policy equality;
* the build-time refusals and module-level mutations;
* verify_image on both ESP kinds;
* the patch applying in any order with the fixprep patches.

**Not tested. Each needs a boot:**
1. **A throwaway VM boot of the patched VM tree.** Use `build_usb --vm-test` or the W5 measurement disk's ESP rebuilt
   with this tree. Expect:
   * `Compiling SELinux policy` followed by `Loading SELinux policy`;
   * `cat /odm/etc/gb/ota_state` showing `state=validated`;
   * no `gb_otagate` start;
   * the VNNI binds present.
2. **The same disk once with `gb.otagate=force`.** Expect:
   * `getprop ro.vendor.gb.ota_gate` = `unvalidated`;
   * `/odm/vnni` absent;
   * the kmsg line;
   * `init.svc.gb_otagate_post` = stopped after it ran;
   * **the notification visible in the VM.** This is the first on-device use of `cmd notification post` from an init
     service as user shell in the VM. gb_notify uses the same identity natively, but it has also only been
     harness-tested. If it does not show, the kmsg/logcat lines remain and nothing else is affected.
3. **Native:** queue-to-gb_notify delivery on real hardware (harness-proven only).
4. **A real OTA to a build we have not validated.** No newer lapis build exists. It was simulated with slot a's real
   init_boot fingerprint.

None of these can produce a boot loop:
* Items 2-3 only exercise paths that run after `boot_completed`.
* Item 1's boot-path differences were proven equal on the host: the policy bytes, and GBINIT under the shipped busybox.
* The one new boot-path element that was not proven equal is init parsing `gb_otagate.rc`. Its syntax is the same as
  the existing gb.rc stanzas. A parse error would only be logged, and that file's services would be skipped.

## 5. How it was tested (commands)

```
python -I -B C:\GB-build\native\ota-gating\tests\test_ota_gating.py --builder --secilc --scratch <dir>
python -I -B C:\GB-build\native\ota-gating\tools\run_existing_suites.py <out dir> --patched-tests w5 w4 w3a trees tb3
python -I -B C:\GB-build\native\ota-gating\tools\apply_order.py <scratch dir>
python -I -B C:\GB-build\native\ota-gating\tools\make_patch.py <scratch dir>     (regenerates ota_gating.patch from base\ -> work\)
```

* `--builder` and `--secilc` use the builder over ssh (`builder@192.168.115.132`), at nice 19 / ionice idle.
  * Only `/build/otagate-*` scratch is used: it is removed afterwards, and the harness run checks that.
  * The harness refuses to start if `/build` has less than 3 GB free.
  * The secilc run reads the existing `/var/tmp/vnniperf/fs` extraction (.276).
* After integration, run the suite against the integrated file:
  `--build-esp C:\GB-x86\build_esp.py --base <the archived pre-integration copy>`.

## 6. Evidence (`evidence\`)

| File | What |
|---|---|
| `test_ota_gating.txt` | the full host suite run (106/106, with `--builder --secilc`) |
| `gate_harness_builder.txt` | the 54 builder harness checks |
| `sepolicy_secilc.txt`, `sepolicy_compile_time.txt` | the secilc proof (re-run 10-29) and the timing (10-26) |
| `classification.tsv` | the 136 classified injections with class, label and stock relation |
| `stock_scan_slot_b.tsv`, `elf_deps.tsv` | stock-partition and ELF evidence (re-run 10-29, identical to 10-26) |
| `verify_image_vm.txt`, `verify_image_native.txt` | verify_image RESULT OK on the in-memory patched ESPs |
| `apply_order.txt` | order independence with the fixprep `build_esp.py` patches |
| `existing_suites.txt` | impact on the existing suites (7.3) |
| `w4_residual.txt` | the one pre-existing w4 snapshot difference, identical in base and patched |

## 7. Integration (for the single writer of C:\GB-x86)

### 7.1 Steps

1. **Copy the originals first.** Copy `build_esp.py` and `tests\w3a\test_w3a.py`, `tests\w4\test_w4.py`,
   `tests\w5\test_w5.py` to the wave's archive folder (for example `C:\GB-x86\archive\pre-w5c\`), with SHA256SUMS.
2. **Check the base, then apply.** If the files still match `base\BASE.sha256`, apply the patch as below. This host
   has `core.autocrlf=true`, so a plain `git apply` outside a repository would write CRLF.
   ```
   cd C:\GB-x86 && git -c core.autocrlf=false -c core.eol=lf apply --check C:\GB-build\native\ota-gating\ota_gating.patch
   cd C:\GB-x86 && git -c core.autocrlf=false -c core.eol=lf apply C:\GB-build\native\ota-gating\ota_gating.patch
   ```
   * With an unchanged base, the result is `work\GB-x86\build_esp.py` byte for byte (`997812d8...b274`).
   * If fixprep patches were applied first, it still applies cleanly (`evidence\apply_order.txt`). Then
     `build_esp.py` is no longer the same file, so check the result with the test suite instead of the hash.
3. **Run the checks.** Run `test_ota_gating.py --build-esp C:\GB-x86\build_esp.py --base <archived copy> --builder`,
   then the existing suites (7.3).
   * With fixprep keymint-wifi integrated, the CLASS "no dead pattern" check passes with the C-2 names in use. The
     DIFF/BASE sections then also show that integration's own changes, which is expected.
4. **Rebuild with the gate.** The user-VM ESP, the USB image, the export and the ISO must be rebuilt to carry it.
   The VM tree record changes (4.1).

### 7.2 Docs to update after integration

Not done here, because C:\GB-x86, HANDOFF.md and the repo are owned by others:
* HANDOFF section 0: the gate, the tokens, the new record.
* The vnni-perf INTEGRATION.md OTA note.
* README-FLASH / docs:
  * the boot-menu tokens `gb.otagate=0` and `gb.otagate=force`;
  * the "System update not validated" notification and what to do: update the ESP from the host, which
    re-validates, or pick the previous slot's entry.

### 7.3 Impact on the existing suites

Each suite was copied and run twice by `tools\run_existing_suites.py --patched-tests` (`evidence\existing_suites.txt`):
* base: today's `C:\GB-x86` test file with the unpatched `build_esp.py`;
* patched: the patched test file from `work\` with the patched `build_esp.py`.

The verify_image and build_usb copies differ only in the `build_esp.py` path.

**Result: no new failure with the patch.** Every failure is pre-existing: it is in the base run too, with the same
lines.

| Suite | Base (today) | Patched | Note |
|---|---|---|---|
| w5 `tests\w5\test_w5.py` | 3 FAIL | the same 3 FAIL (identical lines, details included) | PREP (F7) "fake patcher" cases from the integrate-review edits made by the unidentified agent; they are not ours (fixprep `vnni-review-verify` covers them) |
| w4 `tests\w4\test_w4.py` | 1 FAIL | the same 1 FAIL | "DIFF native vs the pre-W4 snapshot". The only unexpected difference is `gbnative/etc/iptsd/iptsd.conf` (changed 10-26 ~21:05, outside this task), in base and patched alike (`evidence\w4_residual.txt`, `tools\w4_residual.py`) |
| w3a `tests\w3a\test_w3a.py --no-builder` | OK | OK | |
| trees `tests\w2b\test_trees_from_build_esp.py` | OK | OK | |
| tb3 `tests\w2b\test_verify_image.py` | 2 FAIL | the same 2 FAIL (identical lines) | T5/T6: stale-image V12 expectations name the Gatekeeper, gbinit, gbbringup.sh and iptsd.conf; the test's fixtures predate W4. Not ours. |

The test hunks of the patch adapt exactly the checks that compare GBINIT or the tree delta with an older snapshot:
* w5 "PACK GBINIT == the pre-W5 GBINIT plus exactly the three kill-switch edits": it strips the two OTA insertions
  first;
* w4 `w5_paths` and `pre_w5_gbinit`;
* w3a `w5()`: they expect the three new files, the cil and, in the VM tree, `etc/gb`.

The 10-26 run of the earlier patch without test updates showed these checks failing, plus its gb.rc checks
(`prev\attempt-20261026\evidence\suites_summary_without_test_updates.txt`).
`gb.rc` no longer changes, so none of the 10-26 gb.rc test rewrites are needed.

## 8. Follow-ups (not done, by design or scope)

* **F1 verify_image V32.** Check the gate in an image:
  * `ota_validated` == the boot slot;
  * the GBINIT pieces;
  * the rc file;
  * the cil form.

  Today V12 (GBINIT source equality) already catches a GBINIT mismatch, but nothing checks the rc file in an image.
* **F2 gb_bootlog.** Add an `ota gate:` line from `/odm/etc/gb/ota_state` (needs the w2a TA-3 harness re-run).
* **F3 Optional on-device report.** In the unvalidated state, log the sha256 of each stock file a "keep" item shadows
  (via `/odm/vlower`) for comparison with `stock_scan_slot_b.tsv`. Diagnostic only.
* **F4 = fixprep keymint-wifi (C-2).** Private sonames for the native KeyMint graft. It is already classified here. The
  VM tree keeps the stock-replacing names until a rehearsal.
* **F5 systemd-boot boot counting** in gb_slotsync (`gb-<x>+3.conf` + blessing at `boot_completed`). A build that fails
  to boot three times would fall back to the previous slot's entry by itself. This is a C change.
* **F6 (no action).** The native ESP bring-up cannot set the gate property: its `GB_PROP` deny list refuses
  `ro.vendor.gb.*`.
* **F7 Harness.** If the gate is added to `tests\w3a\bringup\test_bringup.sh`, its `boot()` sed list must also rewrite
  `/system`. Its fixtures have no `ota_validated`, so they are unaffected today.

## 9. Changes against the 2026-10-26 attempt (resume, 2026-10-29)

**Kept after re-verification.** All of these were re-run on 10-29, with the same results:
* the classification and the evidence tools (`stock_scan_slot_b.tsv` and `elf_deps.tsv` are identical);
* the GBINIT gate (identical text);
* `ota_build_fingerprint`;
* `ota_safe_cil` and the secilc proof;
* the busybox harness G/M cases.

**Rebased** onto the current `C:\GB-x86` (`build_esp.py` `8367b322...`, after the integrate-review VNNI fixes; the
originals are in `C:\GB-x86\archive\pre-w5b\`). The old patch still applied, with offsets, but it was regenerated
exactly.

**Changed:**
1. **The rc block moved out of gb.rc** into its own file `/odm/etc/init/gb_otagate.rc`.
   * The old patch edited the native `gb.rc` packing line, which fixprep keymint-wifi edits too. Applied after ours,
     keymint-wifi failed: `patch failed: build_esp.py:2189`.
   * `gb.rc` is now byte-identical, and the test patches no longer need gb.rc rewrites.
2. **The direct post in the VM runs as user shell** (`gb_otagate_post`), not as root. That follows INTERFACES D6
   ("Posting from uid 0 is unverified"), and it is the identity gb_notify uses.
3. **Pure insertions in `build_esp.py`.**
   * The cil transform is defined in the OTA block, and `ODMCIL` is reassigned there.
   * The native files are added next to the VM ones, in one loop.
   * So no existing line changes, and the patch applies in any order with every fixprep `build_esp.py` patch.
4. **C-2 names classified** (`OTA_KM_NATIVE`).
5. **The queue is used only where gb_notify ships** (`qok`), so a stray queue directory cannot swallow the VM's
   notification.
6. **More tests:** BASE, MODMUT, ORDER, C-2 CLASS, the rc-mount CLASS checks, the post-mode NOTE checks, and
   harness N1b/N2b/N4b/N7/N8-N11.

Credits: the idea of gating patched Google files per build comes from wave 5 (VNNI) and from gbOS86's porting notes
(ideas only). All code here is ours.
