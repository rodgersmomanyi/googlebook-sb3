> **Repository note.** The executed wave 3 plan (and its review), copied from the development tree. Paths
> such as `C:\GB-build\...` and `C:\GB-x86\...` are the development layout (`C:\GB-build\native\<x>` =
> `native/<x>`, `C:\GB-x86` = the repository root, `builder:` = the Linux builder); `evidence/` and the
> planning session's own files are not published. What wave 3 changed for a user: the README, docs/ and
> [kernel/K15.md](../../../kernel/K15.md).

# Wave 3 plan -- adversarial review (safety, feasibility, split, tests)

Reviewed: `C:\GB-build\native\dgpu\w3\WAVE3_PLAN.md` (692 lines, as written by the planner).
Reviewer scope: read-only everywhere except this file and the plan. Every correction is applied **in place in
WAVE3_PLAN.md** and tagged `[W3R-nn]` there (grep for `W3R-`). Nothing else was modified; temporary helper scripts
live only in the session scratchpad.

## Verdict

**Conditional GO.** The plan's direction is right and its two load-bearing re-verifications hold up (rec 1 and rec 3,
re-checked below against `/build/gki/common@3938c8d97142` and our own patch). As written, though, it had **9
high-severity defects**. Five break a safety property the task asks for: ESP config able to execute code or replace
policy, a boot path that can hang, the VM `/data` changed in place, a slot update that can boot the wrong ramdisk, and
a new bug in the YMM fix. Two items could not be implemented as specified (no devtmpfs/by-name at rdinit; no LZ4/cpio
parser in gb_slotsync), and one shipped feature had no way to be turned on (i915 param vs busybox insmod). All of these
are now corrected in the plan. With the corrections applied, the plan is executable once the R-02 gate opens.

## 0. What I re-verified myself (sources, not the upstream result)

