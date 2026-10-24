/* gb_power.c -- Surface Book 3 performance base + power daemon for lapis in native mode (gb.hw=native).
 *
 * A. Combined battery. surface_battery registers BAT1 (clipboard) and BAT2 (keyboard base, present only while the base
 *    is attached); surface_charger registers ADP1 (Mains). The lapis health HAL (vendor.health-default =
 *    android.hardware.health-service.android-desktop, AOSP BatteryMonitor inside) reports ONE battery: the first
 *    Battery-type supply in /sys/class/power_supply whose "scope" is not "Device" (it scans once, at start). This
 *    daemon sums both packs energy-weighted (what Windows shows) into the test_power module's test_battery, mirrors
 *    ADP1 into test_ac (the HAL scans test_ac at start even if ADP1 probes late), keeps test_usb off, and keeps the
 *    HAL off BAT1/BAT2: kernel patch 0001 (surface_battery scope_device=1 -> scope "Device", skipped by the HAL) or,
 *    on a kernel without it, a bind mount of a file reading "Unknown" over BATn/type (the HAL then ignores BATn).
 *    The desktop HAL derives the level from charge_counter*100/charge_full and the "time to full" ETA from
 *    (charge_full - charge_counter) / average current_now, so with kernel patch 0002 (test_power charge_full params)
 *    real uAh/uA values are published (correct level AND ETA); without it charge_counter=-1 / current=0 (the HAL then
 *    uses capacity and reports ETA -1, so Android falls back to its own step-based estimate).
 * B. Detach (DTX, /dev/surface/dtx). Android never uses the dGPU, so a detach request is confirmed at once (after
 *    unbinding any driver from the dGPU); user abort, EC cancel, latch, base and mode events are handled like
 *    linux-surface's surface-dtx-daemon and logged. Publishes vendor.gb.dtx.{mode,base,latch}; inhibits the base
 *    keyboard + touchpad in studio mode (Windows does the same). SW_TABLET_MODE itself comes from surface_dtx.
 * C. dGPU (NVIDIA TU117 in the base): no Android driver exists, so keep every 10de function unbound with runtime PM
 *    'auto' (power/control) on it and on all its upstream bridges; the root port then runtime-suspends and ACPI _PR3
 *    cuts the dGPU's power (D3cold). Re-applied when the base is re-attached (PCI hot-add uevent).
 *    Opt-in nouveau (persist.vendor.gb.nouveau=1, loaded by gb_nouveau.rc/.sh; or -K <driver>): a function bound to
 *    that driver is left bound and its power/control alone (nouveau runpm=1 runtime-suspends it to D3cold itself); a
 *    detach request still unbinds it, and the detach is confirmed only once its driver link is gone and no NVIDIA
 *    function has a driver right before LATCH_CONFIRM (else canceled); while a detach is under way (confirmed / latch
 *    open) any pass unbinds it again (a late bind is never left on a base that can be pulled).
 *    Property unset/0 and no -K: every driver is unbound, exactly as without the opt-in.
 * D. Platform profile: sets /sys/firmware/acpi/platform_profile at start (persist.vendor.gb.perf_profile if valid,
 *    else balanced). "gb_power -p <profile>" sets one and exits (the rc property trigger runs that).
 *
 * Logs to /dev/kmsg as "gb_power: ...". Needs root (bind mount, sysfs writes). Single-threaded poll loop on the kernel
 * uevent netlink socket + the DTX fd.
 * Build: clang --target=x86_64-linux-android34 -O2 -Wall -Wextra -Werror -static -Iuapi -o gb_power gb_power.c
 * Options: -p <profile> set platform profile and exit | -o one battery pass, print, exit | -r <root> sysfs/dev prefix
 *          (tests) | -H no BATn/type hiding | -D no DTX | -G no dGPU handling (as gb.dgpu=keep: no unbind/runtime PM at
 *          start or hot-plug; a detach request still unbinds before confirming) | -I no studio-mode input inhibit | -v
 *          | -K <driver> leave dGPU functions bound to <driver> except at detach (overrides persist.vendor.gb.nouveau)
 * Built static (build.sh): it starts at early-init, so it does not depend on the dynamic linker / linkerconfig state.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/netlink.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>
#ifdef __ANDROID__
#include <sys/system_properties.h>
#endif

/* ------------------------------------------------------------------------------------------------ basics */
static int g_kmsg = -1, g_verbose;
static char g_root[256] = "";      /* path prefix for /sys, /dev, /proc (unit tests use a fake tree) */
static int g_hide = 1, g_dtx_on = 1, g_dgpu_on = 1, g_inhibit_on = 1, g_dgpu_unbind = 1;
/* dGPU driver left bound outside a detach (nouveau opt-in): "-K <driver>" (g_keep_cli), else "nouveau" while
 * persist.vendor.gb.nouveau=1 (re-read on every dGPU pass). Empty = default: every driver is unbound. */
static char g_keep_drv[48] = "";
static int g_keep_cli;

static void logm(int lvl, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void logm(int lvl, const char *fmt, ...) {
  char msg[700];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(msg, sizeof msg, fmt, ap);
  va_end(ap);
  if (g_kmsg >= 0) {
    char line[760];
    int n = snprintf(line, sizeof line, "<%d>gb_power: %s\n", lvl, msg);
    if (n > (int)sizeof line - 1) n = sizeof line - 1;
    if (write(g_kmsg, line, n) == n) return;
  }
  printf("gb_power: %s\n", msg);
  fflush(stdout);
}
#define LOGI(...) logm(6, __VA_ARGS__)
#define LOGW(...) logm(4, __VA_ARGS__)
#define LOGE(...) logm(3, __VA_ARGS__)
#define LOGD(...) do { if (g_verbose) logm(7, __VA_ARGS__); } while (0)

static long long now_ms(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec * 1000LL + t.tv_nsec / 1000000;
}

/* g_root + formatted path */
static char *rpath(char *buf, size_t n, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
static char *rpath(char *buf, size_t n, const char *fmt, ...) {
  size_t k = (size_t)snprintf(buf, n, "%s", g_root);
  if (k >= n) k = n - 1;
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf + k, n - k, fmt, ap);
  va_end(ap);
  return buf;
}

/* Reads a small text file, strips trailing whitespace. Returns length, -1 on error. */
static int read_text(const char *path, char *buf, size_t cap) {
  int fd = open(path, O_RDONLY | O_CLOEXEC), n = 0;
  if (fd < 0) { buf[0] = 0; return -1; }
  for (;;) {
    ssize_t r = read(fd, buf + n, cap - 1 - n);
    if (r < 0 && errno == EINTR) continue;
    if (r <= 0) { if (r < 0) n = -1; break; }
    n += (int)r;
    if ((size_t)n >= cap - 1) break;
  }
  close(fd);
  if (n < 0) { buf[0] = 0; return -1; }
  buf[n] = 0;
  while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == ' ' || buf[n - 1] == '\t' || buf[n - 1] == '\r')) buf[--n] = 0;
  return n;
}

static int write_text(const char *path, const char *val) {
  int fd = open(path, O_WRONLY | O_TRUNC | O_CLOEXEC);
  if (fd < 0) return -errno;
  size_t len = strlen(val);
  ssize_t w = write(fd, val, len);
  int e = w == (ssize_t)len ? 0 : (w < 0 ? -errno : -EIO);
  if (close(fd) && !e) e = -errno;
  return e;
}

/* basename of a symlink target ("" if none) */
static void link_base(const char *path, char *out, size_t n) {
  char t[PATH_MAX];
  ssize_t r = readlink(path, t, sizeof t - 1);
  out[0] = 0;
  if (r <= 0) return;
  t[r] = 0;
  const char *b = strrchr(t, '/');
  b = b ? b + 1 : t;
  size_t l = strlen(b);
  if (l >= n) l = n - 1;
  memcpy(out, b, l);
  out[l] = 0;
}

/* is word w one of the space-separated words in list? */
static int word_in(const char *list, const char *w) {
  size_t n = strlen(w);
  for (const char *p = list; *p;) {
    while (*p == ' ') p++;
    const char *e = p;
    while (*e && *e != ' ') e++;
    if ((size_t)(e - p) == n && n && !strncmp(p, w, n)) return 1;
    p = e;
  }
  return 0;
}

/* ------------------------------------------------------------------------------------------------ properties */
#ifdef __ANDROID__
static void prop_set(const char *k, const char *v) {
  if (g_root[0]) { LOGI("test: setprop %s %s", k, v); return; }
  if (__system_property_set(k, v)) LOGW("setprop %s=%s failed", k, v);
}
static void prop_get(const char *k, char *v /* >= PROP_VALUE_MAX */) {
  v[0] = 0;
  if (!g_root[0]) __system_property_get(k, v);
}
#else /* host build (unit tests): tiny in-memory property store */
static struct { char k[64], v[92]; } g_props[32];
static void prop_set(const char *k, const char *v) {
  int i;
  for (i = 0; i < 32 && g_props[i].k[0] && strcmp(g_props[i].k, k); i++) {}
  if (i == 32) return;
  snprintf(g_props[i].k, sizeof g_props[i].k, "%s", k);
  snprintf(g_props[i].v, sizeof g_props[i].v, "%s", v);
}
static void prop_get(const char *k, char *v) {
  v[0] = 0;
  for (int i = 0; i < 32 && g_props[i].k[0]; i++)
    if (!strcmp(g_props[i].k, k)) { strcpy(v, g_props[i].v); return; }
}
#endif

