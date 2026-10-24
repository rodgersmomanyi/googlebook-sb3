/*
 * nvk_probe - stand-alone NVK (Mesa Vulkan for NVIDIA/nouveau) test for Googlebook "lapis" on Surface Book 3.
 *
 * Loads a Vulkan ICD .so DIRECTLY (not through Android's libvulkan, which only ever loads the one system HAL
 * driver vulkan.<ro.hardware.vulkan>.so = ANV), lists the DRM render nodes, enumerates physical devices and runs:
 *   test 1 "offscreen": renders a triangle into a 256x256 optimal-tiled VkImage (device-local), copies it to a
 *                       host-visible buffer and checks the pixels (clear colour in the corner, triangle colour in the
 *                       middle, covered-pixel count ~32 %).
 *   test 2/3 (Android only) "anb-*": what Android's swapchain does with a gralloc buffer: allocate an
 *                       AHardwareBuffer through the system allocator (minigbm on lapis = i915 GEM memory), wrap its
 *                       native_handle in VkNativeBufferANDROID, vkCreateImage (NVK imports the dma-buf via u_gralloc),
 *                       render into it on the NVIDIA GPU and (linear variant) read it back with AHardwareBuffer_lock.
 *                       anb-swapchain = the usage bits a real window swapchain would get (driver usage + HW_COMPOSER);
 *                       anb-linear    = same + CPU_READ_OFTEN (makes minigbm pick a linear layout).
 * Each test runs in its own forked child, so a crash/abort inside the driver is reported instead of killing the run.
 *
 * Exit codes: 0 all run tests passed | 1 a test failed | 2 ICD could not be loaded / instance failed
 *             3 no matching (NVIDIA) Vulkan device - e.g. in the VMware VM, or nouveau not loaded
 *
 * Usage: nvk_probe [-d icd.so] [-a] [-n frames] [-o out.ppm] [-t offscreen|anb|all] [-F] [-H]
 *   -d  ICD to load (default: $NVK_ICD, /odm/lib64/nvk/vulkan.nouveau.so, /vendor/lib64/hw/vulkan.nouveau.so,
 *       ./vulkan.nouveau.so, ./libvulkan_nouveau.so)
 *   -a  accept any vendor (default: only vendorID 0x10de) - used to validate this program with lavapipe/ANV
 *   -n  also time N render+submit+wait iterations of the offscreen test (default 0)
 *   -o  write the offscreen result as a PPM image
 *   -F  do not fork (debugging)
 *   -H  enter the driver through its hwvulkan HAL module (HMI->open("vk0")), exactly like Android's libvulkan,
 *       instead of vk_icdGetInstanceProcAddr
 *
 * Build (Android, NDK):  see scripts/30_build_probe.sh
 * Build (Linux host, validation with lavapipe): cc -O2 -Iinclude nvk_probe.c -ldl -o nvk_probe_host
 *
 * SPDX-License-Identifier: MIT
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define VK_NO_PROTOTYPES 1
#include <vulkan/vulkan.h>

#ifdef __ANDROID__
#include <android/hardware_buffer.h>
#include <cutils/native_handle.h>
#include <hardware/gralloc.h>
#include <vulkan/vk_android_native_buffer.h>
#endif

#include "probe_shaders.h" /* tri_vert_spv[], tri_frag_spv[] (glslangValidator --vn) */

/* ---------- minimal DRM uapi (avoid header dependencies) ---------- */
struct probe_drm_version {
  int version_major, version_minor, version_patchlevel;
  size_t name_len;
  char *name;
  size_t date_len;
  char *date;
  size_t desc_len;
  char *desc;
};
#define PROBE_DRM_IOCTL_VERSION _IOWR('d', 0x00, struct probe_drm_version)

/* ---------- minimal hwvulkan HAL ABI (hardware/libhardware hardware.h + frameworks/native hwvulkan.h) ---------- */
struct hal_module;
struct hal_device {
  uint32_t tag;
  uint32_t version;
  struct hal_module *module;
#ifdef __LP64__
  uint64_t reserved[12];
#else
  uint32_t reserved[12];
#endif
  int (*close)(struct hal_device *device);
};
struct hal_methods {
  int (*open)(const struct hal_module *module, const char *id, struct hal_device **device);
};
struct hal_module {
  uint32_t tag;
  uint16_t module_api_version, hal_api_version;
  const char *id, *name, *author;
  struct hal_methods *methods;
  void *dso;
#ifdef __LP64__
  uint64_t reserved[32 - 7];
#else
  uint32_t reserved[32 - 7];
#endif
};
struct hal_vulkan_device {
  struct hal_device common;
  PFN_vkEnumerateInstanceExtensionProperties EnumerateInstanceExtensionProperties;
  PFN_vkCreateInstance CreateInstance;
  PFN_vkGetInstanceProcAddr GetInstanceProcAddr;
};

/* ---------- globals ---------- */
static const char *g_icd;
static int g_any_vendor, g_frames, g_nofork, g_first = -1, g_force_hmi;
static const char *g_ppm;
static PFN_vkGetInstanceProcAddr gipa;

#define W 256
#define H 256
static const float CLEAR[4] = {0.1f, 0.2f, 0.3f, 1.0f}; /* -> 26,51,77 */
static const uint8_t TRI[4] = {255, 128, 0, 255};        /* frag shader colour (1, .5, 0, 1) */

