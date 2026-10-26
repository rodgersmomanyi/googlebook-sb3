> **Repository note.** The executed wave 3 plan (and its review), copied from the development tree. Paths
> such as `C:\GB-build\...` and `C:\GB-x86\...` are the development layout (`C:\GB-build\native\<x>` =
> `native/<x>`, `C:\GB-x86` = the repository root, `builder:` = the Linux builder); `evidence/` and the
> planning session's own files are not published. What wave 3 changed for a user: the README, docs/ and
> [kernel/K15.md](../../../kernel/K15.md).

# Wave 3 plan (gbOS86 cross-project recommendations 1-18, user-approved subset)

Status: READ-ONLY while wave 2b finishes its adversarial review of the lock fixes. This file and anything else under
`C:\GB-build\native\dgpu\w3\` are the only things wave 3 may create now. No source is edited until wave 2b has
published its outputs and the integrator opens the R-02 gate (section 11).

**Review status (2026-10-26):** adversarially reviewed -> `C:\GB-build\native\dgpu\w3\WAVE3_REVIEW.md`. Every
correction is applied in place below and tagged **`[W3R-nn]`** (grep `W3R-`); the review explains each one. Where a
tagged paragraph and older text disagree, the tagged text wins.

Source of the recommendations: the last `result` line of
`<planning session: the gbOS86 comparison result>`
(`recommendations`, `things_we_do_better`, `collaboration_ideas`), itself derived read-only from the gbos-x86 clone at
`<a local clone of github.com/linuxkid473/gbos-x86>`
(HEAD `f8a5c1a`). Credit gbOS86 in every comment/doc the items touch; copy no code (licence rule below).

Licence rule (re-confirmed from the clone this session): gbos-x86 has **no top-level LICENSE** (GitHub API
`license: null`). Only 8 files are SPDX GPL-2.0 (isa-emu/*, kernel/isa_emu_patch/{isa_emu.c,isa_emu.h,isa_emu_core.c,
isa_emu_core.h}, kernel/ashmem_shim/memfd-ashmem-shim.c, isa-emu/test_core.c) and `gatekeeper/soft_gk.c` is Apache-2.0.
`traps_irq.sed`, `xstate_tail.c`, `core_tail.c`, the `i915_*.sh` scripts, `hook.sh`, `hwdetect.sh`, `install.sh`,
`tools/*.py` and everything else carry **no licence** = all rights reserved: ideas only, reimplement, credit by URL and
`@f8a5c1a`. None of the wave 3 items below copies gbos-x86 code; every one is our own code or docs with a credit line.

## 0. Re-verification done this session (do not trust the upstream result blindly)

I re-read the v6.18 sources on the builder (`/build/gki/common` at `3938c8d97142`, the k13/k14 base) and our own files.

### 0.1 Recommendation 1 (8250.nr_uarts) -- CONFIRMED, with the exact mechanism

* `drivers/tty/serial/8250/8250_platform.c@3938c8d97142`: `unsigned int nr_uarts = CONFIG_SERIAL_8250_RUNTIME_UARTS;`
  and `serial8250_init()` does `if (nr_uarts == 0) return -ENODEV;` **before** `uart_register_driver(&serial8250_reg)`.
* Our k14 config (`/build/dist14/kernel_x86_64_dot_config`): `CONFIG_SERIAL_8250=y`, `CONFIG_SERIAL_8250_LPSS=y`,
  `CONFIG_SERIAL_8250_NR_UARTS=32`, **`CONFIG_SERIAL_8250_RUNTIME_UARTS=0`**, `CONFIG_SERIAL_DEV_BUS=y`,
  `CONFIG_SURFACE_AGGREGATOR=m` + `_HUB/_REGISTRY/_CDEV`, `CONFIG_PINCTRL_ICELAKE=m`, `CONFIG_MFD_INTEL_LPSS=m`,
  `CONFIG_MFD_INTEL_LPSS_PCI=m`. Lapis's `lapis_kernel.config` has the same `RUNTIME_UARTS=0`.
* So with `nr_uarts=0` and no `8250.nr_uarts=` on the cmdline, `serial8250_init()` returns `-ENODEV`, the
  `serial8250_reg` uart_driver is never registered (`drv->state == NULL`, `drv->nr == 0`). When lapis's first-stage
  `8250_dw`/`8250_lpss` then calls `serial8250_register_8250_port()` -> `serial_core_add_one_port()`, the guard
  `if (uport->line >= drv->nr)` (line 0 >= 0) returns **`-EINVAL`** = the "`dw-apb-uart ... probe ... -22`" gbOS86 saw.
  The LPSS UART that carries the Surface Aggregator link never comes up -> no base keyboard/touchpad/batteries/charger/
  detach. `register_8250_port` does grow `nr_uarts` on demand, but only after the driver is registered, which never
  happens when it starts at 0.
* The VM path works only because `build_esp.CMDLINE` carries `8250.nr_uarts=4`: `serial0.log` shows
  `Serial: 8250/16550 driver, 4 ports` and `00:04: ttyS0 at I/O 0x3f8 ... 16550A`. `build_usb.native_cmdline()` DROPS
  `8250.nr_uarts` (drop tuple at `build_usb.py:82`) and its docstring (`:72-73`) names the Surface Aggregator as the
  reason -- backwards. **Confirmed bug.**
* **[W3R-19] Value = 4, justified.** v6.18 `serial8250_register_8250_port()` grows the table past `nr_uarts` on demand
  (`8250_core.c:695-708`, up to `UART_NR`=32) once the driver is registered, so the functional requirement is only
  `>= 1`. `4` is the Kconfig default, the VM's value (`build_esp.CMDLINE`) and gbOS86's Surface Laptop 7 fix
  (`RUNTIME_UARTS` -> 4). linux-surface needs no token because distro kernels ship RUNTIME_UARTS >= 4 (Ubuntu 32).
  Side effect (harmless): the 4 legacy ISA slots carry iobases, so the SAM UART reuses the first `PORT_UNKNOWN` slot
  (`8250_core.c:657-660`) and will probably be `ttyS0` on the SB3 (distro kernels: `ttyS4`); serdev owns it, so no
  `/dev/ttyS0` char device appears for it. The param name is `8250.` (`8250/Makefile:6-8`, built-in).
  The SB3 keyboard/touchpad **are** SAM HID (`surface_hid`, see the touchpad IDC header), so this is P1.
* Dual-boot export: `native/dualboot/build_internal.py` calls `bu.native_cmdline(...)`, so it inherits the fix the
  moment `build_usb.py` is fixed. No separate edit needed there (but its `manifest.json` `cmdline` must then show
  `8250.nr_uarts`, checked by cross-check 8 and the installer's print).
* ISO installer kernel: the installer boots **Ubuntu 6.8.0-147-generic**, whose `/boot/config-6.8.0-147-generic` has
  `CONFIG_SERIAL_8250_RUNTIME_UARTS=32` and `CONFIG_SERIAL_8250_DW=m`. Its `grub.cfg` `gb_args` has no `8250.nr_uarts`.
  Because its runtime default is already 32, the 8250 driver registers and `8250_dw` binds the LPSS UART with no
  cmdline help. **The installer does NOT need `8250.nr_uarts`.** (The installer only uses the base keyboard; the
  Googlebook kernel it writes is the native GKI, which is fixed by the build_usb change.) A defensive static assertion
  is added (item 1 PIPELINE) so a future installer-kernel swap with `RUNTIME_UARTS=0` is caught.

### 0.2 Recommendation 3 (#UD VNNI emulator) -- all three defects CONFIRMED in our patch + v6.18 traps.c

Our emulator is in `/build/gki/common/arch/x86/kernel/traps.c` as uncommitted working-tree changes (part of the
k13-native snapshot, carried into k14), generated by `kernel/scripts/apply_vnni3.py`; the canonical form is
`kernel/patches/0002-x86-traps-emulate-VEX-AVX-VNNI-for-the-Googlebook-userspace.patch`.

* **IRQs off.** `DEFINE_IDTENTRY_RAW(exc_invalid_op)` -> `irqentry_enter()` -> `handle_invalid_op()`; `gb_emulate_vnni()`
  runs first, before any `cond_local_irq_enable`. On an exception IDT (interrupt gate) IRQs are hardware-disabled on
  entry. The kernel's own `do_error_trap()` (traps.c:259) does `cond_local_irq_enable(regs)` before `do_trap`, and
  `exc_general_protection` enables IRQs before `fixup_umip_exception`. Our emulator calls `copy_from_user()` of the
  instruction (patch :75) and of the memory operand (:106, :135) with IRQs disabled -- a fault there (a not-present
  page: fresh/zram/under mmap contention) enters the page-fault path in atomic context. `cond_local_irq_enable` is
  exactly `arch/x86/include/asm/traps.h:48` (`if (regs->flags & X86_EFLAGS_IF) local_irq_enable();`). `CONFIG_DEBUG_ATOMIC_SLEEP`
  is **off** in our config, so nothing warns today. **Confirmed; fix with the kernel's own idiom.**
* **VEX.128 upper-half not zeroed.** For `L=0` the patch writes only `xmm[dst]` (16 B) and only writes the YMM upper
  half when `L` is set (patch :161-166). AVX requires a VEX.128 destination to zero bits [255:128]. A stale upper half
  survives. **Confirmed.**
* **XSAVE SSE init state.** The patch reads `...xsave.i387.xmm_space` directly and later OR-s `xfeatures |= 1<<1`
  (patch :140, :164). `get_xsave_addr()` (fpu/xstate.c:1040) and `__raw_xsave_addr` document the init optimisation:
  if the SSE bit was clear, the buffer bytes are stale; setting the bit commits garbage into all 16 XMM registers.
  Our guest uses the **compacted** format (`serial0.log`: "Enabled xstate features 0x2e7 ... using 'compacted'
  format"), and `i387.xmm_space` is the legacy area at a fixed offset (safe to read), but the init-state issue stands.
  **Confirmed.**
* Trigger on our system is narrower than gbOS86's (we have no always-on profiler; only the memory-operand fault). Still
  P1: the fix is small and local. `insn_fetch_from_user`/`get_xsave_addr` are present; `CONFIG_DEBUG_FS=y` so a debugfs
  trap counter is available with no config change (superseded: the counter is sysfs, see [W3R-10] and 2.2).
* **[W3R-04/05/12] Review additions (re-verified in v6.18):** (a) the patch already does all three user copies
  *before* `kernel_fpu_begin()`; with IRQs on, `kernel_fpu_begin_mask()` (`fpu/core.c:530-546`) takes
  `fpregs_lock()` (= `local_bh_disable`, so no preemption, no softirq FPU), saves the live user regs unless
  `TIF_NEED_FPU_LOAD` is already set (e.g. after sleeping in a fault), and forces a restore on return -- keep that
  structure and make "no user access inside the FPU section" an invariant; (b) **MAXVL**: xstate 0x2e7 = AVX-512 on, so
  VEX.128/256 must also zero bits [511:256] of the destination (`XFEATURE_ZMM_Hi256`) -- missing from the patch and the
  original plan; (c) `get_xsave_addr()` `WARN_ON_ONCE`s for a feature that is not enabled, so test the header bit first;
  (d) the instruction fetch is a fixed 5+6 bytes, so a valid 6-byte form right before an unmapped page is refused
  (SIGILL); `insn_fetch_from_user()` (`insn-eval.c:1508`, sleepable, short reads OK) fixes it; (e) VEX.W=1 is not
  AVX-VNNI but is emulated today. debugfs is **not mounted** on our Android (the VM scripts mount it by hand,
  `build_esp.py:199,435`), hence the sysfs counter in 2.2.

Everything below is written to these confirmed facts.

---

## 1. Scope, priority, and the three disjoint implementers

In scope (user-approved): recommendations **1, 2, 3, 4, 7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18**.
Out of scope now: **5** (VM zram -- user OK needed; the KAT's throwaway image may load zram as a *test artifact* only,
section 6.4), **6** (libz patcher -- measure with the new counter first), **15** (IFMA -- measure first), **19**
(software Gatekeeper -- user decision). VM fstab fs-verity (part of item 4) is the user's **live VM** and is **not**
changed; a note is added instead (item 4).

Three implementers, **disjoint file sets** (no file is edited by two). They code to the contracts in section 2.

| Implementer | Owns (edits only these) | Items |
|---|---|---|
| **KERNEL** | `/build/gki/common` working tree via patches only (`arch/x86/kernel/traps.c`, optional new `arch/x86/kernel/gb_vnni_core.h` [W3R-11], `drivers/gpu/drm/i915/display/intel_display.c`, `.../display/intel_display_params.{c,h}` [W3R-09]); `C:\GB-build\kernel\kbuild15_native.sh` + `patches\k15\` + tools; repo `kernel/patches/0002-*.patch`, new `kernel/patches/0005-i915-*.patch`, `kernel/scripts/apply_vnni4.py`, `kernel/README.md`, `kernel/K15.md`; the KAT source `kernel/tools/vnni_kat.c` + KAT-0 host test + host harness; the drop dir `C:\GB-x86\inject\kat\` (contract 2.7) | 3, 10 (kernel part), k15 |
| **PIPELINE** | `C:\GB-x86\{build_esp.py,build_usb.py,verify_image.py,stage_kernel.py}`; `inject\fstab.android-desktop` (read-only now: the native variant is derived in build_esp.py [W3R-06]); `C:\GB-x86\iso\` (DESIGN.md, build_iso.py, test/*, build/builder/*) + repo `docs/INSTALLER-ISO.md` (**PIPELINE only** [W3R-18]); `C:\GB-x86\tests\w3a\` (new) **and** `C:\GB-x86\tests\w2a\`, `tests\w2b\` (fixtures that the V16 change breaks [W3R-18]; after the wave 2b gate) | 1, 4 (native fstab), 8, 10 (bootlog grep + the GBINIT param forwarder [W3R-09]), 11, 12 (gbinit + bring-up script + verify_image + gb.cfg schema owner), 18 (bootlog grep) |
| **AUX** (DUALBOOT+SLOTSYNC+FIRMWARE+DOCS+BRINGUP) | `C:\GB-build\native\dualboot\{install_dualboot.ps1,README_DUALBOOT.md,test\*}` (+ `gb_common.ps1` if a helper is needed; `gbdisk.cs` only if the optional `dump` verb is chosen, see 5.4); `C:\GB-build\mouse\gb_slotsync.c` + `guest/test`; the binary drop `C:\GB-x86\inject\slotsync\gb_slotsync`; firmware tool `C:\GB-build\native\nouveau-kernel\tools\fw_coverage.py` (new); `C:\GB-build\native\audio\tree\...\audio_default.xml` + `audio_config.xml` (sof-hda-dsp_sb3, PCH_sb3_legacy) + `native/audio/test/run_tests.sh`; `C:\GB-build\native\hw-gap-audit\tree\usr\idc\...Touchpad.idc` + `test\check_input_files.py`; `C:\GB-build\native\nouveau-kernel\native\gb_dgpu_report.sh` (item 18; **the NVK self-test report section of that file**, not a second script); all repo `docs/*` **except `docs/INSTALLER-ISO.md`**, `README.md`, `LICENSES.md`, `.github/` (new); the R-15 collect run | 2, 7, 9, 13, 14, 16, 17, 18 |

[W3R-18] Repo copies of build files (`build_esp.py`, `verify_image.py`, `guest/gb_slotsync.c`, ...) are **not** edited by
hand. Each implementer edits the build copy only, and the R-15 collect (AUX, after R-14) publishes them, so two
implementers never write the same repo file. Repo-only files (`kernel/patches/*`, `kernel/*.md`, `docs/*`,
`.github/*`) are edited directly by their owner. `native/audio/test/run_tests.sh` exists in two differing copies: AUX
edits both, and R-15 must not overwrite the repo copy (check collect.py's mapping first).

Note on item 18 and build.prop paths: the HWUI-on-Vulkan grep has two homes -- the **NVK self-test report** (AUX, in
the nouveau-kernel/nvk tree) and `gb_bootlog`'s snapshot (PIPELINE, build_esp.py). Each adds its own grep to its own
file; both match the same literal signature string defined in the contract (2.4). No shared file.

Priority: items **1, 2, 3** are P1 (before the first native boot). **4, 7, 8** P2. **9, 10, 11, 12, 13, 14, 16, 17, 18**
P3. The build is gated on 1/2/3 landing; the rest ride the same rebuild.

---

## 2. Contracts between the implementers (edit only your own files; agree on these)

### 2.1 k15 kernel release (KERNEL -> PIPELINE)
KERNEL writes `builder:/build/dist15/kernel_release.txt` and `C:\GB-build\kernel\k15_release.txt` (one line, no trailing
space), and the bzImage at `builder:/build/dist15/bzImage`. PIPELINE's `stage_kernel.py k15 --dist /build/dist15`
stages it exactly as it staged k14. The kernel release string MUST differ from k14's
`6.18.21-android17-5-g9629ced3e50e-ab16302714`.

### 2.2 #UD trap counter -- **sysfs** (KERNEL -> PIPELINE, item 3) [W3R-10]
~~debugfs `/sys/kernel/debug/gb_vnni/summary`~~ -- replaced: debugfs is not mounted on our Android (the VM scripts mount
it by hand), so every reader would need a mount and the SELinux debugfs access. KERNEL exposes instead:
* **`/sys/kernel/gb_vnni/summary`** (0400, root): static `kobj_attribute`s on a kobject created with
  `kobject_create_and_add("gb_vnni", kernel_kobj)` in a `late_initcall`, so no new export and `vmlinux.symvers` stays
  the same. Line 1 (fixed field order, all decimal):
  `emulated <N> vex128 <N> vex256 <N> word <N> byte <N> irqon <N> efault <N> sse_init <N> ymm_init <N> zmmhi <N>`.
  `irqon` = emulations whose user copies ran with IRQs enabled (must equal `emulated` on k15); `efault` = failed
  instruction/operand copies (they end in a signal); `sse_init`/`ymm_init` = emulations that found that component in
  its init state; `zmmhi` = emulations that zeroed a ZMM_Hi256 slot. Then up to 16 lines
  `comm=<comm> rip=<hex> count=<N>` from a fixed 16-entry LRU (newest wins). The text is built on read, under the
  spinlock, into the sysfs page (< 2 KiB).
* **`/sys/kernel/gb_vnni/reset`** (0200): writing `1` clears everything.
* Rate limit: when a new comm/RIP bucket is inserted, one `printk_ratelimited(KERN_INFO "gb_vnni: emulating
  VEX AVX-VNNI for comm=%s\n")` (no RIP in kmsg). The hot path only bumps counters under the spinlock, outside the
  FPU section.
PIPELINE's `gb_bootlog` summary (item 8) runs `timeout 5 cat /sys/kernel/gb_vnni/summary` (root) and prints it.
The RIPs are user addresses of one past boot (ASLR rerandomises); the issue template's redaction note covers sharing.

### 2.3 ESP bring-up config schema (PIPELINE owns the parser, AUX's gb_slotsync preserves the files, item 12) [W3R-01/02/03/17]
**Trust statement.** Secure Boot must be OFF for this project (`install_dualboot.ps1` step 1), so whoever can write
this boot's ESP already controls vmlinuz and the initrd. The bring-up must not add anything beyond that. It reads only
**this boot's** ESP (GPT GUID binding below, so a foreign USB stick is never read), executes nothing, and never touches
init rc files, SELinux policy, VINTF, libraries, firmware or modules. If signed UKIs or Secure Boot are ever adopted,
`gb.cfg` becomes unsigned input and must be dropped or signed.

**Gate / kill switch.** The feature runs only when `/proc/cmdline` carries the explicit token **`gb.hw=native`** (not
the DMI fallback) **and** **`gb.overlay=1`**. The default is off. `gb.overlay=1` is never written into a shipped entry.
The user adds it for one boot in the systemd-boot editor (`editor yes`, which V2 requires), or in a separate entry file
they create (`gb-<x>-bringup.conf`), **never in the default entry**: gb_slotsync's `copy_entry()` copies the default
entry's options verbatim into the next slot's entry. Recovery from a bad gb.cfg = reboot and pick the normal entry,
or rename `gb\gb.cfg` from Windows. Only `gb/gb.cfg` whose first line is exactly `# gb.cfg v1` is considered.

**Where it runs.** In a separate script `/gbbringup.sh` (packed in the native cpio), started by GBINIT as
`/busybox timeout 40 /busybox sh /gbbringup.sh >> /odm/gbinit_bringup.log 2>&1` after `cp -a $SRC/. /odm/` and before
the modules loop. GBINIT continues to `exec /init` on **every** exit status (timeout, syntax error, failure) and first
lazily unmounts `/gbbu/esp`, `/gbbu/proc` and `/gbbu/sys` (W3R-02). The script mounts its own `proc`/`sysfs` under
`/gbbu/`, and no `/dev` changes are visible to init.

**Disk binding (W3R-03).** There is no devtmpfs (`CONFIG_DEVTMPFS` not set) and no ueventd/by-name at rdinit. Wait
(bounded: 10 s, 0.5 s polls) until a disk appears in `/sys/class/block`. For each whole disk, `mknod` a private node
`/gbbu/dev/<k>` from `/sys/class/block/<k>/dev`, read the GPT header (LBA 1) for the entries LBA and count with
`dd | hexdump`, and look for the partition GUID from `androidboot.boot_part_uuid` (bytes_le). Exactly one matching disk
-> its partition whose `/sys/class/block/<p>/uevent` says `PARTNAME=EFI-SYSTEM`. No token (a `--boot-devices` export),
no match or more than one match -> skip with a log line. Mount it `-t vfat -o
ro,nosuid,nodev,noexec,uid=0,gid=0,fmask=0133,dmask=0022` on `/gbbu/esp`. A missing disk never stalls the boot beyond
the bounded wait. Logging goes to `/odm/gbinit_bringup.log` (gb_bootlog prints it); there is no `/dev/kmsg` here,
unless the script `mknod`s a private `c 1 11` node.

**Format.** `KEY=VALUE`, one per line, `#` comments, ASCII printable only, <= 4 KiB, <= 64 lines. A violation
anywhere -> nothing is applied (all or nothing) plus one log line. Parsed with busybox ash only: `while IFS= read -r`,
`case`, `${v%%=*}`/`${v#*=}`; no `eval`, no `. file`, no command substitution of file content, no `grep -P`/arrays/
`$'...'`. Values are written with `printf '%s\n'`, never `echo`. **Strict whitelist of keys** (anything else is logged
and ignored):
* `GB_MODOPTS_<mod>=<args>`: `<mod>` matches `[a-z0-9_]+` and must equal (with `-` == `_`) the basename of a line in
  `/odm/lib/modules/modules.load`. `<args>` = 1-8 space-separated `name=value` tokens, each matching
  `[a-z0-9_]+=[A-Za-z0-9_.,:-]+`, <= 200 chars in total (no glob or shell metacharacters: the insmod loop expands `$a`
  unquoted). The tokens are appended to that module's line in the tmpfs copy of `modules.load`.
* `GB_SKIPMOD=<comma list of modules.load names>` (same charset): those lines are commented out in the tmpfs copy. It
  only removes, and it replaces the former `GB_DISABLE`, which was infeasible: component rc fragments are **appended
  into the single `gb.rc`**, so there is no per-component rc file to blank. rc-level disabling is deferred.
* `GB_PROP_<n>=<prop>=<value>` (`<n>` = 1..16): one line `<prop>=<value>` appended to **`/odm/etc/build.prop`** (the
  native build.prop; later lines win). `<prop>` must match `^(ro|persist|vendor|debug)\.[a-z0-9_.]+$` and must **not**
  start with anything in `_BAD_PROP` (`build_esp.py:1450`), the gfx contract (`ro.gfx.`, `ro.vendor.gb.`,
  `persist.vendor.gb.`) or the security list (`ro.debuggable`, `ro.secure`, `ro.adb.`, `persist.adb.`,
  `service.adb.`, `ro.boot.`, `ro.build.`, `ro.product.`, `ro.crypto.`, `persist.sys.usb.`, `sys.usb.`, `ro.zygote`,
  `ro.control_privapp_permissions`). `<value>` matches `[A-Za-z0-9_.,:/+=-]{0,91}`.

**Overlay (W3R-01).** `gb/overlay/<dest>` is copied over `/odm/<dest>` only when `<dest>` matches exactly one of:
`usr/idc/<name>.idc`, `usr/keylayout/<name>.kl`, `usr/keychars/<name>.kcm`, `vup/etc/audio/<dir>/<name>.xml`, or the
single file `vup/etc/data/hal_configs/audio_default.xml`. `<name>`/`<dir>` match `[A-Za-z0-9_.-]+` and are not `.` or
`..`. Only regular files; <= 64 files, <= 256 KiB each, <= 4 MiB in total. Each file is created with
`cat src > dst; chmod 0644 dst` (root-owned tmpfs; never `cp -a`, never a mode from the ESP). **Refused, with a log
line**: everything else, explicitly including `etc/` (`etc/init/gb.rc`, `etc/selinux/*`, `etc/build.prop`,
`etc/fstab.*`, `etc/vintf`), `vup/etc/init`, `vup/etc/vintf`, `vup/apex`, `lib*/`, `bin/`, `firmware/`,
`lib/modules/`. Nothing from the ESP is executable or executed.

**ESP lock.** This read-only, single-process mount at rdinit is an exception to INTERFACES 5.4 ("every ESP mount
inside the lock"). The integrator records it in INTERFACES (that spec file is outside all three implementers' sets).

**Files.** PIPELINE: verify_image asserts that build artifacts contain **no** `gb/gb.cfg` and no `gb/overlay/` (the
feature is opt-in; verify_image never sees a live ESP, so no "allowance" is needed), that GBINIT carries the gate,
the timed call and `exec /init` as its last line, and that `/gbbringup.sh` is present in the native cpio only. AUX:
gb_slotsync writes only `gb/slot_*.stamp`, `initrd-*.img`, `loader/entries/gb-<x>[-nvtest].conf` and `loader.conf`
(it only reads `gb/inject.gz` and `gb/bootconfig_extra`; `gb_slotsync.c:271-330,382-450`), so it needs no code change, only a test asserting `gb/gb.cfg`
and `gb/overlay/x` survive a slot switch untouched. `-UpdateEspOnly` erases them (whole-partition write), so 5.4
backs them up.

### 2.4 HWUI-on-Vulkan abort signature (shared literal, item 18)
Exact grep string (fixed-string, both greps use it verbatim):
`queueFamilyProperties.queueCount < kRequestedQueueCount`. Credit `gbos-x86/usb/hook.sh:489-492`.

### 2.5 i915 gamma modparam (KERNEL -> docs/PIPELINE, item 10) [W3R-09]
Module parameter **`i915.gb_ignore_user_gamma`** (int bitmask, default **0** = no change, perm **0400**): bit 0 =
gamma LUT, bit 1 = degamma LUT, bit 2 = CTM. A set bit makes `intel_crtc_copy_uapi_to_hw_state_nomodeset()` use NULL
(linear) for that blob instead of the uapi blob. **3** is the recommended white-veil setting: Night Light stops, but
CTM-based accessibility colour correction keeps working. **7** = gbOS86 kernel #29 exactly. It is a *display* param
(v6.18): `INTEL_DISPLAY_PARAMS_FOR_EACH` in `display/intel_display_params.h` + `intel_display_param_named(...)` in
`intel_display_params.c`, read as `to_intel_display(state)->params.gb_ignore_user_gamma`. That struct is copied at
probe (`intel_display_device.c:1666`), so the value is **fixed at module load**, hence 0400.
**Activation** (`i915.ko` is loaded by GBINIT's `busybox insmod`, which ignores `/proc/cmdline`):
(a) GBINIT forwards exactly one whitelisted cmdline token, `i915.gb_ignore_user_gamma=<0-7>`, as an insmod argument
of the `i915.ko` line (PIPELINE, 4.6), so the systemd-boot editor works; (b) `GB_MODOPTS_i915=gb_ignore_user_gamma=3`
in gb.cfg (item 12). Docs and the bootlog checklist reference this name and **both** routes. Plain "module.param on
the kernel cmdline" does **not** work for our insmod-loaded modules in general; the docs must not suggest it for any
other module.

### 2.6 gb_slotsync HOLD line (AUX -> PIPELINE, item 13) [W3R-18]
Exact kmsg prefix (fixed string): `gb_slotsync: HOLD slot ` followed by `<x>: <reason>; default stays gb-<cur>`, with
`<reason>` one of `KMI <t> != running <r>`, `GKI changed <c> -> <t> (same KMI)`, `cannot read boot_<x> kernel version`.
gb_slotsync's `logmsg` reaches kmsg; PIPELINE's summary greps `dmesg | grep -F 'gb_slotsync: HOLD slot ' | tail -n 2`.

### 2.7 KAT drops (KERNEL -> PIPELINE, R-13) [W3R-18]
KERNEL puts `vnni_kat` (static x86-64 ELF) and a `SHA256SUMS` into **`C:\GB-x86\inject\kat\`**. `zram.ko` and
`zsmalloc.ko` are **not** a separate drop: the native tree already ships them (`stage_kernel.py:30`), so after R-05
the k15-signed copies are in `C:\GB-x86\inject\native\modules\`, and `vm_test_kat_cpio()` takes them from there into
the throwaway VM tree only. PIPELINE's `vm_test_kat_cpio()` checks the sums and refuses to build if anything is
missing. verify_image fails any kind other than `vmtest-kat` that contains `vnni_kat`, and fails `vm`/`vmtest-gfx`
trees (gbstage) that contain `zram.ko`/`zsmalloc.ko`. The shipped VM tree stays without zram (item 5 out of scope).

---

## 3. KERNEL implementer

### 3.1 Item 3 -- fix the #UD VNNI emulator (three defects) + add the rate-limited counter

**File (working tree):** `builder:/build/gki/common/arch/x86/kernel/traps.c` (via a new generator so a clean build is
reproducible). **Repo canonical:** `kernel/patches/0002-x86-traps-emulate-VEX-AVX-VNNI-for-the-Googlebook-userspace.patch`
(rewrite the embedded C), `kernel/scripts/apply_vnni4.py` (new; `apply_vnni3.py` kept for provenance). **Docs:**
`kernel/README.md`, new `kernel/K15.md`.

Edits to `gb_emulate_vnni()` / `handle_invalid_op()`:

1. **IRQ idiom.** In `handle_invalid_op()`, wrap the emulator call:
   ```c
   if (user_mode(regs)) {
       bool done;
       cond_local_irq_enable(regs);
       done = gb_emulate_vnni(regs);
       cond_local_irq_disable(regs);
       if (done)
           return;
   }
   ```
   Remove the `if (!user_mode(regs)) return false;` fast-path reliance for IRQ state (keep the user_mode check inside
   as defence). This matches `do_error_trap`'s own pattern. All user copies now run IRQs-on; record
   `irqon += !irqs_disabled()` at the instruction fetch (contract 2.2).
   **Invariant [W3R-12]:** every user access (instruction fetch, memory operand) happens **before**
   `kernel_fpu_begin()`; nothing between `kernel_fpu_begin()` and `kernel_fpu_end()` may fault, sleep or take the
   counter lock. (With IRQs on, `kernel_fpu_begin()` holds `fpregs_lock()` = bh off; a fault there could not sleep.)
2. **VEX.128/VEX.256 upper-bit zeroing -- corrected [W3R-04/05].** ~~"if `ymm` is NULL, set `XFEATURE_MASK_YMM` and
   re-fetch; write `dst`'s 16 bytes"~~ -- **wrong**: a NULL from `get_xsave_addr()` means XSAVES skipped the init-state
   YMM component, so the 256-byte area holds *stale* bytes. Setting the bit and writing one register would make
   XRSTORS load stale data into the other 15 upper halves. Rule: **never set the YMM or ZMM_Hi256 bit; write a
   component only if its header bit is already set.** When the YMM bit is clear, every upper half is architecturally
   0, and so is the result's upper half for both `L` values (d and a have zero upper halves, so every product and sum
   is 0, saturating forms included). MAXVL: on this CPU (AVX-512 on) a VEX destination must also have bits
   [511:256] zeroed. Code (replaces the block from `kernel_fpu_begin()` to `kernel_fpu_end()`):
   ```c
   	struct xregs_state *xs;
   	u64 xf;
   	u8 *ymm, *zhi;
   	...
   	kernel_fpu_begin();
   	xs = &x86_task_fpu(current)->fpstate->regs.xsave;
   	xf = xs->header.xfeatures;
   	xmm = xs->i387.xmm_space;              /* legacy area: fixed offset in every format */
   	if (!(xf & XFEATURE_MASK_SSE)) {       /* SSE init state: XMM0-15 are 0, the buffer bytes are stale */
   		memset(xmm, 0, 16 * 16);
   		xs->header.xfeatures |= XFEATURE_MASK_SSE;   /* safe: the whole XMM area is now valid */
   		sse_init = true;
   	}
   	/* header bit first: get_xsave_addr() WARNs for a feature that is not enabled */
   	ymm = (xf & XFEATURE_MASK_YMM) ? get_xsave_addr(xs, XFEATURE_YMM) : NULL;
   	zhi = (xf & XFEATURE_MASK_ZMM_Hi256) ? get_xsave_addr(xs, XFEATURE_ZMM_Hi256) : NULL;
   	memcpy(&d[0], &xmm[dst * 4], 16);
   	memcpy(&a[0], &xmm[src1 * 4], 16);
   	if (src2 >= 0)
   		memcpy(&c[0], &xmm[src2 * 4], 16);
   	if (L) {
   		if (ymm) {
   			memcpy(&d[4], ymm + dst * 16, 16);
   			memcpy(&a[4], ymm + src1 * 16, 16);
   			if (src2 >= 0)
   				memcpy(&c[4], ymm + src2 * 16, 16);
   		} else {                       /* uppers are 0 -> result upper is 0 */
   			memset(&d[4], 0, 16);
   			memset(&a[4], 0, 16);
   			if (src2 >= 0)
   				memset(&c[4], 0, 16);
   		}
   	}
   	gb_vnni_lanes(op, d, a, c, ndw);
   	memcpy(&xmm[dst * 4], &d[0], 16);
   	if (ymm) {                             /* bit set: write the real upper half (L=1) or zero it (L=0) */
   		if (L)
   			memcpy(ymm + dst * 16, &d[4], 16);
   		else
   			memset(ymm + dst * 16, 0, 16);
   	}                                      /* bit clear: uppers already 0, leave the bit clear */
   	if (zhi)
   		memset(zhi + dst * 32, 0, 32); /* VEX zeroes bits MAXVL-1:256 (dst <= 15) */
   	kernel_fpu_end();
   ```
   Counters `ymm_init = !(xf & XFEATURE_MASK_YMM)` and `zmmhi = !!zhi` are recorded after `kernel_fpu_end()`.
3. **SSE init-state.** Covered by the code in step 2: test the SSE bit **before** reading `xmm_space`; if it is clear,
   `memset(xmm, 0, 256)` and only then set the bit and read the inputs. (The legacy `i387.xmm_space` is at a fixed
   offset even in compacted format, so the base pointer is valid; the init-state guard is the fix.) MXCSR is not
   involved (VNNI does not read it; XSAVES saves MXCSR whenever RFBM[1|2] is set).
4. **Counter -- sysfs, not debugfs [W3R-10].** Contract 2.2: a `static` struct under a `DEFINE_SPINLOCK` holds the
   totals + a 16-entry LRU keyed by `(current->comm, regs->ip)`, updated after `kernel_fpu_end()` for every
   emulation (and for each `efault`). The summary text is built on read in the sysfs `show()`. A `late_initcall` does
   `kobject_create_and_add("gb_vnni", kernel_kobj)` + `sysfs_create_group()` (`summary` 0400, `reset` 0200), and
   failure there only logs: the emulator works without the node. `printk_ratelimited` on a new bucket.
5. **Hardening [W3R-12].** In `gb_emulate_vnni()`: `if (!cpu_feature_enabled(X86_FEATURE_AVX)) return false;` (no
   AVX in XCR0 -> a VEX op must #UD); `if (p1 & 0x80) return false;` (VEX.W1 is not AVX-VNNI); replace the two fixed
   `copy_from_user(ib, ip, 5)` + `(ip + 5, 6)` fetches with `n = insn_fetch_from_user(regs, ib)` (`u8
   ib[MAX_INSN_SIZE]`; sleepable, tolerates a short read at a page end; `<asm/insn-eval.h>`) and require
   `n >= 5` and later `len <= n` (else `efault++`, return false). Optional, cheap: `if (!user_64bit_mode(regs))
   return false;` (moot today: `ia32_emulation=false`); do not emulate when `uprobe_get_trap_addr(regs) != regs->ip`
   (uprobe XOL slot: advancing ip there would run the bytes after the slot). Under single-step (`X86_EFLAGS_TF`),
   keep emulating; the missing post-instruction trap (a debugger's `stepi` steps over one extra instruction) is a
   documented gap. Do **not** turn it into SIGILL, which would kill a debugged process. The
   memory-operand fault stays "return false" (SIGILL), a known fidelity gap (hardware would give SIGSEGV). Recording
   `efault` makes it visible.
6. **Host-testable core [W3R-11] (optional, recommended).** Move the decode, `gb_vnni_lanes` and the xstate merge of
   step 2 (written against a `struct xregs_state *` + flags) into `arch/x86/kernel/gb_vnni_core.h` (static inline,
   no kernel-only calls), included by traps.c and by the KAT-0 host test (3.5). K-01 then allows that file too.

Constraints: no new `EXPORT_SYMBOL`; only static functions plus the one already-present call site; `get_xsave_addr`
is already `extern`'d in the patch (it is also declared in `<asm/fpu/api.h>:144`, so the patch's own `extern` can go);
add `#include <asm/insn-eval.h>` and `<linux/kobject.h>`/`<linux/sysfs.h>` [W3R-10/12]. This keeps `vmlinux.symvers` byte-identical to k14 (acceptance). Keep the
`GB_VNNI_EMULATION` marker and the GPL-2.0 header; add a credit line `/* IRQ/ymm/xstate review from
github.com/linuxkid473/gbos-x86 @f8a5c1a HANDOFF.md:59-62; code is ours. */`.

