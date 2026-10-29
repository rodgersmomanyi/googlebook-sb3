// SPDX-License-Identifier: Apache-2.0
//
// Host unit tests of gb_gatekeeper: the service code (aidl_service.cpp, gb_gatekeeper.cpp,
// gk_store.cpp, shared_secret.cpp, libgatekeeper from AOSP) linked against the fake parcel.
//   gk_host_tests VECTORS.json TMPDIR SERVICE [ARG...]
// writes the computed handle / auth token / shared-secret values to VECTORS.json, which
// ref_check.py recomputes independently (hashlib.scrypt, hmac, cryptography's AES-CMAC).
// SERVICE [ARG...] runs the service binary built with the fake service manager
// (tests/fake_sm.cpp: gk_host_service, or "linker64 gk_android_service_fake" on lapis bionic)
// for test_registration.
//
// The W4 tests (test_throttling's reboot sequence, test_reboot_loop_no_bypass,
// test_crypto_failure_not_counted, test_registration) also compile against the pre-W4 sources
// (tests/regress_old.sh), where they must fail; that is why the test hooks below use no API that
// only the fixed code has.
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>  // AOSP password_handle.h relies on its includer for <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <memory>
#include <string>
#include <vector>

#include <gatekeeper/password_handle.h>
#include <openssl/hmac.h>

#include "../src/aidl_service.h"
#include "../src/gb_gatekeeper.h"
#include "../src/gk_log.h"
#include "../src/gk_store.h"
#include "../src/shared_secret.h"
#include "fake_binder.h"

using namespace gbgk;
using Bytes = std::vector<uint8_t>;