/* ------------------------------------------------------------------------------------------------ A. battery */
enum { T_OTHER = 0, T_BATTERY, T_MAINS, T_USB };
enum { S_UNKNOWN = 0, S_CHARGING, S_DISCHARGING, S_NOT_CHARGING, S_FULL };
enum { SC_NONE = 0, SC_UNKNOWN, SC_SYSTEM, SC_DEVICE };

struct psy {
  char name[40], driver[40], tech[16];
  int type, present, online, status, scope, capacity, cycle;
  long long e_now, e_full, e_design;   /* uWh */
  long long q_now, q_full, q_design;   /* uAh */
  long long p_now, i_now;              /* uW, uA (surface_battery reports magnitudes) */
  long long v_now, v_design;           /* uV */
};

static void psy_init(struct psy *p) {
  memset(p, 0, sizeof *p);
  p->present = p->online = p->capacity = p->cycle = -1;
  p->e_now = p->e_full = p->e_design = p->q_now = p->q_full = p->q_design = -1;
  p->p_now = p->i_now = p->v_now = p->v_design = -1;
}

static int status_from(const char *v) {
  if (!strcmp(v, "Charging")) return S_CHARGING;
  if (!strcmp(v, "Discharging")) return S_DISCHARGING;
  if (!strcmp(v, "Not charging")) return S_NOT_CHARGING;
  if (!strcmp(v, "Full")) return S_FULL;
  return S_UNKNOWN;
}
static const char *status_name(int s) {
  static const char *n[] = {"unknown", "charging", "discharging", "not-charging", "full"};
  return s >= 0 && s <= 4 ? n[s] : "unknown";
}

/* Parses the KEY=VALUE lines of a power_supply uevent file (or netlink message with '\n' separators). Missing
 * properties (the kernel skips -ENODATA/-ENODEV ones, e.g. everything but PRESENT on an absent battery) stay -1.
 * Returns the number of POWER_SUPPLY_* keys seen. */
static int psy_parse(const char *txt, struct psy *p) {
  int seen = 0;
  const char *l = txt;
  while (*l) {
    const char *e = strchr(l, '\n');
    size_t len = e ? (size_t)(e - l) : strlen(l);
    char line[256];
    if (len >= sizeof line) len = sizeof line - 1;
    memcpy(line, l, len);
    line[len] = 0;
    l += len + (e ? 1 : 0);
    if (strncmp(line, "POWER_SUPPLY_", 13)) continue;
    char *k = line + 13, *v = strchr(k, '=');
    if (!v) continue;
    *v++ = 0;
    seen++;
    char *end;
    long long x = strtoll(v, &end, 10);
    int isnum = end != v && *end == 0;
#define NUM(field) do { if (isnum) p->field = x; } while (0)
    if (!strcmp(k, "NAME")) snprintf(p->name, sizeof p->name, "%s", v);
    else if (!strcmp(k, "TYPE")) p->type = !strcmp(v, "Battery") ? T_BATTERY : !strcmp(v, "Mains") ? T_MAINS :
                                         !strncmp(v, "USB", 3) || !strcmp(v, "Wireless") ? T_USB : T_OTHER;
    else if (!strcmp(k, "STATUS")) p->status = status_from(v);
    else if (!strcmp(k, "SCOPE")) p->scope = !strcmp(v, "Device") ? SC_DEVICE : !strcmp(v, "System") ? SC_SYSTEM : SC_UNKNOWN;
    else if (!strcmp(k, "TECHNOLOGY")) snprintf(p->tech, sizeof p->tech, "%s", v);
    else if (!strcmp(k, "PRESENT")) { if (isnum) p->present = x != 0; }
    else if (!strcmp(k, "ONLINE")) { if (isnum) p->online = x != 0; }
    else if (!strcmp(k, "CAPACITY")) { if (isnum && x >= 0 && x <= 100) p->capacity = (int)x; }
    else if (!strcmp(k, "CYCLE_COUNT")) { if (isnum && x >= 0 && x < 100000) p->cycle = (int)x; }
    else if (!strcmp(k, "ENERGY_NOW")) NUM(e_now);
    else if (!strcmp(k, "ENERGY_FULL")) NUM(e_full);
    else if (!strcmp(k, "ENERGY_FULL_DESIGN")) NUM(e_design);
    else if (!strcmp(k, "CHARGE_NOW")) NUM(q_now);
    else if (!strcmp(k, "CHARGE_FULL")) NUM(q_full);
    else if (!strcmp(k, "CHARGE_FULL_DESIGN")) NUM(q_design);
    else if (!strcmp(k, "POWER_NOW")) NUM(p_now);
    else if (!strcmp(k, "CURRENT_NOW")) NUM(i_now);
    else if (!strcmp(k, "VOLTAGE_NOW")) NUM(v_now);
    else if (!strcmp(k, "VOLTAGE_MIN_DESIGN")) NUM(v_design);
#undef NUM
  }
  return seen;
}

/* A supply we fold into the combined battery: a Battery that is not test_power's own and is either driven by
 * surface_battery (scope "Device" there only means our patch is active) or not device-scoped (HID pen/peripheral
 * batteries are scope "Device" and must stay out). */
static int is_real_battery(const struct psy *p) {
  if (p->type != T_BATTERY || !strncmp(p->name, "test_", 5)) return 0;
  if (!strcmp(p->driver, "surface_battery")) return 1;
  return p->scope != SC_DEVICE;
}

#define MAXPSY 16
static struct { char name[40]; struct psy p; long long t; } g_psy_cache[MAXPSY];

static int psy_cmp(const void *a, const void *b) { return strcmp(((const struct psy *)a)->name, ((const struct psy *)b)->name); }

/* Reads every non-test_* supply's uevent file. A transient read error (SAM timeout -> -EIO) reuses the last good
 * parse for up to 60 s, so a single failed EC request does not make a pack vanish from the sum. */
static int psy_scan(struct psy *out, int max) {
  char dir[PATH_MAX], path[PATH_MAX + 64], buf[4096];
  DIR *d = opendir(rpath(dir, sizeof dir, "/sys/class/power_supply"));
  int n = 0;
  if (!d) return 0;
  struct dirent *e;
  while ((e = readdir(d)) && n < max) {
    if (e->d_name[0] == '.' || !strncmp(e->d_name, "test_", 5)) continue;
    struct psy p;
    psy_init(&p);
    snprintf(path, sizeof path, "%s/%s/uevent", dir, e->d_name);
    int ok = read_text(path, buf, sizeof buf) > 0 && psy_parse(buf, &p) > 0;
    int ci;
    for (ci = 0; ci < MAXPSY && g_psy_cache[ci].name[0] && strcmp(g_psy_cache[ci].name, e->d_name); ci++) {}
    if (!ok) {
      if (ci < MAXPSY && g_psy_cache[ci].name[0] && now_ms() - g_psy_cache[ci].t < 60000) {
        LOGD("%s: uevent read failed, using cached values", e->d_name);
        out[n++] = g_psy_cache[ci].p;
      }
      continue;
    }
    snprintf(p.name, sizeof p.name, "%s", e->d_name);
    snprintf(path, sizeof path, "%s/%s/device/driver", dir, e->d_name);
    link_base(path, p.driver, sizeof p.driver);
    if (ci < MAXPSY) {
      snprintf(g_psy_cache[ci].name, sizeof g_psy_cache[ci].name, "%s", p.name);
      g_psy_cache[ci].p = p;
      g_psy_cache[ci].t = now_ms();
    }
    out[n++] = p;
  }
  closedir(d);
  qsort(out, n, sizeof *out, psy_cmp);
  return n;
}

#define V_FALLBACK 11100000LL   /* uV; nominal 3S Li-ion pack when no voltage is reported */
#define E_FALLBACK 50000000LL   /* uWh; weight of a pack that reports only "capacity" */

struct combo {
  int nreal, nbat;           /* real battery supplies seen / present and usable */
  int present, capacity, status, ac, cycle, p_known;
  long long e_now, e_full, e_design, p_net;   /* uWh, uW (signed: + into the packs) */
  long long v_uv, v_conv, i_ua, q_now, q_full, q_design;
  const char *tech;          /* test_power technology key */
};

static const char *tech_key(const char *t) {
  if (!strcmp(t, "Li-poly")) return "LIPO";
  if (!strcmp(t, "LiFe") || !strcmp(t, "NiCd") || !strcmp(t, "NiMH") || !strcmp(t, "LiMn")) return t;
  return "LION";
}

/* The battery math: energy-weighted sum of all real packs (charge units are converted with the pack's design
 * voltage), one status, and uAh/uA values expressed at one virtual pack voltage v_conv, so that
 *   charge_counter*100/charge_full == E_now*100/E_full   (the HAL's level) and
 *   (charge_full-charge_counter)/current == (E_full-E_now)/P_net   (the HAL's time-to-full, in hours). */