### 3.2 Item 10 -- i915 "white veil" parameter-gated fix (default off) + the grep is PIPELINE/AUX

**File (working tree):** `drivers/gpu/drm/i915/display/intel_display.c` and the param in
~~`i915_params.c`/`.h`~~ **`drivers/gpu/drm/i915/display/intel_display_params.{c,h}`** [W3R-09] (v6.18 display code
reads `display->params`, a copy made at probe). **Repo:** new `kernel/patches/0005-i915-optional-ignore-user-gamma.patch`.
Add `gb_ignore_user_gamma` to `INTEL_DISPLAY_PARAMS_FOR_EACH` (`param(int, gb_ignore_user_gamma, 0, 0400)`) and
`intel_display_param_named(gb_ignore_user_gamma, int, 0400, "GB: bitmask 1=gamma 2=degamma 4=CTM -> linear (default 0)")`,
which exposes it as `i915.gb_ignore_user_gamma`. In `intel_crtc_copy_uapi_to_hw_state_nomodeset()` (confirmed at
intel_display.c:4449 in v6.18), with `struct intel_display *display = to_intel_display(state)` and
`int ig = display->params.gb_ignore_user_gamma`, guard each of the three `drm_property_replace_blob(...)` calls as
`(unlikely(ig & BIT(n)) ? NULL : crtc_state->uapi.<blob>)`. The joiner secondary copies from the primary's hw state
(`copy_joiner_crtc_state_nomodeset`), so it follows automatically. The diff stays <= 25 lines. The default path is the
original expression. Default-off => byte change in `i915.ko`
only when the param is compiled in; behaviour identical unless the user sets it. i915 is `=m` and ours, so the GKI KMI
and `vmlinux.symvers` are unchanged. Credit `gbos-x86 HANDOFF.md:112-135` (cause/fix/Night-Light cost). Mark in
`kernel/K15.md` that applicability to the SB3 panel is INFERRED (confirmed only on gbOS86's i915 laptop) and that it is
default-off precisely so it is safe to ship unused.

Decision recorded: ship the **checklist/grep (always)** AND the **parameter-gated patch default-off**. This satisfies
"a ready, parameter-gated fix if clearly safe": default-off + module-internal + no KMI change makes it clearly safe to
include without activating it.

### 3.3 k15 build -- `kbuild15_native.sh` (from `kbuild14_native.sh`)

`C:\GB-build\kernel\kbuild15_native.sh` (copy of `kbuild14_native.sh`, which is ours/Apache-2.0). Differences:

* **Base tree change.** k14's `gb_emulate_vnni` (buggy) lives in the uncommitted k13 working tree carried into
  `k13-native`. For k15 the fix is applied **as a committed patch on top of the nouveau branch**, so the pristine k13
  working tree is never mutated on disk and K-09 stays identical to k14:
  * record `k13-native` exactly as k14 does (same saved `/build/k15/k13-state`);
  * branch `k15-nouveau` = `k13-native`; `git am` the **40 k14 nouveau patches** (unchanged) **then** the two k15
    patches: `0110-x86-traps-vnni-emulator-irq-ymm-xsave-counter.patch` (the diff from the current buggy emulator to
    the fixed one, generated by KERNEL) and `0111-i915-optional-ignore-user-gamma.patch`;
  * `NPATCH_NOUVEAU=40`, `NPATCH_K15=2`; the K-01 check is split: the 40 nouveau patches must stay inside
    `drivers/gpu/drm/nouveau/`, `0110` must touch only `arch/x86/kernel/traps.c` (+ `arch/x86/kernel/gb_vnni_core.h`
    if 3.1 step 6 is taken) [W3R-11], `0111` only `drivers/gpu/drm/i915/display/`.
  * [W3R-13] Equivalent and simpler: `k15-nouveau` = branch from the existing **`k14-nouveau`** (k13-native + the 40,
    with fixed identity and dates, so the commit ids are reproducible), then `git am` 0110/0111. Either way,
    `git diff k14-nouveau k15-nouveau -- drivers/gpu/drm/nouveau` must be empty (K-07).
* `export BUILD_NUMBER=16302715`; `DIST=/build/dist15`; markers `/build/kbuild15.{done,failed}`; `/build/dist` and
  `/build/dist14` are never written.
* Restore the pristine k13 working tree at the end and prove it equals the saved k13 state (K-09), exactly as k14.
* Test hook `KB15_TEST_SRC`/`KB15_TEST_HOME` as in k14's `test_kbuild14_state.sh` (new `test_kbuild15_state.sh`).

Preflight floors identical to k14: `/build` >= 10 GiB, `/` >= 5 GiB free, 1-min load < 2, no `/var/tmp/gbiso` build
running, `/build/dist14` present (for the comparisons). [W3R-26] `/build` had **12 GB free (90 % used)** on
2026-10-26, only ~2 GB above the floor: record `df` before and after R-04, and do not run the fw_coverage extraction
(5.5) if `/build` < 5 GB afterwards.

### 3.4 k15 acceptance (`patches\k15\tools\k15_accept.sh`, from `k14_accept.sh`)

[W3R-13] Ids K-01..K-09 keep **k14's meanings** (`k14_accept.sh`, INTERFACES 18.3; K-09 = state restore); the new
checks are K-10..K-14. ~~"K-07 nouveau.ko sha256 identical"~~ could never pass: the module vermagic embeds the new
kernel release (so `.modinfo`, build-id and `.BTF` change) and the module is re-signed.

| Id | Check | Expected |
|---|---|---|
| K-01 | patch scope | 40 nouveau commits inside `drivers/gpu/drm/nouveau/`; `0110` only `arch/x86/kernel/traps.c` (+ `gb_vnni_core.h` if used); `0111` only `drivers/gpu/drm/i915/display/` |
| K-02 | `kernel_x86_64_dot_config` diff vs **k14** | empty (no config change; sysfs needs none) |
| K-03 | `vmlinux.symvers` diff vs **k14** | empty (no new export; static funcs only); `kernel_x86_64_Module.symvers` diff informational |
| K-04 | unresolved symbols: nouveau closure **and the i915 closure** (`i915 drm_display_helper drm_buddy ttm ...` as staged) | 0 unresolved, same as k14 |
| K-05 | signatures | every dist15 `.ko` (same count as dist14) + the stage set carry a valid PKCS#7 from the cert in the k15 vmlinux (same tool as k14) |
| K-06 | lapis vendor_ramdisk/vendor_dlkm CRCs | 0 vmlinux CRC mismatch, 0 unresolved, result files identical to k14's (the 11 ipu6 diffs same as k14) |
| K-07 | nouveau unchanged | **gate:** `git diff k14-nouveau k15-nouveau -- drivers/gpu/drm/nouveau` empty and `modinfo nouveau.ko` identical to k14 except `vermagic`/`sig_*`; **informational:** sha256 of `.text*`, `.rodata*`, `.data`, `__versions` sections (objcopy `--dump-section`) vs k14 |
| K-08 | kernel release | `kernel_release.txt` = gki-info = bzImage banner = module vermagic; differs from k14's `6.18.21-android17-5-g9629ced3e50e-ab16302714`; one line; `bzImage`/`vmlinux` differ from k14 (expected) |
| K-09 | state restore (k14 sense) | `/build/gki/common` back on detached `3938c8d97142`, identical to the saved k13 state |
| K-10 | `i915.ko` | `modinfo -F parm` lists `gb_ignore_user_gamma:...`; the file differs from k14's i915.ko (expected) |
| K-11 | counter | `strings vmlinux` contains `gb_vnni` and the attribute names `summary`/`reset`; no `debugfs_create` reference from the gb_vnni code |
| K-12 | repo-patch equivalence | extract the pristine files with `git archive 3938c8d97142 <paths> \| tar -x -C /build/k15/k12` (no worktree, so `.git` and K-09 stay untouched), apply the rewritten repo `kernel/patches/0002-*.patch` with `patch -p1` -> `traps.c` (and `gb_vnni_core.h`) byte-identical to `git show k15-nouveau:<path>`; repo `0005-*.patch` likewise for the i915 files; `0111` == `0005` hunks |
| K-13 | build hygiene | no new objtool/modpost warnings vs the k14 build log (`grep -E 'objtool|WARNING: modpost'` diff empty), notably none for `traps.o` (noinstr) |
| K-14 | KAT-0 | the host emulator-core test (3.5) PASSes on the k15 core and **FAILs** the 3 defect cases on the k14 core (negative control) |

Evidence to `builder:/build/k15/accept/` and `patches\k15\evidence\`.

### 3.5 Item 3/KAT -- the VEX-vs-EVEX known-answer test binary

**File:** `kernel/tools/vnni_kat.c`, authored by KERNEL at R-02 (its owned tree) to the spec below; the spec lives in
this plan only -- no binary or source is written during the read-only phase. Static x86-64 ELF: build with the NDK
r27c clang `--target=x86_64-linux-android34 -static -O2` (no `-mavx*` flags for the C code). Every VEX/EVEX test
instruction is emitted as **explicit `.byte` encodings** inside one inline-asm block per case (load registers, op,
store registers), so neither the compiler nor the assembler can re-encode or interleave vector code; the host check
disassembles the binary (`objdump -d`) and asserts each encoding. Worker processes come from `fork()`, not threads.
[W3R-11] It:

* executes **VEX-encoded** `vpdpbusd`/`vpdpbusds`/`vpdpwssd`/`vpdpwssds` in both **128-bit (VEX.128)** and **256-bit
  (VEX.256)** forms, register-register and with a **memory operand** -- these raise #UD on Ice Lake and are emulated
  by k15 [W3R-24: the host is the SB3 itself (`i7-1065G7`, no AVX_VNNI in silicon), so the `lapis-boot.vmx` mask is
  moot, and the throwaway `gbw2.vmx` from `mkvm.ps1` has no CPUID masks at all; the env asserts above are the check];
* executes the **EVEX AVX512_VNNI** equivalents (`vpdpbusd`/`vpdpwssd` on xmm/ymm with an EVEX prefix) which Ice Lake
  runs **natively** (`cpuid.7.ecx` AVX512_VNNI bit is NOT masked in the vmx; `serial0.log` shows AVX-512 xstate
  enabled) -- these are the reference;
* compares VEX (emulated) vs EVEX (native) for a battery of random inputs (saturating and non-saturating, word and
  byte forms), with the accumulator preloaded;
* **environment asserts first** [W3R-11]: CPUID.7.0:EBX AVX512F + AVX512VL and ECX AVX512_VNNI present, XCR0 has
  opmask/ZMM_Hi256/Hi16_ZMM, CPUID.7.1:EAX AVX_VNNI (bit 4) **absent**. If an assert fails ->
  `GB_VNNI_KAT: ENV <what>` and exit 2 (never PASS: with native AVX_VNNI nothing would be emulated);
* checks **VEX.128 upper zeroing incl. MAXVL** [W3R-05]: preload zmm_dst with a sentinel in all 512 bits, run a VEX.128
  op -> bits [511:128] must be 0; run a VEX.256 op -> bits [511:256] must be 0; on the EVEX reference the same bits are
  0 by definition;
* checks **register preservation**: before every emulated op load all zmm0-31 with per-register patterns. Afterwards
  every register except dst must be bit-identical (all 512 bits), and dst must equal the EVEX reference in [255:0]
  (with zero above as stated);
* checks **SSE / YMM init state with a stale buffer** [W3R-11]: (1) load all 16 ymm with non-zero sentinels and run
  one emulated VEX op, which makes the kernel save non-zero data into the task's xsave buffer; (2) `vzeroall` (SSE+YMM
  -> init) and verify with `XGETBV(ECX=1)` (if CPUID.(0xD,1):EAX[2]) that XINUSE[1] and XINUSE[2] are 0; (3) execute
  the VEX op immediately (reg-reg, all inputs 0) -> dst must be 0 **and all other 15 xmm/ymm must still be 0**. The
  buggy k14 logic commits the stale sentinels into all of them. Variant for YMM-only init: (1) as before, then
  `vzeroupper`, load non-zero xmm low halves, run a VEX.128 op -> all 16 upper halves must be 0 (this catches the
  original plan's "force the YMM bit" bug). If XINUSE cannot be shown to be 0, report `SKIP sse_init` (not PASS).
  KAT-0 below covers the logic deterministically anyway;
* **negative encodings must not be emulated**: VEX.W1 forms of 0F38 50-53, and the pp=F2/F3/none forms (AVX-VNNI-INT8
  `vpdpb[su,ss,uu]d`, item 15 out of scope) must raise **SIGILL** (caught with a handler that records and skips them);
* **page-end instruction** [W3R-12]: copy a 6-byte memory-form op + `ret` so that it ends exactly at the end of an
  executable page whose next page is `PROT_NONE`. It must be emulated (k14 refuses it because of the 11-byte overfetch);
* **preemption / signal stress** [W3R-11]: `2 x nproc` forked workers, each with its own register patterns, loop
  100k emulated ops + full-register checks, with `sched_yield()` every 1k ops and a 1 ms `setitimer(ITIMER_REAL)`
  SIGALRM handler that clobbers zmm0-31 (the kernel must save and restore around the signal frame). Any mismatch fails;
* **memory operands on fresh pages**: `mmap(MAP_ANON)` a region, do NOT touch it, run a VEX op reading from it
  (forces the emulator's `copy_from_user` to fault the page in with IRQs on in k15); also `madvise(MADV_DONTNEED)`
  between iterations. **Fault proof** [W3R-10]: `mincore()` shows the page non-resident before the op, and the
  `getrusage(RUSAGE_SELF)` `ru_minflt` delta across the op is >= 1;
* **memory operands on zram-swapped pages**: only in the KAT image's root service (6.4), after a zram swap is set up:
  fill the region, `madvise(MADV_PAGEOUT)`, confirm with `mincore()` that it is non-resident, run the VEX op, and
  require an `ru_majflt` delta >= 1 or a `/proc/vmstat` `pswpin` delta >= 1 (so the emulator faulted a page back from
  zram with IRQs on);
* **bad address**: a memory operand on a `PROT_NONE` page -> a signal (SIGILL today, see 3.1 step 5), never an oops;
  it counts in `efault`;
* **exact counter deltas**: read `/sys/kernel/gb_vnni/summary` (root) before and after. `emulated`, `vex128`,
  `vex256`, `word` and `byte` must increase by exactly the counts the KAT executed (minus the SIGILL negatives),
  `irqon` must increase by the same amount as `emulated`, and `efault` by exactly the bad-address cases;
* prints `GB_VNNI_KAT: PASS <n> cases (<s> skipped: <names>)` or `GB_VNNI_KAT: FAIL case=<...> vex=<hex> evex=<hex>`
  to stdout (the service routes it to kmsg).

A **host build** of the same logic (compiled for the Ice Lake builder, which has AVX512_VNNI -- `/proc/cpuinfo` shows
`avx512_vnni`, `avx512vl`) runs natively as a self-check of the comparison harness itself (no emulation there; it just
proves EVEX == a scalar C reference), named `kernel/tools/vnni_kat_hostcheck`.

**KAT-0 (host unit test of the emulator core, KERNEL, R-02)** [W3R-11]: `kernel/tools/test_gb_vnni_core.c` includes
`gb_vnni_core.h` (3.1 step 6) and drives decode + lanes + xstate merge against a fake `xregs_state` buffer. Vectors:
every opcode x L x reg/mem, all ModRM/SIB/RIP/disp forms incl. r8-r15/index=rsp/base=rbp,r13, saturation edges, and the
header-bit combinations {SSE, YMM, ZMM_Hi256} in {set, clear} with **stale non-zero bytes in the cleared
components**. Expected: the k15 core never reads or commits stale bytes, never sets the YMM/ZMM_Hi256 bits, and
zeroes dst[511:128/256] only through set components. **Negative control:** a test-only copy of the k14 merge logic
must FAIL the SSE-init, VEX.128-upper and MAXVL cases, which proves the test can see the defects (K-14).

---

## 4. PIPELINE implementer

### 4.1 Item 1 -- keep `8250.nr_uarts` natively

* `C:\GB-x86\build_usb.py` + repo copy, `native_cmdline()` (`:71-87`):
  * Remove `"8250.nr_uarts"` from the `drop` tuple at `:82`.
  * Fix the docstring `:72-73`: explain that `8250.nr_uarts` is **kept** because `CONFIG_SERIAL_8250_RUNTIME_UARTS=0`
    and `serial8250_init()` returns `-ENODEV` with `nr_uarts=0`, so the LPSS UART that carries the Surface Aggregator
    cannot register; still drop `console=ttyS0`/`earlycon`/`video=Virtual-1` and never add `console=ttyS*`/`earlycon`
    natively (the serdev core takes the port). Credit `gbos-x86 HANDOFF.md:423` (Surface Laptop 7).
  * Result: native cmdline inherits `8250.nr_uarts=4` from `be.CMDLINE` (it is no longer stripped).
* `C:\GB-x86\verify_image.py` (repo copy via R-15), `v16()` (`:1384`): add to the required-tokens list a check that a
  native default entry carries `8250.nr_uarts=` with value >= 1, and that it has **no** `console=ttyS` and no
  `earlycon`. [W3R-19] For `vm`/`vmtest*` kinds, assert the VM entry still carries `8250.nr_uarts=4` and
  `console=ttyS0,115200` (proof that the VM is unchanged).
* [W3R-18] **Existing fixture:** `tests\w2b\test_verify_image.py:159` builds a native options string without
  `8250.nr_uarts`, so the tightened V16 fails it. Add the token there (and to any other native fixture found by
  `grep -rn "pstore_disable" tests`), and re-run the whole `tests\w2a` + `tests\w2b` suites (TA-0 in 4.8).
* `build_esp.py` `snapshot()` (item 8, same block): add `cat /proc/tty/driver/serial; ls -l /sys/bus/serial/devices;
  ls /sys/bus/serdev/devices 2>/dev/null` to the `== summary` output.
* Dual-boot export: no edit (inherits `native_cmdline`); the `manifest.json` `cmdline` will now contain
  `8250.nr_uarts`, which cross-check 8 compares token-for-token -- a fresh export is required (section 7).
* ISO installer: `C:\GB-x86\iso\test\t0_static.py` -- add an assertion that the installer kernel config
  (`/boot/config-6.8.0-147-generic`, read on the builder during BUILD, surfaced into the static facts) has
  `CONFIG_SERIAL_8250_RUNTIME_UARTS != 0`; add one sentence to `iso/DESIGN.md` 3.3 recording that the Ubuntu installer
  kernel defaults to 32 so it needs no `8250.nr_uarts` (unlike the native GKI). No grub.cfg change. (Reviewer check:
  that file **is** the ISO kernel's config, because `iso/build/builder/remote.sh:89` copies exactly
  `/boot/vmlinuz-6.8.0-147-generic`; the assertion must read the config of the same `KREL` that `build_iso.py:64`
  pins.)

### 4.2 Item 4 -- `fsverity` on userdata (native fstab only)

* [W3R-06] ~~edit `inject\fstab.android-desktop`~~ -- **the shared file stays unchanged.** `FSTAB` (`build_esp.py:50`)
  feeds the VM tree (`:1299`, bind-mounted over `/vendor/etc/fstab` by the VM `gb.rc`) **and** the native tree
  (`:1509`). The flag is **not** harmless in the VM: the VM runs the same GKI (`CONFIG_FS_VERITY=y`) with lapis's
  tune2fs, and AOSP `tune_verity()` runs `tune2fs -O verity` at **every** mount whose superblock lacks the feature,
  including the existing `/data`, not only after a wipe. The next VM ESP built from a shared edit would set
  RO_COMPAT_VERITY on the user's live VM userdata for good, which is a VM change without approval.
* Instead, in `build_esp.py` next to `FSTAB`, derive a native-only copy with no second source file:
  ```python
  def _with_fsverity(fstab):          # native only (item 4, [W3R-06]); the VM tree keeps FSTAB byte-identical
      out, n = [], 0
      for ln in fstab.split(b"\n"):
          f = ln.split()
          if len(f) == 5 and f[0] == b"/dev/block/by-name/userdata" and f[1] == b"/data" and b"fsverity" not in f[4].split(b","):
              ln = ln.rstrip() + b",fsverity"; n += 1
          out.append(ln)
      assert n == 1, "userdata line not found exactly once"
      return b"\n".join(out)
  FSTAB_NATIVE = _with_fsverity(FSTAB)
  ```
  Use `FSTAB_NATIVE` only in the native tree entry (`:1509`); `:1299` keeps `FSTAB`. Natively the flag takes effect
  **once at the next boot on the existing `/data`** (tune2fs on the unmounted block device before the mount; a failure
  only logs `FS_STAT_ENABLE_VERITY_FAILED`). Ubuntu 6.8 (`CONFIG_FS_VERITY=y`) and its e2fsprogs 1.47 can still read
  such a filesystem, and the installer never runs e2fs tools on userdata (`check_forbidden.py`). Credit gbos-x86 issue #17.
* verify_image: the native tree's `etc/fstab.android-desktop` userdata line has `fsverity`; the VM tree's is
  byte-identical to `inject\fstab.android-desktop` (no `fsverity`). AUX adds the note to `docs/INPUTS.md`/`OTA.md` that
  the VM would need the same flag for Play's fs-verity use, and that it needs a user decision because it changes the
  VM's existing `/data` superblock permanently.
* **Erratum (post-build audit, 2026-10-26).** The premise of [W3R-06] does not hold on this build. lapis's fs_mgr
  enables ext4 verity on `/data` (and on a freshly formatted `/metadata`) without the `fsverity` flag: boots with the
  plain fstab (wave 2 native VM-1, the wave 3 VM-tree KAT boots) log `Enabling ext4 verity on
  /dev/block/by-name/userdata` (INFERRED mechanism: AOSP forces it for `ro.product.first_api_level` >= 30; this build
  reports 37). The live VM's userdata already has RO_COMPAT_VERITY (ro_compat 0x807b, read-only check). So the
  native flag is redundant but harmless, VM-1 W3-07 cannot show the flag's own effect, and D-c still holds (wave 3
  did not change the VM). INPUTS.md, OTA.md, NATIVE.md and README-FLASH.md were corrected; no shipped file changed.

### 4.3 Item 8 -- gb_bootlog one-screen triage summary

`C:\GB-x86\build_esp.py`, `GB_BOOTLOG_SH` `snapshot()` (`:1055`). Add a `== summary` block at the **top** of each
snapshot (so the first screen is the triage), built from read-only loops + greps with per-command `timeout`s. It
reports pass/fail-ish one-liners tied to the DEVICE-STATUS checklist:

* serial/Aggregator: `8250` banner present in dmesg, no `dw-apb-uart ... -22`, `surface_serial_hub` bound, no
  `failed to setup IRQ` (item 1);
* inputs: `surface_hid` devices in `/proc/bus/input/devices`;
* audio: ALSA card id line (`grep -m1 ... /proc/asound/cards`), and whether DRAS loaded `sofhdadsp` (item 2);
* firmware: `dmesg | grep -i 'Direct firmware load.*failed\|Failed to load'` (SOF/i915/iwlwifi/ibt names), and the
  `i915` DMC line;
* `/proc/swaps` line;
* driverless PCI functions: count of `/sys/bus/pci/devices/*` with no `driver` symlink;
* `#UD` counter: `timeout 5 cat /sys/kernel/gb_vnni/summary` (item 3, contract 2.2; **sysfs** [W3R-10]);
* i915 "white veil" (item 10): `logcat -d -b all | grep -c -F 'drmhwc: Missing optional LOG_GAMMA_LUT'` and the
  current value from `/sys/module/i915/parameters/gb_ignore_user_gamma` if present (0400, readable by root);
* HWUI-on-Vulkan (item 18): `logcat -d -b all | grep -c -F 'queueFamilyProperties.queueCount < kRequestedQueueCount'` (2.4);
* [W3R-14] **every** logcat in the summary uses `-d` (dump and exit) under `timeout 20`: a plain `logcat | grep`
  streams forever and would stall the snapshot (the existing snapshot already uses `logcat -d`);
* gb_slotsync HOLD (item 13, contract 2.6): `dmesg | grep -F 'gb_slotsync: HOLD slot ' | tail -n 2`;
* bring-up (item 12): `tail -n 40 /odm/gbinit_bringup.log` (absent = feature off) and the GBINIT forwarder line for
  i915 (item 10) from `/odm/gbinit_modules.log`.

Redaction: the summary must contain **no** serial numbers, SSIDs or account names. The full `getprop`/`logcat` dumps
already below carry those; add a redaction reminder to `docs/FLASHING.md` (AUX) that `gblog` holds getprop+logcat and
should be reviewed before sharing. Cover the new block in `C:\GB-x86\tests\w3a\` (new; mirror `tests\w2a` /
`tests\w2a\bootlog\test_gb_bootlog.sh`, which sources the function block between `#@@functions-begin/end`). Keep
`assert b"\r" not in GB_BOOTLOG_SH` and the ASCII assertion.

### 4.4 Item 11 -- disable apgetty natively

`build_esp.py`, `GB_RC_NATIVE` (`:1143`). [W3R-21] Final instruction (the earlier draft contradicted itself): in
`GB_RC_NATIVE` `on init` add `setprop desktop.apgetty.enable 0`, and in `on boot` add `stop apgetty`. Basis, from
the planner's read-only extraction of `/system_ext/etc/init/apgetty.rc`: apgetty is enabled by
`on early-init && property:ro.boot.verifiedbootstate=orange` -> `setprop desktop.apgetty.enable 1` and started by
`on property:desktop.apgetty.enable=1`. Init evaluates property triggers only after `late-init` is queued, so a value
of 0 set at `on init` (after every `early-init` action, whatever the parse order) means the start trigger never
fires. **The implementer pastes the real `apgetty.rc` stanza into the commit message** and confirms the service is
`disabled` and has no other start path. If it does have one, the fallback is `on property:init.svc.apgetty=running`
and `on property:init.svc.apgetty=restarting` -> `stop apgetty`. gbOS86's empty-file bind over the rc is **not**
available to us: init parses every rc file before any action runs, and GBINIT runs before first-stage init mounts
`/system_ext`. Natively there is no console UART (with item 1 the SAM UART is taken by serdev), so apgetty has
nothing to attach to and crash-loops otherwise. Credit `gbos-x86/usb/hook.sh:611-612`. VM
behaviour unchanged (this is in `GB_RC_NATIVE`, not the VM `GB_RC`). verify_image: optional V8 token check that
`GB_RC_NATIVE` carries the apgetty-disable line.

### 4.5 Item 12 -- ESP bring-up config (gbinit) -- PIPELINE part

`build_esp.py` `GBINIT` (`:213`), a new `GB_BRINGUP_SH` constant packed as `/gbbringup.sh` (0755, root, **in the
native cpio only**), and `verify_image.py`. Per contract 2.3 (rewritten by [W3R-01/02/03/17]). Facts: GBINIT is the
`rdinit` (`panic=0` -> any exit before `exec /init` hangs the machine), it runs before ueventd, there is **no
devtmpfs** (`CONFIG_DEVTMPFS` not set) and no `/dev/block/by-name` yet, and it unmounts `/proc` and `/sys` before it
mounts the `/odm` tmpfs. The ~~mount devtmpfs~~ / ~~by-name resolution like gb_bootlog~~ steps were therefore not
implementable. Changes:

* **GBINIT (minimal, cannot fail):** in the existing cmdline `case`, add `gb.overlay=1) OV=1;;` and extend the
  existing arm to `gb.hw=*) HW=${a#gb.hw=}; [ "$HW" = native ] && HWTOK=1;;`. A separate `gb.hw=native)` arm
  placed before it would shadow it, because `case` takes the first match, and `HW` would stay empty. After
  `cp -a $SRC/. /odm/` (and the existing whiteouts), before the modules loop:
  ```sh
  if [ "$HW" = native ] && [ "$HWTOK" = 1 ] && [ "$OV" = 1 ] && [ -f /gbbringup.sh ]; then
    /busybox timeout 40 /busybox sh /gbbringup.sh >> /odm/gbinit_bringup.log 2>&1
    echo "bringup rc=$?" >> /odm/gbinit_bringup.log
    for m in /gbbu/esp /gbbu/sys /gbbu/proc; do /busybox umount -l $m 2>/dev/null; done
  fi
  ```
  No `set -e`, nothing else new before `exec /init`, which stays the last line. A syntax error, a hang or a non-zero
  exit inside `/gbbringup.sh` only produces a log line.
* **`/gbbringup.sh`** (busybox ash; the rules of 2.3): mount `proc`/`sysfs` on `/gbbu/proc`, `/gbbu/sys`; bounded wait
  (10 s, 0.5 s polls) for block devices; GPT GUID binding to `androidboot.boot_part_uuid` with private `mknod` nodes
  under `/gbbu/dev`; mount the bound ESP with `ro,nosuid,nodev,noexec,uid=0,gid=0,fmask=0133,dmask=0022`; check
  `gb/gb.cfg` (magic line, size and line limits, charset) all-or-nothing; apply `GB_MODOPTS_*`/`GB_SKIPMOD` to the tmpfs
  `/odm/lib/modules/modules.load`, `GB_PROP_*` to `/odm/etc/build.prop`, and the overlay whitelist; log every
  applied, ignored or refused key/file; unmount everything it mounted. Needed applets (confirm with
  `inject/busybox --list` on the builder): `timeout mount umount mknod dd hexdump cat chmod mkdir head wc tr sed sleep`.
* **Never on VM boots:** the explicit `gb.hw=native` token (`HWTOK`), not the DMI fallback, plus `/gbbringup.sh`
  existing only in the native cpio.

`verify_image.py` [W3R-17]: ~~allow `gb/gb.cfg` and `gb/overlay/**` in the ESP file set~~ (verify_image only ever
checks build artifacts). Instead: **fail** if a built ESP contains `gb/gb.cfg` or anything under `gb/overlay/`; check
that GBINIT has the three-token gate, the `timeout` call and `exec /init` as its last non-empty line; check that
`/gbbringup.sh` is in the native cpio and absent from the VM cpio; and check that no shipped loader entry contains
`gb.overlay=`. Busybox-ash validation: the `tests\w3a` harness (TA-6) runs on the **builder** (the shipped
`inject/busybox` is a Linux x86-64 binary).

### 4.6 Item 10 (pipeline part) & 18 (bootlog part)
Covered inside 4.3 (the two greps in the summary). No build.prop change is required for item 10 (the fix is the
default-off modparam); do not alter `ODM_BUILD_PROP_NATIVE` for it. Keep the existing
`vendor.hwc.drm.enable_color_pipeline=false` etc.

**[W3R-09] Item 10 activation forwarder (GBINIT, PIPELINE).** `i915.ko` is loaded by GBINIT's `busybox insmod`
(`modules.load` line 41), and insmod ignores `/proc/cmdline`, so the param is otherwise unreachable from the
systemd-boot editor. In the existing cmdline loop add
`i915.gb_ignore_user_gamma=[0-7]) I915OPT="gb_ignore_user_gamma=${a#i915.gb_ignore_user_gamma=}";;` (the `case`
pattern pins one digit 0-7; anything else does not match). In the modules loop, when `$m` is `i915.ko` and `$I915OPT`
is set, append it to `$a` and log `i915 opt $I915OPT` to `/odm/gbinit_modules.log`. Exactly this one token: no
generic `<module>.<param>` forwarding, because that would suddenly apply tokens that are silently ignored today. Test
in TA-8.

### 4.7 Item 3/4/8/11/12 staging and image checks
`stage_kernel.py k15 --dist /build/dist15` (no code change beyond accepting the k15 TAG, which it already does: TAG is
`k<number>...`). `verify_image.py` V4 `--krel C:\GB-build\kernel\k15_release.txt`.

### 4.8 PIPELINE tests (`C:\GB-x86\tests\w3a\`, builder scratch `/build/native/dgpu/w3a/`, removed after)
* TA-1 `native_cmdline` unit: has `8250.nr_uarts=4`, no `console=ttyS`, no `earlycon` (host, importing build_usb);
  [W3R-19] and `be.CMDLINE_VM` plus the `--vm-test` cmdline are byte-identical to before (golden strings).
* TA-2 `verify_image.v16` native: passes on a fixture entry with the token, fails without it.
* TA-3 bootlog `== summary`: the function block (sourced to `#@@functions-end`) emits every summary line with shimmed
  tools (extend `tests\w2a\bootlog\test_gb_bootlog.sh`).
* TA-4 apgetty: `GB_RC_NATIVE` contains the disable line; the VM `GB_RC` does not.
* TA-5 fstab: the **native-tree** userdata line has `fsverity` (see TA-9 for the VM side); `fs_mgr` keyword grammar
  still parses (a tiny python check).
* TA-6 gbinit bring-up (builder, under the shipped `inject/busybox`, fake root with a fake `/sys/class/block`, fake
  GPT images and a vfat image made with mtools/mkfs.vfat in builder scratch) [W3R-01/02/03/17]:
  `busybox sh -n` on GBINIT and `/gbbringup.sh`; a whitelisted `gb.cfg` applies `GB_MODOPTS`/`GB_SKIPMOD`/`GB_PROP`;
  unknown keys, a bad magic line, >4 KiB, >64 lines, a non-ASCII byte, `$(...)`/`;`/`*` in values and denied props
  (`ro.debuggable`, `ro.hwui.use_vulkan`, `ro.gfx.driver.0`, `persist.vendor.gb.x`) are refused, all or nothing;
  overlay files `etc/init/x.rc`, `etc/selinux/precompiled_sepolicy`, `vup/etc/init/x.rc`, `lib64/x.so`, `../x`, a
  symlink-named path and a 300 KiB file are refused, while `usr/idc/x.idc` is applied 0644 root; the GPT binding picks
  the right disk among two (the other with its own EFI-SYSTEM), and skips when the GUID is missing, appears twice or
  the token is absent; the kill switch (no `gb.overlay=1`, or `gb.hw=vm`, or DMI-only native) applies nothing and
  never mounts; a **hanging** `/gbbringup.sh` (sleep 999) and a **syntax-error** one both still reach `exec /init`
  (a stub) within 41 s.
* TA-7 t0_static: installer kernel `RUNTIME_UARTS != 0` assertion.
* TA-0 [W3R-18] regression: the complete `tests\w2a` and `tests\w2b` suites pass after the fixture update (4.1).
* TA-8 [W3R-09] forwarder: cmdline `i915.gb_ignore_user_gamma=3` -> the i915 insmod line gets
  `gb_ignore_user_gamma=3`; `=8`, `=3x`, `=` and `i915.foo=1` -> nothing forwarded; no other module line changes.
* TA-9 [W3R-06] fstab: the native tree's userdata line ends `...,reservedsize=128M,fsverity`; the VM tree's fstab is
  byte-identical to `inject\fstab.android-desktop`; `_with_fsverity` asserts on a fixture with zero or two userdata
  lines.
* TA-10 [W3R-14] bootlog summary: with a shimmed `logcat` that never exits unless given `-d`, the summary still
  finishes in < 30 s.

---

## 5. AUX implementer

### 5.1 Item 2 -- native DRAS `<soundcard>` = `sofhdadsp` + required fields + test

* `C:\GB-build\native\audio\tree\vup\etc\data\hal_configs\audio_default.xml` **and** the repo copy
  `native/audio/tree/vup/etc/data/hal_configs/audio_default.xml`: change line 2
  `<soundcard>sof-hda-dsp</soundcard>` -> `<soundcard>sofhdadsp</soundcard>` (the ALSA card **id**, not the display
  name). Add an XML comment crediting gbos-x86 (`AGENTS.md:33-34`, `HANDOFF.md:96-104,413-416`): DRAS matches
  `<soundcard>` to the ALSA card id (`sof-soundwire` -> `sofsoundwire`), and `sofhdadsp` is what our own
  `audio_config.xml` comment and `gb_audio.sh` already use (`hw:sofhdadsp`, `ID=sofhdadsp`).
* **Re-verified this session** by extracting lapis's own `/vendor/etc/audio/sof-soundwire_cs42l43_cs35l56/` and
  `/vendor/etc/data/hal_configs/audio_default.xml` read-only on the builder: lapis's `audio_default.xml` uses the
  display name `sof-soundwire` as `<soundcard>` (NOT the id) -- so **the card-id claim and the exact value must be
  stated carefully**. gbOS86's hardware finding is that DRAS compares `<soundcard>` to the **card id**; lapis ships
  `sof-soundwire` and its card id is `sofsoundwire`, so lapis's own default would also seem to not match by that rule.
  ~~Resolution: `skl_hda_dsp_generic` sets `card->name = "hda-dsp"` and ALSA derives ...~~ (speculative; dropped)
  **[W3R-23] Resolution, from evidence only:** the card id `sofhdadsp` is observed (`gb_audio.sh:30` `ID=sofhdadsp`,
  `hw:sofhdadsp`). gbOS86 saw DRAS fail with the short name "HDA Intel PCH" and work with the id "PCH" (HANDOFF.md
  96-104), which **refutes exact short-name matching**. The two rules still consistent with lapis shipping
  `sof-soundwire` (id `sofsoundwire`) on its own hardware are (a) exact id, or (b) the configured string normalised
  the ALSA-id way (non-alphanumerics dropped). `sofhdadsp` matches under both; our current `sof-hda-dsp` matches only
  under (b). So `sofhdadsp` is the value that is right under every rule the evidence allows. Keep it, and record
  this reasoning in the AUX commit message. First-boot DRAS logcat (the 4.3 summary) is the confirmation.
* **Required fields.** From the extracted lapis `sof-soundwire` `audio_config.xml` (read-only this session), the `Mic`
  section carries `CaptureMixerElem` (value `Headset Mic`) and `Headphone` carries `JackSwitch`. gbOS86 hit
  "missing field JackSwitch" and mirrored sof-soundwire field-for-field. **Our `sof-hda-dsp_sb3/audio_config.xml`
  already has `JackSwitch` in Headphone** but its `Mic` section has **no `CaptureMixerElem`**. Add
  `<CaptureMixerElem>Capture</CaptureMixerElem>` to the `Mic` section of `sof-hda-dsp_sb3/audio_config.xml` and of
  `PCH_sb3_legacy/audio_config.xml` (gbOS86's PCH config uses `Capture`). This is still INFERRED (DRAS's exact required
  set was read from strings, not source); mark it INFERRED in the comment. (I confirmed the field tables across all
  lapis `audio_config.xml` files: the only HDA/SoundWire `Mic` section present carries `CaptureMixerElem`.)
* **Test.** `native/audio/test/run_tests.sh` (differs between the build copy and the repo copy -- edit **both**): add a
  check that `<soundcard>` in `audio_default.xml` equals `gb_audio.sh`'s `ID=` value (`sofhdadsp`), and that every
  non-HDMI `Mic`/`Headphone`/`Speaker`/`InternalMic` section in `sof-hda-dsp_sb3/audio_config.xml` is a superset of the
  fields in lapis's `sof-soundwire` section of the same name (the integrator supplies the extracted reference, or the
  test hard-codes the field list from this plan). Keep the shellcheck-clean, mksh+toybox dual run.

### 5.2 Item 9 -- touchpad IDC `Change_Timeout`

`C:\GB-build\native\hw-gap-audit\tree\usr\idc\Microsoft_Surface_045E_09AF_Touchpad.idc` (the repo copy is identical --
`diff -rq` shows the trees differ only by `firmware/rtl_nic`, so edit the build copy; the re-sync republishes it). Add:
```
# Android holds motion for gestureProp.Change_Timeout (200 ms default) after touchdown, so quick flicks never move the
# cursor. gbos-x86 issue #17 confirmed 0.02 fixes it on lapis (Android 17). Credit: github.com/linuxkid473/gbos-x86 #17.
gestureProp.Change_Timeout = 0.02
```
Do NOT add `Drumroll_Suppression_Enable` (never tested on its own -- the reporter said only Change_Timeout mattered).
Extend `native/hw-gap-audit/test/check_input_files.py` (it already validates IDC `key = value` grammar): the new line
parses as a float-valued property; add it to the expected content for that file. Add a DEVICE-STATUS row-19 checklist
note (docs below).

### 5.3 Item 13 -- gb_slotsync KMI/vermagic safety check before switching the default slot

`C:\GB-build\mouse\gb_slotsync.c` + repo `guest/gb_slotsync.c` (identical today) + `guest/test/test_gb_slotsync.sh`.
Today `sync_once()` switches the systemd-boot default to the slot an OTA completed into, after reading only the GPT
vbmeta attributes. [W3R-08] ~~Extract that slot's vendor ramdisk, find a representative `.ko`, compare vermagic~~ --
**not implementable as written**: the vendor ramdisk is LZ4 (`vendor_ramdisk.lz4`, k14 K-06), and gb_slotsync has no
LZ4 or cpio code (it only concatenates). Also, a hold placed before `build_slot()`/`select_entry()` would leave **no**
`gb-<x>.conf`, so "can be chosen manually" was false. Corrected design:

* **Source of truth = the target slot's GKI.** Lapis's vendor modules for build X are built against the GKI in
  build X's `boot_<x>`. `boot_<x>` (by-name, read-only) is boot header v4 (`ANDROID!`, kernel at +4096), and the
  kernel is a bzImage whose setup header carries `HdrS` at +0x202 and a `kernel_version` pointer (u16) at +0x20E; the
  string lies at `4096 + 0x200 + ptr`. Verified this session on both lapis slots:
  `6.18.21-android17-5-g3938c8d97142-ab16302624 (kleaf@build-host) #1 SMP PREEMPT ...`. New
  `static int gki_release(char s, char *out, size_t n)` reads only the first 64 KiB of `boot_<s>` (bounds-checked:
  `ptr + 0x200 < 64 KiB - 4096`, printable ASCII, stops at a space or NUL).
* **KMI tuple** = `<maj>.<min>-android<N>-<gen>` parsed from a release (`6.18-android17-5`). Running kernel: `uname(2)`.
* **Policy** (only when `target != cur`): KMI(target GKI) != KMI(uname) -> **HOLD** (reason `KMI ...`). Else
  release(boot_<target>) == release(boot_<cur>) -> **SWITCH** as today (the vendor modules are built against the same
  GKI we already boot with; e.g. the .271 -> .276 OTA kept `ab16302624`). Else -> **HOLD** (reason
  `GKI changed ... (same KMI)`: newer vendor modules may need KMI symbols newer than our kernel). That last case is an
  integrator decision (recorded at R-02); the permissive alternative is SWITCH. A read or parse failure -> HOLD.
* **HOLD semantics (the fail-safe that is actually true):** still `build_slot(target)` and
  `copy_entry(target, cur, "", 1)` (+ `-nvtest`), so "(slot X)" **is** in the systemd-boot menu, but do **not**
  rewrite `loader.conf`. Log the contract-2.6 line (`gb_slotsync: HOLD slot <x>: <reason>; default stays gb-<cur>`) once
  per (target, release) pair (a `static char held[96]`; do not misuse `settled`). The user can pick the entry once.
  If it boots, the existing `target == cur` path makes it the default; if it does not, the old default is still
  there. Notification: kmsg + the gb_bootlog summary only. A new `gb_notify` tag would need INTERFACES 12.1 to be
  extended (integrator decision; not in this wave).
* Test additions in `test_gb_slotsync.sh`: fake `boot_a`/`boot_b` images (printf/dd: header v4 + a setup header with
  `HdrS` and a version string) and a fake `uname` (`GB_SLOTSYNC_TEST_UNAME`): same release -> switch; same KMI with a
  different `-ab` -> HOLD with the entry + initrd written, `loader.conf` untouched, one log line over 3 ticks; KMI
  generation differs -> HOLD; a truncated or garbage `boot_b` -> HOLD, no crash; `gb/gb.cfg` and `gb/overlay/x`
  survive a switch untouched (2.3); `target == cur` behaviour unchanged. Keep the host `-DGB_SLOTSYNC_TEST` build and
  the NDK build note. Credit `gbos-x86/porting.md` (idea only; no code).

Rebuild `gb_slotsync` with the NDK (header comment's clang line) and refresh `C:\GB-x86\inject\slotsync\gb_slotsync`
(PIPELINE packs it via `SLOTSYNC_BIN`; the binary is AUX's output, dropped into the inject dir -- that path is an input
to build_esp, not a file PIPELINE edits, so no ownership clash; coordinate the drop at R-03).

### 5.4 Item 7 -- `install_dualboot.ps1 -UpdateEspOnly` slot-aware + log-preserving

`C:\GB-build\native\dualboot\install_dualboot.ps1` + `README_DUALBOOT.md` + `test\run_image_tests.ps1`/`run_edge_tests.ps1`
(the test harness; gbdisk.cs/.exe need no change, except the optional read-only `dump` verb below [W3R-07]). In the
`if ($UpdateEspOnly)` block (`:188-210` today; re-locate after the wave 2b rebase):

* Read the **installed active slot** before writing. `gbdisk layout`/`Get-GbLayout` already reports partition
  attributes; add a read of `vbmeta_a`/`vbmeta_b` GPT attributes (the same ChromeOS priority/tries/successful bits
  `extract_slot.slot_attrs` decodes: priority 48-51, tries 52-55, successful 56) and compute the booted/active slot.
  **Refuse** (exit 2) when the installed active slot differs from the export `manifest.slot` -- a kernel refresh from
  Windows onto the wrong slot would point the default entry at the old slot over upgraded `/data` for at least one boot
  (INFERRED boot-loop/data risk; gbos-x86 HANDOFF.md:482 says a downgrade needs a data wipe). The PowerShell decode
  must mirror gb_slotsync's `wanted_slot()` (`gb_slotsync.c:200-207`) **bit for bit**, with a shared truth table used
  by both test suites. [W3R-07] Also **refuse** when `wanted_slot` is -1 (ambiguous or unbootable attributes; fail
  closed).
* **[W3R-07] Content check (the slot equality alone is not enough).** The export's `initrd-<slot>.img` is built from
  **lapis.bin's** `init_boot`/`vendor_boot` of `manifest.slot`. If the installed slot was OTA-updated on the device
  (a->b->a, or b refreshed again), the slot letters still match while the ramdisks would be old over a newer
  system/vendor_dlkm. Before writing, run the existing **read-only** verb
  `gbdisk verify --plan <planEsp> --only boot_<s>,init_boot_<s>,vendor_boot_<s>,vbmeta_<s> --source-dir <ExportDir>`
  (`gbdisk.cs` `CmdWrite(true)`, sha256 per partition; attribute-only differences are only noted, which is what we
  want). In a dry run, write the plan to a temporary file so the check still runs. **Refuse** on any mismatch, with: "slot <s> on the disk is not the build this export was made from (it was
  updated on the device). Boot Googlebook instead (gb_slotsync manages slot changes), or rebuild the export from a
  source whose slot <s> holds that same build (`GB_SLOT=<s>` + `build_internal.py --reuse-guids <installed
  manifest>`), then retry." ~~"rebuild the export for the active slot (`--reuse-guids`)"~~ alone would rebuild the
  same stale content. If the installed ESP is readable (next bullet), also refuse when its `loader/loader.conf` default
  is not `gb-<manifest.slot>`.
* **[W3R-07/W3R-27] Not done:** the recommendation's "Better: replace only vmlinuz and gb/inject.gz and invalidate the
  stamp" is **unsafe**. `initrd-<x>.img` embeds `inject.gz` (it is `[inject | vendor | generic | bootconfig]`,
  `gb_slotsync.c` `build_slot`), so the first boot would run the new kernel with the old initrd's modules (vermagic and
  signature mismatch), before gb_slotsync could rebuild anything. Document this in README_DUALBOOT.md.
* **Preserve everything first [W3R-07].** Before writing, save the **whole installed EFI-SYSTEM partition raw**
  (`$state.run_dir\esp-before-<stamp>.img`, 512 MiB, + sha256, read back once). That is the rollback image: write it
  back with `gbdisk write --only EFI-SYSTEM` from a copy of the manifest whose EFI-SYSTEM entry names this file and
  its sha256 (same size as the partition); README_DUALBOOT documents the command. It holds
  `\gblog\`, `\gbinstall\`, `\gb\gb.cfg`, `\gb\overlay\` and gb_slotsync's other-slot entries. Mechanism: either a
  small **read-only** `gbdisk dump --only EFI-SYSTEM --out <file>` verb (AUX may add it to `gbdisk.cs`; it opens the
  disk with `Access.Read` like `verify`), or a raw `FileStream` read of `\\.\PhysicalDrive<N>` at the layout offset
  from PowerShell (elevated; image mode reads the image file). Then extract `\gblog\` + `\gbinstall\` + `\gb\gb.cfg` +
  `\gb\overlay\` as files into `gblog-preserved-<stamp>\` with a per-file sha256 record. On a real disk use the
  temporary-drive-letter path already used for bcdedit (`mountvol <L>: <EspVol>`, read-only use, and
  **`mountvol <L>: /D` in a `finally` before `gbdisk write`**, which locks and dismounts the volume). In image mode use
  a read-only FAT reader on the saved raw image (reuse the FAT parsing `verify_image.py --esp` already has, run with
  `python -I`). If the raw save fails -> refuse (exit 2) unless `-NoEspBackup` is given (for recovering an unreadable
  ESP). If `gb\gb.cfg` or `gb\overlay\` existed, print that the update removes them and where the copies are. Credit
  gbos-x86 Update flow (`gbos86.com/docs/updating`, `install.sh:17-26,38-45`; idea only).
* README_DUALBOOT.md + image tests [W3R-07]: `run_image_tests.ps1` gains cases built with a test-only helper that
  patches the image's GPT entry attributes and the CRCs (`test\set_attrs.py`, run with `python -I`, image files only):
  (1) b active and successful, export for a -> exit 2, image unchanged (sha); (2) one byte of `vendor_boot_a` changed
  -> exit 2 (content check); (3) attributes ambiguous -> exit 2; (4) ESP with `gblog\x.txt`, `gb\gb.cfg` -> after the
  update the raw backup + extracted copies exist with matching sha256, and the new ESP equals the export image;
  (5) the normal path still passes; (6) `wanted_slot` truth table == `test_gb_slotsync.sh`'s. A later ISO "update" /
  "harvest gblog" tool reuses the same rules (note it, do not build it now). Keep the file free of machine-specific
  phrases (the re-sync scrubs it; keep results as counts only).

### 5.5 Item 14 -- firmware pinning/coverage check tool

New `C:\GB-build\native\nouveau-kernel\tools\fw_coverage.py` (ours, Apache-2.0) + repo. Read-only; run on the builder.
It: runs `modinfo -F firmware` on our native modules (`inject/native/modules/*.ko`) and on lapis's vendor_dlkm modules
(`iwlwifi`, `btintel`, SOF), follows **WHENCE `Link:`** targets so the staged name (FAT has no symlinks) is checked
under the link name, and compares the requested set with what is staged under `inject/native/firmware/` (and
`native/hw-gap-audit/tree/firmware/rtl_nic/`). Output: a table in the style of
`native/nouveau-kernel/firmware/tu117_fw_table.txt` recording each requested name -> real linux-firmware path + source
tag + sha256, and a list of **requested-but-missing** and **staged-but-unrequested** names. For the AX201 at USB
`8087:0026`: v6.18 `btintel.c` (re-read this session) uses `intel/ibt-%u-%u-%u.sfi` for `hw_variant 0x11..0x14` (HrP
etc., the legacy path) via `btintel_get_fw_name` and the TLV `intel/ibt-%04x-%04x` path for newer parts; our staged
`intel/ibt-19-*` is ~~the `hw_variant 0x19` (Slr-F)~~ **hw_variant 0x13 (HrP), printed in decimal by `%u`**
(`btintel.c:1956-1962`) [W3R-20]: the AX201 legacy-name family. The tool flags whether the device actually wants
`ibt-19-*` (our stage) or a TLV `ibt-0040-*` name (gbOS86 saw `ibt-0040-1050.sfi` on another laptop), so a first-boot
`Direct firmware load ... failed` is anticipated. Add a `verify_image.py` build-time hook? -- that is a PIPELINE file;
instead the tool runs standalone and its table is recorded in `docs/INPUTS.md` (AUX). Pin a source tag + sha256 for
i915/iwlwifi/ibt/SOF like the TU117 table already does. Firmware stays out of the repo. Credit
`gbos-x86/tools/fetch_firmware.sh:72-80`, `HANDOFF.md:211-213` (idea only).

### 5.6 Item 18 -- HWUI-on-Vulkan abort signature in the NVK self-test and docs

`C:\GB-build\native\nouveau-kernel\native\gb_dgpu_report.sh` (pinned: that file's NVK self-test report section
[W3R-18]; it is an ESP-lock holder that wave 2b touches, so rebase at R-01) + `docs/NVIDIA.md`. Add a
`timeout 20 logcat -d -b all | grep -c -F 'queueFamilyProperties.queueCount < kRequestedQueueCount'` (2.4; `-d`, see
[W3R-14]) to the
NVK self-test report's "what to test first" section and a one-line note in NVIDIA.md that this abort means HWUI landed
on a one-queue-per-family Vulkan backend. ~~(fix: `ro.hwui.use_vulkan=false`)~~ [W3R-15]: **do not** recommend that.
INTERFACES v1.2 section 4 (row `debug.hwui.renderer`, `ro.hwui.use_vulkan`) says the native rc/build.prop MUST NOT
set or override them, and the ANGLE-HWUI guard (14.3) relies on that. On our stack HWUI must never get NVK (engine
rule 14.2), so a hit means a shim bug or an ANV change. The note says: report it with the gblog, and the supported
workaround is the Intel mode of the gfx switch (INTERFACES 4). Both props are in `_BAD_PROP`, so `GB_PROP` refuses
them (2.3). Diagnostics only. Credit `gbos-x86/usb/hook.sh:489-492`.

### 5.7 Items 16, 17 -- docs and issue template

* **16 (Google apps / recovery hashes):** add a "Google apps" section to `README.md` or `docs/NATIVE.md`: sign-in and
  Play installs work; AICore/Magic-pointer/on-device Gemini are refused (no key attestation: orange boot state,
  permissive SELinux, software KeyMint) -- INFERRED; fs-verity needed for Play updates (item 4). Add to
  `docs/INPUTS.md:22` the `.271` zip sha256 (ours, re-hashed this session:
  `5bc64936c11d9eb268da03063a443b8fd0bbc0f84d266f9a014231653939f247`, 7,728,873,477 bytes -- matches gbos-x86 git
  `03a6a46:lapis/recovery.zip.sha256`) and the `.276` hash **marked third-party** (gbos-x86's `97980097...`). **Never
  ship Google binaries** -- hashes and expectations only. Credit gbos-x86 `HANDOFF.md:159-167,193-200`.
* **17 (troubleshooting table + issue template):** new `.github/ISSUE_TEMPLATE/native-boot-report.md` (attach `gblog\`,
  the boot entry used, kernel build, slot, a redaction reminder) and a symptom->log->boot-option table in
  `docs/FLASHING.md`: black screen after the menu -> NX_COMPAT (`docs/NATIVE.md`); no keyboard -> 8250/Aggregator
  check (item 1; on the SB3 the base keyboard/touchpad, batteries, charger and detach all ride on the SAM UART); no
  sound -> DRAS card id (item 2); hang with the base attached -> `gb.nouveau=0`; grey/washed display -> the
  `drmhwc GAMMA_LUT` grep, then `i915.gb_ignore_user_gamma=3` typed into the systemd-boot editor (it works only
  because GBINIT forwards that one token, 4.6) or `GB_MODOPTS_i915=gb_ignore_user_gamma=3` in `gb\gb.cfg` with
  `gb.overlay=1` (item 10, [W3R-09]; `3` keeps CTM-based accessibility colour modes, `7` = gbOS86 #29); a boot that
  hangs after a gb.cfg change -> pick the normal entry (no `gb.overlay=1`) or rename `gb\gb.cfg` from Windows (item 12).
  The table must not suggest `<module>.<param>=` cmdline tokens for any other insmod-loaded module (they are ignored).
  Structure credited to `gbos86.com/docs/logs` and `/docs/install`.
* **DEVICE-STATUS** row 19 note for the touchpad `Change_Timeout` (item 9); row 12/UART note for item 1; row for the
  i915 white-veil check (item 10).

### 5.8 Item 10/16/17 -- LICENSES.md and README credits
`LICENSES.md`: the new `kernel/patches/0005-i915-*.patch` is ours (GPL-2.0, derived from Linux); the `fw_coverage.py`
tool, the DRAS/IDC config edits, the bring-up parser, the slotsync KMI check and the dual-boot update changes are ours
(Apache-2.0). No gbos-x86 code is included anywhere. `README.md` already lists gbOS86 under Acknowledgements; add the
specific wave-3 credits (8250/Aggregator finding, DRAS card-id rule, #UD review, touchpad `Change_Timeout`, white-veil
cause) as comments in the touched files and a line in the relevant docs.

---

## 6. Build and test order (after wave 2b's outputs are published; the user's VM stays OFF)

Pre-flight R-00 (read-only): wave 2b's rebuilt USB image/export/ISO exist and pass `verify_image.py`; `/build` and
`/build/dist14` present; host C: >= 15 GB and D: >= 3 GB free (section 8); `vmrun -T ws list` does **not** list the
user's `lapis-boot.vmx` -- confirm it is **not** running and must stay off and unchanged. [W3R-24] `vmrun` is not on
PATH: `"C:\Program Files\VMware\VMware Workstation\vmrun.exe" -T ws list`. On 2026-10-26 it listed only
`C:\GB-build\builder2.vmx`, i.e. the user VM was off.

| Step | What | Pass |
|---|---|---|
| R-00 | pre-flight, read-only | floors met; user VM off |
| R-01 | backups: each implementer copies its files to `C:\GB-x86\archive\pre-w3\...` (and builder `/build/k15` for kbuild15) | copies' sha recorded |
| R-02 | **KERNEL / PIPELINE / AUX in parallel** on disjoint files (sections 3/4/5) | unit/host tests green: 3.4, 4.8, 5.x per-item tests |
| R-03 | integration gate: each lists changed files with sha256; intersection empty; `gb_slotsync` binary dropped into `inject\slotsync\` | no file edited by two |
| R-04 | **k15 build** (KERNEL): `kbuild15_native.sh` on the builder; KAT-0 + `vnni_kat_hostcheck` first (R-02) | `/build/dist15` + `k15_release.txt`; k15_accept **K-01..K-14** PASS [W3R-13]; `df /build` recorded [W3R-26] |
| R-05 | **stage k15** (PIPELINE): `stage_kernel.py k15 --dist /build/dist15` | vermagics match, signatures valid |
| R-06 | **ESP-only + determinism** (PIPELINE): `build_usb.py --esp-only` with a fixed super-guid, then `verify_image.py --esp ... --krel k15_release.txt`; rebuild to a 2nd file with `GB_NO_BUILDER=1`, compare sha | `RESULT: OK`; identical sha; V16 has `8250.nr_uarts`; no `gb/gb.cfg`/`gb/overlay` in the artifact and no `gb.overlay=` in any entry [W3R-17]; native fstab has `fsverity`, VM fstab unchanged [W3R-06] |
| R-07 | **USB image** (PIPELINE): `build_usb.py --out ...usb.new.img --sparse` -> `.part` -> verify -> rename; sidecar | size unchanged (28,814,885,376); `RESULT: OK`; C: drop <= ~8 GiB (sparse) |
| R-08 | tree diff + budgets: new vs old USB native tree = exactly the expected differences [W3R-22]: item 2 (audio xml), 4 (native fstab), 8 (gb_bootlog), 9 (touchpad IDC), 10 (GBINIT forwarder), 11 (gb.rc), 12 (GBINIT call + `/gbbringup.sh`), 13 (`gb_slotsync` binary), **k15 restage** (every `.ko`, `modules.load` line 1, vmlinuz). Item 1 changes the ESP entries, not the tree. The VM tree differs only by k15 modules/kernel | only the expected diffs |
| R-09 | **export** (PIPELINE/AUX): `build_internal.py` -> fresh export (new GUIDs, `--new-export`); its `manifest.cmdline` now shows `8250.nr_uarts` | cross-check 8 `8_esp_tree` ok |
| R-10 | **ISO** (PIPELINE): `build_iso.py` with the new `--usb-sha256`, `VERIFIED_USB_SHA256`/`USB_SHA` updated | build done; export_check ok |
| R-11 | [W3R-16] **FULL ISO suite, always** (`C:\GB-x86\iso\test\run_all.py`, every test id), as the task requires. ~~"subset suffices"~~ is withdrawn. The suite's own `MIN_FREE_AFTER` guard may mark a test `SKIPPED (space)`; any such skip must be re-run after freeing space, not accepted | every id PASS |
| R-12 | **VM-1 native smoke** (throwaway VM, k15 USB; recreate `D:\gb-w2test\vm\` with `C:\GB-build\native\dgpu\1b\evidence\w2\tools\mkvm.ps1` + `vmguard.py` [W3R-24]): boot a sparse copy of the new USB image ~18 min, extract `gblog`; check the new `== summary` block, no regression vs the wave-2 AC-701..AC-718. [W3R-19/21] Plus: native dmesg has `Serial: 8250/16550 driver, 4 ports` and `ttyS0 at I/O 0x3f8` (VMware has a real 16550, which proves the token takes effect natively; SAM itself "n/a (VMware)"); `getprop desktop.apgetty.enable` = 0 and `init.svc.apgetty` is not `running`/`restarting`; `/sys/kernel/gb_vnni/summary` readable; `/odm/gbinit_bringup.log` absent (feature off by default); item 4: the gblog dmesg shows fs_mgr's `Enabling ext4 verity on .../userdata` once on the first boot and no `FS_STAT_ENABLE_VERITY_FAILED`/`Failed to run tune2fs`, and `/sys/fs/ext4/features/verity` exists | the wave-2 native ACs still PASS; all listed checks |
| R-12b | [W3R-25] **VM-1b bring-up end-to-end** (same throwaway disk, ESP rewritten via a test-only `build_usb.py --esp-only` variant below `D:\gb-w2test\`): the entry carries `gb.overlay=1`; `gb\gb.cfg` = magic + `GB_PROP_1=debug.gb.bringup_test=1` + `GB_PROP_2=ro.debuggable=1` (denied) + `GB_MODOPTS_i915=gb_ignore_user_gamma=3` (module absent in VMware: logged, harmless); overlay `usr/idc/GB_Test.idc` (allowed) + `etc/init/gb_evil.rc` (denied) | `getprop debug.gb.bringup_test` = 1; `ro.debuggable` unchanged; `/odm/usr/idc/GB_Test.idc` present, 0644 root; no `/odm/etc/init/gb_evil.rc`; the log lists both refusals; boot time increase < 15 s |
| R-13 | **VM-2 k15 vm-test boot + KAT** (section 6.4) | k15 banner; `GB_VNNI_KAT: PASS` with only the documented SKIPs; exact counter deltas incl. `irqon == emulated` [W3R-10/11]; no `BUG:`/`WARNING:`/`Oops` in the serial log |
| R-14 | final acceptance + evidence to `C:\GB-build\native\dgpu\w3\evidence\`; builder scratch removed; throwaway VM files deleted | every check PASS |
| R-15 | repo re-sync (AUX): run the collect script into the repo working copy; PII scan 0; nothing pushed | 0 PII findings |

[W3R-16] Order note: the order of record is the task's (stage k15 -> USB -> verify -> export -> ISO -> FULL ISO
suite -> VM-1 -> VM-2+KAT). Because a VM-2 KAT failure forces a kernel rebuild and therefore a new USB, export and
ISO, the integrator *may* also run R-13 once directly after R-07 as an early fail-fast. That early run does not
replace R-13.

Throwaway-VM rules (unchanged from wave 2, section 7.0 of WAVE2_PLAN_v2): everything under `D:\gb-w2test\`; the
`vmguard.py` pre-start guard; one VM at a time; `vmrun` only on `D:\gb-w2test\vm\gbw2.vmx` and the builder suspend;
**never** `lapis-boot.vmx`, never `lapis.bin`. Suspend the builder for VM-1/VM-2 if host RAM is tight (host free was
11.2 GB this session with the user VM off and the builder up -- enough for one 4 GB throwaway VM without suspending the
builder, but suspend it if a k15 build is still resident).

### 6.4 VMware KAT (R-13) -- the required known-answer test

* **Image.** PIPELINE adds a **test-only** `--vm-test-kat` flag to `build_usb.py` + a `vm_test_kat_cpio()` to
  `build_esp.py`, mirroring `--vm-test-gfx` exactly (output confined below `D:\gb-w2test\`, never `lapis.bin`, never the
  shipped VM tree; a `verify_image.py` kind `vmtest-kat`). The KAT tree = the VM tree plus: `/odm/vnni_kat` (from
  `C:\GB-x86\inject\kat\`, contract 2.7), `/odm/lib/modules/{zsmalloc,zram}.ko` (the k15-staged copies from
  `inject\native\modules\`, **this throwaway image only**; this does NOT change the shipped VM tree, so item 5 stays
  out of scope), and a test-only rc fragment appended to the KAT tree's `gb.rc`:
  ```
  on property:sys.boot_completed=1
      insmod /odm/lib/modules/zsmalloc.ko
      insmod /odm/lib/modules/zram.ko
      write /sys/block/zram0/disksize 67108864
      start gb_vnni_kat
  service gb_vnni_kat /system/bin/sh /odm/gb_vnni_kat.sh
      user root
      seclabel u:r:shell:s0
      oneshot
      disabled
  ```
  `/odm/gb_vnni_kat.sh` (test-only, 0755, KAT tree only) runs `mkswap /dev/block/zram0 && swapon /dev/block/zram0`,
  then `/odm/vnni_kat --all` with every output line copied to `/dev/kmsg`, then `cat /sys/kernel/gb_vnni/summary`
  into `/dev/kmsg`. The shell code lives in the script because `$` in an rc line is a property reference. The module
  loads and the disksize write are init builtins (the init domain may load modules). `user root` +
  `seclabel u:r:shell:s0` follow the existing GB_RC service pattern (gbOS86: an exec/service needs an explicit
  `seclabel`). The bootconfig sets `androidboot.selinux=permissive` (`build_esp.py:1906`). The harness reads the
  results from the VM serial log and via `adb ... dmesg`.
* **Boot.** Recreate the throwaway VM folder (`mkvm.ps1`, `vmguard.py`, [W3R-24]). Build
  `build_usb.py --vm-test --vm-test-kat --sparse --userdata-gib 8 --out D:\gb-w2test\vm\disk.img`. The host is the
  Ice Lake SB3, so VMware passes AVX-512 (incl. AVX512_VNNI) through and AVX_VNNI does not exist; `gbw2.vmx` has no
  CPUID masks, and the KAT's env asserts confirm it at run time. Boot, wait for `gb_adb`, read the serial log.
* **Pass:** `GB_VNNI_KAT: PASS <n> cases` with VEX(emulated) == EVEX(native) for every case, with only these SKIPs
  allowed: `sse_init`/`ymm_init` when XINUSE cannot be shown (KAT-0 covers them). Required: the MAXVL/upper-zero and
  register-preservation cases, the negative encodings (SIGILL), the page-end instruction, the preemption/signal stress,
  and the fresh-page and zram-swapped-page memory-operand cases with their fault proofs. [W3R-10] The **exact counter
  deltas** of 3.5 must hold (`irqon == emulated`, `efault` = the bad-address cases); ~~`faults > 0`~~ is withdrawn
  (not measurable). No `BUG:`, `WARNING:`, `Oops`, `sleeping function` or `Bad FPU state` in the serial log. Any
  `GB_VNNI_KAT: FAIL`, `ENV` or oops fails R-13.
* **Host self-check** first (KERNEL, R-02): `vnni_kat_hostcheck` on the builder (has AVX512_VNNI) proves the EVEX path
  equals a scalar C reference, so a VM FAIL points at the emulator, not the harness; KAT-0 (3.5) proves the emulator
  core logic incl. the init-state rules and the negative control (K-14).

---

## 7. Rebuild/export/ISO coupling (why a full chain is needed)

[W3R-22] Items 9 (IDC), 10 (GBINIT forwarder) and 13 (`gb_slotsync` binary) also change the native tree; the
argument below is unchanged.
Item 1 changes the native kernel cmdline -> the USB image's ESP entries change -> the export's entries change ->
cross-check 8 fails against the old export -> `--new-export` with new GUIDs -> a new ISO (and `VERIFIED_USB_SHA256`/
`USB_SHA`). Items 2/4/8/11/12 change the native `/odm` tree (audio config, fstab, bootlog script, gb.rc, gbinit) ->
the native cpio and `gb/inject.gz` change -> new `initrd-<slot>.img` and USB sha. Item 3/10 change the kernel ->
`vmlinuz` md5 changes -> `modules.load` line 1 (V19) and every staged `.ko` vermagic change -> full restage.
So the order is fixed: **stage k15 -> USB -> verify -> export -> ISO -> ISO suite -> VM-1 -> VM-2+KAT**, exactly as the
table. The previous ISO/USB/export are held in `prev\` until the user accepts (as wave 2 did).

---

## 8. Disk budgets (floors: C: >= 15 GB, D: >= 3 GB; user VM OFF and unchanged)

Measured this session: C: 82.2 GB free, D: 62 GB free, builder `/` 27 GB, `/build` 11-12 GB; host RAM free 11.2 GB
(user VM off). Peaks during wave 3:

| Moment | C: | D: | builder `/build` |
|---|---|---|---|
| k15 build (R-04) | ~0 | ~0 | `/build/dist15` ~0.9 GB + Kleaf cache churn (shared `--config=local`, recompiles traps.c/i915, keeps nouveau objects); `/build` stays > 5 GB (currently 11-12 GB) |
| USB new image sparse (R-07) | about -8 GB (-> ~74 GB) | - | - |
| export (R-09) | ~ -0.5 GB temp | +0.5 GB (export) | - |
| ISO to `D:\GB-iso\new\` (R-10) | ~ -13 GB temp during build, released after | about -7 GB (new ISO) + keep `prev\` (~16 GB already there) | - |
| ISO suite (R-11) | - | test disks sparse, <= ~13.5 GB physical at a time under `D:\GB-iso\test\` (its own `MIN_FREE_AFTER = 8 GiB` guard skips with `SKIPPED (space)` if D: would drop too far) | test media in `/var/tmp/gbiso/test`, removed after |
| VM-1/VM-2 (R-12/R-13) | one sparse VM image at a time under `D:\gb-w2test\`, ~8 GB, deleted after each | - | - |

[W3R-26] Re-measured by the reviewer on 2026-10-26: C: 82.2 GB, D: 62 GB free (matches the plan), builder `/` 27 GB,
**`/build` 12 GB free at 90 % use**, which is only ~2 GB above kbuild's 10 GiB preflight floor (see 3.3). The item-7
raw ESP backup costs 512 MiB per `-UpdateEspOnly` run in the run dir (C:), and the R-12b ESP rewrite works on the
throwaway disk only.
All floors hold with wide margin. If D: tightens because of the 16 GB `D:\GB-iso\prev\` held from wave 2, the integrator
may delete it only after the user accepts wave 2 (do not delete it in wave 3). The user's VM (`lapis-boot.vmx`, 12 GB
disk images, `lapis.bin` 49 GB) is **never** started, copied or written; `lapis.bin` is only read (read-only) by
`carve_lp.py`/`extract_slot.py`/`verify_image.py` as today.

---

## 9. Docs/repo changes and gbOS86 credits (summary)

| File | Item | Credit |
|---|---|---|
| `build_usb.py` native_cmdline docstring | 1 | gbos-x86 HANDOFF.md:423 (Surface Laptop 7) |
| `audio_default.xml` (x2) comment, `sof-hda-dsp_sb3`/`PCH_sb3_legacy` Mic | 2 | AGENTS.md:33-34, HANDOFF.md:96-104 |
| `kernel/patches/0002-*.patch` header, `kernel/K15.md` | 3 | HANDOFF.md:59-62 (review only; code ours) |
| `inject/fstab.android-desktop`, `docs/INPUTS.md`/`OTA.md` | 4 | issue #17 |
| `install_dualboot.ps1`, `README_DUALBOOT.md` | 7 | gbos86.com/docs/updating, install.sh:17-45 |
| `build_esp.py` snapshot, `docs/FLASHING.md` redaction note | 8 | hwdetect.sh section list (names only) |
| `...Touchpad.idc` comment, DEVICE-STATUS row 19 | 9 | issue #17 |
| `kernel/patches/0005-i915-*.patch`, `docs/NVIDIA.md`/DEVICE-STATUS | 10 | HANDOFF.md:112-135 |
| `build_esp.py` GB_RC_NATIVE | 11 | hook.sh:611-612 |
| `build_esp.py` GBINIT, `docs/NATIVE.md`/`FLASHING.md` | 12 | gbos86.com/docs/how-it-works (design idea) |
| `gb_slotsync.c`, `docs/OTA.md` | 13 | porting.md (idea) |
| `tools/fw_coverage.py`, `docs/INPUTS.md` | 14 | fetch_firmware.sh:72-80, HANDOFF.md:211-213 |
| `README.md`/`docs/NATIVE.md`, `docs/INPUTS.md` hashes | 16 | HANDOFF.md:159-167,193-200 |
| `.github/ISSUE_TEMPLATE/native-boot-report.md`, `docs/FLASHING.md` table | 17 | gbos86.com/docs/logs,install |
| NVK self-test report, `docs/NVIDIA.md` | 18 | hook.sh:489-492 |
| `LICENSES.md` | all | our code only; no gbos-x86 code copied |

Re-sync (R-15): the existing `collect.py` publishes the repo; the new files are `kernel/patches/0005-*.patch`,
`kernel/K15.md`, `.github/ISSUE_TEMPLATE/native-boot-report.md` (the repo has no `.github/` yet), `tools/fw_coverage.py`
(confirm in collect.py whether it maps to repo `tools/` or `native/nouveau-kernel/tools/` and use one path in every
doc), [W3R-18] plus `kernel/tools/vnni_kat.c`, `kernel/tools/test_gb_vnni_core.c` and the KAT host harness
(sources only, no `vnni_kat` binary), and, if 3.1 step 6 is taken, `gb_vnni_core.h` inside the rewritten 0002 patch.
Adjust the collect exclusions/inclusions and the README_DUALBOOT phrase check. Binaries (`gb_slotsync`, `vnni_kat`,
`.ko`, firmware) stay excluded.
Nothing is pushed without the user.

---

## 10. Not in scope (explicit, so an implementer does not drift)

* **5** VM zram -- the shipped VM tree (`stage_kernel.VM_MODS`) is **not** changed; only the throwaway KAT image loads
  zram as a test artifact (6.4). The user's live VM needs an OK first.
* **6** libz adler32 patcher -- deferred; measure first with the new counter (item 3) on hardware.
* **15** AVX-IFMA / VNNI-INT8 isa-emu port -- deferred; measure first (the counter will show if IFMA/INT8 sites trap).
* **19** software Gatekeeper (`soft_gk.c`, Apache-2.0) -- user decision; not touched.
* The user's **VM**, `lapis.bin`, and Google binaries -- never started/modified/shipped.

## 11. Gate

This plan is written while wave 2b is still reviewing the lock fixes in the same files (`build_esp.py`, `verify_image.py`,
`gb_slotsync.c`, `gb_audio.sh`/`gb_audiodiag.sh`, the ISO tree; [W3R-18] also `gb_dgpu_report.sh`, an ESP-lock holder
that item 18 edits, the `tests\w2a`/`w2b` suites, and possibly `install_dualboot.ps1`/`gb_common.ps1`). Every
implementer diffs each owned file against both its R-01 snapshot and wave 2b's published version before editing.
Integrator decisions to record at R-02 [W3R-08/17/18]: the item-13 HOLD policy for "same KMI, new GKI release"; the
INTERFACES 5.4 exception for the rdinit read-only ESP mount; and whether a `gb_notify` tag is wanted for item 13
(INTERFACES 12.1). Wave 3 edits **nothing** until: (a) wave 2b has
published its rebuilt USB/export/ISO and they pass `verify_image.py` and the ISO subset; (b) the integrator confirms the
R-02 gate. At R-01 each implementer re-reads its baseline files (they will have wave 2b's r3 lock protocol already) and
rebases these instructions onto that text -- in particular `build_esp.py` GB_BOOTLOG_SH and GBINIT, `gb_slotsync.c`
`sync_once`/lock code, and the audio scripts, which wave 2b touched.