| Claim | Where checked | Result |
|---|---|---|
| `serial8250_init()` returns `-ENODEV` when `nr_uarts == 0`, before `serial8250_reg.nr = UART_NR` + `uart_register_driver` | `drivers/tty/serial/8250/8250_platform.c:299-316` | **confirmed** (so `drv->nr` stays 0) |
| `serial_core_add_one_port` guard `uport->line >= drv->nr` -> `-EINVAL` | `serial_core.c:3074` | **confirmed** (= gbOS86's `-22`) |
| on-demand growth past `nr_uarts` | `8250_core.c:695-708` (`serial8250_setup_port(nr_uarts); nr_uarts++`) | **confirmed**, only once the driver is registered |
| param name `8250.nr_uarts` | `8250/Makefile:6-8` (`8250-y := 8250_core.o 8250_platform.o`), `8250_platform.c:386,413` | **confirmed** (built-in, the kernel applies it) |
| k14 `RUNTIME_UARTS=0`, `NR_UARTS=32` | `/build/dist14/kernel_x86_64_dot_config` | **confirmed** |
| installer kernel needs nothing | `/boot/config-6.8.0-147-generic`: `RUNTIME_UARTS=32`; `iso/build/builder/remote.sh:89` copies exactly `/boot/vmlinuz-6.8.0-147-generic`; `iso/DESIGN.md` 3.3 loads `intel_lpss_pci` + SAM stack | **confirmed** |
| VM and `--vm-test` cmdlines unaffected | `build_esp.py:1951,1960`, `build_usb.py:223` (both use `be.CMDLINE`, which keeps `8250.nr_uarts=4`) | **confirmed** |
| SB3 keyboard/touchpad depend on the SAM UART | `hw-gap-audit/tree/usr/idc/...09AF_Touchpad.idc` header: "Surface Aggregator (SAM) HID 045E:09AF via surface_hid" | **confirmed** (rec 1 really is P1) |
| emulator runs IRQs-off; `cond_local_irq_enable` idiom | `traps.c:537-552` (`irqentry_enter` -> `handle_invalid_op` -> `gb_emulate_vnni`), `traps.h:48-58`, `do_error_trap` `traps.c:259-269` | **confirmed** |
| user copies happen **before** the FPU section | patch 0002 (`copy_from_user` x3, then `kernel_fpu_begin()`) | **confirmed**; good: with IRQs on, `kernel_fpu_begin` (`fpu/core.c:530-546`) takes `fpregs_lock()` (= `local_bh_disable`), so the FPU read-modify-write cannot be preempted or hit by softirq FPU use; faults must stay outside it |
| VEX.128 upper half not zeroed; SSE init state not handled | patch 0002 lines 161-166, 140 | **confirmed** |
| `get_xsave_addr` returns NULL for init-state components | `fpu/xstate.c:1040-1070` | **confirmed**; it also `WARN_ON_ONCE`s for features not enabled |
| `CONFIG_DEBUG_FS=y`, `DEBUG_FS_ALLOW_ALL=y`, `DEBUG_ATOMIC_SLEEP` off, `PREEMPT=y`, `DEVTMPFS` **not set**, `EFIVAR_FS` not set, `NLS_CODEPAGE_437/ISO8859_1=y`, `VFAT_FS=y`, `ZRAM=m`, `ZSMALLOC=m`, `INSTRUCTION_DECODER=y` | k14 config | as listed |
| `insn_fetch_from_user()` (sleepable, tolerates short reads) | `arch/x86/lib/insn-eval.c:1508-1519` | present |
| i915 hook point | `display/intel_display.c:4449-4463` | **confirmed**; but display params live in `display/intel_display_params.{c,h}` and are **copied at probe** (`intel_display_device.c:1666`) |
| lapis `boot_<x>` carries a GKI bzImage whose version string is readable without decompression | read-only read of `C:\GB-x86\lapis.bin` GPT + `boot_a`/`boot_b` headers (setup header `HdrS`, `kernel_version` at +0x20E) | both slots: `6.18.21-android17-5-g3938c8d97142-ab16302624 (kleaf@build-host) #1 SMP PREEMPT ...` |
| btintel legacy name format | `drivers/bluetooth/btintel.c:1941-1969` | `ibt-%u-%u-%u` for hw_variant 0x11-0x14 (**decimal**) |
| host / builder CPUs | host `i7-1065G7` (Ice Lake, no AVX_VNNI), builder `/proc/cpuinfo`: `avx512_vnni`, `avx512vl` | the VMware KAT premise holds; the lapis-boot.vmx AVX_VNNI mask is moot on this host |
| disk now | C: 82.2 GB, D: 62 GB free; builder `/build` **12 GB free (90 % used)**, `/` 27 GB | floors met; `/build` is only ~2 GB above kbuild's 10 GiB preflight floor |

## 1. Findings (most severe first)

### HIGH

**W3R-01: the ESP overlay whitelist allowed code execution and policy replacement (item 12).**
Plan 2.3 allowed `gb/overlay/` to write any regular file under `/odm/etc/`. On our native tree that prefix holds
`etc/init/gb.rc` (all our root services), `etc/selinux/precompiled_sepolicy` + `odm_sepolicy.cil`,
`etc/build.prop`, `etc/fstab.android-desktop` and VINTF. An `.rc` file is executable config: init runs its
`exec`/`service` lines as root. That breaks the task's rule "nothing executable from the ESP" and the plan's own claim.
**Fixed:** the allowed destinations are now an explicit file-type whitelist: `usr/idc/*.idc`, `usr/keylayout/*.kl`,
`usr/keychars/*.kcm`, `vup/etc/audio/<dir>/*.xml` and the one file `vup/etc/data/hal_configs/audio_default.xml`. Only
regular files are allowed, with a strict name charset, no `..`, depth <= 4, <= 64 files, <= 256 KiB each and <= 4 MiB
in total. Each file is created with `cat > dst; chmod 0644` (never `cp -a`), and the ESP is mounted
`ro,nosuid,nodev,noexec,fmask=0133`. `etc/`, `vup/etc/init`, `vup/etc/vintf`, `vup/apex`, `lib*`, `bin`, `firmware`
and `lib/modules` are explicitly refused.

**W3R-02: the bring-up code could make every native boot hang (item 12, "boot never depends on new pieces").**
(Implementation note in the plan: extend GBINIT's existing `gb.hw=*)` case arm to set `HWTOK`. A separate
`gb.hw=native)` arm placed before it would shadow it, and `HW` would stay empty.)
GBINIT is the `rdinit` and the cmdline has `panic=0`. Busybox ash parses compound commands as it goes, so a syntax
error, a `set -e` exit or a hang (waiting for a disk, a stuck vfat mount) anywhere before `exec /init` means init
exits or never runs. The kernel then panics and stays hung. The plan put the parser inline in GBINIT. **Fixed:** the
logic moves to a separate `/gbbringup.sh` run as `/busybox timeout 40 /busybox sh /gbbringup.sh` with its output to
a log. GBINIT gains only a guarded call plus a lazy `umount` of anything left mounted, and it reaches `exec /init` on
every exit status. TA-6 adds `busybox sh -n` on GBINIT and on the bring-up script, and a test where the script hangs.

**W3R-03: the bring-up disk discovery could not work as specified (item 12, feasibility + trust).**
`CONFIG_DEVTMPFS is not set` (the plan said both "mount devtmpfs" and "it is not set"). At rdinit there is no ueventd,
so the `/dev/block/by-name` resolution "like gb_bootlog" does not exist yet. GBINIT also unmounts `/proc` and `/sys`
before it fills `/odm`. A fallback of "the first EFI-SYSTEM partition found" would let a foreign USB stick feed
config into an internal boot. That is more than the ESP's existing trust: physical access to a USB port is less than
the right to write the ESP. **Fixed:** scan `/sys/class/block`, `mknod` private nodes under `/gbbu/dev`, and read
each candidate disk's GPT with `dd | hexdump`. Use only the disk whose GPT contains the partition GUID from
`androidboot.boot_part_uuid`, and on that disk the partition whose `PARTNAME=EFI-SYSTEM`. No token (a
`--boot-devices` export), no match or more than one match means: skip and log. The wait is bounded, and only when
the gate is on. This is transport-agnostic, so the VMware smoke (NVMe) can test it end to end (R-12b).

**W3R-04: the plan's own YMM fix introduced a new state-corruption bug (item 3).**
Plan 3.1 step 2: "if `ymm` is NULL, set `xfeatures |= XFEATURE_MASK_YMM` and re-fetch; memcpy 16 bytes for dst".
`get_xsave_addr()` returns NULL because XSAVES skipped the init-state YMM component, so the 256-byte YMM area holds
**stale** bytes from an earlier save. Setting the bit and writing one register makes XRSTORS load stale data into the
other 15 upper halves. **Fixed:** never set the YMM bit. If the bit is clear, every upper half is architecturally 0,
and so is the result's upper half, for both L values (d and a have zero upper halves, so every product and sum is 0,
including the saturating forms). Touch the YMM area only when the bit is set: L=1 writes `d[4..7]`, L=0 writes 16
zero bytes. Exact code is in plan 3.1.

