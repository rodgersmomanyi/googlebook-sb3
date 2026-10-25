#!/usr/bin/env python3
"""Applies the nvk-android source changes to a pristine mesa-26.3.0-rc2 tree (idempotent) and writes .orig copies +
unified diffs.

Usage: python3 -I apply_patches.py [--set base|8a|8b] [--check] <mesa_src_dir> <patch_out_dir>

  --set base   0001 0002                       (the 2026-10-24 payload ICD, sha256 f7e9154f...; NV-01)
  --set 8a     0001 0002 0004 0005 0006        (default; payload ICD + libgb_nvk.so for the Phase 8a experiment)
  --set 8b     0001 0002 0004 0005 0006 0003   (8a + AHB; compile/API checks only, not shipped)
  --check      apply in memory only: every edit must match exactly once; nothing is written (NV-04)

The series is cumulative and applied in the order above (0003 last: it is only needed for 8b). Each patch file is the
diff from the state after the previous patches of the series, so 0001/0002 stay diffs against pristine 26.3.0-rc2 and
are byte-identical to the 2026-10-24 ones. Every file is rebuilt from its pristine .orig with the edits of the selected
set (and only rewritten when its content changes, so incremental builds stay incremental); files that only patches
outside the set touch are restored from .orig, so switching sets is safe.

0001: NVK ANB import: reject foreign (non-LINEAR, non-NVIDIA) gralloc modifiers with VK_ERROR_INVALID_EXTERNAL_HANDLE
      instead of letting NIL panic (abort the process) in BlockLinearModifier::try_from().unwrap().
0002: allow building NVK without libelf (only the VK_NVX_binary_import cubin entrypoints in nvk_cubin.c use it, and
      libelf is not available for Android in the NDK). NVK 26.3 advertises VK_NVX_binary_import only when
      NVK_EXPERIMENTAL=dlss is set (on Android also via the property debug.mesa.nvk.experimental /
      vendor.mesa.nvk.experimental, see util/os_misc.c). A build without nvk_cubin.c must
      therefore also stop advertising it (-DNVK_NO_CUBIN), otherwise the extension would be listed while
      vkCreateCuModuleNVX & co. resolve to NULL. (Verification fix, nvk-verify 2026-10-24.)
0004: force LINEAR for buffers shared with a foreign gralloc (minigbm on the Intel iGPU). NVK detects that the first DRM
      render node is not its own (override: debug.mesa.nvk.anb.linear = 1/0) and then adds
      GRALLOC_USAGE_SW_WRITE_RARELY / GRALLOC1_PRODUCER_USAGE_CPU_WRITE to vkGetSwapchainGrallocUsage(2)ANDROID and
      (through a new runtime switch, vk_android_set_force_linear_ahb) AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY to the AHB
      usage query and AHB allocations - the mechanism vk_android.c already uses for MUTABLE_FORMAT /
      COMPRESSION_DISABLED (vk_image_info_to_ahb_usage). minigbm's i915 backend allocates LINEAR whenever a SW usage bit
      is set (i915.c linear_mask). NVK 26.3 exposes no GrallocUsage3/4, so Android 17's libvulkan uses Usage2 after
      the AHB GPDIFP2 query fails (a17 swapchain.cpp:1764-1880).
0005: set up the LINEAR->tiled shadow (linear_tiled_shadows) for every image with a LINEAR modifier, not only for
      VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT ones. Android swapchain (ANB) images are created with
      VK_IMAGE_TILING_OPTIMAL around a LINEAR gralloc buffer; NVK renders LINEAR targets that are not aligned for
      direct linear rendering through the shadow, which was left zero-sized for them (nvk_image.c:939-1024).
0006: vkQueueSignalReleaseImageANDROID returns a sync_fd (exported from a per-queue SYNC_FD semaphore signalled after
      the wait semaphores and all earlier work on the queue) instead of QueueWaitIdle() + -1. Falls back to the old
      behaviour if the export fails; debug.mesa.nvk.anb.waitidle = 1 forces the old behaviour.
0003: VK_ANDROID_external_memory_android_hardware_buffer for NVK (8b only): a rebase of MR !33700 (head 21cc50e0)
      onto 26.3.0-rc2, structured like Project Churro's nvk-android-hardware-buffer.patch, with these differences:
      deferred AHB images are laid out at bind time by a new nvk_image_init_layout() (nvk_image_init() minus
      vk_image_init(), so the vk_image is not re-initialised - the MR and Churro run the full init twice); the
      deferred create info is freed by vk_image_finish() only (Churro frees it a second time in DestroyImage, a
      double free on 26.3 where vk_image_finish() already frees it); a foreign AHB modifier is rejected like 0001;
      AHB image format properties are limited to 1 mip / 1 layer / 1 sample (LINEAR DRM-modifier layout, as the MR).
"""
import argparse
import difflib
import os
import shutil
import sys

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

