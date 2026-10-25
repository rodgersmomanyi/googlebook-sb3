> **Repository note.** This is the component's working README, copied from the development tree. Paths
> such as `C:\GB-build\native\gpu-compute` mean `native/gpu-compute` in this repository, `C:\GB-build\native\dgpu`
> is [`native/dgpu`](../dgpu/), `C:\GB-x86` is the workspace (the repository root) and `builder:` paths are
> on the Linux builder. Build outputs, logs and older versions (`out/`, `logs/`, `superseded/`, `*.v1.1`, ...)
> are not published. What the NVIDIA support does for a user: [docs/NVIDIA.md](../../docs/NVIDIA.md).

# gpu-compute: NVIDIA (NVK) Vulkan compute service for Googlebook OS on the Surface Book 3

Component 5 of the dGPU work (plan `NVIDIA_PLAN.md` Phase 6, architecture layer L5). The interface contract is
`C:\GB-build\native\dgpu\INTERFACES.md` sections 9.3 (`compute-state`, `compute-start`), 11 (the service) and 15.3
(its rc file). Wave 2 integration: `C:\GB-build\native\dgpu\integration\gpu-compute.md`.

Status: everything that does not need the GTX 1650 has been built and tested on the builder. Nothing here has run on
the real GPU yet. The hardware checks are listed at the end of this file.

## What it does

`gb_gpu_compute` runs local LLM inference (llama.cpp, Vulkan backend) on the TU117 through NVK. Clients talk HTTP/1.1
(an OpenAI-compatible subset) over the UNIX socket `/dev/socket/gb_gpu_compute`. It never listens on TCP.

```
 client app (allowed uid) --HTTP/AF_UNIX--> /dev/socket/gb_gpu_compute (init: 0666 nobody graphics)
                                               |
 /vendor/bin/gb_gpu_compute (launcher, uid nobody, group graphics, u:r:shell:s0)
   checks: /odm/etc/gb_hw == native, a renderD* bound to nouveau, payload present   (else exit 3 NO_DEVICE)
   dlopen /odm/dgpu/lib64/gpu-compute/libgb_gpu_compute.so  (front end + llama.cpp/ggml, static libc++)
            NEEDED libgb_nvk_loader.so via DT_RUNPATH $ORIGIN
              libgb_nvk_loader.so: private loader stub, dlopen(/odm/dgpu/lib64/hw/vulkan.nouveau.so)
                                   -> vk_icdNegotiateLoaderICDInterfaceVersion + vk_icdGetInstanceProcAddr (NVK)
   one long-lived connection to /dev/socket/gb_power: "compute-state idle|loading|loaded|serving|unloading"
            (gb_power holds dGPU power/control=on while loading/loaded/serving; released on idle or EOF)
```

Why a private loader: lapis' `libvulkan.so` loads only `vulkan.<ro.hardware.vulkan>.so` (ANV), and NVK must never
become a system HAL (invariants 1 and 2). The stub is never named or SONAMEd `libvulkan.so`.

### Files

