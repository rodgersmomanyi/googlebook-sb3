> **Repository note.** The working README of the VNNI libz patch, copied from the development tree.
> `out/` (the patched `libz.so` copies: Google binaries with a 2-byte edit) and `evidence/` are **not
> published**; `vnni_prepare.py` (repository root) re-makes the patched copies from your own lapis image,
> pinned to the hashes in `patcher/manifest.json`. `C:\GB-build\native\vnni-perf` = `native/vnni-perf`,
> `C:\GB-x86` = the repository root, `builder:` / `/var/tmp/vnniperf` = the Linux builder. For users:
> [docs/PERFORMANCE.md](../../docs/PERFORMANCE.md).

# vnni-perf patcher — steer zlib adler32 off the VEX AVX-VNNI path (lapis / Ice Lake)

Apache-2.0. Written from scratch for GB-build. **Idea credit:** the gbOS86
research project first observed that `libz.so`'s `adler32` is the dominant VEX
AVX-VNNI trap source on this image and that the branch after the SSSE3 feature
test in `adler32_z` gates the SIMD path. This is a clean-room reimplementation —
no gbOS86 code was copied; only its published build sha256 values and that idea
were used. (The 2026-10-26 review compared this code with gbOS86's
`tools/patch_libz_adler.py` for the licence check only: same 2-byte edit idea,
different implementation — sha256/manifest-pinned vs. regex search, see §9.)

Build: `CL3B.260622.276.R4 / 16658739`. Companion measurement: `MEASURE.md`
(where the emulation load comes from). This directory is the build-time patcher,
its manifest, the equivalence/zero-VNNI tests, and the integration notes.

---

## 0. Result

Ice Lake (Surface Book 3 i7-1065G7, and the builder) has EVEX `avx512_vnni` but
**not** VEX `avx_vnni`, so lapis — compiled for a VEX AVX-VNNI target — traps
every `vpdpwssd`/`vpdpbusd` into the k15 `#UD` emulator. `MEASURE.md` found
~2.48×10⁸ emulations in one VM-tree boot; every non-KAT VEX.128 RIP the kernel's
16-entry LRU ever captured is one of the 9 VEX `vpdpwssd` of Chromium-zlib
`adler32_simd_` (in `/system/lib64/libz.so`, plus one AppSearch static copy).

This patcher removes the libz part of that load with a **single build-time
2-byte edit per `libz.so`**, pinned to the input's and the output's sha256, bound
at boot **only on the exact build it was made for** (build-fingerprint gate,
§3), and proves on the exact target CPU that the patched library computes
identical results and that **no** VEX AVX-VNNI instruction is reachable in it.

**Measured on the device (wave 5, `w5/MEASURE_W5.md`):** two throwaway VMs with the
user VM's CPUID masks, one with the patch and one with `gb.vnni=0`, started together.
Boot to `sys.boot_completed`: 587.7 s → 313.6 s on a first boot (−46.6 %), 226.0 s →
118.0 s on a warm boot (−47.8 %); emulations at `boot_completed`: 211,368,597 → 756;
boot-time dexopt of 4 packages 643.5 s → 480.3 s (−25 %); adler32 throughput
5.9 / 5.4 MiB/s → 538 / 1229 MiB/s (system / vendor libz). Nothing from either
`libz.so` is emulated any more; what remains is AppSearch's static zlib, ML-KEM
in the tethering APEX and init's own copy. The patch ships enabled in both trees.

Probe results, both `/system` and `/vendor` libz (`evidence/equiv_run.log`):

| op (seed 0x1234) | unpatched | patched |
|---|---|---|
| threads (8 threads race the lazy CPU-feature init, 3200 calls) | 3 ok, **3197 SIGILL** | **3200 ok**, 0 SIGILL |
| adler32, 3019 random+fixed lengths | 10 ok (len<64), **3009 SIGILL** | **3019 ok**, 0 SIGILL |
| adler_chain (random start value, random split points) | 1 ok, **599 SIGILL** | **600 ok** |
| adler_unaligned (start misaligned 1..63) | 1 ok, **599 SIGILL** | **600 ok** |
| adler32_z_large (64 KiB..1 MiB+77) | **24 SIGILL** | **24 ok** |
| adler_combine | **300 SIGILL** | **300 ok** |
| inflate_roundtrip (compress2 + uncompress) | 10 ok, **3009 SIGILL** | **3019 ok** |
| stream (deflate/inflate, levels 0/1/6/9 × zlib/gzip/raw, small chunks) | 84 ok, **36 SIGILL** (zlib-wrapped, len≥64) | **120 ok** |
| crc32 (control, not touched) | 3019 ok, 0 SIGILL | 3019 ok, 0 SIGILL |

