/*
 * GbGfxDriver shim unit tests, INTERFACES v1.2 (host glibc build and bionic build, both with GB_SHIM_TEST_HOOKS).
 * Cases GD-1B-01 .. GD-1B-09 of 1b\TASKS_1B.md 3.2, plus the wave-1 GD-01/GD-02 checks that v1.2 keeps.
 *
 * The test plays Android's libvulkan: it loads the shim by path, checks HMI, opens "vk0", creates instances through
 * the HAL, and - like libvulkan's SetData() - OVERWRITES the first word of every dispatchable handle the shim returns
 * (instance, physical devices, devices, queues) after checking HWVULKAN_DISPATCH_MAGIC. Physical-device and device
 * calls then go through the functions the shim handed out.
 *
 * Properties come from GB_SHIM_PROPS ("k=v,k=v", replaces every system property). The ANGLE-HWUI guard is decided
 * once per process (pthread_once at the first vkCreateInstance), so its cases run in forked children BEFORE this
 * process creates its first instance; every later instance of this process sees ro.hwui.use_vulkan=true.
 *
 * usage: test_shim <shim.so> <base ICD = lavapipe / fake Intel, plays ANV, or "-" = the shim's own platform loader>
 *                  <fake NVK ICD (vendor 0x10de)> [real NVK ICD + drm-shim]
 * The fake sysfs/dev tree for the nouveau gates is created under a temporary GB_SHIM_ROOT.
 * Exit 0 = all PASS.
 * SPDX-License-Identifier: MIT
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#define VK_NO_PROTOTYPES 1
#include <vulkan/vulkan.h>

#define MAGIC 0x01CDC0DEu
#define FAKE_LOADER_DATA ((uintptr_t)0x5e7da7a5e7da7a00ull) /* what libvulkan's SetData would write */
#define NV 0x10de
/* every property set of this test starts with these (HWUI on Vulkan as on native lapis, HAL name intel) */
#define BASE_PROPS "ro.hwui.use_vulkan=true,ro.hardware.vulkan=intel"

struct hal_module {
  uint32_t tag;
  uint16_t module_api_version, hal_api_version;
  const char *id, *name, *author;
  struct hal_methods *methods;
  void *dso;
  uint64_t reserved[25];
};
struct hal_device {
  uint32_t tag, version;
  struct hal_module *module;
  uint64_t reserved[12];
  int (*close)(struct hal_device *);
  PFN_vkEnumerateInstanceExtensionProperties EnumerateInstanceExtensionProperties;
  PFN_vkCreateInstance CreateInstance;
  PFN_vkGetInstanceProcAddr GetInstanceProcAddr;
};
struct hal_methods {
  int (*open)(const struct hal_module *, const char *, struct hal_device **);
};

static struct hal_device *hal;
static const char *g_fake_nvk, *g_real_nvk;
static char g_root[64];
static int g_fail, g_pass;

