#!/usr/bin/env python3
"""By-address cross-check: for each GNU-objdump-verified VNNI address in scan.json, does llvm-objdump decode a {vex}
instruction at the same address?  usage (in /var/tmp/vnniperf): xcheck2.py LLVM_OBJDUMP PATH_IN_SCAN_JSON...  (read-only)"""
import json,subprocess,re,sys
L=sys.argv[1]
R={r["path"]:r for r in json.load(open("scan/full/scan.json")) if r.get("hits")}
rx=re.compile(r"^\s*([0-9a-f]+):\s+(.*)$")
for p in sys.argv[2:]:
    r=R[p]
    d={}
    for l in subprocess.run([L,"-d",p],capture_output=True,text=True,errors="replace").stdout.splitlines():
        m=rx.match(l)
        if m: d[int(m.group(1),16)]=m.group(2)
    n=0
    for i in r["insns"]:
        a=int(i["addr"],16); t=d.get(a)
        if t is None or "vex" not in t:
            n+=1
            if n<=4: print(p.split("/")[-1], i["addr"], i["bytes"], i["text"], "| llvm:", t)
    print(p.split("/")[-1], "gnu-only", n)