All unpatched faults are at `0x11067` / `0x11145`, inside `adler32_simd_`
`[0x10fc0,0x11352)` — the addresses the kernel counter saw at boot — which also
proves the inputs reach the SIMD path. The disassembly proof (§4) extends "0
SIGILL on these paths" to "no VEX VNNI reachable from any entry point".

---

## 1. Targets and why

From `MEASURE.md`'s ranking, the VEX AVX-VNNI emulation load is dominated by one
function: Chromium-zlib `adler32_simd_`, reached through `adler32_z` for buffers
≥ 64 bytes (once the process has run zlib's lazy CPU-feature init). It lives in
both `/system/lib64/libz.so` (system_server, apps, `dex2oat`, …) and
`/vendor/lib64/libz.so`, plus 11 statically-linked copies. The manifest carries
edits for 12 of them, but **only the two exported `libz.so` are deployed**:

| target | strategy | proof | deployed? | measured load |
|---|---|---|---|---|
| `system/lib64/libz.so` | branch_flip | execution + whole-binary reachability | **yes** (gated bind) | rank 1; all 5 captured libz clusters |
| `vendor/lib64/libz.so` | branch_flip | execution + whole-binary reachability | **yes** (gated bind) | same layout as system (RIPs cannot tell them apart) |
| `apex com.google.android.appsearch/…/libicing_anywhere.so` | flag_zero | disasm | no — APEX (private `/apex` per mount namespace) | rank 2 (one cluster, small) — **remains emulated** |
| `system/bin/init` | flag_zero | disasm | **not possible** — already running before any rc; ueventd/vendor_init are the same binary | 16 traps (native tree) — remains |
| `system/bin/bootstrap/linker64`, `apex com.android.runtime/bin/linker64` | flag_zero | disasm | no — PT_INTERP of every executable | not captured |
| `system/bin/{bpftool,snapuserd,update_engine}`, `system/lib64/{libfdtrack,libsnapshot}.so`, `vendor/lib64/libflashrom.so` | flag_zero | disasm | no (bindable, but no load captured) | not captured |

Each non-deployed target carries a `deploy_note` in `manifest.json`. Not touched
at all: BoringSSL ML-KEM (VEX.256, a different library/ISA path), protobuf,
codec/ML/XNNPACK — see `MEASURE.md` §4.

---

## 2. The patch

Two length-preserving edits, both of which make an **existing non-VNNI path** run
instead of rewriting any arithmetic. The VEX `vpdpwssd` instructions stay in the
file byte-for-byte; they just become unreachable.

**a) `branch_flip` (the two deployed `libz.so`).** In `adler32_z`, for `len ≥ 64`:

```
 a7ce: cmpl $0x0, 0x1582b(%rip)   # 0x20000 = x86_cpu_enable_ssse3
 a7d5: je   a733                  # scalar          <-- the edit
 a7db: call 10fc0 <adler32_simd_> # 9x {vex} vpdpwssd
```

`je a733` (`0f 84 58 ff ff ff`) → `nop; jmp a733` (`90 e9 58 ff ff ff`) at file
offset `0xa7d5`. Same target, same length; exactly 2 bytes differ from the
original (`0xa7d5`, `0xa7d6`, checked by `tests/test_patcher.py`). The `len ≥ 64`
branch now unconditionally takes the scalar code at `a733`. Touches only adler32 —
`crc32` (SSE4.2/PCLMUL flag at `0x20004`) and inflate's SSE2 chunkcopy are
untouched. This is gbOS86's idea.

Side effects checked (`evidence/disasm_proof.log`): no relocation of any kind
covers the edit (Android packed RELA, RELR and JUMP_SLOT decoded with
`llvm-readelf`), no IRELATIVE/ifunc, no `DT_TEXTREL`, `BIND_NOW`; the edit is a
PC-relative branch, so load address does not matter. The build-id note is
unchanged (same layout, so symbolization of tombstones stays correct). Thread
safety: nothing changes at run time — the dispatch no longer depends on the
lazily-initialised flag at all (the `threads` probe op races 8 first-callers).

