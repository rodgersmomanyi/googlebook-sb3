// SPDX-License-Identifier: Apache-2.0
//
// The subset of the libbinder_ndk C API (an LLNDK library, stable across Android releases) that
// gb_gatekeeper uses. Declared here instead of including the NDK headers because three of the
// functions (AServiceManager_addService, ABinderProcess_*, AIBinder_markVintfStability) are
// platform/LLNDK-only and not in the public NDK, and because the host unit tests link the same
// service code against a fake parcel (tests/fake_binder.cpp).
//
// The prototypes follow frameworks/native/libs/binder/ndk/include_{ndk,platform}/android/*.h
// (android17-release). abi_check.cpp re-declares them next to the NDK headers in the Android build,
// so a mismatch is a compile error.
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct AIBinder AIBinder;
typedef struct AIBinder_Class AIBinder_Class;
typedef struct AParcel AParcel;
typedef struct AStatus AStatus;
typedef int32_t binder_status_t;
typedef int32_t binder_exception_t;
typedef uint32_t transaction_code_t;

// binder_status_t values (android/binder_status.h)
enum {
    GB_STATUS_OK = 0,
    GB_STATUS_UNKNOWN_ERROR = (-2147483647 - 1),
    GB_STATUS_NO_MEMORY = -12,
    GB_STATUS_BAD_VALUE = -22,
    GB_STATUS_UNKNOWN_TRANSACTION = -74,
    GB_STATUS_UNEXPECTED_NULL = -2147483640,  // UNKNOWN_ERROR + 8
};
// binder_exception_t values
enum {
    GB_EX_NONE = 0,
    GB_EX_SECURITY = -1,
    GB_EX_ILLEGAL_ARGUMENT = -3,
    GB_EX_ILLEGAL_STATE = -5,
    GB_EX_SERVICE_SPECIFIC = -8,
};

typedef void* (*AIBinder_Class_onCreate)(void* args);
typedef void (*AIBinder_Class_onDestroy)(void* userData);
typedef binder_status_t (*AIBinder_Class_onTransact)(AIBinder* binder, transaction_code_t code,
                                                     const AParcel* in, AParcel* out);
typedef bool (*AParcel_byteArrayAllocator)(void* arrayData, int32_t length, int8_t** outBuffer);

AIBinder_Class* AIBinder_Class_define(const char* interfaceDescriptor,
                                      AIBinder_Class_onCreate onCreate,
                                      AIBinder_Class_onDestroy onDestroy,
                                      AIBinder_Class_onTransact onTransact);
AIBinder* AIBinder_new(const AIBinder_Class* clazz, void* args);
void AIBinder_markVintfStability(AIBinder* binder);
uid_t AIBinder_getCallingUid(void);
binder_exception_t AServiceManager_addService(AIBinder* binder, const char* instance);
bool ABinderProcess_setThreadPoolMaxThreadCount(uint32_t numThreads);
void ABinderProcess_joinThreadPool(void);

binder_status_t AParcel_readInt32(const AParcel* parcel, int32_t* value);
binder_status_t AParcel_readInt64(const AParcel* parcel, int64_t* value);
binder_status_t AParcel_readByteArray(const AParcel* parcel, void* arrayData,
                                      AParcel_byteArrayAllocator allocator);
binder_status_t AParcel_writeInt32(AParcel* parcel, int32_t value);
binder_status_t AParcel_writeInt64(AParcel* parcel, int64_t value);
binder_status_t AParcel_writeByteArray(AParcel* parcel, const int8_t* arrayData, int32_t length);
binder_status_t AParcel_writeString(AParcel* parcel, const char* string, int32_t length);
int32_t AParcel_getDataPosition(const AParcel* parcel);
binder_status_t AParcel_setDataPosition(const AParcel* parcel, int32_t position);
binder_status_t AParcel_writeStatusHeader(AParcel* parcel, const AStatus* status);
void AParcel_markSensitive(const AParcel* parcel);

AStatus* AStatus_newOk(void);
AStatus* AStatus_fromExceptionCode(binder_exception_t exception);
AStatus* AStatus_fromServiceSpecificError(int32_t serviceSpecific);
void AStatus_delete(AStatus* status);

#ifdef __cplusplus
}
#endif
