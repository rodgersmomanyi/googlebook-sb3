# nvk-android (native, shipped inactive)

Mesa **NVK** (Vulkan for NVIDIA on nouveau) cross-built for Android x86_64, to see whether the TU117 dGPU can do any
useful Vulkan work under lapis. Full notes, including an independent re-verification of the build:
[INTEGRATION_NOTES.txt](INTEGRATION_NOTES.txt).

**Why it is inactive:** Android's Vulkan loader loads exactly one system Vulkan HAL, chosen by
`ro.hardware.vulkan`. lapis sets it to `intel` (ANV on i915) and has no `ro.vulkan.apex`, so libvulkan never opens
`vulkan.nouveau.so`. NVK is therefore installed inert (`/vendor/lib64/hw/vulkan.nouveau.so`) and used only by the
standalone probe. Switching the system driver to NVK would move SurfaceFlinger's Vulkan RenderEngine and HWUI onto
the dGPU, which has no display outputs; that is not attempted.

* `scripts/`: `00_env.sh` (common settings), `10_build_host_clc.sh` (host `mesa_clc` / `vtn_bindgen2` from the same
  Mesa), `20_build_nvk_android.sh` (NDK + Rust cross build), `30_build_probe.sh`, builder-only tests
  (`35_build_drm_shim_test.sh`, `40_bionic_chroot_test.sh`), `apply_patches.py`, the cross file.
* `patches/`: two Mesa changes (reject foreign gralloc DRM modifiers instead of panicking; build without libelf, which
  also stops advertising `VK_NVX_binary_import` once its entrypoints are compiled out) and one TEST-ONLY drm-shim
  patch. MIT (Mesa).
* `src/nvk_probe.c` + shaders: loads an ICD directly, renders offscreen and into gralloc (AHardwareBuffer) images,
  each test in a forked child.
* `verify/`: `hmi_check.c` re-implements what lapis's libvulkan does with a HAL module (HMI lookup, id check,
  dispatch magic); `chroot_run.sh` runs Android binaries in a read-only chroot of the lapis system image on the
  builder.
* `nvk-android.json` + `gb_nvk.rc`: the component manifest (installed as `inject/native/components/nvk.json`) and the
  on-demand probe service (`setprop vendor.gb.nvk_probe 1`, results in `/data/vendor/nvk/`).

Needs the opt-in nouveau from `../nouveau-kernel`. Status: compile-tested and partly run on the builder (lavapipe,
bionic chroot with drm-shim; no NVIDIA GPU there); nothing ran on the Surface Book 3.
