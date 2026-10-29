// SPDX-License-Identifier: Apache-2.0
//
// Off-device stand-in for the service-manager and thread-pool part of libbinder_ndk. Linked with
// the unchanged src/service_main.cpp (and the rest of the service) into gk_host_service and
// gk_android_service_fake, which test_registration in test_main.cpp runs as child processes.
// It never serves a transaction:
//   AServiceManager_addService     EX_ILLEGAL_ARGUMENT for every instance named in the
//                                  comma-separated $GBGK_FAKE_SM_REFUSE (servicemanager's answer
//                                  for an instance without a VINTF declaration, INFERRED from
//                                  cmds/servicemanager), EX_NONE otherwise
//   ABinderProcess_joinThreadPool  blocks forever, like the real one
// Every call is reported on stdout as "FAKE_SM ..." for the test to read. Objects live in static
// storage, so LeakSanitizer has nothing to report when the service exits.
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "../src/binder_api.h"

struct AIBinder_Class {
    const char* descriptor;
    AIBinder_Class_onCreate on_create;
    AIBinder_Class_onTransact on_transact;
};
struct AIBinder {
    const AIBinder_Class* cls;
    void* user_data;
};

namespace {

constexpr int kMax = 8;
AIBinder_Class g_classes[kMax];
AIBinder g_binders[kMax];
int g_nclasses = 0, g_nbinders = 0;

__attribute__((format(printf, 1, 2))) void say(const char* fmt, ...) {
    char buf[300];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n < 0) return;
    size_t len = static_cast<size_t>(n) < sizeof(buf) ? static_cast<size_t>(n) : sizeof(buf) - 1;
    for (size_t off = 0; off < len;) {
        ssize_t w = write(STDOUT_FILENO, buf + off, len - off);
        if (w <= 0) return;
        off += static_cast<size_t>(w);
    }
}

bool refused(const char* instance) {
    const char* list = getenv("GBGK_FAKE_SM_REFUSE");
    if (list == nullptr || instance == nullptr) return false;
    const size_t n = strlen(instance);
    for (const char* p = list; *p != '\0';) {
        const char* end = strchr(p, ',');
        const size_t len = end ? static_cast<size_t>(end - p) : strlen(p);
        if (len == n && strncmp(p, instance, n) == 0) return true;
        if (end == nullptr) break;
        p = end + 1;
    }
    return false;
}

}  // namespace

extern "C" {

AIBinder_Class* AIBinder_Class_define(const char* interfaceDescriptor,
                                      AIBinder_Class_onCreate onCreate,
                                      AIBinder_Class_onDestroy /* onDestroy */,
                                      AIBinder_Class_onTransact onTransact) {
    if (g_nclasses >= kMax) return nullptr;
    g_classes[g_nclasses] = {interfaceDescriptor, onCreate, onTransact};
    return &g_classes[g_nclasses++];
}

AIBinder* AIBinder_new(const AIBinder_Class* clazz, void* args) {
    if (clazz == nullptr || g_nbinders >= kMax) return nullptr;
    g_binders[g_nbinders] = {clazz, clazz->on_create ? clazz->on_create(args) : args};
    return &g_binders[g_nbinders++];
}

void AIBinder_markVintfStability(AIBinder* /* binder */) {}

uid_t AIBinder_getCallingUid(void) { return 0; }

binder_exception_t AServiceManager_addService(AIBinder* binder, const char* instance) {
    const binder_exception_t ex =
            (binder == nullptr || refused(instance)) ? GB_EX_ILLEGAL_ARGUMENT : GB_EX_NONE;
    say("FAKE_SM addService %s -> %d\n", instance ? instance : "(null)", ex);
    return ex;
}

bool ABinderProcess_setThreadPoolMaxThreadCount(uint32_t numThreads) {
    say("FAKE_SM setThreadPoolMaxThreadCount %u\n", numThreads);
    return true;
}

void ABinderProcess_joinThreadPool(void) {
    say("FAKE_SM joinThreadPool\n");
    for (;;) pause();
}

}  // extern "C"
