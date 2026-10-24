/*
 * hmi_check - independent re-implementation of what Android's libvulkan (frameworks/native/vulkan/libvulkan
 * driver.cpp: LoadDriver / Hal::Open / CreateInstance / SetData magic check) does with a hwvulkan HAL .so.
 * Verification-only tool (nvk-android adversarial check); not shipped.
 */
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <hardware/hwvulkan.h>
#include <vulkan/vk_android_native_buffer.h>

static int fails;
#define CHECK(c, ...) do { if (c) { printf("  ok   "); } else { printf("  FAIL "); fails++; } printf(__VA_ARGS__); printf("\n"); } while (0)

static int magic_ok(const void *h) { return h && *(const uintptr_t *)h == HWVULKAN_DISPATCH_MAGIC; }

int main(int argc, char **argv) {
  const char *path = argc > 1 ? argv[1] : "/vendor/lib64/hw/vulkan.nouveau.so";
  void *so = dlopen(path, RTLD_LOCAL | RTLD_NOW); /* libvulkan: LIB_DL_FLAGS = RTLD_LOCAL | RTLD_NOW */
  if (!so) { printf("FAIL dlopen %s: %s\n", path, dlerror()); return 2; }
  printf("dlopen %s ok\n", path);
  hw_module_t *hmi = (hw_module_t *)dlsym(so, HAL_MODULE_INFO_SYM_AS_STR);
  CHECK(hmi != NULL, "dlsym(%s) = %p", HAL_MODULE_INFO_SYM_AS_STR, (void *)hmi);
  if (!hmi) return 2;
  CHECK(hmi->tag == HARDWARE_MODULE_TAG, "module tag 0x%08x", hmi->tag);
  CHECK(hmi->id && strcmp(hmi->id, HWVULKAN_HARDWARE_MODULE_ID) == 0, "module id '%s'", hmi->id ? hmi->id : "(null)");
  CHECK(hmi->module_api_version == HWVULKAN_MODULE_API_VERSION_0_1, "module_api_version 0x%x", hmi->module_api_version);
  printf("  info name='%s' author='%s' hal_api_version=0x%x\n", hmi->name, hmi->author, hmi->hal_api_version);
  hmi->dso = so; /* libvulkan writes this: HMI must be in writable (non-RELRO) memory */
  CHECK(hmi->dso == so, "hmi->dso writable");
  hwvulkan_device_t *dev = NULL;
  int r = hmi->methods->open(hmi, HWVULKAN_DEVICE_0, (hw_device_t **)&dev);
  CHECK(r == 0 && dev, "open(%s) = %d", HWVULKAN_DEVICE_0, r);
  if (!dev) return 2;
  CHECK(dev->common.tag == HARDWARE_DEVICE_TAG, "device tag 0x%08x", dev->common.tag);
  CHECK(dev->common.version == HWVULKAN_DEVICE_API_VERSION_0_1, "device version 0x%x", dev->common.version);
  CHECK(dev->common.module == hmi, "device->module == HMI");
  CHECK(dev->EnumerateInstanceExtensionProperties && dev->CreateInstance && dev->GetInstanceProcAddr,
        "device has EnumerateInstanceExtensionProperties/CreateInstance/GetInstanceProcAddr");
  uint32_t n = 0;
  VkResult vr = dev->EnumerateInstanceExtensionProperties(NULL, &n, NULL);
  VkExtensionProperties *ep = calloc(n ? n : 1, sizeof *ep);
  dev->EnumerateInstanceExtensionProperties(NULL, &n, ep);
  printf("  %u instance extensions (%d):", n, vr);
  for (uint32_t i = 0; i < n; i++) printf(" %s", ep[i].extensionName);
  printf("\n");
  PFN_vkEnumerateInstanceVersion eiv =
      (PFN_vkEnumerateInstanceVersion)dev->GetInstanceProcAddr(VK_NULL_HANDLE, "vkEnumerateInstanceVersion");
  uint32_t iv = 0;
  if (eiv) eiv(&iv);
  CHECK(eiv && iv >= VK_API_VERSION_1_1, "vkEnumerateInstanceVersion -> %u.%u.%u", VK_API_VERSION_MAJOR(iv),
        VK_API_VERSION_MINOR(iv), VK_API_VERSION_PATCH(iv));
  VkApplicationInfo ai = {.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .apiVersion = VK_API_VERSION_1_3};
  VkInstanceCreateInfo ici = {.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &ai};
  VkInstance inst = VK_NULL_HANDLE;
  vr = dev->CreateInstance(&ici, NULL, &inst);
  CHECK(vr == VK_SUCCESS, "CreateInstance = %d", vr);
  if (vr) return 2;
  CHECK(magic_ok(inst), "VkInstance dispatch magic 0x%lx (libvulkan SetData requires 0x%x)",
        (unsigned long)*(uintptr_t *)inst, HWVULKAN_DISPATCH_MAGIC);
  static const char *ifn[] = {"vkGetDeviceProcAddr", "vkEnumeratePhysicalDevices", "vkGetPhysicalDeviceProperties2",
                              "vkEnumeratePhysicalDeviceGroups", "vkGetPhysicalDeviceImageFormatProperties2",
                              "vkDestroyInstance", "vkCreateDevice", "vkEnumerateDeviceExtensionProperties"};
  for (size_t i = 0; i < sizeof ifn / sizeof *ifn; i++)
    CHECK(dev->GetInstanceProcAddr(inst, ifn[i]) != NULL, "GetInstanceProcAddr(%s)", ifn[i]);
  PFN_vkEnumeratePhysicalDevices epd = (PFN_vkEnumeratePhysicalDevices)dev->GetInstanceProcAddr(inst, "vkEnumeratePhysicalDevices");
  PFN_vkEnumerateDeviceExtensionProperties edep =
      (PFN_vkEnumerateDeviceExtensionProperties)dev->GetInstanceProcAddr(inst, "vkEnumerateDeviceExtensionProperties");
  PFN_vkCreateDevice cd = (PFN_vkCreateDevice)dev->GetInstanceProcAddr(inst, "vkCreateDevice");
  PFN_vkGetDeviceProcAddr gdpa = (PFN_vkGetDeviceProcAddr)dev->GetInstanceProcAddr(inst, "vkGetDeviceProcAddr");
  uint32_t np = 0;
  vr = epd(inst, &np, NULL);
  printf("  %u physical device(s) (%d)\n", np, vr);
  if (np) {
    VkPhysicalDevice pd[8];
    np = np > 8 ? 8 : np;
    epd(inst, &np, pd);
    CHECK(magic_ok(pd[0]), "VkPhysicalDevice dispatch magic");
    uint32_t ne = 0;
    edep(pd[0], NULL, &ne, NULL);
    VkExtensionProperties *de = calloc(ne, sizeof *de);
    edep(pd[0], NULL, &ne, de);
    int anb = 0, ahb = 0, sfd = 0, qff = 0;
    uint32_t anb_ver = 0;
    for (uint32_t i = 0; i < ne; i++) {
      if (!strcmp(de[i].extensionName, "VK_ANDROID_native_buffer")) { anb = 1; anb_ver = de[i].specVersion; }
      if (!strcmp(de[i].extensionName, "VK_ANDROID_external_memory_android_hardware_buffer")) ahb = 1;
      if (!strcmp(de[i].extensionName, "VK_KHR_external_semaphore_fd")) sfd = 1;
      if (!strcmp(de[i].extensionName, "VK_EXT_queue_family_foreign")) qff = 1;
      if (!strcmp(de[i].extensionName, "VK_NVX_binary_import")) printf("  NOTE: VK_NVX_binary_import advertised\n");
    }
    printf("  device: %u exts, ANDROID_native_buffer=%d (spec %u), AHB=%d, KHR_external_semaphore_fd=%d, EXT_queue_family_foreign=%d\n",
           ne, anb, anb_ver, ahb, sfd, qff);
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = {.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueCount = 1, .pQueuePriorities = &prio};
    const char *dext[] = {"VK_ANDROID_native_buffer"};
    VkDeviceCreateInfo dci = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .queueCreateInfoCount = 1, .pQueueCreateInfos = &qci,
                              .enabledExtensionCount = anb ? 1 : 0, .ppEnabledExtensionNames = dext};
    VkDevice d = VK_NULL_HANDLE;
    vr = cd(pd[0], &dci, NULL, &d);
    CHECK(vr == VK_SUCCESS, "vkCreateDevice(+ANDROID_native_buffer) = %d", vr);
    if (vr == VK_SUCCESS) {
      CHECK(magic_ok(d), "VkDevice dispatch magic");
      PFN_vkGetDeviceQueue gq = (PFN_vkGetDeviceQueue)gdpa(d, "vkGetDeviceQueue");
      VkQueue q = VK_NULL_HANDLE;
      gq(d, 0, 0, &q);
      CHECK(magic_ok(q), "VkQueue dispatch magic");
      static const char *dfn[] = {"vkGetSwapchainGrallocUsageANDROID", "vkGetSwapchainGrallocUsage2ANDROID",
                                  "vkGetSwapchainGrallocUsage3ANDROID", "vkGetSwapchainGrallocUsage4ANDROID",
                                  "vkAcquireImageANDROID", "vkQueueSignalReleaseImageANDROID",
                                  "vkGetAndroidHardwareBufferPropertiesANDROID", "vkCreateCuModuleNVX"};
      for (size_t i = 0; i < sizeof dfn / sizeof *dfn; i++)
        printf("  info GetDeviceProcAddr(%s) = %s\n", dfn[i], gdpa(d, dfn[i]) ? "present" : "NULL");
      PFN_vkDestroyDevice dd = (PFN_vkDestroyDevice)gdpa(d, "vkDestroyDevice");
      dd(d, NULL);
    }
  }
  PFN_vkDestroyInstance di = (PFN_vkDestroyInstance)dev->GetInstanceProcAddr(inst, "vkDestroyInstance");
  di(inst, NULL);
  dev->common.close(&dev->common);
  printf("RESULT: %s (%d failed checks)\n", fails ? "FAIL" : "PASS", fails);
  return fails ? 1 : 0;
}