| Path | What |
|---|---|
| `loader/libgb_nvk_loader.c` | The stub. Loads one ICD (env `GB_NVK_ICD`, else `/odm/dgpu/lib64/hw/vulkan.nouveau.so`), never a layer. It exports all 215 Vulkan 1.0-1.3 core commands. Handles are passed through; like every loader the stub writes its dispatch pointer into the first word of each dispatchable handle (ICDs reserve it with `ICD_LOADER_MAGIC`, and the stub refuses handles without that magic). The stub implements the commands that create dispatchable objects itself (instance, physical devices and groups, device, queues, command buffers); for every other command `vkGetInstanceProcAddr`/`vkGetDeviceProcAddr` return the ICD's own pointer. It reports at most Vulkan 1.3, enumerates no layers and refuses layer requests. |
| `loader/gen_trampolines.py` | Generates the trampolines and dispatch tables from `vulkan_core.h` at build time. |
| `loader/libgb_nvk_loader.map` | Exports `vk*` only. |
| `service/gb_compute.cpp` | Front end and lifecycle: socket, HTTP, SO_PEERCRED allowlist, limits, gb_power client, worker queue, idle/screen-off/SIGTERM handling. |
| `service/gb_llm.cpp/.h` | llama.cpp wrapper, which runs on the worker thread only: GPU-only device selection, load/unload, chat template, sampling, stop strings, UTF-8-safe streaming. |
| `service/gb_compute.map` | Exports `gb_gpu_compute_main` only. |
| `launcher/gb_gpu_compute.c` | `/vendor/bin/gb_gpu_compute`. |
| `gb_gpu_compute.rc` | INTERFACES 15.3, plus `setenv MESA_SHADER_CACHE_DISABLE false` (see Deviations). |
| `gpu_compute.json` | Draft component manifest (payload layout). Wave 2 copies it, see the integration file. |
| `CMakeLists.txt` | One project for the NDK build and the glibc test build. |
| `LLAMA_PIN` | llama.cpp tag `b11552`, commit `23b0202a189c44a54625aadcb37a946dd1d6278d`, and the selection rule. |
| `scripts/` | `00_env.sh` (paths, pin, builder etiquette helpers), `10_fetch_llama.sh`, `15_headers.sh`, `20_build_host.sh`, `30_build_android.sh`, `40_make_tiny_gguf.py`, `50_run_tests.sh`, `60_repro.sh`, `bg.sh`. |
| `test/` | `test_loader.c`, `test_service.py`, `mock_gb_power.py`, `gc_http.py`, `gc_client.py`, `check_elf.py`, `gc_probe.c`, `chroot_dir.sh`. |
| `out/` | The artifacts pulled back from the builder: `gb_gpu_compute`, `lib64/*.so`, `test/gc_probe` (never shipped), `SHA256SUMS`, and `logs/` (full test run, per-test logs, `results.json`, reproducibility run). |

## HTTP API (INTERFACES 11.4)

| Request | Answer |
|---|---|
| `GET /health` | `200 {"status":"ok","gb_state":"idle|loading|loaded|serving"}`; `503` when no nouveau render node or no Vulkan GPU device |
| `GET /v1/models` | the regular files `/data/vendor/gb/models/<id>.gguf`, id `[A-Za-z0-9._-]{1,64}` (symlinks ignored), with `gb_loaded` and `size` |
| `POST /v1/chat/completions` | `model` (required), `messages` (string content or text parts), `max_tokens` 1..4096 (default 1024), `temperature` 0..2 (default 0.8, 0 = greedy), `top_p` (default 0.95), `stop` (string or up to 4), `seed`, `stream`; unknown fields ignored |
| `POST /v1/completions` | same with `prompt` |
| `POST /gb/unload` | `200 {"unloaded":true}`; `409` while a request runs |

Errors are `{"error":{"code":<http>,"type":<type>,"message":<text>}}` with the types `invalid_request` 400,
`forbidden` 403, `not_found` 404, `conflict` 409, `too_large` 413, `busy` 429, `gb_unavailable` 503 and
`internal` 500. Streaming uses `text/event-stream` with OpenAI-style `chat.completion.chunk` / `text_completion`
chunks: the first chunk carries the role, the last one `finish_reason` and `usage`, then comes `data: [DONE]`, and
the connection closes. The chat prompt uses the model's built-in template through `llama_chat_apply_template`, with
`chatml` as the fallback.

## Access control, limits, lifecycle

* **Peer uid** (`SO_PEERCRED`, on every connection) is allowed if it is:
  * 0 or 2000;
  * the GbGpu appId, any user;
  * an appId of 10000 or more listed in `/data/vendor/gb/compute_allow`, any user. The list is re-read when its
    mtime changes.

  Everyone else gets `403` and the connection is closed. The service runs as nobody and cannot read
  `packages.list`, so the GbGpu appId is the owner of `/data/data/gb.sb3.gpu`, and it counts only while
  `/odm/app/GbGpu/GbGpu.apk` exists (see Spec question Q1).
* **Limits:**
  * one model;
  * `n_ctx` 4096. `ctx=<512..8192>` in `/data/vendor/gb/compute/gb_gpu_compute.conf` changes it; requests cannot;
  * all layers on the dGPU. There is no CPU fallback: no Vulkan GPU device means `503`;
  * 4 CPU threads;
  * one generation in flight, at most 3 queued, then `429`;
  * at most 4 client connections; the 5th gets `429` and is closed;
  * request body up to 1 MiB, else `413`;
  * model file up to 3584 MiB, else `413`;
  * 600 s per request; the generation stops with `finish_reason:"length"`;
  * a model load needs `MemAvailable` of at least min(model size, 1 GiB) + 256 MiB, else `503`;
  * 30 s receive / keep-alive timeout;
  * an 8 MiB output cap for a client that does not read;
  * `RLIMIT_DATA` 8 GiB, `RLIMIT_NOFILE` 256, no core dumps, `PR_SET_NO_NEW_PRIVS`;
  * `oom_score_adjust 800` (rc).
