#!/usr/bin/env python3
# P3: Android HAL support for pipelines with a single processed stream (simple + software ISP).
#  - configureStreams(): merge all Android output streams onto one NV12 libcamera stream whose
#    size is the bounding box of the requested sizes; an exact-size YUV/PRIV stream stays Direct
#    (zero copy), everything else is Mapped (libyuv crop+scale, or JPEG encode).
#  - Mapped streams may use an Internal stream as their source.
#  - PostProcessorYuv: aspect-preserving centre crop, real destination strides.
#  - PostProcessorJpeg: down-scaling when the JPEG size differs from the source.
#  - EncoderLibJpeg / Thumbnailer: honour the source stride (soft ISP aligns NV12 strides).
# Set LIBCAMERA_HAL_MULTI_STREAM=1 to get the upstream behaviour back.
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from patchlib import edit

root = sys.argv[1]

edit(root, "src/android/camera_device.cpp", [
("""	sortCamera3StreamConfigs(streamConfigs, jpegStream);
	for (const auto &streamConfig : streamConfigs) {
""",
"""	/*
	 * The simple pipeline handler with the software ISP produces a single
	 * processed stream. Merge all the Android streams onto one NV12 stream
	 * covering the bounding box of the requested sizes. A YUV/PRIV stream
	 * of exactly that size is produced directly, all the others are
	 * produced by post-processing (crop and scale, or JPEG encoding).
	 */
	const char *multiStream = utils::secure_getenv("LIBCAMERA_HAL_MULTI_STREAM");
	const bool singleStream = !multiStream || !strcmp(multiStream, "0");
	size_t numOutputs = 0;
	for (const auto &streamConfig : streamConfigs)
		numOutputs += streamConfig.streams.size();

	if (singleStream && !streamConfigs.empty()) {
		Size bbox;
		for (const auto &streamConfig : streamConfigs) {
			for (const auto &s : streamConfig.streams) {
				bbox.width = std::max(bbox.width, s.stream->width);
				bbox.height = std::max(bbox.height, s.stream->height);
			}
		}

		/*
		 * The software ISP crops (it does not scale): to keep the full
		 * field of view, process the whole frame of the smallest sensor
		 * mode that covers the bounding box and scale in the HAL.
		 */
		std::unique_ptr<CameraConfiguration> rawCfg =
			camera_->generateConfiguration({ StreamRole::Raw });
		if (rawCfg && !rawCfg->empty()) {
			Size mode;
			const StreamFormats &fmts = rawCfg->at(0).formats();
			for (const PixelFormat &pf : fmts.pixelformats()) {
				for (const Size &s : fmts.sizes(pf)) {
					if (s.width < bbox.width || s.height < bbox.height)
						continue;
					if (mode.isNull() ||
					    static_cast<uint64_t>(s.width) * s.height <
					    static_cast<uint64_t>(mode.width) * mode.height)
						mode = s;
				}
			}

			std::unique_ptr<CameraConfiguration> fovCfg =
				camera_->generateConfiguration({ StreamRole::Viewfinder });
			if (!mode.isNull() && fovCfg && !fovCfg->empty()) {
				StreamConfiguration &c = fovCfg->at(0);
				c.pixelFormat = formats::NV12;
				c.size = mode;
				if (fovCfg->validate() != CameraConfiguration::Invalid &&
				    c.pixelFormat == formats::NV12 &&
				    c.size.width >= bbox.width && c.size.height >= bbox.height) {
					LOG(HAL, Debug) << "Expanding " << bbox << " to "
							<< c.size << " (sensor mode " << mode << ")";
					bbox = c.size;
				}
			}
		}

		camera3_stream_t *lead = nullptr;
		CameraStream::Type leadType = CameraStream::Type::Internal;
		for (const auto &streamConfig : streamConfigs) {
			for (const auto &s : streamConfig.streams) {
				if (lead)
					break;
				if (s.stream->format != HAL_PIXEL_FORMAT_BLOB &&
				    s.stream->width == bbox.width &&
				    s.stream->height == bbox.height) {
					lead = s.stream;
					leadType = CameraStream::Type::Direct;
				}
			}
		}

		if (!lead) {
			/* Prefer a YUV/PRIV stream, fall back to the JPEG one. */
			for (const auto &streamConfig : streamConfigs) {
				for (const auto &s : streamConfig.streams) {
					if (!lead || (lead->format == HAL_PIXEL_FORMAT_BLOB &&
						      s.stream->format != HAL_PIXEL_FORMAT_BLOB))
						lead = s.stream;
				}
			}
		}

		Camera3StreamConfig merged;
		merged.config.size = bbox;
		merged.config.pixelFormat = formats::NV12;
		merged.streams.push_back({ lead, leadType });
		if (leadType == CameraStream::Type::Direct)
			lead->usage |= GRALLOC_USAGE_SW_READ_OFTEN;
		else
			lead->usage |= GRALLOC_USAGE_SW_WRITE_OFTEN;

		for (const auto &streamConfig : streamConfigs) {
			for (const auto &s : streamConfig.streams) {
				if (s.stream == lead)
					continue;
				s.stream->usage |= GRALLOC_USAGE_SW_WRITE_OFTEN;
				merged.streams.push_back({ s.stream, CameraStream::Type::Mapped });
			}
		}

		LOG(HAL, Info) << "Single processed stream: " << numOutputs
			       << " Android streams produced from one "
			       << bbox << "-NV12 stream ("
			       << (leadType == CameraStream::Type::Direct ? "direct" : "internal")
			       << " lead)";

		streamConfigs.clear();
		streamConfigs.push_back(std::move(merged));
	}

	sortCamera3StreamConfigs(streamConfigs, jpegStream);
	for (const auto &streamConfig : streamConfigs) {
"""),
("""			if (stream.type == CameraStream::Type::Direct)
				sourceStream = &streams_.back();
""",
"""			if (stream.type == CameraStream::Type::Direct ||
			    stream.type == CameraStream::Type::Internal)
				sourceStream = &streams_.back();
"""),
("""	switch (config->validate()) {
	case CameraConfiguration::Valid:
		break;
	case CameraConfiguration::Adjusted:
		LOG(HAL, Info) << "Camera configuration adjusted";

		for (const StreamConfiguration &cfg : *config)
			LOG(HAL, Info) << " - " << cfg.toString();

		return -EINVAL;
""",
"""	/*
	 * Pipeline handlers may adjust fields the HAL leaves unset (buffer
	 * count, stride, colour space). Only reject the configuration when a
	 * stream format or size was changed.
	 */
	std::vector<std::pair<PixelFormat, Size>> requested;
	for (const StreamConfiguration &cfg : *config)
		requested.emplace_back(cfg.pixelFormat, cfg.size);

	switch (config->validate()) {
	case CameraConfiguration::Valid:
		break;
	case CameraConfiguration::Adjusted: {
		bool formatsKept = config->size() == requested.size();
		for (size_t i = 0; formatsKept && i < requested.size(); i++)
			formatsKept = config->at(i).pixelFormat == requested[i].first &&
				      config->at(i).size == requested[i].second;

		LOG(HAL, Info) << "Camera configuration adjusted"
			       << (formatsKept ? " (formats and sizes kept)" : "");

		for (const StreamConfiguration &cfg : *config)
			LOG(HAL, Info) << " - " << cfg.toString();

		if (formatsKept)
			break;

		return -EINVAL;
	}
"""),
])

