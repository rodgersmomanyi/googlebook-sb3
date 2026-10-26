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
 * slot's entries stay in the menu as a fallback while they still match that slot (wave 3 safety review, MEDIUM): a
 * rebuild removes the slot's older entries before it writes the new initrd, and with no switch pending the
 * non-running slot's entries are checked once per boot and whenever its GPT bootable state changes -- they are removed
 * when the slot is not bootable (an update is being written into it) or its initrd no longer matches its
 * init_boot/vendor_boot (stamp), so no menu entry boots a slot's newer system with an older ramdisk.
 *
 * ESP files used (written by build_esp.py): /gb/inject.gz (gb cpio, gzip, padded to 4 bytes), /gb/bootconfig_extra
 * (bootconfig lines common to both slots), /loader/entries/gb-<current>.conf (template, required),
 * /loader/entries/gb-<current>-nvtest.conf (template, optional), /loader/loader.conf. It writes only gb/slot_<x>.stamp,
 * initrd-<x>.img, loader/entries/gb-<x>[-nvtest].conf and loader/loader.conf, and removes only the non-running
 * slot's gb/slot_<x>.stamp and loader/entries/gb-<x>[-nvtest].conf: anything else on the ESP (gblog\,
 * gbinstall\, the opt-in bring-up files gb\gb.cfg and gb\overlay\, WAVE3 contract 2.3) is never touched.
 *
 * Kernel check before a switch (WAVE3 item 13, lead decision D-a, INTERFACES 12.2 N22/N23): lapis's vendor modules for
 * build X are built against the GKI in X's boot_<x>, but this ESP boots our own GKI. Before the default moves to the
 * updated slot, the KMI generation <maj>.<min>-android<N>-<gen> of boot_<target>'s kernel (bzImage setup header,
 * kernel_version string, read from the first 64 KiB) is compared with the running kernel's (uname). Same KMI: switch
 * (a new GKI sublevel within the same KMI is logged, not held). Different KMI, or a version that cannot be read: HOLD
 * - the initrd and the "(slot X)" entries are still written, so the user can pick the new slot once in the menu, but
 * loader.conf keeps the old default; the line "gb_slotsync: HOLD slot <x>: <reason>; default stays gb-<cur>" goes to
 * kmsg once per (slot, release) and again once on every later boot while the hold is in effect, and the notification
 * "System update held" (tag gb_update) is queued for gb_notify once the held slot's initrd and entries are written.
 * Idea: the gbOS86 porting notes (github.com/linuxkid473/gbos-x86 @f8a5c1a, porting.md; no code taken).
 *
 * Every ESP mount is done inside the ESP lock /dev/gb_esp.lock (INTERFACES 5.4 r4: mkdir lock, no mkdir and a back-off
 * while a steal is in progress (only consecutive back-off polls count), owner line "<pid> gb_slotsync <uptime_s>"
 * created exclusively, atomic rename-based steal of a stale lock (a lock naming our own pid that we do not hold is
 * stale) with a put-back that never replaces, retried every 50 ms for up to 5 s, release only our own lock and wait up
 * to 7 polls for a put-back first). After 600 s of waiting this cycle is skipped (the --watch loop retries 60 s later).
 * The ESP is mounted with "flush". The loader.conf default is matched as a whole line (gb-<x>, not gb-<x>-nvtest).
 *
 * Usage: gb_slotsync [--watch SECONDS] [--dry-run]
 * Build (builder, NDK r27c): /build/ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/clang --target=x86_64-linux-android34
 *        -O2 -Wall -Wextra -Werror -o gb_slotsync gb_slotsync.c
 * Test build (host only, never shipped): gcc -DGB_SLOTSYNC_TEST ... (test/test_gb_slotsync.sh): paths, the slot suffix,
 *        the boot id, the by-name directory, the update_engine prefs file, the vbmeta attributes, uname and the
 *        notification queue come from GB_SLOTSYNC_* environment variables, mount/umount2/sync/setprop are no-ops, the
 *        lock's 1 s polls run on a fake clock (10 ms each), and --test-copy-entry / --test-lock / --test-ticks /
 *        --test-wanted-slot / --test-kmi exist (lock race injections: GB_SLOTSYNC_TEST_RACE_OWNER, _DISPLACE,
 *        _PUTBACK[_POLLS], _STEAL_RACE, _INTRUDE_POLLS, _OWN_ORPHAN).
 */
#include <dirent.h>
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
#include <sys/time.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>
#ifdef GB_SLOTSYNC_TEST
#define PROP_VALUE_MAX 92
#else
#include <sys/system_properties.h>
#endif

#define ESP_DEV "/dev/block/by-name/EFI-SYSTEM"
#define LOCK_WAIT_MAX 600                                   /* s, INTERFACES 2.3 (log writers) */
#define LOCK_MISS_POLLS 5                                   /* polls without an owner file = dead holder */
#define LOCK_RELEASE_POLLS 7                                /* r4: longer than a stealer's 5 s put-back budget */
#define PUTBACK_TRIES 100                                   /* r4: put-back retried every 50 ms for up to 5 s */
#define GKI_READ (64 * 1024)                                /* bytes of boot_<x> read for the kernel version */

