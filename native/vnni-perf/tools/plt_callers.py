#!/usr/bin/env python3
"""List the functions that call given imported symbols through the PLT (call/jmp <sym@plt>), with the 8 instructions
before each call (to read e.g. inflateInit2_'s windowBits argument in %esi). Read-only; GNU objdump.

usage: plt_callers.py OUT_JSON SYMS(comma-separated) ELF [ELF...]
"""
import json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_vnni import Symtab, symbols   # noqa: E402

LINE = re.compile(r"^\s*([0-9a-f]+):\t([0-9a-f ]+?)\s*\t(.*)$")


def main():
    out, syms, files = sys.argv[1], sys.argv[2].split(","), sys.argv[3:]
    tgt = re.compile(r"^(call|jmp)\s+[0-9a-f]+ <(%s)@plt>" % "|".join(re.escape(s) for s in syms))
    res = []
    for p in files:
        st = Symtab(symbols(open(p, "rb").read()))
        win = []
        sites = []
        pr = subprocess.Popen(["objdump", "-d", "-w", p], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                              errors="replace")
        for line in pr.stdout:
            m = LINE.match(line.rstrip("\n"))
            if not m:
                win = []
                continue
            a, txt = int(m.group(1), 16), m.group(3).strip()
            t = tgt.match(txt)
            if t:
                s = st.at(a)
                sites.append(dict(site=hex(a), sym=t.group(2), func=s[0] if s else None,
                                  before=["%x: %s" % w for w in win[-8:]]))
            win.append((a, txt))
            if len(win) > 16:
                win.pop(0)
        pr.wait()
        res.append(dict(path=p, sites=sites))
        print(p, len(sites), "call sites")
    with open(out, "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
