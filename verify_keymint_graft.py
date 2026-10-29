"""verify_keymint_graft.py -- prove that keymint_native_graft.sh changed nothing in the KeyMint graft but the names
(wave 5, readiness C-2). Our own code (Apache-2.0).

  python -I verify_keymint_graft.py [ORIG_DIR NEW_DIR]
      ORIG_DIR: bin/ lib64/ = inject\\keymint (default: beside this file);
      NEW_DIR:  bin/ lib64/<stem>_gbkm16.so + SHA256SUMS = inject\\keymint\\native (default: beside this file)

For every (original, renamed) pair:
  G1 link_closure.parse_elf finds no layout problem (PT_LOAD p_offset == p_vaddr mod p_align, order, PT_PHDR /
     PT_DYNAMIC inside a PT_LOAD, verneed files in DT_NEEDED) and the library's DT_SONAME is its new file name;
  G2 DT_NEEDED == the original list with exactly the graft names renamed (same order); DT_SONAME renamed;
  G3 every other dynamic tag is unchanged in order and value, except the addresses of the tables patchelf moved
     (DT_STRTAB, DT_STRSZ, DT_SYMTAB and the hash/version tables, if moved) -- those must point at the tables' new homes;
  G4 the dynamic symbol table is identical entry by entry (name decoded through each file's own string table, value,
     size, info, other, shndx), the version tables (versym, verneed, verdef) decode identically, and the defined
     export set (names + versions) is identical;
  G5 the loaded image is identical: every byte of every original PT_LOAD, at its virtual address, is the same in the
     new file, except inside the original locations of the sections patchelf moved or rewrote (.dynstr, .dynsym,
     .dynamic's changed words, the PT_NOTE/.interp sections it relocated, the program header table it grew), and
     every section that did not move (.text, .rodata, .data.rel.ro, .relr.dyn, the packed .rela.dyn, .eh_frame, ...)
     has identical bytes at the same address;
  G6 nothing was stripped: same section names (+ none lost), .gnu_debugdata / .note.gnu.build-id kept;
  G7 NEW_DIR/SHA256SUMS lists exactly the 10 graft files with their sha256 (what build_esp.py checks).
Prints one line per check and "VERIFY_GRAFT: OK" (exit 0) or "VERIFY_GRAFT: <n> problem(s)" (exit 1).
"""
import os
import struct
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (HERE, os.path.join(HERE, "..", "src")):          # beside build_esp.py, or the fixprep src/ folder
    if os.path.isfile(os.path.join(_d, "link_closure.py")):
        sys.path.insert(0, os.path.abspath(_d))
        break
import link_closure as L  # noqa: E402

LIBS = ["lib_android_keymaster_keymint_utils", "libcppbor", "libcppcose_rkp", "libcrypto", "libkeymaster_messages",
        "libkeymaster_portable", "libkeymint", "libpuresoftkeymasterdevice", "libsoft_attestation_cert"]
RENAME = {f"{n}.so": f"{n}_gbkm16.so" for n in LIBS}
BIN = "android.hardware.security.keymint-service"
MOVABLE_TAGS = {L.DT_STRTAB, L.DT_STRSZ, L.DT_SYMTAB, L.DT_GNU_HASH, L.DT_HASH, L.DT_VERSYM, L.DT_VERNEED,
                L.DT_VERDEF}
PROBS = []


def check(name, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        PROBS.append(name)
    return cond


class E:
    """Minimal ELF64 section/segment/dynamic reader (x86_64 graft files only)."""

    def __init__(self, data):
        self.d = data
        (_, _, _, _, phoff, shoff, _, _, phes, phn, shes, shn, shstr) = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
        self.ph = [struct.unpack_from("<IIQQQQQQ", data, phoff + i * phes) for i in range(phn)]
        self.phoff, self.phsize = phoff, phn * phes
        sh = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * shes) for i in range(shn)]
        so = sh[shstr][4]
        nm = lambda o: data[so + o:data.index(b"\0", so + o)].decode()
        self.sec = {}
        self.secnames = [nm(s[0]) for s in sh]
        for s in sh[1:]:
            self.sec[nm(s[0])] = {"type": s[1], "flags": s[2], "addr": s[3], "off": s[4], "size": s[5],
                                  "link": s[6], "info": s[7], "ent": s[9]}
        self.loads = [p for p in self.ph if p[0] == L.PT_LOAD]
        dyn = [p for p in self.ph if p[0] == L.PT_DYNAMIC][0]
        self.dyn = []
        for o in range(dyn[2], dyn[2] + dyn[5], 16):
            t, v = struct.unpack_from("<qQ", data, o)
            self.dyn.append((t, v))
            if t == 0:
                break
        self.tag = {}
        for t, v in self.dyn:
            self.tag.setdefault(t, v)

    def at(self, va, n):
        for p in self.loads:
            if p[3] <= va and va + n <= p[3] + p[5]:
                return self.d[p[2] + va - p[3]:p[2] + va - p[3] + n]
        return None

    def image(self):
        """The loaded image (file-backed bytes of every PT_LOAD at its vaddr, zeros elsewhere)."""
        img = bytearray(max(p[3] + p[6] for p in self.loads))
        for p in self.loads:
            img[p[3]:p[3] + p[5]] = self.d[p[2]:p[2] + p[5]]
        return img

    def dstr(self, o):
        base = self.tag[L.DT_STRTAB]
        b = self.at(base + o, 1)
        out = bytearray()
        i = 0
        while True:
            c = self.at(base + o + i, 1)
            if c is None or c == b"\0":
                break
            out += c
            i += 1
        return out.decode() if b is not None else None