**W3R-05: the MAXVL zeroing was missing (item 3, correctness on Ice Lake).**
With AVX-512 enabled (xstate 0x2e7), a VEX.128/VEX.256 destination must also have bits [511:256] zeroed (SDM: "bits
MAXVL-1:128/256 of the destination are zeroed"). Neither patch 0002 nor the plan touched `XFEATURE_ZMM_Hi256`.
Ice Lake userspace with AVX-512 paths, such as crypto or media libraries dispatched by CPUID, can leave stale bits
there. **Fixed:** if the ZMM_Hi256 header bit is set, zero `zmm_hi256 + dst*32` (32 B). If it is clear, do nothing.
Check the header bit first, so `get_xsave_addr` never WARNs. A new KAT case covers it.

**W3R-06: fs-verity in the shared fstab changes the user's live VM `/data` (item 4).**
`FSTAB` (`build_esp.py:50`) feeds both trees (`:1299` VM, `:1509` native), and the VM `gb.rc` bind-mounts it over
`/vendor/etc/fstab`. Contrary to the plan, the flag is **not** a no-op in the VM. The VM kernel is the same GKI with
`CONFIG_FS_VERITY=y` and lapis has tune2fs, so AOSP `tune_verity()` runs `tune2fs -O verity` on **every** mount until
the superblock has the feature. That includes the existing filesystem, not only "after a wipe". The next VM ESP
built from this tree would permanently set RO_COMPAT_VERITY on the user's live VM userdata, which is a VM change
without approval. **Fixed:** build_esp.py derives `FSTAB_NATIVE` from the same source file (append `,fsverity` to the
userdata line) and uses it only for the native tree. The VM tree stays byte-identical, and verify_image asserts both.
The statement "only after a wipe" is corrected: natively it applies once at the next boot, on the existing `/data`.

**W3R-07: a slot-aware `-UpdateEspOnly` could still boot the wrong ramdisk (item 7, "cannot brick").**
The export's `initrd-<slot>.img` is built from **lapis.bin's** `init_boot`/`vendor_boot` of `manifest.slot`. If the
installed slot was OTA-updated on the device (a->b->a, or b refreshed again), `manifest.slot == active slot` still
holds. The rewrite then boots an old vendor and generic ramdisk over a newer system/vendor_dlkm. **Fixed:** before
writing, run `gbdisk verify --plan <plan> --only boot_<s>,init_boot_<s>,vendor_boot_<s>,vbmeta_<s>` against the
export. This is a read-only verb that already exists, so no gbdisk change is needed. Refuse on any sha mismatch,
refuse when `wanted_slot` is ambiguous (mirrored bit-for-bit from gb_slotsync, with shared test vectors), and refuse
when the installed `loader.conf` default disagrees. Also: save the whole installed EFI-SYSTEM raw (512 MiB + sha256)
into the run dir before writing. That gives a rollback image and keeps `gblog`, `gbinstall`, `gb.cfg` and `overlay`.
Dismount any temporary drive letter before `gbdisk write`, and correct the remedy text: `--reuse-guids` alone builds
the same stale slot, so the real remedy is `GB_SLOT=<x>` from a source holding the same build plus the content
check. The recommendation's "Better: replace only vmlinuz + gb/inject.gz" variant is **unsafe**, because
`initrd-<x>.img` embeds `inject.gz`: a new kernel would boot with the old modules. It stays out (documented).

**W3R-08: the gb_slotsync KMI check was infeasible, and its fail-safe claim was false (item 13).**
Reading vermagic needs a `.ko` from the vendor ramdisk, which is LZ4 (`vendor_ramdisk.lz4`, k14 K-06), and
gb_slotsync has no LZ4 or cpio code. It only concatenates. The plan also held the switch **before**
`build_slot()`/`select_entry()`, so no `gb-<x>.conf` entry would exist and "the new slot ... can be chosen manually"
was false. **Fixed:** read the first 64 KiB of `boot_<target>`. That is boot header v4, with the GKI bzImage at
+4096, `HdrS` at +0x202 and a `kernel_version` pointer at +0x20E. I verified this on both lapis slots. Compare the
KMI tuple `<maj>.<min>-android<N>-<gen>` with `uname(2)`. If the tuples differ, HOLD. If `boot_<target>` and
`boot_<cur>` have the same release, SWITCH. If the GKI changed within the same KMI generation, HOLD by default (an
integrator decision; the .271->.276 OTA kept the same GKI, so it would have switched). On HOLD, still build the
initrd and copy the entry, but leave `loader.conf` alone, so the user can pick "(slot B)" once, and existing logic
makes it the default after a good boot. Log once per (target, release) pair. Parse failure means HOLD.

**W3R-09: the i915 white-veil fix shipped with no way to turn it on (item 10).**
(a) `i915.ko` is loaded by GBINIT's `busybox insmod` (`modules.load` line 41). insmod never reads `/proc/cmdline` (only
modprobe does), so "set `i915.gb_ignore_user_gamma=1` in the systemd-boot editor" (plan 2.5, 5.7) does nothing.
(b) In v6.18 display code reads `display->params`, a **copy taken at probe**, so a 0644 runtime sysfs write is also
inert. (c) The param belongs in `display/intel_display_params.{c,h}` (`INTEL_DISPLAY_PARAMS_FOR_EACH`), not
`i915_params.c`. **Fixed:** the param goes in the display params with permission `0400`, read via
`to_intel_display(state)->params`. Activation: GBINIT forwards exactly one whitelisted token,
`i915.gb_ignore_user_gamma=[0-7]`, to i915's insmod arguments (PIPELINE; tested), and/or `GB_MODOPTS_i915` in
gb.cfg. I also made it a bitmask (1 = gamma, 2 = degamma, 4 = CTM; the recommended setting is `3`), because nulling
the CTM also turns off Android's accessibility colour correction and inversion as well as Night Light. `7` reproduces
gbOS86's #29 exactly.

