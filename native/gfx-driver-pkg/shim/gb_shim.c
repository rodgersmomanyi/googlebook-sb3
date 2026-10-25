/*
 * GbGfxDriver per-app Vulkan driver shim: "vulkan.<ro.hardware.vulkan>.so" (vulkan.intel.so on lapis, native and VM).
 *
 * Android loads exactly ONE Vulkan HAL per process; an updatable driver package replaces it for the whole app process
 * (HWUI included). This shim is that HAL. It always loads the platform HAL (ANV, or the VM's HAL) through
 * android_load_sphal_library(). Which apps load the package at all is decided by Settings.Global (INTERFACES 24), not
 * here. Per instance (INTERFACES v1.2 section 14, decision D5):
 *   1. vkCreateInstance reads vendor.gb.gfx_active ONCE (written only by gb_power) and keeps want_nvk = (value is
 *      exactly "nvidia") in the instance; a later change never affects an existing instance.
 *   2. Engine rule: pApplicationInfo NULL, or pEngineName / pApplicationName "android framework" (HWUI) -> platform
 *      driver only, NVK never loaded.
 *   3. ANGLE-HWUI guard (process-wide, decided once): debug.hwui.renderer set and not starting with "skiavk", or unset
 *      with ro.hwui.use_vulkan != "true" -> HWUI is on GL and reaches Vulkan through ANGLE (engine "ANGLE", which
 *      rule 2 cannot tell from the app's own ANGLE), so no instance of this process ever gets NVK.
 *   4. Gate at the first vkEnumeratePhysicalDevices* of the instance: want_nvk, rule 3, then a directory entry
 *      /sys/bus/pci/drivers/nouveau/0000:* exists and a nouveau render node (/sys/class/drm/renderD<n>/device/driver
 *      == nouveau) opens as the app's uid (it opens only while gb_power raised it to 0666, i.e. derived "nvidia").
 *   5. Order: gate open and NVK enumerated >= 1 device -> NVK devices FIRST, then the platform devices (groups too);
 *      otherwise the platform devices only. vendor.gb.gfxdriver (v1.1) is ignored.
 * NVK is created lazily at that first enumeration and destroyed with the instance; libgb_nvk.so is unloaded when the
 * last NVK instance is gone. Any NVK failure leaves the instance on the platform driver only: the shim never aborts and
 * never fails a call because of NVK (vkCreateDevice on an NVK physical device returns NVK's own result).
 *
 * Dispatch: the shim returns its own VkInstance object (first word = HWVULKAN_DISPATCH_MAGIC, which libvulkan then
 * overwrites) and the drivers' own VkPhysicalDevice/VkDevice handles. Every physical-device-level command is a
 * trampoline (gb_shim_tramp.inc, generated from vk.xml) that looks the handle up in a registry; vkGetDeviceProcAddr
 * returns the owning driver's functions directly, so device-level calls (queues, command buffers) cost nothing.
 *
 * Build: scripts/build_shim.sh (NDK for the APK; host glibc + GB_SHIM_TEST_HOOKS for the unit tests).
 * GB_SHIM_TEST_HOOKS (test builds only, never in the APK): GB_SHIM_PROPS="k=v,k=v" replaces system properties,
 * GB_SHIM_ROOT prefixes /sys and /dev paths, GB_SHIM_BASE_ICD / GB_SHIM_NVK_ICD give library paths.
 *
 * SPDX-License-Identifier: MIT
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define VK_NO_PROTOTYPES 1
#ifdef __ANDROID__
#define VK_USE_PLATFORM_ANDROID_KHR 1
#include <android/log.h>
#include <sys/system_properties.h>
#endif
#include <vulkan/vulkan.h>

#define GB_EXPORT __attribute__((visibility("default")))
#define GB_DISPATCH_MAGIC 0x01CDC0DEu /* HWVULKAN_DISPATCH_MAGIC == ICD_LOADER_MAGIC */
#define GB_FRAMEWORK_ENGINE "android framework"
#define GB_MAX_PDS 16

