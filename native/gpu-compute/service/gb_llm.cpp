// gb_gpu_compute: llama.cpp engine wrapper (worker thread only).
#include "gb_llm.h"

#include <time.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

#include "ggml-alloc.h"
#include "ggml-backend.h"
#include "ggml.h"
#include "llama.h"

extern "C" void gb_log_msg(int level, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
#define LOGI(...) gb_log_msg(1, __VA_ARGS__)
#define LOGW(...) gb_log_msg(2, __VA_ARGS__)

namespace gb {

int64_t now_ms() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void llama_log_cb(enum ggml_log_level level, const char *text, void *) {
    if ((level != GGML_LOG_LEVEL_WARN && level != GGML_LOG_LEVEL_ERROR) || !text) return;
    std::string t(text);
    while (!t.empty() && (t.back() == '\n' || t.back() == '\r')) t.pop_back();
    if (!t.empty()) gb_log_msg(level == GGML_LOG_LEVEL_ERROR ? 3 : 2, "llama: %s", t.c_str());
}

Engine::~Engine() { unload(); }

bool Engine::init_backend(std::string &desc_or_err) {
    if (dev_) {
        desc_or_err = dev_desc_;
        return true;
    }
    if (!llama_inited_) {
        llama_log_set(llama_log_cb, nullptr);
        llama_backend_init();
        llama_inited_ = true;
    }
    // Only a Vulkan GPU device counts: the service never falls back to CPU inference.
    for (size_t i = 0; i < ggml_backend_dev_count(); i++) {
        ggml_backend_dev_t d = ggml_backend_dev_get(i);
        enum ggml_backend_dev_type t = ggml_backend_dev_type(d);
        ggml_backend_reg_t r = ggml_backend_dev_backend_reg(d);
        const char *rn = r ? ggml_backend_reg_name(r) : "";
        if ((t == GGML_BACKEND_DEVICE_TYPE_GPU || t == GGML_BACKEND_DEVICE_TYPE_IGPU) && rn && !strcmp(rn, "Vulkan")) {
            size_t fr = 0, tot = 0;
            ggml_backend_dev_memory(d, &fr, &tot);
            dev_ = d;
            dev_desc_ = std::string(ggml_backend_dev_name(d)) + " (" + ggml_backend_dev_description(d) + ", " +
                        std::to_string(tot >> 20) + " MiB)";
            desc_or_err = dev_desc_;
            LOGI("GPU device: %s", dev_desc_.c_str());
            return true;
        }
    }
    desc_or_err = "no Vulkan GPU device (NVK) found";
    return false;
}

bool Engine::probe_backend(std::string &msg) {
    if (!dev_) {
        msg = "no device";
        return false;
    }
    ggml_backend_t be = ggml_backend_dev_init((ggml_backend_dev_t)dev_, nullptr);
    if (!be) {
        msg = "backend init failed";
        return false;
    }
    msg = std::string("backend ") + ggml_backend_name(be) + " initialised";
    ggml_backend_free(be);
    return true;
}

// --probe compute self test: c = a x b (f32, K=256, M=64, N=8) on the GPU backend vs a CPU reference.
// 1 = correct, 0 = mismatch (expected under the noop drm-shim), -1 = error.
int Engine::probe_compute(std::string &msg) {
    if (!dev_) {
        msg = "no device";
        return -1;
    }
    const int K = 256, M = 64, N = 8;
    ggml_backend_t be = ggml_backend_dev_init((ggml_backend_dev_t)dev_, nullptr);
    if (!be) {
        msg = "backend init failed";
        return -1;
    }
    ggml_init_params ip = {16 * ggml_tensor_overhead() + ggml_graph_overhead(), nullptr, true};
    ggml_context *ctx = ggml_init(ip);
    ggml_tensor *a = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, K, M);
    ggml_tensor *b = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, K, N);
    ggml_tensor *c = ggml_mul_mat(ctx, a, b);
    ggml_cgraph *gf = ggml_new_graph(ctx);
    ggml_build_forward_expand(gf, c);
    ggml_backend_buffer_t buf = ggml_backend_alloc_ctx_tensors(ctx, be);
    int ret = -1;
    if (!buf) {
        msg = "device buffer allocation failed";
    } else {
        std::vector<float> av((size_t)K * M), bv((size_t)K * N), cv((size_t)M * N);
        for (size_t i = 0; i < av.size(); i++) av[i] = (float)((int)(i * 7 % 13) - 6) / 8.0f;
        for (size_t i = 0; i < bv.size(); i++) bv[i] = (float)((int)(i * 5 % 11) - 5) / 4.0f;
        ggml_backend_tensor_set(a, av.data(), 0, ggml_nbytes(a));
        ggml_backend_tensor_set(b, bv.data(), 0, ggml_nbytes(b));
        int64_t t0 = now_ms();
        enum ggml_status st = ggml_backend_graph_compute(be, gf);
        int64_t dt = now_ms() - t0;
        if (st != GGML_STATUS_SUCCESS) {
            msg = "graph compute failed (" + std::to_string((int)st) + ")";
        } else {
            ggml_backend_tensor_get(c, cv.data(), 0, ggml_nbytes(c));
            double maxerr = 0;
            for (int n = 0; n < N; n++)
                for (int m = 0; m < M; m++) {
                    double ref = 0;
                    for (int k = 0; k < K; k++) ref += (double)av[(size_t)m * K + k] * bv[(size_t)n * K + k];
                    double e = std::fabs(ref - cv[(size_t)n * M + m]) / (1.0 + std::fabs(ref));
                    if (!(e <= maxerr)) maxerr = std::isnan(e) ? 1e30 : std::max(maxerr, e);
                }
            ret = maxerr < 1e-3 ? 1 : 0;
            char b2[160];
            snprintf(b2, sizeof b2, "compute mul_mat %dx%dx%d in %lld ms: max rel err %.3g -> %s", M, N, K,
                     (long long)dt, maxerr, ret ? "correct" : "MISMATCH");
            msg = b2;
        }
        ggml_backend_buffer_free(buf);
    }
    ggml_free(ctx);
    ggml_backend_free(be);
    return ret;
}