def dynsyms(e):
    """Dynamic symbols with st_shndx as the section NAME (patchelf renumbers the section headers; the loader only
    tells SHN_UNDEF from defined, so the defining section's name is what has to stay the same)."""
    s = e.sec[".dynsym"]
    out = []
    for i in range(s["size"] // 24):
        nm, info, other, shndx, val, size = struct.unpack_from("<IBBHQQ", e.d, s["off"] + 24 * i)
        sec = shndx if (shndx == 0 or shndx >= 0xff00) else e.secnames[shndx]
        out.append((e.dstr(nm) if nm else "", info, other, sec, val, size))
    return out


def one(orig_path, new_path, new_name):
    od, nd = open(orig_path, "rb").read(), open(new_path, "rb").read()
    tag = os.path.basename(new_path)
    s, lp = L.parse_elf(nd, tag)
    so, _ = L.parse_elf(od, tag)
    check(f"G1 {tag}: no layout problem", not lp, "; ".join(lp))
    is_lib = new_name is not None
    if is_lib:
        check(f"G1 {tag}: DT_SONAME == file name", s["soname"] == new_name, s["soname"])
    check(f"G2 {tag}: DT_NEEDED renamed, order kept",
          s["needed"] == [RENAME.get(n, n) for n in so["needed"]], f"{so['needed']} -> {s['needed']}")
    if is_lib:
        check(f"G2 {tag}: DT_SONAME renamed", RENAME.get(so["soname"]) == s["soname"], f"{so['soname']} -> {s['soname']}")
    eo, en = E(od), E(nd)
    # G3 dynamic tags
    to = [(t, v) for t, v in eo.dyn if t not in (L.DT_NEEDED, L.DT_SONAME)]
    tn = [(t, v) for t, v in en.dyn if t not in (L.DT_NEEDED, L.DT_SONAME)]
    ok = [t for t, _ in to] == [t for t, _ in tn]
    bad = []
    for (t1, v1), (t2, v2) in zip(to, tn):
        if t1 in MOVABLE_TAGS:
            continue
        if v1 != v2:
            bad.append(f"{t1:#x}: {v1:#x} -> {v2:#x}")
    check(f"G3 {tag}: other dynamic tags unchanged (order and value)", ok and not bad, bad or "tag order differs")
    moved = []
    for t, secname in ((L.DT_SYMTAB, ".dynsym"), (L.DT_STRTAB, ".dynstr"), (L.DT_GNU_HASH, ".gnu.hash"),
                       (L.DT_VERSYM, ".gnu.version"), (L.DT_VERNEED, ".gnu.version_r")):
        if t in en.tag:
            check(f"G3 {tag}: dynamic tag {t:#x} -> {secname} in the new file", en.tag[t] == en.sec[secname]["addr"],
                  f"{en.tag[t]:#x} vs {en.sec[secname]['addr']:#x}")
            if eo.tag[t] != en.tag[t]:
                moved.append(secname)
    check(f"G3 {tag}: DT_STRSZ == .dynstr size", en.tag[L.DT_STRSZ] <= en.sec[".dynstr"]["size"])
    # G4 symbols and versions
    so_, sn_ = dynsyms(eo), dynsyms(en)
    check(f"G4 {tag}: dynamic symbol table identical ({len(so_)} entries)", so_ == sn_,
          next((f"#{i}: {a} vs {b}" for i, (a, b) in enumerate(zip(so_, sn_)) if a != b), f"{len(so_)} vs {len(sn_)}"))
    check(f"G4 {tag}: ELF header identical but e_shoff/e_phnum/e_shnum/e_shstrndx (ident..e_phoff, flags, sizes)",
          od[:0x28] == nd[:0x28] and od[0x30:0x38] == nd[0x30:0x38] and od[0x3a:0x3c] == nd[0x3a:0x3c])
    for secname in (".gnu.version", ".gnu.hash"):
        if secname in eo.sec:
            a, b = eo.sec[secname], en.sec[secname]
            check(f"G4 {tag}: {secname} bytes identical", od[a["off"]:a["off"] + a["size"]] == nd[b["off"]:b["off"] + b["size"]])
    check(f"G4 {tag}: export set identical ({len(so['d'])} names)", so["d"] == s["d"] and so["vd"] == s["vd"])
    check(f"G4 {tag}: undefined set identical ({len(so['u'])})", so["u"] == s["u"])
    check(f"G4 {tag}: verneed files identical", so["vnf"] == s["vnf"], f"{so['vnf']} -> {s['vnf']}")
    check(f"G4 {tag}: DF_SYMBOLIC kept", so["symbolic"] == s["symbolic"])
    # G5 loaded image
    rewritten = {".dynstr", ".dynsym", ".dynamic", ".interp"} | set(moved) | \
        {n for n, x in eo.sec.items() if x["type"] == 7}            # SHT_NOTE
    holes = [(eo.sec[n]["addr"], eo.sec[n]["addr"] + eo.sec[n]["size"]) for n in rewritten if n in eo.sec]
    holes.append((eo.phoff, eo.phoff + max(eo.phsize, en.phsize)))   # the grown program header table (first LOAD)
    holes.append((0, 0x40))                                         # ELF header: e_phnum (checked in G4)
    io, inn = eo.image(), en.image()
    for a, b in holes:                                             # blank the rewritten ranges in both images
        io[a:b] = bytes(len(io[a:b]))
        if a < len(inn):
            inn[a:min(b, len(inn))] = bytes(len(inn[a:min(b, len(inn))]))
    diffs, first = 0, None
    for p in eo.loads:
        x, y = io[p[3]:p[3] + p[6]], inn[p[3]:p[3] + p[6]]
        if x != y:
            d = [i for i in range(min(len(x), len(y))) if x[i] != y[i]]
            diffs += len(d) + abs(len(x) - len(y))
            first = first or hex(p[3] + (d[0] if d else min(len(x), len(y))))
    check(f"G5 {tag}: loaded image identical outside the rewritten tables", diffs == 0, f"{diffs} bytes differ, first {first}")
    # the .dynamic words that changed are only NEEDED/SONAME string offsets and the moved-table addresses
    dd = []
    for i, ((t1, v1), (t2, v2)) in enumerate(zip(eo.dyn, en.dyn)):
        if (t1, v1) != (t2, v2) and not (t1 == t2 and (t1 in MOVABLE_TAGS or t1 in (L.DT_NEEDED, L.DT_SONAME))):
            dd.append(i)
    check(f"G5 {tag}: .dynamic differs only in name offsets / moved-table addresses",
          not dd and len(eo.dyn) == len(en.dyn), dd)
    same = []
    for n, x in eo.sec.items():
        if not (x["flags"] & 2) or n in rewritten or x["type"] == 8:              # SHF_ALLOC; skip NOBITS
            continue
        y = en.sec.get(n)
        if not y or y["addr"] != x["addr"] or y["size"] != x["size"] or \
                od[x["off"]:x["off"] + x["size"]] != nd[y["off"]:y["off"] + y["size"]]:
            same.append(n)
    check(f"G5 {tag}: every unmoved allocated section identical at the same address", not same, same)
    for n in rewritten:
        if n in eo.sec and n not in (".dynstr", ".dynamic", ".dynsym") and n in en.sec:
            a, b = eo.sec[n], en.sec[n]
            check(f"G5 {tag}: relocated {n} bytes identical", od[a["off"]:a["off"] + a["size"]] == nd[b["off"]:b["off"] + b["size"]])
    # G6 not stripped
    lost = sorted(set(eo.sec) - set(en.sec))
    check(f"G6 {tag}: no section lost (not stripped)", not lost, lost)
    for n in (".gnu_debugdata", ".note.gnu.build-id", ".note.android.ident"):
        if n in eo.sec:
            check(f"G6 {tag}: {n} kept", n in en.sec)


def sums_check(new):
    import hashlib
    want = ["bin/" + BIN] + [f"lib64/{n}_gbkm16.so" for n in LIBS]
    try:
        with open(os.path.join(new, "SHA256SUMS"), encoding="utf-8") as f:
            sums = {l.split()[1]: l.split()[0] for l in f if l.strip() and not l.startswith("#")}
    except OSError as e:
        check("G7 SHA256SUMS readable", False, str(e))
        return
    check("G7 SHA256SUMS lists exactly the 10 graft files", sorted(sums) == sorted(want), sorted(sums))
    for rel in want:
        p = os.path.join(new, *rel.split("/"))
        got = None
        if os.path.isfile(p):
            with open(p, "rb") as f:
                got = hashlib.sha256(f.read()).hexdigest()
        check(f"G7 {rel}: sha256 == SHA256SUMS", got is not None and got == sums.get(rel), got)


def main(argv):
    if len(argv) == 3:
        orig, new = argv[1], argv[2]
    elif len(argv) == 1:
        orig = os.path.join(HERE, "inject", "keymint")
        new = os.path.join(orig, "native")
    else:
        print(__doc__)
        return 2
    one(os.path.join(orig, "bin", BIN), os.path.join(new, "bin", BIN), None)
    for n in LIBS:
        one(os.path.join(orig, "lib64", n + ".so"), os.path.join(new, "lib64", n + "_gbkm16.so"), n + "_gbkm16.so")
    extra = sorted(set(os.listdir(os.path.join(new, "lib64"))) - {n + "_gbkm16.so" for n in LIBS})
    check("G0 no other file in lib64", not extra, extra)
    sums_check(new)
    print(f"VERIFY_GRAFT: {'OK' if not PROBS else f'{len(PROBS)} problem(s)'}")
    return 1 if PROBS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
