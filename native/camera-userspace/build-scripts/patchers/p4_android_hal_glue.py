#!/usr/bin/env python3
# P4: Android HAL glue for the SB3 native build.
#  - Internal cameras do not need a camera_hal.yaml entry when the sensor firmware (ACPI SSDB via
#    ipu-bridge) reports the location: the entry, if present, still overrides.
#  - Probe the gralloc NV12 stride alignment once (minigbm i915: 64 bytes, 256 when built with
#    LINEAR_ALIGN_256) and export it to the software ISP through LIBCAMERA_SOFTISP_NV12_STRIDE_ALIGN
#    so that soft ISP output buffers match gralloc buffers exactly.
#  - Logging defaults to the Android log (bionic syslog) instead of stderr on Android.
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from patchlib import edit

root = sys.argv[1]

edit(root, "src/android/camera_hal_manager.cpp", [
("""#include <libcamera/camera.h>
#include <libcamera/property_ids.h>

#include "camera_device.h"
""",
"""#include <libcamera/camera.h>
#include <libcamera/property_ids.h>

#include <stdlib.h>

#include <hardware/gralloc.h>
#include <hardware/hardware.h>

#include "camera_device.h"
#include "mm/minigbm_handle.h"
"""),
("""int CameraHalManager::init()
{
	cameraManager_ = std::make_unique<CameraManager>();
""",
"""namespace {

/*
 * Find the line alignment the gralloc applies to linear NV12 camera buffers,
 * and tell the software ISP to use the same one.
 */
void probeGrallocStrideAlignment()
{
	if (getenv("LIBCAMERA_SOFTISP_NV12_STRIDE_ALIGN"))
		return;

	const hw_module_t *module = nullptr;
	if (hw_get_module(GRALLOC_HARDWARE_MODULE_ID, &module) || !module) {
		LOG(HAL, Warning) << "No gralloc module, keeping default NV12 alignment";
		return;
	}

	alloc_device_t *dev = nullptr;
	if (gralloc_open(module, &dev) || !dev) {
		LOG(HAL, Warning) << "gralloc_open() failed, keeping default NV12 alignment";
		return;
	}

	/* 1032 = 1024 + 8 gives a distinct stride for every alignment 16..512. */
	constexpr int width = 1032;
	buffer_handle_t handle = nullptr;
	int stride = 0;
	int ret = dev->alloc(dev, width, 64, HAL_PIXEL_FORMAT_YCbCr_420_888,
			     GRALLOC_USAGE_HW_CAMERA_WRITE | GRALLOC_USAGE_SW_READ_OFTEN,
			     &handle, &stride);
	if (ret || !handle) {
		LOG(HAL, Warning) << "gralloc NV12 probe allocation failed: " << ret;
		gralloc_close(dev);
		return;
	}

	unsigned int bytesStride = stride;
	MinigbmHandleInfo gbm;
	if (minigbmHandleInfo(handle, &gbm))
		bytesStride = gbm.strides[0];

	unsigned int align = 0;
	for (unsigned int a = 1; a <= 4096; a <<= 1) {
		if (((width + a - 1) & ~(a - 1)) == bytesStride) {
			align = a;
			break;
		}
	}

	dev->free(dev, handle);
	gralloc_close(dev);

	if (!align) {
		LOG(HAL, Warning) << "Unexpected gralloc NV12 stride " << bytesStride
				  << " for width " << width;
		return;
	}

	setenv("LIBCAMERA_SOFTISP_NV12_STRIDE_ALIGN", std::to_string(align).c_str(), 0);
	LOG(HAL, Info) << "gralloc NV12 line alignment: " << align << " bytes";
}

} /* namespace */

int CameraHalManager::init()
{
	probeGrallocStrideAlignment();

	cameraManager_ = std::make_unique<CameraManager>();
"""),
("""	if (!isCameraExternal && !halConfig_.exists()) {
		LOG(HAL, Error)
			<< "HAL configuration file is mandatory for internal cameras."
			<< " Camera " << cam->id() << " failed to load";
		return;
	}

	const CameraConfigData *cameraConfigData = halConfig_.cameraConfigData(cam->id());
""",
"""	const auto location = cam->properties().get(properties::Location);
	const bool firmwareLocation =
		location && (*location == properties::CameraLocationFront ||
			     *location == properties::CameraLocationBack);

	if (!isCameraExternal && !halConfig_.exists() && !firmwareLocation) {
		LOG(HAL, Error)
			<< "HAL configuration file is mandatory for internal cameras."
			<< " Camera " << cam->id() << " failed to load";
		return;
	}

	const CameraConfigData *cameraConfigData =
		halConfig_.exists() ? halConfig_.cameraConfigData(cam->id()) : nullptr;
"""),
("""	if (!isCameraExternal && !cameraConfigData) {
		LOG(HAL, Error)
			<< "HAL configuration entry for internal camera "
			<< cam->id() << " is missing";
		return;
	}
""",
"""	if (!isCameraExternal && !cameraConfigData) {
		if (!firmwareLocation) {
			LOG(HAL, Error)
				<< "HAL configuration entry for internal camera "
				<< cam->id() << " is missing";
			return;
		}

		LOG(HAL, Info) << "Camera " << cam->id()
			       << ": no configuration entry, using the firmware location";
	}
"""),
])

edit(root, "src/android/camera_hal_config.cpp", [
("""	std::string filePath = LIBCAMERA_SYSCONF_DIR "/camera_hal.yaml";
""",
"""	/* LIBCAMERA_HAL_CONFIG overrides the configuration file location. */
	const char *envPath = getenv("LIBCAMERA_HAL_CONFIG");
	std::string filePath = envPath ? envPath
				       : LIBCAMERA_SYSCONF_DIR "/camera_hal.yaml";
"""),
])

edit(root, "src/libcamera/base/log.cpp", [
("""	const char *file = utils::secure_getenv("LIBCAMERA_LOG_FILE");
	if (!file)
		return;
""",
"""	const char *file = utils::secure_getenv("LIBCAMERA_LOG_FILE");
	if (!file) {
#if defined(__ANDROID__)
		/* stderr is not collected for Android HAL services. */
		logSetTarget(LoggingTargetSyslog);
#endif
		return;
	}
"""),
])
print("P4 applied")