static void ok(int cond, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void ok(int cond, const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  printf("  %s ", cond ? "PASS" : "FAIL");
  vprintf(fmt, ap);
  printf("\n");
  va_end(ap);
  if (cond) g_pass++;
  else g_fail++;
}

/* GB_SHIM_PROPS = BASE_PROPS[,extra] */
static void props(const char *extra) {
  char p[512];
  snprintf(p, sizeof p, "%s%s%s", BASE_PROPS, extra && extra[0] ? "," : "", extra ? extra : "");
  setenv("GB_SHIM_PROPS", p, 1);
}

/* libvulkan SetData(): requires the magic, then stores its own pointer there */
static int set_data(void *handle) {
  uintptr_t *w = handle;
  if (*w != MAGIC) return -1;
  *w = FAKE_LOADER_DATA;
  return 0;
}

static int nvk_loaded(const char *path) {
  if (!path) return 0;
  void *h = dlopen(path, RTLD_NOW | RTLD_NOLOAD);
  if (h) dlclose(h);
  return h != NULL;
}

static int count_fds(void) {
  int n = 0;
  DIR *d = opendir("/proc/self/fd");
  if (!d) return -1;
  while (readdir(d)) n++;
  closedir(d);
  return n;
}

struct result {
  VkResult create, enumerate;
  uint32_t n, count_query;
  uint32_t vendor[16];
  char name[16][VK_MAX_PHYSICAL_DEVICE_NAME_SIZE];
  int magic_bad;
  VkResult dev_result[16];
  int groups;
  int nvk_loaded_before_enum, nvk_loaded_after_enum, nvk_loaded_after_destroy;
  int fds_before, fds_after;
};

/* One app instance as libvulkan would drive it. app/engine NULL with has_info = no VkApplicationInfo. */
static void run_instance(const char *app, const char *engine, int has_info, int make_devices, struct result *res,
                         const char *nvk_path) {
  memset(res, 0, sizeof *res);
  res->fds_before = count_fds();
  VkApplicationInfo ai = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
                          .pApplicationName = app,
                          .pEngineName = engine,
                          .apiVersion = VK_API_VERSION_1_1};
  const char *exts[] = {"VK_KHR_get_physical_device_properties2"};
  VkInstanceCreateInfo ci = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
                             .pApplicationInfo = has_info ? &ai : NULL,
                             .enabledExtensionCount = 1,
                             .ppEnabledExtensionNames = exts};
  VkInstance inst = VK_NULL_HANDLE;
  res->create = hal->CreateInstance(&ci, NULL, &inst);
  if (res->create != VK_SUCCESS) return;
  if (set_data(inst)) res->magic_bad++;
  PFN_vkGetInstanceProcAddr gipa = hal->GetInstanceProcAddr;
  PFN_vkEnumeratePhysicalDevices epd = (PFN_vkEnumeratePhysicalDevices)gipa(inst, "vkEnumeratePhysicalDevices");
  PFN_vkEnumeratePhysicalDeviceGroups epg =
      (PFN_vkEnumeratePhysicalDeviceGroups)gipa(inst, "vkEnumeratePhysicalDeviceGroups");
  PFN_vkGetPhysicalDeviceProperties gpp = (PFN_vkGetPhysicalDeviceProperties)gipa(inst, "vkGetPhysicalDeviceProperties");
  PFN_vkGetPhysicalDeviceProperties2 gpp2 =
      (PFN_vkGetPhysicalDeviceProperties2)gipa(inst, "vkGetPhysicalDeviceProperties2");
  PFN_vkGetPhysicalDeviceQueueFamilyProperties gqf =
      (PFN_vkGetPhysicalDeviceQueueFamilyProperties)gipa(inst, "vkGetPhysicalDeviceQueueFamilyProperties");
  PFN_vkCreateDevice cd = (PFN_vkCreateDevice)gipa(inst, "vkCreateDevice");
  PFN_vkGetDeviceProcAddr gdpa = (PFN_vkGetDeviceProcAddr)gipa(inst, "vkGetDeviceProcAddr");
  PFN_vkDestroyInstance di = (PFN_vkDestroyInstance)gipa(inst, "vkDestroyInstance");
  res->nvk_loaded_before_enum = nvk_loaded(nvk_path);
  uint32_t n = 0;
  res->enumerate = epd(inst, &n, NULL);
  res->count_query = n;
  VkPhysicalDevice pds[16];
  if (n > 16) n = 16;
  res->enumerate = epd(inst, &n, pds);
  res->n = n;
  res->nvk_loaded_after_enum = nvk_loaded(nvk_path);
  for (uint32_t i = 0; i < n; i++) {
    if (set_data(pds[i])) res->magic_bad++;
    VkPhysicalDeviceProperties p;
    gpp(pds[i], &p);
    res->vendor[i] = p.vendorID;
    snprintf(res->name[i], sizeof res->name[i], "%s", p.deviceName);
    VkPhysicalDeviceProperties2 p2 = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
    if (gpp2) {
      gpp2(pds[i], &p2);
      if (p2.properties.vendorID != p.vendorID) res->magic_bad++;
    }
  }
  res->groups = -2; /* not offered: the base has no vkEnumeratePhysicalDeviceGroups, libvulkan synthesises them */
  if (epg) {
    uint32_t g = 0;
    epg(inst, &g, NULL);
    VkPhysicalDeviceGroupProperties gp[16];
    for (int i = 0; i < 16; i++) gp[i] = (VkPhysicalDeviceGroupProperties){.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_GROUP_PROPERTIES};
    if (g > 16) g = 16;
    epg(inst, &g, gp);
    res->groups = (int)g;
    for (uint32_t i = 0; i < g && i < n; i++)
      if (gp[i].physicalDevices[0] != pds[i]) res->groups = -1; /* same order as vkEnumeratePhysicalDevices */
  }
  if (make_devices) {
    for (uint32_t i = 0; i < n; i++) {
      uint32_t nq = 0;
      gqf(pds[i], &nq, NULL);
      float prio = 1.0f;
      VkDeviceQueueCreateInfo qci = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
                                     .queueFamilyIndex = 0,
                                     .queueCount = 1,
                                     .pQueuePriorities = &prio};
      VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
                                .queueCreateInfoCount = 1,
                                .pQueueCreateInfos = &qci};
      VkDevice dev = VK_NULL_HANDLE;
      res->dev_result[i] = cd(pds[i], &dci, NULL, &dev);
      if (res->dev_result[i] != VK_SUCCESS) continue;
      if (set_data(dev)) res->magic_bad++;
      PFN_vkGetDeviceQueue gq = (PFN_vkGetDeviceQueue)gdpa(dev, "vkGetDeviceQueue");
      PFN_vkQueueWaitIdle qwi = (PFN_vkQueueWaitIdle)gdpa(dev, "vkQueueWaitIdle");
      PFN_vkDestroyDevice dd = (PFN_vkDestroyDevice)gdpa(dev, "vkDestroyDevice");
      VkQueue q = VK_NULL_HANDLE;
      gq(dev, 0, 0, &q);
      if (!q || set_data(q)) res->magic_bad++;
      if (qwi && qwi(q) != VK_SUCCESS) res->magic_bad++;
      dd(dev, NULL);
    }
  }
  di(inst, NULL);
  res->nvk_loaded_after_destroy = nvk_loaded(nvk_path);
  res->fds_after = count_fds();
}

static void mkpath(const char *rel, int dir) {
  char p[256];
  snprintf(p, sizeof p, "%s%s", g_root, rel);
  char tmp[256];
  snprintf(tmp, sizeof tmp, "%s", p);
  for (char *s = tmp + strlen(g_root) + 1; *s; s++)
    if (*s == '/') {
      *s = 0;
      mkdir(tmp, 0755);
      *s = '/';
    }
  if (dir) mkdir(p, 0755);
  else close(open(p, O_CREAT | O_RDWR, 0644));
}

/* recursive removal without external tools (the lapis chroot has no rm) */
#include <ftw.h>
static int rm_cb(const char *p, const struct stat *st, int flag, struct FTW *f) {
  (void)st;
  (void)f;
  return flag == FTW_DP ? rmdir(p) : unlink(p);
}
static void rm_rf(const char *p) { nftw(p, rm_cb, 16, FTW_DEPTH | FTW_PHYS); }

/* bound: /sys/bus/pci/drivers/nouveau/0000:02:00.0; node: renderD129 driven by nouveau; node_ok: /dev/dri/renderD129
 * exists (mode 0644 = opens for the test's own uid) */