# ---------------------------------------------------------------- 30 fps YUV filter
# Upstream drops every YUV size slower than 30 fps (floor). Sensors that run at 29.97 fps, or
# whose fastest mode is below 30 fps, would then expose no YUV stream at all: round the rate and
# only apply the filter when at least one YUV size reaches 30 fps.
edit(root, "src/android/camera_capabilities.cpp", [
("""	availableStreamConfigurations.reserve(streamConfigurations_.size() * 4);
	minFrameDurations.reserve(streamConfigurations_.size() * 4);

	for (const auto &entry : streamConfigurations_) {
""",
"""	availableStreamConfigurations.reserve(streamConfigurations_.size() * 4);
	minFrameDurations.reserve(streamConfigurations_.size() * 4);

	unsigned int minYuvFps = 0;
	for (const auto &entry : streamConfigurations_) {
		if (entry.androidFormat != HAL_PIXEL_FORMAT_BLOB &&
		    std::lround(1e9 / entry.minFrameDurationNsec) >= 30)
			minYuvFps = 30;
	}
	if (!minYuvFps)
		LOG(HAL, Warning) << "No YUV size reaches 30 fps, not filtering by frame rate";

	for (const auto &entry : streamConfigurations_) {
"""),
("""		unsigned int fps =
			static_cast<unsigned int>(floor(1e9 / entry.minFrameDurationNsec));

		if (entry.androidFormat != HAL_PIXEL_FORMAT_BLOB && fps < 30)
			continue;
""",
"""		unsigned int fps =
			static_cast<unsigned int>(std::lround(1e9 / entry.minFrameDurationNsec));

		if (entry.androidFormat != HAL_PIXEL_FORMAT_BLOB && fps < minYuvFps)
			continue;
"""),
])