static void say(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void say(const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vprintf(fmt, ap);
  va_end(ap);
  fflush(stdout);
}

static double now_ms(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

static int read_line(const char *path, char *buf, size_t n) {
  FILE *f = fopen(path, "re");
  if (!f) return -1;
  if (!fgets(buf, (int)n, f)) buf[0] = 0;
  fclose(f);
  buf[strcspn(buf, "\r\n")] = 0;
  return 0;
}

/* ---------- environment diagnostics ---------- */
static void list_drm_nodes(void) {
  say("== DRM render nodes\n");
  int found = 0;
  for (int i = 128; i < 192; i++) {
    char p[64], sp[160], ven[32] = "?", dev[32] = "?", drvlink[256] = "", namebuf[64] = "";
    snprintf(p, sizeof p, "/dev/dri/renderD%d", i);
    if (access(p, F_OK)) continue;
    found++;
    snprintf(sp, sizeof sp, "/sys/class/drm/renderD%d/device/vendor", i);
    read_line(sp, ven, sizeof ven);
    snprintf(sp, sizeof sp, "/sys/class/drm/renderD%d/device/device", i);
    read_line(sp, dev, sizeof dev);
    snprintf(sp, sizeof sp, "/sys/class/drm/renderD%d/device/driver", i);
    ssize_t l = readlink(sp, drvlink, sizeof drvlink - 1);
    if (l > 0) drvlink[l] = 0;
    int fd = open(p, O_RDWR | O_CLOEXEC);
    const char *err = "";
    if (fd >= 0) {
      struct probe_drm_version v;
      memset(&v, 0, sizeof v);
      v.name = namebuf;
      v.name_len = sizeof namebuf - 1;
      if (ioctl(fd, PROBE_DRM_IOCTL_VERSION, &v) == 0) namebuf[v.name_len < sizeof namebuf ? v.name_len : sizeof namebuf - 1] = 0;
      close(fd);
    } else {
      err = strerror(errno);
    }
    const char *drv = strrchr(drvlink, '/');
    say("  %s  drm-driver=%s pci=%s:%s kdriver=%s %s%s\n", p, namebuf[0] ? namebuf : "?", ven, dev,
        drv ? drv + 1 : "?", err[0] ? "open failed: " : "", err);
  }
  if (!found) say("  (none)\n");
  if (access("/sys/module/nouveau", F_OK) == 0) {
    char v[128];
    say("  nouveau module: loaded");
    if (!read_line("/sys/module/nouveau/parameters/runpm", v, sizeof v)) say(" runpm=%s", v);
    if (!read_line("/sys/module/nouveau/parameters/config", v, sizeof v)) say(" config='%s'", v);
    say("\n");
  } else {
    say("  nouveau module: NOT loaded (NVK needs nouveau.ko bound to the 10de:1f91 dGPU)\n");
  }
}

/* ---------- ICD loading ---------- */
static int load_icd(void) {
  const char *cands[] = {g_icd,
                         getenv("NVK_ICD"),
                         "/odm/lib64/nvk/vulkan.nouveau.so",
                         "/vendor/lib64/hw/vulkan.nouveau.so",
                         "./vulkan.nouveau.so",
                         "./libvulkan_nouveau.so",
                         NULL};
  void *h = NULL;
  const char *used = NULL;
  for (size_t i = 0; i < sizeof cands / sizeof cands[0]; i++) {
    if (!cands[i] || !cands[i][0]) continue;
    if (access(cands[i], R_OK)) continue;
    h = dlopen(cands[i], RTLD_NOW | RTLD_LOCAL);
    if (h) {
      used = cands[i];
      break;
    }
    say("  dlopen(%s) failed: %s\n", cands[i], dlerror());
    if (g_icd) break; /* explicit path: do not silently fall back */
  }
  if (!h) {
    say("ERROR: no loadable Vulkan ICD found (use -d /path/to/vulkan.nouveau.so)\n");
    return -1;
  }
  say("== ICD %s\n", used);
  typedef VkResult(VKAPI_PTR * PFN_negotiate)(uint32_t *);
  PFN_negotiate neg = (PFN_negotiate)dlsym(h, "vk_icdNegotiateLoaderICDInterfaceVersion");
  gipa = g_force_hmi ? NULL : (PFN_vkGetInstanceProcAddr)dlsym(h, "vk_icdGetInstanceProcAddr");
  if (gipa) {
    if (neg) {
      uint32_t ver = 5;
      neg(&ver);
      say("  entry: vk_icdGetInstanceProcAddr (loader interface v%u)\n", ver);
    } else {
      say("  entry: vk_icdGetInstanceProcAddr\n");
    }
    return 0;
  }
  /* Android HAL module path, exactly what libvulkan does */
  struct hal_module *hmi = (struct hal_module *)dlsym(h, "HMI");
  if (!hmi || !hmi->methods || !hmi->methods->open) {
    say("ERROR: %s exports neither vk_icdGetInstanceProcAddr nor HMI\n", used);
    return -1;
  }
  struct hal_device *dev = NULL;
  int r = hmi->methods->open(hmi, "vk0", &dev);
  if (r || !dev) {
    say("ERROR: HMI open(vk0) failed: %d\n", r);
    return -1;
  }
  gipa = ((struct hal_vulkan_device *)dev)->GetInstanceProcAddr;
  say("  entry: HMI '%s' by '%s' (hwvulkan HAL)\n", hmi->name ? hmi->name : "?", hmi->author ? hmi->author : "?");
  return gipa ? 0 : -1;
}

/* ---------- Vulkan dispatch ---------- */
#define IFN(x) static PFN_vk##x vk##x
#define DFN(x) static PFN_vk##x vk##x
IFN(CreateInstance);
IFN(EnumerateInstanceVersion);
IFN(DestroyInstance);
IFN(EnumeratePhysicalDevices);
IFN(GetPhysicalDeviceProperties);
IFN(GetPhysicalDeviceProperties2);
IFN(GetPhysicalDeviceMemoryProperties);
IFN(GetPhysicalDeviceQueueFamilyProperties);
IFN(EnumerateDeviceExtensionProperties);
IFN(CreateDevice);
IFN(GetDeviceProcAddr);
DFN(DestroyDevice);
DFN(GetDeviceQueue);
DFN(CreateImage);
DFN(DestroyImage);
DFN(GetImageMemoryRequirements);
DFN(AllocateMemory);
DFN(FreeMemory);
DFN(BindImageMemory);
DFN(CreateBuffer);
DFN(DestroyBuffer);
DFN(GetBufferMemoryRequirements);
DFN(BindBufferMemory);
DFN(MapMemory);
DFN(UnmapMemory);
DFN(InvalidateMappedMemoryRanges);
DFN(CreateImageView);
DFN(DestroyImageView);
DFN(CreateRenderPass);
DFN(DestroyRenderPass);
DFN(CreateFramebuffer);
DFN(DestroyFramebuffer);
DFN(CreateShaderModule);
DFN(DestroyShaderModule);
DFN(CreatePipelineLayout);
DFN(DestroyPipelineLayout);
DFN(CreateGraphicsPipelines);
DFN(DestroyPipeline);
DFN(CreateCommandPool);
DFN(DestroyCommandPool);
DFN(AllocateCommandBuffers);
DFN(BeginCommandBuffer);
DFN(EndCommandBuffer);
DFN(CmdBeginRenderPass);
DFN(CmdEndRenderPass);
DFN(CmdBindPipeline);
DFN(CmdDraw);
DFN(CmdPipelineBarrier);
DFN(CmdCopyImageToBuffer);
DFN(QueueSubmit);
DFN(QueueWaitIdle);
DFN(CreateFence);
DFN(DestroyFence);
DFN(WaitForFences);
DFN(ResetFences);

static void load_global(void) {
  vkCreateInstance = (PFN_vkCreateInstance)gipa(NULL, "vkCreateInstance");
  vkEnumerateInstanceVersion = (PFN_vkEnumerateInstanceVersion)gipa(NULL, "vkEnumerateInstanceVersion");
}

static void load_instance(VkInstance inst) {
#define L(x) vk##x = (PFN_vk##x)gipa(inst, "vk" #x)
  L(DestroyInstance);
  L(EnumeratePhysicalDevices);
  L(GetPhysicalDeviceProperties);
  L(GetPhysicalDeviceProperties2);
  L(GetPhysicalDeviceMemoryProperties);
  L(GetPhysicalDeviceQueueFamilyProperties);
  L(EnumerateDeviceExtensionProperties);
  L(CreateDevice);
  L(GetDeviceProcAddr);
#undef L
}

static void load_device(VkDevice d) {
#define L(x) vk##x = (PFN_vk##x)vkGetDeviceProcAddr(d, "vk" #x)
  L(DestroyDevice);
  L(GetDeviceQueue);
  L(CreateImage);
  L(DestroyImage);
  L(GetImageMemoryRequirements);
  L(AllocateMemory);
  L(FreeMemory);
  L(BindImageMemory);
  L(CreateBuffer);
  L(DestroyBuffer);
  L(GetBufferMemoryRequirements);
  L(BindBufferMemory);
  L(MapMemory);
  L(UnmapMemory);
  L(InvalidateMappedMemoryRanges);
  L(CreateImageView);
  L(DestroyImageView);
  L(CreateRenderPass);
  L(DestroyRenderPass);
  L(CreateFramebuffer);
  L(DestroyFramebuffer);
  L(CreateShaderModule);
  L(DestroyShaderModule);
  L(CreatePipelineLayout);
  L(DestroyPipelineLayout);
  L(CreateGraphicsPipelines);
  L(DestroyPipeline);
  L(CreateCommandPool);
  L(DestroyCommandPool);
  L(AllocateCommandBuffers);
  L(BeginCommandBuffer);
  L(EndCommandBuffer);
  L(CmdBeginRenderPass);
  L(CmdEndRenderPass);
  L(CmdBindPipeline);
  L(CmdDraw);
  L(CmdPipelineBarrier);
  L(CmdCopyImageToBuffer);
  L(QueueSubmit);
  L(QueueWaitIdle);
  L(CreateFence);
  L(DestroyFence);
  L(WaitForFences);
  L(ResetFences);
#undef L
}

static const char *vkr(VkResult r) {
  switch (r) {
#define C(x) \
  case x: return #x
    C(VK_SUCCESS);
    C(VK_NOT_READY);
    C(VK_TIMEOUT);
    C(VK_INCOMPLETE);
    C(VK_ERROR_OUT_OF_HOST_MEMORY);
    C(VK_ERROR_OUT_OF_DEVICE_MEMORY);
    C(VK_ERROR_INITIALIZATION_FAILED);
    C(VK_ERROR_DEVICE_LOST);
    C(VK_ERROR_MEMORY_MAP_FAILED);
    C(VK_ERROR_LAYER_NOT_PRESENT);
    C(VK_ERROR_EXTENSION_NOT_PRESENT);
    C(VK_ERROR_FEATURE_NOT_PRESENT);
    C(VK_ERROR_INCOMPATIBLE_DRIVER);
    C(VK_ERROR_TOO_MANY_OBJECTS);
    C(VK_ERROR_FORMAT_NOT_SUPPORTED);
    C(VK_ERROR_INVALID_EXTERNAL_HANDLE);
    C(VK_ERROR_UNKNOWN);
#undef C
  default: return "VK_?";
  }
}

#define CHECK(call)                                                   \
  do {                                                                \
    VkResult _r = (call);                                             \
    if (_r != VK_SUCCESS) {                                           \
      say("  FAIL: %s -> %s (%d) [line %d]\n", #call, vkr(_r), _r, __LINE__); \
      return 1;                                                       \
    }                                                                 \
  } while (0)

/* ---------- shared device state ---------- */
static VkInstance g_inst;
static VkPhysicalDevice g_pd;
static VkPhysicalDeviceProperties g_props;
static VkPhysicalDeviceMemoryProperties g_mem;
static VkDevice g_dev;
static VkQueue g_queue;
static uint32_t g_qf;
static int g_has_anb;

static int has_ext(const VkExtensionProperties *e, uint32_t n, const char *name) {
  for (uint32_t i = 0; i < n; i++)
    if (!strcmp(e[i].extensionName, name)) return 1;
  return 0;
}

static int find_mem(uint32_t bits, VkMemoryPropertyFlags want) {
  for (uint32_t i = 0; i < g_mem.memoryTypeCount; i++)
    if ((bits & (1u << i)) && (g_mem.memoryTypes[i].propertyFlags & want) == want) return (int)i;
  return -1;
}

/* Creates the instance, prints every device, picks one, creates the device. Returns 0, 2 or 3 (exit codes). */
static int setup(int verbose) {
  load_global();
  if (!vkCreateInstance) {
    say("ERROR: vkCreateInstance not exported by the ICD\n");
    return 2;
  }
  uint32_t iver = VK_API_VERSION_1_0;
  if (vkEnumerateInstanceVersion) vkEnumerateInstanceVersion(&iver);
  VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
                           .pApplicationName = "nvk_probe",
                           .apiVersion = iver >= VK_API_VERSION_1_1 ? VK_API_VERSION_1_1 : VK_API_VERSION_1_0};
  VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app};
  VkResult r = vkCreateInstance(&ici, NULL, &g_inst);
  if (r != VK_SUCCESS) {
    say("ERROR: vkCreateInstance -> %s (%d)\n", vkr(r), r);
    return 2;
  }
  load_instance(g_inst);
  uint32_t n = 0;
  r = vkEnumeratePhysicalDevices(g_inst, &n, NULL);
  if (verbose) say("== instance version %u.%u.%u, %u physical device(s) (%s)\n", VK_VERSION_MAJOR(iver),
                   VK_VERSION_MINOR(iver), VK_VERSION_PATCH(iver), n, vkr(r));
  if (n == 0) {
    if (verbose)
      say("NO_DEVICE: the ICD found no usable GPU. Expected in the VMware VM (no NVIDIA GPU). On the Surface Book 3 "
          "check: performance base attached, nouveau.ko loaded & bound to 10de:1f91 (persist.vendor.gb.nouveau=1), "
          "/dev/dri/renderD129 present, GSP firmware nvidia/tu117/gsp/*-570.144.bin loaded (dmesg | grep nouveau).\n");
    return 3;
  }
  VkPhysicalDevice pds[16];
  if (n > 16) n = 16;
  vkEnumeratePhysicalDevices(g_inst, &n, pds);
  int pick = -1;
  for (uint32_t i = 0; i < n; i++) {
    VkPhysicalDeviceProperties p;
    vkGetPhysicalDeviceProperties(pds[i], &p);
    VkPhysicalDeviceDriverProperties drv = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES};
    int have_drv = 0;
    if (vkGetPhysicalDeviceProperties2 && p.apiVersion >= VK_API_VERSION_1_2) {
      VkPhysicalDeviceProperties2 p2 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &drv};
      vkGetPhysicalDeviceProperties2(pds[i], &p2);
      have_drv = 1;
    }
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(pds[i], &mp);
    uint32_t ne = 0;
    vkEnumerateDeviceExtensionProperties(pds[i], NULL, &ne, NULL);
    VkExtensionProperties *ext = calloc(ne ? ne : 1, sizeof *ext);
    vkEnumerateDeviceExtensionProperties(pds[i], NULL, &ne, ext);
    if (verbose) {
      say("  [%u] %s  vendor=0x%04x device=0x%04x type=%d api=%u.%u.%u driverVersion=0x%x\n", i, p.deviceName,
          p.vendorID, p.deviceID, p.deviceType, VK_VERSION_MAJOR(p.apiVersion), VK_VERSION_MINOR(p.apiVersion),
          VK_VERSION_PATCH(p.apiVersion), p.driverVersion);
      if (have_drv) say("      driver: %s / %s (driverID %d)\n", drv.driverName, drv.driverInfo, drv.driverID);
      for (uint32_t h = 0; h < mp.memoryHeapCount; h++)
        say("      heap %u: %" PRIu64 " MiB%s\n", h, (uint64_t)(mp.memoryHeaps[h].size >> 20),
            (mp.memoryHeaps[h].flags & VK_MEMORY_HEAP_DEVICE_LOCAL_BIT) ? " DEVICE_LOCAL" : "");
      const char *interesting[] = {"VK_ANDROID_native_buffer",
                                   "VK_ANDROID_external_memory_android_hardware_buffer",
                                   "VK_EXT_external_memory_dma_buf",
                                   "VK_EXT_image_drm_format_modifier",
                                   "VK_KHR_external_memory_fd",
                                   "VK_KHR_external_semaphore_fd",
                                   "VK_KHR_external_fence_fd",
                                   "VK_EXT_queue_family_foreign",
                                   "VK_KHR_swapchain",
                                   "VK_KHR_sampler_ycbcr_conversion"};
      say("      %u device extensions;", ne);
      for (size_t k = 0; k < sizeof interesting / sizeof interesting[0]; k++)
        say(" %s%s", has_ext(ext, ne, interesting[k]) ? "+" : "-", interesting[k] + 3);
      say("\n");
    }
    if (pick < 0 && (p.vendorID == 0x10de || g_any_vendor)) {
      pick = (int)i;
      g_has_anb = has_ext(ext, ne, "VK_ANDROID_native_buffer");
    }
    free(ext);
  }
  if (pick < 0) {
    if (verbose) say("NO_DEVICE: no NVIDIA (vendor 0x10de) device among them (use -a to test any device)\n");
    return 3;
  }
  g_pd = pds[pick];
  vkGetPhysicalDeviceProperties(g_pd, &g_props);
  vkGetPhysicalDeviceMemoryProperties(g_pd, &g_mem);
  uint32_t nq = 0;
  vkGetPhysicalDeviceQueueFamilyProperties(g_pd, &nq, NULL);
  VkQueueFamilyProperties qf[16];
  if (nq > 16) nq = 16;
  vkGetPhysicalDeviceQueueFamilyProperties(g_pd, &nq, qf);
  g_qf = UINT32_MAX;
  for (uint32_t i = 0; i < nq; i++)
    if (qf[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) {
      g_qf = i;
      break;
    }
  if (g_qf == UINT32_MAX) {
    say("ERROR: no graphics queue\n");
    return 2;
  }
  float prio = 1.0f;
  VkDeviceQueueCreateInfo qci = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
                                 .queueFamilyIndex = g_qf,
                                 .queueCount = 1,
                                 .pQueuePriorities = &prio};
  const char *dexts[1];
  uint32_t ndext = 0;
