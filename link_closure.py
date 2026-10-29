"""link_closure.py -- build-time link-closure gate for the native tree's /vendor overlay (wave 5 fixprep keymint-wifi,
readiness finding C-2). Our own code (Apache-2.0); no Google code.

Why: the native tree used to overlay /vendor/lib64/libcrypto.so with the android16 BoringSSL of the KeyMint graft, so
lapis's android17 /vendor/lib64/libssl.so lost 58 symbols and wpa_supplicant / hostapd could not link (no Wi-Fi, no
hotspot). The graft now carries private sonames (libcrypto_gbkm16.so, lib*_gbkm16.so); this gate makes sure no
overlay can silently break a vendor ELF again.

What it models: bionic's linker for a process started from /vendor (linkerconfig's [vendor] section): the vendor
"default" namespace searches /odm/lib64 then /vendor/lib64 (plus their hw/ and egl/ subdirectories; the native tree is
/odm, its vup/ is the /vendor overlay's upper layer); a name on the LLNDK list (system/etc/llndk.libraries.txt) that
the vendor paths lack comes from the system namespace. Libraries load breadth-first in DT_NEEDED order, once per
soname and namespace (find_libraries); the lookup list of every vendor-side ELF in the group is then the breadth-first
walk of that dependency tree from the root that keeps only the objects the vendor namespace can access
(walk_dependencies_tree + android_namespace_t::is_accessible: every vendor-side object, and a system-namespace one only
when at least one vendor-side object needs it; the subtree of an inaccessible one is skipped), its own object first
when it has DF_SYMBOLIC. A system library's non-LLNDK dependencies load in the system namespace and are never
accessible, so the reference does not hold them. A symbol matches with bionic's version rules
(find_verdef_version_index / check_symbol_version); every GLOBAL undefined dynamic symbol must be found (bionic binds
everything at load time); WEAK ones may stay unresolved.

The lapis side comes from a link reference (make: `python3 -I link_closure.py summarize ...` on the builder over the
fsck.erofs-extracted vendor_<slot> / system_<slot>): every vendor ELF (lib64, bin, incl. hw/) and every LLNDK library,
as soname / NEEDED / defined and undefined dynamic symbols with versions, pinned to the two partitions' size and
sha256. check_native() fails closed when the pin does not match lapis.bin.

Checks (check_tree): N1 every ELF of the overlay (gbnative/vup/**) is a sane x86_64 ELF: PT_LOAD p_offset == p_vaddr
(mod p_align), segments in order without overlap and inside the file, PT_PHDR / PT_DYNAMIC inside a PT_LOAD, every
verneed file in DT_NEEDED, a library's DT_SONAME == its file name; N2 its NEEDED closure resolves (every NEEDED found,
every GLOBAL undefined symbol of every vendor-namespace ELF in the group defined); N3 the required roots
(wpa_supplicant, hostapd, the KeyMint service) exist and resolve completely; N4 no lapis vendor ELF resolves worse with
the overlay than without it (every new missing NEEDED or symbol is a problem); N5 no group holds two copies of one
library (X.so and X_gbkm16.so: bionic would bind the graft to whichever comes first).
"""
import gzip
import hashlib
import json
import os
import struct
import sys
from collections import deque

FORMAT = "gb-linkref-1"
PRIVATE_SUFFIX = "_gbkm16"                     # the android16 KeyMint graft's private sonames
REQUIRED_ROOTS = ("vendor/bin/hw/wpa_supplicant", "vendor/bin/hw/hostapd",
                  "vendor/bin/hw/android.hardware.security.keymint-service")
VENDOR_SEARCH = ("odm/lib64", "vendor/lib64", "odm/lib64/hw", "vendor/lib64/hw",
                 "odm/lib64/egl", "vendor/lib64/egl")          # the [vendor] default namespace (64-bit)
