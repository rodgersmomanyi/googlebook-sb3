/*
 * nvk_probe - stand-alone NVK (Mesa Vulkan for NVIDIA/nouveau) test for Googlebook "lapis" on Surface Book 3.
 *
 * Loads a Vulkan ICD .so DIRECTLY (not through Android's libvulkan, which only ever loads the one system HAL
 * driver vulkan.<ro.hardware.vulkan>.so = ANV), lists the DRM render nodes, enumerates physical devices and runs:
 *   offscreen          renders a triangle into a 256x256 optimal-tiled VkImage (device-local), copies it to a
 *                      host-visible buffer and checks the pixels (clear colour in the corner, triangle colour in the
 *                      middle, covered-pixel count ~32 %).
 *   anb-swapchain      (Android) what Android's swapchain does with a gralloc buffer: allocate an AHardwareBuffer
 *                      through the system allocator (minigbm on lapis = i915 GEM memory), wrap its native_handle in
 *                      VkNativeBufferANDROID, vkCreateImage (NVK imports the dma-buf via u_gralloc), render into it on
 *                      the NVIDIA GPU. Usage = the driver's swapchain usage + HW_COMPOSER.
 *   anb-linear         (Android) same + CPU_READ_OFTEN (minigbm picks LINEAR) and a CPU read-back check.
 *   ahb-i915-readback  (Android) Phase 8 gate G0: AHB with CPU_WRITE_RARELY|CPU_READ_RARELY|GPU_COLOR_OUTPUT|
 *                      GPU_SAMPLED_IMAGE -> VkNativeBufferANDROID import into NVK -> render a per-pixel unique pattern
 *                      -> release (sync_fd) -> read back (1) with AHardwareBuffer_lock and (2) by sampling the same
 *                      buffer on ANV (vulkan.intel.so imports it as an AHB image, a compute shader texelFetch()es every
 *                      texel). PASS only if both read-backs are bit-exact.
 *   a17-swapchain-model (Android) the Android 17 libvulkan swapchain sequence for this driver (swapchain.cpp:
 *                      getProducerUsage): vkGetPhysicalDeviceImageFormatProperties2 with the AHB handle type
 *                      (expected to fail on NVK without AHB support) -> GetSwapchainGrallocUsage4/3/2/1 fallback ->
 *                      allocate with SurfaceFlinger's consumer usage + the returned producer usage -> VkNativeBufferANDROID
 *                      (+ VkSwapchainImageCreateInfoANDROID) image -> vkAcquireImageANDROID -> render ->
 *                      vkQueueSignalReleaseImageANDROID with a wait semaphore -> wait the returned sync_fd -> verify the
 *                      pixels on ANV when available. Needs NVK patch 0004 (LINEAR forcing) to pass on i915 gralloc.
 * Each test runs in its own forked child with a watchdog (-T), so a crash/abort/hang inside a driver is reported
 * instead of killing the run.
 * Supervision (INTERFACES v1.2 25.3, the automatic self-test runs `timeout 150 nvk_probe -t <mode> -T 60` as root):
 * the probe never outlives whoever runs it, and a test child never outlives the probe. SIGTERM/SIGINT/SIGHUP to the
 * probe SIGKILL the running test child, wait for it (at most 5 s: a child stuck in an uninterruptible driver ioctl
 * cannot be reaped) and then end the probe with that signal; the probe gets SIGTERM when its parent dies, and every
 * test child gets SIGKILL when the probe dies, even by SIGKILL. (lapis' toybox `timeout` signals the command's whole
 * process group, but a signal sent to the probe's pid alone, or a `timeout` that is itself killed - which is what the
 * self-test script's TERM trap does - used to leave the probe and/or its test child running with the device open;
 * verify/selftest_invocation.sh S5/S6/S9.)
 *
 * Exit codes: 0 all run tests passed | 1 a test failed (also: a requested test is not in this build, or no test ran)
 *             2 ICD could not be loaded / instance failed
 *             3 no matching (NVIDIA) Vulkan device - e.g. in the VMware VM, or nouveau not loaded
 *             killed by SIGTERM/SIGINT/SIGHUP: see "Supervision"
 *
 * Usage: nvk_probe [-d icd.so] [-A anv.so] [-a] [-n frames] [-o out.ppm] [-t TESTS] [-T secs] [-F] [-H]
 *   -d  ICD to load (default: $NVK_ICD, /vendor/lib64/hw/vulkan.nouveau.so, /odm/dgpu/lib64/hw/vulkan.nouveau.so,
 *       ./vulkan.nouveau.so, ./libvulkan_nouveau.so)
 *   -A  ANV (Intel) ICD for the read-back leg (default: $NVK_PROBE_ANV, /vendor/lib64/hw/vulkan.intel.so)
 *   -a  accept any vendor (default: only vendorID 0x10de) - used to validate this program with lavapipe/ANV
 *   -n  also time N render+submit+wait iterations of the offscreen test (default 0)
 *   -o  write the offscreen result as a PPM image
 *   -t  comma-separated tests: offscreen, anb (= anb-swapchain,anb-linear), anb-swapchain, anb-linear,
 *       ahb-i915-readback, a17-swapchain-model, all (default: all; non-Android builds only have offscreen)
 *   -T  per-test watchdog in seconds (default 60; 0 = none)
 *   -F  do not fork (debugging; disables the watchdog)
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
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif

#define VK_NO_PROTOTYPES 1
#include <vulkan/vulkan.h>

#ifdef __ANDROID__
#include <android/hardware_buffer.h>
#include <cutils/native_handle.h>
#include <hardware/gralloc.h>
#include <vulkan/vk_android_native_buffer.h>
#include <vulkan/vulkan_android.h>
#endif

/* tri_vert_spv[], tri_frag_spv[], fs_vert_spv[], pat_frag_spv[], readback_comp_spv[] (glslangValidator --vn) */
#include "probe_shaders.h"

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
static const char *g_icd, *g_anv_icd;
static int g_any_vendor, g_frames, g_nofork, g_first = -1, g_force_hmi, g_timeout = 60;
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
/* Loads an ICD and returns its vkGetInstanceProcAddr (vk_icd* entry or the hwvulkan HMI path). */
static PFN_vkGetInstanceProcAddr load_icd_file(const char *const *cands, int explicit_path, int force_hmi,
                                               const char *what) {
  void *h = NULL;
  const char *used = NULL;
  for (size_t i = 0; cands[i] != (const char *)-1; i++) {
    if (!cands[i] || !cands[i][0]) continue;
    if (access(cands[i], R_OK)) {
      if (explicit_path && i == 0) { /* -d/-A given (always cands[0]): never fall back to a default */
        say("  %s: %s\n", cands[i], strerror(errno));
        break;
      }
      continue;
    }
    h = dlopen(cands[i], RTLD_NOW | RTLD_LOCAL);
    if (h) {
      used = cands[i];
      break;
    }
    say("  dlopen(%s) failed: %s\n", cands[i], dlerror());
    if (explicit_path) break; /* explicit path: do not silently fall back */
  }
  if (!h) {
    say("  %s: no loadable Vulkan ICD found\n", what);
    return NULL;
  }
  say("== %s ICD %s\n", what, used);
  typedef VkResult(VKAPI_PTR * PFN_negotiate)(uint32_t *);
  PFN_negotiate neg = (PFN_negotiate)dlsym(h, "vk_icdNegotiateLoaderICDInterfaceVersion");
  PFN_vkGetInstanceProcAddr gp = force_hmi ? NULL : (PFN_vkGetInstanceProcAddr)dlsym(h, "vk_icdGetInstanceProcAddr");
  if (gp) {
    if (neg) {
      uint32_t ver = 5;
      neg(&ver);
      say("  entry: vk_icdGetInstanceProcAddr (loader interface v%u)\n", ver);
    } else {
      say("  entry: vk_icdGetInstanceProcAddr\n");
    }
    return gp;
  }
  /* Android HAL module path, exactly what libvulkan does */
  struct hal_module *hmi = (struct hal_module *)dlsym(h, "HMI");
  if (!hmi || !hmi->methods || !hmi->methods->open) {
    say("  ERROR: %s exports neither vk_icdGetInstanceProcAddr nor HMI\n", used);
    return NULL;
  }
  struct hal_device *dev = NULL;
  int r = hmi->methods->open(hmi, "vk0", &dev);
  if (r || !dev) {
    say("  ERROR: HMI open(vk0) failed: %d\n", r);
    return NULL;
  }
  say("  entry: HMI '%s' by '%s' (hwvulkan HAL)\n", hmi->name ? hmi->name : "?", hmi->author ? hmi->author : "?");
  return ((struct hal_vulkan_device *)dev)->GetInstanceProcAddr;
}

