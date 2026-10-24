#!/usr/bin/env python3
# P1: software ISP (CPU debayer) NV12 output, BT.601 full range (sYCC), stride alignment configurable
# through LIBCAMERA_SOFTISP_NV12_STRIDE_ALIGN (default 64 = minigbm i915 linear alignment).
import sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from patchlib import edit

root = sys.argv[1]

edit(root, "src/libcamera/software_isp/debayer_cpu.cpp", [
# thread class: NV12 scratch lines, process signature
("""	void configure(unsigned int yStart, unsigned int yEnd);
	void process(uint32_t frame, const uint8_t *src, uint8_t *dst);
""",
"""	void configure(unsigned int yStart, unsigned int yEnd);
	void process(uint32_t frame, const uint8_t *src, uint8_t *dst, uint8_t *dstUV);
"""),
("""	void process2(uint32_t frame, const uint8_t *src, uint8_t *dst);
	void process4(uint32_t frame, const uint8_t *src, uint8_t *dst);
""",
"""	void process2(uint32_t frame, const uint8_t *src, uint8_t *dst);
	void process2NV12(uint32_t frame, const uint8_t *src, uint8_t *dstY, uint8_t *dstUV);
	void process4(uint32_t frame, const uint8_t *src, uint8_t *dst);
"""),
("""	std::vector<uint8_t> lineBuffers_[kMaxLineBuffers];
	bool enableInputMemcpy_;
};
""",
"""	std::vector<uint8_t> lineBuffers_[kMaxLineBuffers];
	/* RGB888 scratch lines used when producing NV12 output */
	std::vector<uint8_t> rgbLines_[2];
	bool enableInputMemcpy_;
};

namespace {

/*
 * NV12 output support (SB3 Android HAL): the Android camera HAL requires
 * YCbCr_420_888 streams. The debayer functions produce one RGB888 line pair
 * at a time into scratch buffers, which is then converted to two Y lines and
 * one interleaved CbCr line (BT.601 full range, i.e. sYCC/JFIF).
 */
unsigned int nv12StrideAlign()
{
	const char *env = utils::secure_getenv("LIBCAMERA_SOFTISP_NV12_STRIDE_ALIGN");
	if (!env)
		return 64;

	unsigned long v = strtoul(env, nullptr, 10);
	if (v < 1 || v > 4096 || (v & (v - 1)))
		return 64;

	return v;
}

inline uint8_t clampU8(int v)
{
	return v < 0 ? 0 : (v > 255 ? 255 : v);
}

void rgbPairToNV12(const uint8_t *l0, const uint8_t *l1, uint8_t *y0,
		   uint8_t *y1, uint8_t *uv, unsigned int width)
{
	/* RGB888 lines are stored as B, G, R bytes. */
	for (unsigned int x = 0; x < width; x += 2) {
		const uint8_t *a = l0 + 3 * x;
		const uint8_t *b = l1 + 3 * x;
		int b00 = a[0], g00 = a[1], r00 = a[2];
		int b01 = a[3], g01 = a[4], r01 = a[5];
		int b10 = b[0], g10 = b[1], r10 = b[2];
		int b11 = b[3], g11 = b[4], r11 = b[5];

		y0[x] = (77 * r00 + 150 * g00 + 29 * b00 + 128) >> 8;
		y0[x + 1] = (77 * r01 + 150 * g01 + 29 * b01 + 128) >> 8;
		y1[x] = (77 * r10 + 150 * g10 + 29 * b10 + 128) >> 8;
		y1[x + 1] = (77 * r11 + 150 * g11 + 29 * b11 + 128) >> 8;

		int rs = r00 + r01 + r10 + r11;
		int gs = g00 + g01 + g10 + g11;
		int bs = b00 + b01 + b10 + b11;
		uv[x] = clampU8(((-43 * rs - 85 * gs + 128 * bs + 512) >> 10) + 128);
		uv[x + 1] = clampU8(((128 * rs - 107 * gs - 21 * bs + 512) >> 10) + 128);
	}
}

} /* namespace */
"""),
# output config
("""	if (outputFormat == formats::XRGB8888 || outputFormat == formats::ARGB8888 ||
	    outputFormat == formats::XBGR8888 || outputFormat == formats::ABGR8888) {
		config.bpp = 32;
		return 0;
	}

	LOG(Debayer, Info)
		<< "Unsupported output format " << outputFormat.toString();
""",
"""	if (outputFormat == formats::XRGB8888 || outputFormat == formats::ARGB8888 ||
	    outputFormat == formats::XBGR8888 || outputFormat == formats::ABGR8888) {
		config.bpp = 32;
		return 0;
	}

	if (outputFormat == formats::NV12) {
		config.bpp = 8; /* luma plane */
		return 0;
	}

	LOG(Debayer, Info)
		<< "Unsupported output format " << outputFormat.toString();
"""),
# setDebayerFunctions: NV12 uses the RGB888 line functions
("""	xShift_ = 0;
	swapRedBlueGains_ = false;
""",
"""	xShift_ = 0;
	swapRedBlueGains_ = false;
	outputNV12_ = false;

	if (outputFormat == formats::NV12) {
		outputNV12_ = true;
		outputFormat = formats::RGB888;
	}
"""),
# configure: NV12 needs pattern height 2
("""	int ret = setDebayerFunctions(inputCfg.pixelFormat,
				      outputCfg.pixelFormat,
				      ccmEnabled);
	if (ret != 0)
		return -EINVAL;
""",
"""	int ret = setDebayerFunctions(inputCfg.pixelFormat,
				      outputCfg.pixelFormat,
				      ccmEnabled);
	if (ret != 0)
		return -EINVAL;

	if (outputNV12_ && inputConfig_.patternSize.height != 2) {
		LOG(Debayer, Error) << "NV12 output needs a Bayer pattern height of 2";
		return -EINVAL;
	}
"""),
# thread configure: allocate scratch lines
("""	if (enableInputMemcpy_) {
		for (unsigned int i = 0; i <= inputConfig.patternSize.height; i++)
			lineBuffers_[i].resize(lineBufferLength_);
	}
}
""",
"""	if (enableInputMemcpy_) {
		for (unsigned int i = 0; i <= inputConfig.patternSize.height; i++)
			lineBuffers_[i].resize(lineBufferLength_);
	}

	if (debayer_->outputNV12_) {
		/* +8: debayer functions may write a few bytes past the window */
		for (auto &line : rgbLines_)
			line.resize(debayer_->window_.width * 3 + 8);
	}
}
"""),
# stride and frame size
("""	/* round up to multiple of 8 for 64 bits alignment */
	unsigned int stride = (size.width * config.bpp / 8 + 7) & ~7;

	return std::make_tuple(stride, stride * size.height);
""",
"""	if (outputFormat == formats::NV12) {
		unsigned int align = nv12StrideAlign();
		unsigned int stride = (size.width + align - 1) & ~(align - 1);
		return std::make_tuple(stride, stride * size.height * 3 / 2);
	}

	/* round up to multiple of 8 for 64 bits alignment */
	unsigned int stride = (size.width * config.bpp / 8 + 7) & ~7;

	return std::make_tuple(stride, stride * size.height);
"""),
# thread process
("""void DebayerCpuThread::process(uint32_t frame, const uint8_t *src, uint8_t *dst)
{
	Rectangle &window = debayer_->window_;

	/* Adjust src to top left corner of the window */
	src += (window.y + yStart_) * debayer_->inputConfig_.stride +
	       window.x * debayer_->inputConfig_.bpp / 8;
	/* Adjust dst for yStart_ */
	dst += yStart_ * debayer_->outputConfig_.stride;

	if (debayer_->inputConfig_.patternSize.height == 2)
		process2(frame, src, dst);
	else
		process4(frame, src, dst);
""",
"""void DebayerCpuThread::process(uint32_t frame, const uint8_t *src, uint8_t *dst, uint8_t *dstUV)
{
	Rectangle &window = debayer_->window_;

	/* Adjust src to top left corner of the window */
	src += (window.y + yStart_) * debayer_->inputConfig_.stride +
	       window.x * debayer_->inputConfig_.bpp / 8;
	/* Adjust dst for yStart_ */
	dst += yStart_ * debayer_->outputConfig_.stride;

	if (debayer_->outputNV12_) {
		/* yStart_ is a multiple of the pattern height (2) */
		dstUV += (yStart_ / 2) * debayer_->outputConfig_.stride;
		process2NV12(frame, src, dst, dstUV);
	} else if (debayer_->inputConfig_.patternSize.height == 2)
		process2(frame, src, dst);
	else
		process4(frame, src, dst);
"""),
# process2NV12 implementation, inserted before process4
("""void DebayerCpuThread::process4(uint32_t frame, const uint8_t *src, uint8_t *dst)
{""",
"""void DebayerCpuThread::process2NV12(uint32_t frame, const uint8_t *src,
				    uint8_t *dstY, uint8_t *dstUV)
{
	unsigned int outputStride = debayer_->outputConfig_.stride;
	unsigned int inputStride = debayer_->inputConfig_.stride;
	Rectangle &window = debayer_->window_;
	unsigned int width = window.width;
	unsigned int yEnd = yEnd_;
	uint8_t *rgb0 = rgbLines_[0].data();
	uint8_t *rgb1 = rgbLines_[1].data();
	/* Holds [0] previous- [1] current- [2] next-line */
	const uint8_t *linePointers[3];

	if (window.y + yStart_) {
		linePointers[1] = src - inputStride; /* previous-line */
		linePointers[2] = src;
	} else {
		/* Top line, use the next line as prev line */
		linePointers[1] = src + inputStride;
		linePointers[2] = src;
	}

	if (window.y == 0 && yEnd_ == window.height)
		yEnd -= 2;

	setupInputMemcpy(linePointers);

	for (unsigned int y = yStart_; y < yEnd; y += 2) {
		shiftLinePointers(linePointers, src);
		memcpyNextLine(linePointers);
		debayer_->stats_->processLine0(frame, y, linePointers, threadIndex_);
		debayer_->debayer0(rgb0, linePointers);
		src += inputStride;

		shiftLinePointers(linePointers, src);
		memcpyNextLine(linePointers);
		debayer_->debayer1(rgb1, linePointers);
		src += inputStride;

		rgbPairToNV12(rgb0, rgb1, dstY, dstY + outputStride, dstUV, width);
		dstY += 2 * outputStride;
		dstUV += outputStride;
	}

	if (window.y == 0 && yEnd_ == window.height) {
		shiftLinePointers(linePointers, src);
		memcpyNextLine(linePointers);
		debayer_->stats_->processLine0(frame, yEnd, linePointers, threadIndex_);
		debayer_->debayer0(rgb0, linePointers);
		src += inputStride;

		shiftLinePointers(linePointers, src);
		/* next line may point outside of src, use prev. */
		linePointers[2] = linePointers[0];
		debayer_->debayer1(rgb1, linePointers);

		rgbPairToNV12(rgb0, rgb1, dstY, dstY + outputStride, dstUV, width);
	}
}

void DebayerCpuThread::process4(uint32_t frame, const uint8_t *src, uint8_t *dst)
{"""),
# DebayerCpu::process: second plane
("""	for (auto &thread : threads_)
		thread->invokeMethod(&DebayerCpuThread::process,
				     ConnectionTypeQueued, frame,
				     in.planes()[0].data(), out.planes()[0].data());
""",
"""	uint8_t *dstUV = nullptr;
	if (outputNV12_) {
		if (out.planes().size() >= 2) {
			dstUV = out.planes()[1].data();
		} else if (out.planes()[0].size() >=
			   outputConfig_.stride * window_.height * 3 / 2) {
			/* Single plane buffer (e.g. from exportFrameBuffers()) */
			dstUV = out.planes()[0].data() + outputConfig_.stride * window_.height;
		} else {
			LOG(Debayer, Error) << "NV12 output buffer is too small";
			metadata.status = FrameMetadata::FrameError;
			return;
		}
	}

	for (auto &thread : threads_)
		thread->invokeMethod(&DebayerCpuThread::process,
				     ConnectionTypeQueued, frame,
				     in.planes()[0].data(), out.planes()[0].data(),
				     dstUV);
"""),
("""	metadata.planes()[0].bytesused = out.planes()[0].size();
""",
"""	metadata.planes()[0].bytesused = out.planes()[0].size();
	if (outputNV12_ && metadata.planes().size() > 1)
		metadata.planes()[1].bytesused = out.planes()[1].size();
"""),
# formats(): advertise NV12 after the RGB formats (RGB stays the default)
("""	std::vector<PixelFormat> outputFormats = { formats::RGB888,
						   formats::XRGB8888,
						   formats::ARGB8888,
						   formats::BGR888,
						   formats::XBGR8888,
						   formats::ABGR8888 };
""",
"""	std::vector<PixelFormat> outputFormats = { formats::RGB888,
						   formats::XRGB8888,
						   formats::ARGB8888,
						   formats::BGR888,
						   formats::XBGR8888,
						   formats::ABGR8888,
						   formats::NV12 };
"""),
])

edit(root, "src/libcamera/software_isp/debayer_cpu.h", [
("""	unsigned int xShift_; /* Offset of 0/1 applied to window_.x */
""",
"""	unsigned int xShift_; /* Offset of 0/1 applied to window_.x */
	bool outputNV12_ = false; /* RGB888 line functions + NV12 conversion */
"""),
])
print("P1 applied")
