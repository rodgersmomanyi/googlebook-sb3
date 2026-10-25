/*
 * Minimal fake Vulkan ICD for the shim unit tests (builder only, never shipped). One physical device whose vendorID /
 * name come from compile-time defines (FAKE_VENDOR, FAKE_NAME); exports vk_icdGetInstanceProcAddr and, with
 * -DFAKE_HMI, an Android hwvulkan HMI. Failure injection by environment: FAKE_ICD_FAIL=instance|enumerate|device.
 * FAKE_ICD_LIVE (out): number of live instances, for leak checks through dlsym("fake_icd_live").
 * SPDX-License-Identifier: MIT
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define VK_NO_PROTOTYPES 1
#include <vulkan/vulkan.h>

#ifndef FAKE_VENDOR
#define FAKE_VENDOR 0x10de
#endif
#ifndef FAKE_NAME
#define FAKE_NAME "fake NVIDIA TU117 (test ICD)"
#endif
#define EXPORT __attribute__((visibility("default")))
#define MAGIC 0x01CDC0DEu

struct obj { uintptr_t magic; };
struct inst { struct obj o; struct obj pd; }; /* one physical device per instance, like real drivers */
EXPORT int fake_icd_live;

static int fail(const char *what) {
#ifdef FAKE_NO_FAIL /* the "platform driver" fake: failure injection is only for the NVK-role fake */
  (void)what;
  return 0;
#else
  const char *f = getenv("FAKE_ICD_FAIL");
  return f && !strcmp(f, what);
#endif
}