static int load_icd(void) {
  const char *cands[] = {g_icd,
                         getenv("NVK_ICD"),
                         "/vendor/lib64/hw/vulkan.nouveau.so",
                         "/odm/dgpu/lib64/hw/vulkan.nouveau.so",
                         "./vulkan.nouveau.so",
                         "./libvulkan_nouveau.so",
                         (const char *)-1};
  gipa = load_icd_file(cands, g_icd != NULL, g_force_hmi, "NVK");
  if (!gipa) say("ERROR: no loadable Vulkan ICD found (use -d /path/to/vulkan.nouveau.so)\n");
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
IFN(GetPhysicalDeviceImageFormatProperties2);
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
DFN(CreateSemaphore);
DFN(DestroySemaphore);

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
  L(GetPhysicalDeviceImageFormatProperties2);
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
  L(CreateSemaphore);
  L(DestroySemaphore);
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
    C(VK_ERROR_SURFACE_LOST_KHR);
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
          "check: performance base attached, nouveau.ko loaded & bound to 10de:1f91 (vendor.gb.nouveau.result), "
          "a second /dev/dri/renderD* driven by nouveau, GSP firmware nvidia/tu117/gsp/*-570.144.bin loaded "
          "(dmesg | grep nouveau).\n");
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

/* Render pass + pipeline + command buffer. pattern=0: the triangle; pattern=1: full-screen pattern (pat.frag).
 * Creating this first means every test mode reaches its first device call (NAK compile) before any allocation. */
