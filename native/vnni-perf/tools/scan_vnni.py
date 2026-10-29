#!/usr/bin/env python3
"""Static scan for VEX-encoded AVX-VNNI (VPDPBUSD/VPDPBUSDS/VPDPWSSD/VPDPWSSDS = VEX.66.0F38.W0 50-53).

Pass 1 (prefilter, a strict superset of every real instruction): in each executable PT_LOAD of every ELF (plain files and
*.so entries of APK/JAR/ZIP files), find  C4 [RXB:3|mmmmm=00010] [W=0|vvvv|L|pp=01] [50-53].
A 2-byte VEX (C5) cannot select map 0F38, so C4 is the only possible prefix.
Pass 2 (verification): GNU objdump -d linear sweep of every ELF with >= 1 prefilter hit; a hit is VERIFIED when
objdump decodes an instruction at exactly that address whose bytes start with the C4 hit and whose mnemonic is one of
the four. Also counts EVEX (62) forms (native on Ice Lake) and any VEX-only VNNI-INT8/INT16 forms (not emulated).
Symbols: .symtab, .dynsym and the xz-compressed MiniDebugInfo (.gnu_debugdata) .symtab.

usage: scan_vnni.py OUTDIR ROOT [ROOT...]        (read-only on ROOTs; writes OUTDIR/scan.json, OUTDIR/apklibs/)
"""
import bisect, hashlib, json, lzma, os, re, struct, subprocess, sys, zipfile
from multiprocessing import Pool

B1 = bytes(0x02 | (r << 5) for r in range(8))
B2 = bytes(0x01 | (k << 2) for k in range(32))
PAT = re.compile(b"\xc4[" + re.escape(B1) + b"][" + re.escape(B2) + b"][\x50-\x53]", re.S)
VNNI4 = ("vpdpbusd", "vpdpbusds", "vpdpwssd", "vpdpwssds")
OTHER_VEX_ONLY = re.compile(r"\bvpdp(b[su][su]ds?|w[su][su]ds?)\b")   # AVX-VNNI-INT8/INT16 (VEX only)
OPC = {0x50: "vpdpbusd", 0x51: "vpdpbusds", 0x52: "vpdpwssd", 0x53: "vpdpwssds"}
OUT = None


def phdrs(d):
    cls = d[4]
    if cls == 2:
        phoff, = struct.unpack_from("<Q", d, 0x20); es, n = struct.unpack_from("<HH", d, 0x36)
        for i in range(n):
            t, fl, off, va, _pa, fs, ms, _al = struct.unpack_from("<IIQQQQQQ", d, phoff + i * es)
            yield t, fl, off, va, fs
    else:
        phoff, = struct.unpack_from("<I", d, 0x1c); es, n = struct.unpack_from("<HH", d, 0x2a)
        for i in range(n):
            t, off, va, _pa, fs, ms, fl, _al = struct.unpack_from("<8I", d, phoff + i * es)
            yield t, fl, off, va, fs


def sections(d):
    cls = d[4]
    if cls == 2:
        shoff, = struct.unpack_from("<Q", d, 0x28); es, n, stri = struct.unpack_from("<HHH", d, 0x3a)
        fmt, = ("<IIQQQQIIQQ",)
    else:
        shoff, = struct.unpack_from("<I", d, 0x20); es, n, stri = struct.unpack_from("<HHH", d, 0x2e)
        fmt = "<10I"
    if not shoff or shoff + n * es > len(d):
        return []
    sh = [struct.unpack_from(fmt, d, shoff + i * es) for i in range(n)]
    names = sh[stri] if stri < n else None
    out = []
    for s in sh:
        name = ""
        if names:
            o = names[4] + s[0]
            name = d[o:d.index(b"\0", o)].decode("latin1") if o < len(d) else ""
        # name, type, flags, addr, offset, size, link, info, align, entsize
        out.append((name,) + tuple(s[1:]))
    return out


