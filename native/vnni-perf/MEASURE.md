# vnni-perf: where the VEX AVX-VNNI emulation load comes from (lapis on Ice Lake)

2026-10-26. Build `CL3B.260622.276.R4` / 16658739 (fingerprints of system, system_ext, vendor and product all checked
in the carved images). Kernel k15 (`0110` VNNI emulator + `/sys/kernel/gb_vnni/summary` counter, see
`C:\GB-build\kernel\K15.md`).

Labels: **MEASURED** = read from the wave-3 counters/serial logs or from the binaries themselves.
**INFERRED** = reasoned from MEASURED facts, not observed directly.

Rules kept: no VM was started; nothing under `C:\GB-x86\**`, `inject\**` or any other component was written;
`C:\GB-x86\lapis.bin` was only opened read-only, by our own `tools\carve_stream.py` (it re-implements the GPT/liblp
parsing and imports nothing from `C:\GB-x86`, so not even a `__pycache__` was written there). Builder work ran in
`/var/tmp/vnniperf` under `nice -n 19 ionice -c3`; `/build/dist*` was not touched (the only `/build` use was running
the prebuilt `llvm-objdump` read-only). gbOS86 was used for ideas and for its published sha256 values only; no code
was copied. Everything this step wrote is under `C:\GB-build\native\vnni-perf\` (tools, evidence, this file) and
builder `/var/tmp/vnniperf`.

---

## 0. Findings

1. **MEASURED: the load is word-form, VEX.128.** In the VM-tree boot `vm2`, after subtracting the KAT's own exact
   counts, **248,149,863** emulations came from Android by the quiet point (about 434 s). All of them are word forms
   (`vpdpwssd`/`vpdpwssds`, the `word` counter). The non-KAT `byte` count is **0**. 248,144,647 (99.998 %) are VEX.128
   and **5,216** are VEX.256. `vm2-early` gives the same picture.
2. **MEASURED: every non-KAT LRU entry from the VM-tree boots is Chromium-zlib `adler32_simd_`.** For each
   (comm, mapping) cluster, every RIP was matched to a verified instruction, using its page offset plus the deltas
   between RIPs. Five clusters in five comms (`dex2oat`, `Profile Saver`, `LazyTaskWriterT`, `DefaultDispatch`,
   `launcher-loader`) map to `/system/lib64/libz.so` at vaddr `0x11067…0x111ea`. That function holds exactly **9**
   VEX.128 `vpdpwssd`, and those are the 9 page offsets seen at runtime. One cluster (`pool-265-thread`) maps to the
   static zlib copy in AppSearch's `libicing_anywhere.so`.
3. **MEASURED: in the native tree the load is BoringSSL ML-KEM.** In `vm1`/`vm1b` (VMware, where boot never completes),
   all **128,064** VEX.256 emulations by 900 s equal exactly **174 × 736**. Every snapshot is an exact multiple of 736
   (3, 13, 30, 79 and 174 events). They come from `mlkem::encrypt_cpa<3>` and `mlkem::mlkem_decap_no_self_test<3>` in
   the tethering APEX's `libcrypto_httpengine.so`, in processes whose comm is `main` (zygote, INFERRED). On top of
   that, 16 VEX.128 emulations came from `init`.
4. **MEASURED: the rate is front-loaded.** In `vm2-early`, 211.39 M non-KAT emulations happened in the first 198.7 s
   (mean **1.06e6/s**). The next 75.7 s added 8.73 M (**1.15e5/s**). `vm2` reached 248.15 M by about 434 s, after which
   the counter stood still for 5 s. The often-quoted "~5.7e5/s" is the **whole-run mean** of vm2 (248.15 M / 434–439 s),
   not a steady-state rate.
5. **MEASURED: the static scan finds 32,563 verified VEX AVX-VNNI instructions in 128 of 3,325 ELF objects** (118
   distinct files; system, system_ext, vendor, product, 46 APEX payloads and the `.so` files inside 400 APK/JAR/ZIP
   files). There is no `vpdpwssds` anywhere. llvm-objdump agrees address-for-address on all 16 libraries it could
   open. It refuses the two copies of `libzlib_rs.dylib.so`, whose 2 sites were checked by hand.
6. **The VNNI exists because the platform is compiled for an AVX-VNNI target, and the CPU gates test older ISAs.**
   (MEASURED for the gates, INFERRED for the compiler mechanism.) The platform-built code (104 ELFs) contains
   `vpdpwssd` where the source has `pmaddwd`+`paddd` (`_mm_madd_epi16`+add) inside SSE2/SSSE3/SSE4.1/AVX2 intrinsics or
   auto-vectorised loops. The compiler fuses them once AVX-VNNI is a target feature. Scalar code is built for that
   target too: `mulx` (BMI2) appears in plain `adler32_z`, with no gate. The runtime gates in front of these functions
   test **SSSE3 / SSE4.1 / AVX2**, never AVX-VNNI, so they pass on Ice Lake and the VNNI traps. This is MEASURED for
   libz and the 11 static zlib copies (disassembled gate + flag writer). For the codec and ML families it is INFERRED
   from their ISA-suffixed names and cpuid-driven selection tables. Generic code (ML-KEM, protobuf, Skia, audio mixer …)
   has no gate at all.
7. **libz's gate is SSSE3, not "simd" (MEASURED).** The path is `adler32_z` (only for `len >= 64`) → `cmpl $0,0x20000`
   at `0xa7ce` → `je scalar` at `0xa7d5` (= `adler32_z+0xb5`, the byte gbOS86 patches) → `call adler32_simd_`.
   `_cpu_check_features` (cpuid at `0x11648`) stores `CPUID.1:ECX & 0x200` (SSSE3) at `0x20000`. The linker64 copy keeps
   symbol names and calls this global **`x86_cpu_enable_ssse3`**. gbOS86's comment calls it `x86_cpu_enable_simd`, but
   that is the neighbouring flag at `0x20004` (SSE2 && SSE4.2 && PCLMUL), which gates `crc32_z`. Our `libz.so` sha256
   values equal gbOS86's MANIFEST "original" hashes for this build: system `371f70d6…683b`, vendor `e45aecb6…2590`.
8. **MEASURED (static call sites): what reaches `adler32`.** These callers do: ART dex checksums
   (`DexFile::CalculateChecksum`/`ChecksumMemoryRange`), `dex2oat`'s `OatWriter` (adler32 over everything it writes),
   `OatHeader::ComputeChecksum`, `ImageHeader::WriteData`, ART profiles (`libprofile` zlib-wrapped deflate/inflate),
   PNG (`libpng` `png_inflate_claim`/`png_deflate_claim`) and `java.util.zip` (`Adler32`, `Inflater`/`Deflater`).
   **APK/ZIP extraction does not reach adler32**: every `inflateInit2_` in libziparchive, libandroidfw,
   libandroid_runtime, libartbase and service.incremental passes `windowBits = -15` (raw deflate, CRC32 only).
9. **Correctly dispatched code is 54 % of the static VNNI and never traps on Ice Lake (INFERRED from the gates).** That
   is XNNPACK `*__avxvnni*` kernels (cpuinfo, CPUID.(7,1):EAX[4]) in 2 named libraries plus 22 stripped prebuilt
   APK/vendor libraries. Four of those are byte-identical to the named kernel set; all 22 contain a CPUID(7,1) reader.
   **Hazard (INFERRED weight):** in vendor `libcros_ml_core_internal.so`, the XNNPACK `*__avx256vnni*` (137) and
   `*__avx2*`/`*__avx256skx*` kernels are selectable on Ice Lake (AVX512-VNNI, AVX2) and still contain VEX VNNI.
   Those traps will appear when its models run.
10. **Side finding (MEASURED static, INFERRED gate):** 22 libraries carry VEX **AVX-VNNI-INT8** (`vpdpbssd`, 72 each, in
    XNNPACK `*__avxvnniint8*` kernels). k15 does not emulate them, so they would SIGILL. They sit behind cpuinfo's
    AVX-VNNI-INT8 check, which is never true on Ice Lake.

---

## 1. Runtime evidence: every gb_vnni summary in wave 3

Extractor: `tools\extract_gbvnni.py` (→ `evidence\gbvnni_records.json`). RIP mapper: `tools\match_rips.py`
(→ `evidence\rip_matches.json`, `evidence\lru_clusters.md`). Snapshot tables: `tools\summaries_md.py`
(→ `evidence\summaries.md`, Appendix B).
Source dir: `C:\GB-build\native\dgpu\w3\evidence\build\`. Serial logs carry each kmsg line twice (two consoles) and
are de-duplicated.

### 1.1 Sources

| run | tree | what | counter reads |
|---|---|---|---|
| `vm1` | native (`gb.hw=native`), VMware; `sys.boot_completed` never comes | gblog snapshots t40/t90/t180/t420/t900 (`-- gb_vnni` block) | 5 (+ duplicates in `vm1_eval_w3.txt`, `t900_summary_block.txt`, `result_w3.txt`) |
| `vm1b` | native + bring-up config | gblog t40/t90/t180 | 3 |
| `vm2-early` | VM tree + KAT, started right after `sys.boot_completed` (189.0 s; KAT start 192.8 s) | `gb_vnni_kat: summary …` lines in `serial0.log` / `3-adb_dmesg.txt` | 2 (198.7 s, 274.4 s) |
| `vm2` | VM tree + KAT with 240 s quiet wait (E-16), boot_completed 186.3 s | same | 1 (442.0 s) |

### 1.2 Totals and the non-KAT share

The KAT's own counts are exact: `emulated 404,620` (vex128 202,308, vex256 202,312, word 202,306, byte 202,314) per
main battery, plus 4,608 per quiet re-run (2,304 VEX.128 word + 2,304 VEX.256 byte).

| snapshot | emulated | vex128 | vex256 | word | byte | ymm_init | KAT part | **non-KAT** | non-KAT vex256 | non-KAT byte |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| vm1 t40 | 2,224 | 16 | 2,208 | 2,224 | 0 | 0 | 0 | 2,224 | 2,208 = 3×736 | 0 |
| vm1 t90 | 9,584 | 16 | 9,568 | 9,584 | 0 | 0 | 0 | 9,584 | 9,568 = 13×736 | 0 |
| vm1 t180 | 22,096 | 16 | 22,080 | 22,096 | 0 | 0 | 0 | 22,096 | 22,080 = 30×736 | 0 |
| vm1 t420 | 58,160 | 16 | 58,144 | 58,160 | 0 | 0 | 0 | 58,160 | 58,144 = 79×736 | 0 |
| vm1 t900 | 128,080 | 16 | 128,064 | 128,080 | 0 | 0 | 0 | 128,080 | 128,064 = 174×736 | 0 |
| vm1b t180 | 23,568 | 16 | 23,552 | 23,568 | 0 | 0 | 0 | 23,568 | 23,552 = 32×736 | 0 |
| vm2-early 198.7 s | 211,809,117 | 211,599,157 | 209,960 | 211,599,891 | 209,226 | 211,389,905 | 418,444 (battery + 3 re-runs) | **211,390,673** | 736 | 0 |
| vm2-early 274.4 s | 220,956,521 | 220,535,257 | 421,264 | 220,538,069 | 418,452 | 220,115,959 | 836,888 (2 runs) | **220,119,633** | 2,816 | 0 |
| vm2 442.0 s | 248,554,483 | 248,346,955 | 207,528 | 248,352,169 | 202,314 | 248,143,785 | 404,620 | **248,149,863** | 5,216 | 0 |

Checks (MEASURED arithmetic):
* vm2: 248,554,483 − 404,620 = **248,149,863**. That equals the KAT's own "quiet wait 240 s (emulated total
  248149863, unchanged for 5 s)" line, so nothing else emulated during that KAT run. Non-KAT word = 248,352,169 −
  202,306 = 248,149,863, so **every** non-KAT emulation is a word form. Non-KAT VEX.128 = 248,144,647, VEX.256 = 5,216.
* vm2-early: non-KAT byte = 209,226 − 202,314 − 3×2,304 = **0**, and 418,452 − 2×209,226 = **0** at 274.4 s.
* `ymm_init` ≈ `emulated`: 99.998 % of non-KAT emulations found the YMM state in its init state (vm2: 248,143,781 of
  248,149,863). INFERRED: the trapping code works on xmm after a `vzeroupper`, i.e. SSE-style intrinsic code. In the
  native tree `ymm_init` is 0, which fits the ymm-using ML-KEM code.
* `irqon == emulated` in every snapshot. `efault` is only the KAT's (2 per battery or re-run: 8, 16, 2; 0 in vm1).

Rates (MEASURED arithmetic):

| window | non-KAT emulations | mean rate |
|---|---:|---:|
| vm2-early, boot → 198.7 s | 211,390,673 | 1.06e6 /s |
| vm2-early, 198.7 → 274.4 s (75.7 s) | 8,728,960 | 1.15e5 /s |
| vm2, boot → quiet point (~434 s; total 248,149,863, unchanged 434–439 s) | 248,149,863 | 5.7e5 /s (whole-run mean) |
| vm1 (native tree), 40 → 900 s | 125,856 | 146 /s (= one 736-trap ML-KEM event every ~5 s) |

The KAT's foreign-activity windows give two point samples. At 198.5 s there were 28,797 foreign emulations in the
~5.5 s battery, from `android.fg` and `pool-166-thread`, all VEX.128 word. At 274.3 s there were 18,839 (18,519
VEX.128 + 320 VEX.256), from `DefaultDispatch` and `pool-267-thread`.

### 1.3 The LRU RIPs mapped to libraries

Method: a library is mapped at a page-aligned base, so `RIP − base` must equal a verified VEX VNNI vaddr **for every
RIP of a (comm, mapping) cluster**. All non-KAT clusters match fully (12/12, 9/9, 10/10 …). The vendor and system
`libz.so` have the same text layout around `adler32_simd_` (different files), so their matches cannot be told apart.
The processes (system_server, apps, dex2oat) load `/system/lib64/libz.so` through the platform linker namespace
(INFERRED).

| source | t | comm | RIPs | Σ count | library | load base | function | vaddrs |
|---|---|---|---:|---:|---|---|---|---|
| vm1, vm1b (all 8 snapshots) | – | `main` | 12 | 736 | apex:tethering `lib64/libcrypto_httpengine.so` | differs per process | `mlkem::encrypt_cpa<3>` (8 RIPs × 80) + `mlkem::mlkem_decap_no_self_test<3>` (4 × 24) | `0xc7d3f…0xc8684`, `0x74b90…0x74c50` |
| vm1, vm1b (all 8 snapshots) | – | `main` (a 2nd process) | 4 | 320 | same | differs | `mlkem::encrypt_cpa<3>` (cut off by the 16-entry LRU) | `0xc7f7f…0xc8684` |
| vm2 | 442.0 s | `Profile Saver` | 9 | 21,728 | `/system/lib64/libz.so` (= vendor layout) | `0x7cc372853000` | `adler32_simd_` | `0x11067 0x11145 … 0x111ea` |
| vm2 | 442.0 s | `dex2oat` | 3 | 7,191 | `/system/lib64/libz.so` | `0x7a8c48149000` | `adler32_simd_` | `0x111b6 0x111d2 0x111ea` |
| vm2-early | 198.7 s | `pool-265-thread` | 10 | 58 | apex:appsearch `lib64/libicing_anywhere.so` | `0x7abf52322000` | `adler32_simd_` (static zlib copy) | `0x2a4f6b … 0x2a508a` |
| vm2-early | 198.7 s | `LazyTaskWriterT` | 6 | 288 | `/system/lib64/libz.so` | `0x7ac3657d3000` | `adler32_simd_` | `0x11145 … 0x111b6` |
| vm2-early | 274.4 s | `DefaultDispatch` | 9 | 5,995 | `/system/lib64/libz.so` | `0x7ac3657d3000` | `adler32_simd_` | `0x11067 0x11145 … 0x111ea` |
| vm2-early | 274.4 s | `launcher-loader` | 6 | 4,032 | `/system/lib64/libz.so` | `0x7ac3657d3000` | `adler32_simd_` | `0x1116a … 0x111ea` |
| vm2, vm2-early | – | `vnni_kat` | – | 404,620 / 418,438 | the KAT binary (not part of the image) | – | – | – |

The same base `0x7ac3657d3000` in `LazyTaskWriterT`, `DefaultDispatch` and `launcher-loader` means one mapping
inherited from zygote, i.e. system_server and app processes (INFERRED). The 8 main-loop RIPs have equal counts, and
`0x11067` (the 32-byte tail loop) has fewer (73 vs 2,707; 112 vs 736), as expected for a loop.

**Bytes per trap (MEASURED from the disassembly, `evidence\libz_gate.txt`):** the `adler32_simd_` main loop
(`0x11120…0x111f6`) handles 4 × 32-byte blocks per iteration with 8 `vpdpwssd`, i.e. **1 trap per 16 bytes**. The
tail loop at `0x11043` handles 32 bytes with 1 trap. So 2.48e8 traps ≈ **4.0 GB of adler32 input** during the vm2 boot
(INFERRED: assumes all non-KAT traps are this loop, which is what every captured RIP says).

### 1.4 Comms seen by the kmsg bucket line (no RIP; rate-limited, first-seen order)

`gb_vnni: emulating VEX AVX-VNNI for comm=` is printed (rate-limited) when a new comm/RIP bucket enters the LRU, so it
lists who emulated, not how much. vm2, first-seen time (s): `init` 24.1 (T1 and T115, during post-fs-data),
`surface:disk$0..3` 29.6, `dex2oat64` 41.7 (several threads; odrefresh-style compile, INFERRED), `TextureLoadThre`
46.7, `dex2oat` 81.8, `com.goo:disk$0/1/3` 153.7–159.5, `com.and:disk$3` 164.3, `ndroid.systemui` 191.3,
`mdd-control-1` 196.9, `CronetNet` 202.3, `.apps.wallpaper` 207.3, `launcher-loader` 207.5, `DefaultDispatch` 217.4,
`glide-source-th` 222.4, `pool-309-thread` 227.5, `Profile Saver` 237.5, `ScionFrontendAp` 252.5, `lowpool[5]`
257.6, `glide-disk-cach` 268.0, `ng:quick_launch` 294.7, `peration loader` 299.7, `pool-91-thread-` 304.8,
`WNLOAD_COMPLETE` 316.3, `binder:10972_3` 358.2, `.gms.persistent` 402.3, `Blocking Thread` 407.4, `profman` 445.5,
`android.vending` 475.0. vm2-early adds `NotifInflation`, `id.desktop.tips`, `ptz-fgd-1-PHOTO`, `highpool[4]`,
`BG Thread #1`.

