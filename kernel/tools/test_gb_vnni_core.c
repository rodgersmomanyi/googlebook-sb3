// SPDX-License-Identifier: GPL-2.0
/*
 * KAT-0: host unit test of the GB_VNNI_EMULATION core (arch/x86/kernel/gb_vnni_core.h, kernel build #15).
 *
 * Build (any x86-64 or other host, plain C99; gb_vnni_core.h from `apply_vnni4.py --emit-core`):
 *   cc -O2 -std=gnu99 -Wall -Wextra -Werror -I<dir of gb_vnni_core.h> -o kat0 test_gb_vnni_core.c
 *   cc ... -DGB_K14_MERGE -o kat0_k14 test_gb_vnni_core.c      (negative control: the k14 merge)
 * Run: ./kat0 <decode-vectors-file>   (vectors made by gen_decode_vectors.py from GNU as output)
 *
 * Categories: decode (assembler vectors: every field and the length), ea, short/not (truncated fetches and
 * foreign encodings), lanes (saturation edges vs an independent scalar reference), and the xstate merge, run on
 * every header combination {SSE, YMM, ZMM_Hi256} x {set, clear} with stale non-zero bytes in the cleared
 * components, every opcode, VEX.128/256 and register/memory sources:
 *   basic        SSE+YMM set, VEX.256
 *   ymm_init     SSE set, YMM clear
 *   vex128_upper SSE+YMM set, ZMM_Hi256 clear, VEX.128 (dst bits 255:128 must become 0)
 *   maxvl        ZMM_Hi256 set (dst bits 511:256 must become 0)
 *   sse_init     SSE clear (XMM0-15 are 0 although the buffer holds stale bytes)
 *   bits         the merge never sets YMM/ZMM_Hi256, always leaves SSE set, never touches a cleared component
 * The check is architectural: the register file seen through the header bits after the merge must equal the
 * reference result applied to the register file seen before it (dst per the SDM, every other register intact).
 * Output: one line per category and "KAT0 RESULT PASS|FAIL failed=<list>". With -DGB_K14_MERGE the expected result
 * is FAIL with failed=sse_init,vex128_upper,maxvl exactly (K-14).
 */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int8_t s8;
typedef int16_t s16;
typedef int32_t s32;
typedef int64_t s64;
#define S32_MAX INT32_MAX
#define S32_MIN INT32_MIN

#include "gb_vnni_core.h"
#ifdef GB_K14_MERGE
#include "k14_merge_ref.h"
#define MERGE gb_vnni_merge_k14
#define VARIANT "k14 merge (negative control)"
#else
#define MERGE gb_vnni_merge
#define VARIANT "k15 core"
#endif

enum { C_DECODE, C_EA, C_SHORT, C_LANES, C_BASIC, C_YMM_INIT, C_VEX128, C_MAXVL, C_SSE_INIT, C_BITS, NCAT };
static const char *cat_name[NCAT] = { "decode", "ea", "short", "lanes", "basic", "ymm_init", "vex128_upper",
				      "maxvl", "sse_init", "bits" };
static unsigned long n_run[NCAT], n_fail[NCAT];
static char first_fail[NCAT][512];

static void check(int cat, bool ok, const char *what)
{
	n_run[cat]++;
	if (!ok) {
		if (!n_fail[cat])
			snprintf(first_fail[cat], sizeof(first_fail[cat]), "%s", what);
		n_fail[cat]++;
	}
}

static u64 rs = 0x9e3779b97f4a7c15ULL;
static u64 rnd(void)
{
	rs ^= rs << 13;
	rs ^= rs >> 7;
	rs ^= rs << 17;
	return rs;
}
static void rndfill(void *p, size_t n)
{
	u8 *b = p;
	size_t i;

	for (i = 0; i < n; i++)
		b[i] = (u8)(rnd() >> 24) | 1;	/* never 0: a stale byte must be visible */
}

/* ---------------------------------------------------------------- independent scalar reference (SDM pseudo-code) */
static s64 sx8(u8 v) { return v >= 128 ? (s64)v - 256 : (s64)v; }
static s64 sx16(u16 v) { return v >= 32768 ? (s64)v - 65536 : (s64)v; }
static u32 rd32(const u8 *p) { return (u32)p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24; }
static void wr32(u8 *p, u32 v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }

static u32 ref_dword(u8 op, u32 acc, const u8 *a, const u8 *c)
{
	s64 sum = (s32)acc;
	int k;

	if (op == 0x50 || op == 0x51) {		/* ZERO_EXTEND(src1.byte) * SIGN_EXTEND(src2.byte) */
		for (k = 0; k < 4; k++)
			sum += (s64)a[k] * sx8(c[k]);
	} else {				/* SIGN_EXTEND(src1.word) * SIGN_EXTEND(src2.word) */
		for (k = 0; k < 2; k++)
			sum += sx16((u16)(a[2 * k] | a[2 * k + 1] << 8)) * sx16((u16)(c[2 * k] | c[2 * k + 1] << 8));
	}
	if (op == 0x51 || op == 0x53) {
		if (sum > 2147483647LL)
			sum = 2147483647LL;
		if (sum < -2147483648LL)
			sum = -2147483648LL;
	}
	return (u32)((u64)sum & 0xffffffffULL);
}

/* architectural zmm0-15 (512 bits each) as seen through the XSAVE header bits */
struct img {
	u32 xmm[64];
	u64 xf;
	u8 ymm[256];
	u8 zhi[512];
};
static void to_arch(const struct img *m, u8 z[16][64])
{
	int r;

	for (r = 0; r < 16; r++) {
		memset(z[r], 0, 64);
		if (m->xf & GB_VNNI_XF_SSE)
			memcpy(z[r], &m->xmm[r * 4], 16);
		if (m->xf & GB_VNNI_XF_YMM)
			memcpy(z[r] + 16, m->ymm + r * 16, 16);
		if (m->xf & GB_VNNI_XF_ZMM_HI256)
			memcpy(z[r] + 32, m->zhi + r * 32, 32);
	}
}
static void ref_exec(u8 z[16][64], u8 op, int l, int dst, int src1, int src2, const u8 *mem)
{
	u8 res[32];
	int i, n = l ? 8 : 4;

	for (i = 0; i < n; i++) {
		const u8 *c = src2 >= 0 ? &z[src2][4 * i] : &mem[4 * i];

		wr32(&res[4 * i], ref_dword(op, rd32(&z[dst][4 * i]), &z[src1][4 * i], c));
	}
	memset(z[dst], 0, 64);			/* VEX: zero above the vector length up to MAXVL */
	memcpy(z[dst], res, n * 4);
}

/* ---------------------------------------------------------------- decode vectors (from the assembler) */
static int hexbytes(const char *s, u8 *out, int max)
{
	int n = 0;
	unsigned int v;

	while (*s && n < max && sscanf(s, "%2x", &v) == 1) {
		out[n++] = (u8)v;
		s += 2;
	}
	return n;
}

