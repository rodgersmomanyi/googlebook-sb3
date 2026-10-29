# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Googlebook OS build (GB-build) authors.
#
# disasm_paths.py -- disassembly-level proof that the patch (a) keeps the VEX
# AVX-VNNI instructions byte-for-byte (no arithmetic rewrite) but makes them
# unreachable on the taken dispatch path, and (b) for flag_zero targets, that the
# only code that reads the cleared SSSE3 flag is adler32_z's gate -- so clearing
# it changes nothing but adler32.
#
# Review fix (2026-10-26): the original checks only looked at the scalar window
# and at direct jumps to the dead call site.  For branch_flip targets this now
# proves the WHOLE-BINARY claim "no VEX AVX-VNNI instruction is reachable from
# any entry point of the patched library" (not just from the paths the
# equivalence probe exercised):
#   R1 every VEX VNNI instruction in the file lies inside the SIMD function
#      (bounds from the .eh_frame FDE that contains the call target);
#   R2 the only code reference into that function from outside it is the call
#      right after the patched gate (direct call/jmp/jcc targets and RIP-relative
#      operands of every instruction in the file);
#   R3 no 8-byte word in any non-executable PT_LOAD segment (RELR/RELA targets,
#      vtables, function-pointer tables) points into the function or at the dead
#      call;
#   R4 no dynamic symbol and no dynamic relocation (incl. Android packed RELA
#      and RELR, decoded with llvm-readelf) targets the function; no IRELATIVE
#      (ifunc) relocations; no DT_TEXTREL; no relocation overlaps the edit;
#   R5 the gate function has no indirect jump (so no jump table can enter the
#      dead call) and contains no VEX VNNI itself;
#   R6 the patched instruction is an unconditional jmp and no direct branch
#      targets the dead call site (so the call cannot be reached by fall-through
#      or by a jump).
# For flag_zero targets it now also checks that every reader of the flag is
# followed by a je and a call into a function that contains VEX VNNI (i.e. the
# adler32_simd_ gate), and that %eax is dead after the flag store (the edited
# `and` cannot change any other computed value).
#
# Usage (builder):
#   python3 -I disasm_paths.py --objdump objdump --readelf readelf \
#       [--llvm-readelf /build/ndk/.../llvm-readelf] \
#       --manifest ../patcher/manifest.json --root /var/tmp/vnniperf [name-substr ...]

import argparse
import json
import os
import re
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "patcher"))
import vnni_patch  # noqa: E402

VNNI_RE = re.compile(r"\bvpdp(?:wssd|wssds|busd|busds)\b")
INSN_RE = re.compile(r"^\s*([0-9a-f]+):\s+((?:[0-9a-f]{2} )+)\s*(.*)$")
# stripped binaries print bare targets as "0x3166f0", symbolized ones as "10fc0 <sym>"
DIRECT_RE = re.compile(r"^(?:bnd\s+|notrack\s+)?(call|jmp|j[a-z]{1,3}|loop\w*)\s+(?:0x)?([0-9a-f]+)\b")
RIPCOMMENT_RE = re.compile(r"\(%rip\).*#\s*(?:0x)?([0-9a-f]+)\b")
FDE_RE = re.compile(r"\bpc=([0-9a-f]+)\.\.([0-9a-f]+)")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def objdump_range(objdump, path, start, stop):
    return run([objdump, "-d", "-w", "--start-address=%#x" % start,
                "--stop-address=%#x" % stop, path])


def parse_insns(txt):
    """[(addr:int, asm:str)] from objdump -d -w output."""
    out = []
    for line in txt.splitlines():
        m = INSN_RE.match(line)
        if m and m.group(3):
            out.append((int(m.group(1), 16), m.group(3).strip()))
    return out


def count_vnni(objdump, path, start, stop):
    txt = objdump_range(objdump, path, start, stop)
    return sum(1 for _a, s in parse_insns(txt) if VNNI_RE.search(s)), txt


def fde_ranges(readelf, path):
    txt = run([readelf, "--debug-dump=frames", path])
    return sorted({(int(a, 16), int(b, 16)) for a, b in FDE_RE.findall(txt)})


def fde_containing(ranges, v):
    for a, b in ranges:
        if a <= v < b:
            return a, b
    return None