* **Power hold:**
  * before a model is loaded the service sends `compute-state loading` and requires `hold:1`. Without gb_power, or
    if gb_power refuses, the load fails with `503`;
  * it then waits up to 3 s for the dGPU's `power/control` to read `on`;
  * states sent: `loaded` after the load, `serving` around each request, `unloading`/`idle` on unload;
  * if the gb_power connection is lost, the service reconnects every 2 s in every state (v1.1; v1 only while a hold
    was wanted) and re-sends its state. If the hold stays lost for 10 s, it unloads (VRAM must not be evicted under a
    model on k13, plan A2);
  * keep-alive (v1.1, INTERFACES 9.1/11.5): the current state is re-sent every 15 s on the live connection.
    gb_power exempts the compute-svc connection from its 30 s idle close (the v1 daemon closed it, dropping the hold
    of a loaded model about every 30 s; review R1-H1); the keep-alive also keeps the connection against a stricter
    server (GC-08i).
* **Exits and unloads:**
  * **idle exit:** 120 s with nothing in flight or queued and no client connection: unload, `idle`, exit 0;
  * **screen off:** every i915 eDP `dpms` reads `Off` for 30 s and no request has run for 30 s: unload, keep running
    (D16; disabled when there is no eDP `dpms` attribute);
  * **SIGTERM** (gb_power before detach or off, `gb_nouveau.sh off`): stop accepting, abort the generation, answer
    queued requests `503`, unload, `idle`, `_exit(0)`. There is a hard limit of 4.5 s.
* **Exit codes:** 0 normal, 1 error, 3 `NO_DEVICE`, 4 configuration (no init socket).

### Using it on the device (once Wave 2 has shipped it; hardware only)

1. `adb push model.gguf /data/vendor/gb/models/<id>.gguf`. The directory is 0775 root:shell, so the shell user can
   write it.
2. Ask gb_power to start the service: `compute-start` on `/dev/socket/gb_power` (state must be `standby` or
   `busy:N`), or use the GbGpu app.
3. As shell: `printf 'GET /v1/models HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n' | toybox nc -U /dev/socket/gb_gpu_compute`.
   Apps use `android.net.LocalSocket` (FILESYSTEM namespace) and must be in `compute_allow` (gb_power
   `compute-allow <pkg>`).

## Build (builder `192.168.115.132`, scratch `/build/native/dgpu/gpu-compute`)

```
# from Windows: copy this folder to $W/src (tar over ssh), then on the builder:
bash src/scripts/10_fetch_llama.sh        # blobless clone + checkout of b11552, verifies tag -> commit, clean tree
bash src/scripts/15_headers.sh            # $W/inc: Vulkan-Headers 1.3.275 (libvulkan-dev) + SPIRV-Headers (spirv-headers)
bash src/scripts/bg.sh build_host bash src/scripts/20_build_host.sh        # glibc test build  -> $W/b-host
bash src/scripts/bg.sh build_android bash src/scripts/30_build_android.sh  # NDK r27c, API 35   -> $W/out-android
bash src/scripts/50_run_tests.sh          # all builder tests, logs in $W/logs/tests
bash src/scripts/60_repro.sh              # GC-10: second build elsewhere, byte comparison
```

* The builds wait for a 1-min load below 2 and no running `gbiso` job (INTERFACES 1.4). They run at `nice -n 19
  ionice -c3` with `-j2` and need at least 6 GB free on `/` and `/build`.
* NDK settings: `ANDROID_STL=c++_static`, `-Wl,-z,max-page-size=16384`, `-ffile-prefix-map` for every source and
  build directory, `SOURCE_DATE_EPOCH` = the pinned commit time.
