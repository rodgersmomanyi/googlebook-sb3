// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 The Googlebook OS build (GB-build) authors.
//
// vnni_probe -- equivalence + "zero VEX AVX-VNNI" probe for a lapis libz.so.
//
// Built for bionic x86-64 with the NDK and run inside a read-only chroot of the
// lapis system (see tests/run_equiv.sh).  It dlopen()s ONE libz.so given on the
// command line and exercises its public functions on many deterministic random
// inputs, comparing every result with an in-process scalar reference that does
// not use libz:
//
//   threads    : 8 threads hammer adler32/adler32_z concurrently as the VERY
//                FIRST libz calls of the process (races the lazy CPU-feature
//                init) -- thread-safety of the dispatch.
//   adler32    : adler32(1, buf, len), aligned, len 0..64 KiB (19 fixed lengths
//                spanning <64 / >=64 / NMAX boundaries + random).
//   crc32      : control.  Not touched by the patch (different feature flag), so
//                it must stay correct and never fault, patched or not.
//   inflate_roundtrip : compress2(level 6) + uncompress (zlib format; deflate and
//                inflate both run adler32 internally).
//   adler_chain: random starting adler (both halves < 65521), the buffer fed in
//                random-sized pieces (mix of <64 and >=64) -- the streaming
//                pattern of deflate/inflate/dex2oat; compared with the reference
//                run over the whole buffer from the same start.
//   adler_unaligned : buffer start misaligned by 1..63 bytes.
//   adler32_z_large : adler32_z (size_t length), 64 KiB..1 MiB+odd.
//   adler_combine : adler32_combine(adler(A), adler(B), |B|) == adler(A||B).
//   stream     : deflateInit2_/deflate + inflateInit2_/inflate with small random
//                in/out chunks, levels {0,1,6,9} x windowBits {15 zlib, 31 gzip,
//                -15 raw}; checks bytes, Z_STREAM_END, and strm.adler against
//                the reference adler32 (zlib) or crc32 (gzip).
//
// The builder is itself an i7-1065G7 (Ice Lake): it has AVX512-VNNI but NOT the
// VEX-encoded AVX-VNNI that lapis is compiled for, and it runs a stock kernel
// with no VNNI #UD emulator.  So any VEX AVX-VNNI instruction that actually
// executes raises SIGILL.  A per-thread SIGILL handler records the faulting RIP
// (as an offset into the dlopen'd libz) and longjmps out, so the tally is exact
// per operation.  Therefore, on this host:
//     SIGILL count on a path  ==  number of operations that executed VEX AVX-VNNI.
// A patched copy that runs every op to completion with zero SIGILL has provably
// executed zero VEX AVX-VNNI on those paths; the unpatched copy faults inside
// adler32_simd_ and names the offset (which also proves the inputs reach the
// SIMD path).
//
// Output: one "SUMMARY ..." line per op plus "FAULT <op> off=0x.." lines for the
// distinct fault offsets, and a final "RESULT ok|FAIL ...".

#include <dlfcn.h>
#include <pthread.h>
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ucontext.h>

typedef unsigned long uLong;
typedef unsigned int uInt;
typedef unsigned char Bytef;
typedef uLong (*adler_fn)(uLong, const Bytef *, uInt);
typedef uLong (*adlerz_fn)(uLong, const Bytef *, size_t);
typedef uLong (*combine_fn)(uLong, uLong, long);
typedef uLong (*crc_fn)(uLong, const Bytef *, uInt);
typedef int (*compress2_fn)(Bytef *, uLong *, const Bytef *, uLong, int);
typedef int (*uncompress_fn)(Bytef *, uLong *, const Bytef *, uLong);
typedef uLong (*bound_fn)(uLong);
typedef const char *(*ver_fn)(void);