### MEDIUM

**W3R-10: the counter interface (item 3, contract 2.2).** debugfs is not mounted on our Android; the VM scripts mount
it by hand (`build_esp.py:199,435`). So the bootlog's `cat /sys/kernel/debug/gb_vnni/summary` would just fail.
"faults" was undefined and could not be measured: a `copy_from_user` that faults a page in and succeeds is invisible
to the emulator. There was also nothing that proved the IRQ-on path. **Fixed:** the node is sysfs
`/sys/kernel/gb_vnni/summary` (0400) plus `reset` (0200), static attributes on `kernel_kobj`, so no export and
symvers stays the same. The fields are `efault` (failed user copies) and `irqon` (emulations whose user copies ran
with IRQs enabled; must equal `emulated` on k15) plus `zmmhi`/`sse_init`/`ymm_init`. A `printk_ratelimited` line
goes out when a new comm/RIP bucket is inserted; this is the "rate-limited" part. The text is built on read, with no
once-per-second rebuild.

**W3R-11: KAT completeness (item 3).** The plan's SSE-init recipe ("vzeroall/xrstor ... then a VEX op") could pass
on the buggy kernel: the buffer must already hold non-zero stale data, and the observable symptom is the **other**
registers. Added: a stale-buffer recipe with an XINUSE check via `XGETBV(ECX=1)` (SKIP, never PASS, if it cannot be
established); checks that all 16 registers are preserved, for both the YMM and ZMM uppers; the MAXVL case;
multi-process preemption plus SIGALRM-signal stress (2 x nproc workers with distinct patterns); negative encodings
(VEX.W1, and pp != 66 = AVX-VNNI-INT8 forms, which must give SIGILL, not be mis-emulated); an instruction ending at a
page boundary before a PROT_NONE page (the 11-byte overfetch bug); CPUID environment asserts (AVX512_VNNI + AVX512VL
present, AVX_VNNI absent); exact counter deltas; fault proof via `mincore()` + `getrusage` majflt; and KAT-0, a host
unit test of the emulator core (pure C header) including a **negative control** against the k14 logic, which must
fail the 3 defect cases.