static int g_pass = 0, g_fail = 0;
static const char* g_test = "";
#define CHECK(cond)                                                                   \
    do {                                                                              \
        if (cond) {                                                                   \
            g_pass++;                                                                 \
        } else {                                                                      \
            g_fail++;                                                                 \
            fprintf(stderr, "FAIL [%s] %s:%d: %s\n", g_test, __FILE__, __LINE__, #cond); \
        }                                                                             \
    } while (0)

static uint64_t g_now = 1000;
static uint64_t fake_clock() { return g_now; }

// The "kernel" boot_id the test hooks report; empty = it cannot be read.
static const char kBootA[] = "0b6a2f1c-5d3e-4c7a-9e21-6f4d8a1b2c3d";
static const char kBootB[] = "d4e5f6a7-b8c9-4d0e-8f12-3a4b5c6d7e8f";
static std::string g_boot_id = kBootA;

static std::vector<std::string> g_service_cmd;  // argv[3...]

static std::string g_tmp = "/tmp";

static std::string make_dir() {
    std::string t = g_tmp + "/gbgk_test.XXXXXX";
    std::vector<char> buf(t.begin(), t.end());
    buf.push_back(0);
    char* d = mkdtemp(buf.data());
    if (d == nullptr) {
        perror("mkdtemp");
        exit(2);
    }
    chmod(d, 0700);
    return d;
}

static void rm_dir(const std::string& d) {
    DIR* dir = opendir(d.c_str());
    if (dir) {
        while (struct dirent* e = readdir(dir)) {
            if (strcmp(e->d_name, ".") && strcmp(e->d_name, "..")) unlink((d + "/" + e->d_name).c_str());
        }
        closedir(dir);
    }
    rmdir(d.c_str());
}

static bool file_exists(const std::string& p) { return access(p.c_str(), F_OK) == 0; }

static Bytes read_file(const std::string& p) {
    Bytes v;
    FILE* f = fopen(p.c_str(), "rb");
    if (!f) return v;
    uint8_t b[256];
    size_t n;
    while ((n = fread(b, 1, sizeof(b), f)) > 0) v.insert(v.end(), b, b + n);
    fclose(f);
    return v;
}

static void write_file(const std::string& p, const Bytes& v) {
    FILE* f = fopen(p.c_str(), "wb");
    if (!f) return;
    if (!v.empty()) fwrite(v.data(), 1, v.size(), f);
    fclose(f);
}

static std::string hex(const uint8_t* p, size_t n) {
    static const char* d = "0123456789abcdef";
    std::string s;
    for (size_t i = 0; i < n; i++) {
        s.push_back(d[p[i] >> 4]);
        s.push_back(d[p[i] & 15]);
    }
    return s;
}
static std::string hex(const Bytes& v) { return hex(v.data(), v.size()); }
static Bytes str_bytes(const char* s) { return Bytes(s, s + strlen(s)); }
static Bytes line_bytes(const char* s) {  // the boot_id file: the id and a newline
    Bytes b = str_bytes(s);
    b.push_back('\n');
    return b;
}

// Test hooks on the production class. Deliberately without `override`: on the pre-W4 sources
// (tests/regress_old.sh) CurrentBootId() does not exist in the base class, so there it is an
// unused method and the old behaviour is measured; here it replaces the kernel boot_id.
class TestGk : public GbGateKeeper {
  public:
    TestGk(GkStore* store, SoftSharedSecret* ss) : GbGateKeeper(store, ss, fake_clock) {}
    // The password signature computation fails, through the production failure path of
    // GbGateKeeper::ComputePasswordSignature (as when scrypt cannot allocate its 16 MiB).
    bool fail_crypto = false;

  protected:
    bool CurrentBootId(std::string* out) const {
        if (g_boot_id.empty()) return false;
        *out = g_boot_id;
        return true;
    }
    void ComputePasswordSignature(uint8_t* signature, uint32_t signature_length,
                                  const uint8_t* key, uint32_t key_length,
                                  const uint8_t* password, uint32_t password_length,
                                  ::gatekeeper::salt_t salt) const {
        GbGateKeeper::ComputePasswordSignature(signature, signature_length,
                                               fail_crypto ? nullptr : key,
                                               fail_crypto ? 0 : key_length, password,
                                               password_length, salt);
    }
};

struct Env {
    std::string dir;
    GkStore store;
    SoftSharedSecret ss;
    TestGk* hooks;  // null with kernel_boot_id: the unmodified production class
    std::unique_ptr<GbGateKeeper> gk;
    GatekeeperService svc;
    explicit Env(const std::string& d, bool selftest_ok = true, bool kernel_boot_id = false)
        : dir(d), store(d, /*refuse_volatile_fs=*/false),
          hooks(kernel_boot_id ? nullptr : new TestGk(&store, &ss)),
          gk(hooks ? static_cast<GbGateKeeper*>(hooks)
                   : new GbGateKeeper(&store, &ss, fake_clock)),
          svc(gk.get(), &ss, selftest_ok) {}
};

static void put_bytes(AParcel* in, const Bytes& v) {
    static const int8_t none = 0;
    AParcel_writeByteArray(in, v.empty() ? &none : reinterpret_cast<const int8_t*>(v.data()),
                           static_cast<int32_t>(v.size()));
}

struct EnrollResult {
    binder_status_t st = 0;
    int32_t ex = 0, code = 0, status = 0, timeout = 0;
    int64_t sid = 0;
    Bytes handle;
    bool parsed = false;
};

static EnrollResult enroll(Env& e, int32_t uid, const Bytes& cur_handle, const Bytes& cur_pw,
                           const Bytes& desired, uid_t caller = kAidSystem) {
    AParcel* in = fake::NewParcel();
    AParcel_writeInt32(in, uid);
    put_bytes(in, cur_handle);
    put_bytes(in, cur_pw);
    put_bytes(in, desired);
    fake::Rewind(in);
    AParcel* out = fake::NewParcel();
    EnrollResult r;
    r.st = e.svc.OnGatekeeperTransact(TX_GK_ENROLL, in, out, caller);
    fake::Rewind(out);
    fake::Reader rd{out};
    r.ex = rd.Status(&r.code);
    if (r.st == GB_STATUS_OK && r.ex == GB_EX_NONE) {
        size_t end = rd.BeginParcelable();
        r.status = rd.I32();
        r.timeout = rd.I32();
        r.sid = rd.I64();
        r.handle = rd.Bytes();
        rd.EndParcelable(end);
        r.parsed = rd.ok && out->pos == out->data.size();
    }
    fake::DeleteParcel(in);
    fake::DeleteParcel(out);
    return r;
}

struct VerifyResult {
    binder_status_t st = 0;
    int32_t ex = 0, code = 0, status = 0, timeout = 0;
    int64_t challenge = 0, user_id = 0, auth_id = 0, ts = 0;
    int32_t type = 0;
    Bytes mac;
    bool parsed = false;
};

static VerifyResult verify(Env& e, int32_t uid, int64_t challenge, const Bytes& handle,
                           const Bytes& pw, uid_t caller = kAidSystem) {
    AParcel* in = fake::NewParcel();
    AParcel_writeInt32(in, uid);
    AParcel_writeInt64(in, challenge);
    put_bytes(in, handle);
    put_bytes(in, pw);
    fake::Rewind(in);
    AParcel* out = fake::NewParcel();
    VerifyResult r;
    r.st = e.svc.OnGatekeeperTransact(TX_GK_VERIFY, in, out, caller);
    fake::Rewind(out);
    fake::Reader rd{out};
    r.ex = rd.Status(&r.code);
    if (r.st == GB_STATUS_OK && r.ex == GB_EX_NONE) {
        size_t end = rd.BeginParcelable();
        r.status = rd.I32();
        r.timeout = rd.I32();
        size_t hat_end = rd.BeginParcelable();
        r.challenge = rd.I64();
        r.user_id = rd.I64();
        r.auth_id = rd.I64();
        r.type = rd.I32();
        size_t ts_end = rd.BeginParcelable();
        r.ts = rd.I64();
        rd.EndParcelable(ts_end);
        r.mac = rd.Bytes();
        rd.EndParcelable(hat_end);
        rd.EndParcelable(end);
        r.parsed = rd.ok && out->pos == out->data.size();
    }
    fake::DeleteParcel(in);
    fake::DeleteParcel(out);
    return r;
}

static binder_status_t simple_call(Env& e, transaction_code_t code, int32_t* ex, int32_t uid_arg,
                                   uid_t caller = kAidSystem, AParcel** keep_out = nullptr) {
    AParcel* in = fake::NewParcel();
    if (code == TX_GK_DELETE_USER) AParcel_writeInt32(in, uid_arg);
    fake::Rewind(in);
    AParcel* out = fake::NewParcel();
    binder_status_t st = e.svc.OnGatekeeperTransact(code, in, out, caller);
    fake::Rewind(out);
    fake::Reader rd{out};
    *ex = rd.Status(nullptr);
    fake::DeleteParcel(in);
    if (keep_out) {
        fake::Rewind(out);
        *keep_out = out;
    } else {
        fake::DeleteParcel(out);
    }
    return st;
}

// The 37 bytes of hw_auth_token_t that the MAC covers, rebuilt from the AIDL fields.
static Bytes token_bytes(const VerifyResult& v) {
    Bytes b;
    b.push_back(0);
    auto le = [&b](uint64_t x) { for (int i = 0; i < 8; i++) b.push_back(x >> (8 * i)); };
    auto be32 = [&b](uint32_t x) { for (int i = 3; i >= 0; i--) b.push_back(x >> (8 * i)); };
    auto be64 = [&b](uint64_t x) { for (int i = 7; i >= 0; i--) b.push_back(x >> (8 * i)); };
    le(v.challenge);
    le(v.user_id);
    le(v.auth_id);
    be32(static_cast<uint32_t>(v.type));
    be64(static_cast<uint64_t>(v.ts));
    return b;
}

static Bytes hmac256(const uint8_t* key, size_t klen, const Bytes& msg) {
    Bytes out(32);
    unsigned int len = 0;
    HMAC(EVP_sha256(), key, klen, msg.data(), msg.size(), out.data(), &len);
    return out;
}

static Bytes key_from_file(const std::string& dir) {
    Bytes f = read_file(dir + "/" + GkStore::kKeyName);
    if (f.size() != 72) return Bytes();
    return Bytes(f.begin() + 8, f.begin() + 40);
}

static std::string g_json;
static void json_add(const std::string& kv) {
    if (!g_json.empty()) g_json += ",\n";
    g_json += kv;
}

static void test_selftest() { CHECK(GbGateKeeper::SelfTest()); }

static void test_store_key() {
    std::string d = make_dir();
    uint8_t k1[32], k2[32], k3[32];
    {
        GkStore s(d, false);
        CHECK(s.LoadKey(k1, false) == GkStore::KeyStatus::ABSENT);
        CHECK(s.LoadKey(k1, true) == GkStore::KeyStatus::OK);
    }
    struct stat st;
    CHECK(stat((d + "/gk_key.v1").c_str(), &st) == 0 && (st.st_mode & 0777) == 0600 &&
          st.st_size == 72);
    CHECK(file_exists(d + "/gk_key.v1.bak"));
    CHECK(read_file(d + "/gk_key.v1") == read_file(d + "/gk_key.v1.bak"));
    {
        GkStore s(d, false);  // a "reboot": same key
        CHECK(s.LoadKey(k2, false) == GkStore::KeyStatus::OK && memcmp(k1, k2, 32) == 0);
    }
    // damaged primary -> restored from the backup, same key
    Bytes f = read_file(d + "/gk_key.v1");
    f[20] ^= 1;
    write_file(d + "/gk_key.v1", f);
    {
        GkStore s(d, false);
        CHECK(s.LoadKey(k2, true) == GkStore::KeyStatus::OK && memcmp(k1, k2, 32) == 0);
    }
    CHECK(read_file(d + "/gk_key.v1") == read_file(d + "/gk_key.v1.bak"));
    // missing backup -> rewritten
    unlink((d + "/gk_key.v1.bak").c_str());
    {
        GkStore s(d, false);
        CHECK(s.LoadKey(k2, false) == GkStore::KeyStatus::OK && memcmp(k1, k2, 32) == 0);
    }
    CHECK(file_exists(d + "/gk_key.v1.bak"));
    // missing primary -> restored
    unlink((d + "/gk_key.v1").c_str());
    {
        GkStore s(d, false);
        CHECK(s.LoadKey(k2, false) == GkStore::KeyStatus::OK && memcmp(k1, k2, 32) == 0);
    }
    // both damaged -> ERROR, and no new key even with create_if_absent
    Bytes good = read_file(d + "/gk_key.v1");
    Bytes bad = good;
    bad[60] ^= 0x80;
    write_file(d + "/gk_key.v1", bad);
    write_file(d + "/gk_key.v1.bak", bad);
    {
        GkStore s(d, false);
        CHECK(s.LoadKey(k3, true) == GkStore::KeyStatus::ERROR);
    }
    CHECK(read_file(d + "/gk_key.v1") == bad);  // left untouched for forensics
    // one damaged, the other absent -> ERROR (never regenerate over a damaged key)
    unlink((d + "/gk_key.v1.bak").c_str());
    {
        GkStore s(d, false);
        CHECK(s.LoadKey(k3, true) == GkStore::KeyStatus::ERROR);
    }
    // both absent -> a new, different key
    unlink((d + "/gk_key.v1").c_str());
    {
        GkStore s(d, false);
        CHECK(s.LoadKey(k3, true) == GkStore::KeyStatus::OK && memcmp(k1, k3, 32) != 0);
    }
    // a missing directory is an error, and nothing creates it
    std::string gone = d + "/nope";
    {
        GkStore s(gone, false);
        CHECK(s.LoadKey(k3, true) == GkStore::KeyStatus::ERROR);
    }
    CHECK(!file_exists(gone));
    rm_dir(d);
}

static void test_store_volatile() {
    struct statfs sfs;
    if (statfs("/dev/shm", &sfs) != 0 || sfs.f_type != 0x01021994) {
        fprintf(stderr, "skip: /dev/shm is not tmpfs\n");
        return;
    }
    std::string save = g_tmp;
    g_tmp = "/dev/shm";
    std::string d = make_dir();
    g_tmp = save;
    uint8_t k[32];
    GkStore s(d, /*refuse_volatile_fs=*/true);
    CHECK(s.LoadKey(k, true) == GkStore::KeyStatus::ERROR);
    CHECK(!file_exists(d + "/gk_key.v1"));
    rm_dir(d);
}

static void test_store_records() {
    std::string d = make_dir();
    GkStore s(d, false);
    FailureRecord r, q;
    CHECK(s.ReadRecord(7, &q) == GkStore::RecordStatus::ABSENT && q.failure_counter == 0);
    r.secure_user_id = 0x1122334455667788ULL;
    r.last_checked_timestamp = 99;
    r.failure_counter = 6;
    CHECK(s.WriteRecord(7, r));
    CHECK(s.ReadRecord(7, &q) == GkStore::RecordStatus::OK && q.failure_counter == 6 &&
          q.secure_user_id == r.secure_user_id && q.last_checked_timestamp == 99);
    Bytes f = read_file(d + "/fr.7");
    CHECK(f.size() == 64);
    f[13] ^= 1;
    write_file(d + "/fr.7", f);
    CHECK(s.ReadRecord(7, &q) == GkStore::RecordStatus::CORRUPT && q.failure_counter == 0);
    // a record copied to another uid's name is rejected
    s.WriteRecord(8, r);
    write_file(d + "/fr.9", read_file(d + "/fr.8"));
    CHECK(s.ReadRecord(9, &q) == GkStore::RecordStatus::CORRUPT);
    CHECK(s.RemoveRecord(8) && !file_exists(d + "/fr.8"));
    CHECK(s.RemoveAllRecords() && !file_exists(d + "/fr.7") && !file_exists(d + "/fr.9"));
    rm_dir(d);
}

static void test_enroll_verify() {
    std::string d = make_dir();
    Env e(d, /*selftest_ok=*/true, /*kernel_boot_id=*/true);  // the production boot_id source
    g_now = 5000;
    const Bytes pw = str_bytes("1234");
    // verify before anything was ever enrolled: no key -> error, nothing created
    Bytes fake_handle(sizeof(gatekeeper::password_handle_t), 0);
    fake_handle[0] = 2;
    VerifyResult v0 = verify(e, 100000, 0, fake_handle, pw);
    CHECK(v0.st == GB_STATUS_OK && v0.ex == GB_EX_SERVICE_SPECIFIC && v0.code == GK_ERROR_GENERAL_FAILURE);
    CHECK(!file_exists(d + "/gk_key.v1"));

    EnrollResult r = enroll(e, 100000, {}, {}, pw);
    CHECK(r.st == GB_STATUS_OK && r.ex == GB_EX_NONE && r.parsed);
    CHECK(r.status == GK_STATUS_OK && r.timeout == 0 && r.sid != 0);
    CHECK(r.handle.size() == sizeof(gatekeeper::password_handle_t));
    gatekeeper::password_handle_t h;
    memcpy(&h, r.handle.data(), sizeof(h));
    CHECK(h.version == 2 && h.flags == HANDLE_FLAG_THROTTLE_SECURE && !h.hardware_backed &&
          static_cast<int64_t>(h.user_id) == r.sid);
    CHECK(file_exists(d + "/gk_key.v1"));
    // W4-GK-1: the first record load stored the kernel's boot_id, byte for byte
    Bytes kernel_boot_id = read_file("/proc/sys/kernel/random/boot_id");
    CHECK(kernel_boot_id.size() == 37 && kernel_boot_id.back() == '\n');
    CHECK(read_file(d + "/boot_id") == kernel_boot_id);
    Bytes key = key_from_file(d);
    CHECK(key.size() == 32);
    json_add("\"handle\": {\"key\": \"" + hex(key) + "\", \"password\": \"" + hex(pw) +
             "\", \"handle\": \"" + hex(r.handle) + "\"}");

    g_now = 6000;
    const int64_t challenge = 0x1122334455667788LL;
    VerifyResult v = verify(e, 100000, challenge, r.handle, pw);
    CHECK(v.st == GB_STATUS_OK && v.ex == GB_EX_NONE && v.parsed);
    CHECK(v.status == GK_STATUS_OK && v.timeout == 0);
    CHECK(v.challenge == challenge && v.user_id == r.sid && v.auth_id == 0 && v.type == 1 &&
          v.ts == 6000 && v.mac.size() == 32);
    static const uint8_t zero_key[32] = {};
    CHECK(v.mac == hmac256(zero_key, 32, token_bytes(v)));  // not negotiated yet: zero key
    char buf[512];
    snprintf(buf, sizeof(buf),
             "\"token_zero\": {\"key\": \"%s\", \"challenge\": %lld, \"user_id\": %lld, "
             "\"authenticator_id\": %lld, \"type\": %d, \"timestamp\": %lld, \"mac\": \"%s\"}",
             hex(zero_key, 32).c_str(), (long long)v.challenge, (long long)v.user_id,
             (long long)v.auth_id, v.type, (long long)v.ts, hex(v.mac).c_str());
    json_add(buf);

    // wrong password: service-specific error, no token
    VerifyResult w = verify(e, 100000, 0, r.handle, str_bytes("1235"));
    CHECK(w.st == GB_STATUS_OK && w.ex == GB_EX_SERVICE_SPECIFIC && w.code == GK_ERROR_GENERAL_FAILURE);
    // a handle from another key never verifies: flip one signature byte
    Bytes tampered = r.handle;
    tampered[30] ^= 1;
    VerifyResult t = verify(e, 100000, 0, tampered, pw);
    CHECK(t.ex == GB_EX_SERVICE_SPECIFIC);
    // wrong handle length
    VerifyResult l = verify(e, 100000, 0, Bytes(57, 1), pw);
    CHECK(l.ex == GB_EX_SERVICE_SPECIFIC && l.code == GK_ERROR_GENERAL_FAILURE);
    // empty desired password
    EnrollResult z = enroll(e, 100000, {}, {}, {});
    CHECK(z.ex == GB_EX_SERVICE_SPECIFIC && z.code == GK_ERROR_GENERAL_FAILURE);

    // the same handle still verifies after a "reboot" (new process state, same /data)
    {
        Env e2(d);
        g_now = 100;
        VerifyResult v2 = verify(e2, 100000, 0, r.handle, pw);
        CHECK(v2.ex == GB_EX_NONE && v2.status == GK_STATUS_OK && v2.user_id == r.sid);
    }
    rm_dir(d);
}

// The verify result is a throttling answer with ms left (prints what came back otherwise).
static bool is_retry(const VerifyResult& v, int32_t ms) {
    if (v.ex == GB_EX_NONE && v.status == GK_ERROR_RETRY_TIMEOUT && v.timeout == ms) return true;
    fprintf(stderr, "  [%s] expected RETRY %d ms, got ex=%d code=%d status=%d timeout=%d\n",
            g_test, ms, v.ex, v.code, v.status, v.timeout);
    return false;
}
static bool is_ok(const VerifyResult& v) {
    if (v.ex == GB_EX_NONE && v.status == GK_STATUS_OK) return true;
    fprintf(stderr, "  [%s] expected OK, got ex=%d code=%d status=%d timeout=%d\n", g_test, v.ex,
            v.code, v.status, v.timeout);
    return false;
}
// The record on disk has this counter and timestamp (prints what is there otherwise).
static bool record_is(const std::string& dir, uint32_t uid, uint32_t counter, uint64_t ts) {
    GkStore probe(dir, false);
    FailureRecord fr;
    GkStore::RecordStatus st = probe.ReadRecord(uid, &fr);
    if (st == GkStore::RecordStatus::OK && fr.failure_counter == counter &&
        fr.last_checked_timestamp == ts) {
        return true;
    }
    fprintf(stderr, "  [%s] expected fr.%u counter %u timestamp %llu, got status %d counter %u "
            "timestamp %llu\n", g_test, uid, counter, (unsigned long long)ts, static_cast<int>(st),
            fr.failure_counter, (unsigned long long)fr.last_checked_timestamp);
    return false;
}

static void test_throttling() {
    std::string d = make_dir();
    const Bytes pw = str_bytes("2580"), bad = str_bytes("0000");
    Bytes handle;
    int64_t sid;
    uint64_t t5 = 0;
    g_boot_id = kBootA;
    {
        Env e(d);
        g_now = 10000;
        EnrollResult r = enroll(e, 100000, {}, {}, pw);
        CHECK(r.status == GK_STATUS_OK);
        handle = r.handle;
        sid = r.sid;
        CHECK(read_file(d + "/boot_id") == line_bytes(kBootA));  // stored at the first record load
        for (int i = 1; i <= 4; i++) {
            g_now += 10;
            VerifyResult w = verify(e, 100000, 0, handle, bad);
            CHECK(w.ex == GB_EX_SERVICE_SPECIFIC);  // failures 1-4: no timeout yet
        }
        g_now += 10;
        t5 = g_now;
        CHECK(is_retry(verify(e, 100000, 0, handle, bad), 60000));
        g_now += 1000;
        CHECK(is_retry(verify(e, 100000, 0, handle, pw), 59000));  // right PIN, still locked out
        FailureRecord fr;
        GkStore probe(d, false);
        CHECK(probe.ReadRecord(100000, &fr) == GkStore::RecordStatus::OK &&
              fr.failure_counter == 5 && fr.secure_user_id == static_cast<uint64_t>(sid));
        CHECK(record_is(d, 100000, 5, t5));
    }
    {
        // a restart of the service within the same boot keeps the timer (crash-proof throttling)
        Env e(d);
        g_now = t5 + 3000;
        CHECK(is_retry(verify(e, 100000, 0, handle, pw), 57000));
        CHECK(record_is(d, 100000, 5, t5));
    }
    {
        // W4-GK-1: reboot during the lock-out (a new kernel boot_id), the first attempt 50 ms
        // after boot. The HAL timer now runs from boot, like LockSettingsService's: 59,950 ms
        // left (the pre-W4 code restarted the full 60,000 ms here), counter kept.
        g_boot_id = kBootB;
        Env e(d);
        g_now = 50;
        CHECK(is_retry(verify(e, 100000, 0, handle, pw), 59950));
        CHECK(record_is(d, 100000, 5, 0));
        CHECK(read_file(d + "/boot_id") == line_bytes(kBootB));
    }
    {
        // a restart of the service later in that boot neither repeats nor undoes the reset
        Env e(d);
        g_now = 30000;
        CHECK(is_retry(verify(e, 100000, 0, handle, pw), 30000));
        // the attempt LockSettingsService lets through after its own 60 s from boot reaches
        // DoVerify: the right PIN unlocks at once and clears the record
        g_now = 60001;
        CHECK(is_ok(verify(e, 100000, 0, handle, pw)));
        CHECK(record_is(d, 100000, 0, 0));
        // escalation needs real wrong guesses: five more give 60 s, the sixth after it 5 min
        for (int i = 1; i <= 5; i++) {
            g_now += 10;
            verify(e, 100000, 0, handle, bad);
        }
        g_now += 60001;
        CHECK(is_retry(verify(e, 100000, 0, handle, bad), 300000));
        CHECK(record_is(d, 100000, 6, g_now));
    }
    {
        // a missing boot_id file counts as a new boot (same kernel boot_id, nothing proves it)
        unlink((d + "/boot_id").c_str());
        Env e(d);
        g_now = 1000;
        CHECK(is_retry(verify(e, 100000, 0, handle, pw), 299000));
        CHECK(record_is(d, 100000, 6, 0));
        CHECK(read_file(d + "/boot_id") == line_bytes(kBootB));
        // after the 5 min from boot a wrong guess reaches DoVerify and counts: 15 min
        g_now = 300001;
        CHECK(is_retry(verify(e, 100000, 0, handle, bad), 900000));
        CHECK(record_is(d, 100000, 7, 300001));
    }
    {
        // a damaged boot_id file counts as a new boot too
        Bytes b = read_file(d + "/boot_id");
        if (b.size() > 3) b[3] ^= 1;
        write_file(d + "/boot_id", b);
        Env e(d);
        g_now = 2000;
        CHECK(is_retry(verify(e, 100000, 0, handle, pw), 898000));
        CHECK(record_is(d, 100000, 7, 0));
        CHECK(read_file(d + "/boot_id") == line_bytes(kBootB));
        g_now = 900001;
        CHECK(is_retry(verify(e, 100000, 0, handle, bad), 1800000));
        CHECK(record_is(d, 100000, 8, 900001));
    }
    {
        // the kernel boot_id cannot be read: also treated as a new boot, nothing stored
        g_boot_id.clear();
        Env e(d);
        g_now = 5000;
        CHECK(is_retry(verify(e, 100000, 0, handle, pw), 1795000));
        CHECK(record_is(d, 100000, 8, 0));
        CHECK(read_file(d + "/boot_id") == line_bytes(kBootB));
        g_boot_id = kBootA;
    }
    rm_dir(d);
}

// A distinct, well-formed kernel boot_id per simulated boot.
static std::string boot_id_n(unsigned n) {
    char b[40];
    snprintf(b, sizeof(b), "5e1f0c2a-7b3d-4e8f-9a01-%012x", n);
    return b;
}

// W4-GK-1's security invariant (added by the fix verification, 2026-10-26): rebooting cannot be
// used to get around the throttle. A new boot moves only the timer's origin to the boot; the
// counter persists and an attempt refused with RETRY is not counted, so after any number of
// reboots the next guess that reaches the password check still needs the full tier, measured
// from the last boot, i.e. later in real time than without the reboots.
static void test_reboot_loop_no_bypass() {
    std::string d = make_dir();
    const Bytes pw = str_bytes("3691"), bad = str_bytes("1470");
    Bytes handle;
    g_boot_id = boot_id_n(0);
    {
        Env e(d);
        g_now = 2000;
        EnrollResult r = enroll(e, 100000, {}, {}, pw);
        CHECK(r.status == GK_STATUS_OK);
        handle = r.handle;
        for (int i = 1; i <= 5; i++) {
            g_now += 10;
            verify(e, 100000, 0, handle, bad);
        }
        CHECK(record_is(d, 100000, 5, g_now));  // 5 failures: 60 s
    }
    // ten reboots in a row, each with the right and a wrong PIN before 60 s from boot
    bool refused = true, kept = true, from_boot = true;
    for (unsigned b = 1; b <= 10; b++) {
        g_boot_id = boot_id_n(b);
        Env e(d);
        g_now = 50 + 5900 * b;  // 5,950 ... 59,050 ms after boot
        const VerifyResult rp = verify(e, 100000, 0, handle, pw);
        const VerifyResult wp = verify(e, 100000, 0, handle, bad);
        refused = refused && rp.ex == GB_EX_NONE && rp.status == GK_ERROR_RETRY_TIMEOUT &&
                  wp.ex == GB_EX_NONE && wp.status == GK_ERROR_RETRY_TIMEOUT;
        from_boot = from_boot && rp.timeout == static_cast<int32_t>(60000 - g_now) &&
                    wp.timeout == rp.timeout;
        GkStore probe(d, false);
        FailureRecord fr;
        kept = kept && probe.ReadRecord(100000, &fr) == GkStore::RecordStatus::OK &&
               fr.failure_counter == 5;
    }
    CHECK(refused);    // never unlocked, the wrong PIN never checked
    CHECK(kept);       // counter 5 after every reboot
    CHECK(from_boot);  // the time left since that boot
    // 60 s after the next boot the attempt reaches the password check: a wrong PIN is the 6th
    // failure (5 min), and that counter, too, survives every following reboot
    g_boot_id = boot_id_n(11);
    {
        Env e(d);
        g_now = 59999;
        CHECK(is_retry(verify(e, 100000, 0, handle, bad), 1));
        g_now = 60000;
        CHECK(is_retry(verify(e, 100000, 0, handle, bad), 300000));
        CHECK(record_is(d, 100000, 6, 60000));
    }
    refused = kept = from_boot = true;
    for (unsigned b = 12; b <= 21; b++) {
        g_boot_id = boot_id_n(b);
        Env e(d);
        g_now = 29000 * (b - 11);  // 29 s ... 290 s after boot
        const VerifyResult rp = verify(e, 100000, 0, handle, pw);
        refused = refused && rp.ex == GB_EX_NONE && rp.status == GK_ERROR_RETRY_TIMEOUT;
        from_boot = from_boot && rp.timeout == static_cast<int32_t>(300000 - g_now);
        GkStore probe(d, false);
        FailureRecord fr;
        kept = kept && probe.ReadRecord(100000, &fr) == GkStore::RecordStatus::OK &&
               fr.failure_counter == 6;
    }
    CHECK(refused);
    CHECK(kept);
    CHECK(from_boot);
    // starts that cannot be told from a new boot (boot_id file deleted before each) keep it too
    for (int i = 0; i < 3; i++) {
        unlink((d + "/boot_id").c_str());
        Env e(d);
        g_now = 1000 + i;
        CHECK(is_retry(verify(e, 100000, 0, handle, pw), 299000 - i));
        CHECK(record_is(d, 100000, 6, 0));
    }
    // the right PIN is accepted only once the full 5 min since the last boot have passed
    g_boot_id = boot_id_n(22);
    {
        Env e(d);
        g_now = 299999;
        CHECK(is_retry(verify(e, 100000, 0, handle, pw), 1));
        g_now = 300000;
        CHECK(is_ok(verify(e, 100000, 0, handle, pw)));
        CHECK(record_is(d, 100000, 0, 0));
    }
    g_boot_id = kBootA;
    rm_dir(d);
}

// W4-GK-2: a password signature that cannot be computed is an error, never a wrong guess.
static void test_crypto_failure_not_counted() {
    std::string d = make_dir();
    g_boot_id = kBootA;
    Env e(d);
    CHECK(e.hooks != nullptr);
    if (e.hooks == nullptr) return;
    const Bytes pw = str_bytes("4826"), bad = str_bytes("1111");
    g_now = 1000;
    EnrollResult r = enroll(e, 100000, {}, {}, pw);
    CHECK(r.status == GK_STATUS_OK);
    for (int i = 1; i <= 4; i++) {
        g_now += 10;
        verify(e, 100000, 0, r.handle, bad);
    }
    const uint64_t t4 = g_now;
    CHECK(record_is(d, 100000, 4, t4));
    // the right PIN while the signature computation fails: an error, the record unchanged on
    // disk (the pre-W4 code counted it: the 5th failure, a 60 s lock-out)
    e.hooks->fail_crypto = true;
    for (int i = 0; i < 2; i++) {
        g_now += 10;
        VerifyResult f = verify(e, 100000, 0, r.handle, pw);
        CHECK(f.st == GB_STATUS_OK && f.ex == GB_EX_SERVICE_SPECIFIC &&
              f.code == GK_ERROR_GENERAL_FAILURE);
        CHECK(record_is(d, 100000, 4, t4));
    }
    e.hooks->fail_crypto = false;
    g_now += 10;
    CHECK(is_ok(verify(e, 100000, 0, r.handle, pw)));  // not throttled: 4 failures, not 5 or 6
    CHECK(record_is(d, 100000, 0, 0));

    // a PIN change (current handle given) whose signature computation fails: not counted
    g_now += 10;
    verify(e, 100000, 0, r.handle, bad);
    g_now += 10;
    verify(e, 100000, 0, r.handle, bad);
    const uint64_t t2 = g_now;
    CHECK(record_is(d, 100000, 2, t2));
    e.hooks->fail_crypto = true;
    g_now += 10;
    EnrollResult c = enroll(e, 100000, r.handle, pw, str_bytes("9999"));
    CHECK(c.ex == GB_EX_SERVICE_SPECIFIC && c.code == GK_ERROR_GENERAL_FAILURE);
    CHECK(record_is(d, 100000, 2, t2));
    // a new enrollment whose handle cannot be signed leaves the record alone (the pre-W4 code
    // had already cleared it under a new SID)
    g_now += 10;
    EnrollResult n = enroll(e, 100000, {}, {}, str_bytes("9999"));
    CHECK(n.ex == GB_EX_SERVICE_SPECIFIC && n.code == GK_ERROR_GENERAL_FAILURE);
    CHECK(record_is(d, 100000, 2, t2));
    {
        GkStore probe(d, false);
        FailureRecord fr;
        CHECK(probe.ReadRecord(100000, &fr) == GkStore::RecordStatus::OK &&
              fr.secure_user_id == static_cast<uint64_t>(r.sid));
    }
    e.hooks->fail_crypto = false;
    // the old handle and PIN still work, and the record is then cleared
    g_now += 10;
    CHECK(is_ok(verify(e, 100000, 0, r.handle, pw)));
    CHECK(record_is(d, 100000, 0, 0));
    rm_dir(d);
}

static void test_key_loss_does_not_count() {
    std::string d = make_dir();
    const Bytes pw = str_bytes("7777");
    Bytes handle;
    {
        Env e(d);
        g_now = 1000;
        handle = enroll(e, 100000, {}, {}, pw).handle;
        g_now += 10;
        verify(e, 100000, 0, handle, str_bytes("1"));  // one real failure
    }
    unlink((d + "/gk_key.v1").c_str());
    unlink((d + "/gk_key.v1.bak").c_str());
    {
        Env e(d);
        g_now = 2000;
        for (int i = 0; i < 7; i++) {
            g_now += 10;
            VerifyResult v = verify(e, 100000, 0, handle, pw);
            CHECK(v.ex == GB_EX_SERVICE_SPECIFIC && v.code == GK_ERROR_GENERAL_FAILURE);
        }
        CHECK(!file_exists(d + "/gk_key.v1"));  // verify never creates a key
        FailureRecord fr;
        GkStore probe(d, false);
        CHECK(probe.ReadRecord(100000, &fr) == GkStore::RecordStatus::OK && fr.failure_counter == 1);
        // a PIN change (current handle given) never creates a key either, and is not counted
        EnrollResult c = enroll(e, 100000, handle, pw, str_bytes("8888"));
        CHECK(c.ex == GB_EX_SERVICE_SPECIFIC && c.code == GK_ERROR_GENERAL_FAILURE);
        CHECK(!file_exists(d + "/gk_key.v1"));
        CHECK(probe.ReadRecord(100000, &fr) == GkStore::RecordStatus::OK && fr.failure_counter == 1);
        // only a first enrollment does (here: K_dev was lost, the old handle stays dead)
        EnrollResult f = enroll(e, 100000, {}, {}, str_bytes("8888"));
        CHECK(f.ex == GB_EX_NONE && f.status == GK_STATUS_OK && file_exists(d + "/gk_key.v1"));
        CHECK(verify(e, 100000, 0, handle, pw).ex == GB_EX_SERVICE_SPECIFIC);
    }
    rm_dir(d);
}

static void test_replace_and_delete() {
    std::string d = make_dir();
    Env e(d);
    g_now = 1000;
    const Bytes pw1 = str_bytes("1111"), pw2 = str_bytes("2222");
    EnrollResult r1 = enroll(e, 0, {}, {}, pw1);
    CHECK(r1.status == GK_STATUS_OK);
    // change with the right current password: same SID, new handle
    EnrollResult r2 = enroll(e, 0, r1.handle, pw1, pw2);
    CHECK(r2.ex == GB_EX_NONE && r2.status == GK_STATUS_OK && r2.sid == r1.sid &&
          r2.handle != r1.handle);
    CHECK(verify(e, 0, 0, r2.handle, pw2).status == GK_STATUS_OK);
    // the wrong current password: error, and it counts
    g_now += 10;
    EnrollResult r3 = enroll(e, 0, r2.handle, pw1, pw1);
    CHECK(r3.ex == GB_EX_SERVICE_SPECIFIC);
    FailureRecord fr;
    GkStore probe(d, false);
    CHECK(probe.ReadRecord(0, &fr) == GkStore::RecordStatus::OK && fr.failure_counter == 1);
    // a fresh enrollment (no current handle) makes a new SID
    EnrollResult r4 = enroll(e, 0, {}, {}, pw1);
    CHECK(r4.status == GK_STATUS_OK && r4.sid != r1.sid);
    // deleteUser / deleteAllUsers drop throttling state, never the key
    int32_t ex = 1;
    CHECK(simple_call(e, TX_GK_DELETE_USER, &ex, 0) == GB_STATUS_OK && ex == GB_EX_NONE);
    CHECK(!file_exists(d + "/fr.0"));
    g_now += 10;
    verify(e, 100000, 0, r4.handle, pw2);
    CHECK(simple_call(e, TX_GK_DELETE_ALL_USERS, &ex, 0) == GB_STATUS_OK && ex == GB_EX_NONE);
    CHECK(!file_exists(d + "/fr.100000") && file_exists(d + "/gk_key.v1"));
    CHECK(read_file(d + "/boot_id") == line_bytes(kBootA));  // not a record: kept
    CHECK(verify(e, 0, 0, r4.handle, pw1).status == GK_STATUS_OK);
    rm_dir(d);
}

static void test_callers_and_meta() {
    std::string d = make_dir();
    Env e(d);
    int32_t ex = 0;
    CHECK(simple_call(e, TX_GK_DELETE_ALL_USERS, &ex, 0, /*caller=*/10123) == GB_STATUS_OK &&
          ex == GB_EX_SECURITY);
    EnrollResult r = enroll(e, 0, {}, {}, str_bytes("1"), /*caller=*/2000);
    CHECK(r.ex == GB_EX_SECURITY && !file_exists(d + "/gk_key.v1"));
    CHECK(simple_call(e, TX_GK_DELETE_ALL_USERS, &ex, 0, kAidRoot) == GB_STATUS_OK && ex == GB_EX_NONE);
    // meta transactions are open to everyone
    AParcel* out = nullptr;
    CHECK(simple_call(e, TX_GET_INTERFACE_VERSION, &ex, 0, 10123, &out) == GB_STATUS_OK && ex == 0);
    {
        fake::Reader rd{out};
        rd.Status(nullptr);
        CHECK(rd.I32() == 1 && rd.ok);
    }
    fake::DeleteParcel(out);
    CHECK(simple_call(e, TX_GET_INTERFACE_HASH, &ex, 0, 10123, &out) == GB_STATUS_OK && ex == 0);
    {
        fake::Reader rd{out};
        rd.Status(nullptr);
        CHECK(rd.String16() == kGatekeeperHash && rd.ok);
    }
    fake::DeleteParcel(out);
    // unknown transaction
    AParcel* in = fake::NewParcel();
    out = fake::NewParcel();
    CHECK(e.svc.OnGatekeeperTransact(5, in, out, kAidSystem) == GB_STATUS_UNKNOWN_TRANSACTION);
    CHECK(e.svc.OnSharedSecretTransact(3, in, out, kAidKeystore) == GB_STATUS_UNKNOWN_TRANSACTION);
    // truncated request: a binder error status, no crash
    CHECK(e.svc.OnGatekeeperTransact(TX_GK_ENROLL, in, out, kAidSystem) != GB_STATUS_OK);
    fake::DeleteParcel(in);
    fake::DeleteParcel(out);
    // a service whose self-test failed refuses enroll and never creates a key
    {
        Env bad(d, /*selftest_ok=*/false);
        EnrollResult b = enroll(bad, 0, {}, {}, str_bytes("9"));
        CHECK(b.ex == GB_EX_SERVICE_SPECIFIC && !file_exists(d + "/gk_key.v1"));
    }
    rm_dir(d);
}

struct SsCall {
    binder_status_t st;
    int32_t ex, code;
    Bytes a, b;
};

static SsCall ss_get(Env& e, uid_t caller = kAidKeystore) {
    AParcel* in = fake::NewParcel();
    AParcel* out = fake::NewParcel();
    SsCall c{};
    c.st = e.svc.OnSharedSecretTransact(TX_SS_GET_PARAMETERS, in, out, caller);
    fake::Rewind(out);
    fake::Reader rd{out};
    c.ex = rd.Status(&c.code);
    if (c.st == GB_STATUS_OK && c.ex == GB_EX_NONE) {
        size_t end = rd.BeginParcelable();
        c.a = rd.Bytes();
        c.b = rd.Bytes();
        rd.EndParcelable(end);
        if (!rd.ok) c.ex = 999;
    }
    fake::DeleteParcel(in);
    fake::DeleteParcel(out);
    return c;
}

static SsCall ss_compute(Env& e, const std::vector<SharedSecretParams>& params,
                         bool null_element = false, int extra_field = 0) {
    AParcel* in = fake::NewParcel();
    AParcel_writeInt32(in, static_cast<int32_t>(params.size()));
    for (const auto& p : params) {
        AParcel_writeInt32(in, null_element ? 0 : 1);
        int32_t start = AParcel_getDataPosition(in);
        AParcel_writeInt32(in, 0);
        put_bytes(in, p.seed);
        put_bytes(in, p.nonce);
        for (int i = 0; i < extra_field; i++) AParcel_writeInt32(in, 0x5a5a5a5a);  // newer field
        int32_t end = AParcel_getDataPosition(in);
        AParcel_setDataPosition(in, start);
        AParcel_writeInt32(in, end - start);
        AParcel_setDataPosition(in, end);
    }
    fake::Rewind(in);
    AParcel* out = fake::NewParcel();
    SsCall c{};
    c.st = e.svc.OnSharedSecretTransact(TX_SS_COMPUTE, in, out, kAidKeystore);
    fake::Rewind(out);
    fake::Reader rd{out};
    c.ex = rd.Status(&c.code);
    if (c.st == GB_STATUS_OK && c.ex == GB_EX_NONE) c.a = rd.Bytes();
    fake::DeleteParcel(in);
    fake::DeleteParcel(out);
    return c;
}

static void test_shared_secret() {
    std::string d = make_dir();
    Env e(d);
    SsCall g = ss_get(e);
    CHECK(g.st == GB_STATUS_OK && g.ex == GB_EX_NONE && g.a.empty() && g.b.size() == 32);
    SsCall g2 = ss_get(e);
    CHECK(g2.b == g.b);  // same nonce for the whole boot
    CHECK(ss_get(e, /*caller=*/10123).ex == GB_EX_SECURITY);

    // keystore2 sorts the parameters (derived Ord: seed, then nonce) and sends them to everyone.
    SharedSecretParams mine{g.a, g.b};
    SharedSecretParams km;  // the software KeyMint: empty seed, random nonce
    km.nonce.resize(32);
    for (int i = 0; i < 32; i++) km.nonce[i] = static_cast<uint8_t>(0x30 + i);
    std::vector<SharedSecretParams> params{mine, km};
    std::sort(params.begin(), params.end(), [](const SharedSecretParams& x,
                                               const SharedSecretParams& y) {
        return x.seed != y.seed ? x.seed < y.seed : x.nonce < y.nonce;
    });
    SsCall miss = ss_compute(e, {km});
    CHECK(miss.ex == GB_EX_SERVICE_SPECIFIC && miss.code == KM_ERROR_INVALID_ARGUMENT);
    SsCall nul = ss_compute(e, params, /*null_element=*/true);
    CHECK(nul.st == GB_STATUS_UNEXPECTED_NULL);
    SsCall c = ss_compute(e, params, false, /*extra_field=*/1);  // tolerates a newer parcelable
    CHECK(c.st == GB_STATUS_OK && c.ex == GB_EX_NONE && c.a.size() == 32);
    uint8_t h[32];
    CHECK(e.ss.HmacKey(h));
    std::string ps;
    for (const auto& p : params) {
        if (!ps.empty()) ps += ", ";
        ps += "{\"seed\": \"" + hex(p.seed) + "\", \"nonce\": \"" + hex(p.nonce) + "\"}";
    }
    json_add("\"shared\": {\"params\": [" + ps + "], \"check\": \"" + hex(c.a) + "\", \"h\": \"" +
             hex(h, 32) + "\"}");

    // tokens are now MAC'd with H
    g_now = 777777;
    const Bytes pw = str_bytes("13579");
    EnrollResult r = enroll(e, 100000, {}, {}, pw);
    VerifyResult v = verify(e, 100000, 42, r.handle, pw);
    CHECK(v.ex == GB_EX_NONE && v.status == GK_STATUS_OK && v.mac == hmac256(h, 32, token_bytes(v)));
    char buf[512];
    snprintf(buf, sizeof(buf),
             "\"token_h\": {\"key\": \"%s\", \"challenge\": %lld, \"user_id\": %lld, "
             "\"authenticator_id\": %lld, \"type\": %d, \"timestamp\": %lld, \"mac\": \"%s\"}",
             hex(h, 32).c_str(), (long long)v.challenge, (long long)v.user_id,
             (long long)v.auth_id, v.type, (long long)v.ts, hex(v.mac).c_str());
    json_add(buf);

    // CKDF against the NIST SP 800-108 structure with a one-block output (independent of H)
    uint8_t one[16];
    CHECK(SoftSharedSecret::Ckdf(h, 32, "label", params, one, sizeof(one)));
    json_add("\"ckdf16\": {\"key\": \"" + hex(h, 32) + "\", \"label\": \"label\", \"out\": \"" +
             hex(one, 16) + "\"}");
    rm_dir(d);
}

// ---- the service process itself (service_main.cpp, unchanged, linked with tests/fake_sm.cpp)

static uint64_t mono_ms() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1000 + static_cast<uint64_t>(ts.tv_nsec) / 1000000;
}

