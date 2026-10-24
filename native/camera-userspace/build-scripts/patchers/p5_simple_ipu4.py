#!/usr/bin/env python3
# P5: simple pipeline / soft IPA bits for the Surface Book 3 IPU4P ISYS.
#  - Accept more media driver names for the IPU4P ISYS (the sp7/sb3 out-of-tree drivers report
#    "intel-ipu6" with model "ipu4p", which upstream already matches), plus a runtime list
#    LIBCAMERA_SIMPLE_EXTRA_DRIVERS="name1,name2" (software ISP enabled) for other ports and tests.
#  - Report controls::FrameDurationLimits from the sensor timings (the Android HAL refuses
#    cameras without it).
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from patchlib import edit

root = sys.argv[1]

edit(root, "src/libcamera/pipeline/simple/simple.cpp", [
("""	{ "intel-ipu6", {}, true },
	{ "intel-ipu7", {}, true },
""",
"""	{ "intel-ipu4", {}, true },
	{ "intel-ipu4p", {}, true },
	{ "intel_ipu4p_isys", {}, true },
	{ "intel-ipu4p-isys", {}, true },
	{ "intel-ipu6", {}, true },
	{ "intel-ipu7", {}, true },
"""),
("""bool SimplePipelineHandler::match(DeviceEnumerator *enumerator)
{
	std::shared_ptr<MediaDevice> media;

	for (const SimplePipelineInfo &inf : supportedDevices) {
""",
"""bool SimplePipelineHandler::match(DeviceEnumerator *enumerator)
{
	std::shared_ptr<MediaDevice> media;

	/* Extra drivers from the environment, matched with the software ISP. */
	static std::vector<std::string> extraNames;
	static std::vector<SimplePipelineInfo> extraDevices;
	if (extraDevices.empty()) {
		const char *env = utils::secure_getenv("LIBCAMERA_SIMPLE_EXTRA_DRIVERS");
		if (env) {
			for (const auto &name : utils::split(env, ",")) {
				if (!name.empty())
					extraNames.emplace_back(name);
			}
			extraNames.reserve(extraNames.size());
			for (const std::string &name : extraNames)
				extraDevices.push_back({ name.c_str(), {}, true });
		}
	}

	std::vector<const SimplePipelineInfo *> devices;
	for (const SimplePipelineInfo &inf : supportedDevices)
		devices.push_back(&inf);
	for (const SimplePipelineInfo &inf : extraDevices)
		devices.push_back(&inf);

	for (const SimplePipelineInfo *infp : devices) {
		const SimplePipelineInfo &inf = *infp;
"""),
])

edit(root, "src/libcamera/pipeline/simple/simple.cpp", [
("""	ret = data->setupFormats(&format, V4L2Subdevice::ActiveFormat,
				 config->combinedTransform());
	if (ret < 0)
		return ret;

	/* Configure the video node, taking into account any Bayer pattern change. */
""",
"""	ret = data->setupFormats(&format, V4L2Subdevice::ActiveFormat,
				 config->combinedTransform());
	if (ret < 0)
		return ret;

	/*
	 * Report the frame duration limits of the configured sensor mode
	 * (binned and full resolution modes differ). The Android HAL reads
	 * them after configure() to build its per-resolution frame rates.
	 */
	{
		IPACameraSensorInfo sensorInfo{};
		if (!data->sensor_->sensorInfo(&sensorInfo) && sensorInfo.pixelRate &&
		    sensorInfo.minLineLength && sensorInfo.minFrameLength) {
			const uint64_t line =
				static_cast<uint64_t>(sensorInfo.minLineLength) * 1000000;
			int64_t minUs = line * sensorInfo.minFrameLength / sensorInfo.pixelRate;
			int64_t maxUs = line * std::max(sensorInfo.maxFrameLength,
							sensorInfo.minFrameLength) /
					sensorInfo.pixelRate;

			ControlInfoMap::Map map;
			for (const auto &[id, info] : data->controlInfo_)
				map.emplace(id, info);
			map[&controls::FrameDurationLimits] = ControlInfo(minUs, maxUs, minUs);
			data->controlInfo_ = ControlInfoMap(std::move(map), controls::controls);
		}
	}

	/* Configure the video node, taking into account any Bayer pattern change. */
"""),
])

edit(root, "src/ipa/simple/soft_simple.cpp", [
("""	ControlInfoMap::Map ctrlMap = context_.ctrlMap;
	*ipaControls = ControlInfoMap(std::move(ctrlMap), controls::controls);
""",
"""	ControlInfoMap::Map ctrlMap = context_.ctrlMap;

	/*
	 * Report the frame duration limits of the sensor configuration the
	 * camera was created with (the Android HAL requires them).
	 */
	if (sensorInfo.pixelRate && sensorInfo.minLineLength &&
	    sensorInfo.minFrameLength &&
	    !ctrlMap.count(&controls::FrameDurationLimits)) {
		const uint64_t lineUs1e6 = static_cast<uint64_t>(sensorInfo.minLineLength) * 1000000;
		int64_t minUs = lineUs1e6 * sensorInfo.minFrameLength / sensorInfo.pixelRate;
		int64_t maxUs = lineUs1e6 * std::max(sensorInfo.maxFrameLength,
						     sensorInfo.minFrameLength) /
				sensorInfo.pixelRate;
		ctrlMap[&controls::FrameDurationLimits] =
			ControlInfo(minUs, maxUs, minUs);
		LOG(IPASoft, Debug) << "Frame duration limits " << minUs
				    << "us - " << maxUs << "us";
	} else if (!ctrlMap.count(&controls::FrameDurationLimits)) {
		/* No sensor timings: assume 30 fps nominal, 1 fps worst case. */
		ctrlMap[&controls::FrameDurationLimits] =
			ControlInfo(static_cast<int64_t>(33333),
				    static_cast<int64_t>(1000000),
				    static_cast<int64_t>(33333));
		LOG(IPASoft, Warning) << "No sensor timings, frame duration limits defaulted";
	}

	*ipaControls = ControlInfoMap(std::move(ctrlMap), controls::controls);
"""),
])
print("P5 applied")
