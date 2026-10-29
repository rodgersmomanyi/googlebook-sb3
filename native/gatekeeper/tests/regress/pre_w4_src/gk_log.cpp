// SPDX-License-Identifier: Apache-2.0
#include "gk_log.h"

#include <stdio.h>

#ifdef __ANDROID__
#include <android/log.h>
#endif

static int g_enabled = 1;

void gk_log_set_enabled(int enabled) { g_enabled = enabled; }

void gk_log(int prio, const char* fmt, ...) {
    if (!g_enabled) return;
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
#ifdef __ANDROID__
    __android_log_write(prio, "gb_gatekeeper", buf);
#endif
    fprintf(stderr, "gb_gatekeeper: %s%s\n",
            prio >= GK_LOG_ERROR ? "E " : (prio == GK_LOG_WARN ? "W " : ""), buf);
}
