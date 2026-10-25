/*
 * libgb_nvk_loader.so - minimal private Vulkan loader stub for gb_gpu_compute (INTERFACES 11.2, plan Phase 6).
 *
 * The lapis system libvulkan.so only loads vulkan.<ro.hardware.vulkan>.so (ANV), so the compute service links
 * against this stub instead. It loads exactly one ICD, never a layer:
 *   env GB_NVK_ICD (tests: lavapipe libvulkan_lvp.so), else /odm/dgpu/lib64/hw/vulkan.nouveau.so (payload NVK).
 * It negotiates with vk_icdNegotiateLoaderICDInterfaceVersion and resolves everything through the ICD's
 * vk_icdGetInstanceProcAddr. Handles are passed through unwrapped; like every loader, the stub stores its dispatch
 * pointer in the first word of each dispatchable handle (the ICD puts ICD_LOADER_MAGIC there for that purpose), so
 * the exported Vulkan 1.0-1.3 core trampolines can dispatch per handle.
 *
 * vkGetInstanceProcAddr / vkGetDeviceProcAddr return the stub's own functions for the commands that create, destroy
 * or enumerate dispatchable objects (so the dispatch pointer is always set) and the ICD's pointers for everything
 * else (no extra hop).
 *
 * The file name and SONAME are libgb_nvk_loader.so and must never be libvulkan.so (INTERFACES 11.1, plan A26).
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define VK_NO_PROTOTYPES
#include <vulkan/vulkan_core.h>
#include <vulkan/vk_icd.h>

#ifdef __ANDROID__
#include <android/log.h>
#endif

#define GB_EXPORT __attribute__((visibility("default")))
#define GB_DEFAULT_ICD "/odm/dgpu/lib64/hw/vulkan.nouveau.so"
#define GB_ICD_IFACE_MAX 5u /* loader/ICD interface version we implement (no surfaces, no layers) */

static void gb_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void gb_log(const char *fmt, ...)
{
    char b[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
#ifdef __ANDROID__
    __android_log_write(ANDROID_LOG_WARN, "gb_nvk_loader", b);
#else
    fprintf(stderr, "gb_nvk_loader: %s\n", b);
#endif
}

static void gb_missing(const char *name)
{
    gb_log("%s: not provided by the ICD for this handle", name);
}

struct gb_inst_fns;
struct gb_dev_fns;
static const struct gb_inst_fns *gb_inst_fns_of(const void *h);
static const struct gb_dev_fns *gb_dev_fns_of(const void *h);

#include "gb_vk_trampolines.inc"

/* The first member of both structs is the function table: a handle's first word points at the struct. */
struct gb_instance {
    struct gb_inst_fns fns;
    VkInstance icd;
    PFN_vkGetInstanceProcAddr gipa;
    PFN_vkGetDeviceProcAddr gdpa;
    PFN_vkEnumeratePhysicalDeviceGroups epdg_khr;
};

struct gb_device {
    struct gb_dev_fns fns;
    VkDevice icd;
    PFN_vkGetDeviceProcAddr gdpa;
    const struct gb_instance *inst;
};

static const void *gb_disp_word(const void *h)
{
    if (!h)
        return NULL;
    const void *p = *(const void *const *)h;
    if ((uintptr_t)p == ICD_LOADER_MAGIC || !p)
        return NULL; /* never seen by the stub (should not happen) */
    return p;
}
static const struct gb_inst_fns *gb_inst_fns_of(const void *h) { return gb_disp_word(h); }
static const struct gb_dev_fns *gb_dev_fns_of(const void *h) { return gb_disp_word(h); }
static struct gb_instance *gb_inst(const void *h) { return (struct gb_instance *)(uintptr_t)gb_disp_word(h); }
static struct gb_device *gb_dev(const void *h) { return (struct gb_device *)(uintptr_t)gb_disp_word(h); }

/* Store our dispatch pointer in a dispatchable handle created by the ICD. */
static int gb_set_disp(void *h, const void *disp, const char *what)
{
    uintptr_t *w = (uintptr_t *)h;
    if (!w)
        return -1;
    if (*w != ICD_LOADER_MAGIC && *w != (uintptr_t)disp) {
        gb_log("%s: ICD handle without loader magic (0x%lx); refusing", what, (unsigned long)*w);
        return -1;
    }
    *w = (uintptr_t)disp;
    return 0;
}

/* ---- ICD loading (once per process) ---------------------------------------------------------------------------- */
static struct {
    pthread_once_t once;
    void *dl;
    PFN_vkGetInstanceProcAddr gipa;
    uint32_t iface;
    int ok;
} g_icd = {PTHREAD_ONCE_INIT, NULL, NULL, 0, 0};

static void gb_icd_load_once(void)
{
    const char *path = getenv("GB_NVK_ICD");
    if (!path || !*path)
        path = GB_DEFAULT_ICD;
    g_icd.dl = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!g_icd.dl) {
        const char *e = dlerror();
        gb_log("cannot load ICD %s: %s", path, e ? e : "?");
        return;
    }
    PFN_vkNegotiateLoaderICDInterfaceVersion neg =
        (PFN_vkNegotiateLoaderICDInterfaceVersion)dlsym(g_icd.dl, "vk_icdNegotiateLoaderICDInterfaceVersion");
    uint32_t v = GB_ICD_IFACE_MAX;
    if (neg) {
        VkResult r = neg(&v);
        if (r != VK_SUCCESS) {
            gb_log("ICD %s refused loader interface %u (%d)", path, GB_ICD_IFACE_MAX, (int)r);
            return;
        }
        if (v > GB_ICD_IFACE_MAX)
            v = GB_ICD_IFACE_MAX;
    } else {
        v = 1; /* pre-negotiation ICD */
    }
    g_icd.gipa = (PFN_vkGetInstanceProcAddr)dlsym(g_icd.dl, "vk_icdGetInstanceProcAddr");
    if (!g_icd.gipa) {
        gb_log("ICD %s does not export vk_icdGetInstanceProcAddr", path);
        return;
    }
    g_icd.iface = v;
    g_icd.ok = 1;
}

