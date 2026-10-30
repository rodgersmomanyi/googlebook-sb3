# gatekeeper: software Gatekeeper HAL (lock-screen PIN)

`android.hardware.gatekeeper-service.gb` (gb_gatekeeper 0.2.0): a small Gatekeeper HAL for Googlebook OS on VMware
and on the Surface Book 3, so a lock-screen PIN can be set. It is a **software** lock: there is no Trusty/GSC, so no
key is hardware-backed. What it does and does not protect, recovery and limits: [docs/GATEKEEPER.md](../../docs/GATEKEEPER.md).
The full design, integration record and test results: [dgpu/w4/GATEKEEPER.md](../dgpu/w4/GATEKEEPER.md).

| Path | What |
|---|---|
| `src/` | the service: AOSP libgatekeeper glue, scrypt + HMAC password signature under a per-device key, persistent failure records, the ISharedSecret participant, the hand-written stable-AIDL server over the libbinder_ndk C API |
| `tests/` | host unit tests (`test_main.cpp`, fake binder and service manager, run under ASan/UBSan and on lapis's bionic), the independent Python check `ref_check.py`, the frozen on-disk vectors (`frozen_v1.json`, `test_frozen_v1.cpp`, `frozen_check.py`: handles 0.2.0 enrolled must keep verifying; never regenerate them; wave 6), and `regress/pre_w4_src/` (the reviewed 0.1.0 sources: `REGRESS_OLD=tests/regress/pre_w4_src sh build.sh` proves the wave 4 tests fail on them) |
| `rc/gb_gatekeeper.rc`, `vintf/android.hardware.gatekeeper-service.gb.xml` | the init service block and the ODM VINTF fragment that `build_esp.py` puts into both trees (the fragment is checked against its reviewed sha256) |
| `component/gatekeeper.json` | the reviewed component draft; the pipeline uses `inject/native/components/gatekeeper.json` (the same files) |
| `third_party/aosp/` | unmodified AOSP sources (libgatekeeper, `hw_auth_token.h`, three libbinder_ndk platform headers), Apache-2.0, commits in `NOTICE` |
| `tools/` | the libbinder_ndk link stub for the NDK build |
| `build.sh`, `NOTICE` | build + tests on the Linux builder; licences and credits (AOSP; the AIDL framing adapted from gbOS86 `soft_gk.c`, Apache-2.0) |

Build on the builder (NDK r27c at `$NDK`, default `/build/ndk`; BoringSSL is cloned from AOSP and pinned):
`sh build.sh` writes `out/android/android.hardware.gatekeeper-service.gb`, `out/android/SHA256SUMS` and
`out/ACCEPTANCE.txt`. `build_esp.py` refuses a binary whose sha256 is not in `SHA256SUMS` and `ACCEPTANCE.txt`
(version 0.2.0 or later) or that needs a library other than `libbinder_ndk libc libdl liblog libm`. Our build of
0.2.0 is `505b404d0606bf80bcb41ff8a66a80c962934e3791d3b8db13645608da143d51` (reproduced byte for byte from empty
output directories on the builder). **The binary and `out/` are never committed.**
