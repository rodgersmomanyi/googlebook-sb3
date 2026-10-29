// SPDX-License-Identifier: Apache-2.0
#include "gk_store.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <unistd.h>

#include <openssl/mem.h>
#include <openssl/rand.h>
#include <openssl/sha.h>

#include "gk_log.h"

namespace gbgk {

namespace {

constexpr char kKeyMagic[8] = {'G', 'B', 'G', 'K', 'K', 'E', 'Y', '1'};
constexpr char kRecMagic[8] = {'G', 'B', 'G', 'K', 'F', 'R', '0', '1'};
constexpr size_t kKeyFileSize = 8 + GkStore::kKeySize + SHA256_DIGEST_LENGTH;  // 72
constexpr size_t kRecBody = 8 + 4 + 4 + 8 + 8;                              // 32
constexpr size_t kRecFileSize = kRecBody + SHA256_DIGEST_LENGTH;            // 64
constexpr long kTmpfsMagic = 0x01021994;
constexpr long kRamfsMagic = 0x858458f6;

// Reads up to max bytes; returns the count, or -1 with errno set.
ssize_t read_all(int fd, uint8_t* buf, size_t max) {
    size_t got = 0;
    while (got < max) {
        ssize_t n = read(fd, buf + got, max - got);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (n == 0) break;
        got += static_cast<size_t>(n);
    }
    return static_cast<ssize_t>(got);
}

bool write_all(int fd, const uint8_t* buf, size_t len) {
    while (len > 0) {
        ssize_t n = write(fd, buf, len);
        if (n < 0) {
            if (errno == EINTR) continue;
            return false;
        }
        buf += n;
        len -= static_cast<size_t>(n);
    }
    return true;
}

std::string record_name(uint32_t uid) { return "fr." + std::to_string(uid); }

void put32(uint8_t* p, uint32_t v) { memcpy(p, &v, 4); }
void put64(uint8_t* p, uint64_t v) { memcpy(p, &v, 8); }
uint32_t get32(const uint8_t* p) { uint32_t v; memcpy(&v, p, 4); return v; }
uint64_t get64(const uint8_t* p) { uint64_t v; memcpy(&v, p, 8); return v; }

}  // namespace

GkStore::GkStore(std::string dir, bool refuse_volatile_fs)
    : dir_(std::move(dir)), refuse_volatile_fs_(refuse_volatile_fs) {}

GkStore::~GkStore() {
    if (dirfd_ >= 0) close(dirfd_);
}

bool GkStore::OpenDir() {
    if (dirfd_ >= 0) return true;
    int fd = open(dir_.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        GKLOGE("state dir %s: %s (it is created by init at post-fs-data)", dir_.c_str(),
               strerror(errno));
        return false;
    }
    struct stat st;
    if (fstat(fd, &st) == 0 && (st.st_mode & 077) != 0) {
        GKLOGW("state dir %s has mode %03o; expected 0700", dir_.c_str(), st.st_mode & 0777);
    }
    dirfd_ = fd;
    // Leftovers of an interrupted write are never valid state.
    unlinkat(dirfd_, (std::string(kKeyName) + ".tmp").c_str(), 0);
    unlinkat(dirfd_, (std::string(kKeyBackupName) + ".tmp").c_str(), 0);
    return true;
}

bool GkStore::IsVolatileFs() {
    struct statfs sfs;
    if (fstatfs(dirfd_, &sfs) != 0) return true;  // unknown: treat as volatile (do not create)
    return static_cast<long>(sfs.f_type) == kTmpfsMagic ||
           static_cast<long>(sfs.f_type) == kRamfsMagic;
}

void GkStore::SyncDir() {
    if (dirfd_ >= 0 && fsync(dirfd_) != 0) GKLOGW("fsync(%s): %s", dir_.c_str(), strerror(errno));
}

GkStore::FileStatus GkStore::ReadKeyFile(const char* name, uint8_t key[kKeySize]) {
    int fd = openat(dirfd_, name, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        if (errno == ENOENT) return FileStatus::ABSENT;
        GKLOGE("open %s/%s: %s", dir_.c_str(), name, strerror(errno));
        return FileStatus::BAD;
    }
    uint8_t buf[kKeyFileSize + 1];
    ssize_t n = read_all(fd, buf, sizeof(buf));
    int err = errno;
    close(fd);
    if (n < 0) {
        GKLOGE("read %s/%s: %s", dir_.c_str(), name, strerror(err));
        return FileStatus::BAD;
    }
    if (static_cast<size_t>(n) != kKeyFileSize || memcmp(buf, kKeyMagic, 8) != 0) {
        GKLOGE("%s/%s: bad size %zd or magic", dir_.c_str(), name, n);
        OPENSSL_cleanse(buf, sizeof(buf));
        return FileStatus::BAD;
    }
    uint8_t digest[SHA256_DIGEST_LENGTH];
    SHA256(buf, 8 + kKeySize, digest);
    if (CRYPTO_memcmp(digest, buf + 8 + kKeySize, SHA256_DIGEST_LENGTH) != 0) {
        GKLOGE("%s/%s: checksum mismatch", dir_.c_str(), name);
        OPENSSL_cleanse(buf, sizeof(buf));
        return FileStatus::BAD;
    }
    memcpy(key, buf + 8, kKeySize);
    OPENSSL_cleanse(buf, sizeof(buf));
    return FileStatus::OK;
}

bool GkStore::WriteFile(const char* name, const uint8_t* data, size_t len, bool no_replace) {
    std::string tmp = std::string(name) + ".tmp";
    unlinkat(dirfd_, tmp.c_str(), 0);
    int fd = openat(dirfd_, tmp.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW,
                    0600);
    if (fd < 0) {
        GKLOGE("create %s/%s: %s", dir_.c_str(), tmp.c_str(), strerror(errno));
        return false;
    }
    bool ok = write_all(fd, data, len) && fsync(fd) == 0;
    int err = errno;
    close(fd);
    if (!ok) {
        GKLOGE("write %s/%s: %s", dir_.c_str(), tmp.c_str(), strerror(err));
        unlinkat(dirfd_, tmp.c_str(), 0);
        return false;
    }
    if (no_replace) {
        ok = linkat(dirfd_, tmp.c_str(), dirfd_, name, 0) == 0;
        err = errno;
        unlinkat(dirfd_, tmp.c_str(), 0);
    } else {
        ok = renameat(dirfd_, tmp.c_str(), dirfd_, name) == 0;
        err = errno;
        if (!ok) unlinkat(dirfd_, tmp.c_str(), 0);
    }
    if (!ok) {
        GKLOGE("install %s/%s: %s", dir_.c_str(), name, strerror(err));
        return false;
    }
    SyncDir();
    return true;
}

GkStore::KeyStatus GkStore::LoadKey(uint8_t out[kKeySize], bool create_if_absent) {
    if (!OpenDir()) return KeyStatus::ERROR;
    uint8_t primary[kKeySize], backup[kKeySize];
    FileStatus p = ReadKeyFile(kKeyName, primary);
    FileStatus b = ReadKeyFile(kKeyBackupName, backup);
    KeyStatus result = KeyStatus::ERROR;
    uint8_t file[kKeyFileSize];
    auto encode = [&file](const uint8_t key[kKeySize]) {
        memcpy(file, kKeyMagic, 8);
        memcpy(file + 8, key, kKeySize);
        SHA256(file, 8 + kKeySize, file + 8 + kKeySize);
    };

    if (p == FileStatus::OK) {
        if (b != FileStatus::OK || CRYPTO_memcmp(primary, backup, kKeySize) != 0) {
            GKLOGW("key backup %s, rewriting it from the primary copy",
                   b == FileStatus::ABSENT ? "missing" : "damaged or different");
            encode(primary);
            WriteFile(kKeyBackupName, file, sizeof(file), /*no_replace=*/false);
        }
        memcpy(out, primary, kKeySize);
        result = KeyStatus::OK;
    } else if (b == FileStatus::OK) {
        GKLOGW("key primary copy %s, restoring it from the backup",
               p == FileStatus::ABSENT ? "missing" : "damaged");
        encode(backup);
        WriteFile(kKeyName, file, sizeof(file), /*no_replace=*/false);
        memcpy(out, backup, kKeySize);
        result = KeyStatus::OK;
    } else if (p == FileStatus::ABSENT && b == FileStatus::ABSENT) {
        if (!create_if_absent) {
            result = KeyStatus::ABSENT;
        } else if (refuse_volatile_fs_ && IsVolatileFs()) {
            GKLOGE("%s is on tmpfs/ramfs (or statfs failed): not creating a password key there",
                   dir_.c_str());
        } else {
            uint8_t key[kKeySize];
            if (RAND_bytes(key, sizeof(key)) != 1) {
                GKLOGE("RAND_bytes failed");
            } else {
                encode(key);
                if (WriteFile(kKeyName, file, sizeof(file), /*no_replace=*/true)) {
                    WriteFile(kKeyBackupName, file, sizeof(file), /*no_replace=*/false);
                    // Read back what is on disk: the key in use must be the persisted one.
                    if (ReadKeyFile(kKeyName, primary) == FileStatus::OK &&
                        CRYPTO_memcmp(primary, key, kKeySize) == 0) {
                        GKLOGI("created the device password key in %s", dir_.c_str());
                        memcpy(out, key, kKeySize);
                        result = KeyStatus::OK;
                    } else {
                        GKLOGE("password key read-back failed");
                    }
                }
            }
            OPENSSL_cleanse(key, sizeof(key));
        }
    } else {
        GKLOGE("password key unusable (primary %s, backup %s): refusing to create a new one; "
               "enrolled credentials cannot be verified (recovery: factory reset)",
               p == FileStatus::ABSENT ? "absent" : "damaged",
               b == FileStatus::ABSENT ? "absent" : "damaged");
    }
    OPENSSL_cleanse(primary, sizeof(primary));
    OPENSSL_cleanse(backup, sizeof(backup));
    OPENSSL_cleanse(file, sizeof(file));
    return result;
}

GkStore::RecordStatus GkStore::ReadRecord(uint32_t uid, FailureRecord* rec) {
    *rec = FailureRecord();
    if (!OpenDir()) return RecordStatus::CORRUPT;
    std::string name = record_name(uid);
    int fd = openat(dirfd_, name.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        if (errno == ENOENT) return RecordStatus::ABSENT;
        GKLOGW("open %s: %s", name.c_str(), strerror(errno));
        return RecordStatus::CORRUPT;
    }
    uint8_t buf[kRecFileSize + 1];
    ssize_t n = read_all(fd, buf, sizeof(buf));
    close(fd);
    bool valid = n == static_cast<ssize_t>(kRecFileSize) && memcmp(buf, kRecMagic, 8) == 0 &&
                 get32(buf + 8) == uid;
    if (valid) {
        uint8_t digest[SHA256_DIGEST_LENGTH];
        SHA256(buf, kRecBody, digest);
        valid = CRYPTO_memcmp(digest, buf + kRecBody, SHA256_DIGEST_LENGTH) == 0;
    }
    if (!valid) {
        GKLOGW("failure record %s is damaged; treating it as no failures", name.c_str());
        return RecordStatus::CORRUPT;
    }
    rec->failure_counter = get32(buf + 12);
    rec->secure_user_id = get64(buf + 16);
    rec->last_checked_timestamp = get64(buf + 24);
    return RecordStatus::OK;
}

bool GkStore::WriteRecord(uint32_t uid, const FailureRecord& rec) {
    if (!OpenDir()) return false;
    uint8_t buf[kRecFileSize];
    memcpy(buf, kRecMagic, 8);
    put32(buf + 8, uid);
    put32(buf + 12, rec.failure_counter);
    put64(buf + 16, rec.secure_user_id);
    put64(buf + 24, rec.last_checked_timestamp);
    SHA256(buf, kRecBody, buf + kRecBody);
    return WriteFile(record_name(uid).c_str(), buf, sizeof(buf), /*no_replace=*/false);
}

bool GkStore::RemoveRecord(uint32_t uid) {
    if (!OpenDir()) return false;
    if (unlinkat(dirfd_, record_name(uid).c_str(), 0) != 0 && errno != ENOENT) return false;
    SyncDir();
    return true;
}

bool GkStore::RemoveAllRecords() {
    if (!OpenDir()) return false;
    int fd = openat(dirfd_, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) return false;
    DIR* d = fdopendir(fd);
    if (d == nullptr) {
        close(fd);
        return false;
    }
    bool ok = true;
    while (struct dirent* e = readdir(d)) {
        if (strncmp(e->d_name, "fr.", 3) == 0 && unlinkat(dirfd_, e->d_name, 0) != 0 &&
            errno != ENOENT) {
            ok = false;
        }
    }
    closedir(d);
    SyncDir();
    return ok;
}

}  // namespace gbgk