static int make_rt_core(struct rt *t, VkImageLayout final_layout, int pattern) {
  VkAttachmentDescription att = {.format = VK_FORMAT_R8G8B8A8_UNORM,
                                 .samples = VK_SAMPLE_COUNT_1_BIT,
                                 .loadOp = pattern ? VK_ATTACHMENT_LOAD_OP_DONT_CARE : VK_ATTACHMENT_LOAD_OP_CLEAR,
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
  VkShaderModule vs, fs;
  VkShaderModuleCreateInfo smci = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
                                   .codeSize = pattern ? sizeof fs_vert_spv : sizeof tri_vert_spv,
                                   .pCode = pattern ? fs_vert_spv : tri_vert_spv};
  CHECK(vkCreateShaderModule(g_dev, &smci, NULL, &vs));
  smci.codeSize = pattern ? sizeof pat_frag_spv : sizeof tri_frag_spv;
  smci.pCode = pattern ? pat_frag_spv : tri_frag_spv;
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
  say("  %s pipeline compiled in %.1f ms\n", pattern ? "pattern" : "triangle", now_ms() - t0);
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

static int make_rt_target(struct rt *t, VkImage img) {
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
  return 0;
}

static int make_pipeline(struct rt *t, VkImage img, VkImageLayout final_layout) {
  if (make_rt_core(t, final_layout, 0)) return 1;
  return make_rt_target(t, img);
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

/* pat.frag reference value of texel (x, y), bytes R,G,B,A. */
__attribute__((unused)) static void pattern_px(uint32_t x, uint32_t y, uint8_t out[4]) {
  out[0] = (uint8_t)(x & 255u);
  out[1] = (uint8_t)(y & 255u);
  out[2] = (uint8_t)((x ^ y) & 255u);
  out[3] = (uint8_t)((x + 3u * y) & 255u);
}

/* Bit-exact check of the pattern. row_bytes = row pitch in bytes. Returns 0 on pass. */
__attribute__((unused)) static int verify_pattern(const uint8_t *px, size_t row_bytes, const char *tag) {
  long bad = 0;
  int fx = -1, fy = -1;
  uint8_t got[4] = {0}, want[4] = {0};
  for (int y = 0; y < H; y++)
    for (int x = 0; x < W; x++) {
      const uint8_t *p = px + (size_t)y * row_bytes + (size_t)x * 4;
      uint8_t e[4];
      pattern_px((uint32_t)x, (uint32_t)y, e);
      if (memcmp(p, e, 4)) {
        if (!bad) {
          fx = x;
          fy = y;
          memcpy(got, p, 4);
          memcpy(want, e, 4);
        }
        bad++;
      }
    }
  if (bad)
    say("  [%s] %ld of %d texels differ; first at (%d,%d): got (%u,%u,%u,%u) want (%u,%u,%u,%u) -> BIT-EXACT FAIL\n",
        tag, bad, W * H, fx, fy, got[0], got[1], got[2], got[3], want[0], want[1], want[2], want[3]);
  else
    say("  [%s] all %d texels bit-exact -> PASS\n", tag, W * H);
  return bad ? 1 : 0;
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

/* ---------- test: offscreen optimal image + copy to buffer ---------- */
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
/* ---------- Android: gralloc (AHardwareBuffer) -> VkNativeBufferANDROID -> NVK, as Android's swapchain does ------- */
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

/* Prints the minigbm layout; returns the modifier or UINT64_MAX when unknown. */
static uint64_t print_gralloc_layout(const native_handle_t *nh) {
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
  if (!gm || !gm->perform || !nh) return UINT64_MAX;
  struct cros_gralloc0_buffer_info bi;
  memset(&bi, 0, sizeof bi);
  if (gm->perform(gm, CROS_GRALLOC_DRM_GET_BUFFER_INFO, nh, &bi) == 0) {
    say("  minigbm layout: fourcc=%.4s planes=%d modifier=0x%016" PRIx64 " (%s) stride0=%d offset0=%d\n",
        (const char *)&bi.drm_fourcc, bi.num_fds, bi.modifier, mod_name(bi.modifier), bi.stride[0], bi.offset[0]);
    return bi.modifier;
  }
  say("  minigbm layout: perform(GET_BUFFER_INFO) failed\n");
  return UINT64_MAX;
}

static PFN_getNativeHandle get_native_handle_fn(void) {
  void *nw = dlopen("libnativewindow.so", RTLD_NOW);
  PFN_getNativeHandle f = nw ? (PFN_getNativeHandle)dlsym(nw, "AHardwareBuffer_getNativeHandle") : NULL;
  if (!f) say("  FAIL: AHardwareBuffer_getNativeHandle not found in libnativewindow.so\n");
  return f;
}

/* Allocates a W x H RGBA8 AHB with exactly `usage` and prints its layout. */
static AHardwareBuffer *alloc_ahb(uint64_t usage, AHardwareBuffer_Desc *desc, const native_handle_t **nh,
                                  uint64_t *modifier) {
  PFN_getNativeHandle get_nh = get_native_handle_fn();
  if (!get_nh) return NULL;
  AHardwareBuffer_Desc d = {.width = W, .height = H, .layers = 1, .format = AHARDWAREBUFFER_FORMAT_R8G8B8A8_UNORM,
                            .usage = usage};
  AHardwareBuffer *ahb = NULL;
  int ar = AHardwareBuffer_allocate(&d, &ahb);
  if (ar || !ahb) {
    say("  FAIL: AHardwareBuffer_allocate(usage=0x%" PRIx64 ") -> %d (gralloc allocator unavailable?)\n", usage, ar);
    return NULL;
  }
  AHardwareBuffer_describe(ahb, &d);
  *desc = d;
  *nh = get_nh(ahb);
  say("  AHB %ux%u stride=%u px usage=0x%" PRIx64 "; native_handle numFds=%d numInts=%d\n", d.width, d.height,
      d.stride, (uint64_t)d.usage, *nh ? (*nh)->numFds : -1, *nh ? (*nh)->numInts : -1);
  *modifier = print_gralloc_layout(*nh);
  return ahb;
}

/* VkNativeBufferANDROID import, like libvulkan's CreateSwapchainKHR (OPTIMAL tiling, usage/usage2/usage3/ahb). */
static VkResult import_anb(AHardwareBuffer *ahb, const native_handle_t *nh, const AHardwareBuffer_Desc *desc,
                           uint64_t usage, int with_swapchain_info, VkImageUsageFlags img_usage, VkImage *img) {
  VkSwapchainImageCreateInfoANDROID sci = {.sType = VK_STRUCTURE_TYPE_SWAPCHAIN_IMAGE_CREATE_INFO_ANDROID,
                                           .usage = 0};
  VkNativeBufferANDROID nb = {.sType = VK_STRUCTURE_TYPE_NATIVE_BUFFER_ANDROID,
                              .pNext = with_swapchain_info ? &sci : NULL,
                              .handle = nh,
                              .stride = (int)desc->stride,
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
                           .usage = img_usage,
                           .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED};
  VkResult r = vkCreateImage(g_dev, &ici, NULL, img);
  say("  vkCreateImage(VkNativeBufferANDROID%s) -> %s\n", with_swapchain_info ? " + SwapchainImageCreateInfo" : "",
      vkr(r));
  return r;
}

/* Waits for a sync_file fd (poll POLLIN). Returns 0 signalled, 1 timeout/error. Closes the fd. */
static int wait_sync_fd(int fd, int timeout_ms, const char *what) {
  if (fd < 0) {
    say("  %s: fence fd -1 (nothing to wait for)\n", what);
    return 0;
  }
  struct pollfd pfd = {.fd = fd, .events = POLLIN};
  double t0 = now_ms();
  int pr = poll(&pfd, 1, timeout_ms);
  say("  %s: sync_fd %d poll -> %d (%s) after %.2f ms\n", what, fd, pr,
      pr > 0 ? "signalled" : pr == 0 ? "TIMEOUT" : strerror(errno), now_ms() - t0);
  close(fd);
  return pr > 0 ? 0 : 1;
}

static int test_anb(int linear) {
  const char *tag = linear ? "anb-linear" : "anb-swapchain";
  say("== test %s: gralloc buffer from the system allocator imported with VkNativeBufferANDROID\n", tag);
  if (!g_has_anb) {
    say("  SKIP/FAIL: driver does not expose VK_ANDROID_native_buffer (u_gralloc found no gralloc: needs "
        "ro.hardware.gralloc=minigbm and /vendor/lib64/hw/gralloc.minigbm.so readable)\n");
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
  struct rt t;
  memset(&t, 0, sizeof t);
  if (make_rt_core(&t, VK_IMAGE_LAYOUT_GENERAL, 0)) return 1;
  uint64_t usage = cons | prod | AHARDWAREBUFFER_USAGE_GPU_COLOR_OUTPUT | AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE |
                   AHARDWAREBUFFER_USAGE_COMPOSER_OVERLAY;
  if (linear) usage |= AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN;
  AHardwareBuffer_Desc desc;
  const native_handle_t *nh = NULL;
  uint64_t mod;
  AHardwareBuffer *ahb = alloc_ahb(usage, &desc, &nh, &mod);
  if (!ahb) return 1;
  VkImage img;
  VkResult r = import_anb(ahb, nh, &desc, usage, 0,
                          VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT, &img);
  if (r != VK_SUCCESS) {
    say("  %s: FAIL (NVK could not import this gralloc buffer: typically an Intel tiling modifier from minigbm's "
        "i915 backend, which NVIDIA hardware cannot address)\n", tag);
    AHardwareBuffer_release(ahb);
    return 1;
  }
  if (make_rt_target(&t, img)) return 1;
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
    say("  vkQueueSignalReleaseImageANDROID -> %s fence_fd=%d (unpatched NVK 26.3 waits idle and returns -1)\n",
        vkr(r), fence_fd);
    if (fence_fd >= 0) close(fence_fd);
  }
  CHECK(vkQueueWaitIdle(g_queue));
  say("  render into gralloc buffer + wait %.2f ms\n", now_ms() - t0);
  int fail = 0;
  if (linear) {
    void *p = NULL;
    int ar = AHardwareBuffer_lock(ahb, AHARDWAREBUFFER_USAGE_CPU_READ_OFTEN, -1, NULL, &p);
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

/* Renders the pattern into an imported ANB image, releases it to the "presentation engine" with
 * vkQueueSignalReleaseImageANDROID and waits for the returned sync_fd. wait_sem: also signal a semaphore in the
 * render submit and hand it to the release call (what vkQueuePresentKHR does with its pWaitSemaphores).
 * Returns 0 on success, 1 on failure. On success the rendering is complete when this returns. */
static int render_pattern_and_release(struct rt *t, VkImage img, int wait_sem, const char *tag) {
  VkCommandBufferBeginInfo bi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
  CHECK(vkBeginCommandBuffer(t->cb, &bi));
  record_draw(t);
  /* Release the image to the foreign (gralloc consumer) queue family, like a WSI present. */
  VkImageMemoryBarrier rel_b = {.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
                                .srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT,
                                .dstAccessMask = 0,
                                .oldLayout = VK_IMAGE_LAYOUT_GENERAL,
                                .newLayout = VK_IMAGE_LAYOUT_GENERAL,
                                .srcQueueFamilyIndex = g_qf,
                                .dstQueueFamilyIndex = VK_QUEUE_FAMILY_FOREIGN_EXT,
                                .image = img,
                                .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
  vkCmdPipelineBarrier(t->cb, VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, 0,
                       0, NULL, 0, NULL, 1, &rel_b);
  CHECK(vkEndCommandBuffer(t->cb));
  VkSemaphore sem = VK_NULL_HANDLE;
  if (wait_sem) {
    VkSemaphoreCreateInfo sci = {.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO};
    CHECK(vkCreateSemaphore(g_dev, &sci, NULL, &sem));
  }
  VkSubmitInfo si = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO,
                     .commandBufferCount = 1,
                     .pCommandBuffers = &t->cb,
                     .signalSemaphoreCount = wait_sem ? 1 : 0,
                     .pSignalSemaphores = &sem};
  double t0 = now_ms();
  CHECK(vkQueueSubmit(g_queue, 1, &si, VK_NULL_HANDLE));
  PFN_vkQueueSignalReleaseImageANDROID rel =
      (PFN_vkQueueSignalReleaseImageANDROID)vkGetDeviceProcAddr(g_dev, "vkQueueSignalReleaseImageANDROID");
  int fail = 0;
  if (!rel) {
    say("  [%s] vkQueueSignalReleaseImageANDROID missing\n", tag);
    fail = 1;
  } else {
    int fence_fd = -2;
    VkResult r = rel(g_queue, wait_sem ? 1 : 0, &sem, img, &fence_fd);
    say("  [%s] vkQueueSignalReleaseImageANDROID(%d wait semaphore(s)) -> %s fence_fd=%d%s\n", tag,
        wait_sem ? 1 : 0, vkr(r), fence_fd,
        fence_fd >= 0 ? " (sync_fd: patch 0006)" : " (-1: QueueWaitIdle path, unpatched or fallback)");
    if (r != VK_SUCCESS || fence_fd < -1) fail = 1;
    else if (wait_sync_fd(fence_fd, 5000, tag)) fail = 1;
  }
  say("  [%s] render + release %.2f ms\n", tag, now_ms() - t0);
  /* Read-backs below happen BEFORE QueueWaitIdle, so they also prove that the sync_fd covered the rendering. */
  if (sem) {
    /* the release consumed the wait; destroy after the queue is idle */
    vkQueueWaitIdle(g_queue);
    vkDestroySemaphore(g_dev, sem, NULL);
  }
  return fail;
}

/* ---------- ANV (Intel) read-back leg: import the AHB into vulkan.intel.so and texelFetch every texel ---------- */
struct anv {
  PFN_vkGetInstanceProcAddr gipa;
  VkInstance inst;
  VkPhysicalDevice pd;
  VkPhysicalDeviceMemoryProperties mem;
  VkDevice dev;
  VkQueue q;
  uint32_t qf;
  PFN_vkGetDeviceProcAddr gdpa;
};

#define ANV_I(a, name) ((PFN_vk##name)(a)->gipa((a)->inst, "vk" #name))
#define ANV_D(a, name) ((PFN_vk##name)(a)->gdpa((a)->dev, "vk" #name))
#define ACHECK(call)                                                                     \
  do {                                                                                   \
    VkResult _r = (call);                                                                \
    if (_r != VK_SUCCESS) {                                                              \
      say("  [anv] FAIL: %s -> %s (%d) [line %d]\n", #call, vkr(_r), _r, __LINE__);      \
      return 1;                                                                          \
    }                                                                                    \
  } while (0)

/* Returns 0 ok, 2 = ANV not available (no ICD / no Intel device), 1 = error. */
static int anv_open(struct anv *a) {
  memset(a, 0, sizeof *a);
  const char *cands[] = {g_anv_icd, getenv("NVK_PROBE_ANV"), "/vendor/lib64/hw/vulkan.intel.so", (const char *)-1};
  a->gipa = load_icd_file(cands, g_anv_icd != NULL, 0, "ANV (read-back leg)");
  if (!a->gipa) return 2;
  PFN_vkCreateInstance ci = (PFN_vkCreateInstance)a->gipa(NULL, "vkCreateInstance");
  if (!ci) return 2;
  VkApplicationInfo app = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
                           .pApplicationName = "nvk_probe readback",
                           .apiVersion = VK_API_VERSION_1_1};
  VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app};
  ACHECK(ci(&ici, NULL, &a->inst));
  uint32_t n = 0;
  ANV_I(a, EnumeratePhysicalDevices)(a->inst, &n, NULL);
  VkPhysicalDevice pds[8];
  if (n > 8) n = 8;
  ANV_I(a, EnumeratePhysicalDevices)(a->inst, &n, pds);
  for (uint32_t i = 0; i < n && !a->pd; i++) {
    VkPhysicalDeviceProperties p;
    ANV_I(a, GetPhysicalDeviceProperties)(pds[i], &p);
    say("  [anv] device [%u] %s vendor=0x%04x\n", i, p.deviceName, p.vendorID);
    if (p.vendorID == 0x8086) a->pd = pds[i];
  }
  if (!a->pd) {
    say("  [anv] no Intel Vulkan device (%u device(s))\n", n);
    return 2;
  }
  ANV_I(a, GetPhysicalDeviceMemoryProperties)(a->pd, &a->mem);
  uint32_t nq = 0;
  ANV_I(a, GetPhysicalDeviceQueueFamilyProperties)(a->pd, &nq, NULL);
  VkQueueFamilyProperties qf[8];
  if (nq > 8) nq = 8;
  ANV_I(a, GetPhysicalDeviceQueueFamilyProperties)(a->pd, &nq, qf);
  a->qf = UINT32_MAX;
  for (uint32_t i = 0; i < nq && a->qf == UINT32_MAX; i++)
    if (qf[i].queueFlags & VK_QUEUE_COMPUTE_BIT) a->qf = i;
  if (a->qf == UINT32_MAX) return 1;
  float prio = 1.0f;
  VkDeviceQueueCreateInfo qci = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
                                 .queueFamilyIndex = a->qf,
                                 .queueCount = 1,
                                 .pQueuePriorities = &prio};
  const char *ext[] = {"VK_ANDROID_external_memory_android_hardware_buffer", "VK_EXT_queue_family_foreign"};
  VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
                            .queueCreateInfoCount = 1,
                            .pQueueCreateInfos = &qci,
                            .enabledExtensionCount = 2,
                            .ppEnabledExtensionNames = ext};
  ACHECK(ANV_I(a, CreateDevice)(a->pd, &dci, NULL, &a->dev));
  a->gdpa = (PFN_vkGetDeviceProcAddr)a->gipa(a->inst, "vkGetDeviceProcAddr");
  ANV_D(a, GetDeviceQueue)(a->dev, a->qf, 0, &a->q);
  return 0;
}

static int anv_find_mem(const struct anv *a, uint32_t bits, VkMemoryPropertyFlags want) {
  for (uint32_t i = 0; i < a->mem.memoryTypeCount; i++)
    if ((bits & (1u << i)) && (a->mem.memoryTypes[i].propertyFlags & want) == want) return (int)i;
  return -1;
}

/* Samples the AHB on ANV and compares with the pattern. Returns 0 pass, 1 fail, 2 ANV unavailable. */
static int anv_readback(AHardwareBuffer *ahb, const char *tag) {
  struct anv a;
  int rc = anv_open(&a);
  if (rc) return rc;
  VkAndroidHardwareBufferFormatPropertiesANDROID fp = {
      .sType = VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_FORMAT_PROPERTIES_ANDROID};
  VkAndroidHardwareBufferPropertiesANDROID hp = {.sType = VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_PROPERTIES_ANDROID,
                                                 .pNext = &fp};
  ACHECK(ANV_D(&a, GetAndroidHardwareBufferPropertiesANDROID)(a.dev, ahb, &hp));
  say("  [anv] AHB properties: allocationSize=%" PRIu64 " memoryTypeBits=0x%x format=%d\n",
      (uint64_t)hp.allocationSize, hp.memoryTypeBits, fp.format);
  VkExternalMemoryImageCreateInfo emi = {.sType = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO,
                                         .handleTypes =
                                             VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID};
  VkImageCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
                           .pNext = &emi,
                           .imageType = VK_IMAGE_TYPE_2D,
                           .format = VK_FORMAT_R8G8B8A8_UNORM,
                           .extent = {W, H, 1},
                           .mipLevels = 1,
                           .arrayLayers = 1,
                           .samples = VK_SAMPLE_COUNT_1_BIT,
                           .tiling = VK_IMAGE_TILING_OPTIMAL,
                           .usage = VK_IMAGE_USAGE_SAMPLED_BIT,
                           .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED};
  VkImage img;
  ACHECK(ANV_D(&a, CreateImage)(a.dev, &ici, NULL, &img));
  VkImportAndroidHardwareBufferInfoANDROID imp = {.sType = VK_STRUCTURE_TYPE_IMPORT_ANDROID_HARDWARE_BUFFER_INFO_ANDROID,
                                                  .buffer = ahb};
  VkMemoryDedicatedAllocateInfo ded = {.sType = VK_STRUCTURE_TYPE_MEMORY_DEDICATED_ALLOCATE_INFO,
                                       .pNext = &imp,
                                       .image = img};
  int mt = anv_find_mem(&a, hp.memoryTypeBits, 0);
  if (mt < 0) {
    say("  [anv] FAIL: no memory type for the AHB\n");
    return 1;
  }
  VkMemoryAllocateInfo mai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
                              .pNext = &ded,
                              .allocationSize = hp.allocationSize,
                              .memoryTypeIndex = (uint32_t)mt};
  VkDeviceMemory imem;
  ACHECK(ANV_D(&a, AllocateMemory)(a.dev, &mai, NULL, &imem));
  ACHECK(ANV_D(&a, BindImageMemory)(a.dev, img, imem, 0));
  VkImageViewCreateInfo vci = {.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO,
                               .image = img,
                               .viewType = VK_IMAGE_VIEW_TYPE_2D,
                               .format = VK_FORMAT_R8G8B8A8_UNORM,
                               .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
  VkImageView view;
  ACHECK(ANV_D(&a, CreateImageView)(a.dev, &vci, NULL, &view));
  VkSamplerCreateInfo smi = {.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO,
                             .magFilter = VK_FILTER_NEAREST,
                             .minFilter = VK_FILTER_NEAREST,
                             .addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
                             .addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE,
                             .addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE};
  VkSampler smp;
  ACHECK(ANV_D(&a, CreateSampler)(a.dev, &smi, NULL, &smp));
  /* output buffer */
  VkBufferCreateInfo bci = {.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
                            .size = (VkDeviceSize)W * H * 4,
                            .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT};
  VkBuffer buf;
  ACHECK(ANV_D(&a, CreateBuffer)(a.dev, &bci, NULL, &buf));
  VkMemoryRequirements mr;
  ANV_D(&a, GetBufferMemoryRequirements)(a.dev, buf, &mr);
  int bmt = anv_find_mem(&a, mr.memoryTypeBits, VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
  if (bmt < 0) return 1;
  VkMemoryAllocateInfo bmai = {.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
                               .allocationSize = mr.size,
                               .memoryTypeIndex = (uint32_t)bmt};
  VkDeviceMemory bmem;
  ACHECK(ANV_D(&a, AllocateMemory)(a.dev, &bmai, NULL, &bmem));
  ACHECK(ANV_D(&a, BindBufferMemory)(a.dev, buf, bmem, 0));
  /* compute pipeline */
  VkDescriptorSetLayoutBinding b[2] = {
      {.binding = 0, .descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, .descriptorCount = 1,
       .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT},
      {.binding = 1, .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .descriptorCount = 1,
       .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT}};
  VkDescriptorSetLayoutCreateInfo dli = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
                                         .bindingCount = 2,
                                         .pBindings = b};
  VkDescriptorSetLayout dsl;
  ACHECK(ANV_D(&a, CreateDescriptorSetLayout)(a.dev, &dli, NULL, &dsl));
  VkPushConstantRange pcr = {.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT, .offset = 0, .size = 8};
  VkPipelineLayoutCreateInfo pli = {.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
                                    .setLayoutCount = 1,
                                    .pSetLayouts = &dsl,
                                    .pushConstantRangeCount = 1,
                                    .pPushConstantRanges = &pcr};
  VkPipelineLayout pl;
  ACHECK(ANV_D(&a, CreatePipelineLayout)(a.dev, &pli, NULL, &pl));
  VkShaderModuleCreateInfo smci = {.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
                                   .codeSize = sizeof readback_comp_spv,
                                   .pCode = readback_comp_spv};
  VkShaderModule cs;
  ACHECK(ANV_D(&a, CreateShaderModule)(a.dev, &smci, NULL, &cs));
  VkComputePipelineCreateInfo cpi = {.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
                                     .stage = {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
                                               .stage = VK_SHADER_STAGE_COMPUTE_BIT,
                                               .module = cs,
                                               .pName = "main"},
                                     .layout = pl};
  VkPipeline pipe;
  ACHECK(ANV_D(&a, CreateComputePipelines)(a.dev, VK_NULL_HANDLE, 1, &cpi, NULL, &pipe));
  VkDescriptorPoolSize ps[2] = {{VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 1}, {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1}};
  VkDescriptorPoolCreateInfo dpi = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO,
                                    .maxSets = 1,
                                    .poolSizeCount = 2,
                                    .pPoolSizes = ps};
  VkDescriptorPool dp;
  ACHECK(ANV_D(&a, CreateDescriptorPool)(a.dev, &dpi, NULL, &dp));
  VkDescriptorSetAllocateInfo dsai = {.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
                                      .descriptorPool = dp,
                                      .descriptorSetCount = 1,
                                      .pSetLayouts = &dsl};
  VkDescriptorSet ds;
  ACHECK(ANV_D(&a, AllocateDescriptorSets)(a.dev, &dsai, &ds));
  VkDescriptorImageInfo dii = {.sampler = smp, .imageView = view, .imageLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL};
  VkDescriptorBufferInfo dbi = {.buffer = buf, .offset = 0, .range = VK_WHOLE_SIZE};
  VkWriteDescriptorSet wr[2] = {
      {.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = ds, .dstBinding = 0, .descriptorCount = 1,
       .descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, .pImageInfo = &dii},
      {.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = ds, .dstBinding = 1, .descriptorCount = 1,
       .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &dbi}};
  ANV_D(&a, UpdateDescriptorSets)(a.dev, 2, wr, 0, NULL);
  /* commands */
  VkCommandPoolCreateInfo cpci = {.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = a.qf};
  VkCommandPool pool;
  ACHECK(ANV_D(&a, CreateCommandPool)(a.dev, &cpci, NULL, &pool));
  VkCommandBufferAllocateInfo cbai = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO,
                                      .commandPool = pool,
                                      .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY,
                                      .commandBufferCount = 1};
  VkCommandBuffer cb;
  ACHECK(ANV_D(&a, AllocateCommandBuffers)(a.dev, &cbai, &cb));
  /* AHB contents are owned by the foreign producer: queue family foreign acquire. ANV must not discard them; the
   * buffer is LINEAR (no aux surface), so the GENERAL -> SHADER_READ_ONLY transition is a no-op on the data. */
  VkImageMemoryBarrier acq = {.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER,
                              .srcAccessMask = 0,
                              .dstAccessMask = VK_ACCESS_SHADER_READ_BIT,
                              .oldLayout = VK_IMAGE_LAYOUT_GENERAL,
                              .newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL,
                              .srcQueueFamilyIndex = VK_QUEUE_FAMILY_FOREIGN_EXT,
                              .dstQueueFamilyIndex = a.qf,
                              .image = img,
                              .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1}};
  VkBufferMemoryBarrier hb = {.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
                              .srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT,
                              .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
                              .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                              .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                              .buffer = buf,
                              .offset = 0,
                              .size = VK_WHOLE_SIZE};
  VkCommandBufferBeginInfo bi = {.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
  ACHECK(ANV_D(&a, BeginCommandBuffer)(cb, &bi));
  ANV_D(&a, CmdPipelineBarrier)(cb, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 0, NULL,
                                0, NULL, 1, &acq);
  ANV_D(&a, CmdBindPipeline)(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pipe);
  ANV_D(&a, CmdBindDescriptorSets)(cb, VK_PIPELINE_BIND_POINT_COMPUTE, pl, 0, 1, &ds, 0, NULL);
  uint32_t pc[2] = {W, H};
  ANV_D(&a, CmdPushConstants)(cb, pl, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof pc, pc);
  ANV_D(&a, CmdDispatch)(cb, (W + 15) / 16, (H + 15) / 16, 1);
  ANV_D(&a, CmdPipelineBarrier)(cb, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, 0, NULL, 1, &hb,
                                0, NULL);
  ACHECK(ANV_D(&a, EndCommandBuffer)(cb));
  VkSubmitInfo si = {.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &cb};
  ACHECK(ANV_D(&a, QueueSubmit)(a.q, 1, &si, VK_NULL_HANDLE));
  ACHECK(ANV_D(&a, QueueWaitIdle)(a.q));
  void *map = NULL;
  ACHECK(ANV_D(&a, MapMemory)(a.dev, bmem, 0, VK_WHOLE_SIZE, 0, &map));
  int fail = verify_pattern((const uint8_t *)map, W * 4, tag);
  ANV_D(&a, UnmapMemory)(a.dev, bmem);
  ANV_D(&a, DestroyDevice)(a.dev, NULL);
  ANV_I(&a, DestroyInstance)(a.inst, NULL);
  return fail;
}

