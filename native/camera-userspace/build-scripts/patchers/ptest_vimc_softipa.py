#!/usr/bin/env python3
# TEST ONLY (host build, never shipped): let the soft IPA run on the vimc virtual sensor, which has
# no V4L2_CID_EXPOSURE / V4L2_CID_ANALOGUE_GAIN controls. Used to exercise the simple pipeline +
# software ISP NV12 path and the Android HAL patches on the builder without camera hardware.
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from patchlib import edit

root = sys.argv[1]

edit(root, "src/ipa/simple/soft_simple.cpp", [
("""	if (sensorControls.find(V4L2_CID_EXPOSURE) == sensorControls.end()) {
		LOG(IPASoft, Error) << "Don't have exposure control";
		return -EINVAL;
	}

	if (sensorControls.find(V4L2_CID_ANALOGUE_GAIN) == sensorControls.end()) {
		LOG(IPASoft, Error) << "Don't have gain control";
		return -EINVAL;
	}
""",
"""	if (sensorControls.find(V4L2_CID_EXPOSURE) == sensorControls.end() ||
	    sensorControls.find(V4L2_CID_ANALOGUE_GAIN) == sensorControls.end()) {
		LOG(IPASoft, Warning) << "TEST BUILD: sensor has no exposure/gain controls";
	}
"""),
("""	const ControlInfo &exposureInfo = sensorInfoMap_.find(V4L2_CID_EXPOSURE)->second;
	const ControlInfo &gainInfo = sensorInfoMap_.find(V4L2_CID_ANALOGUE_GAIN)->second;
""",
"""	static const ControlInfo dummyExposure(int32_t(1), int32_t(1000), int32_t(100));
	static const ControlInfo dummyGain(int32_t(16), int32_t(128), int32_t(16));
	auto expIt = sensorInfoMap_.find(V4L2_CID_EXPOSURE);
	auto gainIt = sensorInfoMap_.find(V4L2_CID_ANALOGUE_GAIN);
	const ControlInfo &exposureInfo = expIt != sensorInfoMap_.end() ? expIt->second : dummyExposure;
	const ControlInfo &gainInfo = gainIt != sensorInfoMap_.end() ? gainIt->second : dummyGain;
"""),
("""	frameContext.sensor.exposure =
		sensorControls.get(V4L2_CID_EXPOSURE).get<int32_t>();
	int32_t again = sensorControls.get(V4L2_CID_ANALOGUE_GAIN).get<int32_t>();
""",
"""	frameContext.sensor.exposure = sensorControls.contains(V4L2_CID_EXPOSURE)
		? sensorControls.get(V4L2_CID_EXPOSURE).get<int32_t>() : 100;
	int32_t again = sensorControls.contains(V4L2_CID_ANALOGUE_GAIN)
		? sensorControls.get(V4L2_CID_ANALOGUE_GAIN).get<int32_t>() : 16;
"""),
("""		LOG(IPASoft, Error) << "Control(s) missing";
		return;
""",
"""		LOG(IPASoft, Debug) << "Control(s) missing";
		return;
"""),
])
print("PTEST applied")

edit(root, "src/libcamera/software_isp/software_isp.cpp", [
("""	IPACameraSensorInfo sensorInfo{};
	int ret = sensor->sensorInfo(&sensorInfo);
	if (ret) {
		LOG(SoftwareIsp, Error) << "Camera sensor information not available";
		return;
	}
""",
"""	IPACameraSensorInfo sensorInfo{};
	int ret = sensor->sensorInfo(&sensorInfo);
	if (ret) {
		/* TEST BUILD: vimc has no pixel rate / blanking controls */
		LOG(SoftwareIsp, Warning) << "TEST BUILD: faking camera sensor information";
		sensorInfo.model = sensor->model();
		sensorInfo.bitsPerPixel = 8;
		sensorInfo.outputSize = sensor->resolution();
		sensorInfo.activeAreaSize = sensor->resolution();
		sensorInfo.pixelRate = 100000000;
		sensorInfo.minLineLength = sensor->resolution().width + 100;
		sensorInfo.maxLineLength = sensorInfo.minLineLength;
		sensorInfo.minFrameLength = sensor->resolution().height + 40;
		sensorInfo.maxFrameLength = sensorInfo.minFrameLength * 10;
		ret = 0;
	}
"""),
])
print("PTEST2 applied")

edit(root, "src/libcamera/v4l2_videodevice.cpp", [
("""		std::vector<SizeRange> sizes = enumSizes(pixelFormat);
		if (sizes.empty())
			return {};
""",
"""		std::vector<SizeRange> sizes = enumSizes(pixelFormat);
		/* TEST BUILD: vimc capture nodes do not enumerate frame sizes */
		if (sizes.empty())
			sizes.emplace_back(Size(16, 16), Size(4096, 2160));
"""),
])
print("PTEST3 applied")

edit(root, "src/libcamera/v4l2_subdevice.cpp", [
("""	int ret = ioctl(VIDIOC_SUBDEV_S_FMT, &subdevFmt);
	if (ret) {
		LOG(V4L2, Error)
			<< "Unable to set format on pad " << stream << ": "
""",
"""	/* TEST BUILD: vimc link validation wants matching colorspaces */
	if (!format->colorSpace && entity_->device()->driver() == "vimc")
		subdevFmt.format.colorspace = V4L2_COLORSPACE_SRGB;

	int ret = ioctl(VIDIOC_SUBDEV_S_FMT, &subdevFmt);
	if (ret) {
		LOG(V4L2, Error)
			<< "Unable to set format on pad " << stream << ": "
"""),
])
print("PTEST4 applied")
