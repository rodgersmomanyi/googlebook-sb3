#!/usr/bin/env python3
# Idempotently patch common/arch/x86/kernel/traps.c to emulate VEX AVX-VNNI
# (vpdpbusd/vpdpbusds/vpdpwssd/vpdpwssds) which the Ice-Lake host lacks but the
# Googlebook binaries use unconditionally.
import sys, re
TRAPS = "/build/gki/common/arch/x86/kernel/traps.c"
MARK = "GB_VNNI_EMULATION"
src = open(TRAPS).read()
if MARK in src:
    print("already patched"); sys.exit(0)

EMU = r'''
/* ''' + MARK + r''': emulate VEX-encoded AVX-VNNI absent on this host CPU. */
#include <asm/fpu/types.h>
static s32 gb_sat_s32(s64 v)
{
	if (v > (s64)S32_MAX)
		return S32_MAX;
	if (v < (s64)S32_MIN)
		return S32_MIN;
	return (s32)v;
}
static noinline bool gb_emulate_vnni(struct pt_regs *regs)
{
	u8 ib[5];
	u8 p0, p1, op, modrm;
	int mod, L, dst, src1, src2, i, k;
	u32 *xmm;

	if (!user_mode(regs))
		return false;
	if (copy_from_user(ib, (const void __user *)regs->ip, sizeof(ib)))
		return false;
	if (ib[0] != 0xc4)              /* 3-byte VEX only */
		return false;
	p0 = ib[1]; p1 = ib[2]; op = ib[3]; modrm = ib[4];
	if ((p0 & 0x1f) != 0x02)        /* 0F38 opcode map */
		return false;
	if ((p1 & 0x03) != 0x01)        /* 66 prefix (pp) */
		return false;
	if (op < 0x50 || op > 0x53)     /* vpdpbusd/busds/wssd/wssds */
		return false;
	mod = (modrm >> 6) & 3;
	if (mod != 3)                   /* register-register form only */
		return false;
	L = (p1 >> 2) & 1;
	if (L != 0)                     /* 128-bit only */
		return false;
	src1 = (~(p1 >> 3)) & 0x0f;
	dst  = ((modrm >> 3) & 7) | ((!((p0 >> 7) & 1)) << 3);
	src2 = (modrm & 7)       | ((!((p0 >> 5) & 1)) << 3);

	kernel_fpu_begin();
	xmm = x86_task_fpu(current)->fpstate->regs.xsave.i387.xmm_space;
	{
		u32 *d = &xmm[dst  * 4];
		u32 *a = &xmm[src1 * 4];
		u32 *c = &xmm[src2 * 4];
		for (i = 0; i < 4; i++) {
			s64 acc = (s32)d[i];
			if (op >= 0x52) {                    /* word variants */
				const s16 *aw = (const s16 *)&a[i];
				const s16 *bw = (const s16 *)&c[i];
				acc += (s32)aw[0] * (s32)bw[0];
				acc += (s32)aw[1] * (s32)bw[1];
			} else {                             /* byte variants */
				const u8 *ab = (const u8 *)&a[i];
				const s8 *bb = (const s8 *)&c[i];
				for (k = 0; k < 4; k++)
					acc += (s32)ab[k] * (s32)bb[k];
			}
			if (op == 0x51 || op == 0x53)        /* saturating */
				d[i] = (u32)gb_sat_s32(acc);
			else                                 /* wrapping */
				d[i] = (u32)(s32)acc;
		}
	}
	/* ensure xrstor reloads the (modified) SSE/XMM state */
	x86_task_fpu(current)->fpstate->regs.xsave.header.xfeatures |= (1ULL << 1);
	kernel_fpu_end();

	regs->ip += 5;                  /* c4 + p0 + p1 + opcode + modrm */
	return true;
}

'''

# Insert the emulator right before the handle_invalid_op definition block.
anchor = "#ifdef CONFIG_X86_F00F_BUG\nvoid handle_invalid_op(struct pt_regs *regs)"
if anchor not in src:
    print("ERROR: handle_invalid_op anchor not found"); sys.exit(1)
src = src.replace(anchor, EMU + anchor, 1)

# Add the emulation call at the top of handle_invalid_op's body.
body = ('{\n\tdo_error_trap(regs, 0, "invalid opcode", X86_TRAP_UD, SIGILL,\n'
        '\t\t      ILL_ILLOPN, error_get_trap_addr(regs));\n}')
newbody = ('{\n\tif (gb_emulate_vnni(regs))\n\t\treturn;\n\n'
           '\tdo_error_trap(regs, 0, "invalid opcode", X86_TRAP_UD, SIGILL,\n'
           '\t\t      ILL_ILLOPN, error_get_trap_addr(regs));\n}')
if body not in src:
    print("ERROR: handle_invalid_op body not found"); sys.exit(1)
src = src.replace(body, newbody, 1)

open(TRAPS, "w").write(src)
print("patched OK")