static bool load_progress(float, void *ud) {
    const std::atomic<bool> *abort = (const std::atomic<bool> *)ud;
    return !(abort && abort->load());  // false = llama aborts the load
}

bool Engine::load(const std::string &id, const std::string &path, int n_ctx, int n_threads, std::string &err,
                  const std::atomic<bool> *abort) {
    unload();
    if (!dev_) {
        err = "no GPU device";
        return false;
    }
    llama_model_params mp = llama_model_default_params();
    static ggml_backend_dev_t devs[2];
    devs[0] = (ggml_backend_dev_t)dev_;
    devs[1] = nullptr;
    mp.devices = devs;
    mp.n_gpu_layers = 999;  // all layers on the dGPU
    mp.split_mode = LLAMA_SPLIT_MODE_NONE;
    mp.main_gpu = 0;
    mp.progress_callback = load_progress;
    mp.progress_callback_user_data = (void *)abort;
    int64_t t0 = now_ms();
    llama_model *m = llama_model_load_from_file(path.c_str(), mp);
    if (!m) {
        err = "cannot load model";
        return false;
    }
    llama_context_params cp = llama_context_default_params();
    cp.n_ctx = (uint32_t)n_ctx;
    cp.n_batch = 512;
    cp.n_ubatch = 512;
    cp.n_seq_max = 1;
    cp.n_threads = n_threads;
    cp.n_threads_batch = n_threads;
    cp.no_perf = true;
    cp.offload_kqv = true;
    llama_context *c = llama_init_from_model(m, cp);
    if (!c) {
        llama_model_free(m);
        err = "cannot create a context";
        return false;
    }
    model_ = m;
    ctx_ = c;
    model_id_ = id;
    n_ctx_ = (int)llama_n_ctx(c);
    LOGI("model %s loaded in %lld ms (n_ctx %d)", id.c_str(), (long long)(now_ms() - t0), n_ctx_);
    return true;
}