#ifdef __ANDROID__
  if (g_has_anb) dexts[ndext++] = "VK_ANDROID_native_buffer"; /* libvulkan enables this on the driver internally */
#endif
  VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
                            .queueCreateInfoCount = 1,
                            .pQueueCreateInfos = &qci,
                            .enabledExtensionCount = ndext,
                            .ppEnabledExtensionNames = dexts};
  r = vkCreateDevice(g_pd, &dci, NULL, &g_dev);
  if (r != VK_SUCCESS) {
    say("ERROR: vkCreateDevice -> %s (%d)\n", vkr(r), r);
    return 2;
  }
  load_device(g_dev);
  vkGetDeviceQueue(g_dev, g_qf, 0, &g_queue);
  if (verbose) say("== using device [%d] %s, queue family %u\n", pick, g_props.deviceName, g_qf);
  return 0;
}

/* ---------- rendering helpers ---------- */
struct rt {
  VkRenderPass rp;
  VkPipelineLayout pl;
  VkPipeline pipe;
  VkImageView view;
  VkFramebuffer fb;
  VkCommandPool pool;
  VkCommandBuffer cb;
};

static int make_pipeline(struct rt *t, VkImage img, VkImageLayout final_layout) {
  VkAttachmentDescription att = {.format = VK_FORMAT_R8G8B8A8_UNORM,
                                 .samples = VK_SAMPLE_COUNT_1_BIT,
                                 .loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR,
                                 .storeOp = VK_ATTACHMENT_STORE_OP_STORE,
                                 .stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE,
                                 .stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE,
                                 .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
                                 .finalLayout = final_layout};
  VkAttachmentReference ref = {0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
  VkSubpassDescription sp = {.pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS,
                             .colorAttachmentCount = 1,
                             .pColorAttachments = &ref};
  VkSubpassDependency dep = {.srcSubpass = 0,
                             .dstSubpass = VK_SUBPASS_EXTERNAL,
                             .srcStageMask = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
                             .dstStageMask = VK_PIPELINE_STAGE_TRANSFER_BIT | VK_PIPELINE_STAGE_HOST_BIT,
                             .srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,
                             .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_HOST_READ_BIT |
                                              VK_ACCESS_MEMORY_READ_BIT};
  VkRenderPassCreateInfo rpci = {.sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO,
                                 .attachmentCount = 1,
                                 .pAttachments = &att,
                                 .subpassCount = 1,
                                 .pSubpasses = &sp,
                                 .dependencyCount = 1,
                                 .pDependencies = &dep};
  CHECK(vkCreateRenderPass(g_dev, &rpci, NULL, &t->rp));
  VkImageViewCreateInfo vci = {.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
                               .image = img,
                               .viewType = VK_IMAGE_VIEW_TYPE_2D,
                               .format = VK_FORMAT_R8G8B8A8_UNORM,
                               .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
  CHECK(vkCreateImageView(g_dev, &vci, NULL, &t->view));
  VkFramebufferCreateInfo fci = {.sType = VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO,
                                 .renderPass = t->rp,
                                 .attachmentCount = 1,
                                 .pAttachments = &t->view,
                                 .width = W,
                                 .height = H,
                                 .layers = 1};
  CHECK(vkCreateFramebuffer(g_dev, &fci, NULL, &t->fb));
  VkShaderModule vs, fs;
  VkShaderModuleCreateInfo smci = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
                                   .codeSize = sizeof tri_vert_spv,
                                   .pCode = tri_vert_spv};
  CHECK(vkCreateShaderModule(g_dev, &smci, NULL, &vs));
  smci.codeSize = sizeof tri_frag_spv;
  smci.pCode = tri_frag_spv;
  CHECK(vkCreateShaderModule(g_dev, &smci, NULL, &fs));
  VkPipelineLayoutCreateInfo plci = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
  CHECK(vkCreatePipelineLayout(g_dev, &plci, NULL, &t->pl));
  VkPipelineShaderStageCreateInfo st[2] = {
      {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
       .stage = VK_SHADER_STAGE_VERTEX_BIT,
       .module = vs,
       .pName = "main"},
      {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
       .stage = VK_SHADER_STAGE_FRAGMENT_BIT,
       .module = fs,
       .pName = "main"}};
  VkPipelineVertexInputStateCreateInfo vi = {.sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO};
  VkPipelineInputAssemblyStateCreateInfo ia = {.sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO,
                                               .topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST};
  VkViewport vp = {0, 0, W, H, 0, 1};
  VkRect2D sc = {{0, 0}, {W, H}};
  VkPipelineViewportStateCreateInfo vps = {.sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO,
                                           .viewportCount = 1,
                                           .pViewports = &vp,
                                           .scissorCount = 1,
                                           .pScissors = &sc};
  VkPipelineRasterizationStateCreateInfo rs = {.sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO,
                                               .polygonMode = VK_POLYGON_MODE_FILL,
                                               .cullMode = VK_CULL_MODE_NONE,
                                               .frontFace = VK_FRONT_FACE_COUNTER_CLOCKWISE,
                                               .lineWidth = 1.0f};
  VkPipelineMultisampleStateCreateInfo ms = {.sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO,
                                             .rasterizationSamples = VK_SAMPLE_COUNT_1_BIT};
  VkPipelineColorBlendAttachmentState cba = {.colorWriteMask = 0xf};
  VkPipelineColorBlendStateCreateInfo cb = {.sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO,
                                            .attachmentCount = 1,
                                            .pAttachments = &cba};
  VkGraphicsPipelineCreateInfo gpci = {.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO,
                                       .stageCount = 2,
                                       .pStages = st,
                                       .pVertexInputState = &vi,
                                       .pInputAssemblyState = &ia,
                                       .pViewportState = &vps,
                                       .pRasterizationState = &rs,
                                       .pMultisampleState = &ms,
                                       .pColorBlendState = &cb,
                                       .layout = t->pl,
                                       .renderPass = t->rp};
  double t0 = now_ms();
  CHECK(vkCreateGraphicsPipelines(g_dev, VK_NULL_HANDLE, 1, &gpci, NULL, &t->pipe));
  say("  pipeline compiled in %.1f ms\n", now_ms() - t0);
  vkDestroyShaderModule(g_dev, vs, NULL);
  vkDestroyShaderModule(g_dev, fs, NULL);
  VkCommandPoolCreateInfo cpci = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
                                  .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT,
                                  .queueFamilyIndex = g_qf};
  CHECK(vkCreateCommandPool(g_dev, &cpci, NULL, &t->pool));
  VkCommandBufferAllocateInfo cbai = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
                                      .commandPool = t->pool,
                                      .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
                                      .commandBufferCount = 1};
  CHECK(vkAllocateCommandBuffers(g_dev, &cbai, &t->cb));
  return 0;
}

