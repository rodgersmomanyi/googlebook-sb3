/* gb_layout.c -- set the vmwgfx display layout, as open-vm-tools' resolutionKMS plugin does.
 *
 * vmwgfx's connector PREFERRED mode is its layout size, which defaults to the 1280x800 minimum
 * (VMWGFX_MIN_INITIAL_*) because nothing in the guest ever sends a layout. hwc3-drm takes the PREFERRED mode as the
 * display's default config, so the whole UI is stuck at 1280x800. DRM_VMW_UPDATE_LAYOUT makes the given size the
 * preferred mode. Root (CAP_SYS_ADMIN) may call it on the render node, so no DRM master is needed. Run it once at
 * early-init, before the composer probes the connector.
 * Static NDK build (runs at early-init): clang --target=x86_64-linux-android34 -static -O2 -Wall -o gb_layout gb_layout.c
 * Usage: gb_layout WIDTHxHEIGHT */
#include <drm/drm.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

struct drm_vmw_rect { int32_t x, y; uint32_t w, h; };
struct drm_vmw_update_layout_arg { uint32_t num_outputs, pad64; uint64_t rects; };
#define DRM_VMW_UPDATE_LAYOUT 20
#define DRM_IOCTL_VMW_UPDATE_LAYOUT \
  DRM_IOW(DRM_COMMAND_BASE + DRM_VMW_UPDATE_LAYOUT, struct drm_vmw_update_layout_arg)

int main(int argc, char **argv) {
  unsigned w, h;
  if (argc != 2 || sscanf(argv[1], "%ux%u", &w, &h) != 2 || !w || !h) {
    fprintf(stderr, "usage: gb_layout WIDTHxHEIGHT\n");
    return 2;
  }
  static const char *nodes[] = {"/dev/dri/renderD128", "/dev/dri/card0"};
  for (unsigned i = 0; i < sizeof nodes / sizeof nodes[0]; i++) {
    int fd = open(nodes[i], O_RDWR | O_CLOEXEC);
    if (fd < 0) continue;
    struct drm_vmw_rect r = {0, 0, w, h};
    struct drm_vmw_update_layout_arg a;
    memset(&a, 0, sizeof a);
    a.num_outputs = 1;
    a.rects = (uintptr_t)&r;
    int ret = ioctl(fd, DRM_IOCTL_VMW_UPDATE_LAYOUT, &a);
    int err = errno;
    close(fd);
    if (!ret) {
      printf("gb_layout: %s layout %ux%u\n", nodes[i], w, h);
      return 0;
    }
    fprintf(stderr, "gb_layout: %s: %s\n", nodes[i], strerror(err));
  }
  return 1;
}
