// SPDX-License-Identifier: Apache-2.0
//
// Host-side stand-in for the parts of libbinder_ndk that aidl_service.cpp uses, with the byte
// layout of android::Parcel (frameworks/native/libs/binder/Parcel.cpp): little-endian int32 and
// int64, arrays as int32 length + data padded to 4 bytes, strings as UTF-16 String16 (length,
// chars, NUL, padding), status headers as Status::writeToParcel writes them.
#pragma once

#include <stdint.h>

#include <string>
#include <vector>

#include "../src/binder_api.h"

struct AParcel {
    std::vector<uint8_t> data;
    size_t pos = 0;
};

namespace fake {

AParcel* NewParcel();
void DeleteParcel(AParcel* p);
void Rewind(const AParcel* p);  // position 0, for reading what was written

// Independent decoders used by the tests (they do not share code with aidl_service.cpp).
struct Reader {
    const AParcel* p;
    bool ok = true;
    int32_t I32();
    int64_t I64();
    std::vector<uint8_t> Bytes();  // int32 length + padded data; -1 -> empty, flags !ok
    std::string String16();
    // Status header: returns the exception code; for EX_SERVICE_SPECIFIC, *code gets the value.
    int32_t Status(int32_t* code);
    // Parcelable framing: checks the non-null marker, returns the end position.
    size_t BeginParcelable();
    void EndParcelable(size_t end);  // checks that the fields consumed exactly the declared size
};

}  // namespace fake
