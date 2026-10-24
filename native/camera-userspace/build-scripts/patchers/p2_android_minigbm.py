#!/usr/bin/env python3
# P2: Android HAL buffer handling for minigbm (cros_gralloc) handles on the "generic" platform.
#  - CameraBuffer: per-plane stride/offset/size from the gralloc handle (layout verified against
#    lapis libminigbm_gralloc.so: magic @0x74, num_planes @0x88), one dmabuf shared by all planes.
#  - PlatformFrameBufferAllocator: same, no ASSERT crash when gralloc0 is missing.
#  - createFrameBuffer(): tolerate handles with fewer fds than planes.
#  - CameraStream::process(): destination CameraBuffer uses the Android stream format/size
#    (upstream used the *source* configuration, which breaks JPEG/scaled outputs).
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from patchlib import edit, write

root = sys.argv[1]

write(root, "src/android/mm/minigbm_handle.h", r'''/* SPDX-License-Identifier: LGPL-2.1-or-later */
/*
 * minigbm (cros_gralloc) buffer handle introspection.
 *
 * The camera HAL receives buffer_handle_t objects created by the minigbm
 * gralloc of the lapis (Android 17 desktop) vendor image. The layout of the
 * private handle was verified against /vendor/lib64/libminigbm_gralloc.so:
 * fds[5] @0x0c, strides[4] @0x20, offsets[4] @0x30, sizes[4] @0x40,
 * width @0x54, height @0x58, format @0x5c, format_modifier @0x64,
 * magic 0xabcddcba @0x74, num_planes @0x88, total_size @0x94 (0x9c bytes).
 * Handles that do not match are left to the generic (packed) code path.
 */

#pragma once

#include <stdint.h>
#include <string.h>

#include <cutils/native_handle.h>

struct MinigbmHandleInfo {
	unsigned int numPlanes;
	int fds[4];
	uint32_t strides[4];
	uint32_t offsets[4];
	uint32_t sizes[4];
	uint32_t width;
	uint32_t height;
	uint32_t drmFormat;
	uint64_t modifier;
	uint64_t totalSize;
};

static inline bool minigbmHandleInfo(const native_handle_t *h,
				     MinigbmHandleInfo *info)
{
	constexpr size_t kMinSize = 0x9c;
	constexpr uint32_t kMagic = 0xabcddcba;

	if (!h || h->version != static_cast<int>(sizeof(native_handle_t)))
		return false;
	if (h->numFds < 1 || h->numInts < 0)
		return false;

	size_t total = sizeof(native_handle_t) +
		       static_cast<size_t>(h->numFds + h->numInts) * sizeof(int);
	if (total < kMinSize)
		return false;

	const uint8_t *b = reinterpret_cast<const uint8_t *>(h);
	auto rd32 = [b](size_t off) {
		uint32_t v;
		memcpy(&v, b + off, sizeof(v));
		return v;
	};
	auto rd64 = [b](size_t off) {
		uint64_t v;
		memcpy(&v, b + off, sizeof(v));
		return v;
	};

	if (rd32(0x74) != kMagic)
		return false;

	unsigned int n = rd32(0x88);
	if (n < 1 || n > 4 || static_cast<int>(n) > h->numFds)
		return false;

	info->numPlanes = n;
	for (unsigned int i = 0; i < 4; i++) {
		info->fds[i] = static_cast<int>(rd32(0x0c + 4 * i));
		info->strides[i] = rd32(0x20 + 4 * i);
		info->offsets[i] = rd32(0x30 + 4 * i);
		info->sizes[i] = rd32(0x40 + 4 * i);
	}
	info->width = rd32(0x54);
	info->height = rd32(0x58);
	info->drmFormat = rd32(0x5c);
	info->modifier = rd64(0x64);
	info->totalSize = rd64(0x94);

	return true;
}
''')

edit(root, "src/android/mm/generic_camera_buffer.cpp", [
("""#include "libcamera/internal/formats.h"
""",
"""#include "libcamera/internal/formats.h"

#include <sys/stat.h>

#include "minigbm_handle.h"
"""),
("""	const auto &info = PixelFormatInfo::info(pixelFormat);
	if (!info.isValid()) {
		error_ = -EINVAL;
		LOG(HAL, Error) << "Invalid pixel format: " << pixelFormat;
		return;
	}

	/*
	 * As Android doesn't offer an API to query buffer layouts, assume for
""",
"""	/*
	 * minigbm handles describe their own layout (strides are aligned by
	 * the gralloc, planes may use distinct fds of the same dmabuf).
	 */
	MinigbmHandleInfo gbm;
	if (minigbmHandleInfo(camera3Buffer, &gbm)) {
		fd_ = gbm.fds[0];
		struct stat st0;
		if (fstat(fd_, &st0) < 0) {
			error_ = -errno;
			LOG(HAL, Error) << "Invalid gralloc fd";
			return;
		}
		for (unsigned int i = 1; i < gbm.numPlanes; i++) {
			struct stat st;
			if (gbm.fds[i] == fd_ || gbm.fds[i] < 0)
				continue;
			if (fstat(gbm.fds[i], &st) < 0 ||
			    st.st_ino != st0.st_ino || st.st_dev != st0.st_dev) {
				error_ = -EINVAL;
				LOG(HAL, Error) << "Discontiguous gralloc planes are not supported";
				return;
			}
		}

		if (gbm.modifier != 0)
			LOG(HAL, Warning) << "Non-linear gralloc buffer (modifier "
					  << utils::hex(gbm.modifier) << ")";

		bufferLength_ = lseek(fd_, 0, SEEK_END);
		if (bufferLength_ < 0) {
			error_ = -errno;
			LOG(HAL, Error) << "Failed to get buffer length";
			return;
		}

		planeInfo_.resize(gbm.numPlanes);
		for (unsigned int i = 0; i < gbm.numPlanes; i++) {
			planeInfo_[i].stride = gbm.strides[i];
			planeInfo_[i].offset = gbm.offsets[i];
			planeInfo_[i].size = gbm.sizes[i];
			if (static_cast<off_t>(gbm.offsets[i]) + gbm.sizes[i] > bufferLength_) {
				error_ = -EINVAL;
				LOG(HAL, Error) << "gralloc plane " << i << " is out of buffer";
				return;
			}
		}
		return;
	}

	const auto &info = PixelFormatInfo::info(pixelFormat);
	if (!info.isValid()) {
		error_ = -EINVAL;
		LOG(HAL, Error) << "Invalid pixel format: " << pixelFormat;
		return;
	}

	/*
	 * As Android doesn't offer an API to query buffer layouts, assume for
"""),
])

