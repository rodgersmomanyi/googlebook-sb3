/* gb_slotsync.c -- A/B slot follower for the Googlebook x86 systemd-boot ESP.
 *
 * Our ESP boots its own GKI kernel with an initrd made of [gzip'd gb cpio | vendor ramdisk | generic ramdisk |
 * bootconfig]. An OTA (update_engine) writes the inactive slot, and the android-desktop boot_control HAL then marks
 * that slot active ChromeOS-style in the GPT attributes of vbmeta_<x> (priority bits 48-51, tries 52-55,
 * successful 56). No firmware here reads those, so without this tool every update would boot the old slot and roll
 * itself back. gb_slotsync is the bootloader's slot choice: it picks the slot the way depthcharge does (highest
 * priority among slots that are successful or have tries left), rebuilds /initrd-<x>.img on the ESP from that slot's
 * init_boot/vendor_boot (+ androidboot.slot_suffix=_<x>), writes loader/entries/gb-<x>.conf and makes it the
 * systemd-boot default. The previous slot's entry stays in the menu as a fallback.
 *
 * ESP files used (written by build_esp.py): /gb/inject.gz (gb cpio, gzip, padded to 4 bytes), /gb/bootconfig_extra
 * (bootconfig lines common to both slots), /loader/entries/gb-<current>.conf (template), /loader/loader.conf.
 *
 * Usage: gb_slotsync [--watch SECONDS] [--dry-run]
 * Build: clang --target=x86_64-linux-android34 -O2 -Wall -o gb_slotsync gb_slotsync.c
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
#include <sys/system_properties.h>
#include <unistd.h>

#define ESP_DEV "/dev/block/by-name/EFI-SYSTEM"
#define MNT "/mnt/gb_slotsync"

static int dry_run;

static void logmsg(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void logmsg(const char *fmt, ...) {
  char buf[512];
  va_list ap;
  va_start(ap, fmt); vsnprintf(buf, sizeof buf, fmt, ap); va_end(ap);
  printf("gb_slotsync: %s\n", buf);
  fflush(stdout);
  FILE *k = fopen("/dev/kmsg", "w");
  if (k) { fprintf(k, "gb_slotsync: %s\n", buf); fclose(k); }
}

static int read_all(const char *path, unsigned char **out, size_t *len) {
  FILE *f = fopen(path, "rb");
  if (!f) return -1;
  size_t cap = 1 << 20, n = 0;
  unsigned char *b = malloc(cap);
  for (;;) {
    if (n == cap) { cap *= 2; b = realloc(b, cap); }
    size_t r = fread(b + n, 1, cap - n, f);
    if (!r) break;
    n += r;
  }
  fclose(f);
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
  if (esz < 128 || num > 1024) { close(fd); return -1; }
  unsigned char *ents = malloc((size_t)num * esz);
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
  while (*bsz && img[*boff + *bsz - 1] == 0) (*bsz)--;   /* strip zero padding */
  return o + bc <= len ? 0 : -1;
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
  char tmp[PATH_MAX]; snprintf(tmp, sizeof tmp, "%s.tmp", path);
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
  char ib[16], vb[16], path[PATH_MAX];
  snprintf(ib, sizeof ib, "init_boot_%c", s); snprintf(vb, sizeof vb, "vendor_boot_%c", s);
  unsigned char *ini = NULL, *ven = NULL, *inj = NULL, *extra = NULL, *stamp = NULL;
  size_t ilen, vlen, injlen, exlen, stlen, goff, gsz, roff, rsz, boff, bsz;
  int rc = -1;
  if (read_part(ib, &ini, &ilen) || read_part(vb, &ven, &vlen)) { logmsg("cannot read %s/%s", ib, vb); goto out; }
  if (generic_ramdisk(ini, ilen, &goff, &gsz) || vendor_parts(ven, vlen, &roff, &rsz, &boff, &bsz)) {
    logmsg("slot %c: bad init_boot/vendor_boot image", s); goto out;
  }
  if (read_all(MNT "/gb/inject.gz", &inj, &injlen) || read_all(MNT "/gb/bootconfig_extra", &extra, &exlen)) {
    logmsg("ESP lacks /gb/inject.gz or /gb/bootconfig_extra (old build_esp?)"); goto out;
  }
  uint32_t h = 0;
  h = crc32_upd(h, inj, injlen); h = crc32_upd(h, extra, exlen);
  h = crc32_upd(h, ini + goff, gsz); h = crc32_upd(h, ven + roff, rsz); h = crc32_upd(h, ven + boff, bsz);
  char want[32]; snprintf(want, sizeof want, "%08x\n", h);
  snprintf(path, sizeof path, MNT "/gb/slot_%c.stamp", s);
  char img[PATH_MAX]; snprintf(img, sizeof img, MNT "/initrd-%c.img", s);
  if (!read_all(path, &stamp, &stlen) && stlen == strlen(want) && !memcmp(stamp, want, stlen) && !access(img, R_OK)) {
    rc = 0; goto out;                                       /* already built from these images */
  }
  /* bootconfig = vendor bootconfig + slot suffix + common extra lines */
  char slotline[64]; int sl = snprintf(slotline, sizeof slotline, "androidboot.slot_suffix = \"_%c\"\n", s);
  size_t bclen = bsz + (bsz && ven[boff + bsz - 1] != '\n') + (size_t)sl + exlen;
  size_t total = pad(injlen, 4) + rsz + gsz;
  total = pad(total, 4) + bclen + 8 + 12;
  unsigned char *out = calloc(1, total), *p = out;
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