static void combine(const struct psy *ps, int n, struct combo *c) {
  memset(c, 0, sizeof *c);
  c->status = S_DISCHARGING;
  c->capacity = -1;
  c->cycle = -1;
  c->tech = "LION";
  int ac = -1, nchg = 0, ndis = 0, nfull = 0, pk = 1;
  long long en = 0, ef = 0, ed = 0, vdw = 0, vnw = 0, vnw_w = 0, pnet = 0;
  for (int i = 0; i < n; i++) {
    const struct psy *p = &ps[i];
    if (p->type != T_BATTERY) {
      if (strncmp(p->name, "test_", 5) && p->online >= 0) ac = (ac > 0 || p->online > 0);
      continue;
    }
    if (!is_real_battery(p)) continue;
    c->nreal++;
    if (p->present == 0) continue;
    long long vd = p->v_design > 0 ? p->v_design : p->v_now > 0 ? p->v_now : V_FALLBACK;
    long long now, full, design;
    if (p->e_now >= 0 && (p->e_full > 0 || p->e_design > 0)) {
      now = p->e_now;
      full = p->e_full > 0 ? p->e_full : p->e_design;
      design = p->e_design > 0 ? p->e_design : full;
    } else if (p->q_now >= 0 && (p->q_full > 0 || p->q_design > 0)) {
      now = p->q_now * vd / 1000000;
      full = (p->q_full > 0 ? p->q_full : p->q_design) * vd / 1000000;
      design = (p->q_design > 0 ? p->q_design : p->q_full) * vd / 1000000;
    } else if (p->capacity >= 0) {
      full = design = E_FALLBACK;
      now = full * p->capacity / 100;
    } else {
      continue;                                   /* no usable data this round */
    }
    if (full <= 0) continue;
    if (now > full) now = full;
    if (now < 0) now = 0;
    c->nbat++;
    en += now; ef += full; ed += design;
    vdw += vd * (full / 1000);
    if (p->v_now > 0) { vnw += p->v_now * (full / 1000); vnw_w += full / 1000; }
    long long pw = p->p_now >= 0 ? p->p_now : (p->i_now >= 0 && p->v_now > 0) ? p->i_now * p->v_now / 1000000 : -1;
    switch (p->status) {
      case S_CHARGING: nchg++; if (pw < 0) pk = 0; else pnet += pw; break;
      case S_DISCHARGING: ndis++; if (pw < 0) pk = 0; else pnet -= pw; break;
      case S_FULL: nfull++; break;
      default: break;                             /* not charging / unknown: no net flow assumed */
    }
    if (p->cycle > c->cycle) c->cycle = p->cycle;
    if (c->nbat == 1) c->tech = tech_key(p->tech);
  }
  if (!c->nbat) {                                 /* no battery (yet): desktop-like, on mains */
    c->ac = ac < 0 ? 1 : ac;
    return;
  }
  /* no charger supply registered: infer. Any discharging pack means battery power (unplugged, the base charges the
   * clipboard: "Charging" + "Discharging" must not read as AC). */
  if (ac < 0) ac = ndis == 0 && (nchg > 0 || nfull > 0);
  c->present = 1;
  c->ac = ac;
  c->e_now = en; c->e_full = ef; c->e_design = ed;
  c->p_known = pk; c->p_net = pnet;
  c->v_conv = vdw / (ef / 1000 > 0 ? ef / 1000 : 1);
  if (c->v_conv <= 0) c->v_conv = V_FALLBACK;
  c->v_uv = vnw_w ? vnw / vnw_w : c->v_conv;
  c->q_now = en * 1000000 / c->v_conv;
  c->q_full = ef * 1000000 / c->v_conv;
  c->q_design = ed * 1000000 / c->v_conv;
  if (c->q_full <= 0) c->q_full = 1;
  /* level exactly as the desktop HAL computes it from counter/full: floor(counter*100/full + 0.05) */
  int cap = (int)((c->q_now * 2000 + c->q_full) / (20 * c->q_full));
  if (cap > 100) cap = 100;
  if (cap < 0) cap = 0;
  int st;
  if (!ac) st = S_DISCHARGING;
  else if (nchg > 0 && (!pk || pnet > 0)) st = S_CHARGING;
  else if (nfull == c->nbat || cap >= 100) st = S_FULL;
  else st = S_NOT_CHARGING;                       /* plugged in: charge limit reached, or load >= adapter */
  if (st == S_FULL) { cap = 100; c->q_now = c->q_full; }
  long long i = pk ? pnet * 1000000 / c->v_conv : 0;
  /* the HAL turns "charging with negative current" into discharging (and negates positive discharge current) */
  if ((st == S_CHARGING && i < 0) || (st == S_DISCHARGING && i > 0)) i = 0;
  c->i_ua = i;
  c->capacity = cap;
  c->status = st;
}

/* test_power module parameters */
#define TP "/sys/module/test_power/parameters/"
enum { TP_PRESENT, TP_TECH, TP_HEALTH, TP_VOLTAGE, TP_FULL_DESIGN, TP_FULL, TP_COUNTER, TP_CURRENT, TP_TEMP,
       TP_CAPACITY, TP_STATUS, TP_AC, TP_USB, TP_N };
static const char *const tp_param[TP_N] = {
  "battery_present", "battery_technology", "battery_health", "battery_voltage", "battery_charge_full_design",
  "battery_charge_full", "battery_charge_counter", "battery_current", "battery_temp", "battery_capacity",
  "battery_status", "ac_online", "usb_online"};
static struct { int ok, full, temp, writes; char last[TP_N][24]; } g_tp;

static int tp_put(int idx, const char *val) {
  char path[PATH_MAX];
  if (!strcmp(g_tp.last[idx], val)) return 0;
  int r = write_text(rpath(path, sizeof path, TP "%s", tp_param[idx]), val);
  if (r) { LOGW("write %s=%s failed: %s", tp_param[idx], val, strerror(-r)); return -1; }
  snprintf(g_tp.last[idx], sizeof g_tp.last[idx], "%s", val);
  g_tp.writes++;
  return 1;
}
static int tp_putll(int idx, long long v) { char b[24]; snprintf(b, sizeof b, "%lld", v); return tp_put(idx, b); }

static int tp_probe(void) {
  char path[PATH_MAX];
  if (access(rpath(path, sizeof path, TP "battery_capacity"), W_OK)) return 0;
  if (!g_tp.ok) {
    g_tp.ok = 1;
    g_tp.full = !access(rpath(path, sizeof path, TP "battery_charge_full"), W_OK) &&
                !access(rpath(path, sizeof path, TP "battery_charge_full_design"), W_OK);
    g_tp.temp = !access(rpath(path, sizeof path, TP "battery_temp"), W_OK);
    memset(g_tp.last, 0, sizeof g_tp.last);
    LOGI("test_power found (%s); combined battery -> test_battery", g_tp.full ?
         "charge_full params: real uAh/uA, HAL level + time-to-full from them" :
         "no charge_full param (kernel patch 0002 missing): capacity only, Android estimates the ETA itself");
    /* Defaults until real batteries show up: no battery, on mains, no USB charger, no bogus counter/current. */
    tp_put(TP_USB, "off");
    tp_put(TP_HEALTH, "good");
    tp_put(TP_TECH, "LION");
    tp_put(TP_COUNTER, "-1");
    tp_put(TP_CURRENT, "0");
    if (g_tp.temp) tp_put(TP_TEMP, "250");       /* no battery thermistor reading available: 25.0 C */
    tp_put(TP_PRESENT, "false");
    tp_put(TP_AC, "on");
  }
  return 1;
}

static void publish(const struct combo *c) {
  if (!g_tp.ok) return;
  if (!c->nbat) {
    tp_put(TP_COUNTER, "-1");
    tp_put(TP_CURRENT, "0");
    tp_put(TP_PRESENT, "false");
    tp_put(TP_AC, c->ac ? "on" : "off");
    return;
  }
  tp_put(TP_TECH, c->tech);
  tp_put(TP_HEALTH, "good");
  tp_putll(TP_VOLTAGE, c->v_uv / 1000 * 1000);    /* test_power hands it out raw as voltage_now: uV */
  if (g_tp.full) {
    tp_putll(TP_FULL_DESIGN, c->q_design);
    tp_putll(TP_FULL, c->q_full);
    tp_putll(TP_COUNTER, c->q_now);
    tp_putll(TP_CURRENT, c->i_ua / 1000 * 1000);
  } else {
    tp_put(TP_COUNTER, "-1");                     /* <=0: the HAL keeps "capacity" (counter*100/100 would be wrong) */
    tp_put(TP_CURRENT, "0");                      /* <1 mA: the HAL reports no ETA instead of a bogus one */
  }
  tp_putll(TP_CAPACITY, c->capacity);
  tp_put(TP_STATUS, status_name(c->status));
  tp_put(TP_PRESENT, "true");
  tp_put(TP_AC, c->ac ? "on" : "off");
}

/* Kernel without patch 0001: hide BATn from the HAL's type scan by bind-mounting "Unknown" over BATn/type. */
static int g_hidden;
static void hide_batteries(const struct psy *ps, int n) {
  char src[PATH_MAX], tpath[PATH_MAX + 64], real[PATH_MAX], cur[32];
  for (int i = 0; i < n; i++) {
    const struct psy *p = &ps[i];
    if (!is_real_battery(p) || p->scope == SC_DEVICE) continue;
    rpath(tpath, sizeof tpath, "/sys/class/power_supply/%s/type", p->name);
    if (read_text(tpath, cur, sizeof cur) < 0 || !strcmp(cur, "Unknown")) continue;   /* gone / already hidden */
    if (!g_hide) { LOGW("%s has no scope=Device and hiding is off: the health HAL may report it instead of the "
                        "combined battery", p->name); continue; }
    if (g_root[0]) {                              /* unit tests: emulate the bind mount */
      write_text(tpath, "Unknown\n");
      g_hidden++;
      LOGI("test: hid %s", p->name);
      continue;
    }
    rpath(src, sizeof src, "/dev/gb_power");
    mkdir(src, 0755);
    rpath(src, sizeof src, "/dev/gb_power/type_unknown");
    if (access(src, R_OK)) {
      int fd = open(src, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0444);
      if (fd >= 0) { if (write(fd, "Unknown\n", 8) != 8) LOGW("write %s failed", src); close(fd); }
    }
    if (!realpath(tpath, real)) continue;
    if (mount(src, real, NULL, MS_BIND, NULL)) {
      LOGE("%s: bind mount over %s failed: %s (the health HAL may pick %s)", p->name, real, strerror(errno), p->name);
      continue;
    }
    g_hidden++;
    LOGW("%s: kernel lacks surface_battery scope_device (patch 0001); hid it from the health HAL "
         "(bind-mounted 'Unknown' over %s)", p->name, real);
  }
}

