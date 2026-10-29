// SPDX-License-Identifier: Apache-2.0
#include "gb_gatekeeper.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <openssl/cipher.h>
#include <openssl/cmac.h>
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <openssl/mem.h>
#include <openssl/rand.h>
#include <openssl/sha.h>

#include "gk_log.h"

using ::gatekeeper::ERROR_NONE;
using ::gatekeeper::failure_record_t;
using ::gatekeeper::gatekeeper_error_t;
using ::gatekeeper::salt_t;
using ::gatekeeper::secure_id_t;

namespace gbgk {

namespace {

bool hex_eq(const uint8_t* got, size_t len, const char* hex) {
    if (strlen(hex) != len * 2) return false;
    for (size_t i = 0; i < len; i++) {
        unsigned v;
        char b[3] = {hex[2 * i], hex[2 * i + 1], 0};
        if (sscanf(b, "%2x", &v) != 1 || got[i] != v) return false;
    }
    return true;
}

// The kernel's boot_id format: 8-4-4-4-12 hex digits.
bool is_boot_id(const std::string& s) {
    if (s.size() != 36) return false;
    for (size_t i = 0; i < s.size(); i++) {
        const char c = s[i];
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (c != '-') return false;
        } else if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'))) {
            return false;
        }
    }
    return true;
}

}  // namespace

GbGateKeeper::GbGateKeeper(GkStore* store, SoftSharedSecret* shared_secret, ClockFn clock)
    : store_(store), shared_secret_(shared_secret), clock_(clock ? clock : &BootTimeMs) {}

GbGateKeeper::~GbGateKeeper() {
    OPENSSL_cleanse(key_, sizeof(key_));
    OPENSSL_cleanse(auth_key_, sizeof(auth_key_));
}

uint64_t GbGateKeeper::BootTimeMs() {
    struct timespec ts;
    if (clock_gettime(CLOCK_BOOTTIME, &ts) != 0) return 0;
    return static_cast<uint64_t>(ts.tv_sec) * 1000 + static_cast<uint64_t>(ts.tv_nsec) / 1000000;
}

bool GbGateKeeper::EnsureKey(bool allow_create) {
    if (have_key_) return true;
    switch (store_->LoadKey(key_, allow_create)) {
        case GkStore::KeyStatus::OK:
            have_key_ = true;
            return true;
        case GkStore::KeyStatus::ABSENT:
            GKLOGE("no password key yet: nothing was ever enrolled on this /data, so there is "
                   "no handle this service could verify");
            return false;
        case GkStore::KeyStatus::ERROR:
        default:
            return false;
    }
}

bool GbGateKeeper::GetAuthTokenKey(const uint8_t** auth_token_key, uint32_t* length) const {
    if (auth_token_key == nullptr || length == nullptr) return false;
    if (!shared_secret_->HmacKey(auth_key_)) {
        memset(auth_key_, 0, sizeof(auth_key_));
        if (!warned_no_hmac_key_) {
            GKLOGW("no shared HMAC key negotiated yet: signing auth tokens with the zero key");
            warned_no_hmac_key_ = true;
        }
    }
    *auth_token_key = auth_key_;
    *length = sizeof(auth_key_);
    return true;
}

void GbGateKeeper::GetPasswordKey(const uint8_t** password_key, uint32_t* length) {
    if (password_key == nullptr || length == nullptr) return;
    if (!have_key_) {
        // libgatekeeper treats a null key as "cannot create a handle"; the adapter calls
        // EnsureKey() first, so this is only reached if that contract is broken.
        *password_key = nullptr;
        *length = 0;
        crypto_failed_ = true;
        return;
    }
    *password_key = key_;
    *length = sizeof(key_);
}

void GbGateKeeper::ComputePasswordSignature(uint8_t* signature, uint32_t signature_length,
                                            const uint8_t* key, uint32_t key_length,
                                            const uint8_t* password, uint32_t password_length,
                                            salt_t salt) const {
    if (signature == nullptr || signature_length == 0) return;
    uint8_t stretched[32];
    unsigned int mac_len = 0;
    uint8_t mac[EVP_MAX_MD_SIZE];
    bool ok = key != nullptr && key_length == GkStore::kKeySize &&
              signature_length <= SHA256_DIGEST_LENGTH &&
              EVP_PBE_scrypt(reinterpret_cast<const char*>(password), password_length,
                             reinterpret_cast<const uint8_t*>(&salt), sizeof(salt), kScryptN,
                             kScryptR, kScryptP, 0 /* default max_mem 32 MiB */, stretched,
                             sizeof(stretched)) == 1 &&
              HMAC(EVP_sha256(), key, key_length, stretched, sizeof(stretched), mac, &mac_len) !=
                      nullptr &&
              mac_len == SHA256_DIGEST_LENGTH;
    if (ok) {
        memcpy(signature, mac, signature_length);
    } else {
        // Never leave a predictable value behind: a failed computation must not match anything.
        GKLOGE("password signature computation failed");
        crypto_failed_ = true;
        RAND_bytes(signature, signature_length);
    }
    OPENSSL_cleanse(stretched, sizeof(stretched));
    OPENSSL_cleanse(mac, sizeof(mac));
}