static const char *mnt = "/mnt/gb_slotsync";
static const char *lock_path = "/dev/gb_esp.lock";
static const char *proc_root = "/proc";
static const char *byname = "/dev/block/by-name";
static const char *ue_prefs = "/data/misc/update_engine/prefs/update-completed-on-boot-id";
static const char *notify_dir = "/data/vendor/gb/notify";   /* INTERFACES 12.1 queue (gb_notify, native tree) */
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
static int __system_property_set(const char *name, const char *value) {
  printf("test: setprop %s %s\n", name, value);
  return 0;
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

/* snprintf into a fixed buffer where a cut is acceptable (log and message texts) */
static void bfmt(char *o, size_t n, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
static void bfmt(char *o, size_t n, const char *fmt, ...) {
  va_list ap;
  va_start(ap, fmt); vsnprintf(o, n, fmt, ap); va_end(ap);
}

/* 1 s poll of the lock wait (fake clock in the test build: 10 ms of real time per second). Test build only:
 * GB_SLOTSYNC_TEST_PUTBACK=<dir> + GB_SLOTSYNC_TEST_PUTBACK_POLLS=<n>: after n polls <dir> is renamed back to the
 * lock path (a stealer's put-back, for the release case of --test-lock). */
#ifdef GB_SLOTSYNC_TEST
static int test_polls;
#endif
static void poll_sleep(void) {
#ifdef GB_SLOTSYNC_TEST
  struct timespec ts = {0, 10 * 1000 * 1000};
  nanosleep(&ts, NULL);
  test_polls++;
  const char *pb = getenv("GB_SLOTSYNC_TEST_PUTBACK"), *pn = getenv("GB_SLOTSYNC_TEST_PUTBACK_POLLS");
  if (pb && *pb && pn && test_polls == atoi(pn)) {
    if (rename(pb, lock_path)) printf("test: put-back failed: %s\n", strerror(errno));
    else printf("test: put back after %d polls\n", test_polls);
  }
#else
  sleep(1);
#endif
}

/* r4: the 50 ms pause between two put-back attempts of a steal (test build: 1 ms, counted in test_polls too, so
 * GB_SLOTSYNC_TEST_INTRUDE_POLLS counts put-back attempts) */
static void putback_sleep(void) {
#ifdef GB_SLOTSYNC_TEST
  struct timespec ts = {0, 1000 * 1000};
  nanosleep(&ts, NULL);
  test_polls++;
#else
  struct timespec ts = {0, 50 * 1000 * 1000};
  nanosleep(&ts, NULL);
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

static int __attribute__((unused)) pread_full(int fd, void *buf, size_t n, off_t off) {
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
#ifdef GB_SLOTSYNC_TEST
  /* GB_SLOTSYNC_TEST_ATTRS="<hex a> <hex b>" stands in for the GPT read */
  const char *ta = getenv("GB_SLOTSYNC_TEST_ATTRS");
  if (!ta || !*ta) return -1;
  char *e1 = NULL, *e2 = NULL;
  attr[0] = strtoull(ta, &e1, 16);
  attr[1] = strtoull(e1, &e2, 16);
  return e2 == e1 ? -1 : 0;
#else
  char part[PATH_MAX], sys[PATH_MAX], link[PATH_MAX], disk[PATH_MAX], vp[PATH_MAX];
  snprintf(vp, sizeof vp, "%s/vbmeta_a", byname);
  if (!realpath(vp, part)) return -1;
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
#endif
}

/* The slot depthcharge would boot: the highest priority among slots that are successful or have tries left (the
 * first slot wins a tie). install_dualboot.ps1 -UpdateEspOnly mirrors this bit for bit (Get-GbWantedSlot); both test
 * suites run the same truth table (test_gb_slotsync.sh, block "wanted_slot-vectors"). */
static int slot_bootable(uint64_t a) {                       /* priority > 0 and (successful or tries left) */
  int prio = (a >> 48) & 15, tries = (a >> 52) & 15, ok = (a >> 56) & 1;
  return prio > 0 && (ok || tries > 0);
}
static int wanted_slot(const uint64_t attr[2]) {
  int best = -1, bestp = 0;
  for (int s = 0; s < 2; s++) {
    int prio = (attr[s] >> 48) & 15;
    if (slot_bootable(attr[s]) && prio > bestp) { best = s; bestp = prio; }
  }
  return best;
}

/* ---- boot images ---- */
static int read_part(const char *name, unsigned char **buf, size_t *len) {
  char p[PATH_MAX]; snprintf(p, sizeof p, "%s/%s", byname, name);
  return read_all(p, buf, len);
}

/* ---- kernel check (item 13, D-a) ---- */
/* Kernel release of boot_<s>: boot header v3/v4 ("ANDROID!", kernel at +4096), the kernel is a bzImage whose setup
 * header has "HdrS" at +0x202 and the kernel_version pointer (u16) at +0x20E; the string is at +0x200 + pointer.
 * Only the first 64 KiB are read. 0 = out holds the release (printable, up to the first blank or NUL); -1 = no. */
static int gki_release(char s, char *out, size_t n) {
  char p[PATH_MAX];
  snprintf(p, sizeof p, "%s/boot_%c", byname, s);
  out[0] = 0;
  int fd = open(p, O_RDONLY | O_CLOEXEC);
  if (fd < 0) return -1;
  unsigned char *b = malloc(GKI_READ);
  if (!b) { close(fd); return -1; }
  size_t len = 0;
  while (len < GKI_READ) {                                  /* a short file is fine if the string fits in it */
    ssize_t r = read(fd, b + len, GKI_READ - len);
    if (r <= 0) break;
    len += (size_t)r;
  }
  close(fd);
  int rc = -1;
  uint32_t ksz = 0, ver = 0;
  uint16_t ptr = 0;
  if (len >= 4096 + 0x210 && !memcmp(b, "ANDROID!", 8)) {
    memcpy(&ksz, b + 8, 4); memcpy(&ver, b + 40, 4);
    const unsigned char *k = b + 4096;
    if (ver >= 3 && ksz >= 0x400 && !memcmp(k + 0x202, "HdrS", 4)) memcpy(&ptr, k + 0x20E, 2);
  }
  size_t off = 4096 + 0x200 + (size_t)ptr;
  if (ptr && (size_t)ptr + 0x200 < GKI_READ - 4096 && off < len && (size_t)ptr + 0x200 < ksz) {
    size_t i = 0;
    while (off + i < len && i + 1 < n && b[off + i] > 0x20 && b[off + i] < 0x7f) { out[i] = (char)b[off + i]; i++; }
    int end = off + i < len && (b[off + i] == 0 || b[off + i] == ' ');
    out[i] = 0;
    if (i && end) rc = 0;
    else out[0] = 0;
  }
  free(b);
  return rc;
}

/* KMI generation of a kernel release "<maj>.<min>.<sub>-android<N>-<gen>[-...]" -> "<maj>.<min>-android<N>-<gen>"
 * (6.18.21-android17-5-g3938c8d97142-ab16302624 -> 6.18-android17-5). 0 = parsed. */
static int kmi_of(const char *rel, char *out, size_t n) {
  const char *p = rel;
  size_t a = strspn(p, "0123456789");
  if (!a || p[a] != '.') return -1;
  size_t b = strspn(p + a + 1, "0123456789");
  if (!b) return -1;
  const char *q = p + a + 1 + b;                            /* after <maj>.<min> */
  if (*q == '.') { size_t c = strspn(q + 1, "0123456789"); if (!c) return -1; q += 1 + c; }
  if (strncmp(q, "-android", 8)) return -1;
  size_t d = strspn(q + 8, "0123456789");
  if (!d || q[8 + d] != '-') return -1;
  size_t g = strspn(q + 9 + d, "0123456789");
  if (!g) return -1;
  char t = q[9 + d + g];
  if (t && t != '-' && t != '+') return -1;
  int r = snprintf(out, n, "%.*s-android%.*s-%.*s", (int)(a + 1 + b), p, (int)d, q + 8, (int)g, q + 9 + d);
  return r > 0 && (size_t)r < n ? 0 : -1;
}

static void running_release(char *out, size_t n) {
#ifdef GB_SLOTSYNC_TEST
  snprintf(out, n, "%s", test_env("GB_SLOTSYNC_TEST_UNAME", ""));
#else
  struct utsname u;
  if (uname(&u)) out[0] = 0;
  else snprintf(out, n, "%s", u.release);
#endif
}

/* Notification for the gb_notify queue (INTERFACES 12.1: <epoch_ms 13 digits>-<pid>-<n>.msg, written as an exclusive
 * .tmp without following a symlink, mode 0644, renamed; then vendor.gb.notify = epoch_ms). Best effort: without the
 * queue directory (a tree without the nouveau component, e.g. the VM) the kmsg line is all there is. */
static void post_notify(const char *tag, const char *title, const char *text) {
  static int seq;
  struct timeval tv;
  gettimeofday(&tv, NULL);
  long long ms = (long long)tv.tv_sec * 1000 + tv.tv_usec / 1000;
  char tmp[PATH_MAX], dst[PATH_MAX], body[600], val[24];
  struct stat st;
  if (stat(notify_dir, &st) || !S_ISDIR(st.st_mode)) { logmsg("no notification queue %s: kmsg only", notify_dir); return; }
  if (dry_run) { logmsg("dry-run: would post the notification '%s'", title); return; }
  seq++;
  if (snprintf(dst, sizeof dst, "%s/%013lld-%d-%d.msg", notify_dir, ms, (int)getpid(), seq) >= (int)sizeof dst ||
      snprintf(tmp, sizeof tmp, "%s.tmp", dst) >= (int)sizeof tmp) { logmsg("notification: path too long"); return; }
  int bl = snprintf(body, sizeof body, "kind=notify\ntag=%s\ntitle=%s\ntext=%s\n", tag, title, text);
  int fd = open(tmp, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644);
  if (fd < 0) { logmsg("notification: cannot create %s: %s", tmp, strerror(errno)); return; }
  int ok = bl > 0 && bl < (int)sizeof body && fchmod(fd, 0644) == 0 && write(fd, body, (size_t)bl) == bl;
  if (close(fd)) ok = 0;
  if (!ok || rename(tmp, dst)) { logmsg("notification: cannot write %s", dst); unlink(tmp); return; }
  snprintf(val, sizeof val, "%lld", ms);
  if (__system_property_set("vendor.gb.notify", val)) logmsg("notification: setprop vendor.gb.notify failed");
}

/* Decision for an update in slot t while slot c runs. 0 = switch, 1 = hold. rel = boot_<t>'s release ("" if
 * unreadable); reason = the contract-2.6 reason for a hold, or the note logged with a switch. */
static int kernel_check(char t, char c, char *rel, size_t rn, char *reason, size_t sn, int *unreadable) {
  char run[128], tk[96], rk[96], crel[128];
  *unreadable = 0;
  running_release(run, sizeof run);
  if (gki_release(t, rel, rn) || kmi_of(rel, tk, sizeof tk)) {
    rel[0] = 0; *unreadable = 1;
    bfmt(reason, sn, "cannot read boot_%c kernel version", t);
    return 1;
  }
  if (kmi_of(run, rk, sizeof rk)) {
    bfmt(reason, sn, "cannot parse the running kernel release '%.80s'", run);
    return 1;
  }
  if (strcmp(tk, rk)) { bfmt(reason, sn, "KMI %s != running %s", tk, rk); return 1; }
  if (gki_release(c, crel, sizeof crel)) bfmt(reason, sn, "KMI %s == running %s (boot_%c unreadable)", tk, rk, c);
  else if (strcmp(crel, rel)) bfmt(reason, sn, "GKI changed %s -> %s (same KMI %s as running %s)", crel, rel, tk, rk);
  else bfmt(reason, sn, "GKI unchanged %s, KMI %s == running %s", rel, tk, rk);
  return 0;
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
  if (rename(tmp, path)) { int e = errno; unlink(tmp); errno = e; return -1; }   /* no .tmp left on the ESP */
  return 0;
}

/* Remove slot s's systemd-boot entries and its stamp (ENOENT is fine); 0, or -1 if one could not be removed. */
static int drop_entries(char s, int *removed) {
  char p[PATH_MAX];
  int bad = 0;
  *removed = 0;
  for (int i = 0; i < 3; i++) {
    if (i == 2) snprintf(p, sizeof p, "%s/gb/slot_%c.stamp", mnt, s);
    else snprintf(p, sizeof p, "%s/loader/entries/gb-%c%s.conf", mnt, s, i ? "-nvtest" : "");
    if (!unlink(p)) { if (i < 2) (*removed)++; }               /* counts the entries, not the stamp */
    else if (errno != ENOENT) { logmsg("cannot remove %s: %s", p, strerror(errno)); bad = 1; }
  }
  return bad ? -1 : 0;
}

/* Build initrd-<s>.img for slot s (never the running slot cur); returns 0 if written or already current.
 * check_only: nothing is written; 0 = initrd-<s>.img and gb/slot_<s>.stamp match slot s's current init_boot/
 * vendor_boot and the ESP's inject.gz/bootconfig_extra, 1 = they do not (or slot s's images are unreadable or bad),
 * -1 = the ESP lacks its own inputs (no verdict). */
static int build_slot(char s, char cur, int check_only) {
  char ib[16], vb[16], path[PATH_MAX], img[PATH_MAX], injp[PATH_MAX], exp[PATH_MAX];
  if (s == cur) return -1;                                  /* the running slot's files are never rebuilt here */
  snprintf(ib, sizeof ib, "init_boot_%c", s); snprintf(vb, sizeof vb, "vendor_boot_%c", s);
  snprintf(injp, sizeof injp, "%s/gb/inject.gz", mnt); snprintf(exp, sizeof exp, "%s/gb/bootconfig_extra", mnt);
  snprintf(path, sizeof path, "%s/gb/slot_%c.stamp", mnt, s); snprintf(img, sizeof img, "%s/initrd-%c.img", mnt, s);
  unsigned char *ini = NULL, *ven = NULL, *inj = NULL, *extra = NULL, *stamp = NULL;
  size_t ilen, vlen, injlen, exlen, stlen, goff, gsz, roff, rsz, boff, bsz;
  int rc = -1;
  if (read_part(ib, &ini, &ilen) || read_part(vb, &ven, &vlen)) {
    logmsg("cannot read %s/%s", ib, vb); rc = check_only ? 1 : -1; goto out;
  }
  if (generic_ramdisk(ini, ilen, &goff, &gsz) || vendor_parts(ven, vlen, &roff, &rsz, &boff, &bsz)) {
    logmsg("slot %c: bad init_boot/vendor_boot image", s); rc = check_only ? 1 : -1; goto out;
  }
  if (read_all(injp, &inj, &injlen) || read_all(exp, &extra, &exlen)) {
    logmsg("ESP lacks /gb/inject.gz or /gb/bootconfig_extra (old build_esp?)"); goto out;
  }
  uint32_t h = 0;
  h = crc32_upd(h, inj, injlen); h = crc32_upd(h, extra, exlen);
  h = crc32_upd(h, ini + goff, gsz); h = crc32_upd(h, ven + roff, rsz); h = crc32_upd(h, ven + boff, bsz);
  char want[32]; snprintf(want, sizeof want, "%08x\n", h);
  int current = !read_all(path, &stamp, &stlen) && stlen == strlen(want) && !memcmp(stamp, want, stlen) &&
                !access(img, R_OK);
  if (check_only) { rc = !current; goto out; }
  if (current) { rc = 0; goto out; }                        /* already built from these images */
  /* Wave 3 safety review (MEDIUM): an older gb-<s>.conf (from an earlier switch) names initrd-<s>.img; remove it and
   * the stamp before the new initrd is written, so an interrupted or failed rebuild leaves no entry that boots slot
   * s's new system with its old ramdisk. The entries are written again only after the initrd and stamp are. */
  if (!dry_run) {
    int removed = 0;
    if (drop_entries(s, &removed)) goto out;
    if (removed) logmsg("slot %c: removed its older boot entries before rebuilding initrd-%c.img", s, s);
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

/* Whole-line match (finding 5 of the Wave 2 safety review): a substring test also took "default gb-b-nvtest" (or
 * "#default gb-b") for "default gb-b". mode 0: 1 when every "default" line (the key "default" as a whole word, then
 * blanks) names exactly gb-<s>, ignoring a CR and trailing blanks, and there is at least one; mode 1: 1 when any
 * "default" line names exactly gb-<s>. */
static int loader_default(const char *conf, char s, int any) {
  char want[8]; snprintf(want, sizeof want, "gb-%c", s);
  int seen = 0, right = 1, hit = 0;
  for (const char *p = conf; *p;) {
    const char *e = strchr(p, '\n');
    size_t n = e ? (size_t)(e - p) : strlen(p);
    if (n >= 7 && !strncmp(p, "default", 7) && (n == 7 || p[7] == ' ' || p[7] == '\t' || p[7] == '\r')) {
      size_t a = 7, z = n;
      while (a < z && (p[a] == ' ' || p[a] == '\t')) a++;
      while (z > a && (p[z - 1] == ' ' || p[z - 1] == '\t' || p[z - 1] == '\r')) z--;
      seen = 1;
      if (z - a != strlen(want) || strncmp(p + a, want, z - a)) right = 0;
      else hit = 1;
    }
    p += n + (e ? 1 : 0);
  }
  return any ? hit : seen && right;
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
  if (loader_default(conf, s, 0)) { free(conf); return 0; }
  size_t cap = 2 * ll + 64, rem = cap;
  char *outb = malloc(cap), *o = outb, *save = NULL;
  if (!outb) { free(conf); logmsg("loader.conf: out of memory"); return -1; }
  int done = 0, bad = 0;
  for (char *line = strtok_r(conf, "\n", &save); line && !bad; line = strtok_r(NULL, "\n", &save)) {
    size_t n = strlen(line);
    const char *cr = n && line[n - 1] == '\r' ? "\r" : "";
    if (!strncmp(line, "default", 7) && (line[7] == 0 || line[7] == ' ' || line[7] == '\t' || line[7] == '\r')) {
      bad = appendf(&o, &rem, "%s%s\n", want, cr);
      done = 1;
    }
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

/* stale = owner field not a number, no /proc/<pid>, or (r4, wave 2b review L2) our own pid: lock_esp() is only called
 * while we hold nothing, so a lock naming us is a put-back that landed after an earlier release of ours gave up */
static int owner_stale(const char *ol) {
  size_t n = strcspn(ol, " ");
  if (!n) return 1;
  for (size_t i = 0; i < n; i++) if (ol[i] < '0' || ol[i] > '9') return 1;
  char own[24];
  snprintf(own, sizeof own, "%d", (int)getpid());
  if (n == strlen(own) && !strncmp(ol, own, n)) return 1;
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

/* 1 = another live process is in the middle of a steal (INTERFACES 5.4 r3): a directory <lock>.stale.<pid> of a
 * live pid other than ours exists in the lock's directory (it may put a moved lock back at any moment) */
static int steal_busy(void) {
  char dir[PATH_MAX], base[PATH_MAX], pre[PATH_MAX + 8], p[PATH_MAX];
  snprintf(dir, sizeof dir, "%s", lock_path);
  snprintf(base, sizeof base, "%s", lock_path);
  snprintf(pre, sizeof pre, "%s.stale.", basename(base));
  const char *dn = dirname(dir);
  DIR *d = opendir(dn);
  if (!d) return 0;
  int busy = 0;
  size_t pl = strlen(pre);
  for (struct dirent *e; !busy && (e = readdir(d));) {
    if (strncmp(e->d_name, pre, pl)) continue;
    const char *q = e->d_name + pl;
    if (!*q || strspn(q, "0123456789") != strlen(q) || strlen(q) > 10 || atol(q) == (long)getpid()) continue;
    struct stat st;
    if (snprintf(p, sizeof p, "%s/%s", dn, e->d_name) >= (int)sizeof p || stat(p, &st) || !S_ISDIR(st.st_mode)) continue;
    snprintf(p, sizeof p, "%s/%s", proc_root, q);
    busy = !stat(p, &st) && S_ISDIR(st.st_mode);
  }
  closedir(d);
  return busy;
}

/* 0 = the stale lock (same inode, same owner line as seen) was removed: retry mkdir; -1 = not stolen. Anything else
 * that was moved is put back (r3): never over an entry in the way (RENAME_NOREPLACE, as toybox mv -T), (r4, wave 2b
 * review L1) retried every 50 ms for up to 5 s (PUTBACK_TRIES) while newcomers do not mkdir; a moved directory without
 * an owner file that cannot go back is removed (a holder that had not written its owner yet; its owner write fails or
 * lands elsewhere). A releaser waits LOCK_RELEASE_POLLS (7) polls, longer than this budget. */
static int steal_lock(const char *seen_owner, ino_t seen_ino) {
  char st[PATH_MAX], o2[256], s1[64], s2[64], op[PATH_MAX + 8];
  struct stat sb;
  snprintf(st, sizeof st, "%s.stale.%d", lock_path, (int)getpid());
  remove_lockdir(st);                                       /* a leftover of an earlier steal by this pid */
#ifdef GB_SLOTSYNC_TEST
  /* GB_SLOTSYNC_TEST_STEAL_RACE=<owner line|->: once, a live holder re-creates the lock right before our rename
   * ("-": without an owner yet); GB_SLOTSYNC_TEST_INTRUDE_POLLS=<n>: right after the rename a newcomer's empty
   * directory takes the lock path and backs off (is removed) after n put-back polls */
  static int raced;
  const char *sr = getenv("GB_SLOTSYNC_TEST_STEAL_RACE"), *ip = getenv("GB_SLOTSYNC_TEST_INTRUDE_POLLS");
  if (sr && *sr && !raced) {
    raced = 1;
    remove_lockdir(lock_path);
    mkdir(lock_path, 0700);
    if (strcmp(sr, "-")) {
      char rp[PATH_MAX];
      snprintf(rp, sizeof rp, "%s/owner", lock_path);
      FILE *f = fopen(rp, "w");
      if (f) { fprintf(f, "%s\n", sr); fclose(f); }
    }
  }
#endif
  if (rename_noreplace(lock_path, st)) return -1;           /* another waiter won, or the lock is gone */
#ifdef GB_SLOTSYNC_TEST
  int intrude = ip && *ip && raced == 1 ? atoi(ip) : -1;
  if (intrude >= 0) { raced = 2; mkdir(lock_path, 0700); test_polls = 0; }
#endif
  read_owner(st, o2, sizeof o2);
  if (!stat(st, &sb) && sb.st_ino == seen_ino && !strcmp(o2, seen_owner)) {
    logmsg("ESP lock: stealing a stale lock (owner '%s')", san(seen_owner, s1));
    remove_lockdir(st);
    return 0;
  }
  snprintf(op, sizeof op, "%s/owner", st);
  for (int n = 0;; n++) {
    if (!rename_noreplace(st, lock_path)) {
      logmsg("ESP lock: the lock changed while stealing it (owner '%s'): put back", san(o2, s2));
      return -1;
    }
    if (access(op, F_OK) && !rmdir(st)) {
      logmsg("ESP lock: the lock changed while stealing it: the moved directory had no owner and another is in "
             "place: removed");
      return -1;
    }
    if (n >= PUTBACK_TRIES) break;
    putback_sleep();
#ifdef GB_SLOTSYNC_TEST
    if (intrude >= 0 && test_polls == intrude) rmdir(lock_path);   /* the newcomer backed off */
#endif
  }
  logmsg("ESP lock: the lock changed while stealing it and could not be put back (left as %s)", basename(st));
  return -1;
}

/* <lock>/owner = our line, created exclusively (not write_file: its .tmp rename could leave a file that blocks rmdir).
 * 0 = written; 1 = not ours (r3): the directory vanished meanwhile (a stealer renamed it away) or an owner file is
 * already there (a lock put back in place of ours) - nothing is removed, wait again; -1 = error */
static int write_owner(void) {
  char op[PATH_MAX], s1[64];
  snprintf(lock_own, sizeof lock_own, "%d gb_slotsync %ld", (int)getpid(), uptime_s());
  snprintf(op, sizeof op, "%s/owner", lock_path);
  int fd = open(op, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
  if (fd < 0 && (errno == ENOENT || errno == EEXIST)) {
    if (errno == EEXIST) {
      char ol[256];
      read_owner(lock_path, ol, sizeof ol);
      logmsg("ESP lock: another lock took the place of ours before its owner was written (owner '%s'): waiting",
             san(ol, s1));
    }
    lock_own[0] = 0;
    return 1;
  }
  if (fd < 0) {                                             /* our empty directory: rmdir only, no owner to unlink */
    logmsg("ESP lock: cannot create %s: %s", op, strerror(errno));
    rmdir(lock_path);
    lock_own[0] = 0;
    return -1;
  }
  int ok = dprintf(fd, "%s\n", lock_own) == (int)strlen(lock_own) + 1;
  int err = errno;
  if (close(fd)) { ok = 0; err = errno; }
  if (ok) return 0;
  logmsg("ESP lock: cannot write %s: %s", op, strerror(err));
  remove_lockdir(lock_path);                                /* the owner file is ours (created O_EXCL just now) */
  lock_own[0] = 0;
  return -1;
}

/* 0 = we hold the lock (lock_own = our owner line); -1 = not taken (waited LOCK_WAIT_MAX s, or an error).
 * INTERFACES 5.4 r4: while a steal is in progress (steal_busy) we do not mkdir at all (r4, wave 2b review L1: our
 * directory would block the stealer's put-back) and back off; a steal that starts between the check and our mkdir
 * makes us rmdir our empty directory. Only consecutive back-off polls count (a poll that finds the lock held resets
 * the count); after LOCK_MISS_POLLS of them a leftover is ignored. Then the owner file is created exclusively. */
static int lock_esp(void) {
  int w = 0, miss = 0, backoff = 0;
  char ol[256], s1[64];
  lock_own[0] = 0;
#ifdef GB_SLOTSYNC_TEST
  const char *race = getenv("GB_SLOTSYNC_TEST_RACE_OWNER");  /* once: an owner file appears right after our mkdir */
#endif
  for (;;) {
    ol[0] = 0;                                              /* the 600 s message names only this poll's holder */
    if (backoff < LOCK_MISS_POLLS && steal_busy()) {
      if (!backoff++) logmsg("ESP lock: a steal is in progress: backing off");
    } else if (
#ifdef GB_SLOTSYNC_TEST
               (printf("test: mkdir lock\n"), fflush(stdout)),     /* counted by S8b (no mkdir while busy) */
#endif
               !mkdir(lock_path, 0700)) {
#ifdef GB_SLOTSYNC_TEST
      if (race && *race) {
        char rp[PATH_MAX];
        snprintf(rp, sizeof rp, "%s/owner", lock_path);
        FILE *f = fopen(rp, "w");
        if (f) { fprintf(f, "%s\n", race); fclose(f); }
        race = NULL;
      }
#endif
      if (backoff < LOCK_MISS_POLLS && steal_busy()) {
        rmdir(lock_path);                                   /* only an empty directory goes: ours */
        if (!backoff++) logmsg("ESP lock: a steal is in progress: backing off");
      } else {
        int r = write_owner();
        if (!r) break;
        if (r < 0) return -1;
      }
    } else if (errno != EEXIST) {
      logmsg("ESP lock: mkdir %s: %s", lock_path, strerror(errno));
      return -1;
    } else {
      struct stat sb;
      backoff = 0;                                          /* r4: only consecutive back-off polls count */
      int have_ino = !stat(lock_path, &sb);
      read_owner(lock_path, ol, sizeof ol);
      int stale = 0;
      if (ol[0]) { miss = 0; stale = owner_stale(ol); }
      else if (++miss >= LOCK_MISS_POLLS) stale = 1;
      if (stale && have_ino) {
        miss = 0;
        if (!steal_lock(ol, sb.st_ino)) continue;
      }
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

/* release only our own lock (a lock stolen from us belongs to someone else now). r3: while a steal is in progress
 * (r4: at most LOCK_RELEASE_POLLS polls, longer than a stealer's put-back budget) wait for the stealer to put a moved
 * lock back, then read the owner again. */
static void unlock_esp(void) {
  char ol[256], s1[64];
  if (!lock_own[0]) return;
  for (int n = 0;; n++) {
    read_owner(lock_path, ol, sizeof ol);
    if (!strcmp(ol, lock_own)) {
      remove_lockdir(lock_path);
#ifdef GB_SLOTSYNC_TEST
      printf("test: lock released\n");
#endif
      break;
    }
    if (n >= LOCK_RELEASE_POLLS || !steal_busy()) {
#ifdef GB_SLOTSYNC_TEST
      printf("test: release gave up after %d polls\n", n);
#endif
      logmsg("ESP lock: not ours any more at release (owner '%s'): left alone", san(ol, s1));
      break;
    }
    poll_sleep();
  }
  lock_own[0] = 0;
}

/* HOLD (kernel check failed): the initrd is built and gb-<s>.conf (+ gb-<s>-nvtest.conf) written, so "(slot S)" is in
 * the systemd-boot menu, but loader.conf is not touched: the default stays on the slot that booted. */
static int hold_entries(char s, char cur) {
  if (copy_entry(s, cur, "", 1)) return -1;
  if (copy_entry(s, cur, "-nvtest", 0))
    logmsg("slot %c: the [NVIDIA test] entry was not copied (the default entry is unaffected)", s);
  return 0;
}

/* The non-running slot's entries (wave 3 safety review, MEDIUM). Run once per boot and again whenever that slot's
 * GPT bootable state changes, with the ESP mounted and locked, only after the running slot is the default.
 * gb-<o>.conf (+ -nvtest) stays in the menu only while it matches slot o: o must be bootable by its GPT attributes
 * (update_engine marks the slot it writes unbootable first), and gb/slot_<o>.stamp + initrd-<o>.img must match o's
 * current init_boot/vendor_boot and the ESP's inject.gz/bootconfig_extra. Otherwise the entries (and the stamp) are
 * removed: they would boot o's newer system with an older ramdisk, or a half-written slot. The next update into o
 * writes them again. A loader.conf default naming gb-<o> is never left dangling: then nothing is removed.
 * A HOLD that is still in effect (o is the slot the GPT wants, its entries are current, its kernel check still
 * fails) is logged again, so every boot's kmsg carries the "HOLD slot" line. 0 = done; -1 = retry next tick. */
static int check_other(char o, char cur, const uint64_t *attr) {
  char e1[PATH_MAX], e2[PATH_MAX], lcp[PATH_MAX];
  snprintf(e1, sizeof e1, "%s/loader/entries/gb-%c.conf", mnt, o);
  snprintf(e2, sizeof e2, "%s/loader/entries/gb-%c-nvtest.conf", mnt, o);
  if (access(e1, F_OK) && access(e2, F_OK)) return 0;      /* nothing offered for slot o */
  const char *why = NULL;
  if (attr && !slot_bootable(attr[o - 'a'])) {
    why = "the slot is not bootable (GPT attributes: an update is being written into it, or it failed)";
  } else {
    int st = build_slot(o, cur, 1);
    if (st < 0) return -1;
    if (st > 0) why = "its initrd does not match the slot's current init_boot/vendor_boot and the ESP";
  }
  if (!why) {
    if (attr && wanted_slot(attr) == o - 'a') {
      char rel[128], reason[320];
      int unreadable = 0;
      if (kernel_check(o, cur, rel, sizeof rel, reason, sizeof reason, &unreadable))
        logmsg("HOLD slot %c: %s; default stays gb-%c", o, reason, cur);
    }
    return 0;
  }
  snprintf(lcp, sizeof lcp, "%s/loader/loader.conf", mnt);
  unsigned char *lc = NULL; size_t ll = 0;
  if (read_all(lcp, &lc, &ll)) { logmsg("cannot read %s", lcp); return -1; }
  char *conf = realloc(lc, ll + 1);
  if (!conf) { free(lc); return -1; }
  conf[ll] = 0;
  int dflt = loader_default(conf, o, 1);
  free(conf);
  if (dflt) { logmsg("slot %c: entries are stale (%s) but loader.conf names gb-%c: left alone", o, why, o); return 0; }
  if (dry_run) { logmsg("dry-run: would remove the boot entries of slot %c: %s", o, why); return 0; }
  int removed = 0;
  int bad = drop_entries(o, &removed);
  logmsg("slot %c: removed its boot entries (%d): %s", o, removed, why);
  return bad;
}

/* The GPT flag alone is not trusted: an update that was rolled back by booting the old slot leaves the other slot
 * marked active with nothing behind it. Switch only for an update that update_engine completed during THIS boot
 * (prefs/update-completed-on-boot-id == boot_id, i.e. "waiting to reboot"); otherwise keep the systemd-boot default
 * on the slot that actually booted -- which also makes a failed new slot fall back once the old one is picked.
 * Before a switch the kernel check (item 13, D-a) runs: a different KMI generation, or an unreadable kernel version,
 * holds the default (hold_entries); the decision is logged once per (slot, release) of boot_<slot>, and the
 * "System update held" notification is posted only after the held slot's initrd and entries are on the ESP.
 * With no switch pending, the non-running slot's entries are checked (check_other). */
static int sync_once(void) {
  static char settled;                                      /* slot whose entry is known to be the default */
  static char held[160];                                    /* "<slot> <release>" whose HOLD entries are written */
  static char decided[160];                                 /* "<slot> <release>" whose decision was logged */
  static char noted[160];                                   /* "<slot> <release>" whose HOLD notification was posted */
  static char other_done[8];                                /* "<slot><y|n|?>": last completed check_other */
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
  int pending = !read_line(ue_prefs, done_bid, sizeof done_bid) && bid[0] && !strcmp(bid, done_bid);
  uint64_t attr[2] = {0, 0};
  int have_attr = 0;
  if (pending) {
    if (vbmeta_attrs(attr)) { logmsg("cannot read vbmeta_a/b GPT attributes"); return -1; }
    have_attr = 1;
    int w = wanted_slot(attr);
    if (w >= 0) target = (char)('a' + w);
  }
  /* no switch pending: check the other slot's entries once per boot and whenever its bootable state changes */
  char other = cur == 'a' ? 'b' : 'a', okey[8] = "";
  int ocheck = 0;
  if (target == cur) {
    if (!have_attr) have_attr = !vbmeta_attrs(attr);
    snprintf(okey, sizeof okey, "%c%c", other, !have_attr ? '?' : slot_bootable(attr[other - 'a']) ? 'y' : 'n');
    ocheck = strcmp(okey, other_done) != 0;
  }
  if (target == settled && !ocheck) return 0;
  int hold = 0;
  char key[160] = "", text[240] = "";
  if (target != cur) {
    char rel[128], reason[320], run[128];
    int unreadable = 0;
    hold = kernel_check(target, cur, rel, sizeof rel, reason, sizeof reason, &unreadable);
    snprintf(key, sizeof key, "%c %s", target, rel[0] ? rel : "?");
    if (hold && !strcmp(key, held)) return 0;               /* this HOLD's entries are already on the ESP */
    running_release(run, sizeof run);
    if (hold) {
      char tk[96] = "", rk[96] = "";
      /* INTERFACES 12.2 N22 / N23: printable ASCII, the text <= 200 characters (the KMI strings are cut to 24) */
      if (unreadable || kmi_of(rel, tk, sizeof tk) || kmi_of(run, rk, sizeof rk))
        snprintf(text, sizeof text, "Kernel check failed: the kernel version of the update (slot %c) could not be "
                 "checked. Startup stays on slot %c; (slot %c) is in the boot menu.", target - 32, cur - 32,
                 target - 32);
      else
        snprintf(text, sizeof text, "Kernel mismatch: the update (slot %c) needs kernel %.24s, this system runs "
                 "%.24s. Startup stays on slot %c; (slot %c) is in the boot menu.", target - 32, tk, rk, cur - 32,
                 target - 32);
    }
    if (strcmp(key, decided)) {                             /* always logged, once per (slot, release) */
      snprintf(decided, sizeof decided, "%s", key);
      logmsg("update applied, slot %c active (attr a=%016llx b=%016llx), booted %c: preparing the ESP", target,
             (unsigned long long)attr[0], (unsigned long long)attr[1], cur);
      logmsg("slot %c: kernel of boot_%c '%s', running '%s'", target, target, rel[0] ? rel : "?", run);
      if (!hold) logmsg("slot %c: kernel check passed (%s): switching the default", target, reason);
      else logmsg("HOLD slot %c: %s; default stays gb-%c", target, reason, cur);
    }
  }
  if (lock_esp()) return -1;                                /* settled/held stay unset: the next tick retries */
  mkdir(mnt, 0700);
  if (do_mount() && errno != EBUSY) {
    logmsg("mount %s: %s", ESP_DEV, strerror(errno));
    unlock_esp();
    return -1;
  }
  int rc = 0, orc = -1;
  if (target != settled) {
    rc = target != cur ? build_slot(target, cur, 0) : 0;
    if (!rc) rc = hold ? hold_entries(target, cur) : select_entry(target, cur);
  }
  if (ocheck && !rc) orc = check_other(other, cur, have_attr ? attr : NULL);
  do_umount();
  unlock_esp();
  if (!rc && target != settled) {
    if (hold) snprintf(held, sizeof held, "%s", key);
    else settled = target;
  }
  /* the notification says "(slot X) is in the boot menu": post it only now that the entries are written */
  if (!rc && hold && strcmp(key, noted)) {
    snprintf(noted, sizeof noted, "%s", key);
    post_notify("gb_update", "System update held", text);
  }
  if (ocheck && !orc) snprintf(other_done, sizeof other_done, "%s", okey);
#ifdef GB_SLOTSYNC_TEST
  printf("test: sync_once rc=%d settled=%c held=%s\n", rc, settled ? settled : '-', held[0] ? held : "-");
#endif
  return rc;
}

int main(int argc, char **argv) {
  int watch = 0;
#ifdef GB_SLOTSYNC_TEST
  mnt = test_env("GB_SLOTSYNC_MNT", mnt);
  lock_path = test_env("GB_SLOTSYNC_LOCK", lock_path);
  proc_root = test_env("GB_SLOTSYNC_PROC", proc_root);
  byname = test_env("GB_SLOTSYNC_BYNAME", byname);
  ue_prefs = test_env("GB_SLOTSYNC_UE_PREFS", ue_prefs);
  notify_dir = test_env("GB_SLOTSYNC_NOTIFY", notify_dir);
  if (argc == 4 && !strcmp(argv[1], "--test-copy-entry")) {        /* --test-copy-entry S CUR */
    int rc = select_entry(argv[2][0], argv[3][0]);
    printf("test: select_entry rc=%d\n", rc);
    return rc ? 1 : 0;
  }
  if (argc == 4 && !strcmp(argv[1], "--test-wanted-slot")) {       /* --test-wanted-slot HEX_A HEX_B -> a, b or - */
    uint64_t at[2] = {strtoull(argv[2], NULL, 16), strtoull(argv[3], NULL, 16)};
    int w = wanted_slot(at);
    printf("%c\n", w < 0 ? '-' : 'a' + w);
    return 0;
  }
  if (argc == 3 && !strcmp(argv[1], "--test-kmi")) {               /* --test-kmi RELEASE -> KMI or "-" */
    char k[96];
    printf("%s\n", kmi_of(argv[2], k, sizeof k) ? "-" : k);
    return 0;
  }
  if (argc == 3 && !strcmp(argv[1], "--test-gki")) {               /* --test-gki S -> boot_<S> release or "-" */
    char r[128];
    printf("%s\n", gki_release(argv[2][0], r, sizeof r) ? "-" : r);
    return 0;
  }
  if (argc == 3 && !strcmp(argv[1], "--test-ticks")) {             /* --test-ticks N: N sync_once ticks, no sleep */
    int n = atoi(argv[2]), bad = 0;
    for (int i = 0; i < n; i++) if (sync_once()) bad++;
    printf("test: %d ticks, %d failed\n", n, bad);
    return bad ? 1 : 0;
  }
  if (argc == 2 && !strcmp(argv[1], "--test-lock")) {               /* hold GB_SLOTSYNC_TEST_HOLD_MS, then release */
    const char *orph = getenv("GB_SLOTSYNC_TEST_OWN_ORPHAN");      /* a late put-back of a lock naming our pid */
    if (orph && *orph) {
      char rp[PATH_MAX];
      mkdir(lock_path, 0700);
      snprintf(rp, sizeof rp, "%s/owner", lock_path);
      FILE *f = fopen(rp, "w");
      if (f) { fprintf(f, "%d gb_slotsync 7\n", (int)getpid()); fclose(f); }
    }
    if (lock_esp()) { printf("test: lock not taken after %d s\n", lock_waited); return 3; }
    long ms = strtol(test_env("GB_SLOTSYNC_TEST_HOLD_MS", "0"), NULL, 10);
    struct timespec ts = {ms / 1000, (ms % 1000) * 1000000L};
    nanosleep(&ts, NULL);
    const char *dp = getenv("GB_SLOTSYNC_TEST_DISPLACE");          /* a stealer moves our lock away right now */
    if (dp && *dp && rename(lock_path, dp)) printf("test: displace failed: %s\n", strerror(errno));
    test_polls = 0;
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
