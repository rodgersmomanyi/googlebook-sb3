// SPDX-License-Identifier: Apache-2.0
//
// ISharedSecret/gatekeeper participant: lets keystore2 include the Gatekeeper in the HMAC key
// agreement (keystore2/src/shared_secret_negotiation.rs), so the auth tokens it mints carry a MAC
// under the same per-boot key H as the KeyMint's. Same derivation as AOSP's nonsecure Gatekeeper
// (hardware/interfaces/gatekeeper/aidl/software/SharedSecret.cpp) and as the pure-software KeyMint
// (system/keymaster/km_openssl/soft_keymaster_enforcement.cpp):
//
//   H = CKDF(K = 32 zero bytes, label = "KeymasterSharedMac", context = P1 || ... || Pn)
//   sharingCheck = HMAC-SHA256(H, "Keymaster HMAC Verification")
//
// with CKDF = NIST SP 800-108 counter mode over AES-256-CMAC and Pi = seed_i || nonce_i. K is a
// public constant on every software implementation, so H is not a secret from anyone who can
// read the nonces: it only keeps the token format interoperable. Our seed is empty (the AIDL spec:
// "either empty or a non zero persistent value"), our nonce is random once per process.
#pragma once

#include <stdint.h>

#include <mutex>
#include <vector>

namespace gbgk {

struct SharedSecretParams {
    std::vector<uint8_t> seed;
    std::vector<uint8_t> nonce;
};

// KeyMint error codes returned as service-specific errors (as AOSP's SharedSecret.cpp does).
enum : int32_t {
    KM_ERROR_OK = 0,
    KM_ERROR_INVALID_ARGUMENT = -38,
    KM_ERROR_MEMORY_ALLOCATION_FAILED = -41,
    KM_ERROR_UNKNOWN_ERROR = -1000,
};

class SoftSharedSecret {
  public:
    static constexpr size_t kKeySize = 32;
    static constexpr const char* kKeyAgreementLabel = "KeymasterSharedMac";
    static constexpr const char* kKeyCheckLabel = "Keymaster HMAC Verification";

    SharedSecretParams GetParameters();
    // Returns a KM_ERROR_* code; on KM_ERROR_OK *sharing_check is the 32-byte check value.
    int32_t Compute(const std::vector<SharedSecretParams>& params,
                    std::vector<uint8_t>* sharing_check);
    // The negotiated key H; false until Compute() succeeded.
    bool HmacKey(uint8_t out[kKeySize]) const;

    // Exposed for the unit tests: CKDF as specified in ISharedSecret.aidl.
    static bool Ckdf(const uint8_t* key, size_t key_len, const char* label,
                     const std::vector<SharedSecretParams>& context, uint8_t* out,
                     size_t out_len);

  private:
    mutable std::mutex mutex_;
    std::vector<uint8_t> nonce_;
    uint8_t hmac_key_[kKeySize] = {};
    bool have_key_ = false;
};

}  // namespace gbgk