# ---------------------------------------------------------------------------------------------------------------------
# 0004: force LINEAR for buffers shared with a foreign gralloc
P4 = [("src/vulkan/runtime/vk_android.h",
       """uint64_t vk_android_get_front_buffer_usage(void);
""",
       """uint64_t vk_android_get_front_buffer_usage(void);

/* Set by a driver that can only share LINEAR buffers with the system gralloc
 * (e.g. NVK when gralloc allocates on another GPU).  From then on the AHB usage
 * query and AHB allocations of this driver add AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY,
 * which gralloc implementations treat as "allocate LINEAR".
 */
void vk_android_set_force_linear_ahb(bool force_linear);
"""),
      ("src/vulkan/runtime/vk_android.h",
       """#else /* defined(VK_USE_PLATFORM_ANDROID_KHR) && ANDROID_API_LEVEL >= 26 */

static inline uint64_t
vk_android_get_front_buffer_usage(void)
{
   return 0;
}
""",
       """#else /* defined(VK_USE_PLATFORM_ANDROID_KHR) && ANDROID_API_LEVEL >= 26 */

static inline uint64_t
vk_android_get_front_buffer_usage(void)
{
   return 0;
}

static inline void
vk_android_set_force_linear_ahb(bool force_linear)
{
   (void)force_linear;
}
"""),
      ("src/vulkan/runtime/vk_android.c",
       """uint64_t
vk_android_get_front_buffer_usage(void)
{
""",
       """/* See vk_android_set_force_linear_ahb() in vk_android.h.  One gralloc per
 * process and one driver per copy of this runtime, so a plain global is enough.
 */
static bool vk_android_force_linear_ahb;

void
vk_android_set_force_linear_ahb(bool force_linear)
{
   vk_android_force_linear_ahb = force_linear;
}

uint64_t
vk_android_get_front_buffer_usage(void)
{
"""),
      ("src/vulkan/runtime/vk_android.c",
       """   if (force_linear)
      ahb_usage |= AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY;
""",
       """   if (force_linear || vk_android_force_linear_ahb)
      ahb_usage |= AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY;
"""),
      ("src/vulkan/runtime/vk_android.c",
       """         usage = vk_image_usage_to_ahb_usage(image->create_flags, image->usage);
         if ((image->compr_flags & VK_IMAGE_COMPRESSION_DISABLED_EXT) &&
             !vk_format_is_depth_or_stencil(image->format))
            usage |= AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY;
""",
       """         usage = vk_image_usage_to_ahb_usage(image->create_flags, image->usage);
         if (vk_android_force_linear_ahb ||
             ((image->compr_flags & VK_IMAGE_COMPRESSION_DISABLED_EXT) &&
              !vk_format_is_depth_or_stencil(image->format)))
            usage |= AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY;
"""),
      ("src/nouveau/vulkan/nvk_physical_device.h",
       """   struct nvk_queue_family queue_families[3];
   uint8_t queue_family_count;
};
""",
       """   struct nvk_queue_family queue_families[3];
   uint8_t queue_family_count;

   /* Android: the system gralloc allocates on another GPU (minigbm on the
    * Intel iGPU), so every buffer NVK shares with it must be LINEAR.
    */
   bool android_foreign_gralloc;
};

/* nvk_android.c, Android builds only */
bool nvk_android_gralloc_is_foreign(const struct nvk_physical_device *pdev);
"""),
      ("src/nouveau/vulkan/nvk_physical_device.c",
       """   *pdev_out = &pdev->vk;

   return VK_SUCCESS;
""",
       """#if DETECT_OS_ANDROID
   pdev->android_foreign_gralloc = nvk_android_gralloc_is_foreign(pdev);
   if (pdev->android_foreign_gralloc)
      vk_android_set_force_linear_ahb(true);
#endif

   *pdev_out = &pdev->vk;

   return VK_SUCCESS;
"""),
      ("src/nouveau/vulkan/nvk_android.c",
       """#include "nvk_private.h"

#include "nvk_entrypoints.h"
#include "vk_android.h"

#include "vk_device.h"
#include "vk_queue.h"
""",
       """#include "nvk_private.h"

#include "nvk_device.h"
#include "nvk_entrypoints.h"
#include "nvk_physical_device.h"
#include "nvkmd/nvkmd.h"
#include "vk_android.h"
#include "vk_common_entrypoints.h"

#include "vk_device.h"
#include "vk_queue.h"

#include "util/os_misc.h"

#include <hardware/gralloc.h>
#if ANDROID_API_LEVEL >= 26
#include <hardware/gralloc1.h>
#endif
#include <sys/stat.h>

/* Is the system gralloc allocator on another GPU than this physical device?
 *
 * Android's gralloc (minigbm on lapis) opens the first DRM render node it can
 * drive; with i915 on renderD128 every gralloc buffer is an i915 buffer whose
 * Intel X/Y tiling NVIDIA hardware cannot address.  Only LINEAR buffers can be
 * shared then.  NVK_ANB_LINEAR (Android property
 * debug.mesa.nvk.anb.linear) = 1 forces "foreign", 0 forces "native".
 */
bool
nvk_android_gralloc_is_foreign(const struct nvk_physical_device *pdev)
{
   const char *opt = os_get_option("NVK_ANB_LINEAR");
   if (opt != NULL && opt[0] == '1')
      return true;
   if (opt != NULL && opt[0] == '0')
      return false;

   struct stat st;
   if (stat("/dev/dri/renderD128", &st) != 0 || !S_ISCHR(st.st_mode))
      return true;

   return pdev->nvkmd->drm.render_dev == 0 ||
          st.st_rdev != pdev->nvkmd->drm.render_dev;
}

VKAPI_ATTR VkResult VKAPI_CALL
nvk_GetSwapchainGrallocUsageANDROID(VkDevice device,
                                    VkFormat format,
                                    VkImageUsageFlags imageUsage,
                                    int *grallocUsage)
{
   VK_FROM_HANDLE(nvk_device, dev, device);

   VkResult result = vk_common_GetSwapchainGrallocUsageANDROID(
      device, format, imageUsage, grallocUsage);
   if (result == VK_SUCCESS && nvk_device_physical(dev)->android_foreign_gralloc)
      *grallocUsage |= GRALLOC_USAGE_SW_WRITE_RARELY;

   return result;
}

#if ANDROID_API_LEVEL >= 26
VKAPI_ATTR VkResult VKAPI_CALL
nvk_GetSwapchainGrallocUsage2ANDROID(
   VkDevice device, VkFormat format, VkImageUsageFlags imageUsage,
   VkSwapchainImageUsageFlagsANDROID swapchainImageUsage,
   uint64_t *grallocConsumerUsage, uint64_t *grallocProducerUsage)
{
   VK_FROM_HANDLE(nvk_device, dev, device);

   VkResult result = vk_common_GetSwapchainGrallocUsage2ANDROID(
      device, format, imageUsage, swapchainImageUsage,
      grallocConsumerUsage, grallocProducerUsage);
   /* libvulkan's convertGralloc1ToBufferUsage() keeps this bit as
    * BufferUsage::CPU_WRITE_RARELY (= GRALLOC_USAGE_SW_WRITE_RARELY).
    */
   if (result == VK_SUCCESS && nvk_device_physical(dev)->android_foreign_gralloc)
      *grallocProducerUsage |= GRALLOC1_PRODUCER_USAGE_CPU_WRITE;

   return result;
}
#endif
""")]