static VKAPI_ATTR VkResult VKAPI_CALL f_EnumerateInstanceExtensionProperties(const char *l, uint32_t *n,
                                                                            VkExtensionProperties *p) {
  static const char *ext[] = {"VK_KHR_get_physical_device_properties2", "VK_EXT_debug_utils"};
  (void)l;
  if (!p) {
    *n = 2;
    return VK_SUCCESS;
  }
  uint32_t k = *n < 2 ? *n : 2;
  for (uint32_t i = 0; i < k; i++) {
    memset(&p[i], 0, sizeof p[i]);
    strcpy(p[i].extensionName, ext[i]);
    p[i].specVersion = 1;
  }
  *n = k;
  return k < 2 ? VK_INCOMPLETE : VK_SUCCESS;
}
static VKAPI_ATTR VkResult VKAPI_CALL f_EnumerateInstanceVersion(uint32_t *v) {
  *v = VK_API_VERSION_1_3;
  return VK_SUCCESS;
}
static VKAPI_ATTR VkResult VKAPI_CALL f_CreateInstance(const VkInstanceCreateInfo *ci, const VkAllocationCallbacks *a,
                                                      VkInstance *out) {
  (void)a;
  if (fail("instance")) return VK_ERROR_INITIALIZATION_FAILED;
  for (uint32_t i = 0; i < ci->enabledExtensionCount; i++)
    if (strcmp(ci->ppEnabledExtensionNames[i], "VK_KHR_get_physical_device_properties2") &&
        strcmp(ci->ppEnabledExtensionNames[i], "VK_EXT_debug_utils"))
      return VK_ERROR_EXTENSION_NOT_PRESENT;
  struct inst *o = malloc(sizeof *o);
  o->o.magic = MAGIC;
  o->pd.magic = MAGIC;
  *out = (VkInstance)o;
  fake_icd_live++;
  return VK_SUCCESS;
}
static VKAPI_ATTR void VKAPI_CALL f_DestroyInstance(VkInstance i, const VkAllocationCallbacks *a) {
  (void)a;
  if (i) {
    free(i);
    fake_icd_live--;
  }
}
static VKAPI_ATTR VkResult VKAPI_CALL f_EnumeratePhysicalDevices(VkInstance i, uint32_t *n, VkPhysicalDevice *p) {
  if (fail("enumerate")) {
    *n = 0;
    return VK_SUCCESS;
  }
  if (!p) {
    *n = 1;
    return VK_SUCCESS;
  }
  if (*n < 1) return VK_INCOMPLETE;
  p[0] = (VkPhysicalDevice)&((struct inst *)i)->pd;
  *n = 1;
  return VK_SUCCESS;
}
static VKAPI_ATTR void VKAPI_CALL f_GetPhysicalDeviceProperties(VkPhysicalDevice pd, VkPhysicalDeviceProperties *p) {
  (void)pd;
  memset(p, 0, sizeof *p);
  p->apiVersion = VK_API_VERSION_1_3;
  p->vendorID = FAKE_VENDOR;
  p->deviceID = 0x1f91;
  p->deviceType = VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU;
  strcpy(p->deviceName, FAKE_NAME);
}
static VKAPI_ATTR void VKAPI_CALL f_GetPhysicalDeviceProperties2(VkPhysicalDevice pd, VkPhysicalDeviceProperties2 *p) {
  f_GetPhysicalDeviceProperties(pd, &p->properties);
}
static VKAPI_ATTR void VKAPI_CALL f_GetPhysicalDeviceQueueFamilyProperties(VkPhysicalDevice pd, uint32_t *n,
                                                                          VkQueueFamilyProperties *q) {
  (void)pd;
  if (!q) {
    *n = 1;
    return;
  }
  memset(q, 0, sizeof *q);
  q->queueFlags = VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT | VK_QUEUE_TRANSFER_BIT;
  q->queueCount = 1;
  *n = 1;
}
static VKAPI_ATTR VkResult VKAPI_CALL f_EnumerateDeviceExtensionProperties(VkPhysicalDevice pd, const char *l,
                                                                          uint32_t *n, VkExtensionProperties *p) {
  (void)pd;
  (void)l;
  (void)p;
  *n = 0;
  return VK_SUCCESS;
}
static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL f_GetDeviceProcAddr(VkDevice d, const char *name);
static VKAPI_ATTR VkResult VKAPI_CALL f_CreateDevice(VkPhysicalDevice pd, const VkDeviceCreateInfo *ci,
                                                    const VkAllocationCallbacks *a, VkDevice *out) {
  (void)pd;
  (void)ci;
  (void)a;
  if (fail("device")) return VK_ERROR_INITIALIZATION_FAILED;
  struct obj *o = malloc(2 * sizeof *o); /* [0] device, [1] its queue */
  o[0].magic = MAGIC;
  o[1].magic = MAGIC;
  *out = (VkDevice)o;
  return VK_SUCCESS;
}
static VKAPI_ATTR void VKAPI_CALL f_DestroyDevice(VkDevice d, const VkAllocationCallbacks *a) {
  (void)a;
  free(d);
}
static VKAPI_ATTR void VKAPI_CALL f_GetDeviceQueue(VkDevice d, uint32_t f, uint32_t i, VkQueue *q) {
  (void)f;
  (void)i;
  *q = (VkQueue)((struct obj *)d + 1);
}
static VKAPI_ATTR VkResult VKAPI_CALL f_QueueWaitIdle(VkQueue q) {
  (void)q;
  return VK_SUCCESS;
}

static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL f_GetDeviceProcAddr(VkDevice d, const char *name) {
  (void)d;
#define F(n) \
  if (!strcmp(name, "vk" #n)) return (PFN_vkVoidFunction)f_##n
  F(GetDeviceProcAddr);
  F(DestroyDevice);
  F(GetDeviceQueue);
  F(QueueWaitIdle);
  return NULL;
}

EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vk_icdGetInstanceProcAddr(VkInstance inst, const char *name) {
  F(EnumerateInstanceExtensionProperties);
  F(EnumerateInstanceVersion);
  F(CreateInstance);
  if (!inst) return NULL;
  F(DestroyInstance);
  F(EnumeratePhysicalDevices);
  F(GetPhysicalDeviceProperties);
  F(GetPhysicalDeviceProperties2);
  F(GetPhysicalDeviceQueueFamilyProperties);
  F(EnumerateDeviceExtensionProperties);
  F(CreateDevice);
  F(GetDeviceProcAddr);
#undef F
  return NULL;
}

EXPORT VKAPI_ATTR VkResult VKAPI_CALL vk_icdNegotiateLoaderICDInterfaceVersion(uint32_t *v) {
  if (*v > 5) *v = 5;
  return VK_SUCCESS;
}
