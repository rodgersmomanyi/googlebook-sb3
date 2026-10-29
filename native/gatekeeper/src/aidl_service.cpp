// SPDX-License-Identifier: Apache-2.0
//
// Stable-AIDL wire handlers, written by hand against the libbinder_ndk C API (no generated
// android.hardware.*-V1-ndk.so, whose C++ ABI is tied to the vendor libc++). The encoding is the
// one of the NDK backend (binder_parcel_utils.h): a non-null parcelable is int32 1, then int32
// size (patched afterwards, measured from its own position), then the fields in declaration
// order; nested parcelable fields are framed the same way; byte[] is int32 length + bytes.
//
// The parcelable framing and the meta transactions are adapted from gbOS86
// gatekeeper/soft_gk.c (github.com/linuxkid473/gbos-x86, commit 03a6a46, by Vihaan Nathan,
// SPDX-License-Identifier: Apache-2.0); see NOTICE.
#include "aidl_service.h"

#include <endian.h>
#include <stdint.h>
#include <string.h>

#include <mutex>
#include <new>
#include <vector>

#include <gatekeeper/gatekeeper_messages.h>
#include <gatekeeper/password_handle.h>
#include <hardware/hw_auth_token.h>
#include <openssl/mem.h>

#include "gk_log.h"