static int gb_icd(void)
{
    pthread_once(&g_icd.once, gb_icd_load_once);
    return g_icd.ok;
}

/* ---- global commands ----------------------------------------------------------------------------------------- */
GB_EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateInstanceVersion(uint32_t *pApiVersion)
{
    if (!pApiVersion)
        return VK_ERROR_INITIALIZATION_FAILED;
    uint32_t v = VK_API_VERSION_1_0;
    if (gb_icd()) {
        PFN_vkEnumerateInstanceVersion f = (PFN_vkEnumerateInstanceVersion)g_icd.gipa(NULL, "vkEnumerateInstanceVersion");
        if (f && f(&v) != VK_SUCCESS)
            v = VK_API_VERSION_1_0;
    }
    /* the stub exports the 1.3 core: report at most 1.3 */
    if (VK_API_VERSION_MAJOR(v) > 1 || (VK_API_VERSION_MAJOR(v) == 1 && VK_API_VERSION_MINOR(v) > 3))
        v = VK_MAKE_API_VERSION(0, 1, 3, VK_HEADER_VERSION);
    *pApiVersion = v;
    return VK_SUCCESS;
}

GB_EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateInstanceLayerProperties(uint32_t *pPropertyCount,
                                                                            VkLayerProperties *pProperties)
{
    (void)pProperties;
    if (!pPropertyCount)
        return VK_ERROR_INITIALIZATION_FAILED;
    *pPropertyCount = 0; /* no layers, ever */
    return VK_SUCCESS;
}

GB_EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateInstanceExtensionProperties(const char *pLayerName,
                                                                                uint32_t *pPropertyCount,
                                                                                VkExtensionProperties *pProperties)
{
    if (!pPropertyCount)
        return VK_ERROR_INITIALIZATION_FAILED;
    if (pLayerName)
        return VK_ERROR_LAYER_NOT_PRESENT;
    if (!gb_icd()) {
        *pPropertyCount = 0;
        return VK_SUCCESS;
    }
    PFN_vkEnumerateInstanceExtensionProperties f =
        (PFN_vkEnumerateInstanceExtensionProperties)g_icd.gipa(NULL, "vkEnumerateInstanceExtensionProperties");
    if (!f) {
        *pPropertyCount = 0;
        return VK_SUCCESS;
    }
    return f(NULL, pPropertyCount, pProperties);
}

