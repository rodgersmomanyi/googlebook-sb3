# INTEGRATION.md — wiring the VNNI libz patch into build_esp.py

> **Wave 5 (2026-10-26): integrated with a simpler design — see section 6 "As built".** build_esp.py cannot read
> `/system` or `/vendor` (LZ4-compressed EROFS), so sections 0-2 below (patching inside build_esp) were NOT built;
> the gating rule (section 3), the non-deployed copies (section 4) and section 5 still apply.

Status: prototype, reviewed 2026-10-26. This describes how `C:\GB-x86\build_esp.py`
should stage the patched `libz.so` and bind it over `/system` and `/vendor` in
**both** the VM tree and the native tree. **Nothing under `C:\GB-x86\**` or
`inject\**` was written** (wave 4 owns those). Anchors below are names in
`build_esp.py`; line numbers drift (they were re-checked on 2026-10-26 ~14:50).

The patch is a single build-time 2-byte edit per `libz.so` (see `README.md` §2):
`adler32_z`'s `je`-to-scalar becomes `nop; jmp` (`0xa7d5: 0f 84 58 ff ff ff` →
`90 e9 58 ff ff ff`), so `adler32_simd_` (the 9 VEX `vpdpwssd`) is unreachable.

**Rule that must not be broken:** every bind is emitted by `vnni_patch.py --out-rc`
and is gated on the partition's build fingerprint
(`on early-init && property:ro.system.build.fingerprint=<exact>`). Never write an
ungated `on early-init` bind for these files (§3 explains why: `gb_slotsync`
reuses the same initrd for an OTA'd slot).

---

## 0. Build-time step (both trees)

`build_esp.py` has the slot's `/system` and `/vendor` contents (it carves the LP
partitions of `SLOT` / mounts the erofs). Stage each partition's `libz.so` **and
`build.prop`** into a throwaway in-root and run the patcher:

```python
import importlib.util, json, os, tempfile
VNNI = r"C:\GB-build\native\vnni-perf"
_spec = importlib.util.spec_from_file_location("vnni_patch", os.path.join(VNNI, "patcher", "vnni_patch.py"))
vnni_patch = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(vnni_patch)

_in, _out, _odm = (tempfile.mkdtemp(prefix=p) for p in ("vnni_in_", "vnni_out_", "vnni_odm_"))
for rel, data in (("system/lib64/libz.so", SYSTEM_LIBZ_BYTES),     # /system/lib64/libz.so of the slot
                  ("vendor/lib64/libz.so", VENDOR_LIBZ_BYTES),     # /vendor/lib64/libz.so of the slot
                  ("system/build.prop",    SYSTEM_BUILD_PROP),     # /system/build.prop of the slot
                  ("vendor/build.prop",    VENDOR_BUILD_PROP)):    # /vendor/build.prop of the slot
    p = os.path.join(_in, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)
_rc_path = os.path.join(_out, "vnni.rc")
_rc = vnni_patch.main(["--manifest", os.path.join(VNNI, "patcher", "manifest.json"),
                       "--in-root", _in, "--out-root", _out, "--odm-root", _odm,
                       "--out-rc", _rc_path, "--out-manifest", os.path.join(_out, "out-manifest.json")])
assert _rc == 0, "vnni_patch refused a target (byte or hash mismatch): investigate, do not ship"
VNNI_RC = open(_rc_path, "rb").read()                                   # b"" when nothing matched
VNNI_FILES = {n: open(os.path.join(_odm, n), "rb").read() for n in sorted(os.listdir(_odm))}
assert all(l.startswith(b"on early-init && property:ro.") for l in VNNI_RC.splitlines() if l.startswith(b"on "))
```

`vnni_patch` is pinned three ways: the input's sha256, the exact original bytes
at the edit, and the output's sha256. A slot whose `libz.so` is not the build the
manifest was made for yields no file in `_odm` and no rc line — the stock
library stays under k15 emulation. A refusal (exit 2) means the manifest and the
binary disagree in a way that should never happen: fail the build.

Ordering and timing:

