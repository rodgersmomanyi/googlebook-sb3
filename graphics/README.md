# Graphics (VM)

The VM renders on the host GPU through VMware SVGA3D. Natively none of this is used: lapis's own Intel stack drives
i915.

| Piece | Where | Result in the workspace |
|---|---|---|
| Kernel: vmwgfx fixes (RGBA dumb buffers, dma-buf import, leaks) | [../kernel/patches/0001-*](../kernel/patches/) | `inject/modules/vmwgfx.ko` |
| Mesa gallium `svga` EGL/GLES for Android x86_64 (NDK r27c, API 35, mesa-26.3.0-rc2, static libdrm) | [mesa/build_mesa.sh](mesa/build_mesa.sh) + [mesa/patches/0001-*.patch](mesa/patches/) (apply in the Mesa tree before configuring) | `inject/mesa/libEGL_mesa.so`, `libGLESv1_CM_mesa.so`, `libGLESv2_mesa.so`, `libgallium_dri.so`, `egl_probe`; create `inject/mesa/ENABLE_SKIAGL` to make it the system driver |
| minigbm for vmwgfx | [minigbm/patch_minigbm.py](minigbm/patch_minigbm.py): patches **your** lapis `libminigbm_gralloc.so` / `libgbm.so` | `inject/vendor_gfx/` |
| SwiftShader Vulkan HAL (`vulkan.pastel.so`) | [swiftshader/](swiftshader/): build script, CMake patchers, notes | `inject/vulkan.pastel.so` |
| Probes | [mesa/probes/](mesa/probes/): `egl_probe` (Mesa EGL bring-up), `egl_winprobe` (window surfaces via the system loader), `ahb_leak` (gralloc memory leaks), `ahb_stride_probe` (odd-width AHB sampling) | run from `/data/local/tmp` |

`mesa/patch_svga_shader_cache.py` is the first form of the shader-cache change (SHA-1, Mesa 26.2); the patch file
carries the version used with 26.3 (BLAKE3).

How it fits together: gralloc (minigbm, komeda backend renamed to vmwgfx) allocates dumb buffers; with the patched
vmwgfx they are R8G8B8A8 surfaces that Mesa svga imports through dma-buf (DRM_VMW_GB_SURFACE_REF_EXT). SurfaceFlinger
and HWUI run SkiaGL on Mesa (`ro.hardware.egl=mesa`); drm_hwcomposer scans out on vmwgfx's primary plane, with
Android's pointer on the cursor plane.