void Engine::unload() {
    if (ctx_) llama_free((llama_context *)ctx_);
    if (model_) {
        llama_model_free((llama_model *)model_);
        LOGI("model %s unloaded", model_id_.c_str());
    }
    ctx_ = nullptr;
    model_ = nullptr;
    model_id_.clear();
    n_ctx_ = 0;
}

bool Engine::chat_prompt(const std::vector<std::pair<std::string, std::string>> &msgs, std::string &out,
                         std::string &err) {
    if (!model_) {
        err = "no model";
        return false;
    }
    std::vector<llama_chat_message> cm;
    size_t total = 0;
    for (const auto &m : msgs) {
        cm.push_back({m.first.c_str(), m.second.c_str()});
        total += m.first.size() + m.second.size();
    }
    const char *tmpl = llama_model_chat_template((llama_model *)model_, nullptr);
    for (int attempt = 0; attempt < 2; attempt++) {
        const char *t = (attempt == 0 && tmpl) ? tmpl : "chatml";
        std::vector<char> buf(2 * total + 4096);
        int32_t n = llama_chat_apply_template(t, cm.data(), cm.size(), true, buf.data(), (int32_t)buf.size());
        if (n > (int32_t)buf.size()) {
            buf.resize((size_t)n + 1);
            n = llama_chat_apply_template(t, cm.data(), cm.size(), true, buf.data(), (int32_t)buf.size());
        }
        if (n >= 0) {
            out.assign(buf.data(), (size_t)n);
            return true;
        }
        if (attempt == 0 && tmpl) LOGW("model chat template not supported by llama_chat_apply_template; using chatml");
        if (!tmpl) break;
    }
    err = "cannot apply the chat template";
    return false;
}

// Length of the longest suffix of s that is a proper prefix of one of the stop strings.
static size_t stop_holdback(const std::string &s, const std::vector<std::string> &stops) {
    size_t best = 0;
    for (const auto &st : stops) {
        if (st.empty()) continue;
        size_t maxk = std::min(st.size() - 1, s.size());
        for (size_t k = maxk; k > best; k--)
            if (s.compare(s.size() - k, k, st, 0, k) == 0) {
                best = k;
                break;
            }
    }
    return best;
}

// Number of trailing bytes that form an incomplete UTF-8 sequence.
static size_t utf8_incomplete_tail(const std::string &s) {
    size_t n = s.size();
    for (size_t i = 1; i <= 3 && i <= n; i++) {
        unsigned char c = (unsigned char)s[n - i];
        if ((c & 0xC0) == 0x80) continue;  // continuation byte
        size_t need = (c & 0xE0) == 0xC0 ? 2 : (c & 0xF0) == 0xE0 ? 3 : (c & 0xF8) == 0xF0 ? 4 : 1;
        return need > i ? i : 0;
    }
    return 0;
}