// zlib's z_stream (stable ABI)
typedef struct {
    const Bytef *next_in;
    uInt avail_in;
    uLong total_in;
    Bytef *next_out;
    uInt avail_out;
    uLong total_out;
    const char *msg;
    void *state;
    void *zalloc;
    void *zfree;
    void *opaque;
    int data_type;
    uLong adler;
    uLong reserved;
} zstream;
typedef int (*dinit2_fn)(zstream *, int, int, int, int, int, const char *, int);
typedef int (*iinit2_fn)(zstream *, int, const char *, int);
typedef int (*dfl_fn)(zstream *, int);
typedef int (*end_fn)(zstream *);

// ---- in-process scalar reference implementations (independent of libz) ----
#define ADLER_BASE 65521u
static uint32_t ref_adler32_from(uint32_t start, const uint8_t *p, size_t n) {
    uint32_t a = start & 0xffff, b = start >> 16;
    for (size_t i = 0; i < n; i++) {
        a += p[i];
        if (a >= ADLER_BASE) a -= ADLER_BASE;
        b += a;
        if (b >= ADLER_BASE) b -= ADLER_BASE;
    }
    return (b << 16) | a;
}
static uint32_t ref_adler32(const uint8_t *p, size_t n) { return ref_adler32_from(1, p, n); }
static uint32_t crc_table[256];
static void crc_init(void) {
    for (uint32_t n = 0; n < 256; n++) {
        uint32_t c = n;
        for (int k = 0; k < 8; k++) c = (c & 1) ? 0xedb88320u ^ (c >> 1) : c >> 1;
        crc_table[n] = c;
    }
}
static uint32_t ref_crc32(const uint8_t *p, size_t n) {
    uint32_t c = 0xffffffffu;
    for (size_t i = 0; i < n; i++) c = crc_table[(c ^ p[i]) & 0xff] ^ (c >> 8);
    return c ^ 0xffffffffu;
}

// ---- deterministic PRNG (splitmix64), per caller state ----
static uint64_t sm_next(uint64_t *st) {
    uint64_t z = (*st += 0x9e3779b97f4a7c15ULL);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}
static void fill_random(uint64_t *st, uint8_t *buf, size_t n) {
    size_t i = 0;
    for (; i + 8 <= n; i += 8) {
        uint64_t r = sm_next(st);
        memcpy(buf + i, &r, 8);
    }
    if (i < n) {
        uint64_t r = sm_next(st);
        memcpy(buf + i, &r, n - i);
    }
}
// compressible-ish data for the stream tests (random runs + literals)
static void fill_mixed(uint64_t *st, uint8_t *buf, size_t n) {
    size_t i = 0;
    while (i < n) {
        uint64_t r = sm_next(st);
        size_t run = 1 + (r & 63);
        if (run > n - i) run = n - i;
        if (r & 0x100) memset(buf + i, (int)(r >> 16), run);
        else fill_random(st, buf + i, run);
        i += run;
    }
}

// ---- SIGILL capture (per thread) ----
static __thread sigjmp_buf t_jb;
static __thread volatile uintptr_t t_fault_rip;
static void sigill_handler(int sig, siginfo_t *si, void *uc) {
    (void)sig;
    (void)si;
    ucontext_t *c = (ucontext_t *)uc;
    t_fault_rip = (uintptr_t)c->uc_mcontext.gregs[REG_RIP];
    siglongjmp(t_jb, 1);
}

static uintptr_t g_lib_base;  // load base of the dlopen'd libz

enum { OP_THREADS, OP_ADLER, OP_CRC, OP_INFLATE, OP_CHAIN, OP_UNALIGNED, OP_ADLERZ,
       OP_COMBINE, OP_STREAM, N_OP };
static const char *op_name[N_OP] = {"threads", "adler32", "crc32", "inflate_roundtrip",
                                    "adler_chain", "adler_unaligned", "adler32_z_large",
                                    "adler_combine", "stream"};
static long ok[N_OP], mismatch[N_OP], sigill[N_OP], skipped[N_OP];
static uintptr_t faults[N_OP][64];
static int n_faults[N_OP];
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;