/* ------------------------------------------------------------------------------------------------ logging */
static void gb_log(int prio_err, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void gb_log(int prio_err, const char *fmt, ...) {
  char buf[512];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
#ifdef __ANDROID__
  __android_log_write(prio_err ? ANDROID_LOG_WARN : ANDROID_LOG_INFO, "gb_gfxdriver", buf);
#endif
#if defined(GB_SHIM_TEST_HOOKS) || !defined(__ANDROID__)
  if (getenv("GB_SHIM_DEBUG")) fprintf(stderr, "gb_shim: %s\n", buf);
#endif
}

/* ------------------------------------------------------------------------------------------------ platform */
#ifdef GB_SHIM_TEST_HOOKS
static const char *gb_test_env(const char *k) { return getenv(k); }
#endif

/* Reads a system property; returns its length, 0 when unset. */
static int gb_get_prop(const char *name, char out[92]) {
  out[0] = 0;
#ifdef GB_SHIM_TEST_HOOKS
  const char *all = gb_test_env("GB_SHIM_PROPS");
  if (all) {
    size_t nl = strlen(name);
    for (const char *p = all; *p;) {
      const char *e = strchr(p, ',');
      size_t l = e ? (size_t)(e - p) : strlen(p);
      if (l > nl && p[nl] == '=' && !strncmp(p, name, nl)) {
        size_t vl = l - nl - 1;
        if (vl > 91) vl = 91;
        memcpy(out, p + nl + 1, vl);
        out[vl] = 0;
        return (int)vl;
      }
      p += l + (e ? 1 : 0);
    }
    return 0;
  }
#endif
#ifdef __ANDROID__
  return __system_property_get(name, out);
#else
  return 0;
#endif
}

static void gb_path(char *out, size_t n, const char *abs) {
#ifdef GB_SHIM_TEST_HOOKS
  const char *root = gb_test_env("GB_SHIM_ROOT");
  if (root) {
    snprintf(out, n, "%s%s", root, abs);
    return;
  }
#endif
  snprintf(out, n, "%s", abs);
}

/* android_load_sphal_library() lives in libvndksupport (LLNDK); resolved at run time so that the shim has no
 * link-time dependency outside the NDK. Without a sphal namespace it behaves like dlopen(). */
static void *gb_load_sphal(const char *name) {
#ifdef __ANDROID__
  typedef void *(*PFN_sphal)(const char *, int);
  static PFN_sphal sphal;
  static int tried;
  if (!tried) {
    tried = 1;
    void *vs = dlopen("libvndksupport.so", RTLD_NOW | RTLD_LOCAL);
    if (vs) sphal = (PFN_sphal)dlsym(vs, "android_load_sphal_library");
    if (!sphal) gb_log(1, "android_load_sphal_library unavailable (%s); using dlopen", dlerror());
  }
  if (sphal) return sphal(name, RTLD_NOW | RTLD_LOCAL);
#endif
  return dlopen(name, RTLD_NOW | RTLD_LOCAL);
}

/* ------------------------------------------------------------------------------------------------ drivers */
struct gb_hal_module; /* hw_module_t layout, see hardware/hardware.h */
struct gb_hal_device {
  uint32_t tag, version;
  struct gb_hal_module *module;
  uint64_t reserved[12];
  int (*close)(struct gb_hal_device *);
  PFN_vkEnumerateInstanceExtensionProperties EnumerateInstanceExtensionProperties;
  PFN_vkCreateInstance CreateInstance;
  PFN_vkGetInstanceProcAddr GetInstanceProcAddr;
};
struct gb_hal_methods {
  int (*open)(const struct gb_hal_module *, const char *, struct gb_hal_device **);
};
struct gb_hal_module {
  uint32_t tag;
  uint16_t module_api_version, hal_api_version;
  const char *id, *name, *author;
  struct gb_hal_methods *methods;
  void *dso;
  uint64_t reserved[32 - 7];
};

struct gb_drv {
  const char *name; /* "base" or "nvk" */
  void *dso;
  PFN_vkGetInstanceProcAddr gipa;
  PFN_vkCreateInstance CreateInstance;
  PFN_vkEnumerateInstanceExtensionProperties EnumerateInstanceExtensionProperties;
  PFN_vkEnumerateInstanceVersion EnumerateInstanceVersion;
  PFN_vkEnumerateInstanceLayerProperties EnumerateInstanceLayerProperties;
};

GB_EXPORT extern struct gb_hal_module HMI; /* forward: used to detect that we loaded ourselves */

/* Fills drv from a dlopen()ed ICD/HAL. Returns 0 on success. */
static int gb_drv_init(struct gb_drv *d, void *dso, const char *what) {
  memset(d, 0, sizeof *d);
  d->name = what;
  d->dso = dso;
  struct gb_hal_module *hmi = (struct gb_hal_module *)dlsym(dso, "HMI");
  if (hmi == &HMI) {
    gb_log(1, "%s: loader returned the shim itself", what);
    return -1;
  }
  PFN_vkGetInstanceProcAddr icd = (PFN_vkGetInstanceProcAddr)dlsym(dso, "vk_icdGetInstanceProcAddr");
  if (hmi && hmi->methods && hmi->methods->open && strcmp(what, "nvk") != 0) {
    /* the HAL contract, exactly what libvulkan uses */
    struct gb_hal_device *dev = NULL;
    hmi->dso = dso;
    if (hmi->methods->open(hmi, "vk0", &dev) == 0 && dev) d->gipa = dev->GetInstanceProcAddr;
  }
  if (!d->gipa && icd) {
    typedef VkResult(VKAPI_PTR * PFN_negotiate)(uint32_t *);
    PFN_negotiate neg = (PFN_negotiate)dlsym(dso, "vk_icdNegotiateLoaderICDInterfaceVersion");
    if (neg) {
      uint32_t v = 5;
      neg(&v);
    }
    d->gipa = icd;
  }
  if (!d->gipa) {
    gb_log(1, "%s: neither HMI nor vk_icdGetInstanceProcAddr", what);
    return -1;
  }
  d->CreateInstance = (PFN_vkCreateInstance)d->gipa(NULL, "vkCreateInstance");
  d->EnumerateInstanceExtensionProperties =
      (PFN_vkEnumerateInstanceExtensionProperties)d->gipa(NULL, "vkEnumerateInstanceExtensionProperties");
  d->EnumerateInstanceVersion = (PFN_vkEnumerateInstanceVersion)d->gipa(NULL, "vkEnumerateInstanceVersion");
  d->EnumerateInstanceLayerProperties =
      (PFN_vkEnumerateInstanceLayerProperties)d->gipa(NULL, "vkEnumerateInstanceLayerProperties");
  return d->CreateInstance ? 0 : -1;
}

static struct gb_drv g_base;
static int g_base_ok;
static pthread_once_t g_base_once = PTHREAD_ONCE_INIT;

static void gb_base_load_once(void) {
  char hal[92], name[128];
  if (gb_get_prop("ro.hardware.vulkan", hal) <= 0 && gb_get_prop("ro.board.platform", hal) <= 0)
    snprintf(hal, sizeof hal, "intel");
  snprintf(name, sizeof name, "vulkan.%s.so", hal);
  const char *cands[4] = {NULL, name, NULL, NULL};
  char abs1[160], abs2[160];
  snprintf(abs1, sizeof abs1, "/vendor/lib64/hw/%s", name);
  snprintf(abs2, sizeof abs2, "/odm/lib64/hw/%s", name);
  cands[2] = abs1;
  cands[3] = abs2;
#ifdef GB_SHIM_TEST_HOOKS
  cands[0] = gb_test_env("GB_SHIM_BASE_ICD");
#endif
  for (int i = 0; i < 4; i++) {
    if (!cands[i]) continue;
    void *h = i == 0 ? dlopen(cands[i], RTLD_NOW | RTLD_LOCAL) : gb_load_sphal(cands[i]);
    if (!h) continue;
    if (gb_drv_init(&g_base, h, "base") == 0) {
      gb_log(0, "platform Vulkan HAL: %s", cands[i]);
      g_base_ok = 1;
      return;
    }
    if (dlsym(h, "HMI") != (void *)&HMI) dlclose(h); /* never dlclose ourselves */
  }
  gb_log(1, "no platform Vulkan HAL (%s): Vulkan unavailable in this process", name);
}

static int gb_base(void) {
  pthread_once(&g_base_once, gb_base_load_once);
  return g_base_ok;
}

/* NVK library: loaded on demand, refcounted by live NVK instances. */
static pthread_mutex_t g_nvk_lock = PTHREAD_MUTEX_INITIALIZER;
static struct gb_drv g_nvk;
static int g_nvk_refs;

static struct gb_drv *gb_nvk_get(void) {
  pthread_mutex_lock(&g_nvk_lock);
  if (g_nvk_refs == 0) {
    const char *path = "libgb_nvk.so";
#ifdef GB_SHIM_TEST_HOOKS
    if (gb_test_env("GB_SHIM_NVK_ICD")) path = gb_test_env("GB_SHIM_NVK_ICD");
#endif
    void *h = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!h || gb_drv_init(&g_nvk, h, "nvk") != 0) {
      gb_log(1, "NVK: cannot load %s: %s", path, h ? "bad ICD" : dlerror());
      if (h) dlclose(h);
      pthread_mutex_unlock(&g_nvk_lock);
      return NULL;
    }
  }
  g_nvk_refs++;
  pthread_mutex_unlock(&g_nvk_lock);
  return &g_nvk;
}