**b) `flag_zero` (static copies; not deployed).** In `_cpu_check_features`,
right after `cpuid`: `and $0x200,%eax` (`25 00 02 00 00`) → `and $0x0,%eax`. The
SSSE3 flag is always 0, so that copy's own `adler32_z` always takes its scalar
branch. The disasm proof shows, in every one of these binaries, exactly one
writer and one reader of the flag, the reader is followed by `je` + `call` into
a function that holds the 10 VEX `vpdpwssd` (the copy's `adler32_simd_`), and
`%eax` is overwritten (`mov %ecx,%eax`) right after the store, so the edit feeds
nothing else.

Each edit was derived from the binary, not hand-typed: `patcher/gen_manifest.py`
locates it by byte signature, confirms the gate flag, and cross-checks the call
target against the measured `adler32_simd_` addresses in `scan.json`. The review
re-ran it on the builder: the regenerated manifest was byte-identical to the
committed one before the review's additive fields (`patched_sha256`, `deploy`,
`deploy_note`) were introduced.

---

## 3. Pinning and the boot-time gate — no match, no patch, no bind

`patcher/vnni_patch.py`:

* keys every target on the original file's sha256; no match ⇒ **skipped** (no
  patched copy, no bind; k15 still emulates it correctly);
* verifies the exact original bytes at the ELF-mapped offset (one byte of drift
  ⇒ the whole target is refused);
* requires the result to hash to the manifest's `patched_sha256` (determinism);
* with `--out-rc`, emits the init fragment that binds each deployable copy
  **gated on the partition's build fingerprint**, read from the in-root's
  `build.prop` (else the manifest's `gate_value`) — never an ungated bind:

```
on early-init && property:ro.system.build.fingerprint=Android/desktop_system/desktop:17/CL3B.260622.276.R4/16658739:user/release-keys
    restorecon /odm/vnni/system_libz.so
    mount none /odm/vnni/system_libz.so /system/lib64/libz.so bind
    restorecon /system/lib64/libz.so
    mount none none /system/lib64/libz.so remount bind ro
(same for /vendor with ro.vendor.build.fingerprint)
```

**Why the gate is mandatory (review finding, HIGH).** The sha256 pin only acts
at build time. On the device, `gb_slotsync` builds the other slot's initrd from
the **same** `/gb/inject.gz` after an A/B OTA (`build_esp.py` ~line 1214), so the
`/odm/vnni/*` copies and their rc are reused for a slot holding a *different*
build. The original prototype's unconditional `on early-init` bind would then
have mounted this build's old libz over the new build's libz (ABI and security
regression). init loads `ro.system.build.fingerprint` / `ro.vendor.build.fingerprint`
from the verified partitions before early-init, so on any other build the action
simply never fires. Stock `init.rc` on this image uses the same
`on early-init && property:` form.

`tests/test_patcher.py` (`evidence/test_patcher.log`, 15 checks, all ok) covers:
matching image → 2 gated binds with the image's own fingerprints; no build.prop
→ manifest value; changed libz (OTA) → skipped, no bind; identical libz on another
build → gated on *that* build only; gate property missing or unsafe → no bind;
wrong original bytes / wrong result hash → refused; symlinked target → skipped;
static targets → never deployable.

Patched hashes for this build (`out/out-manifest.json`):
`system/lib64/libz.so` `371f70d6…` → `aa60fb2b951db26a…`;
`vendor/lib64/libz.so` `e45aecb6…` → `fc4987b213e15017…`.

---

## 4. Equivalence + zero-VEX-AVX-VNNI proof

Run on the builder — itself an **i7-1065G7 (Ice Lake)** with `avx512_vnni` but no
VEX `avx_vnni` and a stock kernel (no `#UD` emulator, `evidence/builder_env.txt`),
so a VEX AVX-VNNI instruction that executes raises **SIGILL**.

`tests/vnni_probe.c` (bionic x86-64, NDK clang; run in a read-only chroot of the
carved lapis tree by `tests/run_equiv.sh`) `dlopen`s one `libz.so` and compares
every result with an in-process scalar reference (independent of libz); a
per-thread SIGILL handler records the faulting RIP. Ops: see §0. `run_equiv.sh`
ends with a VERDICT step (`VERDICT overall: PASS`): patched = every op correct
with 0 SIGILL; unpatched = every adler32-using op faults inside `adler32_simd_`
(so the inputs do reach the SIMD path) and crc32 stays clean; every generated
bind is fingerprint-gated. (The unpatched runs print a scudo "can't populate
more pages" warning: each SIGILL longjmps out of `compress2`/`deflate` and leaks
its state; no allocation failed — mismatch=0 everywhere.)

### Disassembly / reachability proof (`tests/disasm_paths.py`, 12/12 PASS)

For the two deployed `libz.so` it proves the whole-binary claim
"**no VEX AVX-VNNI instruction is reachable from any entry point**", not only
from the paths the probe exercised:
R1 all 9 VEX VNNI in the file lie inside `adler32_simd_` `[0x10fc0,0x11352)`
(bounds from `.eh_frame`); R2 the only code reference into it from outside is the
now-dead `call` at `0xa7db`; R3 no data word (RELR/RELA targets, tables) points
into it or at the dead call; R4 no dynamic symbol/relocation targets it, no
IRELATIVE, no TEXTREL, no relocation overlaps the edit; R5 `adler32_z` has no
indirect jump (no jump table can reach the dead call) and no VNNI; R6 the gate is
`nop; jmp a733` and no branch targets the dead call. For the 10 static copies:
the flag_zero checks in §2b.

---

## 5. Emulation reduction per boot (estimate) — what the evidence supports

Measured (`MEASURE.md`, VM tree, run `vm2`): **248,149,863** non-KAT emulations by
~434 s, 100% word-form (`vpdpwssd`), 99.998% VEX.128; non-KAT VEX.256 = 5,216
(BoringSSL ML-KEM, not targeted). Front-loaded: ~1.06×10⁶/s average over the
first 198.7 s.

What is **measured** about libz's share is small: the LRU entries of the captured
`adler32_simd_` clusters hold 28,919 counts in vm2's final snapshot (Profile Saver
21,728 + dex2oat 7,191) — about 0.01% of the total. The LRU only keeps the 16 most
recent comm/RIP buckets, so it cannot apportion the total.

