/*
 * test_loader: self test of libgb_nvk_loader.so (glibc host build, run against lavapipe).
 *
 *   test_loader symbols <gb_vk_core_names.txt>   every Vulkan 1.0-1.3 core command is exported by the stub
 *   test_loader full                             GB_NVK_ICD=<lavapipe>: two live instances, device, queue,
 *                                                command buffer, vkCmdFillBuffer + read-back through the
 *                                                exported trampolines; proc-addr routing; no layers
 *   test_loader noicd                            GB_NVK_ICD=<missing file>: clean VK_ERROR_INCOMPATIBLE_DRIVER
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <vulkan/vulkan_core.h>

static int g_checks, g_fail;
#define CHECK(c, ...)                                                                                                  \
    do {                                                                                                               \
        g_checks++;                                                                                                    \
        if (!(c)) {                                                                                                    \
            g_fail++;                                                                                                  \
            printf("FAIL %s:%d: %s: ", __FILE__, __LINE__, #c);                                                       \
            printf(__VA_ARGS__);                                                                                       \
            printf("\n");                                                                                              \
        }                                                                                                              \
    } while (0)

static int symbols(const char *names)
{
    void *h = dlopen("libgb_nvk_loader.so", RTLD_NOW | RTLD_NOLOAD);
    CHECK(h != NULL, "stub not loaded: %s", dlerror());
    if (!h)
        return 1;
    FILE *f = fopen(names, "r");
    CHECK(f != NULL, "%s", names);
    if (!f)
        return 1;
    char line[256];
    int n = 0;
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!line[0])
            continue;
        n++;
        CHECK(dlsym(h, line) != NULL, "%s not exported", line);
    }
    fclose(f);
    printf("symbols: %d core commands checked\n", n);
    return 0;
}

static VkInstance mk_instance(void)
{
    VkApplicationInfo ai = {VK_STRUCTURE_TYPE_APPLICATION_INFO, NULL, "test_loader", 1, NULL, 0, VK_API_VERSION_1_2};
    VkInstanceCreateInfo ci = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, &ai, 0, NULL, 0, NULL};
    VkInstance i = VK_NULL_HANDLE;
    VkResult r = vkCreateInstance(&ci, NULL, &i);
    CHECK(r == VK_SUCCESS, "vkCreateInstance %d", r);
    return i;
}

static int full(void)
{
    uint32_t v = 0;
    CHECK(vkEnumerateInstanceVersion(&v) == VK_SUCCESS && v >= VK_API_VERSION_1_1, "version 0x%x", v);
    CHECK(VK_API_VERSION_MINOR(v) <= 3 && VK_API_VERSION_MAJOR(v) == 1, "stub caps at 1.3: 0x%x", v);
    uint32_t nl = 99;
    CHECK(vkEnumerateInstanceLayerProperties(&nl, NULL) == VK_SUCCESS && nl == 0, "layers %u", nl);
    const char *layer = "VK_LAYER_KHRONOS_validation";
    VkInstanceCreateInfo lci = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, NULL, 1, &layer, 0, NULL};
    VkInstance li = VK_NULL_HANDLE;
    CHECK(vkCreateInstance(&lci, NULL, &li) == VK_ERROR_LAYER_NOT_PRESENT, "layer request must fail");
    uint32_t ne = 0;
    CHECK(vkEnumerateInstanceExtensionProperties(NULL, &ne, NULL) == VK_SUCCESS, "ext count");
    CHECK(vkEnumerateInstanceExtensionProperties("x", &ne, NULL) == VK_ERROR_LAYER_NOT_PRESENT, "layer exts");

    VkInstance a = mk_instance(), b = mk_instance();
    if (!a || !b)
        return 1;
    CHECK(a != b, "two instances");
    /* routing: stub functions for object creators, ICD pointers for the rest */
    CHECK(vkGetInstanceProcAddr(a, "vkEnumeratePhysicalDevices") == (PFN_vkVoidFunction)vkEnumeratePhysicalDevices,
          "EnumeratePhysicalDevices must be the stub's");
    CHECK(vkGetInstanceProcAddr(a, "vkCreateDevice") == (PFN_vkVoidFunction)vkCreateDevice, "CreateDevice");
    CHECK(vkGetInstanceProcAddr(NULL, "vkCreateInstance") == (PFN_vkVoidFunction)vkCreateInstance, "global");
    CHECK(vkGetInstanceProcAddr(NULL, "vkGetPhysicalDeviceProperties") == NULL, "NULL instance non-global");
    PFN_vkVoidFunction gpdp = vkGetInstanceProcAddr(a, "vkGetPhysicalDeviceProperties");
    CHECK(gpdp != NULL && gpdp != (PFN_vkVoidFunction)vkGetPhysicalDeviceProperties, "ICD pointer expected");

    VkPhysicalDevice pd[4], pdb[4];
    uint32_t n = 4, nb = 4;
    CHECK(vkEnumeratePhysicalDevices(a, &n, pd) == VK_SUCCESS && n >= 1, "devices %u", n);
    CHECK(vkEnumeratePhysicalDevices(b, &nb, pdb) == VK_SUCCESS && nb >= 1, "devices b %u", nb);
    if (n < 1 || nb < 1)
        return 1;
    VkPhysicalDeviceProperties pp, ppb;
    vkGetPhysicalDeviceProperties(pd[0], &pp);  /* exported trampoline */
    vkGetPhysicalDeviceProperties(pdb[0], &ppb);
    CHECK(!strcmp(pp.deviceName, ppb.deviceName), "same device through both instances");
    printf("device: %s (vendor 0x%04x, api %u.%u)\n", pp.deviceName, pp.vendorID, VK_API_VERSION_MAJOR(pp.apiVersion),
           VK_API_VERSION_MINOR(pp.apiVersion));
    uint32_t ng = 0;
    CHECK(vkEnumeratePhysicalDeviceGroups(a, &ng, NULL) == VK_SUCCESS && ng >= 1, "groups %u", ng);

    uint32_t nq = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(pd[0], &nq, NULL);
    VkQueueFamilyProperties qf[16];
    nq = nq > 16 ? 16 : nq;
    vkGetPhysicalDeviceQueueFamilyProperties(pd[0], &nq, qf);
    uint32_t fam = UINT32_MAX;
    for (uint32_t i = 0; i < nq; i++)
        if (qf[i].queueFlags & VK_QUEUE_COMPUTE_BIT) {
            fam = i;
            break;
        }
    CHECK(fam != UINT32_MAX, "compute queue family");
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, NULL, 0, fam, 1, &prio};
    VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, NULL, 0, 1, &qci, 0, NULL, 0, NULL, NULL};
    VkDevice dev = VK_NULL_HANDLE;
    CHECK(vkCreateDevice(pd[0], &dci, NULL, &dev) == VK_SUCCESS, "vkCreateDevice");
    if (!dev)
        return 1;
    CHECK(vkGetDeviceProcAddr(dev, "vkGetDeviceQueue") == (PFN_vkVoidFunction)vkGetDeviceQueue, "GetDeviceQueue");
    CHECK(vkGetDeviceProcAddr(dev, "vkCmdFillBuffer") != NULL, "CmdFillBuffer via GDPA");
    VkQueue q = VK_NULL_HANDLE;
    vkGetDeviceQueue(dev, fam, 0, &q);
    CHECK(q != VK_NULL_HANDLE, "queue");

    VkBufferCreateInfo bci = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, NULL, 0, 4096, VK_BUFFER_USAGE_TRANSFER_DST_BIT,
                              VK_SHARING_MODE_EXCLUSIVE, 0, NULL};
    VkBuffer buf;
    CHECK(vkCreateBuffer(dev, &bci, NULL, &buf) == VK_SUCCESS, "buffer");
    VkMemoryRequirements mr;
    vkGetBufferMemoryRequirements(dev, buf, &mr);
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(pd[0], &mp);
    uint32_t mt = UINT32_MAX;
    for (uint32_t i = 0; i < mp.memoryTypeCount; i++)
        if ((mr.memoryTypeBits & (1u << i)) &&
            (mp.memoryTypes[i].propertyFlags & (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) ==
                (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)) {
            mt = i;
            break;
        }
    CHECK(mt != UINT32_MAX, "host visible memory");
    VkMemoryAllocateInfo mai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, NULL, mr.size, mt};
    VkDeviceMemory mem;
    CHECK(vkAllocateMemory(dev, &mai, NULL, &mem) == VK_SUCCESS, "alloc");
    CHECK(vkBindBufferMemory(dev, buf, mem, 0) == VK_SUCCESS, "bind");
    uint32_t *ptr = NULL;
    CHECK(vkMapMemory(dev, mem, 0, VK_WHOLE_SIZE, 0, (void **)&ptr) == VK_SUCCESS && ptr, "map");
    memset(ptr, 0, 4096);
    VkCommandPoolCreateInfo pci = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, NULL, 0, fam};
    VkCommandPool pool;
    CHECK(vkCreateCommandPool(dev, &pci, NULL, &pool) == VK_SUCCESS, "pool");
    VkCommandBufferAllocateInfo cai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, NULL, pool,
                                       VK_COMMAND_BUFFER_LEVEL_PRIMARY, 1};
    VkCommandBuffer cb;
    CHECK(vkAllocateCommandBuffers(dev, &cai, &cb) == VK_SUCCESS, "cmdbuf");
    VkCommandBufferBeginInfo cbi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO, NULL,
                                    VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT, NULL};
    CHECK(vkBeginCommandBuffer(cb, &cbi) == VK_SUCCESS, "begin");
    vkCmdFillBuffer(cb, buf, 0, 4096, 0x5a5a5a5au);  /* exported trampoline on a VkCommandBuffer */
    CHECK(vkEndCommandBuffer(cb) == VK_SUCCESS, "end");
    VkFenceCreateInfo fci = {VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, NULL, 0};
    VkFence fence;
    CHECK(vkCreateFence(dev, &fci, NULL, &fence) == VK_SUCCESS, "fence");
    VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO, NULL, 0, NULL, NULL, 1, &cb, 0, NULL};
    CHECK(vkQueueSubmit(q, 1, &si, fence) == VK_SUCCESS, "submit");  /* trampoline on a VkQueue */
    CHECK(vkWaitForFences(dev, 1, &fence, VK_TRUE, 5000000000ull) == VK_SUCCESS, "wait");
    int good = 1;
    for (int i = 0; i < 1024; i++)
        if (ptr[i] != 0x5a5a5a5au)
            good = 0;
    CHECK(good, "fill read-back");
    vkUnmapMemory(dev, mem);
    vkDestroyFence(dev, fence, NULL);
    vkFreeCommandBuffers(dev, pool, 1, &cb);
    vkDestroyCommandPool(dev, pool, NULL);
    vkDestroyBuffer(dev, buf, NULL);
    vkFreeMemory(dev, mem, NULL);
    CHECK(vkDeviceWaitIdle(dev) == VK_SUCCESS, "idle");
    vkDestroyDevice(dev, NULL);
    vkDestroyInstance(b, NULL);
    vkDestroyInstance(a, NULL);
    return 0;
}

