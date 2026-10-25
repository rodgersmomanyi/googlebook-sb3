// gb_gpu_compute: NVIDIA (NVK) Vulkan compute service for Googlebook OS on the Surface Book 3 (plan Phase 6,
// INTERFACES section 11). Front end + lifecycle; the model work is in gb_llm.cpp.
//
//   * HTTP/1.1 over the AF_UNIX stream socket /dev/socket/gb_gpu_compute (created by init, fd from
//     ANDROID_SOCKET_gb_gpu_compute). Never TCP, never loopback (invariant 18).
//   * SO_PEERCRED allowlist on every connection: uid 0, 2000, the GbGpu appId (any user), the appIds in
//     /data/vendor/gb/compute_allow (any user). Everyone else: 403 and close.
//   * One model, one generation in flight, <= 3 queued, max 4 connections, body <= 1 MiB, model <= 3584 MiB,
//     600 s per request, 4 CPU threads, n_ctx 4096 (configurable <= 8192 in gb_gpu_compute.conf).
//   * gb_power interplay: one long-lived connection to /dev/socket/gb_power, "compute-state <s>" for every change;
//     loading/loaded/serving make gb_power hold the dGPU awake (power/control=on). No hold, no model.
//   * Lifecycle: 120 s without a request in flight and without a client connection -> unload, idle, exit 0.
//     Screen off (i915 eDP dpms) for >= 30 s -> unload. SIGTERM -> abort, unload, exit 0 within 5 s.
//
// Device build: no environment knobs except the Mesa/loader ones init sets. Test build (-DGB_COMPUTE_TESTING) reads a
// few GB_COMPUTE_* variables (root prefix, socket paths, shortened timers) so the same code runs on the builder.
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

#ifdef __ANDROID__
#include <android/log.h>
#endif

#include "gb_llm.h"
#include "nlohmann/json.hpp"

using json = nlohmann::json;
using gb::now_ms;

#ifndef GB_COMPUTE_VERSION
#define GB_COMPUTE_VERSION "gb_gpu_compute 1"
#endif
#ifndef GB_LLAMA_TAG
#define GB_LLAMA_TAG "unknown"
#endif

enum { EXIT_OK = 0, EXIT_ERR = 1, EXIT_NO_DEVICE = 3, EXIT_CONFIG = 4 };