What is **INFERRED** (the basis for "libz is essentially all of it"): every
non-KAT VEX.128 RIP the LRU ever captured (6 clusters across vm2/vm2-early) is an
`adler32_simd_` copy, 100% of the load is word-form, and the boot workload is
dominated by adler32 consumers (dex2oat's OatWriter checksums everything it
writes; ART dex/oat/profile checksums; the VM boots with a fresh `/data`, so
dex2oat runs every boot). Scale check (INFERRED): `adler32_simd_`'s main loop
executes 8 `vpdpwssd` per 128 bytes (1 trap / 16 B; tail loop 1 / 32 B), so
2.48×10⁸ traps ≈ 4×10⁹ bytes (~3.7 GiB) adler32'd in ~434 s — plausible for a
full first-boot dex2oat pass, but not a measurement.

Therefore:

* **Patching the two `libz.so` removes every emulation that comes from them**
  (proven: no VEX VNNI reachable). That they account for ≥ 99% of the 2.48×10⁸
  is **INFERRED**, not measured.
* **Residual (not removed):** ML-KEM VEX.256 (≥ 5,216/boot, measured); the
  AppSearch `libicing_anywhere.so` adler32 copy (not deployed; measured present,
  share unknown); `init`'s copy (cannot be patched by a bind); and any non-zlib
  word-form site the LRU never caught (ranks 5–11 in `MEASURE.md`, unmeasured).
  The earlier claim that the count "should fall to the low thousands" is **not
  supported** by the evidence and is withdrawn; the residual is unquantified
  until measured on-device.
* **Speed-up:** not measured. The reduction is in trap *count*; CPU time saved =
  count × per-trap cost, and the per-trap cost of the k15 `#UD` path has not been
  measured (each 1 µs of per-trap cost would correspond to ~250 CPU-seconds per
  boot at 2.48×10⁸ traps — a conversion, not an estimate).
* **Native tree:** its captured load (vm1/vm1b) is ML-KEM plus init's 16 traps;
  this patch removes neither. Native boots never completed in wave 3 and no libz
  cluster was captured there, so no native-tree benefit has been observed yet.

**Acceptance measurement (turns the INFERRED share into MEASURED):** boot the same
VM tree twice — once with the gated binds, once with the rc gate value
deliberately non-matching (so nothing binds) — and read the first line of
`/sys/kernel/gb_vnni/summary` (`emulated N vex128 … word …`) at the same uptimes
(e.g. 200 s and 434 s), subtracting the KAT's own logged counts. Also confirm
`adler32_simd_` RIPs (`…067`, `…145`–`…1ea` page offsets) no longer appear in the
LRU, and `grep libz /proc/<pid>/maps` shows the bound file for zygote/system_server.

