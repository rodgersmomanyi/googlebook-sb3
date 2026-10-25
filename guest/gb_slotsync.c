/* gb_slotsync.c -- A/B slot follower for the Googlebook x86 systemd-boot ESP.
 *
 * Our ESP boots its own GKI kernel with an initrd made of [gzip'd gb cpio | vendor ramdisk | generic ramdisk |
 * bootconfig]. An OTA (update_engine) writes the inactive slot, and the android-desktop boot_control HAL then marks
 * that slot active ChromeOS-style in the GPT attributes of vbmeta_<x> (priority bits 48-51, tries 52-55,
 * successful 56). No firmware here reads those, so without this tool every update would boot the old slot and roll
 * itself back. gb_slotsync is the bootloader's slot choice: it picks the slot the way depthcharge does (highest
 * priority among slots that are successful or have tries left), rebuilds /initrd-<x>.img on the ESP from that slot's
 * init_boot/vendor_boot (+ androidboot.slot_suffix=_<x>), writes loader/entries/gb-<x>.conf (and, if the current
 * slot has one, gb-<x>-nvtest.conf, the "[NVIDIA test]" entry) and makes gb-<x> the systemd-boot default. The previous
 * slot's entries stay in the menu as a fallback.
 *
 * ESP files used (written by build_esp.py): /gb/inject.gz (gb cpio, gzip, padded to 4 bytes), /gb/bootconfig_extra
 * (bootconfig lines common to both slots), /loader/entries/gb-<current>.conf (template, required),
 * /loader/entries/gb-<current>-nvtest.conf (template, optional), /loader/loader.conf.
 *
 * Every ESP mount is done inside the ESP lock /dev/gb_esp.lock (INTERFACES 5.4: mkdir lock, owner line
 * "<pid> gb_slotsync <uptime_s>", atomic rename-based steal of a stale lock, release only our own lock). After 600 s
 * of waiting this cycle is skipped (the --watch loop retries 60 s later). The ESP is mounted with "flush".
 *
 * Usage: gb_slotsync [--watch SECONDS] [--dry-run]
 * Build (builder, NDK r27c): /build/ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang --target=x86_64-linux-android34
 *        -O2 -Wall -Wextra -Werror -o gb_slotsync gb_slotsync.c
 * Test build (host only, never shipped): gcc -DGB_SLOTSYNC_TEST ... (test/test_gb_slotsync.sh): paths, the slot suffix
 *        and the boot id come from GB_SLOTSYNC_* environment variables, mount/umount2/sync are no-ops, the lock's 1 s
 *        polls run on a fake clock (10 ms each), and --test-copy-entry / --test-lock exist.
 */
#include <errno.h>
#include <fcntl.h>
#include <libgen.h>
#include <limits.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#ifdef GB_SLOTSYNC_TEST
#include <time.h>
#define PROP_VALUE_MAX 92
#else
#include <sys/system_properties.h>
#endif

#define ESP_DEV "/dev/block/by-name/EFI-SYSTEM"
#define LOCK_WAIT_MAX 600                                   /* s, INTERFACES 2.3 (log writers) */
#define LOCK_MISS_POLLS 5                                   /* polls without an owner file = dead holder */

static const char *mnt = "/mnt/gb_slotsync";
static const char *lock_path = "/dev/gb_esp.lock";
static const char *proc_root = "/proc";
static char lock_own[96];                                   /* our owner line while we hold the lock */
static int lock_waited;                                     /* seconds waited for the last lock_esp() */
static int dry_run;

#ifdef GB_SLOTSYNC_TEST
static const char *test_env(const char *name, const char *dflt) {
  const char *v = getenv(name);
  return v && *v ? v : dflt;
}
static int __system_property_get(const char *name, char *value) {
  const char *v = strcmp(name, "ro.boot.slot_suffix") ? "" : test_env("GB_SLOTSYNC_SLOT_SUFFIX", "");
  snprintf(value, PROP_VALUE_MAX, "%s", v);
  return (int)strlen(value);
}
#endif