PT_LOAD, PT_DYNAMIC, PT_INTERP, PT_PHDR = 1, 2, 3, 6
DT_NEEDED, DT_STRTAB, DT_SYMTAB, DT_STRSZ, DT_SYMENT, DT_SONAME, DT_HASH = 1, 5, 6, 10, 11, 14, 4
DT_SYMBOLIC, DT_FLAGS, DT_GNU_HASH = 16, 30, 0x6ffffef5
DT_VERSYM, DT_VERDEF, DT_VERDEFNUM, DT_VERNEED, DT_VERNEEDNUM = 0x6ffffff0, 0x6ffffffc, 0x6ffffffd, 0x6ffffffe, 0x6fffffff
DF_SYMBOLIC = 0x2
STB_GLOBAL, STB_WEAK = 1, 2
HIDDEN = 0x8000
PAGE = 4096


class ElfError(Exception):
    pass


def is_elf(data):
    return data is not None and data[:4] == b"\x7fELF"


def parse_elf(data, name="?"):
    """Summary of an ELF (64- or 32-bit, little-endian) as bionic's loader sees it, and its layout problems:
    ({"soname", "needed", "exe", "symbolic", "vs", "vd", "d", "u", "vnf", "cls", "mach"}, [problem, ...]).
    d: {symbol: [versym, ...]} of GLOBAL/WEAK defined dynamic symbols (versym 0 when there is no version table);
    u: [[symbol, version or ""], ...] of the GLOBAL undefined ones; vd: {verdef name: index}; vnf: verneed files.
    Tables are read through the loader's view (page-rounded PT_LOAD mapping), so a mis-laid-out file reads as the
    loader would read it. Raises ElfError when the file cannot be read at all."""
    probs = []
    try:
        return _parse_elf(data, name, probs), probs
    except ElfError as e:
        e.layout = list(probs)                             # the layout problems found before the failure
        raise
    except (struct.error, IndexError, ValueError, UnicodeError, OverflowError) as e:
        err = ElfError(f"{name}: malformed ELF ({e.__class__.__name__}: {e})")
        err.layout = list(probs)
        raise err from None


