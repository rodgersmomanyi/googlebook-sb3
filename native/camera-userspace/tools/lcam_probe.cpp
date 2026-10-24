/* SPDX-License-Identifier: LGPL-2.1-or-later */
/*
 * lcam-probe: minimal libcamera client for bring-up on the native SB3 boot
 * (adb shell, no Android camera framework needed).
 *
 *   lcam-probe -l                         list cameras and their properties
 *   lcam-probe -c 0 -s 1280x720 -n 60 -o /data/local/tmp/f.nv12
 *                                         capture NV12 frames, save the last one,
 *                                         report fps and the mean luma
 *
 * Stop the HAL first (it owns the cameras):  stop vendor.camera-provider-2-7-legacy-wrapper
 */
#include <chrono>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <sys/mman.h>
#include <unistd.h>
#include <vector>

#include <libcamera/camera.h>
#include <libcamera/camera_manager.h>
#include <libcamera/control_ids.h>
#include <libcamera/formats.h>
#include <libcamera/framebuffer_allocator.h>
#include <libcamera/property_ids.h>
#include <libcamera/request.h>
#include <libcamera/stream.h>

using namespace libcamera;

static std::mutex mtx;
static std::condition_variable cv;
static unsigned int completed;
static unsigned int failed;
static Request *lastDone;

static void requestComplete(Request *request)
{
	std::lock_guard<std::mutex> l(mtx);
	if (request->status() == Request::RequestComplete)
		completed++;
	else if (request->status() == Request::RequestCancelled)
		failed++;
	lastDone = request;
	cv.notify_all();
}

static const char *locationName(int32_t l)
{
	switch (l) {
	case properties::CameraLocationFront: return "front";
	case properties::CameraLocationBack: return "back";
	case properties::CameraLocationExternal: return "external";
	default: return "?";
	}
}