static void record_draw(struct rt *t) {
  VkClearValue cv;
  memcpy(cv.color.float32, CLEAR, sizeof CLEAR);
  VkRenderPassBeginInfo rpbi = {.sType = VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO,
                                .renderPass = t->rp,
                                .framebuffer = t->fb,
                                .renderArea = {{0, 0}, {W, H}},
                                .clearValueCount = 1,
                                .pClearValues = &cv};
  vkCmdBeginRenderPass(t->cb, &rpbi, VK_SUBPASS_CONTENTS_INLINE);
  vkCmdBindPipeline(t->cb, VK_PIPELINE_BIND_POINT_GRAPHICS, t->pipe);
  vkCmdDraw(t->cb, 3, 1, 0, 0);
  vkCmdEndRenderPass(t->cb);
}

static void destroy_rt(struct rt *t) {
  if (t->pool) vkDestroyCommandPool(g_dev, t->pool, NULL);
  if (t->pipe) vkDestroyPipeline(g_dev, t->pipe, NULL);
  if (t->pl) vkDestroyPipelineLayout(g_dev, t->pl, NULL);
  if (t->fb) vkDestroyFramebuffer(g_dev, t->fb, NULL);
  if (t->view) vkDestroyImageView(g_dev, t->view, NULL);
  if (t->rp) vkDestroyRenderPass(g_dev, t->rp, NULL);
}

