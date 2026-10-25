/*
 * /vendor/bin/gb_gpu_compute - launcher of the NVIDIA compute service (INTERFACES 11.1, D7).
 *
 * linkerconfig gives executables the vendor linker section only under /odm/bin and /vendor/bin, so the real code
 * lives in the payload library /odm/dgpu/lib64/gpu-compute/libgb_gpu_compute.so and this file only checks the
 * preconditions and dlopen()s it. gb_power identifies the service by uid 9999 and /proc/<pid>/exe == this file.
 *
 * Exit codes: 0 normal, 1 error, 3 NO_DEVICE (not native / no payload / no nouveau render node), 4 configuration.
 *
 * Test build (-DGB_COMPUTE_TESTING): GB_COMPUTE_ROOT prefixes every absolute path. The device build has no
 * environment knobs at all.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifdef __ANDROID__
#include <android/log.h>
#endif

#define GB_LIB "/odm/dgpu/lib64/gpu-compute/libgb_gpu_compute.so"
#define EXIT_NO_DEVICE 3

static const char *g_root = "";

static void lg(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void lg(const char *fmt, ...)
{
    char b[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
#ifdef __ANDROID__
    __android_log_write(ANDROID_LOG_INFO, "gb_gpu_compute", b);
#endif
    fprintf(stderr, "gb_gpu_compute: %s\n", b);
}

static const char *rp(char *buf, size_t n, const char *path)
{
    int r = snprintf(buf, n, "%s%s", g_root, path);
    if (r < 0 || (size_t)r >= n)
        buf[0] = 0; /* too long: an empty path fails every later check */
    return buf;
}

static int read_word(const char *path, char *out, size_t n)
{
    FILE *f = fopen(path, "re");
    if (!f)
        return -1;
    if (!fgets(out, (int)n, f)) {
        fclose(f);
        return -1;
    }
    fclose(f);
    out[strcspn(out, " \t\r\n")] = 0;
    return 0;
}

/* INTERFACES 2.2: a /sys/class/drm/renderD<n> whose device/driver basename is "nouveau". */
static int nouveau_render_node(char *name, size_t n)
{
    char p[PATH_MAX], l[PATH_MAX];
    DIR *d = opendir(rp(p, sizeof p, "/sys/class/drm"));
    if (!d)
        return 0;
    struct dirent *e;
    int found = 0;
    while (!found && (e = readdir(d))) {
        if (strncmp(e->d_name, "renderD", 7) || !e->d_name[7] || strspn(e->d_name + 7, "0123456789") != strlen(e->d_name + 7))
            continue; /* renderD<digits> only */
        char q[PATH_MAX];
        int qn = snprintf(q, sizeof q, "%s/%s/device/driver", p, e->d_name);
        if (qn < 0 || (size_t)qn >= sizeof q)
            continue;
        ssize_t r = readlink(q, l, sizeof l - 1);
        if (r <= 0)
            continue;
        l[r] = 0;
        const char *b = strrchr(l, '/');
        b = b ? b + 1 : l;
        if (!strcmp(b, "nouveau")) {
            int nn = snprintf(name, n, "%s", e->d_name);
            found = nn > 0;
        }
    }
    closedir(d);
    return found;
}

int main(int argc, char **argv)
{
#ifdef GB_COMPUTE_TESTING
    const char *r = getenv("GB_COMPUTE_ROOT");
    if (r)
        g_root = r;
#endif
    char p[PATH_MAX], w[64] = "", node[64] = "";
    if (read_word(rp(p, sizeof p, "/odm/etc/gb_hw"), w, sizeof w) || strcmp(w, "native")) {
        lg("not a native Googlebook image (gb_hw=%s): NO_DEVICE", w[0] ? w : "?");
        return EXIT_NO_DEVICE;
    }
    if (!nouveau_render_node(node, sizeof node)) {
        lg("no render node bound to nouveau: NO_DEVICE");
        return EXIT_NO_DEVICE;
    }
    const char *lib = rp(p, sizeof p, GB_LIB);
    if (access(lib, R_OK)) {
        lg("%s: %s (dGPU payload not mounted?): NO_DEVICE", lib, strerror(errno));
        return EXIT_NO_DEVICE;
    }
    void *h = dlopen(lib, RTLD_NOW | RTLD_LOCAL);
    if (!h) {
        const char *e = dlerror();
        lg("dlopen %s: %s", lib, e ? e : "?");
        return 1;
    }
    int (*fn)(int, char **) = (int (*)(int, char **))dlsym(h, "gb_gpu_compute_main");
    if (!fn) {
        lg("%s: no gb_gpu_compute_main", lib);
        return 1;
    }
    lg("starting (%s)", node);
    return fn(argc, argv);
}