**W3R-12: emulator hardening (item 3).** Return false (SIGILL) when `!cpu_feature_enabled(X86_FEATURE_AVX)` or
VEX.W=1 (not AVX-VNNI). Fetch with `insn_fetch_from_user()`, which is sleepable and tolerates short reads, and require
`len <= fetched`. Today's fixed 5+6-byte fetch makes a valid 6-byte instruction right before an unmapped page raise
SIGILL. An invariant is written down: no user access between `kernel_fpu_begin()` and `kernel_fpu_end()`. Optional:
don't emulate inside a uprobe XOL slot; under single-step (TF) keep emulating and document the missing trap rather
than send SIGILL, which would kill a debugged process; add a `user_64bit_mode()` guard (moot with
`ia32_emulation=false`).

**W3R-13: k15 acceptance (3.4).** "K-07 nouveau.ko sha256 identical to k14" fails by construction: vermagic embeds
the new kernel release, and the `.modinfo`, build-id and `.BTF` sections change with it. It is now a source-level
gate (`git diff k14-nouveau k15-nouveau -- drivers/gpu/drm/nouveau` empty) plus an informational compare of
`.text*`/`.rodata*`/`.data`/`__versions`. The numbering collided (3.3 "K-09 stays identical" against the table's
K-09 = i915). K-01..K-09 now keep k14's meanings (K-09 = state restore), and K-10..K-14 are new: i915 param, sysfs
node, repo-patch equivalence (repo 0002/0005 applied to pristine 3938c8d97142 must give byte-identical files), no new
objtool/modpost warnings, and the KAT-0 host result. K-04 now also covers the i915 dependency closure.

**W3R-14: bootlog summary (item 8).** `logcat | grep -c ...` without `-d` streams forever, so the snapshot hangs until
an outer timeout. It is now `logcat -d -b all | grep -c -F`. The summary also reads the sysfs counter, the
gb_slotsync HOLD line (contract 2.6) and `/odm/gbinit_bringup.log`.