def load_segments(data):
    e_phoff = struct.unpack_from("<Q", data, 0x20)[0]
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 0x36)
    segs = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, p_flags = struct.unpack_from("<II", data, off)
        p_offset, p_vaddr = struct.unpack_from("<QQ", data, off + 8)
        p_filesz = struct.unpack_from("<Q", data, off + 32)[0]
        if p_type == 1:
            segs.append((p_vaddr, p_offset, p_filesz, p_flags))
    return segs


def data_words_into(data, segs, lo, hi, extra=()):
    """8-byte words in non-exec PT_LOAD file bytes whose value is in [lo,hi) or in extra."""
    hits = []
    for v, o, sz, fl in segs:
        if fl & 1:
            continue
        start = o + ((-o) % 8)
        for off in range(start, o + sz - 7, 8):
            w = struct.unpack_from("<Q", data, off)[0]
            if lo <= w < hi or w in extra:
                hits.append((v + off - o, w))
    return hits


def dyn_checks(readelf, llvm_readelf, path, lo, hi, edit_lo, edit_hi):
    """R4. Returns (ok, lines)."""
    lines, ok = [], True
    dyn = run([readelf, "-dW", path])
    textrel = bool(re.search(r"\(TEXTREL\)|\bTEXTREL\b", dyn))
    lines.append("DT_TEXTREL present: %s" % textrel)
    ok &= not textrel
    syms = run([readelf, "--dyn-syms", "-W", path])
    bad_syms = []
    for line in syms.splitlines():
        p = line.split()
        if len(p) >= 8 and p[0].endswith(":"):
            try:
                val = int(p[1], 16)
            except ValueError:
                continue
            if lo <= val < hi:
                bad_syms.append(line.strip())
    lines.append("dynamic symbols inside the SIMD function: %d" % len(bad_syms))
    ok &= not bad_syms
    rtool = llvm_readelf or readelf
    rel = run([rtool, "-rW", path])
    n_irel = len(re.findall(r"IRELATIV", rel))
    lines.append("IRELATIVE (ifunc) relocations: %d   [decoded with %s]"
                 % (n_irel, os.path.basename(rtool)))
    ok &= n_irel == 0
    overlap, into = [], []
    for line in rel.splitlines():
        p = line.split()
        if len(p) < 3 or not re.fullmatch(r"[0-9a-f]{8,16}", p[0]):
            continue
        r_off = int(p[0], 16)
        if r_off < edit_hi and r_off + 8 > edit_lo:
            overlap.append(line.strip())
        if len(p) >= 4 and re.fullmatch(r"[0-9a-f]{8,16}", p[3]):
            sv = int(p[3], 16)
            if lo <= sv < hi:
                into.append(line.strip())
    lines.append("relocations overlapping the edit bytes: %d; symbol relocations into "
                 "the SIMD function: %d" % (len(overlap), len(into)))
    ok &= not overlap and not into
    return ok, lines