# ---------------------------------------------------------------- PostProcessorYuv
edit(root, "src/android/yuv/post_processor_yuv.cpp", [
("""	if (inCfg.size < outCfg.size) {
		LOG(YUV, Error) << "Up-scaling is not supported"
""",
"""	if (outCfg.size.width > inCfg.size.width ||
	    outCfg.size.height > inCfg.size.height) {
		LOG(YUV, Error) << "Up-scaling is not supported"
"""),
("""	int ret = libyuv::NV12Scale(sourceMapped.planes()[0].data(),
				    sourceStride_[0],
				    sourceMapped.planes()[1].data(),
				    sourceStride_[1],
				    sourceSize_.width, sourceSize_.height,
				    destination->plane(0).data(),
				    destinationStride_[0],
				    destination->plane(1).data(),
				    destinationStride_[1],
				    destinationSize_.width,
				    destinationSize_.height,
				    libyuv::FilterMode::kFilterBilinear);
""",
"""	/*
	 * Centre-crop the source to the destination aspect ratio, then scale.
	 * Use the real gralloc strides of the destination when known.
	 */
	unsigned int cropW = sourceSize_.width;
	unsigned int cropH = sourceSize_.height;
	if (static_cast<uint64_t>(sourceSize_.width) * destinationSize_.height >
	    static_cast<uint64_t>(destinationSize_.width) * sourceSize_.height)
		cropW = static_cast<uint64_t>(sourceSize_.height) *
			destinationSize_.width / destinationSize_.height;
	else
		cropH = static_cast<uint64_t>(sourceSize_.width) *
			destinationSize_.height / destinationSize_.width;
	cropW = std::max(2u, cropW & ~1u);
	cropH = std::max(2u, cropH & ~1u);
	const unsigned int x0 = ((sourceSize_.width - cropW) / 2) & ~1u;
	const unsigned int y0 = ((sourceSize_.height - cropH) / 2) & ~1u;

	const unsigned int dstStrideY = destination->stride(0) ? destination->stride(0)
							       : destinationStride_[0];
	const unsigned int dstStrideUV = destination->stride(1) ? destination->stride(1)
								: destinationStride_[1];

	int ret = libyuv::NV12Scale(sourceMapped.planes()[0].data() +
				    y0 * sourceStride_[0] + x0,
				    sourceStride_[0],
				    sourceMapped.planes()[1].data() +
				    (y0 / 2) * sourceStride_[1] + x0,
				    sourceStride_[1],
				    cropW, cropH,
				    destination->plane(0).data(),
				    dstStrideY,
				    destination->plane(1).data(),
				    dstStrideUV,
				    destinationSize_.width,
				    destinationSize_.height,
				    libyuv::FilterMode::kFilterBilinear);
"""),
("""	if (destination.plane(0).size() < destinationLength_[0] ||
	    destination.plane(1).size() < destinationLength_[1]) {
""",
"""	const unsigned int dstY = destination.stride(0)
		? destination.stride(0) * destinationSize_.height
		: destinationLength_[0];
	const unsigned int dstUV = destination.stride(1)
		? destination.stride(1) * destinationSize_.height / 2
		: destinationLength_[1];
	if (destination.plane(0).size() < dstY ||
	    destination.plane(1).size() < dstUV) {
"""),
])

edit(root, "src/android/yuv/post_processor_yuv.cpp", [
("""#include <libyuv/scale.h>
""",
"""#include <algorithm>

#include <libyuv/scale.h>
"""),
])