static void gb_nvk_put(void) {
  pthread_mutex_lock(&g_nvk_lock);
  if (g_nvk_refs > 0 && --g_nvk_refs == 0) {
    gb_log(0, "NVK: last instance gone, unloading libgb_nvk.so");
    dlclose(g_nvk.dso);
    memset(&g_nvk, 0, sizeof g_nvk);
  }
  pthread_mutex_unlock(&g_nvk_lock);
}

/* ------------------------------------------------------------------------------------------------ objects */
struct gb_fn_table {
  PFN_vkVoidFunction fn[512];
  uint8_t resolved[512];
};

struct gb_instance {
  uintptr_t magic; /* must stay first: libvulkan overwrites it with its own dispatch pointer */
  pthread_mutex_t lock;
  VkInstance base_inst;
  struct gb_fn_table *base_tbl, *nvk_tbl;
  /* NVK */
  bool nvk_allowed;     /* engine/app gate, decided at vkCreateInstance */
  bool want_nvk;        /* vendor.gb.gfx_active == "nvidia" and the HWUI guard passed, snapshot at vkCreateInstance */
  bool enumerated;
  VkInstance nvk_inst;
  struct gb_drv *nvk;
  /* copy of what the app asked for, to create the NVK instance later */
  VkApplicationInfo app;
  char app_name[128], engine_name[128];
  uint32_t ext_count;
  char (*exts)[VK_MAX_EXTENSION_NAME_SIZE];
  /* physical devices in the order the app sees them: NVK first (pds[0 .. nvk_pd_count)), then the platform driver's
   * (pds[nvk_pd_count .. nvk_pd_count + base_pd_count)) */
  uint32_t base_pd_count, nvk_pd_count;
  VkPhysicalDevice pds[GB_MAX_PDS];
};

enum gb_kind { GB_KIND_PD = 1, GB_KIND_INST = 2 };
struct gb_tramp_entry {
  const char *name;
  int kind;
  int index;
  PFN_vkVoidFunction fn;
};

/* registry of physical devices and devices -> owner */
struct gb_pd_reg {
  VkPhysicalDevice pd;
  struct gb_instance *si;
  struct gb_drv *drv;
  VkInstance owner_inst;
  struct gb_fn_table *tbl;
};
struct gb_dev_reg {
  VkDevice dev;
  struct gb_instance *si;
  bool nvk;
  PFN_vkGetDeviceProcAddr gdpa;
  PFN_vkDestroyDevice destroy;
};
static pthread_mutex_t g_reg_lock = PTHREAD_MUTEX_INITIALIZER;
static struct gb_pd_reg g_pds[64];
static struct gb_dev_reg g_devs[64];

static int gb_pd_register(VkPhysicalDevice pd, struct gb_instance *si, struct gb_drv *drv, VkInstance oi,
                          struct gb_fn_table *tbl) {
  pthread_mutex_lock(&g_reg_lock);
  for (size_t i = 0; i < sizeof g_pds / sizeof g_pds[0]; i++) {
    if (!g_pds[i].pd) {
      g_pds[i] = (struct gb_pd_reg){pd, si, drv, oi, tbl};
      pthread_mutex_unlock(&g_reg_lock);
      return 0;
    }
  }
  pthread_mutex_unlock(&g_reg_lock);
  return -1;
}

