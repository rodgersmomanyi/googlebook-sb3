// SPDX-License-Identifier: Apache-2.0
//
// Compile-only check (Android build): our binder_api.h prototypes must be identical to the NDK
// headers and to the platform headers of frameworks/native (android17-release, copied to
// third_party/aosp/libbinder_ndk_platform). A differing redeclaration of an extern "C" function
// is a compile error, so this file failing to compile means binder_api.h is wrong.
#include <android/binder_ibinder.h>
#include <android/binder_parcel.h>
#include <android/binder_status.h>

#include <android/binder_manager.h>
#include <android/binder_process.h>
#include <android/binder_stability.h>

#include "binder_api.h"

static_assert(GB_STATUS_OK == STATUS_OK, "STATUS_OK");
static_assert(GB_STATUS_NO_MEMORY == STATUS_NO_MEMORY, "STATUS_NO_MEMORY");
static_assert(GB_STATUS_BAD_VALUE == STATUS_BAD_VALUE, "STATUS_BAD_VALUE");
static_assert(GB_STATUS_UNKNOWN_TRANSACTION == STATUS_UNKNOWN_TRANSACTION, "UNKNOWN_TRANSACTION");
static_assert(GB_STATUS_UNEXPECTED_NULL == STATUS_UNEXPECTED_NULL, "STATUS_UNEXPECTED_NULL");
static_assert(GB_EX_NONE == EX_NONE, "EX_NONE");
static_assert(GB_EX_SECURITY == EX_SECURITY, "EX_SECURITY");
static_assert(GB_EX_ILLEGAL_ARGUMENT == EX_ILLEGAL_ARGUMENT, "EX_ILLEGAL_ARGUMENT");
static_assert(GB_EX_ILLEGAL_STATE == EX_ILLEGAL_STATE, "EX_ILLEGAL_STATE");
static_assert(GB_EX_SERVICE_SPECIFIC == EX_SERVICE_SPECIFIC, "EX_SERVICE_SPECIFIC");
