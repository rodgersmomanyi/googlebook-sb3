/* SPDX-License-Identifier: LGPL-2.1-or-later */
/*
 * TEST ONLY: drive the libcamera Android camera3 HAL like the Android camera
 * provider does (init, static info, open, configure_streams,
 * process_capture_request) without an Android framework. Buffers come from
 * the fake minigbm-layout gralloc in fake_hw.c.
 *
 * usage: hal_test <hal.so> <outdir> [scenario...]
 *   scenarios: preview_jpeg  (PRIV small + JPEG max, merged single stream)
 *              two_yuv       (YUV 320x240 + PRIV 1280x720)
 *              jpeg_small    (PRIV max + JPEG small: JPEG down-scaling)
 *              preview_only  (PRIV 640x480 only, full-FOV expansion)
 */
#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <dlfcn.h>
#include <map>
#include <mutex>
#include <string>
#include <sys/mman.h>
#include <unistd.h>
#include <vector>

#include <hardware/camera3.h>
#include <hardware/gralloc.h>
#include <system/camera_metadata.h>

extern "C" int fake_gralloc_alloc(int w, int h, int format, int usage,
				  buffer_handle_t *handle, int *stride);
extern "C" void fake_gralloc_free(buffer_handle_t handle);

struct Size {
	int w, h;
};

static std::mutex lock;
static std::condition_variable cv;
static std::map<uint32_t, int> buffersDone; /* frame -> buffers returned */
static std::map<uint32_t, int> buffersErr;
static int notifyErrors;

static void cbResult(const camera3_callback_ops *, const camera3_capture_result_t *r)
{
	std::lock_guard<std::mutex> l(lock);
	for (uint32_t i = 0; i < r->num_output_buffers; i++) {
		buffersDone[r->frame_number]++;
		if (r->output_buffers[i].status != CAMERA3_BUFFER_STATUS_OK)
			buffersErr[r->frame_number]++;
	}
	cv.notify_all();
}

static void cbNotify(const camera3_callback_ops *, const camera3_notify_msg_t *m)
{
	if (m->type == CAMERA3_MSG_ERROR) {
		std::lock_guard<std::mutex> l(lock);
		notifyErrors++;
		fprintf(stderr, "notify ERROR frame %u code %d\n",
			m->message.error.frame_number, m->message.error.error_code);
	}
}

static camera3_callback_ops_t cbOps = { cbResult, cbNotify };

static void statusChange(const camera_module_callbacks *, int id, int status)
{
	fprintf(stderr, "camera %d status %d\n", id, status);
}
static void torchChange(const camera_module_callbacks *, const char *, int) {}
static camera_module_callbacks_t modCbs = { statusChange, torchChange };

struct Buf {
	buffer_handle_t h;
	int stride;
	camera3_stream_t *s;
};

static bool readHandle(buffer_handle_t h, uint32_t off, uint32_t *v)
{
	memcpy(v, reinterpret_cast<const uint8_t *>(h) + off, 4);
	return true;
}

static int dumpNV12(const Buf &b, const std::string &path, double *meanY, double *meanUV)
{
	uint32_t stride, off1, size0, size1;
	readHandle(b.h, 0x20, &stride);
	readHandle(b.h, 0x34, &off1);
	readHandle(b.h, 0x40, &size0);
	readHandle(b.h, 0x44, &size1);
	int fd = b.h->data[0];
	size_t len = off1 + size1;
	uint8_t *p = (uint8_t *)mmap(nullptr, len, PROT_READ, MAP_SHARED, fd, 0);
	if (p == MAP_FAILED)
		return -1;
	FILE *f = fopen(path.c_str(), "wb");
	double sy = 0, suv = 0;
	for (unsigned int y = 0; y < b.s->height; y++) {
		fwrite(p + y * stride, 1, b.s->width, f);
		for (unsigned int x = 0; x < b.s->width; x++)
			sy += p[y * stride + x];
	}
	for (unsigned int y = 0; y < b.s->height / 2; y++) {
		fwrite(p + off1 + y * stride, 1, b.s->width, f);
		for (unsigned int x = 0; x < b.s->width; x++)
			suv += std::abs((int)p[off1 + y * stride + x] - 128);
	}
	fclose(f);
	munmap(p, len);
	*meanY = sy / (b.s->width * b.s->height);
	*meanUV = suv / (b.s->width * b.s->height / 2);
	return 0;
}

