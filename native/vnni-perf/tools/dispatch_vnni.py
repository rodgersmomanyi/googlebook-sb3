#!/usr/bin/env python3
"""Dispatch analysis for every function that holds a verified VEX AVX-VNNI instruction (input: scan.json).

Per library (one GNU objdump -d -w sweep each, read-only):
  * every direct reference (call / jmp / jcc / lea rip) to each VNNI function start, by referencing function;
  * 8-byte little-endian copies of the start in non-executable segments (function-pointer tables; with RELR the
    addend sits in place) and R_X86_64_RELATIVE / IRELATIVE addends in plain .rela.dyn;
  * IFUNC symbols; cpuid / xgetbv sites (function names); whether a cpuid site loads leaf 7 subleaf 1 (the AVX-VNNI bit
    is CPUID.(7,1):EAX[4]) within the 6 instructions before it;
  * for each call site, the 14 instructions before it (the guard window), and every rip-relative cmp/test/bt/mov
    (flag reads) in that window;
  * BMI2 (shlx/sarx/shrx/rorx) and AVX2-ymm line counts as a hint of a library-wide -march baseline.
Writes OUT/dispatch.json and OUT/disasm/<lib>/<func>.s (the VNNI functions and their direct referrers).

usage: dispatch_vnni.py SCAN_JSON OUTDIR [PATH_SUBSTRING...]
"""
import bisect, json, os, re, struct, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_vnni import Symtab, phdrs, sections, symbols   # noqa: E402

LINE = re.compile(r"^\s*([0-9a-f]+):\t([0-9a-f ]+?)\s*\t(.*)$")
FHDR = re.compile(r"^([0-9a-f]+) <(.*)>:$")
BR = re.compile(r"^(call|jmp|j[a-z]{1,3})\s+([0-9a-f]+)\b")
RIPCMT = re.compile(r"#\s*([0-9a-f]+)\b")
FLAGOP = re.compile(r"^(cmp[bwlq]?|test[bwlq]?|bt[lq]?|mov[bwlqzsx]*|and[bwlq]?|or[bwlq]?)\b.*\(%rip\)")