/* surface_battery's scope_device (patch 0001, set to 1 in modules.load) only makes sense while test_battery carries
 * the combined battery. Without test_power, hand the HAL a real pack rather than none (it reads scope when it scans,
 * so this must happen before it starts; we run from early-init). */
static void scope_param(int want) {
  char p[PATH_MAX], v[8];
  if (read_text(rpath(p, sizeof p, "/sys/module/surface_battery/parameters/scope_device"), v, sizeof v) < 0) return;
  if ((v[0] == 'Y' || v[0] == '1') == want) return;
  if (!write_text(p, want ? "Y" : "N"))
    LOGW("surface_battery scope_device -> %s (%s)", want ? "Y" : "N",
         want ? "test_power present: combined battery" : "test_power missing: the HAL gets a real pack");
}

static char g_bat_last[256];
static void describe(const struct psy *ps, int n, const struct combo *c, char *out, size_t cap) {
  int k = snprintf(out, cap, "%d%% %s ac=%d", c->capacity, status_name(c->status), c->ac);
  for (int i = 0; i < n && k < (int)cap; i++) {
    const struct psy *p = &ps[i];
    if (!is_real_battery(p)) continue;
    long long full = p->e_full > 0 ? p->e_full : p->q_full;
    long long now = p->e_full > 0 ? p->e_now : p->q_now;
    k += snprintf(out + k, cap - k, " | %s%s %lld%% %s", p->name, p->present == 0 ? " absent" : "",
                  full > 0 && now >= 0 ? now * 100 / full : (long long)p->capacity, status_name(p->status));
  }
}

static void battery_update(int force_log) {
  struct psy ps[MAXPSY];
  struct combo c;
  int n = psy_scan(ps, MAXPSY);
  if (g_tp.ok) {                                  /* only hide the real packs when the combined one exists */
    scope_param(1);
    hide_batteries(ps, n);
  } else {
    scope_param(0);
  }
  combine(ps, n, &c);
  publish(&c);
  char d[256];
  describe(ps, n, &c, d, sizeof d);
  /* log on capacity/status/ac/pack changes (the per-pack numbers change too often to log every one) */
  char key[64];
  snprintf(key, sizeof key, "%d %d %d %d", c.capacity, c.status, c.ac, c.nbat);
  if (force_log || strcmp(key, g_bat_last)) {
    if (c.nbat)
      LOGI("battery %s | %.1f/%.1f Wh, net %+.1f W%s, %.2f V", d, c.e_now / 1e6, c.e_full / 1e6, c.p_net / 1e6,
           c.p_known ? "" : " (rate unknown)", c.v_uv / 1e6);
    else
      LOGI("no battery present (%d battery supplies seen) ac=%d", c.nreal, c.ac);
    snprintf(g_bat_last, sizeof g_bat_last, "%s", key);
  }
}

/* ------------------------------------------------------------------------------------------------ C. dGPU */
static const char *hex4(const char *v) { return !strncasecmp(v, "0x", 2) ? v + 2 : v; }

static int is_bdf(const char *s) {
  unsigned a, b, c, d;
  int k = 0;
  return strlen(s) == 12 && sscanf(s, "%4x:%2x:%2x.%1x%n", &a, &b, &c, &d, &k) == 4 && k == 12;
}

/* power/control on -> auto. Returns 1 if changed. */
static int rpm_auto(const char *devdir, const char *what) {
  char p[PATH_MAX + 32], v[16];
  snprintf(p, sizeof p, "%s/power/control", devdir);
  if (read_text(p, v, sizeof v) < 0 || !strcmp(v, "auto")) return 0;
  int r = write_text(p, "auto");
  if (r) { LOGW("%s: runtime PM auto failed: %s", what, strerror(-r)); return 0; }
  LOGI("%s: runtime PM on -> auto", what);
  return 1;
}

static void pci_state(const char *devdir, char *out, size_t n) {
  char p[PATH_MAX + 32], rs[24], ps[16];
  snprintf(p, sizeof p, "%s/power/runtime_status", devdir);
  if (read_text(p, rs, sizeof rs) < 0) strcpy(rs, "?");
  snprintf(p, sizeof p, "%s/power_state", devdir);  /* pci_dev->current_state; reading it does not wake the GPU */
  if (read_text(p, ps, sizeof ps) < 0) strcpy(ps, "?");
  snprintf(out, n, "runtime %s, %s", rs, ps);
}

/* nouveau opt-in state for the next pass: -K wins; else persist.vendor.gb.nouveau=1 -> "nouveau" (the value
 * gb_nouveau.rc triggers on), anything else -> "" (default). Logs only when it changes, so the default stays silent. */
static void keep_drv_update(void) {
  if (g_keep_cli) return;
  char v[100], want[sizeof g_keep_drv];
  prop_get("persist.vendor.gb.nouveau", v);
  snprintf(want, sizeof want, "%s", !strcmp(v, "1") ? "nouveau" : "");
  if (!strcmp(want, g_keep_drv)) return;
  if (want[0]) LOGI("dGPU: persist.vendor.gb.nouveau=1: functions bound to %s stay bound (it runs their runtime PM) "
                    "and are unbound only before a detach is confirmed", want);
  else LOGI("dGPU: nouveau opt-in off: every driver on the dGPU is unbound again");
  snprintf(g_keep_drv, sizeof g_keep_drv, "%s", want);
}

/* Unbinds drivers from every NVIDIA function and (pm) enables runtime PM on it and its bridges. Never touches config
 * space (vendor/device/class/power_state come from sysfs caches). Returns the number of functions still bound.
 * Opt-in (g_keep_drv set): a function bound to g_keep_drv is left bound with its power/control untouched, except in
 * a detach pass (the request's own, or any pass while a detach is under way: dgpu_scan), which unbinds every driver
 * and then counts a function as bound until its driver link is gone. */
static int g_dgpu_seen, g_dgpu_kept;              /* last pass: NVIDIA functions seen / found bound to g_keep_drv */
static int (*dgpu_unbind_write)(const char *path, const char *bdf) = write_text;   /* unit tests emulate the kernel */
static int dgpu_pass(const char *why, int report, int pm, int detach) {
  char dir[PATH_MAX], dev[PATH_MAX + 32], p[PATH_MAX + 64], v[32], cls[16], did[16], drv[48], real[PATH_MAX];
  keep_drv_update();
  g_dgpu_kept = 0;
  DIR *d = opendir(rpath(dir, sizeof dir, "/sys/bus/pci/devices"));
  int found = 0, bound = 0, kept = 0;
  if (!d) return 0;
  struct dirent *e;
  while ((e = readdir(d))) {
    if (e->d_name[0] == '.') continue;
    snprintf(dev, sizeof dev, "%s/%s", dir, e->d_name);
    snprintf(p, sizeof p, "%s/vendor", dev);
    if (read_text(p, v, sizeof v) < 0 || strcasecmp(v, "0x10de")) continue;
    found++;
    snprintf(p, sizeof p, "%s/class", dev);
    if (read_text(p, cls, sizeof cls) < 0) strcpy(cls, "?");
    snprintf(p, sizeof p, "%s/device", dev);
    if (read_text(p, did, sizeof did) < 0) strcpy(did, "?");
    snprintf(p, sizeof p, "%s/driver", dev);
    link_base(p, drv, sizeof drv);
    int keep = drv[0] && g_keep_drv[0] && !strcmp(drv, g_keep_drv);   /* always 0 without the opt-in */
    int left = keep && !detach;                   /* stays bound after this pass */
    kept += keep;
    if (left) {
      if (report) LOGI("dGPU %s (10de:%s class %s): left bound to %s (opt-in; unbound before a detach)", e->d_name,
                       hex4(did), cls, drv);
    } else if (drv[0]) {
      int r = -EPERM;
      char still[48] = "";
      if (g_dgpu_unbind) {
        snprintf(p, sizeof p, "%s/driver/unbind", dev);
        r = dgpu_unbind_write(p, e->d_name);
        if (!r && detach && g_keep_drv[0]) {      /* opt-in: the base is never pulled while a driver holds the dGPU */
          snprintf(p, sizeof p, "%s/driver", dev);
          link_base(p, still, sizeof still);
        }
      }
      if (still[0]) {
        bound++;
        LOGE("dGPU %s: unbind from %s returned, but the function is still bound to %s", e->d_name, drv, still);
      } else if (!r && keep) {
        LOGI("dGPU %s (10de:%s class %s): unbound from %s before the detach (opt-in driver)", e->d_name, hex4(did), cls,
             drv);
      } else if (!r) {
        LOGI("dGPU %s (10de:%s class %s): unbound from %s (no Android user of the NVIDIA GPU)", e->d_name, hex4(did),
             cls, drv);
      } else {
        bound++;
        LOGW("dGPU %s: still bound to %s (%s)", e->d_name, drv, g_dgpu_unbind ? strerror(-r) : "unbind off");
      }
    }
    if (pm) {
      snprintf(p, sizeof p, "%s/d3cold_allowed", dev);
      if (read_text(p, v, sizeof v) >= 0 && !strcmp(v, "0")) write_text(p, "1");
      snprintf(p, sizeof p, "dGPU %s", e->d_name);
      if (!left) rpm_auto(dev, p);                /* a kept driver owns power/control (nouveau runpm: pm_runtime_allow) */
    }
    /* every upstream PCI bridge (the root port 00:1d.4 on the SB3): runtime PM auto, so it can drop to D3cold */
    char top[PATH_MAX] = "";
    if (realpath(dev, real)) {
      char *s;
      while ((s = strrchr(real, '/')) && s != real) {
        *s = 0;
        const char *b = strrchr(real, '/');
        if (!b || !is_bdf(b + 1)) break;
        snprintf(p, sizeof p, "bridge %s (above dGPU)", b + 1);
        if (pm) rpm_auto(real, p);
        snprintf(top, sizeof top, "%s", real);
      }
    }
    if (report) {
      char s1[64], s2[64] = "-";
      pci_state(dev, s1, sizeof s1);
      if (top[0]) pci_state(top, s2, sizeof s2);
      LOGI("dGPU %s 10de:%s class %s driver=%s: %s | root port %s: %s", e->d_name, hex4(did), cls,
           drv[0] ? drv : "none", s1, top[0] ? strrchr(top, '/') + 1 : "-", s2);
      if (strstr(s1, "D0") && !drv[0])
        LOGW("dGPU %s still in D0: root port not runtime-suspended (check its power/control, pcie_port_pm, and that "
             "no other function below it is active)", e->d_name);
      else if (strstr(s1, "D0") && left)
        LOGW("dGPU %s still in D0 on %s: check that it was loaded with runpm=1, its power/control is auto, and that "
             "nothing keeps its render node busy", e->d_name, drv);
    }
  }
  closedir(d);
  if (report && !found) LOGI("no NVIDIA function on PCI (%s): base detached or dGPU off", why);
  else if (found) LOGD("dGPU scan (%s): %d NVIDIA functions, %d still bound", why, found, bound);
  g_dgpu_seen = found;
  g_dgpu_kept = kept;
  return bound;
}

