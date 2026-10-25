/*
 * gc_probe <libgb_gpu_compute.so>: dlopen the service library and run its --probe mode (llama/ggml init, Vulkan
 * device enumeration through libgb_nvk_loader.so). Used for the drm-shim TU117 smoke test in the bionic chroot
 * (GC-09) and the lavapipe smoke test on the host. Exit 0 = GPU device found, 3 = none.
 */
#include <dlfcn.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    if (argc != 2) {
        fprintf(stderr, "usage: gc_probe <libgb_gpu_compute.so>\n");
        return 2;
    }
    void *h = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!h) {
        fprintf(stderr, "dlopen: %s\n", dlerror());
        return 1;
    }
    int (*fn)(int, char **) = (int (*)(int, char **))dlsym(h, "gb_gpu_compute_main");
    if (!fn) {
        fprintf(stderr, "no gb_gpu_compute_main\n");
        return 1;
    }
    char a0[] = "gb_gpu_compute", a1[] = "--probe";
    char *av[] = {a0, a1, NULL};
    return fn(2, av);
}