**W3R-15: item 18 doc advice contradicted INTERFACES v1.2 section 4.** "Fix: `ro.hwui.use_vulkan=false`" cannot be
recommended: INTERFACES 4 row 279 says the native rc/build.prop MUST NOT set it, and the ANGLE-HWUI guard (14.3)
depends on it. On our stack HWUI must never get NVK (engine rule 14.2). A hit therefore means a shim or ANV bug to
report. It is diagnostic only, and `GB_PROP` refuses the property (it is in `_BAD_PROP`).

**W3R-16: R-11 ran an ISO subset; the task requires the FULL ISO suite.** Changed to FULL (`iso\test\run_all.py`),
always.

**W3R-17: gb.cfg schema defects (item 12).** `GB_PROP` targeted `/odm/build.prop`; the file is
`/odm/etc/build.prop`. The deny list now covers `_BAD_PROP`, the gfx contract (`ro.gfx.*`, `ro.vendor.gb.*`,
`persist.vendor.gb.*`) and security props (`ro.debuggable`, `ro.secure`, `ro.adb.*`, `persist.adb.*`, `service.adb.*`,
`ro.boot.*`, `ro.build.*`, `ro.product.*`, `ro.crypto.*`, `persist.sys.usb.*`, `sys.usb.*`, `ro.zygote*`,
`ro.control_privapp_permissions`), with a value charset. `GB_DISABLE` (an empty rc via bind-mount) was infeasible:
component rc fragments are **appended into the single `gb.rc`**, and bind-mounts made before first-stage init are not
the right tool. It is replaced by `GB_SKIPMOD=<modules.load names>`, which only removes, and rc-level disabling is
deferred. `GB_MODOPTS` needs a charset, because the insmod loop expands `$a` unquoted (globbing). The gate is the
explicit `gb.hw=native` token, not the DMI fallback. `gb.overlay=1` goes only in the one-shot systemd-boot editor or a
user-made separate entry, never the default entry: gb_slotsync's `copy_entry()` copies the default entry's options
verbatim into the next slot. verify_image never sees a live ESP (the ISO's `wholedisk.verify_image` is a different
function), so instead of "allow gb.cfg" it asserts that **build artifacts contain no** `gb/gb.cfg`/`gb/overlay`. A
kmsg line at rdinit needs a private `/dev/kmsg` node.

