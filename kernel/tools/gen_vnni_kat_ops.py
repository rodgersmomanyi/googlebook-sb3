#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate vnni_kat_ops.h: the AVX-VNNI instruction blobs the KAT executes, assembled by GNU as (the oracle, so
the encodings are correct by construction and independent of the kernel decoder).

Blob kinds (r2, wave 3 review H1/H2/M1):
  full      for each (encoding VEX/EVEX, opcode 50-53, L 0/1, a fixed register tuple, operand form reg/mem0/mem64):
            load zmm0-31 from (%rdi + 64*i) ; <the instruction under test> ; store zmm0-31 to (%rdi) ; ret
            The memory forms read [%rsi] (mem0) or [%rsi+64] (mem64); the caller's buffer is 128 bytes.
            EVEX memory forms exist too, so the native reference (and the host self-check) covers every form.
  init_sse  for the init-state case (tuple 0/1/2, vpdpbusd, L 0/1, VEX and EVEX): with %rdi -> {pre, post, in}
            (3 x 2048 bytes) and %rsi -> 8 bytes:
              store zmm0-31 -> pre ; vzeroall ; xgetbv(ECX=1) -> (%rsi) ; <op, all inputs 0> ; store zmm0-31 -> post
            Nothing runs between vzeroall, the XINUSE snapshot and the op, so the snapshot is the state the #UD
            handler sees.
  init_ymm  the YMM-only init variant: store -> pre ; vzeroupper ; VEX.128 vmovdqu of in.z[i][0..15] into xmm0-15 ;
            xgetbv(ECX=1) -> (%rsi) ; <op> ; store -> post