These comms fit the static call-site analysis (§3): dex/oat checksums (`dex2oat*`, `profman`), ART profiles
(`Profile Saver`) and PNG/bitmap paths (`TextureLoadThre`, `launcher-loader`, `.apps.wallpaper`, `glide-*`,
`LazyTaskWriterT`, `NotifInflation`). The library behind each comm is only MEASURED where §1.3 has a RIP. For the
others it is INFERRED.

---

## 2. Static scan of the active slot

### 2.1 Inputs (MEASURED)

Active slot: GPT `vbmeta_b` priority 15, successful 1 (`vbmeta_a` 0/0/0), so **slot b, LP metadata slot 1**. That
matches the VM log `init: [libfs_mgr] Created logical partition system_b on device /dev/block/dm-0`.
Carve: `python -I tools\carve_stream.py stream <name>_b | ssh builder 'cat > /var/tmp/vnniperf/img/<name>_b.img'`, with
sha256 computed on both ends (`evidence\carve_stream.log`, `evidence\builder_img_sha256.txt`). The images were deleted
after extraction to free builder space and can be re-made with the same command.

| partition | LP extent (metadata slot 1) | bytes | sha256 | fs | extracted |
|---|---|---:|---|---|---|
| system_b | sector 2048, 2,219,136 sectors | 1,136,197,632 | `15fc09de36d3cdb8…` | EROFS | 1.5 GiB |
| system_ext_b | sector 15,417,344 | 169,435,136 | `8ceacee60ce99137…` | EROFS | 297 MiB |
| vendor_b | sector 10,823,680 | 380,608,512 | `a1491aa4da413df9…` | EROFS | 715 MiB |
| product_b | sector 11,567,104 | 1,970,524,160 | `8955c4cf4696c7ae…` | EROFS | 3.2 GiB |
| APEX payloads | 46 `.apex`, 0 `.capex` (37 system, 2 system_ext, 6 vendor, 1 product) | | `evidence\builder_unpack_apex.log` | 44 EROFS + 2 ext4 | 851 MiB |

`fsck.erofs --extract` exited 0 for all four. Every APEX payload unpacked with rc 0.

### 2.2 Method

1. **Prefilter** (`tools\scan_vnni.py`, a strict superset): in every executable `PT_LOAD` of every ELF, and of every
   `.so` inside an APK/JAR/ZIP, find `C4 [RXB|00010] [W=0 vvvv L pp=01] [50-53]`. A 2-byte VEX (`C5`) cannot select
   map 0F38, so `C4` is the only possible prefix.
2. **Verification:** GNU objdump 2.42 `-d -w` linear sweep. A hit counts only if objdump decodes an instruction at
   exactly that address whose first byte is the `C4` and whose mnemonic is `vpdpbusd/vpdpbusds/vpdpwssd/vpdpwssds`.
   EVEX (`62`) forms are counted separately; they are native on Ice Lake.
3. **Symbols:** `.symtab`, `.dynsym`, and the xz-compressed MiniDebugInfo (`.gnu_debugdata`) symtab that Android keeps,
   so most platform functions have names.
4. **Cross-check:** the prebuilt llvm-objdump 22.0.2 found an identical address set for 16 libraries: libz ×2,
   libcrypto, libcrypto_httpengine, init, linker64, libicing_anywhere, libhwui, surfaceflinger, protobuf-lite ×3,
   audio_effects, cros_ml_core, libvpx and av1enc (`evidence\xcheck_llvm.txt`, `xcheck_notes.txt`). For the last three,
   the first line-regex pass under-counted the llvm output. A by-address recheck (`tools\xcheck2.py`: does llvm's
   text at each GNU-verified address contain `{vex}`?) found 0 GNU-only sites. The exception is the two copies of `libzlib_rs.dylib.so`, which llvm-objdump refuses ("invalid section index:
   31"). Their 2 sites were checked by hand: `c4 e2 4d 52 da` = C4, map 0F38, W0, L1, pp=66, opcode 52 =
   `{vex} vpdpwssd %ymm2,%ymm6,%ymm3`.
5. **Dispatch** (`tools\dispatch_vnni.py`): for each VNNI function it records every call/jmp/jcc/lea reference, the 14
   instructions before each call site with rip-relative flag reads, 8-byte data references (function-pointer tables),
   IFUNCs, cpuid/xgetbv sites (and whether one loads leaf 7 subleaf 1), and BMI2/AVX2 line counts
   (`evidence\dispatch_platform.json`, `dispatch_apk.json`, disassembly in `evidence\disasm.tar.xz`).

### 2.3 Totals (MEASURED)

* Inputs: 2,966 ELF files + 400 APK/JAR/ZIP files (359 ELF members) = **3,325 ELF objects**: 2,767 x86-64, 18 i386,
  505 aarch64 (`lib64/arm64`, berberis), 29 eBPF, 6 ARM.
* Prefilter candidates: 32,581. **Verified: 32,563 in 128 ELFs** (118 distinct sha256). The 18 unverified candidates
  are bytes inside other instructions or data, in 10 ELFs (Trichrome, WebView, icing, gs, …; list in `scan.json`).
* Forms: `vpdpbusd` ymm 17,601 / xmm 7; `vpdpbusds` ymm 160; `vpdpwssd` xmm 9,364 / ymm 5,431; **`vpdpwssds` 0**.
* Platform-built (system/system_ext/vendor/APEX): 104 ELFs, 16,909 instructions. Prebuilt APK libraries: 24 ELFs, 15,654.

By dispatch class (`tools\report_tables.py`, rules in its docstring; per-library numbers in Appendix A):

| class | meaning | instructions | libraries | traps on Ice Lake when run? |
|---|---|---:|---:|---|
| `ANON_XNN` | stripped prebuilt XNNPACK `__avxvnni` kernels (fingerprint + CPUID(7,1) reader) | 15,986 | 22 | no (INFERRED) |
| `OLDER_ISA` | SIMD function for an older ISA, picked at runtime by a cpuid table (libaom/libvpx rtcd, libgav1 `GetCpuInfo`, libyuv, libwebp, ruy/TFLite, XNNPACK avx2/avx256skx, webrtc) | 11,867 | 16 | **yes**: the gate tests SSE2…AVX2 |
| `COMPILE_TIME` | generic C/C++/Rust auto-vectorised under the platform `-march`, no CPU check found | 2,008 | 54 | **yes** |
| `AVXVNNI_OK` | named XNNPACK `__avxvnni` kernels (cpuinfo AVX-VNNI bit) | 1,478 | 2 | no |
| `PROTOBUF` | generated `ByteSizeLong` / `ExtensionSet::ByteSize`, virtual or unconditional | 442 | 30 | **yes** |
| `MLKEM` | BoringSSL ML-KEM / Kyber, called unconditionally (incl. self-tests) | 354 | 13 | **yes** |
| `UNRESOLVED` | stripped `libopenvino_intel_cpu_plugin.so`; CPUID(7,1) reader present | 160 | 1 | unknown (INFERRED gated) |
| `AVX512VNNI` | XNNPACK `__avx256vnni` kernels: gate = AVX512-VNNI(+VL), present on Ice Lake; compiler chose VEX | 137 | 1 | **yes** |
| `ZLIB_SSSE3` | Chromium-zlib `adler32_simd_` behind `x86_cpu_enable_ssse3` | 119 | 13 | **yes** |
| `RUST_ZLIBRS` | zlib-rs `adler32`, avx2 as a compile-time target feature (no check in the function) | 12 | 6 | **yes** |

### 2.4 Dispatch logic, family by family

**a) Chromium zlib `adler32_simd_`: `libz.so` (system + vendor) and 11 static copies.** MEASURED, `evidence\libz_gate.txt`:

```
adler32_z:  a729: cmp  $0x40,%rdx ; a72d: jae a7ce            # only len >= 64 takes the SIMD path
            a7ce: cmpl $0x0,0x1582b(%rip)   # 0x20000 = x86_cpu_enable_ssse3
            a7d5: je   a733                  # scalar (gbOS86 turns this je into nop; jmp)
            a7db: call 10fc0 <adler32_simd_> # 9 x {vex} vpdpwssd xmm
_cpu_check_features:                         # (excerpt; full listing in evidence\libz_gate.txt)
            11640: mov  $0x1,%eax ; 11645: xchg %rsi,%rbx ; 11648: cpuid ; 1164a: xchg %rsi,%rbx
            1164d: mov  %ecx,%eax ; 1164f: and $0x200,%eax ; 11654: mov %eax,0xe9a6(%rip)   # -> 0x20000: CPUID.1:ECX[9] SSSE3
            1165a: shr  $0x1a,%edx ; ... ; 11668: and $0x1,%ecx ; 1166b: mov %ecx,0xe993(%rip) # -> 0x20004: SSE2&SSE4.2&PCLMUL (crc32_z)
```

The same structure exists in each static copy: `cmpl $0` of the global that `_cpu_check_features` fills with
`ECX & 0x200`, verified per binary. Copies: `/system/bin/init`, `/system/bin/bootstrap/linker64`, apex:runtime
`bin/linker64` (symbols `__dl_x86_cpu_enable_ssse3` / `__dl_x86_cpu_enable_simd`), apex:appsearch
`libicing_anywhere.so`, `bpftool`, `snapuserd` (gate not resolved by the tool, same cpu-check code), `update_engine`,
`libsnapshot.so`, `libfdtrack.so`, vendor `libflashrom.so`, and one instruction in vendor `libcros_ml_core_internal.so`.
The scalar `adler32_z` contains `mulx` (BMI2) with no gate, which is MEASURED evidence that the whole library is built
for a modern `-march`.

**b) zlib-rs `adler32`** (`libzlib_rs.dylib.so` system + vendor; statically in `virtmgr`, `early_virtmgr`, `vm`,
`aisealhostservice`): 2 × `vpdpwssd ymm` in `zlib_rs::adler32::adler32`, with no cpu check inside or around it. The
AVX2 path is chosen at compile time. MEASURED.

**c) BoringSSL ML-KEM** (`libcrypto.so` in system, vendor and 10 APEX copies, plus `libcrypto_httpengine.so`): 18–28
`vpdpwssd ymm` in `mlkem::encrypt_cpa<3/4>`, `mlkem_decap_no_self_test<3/4>`, `vector_compress`, Kyber
`encrypt_cpa`/`decrypt_cpa`. They are called directly from `BCM_mlkem768_encap/decap`, `…_generate_key_fips`,
`…_check_fips`, `boringssl_self_test_mlkem`, `fips::encap_self_test` and `fips::ensure_decap_self_test`.
`OPENSSL_cpuid_setup` exists, but these paths are generic C with no dispatch. MEASURED. The native-tree 736-per-event
quantum fits a self-test (encap + decap) per process start (INFERRED).

**d) Protobuf** (30 libraries: `libprotobuf-cpp-lite/full` system + vendor + audio APEX, `statsd`, `surfaceflinger`
(perfetto protos), `libicing_anywhere`, `init`, `linker64`, `apexd`, `linkerconfig`, `service.incremental`, `aapt2`,
`pixelatoms-cpp`, `crash_dump64`, …): `vpdpwssd xmm` in generated `ByteSizeLong` (vectorised size sums over repeated
fields). These are virtual functions, reached through vtables (data references) or unconditional calls. MEASURED.