static int dumpJpeg(const Buf &b, size_t maxJpeg, const std::string &path)
{
	int fd = b.h->data[0];
	uint8_t *p = (uint8_t *)mmap(nullptr, maxJpeg, PROT_READ, MAP_SHARED, fd, 0);
	if (p == MAP_FAILED)
		return -1;
	const camera3_jpeg_blob *blob =
		reinterpret_cast<const camera3_jpeg_blob *>(p + maxJpeg - sizeof(camera3_jpeg_blob));
	int ret = -1;
	if (blob->jpeg_blob_id == CAMERA3_JPEG_BLOB_ID && blob->jpeg_size > 4 &&
	    p[0] == 0xff && p[1] == 0xd8) {
		FILE *f = fopen(path.c_str(), "wb");
		fwrite(p, 1, blob->jpeg_size, f);
		fclose(f);
		ret = blob->jpeg_size;
	}
	munmap(p, maxJpeg);
	return ret;
}

int main(int argc, char **argv)
{
	if (argc < 3) {
		fprintf(stderr, "usage: %s hal.so outdir [scenario...]\n", argv[0]);
		return 2;
	}
	std::string out = argv[2];
	std::vector<std::string> scenarios;
	for (int i = 3; i < argc; i++)
		scenarios.push_back(argv[i]);
	if (scenarios.empty())
		scenarios = { "preview_jpeg", "two_yuv", "jpeg_small", "preview_only" };

	void *dl = dlopen(argv[1], RTLD_NOW);
	if (!dl) {
		fprintf(stderr, "dlopen: %s\n", dlerror());
		return 1;
	}
	camera_module_t *mod = (camera_module_t *)dlsym(dl, HAL_MODULE_INFO_SYM_AS_STR);
	if (!mod) {
		fprintf(stderr, "no HMI\n");
		return 1;
	}
	printf("module: %s api %x\n", mod->common.name, mod->common.module_api_version);
	if (mod->init)
		mod->init();
	mod->set_callbacks(&modCbs);

	int n = mod->get_number_of_cameras();
	printf("cameras: %d\n", n);
	if (n < 1)
		return 1;

	camera_info info;
	if (mod->get_camera_info(0, &info)) {
		fprintf(stderr, "get_camera_info failed\n");
		return 1;
	}
	printf("camera 0: facing %d orientation %d device_version %x\n",
	       info.facing, info.orientation, info.device_version);

	const camera_metadata_t *meta = info.static_camera_characteristics;
	camera_metadata_ro_entry_t e;
	std::vector<Size> yuvSizes, jpegSizes;
	if (!find_camera_metadata_ro_entry(meta, ANDROID_SCALER_AVAILABLE_STREAM_CONFIGURATIONS, &e)) {
		for (size_t i = 0; i + 3 < e.count; i += 4) {
			int32_t fmt = e.data.i32[i], w = e.data.i32[i + 1], h = e.data.i32[i + 2];
			if (e.data.i32[i + 3] != ANDROID_SCALER_AVAILABLE_STREAM_CONFIGURATIONS_OUTPUT)
				continue;
			printf("  stream config fmt 0x%x %dx%d\n", fmt, w, h);
			if (fmt == HAL_PIXEL_FORMAT_YCbCr_420_888)
				yuvSizes.push_back({ w, h });
			if (fmt == HAL_PIXEL_FORMAT_BLOB)
				jpegSizes.push_back({ w, h });
		}
	}
	size_t maxJpeg = 0;
	if (!find_camera_metadata_ro_entry(meta, ANDROID_JPEG_MAX_SIZE, &e))
		maxJpeg = e.data.i32[0];
	if (!find_camera_metadata_ro_entry(meta, ANDROID_INFO_SUPPORTED_HARDWARE_LEVEL, &e))
		printf("hardware level %d\n", e.data.u8[0]);
	printf("max jpeg size %zu\n", maxJpeg);
	if (yuvSizes.empty() || jpegSizes.empty())
		return 1;

	auto area = [](const Size &s) { return s.w * s.h; };
	Size maxYuv = *std::max_element(yuvSizes.begin(), yuvSizes.end(),
					[&](const Size &a, const Size &b) { return area(a) < area(b); });
	Size minJpeg = *std::min_element(jpegSizes.begin(), jpegSizes.end(),
					 [&](const Size &a, const Size &b) { return area(a) < area(b); });
	auto pick = [&](int w, int h) {
		for (const Size &s : yuvSizes)
			if (s.w == w && s.h == h)
				return s;
		return yuvSizes.back();
	};

	int failures = 0;
	for (const std::string &sc : scenarios) {
		hw_device_t *hwdev = nullptr;
		if (mod->common.methods->open(&mod->common, "0", &hwdev) || !hwdev) {
			fprintf(stderr, "[%s] open failed\n", sc.c_str());
			failures++;
			continue;
		}
		camera3_device_t *dev = (camera3_device_t *)hwdev;
		dev->ops->initialize(dev, &cbOps);
		const camera_metadata_t *settings =
			dev->ops->construct_default_request_settings(dev, CAMERA3_TEMPLATE_PREVIEW);

		std::vector<camera3_stream_t> streams;
		auto add = [&](int fmt, Size s) {
			camera3_stream_t st = {};
			st.stream_type = CAMERA3_STREAM_OUTPUT;
			st.width = s.w;
			st.height = s.h;
			st.format = fmt;
			st.usage = GRALLOC_USAGE_HW_TEXTURE;
			streams.push_back(st);
		};
		if (sc == "preview_jpeg") {
			add(HAL_PIXEL_FORMAT_IMPLEMENTATION_DEFINED, pick(640, 480));
			add(HAL_PIXEL_FORMAT_BLOB, jpegSizes.front());
		} else if (sc == "two_yuv") {
			add(HAL_PIXEL_FORMAT_YCbCr_420_888, pick(320, 240));
			add(HAL_PIXEL_FORMAT_IMPLEMENTATION_DEFINED, pick(1280, 720));
		} else if (sc == "jpeg_small") {
			add(HAL_PIXEL_FORMAT_IMPLEMENTATION_DEFINED, maxYuv);
			add(HAL_PIXEL_FORMAT_BLOB, minJpeg);
		} else {
			add(HAL_PIXEL_FORMAT_IMPLEMENTATION_DEFINED, pick(640, 480));
		}

		std::vector<camera3_stream_t *> sp;
		for (auto &s : streams)
			sp.push_back(&s);
		camera3_stream_configuration_t cfg = {};
		cfg.num_streams = sp.size();
		cfg.streams = sp.data();
		cfg.operation_mode = CAMERA3_STREAM_CONFIGURATION_NORMAL_MODE;
		int ret = dev->ops->configure_streams(dev, &cfg);
		printf("[%s] configure_streams: %d\n", sc.c_str(), ret);
		for (auto &s : streams)
			printf("[%s]   stream %dx%d fmt 0x%x usage 0x%x max_buffers %u\n", sc.c_str(),
			       s.width, s.height, s.format, s.usage, s.max_buffers);
		if (ret) {
			failures++;
			dev->common.close(&dev->common);
			continue;
		}

		/* Allocate buffers like the framework would (after configure). */
		std::vector<std::vector<Buf>> bufs(streams.size());
		for (size_t i = 0; i < streams.size(); i++) {
			camera3_stream_t &s = streams[i];
			for (unsigned int k = 0; k < std::max(4u, s.max_buffers); k++) {
				Buf b;
				int w = s.format == HAL_PIXEL_FORMAT_BLOB ? (int)maxJpeg : s.width;
				int h = s.format == HAL_PIXEL_FORMAT_BLOB ? 1 : s.height;
				int fmt = s.format == HAL_PIXEL_FORMAT_IMPLEMENTATION_DEFINED
						  ? HAL_PIXEL_FORMAT_YCbCr_420_888 : s.format;
				if (fake_gralloc_alloc(w, h, fmt, s.usage, &b.h, &b.stride)) {
					fprintf(stderr, "alloc failed\n");
					return 1;
				}
				b.s = &s;
				bufs[i].push_back(b);
			}
		}

		const unsigned int frames = 24;
		buffersDone.clear();
		buffersErr.clear();
		notifyErrors = 0;
		auto t0 = std::chrono::steady_clock::now();
		std::map<uint32_t, int> expected;
		for (uint32_t f = 0; f < frames; f++) {
			std::vector<camera3_stream_buffer_t> ob;
			for (size_t i = 0; i < streams.size(); i++) {
				bool isJpeg = streams[i].format == HAL_PIXEL_FORMAT_BLOB;
				if (isJpeg && f != 12 && f != 20)
					continue;
				Buf &b = bufs[i][f % bufs[i].size()];
				camera3_stream_buffer_t sb = {};
				sb.stream = &streams[i];
				sb.buffer = &b.h;
				sb.status = CAMERA3_BUFFER_STATUS_OK;
				sb.acquire_fence = -1;
				sb.release_fence = -1;
				ob.push_back(sb);
			}
			/* Keep at most 4 requests in flight (buffer reuse). */
			{
				std::unique_lock<std::mutex> l(lock);
				cv.wait_for(l, std::chrono::seconds(5), [&] {
					unsigned int inflight = 0;
					for (auto &[fn, cnt] : expected)
						if (buffersDone[fn] < cnt)
							inflight++;
					return inflight < 3;
				});
			}
			camera3_capture_request_t req = {};
			req.frame_number = f;
			req.settings = f == 0 ? settings : nullptr;
			req.num_output_buffers = ob.size();
			req.output_buffers = ob.data();
			expected[f] = ob.size();
			ret = dev->ops->process_capture_request(dev, &req);
			if (ret) {
				fprintf(stderr, "[%s] process_capture_request %u: %d\n", sc.c_str(), f, ret);
				failures++;
				break;
			}
		}
		{
			std::unique_lock<std::mutex> l(lock);
			cv.wait_for(l, std::chrono::seconds(10), [&] {
				for (auto &[fn, cnt] : expected)
					if (buffersDone[fn] < cnt)
						return false;
				return true;
			});
		}
		double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
		int missing = 0, errs = 0;
		for (auto &[fn, cnt] : expected) {
			missing += cnt - std::min(cnt, buffersDone[fn]);
			errs += buffersErr[fn];
		}
		printf("[%s] %u requests in %.2fs, missing buffers %d, error buffers %d, notify errors %d\n",
		       sc.c_str(), frames, secs, missing, errs, notifyErrors);
		if (missing || errs)
			failures++;

		/* Dump the last buffers of each stream. */
		for (size_t i = 0; i < streams.size(); i++) {
			camera3_stream_t &s = streams[i];
			char name[256];
			if (s.format == HAL_PIXEL_FORMAT_BLOB) {
				for (unsigned int f : { 12u, 20u }) {
					snprintf(name, sizeof(name), "%s/%s_jpeg_%ux%u_f%u.jpg", out.c_str(),
						 sc.c_str(), s.width, s.height, f);
					int sz = dumpJpeg(bufs[i][f % bufs[i].size()], maxJpeg, name);
					printf("[%s]   %s: %d bytes\n", sc.c_str(), name, sz);
					if (sz <= 0)
						failures++;
				}
			} else {
				const Buf &b = bufs[i][(frames - 1) % bufs[i].size()];
				snprintf(name, sizeof(name), "%s/%s_nv12_%ux%u_stride%d.yuv", out.c_str(),
					 sc.c_str(), s.width, s.height, b.stride);
				double my = 0, muv = 0;
				dumpNV12(b, name, &my, &muv);
				printf("[%s]   %s: mean Y %.1f, mean |UV-128| %.1f\n", sc.c_str(), name, my, muv);
				if (my < 1.0)
					failures++;
			}
		}

		dev->common.close(&dev->common);
		for (auto &v : bufs)
			for (auto &b : v)
				fake_gralloc_free(b.h);
	}

	printf("RESULT: %s (%d failures)\n", failures ? "FAIL" : "PASS", failures);
	return failures ? 1 : 0;
}