struct SvcRun {
    bool started = false;
    bool serving = false;  // reached joinThreadPool (then killed by the test)
    bool exited = false;   // exited by itself
    int status = -1;       // its exit status
    std::string out;       // stdout + stderr (FAKE_SM lines and the service log)
};

static size_t count_of(const std::string& s, const std::string& what) {
    size_t n = 0;
    for (size_t p = s.find(what); p != std::string::npos; p = s.find(what, p + 1)) n++;
    return n;
}
static bool has(const std::string& s, const std::string& what) { return count_of(s, what) > 0; }

// Runs SERVICE [ARG...] args...; refuse = $GBGK_FAKE_SM_REFUSE. Waits until it serves, exits,
// or 30 s pass (5 registration attempts take 5 s).
static SvcRun run_service(const std::vector<std::string>& args, const char* refuse) {
    SvcRun r;
    if (g_service_cmd.empty()) return r;
    std::vector<std::string> cmd = g_service_cmd;
    cmd.insert(cmd.end(), args.begin(), args.end());
    int p[2];
    if (pipe(p) != 0) return r;
    pid_t pid = fork();
    if (pid < 0) {
        close(p[0]);
        close(p[1]);
        return r;
    }
    if (pid == 0) {
        dup2(p[1], STDOUT_FILENO);
        dup2(p[1], STDERR_FILENO);
        close(p[0]);
        close(p[1]);
        if (refuse) {
            setenv("GBGK_FAKE_SM_REFUSE", refuse, 1);
        } else {
            unsetenv("GBGK_FAKE_SM_REFUSE");
        }
        std::vector<char*> av;
        for (auto& s : cmd) av.push_back(const_cast<char*>(s.c_str()));
        av.push_back(nullptr);
        execv(av[0], av.data());
        _exit(127);
    }
    close(p[1]);
    r.started = true;
    const uint64_t deadline = mono_ms() + 30000;
    bool eof = false;
    int st = 0;
    bool reaped = false;
    while (mono_ms() < deadline && !r.serving) {
        if (!eof) {
            struct pollfd pfd = {p[0], POLLIN, 0};
            if (poll(&pfd, 1, 100) > 0) {
                char buf[512];
                ssize_t k = read(p[0], buf, sizeof(buf));
                if (k > 0) {
                    r.out.append(buf, static_cast<size_t>(k));
                } else if (k == 0 || errno != EINTR) {
                    eof = true;
                }
            }
            if (has(r.out, "FAKE_SM joinThreadPool")) r.serving = true;
        } else {
            usleep(20000);
        }
        if (!r.serving && waitpid(pid, &st, WNOHANG) == pid) {
            reaped = true;
            // drain what is left in the pipe
            char buf[512];
            ssize_t k;
            while ((k = read(p[0], buf, sizeof(buf))) > 0) r.out.append(buf, static_cast<size_t>(k));
            break;
        }
    }
    if (!reaped) {
        kill(pid, SIGKILL);
        waitpid(pid, &st, 0);
    } else {
        r.exited = WIFEXITED(st);
        r.status = WIFEXITED(st) ? WEXITSTATUS(st) : -1;
    }
    close(p[0]);
    return r;
}