# ---------------------------------------------------------------------------------------------------------------------
# 0005: LINEAR tiled shadow for every LINEAR-modifier image (ANB/AHB included)
P5 = [("src/nouveau/vulkan/nvk_image.c",
       """         assert(image->vk.drm_format_mod != DRM_FORMAT_MOD_INVALID);
      }

      if (image->vk.drm_format_mod == DRM_FORMAT_MOD_LINEAR) {
         for (uint8_t plane = 0; plane < image->plane_count; plane++) {
            VkFormat format = ycbcr_info ?
               ycbcr_info->planes[plane].format : image->vk.format;
            const uint8_t width_scale = ycbcr_info ?
               ycbcr_info->planes[plane].denominator_scales[0] : 1;
            const uint8_t height_scale = ycbcr_info ?
               ycbcr_info->planes[plane].denominator_scales[1] : 1;

            struct nil_image_init_info tiled_shadow_nil_info = {
               .dim = vk_image_type_to_nil_dim(image->vk.image_type),
               .format = nil_format(nvk_format_to_pipe_format(format)),
               .modifier = DRM_FORMAT_MOD_INVALID,
               .extent_px = {
                  .width = image->vk.extent.width / width_scale,
                  .height = image->vk.extent.height / height_scale,
                  .depth = image->vk.extent.depth,
                  .array_len = image->vk.array_layers,
               },
               .levels = image->vk.mip_levels,
               .samples = image->vk.samples,
               .usage = usage & ~NIL_IMAGE_USAGE_LINEAR_BIT,
            };

            bool ok = nil_image_init(&pdev->info,
                                     &image->linear_tiled_shadows[plane].nil,
                                     &tiled_shadow_nil_info);
            if (!ok)
               return vk_errorf(dev, VK_ERROR_UNKNOWN,
                                "Invalid image creation parameters");

            /* These are always allocated by the driver so the offset
             * doesn't matter.
             */
            image->linear_tiled_shadows[plane].plane_offset_B = 0;
            image->linear_tiled_shadows[plane].plane_align_B =
               MAX2(image->linear_tiled_shadows[plane].nil.align_B,
                    pdev->nvkmd->bind_align_B);
         }
      }
   }
""",
       """         assert(image->vk.drm_format_mod != DRM_FORMAT_MOD_INVALID);
      }
   }

   /* Rendering to a LINEAR image goes through a tiled shadow unless the
    * attachment is aligned for direct linear rendering (see
    * nvk_image_plane_aligned_for_linear_attachment()).  Besides explicit
    * DRM-modifier images this covers Android swapchain (ANB) images: libvulkan
    * creates them with VK_IMAGE_TILING_OPTIMAL around a gralloc buffer whose
    * modifier is LINEAR, and their shadow used to be left zero-sized.
    */
   if (image->vk.drm_format_mod == DRM_FORMAT_MOD_LINEAR &&
       (image->vk.tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT ||
        vk_image_is_android_native_buffer(&image->vk) ||
        vk_image_is_android_hardware_buffer(&image->vk))) {
      for (uint8_t plane = 0; plane < image->plane_count; plane++) {
         VkFormat format = ycbcr_info ?
            ycbcr_info->planes[plane].format : image->vk.format;
         const uint8_t width_scale = ycbcr_info ?
            ycbcr_info->planes[plane].denominator_scales[0] : 1;
         const uint8_t height_scale = ycbcr_info ?
            ycbcr_info->planes[plane].denominator_scales[1] : 1;

         struct nil_image_init_info tiled_shadow_nil_info = {
            .dim = vk_image_type_to_nil_dim(image->vk.image_type),
            .format = nil_format(nvk_format_to_pipe_format(format)),
            .modifier = DRM_FORMAT_MOD_INVALID,
            .extent_px = {
               .width = image->vk.extent.width / width_scale,
               .height = image->vk.extent.height / height_scale,
               .depth = image->vk.extent.depth,
               .array_len = image->vk.array_layers,
            },
            .levels = image->vk.mip_levels,
            .samples = image->vk.samples,
            .usage = usage & ~NIL_IMAGE_USAGE_LINEAR_BIT,
         };

         bool ok = nil_image_init(&pdev->info,
                                  &image->linear_tiled_shadows[plane].nil,
                                  &tiled_shadow_nil_info);
         if (!ok)
            return vk_errorf(dev, VK_ERROR_UNKNOWN,
                             "Invalid image creation parameters");

         /* These are always allocated by the driver so the offset
          * doesn't matter.
          */
         image->linear_tiled_shadows[plane].plane_offset_B = 0;
         image->linear_tiled_shadows[plane].plane_align_B =
            MAX2(image->linear_tiled_shadows[plane].nil.align_B,
                 pdev->nvkmd->bind_align_B);
      }
   }
""")]