---

## 6. APEX-resident copies, OTA, verified boot — see INTEGRATION.md

* **OTA / other slot:** handled by the build-fingerprint gate (§3); a different
  build never gets this build's libz. A rollback to this build re-enables it.
  Since wave 5b a second, independent layer exists: the OTA gate in GBINIT removes
  `/odm/vnni` before init starts whenever the boot slot's init_boot fingerprint is
  not the validated one (`/odm/etc/gb/ota_validated`), and posts a "System update
  not validated" notification (INTEGRATION.md §3; boot-menu tokens `gb.otagate=0`
  = layer off, `gb.otagate=force` = test the unvalidated path).
* **Kill switch:** `gb.vnni=0` on the kernel command line (press `e` at the boot
  menu, one boot; or edit `loader/entries/gb-<x>.conf` on the ESP to keep it) makes
  GBINIT delete `/odm/vnni` before init starts; the stock libz then runs under
  emulation as before (INTEGRATION.md §6 (3)).
* **APEX copies** (`com.android.runtime/bin/linker64`,
  `com.google.android.appsearch/…/libicing_anywhere.so`) are **not bound**: `/apex`
  is a private mount point per init mount namespace (bootstrap vs default), so
  an init bind at early-init is not seen by default-namespace processes; a
  `/data/apex` mainline update re-activates over the preinstalled APEX; and a bind
  over the `.apex` file itself would fail apexd's signature/verity check. Binding
  over a path *inside* an activated APEX does not touch the APEX file or its
  verity, but it is fragile for the reasons above.
* **Verified boot (orange):** a bind does not modify the dm-verity-protected
  partition or any APEX file, so AVB/dm-verity/apexd checks are unaffected. It
  does move the trust anchor of `libz.so` from the verified `/system` to the
  unverified ESP initrd / `/odm` tmpfs — the same trust level as everything else
  this pipeline already injects through `/odm` on an unlocked (orange) device. The
  generated rc remounts the bind read-only in init's mount namespace only (bind
  copies propagated to other namespaces may stay rw); the source inode stays
  writable by root through `/odm/vnni/` (the `/odm` tmpfs is read-write). SELinux:
  the global mode is enforcing (lapis's user build ignores
  `androidboot.selinux=permissive`), and every type of this build is permissive
  through `inject/odm_sepolicy.cil`; `build_esp.py` refuses a cil that drops
  `init`, `vendor_init` or the other required domains (INTEGRATION.md §5).

---

## 7. Files and reproduce

```
patcher/
  vnni_patch.py     the patcher: sha256-pinned in and out, ELF-aware, length-preserving byte edits,
                    --out-rc (fingerprint-gated binds), --odm-root (staging copies), out-manifest
  gen_manifest.py   derives the manifest from the binaries (byte signature + scan.json cross-check,
                    patched_sha256, deploy blocks from --build-prop)
  manifest.json     12 targets for build 16658739 (2 deployable libz branch_flip + 10 static flag_zero)
  LICENSE           Apache-2.0
tests/
  vnni_probe.c      bionic probe: 9 ops vs scalar reference + per-thread SIGILL(RIP) counter
  run_equiv.sh      builder: patch (+rc) -> NDK build -> chroot of carved lapis -> run -> VERDICT
  disasm_paths.py   disassembly + whole-binary reachability proof (R1-R6), flag_zero checks
  test_patcher.py   patcher safety tests (pins, refusals, OTA/fingerprint gate)
component/
  vnni.json         DRAFT native-tree component (template; regenerate per slot)
  vnni.rc           the generated, fingerprint-gated rc for build 16658739 (+ ordering notes)
  gb_vnni.sh        optional runtime sha256 guard (stricter alternative to the init binds)
out/
  patched/system/lib64/libz.so, patched/vendor/lib64/libz.so   patched copies (build 16658739)
  vnni.rc                                                      rc generated by the run
  out-manifest.json                                            orig->patched sha256, edits, gates
evidence/
  equiv_run.log     the chroot equivalence/zero-VNNI run (+ VERDICT)
  disasm_proof.log  the 12-target disassembly/reachability proof (ALL PASS)
  test_patcher.log  patcher safety tests (0 failures)
  builder_env.txt   CPU/kernel/toolchain the proof ran on
README.md, INTEGRATION.md, MEASURE.md
```

