// SPDX-License-Identifier: Apache-2.0
//
// Frozen on-disk vectors of gb_gatekeeper (safety audit SA-6). The two key files and handles below were written by
// gb_gatekeeper 0.2.0 itself (the unit tests of its 2026-10-26 build enrolled the PIN "1234" with a random K_dev:
// out/host/vectors.json and out/android/test/vectors.json); tests/frozen_v1.json holds the same bytes and
// tests/frozen_check.py re-derives the scheme from them independently (hashlib scrypt + HMAC) and checks that the
// constants here equal that file. Every later build must still verify these handles through the production path
// (GkStore key files -> GbGateKeeper -> the IGatekeeper verify transaction), or a device with an enrolled PIN would
// lock its user out after the update. Never regenerate these constants: a scheme change needs a migration instead.
//
//   gk_frozen_tests TMPDIR        prints "GK_FROZEN_V1: PASS (<n> checks)" or FAIL lines; exit 0 only on PASS
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>  // AOSP password_handle.h relies on its includer for <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <string>
#include <vector>

#include <openssl/hmac.h>
#include <openssl/sha.h>

#include "../src/aidl_service.h"
#include "../src/gb_gatekeeper.h"
#include "../src/gk_log.h"
#include "../src/gk_store.h"
#include "../src/shared_secret.h"
#include "fake_binder.h"

using namespace gbgk;
using Bytes = std::vector<uint8_t>;