/* loader/entries/gb-<s>.conf from the current slot's entry, and loader.conf default -> gb-<s> */
static int select_entry(char s, char cur) {
  char src[PATH_MAX], dst[PATH_MAX];
  snprintf(src, sizeof src, MNT "/loader/entries/gb-%c.conf", cur);
  snprintf(dst, sizeof dst, MNT "/loader/entries/gb-%c.conf", s);
  if (s != cur || access(dst, R_OK)) {
    unsigned char *t; size_t tl;
    if (read_all(src, &t, &tl)) { logmsg("no template entry %s", src); return -1; }
    char *outb = malloc(tl + 256), *o = outb, *line = strtok((char *)realloc(t, tl + 1), "\n");
    ((char *)t)[tl] = 0;
    for (; line; line = strtok(NULL, "\n")) {
      if (!strncmp(line, "title", 5)) {
        char *q = strstr(line, "(slot ");
        if (q && q[6]) q[6] = (char)(s - 32);
        o += sprintf(o, "%s\n", line);
      }
      else if (!strncmp(line, "initrd", 6)) o += sprintf(o, "initrd  /initrd-%c.img\n", s);
      else o += sprintf(o, "%s\n", line);
    }
    int r = dry_run ? 0 : write_file(dst, outb, (size_t)(o - outb));
    free(outb); free(t);
    if (r) { logmsg("writing %s failed", dst); return -1; }
  }
  unsigned char *lc; size_t ll;
  if (read_all(MNT "/loader/loader.conf", &lc, &ll)) return -1;
  char *conf = realloc(lc, ll + 1); conf[ll] = 0;
  char want[32]; snprintf(want, sizeof want, "default gb-%c", s);
  if (strstr(conf, want)) { free(conf); return 0; }
  char *outb = malloc(ll + 64), *o = outb, *line = strtok(conf, "\n");
  int done = 0;
  for (; line; line = strtok(NULL, "\n")) {
    if (!strncmp(line, "default", 7)) { o += sprintf(o, "%s\n", want); done = 1; }
    else o += sprintf(o, "%s\n", line);
  }
  if (!done) o += sprintf(o, "%s\n", want);
  int r = dry_run ? 0 : write_file(MNT "/loader/loader.conf", outb, (size_t)(o - outb));
  free(outb); free(conf);
  if (!r) logmsg("systemd-boot default -> gb-%c", s);
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
  read_line("/proc/sys/kernel/random/boot_id", bid, sizeof bid);
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
  mkdir(MNT, 0700);
  if (mount(ESP_DEV, MNT, "vfat", MS_NOATIME | MS_NODEV | MS_NOSUID | MS_NOEXEC, "utf8") && errno != EBUSY) {
    logmsg("mount %s: %s", ESP_DEV, strerror(errno)); return -1;
  }
  int rc = target != cur ? build_slot(target) : 0;
  if (!rc) rc = select_entry(target, cur);
  sync();
  umount2(MNT, 0);
  if (!rc) settled = target;
  return rc;
}

int main(int argc, char **argv) {
  int watch = 0;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--dry-run")) dry_run = 1;
    else if (!strcmp(argv[i], "--watch") && i + 1 < argc) watch = atoi(argv[++i]);
  }
  if (!watch) return sync_once() ? 1 : 0;
  for (;;) { sync_once(); sleep((unsigned)watch); }
}