static void test_decode_file(const char *path)
{
	char line[512], what[300];
	FILE *f = fopen(path, "r");
	int lines = 0;

	if (!f) {
		check(C_DECODE, false, "cannot open the vectors file");
		return;
	}
	while (fgets(line, sizeof(line), f)) {
		char hex[64], kind[16];
		int op, l, dst, src1, src2, len, base, index, scale, rip, n, rc, k;
		long long disp;
		u8 ib[16];
		struct gb_vnni_insn in;

		if (line[0] == '#' || line[0] == '\n')
			continue;
		if (sscanf(line, "%15s %63s", kind, hex) != 2)
			continue;
		n = hexbytes(hex, ib, 15);
		lines++;
		if (!strcmp(kind, "NOT")) {			/* a foreign encoding: never emulated */
			rc = gb_vnni_decode(ib, n, &in);
			snprintf(what, sizeof(what), "NOT %s -> rc %d", hex, rc);
			check(C_SHORT, rc == GB_VNNI_NOT, what);
			continue;
		}
		if (sscanf(line, "%*s %*s %x %d %d %d %d %d %d %d %d %d %lld", &op, &l, &dst, &src1, &src2, &len, &base,
			   &index, &scale, &rip, &disp) != 11) {
			check(C_DECODE, false, "malformed vector line");
			continue;
		}
		rc = gb_vnni_decode(ib, n, &in);
		snprintf(what, sizeof(what), "%s: rc %d op %x/%x l %d/%d dst %d/%d src1 %d/%d src2 %d/%d len %d/%d "
			 "base %d/%d index %d/%d scale %d/%d rip %d/%d disp %d/%lld", hex, rc, in.op, op, in.l, l, in.dst,
			 dst, in.src1, src1, in.src2, src2, in.len, len, in.base, base, in.index, index, in.scale, scale,
			 in.rip, rip, in.disp, disp);
		check(C_DECODE, rc == GB_VNNI_OK && in.op == op && in.l == l && in.dst == dst && in.src1 == src1 &&
		      in.src2 == src2 && in.len == len && n == len &&
		      (src2 >= 0 || (in.base == base && in.index == index && in.scale == scale && in.rip == rip &&
				     in.disp == disp)), what);
		/* every truncated fetch of a valid instruction is SHORT, never OK or NOT */
		for (k = 0; k < len; k++) {
			struct gb_vnni_insn t;

			rc = gb_vnni_decode(ib, k, &t);
			snprintf(what, sizeof(what), "%s truncated to %d -> rc %d", hex, k, rc);
			check(C_SHORT, rc == GB_VNNI_SHORT, what);
		}
		/* EA: independent computation from the expected fields with random GPR values */
		if (src2 < 0) {
			u64 g[16], next = 0x7f0012345000ULL + len, want, got;

			for (k = 0; k < 16; k++)
				g[k] = rnd();
			want = (u64)disp + (rip ? next : 0) + (base >= 0 ? g[base] : 0) +
			       (index >= 0 ? g[index] * (1ULL << scale) : 0);
			got = gb_vnni_ea(&in, in.base >= 0 ? g[in.base] : 0, in.index >= 0 ? g[in.index] : 0, next);
			snprintf(what, sizeof(what), "%s ea %llx want %llx", hex, (unsigned long long)got,
				 (unsigned long long)want);
			check(C_EA, got == want, what);
		}
	}
	fclose(f);
	if (lines < 100)
		check(C_DECODE, false, "fewer than 100 vector lines");
}

/* hand-made foreign encodings and the SHORT edge cases */
static void test_not(void)
{
	static const struct { const char *hex; int want; } v[] = {
		{ "c4e2f950c1", GB_VNNI_NOT },	/* VEX.W1 vpdpbusd: not AVX-VNNI */
		{ "c4e2fd53c1", GB_VNNI_NOT },	/* VEX.W1 vpdpwssds ymm */
		{ "c4e27b50c1", GB_VNNI_NOT },	/* pp=F2: vpdpbssd (AVX-VNNI-INT8) */
		{ "c4e27a51c1", GB_VNNI_NOT },	/* pp=F3: vpdpbsuds */
		{ "c4e27850c1", GB_VNNI_NOT },	/* pp=none: vpdpbuud */
		{ "c4e27954c1", GB_VNNI_NOT },	/* 0F38 54: not 50-53 */
		{ "c4e2794fc1", GB_VNNI_NOT },	/* 0F38 4F */
		{ "c4e17950c1", GB_VNNI_NOT },	/* map 0F */
		{ "c4e37950c1", GB_VNNI_NOT },	/* map 0F3A */
		{ "c5f950c1", GB_VNNI_NOT },	/* 2-byte VEX (map 0F) */
		{ "62f27d0850c1", GB_VNNI_NOT },	/* EVEX vpdpbusd: native, never #UD */
		{ "0f0b", GB_VNNI_NOT },		/* ud2 */
		{ "67c4e27950c1", GB_VNNI_NOT },	/* address-size prefix before VEX: documented gap */
		{ "", GB_VNNI_SHORT },		/* nothing fetched */
	};
	unsigned int i;
	char what[300];

	for (i = 0; i < sizeof(v) / sizeof(v[0]); i++) {
		u8 ib[16];
		struct gb_vnni_insn in;
		int n = hexbytes(v[i].hex, ib, 15), rc = gb_vnni_decode(ib, n, &in);

		snprintf(what, sizeof(what), "%s -> rc %d want %d", v[i].hex, rc, v[i].want);
		check(C_SHORT, rc == v[i].want, what);
	}
	{	/* a 4-byte fetch of a foreign map is NOT, not SHORT (a disqualifying byte wins) */
		u8 ib[] = { 0xc4, 0xe3, 0x79, 0x50 };
		struct gb_vnni_insn in;

		check(C_SHORT, gb_vnni_decode(ib, 4, &in) == GB_VNNI_NOT, "c4e37950 (4 bytes) must be NOT");
	}
}