* The vnni actions must run **after** the `gb.rc` early-init action that mounts
  the `/vendor` overlay (`mount none /vendor /odm/vlower bind` + `mount overlay
  gbkm /vendor …`): a bind made earlier would be hidden by the overlay, because the
  `/vendor` → `/odm/vlower` bind is not recursive. Appending `VNNI_RC` to the end
  of `GB_RC` / the native `_COMP_RC` does this (actions with the same trigger run
  in parse order). Do **not** stage the vendor copy into `/odm/vup/lib64/` instead:
  the overlay's upper layer is not fingerprint-gated.
* `early-init` precedes `on init`, where logd (the first daemon that links
  `libz.so` directly) starts, and zygote/dex2oat come much later. Stock init.rc's
  `exec_start apexd-bootstrap` runs earlier in early-init; if it maps libz at all
  it keeps the stock copy for its short life (emulated, no correctness impact).
  `/system/bin/init` itself carries a static zlib copy that no bind can replace.
* Each bind is relabelled and then remounted read-only in init's mount
  namespace (`mount none none <dst> remount bind ro`). That per-mount flag does
  not reach copies propagated to other namespaces. (W6) The source files
  therefore sit on their own tmpfs: gbinit moves `/odm/vnni` onto a tmpfs named
  `gbvnni`, mode 0755, with no other options, so the binds keep the `/odm`
  mount flags. This happens after the kill switch and the OTA gate. The vnni
  block's last action, `on init` / `mount tmpfs gbvnni /odm/vnni remount ro`,
  runs after every early-init relabel and bind. It is a superblock remount
  (MS_REMOUNT without MS_BIND), so every mount of that filesystem refuses
  writes: the binds in every mount namespace, the propagated copy
  `/odm/vlower/lib64/libz.so` and the source path. A `context=` mount at gbinit
  time is not possible: SELinux refuses mount context options before the policy
  is loaded (EINVAL, `security/selinux/hooks.c` `selinux_add_opt` /
  `selinux_set_mnt_opts` of the k15 tree), and context mounts cannot be
  relabelled. The libraries keep their stock labels through `restorecon` before
  the remount. Any failure in the gbinit step removes `/odm/vnni`, so there is
  no bind and the stock libz stays under emulation; gbinit then writes
  `/odm/gbinit_vnni.log`. (Wave 6 VM-1: with `/odm/vnni` absent, lapis's init
  logs no ENOENT line for the tail, only the two `remount bind ro` failures.)

---

## 1. VM tree

VM-tree override files are staged under `gbstage/` (gbinit copies `/gbstage` →
`/odm`) and the VM rc is `GB_RC`, staged as `('gbstage/etc/init/gb.rc', …, GB_RC)`.
Mirror the gatekeeper pattern (`GB_RC += GK_RC_BLOCK`):

```python
if VNNI_FILES:
    _entries.append(('gbstage/vnni', 0o040755, None))
    for _n, _b in VNNI_FILES.items():
        _entries.append(('gbstage/vnni/' + _n, 0o100644, _b))
GB_RC += b"\n# ---- component vnni ----\n" + VNNI_RC      # after the /vendor overlay action
```

---

## 2. Native tree

The native component loader (`for _mf in sorted(_glob.glob(... "components", "*.json"))`,
`_load_manifest`) stages `files[].dest` under `/odm` and appends `rc_files` /
`rc_text` to the native gb.rc (`GB_RC_NATIVE + GB_SLOTSYNC_RC + _COMP_RC`, i.e.
after the native `/vendor` overlay action). Two options:

**(a) Inline (preferred: always matches the slot being built).**

```python
for _n, _b in VNNI_FILES.items():
    _add_native("vnni/" + _n, _b, 0o100644)
_COMP_RC += b"\n# ---- component vnni ----\n" + VNNI_RC
```

**(b) Component manifest.** `component/vnni.json` (allowed keys only: `name`,
`source`, `comment`, `files`, `rc_files`) + `component/vnni.rc` are ready for
build 16658739. Because the rc is fingerprint-gated, a static copy of this
component is *safe* on any other build (the gate never fires there), it just
stops helping; regenerate it per build to keep the benefit.

Either way the rc text is byte-identical in both trees, matching the project's
"same rc block in both trees" convention (cf. the gatekeeper block).

---

## 3. OTA / other slot — why the gate is mandatory