/* ---------- test: gate G0, ahb-i915-readback ---------- */
static int test_ahb_i915_readback(void) {
  const char *tag = "ahb-i915-readback";
  say("== test %s: gralloc (i915) AHB -> NVK render -> bit-exact read-back by CPU lock and by ANV sampling\n", tag);
  if (!g_has_anb) {
    say("  FAIL: driver does not expose VK_ANDROID_native_buffer (no u_gralloc)\n");
    return 1;
  }
  struct rt t;
  memset(&t, 0, sizeof t);
  if (make_rt_core(&t, VK_IMAGE_LAYOUT_GENERAL, 1)) return 1; /* first device call: NAK compile of the pattern */
  /* Plan G0 usage + CPU_READ_RARELY: AHardwareBuffer_lock(CPU_READ) requires a CPU read usage at allocation time;
   * both are SW usage bits, so minigbm's i915 backend picks LINEAR either way. */
  const uint64_t usage = AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY | AHARDWAREBUFFER_USAGE_CPU_READ_RARELY |
                         AHARDWAREBUFFER_USAGE_GPU_COLOR_OUTPUT | AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE;
  AHardwareBuffer_Desc desc;
  const native_handle_t *nh = NULL;
  uint64_t mod;
  AHardwareBuffer *ahb = alloc_ahb(usage, &desc, &nh, &mod);
  if (!ahb) return 1;
  if (mod != 0 && mod != UINT64_MAX) say("  NOTE: minigbm did not pick LINEAR for a SW-usage buffer\n");
  VkImage img;
  VkResult r = import_anb(ahb, nh, &desc, usage, 0, VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_SAMPLED_BIT,
                          &img);
  if (r != VK_SUCCESS) {
    say("  %s: FAIL (import into NVK)\n", tag);
    AHardwareBuffer_release(ahb);
    return 1;
  }
  if (make_rt_target(&t, img)) return 1;
  int rel_fail = render_pattern_and_release(&t, img, 0, tag);
  /* leg 1: CPU */
  void *p = NULL;
  int ar = AHardwareBuffer_lock(ahb, AHARDWAREBUFFER_USAGE_CPU_READ_RARELY, -1, NULL, &p);
  int cpu_fail = 1;
  if (ar || !p) {
    say("  [cpu] FAIL: AHardwareBuffer_lock -> %d\n", ar);
  } else {
    cpu_fail = verify_pattern((const uint8_t *)p, (size_t)desc.stride * 4, "cpu lock read-back");
    AHardwareBuffer_unlock(ahb, NULL);
  }
  CHECK(vkQueueWaitIdle(g_queue));
  /* leg 2: ANV sampling */
  int anv = anv_readback(ahb, "anv texelFetch read-back");
  if (anv == 2) say("  [anv] FAIL: ANV leg unavailable (needs /vendor/lib64/hw/vulkan.intel.so and an Intel GPU)\n");
  destroy_rt(&t);
  vkDestroyImage(g_dev, img, NULL);
  AHardwareBuffer_release(ahb);
  int fail = rel_fail || cpu_fail || anv != 0;
  say("  %s: release=%s cpu=%s anv=%s -> %s\n", tag, rel_fail ? "FAIL" : "ok", cpu_fail ? "FAIL" : "PASS",
      anv == 0 ? "PASS" : anv == 2 ? "UNAVAILABLE" : "FAIL", fail ? "FAIL" : "PASS");
  return fail;
}