static void fake_tree(int bound, int node, int node_ok) {
  char p[256];
  snprintf(p, sizeof p, "%s/sys", g_root);
  rm_rf(p);
  snprintf(p, sizeof p, "%s/dev", g_root);
  rm_rf(p);
  mkpath("/sys/bus/pci/drivers/nouveau", 1);
  mkpath("/sys/class/drm", 1);
  mkpath("/dev/dri", 1);
  if (bound) mkpath("/sys/bus/pci/drivers/nouveau/0000:02:00.0", 1);
  /* renderD128 = i915 (never used), renderD129 = nouveau */
  mkpath("/sys/class/drm/renderD128/device", 1);
  mkpath("/sys/bus/pci/drivers/i915", 1);
  char l[256];
  snprintf(l, sizeof l, "%s/sys/class/drm/renderD128/device/driver", g_root);
  if (symlink("../../../../bus/pci/drivers/i915", l)) {}
  mkpath("/dev/dri/renderD128", 0);
  if (node) {
    mkpath("/sys/class/drm/renderD129/device", 1);
    snprintf(l, sizeof l, "%s/sys/class/drm/renderD129/device/driver", g_root);
    if (symlink("../../../../bus/pci/drivers/nouveau", l)) {}
    if (node_ok) mkpath("/dev/dri/renderD129", 0);
  }
}

static int count_vendor(const struct result *r, uint32_t v) {
  int c = 0;
  for (uint32_t i = 0; i < r->n; i++) c += r->vendor[i] == v;
  return c;
}

/* NVK first: exactly one NVIDIA device at index 0, the platform device(s) after it */
static int nvk_first(const struct result *r, uint32_t base_n) {
  return r->create == VK_SUCCESS && r->enumerate == VK_SUCCESS && r->n == base_n + 1 && r->vendor[0] == NV &&
         count_vendor(r, NV) == 1;
}
static int platform_only(const struct result *r, uint32_t base_n) {
  return r->create == VK_SUCCESS && r->enumerate == VK_SUCCESS && r->n == base_n && count_vendor(r, NV) == 0;
}

#include <link.h>
static int find_lib_cb(struct dl_phdr_info *info, size_t size, void *data) {
  (void)size;
  const char *want = data;
  return info->dlpi_name && strstr(info->dlpi_name, want) != NULL;
}
static int lib_mapped(const char *path_part) { return dl_iterate_phdr(find_lib_cb, (void *)path_part); }

/* ---------------------------------------------------------------------------------------------- forked cases */
/* Runs fn in a child process (fresh pthread_once state for the HWUI guard as long as this process has not created
 * an instance yet; or another uid) and returns its result through a pipe. */
struct child_out {
  struct result a, b;
  int ran;
};
typedef void (*child_fn)(struct child_out *, const void *);
static int in_child(child_fn fn, const void *arg, struct child_out *out) {
  int p[2];
  memset(out, 0, sizeof *out);
  if (pipe(p)) return -1;
  fflush(stdout);
  pid_t pid = fork();
  if (pid < 0) return -1;
  if (pid == 0) {
    close(p[0]);
    struct child_out o;
    memset(&o, 0, sizeof o);
    fn(&o, arg);
    o.ran = 1;
    ssize_t w = write(p[1], &o, sizeof o);
    fflush(stdout);
    _exit(w == (ssize_t)sizeof o ? 0 : 1);
  }
  close(p[1]);
  size_t got = 0;
  while (got < sizeof *out) {
    ssize_t r = read(p[0], (char *)out + got, sizeof *out - got);
    if (r <= 0) break;
    got += (size_t)r;
  }
  close(p[0]);
  int st = 0;
  waitpid(pid, &st, 0);
  return got == sizeof *out && WIFEXITED(st) && WEXITSTATUS(st) == 0 ? 0 : -1;
}

struct guard_arg {
  const char *props1, *engine, *props2; /* props2: a second instance after a property change (process-wide rule) */
};
static void guard_child(struct child_out *o, const void *v) {
  const struct guard_arg *g = v;
  setenv("GB_SHIM_PROPS", g->props1, 1);
  run_instance("guard app", g->engine, 1, 0, &o->a, g_fake_nvk);
  if (g->props2) {
    setenv("GB_SHIM_PROPS", g->props2, 1);
    run_instance("guard app", g->engine, 1, 0, &o->b, g_fake_nvk);
  }
}

static uint32_t g_base_n; /* platform device count, measured in a child before the guard cases */
static void base_count_child(struct child_out *o, const void *v) {
  (void)v;
  setenv("GB_SHIM_PROPS", BASE_PROPS ",vendor.gb.gfx_active=intel", 1);
  run_instance("count", "count", 1, 0, &o->a, g_fake_nvk);
}

static void guard_case(const char *what, const char *props1, const char *engine, int want_nvk, const char *props2,
                       int want_nvk2) {
  struct guard_arg g = {props1, engine, props2};
  struct child_out o;
  int rc = in_child(guard_child, &g, &o);
  int good = rc == 0 && (want_nvk ? nvk_first(&o.a, g_base_n) : platform_only(&o.a, g_base_n) &&
                                                                    !o.a.nvk_loaded_after_enum);
  if (props2)
    good = good && (want_nvk2 ? nvk_first(&o.b, g_base_n) : platform_only(&o.b, g_base_n) &&
                                                                !o.b.nvk_loaded_after_enum);
  ok(good, "%s -> %s%s (first device vendor 0x%x, %u device(s))", what, want_nvk ? "NVK first" : "platform only",
     props2 ? (want_nvk2 ? ", second instance NVK first" : ", second instance platform only") : "", o.a.vendor[0],
     o.a.n);
}

/* render node present but not openable for the app's uid (0660 root:graphics as for a non-root app) */
static void node_closed_child(struct child_out *o, const void *v) {
  (void)v;
  if (geteuid() == 0) {
    /* root ignores mode bits: become an unprivileged uid like an app */
    if (chmod(g_root, 0755) || setgid(9999) || setuid(9999)) return;
  }
  run_instance("vkcube", "vkcube", 1, 1, &o->a, g_fake_nvk);
}