GB_EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkCreateInstance(const VkInstanceCreateInfo *pCreateInfo,
                                                          const VkAllocationCallbacks *pAllocator,
                                                          VkInstance *pInstance)
{
    if (!pCreateInfo || !pInstance)
        return VK_ERROR_INITIALIZATION_FAILED;
    if (!gb_icd())
        return VK_ERROR_INCOMPATIBLE_DRIVER;
    if (pCreateInfo->enabledLayerCount)
        return VK_ERROR_LAYER_NOT_PRESENT;
    PFN_vkCreateInstance create = (PFN_vkCreateInstance)g_icd.gipa(NULL, "vkCreateInstance");
    if (!create)
        return VK_ERROR_INCOMPATIBLE_DRIVER;
    struct gb_instance *gi = calloc(1, sizeof *gi);
    if (!gi)
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    VkInstance inst = VK_NULL_HANDLE;
    VkResult r = create(pCreateInfo, pAllocator, &inst);
    if (r != VK_SUCCESS) {
        free(gi);
        return r;
    }
    gi->icd = inst;
    gi->gipa = g_icd.gipa;
    gb_fill_inst_fns(&gi->fns, inst, g_icd.gipa);
    gi->gdpa = (PFN_vkGetDeviceProcAddr)g_icd.gipa(inst, "vkGetDeviceProcAddr");
    gi->epdg_khr = (PFN_vkEnumeratePhysicalDeviceGroups)g_icd.gipa(inst, "vkEnumeratePhysicalDeviceGroupsKHR");
    if (!gi->fns.DestroyInstance || !gi->gdpa || gb_set_disp(inst, gi, "vkCreateInstance")) {
        if (gi->fns.DestroyInstance)
            gi->fns.DestroyInstance(inst, pAllocator);
        free(gi);
        return VK_ERROR_INITIALIZATION_FAILED;
    }
    *pInstance = inst;
    return VK_SUCCESS;
}

/* ---- instance-level commands that touch dispatchable objects -------------------------------------------------- */
GB_EXPORT VKAPI_ATTR void VKAPI_CALL vkDestroyInstance(VkInstance instance, const VkAllocationCallbacks *pAllocator)
{
    struct gb_instance *gi = gb_inst(instance);
    if (!gi)
        return;
    gi->fns.DestroyInstance(instance, pAllocator);
    free(gi);
}

GB_EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkEnumeratePhysicalDevices(VkInstance instance, uint32_t *pCount,
                                                                    VkPhysicalDevice *pDevices)
{
    struct gb_instance *gi = gb_inst(instance);
    if (!gi || !gi->fns.EnumeratePhysicalDevices)
        return VK_ERROR_INITIALIZATION_FAILED;
    VkResult r = gi->fns.EnumeratePhysicalDevices(instance, pCount, pDevices);
    if ((r == VK_SUCCESS || r == VK_INCOMPLETE) && pDevices && pCount)
        for (uint32_t i = 0; i < *pCount; i++)
            if (gb_set_disp(pDevices[i], gi, "vkEnumeratePhysicalDevices"))
                return VK_ERROR_INITIALIZATION_FAILED;
    return r;
}

static VkResult gb_epdg(PFN_vkEnumeratePhysicalDeviceGroups f, struct gb_instance *gi, VkInstance instance,
                        uint32_t *pCount, VkPhysicalDeviceGroupProperties *pProps)
{
    if (!f)
        return VK_ERROR_INITIALIZATION_FAILED;
    VkResult r = f(instance, pCount, pProps);
    if ((r == VK_SUCCESS || r == VK_INCOMPLETE) && pProps && pCount)
        for (uint32_t i = 0; i < *pCount; i++)
            for (uint32_t j = 0; j < pProps[i].physicalDeviceCount && j < VK_MAX_DEVICE_GROUP_SIZE; j++)
                if (gb_set_disp(pProps[i].physicalDevices[j], gi, "vkEnumeratePhysicalDeviceGroups"))
                    return VK_ERROR_INITIALIZATION_FAILED;
    return r;
}

GB_EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkEnumeratePhysicalDeviceGroups(VkInstance instance, uint32_t *pCount,
                                                                         VkPhysicalDeviceGroupProperties *pProps)
{
    struct gb_instance *gi = gb_inst(instance);
    if (!gi)
        return VK_ERROR_INITIALIZATION_FAILED;
    return gb_epdg(gi->fns.EnumeratePhysicalDeviceGroups, gi, instance, pCount, pProps);
}