/* ---------- test: Android 17 libvulkan swapchain model ---------- */
static uint64_t gralloc1_to_buffer_usage(uint64_t producer, uint64_t consumer) {
  /* libvulkan swapchain.cpp convertGralloc1ToBufferUsage(): only the *_OFTEN CPU bits differ between gralloc1 and
   * BufferUsage; CPU_READ_OFTEN = 0x6 -> 0x3, CPU_WRITE_OFTEN = 0x60 -> 0x30. */
  uint64_t merged = producer | consumer;
  if ((merged & 0x6) == 0x6) merged = (merged & ~0x6ull) | 0x3;
  if ((merged & 0x60) == 0x60) merged = (merged & ~0x60ull) | 0x30;
  return merged;
}

static int test_a17_swapchain_model(void) {
  const char *tag = "a17-swapchain-model";
  say("== test %s: Android 17 libvulkan CreateSwapchainKHR/QueuePresentKHR driver calls\n", tag);
  if (!g_has_anb) {
    say("  FAIL: driver does not expose VK_ANDROID_native_buffer (libvulkan's CreateDevice would fail)\n");
    return 1;
  }
  const VkFormat fmt = VK_FORMAT_R8G8B8A8_UNORM;
  const VkImageUsageFlags img_usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
  /* 1. getProducerUsageGPDIFP2: AHB-typed format query (swapchain.cpp:1654-1760) */
  uint64_t producer = 0;
  int have_producer = 0;
  if (vkGetPhysicalDeviceImageFormatProperties2) {
    VkPhysicalDeviceExternalImageFormatInfo ext = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO,
        .handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_ANDROID_HARDWARE_BUFFER_BIT_ANDROID};
    VkPhysicalDeviceImageFormatInfo2 fi = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2,
                                           .pNext = &ext,
                                           .format = fmt,
                                           .type = VK_IMAGE_TYPE_2D,
                                           .tiling = VK_IMAGE_TILING_OPTIMAL,
                                           .usage = img_usage};
    VkAndroidHardwareBufferUsageANDROID au = {.sType = VK_STRUCTURE_TYPE_ANDROID_HARDWARE_BUFFER_USAGE_ANDROID};
    VkImageFormatProperties2 fp = {.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2, .pNext = &au};
    VkResult r = vkGetPhysicalDeviceImageFormatProperties2(g_pd, &fi, &fp);
    say("  GPDIFP2(AHB handle type) -> %s%s", vkr(r),
        r == VK_SUCCESS ? "" : " (expected without patch 0003: libvulkan falls through to the gralloc path)\n");
    if (r == VK_SUCCESS) {
      say(" androidHardwareBufferUsage=0x%" PRIx64 "%s\n", (uint64_t)au.androidHardwareBufferUsage,
          (au.androidHardwareBufferUsage & AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY) ? " (+CPU_WRITE_RARELY: 0004)" : "");
      producer = au.androidHardwareBufferUsage;
      have_producer = 1;
    }
  } else {
    say("  vkGetPhysicalDeviceImageFormatProperties2 not exposed\n");
  }
  struct rt t;
  memset(&t, 0, sizeof t);
  if (!have_producer) {
    /* 2. gralloc fallback, same order as libvulkan: Usage4 > Usage3 > Usage2 > Usage */
    PFN_vkGetSwapchainGrallocUsage4ANDROID u4 =
        (PFN_vkGetSwapchainGrallocUsage4ANDROID)vkGetDeviceProcAddr(g_dev, "vkGetSwapchainGrallocUsage4ANDROID");
    PFN_vkGetSwapchainGrallocUsage3ANDROID u3 =
        (PFN_vkGetSwapchainGrallocUsage3ANDROID)vkGetDeviceProcAddr(g_dev, "vkGetSwapchainGrallocUsage3ANDROID");
    PFN_vkGetSwapchainGrallocUsage2ANDROID u2 =
        (PFN_vkGetSwapchainGrallocUsage2ANDROID)vkGetDeviceProcAddr(g_dev, "vkGetSwapchainGrallocUsage2ANDROID");
    PFN_vkGetSwapchainGrallocUsageANDROID u1 =
        (PFN_vkGetSwapchainGrallocUsageANDROID)vkGetDeviceProcAddr(g_dev, "vkGetSwapchainGrallocUsageANDROID");
    VkResult r = VK_ERROR_INCOMPATIBLE_DRIVER;
    if (u4) {
      VkGrallocUsageInfo2ANDROID gi = {.sType = VK_STRUCTURE_TYPE_GRALLOC_USAGE_INFO_2_ANDROID,
                                       .format = fmt, .imageUsage = img_usage, .swapchainImageUsage = 0};
      r = u4(g_dev, &gi, &producer);
      say("  vkGetSwapchainGrallocUsage4ANDROID -> %s usage=0x%" PRIx64 "\n", vkr(r), producer);
    } else if (u3) {
      VkGrallocUsageInfoANDROID gi = {.sType = VK_STRUCTURE_TYPE_GRALLOC_USAGE_INFO_ANDROID,
                                      .format = fmt, .imageUsage = img_usage};
      r = u3(g_dev, &gi, &producer);
      say("  vkGetSwapchainGrallocUsage3ANDROID -> %s usage=0x%" PRIx64 "\n", vkr(r), producer);
    } else if (u2) {
      uint64_t c = 0, p = 0;
      r = u2(g_dev, fmt, img_usage, 0, &c, &p);
      producer = gralloc1_to_buffer_usage(p, c);
      say("  vkGetSwapchainGrallocUsage2ANDROID -> %s consumer=0x%" PRIx64 " producer=0x%" PRIx64
          " -> native usage 0x%" PRIx64 "%s\n", vkr(r), c, p, producer,
          (producer & AHARDWAREBUFFER_USAGE_CPU_WRITE_RARELY) ? " (+CPU_WRITE: patch 0004 LINEAR forcing)" :
                                                                " (no SW bit: minigbm will tile -> needs 0004)");
    } else if (u1) {
      int lu = 0;
      r = u1(g_dev, fmt, img_usage, &lu);
      producer = (uint64_t)(uint32_t)lu;
      say("  vkGetSwapchainGrallocUsageANDROID -> %s usage=0x%x\n", vkr(r), lu);
    } else {
      say("  FAIL: no vkGetSwapchainGrallocUsage*ANDROID (libvulkan's CreateDevice returns INCOMPATIBLE_DRIVER)\n");
    }
    if (r != VK_SUCCESS) {
      say("  %s: FAIL (libvulkan would return VK_ERROR_SURFACE_LOST_KHR)\n", tag);
      return 1;
    }
  }
  if (make_rt_core(&t, VK_IMAGE_LAYOUT_GENERAL, 1)) return 1;
  /* 3. consumer usage of an app window queue in SurfaceFlinger: HW_COMPOSER | HW_TEXTURE */
  const uint64_t consumer = AHARDWAREBUFFER_USAGE_COMPOSER_OVERLAY | AHARDWAREBUFFER_USAGE_GPU_SAMPLED_IMAGE;
  const uint64_t usage = consumer | producer;
  AHardwareBuffer_Desc desc;
  const native_handle_t *nh = NULL;
  uint64_t mod;
  AHardwareBuffer *ahb = alloc_ahb(usage, &desc, &nh, &mod);
  if (!ahb) return 1;
  VkImage img;
  VkResult r = import_anb(ahb, nh, &desc, usage, 1, img_usage, &img);
  if (r != VK_SUCCESS) {
    say("  %s: FAIL (swapchain image creation; with an Intel-tiled buffer this is blocker B2 -> patch 0004)\n", tag);
    AHardwareBuffer_release(ahb);
    return 1;
  }
  if (make_rt_target(&t, img)) return 1;
  /* 4. vkAcquireImageANDROID with no fence fd (buffer idle), signalling a VkFence like libvulkan's AcquireNextImage */
  PFN_vkAcquireImageANDROID acq = (PFN_vkAcquireImageANDROID)vkGetDeviceProcAddr(g_dev, "vkAcquireImageANDROID");
  int fail = 0;
  if (acq) {
    VkFenceCreateInfo fci = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkFence f;
    CHECK(vkCreateFence(g_dev, &fci, NULL, &f));
    r = acq(g_dev, img, -1, VK_NULL_HANDLE, f);
    VkResult wr = r == VK_SUCCESS ? vkWaitForFences(g_dev, 1, &f, VK_TRUE, 2000000000ull) : r;
    say("  vkAcquireImageANDROID(fd -1, fence) -> %s, fence wait -> %s\n", vkr(r), vkr(wr));
    if (r != VK_SUCCESS || wr != VK_SUCCESS) fail = 1;
    vkDestroyFence(g_dev, f, NULL);
  } else {
    say("  FAIL: vkAcquireImageANDROID missing\n");
    fail = 1;
  }
  /* 5. render + vkQueueSignalReleaseImageANDROID with the present's wait semaphore, wait for the sync_fd */
  if (render_pattern_and_release(&t, img, 1, tag)) fail = 1;
  /* 6. what SurfaceFlinger's RenderEngine (ANV) would sample */
  int anv = anv_readback(ahb, "anv texelFetch (compositor view)");
  if (anv == 2) say("  [anv] pixels not verified: ANV leg unavailable\n");
  else if (anv) fail = 1;
  vkQueueWaitIdle(g_queue);
  destroy_rt(&t);
  vkDestroyImage(g_dev, img, NULL);
  AHardwareBuffer_release(ahb);
  say("  %s: %s\n", tag, fail ? "FAIL" : "PASS");
  return fail;
}
#endif