/* NVIDIA functions that have a driver right now (read-only): the opt-in detach's last look before LATCH_CONFIRM, as
 * its unbind pass can block for seconds (nouveau resume + teardown) while another probe binds a function. */
static int dgpu_bound_now(void) {
  char dir[PATH_MAX], p[PATH_MAX + 300], v[32], drv[48];
  DIR *d = opendir(rpath(dir, sizeof dir, "/sys/bus/pci/devices"));
  int n = 0;
  if (!d) return 0;
  struct dirent *e;
  while ((e = readdir(d))) {
    if (e->d_name[0] == '.') continue;
    snprintf(p, sizeof p, "%s/%s/vendor", dir, e->d_name);
    if (read_text(p, v, sizeof v) < 0 || strcasecmp(v, "0x10de")) continue;
    snprintf(p, sizeof p, "%s/%s/driver", dir, e->d_name);
    link_base(p, drv, sizeof drv);
    if (drv[0]) { n++; LOGE("dGPU %s: bound to %s after the unbind pass", e->d_name, drv); }
  }
  closedir(d);
  return n;
}

/* ------------------------------------------------------------------------------------------------ D. profile */
static int profile_apply(const char *want) {
  char p[PATH_MAX], choices[160], cur[48];
  if (read_text(rpath(p, sizeof p, "/sys/firmware/acpi/platform_profile_choices"), choices, sizeof choices) <= 0)
    return -1;
  if (!want || !*want) want = "balanced";
  if (!word_in(choices, want)) {
    LOGW("platform profile '%s' not offered (choices: %s); using balanced", want, choices);
    want = "balanced";
    if (!word_in(choices, want)) return -2;
  }
  rpath(p, sizeof p, "/sys/firmware/acpi/platform_profile");
  read_text(p, cur, sizeof cur);
  if (!strcmp(cur, want)) { LOGI("platform profile: %s (choices: %s)", cur, choices); return 0; }
  int r = write_text(p, want);
  if (r) { LOGE("platform profile %s -> %s failed: %s", cur, want, strerror(-r)); return -3; }
  LOGI("platform profile: %s -> %s (choices: %s)", cur[0] ? cur : "?", want, choices);
  return 0;
}

/* ------------------------------------------------------------------------------------------------ B. DTX */
/* uAPI: include/uapi/linux/surface_aggregator/dtx.h. build.sh compiles against a verbatim copy of the kernel tree's header
 * (uapi/ next to this file, -Iuapi, ahead of the NDK sysroot's identical copy). The literal values below pin the ABI, so a
 * header that differs from what surface_dtx.c implements fails the build instead of producing wrong ioctls. */
#include <linux/surface_aggregator/dtx.h>
_Static_assert(SDTX_IOCTL_EVENTS_ENABLE == 0xa521 && SDTX_IOCTL_LATCH_CONFIRM == 0xa526 &&
               SDTX_IOCTL_LATCH_HEARTBEAT == 0xa527 && SDTX_IOCTL_LATCH_CANCEL == 0xa528, "DTX ioctl numbers");
_Static_assert(SDTX_IOCTL_GET_BASE_INFO == 0x8004a529 && SDTX_IOCTL_GET_DEVICE_MODE == 0x8002a52a &&
               SDTX_IOCTL_GET_LATCH_STATUS == 0x8002a52b, "DTX read ioctl numbers/sizes");
_Static_assert(sizeof(struct sdtx_base_info) == 4 && sizeof(struct sdtx_event) == 4, "DTX struct layout");
_Static_assert(SDTX_EVENT_REQUEST == 1 && SDTX_EVENT_CANCEL == 2 && SDTX_EVENT_BASE_CONNECTION == 3 &&
               SDTX_EVENT_LATCH_STATUS == 4 && SDTX_EVENT_DEVICE_MODE == 5, "DTX event codes");
_Static_assert(SDTX_BASE_DETACHED == 0 && SDTX_BASE_ATTACHED == 1 && SDTX_DETACH_NOT_FEASIBLE == 0x1001 &&
               SDTX_DETACH_TIMEDOUT == 0x1002 && SDTX_LATCH_CLOSED == 0 && SDTX_LATCH_OPENED == 1 &&
               SDTX_ERR_FAILED_TO_OPEN == 0x2001 && SDTX_ERR_FAILED_TO_REMAIN_OPEN == 0x2002 &&
               SDTX_ERR_FAILED_TO_CLOSE == 0x2003 && SDTX_CATEGORY_HARDWARE_ERROR == 0x2000, "DTX status codes");
_Static_assert(SDTX_DEVICE_MODE_TABLET == 0 && SDTX_DEVICE_MODE_LAPTOP == 1 && SDTX_DEVICE_MODE_STUDIO == 2, "modes");
#define SDTX_CAT(v) SDTX_CATEGORY(v)
#define SDTX_CAT_HW SDTX_CATEGORY_HARDWARE_ERROR
enum { MODE_TABLET = SDTX_DEVICE_MODE_TABLET, MODE_LAPTOP = SDTX_DEVICE_MODE_LAPTOP, MODE_STUDIO = SDTX_DEVICE_MODE_STUDIO };
enum { EC_READY, EC_IN_PROGRESS, EC_CONFIRMED };
static int g_race_wait_ms = 2000;                 /* confirm/abort race re-check delay (unit tests shorten it) */

/* Latch value -> SDTX_LATCH_CLOSED / SDTX_LATCH_OPENED, inferring the mechanical state from a hardware error the way
 * surface-dtx-daemon does; -1 for unknown values (ignored, like the daemon). */
static int latch_state(int v) {
  switch (v) {
    case SDTX_LATCH_CLOSED: case SDTX_ERR_FAILED_TO_OPEN: case SDTX_ERR_FAILED_TO_REMAIN_OPEN: return SDTX_LATCH_CLOSED;
    case SDTX_LATCH_OPENED: case SDTX_ERR_FAILED_TO_CLOSE: return SDTX_LATCH_OPENED;
    default: return -1;
  }
}
static int base_known(int v) { return v == SDTX_BASE_DETACHED || v == SDTX_BASE_ATTACHED || v == SDTX_DETACH_NOT_FEASIBLE; }

static int sys_ioctl(int fd, unsigned long req, void *arg) { return ioctl(fd, req, arg) < 0 ? -errno : 0; }
static int (*dtx_ioctl)(int fd, unsigned long req, void *arg) = sys_ioctl;   /* unit tests swap in a mock */

static struct {
  int fd, base, latch, mode, ec, inhibited;
  unsigned char buf[1024];
  size_t blen;
  long long requery_at, retry_at;
} g_dtx = {.fd = -1, .base = SDTX_BASE_ATTACHED, .mode = MODE_LAPTOP, .inhibited = -1};
static long long g_dgpu_due = -1, g_dgpu_report_due = -1;

/* Start/hot-plug/report passes. While a detach is under way (requested, confirmed or the latch open) they get detach
 * semantics: with the opt-in, a function the kept driver binds meanwhile (gb_nouveau.sh's insmod or a re-probe racing
 * the button) is unbound again instead of being left bound while the base can be pulled. Without the opt-in the flag
 * changes nothing in dgpu_pass (every driver is unbound anyway). */
static int dgpu_releasing(void) { return g_dtx.ec != EC_READY || g_dtx.latch == SDTX_LATCH_OPENED; }
static int dgpu_scan(const char *why, int report, int pm) { return dgpu_pass(why, report, pm, dgpu_releasing()); }