static int near(int a, int b, int tol) { return abs(a - b) <= tol; }

/* Checks an RGBA8 image in memory. Returns 0 on pass. */
static int verify(const uint8_t *px, size_t row_bytes, const char *tag) {
  const uint8_t clr[4] = {(uint8_t)(CLEAR[0] * 255 + .5f), (uint8_t)(CLEAR[1] * 255 + .5f),
                          (uint8_t)(CLEAR[2] * 255 + .5f), 255};
  const uint8_t *c0 = px;                                   /* (0,0): outside the triangle */
  const uint8_t *cm = px + (size_t)(H / 2) * row_bytes + (W / 2) * 4; /* centre: inside */
  long covered = 0, other = 0;
  for (int y = 0; y < H; y++)
    for (int x = 0; x < W; x++) {
      const uint8_t *p = px + (size_t)y * row_bytes + (size_t)x * 4;
      if (near(p[0], TRI[0], 1) && near(p[1], TRI[1], 1) && near(p[2], TRI[2], 1)) covered++;
      else if (!(near(p[0], clr[0], 1) && near(p[1], clr[1], 1) && near(p[2], clr[2], 1))) other++;
    }
  double frac = covered / (double)(W * H);
  int ok = near(c0[0], clr[0], 1) && near(c0[1], clr[1], 1) && near(c0[2], clr[2], 1) && near(cm[0], TRI[0], 1) &&
           near(cm[1], TRI[1], 1) && near(cm[2], TRI[2], 1) && frac > 0.31 && frac < 0.33 && other < W * H / 100;
  say("  [%s] corner=(%u,%u,%u,%u) want (%u,%u,%u,255); centre=(%u,%u,%u,%u) want (255,128,0,255); triangle "
      "covers %.2f%% (want ~32%%), %ld unexpected pixels -> %s\n",
      tag, c0[0], c0[1], c0[2], c0[3], clr[0], clr[1], clr[2], cm[0], cm[1], cm[2], cm[3], frac * 100, other,
      ok ? "PIXELS OK" : "PIXELS WRONG");
  return ok ? 0 : 1;
}

