// SPDX-License-Identifier: Apache-2.0
//
// GbGateKeeper: a software gatekeeper::GateKeeper (AOSP system/gatekeeper, compiled in from
// source) whose state survives reboots and OTAs.
//
//   password signature  sig = HMAC-SHA256(K_dev, scrypt(P = version|user_id|flags|password,
//                                                       S = salt, N = 16384, r = 8, p = 1, 32))
//                       The scrypt step and its parameters are those of AOSP's nonsecure
//                       SoftGateKeeper (hardware/interfaces/gatekeeper/aidl/software/
//                       SoftGateKeeper.h); K_dev is a per-device random key (GkStore), generated
//                       at the first enrollment, never a compile-time constant.
//   auth token MAC      HMAC-SHA256(H, token) with H from the ISharedSecret agreement
//                       (SoftSharedSecret); 32 zero bytes until keystore2 has negotiated.
//   clock               CLOCK_BOOTTIME in ms, the clock the software KeyMint checks
//                       AUTH_TIMEOUT against.
//   failure records     persisted per uid (GkStore), so throttling survives a reboot or a crash
//                       of this service; libgatekeeper's ThrottleRequest restarts a pending
//                       timeout at boot instead of dropping it.
//   hardware_backed     false.
#pragma once

#include <stdint.h>

#include <map>

#include <gatekeeper/gatekeeper.h>

#include "gk_store.h"
#include "shared_secret.h"

namespace gbgk {

using ClockFn = uint64_t (*)();

class GbGateKeeper : public ::gatekeeper::GateKeeper {
  public:
    // scrypt parameters of AOSP's SoftGateKeeper
    static constexpr uint64_t kScryptN = 16384;
    static constexpr uint32_t kScryptR = 8;
    static constexpr uint32_t kScryptP = 1;

    GbGateKeeper(GkStore* store, SoftSharedSecret* shared_secret, ClockFn clock = nullptr);
    ~GbGateKeeper() override;

    // Makes K_dev available: enroll may create it (allow_create), verify never does.
    bool EnsureKey(bool allow_create);

    // Set when a password signature could not be computed during the last Enroll/Verify; the
    // adapter then refuses the result (never hands out a handle nobody can verify).
    void ClearCryptoFailure() { crypto_failed_ = false; }
    bool CryptoFailed() const { return crypto_failed_; }

    // Known-answer tests of SHA-256, HMAC-SHA256, scrypt and AES-256-CMAC.
    static bool SelfTest();

    static uint64_t BootTimeMs();

  protected:
    bool GetAuthTokenKey(const uint8_t** auth_token_key, uint32_t* length) const override;
    void GetPasswordKey(const uint8_t** password_key, uint32_t* length) override;
    void ComputePasswordSignature(uint8_t* signature, uint32_t signature_length,
                                  const uint8_t* key, uint32_t key_length,
                                  const uint8_t* password, uint32_t password_length,
                                  ::gatekeeper::salt_t salt) const override;
    void GetRandom(void* random, uint32_t requested_size) const override;
    void ComputeSignature(uint8_t* signature, uint32_t signature_length, const uint8_t* key,
                          uint32_t key_length, const uint8_t* message,
                          const uint32_t length) const override;
    uint64_t GetMillisecondsSinceBoot() const override;
    ::gatekeeper::gatekeeper_error_t RemoveUser(uint32_t uid) override;
    ::gatekeeper::gatekeeper_error_t RemoveAllUsers() override;
    bool GetFailureRecord(uint32_t uid, ::gatekeeper::secure_id_t user_id,
                          ::gatekeeper::failure_record_t* record, bool secure) override;
    bool ClearFailureRecord(uint32_t uid, ::gatekeeper::secure_id_t user_id,
                            bool secure) override;
    bool WriteFailureRecord(uint32_t uid, ::gatekeeper::failure_record_t* record,
                            bool secure) override;
    bool IsHardwareBacked() const override { return false; }

  private:
    FailureRecord& CachedRecord(uint32_t uid);
    void Persist(uint32_t uid, const FailureRecord& rec);

    GkStore* store_;
    SoftSharedSecret* shared_secret_;
    ClockFn clock_;
    uint8_t key_[GkStore::kKeySize] = {};
    bool have_key_ = false;
    mutable uint8_t auth_key_[SoftSharedSecret::kKeySize] = {};
    mutable bool warned_no_hmac_key_ = false;
    mutable bool crypto_failed_ = false;
    std::map<uint32_t, FailureRecord> records_;
};

}  // namespace gbgk