# ---------------------------------------------------------------------------------------------------------------------
# 0006: sync_fd release
P6 = [("src/nouveau/vulkan/nvk_android.c",
       """#include "util/os_misc.h"
""",
       """#include "util/log.h"
#include "util/os_misc.h"
#include "util/stack_array.h"
#include "util/u_debug.h"
"""),
      ("src/nouveau/vulkan/nvk_android.c",
       """VKAPI_ATTR VkResult VKAPI_CALL
nvk_QueueSignalReleaseImageANDROID(VkQueue _queue,
                                   uint32_t waitSemaphoreCount,
                                   const VkSemaphore *pWaitSemaphores,
                                   VkImage image,
                                   int *pNativeFenceFd)
{
   VK_FROM_HANDLE(vk_queue, queue, _queue);
   struct vk_device *device = queue->base.device;

   /* This is to workaround""",
       """/* Submits the wait semaphores and signals the queue's SYNC_FD-exportable
 * anb_semaphore (destroyed by vk_queue_finish()), then exports it.  The
 * signal operation's first synchronization scope is every command submitted to
 * this queue before it, so the fence also covers rendering the app submitted
 * without semaphores.  The sync_file wraps a nouveau dma_fence that the
 * consumer (SurfaceFlinger's RenderEngine on ANV, which imports it with
 * vkImportSemaphoreFdKHR) waits on.
 */
static VkResult
nvk_android_release_sync_fd(struct vk_queue *queue,
                            uint32_t waitSemaphoreCount,
                            const VkSemaphore *pWaitSemaphores,
                            int *pNativeFenceFd)
{
   struct vk_device *device = queue->base.device;
   const VkDevice _device = vk_device_to_handle(device);
   const VkQueue _queue = vk_queue_to_handle(queue);
   VkResult result;

   if (queue->anb_semaphore == VK_NULL_HANDLE) {
      const VkExportSemaphoreCreateInfo export_info = {
         .sType = VK_STRUCTURE_TYPE_EXPORT_SEMAPHORE_CREATE_INFO,
         .handleTypes = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT,
      };
      const VkSemaphoreCreateInfo create_info = {
         .sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO,
         .pNext = &export_info,
      };
      result = device->dispatch_table.CreateSemaphore(_device, &create_info,
                                                      NULL,
                                                      &queue->anb_semaphore);
      if (result != VK_SUCCESS)
         return result;
   }

   STACK_ARRAY(VkSemaphoreSubmitInfo, wait_infos, waitSemaphoreCount);
   for (uint32_t i = 0; i < waitSemaphoreCount; i++) {
      wait_infos[i] = (VkSemaphoreSubmitInfo){
         .sType = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO,
         .semaphore = pWaitSemaphores[i],
         .stageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
      };
   }
   const VkSemaphoreSubmitInfo signal_info = {
      .sType = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO,
      .semaphore = queue->anb_semaphore,
      .stageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT,
   };
   const VkSubmitInfo2 submit_info = {
      .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO_2,
      .waitSemaphoreInfoCount = waitSemaphoreCount,
      .pWaitSemaphoreInfos = wait_infos,
      .signalSemaphoreInfoCount = 1,
      .pSignalSemaphoreInfos = &signal_info,
   };
   result = device->dispatch_table.QueueSubmit2(_queue, 1, &submit_info,
                                                VK_NULL_HANDLE);
   STACK_ARRAY_FINISH(wait_infos);
   if (result != VK_SUCCESS)
      return result;

   const VkSemaphoreGetFdInfoKHR get_fd = {
      .sType = VK_STRUCTURE_TYPE_SEMAPHORE_GET_FD_INFO_KHR,
      .semaphore = queue->anb_semaphore,
      .handleType = VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_SYNC_FD_BIT,
   };
   result = device->dispatch_table.GetSemaphoreFdKHR(_device, &get_fd,
                                                     pNativeFenceFd);
   if (result != VK_SUCCESS) {
      /* The semaphore is still pending-signalled and a binary semaphore must
       * not be signalled twice: drain the queue and start with a new one.
       */
      device->dispatch_table.QueueWaitIdle(_queue);
      device->dispatch_table.DestroySemaphore(_device, queue->anb_semaphore,
                                              NULL);
      queue->anb_semaphore = VK_NULL_HANDLE;
   }
   return result;
}

VKAPI_ATTR VkResult VKAPI_CALL
nvk_QueueSignalReleaseImageANDROID(VkQueue _queue,
                                   uint32_t waitSemaphoreCount,
                                   const VkSemaphore *pWaitSemaphores,
                                   VkImage image,
                                   int *pNativeFenceFd)
{
   VK_FROM_HANDLE(vk_queue, queue, _queue);
   struct vk_device *device = queue->base.device;

   /* NVK_ANB_WAITIDLE (debug.mesa.nvk.anb.waitidle) = 1 restores the
    * QueueWaitIdle() + -1 behaviour below for bring-up.
    */
   if (!debug_get_bool_option("NVK_ANB_WAITIDLE", false)) {
      int fd = -1;
      VkResult result = nvk_android_release_sync_fd(queue, waitSemaphoreCount,
                                                    pWaitSemaphores, &fd);
      if (result == VK_SUCCESS) {
         *pNativeFenceFd = fd;
         return VK_SUCCESS;
      }
      if (result == VK_ERROR_DEVICE_LOST)
         return result;
      mesa_logw("NVK: sync_fd release failed (%d), using QueueWaitIdle",
                result);
   }

   /* Fallback.  This is to workaround""")]