namespace {

struct Vector {
    const char* name;
    const char* key_file;   // 72 bytes: "GBGKKEY1" | K_dev | SHA-256("GBGKKEY1" | K_dev)
    const char* password;   // the PIN "1234"
    const char* handle;     // 58 bytes: password_handle_t v2 as enrolled by 0.2.0
    const char* secure_user_id;
};

// FROZEN v1 (== tests/frozen_v1.json; checked by tests/frozen_check.py). Do not edit.
const Vector kVectors[] = {
    {"v1-host-0.2.0",
     "4742474b4b45593105e7574af475fe1ee105ec08f9be9a875fd1fc6161e9ba54dcba5d266fa2ba5fa85a36a2dbf17595df569c1f52b00f845701e2c23f87ffe2b40bcf0fc5abd659",
     "31323334",
     "02cfcf14b485efc4870100000000000000dd018a042c68fc6bd52f4a0c1b9db337af60aafa8909cda77e2bd47157db37c5960d8453d75da91c00",
     "87c4ef85b414cfcf"},
    {"v1-android-lapis-bionic-0.2.0",
     "4742474b4b45593101c8d065430ef2f03ba0315176765020f1da4745095e344836aa18582f9f31b099d3fe14fd4420ce1b448f8879f4c2efd5739ad6dda38684d896017e08306f8c",
     "31323334",
     "027e6472808114bc2501000000000000001fa5c0c7e4aad079bdd206d574e7b529722dd944f381a6ec08bf38aa778557f33c2628ba130772bc00",
     "25bc14818072647e"},
};
const int32_t kUid = 100000;

int g_pass = 0, g_fail = 0;
std::string g_case;
#define CHECK(cond)                                                                        \
    do {                                                                                   \
        if (cond) {                                                                        \
            g_pass++;                                                                      \
        } else {                                                                           \
            g_fail++;                                                                      \
            fprintf(stderr, "FAIL [%s] %s:%d: %s\n", g_case.c_str(), __FILE__, __LINE__, #cond); \
        }                                                                                  \
    } while (0)

uint64_t g_now = 5000;
uint64_t fake_clock() { return g_now; }
std::string g_tmp = "/tmp";

Bytes unhex(const char* s) {
    Bytes v;
    for (size_t i = 0; s[i] && s[i + 1]; i += 2) {
        unsigned x;
        char b[3] = {s[i], s[i + 1], 0};
        sscanf(b, "%2x", &x);
        v.push_back(static_cast<uint8_t>(x));
    }
    return v;
}

std::string make_dir() {
    std::string t = g_tmp + "/gbgk_frozen.XXXXXX";
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

void rm_dir(const std::string& d) {
    for (const char* n : {"gk_key.v1", "gk_key.v1.bak", "gk_key.v1.tmp", "gk_key.v1.bak.tmp", "boot_id",
                          "boot_id.tmp", "fr.100000", "fr.100000.tmp"}) {
        unlink((d + "/" + n).c_str());
    }
    rmdir(d.c_str());
}

void write_file(const std::string& p, const Bytes& v) {
    int fd = open(p.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0) return;
    if (!v.empty() && write(fd, v.data(), v.size()) != static_cast<ssize_t>(v.size())) perror("write");
    close(fd);
}

Bytes read_file(const std::string& p) {
    Bytes v;
    FILE* f = fopen(p.c_str(), "rb");
    if (!f) return v;
    uint8_t b[256];
    size_t n;
    while ((n = fread(b, 1, sizeof(b), f)) > 0) v.insert(v.end(), b, b + n);
    fclose(f);
    return v;
}

struct VerifyResult {
    binder_status_t st = -1;
    int32_t ex = 0, code = 0, status = 0, timeout = 0;
    int64_t challenge = 0, user_id = 0, auth_id = 0, ts = 0;
    int32_t type = 0;
    Bytes mac;
    bool parsed = false;
};

// One IGatekeeper verify transaction against the production classes, on the state directory d (a new process: no
// cached key or record, as after a reboot or an OTA).
VerifyResult verify(const std::string& d, int64_t challenge, const Bytes& handle, const Bytes& pw) {
    GkStore store(d, /*refuse_volatile_fs=*/false);
    SoftSharedSecret ss;
    GbGateKeeper gk(&store, &ss, fake_clock);
    GatekeeperService svc(&gk, &ss, /*selftest_ok=*/true);
    AParcel* in = fake::NewParcel();
    AParcel_writeInt32(in, kUid);
    AParcel_writeInt64(in, challenge);
    static const int8_t none = 0;
    AParcel_writeByteArray(in, handle.empty() ? &none : reinterpret_cast<const int8_t*>(handle.data()),
                           static_cast<int32_t>(handle.size()));
    AParcel_writeByteArray(in, pw.empty() ? &none : reinterpret_cast<const int8_t*>(pw.data()),
                           static_cast<int32_t>(pw.size()));
    fake::Rewind(in);
    AParcel* out = fake::NewParcel();
    VerifyResult r;
    r.st = svc.OnGatekeeperTransact(TX_GK_VERIFY, in, out, kAidSystem);
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

// The 37 bytes of hw_auth_token_t the MAC covers (libgatekeeper byte order), rebuilt from the AIDL fields.
Bytes token_bytes(const VerifyResult& v) {
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

bool ok(const VerifyResult& r) {
    return r.st == GB_STATUS_OK && r.ex == GB_EX_NONE && r.parsed && r.status == GK_STATUS_OK && r.timeout == 0;
}
bool refused(const VerifyResult& r) {
    return r.st == GB_STATUS_OK && r.ex == GB_EX_SERVICE_SPECIFIC && r.code == GK_ERROR_GENERAL_FAILURE;
}

void test_vector(const Vector& v) {
    const Bytes key = unhex(v.key_file), pw = unhex(v.password), handle = unhex(v.handle);
    const Bytes sid_be = unhex(v.secure_user_id);
    uint64_t sid = 0;
    for (uint8_t b : sid_be) sid = (sid << 8) | b;
    CHECK(key.size() == 72 && memcmp(key.data(), "GBGKKEY1", 8) == 0 && handle.size() == 58 && sid != 0);

    g_case = std::string(v.name) + ": both key copies, right PIN";
    {
        std::string d = make_dir();
        write_file(d + "/gk_key.v1", key);
        write_file(d + "/gk_key.v1.bak", key);
        const int64_t challenge = 0x0123456789abcdefLL;
        VerifyResult r = verify(d, challenge, handle, pw);
        CHECK(ok(r));
        CHECK(static_cast<uint64_t>(r.user_id) == sid && r.challenge == challenge && r.type == 1 && r.auth_id == 0);
        static const uint8_t zero_key[32] = {};
        Bytes mac(32);
        unsigned int len = 0;
        Bytes tok = token_bytes(r);
        HMAC(EVP_sha256(), zero_key, 32, tok.data(), tok.size(), mac.data(), &len);
        CHECK(len == 32 && r.mac == mac);  // the auth token is signed (zero key: no shared secret negotiated)
        CHECK(read_file(d + "/gk_key.v1") == key && read_file(d + "/gk_key.v1.bak") == key);  // never rewritten
        g_case = std::string(v.name) + ": wrong PIN";
        VerifyResult w = verify(d, 0, handle, unhex("31323335"));
        CHECK(refused(w));
        g_case = std::string(v.name) + ": right PIN again (one failure does not throttle)";
        CHECK(ok(verify(d, 0, handle, pw)));
        rm_dir(d);
    }
    g_case = std::string(v.name) + ": only the backup copy gk_key.v1.bak";
    {
        std::string d = make_dir();
        write_file(d + "/gk_key.v1.bak", key);
        CHECK(ok(verify(d, 0, handle, pw)));
        CHECK(read_file(d + "/gk_key.v1") == key);  // the primary is restored byte for byte
        rm_dir(d);
    }
    g_case = std::string(v.name) + ": only the primary copy gk_key.v1";
    {
        std::string d = make_dir();
        write_file(d + "/gk_key.v1", key);
        CHECK(ok(verify(d, 0, handle, pw)));
        CHECK(read_file(d + "/gk_key.v1.bak") == key);
        rm_dir(d);
    }
    g_case = std::string(v.name) + ": a valid key file with another K_dev";
    {
        Bytes other = key;
        other[8] ^= 1;
        SHA256(other.data(), 40, other.data() + 40);
        std::string d = make_dir();
        write_file(d + "/gk_key.v1", other);
        write_file(d + "/gk_key.v1.bak", other);
        CHECK(refused(verify(d, 0, handle, pw)));
        rm_dir(d);
    }
    g_case = std::string(v.name) + ": both key copies with a bad checksum";
    {
        Bytes bad = key;
        bad[71] ^= 1;
        std::string d = make_dir();
        write_file(d + "/gk_key.v1", bad);
        write_file(d + "/gk_key.v1.bak", bad);
        CHECK(refused(verify(d, 0, handle, pw)));
        CHECK(read_file(d + "/gk_key.v1") == bad && read_file(d + "/gk_key.v1.bak") == bad);  // no new key
        rm_dir(d);
    }
    g_case = std::string(v.name) + ": tampered handle (signature, salt, user id)";
    for (size_t at : {30, 17, 1}) {
        Bytes t = handle;
        t[at] ^= 1;
        std::string d = make_dir();
        write_file(d + "/gk_key.v1", key);
        write_file(d + "/gk_key.v1.bak", key);
        CHECK(!ok(verify(d, 0, t, pw)));
        rm_dir(d);
    }
}

}  // namespace

int main(int argc, char** argv) {
    if (argc > 1) g_tmp = argv[1];
    gk_log_set_enabled(getenv("GBGK_TEST_LOG") != nullptr);
    for (const Vector& v : kVectors) test_vector(v);
    g_case = "cross: vector 1 handle with vector 2 key";
    {
        std::string d = make_dir();
        Bytes k2 = unhex(kVectors[1].key_file);
        write_file(d + "/gk_key.v1", k2);
        write_file(d + "/gk_key.v1.bak", k2);
        CHECK(refused(verify(d, 0, unhex(kVectors[0].handle), unhex(kVectors[0].password))));
        rm_dir(d);
    }
    if (g_fail == 0) {
        printf("GK_FROZEN_V1: PASS (%d checks, %zu vectors)\n", g_pass,
               sizeof(kVectors) / sizeof(kVectors[0]));
        return 0;
    }
    printf("GK_FROZEN_V1: FAIL (%d passed, %d failed)\n", g_pass, g_fail);
    return 1;
}