static int noicd(void)
{
    VkInstanceCreateInfo ci = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, NULL, 0, NULL, 0, NULL, 0, NULL};
    VkInstance i = VK_NULL_HANDLE;
    CHECK(vkCreateInstance(&ci, NULL, &i) == VK_ERROR_INCOMPATIBLE_DRIVER, "no ICD -> incompatible driver");
    CHECK(i == VK_NULL_HANDLE, "no handle");
    uint32_t n = 7;
    CHECK(vkEnumerateInstanceExtensionProperties(NULL, &n, NULL) == VK_SUCCESS && n == 0, "0 extensions");
    uint32_t v = 0;
    CHECK(vkEnumerateInstanceVersion(&v) == VK_SUCCESS && v == VK_API_VERSION_1_0, "1.0 without ICD");
    return 0;
}

int main(int argc, char **argv)
{
    if (argc >= 3 && !strcmp(argv[1], "symbols"))
        symbols(argv[2]);
    else if (argc >= 2 && !strcmp(argv[1], "full"))
        full();
    else if (argc >= 2 && !strcmp(argv[1], "noicd"))
        noicd();
    else {
        fprintf(stderr, "usage: test_loader symbols <names> | full | noicd\n");
        return 2;
    }
    printf("TEST_LOADER %s %s: %d checks, %d failures\n", argv[1], g_fail ? "FAIL" : "PASS", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