int main(int argc, char **argv)
{
	bool list = false;
	unsigned int index = 0, frames = 30, width = 1280, height = 720;
	std::string outFile;
	PixelFormat format = formats::NV12;

	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "-l"))
			list = true;
		else if (!strcmp(argv[i], "-c") && i + 1 < argc)
			index = atoi(argv[++i]);
		else if (!strcmp(argv[i], "-n") && i + 1 < argc)
			frames = atoi(argv[++i]);
		else if (!strcmp(argv[i], "-o") && i + 1 < argc)
			outFile = argv[++i];
		else if (!strcmp(argv[i], "-s") && i + 1 < argc)
			sscanf(argv[++i], "%ux%u", &width, &height);
		else if (!strcmp(argv[i], "-f") && i + 1 < argc)
			format = PixelFormat::fromString(argv[++i]);
		else {
			fprintf(stderr, "usage: %s [-l] [-c idx] [-s WxH] [-f NV12|RGB888] [-n frames] [-o file]\n",
				argv[0]);
			return 2;
		}
	}

	auto cm = std::make_unique<CameraManager>();
	if (cm->start()) {
		fprintf(stderr, "CameraManager start failed\n");
		return 1;
	}

	auto cameras = cm->cameras();
	printf("%zu camera(s)\n", cameras.size());
	for (size_t i = 0; i < cameras.size(); i++) {
		const ControlList &p = cameras[i]->properties();
		auto model = p.get(properties::Model);
		auto loc = p.get(properties::Location);
		auto rot = p.get(properties::Rotation);
		auto pas = p.get(properties::PixelArraySize);
		const std::string modelName = model ? std::string(*model) : "?";
		printf("[%zu] id=%s model=%s location=%s rotation=%d pixel_array=%s\n", i,
		       cameras[i]->id().c_str(), modelName.c_str(),
		       loc ? locationName(*loc) : "unset", rot ? *rot : -1,
		       pas ? pas->toString().c_str() : "?");
	}
	if (list || cameras.empty()) {
		cameras.clear();
		cm->stop();
		return cameras.empty() && !list ? 1 : 0;
	}
	if (index >= cameras.size()) {
		fprintf(stderr, "no camera %u\n", index);
		return 1;
	}

	std::shared_ptr<Camera> cam = cameras[index];
	if (cam->acquire()) {
		fprintf(stderr, "acquire failed (is the camera HAL running?)\n");
		return 1;
	}

	std::unique_ptr<CameraConfiguration> config =
		cam->generateConfiguration({ StreamRole::Viewfinder });
	StreamConfiguration &cfg = config->at(0);
	cfg.pixelFormat = format;
	cfg.size = { width, height };
	CameraConfiguration::Status st = config->validate();
	printf("configuration %s: %s (stride %u, frame %u)\n",
	       st == CameraConfiguration::Valid ? "valid" :
	       st == CameraConfiguration::Adjusted ? "adjusted" : "invalid",
	       cfg.toString().c_str(), cfg.stride, cfg.frameSize);
	if (st == CameraConfiguration::Invalid || cam->configure(config.get())) {
		fprintf(stderr, "configure failed\n");
		return 1;
	}

	FrameBufferAllocator allocator(cam);
	Stream *stream = cfg.stream();
	if (allocator.allocate(stream) < 0) {
		fprintf(stderr, "buffer allocation failed\n");
		return 1;
	}

	std::vector<std::unique_ptr<Request>> requests;
	for (const auto &buffer : allocator.buffers(stream)) {
		std::unique_ptr<Request> req = cam->createRequest();
		req->addBuffer(stream, buffer.get());
		requests.push_back(std::move(req));
	}

	cam->requestCompleted.connect(requestComplete);
	auto t0 = std::chrono::steady_clock::now();
	if (cam->start()) {
		fprintf(stderr, "start failed\n");
		return 1;
	}
	for (auto &r : requests)
		cam->queueRequest(r.get());

	unsigned int queued = requests.size();
	std::vector<uint8_t> lastFrame;
	while (true) {
		Request *done;
		{
			std::unique_lock<std::mutex> l(mtx);
			if (!cv.wait_for(l, std::chrono::seconds(5), [] { return lastDone != nullptr; })) {
				fprintf(stderr, "timeout waiting for frames (%u completed)\n", completed);
				break;
			}
			done = lastDone;
			lastDone = nullptr;
		}
		if (completed + failed >= frames) {
			FrameBuffer *fb = done->buffers().begin()->second;
			size_t total = 0;
			for (const auto &pl : fb->planes())
				total = std::max<size_t>(total, pl.offset + pl.length);
			void *m = mmap(nullptr, total, PROT_READ, MAP_SHARED, fb->planes()[0].fd.get(), 0);
			if (m != MAP_FAILED) {
				const uint8_t *p = static_cast<const uint8_t *>(m);
				double sum = 0;
				for (unsigned int y = 0; y < cfg.size.height; y++)
					for (unsigned int x = 0; x < cfg.size.width; x++)
						sum += p[fb->planes()[0].offset + y * cfg.stride + x];
				printf("last frame seq %u mean(plane0 bytes)=%.1f\n",
				       fb->metadata().sequence, sum / (cfg.size.width * cfg.size.height));
				lastFrame.assign(p, p + total);
				munmap(m, total);
			}
			break;
		}
		done->reuse(Request::ReuseBuffers);
		cam->queueRequest(done);
		queued++;
	}

	double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
	printf("%u frames completed, %u failed in %.2fs (%.1f fps)\n", completed, failed, secs,
	       completed / secs);
	cam->stop();

	if (!outFile.empty() && !lastFrame.empty()) {
		FILE *f = fopen(outFile.c_str(), "wb");
		if (f) {
			fwrite(lastFrame.data(), 1, lastFrame.size(), f);
			fclose(f);
			printf("wrote %s (%zu bytes, stride %u)\n", outFile.c_str(), lastFrame.size(),
			       cfg.stride);
		}
	}

	allocator.free(stream);
	cam->release();
	cam.reset();
	cameras.clear();
	cm->stop();
	return completed ? 0 : 1;
}
