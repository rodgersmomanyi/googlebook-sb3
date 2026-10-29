# INTEGRATE_REVIEW.md: wave 5 VNNI libz patch, read-only adversarial review

Date: 2026-10-26 (~20:15). Scope: the wave 5 integrate step as built (`INTEGRATION.md` section 6). Read-only: I edited
no pipeline, test, inject or repo file. No VM was started and no image was built. The only thing I ran was a small
replication of one `tests\w5` step against the current `collect.py`; it wrote only into the review's own scratch folder.
The measurement agent runs in parallel, so I kept host load low and did not run the full suites.

Reviewed files (sha256; the pipeline files are unchanged since the integrate step's `w5\evidence\CHANGED_FILES.sha256`):

| file | sha256 |
|---|---|
| `C:\GB-x86\build_esp.py` | `bd6109a6...ce1c` |
| `C:\GB-x86\verify_image.py` | `8c16cf20...33cf` |
| `C:\GB-x86\vnni_prepare.py` | `7782d159...8726` |
| `C:\GB-x86\build_usb.py` | `65dea046...1b99` |
| `C:\GB-x86\tests\w5\test_w5.py` | `33a8cf25...cc74` |
| `C:\GB-x86\tests\w2b\test_verify_image.py` | `362d1646...2562` |
| `native\vnni-perf\patcher\vnni_patch.py` | `df622626...e791` |
| repo-stage `collect.py` (planning-session scratchpad) | `2cb0d79b...2766` (**changed after the integrate step**, was `cf9898d1...`) |

Runtime facts used below come from earlier VM boot logs of this same build (`native\dgpu\1b\evidence\w2fix\vm1\gblog\`,
`native\dgpu\w3\evidence\build\vm1\gblog\`, `...\vm2\serial0.log`):
`ro.boot.selinux=permissive`, AVC lines with `permissive=1`, `ro.build.type=user`, `ro.apex.updatable=true`,
`ro.actionable_compatible_property.enabled=true`, `ro.system.build.fingerprint=Android/desktop_system/desktop:17/CL3B.260622.276.R4/16658739:user/release-keys`,
`ro.vendor.build.fingerprint=ASUS/lapis/lapis:17/CL3B.260622.276.R4/16658739:user/release-keys`. In every boot,
`init: Command 'enter_default_mount_ns' action=post-fs-data (/system/etc/init/hw/init.rc:696) ... failed: Failed to switch to default mount namespace.: Invalid argument`
appears. The logs also show `init: Overriding previous property ...` lines from later prop files, and
`avc: denied { mounton } ... scontext=u:r:init:s0 ... tclass=file permissive=1` for the existing `/odm` binds over
`/system/bin` files.

## Verdict

No HIGH finding. The design holds. Every bind is gated on the exact build fingerprint, and every failure path I could
construct means "no bind": the stock libz then stays emulated, which is correct, only slower. Three MEDIUM findings
should be fixed in the Apply step:

1. **F1:** the read-only remount does not reach other mount namespaces, so the measurement's "ro" acceptance check
   is ill-defined.
2. **F2:** `tests\w5` currently crashes in its REPO step, because `collect.py` changed after the integrate step.
3. **F3:** neither guard (`_check_vnni`, V31) refuses a `libz.so` *file* outside `/odm/vnni`, for example in the
   `/vendor` overlay's upper layer. That is the one ungated path the design forbids.

There are also seven LOW findings.

| id | sev | where | one line |
|---|---|---|---|
| F1 | MEDIUM | `build_esp.py:1444`, `vnni_patch.py:275-285`, `INTEGRATION.md:74-75,180,279`, `verify_image.py:1972` | `remount bind ro` is per mount namespace; copies propagated at bind time stay rw; the source inode stays writable through the rw `/odm` tmpfs anyway |
| F2 | MEDIUM | `tests\w5\test_w5.py:438-451` (vs `collect.py` `put()` line 64) | REPO execs only `put()`; `put()` now calls `_hard_refuse` -> `NameError`, the suite ends in a traceback (exit 1) |
| F3 | MEDIUM | `build_esp.py:2452-2476`, `verify_image.py:1990,2006` | no check that no `libz.so` file (or whiteout) exists outside `vnni/<id>/` in a tree, e.g. `vup/lib64/libz.so` |
| F4 | LOW | `build_esp.py:446-452` (bring-up deny list), V31 | `/odm/etc/build.prop` can override the gate properties (ESP `GB_PROP_n`, component `build_prop`) |
| F5 | LOW | `verify_image.py:166,1944,1966`, `build_esp.py:2472` | the gate regexes accept `*` and any token; V31 ignores files directly under `vnni/` |
| F6 | LOW | `build_esp.py:2465-2466`, `verify_image.py:2027-2029` | the order check is positional only; it does not check that the overlay runs in the same `early-init` trigger |
| F7 | LOW | `tests\w5\test_w5.py` (PREP, V31) | prepare's post-patch guards are not mutation-tested; V31 mutations live mostly in `tests\w2b` |
| F8 | LOW | user docs / `build_esp.py:2865` | kill switch is per boot via the systemd-boot editor only; not documented for users |
| F9 | LOW | `build_esp.py:1446-1483`, Apply step | no check that `inject\vnni\<id>` still matches lapis.bin's current boot slot (an OTA makes the patch silently inert) |
| F10 | LOW | repo-stage `collect.py` `_hard_refuse` | magic checks only at offset 0; uncompressed tar/cpio, lz4, bzip2, zlib not refused for explicit `put()` rules |

---

## Findings

### F1 (MEDIUM): the read-only remount is per namespace; "ro" cannot be the acceptance criterion as written

**Where:** `C:\GB-x86\build_esp.py:1444` (`mount none none {dst} remount bind ro`),
`native\vnni-perf\patcher\vnni_patch.py:275-285`. The docstring there says a writable inode under every process's libz
"would let a write change code in all processes"; the remount is meant to prevent that. Also
`INTEGRATION.md:74-75`, `:180`, `:279` ("the binds in `/proc/self/mountinfo` (ro)"), and `verify_image.py:1972`.

**Evidence and reasoning:**
* SetupMountNamespaces makes `/` `MS_SHARED|MS_REC` before `unshare(CLONE_NEWNS)`, so a bind that init makes later
  propagates to the peer mount in the other namespace. That is why the existing `/odm` binds over `/system/bin/*` and
  `/vendor/etc/*` work for later services. The propagated copy is cloned **at bind time**, while it is still rw.
  `MS_REMOUNT|MS_BIND|MS_RDONLY` (kernel `do_reconfigure_mnt`) changes only the mount it is applied to. Per-mount
  flags never propagate.
* On this build, init itself never leaves the bootstrap namespace (`enter_default_mount_ns` fails with EINVAL in every
  logged boot). Services that init forks may still `setns` into the default namespace in the child, which is
  single-threaded. So the bootstrap copy is ro and the default-namespace copy is rw. Which copy zygote, system_server,
  the apps and the root console see depends on their namespace.
* Either way, the bound inode is the tmpfs file `/odm/vnni/<id>/.../libz.so`. `/odm` is a rw tmpfs in every
  namespace, so root can rewrite the code through that path whatever the bind's flags. `INTEGRATION.md:180` already
  says the source stays writable. The docstring's protection is therefore not delivered. Root on a permissive
  device already controls everything, so the security impact is nil; the problem is the acceptance check and the
  claims in the docs.

**Fix (image):**
1. *Now (measure/Apply):* make the acceptance check per namespace. Record
   `readlink /proc/1/ns/mnt /proc/self/ns/mnt /proc/$(pidof zygote64)/ns/mnt` and
   `grep libz /proc/1/mountinfo /proc/$(pidof zygote64)/mountinfo`. Accept: the bind is present in both namespaces;
   `ro` in init's (bootstrap) namespace; `rw` allowed in the default namespace. Change `INTEGRATION.md`
   sections 0/5/6, the V31 docstring and the `vnni_patch.rc_block` docstring to say: "read-only in init's mount
   namespace only; the inode stays writable through the rw `/odm` tmpfs (same trust as every other `/odm`
   injection)". The rc text, V31 and the tests can stay byte-identical.
2. *Optional hardening (a later wave, builder-tested first):* in GBINIT, give `/odm/vnni/<id>/system` and `.../vendor`
   their own tmpfs mounts with `context=u:object_r:system_lib_file:s0` / `vendor_file` (SELinux applies `context=` at
   policy load). Copy the files in, then `mount -o remount,ro`. All of this happens before Android init creates the
   second namespace, so every later copy and every bind made from it inherits ro. The rc actions then drop both
   `restorecon` lines, which would fail with EROFS, and the remount line.

### F2 (MEDIUM): `tests\w5` currently crashes in REPO (fresh `collect.py`)

**Where:** `C:\GB-x86\tests\w5\test_w5.py:438-451`. `repo()` compiles only the AST node of `put()` into a namespace
holding `os`, `DEST`, `LOG` and `X86`. Since the integrate step, `collect.py`'s `put()` (line 64) calls
`_hard_refuse(rel, data)`, which uses `HARD_NAMES`, `HARD_EXT`, `HARD_MAGIC` and `PERSONAL_RE`.

**Evidence:** I replicated `repo()` exactly against the current `collect.py` (hash above), writing only to a scratch
DEST. The four refusal cases still raise SystemExit before reaching `_hard_refuse`. The "ordinary file" call at line
451 is not wrapped in `check()`. It raises `NameError: name '_hard_refuse' is not defined`, the exception escapes
`main()`, and the suite ends with a traceback instead of `W5 RESULT: OK`. The Apply, Audit and Final phases re-run
`tests\w5`.

**Fix (image):** in `repo()`, compile `put`, `_hard_refuse` and the module-level assignments of `HARD_NAMES`,
`HARD_EXT`, `HARD_MAGIC` and `PERSONAL_RE`, selected by target name from the same AST, with `re` in `ns`. Add REPO
cases that must raise SystemExit: a `x.so` name, ELF content under a `.txt` name, gzip content, a buffer with the
EROFS magic at offset 1024, and a text naming a user-profile path. Keep the "ordinary file" case.

### F3 (MEDIUM): an ungated libz via the overlay upper layer (or any tree file) passes both guards

**Where:** `C:\GB-x86\build_esp.py:2452-2476` (`_check_vnni`) and `C:\GB-x86\verify_image.py:1990`, `:2006` (V31).
Both look only at `<tree>/vnni/` and at the gb.rc *text* ("no `libz.so` outside the block"). Neither looks at other
tree *files*. `INTEGRATION.md:66-68` forbids exactly this: "Do not stage the vendor copy into `/odm/vup/lib64/`
instead: the overlay's upper layer is not fingerprint-gated". After an OTA, a `gbstage/vup/lib64/libz.so` (or a native
component `files[].dest` of `vup/lib64/libz.so`) would put build N's patched vendor libz under build N+1. That is the
failure the gate exists to prevent. The same gap covers a whiteout (char 0,0) named `libz.so` in `vup/lib64`, which
would hide the vendor libz entirely. No such file exists today: test_w5 PACK checks this for the current trees only,
and REF/V31 mutations do not cover it.

**Fix (image):** in `_check_vnni` and V31, refuse every tree entry (regular file, symlink or char device) whose
basename starts with `libz.so`, except `<tree>/vnni/<id>/system/lib64/libz.so` and `.../vendor/lib64/libz.so`. Add a
REF mutation (`gbstage/vup/lib64/libz.so`, `gbnative/vup/lib64/libz.so`, a whiteout) to `tests\w5` and a T-V31
mutation to `tests\w2b`. Each must fail exactly V31.

### F4 (LOW): the gate properties can be overridden from `/odm/etc/build.prop`

**Where:** `C:\GB-x86\build_esp.py:446-452` (`GB_BRINGUP_SH` `prop()`: any `ro.?*` except a deny list that has
`ro.build.*`, `ro.product.*`, `ro.boot.*` ... but not `ro.system.build.*` / `ro.vendor.build.*`), and component
`build_prop` lines. Init loads `/odm/etc/build.prop` after `/system` and `/vendor`, and later values win: the gblog
shows `Overriding previous property 'ro.build.flavor' ...`. The vendor_init permission check passes while permissive.
A line `ro.system.build.fingerprint=<build N value>` on build N+1 would re-enable the stale binds. The bring-up is
test-only (it needs `gb.hw=native gb.overlay=1` and ESP write access), and "fingerprint spoofing" guides usually set a
*different* value, which only disables the patch. Still, this is the one way around the gate.

**Fix (image):** add `ro.system.build.*|ro.vendor.build.*|ro.*.build.fingerprint*` to the bring-up deny list, plus
the `_BAD_PROP` sync assert at `build_esp.py:2114-2117`. In `_check_vnni`/V31, refuse any `/odm/etc/build.prop` line
(both trees, including component `build_prop`) that sets a `VNNI_BINDS` gate property.

### F5 (LOW): V31's gate check accepts `*`; stray top-level files are ignored

**Where:** `verify_image.py:166` (`VNNI_GATE_RE ... =([^\s=]+)`) and `build_esp.py:2472` (same pattern). I checked:
`*`, `x` and `a/b` all match. V31's "no `=*` gate" therefore holds only because the rc must equal the tree's manifest
and the tree must equal the host `inject\vnni`. `vnni_prepare`/`_vnni_inputs` refuse `*` through PROP_SAFE, but V31
is meant to be the independent check. Also, `verify_image.py:1944` derives ids only from paths with two slashes, so a
`vnni/README` present in both host and image is never looked at. `_vnni_inputs` does refuse it at build time.

**Fix (image):** in `vnni_actions`, require the gate value to match the fingerprint grammar of `vnni_prepare.fp_id`
(`[^/]+/[^/]+/[^/:]+:[^/]+/[^/]+/[^:/]+:[^/]+/[^/]+`), use only PROP_SAFE characters and never equal `*`. Report any
`vnni/<name>` regular file. Use the same grammar in `_check_vnni`.

### F6 (LOW): the ordering check is positional only

**Where:** `build_esp.py:2465-2466`, `verify_image.py:2027-2029`. Both check that the overlay *line* comes before the
vnni block. Execution order holds only if the overlay's section is the same trigger (`on early-init`). If the overlay
ever moved to `on init`, the vendor bind would run first and be hidden. That fails safe (stock vendor libz), but
silently.

**Fix (image):** also check that the head of the section holding `mount overlay gbkm /vendor` is exactly
`on early-init`.

### F7 (LOW): mutation sensitivity of `tests\w5`

Static analysis of each guard against the tests:
* Caught: every rc-form change (PACK compares with both `vnni_patch.rc_block` and `vi.vnni_rc_text`); every
  `_vnni_inputs` refusal (REF); every GBINIT kill-switch change (import-time assert, CONS, PACK, builder B11); the
  block's order, count and presence (REF).
* Not caught:
  * `vnni_prepare.prepare()`'s post-patch guards: patched sha, "differs outside the manifest's edits" and the
    patcher's gate mismatch (`vnni_prepare.py:352-363`). The real patcher never trips them. `prepare(patcher=...)`
    already takes a path, so a fake patcher script that changes a third byte, writes a wrong hash or reports a
    wrong gate would cover all three.
  * The carve re-read check (it needs the builder).
* `test_w5`'s V31 section has 3 mutations. The other 13 V31 mutations live in `tests\w2b\test_verify_image.py` (TB-3),
  so both suites must run for V31 coverage.

**Fix (image):** add the fake-patcher PREP cases; keep TB-3 in the Apply and Audit runs.

### F8 (LOW): kill-switch usability

The switch is reliable as built (below). For users, though, it exists only as a per-boot edit: shipped entries have no
VNNI-off entry, `loader.conf` has `editor yes` and `timeout 3` (`build_esp.py:2865`), and a `gb.vnni=0` placed in
`gb/bootconfig_extra` is ignored because only `/proc/cmdline` is parsed. `gb_slotsync` copies the entry *file*, not
`/proc/cmdline` (`gb_slotsync.c` `copy_entry`). So a one-off edit does not persist, and a permanent edit of
`loader/entries/gb-<x>.conf` is carried to the other slot.

**Fix (image/docs):** document these points in the user docs (PERFORMANCE.md / README troubleshooting).

### F9 (LOW): slot or build drift before Apply

`_vnni_inputs` (`build_esp.py:1446-1483`) and V31 do not compare the folder's `slot` and partition sizes with
build_esp's `SLOT` and lapis.bin's current LP extents. If the user's VM installs an OTA before the Apply step, the
patch is packed but never fires. That is safe, but the measured gain would not transfer.

**Fix (image):** before writing the user's ESP, the Apply step should call `vnni_prepare.carve_plan(lapis, SLOT)`
(GPT + LP metadata only) and compare slot and extent sizes with `inject\vnni\<id>\manifest.json` `partitions`. On a
mismatch, re-run `vnni_prepare.py` or report the patch as inert. build_esp could print a warning on a slot mismatch.

### F10 (LOW): `collect.py` hardening (no live leak found)

The current rules held up against every vector I tried:
* `put()` refuses `inject/vnni/*`, any `libz.so` and any source below `inject\vnni`.
* `_hard_refuse` refuses `.so/.bin/.img/.erofs/.dex/...` by extension, ELF/gzip/xz/zip/zstd/sparse/PE magic at
  offset 0, the EROFS superblock, and personal paths.
* The component and ISO walks drop `out/`, `evidence/`, `prev/` and `work/`, ELF, any NUL in the first 8 KiB, and
  files over 2 MB.
* `collect.py` never walks `C:\GB-x86\archive`, `cache`, `inject\vnni` or `D:\`.
* A scan of every publishable `vnni-perf` file found no ELF, hex or base64 blob.

Remaining gaps, reachable only by a future *explicit* `put()`/`text_copy()` rule:
* magic checks look at offset 0 only;
* uncompressed tar (`ustar` at 257), cpio newc (`070701`/`070702`), lz4 frame, bzip2 (`BZh`) and raw zlib
  (`78 01/5e/9c/da`) are not in `HARD_MAGIC`.

The measure agent's `w5\tools\adler_kat\` currently holds an ELF, a `classes.dex` of unconfirmed origin and a 3 MB
`host_stream.zlib`. All three are excluded today, but only by the walk's elf/ext/size rules.

**Fix (image):**
* Extend `_hard_refuse` with `b"\x7fELF" in data` for non-`.md` files, plus the magics above.
* Move binary test inputs under an excluded folder name (`out/` or `evidence/`).
* If `tests\w5` is ever published, it needs a transform: `test_w5.py:49-50` holds a user-profile path, which
  `collect.py` already refuses (fail-closed).

---

## Verified OK (with the reasoning)

* **Property triggers in `/odm` rc under vendor_init.**
  * `/odm/etc/init/*.rc` is parsed with the vendor_init subcontext (path prefix `/odm`), so `IsActionableProperty`
    applies.
  * `ro.vendor.build.fingerprint` has the partner prefix `ro.vendor.`, so it is always actionable.
  * `ro.system.build.fingerprint` goes through `CanReadProperty(u:r:vendor_init:s0, ...)`. That passes: `build_prop`
    is world-readable, and the device is permissive in any case. `ro.actionable_compatible_property.enabled=true`
    makes the check live.
  * If the check ever failed, init's parser logs "unexported property trigger", sets `section_parser = nullptr` and
    skips that action's command lines (`bad_section_found`). The binds then never run: the safe failure. They are
    not attached to the previous action.
* **Command semantics.**
  * `mount` runs in init (`run_in_subcontext=false`); `restorecon` runs in vendor_init. Both are in the bootstrap
    namespace at early-init.
  * `mount none SRC DST bind` gives `mount(SRC, DST, "none", MS_BIND)`. File binds from `/odm` over `/system` and
    `/vendor` files already work on this build: the gblog shows only `mounton ... permissive=1` denials and no
    `mount() failed` apart from binfmt_misc.
  * `mount none none DST remount bind ro` gives `MS_REMOUNT|MS_BIND|MS_RDONLY`. This is AOSP-standard syntax (init.rc
    itself has `mount rootfs rootfs / remount bind ro nodev`), but it is new in this project. Measure it per F1.
  * Gated actions that combine an event and a property fire only on the event; the property part is an exact string
    compare (`Action::CheckPropertyTriggers`; `*` is the only wildcard, and it is refused at build time).
* **Ordering.**
  * Actions with the same event run in parse order. `/odm/etc/init` is parsed after `/system`, `/system_ext` and
    `/vendor`. The vnni block is the last block of both gb.rc files, after the early-init action that mounts the
    `/vendor` overlay.
  * No component rc and no later gb.rc action mounts over `/vendor`, `/vendor/lib64`, `/system/lib64` or
    `/system/lib64/libz.so` (grep of every component rc).
  * Nothing runs `restorecon_recursive /odm`.
* **Processes started before the bind.**
  * `/system/bin/init`, ueventd and vendor_init carry a static zlib (INTEGRATION section 4; stays emulated).
  * `apexd-bootstrap` is `exec_start`ed and exits; `gb_kmsgdbg` and `gb_edid` are short oneshots.
  * Zygote, system_server, apps, logd, servicemanager and vold all start after early-init and map the bound file.
    MEASURE's hot comms (DefaultDispatch, launcher-loader, LazyTaskWriterT) are zygote children.
  * The linker sees the realpath `/system/lib64/libz.so`, so namespace permitted paths are unaffected. APEX
    activation (`/apex` private) does not touch `/system/lib64`.
* **Fingerprint gating.**
  * The gate values equal the runtime values in the gblog. `vnni_prepare.read_prop` uses "last assignment wins", like
    init.
  * Gate characters are restricted to PROP_SAFE (no whitespace, `#`, `$` or `*`).
  * The system bind is gated on the system fingerprint and the vendor bind on the vendor fingerprint (allowed set
    plus `VNNI_BINDS`).
* **Kill switch.**
  * The match is the exact token `gb.vnni=0`. B11 confirms that `=1`, `=00`, `=`, a prefix and a suffix are all kept.
  * `rm -rf /odm/vnni` runs right after `cp -a`, before the bring-up overlay, which may not write libraries or rc.
  * With the folder gone: `restorecon SRC` fails, the bind fails with ENOENT, `restorecon /system/lib64/libz.so` is a
    no-op on the stock label, and the remount fails with EINVAL (not a mount root). Each is logged; none is fatal.
* **OTA.**
  * `gb_slotsync` reuses `/gb/inject.gz`, and the new slot's fingerprints differ, so no bind fires there; a rollback
    re-enables the binds with matching bytes.
  * The binds change no block, so dm-verity, AVB, apexd and update_engine's source-partition hash checks are
    unaffected.
  * A one-off `gb.vnni=0` is not copied to the other slot.
* **V31.** It covers the file set equal to the host, the patched hash against the reviewed manifest's deployable
  targets, one exact action per file, no other rc use, overlay order, the kill switch and entry tokens. The gaps are
  F3, F5 and F6.
* **No patched Google binary in the repo** (F10 for hardening only).

## For the measurement / Apply steps (cheap, high value)

1. In the dmesg of the "on" boot, look for `processing action (early-init && property:ro.system.build.fingerprint=...)
   from (/odm/etc/init/gb.rc:N)` and the vendor twin. There must be no `unexported property trigger`, no
   `mount() failed` and no failed `restorecon` for the vnni lines.
2. Per namespace (F1): `readlink /proc/{1,self,$(pidof zygote64)}/ns/mnt`, and `grep libz` in each mountinfo.
3. `toybox sha256sum /system/lib64/libz.so /vendor/lib64/libz.so` must equal `aa60fb2b...1047` / `fc4987b2...5350`
   (patched), and `371f70d6...` / `e45aecb6...` in the `gb.vnni=0` boot.
4. `ls -Z` on both paths should show the destination labels, not `tmpfs` or `vendor_file` for the system copy. Count
   `avc: denied { map|execute }` lines naming `libz.so`. A failed relabel shows up as a burst of these, harmless while
   permissive but log spam.
5. In the gb_vnni LRU of the "on" boot, `adler32_simd_` RIPs may remain only for comm `init`, `ueventd` or
   `vendor_init`.

## Open issues

* F1's hardening option needs a builder test of `context=` on a tmpfs mounted before the policy load, and a decision
  by the lead.
* Under SELinux enforcing, the binds need init `mounton` and vendor_init relabel permissions. A bind that succeeded
  without the relabel would leave libz unmappable for apps, and boot would fail. Both trees are permissive today
  (INTEGRATION section 5). Any future enforcing work must treat the vnni actions as boot-critical.