static VKAPI_ATTR VkResult VKAPI_CALL gb_EnumeratePhysicalDeviceGroupsKHR(VkInstance instance, uint32_t *pCount,
                                                                          VkPhysicalDeviceGroupProperties *pProps)
{
    struct gb_instance *gi = gb_inst(instance);
    if (!gi)
        return VK_ERROR_INITIALIZATION_FAILED;
    return gb_epdg(gi->epdg_khr, gi, instance, pCount, pProps);
}

GB_EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateDeviceLayerProperties(VkPhysicalDevice physicalDevice,
                                                                          uint32_t *pPropertyCount,
                                                                          VkLayerProperties *pProperties)
{
    (void)physicalDevice;
    (void)pProperties;
    if (!pPropertyCount)
        return VK_ERROR_INITIALIZATION_FAILED;
    *pPropertyCount = 0;
    return VK_SUCCESS;
}

GB_EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateDeviceExtensionProperties(VkPhysicalDevice physicalDevice,
                                                                              const char *pLayerName,
                                                                              uint32_t *pPropertyCount,
                                                                              VkExtensionProperties *pProperties)
{
    struct gb_instance *gi = gb_inst(physicalDevice);
    if (!gi || !gi->fns.EnumerateDeviceExtensionProperties)
        return VK_ERROR_INITIALIZATION_FAILED;
    if (pLayerName)
        return VK_ERROR_LAYER_NOT_PRESENT;
    return gi->fns.EnumerateDeviceExtensionProperties(physicalDevice, NULL, pPropertyCount, pProperties);
}

GB_EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkCreateDevice(VkPhysicalDevice physicalDevice,
                                                        const VkDeviceCreateInfo *pCreateInfo,
                                                        const VkAllocationCallbacks *pAllocator, VkDevice *pDevice)
{
    struct gb_instance *gi = gb_inst(physicalDevice);
    if (!gi || !gi->fns.CreateDevice || !pDevice)
        return VK_ERROR_INITIALIZATION_FAILED;
    struct gb_device *gd = calloc(1, sizeof *gd);
    if (!gd)
        return VK_ERROR_OUT_OF_HOST_MEMORY;
    VkDevice dev = VK_NULL_HANDLE;
    VkResult r = gi->fns.CreateDevice(physicalDevice, pCreateInfo, pAllocator, &dev);
    if (r != VK_SUCCESS) {
        free(gd);
        return r;
    }
    gd->icd = dev;
    gd->inst = gi;
    gd->gdpa = gi->gdpa;
    gb_fill_dev_fns(&gd->fns, dev, gd->gdpa);
    if (!gd->fns.DestroyDevice || gb_set_disp(dev, gd, "vkCreateDevice")) {
        if (gd->fns.DestroyDevice)
            gd->fns.DestroyDevice(dev, pAllocator);
        free(gd);
        return VK_ERROR_INITIALIZATION_FAILED;
    }
    *pDevice = dev;
    return VK_SUCCESS;
}

/* ---- device-level commands that touch dispatchable objects ---------------------------------------------------- */
GB_EXPORT VKAPI_ATTR void VKAPI_CALL vkDestroyDevice(VkDevice device, const VkAllocationCallbacks *pAllocator)
{
    struct gb_device *gd = gb_dev(device);
    if (!gd)
        return;
    gd->fns.DestroyDevice(device, pAllocator);
    free(gd);
}

GB_EXPORT VKAPI_ATTR void VKAPI_CALL vkGetDeviceQueue(VkDevice device, uint32_t queueFamilyIndex, uint32_t queueIndex,
                                                      VkQueue *pQueue)
{
    struct gb_device *gd = gb_dev(device);
    if (!gd || !gd->fns.GetDeviceQueue || !pQueue) {
        if (pQueue)
            *pQueue = VK_NULL_HANDLE;
        return;
    }
    gd->fns.GetDeviceQueue(device, queueFamilyIndex, queueIndex, pQueue);
    if (*pQueue && gb_set_disp(*pQueue, gd, "vkGetDeviceQueue"))
        *pQueue = VK_NULL_HANDLE;
}

GB_EXPORT VKAPI_ATTR void VKAPI_CALL vkGetDeviceQueue2(VkDevice device, const VkDeviceQueueInfo2 *pQueueInfo,
                                                       VkQueue *pQueue)
{
    struct gb_device *gd = gb_dev(device);
    if (!gd || !gd->fns.GetDeviceQueue2 || !pQueue) {
        if (pQueue)
            *pQueue = VK_NULL_HANDLE;
        return;
    }
    gd->fns.GetDeviceQueue2(device, pQueueInfo, pQueue);
    if (*pQueue && gb_set_disp(*pQueue, gd, "vkGetDeviceQueue2"))
        *pQueue = VK_NULL_HANDLE;
}