`gb_slotsync` (source `C:\GB-build\mouse\gb_slotsync.c`, rc `GB_SLOTSYNC_RC`)
rebuilds the ESP initrd for the slot an OTA completed into **from the same
`/gb/inject.gz`** (`slot_files()` docstring). So the `/odm/vnni/*` files and the
rc produced for build N are also used to boot build N+1. The build-time sha256
pin cannot see that; the boot-time gate does:

* init loads `ro.system.build.fingerprint` / `ro.vendor.build.fingerprint` from
  the dm-verity-protected partitions' own `build.prop` before `early-init`;
* the vnni actions are `on early-init && property:<that prop>=<build N value>`;
  on build N+1 the value differs, the action never fires, the stock libz of N+1
  runs (emulated). A rollback to N re-enables it. No new SELinux permission is
  needed (`build_prop` is readable by init/vendor_init).
* The stock init.rc of this image already uses `on early-init && property:…`,
  so the syntax is supported by this init.

**Second layer: the OTA gate (wave 5b, `ota-gating/DESIGN.md`).** Independently of
the fingerprint triggers, GBINIT compares the boot slot's init_boot fingerprint
with `/odm/etc/gb/ota_validated` (packed at build time). On any other or unreadable
build it runs `rm -rf /odm/vnni` before Android's init starts, appends
`ro.vendor.gb.ota_gate=unvalidated` to `/odm/etc/build.prop`, and a
"System update not validated" notification follows at `boot_completed`. So after an
OTA the binds are skipped twice: the files are gone, and the triggers would not
match anyway. Boot-menu tokens: `gb.otagate=0` turns this layer off (the
fingerprint triggers still apply), `gb.otagate=force` takes the unvalidated path on
the validated build (a test). `gb.vnni=0` (§6 (3)) removes `/odm/vnni` for one boot
whatever the build. Rebuilding the ESP from the host for the new build (re-run
`vnni_prepare.py`, then `build_esp.py`) re-validates it.

**Optional stricter guard.** `component/gb_vnni.sh` + `/odm/vnni/guard.tsv`
(`dest \t orig_sha256 \t src`) re-hashes each destination at boot and binds only
on an exact match (closes the "same fingerprint, different bytes" case). Run it as
a `service … seclabel u:r:shell:s0` oneshot `exec_start`ed in `on early-init`
**instead of** the init binds (the global SELinux mode is enforcing, but
`inject\odm_sepolicy.cil` makes every type of this build permissive, `shell`
included; without that it would need mount/relabel permissions). The review fixed its
mount command (it used init's `mount none SRC DST bind` syntax, which toybox
rejects, so it could never have bound anything).

---

## 4. Not deployed: APEX-resident and other static copies

The manifest carries flag_zero edits for 10 static zlib copies (disasm-proven,
`README.md` §2b) but none is staged or bound; each has a `deploy_note`:

| target | why not |
|---|---|
| `com.android.runtime` `bin/linker64`, `/system/bin/bootstrap/linker64` | PT_INTERP of every dynamic executable; a bad bind breaks every exec; load not captured. |
| `com.google.android.appsearch` `lib64/libicing_anywhere.so` | `/apex` is a **private** mount point in each init mount namespace (bootstrap vs default), so an init bind made at early-init in the bootstrap namespace is not seen by default-namespace processes (where AppSearch's users run); a `/data/apex` mainline update re-activates over the preinstalled APEX and drops any bind. Measured rank 2 — stays emulated. |
| `/system/bin/init` | already running (exec'd by first-stage init) before any rc; ueventd and the vendor_init subcontext are the same binary. 16 traps — stays emulated. |
| bpftool, snapuserd, update_engine, libfdtrack, libsnapshot, libflashrom | bindable in principle, but no runtime load captured; not worth a bind. |

If the AppSearch copy ever shows measurable load, bind it from a trigger that
runs in the **default** namespace after apexd activation (e.g. `on
property:apexd.status=ready`, through `gb_vnni.sh`'s sha256 check), and accept that
a mainline update disables it. Never bind over a `.apex` file: apexd verifies the
APEX signature and payload verity at activation.

---

## 5. SELinux, verified boot, and on-device verification (for wave 4)

* SELinux (corrected after the wave 5 safety audit, SA-1): the **global mode is
  enforcing** — `getenforce` prints `Enforcing`; lapis's user-build init ignores
  `androidboot.selinux = "permissive"` in `BOOTCONFIG_EXTRA`. Every type of this
  build is permissive through `inject\odm_sepolicy.cil` (2,694 `typepermissive`
  lines, OTA-robust `optional` form), and that is why labelling cannot block boot
  today: every logged denial is `permissive=1`, 0 enforced denials in every wave 5
  boot. The bind and relabel steps work because `init` and `vendor_init` are
  permissive types; `build_esp.py` refuses a cil that drops them or the other
  domains in `OTA_REQUIRED_PERMISSIVE` (`_check_otagate`, tests\w5 SA1). A type that a
  later Google build renames is no longer in the list, so that domain becomes
  enforcing on that build (the OTA gate marks such a build "not validated"). The
  `restorecon` after each bind relabels the bound tmpfs inode to the destination's
  context (measured: `system_lib_file` for `/system/lib64/libz.so`,
  `same_process_hal_file` for `/vendor/lib64/libz.so`) so processes see the
  expected label. If the list is ever dropped (real enforcing),
  `inject\odm_sepolicy.cil` must allow init (and vendor_init for `restorecon`)
  `mounton` + `relabelfrom/relabelto` on those types.
* Verified boot (orange): binds change no block of a dm-verity partition and no
  APEX file, so AVB/dm-verity/apexd verification is unaffected. The bound inode
  lives on the unverified `/odm` tmpfs (from the ESP initrd) — same trust level as
  everything else `/odm` already injects. (W6) The bound files live on gbinit's
  own `gbvnni` tmpfs, which init remounts read-only as a whole at `on init`,
  after the relabel: no mount of it (in any namespace, `/odm/vlower` included)
  accepts a write. Root can still remount it read-write; that is the same trust
  level as every other `/odm` injection on an unlocked device.
* Smoke test after wiring (VM tree):
  * `dmesg | grep -iE "vnni|libz|unexported property trigger|gb.rc"` shows no init
    parse error for the gated actions (gb.rc under `/odm` is parsed in the
    vendor_init subcontext, which checks that a property trigger is readable there;
    a rejected action means no bind, i.e. the safe failure) and no `mount() failed`;
    `grep libz /proc/$(pidof system_server)/maps`,
    `readlink /proc/{1,self,$(pidof zygote64)}/ns/mnt` and
    `grep libz /proc/1/mountinfo /proc/$(pidof zygote64)/mountinfo` show the bound
    file in both namespaces (`ro` in init's bootstrap namespace, `rw` allowed in
    the default namespace).
  * Acceptance measurement (README §5): two boots of the same tree, with and
    without the bind (for the "without" boot change the gate value so it does not
    match), reading the first line of `/sys/kernel/gb_vnni/summary`
    (`emulated N vex128 … word …`) at the same uptimes, minus the KAT's own counts;
    `adler32_simd_` RIPs (page offsets `0x067`, `0x145`–`0x1ea`) must be absent from
    the LRU in the "with" boot.
  * OTA check: boot a tree built for build N on a slot holding another build
    (or edit the gate value) and confirm no vnni bind appears in mountinfo.

---

## 6. As built (wave 5, 2026-10-26; host-tested, not booted yet)

Lead decision W5: a separate prepare step, build_esp only packs. Pre-edit copies of every changed pipeline file:
`C:\GB-x86\archive\pre-w5\` (+ `SHA256SUMS.txt`); evidence: `w5\evidence\` next to this file.

**(1) `C:\GB-x86\vnni_prepare.py`** (run on demand, never inside build_esp; `--info` = plan only).
* Reads `lapis.bin` read-only: GPT, the boot slot (`extract_slot.boot_slot`'s rule, = build_esp's `SLOT`; `GB_SLOT`
  or `--slot` override), the LP metadata slot **that belongs to the suffix** (`_a` -> 0, `_b` -> 1, as
  `tools\carve_stream.py`; metadata slots 0 and 2 still name `system_b` with stale 4199 MiB extents, the trap
  `carve_lp.py` fell into).
* Streams `system_<x>` / `vendor_<x>` to the builder (`/build/w5vnni/<run>`, sha256 on both ends), extracts them with
  `fsck.erofs --extract`, fetches only `system/lib64/libz.so`, `lib64/libz.so` and the two `build.prop` (a tar of
  regular files; symlinks refused), removes the builder scratch at once, then re-reads the carved extents and the LP
  metadata and refuses if anything changed (the user's VM may run meanwhile; it never writes these partitions).
* Each deployable manifest target: `orig_sha256` must match, else the target is **skipped** (stays emulated).
  `patcher\vnni_patch.py` runs in its own `python -I` process; the result must equal `patched_sha256` and differ from
  the original only inside the manifest's edit (2 bytes at `0xa7d5`).
* Fingerprints from the partitions' own `build.prop` (last assignment wins, init-safe characters only).
* Writes `C:\GB-x86\inject\vnni\<id>\{system/lib64/libz.so, vendor/lib64/libz.so, manifest.json}`,
  `<id> = <build id>_<incremental>-<8 hex of sha256(system fp LF vendor fp)>`, built in `cache\vnni-tmp` and renamed into
  place; an existing folder with other bytes is MOVED to `C:\GB-x86\archive\vnni-prev\<id>-<time>\` (never deleted).
  `manifest.json` (format `gb-vnni-1`) holds slot, fingerprints, the carved partitions' size + sha256, the patcher's
  and manifest's sha256, per target bind/gate/orig/patched sha256/edits; no host path, no time stamp.
* Run of 2026-10-26 (`w5\evidence\vnni_prepare_run1.log`, 294 s): slot b, metadata slot 1, `system_b`
  1,136,197,632 B `15fc09de...14d0`, `vendor_b` 380,608,512 B `a1491aa4...9513` (both = MEASURE.md 2.1), both
  targets patched: system `371f70d6... -> aa60fb2b...1047`, vendor `e45aecb6... -> fc4987b2...5350` (= `out\patched\`),
  folder `CL3B.260622.276.R4_16658739-3b953c77`.

**(2) build_esp.py packs, both trees.** `_vnni_inputs()` checks every `inject\vnni\<id>\` (fail closed: only
`<id>` folders, exactly the manifest's files, each file == its `patched_sha256`, each (orig, patched, bind, gate)
a deployable target of `patcher\manifest.json`, gate value == the folder's fingerprint, unique gates) and packs it as
`/odm/vnni/<id>/...` (0644) into `gbstage` and `gbnative`. The same rc block is appended as the LAST block of both
gb.rc files (VM: after the gatekeeper block; native: after `_COMP_RC`), i.e. after the `/vendor` overlay action:

```
on early-init && property:ro.system.build.fingerprint=Android/desktop_system/desktop:17/CL3B.260622.276.R4/16658739:user/release-keys
    restorecon /odm/vnni/<id>/system/lib64/libz.so
    mount none /odm/vnni/<id>/system/lib64/libz.so /system/lib64/libz.so bind
    restorecon /system/lib64/libz.so
    mount none none /system/lib64/libz.so remount bind ro
(the same for /vendor/lib64/libz.so, gated on ro.vendor.build.fingerprint=ASUS/lapis/lapis:17/...:user/release-keys)
```

(W6) then, as the block's last action:

```
on init
    mount tmpfs gbvnni /odm/vnni remount ro
```
Each action is byte-identical to `vnni_patch.rc_block()` with source `/odm/vnni/<id>/<file>` (tests\w5 PACK).
(W6) followed by `VNNI_RC_TAIL` (above); gbinit moves `/odm/vnni` onto its own tmpfs (`VNNI_GBINIT_RO`).
`_vnni_inputs` refuses a folder for another slot or another build than the boot slot's init_boot fingerprint (SA-2)
and a gate value that is not the reviewed `deploy.gate_value` (SA-3).
No service. Those fingerprint values are what init reported on this build (`dgpu\1b\evidence\w2fix\vm1\gblog\`).
`_check_vnni` (both trees) refuses a missing/extra file, an ungated action, `/odm/vnni` or `libz.so` outside the
block, the block before the overlay or twice, and a GBINIT without the kill switch.

**(3) Kill switch.** GBINIT: `VN=1`; the exact token `gb.vnni=0` sets `VN=0`; right after `cp -a $SRC/. /odm/`:
`[ "$VN" = 0 ] && /busybox rm -rf /odm/vnni`. The binds then fail (ENOENT), init logs and goes on; the stock libz
stays under k15 emulation. For one boot, press `e` at the systemd-boot menu (`editor yes`, `timeout 3`) and append
`gb.vnni=0`; to make it permanent across boots and slot switches, edit `loader/entries/gb-<x>.conf` on the ESP
(`gb/bootconfig_extra` is not read by `gbinit`). Builder harness B11 (`tests\w3a\bringup\test_bringup.sh`, 9 cases):
removed with `gb.vnni=0` (native and VM), kept without it and for `gb.vnni=1`, `=00`, `=`, `xgb.vnni=0`, `gb.vnni=0x`;
init reached.

**(3b) Pre-Apply slot/extent check (F9).** Before writing the ESP in the Apply step, run
`python -I C:\GB-x86\vnni_prepare.py --check-slot` (or `python -I C:\GB-build\native\vnni-perf\w5\tools\check_vnni_slot.py`),
which calls `vnni_prepare.carve_plan(lapis, SLOT)` (GPT + LP metadata only) and verifies that the prepared
`inject\vnni\<id>\manifest.json` slot and `system_<x>`/`vendor_<x>` partition byte sizes still match `lapis.bin`'s
current boot slot. On any mismatch, re-run `vnni_prepare.py` before building the ESP.

**(4) verify_image V31** (every kind): the tree's `/odm/vnni` == `inject\vnni` byte for byte (0644); per folder
manifest every file exists with its `patched_sha256` and its (orig, patched, bind, gate) is a deployable target of
the reviewed patcher manifest; gb.rc has exactly one 5-line gated action per file, after the overlay; no other
section uses `/odm/vnni` or names `libz.so` (no ungated bind, no `=*` gate); gbinit has the kill switch; a `gb.vnni=`
token only as `gb.vnni=0` in an entry titled "(VM test, W5, VNNI off)".

**(5) collect.py** never publishes `inject\vnni` (its `put()` refuses `inject/vnni/*`, any `libz.so` and any source
below `inject\vnni`; the manifest's EXCLUDED list records `inject/vnni/`).

**(6) Suites** (all pass; logs in `w5\evidence\`): new `C:\GB-x86\tests\w5\test_w5.py` 99 ok; w4 119 ok; w3a host
108 ok; builder bring-up 85/85; TB-3 OK (today's USB image 377310ee now fails exactly V12 (+gbinit), V29, V31:
recorded in KNOWN_STALE); trees 18 PASS; w2a 8/8 groups (140 ok); ISO static + build_iso dry run OK.

**(7) Measurement inputs (test-only, `D:\gb-w5test\`).**
* `build_usb.py --vm-test --vm-test-w5 --sparse --userdata-gib 8 --out D:\gb-w5test\vm\disk.img`: exactly
  `build_esp.INJECT_CPIO`; image `dd88458a...c516`, super GUID `02b2cf1f-8ec8-4abe-9a7e-6b61018667e4`, verify_image
  RESULT OK incl. V30/V31.
* **New VM-tree record** (replaces the W4 record `1e939bc0...2934` for any later user-VM preview):
  `gb/inject.gz 080658020d285ee55d506f0302ca02340226e8e3ac80a808633556618b9a8234`
  (`D:\gb-w5test\vm\disk.img.vmtree.sha256`, copy `w5\evidence\w5-tree.vmtree.sha256`). The in-memory 192 MiB user-VM
  ESP passes every check with `--esp-size 201326592 --expect-inject-sha256 <it>` (tests\w5 USB, trees).
* Baseline = the same tree with `gb.vnni=0`: `--vm-test-w5 --vnni-off --esp-only D:\gb-w5test\esp\vnni-off.img
  --super-guid 02b2cf1f-...` (`9c303f64...fe25`); `esp\vnni-on.img` (`242691c7...17e8`) == the disk's own ESP. The
  two differ only in `loader/entries/gb-b.conf` (title "(VM test, W5, VNNI off)" + ` gb.vnni=0`;
  `w5\evidence\esp_compare.txt`).

Not verified until a boot (the Measure step): that vendor_init accepts the two property triggers in `/odm` rc
(`unexported property trigger` would mean no bind: the safe failure), the binds per mount namespace (W6: the `gbvnni`
superblock `ro` in every namespace, mountinfo super options `ro`, `/odm/vlower/lib64/libz.so` included), the
emulation counter drop, and no SELinux/`restorecon` errors.