# ---------------------------------------------------------------------------------------------------------------------
# 0003: VK_ANDROID_external_memory_android_hardware_buffer (8b)
P3 = [("src/nouveau/vulkan/nvk_physical_device.c",
       """#if DETECT_OS_ANDROID
      .ANDROID_native_buffer = vk_android_get_ugralloc() != NULL,
#endif
""",
       """#if DETECT_OS_ANDROID
      .ANDROID_native_buffer = vk_android_get_ugralloc() != NULL,
#if ANDROID_API_LEVEL >= 26
      .ANDROID_external_memory_android_hardware_buffer =
         vk_android_get_ugralloc() != NULL,
#endif
#endif
"""),
      ("src/nouveau/vulkan/nvk_buffer.c",
       """#include "nvkmd/nvkmd.h"
""",
       """#include "nvkmd/nvkmd.h"

#include "util/detect_os.h"
#include "vk_android.h"
"""),
      ("src/nouveau/vulkan/nvk_buffer.c",
       """   case VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT:
   case VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT:
      pExternalBufferProperties->externalMemoryProperties =
         nvk_dma_buf_mem_props;
      return;
""",
       """   case VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT:
   case VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT:
      pExternalBufferProperties->externalMemoryProperties =
         nvk_dma_buf_mem_props;
      return;
#if DETECT_OS_ANDROID && ANDROID_API_LEVEL >= 26
   case VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID:
      vk_android_get_ahb_buffer_properties(physicalDevice, pExternalBufferInfo,
                                           pExternalBufferProperties);
      return;
#endif
"""),
      ("src/nouveau/vulkan/nvk_device_memory.c",
       """#include "util/u_atomic.h"
""",
       """#include "util/detect_os.h"
#include "util/u_atomic.h"

#if DETECT_OS_ANDROID && ANDROID_API_LEVEL >= 26
#include <vndk/hardware_buffer.h>
#endif
"""),
      ("src/nouveau/vulkan/nvk_device_memory.c",
       """   if (fd_info != NULL)
      handle_types |= fd_info->handleType;
""",
       """   if (fd_info != NULL)
      handle_types |= fd_info->handleType;

   int import_fd = (fd_info != NULL && fd_info->handleType) ? fd_info->fd : -1;
   bool is_ahb = false;
#if DETECT_OS_ANDROID && ANDROID_API_LEVEL >= 26
   /* AHB import (VkImportAndroidHardwareBufferInfoANDROID) and AHB export
    * allocations: vk_device_memory_create() has already acquired or allocated
    * mem->vk.ahardware_buffer.  Import its dma-buf; the AHB keeps owning that
    * fd, so it is not closed below.
    */
   if (mem->vk.ahardware_buffer != NULL) {
      const native_handle_t *handle =
         AHardwareBuffer_getNativeHandle(mem->vk.ahardware_buffer);
      if (handle == NULL || handle->numFds < 1) {
         result = vk_error(dev, VK_ERROR_INVALID_EXTERNAL_HANDLE);
         goto fail_alloc;
      }
      import_fd = handle->data[0];
      is_ahb = true;
      handle_types |=
         VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID;
   }
#endif
"""),
      ("src/nouveau/vulkan/nvk_device_memory.c",
       """      if (image->vk.tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT &&
          image->vk.drm_format_mod != DRM_FORMAT_MOD_LINEAR) {
         /* This image might be shared with GL so we need to set the BO flags""",
       """      if (!is_ahb &&
          image->vk.tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT &&
          image->vk.drm_format_mod != DRM_FORMAT_MOD_LINEAR) {
         /* This image might be shared with GL so we need to set the BO flags"""),
      ("src/nouveau/vulkan/nvk_device_memory.c",
       """   const bool is_import = fd_info && fd_info->handleType;
   if (is_import) {
      assert(fd_info->handleType ==
               VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT ||
             fd_info->handleType ==
               VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT);

      result = nvkmd_dev_import_dma_buf(dev->nvkmd, &dev->vk.base,
                                        fd_info->fd, &mem->mem);""",
       """   const bool is_import = import_fd >= 0;
   if (is_import) {
      assert(is_ahb ||
             fd_info->handleType ==
               VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT ||
             fd_info->handleType ==
               VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT);

      result = nvkmd_dev_import_dma_buf(dev->nvkmd, &dev->vk.base,
                                        import_fd, &mem->mem);"""),
      ("src/nouveau/vulkan/nvk_device_memory.c",
       """   if (fd_info && fd_info->handleType) {
      /* From the Vulkan spec:""",
       """   if (fd_info && fd_info->handleType && !is_ahb) {
      /* From the Vulkan spec:"""),
      ("src/nouveau/vulkan/nvk_image.c",
       """#include "clcd97.h"
""",
       """#include "clcd97.h"

#if DETECT_OS_ANDROID && ANDROID_API_LEVEL >= 26
/* AHB images are created deferred and laid out from the buffer at bind time */
static const VkExternalMemoryProperties nvk_ahb_mem_props = {
   .externalMemoryFeatures =
      VK_EXTERNAL_MEMORY_FEATURE_DEDICATED_ONLY_BIT |
      VK_EXTERNAL_MEMORY_FEATURE_EXPORTABLE_BIT |
      VK_EXTERNAL_MEMORY_FEATURE_IMPORTABLE_BIT,
   .exportFromImportedHandleTypes =
      VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID,
   .compatibleHandleTypes =
      VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID,
};
#endif
"""),
      ("src/nouveau/vulkan/nvk_image.c",
       """         ext_mem_props = &nvk_dma_buf_mem_props;
         break;

      default:
         /* From the Vulkan 1.3.256 spec:""",
       """         ext_mem_props = &nvk_dma_buf_mem_props;
         break;

#if DETECT_OS_ANDROID && ANDROID_API_LEVEL >= 26
      case VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID: {
         VkResult result = vk_android_get_ahb_image_properties(
            physicalDevice, pImageFormatInfo, pImageFormatProperties);
         if (result != VK_SUCCESS)
            return result;

         /* The image gets a LINEAR DRM-modifier layout from the AHB at bind
          * time (vk_android_init_deferred_image), which NIL only supports as a
          * single-level, single-layer, single-sample 2D image.
          */
         ext_mem_props = &nvk_ahb_mem_props;
         maxMipLevels = 1;
         maxArraySize = 1;
         sampleCounts = VK_SAMPLE_COUNT_1_BIT;
         break;
      }
#endif

      default:
         /* From the Vulkan 1.3.256 spec:"""),
      ("src/nouveau/vulkan/nvk_image.c",
       """static VkResult
nvk_image_init(struct nvk_device *dev,
               struct nvk_image *image,
               const VkImageCreateInfo *pCreateInfo)
{
   const struct nvk_physical_device *pdev = nvk_device_physical(dev);

   vk_image_init(&dev->vk, &image->vk, pCreateInfo);

   nil_image_usage_flags usage = 0;
""",
       """/* nvk_image_init() without vk_image_init(): lets an AHB image that was created
 * deferred (vk_android_init_deferred_image) be laid out at bind time without
 * re-initialising its vk_image (object base, private data, deferred info).
 */
static VkResult
nvk_image_init_layout(struct nvk_device *dev,
                      struct nvk_image *image,
                      const VkImageCreateInfo *pCreateInfo);

static VkResult
nvk_image_init(struct nvk_device *dev,
               struct nvk_image *image,
               const VkImageCreateInfo *pCreateInfo)
{
   vk_image_init(&dev->vk, &image->vk, pCreateInfo);
   return nvk_image_init_layout(dev, image, pCreateInfo);
}

static VkResult
nvk_image_init_layout(struct nvk_device *dev,
                      struct nvk_image *image,
                      const VkImageCreateInfo *pCreateInfo)
{
   const struct nvk_physical_device *pdev = nvk_device_physical(dev);

   nil_image_usage_flags usage = 0;
"""),
      ("src/nouveau/vulkan/nvk_image.c",
       """   image = vk_zalloc2(&dev->vk.alloc, pAllocator, sizeof(*image), 8,
                      VK_SYSTEM_ALLOCATION_SCOPE_OBJECT);
   if (!image)
      return vk_error(dev, VK_ERROR_OUT_OF_HOST_MEMORY);

   result = nvk_image_init(dev, image, pCreateInfo);
""",
       """#if DETECT_OS_ANDROID && ANDROID_API_LEVEL >= 26
   /* AHB images: the layout comes from the buffer, which is only known at
    * vkBindImageMemory2 time (nvk_android_ahb_image_init()).
    */
   const VkExternalMemoryImageCreateInfo *ext_info =
      vk_find_struct_const(pCreateInfo->pNext,
                           EXTERNAL_MEMORY_IMAGE_CREATE_INFO);
   if (ext_info != NULL &&
       (ext_info->handleTypes &
        VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID)) {
      image = vk_zalloc2(&dev->vk.alloc, pAllocator, sizeof(*image), 8,
                         VK_SYSTEM_ALLOCATION_SCOPE_OBJECT);
      if (!image)
         return vk_error(dev, VK_ERROR_OUT_OF_HOST_MEMORY);

      vk_image_init(&dev->vk, &image->vk, pCreateInfo);
      result = vk_android_init_deferred_image(&dev->vk, &image->vk,
                                              pCreateInfo, pAllocator);
      if (result != VK_SUCCESS) {
         vk_image_finish(&image->vk);
         vk_free2(&dev->vk.alloc, pAllocator, image);
         return vk_error(dev, result);
      }

      *pImage = nvk_image_to_handle(image);
      return VK_SUCCESS;
   }
#endif

   image = vk_zalloc2(&dev->vk.alloc, pAllocator, sizeof(*image), 8,
                      VK_SYSTEM_ALLOCATION_SCOPE_OBJECT);
   if (!image)
      return vk_error(dev, VK_ERROR_OUT_OF_HOST_MEMORY);

   result = nvk_image_init(dev, image, pCreateInfo);
"""),
      ("src/nouveau/vulkan/nvk_image.c",
       """      case VK_STRUCTURE_TYPE_MEMORY_DEDICATED_REQUIREMENTS: {
         VkMemoryDedicatedRequirements *dedicated = ext;
         if (image->vk.tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT &&""",
       """      case VK_STRUCTURE_TYPE_MEMORY_DEDICATED_REQUIREMENTS: {
         VkMemoryDedicatedRequirements *dedicated = ext;
         if (vk_image_is_android_hardware_buffer(&image->vk)) {
            /* VK_ANDROID_external_memory_android_hardware_buffer */
            dedicated->prefersDedicatedAllocation = true;
            dedicated->requiresDedicatedAllocation = true;
         } else if (image->vk.tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT &&"""),
      ("src/nouveau/vulkan/nvk_image.c",
       """static VkResult
nvk_bind_image_memory(struct nvk_device *dev,
                      const VkBindImageMemoryInfo *info)
{""",
       """#if DETECT_OS_ANDROID && ANDROID_API_LEVEL >= 26
static VkResult
nvk_android_ahb_image_init(struct nvk_device *dev,
                           struct nvk_image *image,
                           struct AHardwareBuffer *ahb)
{
   const VkImageCreateInfo *deferred = image->vk.android_deferred_create_info;
   VkImageDrmFormatModifierExplicitCreateInfoEXT mod_info;
   VkSubresourceLayout layouts[NVK_MAX_IMAGE_PLANES];

   VkResult result = vk_android_get_ahb_layout(ahb, &mod_info, layouts,
                                               NVK_MAX_IMAGE_PLANES);
   if (result != VK_SUCCESS)
      return result;

   /* Same rule as for gralloc ANB imports: NIL panics on foreign modifiers. */
   if (mod_info.drmFormatModifier != DRM_FORMAT_MOD_LINEAR &&
       !fourcc_mod_is_vendor(mod_info.drmFormatModifier, NVIDIA)) {
      return vk_errorf(dev, VK_ERROR_INVALID_EXTERNAL_HANDLE,
                       "AHB has DRM modifier 0x%016llx; NVK can only import "
                       "LINEAR or NVIDIA modifiers",
                       (unsigned long long)mod_info.drmFormatModifier);
   }

   const VkExternalMemoryImageCreateInfo ext_info = {
      .sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO,
      .pNext = deferred->pNext,
      .handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT,
   };
   mod_info.pNext = &ext_info;

   VkImageCreateInfo create_info = *deferred;
   create_info.pNext = &mod_info;
   assert(create_info.tiling == VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT);

   return nvk_image_init_layout(dev, image, &create_info);
}
#endif

static VkResult
nvk_bind_image_memory(struct nvk_device *dev,
                      const VkBindImageMemoryInfo *info)
{"""),
      ("src/nouveau/vulkan/nvk_image.c",
       """   /* Ignore this struct on Android, we cannot access swapchain structures there. */
#ifdef NVK_USE_WSI_PLATFORM
   if (mem == NULL) {""",
       """#if DETECT_OS_ANDROID && ANDROID_API_LEVEL >= 26
   /* Deferred AHB image (nvk_CreateImage): lay it out from the buffer now. */
   if (image->vk.android_deferred_create_info != NULL &&
       image->plane_count == 0) {
      if (mem == NULL || mem->vk.ahardware_buffer == NULL)
         return vk_error(dev, VK_ERROR_INVALID_EXTERNAL_HANDLE);
      result = nvk_android_ahb_image_init(dev, image,
                                          mem->vk.ahardware_buffer);
      if (result != VK_SUCCESS)
         return result;
   }
#endif

   /* Ignore this struct on Android, we cannot access swapchain structures there. */
#ifdef NVK_USE_WSI_PLATFORM
   if (mem == NULL) {""")]

