# gpu-compute: Wave 2 integration notes

Component 5 (INTERFACES.md **v1.1**, sections 1.1, 9.1, 9.3, 11, 15.3, 18.5; v1.1 changes in section 21: C3, C10). Source: `C:\GB-build\native\gpu-compute\`. Read
its `README.md` for the design. This Wave 1 step delivered:

* the private Vulkan loader stub `libgb_nvk_loader.so`;
* the service `libgb_gpu_compute.so`: front end plus llama.cpp b11552 with the Vulkan backend, statically linked;
* the `/vendor/bin` launcher `gb_gpu_compute`;
* `gb_gpu_compute.rc` and the draft manifest `gpu_compute.json`;
* the build scripts and the tests.

No shared file was edited. Nothing ran on the GTX 1650.

**v1.1 fix pass (2026-10-25).** Review R1-H1: the v1 gb_power closed every connection idle for 30 s, so a loaded
model lost its power hold about every 30 s. Now gb_power exempts the compute-svc connection from the idle close, and
the service re-sends its state every 15 s (keep-alive) and reconnects in every state. `gb_gpu_compute.rc` creates
`/data/vendor/gb` as 0751 (C1). Only `libgb_gpu_compute.so` and the rc changed; the launcher, the stub and `gc_probe`
are byte-identical to v1.

## 1. Artifacts (sha256)

The Android files were built by `scripts/30_build_android.sh`: NDK r27c, x86_64, API 35, `c++_static`, stripped. They
were copied to `C:\GB-build\native\gpu-compute\out\`. A second clean build in another directory gave the same bytes
for all of them (GC-10).

| Artifact | Bytes | sha256 | Goes to |
|---|---|---|---|
| `out\gb_gpu_compute` (launcher, PIE) | 8152 | `f275dae628950181d45cfded2752398d81bf0a17c20c692074069cdd77d4b03a` | cpio `vup/bin/gb_gpu_compute` 0755 -> `/vendor/bin/gb_gpu_compute` |
| `out\lib64\libgb_nvk_loader.so` | 51560 | `576193fa4441808428ee5456aa2722115bb555b595f98baa4fca1412e011ae05` | payload `lib64/gpu-compute/libgb_nvk_loader.so` 0644 |
| `out\lib64\libgb_gpu_compute.so` | 32844816 | `8042d3658cdc44e197bed460bc9ec35f1307e74a6bd6037f5f8fa5b9cf970853` | payload `lib64/gpu-compute/libgb_gpu_compute.so` 0644 (v1 `61ffd825...`) |
| `gb_gpu_compute.rc` | | `e1eaf3b9d8da68d5a7e5758247ae7bb3946eea167620806a4455a7837ba2a256` | manifest `rc_files` (v1 `2af97424...`) |
| `gpu_compute.json` | | `b29ec2fafb4fc53a679a3e496dca92d05ddcd48225781c898c20c30cb17fb7a0` | copy to `C:\GB-x86\inject\native\components\` (2.1) |
| `out\test\gc_probe` (test harness, **never shipped**) | | `700b6b70a46019c6296a58d6823946e9850dfe4f0b6b2b673753d121d3ccbdb2` | none |

Payload budget (W2-08): the two libraries are 32.9 MB uncompressed and 16,355,328 bytes as an erofs image built
with INTERFACES 6.3's `mkfs.erofs -zlz4hc,12 -T0 --all-root -x -1` options (measured on the builder). The ESP copy and
the `/data/vendor/gb/payload` copy each grow by about 16 MB. The unstripped copies (for symbolising tombstones) stay
on the builder in `/build/native/dgpu/gpu-compute/out-android/unstripped/`.

The service needs a model in `/data/vendor/gb/models/<id>.gguf`. None ships with the image, by design (no model
downloads, INTERFACES 1.4).

## 2. Exact changes Wave 2 must make to shared files

### 2.1 `C:\GB-x86\inject\native\components\gpu_compute.json` (new; W2-01, only together with W2-08)

The two libraries live only in the payload (INTERFACES 6.2, 11.1, D7), so this manifest needs W2-01's `payload` key
and W2-08's `dgpu_payload()`. Before W2-08 the component must **not** be added: the launcher would only ever exit 3,
because `/odm/dgpu` does not exist in the legacy layout. Copy `C:\GB-build\native\gpu-compute\gpu_compute.json`
unchanged:

```json
{
 "name": "gpu_compute",
 "source": "C:\\GB-build\\native\\gpu-compute",
 "files": [
  {"src": "C:\\GB-build\\native\\gpu-compute\\out\\gb_gpu_compute", "dest": "vup/bin/gb_gpu_compute", "mode": "0755"}
 ],
 "payload": [
  {"src": "C:\\GB-build\\native\\gpu-compute\\out\\lib64\\libgb_nvk_loader.so", "dest": "lib64/gpu-compute/libgb_nvk_loader.so", "mode": "0644"},
  {"src": "C:\\GB-build\\native\\gpu-compute\\out\\lib64\\libgb_gpu_compute.so", "dest": "lib64/gpu-compute/libgb_gpu_compute.so", "mode": "0644"}
 ],
 "rc_files": ["C:\\GB-build\\native\\gpu-compute\\gb_gpu_compute.rc"],
 "rc_text": "",
 "build_prop": []
}
```

This is README W2-01's `gpu_compute.json` with the exact library list. llama.cpp/ggml are linked statically into
`libgb_gpu_compute.so`, so there are no further `lib64/gpu-compute/*.so` files (GC-D6). The payload files are not
executables, which INTERFACES 6.2 requires (0644). `make_dgpu_payload.sh` (component 1) already treats
`lib64/gpu-compute/*` as optional P6 entries.

Verify after build_esp:
* the native cpio has `gbnative/vup/bin/gb_gpu_compute` (0755) with the sha256 from section 1;
* the payload listing (`erofs_ls.py`) has both libraries with their sha256, and `MANIFEST.sha256` matches;
* the native `etc/init/gb.rc` contains `service gb_gpu_compute /vendor/bin/gb_gpu_compute`, `user nobody`,
  `socket gb_gpu_compute stream 0666 nobody graphics` and `mkdir /data/vendor/gb/compute 0700 nobody graphics`;
* the VM cpio contains no `gb_gpu_compute` (invariant 19).

### 2.2 `gb_gpu_compute.rc`: one line beyond INTERFACES 15.3

The shipped file is 15.3's text plus `setenv MESA_SHADER_CACHE_DISABLE false` (GC-D1). Component 4 found that Mesa
keeps its disk shader cache off on Android unless that variable is false (`nvk-gfxdriver.md` 2.4). The service also
sets both Mesa variables itself when they are missing, so dropping the line costs only the cache. The three `mkdir`
lines agree with `gb_nouveau.rc` 15.1 v1.1 (`/data/vendor/gb` 0751 root shell: shell can no longer create or
rename entries there; nobody and shell still traverse it to `models/`, `compute/` and the 0644 `compute_allow`).

### 2.3 `C:\GB-x86\verify_image.py` (W2-07): proposed extra checks (read-only)

1. `gbnative/vup/bin/gb_gpu_compute` has the sha256 from section 1.
2. With the payload: `lib64/gpu-compute/libgb_nvk_loader.so` and `libgb_gpu_compute.so` are in
   `ESP:/gb/dgpu-<krel>.erofs` with the sha256 from section 1.
3. No file in the image is named `libvulkan.so` outside `/system` and `/vendor` (the stub never is).
4. `gbnative/etc/init/gb.rc` has no `gb_gpu_compute` service running as root (the line after
   `service gb_gpu_compute` contains `user nobody`).

### 2.4 SELinux (W2-15): nothing to add

* The service runs in `u:r:shell:s0` (`typepermissive shell`) as uid nobody, group graphics.
* It touches:
  * `/dev/socket/gb_gpu_compute` (init-created, `socket_device`) and `/dev/socket/gb_power` (connect);
  * the nouveau render node (0660 root:graphics, INTERFACES 7.5);
  * `/odm/dgpu/**` (`vendor_file` via the erofs `context=`);
  * `/data/vendor/gb/{models,compute,compute_allow}` (`vendor_data_file`);
  * a `stat()` of `/data/data/gb.sb3.gpu` (Q1);
  * reads of `/sys/class/drm/*`, `/sys/bus/pci/devices/*/{vendor,class,power/control}` and `/proc/meminfo`.
* All of that is covered by the permissive shell domain. If shell is ever made enforcing, it needs real policy.

### 2.5 Wave 2 test GC-11 (W2-16, **VM**, needs the user's approval)

The VM image carries no dGPU stack. GC-11 therefore runs on the VMware native-smoke boot (`gb-ntest`) once W2-08 has
landed:

```
adb shell /vendor/bin/gb_gpu_compute; echo rc=$?        -> rc=3
adb shell 'logcat -d -s gb_gpu_compute | tail -2'       -> "no render node bound to nouveau: NO_DEVICE"
```

That boot reads `gb_hw=native` (NP-25 expects `refused:i915`), so the launcher passes the native check and stops at
the nouveau-node check. On a plain VM image it would stop at `gb_hw=vm`, also with exit 3. gb_power's
`compute-start` there answers `ENODEV` (state `unavailable`), so init never starts the service. Expect
`getprop vendor.gb.compute.state` to read `stopped`.

### 2.6 Docs (W2-14)

Add a "Compute service" section to `docs\NVIDIA.md`:
* what it is (local LLM inference on the NVIDIA GPU through NVK, OpenAI-style API on a UNIX socket, no network);
* how to add a model (`adb push <file>.gguf /data/vendor/gb/models/<id>.gguf`) and which apps may use it (GbGpu,
  allowlisted apps, `adb shell`);
* that it holds the GPU awake while a model is loaded and exits 120 s after the last use;
* that detaching stops it;
* that the 4 GB VRAM limits models to about 3B parameters at Q4.

The source is the component README.

## 3. Tests run (builder 192.168.115.132, 2026-10-25)

Scripts: `scripts/50_run_tests.sh` (all except GC-10) and `scripts/60_repro.sh` (GC-10). Logs are in
`C:\GB-build\native\gpu-compute\out\logs\`: `tests_run.log` is the full run, `tests\*.log` the per-test logs,
`tests\results.json` holds the 60 end-to-end checks, `gc10_repro.log` the reproducibility run and
`build_fixpass.log` the tail of the two builds. Final run (v1.1 fix pass, after a full host + Android rebuild):
2026-10-25 10:00-10:08 UTC, `failures: 0`; GC-10 10:08-10:18 UTC, `REPRO PASS`.

| Id | Result | Evidence (real output) |
|---|---|---|
| GC-01 NDK cross-build + ELF contract | PASS | `check_elf.py`: `CHECK_ELF PASS (0 failures)`. ELF64 x86-64; `PT_LOAD align >= 16 KiB (['0x4000', ...])`; no TEXTREL; NEEDED of the service `libdl.so liblog.so libm.so libgb_nvk_loader.so libc.so`, of the stub and the launcher `libdl.so liblog.so libm.so libc.so`; no `libvulkan*`; static libc++; SONAMEs `libgb_nvk_loader.so` / `libgb_gpu_compute.so`; service `DT_RUNPATH = $ORIGIN`; `loader exports all 215 core commands`, `only vk*`; `service exports only gb_gpu_compute_main` (`tests\gc01_check_elf.log`). |
| GC-01 imports vs lapis 276.R4 | PASS | component 4's `import_check.py --root rootfs-276R4`: `203 undefined dynamic symbols, 0 unresolved` (service), `15 ... 0 unresolved` (stub), `28 ... 0 unresolved` (launcher), `RESULT: PASS` (`tests\gc01_import.log`). |
| GC-02 stub self test (host, lavapipe) | PASS | `TEST_LOADER symbols PASS: 217 checks`, `TEST_LOADER full PASS: 37 checks` (two live instances, device, queue, command buffer, `vkCmdFillBuffer` + read-back through the exported trampolines, proc-addr routing, no layers), `TEST_LOADER noicd PASS: 4 checks` (`VK_ERROR_INCOMPATIBLE_DRIVER`). |
| GC-02 llama.cpp `test-backend-ops -b Vulkan0` through the stub on lavapipe | PASS | `ldd test-backend-ops`: only `libgb_nvk_loader.so`, no libvulkan. `ADD,MUL,SCALE,RMS_NORM,GET_ROWS,SET_ROWS,CPY,CONT,ROPE,SOFT_MAX,GLU: 1818/1818 tests passed`; `MUL_MAT type_a=(f16|q4_0|q8_0|q4_K|q5_K|q6_K),type_b=f32,m=16,n=(1|9),k=256: 23/23 tests passed`; `FLASH_ATTN_EXT f16 hs 64/128: 70/70 tests passed`; each `Backend Vulkan0: OK`. |
| GC-02 service `--probe` (host, lavapipe) | PASS | `PROBE device Vulkan0 (llvmpipe (LLVM 20.1.2, 256 bits), 7941 MiB)`, `PROBE backend Vulkan0 initialised`, `PROBE compute mul_mat 64x8x256 in 3191 ms: max rel err 0 -> correct`. |
| GC-03 end-to-end over the UNIX socket (tiny synthetic Q4_0/Q8_0 models) | PASS | `GET /health` `{"gb_state":"idle","status":"ok"}`; `/v1/models` ids `['huge','tiny','tiny8']` (symlink, bad ids, `.txt` excluded); chat non-stream `200 in 8.6 s usage={'completion_tokens': 16, 'prompt_tokens': 30, 'total_tokens': 46} finish=length` (first load includes lavapipe pipeline compiles); stream `events=19 streamed text == non-stream text: True`; `/v1/completions` plain and stream; `stop=['e']` -> `finish=stop`, no `e`; seeded sampling reproducible. |
| GC-04 peer credentials (real SO_PEERCRED, uids via sudo) | PASS | 200 for uid 2000, 10123 (GbGpu data-dir owner), 1010123, 10200 (compute_allow), 1010200. 403 `forbidden` for 10999, 10555 (packages.list decoy, unreadable for nobody), 1000, 9999. A POST chat as 10999 gets 403. compute_allow is re-read on mtime change (10999 then gets 200). The GbGpu uid is refused once the system APK is gone. |
| GC-05 limits | PASS | bad JSON / no model / bad id / `max_tokens 5000` / GET on a POST path / chunked -> 400; unknown or symlinked model -> 404; `2 MiB body: 413 ... request body larger than 1 MiB`; `model > 3584 MiB: 413`; other model while running -> 409 `conflict`; `/gb/unload` while running -> 409; `5th connection refused (4 open: 1 generating + 3 queued): 429 closed=True`; queue full with the cap raised -> `429 generation queue full`; client disconnect aborts (`(aborted)` in the log) and the queued requests then finish (`[200, 200, 200] in 0.4 s`); low `MemAvailable` -> 503. |
| GC-06 lifecycle | PASS | idle exit `rc=0 after 8.1 s states=['loaded','unloading','idle']` (idle set to 8 s in the test build); SIGTERM during a generation `rc=0 in 0.10 s`, hold released; screen off (`card0-eDP-1/dpms` = `Off`) -> unloaded `after 3.2 s` (2 s threshold in the test build), service keeps running. |
| GC-07 no TCP | PASS | `/proc/<pid>/fd` sockets vs `/proc/net/{unix,tcp*,udp*,raw*}`: `2 sockets, all unix: True, inet: 0`; `strace -f --seccomp-bpf -e trace=socket,...` over a whole server lifetime: `8 socket() calls, 0 inet`. |
| GC-08h/i keep-alive (v1.1) | PASS | `exempt compute-svc connection: hold kept through 8 s without a request (no keep-alive)`: `200 power=on idle_closed=0`; `strict server (idle close 2.5 s for every connection): keep-alive keeps the hold for 8 s`: `200 power=on idle_closed=0 re-sent=4 ['loaded']`. |
| GC-08 gb_power interplay (mock with gb_power's reply format) | PASS | sequence `['hello', 'compute-state idle', 'compute-state loading', 'compute-state loaded', 'compute-state serving', 'compute-state loaded']`; `power/control=on` while loaded, `auto` after `/gb/unload` (`['loaded','unloading','idle']`); gb_power gone -> `dGPU power hold lost for 1500 ms: unloading the model`, new loads `503 gb_power did not grant the dGPU power hold`; gb_power back -> `hello`, `compute-state loading`, 200; EPERM -> 503. |
| GC-09 drm-shim TU117, bionic chroot of lapis 276.R4 | PASS | Android `gc_probe` + `libgb_gpu_compute.so` + the shipped 8a ICD (`625314e0...`, identical to `C:\GB-build\native\nvk-android\bin\vulkan.nouveau.so`) + component 4's noop drm-shim (`88ed9ffb...`), `NOUVEAU_CHIPSET=167`: `PROBE device Vulkan0 (NVK , 3072 MiB)`, `PROBE backend Vulkan0 initialised`, `PROBE compute mul_mat 64x8x256 in 759 ms: max rel err 0.9 -> MISMATCH`, `exit=5`. The mismatch is expected because the noop shim executes nothing; device enumeration through the stub, VkDevice creation, NAK compile and submit/wait all worked. |
| GC-10 reproducible build | PASS | `60_repro.sh`: a clean second build in `b-android-r2` gave `same` for all 7 files (stripped and unstripped), `REPRO PASS: two builds in .../b-android and .../b-android-r2 are byte-identical`. |
| GC-11 refuses cleanly without an NVIDIA device (host + bionic) | PASS | host launcher: gb_hw=vm -> 3, no nouveau node -> 3, no payload -> 3, no init socket -> 4; bionic: `gb_hw=vm: exit 3 NO_DEVICE`; native without a nouveau node: launcher `exit=3`, and the payload NVK through the stub `PROBE no-device ... exit=3`; missing ICD -> `503 ... no Vulkan GPU device (NVK) found`, `/health` 503, clean exit 0. The VMware run is Wave 2 (2.5). |
| synthetic models deterministic | PASS | two generations byte-identical: q4_0 `578b29f0740eb5d825195ce39a9e20052d6b5bfb1d1c4f8aa19351f217dd14ba`, q8_0 `f6509d698d69be51ea6da3e726c514ae58439d1aff9e38572971e4d71039c5ad`. |
| static checks | PASS | `shellcheck -x -P SCRIPTDIR -s bash scripts/*.sh test/chroot_dir.sh`: rc 0 (justified disables only); `python3 -I -m py_compile` of every `.py`; the loader is built with `-Wall -Wextra -Werror` (gcc and NDK clang). |
| GC-HW | NOT RUN | hardware only (section 6). |

## 4. Contract notes for the other components (no shared files)

* **gb-power (component 2):**
  * the service connects once, sends `hello`, and expects `role` `compute-svc`. That needs uid 9999 and
    `/proc/<pid>/exe` = `/vendor/bin/gb_gpu_compute`; the launcher keeps that exe because it `dlopen()`s the payload
    library instead of exec'ing;
  * it then sends `compute-state idle`, and later `loading` / `loaded` / `serving` / `unloading` / `idle`, keeping the
    connection open;
  * a load needs `hold:1` in the reply to `loading`. gb_power replies at once and writes `power/control=on` from its
    worker, so the service additionally waits up to 3 s for `power/control` to read `on`;
  * reconnect: after EOF the service reconnects every 2 s in every state and re-sends its state, and unloads if the
    hold is not back within 10 s;
  * keep-alive (v1.1): the current state is re-sent every 15 s on the live connection, and gb_power must not
    idle-close the compute-svc connection (gb_power 1.1 exempts it; tested both ways: GC-08h with an exempting mock and
    no keep-alive, GC-08i with a mock that idle-closes every connection after 2.5 s);
  * SIGTERM from `ctl.stop` ends the service within 5 s (measured, section 3), so gb_power's 30 s refcnt poll covers it.
* **nouveau-policy (component 1):** `gb_nouveau.sh off` step 3 (`ctl.stop gb_gpu_compute`, wait up to 5 s for
  `init.svc.gb_gpu_compute=stopped`) fits the measured exit time. The service never writes any `vendor.gb.*` property.
* **gpu-ui (component 6):** GbGpu may call the compute socket directly; its uid is always allowed (Q1 explains how the
  service finds it). Other apps need `compute-allow <pkg>` through gb_power. Clients that get ECONNREFUSED/ENOENT call
  gb_power `compute-start` and retry for up to 10 s (INTERFACES 11.5). The idle exit is 120 s after the last request
  *and* the last connection close, so a client that keeps a connection open keeps the GPU awake. Close connections
  when done; the server closes idle keep-alive connections after 30 s.
* **nvk-gfxdriver (component 4):** the stub loads `/odm/dgpu/lib64/hw/vulkan.nouveau.so` through
  `vk_icdNegotiateLoaderICDInterfaceVersion` (requests interface 5) and `vk_icdGetInstanceProcAddr`. It relies on
  NVK's dispatchable objects starting with `ICD_LOADER_MAGIC`; Mesa's `vk_object_base` guarantees that, including
  recycled command buffers. GC-09 used component 4's 8a ICD from `/build/native/nvk-android/stage-8a/` read-only.

## 5. Spec questions (please resolve before the Wave 2 edits)

* **Q1 GbGpu appId for the service.** INTERFACES 11.3 allows "the GbGpu appId (any user)", but the service runs as
  nobody and cannot read `/data/system/packages.list` (0640 system:package_info; checked on the VM with read-only adb).
  * Implemented: if `packages.list` is unreadable, use the owner uid of `/data/data/gb.sb3.gpu`. `/data/data` is 0771
    system:system, so `stat()` works, and only PackageManager creates entries there. This counts only while
    `/odm/app/GbGpu/GbGpu.apk` exists (the same squatting guard as gb_power 9.2).
  * Alternative, if preferred: gb_power writes a `<appId> gb.sb3.gpu` line into `compute_allow` itself.
* **Q2 queue vs connection limit (GC-05).** With at most 4 connections and one request per connection, 1 in flight +
  3 queued already uses every connection. So "429 after the queue" shows up as the 5th connection's `429`. The
  queue-full `429` exists and is tested with the connection cap raised (test build only). Should the connection cap be
  5+ so that both limits can bite?
* **Q3 timeouts not in the spec:**
  * 30 s receive/keep-alive (so that an idle client cannot block the idle exit forever);
  * 10 s grace before unloading when the gb_power hold is lost;
  * 3 s `power/control` wait;
  * 4.5 s hard shutdown limit;
  * 8 MiB output cap for a client that does not read.
* **Q4 `compute_allow` appIds below 10000** are ignored by the service. gb_power accepts 0..99999, but it only ever
  writes app appIds from `packages.list`.
* **Q5 600 s limit.** A request that hits it returns its partial text with `finish_reason:"length"` instead of an
  error (GC-D5).

## 6. Hardware-only checks (not run)

These are listed in the component README ("Hardware-only checks"):
* NVK on TU117: model load, tokens/s against CPU and ANV;
* the `power/control` hold and `vendor.gb.compute.state` sequence;
* a model over 2 GB with idle unload and reload under memory pressure (no `runtime_status=error`, identical greedy
  output before and after);
* detach while serving;
* the i915 eDP `dpms` screen-off signal (D16, INFERRED);
* 30-60 min soaks per `platform_profile`;
* the Mesa shader cache on the second start;
* SELinux audit;
* Terminal VM reachability (open).

## 7. Deviations from the plan / spec

| # | Plan / spec | Done | Why |
|---|---|---|---|
| GC-D1 | rc exactly as 15.3 | `+ setenv MESA_SHADER_CACHE_DISABLE false` | Mesa disables the disk cache on Android otherwise (component 4). |
| GC-D2 | GbGpu appId from packages.list (9.2 style) | owner of `/data/data/gb.sb3.gpu` when packages.list is unreadable | uid nobody cannot read packages.list (Q1). |
| GC-D3 | "429 after the queue" | 5th connection `429`; the queue-full `429` is tested with a raised cap | The two limits overlap (Q2). |
| GC-D4 | not specified | 30 s keep-alive, 10 s hold-loss grace, 3 s power wait, 4.5 s hard stop, 8 MiB output cap; v1.1: 15 s gb_power keep-alive (now in INTERFACES 9.1/11.5) | Robustness (Q3). |
| GC-D5 | 600 s per request | partial result, `finish_reason:"length"` | More useful than discarding the text (Q5). |
| GC-D6 | `lib64/gpu-compute/<other ggml/llama .so>` (6.2) | llama/ggml static inside `libgb_gpu_compute.so` | Two payload files; NEEDED stays LLNDK + one sibling. |
| GC-D7 | NDK sysroot Vulkan headers | Ubuntu Vulkan-Headers 1.3.275 (with `vulkan.hpp`) + SPIRV-Headers for both builds | ggml-vulkan needs `vulkan.hpp`/`spirv.hpp`; the same headers keep the host test build and the device build identical. |
| GC-D8 | plan P6: "front end proxies to a loopback port in a private netns if llama-server has no UNIX listener" | own HTTP front end on the UNIX socket, llama-server not used | INTERFACES D15 (AF_UNIX only); llama-server is TCP-only and much larger. |
| GC-D9 | "Resource limits on RAM and threads" | `RLIMIT_DATA` 8 GiB, `RLIMIT_NOFILE` 256, no core, `NO_NEW_PRIVS`, 4 compute threads, MemAvailable check before loads, `oom_score_adjust 800` | `RLIMIT_AS` would break Scudo's large `PROT_NONE` reservations (INFERRED); a per-uid `RLIMIT_NPROC` cannot be used because nobody is shared. |
| GC-D10 | builder packages | added `spirv-headers` and `python3-numpy` (Ubuntu archive) | ggml-vulkan's `find_package(SPIRV-Headers)`; gguf-py for the synthetic test model. |