Reproduce on the builder (read-only on the carved tree; writes only under
`/var/tmp/vnniperf/{vnni,evidence,review}` and a private `chroot.*` it removes):

```
cd /var/tmp/vnniperf
# manifest (provenance; add the 10 static FILE specs as in the committed manifest)
python3 -I patcher/gen_manifest.py --scan scan/full/scan.json --root . \
    --build "CL3B.260622.276.R4 / 16658739" \
    --build-prop system=fs/system_b/system/build.prop --build-prop vendor=fs/vendor_b/build.prop \
    "fs/system_b/system/lib64/libz.so>system/lib64/libz.so:execution" \
    "fs/vendor_b/lib64/libz.so>vendor/lib64/libz.so:execution"  <...static copies...>
# equivalence + zero-VNNI + gated rc (needs sudo for the chroot mounts, like the nvk tests)
bash tests/run_equiv.sh
# disassembly / reachability proof
python3 -I tests/disasm_paths.py --objdump objdump --readelf readelf \
    --llvm-readelf /build/ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf \
    --manifest patcher/manifest.json --root .
# patcher safety tests
python3 -I tests/test_patcher.py --system-libz fs/system_b/system/lib64/libz.so \
    --vendor-libz fs/vendor_b/lib64/libz.so --system-prop fs/system_b/system/build.prop \
    --vendor-prop fs/vendor_b/build.prop
```

---

## 8. Caveats

* The libz **share** of the 2.48×10⁸ is INFERRED (LRU limitation, §5); the
  measured lower bound is ~2.9×10⁴. Making it MEASURED needs the on-device A/B
  boot in §5 or a per-RIP-page histogram in the kernel counter.
* Per-trap CPU cost not measured; the saving is stated in trap count only.
* The equivalence run was SELinux-non-enforcing (as the existing nvk chroot tests
  are). On the device the global mode is enforcing, but every type of this build
  is permissive through `inject/odm_sepolicy.cil` (wave 5 safety audit SA-1). If
  that list is ever dropped, init needs `mounton`/`relabelto` on
  `system_lib_file`/`same_process_hal_file` for these two paths (`INTEGRATION.md` §5).
* Not scanned/covered: `/data` APEX updates, user apps, JIT; the AppSearch and
  init copies remain emulated.
* The fingerprint gate trusts that one build fingerprint ⇔ one image; a re-signed
  image with an unchanged fingerprint but a different libz would still be bound.
  `component/gb_vnni.sh` (exact sha256 of the bound-over file at boot) closes that
  if needed.

## 9. Review log (2026-10-26)

Adversarial review of this prototype; fixes applied here, nothing outside this
directory changed:
* **HIGH — OTA/slot:** unconditional bind would survive an A/B OTA via
  `gb_slotsync`'s reuse of `/gb/inject.gz` → fingerprint-gated rc generated by
  the patcher (§3), template rc/json updated, tests added.
* **MEDIUM — proof completeness:** reachability was only checked in the scalar
  window → whole-binary proof R1–R6 incl. relocations/ifunc (§4); flag_zero now
  checks the reader really gates the SIMD function and that `%eax` is dead.
* **MEDIUM — test coverage:** only seed-1, aligned, ≤64 KiB adler32 and one
  compress2 level → chained/unaligned/large/combine/streaming (3 wrappers × 4
  levels)/threaded ops, plus an automatic VERDICT.
* **MEDIUM — estimate:** "low thousands" residual and "statics remove the rest"
  were unsupported (icing/init are not deployable by default) → §5 rewritten
  with the measured lower bound and an acceptance measurement.
* **MEDIUM — hardening:** bound tmpfs inode was writable → read-only remount
  (init's mount namespace only; see INTEGRATION.md and w5/INTEGRATE_REVIEW.md F1).
* **LOW:** `gb_vnni.sh` used init's mount syntax with toybox (always failed);
  patcher crashed on a symlinked target; output hash not pinned; `run_equiv.sh`
  left a root-owned `chroot.*` skeleton; INTEGRATION's early-init/ordering
  notes.
* **Licence:** no gbOS86 code copied. gbOS86 locates the gate with a regex and a
  scalar-entry byte check and patches in place; this patcher uses a
  binary-derived, sha256-pinned manifest with ELF vaddr mapping and a separate
  generator cross-checked against `scan.json`. Shared: the idea (`je` → `nop; jmp`),
  which is credited, and generic PT_LOAD vaddr→offset helpers (necessarily
  similar, independently written).