GenResult Engine::generate(const std::string &prompt, const GenParams &p,
                           const std::function<bool(const std::string &)> &on_text, const std::atomic<bool> &abort,
                           int64_t deadline_ms) {
    GenResult r;
    llama_model *m = (llama_model *)model_;
    llama_context *c = (llama_context *)ctx_;
    if (!m || !c) {
        r.http_status = 500;
        r.err_type = "internal";
        r.err_msg = "no model loaded";
        return r;
    }
    const llama_vocab *vocab = llama_model_get_vocab(m);
    int32_t n = llama_tokenize(vocab, prompt.data(), (int32_t)prompt.size(), nullptr, 0, true, true);
    if (n == INT32_MIN) n = 0;
    n = n < 0 ? -n : n;
    std::vector<llama_token> toks((size_t)std::max(n, 1));
    n = llama_tokenize(vocab, prompt.data(), (int32_t)prompt.size(), toks.data(), (int32_t)toks.size(), true, true);
    if (n <= 0) {
        r.http_status = 400;
        r.err_type = "invalid_request";
        r.err_msg = "empty or untokenizable prompt";
        return r;
    }
    toks.resize((size_t)n);
    r.prompt_tokens = n;
    if (n >= n_ctx_) {
        r.http_status = 400;
        r.err_type = "invalid_request";
        r.err_msg = "prompt too long: " + std::to_string(n) + " tokens, context " + std::to_string(n_ctx_);
        return r;
    }
    llama_memory_clear(llama_get_memory(c), true);

    llama_sampler *smpl = llama_sampler_chain_init(llama_sampler_chain_default_params());
    if (p.temperature <= 0.0f) {
        llama_sampler_chain_add(smpl, llama_sampler_init_greedy());
    } else {
        if (p.top_p < 1.0f) llama_sampler_chain_add(smpl, llama_sampler_init_top_p(p.top_p, 1));
        llama_sampler_chain_add(smpl, llama_sampler_init_temp(p.temperature));
        llama_sampler_chain_add(smpl, llama_sampler_init_dist(p.seed));
    }

    const int nb = 512;
    for (int i = 0; i < n; i += nb) {
        if (abort.load() || now_ms() > deadline_ms) {
            r.aborted = abort.load();
            r.timed_out = !r.aborted;
            break;
        }
        int k = std::min(nb, n - i);
        if (llama_decode(c, llama_batch_get_one(&toks[(size_t)i], k)) != 0) {
            llama_sampler_free(smpl);
            r.http_status = 500;
            r.err_type = "internal";
            r.err_msg = "decode failed (prompt)";
            return r;
        }
    }
    int n_past = n;
    std::string pending;  // generated text not yet handed to on_text
    std::string finish = "length";
    bool stop_hit = false;
    if (!r.aborted && !r.timed_out) {
        for (int gen = 0; gen < p.max_tokens; gen++) {
            if (abort.load()) {
                r.aborted = true;
                break;
            }
            if (now_ms() > deadline_ms) {
                r.timed_out = true;
                break;
            }
            llama_token tok = llama_sampler_sample(smpl, c, -1);
            if (llama_vocab_is_eog(vocab, tok)) {
                finish = "stop";
                break;
            }
            char buf[256];
            int32_t len = llama_token_to_piece(vocab, tok, buf, sizeof buf, 0, false);
            std::string piece;
            if (len < 0) {
                std::vector<char> big((size_t)-len);
                len = llama_token_to_piece(vocab, tok, big.data(), (int32_t)big.size(), 0, false);
                if (len > 0) piece.assign(big.data(), (size_t)len);
            } else {
                piece.assign(buf, (size_t)len);
            }
            r.completion_tokens++;
            pending += piece;
            // stop strings
            size_t cut = std::string::npos;
            for (const auto &st : p.stop) {
                if (st.empty()) continue;
                size_t pos = pending.find(st);
                if (pos != std::string::npos && pos < cut) cut = pos;
            }
            if (cut != std::string::npos) {
                pending.resize(cut);
                finish = "stop";
                stop_hit = true;
                break;
            }
            size_t hold = std::max(stop_holdback(pending, p.stop), utf8_incomplete_tail(pending));
            if (pending.size() > hold) {
                std::string out = pending.substr(0, pending.size() - hold);
                pending.erase(0, pending.size() - hold);
                r.text += out;
                if (!on_text(out)) {
                    r.aborted = true;
                    break;
                }
            }
            if (n_past + 1 >= n_ctx_) {
                finish = "length";
                break;
            }
            if (llama_decode(c, llama_batch_get_one(&tok, 1)) != 0) {
                llama_sampler_free(smpl);
                r.http_status = 500;
                r.err_type = "internal";
                r.err_msg = "decode failed";
                return r;
            }
            n_past++;
        }
    }
    (void)stop_hit;
    llama_sampler_free(smpl);
    if (!r.aborted && !pending.empty()) {
        r.text += pending;
        on_text(pending);
    }
    r.finish_reason = finish;
    r.ok = true;
    return r;
}

}  // namespace gb