static void dump_on_failure(bool ok, const char* what, const SvcRun& r) {
    if (ok) return;
    fprintf(stderr, "  [%s] %s: started=%d serving=%d exited=%d status=%d, output:\n%s", g_test,
            what, r.started, r.serving, r.exited, r.status, r.out.c_str());
}

// W4-GK-3: IGatekeeper/default not registered -> exit non-zero (init restarts the service)
// instead of idling in joinThreadPool, which was a silent boot hang.
static void test_registration() {
    CHECK(!g_service_cmd.empty());  // gk_host_tests VECTORS.json TMPDIR SERVICE [ARG...]
    if (g_service_cmd.empty()) return;
    std::string d = make_dir();
    const std::vector<std::string> sd{"--state-dir", d};
    const std::string gk_line = std::string("addService ") + kGatekeeperInstance + " -> ";
    const std::string ss_line = std::string("addService ") + kSharedSecretInstance + " -> ";

    SvcRun ok = run_service(sd, nullptr);  // both instances register: it serves
    bool good = ok.serving && !ok.exited && has(ok.out, gk_line + "0") && has(ok.out, ss_line + "0");
    CHECK(good);
    dump_on_failure(good, "both registered", ok);

    SvcRun gk = run_service(sd, kGatekeeperInstance);  // IGatekeeper/default refused
    good = gk.exited && gk.status == 3 && !gk.serving;
    CHECK(good);  // pre-W4: kept running in joinThreadPool
    dump_on_failure(good, "IGatekeeper refused", gk);
    CHECK(count_of(gk.out, gk_line + "-3") == 5);  // still five attempts first
    CHECK(!has(gk.out, ss_line));  // a process about to exit never offers ISharedSecret
    CHECK(has(gk.out, std::string(kGatekeeperInstance) + " not registered: exiting with status 3"));

    SvcRun ss = run_service(sd, kSharedSecretInstance);  // only ISharedSecret refused: serve
    good = ss.serving && !ss.exited && has(ss.out, gk_line + "0") &&
           count_of(ss.out, ss_line + "-3") == 5;
    CHECK(good);
    dump_on_failure(good, "ISharedSecret refused", ss);

    SvcRun st = run_service({"--selftest"}, nullptr);
    CHECK(st.exited && st.status == 0 && has(st.out, "gb_gatekeeper selftest: PASS"));
    SvcRun bad_arg = run_service({"--bogus"}, nullptr);
    CHECK(bad_arg.exited && bad_arg.status == 2 && !has(bad_arg.out, "FAKE_SM"));
    rm_dir(d);
}

#define RUN(fn)        \
    do {               \
        g_test = #fn;  \
        fn();          \
    } while (0)

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s VECTORS.json TMPDIR SERVICE [ARG...]\n", argv[0]);
        return 2;
    }
    if (argc > 2) g_tmp = argv[2];
    for (int i = 3; i < argc; i++) g_service_cmd.push_back(argv[i]);
    gk_log_set_enabled(getenv("GBGK_TEST_LOG") != nullptr);
    RUN(test_selftest);
    RUN(test_store_key);
    RUN(test_store_volatile);
    RUN(test_store_records);
    RUN(test_enroll_verify);
    RUN(test_throttling);
    RUN(test_reboot_loop_no_bypass);
    RUN(test_crypto_failure_not_counted);
    RUN(test_key_loss_does_not_count);
    RUN(test_replace_and_delete);
    RUN(test_callers_and_meta);
    RUN(test_shared_secret);
    RUN(test_registration);
    FILE* f = fopen(argv[1], "w");
    if (f) {
        fprintf(f, "{\n%s\n}\n", g_json.c_str());
        fclose(f);
    }
    printf("GK_HOST_TESTS: %d passed, %d failed\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
