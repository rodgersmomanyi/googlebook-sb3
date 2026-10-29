// SPDX-License-Identifier: Apache-2.0
//
// android.hardware.gatekeeper-service.gb: software Gatekeeper HAL (IGatekeeper/default) plus its
// ISharedSecret/gatekeeper participant, for Googlebook OS on VMware and on the Surface Book 3,
// where the Trusty/GSC-backed Gatekeeper cannot run. A convenience lock, not a hardware security
// boundary: the key and the throttling state live in /data/vendor/gatekeeper.
//
//   android.hardware.gatekeeper-service.gb [--state-dir DIR]   serve (init starts it this way)
//   android.hardware.gatekeeper-service.gb --selftest          crypto known-answer tests, exit 0/1
//   android.hardware.gatekeeper-service.gb --version
//
// Serving never returns; exit status 3 = IGatekeeper/default could not be registered (W4-GK-3),
// 2 = usage error.
//
// Links only LLNDK libraries (libc, libm, libdl, liblog, libbinder_ndk); BoringSSL and libc++
// are static, so a lapis OTA that changes /vendor libraries cannot break it.
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "aidl_service.h"
#include "binder_api.h"
#include "gb_gatekeeper.h"
#include "gk_log.h"
#include "gk_store.h"
#include "shared_secret.h"

#ifndef GBGK_VERSION
#define GBGK_VERSION "dev"
#endif

namespace {

// Exit status when IGatekeeper/default could not be registered (W4-GK-3). The service is not
// oneshot, so init restarts it (with its usual delay); the failure shows as a crash loop
// ("... exited with status 3" in the kernel log) instead of a silent boot hang, and a transient
// servicemanager refusal heals at a later start.
constexpr int kExitNotRegistered = 3;

gbgk::GatekeeperService* g_service = nullptr;

void* on_create(void* args) { return args; }
void on_destroy(void*) {}

binder_status_t on_gatekeeper(AIBinder*, transaction_code_t code, const AParcel* in,
                              AParcel* out) {
    return g_service->OnGatekeeperTransact(code, in, out, AIBinder_getCallingUid());
}

binder_status_t on_shared_secret(AIBinder*, transaction_code_t code, const AParcel* in,
                                 AParcel* out) {
    return g_service->OnSharedSecretTransact(code, in, out, AIBinder_getCallingUid());
}

bool register_service(const char* descriptor, const char* instance,
                      AIBinder_Class_onTransact transact) {
    AIBinder_Class* cls = AIBinder_Class_define(descriptor, on_create, on_destroy, transact);
    if (cls == nullptr) {
        GKLOGE("AIBinder_Class_define(%s) failed", descriptor);
        return false;
    }
    AIBinder* binder = AIBinder_new(cls, nullptr);
    if (binder == nullptr) return false;
    AIBinder_markVintfStability(binder);
    for (int attempt = 1; attempt <= 5; attempt++) {
        binder_exception_t ex = AServiceManager_addService(binder, instance);
        if (ex == GB_EX_NONE) {
            GKLOGI("registered %s", instance);
            return true;  // the binder stays referenced for the life of the process
        }
        GKLOGE("addService(%s) failed: exception %d (attempt %d; is it declared in VINTF?)",
               instance, ex, attempt);
        sleep(1);
    }
    return false;
}

}  // namespace

int main(int argc, char** argv) {
    const char* state_dir = "/data/vendor/gatekeeper";
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--version") == 0) {
            printf("gb_gatekeeper %s\n", GBGK_VERSION);
            return 0;
        }
        if (strcmp(argv[i], "--selftest") == 0) {
            bool ok = gbgk::GbGateKeeper::SelfTest();
            printf("gb_gatekeeper selftest: %s\n", ok ? "PASS" : "FAIL");
            return ok ? 0 : 1;
        }
        if (strcmp(argv[i], "--state-dir") == 0 && i + 1 < argc) {
            state_dir = argv[++i];
            continue;
        }
        fprintf(stderr, "usage: %s [--state-dir DIR] | --selftest | --version\n", argv[0]);
        return 2;
    }

    const bool selftest_ok = gbgk::GbGateKeeper::SelfTest();
    if (!selftest_ok) {
        // Still register: an absent IGatekeeper would hang gatekeeperd (waitForService) and with
        // it LockSettingsService. Enroll/verify then fail cleanly instead.
        GKLOGE("crypto self-test FAILED: enroll and verify will be refused");
    }
    GKLOGI("gb_gatekeeper %s starting, state in %s", GBGK_VERSION, state_dir);

    static gbgk::GkStore store(state_dir);  // opened lazily, at the first enroll/verify
    static gbgk::SoftSharedSecret shared_secret;
    static gbgk::GbGateKeeper gatekeeper(&store, &shared_secret);
    static gbgk::GatekeeperService service(&gatekeeper, &shared_secret, selftest_ok);
    g_service = &service;

    ABinderProcess_setThreadPoolMaxThreadCount(0);
    // IGatekeeper/default first: gatekeeperd, and with it LockSettingsService, waits for it
    // forever, so without it this process is useless and must not idle (W4-GK-3: idling in
    // joinThreadPool was a silent boot hang, since init restarts only a service that exited).
    // Registering it first also means a process that is about to exit never offers
    // ISharedSecret/gatekeeper to keystore2's negotiation.
    if (!register_service(gbgk::kGatekeeperDescriptor, gbgk::kGatekeeperInstance,
                          on_gatekeeper)) {
        GKLOGE("%s not registered: exiting with status %d so that init restarts the service "
               "(gatekeeperd and LockSettingsService wait for this instance)",
               gbgk::kGatekeeperInstance, kExitNotRegistered);
        return kExitNotRegistered;
    }
    if (!register_service(gbgk::kSharedSecretDescriptor, gbgk::kSharedSecretInstance,
                          on_shared_secret)) {
        // Not fatal: keystore2 keeps retrying in a background thread, auth tokens keep the zero
        // MAC key (which our KeyMint does not check). Exiting would take IGatekeeper down too.
        GKLOGE("%s not registered: serving %s only", gbgk::kSharedSecretInstance,
               gbgk::kGatekeeperInstance);
    }
    ABinderProcess_joinThreadPool();
    return 1;
}