void GbGateKeeper::GetRandom(void* random, uint32_t requested_size) const {
    if (random == nullptr) return;
    // BoringSSL's RAND_bytes uses getrandom(2) and aborts rather than return weak bytes.
    RAND_bytes(static_cast<uint8_t*>(random), requested_size);
}

void GbGateKeeper::ComputeSignature(uint8_t* signature, uint32_t signature_length,
                                    const uint8_t* key, uint32_t key_length,
                                    const uint8_t* message, const uint32_t length) const {
    if (signature == nullptr) return;
    uint8_t mac[EVP_MAX_MD_SIZE];
    unsigned int mac_len = 0;
    if (HMAC(EVP_sha256(), key, key_length, message, length, mac, &mac_len) == nullptr ||
        mac_len < signature_length) {
        GKLOGE("auth token MAC computation failed");
        memset(signature, 0, signature_length);
        return;
    }
    memcpy(signature, mac, signature_length);
    OPENSSL_cleanse(mac, sizeof(mac));
}

uint64_t GbGateKeeper::GetMillisecondsSinceBoot() const { return clock_(); }

bool GbGateKeeper::CurrentBootId(std::string* out) const {
    int fd = open(kKernelBootIdPath, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return false;
    char buf[64];
    ssize_t n;
    do {
        n = read(fd, buf, sizeof(buf));
    } while (n < 0 && errno == EINTR);
    close(fd);
    if (n <= 0) return false;
    size_t len = static_cast<size_t>(n);
    if (buf[len - 1] == '\n') len--;
    out->assign(buf, len);
    return true;
}

void GbGateKeeper::CheckBoot() {
    if (boot_checked_) return;
    std::string now;
    const bool have_now = CurrentBootId(&now) && is_boot_id(now);
    const GkStore::BootIdStatus st = store_->CompareBootId(have_now ? now : std::string());
    // No directory: no record can be read either; check again at the next load.
    if (st == GkStore::BootIdStatus::NO_DIR) return;
    boot_checked_ = true;
    if (have_now && st == GkStore::BootIdStatus::SAME) return;  // a restart within this boot

    // A new boot (or one that cannot be told apart from it): the pending timeouts run from boot,
    // as LockSettingsService's do. Counters are kept. Erring this way is never stricter than
    // LockSettingsService, which still enforces its own timeout.
    new_boot_ = true;
    const bool reset_ok = store_->ResetRecordTimestamps();
    if (!have_now) {
        GKLOGW("cannot read %s: treating this start as a new boot (throttling timers run from "
               "boot, counters kept)",
               kKernelBootIdPath);
        return;
    }
    if (!reset_ok) {
        // Not stored, so the next start of the service repeats the reset; this process zeroes
        // the timestamps of the records it loads anyway (CachedRecord).
        GKLOGW("not every failure record could be rewritten; boot_id not stored");
        return;
    }
    if (!store_->WriteBootId(now)) {
        GKLOGW("boot_id not stored; the next start of the service repeats the reset");
        return;
    }
    GKLOGI("new boot: failure-record timers run from boot (counters kept)");
}

FailureRecord& GbGateKeeper::CachedRecord(uint32_t uid) {
    auto it = records_.find(uid);
    if (it != records_.end()) return it->second;
    CheckBoot();
    FailureRecord rec;
    store_->ReadRecord(uid, &rec);  // ABSENT and CORRUPT both read as "no failures"
    // W4-GK-1: also covers a record whose reset could not be written. A record cached by this
    // process is never reloaded (only RemoveUser/RemoveAllUsers drop one, with its file), so
    // this never touches a timestamp written during this boot by this process.
    if (new_boot_) rec.last_checked_timestamp = 0;
    return records_.emplace(uid, rec).first->second;
}

void GbGateKeeper::Persist(uint32_t uid, const FailureRecord& rec) {
    if (!store_->WriteRecord(uid, rec)) {
        GKLOGW("failure record of uid %u not persisted (kept in memory)", uid);
    }
}

FailureRecord GbGateKeeper::SnapshotRecord(uint32_t uid) { return CachedRecord(uid); }

void GbGateKeeper::RestoreRecord(uint32_t uid, const FailureRecord& rec) {
    FailureRecord& r = CachedRecord(uid);
    if (r.secure_user_id == rec.secure_user_id && r.failure_counter == rec.failure_counter &&
        r.last_checked_timestamp == rec.last_checked_timestamp) {
        return;
    }
    r = rec;
    Persist(uid, r);
}

bool GbGateKeeper::GetFailureRecord(uint32_t uid, secure_id_t user_id, failure_record_t* record,
                                    bool /* secure */) {
    FailureRecord& r = CachedRecord(uid);
    if (r.secure_user_id != user_id) {
        // A record of an older enrollment (other SID) does not throttle the new one.
        r = FailureRecord();
        r.secure_user_id = user_id;
    }
    record->secure_user_id = r.secure_user_id;
    record->last_checked_timestamp = r.last_checked_timestamp;
    record->failure_counter = r.failure_counter;
    return true;
}

bool GbGateKeeper::ClearFailureRecord(uint32_t uid, secure_id_t user_id, bool /* secure */) {
    FailureRecord& r = CachedRecord(uid);
    FailureRecord cleared;
    cleared.secure_user_id = user_id;
    bool changed = r.secure_user_id != cleared.secure_user_id || r.failure_counter != 0 ||
                   r.last_checked_timestamp != 0;
    r = cleared;
    if (changed) Persist(uid, r);
    // true for the "secure" variant too: new handles carry HANDLE_FLAG_THROTTLE_SECURE, as with
    // AOSP's SoftGateKeeper, and our records are persistent.
    return true;
}

bool GbGateKeeper::WriteFailureRecord(uint32_t uid, failure_record_t* record, bool /* secure */) {
    FailureRecord& r = CachedRecord(uid);
    r.secure_user_id = record->secure_user_id;
    r.last_checked_timestamp = record->last_checked_timestamp;
    r.failure_counter = record->failure_counter;
    Persist(uid, r);
    return true;  // never fail the request because of storage (see gk_store.h)
}

gatekeeper_error_t GbGateKeeper::RemoveUser(uint32_t uid) {
    records_.erase(uid);
    if (!store_->RemoveRecord(uid)) GKLOGW("removing the failure record of uid %u failed", uid);
    return ERROR_NONE;
}

gatekeeper_error_t GbGateKeeper::RemoveAllUsers() {
    // Only throttling state. K_dev stays: gatekeeperd calls deleteAllUsers on its first enroll
    // after /data/misc/gatekeeper was created, and a handle made with K_dev may still exist in
    // LockSettings' storage. A factory reset removes K_dev together with /data.
    records_.clear();
    if (!store_->RemoveAllRecords()) GKLOGW("removing the failure records failed");
    return ERROR_NONE;
}

bool GbGateKeeper::SelfTest() {
    uint8_t out[64];
    unsigned int len = 0;
    // FIPS 180-2 B.1
    SHA256(reinterpret_cast<const uint8_t*>("abc"), 3, out);
    if (!hex_eq(out, 32, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")) {
        GKLOGE("self-test: SHA-256 failed");
        return false;
    }
    // RFC 4231 test case 2
    static const char kData[] = "what do ya want for nothing?";
    if (HMAC(EVP_sha256(), "Jefe", 4, reinterpret_cast<const uint8_t*>(kData), strlen(kData),
             out, &len) == nullptr ||
        len != 32 ||
        !hex_eq(out, 32, "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843")) {
        GKLOGE("self-test: HMAC-SHA256 failed");
        return false;
    }
    // RFC 7914 section 12, first vector (P = "", S = "", N = 16, r = 1, p = 1)
    if (EVP_PBE_scrypt("", 0, reinterpret_cast<const uint8_t*>(""), 0, 16, 1, 1, 0, out, 64) !=
            1 ||
        !hex_eq(out, 64,
                "77d6576238657b203b19ca42c18a0497f16b4844e3074ae8dfdffa3fede21442"
                "fcd0069ded0948f8326a753a0fc81f17e8d3e0fb2e0d3628cf35e20c38d18906")) {
        GKLOGE("self-test: scrypt failed");
        return false;
    }
    // NIST SP 800-38B D.3 (AES-256), example 2 (Mlen = 128)
    static const uint8_t kKey[32] = {0x60, 0x3d, 0xeb, 0x10, 0x15, 0xca, 0x71, 0xbe,
                                     0x2b, 0x73, 0xae, 0xf0, 0x85, 0x7d, 0x77, 0x81,
                                     0x1f, 0x35, 0x2c, 0x07, 0x3b, 0x61, 0x08, 0xd7,
                                     0x2d, 0x98, 0x10, 0xa3, 0x09, 0x14, 0xdf, 0xf4};
    static const uint8_t kMsg[16] = {0x6b, 0xc1, 0xbe, 0xe2, 0x2e, 0x40, 0x9f, 0x96,
                                     0xe9, 0x3d, 0x7e, 0x11, 0x73, 0x93, 0x17, 0x2a};
    size_t cmac_len = 0;
    CMAC_CTX* ctx = CMAC_CTX_new();
    bool ok = ctx != nullptr && CMAC_Init(ctx, kKey, 32, EVP_aes_256_cbc(), nullptr) == 1 &&
              CMAC_Update(ctx, kMsg, 16) == 1 && CMAC_Final(ctx, out, &cmac_len) == 1 &&
              cmac_len == 16 && hex_eq(out, 16, "28a7023f452e8f82bd4bf28d8c37c35c");
    CMAC_CTX_free(ctx);
    if (!ok) {
        GKLOGE("self-test: AES-256-CMAC failed");
        return false;
    }
    return true;
}

}  // namespace gbgk
