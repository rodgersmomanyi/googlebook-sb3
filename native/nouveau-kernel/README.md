# nouveau-kernel (experimental, opt-in)

nouveau for the NVIDIA GTX 1650 Max-Q (TU117) in the performance base. **Not in the default build**: by default the
dGPU stays driverless and powered down by `gb_power`, which is the right state for battery life; Android has no
NVIDIA userspace driver (see `../nvk-android` for the Vulkan experiment).

* `kernel/gki_defconfig.nouveau.fragment`, `kernel/kleaf_add_nouveau.py`, `kernel/kleaf_module_paths.txt`: add
  `CONFIG_DRM_NOUVEAU=m` (+ `drm_exec`, `drm_gpuvm`, `gpu-sched`, `mxm-wmi`) to the Kleaf build, idempotently.
* `native/gb_nouveau.sh` + `gb_nouveau.rc`: `setprop persist.vendor.gb.nouveau 1` loads it after boot_completed
  (`runpm=1 modeset=2`, headless, i915 stays the display GPU); `0` unloads it and restores runtime PM.
  `gb_dgpu_check.sh`: read-only power-state report.
* `coordination/gb_power-nouveau-optin.patch`: `gb_power` must stop unbinding drivers from the dGPU when nouveau is
  opted in (unpatched it tears nouveau down within 0.5 s, by design).
* `firmware/fetch_tu117_firmware.sh`: fetches the TU117 GSP/firmware files from linux-firmware (not included).
* `build/compile_test.sh`: out-of-tree compile test against the GKI tree.

Status: experimental; compile-tested only.