/* ---------------------------------------------------------------- lanes: saturation edges */
static void test_lanes(void)
{
	static const u32 accs[] = { 0, 1, 0x7fffffff, 0x80000000, 0x7fff0000, 0x80010000, 0xffffffff, 0x7ffffff0,
				    0x80000010, 0x12345678 };
	static const u32 pats[] = { 0, 0xffffffff, 0x7f7f7f7f, 0x80808080, 0x7fff7fff, 0x80008000, 0x01010101,
				    0xff7f80ff, 0x8000ffff, 0x00ff00ff };
	u8 op;
	unsigned int ia, ib, ic;
	char what[300];

	for (op = 0x50; op <= 0x53; op++)
		for (ia = 0; ia < sizeof(accs) / 4; ia++)
			for (ib = 0; ib < sizeof(pats) / 4; ib++)
				for (ic = 0; ic < sizeof(pats) / 4; ic++) {
					u32 d[8], a[8], c[8], want;
					u8 ab[4], cb[4];
					int i;

					for (i = 0; i < 8; i++) {
						d[i] = accs[ia];
						a[i] = pats[ib];
						c[i] = pats[ic];
					}
					wr32(ab, pats[ib]);
					wr32(cb, pats[ic]);
					want = ref_dword(op, accs[ia], ab, cb);
					gb_vnni_lanes(op, d, a, c, 8);
					snprintf(what, sizeof(what), "op %x acc %08x a %08x c %08x -> %08x want %08x", op,
						 accs[ia], pats[ib], pats[ic], d[0], want);
					for (i = 0; i < 8; i++)
						check(C_LANES, d[i] == want, what);
				}
	/* random */
	for (ia = 0; ia < 200000; ia++) {
		u32 d[8], a[8], c[8], d0[8];
		u8 ab[32], cb[32];
		int i;

		op = 0x50 + (rnd() & 3);
		for (i = 0; i < 8; i++) {
			d[i] = d0[i] = (u32)rnd();
			a[i] = (u32)rnd();
			c[i] = (u32)rnd();
			wr32(&ab[4 * i], a[i]);
			wr32(&cb[4 * i], c[i]);
		}
		gb_vnni_lanes(op, d, a, c, 8);
		for (i = 0; i < 8; i++) {
			u32 w = ref_dword(op, d0[i], &ab[4 * i], &cb[4 * i]);

			snprintf(what, sizeof(what), "random op %x lane %d -> %08x want %08x", op, i, d[i], w);
			check(C_LANES, d[i] == w, what);
		}
	}
}

/* ---------------------------------------------------------------- xstate merge on every header combination */
static int merge_cat(u64 xf, int l)
{
	if (!(xf & GB_VNNI_XF_SSE))
		return C_SSE_INIT;
	if (xf & GB_VNNI_XF_ZMM_HI256)
		return C_MAXVL;
	if ((xf & GB_VNNI_XF_YMM) && !l)
		return C_VEX128;
	if (!(xf & GB_VNNI_XF_YMM))
		return C_YMM_INIT;
	return C_BASIC;
}

