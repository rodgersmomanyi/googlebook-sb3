/* SPDX-License-Identifier: GPL-2.0 */
/*
 * TEST ONLY (KAT-0 negative control, K-14): the xstate handling of the k13/k14 emulator (apply_vnni3.py, v3),
 * transcribed onto the gb_vnni_merge() interface. It keeps the three defects that k15 fixes:
 *   1. it reads i387.xmm_space even when SSE is in its init state and then sets the SSE bit (stale XMM0-15
 *      become live);
 *   2. a VEX.128 instruction leaves bits 255:128 of the destination untouched;
 *   3. bits 511:256 (ZMM_Hi256) are never zeroed (MAXVL).
 * The YMM component pointer is what get_xsave_addr() returned in v3: NULL when the YMM bit is clear, and only
 * looked up for VEX.256. test_gb_vnni_core.c must FAIL exactly the sse_init, vex128_upper and maxvl categories
 * with this merge, which proves it can see the defects.
 */
#ifndef _GB_K14_MERGE_REF_H
#define _GB_K14_MERGE_REF_H

static inline unsigned int gb_vnni_merge_k14(const struct gb_vnni_insn *in, const u32 *mem, u32 *xmm,
					     u64 *xfeatures, u8 *ymm_component, u8 *zhi_unused)
{
	u32 d[8], a[8], c[8];
	int ndw = in->l ? 8 : 4;
	u8 *ymm = NULL;

	(void)zhi_unused;
	memset(c, 0, sizeof(c));
	if (in->src2 < 0)
		memcpy(c, mem, ndw * 4);
	if (in->l && (*xfeatures & GB_VNNI_XF_YMM))
		ymm = ymm_component;
	memcpy(&d[0], &xmm[in->dst * 4], 16);
	memcpy(&a[0], &xmm[in->src1 * 4], 16);
	if (in->src2 >= 0)
		memcpy(&c[0], &xmm[in->src2 * 4], 16);
	if (in->l) {
		if (ymm) {
			memcpy(&d[4], ymm + in->dst * 16, 16);
			memcpy(&a[4], ymm + in->src1 * 16, 16);
			if (in->src2 >= 0)
				memcpy(&c[4], ymm + in->src2 * 16, 16);
		} else {
			memset(&d[4], 0, 16);
			memset(&a[4], 0, 16);
			if (in->src2 >= 0)
				memset(&c[4], 0, 16);
		}
	}
	gb_vnni_lanes(in->op, d, a, c, ndw);
	memcpy(&xmm[in->dst * 4], &d[0], 16);
	if (in->l && ymm)
		memcpy(ymm + in->dst * 16, &d[4], 16);
	*xfeatures |= GB_VNNI_XF_SSE;
	if (in->l && ymm)
		*xfeatures |= GB_VNNI_XF_YMM;
	return 0;
}

#endif