# ---------------------------------------------------------------- JPEG encoder stride
edit(root, "src/android/jpeg/encoder_libjpeg.cpp", [
("""	compress_.image_width = cfg.size.width;
	compress_.image_height = cfg.size.height;
""",
"""	compress_.image_width = cfg.size.width;
	compress_.image_height = cfg.size.height;
	stride_ = cfg.stride;
"""),
("""	unsigned int y_stride = pixelFormatInfo_->stride(compress_.image_width, 0);
	unsigned int c_stride = pixelFormatInfo_->stride(compress_.image_width, 1);

	unsigned int horzSubSample = 2 * compress_.image_width / c_stride;
""",
"""	unsigned int y_stride = pixelFormatInfo_->stride(compress_.image_width, 0);
	unsigned int c_stride = pixelFormatInfo_->stride(compress_.image_width, 1);

	unsigned int horzSubSample = 2 * compress_.image_width / c_stride;

	/* NV12/NV21 sources may have padded lines (soft ISP, gralloc). */
	if (stride_ > y_stride && c_stride == y_stride) {
		y_stride = stride_;
		c_stride = stride_;
	}
"""),
])
edit(root, "src/android/jpeg/encoder_libjpeg.h", [
("""	const libcamera::PixelFormatInfo *pixelFormatInfo_;
""",
"""	const libcamera::PixelFormatInfo *pixelFormatInfo_;
	unsigned int stride_ = 0;
"""),
])

# ---------------------------------------------------------------- Thumbnailer stride
edit(root, "src/android/jpeg/thumbnailer.cpp", [
("""void Thumbnailer::configure(const Size &sourceSize, PixelFormat pixelFormat)
{
	sourceSize_ = sourceSize;
	pixelFormat_ = pixelFormat;
""",
"""void Thumbnailer::configure(const Size &sourceSize, PixelFormat pixelFormat,
			    unsigned int stride)
{
	sourceSize_ = sourceSize;
	pixelFormat_ = pixelFormat;
	sourceStride_ = stride > sourceSize.width ? stride : sourceSize.width;
"""),
("""	const unsigned int sw = sourceSize_.width;
	const unsigned int sh = sourceSize_.height;
""",
"""	const unsigned int sw = sourceSize_.width;
	const unsigned int sh = sourceSize_.height;
	const unsigned int ss = sourceStride_;
"""),
("""		srcY = src + sw * sourceY;
		srcCb = srcC + (sourceY / 2) * sw + 0;
		srcCr = srcC + (sourceY / 2) * sw + 1;
""",
"""		srcY = src + ss * sourceY;
		srcCb = srcC + (sourceY / 2) * ss + 0;
		srcCr = srcC + (sourceY / 2) * ss + 1;
"""),
("""			dstY[tw + x] = srcY[sw + sourceX];
""",
"""			dstY[tw + x] = srcY[ss + sourceX];
"""),
("""			dstY[tw + x + 1] = srcY[sw + sourceX + 1];
""",
"""			dstY[tw + x + 1] = srcY[ss + sourceX + 1];
"""),
])
edit(root, "src/android/jpeg/thumbnailer.h", [
("""	void configure(const libcamera::Size &sourceSize,
		       libcamera::PixelFormat pixelFormat);
""",
"""	void configure(const libcamera::Size &sourceSize,
		       libcamera::PixelFormat pixelFormat,
		       unsigned int stride = 0);
"""),
("""	libcamera::Size sourceSize_;
""",
"""	libcamera::Size sourceSize_;
	unsigned int sourceStride_ = 0;
"""),
])

