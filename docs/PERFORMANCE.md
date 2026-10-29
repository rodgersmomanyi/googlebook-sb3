# Performance: AVX-VNNI emulation and the libz patch

## The finding

Googlebook OS (lapis) is compiled for a CPU target that includes **VEX AVX-VNNI** (`vpdpwssd`, `vpdpbusd` in their
VEX encoding). The Surface Book 3's i7-1065G7 (Ice Lake) has the EVEX form (`avx512_vnni`) but **not** the VEX form.
In the VM our `.vmx` hides AVX-VNNI from the guest as well. Every such instruction therefore raises `#UD`, and our
kernel (build #15, [kernel/K15.md](../kernel/K15.md)) emulates it in the trap handler. This is correct, but each
instruction costs a trap.

We measured where those traps come from (in [native/vnni-perf/MEASURE.md](../native/vnni-perf/MEASURE.md); *measured*
= read from the kernel counter or the binaries, *inferred* = reasoned from those facts):

* **Volume (measured).** One VM-tree boot made **248,149,863** emulations by about 434 s, not counting the kernel's
  own known-answer test. All of them were the word form (`vpdpwssd`) and 99.998 % were VEX.128. The load is
  front-loaded: on average 1.06 million per second over the first 199 s.
* **Source (measured).** The kernel's counter keeps the 16 most recent (process, address) pairs. Every pair it caught
  from Android was inside Chromium-zlib's **`adler32_simd_`** in `/system/lib64/libz.so`, plus one in a static zlib
  copy inside AppSearch. The callers were dex2oat, Profile Saver and app threads. `/vendor/lib64/libz.so` has the same
  code.
* **Why (measured for zlib).** zlib's `adler32_z` picks its SIMD path after testing for **SSSE3**, which Ice Lake has.
  The compiler fused the multiply-add of that SSSE3 code into VEX `vpdpwssd`, so the "SSSE3" path traps on this CPU.
* **Share (measured in wave 5).** The 16-entry list alone cannot split up the total, so this was first an inference.
  The wave 5 before/after measurement [below](#measured-effect-wave-5) confirms it: with only the two `libz.so`
  libraries patched, the emulations at `sys.boot_completed` fell from 211,368,597 to 756.
* **Native tree (measured).** Its captured load was different: BoringSSL ML-KEM (VEX.256) plus 16 traps in `init`.
  The libz patch removes neither.

## The fix: a 2-byte, pinned, build-gated libz

Wave 5 binds a patched copy over each of the two exported `libz.so` libraries (system and vendor) at boot, and only
on the exact lapis build the copy was made from.

* **The edit.** In `adler32_z`, the branch that chooses between the SIMD and the scalar path is changed from
  `je scalar` (`0f 84 58 ff ff ff`) to `nop; jmp scalar` (`90 e9 58 ff ff ff`) at file offset `0xa7d5`. That is two
  bytes, and the length is unchanged. Buffers of 64 bytes or more now always take zlib's own scalar `adler32` code.
  The `vpdpwssd` bytes stay in the file but are unreachable. `crc32` and inflate are untouched.
* **Proof the results are unchanged.** The test machine was an Ice Lake CPU with a stock kernel, so a VEX AVX-VNNI
  instruction faults there:
  * **Results.** A probe compares adler32, chained, unaligned and large inputs, combine, compress/uncompress and
    deflate/inflate streams at four levels and three wrappers, with eight racing threads, against an independent
    scalar reference. The unpatched library faults inside `adler32_simd_`; the patched one returns the reference
    result in every case.
  * **Reachability.** A disassembly proof shows that no VEX AVX-VNNI instruction is reachable from any entry point of
    the patched files. It covers code references, data words, relocations, ifuncs and jump tables.

  Details: [native/vnni-perf/README.md](../native/vnni-perf/README.md).
* **Pinned to exact bytes.** [`patcher/manifest.json`](../native/vnni-perf/patcher/manifest.json) holds, per target,
  the sha256 of the original library, the edit and the sha256 of the result. For build `CL3B.260622.276.R4` /
  16658739:

  | Library | Original | Patched |
  |---|---|---|
  | system `libz.so` | `371f70d6…` | `aa60fb2b…` |
  | vendor `libz.so` | `e45aecb6…` | `fc4987b2…` |

  A library with any other bytes is **skipped**. It then stays under the kernel's emulation, which is slower but
  always correct.
* **Made from your own image, never distributed.** [`vnni_prepare.py`](../vnni_prepare.py) produces the patched
  copies, and you run it on demand:
  1. it reads `lapis.bin` read-only and carves the boot slot's `system` and `vendor`;
  2. it extracts them on the Linux builder;
  3. it runs [`vnni_patch.py`](../native/vnni-perf/patcher/vnni_patch.py), checks both hashes and reads the build
     fingerprints from the partitions' own `build.prop`;
  4. it writes `inject/vnni/<id>/`.

  These are Google's libraries with a 2-byte edit, so they are never committed (`.gitignore`, and the repository
  sync refuses them). Without `inject/vnni/`, `build_esp.py` packs nothing and the images behave as before.
* **Gated on the build fingerprint.** `build_esp.py` packs the copies as `/odm/vnni/<id>/` into both trees and adds
  one action per library as the **last** block of `gb.rc`, after the `/vendor` overlay:

  ```
  on early-init && property:ro.system.build.fingerprint=Android/desktop_system/desktop:17/CL3B.260622.276.R4/16658739:user/release-keys
      restorecon /odm/vnni/<id>/system/lib64/libz.so
      mount none /odm/vnni/<id>/system/lib64/libz.so /system/lib64/libz.so bind
      restorecon /system/lib64/libz.so
      mount none none /system/lib64/libz.so remount bind ro
  ```

  The vendor action is the same, gated on `ro.vendor.build.fingerprint`. The gate matters because after an A/B
  system update `gb_slotsync` boots the new slot with the **same** initrd. A different build never matches the
  fingerprint, so it keeps its own libz. The bind does not modify any verified partition or APEX, so dm-verity and
  apexd are unaffected. The bound file is remounted read-only in `init`'s mount namespace only; the inode stays
  writable through the rw `/odm` tmpfs (same trust level as every other `/odm` injection).
* **Second layer: the OTA gate.** Independently of those triggers, `gbinit` removes `/odm/vnni` before Android starts
  whenever the boot slot is not the build the image was made for ([OTA.md](OTA.md#the-ota-gate-wave-5)). After a
  Google update the patch is therefore skipped twice, and the stock libz of the new build runs under emulation.

## Kill switch

* **For one boot:** press **`e`** at the systemd-boot menu (`editor yes`, `timeout 3`) and append **`gb.vnni=0`** to the
  kernel command line. (A shipped boot entry never carries it; `verify_image.py` V31 flags it on any entry except the
  VNNI-off test entry.) A one-off boot-menu edit does not persist across reboots or slot switches.
* **To make it permanent:** edit `loader/entries/gb-<x>.conf` on the ESP and append `gb.vnni=0` to the `options` line
  (`gb_slotsync` copies the entry file on slot switch, so a permanent entry edit carries over to the other slot).
  `verify_image.py` will then report V31 for that entry; this is expected for a user-edited ESP.
* **Note:** `gbinit` reads only `/proc/cmdline`, so putting `gb.vnni=0` in `gb/bootconfig_extra` (bootconfig) is ignored.
* When `gb.vnni=0` is set, `gbinit` deletes `/odm/vnni` before Android's init starts. The two bind actions fail
  harmlessly (init logs the missing file and goes on), and the stock libz runs under emulation as before. No other
  value of `gb.vnni` does anything.

## Checking it

* **Before flashing:** `verify_image.py` check **V31**. It requires that:
  * the image's `/odm/vnni` equals `inject/vnni` byte for byte;
  * every file matches a deployable target of the reviewed manifest;
  * `gb.rc` has exactly one gated five-line action per file, after the `on early-init` `/vendor` overlay;
  * no other line binds or names `libz.so`, no `libz.so*` file exists outside `/odm/vnni/<id>/`, and `/odm/etc/build.prop`
    does not override the gate properties;
  * `gbinit` has the kill switch.
* **On a running system** (root shell):
  * `readlink /proc/{1,self,$(pidof zygote64)}/ns/mnt` and `grep libz /proc/{1,$(pidof zygote64)}/mountinfo` show the
    two binds in both namespaces (`ro` in `init`'s namespace, `rw` allowed in the default namespace);
  * the first line of `/sys/kernel/gb_vnni/summary` (`emulated N ...`) is the emulation count since boot. The
    boot-log summary on a native stick shows it as `-- gb_vnni`.

## Measured effect (wave 5)

Two throwaway VMware VMs (4 GiB, 2 vCPUs, the same CPUID masks as the daily VM) booted the same VM-tree image at the
same second, from fresh `/data`. *Before* = the patch switched off with `gb.vnni=0`; *after* = the patch active. The
second boot restarted both disks with warm data. Counts are the `emulated` total of `/sys/kernel/gb_vnni/summary`;
boot time is init's kernel-log timestamp of `processing action (sys.boot_completed=1)`. Each case was measured once,
with both VMs running at the same time; the two boot-time pairs agree. Full record:
[native/vnni-perf/w5/MEASURE_W5.md](../native/vnni-perf/w5/MEASURE_W5.md).

### First boot (fresh data)

| | before (`gb.vnni=0`) | after (patch active) |
|---|---:|---:|
| emulations at t ≈ 60 s | 39,117 | 16 |
| emulations at t = 200 s | 48,428,050 | 752 |
| emulations at t = 420 s | 126,326,002 | 9,816 |
| emulations at `sys.boot_completed` | 211,368,597 | 756 |
| emulations at t = 1200 s | 229,191,430 | 198,420 |
| **boot to `sys.boot_completed`** | **587.7 s** | **313.6 s (−46.6 %)** |
| boot-time dexopt (`gb_dexopt`, 4 packages) | 643.5 s | 480.3 s (−25 %) |
| `cmd package compile -m speed -f`, 4 packages, both VMs at once | 255.9 s | 239.9 s (−6 %) |
| emulations during that compile | 19,558,757 | 66,668 |
| adler32 throughput, system / vendor `libz.so` | 5.9 / 5.4 MiB/s | 538 / 1,229 MiB/s |

### Second boot (warm data)

| | before (`gb.vnni=0`) | after (patch active) |
|---|---:|---:|
| emulations at t = 60 s | 305,760 | 752 |
| emulations at t = 200 s | 93,316,268 | 18,196 |
| emulations at t = 420 s | 141,358,142 | 18,676 |
| **boot to `sys.boot_completed`** | **226.0 s** | **118.0 s (−47.8 %)** |

**On the patched VM:** both binds were in place and read-only in the mount namespaces of `init`, the console shell and
`system_server`; `system_server`, `zygote64` and `surfaceflinger` mapped the patched vendor copy and `logd` the
patched system copy; the bound files had the manifest's sha256 values. No emulation came from either `libz.so`; what
remains is AppSearch's static zlib, ML-KEM in the tethering APEX and `init`'s own copy. An on-device adler32 test
(3,547 cases, including a dex header checksum and a zlib stream made on another machine) passed on both VMs, app
installs and every compile call returned `Success`, and there were no tombstones, no kernel warnings and no enforced
SELinux denials. The explicit-compile results scatter per package (n = 1); the robust numbers are the boot times, the
boot-time dexopt total and the emulation counts.

**Later boots.** The daily VM, after its ESP was updated to this image: 136,814 emulations after 8 minutes, against
138.7 million in the same window before the update. The native image in a VMware smoke test (no Surface hardware, the
boot never completes there): 16 emulations (`init`'s own) in every snapshot up to 900 s, against up to 128,080 in the
wave 3 smoke test.

## What is not covered

* **Other libraries.** Not covered are BoringSSL ML-KEM (VEX.256, at least 5,216 per VM boot and the main load
  measured in the native tree), the static zlib copies in AppSearch (inside an APEX: an init bind is not seen in other
  mount namespaces) and in `init` itself (already running before any rc action), and any rarer site the kernel's
  16-entry list never caught. They stay under emulation, which is correct.
* **CPU time.** The CPU time per trap of the k15 emulator was not measured directly; about 2.8 µs per emulation is
  inferred from the adler32 test's timings. The boot times above are measured.
* **Native tree.** The binds are the same there, but no boot on the Surface Book 3 has run yet, so the native benefit
  is not measured.
* **Fingerprint gate.** The gate assumes that one build fingerprint means one image. A re-signed image with an
  unchanged fingerprint but a different libz would still get the bind. `native/vnni-perf/component/gb_vnni.sh` (an
  exact sha256 check at boot) is the stricter alternative if that ever matters.
* **SELinux.** The global mode is **enforcing** (`getenforce` prints `Enforcing`; lapis's user build ignores
  `androidboot.selinux=permissive`). Every type of this build is permissive through our
  [`inject/odm_sepolicy.cil`](../inject/odm_sepolicy.cil), and that is why the bind and relabel steps work: `init`
  and `vendor_init` are permissive types. `build_esp.py` refuses a cil without them (`OTA_REQUIRED_PERMISSIVE`). If
  that list is ever dropped, init needs `mounton`/`relabelto` on these two paths
  ([INTEGRATION.md](../native/vnni-perf/INTEGRATION.md) section 5).

## Credits

**gbOS86** ([gbos-x86](https://github.com/linuxkid473/gbos-x86)) first observed that `libz.so`'s `adler32` is the
dominant AVX-VNNI trap source on lapis and that the branch after the CPU-feature test gates the SIMD path (the
`je` → `nop; jmp` idea). Our patcher, manifest generator, proofs and gated integration are a clean-room
implementation; **no gbOS86 code was copied**. Only that idea and its published sha256 values were used.