static void gb_pd_unregister_instance(struct gb_instance *si, struct gb_drv *drv) {
  pthread_mutex_lock(&g_reg_lock);
  for (size_t i = 0; i < sizeof g_pds / sizeof g_pds[0]; i++)
    if (g_pds[i].pd && g_pds[i].si == si && (!drv || g_pds[i].drv == drv)) memset(&g_pds[i], 0, sizeof g_pds[i]);
  pthread_mutex_unlock(&g_reg_lock);
}

static int gb_pd_find(VkPhysicalDevice pd, struct gb_pd_reg *out) {
  pthread_mutex_lock(&g_reg_lock);
  for (size_t i = 0; i < sizeof g_pds / sizeof g_pds[0]; i++) {
    if (g_pds[i].pd == pd && pd) {
      *out = g_pds[i];
      pthread_mutex_unlock(&g_reg_lock);
      return 0;
    }
  }
  pthread_mutex_unlock(&g_reg_lock);
  return -1;
}

static PFN_vkVoidFunction gb_pd_lookup(VkPhysicalDevice pd, int idx);
static PFN_vkVoidFunction gb_inst_lookup(VkInstance instance, int idx, VkInstance *base_inst);

#include "gb_shim_tramp.inc"

_Static_assert(GB_PD_COUNT <= 512 && GB_INST_COUNT <= 512, "function table too small");

/* Resolves command `idx` of `names` for (drv, instance) once, then serves it from the table. */
static PFN_vkVoidFunction gb_tbl_get(struct gb_fn_table *tbl, struct gb_drv *drv, VkInstance inst,
                                     const char *const *names, int idx) {
  if (!tbl->resolved[idx]) {
    PFN_vkVoidFunction f = drv->gipa(inst, names[idx]);
    tbl->fn[idx] = f;
    __atomic_store_n(&tbl->resolved[idx], 1, __ATOMIC_RELEASE);
    return f;
  }
  return tbl->fn[idx];
}

/* For trampolines: the owning driver's implementation of PD command idx (NULL if it has none). */
static PFN_vkVoidFunction gb_pd_lookup(VkPhysicalDevice pd, int idx) {
  struct gb_pd_reg r;
  if (gb_pd_find(pd, &r)) {
    gb_log(1, "unknown VkPhysicalDevice %p (%s)", (void *)pd, gb_pd_names[idx]);
    return NULL;
  }
  return gb_tbl_get(r.tbl, r.drv, r.owner_inst, gb_pd_names, idx);
}

/* Instance commands other than the custom ones go to the base driver only. */
static PFN_vkVoidFunction gb_inst_lookup(VkInstance instance, int idx, VkInstance *base_inst) {
  struct gb_instance *si = (struct gb_instance *)instance;
  *base_inst = si->base_inst;
  /* base_tbl is only used for instance commands here; PD commands of the base use their own table */
  return gb_tbl_get(si->base_tbl + 1, &g_base, si->base_inst, gb_inst_names, idx);
}

/* ------------------------------------------------------------------------------------------------ gates */
static bool gb_is_framework(const VkApplicationInfo *ai) {
  return (ai->pEngineName && !strcmp(ai->pEngineName, GB_FRAMEWORK_ENGINE)) ||
         (ai->pApplicationName && !strcmp(ai->pApplicationName, GB_FRAMEWORK_ENGINE));
}

/* Property values are untrusted (any app can set vendor.*): logs get at most 24 printable characters of them. */
static const char *gb_log_value(const char *v, char out[32]) {
  size_t o = 0;
  for (size_t i = 0; v[i] && o < 24; i++) out[o++] = (v[i] >= 0x20 && v[i] < 0x7f) ? v[i] : '?';
  out[o] = 0;
  if (v[0] && strlen(v) > 24) snprintf(out + o, 32 - o, "...");
  return o ? out : "unset";
}

/* ANGLE-HWUI guard (INTERFACES 14 rule 3), decided once per process: true = HWUI renders through Vulkan with its own
 * "android framework" instance, so every other instance in this process is the app's own. (R-13) HWUI takes the Vulkan
 * pipeline only for the exact value "skiavk" (any other set value means SkiaGL, through ANGLE on lapis), so only that
 * exact value counts; unset falls back to ro.hwui.use_vulkan, as HWUI's own default does. */
static bool g_hwui_vk;
static pthread_once_t g_hwui_once = PTHREAD_ONCE_INIT;

static void gb_hwui_once(void) {
  char r[92], u[92], lr[32], lu[32];
  int rl = gb_get_prop("debug.hwui.renderer", r);
  gb_get_prop("ro.hwui.use_vulkan", u);
  if (rl > 0) g_hwui_vk = !strcmp(r, "skiavk");
  else g_hwui_vk = !strcmp(u, "true");
  if (g_hwui_vk)
    gb_log(0, "HWUI on Vulkan (debug.hwui.renderer=%s, ro.hwui.use_vulkan=%s)", gb_log_value(r, lr),
           gb_log_value(u, lu));
  else
    gb_log(0, "HWUI not on Vulkan (debug.hwui.renderer=%s, ro.hwui.use_vulkan=%s): NVK disabled for this process",
           gb_log_value(r, lr), gb_log_value(u, lu));
}

static bool gb_hwui_vulkan(void) {
  pthread_once(&g_hwui_once, gb_hwui_once);
  return g_hwui_vk;
}

