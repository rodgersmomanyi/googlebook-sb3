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
//                       of this service. Counters are never reset by a reboot.
//   reboot (W4-GK-1)    at the first record load of a process, if the kernel boot_id differs
//                       from the one stored in the state directory (or that file is missing or
//                       damaged, or the kernel's cannot be read), every record's
//                       last_checked_timestamp is set to 0 on disk and in every record this
//                       process loads, then the new boot_id is stored. A pending timeout then
//                       runs from boot, exactly like LockSettingsService's own limiter (which
//                       restarts its timer at boot and counts every non-match, also RETRY), so an
//                       attempt it lets through is never refused here. Without this,
//                       libgatekeeper's ThrottleRequest sees a timestamp at or before the stored
//                       one and restarts the full timeout, and LockSettingsService counts that
//                       RETRY: one more tier per reboot (review GK-2). A restart of the service
//                       within the same boot keeps the timestamps.
//   internal failures   (W4-GK-2) the adapter (aidl_service.cpp) takes a copy of the record
//                       before libgatekeeper runs and puts it back when the request failed for an
//                       internal reason (CryptoFailed(), or a libgatekeeper error other than a
//                       mismatch or a timeout): libgatekeeper writes the incremented record
//                       before it computes the signature, so otherwise e.g. a failed scrypt
//                       allocation would count as a wrong guess.
//   hardware_backed     false.
#pragma once

#include <stdint.h>

#include <map>
#include <string>

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

    // W4-GK-2: the record of uid as libgatekeeper will see it (loads it, after the boot check),
    // and putting such a copy back (cache and disk) after an internal failure.
    FailureRecord SnapshotRecord(uint32_t uid);
    void RestoreRecord(uint32_t uid, const FailureRecord& rec);

    // Known-answer tests of SHA-256, HMAC-SHA256, scrypt and AES-256-CMAC.
    static bool SelfTest();

    static uint64_t BootTimeMs();

    static constexpr const char* kKernelBootIdPath = "/proc/sys/kernel/random/boot_id";

  protected:
    // The current boot's id (36 characters, without the newline); false if it cannot be read.
    // Reads kKernelBootIdPath; virtual for the host tests.
    virtual bool CurrentBootId(std::string* out) const;

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
    void CheckBoot();  // W4-GK-1, once per process, before the first record load

    GkStore* store_;
    SoftSharedSecret* shared_secret_;
    ClockFn clock_;
    uint8_t key_[GkStore::kKeySize] = {};
    bool have_key_ = false;
    mutable uint8_t auth_key_[SoftSharedSecret::kKeySize] = {};
    mutable bool warned_no_hmac_key_ = false;
    mutable bool crypto_failed_ = false;
    bool boot_checked_ = false;
    bool new_boot_ = false;  // records loaded by this process start with last_checked_timestamp 0
    std::map<uint32_t, FailureRecord> records_;
};

}  // namespace gbgk
