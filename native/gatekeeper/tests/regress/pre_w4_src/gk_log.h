// SPDX-License-Identifier: Apache-2.0
//
// Logging for gb_gatekeeper: logcat (tag "gb_gatekeeper") on Android, stderr everywhere (init's
// stdio_to_kmsg puts stderr into the kernel log, the convention of the other gb services).
// Never log a password, a password handle, a key or an auth token MAC.
#pragma once

#include <stdarg.h>

#ifdef __cplusplus
extern "C" {
#endif

enum { GK_LOG_INFO = 4, GK_LOG_WARN = 5, GK_LOG_ERROR = 6 };

void gk_log(int prio, const char* fmt, ...) __attribute__((format(printf, 2, 3)));
// Tests silence the logger (0) or turn it back on (1).
void gk_log_set_enabled(int enabled);

#ifdef __cplusplus
}
#endif

#define GKLOGI(...) gk_log(GK_LOG_INFO, __VA_ARGS__)
#define GKLOGW(...) gk_log(GK_LOG_WARN, __VA_ARGS__)
#define GKLOGE(...) gk_log(GK_LOG_ERROR, __VA_ARGS__)
