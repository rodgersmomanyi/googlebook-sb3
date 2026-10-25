// gb_gpu_compute: llama.cpp engine wrapper. Every method runs on the single worker thread (llama/ggml objects are
// not shared with the network thread).
#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <string>
#include <utility>
#include <vector>

namespace gb {

struct GenParams {
    int max_tokens = 1024;
    float temperature = 0.8f;
    float top_p = 1.0f;
    uint32_t seed = 0xFFFFFFFFu;  // LLAMA_DEFAULT_SEED = random
    std::vector<std::string> stop;
};

struct GenResult {
    bool ok = false;
    int http_status = 200;     // when !ok
    std::string err_type;      // when !ok (INTERFACES 11.4 error types)
    std::string err_msg;
    std::string text;          // complete generated text (stop strings removed)
    int prompt_tokens = 0;
    int completion_tokens = 0;
    std::string finish_reason; // "stop" | "length"
    bool aborted = false;      // client went away / service stopping
    bool timed_out = false;    // per-request limit reached (finish_reason "length")
};

class Engine {
public:
    ~Engine();
    // Initialise llama/ggml and find a Vulkan GPU device (through libgb_nvk_loader.so). false = no device.
    bool init_backend(std::string &desc_or_err);
    bool backend_ready() const { return dev_ != nullptr; }
    // --probe: create and free a ggml backend on the device (VkDevice creation through the stub).
    bool probe_backend(std::string &msg);
    // --probe: tiny mul_mat on the device vs a CPU reference: 1 correct, 0 mismatch, -1 error.
    int probe_compute(std::string &msg);
    bool loaded() const { return model_ != nullptr; }
    const std::string &model_id() const { return model_id_; }
    const std::string &device_desc() const { return dev_desc_; }
    int n_ctx() const { return n_ctx_; }

    // abort (may be null) cancels a load in progress through llama's progress callback.
    bool load(const std::string &id, const std::string &path, int n_ctx, int n_threads, std::string &err,
              const std::atomic<bool> *abort = nullptr);
    void unload();

    bool chat_prompt(const std::vector<std::pair<std::string, std::string>> &msgs, std::string &out,
                     std::string &err);

    // on_text receives text that can no longer be part of a stop string (UTF-8 complete); returning false aborts.
    GenResult generate(const std::string &prompt, const GenParams &p,
                       const std::function<bool(const std::string &)> &on_text, const std::atomic<bool> &abort,
                       int64_t deadline_ms);

private:
    void *dev_ = nullptr;    // ggml_backend_dev_t
    void *model_ = nullptr;  // llama_model *
    void *ctx_ = nullptr;    // llama_context *
    std::string model_id_, dev_desc_;
    int n_ctx_ = 0;
    bool llama_inited_ = false;
};

int64_t now_ms();  // CLOCK_MONOTONIC

}  // namespace gb
