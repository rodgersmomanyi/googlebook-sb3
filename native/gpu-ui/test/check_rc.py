#!/usr/bin/env python3
"""rc checks for gb_gpu_ui.rc (python3 -I).

  python3 -I check_rc.py <gb_gpu_ui.rc> [<INTERFACES.md>]

1. Lint: every service has user, seclabel and oneshot/disabled; no service runs as root; every `start` names a
   service defined in the file; only the documented trigger is used.
2. With INTERFACES.md: the file is byte-identical to the fenced block of section 15.4 (plus the final newline).
"""
import re
import sys

FAILS = []
CHECKS = [0]


def check(ok, what):
    CHECKS[0] += 1
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)


def main():
    if len(sys.argv) not in (2, 3):
        print(__doc__)
        return 2
    text = open(sys.argv[1], encoding="ascii").read()
    check(text.endswith("\n") and "\r" not in text and "\t" not in text, "LF line ends, no tabs, final newline")
    services, cur, starts, trig, mkdirs = {}, None, [], None, []
    # INTERFACES 5.1: the /data/vendor/gb chain with exactly the shared modes, then the shell-owned marker dir
    want_mkdirs = ["mkdir /data/vendor/gb 0751 root shell", "mkdir /data/vendor/gb/ui 0700 shell shell"]
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            cur = None
            m = re.match(r"^service (\S+) (\S+)", line)
            if m:
                cur = services.setdefault(m.group(1), {"exec": m.group(2), "opts": []})
            else:
                trig = line
                check(line in ("on property:sys.boot_completed=1", "on post-fs-data"), "trigger: " + line)
            continue
        if cur is not None:
            cur["opts"].append(line.strip())
        elif trig == "on post-fs-data":
            check(re.match(r"^    mkdir /data/vendor/gb\S* \d{4} \S+ \S+$", line) is not None, "post-fs-data action is a mkdir: " + line.strip())
            mkdirs.append(line.strip())
        else:
            m = re.match(r"^\s+start (\S+)$", line)
            check(m is not None, "action is a start: " + line.strip())
            if m:
                starts.append(m.group(1))
    check(mkdirs == want_mkdirs, "post-fs-data creates the /data/vendor/gb chain (0751 root:shell) and ui/ (0700 shell)")
    check(list(services) == ["gb_gpu_ui_setup"], "one service gb_gpu_ui_setup")
    for n, s in services.items():
        o = s["opts"]
        check(s["exec"] == "/system/bin/sh", "%s runs through /system/bin/sh" % n)
        check("user shell" in o, "%s: user shell (never root)" % n)
        check("group shell" in o, "%s: group shell" % n)
        check("seclabel u:r:shell:s0" in o, "%s: seclabel u:r:shell:s0" % n)
        check("oneshot" in o and "disabled" in o, "%s: oneshot + disabled" % n)
    check(starts == ["gb_gpu_ui_setup"] and all(x in services for x in starts), "start names a defined service")
    if len(sys.argv) == 3:
        spec = open(sys.argv[2], encoding="utf-8").read()
        m = re.search(r"### 15\.4 `gb_gpu_ui\.rc`.*?\n```\n(.*?)```", spec, re.S)
        check(m is not None, "INTERFACES 15.4 block found")
        if m:
            check(text == m.group(1), "gb_gpu_ui.rc is byte-identical to INTERFACES 15.4")
    print("\n%d checks, %d failures" % (CHECKS[0], len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