**W3R-18: the split was not quite disjoint.** (a) `docs/INSTALLER-ISO.md` was owned twice (PIPELINE row and AUX
"all repo docs/*"); it now goes to PIPELINE only. (b) The V16 change breaks the existing
`tests/w2b/test_verify_image.py` native fixture, which has no `8250.nr_uarts`, and `tests\w2a`/`w2b` were in nobody's
column; they are now PIPELINE's (after the wave 2b gate). (c) Item 13's "add a line to the gb_bootlog summary" is a
PIPELINE file; it is now contract 2.6 with a literal string. (d) The KAT binary's drop point was undefined; it is now
contract 2.7 (`C:\GB-x86\inject\kat\`). zram/zsmalloc come from the already-staged native module set
(`stage_kernel.py:30`), so they need no second drop. (e) `gb_dgpu_report.sh` (item 18, AUX) is an ESP-lock holder that wave 2b
is touching; it is added to the R-01 rebase list. (f) Repo copies are produced only by the R-15 collect, so no two
implementers write the same repo file. (g) The GBINIT ESP read is an exception to INTERFACES 5.4 ("every ESP mount
inside the lock"; it is single-process at rdinit). That spec change is beyond the implementers' files and is flagged
for the integrator.

**W3R-19: item 1 details.** The value choice is now justified: v6.18 grows the table on demand, so the only functional
requirement is >= 1. `4` is the Kconfig default, the VM's value and gbOS86's SL7 fix. Note: with 4 legacy slots
holding iobases, the SAM UART reuses a `PORT_UNKNOWN` slot (`8250_core.c:657-660`), probably `ttyS0` on SB3, not
`ttyS4` as on distro kernels, which harms nothing. V16 also asserts the VM entry is unchanged
(`8250.nr_uarts=4 console=ttyS0`). R-12 checks `Serial: 8250/16550 driver, 4 ports` in the native dmesg; VMware has
a real 0x3f8 UART, so this proves the token works natively.

### LOW

* **W3R-20** item 14: `ibt-19-*` is hw_variant **0x13 (HrP)**, written in decimal by `%u`, not "0x19 (Slr-F)". The
  conclusion (AX201 uses the legacy-name family) stands.
* **W3R-21** item 11: the text contradicted itself ("also add ... no -- simplest ..."). It is now final: `on init`
  `setprop desktop.apgetty.enable 0` + `on boot` `stop apgetty`. The implementer pastes the real `apgetty.rc` stanza
  into the commit and confirms the service is `disabled` and started only by the property trigger. If it is not, the
  fallback is `on property:init.svc.apgetty=running` / `=restarting` -> `stop apgetty`. gbOS86's empty-file bind over
  `apgetty.rc` is not available to us: init parses every rc file before any action runs, and GBINIT runs before
  first-stage init mounts `/system_ext`. R-12 checks `init.svc.apgetty` is not running. VMware has a real ttyS0, so
  the smoke proves the disable.
* **W3R-22** R-08 tree-diff expectation was incomplete. The k15 restage changes every `.ko` and the `modules.load`
  header, and items 9, 10 (gbinit forwarder) and 13 (`gb_slotsync` binary) also change the tree.
* **W3R-23** item 2 rationale: the speculative "card->name = hda-dsp ... derives sofhdadsp" text is replaced by the
  evidence argument. gbOS86 saw "HDA Intel PCH" fail and "PCH" (the id) work, which refutes exact short-name matching.
  The two remaining rules consistent with lapis shipping `sof-soundwire` (exact id, or the name normalised the ALSA-id
  way) both accept `sofhdadsp`. The value is unchanged.
* **W3R-24** the throwaway VM folder `D:\gb-w2test\vm` no longer exists; R-12/R-13 recreate it with
  `C:\GB-build\native\dgpu\1b\evidence\w2\tools\mkvm.ps1` + `vmguard.py` (no CPUID masks in gbw2.vmx).
* **W3R-25** added R-12b, an end-to-end bring-up test on a throwaway copy: an allowed prop, an allowed `.idc`, a
  forbidden `etc/init/x.rc` and a forbidden prop. Only the allowed ones may appear, and the log lists the refusals.
* **W3R-26** builder `/build` is at 90 % (12 GB free). That is enough for kbuild15's 10 GiB floor, but record `df`
  after R-04 and keep `/build` >= 5 GB before the fw_coverage extraction.
* **W3R-27** item 7 "Better" variant documented as unsafe (see W3R-07).
* Text clean-ups: 3.5 (the "-nostartfiles? no --" aside), 5.3 (the "tag=gb_dgpu? no --" aside), 4.4.

## 2. Safety properties after the corrections

| Property asked for | Status |
|---|---|
| Boot never depends on new pieces | k15 is on the path, but it is covered by the KAT and VM-1/VM-2, and `prev\` media are the rollback. `8250.nr_uarts` is a kernel token. The bring-up is default-off, isolated in a timed subshell, and `exec /init` is guaranteed (W3R-02). fsverity failures only log. The i915 param is default 0 (pure `unlikely()` guard). apgetty-off and DRAS/IDC edits are data. gb_slotsync can only hold back. |
| ESP config cannot exceed the ESP's existing trust | Secure Boot is required OFF (`install_dualboot.ps1` step 1), so whoever writes the ESP already controls vmlinuz and the initrd. After W3R-01/03/17 the bring-up reads only **this boot's** ESP (GPT GUID binding), executes nothing, never touches rc/policy/VINTF/libs/firmware/modules, and is opt-in per boot. If Secure Boot + signed UKIs are ever adopted, gb.cfg becomes unsigned input and must be dropped or signed: noted in plan 2.3. |
| Emulator correct under v6.18 IRQ/preemption/FPU rules | User copies run IRQs-on and outside the FPU section. The FPU read-modify-write sits inside `kernel_fpu_begin/end`, which with IRQs on holds `fpregs_lock()` (bh off, so no preemption and no softirq FPU), saves live regs unless `TIF_NEED_FPU_LOAD` is set, and forces a restore on exit. Init-state handling for SSE/YMM/ZMM_Hi256 is correct and never commits stale bytes. IRQs are disabled again before `irqentry_exit`. |
| 8250 change cannot break the VM or the installer | VM and `--vm-test` use `be.CMDLINE` (unchanged); the installer uses Ubuntu's RUNTIME_UARTS=32; V16 asserts the VM entry is unchanged; t0_static asserts the installer config. |
| Slot-aware update cannot brick | It refuses on slot mismatch, ambiguity, loader-default disagreement or **content mismatch** (W3R-07); it writes only after a raw ESP backup (rollback); the unsafe partial-file variant is excluded. |

## 3. Residual risks (accepted, recorded in the plan)

* DRAS's exact field set is still INFERRED (strings, not source); confirm it from first-boot DRAS logcat.
* i915 white-veil applicability to the SB3 panel is INFERRED; the fix is default-off.
* The i915 patch's default path cannot be exercised before hardware (VMware has no i915). Keep the diff a pure guard
  of <= 25 lines, reviewed against K-12 equivalence.
* XINUSE tracking for SSE is implementation-defined. The KAT reports SKIP for the SSE-init case if `XGETBV(1)` cannot
  show init state; KAT-0 (host, fake xsave buffer) still covers the logic deterministically.
* The item 13 HOLD policy for "same KMI generation, new GKI release" is conservative, and it is an integrator choice
  (plan 5.3).
* The INTERFACES 5.4 exception for the rdinit read-only ESP mount needs the integrator's sign-off.

## 4. Where each correction landed in WAVE3_PLAN.md

| Id | Plan section(s) |
|---|---|
| W3R-01, 02, 03, 17 | 2.3 (rewritten), 4.5 (rewritten), 4.8 TA-6, 5.7 table row, 6 R-06/R-12b |
| W3R-04, 05, 12 | 0.2 (review additions), 3.1 steps 1-5 (code), constraints |
| W3R-06 | 1 table (PIPELINE row), 4.2 (rewritten), 4.8 TA-5/TA-9, 6 R-06/R-12 |
| W3R-07, 27 | 5.4 (content check, raw backup, unsafe variant, tests) |
| W3R-08 | 5.3 (rewritten), 2.6 |
| W3R-09 | 1 table, 2.5 (rewritten), 3.2, 4.6 forwarder, 4.8 TA-8, 5.7 table row |
| W3R-10 | 0.2, 2.2 (rewritten), 3.1 step 4, 3.5, 4.3, 6.4 |
| W3R-11 | 3.1 step 6, 3.5 (env asserts, stale-buffer recipe, preservation, stress, negatives, KAT-0), 6.4 pass criteria |
| W3R-13 | 3.3, 3.4 (table rewritten), 6 R-04 |
| W3R-14 | 4.3, 4.8 TA-10, 5.6 |
| W3R-15 | 5.6 |
| W3R-16 | 6 R-11 (FULL), order note after R-14 |
| W3R-18 | 1 table + repo-copy rule, 2.6, 2.7, 4.1 (w2b fixture), 4.8 TA-0, 9 re-sync list, 11 |
| W3R-19 | 0.1 (value justification), 4.1 V16 VM assert, 4.8 TA-1, 6 R-12 |
| W3R-20 | 5.5 |
| W3R-21 | 4.4 (rewritten), 6 R-12 |
| W3R-22 | 6 R-08, 7 |
| W3R-23 | 5.1 |
| W3R-24 | 3.5, 6 R-00/R-12, 6.4 |
| W3R-25 | 6 R-12b |
| W3R-26 | 3.3, 8 |

Reviewer's own reads were all read-only: builder `/build/gki/common` + `/build/dist14` + `/boot/config-6.8.0-147-generic`
over ssh; the local pipeline, dual-boot, slotsync, audio and IDC files; the gbos-x86 clone (data only); and the GPT
plus the first 4 KiB + 0x400 of `boot_a`/`boot_b` in `C:\GB-x86\lapis.bin`, read while `vmrun list` showed only
`builder2.vmx` running (the user's VM was off). No file other than WAVE3_PLAN.md and WAVE3_REVIEW.md was written,
except two helper scripts in the session scratchpad, which were deleted afterwards.
