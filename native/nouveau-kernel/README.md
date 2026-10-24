# nouveau-kernel (native, opt-in)

nouveau for the NVIDIA GTX 1650 Max-Q (TU117) in the performance base. **Off by default**: the dGPU stays
driverless and powered down by `gb_power`, which is the right state for battery life; Android has no NVIDIA
userspace driver (see `../nvk-android` for the Vulkan experiment).

* Kernel: build #13 has `CONFIG_DRM_NOUVEAU=m` (+ `drm_exec`, `drm_gpuvm`, `gpu-sched`, `mxm-wmi`;
  `kernel/scripts/kbuild13_native.sh`). `kernel/gki_defconfig.nouveau.fragment`, `kernel/kleaf_add_nouveau.py` and
  `kernel/kleaf_module_paths.txt` are the stand-alone, idempotent form of the same change. `stage_kernel.py` stages
  the modules (`NATIVE_OPTIN`) but puts them in no load list.
* `native/gb_nouveau.sh` + `gb_nouveau.rc` (in the default native tree via `inject/native/components/nouveau.json`):
  `setprop persist.vendor.gb.nouveau 1` loads it after boot_completed, now and at every later boot (`runpm=1
  modeset=2`, headless, i915 stays the display GPU); `0` unloads it and restores runtime PM.
  `gb_dgpu_check.sh`: read-only power-state report.
* `coordination/gb_power-nouveau-optin.patch`: the change `gb_power` needed so it stops unbinding nouveau. It is now
  merged into `../power/gb_power.c` (kept here for reference, with the pre-merge copy under `coordination/gb_power/`):
  with the opt-in, a nouveau-bound dGPU stays bound, and it is still unbound before any base detach is confirmed.
* `firmware/fetch_tu117_firmware.sh`: fetches the TU117 GSP/firmware files from linux-firmware (not included).
* `build/compile_test.sh`: out-of-tree compile test against the GKI tree.

Status: opt-in; compile-tested; untested on hardware.
