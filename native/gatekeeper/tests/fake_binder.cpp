// SPDX-License-Identifier: Apache-2.0
#include "fake_binder.h"

#include <string.h>

struct AStatus {
    int32_t exception = 0;
    int32_t service_specific = 0;
};

namespace {

constexpr binder_status_t kNotEnoughData = -61;  // -ENODATA

void put(AParcel* p, const void* v, size_t n) {
    if (p->pos + n > p->data.size()) p->data.resize(p->pos + n);
    memcpy(p->data.data() + p->pos, v, n);
    p->pos += n;
}

bool get(const AParcel* p, void* v, size_t n) {
    AParcel* q = const_cast<AParcel*>(p);
    if (q->pos + n > q->data.size()) return false;
    memcpy(v, q->data.data() + q->pos, n);
    q->pos += n;
    return true;
}

size_t pad4(size_t n) { return (n + 3) & ~static_cast<size_t>(3); }

}  // namespace

extern "C" {

binder_status_t AParcel_readInt32(const AParcel* parcel, int32_t* value) {
    return get(parcel, value, 4) ? GB_STATUS_OK : kNotEnoughData;
}
binder_status_t AParcel_readInt64(const AParcel* parcel, int64_t* value) {
    return get(parcel, value, 8) ? GB_STATUS_OK : kNotEnoughData;
}
binder_status_t AParcel_readByteArray(const AParcel* parcel, void* arrayData,
                                      AParcel_byteArrayAllocator allocator) {
    int32_t len;
    if (!get(parcel, &len, 4)) return kNotEnoughData;
    if (len < -1) return GB_STATUS_BAD_VALUE;
    if (len > 0 && parcel->pos + static_cast<size_t>(len) > parcel->data.size())
        return GB_STATUS_NO_MEMORY;
    int8_t* out = nullptr;
    if (!allocator(arrayData, len, &out)) return len < 0 ? GB_STATUS_UNEXPECTED_NULL : GB_STATUS_NO_MEMORY;
    if (len <= 0) return GB_STATUS_OK;
    if (out == nullptr) return GB_STATUS_NO_MEMORY;
    AParcel* q = const_cast<AParcel*>(parcel);
    memcpy(out, q->data.data() + q->pos, static_cast<size_t>(len));
    q->pos += pad4(static_cast<size_t>(len));
    if (q->pos > q->data.size()) q->pos = q->data.size();
    return GB_STATUS_OK;
}
binder_status_t AParcel_writeInt32(AParcel* parcel, int32_t value) {
    put(parcel, &value, 4);
    return GB_STATUS_OK;
}
binder_status_t AParcel_writeInt64(AParcel* parcel, int64_t value) {
    put(parcel, &value, 8);
    return GB_STATUS_OK;
}
binder_status_t AParcel_writeByteArray(AParcel* parcel, const int8_t* arrayData, int32_t length) {
    if (length < -1 || (arrayData == nullptr && length > 0) || (arrayData != nullptr && length < 0))
        return GB_STATUS_BAD_VALUE;
    put(parcel, &length, 4);
    if (length > 0) {
        put(parcel, arrayData, static_cast<size_t>(length));
        static const uint8_t zeros[4] = {};
        put(parcel, zeros, pad4(static_cast<size_t>(length)) - static_cast<size_t>(length));
    }
    return GB_STATUS_OK;
}
binder_status_t AParcel_writeString(AParcel* parcel, const char* string, int32_t length) {
    if (string == nullptr) {
        int32_t m1 = -1;
        put(parcel, &m1, 4);
        return GB_STATUS_OK;
    }
    put(parcel, &length, 4);  // ASCII only in these tests: one UTF-16 unit per byte
    std::vector<uint8_t> u16;
    for (int32_t i = 0; i < length; i++) {
        u16.push_back(static_cast<uint8_t>(string[i]));
        u16.push_back(0);
    }
    u16.push_back(0);
    u16.push_back(0);
    while (u16.size() % 4) u16.push_back(0);
    put(parcel, u16.data(), u16.size());
    return GB_STATUS_OK;
}
int32_t AParcel_getDataPosition(const AParcel* parcel) { return static_cast<int32_t>(parcel->pos); }
binder_status_t AParcel_setDataPosition(const AParcel* parcel, int32_t position) {
    if (position < 0) return GB_STATUS_BAD_VALUE;
    const_cast<AParcel*>(parcel)->pos = static_cast<size_t>(position);
    return GB_STATUS_OK;
}
binder_status_t AParcel_writeStatusHeader(AParcel* parcel, const AStatus* status) {
    AParcel_writeInt32(parcel, status->exception);
    if (status->exception == GB_EX_NONE) return GB_STATUS_OK;
    AParcel_writeInt32(parcel, -1);  // message: null String16
    AParcel_writeInt32(parcel, 0);   // remote stack trace header
    if (status->exception == GB_EX_SERVICE_SPECIFIC)
        AParcel_writeInt32(parcel, status->service_specific);
    return GB_STATUS_OK;
}
void AParcel_markSensitive(const AParcel*) {}

AStatus* AStatus_newOk(void) { return new AStatus(); }
AStatus* AStatus_fromExceptionCode(binder_exception_t exception) {
    AStatus* s = new AStatus();
    s->exception = exception;
    return s;
}
AStatus* AStatus_fromServiceSpecificError(int32_t serviceSpecific) {
    AStatus* s = new AStatus();
    s->exception = GB_EX_SERVICE_SPECIFIC;
    s->service_specific = serviceSpecific;
    return s;
}
void AStatus_delete(AStatus* status) { delete status; }

}  // extern "C"