enum { T_OFFSCREEN, T_ANB_SWAPCHAIN, T_ANB_LINEAR, T_AHB_READBACK, T_A17_MODEL, T_COUNT };
static const char *const k_names[T_COUNT] = {"offscreen", "anb-swapchain", "anb-linear", "ahb-i915-readback",
                                             "a17-swapchain-model"};

static int run_one(int which) {
  int rc = setup(which == g_first);
  if (rc) return rc;
  switch (which) {
  case T_OFFSCREEN: return test_offscreen();
#ifdef __ANDROID__
  case T_ANB_SWAPCHAIN: return test_anb(0);
  case T_ANB_LINEAR: return test_anb(1);
  case T_AHB_READBACK: return test_ahb_i915_readback();
  case T_A17_MODEL: return test_a17_swapchain_model();
#endif
  default: return 1;
  }
}

/* Parses -t. Returns 0 ok, -1 unknown name. named[i]: the test was asked for by name (or "anb"), not only by "all". */
static int parse_tests(const char *spec, int want[T_COUNT], int named[T_COUNT]) {
  char buf[256];
  snprintf(buf, sizeof buf, "%s", spec);
  for (char *save = NULL, *tok = strtok_r(buf, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) {
    int hit = 0;
    if (!strcmp(tok, "all")) {
      for (int i = 0; i < T_COUNT; i++) want[i] = 1;
      hit = 1;
    } else if (!strcmp(tok, "anb")) {
      want[T_ANB_SWAPCHAIN] = want[T_ANB_LINEAR] = 1;
      named[T_ANB_SWAPCHAIN] = named[T_ANB_LINEAR] = 1;
      hit = 1;
    } else {
      for (int i = 0; i < T_COUNT; i++)
        if (!strcmp(tok, k_names[i])) want[i] = named[i] = hit = 1;
    }
    if (!hit) {
      printf("unknown test '%s'\n", tok);
      return -1;
    }
  }
  return 0;
}

/* ---------- supervision (header: "Supervision") ---------- */
static volatile sig_atomic_t g_child;    /* running test child, 0 = none; set and cleared with the stop signals blocked */
static volatile sig_atomic_t g_stop_sig; /* first SIGTERM/SIGINT/SIGHUP received */

static void on_stop_signal(int sig) {
  if (!g_stop_sig) g_stop_sig = sig;
  pid_t c = (pid_t)g_child;
  if (c > 0) {
    kill(c, SIGKILL); /* the main loop reaps it and then ends the probe with g_stop_sig */
    return;
  }
  signal(sig, SIG_DFL); /* no test child: nothing to clean up, end now with this signal */
  raise(sig);
}

static void stop_signals(sigset_t *set) {
  sigemptyset(set);
  sigaddset(set, SIGTERM);
  sigaddset(set, SIGINT);
  sigaddset(set, SIGHUP);
}

static void supervise_init(void) {
  struct sigaction sa;
  memset(&sa, 0, sizeof sa);
  sa.sa_handler = on_stop_signal;
  sigemptyset(&sa.sa_mask);
  sa.sa_flags = 0; /* no SA_RESTART: a blocking waitid() returns EINTR */
  sigaction(SIGTERM, &sa, NULL);
  sigaction(SIGINT, &sa, NULL);
  sigaction(SIGHUP, &sa, NULL);
#ifdef __linux__
  pid_t parent = getppid();
  prctl(PR_SET_PDEATHSIG, SIGTERM); /* e.g. `timeout` killed: the probe stops too */
  if (getppid() != parent) raise(SIGTERM); /* the parent died before the prctl */
#endif
}

/* Waits for the test child and reaps it, polling every 100 ms (a stop signal also cuts the sleep short). The child is
 * first only observed (WNOWAIT), so its pid cannot be reused while g_child still names it. Bounds: 5 s after a stop
 * signal (the handler SIGKILLed the child); with -T, the parent SIGKILLs a child that outlived its own alarm by 5 s
 * (*overdue = 1) and waits 5 s more. Returns 0 with *st set, -1 if the child could not be reaped (stuck in the kernel:
 * g_child stays set, so a further stop signal kills it again). */
static int wait_child(pid_t pid, int *st, int *overdue) {
  siginfo_t si;
  long ticks = 0, stop_ticks = 0, kill_ticks = 0;
  const long deadline = g_timeout > 0 ? (g_timeout + 5) * 10L : -1;
  *overdue = 0;
  for (;;) {
    memset(&si, 0, sizeof si);
    if (waitid(P_PID, (id_t)pid, &si, WEXITED | WNOWAIT | WNOHANG) == 0) {
      if (si.si_pid == pid) break; /* exited, not reaped yet */
    } else if (errno != EINTR) {
      return -1;
    }
    if (g_stop_sig && ++stop_ticks > 50) return -1;
    if (deadline > 0 && ++ticks > deadline && !*overdue) {
      *overdue = 1;
      kill(pid, SIGKILL);
    }
    if (*overdue && ++kill_ticks > 50) return -1;
    struct timespec ts = {0, 100 * 1000 * 1000};
    nanosleep(&ts, NULL);
  }
  sigset_t stop, old;
  stop_signals(&stop);
  sigprocmask(SIG_BLOCK, &stop, &old);
  g_child = 0;
  pid_t r = waitpid(pid, st, 0);
  sigprocmask(SIG_SETMASK, &old, NULL);
  return r == pid ? 0 : -1;
}

int main(int argc, char **argv) {
  const char *tests = "all";
  int opt;
  while ((opt = getopt(argc, argv, "d:A:an:o:t:T:FHh")) != -1) {
    switch (opt) {
    case 'd': g_icd = optarg; break;
    case 'A': g_anv_icd = optarg; break;
    case 'a': g_any_vendor = 1; break;
    case 'n': g_frames = atoi(optarg); break;
    case 'o': g_ppm = optarg; break;
    case 't': tests = optarg; break;
    case 'T': g_timeout = atoi(optarg); break;
    case 'F': g_nofork = 1; break;
    case 'H': g_force_hmi = 1; break;
    default:
      printf("usage: %s [-d icd.so] [-A anv.so] [-a] [-n frames] [-o out.ppm] [-t offscreen|anb|anb-swapchain|"
             "anb-linear|ahb-i915-readback|a17-swapchain-model|all[,...]] [-T secs] [-F] [-H]\n", argv[0]);
      return 2;
    }
  }
  int want[T_COUNT] = {0}, named[T_COUNT] = {0};
  if (parse_tests(tests, want, named)) return 2;
  setvbuf(stdout, NULL, _IOLBF, 0);
  supervise_init();
  say("nvk_probe (built %s %s) pid %d\n", __DATE__, __TIME__, getpid());
  list_drm_nodes();
  if (load_icd()) return 2;
  int missing = 0; /* tests asked for by name that this build does not have: never a PASS */
#ifndef __ANDROID__
  for (int i = T_ANB_SWAPCHAIN; i < T_COUNT; i++)
    if (want[i] && named[i]) {
      say("  %s: SKIP (Android-only test, not in this build)\n", k_names[i]);
      missing++;
    }
  for (int i = T_ANB_SWAPCHAIN; i < T_COUNT; i++) want[i] = 0;
#endif
  for (int i = T_COUNT - 1; i >= 0; i--)
    if (want[i]) g_first = i;
  int results[T_COUNT], worst = 0, ran = 0;
  for (int i = 0; i < T_COUNT; i++) results[i] = -1;
  for (int i = 0; i < T_COUNT && !g_stop_sig; i++) {
    if (!want[i]) continue;
    int rc;
    ran++;
    if (g_nofork) {
      rc = run_one(i);
    } else {
      sigset_t stop, old;
      stop_signals(&stop);
      sigprocmask(SIG_BLOCK, &stop, &old); /* a stop signal now waits until g_child names the child */
      pid_t parent = getpid();
      fflush(stdout);
      pid_t pid = fork();
      if (pid == 0) {
        signal(SIGTERM, SIG_DFL);
        signal(SIGINT, SIG_DFL);
        signal(SIGHUP, SIG_DFL);
#ifdef __linux__
        prctl(PR_SET_PDEATHSIG, SIGKILL); /* the probe died (even by SIGKILL): the test child dies with it */
        if (getppid() != parent) _exit(1);
#endif
        sigprocmask(SIG_SETMASK, &old, NULL);
        if (g_timeout > 0) alarm((unsigned)g_timeout); /* watchdog: a hung driver call is reported as SIGALRM */
        _exit(run_one(i));
      }
      if (pid > 0) g_child = pid;
      sigprocmask(SIG_SETMASK, &old, NULL);
      int st = 0, overdue = 0;
      if (pid < 0) {
        say("  %s: fork failed: %s\n", k_names[i], strerror(errno));
        rc = 1;
      } else if (wait_child(pid, &st, &overdue)) {
        say("  %s: test child %d did not exit after SIGKILL within 5 s (stuck in the driver/kernel)\n", k_names[i],
            (int)pid);
        rc = 1;
      } else if (g_stop_sig) {
        say("  %s: INTERRUPTED by signal %d, test child killed\n", k_names[i], (int)g_stop_sig);
        rc = -2;
      } else if (overdue) {
        say("  %s: TIMED OUT: no exit %d s after the -T alarm, SIGKILLed by the parent\n", k_names[i], 5);
        rc = 1;
      } else if (WIFSIGNALED(st)) {
        say("  %s: %s in the driver with signal %d (see logcat / tombstone)\n", k_names[i],
            WTERMSIG(st) == SIGALRM ? "TIMED OUT" : "CRASHED", WTERMSIG(st));
        rc = 1;
      } else {
        rc = WEXITSTATUS(st);
      }
    }
    results[i] = rc;
    if (rc == -2) break;
    if (g_child) { /* a test child that cannot be reaped still holds the device: run nothing more */
      worst = 1;
      break;
    }
    if (rc == 2 || rc == 3) { /* no device / no instance: the other tests cannot run either */
      worst = rc;
      break;
    }
    if (rc) worst = 1;
  }
  say("== summary:");
  for (int i = 0; i < T_COUNT; i++)
    if (results[i] >= 0 || results[i] == -2)
      say(" %s=%s", k_names[i], results[i] == 0 ? "PASS" : results[i] == 3 ? "NO_DEVICE" : results[i] == 2 ? "ERROR" :
          results[i] == -2 ? "INTERRUPTED" : "FAIL");
  if (g_stop_sig) {
    int sig = (int)g_stop_sig;
    say("\nRESULT: INTERRUPTED (signal %d)\n", sig);
    signal(sig, SIG_DFL);
    raise(sig);
    _exit(128 + sig);
  }
  if (worst == 0 && (missing || !ran)) {
    worst = 1;
    say("\nRESULT: FAIL (%s)\n", missing ? "a requested test is not in this build" : "no test ran");
    return worst;
  }
  say("\nRESULT: %s\n", worst == 0 ? "PASS" : worst == 3 ? "NO_DEVICE" : worst == 2 ? "ERROR" : "FAIL");
  return worst;
}