edit(root, "src/android/mm/generic_frame_buffer_allocator.cpp", [
("""#include "../camera_device.h"
#include "../frame_buffer_allocator.h"
#include "../hal_framebuffer.h"
""",
"""#include "../camera_device.h"
#include "../frame_buffer_allocator.h"
#include "../hal_framebuffer.h"
#include "minigbm_handle.h"
"""),
("""		hw_get_module(GRALLOC_HARDWARE_MODULE_ID, &hardwareModule_);
		ASSERT(hardwareModule_);
	}
""",
"""		if (hw_get_module(GRALLOC_HARDWARE_MODULE_ID, &hardwareModule_) ||
		    !hardwareModule_) {
			hardwareModule_ = nullptr;
			LOG(HAL, Error) << "gralloc module not available";
		}
	}
"""),
("""	if (allocDevice_)
		gralloc_close(allocDevice_);
	dlclose(hardwareModule_->dso);
}
""",
"""	if (allocDevice_)
		gralloc_close(allocDevice_);
	if (hardwareModule_)
		dlclose(hardwareModule_->dso);
}
"""),
("""	if (!allocDevice_) {
		int ret = gralloc_open(hardwareModule_, &allocDevice_);
""",
"""	if (!hardwareModule_)
		return nullptr;

	if (!allocDevice_) {
		int ret = gralloc_open(hardwareModule_, &allocDevice_);
"""),
("""	/* This code assumes the planes are mapped consecutively. */
	const libcamera::PixelFormat pixelFormat =
		cameraDevice_->capabilities()->toPixelFormat(halPixelFormat);
	const auto &info = PixelFormatInfo::info(pixelFormat);
	std::vector<FrameBuffer::Plane> planes(info.numPlanes());
""",
"""	const libcamera::PixelFormat pixelFormat =
		cameraDevice_->capabilities()->toPixelFormat(halPixelFormat);
	const auto &info = PixelFormatInfo::info(pixelFormat);
	std::vector<FrameBuffer::Plane> planes(info.numPlanes());

	MinigbmHandleInfo gbm;
	if (minigbmHandleInfo(handle, &gbm) && gbm.numPlanes == planes.size()) {
		for (auto [i, plane] : utils::enumerate(planes)) {
			plane.fd = SharedFD(handle->data[i < static_cast<size_t>(handle->numFds) ? i : 0]);
			plane.offset = gbm.offsets[i];
			plane.length = gbm.sizes[i];
		}

		return std::make_unique<HALFrameBuffer>(
			std::make_unique<GenericFrameBufferData>(
				allocDevice_, handle, planes),
			handle);
	}

	/* This code assumes the planes are mapped consecutively. */
"""),
])

edit(root, "src/android/camera_device.cpp", [
("""	std::vector<FrameBuffer::Plane> planes(buf.numPlanes());
	for (size_t i = 0; i < buf.numPlanes(); ++i) {
		SharedFD fd{ camera3buffer->data[i] };
""",
"""	std::vector<FrameBuffer::Plane> planes(buf.numPlanes());
	for (size_t i = 0; i < buf.numPlanes(); ++i) {
		/* Single-fd buffers carry all planes in data[0]. */
		int fdIndex = i < static_cast<size_t>(camera3buffer->numFds) ? i : 0;
		SharedFD fd{ camera3buffer->data[fdIndex] };
"""),
])

edit(root, "src/android/camera_stream.cpp", [
("""	const StreamConfiguration &output = configuration();
	streamBuffer->dstBuffer = std::make_unique<CameraBuffer>(
		*streamBuffer->camera3Buffer, output.pixelFormat, output.size,
		PROT_READ | PROT_WRITE);
""",
"""	/*
	 * The destination buffer has the format and size of the Android
	 * stream, not of the libcamera stream it is produced from.
	 */
	const PixelFormat dstFormat =
		cameraDevice_->capabilities()->toPixelFormat(camera3Stream_->format);
	const Size dstSize(camera3Stream_->width, camera3Stream_->height);
	streamBuffer->dstBuffer = std::make_unique<CameraBuffer>(
		*streamBuffer->camera3Buffer, dstFormat, dstSize,
		PROT_READ | PROT_WRITE);
"""),
])
print("P2 applied")