**e) Older-ISA SIMD selected at runtime.** The gate passes on Ice Lake. MEASURED: the function names and the cpuid
setup (`setup_rtcd_internal`, `libgav1::GetCpuInfo`, libyuv `MaskCpuFlags`/`CpuId`, `webrtc::GetCPUInfo`,
`EigenForTFLite::queryCacheSizes`, XNNPACK `init_*_gemm_config` testing `hardware_config` bits). Libraries:
* swcodec APEX `libcodec2_soft_av1enc.so` (libaom: `av1_fdct8x64_new_sse2` 324, `fdct16x64_new_avx2` 323,
  `av1_compute_stats_avx2` 282 …) — 8,130 total;
* `libvpx.so` 1,881; `libcodec2_soft_av1dec_gav1.so` 1,853 (`Dct64_SSE4_1`, `…_AVX2`); `libcodec2_soft_avcdec.so`;
  `libopus.so` (`xcorr_kernel_sse4_1`);
* `libhwui.so` (libwebp `SSE8x8_SSE2`, `FTransformPass1_SSE2`);
* `libbluetooth_jni.so`, `libinput.so`, `libtflite.so`, `libfcp_cpp_dep_jni.so`, extservices/adservices
  `libtflite_support_classifiers_native.so`, `libtextclassifier.so`, SystemUITitan `libtensorflowlite_jni.so`
  (ruy `Kernel8bitAvx2Impl`, tflite `SseMatrixBatchVectorMultiplyAccumulate`);
* vendor `libcros_ml_core_internal.so` (XNNPACK `__avx2`, `__avx2_madd`, `__avx256skx` = 653).

**f) XNNPACK `__avx256vnni` in `libcros_ml_core_internal.so`** (137): picked when the CPU reports AVX512-VNNI+VL, which
Ice Lake does. The kernels use 256-bit VNNI intrinsics, and with AVX-VNNI also enabled the compiler emitted the shorter
VEX encoding (MEASURED: every one is verified `C4`). So they trap on Ice Lake if XNNPACK picks them (INFERRED).

**g) Correctly gated XNNPACK `__avxvnni` kernels.** The named ones are in vendor `libcros_ml_core_internal.so` (763)
and audio-APEX `libdesktop_audio_effects_internal.so` (715, all of its VNNI). `init_qs8_qc8w_gemm_config` stores
`xnn_…__avxvnni_prfm` into the gemm config only under a `hardware_config` bit test. In the 22 stripped prebuilts (GMS/ML
apps, Photos, Trichrome/WebView, TTS, Bugle, Satin, ALAI, AiAi, vendor `libcamera_internal.so`), the VNNI is
`vpdpbusd ymm` only. 45–100 % of those instruction byte-strings occur in the named `__avxvnni` kernels; GoogleTTS,
DesktopRecorder ×2 and DeviceIntelligence are byte-identical, in order, to the audio-effects kernel set. All 22 contain
a CPUID leaf-7-subleaf-1 reader (MEASURED). INFERRED: cpuinfo reports no AVX-VNNI on Ice Lake, so these never run.

**h) Compile-time generic code** (54 libraries; e.g. `libaudioprocessing.so` `AudioMixerBase::TrackBase::track__16Bits*`
and `volumeStereo`, Skia blur and blend in `surfaceflinger`, libyuv `*_C` rows in `libhwui`/`libyuv`/camera HALs,
AMR/AAC/H.263/MPEG-4 codecs, `libRSCpuRef`, `libpdfium`, `gs`, `vulkan.intel.so` (2 sites), `crosvm` (3 sites)):
reached with no CPU check. Platform-wide `-march`, MEASURED: 102 of the 104 platform-built hit libraries contain BMI2
`shlx/sarx/shrx/rorx` (Appendix A). So do common libraries with **no** VNNI: bionic `libc.so` 420 BMI2 lines,
`libart.so` 7,363, `libbinder.so` 39, `libutils.so` 8, `libgui.so` 149, `libsqlite.so` 219 (counts include
`mulx/bzhi/pdep/pext`). Those libraries simply contain no madd+add pattern for the compiler to fuse.

**i) AVX-VNNI-INT8** (`vpdpbssd`): 72 instructions in each of 22 libraries. In the named ones they are
`xnn_qs8_qc8w_[i]gemm_minmax_fp32_ukernel_{1,5}x8c8__avxvnniint8_prfm`. k15 does not emulate them (pp ≠ 66), but they
are gated by cpuinfo (INFERRED never selected on Ice Lake).

---

## 3. Call sites of libz's adler32 (who drives the dominant load)

`tools\zlib_consumers.py` (→ `evidence\zlib_consumers.json`): 84 x86-64 ELFs touch the zlib API, and 77 of them
`DT_NEEDED libz.so`. `tools\plt_callers.py` (→ `evidence\plt_callers.json`) lists each call site with the
`windowBits` argument.

| path | caller (MEASURED) | reaches `adler32_z`? |
|---|---|---|
| DEX checksum | apex:art `libdexfile.so` `DexFile::CalculateChecksum`, `DexFile::ChecksumMemoryRange` → `adler32` | yes, over whole dex files |
| OAT/VDEX output | apex:art `dex2oat64` `OatWriter::ChecksumUpdatingOutputStream::WriteFully`, `OatWriter` ctor → `adler32` | yes, over every byte written |
| OAT/boot image | `libart.so` `OatHeader::ComputeChecksum`, `ImageHeader::WriteData`, `OatFileAssistant::AnonymousDexVdexLocation` (`adler32_combine`), CheckJNI `GuardedCopy` | yes |
| ART profiles | apex:art `libprofile.so` `ProfileCompilationInfo::SafeBuffer::Deflate` (`deflateInit_`), `ReadSectionData` (`inflateInit_`), i.e. the zlib wrapper | yes (deflate/inflate update adler32) |
| PNG | `libpng.so` `png_inflate_claim` / `png_deflate_claim` (system, vendor, mediaprovider) | yes for zlib-wrapped IDAT (INFERRED: unless the decoder disables the adler check) |
| java.util.zip | apex:art `libopenjdk.so` `Adler32_update*`, `Inflater_init`/`Deflater_init` (nowrap selects the wrapper) | yes when `nowrap=false` |
| HTTP | apex:tethering `libhttpengine.so` `GzipSourceStream` (`inflateInit_` = zlib "deflate" encoding), `quic::CertCompressor::DecompressChain` | yes for zlib streams |
| ZIP/APK | `libziparchive`, `libandroidfw` (`StreamingZipInflater`, `zip_archive::Inflate`), `libandroid_runtime`, `libartbase` (`extractToWriter`), `service.incremental` | **no**: `inflateInit2_(…, -15)`, raw deflate |
| gzip | toybox, libsparse, libvintf, libinput (`gz*`), protobuf `GzipInputStream` | no (CRC32) |
| other | `libunwindstack` (compressed debug sections), `libperfetto` (trace compression), `libRSCpuRef` (build checksum), `libft2` (gzip fonts) | rare |

Only buffers of **64 bytes or more** take the SIMD path (`cmp $0x40,%rdx; jae`).

---

## 4. Ranking by likely runtime weight

| rank | library → function | static VEX VNNI | dispatch | runtime evidence | likely weight |
|---:|---|---:|---|---|---|
| 1 | `/system/lib64/libz.so` → `adler32_simd_` (via `adler32_z`, len ≥ 64) | 9 | `ZLIB_SSSE3` | **MEASURED**: 5 LRU clusters in 5 comms. The only VEX.128-word RIPs ever captured are `adler32_simd_` copies, and 100 % of the 2.48e8 non-KAT emulations are VEX word forms (99.998 % VEX.128). gbOS86 measured libz adler32 = 50 % of all traps on a CPU that also trapped SHA-NI; Ice Lake has SHA-NI | **dominant, ≈ all of the 2.5e8 per boot** (INFERRED share ≥ 99.9 %) |
| 2 | apex:appsearch `libicing_anywhere.so` → static `adler32_simd_` (+ 56 protobuf) | 66 | `ZLIB_SSSE3` + `PROTOBUF` | MEASURED: `pool-265-thread`, 58 in one snapshot | low |
| 3 | `/system/bin/init` → `adler32_simd_` / protobuf `SubcontextReply_ExpandArgsReply::ByteSizeLong` | 14 | `ZLIB_SSSE3` + `PROTOBUF` | MEASURED: `comm=init` T1+T115 at 24.1 s (VM tree); exactly 16 VEX.128 in the native tree, constant from t40 | negligible |
| 4 | BoringSSL ML-KEM: apex:tethering `libcrypto_httpengine.so` (+12 `libcrypto.so` copies) | 18–28 each | `MLKEM` | **MEASURED**: native tree 174 × 736 by 900 s in `main`; VM tree non-KAT VEX.256 is 736 at 198.7 s, 2,816 at 274.4 s and 5,216 at 442 s (the ML-KEM RIPs are the only VEX.256 RIPs captured) | negligible (~10³ per process start / handshake) |
| 5 | other static zlib copies: `linker64` ×2, `update_engine`, `libsnapshot`, `snapuserd`, `bpftool`, `libfdtrack`, vendor `libflashrom` | 10 each | `ZLIB_SSSE3` | not captured | low; OTA/snapshot merge (`update_engine`, `snapuserd`) could be heavy during an update (INFERRED) |
| 6 | vendor `libcros_ml_core_internal.so` (XNNPACK avx2/avx256skx/avx256vnni + TFLite SSE) | 1,767 (≈1,000 reachable on Ice Lake) | `OLDER_ISA` + `AVX512VNNI` (+ `AVXVNNI_OK`) | not captured; byte counter 0 non-KAT, so no `vpdpbusd` ran in the boot window | **potentially high while on-device ML runs** (INFERRED) |
| 7 | swcodec APEX software codecs (`libcodec2_soft_av1enc` 8,130, `libvpx` 1,881, `…_av1dec_gav1` 1,853, avc/opus/amr/aac …), `libstagefright`, `libcodec2_soft_common`, `libsfplugin_ccodec_utils` | ≈ 12,000 | `OLDER_ISA` + `COMPILE_TIME` | not captured | **high only while a software codec runs** (INFERRED; on the laptop HW decode goes through `c2-cros-codecs-service`, 6 generic sites) |
| 8 | `libaudioprocessing.so` (`AudioMixerBase` 16-bit track/volume paths), `libbluetooth_jni` (SBC/PLC), webrtc APM, BT audio session | 80 / 66 / 2–6 / 13 | `COMPILE_TIME` (+ `OLDER_ISA`) | not captured | moderate during audio playback / BT audio if those mixer paths are the ones used (INFERRED) |
| 9 | image/UI: `libhwui` (libyuv `ScaleRowDown34_*_C`, libwebp `*_SSE2`, crabbyavif), `surfaceflinger` (Skia CPU blur and blend, perfetto protos), `libpdfium`/`libpdfclient`, `gs` | 46 / 28 / 19 / 39 | `COMPILE_TIME` + `OLDER_ISA` | not captured; the VEX.256 budget in the VM tree is ≤ 5,216 | low–moderate (INFERRED) |
| 10 | protobuf `ByteSizeLong` users (30 libraries) | 442 | `PROTOBUF` | not captured | low (INFERRED) |
| 11 | TFLite/ruy in apex:extservices/adservices/ondevicepersonalization, SystemUITitan, vendor `libtflite` | 39–145 | `OLDER_ISA` | not captured | low–moderate when those models run (INFERRED) |
| — | XNNPACK `__avxvnni` (2 named + 22 stripped libraries, 17,464 instructions), `__avxvnniint8` (22 × 72), OpenVINO (160) | — | correct CPUID(7,1) gate (OpenVINO: INFERRED) | — | none expected on Ice Lake |

How the ranking was made: rank 1–4 rests on counter evidence (RIPs or exact quanta). Rank 5–11 is ordered by how
often the code path runs on a desktop (call-site analysis, §3) times the number of trapping sites in its hot loops.
None of 5–11 appears in any captured LRU or kmsg RIP, and their VEX.256/byte forms are bounded by the counters above.

---

## 5. gbOS86 cross-reference (ideas only, no code copied)

* gbOS86 `HANDOFF.md:64-65`: "libz.so adler32 (vpdpwssd, 50%) … the je at adler32_z+0xb5 (0xa7d5) gates the SIMD
  path". Same offsets in our build: `je` at file/vaddr `0xa7d5` in both `libz.so`.
* gbOS86 `usb/patched/MANIFEST` (build 16658739): system `371f70d6a9392327cb36838d0cbc7af7388d1689c25d91cd343af8e58433683b`,
  vendor `e45aecb65cbb40f0c3070b05aa9d974076d401ed6f2aff3081af26a14e812590`. These are **identical** to the carved
  `/system/lib64/libz.so` and `/vendor/lib64/libz.so`. A sha256-gated bind of a patched libz (the pipeline already binds
  from `/odm`) would therefore match this build.
* Their other two items (Conscrypt/system libcrypto SHA-256, 40 % + 10 %) are SHA-NI traps. Ice Lake has SHA-NI, so on
  this hardware libz is ~100 % of the VNNI load instead of 50 %.
* Naming: the flag is `x86_cpu_enable_ssse3` (see §2.4a), not `x86_cpu_enable_simd`.
* INFERRED effect of a libz-only fix: removes the dominant source (rank 1). The static copies (rank 2, 3, 5) keep the same
  gate pattern and would need their own per-binary handling if they ever matter. The counter can confirm this after the
  change.

---

## 6. Caveats and open items

* The LRU holds 16 entries, so it cannot apportion the 2.48e8 total exactly. The ≥ 99.9 % libz share is INFERRED from
  (a) every captured non-KAT VEX.128 RIP being `adler32_simd_` and (b) 100 % word forms with 0 byte forms. A per-library
  histogram (e.g. counting per RIP page offset or per VMA) would make it MEASURED. That is a kernel change, out of
  scope here.
* The per-trap cost was not measured. CPU time = count × per-trap cost, and the latter must be measured on the laptop.
* The system and vendor `libz.so` cannot be told apart by RIP (same layout). Process context says system (INFERRED).
* Not scanned: `/data` (APEX updates installed from Play into `/data/apex`, user-installed apps), odm, system_dlkm and
  vendor_dlkm (kernel modules), and JIT code. A mainline update of the ART, AppSearch or tethering APEX in `/data`
  could replace the scanned copies.
* **Pipeline note (not fixed, shared file):** `C:\GB-x86\carve_lp.py` takes the *first* LP metadata slot that lists a
  name with extents. For `system_b`, metadata slot 0 lists a stale 4,199 MiB extent at sector 2,222,080, while the
  current slot-b metadata (slot 1, which init uses) has 1,083 MiB at sector 2,048 (`evidence\carve_info.txt`). So
  `carve_lp.py <dir> system_b` would carve the wrong bytes. `vendor_dlkm_b` and the other names are unaffected because
  their slot-0 entries are empty. `tools\carve_stream.py` selects the metadata slot by slot suffix.

---

## 7. Reproduce