static void write_ppm(const char *path, const uint8_t *px, size_t row_bytes) {
  FILE *f = fopen(path, "wbe");
  if (!f) {
    say("  cannot write %s: %s\n", path, strerror(errno));
    return;
  }
  fprintf(f, "P6\n%d %d\n255\n", W, H);
  for (int y = 0; y < H; y++)
    for (int x = 0; x < W; x++) fwrite(px + (size_t)y * row_bytes + (size_t)x * 4, 1, 3, f);
  fclose(f);
  say("  wrote %s\n", path);
}

/* ---------- test 1: offscreen optimal image + copy to buffer ---------- */
static int test_offscreen(void) {
  say("== test offscreen: %dx%d R8G8B8A8_UNORM optimal image, device-local\n", W, H);
  VkImageCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
                           .imageType = VK_IMAGE_TYPE_2D,
                           .format = VK_FORMAT_R8G8B8A8_UNORM,
                           .extent = {W, H, 1},
                           .mipLevels = 1,
                           .arrayLayers = 1,
                           .samples = VK_SAMPLE_COUNT_1_BIT,
                           .tiling = VK_IMAGE_TILING_OPTIMAL,
                           .usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
                           .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED};
  VkImage img;
  CHECK(vkCreateImage(g_dev, &ici, NULL, &img));
  VkMemoryRequirements mr;
  vkGetImageMemoryRequirements(g_dev, img, &mr);
  int mt = find_mem(mr.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
  if (mt < 0) mt = find_mem(mr.memoryTypeBits, 0);
  VkMemoryAllocateInfo mai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
                              .allocationSize = mr.size,
                              .memoryTypeIndex = (uint32_t)mt};
  VkDeviceMemory imem;
  CHECK(vkAllocateMemory(g_dev, &mai, NULL, &imem));
  CHECK(vkBindImageMemory(g_dev, img, imem, 0));

  VkBufferCreateInfo bci = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
                            .size = (VkDeviceSize)W * H * 4,
                            .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT};
  VkBuffer buf;
  CHECK(vkCreateBuffer(g_dev, &bci, NULL, &buf));
  vkGetBufferMemoryRequirements(g_dev, buf, &mr);
  int bmt = find_mem(mr.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_CACHED_BIT);
  if (bmt < 0) bmt = find_mem(mr.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT);
  if (bmt < 0) {
    say("  FAIL: no host-visible memory type\n");
    return 1;
  }
  mai.allocationSize = mr.size;
  mai.memoryTypeIndex = (uint32_t)bmt;
  VkDeviceMemory bmem;
  CHECK(vkAllocateMemory(g_dev, &mai, NULL, &bmem));
  CHECK(vkBindBufferMemory(g_dev, buf, bmem, 0));

  struct rt t;
  memset(&t, 0, sizeof t);
  if (make_pipeline(&t, img, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL)) return 1;
  VkCommandBufferBeginInfo bi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
  CHECK(vkBeginCommandBuffer(t.cb, &bi));
  record_draw(&t);
  VkBufferImageCopy reg = {.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1}, .imageExtent = {W, H, 1}};
  vkCmdCopyImageToBuffer(t.cb, img, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buf, 1, &reg);
  VkMemoryBarrier hb = {.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
                        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
                        .dstAccessMask = VK_ACCESS_HOST_READ_BIT};
  vkCmdPipelineBarrier(t.cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &hb, 0, NULL, 0,
                       NULL);
  CHECK(vkEndCommandBuffer(t.cb));
  VkFenceCreateInfo fci = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
  VkFence fence;
  CHECK(vkCreateFence(g_dev, &fci, NULL, &fence));
  VkSubmitInfo si = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &t.cb};
  double t0 = now_ms();
  CHECK(vkQueueSubmit(g_queue, 1, &si, fence));
  CHECK(vkWaitForFences(g_dev, 1, &fence, VK_TRUE, 5000000000ull));
  say("  first submit+wait %.2f ms\n", now_ms() - t0);
  void *map;
  CHECK(vkMapMemory(g_dev, bmem, 0, VK_WHOLE_SIZE, 0, &map));
  VkMappedMemoryRange range = {.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE, .memory = bmem, .size = VK_WHOLE_SIZE};
  vkInvalidateMappedMemoryRanges(g_dev, 1, &range);
  int fail = verify((const uint8_t *)map, W * 4, "offscreen");
  if (g_ppm) write_ppm(g_ppm, (const uint8_t *)map, W * 4);
  vkUnmapMemory(g_dev, bmem);
  if (g_frames > 0) {
    t0 = now_ms();
    for (int i = 0; i < g_frames; i++) {
      vkResetFences(g_dev, 1, &fence);
      CHECK(vkQueueSubmit(g_queue, 1, &si, fence));
      CHECK(vkWaitForFences(g_dev, 1, &fence, VK_TRUE, 5000000000ull));
    }
    double dt = now_ms() - t0;
    say("  %d x (render+copy+submit+wait): %.3f ms each\n", g_frames, dt / g_frames);
  }
  vkDestroyFence(g_dev, fence, NULL);
  destroy_rt(&t);
  vkDestroyBuffer(g_dev, buf, NULL);
  vkFreeMemory(g_dev, bmem, NULL);
  vkDestroyImage(g_dev, img, NULL);
  vkFreeMemory(g_dev, imem, NULL);
  say("  offscreen: %s\n", fail ? "FAIL" : "PASS");
  return fail;
}

