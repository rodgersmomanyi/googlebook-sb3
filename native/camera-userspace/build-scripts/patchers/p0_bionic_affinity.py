#!/usr/bin/env python3
# P0: bionic has no pthread_setaffinity_np(); use sched_setaffinity() on the thread's kernel TID.
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from patchlib import edit

root = sys.argv[1]

edit(root, "src/libcamera/base/thread.cpp", [
("""	const cpu_set_t &cpuset = data_->cpuset_.value();
	pthread_setaffinity_np(thread_.native_handle(), sizeof(cpuset), &cpuset);
""",
"""	const cpu_set_t &cpuset = data_->cpuset_.value();
#if defined(__ANDROID__)
	/* Bionic has no pthread_setaffinity_np(), use the kernel TID instead. */
	sched_setaffinity(pthread_gettid_np(thread_.native_handle()),
			  sizeof(cpuset), &cpuset);
#else
	pthread_setaffinity_np(thread_.native_handle(), sizeof(cpuset), &cpuset);
#endif
"""),
])

# debayer_cpu.cpp uses utils::secure_getenv() (P1)
edit(root, "src/libcamera/software_isp/debayer_cpu.cpp", [
("""#include <libcamera/base/thread.h>
""",
"""#include <libcamera/base/thread.h>
#include <libcamera/base/utils.h>
"""),
])
print("P0 applied")