static void test_merge(void)
{
	static const u64 combos[8] = { 0, 2, 4, 6, 64, 66, 68, 70 };	/* {SSE=2, YMM=4, ZMM_Hi256=64} */
	char what[300];
	int ci, l, form, it;
	u8 op;

	for (ci = 0; ci < 8; ci++)
		for (l = 0; l < 2; l++)
			for (op = 0x50; op <= 0x53; op++)
				for (form = 0; form < 2; form++)
					for (it = 0; it < 400; it++) {
						struct img m, m2;
						struct gb_vnni_insn in;
						u8 before[16][64], want[16][64], after[16][64];
						u32 mem[8];
						u64 xf = combos[ci] | 0x1;	/* x87 bit: irrelevant, kept */
						int cat = merge_cat(xf, l), r, ok;
						unsigned int fl;

						rndfill(&m, sizeof(m));	/* stale non-zero bytes everywhere */
						m.xf = xf;
						rndfill(mem, sizeof(mem));
						if (it % 4 == 1)	/* some all-zero sources (init-like inputs) */
							memset(mem, 0, sizeof(mem));
						memset(&in, 0, sizeof(in));
						in.op = op;
						in.l = l;
						in.dst = rnd() & 15;
						in.src1 = (it % 7 == 0) ? in.dst : (rnd() & 15);
						in.src2 = form ? -1 : (s8)((it % 5 == 0) ? in.dst : (rnd() & 15));
						in.len = 5;
						to_arch(&m, before);
						memcpy(want, before, sizeof(want));
						ref_exec(want, op, l, in.dst, in.src1, in.src2, (const u8 *)mem);
						m2 = m;
						fl = MERGE(&in, mem, m2.xmm, &m2.xf, (m2.xf & GB_VNNI_XF_YMM) ? m2.ymm : NULL,
							   (m2.xf & GB_VNNI_XF_ZMM_HI256) ? m2.zhi : NULL);
						to_arch(&m2, after);
						ok = !memcmp(after, want, sizeof(want));
						r = 0;
						if (!ok)
							for (r = 0; r < 16 && !memcmp(after[r], want[r], 64); r++)
								;
						snprintf(what, sizeof(what), "xf %llx L%d op %x %s dst %d src1 %d src2 %d: zmm%d differs",
							 (unsigned long long)xf, l, op, form ? "mem" : "reg", in.dst, in.src1,
							 in.src2, r);
						check(cat, ok, what);
						/* bits: SSE now set; YMM/ZMM_Hi256 never set; a cleared component untouched */
						ok = (m2.xf & GB_VNNI_XF_SSE) &&
						     (m2.xf & (GB_VNNI_XF_YMM | GB_VNNI_XF_ZMM_HI256)) ==
						     (xf & (GB_VNNI_XF_YMM | GB_VNNI_XF_ZMM_HI256)) &&
						     ((xf & GB_VNNI_XF_YMM) || !memcmp(m.ymm, m2.ymm, sizeof(m.ymm))) &&
						     ((xf & GB_VNNI_XF_ZMM_HI256) || !memcmp(m.zhi, m2.zhi, sizeof(m.zhi)));
						snprintf(what, sizeof(what), "xf %llx -> %llx L%d: header or cleared component",
							 (unsigned long long)xf, (unsigned long long)m2.xf, l);
						check(C_BITS, ok, what);
#ifndef GB_K14_MERGE
						/* flags (counters) */
						ok = !!(fl & GB_VNNI_F_SSE_INIT) == !(xf & GB_VNNI_XF_SSE) &&
						     !!(fl & GB_VNNI_F_YMM_INIT) == !(xf & GB_VNNI_XF_YMM) &&
						     !!(fl & GB_VNNI_F_ZMMHI) == !!(xf & GB_VNNI_XF_ZMM_HI256);
						check(C_BITS, ok, "result flags do not match the header bits");
#else
						(void)fl;
#endif
					}
}

int main(int argc, char **argv)
{
	int c, bad = 0;
	char failed[200] = "";

	if (argc != 2) {
		fprintf(stderr, "usage: %s <decode-vectors-file>\n", argv[0]);
		return 2;
	}
	test_decode_file(argv[1]);
	test_not();
	test_lanes();
	test_merge();
	printf("KAT0 variant: %s\n", VARIANT);
	for (c = 0; c < NCAT; c++) {
		if (n_fail[c]) {
			printf("KAT0 %-12s FAIL %lu/%lu first: %s\n", cat_name[c], n_fail[c], n_run[c], first_fail[c]);
			if (bad++)
				strcat(failed, ",");
			strcat(failed, cat_name[c]);
		} else {
			printf("KAT0 %-12s PASS %lu\n", cat_name[c], n_run[c]);
		}
		if (!n_run[c])
			printf("KAT0 %-12s EMPTY (no cases ran)\n", cat_name[c]), bad++;
	}
	printf("KAT0 RESULT %s failed=%s\n", bad ? "FAIL" : "PASS", bad ? failed : "none");
	return bad ? 1 : 0;
}