#ifdef __ANDROID__
/* ---------- tests 2/3: gralloc (AHardwareBuffer) -> VkNativeBufferANDROID -> NVK, as Android's swapchain does ------- */
typedef const native_handle_t *(*PFN_getNativeHandle)(const AHardwareBuffer *);

/* minigbm "CrOS Gralloc" gralloc0 perform() query - the same call Mesa's u_gralloc_cros backend (used by both ANV and
 * NVK on lapis) makes to learn the buffer layout. Lets us print the DRM modifier minigbm picked. */
struct cros_gralloc0_buffer_info {
  uint32_t drm_fourcc;
  int num_fds;
  int fds[4];
  uint64_t modifier;
  int offset[4];
  int stride[4];
};
#define CROS_GRALLOC_DRM_GET_BUFFER_INFO 4

static const char *mod_name(uint64_t m) {
  switch (m) {
  case 0: return "LINEAR";
  case 0x0100000000000001ull: return "I915_X_TILED";
  case 0x0100000000000002ull: return "I915_Y_TILED";
  case 0x0100000000000003ull: return "I915_Yf_TILED";
  case 0x0100000000000004ull: return "I915_Y_TILED_CCS";
  case 0x0100000000000005ull: return "I915_Yf_TILED_CCS";
  case 0x0100000000000006ull: return "I915_Y_TILED_GEN12_RC_CCS";
  case 0x0100000000000007ull: return "I915_Y_TILED_GEN12_MC_CCS";
  case 0x00ffffffffffffffull: return "INVALID";
  default: return (m >> 56) == 0x03 ? "NVIDIA" : "other";
  }
}

static void print_gralloc_layout(const native_handle_t *nh) {
  static gralloc_module_t *gm;
  static int tried;
  if (!tried) {
    tried = 1;
    void *hw = dlopen("libhardware.so", RTLD_NOW);
    int (*get_module)(const char *, const struct hw_module_t **) =
        hw ? (int (*)(const char *, const struct hw_module_t **))dlsym(hw, "hw_get_module") : NULL;
    if (!get_module || get_module(GRALLOC_HARDWARE_MODULE_ID, (const struct hw_module_t **)&gm)) gm = NULL;
    if (gm) say("  gralloc0 module: '%s' (Mesa's u_gralloc_cros needs 'CrOS Gralloc')\n", gm->common.name);
    else say("  gralloc0 module: not loadable (hw_get_module(\"gralloc\") failed)\n");
  }
  if (!gm || !gm->perform || !nh) return;
  struct cros_gralloc0_buffer_info bi;
  memset(&bi, 0, sizeof bi);
  if (gm->perform(gm, CROS_GRALLOC_DRM_GET_BUFFER_INFO, nh, &bi) == 0)
    say("  minigbm layout: fourcc=%.4s planes=%d modifier=0x%016" PRIx64 " (%s) stride0=%d offset0=%d\n",
        (const char *)&bi.drm_fourcc, bi.num_fds, bi.modifier, mod_name(bi.modifier), bi.stride[0], bi.offset[0]);
  else
    say("  minigbm layout: perform(GET_BUFFER_INFO) failed\n");
}

