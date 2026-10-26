#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Cross-check every op blob in vnni_kat_ops.h against GNU as/objdump: reassemble the op bytes and assert the
disassembly is the expected AVX-VNNI mnemonic, encoding (VEX vs EVEX), vector length and register/memory operand.
Reads ops_meta.txt (written by gen_vnni_kat_ops.py). Usage: python3 -I gen_vnni_kat_check.py <ops_meta.txt> <workdir>
"""
import os
import re
import subprocess
import sys

MN = {"vpdpbusd", "vpdpbusds", "vpdpwssd", "vpdpwssds"}


def main(meta, work):
    os.makedirs(work, exist_ok=True)
    rows = [ln.split() for ln in open(meta) if ln.strip()]
    # build one .s with .byte of each op, disassemble, compare
    s = os.path.join(work, "chk.s")
    with open(s, "w") as f:
        f.write(".text\n")
        for r in rows:
            name, hexb = r[0], r[-1]
            f.write("%s: .byte %s\n" % (name, ",".join("0x" + hexb[i:i + 2] for i in range(0, len(hexb), 2))))
        f.write("end: nop\n")
    obj = os.path.join(work, "chk.o")
    subprocess.run(["as", "--64", "-o", obj, s], check=True)
    dis = subprocess.run(["objdump", "-d", "-w", "--no-show-raw-insn", obj], check=True, capture_output=True,
                         text=True).stdout
    # map each label addr
    sym = {}
    for m in re.finditer(r"^([0-9a-f]+) <(\w+)>:$", dis, re.M):
        sym[int(m.group(1), 16)] = m.group(2)
    insns = {}
    cur = None
    for line in dis.splitlines():
        m = re.match(r"^\s*([0-9a-f]+):\s+(.*)$", line)
        if not m:
            continue
        a = int(m.group(1), 16)
        if a in sym:
            cur = sym[a]
            insns.setdefault(cur, m.group(2).strip())
    bad = 0
    for r in rows:
        name, enc, mn, lx = r[0], r[1], r[2], r[3]
        form = [x for x in r if x.startswith("form=")][0].split("=")[1]
        txt = insns.get(name, "")
        want_l = int(lx[1:])
        vl = "ymm" if want_l else "xmm"
        ok = mn in MN and mn in txt and vl in txt
        if form == "reg":
            ok = ok and txt.count(vl) >= 3 and "(" not in txt
        else:
            ok = ok and "(%rsi)" in txt
            # mem0 = no displacement, mem64 = +0x40 (objdump prints the decoded displacement for VEX and EVEX)
            ok = ok and (("0x40(%rsi)" in txt) if form == "mem64" else ("0x40(" not in txt))
        # the encoding itself: VEX ops start with the 3-byte VEX prefix C4, EVEX ops with 62
        ok = ok and r[-1].startswith("c4" if enc == "vex" else "62")
        if not ok:
            print("MISMATCH %s enc=%s want %s %s %s -> %r" % (name, enc, mn, vl, form, txt))
            bad += 1
    print("encoding cross-check: %d blobs, %d mismatches" % (len(rows), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
