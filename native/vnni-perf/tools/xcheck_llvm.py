#!/usr/bin/env python3
"""Cross-check the GNU-objdump-verified VEX AVX-VNNI addresses (scan.json) against llvm-objdump for the given libraries.
usage: xcheck_llvm.py LLVM_OBJDUMP SCAN_JSON SUBSTR...   (read-only)"""
import json, re, subprocess, sys
L, scan, subs = sys.argv[1], sys.argv[2], sys.argv[3:]
rx = re.compile(r"^\s*([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*\{vex\}\s+(vpdp(?:busd|busds|wssd|wssds))\b")
for r in json.load(open(scan)):
    if not r.get("hits") or not any(s in r["path"] for s in subs):
        continue
    p = r.get("extracted") or r["path"]
    out = subprocess.run([L, "-d", p], capture_output=True, text=True, errors="replace").stdout
    ll = {}
    for line in out.splitlines():
        m = rx.match(line)
        if m:
            ll[int(m.group(1), 16)] = (m.group(2).split()[0], m.group(3))
    gnu = {int(i["addr"], 16) for i in r["insns"]}
    print("%-90s gnu %5d llvm %5d both %5d gnu-only %d llvm-only %d llvm-non-C4 %d" % (
        r["path"][-90:], len(gnu), len(ll), len(gnu & set(ll)), len(gnu - set(ll)), len(set(ll) - gnu),
        sum(1 for v in ll.values() if v[0] != "c4")))