def check_branch_flip(args, t, edit, op, pp, orig, patched):
    ev = int(edit["vaddr"], 16)
    dead = ev + 6
    call_v = int(re.search(r"call->(0x[0-9a-f]+)", edit["desc"]).group(1), 16)
    scalar_v = int(re.search(r"scalar@(0x[0-9a-f]+)", edit["desc"]).group(1), 16)
    ranges = fde_ranges(args.readelf, pp)
    fn = fde_containing(ranges, call_v)
    gate = fde_containing(ranges, ev)
    ok = True
    if not fn or not gate:
        print("  FDE bounds not found (simd %s, gate %s)" % (fn, gate))
        return False
    s, e = fn
    gs, ge = gate
    print("  SIMD function [%#x,%#x) (FDE of call target %#x); gate function [%#x,%#x)"
          % (s, e, call_v, gs, ge))
    n_orig, _ = count_vnni(args.objdump, op, s, e)
    n_pat, _ = count_vnni(args.objdump, pp, s, e)
    print("  SIMD function VEX VNNI count: orig=%d patched=%d (arithmetic untouched)"
          % (n_orig, n_pat))
    ok &= n_orig == n_pat and n_pat > 0
    full = parse_insns(run([args.objdump, "-d", "-w", pp]))
    all_vnni = [a for a, sm in full if VNNI_RE.search(sm)]
    outside = [a for a in all_vnni if not s <= a < e]
    print("  R1 whole-file VEX VNNI = %d, outside the SIMD function = %d"
          % (len(all_vnni), len(outside)))
    ok &= not outside and len(all_vnni) == n_pat
    refs = []
    for a, sm in full:
        if s <= a < e:
            continue
        m = DIRECT_RE.match(sm)
        tgt = int(m.group(2), 16) if m else None
        if tgt is None:
            m2 = RIPCOMMENT_RE.search(sm)
            tgt = int(m2.group(1), 16) if m2 else None
        if tgt is not None and (s <= tgt < e or tgt == dead):
            refs.append((a, sm))
    allowed = [(a, sm) for a, sm in refs if a == dead]
    other = [(a, sm) for a, sm in refs if a != dead]
    print("  R2 code refs into SIMD fn / dead call from outside: %d (allowed: the dead call "
          "at %#x = %d; other = %d)" % (len(refs), dead, len(allowed), len(other)))
    for a, sm in other:
        print("      UNEXPECTED %#x: %s" % (a, sm))
    ok &= len(allowed) == 1 and not other
    segs = load_segments(patched)
    words = data_words_into(bytes(patched), segs, s, e, extra=(dead,))
    print("  R3 data words pointing into SIMD fn / at dead call: %d" % len(words))
    ok &= not words
    dok, dlines = dyn_checks(args.readelf, args.llvm_readelf, pp, s, e, ev, ev + 6)
    for line in dlines:
        print("  R4 " + line)
    ok &= dok
    gate_insns = [(a, sm) for a, sm in full if gs <= a < ge]
    ind = [(a, sm) for a, sm in gate_insns if re.match(r"(?:notrack\s+)?jmp\s+\*", sm)]
    gvnni = [a for a, sm in gate_insns if VNNI_RE.search(sm)]
    print("  R5 gate function: indirect jumps = %d, VEX VNNI = %d" % (len(ind), len(gvnni)))
    ok &= not ind and not gvnni
    at_ev = [sm for a, sm in full if a == ev]
    at_jmp = [sm for a, sm in full if a == ev + 1]
    jmp_ok = at_ev[:1] == ["nop"] and bool(at_jmp) and \
        re.match(r"jmp\s+(?:0x)?%x\b" % scalar_v, at_jmp[0]) is not None
    into_dead = [(a, sm) for a, sm in full
                 if DIRECT_RE.match(sm) and int(DIRECT_RE.match(sm).group(2), 16) == dead]
    print("  R6 patched gate = %r ; %r ; direct branches to dead call %#x = %d"
          % (at_ev[:1], at_jmp[:1], dead, len(into_dead)))
    ok &= jmp_ok and not into_dead
    n_scalar, _ = count_vnni(args.objdump, pp, scalar_v, ev)
    print("  scalar window [%#x,%#x) VEX VNNI = %d" % (scalar_v, ev, n_scalar))
    ok &= n_scalar == 0
    print("  => no VEX AVX-VNNI instruction is reachable from any entry point: %s"
          % ("PROVEN" if ok else "NOT PROVEN"))
    return ok