// ------------------------------------------------------------------------------------------------- logging
extern "C" void gb_log_msg(int level, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
extern "C" void gb_log_msg(int level, const char *fmt, ...) {
    char b[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
#ifdef __ANDROID__
    __android_log_write(level >= 3 ? ANDROID_LOG_ERROR : level == 2 ? ANDROID_LOG_WARN : ANDROID_LOG_INFO,
                        "gb_gpu_compute", b);
#endif
    int64_t t = now_ms();
    fprintf(stderr, "[%lld.%03lld] gb_gpu_compute: %s\n", (long long)(t / 1000), (long long)(t % 1000), b);
}
#define LOGI(...) gb_log_msg(1, __VA_ARGS__)
#define LOGW(...) gb_log_msg(2, __VA_ARGS__)
#define LOGE(...) gb_log_msg(3, __VA_ARGS__)

// ------------------------------------------------------------------------------------------------- config
namespace {

struct Cfg {
    std::string root;         // test prefix for absolute paths ("" on the device)
    std::string listen_path;  // test build only: bind this path instead of the init socket
    std::string power_sock = "/dev/socket/gb_power";
    int n_ctx = 4096;
    int n_threads = 4;
    int idle_exit_s = 120;
    int screen_off_s = 30;
    int screen_poll_ms = 5000;
    int max_conn = 4;
    int queue_max = 3;
    int req_timeout_s = 600;
    int hdr_timeout_s = 30;
    size_t max_body = 1u << 20;
    long long max_model = 3584LL << 20;
    int max_tokens_cap = 4096;
    int max_tokens_default = 1024;
    int power_wait_ms = 3000;
    int hold_lost_unload_ms = 10000;
    int keepalive_ms = 15000;  // re-send the current compute-state this often (gb_power 9.1 idle rule)
    size_t max_out = 8u << 20;
    rlim_t rlimit_data = (rlim_t)8 << 30;
} g_cfg;

std::string P(const std::string &abs) { return g_cfg.root + abs; }

#ifdef GB_COMPUTE_TESTING
int env_int(const char *n, int def, int lo, int hi) {
    const char *v = getenv(n);
    if (!v || !*v) return def;
    char *e = nullptr;
    long x = strtol(v, &e, 10);
    if (*e || x < lo || x > hi) return def;
    return (int)x;
}
#endif

void load_config() {
#ifdef GB_COMPUTE_TESTING
    if (const char *r = getenv("GB_COMPUTE_ROOT")) g_cfg.root = r;
    if (const char *s = getenv("GB_COMPUTE_SOCKET")) g_cfg.listen_path = s;
    if (const char *s = getenv("GB_POWER_SOCKET")) g_cfg.power_sock = s;
    g_cfg.idle_exit_s = env_int("GB_COMPUTE_IDLE_S", g_cfg.idle_exit_s, 1, 3600);
    g_cfg.screen_off_s = env_int("GB_COMPUTE_SCREEN_OFF_S", g_cfg.screen_off_s, 0, 3600);
    g_cfg.screen_poll_ms = env_int("GB_COMPUTE_SCREEN_POLL_MS", g_cfg.screen_poll_ms, 100, 60000);
    g_cfg.max_conn = env_int("GB_COMPUTE_TEST_MAX_CONN", g_cfg.max_conn, 1, 64);
    g_cfg.req_timeout_s = env_int("GB_COMPUTE_REQ_TIMEOUT_S", g_cfg.req_timeout_s, 1, 3600);
    g_cfg.hdr_timeout_s = env_int("GB_COMPUTE_HDR_TIMEOUT_S", g_cfg.hdr_timeout_s, 1, 3600);
    g_cfg.hold_lost_unload_ms = env_int("GB_COMPUTE_HOLD_LOST_MS", g_cfg.hold_lost_unload_ms, 100, 600000);
    g_cfg.keepalive_ms = env_int("GB_COMPUTE_KEEPALIVE_MS", g_cfg.keepalive_ms, 100, 600000);
#endif
    // Service config (INTERFACES 11.5: context configurable <= 8192 in the service config, not per request).
    FILE *f = fopen(P("/data/vendor/gb/compute/gb_gpu_compute.conf").c_str(), "re");
    if (f) {
        char line[256];
        while (fgets(line, sizeof line, f)) {
            int v = 0;
            if (line[0] == '#') continue;
            if (sscanf(line, "ctx=%d", &v) == 1) {
                if (v >= 512 && v <= 8192)
                    g_cfg.n_ctx = v;
                else
                    LOGW("config: ctx=%d out of range 512..8192, ignored", v);
            }
        }
        fclose(f);
    }
}

// ------------------------------------------------------------------------------------------------- small helpers
bool read_text(const std::string &path, std::string &out, size_t max = 65536) {
    FILE *f = fopen(path.c_str(), "re");
    if (!f) return false;
    out.clear();
    char b[4096];
    size_t n;
    while ((n = fread(b, 1, sizeof b, f)) > 0 && out.size() < max) out.append(b, n);
    fclose(f);
    return true;
}

std::string first_word(const std::string &s) {
    size_t e = s.find_first_of(" \t\r\n");
    return s.substr(0, e);
}

bool is_regular(const std::string &p) {
    struct stat st;
    return stat(p.c_str(), &st) == 0 && S_ISREG(st.st_mode);
}

// [A-Za-z0-9._-]{1,64}
bool model_id_ok(const std::string &id) {
    if (id.empty() || id.size() > 64) return false;
    for (char c : id)
        if (!(isalnum((unsigned char)c) || c == '.' || c == '_' || c == '-')) return false;
    return true;
}

// INTERFACES 2.2: a /sys/class/drm/renderD<n> bound to nouveau.
bool has_nouveau_node() {
    std::string d = P("/sys/class/drm");
    DIR *dir = opendir(d.c_str());
    if (!dir) return false;
    bool found = false;
    while (struct dirent *e = readdir(dir)) {
        if (strncmp(e->d_name, "renderD", 7) || !e->d_name[7] ||
            strspn(e->d_name + 7, "0123456789") != strlen(e->d_name + 7))
            continue;  // renderD<digits> only
        char l[PATH_MAX];
        std::string q = d + "/" + e->d_name + "/device/driver";
        ssize_t r = readlink(q.c_str(), l, sizeof l - 1);
        if (r <= 0) continue;
        l[r] = 0;
        const char *b = strrchr(l, '/');
        if (!strcmp(b ? b + 1 : l, "nouveau")) {
            found = true;
            break;
        }
    }
    closedir(dir);
    return found;
}

// The dGPU function (INTERFACES 2.2): first sorted PCI device with vendor 0x10de and class 0x03*.
std::string dgpu_dir() {
    std::string d = P("/sys/bus/pci/devices");
    DIR *dir = opendir(d.c_str());
    if (!dir) return "";
    std::vector<std::string> names;
    while (struct dirent *e = readdir(dir))
        if (e->d_name[0] != '.') names.push_back(e->d_name);
    closedir(dir);
    std::sort(names.begin(), names.end());
    for (const auto &n : names) {
        std::string v, c;
        if (!read_text(d + "/" + n + "/vendor", v) || !read_text(d + "/" + n + "/class", c)) continue;
        if (first_word(v) == "0x10de" && c.compare(0, 4, "0x03") == 0) return d + "/" + n;
    }
    return "";
}

// 1 = every i915 eDP connector reports dpms Off, 0 = on, -1 = no eDP dpms attribute (feature disabled; D16).
int screen_state() {
    std::string d = P("/sys/class/drm");
    DIR *dir = opendir(d.c_str());
    if (!dir) return -1;
    int seen = 0, off = 0;
    while (struct dirent *e = readdir(dir)) {
        const char *n = e->d_name;
        if (strncmp(n, "card", 4) || !strstr(n, "-eDP-")) continue;
        std::string s;
        if (!read_text(d + "/" + n + "/dpms", s)) continue;
        seen++;
        if (first_word(s) == "Off") off++;
    }
    closedir(dir);
    if (!seen) return -1;
    return off == seen ? 1 : 0;
}

long long mem_available_kb() {
    std::string s;
    if (!read_text(P("/proc/meminfo"), s)) return -1;
    size_t p = s.find("MemAvailable:");
    if (p == std::string::npos) return -1;
    return atoll(s.c_str() + p + 13);
}

// ------------------------------------------------------------------------------------------------- access control
class Access {
public:
    bool allowed(uid_t uid) {
        if (uid == 0 || uid == 2000) return true;
        int app = (int)(uid % 100000);
        if (app < 10000) return false;  // system uids only via the explicit 0/2000 rule
        int ui = ui_appid();
        if (ui > 0 && app == ui) return true;
        reload_allow();
        return allow_.count(app) != 0;
    }

private:
    long long allow_mt_ = -2;
    std::set<int> allow_;
    int64_t ui_checked_ = -1000000;
    int ui_appid_ = -1;

    void reload_allow() {
        struct stat st;
        std::string p = P("/data/vendor/gb/compute_allow");
        long long mt = -1;
        if (stat(p.c_str(), &st) == 0) mt = (long long)st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec;
        if (mt == allow_mt_) return;
        allow_mt_ = mt;
        allow_.clear();
        FILE *f = mt >= 0 ? fopen(p.c_str(), "re") : nullptr;
        if (!f) return;
        char line[256];
        while (fgets(line, sizeof line, f)) {
            if (line[0] == '#') continue;
            int id = -1;
            char pkg[160];
            if (sscanf(line, "%d %159s", &id, pkg) == 2 && id >= 10000 && id < 100000) allow_.insert(id);
        }
        fclose(f);
        LOGI("compute_allow: %zu app(s)", allow_.size());
    }

    // GbGpu's appId. The service (uid nobody) cannot read /data/system/packages.list (0640 system:package_info), so
    // it falls back to the owner of the app's data directory /data/data/gb.sb3.gpu (/data/data is 0771 system:system,
    // so stat() works for anyone; only PackageManager creates entries there). Both only count when the system APK
    // /odm/app/GbGpu/GbGpu.apk exists (same squatting guard as gb_power, INTERFACES 9.2).
    int ui_appid() {
        int64_t t = now_ms();
        if (t - ui_checked_ < 5000) return ui_appid_;
        ui_checked_ = t;
        ui_appid_ = -1;
        if (!is_regular(P("/odm/app/GbGpu/GbGpu.apk"))) return -1;
        std::string pl;
        if (read_text(P("/data/system/packages.list"), pl, 4u << 20)) {
            size_t pos = 0;
            while (pos < pl.size()) {
                size_t e = pl.find('\n', pos);
                std::string line = pl.substr(pos, e == std::string::npos ? std::string::npos : e - pos);
                pos = e == std::string::npos ? pl.size() : e + 1;
                if (line.compare(0, 11, "gb.sb3.gpu ") == 0) {
                    int id = atoi(line.c_str() + 11);
                    if (id >= 10000 && id <= 19999) ui_appid_ = id;
                    break;
                }
            }
            if (ui_appid_ > 0) return ui_appid_;
        }
        struct stat st;
        if (stat(P("/data/data/gb.sb3.gpu").c_str(), &st) == 0 && S_ISDIR(st.st_mode)) {
            int id = (int)(st.st_uid % 100000);
            if (id >= 10000 && id <= 19999) ui_appid_ = id;
        }
        return ui_appid_;
    }
} g_access;

// ------------------------------------------------------------------------------------------------- gb_power client
// One long-lived connection (INTERFACES 9.3 compute-state, 11.5). Used by the worker (state changes) and the main
// thread (start, EOF detection, reconnect). The mutex is held for one request/reply exchange (gb_power never blocks).
class Power {
public:
    // Sends compute-state s; true if gb_power answered ok. *hold = gb_power's hold flag.
    bool set_state(const std::string &s, bool *hold = nullptr) {
        std::lock_guard<std::mutex> l(mu_);
        busy_ = true;
        want_ = s;
        bool h = false;
        bool ok = send_locked(&h, true);  // a state change always tries to (re)connect
        if (hold) *hold = h;
        busy_ = false;
        return ok;
    }
    // Exit path: tell gb_power "idle" unless that is already the last state sent.
    void idle_if_needed() {
        std::lock_guard<std::mutex> l(mu_);
        if (want_ == "idle") return;
        want_ = "idle";
        bool h = false;
        send_locked(&h, true);
    }
    bool hold() const { return hold_.load(); }
    bool busy() const { return busy_.load(); }
    bool wants_hold() {
        std::lock_guard<std::mutex> l(mu_);
        return want_ == "loading" || want_ == "loaded" || want_ == "serving";
    }
    int fd() const { return fd_.load(); }
    // Main thread: poll() reported activity on the fd. Never blocks (try_lock).
    void on_readable() {
        std::unique_lock<std::mutex> l(mu_, std::try_to_lock);
        if (!l.owns_lock() || fd_ < 0) return;
        char b[512];
        ssize_t r = recv(fd_, b, sizeof b, MSG_DONTWAIT);
        if (r == 0 || (r < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) {
            LOGW("gb_power connection lost%s", hold_ ? " (power hold released by gb_power)" : "");
            close_locked();
        }
    }
    // Main thread, periodic: reconnect (every 2 s while the connection is down, whatever the state, so gb_power's
    // vendor.gb.compute.state stays right) and re-send the current state every keepalive_ms on a live connection.
    // gb_power exempts the compute-svc connection from its 30 s idle close (INTERFACES 9.1); the keep-alive is the
    // second line of defence: the re-sent state is idempotent in gb_power.
    void maintain() {
        std::unique_lock<std::mutex> l(mu_, std::try_to_lock);
        if (!l.owns_lock()) return;
        int64_t t = now_ms();
        bool down = fd_ < 0;
        if ((down && t >= next_try_) || (!down && t - last_tx_ >= g_cfg.keepalive_ms)) {
            bool h = false;
            busy_ = true;
            if (send_locked(&h, false) && down) LOGI("gb_power reconnected, state %s re-sent (hold %d)", want_.c_str(), h);
            busy_ = false;
        }
    }

private:
    std::mutex mu_;
    std::atomic<int> fd_{-1};
    std::atomic<bool> hold_{false};
    std::atomic<bool> busy_{false};
    std::string want_ = "idle";
    std::string rbuf_;
    int64_t next_try_ = 0;
    int64_t last_tx_ = 0;  // last answered compute-state
    bool warned_role_ = false;

    void close_locked() {
        if (fd_ >= 0) close(fd_);
        fd_ = -1;
        hold_ = false;
        rbuf_.clear();
    }

    bool xact_locked(const std::string &cmd, const std::string &line, json &reply) {
        std::string out = line + "\n";
        size_t off = 0;
        int64_t dl = now_ms() + 2000;
        while (off < out.size()) {
            ssize_t w = send(fd_, out.data() + off, out.size() - off, MSG_NOSIGNAL | MSG_DONTWAIT);
            if (w > 0) {
                off += (size_t)w;
                continue;
            }
            if (w < 0 && (errno == EAGAIN || errno == EINTR) && now_ms() < dl) {
                struct pollfd p = {fd_, POLLOUT, 0};
                poll(&p, 1, 100);
                continue;
            }
            return false;
        }
        while (now_ms() < dl) {
            size_t nl;
            while ((nl = rbuf_.find('\n')) != std::string::npos) {
                std::string l = rbuf_.substr(0, nl);
                rbuf_.erase(0, nl + 1);
                json j = json::parse(l, nullptr, false);
                if (j.is_object() && j.value("cmd", std::string()) == cmd) {
                    reply = j;
                    return true;
                }
            }
            struct pollfd p = {fd_, POLLIN, 0};
            int pr = poll(&p, 1, 200);
            if (pr <= 0) continue;
            char b[4096];
            ssize_t r = recv(fd_, b, sizeof b, MSG_DONTWAIT);
            if (r > 0) {
                rbuf_.append(b, (size_t)r);
                if (rbuf_.size() > 65536) return false;
            } else if (r == 0 || (errno != EAGAIN && errno != EINTR)) {
                return false;
            }
        }
        return false;
    }

    bool connect_locked() {
        int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
        if (fd < 0) return false;
        struct sockaddr_un a;
        memset(&a, 0, sizeof a);
        a.sun_family = AF_UNIX;
        if (g_cfg.power_sock.size() >= sizeof a.sun_path) {
            close(fd);
            return false;
        }
        memcpy(a.sun_path, g_cfg.power_sock.c_str(), g_cfg.power_sock.size());
        if (connect(fd, (struct sockaddr *)&a, sizeof a) != 0 && errno != EINPROGRESS) {
            close(fd);
            return false;
        }
        fd_ = fd;
        json r;
        if (!xact_locked("hello", "hello", r) || !r.value("ok", false)) {
            close_locked();
            return false;
        }
        std::string role = r.value("role", std::string());
        if (role != "compute-svc") {
            if (!warned_role_) LOGW("gb_power sees this process as role '%s', not compute-svc: no power hold", role.c_str());
            warned_role_ = true;
            close_locked();
            return false;
        }
        return true;
    }

    bool send_locked(bool *hold, bool force) {
        *hold = false;
        if (fd_ < 0) {
            if (!force && now_ms() < next_try_) return false;
            if (!connect_locked()) {
                next_try_ = now_ms() + 2000;
                return false;
            }
        }
        json r;
        if (!xact_locked("compute-state", "compute-state " + want_, r)) {
            LOGW("gb_power: no reply to compute-state %s", want_.c_str());
            close_locked();
            next_try_ = now_ms() + 2000;
            return false;
        }
        last_tx_ = now_ms();  // answered (also a refusal): the keep-alive waits a full period again
        if (!r.value("ok", false)) {
            LOGW("gb_power: compute-state %s refused: %s", want_.c_str(), r.value("err", std::string("?")).c_str());
            hold_ = false;
            return false;
        }
        *hold = r.value("hold", 0) == 1;
        hold_ = *hold;
        return true;
    }
} g_power;

// ------------------------------------------------------------------------------------------------- jobs / worker
enum EState { ES_IDLE, ES_LOADING, ES_LOADED, ES_SERVING, ES_UNLOADING };
std::atomic<int> g_estate{ES_IDLE};
std::mutex g_model_mu;
std::string g_loaded_model;  // guarded by g_model_mu
std::atomic<int64_t> g_last_job_end{0};
std::atomic<bool> g_backend_failed{false};

struct Job {
    enum Kind { GEN, UNLOAD } kind = GEN;
    bool chat = true, stream = false;
    std::string model, prompt, rid;
    std::vector<std::pair<std::string, std::string>> msgs;
    gb::GenParams gp;
    long long created = 0;
    int64_t deadline = 0;
    std::atomic<bool> abort{false};
    // output, guarded by mu
    std::mutex mu;
    bool done = false, sse_started = false;
    std::string sse;  // pending server-sent-event bytes
    int status = 0;
    std::string body;
};
using JobP = std::shared_ptr<Job>;

int g_wake[2] = {-1, -1};
void wake_main() {
    char c = 1;
    ssize_t r = write(g_wake[1], &c, 1);
    (void)r;
}

std::string err_body(int code, const std::string &type, const std::string &msg) {
    json j = {{"error", {{"code", code}, {"type", type}, {"message", msg}}}};
    return j.dump(-1, ' ', false, json::error_handler_t::replace);
}

class Worker {
public:
    std::mutex qmu;
    std::condition_variable qcv;
    std::deque<JobP> q;
    JobP running;
    bool stop = false;

    void start() { th_ = std::thread([this] { run(); }); }
    bool idle() {
        std::lock_guard<std::mutex> l(qmu);
        return !running && q.empty();
    }

private:
    std::thread th_;
    gb::Engine eng_;

    void set_state(int s) { g_estate = s; }

    void fail(const JobP &j, int code, const std::string &type, const std::string &msg) {
        std::lock_guard<std::mutex> l(j->mu);
        if (j->stream && j->sse_started) {
            j->sse += "data: " + err_body(code, type, msg) + "\n\ndata: [DONE]\n\n";
        } else {
            j->status = code;
            j->body = err_body(code, type, msg);
        }
    }

    void do_unload() {
        if (!eng_.loaded()) return;
        set_state(ES_UNLOADING);
        g_power.set_state("unloading");
        eng_.unload();
        {
            std::lock_guard<std::mutex> l(g_model_mu);
            g_loaded_model.clear();
        }
        g_power.set_state("idle");
        set_state(ES_IDLE);
    }

    void wait_power_on() {
        std::string d = dgpu_dir();
        if (d.empty()) return;
        int64_t dl = now_ms() + g_cfg.power_wait_ms;
        std::string v;
        while (now_ms() < dl) {
            if (read_text(d + "/power/control", v) && first_word(v) == "on") return;
            usleep(100 * 1000);
        }
        LOGW("dGPU power/control did not read 'on' within %d ms; continuing", g_cfg.power_wait_ms);
    }

    bool ensure_model(const JobP &j) {
        if (eng_.loaded() && eng_.model_id() == j->model) return true;
        std::string path = P("/data/vendor/gb/models/") + j->model + ".gguf";
        struct stat st;
        if (lstat(path.c_str(), &st) || !S_ISREG(st.st_mode)) {
            fail(j, 404, "not_found", "unknown model");
            return false;
        }
        if ((long long)st.st_size > g_cfg.max_model) {
            fail(j, 413, "too_large", "model larger than 3584 MiB");
            return false;
        }
        do_unload();
        long long avail = mem_available_kb();
        long long need_kb = (std::min<long long>(st.st_size, 1LL << 30) >> 10) + (256 << 10);
        if (avail >= 0 && avail < need_kb) {
            fail(j, 503, "gb_unavailable", "not enough free memory to load the model");
            return false;
        }
        set_state(ES_LOADING);
        bool hold = false;
        if (!g_power.set_state("loading", &hold) || !hold) {
            g_power.set_state("idle");
            set_state(ES_IDLE);
            fail(j, 503, "gb_unavailable", "gb_power did not grant the dGPU power hold");
            return false;
        }
        wait_power_on();
        std::string desc;
        if (!eng_.init_backend(desc)) {
            g_backend_failed = true;
            g_power.set_state("idle");
            set_state(ES_IDLE);
            fail(j, 503, "gb_unavailable", desc);
            return false;
        }
        std::string err;
        if (!eng_.load(j->model, path, g_cfg.n_ctx, g_cfg.n_threads, err, &j->abort)) {
            g_power.set_state("idle");
            set_state(ES_IDLE);
            fail(j, 500, "internal", err);
            return false;
        }
        {
            std::lock_guard<std::mutex> l(g_model_mu);
            g_loaded_model = j->model;
        }
        g_power.set_state("loaded");
        set_state(ES_LOADED);
        return true;
    }

    json chunk(const JobP &j) {
        json c = {{"id", j->rid}, {"object", j->chat ? "chat.completion.chunk" : "text_completion"},
                  {"created", j->created}, {"model", j->model}};
        return c;
    }

    void emit(const JobP &j, const json &ev) {
        std::lock_guard<std::mutex> l(j->mu);
        j->sse += "data: " + ev.dump(-1, ' ', false, json::error_handler_t::replace) + "\n\n";
        j->sse_started = true;
    }

    void do_gen(const JobP &j) {
        if (j->abort) return;
        if (!ensure_model(j)) return;
        set_state(ES_SERVING);
        g_power.set_state("serving");
        std::string prompt = j->prompt, err;
        if (j->chat && !eng_.chat_prompt(j->msgs, prompt, err)) {
            fail(j, 500, "internal", err);
        } else {
            if (j->stream) {
                json c = chunk(j);
                if (j->chat)
                    c["choices"] = json::array({{{"index", 0}, {"delta", {{"role", "assistant"}, {"content", ""}}},
                                                 {"finish_reason", nullptr}}});
                else
                    c["choices"] = json::array({{{"index", 0}, {"text", ""}, {"finish_reason", nullptr}}});
                emit(j, c);
                wake_main();
            }
            auto on_text = [&](const std::string &t) -> bool {
                if (j->stream && !t.empty()) {
                    json c = chunk(j);
                    if (j->chat)
                        c["choices"] = json::array(
                            {{{"index", 0}, {"delta", {{"content", t}}}, {"finish_reason", nullptr}}});
                    else
                        c["choices"] = json::array({{{"index", 0}, {"text", t}, {"finish_reason", nullptr}}});
                    emit(j, c);
                    wake_main();
                }
                return !j->abort.load();
            };
            gb::GenResult r = eng_.generate(prompt, j->gp, on_text, j->abort, j->deadline);
            if (r.timed_out) LOGW("request %s hit the %d s limit", j->rid.c_str(), g_cfg.req_timeout_s);
            if (!r.ok) {
                fail(j, r.http_status, r.err_type, r.err_msg);
            } else if (!r.aborted) {
                json usage = {{"prompt_tokens", r.prompt_tokens},
                              {"completion_tokens", r.completion_tokens},
                              {"total_tokens", r.prompt_tokens + r.completion_tokens}};
                if (j->stream) {
                    json c = chunk(j);
                    if (j->chat)
                        c["choices"] = json::array({{{"index", 0}, {"delta", json::object()}, {"finish_reason", r.finish_reason}}});
                    else
                        c["choices"] = json::array({{{"index", 0}, {"text", ""}, {"finish_reason", r.finish_reason}}});
                    c["usage"] = usage;
                    emit(j, c);
                    std::lock_guard<std::mutex> l(j->mu);
                    j->sse += "data: [DONE]\n\n";
                } else {
                    json b = {{"id", j->rid}, {"object", j->chat ? "chat.completion" : "text_completion"},
                              {"created", j->created}, {"model", j->model}};
                    if (j->chat)
                        b["choices"] = json::array({{{"index", 0},
                                                     {"message", {{"role", "assistant"}, {"content", r.text}}},
                                                     {"finish_reason", r.finish_reason}}});
                    else
                        b["choices"] = json::array({{{"index", 0}, {"text", r.text}, {"finish_reason", r.finish_reason}}});
                    b["usage"] = usage;
                    std::lock_guard<std::mutex> l(j->mu);
                    j->status = 200;
                    j->body = b.dump(-1, ' ', false, json::error_handler_t::replace);
                }
            }
            LOGI("request %s: %d prompt + %d completion tokens, finish %s%s", j->rid.c_str(), r.prompt_tokens,
                 r.completion_tokens, r.finish_reason.c_str(), r.aborted ? " (aborted)" : "");
        }
        g_power.set_state("loaded");
        set_state(ES_LOADED);
    }

    void run() {
        for (;;) {
            JobP j;
            {
                std::unique_lock<std::mutex> l(qmu);
                qcv.wait(l, [this] { return stop || !q.empty(); });
                if (q.empty()) break;  // stop requested and nothing left
                j = q.front();
                q.pop_front();
                running = j;
            }
            if (j->kind == Job::UNLOAD)
                do_unload();
            else
                do_gen(j);
            {
                std::lock_guard<std::mutex> l(j->mu);
                if (j->kind == Job::UNLOAD && !j->status) {
                    j->status = 200;
                    j->body = "{\"unloaded\":true}";
                }
                j->done = true;
            }
            {
                std::lock_guard<std::mutex> l(qmu);
                running.reset();
            }
            g_last_job_end = now_ms();
            wake_main();
        }
    }
} g_worker;

// ------------------------------------------------------------------------------------------------- HTTP connections
struct Conn {
    int fd = -1;
    uid_t uid = (uid_t)-1;
    pid_t pid = 0;
    int reject = 0;  // 0 = normal; else HTTP status (403/429) sent once the request header is in
    bool hdr_seen = false;  // reject mode: end of the request header received
    std::string in, out;
    int64_t deadline = 0;  // receive / keep-alive deadline
    bool close_after = false;
    bool dead = false;
    JobP job;
    bool streaming = false;
};

const char *reason(int s) {
    switch (s) {
    case 100: return "Continue";
    case 200: return "OK";
    case 400: return "Bad Request";
    case 403: return "Forbidden";
    case 404: return "Not Found";
    case 409: return "Conflict";
    case 413: return "Payload Too Large";
    case 429: return "Too Many Requests";
    case 500: return "Internal Server Error";
    case 503: return "Service Unavailable";
    default: return "Error";
    }
}

std::string http_resp(int status, const std::string &body, bool keep_alive) {
    std::string h = "HTTP/1.1 " + std::to_string(status) + " " + reason(status) +
                    "\r\nContent-Type: application/json\r\nContent-Length: " + std::to_string(body.size()) +
                    (keep_alive ? "\r\n" : "\r\nConnection: close\r\n") + "Server: " GB_COMPUTE_VERSION "\r\n\r\n";
    return h + body;
}

const char *type_for(int status) {
    switch (status) {
    case 400: return "invalid_request";
    case 403: return "forbidden";
    case 404: return "not_found";
    case 409: return "conflict";
    case 413: return "too_large";
    case 429: return "busy";
    case 503: return "gb_unavailable";
    default: return "internal";
    }
}

struct Req {
    std::string method, path, version;
    std::map<std::string, std::string> hdr;
    std::string body;
    bool keep_alive = true;
};

std::vector<std::unique_ptr<Conn>> g_conns;
std::atomic<long long> g_rid{0};
bool g_shutdown = false;
int64_t g_shutdown_at = 0;
const char *g_shutdown_why = "";

void send_now(Conn &c, int status, const std::string &body, bool close_conn) {
    c.out += http_resp(status, body, !close_conn);
    if (close_conn) c.close_after = true;
}
void send_err(Conn &c, int status, const std::string &msg, bool close_conn = false) {
    send_now(c, status, err_body(status, type_for(status), msg), close_conn);
}

// 1 = request parsed into r and removed from c.in, 0 = need more bytes, -1 = protocol error (reply queued)
int parse_req(Conn &c, Req &r) {
    size_t he = c.in.find("\r\n\r\n");
    if (he == std::string::npos) {
        if (c.in.size() > 16384) {
            send_err(c, 400, "request header too large", true);
            return -1;
        }
        return 0;
    }
    std::string head = c.in.substr(0, he);
    size_t le = head.find("\r\n");
    std::string rl = head.substr(0, le);
    size_t s1 = rl.find(' '), s2 = rl.rfind(' ');
    if (s1 == std::string::npos || s2 == s1) {
        send_err(c, 400, "bad request line", true);
        return -1;
    }
    r.method = rl.substr(0, s1);
    r.path = rl.substr(s1 + 1, s2 - s1 - 1);
    r.version = rl.substr(s2 + 1);
    if (r.version.compare(0, 7, "HTTP/1.") != 0) {
        send_err(c, 400, "HTTP/1.x only", true);
        return -1;
    }
    r.keep_alive = r.version == "HTTP/1.1";
    size_t pos = le == std::string::npos ? head.size() : le + 2;
    while (pos < head.size()) {
        size_t e = head.find("\r\n", pos);
        std::string line = head.substr(pos, e == std::string::npos ? std::string::npos : e - pos);
        pos = e == std::string::npos ? head.size() : e + 2;
        size_t colon = line.find(':');
        if (colon == std::string::npos || !colon) {
            send_err(c, 400, "bad header", true);
            return -1;
        }
        std::string k = line.substr(0, colon), v = line.substr(colon + 1);
        std::transform(k.begin(), k.end(), k.begin(), [](unsigned char ch) { return (char)tolower(ch); });
        v.erase(0, v.find_first_not_of(" \t"));
        while (!v.empty() && (v.back() == ' ' || v.back() == '\t')) v.pop_back();
        if (k == "content-length" && r.hdr.count(k) && r.hdr[k] != v) {
            send_err(c, 400, "conflicting Content-Length", true);
            return -1;
        }
        r.hdr[k] = v;
    }
    if (r.hdr.count("transfer-encoding")) {
        send_err(c, 400, "chunked request bodies are not supported; send Content-Length", true);
        return -1;
    }
    auto conn = r.hdr.find("connection");
    if (conn != r.hdr.end()) {
        std::string v = conn->second;
        std::transform(v.begin(), v.end(), v.begin(), [](unsigned char ch) { return (char)tolower(ch); });
        if (v.find("close") != std::string::npos) r.keep_alive = false;
        if (v.find("keep-alive") != std::string::npos) r.keep_alive = true;
    }
    size_t cl = 0;
    auto it = r.hdr.find("content-length");
    if (it != r.hdr.end()) {
        const std::string &v = it->second;
        if (v.empty() || v.size() > 12 || v.find_first_not_of("0123456789") != std::string::npos) {
            send_err(c, 400, "bad Content-Length", true);
            return -1;
        }
        unsigned long long x = strtoull(v.c_str(), nullptr, 10);
        if (x > g_cfg.max_body) {
            send_err(c, 413, "request body larger than 1 MiB", true);
            return -1;
        }
        cl = (size_t)x;
    }
    if (c.in.size() < he + 4 + cl) {
        auto ex = r.hdr.find("expect");
        if (ex != r.hdr.end() && ex->second.find("100-continue") != std::string::npos &&
            c.in.size() == he + 4 && c.out.empty())
            c.out += "HTTP/1.1 100 Continue\r\n\r\n";
        return 0;
    }
    r.body = c.in.substr(he + 4, cl);
    c.in.erase(0, he + 4 + cl);
    return 1;
}

std::string str_or_parts(const json &content, bool &ok) {
    ok = true;
    if (content.is_string()) return content.get<std::string>();
    if (content.is_null()) return "";
    if (content.is_array()) {
        std::string s;
        for (const auto &p : content) {
            if (!p.is_object() || p.value("type", std::string()) != "text" || !p.contains("text") ||
                !p["text"].is_string()) {
                ok = false;
                return "";
            }
            s += p["text"].get<std::string>();
        }
        return s;
    }
    ok = false;
    return "";
}

// Validates an OpenAI-style request (INTERFACES 11.4 subset) into j. Returns "" or an error message (400).
std::string parse_gen(const json &b, bool chat, Job &j) {
    if (!b.is_object()) return "body must be a JSON object";
    if (!b.contains("model") || !b["model"].is_string()) return "'model' (string) is required";
    j.model = b["model"].get<std::string>();
    if (!model_id_ok(j.model)) return "bad model id";
    j.chat = chat;
    if (chat) {
        if (!b.contains("messages") || !b["messages"].is_array() || b["messages"].empty())
            return "'messages' must be a non-empty array";
        if (b["messages"].size() > 512) return "too many messages";
        for (const auto &m : b["messages"]) {
            if (!m.is_object() || !m.contains("role") || !m["role"].is_string()) return "every message needs a role";
            std::string role = m["role"].get<std::string>();
            if (role.empty() || role.size() > 32) return "bad role";
            bool ok;
            std::string content = str_or_parts(m.contains("content") ? m["content"] : json(), ok);
            if (!ok) return "message content must be a string or an array of text parts";
            j.msgs.emplace_back(role, content);
        }
    } else {
        if (!b.contains("prompt")) return "'prompt' is required";
        const json &p = b["prompt"];
        if (p.is_string()) {
            j.prompt = p.get<std::string>();
        } else if (p.is_array() && p.size() == 1 && p[0].is_string()) {
            j.prompt = p[0].get<std::string>();
        } else {
            return "'prompt' must be a string";
        }
    }
    j.gp.max_tokens = g_cfg.max_tokens_default;
    if (b.contains("max_tokens") && !b["max_tokens"].is_null()) {
        if (!b["max_tokens"].is_number_integer()) return "'max_tokens' must be an integer";
        long long v = b["max_tokens"].get<long long>();
        if (v < 1 || v > g_cfg.max_tokens_cap) return "'max_tokens' must be 1..4096";
        j.gp.max_tokens = (int)v;
    }
    j.gp.temperature = 0.8f;
    if (b.contains("temperature") && !b["temperature"].is_null()) {
        if (!b["temperature"].is_number()) return "'temperature' must be a number";
        double v = b["temperature"].get<double>();
        if (!(v >= 0.0 && v <= 2.0)) return "'temperature' must be 0..2";
        j.gp.temperature = (float)v;
    }
    j.gp.top_p = 0.95f;
    if (b.contains("top_p") && !b["top_p"].is_null()) {
        if (!b["top_p"].is_number()) return "'top_p' must be a number";
        double v = b["top_p"].get<double>();
        if (!(v > 0.0 && v <= 1.0)) return "'top_p' must be in (0, 1]";
        j.gp.top_p = (float)v;
    }
    if (b.contains("stop") && !b["stop"].is_null()) {
        const json &s = b["stop"];
        std::vector<json> items;
        if (s.is_string())
            items.push_back(s);
        else if (s.is_array() && s.size() <= 4)
            for (const auto &x : s) items.push_back(x);
        else
            return "'stop' must be a string or up to 4 strings";
        for (const auto &x : items) {
            if (!x.is_string() || x.get<std::string>().empty() || x.get<std::string>().size() > 128)
                return "bad 'stop' string";
            j.gp.stop.push_back(x.get<std::string>());
        }
    }
    if (b.contains("seed") && !b["seed"].is_null()) {
        if (!b["seed"].is_number_integer()) return "'seed' must be an integer";
        long long v = b["seed"].get<long long>();
        j.gp.seed = v < 0 ? 0xFFFFFFFFu : (uint32_t)(v & 0xFFFFFFFFLL);
    }
    if (b.contains("stream") && !b["stream"].is_null()) {
        if (!b["stream"].is_boolean()) return "'stream' must be a boolean";
        j.stream = b["stream"].get<bool>();
    }
    return "";
}

const char *estate_name() {
    switch (g_estate.load()) {
    case ES_LOADING: return "loading";
    case ES_LOADED: return "loaded";
    case ES_SERVING: return "serving";
    default: return "idle";
    }
}

void route(Conn &c, Req &r) {
    std::string path = r.path.substr(0, r.path.find('?'));
    bool ka = r.keep_alive;
    if (path == "/health") {
        if (r.method != "GET") return send_err(c, 400, "use GET", !ka);
        if (!has_nouveau_node() || g_backend_failed)
            return send_err(c, 503, "no NVIDIA GPU (nouveau render node / Vulkan device) available", !ka);
        json j = {{"status", "ok"}, {"gb_state", estate_name()}};
        return send_now(c, 200, j.dump(), !ka);
    }
    if (path == "/v1/models") {
        if (r.method != "GET") return send_err(c, 400, "use GET", !ka);
        std::string loaded;
        {
            std::lock_guard<std::mutex> l(g_model_mu);
            loaded = g_loaded_model;
        }
        std::vector<std::pair<std::string, long long>> ms;
        std::string d = P("/data/vendor/gb/models");
        if (DIR *dir = opendir(d.c_str())) {
            while (struct dirent *e = readdir(dir)) {
                std::string n = e->d_name;
                if (n.size() <= 5 || n.compare(n.size() - 5, 5, ".gguf") != 0) continue;
                std::string id = n.substr(0, n.size() - 5);
                if (!model_id_ok(id)) continue;
                struct stat st;
                if (lstat((d + "/" + n).c_str(), &st) || !S_ISREG(st.st_mode)) continue;
                ms.emplace_back(id, (long long)st.st_size);
            }
            closedir(dir);
        }
        std::sort(ms.begin(), ms.end());
        json data = json::array();
        for (const auto &m : ms)
            data.push_back({{"id", m.first}, {"object", "model"}, {"owned_by", "local"},
                            {"gb_loaded", m.first == loaded && !loaded.empty()}, {"size", m.second}});
        json j = {{"object", "list"}, {"data", data}};
        return send_now(c, 200, j.dump(-1, ' ', false, json::error_handler_t::replace), !ka);
    }
    if (path == "/v1/chat/completions" || path == "/v1/completions") {
        if (r.method != "POST") return send_err(c, 400, "use POST", !ka);
        json b = json::parse(r.body, nullptr, false);
        if (b.is_discarded()) return send_err(c, 400, "body is not valid JSON", !ka);
        auto j = std::make_shared<Job>();
        std::string e = parse_gen(b, path == "/v1/chat/completions", *j);
        if (!e.empty()) return send_err(c, 400, e, !ka);
        j->rid = std::string(j->chat ? "chatcmpl-gb" : "cmpl-gb") + std::to_string(++g_rid);
        j->created = (long long)time(nullptr);
        j->deadline = now_ms() + (int64_t)g_cfg.req_timeout_s * 1000;
        {
            std::lock_guard<std::mutex> l(g_worker.qmu);
            std::string active;
            int inflight = 0;
            if (g_worker.running && g_worker.running->kind == Job::GEN && !g_worker.running->abort) {
                active = g_worker.running->model;
                inflight++;
            }
            for (const auto &q : g_worker.q) {
                if (q->abort) continue;
                if (q->kind == Job::UNLOAD) continue;
                if (active.empty()) active = q->model;
                inflight++;
            }
            if (!active.empty() && active != j->model)
                return send_err(c, 409, "model '" + active + "' is in use by another request", !ka);
            if (inflight >= 1 + g_cfg.queue_max) return send_err(c, 429, "generation queue full", !ka);
            g_worker.q.push_back(j);
        }
        g_worker.qcv.notify_one();
        c.job = j;
        c.streaming = false;
        if (!ka) c.close_after = true;
        return;
    }
    if (path == "/gb/unload") {
        if (r.method != "POST") return send_err(c, 400, "use POST", !ka);
        auto j = std::make_shared<Job>();
        j->kind = Job::UNLOAD;
        {
            std::lock_guard<std::mutex> l(g_worker.qmu);
            bool busy = g_worker.running && g_worker.running->kind == Job::GEN;
            for (const auto &q : g_worker.q)
                if (q->kind == Job::GEN && !q->abort) busy = true;
            if (busy) return send_err(c, 409, "a request is running", !ka);
            g_worker.q.push_back(j);
        }
        g_worker.qcv.notify_one();
        c.job = j;
        if (!ka) c.close_after = true;
        return;
    }
    send_err(c, 404, "no such path", !ka);
}

void drop_job(Conn &c) {
    if (!c.job) return;
    c.job->abort = true;
    {
        std::lock_guard<std::mutex> l(g_worker.qmu);
        auto &q = g_worker.q;
        q.erase(std::remove(q.begin(), q.end(), c.job), q.end());
    }
    c.job.reset();
}

void kill_conn(Conn &c) {
    if (c.dead) return;
    drop_job(c);
    if (c.fd >= 0) close(c.fd);
    c.fd = -1;
    c.dead = true;
}

void serve_requests(Conn &c) {
    while (!c.dead && !c.job && !c.close_after) {
        Req r;
        int pr = parse_req(c, r);
        if (pr <= 0) break;
        route(c, r);
        if (!c.job) c.deadline = now_ms() + (int64_t)g_cfg.hdr_timeout_s * 1000;
    }
}

void pump_job(Conn &c) {
    if (!c.job) return;
    JobP j = c.job;
    std::lock_guard<std::mutex> l(j->mu);
    if (j->stream && j->sse_started && !c.streaming) {
        c.out += "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
                 "Connection: close\r\nServer: " GB_COMPUTE_VERSION "\r\n\r\n";
        c.streaming = true;
    }
    if (c.streaming) {
        c.out += j->sse;
        j->sse.clear();
    }
    if (j->done) {
        if (c.streaming) {
            c.close_after = true;
        } else {
            int st = j->status ? j->status : 503;
            std::string body = j->status ? j->body : err_body(503, "gb_unavailable", "service stopping");
            c.out += http_resp(st, body, !c.close_after);
        }
        c.job.reset();
        c.deadline = now_ms() + (int64_t)g_cfg.hdr_timeout_s * 1000;
    }
}

void flush_out(Conn &c) {
    while (!c.out.empty() && !c.dead) {
        ssize_t w = send(c.fd, c.out.data(), c.out.size(), MSG_NOSIGNAL | MSG_DONTWAIT);
        if (w > 0) {
            c.out.erase(0, (size_t)w);
        } else if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
            break;
        } else {
            kill_conn(c);
            return;
        }
    }
    if (!c.dead && c.out.size() > g_cfg.max_out) {
        LOGW("client uid %u not reading: closed", (unsigned)c.uid);
        kill_conn(c);
        return;
    }
    if (!c.dead && c.out.empty() && c.close_after && !c.job) kill_conn(c);
}

void on_readable(Conn &c) {
    char b[16384];
    for (;;) {
        ssize_t r = recv(c.fd, b, sizeof b, MSG_DONTWAIT);
        if (r > 0) {
            if (c.reject) {  // a refused peer: only look for the end of its request header, keep nothing else
                c.in.append(b, (size_t)r);
                if (c.in.find("\r\n\r\n") != std::string::npos) c.hdr_seen = true;
                if (c.in.size() > 3) c.in.erase(0, c.in.size() - 3);
                continue;
            }
            c.in.append(b, (size_t)r);
            if (c.in.size() > g_cfg.max_body + 32768) {
                send_err(c, 413, "request too large", true);
                c.in.clear();
                break;
            }
            continue;
        }
        if (r == 0) {
            // peer closed its side: an unfinished job is abandoned (abort), queued output is dropped
            kill_conn(c);
            return;
        }
        if (errno == EAGAIN || errno == EWOULDBLOCK) break;
        if (errno == EINTR) continue;
        kill_conn(c);
        return;
    }
    if (c.reject) {
        if (c.hdr_seen && c.out.empty() && !c.close_after) {
            int st = c.reject;
            send_err(c, st, st == 403 ? "this app is not allowed to use the NVIDIA compute service" : "too many connections", true);
        }
        return;
    }
    if (!c.job) serve_requests(c);
}

int g_listen = -1;
int g_sigpipe[2] = {-1, -1};
int g_rejecting = 0;

void on_signal(int) {
    char c = 's';
    ssize_t r = write(g_sigpipe[1], &c, 1);
    (void)r;
}

void accept_all() {
    for (;;) {
        int fd = accept4(g_listen, nullptr, nullptr, SOCK_NONBLOCK | SOCK_CLOEXEC);
        if (fd < 0) {
            if (errno == EINTR) continue;
            return;
        }
        auto c = std::make_unique<Conn>();
        c->fd = fd;
        struct ucred cr;
        socklen_t cl = sizeof cr;
        if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cr, &cl) != 0 || cl != sizeof cr) {
            LOGW("SO_PEERCRED failed: %s", strerror(errno));
            c->reject = 403;
        } else {
            c->uid = cr.uid;
            c->pid = cr.pid;
            if (!g_access.allowed(cr.uid)) {
                LOGW("refused uid %u pid %d (not in the compute allowlist)", (unsigned)cr.uid, (int)cr.pid);
                c->reject = 403;
            }
        }
        size_t normal = 0;
        for (const auto &x : g_conns)
            if (!x->dead && !x->reject) normal++;
        if (!c->reject && (int)normal >= g_cfg.max_conn) {
            LOGW("refused uid %u pid %d: %d connections open", (unsigned)c->uid, (int)c->pid, (int)normal);
            c->reject = 429;
        }
        if (c->reject) {
            if (g_rejecting >= 8) {  // never let refused peers consume resources
                close(fd);
                continue;
            }
            g_rejecting++;
            c->deadline = now_ms() + 1000;  // answer after the request header, or after 1 s
        } else {
            c->deadline = now_ms() + (int64_t)g_cfg.hdr_timeout_s * 1000;
        }
        g_conns.push_back(std::move(c));
    }
}

void begin_shutdown(const char *why) {
    if (g_shutdown) return;
    g_shutdown = true;
    g_shutdown_at = now_ms();
    g_shutdown_why = why;
    LOGI("stopping (%s)", why);
    if (g_listen >= 0) close(g_listen);
    g_listen = -1;
    std::lock_guard<std::mutex> l(g_worker.qmu);
    for (auto &q : g_worker.q) q->abort = true;
    if (g_worker.running) g_worker.running->abort = true;
    // queued jobs are answered 503 by pump_job (done without status); the worker skips aborted ones
    for (auto &q : g_worker.q) {
        std::lock_guard<std::mutex> jl(q->mu);
        q->done = true;
    }
    g_worker.q.clear();
}

[[noreturn]] void finish(int code) {
    g_power.idle_if_needed();
    for (auto &c : g_conns)
        if (!c->dead && c->fd >= 0) close(c->fd);
    LOGI("exit %d (%s)", code, g_shutdown_why);
    // _exit: static destructors of the Vulkan backend are skipped on purpose (the model and context were freed
    // explicitly; the kernel releases the device file and its objects).
    _exit(code);
}

int get_listen_socket() {
    if (const char *e = getenv("ANDROID_SOCKET_gb_gpu_compute")) {
        char *end = nullptr;
        long fd = strtol(e, &end, 10);
        int type = 0, dom = 0;
        socklen_t l = sizeof type, l2 = sizeof dom;
        if (*e && !*end && fd >= 0 && fd < 65536 && getsockopt((int)fd, SOL_SOCKET, SO_TYPE, &type, &l) == 0 &&
            type == SOCK_STREAM && getsockopt((int)fd, SOL_SOCKET, SO_DOMAIN, &dom, &l2) == 0 && dom == AF_UNIX) {
            fcntl((int)fd, F_SETFD, FD_CLOEXEC);
            fcntl((int)fd, F_SETFL, fcntl((int)fd, F_GETFL) | O_NONBLOCK);
            if (listen((int)fd, 16) == 0) return (int)fd;
        }
        LOGE("ANDROID_SOCKET_gb_gpu_compute=%s is not a listening AF_UNIX stream socket", e);
        return -1;
    }
#ifdef GB_COMPUTE_TESTING
    if (!g_cfg.listen_path.empty()) {
        int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
        struct sockaddr_un a;
        memset(&a, 0, sizeof a);
        a.sun_family = AF_UNIX;
        if (fd < 0 || g_cfg.listen_path.size() >= sizeof a.sun_path) return -1;
        memcpy(a.sun_path, g_cfg.listen_path.c_str(), g_cfg.listen_path.size());
        unlink(g_cfg.listen_path.c_str());
        if (bind(fd, (struct sockaddr *)&a, sizeof a) || chmod(g_cfg.listen_path.c_str(), 0666) || listen(fd, 16)) {
            LOGE("bind %s: %s", g_cfg.listen_path.c_str(), strerror(errno));
            close(fd);
            return -1;
        }
        return fd;
    }
#endif
    LOGE("no listening socket (start through init: service gb_gpu_compute, socket gb_gpu_compute)");
    return -1;
}

void apply_limits() {
    struct rlimit r;
    r.rlim_cur = r.rlim_max = 0;
    setrlimit(RLIMIT_CORE, &r);
    r.rlim_cur = r.rlim_max = 256;
    setrlimit(RLIMIT_NOFILE, &r);
    // Private writable memory (malloc, Vulkan host allocations). Model files are mapped read-only and device memory
    // is a shared mapping of the DRM fd, so neither counts; PROT_NONE allocator reservations do not count either.
    r.rlim_cur = r.rlim_max = g_cfg.rlimit_data;
    setrlimit(RLIMIT_DATA, &r);
    prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
}

void prepare_dirs() {
    std::string cache = P("/data/vendor/gb/compute/cache");
    if (mkdir(cache.c_str(), 0700) != 0 && errno != EEXIST) LOGW("mkdir %s: %s", cache.c_str(), strerror(errno));
    // init sets these (gb_gpu_compute.rc); keep the service correct when started another way
    setenv("MESA_SHADER_CACHE_DIR", cache.c_str(), 0);
    setenv("MESA_SHADER_CACHE_DISABLE", "false", 0);
}

int probe_main() {
    gb::Engine e;
    std::string d, b;
    bool ok = e.init_backend(d);
    printf("PROBE %s %s\n", ok ? "device" : "no-device", d.c_str());
    int comp = -1;
    if (ok) {
        ok = e.probe_backend(b);
        printf("PROBE %s\n", b.c_str());
    }
    if (ok) {
        comp = e.probe_compute(b);
        printf("PROBE %s\n", b.c_str());
    }
    fflush(stdout);
    // 0: device + backend + correct result; 5: device + backend, wrong result (expected under the noop drm-shim);
    // 3: no device; 1: compute error
    _exit(!ok ? EXIT_NO_DEVICE : comp == 1 ? EXIT_OK : comp == 0 ? 5 : EXIT_ERR);
}

}  // namespace