static int test_anb(int linear) {
  const char *tag = linear ? "anb-linear" : "anb-swapchain";
  say("== test %s: gralloc buffer from the system allocator imported with VkNativeBufferANDROID\n", tag);
  if (!g_has_anb) {
    say("  SKIP/FAIL: driver does not expose VK_ANDROID_native_buffer (u_gralloc found no gralloc: needs "
        "ro.hardware.gralloc=minigbm and /vendor/lib64/hw/gralloc.minigbm.so readable)\n");
    return 1;
  }
  void *nw = dlopen("libnativewindow.so", RTLD_NOW);
  PFN_getNativeHandle get_nh = nw ? (PFN_getNativeHandle)dlsym(nw, "AHardwareBuffer_getNativeHandle") : NULL;
  if (!get_nh) {
    say("  FAIL: AHardwareBuffer_getNativeHandle not found in libnativewindow.so\n");
    return 1;
  }
  /* The usage a window swapchain image gets: the driver's swapchain gralloc usage (vkGetSwapchainGrallocUsage2ANDROID)
   * ORed with the consumer usage SurfaceFlinger sets on app window queues (HW_COMPOSER | HW_TEXTURE). */
  uint64_t cons = 0, prod = 0;
  PFN_vkGetSwapchainGrallocUsage2ANDROID gu2 =
      (PFN_vkGetSwapchainGrallocUsage2ANDROID)vkGetDeviceProcAddr(g_dev, "vkGetSwapchainGrallocUsage2ANDROID");
  if (gu2) {
    VkResult r = gu2(g_dev, VK_FORMAT_R8G8B8A8_UNORM,
                     VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT, 0, &cons, &prod);
    say("  vkGetSwapchainGrallocUsage2ANDROID -> %s consumer=0x%" PRIx64 " producer=0x%" PRIx64 "\n", vkr(r), cons,
        prod);
  } else {
    say("  vkGetSwapchainGrallocUsage2ANDROID not available; using HW_RENDER|HW_TEXTURE\n");
    prod = AHARDWAREBUFFER_USAGE_GPU_COLOR_OUTPUT | AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE;
  }
  uint64_t usage = cons | prod | AHARDWAREBUFFER_USAGE_GPU_COLOR_OUTPUT | AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE |
                   AHARDWAREBUFFER_USAGE_COMPOSER_OVERLAY;
  if (linear) usage |= AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN;
  AHardwareBuffer_Desc desc = {.width = W, .height = H, .layers = 1,
                               .format = AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM, .usage = usage};
  AHardwareBuffer *ahb = NULL;
  int ar = AHardwareBuffer_allocate(&desc, &ahb);
  if (ar || !ahb) {
    say("  FAIL: AHardwareBuffer_allocate(usage=0x%" PRIx64 ") -> %d\n", usage, ar);
    return 1;
  }
  AHardwareBuffer_describe(ahb, &desc);
  const native_handle_t *nh = get_nh(ahb);
  say("  AHB %ux%u stride=%u px usage=0x%" PRIx64 "; native_handle numFds=%d numInts=%d\n", desc.width, desc.height,
      desc.stride, (uint64_t)desc.usage, nh ? nh->numFds : -1, nh ? nh->numInts : -1);
  print_gralloc_layout(nh);
  VkNativeBufferANDROID nb = {.sType = VK_STRUCTURE_TYPE_NATIVE_BUFFER_ANDROID,
                              .handle = nh,
                              .stride = (int)desc.stride,
                              .format = 1 /* HAL_PIXEL_FORMAT_RGBA_8888 */,
                              .usage = (int)(usage & 0xffffffffu),
                              .usage2 = {.consumer = usage, .producer = usage},
                              .usage3 = usage,
                              .ahb = ahb};
  VkImageCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
                           .pNext = &nb,
                           .imageType = VK_IMAGE_TYPE_2D,
                           .format = VK_FORMAT_R8G8B8A8_UNORM,
                           .extent = {W, H, 1},
                           .mipLevels = 1,
                           .arrayLayers = 1,
                           .samples = VK_SAMPLE_COUNT_1_BIT,
                           .tiling = VK_IMAGE_TILING_OPTIMAL, /* what libvulkan's swapchain passes */
                           .usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
                           .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED};
  VkImage img;
  VkResult r = vkCreateImage(g_dev, &ici, NULL, &img);
  say("  vkCreateImage(VkNativeBufferANDROID) -> %s\n", vkr(r));
  if (r != VK_SUCCESS) {
    say("  %s: FAIL (NVK could not import this gralloc buffer: typically an Intel tiling modifier from minigbm's "
        "i915 backend, which NVIDIA hardware cannot address)\n", tag);
    AHardwareBuffer_release(ahb);
    return 1;
  }
  struct rt t;
  memset(&t, 0, sizeof t);
  if (make_pipeline(&t, img, VK_IMAGE_LAYOUT_GENERAL)) return 1;
  VkCommandBufferBeginInfo bi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
  CHECK(vkBeginCommandBuffer(t.cb, &bi));
  record_draw(&t);
  CHECK(vkEndCommandBuffer(t.cb));
  VkSubmitInfo si = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &t.cb};
  double t0 = now_ms();
  CHECK(vkQueueSubmit(g_queue, 1, &si, VK_NULL_HANDLE));
  PFN_vkQueueSignalReleaseImageANDROID rel =
      (PFN_vkQueueSignalReleaseImageANDROID)vkGetDeviceProcAddr(g_dev, "vkQueueSignalReleaseImageANDROID");
  if (rel) {
    int fence_fd = -2;
    r = rel(g_queue, 0, NULL, img, &fence_fd);
    say("  vkQueueSignalReleaseImageANDROID -> %s fence_fd=%d (NVK 26.3 waits idle and returns -1)\n", vkr(r),
        fence_fd);
    if (fence_fd >= 0) close(fence_fd);
  }
  CHECK(vkQueueWaitIdle(g_queue));
  say("  render into gralloc buffer + wait %.2f ms\n", now_ms() - t0);
  int fail = 0;
  if (linear) {
    void *p = NULL;
    ar = AHardwareBuffer_lock(ahb, AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN, -1, NULL, &p);
    if (ar || !p) {
      say("  FAIL: AHardwareBuffer_lock -> %d\n", ar);
      fail = 1;
    } else {
      fail = verify((const uint8_t *)p, (size_t)desc.stride * 4, tag);
      AHardwareBuffer_unlock(ahb, NULL);
    }
  } else {
    say("  (no CPU usage on this buffer, so its pixels are not read back; import+render success is the result)\n");
  }
  destroy_rt(&t);
  vkDestroyImage(g_dev, img, NULL);
  AHardwareBuffer_release(ahb);
  say("  %s: %s\n", tag, fail ? "FAIL" : "PASS");
  return fail;
}
#endif

static int run_one(int which) {
  int rc = setup(which == g_first);
  if (rc) return rc;
  if (which == 0) return test_offscreen();
#ifdef __ANDROID__
  return test_anb(which == 2);
#else
  return 0;
#endif
}

int main(int argc, char **argv) {
  const char *tests = "all";
  int opt;
  while ((opt = getopt(argc, argv, "d:an:o:t:FHh")) != -1) {
    switch (opt) {
    case 'd': g_icd = optarg; break;
    case 'a': g_any_vendor = 1; break;
    case 'n': g_frames = atoi(optarg); break;
    case 'o': g_ppm = optarg; break;
    case 't': tests = optarg; break;
    case 'F': g_nofork = 1; break;
    case 'H': g_force_hmi = 1; break;
    default:
      printf("usage: %s [-d icd.so] [-a] [-n frames] [-o out.ppm] [-t offscreen|anb|all] [-F] [-H]\n", argv[0]);
      return 2;
    }
  }
  setvbuf(stdout, NULL, _IOLBF, 0);
  say("nvk_probe (built %s %s) pid %d\n", __DATE__, __TIME__, getpid());
  list_drm_nodes();
  if (load_icd()) return 2;
  int want[3] = {1, 0, 0};
  (void)tests;
#ifdef __ANDROID__
  if (!strcmp(tests, "all") || !strcmp(tests, "anb")) want[1] = want[2] = 1;
  if (!strcmp(tests, "anb")) want[0] = 0;
#endif
  const char *names[3] = {"offscreen", "anb-swapchain", "anb-linear"};
  for (int i = 2; i >= 0; i--)
    if (want[i]) g_first = i;
  int results[3] = {-1, -1, -1}, worst = 0;
  for (int i = 0; i < 3; i++) {
    if (!want[i]) continue;
    int rc;
    if (g_nofork) {
      rc = run_one(i);
    } else {
      fflush(stdout);
      pid_t pid = fork();
      if (pid == 0) _exit(run_one(i));
      int st = 0;
      waitpid(pid, &st, 0);
      if (WIFSIGNALED(st)) {
        say("  %s: CRASHED in the driver with signal %d (see logcat / tombstone)\n", names[i], WTERMSIG(st));
        rc = 1;
      } else {
        rc = WEXITSTATUS(st);
      }
    }
    results[i] = rc;
    if (rc == 2 || rc == 3) { /* no device / no instance: the other tests cannot run either */
      worst = rc;
      break;
    }
    if (rc) worst = 1;
  }
  say("== summary:");
  for (int i = 0; i < 3; i++)
    if (results[i] >= 0)
      say(" %s=%s", names[i], results[i] == 0 ? "PASS" : results[i] == 3 ? "NO_DEVICE" : results[i] == 2 ? "ERROR" : "FAIL");
  say("\nRESULT: %s\n", worst == 0 ? "PASS" : worst == 3 ? "NO_DEVICE" : worst == 2 ? "ERROR" : "FAIL");
  return worst;
}