```
# Windows (read-only on lapis.bin)
python -I tools\carve_stream.py info
python -I tools\carve_stream.py stream system_b | ssh -i C:/GB-build/builder_key builder@192.168.115.132 "nice -n 19 ionice -c3 sh -c 'cat > /var/tmp/vnniperf/img/system_b.img'"
#   (same for system_ext_b, vendor_b, product_b)
# builder, in /var/tmp/vnniperf
fsck.erofs --extract=fs/system_b --no-preserve img/system_b.img      # x4
sh scan/unpack_apex.sh /var/tmp/vnniperf
JOBS=2 nice -n 19 ionice -c3 python3 scan/scan_vnni.py scan/full fs/system_b fs/system_ext_b fs/vendor_b fs/product_b apex
EXCLUDE='apk!' nice -n 19 python3 scan/dispatch_vnni.py scan/full/scan.json scan/disp_platform
nice -n 19 python3 scan/dispatch_vnni.py scan/full/scan.json scan/disp_apk 'apk!'
python3 scan/zlib_consumers.py scan/zlib_consumers.json fs/system_b fs/system_ext_b fs/vendor_b fs/product_b apex
python3 scan/plt_callers.py scan/plt_callers.json adler32,adler32_z,...,inflateInit2_ <ELFs>
python3 scan/xcheck_llvm.py <llvm-objdump> scan/full/scan.json <substrings>
# Windows, in C:\GB-build\native\vnni-perf
python -I tools\extract_gbvnni.py C:\GB-build\native\dgpu\w3\evidence\build evidence\gbvnni_records.json
python -I tools\match_rips.py evidence\scan.json evidence\gbvnni_records.json evidence\rip_matches.json
python -I tools\report_tables.py evidence
python -I tools\summaries_md.py evidence > evidence\summaries.md
```