def disasm(path):
    p = subprocess.Popen(["objdump", "-d", "-w", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, errors="replace", bufsize=1 << 20)
    for line in p.stdout:
        line = line.rstrip("\n")
        m = LINE.match(line)
        if m:
            yield ("i", int(m.group(1), 16), m.group(2), m.group(3).strip(), line)
        else:
            m = FHDR.match(line)
            if m:
                yield ("f", int(m.group(1), 16), m.group(2), None, line)
    p.wait()


def analyse(rec, out):
    path = rec.get("extracted") or rec["path"]
    with open(path, "rb") as f:
        d = f.read()
    st = Symtab(symbols(d))
    funcs = {}
    for i in rec["insns"]:
        key = int(i["fstart"], 16) if i["fstart"] else int(i["addr"], 16)
        fu = funcs.setdefault(key, dict(name=i["func"], start=key, size=i["fsize"], insns=[], refs=[], dataref=[],
                                        relaref=[]))
        fu["insns"].append(i["addr"])
    starts = set(funcs)
    res = dict(path=rec["path"], sha256=rec["sha256"], nsyms=len(st.s), funcs=[], cpuid=[], xgetbv=[],
               ifunc=sorted({s[2] for s in st.s if s[3] == "IFUNC"}), bmi2=0, ymm_avx2=0)
    window = []
    keep = {}                                    # function start -> list of lines to save
    want_dump = set(starts)
    cur_f = None
    for kind, a, by, txt, line in disasm(path):
        if kind == "f":
            cur_f = a; window = []
            continue
        mn = txt.split()[0] if txt else ""
        if mn in ("shlx", "sarx", "shrx", "rorx"):
            res["bmi2"] += 1
        elif "%ymm" in txt and mn.startswith("vp"):
            res["ymm_avx2"] += 1
        if mn == "cpuid" or mn == "xgetbv":
            s = st.at(a)
            ctx = [w[2] for w in window[-6:]]
            leaf71 = any(re.search(r"mov\s+\$0x7,%eax", c) for c in ctx) and any(
                re.search(r"mov\s+\$0x1,%ecx", c) for c in ctx)
            res[mn].append(dict(addr=hex(a), func=s[0] if s else None, leaf7_sub1=leaf71 if mn == "cpuid" else None,
                                before=ctx))
        m = BR.match(txt)
        tgt = None
        if m:
            tgt = int(m.group(2), 16)
        elif mn.startswith("lea") and "(%rip)" in txt:
            c = RIPCMT.search(txt)
            if c:
                tgt = int(c.group(1), 16)
        if tgt is not None and tgt in starts and not (funcs[tgt]["start"] <= a < funcs[tgt]["start"] + (funcs[tgt]["size"] or 0)):
            s = st.at(a)
            guard = [w for w in window[-14:]]
            flags = []
            for wa, wby, wtxt in guard:
                if FLAGOP.match(wtxt):
                    c = RIPCMT.search(wtxt)
                    if c:
                        ga = int(c.group(1), 16)
                        gs = st.at(ga)
                        flags.append(dict(addr=hex(wa), text=wtxt, target=hex(ga)))
            funcs[tgt]["refs"].append(dict(site=hex(a), kind=mn, from_func=s[0] if s else None,
                                           from_start=hex(s[1]) if s else None,
                                           window=["%x: %s" % (w[0], w[2]) for w in guard], flag_reads=flags))
            if s:
                want_dump.add(s[1])
        window.append((a, by, txt))
        if len(window) > 32:
            window.pop(0)
        s = st.at(a)
        if s and s[1] in starts:
            keep.setdefault(s[1], []).append(line)
    # second, cheap pass for the referrers' text (only when small)
    refs_starts = want_dump - starts
    if refs_starts:
        for kind, a, by, txt, line in disasm(path):
            if kind != "i":
                continue
            s = st.at(a)
            if s and s[1] in refs_starts and len(keep.get(s[1], [])) < 4000:
                keep.setdefault(s[1], []).append(line)
    # data references: 8-byte LE copies of a function start in non-exec PT_LOADs
    for t, fl, off, va, fs in phdrs(d):
        if t == 1 and not fl & 1:
            seg = d[off:off + fs]
            for fstart in starts:
                pat = struct.pack("<Q", fstart)
                i = seg.find(pat)
                n = 0
                while i >= 0 and n < 50:
                    if i % 8 == 0:
                        funcs[fstart]["dataref"].append(hex(va + i))
                    i = seg.find(pat, i + 1); n += 1
    for name, typ, _fl, _ad, off, size, link, _info, _al, ent in sections(d):
        if typ == 4 and ent == 24:               # SHT_RELA
            for k in range(size // 24):
                r_off, r_info, r_add = struct.unpack_from("<QQq", d, off + k * 24)
                if (r_info & 0xffffffff) in (8, 37) and r_add in starts:
                    funcs[r_add]["relaref"].append(dict(sec=name, where=hex(r_off),
                                                        type="IRELATIVE" if (r_info & 0xffffffff) == 37 else "RELATIVE"))
    res["funcs"] = sorted(funcs.values(), key=lambda f: f["start"])
    for f in res["funcs"]:
        f["start"] = hex(f["start"])
    lib = rec["path"].strip("/").replace("/", "_").replace("!", "__")
    dd = os.path.join(out, "disasm", lib)
    os.makedirs(dd, exist_ok=True)
    for fs_, lines in keep.items():
        s = st.at(fs_)
        nm = (s[0] if s else hex(fs_))[:120].replace("/", "_")
        with open(os.path.join(dd, "%x_%s.s" % (fs_, nm)), "w") as f:
            f.write("\n".join(lines) + "\n")
    return res


def main():
    scan, out = sys.argv[1], sys.argv[2]
    subs = sys.argv[3:]
    os.makedirs(out, exist_ok=True)
    recs = [r for r in json.load(open(scan)) if r.get("hits")]
    if subs:
        recs = [r for r in recs if any(s in r["path"] for s in subs)]
    if os.environ.get("EXCLUDE"):
        recs = [r for r in recs if os.environ["EXCLUDE"] not in r["path"]]
    res = []
    for r in recs:
        print("analysing", r["path"], r["hits"], flush=True)
        try:
            res.append(analyse(r, out))
        except Exception as e:
            res.append(dict(path=r["path"], error="%s: %s" % (type(e).__name__, e)))
    with open(os.path.join(out, "dispatch.json"), "w") as f:
        json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