namespace fake {

AParcel* NewParcel() { return new AParcel(); }
void DeleteParcel(AParcel* p) { delete p; }
void Rewind(const AParcel* p) { const_cast<AParcel*>(p)->pos = 0; }

int32_t Reader::I32() {
    int32_t v = 0;
    if (!get(p, &v, 4)) ok = false;
    return v;
}
int64_t Reader::I64() {
    int64_t v = 0;
    if (!get(p, &v, 8)) ok = false;
    return v;
}
std::vector<uint8_t> Reader::Bytes() {
    int32_t len = I32();
    std::vector<uint8_t> v;
    if (len < 0) {
        ok = false;
        return v;
    }
    v.resize(static_cast<size_t>(len));
    if (len > 0 && !get(p, v.data(), v.size())) ok = false;
    size_t skip = pad4(v.size()) - v.size();
    AParcel* q = const_cast<AParcel*>(p);
    q->pos += skip;
    return v;
}
std::string Reader::String16() {
    int32_t len = I32();
    std::string s;
    if (len < 0) return s;
    for (int32_t i = 0; i < len; i++) {
        uint16_t c = 0;
        if (!get(p, &c, 2)) ok = false;
        s.push_back(static_cast<char>(c));
    }
    uint16_t nul = 1;
    if (!get(p, &nul, 2) || nul != 0) ok = false;
    const_cast<AParcel*>(p)->pos = pad4(p->pos);
    return s;
}
int32_t Reader::Status(int32_t* code) {
    int32_t ex = I32();
    if (code) *code = 0;
    if (ex == GB_EX_NONE) return ex;
    I32();  // message
    I32();  // stack trace header
    if (ex == GB_EX_SERVICE_SPECIFIC) {
        int32_t c = I32();
        if (code) *code = c;
    }
    return ex;
}
size_t Reader::BeginParcelable() {
    if (I32() != 1) ok = false;  // non-null marker
    size_t start = p->pos;
    int32_t size = I32();
    if (size < 4) ok = false;
    return start + static_cast<size_t>(size < 0 ? 0 : size);
}
void Reader::EndParcelable(size_t end) {
    if (p->pos != end) ok = false;
}

}  // namespace fake
