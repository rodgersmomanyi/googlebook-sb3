// SPDX-License-Identifier: Apache-2.0
//
// Link-time stand-in for the LLNDK libbinder_ndk.so (soname libbinder_ndk.so): it only provides
// the symbol names and their version nodes (libbinder_ndk_stub.map, as exported by the lapis
// /system/lib64/libbinder_ndk.so: LIBBINDER_NDK, LIBBINDER_NDK30, LIBBINDER_NDK31), so the
// binary records the same NEEDED + version requirements as one linked against the real library.
// It is never installed; at run time the dynamic linker binds to the device's libbinder_ndk.so.

#define STUB(name) \
    void name(void); \
    void name(void) { __builtin_trap(); }

STUB(AIBinder_Class_define)
STUB(AIBinder_new)
STUB(AIBinder_markVintfStability)
STUB(AIBinder_getCallingUid)
STUB(AServiceManager_addService)
STUB(ABinderProcess_setThreadPoolMaxThreadCount)
STUB(ABinderProcess_joinThreadPool)
STUB(AParcel_readInt32)
STUB(AParcel_readInt64)
STUB(AParcel_readByteArray)
STUB(AParcel_writeInt32)
STUB(AParcel_writeInt64)
STUB(AParcel_writeByteArray)
STUB(AParcel_writeString)
STUB(AParcel_getDataPosition)
STUB(AParcel_setDataPosition)
STUB(AParcel_writeStatusHeader)
STUB(AParcel_markSensitive)
STUB(AStatus_newOk)
STUB(AStatus_fromExceptionCode)
STUB(AStatus_fromServiceSpecificError)
STUB(AStatus_delete)