def _parse_elf(data, name, probs):
    if not is_elf(data) or len(data) < 64:
        raise ElfError(f"{name}: not an ELF file")
    cls, enc = data[4], data[5]
    if enc != 1 or cls not in (1, 2):
        raise ElfError(f"{name}: not a little-endian ELF32/ELF64 file")
    b64 = cls == 2
    if b64:
        (etype, mach, _, _, phoff, shoff, _, _, phentsize, phnum, shentsize, shnum, _) = \
            struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
        phfmt, dynfmt, dynsz, symsz = "<IIQQQQQQ", "<qQ", 16, 24
    else:
        (etype, mach, _, _, phoff, shoff, _, _, phentsize, phnum, shentsize, shnum, _) = \
            struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
        phfmt, dynfmt, dynsz, symsz = "<IIIIIIII", "<iI", 8, 16
    if phoff + phnum * phentsize > len(data) or phnum == 0:
        raise ElfError(f"{name}: program headers outside the file")
    ph = []
    for i in range(phnum):
        v = struct.unpack_from(phfmt, data, phoff + i * phentsize)
        if b64:
            pt, pf, off, va, _, fsz, msz, al = v
        else:
            pt, off, va, _, fsz, msz, pf, al = v
        ph.append((pt, off, va, fsz, msz, al))
    loads = [p for p in ph if p[0] == PT_LOAD]
    if not loads:
        raise ElfError(f"{name}: no PT_LOAD")
    # ---- layout (what bionic's ElfReader relies on; C-1 / C-2 "no strip after patchelf, PT_LOAD congruence")
    last_end = None
    for i, (pt, off, va, fsz, msz, al) in enumerate(loads):
        if al and (al & (al - 1)):
            probs.append(f"PT_LOAD[{i}] p_align {al:#x} is not a power of two")
        elif al > 1 and off % al != va % al:
            probs.append(f"PT_LOAD[{i}] p_offset {off:#x} != p_vaddr {va:#x} (mod p_align {al:#x})")
        if off % PAGE != va % PAGE:
            probs.append(f"PT_LOAD[{i}] p_offset {off:#x} != p_vaddr {va:#x} (mod page {PAGE:#x})")
        if fsz > msz:
            probs.append(f"PT_LOAD[{i}] p_filesz {fsz:#x} > p_memsz {msz:#x}")
        if off + fsz > len(data):
            probs.append(f"PT_LOAD[{i}] file range {off:#x}+{fsz:#x} beyond the file ({len(data):#x})")
        if last_end is not None and va < last_end:
            probs.append(f"PT_LOAD[{i}] p_vaddr {va:#x} overlaps or precedes the previous segment (end {last_end:#x})")
        last_end = va + msz

    def file_off(vaddr, n=1):
        """File offset of [vaddr, vaddr+n) as the loader maps it (whole pages from page_start(p_offset))."""
        for pt, off, va, fsz, msz, al in loads:
            if va <= vaddr and vaddr + n <= va + fsz:
                o = (off - off % PAGE) + (vaddr - (va - va % PAGE))
                if o + n > len(data):
                    raise ElfError(f"{name}: vaddr {vaddr:#x} maps beyond the file")
                return o
        raise ElfError(f"{name}: vaddr {vaddr:#x}+{n} is not file-backed by a PT_LOAD")

    def inside_load(p, what):
        pt, off, va, fsz, msz, al = p
        if not any(l[2] <= va and va + fsz <= l[2] + l[3] and off - l[1] == va - l[2] for l in loads):
            probs.append(f"{what} ({off:#x}/{va:#x}+{fsz:#x}) is not inside a PT_LOAD with the same offset/vaddr delta")

    for p in ph:
        if p[0] == PT_PHDR:
            inside_load(p, "PT_PHDR")
    dyn = [p for p in ph if p[0] == PT_DYNAMIC]
    interp = [p for p in ph if p[0] == PT_INTERP]
    s = {"cls": 64 if b64 else 32, "mach": mach, "exe": bool(interp), "soname": None, "needed": [], "symbolic": False,
         "vs": False, "vd": {}, "d": {}, "u": [], "vnf": []}
    if not dyn:
        return s                                            # static: nothing to resolve
    inside_load(dyn[0], "PT_DYNAMIC")
    doff, dsize = dyn[0][1], dyn[0][3]
    tags = []
    for o in range(doff, min(doff + dsize, len(data) - dynsz + 1), dynsz):
        t, v = struct.unpack_from(dynfmt, data, o)
        if t == 0:
            break
        tags.append((t, v))
    one = {}
    for t, v in tags:
        one.setdefault(t, v)
    if DT_STRTAB not in one or DT_SYMTAB not in one:
        raise ElfError(f"{name}: no DT_STRTAB/DT_SYMTAB")
    stroff = file_off(one[DT_STRTAB], max(1, one.get(DT_STRSZ, 1)))
    strsz = one.get(DT_STRSZ, len(data) - stroff)

    def cstr(i):
        if i >= strsz:
            raise ElfError(f"{name}: string offset {i:#x} beyond DT_STRSZ")
        e = data.find(b"\0", stroff + i, stroff + strsz)
        if e < 0:
            raise ElfError(f"{name}: unterminated string at {i:#x}")
        return data[stroff + i:e].decode("utf-8", "replace")

    s["needed"] = [cstr(v) for t, v in tags if t == DT_NEEDED]
    if DT_SONAME in one:
        s["soname"] = cstr(one[DT_SONAME])
    s["symbolic"] = DT_SYMBOLIC in one or bool(one.get(DT_FLAGS, 0) & DF_SYMBOLIC)
    if interp:
        io_ = interp[0][1]
        s["interp"] = data[io_:io_ + interp[0][3]].split(b"\0")[0].decode("utf-8", "replace")
    # number of dynamic symbols: DT_HASH nchain, else the DT_GNU_HASH chains, else the SHT_DYNSYM section
    nsym = 0
    if DT_HASH in one:
        nsym = struct.unpack_from("<I", data, file_off(one[DT_HASH], 8) + 4)[0]
    elif DT_GNU_HASH in one:
        g = file_off(one[DT_GNU_HASH], 16)
        nb, symoff, bloom, _ = struct.unpack_from("<4I", data, g)
        bk = g + 16 + bloom * (8 if b64 else 4)
        buckets = struct.unpack_from(f"<{nb}I", data, bk)
        ch = bk + 4 * nb
        mx = max(buckets) if buckets else 0
        if mx < symoff:
            nsym = symoff
        else:
            i = mx
            while not (struct.unpack_from("<I", data, ch + 4 * (i - symoff))[0] & 1):
                i += 1
            nsym = i + 1
    if shoff and shentsize and shoff + shnum * shentsize <= len(data):
        for i in range(shnum):
            if b64:
                _, sht, _, _, _, sz, _, _, _, es = struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * shentsize)
            else:
                _, sht, _, _, _, sz, _, _, _, es = struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize)
            if sht == 11 and es:                            # SHT_DYNSYM
                nsym = max(nsym, sz // es)
    symoff_f = file_off(one[DT_SYMTAB], symsz * max(nsym, 1)) if nsym else 0
    versym = None
    if DT_VERSYM in one and nsym:
        vo = file_off(one[DT_VERSYM], 2 * nsym)
        versym = struct.unpack_from(f"<{nsym}H", data, vo)
        s["vs"] = True
    # verdef: name -> index
    if DT_VERDEF in one:
        o = file_off(one[DT_VERDEF], 20)
        for _ in range(one.get(DT_VERDEFNUM, 0)):
            _, _, ndx, cnt, _, aux, nxt = struct.unpack_from("<HHHHIII", data, o)
            if cnt:
                s["vd"][cstr(struct.unpack_from("<I", data, o + aux)[0])] = ndx
            if not nxt:
                break
            o += nxt
    # verneed: index -> (version name, file)
    vneed = {}
    if DT_VERNEED in one:
        o = file_off(one[DT_VERNEED], 16)
        for _ in range(one.get(DT_VERNEEDNUM, 0)):
            _, cnt, vfile, aux, nxt = struct.unpack_from("<HHIII", data, o)
            f = cstr(vfile)
            s["vnf"].append(f)
            a = o + aux
            for _ in range(cnt):
                _, _, other, vname, anext = struct.unpack_from("<IHHII", data, a)
                vneed[other & 0x7fff] = (cstr(vname), f)
                if not anext:
                    break
                a += anext
            if not nxt:
                break
            o += nxt
        for f in s["vnf"]:
            if f not in s["needed"]:
                probs.append(f"verneed file {f!r} is not in DT_NEEDED (bionic: 'cannot find ... from verneed')")
    for i in range(1, nsym):
        o = symoff_f + i * symsz
        if b64:
            nm, info, other, shndx, _, _ = struct.unpack_from("<IBBHQQ", data, o)
        else:
            nm, _, _, info, other, shndx = struct.unpack_from("<IIIBBH", data, o)
        bind = info >> 4
        if bind not in (STB_GLOBAL, STB_WEAK) or not nm:
            continue
        sym = cstr(nm)
        vv = versym[i] if versym else 0
        if shndx != 0:
            s["d"].setdefault(sym, []).append(vv)
        elif bind == STB_GLOBAL:
            ver = vneed.get(vv & 0x7fff, ("", ""))[0] if (vv & 0x7fff) >= 2 else ""
            s["u"].append([sym, ver])
    return s


def defines(lib, sym, ver):
    """bionic: does `lib` satisfy a reference to sym (with required version ver, "" = none)?"""
    vs = lib["d"].get(sym)
    if not vs:
        return False
    if not lib["vs"]:
        return True                                        # no version table: any definition
    if ver:
        idx = lib["vd"].get(ver, 1)                        # unknown version name -> VER_NDX_GLOBAL
        return any((v & 0x7fff) == idx for v in vs)
    return any(not (v & HIDDEN) for v in vs)


class View:
    """The files a vendor process can load: {"vendor/<rel>", "odm/<rel>", "system/<name>": summary}."""

    def __init__(self, elfs, llndk):
        self.elfs = elfs
        self.llndk = set(llndk)

    def find(self, name, system_side=False):
        if not system_side:
            for d in VENDOR_SEARCH:
                p = f"{d}/{name}"
                if p in self.elfs:
                    return p
        if name in self.llndk and f"system/{name}" in self.elfs:
            return f"system/{name}"
        return None

    def group(self, root):
        """(lookup order, [(needed_by, name) not found]) for a vendor-side root, as bionic builds it (module doc):
        load breadth-first (one object per soname in each namespace), then walk the tree breadth-first from root,
        keeping vendor-side objects and system ones that have a vendor-side parent; skipped objects' children are not
        walked."""
        kids, parents, missing = {root: []}, {root: set()}, []
        known = {False: {self.elfs[root].get("soname") or os.path.basename(root): root}, True: {}}   # per namespace
        q = deque([root])
        while q:
            cur = q.popleft()
            sys_side = cur.startswith("system/")
            for n in self.elfs[cur]["needed"]:
                if sys_side and n not in self.llndk:
                    continue                               # system namespace only: never accessible from vendor
                p = known[sys_side].get(n)
                if p is None:
                    p = self.find(n, sys_side)
                    if p is None:
                        missing.append((cur, n))
                        continue
                    ns = p.startswith("system/")
                    known[ns].setdefault(n, p)
                    known[ns].setdefault(self.elfs[p].get("soname") or n, p)
                    if ns != sys_side:
                        known[sys_side].setdefault(n, p)   # the vendor namespace reaches it through its link
                if p not in kids:
                    kids[p], parents[p] = [], set()
                    q.append(p)
                if p not in kids[cur]:
                    kids[cur].append(p)
                parents[p].add(cur)
        order, seen, q = [root], {root}, deque([root])
        while q:
            cur = q.popleft()
            for c in kids[cur]:
                if c in seen:
                    continue
                seen.add(c)
                if not c.startswith("system/") or any(not x.startswith("system/") for x in parents[c]):
                    order.append(c)
                    q.append(c)
        return order, missing

    def unresolved(self, order, who):
        """[(symbol, version)] of who's GLOBAL undefined symbols that no object of the lookup list defines."""
        s = self.elfs[who]
        look = ([who] if s["symbolic"] else []) + order
        libs = [self.elfs[p] for p in look]
        return [(sym, ver) for sym, ver in s["u"] if not any(defines(l, sym, ver) for l in libs)]

    def provider(self, order, who, sym, ver):
        s = self.elfs[who]
        for p in ([who] if s["symbolic"] else []) + order:
            if defines(self.elfs[p], sym, ver):
                return p
        return None

    def failures(self, root):
        """{("needed", by, name) | ("symbol", elf, sym@ver) | ("bad", path, why)} for root's whole vendor-namespace
        group (a "bad" object is a native-tree ELF that cannot be read: bionic could not load it either)."""
        order, missing = self.group(root)
        out = {("needed", by, n) for by, n in missing}
        for p in order:
            if self.elfs[p].get("bad"):
                out.add(("bad", p, self.elfs[p]["bad"]))
                continue
            if not p.startswith("system/"):
                out |= {("symbol", p, f"{sym}@{ver}" if ver else sym) for sym, ver in self.unresolved(order, p)}
        return out


def _stub(why):
    return {"cls": 64, "mach": 62, "exe": False, "soname": None, "needed": [], "symbolic": False, "vs": False,
            "vd": {}, "d": {}, "u": [], "vnf": [], "bad": why}


def _fmt(f):
    if f[0] == "needed":
        return f"NEEDED {f[2]} (of {f[1]}) not found"
    if f[0] == "bad":
        return f"{f[1]}: unreadable ELF ({f[2]})"
    return f"{f[1]}: undefined symbol {f[2]}"


def _stem(soname):
    b = soname[:-3] if soname.endswith(".so") else soname
    return b[:-len(PRIVATE_SUFFIX)] if b.endswith(PRIVATE_SUFFIX) else b


def duplicate_copies(view, order):
    """[(stem, [paths])] of libraries present twice in one group (X.so and X_gbkm16.so)."""
    by = {}
    for p in order:
        sn = view.elfs[p].get("soname") or os.path.basename(p)
        by.setdefault(_stem(sn), []).append(p)
    return [(k, v) for k, v in sorted(by.items()) if len(v) > 1]


def tree_elfs(entries, prefix="gbnative/"):
    """{view path: (bytes, tree path)} of the native tree's regular ELF files: gbnative/vup/<rel> -> vendor/<rel>,
    any other gbnative/<rel> -> odm/<rel>."""
    out = {}
    for path, mode, data in entries:
        if not path.startswith(prefix) or data is None or (mode & 0o170000) != 0o100000 or not is_elf(data):
            continue
        rel = path[len(prefix):]
        out[("vendor/" + rel[4:]) if rel.startswith("vup/") else ("odm/" + rel)] = (data, path)
    return out


def tree_shadows(entries, prefix="gbnative/"):
    """View paths the native tree replaces with a non-ELF file or a whiteout (it hides the lapis ELF there)."""
    out = set()
    for path, mode, data in entries:
        if not path.startswith(prefix) or (mode & 0o170000) == 0o040000:
            continue
        rel = path[len(prefix):]
        if rel.startswith("vup/") and not is_elf(data):
            out.add("vendor/" + rel[4:])
    return out


def view_of(entries, ref, prefix="gbnative/"):
    """(View of lapis's vendor/system with the native tree laid over it, {view path: (tree path, summary or None,
    [layout problems])} of the tree's ELFs). A tree file that is not an ELF hides the lapis ELF at its /vendor path; an
    unreadable ELF becomes a "bad" stub (bionic could not load it either); a 32-bit one leaves the lib64 view."""
    elfs = dict(ref["elfs"])
    for p in tree_shadows(entries, prefix):
        elfs.pop(p, None)
    ours = {}
    for vp_, (data, tp) in sorted(tree_elfs(entries, prefix).items()):
        try:
            s, lp = parse_elf(data, tp)
        except ElfError as e:
            s, lp = None, getattr(e, "layout", []) + [str(e)]
            bad = str(e)
        ours[vp_] = (tp, s, lp)
        if s is None:
            elfs[vp_] = _stub(bad)
        elif s["cls"] == 64:
            elfs[vp_] = s
        else:
            elfs.pop(vp_, None)                            # a 32-bit file is not on the lib64 search path
    return View(elfs, ref["llndk"]), ours


def check_tree(entries, ref, required=REQUIRED_ROOTS, prefix="gbnative/"):
    """Problems (N1-N5, see the module doc) of the native tree `entries` [(path, mode, data)] against the lapis link
    reference `ref`, and a short report. An empty problem list means the gate passes."""
    probs, report = [], []
    base = dict(ref["elfs"])
    pristine = View(base, ref["llndk"])
    view, ours = view_of(entries, ref, prefix)
    elfs = view.elfs
    for vp_, (tp, s, lp) in ours.items():
        if not vp_.startswith("vendor/"):
            continue                                       # N1: the /vendor overlay's ELFs (graft and components)
        for x in lp:
            probs.append(f"N1 {tp}: {x}")
        if s is not None:
            if s["cls"] != 64 or s["mach"] != 62:
                probs.append(f"N1 {tp}: not an x86_64 ELF64 (class {s['cls']}, machine {s['mach']})")
            if os.path.dirname(vp_) == "vendor/lib64" and s["soname"] != os.path.basename(vp_):
                probs.append(f"N1 {tp}: DT_SONAME {s['soname']!r} != file name {os.path.basename(vp_)!r} "
                             "(bionic matches loaded libraries by soname)")

    roots = sorted(p for p in ours if p.startswith("vendor/") and p in elfs)
    for r in list(required):
        if r not in elfs:
            probs.append(f"N3 required root {r} is missing from the native tree + lapis view")
    for r in roots + [x for x in required if x in elfs and x not in roots]:
        order, _ = view.group(r)
        fails = view.failures(r)
        tag = "N3" if r in required else "N2"
        for f in sorted(fails):
            probs.append(f"{tag} {r}: {_fmt(f)}")
        for k, v in duplicate_copies(view, order):
            probs.append(f"N5 {r}: two copies of {k} in one group: {', '.join(v)}")
        report.append(f"{r}: {len(order)} objects, {'OK' if not fails else f'{len(fails)} failure(s)'}")
    # N4: every lapis vendor ELF whose group now holds a native-tree object (or lost one) must not resolve worse
    changed = {p for p in elfs if p in ours} | (set(base) - set(elfs))
    n4 = 0
    for p in sorted(base):
        if not p.startswith("vendor/") or base[p]["cls"] != 64 or p in ours:
            continue
        if p not in elfs:
            continue                                       # replaced by a non-ELF stub / whiteout
        order, _ = view.group(p)
        if not (set(order) & changed) and not (set(pristine.group(p)[0]) & changed):
            continue
        n4 += 1
        new = view.failures(p) - pristine.failures(p)
        for f in sorted(new):
            probs.append(f"N4 {p}: {_fmt(f)} (resolves without the overlay)")
        for k, v in duplicate_copies(view, order):
            probs.append(f"N5 {p}: two copies of {k} in one group: {', '.join(v)}")
    report.append(f"lapis vendor ELFs whose group touches the overlay: {n4} checked")
    return probs, report


# ------------------------------------------------------------------------------------------------ reference
def load_ref(path):
    with open(path, "rb") as f:
        raw = f.read()
    ref = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
    if ref.get("format") != FORMAT:
        raise ElfError(f"{path}: format {ref.get('format')!r}, expected {FORMAT}")
    return ref


_PART_HASH_CACHE = {}


def pin_problems(ref, lapis, slot, vp):
    """The reference must describe the boot slot's vendor/system partitions of `lapis` (sizes and sha256, read with
    vnni_prepare's carve_plan / stream_partition, "rb" only)."""
    probs = []
    if ref["lapis"].get("slot") != slot:
        return [f"N0 the link reference is for slot {ref['lapis'].get('slot')!r}, the build boots slot {slot!r}"]
    st = os.stat(lapis)
    ck = (os.path.abspath(lapis), st.st_mtime_ns, st.st_size, slot)
    if ck not in _PART_HASH_CACHE:
        base, plan, _ = vp.carve_plan(lapis, slot)
        actual = {}
        for key in ("vendor", "system"):
            name, exts, size = plan[key]
            h = hashlib.sha256()
            for b in vp.stream_partition(lapis, base, exts):
                h.update(b)
            actual[key] = (name, size, h.hexdigest())
        _PART_HASH_CACHE[ck] = actual
    for key in ("vendor", "system"):
        name, size, got_sha = _PART_HASH_CACHE[ck][key]
        want = ref["lapis"]["partitions"].get(name)
        if not want or want["bytes"] != size:
            probs.append(f"N0 {name}: {size:,} B in lapis.bin, the link reference has {want}")
            continue
        if got_sha != want["sha256"]:
            probs.append(f"N0 {name}: sha256 {got_sha} in lapis.bin, the link reference has {want['sha256']} "
                         "(lapis changed: regenerate the reference)")
    return probs


def _load_module(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def check_native(entries, lapis, slot, ref_path, here):
    """The build_esp.py gate: N0 (the reference matches lapis.bin's boot slot) then N1-N5 (check_tree).
    Returns (problems, report lines)."""
    if not os.path.isfile(ref_path):
        return [f"N0 {ref_path} is missing: run linkref_prepare.py (reads lapis.bin read-only, uses the builder)"], []
    ref = load_ref(ref_path)
    vp = _load_module("vnni_prepare", os.path.join(here, "vnni_prepare.py"))
    probs = pin_problems(ref, lapis, slot, vp)
    if probs:
        return probs, ["reference pin FAILED"]
    probs, report = check_tree(entries, ref)
    fp = ref["lapis"].get("fingerprints", {}).get("ro.vendor.build.fingerprint", "?")
    return probs, [f"reference {fp} slot {slot} (vendor/system sha256 pinned, equal)"] + report


def summarize(vendor_dir, system_dir, bionic_dir, apex_dirs, part_record):
    """The link reference of an extracted lapis slot (run on the builder)."""
    elfs = {}
    for top in ("lib64", "bin"):
        for dp, dns, fns in os.walk(os.path.join(vendor_dir, top)):
            dns.sort()
            for fn in sorted(fns):
                fp = os.path.join(dp, fn)
                if os.path.islink(fp) or not os.path.isfile(fp):
                    continue
                with open(fp, "rb") as f:
                    data = f.read()
                if not is_elf(data):
                    continue
                rel = "vendor/" + os.path.relpath(fp, vendor_dir).replace(os.sep, "/")
                s, _ = parse_elf(data, rel)
                if s["cls"] == 64:
                    elfs[rel] = s
    with open(os.path.join(system_dir, "etc", "llndk.libraries.txt"), encoding="utf-8") as f:
        llndk = sorted({l.strip() for l in f if l.strip() and not l.startswith("#")})
    src = {}
    for n in llndk:                                        # bionic first, then system/lib64, then the apexes
        for d in [bionic_dir, os.path.join(system_dir, "lib64")] + list(apex_dirs):
            fp = os.path.join(d, n)
            if os.path.isfile(fp) and not os.path.islink(fp):
                with open(fp, "rb") as f:
                    data = f.read()
                s, _ = parse_elf(data, fp)
                elfs["system/" + n] = s
                src[n] = os.path.relpath(fp, os.path.dirname(system_dir)) if d != bionic_dir else "bionic/" + n
                break
    rec = json.loads(open(part_record, encoding="utf-8").read()) if part_record else {}
    return {"format": FORMAT, "lapis": rec, "llndk": llndk, "llndk_source": src, "elfs": elfs}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="link-closure reference / closure printer (see the module doc)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("summarize")
    a1.add_argument("--vendor", required=True)
    a1.add_argument("--system", required=True, help="the system/ directory of the extracted system image")
    a1.add_argument("--bionic", required=True, help="directory with the runtime libc/libm/libdl (bootstrap copies)")
    a1.add_argument("--apex-lib", action="append", default=[])
    a1.add_argument("--record", help="the record linkref_prepare.py writes (slot, partition bytes + sha256, fingerprints)")
    a1.add_argument("--out", required=True)
    a2 = sub.add_parser("closure")
    a2.add_argument("--ref", required=True)
    a2.add_argument("--overlay", help="a directory laid out like gbnative/ (vup/... = /vendor/...)")
    a2.add_argument("--root", action="append", required=True)
    a2.add_argument("--symbols", action="append", default=[], help="print the provider of every symbol of this ELF")
    a = ap.parse_args(argv)
    if a.cmd == "summarize":
        ref = summarize(a.vendor, a.system, a.bionic, a.apex_lib, a.record)
        raw = json.dumps(ref, sort_keys=True, separators=(",", ":")).encode()
        with open(a.out, "wb") as f:
            f.write(gzip.compress(raw, 9, mtime=0))
        print(f"{a.out}: {len(ref['elfs'])} ELFs, {len(ref['llndk'])} LLNDK names, {len(raw):,} B json")
        return 0
    ref = load_ref(a.ref)
    ents = []
    if a.overlay:
        for dp, dns, fns in os.walk(a.overlay):
            dns.sort()
            for fn in sorted(fns):
                fp = os.path.join(dp, fn)
                with open(fp, "rb") as f:
                    ents.append(("gbnative/" + os.path.relpath(fp, a.overlay).replace(os.sep, "/"), 0o100644, f.read()))
    view, _ = view_of(ents, ref)
    elfs = view.elfs
    rc = 0
    for r in a.root:
        order, missing = view.group(r)
        fails = view.failures(r)
        print(f"== {r}: {len(order)} objects in load order, {len(fails)} failure(s)")
        for i, p in enumerate(order):
            print(f"  {i:3d} {p}  (soname {elfs[p].get('soname')}, {len(elfs[p]['d'])} defs, {len(elfs[p]['u'])} undefs)")
        for by, n in missing:
            print(f"  MISSING NEEDED {n} (of {by})")
        for f in sorted(fails):
            if f[0] == "symbol":
                print(f"  UNDEFINED {f[1]}: {f[2]}")
        for k, v in duplicate_copies(view, order):
            print(f"  TWO COPIES of {k}: {v}")
        rc |= bool(fails)
    for who in a.symbols:
        order, _ = view.group(a.root[0])
        s = elfs[who]
        print(f"== providers of the {len(s['u'])} GLOBAL undefined symbols of {who} (group of {a.root[0]})")
        cnt = {}
        for sym, ver in s["u"]:
            p = view.provider(order, who, sym, ver)
            cnt[p] = cnt.get(p, 0) + 1
            print(f"  {sym}{'@' + ver if ver else ''} -> {p or 'UNDEFINED'}")
        print("  summary: " + ", ".join(f"{k or 'UNDEFINED'}={v}" for k, v in sorted(cnt.items(), key=str)))
    return rc


if __name__ == "__main__":
    sys.exit(main())