/* ---------------------------------------------------------------------------------------------- real NVK */
/* Real NVK: what the dGPU sees is NVK's DRM file descriptors (nouveau refcnt); they must all be closed when the app
 * instance is destroyed. Whether bionic unmaps libgb_nvk.so after the shim's dlclose is reported only: once NVK code
 * registered thread-local destructors on this thread, bionic keeps the DSO mapped until the thread exits (observed in
 * the chroot after a vkCreateDevice), which holds no device. The fake NVK checks above cover the shim's own unload. */
static void real_nvk_release_checks(const char *nvk) {
  struct result res;
  props("vendor.gb.gfx_active=nvidia");
  run_instance("vkcube", "vkcube", 1, 1, &res, nvk);
  ok(res.n >= 1 && res.vendor[0] == NV && res.dev_result[0] == VK_SUCCESS,
     "real NVK again: listed first ('%s') and its device created", res.name[0]);
  ok(res.fds_before == res.fds_after,
     "no file descriptor left open after vkDestroyDevice + vkDestroyInstance (%d -> %d)", res.fds_before,
     res.fds_after);
  printf("  info libgb_nvk.so still mapped after the shim's dlclose: %s\n", res.nvk_loaded_after_destroy ? "yes" : "no");
  int was_mapped = nvk_loaded(nvk);
  run_instance("x", "android framework", 1, 0, &res, nvk);
  ok(count_vendor(&res, NV) == 0 && (was_mapped || !res.nvk_loaded_after_enum) && res.fds_before == res.fds_after,
     "HWUI engine with gfx_active=nvidia: platform device(s) only, NVK not loaded by this instance, fds unchanged");
}

/* Platform mode (bionic chroot): no GB_SHIM_BASE_ICD, so the shim must find the platform HAL itself
 * (android_load_sphal_library -> no sphal namespace in the chroot -> dlopen("vulkan.intel.so") returns the shim
 * itself -> rejected -> /vendor/lib64/hw/vulkan.intel.so). */
static int platform_mode(const char *nvk) {
  struct result res;
  printf("== platform base: lapis HAL found by the shim's own loader path (v1.2 properties)\n");
  setenv("GB_SHIM_NVK_ICD", nvk, 1);
  fake_tree(1, 1, 1);
  uint32_t ne = 0;
  hal->EnumerateInstanceExtensionProperties(NULL, &ne, NULL);
  ok(ne > 0, "base instance extensions: %u", ne);
  ok(lib_mapped("/vendor/lib64/hw/vulkan.intel.so"), "/vendor/lib64/hw/vulkan.intel.so (the platform HAL) is mapped");
  props("vendor.gb.gfx_active=intel");
  run_instance("vkcube", "vkcube", 1, 1, &res, nvk);
  uint32_t base_n = res.n;
  ok(res.create == VK_SUCCESS && res.enumerate == VK_SUCCESS && res.n >= 1 && count_vendor(&res, NV) == 0 &&
         !res.nvk_loaded_after_enum,
     "gfx_active=intel: platform device(s) only (%u), NVK never loaded", res.n);
  props("vendor.gb.gfx_active=nvidia");
  run_instance("vkcube", "vkcube", 1, 1, &res, nvk);
  ok(res.create == VK_SUCCESS && res.enumerate == VK_SUCCESS, "gfx_active=nvidia: create/enumerate");
  printf("  info %u device(s): %d NVIDIA, %d platform\n", res.n, count_vendor(&res, NV),
         (int)res.n - count_vendor(&res, NV));
  for (uint32_t i = 0; i < res.n; i++) printf("  info   [%u] '%s' vendor 0x%x\n", i, res.name[i], res.vendor[i]);
  ok(nvk_first(&res, base_n), "NVK listed first, the platform device(s) after it");
  ok(res.n >= 1 && res.dev_result[0] == VK_SUCCESS, "NVK device created through the shim");
  ok(res.n >= 2 && res.dev_result[res.n - 1] == VK_SUCCESS, "platform device created through the shim");
  ok(res.magic_bad == 0, "dispatch magic");
  real_nvk_release_checks(nvk);
  return 0;
}

