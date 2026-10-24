# nvk-android (experimental)

Mesa **NVK** (Vulkan for NVIDIA on nouveau) cross-built for Android x86_64, to see whether the TU117 dGPU can do any
useful Vulkan work under lapis. Android's loader only loads the one system Vulkan HAL (ANV on i915), so NVK is
installed inert (`/vendor/lib64/hw/vulkan.nouveau.so`) and tested standalone. Full notes:
[INTEGRATION_NOTES.txt](INTEGRATION_NOTES.txt).

* `scripts/`: `00_env.sh` (common settings), `10_build_host_clc.sh` (host `mesa_clc` / `vtn_bindgen2` from the same
  Mesa), `20_build_nvk_android.sh` (NDK + Rust cross build), `30_build_probe.sh`, builder-only tests
  (`35_build_drm_shim_test.sh`, `40_bionic_chroot_test.sh`), the cross file.
* `patches/`: two Mesa changes (reject foreign gralloc DRM modifiers instead of panicking; build without libelf) and
  one TEST-ONLY drm-shim patch. MIT (Mesa).
* `src/nvk_probe.c` + shaders: loads an ICD directly, renders offscreen and into gralloc (AHardwareBuffer) images,
  each test in a forked child.
* `nvk-android.json` + `gb_nvk.rc`: a native-tree manifest in the components format and an on-demand probe service
  (`setprop vendor.gb.nvk_probe 1`); not part of `inject/native/components`, so not in the default build.

Needs the opt-in nouveau from `../nouveau-kernel`. Status: experimental; compile-tested and partly run on the builder
(lavapipe, bionic chroot; no NVIDIA GPU there); nothing ran on the Surface Book 3.