PATCHES = {
    "0001": ("0001-nvk-android-reject-foreign-gralloc-modifiers.patch", P1),
    "0002": ("0002-nvk-allow-building-without-libelf.patch", P2),
    "0003": ("0003-nvk-android-hardware-buffer.patch", P3),
    "0004": ("0004-nvk-android-force-linear-for-foreign-gralloc.patch", P4),
    "0005": ("0005-nvk-linear-tiled-shadow-for-android-images.patch", P5),
    "0006": ("0006-nvk-android-sync-fd-release.patch", P6),
}
SERIES = ["0001", "0002", "0004", "0005", "0006", "0003"]
SETS = {
    "base": ["0001", "0002"],
    "8a": ["0001", "0002", "0004", "0005", "0006"],
    "8b": ["0001", "0002", "0004", "0005", "0006", "0003"],
}


def read(path):
    with open(path) as f:
        return f.read()


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser(description="apply the nvk-android patch series (see the module docstring)")
    ap.add_argument("--set", default="8a", choices=sorted(SETS))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("src")
    ap.add_argument("out")
    a = ap.parse_args()

    selected = [p for p in SERIES if p in SETS[a.set]]
    all_files = []
    for pid in SERIES:
        for rel, _, _ in PATCHES[pid][1]:
            if rel not in all_files:
                all_files.append(rel)

    def pristine(rel):
        p = os.path.join(a.src, rel)
        if os.path.exists(p + ".orig"):
            return read(p + ".orig")
        if os.path.exists(p):
            return read(p)
        raise SystemExit("%s does not exist in %s" % (rel, a.src))

    state = {}
    diffs = {}
    for pid in selected:
        name, edits = PATCHES[pid]
        before = dict(state)
        for rel, old, new in edits:
            if rel not in state:
                state[rel] = pristine(rel)
            n = state[rel].count(old)
            if n != 1:
                raise SystemExit("%s: pattern found %d times (want 1) in %s:\n%s" % (name, n, rel, old[:300]))
            state[rel] = state[rel].replace(old, new)
        d = []
        for rel in [r for r in all_files if r in state]:
            base = before[rel] if rel in before else pristine(rel)
            if base != state[rel]:
                d += difflib.unified_diff(base.splitlines(True), state[rel].splitlines(True), "a/" + rel, "b/" + rel)
        diffs[name] = d
        print("%s: %d file(s), %d diff lines%s" % (name, len({e[0] for e in edits}), len(d),
                                                    " (check only)" if a.check else ""))

    if a.check:
        print("check OK: set %s = %s" % (a.set, " ".join(selected)))
        return 0

    for rel in all_files:
        p = os.path.join(a.src, rel)
        if rel in state:
            if not os.path.exists(p + ".orig"):
                shutil.copy2(p, p + ".orig")
            if read(p) != state[rel]:
                write(p, state[rel])
        elif os.path.exists(p + ".orig") and read(p) != read(p + ".orig"):
            # touched only by patches outside this set: restore the pristine content
            shutil.copy2(p + ".orig", p)
    os.makedirs(a.out, exist_ok=True)
    for pid in selected:
        name = PATCHES[pid][0]
        write(os.path.join(a.out, name), "".join(diffs[name]))
    print("applied set %s = %s" % (a.set, " ".join(selected)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
