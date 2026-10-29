// SPDX-License-Identifier: Apache-2.0
//
// The two binder interfaces served by gb_gatekeeper, as stable-AIDL wire handlers:
//   android.hardware.gatekeeper.IGatekeeper/default                 V1, hash d877e8a1...
//   android.hardware.security.sharedsecret.ISharedSecret/gatekeeper  V1, hash aea3745b...
// Semantics follow AOSP's nonsecure Gatekeeper (hardware/interfaces/gatekeeper/aidl/software/
// GateKeeper.cpp): a failed enroll/verify is a service-specific ERROR_GENERAL_FAILURE, throttling
// is statusCode ERROR_RETRY_TIMEOUT with timeoutMs, success carries the handle / the auth token.
#pragma once

#include <sys/types.h>

#include "binder_api.h"
#include "gb_gatekeeper.h"
#include "shared_secret.h"

namespace gbgk {

constexpr const char kGatekeeperDescriptor[] = "android.hardware.gatekeeper.IGatekeeper";
constexpr const char kGatekeeperInstance[] = "android.hardware.gatekeeper.IGatekeeper/default";
constexpr const char kGatekeeperHash[] = "d877e8a1608a00fd9068bcd7e69ea480a7ea17f0";
constexpr const char kSharedSecretDescriptor[] =
        "android.hardware.security.sharedsecret.ISharedSecret";
constexpr const char kSharedSecretInstance[] =
        "android.hardware.security.sharedsecret.ISharedSecret/gatekeeper";
constexpr const char kSharedSecretHash[] = "aea3745b559d2977f0676952e510fb0547a52140";

// transaction codes: FIRST_CALL_TRANSACTION + method index; the two meta transactions
enum : transaction_code_t {
    TX_GK_DELETE_ALL_USERS = 1,
    TX_GK_DELETE_USER = 2,
    TX_GK_ENROLL = 3,
    TX_GK_VERIFY = 4,
    TX_SS_GET_PARAMETERS = 1,
    TX_SS_COMPUTE = 2,
    TX_GET_INTERFACE_HASH = 0x00fffffe,
    TX_GET_INTERFACE_VERSION = 0x00ffffff,
};

// IGatekeeper constants
enum : int32_t {
    GK_STATUS_REENROLL = 1,
    GK_STATUS_OK = 0,
    GK_ERROR_GENERAL_FAILURE = -1,
    GK_ERROR_RETRY_TIMEOUT = -2,
    GK_ERROR_NOT_IMPLEMENTED = -3,
};

// Callers allowed to use the interfaces (meta transactions are open to everyone):
// IGatekeeper only from gatekeeperd (user system), ISharedSecret only from keystore2 (user
// keystore); root for on-device tests. SELinux runs permissive here, so this is the check that
// keeps an app from resetting throttling or the shared HMAC key.
constexpr uid_t kAidRoot = 0;
constexpr uid_t kAidSystem = 1000;
constexpr uid_t kAidKeystore = 1017;

class GatekeeperService {
  public:
    GatekeeperService(GbGateKeeper* gk, SoftSharedSecret* ss, bool selftest_ok)
        : gk_(gk), ss_(ss), selftest_ok_(selftest_ok) {}

    binder_status_t OnGatekeeperTransact(transaction_code_t code, const AParcel* in,
                                         AParcel* out, uid_t caller);
    binder_status_t OnSharedSecretTransact(transaction_code_t code, const AParcel* in,
                                           AParcel* out, uid_t caller);

  private:
    binder_status_t Enroll(const AParcel* in, AParcel* out);
    binder_status_t Verify(const AParcel* in, AParcel* out);
    binder_status_t GetParameters(AParcel* out);
    binder_status_t Compute(const AParcel* in, AParcel* out);

    GbGateKeeper* gk_;
    SoftSharedSecret* ss_;
    bool selftest_ok_;
};

}  // namespace gbgk
