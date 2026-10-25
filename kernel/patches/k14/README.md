> **Repository note.** In this repository the build script is `kernel/scripts/kbuild14_native.sh`, the review
> notes are `kernel/K14.md` and the Wave 2 staging notes are `native/dgpu/integration/k14-kernel.md`. The
> `evidence/` copy of the acceptance run is not published. `C:\GB-build\kernel\...` below is the
> development layout of the same files.

# k14 kernel: nouveau backports and hardening (component `k14`)

Kernel build #14 for Googlebook OS on the Surface Book 3: k13 plus nouveau-only changes. Nothing outside
`drivers/gpu/drm/nouveau/` changes, so the k14 `.config` and `vmlinux.symvers` equal k13's. Plan phase 4,
`INTERFACES.md` section 18.3. Review notes and the patch table are in `C:\GB-build\kernel\K14.md`. Wave 2 staging is in
`C:\GB-build\native\dgpu\integration\k14-kernel.md`.

## Contents

| Path | What |
|---|---|
| `0001`-`0037-*.patch` | The 37 gregkh `linux-6.18.y` commits between v6.18.21 and v6.18.57 that touch `drivers/gpu/drm/nouveau/`, in stable-branch order. The range has 38 such commits; `drm/client: Remove holds_console_lock parameter from suspend/resume` (0589706ace3b) is left out because it changes DRM core. `0022` has a one-line backport fix (`kzalloc_obj()` takes an explicit GFP argument in android17-6.18). The other 36 are unmodified `git format-patch` output of the stable commits. |
| `0101-*.patch` | N1: no `pci_ignore_hotplug()` when the upstream bridge has `_PR3` and pciehp masks its interrupt across D3cold. |
| `0102-*.patch` | N2: presence guards. Resume returns `-ENODEV` without touching a GPU that left the bus; remove/fini mark the nvkm device gone, so GSP-RM waits stop at once. |
| `0103-*.patch` | N3: a failed runtime suspend returns `-EBUSY` before powering off. A failed VRAM eviction fails runtime suspend, and SVM/dmem/LED suspend steps are undone on failure. |
| `SHA256SUMS` | sha256 of the 40 patches. The build refuses a patch set that does not match. |
| `tools/k14check.py` | Acceptance checker (python3 -I, stdlib only), with subcommands `sig` (K-05), `unresolved` (K-04) and `crc` (K-06). |
| `tools/k14_accept.sh` | Runs K-01 to K-09 on the builder and writes evidence to `/build/k14/accept/`. |
| `tools/test_kbuild14_state.sh` | Host test of the build script's source-tree handling (save, branch, am, restore) against a throw-away repository. |
| `tools/extract_lapis.sh` | Extracts the lapis vendor_ramdisk (all concatenated cpio archives) and vendor_dlkm (erofs) modules for K-06. |
| `evidence/` | Copy of `/build/k14/accept/` from the Wave 1 run: `summary.txt` and one file per check. |
| `../../scripts/kbuild14_native.sh` | The build script (copy of `kbuild13_native.sh` with a patch step, `BUILD_NUMBER=16302714`, `--destdir=/build/dist14`). |

## How the build works

`/build/gki/common` holds k13 as uncommitted changes on a detached HEAD (`3938c8d97142`). `kbuild14_native.sh`:

1. Runs the preflight checks:
   * no `/var/tmp/gbiso` process;
   * `/build` has at least 10 GB free and `/` at least 5 GB;
   * `SHA256SUMS` matches;
   * every `diff --git` path is under `drivers/gpu/drm/nouveau/` (K-01, patch files);
   * then it waits until the 1-minute load is below 2.
2. Saves the k13 state to `/build/k14/k13-state/`: HEAD, `git status`, a binary diff, the untracked list with sha256, and a tar of the untracked files.
3. Records the k13 working tree as commit `k13-native`, through a temporary index, so the working tree and the real index are not touched. The identity and dates are fixed, so the commit id is reproducible.
4. Moves HEAD to branch `k14-nouveau` (= `k13-native`) without touching files. It then runs `git am -3 --committer-date-is-author-date` on the 40 patches and checks K-01 on every commit. It also checks that `gki_defconfig` and `BUILD.bazel` are unchanged.
5. Runs a Kleaf build at `nice -n 19 ionice -c3` with `--config=stamp --config=local //common:kernel_x86_64_dist -- --destdir=/build/dist14`. It reuses the k13 `--config=local` cache, so only nouveau and the relinks are rebuilt.
6. Always restores the original detached HEAD and the uncommitted k13 working tree, through an EXIT trap as well, and proves the result identical to the saved state (K-09).
7. Writes `/build/dist14/kernel_release.txt` and compares `.config` and `vmlinux.symvers` with `/build/dist`.

`/build/dist` (k13) is only read. Log: `/build/kbuild14.log`. Markers: `/build/kbuild14.{done,failed}`.

## Build