/* /sys/bus/pci/drivers/nouveau/0000:* exists and a nouveau render node opens */
static bool gb_nouveau_ready(char *why, size_t n) {
  char p[PATH_MAX];
  gb_path(p, sizeof p, "/sys/bus/pci/drivers/nouveau");
  DIR *d = opendir(p);
  bool bound = false;
  if (d) {
    struct dirent *e;
    while ((e = readdir(d)) != NULL)
      if (!strncmp(e->d_name, "0000:", 5)) bound = true;
    closedir(d);
  }
  if (!bound) {
    snprintf(why, n, "nouveau not bound");
    return false;
  }
  gb_path(p, sizeof p, "/sys/class/drm");
  d = opendir(p);
  bool opened = false;
  if (d) {
    struct dirent *e;
    while (!opened && (e = readdir(d)) != NULL) {
      if (strncmp(e->d_name, "renderD", 7)) continue;
      char link[PATH_MAX], tgt[PATH_MAX], rel[PATH_MAX];
      snprintf(rel, sizeof rel, "/sys/class/drm/%s/device/driver", e->d_name);
      gb_path(link, sizeof link, rel);
      ssize_t l = readlink(link, tgt, sizeof tgt - 1);
      if (l <= 0) continue;
      tgt[l] = 0;
      const char *base = strrchr(tgt, '/');
      if (strcmp(base ? base + 1 : tgt, "nouveau")) continue;
      char node[PATH_MAX];
      snprintf(rel, sizeof rel, "/dev/dri/%s", e->d_name);
      gb_path(node, sizeof node, rel);
      int fd = open(node, O_RDWR | O_CLOEXEC);
      if (fd >= 0) {
        close(fd);
        opened = true;
      } else {
        snprintf(why, n, "%.160s: %.64s", node, strerror(errno));
      }
    }
    closedir(d);
  }
  if (!opened && !why[0]) snprintf(why, n, "no nouveau render node");
  return opened;
}

/* Creates the NVK instance and stores up to `cap` of its physical devices in out[] (registered). Returns their number;
 * 0 = the instance stays on the platform driver. Called with si->lock held. Never fails the caller. */
static uint32_t gb_nvk_enumerate(struct gb_instance *si, uint32_t cap, VkPhysicalDevice *out) {
  char why[256] = "";
  if (!si->nvk_allowed || !si->want_nvk || cap == 0) return 0;
  if (!gb_nouveau_ready(why, sizeof why)) {
    gb_log(0, "NVK: not offered (%s)", why);
    return 0;
  }
  struct gb_drv *nvk = gb_nvk_get();
  if (!nvk) return 0;
  /* enable only the requested instance extensions NVK has */
  uint32_t n = 0;
  nvk->EnumerateInstanceExtensionProperties(NULL, &n, NULL);
  VkExtensionProperties *avail = calloc(n ? n : 1, sizeof *avail);
  const char **en = calloc(si->ext_count ? si->ext_count : 1, sizeof *en);
  uint32_t en_count = 0;
  if (avail && en) {
    nvk->EnumerateInstanceExtensionProperties(NULL, &n, avail);
    for (uint32_t i = 0; i < si->ext_count; i++)
      for (uint32_t j = 0; j < n; j++)
        if (!strcmp(si->exts[i], avail[j].extensionName)) {
          en[en_count++] = si->exts[i];
          break;
        }
  }
  uint32_t nvk_api = VK_API_VERSION_1_0;
  if (nvk->EnumerateInstanceVersion) nvk->EnumerateInstanceVersion(&nvk_api);
  VkApplicationInfo app = si->app;
  if (app.apiVersion > nvk_api) app.apiVersion = nvk_api;
  VkInstanceCreateInfo ci = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
                             .pApplicationInfo = &app,
                             .enabledExtensionCount = en_count,
                             .ppEnabledExtensionNames = en};
  VkInstance inst = VK_NULL_HANDLE;
  VkResult r = avail && en ? nvk->CreateInstance(&ci, NULL, &inst) : VK_ERROR_OUT_OF_HOST_MEMORY;
  free(avail);
  free(en);
  if (r != VK_SUCCESS) {
    gb_log(1, "NVK: vkCreateInstance -> %d, staying on the platform driver", r);
    gb_nvk_put();
    return 0;
  }
  PFN_vkEnumeratePhysicalDevices epd = (PFN_vkEnumeratePhysicalDevices)nvk->gipa(inst, "vkEnumeratePhysicalDevices");
  PFN_vkDestroyInstance di = (PFN_vkDestroyInstance)nvk->gipa(inst, "vkDestroyInstance");
  uint32_t cnt = cap < GB_MAX_PDS ? cap : GB_MAX_PDS;
  VkPhysicalDevice pds[GB_MAX_PDS];
  r = epd ? epd(inst, &cnt, pds) : VK_ERROR_INITIALIZATION_FAILED;
  if ((r != VK_SUCCESS && r != VK_INCOMPLETE) || cnt == 0) {
    gb_log(1, "NVK: %u physical device(s) (%d), staying on the platform driver", cnt, r);
    if (di) di(inst, NULL);
    gb_nvk_put();
    return 0;
  }
  si->nvk_tbl = calloc(1, sizeof *si->nvk_tbl);
  if (!si->nvk_tbl) {
    if (di) di(inst, NULL);
    gb_nvk_put();
    return 0;
  }
  uint32_t got = 0;
  for (uint32_t i = 0; i < cnt; i++) {
    if (gb_pd_register(pds[i], si, nvk, inst, si->nvk_tbl)) break;
    out[got++] = pds[i];
  }
  if (got == 0) { /* registry full: stay on the platform driver */
    gb_log(1, "NVK: device registry full, staying on the platform driver");
    if (di) di(inst, NULL);
    free(si->nvk_tbl);
    si->nvk_tbl = NULL;
    gb_nvk_put();
    return 0;
  }
  si->nvk_inst = inst;
  si->nvk = nvk;
  return got;
}