static const char *base_name(int v) {
  switch (v) {
    case SDTX_BASE_DETACHED: return "detached";
    case SDTX_BASE_ATTACHED: return "attached";
    case SDTX_DETACH_NOT_FEASIBLE: return "not-feasible";
    default: return "unknown";
  }
}
static const char *latch_name(int v) {
  switch (v) {
    case SDTX_LATCH_CLOSED: return "closed";
    case SDTX_LATCH_OPENED: return "opened";
    case SDTX_ERR_FAILED_TO_OPEN: return "error: failed to open";
    case SDTX_ERR_FAILED_TO_REMAIN_OPEN: return "error: failed to remain open";
    case SDTX_ERR_FAILED_TO_CLOSE: return "error: failed to close";
    default: return "unknown";
  }
}
static const char *mode_name(int v) { return v == MODE_TABLET ? "tablet" : v == MODE_LAPTOP ? "laptop" : v == MODE_STUDIO ? "studio" : "unknown"; }
static const char *cancel_name(int v) {
  switch (v) {
    case SDTX_DETACH_NOT_FEASIBLE: return "not feasible (clipboard battery too low)";
    case SDTX_DETACH_TIMEDOUT: return "timed out while the latch was locked";
    case SDTX_ERR_FAILED_TO_OPEN: return "hardware: latch failed to open";
    case SDTX_ERR_FAILED_TO_REMAIN_OPEN: return "hardware: latch failed to remain open";
    case SDTX_ERR_FAILED_TO_CLOSE: return "hardware: latch failed to close";
    default: return "unknown reason";
  }
}

static int dtx_cmd(unsigned long req, const char *what) {
  int r = dtx_ioctl(g_dtx.fd, req, NULL);
  if (r) LOGE("DTX %s failed: %s", what, strerror(-r));
  return r;
}

/* Base keyboard + touchpad (SAM HID target 02 = base: ssam:01:15:02:xx) inhibited in studio mode. */
static void inhibit_base_input(int on) {
  if (!g_inhibit_on || g_dtx.inhibited == on) return;
  char dir[PATH_MAX], p[PATH_MAX + 300], real[PATH_MAX];
  DIR *d = opendir(rpath(dir, sizeof dir, "/sys/class/input"));
  int k = 0;
  if (!d) return;
  struct dirent *e;
  while ((e = readdir(d))) {
    if (strncmp(e->d_name, "input", 5)) continue;
    snprintf(p, sizeof p, "%s/%s", dir, e->d_name);
    if (!realpath(p, real) || !strstr(real, "/ssam:01:15:02:")) continue;
    snprintf(p, sizeof p, "%s/%s/inhibited", dir, e->d_name);
    if (!write_text(p, on ? "1" : "0")) k++;
  }
  closedir(d);
  int was = g_dtx.inhibited;
  g_dtx.inhibited = on;
  if (k && (on || was == 1)) LOGI("%s %d base keyboard/touchpad input device(s) (%s mode)", on ? "inhibited" : "re-enabled", k, mode_name(g_dtx.mode));
}

static void dtx_set_mode(int mode, const char *src) {
  if (mode < MODE_TABLET || mode > MODE_STUDIO) { LOGW("device mode: unknown value %d (%s) ignored", mode, src); return; }
  if (mode == g_dtx.mode && g_dtx.inhibited >= 0) return;
  if (mode != g_dtx.mode) LOGI("device mode: %s -> %s (%s); SW_TABLET_MODE=%d", mode_name(g_dtx.mode), mode_name(mode),
                               src, mode != MODE_LAPTOP);
  g_dtx.mode = mode;
  prop_set("vendor.gb.dtx.mode", mode_name(mode));
  inhibit_base_input(mode == MODE_STUDIO);
}

static void dtx_set_base(int base, const char *src) {
  /* unknown (SDTX_UNKNOWN) states keep the last known one, like surface-dtx-daemon: otherwise one odd EC value would
   * make every detach request look "not attached" and get canceled (detach impossible) */
  if (!base_known(base)) { LOGW("base: unknown state 0x%04x (%s) ignored", base, src); return; }
  if (base == g_dtx.base) return;
  int old = g_dtx.base;
  g_dtx.base = base;
  LOGI("base: %s -> %s (%s)", base_name(old), base_name(base), src);
  prop_set("vendor.gb.dtx.base", base_name(base));
  if (base == SDTX_BASE_DETACHED && g_dtx.latch == SDTX_LATCH_CLOSED)
    LOGW("base disconnected while the latch is closed (forced removal or EC misreport)");
  if (old == SDTX_BASE_DETACHED && base != SDTX_BASE_DETACHED) {
    /* re-attached: pciehp/surface_hotplug re-enumerate the dGPU; the PCI add uevent triggers dgpu_scan, this is
     * the backstop (and logs the resulting power state) */
    long long t = now_ms();
    g_dgpu_due = t + 3000;
    g_dgpu_report_due = t + 10000;
  }
}

static void dtx_set_latch(int latch, const char *src) {
  if (latch == g_dtx.latch) return;
  LOGI("latch: %s -> %s (%s)", latch_name(g_dtx.latch), latch_name(latch), src);
  g_dtx.latch = latch;
  prop_set("vendor.gb.dtx.latch", latch == SDTX_LATCH_OPENED ? "opened" : latch == SDTX_LATCH_CLOSED ? "closed" : "error");
}

/* Fresh state from the EC; used at open, after latch close (EC sends no base/mode events when the clipboard is pulled
 * while the latch is still closing) and for every base event (v6.18 surface_dtx's resume path emits a
 * BASE_CONNECTION event carrying the latch status, see patch 0003). */
static int dtx_query(const char *src) {
  struct sdtx_base_info bi;
  uint16_t v;
  int r = dtx_ioctl(g_dtx.fd, SDTX_IOCTL_GET_BASE_INFO, &bi);
  if (r) { LOGE("DTX GET_BASE_INFO failed: %s", strerror(-r)); return r; }
  dtx_set_base(bi.state, src);
  if (!dtx_ioctl(g_dtx.fd, SDTX_IOCTL_GET_LATCH_STATUS, &v) && latch_state(v) >= 0) dtx_set_latch(latch_state(v), src);
  if (!dtx_ioctl(g_dtx.fd, SDTX_IOCTL_GET_DEVICE_MODE, &v)) dtx_set_mode(v, src);
  return 0;
}

static void dtx_on_request(void) {
  if (g_dtx.ec != EC_READY) {                     /* a second request event = abort (button pressed again / echo) */
    if (g_dtx.latch == SDTX_LATCH_OPENED) {
      LOGI("detach: request while the latch is open -> the EC closes it again (detach canceled)");
      return;                                     /* stays confirmed until the LATCH_CLOSED event */
    }
    if (g_dtx.ec == EC_CONFIRMED) {
      /* Confirm/abort race (as surface-dtx-daemon): either the latch opens momentarily, or the abort was queued before
       * our confirm and it never opens. Wait 2 s and look; blocking on purpose like the daemon, so later DTX events
       * (kept in the driver's FIFO) are handled against the settled state. Rare (needs a double press). */
      struct timespec ts = {g_race_wait_ms / 1000, (g_race_wait_ms % 1000) * 1000000L};
      while (nanosleep(&ts, &ts) && errno == EINTR) {}
      uint16_t s;
      if (!dtx_ioctl(g_dtx.fd, SDTX_IOCTL_GET_LATCH_STATUS, &s) && latch_state(s) != SDTX_LATCH_CLOSED) {
        LOGI("detach: abort after confirm, latch not closed (0x%04x): waiting for it to close", s);
        if (latch_state(s) == SDTX_LATCH_OPENED) dtx_set_latch(SDTX_LATCH_OPENED, "re-check");
        return;                                   /* stays confirmed; LATCH_CLOSED -> ready */
      }
    }
    LOGI("detach: request canceled");
    g_dtx.ec = EC_READY;
    return;
  }
  g_dtx.ec = EC_IN_PROGRESS;
  struct sdtx_base_info bi;
  if (!dtx_ioctl(g_dtx.fd, SDTX_IOCTL_GET_BASE_INFO, &bi)) dtx_set_base(bi.state, "request");
  if (g_dtx.base != SDTX_BASE_ATTACHED) {        /* EC answers LATCH_CANCEL with a REQUEST event -> EC_READY */
    LOGW("detach: refused, %s", g_dtx.base == SDTX_DETACH_NOT_FEASIBLE ? "clipboard battery too low" : "no base attached");
    dtx_cmd(SDTX_IOCTL_LATCH_CANCEL, "LATCH_CANCEL");
    return;
  }
  LOGI("detach: requested (detach button)");
  dtx_cmd(SDTX_IOCTL_LATCH_HEARTBEAT, "LATCH_HEARTBEAT");
  /* Always unbind before confirming, also with -G / gb.dgpu=keep (then without runtime-PM writes) and with the nouveau
   * opt-in (its functions too, each checked to be driverless afterwards): the dGPU is about to be hot-removed, and a
   * confirm with a driver still bound is exactly the unsafe case. */
  int busy = dgpu_pass("detach", 0, g_dgpu_on, 1);
  if (busy) {                                     /* never pull a dGPU out from under a driver */
    LOGE("detach: aborted, %d dGPU function(s) still bound to a driver", busy);
    dtx_cmd(SDTX_IOCTL_LATCH_CANCEL, "LATCH_CANCEL");
    return;
  }
  /* opt-in driver was bound: its unbind resumed the GPU and tore nouveau/GSP-RM down (can take seconds), so restart
   * the EC's detach timeout before confirming (never sent without the opt-in: g_dgpu_kept is 0 then) */
  if (g_dgpu_kept) dtx_cmd(SDTX_IOCTL_LATCH_HEARTBEAT, "LATCH_HEARTBEAT");
  /* opt-in: the pass above can take seconds, so look once more right before confirming; a driver that bound meanwhile
   * cancels the detach (the next press unbinds it). Not done without the opt-in (its pass is quick; unchanged path). */
  if (g_keep_drv[0] && (busy = dgpu_bound_now())) {
    LOGE("detach: aborted, %d dGPU function(s) bound to a driver again before the confirm", busy);
    dtx_cmd(SDTX_IOCTL_LATCH_CANCEL, "LATCH_CANCEL");
    return;
  }
  if (!dtx_cmd(SDTX_IOCTL_LATCH_CONFIRM, "LATCH_CONFIRM")) {
    g_dtx.ec = EC_CONFIRMED;
    LOGI("detach: confirmed (dGPU idle), latch opening");
  }
}