extern "C" __attribute__((visibility("default"))) int gb_gpu_compute_main(int argc, char **argv) {
    signal(SIGPIPE, SIG_IGN);
    load_config();
    if (argc > 1 && !strcmp(argv[1], "--version")) {
        printf("%s (llama.cpp %s)\n", GB_COMPUTE_VERSION, GB_LLAMA_TAG);
        return EXIT_OK;
    }
    apply_limits();
    prepare_dirs();
    if (argc > 1 && !strcmp(argv[1], "--probe")) return probe_main();
    if (argc > 1) {
        LOGE("unknown argument %s", argv[1]);
        return EXIT_CONFIG;
    }
    if (pipe2(g_wake, O_NONBLOCK | O_CLOEXEC) || pipe2(g_sigpipe, O_NONBLOCK | O_CLOEXEC)) return EXIT_ERR;
    g_listen = get_listen_socket();
    if (g_listen < 0) return EXIT_CONFIG;
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_signal;
    sigaction(SIGTERM, &sa, nullptr);
    sigaction(SIGINT, &sa, nullptr);
    LOGI("%s (llama.cpp %s) listening; n_ctx %d, %d threads, idle exit %d s", GB_COMPUTE_VERSION, GB_LLAMA_TAG,
         g_cfg.n_ctx, g_cfg.n_threads, g_cfg.idle_exit_s);
    if (!g_power.set_state("idle")) LOGW("gb_power not reachable yet (models need its power hold)");
    g_worker.start();

    int64_t idle_since = now_ms(), next_screen = 0, off_since = 0, hold_lost_since = 0, next_maint = 0;
    bool unload_posted = false;
    for (;;) {
        int64_t t = now_ms();
        std::vector<struct pollfd> pf;
        pf.push_back({g_sigpipe[0], POLLIN, 0});
        pf.push_back({g_wake[0], POLLIN, 0});
        int li = -1, pi = -1;
        if (g_listen >= 0) {
            li = (int)pf.size();
            pf.push_back({g_listen, POLLIN, 0});
        }
        int pfd = g_power.busy() ? -1 : g_power.fd();
        if (pfd >= 0) {
            pi = (int)pf.size();
            pf.push_back({pfd, POLLIN | POLLRDHUP, 0});
        }
        size_t base = pf.size();
        std::vector<Conn *> pc;
        for (auto &c : g_conns) {
            if (c->dead) continue;
            short ev = POLLIN | POLLRDHUP;
            if (!c->out.empty()) ev |= POLLOUT;
            pf.push_back({c->fd, ev, 0});
            pc.push_back(c.get());
        }
        int timeout = 1000;
        for (Conn *c : pc)
            if (c->deadline && !c->job) timeout = (int)std::max<int64_t>(0, std::min<int64_t>(timeout, c->deadline - t));
        int n = poll(pf.data(), pf.size(), timeout);
        if (n < 0 && errno != EINTR) {
            LOGE("poll: %s", strerror(errno));
            begin_shutdown("poll error");
        }
        t = now_ms();
        char buf[256];
        if (pf[0].revents) {
            while (read(g_sigpipe[0], buf, sizeof buf) > 0) {
            }
            begin_shutdown("SIGTERM");
        }
        if (pf[1].revents)
            while (read(g_wake[0], buf, sizeof buf) > 0) {
            }
        if (li >= 0 && g_listen >= 0 && (pf[(size_t)li].revents & POLLIN)) accept_all();
        if (pi >= 0 && pf[(size_t)pi].revents) g_power.on_readable();
        for (size_t i = 0; i < pc.size(); i++) {
            Conn &c = *pc[i];
            short re = pf[base + i].revents;
            if (c.dead) continue;
            if (re & (POLLIN | POLLRDHUP | POLLHUP | POLLERR)) on_readable(c);
        }
        for (auto &c : g_conns) {
            if (c->dead) continue;
            pump_job(*c);
            if (!c->job && !c->reject && !c->close_after) serve_requests(*c);
            flush_out(*c);
            if (c->dead) continue;
            if (c->deadline && !c->job && t >= c->deadline && c->out.empty()) {
                if (c->reject && !c->close_after) {
                    send_err(*c, c->reject, c->reject == 403 ? "not allowed" : "too many connections", true);
                    flush_out(*c);
                } else {
                    kill_conn(*c);  // receive / keep-alive timeout
                }
            }
        }
        for (auto it = g_conns.begin(); it != g_conns.end();) {
            if ((*it)->dead) {
                if ((*it)->reject) g_rejecting--;
                it = g_conns.erase(it);
            } else {
                ++it;
            }
        }

        // ---- gb_power: reconnect while a hold is wanted; unload when the hold stays lost (no VRAM without hold)
        if (t >= next_maint) {
            next_maint = t + 2000;
            g_power.maintain();
        }
        int es = g_estate.load();
        bool model_in_use = es == ES_LOADED || es == ES_SERVING;
        if (model_in_use && !g_power.hold()) {
            if (!hold_lost_since) hold_lost_since = t;
            if (t - hold_lost_since >= g_cfg.hold_lost_unload_ms && g_worker.idle() && !g_shutdown) {
                LOGW("dGPU power hold lost for %d ms: unloading the model", g_cfg.hold_lost_unload_ms);
                auto j = std::make_shared<Job>();
                j->kind = Job::UNLOAD;
                {
                    std::lock_guard<std::mutex> l(g_worker.qmu);
                    g_worker.q.push_back(j);
                }
                g_worker.qcv.notify_one();
                hold_lost_since = 0;
            }
        } else {
            hold_lost_since = 0;
        }

        // ---- screen off >= 30 s (and no request for 30 s): unload, keep running until the idle exit
        if (t >= next_screen) {
            next_screen = t + g_cfg.screen_poll_ms;
            int s = screen_state();
            if (s == 1) {
                if (!off_since) off_since = t;
                int64_t quiet = t - g_last_job_end.load();
                if (t - off_since >= (int64_t)g_cfg.screen_off_s * 1000 &&
                    quiet >= (int64_t)g_cfg.screen_off_s * 1000 && g_estate.load() == ES_LOADED && g_worker.idle() &&
                    !g_shutdown) {
                    LOGI("screen off for %lld s: unloading the model", (long long)((t - off_since) / 1000));
                    auto j = std::make_shared<Job>();
                    j->kind = Job::UNLOAD;
                    {
                        std::lock_guard<std::mutex> l(g_worker.qmu);
                        g_worker.q.push_back(j);
                    }
                    g_worker.qcv.notify_one();
                }
            } else {
                off_since = 0;
            }
        }

        // ---- idle exit: no request in flight, nothing queued, no client connection
        bool busy = !g_worker.idle();
        size_t live = 0;
        for (auto &c : g_conns)
            if (!c->dead && !c->reject) live++;
        if (busy || live) {
            idle_since = 0;
        } else if (!idle_since) {
            idle_since = t;
        } else if (!g_shutdown && t - idle_since >= (int64_t)g_cfg.idle_exit_s * 1000) {
            begin_shutdown("idle");
        }

        // ---- shutdown: abort, unload, idle, exit (hard limit 4.5 s after the request)
        if (g_shutdown) {
            if (t - g_shutdown_at > 4500) {
                LOGW("shutdown deadline reached");
                _exit(EXIT_OK);
            }
            if (g_worker.idle()) {
                int st = g_estate.load();
                if ((st == ES_LOADED || st == ES_SERVING || st == ES_LOADING) && !unload_posted) {
                    auto j = std::make_shared<Job>();
                    j->kind = Job::UNLOAD;
                    {
                        std::lock_guard<std::mutex> l(g_worker.qmu);
                        g_worker.q.push_back(j);
                    }
                    g_worker.qcv.notify_one();
                    unload_posted = true;
                } else if (st == ES_IDLE || unload_posted) {
                    bool pending_out = false;
                    for (auto &c : g_conns)
                        if (!c->dead && !c->out.empty()) pending_out = true;
                    if (!pending_out || t - g_shutdown_at > 1000) finish(EXIT_OK);
                }
            }
        }
    }
}