# ---------------------------------------------------------------- JPEG down-scaling
edit(root, "src/android/jpeg/post_processor_jpeg.cpp", [
("""#include <chrono>
""",
"""#include <algorithm>
#include <chrono>

#include <libyuv/scale.h>

#include "libcamera/internal/mapped_framebuffer.h"
"""),
("""	if (inCfg.size != outCfg.size) {
		LOG(JPEG, Error) << "Mismatch of input and output stream sizes";
		return -EINVAL;
	}

	if (outCfg.pixelFormat != formats::MJPEG) {
		LOG(JPEG, Error) << "Output stream pixel format is not JPEG";
		return -EINVAL;
	}

	streamSize_ = outCfg.size;

	thumbnailer_.configure(inCfg.size, inCfg.pixelFormat);
""",
"""	if (outCfg.pixelFormat != formats::MJPEG) {
		LOG(JPEG, Error) << "Output stream pixel format is not JPEG";
		return -EINVAL;
	}

	streamSize_ = outCfg.size;

	thumbnailer_.configure(inCfg.size, inCfg.pixelFormat, inCfg.stride);

#if !defined(OS_CHROMEOS)
	encoder_ = std::make_unique<EncoderLibJpeg>();
#endif

	scale_ = inCfg.size != outCfg.size;
	if (scale_) {
		/* Single processed stream: scale the NV12 source first. */
		if (inCfg.pixelFormat != formats::NV12 ||
		    outCfg.size.width > inCfg.size.width ||
		    outCfg.size.height > inCfg.size.height) {
			LOG(JPEG, Error) << "Cannot produce " << outCfg.size
					 << " JPEG from " << inCfg.toString();
			return -EINVAL;
		}

		sourceSize_ = inCfg.size;
		sourceStride_ = std::max(inCfg.stride, inCfg.size.width);
		scaled_.resize(outCfg.size.width * outCfg.size.height * 3 / 2);

		StreamConfiguration encCfg;
		encCfg.pixelFormat = formats::NV12;
		encCfg.size = outCfg.size;
		encCfg.stride = outCfg.size.width;
		return scaledEncoder_.configure(encCfg);
	}
"""),
("""	int jpeg_size = encoder_->encode(streamBuffer, exif.data(), quality);
""",
"""	int jpeg_size;
	if (scale_) {
		const MappedFrameBuffer src(&source, MappedFrameBuffer::MapFlag::Read);
		if (!src.isValid() || src.planes().size() < 2) {
			LOG(JPEG, Error) << "Failed to map the JPEG source";
			processComplete.emit(streamBuffer, PostProcessor::Status::Error);
			return;
		}

		/* Centre crop to the JPEG aspect ratio, then scale. */
		const Size &out = streamSize_;
		unsigned int cropW = sourceSize_.width;
		unsigned int cropH = sourceSize_.height;
		if (static_cast<uint64_t>(sourceSize_.width) * out.height >
		    static_cast<uint64_t>(out.width) * sourceSize_.height)
			cropW = static_cast<uint64_t>(sourceSize_.height) * out.width / out.height;
		else
			cropH = static_cast<uint64_t>(sourceSize_.width) * out.height / out.width;
		cropW = std::max(2u, cropW & ~1u);
		cropH = std::max(2u, cropH & ~1u);
		const unsigned int x0 = ((sourceSize_.width - cropW) / 2) & ~1u;
		const unsigned int y0 = ((sourceSize_.height - cropH) / 2) & ~1u;

		uint8_t *dstY = scaled_.data();
		uint8_t *dstUV = dstY + out.width * out.height;
		int ret2 = libyuv::NV12Scale(src.planes()[0].data() + y0 * sourceStride_ + x0,
					     sourceStride_,
					     src.planes()[1].data() + (y0 / 2) * sourceStride_ + x0,
					     sourceStride_, cropW, cropH,
					     dstY, out.width, dstUV, out.width,
					     out.width, out.height,
					     libyuv::FilterMode::kFilterBilinear);
		if (ret2) {
			LOG(JPEG, Error) << "Failed to scale the JPEG source: " << ret2;
			processComplete.emit(streamBuffer, PostProcessor::Status::Error);
			return;
		}

		std::vector<Span<uint8_t>> planes = {
			{ dstY, static_cast<size_t>(out.width * out.height) },
			{ dstUV, static_cast<size_t>(out.width * out.height / 2) },
		};
		jpeg_size = scaledEncoder_.encode(planes, destination->plane(0),
						  exif.data(), quality);
	} else {
		jpeg_size = encoder_->encode(streamBuffer, exif.data(), quality);
	}
"""),
])
edit(root, "src/android/jpeg/post_processor_jpeg.h", [
("""	EncoderLibJpeg thumbnailEncoder_;
	Thumbnailer thumbnailer_;
""",
"""	EncoderLibJpeg thumbnailEncoder_;
	Thumbnailer thumbnailer_;

	/* Down-scaling support (single processed stream pipelines) */
	bool scale_ = false;
	libcamera::Size sourceSize_;
	unsigned int sourceStride_ = 0;
	std::vector<uint8_t> scaled_;
	EncoderLibJpeg scaledEncoder_;
"""),
])
print("P3 applied")