def symbols(d, depth=0):
    """[(start, size, name, type)] of FUNC / IFUNC symbols from .symtab, .dynsym and .gnu_debugdata."""
    out = []
    try:
        secs = sections(d)
    except (struct.error, ValueError):
        return out
    cls = d[4]
    for name, typ, _fl, _addr, off, size, link, _info, _al, ent in secs:
        if typ in (2, 11) and link < len(secs):
            stroff = secs[link][4]
            ent = ent or (24 if cls == 2 else 16)
            for i in range(size // ent):
                o = off + i * ent
                if cls == 2:
                    nm, info, _o, _sh, val, sz = struct.unpack_from("<IBBHQQ", d, o)
                else:
                    nm, val, sz, info, _o, _sh = struct.unpack_from("<IIIBBH", d, o)
                st = info & 15
                if st in (2, 10) and val:
                    e = d.find(b"\0", stroff + nm)
                    out.append((val, sz, d[stroff + nm:e].decode("latin1"), "IFUNC" if st == 10 else "FUNC"))
        elif name == ".gnu_debugdata" and depth == 0:
            try:
                out += symbols(lzma.decompress(d[off:off + size]), 1)
            except lzma.LZMAError:
                pass
    return out


class Symtab:
    def __init__(self, syms):
        best = {}
        for s in syms:                       # prefer sized symbols; keep one name per start
            if s[0] not in best or (s[1] and not best[s[0]][1]):
                best[s[0]] = s
        self.s = sorted(best.values())
        self.starts = [s[0] for s in self.s]

    def at(self, a):
        i = bisect.bisect_right(self.starts, a) - 1
        while i >= 0:
            st, sz, nm, ty = self.s[i]
            if sz and st <= a < st + sz:
                return nm, st, sz, True
            if not sz:
                return nm, st, 0, False      # nearest preceding unsized symbol
            if a >= st + sz:
                return None
            i -= 1
        return None


def prefilter(d):
    hits = []
    for t, fl, off, va, fs in phdrs(d):
        if t == 1 and fl & 1 and fs:
            seg = d[off:off + fs]
            for m in PAT.finditer(seg):
                hits.append(va + m.start())
    return hits


def objdump_verify(path, hits):
    hs = set(hits)
    ver, evex, other, bmi = {}, 0, {}, 0
    p = subprocess.Popen(["objdump", "-d", "-w", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                         errors="replace", bufsize=1 << 20)
    rx = re.compile(r"^\s*([0-9a-f]+):\t([0-9a-f ]+?)\s*\t(.*)$")
    for line in p.stdout:
        if "vpdp" in line:
            m = rx.match(line)
            if not m:
                continue
            a = int(m.group(1), 16); by = m.group(2).split(); txt = m.group(3).strip()
            mn = txt.replace("{vex}", "").split()[0] if txt.replace("{vex}", "").split() else ""
            if mn in VNNI4:
                if by[0] == "c4" and a in hs:
                    ver[a] = (" ".join(by), txt)
                elif by[0] == "62":
                    evex += 1
            elif OTHER_VEX_ONLY.search(txt):
                other[a] = (" ".join(by), txt)
        elif "shlx" in line or "sarx" in line or "shrx" in line or "rorx" in line:
            bmi += 1
    p.wait()
    return ver, evex, other, bmi


def scan_blob(d, label, disk_path):
    if len(d) < 64 or d[:4] != b"\x7fELF":
        return None
    mach, = struct.unpack_from("<H", d, 0x12)
    r = dict(path=label, size=len(d), sha256=hashlib.sha256(d).hexdigest(), cls=d[4] * 32, machine=mach)
    if mach not in (0x3e, 0x03):
        r["hits"] = 0
        return r
    try:
        hits = prefilter(d)
    except struct.error:
        r["error"] = "bad phdrs"; return r
    r["prefilter"] = len(hits)
    if not hits:
        r["hits"] = 0
        return r
    if disk_path is None:                    # from an APK: write it out for objdump
        disk_path = os.path.join(OUT, "apklibs", label.lstrip("/").replace("!", "__"))
        os.makedirs(os.path.dirname(disk_path), exist_ok=True)
        with open(disk_path, "wb") as f:
            f.write(d)
        r["extracted"] = disk_path
    ver, evex, other, bmi = objdump_verify(disk_path, hits)
    st = Symtab(symbols(d))
    r["nsyms"] = len(st.s)
    r["hits"] = len(ver)
    r["unverified"] = sorted(hex(a) for a in set(hits) - set(ver))
    r["evex_vnni"] = evex
    r["bmi2_lines"] = bmi
    r["other_vex_only"] = {hex(a): v for a, v in other.items()}
    ins = []
    for a in sorted(ver):
        by, txt = ver[a]
        s = st.at(a)
        b = bytes.fromhex(by.replace(" ", ""))
        ins.append(dict(addr=hex(a), bytes=by, text=txt, L=(b[2] >> 2) & 1, op=OPC.get(b[3]),
                        func=s[0] if s else None, fstart=hex(s[1]) if s else None, fsize=s[2] if s else None,
                        sized=s[3] if s else None))
    r["insns"] = ins
    return r


def work(item):
    kind, path, label = item
    out = []
    try:
        if kind == "elf":
            with open(path, "rb") as f:
                d = f.read()
            r = scan_blob(d, label, path)
            if r:
                out.append(r)
        else:
            with zipfile.ZipFile(path) as z:
                for zi in z.infolist():
                    if zi.file_size < 64 or zi.is_dir():
                        continue
                    if not (zi.filename.endswith(".so") or "/lib/" in "/" + zi.filename):
                        continue
                    with z.open(zi) as f:
                        if f.read(4) != b"\x7fELF":
                            continue
                    r = scan_blob(z.read(zi), label + "!" + zi.filename, None)
                    if r:
                        r["in_zip"] = True
                        out.append(r)
    except Exception as e:                   # report, never hide
        out.append(dict(path=label, error="%s: %s" % (type(e).__name__, e)))
    return out


def items(roots):
    for root in roots:
        for dp, dn, fn in os.walk(root):
            dn.sort()
            for n in sorted(fn):
                p = os.path.join(dp, n)
                if os.path.islink(p) or not os.path.isfile(p):
                    continue
                try:
                    with open(p, "rb") as f:
                        h = f.read(4)
                except OSError:
                    continue
                if h == b"\x7fELF":
                    yield ("elf", p, p)
                elif h == b"PK\x03\x04" and n.endswith((".apk", ".jar", ".zip")):
                    yield ("zip", p, p)


def main():
    global OUT
    OUT = os.path.abspath(sys.argv[1])
    os.makedirs(OUT, exist_ok=True)
    todo = list(items(sys.argv[2:]))
    print("%d ELF / %d zip inputs" % (sum(k == "elf" for k, _, _ in todo), sum(k == "zip" for k, _, _ in todo)),
          flush=True)
    res = []
    with Pool(int(os.environ.get("JOBS", "2"))) as pool:
        for i, rs in enumerate(pool.imap_unordered(work, todo, chunksize=8)):
            res += rs
            if i % 500 == 0:
                print("  %d/%d" % (i, len(todo)), flush=True)
    res.sort(key=lambda r: r["path"])
    with open(os.path.join(OUT, "scan.json"), "w") as f:
        json.dump(res, f, indent=1)
    n_elf = len(res); n_hit = sum(1 for r in res if r.get("hits"))
    print("ELFs scanned %d; with verified VEX AVX-VNNI %d; total verified insns %d; errors %d" % (
        n_elf, n_hit, sum(r.get("hits", 0) for r in res), sum(1 for r in res if "error" in r)))


if __name__ == "__main__":
    main()
