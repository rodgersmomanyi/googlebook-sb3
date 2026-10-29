// SPDX-License-Identifier: Apache-2.0
//
// Persistent state of gb_gatekeeper in one directory (on the device /data/vendor/gatekeeper, mode
// 0700, owned by the service user, SELinux type gatekeeper_vendor_data_file):
//
//   gk_key.v1, gk_key.v1.bak   the device password key K_dev (32 random bytes), generated once.
//                              72 bytes: "GBGKKEY1" | K_dev | SHA-256("GBGKKEY1" | K_dev)
//   fr.<uid>                   the failure record of one Gatekeeper uid (throttling state).
//                              64 bytes: "GBGKFR01" | uid u32 | failure_counter u32 |
//                              secure_user_id u64 | last_checked_timestamp u64 | SHA-256 of the rest
//
// Rules that keep an enrolled PIN verifiable (losing K_dev = every handle fails = lock-out):
//   * K_dev is created only when BOTH key files are absent (ENOENT), never on any other error;
//     a corrupt or unreadable copy is repaired from the other one, never regenerated.
//   * Files are written to <name>.tmp, fsync'd and renamed (the key with link(): no replace), then
//     the directory is fsync'd. The directory itself is never created here (init creates it on the
//     mounted /data), and a key is never created on tmpfs/ramfs.
//   * A failure record that is missing or corrupt reads as "no failures" (like LockSettingsService's
//     own software rate-limiter counter); a failed write is logged and the in-memory copy stays
//     authoritative, so a full disk can never block an unlock.
#pragma once

#include <stdint.h>

#include <string>

namespace gbgk {

struct FailureRecord {
    uint64_t secure_user_id = 0;
    uint64_t last_checked_timestamp = 0;
    uint32_t failure_counter = 0;
};

class GkStore {
  public:
    enum class KeyStatus { OK, ABSENT, ERROR };
    enum class RecordStatus { OK, ABSENT, CORRUPT };

    static constexpr size_t kKeySize = 32;
    static constexpr const char* kKeyName = "gk_key.v1";
    static constexpr const char* kKeyBackupName = "gk_key.v1.bak";

    // refuse_volatile_fs: never create K_dev on tmpfs/ramfs (true on the device).
    explicit GkStore(std::string dir, bool refuse_volatile_fs = true);
    ~GkStore();

    // Loads K_dev into out. create_if_absent: generate it when both copies are absent.
    KeyStatus LoadKey(uint8_t out[kKeySize], bool create_if_absent);

    RecordStatus ReadRecord(uint32_t uid, FailureRecord* rec);
    bool WriteRecord(uint32_t uid, const FailureRecord& rec);
    bool RemoveRecord(uint32_t uid);
    bool RemoveAllRecords();

    const std::string& dir() const { return dir_; }

  private:
    enum class FileStatus { OK, ABSENT, BAD };
    bool OpenDir();
    FileStatus ReadKeyFile(const char* name, uint8_t key[kKeySize]);
    bool WriteFile(const char* name, const uint8_t* data, size_t len, bool no_replace);
    void SyncDir();
    bool IsVolatileFs();

    std::string dir_;
    bool refuse_volatile_fs_;
    int dirfd_ = -1;
};

}  // namespace gbgk