static void dtx_event(int code, const unsigned char *d, int len) {
  uint16_t v = len >= 2 ? (uint16_t)(d[0] | d[1] << 8) : 0;
  switch (code) {
    case SDTX_EVENT_REQUEST:
      dtx_on_request();
      break;
    case SDTX_EVENT_CANCEL:
      if (SDTX_CAT(v) == SDTX_CAT_HW) LOGE("detach: canceled by the EC: %s (0x%04x)", cancel_name(v), v);
      else LOGW("detach: canceled by the EC: %s (0x%04x)", cancel_name(v), v);
      g_dtx.ec = EC_READY;
      break;
    case SDTX_EVENT_BASE_CONNECTION:
      LOGD("DTX base event state=0x%04x", v);
      if (dtx_query("base event")) dtx_set_base(v, "base event");
      break;
    case SDTX_EVENT_LATCH_STATUS: {
      int l;
      if (SDTX_CAT(v) == SDTX_CAT_HW) {           /* re-read; if that fails or is unknown, infer from the error */
        uint16_t s;
        LOGE("latch %s (0x%04x)", latch_name(v), v);
        l = !dtx_ioctl(g_dtx.fd, SDTX_IOCTL_GET_LATCH_STATUS, &s) ? latch_state(s) : -1;
        if (l < 0) l = latch_state(v);
      } else {
        l = latch_state(v);
      }
      if (l < 0) { LOGW("latch: unknown status 0x%04x ignored", v); break; }
      if (l == SDTX_LATCH_CLOSED) {
        g_dtx.ec = EC_READY;
        g_dtx.requery_at = now_ms() + 1500;
      }
      dtx_set_latch(l, "latch event");
      if (l == SDTX_LATCH_OPENED) LOGI("detach: latch open, the clipboard can be removed");
      break;
    }
    case SDTX_EVENT_DEVICE_MODE:
      dtx_set_mode(v, "mode event");
      break;
    default:
      LOGD("DTX: unknown event %d (%d bytes) skipped", code, len);
  }
}

/* Splits the byte stream read from /dev/surface/dtx into events: u16 length, u16 code, payload. Keeps a partial
 * trailing event for the next read. Returns the number of events handled. */
static int dtx_parse(void) {
  size_t off = 0;
  int k = 0;
  while (g_dtx.blen - off >= 4) {
    unsigned len = g_dtx.buf[off] | g_dtx.buf[off + 1] << 8, code = g_dtx.buf[off + 2] | g_dtx.buf[off + 3] << 8;
    if (len > sizeof g_dtx.buf - 4) { LOGE("DTX: bad event length %u, resync", len); off = g_dtx.blen; break; }
    if (g_dtx.blen - off < 4 + len) break;
    dtx_event(code, g_dtx.buf + off + 4, len);
    off += 4 + len;
    k++;
  }
  memmove(g_dtx.buf, g_dtx.buf + off, g_dtx.blen - off);
  g_dtx.blen -= off;
  return k;
}

static void dtx_close(void) {
  if (g_dtx.fd >= 0) close(g_dtx.fd);
  g_dtx.fd = -1;
  g_dtx.blen = 0;
  g_dtx.ec = EC_READY;
  g_dtx.requery_at = -1;
}

static int dtx_open(void) {
  char p[PATH_MAX], dev[32];
  if (g_dtx.fd >= 0) return 0;
  int fd = open(rpath(p, sizeof p, "/dev/surface/dtx"), O_RDWR | O_NONBLOCK | O_CLOEXEC);
  if (fd < 0 && errno == ENOENT &&
      read_text(rpath(p, sizeof p, "/sys/class/misc/surface_dtx/dev"), dev, sizeof dev) > 0) {
    unsigned ma, mi;                              /* misc device registered but no node (yet): make our own */
    if (sscanf(dev, "%u:%u", &ma, &mi) == 2) {
      rpath(p, sizeof p, "/dev/gb_power");
      mkdir(p, 0755);
      rpath(p, sizeof p, "/dev/gb_power/dtx");
      unlink(p);
      if (!mknod(p, S_IFCHR | 0600, makedev(ma, mi))) fd = open(p, O_RDWR | O_NONBLOCK | O_CLOEXEC);
    }
  }
  if (fd < 0) return -1;
  g_dtx.fd = fd;
  int r = dtx_ioctl(fd, SDTX_IOCTL_EVENTS_ENABLE, NULL);
  if (r) { LOGE("DTX EVENTS_ENABLE failed: %s", strerror(-r)); dtx_close(); return -1; }
  /* events first, then the state, so nothing is missed (same order as surface-dtx-daemon); taken as-is, no
   * transition handling */
  struct sdtx_base_info bi;
  uint16_t v;
  if (!dtx_ioctl(fd, SDTX_IOCTL_GET_BASE_INFO, &bi) && base_known(bi.state)) g_dtx.base = bi.state;
  if (!dtx_ioctl(fd, SDTX_IOCTL_GET_LATCH_STATUS, &v) && latch_state(v) >= 0) g_dtx.latch = latch_state(v);
  if (!dtx_ioctl(fd, SDTX_IOCTL_GET_DEVICE_MODE, &v) && v <= MODE_STUDIO) g_dtx.mode = v;
  g_dtx.inhibited = -1;
  inhibit_base_input(g_dtx.mode == MODE_STUDIO);
  g_dtx.ec = g_dtx.latch == SDTX_LATCH_OPENED ? EC_CONFIRMED : EC_READY;
  prop_set("vendor.gb.dtx.base", base_name(g_dtx.base));
  prop_set("vendor.gb.dtx.latch", g_dtx.latch == SDTX_LATCH_OPENED ? "opened" : "closed");
  prop_set("vendor.gb.dtx.mode", mode_name(g_dtx.mode));
  LOGI("DTX ready: base %s, latch %s, mode %s; detach requests are confirmed immediately",
       base_name(g_dtx.base), latch_name(g_dtx.latch), mode_name(g_dtx.mode));
  return 0;
}

static void dtx_read(void) {
  for (;;) {
    unsigned char tmp[256];                       /* events are 4..8 bytes */
    ssize_t r = read(g_dtx.fd, tmp, sizeof tmp);
    if (r < 0 && errno == EINTR) continue;
    if (r < 0 && errno == EAGAIN) break;
    if (r <= 0) { LOGW("DTX device gone (%s)", r ? strerror(errno) : "EOF"); dtx_close(); g_dtx.retry_at = now_ms() + 2000; return; }
    if ((size_t)r > sizeof g_dtx.buf - g_dtx.blen) { LOGE("DTX: event buffer overrun, resync"); g_dtx.blen = 0; }
    memcpy(g_dtx.buf + g_dtx.blen, tmp, r);
    g_dtx.blen += r;
    dtx_parse();
  }
}

/* ------------------------------------------------------------------------------------------------ uevents */
struct uev { const char *action, *devpath, *subsystem, *devname, *psy_name, *pci_id; };

static void uev_parse(char *buf, int len, struct uev *u) {
  memset(u, 0, sizeof *u);
  for (int i = 0; i < len;) {
    char *s = buf + i;
    size_t l = strnlen(s, len - i);
    if (!strncmp(s, "ACTION=", 7)) u->action = s + 7;
    else if (!strncmp(s, "DEVPATH=", 8)) u->devpath = s + 8;
    else if (!strncmp(s, "SUBSYSTEM=", 10)) u->subsystem = s + 10;
    else if (!strncmp(s, "DEVNAME=", 8)) u->devname = s + 8;
    else if (!strncmp(s, "POWER_SUPPLY_NAME=", 18)) u->psy_name = s + 18;
    else if (!strncmp(s, "PCI_ID=", 7)) u->pci_id = s + 7;
    i += l + 1;
  }
}

static long long g_bat_due = 0, g_profile_due = 0, g_tp_due = 0;
static int g_profile_done;

static void uev_handle(const struct uev *u) {
  if (!u->subsystem || !u->action) return;
  long long t = now_ms();
  if (!strcmp(u->subsystem, "power_supply")) {
    if (u->psy_name && !strncmp(u->psy_name, "test_", 5)) return;   /* our own writes */
    if (g_bat_due < 0 || g_bat_due > t + 300) g_bat_due = t + 300;    /* coalesce bursts */
  } else if (!strcmp(u->subsystem, "pci") && g_dgpu_on && u->pci_id && !strncasecmp(u->pci_id, "10DE:", 5)) {
    if (!strcmp(u->action, "remove")) {
      const char *b = u->devpath ? strrchr(u->devpath, '/') : NULL;
      LOGI("dGPU %s removed (base detached)", b ? b + 1 : "?");
    }
    else if (!strcmp(u->action, "add") || !strcmp(u->action, "bind")) {
      g_dgpu_due = t + 500;
      g_dgpu_report_due = t + 8000;
    }
  } else if (!strcmp(u->subsystem, "misc") && g_dtx_on && u->devname && !strcmp(u->devname, "surface/dtx")) {
    if (!strcmp(u->action, "add")) g_dtx.retry_at = t + 200;
    else if (!strcmp(u->action, "remove")) dtx_close();
  } else if (!strcmp(u->subsystem, "platform-profile") && !strcmp(u->action, "add") && !g_profile_done) {
    g_profile_due = t + 200;
  } else if (!strcmp(u->subsystem, "input") && !strcmp(u->action, "add") && g_dtx.mode == MODE_STUDIO) {
    g_dtx.inhibited = -1;                         /* base devices re-registered while in studio mode */
    inhibit_base_input(1);
  } else if (!strcmp(u->subsystem, "module") && u->devpath && !strcmp(u->devpath, "/module/test_power") &&
             !strcmp(u->action, "add")) {
    g_tp.ok = 0;                                  /* (re)loaded: parameters are back at their defaults */
    g_tp_due = t + 100;
  }
}