static void note_fault(int op) {
    uintptr_t off = t_fault_rip - g_lib_base;
    pthread_mutex_lock(&g_mu);
    sigill[op]++;
    int seen = 0;
    for (int i = 0; i < n_faults[op]; i++)
        if (faults[op][i] == off) seen = 1;
    if (!seen && n_faults[op] < 64) faults[op][n_faults[op]++] = off;
    pthread_mutex_unlock(&g_mu);
}
static void note(int op, int good) {
    pthread_mutex_lock(&g_mu);
    if (good) ok[op]++;
    else mismatch[op]++;
    pthread_mutex_unlock(&g_mu);
}

// GUARD(op, body...): run body; a SIGILL inside it is tallied for op.
#define GUARD(op, ...)                        \
    do {                                      \
        if (sigsetjmp(t_jb, 1) == 0) {        \
            __VA_ARGS__;                      \
        } else {                              \
            note_fault(op);                   \
        }                                     \
    } while (0)

static adler_fn p_adler;
static adlerz_fn p_adlerz;
static combine_fn p_combine;
static crc_fn p_crc;
static compress2_fn p_c2;
static uncompress_fn p_unc;
static bound_fn p_bound;
static dinit2_fn p_dinit2;
static iinit2_fn p_iinit2;
static dfl_fn p_deflate, p_inflate;
static end_fn p_dend, p_iend;
static const char *g_ver = "1.3.2.1-motley";

// ---- threads: concurrent first calls ----
#define N_THREADS 8
static pthread_barrier_t g_bar;
static void *thread_main(void *arg) {
    uint64_t st = 0xabc0000ULL + (uintptr_t)arg;
    uint8_t *buf = (uint8_t *)malloc(70000);
    pthread_barrier_wait(&g_bar);  // all threads enter libz at the same moment
    // zlib convention adler32(0, NULL, 0) runs the lazy CPU-feature init
    // (pthread_once inside libz); 8 threads race it here.  Without it the SSSE3
    // flag stays 0 and even the unpatched libz would take the scalar path.
    GUARD(OP_THREADS, (void)p_adler(0, NULL, 0));
    for (int it = 0; it < 400; it++) {
        unsigned len = (unsigned)(sm_next(&st) % 70000);
        fill_random(&st, buf, len);
        uint32_t ref = ref_adler32(buf, len);
        GUARD(OP_THREADS, {
            uLong got = (it & 1) && p_adlerz ? p_adlerz(1, buf, len) : p_adler(1, buf, len);
            note(OP_THREADS, (uint32_t)got == ref);
        });
    }
    free(buf);
    return NULL;
}