* llama.cpp settings:
  * `GGML_VULKAN=ON`, `BUILD_SHARED_LIBS=OFF`, `GGML_OPENMP=OFF` (`libomp.so` is not LLNDK), `GGML_NATIVE=OFF`;
  * CPU flags SSE4.2/AVX/AVX2/FMA/F16C/BMI2 (i7-1065G7);
  * no `common`, tools, server or curl.
* `ggml-vulkan`'s `find_package(Vulkan)` resolves to `add_library(Vulkan::Vulkan ALIAS gb_nvk_loader)`. Neither build
  links any `libvulkan`.
* Shaders: Ubuntu `glslc` (shaderc 2023.8, glslang 14) through ggml's `vulkan-shaders-gen`. Only
  `GL_KHR_cooperative_matrix` is supported by that glslc; see Later work.
* Builder packages added for this component (Ubuntu archive): `spirv-headers` (ggml-vulkan needs `spirv.hpp` and
  its CMake config) and `python3-numpy` (gguf-py, tests only).
* Builder scratch kept after Wave 1 (404 MB): `src/`, `llama.cpp/` (pinned, clean), `inc/`, `models/` (synthetic test
  GGUFs), `inputs/` (component 4's 8a ICD and noop drm-shim, copied for GC-09), `out-android/` (with `unstripped/`
  for symbolising tombstones), and `logs/`. The build directories and test roots were deleted. A rebuild is
  `15_headers.sh` + `30_build_android.sh`, about 15 minutes at `-j2`.
* `gb_gpu_compute --probe` (diagnostic, also used by GC-02/GC-09) initialises the Vulkan device and a ggml backend,
  then runs a 64x8x256 `mul_mat` against a CPU reference. Exit codes: 0 correct, 5 wrong result (expected under the
  noop drm-shim), 3 no device. It holds no model and asks gb_power for no hold. On hardware it is a quick NVK
  sanity check (HW).

## Tests (all on the builder, none on the GPU)

