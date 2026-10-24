#!/usr/bin/env python3
"""Applies the two nvk-android source changes to a pristine mesa-26.3.0-rc2 tree (idempotent) and writes .orig
copies + unified diffs. Usage: python3 -I apply_patches.py <mesa_src_dir> <patch_out_dir>

0001: NVK ANB import: reject foreign (non-LINEAR, non-NVIDIA) gralloc modifiers with VK_ERROR_INVALID_EXTERNAL_HANDLE
      instead of letting NIL panic (abort the process) in BlockLinearModifier::try_from().unwrap().
0002: allow building NVK without libelf (only VK_NVX_binary_import cubin entrypoints use it; NVK 26.3 does not
      advertise that extension, and libelf is not available for Android in the NDK).
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
  # libelf is only used by the NVK cubin (VK_NVX_binary_import) entrypoints, which NVK does not advertise.
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

nvk_include_directories = [""")]


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
