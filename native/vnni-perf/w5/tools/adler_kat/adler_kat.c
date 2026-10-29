/* adler_kat: known-answer test of the device's libz adler32 (W5 VNNI measurement, our own code).
 *
 * usage: adler_kat [--bench MiB] [--dex FILE]... [--zlib FILE EXPECT_LEN]... LIB.so...
 * For every LIB (loaded by path with dlopen, so /system and /vendor copies are separate instances):
 *   1. adler32 / adler32_z over a fixed pseudo-random buffer (LCG) and an all-0xff buffer at many lengths and
 *      misalignments (0..5552*3+17 bytes, 1 MiB), each compared with a plain scalar reference implementation;
 *      incremental (chunked) updates compared with the one-shot value;
 *   2. compress2 -> uncompress round trip (1 MiB, the zlib trailer carries the adler32); the same stream with one
 *      flipped trailer byte must fail with Z_DATA_ERROR (-3);
 *   3. cross-library: a stream compressed by LIB[0] is uncompressed by every LIB;
 *   4. --dex FILE: the dex header checksum (bytes 8..11) == adler32(1, data + 12, size - 12);
 *   5. --zlib FILE N: a zlib stream made on the host (Python zlib.compress) uncompresses to N bytes (adler checked);
 *   6. --bench M: adler32 throughput over M MiB (wall time, CLOCK_MONOTONIC).
 * Prints one "ADLER_KAT ..." line per check and "ADLER_KAT RESULT PASS|FAIL cases=N fail=F". Exit 0 only on PASS. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef unsigned long (*adler_fn)(unsigned long, const unsigned char *, unsigned int);
typedef unsigned long (*adlerz_fn)(unsigned long, const unsigned char *, size_t);
typedef int (*compress2_fn)(unsigned char *, unsigned long *, const unsigned char *, unsigned long, int);
typedef int (*uncompress_fn)(unsigned char *, unsigned long *, const unsigned char *, unsigned long);
typedef unsigned long (*bound_fn)(unsigned long);
typedef const char *(*version_fn)(void);

struct lib {
    const char *path;
    void *h;
    adler_fn adler32;
    adlerz_fn adler32_z;
    compress2_fn compress2;
    uncompress_fn uncompress;
    bound_fn compressBound;
    version_fn zlibVersion;
};

static int cases, fails;

static void check(int ok, const char *what, const char *lib, unsigned long got, unsigned long want, size_t n, size_t off) {
    cases++;
    if (!ok) {
        fails++;
        printf("ADLER_KAT FAIL %s lib=%s len=%zu off=%zu got=%08lx want=%08lx\n", what, lib, n, off, got, want);
    }
}

static uint32_t ref_adler(uint32_t a, const uint8_t *p, size_t n) {
    uint32_t s1 = a & 0xffff, s2 = a >> 16;
    for (size_t i = 0; i < n; i++) {
        s1 = (s1 + p[i]) % 65521u;
        s2 = (s2 + s1) % 65521u;
    }
    return (s2 << 16) | s1;
}

static void fill_lcg(uint8_t *p, size_t n, uint64_t seed) {
    uint64_t x = seed;
    for (size_t i = 0; i < n; i++) {
        x = x * 6364136223846793005ull + 1442695040888963407ull;
        p[i] = (uint8_t)(x >> 56);
    }
}

static uint8_t *read_file(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc(sz > 0 ? (size_t)sz : 1);
    if (b && fread(b, 1, (size_t)sz, f) != (size_t)sz) { free(b); b = NULL; }
    fclose(f);
    *n = (size_t)sz;
    return b;
}

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

static int load(struct lib *l) {
    l->h = dlopen(l->path, RTLD_NOW | RTLD_LOCAL);
    if (!l->h) { printf("ADLER_KAT FAIL dlopen %s: %s\n", l->path, dlerror()); return 0; }
    l->adler32 = (adler_fn)dlsym(l->h, "adler32");
    l->adler32_z = (adlerz_fn)dlsym(l->h, "adler32_z");
    l->compress2 = (compress2_fn)dlsym(l->h, "compress2");
    l->uncompress = (uncompress_fn)dlsym(l->h, "uncompress");
    l->compressBound = (bound_fn)dlsym(l->h, "compressBound");
    l->zlibVersion = (version_fn)dlsym(l->h, "zlibVersion");
    if (!l->adler32 || !l->compress2 || !l->uncompress || !l->compressBound) {
        printf("ADLER_KAT FAIL symbols missing in %s\n", l->path);
        return 0;
    }
    Dl_info di;
    const char *from = "?";
    if (dladdr((void *)l->adler32, &di) && di.dli_fname) from = di.dli_fname;
    printf("ADLER_KAT lib=%s loaded adler32=%p from=%s zlib=%s adler32_z=%s\n", l->path, (void *)l->adler32, from,
           l->zlibVersion ? l->zlibVersion() : "?", l->adler32_z ? "yes" : "no");
    cases++;
    if (strcmp(from, l->path) != 0) {
        fails++;
        printf("ADLER_KAT FAIL adler32 of %s resolved into %s\n", l->path, from);
    }
    return 1;
}

int main(int argc, char **argv) {
    struct lib libs[8];
    int nl = 0;
    const char *dex[8]; int nd = 0;
    const char *zf[8]; size_t zn[8]; int nz = 0;
    int bench = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--bench") && i + 1 < argc) bench = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--dex") && i + 1 < argc && nd < 8) dex[nd++] = argv[++i];
        else if (!strcmp(argv[i], "--zlib") && i + 2 < argc && nz < 8) { zf[nz] = argv[++i]; zn[nz++] = strtoul(argv[++i], 0, 10); }
        else if (nl < 8) { memset(&libs[nl], 0, sizeof libs[nl]); libs[nl++].path = argv[i]; }
    }
    if (!nl) { fprintf(stderr, "usage: adler_kat [--bench MiB] [--dex F] [--zlib F N] LIB.so...\n"); return 2; }

    const size_t MAXN = (1u << 20) + 64;
    uint8_t *rnd = malloc(MAXN), *ff = malloc(MAXN);
    fill_lcg(rnd, MAXN, 0x5eed5eed12345678ull);
    memset(ff, 0xff, MAXN);
    static const size_t lens[] = {0, 1, 2, 15, 16, 17, 31, 32, 33, 63, 64, 65, 95, 96, 127, 128, 129, 255, 256, 1000, 4095,
                                  4096, 5551, 5552, 5553, 11104, 16673, 65536, 100000, 1u << 20};
    static const size_t offs[] = {0, 1, 3, 7, 16, 33};

    for (int li = 0; li < nl; li++) {
        struct lib *l = &libs[li];
        if (!load(l)) { cases++; fails++; continue; }
        int c0 = cases, f0 = fails;
        for (int bi = 0; bi < 2; bi++) {
            const uint8_t *buf = bi ? ff : rnd;
            for (size_t k = 0; k < sizeof lens / sizeof *lens; k++)
                for (size_t o = 0; o < sizeof offs / sizeof *offs; o++) {
                    size_t n = lens[k], off = offs[o];
                    if (off + n > MAXN) continue;
                    uint32_t want = ref_adler(1, buf + off, n);
                    unsigned long got = l->adler32(1, buf + off, (unsigned int)n);
                    check(got == want, bi ? "adler32(ff)" : "adler32(rnd)", l->path, got, want, n, off);
                    if (l->adler32_z) {
                        got = l->adler32_z(1, buf + off, n);
                        check(got == want, "adler32_z", l->path, got, want, n, off);
                    }
                    /* incremental: chunks of 1, 63, 64, 1000 */
                    static const size_t chunks[] = {63, 64, 1000};
                    for (size_t c = 0; c < 3 && n <= 100000; c++) {
                        unsigned long a = 1;
                        for (size_t p = 0; p < n; p += chunks[c]) {
                            size_t m = n - p < chunks[c] ? n - p : chunks[c];
                            a = l->adler32(a, buf + off + p, (unsigned int)m);
                        }
                        check(a == want, "adler32 incremental", l->path, a, want, n, off);
                    }
                }
        }
        /* NULL buffer returns the initial value 1 */
        unsigned long z = l->adler32(0, NULL, 0);
        check(z == 1, "adler32(NULL)", l->path, z, 1, 0, 0);

        /* compress2 -> uncompress round trip, corrupted trailer */
        unsigned long cb = l->compressBound(1u << 20), cl = cb, ul = 1u << 20;
        uint8_t *cbuf = malloc(cb), *ubuf = malloc((1u << 20) + 16);
        int rc = l->compress2(cbuf, &cl, rnd, 1u << 20, 6);
        check(rc == 0, "compress2", l->path, (unsigned long)rc, 0, 1u << 20, 0);
        uint32_t trailer = (uint32_t)cbuf[cl - 4] << 24 | (uint32_t)cbuf[cl - 3] << 16 | (uint32_t)cbuf[cl - 2] << 8 | cbuf[cl - 1];
        uint32_t want = ref_adler(1, rnd, 1u << 20);
        check(trailer == want, "zlib trailer == adler32", l->path, trailer, want, 1u << 20, 0);
        rc = l->uncompress(ubuf, &ul, cbuf, cl);
        check(rc == 0 && ul == (1u << 20) && !memcmp(ubuf, rnd, 1u << 20), "uncompress round trip", l->path,
              (unsigned long)rc, 0, ul, 0);
        cbuf[cl - 1] ^= 0x01;
        ul = 1u << 20;
        rc = l->uncompress(ubuf, &ul, cbuf, cl);
        check(rc == -3, "uncompress refuses a bad adler32 trailer (Z_DATA_ERROR)", l->path, (unsigned long)(long)rc,
              (unsigned long)-3L, 1u << 20, 0);
        cbuf[cl - 1] ^= 0x01;
        /* cross-library */
        for (int lj = 0; lj < nl; lj++) {
            if (!libs[lj].uncompress) continue;
            ul = 1u << 20;
            rc = libs[lj].uncompress(ubuf, &ul, cbuf, cl);
            check(rc == 0 && ul == (1u << 20) && !memcmp(ubuf, rnd, 1u << 20), "cross-library uncompress", libs[lj].path,
                  (unsigned long)rc, 0, ul, 0);
        }
        free(cbuf); free(ubuf);

        for (int d = 0; d < nd; d++) {
            size_t n; uint8_t *b = read_file(dex[d], &n);
            if (!b || n < 112 || memcmp(b, "dex\n", 4)) { cases++; fails++; printf("ADLER_KAT FAIL dex read %s\n", dex[d]); free(b); continue; }
            uint32_t hdr = b[8] | b[9] << 8 | b[10] << 16 | (uint32_t)b[11] << 24;
            unsigned long got = l->adler32(1, b + 12, (unsigned int)(n - 12));
            check(got == hdr, "dex header checksum", l->path, got, hdr, n, 0);
            printf("ADLER_KAT lib=%s dex=%s size=%zu header=%08x adler32=%08lx %s\n", l->path, dex[d], n, hdr, got,
                   got == hdr ? "MATCH" : "MISMATCH");
            free(b);
        }
        for (int q = 0; q < nz; q++) {
            size_t n; uint8_t *b = read_file(zf[q], &n);
            unsigned long ol = zn[q] + 16;
            uint8_t *o = malloc(ol);
            rc = b ? l->uncompress(o, &ol, b, n) : -99;
            check(rc == 0 && ol == zn[q], "host zlib stream", l->path, (unsigned long)(long)rc, 0, ol, 0);
            printf("ADLER_KAT lib=%s zlib=%s in=%zu rc=%d out=%lu (want %zu)\n", l->path, zf[q], n, rc, ol, zn[q]);
            free(b); free(o);
        }
        printf("ADLER_KAT lib=%s cases=%d fail=%d\n", l->path, cases - c0, fails - f0);

        if (bench > 0) {
            size_t total = (size_t)bench << 20, done = 0;
            unsigned long a = 1;
            double t0 = now();
            while (done < total) { a = l->adler32(a, rnd, 1u << 20); done += 1u << 20; }
            double dt = now() - t0;
            printf("ADLER_KAT bench lib=%s MiB=%d seconds=%.4f MiB_per_s=%.1f result=%08lx\n", l->path, bench, dt,
                   bench / dt, a);
        }
    }
    printf("ADLER_KAT RESULT %s cases=%d fail=%d\n", fails ? "FAIL" : "PASS", cases, fails);
    return fails ? 1 : 0;
}