static void logmsg(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void logmsg(const char *fmt, ...) {
  char buf[512];
  va_list ap;
  va_start(ap, fmt); vsnprintf(buf, sizeof buf, fmt, ap); va_end(ap);
  printf("gb_slotsync: %s\n", buf);
  fflush(stdout);
#ifndef GB_SLOTSYNC_TEST
  FILE *k = fopen("/dev/kmsg", "w");
  if (k) { fprintf(k, "gb_slotsync: %s\n", buf); fclose(k); }
#endif
}

/* 1 s poll of the lock wait (fake clock in the test build: 10 ms of real time per second) */
static void poll_sleep(void) {
#ifdef GB_SLOTSYNC_TEST
  struct timespec ts = {0, 10 * 1000 * 1000};
  nanosleep(&ts, NULL);
#else
  sleep(1);
#endif
}

static int do_mount(void) {
#ifdef GB_SLOTSYNC_TEST
  printf("test: mount %s %s vfat noatime,nodev,nosuid,noexec,utf8,flush\n", ESP_DEV, mnt);
  return 0;
#else
  return mount(ESP_DEV, mnt, "vfat", MS_NOATIME | MS_NODEV | MS_NOSUID | MS_NOEXEC, "utf8,flush");
#endif
}

static void do_umount(void) {
#ifdef GB_SLOTSYNC_TEST
  printf("test: umount %s\n", mnt);
#else
  sync();
  umount2(mnt, 0);
#endif
}

/* Whole file into a malloc'd buffer (*out = NULL on failure; errno set). */
static int read_all(const char *path, unsigned char **out, size_t *len) {
  *out = NULL; *len = 0;
  FILE *f = fopen(path, "rb");
  if (!f) return -1;
  size_t cap = 1 << 20, n = 0;
  unsigned char *b = malloc(cap);
  if (!b) { fclose(f); errno = ENOMEM; return -1; }
  for (;;) {
    if (n == cap) {
      unsigned char *nb = cap <= SIZE_MAX / 2 ? realloc(b, cap * 2) : NULL;
      if (!nb) { free(b); fclose(f); errno = ENOMEM; return -1; }
      b = nb; cap *= 2;
    }
    size_t r = fread(b + n, 1, cap - n, f);
    if (!r) break;
    n += r;
  }
  int err = ferror(f);
  fclose(f);
  if (err) { free(b); errno = EIO; return -1; }
  *out = b; *len = n;
  return 0;
}

static int pread_full(int fd, void *buf, size_t n, off_t off) {
  size_t done = 0;
  while (done < n) {
    ssize_t r = pread(fd, (char *)buf + done, n - done, off + (off_t)done);
    if (r <= 0) return -1;
    done += (size_t)r;
  }
  return 0;
}

/* ---- GPT: attributes of vbmeta_a / vbmeta_b on the disk that holds them ---- */
static int vbmeta_attrs(uint64_t attr[2]) {
  char part[PATH_MAX], sys[PATH_MAX], link[PATH_MAX], disk[PATH_MAX];
  if (!realpath("/dev/block/by-name/vbmeta_a", part)) return -1;
  snprintf(sys, sizeof sys, "/sys/class/block/%s", basename(part));
  ssize_t n = readlink(sys, link, sizeof link - 1);
  if (n <= 0) return -1;
  link[n] = 0;
  char *parent = basename(dirname(link));                 /* .../nvme0n1/nvme0n1p15 -> nvme0n1 */
  snprintf(disk, sizeof disk, "/dev/block/%s", parent);
  unsigned lbs = 512;
  char q[PATH_MAX]; snprintf(q, sizeof q, "/sys/class/block/%s/queue/logical_block_size", parent);
  FILE *qf = fopen(q, "r");
  if (qf) { if (fscanf(qf, "%u", &lbs) != 1) lbs = 512; fclose(qf); }
  int fd = open(disk, O_RDONLY | O_CLOEXEC);
  if (fd < 0) return -1;
  unsigned char hdr[92];
  if (pread_full(fd, hdr, sizeof hdr, lbs) || memcmp(hdr, "EFI PART", 8)) { close(fd); return -1; }
  uint64_t ent_lba; uint32_t num, esz;
  memcpy(&ent_lba, hdr + 72, 8); memcpy(&num, hdr + 80, 4); memcpy(&esz, hdr + 84, 4);
  if (esz < 128 || esz > 4096 || num > 1024) { close(fd); return -1; }
  unsigned char *ents = malloc((size_t)num * esz);
  if (!ents) { close(fd); return -1; }
  int rc = pread_full(fd, ents, (size_t)num * esz, (off_t)(ent_lba * lbs));
  close(fd);
  if (rc) { free(ents); return -1; }
  int found = 0;
  for (uint32_t i = 0; i < num; i++) {
    unsigned char *e = ents + (size_t)i * esz;
    char name[37] = {0};
    for (int j = 0; j < 36; j++) name[j] = (char)e[56 + 2 * j];   /* UTF-16LE, ASCII names */
    for (int s = 0; s < 2; s++)
      if (!strcmp(name, s ? "vbmeta_b" : "vbmeta_a")) { memcpy(&attr[s], e + 48, 8); found |= 1 << s; }
  }
  free(ents);
  return found == 3 ? 0 : -1;
}

static int wanted_slot(const uint64_t attr[2]) {
  int best = -1, bestp = 0;
  for (int s = 0; s < 2; s++) {
    int prio = (attr[s] >> 48) & 15, tries = (attr[s] >> 52) & 15, ok = (attr[s] >> 56) & 1;
    if (prio > 0 && (ok || tries > 0) && prio > bestp) { best = s; bestp = prio; }
  }
  return best;
}

/* ---- boot images ---- */
static int read_part(const char *name, unsigned char **buf, size_t *len) {
  char p[128]; snprintf(p, sizeof p, "/dev/block/by-name/%s", name);
  return read_all(p, buf, len);
}
static size_t pad(size_t n, size_t page) { return (n + page - 1) / page * page; }

/* generic ramdisk from init_boot (boot header v3/v4, page 4096) */
static int generic_ramdisk(const unsigned char *img, size_t len, size_t *off, size_t *size) {
  uint32_t ksz, rsz, ver;
  if (len < 4096 || memcmp(img, "ANDROID!", 8)) return -1;
  memcpy(&ksz, img + 8, 4); memcpy(&rsz, img + 12, 4); memcpy(&ver, img + 40, 4);
  if (ver < 3) return -1;
  *off = 4096 + pad(ksz, 4096); *size = rsz;
  return *off + *size <= len ? 0 : -1;
}
/* vendor ramdisk + bootconfig from vendor_boot (v4) */
static int vendor_parts(const unsigned char *img, size_t len, size_t *roff, size_t *rsz, size_t *boff, size_t *bsz) {
  uint32_t ver, page, vr, hs, dtb, tbl, bc;
  if (len < 2128 || memcmp(img, "VNDRBOOT", 8)) return -1;
  memcpy(&ver, img + 8, 4); memcpy(&page, img + 12, 4); memcpy(&vr, img + 24, 4);
  memcpy(&hs, img + 2096, 4); memcpy(&dtb, img + 2100, 4); memcpy(&tbl, img + 2112, 4); memcpy(&bc, img + 2124, 4);
  if (ver < 4 || !page) return -1;
  size_t o = pad(hs, page);
  *roff = o; *rsz = vr;
  o += pad(vr, page) + pad(dtb, page) + pad(tbl, page);
  *boff = o; *bsz = bc;
  if (o + bc > len) return -1;                            /* bounds first, then strip the zero padding */
  while (*bsz && img[*boff + *bsz - 1] == 0) (*bsz)--;
  return 0;
}

/* zlib-compatible CRC32 (build_esp.py writes the same stamp with zlib.crc32) */
static uint32_t crc32_upd(uint32_t crc, const unsigned char *p, size_t n) {
  static uint32_t t[256];
  if (!t[1])
    for (uint32_t i = 0; i < 256; i++) {
      uint32_t c = i;
      for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
      t[i] = c;
    }
  crc = ~crc;
  while (n--) crc = t[(crc ^ *p++) & 0xff] ^ (crc >> 8);
  return ~crc;
}

static int write_file(const char *path, const void *data, size_t n) {
  char tmp[PATH_MAX];
  if (snprintf(tmp, sizeof tmp, "%s.tmp", path) >= (int)sizeof tmp) { errno = ENAMETOOLONG; return -1; }
  int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
  if (fd < 0) return -1;
  size_t done = 0;
  while (done < n) {
    ssize_t w = write(fd, (const char *)data + done, n - done);
    if (w <= 0) { close(fd); unlink(tmp); return -1; }
    done += (size_t)w;
  }
  if (fsync(fd) || close(fd)) { unlink(tmp); return -1; }
  return rename(tmp, path);
}

/* Build initrd-<s>.img for slot s; returns 0 if written or already current. */
static int build_slot(char s) {
  char ib[16], vb[16], path[PATH_MAX], img[PATH_MAX], injp[PATH_MAX], exp[PATH_MAX];
  snprintf(ib, sizeof ib, "init_boot_%c", s); snprintf(vb, sizeof vb, "vendor_boot_%c", s);
  snprintf(injp, sizeof injp, "%s/gb/inject.gz", mnt); snprintf(exp, sizeof exp, "%s/gb/bootconfig_extra", mnt);
  snprintf(path, sizeof path, "%s/gb/slot_%c.stamp", mnt, s); snprintf(img, sizeof img, "%s/initrd-%c.img", mnt, s);
  unsigned char *ini = NULL, *ven = NULL, *inj = NULL, *extra = NULL, *stamp = NULL;
  size_t ilen, vlen, injlen, exlen, stlen, goff, gsz, roff, rsz, boff, bsz;
  int rc = -1;
  if (read_part(ib, &ini, &ilen) || read_part(vb, &ven, &vlen)) { logmsg("cannot read %s/%s", ib, vb); goto out; }
  if (generic_ramdisk(ini, ilen, &goff, &gsz) || vendor_parts(ven, vlen, &roff, &rsz, &boff, &bsz)) {
    logmsg("slot %c: bad init_boot/vendor_boot image", s); goto out;
  }
  if (read_all(injp, &inj, &injlen) || read_all(exp, &extra, &exlen)) {
    logmsg("ESP lacks /gb/inject.gz or /gb/bootconfig_extra (old build_esp?)"); goto out;
  }
  uint32_t h = 0;
  h = crc32_upd(h, inj, injlen); h = crc32_upd(h, extra, exlen);
  h = crc32_upd(h, ini + goff, gsz); h = crc32_upd(h, ven + roff, rsz); h = crc32_upd(h, ven + boff, bsz);
  char want[32]; snprintf(want, sizeof want, "%08x\n", h);
  if (!read_all(path, &stamp, &stlen) && stlen == strlen(want) && !memcmp(stamp, want, stlen) && !access(img, R_OK)) {
    rc = 0; goto out;                                       /* already built from these images */
  }
  /* bootconfig = vendor bootconfig + slot suffix + common extra lines */
  char slotline[64]; int sl = snprintf(slotline, sizeof slotline, "androidboot.slot_suffix = \"_%c\"\n", s);
  size_t bclen = bsz + (bsz && ven[boff + bsz - 1] != '\n') + (size_t)sl + exlen;
  size_t total = pad(injlen, 4) + rsz + gsz;
  total = pad(total, 4) + bclen + 8 + 12;
  unsigned char *out = calloc(1, total), *p = out;
  if (!out) { logmsg("slot %c: out of memory (%zu bytes)", s, total); goto out; }
  memcpy(p, inj, injlen); p = out + pad(injlen, 4);
  memcpy(p, ven + roff, rsz); p += rsz;
  memcpy(p, ini + goff, gsz); p += gsz;
  p = out + pad((size_t)(p - out), 4);
  unsigned char *bc = p;
  memcpy(p, ven + boff, bsz); p += bsz;
  if (bsz && ven[boff + bsz - 1] != '\n') *p++ = '\n';
  memcpy(p, slotline, (size_t)sl); p += sl;
  memcpy(p, extra, exlen); p += exlen;
  uint32_t size = (uint32_t)(p - bc), csum = 0;
  for (uint32_t i = 0; i < size; i++) csum += bc[i];
  memcpy(p, &size, 4); memcpy(p + 4, &csum, 4); memcpy(p + 8, "#BOOTCONFIG\n", 12); p += 20;
  if (dry_run) { logmsg("dry-run: would write initrd-%c.img (%zu bytes)", s, (size_t)(p - out)); rc = 0; free(out); goto out; }
  if (write_file(img, out, (size_t)(p - out)) || write_file(path, want, strlen(want))) {
    logmsg("writing initrd-%c.img failed: %s", s, strerror(errno));
  } else {
    logmsg("built initrd-%c.img (%zu bytes) from %s + %s", s, (size_t)(p - out), ib, vb);
    rc = 0;
  }
  free(out);
out:
  free(ini); free(ven); free(inj); free(extra); free(stamp);
  return rc;
}

/* Bounded append: 0, or -1 if the output would not fit (nothing is written past *rem). */
static int appendf(char **o, size_t *rem, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
static int appendf(char **o, size_t *rem, const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(*o, *rem, fmt, ap);
  va_end(ap);
  if (n < 0 || (size_t)n >= *rem) return -1;
  *o += n; *rem -= (size_t)n;
  return 0;
}

/* loader/entries/gb-<s><sfx>.conf from the current slot's gb-<cur><sfx>.conf: the title's "(slot X)" and the initrd
 * line name slot s, everything else is copied (a CR before the newline is kept). Returns 0 if written, already present
 * (s == cur) or - with required == 0 - if there is no template; -1 on failure.
 * C-19: the template is copied into a fresh NUL-terminated buffer before strtok_r; every buffer is freed exactly once. */
static int copy_entry(char s, char cur, const char *sfx, int required) {
  char src[PATH_MAX], dst[PATH_MAX];
  snprintf(src, sizeof src, "%s/loader/entries/gb-%c%s.conf", mnt, cur, sfx);
  snprintf(dst, sizeof dst, "%s/loader/entries/gb-%c%s.conf", mnt, s, sfx);
  if (s == cur && !access(dst, R_OK)) return 0;
  unsigned char *t = NULL; size_t tl = 0;
  if (read_all(src, &t, &tl)) {
    if (!required && errno == ENOENT) return 0;           /* no such template: nothing to copy */
    logmsg("no template entry %s (%s)", src, strerror(errno));
    return -1;
  }
  char *txt = tl < SIZE_MAX / 2 - 512 ? malloc(tl + 1) : NULL;
  size_t cap = tl < SIZE_MAX / 2 - 512 ? 2 * tl + 512 : 0;
  char *outb = cap ? malloc(cap) : NULL;
  if (!txt || !outb) { logmsg("copying %s: out of memory", src); free(t); free(txt); free(outb); return -1; }
  memcpy(txt, t, tl); txt[tl] = 0;
  free(t);
  char *o = outb, *save = NULL;
  size_t rem = cap;
  int bad = 0;
  for (char *line = strtok_r(txt, "\n", &save); line && !bad; line = strtok_r(NULL, "\n", &save)) {
    size_t ll = strlen(line);
    const char *cr = ll && line[ll - 1] == '\r' ? "\r" : "";
    if (!strncmp(line, "title", 5)) {
      char *q = strstr(line, "(slot ");
      if (q && q[6]) q[6] = (char)(s - 32);
      bad = appendf(&o, &rem, "%s\n", line);
    }
    else if (!strncmp(line, "initrd", 6)) bad = appendf(&o, &rem, "initrd  /initrd-%c.img%s\n", s, cr);
    else bad = appendf(&o, &rem, "%s\n", line);
  }
  int r = -1;
  if (bad) logmsg("entry %s does not fit the output buffer", src);
  else if (dry_run) { logmsg("dry-run: would write %s", dst); r = 0; }
  else if (write_file(dst, outb, (size_t)(o - outb))) logmsg("writing %s failed: %s", dst, strerror(errno));
  else r = 0;
  free(outb); free(txt);
  return r;
}

/* loader/entries/gb-<s>.conf (+ gb-<s>-nvtest.conf) from the current slot's entries, and loader.conf default -> gb-<s> */
static int select_entry(char s, char cur) {
  if (copy_entry(s, cur, "", 1)) return -1;
  if (copy_entry(s, cur, "-nvtest", 0))                    /* optional: never blocks the switch */
    logmsg("slot %c: the [NVIDIA test] entry was not copied (the default entry is unaffected)", s);
  char lcp[PATH_MAX]; snprintf(lcp, sizeof lcp, "%s/loader/loader.conf", mnt);
  unsigned char *lc = NULL; size_t ll = 0;
  if (read_all(lcp, &lc, &ll)) { logmsg("cannot read %s", lcp); return -1; }
  char *conf = realloc(lc, ll + 1);
  if (!conf) { free(lc); logmsg("cannot read %s: out of memory", lcp); return -1; }
  conf[ll] = 0;
  char want[32]; snprintf(want, sizeof want, "default gb-%c", s);
  if (strstr(conf, want)) { free(conf); return 0; }
  size_t cap = 2 * ll + 64, rem = cap;
  char *outb = malloc(cap), *o = outb, *save = NULL;
  if (!outb) { free(conf); logmsg("loader.conf: out of memory"); return -1; }
  int done = 0, bad = 0;
  for (char *line = strtok_r(conf, "\n", &save); line && !bad; line = strtok_r(NULL, "\n", &save)) {
    if (!strncmp(line, "default", 7)) { bad = appendf(&o, &rem, "%s\n", want); done = 1; }
    else bad = appendf(&o, &rem, "%s\n", line);
  }
  if (!done && !bad) bad = appendf(&o, &rem, "%s\n", want);
  int r = bad ? -1 : dry_run ? 0 : write_file(lcp, outb, (size_t)(o - outb));
  free(outb); free(conf);
  if (!r) logmsg("systemd-boot default -> gb-%c", s);
  else logmsg("writing %s failed", lcp);
  return r;
}

static int read_line(const char *path, char *buf, size_t cap) {
  FILE *f = fopen(path, "r");
  if (!f) return -1;
  if (!fgets(buf, (int)cap, f)) buf[0] = 0;
  fclose(f);
  buf[strcspn(buf, "\r\n")] = 0;
  return 0;
}

/* ---- ESP lock (INTERFACES 5.4) ---- */
static long uptime_s(void) {
  char b[64] = "";
  if (read_line("/proc/uptime", b, sizeof b)) return 0;
  return strtol(b, NULL, 10);
}

/* printable, at most 60 characters (owner lines are untrusted text) */
static const char *san(const char *s, char out[64]) {
  size_t i = 0;
  for (; s[i] && i < 60; i++) out[i] = (s[i] >= 0x20 && s[i] < 0x7f) ? s[i] : '?';
  out[i] = 0;
  return out;
}

/* first line of <dir>/owner without the newline; "" if missing or empty */
static void read_owner(const char *dir, char *buf, size_t cap) {
  char p[PATH_MAX];
  buf[0] = 0;
  if (snprintf(p, sizeof p, "%s/owner", dir) >= (int)sizeof p) return;
  int fd = open(p, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
  if (fd < 0) return;
  ssize_t n = read(fd, buf, cap - 1);
  close(fd);
  buf[n > 0 ? n : 0] = 0;
  buf[strcspn(buf, "\n")] = 0;
}

/* stale = owner field not a number, or no /proc/<pid> */
static int owner_stale(const char *ol) {
  size_t n = strcspn(ol, " ");
  if (!n) return 1;
  for (size_t i = 0; i < n; i++) if (ol[i] < '0' || ol[i] > '9') return 1;
  char p[PATH_MAX];
  struct stat st;
  snprintf(p, sizeof p, "%s/%.*s", proc_root, (int)(n > 20 ? 20 : n), ol);
  return !(stat(p, &st) == 0 && S_ISDIR(st.st_mode));
}

/* rename(2) that never replaces an existing (possibly freshly created, still empty) lock directory */
static int rename_noreplace(const char *from, const char *to) {
  if (!syscall(__NR_renameat2, AT_FDCWD, from, AT_FDCWD, to, 1u /* RENAME_NOREPLACE */)) return 0;
  if (errno != ENOSYS && errno != EINVAL) return -1;
  return rename(from, to);
}

static void remove_lockdir(const char *dir) {
  char p[PATH_MAX];
  if (snprintf(p, sizeof p, "%s/owner", dir) < (int)sizeof p) unlink(p);
  rmdir(dir);
}

/* 0 = the stale lock (same inode, same owner line as seen) was removed: retry mkdir; -1 = not stolen */
static int steal_lock(const char *seen_owner, ino_t seen_ino) {
  char st[PATH_MAX], o2[256], s1[64], s2[64];
  struct stat sb;
  snprintf(st, sizeof st, "%s.stale.%d", lock_path, (int)getpid());
  remove_lockdir(st);                                       /* a leftover of an earlier steal by this pid */
  if (rename_noreplace(lock_path, st)) return -1;           /* another waiter won, or the lock is gone */
  read_owner(st, o2, sizeof o2);
  if (!stat(st, &sb) && sb.st_ino == seen_ino && !strcmp(o2, seen_owner)) {
    logmsg("ESP lock: stealing a stale lock (owner '%s')", san(seen_owner, s1));
    remove_lockdir(st);
    return 0;
  }
  if (!rename_noreplace(st, lock_path))
    logmsg("ESP lock: the lock changed while stealing it (owner '%s'): put back", san(o2, s2));
  else
    logmsg("ESP lock: the lock changed while stealing it and could not be put back (left as %s)", basename(st));
  return -1;
}

/* <lock>/owner = our line, created exclusively (not write_file: its .tmp rename could leave a file that blocks rmdir).
 * 0 = written; 1 = the directory vanished meanwhile (a concurrent stealer renamed it away): wait again; -1 = error */
static int write_owner(void) {
  char op[PATH_MAX];
  snprintf(lock_own, sizeof lock_own, "%d gb_slotsync %ld", (int)getpid(), uptime_s());
  snprintf(op, sizeof op, "%s/owner", lock_path);
  int fd = open(op, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
  if (fd < 0 && errno == ENOENT) { lock_own[0] = 0; return 1; }
  int ok = fd >= 0 && dprintf(fd, "%s\n", lock_own) == (int)strlen(lock_own) + 1;
  int err = errno;
  if (fd >= 0 && close(fd)) { ok = 0; err = errno; }
  if (ok) return 0;
  logmsg("ESP lock: cannot write %s: %s", op, strerror(err));
  remove_lockdir(lock_path);
  lock_own[0] = 0;
  return -1;
}

/* 0 = we hold the lock (lock_own = our owner line); -1 = not taken (waited LOCK_WAIT_MAX s, or an error) */
static int lock_esp(void) {
  int w = 0, miss = 0;
  char ol[256], s1[64];
  lock_own[0] = 0;
  for (;;) {
    if (!mkdir(lock_path, 0700)) {
      int r = write_owner();
      if (!r) break;
      if (r < 0) return -1;
    } else if (errno != EEXIST) {
      logmsg("ESP lock: mkdir %s: %s", lock_path, strerror(errno));
      return -1;
    }
    struct stat sb;
    int have_ino = !stat(lock_path, &sb);
    read_owner(lock_path, ol, sizeof ol);
    int stale = 0;
    if (ol[0]) { miss = 0; stale = owner_stale(ol); }
    else if (++miss >= LOCK_MISS_POLLS) stale = 1;
    if (stale && have_ino) {
      miss = 0;
      if (!steal_lock(ol, sb.st_ino)) continue;
    }
    if (w >= LOCK_WAIT_MAX) {
      lock_waited = w;
      logmsg("ESP lock held over %d s (owner '%s'): skipping this cycle", LOCK_WAIT_MAX, san(ol, s1));
      return -1;
    }
    poll_sleep();
    w++;
  }
  lock_waited = w;
#ifdef GB_SLOTSYNC_TEST
  printf("test: lock acquired after %d s (owner '%s')\n", w, lock_own);
#endif
  return 0;
}

/* release only our own lock (a lock stolen from us belongs to someone else now) */
static void unlock_esp(void) {
  char ol[256], s1[64];
  if (!lock_own[0]) return;
  read_owner(lock_path, ol, sizeof ol);
  if (!strcmp(ol, lock_own)) {
    remove_lockdir(lock_path);
#ifdef GB_SLOTSYNC_TEST
    printf("test: lock released\n");
#endif
  } else {
    logmsg("ESP lock: not ours any more at release (owner '%s'): left alone", san(ol, s1));
  }
  lock_own[0] = 0;
}

/* The GPT flag alone is not trusted: an update that was rolled back by booting the old slot leaves the other slot
 * marked active with nothing behind it. Switch only for an update that update_engine completed during THIS boot
 * (prefs/update-completed-on-boot-id == boot_id, i.e. "waiting to reboot"); otherwise keep the systemd-boot default
 * on the slot that actually booted -- which also makes a failed new slot fall back once the old one is picked. */
static int sync_once(void) {
  static char settled;                                      /* slot whose entry is known to be the default */
  char suf[PROP_VALUE_MAX] = "";
  __system_property_get("ro.boot.slot_suffix", suf);
  if (strlen(suf) != 2 || (suf[1] != 'a' && suf[1] != 'b')) { logmsg("no ro.boot.slot_suffix"); return -1; }
  char cur = suf[1], target = cur;
  char bid[64] = "", done_bid[64] = "";
#ifdef GB_SLOTSYNC_TEST
  snprintf(bid, sizeof bid, "%s", test_env("GB_SLOTSYNC_BOOT_ID", ""));
#else
  read_line("/proc/sys/kernel/random/boot_id", bid, sizeof bid);
#endif
  int pending = !read_line("/data/misc/update_engine/prefs/update-completed-on-boot-id", done_bid, sizeof done_bid) &&
                bid[0] && !strcmp(bid, done_bid);
  uint64_t attr[2] = {0, 0};
  if (pending) {
    if (vbmeta_attrs(attr)) { logmsg("cannot read vbmeta_a/b GPT attributes"); return -1; }
    int w = wanted_slot(attr);
    if (w >= 0) target = (char)('a' + w);
  }
  if (target == settled) return 0;
  if (target != cur)
    logmsg("update applied, slot %c active (attr a=%016llx b=%016llx), booted %c: preparing the ESP", target,
           (unsigned long long)attr[0], (unsigned long long)attr[1], cur);
  if (lock_esp()) return -1;                                /* settled stays unset: the next tick retries */
  mkdir(mnt, 0700);
  if (do_mount() && errno != EBUSY) {
    logmsg("mount %s: %s", ESP_DEV, strerror(errno));
    unlock_esp();
    return -1;
  }
  int rc = target != cur ? build_slot(target) : 0;
  if (!rc) rc = select_entry(target, cur);
  do_umount();
  unlock_esp();
  if (!rc) settled = target;
#ifdef GB_SLOTSYNC_TEST
  printf("test: sync_once rc=%d settled=%c\n", rc, settled ? settled : '-');
#endif
  return rc;
}

int main(int argc, char **argv) {
  int watch = 0;
#ifdef GB_SLOTSYNC_TEST
  mnt = test_env("GB_SLOTSYNC_MNT", mnt);
  lock_path = test_env("GB_SLOTSYNC_LOCK", lock_path);
  proc_root = test_env("GB_SLOTSYNC_PROC", proc_root);
  if (argc == 4 && !strcmp(argv[1], "--test-copy-entry")) {        /* --test-copy-entry S CUR */
    int rc = select_entry(argv[2][0], argv[3][0]);
    printf("test: select_entry rc=%d\n", rc);
    return rc ? 1 : 0;
  }
  if (argc == 2 && !strcmp(argv[1], "--test-lock")) {               /* hold GB_SLOTSYNC_TEST_HOLD_MS, then release */
    if (lock_esp()) { printf("test: lock not taken after %d s\n", lock_waited); return 3; }
    long ms = strtol(test_env("GB_SLOTSYNC_TEST_HOLD_MS", "0"), NULL, 10);
    struct timespec ts = {ms / 1000, (ms % 1000) * 1000000L};
    nanosleep(&ts, NULL);
    unlock_esp();
    return 0;
  }
#endif
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--dry-run")) dry_run = 1;
    else if (!strcmp(argv[i], "--watch") && i + 1 < argc) watch = atoi(argv[++i]);
  }
  if (!watch) return sync_once() ? 1 : 0;
  for (;;) { sync_once(); sleep((unsigned)watch); }
}