def check_flag_zero(args, t, edit, op, pp):
    ev = int(edit["vaddr"], 16)
    flag_v = int(re.search(r"flag@(0x[0-9a-f]+)", edit["desc"]).group(1), 16)
    full = parse_insns(run([args.objdump, "-d", "-w", op]))
    idx = {a: i for i, (a, _sm) in enumerate(full)}
    ranges = fde_ranges(args.readelf, op)
    readers, writers, others = [], [], []
    for i, (a, sm) in enumerate(full):
        m = RIPCOMMENT_RE.search(sm)
        if not m or int(m.group(1), 16) != flag_v:
            continue
        if re.match(r"cmpl\s+\$0x0,", sm):
            readers.append(i)
        elif re.match(r"mov\s+%eax,", sm):
            writers.append(i)
        else:
            others.append((a, sm))
    print("  SSSE3 flag @ %#x: %d reader(s), %d writer(s), %d other ref(s)"
          % (flag_v, len(readers), len(writers), len(others)))
    ok = len(readers) >= 1 and len(writers) == 1 and not others
    for i in readers:
        a, sm = full[i]
        nxt = full[i + 1:i + 6]
        je = [x for x in nxt[:2] if re.match(r"je\s+", x[1])]
        calls = [x for x in nxt if DIRECT_RE.match(x[1]) and x[1].startswith("call")]
        good = False
        if je and calls:
            tgt = int(DIRECT_RE.match(calls[0][1]).group(2), 16)
            fn = fde_containing(ranges, tgt)
            nv = count_vnni(args.objdump, op, fn[0], fn[1])[0] if fn else 0
            good = nv > 0
            print("    reader %#x: %s -> je, call %#x (fn %s, %d VEX VNNI) %s"
                  % (a, sm.split("#")[0].strip(), tgt,
                     "[%#x,%#x)" % fn if fn else "?", nv, "ok" if good else "NOT A SIMD GATE"))
        else:
            print("    reader %#x: %s -> NOT followed by je+call" % (a, sm))
        ok &= good
    # the edited `and` must feed only the flag store: next insn stores %eax to the
    # flag, and %eax is overwritten before any later read in the function
    i = idx.get(ev)
    dead_ok = False
    if i is not None and i + 1 < len(full):
        st = full[i + 1][1]
        if re.match(r"mov\s+%eax,", st) and RIPCOMMENT_RE.search(st) and \
                int(RIPCOMMENT_RE.search(st).group(1), 16) == flag_v:
            for _a, sm in full[i + 2:i + 40]:
                if re.match(r"ret\b", sm):
                    dead_ok = True
                    break
                mw = re.match(r"(?:mov|xor)l?\s+(\S+),%eax$", sm)
                if mw and "%eax" not in mw.group(1) and "%rax" not in mw.group(1):
                    dead_ok = True
                    break
                if re.search(r"%[re]?ax\b|%al\b|%ah\b", sm):
                    break
    print("  %%eax dead after the flag store: %s" % dead_ok)
    ok &= dead_ok
    print("  CHECK flag_zero (only the adler32 SIMD gate reads the flag; edit feeds "
          "nothing else): %s" % ("PASS" if ok else "FAIL"))
    return ok


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--objdump", default="objdump")
    ap.add_argument("--readelf", default="readelf")
    ap.add_argument("--llvm-readelf", default=None,
                    help="decodes Android packed RELA (APS2) and RELR; recommended")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("filters", nargs="*")
    args = ap.parse_args(argv)

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    tmp = tempfile.mkdtemp(prefix="vnni_disasm_")
    all_ok = True
    n = 0
    for t in manifest["targets"]:
        if args.filters and not any(s in t["name"] for s in args.filters):
            continue
        n += 1
        src = os.path.join(args.root, t.get("scan_path", t["path"]))
        with open(src, "rb") as f:
            orig = f.read()
        # apply_edits re-verifies the original bytes; a mismatch raises here.
        patched, _applied = vnni_patch.apply_edits(orig, t)
        op = os.path.join(tmp, "orig.bin")
        pp = os.path.join(tmp, "patched.bin")
        with open(op, "wb") as f:
            f.write(orig)
        with open(pp, "wb") as f:
            f.write(bytes(patched))
        edit = t["edits"][0]
        ev = int(edit["vaddr"], 16)
        print("\n==================== %s  [%s / %s]" % (t["name"], t["strategy"], t["proof"]))
        print("  orig bytes verified at %s: %s (apply_edits accepted)" % (edit["vaddr"], edit["orig"]))
        for label, path in (("ORIGINAL", op), ("PATCHED", pp)):
            print("  --- edit window, %s ---" % label)
            print("".join("    %x: %s\n" % (a, sm) for a, sm in
                          parse_insns(objdump_range(args.objdump, path, ev - 16, ev + 24))))
        if t["strategy"] == "branch_flip":
            ok = check_branch_flip(args, t, edit, op, pp, orig, patched)
            print("  CHECK branch_flip: %s" % ("PASS" if ok else "FAIL"))
        elif t["strategy"] == "flag_zero":
            ok = check_flag_zero(args, t, edit, op, pp)
        else:
            print("  unknown strategy")
            ok = False
        all_ok = all_ok and ok

    print("\n==================== disasm proof (%d target(s)): %s"
          % (n, "ALL PASS" if all_ok and n else "FAIL"))
    return 0 if all_ok and n else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
