#!/usr/bin/env python3
"""Applies the two nvk-android source changes to a pristine mesa-26.3.0-rc2 tree (idempotent) and writes .orig
copies + unified diffs. Usage: python3 -I apply_patches.py <mesa_src_dir> <patch_out_dir>

0001: NVK ANB import: reject foreign (non-LINEAR, non-NVIDIA) gralloc modifiers with VK_ERROR_INVALID_EXTERNAL_HANDLE
      instead of letting NIL panic (abort the process) in BlockLinearModifier::try_from().unwrap().
0002: allow building NVK without libelf (only the VK_NVX_binary_import cubin entrypoints in nvk_cubin.c use it, and
      libelf is not available for Android in the NDK). NVK 26.3 advertises VK_NVX_binary_import only when
      NVK_EXPERIMENTAL=dlss is set (on Android also via the property debug.mesa.nvk.experimental /
      vendor.mesa.nvk.experimental / mesa.nvk.experimental, see util/os_misc.c). A build without nvk_cubin.c must
      therefore also stop advertising it (-DNVK_NO_CUBIN), otherwise the extension would be listed while
      vkCreateCuModuleNVX & co. resolve to NULL. (Verification fix, nvk-verify 2026-10-24.)
"""
import difflib
import os
import shutil
import sys

src, out = sys.argv[1], sys.argv[2]

P1 = [("src/nouveau/vulkan/nvk_image.c",
       """      if (result != VK_SUCCESS)
         return result;

      image->vk.drm_format_mod = eci.drmFormatModifier;
""",
       """      if (result != VK_SUCCESS)
         return result;

      /* gralloc (e.g. minigbm on an Intel iGPU) may hand us a buffer with a
       * foreign tiling such as I915_FORMAT_MOD_Y_TILED.  NVIDIA hardware can
       * only address LINEAR or its own block-linear layouts and NIL would
       * panic (abort the process) on any other modifier, so fail the import
       * cleanly instead.
       */
      if (eci.drmFormatModifier != DRM_FORMAT_MOD_LINEAR &&
          eci.drmFormatModifier != DRM_FORMAT_MOD_INVALID &&
          !fourcc_mod_is_vendor(eci.drmFormatModifier, NVIDIA)) {
         return vk_errorf(dev, VK_ERROR_INVALID_EXTERNAL_HANDLE,
                          "gralloc buffer has DRM modifier 0x%016llx; NVK "
                          "can only import LINEAR or NVIDIA modifiers",
                          (unsigned long long)eci.drmFormatModifier);
      }

      image->vk.drm_format_mod = eci.drmFormatModifier;
""")]

P2 = [("meson.build",
       """elif with_nouveau_vk
  error('NVK requires libelf')
endif""",
       """elif with_nouveau_vk
  # libelf is only used by the NVK cubin (VK_NVX_binary_import) entrypoints. Without it nvk_cubin.c is not built
  # and src/nouveau/vulkan/meson.build defines NVK_NO_CUBIN so the extension is never advertised.
  warning('libelf not found: building NVK without the cubin (VK_NVX_binary_import) entrypoints')
endif"""),
      ("src/nouveau/meson.build",
       """if with_nouveau_vk
  subdir('cubin')
  subdir('mme')""",
       """if with_nouveau_vk
  if dep_elf.found()
    subdir('cubin')
  endif
  subdir('mme')"""),
      ("src/nouveau/vulkan/meson.build", """  'nvk_cubin.c',
  'nvk_cubin.h',
""", ""),
      ("src/nouveau/vulkan/meson.build", """  idep_nouveau_cubin,
  idep_nouveau_mme,""", """  idep_nouveau_mme,"""),
      ("src/nouveau/vulkan/meson.build", """nvk_include_directories = [""", """if dep_elf.found()
  nvk_files += files('nvk_cubin.c', 'nvk_cubin.h')
  nvk_deps += idep_nouveau_cubin
endif

nvk_include_directories = ["""),
      ("src/nouveau/vulkan/meson.build", """nvk_flags = []
""", """nvk_flags = []

# Built without libelf (nvk_cubin.c left out): VK_NVX_binary_import must not be advertised.
if not dep_elf.found()
  nvk_flags += '-DNVK_NO_CUBIN=1'
endif
"""),
      ("src/nouveau/vulkan/nvk_physical_device.c",
       """      .NVX_binary_import = info->cls_eng3d >= MAXWELL_A &&
         instance->experimental_flags & NVK_EXPERIMENTAL_DLSS,
""", """#ifndef NVK_NO_CUBIN
      .NVX_binary_import = info->cls_eng3d >= MAXWELL_A &&
         instance->experimental_flags & NVK_EXPERIMENTAL_DLSS,
#endif
""")]


def apply(name, edits):
    """Idempotent: every file is rebuilt from its pristine .orig copy with all of its edits."""
    files = []
    for rel, _, _ in edits:
        if rel not in files:
            files.append(rel)
    diff = []
    for rel in files:
        p = os.path.join(src, rel)
        if not os.path.exists(p + ".orig"):
            shutil.copy2(p, p + ".orig")
        base = open(p + ".orig").read()
        s = base
        for r, old, new in edits:
            if r != rel:
                continue
            if s.count(old) != 1:
                raise SystemExit("%s: pattern not found exactly once in %s" % (name, rel))
            s = s.replace(old, new)
        open(p, "w").write(s)
        diff += difflib.unified_diff(base.splitlines(True), s.splitlines(True), "a/" + rel, "b/" + rel)
    open(os.path.join(out, name), "w").write("".join(diff))
    print("%s: %d file(s), %d diff lines" % (name, len(files), len(diff)))


# 0001 and 0002 touch disjoint files, so each diff is relative to pristine 26.3.0-rc2.
apply("0001-nvk-android-reject-foreign-gralloc-modifiers.patch", P1)
apply("0002-nvk-allow-building-without-libelf.patch", P2)