From Windows (Git Bash), copy the inputs to the builder and start the build:

```sh
K="-i C:/GB-build/builder_key -o StrictHostKeyChecking=no"; B=builder@192.168.115.132
ssh $K $B 'mkdir -p /build/k14/patches /build/k14/tools'
scp $K C:/GB-build/kernel/patches/k14/*.patch C:/GB-build/kernel/patches/k14/SHA256SUMS $B:/build/k14/patches/
scp $K C:/GB-build/kernel/patches/k14/tools/* $B:/build/k14/tools/
scp $K C:/GB-build/kernel/kbuild14_native.sh $B:/build/k14/
ssh $K $B 'bash /build/k14/kbuild14_native.sh --preflight && (nohup setsid bash /build/k14/kbuild14_native.sh >/dev/null 2>&1 </dev/null &)'
```

`--restore` puts the k13 working tree back after an interrupted run. If the tree is already back, it only verifies
it. A re-run rebuilds and replaces `/build/dist14`. The k14 build took 2054 s of Kleaf time (about 35 minutes at nice
19). That included recompiling all of nouveau, because N2 changes `nvkm/core/device.h`; the rest came from the k13
cache. The build result, kernel release and test results are in `K14.md`.

## Tests (no hardware)

On the builder, after `/build/kbuild14.done`:

```sh
bash /build/k14/tools/k14_accept.sh          # K-01..K-09, evidence in /build/k14/accept/
bash /build/k14/tools/test_kbuild14_state.sh /build/k14/kbuild14_native.sh   # 17 state-handling cases
shellcheck -s bash /build/k14/kbuild14_native.sh /build/k14/tools/*.sh
```

The CRC check (K-06) needs the lapis modules extracted to `/build/k14/lapis/`:

* `vr/a2/lib/modules`: 59 modules from the third cpio archive of `C:\GB-x86\boot\vendor_ramdisk.lz4`;
* `vdlkm_b276`: 484 modules, from `fsck.erofs --extract` of `/build/native/audio/vendor_dlkm_b276.img` (276.R4);
* `vdlkm_a`: 484 modules, from `vendor_dlkm_a.img` (271.R1).

`tools/extract_lapis.sh` recreates these directories (`K14.md` section 4).

| Id | Check | How |
|---|---|---|
| K-01 | Every patch and every commit touches only `drivers/gpu/drm/nouveau/` | build script, preflight and per-commit; `k14_accept.sh` again on the branch |
| K-02 | `.config` k13 vs k14 | `diff` of `kernel_x86_64_dot_config` |
| K-03 | `vmlinux.symvers` k13 vs k14 | `diff` (also all module exports, informational) |
| K-04 | 0 unresolved symbols for the nouveau set and its depends closure | `k14check.py unresolved` |
| K-05 | Every `.ko` signed by the bzImage's built-in key | `k14check.py sig`: the certificate comes from `vmlinux` and is found inside the decompressed bzImage. Every signature is verified with `openssl dgst -verify`. Also reports whether k13 and k14 share the key; they do, because the `--config=local` cache keeps it. |
| K-06 | lapis vendor_ramdisk and vendor_dlkm modversion CRCs against k14 | `k14check.py crc`; the result must equal k13's |
| K-07 | N1-N3 reviewed against mainline 7.3 | `K14.md`; the three patches also apply unchanged to v7.3 `drivers/gpu/drm/nouveau` |
| K-08 | `kernel_release.txt` matches the bzImage banner and the nouveau vermagic, and differs from k13 | `k14_accept.sh` |
| K-09 | k13 working tree restored | `kbuild14_native.sh --restore` and the saved state |
| K-10 | VMware boot of k14, then the native image builds | Wave 2, needs the user's approval |

## Hardware-only checks (HW, not run)

All of these need the Surface Book 3 with its base (plan Phase 4, INTERFACES K-HW):

* Repeat Phase 2B steps 1-7 on k14 and compare with k13: D3cold residency, resume time, PM stress (`runtime_status=error`, WARN, `-110` counts), suspend and reboot.
* 20 suspend/resume cycles idle and 5 with `nvk_probe` running.
* 10 reboots and 10 power-offs, timed, with nouveau bound.
* N1 preconditions. dmesg must show the OS controlling `PCIeHotplug` and `PME` in `_OSC` for the host bridge, and `nouveau ... runtime PM through ACPI _PR3, hotplug left enabled` after the first runtime suspend. Without that line N1 is inactive and behaviour equals k13.
* N1: after a runtime suspend, detach and attach are handled by pciehp without the manual rescan. Also, runtime suspend/resume cycles must cause no `pciehp ... Link Down` / `Card not present` lines.
* N2: no deliberate surprise-removal test (plan). It is verified by code review and the Phase 3 gb_power cases.
* N3: not forced on hardware. Watch for `runtime suspend failed (...), staying in D0` and `failed to evict VRAM` in the PM stress logs. Each is expected 0 times.