static int stream_one(uint64_t *st, int level, int wbits, uint8_t *src, size_t n, uint8_t *cbuf,
                      size_t ccap, uint8_t *dbuf) {
    // returns 1 if good, 0 if mismatch (SIGILL longjmps out of here)
    zstream d;
    memset(&d, 0, sizeof(d));
    if (p_dinit2(&d, level, 8, wbits, 8, 0, g_ver, (int)sizeof(zstream)) != 0) return 0;
    size_t in_off = 0, out_off = 0;
    int rc, stall = 0;
    do {
        uInt in_chunk = (uInt)(1 + sm_next(st) % 9000);
        uInt out_chunk = (uInt)(1 + sm_next(st) % 9000);
        if (in_chunk > n - in_off) in_chunk = (uInt)(n - in_off);
        if (out_chunk > ccap - out_off) out_chunk = (uInt)(ccap - out_off);
        d.next_in = src + in_off;
        d.avail_in = in_chunk;
        d.next_out = cbuf + out_off;
        d.avail_out = out_chunk;
        int flush = (in_off + in_chunk == n) ? 4 /*Z_FINISH*/ : 0;
        rc = p_deflate(&d, flush);
        size_t used = in_chunk - d.avail_in, made = out_chunk - d.avail_out;
        in_off += used;
        out_off += made;
        if (rc != 0 && rc != 1 && rc != -5) break;
        stall = (used || made) ? 0 : stall + 1;
        if (stall > 3) break;
    } while (rc != 1 && out_off < ccap);
    uLong d_adler = d.adler;
    p_dend(&d);
    if (rc != 1) return 0;
    size_t clen = out_off;
    zstream z;
    memset(&z, 0, sizeof(z));
    if (p_iinit2(&z, wbits, g_ver, (int)sizeof(zstream)) != 0) return 0;
    in_off = out_off = 0;
    stall = 0;
    do {
        uInt in_chunk = (uInt)(1 + sm_next(st) % 9000);
        uInt out_chunk = (uInt)(1 + sm_next(st) % 9000);
        if (in_chunk > clen - in_off) in_chunk = (uInt)(clen - in_off);
        if (out_chunk > n + 1 - out_off) out_chunk = (uInt)(n + 1 - out_off);
        z.next_in = cbuf + in_off;
        z.avail_in = in_chunk;
        z.next_out = dbuf + out_off;
        z.avail_out = out_chunk;
        rc = p_inflate(&z, 0);
        size_t used = in_chunk - z.avail_in, made = out_chunk - z.avail_out;
        in_off += used;
        out_off += made;
        if (rc != 0 && rc != 1 && rc != -5) break;
        if (out_off > n) break;  // more output than was compressed
        stall = (used || made) ? 0 : stall + 1;
        if (stall > 3) break;
    } while (rc != 1);
    uLong i_adler = z.adler;
    p_iend(&z);
    if (rc != 1 || out_off != n || memcmp(dbuf, src, n) != 0) return 0;
    if (wbits == 15) {  // zlib wrapper: strm.adler is adler32 of the data
        uint32_t ref = ref_adler32(src, n);
        if ((uint32_t)d_adler != ref || (uint32_t)i_adler != ref) return 0;
    } else if (wbits == 31) {  // gzip wrapper: strm.adler is crc32 of the data
        uint32_t ref = ref_crc32(src, n);
        if ((uint32_t)d_adler != ref || (uint32_t)i_adler != ref) return 0;
    }
    return 1;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <path/to/libz.so> [seed] [n_random]\n", argv[0]);
        return 2;
    }
    const char *path = argv[1];
    uint64_t seed = (argc > 2) ? strtoull(argv[2], NULL, 0) : 0x1234abcdULL;
    int n_random = (argc > 3) ? atoi(argv[3]) : 2000;

    crc_init();

    void *h = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!h) {
        fprintf(stderr, "dlopen(%s) failed: %s\n", path, dlerror());
        return 2;
    }
    p_adler = (adler_fn)dlsym(h, "adler32");
    p_adlerz = (adlerz_fn)dlsym(h, "adler32_z");
    p_combine = (combine_fn)dlsym(h, "adler32_combine");
    p_crc = (crc_fn)dlsym(h, "crc32");
    p_c2 = (compress2_fn)dlsym(h, "compress2");
    p_unc = (uncompress_fn)dlsym(h, "uncompress");
    p_bound = (bound_fn)dlsym(h, "compressBound");
    p_dinit2 = (dinit2_fn)dlsym(h, "deflateInit2_");
    p_iinit2 = (iinit2_fn)dlsym(h, "inflateInit2_");
    p_deflate = (dfl_fn)dlsym(h, "deflate");
    p_inflate = (dfl_fn)dlsym(h, "inflate");
    p_dend = (end_fn)dlsym(h, "deflateEnd");
    p_iend = (end_fn)dlsym(h, "inflateEnd");
    ver_fn p_ver = (ver_fn)dlsym(h, "zlibVersion");
    if (!p_adler || !p_crc || !p_c2 || !p_unc || !p_bound || !p_dinit2 || !p_iinit2 ||
        !p_deflate || !p_inflate || !p_dend || !p_iend || !p_ver) {
        fprintf(stderr, "missing symbols\n");
        return 2;
    }
    Dl_info info;
    if (dladdr((void *)p_adler, &info)) g_lib_base = (uintptr_t)info.dli_fbase;

    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = sigill_handler;
    sa.sa_flags = SA_SIGINFO | SA_NODEFER;
    sigaction(SIGILL, &sa, NULL);

    // ---- threads FIRST: these are the process's first adler32 calls ----
    {
        pthread_t th[N_THREADS];
        pthread_barrier_init(&g_bar, NULL, N_THREADS);
        for (long i = 0; i < N_THREADS; i++) pthread_create(&th[i], NULL, thread_main, (void *)i);
        for (int i = 0; i < N_THREADS; i++) pthread_join(th[i], NULL);
        pthread_barrier_destroy(&g_bar);
    }
    g_ver = p_ver();
    printf("LIB %s base=%p zlibVersion=%s adler32_z=%s\n", path, (void *)g_lib_base, g_ver,
           p_adlerz ? "yes" : "no");

    uint64_t st = seed;
    const size_t maxlen = 65536, biglen = (1u << 20) + 77;
    uint8_t *buf = (uint8_t *)malloc(biglen + 128);
    uLong cbound = p_bound(biglen) + 1024;
    uint8_t *cbuf = (uint8_t *)malloc(cbound);
    uint8_t *dbuf = (uint8_t *)malloc(biglen + 128);

    static const unsigned fixed_lens[] = {0, 1, 2, 15, 16, 55, 63, 64, 65, 96, 127,
                                          128, 129, 255, 256, 1000, 4096, 65535, 65536};
    int n_fixed = (int)(sizeof(fixed_lens) / sizeof(fixed_lens[0]));
    int total = n_fixed + n_random;

    for (int t = 0; t < total; t++) {
        unsigned len = (t < n_fixed) ? fixed_lens[t] : (unsigned)(sm_next(&st) % (maxlen + 1));
        fill_random(&st, buf, len);
        {   // adler32
            uint32_t ref = ref_adler32(buf, len);
            GUARD(OP_ADLER, {
                uLong got = p_adler(p_adler(0, NULL, 0), buf, len);
                note(OP_ADLER, (uint32_t)got == ref);
                if ((uint32_t)got != ref)
                    printf("MISMATCH adler32 len=%u ref=0x%08x got=0x%08lx\n", len, ref, got);
            });
        }
        {   // crc32 (control)
            uint32_t ref = ref_crc32(buf, len);
            GUARD(OP_CRC, {
                uLong got = p_crc(p_crc(0, NULL, 0), buf, len);
                note(OP_CRC, (uint32_t)got == ref);
                if ((uint32_t)got != ref)
                    printf("MISMATCH crc32 len=%u ref=0x%08x got=0x%08lx\n", len, ref, got);
            });
        }
        {   // compress2 + uncompress
            GUARD(OP_INFLATE, {
                uLong clen = cbound;
                int rc = p_c2(cbuf, &clen, buf, len, 6);
                uLong dlen = maxlen;
                int rc2 = (rc == 0) ? p_unc(dbuf, &dlen, cbuf, clen) : -999;
                int good = rc == 0 && rc2 == 0 && dlen == len && memcmp(dbuf, buf, len) == 0;
                note(OP_INFLATE, good);
                if (!good) printf("MISMATCH inflate len=%u rc=%d rc2=%d\n", len, rc, rc2);
            });
        }
    }

    // adler_chain: random start, random split points
    for (int t = 0; t < 600; t++) {
        unsigned len = (unsigned)(sm_next(&st) % (maxlen + 1));
        fill_random(&st, buf, len);
        uint32_t a0 = (uint32_t)((sm_next(&st) % ADLER_BASE) << 16 | (sm_next(&st) % ADLER_BASE));
        uint32_t ref = ref_adler32_from(a0, buf, len);
        GUARD(OP_CHAIN, {
            uLong a = a0;
            unsigned off = 0;
            while (off < len) {
                unsigned piece = (unsigned)((sm_next(&st) & 1) ? sm_next(&st) % 64
                                                               : sm_next(&st) % 9000);
                if (piece > len - off) piece = len - off;
                a = p_adler(a, buf + off, piece);
                off += piece;
                if (piece == 0 && off < len) { a = p_adler(a, buf + off, 1); off++; }
            }
            note(OP_CHAIN, (uint32_t)a == ref);
            if ((uint32_t)a != ref) printf("MISMATCH adler_chain len=%u a0=0x%08x\n", len, a0);
        });
    }
    // adler_unaligned
    for (int t = 0; t < 600; t++) {
        unsigned mis = 1 + (unsigned)(sm_next(&st) % 63);
        unsigned len = (unsigned)(sm_next(&st) % (maxlen + 1));
        uint8_t *p = buf + mis;
        fill_random(&st, p, len);
        uint32_t ref = ref_adler32(p, len);
        GUARD(OP_UNALIGNED, {
            uLong got = p_adler(1, p, len);
            note(OP_UNALIGNED, (uint32_t)got == ref);
            if ((uint32_t)got != ref) printf("MISMATCH adler_unaligned mis=%u len=%u\n", mis, len);
        });
    }
    // adler32_z_large
    if (p_adlerz) {
        for (int t = 0; t < 24; t++) {
            size_t len = (t == 0) ? biglen : (size_t)(maxlen + sm_next(&st) % (biglen - maxlen));
            fill_random(&st, buf, len);
            uint32_t ref = ref_adler32(buf, len);
            GUARD(OP_ADLERZ, {
                uLong got = p_adlerz(1, buf, len);
                note(OP_ADLERZ, (uint32_t)got == ref);
                if ((uint32_t)got != ref) printf("MISMATCH adler32_z len=%zu\n", len);
            });
        }
    } else {
        skipped[OP_ADLERZ]++;
    }
    // adler_combine
    if (p_combine) {
        for (int t = 0; t < 300; t++) {
            unsigned la = (unsigned)(sm_next(&st) % 20000), lb = (unsigned)(sm_next(&st) % 20000);
            fill_random(&st, buf, la + lb);
            uint32_t ref = ref_adler32(buf, la + lb);
            GUARD(OP_COMBINE, {
                uLong A = p_adler(1, buf, la), B = p_adler(1, buf + la, lb);
                uLong got = p_combine(A, B, (long)lb);
                note(OP_COMBINE, (uint32_t)got == ref);
                if ((uint32_t)got != ref) printf("MISMATCH adler_combine la=%u lb=%u\n", la, lb);
            });
        }
    } else {
        skipped[OP_COMBINE]++;
    }
    // stream: levels x wrappers, small random chunks
    {
        static const int levels[] = {0, 1, 6, 9};
        static const int wbits[] = {15, 31, -15};
        for (int t = 0; t < 120; t++) {
            int level = levels[t % 4], wb = wbits[(t / 4) % 3];
            size_t len = (t % 10 == 0) ? (size_t)(sm_next(&st) % 64)
                                       : (size_t)(sm_next(&st) % (256 * 1024));
            fill_mixed(&st, buf, len);
            GUARD(OP_STREAM, {
                int good = stream_one(&st, level, wb, buf, len, cbuf, cbound, dbuf);
                note(OP_STREAM, good);
                if (!good) printf("MISMATCH stream level=%d wbits=%d len=%zu\n", level, wb, len);
            });
        }
    }

    int failed = 0;
    for (int o = 0; o < N_OP; o++) {
        printf("SUMMARY %-18s ok=%ld mismatch=%ld sigill=%ld%s\n", op_name[o], ok[o], mismatch[o],
               sigill[o], skipped[o] ? " (skipped: symbol absent)" : "");
        for (int i = 0; i < n_faults[o]; i++)
            printf("FAULT %-18s off=0x%lx\n", op_name[o], (unsigned long)faults[o][i]);
        if (mismatch[o]) failed = 1;
    }
    printf("RESULT %s seed=0x%llx\n", failed ? "FAIL" : "ok", (unsigned long long)seed);
    free(buf);
    free(cbuf);
    free(dbuf);
    dlclose(h);
    return failed ? 1 : 0;
}