/* Enumerates the platform driver, then (gated) NVK, and fixes the order the app sees: NVK first. */
static VkResult gb_enumerate_locked(struct gb_instance *si) {
  if (si->enumerated) return VK_SUCCESS;
  PFN_vkEnumeratePhysicalDevices epd =
      (PFN_vkEnumeratePhysicalDevices)g_base.gipa(si->base_inst, "vkEnumeratePhysicalDevices");
  if (!epd) return VK_ERROR_INITIALIZATION_FAILED;
  uint32_t cnt = GB_MAX_PDS;
  VkPhysicalDevice base[GB_MAX_PDS], nvk[GB_MAX_PDS];
  VkResult r = epd(si->base_inst, &cnt, base);
  if (r != VK_SUCCESS && r != VK_INCOMPLETE) return r;
  for (uint32_t i = 0; i < cnt; i++) {
    if (gb_pd_register(base[i], si, &g_base, si->base_inst, si->base_tbl)) {
      gb_pd_unregister_instance(si, &g_base);
      return VK_ERROR_OUT_OF_HOST_MEMORY;
    }
  }
  uint32_t nn = gb_nvk_enumerate(si, GB_MAX_PDS - cnt, nvk);
  memcpy(si->pds, nvk, nn * sizeof *nvk);
  memcpy(si->pds + nn, base, cnt * sizeof *base);
  si->nvk_pd_count = nn;
  si->base_pd_count = cnt;
  if (nn) gb_log(0, "NVK: %u NVIDIA physical device(s) first, then %u platform device(s)", nn, cnt);
  si->enumerated = true;
  return VK_SUCCESS;
}

/* ------------------------------------------------------------------------------------------------ entry points */
static VKAPI_ATTR VkResult VKAPI_CALL gb_EnumerateInstanceExtensionProperties(const char *layer, uint32_t *count,
                                                                             VkExtensionProperties *props) {
  if (!gb_base()) {
    *count = 0;
    return layer ? VK_ERROR_LAYER_NOT_PRESENT : VK_SUCCESS;
  }
  return g_base.EnumerateInstanceExtensionProperties(layer, count, props);
}