Extra blobs: kat_call_code (load zmm0-31 from (%rdi) ; call *%rdx ; store ; ret: runs an op placed elsewhere, e.g.
at a page end, on a full register file) and kat_clobber_code (vpternlogd $0xff on zmm0-31 ; ret: the SIGALRM
handler's clobber of all 32 registers, all 512 bits).
The header records each blob's bytes, its op-instruction bytes (for the objdump cross-check), and its fields.

Usage (builder; needs as/objdump/objcopy): python3 -I gen_vnni_kat_ops.py <out header> <workdir>
"""
import os
import re
import subprocess
import sys

OPS = {0x50: "vpdpbusd", 0x51: "vpdpbusds", 0x52: "vpdpwssd", 0x53: "vpdpwssds"}
# fixed register tuples (dst, src1, src2): low, all-equal, high (REX/EVEX extensions), mixed
TUPLES = [(0, 1, 2), (2, 2, 2), (3, 4, 5), (8, 9, 10), (15, 14, 13), (1, 15, 8), (7, 0, 9), (11, 6, 11)]
# operand forms: ("reg", -), ("mem0", disp 0), ("mem64", disp 0x40)
FORMS = [("reg", None), ("mem0", 0), ("mem64", 0x40)]
FCODE = {"reg": 0, "mem0": 1, "mem64": 2}
ENCS = [("vex", "{vex}"), ("evex", "{evex}")]
KINDS = {"full": 0, "init_sse": 1, "init_ymm": 2}
ZCTX = 2048                     # sizeof(struct zctx) in vnni_kat.c
OP_LEN = {("vex", "mem64"): 6, ("evex", "mem64"): 7, ("vex", "mem0"): 5, ("evex", "mem0"): 6,
          ("vex", "reg"): 5, ("evex", "reg"): 6}


def vreg(l, n):
    return ("%ymm" if l else "%xmm") + str(n)


def instr(prefix, l, op, dst, src1, form, disp, src2):
    mn = OPS[op]
    if form == "reg":
        src = vreg(l, src2)
    elif disp:
        src = str(disp) + "(%rsi)"
    else:
        src = "(%rsi)"
    # AT&T: src2/mem, src1, dst
    return prefix + " " + mn + " " + src + ", " + vreg(l, src1) + ", " + vreg(l, dst)


def load_all(base=0):
    return "\n".join("\tvmovdqu64 " + str(base + 64 * i) + "(%rdi), %zmm" + str(i) for i in range(32))


def store_all(base=0):
    return "\n".join("\tvmovdqu64 %zmm" + str(i) + ", " + str(base + 64 * i) + "(%rdi)" for i in range(32))


XGETBV1 = "\tmovl $1, %ecx\n\txgetbv\n\tmovl %eax, (%rsi)\n\tmovl %edx, 4(%rsi)"


def main(out, work):
    os.makedirs(work, exist_ok=True)
    entries = []        # (name, enc, op, l, dst, src1, src2, form, disp, kind)
    lines = [".text"]
    idx = 0

    def add(kind, enc, pfx, op, l, dst, src1, src2, form, disp):
        nonlocal idx
        name = "b%d" % idx
        idx += 1
        ins = "\t" + instr(pfx, l, op, dst, src1, form, disp, src2)
        lines.append("%s_op:" % name)
        lines.append(ins)
        lines.append("%s_opend:" % name)
        lines.append("%s_code:" % name)
        if kind == "full":
            lines.extend([load_all(), ins, store_all()])
        elif kind == "init_sse":
            lines.extend([store_all(0), "\tvzeroall", XGETBV1, ins, store_all(ZCTX)])
        else:
            lines.extend([store_all(0), "\tvzeroupper"])
            lines.extend(["\t{vex} vmovdqu %d(%%rdi), %%xmm%d" % (2 * ZCTX + 64 * i, i) for i in range(16)])
            lines.extend([XGETBV1, ins, store_all(ZCTX)])
        lines.append("\tret")
        lines.append("%s_codeend:" % name)
        entries.append((name, enc, op, l, dst, src1, src2, form, disp, kind))

    for enc, pfx in ENCS:
        for op in OPS:
            for l in (0, 1):
                for dst, src1, src2 in TUPLES:
                    for form, disp in FORMS:
                        add("full", enc, pfx, op, l, dst, src1, src2, form, disp)
    for kind in ("init_sse", "init_ymm"):
        for enc, pfx in ENCS:
            for l in (0, 1):
                add(kind, enc, pfx, 0x50, l, 0, 1, 2, "reg", None)
    lines += ["kat_call:", load_all(), "\tcall *%rdx", store_all(), "\tret", "kat_callend:"]
    lines += ["kat_clob:"] + ["\tvpternlogd $0xff, %%zmm%d, %%zmm%d, %%zmm%d" % (i, i, i) for i in range(32)]
    lines += ["\tret", "kat_clobend:"]
    src = os.path.join(work, "ops.s")
    obj = os.path.join(work, "ops.o")
    with open(src, "w") as f:
        f.write("\n".join(lines) + "\n")
    subprocess.run(["as", "--64", "-o", obj, src], check=True)
    syms = subprocess.run(["objdump", "-t", obj], check=True, capture_output=True, text=True).stdout
    addr = {}
    for m in re.finditer(r"^([0-9a-f]{16}) l\s+\.text\s+[0-9a-f]+ (\w+)$", syms, re.M):
        addr[m.group(2)] = int(m.group(1), 16)
    code = subprocess.run(["objcopy", "-O", "binary", "-j", ".text", obj, "/dev/stdout"], check=True,
                          capture_output=True).stdout

    def blob(a, b):
        return code[addr[a]:addr[b]]

    bad = []
    for (name, enc, op, l, dst, src1, src2, form, disp, kind) in entries:
        opb = blob(name + "_op", name + "_opend")
        if (enc == "vex") != (opb[0] == 0xc4) or (enc == "evex") != (opb[0] == 0x62):
            bad.append("%s: %s prefix byte %02x" % (name, enc, opb[0]))
        if len(opb) != OP_LEN[(enc, form)]:
            bad.append("%s: %s %s op is %d bytes, want %d" % (name, enc, form, len(opb), OP_LEN[(enc, form)]))
    if bad:
        print("\n".join(bad))
        return 1

    def arr(cvar, b):
        return "static const unsigned char %s[] = {%s};\n" % (cvar, ",".join(str(x) for x in b))

    with open(out, "w") as f:
        f.write("/* SPDX-License-Identifier: GPL-2.0 */\n")
        f.write("/* GENERATED by gen_vnni_kat_ops.py r2 (GNU as, %d op blobs); do not edit. */\n" % len(entries))
        f.write("#ifndef _VNNI_KAT_OPS_H\n#define _VNNI_KAT_OPS_H\n")
        f.write("#define K_FULL %d\n#define K_INIT_SSE %d\n#define K_INIT_YMM %d\n"
                % (KINDS["full"], KINDS["init_sse"], KINDS["init_ymm"]))
        f.write("#define KAT_ZCTX %d\n" % ZCTX)
        f.write("struct kat_op { const char *name; int enc, op, l, dst, src1, src2; int form; int disp;\n"
                "\tconst unsigned char *code; int clen; const unsigned char *opbytes; int oplen; int kind; };\n")
        meta = []
        for (name, enc, op, l, dst, src1, src2, form, disp, kind) in entries:
            opb = blob(name + "_op", name + "_opend")
            cb = blob(name + "_code", name + "_codeend")
            f.write(arr("c_" + name, cb))
            f.write(arr("o_" + name, opb))
            meta.append('{"%s",%d,%d,%d,%d,%d,%d,%d,%d,c_%s,%d,o_%s,%d,%d}'
                        % (name, 1 if enc == "evex" else 0, op, l, dst, src1, src2, FCODE[form], disp or 0,
                           name, len(cb), name, len(opb), KINDS[kind]))
        f.write("static const struct kat_op kat_ops[] = {\n  " + ",\n  ".join(meta) + "\n};\n")
        f.write("#define KAT_NOPS %d\n" % len(meta))
        f.write("static const int kat_tuples[][3] = {%s};\n" % ",".join("{%d,%d,%d}" % t for t in TUPLES))
        f.write("#define KAT_NTUPLES %d\n" % len(TUPLES))
        cb = blob("kat_call", "kat_callend")
        f.write(arr("kat_call_code", cb) + "#define KAT_CALL_LEN %d\n" % len(cb))
        cb = blob("kat_clob", "kat_clobend")
        f.write(arr("kat_clobber_code", cb) + "#define KAT_CLOBBER_LEN %d\n" % len(cb))
        f.write("#endif\n")
    # a disassembly cross-check file: op bytes -> objdump mnemonic, for gen_vnni_kat_check.py
    with open(os.path.join(work, "ops_meta.txt"), "w") as f:
        for (name, enc, op, l, dst, src1, src2, form, disp, kind) in entries:
            opb = blob(name + "_op", name + "_opend")
            f.write("%s %s %s L%d dst=%d src1=%d src2=%d form=%s disp=%d kind=%s %s\n"
                    % (name, enc, OPS[op], l, dst, src1, src2, form, disp or 0, kind, opb.hex()))
    n = {k: sum(1 for e in entries if e[9] == k) for k in KINDS}
    print("wrote %s: %d op blobs (%d full, %d init_sse, %d init_ymm) + call trampoline + clobber"
          % (out, len(entries), n["full"], n["init_sse"], n["init_ymm"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