`50_run_tests.sh` runs these; `60_repro.sh` runs GC-10. The results with real output are in
`C:\GB-build\native\dgpu\integration\gpu-compute.md` section 3, and the logs are under `out\logs\`.

| Id | What |
|---|---|
| GC-01 | `check_elf.py`: ELF64 x86-64, 16 KiB `PT_LOAD` alignment, no TEXTREL, static libc++, no `libvulkan*` NEEDED, SONAMEs, `DT_RUNPATH=$ORIGIN`, stub exports all 215 core commands and only `vk*`, service exports only `gb_gpu_compute_main`. Plus component 4's `import_check.py` against the lapis 276.R4 rootfs: NEEDED within LLNDK + siblings, every import resolves. |
| GC-02 | `test_loader` (all core symbols; two live instances, device, queue, command buffer, fill + read-back through the exported trampolines on lavapipe; proc-addr routing; no layers; a missing ICD gives `VK_ERROR_INCOMPATIBLE_DRIVER`). llama.cpp `test-backend-ops -b Vulkan0` through the stub on lavapipe: 1818 element-wise/norm/rope/softmax/glu cases, 23 `MUL_MAT` f16/q4_0/q8_0/q4_K/q5_K/q6_K cases and 70 `FLASH_ATTN_EXT` f16 hs 64/128 cases. `gc_probe` (service `--probe`: device + backend + correct `mul_mat`). |
| GC-03..08 | `test_service.py` against the host build of the same sources with lavapipe. It runs under sudo, so that the service runs as uid 9999 with an init-style inherited socket, and clients use several real uids. A mock gb_power reproduces gb_power's exact reply lines. Covered: health/models/chat/completions (stream and not), stop strings, reproducible seeded sampling, the error and limit cases, the peer-credential matrix, queue/connection limits, client-abort, unload, gb_power sequence and hold, gb_power loss and return, the v1.1 keep-alive against an exempting and a strict idle-closing gb_power (GC-08h/i), EPERM, memory check, idle exit, SIGTERM, screen-off unload, no TCP (fd/`/proc/net` and strace). |
| GC-09 | Android build in a bionic chroot of lapis 276.R4 with the shipped NVK 8a ICD and component 4's noop drm-shim as TU117 (`NOUVEAU_CHIPSET=167`): `gc_probe` finds the NVK device through the stub, creates the VkDevice/ggml backend, and compiles (NAK) and submits a `mul_mat`. The result is wrong, as expected, because the noop shim runs nothing. |
| GC-10 | Two Android builds in different directories give byte-identical stripped and unstripped files. |
| GC-11 | The launcher refuses with exit 3 for gb_hw=vm, no nouveau node or no payload (host and bionic). Without a usable ICD/device the service answers `503` (no CPU fallback). The VMware run itself is Wave 2. |
| models | The synthetic tiny GGUFs are byte-identical across two generations. |

The test model is generated from the pinned tree only: gguf-py plus its bundled `models/ggml-vocab-llama-spm.gguf`,
random weights (n_embd 64, 2 layers), and the EOS output row zeroed so that greedy runs never stop early. No model is
downloaded.

## Hardware-only checks (HW; not run, need the SB3 + base, after P3 + P4 on hardware)

* **Real NVK on TU117:**
  * the service loads a 1-3B Q4 model;
  * `/health` and `/v1/models`;
  * a chat completion;
  * tokens/s compared with CPU and with ANV (iGPU).
* **Power hold:**
  * `power/control` reads `on` while loaded and `auto` after unload/exit;
  * `vendor.gb.compute.state` follows `idle -> loading -> loaded -> serving -> loaded -> unloading -> idle`;
  * no `runtime_status=error`.
* **Memory:**
  * a model over 2 GB resident, then the idle unload, then a reload under memory pressure;
  * no `runtime_status=error`, no VRAM corruption (same greedy output before and after).
* **Detach while serving:** gb_power stops the service (SIGTERM), the generation aborts, exit within 5 s, refcnt
  reaches 0, the detach proceeds.
* **Screen off:** the i915 eDP `dpms` attribute really reads `Off` under drm_hwcomposer when the screen is off (D16
  is INFERRED), and the model is unloaded after 30 s.
* **Soak:** 30-60 min per `platform_profile`, on AC and on battery: tokens/s drift.
* **Shader cache:** the Mesa disk cache in `/data/vendor/gb/compute/cache` makes the second start faster.
* **SELinux:** no denials that matter in `u:r:shell:s0` (permissive): socket connect to gb_power, DRM open, `/data`
  access.
* **Terminal VM reachability:** open question (plan P6). It needs vsock or TCP, and TCP is forbidden.
* **VMware (Wave 2, GC-11):** on the native-smoke boot, `adb shell /vendor/bin/gb_gpu_compute; echo $?` prints 3.

## Deviations from the plan / spec (with reasons)

* **GC-D1 rc:** `setenv MESA_SHADER_CACHE_DISABLE false` was added to the exact 15.3 text. Mesa keeps the disk cache
  off on Android unless this is set (component 4's finding, `nvk-gfxdriver.md` 2.4). The service also sets both
  variables itself if they are missing.
* **GC-D2 GbGpu appId:** taken from the owner of `/data/data/gb.sb3.gpu` when `packages.list` is unreadable, which it
  is for nobody (Q1).
* **GC-D3 queue vs connections:** with 4 connections and one request per connection, at most 1 + 3 requests can be
  in flight. The "429 after the queue" case therefore shows up as the 5th connection's `429`. The queue-full `429`
  is still implemented and tested with the connection cap raised (test build only).
* **GC-D4 timeouts not in the spec:** 30 s receive/keep-alive timeout, 10 s hold-loss grace, 3 s `power/control`
  wait, 4.5 s hard shutdown limit, 8 MiB output cap.
* **GC-D5 timeout result:** a request that reaches 600 s returns what it has with `finish_reason:"length"`, not an
  error.
* **GC-D6 single library:** llama.cpp and ggml are linked statically into `libgb_gpu_compute.so`, so the payload
  holds two libraries and no further `.so` files.
* **GC-D7 headers:** both builds use Ubuntu's Vulkan-Headers 1.3.275 (with `vulkan.hpp`) and SPIRV-Headers, not the
  NDK sysroot's Vulkan headers, so that the host and device builds compile identical code.

## Later work

* **Faster quantised matmuls on Turing:** a newer shaderc/glslang (for example AOSP `external/shaderc` at a pinned
  tag) would add the `GL_EXT_integer_dot_product` (DP4A) shaders. Ubuntu's glslc 2023.8 lacks it, and so do the
  NDK's shader-tools (shaderc 2022.3, which lacks even cooperative matrix).
* **Rebase:** move to a newer llama.cpp tag and to Mesa 26.3.0 final together with component 4.