Files: `tools\` (carve_stream.py, scan_vnni.py, dispatch_vnni.py, zlib_consumers.py, plt_callers.py, xcheck_llvm.py,
xcheck2.py, unpack_apex.sh, extract_gbvnni.py, match_rips.py, report_tables.py, summaries_md.py) and `evidence\`
(scan.json, dispatch_platform.json, dispatch_apk.json, rip_matches.json, gbvnni_records.json, lru_clusters.md,
summaries.md, libs_table.md, libs.csv, libz_gate.txt, plt_callers.json, zlib_consumers.json, xcheck_llvm.txt,
xcheck_notes.txt, carve_info.txt, carve_stream.log, builder_*.log, builder_img_sha256.txt, disasm.tar.xz).
Builder scratch kept: `/var/tmp/vnniperf/{fs,apex,scan}` (≈ 7.3 GiB; images deleted).

---

## Appendix A: every library with verified VEX AVX-VNNI (generated by `tools\report_tables.py`)

Columns: `forms` = opcode.width:count. `dispatch classes` = per-instruction class counts (§2.3). `cpuid in lib` =
cpuid sites found (", leaf7.1" when one loads EAX=7/ECX=1 just before). `BMI2 lines` = shlx/sarx/shrx/rorx count in the
whole library. `seen at runtime` = comms whose LRU RIPs matched this layout. `/vendor/lib64/libz.so` has the same
layout as the system copy, so it repeats the system copy's comms; the processes are INFERRED to use the system copy.


### A.1 Table

| # | library | sha256[:16] | VEX VNNI | funcs | forms | dispatch classes (insns) | top functions | cpuid in lib | BMI2 lines | seen at runtime (comm) |
|---|---|---|---:|---:|---|---|---|---|---:|---|
| 1 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_av1enc.so` | `8e9bcbb98b4808f0` | 8130 | 460 | vpdpwssd.xmm:4931 vpdpwssd.ymm:3199 | OLDER_ISA:7893 COMPILE_TIME:237 | av1\_fdct8x64\_new\_sse2(324) ; fdct16x64\_new\_avx2(323) ; av1\_compute\_stats\_avx2(282) | 9 site(s) | 6557 |  |
| 2 | `apex:com.google.android.media.swcodec/lib64/libvpx.so` | `6b05090b6c519cbc` | 1881 | 67 | vpdpwssd.xmm:1497 vpdpwssd.ymm:384 | OLDER_ISA:1378 COMPILE_TIME:503 | idct32\_1024\_8x32(131) ; idct32\_1024\_16x32(131) ; vpx\_fdct32x32\_sse2(127) | 12 site(s) | 2373 |  |
| 3 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_av1dec_gav1.so` | `d3569416d542048c` | 1853 | 65 | vpdpwssd.xmm:1540 vpdpwssd.ymm:313 | OLDER_ISA:1676 COMPILE_TIME:177 | \_ZN7libgav13dsp12low\_bitdepth12\_GLOBAL\_\_N\_112Dct64\_SSE4\_1EPvib(195) ; \_ZN7libgav13dsp12low\_bitdepth12\_GLOBAL\_\_N\_129Adst16TransformLoopRow\_SS(133) ; \_ZN7libgav13dsp12low\_bitdepth12\_GLOBAL\_\_N\_128Dct32TransformLoopRow\_SSE(132) | 9 site(s), leaf7.1 | 1512 |  |
| 4 | `/vendor/lib64/libcros_ml_core_internal.so` | `a400cbaac9571b83` | 1767 | 127 | vpdpbusd.ymm:900 vpdpwssd.xmm:260 vpdpwssd.ymm:607 | AVXVNNI_OK:763 OLDER_ISA:653 COMPILE_TIME:189 AVX512VNNI:137 PROTOBUF:24 ZLIB_SSSE3:1 | xnn\_qd8\_f16\_qc2w\_gemm\_minmax\_ukernel\_5x8c8\_\_avxvnni(80) ; xnn\_qd8\_f32\_qc2w\_gemm\_minmax\_ukernel\_5x8c8\_\_avxvnni(80) ; xnn\_qs8\_qc2w\_gemm\_minmax\_fp32\_ukernel\_5x8c8\_\_avxvnni(80) | 55 site(s), leaf7.1 | 4970 |  |
| 5 | `/product/priv-app/ALAIPrebuiltRelease/ALAIPrebuiltRelease.apk!lib/x86_64/libmlkit_google_ocr_pipeline.so` | `54a82a1c9b465b86` | 811 | 1 | vpdpbusd.ymm:811 | ANON_XNN:811 | (no symbol)(811) | 45 site(s), leaf7.1 | 6 |  |
| 6 | `/product/priv-app/ALAIPrebuiltRelease/ALAIPrebuiltRelease.apk!lib/x86_64/libtartarus_core.so` | `75b0cd066c932cd5` | 811 | 1 | vpdpbusd.ymm:811 | ANON_XNN:811 | (no symbol)(811) | 44 site(s), leaf7.1 | 6 |  |
| 7 | `/product/priv-app/ContextualCursorPrebuilt/ContextualCursorPrebuilt.apk!lib/x86_64/libLiteRt.so` | `f020e7d4255e88a9` | 811 | 1 | vpdpbusd.ymm:811 | ANON_XNN:811 | (no symbol)(811) | 26 site(s), leaf7.1 | 6 |  |
| 8 | `/product/priv-app/ContextualCursorPrebuilt/ContextualCursorPrebuilt.apk!lib/x86_64/libmediapipe_tasks_jni.so` | `5189bf211f24e374` | 811 | 1 | vpdpbusd.ymm:811 | ANON_XNN:811 | (no symbol)(811) | 36 site(s), leaf7.1 | 6 |  |
| 9 | `/product/priv-app/ContextualCursorPrebuilt/ContextualCursorPrebuilt.apk!lib/x86_64/libocr_grouping_jni_lib.so` | `64c16bac8b8721b1` | 811 | 1 | vpdpbusd.ymm:811 | ANON_XNN:811 | (no symbol)(811) | 44 site(s), leaf7.1 | 6 |  |
| 10 | `/product/priv-app/LatinIMEGoogleDesktopPrebuilt/LatinIMEGoogleDesktopPrebuilt.apk!lib/x86_64/libintegrated_shared_object.so` | `2fd2333ffead2942` | 811 | 1 | vpdpbusd.ymm:811 | ANON_XNN:811 | (no symbol)(811) | 54 site(s), leaf7.1 | 587 |  |
| 11 | `/product/priv-app/Satin/Satin.apk!lib/x86_64/liblens_ondevice_engine_play_ml.so` | `75958190ea626c19` | 811 | 1 | vpdpbusd.ymm:811 | ANON_XNN:811 | (no symbol)(811) | 60 site(s), leaf7.1 | 116 |  |
| 12 | `/product/priv-app/Satin/Satin.apk!lib/x86_64/libnga_jni.so` | `575f816a9a6cc010` | 811 | 1 | vpdpbusd.ymm:811 | ANON_XNN:811 | (no symbol)(811) | 42 site(s), leaf7.1 | 807 |  |
| 13 | `/product/priv-app/DevicePersonalizationAiAiPrebuiltPixel2025/DevicePersonalizationAiAiPrebuiltPixel2025.apk!lib/x86_64/libaiai_native.so` | `24b938f0a8ff018f` | 803 | 1 | vpdpbusd.ymm:803 | ANON_XNN:803 | (no symbol)(803) | 57 site(s), leaf7.1 | 907 |  |
| 14 | `/product/priv-app/DevicePersonalizationAiAiPrebuiltPixel2025/DevicePersonalizationAiAiPrebuiltPixel2025.apk!lib/x86_64/libtensorflowlite_jni_stable.so` | `ed3e9c9308835d42` | 803 | 1 | vpdpbusd.ymm:803 | ANON_XNN:803 | (no symbol)(803) | 32 site(s), leaf7.1 | 6 |  |
| 15 | `/product/app/GoogleTTS/GoogleTTS.apk!lib/x86_64/libgoogle_speech_sbg_tts_jni.so` | `61524ccee3b1407f` | 715 | 1 | vpdpbusd.ymm:715 | ANON_XNN:715 | (no symbol)(715) | 57 site(s), leaf7.1 | 587 |  |
| 16 | `/product/app/WallpaperAIPrebuilt_10000652/WallpaperAIPrebuilt_10000652.apk!lib/x86_64/liblancet_jni.so` | `210a18e59432395e` | 715 | 1 | vpdpbusd.ymm:715 | ANON_XNN:715 | (no symbol)(715) | 53 site(s), leaf7.1 | 6 |  |
| 17 | `/product/priv-app/DesktopRecorderPrebuilt_953092853/DesktopRecorderPrebuilt_953092853.apk!lib/x86_64/libdeepclu_jni.so` | `5d95706afea466c9` | 715 | 1 | vpdpbusd.ymm:715 | ANON_XNN:715 | (no symbol)(715) | 25 site(s), leaf7.1 | 6 |  |
| 18 | `/product/priv-app/DesktopRecorderPrebuilt_953092853/DesktopRecorderPrebuilt_953092853.apk!lib/x86_64/libtensorflowlite_jni.so` | `a4a096ba7d15f7f9` | 715 | 1 | vpdpbusd.ymm:715 | ANON_XNN:715 | (no symbol)(715) | 25 site(s), leaf7.1 | 6 |  |
| 19 | `/product/priv-app/DeviceIntelligenceNetworkPrebuiltAstrea/DeviceIntelligenceNetworkPrebuiltAstrea.apk!lib/x86_64/libpcs_tensorflow_jni.so` | `2d4779d70ab5bccd` | 715 | 1 | vpdpbusd.ymm:715 | ANON_XNN:715 | (no symbol)(715) | 51 site(s), leaf7.1 | 326 |  |
| 20 | `/product/priv-app/PrebuiltBugle/PrebuiltBugle.apk!lib/x86_64/libgoogle3.so` | `6da0d4bbce65b8ce` | 715 | 1 | vpdpbusd.ymm:715 | ANON_XNN:715 | (no symbol)(715) | 50 site(s), leaf7.1 | 326 |  |
| 21 | `/product/priv-app/PrebuiltBugle/PrebuiltBugle.apk!lib/x86_64/libmediapipe_tasks_vision_jni.so` | `b4d249c420fc55cd` | 715 | 1 | vpdpbusd.ymm:715 | ANON_XNN:715 | (no symbol)(715) | 42 site(s), leaf7.1 | 6 |  |
| 22 | `apex:com.android.hardware.audio.desktop/lib64/libdesktop_audio_effects_internal.so` | `5ff4723b1ff8014f` | 715 | 28 | vpdpbusd.ymm:715 | AVXVNNI_OK:715 | xnn\_qd8\_f16\_qc2w\_gemm\_minmax\_ukernel\_5x8c8\_\_avxvnni(80) ; xnn\_qd8\_f32\_qc2w\_gemm\_minmax\_ukernel\_5x8c8\_\_avxvnni(80) ; xnn\_qs8\_qc2w\_gemm\_minmax\_fp32\_ukernel\_5x8c8\_\_avxvnni(80) | 29 site(s), leaf7.1 | 326 |  |
| 23 | `/product/app/Photos/Photos.apk!lib/x86_64/libnative.so` | `e23f52a61f0f924e` | 699 | 1 | vpdpbusd.ymm:699 | ANON_XNN:699 | (no symbol)(699) | 93 site(s), leaf7.1 | 898 |  |
| 24 | `/product/app/TrichromeLibrary/TrichromeLibrary.apk!lib/x86_64/libmonochrome_64.so` | `19b1df399cf8cf16` | 675 | 1 | vpdpbusd.ymm:675 | ANON_XNN:675 | (no symbol)(675) | 135 site(s), leaf7.1 | 1401 |  |
| 25 | `/product/app/WebViewGoogle/WebViewGoogle.apk!lib/x86_64/libwebviewchromium.so` | `40c3e37249de2a2a` | 675 | 1 | vpdpbusd.ymm:675 | ANON_XNN:675 | (no symbol)(675) | 136 site(s), leaf7.1 | 1426 |  |
| 26 | `/product/priv-app/ScribePrebuilt_v8.7.880674799/ScribePrebuilt_v8.7.880674799.apk!lib/x86_64/libtensorflowlite_jni.so` | `3b781b09e06bd495` | 419 | 1 | vpdpbusd.ymm:419 | ANON_XNN:419 | (no symbol)(419) | 27 site(s), leaf7.1 | 6 |  |
| 27 | `/vendor/lib64/libcamera_internal.so` | `56cbc0b408e56cdb` | 419 | 1 | vpdpbusd.ymm:419 | ANON_XNN:419 | (no symbol)(419) | 39 site(s), leaf7.1 | 6 |  |
| 28 | `/vendor/lib64/libopenvino_intel_cpu_plugin.so` | `db006976f4839e1f` | 160 | 1 | vpdpbusds.ymm:160 | UNRESOLVED:160 | (no symbol)(160) | 43 site(s), leaf7.1 | 0 |  |
| 29 | `apex:com.google.android.ondevicepersonalization/lib64/libfcp_cpp_dep_jni.so` | `2d568e2fb9741fb2` | 145 | 35 | vpdpwssd.xmm:126 vpdpwssd.ymm:19 | COMPILE_TIME:82 PROTOBUF:36 OLDER_ISA:27 | \_ZN6tflite16cpu\_backend\_gemm6detail14CustomGemvImplIaaiaLNS0\_18Quantiz(13) ; \_ZN6tflite16cpu\_backend\_gemm6detail14CustomGemvImplIaaiaLNS0\_18Quantiz(10) ; \_ZN6tflite16cpu\_backend\_gemm6detail14CustomGemvImplIhhihLNS0\_18Quantiz(10) | 28 site(s), leaf7.1 | 6379 |  |
| 30 | `/system/lib64/libaudioprocessing.so` | `6e2f4278fe9f0a7a` | 80 | 19 | vpdpwssd.xmm:67 vpdpwssd.ymm:13 | COMPILE_TIME:80 | \_ZN7android14AudioMixerBase9TrackBase17track\_\_16BitsMonoEPimS2\_S2\_(12) ; \_ZN7android14AudioMixerBase9TrackBase19track\_\_16BitsStereoEPimS2\_S2\_(11) ; \_ZN7android14AudioMixerBase9TrackBase12volumeStereoEPimS2\_S2\_(9) | 0 site(s) | 836 |  |
| 31 | `apex:com.google.android.appsearch/lib64/libicing_anywhere.so` | `7daac53fd83f048f` | 66 | 15 | vpdpwssd.xmm:66 | PROTOBUF:56 ZLIB_SSSE3:10 | \_ZNK5icing3lib20SetSchemaResultProto12ByteSizeLongEv(14) ; adler32\_simd\_(10) ; \_ZNK5icing3lib15SearchSpecProto12ByteSizeLongEv(8) | 1 site(s) | 606 | pool-265-thread |
| 32 | `apex:com.google.android.bt/lib64/libbluetooth_jni.so` | `73d3da261fcc6a5a` | 66 | 18 | vpdpwssd.xmm:51 vpdpwssd.ymm:15 | COMPILE_TIME:49 OLDER_ISA:17 | \_Z12CLpc\_ConcealPA16\_sPsS1\_i(8) ; xcorr\_kernel\_sse4\_1(8) ; SbcAnalysisFilter8(6) | 0 site(s) | 8650 |  |
| 33 | `/vendor/lib64/libtflite.so` | `b96b760657c29bc2` | 49 | 14 | vpdpwssd.xmm:24 vpdpwssd.ymm:25 | OLDER_ISA:31 COMPILE_TIME:18 | \_ZN8gemmlowp20PackingRegisterBlockINS\_7SideMapIKhLNS\_12SideMapOrderE0E(9) ; \_ZN3ruy18Kernel8bitAvx2ImplILNS\_4PathE32EEEvRKNS\_16KernelParams8bitILi(8) ; \_ZN6tflite3ops7builtin15fully\_connected13EvalQuantizedILNS2\_10KernelTy(6) | 13 site(s) | 1757 |  |
| 34 | `/system/lib64/libhwui.so` | `89bc7e7aab59792b` | 46 | 12 | vpdpwssd.xmm:21 vpdpwssd.ymm:25 | COMPILE_TIME:34 OLDER_ISA:12 | ScaleRowDown34\_0\_Box\_C(13) ; ScaleRowDown34\_1\_Box\_C(8) ; \_ZL11SSE8x8\_SSE2PKhS0\_.\_\_uniq.129003089420588146622684099794510073405.(7) | 17 site(s), leaf7.1 | 1901 |  |
| 35 | `apex:com.google.android.extservices/priv-app/GoogleExtServices-sminus@CL3B.260622.276.R4/GoogleExtServices-sminus.apk!lib/x86_64/libtextclassifier.so` | `5700df1714ed828c` | 44 | 13 | vpdpwssd.xmm:21 vpdpwssd.ymm:23 | OLDER_ISA:27 COMPILE_TIME:17 | \_ZN8gemmlowp20PackingRegisterBlockINS\_7SideMapIKhLNS\_12SideMapOrderE0E(9) ; \_ZN3ruy18Kernel8bitAvx2ImplILNS\_4PathE32EEEvRKNS\_16KernelParams8bitILi(8) ; \_ZN6tflite12tensor\_utils44SseSparseMatrixBatchVectorMultiplyAccumulate(6) | 14 site(s) | 1390 |  |
| 36 | `apex:com.google.android.adservices/lib64/libtflite_support_classifiers_native.so` | `2a2f74f1cc6e0f96` | 41 | 13 | vpdpwssd.xmm:22 vpdpwssd.ymm:19 | OLDER_ISA:27 COMPILE_TIME:12 PROTOBUF:2 | \_ZN8gemmlowp20PackingRegisterBlockINS\_7SideMapIKhLNS\_12SideMapOrderE0E(9) ; \_ZN3ruy18Kernel8bitAvx2ImplILNS\_4PathE32EEEvRKNS\_16KernelParams8bitILi(8) ; \_ZN6tflite12tensor\_utils44SseSparseMatrixBatchVectorMultiplyAccumulate(6) | 13 site(s) | 1301 |  |
| 37 | `apex:com.google.android.extservices/lib64/libtflite_support_classifiers_native.so` | `671bc664d9c20b55` | 41 | 13 | vpdpwssd.xmm:22 vpdpwssd.ymm:19 | OLDER_ISA:27 COMPILE_TIME:12 PROTOBUF:2 | \_ZN8gemmlowp20PackingRegisterBlockINS\_7SideMapIKhLNS\_12SideMapOrderE0E(9) ; \_ZN3ruy18Kernel8bitAvx2ImplILNS\_4PathE32EEEvRKNS\_16KernelParams8bitILi(8) ; \_ZN6tflite12tensor\_utils44SseSparseMatrixBatchVectorMultiplyAccumulate(6) | 13 site(s) | 1301 |  |
| 38 | `/system/bin/cups/filter/gs` | `2d80c3109eb66b74` | 39 | 9 | vpdpbusd.xmm:3 vpdpwssd.xmm:9 vpdpwssd.ymm:27 | COMPILE_TIME:39 | art\_blend\_pixel\_8\_inline(8) ; art\_pdf\_composite\_pixel\_alpha\_8(5) ; art\_pdf\_composite\_knockout\_8(5) | 0 site(s) | 2574 |  |
| 39 | `/system_ext/priv-app/SystemUITitan/SystemUITitan.apk!lib/x86_64/libtensorflowlite_jni.so` | `a99a8e1fa89f7a3c` | 39 | 12 | vpdpwssd.xmm:20 vpdpwssd.ymm:19 | OLDER_ISA:27 COMPILE_TIME:12 | \_ZN8gemmlowp20PackingRegisterBlockINS\_7SideMapIKhLNS\_12SideMapOrderE0E(9) ; \_ZN3ruy18Kernel8bitAvx2ImplILNS\_4PathE32EEEvRKNS\_16KernelParams8bitILi(8) ; \_ZN6tflite12tensor\_utils44SseSparseMatrixBatchVectorMultiplyAccumulate(6) | 13 site(s) | 1136 |  |
| 40 | `/system/lib64/libprotobuf-cpp-full-6.33.5-absl20260526.so` | `90185efece824e8b` | 38 | 7 | vpdpwssd.xmm:38 | PROTOBUF:38 | \_ZNK6google8protobuf8internal12ExtensionSet9Extension8ByteSizeEi(24) ; \_ZNK6google8protobuf19FileDescriptorProto12ByteSizeLongEv(4) ; \_ZNK6google8protobuf15DescriptorProto12ByteSizeLongEv(2) | 3 site(s) | 700 |  |
| 41 | `/vendor/lib64/libprotobuf-cpp-full-6.33.5-absl20260526.so` | `3cabee900b013422` | 38 | 7 | vpdpwssd.xmm:38 | PROTOBUF:38 | \_ZNK6google8protobuf8internal12ExtensionSet9Extension8ByteSizeEi(24) ; \_ZNK6google8protobuf19FileDescriptorProto12ByteSizeLongEv(4) ; \_ZNK6google8protobuf15DescriptorProto12ByteSizeLongEv(2) | 3 site(s) | 700 |  |
| 42 | `/system/lib64/libinput.so` | `9d181166ba4e8769` | 33 | 9 | vpdpwssd.xmm:16 vpdpwssd.ymm:17 | OLDER_ISA:23 COMPILE_TIME:10 | \_ZN8gemmlowp20PackingRegisterBlockINS\_7SideMapIKhLNS\_12SideMapOrderE0E(9) ; \_ZN3ruy18Kernel8bitAvx2ImplILNS\_4PathE32EEEvRKNS\_16KernelParams8bitILi(8) ; \_ZN6tflite12tensor\_utils44SseSparseMatrixBatchVectorMultiplyAccumulate(6) | 7 site(s) | 864 |  |
| 43 | `/vendor/lib64/libcodec2_soft_common.so` | `85de0acb495075e8` | 33 | 9 | vpdpwssd.xmm:10 vpdpwssd.ymm:23 | COMPILE_TIME:33 | ScaleRowDown34\_0\_Box\_C.cfi(13) ; ScaleRowDown34\_1\_Box\_C.cfi(8) ; I210AlphaToARGBRow\_C.cfi(2) | 7 site(s), leaf7.1 | 186 |  |
| 44 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_common.so` | `ad4df2cf3dddb0c6` | 33 | 9 | vpdpwssd.xmm:10 vpdpwssd.ymm:23 | COMPILE_TIME:33 | ScaleRowDown34\_0\_Box\_C.cfi(13) ; ScaleRowDown34\_1\_Box\_C.cfi(8) ; I210AlphaToARGBRow\_C.cfi(2) | 7 site(s), leaf7.1 | 186 |  |
| 45 | `/system/lib64/libsfplugin_ccodec_utils.so` | `df070e4b65a662ed` | 32 | 8 | vpdpwssd.xmm:9 vpdpwssd.ymm:23 | COMPILE_TIME:32 | ScaleRowDown34\_0\_Box\_C.cfi(13) ; ScaleRowDown34\_1\_Box\_C.cfi(8) ; I210AlphaToARGBRow\_C.cfi(2) | 7 site(s), leaf7.1 | 153 |  |
| 46 | `/system/lib64/libyuv.so` | `4aaf64f79ace51d3` | 32 | 8 | vpdpwssd.xmm:9 vpdpwssd.ymm:23 | COMPILE_TIME:32 | ScaleRowDown34\_0\_Box\_C(13) ; ScaleRowDown34\_1\_Box\_C(8) ; I210AlphaToARGBRow\_C(2) | 7 site(s), leaf7.1 | 108 |  |
| 47 | `/vendor/lib64/hw/camera.libcamera_hal_base.so` | `f58831e02f413630` | 32 | 8 | vpdpwssd.xmm:9 vpdpwssd.ymm:23 | COMPILE_TIME:32 | ScaleRowDown34\_0\_Box\_C(13) ; ScaleRowDown34\_1\_Box\_C(8) ; I210AlphaToARGBRow\_C(2) | 7 site(s), leaf7.1 | 131 |  |
| 48 | `/vendor/lib64/libcamera/pipeline/camera.libipu7_hal_fatcat.so` | `8c9a58e2f10a1d38` | 32 | 8 | vpdpwssd.xmm:9 vpdpwssd.ymm:23 | COMPILE_TIME:32 | ScaleRowDown34\_0\_Box\_C(13) ; ScaleRowDown34\_1\_Box\_C(8) ; I210AlphaToARGBRow\_C(2) | 7 site(s), leaf7.1 | 184 |  |
| 49 | `/vendor/lib64/libsfplugin_ccodec_utils.so` | `6c95af9dab3b69d9` | 32 | 8 | vpdpwssd.xmm:9 vpdpwssd.ymm:23 | COMPILE_TIME:32 | ScaleRowDown34\_0\_Box\_C.cfi(13) ; ScaleRowDown34\_1\_Box\_C.cfi(8) ; I210AlphaToARGBRow\_C.cfi(2) | 7 site(s), leaf7.1 | 153 |  |
| 50 | `/vendor/lib64/libyuv.so` | `4aaf64f79ace51d3` | 32 | 8 | vpdpwssd.xmm:9 vpdpwssd.ymm:23 | COMPILE_TIME:32 | ScaleRowDown34\_0\_Box\_C(13) ; ScaleRowDown34\_1\_Box\_C(8) ; I210AlphaToARGBRow\_C(2) | 7 site(s), leaf7.1 | 108 |  |
| 51 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_avcdec.so` | `207794be65efb0ca` | 32 | 3 | vpdpwssd.xmm:24 vpdpwssd.ymm:8 | OLDER_ISA:24 COMPILE_TIME:8 | ih264\_inter\_pred\_luma\_horz\_hpel\_vert\_hpel\_ssse3(24) ; ih264\_weighted\_pred\_luma(4) ; ih264\_weighted\_pred\_chroma(4) | 0 site(s) | 1039 |  |
| 52 | `apex:com.google.android.media.swcodec/lib64/libsfplugin_ccodec_utils.so` | `df070e4b65a662ed` | 32 | 8 | vpdpwssd.xmm:9 vpdpwssd.ymm:23 | COMPILE_TIME:32 | ScaleRowDown34\_0\_Box\_C.cfi(13) ; ScaleRowDown34\_1\_Box\_C.cfi(8) ; I210AlphaToARGBRow\_C.cfi(2) | 7 site(s), leaf7.1 | 153 |  |
| 53 | `apex:com.google.android.media.swcodec/lib64/libopus.so` | `17f6b0a3fa913367` | 31 | 10 | vpdpwssd.xmm:25 vpdpwssd.ymm:6 | OLDER_ISA:22 COMPILE_TIME:9 | xcorr\_kernel\_sse4\_1(8) ; celt\_inner\_prod\_sse4\_1(6) ; renormalise\_vector(5) | 0 site(s) | 736 |  |
| 54 | `/system/bin/surfaceflinger` | `d9a8bd0b71da4d13` | 28 | 10 | vpdpwssd.xmm:8 vpdpwssd.ymm:20 | COMPILE_TIME:22 PROTOBUF:6 | \_ZN3$\_38\_\_invokeEPhhih.\_\_uniq.89848800506738526971539220142122416183(5) ; \_Z6mergeTIhEvPKviPKhiPv(5) ; \_ZL21clamp\_solid\_with\_origIN6SkMask9AlphaIterILNS0\_6FormatE1EEEEvPhiT\_(4) | 0 site(s) | 791 |  |
| 55 | `/system/lib64/libcrypto.so` | `0ee96c7e0cb1ffca` | 28 | 7 | vpdpwssd.ymm:28 | MLKEM:28 | \_ZN5mlkem12\_GLOBAL\_\_N\_111encrypt\_cpaILi3EEEvPhPKNS0\_10public\_keyIXT\_EE(8) ; \_ZL15vector\_compressP6vectori.\_\_uniq.248849528654135170066675156412194(6) ; \_ZN5mlkem12\_GLOBAL\_\_N\_124mlkem\_decap\_no\_self\_testILi3EEEvPhPKhPKNS0\_11(4) | 3 site(s) | 1067 |  |
| 56 | `/vendor/lib64/libcrypto.so` | `70cc371baf9925d5` | 28 | 7 | vpdpwssd.ymm:28 | MLKEM:28 | \_ZN5mlkem12\_GLOBAL\_\_N\_111encrypt\_cpaILi3EEEvPhPKNS0\_10public\_keyIXT\_EE(8) ; \_ZL15vector\_compressP6vectori.\_\_uniq.248849528654135170066675156412194(6) ; \_ZN5mlkem12\_GLOBAL\_\_N\_124mlkem\_decap\_no\_self\_testILi3EEEvPhPKhPKNS0\_11(4) | 3 site(s) | 1067 |  |
| 57 | `apex:com.android.hardware.drm.clearkey/lib64/libcrypto.so` | `cfb27e3d77b46475` | 28 | 7 | vpdpwssd.ymm:28 | MLKEM:28 | \_ZN5mlkem12\_GLOBAL\_\_N\_111encrypt\_cpaILi3EEEvPhPKNS0\_10public\_keyIXT\_EE(8) ; \_ZL15vector\_compressP6vectori.\_\_uniq.248849528654135170066675156412194(6) ; \_ZN5mlkem12\_GLOBAL\_\_N\_124mlkem\_decap\_no\_self\_testILi3EEEvPhPKhPKNS0\_11(4) | 3 site(s) | 1067 |  |
| 58 | `apex:com.android.hardware.fingerprint.crosfp/lib64/libcrypto.so` | `cfb27e3d77b46475` | 28 | 7 | vpdpwssd.ymm:28 | MLKEM:28 | \_ZN5mlkem12\_GLOBAL\_\_N\_111encrypt\_cpaILi3EEEvPhPKNS0\_10public\_keyIXT\_EE(8) ; \_ZL15vector\_compressP6vectori.\_\_uniq.248849528654135170066675156412194(6) ; \_ZN5mlkem12\_GLOBAL\_\_N\_124mlkem\_decap\_no\_self\_testILi3EEEvPhPKhPKNS0\_11(4) | 3 site(s) | 1067 |  |
| 59 | `apex:com.android.hardware.keymint.strongbox.desktop/lib64/libcrypto.so` | `cfb27e3d77b46475` | 28 | 7 | vpdpwssd.ymm:28 | MLKEM:28 | \_ZN5mlkem12\_GLOBAL\_\_N\_111encrypt\_cpaILi3EEEvPhPKNS0\_10public\_keyIXT\_EE(8) ; \_ZL15vector\_compressP6vectori.\_\_uniq.248849528654135170066675156412194(6) ; \_ZN5mlkem12\_GLOBAL\_\_N\_124mlkem\_decap\_no\_self\_testILi3EEEvPhPKhPKNS0\_11(4) | 3 site(s) | 1067 |  |
| 60 | `apex:com.google.android.adbd/lib64/libcrypto.so` | `dc4f73623434eaaa` | 28 | 7 | vpdpwssd.ymm:28 | MLKEM:28 | \_ZN5mlkem12\_GLOBAL\_\_N\_111encrypt\_cpaILi3EEEvPhPKNS0\_10public\_keyIXT\_EE(8) ; \_ZL15vector\_compressP6vectori.\_\_uniq.248849528654135170066675156412194(6) ; \_ZN5mlkem12\_GLOBAL\_\_N\_124mlkem\_decap\_no\_self\_testILi3EEEvPhPKhPKNS0\_11(4) | 3 site(s) | 1067 |  |
| 61 | `apex:com.google.android.bt/lib64/libcrypto.so` | `dc4f73623434eaaa` | 28 | 7 | vpdpwssd.ymm:28 | MLKEM:28 | \_ZN5mlkem12\_GLOBAL\_\_N\_111encrypt\_cpaILi3EEEvPhPKNS0\_10public\_keyIXT\_EE(8) ; \_ZL15vector\_compressP6vectori.\_\_uniq.248849528654135170066675156412194(6) ; \_ZN5mlkem12\_GLOBAL\_\_N\_124mlkem\_decap\_no\_self\_testILi3EEEvPhPKhPKNS0\_11(4) | 3 site(s) | 1067 |  |
| 62 | `apex:com.google.android.configinfrastructure/lib64/libcrypto.so` | `dc4f73623434eaaa` | 28 | 7 | vpdpwssd.ymm:28 | MLKEM:28 | \_ZN5mlkem12\_GLOBAL\_\_N\_111encrypt\_cpaILi3EEEvPhPKNS0\_10public\_keyIXT\_EE(8) ; \_ZL15vector\_compressP6vectori.\_\_uniq.248849528654135170066675156412194(6) ; \_ZN5mlkem12\_GLOBAL\_\_N\_124mlkem\_decap\_no\_self\_testILi3EEEvPhPKhPKNS0\_11(4) | 3 site(s) | 1067 |  |
| 63 | `apex:com.google.android.conscrypt/lib64/libcrypto.so` | `dc4f73623434eaaa` | 28 | 7 | vpdpwssd.ymm:28 | MLKEM:28 | \_ZN5mlkem12\_GLOBAL\_\_N\_111encrypt\_cpaILi3EEEvPhPKNS0\_10public\_keyIXT\_EE(8) ; \_ZL15vector\_compressP6vectori.\_\_uniq.248849528654135170066675156412194(6) ; \_ZN5mlkem12\_GLOBAL\_\_N\_124mlkem\_decap\_no\_self\_testILi3EEEvPhPKhPKNS0\_11(4) | 3 site(s) | 1067 |  |
| 64 | `apex:com.google.android.ondevicepersonalization/lib64/libcrypto.so` | `dc4f73623434eaaa` | 28 | 7 | vpdpwssd.ymm:28 | MLKEM:28 | \_ZN5mlkem12\_GLOBAL\_\_N\_111encrypt\_cpaILi3EEEvPhPKNS0\_10public\_keyIXT\_EE(8) ; \_ZL15vector\_compressP6vectori.\_\_uniq.248849528654135170066675156412194(6) ; \_ZN5mlkem12\_GLOBAL\_\_N\_124mlkem\_decap\_no\_self\_testILi3EEEvPhPKhPKNS0\_11(4) | 3 site(s) | 1067 |  |
| 65 | `apex:com.google.android.tethering/lib64/libcrypto.so` | `dc4f73623434eaaa` | 28 | 7 | vpdpwssd.ymm:28 | MLKEM:28 | \_ZN5mlkem12\_GLOBAL\_\_N\_111encrypt\_cpaILi3EEEvPhPKNS0\_10public\_keyIXT\_EE(8) ; \_ZL15vector\_compressP6vectori.\_\_uniq.248849528654135170066675156412194(6) ; \_ZN5mlkem12\_GLOBAL\_\_N\_124mlkem\_decap\_no\_self\_testILi3EEEvPhPKhPKNS0\_11(4) | 3 site(s) | 1067 |  |
| 66 | `apex:com.google.android.wifi/lib64/libcrypto.so` | `dc4f73623434eaaa` | 28 | 7 | vpdpwssd.ymm:28 | MLKEM:28 | \_ZN5mlkem12\_GLOBAL\_\_N\_111encrypt\_cpaILi3EEEvPhPKNS0\_10public\_keyIXT\_EE(8) ; \_ZL15vector\_compressP6vectori.\_\_uniq.248849528654135170066675156412194(6) ; \_ZN5mlkem12\_GLOBAL\_\_N\_124mlkem\_decap\_no\_self\_testILi3EEEvPhPKhPKNS0\_11(4) | 3 site(s) | 1067 |  |
| 67 | `apex:com.google.android.os.statsd/bin/statsd` | `0782ad6a87fef76f` | 26 | 2 | vpdpwssd.xmm:26 | PROTOBUF:26 | \_ZNK6google8protobuf8internal12ExtensionSet9Extension8ByteSizeEi(24) ; \_ZNK10zetasketch7android50KllQuantilesStateProto\_Compactor\_NonPackable(2) | 0 site(s) | 478 |  |
| 68 | `/system/lib64/libRSCpuRef.so` | `1a108dfe44e6d350` | 25 | 3 | vpdpwssd.xmm:25 | COMPILE_TIME:25 | \_Z28rsdIntrinsicColorMatrix4x4\_KPvPKvPKsj(12) ; \_ZN8gemmlowp20PackingRegisterBlockINS\_7SideMapIKhLNS\_12SideMapOrderE0E(9) ; \_ZN7android12renderscript26RsdCpuScriptIntrinsic3DLUT6kernelEPK24RsExp(4) | 0 site(s) | 24 |  |
| 69 | `/system/lib64/libprotobuf-cpp-lite-6.33.5-absl20260526.so` | `7af0cef8e9a3a18c` | 24 | 1 | vpdpwssd.xmm:24 | PROTOBUF:24 | \_ZNK6google8protobuf8internal12ExtensionSet9Extension8ByteSizeEi(24) | 3 site(s) | 447 |  |
| 70 | `/system/lib64/service.incremental.so` | `9c47f32f9f083b62` | 24 | 1 | vpdpwssd.xmm:24 | PROTOBUF:24 | \_ZNK6google8protobuf8internal12ExtensionSet9Extension8ByteSizeEi(24) | 3 site(s) | 446 |  |
| 71 | `/vendor/lib64/libprotobuf-cpp-lite-6.33.5-absl20260526.so` | `c833564ed5248450` | 24 | 1 | vpdpwssd.xmm:24 | PROTOBUF:24 | \_ZNK6google8protobuf8internal12ExtensionSet9Extension8ByteSizeEi(24) | 3 site(s) | 447 |  |
| 72 | `apex:com.android.hardware.audio.desktop/lib64/libprotobuf-cpp-lite-6.33.5-absl20260526.so` | `d57fc1fe8289302e` | 24 | 1 | vpdpwssd.xmm:24 | PROTOBUF:24 | \_ZNK6google8protobuf8internal12ExtensionSet9Extension8ByteSizeEi(24) | 3 site(s) | 447 |  |
| 73 | `apex:com.google.android.webapp/bin/aapt2` | `4e2a695215728431` | 22 | 9 | vpdpwssd.xmm:22 | PROTOBUF:22 | \_ZNK4aapt2pb14UsesPermission12ByteSizeLongEv(4) ; \_ZNK4aapt2pb13Architectures12ByteSizeLongEv(4) ; \_ZNK4aapt2pb7Badging12ByteSizeLongEv(2) | 0 site(s) | 817 |  |
| 74 | `/system/lib64/libpdfium.so` | `535bcef6fb899dee` | 19 | 5 | vpdpwssd.ymm:19 | COMPILE_TIME:19 | \_ZN12CFX\_DIBitmap13CompositeRectEiiiij(5) ; \_ZN6pdfium19CFX\_AggDeviceDriver11SetClipMaskERNS\_3agg22rasterizer\_scan(5) ; \_ZN22CFX\_ScanlineCompositor4InitE12FXDIB\_FormatS0\_N6pdfium4spanIKjLm18(4) | 0 site(s) | 1654 |  |
| 75 | `apex:com.google.android.mediaprovider/lib64/libpdfclient.so` | `938a1053ceed5679` | 19 | 5 | vpdpwssd.ymm:19 | COMPILE_TIME:19 | \_ZN12CFX\_DIBitmap13CompositeRectEiiiij(5) ; \_ZN6pdfium19CFX\_AggDeviceDriver11SetClipMaskERNS\_3agg22rasterizer\_scan(5) ; \_ZN22CFX\_ScanlineCompositor4InitE12FXDIB\_FormatS0\_N6pdfium4spanIKjLm18(4) | 0 site(s) | 1661 |  |
| 76 | `apex:com.google.android.tethering/lib64/libcrypto_httpengine.so` | `223db1af99ed2835` | 18 | 4 | vpdpwssd.ymm:18 | MLKEM:18 | \_ZN5mlkem12\_GLOBAL\_\_N\_111encrypt\_cpaILi3EEEvPhPKNS0\_10public\_keyIXT\_EE(8) ; \_ZN5mlkem12\_GLOBAL\_\_N\_124mlkem\_decap\_no\_self\_testILi3EEEvPhPKhPKNS0\_11(4) ; \_ZN5mlkem12\_GLOBAL\_\_N\_124mlkem\_decap\_no\_self\_testILi4EEEvPhPKhPKNS0\_11(4) | 3 site(s) | 956 | main |
| 77 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_apvenc.so` | `65fa93ec96db9ba4` | 17 | 2 | vpdpwssd.xmm:14 vpdpwssd.ymm:3 | COMPILE_TIME:14 OLDER_ISA:3 | ssd\_16b\_sse\_8x8(14) ; ssd\_16b\_avx\_8x8(3) | 3 site(s) | 283 |  |
| 78 | `/system/bin/apexd` | `5c99572a92258396` | 16 | 3 | vpdpwssd.xmm:16 | PROTOBUF:16 | \_ZNK4apex5proto12ApexManifest12ByteSizeLongEv(8) ; \_ZNK4apex5proto12SessionState12ByteSizeLongEv(6) ; \_ZNK4apex5proto19ApexStorageMetadata12ByteSizeLongEv(2) | 0 site(s) | 25 |  |
| 79 | `/system/bin/bootstrap/linker64` | `8d0ef11d34fbfbb1` | 16 | 3 | vpdpwssd.xmm:16 | ZLIB_SSSE3:10 PROTOBUF:6 | \_\_dl\_adler32\_simd\_(10) ; \_\_dl\_\_ZNK6Thread12ByteSizeLongEv(4) ; \_\_dl\_\_ZNK9Tombstone12ByteSizeLongEv(2) | 1 site(s) | 1105 |  |
| 80 | `apex:com.android.runtime/bin/linker64` | `72a818e0c73483b9` | 16 | 3 | vpdpwssd.xmm:16 | ZLIB_SSSE3:10 PROTOBUF:6 | \_\_dl\_adler32\_simd\_(10) ; \_\_dl\_\_ZNK6Thread12ByteSizeLongEv(4) ; \_\_dl\_\_ZNK9Tombstone12ByteSizeLongEv(2) | 1 site(s) | 1105 |  |
| 81 | `apex:com.android.runtime/bin/linkerconfig` | `6c3c8b6616276988` | 16 | 3 | vpdpwssd.xmm:16 | PROTOBUF:16 | \_ZNK4apex5proto12ApexManifest12ByteSizeLongEv(8) ; \_ZNK7android12linkerconfig5proto12LinkerConfig12ByteSizeLongEv(6) ; \_ZNK7android12linkerconfig5proto25LinkerConfig\_Contribution12ByteSizeL(2) | 0 site(s) | 141 |  |
| 82 | `/system/bin/init` | `9c6d33419ac64c79` | 14 | 3 | vpdpwssd.xmm:14 | ZLIB_SSSE3:10 PROTOBUF:4 | adler32\_simd\_(10) ; \_ZNK31SubcontextReply\_ExpandArgsReply12ByteSizeLongEv(2) ; \_ZNK22chromeos\_update\_engine21DynamicPartitionGroup12ByteSizeLongEv(2) | 3 site(s) | 2705 |  |
| 83 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_av1dec_dav1d.so` | `fa4ff8129e43051d` | 14 | 8 | vpdpbusd.xmm:4 vpdpwssd.xmm:6 vpdpwssd.ymm:4 | COMPILE_TIME:14 | ipred\_smooth\_v\_c(5) ; warp\_affine\_8x8\_c(2) ; warp\_affine\_8x8t\_c(2) | 0 site(s) | 1436 |  |
| 84 | `/vendor/lib64/libbluetooth_audio_session_aidl.so` | `237e55fc966bb4c5` | 13 | 4 | vpdpwssd.xmm:12 vpdpwssd.ymm:1 | COMPILE_TIME:13 | SbcAnalysisFilter8(6) ; SbcAnalysisFilter4(4) ; resample\_x192k\_12k8(2) | 0 site(s) | 1025 |  |
| 85 | `apex:com.android.hardware.audio.desktop/lib64/libbluetooth_audio_session_aidl.so` | `8c96f9dffebb197a` | 13 | 4 | vpdpwssd.xmm:12 vpdpwssd.ymm:1 | COMPILE_TIME:13 | SbcAnalysisFilter8(6) ; SbcAnalysisFilter4(4) ; resample\_x192k\_12k8(2) | 0 site(s) | 1025 |  |
| 86 | `/system/bin/update_engine` | `cc9962207240038b` | 12 | 2 | vpdpwssd.xmm:12 | ZLIB_SSSE3:10 PROTOBUF:2 | adler32\_simd\_(10) ; \_ZNK22chromeos\_update\_engine21DynamicPartitionGroup12ByteSizeLongEv(2) | 3 site(s) | 2689 |  |
| 87 | `/system/lib64/libsnapshot.so` | `244b2b1e2924f85c` | 12 | 2 | vpdpwssd.xmm:12 | ZLIB_SSSE3:10 PROTOBUF:2 | adler32\_simd\_(10) ; \_ZNK22chromeos\_update\_engine21DynamicPartitionGroup12ByteSizeLongEv(2) | 13 site(s) | 2832 |  |
| 88 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_amrwbenc.so` | `881fed95b98311ee` | 11 | 3 | vpdpwssd.xmm:1 vpdpwssd.ymm:10 | COMPILE_TIME:11 | coder(8) ; voAWB\_Pred\_lt4(2) ; voAWB\_Syn\_filt\_32(1) | 0 site(s) | 254 |  |
| 89 | `/system/bin/bpftool` | `0ce148b1dc0e1c5d` | 10 | 1 | vpdpwssd.xmm:10 | ZLIB_SSSE3:10 | adler32\_simd\_(10) | 1 site(s) | 737 |  |
| 90 | `/system/bin/snapuserd` | `2f9621c0a8f409d1` | 10 | 1 | vpdpwssd.xmm:10 | ZLIB_SSSE3:10 | adler32\_simd\_(10) | 3 site(s) | 966 |  |
| 91 | `/system/lib64/libfdtrack.so` | `85c152dabedbb093` | 10 | 1 | vpdpwssd.xmm:10 | ZLIB_SSSE3:10 | adler32\_simd\_(10) | 1 site(s) | 739 |  |
| 92 | `/vendor/lib64/libflashrom.so` | `31dde3e14e64e6a4` | 10 | 1 | vpdpwssd.xmm:10 | ZLIB_SSSE3:10 | adler32\_simd\_(10) | 1 site(s) | 226 |  |
| 93 | `/vendor/lib64/pixelatoms-cpp.so` | `67842488d245d323` | 10 | 4 | vpdpwssd.xmm:10 | PROTOBUF:10 | \_ZNK6google8protobuf19FileDescriptorProto12ByteSizeLongEv(4) ; \_ZNK6google8protobuf15DescriptorProto12ByteSizeLongEv(2) ; \_ZNK6google8protobuf19EnumDescriptorProto12ByteSizeLongEv(2) | 0 site(s) | 0 |  |
| 94 | `/system/lib64/libstagefright.so` | `1f8b4f5427c9241e` | 9 | 5 | vpdpwssd.xmm:2 vpdpwssd.ymm:7 | COMPILE_TIME:9 | I210AlphaToARGBRow\_C.cfi(2) ; I410AlphaToARGBRow\_C.cfi(2) ; I422ToRGB565Row\_C.cfi(2) | 6 site(s), leaf7.1 | 3764 |  |
| 95 | `/system/lib64/libz.so` | `371f70d6a9392327` | 9 | 1 | vpdpwssd.xmm:9 | ZLIB_SSSE3:9 | adler32\_simd\_(9) | 1 site(s) | 156 | DefaultDispatch,LazyTaskWriterT,Profile Saver,dex2oat,launcher-loader |
| 96 | `/vendor/lib64/libz.so` | `e45aecb65cbb40f0` | 9 | 1 | vpdpwssd.xmm:9 | ZLIB_SSSE3:9 | adler32\_simd\_(9) | 1 site(s) | 156 | DefaultDispatch,LazyTaskWriterT,Profile Saver,dex2oat,launcher-loader |
| 97 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_amrnbdec.so` | `2eda489c09a4568d` | 9 | 2 | vpdpwssd.xmm:1 vpdpwssd.ymm:8 | COMPILE_TIME:9 | pvDecoder\_AmrWb(8) ; low\_pass\_filt\_7k(1) | 0 site(s) | 192 |  |
| 98 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_amrwbdec.so` | `c1bf2b1a1239624c` | 9 | 2 | vpdpwssd.xmm:1 vpdpwssd.ymm:8 | COMPILE_TIME:9 | pvDecoder\_AmrWb(8) ; low\_pass\_filt\_7k(1) | 0 site(s) | 192 |  |
| 99 | `apex:com.google.android.media.swcodec/lib64/libFraunhoferAAC.so` | `eee2c312c4690e0b` | 8 | 1 | vpdpwssd.ymm:8 | COMPILE_TIME:8 | \_Z12CLpc\_ConcealPA16\_sPsS1\_i.cfi(8) | 0 site(s) | 4368 |  |
| 100 | `/system/lib64/librtp_jni.so` | `9028ed7d4095dcb0` | 7 | 5 | vpdpwssd.xmm:5 vpdpwssd.ymm:2 | COMPILE_TIME:7 | Gsm\_Long\_Term\_Predictor(2) ; G\_pitch(2) ; Autocorr(1) | 0 site(s) | 298 |  |
| 101 | `/system/bin/pbtombstone` | `1c951da193aaa7e7` | 6 | 2 | vpdpwssd.xmm:6 | PROTOBUF:6 | \_ZNK6Thread12ByteSizeLongEv(4) ; \_ZNK9Tombstone12ByteSizeLongEv(2) | 0 site(s) | 172 |  |
| 102 | `/system/bin/simpleperf` | `ed04d59a2167d861` | 6 | 2 | vpdpwssd.xmm:6 | PROTOBUF:6 | \_ZNK23simpleperf\_report\_proto4File12ByteSizeLongEv(4) ; \_ZNK23simpleperf\_report\_proto8MetaInfo12ByteSizeLongEv(2) | 2 site(s) | 2411 |  |
| 103 | `/vendor/bin/hw/android.hardware.media.c2-cros-codecs-service` | `d6f145236b7cb156` | 6 | 1 | vpdpwssd.ymm:6 | COMPILE_TIME:6 | \_RNvNtCs1RAkYfvpS5r\_11cros\_codecs16image\_processing12ab30\_to\_p010(6) | 0 site(s) | 997 |  |
| 104 | `apex:com.android.hardware.audio.desktop/lib64/libwebrtc_apm_rust.dylib.so` | `12181b4839c6f158` | 6 | 2 | vpdpwssd.xmm:6 | PROTOBUF:6 | \_ZNK6webrtc9audioproc6Stream12ByteSizeLongEv(4) ; \_ZNK6webrtc9audioproc13ReverseStream12ByteSizeLongEv(2) | 21 site(s) | 308 |  |
| 105 | `apex:com.android.runtime/bin/crash_dump64` | `5912f7f31f4e07b6` | 6 | 2 | vpdpwssd.xmm:6 | PROTOBUF:6 | \_ZNK6Thread12ByteSizeLongEv(4) ; \_ZNK9Tombstone12ByteSizeLongEv(2) | 0 site(s) | 201 |  |
| 106 | `/system/lib64/libFFTEm.so` | `e0b1895fe5bcd1da` | 5 | 4 | vpdpwssd.xmm:5 | COMPILE_TIME:5 | bts\_CompactMat\_fltDotPrdRow(2) ; bbs\_dotProduct\_stdc(1) ; bbs\_dotProductInt16(1) | 0 site(s) | 675 |  |
| 107 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_amrnbenc.so` | `7b033a757e768a37` | 5 | 4 | vpdpwssd.xmm:5 | COMPILE_TIME:5 | G\_pitch(2) ; Autocorr(1) ; \_ZL7Lag\_maxP9vadState1PiPssssssS2\_iS1\_(1) | 0 site(s) | 220 |  |
| 108 | `/system/lib64/libndk_translation.so` | `ebdd6c0df05adefe` | 4 | 4 | vpdpwssd.xmm:4 | COMPILE_TIME:4 | \_ZN8berberis10intrinsics32VectorMultiplyAddByScalarLongIntIsLi4ELNS0\_3(1) ; \_ZN8berberis10intrinsics24VectorMultiplyAddLongIntIsLi4ELNS0\_33Preferr(1) ; \_ZNK8berberis11Interpreter32VectorMultiplyAddByScalarLongIntENS\_15SIMD(1) | 5 site(s) | 2094 |  |
| 109 | `apex:com.google.android.extservices/priv-app/GoogleExtServices-sminus@CL3B.260622.276.R4/GoogleExtServices-sminus.apk!lib/x86_64/libhpke_jni.so` | `71f97cd11f038aec` | 4 | 2 | vpdpwssd.xmm:4 | PROTOBUF:4 | \_ZNK24private\_join\_and\_compute5proto12BigNumVector12ByteSizeLongEv(2) ; \_ZNK24private\_join\_and\_compute25anonymous\_counting\_tokens27GeneratedTo(2) | 3 site(s) | 246 |  |
| 110 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_hevcenc.so` | `eb39273f432b549e` | 3 | 1 | vpdpwssd.ymm:3 | COMPILE_TIME:3 | ihevc\_intra\_pred\_ref\_filtering(3) | 0 site(s) | 1107 |  |
| 111 | `apex:com.google.android.virt/bin/crosvm` | `2b642953cc6bf985` | 3 | 1 | vpdpwssd.xmm:3 | COMPILE_TIME:3 | \_Z20rgtc\_decode\_subblockIhEvPKjPT\_iS2\_S2\_(3) | 13 site(s) | 3824 |  |
| 112 | `/system/bin/aisealhostservice` | `4a9f43a12c3f5852` | 2 | 1 | vpdpwssd.ymm:2 | RUST_ZLIBRS:2 | \_RNvNtCsg0aNEAVSCJ7\_7zlib\_rs7adler327adler32(2) | 0 site(s) | 435 |  |
| 113 | `/system/lib64/libkll.so` | `92a7985dcde6a548` | 2 | 1 | vpdpwssd.xmm:2 | PROTOBUF:2 | \_ZNK10zetasketch7android50KllQuantilesStateProto\_Compactor\_NonPackable(2) | 0 site(s) | 10 |  |
| 114 | `/system/lib64/libzlib_rs.dylib.so` | `75ad70ac0c1f5869` | 2 | 1 | vpdpwssd.ymm:2 | RUST_ZLIBRS:2 | \_RNvNtCs7Vu1lncgE74\_7zlib\_rs7adler327adler32(2) | 0 site(s) | 186 |  |
| 115 | `/vendor/lib64/hw/vulkan.intel.so` | `5c687450523ea9ed` | 2 | 2 | vpdpwssd.xmm:1 vpdpwssd.ymm:1 | COMPILE_TIME:2 | vk\_common\_CmdEndRenderPass2(1) ; util\_format\_r16g16b16a16\_snorm\_unpack\_rgba\_8unorm(1) | 17 site(s) | 5487 |  |
| 116 | `/vendor/lib64/libzlib_rs.dylib.so` | `393269d2b8fced25` | 2 | 1 | vpdpwssd.ymm:2 | RUST_ZLIBRS:2 | \_RNvNtCs7Vu1lncgE74\_7zlib\_rs7adler327adler32(2) | 0 site(s) | 186 |  |
| 117 | `/vendor/lib64/soundfx/libaudiopreprocessing.so` | `7518e04d058d8368` | 2 | 1 | vpdpwssd.xmm:2 | COMPILE_TIME:2 | WebRtcSpl\_ScaleAndAddVectorsWithRoundC.cfi(2) | 3 site(s) | 560 |  |
| 118 | `/vendor/lib64/soundfx/libpreprocessingaidl.so` | `495ea9810c4d31b6` | 2 | 1 | vpdpwssd.xmm:2 | COMPILE_TIME:2 | WebRtcSpl\_ScaleAndAddVectorsWithRoundC.cfi(2) | 3 site(s) | 1096 |  |
| 119 | `apex:com.google.android.adbd/bin/adbd` | `2abd69b7176ef88e` | 2 | 1 | vpdpwssd.xmm:2 | PROTOBUF:2 | \_ZNK3adb5proto12ProcessEntry12ByteSizeLongEv(2) | 2 site(s) | 3767 |  |
| 120 | `apex:com.google.android.adbd/lib64/libadbconnection_client.so` | `0ba859d42a68d00c` | 2 | 1 | vpdpwssd.xmm:2 | PROTOBUF:2 | \_ZNK3adb5proto12ProcessEntry12ByteSizeLongEv(2) | 0 site(s) | 134 |  |
| 121 | `apex:com.google.android.neuralnetworks/lib64/libneuralnetworks.so` | `e4c2850d27cb3f58` | 2 | 1 | vpdpwssd.xmm:2 | COMPILE_TIME:2 | \_ZN7android2nn17QuantizedLSTMCell4evalEv(2) | 7 site(s) | 375 |  |
| 122 | `apex:com.google.android.virt/bin/early_virtmgr` | `428a56875e184fc9` | 2 | 1 | vpdpwssd.ymm:2 | RUST_ZLIBRS:2 | \_RNvNtCsg0aNEAVSCJ7\_7zlib\_rs7adler327adler32(2) | 0 site(s) | 1490 |  |
| 123 | `apex:com.google.android.virt/bin/virtmgr` | `557ef96c0f8e77fd` | 2 | 1 | vpdpwssd.ymm:2 | RUST_ZLIBRS:2 | \_RNvNtCsg0aNEAVSCJ7\_7zlib\_rs7adler327adler32(2) | 0 site(s) | 1398 |  |
| 124 | `apex:com.google.android.virt/bin/vm` | `dcae3a1b5c7caf88` | 2 | 1 | vpdpwssd.ymm:2 | RUST_ZLIBRS:2 | \_RNvNtCsg0aNEAVSCJ7\_7zlib\_rs7adler327adler32(2) | 0 site(s) | 331 |  |
| 125 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_h263dec.so` | `4f44b4849954bacf` | 1 | 1 | vpdpwssd.xmm:1 | COMPILE_TIME:1 | idctrow2\_intra.cfi(1) | 0 site(s) | 138 |  |
| 126 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_h263enc.so` | `d60b8f53617e7918` | 1 | 1 | vpdpwssd.xmm:1 | COMPILE_TIME:1 | idct\_row2Intra.cfi(1) | 0 site(s) | 149 |  |
| 127 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_mpeg4dec.so` | `58295ec61afbc806` | 1 | 1 | vpdpwssd.xmm:1 | COMPILE_TIME:1 | idctrow2\_intra.cfi(1) | 0 site(s) | 138 |  |
| 128 | `apex:com.google.android.media.swcodec/lib64/libcodec2_soft_mpeg4enc.so` | `aac32cf1777cf971` | 1 | 1 | vpdpwssd.xmm:1 | COMPILE_TIME:1 | idct\_row2Intra.cfi(1) | 0 site(s) | 149 |  |

## Appendix B: every gb_vnni snapshot with each LRU RIP mapped (generated by `tools\summaries_md.py`)

Duplicate snapshots (same totals and LRU) are listed once. A totals-only line is omitted when the same totals appear with an LRU block. `vm1/vm1_eval_w3.txt` is the vm1 t40 snapshot.

### `vm1/t900_summary_block.txt` (snapshot)

`emulated 128080 vex128 16 vex256 128064 word 128080 byte 0 irqon 128080 efault 0 sse_init 0 ymm_init 0 zmmhi 0`

| comm | rip | count | library (layout match) | vaddr | function | instruction |
|---|---|---:|---|---|---|---|
| main | `730230b0c684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `730230b0c67f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `730230b0bf84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `730230b0bf7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `730230b0be64` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e64` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `730230b0be5f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e5f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `730230b0bd44` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d44` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `730230b0bd3f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d3f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `730230ab8c50` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c50` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `730230ab8c4b` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c4b` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `730230ab8b95` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b95` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `730230ab8b90` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b90` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `7394bd795684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7394bd79567f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7394bd794f84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7394bd794f7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |

### `vm1/vm1_eval_w3.txt` (snapshot)

`emulated 2224 vex128 16 vex256 2208 word 2224 byte 0 irqon 2224 efault 0 sse_init 0 ymm_init 0 zmmhi 0`

| comm | rip | count | library (layout match) | vaddr | function | instruction |
|---|---|---:|---|---|---|---|
| main | `785900992684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `78590099267f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `785900991f84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `785900991f7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `785900991e64` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e64` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `785900991e5f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e5f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `785900991d44` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d44` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `785900991d3f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d3f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `78590093ec50` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c50` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `78590093ec4b` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c4b` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `78590093eb95` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b95` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `78590093eb90` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b90` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `763031928684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `76303192867f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `763031927f84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `763031927f7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |

### `vm1/gblog/boot1-t180.txt` (snapshot)

`emulated 22096 vex128 16 vex256 22080 word 22096 byte 0 irqon 22096 efault 0 sse_init 0 ymm_init 0 zmmhi 0`

| comm | rip | count | library (layout match) | vaddr | function | instruction |
|---|---|---:|---|---|---|---|
| main | `7904e0144684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7904e014467f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7904e0143f84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7904e0143f7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7904e0143e64` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e64` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7904e0143e5f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e5f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7904e0143d44` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d44` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7904e0143d3f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d3f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7904e00f0c50` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c50` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `7904e00f0c4b` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c4b` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `7904e00f0b95` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b95` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `7904e00f0b90` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b90` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `7864ff992684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7864ff99267f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7864ff991f84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7864ff991f7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |

### `vm1/gblog/boot1-t420.txt` (snapshot)

`emulated 58160 vex128 16 vex256 58144 word 58160 byte 0 irqon 58160 efault 0 sse_init 0 ymm_init 0 zmmhi 0`

| comm | rip | count | library (layout match) | vaddr | function | instruction |
|---|---|---:|---|---|---|---|
| main | `7497ad4d2684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7497ad4d267f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7497ad4d1f84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7497ad4d1f7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7497ad4d1e64` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e64` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7497ad4d1e5f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e5f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7497ad4d1d44` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d44` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7497ad4d1d3f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d3f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7497ad47ec50` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c50` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `7497ad47ec4b` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c4b` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `7497ad47eb95` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b95` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `7497ad47eb90` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b90` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `78ae3f38b684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `78ae3f38b67f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `78ae3f38af84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `78ae3f38af7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |

### `vm1/gblog/boot1-t90.txt` (snapshot)

`emulated 9584 vex128 16 vex256 9568 word 9584 byte 0 irqon 9584 efault 0 sse_init 0 ymm_init 0 zmmhi 0`

| comm | rip | count | library (layout match) | vaddr | function | instruction |
|---|---|---:|---|---|---|---|
| main | `7b651ab84684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7b651ab8467f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7b651ab83f84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7b651ab83f7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7b651ab83e64` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e64` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7b651ab83e5f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e5f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7b651ab83d44` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d44` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7b651ab83d3f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d3f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7b651ab30c50` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c50` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `7b651ab30c4b` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c4b` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `7b651ab30b95` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b95` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `7b651ab30b90` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b90` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `703390599684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `70339059967f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `703390598f84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `703390598f7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |

### `vm1b/gblog/boot1-t180.txt` (snapshot)

`emulated 23568 vex128 16 vex256 23552 word 23568 byte 0 irqon 23568 efault 0 sse_init 0 ymm_init 0 zmmhi 0`

| comm | rip | count | library (layout match) | vaddr | function | instruction |
|---|---|---:|---|---|---|---|
| main | `796faf59b684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `796faf59b67f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `796faf59af84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `796faf59af7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `796faf59ae64` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e64` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `796faf59ae5f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e5f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `796faf59ad44` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d44` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `796faf59ad3f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d3f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `796faf547c50` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c50` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `796faf547c4b` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c4b` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `796faf547b95` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b95` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `796faf547b90` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b90` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `7fb4bc78a684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7fb4bc78a67f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `7fb4bc789f84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `7fb4bc789f7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |

### `vm1b/gblog/boot1-t40.txt` (snapshot)

`emulated 2224 vex128 16 vex256 2208 word 2224 byte 0 irqon 2224 efault 0 sse_init 0 ymm_init 0 zmmhi 0`

| comm | rip | count | library (layout match) | vaddr | function | instruction |
|---|---|---:|---|---|---|---|
| main | `78ae3394d684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `78ae3394d67f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `78ae3394cf84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `78ae3394cf7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `78ae3394ce64` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e64` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `78ae3394ce5f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e5f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `78ae3394cd44` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d44` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `78ae3394cd3f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d3f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `78ae338f9c50` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c50` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `78ae338f9c4b` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c4b` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `78ae338f9b95` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b95` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `78ae338f9b90` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b90` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `78df76328684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `78df7632867f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `78df76327f84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `78df76327f7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |

### `vm1b/gblog/boot1-t90.txt` (snapshot)

`emulated 9584 vex128 16 vex256 9568 word 9584 byte 0 irqon 9584 efault 0 sse_init 0 ymm_init 0 zmmhi 0`

| comm | rip | count | library (layout match) | vaddr | function | instruction |
|---|---|---:|---|---|---|---|
| main | `780d3ad4b684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `780d3ad4b67f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `780d3ad4af84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `780d3ad4af7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `780d3ad4ae64` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e64` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `780d3ad4ae5f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7e5f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `780d3ad4ad44` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d44` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `780d3ad4ad3f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7d3f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `780d3acf7c50` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c50` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `780d3acf7c4b` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74c4b` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `780d3acf7b95` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b95` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm5,%ymm6` |
| main | `780d3acf7b90` | 24 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0x74b90` | `_ZN5mlkem12_GLOBAL__N_124mlkem_decap_no_self_testILi3EEEv...` | `{vex} vpdpwssd %ymm10,%ymm7,%ymm4` |
| main | `718a868ec684` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc8684` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `718a868ec67f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc867f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |
| main | `718a868ebf84` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f84` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm10,%ymm5` |
| main | `718a868ebf7f` | 80 | apex:com.google.android.tethering/lib64/libcrypto_httpengine.so | `0xc7f7f` | `_ZN5mlkem12_GLOBAL__N_111encrypt_cpaILi3EEEvPhPKNS0_10pub...` | `{vex} vpdpwssd %ymm13,%ymm11,%ymm4` |

### `vm2/serial0.log` at 442.0 s (kmsg time)

`emulated 248554483 vex128 248346955 vex256 207528 word 248352169 byte 202314 irqon 248554483 efault 2 sse_init 2 ymm_init 248143785 zmmhi 404616`

| comm | rip | count | library (layout match) | vaddr | function | instruction |
|---|---|---:|---|---|---|---|
| vnni_kat | `75a06cc960df` | 404614 | vnni_kat (KAT test binary, not part of the image) | `-` | `-` | `-` |
| vnni_kat | `75a06cc9616f` | 2 | vnni_kat (KAT test binary, not part of the image) | `-` | `-` | `-` |
| vnni_kat | `75a06cc960ef` | 2 | vnni_kat (KAT test binary, not part of the image) | `-` | `-` | `-` |
| vnni_kat | `75a06cd20ff9` | 2 | vnni_kat (KAT test binary, not part of the image) | `-` | `-` | `-` |
| Profile Saver | `7cc3728641ea` | 2707 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x111ea` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| Profile Saver | `7cc3728641d2` | 2707 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x111d2` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| Profile Saver | `7cc3728641b6` | 2707 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x111b6` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| Profile Saver | `7cc37286419e` | 2707 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x1119e` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| Profile Saver | `7cc372864182` | 2707 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x11182` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| Profile Saver | `7cc37286416a` | 2707 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x1116a` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm10,%xmm6` |
| Profile Saver | `7cc372864158` | 2707 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x11158` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| Profile Saver | `7cc372864145` | 2706 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x11145` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| Profile Saver | `7cc372864067` | 73 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x11067` | `adler32_simd_` | `{vex} vpdpwssd %xmm11,%xmm9,%xmm6` |
| dex2oat | `7a8c4815a1ea` | 2397 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x111ea` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| dex2oat | `7a8c4815a1d2` | 2397 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x111d2` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| dex2oat | `7a8c4815a1b6` | 2397 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x111b6` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |

### `vm2-early/serial0.log` at 198.7 s (kmsg time)

`emulated 211809117 vex128 211599157 vex256 209960 word 211599891 byte 209226 irqon 211809117 efault 8 sse_init 2 ymm_init 211389905 zmmhi 418440`

| comm | rip | count | library (layout match) | vaddr | function | instruction |
|---|---|---:|---|---|---|---|
| pool-265-thread | `7abf525c708a` | 6 | apex:com.google.android.appsearch/lib64/libicing_anywhere.so | `0x2a508a` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| pool-265-thread | `7abf525c7072` | 6 | apex:com.google.android.appsearch/lib64/libicing_anywhere.so | `0x2a5072` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| pool-265-thread | `7abf525c7056` | 6 | apex:com.google.android.appsearch/lib64/libicing_anywhere.so | `0x2a5056` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| pool-265-thread | `7abf525c703e` | 6 | apex:com.google.android.appsearch/lib64/libicing_anywhere.so | `0x2a503e` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| pool-265-thread | `7abf525c7022` | 6 | apex:com.google.android.appsearch/lib64/libicing_anywhere.so | `0x2a5022` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| pool-265-thread | `7abf525c700a` | 6 | apex:com.google.android.appsearch/lib64/libicing_anywhere.so | `0x2a500a` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm10,%xmm6` |
| pool-265-thread | `7abf525c6ff8` | 6 | apex:com.google.android.appsearch/lib64/libicing_anywhere.so | `0x2a4ff8` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| pool-265-thread | `7abf525c6fe5` | 6 | apex:com.google.android.appsearch/lib64/libicing_anywhere.so | `0x2a4fe5` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| pool-265-thread | `7abf525c6f7e` | 5 | apex:com.google.android.appsearch/lib64/libicing_anywhere.so | `0x2a4f7e` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| pool-265-thread | `7abf525c6f6b` | 5 | apex:com.google.android.appsearch/lib64/libicing_anywhere.so | `0x2a4f6b` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| LazyTaskWriterT | `7ac3657e41b6` | 48 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x111b6` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| LazyTaskWriterT | `7ac3657e419e` | 48 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x1119e` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| LazyTaskWriterT | `7ac3657e4182` | 48 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x11182` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| LazyTaskWriterT | `7ac3657e416a` | 48 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x1116a` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm10,%xmm6` |
| LazyTaskWriterT | `7ac3657e4158` | 48 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x11158` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| LazyTaskWriterT | `7ac3657e4145` | 48 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x11145` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |

### `vm2-early/serial0.log` at 274.4 s (kmsg time)

`emulated 220956521 vex128 220535257 vex256 421264 word 220538069 byte 418452 irqon 220956521 efault 16 sse_init 4 ymm_init 220115959 zmmhi 836880`

| comm | rip | count | library (layout match) | vaddr | function | instruction |
|---|---|---:|---|---|---|---|
| DefaultDispatch | `7ac3657e41ea` | 736 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x111ea` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| DefaultDispatch | `7ac3657e41d2` | 736 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x111d2` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| DefaultDispatch | `7ac3657e41b6` | 736 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x111b6` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| DefaultDispatch | `7ac3657e419e` | 735 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x1119e` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| DefaultDispatch | `7ac3657e4182` | 735 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x11182` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| DefaultDispatch | `7ac3657e416a` | 735 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x1116a` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm10,%xmm6` |
| DefaultDispatch | `7ac3657e4158` | 735 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x11158` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| DefaultDispatch | `7ac3657e4145` | 735 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x11145` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| DefaultDispatch | `7ac3657e4067` | 112 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x11067` | `adler32_simd_` | `{vex} vpdpwssd %xmm11,%xmm9,%xmm6` |
| vnni_kat | `7ec3657cd0df` | 418438 | vnni_kat (KAT test binary, not part of the image) | `-` | `-` | `-` |
| launcher-loader | `7ac3657e41ea` | 672 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x111ea` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| launcher-loader | `7ac3657e41d2` | 672 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x111d2` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| launcher-loader | `7ac3657e41b6` | 672 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x111b6` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| launcher-loader | `7ac3657e419e` | 672 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x1119e` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| launcher-loader | `7ac3657e4182` | 672 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x11182` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm9,%xmm6` |
| launcher-loader | `7ac3657e416a` | 672 | /system/lib64/libz.so or /vendor/lib64/libz.so | `0x1116a` | `adler32_simd_` | `{vex} vpdpwssd %xmm2,%xmm10,%xmm6` |

