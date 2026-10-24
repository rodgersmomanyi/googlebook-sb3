/* SPDX-License-Identifier: LGPL-2.1-or-later */
/*
 * TEST ONLY: fake libhardware for host (glibc) testing of the libcamera Android HAL.
 *
 * hw_get_module("gralloc") returns a gralloc0 module whose alloc() creates
 * memfd-backed buffers carrying the minigbm private handle layout used by
 * lapis (/vendor/lib64/libminigbm_gralloc.so): fds[5] @0x0c, strides @0x20,
 * offsets @0x30, sizes @0x40, width @0x54, height @0x58, format @0x5c,
 * modifier @0x64, use_flags @0x6c, magic @0x74, pixel_stride @0x78,
 * droid_format @0x7c, usage @0x80, num_planes @0x88, reserved @0x8c,
 * total_size @0x94 (0x9c bytes in total).
 *
 * NV12 lines are aligned to FAKE_GRALLOC_ALIGN bytes (default 64, like
 * minigbm i915 linear buffers) and heights to 4 lines.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include <cutils/native_handle.h>
#include <hardware/gralloc.h>
#include <hardware/hardware.h>

#define ALIGN(x, a) (((x) + (a) - 1) & ~((a) - 1))
#define HANDLE_BYTES 0x9c
#define DATA_INTS ((HANDLE_BYTES - sizeof(native_handle_t)) / sizeof(int))

static unsigned int fake_align(void)
{
	const char *e = getenv("FAKE_GRALLOC_ALIGN");
	unsigned int a = e ? (unsigned int)atoi(e) : 64;
	return a ? a : 64;
}

static void put32(void *h, size_t off, uint32_t v) { memcpy((uint8_t *)h + off, &v, 4); }
static void put64(void *h, size_t off, uint64_t v) { memcpy((uint8_t *)h + off, &v, 8); }

static int fake_alloc(struct alloc_device_t *dev, int w, int h, int format,
		      int usage, buffer_handle_t *handle, int *stride)
{
	(void)dev;
	unsigned int planes, i;
	uint32_t strides[4] = { 0 }, offsets[4] = { 0 }, sizes[4] = { 0 }, drm;

	if (format == HAL_PIXEL_FORMAT_BLOB) {
		planes = 1;
		strides[0] = w;
		sizes[0] = (uint32_t)w * h;
		drm = 0x20203852; /* R8 */
	} else if (format == HAL_PIXEL_FORMAT_YCbCr_420_888 ||
		   format == HAL_PIXEL_FORMAT_IMPLEMENTATION_DEFINED) {
		unsigned int s = ALIGN((unsigned int)w, fake_align());
		unsigned int ah = ALIGN((unsigned int)h, 4u);
		planes = 2;
		strides[0] = strides[1] = s;
		sizes[0] = s * ah;
		offsets[1] = sizes[0];
		sizes[1] = s * ah / 2;
		drm = 0x3231564e; /* NV12 */
	} else {
		return -EINVAL;
	}

	uint64_t total = offsets[planes - 1] + sizes[planes - 1];
	int fd = memfd_create("fakegralloc", MFD_CLOEXEC);
	if (fd < 0 || ftruncate(fd, total) < 0)
		return -errno;

	native_handle_t *nh = calloc(1, HANDLE_BYTES);
	nh->version = sizeof(native_handle_t);
	nh->numFds = planes;
	nh->numInts = DATA_INTS - planes;
	for (i = 0; i < 5; i++)
		put32(nh, 0x0c + 4 * i, (uint32_t)-1);
	for (i = 0; i < planes; i++) {
		put32(nh, 0x0c + 4 * i, (uint32_t)(i ? dup(fd) : fd));
		put32(nh, 0x20 + 4 * i, strides[i]);
		put32(nh, 0x30 + 4 * i, offsets[i]);
		put32(nh, 0x40 + 4 * i, sizes[i]);
	}
	put32(nh, 0x50, 1);
	put32(nh, 0x54, w);
	put32(nh, 0x58, h);
	put32(nh, 0x5c, drm);
	put32(nh, 0x60, 0);
	put64(nh, 0x64, 0);
	put64(nh, 0x6c, 0);
	put32(nh, 0x74, 0xabcddcba);
	put32(nh, 0x78, strides[0]);
	put32(nh, 0x7c, format);
	put64(nh, 0x80, (uint64_t)(uint32_t)usage);
	put32(nh, 0x88, planes);
	put64(nh, 0x8c, 0);
	put64(nh, 0x94, total);

	*handle = nh;
	*stride = strides[0];
	return 0;
}

static int fake_free(struct alloc_device_t *dev, buffer_handle_t handle)
{
	(void)dev;
	native_handle_t *nh = (native_handle_t *)handle;
	for (int i = 0; i < nh->numFds; i++)
		close(nh->data[i]);
	free(nh);
	return 0;
}

static int fake_close(struct hw_device_t *dev)
{
	free(dev);
	return 0;
}

static int fake_open(const struct hw_module_t *module, const char *id,
		     struct hw_device_t **device)
{
	(void)id;
	struct alloc_device_t *dev = calloc(1, sizeof(*dev));
	dev->common.tag = HARDWARE_DEVICE_TAG;
	dev->common.version = 0;
	dev->common.module = (struct hw_module_t *)module;
	dev->common.close = fake_close;
	dev->alloc = fake_alloc;
	dev->free = fake_free;
	*device = &dev->common;
	return 0;
}

static struct hw_module_methods_t fake_methods = { .open = fake_open };

static struct gralloc_module_t fake_gralloc = {
	.common = {
		.tag = HARDWARE_MODULE_TAG,
		.module_api_version = GRALLOC_MODULE_API_VERSION_0_3,
		.hal_api_version = HARDWARE_HAL_API_VERSION,
		.id = GRALLOC_HARDWARE_MODULE_ID,
		.name = "fake minigbm-layout gralloc (host test)",
		.author = "sb3",
		.methods = &fake_methods,
	},
};

int hw_get_module(const char *id, const struct hw_module_t **module)
{
	if (strcmp(id, GRALLOC_HARDWARE_MODULE_ID)) {
		*module = NULL;
		return -ENOENT;
	}
	if (!fake_gralloc.common.dso)
		fake_gralloc.common.dso = dlopen(NULL, RTLD_NOW);
	*module = &fake_gralloc.common;
	return 0;
}

/* Helpers for the test harness */
int fake_gralloc_alloc(int w, int h, int format, int usage, buffer_handle_t *handle, int *stride)
{
	return fake_alloc(NULL, w, h, format, usage, handle, stride);
}

void fake_gralloc_free(buffer_handle_t handle)
{
	fake_free(NULL, handle);
}
