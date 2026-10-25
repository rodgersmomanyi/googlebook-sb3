#!/usr/bin/env python3
"""Static check of gb_power.rc (INTERFACES.md 15.2): every service has user/seclabel, every start/restart names a
defined service, the control socket line and the /dev/gb_dgpu mkdir are present (mkdir before start gb_power).

Usage: python3 -I test/check_rc.py gb_power.rc
"""
import sys


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    with open(sys.argv[1], encoding="ascii") as f:
        lines = f.read().splitlines()
    services, starts, problems = {}, [], []
    cur, section = None, None
    for n, raw in enumerate(lines, 1):
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not raw.startswith(" "):
            words = line.split()
            section = words[0]
            cur = None
            if section == "service":
                cur = words[1]
                services[cur] = {"opts": [], "line": n}
            elif section != "on":
                problems.append("line %d: unknown section %r" % (n, section))
            continue
        words = line.split()
        if section == "service" and cur:
            services[cur]["opts"].append(words)
        elif section == "on":
            if words[0] in ("start", "restart", "stop"):
                starts.append((n, words[1]))
    for name, s in services.items():
        keys = [o[0] for o in s["opts"]]
        for need in ("user", "seclabel"):
            if need not in keys:
                problems.append("service %s: no %s" % (name, need))
        for o in s["opts"]:
            if o[0] == "seclabel" and o[1] != "u:r:shell:s0":
                problems.append("service %s: seclabel %s (INTERFACES 15: u:r:shell:s0)" % (name, o[1]))
    for n, name in starts:
        if name not in services:
            problems.append("line %d: start of undefined service %s" % (n, name))
    sock = [o for o in services.get("gb_power", {"opts": []})["opts"] if o[0] == "socket"]
    if sock != [["socket", "gb_power", "stream", "0666", "root", "root"]]:
        problems.append("gb_power: socket line must be 'socket gb_power stream 0666 root root' (got %r)" % sock)
    text = [ln.strip() for ln in lines]
    try:
        i_mk = text.index("mkdir /dev/gb_dgpu 0700 root root")
        i_st = text.index("start gb_power")
        if i_mk > i_st:
            problems.append("mkdir /dev/gb_dgpu must come before start gb_power")
    except ValueError as e:
        problems.append("missing line: %s" % e)
    # v1.2 (INTERFACES 15.1-v12, 15.2): the graphics-mode triggers and the self-test service belong to gb_nouveau.rc;
    # the retired persist.vendor.gb.nouveau knob has no trigger anywhere
    for n, raw in enumerate(lines, 1):
        code = raw.split("#", 1)[0]
        for bad in ("persist.vendor.gb.nouveau", "persist.vendor.gb.gfx_mode", "gb_gfx_selftest", "gb_nouveau"):
            if bad in code:
                problems.append("line %d: %s does not belong in gb_power.rc (v1.2: gb_nouveau.rc 15.1-v12)" % (n, bad))
    for p in problems:
        print("RC PROBLEM", p)
    print("rc check: %d services, %d start/stop lines, %d problems" % (len(services), len(starts), len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