GB_EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkAllocateCommandBuffers(VkDevice device,
                                                                  const VkCommandBufferAllocateInfo *pAllocateInfo,
                                                                  VkCommandBuffer *pCommandBuffers)
{
    struct gb_device *gd = gb_dev(device);
    if (!gd || !gd->fns.AllocateCommandBuffers || !pAllocateInfo)
        return VK_ERROR_INITIALIZATION_FAILED;
    VkResult r = gd->fns.AllocateCommandBuffers(device, pAllocateInfo, pCommandBuffers);
    if (r != VK_SUCCESS)
        return r;
    for (uint32_t i = 0; i < pAllocateInfo->commandBufferCount; i++)
        if (gb_set_disp(pCommandBuffers[i], gd, "vkAllocateCommandBuffers")) {
            if (gd->fns.FreeCommandBuffers)
                gd->fns.FreeCommandBuffers(device, pAllocateInfo->commandPool, pAllocateInfo->commandBufferCount,
                                           pCommandBuffers);
            for (uint32_t j = 0; j < pAllocateInfo->commandBufferCount; j++)
                pCommandBuffers[j] = VK_NULL_HANDLE;
            return VK_ERROR_INITIALIZATION_FAILED;
        }
    return VK_SUCCESS;
}

/* ---- proc addr ------------------------------------------------------------------------------------------------ */
GB_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetDeviceProcAddr(VkDevice device, const char *pName);
GB_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr(VkInstance instance, const char *pName);

struct gb_icept {
    const char *name;
    PFN_vkVoidFunction fn;
};
#define GB_IC(n) {#n, (PFN_vkVoidFunction)n}
static const struct gb_icept gb_global_icepts[] = {
    GB_IC(vkGetInstanceProcAddr), GB_IC(vkCreateInstance), GB_IC(vkEnumerateInstanceExtensionProperties),
    GB_IC(vkEnumerateInstanceLayerProperties), GB_IC(vkEnumerateInstanceVersion),
};
static const struct gb_icept gb_inst_icepts[] = {
    GB_IC(vkDestroyInstance), GB_IC(vkEnumeratePhysicalDevices), GB_IC(vkEnumeratePhysicalDeviceGroups),
    {"vkEnumeratePhysicalDeviceGroupsKHR", (PFN_vkVoidFunction)gb_EnumeratePhysicalDeviceGroupsKHR},
    GB_IC(vkCreateDevice), GB_IC(vkEnumerateDeviceLayerProperties), GB_IC(vkEnumerateDeviceExtensionProperties),
};
static const struct gb_icept gb_dev_icepts[] = {
    GB_IC(vkGetDeviceProcAddr), GB_IC(vkDestroyDevice), GB_IC(vkGetDeviceQueue), GB_IC(vkGetDeviceQueue2),
    GB_IC(vkAllocateCommandBuffers),
};

static PFN_vkVoidFunction gb_find(const struct gb_icept *t, size_t n, const char *name)
{
    for (size_t i = 0; i < n; i++)
        if (!strcmp(t[i].name, name))
            return t[i].fn;
    return NULL;
}
#define GB_FIND(t, name) gb_find(t, sizeof(t) / sizeof((t)[0]), name)

GB_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr(VkInstance instance, const char *pName)
{
    if (!pName)
        return NULL;
    PFN_vkVoidFunction f = GB_FIND(gb_global_icepts, pName);
    if (f)
        return f;
    if (!instance)
        return NULL;
    struct gb_instance *gi = gb_inst(instance);
    if (!gi)
        return NULL;
    if ((f = GB_FIND(gb_inst_icepts, pName)) || (f = GB_FIND(gb_dev_icepts, pName)))
        return f;
    return gi->gipa(instance, pName);
}

GB_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetDeviceProcAddr(VkDevice device, const char *pName)
{
    if (!pName || !device)
        return NULL;
    struct gb_device *gd = gb_dev(device);
    if (!gd)
        return NULL;
    PFN_vkVoidFunction f = GB_FIND(gb_dev_icepts, pName);
    if (f)
        return f;
    return gd->gdpa(device, pName);
}