int main(int argc, char **argv) {
  if (argc < 4) {
    fprintf(stderr, "usage: %s shim.so base_icd.so|- fake_nvk.so [real_nvk.so]\n", argv[0]);
    return 2;
  }
  setvbuf(stdout, NULL, _IOLBF, 0);
  g_fake_nvk = argv[3];
  g_real_nvk = argc > 4 ? argv[4] : NULL;
  snprintf(g_root, sizeof g_root, "/tmp/gbshim.XXXXXX");
  if (!mkdtemp(g_root)) return 2;
  setenv("GB_SHIM_ROOT", g_root, 1);
  int platform = !strcmp(argv[2], "-");
  if (platform) unsetenv("GB_SHIM_BASE_ICD");
  else setenv("GB_SHIM_BASE_ICD", argv[2], 1);
  setenv("GB_SHIM_NVK_ICD", g_fake_nvk, 1);
  props(NULL);

  printf("== HAL module (libvulkan LoadDriver/Hal::Open)\n");
  void *so = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  ok(so != NULL, "dlopen %s", argv[1]);
  if (!so) return 1;
  struct hal_module *hmi = dlsym(so, "HMI");
  ok(hmi && hmi->tag == 0x48574d54u && !strcmp(hmi->id, "vulkan") && hmi->module_api_version == 1,
     "HMI tag/id/module_api_version (id '%s')", hmi ? hmi->id : "?");
  if (!hmi) return 1;
  hmi->dso = so; /* libvulkan writes this: must be writable */
  ok(hmi->dso == so, "HMI.dso writable");
  int r = hmi->methods->open(hmi, "vk0", &hal);
  ok(r == 0 && hal && hal->tag == 0x48574454u && hal->version == 1 && hal->module == hmi, "open(vk0)");
  if (!hal) return 1;
  ok(nvk_loaded(g_fake_nvk) == 0, "libgb_nvk not loaded by dlopen/open of the shim");
  uint32_t ne = 0;
  hal->EnumerateInstanceExtensionProperties(NULL, &ne, NULL);
  ok(ne > 0, "instance extensions come from the base driver (%u)", ne);
  ok(hal->GetInstanceProcAddr(NULL, "vkFrobnicateGB") == NULL, "unknown global command -> NULL");
  if (platform) {
    platform_mode(g_real_nvk ? g_real_nvk : g_fake_nvk);
    rm_rf(g_root);
    printf("RESULT: %s (%d passed, %d failed)\n", g_fail ? "FAIL" : "PASS", g_pass, g_fail);
    return g_fail ? 1 : 0;
  }

  fake_tree(1, 1, 1);
  struct child_out co;
  if (in_child(base_count_child, NULL, &co) || co.a.n == 0 || co.a.n > 15) {
    printf("  FAIL cannot count the platform devices\n");
    return 1;
  }
  g_base_n = co.a.n;
  printf("  info platform driver: %u device(s), first '%s' vendor 0x%x\n", g_base_n, co.a.name[0], co.a.vendor[0]);

  printf("== GD-1B-05: ANGLE-HWUI guard (each case in a fresh child process; engine 'ANGLE', gfx_active=nvidia)\n");
  guard_case("debug.hwui.renderer=skiagl", "debug.hwui.renderer=skiagl,ro.hwui.use_vulkan=true,"
             "vendor.gb.gfx_active=nvidia", "ANGLE", 0, NULL, 0);
  guard_case("renderer unset, ro.hwui.use_vulkan=false", "ro.hwui.use_vulkan=false,vendor.gb.gfx_active=nvidia",
             "ANGLE", 0, NULL, 0);
  guard_case("renderer unset, ro.hwui.use_vulkan unset", "vendor.gb.gfx_active=nvidia", "ANGLE", 0, NULL, 0);
  guard_case("renderer unset, ro.hwui.use_vulkan=true", "ro.hwui.use_vulkan=true,vendor.gb.gfx_active=nvidia", "ANGLE",
             1, NULL, 0);
  guard_case("debug.hwui.renderer=skiavkthreaded (R-13: not the exact skiavk, HWUI on GL)",
             "debug.hwui.renderer=skiavkthreaded,vendor.gb.gfx_active=nvidia", "ANGLE", 0, NULL, 0);
  guard_case("debug.hwui.renderer=skiavk (exact)", "debug.hwui.renderer=skiavk,vendor.gb.gfx_active=nvidia", "ANGLE", 1,
             NULL, 0);
  guard_case("debug.hwui.renderer=skiavk overrides ro.hwui.use_vulkan=false",
             "debug.hwui.renderer=skiavk,ro.hwui.use_vulkan=false,vendor.gb.gfx_active=nvidia", "ANGLE", 1, NULL, 0);
  guard_case("ro.hwui.use_vulkan=TRUE (not exactly 'true')", "ro.hwui.use_vulkan=TRUE,vendor.gb.gfx_active=nvidia",
             "ANGLE", 0, NULL, 0);
  guard_case("process-wide: skiagl at the first instance, skiavk later",
             "debug.hwui.renderer=skiagl,vendor.gb.gfx_active=nvidia", "ANGLE", 0,
             "debug.hwui.renderer=skiavk,ro.hwui.use_vulkan=true,vendor.gb.gfx_active=nvidia", 0);
  guard_case("process-wide: guard decided at the first instance even when it was gfx_active=intel",
             "debug.hwui.renderer=skiagl,vendor.gb.gfx_active=intel", "ANGLE", 0,
             "debug.hwui.renderer=skiavk,ro.hwui.use_vulkan=true,vendor.gb.gfx_active=nvidia", 0);
  guard_case("process-wide: Vulkan HWUI at the first instance, skiagl later",
             "ro.hwui.use_vulkan=true,vendor.gb.gfx_active=nvidia", "ANGLE", 1,
             "debug.hwui.renderer=skiagl,vendor.gb.gfx_active=nvidia", 1);
  guard_case("app's own engine (not ANGLE) under GL HWUI", "debug.hwui.renderer=skiagl,vendor.gb.gfx_active=nvidia",
             "vkcube", 0, NULL, 0);

  /* from here on this process's guard is decided: HWUI on Vulkan (BASE_PROPS) */
  struct result res;
  uint32_t base_n = g_base_n;
  printf("== GD-1B-02: vendor.gb.gfx_active not exactly 'nvidia' -> platform only, libgb_nvk never mapped\n");
  const char *notnv[] = {"vendor.gb.gfx_active=intel", "", "vendor.gb.gfx_active=", "vendor.gb.gfx_active=NVIDIA",
                         "vendor.gb.gfx_active=nvidia ",
                         "vendor.gb.gfx_active= nvidia", "vendor.gb.gfx_active=nvidia1", "vendor.gb.gfx_active=nvidi",
                         NULL};
  for (int i = 0; notnv[i]; i++) {
    props(notnv[i]);
    run_instance("vkcube", "vkcube", 1, 1, &res, g_fake_nvk);
    ok(platform_only(&res, base_n) && !res.nvk_loaded_after_enum && !res.nvk_loaded_after_destroy &&
           res.dev_result[0] == VK_SUCCESS,
       "'%s' -> platform only (%u device(s), first '%s')", notnv[i][0] ? notnv[i] : "gfx_active unset", res.n,
       res.name[0]);
  }
  {
    char garbage[160];
    memset(garbage, 0, sizeof garbage);
    strcpy(garbage, "vendor.gb.gfx_active=nvidia");
    size_t l = strlen(garbage);
    for (int i = 0; i < 85; i++) garbage[l + i] = 'x'; /* 91-character value starting with "nvidia" */
    props(garbage);
    run_instance("vkcube", "vkcube", 1, 0, &res, g_fake_nvk);
    ok(platform_only(&res, base_n) && !res.nvk_loaded_after_enum, "91-character garbage value -> platform only");
  }

  printf("== GD-1B-07: the v1.1 property vendor.gb.gfxdriver is ignored\n");
  props("vendor.gb.gfxdriver=on,vendor.gb.gfx_active=intel");
  run_instance("vkcube", "vkcube", 1, 0, &res, g_fake_nvk);
  ok(platform_only(&res, base_n) && !res.nvk_loaded_after_enum, "gfxdriver=on + gfx_active=intel -> platform only");
  props("vendor.gb.gfxdriver=off,vendor.gb.gfx_active=nvidia");
  run_instance("vkcube", "vkcube", 1, 0, &res, g_fake_nvk);
  ok(nvk_first(&res, base_n), "gfxdriver=off + gfx_active=nvidia -> NVK first");

  printf("== GD-1B-01 / GD-1B-08: gfx_active=nvidia, every gate open: NVK first, devices on both, lazy, unloaded\n");
  props("vendor.gb.gfx_active=nvidia");
  run_instance("vkcube", "vkcube", 1, 1, &res, g_fake_nvk);
  ok(res.count_query == base_n + 1, "count query: %u device(s) (platform %u + NVK 1)", res.count_query, base_n);
  ok(nvk_first(&res, base_n), "NVK first ('%s' 0x%x), platform after ('%s' 0x%x)", res.name[0], res.vendor[0],
     res.name[res.n ? res.n - 1 : 0], res.vendor[res.n ? res.n - 1 : 0]);
  ok(res.magic_bad == 0, "dispatch magic on instance/PDs/devices/queues, calls work after libvulkan overwrote it");
  ok(res.dev_result[0] == VK_SUCCESS && res.dev_result[res.n - 1] == VK_SUCCESS,
     "vkCreateDevice + queue + vkDestroyDevice on device 0 (NVK) and on the platform device");
  ok(res.groups == (int)res.n || res.groups == -2,
     "vkEnumeratePhysicalDeviceGroups: %d group(s), same order as the devices (-2 = not offered by the base)",
     res.groups);
  ok(!res.nvk_loaded_before_enum && res.nvk_loaded_after_enum, "NVK loaded lazily at the first enumeration");
  ok(!res.nvk_loaded_after_destroy, "libgb_nvk unloaded when the last instance is destroyed");
  ok(res.fds_before == res.fds_after, "fds unchanged (%d -> %d)", res.fds_before, res.fds_after);
  {
    VkApplicationInfo ai = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "a", .pEngineName = "e",
                            .apiVersion = VK_API_VERSION_1_1};
    VkInstanceCreateInfo ci = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &ai};
    VkInstance a = VK_NULL_HANDLE;
    hal->CreateInstance(&ci, NULL, &a);
    PFN_vkEnumeratePhysicalDevices epd =
        (PFN_vkEnumeratePhysicalDevices)hal->GetInstanceProcAddr(a, "vkEnumeratePhysicalDevices");
    PFN_vkEnumeratePhysicalDeviceGroups epg =
        (PFN_vkEnumeratePhysicalDeviceGroups)hal->GetInstanceProcAddr(a, "vkEnumeratePhysicalDeviceGroups");
    PFN_vkGetPhysicalDeviceProperties gpp =
        (PFN_vkGetPhysicalDeviceProperties)hal->GetInstanceProcAddr(a, "vkGetPhysicalDeviceProperties");
    PFN_vkDestroyInstance di = (PFN_vkDestroyInstance)hal->GetInstanceProcAddr(a, "vkDestroyInstance");
    uint32_t one = 1;
    VkPhysicalDevice first = VK_NULL_HANDLE, all[16];
    VkResult inc = epd(a, &one, &first);
    VkPhysicalDeviceProperties p = {0};
    if (first) gpp(first, &p);
    ok(inc == VK_INCOMPLETE && one == 1 && p.vendorID == NV, "capacity 1: VK_INCOMPLETE, the one device is NVK (0x%x)",
       p.vendorID);
    uint32_t nall = 16;
    epd(a, &nall, all);
    if (epg) {
      VkPhysicalDeviceGroupProperties gp[16];
      for (int i = 0; i < 16; i++) gp[i] = (VkPhysicalDeviceGroupProperties){.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_GROUP_PROPERTIES};
      uint32_t g = 0;
      epg(a, &g, NULL);
      ok(g == nall, "groups count query = device count (%u)", g);
      uint32_t g1 = 1;
      VkResult gr = epg(a, &g1, gp);
      ok(gr == VK_INCOMPLETE && g1 == 1 && gp[0].physicalDeviceCount == 1 && gp[0].physicalDevices[0] == all[0],
         "groups capacity 1: VK_INCOMPLETE, the NVK group");
      uint32_t g2 = 2;
      for (int i = 0; i < 16; i++) gp[i] = (VkPhysicalDeviceGroupProperties){.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_GROUP_PROPERTIES};
      gr = epg(a, &g2, gp);
      ok(g2 == (nall < 2 ? nall : 2) && gp[0].physicalDevices[0] == all[0] && (nall < 2 || gp[1].physicalDevices[0] == all[1]) &&
             gr == (nall > 2 ? VK_INCOMPLETE : VK_SUCCESS),
         "groups capacity 2: NVK group then the first platform group (%s)", gr == VK_SUCCESS ? "VK_SUCCESS" : "VK_INCOMPLETE");
      uint32_t g16 = 16;
      for (int i = 0; i < 16; i++) gp[i] = (VkPhysicalDeviceGroupProperties){.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_GROUP_PROPERTIES};
      gr = epg(a, &g16, gp);
      int same = gr == VK_SUCCESS && g16 == nall;
      for (uint32_t i = 0; same && i < g16; i++) same = gp[i].physicalDevices[0] == all[i] && gp[i].sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_GROUP_PROPERTIES;
      ok(same, "groups full: NVK first, same order as vkEnumeratePhysicalDevices, sType kept");
    } else {
      printf("  info base driver has no vkEnumeratePhysicalDeviceGroups: libvulkan synthesises the groups from the "
             "device order\n");
    }
    di(a, NULL);
  }

  printf("== GD-1B-03: 'android framework' and missing application info -> platform only, NVK never loaded\n");
  props("vendor.gb.gfx_active=nvidia");
  run_instance("myapp", "android framework", 1, 0, &res, g_fake_nvk);
  ok(platform_only(&res, base_n) && !res.nvk_loaded_after_enum, "pEngineName 'android framework' (HWUI)");
  run_instance("android framework", "Skia", 1, 0, &res, g_fake_nvk);
  ok(platform_only(&res, base_n) && !res.nvk_loaded_after_enum, "pApplicationName 'android framework'");
  run_instance(NULL, NULL, 0, 0, &res, g_fake_nvk);
  ok(platform_only(&res, base_n) && !res.nvk_loaded_after_enum, "pApplicationInfo NULL");
  run_instance("ANGLE app", "ANGLE", 1, 0, &res, g_fake_nvk);
  ok(nvk_first(&res, base_n), "engine 'ANGLE' with HWUI on Vulkan -> NVK first");

  printf("== GD-1B-04: gfx_active is a per-instance snapshot taken at vkCreateInstance\n");
  {
    VkApplicationInfo ai = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "snap",
                            .pEngineName = "snap", .apiVersion = VK_API_VERSION_1_1};
    VkInstanceCreateInfo ci = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &ai};
    for (int dir = 0; dir < 2; dir++) {
      const char *p1 = dir == 0 ? "vendor.gb.gfx_active=nvidia" : "vendor.gb.gfx_active=intel";
      const char *p2 = dir == 0 ? "vendor.gb.gfx_active=intel" : "vendor.gb.gfx_active=nvidia";
      VkInstance a = VK_NULL_HANDLE, b = VK_NULL_HANDLE;
      props(p1);
      hal->CreateInstance(&ci, NULL, &a);
      props(p2); /* changed after A was created, before A enumerates */
      PFN_vkEnumeratePhysicalDevices epd =
          (PFN_vkEnumeratePhysicalDevices)hal->GetInstanceProcAddr(a, "vkEnumeratePhysicalDevices");
      PFN_vkGetPhysicalDeviceProperties gpp =
          (PFN_vkGetPhysicalDeviceProperties)hal->GetInstanceProcAddr(a, "vkGetPhysicalDeviceProperties");
      PFN_vkDestroyInstance di = (PFN_vkDestroyInstance)hal->GetInstanceProcAddr(a, "vkDestroyInstance");
      uint32_t na = 16, nb = 16;
      VkPhysicalDevice pa[16], pb[16];
      epd(a, &na, pa);
      hal->CreateInstance(&ci, NULL, &b);
      epd(b, &nb, pb);
      VkPhysicalDeviceProperties qa = {0}, qb = {0};
      if (na) gpp(pa[0], &qa);
      if (nb) gpp(pb[0], &qb);
      if (dir == 0) {
        ok(na == base_n + 1 && qa.vendorID == NV, "A created with nvidia, property then intel, A enumerated -> NVK first");
        ok(nb == base_n && qb.vendorID != NV, "B created after the change (intel) -> platform only");
      } else {
        ok(na == base_n && qa.vendorID != NV, "A created with intel, property then nvidia, A enumerated -> platform only");
        ok(nb == base_n + 1 && qb.vendorID == NV, "B created after the change (nvidia) -> NVK first");
      }
      di(a, NULL);
      di(b, NULL);
    }
    ok(!nvk_loaded(g_fake_nvk), "libgb_nvk unloaded after both snapshot pairs");
  }

  printf("== GD-1B-06: NVK failures with gfx_active=nvidia -> platform only, VK_SUCCESS, no fd left open\n");
  props("vendor.gb.gfx_active=nvidia");
  setenv("GB_SHIM_NVK_ICD", "/nonexistent/libgb_nvk.so", 1);
  run_instance("vkcube", "vkcube", 1, 1, &res, NULL);
  ok(platform_only(&res, base_n) && res.dev_result[0] == VK_SUCCESS && res.fds_before == res.fds_after,
     "libgb_nvk.so missing (fds %d -> %d)", res.fds_before, res.fds_after);
  setenv("GB_SHIM_NVK_ICD", g_fake_nvk, 1);
  setenv("FAKE_ICD_FAIL", "instance", 1);
  run_instance("vkcube", "vkcube", 1, 1, &res, g_fake_nvk);
  ok(platform_only(&res, base_n) && !res.nvk_loaded_after_enum && res.fds_before == res.fds_after,
     "NVK vkCreateInstance fails -> library unloaded again (fds %d -> %d)", res.fds_before, res.fds_after);
  setenv("FAKE_ICD_FAIL", "enumerate", 1);
  run_instance("vkcube", "vkcube", 1, 1, &res, g_fake_nvk);
  ok(platform_only(&res, base_n) && !res.nvk_loaded_after_enum && res.fds_before == res.fds_after,
     "NVK finds 0 devices (fds %d -> %d)", res.fds_before, res.fds_after);
  setenv("FAKE_ICD_FAIL", "device", 1);
  run_instance("vkcube", "vkcube", 1, 1, &res, g_fake_nvk);
  ok(nvk_first(&res, base_n) && res.dev_result[0] != VK_SUCCESS && res.dev_result[res.n - 1] == VK_SUCCESS,
     "NVK vkCreateDevice fails -> only that call fails (%d), the platform device works", res.dev_result[0]);
  unsetenv("FAKE_ICD_FAIL");
  fake_tree(0, 1, 1);
  run_instance("vkcube", "vkcube", 1, 0, &res, g_fake_nvk);
  ok(platform_only(&res, base_n) && !res.nvk_loaded_after_enum && res.fds_before == res.fds_after,
     "no /sys/bus/pci/drivers/nouveau/0000:* (fds %d -> %d)", res.fds_before, res.fds_after);
  fake_tree(1, 0, 0);
  run_instance("vkcube", "vkcube", 1, 0, &res, g_fake_nvk);
  ok(platform_only(&res, base_n) && !res.nvk_loaded_after_enum, "no nouveau render node");
  fake_tree(1, 1, 0);
  run_instance("vkcube", "vkcube", 1, 0, &res, g_fake_nvk);
  ok(platform_only(&res, base_n) && !res.nvk_loaded_after_enum, "nouveau render node missing in /dev/dri");
  {
    fake_tree(1, 1, 1);
    char node[256];
    snprintf(node, sizeof node, "%s/dev/dri/renderD129", g_root);
    chmod(node, 0000); /* the uid cannot open it, as an app with 0660 root:graphics */
    if (in_child(node_closed_child, NULL, &co)) {
      ok(0, "render node not openable: child failed");
    } else {
      ok(platform_only(&co.a, base_n) && !co.a.nvk_loaded_after_enum && co.a.fds_before == co.a.fds_after &&
             co.a.dev_result[0] == VK_SUCCESS,
         "render node not openable as the app's uid (%s) -> platform only (fds %d -> %d)",
         geteuid() == 0 ? "root test: child dropped to uid 9999" : "mode 0000", co.a.fds_before, co.a.fds_after);
    }
    fake_tree(1, 1, 1);
  }

  printf("== wave 1 (kept): two instances share one libgb_nvk.so\n");
  props("vendor.gb.gfx_active=nvidia");
  {
    VkApplicationInfo ai = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "a", .pEngineName = "e",
                            .apiVersion = VK_API_VERSION_1_1};
    VkInstanceCreateInfo ci = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &ai};
    VkInstance a = VK_NULL_HANDLE, b = VK_NULL_HANDLE;
    hal->CreateInstance(&ci, NULL, &a);
    hal->CreateInstance(&ci, NULL, &b);
    PFN_vkEnumeratePhysicalDevices epd =
        (PFN_vkEnumeratePhysicalDevices)hal->GetInstanceProcAddr(a, "vkEnumeratePhysicalDevices");
    PFN_vkDestroyInstance di = (PFN_vkDestroyInstance)hal->GetInstanceProcAddr(a, "vkDestroyInstance");
    uint32_t n1 = 16, n2 = 16;
    VkPhysicalDevice p1[16], p2[16];
    epd(a, &n1, p1);
    epd(b, &n2, p2);
    ok(n1 == base_n + 1 && n2 == base_n + 1 && p1[0] != p2[0], "each instance has its own NVK instance (device 0)");
    di(a, NULL);
    ok(nvk_loaded(g_fake_nvk), "still loaded while the second instance lives");
    di(b, NULL);
    ok(!nvk_loaded(g_fake_nvk), "unloaded after the last NVK instance");
  }

  printf("== wave 1 (kept): GetInstanceProcAddr surface\n");
  {
    VkInstanceCreateInfo ci = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    VkInstance a = VK_NULL_HANDLE;
    hal->CreateInstance(&ci, NULL, &a);
    ok(hal->GetInstanceProcAddr(a, "vkGetPhysicalDeviceProperties") != NULL, "PD trampoline offered when the base has it");
    ok(hal->GetInstanceProcAddr(a, "vkGetPhysicalDeviceFeatures2") == NULL,
       "1.0 instance without VK_KHR_get_physical_device_properties2: the base has no vkGetPhysicalDeviceFeatures2 -> NULL");
    ok(hal->GetInstanceProcAddr(a, "vkNotARealCommandGB") == NULL, "unknown command -> NULL");
    ok(hal->GetInstanceProcAddr(a, "vkCreateDevice") != NULL && hal->GetInstanceProcAddr(a, "vkGetDeviceProcAddr") != NULL,
       "vkCreateDevice / vkGetDeviceProcAddr offered");
    ok(hal->GetInstanceProcAddr(a, "vkCmdDraw") == NULL,
       "device command through vkGetInstanceProcAddr -> NULL (libvulkan uses vkGetDeviceProcAddr)");
    PFN_vkDestroyInstance di = (PFN_vkDestroyInstance)hal->GetInstanceProcAddr(a, "vkDestroyInstance");
    di(a, NULL);
  }

  if (g_real_nvk) {
    printf("== GD-1B-09: real NVK (drm-shim, NOUVEAU_CHIPSET=%s) as the NVIDIA driver\n",
           getenv("NOUVEAU_CHIPSET") ? getenv("NOUVEAU_CHIPSET") : "?");
    setenv("GB_SHIM_NVK_ICD", g_real_nvk, 1);
    props("vendor.gb.gfx_active=nvidia");
    run_instance("vkcube", "vkcube", 1, 1, &res, g_real_nvk);
    ok(nvk_first(&res, base_n), "NVK listed first: [0] '%s' 0x%x", res.name[0], res.vendor[0]);
    ok(res.dev_result[0] == VK_SUCCESS && res.magic_bad == 0, "NVK device created through the shim");
    real_nvk_release_checks(g_real_nvk);
  }

  rm_rf(g_root);
  printf("RESULT: %s (%d passed, %d failed)\n", g_fail ? "FAIL" : "PASS", g_pass, g_fail);
  return g_fail ? 1 : 0;
}