static int uevent_open(void) {
  struct sockaddr_nl a = {.nl_family = AF_NETLINK, .nl_groups = 1};
  int fd = socket(AF_NETLINK, SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, NETLINK_KOBJECT_UEVENT);
  if (fd < 0) return -1;
  int sz = 1 << 20;
  if (setsockopt(fd, SOL_SOCKET, SO_RCVBUFFORCE, &sz, sizeof sz)) setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &sz, sizeof sz);
  if (bind(fd, (struct sockaddr *)&a, sizeof a)) { close(fd); return -1; }
  return fd;
}

static void uevent_read(int fd) {
  char buf[8192];
  for (;;) {
    ssize_t r = recv(fd, buf, sizeof buf - 1, 0);
    if (r < 0 && errno == EINTR) continue;
    if (r < 0 && errno == ENOBUFS) { LOGW("uevent overrun: rescanning"); g_bat_due = now_ms(); g_dgpu_due = now_ms(); continue; }
    if (r <= 0) return;
    buf[r] = 0;
    struct uev u;
    uev_parse(buf, (int)r, &u);
    uev_handle(&u);
  }
}

/* ------------------------------------------------------------------------------------------------ main */
static long long min_due(long long a, long long b) { return a < 0 ? b : b < 0 ? a : a < b ? a : b; }

static void read_cmdline(void) {
  char p[PATH_MAX], c[4096];
  if (read_text(rpath(p, sizeof p, "/proc/cmdline"), c, sizeof c) <= 0) return;
  for (char *t = strtok(c, " "); t; t = strtok(NULL, " ")) {
    if (!strcmp(t, "gb.dgpu=keep")) { g_dgpu_on = 0; LOGI("gb.dgpu=keep: dGPU left alone"); }
    if (!strcmp(t, "gb.power.hide=0")) g_hide = 0;
  }
}

static void usage(void) {
  fprintf(stderr, "usage: gb_power [-p profile] [-o] [-r root] [-H] [-D] [-G] [-I] [-K driver] [-v]\n");
}

int main(int argc, char **argv) {
  const char *profile = NULL;
  int once = 0, opt;
  while ((opt = getopt(argc, argv, "p:or:HDGIK:v")) != -1) {
    switch (opt) {
      case 'K': snprintf(g_keep_drv, sizeof g_keep_drv, "%s", optarg); g_keep_cli = 1; break;
      case 'p': profile = optarg; break;
      case 'o': once = 1; break;
      case 'r': snprintf(g_root, sizeof g_root, "%s", optarg); break;
      case 'H': g_hide = 0; break;
      case 'D': g_dtx_on = 0; break;
      case 'G': g_dgpu_on = 0; break;
      case 'I': g_inhibit_on = 0; break;
      case 'v': g_verbose = 1; break;
      default: usage(); return 2;
    }
  }
  setvbuf(stdout, NULL, _IOLBF, 0);
  signal(SIGPIPE, SIG_IGN);
  if (!g_root[0] && !once) g_kmsg = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
  if (profile) {                                  /* rc: on property:persist.vendor.gb.perf_profile=* */
    int r = profile_apply(profile);
    if (r == -1) LOGW("no platform profile interface (surface_platform_profile not loaded?)");
    return r ? 1 : 0;
  }
  if (once) {
    tp_probe();
    struct psy ps[MAXPSY];
    struct combo c;
    int n = psy_scan(ps, MAXPSY);
    if (g_tp.ok) hide_batteries(ps, n);
    combine(ps, n, &c);
    publish(&c);
    char d[256];
    describe(ps, n, &c, d, sizeof d);
    printf("combined: %s\npresent=%d nbat=%d capacity=%d status=%s ac=%d voltage_uV=%lld current_uA=%lld "
           "counter_uAh=%lld full_uAh=%lld design_uAh=%lld e_now_uWh=%lld e_full_uWh=%lld net_uW=%lld%s\n",
           d, c.present, c.nbat, c.capacity, status_name(c.status), c.ac, c.v_uv, c.i_ua, c.q_now, c.q_full,
           c.q_design, c.e_now, c.e_full, c.p_net, g_tp.ok ? "" : " (test_power absent: nothing published)");
    return 0;
  }

  read_cmdline();
  LOGI("starting (hide=%d dtx=%d dgpu=%d inhibit=%d)", g_hide, g_dtx_on, g_dgpu_on, g_inhibit_on);
  if (g_keep_cli)                                 /* (without -K nothing extra is logged: default output unchanged) */
    LOGI("-K '%s': %s (persist.vendor.gb.nouveau ignored)", g_keep_drv,
         g_keep_drv[0] ? "dGPU functions bound to it stay bound except at detach" : "every dGPU driver is unbound");
  int ue = uevent_open();                         /* before the first scan, so no change is missed */
  if (ue < 0) LOGW("uevent socket failed (%s): polling only", strerror(errno));
  if (!tp_probe()) LOGW("test_power not loaded yet: battery bridge waits for it");
  battery_update(1);
  if (g_dgpu_on) { dgpu_scan("start", 0, 1); g_dgpu_report_due = now_ms() + 8000; }
  if (g_dtx_on && dtx_open()) g_dtx.retry_at = now_ms() + 1000;
  char want[100];
  prop_get("persist.vendor.gb.perf_profile", want);
  g_profile_done = profile_apply(want) == 0;
  long long start = now_ms(), last_bat = now_ms();
  if (!g_profile_done) g_profile_due = now_ms() + 2000;
  g_bat_due = -1;

  for (;;) {
    long long t = now_ms();
    /* backstop poll: 20 s while bridging; without test_power 5 s during boot, then 60 s (its load uevent wakes us) */
    long long periodic = last_bat + (g_tp.ok ? 20000 : t - start < 120000 ? 5000 : 60000);
    long long due = min_due(min_due(min_due(g_bat_due, periodic), min_due(g_dgpu_due, g_dgpu_report_due)),
                            min_due(min_due(g_dtx.fd >= 0 && g_dtx.requery_at > 0 ? g_dtx.requery_at : -1,
                                            g_dtx.fd < 0 && g_dtx_on && g_dtx.retry_at > 0 ? g_dtx.retry_at : -1),
                                    min_due(g_profile_due > 0 ? g_profile_due : -1, g_tp_due > 0 ? g_tp_due : -1)));
    int timeout = due < 0 ? 60000 : due <= t ? 0 : (int)(due - t > 60000 ? 60000 : due - t);
    struct pollfd pf[2];
    int np = 0;
    if (ue >= 0) pf[np++] = (struct pollfd){.fd = ue, .events = POLLIN};
    if (g_dtx.fd >= 0) pf[np++] = (struct pollfd){.fd = g_dtx.fd, .events = POLLIN};
    int r = poll(pf, np, timeout);
    if (r < 0 && errno != EINTR) { LOGE("poll: %s", strerror(errno)); sleep(1); }
    for (int i = 0; r > 0 && i < np; i++) {
      if (!pf[i].revents) continue;
      if (pf[i].fd == ue) uevent_read(ue);
      else if (pf[i].fd != g_dtx.fd) continue;    /* closed meanwhile (misc remove uevent) */
      else if (pf[i].revents & (POLLERR | POLLHUP | POLLNVAL)) { LOGW("DTX device hung up"); dtx_close(); g_dtx.retry_at = now_ms() + 2000; }
      else dtx_read();
    }
    t = now_ms();
    if (g_tp_due > 0 && t >= g_tp_due) { g_tp_due = -1; if (tp_probe()) g_bat_due = t; }
    if ((g_bat_due >= 0 && t >= g_bat_due) || t >= periodic) {
      g_bat_due = -1;
      last_bat = t;
      if (!g_tp.ok) tp_probe();
      battery_update(0);
    }
    if (g_dgpu_due >= 0 && t >= g_dgpu_due) { g_dgpu_due = -1; if (g_dgpu_on) dgpu_scan("hot-plug", 0, 1); }
    if (g_dgpu_report_due >= 0 && t >= g_dgpu_report_due) { g_dgpu_report_due = -1; if (g_dgpu_on) dgpu_scan("report", 1, 1); }
    if (g_dtx.fd >= 0 && g_dtx.requery_at > 0 && t >= g_dtx.requery_at) { g_dtx.requery_at = -1; dtx_query("re-check"); }
    if (g_dtx_on && g_dtx.fd < 0 && g_dtx.retry_at > 0 && t >= g_dtx.retry_at) {
      /* retry while the driver may still be probing; after 2 min only on its uevent */
      g_dtx.retry_at = dtx_open() && t - start < 120000 ? t + 3000 : -1;
    }
    if (g_profile_due > 0 && t >= g_profile_due) {
      prop_get("persist.vendor.gb.perf_profile", want);
      g_profile_done = profile_apply(want) == 0;
      g_profile_due = g_profile_done || t - start > 120000 ? -1 : t + 2000;
      if (!g_profile_done && g_profile_due < 0) LOGW("no platform profile interface after 2 min");
    }
  }
}
