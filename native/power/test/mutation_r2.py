#!/usr/bin/env python3
"""Mutation check for the review fix pass: each mutation reverts one fix in a copy of gb_power.c; the unit tests must
fail in the groups named. Run on the builder: python3 -I gp_mut.py <src dir> <work dir>."""
import os
import re
import shutil
import subprocess
import sys

SRC, WORK = sys.argv[1], sys.argv[2]
MUTS = [
    ("R-1 term_handle", '  if (pc[0]) shutdown_markers("SIGTERM during shutdown");\n', ''),
    ("R-2 rebind intel", '      if (!bound && (g_gfx.mode == GM_INTEL || g_rb.off_seen)) { rb_end("unbound by off (Intel mode)", 0); return; }\n', ''),
    ("R-3 reconcile_on", '  reconcile_on(t);                                /* (R-3) after the hot attach (which counts against the same 3) */\n', ''),
    ("R-4 dgpu_session", '  dsess_eval();                                   /* (R-4) */\n', ''),
    ("R-5 node clamp", '  int noraise = nodes_enforce_skip();\n', '  if (nodes_enforce_skip()) return;\n  int noraise = 0;\n'),
    ("R-5 wedge 000", '    int k = nodes_lock();\n', '    return;\n    int k = nodes_lock();\n'),
    ("R-6 compute-start", '  if (load_window_open() || !strcmp(st, "loading")) { reply_err(c, "compute-start", "EAGAIN", "NVIDIA driver starting"); return; }\n', ''),
    ("R-6 hold gate", '!dec_running() && !load_window_open()) {', '!dec_running()) {'),
    ("R-8 fault intel", '    if (stack_present() && gfx_mode_get() != GM_INTEL) {\n', '    if (0) {\n'),
    ("R-9 N08", '  if (g_gfx.ui_notify && g_hold.n > 0 && path_exists("/odm/app/GbGpu/GbGpu.apk"))\n', '  if (g_gfx.ui_notify && path_exists("/odm/app/GbGpu/GbGpu.apk"))\n'),
    ("R-12 unmark compare", '        if (strcmp(cur, val)) return -ESTALE;\n', ''),
    ("R-12 resume clear", '    pend_clear("system resumed");', '    (void)0;'),
    ("R-15 persist gate", 'static int persist_ready(void) { return prop_is("ro.persistent_properties.ready", "true") || boot_completed(); }', 'static int persist_ready(void) { return 1; }'),
]
base = open(os.path.join(SRC, "gb_power.c"), encoding="utf-8").read()
rc_all = 0
for name, old, new in MUTS:
    if base.count(old) != 1:
        print("%-22s SETUP-ERROR (pattern count %d)" % (name, base.count(old)))
        rc_all = 1
        continue
    d = os.path.join(WORK, re.sub(r"[^A-Za-z0-9]+", "_", name))
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(d, "test"))
    shutil.copytree(os.path.join(SRC, "uapi"), os.path.join(d, "uapi"))
    for f in ("test_gb_power.c", "test_gfx.c"):
        shutil.copy(os.path.join(SRC, "test", f), os.path.join(d, "test", f))
    open(os.path.join(d, "gb_power.c"), "w", encoding="utf-8").write(base.replace(old, new))
    b = subprocess.run(["nice", "-n", "19", "cc", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-format-truncation",
                        "-Wno-unused-variable", "-Wno-unused-function", "-Iuapi", "-o", "test/t", "test/test_gb_power.c",
                        "-lm"], cwd=d, capture_output=True, text=True)
    if b.returncode:
        print("%-22s BUILD-ERROR %s" % (name, b.stderr.strip().splitlines()[-1:]))
        rc_all = 1
        continue
    r = subprocess.run(["nice", "-n", "19", "./test/t", os.path.join(d, "tw")], cwd=d, capture_output=True, text=True)
    groups = [l.split(":")[0] for l in r.stdout.splitlines() if re.search(r"checks, [1-9][0-9]* failures", l)
              and not l.startswith(("wave-1", "v1.2", "review-fix")) and "checks" in l]
    killed = r.returncode != 0
    print("%-22s %s rc=%d failing groups: %s" % (name, "KILLED" if killed else "SURVIVED", r.returncode,
                                                  " ".join(groups) or "-"))
    if not killed:
        rc_all = 1
    shutil.rmtree(d, ignore_errors=True)
sys.exit(rc_all)
