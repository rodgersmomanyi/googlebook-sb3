// SPDX-License-Identifier: Apache-2.0
#include "shared_secret.h"

#include <string.h>

#include <openssl/cipher.h>
#include <openssl/cmac.h>
#include <openssl/hmac.h>
#include <openssl/mem.h>
#include <openssl/rand.h>

#include "gk_log.h"

namespace gbgk {

namespace {
void put_be32(uint8_t* p, uint32_t v) {
    p[0] = v >> 24;
    p[1] = v >> 16;
    p[2] = v >> 8;
    p[3] = v;
}
}  // namespace

SharedSecretParams SoftSharedSecret::GetParameters() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (nonce_.empty()) {
        nonce_.resize(32);
        if (RAND_bytes(nonce_.data(), nonce_.size()) != 1) {
            GKLOGE("RAND_bytes failed for the shared-secret nonce");
            nonce_.clear();
            return {};
        }
    }
    SharedSecretParams p;
    p.nonce = nonce_;  // seed stays empty
    return p;
}

bool SoftSharedSecret::Ckdf(const uint8_t* key, size_t key_len, const char* label,
                            const std::vector<SharedSecretParams>& context, uint8_t* out,
                            size_t out_len) {
    const EVP_CIPHER* cipher = key_len == 16 ? EVP_aes_128_cbc()
                               : key_len == 32 ? EVP_aes_256_cbc()
                                               : nullptr;
    if (cipher == nullptr || out_len == 0) return false;
    CMAC_CTX* ctx = CMAC_CTX_new();
    if (ctx == nullptr) return false;
    bool ok = CMAC_Init(ctx, key, key_len, cipher, nullptr) == 1;
    const uint32_t blocks = static_cast<uint32_t>((out_len + 15) / 16);
    uint8_t L[4];
    put_be32(L, static_cast<uint32_t>(out_len * 8));
    const uint8_t zero = 0;
    size_t pos = 0;
    for (uint32_t i = 1; ok && i <= blocks; ++i) {
        // i || label || 0x00 || context || L   (i and L: 32-bit big-endian)
        uint8_t I[4];
        put_be32(I, i);
        ok = CMAC_Update(ctx, I, 4) &&
             CMAC_Update(ctx, reinterpret_cast<const uint8_t*>(label), strlen(label)) &&
             CMAC_Update(ctx, &zero, 1);
        for (const auto& p : context) {
            if (ok && !p.seed.empty()) ok = CMAC_Update(ctx, p.seed.data(), p.seed.size());
            if (ok && !p.nonce.empty()) ok = CMAC_Update(ctx, p.nonce.data(), p.nonce.size());
        }
        uint8_t block[16];
        size_t block_len = 0;
        ok = ok && CMAC_Update(ctx, L, 4) && CMAC_Final(ctx, block, &block_len) &&
             block_len == 16;
        if (ok) {
            size_t n = out_len - pos < 16 ? out_len - pos : 16;
            memcpy(out + pos, block, n);
            pos += n;
            ok = CMAC_Reset(ctx) == 1;
        }
        OPENSSL_cleanse(block, sizeof(block));
    }
    CMAC_CTX_free(ctx);
    return ok && pos == out_len;
}

int32_t SoftSharedSecret::Compute(const std::vector<SharedSecretParams>& params,
                                  std::vector<uint8_t>* sharing_check) {
    std::lock_guard<std::mutex> lock(mutex_);
    bool found_mine = false;
    for (const auto& p : params) {
        if (!nonce_.empty() && p.seed.empty() && p.nonce == nonce_) found_mine = true;
    }
    if (!found_mine) {
        GKLOGE("computeSharedSecret: our own parameters are not in the list (%zu entries)",
               params.size());
        return KM_ERROR_INVALID_ARGUMENT;
    }
    static const uint8_t kZeroKey[32] = {};
    uint8_t h[kKeySize];
    if (!Ckdf(kZeroKey, sizeof(kZeroKey), kKeyAgreementLabel, params, h, sizeof(h))) {
        GKLOGE("computeSharedSecret: CKDF failed");
        return KM_ERROR_UNKNOWN_ERROR;
    }
    sharing_check->assign(32, 0);
    unsigned int len = 0;
    if (HMAC(EVP_sha256(), h, sizeof(h), reinterpret_cast<const uint8_t*>(kKeyCheckLabel),
             strlen(kKeyCheckLabel), sharing_check->data(), &len) == nullptr ||
        len != 32) {
        OPENSSL_cleanse(h, sizeof(h));
        return KM_ERROR_UNKNOWN_ERROR;
    }
    memcpy(hmac_key_, h, sizeof(h));
    OPENSSL_cleanse(h, sizeof(h));
    have_key_ = true;
    GKLOGI("shared HMAC key computed with %zu participants", params.size());
    return KM_ERROR_OK;
}

bool SoftSharedSecret::HmacKey(uint8_t out[kKeySize]) const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!have_key_) return false;
    memcpy(out, hmac_key_, kKeySize);
    return true;
}

}  // namespace gbgk