static VKAPI_ATTR VkResult VKAPI_CALL gb_EnumerateInstanceLayerProperties(uint32_t *count, VkLayerProperties *props) {
  if (gb_base() && g_base.EnumerateInstanceLayerProperties) return g_base.EnumerateInstanceLayerProperties(count, props);
  (void)props;
  *count = 0;
  return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL gb_EnumerateInstanceVersion(uint32_t *ver) {
  if (gb_base() && g_base.EnumerateInstanceVersion) return g_base.EnumerateInstanceVersion(ver);
  *ver = VK_API_VERSION_1_0;
  return VK_SUCCESS;
}

static VKAPI_ATTR VkResult VKAPI_CALL gb_CreateInstance(const VkInstanceCreateInfo *ci,
                                                       const VkAllocationCallbacks *alloc, VkInstance *out) {
  if (!gb_base()) return VK_ERROR_INCOMPATIBLE_DRIVER;
  struct gb_instance *si = calloc(1, sizeof *si);
  struct gb_fn_table *tbl = calloc(2, sizeof *tbl); /* [0] base PD commands, [1] base instance commands */
  if (!si || !tbl) {
    free(si);
    free(tbl);
    return VK_ERROR_OUT_OF_HOST_MEMORY;
  }
  si->magic = GB_DISPATCH_MAGIC;
  si->base_tbl = tbl;
  pthread_mutex_init(&si->lock, NULL);
  VkResult r = g_base.CreateInstance(ci, alloc, &si->base_inst);
  if (r != VK_SUCCESS) {
    pthread_mutex_destroy(&si->lock);
    free(tbl);
    free(si);
    return r;
  }
  /* D5 read point: one read per instance; the property is gb_power's, an app setting it gains nothing because the
   * render node gate (gb_nouveau_ready) still has to open */
  char act[92], lv[32];
  bool hwui_vk = gb_hwui_vulkan(); /* decided at the process's first instance, whatever gfx_active says */
  bool active_nvidia = gb_get_prop("vendor.gb.gfx_active", act) > 0 && !strcmp(act, "nvidia");
  si->want_nvk = active_nvidia && hwui_vk;
  if (!active_nvidia) gb_log(0, "instance: platform driver only (vendor.gb.gfx_active=%s)", gb_log_value(act, lv));
  const VkApplicationInfo *ai = ci->pApplicationInfo;
  if (ai && !gb_is_framework(ai)) {
    si->app = *ai;
    si->app.pNext = NULL;
    if (ai->pApplicationName) {
      snprintf(si->app_name, sizeof si->app_name, "%s", ai->pApplicationName);
      si->app.pApplicationName = si->app_name;
    }
    if (ai->pEngineName) {
      snprintf(si->engine_name, sizeof si->engine_name, "%s", ai->pEngineName);
      si->app.pEngineName = si->engine_name;
    }
    if (ci->enabledExtensionCount) {
      si->exts = calloc(ci->enabledExtensionCount, sizeof *si->exts);
      if (si->exts) {
        for (uint32_t i = 0; i < ci->enabledExtensionCount; i++)
          snprintf(si->exts[i], sizeof si->exts[i], "%s", ci->ppEnabledExtensionNames[i]);
        si->ext_count = ci->enabledExtensionCount;
      }
    }
    si->nvk_allowed = true;
  } else {
    gb_log(0, "instance '%s'/'%s': platform driver only", ai && ai->pApplicationName ? ai->pApplicationName : "(null)",
           ai && ai->pEngineName ? ai->pEngineName : "(null)");
  }
  *out = (VkInstance)si;
  return VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL gb_DestroyInstance(VkInstance instance, const VkAllocationCallbacks *alloc) {
  struct gb_instance *si = (struct gb_instance *)instance;
  if (!si) return;
  bool nvk_devices = false;
  pthread_mutex_lock(&g_reg_lock);
  for (size_t i = 0; i < sizeof g_devs / sizeof g_devs[0]; i++)
    if (g_devs[i].dev && g_devs[i].si == si && g_devs[i].nvk) nvk_devices = true;
  pthread_mutex_unlock(&g_reg_lock);
  if (si->nvk_inst) {
    gb_pd_unregister_instance(si, si->nvk);
    if (!nvk_devices) {
      PFN_vkDestroyInstance di = (PFN_vkDestroyInstance)si->nvk->gipa(si->nvk_inst, "vkDestroyInstance");
      if (di) di(si->nvk_inst, NULL);
      gb_nvk_put();
    } else {
      gb_log(1, "NVK: instance destroyed with live NVK devices; keeping libgb_nvk.so loaded");
    }
  }
  gb_pd_unregister_instance(si, NULL);
  PFN_vkDestroyInstance di = (PFN_vkDestroyInstance)g_base.gipa(si->base_inst, "vkDestroyInstance");
  if (di) di(si->base_inst, alloc);
  pthread_mutex_destroy(&si->lock);
  free(si->exts);
  free(si->nvk_tbl);
  free(si->base_tbl);
  free(si);
}

static VKAPI_ATTR VkResult VKAPI_CALL gb_EnumeratePhysicalDevices(VkInstance instance, uint32_t *count,
                                                                 VkPhysicalDevice *pds) {
  struct gb_instance *si = (struct gb_instance *)instance;
  pthread_mutex_lock(&si->lock);
  VkResult r = gb_enumerate_locked(si);
  uint32_t total = si->base_pd_count + si->nvk_pd_count;
  if (r == VK_SUCCESS) {
    if (!pds) {
      *count = total;
    } else {
      /* si->pds is already in the app's order (NVK first), so a short array gets the NVK device(s) */
      uint32_t n = *count < total ? *count : total;
      memcpy(pds, si->pds, n * sizeof *pds);
      *count = n;
      if (n < total) r = VK_INCOMPLETE;
    }
  }
  pthread_mutex_unlock(&si->lock);
  return r;
}

static void gb_single_group(VkPhysicalDeviceGroupProperties *g, VkPhysicalDevice pd) {
  /* sType/pNext belong to the caller */
  g->physicalDeviceCount = 1;
  memset(g->physicalDevices, 0, sizeof g->physicalDevices);
  g->physicalDevices[0] = pd;
  g->subsetAllocation = VK_FALSE;
}

static VKAPI_ATTR VkResult VKAPI_CALL gb_EnumeratePhysicalDeviceGroups(VkInstance instance, uint32_t *count,
                                                                      VkPhysicalDeviceGroupProperties *groups) {
  struct gb_instance *si = (struct gb_instance *)instance;
  pthread_mutex_lock(&si->lock);
  VkResult r = gb_enumerate_locked(si);
  if (r != VK_SUCCESS) {
    pthread_mutex_unlock(&si->lock);
    return r;
  }
  /* one group per NVK physical device FIRST, then the base groups as the base reports them */
  PFN_vkEnumeratePhysicalDeviceGroups beg =
      (PFN_vkEnumeratePhysicalDeviceGroups)g_base.gipa(si->base_inst, "vkEnumeratePhysicalDeviceGroups");
  if (!beg) beg = (PFN_vkEnumeratePhysicalDeviceGroups)g_base.gipa(si->base_inst, "vkEnumeratePhysicalDeviceGroupsKHR");
  uint32_t nn = si->nvk_pd_count, nb = 0;
  if (beg) {
    r = beg(si->base_inst, &nb, NULL);
    if (r < 0) {
      pthread_mutex_unlock(&si->lock);
      return r;
    }
  } else {
    nb = si->base_pd_count;
  }
  uint32_t total = nn + nb;
  if (!groups) {
    *count = total;
    pthread_mutex_unlock(&si->lock);
    return VK_SUCCESS;
  }
  uint32_t cap = *count, out = 0;
  for (uint32_t i = 0; i < nn && out < cap; i++) gb_single_group(&groups[out++], si->pds[i]);
  if (out < cap && nb) {
    if (beg) {
      uint32_t k = cap - out < nb ? cap - out : nb;
      r = beg(si->base_inst, &k, groups + out);
      if (r != VK_SUCCESS && r != VK_INCOMPLETE) {
        pthread_mutex_unlock(&si->lock);
        return r;
      }
      out += k;
    } else {
      for (uint32_t i = 0; i < nb && out < cap; i++) gb_single_group(&groups[out++], si->pds[nn + i]);
    }
  }
  *count = out;
  pthread_mutex_unlock(&si->lock);
  return out < total ? VK_INCOMPLETE : VK_SUCCESS;
}

static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL gb_GetDeviceProcAddr(VkDevice device, const char *name);
static VKAPI_ATTR void VKAPI_CALL gb_DestroyDevice(VkDevice device, const VkAllocationCallbacks *alloc);

static VKAPI_ATTR VkResult VKAPI_CALL gb_CreateDevice(VkPhysicalDevice pd, const VkDeviceCreateInfo *ci,
                                                     const VkAllocationCallbacks *alloc, VkDevice *out) {
  struct gb_pd_reg r;
  if (gb_pd_find(pd, &r)) return VK_ERROR_INITIALIZATION_FAILED;
  PFN_vkCreateDevice cd = (PFN_vkCreateDevice)r.drv->gipa(r.owner_inst, "vkCreateDevice");
  PFN_vkGetDeviceProcAddr gdpa = (PFN_vkGetDeviceProcAddr)r.drv->gipa(r.owner_inst, "vkGetDeviceProcAddr");
  if (!cd || !gdpa) return VK_ERROR_INITIALIZATION_FAILED;
  VkResult res = cd(pd, ci, alloc, out);
  if (res != VK_SUCCESS) {
    if (r.drv != &g_base) gb_log(1, "NVK: vkCreateDevice -> %d", res);
    return res;
  }
  PFN_vkDestroyDevice dd = (PFN_vkDestroyDevice)gdpa(*out, "vkDestroyDevice");
  pthread_mutex_lock(&g_reg_lock);
  size_t i;
  for (i = 0; i < sizeof g_devs / sizeof g_devs[0]; i++)
    if (!g_devs[i].dev) {
      g_devs[i] = (struct gb_dev_reg){*out, r.si, r.drv != &g_base, gdpa, dd};
      break;
    }
  pthread_mutex_unlock(&g_reg_lock);
  if (i == sizeof g_devs / sizeof g_devs[0]) {
    if (dd) dd(*out, alloc);
    *out = VK_NULL_HANDLE;
    return VK_ERROR_TOO_MANY_OBJECTS;
  }
  return VK_SUCCESS;
}

static int gb_dev_find(VkDevice dev, struct gb_dev_reg *out, bool remove) {
  pthread_mutex_lock(&g_reg_lock);
  for (size_t i = 0; i < sizeof g_devs / sizeof g_devs[0]; i++)
    if (dev && g_devs[i].dev == dev) {
      *out = g_devs[i];
      if (remove) memset(&g_devs[i], 0, sizeof g_devs[i]);
      pthread_mutex_unlock(&g_reg_lock);
      return 0;
    }
  pthread_mutex_unlock(&g_reg_lock);
  return -1;
}

static VKAPI_ATTR void VKAPI_CALL gb_DestroyDevice(VkDevice device, const VkAllocationCallbacks *alloc) {
  struct gb_dev_reg d;
  if (!device || gb_dev_find(device, &d, true)) return;
  if (d.destroy) d.destroy(device, alloc);
}

static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL gb_GetDeviceProcAddr(VkDevice device, const char *name) {
  if (!name) return NULL;
  if (!strcmp(name, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)gb_GetDeviceProcAddr;
  struct gb_dev_reg d;
  if (gb_dev_find(device, &d, false)) return NULL;
  PFN_vkVoidFunction f = d.gdpa(device, name);
  if (f && !strcmp(name, "vkDestroyDevice")) return (PFN_vkVoidFunction)gb_DestroyDevice;
  return f;
}

static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL gb_GetInstanceProcAddr(VkInstance instance, const char *name) {
  if (!name) return NULL;
#define G(n) \
  if (!strcmp(name, "vk" #n)) return (PFN_vkVoidFunction)gb_##n
  G(GetInstanceProcAddr);
  G(EnumerateInstanceExtensionProperties);
  G(EnumerateInstanceLayerProperties);
  G(EnumerateInstanceVersion);
  G(CreateInstance);
  if (!instance) return NULL;
  struct gb_instance *si = (struct gb_instance *)instance;
  /* only offer what the base driver offers, so libvulkan's view of "the driver" stays ANV's */
  PFN_vkVoidFunction base = g_base.gipa(si->base_inst, name);
  if (!base) return NULL;
  G(DestroyInstance);
  G(EnumeratePhysicalDevices);
  G(GetDeviceProcAddr);
  G(CreateDevice);
  if (!strcmp(name, "vkEnumeratePhysicalDeviceGroups") || !strcmp(name, "vkEnumeratePhysicalDeviceGroupsKHR"))
    return (PFN_vkVoidFunction)gb_EnumeratePhysicalDeviceGroups;
#undef G
  for (const struct gb_tramp_entry *e = gb_tramp_table; e->name; e++)
    if (!strcmp(e->name, name)) return e->fn;
  /* device-level command asked through vkGetInstanceProcAddr: not dispatchable without a device -> unsupported
   * (libvulkan resolves device commands with vkGetDeviceProcAddr per device) */
  return NULL;
}

/* ------------------------------------------------------------------------------------------------ HAL module */
static int gb_hal_close(struct gb_hal_device *dev) {
  (void)dev;
  return 0;
}

static struct gb_hal_device g_hal_dev = {
    .tag = 0x48574454u, /* HARDWARE_DEVICE_TAG 'HWDT' */
    .version = 0x00000001u, /* HWVULKAN_DEVICE_API_VERSION_0_1 */
    .close = gb_hal_close,
    .EnumerateInstanceExtensionProperties = gb_EnumerateInstanceExtensionProperties,
    .CreateInstance = gb_CreateInstance,
    .GetInstanceProcAddr = gb_GetInstanceProcAddr,
};

static int gb_hal_open(const struct gb_hal_module *mod, const char *id, struct gb_hal_device **dev) {
  if (strcmp(id, "vk0")) return -22; /* -EINVAL */
  g_hal_dev.module = (struct gb_hal_module *)mod;
  *dev = &g_hal_dev;
  return 0;
}

static struct gb_hal_methods g_hal_methods = {.open = gb_hal_open};

/* libvulkan writes HMI.dso, so this must stay in writable memory (not const) */
GB_EXPORT struct gb_hal_module HMI = {
    .tag = 0x48574d54u,              /* HARDWARE_MODULE_TAG 'HWMT' */
    .module_api_version = 0x0001,    /* HWVULKAN_MODULE_API_VERSION_0_1 */
    .hal_api_version = 0x0100,       /* HARDWARE_HAL_API_VERSION 1.0 */
    .id = "vulkan",                  /* HWVULKAN_HARDWARE_MODULE_ID */
    .name = "GbGfxDriver shim (NVK first while vendor.gb.gfx_active=nvidia)",
    .author = "googlebook-sb3",
    .methods = &g_hal_methods,
};