namespace gbgk {

namespace {

using Bytes = std::vector<uint8_t>;
using ::gatekeeper::DeleteAllUsersRequest;
using ::gatekeeper::DeleteAllUsersResponse;
using ::gatekeeper::DeleteUserRequest;
using ::gatekeeper::DeleteUserResponse;
using ::gatekeeper::EnrollRequest;
using ::gatekeeper::EnrollResponse;
using ::gatekeeper::ERROR_INVALID;
using ::gatekeeper::ERROR_NONE;
using ::gatekeeper::ERROR_RETRY;
using ::gatekeeper::gatekeeper_error_t;
using ::gatekeeper::password_handle_t;
using ::gatekeeper::SizedBuffer;
using ::gatekeeper::VerifyRequest;
using ::gatekeeper::VerifyResponse;

constexpr int32_t kMaxArgBytes = 64 * 1024;  // no legitimate argument comes near this
constexpr int32_t kMaxParticipants = 16;
std::mutex g_gk_mutex;

// Wipes the vectors that held credentials when a handler returns.
struct Wiper {
    std::vector<Bytes*> v;
    ~Wiper() {
        for (Bytes* b : v) {
            if (!b->empty()) OPENSSL_cleanse(b->data(), b->size());
        }
    }
};

bool alloc_bytes(void* data, int32_t len, int8_t** out) {
    Bytes* v = static_cast<Bytes*>(data);
    v->clear();
    if (len <= 0) {  // -1 (null) is read as empty: the arrays of these interfaces are non-null
        *out = nullptr;
        return true;
    }
    if (len > kMaxArgBytes) return false;
    v->assign(static_cast<size_t>(len), 0);
    *out = reinterpret_cast<int8_t*>(v->data());
    return true;
}

binder_status_t read_bytes(const AParcel* in, Bytes* v) {
    return AParcel_readByteArray(in, v, alloc_bytes);
}

binder_status_t write_bytes(AParcel* out, const uint8_t* p, size_t n) {
    static const int8_t kNone = 0;
    return AParcel_writeByteArray(out, n ? reinterpret_cast<const int8_t*>(p) : &kNone,
                                  static_cast<int32_t>(n));
}

binder_status_t begin_parcelable(AParcel* out, int32_t* start) {
    binder_status_t s = AParcel_writeInt32(out, 1);  // non-null
    if (s != GB_STATUS_OK) return s;
    *start = AParcel_getDataPosition(out);
    return AParcel_writeInt32(out, 0);  // size, patched by end_parcelable()
}

binder_status_t end_parcelable(AParcel* out, int32_t start) {
    int32_t end = AParcel_getDataPosition(out);
    binder_status_t s = AParcel_setDataPosition(out, start);
    if (s == GB_STATUS_OK) s = AParcel_writeInt32(out, end - start);
    if (s == GB_STATUS_OK) s = AParcel_setDataPosition(out, end);
    return s;
}

binder_status_t write_status(AParcel* out, AStatus* st) {
    if (st == nullptr) return GB_STATUS_NO_MEMORY;
    binder_status_t s = AParcel_writeStatusHeader(out, st);
    AStatus_delete(st);
    return s;
}
binder_status_t write_ok(AParcel* out) { return write_status(out, AStatus_newOk()); }
binder_status_t write_service_error(AParcel* out, int32_t code) {
    return write_status(out, AStatus_fromServiceSpecificError(code));
}
binder_status_t write_exception(AParcel* out, binder_exception_t ex) {
    return write_status(out, AStatus_fromExceptionCode(ex));
}

binder_status_t write_meta(transaction_code_t code, AParcel* out, const char* hash) {
    binder_status_t s = write_ok(out);
    if (s != GB_STATUS_OK) return s;
    if (code == TX_GET_INTERFACE_VERSION) return AParcel_writeInt32(out, 1);
    return AParcel_writeString(out, hash, static_cast<int32_t>(strlen(hash)));
}

// W4-GK-2: what libgatekeeper answers for a request it did not decide on the password: a
// signature it could not compute (CryptoFailed), or an error other than "mismatch / bad request"
// (ERROR_INVALID) and "throttled" (ERROR_RETRY), e.g. ERROR_MEMORY_ALLOCATION_FAILED from
// MintAuthToken or ERROR_UNKNOWN. Such a request must leave the failure record as it was.
bool internal_failure(bool crypto_failed, gatekeeper_error_t error) {
    return crypto_failed || (error != ERROR_NONE && error != ERROR_INVALID && error != ERROR_RETRY);
}

SizedBuffer to_sized(const Bytes& v) {
    if (v.empty()) return SizedBuffer();
    uint8_t* p = new (std::nothrow) uint8_t[v.size()];
    if (p == nullptr) return SizedBuffer();
    memcpy(p, v.data(), v.size());
    return SizedBuffer(p, static_cast<uint32_t>(v.size()));
}

// GatekeeperEnrollResponse {int statusCode; int timeoutMs; long secureUserId; byte[] data;}
binder_status_t write_enroll_response(AParcel* out, int32_t status, int32_t timeout_ms,
                                      int64_t sid, const uint8_t* data, size_t len) {
    int32_t start = 0;
    binder_status_t s = write_ok(out);
    if (s == GB_STATUS_OK) s = begin_parcelable(out, &start);
    if (s == GB_STATUS_OK) s = AParcel_writeInt32(out, status);
    if (s == GB_STATUS_OK) s = AParcel_writeInt32(out, timeout_ms);
    if (s == GB_STATUS_OK) s = AParcel_writeInt64(out, sid);
    if (s == GB_STATUS_OK) s = write_bytes(out, data, len);
    if (s == GB_STATUS_OK) s = end_parcelable(out, start);
    return s;
}

// GatekeeperVerifyResponse {int statusCode; int timeoutMs; HardwareAuthToken hardwareAuthToken;}
// HardwareAuthToken {long challenge; long userId; long authenticatorId;
//                    HardwareAuthenticatorType(int) authenticatorType; Timestamp timestamp;
//                    byte[] mac;}   Timestamp {long milliSeconds;}
binder_status_t write_verify_response(AParcel* out, int32_t status, int32_t timeout_ms,
                                      const hw_auth_token_t* t) {
    int32_t start = 0, hat = 0, ts = 0;
    binder_status_t s = write_ok(out);
    if (s == GB_STATUS_OK) s = begin_parcelable(out, &start);
    if (s == GB_STATUS_OK) s = AParcel_writeInt32(out, status);
    if (s == GB_STATUS_OK) s = AParcel_writeInt32(out, timeout_ms);
    if (s == GB_STATUS_OK) s = begin_parcelable(out, &hat);
    // hw_auth_token_t keeps authenticator_type and timestamp in network order (libgatekeeper
    // MintAuthToken); the AIDL token carries host order (AOSP sizedBuffer2AidlHWToken).
    if (s == GB_STATUS_OK) s = AParcel_writeInt64(out, t ? static_cast<int64_t>(t->challenge) : 0);
    if (s == GB_STATUS_OK) s = AParcel_writeInt64(out, t ? static_cast<int64_t>(t->user_id) : 0);
    if (s == GB_STATUS_OK)
        s = AParcel_writeInt64(out, t ? static_cast<int64_t>(t->authenticator_id) : 0);
    if (s == GB_STATUS_OK)
        s = AParcel_writeInt32(out, t ? static_cast<int32_t>(be32toh(t->authenticator_type)) : 0);
    if (s == GB_STATUS_OK) s = begin_parcelable(out, &ts);
    if (s == GB_STATUS_OK)
        s = AParcel_writeInt64(out, t ? static_cast<int64_t>(be64toh(t->timestamp)) : 0);
    if (s == GB_STATUS_OK) s = end_parcelable(out, ts);
    if (s == GB_STATUS_OK) s = write_bytes(out, t ? t->hmac : nullptr, t ? sizeof(t->hmac) : 0);
    if (s == GB_STATUS_OK) s = end_parcelable(out, hat);
    if (s == GB_STATUS_OK) s = end_parcelable(out, start);
    return s;
}

// One SharedSecretParameters {byte[] seed; byte[] nonce;} element of an array.
binder_status_t read_params(const AParcel* in, SharedSecretParams* p) {
    int32_t nonnull = 0, size = 0;
    binder_status_t s = AParcel_readInt32(in, &nonnull);
    if (s != GB_STATUS_OK) return s;
    if (nonnull == 0) return GB_STATUS_UNEXPECTED_NULL;
    int32_t start = AParcel_getDataPosition(in);
    s = AParcel_readInt32(in, &size);
    if (s != GB_STATUS_OK) return s;
    if (size < 4 || start > INT32_MAX - size) return GB_STATUS_BAD_VALUE;
    if (AParcel_getDataPosition(in) - start < size) {
        s = read_bytes(in, &p->seed);
        if (s != GB_STATUS_OK) return s;
    }
    if (AParcel_getDataPosition(in) - start < size) {
        s = read_bytes(in, &p->nonce);
        if (s != GB_STATUS_OK) return s;
    }
    if (AParcel_getDataPosition(in) - start > size) return GB_STATUS_BAD_VALUE;
    return AParcel_setDataPosition(in, start + size);  // skips fields of a newer version
}

}  // namespace

binder_status_t GatekeeperService::OnGatekeeperTransact(transaction_code_t code,
                                                        const AParcel* in, AParcel* out,
                                                        uid_t caller) {
    AParcel_markSensitive(out);  // IGatekeeper is @SensitiveData
    if (code == TX_GET_INTERFACE_VERSION || code == TX_GET_INTERFACE_HASH) {
        return write_meta(code, out, kGatekeeperHash);
    }
    if (code < TX_GK_DELETE_ALL_USERS || code > TX_GK_VERIFY) return GB_STATUS_UNKNOWN_TRANSACTION;
    if (caller != kAidSystem && caller != kAidRoot) {
        GKLOGW("IGatekeeper call %u from uid %u refused", code, static_cast<unsigned>(caller));
        return write_exception(out, GB_EX_SECURITY);
    }
    std::lock_guard<std::mutex> lock(g_gk_mutex);
    switch (code) {
        case TX_GK_DELETE_ALL_USERS: {
            DeleteAllUsersResponse rsp;
            gk_->DeleteAllUsers(DeleteAllUsersRequest(), &rsp);
            GKLOGI("deleteAllUsers");
            return write_ok(out);
        }
        case TX_GK_DELETE_USER: {
            int32_t uid = 0;
            binder_status_t s = AParcel_readInt32(in, &uid);
            if (s != GB_STATUS_OK) return s;
            DeleteUserResponse rsp;
            gk_->DeleteUser(DeleteUserRequest(static_cast<uint32_t>(uid)), &rsp);
            GKLOGI("deleteUser uid=%d", uid);
            return write_ok(out);
        }
        case TX_GK_ENROLL:
            return Enroll(in, out);
        default:
            return Verify(in, out);
    }
}

binder_status_t GatekeeperService::Enroll(const AParcel* in, AParcel* out) {
    int32_t uid = 0;
    Bytes cur_handle, cur_pw, desired;
    Wiper wipe{{&cur_handle, &cur_pw, &desired}};
    binder_status_t s;
    if ((s = AParcel_readInt32(in, &uid)) != GB_STATUS_OK ||
        (s = read_bytes(in, &cur_handle)) != GB_STATUS_OK ||
        (s = read_bytes(in, &cur_pw)) != GB_STATUS_OK ||
        (s = read_bytes(in, &desired)) != GB_STATUS_OK) {
        return s;
    }
    const bool replace = !cur_handle.empty();
    const char* why = nullptr;
    if (!selftest_ok_) {
        why = "crypto self-test failed at start";
    } else if (desired.empty()) {
        why = "empty desired password";
    } else if (replace && cur_handle.size() != sizeof(password_handle_t)) {
        why = "current handle has the wrong length";
    } else if (!gk_->EnsureKey(/*allow_create=*/!replace)) {
        // Only a first enrollment (no current handle) may create K_dev: a PIN change presents a
        // handle made with an existing K_dev, so a missing K_dev there is a loss, not a start.
        why = "password key unavailable";
    }
    if (why != nullptr) {
        GKLOGE("enroll uid=%d replace=%d -> error (%s)", uid, replace, why);
        return write_service_error(out, GK_ERROR_GENERAL_FAILURE);
    }

    EnrollRequest req(static_cast<uint32_t>(uid), to_sized(cur_handle), to_sized(desired),
                      to_sized(cur_pw));
    EnrollResponse rsp;
    // libgatekeeper writes the record (incremented for a PIN change, cleared for a new
    // enrollment) before it computes a signature: keep a copy to put back (W4-GK-2).
    const FailureRecord before = gk_->SnapshotRecord(static_cast<uint32_t>(uid));
    gk_->ClearCryptoFailure();
    gk_->Enroll(req, &rsp);
    if (internal_failure(gk_->CryptoFailed(), rsp.error)) {
        gk_->RestoreRecord(static_cast<uint32_t>(uid), before);
        GKLOGE("enroll uid=%d replace=%d -> error (%s %d; not counted)", uid, replace,
               gk_->CryptoFailed() ? "crypto failure" : "internal error", rsp.error);
        return write_service_error(out, GK_ERROR_GENERAL_FAILURE);
    }
    if (rsp.error == ERROR_RETRY) {
        GKLOGI("enroll uid=%d replace=%d -> retry in %u ms", uid, replace, rsp.retry_timeout);
        return write_enroll_response(out, GK_ERROR_RETRY_TIMEOUT,
                                     static_cast<int32_t>(rsp.retry_timeout), 0, nullptr, 0);
    }
    const password_handle_t* h = rsp.enrolled_password_handle.Data<password_handle_t>();
    if (rsp.error != ERROR_NONE || h == nullptr ||
        rsp.enrolled_password_handle.size() != sizeof(password_handle_t)) {
        GKLOGI("enroll uid=%d replace=%d -> error %d", uid, replace, rsp.error);
        return write_service_error(out, GK_ERROR_GENERAL_FAILURE);
    }
    GKLOGI("enroll uid=%d replace=%d -> ok", uid, replace);
    return write_enroll_response(out, GK_STATUS_OK, 0, static_cast<int64_t>(h->user_id),
                                 rsp.enrolled_password_handle.Data<uint8_t>(),
                                 rsp.enrolled_password_handle.size());
}

binder_status_t GatekeeperService::Verify(const AParcel* in, AParcel* out) {
    int32_t uid = 0;
    int64_t challenge = 0;
    Bytes handle, pw;
    Wiper wipe{{&handle, &pw}};
    binder_status_t s;
    if ((s = AParcel_readInt32(in, &uid)) != GB_STATUS_OK ||
        (s = AParcel_readInt64(in, &challenge)) != GB_STATUS_OK ||
        (s = read_bytes(in, &handle)) != GB_STATUS_OK ||
        (s = read_bytes(in, &pw)) != GB_STATUS_OK) {
        return s;
    }
    const char* why = nullptr;
    if (!selftest_ok_) {
        why = "crypto self-test failed at start";
    } else if (handle.size() != sizeof(password_handle_t)) {
        why = "handle has the wrong length";
    } else if (!gk_->EnsureKey(/*allow_create=*/false)) {
        // Checked before libgatekeeper runs, so it is not counted as a wrong guess.
        why = "password key unavailable";
    }
    if (why != nullptr) {
        GKLOGE("verify uid=%d -> error (%s)", uid, why);
        return write_service_error(out, GK_ERROR_GENERAL_FAILURE);
    }

    VerifyRequest req(static_cast<uint32_t>(uid), static_cast<uint64_t>(challenge),
                      to_sized(handle), to_sized(pw));
    VerifyResponse rsp;
    // libgatekeeper increments and writes the record before it computes the signature: keep a
    // copy to put back if the request fails for an internal reason (W4-GK-2).
    const FailureRecord before = gk_->SnapshotRecord(static_cast<uint32_t>(uid));
    gk_->ClearCryptoFailure();
    gk_->Verify(req, &rsp);
    if (internal_failure(gk_->CryptoFailed(), rsp.error)) {
        gk_->RestoreRecord(static_cast<uint32_t>(uid), before);
        GKLOGE("verify uid=%d -> error (%s %d; not counted)", uid,
               gk_->CryptoFailed() ? "crypto failure" : "internal error", rsp.error);
        return write_service_error(out, GK_ERROR_GENERAL_FAILURE);
    }
    if (rsp.error == ERROR_RETRY) {
        GKLOGI("verify uid=%d -> retry in %u ms", uid, rsp.retry_timeout);
        return write_verify_response(out, GK_ERROR_RETRY_TIMEOUT,
                                     static_cast<int32_t>(rsp.retry_timeout), nullptr);
    }
    const hw_auth_token_t* t = rsp.auth_token.Data<hw_auth_token_t>();
    if (rsp.error != ERROR_NONE || t == nullptr || rsp.auth_token.size() != sizeof(*t)) {
        GKLOGI("verify uid=%d -> error %d", uid, rsp.error);
        return write_service_error(out, GK_ERROR_GENERAL_FAILURE);
    }
    GKLOGI("verify uid=%d -> ok%s", uid, rsp.request_reenroll ? " (re-enroll)" : "");
    return write_verify_response(out, rsp.request_reenroll ? GK_STATUS_REENROLL : GK_STATUS_OK, 0,
                                 t);
}

binder_status_t GatekeeperService::OnSharedSecretTransact(transaction_code_t code,
                                                          const AParcel* in, AParcel* out,
                                                          uid_t caller) {
    if (code == TX_GET_INTERFACE_VERSION || code == TX_GET_INTERFACE_HASH) {
        return write_meta(code, out, kSharedSecretHash);
    }
    if (code != TX_SS_GET_PARAMETERS && code != TX_SS_COMPUTE) return GB_STATUS_UNKNOWN_TRANSACTION;
    if (caller != kAidKeystore && caller != kAidRoot) {
        GKLOGW("ISharedSecret call %u from uid %u refused", code, static_cast<unsigned>(caller));
        return write_exception(out, GB_EX_SECURITY);
    }
    return code == TX_SS_GET_PARAMETERS ? GetParameters(out) : Compute(in, out);
}

binder_status_t GatekeeperService::GetParameters(AParcel* out) {
    SharedSecretParams p = ss_->GetParameters();
    if (p.nonce.size() != 32) return write_service_error(out, KM_ERROR_UNKNOWN_ERROR);
    int32_t start = 0;
    binder_status_t s = write_ok(out);
    if (s == GB_STATUS_OK) s = begin_parcelable(out, &start);
    if (s == GB_STATUS_OK) s = write_bytes(out, p.seed.data(), p.seed.size());
    if (s == GB_STATUS_OK) s = write_bytes(out, p.nonce.data(), p.nonce.size());
    if (s == GB_STATUS_OK) s = end_parcelable(out, start);
    GKLOGI("getSharedSecretParameters");
    return s;
}

binder_status_t GatekeeperService::Compute(const AParcel* in, AParcel* out) {
    int32_t n = 0;
    binder_status_t s = AParcel_readInt32(in, &n);
    if (s != GB_STATUS_OK) return s;
    if (n < 0) return GB_STATUS_UNEXPECTED_NULL;
    if (n > kMaxParticipants) return GB_STATUS_BAD_VALUE;
    std::vector<SharedSecretParams> params(static_cast<size_t>(n));
    for (auto& p : params) {
        s = read_params(in, &p);
        if (s != GB_STATUS_OK) return s;
    }
    if (!selftest_ok_) return write_service_error(out, KM_ERROR_UNKNOWN_ERROR);
    std::vector<uint8_t> check;
    int32_t err = ss_->Compute(params, &check);
    if (err != KM_ERROR_OK) return write_service_error(out, err);
    s = write_ok(out);
    if (s == GB_STATUS_OK) s = write_bytes(out, check.data(), check.size());
    return s;
}

}  // namespace gbgk
