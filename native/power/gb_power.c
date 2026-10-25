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
 * B. Detach (DTX, /dev/surface/dtx) and the latch lifecycle (C:\GB-build\native\dgpu\INTERFACES.md 10.2, 10.3).
 *    * Quick path (unchanged): no driver on any NVIDIA function and nouveau never used this boot -> the request is
 *      confirmed at once.
 *    * Slow path otherwise: boot-disk guard (a mounted USB/SD block device counts as base-side), no gb_nouveau.sh load
 *      window open (else refused, N15), "Preparing to detach" notification, latch locked, gb_gpu_compute,
 *      gb_nvk_probe and gb_dgpu_test stopped, nouveau DRM nodes chmod 000 (also nodes that appear later),
 *      /sys/module/nouveau/refcnt gate (30 s; holders named from /proc/<pid>/fd and /proc/<pid>/maps), then forked
 *      worker job 1 unbinds every bound NVIDIA function, the main loop re-checks (late re-bind, refcnt), and worker job
 *      2 PCI-removes each function (20 s deadline per job; the child re-checks the refcnt gate right before every
 *      unbind/remove write). The main loop keeps the battery bridge, DTX, the control socket and a LATCH_HEARTBEAT
 *      every second running meanwhile. Verify that no 10de device is left, the refcnt gate and the boot-disk guard
 *      again, then LATCH_CONFIRM. A missed deadline = "wedged": latch kept locked, LATCH_CANCEL, no further dGPU
 *      writes this boot. /dev/gb_dgpu/detaching exists from the request until the EC process is over; gb_nouveau.sh
 *      refuses insmod while it exists.
 *    * Latch: LOCKed whenever a driver is bound to an NVIDIA function or a drive in the base is mounted, and for every
 *      slow-path decision (re-locked after every system resume and every BASE_CONNECTION event), UNLOCKed when
 *      driverless, at daemon start only if driverless, at shutdown (sys.powerctl) and on SIGTERM only if driverless
 *      (and no base drive mounted) or shutting down. The EC cancels a locked request on timeout.
 *    * User abort, EC cancel, latch, base and mode events are handled like linux-surface's surface-dtx-daemon.
 *      Publishes vendor.gb.dtx.{mode,base,latch}; inhibits the base keyboard + touchpad in studio mode (Windows does
 *      the same). SW_TABLET_MODE itself comes from surface_dtx.
 * C. dGPU (NVIDIA TU117 in the base). Default (no nouveau): every 10de function driverless with runtime PM 'auto' on
 *    it and all its upstream bridges; the root port then runtime-suspends and ACPI _PR3 cuts the power (D3cold).
 *    gb_power never unbinds nouveau on its own initiative (that is gb_nouveau.sh off's job, gated by refcnt); a VGA
 *    function bound to another driver and any driver on a non-VGA function (HDA 02:00.1) are unbound by the worker.
 *    After nouveau was used this boot (or gb_power removed the functions), attach/cancel runs a staged rescan of the
 *    root port. Watchdog: runtime_status=error or idle D0 > 60 s -> fault. Live state in vendor.gb.dgpu.state
 *    (single writer), holders in vendor.gb.dgpu.holders. nouveau DRM nodes: 0660 root:graphics (render) / 0600
 *    root:root (card), enforced by stat() on every fast tick; render 0666 only while gb_power's own graphics
 *    derivation says nvidia (section G). In Intel mode a bound, idle nouveau is handed to gb_nouveau_off
 *    (reconcile_off), which does the refcnt-gated unbind.
 * D. Platform profile: sets /sys/firmware/acpi/platform_profile at start (persist.vendor.gb.perf_profile if valid,
 *    else balanced). "gb_power -p <profile>" sets one and exits (the rc property trigger runs that).
 * E. Control socket /dev/socket/gb_power (init-created, INTERFACES 9): one ASCII line in, one JSON line out, roles
 *    from SO_PEERCRED; status/watch/holders/enable/disable/detach-request/report/compute-* commands.
 * F. Notifications: messages queued in /data/vendor/gb/notify + vendor.gb.notify trigger (gb_notify delivers them as
 *    the shell user); gb_power never forks Java tools.
 * G. Graphics switching (INTERFACES v1.2 sections 22-26): the mode persist.vendor.gb.gfx_mode (auto/intel/nvidia),
 *    the effective app graphics vendor.gb.gfx_active/gfx_reason (single writer, 16-row derivation), the app-graphics
 *    session marker, the three Settings.Global updatable_driver keys (queued for gb_notify, kind=settings), the
 *    per-app choices and the app catalog GbGpu pushes, the automatic self-test (gb_gfx_selftest, root-owned record
 *    /data/vendor/gb/selftest/result, mirrored to persist.vendor.gb.gfx_ok), the rebind guard for the kernel's own
 *    nouveau re-probe on reattach, generalised hot attach, reconcile_off and the pending detach of
 *    close-holders-and-detach.
 *
 * Invariant: the main process never performs a dGPU sysfs write that can block. unbind, remove, rescan and
 * power/control=on run in a forked worker with a deadline. Status polling reads only power_state, runtime_status and
 * refcnt (vendor/class/device/resource once per device appearance); it never reads config space or opens DRM nodes.
 *
 * Logs to /dev/kmsg as "gb_power: ...". Needs root (bind mount, sysfs writes). Single-threaded poll loop on the kernel
 * uevent netlink socket, the DTX fd, the control socket and the worker pipe.
 * Build: clang --target=x86_64-linux-android34 -O2 -Wall -Wextra -Werror -static -Iuapi -o gb_power gb_power.c
 * Options: -p <profile> set platform profile and exit | -o one battery pass, print, exit | -r <root> sysfs/dev prefix
 *          (tests) | -S <path> control socket path (tests, only with -r) | -H no BATn/type hiding | -D no DTX | -G no
 *          dGPU handling (as gb.dgpu=keep: no unbind/runtime PM at start or hot-plug; detach still removes) | -I no
 *          studio-mode input inhibit | -v | -K <driver> VGA functions bound to <driver> stay bound (default nouveau;
 *          -K '' = unbind every driver, tests only) | --dtx-monitor (read-only DTX event printer, daemon stopped)
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
#include <sys/un.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#ifdef __ANDROID__
#include <sys/system_properties.h>
#endif

#ifndef GB_POWER_BUILD_ID
#define GB_POWER_BUILD_ID "1"                     /* "server" in the hello reply (INTERFACES 9.6 golden transcript) */
#endif
#ifndef GB_POWER_IOMMU_DMAFQ
#define GB_POWER_IOMMU_DMAFQ 0                    /* P3 item 7: DMA-FQ domain for the dGPU group; HW-gated, default off */
#endif
#ifndef PROP_VALUE_MAX
#define PROP_VALUE_MAX 92
#endif

/* timing (INTERFACES 2.3) */
#define HB_MS 1000                                /* LATCH_HEARTBEAT period during a slow-path decision (<= 2.0 s) */
#define DEC_STEP_MS 500                           /* decision poll period (refcnt, heartbeats) */
#define REFCNT_WAIT_MS 30000                      /* refcnt gate */
#define DETACH_DEADLINE_MS 20000                  /* unbind + remove worker */
#define UNBIND_DEADLINE_MS 20000                  /* background unbind of non-nouveau drivers */
#define POWER_ON_DEADLINE_MS 10000                /* power/control=on (rescan port, compute hold) */
#define RESCAN_DEADLINE_MS 20000
#define D0_WAIT_MS 4000                           /* rescan: root port to D0 */
#define D0_IDLE_FAULT_MS 60000                    /* watchdog: nouveau-bound dGPU idle in D0 */
#define TICK_FAST_MS 2000
#define TICK_SLOW_MS 10000
#define HOLDER_RATE_MS 1000
#define HOLDER_RESCAN_MS 10000                    /* full fd+maps holder rescan while busy and refcnt unchanged */
#define CL_IDLE_MS 30000
#define WATCH_PING_MS 30000
#define BCAST_MS 2000
#define ECHO_WAIT_MS 10000                        /* after our LATCH_CANCEL: wait this long for the EC's REQUEST echo */
#define CONFIRM_WATCHDOG_MS 10000                 /* slow path: latch must open this soon after LATCH_CONFIRM */
#define UNBIND_TRIES 3                            /* detach: unbind job repeated at most this often (late re-binds) */
/* v1.2 graphics switching (INTERFACES 2.3) */
#define MARK_DEADLINE_MS 20000                    /* worker: crash/session marker write + fsync (storage, not the dGPU) */
#define EARLY_REFUSE_MS 5000                      /* detach refused this early when only closable apps hold nouveau */
#define PEND_DEADLINE_MS 15000                    /* close-holders-and-detach: refcnt must reach 0 within */
#define PEND_READ_MS 500                          /* ... read twice this far apart */
#define OFF_GAP_MS 10000                          /* reconcile_off: minimum gap between gb_nouveau_off starts */
#define OFF_SKIP_MS 30000                         /* node enforcement skipped this long after gb_power started off */
#define HOTATTACH_MAX 3                           /* gb_nouveau starts per boot by the hot attach */
#define UNCLEAN_MAX 2                             /* unclean NVIDIA app-graphics sessions in a row before Intel */
#define RECONCILE_ON_GAP_MS 10000                 /* reconcile_on (R-3): gap after gb_nouveau/_off ran or was started */
#define REBIND_WAIT_MS 60000                      /* rebind guard: dGPU must appear this soon after arming */
#define REBIND_HEALTH_MS 30000                    /* ... and be bound + suspended + D3cold this soon after it appeared */
#define REBIND_CLEAN_MS 120000                    /* ... clean mark of the binding (marker kept until then) */
#define REBIND_CLEAN_GRACE_MS 30000               /* ... D3cold may take this long after the clean mark (GP-1B-D3) */
#define SELFTEST_DEADLINE_MS 360000               /* gb_gfx_selftest: ctl.stop this long after the start */
#define SELFTEST_GRACE_MS 10000                   /* ... still running this long after the stop: hang */
#define SELFTEST_START_MS 10000                   /* ... init has this long to report it running after ctl.start */

/* ------------------------------------------------------------------------------------------------ basics */
static int g_kmsg = -1, g_verbose;
static char g_root[256] = "";      /* path prefix for /sys, /dev, /proc, /data, /odm (unit tests use a fake tree) */
static int g_hide = 1, g_dtx_on = 1, g_dgpu_on = 1, g_inhibit_on = 1;
/* VGA function driver gb_power leaves bound outside a detach: nouveau (gb_nouveau.sh's job to unbind), or -K. */
static char g_keep_drv[48] = "nouveau";
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

static long long real_now_ms(void) {
  struct timespec t;
  clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec * 1000LL + t.tv_nsec / 1000000;
}
static long long (*g_clock)(void) = real_now_ms;  /* unit tests use a fake clock */
static long long now_ms(void) { return g_clock(); }
static long long epoch_ms(void) {
  struct timespec t;
  clock_gettime(CLOCK_REALTIME, &t);
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

static int path_exists(const char *abs) {
  char p[PATH_MAX];
  return !access(rpath(p, sizeof p, "%s", abs), F_OK);
}
static int is_num(const char *s) {
  if (!*s) return 0;
  for (; *s; s++)
    if (*s < '0' || *s > '9') return 0;
  return 1;
}
/* printable ASCII copy, other bytes -> '?' (INTERFACES 9.1, 12.1) */
static void ascii_copy(char *dst, size_t cap, const char *src, size_t maxlen) {
  size_t n = 0;
  for (; *src && n < maxlen && n + 1 < cap; src++) {
    unsigned char ch = (unsigned char)*src;
    dst[n++] = ch >= 0x20 && ch < 0x7f ? (char)ch : '?';
  }
  dst[n] = 0;
}

/* unit tests: one ordered log of the setprops, node chmods and marker results (INTERFACES 23.3 ordering) */
static void (*g_trace)(const char *what) = NULL;
static void trace(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void trace(const char *fmt, ...) {
  if (!g_trace) return;
  char b[200];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(b, sizeof b, fmt, ap);
  va_end(ap);
  g_trace(b);
}

/* ------------------------------------------------------------------------------------------------ properties */
#ifdef __ANDROID__
static void prop_set(const char *k, const char *v) {
  trace("setprop:%s=%s", k, v);
  if (g_root[0]) { LOGI("test: setprop %s %s", k, v); return; }
  if (__system_property_set(k, v)) LOGW("setprop %s=%s failed", k, v);
}
static void prop_get(const char *k, char *v /* >= PROP_VALUE_MAX */) {
  v[0] = 0;
  if (!g_root[0]) __system_property_get(k, v);
}
#define PROP_REAL (!g_root[0])
#else /* host build (unit tests): in-memory store, or one file per property in $GB_POWER_PROPS_DIR (socket tests) */
static struct { char k[64], v[PROP_VALUE_MAX]; } g_props[128];
static int props_file(const char *k, char *p, size_t n) {
  const char *d = getenv("GB_POWER_PROPS_DIR");
  if (!d || !*d) return 0;
  snprintf(p, n, "%s/%s", d, k);
  return 1;
}
static char g_ctl_log[1024];                     /* unit tests: every ctl.start/ctl.stop in order */
static void prop_set(const char *k, const char *v) {
  char p[PATH_MAX], t[PATH_MAX + 8];
  trace("setprop:%s=%s", k, v);
  if (!strncmp(k, "ctl.", 4)) {
    size_t l = strlen(g_ctl_log);
    if (l + strlen(k) + strlen(v) + 3 < sizeof g_ctl_log) snprintf(g_ctl_log + l, sizeof g_ctl_log - l, "%s=%s ", k, v);
  }
  if (props_file(k, p, sizeof p)) {
    snprintf(t, sizeof t, "%s.tmp", p);
    FILE *f = fopen(t, "w");
    if (f) {
      fputs(v, f);
      if (!fclose(f) && rename(t, p)) unlink(t);
    }
    return;
  }
  int i;
  for (i = 0; i < 128 && g_props[i].k[0] && strcmp(g_props[i].k, k); i++) {}
  if (i == 128) return;
  snprintf(g_props[i].k, sizeof g_props[i].k, "%s", k);
  snprintf(g_props[i].v, sizeof g_props[i].v, "%s", v);
}
static void prop_get(const char *k, char *v) {
  char p[PATH_MAX];
  v[0] = 0;
  if (props_file(k, p, sizeof p)) { if (read_text(p, v, PROP_VALUE_MAX) < 0) v[0] = 0; return; }
  for (int i = 0; i < 128 && g_props[i].k[0]; i++)
    if (!strcmp(g_props[i].k, k)) { strcpy(v, g_props[i].v); return; }
}
#define PROP_REAL 1
#endif

/* Publishes a property gb_power owns: written only when it changed (or was overwritten by someone else). A value that
 * already reads as wanted is not written again (no empty persist.* property is created on an image without the
 * graphics stack). */
#define NPUB 32
static struct { char k[40], v[PROP_VALUE_MAX]; } g_pub[NPUB];
static void prop_put(const char *k, const char *v) {
  int i;
  for (i = 0; i < NPUB && g_pub[i].k[0] && strcmp(g_pub[i].k, k); i++) {}
  if (i < NPUB && g_pub[i].k[0] && !strcmp(g_pub[i].v, v) && !PROP_REAL) return;
  if (PROP_REAL || i == NPUB || !g_pub[i].k[0]) {
    char cur[PROP_VALUE_MAX];
    prop_get(k, cur);
    if (!strcmp(cur, v)) {
      if (i < NPUB) { snprintf(g_pub[i].k, sizeof g_pub[i].k, "%s", k); snprintf(g_pub[i].v, sizeof g_pub[i].v, "%s", v); }
      return;
    }
  }
  prop_set(k, v);
  if (i < NPUB) {
    snprintf(g_pub[i].k, sizeof g_pub[i].k, "%s", k);
    snprintf(g_pub[i].v, sizeof g_pub[i].v, "%s", v);
  }
}
static int prop_is(const char *k, const char *want) {
  char v[PROP_VALUE_MAX];
  prop_get(k, v);
  return !strcmp(v, want);
}

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

/* ------------------------------------------------------------------------------------------------ F. notifications */
/* INTERFACES 12.1: one key=value message file per notification in /data/vendor/gb/notify, then vendor.gb.notify.
 * Queued in memory (max 16, newest per tag / settings key) until sys.boot_completed=1 and the directory exists. */
#define NQ_MAX 16
static struct { char key[64]; char body[640]; } g_nq[NQ_MAX];
static int g_nqn, g_nseq;
static long long g_notify_last;

static int boot_completed(void) { return prop_is("sys.boot_completed", "1"); }

static void nq_flush(void) {
  if (!g_nqn || !boot_completed()) return;
  char dir[PATH_MAX];
  struct stat st;
  if (stat(rpath(dir, sizeof dir, "/data/vendor/gb/notify"), &st) || !S_ISDIR(st.st_mode)) return;
  long long ms = epoch_ms();
  int done = 0, taken = 0;
  while (g_nqn) {
    char fn[PATH_MAX + 64], tmp[PATH_MAX + 72];
    snprintf(fn, sizeof fn, "%s/%013lld-%d-%d.msg", dir, ms, (int)getpid(), ++g_nseq);
    snprintf(tmp, sizeof tmp, "%s.tmp", fn);
    /* notify/ is group-shell writable (gb_notify deletes the files): never follow or truncate a planted entry */
    int fd = open(tmp, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644);
    if (fd < 0 && errno == EEXIST && ++taken < 16) continue;   /* name taken: the next sequence number */
    if (fd < 0) { LOGW("notify: %s: %s", tmp, strerror(errno)); break; }
    size_t len = strlen(g_nq[0].body);
    int ok = write(fd, g_nq[0].body, len) == (ssize_t)len;
    if (fchmod(fd, 0644)) ok = 0;
    if (close(fd)) ok = 0;
    if (!ok || rename(tmp, fn)) { LOGW("notify: writing %s failed", fn); unlink(tmp); break; }
    memmove(&g_nq[0], &g_nq[1], (g_nqn - 1) * sizeof g_nq[0]);
    g_nqn--;
    done++;
  }
  if (done) {
    if (ms <= g_notify_last) ms = g_notify_last + 1;
    g_notify_last = ms;
    char v[24];
    snprintf(v, sizeof v, "%lld", ms);
    prop_set("vendor.gb.notify", v);
  }
}

static void nq_put(const char *key, const char *body) {
  int i;
  for (i = 0; i < g_nqn && strcmp(g_nq[i].key, key); i++) {}
  if (i < g_nqn) {                                /* newest per key kept */
    memmove(&g_nq[i], &g_nq[i + 1], (g_nqn - i - 1) * sizeof g_nq[0]);
    g_nqn--;
  }
  if (g_nqn == NQ_MAX) {
    LOGW("notification queue full: dropped %s", g_nq[0].key);
    memmove(&g_nq[0], &g_nq[1], (NQ_MAX - 1) * sizeof g_nq[0]);
    g_nqn--;
  }
  snprintf(g_nq[g_nqn].key, sizeof g_nq[g_nqn].key, "%s", key);
  snprintf(g_nq[g_nqn].body, sizeof g_nq[g_nqn].body, "%s", body);
  g_nqn++;
  nq_flush();
}

static void notify(const char *tag, const char *title, const char *text) {
  char t[48], x[208], b[640];
  ascii_copy(t, sizeof t, title, 40);
  ascii_copy(x, sizeof x, text, 200);
  snprintf(b, sizeof b, "kind=notify\ntag=%s\ntitle=%s\ntext=%s\n", tag, t, x);
  LOGI("notification [%s] %s: %s", tag, t, x);
  nq_put(tag, b);
}
static void notify_force_stop(const char *pkg, int user) {
  char k[160], b[200];
  snprintf(k, sizeof k, "fs:%s:%d", pkg, user);
  snprintf(b, sizeof b, "kind=force-stop\npkg=%s\nuser=%d\n", pkg, user);
  nq_put(k, b);
}
/* INTERFACES 12.1 / 24.6: kind=settings, exactly three key/value-or-src pairs (gb_notify re-checks the whitelist) */
static void settings_all_apps(int v) {
  char b[96];
  snprintf(b, sizeof b, "kind=settings\nkey=updatable_driver_all_apps\nvalue=%d\n", v);
  LOGI("settings: updatable_driver_all_apps=%d queued", v);
  nq_put("set:all_apps", b);
}
static void settings_list(int out) {
  char b[128];
  snprintf(b, sizeof b, "kind=settings\nkey=updatable_driver_production_opt_%s_apps\nsrc=gfx_opt%s\n", out ? "out" : "in",
           out ? "out" : "in");
  LOGI("settings: updatable_driver_production_opt_%s_apps from gfx_opt%s queued", out ? "out" : "in", out ? "out" : "in");
  nq_put(out ? "set:opt_out" : "set:opt_in", b);
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

/* ------------------------------------------------------------------------------------------------ C. dGPU inventory */
static const char *hex4(const char *v) { return !strncasecmp(v, "0x", 2) ? v + 2 : v; }

static int is_bdf(const char *s) {
  unsigned a, b, c, d;
  int k = 0;
  return strlen(s) == 12 && sscanf(s, "%4x:%2x:%2x.%1x%n", &a, &b, &c, &d, &k) == 4 && k == 12;
}

#define MAXFN 8
struct dfn { char bdf[16], did[12], drv[48]; unsigned cls; int vga, bar_ok; };
/* last read-only scan: every 10de function (sorted), vga = first class-0x03 function (the dGPU) or -1 */
static struct { int n, vga, bound_any, nouveau_bound; struct dfn f[MAXFN]; char sig[512]; } g_inv = {.vga = -1};
static char g_port[16];                           /* root port of the dGPU, cached at first sight this boot */

/* vendor/class/device/resource are read once per device appearance (INTERFACES 10.1) */
struct pcent { char bdf[16], did[12]; unsigned vendor, cls; int bar_ok, gen; };
#define PCMAX 96
static struct pcent g_pc[PCMAX];
static int g_pcn, g_pcgen;

static int bar_nonzero(const char *dev) {
  char p[PATH_MAX + 32], b[256];
  snprintf(p, sizeof p, "%s/resource", dev);
  if (read_text(p, b, sizeof b) <= 0) return 0;
  return strtoull(b, NULL, 16) != 0;
}
static int fn_cmp(const void *a, const void *b) { return strcmp(((const struct dfn *)a)->bdf, ((const struct dfn *)b)->bdf); }
static const char *rs_port(void) { return g_port[0] ? g_port : "0000:00:1d.4"; }

static void inventory(void) {
  char dir[PATH_MAX], dev[PATH_MAX + 32], p[PATH_MAX + 64], v[32], real[PATH_MAX];
  g_pcgen++;
  g_inv.n = g_inv.bound_any = g_inv.nouveau_bound = 0;
  g_inv.vga = -1;
  DIR *d = opendir(rpath(dir, sizeof dir, "/sys/bus/pci/devices"));
  if (d) {
    struct dirent *e;
    while ((e = readdir(d))) {
      if (e->d_name[0] == '.' || strlen(e->d_name) >= 16) continue;
      snprintf(dev, sizeof dev, "%s/%s", dir, e->d_name);
      int i;
      for (i = 0; i < g_pcn && strcmp(g_pc[i].bdf, e->d_name); i++) {}
      struct pcent tmp, *c = i < g_pcn ? &g_pc[i] : &tmp;
      if (i == g_pcn) {
        memset(&tmp, 0, sizeof tmp);
        snprintf(tmp.bdf, sizeof tmp.bdf, "%s", e->d_name);
        snprintf(p, sizeof p, "%s/vendor", dev);
        int ok = read_text(p, v, sizeof v) > 0;
        tmp.vendor = ok ? (unsigned)strtoul(v, NULL, 16) : 0;
        if (tmp.vendor == 0x10de) {
          snprintf(p, sizeof p, "%s/class", dev);
          if (read_text(p, v, sizeof v) > 0) tmp.cls = (unsigned)strtoul(v, NULL, 16); else ok = 0;
          snprintf(p, sizeof p, "%s/device", dev);
          if (read_text(p, tmp.did, sizeof tmp.did) < 0) strcpy(tmp.did, "?");
          tmp.bar_ok = bar_nonzero(dev);
        }
        if (ok && g_pcn < PCMAX) { g_pc[g_pcn] = tmp; c = &g_pc[g_pcn++]; }   /* unreadable: read again next time */
      }
      c->gen = g_pcgen;
      if (c->vendor != 0x10de || g_inv.n >= MAXFN) continue;
      struct dfn *f = &g_inv.f[g_inv.n++];
      memset(f, 0, sizeof *f);
      snprintf(f->bdf, sizeof f->bdf, "%s", c->bdf);
      snprintf(f->did, sizeof f->did, "%s", c->did);
      f->cls = c->cls;
      f->vga = (c->cls >> 16) == 0x03;
      f->bar_ok = c->bar_ok;
      snprintf(p, sizeof p, "%s/driver", dev);
      link_base(p, f->drv, sizeof f->drv);
    }
    closedir(d);
  }
  for (int i = 0; i < g_pcn;) {                   /* gone: a re-appearance reads vendor/class/resource again */
    if (g_pc[i].gen != g_pcgen) g_pc[i] = g_pc[--g_pcn];
    else i++;
  }
  qsort(g_inv.f, g_inv.n, sizeof g_inv.f[0], fn_cmp);
  size_t k = 0;
  g_inv.sig[0] = 0;
  for (int i = 0; i < g_inv.n; i++) {
    const struct dfn *f = &g_inv.f[i];
    if (f->drv[0]) g_inv.bound_any = 1;
    if (g_inv.vga < 0 && f->vga) g_inv.vga = i;
    if (k < sizeof g_inv.sig) k += snprintf(g_inv.sig + k, sizeof g_inv.sig - k, "%s=%s;", f->bdf, f->drv);
  }
  if (g_inv.vga >= 0) {
    const struct dfn *f = &g_inv.f[g_inv.vga];
    g_inv.nouveau_bound = !strcmp(f->drv, "nouveau");
    snprintf(dev, sizeof dev, "%s/%s", dir, f->bdf);
    if (!g_port[0] && realpath(dev, real)) {
      char *s = strrchr(real, '/');
      if (s && s != real) {
        *s = 0;
        const char *b = strrchr(real, '/');
        if (b && is_bdf(b + 1)) { snprintf(g_port, sizeof g_port, "%s", b + 1); LOGI("dGPU %s: root port %s", f->bdf, g_port); }
      }
    }
  }
}

static int nouveau_loaded(void) { return path_exists("/sys/module/nouveau"); }
static int nouveau_refcnt(void) {
  char p[PATH_MAX], v[16];
  if (read_text(rpath(p, sizeof p, "/sys/module/nouveau/refcnt"), v, sizeof v) <= 0 || !is_num(v)) return -1;
  return atoi(v);
}
/* The refcnt gate (invariants 7, 10): 0 = nobody holds nouveau (module absent, or loaded with refcnt 0). An unreadable
 * refcnt while the module is loaded counts as busy (fail closed), like gb_nouveau.sh off. */
static int refcnt_busy(void) {
  if (!nouveau_loaded()) return 0;
  int rc = nouveau_refcnt();
  return rc == 0 ? 0 : rc > 0 ? rc : 1;
}

/* ------------------------------------------------------------------------------------------------ per-boot flags */
/* used/foreign: property OR root-only file /dev/gb_dgpu/<flag>; wedged: file + memory. Monotonic (INTERFACES 4). */
static int g_used, g_wedged, g_removed, g_shutdown, g_cmd_kill;
static int g_cmd_test, g_cmd_once;                /* the last gb.nouveau= is test / once (v1.2) */
static char g_fault[16];                          /* "" | rpm-error | d0-idle | rescan | bar | rebind | selftest-hang */
static char g_boot[16];                           /* gb.boot= */
static int g_bcast_pending;                       /* a state broadcast to GbGpu is due (10.7) */
static long long g_bcast_last = -100000;

/* ---- v1.2 graphics switching state (INTERFACES 22-26); the functions are in section G further down */
enum { GM_AUTO, GM_INTEL, GM_NVIDIA };
static const char *const g_gm_names[] = {"auto", "intel", "nvidia"};
enum { GPOL_GAMES, GPOL_ALL };
static struct {
  int cap, mode, policy;                          /* 23.1 inputs, re-read every tick and by the commands */
  int active, seq;                                /* in-memory gfx_active (1 = nvidia), gfx_seq */
  int n16;                                        /* N16 due: base attach event, or a self-test pass */
  int forced;                                     /* gb_power forced Intel this boot (rebind failure, unclean sessions) */
  int data_seen, settings_boot, last_all_apps, detach_ref, ui_notify, boot_bcast;
  int sess_checked;                               /* (R-15) the session start checks ran (persistent props loaded) */
  char reason[16], test[12];
  char build[100], krel[100], gfx[520];           /* self-test identity (25.1) */
} g_gfx = {.policy = -1, .last_all_apps = -1, .reason = "nopkg", .test = "none"};
static struct { int have, job, stale; } g_sess;   /* gfx_session: on disk; J_MARK pending (1 mark, 2 unmark); earlier boot */
static struct { int have, job, stale; } g_dsess;  /* (R-4) dgpu_session: the same for "nouveau bound" (23.10a) */
static int g_shut_marks;                          /* (R-1) the clean-shutdown marker removal ran */
struct strec {                                    /* self-test record (25.4) */
  int valid, attempts, crashes;
  long long t;
  char verdict[8], reason[48], build[100], krel[100], gfx[520], report[48];
};
static struct {
  int dir_seen, inited, running, seen_running, want, request, retried, clean_seen, bound_prev, result_stale;
  int stop_sent, marker_check;
  long long started_at, stopped_at, stop_sent_at, snap_t;
  char snap_verdict[8], tried_id[720], n18_id[720];
  struct strec rec;                               /* last validated record (identity compared on use) */
  long long rec_mt, rec_sz, rec_ino;
  int rec_stat;                                   /* the stat of the record file the cache was made from (1 = file) */
} g_st = {.snap_t = -1};
enum { RB_NONE, RB_WAIT, RB_SEEN, RB_CLEAN };     /* rebind guard (23.8) */
static struct {
  int phase, locked, own_marker, mark_ok, mark_job, off_seen;   /* off_seen: gb_nouveau_off ran during the watch (R-2) */
  long long armed_at, seen_at;
  char lock_line[96], why[24], mark_line[128];    /* mark_line: the crash marker line gb_power wrote (R-12) */
} g_rb;
static struct { int kind, ok_reads; long long deadline, next_read; } g_pend;   /* pending detach (23.9) */
static struct {                                   /* hot attach and reconcile_on (23.7, R-3) */
  int inited, present_prev, appear, deferred, starts, started_self, cap_logged, on_logged;
  long long busy_at, last_start;                  /* last tick gb_nouveau or gb_nouveau_off ran; gb_power's last start */
} g_nv;
static struct { int zero_ticks; long long last_start; } g_off;                 /* reconcile_off (23.6) */
static int rb_pending(void) { return g_rb.phase == RB_WAIT || g_rb.phase == RB_SEEN; }
static int rb_active(void) { return g_rb.phase != RB_NONE; }

static int flag_exists(const char *name) {
  char p[PATH_MAX];
  return !access(rpath(p, sizeof p, "/dev/gb_dgpu/%s", name), F_OK);
}
static void flag_make(const char *name) {
  char p[PATH_MAX];
  rpath(p, sizeof p, "/dev/gb_dgpu");
  if (mkdir(p, 0700) && errno != EEXIST) { LOGW("mkdir %s: %s", p, strerror(errno)); return; }
  rpath(p, sizeof p, "/dev/gb_dgpu/%s", name);
  int fd = open(p, O_WRONLY | O_CREAT | O_NOFOLLOW | O_CLOEXEC, 0600);
  if (fd < 0) { LOGW("%s: %s", p, strerror(errno)); return; }
  if (write(fd, "1\n", 2) != 2) LOGW("%s: short write", p);
  close(fd);
}
/* /dev/gb_dgpu/detaching (INTERFACES 5.2): NOT monotonic. gb_power creates it when it starts handling a detach request
 * and removes it when the EC process is over (ec_ready). gb_nouveau.sh refuses insmod while it exists. */
static int g_detflag = -1;
static void detaching_flag(int on) {
  if (on == g_detflag) return;
  char p[PATH_MAX];
  if (on) flag_make("detaching");
  else if (unlink(rpath(p, sizeof p, "/dev/gb_dgpu/detaching")) && errno != ENOENT) LOGW("%s: %s", p, strerror(errno));
  g_detflag = on;
}
/* INTERFACES 5.4 / 10.3: gb_nouveau.sh holds the ESP lock for its whole load window (payload staging .. health check)
 * and writes "<pid> gb_nouveau <uptime>" into the root-only owner file. Properties are untrusted, so this is read. */
#define ESP_LOCK "/dev/gb_esp.lock"
static int pid_alive(int pid) { char p[PATH_MAX]; return pid > 0 && !access(rpath(p, sizeof p, "/proc/%d", pid), F_OK); }
/* 1 = a live process holds the lock (name = its owner name), 0 = stale (dead pid), -1 = no owner file, -2 = garbage */
static int esp_owner(char *name, size_t n, int *pid_out, char *line, size_t ln) {
  char p[PATH_MAX], b[128], nm[32] = "";
  int pid = 0;
  name[0] = 0;
  if (line) line[0] = 0;
  if (read_text(rpath(p, sizeof p, ESP_LOCK "/owner"), b, sizeof b) <= 0) return -1;
  if (line) snprintf(line, ln, "%s", b);
  if (sscanf(b, "%d %31s", &pid, nm) != 2 || pid <= 0) return -2;
  if (pid_out) *pid_out = pid;
  snprintf(name, n, "%s", nm);
  return pid_alive(pid) ? 1 : 0;
}
/* gb_nouveau.sh's load window: a live owner named gb_nouveau */
static int nouveau_window_open(void) {
  char nm[32];
  return esp_owner(nm, sizeof nm, NULL, NULL, 0) == 1 && !strcmp(nm, "gb_nouveau");
}
/* (v1.2) also while gb_power's own rebind guard watches the kernel's re-probe (23.8, review B7): detach -> N15 */
static int load_window_open(void) { return nouveau_window_open() || rb_pending(); }
static long long uptime_s(void) {
  char p[PATH_MAX], b[64];
  if (read_text(rpath(p, sizeof p, "/proc/uptime"), b, sizeof b) > 0) return atoll(b);
  return now_ms() / 1000;
}
static void boot_id(char *out, size_t n) {
  char p[PATH_MAX];
  if (read_text(rpath(p, sizeof p, "/proc/sys/kernel/random/boot_id"), out, n) <= 0) snprintf(out, n, "unknown");
}
/* ESP lock without waiting (5.4, rebind guard 23.8): 1 = acquired (line = the owner line written), 0 = a live process
 * (or one that is just taking it) holds it, -1 = error. A stale lock (dead owner pid, garbage owner) is stolen with the
 * atomic rename of 5.4: rename away, check inode + owner line, remove; a re-created lock is put back. */
static int esp_lock_try(char *line, size_t ln) {
  char d[PATH_MAX], o[PATH_MAX + 8], sd[PATH_MAX + 32], so[PATH_MAX + 48];
  rpath(d, sizeof d, ESP_LOCK);
  snprintf(o, sizeof o, "%s/owner", d);
  snprintf(sd, sizeof sd, "%s.stale.%d", d, (int)getpid());
  snprintf(so, sizeof so, "%s/owner", sd);
  line[0] = 0;
  for (int round = 0; round < 3; round++) {
    if (!mkdir(d, 0700)) {
      char b[112];
      snprintf(line, ln, "%d gb_power %lld", (int)getpid(), uptime_s());
      int l = snprintf(b, sizeof b, "%s\n", line);
      int fd = open(o, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
      int ok = fd >= 0 && write(fd, b, l) == l;
      if (fd >= 0 && close(fd)) ok = 0;
      if (!ok) { LOGW("ESP lock: owner file: %s", strerror(errno)); unlink(o); rmdir(d); line[0] = 0; return -1; }
      return 1;
    }
    if (errno != EEXIST) { LOGW("ESP lock: mkdir %s: %s", d, strerror(errno)); return -1; }
    char nm[32], cur[128];
    int pid = 0, r = esp_owner(nm, sizeof nm, &pid, cur, sizeof cur);
    if (r == 1) { LOGI("ESP lock held by %s (pid %d): going on without it (the kernel probe cannot be held back)", nm, pid); return 0; }
    if (r == -1) { LOGI("ESP lock being taken (no owner yet): going on without it"); return 0; }
    struct stat st, s2;
    if (stat(d, &st)) continue;                   /* released meanwhile: mkdir again */
    unlink(so);                                   /* an own leftover of an earlier steal */
    rmdir(sd);
    if (rename(d, sd)) continue;                  /* somebody else stole or released it: mkdir again */
    char again[128] = "";
    read_text(so, again, sizeof again);
    if (!stat(sd, &s2) && s2.st_ino == st.st_ino && !strcmp(again, cur)) {
      LOGI("ESP lock: stale owner '%s' removed", cur);
      unlink(so);
      rmdir(sd);
      continue;
    }
    if (rename(sd, d)) LOGW("ESP lock: could not put back a re-created lock: %s", strerror(errno));
    return 0;
  }
  return 0;
}
static void esp_unlock(const char *line) {
  char p[PATH_MAX], cur[128] = "";
  if (!line || !line[0]) return;
  if (read_text(rpath(p, sizeof p, ESP_LOCK "/owner"), cur, sizeof cur) > 0 && !strcmp(cur, line)) {
    unlink(p);
    if (rmdir(rpath(p, sizeof p, ESP_LOCK))) LOGW("ESP lock: rmdir: %s", strerror(errno));
  } else {
    LOGW("ESP lock is not ours any more (owner '%s'): left alone", cur);
  }
}
static void mark_used(const char *why) {
  if (!g_used) {
    g_used = 1;
    LOGI("dGPU: nouveau used this boot (%s): a detach removes every NVIDIA function, attach/cancel rescans", why);
  }
  if (!flag_exists("used")) flag_make("used");
  prop_put("vendor.gb.nouveau.used", "1");
}
static int used(void) {
  if (g_used) return 1;
  if (!prop_is("vendor.gb.nouveau.used", "1") && !flag_exists("used")) return 0;
  mark_used("flag set by gb_nouveau.sh");
  return 1;
}
static int foreign(void) { return prop_is("vendor.gb.nouveau.foreign", "1") || flag_exists("foreign"); }
static int wedged(void) {
  if (!g_wedged && flag_exists("wedged")) {
    g_wedged = 1;
    LOGE("dGPU wedged earlier this boot (/dev/gb_dgpu/wedged): no dGPU writes until restart");
  }
  return g_wedged;
}
static void set_wedged(const char *why) {
  if (!g_wedged) LOGE("dGPU wedged: %s; no further dGPU writes this boot, restart required", why);
  g_wedged = 1;
  flag_make("wedged");
}
/* (v1.2) the P8 gate vendor.gb.gfxdriver + /dev/gb_dgpu/gfxdriver is retired (gfxdriver_on() removed): render 0666
 * comes only from gb_power's own derivation (INTERFACES 7.5, 23.5). */

static int svc_running(const char *name) {        /* init.svc.<name> is running (or stopping/restarting) */
  char k[64], v[PROP_VALUE_MAX];
  snprintf(k, sizeof k, "init.svc.%s", name);
  prop_get(k, v);
  return !strcmp(v, "running") || !strcmp(v, "stopping") || !strcmp(v, "restarting");
}
/* Root services that open the nouveau render node: stopped before every detach decision (INTERFACES 10.3 step 4) and
 * by gb_nouveau.sh off. (v1.2) gb_gfx_selftest joins them. gb_gpu_compute last (tests and logs key on it). */
static const char *const g_gpu_users[] = {"gb_dgpu_test", "gb_nvk_probe", "gb_gfx_selftest", "gb_gpu_compute"};
static void gpu_users_stop(void) {
  for (size_t i = 0; i < sizeof g_gpu_users / sizeof g_gpu_users[0]; i++) prop_set("ctl.stop", g_gpu_users[i]);
}
static const char *gpu_user_running(void) {        /* first of them whose init.svc is still running/stopping */
  for (size_t i = 0; i < sizeof g_gpu_users / sizeof g_gpu_users[0]; i++)
    if (svc_running(g_gpu_users[i])) return g_gpu_users[i];
  return NULL;
}
/* the NVIDIA driver stack is on this image (native, legacy module or payload) */
static int stack_present(void) {
  char hw[32], p[PATH_MAX];
  if (read_text(rpath(p, sizeof p, "/odm/etc/gb_hw"), hw, sizeof hw) <= 0 || strcmp(hw, "native")) return 0;
  return path_exists("/odm/lib/modules/nouveau.ko") || path_exists("/odm/etc/gb/dgpu_payload.sha256");
}
static int data_dir_ok(void) {
  char p[PATH_MAX];
  struct stat st;
  return !stat(rpath(p, sizeof p, "/data/vendor/gb"), &st) && S_ISDIR(st.st_mode);
}

/* ------------------------------------------------------------------------------------------------ worker */
/* Every dGPU sysfs write that can block runs in a forked child: it performs the ops in order and reports "<i> <res>"
 * lines through a pipe. One job at a time, FIFO queue behind it. A missed deadline = wedged; the child is left alone. */
enum { OP_UNBIND, OP_REMOVE, OP_WRITE, OP_MARK, OP_UNMARK };   /* MARK/UNMARK: crash + session markers (v1.2) */
enum { J_UNBIND = 1, J_DETACH, J_PORT_ON, J_RESCAN, J_HOLD_ON, J_IOMMU, J_DREMOVE, J_MARK };
static const char *job_name(int k) {
  static const char *const n[] = {"?", "unbind", "detach: unbind", "rescan: root port on", "rescan",
                                  "power hold: power on", "iommu domain", "detach: remove", "marker"};
  return k >= 1 && k <= J_MARK ? n[k] : "?";
}
static const char *op_name(int k) {
  return k == OP_UNBIND ? "unbind" : k == OP_REMOVE ? "remove" : k == OP_MARK ? "mark" : k == OP_UNMARK ? "unmark" : "write";
}
/* J_MARK purposes: the app-graphics session marker (23.10) and the rebind guard's crash marker (23.8) */
enum { MK_NONE, MK_SESS, MK_UNSESS, MK_SESS_STALE, MK_RB, MK_UNRB, MK_DSESS, MK_UNDSESS, MK_DSESS_STALE };   /* (R-4) dgpu_session */
struct wop { int kind, res; char path[400], val[128], bdf[16]; };
#define JOPS (2 * MAXFN + 2)
struct job { int kind, tag, nops, deadline_ms; struct wop op[JOPS]; };
#define JQ 6
static struct { struct job q[JQ]; int qn; struct job cur; int busy, fd, got; pid_t pid; long long t0; char lb[64]; size_t ll; } g_wk = {.fd = -1};

static int real_op_write(int kind, const char *path, const char *val) { (void)kind; return write_text(path, val); }
static int (*g_op_write)(int kind, const char *path, const char *val) = real_op_write;   /* runs in the child */
/* OP_MARK: write the marker (O_NOFOLLOW, 0600) and fsync it and its directory; OP_UNMARK: unlink (ENOENT = done) and
 * fsync the directory. Runs in the worker child (INTERFACES 23.3, 23.8): no fsync in the main process. */
static int real_mark_op(int kind, const char *path, const char *val) {
  char dir[PATH_MAX];
  snprintf(dir, sizeof dir, "%s", path);
  char *s = strrchr(dir, '/');
  if (!s || s == dir) return -EINVAL;
  *s = 0;
  if (kind == OP_MARK) {
    char b[200];
    int l = snprintf(b, sizeof b, "%s\n", val), e = 0;
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (fd < 0) return -errno;
    if (write(fd, b, l) != l) e = -EIO;
    if (!e && fchmod(fd, 0600)) e = -errno;
    if (!e && fsync(fd)) e = -errno;
    if (close(fd) && !e) e = -errno;
    if (e) return e;
  } else {
    if (val && val[0]) {                          /* (R-12) only the marker line we wrote: another window's is left */
      char cur[200] = "";
      int fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
      if (fd >= 0) {
        ssize_t r = read(fd, cur, sizeof cur - 1);
        close(fd);
        cur[r > 0 ? r : 0] = 0;
        size_t l = strlen(cur);
        while (l && (cur[l - 1] == '\n' || cur[l - 1] == '\r')) cur[--l] = 0;
        if (strcmp(cur, val)) return -ESTALE;
      } else if (errno != ENOENT) {
        return -errno;
      }
    }
    if (unlink(path) && errno != ENOENT) return -errno;
  }
  int dfd = open(dir, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (dfd < 0) return -errno;
  int e = fsync(dfd) ? -errno : 0;
  close(dfd);
  return e;
}
static int (*g_mark_op)(int kind, const char *path, const char *val) = real_mark_op;   /* child; tests inject errors */
static void (*g_on_op)(const struct wop *o) = NULL;   /* unit tests: parent-side record of each op result */

static void job_add(struct job *j, int kind, const char *path, const char *val, const char *bdf) {
  if (j->nops >= JOPS) return;
  struct wop *o = &j->op[j->nops++];
  memset(o, 0, sizeof *o);
  o->kind = kind;
  o->res = 1;                                     /* 1 = no result (not run, or stuck) */
  snprintf(o->path, sizeof o->path, "%s", path);
  snprintf(o->val, sizeof o->val, "%s", val);
  snprintf(o->bdf, sizeof o->bdf, "%s", bdf);
}
static int op_ok(const struct wop *o) { return o->res == 0 || o->res == -ENOENT || o->res == -ENODEV; }

/* Runs in the worker child right before each unbind/remove write: the refcnt gate is re-checked at the last moment
 * (INTERFACES 10.3), because a root process can open a nouveau node despite chmod 000. -EBUSY = gate refused:
 *   unbind of a function bound to nouveau: /sys/module/nouveau/refcnt must read 0;
 *   remove: the function must be driverless, and nouveau absent or at refcnt 0. */
static int op_gate(const struct wop *o) {
  char p[PATH_MAX], drv[48];
  if (o->kind == OP_WRITE || o->kind == OP_MARK || o->kind == OP_UNMARK) return 0;   /* not dGPU writes */
  if (access(o->path, F_OK)) return 0;            /* a missing attribute: the write reports it */
  link_base(rpath(p, sizeof p, "/sys/bus/pci/devices/%s/driver", o->bdf), drv, sizeof drv);
  if (o->kind == OP_UNBIND && strcmp(drv, "nouveau")) return 0;
  if (o->kind == OP_REMOVE && drv[0]) return -EBUSY;
  return refcnt_busy() ? -EBUSY : 0;
}

static void job_done(const struct job *j, int wedge);
static void child_close_fds(void);

static int worker_start(void) {
  int pfd[2];
  if (pipe2(pfd, O_CLOEXEC)) { LOGE("worker: pipe: %s", strerror(errno)); return -1; }
  pid_t pid = fork();
  if (pid < 0) { LOGE("worker: fork: %s", strerror(errno)); close(pfd[0]); close(pfd[1]); return -1; }
  if (pid == 0) {
    sigset_t none;
    sigemptyset(&none);
    sigprocmask(SIG_SETMASK, &none, NULL);
    signal(SIGTERM, SIG_DFL);
    close(pfd[0]);
    child_close_fds();
    int skip = 0;
    for (int i = 0; i < g_wk.cur.nops; i++) {
      const struct wop *o = &g_wk.cur.op[i];
      int r;
      if (skip) {
        r = -ECANCELED;                           /* a failed marker: what it guards is not done (J_RESCAN) */
      } else if (o->kind == OP_MARK || o->kind == OP_UNMARK) {
        r = g_mark_op(o->kind, o->path, o->val);
        if (r && o->kind == OP_MARK) skip = 1;
      } else {
        r = op_gate(o);
        if (!r) r = g_op_write(o->kind, o->path, o->kind == OP_UNBIND ? o->bdf : o->val);
      }
      char l[32];
      int n = snprintf(l, sizeof l, "%d %d\n", i, r);
      if (write(pfd[1], l, n) != n) break;
    }
    _exit(0);
  }
  close(pfd[1]);
  fcntl(pfd[0], F_SETFL, O_NONBLOCK);
  g_wk.busy = 1;
  g_wk.pid = pid;
  g_wk.fd = pfd[0];
  g_wk.t0 = now_ms();
  g_wk.got = 0;
  g_wk.ll = 0;
  LOGI("worker %d: %s (%d writes, deadline %d s)", (int)pid, job_name(g_wk.cur.kind), g_wk.cur.nops,
       g_wk.cur.deadline_ms / 1000);
  return 0;
}

static void worker_next(void) {
  while (!g_wk.busy && g_wk.qn) {
    g_wk.cur = g_wk.q[0];
    memmove(&g_wk.q[0], &g_wk.q[1], (g_wk.qn - 1) * sizeof g_wk.q[0]);
    g_wk.qn--;
    if (worker_start()) {
      struct job failed = g_wk.cur;
      job_done(&failed, 0);
    }
  }
}

static int (*g_submit_hook)(const struct job *j) = NULL;   /* unit tests (derivation matrix): record, do not fork */
static int worker_submit(const struct job *j) {
  if (g_submit_hook) return g_submit_hook(j);
  /* markers are files on /data, not dGPU writes: they still run after a wedge (a session must be closable) */
  if (wedged() && j->kind != J_MARK) { LOGW("dGPU wedged: %s not run", job_name(j->kind)); return -1; }
  if (g_wk.busy) {
    if (g_wk.qn == JQ) { LOGE("worker queue full: %s dropped", job_name(j->kind)); return -1; }
    g_wk.q[g_wk.qn++] = *j;
    return 0;
  }
  g_wk.cur = *j;
  return worker_start();
}
static int worker_has(int kind) {
  if (g_wk.busy && g_wk.cur.kind == kind) return 1;
  for (int i = 0; i < g_wk.qn; i++)
    if (g_wk.q[i].kind == kind) return 1;
  return 0;
}

static void worker_line(const char *l) {
  int i, r;
  if (sscanf(l, "%d %d", &i, &r) != 2 || i < 0 || i >= g_wk.cur.nops) return;
  struct wop *o = &g_wk.cur.op[i];
  o->res = r;
  g_wk.got++;
  if (op_ok(o)) LOGD("worker: %s %s ok", op_name(o->kind), o->path);
  else LOGW("worker: %s %s%s%s: %s", op_name(o->kind), o->path, o->kind == OP_WRITE ? " = " : "",
            o->kind == OP_WRITE ? o->val : "", strerror(-r));
  if (g_on_op) g_on_op(o);
}

static void worker_read(void) {
  for (;;) {
    char buf[256];
    ssize_t r = read(g_wk.fd, buf, sizeof buf);
    if (r < 0 && errno == EINTR) continue;
    if (r < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return;
    if (r <= 0) break;                            /* EOF: the child is done (or died) */
    for (ssize_t i = 0; i < r; i++) {
      if (buf[i] == '\n') { g_wk.lb[g_wk.ll] = 0; worker_line(g_wk.lb); g_wk.ll = 0; }
      else if (g_wk.ll < sizeof g_wk.lb - 1) g_wk.lb[g_wk.ll++] = buf[i];
    }
  }
  close(g_wk.fd);
  g_wk.fd = -1;
  int st;
  if (waitpid(g_wk.pid, &st, WNOHANG) <= 0) {}    /* not exited yet: reaped later by the tick */
  g_wk.busy = 0;
  struct job done = g_wk.cur;
  LOGD("worker %d done: %d/%d results", (int)g_wk.pid, g_wk.got, done.nops);
  worker_next();                                  /* older queued jobs first */
  job_done(&done, 0);
}

static int dec_running(void);
static void worker_deadline(long long t) {
  if (!g_wk.busy || t - g_wk.t0 < g_wk.cur.deadline_ms) return;
  if (g_wk.cur.kind == J_MARK) {                  /* a stuck fsync on /data: not a dGPU wedge */
    LOGE("worker %d (%s) missed its %d s deadline (storage, not the dGPU): left alone, marker treated as not written",
         (int)g_wk.pid, job_name(g_wk.cur.kind), g_wk.cur.deadline_ms / 1000);
    close(g_wk.fd);
    g_wk.fd = -1;
    g_wk.busy = 0;
    struct job done = g_wk.cur;
    worker_next();
    job_done(&done, 1);
    return;
  }
  LOGE("worker %d (%s) missed its %d s deadline: left alone (it may be stuck in the kernel)", (int)g_wk.pid,
       job_name(g_wk.cur.kind), g_wk.cur.deadline_ms / 1000);
  close(g_wk.fd);
  g_wk.fd = -1;
  g_wk.busy = 0;
  struct job done = g_wk.cur;
  int in_decision = dec_running();
  set_wedged(job_name(done.kind));
  if (!in_decision)
    notify("gb_dgpu", "Restart required", "The NVIDIA GPU stopped responding. Restart before detaching the screen.");
  job_done(&done, 1);
  struct job q[JQ];
  int qn = g_wk.qn;
  memcpy(q, g_wk.q, sizeof q);
  g_wk.qn = 0;
  for (int i = 0; i < qn; i++) job_done(&q[i], 1);   /* queued jobs never run: no further dGPU writes */
}

/* ------------------------------------------------------------------------------------------------ DRM nodes */
#define AID_GRAPHICS 1003
static int real_chown(const char *p, uid_t u, gid_t g) { return chown(p, u, g) ? -errno : 0; }
static int (*g_chown)(const char *p, uid_t u, gid_t g) = real_chown;   /* unit tests (non-root) record instead */
static struct { char name[24]; ino_t ino; } g_np[4];   /* nodes whose permissions we applied (by inode) */
static int g_npn;

/* nouveau DRM nodes: /sys/class/drm/{card,renderD}<n> whose device/driver is nouveau (INTERFACES 2.2) */
static int nouveau_nodes(char names[][24], int max) {
  char dir[PATH_MAX], p[PATH_MAX + 300], drv[48];
  DIR *d = opendir(rpath(dir, sizeof dir, "/sys/class/drm"));
  int n = 0;
  if (!d) return 0;
  struct dirent *e;
  while ((e = readdir(d)) && n < max) {
    const char *s = !strncmp(e->d_name, "renderD", 7) ? e->d_name + 7 : !strncmp(e->d_name, "card", 4) ? e->d_name + 4 : NULL;
    if (!s || !is_num(s) || strlen(e->d_name) >= 24) continue;
    snprintf(p, sizeof p, "%s/%s/device/driver", dir, e->d_name);
    link_base(p, drv, sizeof drv);
    if (strcmp(drv, "nouveau")) continue;
    snprintf(names[n++], 24, "%s", e->d_name);
  }
  closedir(d);
  return n;
}
static void np_record(const char *name, ino_t ino) {
  int i;
  for (i = 0; i < g_npn && strcmp(g_np[i].name, name); i++) {}
  if (i == g_npn) { if (g_npn == 4) i = 0; else g_npn++; }
  snprintf(g_np[i].name, sizeof g_np[i].name, "%s", name);
  g_np[i].ino = ino;
}
/* INTERFACES 7.5 (v1.2): render 0666 root:graphics only while gb_power's own in-memory gfx_active is nvidia (23.2),
 * else 0660; card 0600 root:root */
static mode_t node_mode_want(const char *name) { return !strncmp(name, "renderD", 7) ? (g_gfx.active ? 0666 : 0660) : 0600; }
static void node_set(const char *name, mode_t m) {
  char p[PATH_MAX];
  struct stat st;
  rpath(p, sizeof p, "/dev/dri/%s", name);
  int render = !strncmp(name, "renderD", 7);
  int r = g_chown(p, 0, render ? AID_GRAPHICS : 0);
  if (r && r != -ENOENT) LOGW("chown %s: %s", p, strerror(-r));
  if (chmod(p, m)) { if (errno != ENOENT) LOGW("chmod %s: %s", p, strerror(errno)); return; }
  if (!stat(p, &st)) np_record(name, st.st_ino);
  trace("chmod:%s=%04o", name, (unsigned)m);
  LOGI("nouveau node %s: %s %04o", name, render ? "root:graphics" : "root:root", (unsigned)m);
}
static void node_perms(const char *name) { node_set(name, node_mode_want(name)); }
/* (v1.2, 23.5; replaces nodes_apply_new) stat() only, never an open (PLAN inv. 12): a new inode is set to the wanted
 * mode E, a node more permissive than E is lowered to E, a render node at 0660 is raised to 0666 only while E is
 * 0666, and a node at 0000 is never raised here (it belongs to a decision, an off window or a wedge).
 * (R-5) The lowering and the new-inode rule always apply; only the 0660 -> 0666 raise is skipped (nodes_enforce_skip)
 * while a live gb_nouveau owns the ESP lock (its apply_perms window) and for 30 s after gb_power itself started
 * gb_nouveau_off while that service reads running. A detach decision owns the nodes (000, nodes_lock) and is never
 * touched; after a wedge every nouveau node is chmod 000 (a /dev chmod, not a dGPU write: nothing may open it). */
static int nodes_enforce_skip(void) {
  if (dec_running() || wedged() || nouveau_window_open()) return 1;
  return g_off.last_start > 0 && now_ms() - g_off.last_start < OFF_SKIP_MS && svc_running("gb_nouveau_off");
}
static int nodes_lock(void);
static void nodes_enforce(void) {
  if (dec_running()) return;
  if (wedged()) {
    int k = nodes_lock();
    if (k) LOGW("dGPU wedged: %d nouveau node(s) chmod 000 (nothing may open a wedged GPU)", k);
    return;
  }
  int noraise = nodes_enforce_skip();
  char names[4][24], p[PATH_MAX];
  int n = nouveau_nodes(names, 4);
  for (int i = 0; i < n; i++) {
    struct stat st;
    if (stat(rpath(p, sizeof p, "/dev/dri/%s", names[i]), &st)) continue;
    mode_t m = st.st_mode & 07777, e = node_mode_want(names[i]);
    if (!m) continue;
    if (noraise && e == 0666) e = 0660;           /* (never while gfx_active is nvidia in practice: 23.2 row 11) */
    int k;
    for (k = 0; k < g_npn && (strcmp(g_np[k].name, names[i]) || g_np[k].ino != st.st_ino); k++) {}
    if (k == g_npn) node_set(names[i], e);                       /* new inode (rescan, ueventd: 0666) */
    else if (m & ~e) node_set(names[i], e);                      /* more permissive than wanted */
    else if (!noraise && e == 0666 && m == 0660) node_set(names[i], e);   /* nvidia derived by gb_power itself */
  }
}
/* chmod 000 every nouveau node; during a decision also every node that appears later (a rebind or ueventd re-creation
 * gives a fresh 0666 node), so only nodes not already 000 are touched and logged. (R-5) Also after a wedge. */
static int nodes_lock(void) {
  char names[4][24], p[PATH_MAX];
  int n = nouveau_nodes(names, 4), k = 0;
  for (int i = 0; i < n; i++) {
    struct stat st;
    rpath(p, sizeof p, "/dev/dri/%s", names[i]);
    if (!stat(p, &st) && !(st.st_mode & 07777)) continue;
    if (chmod(p, 0)) { if (errno != ENOENT) LOGW("chmod 000 %s: %s", p, strerror(errno)); continue; }
    trace("chmod:%s=0000", names[i]);
    k++;
  }
  if (k && dec_running()) LOGI("detach: %d nouveau node(s) chmod 000 (no new opens)", k);
  return k;
}
static void nodes_restore(void) {
  char names[4][24];
  int n = nouveau_nodes(names, 4);
  for (int i = 0; i < n; i++) node_perms(names[i]);
}

/* ------------------------------------------------------------------------------------------------ packages.list */
#define PKGMAX 1024
static struct {
  int n, ok, gen;                                 /* gen: +1 on every re-read (a changed packages.list, 23.11) */
  long long mt, sz, checked;
  struct { char name[128]; int appid; } e[PKGMAX];
  int idx[PKGMAX];                                /* e[] sorted by name (bsearch) */
} g_pkgs;
static void allow_validate(void);
static int pkg_idx_cmp(const void *a, const void *b) { return strcmp(g_pkgs.e[*(const int *)a].name, g_pkgs.e[*(const int *)b].name); }
static int pkg_installed(const char *pkg) {
  int lo = 0, hi = g_pkgs.ok ? g_pkgs.n - 1 : -1;
  while (lo <= hi) {
    int mid = (lo + hi) / 2, c = strcmp(g_pkgs.e[g_pkgs.idx[mid]].name, pkg);
    if (!c) return 1;
    if (c < 0) lo = mid + 1;
    else hi = mid - 1;
  }
  return 0;
}

static void pkgs_refresh(int force) {
  long long t = now_ms();
  if (!force && g_pkgs.checked && t - g_pkgs.checked < 5000) return;   /* re-read at most every 5 s */
  g_pkgs.checked = t;
  char p[PATH_MAX];
  struct stat st;
  if (stat(rpath(p, sizeof p, "/data/system/packages.list"), &st)) { g_pkgs.ok = 0; g_pkgs.n = 0; return; }
  long long mt = (long long)st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec;
  if (g_pkgs.ok && mt == g_pkgs.mt && (long long)st.st_size == g_pkgs.sz) return;
  FILE *f = fopen(p, "re");
  if (!f) { g_pkgs.ok = 0; g_pkgs.n = 0; return; }
  char line[4096];
  int n = 0, skip = 0;
  while (fgets(line, sizeof line, f)) {
    size_t l = strlen(line);
    int whole = l && line[l - 1] == '\n';
    if (skip) { skip = !whole; continue; }        /* tail of an over-long line */
    if (!whole && !feof(f)) skip = 1;
    char name[160];
    long id;
    if (n < PKGMAX && sscanf(line, "%159s %ld", name, &id) == 2 && strlen(name) < 128 && id >= 0 && id < 100000) {
      snprintf(g_pkgs.e[n].name, sizeof g_pkgs.e[n].name, "%s", name);
      g_pkgs.e[n].appid = (int)id;
      n++;
    }
  }
  fclose(f);
  g_pkgs.n = n;
  g_pkgs.ok = 1;
  g_pkgs.mt = mt;
  g_pkgs.sz = (long long)st.st_size;
  for (int i = 0; i < n; i++) g_pkgs.idx[i] = i;
  qsort(g_pkgs.idx, n, sizeof g_pkgs.idx[0], pkg_idx_cmp);
  g_pkgs.gen++;
  g_bcast_pending = 1;                            /* (v1.2) apps_sync is 0 now: GbGpu pushes a fresh catalog (23.11) */
  LOGD("packages.list: %d packages", n);
  allow_validate();
}
static const char *pkg_by_appid(int appid) {   /* NULL if unknown or ambiguous (shared uid) */
  const char *r = NULL;
  for (int i = 0; i < g_pkgs.n; i++)
    if (g_pkgs.e[i].appid == appid) { if (r) return NULL; r = g_pkgs.e[i].name; }
  return r;
}
static int appid_by_pkg(const char *pkg) {
  for (int i = 0; i < g_pkgs.n; i++)
    if (!strcmp(g_pkgs.e[i].name, pkg)) return g_pkgs.e[i].appid;
  return -1;
}
static int pkg_name_ok(const char *s) {   /* [A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+, <= 127 */
  size_t l = strlen(s);
  if (!l || l > 127 || !((s[0] >= 'a' && s[0] <= 'z') || (s[0] >= 'A' && s[0] <= 'Z'))) return 0;
  int dots = 0, seg = 1;
  for (size_t i = 1; i < l; i++) {
    char c = s[i];
    if (c == '.') { if (!seg) return 0; dots++; seg = 0; continue; }
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_')) return 0;
    seg++;
  }
  return dots > 0 && seg > 0;
}

/* ------------------------------------------------------------------------------------------------ compute_allow */
#define ALLOWMAX 64
static struct { int n, loaded; long long mt; struct { int appid; char pkg[128]; } e[ALLOWMAX]; } g_allow;

static void allow_load(void) {
  char p[PATH_MAX];
  struct stat st;
  rpath(p, sizeof p, "/data/vendor/gb/compute_allow");
  if (stat(p, &st)) { g_allow.n = 0; g_allow.loaded = 1; g_allow.mt = -1; return; }
  long long mt = (long long)st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec;
  if (g_allow.loaded && mt == g_allow.mt) return;
  FILE *f = fopen(p, "re");
  g_allow.n = 0;
  g_allow.loaded = 1;
  g_allow.mt = mt;
  if (!f) return;
  char line[256];
  while (fgets(line, sizeof line, f) && g_allow.n < ALLOWMAX) {
    char pkg[160];
    int id;
    if (line[0] == '#') continue;
    if (sscanf(line, "%d %159s", &id, pkg) != 2 || id < 0 || id >= 100000 || !pkg_name_ok(pkg)) continue;
    g_allow.e[g_allow.n].appid = id;
    snprintf(g_allow.e[g_allow.n].pkg, sizeof g_allow.e[g_allow.n].pkg, "%s", pkg);
    g_allow.n++;
  }
  fclose(f);
}
static int allow_save(void) {
  char p[PATH_MAX], t[PATH_MAX + 8];
  rpath(p, sizeof p, "/data/vendor/gb/compute_allow");
  snprintf(t, sizeof t, "%s.tmp", p);
  unlink(t);                                      /* never follow or reuse what is there (O_EXCL | O_NOFOLLOW) */
  int fd = open(t, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644);
  FILE *f = fd >= 0 ? fdopen(fd, "w") : NULL;
  if (!f) { LOGW("compute_allow: %s: %s", t, strerror(errno)); if (fd >= 0) close(fd); return -1; }
  int bad = fchmod(fd, 0644) != 0;
  fprintf(f, "# gb_gpu_compute client allowlist (written by gb_power): <appId> <package>\n");
  for (int i = 0; i < g_allow.n; i++) fprintf(f, "%d %s\n", g_allow.e[i].appid, g_allow.e[i].pkg);
  bad |= ferror(f);
  if (fclose(f) || bad || rename(t, p)) { unlink(t); LOGW("compute_allow: write failed"); return -1; }
  struct stat st;
  if (!stat(p, &st)) g_allow.mt = (long long)st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec;
  return 0;
}
static void allow_validate(void) {   /* drop appId/package pairs that no longer match packages.list */
  if (!g_pkgs.ok || !g_pkgs.n) return;
  allow_load();
  int drop = 0;
  for (int i = 0; i < g_allow.n;) {
    if (appid_by_pkg(g_allow.e[i].pkg) != g_allow.e[i].appid) {
      LOGI("compute_allow: %s (%d) no longer installed with that appId: removed", g_allow.e[i].pkg, g_allow.e[i].appid);
      g_allow.e[i] = g_allow.e[--g_allow.n];
      drop++;
    } else {
      i++;
    }
  }
  if (drop) allow_save();
}
static int allow_has_appid(int appid) {
  for (int i = 0; i < g_allow.n; i++)
    if (g_allow.e[i].appid == appid) return 1;
  return 0;
}

/* ------------------------------------------------------------------------------------------------ app graphics lists (v1.2) */
/* Small root-owned files in /data/vendor/gb (root-only directory, 5.1): read without following a symlink; written as
 * <name>.tmp with O_CREAT|O_EXCL|O_NOFOLLOW + fchmod + fsync, then renamed (the allow_save() pattern). */
static int file_load(const char *rel, char *buf, size_t cap) {
  char p[PATH_MAX];
  int fd = open(rpath(p, sizeof p, "%s", rel), O_RDONLY | O_NOFOLLOW | O_CLOEXEC), n = 0;
  buf[0] = 0;
  if (fd < 0) return -1;
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
  return n;
}
static void dir_sync(const char *rel) {
  char p[PATH_MAX];
  int fd = open(rpath(p, sizeof p, "%s", rel), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (fd >= 0) { if (fsync(fd)) LOGD("fsync %s: %s", p, strerror(errno)); close(fd); }
}
static int file_save(const char *rel, const char *content, size_t len, mode_t mode) {
  char p[PATH_MAX], t[PATH_MAX + 8];
  rpath(p, sizeof p, "%s", rel);
  snprintf(t, sizeof t, "%s.tmp", p);
  unlink(t);                                      /* never follow or reuse what is there */
  int fd = open(t, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, mode);
  if (fd < 0) { LOGW("%s: %s", t, strerror(errno)); return -1; }
  int ok = fchmod(fd, mode) == 0;
  size_t off = 0;
  while (ok && off < len) {
    ssize_t w = write(fd, content + off, len - off);
    if (w < 0 && errno == EINTR) continue;
    if (w <= 0) ok = 0;
    else off += (size_t)w;
  }
  if (ok && fsync(fd)) ok = 0;
  if (close(fd)) ok = 0;
  if (!ok || rename(t, p)) { unlink(t); LOGW("%s: write failed", p); return -1; }
  return 0;
}

/* Per-app choices /data/vendor/gb/gfx_app_prefs (24.2): "<pkg> intel|nvidia", sorted, unique, <= 128; seeded once from
 * /odm/etc/gb/gfx_app_prefs.default. App catalog /data/vendor/gb/gfx_catalog (24.3): "# pkgs <mtime_ns> <size>" (the
 * packages.list it was checked against), then "<pkg> g|a", sorted, <= 512. */
#define PREFMAX 128
#define CATMAX 512
#define OPTIN_MAX 512
#define OPTIN_FILE_MAX 65536                      /* gfx_optin <= 64 KiB (gb_notify's limit, 12.1) */
struct pref { char pkg[128]; char ch; };          /* ch 'i' Always Intel, 'n' Use NVIDIA */
struct catent { char pkg[128]; char cls; };       /* cls 'g' game, 'a' other launchable app */
static struct { int n, loaded; struct pref e[PREFMAX]; } g_prefs;
static struct { int n, loaded, stamped; long long mt, sz; struct catent e[CATMAX]; } g_cat;
static struct {
  int dirty, n_in, n_out, in_dirty, out_dirty, in_have, out_have, pkgs_gen;
  char in_disk[OPTIN_FILE_MAX + 1], out_disk[PREFMAX * 130 + 1];
} g_apps = {.dirty = 1};
static int pref_cmp(const void *a, const void *b) { return strcmp(((const struct pref *)a)->pkg, ((const struct pref *)b)->pkg); }
static int cat_cmp(const void *a, const void *b) { return strcmp(((const struct catent *)a)->pkg, ((const struct catent *)b)->pkg); }
static int pref_find(const char *pkg) {
  struct pref k;
  snprintf(k.pkg, sizeof k.pkg, "%s", pkg);
  const struct pref *f = bsearch(&k, g_prefs.e, g_prefs.n, sizeof g_prefs.e[0], pref_cmp);
  return f ? (int)(f - g_prefs.e) : -1;
}
static void prefs_parse(char *buf) {
  g_prefs.n = 0;
  for (char *l = strtok(buf, "\n"); l; l = strtok(NULL, "\n")) {
    char pkg[160], ch[16], extra[4];
    if (l[0] == '#') continue;
    int k = sscanf(l, "%159s %15s %3s", pkg, ch, extra);
    if (k != 2 || !pkg_name_ok(pkg) || (strcmp(ch, "intel") && strcmp(ch, "nvidia"))) { if (l[0]) LOGW("gfx_app_prefs: bad line dropped"); continue; }
    if (g_prefs.n == PREFMAX) { LOGW("gfx_app_prefs: more than %d choices: rest dropped", PREFMAX); break; }
    int dup = 0;
    for (int i = 0; i < g_prefs.n; i++) if (!strcmp(g_prefs.e[i].pkg, pkg)) dup = 1;
    if (dup) continue;
    snprintf(g_prefs.e[g_prefs.n].pkg, sizeof g_prefs.e[0].pkg, "%s", pkg);
    g_prefs.e[g_prefs.n++].ch = ch[0] == 'i' ? 'i' : 'n';
  }
  qsort(g_prefs.e, g_prefs.n, sizeof g_prefs.e[0], pref_cmp);
}
static int prefs_save(void) {
  static char b[PREFMAX * 140 + 128];
  size_t k = (size_t)snprintf(b, sizeof b, "# graphics choice per app (written by gb_power): <package> intel|nvidia\n");
  for (int i = 0; i < g_prefs.n && k < sizeof b; i++)
    k += (size_t)snprintf(b + k, sizeof b - k, "%s %s\n", g_prefs.e[i].pkg, g_prefs.e[i].ch == 'i' ? "intel" : "nvidia");
  if (k >= sizeof b) return -1;
  return file_save("/data/vendor/gb/gfx_app_prefs", b, k, 0644);
}
static void prefs_load(void) {
  static char b[PREFMAX * 200 + 4096];
  if (g_prefs.loaded || !data_dir_ok()) return;
  g_prefs.loaded = 1;
  g_prefs.n = 0;
  if (file_load("/data/vendor/gb/gfx_app_prefs", b, sizeof b) >= 0) { prefs_parse(b); return; }
  if (file_load("/odm/etc/gb/gfx_app_prefs.default", b, sizeof b) < 0) return;   /* no seed: no choices, no file */
  prefs_parse(b);
  LOGI("gfx_app_prefs: seeded from /odm/etc/gb/gfx_app_prefs.default (%d choices)", g_prefs.n);
  if (prefs_save()) LOGW("gfx_app_prefs: seed copy failed (retried at the next change)");
}
static void catalog_load(void) {
  static char b[CATMAX * 140 + 256];
  if (g_cat.loaded || !data_dir_ok()) return;
  g_cat.loaded = 1;
  g_cat.n = g_cat.stamped = 0;
  if (file_load("/data/vendor/gb/gfx_catalog", b, sizeof b) < 0) return;
  for (char *l = strtok(b, "\n"); l; l = strtok(NULL, "\n")) {
    long long mt, sz;
    char pkg[160], cls[8], extra[4];
    if (l[0] == '#') {
      if (sscanf(l, "# pkgs %lld %lld", &mt, &sz) == 2) { g_cat.mt = mt; g_cat.sz = sz; g_cat.stamped = 1; }
      continue;
    }
    if (sscanf(l, "%159s %7s %3s", pkg, cls, extra) != 2 || !pkg_name_ok(pkg) || (strcmp(cls, "g") && strcmp(cls, "a"))) continue;
    if (g_cat.n == CATMAX) break;
    snprintf(g_cat.e[g_cat.n].pkg, sizeof g_cat.e[0].pkg, "%s", pkg);
    g_cat.e[g_cat.n++].cls = cls[0];
  }
  qsort(g_cat.e, g_cat.n, sizeof g_cat.e[0], cat_cmp);
  int k = 0;                                      /* unique */
  for (int i = 0; i < g_cat.n; i++) if (!k || strcmp(g_cat.e[k - 1].pkg, g_cat.e[i].pkg)) g_cat.e[k++] = g_cat.e[i];
  g_cat.n = k;
}
/* apps_sync (9.5): the catalog was committed against the current packages.list stamp */
static int apps_sync(void) { return g_pkgs.ok && g_cat.stamped && g_cat.mt == g_pkgs.mt && g_cat.sz == g_pkgs.sz; }

/* 24.4: base = policy all ? catalog : catalog games; opt_in = (base + "Use NVIDIA") - "Always Intel", installed only,
 * sorted, <= 512 packages and <= 64 KiB (over the limit class a goes first, then the list is cut); opt_out = "Always
 * Intel", sorted. The files are rewritten only when their content changes; a rewrite queues its settings message. */
struct cand { const char *pkg; int pri; };        /* pri 0 Use NVIDIA, 1 game, 2 other app */
static int cand_pri_cmp(const void *a, const void *b) {
  const struct cand *x = a, *y = b;
  return x->pri != y->pri ? x->pri - y->pri : strcmp(x->pkg, y->pkg);
}
static int cand_name_cmp(const void *a, const void *b) { return strcmp(((const struct cand *)a)->pkg, ((const struct cand *)b)->pkg); }
static void gfx_apps_eval(void) {
  static struct cand c[CATMAX + PREFMAX];
  static char in_txt[OPTIN_FILE_MAX + 1], out_txt[PREFMAX * 130 + 1];
  if (!g_apps.dirty) return;
  prefs_load();
  catalog_load();
  if (!g_prefs.loaded || !g_cat.loaded || !g_pkgs.ok) return;   /* no /data or packages.list yet: stays dirty */
  g_apps.dirty = 0;
  int n = 0, all = g_gfx.policy == GPOL_ALL;
  for (int i = 0; i < g_cat.n; i++)
    if (all || g_cat.e[i].cls == 'g') c[n++] = (struct cand){g_cat.e[i].pkg, g_cat.e[i].cls == 'g' ? 1 : 2};
  for (int i = 0; i < g_prefs.n; i++) {
    if (g_prefs.e[i].ch != 'n') continue;
    int k;
    for (k = 0; k < n && strcmp(c[k].pkg, g_prefs.e[i].pkg); k++) {}
    if (k < n) c[k].pri = 0;
    else c[n++] = (struct cand){g_prefs.e[i].pkg, 0};
  }
  int m = 0;
  for (int i = 0; i < n; i++) {                   /* Always Intel wins; installed packages only */
    int p = pref_find(c[i].pkg);
    if ((p >= 0 && g_prefs.e[p].ch == 'i') || !pkg_installed(c[i].pkg)) continue;
    c[m++] = c[i];
  }
  qsort(c, m, sizeof c[0], cand_pri_cmp);
  size_t bytes = 0;
  int keep = 0, cut = 0;
  for (int i = 0; i < m; i++) {
    size_t l = strlen(c[i].pkg) + 1;
    if (keep >= OPTIN_MAX || bytes + l > OPTIN_FILE_MAX) { cut++; continue; }
    bytes += l;
    c[keep++] = c[i];
  }
  if (cut) LOGW("app graphics: opt-in list over %d packages / 64 KiB: %d dropped (other apps first)", OPTIN_MAX, cut);
  qsort(c, keep, sizeof c[0], cand_name_cmp);
  size_t k = 0;
  in_txt[0] = 0;
  for (int i = 0; i < keep; i++) k += (size_t)snprintf(in_txt + k, sizeof in_txt - k, "%s\n", c[i].pkg);
  size_t ko = 0;
  int no = 0;
  out_txt[0] = 0;
  for (int i = 0; i < g_prefs.n; i++)
    if (g_prefs.e[i].ch == 'i') { ko += (size_t)snprintf(out_txt + ko, sizeof out_txt - ko, "%s\n", g_prefs.e[i].pkg); no++; }
  g_apps.n_in = keep;
  g_apps.n_out = no;
  if (!g_gfx.cap) return;                         /* no driver package on this image: nothing to write (D8) */
  if (!g_apps.in_have) g_apps.in_have = file_load("/data/vendor/gb/gfx_optin", g_apps.in_disk, sizeof g_apps.in_disk) >= 0 ? 1 : -1;
  if (!g_apps.out_have) g_apps.out_have = file_load("/data/vendor/gb/gfx_optout", g_apps.out_disk, sizeof g_apps.out_disk) >= 0 ? 1 : -1;
  if (g_apps.in_have < 0 || strcmp(in_txt, g_apps.in_disk)) {
    if (!file_save("/data/vendor/gb/gfx_optin", in_txt, k, 0644)) {
      snprintf(g_apps.in_disk, sizeof g_apps.in_disk, "%s", in_txt);
      g_apps.in_have = 1;
      g_apps.in_dirty = 1;
      LOGI("app graphics: gfx_optin rewritten (%d packages, policy %s)", keep, all ? "all" : "games");
    } else {
      g_apps.dirty = 1;
    }
  }
  if (g_apps.out_have < 0 || strcmp(out_txt, g_apps.out_disk)) {
    if (!file_save("/data/vendor/gb/gfx_optout", out_txt, ko, 0644)) {
      snprintf(g_apps.out_disk, sizeof g_apps.out_disk, "%s", out_txt);
      g_apps.out_have = 1;
      g_apps.out_dirty = 1;
      LOGI("app graphics: gfx_optout rewritten (%d packages)", no);
    } else {
      g_apps.dirty = 1;
    }
  }
}

/* ------------------------------------------------------------------------------------------------ holders */
struct holder { int pid, uid; char comm[20], pkg[128]; int closable; };
#define MAXHOLD 48
static struct { struct holder h[MAXHOLD]; int n, total, valid, refcnt_at; long long t; } g_hold;

static int link_is_node(const char *link, char names[][24], int nn) {
  for (int i = 0; i < nn; i++) {
    char want[48];
    int l = snprintf(want, sizeof want, "/dev/dri/%s", names[i]);
    if (!strncmp(link, want, l) && (!link[l] || !strcmp(link + l, " (deleted)"))) return 1;
  }
  return 0;
}
/* /proc/<pid>/maps line whose path field is one of the nodes (mmap holders keep refcnt after closing the fd) */
static int maps_holder(const char *path, char names[][24], int nn) {
  int fd = open(path, O_RDONLY | O_CLOEXEC);
  if (fd < 0) return 0;
  char buf[16384 + 512];
  size_t have = 0;
  int hit = 0;
  for (;;) {
    ssize_t r = read(fd, buf + have, sizeof buf - 1 - have);
    if (r < 0 && errno == EINTR) continue;
    if (r <= 0) break;
    have += (size_t)r;
    buf[have] = 0;
    char *l = buf, *e;
    while (!hit && (e = strchr(l, '\n'))) {
      *e = 0;
      char *s = strstr(l, " /dev/dri/");
      if (s && link_is_node(s + 1, names, nn)) hit = 1;
      l = e + 1;
    }
    if (hit) break;
    size_t rest = have - (size_t)(l - buf);
    if (rest == sizeof buf - 1) rest = 0;           /* absurdly long line: drop it */
    memmove(buf, l, rest);
    have = rest;
  }
  if (!hit && have) { buf[have] = 0; char *s = strstr(buf, " /dev/dri/"); if (s && link_is_node(s + 1, names, nn)) hit = 1; }
  close(fd);
  return hit;
}
static int proc_uid(int pid) {
  char p[PATH_MAX], b[2048];
  if (read_text(rpath(p, sizeof p, "/proc/%d/status", pid), b, sizeof b) <= 0) return -1;
  const char *u = strstr(b, "\nUid:");
  return u ? atoi(u + 5) : -1;
}
static int is_compute_pid(int pid) {
  char p[PATH_MAX], t[PATH_MAX];
  ssize_t r = readlink(rpath(p, sizeof p, "/proc/%d/exe", pid), t, sizeof t - 1);
  if (r <= 0) return 0;
  t[r] = 0;
  return !strcmp(t, "/vendor/bin/gb_gpu_compute");
}

/* Fresh holder scan (rate limited to 1/s): every process with an fd or a mapping of a nouveau DRM node. */
static void holders_scan(void) {
  long long t = now_ms();
  if (g_hold.valid && t - g_hold.t < HOLDER_RATE_MS) return;
  char names[4][24], dir[PATH_MAX], p[PATH_MAX + 300], l[PATH_MAX];
  int nn = nouveau_nodes(names, 4);
  g_hold.n = g_hold.total = 0;
  g_hold.valid = 1;
  g_hold.t = t;
  g_hold.refcnt_at = nouveau_refcnt();
  if (!nn) return;
  pkgs_refresh(0);
  DIR *d = opendir(rpath(dir, sizeof dir, "/proc"));
  if (!d) return;
  struct dirent *e;
  while ((e = readdir(d))) {
    if (!is_num(e->d_name)) continue;
    int pid = atoi(e->d_name), found = 0;
    snprintf(p, sizeof p, "%s/%s/fd", dir, e->d_name);
    DIR *fdd = opendir(p);
    if (fdd) {
      struct dirent *f;
      while (!found && (f = readdir(fdd))) {
        if (f->d_name[0] == '.') continue;
        char q[PATH_MAX + 600];
        snprintf(q, sizeof q, "%s/%s", p, f->d_name);
        ssize_t r = readlink(q, l, sizeof l - 1);
        if (r <= 0) continue;
        l[r] = 0;
        if (link_is_node(l, names, nn)) found = 1;
      }
      closedir(fdd);
    }
    if (!found) {
      snprintf(p, sizeof p, "%s/%s/maps", dir, e->d_name);
      found = maps_holder(p, names, nn);
    }
    if (!found) continue;
    g_hold.total++;
    if (g_hold.n >= MAXHOLD) continue;
    struct holder *h = &g_hold.h[g_hold.n++];
    memset(h, 0, sizeof *h);
    h->pid = pid;
    h->uid = proc_uid(pid);
    char c[64];
    snprintf(p, sizeof p, "%s/%s/comm", dir, e->d_name);
    if (read_text(p, c, sizeof c) < 0) strcpy(c, "?");
    ascii_copy(h->comm, sizeof h->comm, c, 16);
    int app = h->uid >= 0 ? h->uid % 100000 : -1;
    const char *pkg = app >= 10000 ? pkg_by_appid(app) : NULL;
    if (pkg) snprintf(h->pkg, sizeof h->pkg, "%s", pkg);
    h->closable = app >= 10000 && pkg && strcmp(pkg, "gb.sb3.gpu") && !is_compute_pid(pid);
  }
  closedir(d);
  LOGD("holders: %d process(es)", g_hold.total);
}
static void holders_clear(void) { g_hold.n = g_hold.total = 0; g_hold.valid = 0; }
static int holder_find(int pid) {
  for (int i = 0; i < g_hold.n; i++)
    if (g_hold.h[i].pid == pid) return i;
  return -1;
}
/* "comm:pid[,comm:pid...]" truncated at an entry boundary with ",+N" (vendor.gb.dgpu.holders, <= 91 bytes) */
static void holders_prop(char *out, size_t n) {
  size_t k = 0;
  out[0] = 0;
  for (int i = 0; i < g_hold.n; i++) {
    char c[20], e[48];
    snprintf(c, sizeof c, "%s", g_hold.h[i].comm);
    for (char *s = c; *s; s++) if (*s == ',' || *s == ':' || *s == ' ') *s = '?';
    int l = snprintf(e, sizeof e, "%s%s:%d", i ? "," : "", c, g_hold.h[i].pid);
    if (k + (size_t)l > n - 8) { snprintf(out + k, n - k, "%s+%d", i ? "," : "", g_hold.total - i); return; }
    memcpy(out + k, e, (size_t)l + 1);
    k += (size_t)l;
  }
  if (g_hold.total > g_hold.n) snprintf(out + k, n - k, ",+%d", g_hold.total - g_hold.n);
}
/* "<a>, <b> ... and N more" for notifications: package names, else comm (max 5, distinct) */
static void holders_text(char *out, size_t n) {
  const char *seen[MAXHOLD];
  int ns = 0;
  size_t k = 0;
  out[0] = 0;
  for (int i = 0; i < g_hold.n; i++) {
    const char *nm = g_hold.h[i].pkg[0] ? g_hold.h[i].pkg : g_hold.h[i].comm;
    int dup = 0;
    for (int j = 0; j < ns; j++) if (!strcmp(seen[j], nm)) dup = 1;
    if (dup) continue;
    seen[ns++] = nm;
  }
  int extra = g_hold.total - g_hold.n;            /* not listed at all (more than MAXHOLD holders) */
  for (int i = 0; i < ns && i < 5 && k < n; i++) k += snprintf(out + k, n - k, "%s%s", i ? ", " : "", seen[i]);
  int more = (ns > 5 ? ns - 5 : 0) + (extra > 0 ? extra : 0);
  if (more && k < n) snprintf(out + k, n - k, " and %d more", more);
  if (!ns) snprintf(out, n, "an app");
}

/* ------------------------------------------------------------------------------------------------ boot-disk guard */
static int real_blkdev(const char *src, unsigned *ma, unsigned *mi) {
  char p[PATH_MAX];
  struct stat st;
  if (stat(rpath(p, sizeof p, "%s", src), &st) || !S_ISBLK(st.st_mode)) return -1;
  *ma = major(st.st_rdev);
  *mi = minor(st.st_rdev);
  return 0;
}
static int (*g_blkdev)(const char *src, unsigned *ma, unsigned *mi) = real_blkdev;   /* unit tests map names */

/* base-side: the device's sysfs path contains "/usb" or it is an mmcblk (SD reader); dm/md: any slave is. */
static int blk_base_side(unsigned ma, unsigned mi, int depth, char *what, size_t n) {
  char p[PATH_MAX], real[PATH_MAX], rr[PATH_MAX], sl[PATH_MAX + 16];
  if (depth > 4) return 0;
  rpath(p, sizeof p, "/sys/dev/block/%u:%u", ma, mi);
  if (!realpath(p, real)) return 0;
  const char *rel = real;
  if (g_root[0] && realpath(g_root, rr) && !strncmp(real, rr, strlen(rr))) rel = real + strlen(rr);
  const char *name = strrchr(rel, '/');
  name = name ? name + 1 : rel;
  if (strstr(rel, "/usb") || !strncmp(name, "mmcblk", 6)) { snprintf(what, n, "%s", name); return 1; }
  snprintf(sl, sizeof sl, "%s/slaves", real);
  DIR *d = opendir(sl);
  if (!d) return 0;
  struct dirent *e;
  int hit = 0;
  while (!hit && (e = readdir(d))) {
    if (e->d_name[0] == '.') continue;
    char dp[PATH_MAX + 300], dv[32];
    unsigned a, b;
    snprintf(dp, sizeof dp, "%s/%s/dev", sl, e->d_name);
    if (read_text(dp, dv, sizeof dv) > 0 && sscanf(dv, "%u:%u", &a, &b) == 2) hit = blk_base_side(a, b, depth + 1, what, n);
  }
  closedir(d);
  return hit;
}
/* INTERFACES 10.3 step 2: a mounted block device behind a base USB/SD port blocks the detach (until the Phase 2
 * port mapping exists, every USB storage device and every mmcblk counts as base-side). */
static int bootdisk_guard(char *what, size_t n) {
  char p[PATH_MAX], line[1024];
  FILE *f = fopen(rpath(p, sizeof p, "/proc/mounts"), "re");
  if (!f) return 0;
  unsigned seen[64][2];
  int ns = 0, hit = 0;
  while (!hit && fgets(line, sizeof line, f)) {
    char src[512], mnt[512], w[64];
    unsigned ma, mi;
    if (sscanf(line, "%511s %511s", src, mnt) != 2 || src[0] != '/' || g_blkdev(src, &ma, &mi)) continue;
    int dup = 0;
    for (int i = 0; i < ns; i++) if (seen[i][0] == ma && seen[i][1] == mi) dup = 1;
    if (dup) continue;
    if (ns < 64) { seen[ns][0] = ma; seen[ns][1] = mi; ns++; }
    if (blk_base_side(ma, mi, 0, w, sizeof w)) { snprintf(what, n, "%s (%s)", mnt, w); hit = 1; }
  }
  fclose(f);
  return hit;
}

/* ---- v1.2 graphics: functions used by the DTX and dGPU code above section G (INTERFACES 23) */
static void gfx_force_intel(const char *reason);
static void rb_arm(const char *why, struct job *ride);
static void rb_end(const char *why, int clean);
static void pend_clear(const char *why);
static void mark_job_done(const struct job *j, int wedge);
static int gfx_attached(void);
static void unclean_write(int n);
#define N02_TITLE "NVIDIA GPU problem"
#define N02_FMT "The NVIDIA driver started with errors (%s). Graphics are set to Intel only."
#define N16_TITLE "Performance base connected"
#define N16_TEXT "NVIDIA graphics on for apps you open now. Restart apps that are already open to switch them."
#define N17_TITLE "Tablet mode - Intel graphics"
#define N17_TEXT "Apps use Intel graphics while the keyboard base is detached. Reattach it to use NVIDIA graphics again."
#define N18_TITLE "NVIDIA app graphics off"
#define N18_FMT "The NVIDIA graphics self-test failed (%s), so apps stay on Intel graphics. The NVIDIA compute service " \
                "still works. See Settings > System > Graphics."
#define N19_TITLE "Switched to Intel graphics"
#define N19_TEXT "The system restarted unexpectedly twice while apps used NVIDIA graphics, so graphics were set to Intel " \
                 "only. Choose Auto in Settings > System > Graphics to try again."
#define N06_FMT "The NVIDIA GPU is not responding to power management (%s). Graphics are set to Intel only. Restart, " \
                "then detach."
#define N21_TITLE "Switched to Intel graphics"
#define N21_TEXT "The system restarted unexpectedly twice while the NVIDIA GPU was in use, so graphics were set to Intel " \
                 "only. Choose Auto in Settings > System > Graphics to try again."
#define N20_TITLE "NVIDIA GPU not available"
#define N20_FMT "The NVIDIA GPU did not start correctly (%s). Apps use Intel graphics until restart."

/* ------------------------------------------------------------------------------------------------ B. DTX */
/* uAPI: include/uapi/linux/surface_aggregator/dtx.h. build.sh compiles against a verbatim copy of the kernel tree's header
 * (uapi/ next to this file, -Iuapi, ahead of the NDK sysroot's identical copy). The literal values below pin the ABI, so a
 * header that differs from what surface_dtx.c implements fails the build instead of producing wrong ioctls. */
#include <linux/surface_aggregator/dtx.h>
_Static_assert(SDTX_IOCTL_EVENTS_ENABLE == 0xa521 && SDTX_IOCTL_LATCH_CONFIRM == 0xa526 &&
               SDTX_IOCTL_LATCH_HEARTBEAT == 0xa527 && SDTX_IOCTL_LATCH_CANCEL == 0xa528, "DTX ioctl numbers");
_Static_assert(SDTX_IOCTL_LATCH_LOCK == 0xa523 && SDTX_IOCTL_LATCH_UNLOCK == 0xa524 &&
               SDTX_IOCTL_LATCH_REQUEST == 0xa525, "DTX latch lock/unlock/request ioctl numbers (dtx.rst:563-578)");
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
  long long echo_until;                           /* waiting for the REQUEST echo of our LATCH_CANCEL (slow path) */
  long long slow_confirm_at;                      /* slow-path LATCH_CONFIRM sent at (confirm watchdog) */
} g_dtx = {.fd = -1, .base = SDTX_BASE_ATTACHED, .mode = MODE_LAPTOP, .inhibited = -1};
static long long g_dgpu_due = -1, g_dgpu_report_due = -1, g_tick_due = 0;
static int g_latch = -1;                          /* last LATCH_LOCK (1) / LATCH_UNLOCK (0) issued; -1 unknown */

/* While a detach is under way (confirmed, or the latch open) a driver that binds an NVIDIA function is unbound again by
 * the next pass (a late bind is never left on a base that can be pulled); nouveau only through the refcnt gate. Not
 * while EC_IN_PROGRESS outside a decision: that is the echo wait after a refusal/abort, when nouveau is in use. */
static int dgpu_releasing(void) { return g_dtx.ec == EC_CONFIRMED || g_dtx.latch == SDTX_LATCH_OPENED; }
static int g_bootdisk;                            /* a mounted base-side drive was seen by the last tick (latch lock) */
static int dtx_ready(void) { return g_dtx_on && g_dtx.fd >= 0; }
static int base_attached(void) { return dtx_ready() && g_dtx.base == SDTX_BASE_ATTACHED; }

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

/* ---- latch lifecycle (INTERFACES 10.2) */
static void latch_set(int lock, int force, const char *why) {
  if (g_dtx.fd < 0) return;
  if (!force && g_latch == lock) return;
  if (dtx_cmd(lock ? SDTX_IOCTL_LATCH_LOCK : SDTX_IOCTL_LATCH_UNLOCK, lock ? "LATCH_LOCK" : "LATCH_UNLOCK")) return;
  if (g_latch != lock)
    LOGI("latch %s (%s)", lock ? "locked: a detach needs gb_power's confirm, the EC cancels it on timeout" : "unlocked", why);
  else LOGD("latch %s again (%s)", lock ? "locked" : "unlocked", why);
  g_latch = lock;
  prop_put("vendor.gb.dgpu.latch", lock ? "locked" : "unlocked");
}
/* LOCK while any driver is bound to an NVIDIA function or a drive in the base is mounted (so the EC cancels instead of
 * opening if gb_power is not there to refuse); UNLOCK otherwise, except during a decision, after a wedge or a fault,
 * and once shutdown started (the shutdown path unlocks itself). */
static void latch_eval(const char *why) {
  if (g_dtx.fd < 0 || g_shutdown) return;
  if (g_inv.bound_any) latch_set(1, 0, why);
  else if (g_bootdisk) latch_set(1, 0, "a drive in the keyboard base is mounted");
  else if (!dec_running() && !g_wedged && !g_fault[0]) latch_set(0, 0, why);
}

/* ---- suspend/resume (re-LOCK after every system resume) and shutdown */
static char g_susp[24];
static void resume_check(void) {
  char p[PATH_MAX], v[24];
  if (read_text(rpath(p, sizeof p, "/sys/power/suspend_stats/success"), v, sizeof v) <= 0) return;
  if (g_susp[0] && strcmp(v, g_susp)) {
    LOGI("system resumed (suspend_stats/success %s -> %s)", g_susp, v);
    pend_clear("system resumed");                 /* (R-12) a pending detach never fires long after the user acted */
    inventory();
    if (g_inv.bound_any && !g_shutdown) latch_set(1, 1, "after resume");
    g_tick_due = now_ms();
  }
  snprintf(g_susp, sizeof g_susp, "%s", v);
}
/* (v1.2 23.10, R-1) a clean end of the boot (sys.powerctl set; from the tick or from the SIGTERM init sends right after
 * it): in the main process at once (the system is going down) gfx_session and dgpu_session are removed, the counts of
 * unclean endings in a row start again, and the rebind guard's own crash marker is removed when the kernel's probe has
 * returned (bound: the watch's healthy point or later). Once per run. */
static void dunclean_write(int n);
static int unlink_quiet(const char *rel, const char *what) {
  char p[PATH_MAX];
  rpath(p, sizeof p, "%s", rel);
  if (!unlink(p)) { LOGI("shutdown: %s removed", what); return 1; }
  if (errno != ENOENT) LOGW("%s: %s", p, strerror(errno));
  return 0;
}
static void shutdown_markers(const char *why) {
  if (g_shut_marks) return;
  g_shut_marks = 1;
  int k = unlink_quiet("/data/vendor/gb/gfx_session", "gfx_session (clean end of the NVIDIA app-graphics session)");
  k += unlink_quiet("/data/vendor/gb/dgpu_session", "dgpu_session (clean end while nouveau was bound)");
  if (g_rb.own_marker && (g_rb.phase == RB_CLEAN || (g_rb.phase == RB_SEEN && g_inv.nouveau_bound))) {
    char p[PATH_MAX], cur[200] = "";
    rpath(p, sizeof p, "/data/vendor/gb/nouveau.loading");
    if (read_text(p, cur, sizeof cur) >= 0 && !strcmp(cur, g_rb.mark_line)) {
      if (!unlink(p)) { k++; LOGI("shutdown: the rebind guard's crash marker removed (the probe had returned)"); }
    }
    g_rb.own_marker = 0;
  }
  if (k) dir_sync("/data/vendor/gb");
  g_sess.have = g_sess.stale = 0;
  g_dsess.have = g_dsess.stale = 0;
  if (data_dir_ok()) { trace("unclean=0"); unclean_write(0); dunclean_write(0); }
  LOGI("shutdown (%s): markers of a clean end handled (%d removed)", why, k);
}
static void powerctl_check(void) {
  char v[PROP_VALUE_MAX];
  prop_get("sys.powerctl", v);
  if (!v[0] || g_shutdown) return;
  g_shutdown = 1;
  LOGI("sys.powerctl=%s: shutting down, latch unlocked (no re-lock any more)", v);
  latch_set(0, 1, "shutdown");
  shutdown_markers("sys.powerctl");
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

static struct { int active, attempt, phase, abort, done; long long next_at; } g_rs;   /* staged rescan (10.4) */

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
  if (base == SDTX_BASE_DETACHED && g_rs.active) g_rs.abort = 1;
  long long t = now_ms();
  if (base == SDTX_BASE_DETACHED) {
    pend_clear("base detached");
    rb_end("base detached again", 0);
    /* (v1.2, 23.9) N17 after a confirmed detach (slow or quick path) in auto/nvidia mode with the driver package */
    char m[PROP_VALUE_MAX];
    prop_get("persist.vendor.gb.gfx_mode", m);
    if (old != SDTX_BASE_DETACHED && (g_dtx.ec == EC_CONFIRMED || g_dtx.latch == SDTX_LATCH_OPENED) && g_gfx.cap &&
        strcmp(m, "intel"))
      notify("gb_gfx", N17_TITLE, N17_TEXT);
  }
  if (old == SDTX_BASE_DETACHED && base != SDTX_BASE_DETACHED) {
    /* re-attached: pciehp/surface_hotplug re-enumerate the dGPU; the PCI add uevent triggers a dGPU pass, this is
     * the backstop (and logs the resulting power state); the tick runs the staged rescan if nouveau was used */
    g_dgpu_due = t + 3000;
    g_dgpu_report_due = t + 10000;
    g_gfx.n16 = 1;                                /* (v1.2) the first nvidia after this attach event posts N16 */
    inventory();
    rb_arm("base attached", NULL);                /* (v1.2) the kernel re-probes nouveau by itself (23.8) */
  }
  g_tick_due = t + 200;
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

/* After our LATCH_CANCEL the EC answers with a REQUEST event (dtx.rst), which takes ec back to READY. The slow-path
 * cancels also time out after ECHO_WAIT_MS, so a lost echo cannot swallow the user's next press forever. */
static void dtx_cancel_await(void) {
  dtx_cmd(SDTX_IOCTL_LATCH_CANCEL, "LATCH_CANCEL");
  g_dtx.echo_until = now_ms() + ECHO_WAIT_MS;
}
static void ec_ready(void) {
  g_dtx.ec = EC_READY;
  g_dtx.echo_until = 0;
  g_dtx.slow_confirm_at = 0;
  detaching_flag(0);                              /* the EC process is over: gb_nouveau.sh may load again */
  g_tick_due = now_ms() + 200;                    /* cancel rescan, latch evaluation */
}

/* ---- slow-path detach decision (INTERFACES 10.3 steps 4-6)
 * DEC_REFCNT: the users are stopped, the nodes are 000; wait (30 s) until no GPU user service runs, no gb_nouveau load
 *             window is open and the refcnt gate reads 0.
 * DEC_UNBIND: worker job 1 unbinds every bound NVIDIA function (the child re-checks the gate before a nouveau unbind).
 *             Then the main loop looks again: a late re-bind repeats the job (at most UNBIND_TRIES), a refcnt that rose
 *             again refuses.
 * DEC_REMOVE: worker job 2 PCI-removes every NVIDIA function (the child refuses a bound function or a held nouveau).
 *             Then the last checks before LATCH_CONFIRM: no function left, refcnt gate 0, no base drive, no load window. */
enum { DEC_NONE, DEC_REFCNT, DEC_UNBIND, DEC_REMOVE };
static struct { int phase, abort, ec_canceled, tries; long long t0, t_phase, hb_at, step_at, early_at; } g_dec;
static int dec_running(void) { return g_dec.phase != DEC_NONE; }
static void publish_state(long long t);
#define N15_TEXT "The NVIDIA driver is starting. Wait a minute, then press detach again."

static void dec_hb(long long t) {
  if (g_dtx.fd >= 0 && !g_dec.ec_canceled) dtx_cmd(SDTX_IOCTL_LATCH_HEARTBEAT, "LATCH_HEARTBEAT");
  g_dec.hb_at = t;
}
static void decision_end(void) {
  g_dec.phase = DEC_NONE;
  publish_state(now_ms());
}
static void decision_cancel(const char *why, int restore) {
  LOGW("detach: canceled: %s", why);
  if (restore) nodes_restore();
  if (g_dec.ec_canceled) ec_ready();              /* the EC already ended its process (cancel event) */
  else dtx_cancel_await();
  decision_end();
}
/* job 1 (J_DETACH): unbind every bound NVIDIA function; job 2 (J_DREMOVE): remove every NVIDIA function. The op lists
 * are built from a fresh inventory; the child re-checks the refcnt gate right before each write (op_gate). */
static int submit_detach_job(int kind) {
  char dir[PATH_MAX], p[PATH_MAX + 64];
  struct job j;
  memset(&j, 0, sizeof j);
  j.kind = kind;
  j.deadline_ms = DETACH_DEADLINE_MS;
  inventory();
  rpath(dir, sizeof dir, "/sys/bus/pci/devices");
  if (kind == J_DETACH) {
    for (int i = 0; i < g_inv.n; i++) {
      if (!g_inv.f[i].drv[0]) continue;
      snprintf(p, sizeof p, "%s/%s/driver/unbind", dir, g_inv.f[i].bdf);
      job_add(&j, OP_UNBIND, p, "", g_inv.f[i].bdf);
    }
    LOGI("detach: worker unbinds %d NVIDIA function(s)", j.nops);
  } else {
    for (int i = g_inv.n - 1; i >= 0; i--) {
      snprintf(p, sizeof p, "%s/%s/remove", dir, g_inv.f[i].bdf);
      job_add(&j, OP_REMOVE, p, "1", g_inv.f[i].bdf);
    }
    LOGI("detach: worker removes %d NVIDIA function(s)", j.nops);
  }
  g_dec.phase = kind == J_DETACH ? DEC_UNBIND : DEC_REMOVE;
  g_dec.t_phase = now_ms();
  return worker_submit(&j);
}
/* refused because the GPU is (again) in use: holders named in N08 (or the load window: N15) */
static void decision_busy(int rc, const char *svc) {
  if (load_window_open()) {
    LOGW("detach: refused, gb_nouveau.sh is loading the NVIDIA driver (load window open)");
    notify("gb_dtx", "Can't detach yet", N15_TEXT);
    decision_cancel("the NVIDIA driver is still starting", 1);
    return;
  }
  char who[220], txt[320], pv[PROP_VALUE_MAX];
  g_hold.valid = 0;                               /* fresh scan for the names */
  holders_scan();
  holders_prop(pv, sizeof pv);
  prop_put("vendor.gb.dgpu.holders", pv);
  if (!g_hold.n && svc) snprintf(who, sizeof who, "%s", svc);
  else holders_text(who, sizeof who);
  snprintf(txt, sizeof txt, "The NVIDIA GPU is in use by %s. Close them, then press detach again.", who);
  LOGW("detach: refused, nouveau still in use (refcnt %d%s%s): %s", rc, svc ? ", service " : "", svc ? svc : "", pv);
  /* (v1.2, 23.9) GbGpu shows the actionable notification when detach_ref grows; N08 only while GbGpu is absent or
   * has not declared ui-caps notify this boot (review E7) */
  g_gfx.detach_ref++;
  g_bcast_pending = 1;
  /* (R-9) only refusals naming app holders: GbGpu shows its notification for those (busy:N); a service holder
   * (refcnt 0 while it still reads running) gets N08 from gb_power */
  if (g_gfx.ui_notify && g_hold.n > 0 && path_exists("/odm/app/GbGpu/GbGpu.apk"))
    LOGI("detach: N08 left to GbGpu (ui-caps notify), detach_ref %d", g_gfx.detach_ref);
  else notify("gb_dtx", "Can't detach yet", txt);
  decision_cancel("the NVIDIA GPU is in use", 1);
}
static void decision_abort(void) {
  decision_cancel(g_dec.ec_canceled ? "the EC ended the detach process" : "detach pressed again (user abort)", 1);
}
static void decision_step(long long t) {
  if (dec_running()) nodes_lock();               /* a node that appeared meanwhile (rebind, ueventd) is locked too */
  if (g_dec.phase == DEC_REFCNT) {
    if (g_dec.abort) { decision_abort(); return; }
    const char *svc = gpu_user_running();
    int rc = refcnt_busy(), lw = load_window_open();
    if (!rc && !svc && !lw) {
      inventory();
      LOGI("detach: refcnt gate passed after %lld ms (%s)", t - g_dec.t_phase,
           g_inv.bound_any ? "unbinding, then removing every NVIDIA function" : "removing every NVIDIA function");
      if (submit_detach_job(g_inv.bound_any ? J_DETACH : J_DREMOVE)) { decision_cancel("the detach worker could not start", 1); return; }
    } else if (t - g_dec.t_phase >= REFCNT_WAIT_MS) {
      decision_busy(rc, svc);
      return;
    } else if (rc && !svc && !lw && t - g_dec.t_phase >= EARLY_REFUSE_MS && t >= g_dec.early_at) {
      /* (v1.2, 23.9) only closable app processes hold nouveau after 5 s: refuse now (they will not let go by themselves;
       * the user closes them from the notification). Any other holder keeps the 30 s wait. */
      g_dec.early_at = t + 1000;
      g_hold.valid = 0;
      holders_scan();
      int all = g_hold.n > 0 && g_hold.total == g_hold.n;
      for (int i = 0; all && i < g_hold.n; i++) if (!g_hold.h[i].closable) all = 0;
      if (all) {
        LOGI("detach: after %lld ms only %d closable app process(es) hold nouveau: refusing now", t - g_dec.t_phase, g_hold.n);
        decision_busy(rc, NULL);
        return;
      }
    }
  }
  if (dec_running() && !g_dec.ec_canceled && t - g_dec.hb_at >= HB_MS) dec_hb(t);
  g_dec.step_at = t + DEC_STEP_MS;
}
static void decision_start(const char *why) {
  long long t = now_ms();
  /* (v1.2, 23.3/23.9, REQ:60) new app Vulkan instances get Intel BEFORE any lock, chmod or unbind */
  gfx_force_intel("detaching");
  pend_clear("a detach decision started");
  memset(&g_dec, 0, sizeof g_dec);
  g_dec.phase = DEC_REFCNT;
  g_dec.t0 = g_dec.t_phase = t;
  LOGI("detach: slow path (%s): stopping NVIDIA GPU users", why);
  if (g_rs.active) g_rs.abort = 1;                /* a staged rescan stops at its next safe point */
  latch_set(1, 1, "detach decision");             /* so the EC cancels (never opens) if gb_power stalls meanwhile */
  dec_hb(t);
  notify("gb_dtx", "Preparing to detach", "Getting the NVIDIA GPU ready. Keep the screen attached.");
  gpu_users_stop();
  nodes_lock();
  publish_state(t);
  decision_step(t);
}
static void decision_worker_done(const struct job *j, int wedge) {
  if (!dec_running()) return;
  int removed = 0, gated = 0;
  for (int i = 0; i < j->nops; i++) {
    if (j->op[i].kind == OP_REMOVE && j->op[i].res == 0) removed++;
    if (j->op[i].res == -EBUSY) gated++;
  }
  if (removed) g_removed = 1;
  if (wedge) {
    LOGE("detach: the NVIDIA GPU did not shut down within %d s: latch stays locked, detach canceled",
         DETACH_DEADLINE_MS / 1000);
    notify("gb_dtx", "Restart required to detach", "The NVIDIA GPU did not shut down in time. Restart, then detach.");
    if (g_dec.ec_canceled) ec_ready();
    else dtx_cancel_await();
    decision_end();                               /* nodes stay 000: nothing may open a wedged GPU */
    return;
  }
  inventory();
  if (g_dec.abort) { decision_abort(); return; }  /* the safe point between and after the jobs */
  if (gated) {
    LOGW("detach: the worker's refcnt gate refused %d write(s): nouveau is in use again", gated);
    decision_busy(refcnt_busy(), gpu_user_running());
    return;
  }
  if (j->kind == J_DETACH) {                      /* job 1 done: look again before the remove */
    if (g_inv.bound_any) {
      if (++g_dec.tries >= UNBIND_TRIES) { decision_cancel("a driver keeps binding the NVIDIA GPU", 1); return; }
      LOGW("detach: a driver bound an NVIDIA function again: unbinding again (%d)", g_dec.tries + 1);
      if (submit_detach_job(J_DETACH)) decision_cancel("the detach worker could not start", 1);
      return;
    }
    const char *svc = gpu_user_running();
    int rc = refcnt_busy();
    if (rc || svc || load_window_open()) { decision_busy(rc, svc); return; }
    if (submit_detach_job(J_DREMOVE)) decision_cancel("the detach worker could not start", 1);
    return;
  }
  if (g_inv.n) {
    LOGE("detach: %d NVIDIA function(s) still present after the remove", g_inv.n);
    decision_cancel("NVIDIA functions still present", 1);
    return;
  }
  /* invariant 7, checked once more right before the confirm */
  int rc = refcnt_busy();
  if (rc) { decision_busy(rc, gpu_user_running()); return; }
  if (load_window_open()) { decision_busy(0, NULL); return; }
  char what[200];
  if (bootdisk_guard(what, sizeof what)) {
    int usb_boot = !strcmp(g_boot, "usb");
    LOGW("detach: refused, %s is a mounted drive in the keyboard base (checked before the confirm)", what);
    if (usb_boot) notify("gb_dtx", "Can't detach", "The system is running from a drive in the keyboard base. Shut down before detaching.");
    else notify("gb_dtx", "Can't detach", "A USB or SD drive in the keyboard base is in use. Eject it, then press detach again.");
    decision_cancel("a drive in the keyboard base is mounted", 1);
    return;
  }
  notify("gb_dtx", "Ready to detach", "You can remove the screen now.");
  if (!dtx_cmd(SDTX_IOCTL_LATCH_CONFIRM, "LATCH_CONFIRM")) {
    g_dtx.ec = EC_CONFIRMED;
    g_dtx.slow_confirm_at = now_ms();
    g_latch = 0;                                  /* LATCH_CONFIRM also resets the lock (dtx.rst) */
    prop_put("vendor.gb.dgpu.latch", "unlocked");
    LOGI("detach: confirmed (every NVIDIA function removed after %lld ms), latch opening", now_ms() - g_dec.t0);
  } else {
    g_dtx.echo_until = now_ms() + ECHO_WAIT_MS;
  }
  decision_end();
}

static int dgpu_fault_blocks(void) { return !strcmp(g_fault, "rpm-error") || !strcmp(g_fault, "d0-idle"); }
static void dgpu_pm_pass(void);

static void dtx_on_request(void) {
  pend_clear("detach request event");             /* (v1.2) the pending detach ends with the EC's REQUEST (23.9) */
  if (dec_running()) {                            /* a press during the decision = the user's abort */
    if (!g_dec.abort) LOGI("detach: pressed again: aborting at the next safe point (never inside the worker)");
    g_dec.abort = 1;
    if (g_dec.phase == DEC_REFCNT) decision_step(now_ms());
    return;
  }
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
    ec_ready();
    return;
  }
  g_dtx.ec = EC_IN_PROGRESS;
  resume_check();                                 /* a press right after resume: re-lock first */
  struct sdtx_base_info bi;
  if (!dtx_ioctl(g_dtx.fd, SDTX_IOCTL_GET_BASE_INFO, &bi)) dtx_set_base(bi.state, "request");
  if (g_dtx.base != SDTX_BASE_ATTACHED) {        /* EC answers LATCH_CANCEL with a REQUEST event -> EC_READY */
    LOGW("detach: refused, %s", g_dtx.base == SDTX_DETACH_NOT_FEASIBLE ? "clipboard battery too low" : "no base attached");
    dtx_cmd(SDTX_IOCTL_LATCH_CANCEL, "LATCH_CANCEL");
    return;
  }
  LOGI("detach: requested (detach button)");
  /* Mutual exclusion with a nouveau load (INTERFACES 5.2, 10.3): set our flag first, then look at gb_nouveau.sh's load
   * window; gb_nouveau.sh takes its lock first, then looks at our flag right before insmod. One of the two refuses. */
  detaching_flag(1);
  inventory();
  if (wedged()) {
    LOGE("detach: refused, the NVIDIA GPU is wedged (restart required); latch stays locked");
    notify("gb_dtx", "Restart required to detach", "The NVIDIA GPU did not shut down in time. Restart, then detach.");
    dtx_cancel_await();
    return;
  }
  if (dgpu_fault_blocks()) {
    char t[200];
    snprintf(t, sizeof t, N06_FMT, g_fault);
    LOGE("detach: refused, dGPU fault %s", g_fault);
    notify("gb_dgpu", "NVIDIA GPU fault", t);
    dtx_cancel_await();
    return;
  }
  if (load_window_open()) {                       /* insmod/probe/health check of nouveau under way: never confirm */
    LOGW("detach: refused, gb_nouveau.sh is loading the NVIDIA driver (load window open)");
    notify("gb_dtx", "Can't detach yet", N15_TEXT);
    dtx_cancel_await();
    return;
  }
  char what[200];
  if (bootdisk_guard(what, sizeof what)) {
    int usb_boot = !strcmp(g_boot, "usb");
    LOGW("detach: refused, %s is a mounted drive in the keyboard base%s", what, usb_boot ? " (booted from USB)" : "");
    if (usb_boot) notify("gb_dtx", "Can't detach", "The system is running from a drive in the keyboard base. Shut down before detaching.");
    else notify("gb_dtx", "Can't detach", "A USB or SD drive in the keyboard base is in use. Eject it, then press detach again.");
    dtx_cancel_await();
    return;
  }
  if (!g_inv.bound_any && !used()) {             /* quick path: today's sequence, unchanged */
    dtx_cmd(SDTX_IOCTL_LATCH_HEARTBEAT, "LATCH_HEARTBEAT");
    if (g_dgpu_on) dgpu_pm_pass();
    inventory();                                  /* last look before the confirm (read-only) */
    if (!g_inv.bound_any && !used() && !load_window_open()) {
      if (!dtx_cmd(SDTX_IOCTL_LATCH_CONFIRM, "LATCH_CONFIRM")) {
        g_dtx.ec = EC_CONFIRMED;
        LOGI("detach: confirmed (dGPU idle), latch opening");
      } else {
        g_dtx.echo_until = now_ms() + ECHO_WAIT_MS;   /* ready again (and the detaching flag gone) after the wait */
      }
      return;
    }
    LOGW("detach: a driver bound an NVIDIA function (or a nouveau load started) meanwhile: slow path");
  }
  decision_start(g_inv.nouveau_bound ? "nouveau bound" : g_inv.bound_any ? "a driver is bound" : "nouveau was used this boot");
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
      if (dec_running()) {                        /* finish at the next safe point; the EC process is over */
        g_dec.abort = g_dec.ec_canceled = 1;
        if (g_dec.phase == DEC_REFCNT) decision_step(now_ms());
      } else {
        ec_ready();
      }
      break;
    case SDTX_EVENT_BASE_CONNECTION:
      LOGD("DTX base event state=0x%04x", v);
      if (dtx_query("base event")) dtx_set_base(v, "base event");
      inventory();
      if (g_inv.bound_any && !g_shutdown) latch_set(1, 1, "BASE_CONNECTION event");
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
        if (!dec_running()) ec_ready();
        g_dtx.requery_at = now_ms() + 1500;
      }
      dtx_set_latch(l, "latch event");
      if (l == SDTX_LATCH_OPENED) { g_dtx.slow_confirm_at = 0; LOGI("detach: latch open, the clipboard can be removed"); }
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
  g_dtx.echo_until = g_dtx.slow_confirm_at = 0;
  g_latch = -1;
  detaching_flag(0);
  prop_put("vendor.gb.dgpu.latch", "unknown");
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
  detaching_flag(g_dtx.ec != EC_READY);           /* a stale flag of a crashed instance goes; an open latch keeps it */
  prop_set("vendor.gb.dtx.base", base_name(g_dtx.base));
  prop_set("vendor.gb.dtx.latch", g_dtx.latch == SDTX_LATCH_OPENED ? "opened" : "closed");
  prop_set("vendor.gb.dtx.mode", mode_name(g_dtx.mode));
  LOGI("DTX ready: base %s, latch %s, mode %s; driverless detach requests are confirmed immediately",
       base_name(g_dtx.base), latch_name(g_dtx.latch), mode_name(g_dtx.mode));
  /* latch lifecycle at (re)start: a crashed/restarted instance may have left it locked */
  inventory();
  used();
  if (g_inv.bound_any) latch_set(1, 1, "start: a driver is bound to the dGPU");
  else if (wedged() || g_fault[0]) LOGW("latch left as the EC has it (dGPU %s)", g_wedged ? "wedged" : g_fault);
  else latch_set(0, 1, "start: dGPU driverless");
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
    if (g_dtx.fd < 0) return;
  }
}

/* ------------------------------------------------------------------------------------------------ C. dGPU passes */
/* power/control on -> auto (main process: allowed, pm_runtime_allow is asynchronous). Returns 1 if changed. */
static int rpm_auto(const char *devdir, const char *what) {
  char p[PATH_MAX + 32], v[16];
  if (g_wedged) return 0;
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

/* every upstream PCI bridge of dev (the root port 00:1d.4 on the SB3), nearest first; pm: runtime PM auto so it can
 * drop to D3cold. top = the topmost one. */
static void bridges(const char *dev, int pm, char *top, size_t n) {
  char real[PATH_MAX], p[PATH_MAX + 64];
  top[0] = 0;
  if (!realpath(dev, real)) return;
  char *s;
  while ((s = strrchr(real, '/')) && s != real) {
    *s = 0;
    const char *b = strrchr(real, '/');
    if (!b || !is_bdf(b + 1)) break;
    if (pm && !(g_rs.active && !strcmp(b + 1, rs_port()))) {
      snprintf(p, sizeof p, "bridge %s (above dGPU)", b + 1);
      rpm_auto(real, p);
    }
    snprintf(top, n, "%s", real);
  }
}

static struct { int want, applied, pending, conn; char state[12]; } g_cmp;   /* power hold: compute (9.3), self-test (25.2) */
static int hold_wanted(void) { return g_cmp.want || g_st.want; }   /* the second want bit: the self-test (v1.2) */

/* runtime PM writes allowed in the main process: d3cold_allowed=1 on every NVIDIA function, power/control=auto on the
 * driverless ones (a bound driver owns it: nouveau runpm) and on the upstream bridges. */
static void dgpu_pm_pass(void) {
  if (wedged()) return;
  char dir[PATH_MAX], dev[PATH_MAX + 32], p[PATH_MAX + 64], v[16], top[PATH_MAX];
  rpath(dir, sizeof dir, "/sys/bus/pci/devices");
  for (int i = 0; i < g_inv.n; i++) {
    const struct dfn *f = &g_inv.f[i];
    snprintf(dev, sizeof dev, "%s/%s", dir, f->bdf);
    snprintf(p, sizeof p, "%s/d3cold_allowed", dev);
    /* the store resumes the device synchronously (pci-sysfs, INFERRED): only for driverless functions */
    if (!f->drv[0] && read_text(p, v, sizeof v) >= 0 && !strcmp(v, "0")) write_text(p, "1");
    int held = i == g_inv.vga && (hold_wanted() || g_cmp.applied || g_cmp.pending);
    if (!f->drv[0] && !held) {
      snprintf(p, sizeof p, "dGPU %s", f->bdf);
      rpm_auto(dev, p);
    }
    bridges(dev, 1, top, sizeof top);
  }
}

static void dgpu_report(const char *why) {
  char dir[PATH_MAX], dev[PATH_MAX + 32], top[PATH_MAX], s1[64], s2[64];
  rpath(dir, sizeof dir, "/sys/bus/pci/devices");
  if (!g_inv.n) { LOGI("no NVIDIA function on PCI (%s): base detached or dGPU off", why); return; }
  for (int i = 0; i < g_inv.n; i++) {
    const struct dfn *f = &g_inv.f[i];
    snprintf(dev, sizeof dev, "%s/%s", dir, f->bdf);
    pci_state(dev, s1, sizeof s1);
    bridges(dev, 0, top, sizeof top);
    strcpy(s2, "-");
    if (top[0]) pci_state(top, s2, sizeof s2);
    LOGI("dGPU %s 10de:%s class %06x driver=%s: %s | root port %s: %s", f->bdf, hex4(f->did), f->cls,
         f->drv[0] ? f->drv : "none", s1, top[0] ? strrchr(top, '/') + 1 : "-", s2);
    if (strstr(s1, "D0") && !f->drv[0])
      LOGW("dGPU %s still in D0: root port not runtime-suspended (check its power/control, pcie_port_pm, and that "
           "no other function below it is active)", f->bdf);
    else if (strstr(s1, "D0") && f->drv[0] && !strcmp(f->drv, g_keep_drv))
      LOGW("dGPU %s still in D0 on %s: check that it was loaded with runpm=1, its power/control is auto, and that "
           "nothing keeps its render node busy", f->bdf, f->drv);
  }
}

/* Keeps NVIDIA functions driverless except a VGA function bound to g_keep_drv (nouveau): unbinds go to the worker.
 * While a detach is under way every driver is unbound (nouveau too). Then the main-process runtime PM writes. */
static int g_unbind_pending;
static void dgpu_enforce(const char *why, int report) {
  inventory();
  if (!wedged() && !dec_running()) {
    int releasing = dgpu_releasing();
    char dir[PATH_MAX], p[PATH_MAX + 64];
    struct job j;
    memset(&j, 0, sizeof j);
    j.kind = J_UNBIND;
    j.deadline_ms = UNBIND_DEADLINE_MS;
    rpath(dir, sizeof dir, "/sys/bus/pci/devices");
    for (int i = 0; i < g_inv.n; i++) {
      const struct dfn *f = &g_inv.f[i];
      if (!f->drv[0]) continue;
      int keep = f->vga && g_keep_drv[0] && !strcmp(f->drv, g_keep_drv) && !releasing;
      if (keep) {
        if (report) LOGI("dGPU %s (10de:%s class %06x): bound to %s (opt-in; unbound only by gb_nouveau.sh off or a detach)",
                         f->bdf, hex4(f->did), f->cls, f->drv);
        continue;
      }
      if (!strcmp(f->drv, "nouveau") && refcnt_busy()) {   /* invariant 10: never past the refcnt gate */
        if (!g_unbind_pending)
          LOGE("dGPU %s: nouveau bound and in use while a detach is under way: left bound, latch locked", f->bdf);
        latch_set(1, 0, "nouveau bound during a detach");
        continue;
      }
      if (!g_unbind_pending)
        LOGI("dGPU %s (10de:%s class %06x): unbinding %s (%s)", f->bdf, hex4(f->did), f->cls, f->drv,
             !f->vga ? "non-VGA NVIDIA functions stay driverless" : releasing ? "detach under way" : "no Android user of this driver");
      snprintf(p, sizeof p, "%s/%s/driver/unbind", dir, f->bdf);
      job_add(&j, OP_UNBIND, p, "", f->bdf);
    }
    if (j.nops && !g_unbind_pending && !worker_submit(&j)) g_unbind_pending = 1;
    if (g_dgpu_on) dgpu_pm_pass();
  }
  if (report) dgpu_report(why);
  latch_eval(why);
}

/* ---- staged rescan (INTERFACES 10.4): attach/cancel when nouveau was used (or gb_power removed the functions) */
enum { RS_NONE, RS_PORT_ON, RS_WAIT_D0, RS_RESCAN, RS_RETRY };
static const int rs_delay[3] = {2000, 5000, 15000};

static int dgpu_fault_blocks(void);
/* INTERFACES 8.2 (v1.2): only the faults that block a detach (rpm-error, d0-idle) post N06; rescan, bar and
 * selftest-hang post N20; rebind posts nothing here (the rebind guard posts N02). Every fault: app graphics Intel.
 * (R-8) A fault that blocks a detach also sets Intel mode (keep + noload first, as gb_nouveau.sh's unhealthy path, so
 * the rc's gb_nouveau_off leaves the sick driver alone): the restart N06 asks for then really frees the detach. */
static int gfx_mode_get(void);
static void set_fault(const char *reason) {
  if (g_fault[0] && strcmp(g_fault, "bar")) return;   /* latched for the boot; "bar" may be replaced */
  if (!strcmp(g_fault, reason)) return;
  snprintf(g_fault, sizeof g_fault, "%s", reason);
  char t[220];
  LOGE("dGPU fault: %s (latch stays locked)", reason);
  if (dgpu_fault_blocks()) {
    flag_make("keep");
    flag_make("noload");
    g_gfx.forced = 1;
    if (stack_present() && gfx_mode_get() != GM_INTEL) {
      LOGE("dGPU fault %s blocks the detach: graphics mode set to Intel (the next boot does not load nouveau)", reason);
      prop_set("persist.vendor.gb.gfx_mode", "intel");
    }
    snprintf(t, sizeof t, N06_FMT, reason);
    notify("gb_dgpu", "NVIDIA GPU fault", t);
  } else if (strcmp(reason, "rebind")) {
    snprintf(t, sizeof t, N20_FMT, reason);
    notify("gb_dgpu", N20_TITLE, t);
  }
  gfx_force_intel("fault");
  publish_state(now_ms());
}
static void port_auto(void) {
  char dir[PATH_MAX];
  rpath(dir, sizeof dir, "/sys/bus/pci/devices/%s", rs_port());
  rpm_auto(dir, "rescan: root port");
}
static void rescan_finish(const char *why, int fault, int done) {
  LOGI("rescan: %s", why);
  port_auto();
  g_rs.active = 0;
  g_rs.done = done;
  if (fault) set_fault("rescan");
}
static void rescan_step(long long t);
static void rescan_submit(int kind) {
  char p[PATH_MAX];
  struct job j;
  memset(&j, 0, sizeof j);
  j.kind = kind;
  j.deadline_ms = kind == J_PORT_ON ? POWER_ON_DEADLINE_MS : RESCAN_DEADLINE_MS;
  /* (v1.2, 23.8) with nouveau loaded the kernel probes the re-added dGPU itself: the rebind guard's crash marker is
   * the first op of this job (a failed marker cancels the rescan write) */
  if (kind == J_RESCAN) { inventory(); rb_arm("rescan", &j); }
  rpath(p, sizeof p, "/sys/bus/pci/devices/%s/%s", rs_port(), kind == J_PORT_ON ? "power/control" : "rescan");
  job_add(&j, OP_WRITE, p, kind == J_PORT_ON ? "on" : "1", rs_port());
  g_rs.phase = kind == J_PORT_ON ? RS_PORT_ON : RS_RESCAN;
  if (worker_submit(&j)) rescan_finish("worker unavailable", 0, 1);
}
static int rescan_stop(void) { return g_rs.abort || !base_attached() || dec_running(); }
static void rescan_step(long long t) {
  if (!g_rs.active || g_rs.phase == RS_PORT_ON || g_rs.phase == RS_RESCAN) return;   /* a worker job is pending */
  if (rescan_stop()) { rescan_finish("stopped (base not attached, or a detach started)", 0, 0); return; }
  char p[PATH_MAX], v[16];
  if (g_rs.phase == RS_NONE || g_rs.phase == RS_RETRY) {
    if (g_rs.phase == RS_RETRY && t < g_rs.next_at) return;
    g_rs.attempt++;
    LOGI("rescan: attempt %d of 4 on root port %s", g_rs.attempt, rs_port());
    if (read_text(rpath(p, sizeof p, "/sys/bus/pci/devices/%s/power/control", rs_port()), v, sizeof v) < 0 || strcmp(v, "on")) {
      rescan_submit(J_PORT_ON);
      return;
    }
    g_rs.phase = RS_WAIT_D0;
    g_rs.next_at = t + D0_WAIT_MS;
  }
  if (g_rs.phase == RS_WAIT_D0) {
    int d0 = read_text(rpath(p, sizeof p, "/sys/bus/pci/devices/%s/power_state", rs_port()), v, sizeof v) > 0 && !strcmp(v, "D0");
    if (!d0 && t < g_rs.next_at) return;
    if (!d0) LOGW("rescan: root port %s not in D0 after %d s (%s): rescanning anyway", rs_port(), D0_WAIT_MS / 1000, v);
    rescan_submit(J_RESCAN);
  }
}
static void rescan_job_done(const struct job *j, int wedge) {
  if (!g_rs.active) return;
  long long t = now_ms();
  if (wedge) { g_rs.active = 0; g_rs.done = 1; return; }   /* no further writes (not even auto) */
  if (rescan_stop()) { rescan_finish("stopped (base not attached, or a detach started)", 0, 0); return; }
  if (j->kind == J_PORT_ON) {
    g_rs.phase = RS_WAIT_D0;
    g_rs.next_at = t + D0_WAIT_MS;
    rescan_step(t);
    return;
  }
  if (j->nops && j->op[0].kind == OP_MARK) {      /* the rebind guard's marker rode on this job */
    g_rb.mark_ok = op_ok(&j->op[0]);
    if (!g_rb.mark_ok) { LOGW("rescan: rebind marker not written: rescan write skipped, retried"); rb_end("marker not written", 0); }
  }
  inventory();
  if (g_inv.n) {
    char m[64];
    snprintf(m, sizeof m, "%d NVIDIA function(s) back after attempt %d", g_inv.n, g_rs.attempt);
    rescan_finish(m, 0, 0);
    if (g_inv.vga >= 0 && !g_inv.f[g_inv.vga].bar_ok) {
      LOGE("rescan: dGPU %s came back with a zero BAR: nouveau must not use it", g_inv.f[g_inv.vga].bdf);
      set_fault("bar");
    } else if (!strcmp(g_fault, "bar")) {
      LOGI("rescan: BAR assigned again: fault bar cleared");
      g_fault[0] = 0;
    }
    g_dgpu_due = t + 500;
    g_dgpu_report_due = t + 8000;
    g_tick_due = t + 100;
    return;
  }
  if (g_rs.attempt >= 4) { rescan_finish("no NVIDIA function after 4 attempts with the base attached", 1, 1); return; }
  g_rs.phase = RS_RETRY;
  g_rs.next_at = t + rs_delay[g_rs.attempt - 1];
}
static void rescan_check(long long t) {
  if (g_inv.n > 0) g_rs.done = 0;                 /* present: a later absence may rescan again */
  if (g_rs.active) { rescan_step(t); return; }
  if (g_rs.done || g_wedged || dec_running() || g_inv.n > 0) return;
  if (!base_attached() || g_dtx.ec != EC_READY || g_dtx.latch == SDTX_LATCH_OPENED) return;
  if (!used() && !g_removed) return;
  LOGI("dGPU: base attached but no NVIDIA function (nouveau %s this boot): staged rescan of %s",
       g_used ? "used" : "not used, functions removed", rs_port());
  memset(&g_rs, 0, sizeof g_rs);
  g_rs.active = 1;
  rescan_step(t);
}

/* ---- compute power hold (9.3 compute-state): power/control=on by the worker, auto again from the main process */
static void hold_eval(void) {
  if (g_inv.vga < 0) { g_cmp.applied = 0; return; }
  char p[PATH_MAX];
  rpath(p, sizeof p, "/sys/bus/pci/devices/%s/power/control", g_inv.f[g_inv.vga].bdf);
  /* (R-6) no hold inside a load window (gb_nouveau's health check, the rebind guard's watch): D3cold is checked there */
  if (hold_wanted() && !g_cmp.applied && !g_cmp.pending && !g_wedged && !dec_running() && !load_window_open()) {
    struct job j;
    memset(&j, 0, sizeof j);
    j.kind = J_HOLD_ON;
    j.deadline_ms = POWER_ON_DEADLINE_MS;
    job_add(&j, OP_WRITE, p, "on", g_inv.f[g_inv.vga].bdf);
    if (!worker_submit(&j)) {
      g_cmp.pending = 1;
      LOGI("power hold: dGPU power/control -> on (%s)", g_cmp.want ? "compute model loaded" : "self-test running");
    }
  } else if (!hold_wanted() && g_cmp.applied && !g_cmp.pending) {
    g_cmp.applied = 0;
    if (!g_wedged) {
      int r = write_text(p, "auto");
      LOGI("power hold released: dGPU power/control -> auto%s%s", r ? ": " : "", r ? strerror(-r) : "");
    }
  }
}
static void hold_job_done(const struct job *j, int wedge) {
  g_cmp.pending = 0;
  if (!wedge && j->nops && op_ok(&j->op[0])) g_cmp.applied = 1;
  hold_eval();
}

/* ---- optional IOMMU DMA domain for the dGPU group (P3 item 7, HW-gated, compiled out by default; no property) */
static int g_iommu_dmafq = GB_POWER_IOMMU_DMAFQ;
static int g_iommu_done;
static void iommu_check(void) {
  if (!g_iommu_dmafq || g_iommu_done || g_wedged || g_inv.vga < 0 || g_inv.bound_any || g_wk.busy || dec_running()) return;
  char p[PATH_MAX], grp[32], dir[PATH_MAX + 64], v[32];
  link_base(rpath(p, sizeof p, "/sys/bus/pci/devices/%s/iommu_group", g_inv.f[g_inv.vga].bdf), grp, sizeof grp);
  if (!grp[0] || !is_num(grp)) return;
  g_iommu_done = 1;
  rpath(dir, sizeof dir, "/sys/kernel/iommu_groups/%s/devices", grp);
  DIR *d = opendir(dir);
  if (!d) return;
  struct dirent *e;
  int pure = 1;
  while ((e = readdir(d))) {
    if (e->d_name[0] == '.') continue;
    char vp[PATH_MAX + 400];
    snprintf(vp, sizeof vp, "%s/%s/vendor", dir, e->d_name);
    if (read_text(vp, v, sizeof v) <= 0 || strcasecmp(v, "0x10de")) pure = 0;
  }
  closedir(d);
  if (!pure) { LOGI("iommu group %s holds non-NVIDIA devices: DMA domain left alone", grp); return; }
  rpath(p, sizeof p, "/sys/kernel/iommu_groups/%s/type", grp);
  if (read_text(p, v, sizeof v) > 0 && !strcmp(v, "DMA-FQ")) return;
  struct job j;
  memset(&j, 0, sizeof j);
  j.kind = J_IOMMU;
  j.deadline_ms = POWER_ON_DEADLINE_MS;
  job_add(&j, OP_WRITE, p, "DMA-FQ", g_inv.f[g_inv.vga].bdf);
  LOGI("iommu group %s (%s -> DMA-FQ) for the driverless dGPU", grp, v);
  worker_submit(&j);
}

static void job_done(const struct job *j, int wedge) {
  switch (j->kind) {
    case J_UNBIND: g_unbind_pending = 0; g_dgpu_due = now_ms() + 200; break;
    case J_DETACH: case J_DREMOVE: decision_worker_done(j, wedge); break;
    case J_PORT_ON: case J_RESCAN: rescan_job_done(j, wedge); break;
    case J_HOLD_ON: hold_job_done(j, wedge); break;
    case J_MARK: mark_job_done(j, wedge); break;
    default: break;
  }
  publish_state(now_ms());
}

/* ------------------------------------------------------------------------------------------------ state */
/* INTERFACES 8.1: first matching rule wins */
static int g_refcnt = -1;
static char g_state[24];
static char g_bcast_sig[200];                     /* (v1.2) the graphics fields of the last broadcast check (10.7) */

static int mode_valid(const char *m) {           /* v1.2: auto (persist is no longer produced, still accepted) */
  return !strcmp(m, "default") || !strcmp(m, "disabled") || !strcmp(m, "once") || !strcmp(m, "test") ||
         !strcmp(m, "auto") || !strcmp(m, "persist");
}
static void prop_mode(char *m) { prop_get("vendor.gb.nouveau.mode", m); if (!mode_valid(m)) m[0] = 0; }
static void prop_result(char *r) {               /* informational: [a-z0-9:._-] only, else empty */
  prop_get("vendor.gb.nouveau.result", r);
  for (char *s = r; *s; s++)
    if (!((*s >= 'a' && *s <= 'z') || (*s >= '0' && *s <= '9') || *s == ':' || *s == '.' || *s == '_' || *s == '-')) { r[0] = 0; return; }
}
static void derive_state(char *st, size_t n, int *busy_n) {
  char mode[PROP_VALUE_MAX], result[PROP_VALUE_MAX];
  prop_mode(mode);
  prop_result(result);
  *busy_n = 0;
  if (wedged()) snprintf(st, n, "wedged");
  else if (g_fault[0]) snprintf(st, n, "fault");
  else if (dec_running()) snprintf(st, n, "detaching");
  else if (!strcmp(mode, "disabled") || g_cmd_kill) snprintf(st, n, "disabled");
  else if (!strcmp(result, "crashloop")) snprintf(st, n, "crashloop");
  else if (g_inv.vga < 0) snprintf(st, n, "unavailable");
  else if (!strcmp(result, "loading") && !g_inv.nouveau_bound) snprintf(st, n, "loading");
  else if (g_inv.nouveau_bound && g_refcnt > 0) {
    int k = g_hold.total < 1 ? 1 : g_hold.total > 999 ? 999 : g_hold.total;
    *busy_n = k;
    snprintf(st, n, "busy:%d", k);
  } else if (g_inv.nouveau_bound) snprintf(st, n, "standby");
  else snprintf(st, n, "off");
}

static void compute_publish(void) {
  const char *s = "stopped";
  if (prop_is("init.svc.gb_gpu_compute", "running")) s = g_cmp.conn && g_cmp.state[0] ? g_cmp.state : "starting";
  prop_put("vendor.gb.compute.state", s);
}

static void watch_update(long long t);
static const char *gfx_pending(void);
static void publish_state(long long t) {
  char st[24], hp[PROP_VALUE_MAX], sig[200];
  int n;
  derive_state(st, sizeof st, &n);
  if (strcmp(st, g_state)) {
    LOGI("dGPU state: %s -> %s", g_state[0] ? g_state : "-", st);
    snprintf(g_state, sizeof g_state, "%s", st);
    g_bcast_pending = 1;
  }
  prop_put("vendor.gb.dgpu.state", st);
  hp[0] = 0;
  if (g_refcnt > 0) holders_prop(hp, sizeof hp);
  prop_put("vendor.gb.dgpu.holders", hp);
  /* (v1.2, 10.7) also on changes of gfx_mode, gfx_policy, gfx_active, gfx_reason, gfx_test, pending, detach_ref,
   * apps_sync, persist (= mode not intel), and once after boot while apps_sync is 0 */
  snprintf(sig, sizeof sig, "%d %d %d %s %s %s %d %d %d", g_gfx.mode, g_gfx.policy, g_gfx.active, g_gfx.reason, g_gfx.test,
           gfx_pending(), g_gfx.detach_ref, apps_sync(), g_gfx.mode != GM_INTEL);
  if (strcmp(sig, g_bcast_sig)) {
    if (g_bcast_sig[0]) g_bcast_pending = 1;
    snprintf(g_bcast_sig, sizeof g_bcast_sig, "%s", sig);
  }
  if (!g_gfx.boot_bcast && boot_completed()) {
    g_gfx.boot_bcast = 1;
    if (!apps_sync()) g_bcast_pending = 1;
  }
  /* 10.7: state broadcast to GbGpu, coalesced to one per 2 s, after boot, only with the app present */
  if (g_bcast_pending && t - g_bcast_last >= BCAST_MS && boot_completed()) {
    g_bcast_pending = 0;
    g_bcast_last = t;
    if (path_exists("/odm/app/GbGpu/GbGpu.apk")) nq_put("broadcast", "kind=broadcast\n");
  }
  watch_update(t);
}

/* ---- watchdog (8.2) */
static long long g_d0_since;
static void watchdog(long long t) {
  if (!g_inv.nouveau_bound) { g_d0_since = 0; return; }
  char dir[PATH_MAX], p[PATH_MAX + 64], rs[24], ps[16];
  rpath(dir, sizeof dir, "/sys/bus/pci/devices");
  for (int i = 0; i < g_inv.n; i++) {
    if (strcmp(g_inv.f[i].drv, "nouveau")) continue;
    snprintf(p, sizeof p, "%s/%s/power/runtime_status", dir, g_inv.f[i].bdf);
    if (read_text(p, rs, sizeof rs) > 0 && !strcmp(rs, "error")) { set_fault("rpm-error"); return; }
  }
  snprintf(p, sizeof p, "%s/%s/power_state", dir, g_inv.f[g_inv.vga].bdf);
  int d0 = read_text(p, ps, sizeof ps) > 0 && !strcmp(ps, "D0");
  if (d0 && g_refcnt == 0 && !hold_wanted() && !g_cmp.applied && !dec_running() && !g_wk.busy) {
    if (!g_d0_since) g_d0_since = t;
    else if (t - g_d0_since > D0_IDLE_FAULT_MS) set_fault("d0-idle");
  } else {
    g_d0_since = 0;
  }
}

/* ================================================================================================ G. graphics (v1.2) */
/* ---- inputs (INTERFACES 22.2, 23.1, 25.1) */
static int gfx_mode_get(void) {                  /* exactly auto/intel/nvidia, anything else (unset) = auto */
  char v[PROP_VALUE_MAX];
  prop_get("persist.vendor.gb.gfx_mode", v);
  return !strcmp(v, "intel") ? GM_INTEL : !strcmp(v, "nvidia") ? GM_NVIDIA : GM_AUTO;
}
static int gfx_policy_get(void) {
  char v[PROP_VALUE_MAX];
  prop_get("persist.vendor.gb.gfx_apps", v);
  return !strcmp(v, "all") ? GPOL_ALL : GPOL_GAMES;
}
/* /odm/etc/gb/gfx_build_id: one readable line of printable ASCII (absent = the driver package is not configured) */
static int gfx_build_id(char *out, size_t n) {
  char p[PATH_MAX], b[1024];
  out[0] = 0;
  int l = read_text(rpath(p, sizeof p, "/odm/etc/gb/gfx_build_id"), b, sizeof b);
  if (l <= 0 || (size_t)l >= n || (size_t)l >= sizeof b - 1) return -1;
  for (const char *s = b; *s; s++)
    if ((unsigned char)*s < 0x20 || (unsigned char)*s > 0x7e) return -1;   /* also a second line */
  snprintf(out, n, "%s", b);
  return 0;
}
static void gfx_krel(char *out, size_t n) {      /* uname -r; test trees (-r): <root>/proc/sys/kernel/osrelease */
  char p[PATH_MAX];
  struct utsname u;
  out[0] = 0;
  if (g_root[0]) { if (read_text(rpath(p, sizeof p, "/proc/sys/kernel/osrelease"), out, n) < 0) out[0] = 0; return; }
  if (!uname(&u)) snprintf(out, n, "%s", u.release);
}
static void gfx_inputs(void) {
  char v[PROP_VALUE_MAX], w[PROP_VALUE_MAX];
  int have_id = !gfx_build_id(g_gfx.gfx, sizeof g_gfx.gfx);
  prop_get("ro.gfx.driver.0", v);
  int cap = have_id && stack_present() && !strcmp(v, "gb.sb3.gfxdriver") && path_exists("/odm/app/GbGfxDriver/GbGfxDriver.apk");
  int pol = gfx_policy_get();
  if (pol != g_gfx.policy || cap != g_gfx.cap) g_apps.dirty = 1;
  g_gfx.cap = cap;
  g_gfx.policy = pol;
  g_gfx.mode = gfx_mode_get();
  prop_get("ro.build.id", v);
  prop_get("ro.build.version.incremental", w);
  snprintf(g_gfx.build, sizeof g_gfx.build, "%s/%s", v, w);
  gfx_krel(g_gfx.krel, sizeof g_gfx.krel);
}

/* ---- self-test record (25.4): the truth; persist.vendor.gb.gfx_ok is only its mirror and never read */
static int real_owner_root(const struct stat *st) { return st->st_uid == 0 || (g_root[0] && st->st_uid == getuid()); }
static int (*g_owner_root)(const struct stat *st) = real_owner_root;   /* (test trees are owned by the test user) */
#define REC_PATH "/data/vendor/gb/selftest/result"
#define REC_MARKER "/data/vendor/gb/selftest/running"
static const char *const g_rec_keys[] = {"verdict", "reason", "build", "krel", "gfx", "t", "attempts", "crashes", "report"};
static int rec_val_ok(int ki, const char *v) {
  size_t l = strlen(v);
  switch (ki) {
    case 0: return !strcmp(v, "pass") || !strcmp(v, "fail") || !strcmp(v, "error");
    case 1:
      if (l > 40) return 0;
      for (; *v; v++) if (!((*v >= 'a' && *v <= 'z') || (*v >= '0' && *v <= '9') || *v == ' ' || *v == ':' || *v == '.' || *v == '_' || *v == '-')) return 0;
      return 1;
    case 5: return l >= 1 && l <= 12 && is_num(v);
    case 6: case 7: return l >= 1 && l <= 6 && is_num(v);
    case 8: {
      if (!strcmp(v, "-")) return 1;                /* gb_power's own verdicts carry no report */
      if (l < 14 || l > 40 || strncmp(v, "selftest-", 9) || strcmp(v + l - 4, ".txt")) return 0;
      for (size_t i = 9; i < l - 4; i++) if (v[i] < '0' || v[i] > '9') return 0;
      return 1;
    }
    default:                                      /* build, krel, gfx: non-empty printable ASCII */
      if (!l || l > (ki == 4 ? 519u : 99u)) return 0;
      for (; *v; v++) if ((unsigned char)*v < 0x20 || (unsigned char)*v > 0x7e) return 0;
      return 1;
  }
}
/* O_NOFOLLOW, regular, owned by root, <= 2 KiB, every key present once and well-formed: 0; else < 0 */
static int rec_parse_file(struct strec *r) {
  char p[PATH_MAX], b[2100];
  memset(r, 0, sizeof *r);
  int fd = open(rpath(p, sizeof p, REC_PATH), O_RDONLY | O_NOFOLLOW | O_CLOEXEC), n = 0;
  if (fd < 0) return -1;
  struct stat st;
  if (fstat(fd, &st) || !S_ISREG(st.st_mode) || !g_owner_root(&st) || st.st_size > 2048) { close(fd); return -2; }
  for (;;) {
    ssize_t x = read(fd, b + n, sizeof b - 1 - n);
    if (x < 0 && errno == EINTR) continue;
    if (x <= 0) break;
    n += (int)x;
    if (n >= (int)sizeof b - 1) break;
  }
  close(fd);
  if (n > 2048) return -2;
  b[n] = 0;
  unsigned seen = 0;
  for (char *l = strtok(b, "\n"); l; l = strtok(NULL, "\n")) {
    char *eq = strchr(l, '=');
    if (!eq) return -3;
    *eq = 0;
    const char *v = eq + 1;
    int ki = -1;
    for (int i = 0; i < 9; i++) if (!strcmp(l, g_rec_keys[i])) ki = i;
    if (ki < 0) continue;                         /* unknown keys are ignored */
    if ((seen & (1u << ki)) || !rec_val_ok(ki, v)) return -3;
    seen |= 1u << ki;
    switch (ki) {
      case 0: snprintf(r->verdict, sizeof r->verdict, "%s", v); break;
      case 1: snprintf(r->reason, sizeof r->reason, "%s", v); break;
      case 2: snprintf(r->build, sizeof r->build, "%s", v); break;
      case 3: snprintf(r->krel, sizeof r->krel, "%s", v); break;
      case 4: snprintf(r->gfx, sizeof r->gfx, "%s", v); break;
      case 5: r->t = atoll(v); break;
      case 6: r->attempts = atoi(v); break;
      case 7: r->crashes = atoi(v); break;
      default: snprintf(r->report, sizeof r->report, "%s", v); break;
    }
  }
  if (seen != 0x1ffu) return -3;
  r->valid = 1;
  return 0;
}
static void rec_stat_now(int *have, long long *mt, long long *sz, long long *ino) {
  char p[PATH_MAX];
  struct stat st;
  *have = !lstat(rpath(p, sizeof p, REC_PATH), &st);
  *mt = *have ? (long long)st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec : 0;
  *sz = *have ? (long long)st.st_size : 0;
  *ino = *have ? (long long)st.st_ino : 0;
}
static void rec_validate(const char *why) {
  struct strec r;
  char old[8];
  int had = g_st.rec.valid, e = rec_parse_file(&r);
  snprintf(old, sizeof old, "%s", g_st.rec.verdict);
  if (e) memset(&r, 0, sizeof r);
  g_st.rec = r;
  rec_stat_now(&g_st.rec_stat, &g_st.rec_mt, &g_st.rec_sz, &g_st.rec_ino);
  if (e < -1) LOGW("self-test record invalid (%s, check %d): ignored", why, e);
  else if (r.valid && (!had || strcmp(old, r.verdict)))
    LOGI("self-test record (%s): verdict %s%s%s, attempts %d, crashes %d", why, r.verdict, r.reason[0] ? " " : "", r.reason,
         r.attempts, r.crashes);
}
static void rec_check_changed(void) {            /* re-validated whenever the file changed (stat only) */
  int have;
  long long mt, sz, ino;
  rec_stat_now(&have, &mt, &sz, &ino);
  if (have != g_st.rec_stat || mt != g_st.rec_mt || sz != g_st.rec_sz || ino != g_st.rec_ino) rec_validate("record changed");
}
static int rec_same_id(void) {
  return g_st.rec.valid && g_gfx.gfx[0] && !strcmp(g_st.rec.build, g_gfx.build) && !strcmp(g_st.rec.krel, g_gfx.krel) &&
         !strcmp(g_st.rec.gfx, g_gfx.gfx);
}
/* 1 pass, 0 fail, -2 error (for this identity), -1 no valid record for this identity (none or stale) */
static int gfx_ok_now(void) {
  if (!rec_same_id()) return -1;
  return !strcmp(g_st.rec.verdict, "pass") ? 1 : !strcmp(g_st.rec.verdict, "fail") ? 0 : -2;
}
static int st_dir_ok(void) {                     /* selftest/: a real directory, root, 0700 (before gb_power writes) */
  char p[PATH_MAX];
  struct stat st;
  return !lstat(rpath(p, sizeof p, "/data/vendor/gb/selftest"), &st) && S_ISDIR(st.st_mode) && g_owner_root(&st) &&
         (st.st_mode & 07777) == 0700;
}
/* gb_power's own verdicts (interrupted, crash, hang; 25.5, 25.6): .tmp O_EXCL, fsync, rename, directory fsync */
static int rec_write(const char *verdict, const char *reason, int attempts, int crashes) {
  char b[1200];
  if (!st_dir_ok()) { LOGW("self-test: selftest/ is not a root-only directory: verdict %s %s not written", verdict, reason); return -1; }
  int l = snprintf(b, sizeof b, "verdict=%s\nreason=%s\nbuild=%s\nkrel=%s\ngfx=%s\nt=%lld\nattempts=%d\ncrashes=%d\nreport=-\n",
                   verdict, reason, g_gfx.build, g_gfx.krel[0] ? g_gfx.krel : "-", g_gfx.gfx[0] ? g_gfx.gfx : "-",
                   (long long)time(NULL), attempts, crashes);
  if (l <= 0 || l >= (int)sizeof b) return -1;
  int r = file_save(REC_PATH, b, (size_t)l, 0644);
  dir_sync("/data/vendor/gb/selftest");
  LOGW("self-test: verdict=%s reason=%s written by gb_power (attempts %d, crashes %d)%s", verdict, reason, attempts, crashes,
       r ? ": write failed" : "");
  rec_validate("own verdict");
  return r;
}
static void st_marker_remove(void) {
  char p[PATH_MAX];
  if (unlink(rpath(p, sizeof p, REC_MARKER)) && errno != ENOENT) LOGW("%s: %s", p, strerror(errno));
  dir_sync("/data/vendor/gb/selftest");
}
static void n18(const char *reason) {           /* once per identity (25.4; per gb_power run) */
  char t[260], id[720];
  snprintf(id, sizeof id, "%s|%s|%s", g_gfx.build, g_gfx.krel, g_gfx.gfx);
  if (!strcmp(id, g_st.n18_id)) { LOGI("self-test: fail again (%s): N18 already posted for this identity", reason); return; }
  snprintf(g_st.n18_id, sizeof g_st.n18_id, "%s", id);
  snprintf(t, sizeof t, N18_FMT, reason[0] ? reason : "fail");
  notify("gb_gfx", N18_TITLE, t);
}

/* ---- derivation (23.2): the first matching row wins; NULL = nvidia (reason ok) */
static int gfx_attached(void) {                  /* not-feasible: the clipboard battery is low, the base is there */
  return dtx_ready() && (g_dtx.base == SDTX_BASE_ATTACHED || g_dtx.base == SDTX_DETACH_NOT_FEASIBLE);
}
static int gfx_recovered(const char *state) {
  return flag_exists("keep") || flag_exists("noload") || g_gfx.forced || !strcmp(state, "crashloop");
}
static int gfx_loading(const char *state) {      /* the properties only select a reason here, never a permission */
  if (!strcmp(state, "loading") || load_window_open()) return 1;   /* (load_window_open includes the rebind watch) */
  return g_nv.started_self && !g_inv.nouveau_bound && svc_running("gb_nouveau");
}
static int gfx_bound(const char *state) {
  return g_inv.nouveau_bound && (!strcmp(state, "standby") || !strncmp(state, "busy:", 5));
}
static const char *gfx_derive(const char *st) {
  int ok = gfx_ok_now();
  if (!g_gfx.cap) return "nopkg";                                       /* 1 */
  if (!dtx_ready()) return "nodtx";                                     /* 2 */
  if (g_cmd_kill) return "kill";                                        /* 3 */
  if (g_cmd_test) return "test";                                        /* 4 */
  if (gfx_recovered(st)) return "crashloop";                            /* 5 */
  if (!strcmp(st, "wedged")) return "wedged";                           /* 6 */
  if (!strcmp(st, "fault")) return "fault";                             /* 7 */
  if (g_gfx.mode == GM_INTEL) return "mode";                            /* 8 */
  if (!gfx_attached()) return "detached";                               /* 9 */
  if (!strcmp(st, "detaching") || g_dtx.ec != EC_READY || g_pend.kind) return "detaching";   /* 10 */
  if (gfx_loading(st)) return "loading";                                /* 11 */
  if (!gfx_bound(st)) return "nodriver";                                /* 12 */
  if (ok == -1) return "untested";                                      /* 13 */
  if (ok == -2) return "testerror";                                     /* 14 */
  if (ok == 0) return "selftest";                                       /* 15 */
  return NULL;                                                          /* 16 */
}
static const char *gfx_pending(void) {
  if (g_pend.kind) return "detach";
  if (g_gfx.mode == GM_INTEL && g_inv.nouveau_bound && g_refcnt > 0) return "intel";
  return "";
}

/* ---- transitions (23.3) and the app-graphics session marker (23.10) */
static void gfx_publish(const char *reason) {
  prop_put("vendor.gb.gfx_active", g_gfx.active ? "nvidia" : "intel");
  prop_put("vendor.gb.gfx_reason", reason);
  snprintf(g_gfx.reason, sizeof g_gfx.reason, "%s", reason);
}
#define SESS_PATH "/data/vendor/gb/gfx_session"
static void sess_job(int op, int tag) {
  char p[PATH_MAX], v[128] = "", bid[48];
  struct job j;
  memset(&j, 0, sizeof j);
  j.kind = J_MARK;
  j.tag = tag;
  j.deadline_ms = MARK_DEADLINE_MS;
  rpath(p, sizeof p, SESS_PATH);
  if (op == OP_MARK) {
    boot_id(bid, sizeof bid);
    snprintf(v, sizeof v, "boot=%s t=%lld", bid, (long long)time(NULL));
  }
  job_add(&j, op, p, v, "");
  if (worker_submit(&j)) { LOGW("gfx_session: worker unavailable (retried at the next tick)"); return; }
  g_sess.job = op == OP_MARK ? 1 : 2;
}
static int unclean_read(void) {
  char p[PATH_MAX], v[16];
  if (read_text(rpath(p, sizeof p, "/data/vendor/gb/gfx_unclean"), v, sizeof v) <= 0 || !is_num(v) || strlen(v) > 6) return 0;
  return atoi(v);
}
static void unclean_write(int n) {
  char b[16];
  if (n == unclean_read() && (n == 0 || path_exists("/data/vendor/gb/gfx_unclean"))) return;
  int l = snprintf(b, sizeof b, "%d\n", n);
  if (file_save("/data/vendor/gb/gfx_unclean", b, (size_t)l, 0644)) LOGW("gfx_unclean: write failed");
}
static void gfx_to_intel(const char *reason) {
  if (!g_gfx.active) { gfx_publish(reason); return; }
  g_gfx.active = 0;
  gfx_publish(reason);                            /* FIRST: new app Vulkan instances stop asking for NVK */
  nodes_enforce();                                /* render node 0660 (unless a decision, off or load window owns it) */
  if (!g_sess.job) sess_job(OP_UNMARK, MK_UNSESS);   /* a clean end of the NVIDIA session */
  g_gfx.seq++;
  g_bcast_pending = 1;
  LOGI("app graphics: Intel (%s); apps already on NVIDIA keep it until they exit (the refcnt gate still applies)", reason);
}
/* called by decision_start ("detaching"), set-mode intel ("mode"), the rebind failure ("crashloop"), set_fault ("fault") */
static void gfx_force_intel(const char *reason) { gfx_to_intel(reason); }
static void gfx_eval(void) {
  char st[24];
  int bn;
  derive_state(st, sizeof st, &bn);
  const char *why = gfx_derive(st);
  if (why || g_shutdown) {
    gfx_to_intel(why ? why : "loading");
    if (g_sess.have && !g_sess.job) sess_job(OP_UNMARK, g_sess.stale ? MK_SESS_STALE : MK_UNSESS);
    return;
  }
  if (g_gfx.active) { gfx_publish("ok"); return; }
  /* intel -> nvidia, in this order (main process, nothing blocks) */
  if (!foreign()) {                               /* 1: apps import i915 buffers into nouveau: no rmmod this boot */
    LOGI("app graphics: setting foreign (no rmmod of nouveau this boot)");
    prop_set("vendor.gb.nouveau.foreign", "1");
    flag_make("foreign");
  }
  if (g_sess.job) { gfx_publish("loading"); return; }   /* 2: the marker op is in flight */
  if (g_sess.stale) { sess_job(OP_UNMARK, MK_SESS_STALE); gfx_publish("loading"); return; }
  if (!g_sess.have) { sess_job(OP_MARK, MK_SESS); gfx_publish("loading"); return; }
  if (nodes_enforce_skip()) { gfx_publish("loading"); return; }   /* the node cannot be raised yet (own off window) */
  g_gfx.active = 1;
  nodes_enforce();                                /* 3: render node 0666 */
  gfx_publish("ok");                              /* 4 */
  g_gfx.seq++;
  LOGI("app graphics: NVIDIA for apps started from now on (system UI stays on Intel)");
  if (g_gfx.n16) { g_gfx.n16 = 0; notify("gb_gfx", N16_TITLE, N16_TEXT); }   /* 5 */
  g_bcast_pending = 1;                            /* 6 */
}
static void mark_job_done(const struct job *j, int wedge) {
  int ok = !wedge && j->nops > 0 && op_ok(&j->op[0]);
  switch (j->tag) {
    case MK_SESS:
      g_sess.job = 0;
      if (ok) g_sess.have = 1;
      else LOGW("gfx_session: marker not written: app graphics stay on Intel, retried at the next tick");
      g_tick_due = now_ms();
      break;
    case MK_UNSESS:
      g_sess.job = 0;
      if (ok) { g_sess.have = 0; trace("unclean=0"); unclean_write(0); }
      else LOGW("gfx_session: not removed (retried at the next tick)");
      break;
    case MK_SESS_STALE:
      g_sess.job = 0;
      if (ok) { g_sess.have = 0; g_sess.stale = 0; }
      else LOGW("gfx_session of an earlier boot: not removed (retried)");
      g_tick_due = now_ms();
      break;
    case MK_RB:
      g_rb.mark_job = 0;
      g_rb.mark_ok = ok;
      if (!ok) LOGW("rebind guard: crash marker not written (the probe is watched anyway)");
      break;
    case MK_UNRB:
      if (!wedge && j->nops > 0 && j->op[0].res == -ESTALE)
        LOGI("rebind guard: the crash marker belongs to another window now: left alone");
      else if (!ok) LOGW("rebind guard: crash marker not removed");
      break;
    case MK_DSESS:
      g_dsess.job = 0;
      if (ok) g_dsess.have = 1;
      else LOGW("dgpu_session: marker not written (retried at the next tick)");
      break;
    case MK_UNDSESS:
      g_dsess.job = 0;
      if (ok) { g_dsess.have = 0; trace("dunclean=0"); dunclean_write(0); }
      else LOGW("dgpu_session: not removed (retried at the next tick)");
      break;
    case MK_DSESS_STALE:
      g_dsess.job = 0;
      if (ok) { g_dsess.have = 0; g_dsess.stale = 0; }
      else LOGW("dgpu_session of an earlier boot: not removed (retried)");
      break;
    default: break;
  }
}
/* (R-4, 23.10a) dgpu_session: written through the worker on the first tick with nouveau bound outside every load
 * window (after the crash-loop marker is gone), removed when nouveau is no longer bound and at a clean shutdown. One of
 * an earlier boot = that boot ended while nouveau was bound (compute, a failed self-test, Intel pending, ...): two in a
 * row force Intel (N21, unless N19 did it in the same check). Counter /data/vendor/gb/dgpu_unclean. */
#define DSESS_PATH "/data/vendor/gb/dgpu_session"
static void dsess_job(int op, int tag) {
  char p[PATH_MAX], v[128] = "", bid[48];
  struct job j;
  memset(&j, 0, sizeof j);
  j.kind = J_MARK;
  j.tag = tag;
  j.deadline_ms = MARK_DEADLINE_MS;
  rpath(p, sizeof p, DSESS_PATH);
  if (op == OP_MARK) {
    boot_id(bid, sizeof bid);
    snprintf(v, sizeof v, "boot=%s t=%lld", bid, (long long)time(NULL));
  }
  job_add(&j, op, p, v, "");
  if (worker_submit(&j)) { LOGW("dgpu_session: worker unavailable (retried at the next tick)"); return; }
  g_dsess.job = 1;
}
static int dunclean_read(void) {
  char p[PATH_MAX], v[16];
  if (read_text(rpath(p, sizeof p, "/data/vendor/gb/dgpu_unclean"), v, sizeof v) <= 0 || !is_num(v) || strlen(v) > 6) return 0;
  return atoi(v);
}
static void dunclean_write(int n) {
  char b[16];
  if (n == dunclean_read() && (n == 0 || path_exists("/data/vendor/gb/dgpu_unclean"))) return;
  int l = snprintf(b, sizeof b, "%d\n", n);
  if (file_save("/data/vendor/gb/dgpu_unclean", b, (size_t)l, 0644)) LOGW("dgpu_unclean: write failed");
}
static void dsess_start_check(void) {
  char p[PATH_MAX], m[160], bid[48], mb[48] = "";
  if (read_text(rpath(p, sizeof p, DSESS_PATH), m, sizeof m) < 0) return;
  boot_id(bid, sizeof bid);
  const char *b = strstr(m, "boot=");
  if (b) sscanf(b + 5, "%47s", mb);
  if (mb[0] && !strcmp(mb, bid)) { g_dsess.have = 1; LOGI("dgpu_session of this boot adopted (gb_power restarted)"); return; }
  int n = dunclean_read() + 1;
  LOGW("dgpu_session of an earlier boot (%s): it ended while nouveau was bound (%d unclean in a row)", mb[0] ? mb : "?", n);
  if (g_gfx.forced) {
    n = 0;                                        /* N19 set Intel in this same check */
  } else if (n >= UNCLEAN_MAX) {
    LOGE("dGPU: %d unclean endings in a row with nouveau bound: graphics mode set to Intel (N21)", n);
    if (gfx_mode_get() != GM_INTEL) {
      prop_set("persist.vendor.gb.gfx_mode", "intel");
      notify("gb_gfx", N21_TITLE, N21_TEXT);
    }
    g_gfx.forced = 1;
    n = 0;
    unclean_write(0);
  }
  dunclean_write(n);
  g_dsess.have = g_dsess.stale = 1;
  dsess_job(OP_UNMARK, MK_DSESS_STALE);
}
static void dsess_eval(void) {
  if (!g_gfx.sess_checked || g_dsess.job || g_shut_marks) return;
  if (g_dsess.stale) { dsess_job(OP_UNMARK, MK_DSESS_STALE); return; }
  int bound = g_inv.nouveau_bound;
  if (bound && !g_dsess.have && !g_shutdown && !load_window_open() && !rb_active() && !path_exists("/data/vendor/gb/nouveau.loading"))
    dsess_job(OP_MARK, MK_DSESS);
  else if (!bound && g_dsess.have)
    dsess_job(OP_UNMARK, MK_UNDSESS);
}
/* (R-15) persist.* written at the first data tick (N19, N21) must not race init's load of the persistent properties
 * (a value set before it is neither stored nor kept): wait for ro.persistent_properties.ready (or boot_completed) */
static int persist_ready(void) { return prop_is("ro.persistent_properties.ready", "true") || boot_completed(); }
/* first tick at which /data/vendor/gb exists: a gfx_session of an earlier boot = that boot ended while apps could use
 * NVK (crash, panic, power loss); two in a row force Intel (N19). One of this boot (gb_power restarted) is adopted. */
static void sess_start_check(void) {
  char p[PATH_MAX], m[160], bid[48], mb[48] = "";
  if (read_text(rpath(p, sizeof p, SESS_PATH), m, sizeof m) < 0) return;
  boot_id(bid, sizeof bid);
  const char *b = strstr(m, "boot=");
  if (b) sscanf(b + 5, "%47s", mb);
  if (mb[0] && !strcmp(mb, bid)) { g_sess.have = 1; LOGI("gfx_session of this boot adopted (gb_power restarted)"); return; }
  int n = unclean_read() + 1;
  LOGW("gfx_session of an earlier boot (%s): it ended while apps could use NVIDIA graphics (%d unclean in a row)", mb[0] ? mb : "?", n);
  if (n >= UNCLEAN_MAX) {
    LOGE("app graphics: %d unclean NVIDIA sessions in a row: graphics mode set to Intel (N19)", n);
    prop_set("persist.vendor.gb.gfx_mode", "intel");
    n = 0;
    g_gfx.forced = 1;
    notify("gb_gfx", N19_TITLE, N19_TEXT);
  }
  unclean_write(n);
  g_sess.have = g_sess.stale = 1;
  sess_job(OP_UNMARK, MK_SESS_STALE);
}

/* ---- rebind guard (23.8): the kernel probes a re-added dGPU itself while nouveau.ko is loaded (drivers_autoprobe
 * cannot stop it for PCI), so the probe is marked as a load window: ESP lock (if free), crash marker through the
 * worker, a 30 s health watch, and keep/noload + fault rebind + Intel on failure. */
#define LOADING_MARKER "/data/vendor/gb/nouveau.loading"
static void fn_rs_ps(const char *bdf, char *rs, size_t rn, char *ps, size_t pn) {
  char p[PATH_MAX];
  if (read_text(rpath(p, sizeof p, "/sys/bus/pci/devices/%s/power/runtime_status", bdf), rs, rn) < 0) rs[0] = 0;
  if (read_text(rpath(p, sizeof p, "/sys/bus/pci/devices/%s/power_state", bdf), ps, pn) < 0) ps[0] = 0;
}
static int nouveau_rpm_error(void) {
  for (int i = 0; i < g_inv.n; i++) {
    char rs[24], ps[16];
    if (strcmp(g_inv.f[i].drv, "nouveau")) continue;
    fn_rs_ps(g_inv.f[i].bdf, rs, sizeof rs, ps, sizeof ps);
    if (!strcmp(rs, "error")) return 1;
  }
  return 0;
}
static void rb_arm(const char *why, struct job *ride) {
  if (!nouveau_loaded() || rb_active() || g_wedged || g_inv.n > 0) return;   /* g_inv: as the caller saw it */
  memset(&g_rb, 0, sizeof g_rb);
  snprintf(g_rb.why, sizeof g_rb.why, "%s", why);
  g_rb.locked = esp_lock_try(g_rb.lock_line, sizeof g_rb.lock_line) == 1;
  if (!g_rb.locked) g_rb.lock_line[0] = 0;
  char p[PATH_MAX], cur[200], bid[48], mb[48] = "", v[128];
  boot_id(bid, sizeof bid);
  rpath(p, sizeof p, LOADING_MARKER);
  if (read_text(p, cur, sizeof cur) >= 0) {
    const char *b = strstr(cur, "boot=");
    if (b) sscanf(b + 5, "%47s", mb);
  }
  if (mb[0] && !strcmp(mb, bid)) {
    LOGI("rebind guard: a load window of this boot already has the crash marker: left alone");
  } else if (!data_dir_ok()) {
    LOGW("rebind guard: /data/vendor/gb missing: no crash marker");
  } else {
    snprintf(v, sizeof v, "mode=rebind t=%lld boot=%s argv=-", uptime_s(), bid);
    snprintf(g_rb.mark_line, sizeof g_rb.mark_line, "%s", v);
    g_rb.own_marker = 1;
    if (ride) {
      job_add(ride, OP_MARK, p, v, "");           /* the first op of J_RESCAN */
    } else {
      struct job j;
      memset(&j, 0, sizeof j);
      j.kind = J_MARK;
      j.tag = MK_RB;
      j.deadline_ms = MARK_DEADLINE_MS;
      job_add(&j, OP_MARK, p, v, "");
      if (!worker_submit(&j)) g_rb.mark_job = 1;
      else LOGW("rebind guard: worker unavailable: no crash marker");
    }
  }
  g_rb.phase = RB_WAIT;
  g_rb.armed_at = now_ms();
  LOGI("rebind guard armed (%s): the kernel re-probes nouveau on the re-added dGPU; detach refused (N15) during the "
       "watch%s", why, g_rb.locked ? "; ESP lock held" : "");
}
static void rb_end(const char *why, int clean) {
  if (!rb_active()) return;
  if (g_rb.own_marker) {
    char p[PATH_MAX];
    struct job j;
    memset(&j, 0, sizeof j);
    j.kind = J_MARK;
    j.tag = MK_UNRB;
    j.deadline_ms = MARK_DEADLINE_MS;
    job_add(&j, OP_UNMARK, rpath(p, sizeof p, LOADING_MARKER), g_rb.mark_line, "");   /* (R-12) only our line */
    if (worker_submit(&j)) LOGW("rebind guard: worker unavailable: crash marker left");
  }
  if (g_rb.locked) esp_unlock(g_rb.lock_line);
  LOGI("rebind guard ended: %s", why);
  memset(&g_rb, 0, sizeof g_rb);
  if (clean) { g_st.clean_seen = 1; g_st.result_stale = 0; }
  g_tick_due = now_ms() + 50;
}
static void rb_fail(const char *why) {
  LOGE("rebind guard: the re-added dGPU failed its health check (%s): driver left loaded, graphics set to Intel only", why);
  rb_end(why, 0);
  flag_make("keep");                              /* as unhealthy() in gb_nouveau.sh (review A5) */
  flag_make("noload");
  g_gfx.forced = 1;
  set_fault("rebind");                            /* does not block a detach */
  prop_set("persist.vendor.gb.gfx_mode", "intel");
  char t[220];
  snprintf(t, sizeof t, N02_FMT, "rebind");
  notify("gb_dgpu", N02_TITLE, t);
  gfx_force_intel("crashloop");
}
static void rb_check(long long t) {
  if (!rb_active()) return;
  if (!gfx_attached()) { rb_end("base detached again", 0); return; }
  int present = g_inv.vga >= 0;
  if (g_rb.phase == RB_WAIT) {
    if (!present) { if (t - g_rb.armed_at >= REBIND_WAIT_MS) rb_end("no dGPU within 60 s", 0); return; }
    g_rb.phase = RB_SEEN;
    g_rb.seen_at = t;
    LOGI("rebind guard: dGPU %s appeared: watching the kernel's nouveau probe (%d s)", g_inv.f[g_inv.vga].bdf, REBIND_HEALTH_MS / 1000);
  }
  if (!present) { rb_end("dGPU gone", 0); return; }
  if (svc_running("gb_nouveau_off")) g_rb.off_seen = 1;   /* (R-2) an Intel switch's off ran during the watch */
  if (nouveau_rpm_error()) { rb_fail("runtime_status=error"); return; }
  char rs[24], ps[16];
  fn_rs_ps(g_inv.f[g_inv.vga].bdf, rs, sizeof rs, ps, sizeof ps);
  int bound = g_inv.nouveau_bound;
  if (g_rb.phase == RB_SEEN) {
    if (bound && !strcmp(rs, "suspended") && !strcmp(ps, "D3cold")) {
      g_rb.phase = RB_CLEAN;
      if (g_rb.locked) { esp_unlock(g_rb.lock_line); g_rb.locked = 0; g_rb.lock_line[0] = 0; }
      LOGI("rebind guard: nouveau bound, runtime-suspended and in D3cold after %lld ms: healthy (clean mark at +%d s)",
           t - g_rb.seen_at, REBIND_CLEAN_MS / 1000);
      nodes_enforce();
      return;
    }
    if (t - g_rb.seen_at >= REBIND_HEALTH_MS) {
      /* (R-2) not bound because the user switched to Intel and gb_nouveau_off unbound it: not a failed probe */
      if (!bound && (g_gfx.mode == GM_INTEL || g_rb.off_seen)) { rb_end("unbound by off (Intel mode)", 0); return; }
      rb_fail(bound ? "no D3cold within 30 s" : "not bound within 30 s");
    }
    return;
  }
  if (dec_running()) { rb_end("a detach started", 0); return; }   /* RB_CLEAN */
  if (t - g_rb.seen_at < REBIND_CLEAN_MS) return;
  if (!bound) {
    if (g_gfx.mode == GM_INTEL || g_rb.off_seen) { rb_end("unbound by off (Intel mode)", 0); return; }
    rb_fail("not bound at the clean mark");
    return;
  }
  if (!strcmp(ps, "D3cold") || g_refcnt > 0 || hold_wanted() || g_cmp.applied) { rb_end("clean mark of this binding", 1); return; }
  if (t - g_rb.seen_at >= REBIND_CLEAN_MS + REBIND_CLEAN_GRACE_MS) rb_fail("no D3cold at the clean mark");
}

/* ---- pending detach (close-holders-and-detach, 23.9): LATCH_REQUEST once the refcnt gate reads 0 twice 500 ms apart */
static void pend_clear(const char *why) {
  if (!g_pend.kind) return;
  LOGI("detach: pending detach cleared (%s)", why);
  memset(&g_pend, 0, sizeof g_pend);
  g_bcast_pending = 1;
}
static void pending_detach_step(long long t) {
  if (!g_pend.kind || t < g_pend.next_read) return;
  if (dec_running()) { pend_clear("a decision runs"); return; }
  if (!base_attached()) { pend_clear("base not attached"); return; }
  if (refcnt_busy()) g_pend.ok_reads = 0;
  else g_pend.ok_reads++;
  if (g_pend.ok_reads >= 2) {
    int r = dtx_cmd(SDTX_IOCTL_LATCH_REQUEST, "LATCH_REQUEST");
    pend_clear(r ? "LATCH_REQUEST failed" : "refcnt 0: LATCH_REQUEST sent, the EC raises a detach request");
    if (r) { g_gfx.detach_ref++; g_bcast_pending = 1; }
    return;
  }
  if (t >= g_pend.deadline) {
    LOGW("detach: apps still hold nouveau 15 s after close-holders-and-detach");
    pend_clear("deadline");
    g_gfx.detach_ref++;
    g_bcast_pending = 1;
    return;
  }
  g_pend.next_read = t + PEND_READ_MS;
}

/* ---- reconcile_off (23.6; replaces off_rebind_check): in Intel mode a bound, idle nouveau goes to gb_nouveau_off
 * (whose own refcnt gate decides); gb_power itself never writes unbind outside the detach worker */
static void reconcile_off(long long t) {
  if (g_gfx.mode != GM_INTEL || !g_inv.nouveau_bound || refcnt_busy()) { g_off.zero_ticks = 0; return; }
  if (++g_off.zero_ticks < 2) return;
  if (flag_exists("keep") || g_cmd_once || g_cmd_test || dec_running() || rb_active() || wedged()) return;
  if (svc_running("gb_nouveau_off") || svc_running("gb_nouveau")) return;
  if (g_off.last_start > 0 && t - g_off.last_start < OFF_GAP_MS) return;
  g_off.last_start = t;
  LOGI("dGPU: Intel mode with nouveau bound and idle: starting gb_nouveau_off");
  prop_set("ctl.start", "gb_nouveau_off");
}

/* ---- hot attach (23.7; replaces 7.9): a driverless dGPU appeared (or a start was deferred) and the mode wants a
 * driver: start gb_nouveau (insmod, or its bind path with the module loaded). Once per appearance, 3 per boot. */
static void hotattach_check(void) {
  int present = g_inv.vga >= 0;
  if (!g_nv.inited) { g_nv.inited = 1; g_nv.present_prev = present; return; }   /* present at start: the rc's job */
  if (present && !g_nv.present_prev) g_nv.appear = 1;
  g_nv.present_prev = present;
  if (!present) { g_nv.appear = 0; return; }
  if (g_inv.nouveau_bound) {                      /* (R-3) a deferred start survives an off that is still unbinding */
    g_nv.appear = 0;
    if (!svc_running("gb_nouveau_off")) g_nv.deferred = 0;
    return;
  }
  if (!g_nv.appear && !g_nv.deferred) return;
  const struct dfn *f = &g_inv.f[g_inv.vga];
  char st[24];
  int bn;
  derive_state(st, sizeof st, &bn);
  int wants = g_gfx.mode != GM_INTEL || g_cmd_once || g_cmd_test;
  if (!wants) { g_nv.appear = g_nv.deferred = 0; return; }
  if (f->drv[0] || !f->bar_ok || !gfx_attached() || g_cmd_kill || gfx_recovered(st) || wedged() || g_fault[0] ||
      !prop_is("vendor.gb.nouveau.booted", "1") || rb_pending())
    return;
  if (svc_running("gb_nouveau") || svc_running("gb_nouveau_off")) {   /* deferred, not counted, issued when stopped */
    if (!g_nv.deferred) LOGI("hot attach: %s still running: start deferred", svc_running("gb_nouveau") ? "gb_nouveau" : "gb_nouveau_off");
    g_nv.deferred = 1;
    g_nv.appear = 0;
    return;
  }
  if (g_nv.starts >= HOTATTACH_MAX) {
    if (!g_nv.cap_logged) LOGW("hot attach: %d starts this boot already: not starting gb_nouveau again", HOTATTACH_MAX);
    g_nv.cap_logged = 1;
    g_nv.appear = g_nv.deferred = 0;
    return;
  }
  g_nv.starts++;
  g_nv.appear = g_nv.deferred = 0;
  g_nv.started_self = 1;
  g_nv.last_start = now_ms();
  LOGI("hot attach: dGPU %s driverless with mode %s: starting gb_nouveau (%d/%d this boot)", f->bdf, g_gm_names[g_gfx.mode],
       g_nv.starts, HOTATTACH_MAX);
  prop_set("ctl.start", "gb_nouveau");
}

/* ---- reconcile_on (R-3, 23.7a; the mirror of 23.6): the mode wants a driver, the dGPU is there, driverless, with its
 * BAR, and nothing else will start gb_nouveau (an off that raced a mode change back, a boot-time refusal such as
 * refused:esp): start it again 10 s after gb_nouveau/gb_nouveau_off last ran and after gb_power's own last start,
 * sharing the 3 starts per boot of the hot attach. The 7.2-v12 / 7.2a guards run again inside the script. The 10 s
 * also count from gb_power's first tick (a restarted gb_power does not race the rc's own start). */
static void reconcile_on(long long t) {
  if (!g_nv.busy_at || svc_running("gb_nouveau") || svc_running("gb_nouveau_off")) { g_nv.busy_at = t; return; }
  if (g_inv.vga < 0 || g_inv.nouveau_bound || !g_nv.inited) return;
  const struct dfn *f = &g_inv.f[g_inv.vga];
  int wants = g_gfx.mode != GM_INTEL || g_cmd_once || g_cmd_test;
  if (!wants || f->drv[0] || !f->bar_ok || !gfx_attached() || g_cmd_kill || wedged() || g_fault[0] || rb_pending() ||
      dec_running() || g_pend.kind || g_dtx.ec != EC_READY || !prop_is("vendor.gb.nouveau.booted", "1"))
    return;
  char st[24], r[PROP_VALUE_MAX];
  int bn;
  derive_state(st, sizeof st, &bn);
  if (gfx_recovered(st)) return;
  prop_result(r);                                 /* a refusal that a retry cannot change (fail-safe if spoofed) */
  if (!strncmp(r, "insmod-failed", 13) || !strcmp(r, "refused:i915") || !strcmp(r, "refused:hw") || !strcmp(r, "refused:module"))
    return;
  if (t - g_nv.busy_at < RECONCILE_ON_GAP_MS || (g_nv.last_start > 0 && t - g_nv.last_start < RECONCILE_ON_GAP_MS)) return;
  if (g_nv.starts >= HOTATTACH_MAX) {
    if (!g_nv.on_logged) LOGW("reconcile_on: %d gb_nouveau starts this boot already: not starting it again", HOTATTACH_MAX);
    g_nv.on_logged = 1;
    return;
  }
  g_nv.starts++;
  g_nv.started_self = 1;
  g_nv.last_start = t;
  LOGI("reconcile_on: mode %s wants a driver, dGPU %s driverless (result %s): starting gb_nouveau (%d/%d this boot)",
       g_gm_names[g_gfx.mode], f->bdf, r[0] ? r : "-", g_nv.starts, HOTATTACH_MAX);
  prop_set("ctl.start", "gb_nouveau");
}

/* ---- automatic self-test (25): scheduling, hold, record, interruptions, crashes, deadline */
/* the 25.2 conditions except the record ones: NULL when they hold, else why (*err = the socket error code) */
static const char *st_blocked(const char *st, const char **err) {
  if (g_st.marker_check) { *err = "EAGAIN"; return "self-test finishing"; }   /* the 10 s marker check first */
  *err = "ENODEV";
  if (!g_gfx.cap) return "no NVIDIA app graphics on this image";
  if (!dtx_ready() || !gfx_attached()) return "keyboard base not attached";
  if (!g_inv.nouveau_bound) return "nouveau not bound";
  *err = "EBUSY";
  if (g_st.running || svc_running("gb_gfx_selftest")) return "self-test running";
  if (g_cmd_kill) return "NVIDIA disabled for this boot";
  if (g_cmd_test) return "NVIDIA test boot";
  if (gfx_recovered(st)) return "NVIDIA switched off after a problem";
  if (wedged()) return "restart required";
  if (g_fault[0]) return "NVIDIA GPU fault";
  if (g_gfx.mode == GM_INTEL) return "Intel only mode";
  if (dec_running() || !strcmp(st, "detaching")) return "detaching";
  if (g_pend.kind) return "a detach is pending";
  *err = "EAGAIN";
  if (rb_pending() || load_window_open() || !gfx_bound(st) || !g_st.clean_seen) return "NVIDIA driver starting";
  return NULL;
}
static void st_start(long long t, const char *why) {
  g_st.snap_t = g_st.rec.valid ? g_st.rec.t : -1;
  snprintf(g_st.snap_verdict, sizeof g_st.snap_verdict, "%s", g_st.rec.verdict);
  snprintf(g_st.tried_id, sizeof g_st.tried_id, "%s|%s|%s", g_gfx.build, g_gfx.krel, g_gfx.gfx);
  g_st.running = 1;
  g_st.seen_running = g_st.stop_sent = g_st.marker_check = 0;
  g_st.started_at = t;
  g_st.want = 1;                                  /* the hold: power/control=on through the worker */
  snprintf(g_gfx.test, sizeof g_gfx.test, "running");
  prop_put("vendor.gb.gfx_test", "running");
  LOGI("self-test: starting gb_gfx_selftest (%s), dGPU held awake", why);
  prop_set("ctl.start", "gb_gfx_selftest");
  hold_eval();
  g_bcast_pending = 1;
}
static void st_stopped(long long t) {
  g_st.running = 0;
  g_st.want = 0;
  g_st.stopped_at = t;
  g_st.marker_check = 1;
  hold_eval();
  rec_validate("service stopped");
  int newv = g_st.rec.valid && (g_st.rec.t != g_st.snap_t || strcmp(g_st.rec.verdict, g_st.snap_verdict));
  int ok = gfx_ok_now();
  LOGI("self-test: gb_gfx_selftest stopped (%s)", newv ? g_st.rec.verdict : "no new verdict");
  if (newv && ok == 0) n18(g_st.rec.reason);
  if (newv && ok == 1 && !g_gfx.active) g_gfx.n16 = 1;   /* the first nvidia after a pass posts N16 */
}
/* first tick at which selftest/ exists: validate; a marker of an earlier boot = the boot ended inside the test */
static void st_start_check(long long t) {
  char p[PATH_MAX], m[160], bid[48], mb[48] = "";
  struct stat st;
  if (g_st.dir_seen) return;
  if (lstat(rpath(p, sizeof p, "/data/vendor/gb/selftest"), &st) || !S_ISDIR(st.st_mode)) return;
  g_st.dir_seen = 1;
  rec_validate("start");
  if (read_text(rpath(p, sizeof p, REC_MARKER), m, sizeof m) < 0) return;
  if (svc_running("gb_gfx_selftest")) {          /* gb_power restarted while the test runs: adopt it */
    LOGI("self-test: gb_gfx_selftest is running: adopted");
    g_st.running = g_st.seen_running = g_st.want = 1;
    g_st.started_at = t;
    g_st.snap_t = g_st.rec.valid ? g_st.rec.t : -1;
    snprintf(g_st.snap_verdict, sizeof g_st.snap_verdict, "%s", g_st.rec.verdict);
    hold_eval();
    return;
  }
  boot_id(bid, sizeof bid);
  const char *b = strstr(m, "boot=");
  if (b) sscanf(b + 5, "%47s", mb);
  int same = rec_same_id(), att = same ? g_st.rec.attempts : 0, cr = same ? g_st.rec.crashes : 0;
  if (mb[0] && !strcmp(mb, bid)) {
    LOGW("self-test: marker of this boot without the service (gb_power restarted): interrupted");
    rec_write("error", "interrupted", att, cr);
  } else {
    cr++;
    LOGE("self-test: marker of an earlier boot: that boot ended inside the self-test (crash %d)", cr);
    rec_write(cr >= 2 ? "fail" : "error", "crash", att, cr);
    if (cr >= 2) n18("crash");
  }
  st_marker_remove();
}
/* clean_seen: this binding reached its clean mark (result=clean seen after the bind, or the rebind guard's +120 s
 * check); a "clean" left over from before a kernel re-bind does not count */
static void st_clean_track(void) {
  char r[PROP_VALUE_MAX];
  prop_result(r);
  int clean = !strcmp(r, "clean");
  if (!g_inv.nouveau_bound) { g_st.clean_seen = g_st.bound_prev = g_st.result_stale = 0; g_st.inited = 1; return; }
  if (!g_st.bound_prev) { g_st.bound_prev = 1; g_st.result_stale = g_st.inited && clean; }
  g_st.inited = 1;
  if (g_st.result_stale && !clean) g_st.result_stale = 0;
  if (!g_st.result_stale && clean && !g_st.clean_seen) { g_st.clean_seen = 1; LOGI("self-test: this binding reached its clean mark"); }
}
static void selftest_check(long long t) {
  if (!g_st.dir_seen) return;
  int run = svc_running("gb_gfx_selftest");
  if (g_st.running) {
    if (run) g_st.seen_running = 1;
    int stopped = !run && (g_st.seen_running || t - g_st.started_at >= SELFTEST_START_MS);
    if (!stopped) {
      if (!g_st.stop_sent && t - g_st.started_at >= SELFTEST_DEADLINE_MS) {
        LOGW("self-test: still running %d s after the start: ctl.stop", SELFTEST_DEADLINE_MS / 1000);
        prop_set("ctl.stop", "gb_gfx_selftest");
        g_st.stop_sent = 1;
        g_st.stop_sent_at = t;
      } else if (g_st.stop_sent && run && t - g_st.stop_sent_at >= SELFTEST_GRACE_MS) {
        /* a probe stuck in a kernel ioctl (25.6): fault (does not block detach; the refcnt gate does), N20 */
        LOGE("self-test: gb_gfx_selftest did not stop within %d s: hang", SELFTEST_GRACE_MS / 1000);
        g_st.running = g_st.want = 0;
        hold_eval();
        set_fault("selftest-hang");
        int same = rec_same_id();
        rec_write("fail", "hang", same ? g_st.rec.attempts : 0, same ? g_st.rec.crashes : 0);
      }
      return;
    }
    st_stopped(t);
  }
  if (g_st.marker_check && t - g_st.stopped_at >= SELFTEST_GRACE_MS) {   /* killed hard: no trap ran (25.5) */
    char p[PATH_MAX];
    g_st.marker_check = 0;
    if (!run && !access(rpath(p, sizeof p, REC_MARKER), F_OK)) {
      int same = rec_same_id();
      LOGW("self-test: the service stopped but its marker is still there after %d s: interrupted", SELFTEST_GRACE_MS / 1000);
      rec_write("error", "interrupted", same ? g_st.rec.attempts : 0, same ? g_st.rec.crashes : 0);
      st_marker_remove();
    }
  }
  if (run) return;
  char st[24], id[720];
  int bn;
  const char *err;
  derive_state(st, sizeof st, &bn);
  if (st_blocked(st, &err)) return;
  snprintf(id, sizeof id, "%s|%s|%s", g_gfx.build, g_gfx.krel, g_gfx.gfx);
  int ok = gfx_ok_now();
  if (ok == -1 && strcmp(id, g_st.tried_id)) { st_start(t, "no valid record for this identity"); return; }
  if (ok == -2 && g_st.rec.attempts < 3 && !g_st.retried) { g_st.retried = 1; st_start(t, "retry after an error"); return; }
}
static void st_mirror(void) {                    /* vendor.gb.gfx_test, persist.vendor.gb.gfx_ok (25.4) */
  int ok = gfx_ok_now();
  const char *v;
  if (g_st.running || svc_running("gb_gfx_selftest")) v = "running";
  else if (ok != -1) v = g_st.rec.verdict;
  else v = g_gfx.cap && g_gfx.mode != GM_INTEL && gfx_attached() ? "pending" : "none";
  snprintf(g_gfx.test, sizeof g_gfx.test, "%s", v);
  prop_put("vendor.gb.gfx_test", v);
  prop_put("persist.vendor.gb.gfx_ok", ok == 1 ? "1" : ok == 0 ? "0" : "");
}

/* ---- Settings push (23.4, 24.6): never without the package or DTX (VMware: the keys stay null, D8) */
static void gfx_settings_eval(void) {
  if (!g_gfx.cap || !dtx_ready() || !boot_completed()) return;
  char st[24];
  int bn;
  derive_state(st, sizeof st, &bn);
  int want = !g_cmd_kill && !g_cmd_test && !gfx_recovered(st) && g_gfx.mode != GM_INTEL && gfx_ok_now() == 1 ? 0 : 3;
  if (!g_gfx.settings_boot) {
    if (g_apps.in_have != 1 || g_apps.out_have != 1) return;   /* gb_notify reads the files: written first */
    g_gfx.settings_boot = 1;
    g_gfx.last_all_apps = want;
    g_apps.in_dirty = g_apps.out_dirty = 0;
    LOGI("settings: boot push (all_apps %d, %d opt-in, %d opt-out)", want, g_apps.n_in, g_apps.n_out);
    settings_all_apps(want);
    settings_list(0);
    settings_list(1);
    return;
  }
  if (want != g_gfx.last_all_apps) { g_gfx.last_all_apps = want; settings_all_apps(want); }
  if (g_apps.in_dirty) { g_apps.in_dirty = 0; settings_list(0); }
  if (g_apps.out_dirty) { g_apps.out_dirty = 0; settings_list(1); }
}

/* ---- the graphics part of every status tick (end of status_tick, before publish_state) and of the commands */
static void gfx_tick(long long t) {
  gfx_inputs();
  if (!g_gfx.data_seen && data_dir_ok()) {
    g_gfx.data_seen = 1;
    prefs_load();
    catalog_load();
    g_apps.dirty = 1;
  }
  if (g_gfx.data_seen && !g_gfx.sess_checked && persist_ready()) {   /* (R-15) they may write persist.* */
    g_gfx.sess_checked = 1;
    sess_start_check();
    dsess_start_check();                          /* (R-4) after the gfx_session check (N19 wins over N21) */
  }
  st_start_check(t);
  if (g_st.dir_seen) rec_check_changed();
  st_clean_track();
  if (g_pkgs.gen != g_apps.pkgs_gen) { g_apps.pkgs_gen = g_pkgs.gen; g_apps.dirty = 1; }
  gfx_apps_eval();
  selftest_check(t);
  gfx_eval();
  dsess_eval();                                   /* (R-4) */
  nodes_enforce();
  st_mirror();
  gfx_settings_eval();
}
static void gfx_refresh(void) {
  gfx_inputs();
  gfx_apps_eval();
  gfx_eval();
  nodes_enforce();
  st_mirror();
  gfx_settings_eval();
  publish_state(now_ms());
}
/* unit tests: back to a fresh daemon (every v1.2 state) */
static void gfx_state_reset(void) {
  memset(&g_gfx, 0, sizeof g_gfx);
  g_gfx.policy = g_gfx.last_all_apps = -1;
  snprintf(g_gfx.reason, sizeof g_gfx.reason, "nopkg");
  snprintf(g_gfx.test, sizeof g_gfx.test, "none");
  memset(&g_sess, 0, sizeof g_sess);
  memset(&g_dsess, 0, sizeof g_dsess);
  g_shut_marks = 0;
  memset(&g_st, 0, sizeof g_st);
  g_st.snap_t = -1;
  memset(&g_rb, 0, sizeof g_rb);
  memset(&g_pend, 0, sizeof g_pend);
  memset(&g_nv, 0, sizeof g_nv);
  memset(&g_off, 0, sizeof g_off);
  memset(&g_prefs, 0, sizeof g_prefs);
  memset(&g_cat, 0, sizeof g_cat);
  memset(&g_apps, 0, sizeof g_apps);
  g_apps.dirty = 1;
  g_bcast_sig[0] = 0;
}

/* ------------------------------------------------------------------------------------------------ status tick */
static char g_sig_last[512];
static long long g_tick_last = -100000;
static void status_tick(long long t) {
  g_tick_last = t;
  inventory();
  g_refcnt = nouveau_refcnt();
  if (g_inv.nouveau_bound) mark_used("nouveau bound");
  else if (g_refcnt >= 0) mark_used("nouveau loaded");
  else used();
  if (strcmp(g_inv.sig, g_sig_last)) {           /* bind/unbind/add/remove seen */
    LOGI("dGPU functions: %s", g_inv.sig[0] ? g_inv.sig : "none");
    snprintf(g_sig_last, sizeof g_sig_last, "%s", g_inv.sig);
    if (g_dgpu_on) dgpu_enforce("change", 0);
  }
  gfx_inputs();                                   /* (v1.2) mode, policy, cap, identity for the checks below */
  {
    char what[200];
    int disk = g_dtx.fd >= 0 && bootdisk_guard(what, sizeof what);
    if (disk != g_bootdisk) LOGI("%s", disk ? "a drive in the keyboard base is mounted: latch locked" : "no drive in the keyboard base mounted");
    g_bootdisk = disk;
  }
  if (g_refcnt > 0 && g_inv.nouveau_bound) {
    if (!g_hold.valid || g_refcnt != g_hold.refcnt_at || t - g_hold.t >= HOLDER_RESCAN_MS) holders_scan();
  } else {
    holders_clear();
  }
  watchdog(t);
  rb_check(t);                                    /* (v1.2) after the watchdog: rpm-error latches first */
  reconcile_off(t);
  resume_check();
  powerctl_check();
  latch_eval("tick");
  hotattach_check();
  reconcile_on(t);                                /* (R-3) after the hot attach (which counts against the same 3) */
  rescan_check(t);
  hold_eval();
  iommu_check();
  compute_publish();
  pkgs_refresh(0);                                /* (stat only, <= every 5 s) a changed packages.list re-validates compute_allow */
  gfx_tick(t);                                    /* (v1.2) derivation, transitions, nodes, self-test, Settings */
  publish_state(t);
  nq_flush();
  while (waitpid(-1, NULL, WNOHANG) > 0) {}      /* reap finished workers (a stuck one stays) */
  int fast = g_inv.nouveau_bound || dec_running() || g_wk.busy || g_rs.active || rb_active() || g_st.running || g_pend.kind;
  g_tick_due = t + (fast ? TICK_FAST_MS : TICK_SLOW_MS);
}

/* ------------------------------------------------------------------------------------------------ E. control socket */
struct jb { char *b; size_t cap, len; int over; };
static void jb_raw(struct jb *j, const char *s) {
  size_t l = strlen(s);
  if (j->over || j->len + l + 1 > j->cap) { j->over = 1; return; }
  memcpy(j->b + j->len, s, l + 1);
  j->len += l;
}
static void jb_fmt(struct jb *j, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void jb_fmt(struct jb *j, const char *fmt, ...) {
  char t[512];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(t, sizeof t, fmt, ap);
  va_end(ap);
  jb_raw(j, t);
}
static void jb_str(struct jb *j, const char *s) {   /* JSON string; control and non-ASCII bytes -> '?' */
  char t[600];
  size_t k = 0;
  t[k++] = '"';
  for (; *s && k < sizeof t - 4; s++) {
    unsigned char c = (unsigned char)*s;
    if (c == '"' || c == '\\') { t[k++] = '\\'; t[k++] = (char)c; }
    else t[k++] = c >= 0x20 && c < 0x7f ? (char)c : '?';
  }
  t[k++] = '"';
  t[k] = 0;
  jb_raw(j, t);
}
static void jb_kstr(struct jb *j, const char *k, const char *v) { jb_fmt(j, ",\"%s\":", k); jb_str(j, v); }
static void jb_kint(struct jb *j, const char *k, long long v) { jb_fmt(j, ",\"%s\":%lld", k, v); }

static void holders_json(struct jb *j, size_t reserve) {
  jb_raw(j, "[");
  for (int i = 0; i < g_hold.n; i++) {
    const struct holder *h = &g_hold.h[i];
    if (j->len + 360 + reserve > j->cap) break;   /* stay within the 4096-byte reply */
    jb_fmt(j, "%s{\"pid\":%d", i ? "," : "", h->pid);
    jb_kstr(j, "comm", h->comm);
    jb_kint(j, "uid", h->uid);
    jb_kint(j, "user", h->uid >= 0 ? h->uid / 100000 : -1);
    jb_kstr(j, "pkg", h->pkg);
    jb_fmt(j, ",\"closable\":%s}", h->closable ? "true" : "false");
  }
  jb_raw(j, "]");
}

static const char *payload_value(char *v) {
  prop_get("vendor.gb.dgpu.payload", v);
  if (!strcmp(v, "legacy") || !strcmp(v, "absent") || !strcmp(v, "ready") || !strcmp(v, "mounted")) return v;
  if (!strncmp(v, "error:", 6)) {
    const char *s = v + 6;
    if (!strcmp(s, "lock") || !strcmp(s, "krel") || !strcmp(s, "esp-mount") || !strcmp(s, "sha") || !strcmp(s, "mount") ||
        !strcmp(s, "bind") || !strcmp(s, "version")) return v;
  }
  return "";
}

/* INTERFACES 9.5 */
static void status_json(char *buf, size_t cap, const char *cmd, int with_t) {
  struct jb j = {buf, cap, 0, 0};
  char st[24], mode[PROP_VALUE_MAX], result[PROP_VALUE_MAX], v[PROP_VALUE_MAX], dir[PATH_MAX], p[PATH_MAX + 64];
  char rs[24] = "", ps[16] = "";
  int n;
  buf[0] = 0;
  derive_state(st, sizeof st, &n);
  prop_mode(mode);
  prop_result(result);
  jb_fmt(&j, "{\"v\":1,\"ok\":true,\"cmd\":\"%s\"", cmd);
  jb_kstr(&j, "state", st);
  jb_kint(&j, "n", n);
  jb_raw(&j, ",\"holders\":");
  if (g_refcnt > 0 && g_inv.nouveau_bound) holders_json(&j, 1200);   /* v1.2: 1200 (the gfx fields below) */
  else jb_raw(&j, "[]");
  jb_kstr(&j, "mode", mode);
  jb_kstr(&j, "result", result);
  jb_kint(&j, "persist", g_gfx.mode != GM_INTEL);  /* v1.2: the effective gfx_mode is not intel */
  prop_get("vendor.gb.nouveau.clean", v);
  int clean = is_num(v) && strlen(v) < 7 ? atoi(v) : 0;
  jb_kint(&j, "clean_boots", clean);
  jb_kint(&j, "probation", !strcmp(mode, "auto") && clean < 3);
  jb_kint(&j, "used", used());
  jb_kint(&j, "foreign", foreign());
  jb_kint(&j, "wedged", g_wedged);
  jb_kstr(&j, "fault_reason", g_fault);
  const struct dfn *f = g_inv.vga >= 0 ? &g_inv.f[g_inv.vga] : NULL;
  if (f) {
    rpath(dir, sizeof dir, "/sys/bus/pci/devices/%s", f->bdf);
    snprintf(p, sizeof p, "%s/power/runtime_status", dir);
    if (read_text(p, rs, sizeof rs) < 0) rs[0] = 0;
    snprintf(p, sizeof p, "%s/power_state", dir);
    if (read_text(p, ps, sizeof ps) < 0) ps[0] = 0;
  }
  jb_kstr(&j, "bdf", f ? f->bdf : "");
  jb_kstr(&j, "driver", f ? f->drv : "");
  jb_kstr(&j, "runtime_status", rs);
  jb_kstr(&j, "power_state", ps);
  jb_kint(&j, "refcnt", g_refcnt);
  jb_kstr(&j, "latch", g_latch < 0 ? "unknown" : g_latch ? "locked" : "unlocked");
  jb_kstr(&j, "base", dtx_ready() ? base_name(g_dtx.base) : "unknown");
  jb_kstr(&j, "dtx", !dtx_ready() ? "n/a" : g_dtx.ec == EC_READY ? "ready" : g_dtx.ec == EC_IN_PROGRESS ? "in-progress" : "confirmed");
  char cs[PROP_VALUE_MAX];
  prop_get("vendor.gb.compute.state", cs);
  jb_kstr(&j, "compute", cs[0] ? cs : "stopped");
  jb_kstr(&j, "payload", payload_value(v));
  jb_kint(&j, "gfxdriver", g_gfx.cap);              /* v1.2: = gfx_cap */
  int ok = gfx_ok_now();
  jb_kstr(&j, "gfx_mode", g_gm_names[g_gfx.mode]);
  jb_kstr(&j, "gfx_policy", g_gfx.policy == GPOL_ALL ? "all" : "games");
  jb_kstr(&j, "gfx_active", g_gfx.active ? "nvidia" : "intel");
  jb_kstr(&j, "gfx_reason", g_gfx.reason);
  jb_kint(&j, "gfx_ok", ok);
  jb_kstr(&j, "gfx_test", g_gfx.test);
  jb_kint(&j, "gfx_test_t", ok != -1 ? g_st.rec.t : 0);
  jb_kint(&j, "gfx_cap", g_gfx.cap);
  jb_kint(&j, "gfx_seq", g_gfx.seq);
  jb_kstr(&j, "pending", gfx_pending());
  jb_kint(&j, "detach_ref", g_gfx.detach_ref);
  jb_kint(&j, "apps_sync", apps_sync());
  if (with_t) jb_kint(&j, "t_ms", now_ms());
  jb_raw(&j, "}\n");
  if (j.over) snprintf(buf, cap, "{\"v\":1,\"ok\":false,\"cmd\":\"%s\",\"err\":\"EIO\",\"msg\":\"reply too large\"}\n", cmd);
}

enum { R_ROOT, R_SHELL, R_UI, R_CSVC, R_CCLIENT, R_OTHER };
static const char *const role_names[] = {"root", "shell", "ui", "compute-svc", "compute-client", "other"};
#define MAXCL 8
#define MAXWATCH 4
#define CL_OUT 8192
struct client {
  int fd, uid, pid, role, watch, compute, closing, dead;
  size_t inlen, outlen;
  long long last, ping_at;
  char in[256], out[CL_OUT], last_st[4096];
  int st_on, st_n, st_max;                        /* (v1.2) staged app catalog of apps-begin/apps-add (24.3) */
  struct catent *st_e;
};
static struct client g_cl[MAXCL];
static int g_ncl, g_listen = -1, g_ue = -1;
static void cl_stage_free(struct client *c) {
  free(c->st_e);
  c->st_e = NULL;
  c->st_on = c->st_n = c->st_max = 0;
}

static void child_close_fds(void) {             /* worker child: drop the daemon's descriptors */
  if (g_ue >= 0) close(g_ue);
  if (g_dtx.fd >= 0) close(g_dtx.fd);
  if (g_listen >= 0) close(g_listen);
  for (int i = 0; i < g_ncl; i++) close(g_cl[i].fd);
}

static int sys_peer(int fd, int *uid, int *pid) {
  struct ucred c;
  socklen_t l = sizeof c;
  if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &c, &l)) return -errno;
  *uid = (int)c.uid;
  *pid = (int)c.pid;
#ifndef __ANDROID__
  if (g_root[0]) {                                /* host test builds only (-r): the socket test chooses a role */
    const char *u = getenv("GB_POWER_TEST_UID");
    if (u && *u) *uid = atoi(u);
  }
#endif
  return 0;
}
static int (*peer_cred)(int fd, int *uid, int *pid) = sys_peer;

static int ui_appid(void) {
  pkgs_refresh(0);
  if (!path_exists("/odm/app/GbGpu/GbGpu.apk")) return -1;   /* no squatting on an image without GbGpu */
  int a = appid_by_pkg("gb.sb3.gpu");
  return a >= 10000 && a <= 19999 ? a : -1;
}
static int peer_role(int uid, int pid) {
  if (uid == 0) return R_ROOT;
  if (uid == 2000) return R_SHELL;
  if (uid < 0) return R_OTHER;
  int app = uid % 100000, ua = ui_appid();
  if (ua >= 0 && app == ua) return R_UI;
  if (uid == 9999 && is_compute_pid(pid)) return R_CSVC;
  allow_load();
  if (allow_has_appid(app)) return R_CCLIENT;
  return R_OTHER;
}

static void cl_flush(struct client *c) {
  while (c->outlen && !c->dead) {
    ssize_t w = send(c->fd, c->out, c->outlen, MSG_DONTWAIT | MSG_NOSIGNAL);
    if (w < 0) {
      if (errno == EINTR) continue;
      if (errno == EAGAIN || errno == EWOULDBLOCK) return;
      c->dead = 1;
      return;
    }
    memmove(c->out, c->out + w, c->outlen - (size_t)w);
    c->outlen -= (size_t)w;
  }
  if (c->closing && !c->outlen) c->dead = 1;
}
static void cl_send(struct client *c, const char *line) {
  if (c->dead) return;
  size_t l = strlen(line);
  if (c->outlen + l > CL_OUT) {                   /* never block on a client: drop one that does not read */
    LOGW("control: client uid %d pid %d does not read its replies: closed", c->uid, c->pid);
    c->dead = 1;
    return;
  }
  memcpy(c->out + c->outlen, line, l);
  c->outlen += l;
  cl_flush(c);
}
static void reply_ok(struct client *c, const char *cmd, const char *extra) {
  char b[4096];
  snprintf(b, sizeof b, "{\"v\":1,\"ok\":true,\"cmd\":\"%s\"%s}\n", cmd, extra ? extra : "");
  cl_send(c, b);
}
static void reply_err(struct client *c, const char *cmd, const char *code, const char *msg) {
  char b[512];
  struct jb j = {b, sizeof b, 0, 0};
  jb_raw(&j, "{\"v\":1,\"ok\":false,\"cmd\":");
  jb_str(&j, cmd);
  jb_fmt(&j, ",\"err\":\"%s\",\"msg\":", code);
  jb_str(&j, msg);
  jb_raw(&j, "}\n");
  cl_send(c, b);
}

static void status_fresh(void) {                 /* a request wants current values: tick at most every 250 ms */
  long long t = now_ms();
  if (t - g_tick_last >= 250) status_tick(t);
}

static void cmd_hello(struct client *c, const char *a) {
  char x[200];
  (void)a;
  snprintf(x, sizeof x, ",\"api\":1,\"role\":\"%s\",\"uid\":%d,\"server\":\"gb_power %s\"", role_names[c->role], c->uid,
           GB_POWER_BUILD_ID);
  reply_ok(c, "hello", x);
}
static void cmd_status(struct client *c, const char *a) {
  char b[4096];
  (void)a;
  status_fresh();
  status_json(b, sizeof b, "status", 1);
  cl_send(c, b);
}
static void cmd_watch(struct client *c, const char *a) {
  (void)a;
  int nw = 0;
  for (int i = 0; i < g_ncl; i++) nw += g_cl[i].watch && !g_cl[i].dead;
  if (nw >= MAXWATCH) { reply_err(c, "watch", "EBUSY", "watch limit"); return; }
  char b[4096];
  status_fresh();
  status_json(b, sizeof b, "status", 1);
  c->watch = 1;
  c->ping_at = now_ms() + WATCH_PING_MS;
  status_json(c->last_st, sizeof c->last_st, "status", 0);
  cl_send(c, b);
}
static void cmd_holders_as(struct client *c, const char *cmd) {
  if (!nouveau_loaded()) { reply_err(c, cmd, "ENODEV", "nouveau not loaded"); return; }
  holders_scan();
  char b[4096];
  struct jb j = {b, sizeof b, 0, 0};
  jb_fmt(&j, "{\"v\":1,\"ok\":true,\"cmd\":\"%s\",\"holders\":", cmd);
  holders_json(&j, 16);
  jb_raw(&j, "}\n");
  cl_send(c, b);
}
static void cmd_holders(struct client *c, const char *a) { (void)a; cmd_holders_as(c, "holders"); }
static void cmd_list_holders(struct client *c, const char *a) { (void)a; cmd_holders_as(c, "list-holders"); }   /* v1.2 alias */

/* ---- (v1.2) graphics mode (26.1, 26.2) */
static int gfx_mode_parse(const char *a) {
  return !a ? -1 : !strcmp(a, "auto") ? GM_AUTO : !strcmp(a, "intel") ? GM_INTEL : !strcmp(a, "nvidia") ? GM_NVIDIA : -1;
}
/* 26.2: next-boot / next-attach / pending (n = holders still on NVIDIA) / now */
static const char *set_mode_effect(int m, int *n) {
  *n = 0;
  if (!prop_is("vendor.gb.nouveau.booted", "1") || g_cmd_once || g_cmd_test || g_cmd_kill || wedged()) return "next-boot";
  if (m != GM_INTEL && (flag_exists("noload") || flag_exists("keep"))) return "next-boot";
  if (m == GM_INTEL && flag_exists("keep")) return "next-boot";   /* the sick driver stays loaded until restart (D20) */
  if (m != GM_INTEL && !gfx_attached()) return "next-attach";
  if (m == GM_INTEL && g_inv.nouveau_bound && refcnt_busy()) {
    holders_scan();
    *n = g_hold.total > 0 ? g_hold.total : 1;
    return "pending";
  }
  return "now";
}
static const char *set_mode_apply(struct client *c, const char *cmd, int m, int *n) {
  status_fresh();
  const char *eff = set_mode_effect(m, n);
  if (m == GM_INTEL) {
    gfx_force_intel("mode");                      /* before the property: its rc trigger then starts off */
    if (g_st.running || svc_running("gb_gfx_selftest")) { LOGI("self-test stopped: Intel only"); prop_set("ctl.stop", "gb_gfx_selftest"); }
  }
  prop_set("persist.vendor.gb.gfx_mode", g_gm_names[m]);
  /* the rc trigger's start is a no-op while gb_nouveau runs (23.7), and an off still unbinding races it (R-3) */
  if (m != GM_INTEL && (svc_running("gb_nouveau") || svc_running("gb_nouveau_off"))) {
    LOGI("set-mode: %s still running: a start is deferred until it stops",
         svc_running("gb_nouveau") ? "gb_nouveau" : "gb_nouveau_off");
    g_nv.deferred = 1;
  }
  LOGI("control: %s %s by %s uid %d (effect %s)", cmd, g_gm_names[m], role_names[c->role], c->uid, eff);
  gfx_refresh();
  return eff;
}
static void cmd_set_mode(struct client *c, const char *a) {
  int m = gfx_mode_parse(a), n;
  if (m < 0) { reply_err(c, "set-mode", "EINVAL", "bad mode"); return; }
  if (!stack_present()) { reply_err(c, "set-mode", "ENODEV", "no NVIDIA driver on this image"); return; }
  const char *eff = set_mode_apply(c, "set-mode", m, &n);
  char x[160];
  snprintf(x, sizeof x, ",\"mode\":\"%s\",\"active\":\"%s\",\"effect\":\"%s\",\"n\":%d", g_gm_names[g_gfx.mode],
           g_gfx.active ? "nvidia" : "intel", eff, n);
  reply_ok(c, "set-mode", x);
}
/* enable = set-mode auto, disable = set-mode intel; the v1.1 replies: next-attach and pending are reported as now */
static void cmd_enable(struct client *c, const char *a) {
  (void)a;
  int n;
  if (!stack_present()) { reply_err(c, "enable", "ENODEV", "no NVIDIA driver on this image"); return; }
  if (wedged()) { reply_err(c, "enable", "EBUSY", "restart required"); return; }
  const char *eff = set_mode_apply(c, "enable", GM_AUTO, &n);
  reply_ok(c, "enable", !strcmp(eff, "next-boot") ? ",\"effect\":\"next-boot\"" : ",\"effect\":\"now\"");
}
static void cmd_disable(struct client *c, const char *a) {
  (void)a;
  int n;
  const char *eff = set_mode_apply(c, "disable", GM_INTEL, &n);
  reply_ok(c, "disable", !strcmp(eff, "next-boot") ? ",\"effect\":\"next-boot\"" : ",\"effect\":\"now\"");
}
static void cmd_get_mode(struct client *c, const char *a) {
  (void)a;
  status_fresh();
  prefs_load();
  int ok = gfx_ok_now();
  char x[640];
  snprintf(x, sizeof x,
           ",\"mode\":\"%s\",\"policy\":\"%s\",\"active\":\"%s\",\"reason\":\"%s\",\"gfx_ok\":%d,\"test\":\"%s\",\"test_t\":%lld,"
           "\"cap\":%d,\"base\":\"%s\",\"pending\":\"%s\",\"apps_sync\":%d,\"prefs_n\":%d,\"opt_in_n\":%d",
           g_gm_names[g_gfx.mode], g_gfx.policy == GPOL_ALL ? "all" : "games", g_gfx.active ? "nvidia" : "intel",
           g_gfx.reason, ok, g_gfx.test, ok != -1 ? g_st.rec.t : 0, g_gfx.cap, dtx_ready() ? base_name(g_dtx.base) : "unknown",
           gfx_pending(), apps_sync(), g_prefs.n, g_apps.n_in);
  reply_ok(c, "get-mode", x);
}
static void cmd_set_app_policy(struct client *c, const char *a) {
  int p = !a ? -1 : !strcmp(a, "games") ? GPOL_GAMES : !strcmp(a, "all") ? GPOL_ALL : -1;
  if (p < 0) { reply_err(c, "set-app-policy", "EINVAL", "bad argument"); return; }
  status_fresh();
  if (!g_gfx.cap) { reply_err(c, "set-app-policy", "ENODEV", "no NVIDIA app graphics on this image"); return; }
  prop_set("persist.vendor.gb.gfx_apps", a);
  g_apps.dirty = 1;
  gfx_refresh();
  LOGI("control: set-app-policy %s by %s uid %d (%d apps opted in)", a, role_names[c->role], c->uid, g_apps.n_in);
  char x[96];
  snprintf(x, sizeof x, ",\"policy\":\"%s\",\"opt_in_n\":%d,\"effect\":\"restart-apps\"", a, g_apps.n_in);
  reply_ok(c, "set-app-policy", x);
}
/* set-app-intel / set-app-nvidia <pkg>:1|0 (24.2): one choice per package; :0 clears only that choice */
static void cmd_set_app_choice(struct client *c, const char *a, const char *cmd, char ch) {
  char pkg[160];
  const char *colon = a ? strrchr(a, ':') : NULL;
  size_t pl = colon ? (size_t)(colon - a) : 0;
  if (!colon || pl >= sizeof pkg || (strcmp(colon + 1, "0") && strcmp(colon + 1, "1"))) { reply_err(c, cmd, "EINVAL", "bad argument"); return; }
  memcpy(pkg, a, pl);
  pkg[pl] = 0;
  if (!pkg_name_ok(pkg)) { reply_err(c, cmd, "EINVAL", "bad argument"); return; }
  int on = colon[1] == '1';
  status_fresh();
  if (!g_gfx.cap) { reply_err(c, cmd, "ENODEV", "no NVIDIA app graphics on this image"); return; }
  prefs_load();
  if (!g_prefs.loaded) { reply_err(c, cmd, "EIO", "cannot read gfx_app_prefs"); return; }
  int i = pref_find(pkg), changed = 0;
  struct pref saved[PREFMAX];
  int saved_n = g_prefs.n;
  memcpy(saved, g_prefs.e, sizeof saved);
  if (on) {
    pkgs_refresh(1);
    if (!pkg_installed(pkg)) { reply_err(c, cmd, "ENOENT", "package not installed"); return; }
    if (i < 0) {
      if (g_prefs.n == PREFMAX) { reply_err(c, cmd, "EBUSY", "too many choices"); return; }
      snprintf(g_prefs.e[g_prefs.n].pkg, sizeof g_prefs.e[0].pkg, "%s", pkg);
      g_prefs.e[g_prefs.n++].ch = ch;
      qsort(g_prefs.e, g_prefs.n, sizeof g_prefs.e[0], pref_cmp);
      changed = 1;
    } else if (g_prefs.e[i].ch != ch) {
      g_prefs.e[i].ch = ch;                       /* replaces the other choice */
      changed = 1;
    }
  } else if (i >= 0 && g_prefs.e[i].ch == ch) {
    memmove(&g_prefs.e[i], &g_prefs.e[i + 1], (size_t)(g_prefs.n - i - 1) * sizeof g_prefs.e[0]);
    g_prefs.n--;
    changed = 1;
  }
  if (changed && prefs_save()) {
    g_prefs.n = saved_n;
    memcpy(g_prefs.e, saved, sizeof saved);
    reply_err(c, cmd, "EIO", "cannot write gfx_app_prefs");
    return;
  }
  if (changed) {
    g_apps.dirty = 1;
    gfx_refresh();
    LOGI("control: %s %s by %s uid %d", cmd, a, role_names[c->role], c->uid);
  }
  i = pref_find(pkg);
  char x[260];
  struct jb j = {x, sizeof x, 0, 0};
  jb_kstr(&j, "pkg", pkg);
  jb_fmt(&j, ",\"choice\":\"%s\",\"effect\":\"restart-app\"", i < 0 ? "default" : g_prefs.e[i].ch == 'i' ? "intel" : "nvidia");
  reply_ok(c, cmd, x);
}
static void cmd_set_app_intel(struct client *c, const char *a) { cmd_set_app_choice(c, a, "set-app-intel", 'i'); }
static void cmd_set_app_nvidia(struct client *c, const char *a) { cmd_set_app_choice(c, a, "set-app-nvidia", 'n'); }
/* get-apps [<start>]: the choices, paged (as many as fit 3500 bytes; next = -1 at the end) */
static void cmd_get_apps(struct client *c, const char *a) {
  int start = 0;
  if (a && (!is_num(a) || strlen(a) > 3 || (start = atoi(a)) > PREFMAX)) { reply_err(c, "get-apps", "EINVAL", "bad argument"); return; }
  prefs_load();
  char items[3400], x[3700];
  struct jb ji = {items, sizeof items, 0, 0};
  int i;
  items[0] = 0;
  for (i = start; i < g_prefs.n; i++) {
    char e[220];
    struct jb je = {e, sizeof e, 0, 0};
    jb_raw(&je, i > start ? ",{\"pkg\":" : "{\"pkg\":");
    jb_str(&je, g_prefs.e[i].pkg);
    jb_fmt(&je, ",\"choice\":\"%s\"}", g_prefs.e[i].ch == 'i' ? "intel" : "nvidia");
    if (ji.len + je.len > 3300) break;            /* the whole reply stays <= 3500 bytes */
    jb_raw(&ji, e);
  }
  snprintf(x, sizeof x, ",\"policy\":\"%s\",\"total\":%d,\"start\":%d,\"next\":%d,\"prefs\":[%s]",
           g_gfx.policy == GPOL_ALL ? "all" : "games", g_prefs.n, start, i < g_prefs.n ? i : -1, items);
  reply_ok(c, "get-apps", x);
}
/* ---- (v1.2) app catalog push from GbGpu (24.3): apps-begin <n>, apps-add <c>:<pkg>[,...], apps-commit <n> */
static int parse_count(const char *a, int max) {
  if (!a || !is_num(a) || strlen(a) > 4) return -1;
  int n = atoi(a);
  return n <= max ? n : -1;
}
static void cmd_apps_begin(struct client *c, const char *a) {
  int n = parse_count(a, CATMAX);
  if (n < 0) { reply_err(c, "apps-begin", "EINVAL", "bad argument"); return; }
  cl_stage_free(c);
  if (n && !(c->st_e = calloc((size_t)n, sizeof *c->st_e))) { reply_err(c, "apps-begin", "EIO", "out of memory"); return; }
  c->st_on = 1;
  c->st_max = n;
  char x[32];
  snprintf(x, sizeof x, ",\"n\":%d", n);
  reply_ok(c, "apps-begin", x);
}
static void cmd_apps_add(struct client *c, const char *a) {
  if (!c->st_on) { reply_err(c, "apps-add", "EINVAL", "no apps-begin"); return; }
  if (!a || (a[0] != 'g' && a[0] != 'a') || a[1] != ':' || !a[2]) { reply_err(c, "apps-add", "EINVAL", "bad argument"); return; }
  int k = 0;                                      /* syntax first, then append (all or nothing) */
  for (const char *s = a + 2; *s;) {
    const char *e = strchr(s, ',');
    size_t l = e ? (size_t)(e - s) : strlen(s);
    if (!l || l > 127 || (e && !e[1])) { reply_err(c, "apps-add", "EINVAL", "bad argument"); return; }
    for (size_t i = 0; i < l; i++) {
      char ch = s[i];
      if (!((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '_' || ch == '.')) {
        reply_err(c, "apps-add", "EINVAL", "bad argument");
        return;
      }
    }
    k++;
    s += l + (e ? 1 : 0);
  }
  if (c->st_n + k > c->st_max) { reply_err(c, "apps-add", "EINVAL", "count mismatch"); return; }
  for (const char *s = a + 2; *s;) {
    const char *e = strchr(s, ',');
    size_t l = e ? (size_t)(e - s) : strlen(s);
    memcpy(c->st_e[c->st_n].pkg, s, l);
    c->st_e[c->st_n].pkg[l] = 0;
    c->st_e[c->st_n++].cls = a[0];
    s += l + (e ? 1 : 0);
  }
  char x[32];
  snprintf(x, sizeof x, ",\"staged\":%d", c->st_n);
  reply_ok(c, "apps-add", x);
}
static void cmd_apps_commit(struct client *c, const char *a) {
  static char b[CATMAX * 140 + 128];
  static struct catent acc[CATMAX];
  if (!c->st_on) { reply_err(c, "apps-commit", "EINVAL", "no apps-begin"); return; }
  int n = parse_count(a, CATMAX);
  if (n < 0) { cl_stage_free(c); reply_err(c, "apps-commit", "EINVAL", "bad argument"); return; }
  if (n != c->st_n) { cl_stage_free(c); reply_err(c, "apps-commit", "EINVAL", "count mismatch"); return; }
  pkgs_refresh(1);                                /* checked against packages.list as it is now */
  if (!g_pkgs.ok) { cl_stage_free(c); reply_err(c, "apps-commit", "EIO", "cannot read packages.list"); return; }
  if (!data_dir_ok()) { cl_stage_free(c); reply_err(c, "apps-commit", "EIO", "cannot write gfx_catalog"); return; }
  int m = 0, dropped = 0;
  for (int i = 0; i < c->st_n; i++) {
    const struct catent *e = &c->st_e[i];
    int dup = 0;
    for (int k = 0; k < m && !dup; k++) dup = !strcmp(acc[k].pkg, e->pkg);
    if (dup || !pkg_name_ok(e->pkg) || !pkg_installed(e->pkg)) { dropped++; continue; }
    acc[m++] = *e;
  }
  cl_stage_free(c);
  qsort(acc, m, sizeof acc[0], cat_cmp);
  size_t k = (size_t)snprintf(b, sizeof b, "# pkgs %lld %lld\n", g_pkgs.mt, g_pkgs.sz);
  for (int i = 0; i < m; i++) k += (size_t)snprintf(b + k, sizeof b - k, "%s %c\n", acc[i].pkg, acc[i].cls);
  if (k >= sizeof b || file_save("/data/vendor/gb/gfx_catalog", b, k, 0644)) { reply_err(c, "apps-commit", "EIO", "cannot write gfx_catalog"); return; }
  memcpy(g_cat.e, acc, (size_t)m * sizeof acc[0]);
  g_cat.n = m;
  g_cat.loaded = g_cat.stamped = 1;
  g_cat.mt = g_pkgs.mt;
  g_cat.sz = g_pkgs.sz;
  g_apps.dirty = 1;
  status_fresh();
  gfx_refresh();
  LOGI("control: app catalog committed by %s uid %d: %d apps (%d dropped), %d opted in", role_names[c->role], c->uid, m, dropped,
       g_apps.n_in);
  char x[96];
  snprintf(x, sizeof x, ",\"n\":%d,\"dropped\":%d,\"opt_in_n\":%d,\"apps_sync\":%d", m, dropped, g_apps.n_in, apps_sync());
  reply_ok(c, "apps-commit", x);
}
static void cmd_run_selftest(struct client *c, const char *a) {
  (void)a;
  status_fresh();
  char st[24];
  int bn;
  const char *err;
  derive_state(st, sizeof st, &bn);
  const char *why = st_blocked(st, &err);
  if (why) { reply_err(c, "run-selftest", err, why); return; }
  LOGI("control: run-selftest by %s uid %d", role_names[c->role], c->uid);
  st_start(now_ms(), "run-selftest");
  reply_ok(c, "run-selftest", ",\"test\":\"running\"");
}
static void cmd_ui_caps(struct client *c, const char *a) {
  if (!a || (strcmp(a, "notify") && strcmp(a, "none"))) { reply_err(c, "ui-caps", "EINVAL", "bad argument"); return; }
  int v = !strcmp(a, "notify");
  if (v != g_gfx.ui_notify) LOGI("control: ui-caps %s by %s uid %d (N08 %s)", a, role_names[c->role], c->uid, v ? "left to GbGpu" : "posted by gb_power");
  g_gfx.ui_notify = v;
  reply_ok(c, "ui-caps", v ? ",\"notify\":1" : ",\"notify\":0");
}
static void cmd_detach_request(struct client *c, const char *a) {
  (void)a;
  if (!dtx_ready()) { reply_err(c, "detach-request", "ENODEV", "no DTX device"); return; }
  if (dec_running() || worker_has(J_DETACH) || worker_has(J_DREMOVE)) { reply_err(c, "detach-request", "EBUSY", "a detach decision is running"); return; }
  if (dtx_cmd(SDTX_IOCTL_LATCH_REQUEST, "LATCH_REQUEST")) { reply_err(c, "detach-request", "EIO", "LATCH_REQUEST failed"); return; }
  LOGI("control: detach requested by %s uid %d", role_names[c->role], c->uid);
  reply_ok(c, "detach-request", NULL);
}
static void cmd_report(struct client *c, const char *a) {
  (void)a;
  prop_set("vendor.gb.dgpu_report", "1");
  reply_ok(c, "report", NULL);
}
static void cmd_stop_compute(struct client *c, const char *a) {
  (void)a;
  prop_set("ctl.stop", "gb_gpu_compute");
  reply_ok(c, "stop-compute", NULL);
}
static int pid_list_parse(const char *a, int *pids) {   /* <pid>[,<pid>...], at most 16; <= 0 = bad */
  int n = 0;
  const char *s = a;
  while (s && *s) {
    char *e;
    if (*s < '0' || *s > '9') return -1;
    long v = strtol(s, &e, 10);
    if (v <= 0 || v > 4194304 || (*e && *e != ',') || n == 16) return -1;
    pids[n++] = (int)v;
    s = *e ? e + 1 : e;
    if (*e == ',' && !*s) return -1;
  }
  return a ? n : -1;
}
/* queues a force-stop for every pid that is a current closable holder; returns how many; fills the reply arrays */
static int close_holders_do(struct client *c, const char *cmd, const int *pids, int n, char *closed, size_t cn, char *refused, size_t rn) {
  holders_scan();
  struct jb jc = {closed, cn, 0, 0}, jr = {refused, rn, 0, 0};
  int nc = 0, nr = 0;
  closed[0] = refused[0] = 0;
  for (int i = 0; i < n; i++) {
    int k = holder_find(pids[i]);
    const char *why = NULL;
    const struct holder *h = k >= 0 ? &g_hold.h[k] : NULL;
    if (!h) why = "not-holder";
    else if (pids[i] == c->pid || !strcmp(h->pkg, "gb.sb3.gpu")) why = "self";
    else if (h->uid < 0 || h->uid % 100000 < 10000 || is_compute_pid(h->pid)) why = "system";
    else if (!h->pkg[0] || !h->closable) why = "unknown-pkg";
    if (why) {
      jb_fmt(&jr, "%s{\"pid\":%d,\"reason\":\"%s\"}", nr++ ? "," : "", pids[i], why);
      continue;
    }
    notify_force_stop(h->pkg, h->uid / 100000);
    LOGI("control: %s by %s uid %d: force-stop %s (user %d, pid %d)", cmd, role_names[c->role], c->uid, h->pkg,
         h->uid / 100000, h->pid);
    jb_fmt(&jc, "%s{\"pid\":%d,\"pkg\":", nc++ ? "," : "", h->pid);
    jb_str(&jc, h->pkg);
    jb_fmt(&jc, ",\"user\":%d}", h->uid / 100000);
  }
  return nc;
}
static void cmd_close_holders(struct client *c, const char *a) {
  int pids[17], n = pid_list_parse(a, pids);
  if (n <= 0) { reply_err(c, "close-holders", "EINVAL", "bad pid list"); return; }
  char closed[1600], refused[1200], x[3000];
  if (!close_holders_do(c, "close-holders", pids, n, closed, sizeof closed, refused, sizeof refused)) {
    reply_err(c, "close-holders", "ESTALE", "none of the pids is a closable holder");
    return;
  }
  snprintf(x, sizeof x, ",\"closed\":[%s],\"refused\":[%s]", closed, refused);
  reply_ok(c, "close-holders", x);
}
/* (v1.2, 23.9) close-holders, then the pending detach: LATCH_REQUEST once nouveau is free (the EC then raises the
 * normal request and the decision applies every gate) */
static void cmd_close_holders_detach(struct client *c, const char *a) {
  const char *cmd = "close-holders-and-detach";
  int pids[17], n = pid_list_parse(a, pids);
  if (n <= 0) { reply_err(c, cmd, "EINVAL", "bad pid list"); return; }
  if (!dtx_ready() || !base_attached()) { reply_err(c, cmd, "ENODEV", !dtx_ready() ? "no DTX device" : "keyboard base not attached"); return; }
  if (dec_running() || worker_has(J_DETACH) || worker_has(J_DREMOVE)) { reply_err(c, cmd, "EBUSY", "a detach decision is running"); return; }
  if (g_pend.kind) { reply_err(c, cmd, "EBUSY", "a detach is pending"); return; }
  char closed[1600], refused[1200], x[3000];
  if (!close_holders_do(c, cmd, pids, n, closed, sizeof closed, refused, sizeof refused)) {
    reply_err(c, cmd, "ESTALE", "none of the pids is a closable holder");
    return;
  }
  gpu_users_stop();
  long long t = now_ms();
  g_pend.kind = 1;
  g_pend.ok_reads = 0;
  g_pend.deadline = t + PEND_DEADLINE_MS;
  g_pend.next_read = t + PEND_READ_MS;
  g_bcast_pending = 1;
  LOGI("detach: pending until nouveau is free (%d s)", PEND_DEADLINE_MS / 1000);
  gfx_refresh();                                  /* pending=detach: gfx_active intel (23.2 row 10) */
  snprintf(x, sizeof x, ",\"closed\":[%s],\"refused\":[%s],\"detach\":\"pending\"", closed, refused);
  reply_ok(c, cmd, x);
}
static void cmd_compute_list(struct client *c, const char *a) {
  (void)a;
  allow_load();
  char b[4096];
  struct jb j = {b, sizeof b, 0, 0};
  jb_raw(&j, "{\"v\":1,\"ok\":true,\"cmd\":\"compute-list\",\"allow\":[");
  for (int i = 0; i < g_allow.n && j.len + 200 < sizeof b; i++) {
    jb_fmt(&j, "%s{\"appId\":%d,\"pkg\":", i ? "," : "", g_allow.e[i].appid);
    jb_str(&j, g_allow.e[i].pkg);
    jb_raw(&j, "}");
  }
  jb_raw(&j, "]}\n");
  cl_send(c, b);
}
static void cmd_compute_allow(struct client *c, const char *a) {
  if (!a || !pkg_name_ok(a)) { reply_err(c, "compute-allow", "EINVAL", "bad package name"); return; }
  pkgs_refresh(1);
  int id = appid_by_pkg(a);
  if (id < 0) { reply_err(c, "compute-allow", "ENOENT", "package not installed"); return; }
  allow_load();
  int i;
  for (i = 0; i < g_allow.n && strcmp(g_allow.e[i].pkg, a); i++) {}
  if (i == g_allow.n) {
    if (g_allow.n == ALLOWMAX) { reply_err(c, "compute-allow", "EBUSY", "allowlist full"); return; }
    g_allow.n++;
  }
  g_allow.e[i].appid = id;
  snprintf(g_allow.e[i].pkg, sizeof g_allow.e[i].pkg, "%s", a);
  if (allow_save()) { reply_err(c, "compute-allow", "EIO", "cannot write compute_allow"); return; }
  LOGI("control: compute-allow %s (%d) by %s uid %d", a, id, role_names[c->role], c->uid);
  char x[48];
  snprintf(x, sizeof x, ",\"appId\":%d", id);
  reply_ok(c, "compute-allow", x);
}
static void cmd_compute_deny(struct client *c, const char *a) {
  if (!a || !pkg_name_ok(a)) { reply_err(c, "compute-deny", "EINVAL", "bad package name"); return; }
  allow_load();
  int i;
  for (i = 0; i < g_allow.n && strcmp(g_allow.e[i].pkg, a); i++) {}
  if (i == g_allow.n) { reply_err(c, "compute-deny", "ENOENT", "not in the allowlist"); return; }
  g_allow.e[i] = g_allow.e[--g_allow.n];
  if (allow_save()) { reply_err(c, "compute-deny", "EIO", "cannot write compute_allow"); return; }
  LOGI("control: compute-deny %s by %s uid %d", a, role_names[c->role], c->uid);
  reply_ok(c, "compute-deny", NULL);
}
static void cmd_compute_start(struct client *c, const char *a) {
  (void)a;
  char st[24];
  int n;
  status_fresh();
  derive_state(st, sizeof st, &n);
  if (!strcmp(st, "detaching") || !strcmp(st, "wedged") || !strcmp(st, "fault")) { reply_err(c, "compute-start", "EBUSY", st); return; }
  /* (R-6) not inside a load window: the health check (gb_nouveau.sh) and the rebind watch need D3cold */
  if (load_window_open() || !strcmp(st, "loading")) { reply_err(c, "compute-start", "EAGAIN", "NVIDIA driver starting"); return; }
  if (strcmp(st, "standby") && strncmp(st, "busy:", 5)) { reply_err(c, "compute-start", "ENODEV", "NVIDIA GPU not ready"); return; }
  int running = prop_is("init.svc.gb_gpu_compute", "running");
  if (!running) { prop_set("ctl.start", "gb_gpu_compute"); LOGI("control: compute-start by %s uid %d", role_names[c->role], c->uid); }
  reply_ok(c, "compute-start", running ? ",\"socket\":\"/dev/socket/gb_gpu_compute\",\"state\":\"running\""
                                       : ",\"socket\":\"/dev/socket/gb_gpu_compute\",\"state\":\"starting\"");
}
static void cmd_compute_state(struct client *c, const char *a) {
  static const char *const ok[] = {"idle", "loading", "loaded", "serving", "unloading"};
  int k = -1;
  for (int i = 0; a && i < 5; i++) if (!strcmp(a, ok[i])) k = i;
  if (k < 0) { reply_err(c, "compute-state", "EINVAL", "bad state"); return; }
  for (int i = 0; i < g_ncl; i++)
    if (&g_cl[i] != c && g_cl[i].compute && !g_cl[i].dead) { reply_err(c, "compute-state", "EBUSY", "another compute service connection"); return; }
  c->compute = 1;
  g_cmp.conn = 1;
  snprintf(g_cmp.state, sizeof g_cmp.state, "%s", ok[k]);
  int want = k >= 1 && k <= 3;
  if (want != g_cmp.want) LOGI("compute: %s (power hold %s)", ok[k], want ? "on" : "off");
  g_cmp.want = want;
  inventory();
  hold_eval();
  compute_publish();
  reply_ok(c, "compute-state", want ? ",\"hold\":1" : ",\"hold\":0");
}
static void compute_conn_closed(void) {
  LOGI("compute: service connection closed (power hold released)");
  g_cmp.conn = 0;
  g_cmp.state[0] = 0;
  g_cmp.want = 0;
  inventory();
  hold_eval();
  compute_publish();
}

#define RB(r) (1u << (r))
#define RSU (RB(R_ROOT) | RB(R_SHELL) | RB(R_UI))
#define RALL (RSU | RB(R_CSVC) | RB(R_CCLIENT) | RB(R_OTHER))
static const struct { const char *name; unsigned roles; int arg; void (*fn)(struct client *, const char *); } g_cmds[] = {
  {"hello", RALL, 0, cmd_hello},
  {"status", RSU, 0, cmd_status},
  {"watch", RSU, 0, cmd_watch},
  {"holders", RSU, 0, cmd_holders},
  {"enable", RSU, 0, cmd_enable},
  {"disable", RSU, 0, cmd_disable},
  {"detach-request", RSU, 0, cmd_detach_request},
  {"report", RSU, 0, cmd_report},
  {"stop-compute", RSU, 0, cmd_stop_compute},
  {"close-holders", RSU, 1, cmd_close_holders},
  {"compute-list", RSU, 0, cmd_compute_list},
  {"compute-allow", RSU, 1, cmd_compute_allow},
  {"compute-deny", RSU, 1, cmd_compute_deny},
  {"compute-start", RSU | RB(R_CCLIENT), 0, cmd_compute_start},
  {"compute-state", RB(R_CSVC), 1, cmd_compute_state},
  /* v1.2 (INTERFACES 26.1): root, shell, ui only; compute-svc, compute-client and other get EPERM */
  {"get-mode", RSU, 0, cmd_get_mode},
  {"set-mode", RSU, 1, cmd_set_mode},
  {"set-app-policy", RSU, 1, cmd_set_app_policy},
  {"set-app-intel", RSU, 1, cmd_set_app_intel},
  {"set-app-nvidia", RSU, 1, cmd_set_app_nvidia},
  {"get-apps", RSU, 1, cmd_get_apps},
  {"apps-begin", RSU, 1, cmd_apps_begin},
  {"apps-add", RSU, 1, cmd_apps_add},
  {"apps-commit", RSU, 1, cmd_apps_commit},
  {"run-selftest", RSU, 0, cmd_run_selftest},
  {"list-holders", RSU, 0, cmd_list_holders},
  {"close-holders-and-detach", RSU, 1, cmd_close_holders_detach},
  {"ui-caps", RSU, 1, cmd_ui_caps},
};

static void cl_line(struct client *c, char *line) {
  size_t l = strlen(line);
  if (l && line[l - 1] == '\r') line[--l] = 0;
  int bad = !l || line[0] == ' ' || line[l - 1] == ' ' || strstr(line, "  ");
  for (size_t i = 0; i < l; i++) {
    unsigned char ch = (unsigned char)line[i];
    if (ch < 0x20 || ch > 0x7e) bad = 1;
  }
  char *arg = NULL, *sp = strchr(line, ' ');
  if (sp) { *sp = 0; arg = sp + 1; if (strchr(arg, ' ')) bad = 1; }
  if (bad) { reply_err(c, "", "EINVAL", "bad request"); return; }
  char name[65];
  snprintf(name, sizeof name, "%s", line);
  for (size_t i = 0; i < sizeof g_cmds / sizeof g_cmds[0]; i++) {
    if (strcmp(g_cmds[i].name, line)) continue;
    if (!(g_cmds[i].roles & RB(c->role))) { reply_err(c, name, "EPERM", "not allowed"); return; }
    if (!g_cmds[i].arg && arg) { reply_err(c, name, "EINVAL", "unexpected argument"); return; }
    g_cmds[i].fn(c, arg);
    return;
  }
  reply_err(c, name, "ENOSYS", "unknown command");
}

static void cl_read(struct client *c) {
  for (;;) {
    char buf[512];
    ssize_t r = recv(c->fd, buf, sizeof buf, MSG_DONTWAIT);
    if (r < 0) {
      if (errno == EINTR) continue;
      if (errno != EAGAIN && errno != EWOULDBLOCK) c->dead = 1;
      return;
    }
    if (r == 0) { c->dead = 1; return; }          /* EOF */
    if (c->watch || c->closing) continue;         /* watch: further input ignored until EOF */
    for (ssize_t i = 0; i < r && !c->dead && !c->closing && !c->watch; i++) {
      if (buf[i] == '\n') {
        c->in[c->inlen] = 0;
        c->inlen = 0;
        c->last = now_ms();
        cl_line(c, c->in);
        continue;
      }
      if (c->inlen >= 254) {                      /* 255 bytes including the newline at most (9.1) */
        reply_err(c, "", "E2BIG", "request too long");
        c->closing = 1;
        cl_flush(c);
        break;
      }
      c->in[c->inlen++] = buf[i];
    }
  }
}

static struct client *cl_add(int fd, int uid, int pid) {
  struct client *c = &g_cl[g_ncl++];
  memset(c, 0, sizeof *c);
  c->fd = fd;
  c->uid = uid;
  c->pid = pid;
  c->role = peer_role(uid, pid);
  c->last = now_ms();
  int sz = 16384;                                 /* small kernel buffer: a non-reading client is noticed early */
  setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &sz, sizeof sz);
  LOGD("control: client uid %d pid %d role %s", uid, pid, role_names[c->role]);
  return c;
}
static void cl_accept(void) {
  for (;;) {
    int fd = accept4(g_listen, NULL, NULL, SOCK_NONBLOCK | SOCK_CLOEXEC);
    if (fd < 0) {
      if (errno == EINTR) continue;
      if (errno != EAGAIN && errno != EWOULDBLOCK) LOGW("control: accept: %s", strerror(errno));
      return;
    }
    if (g_ncl >= MAXCL) {
      static const char m[] = "{\"v\":1,\"ok\":false,\"cmd\":\"\",\"err\":\"EBUSY\",\"msg\":\"too many connections\"}\n";
      if (send(fd, m, sizeof m - 1, MSG_DONTWAIT | MSG_NOSIGNAL) < 0) {}
      close(fd);
      LOGW("control: connection refused (%d clients connected)", MAXCL);
      continue;
    }
    int uid = -1, pid = 0;
    if (peer_cred(fd, &uid, &pid)) { close(fd); continue; }
    cl_add(fd, uid, pid);
  }
}
static void cl_sweep(void) {
  for (int i = 0; i < g_ncl;) {
    if (!g_cl[i].dead) { i++; continue; }
    int was_compute = g_cl[i].compute;
    close(g_cl[i].fd);
    cl_stage_free(&g_cl[i]);                      /* a staged catalog is discarded on close */
    if (i != g_ncl - 1) g_cl[i] = g_cl[g_ncl - 1];
    g_ncl--;
    if (was_compute) compute_conn_closed();
  }
}
static void watch_update(long long t) {
  char a[4096], b[4096];
  int have = 0, any = 0;
  for (int i = 0; i < g_ncl; i++) any |= g_cl[i].watch && !g_cl[i].dead;
  if (!any) return;
  status_json(a, sizeof a, "status", 0);
  for (int i = 0; i < g_ncl; i++) {
    struct client *c = &g_cl[i];
    if (!c->watch || c->dead) continue;
    if (strcmp(a, c->last_st)) {
      if (!have) { status_json(b, sizeof b, "status", 1); have = 1; }
      snprintf(c->last_st, sizeof c->last_st, "%s", a);
      cl_send(c, b);
    }
    if (t >= c->ping_at) {
      cl_send(c, "{\"v\":1,\"ok\":true,\"cmd\":\"ping\"}\n");
      c->ping_at = t + WATCH_PING_MS;
    }
  }
}
/* INTERFACES 9.1: idle non-watch connections are closed after 30 s. Watch connections and the compute service's
 * connection are exempt: the compute power hold is tied to that connection's lifetime (9.3, D14), and the service
 * sends compute-state only on changes (11.5), so an idle close would drop the hold of a loaded model. */
static int cl_idle_exempt(const struct client *c) { return c->watch || c->compute; }
static void clients_idle(long long t) {
  for (int i = 0; i < g_ncl; i++)
    if (!cl_idle_exempt(&g_cl[i]) && !g_cl[i].dead && t - g_cl[i].last > CL_IDLE_MS) { LOGD("control: idle client closed"); g_cl[i].dead = 1; }
}

static int ctl_open(const char *test_path) {
  int fd = -1;
  const char *env = getenv("ANDROID_SOCKET_gb_power");
  if (!test_path && !g_root[0] && env && is_num(env)) {   /* init's "socket gb_power stream 0666 root root" */
    fd = atoi(env);
    if (fcntl(fd, F_GETFD) < 0) fd = -1;
    else fcntl(fd, F_SETFD, FD_CLOEXEC);
  }
  if (fd < 0 && (test_path || !g_root[0])) {
    const char *path = test_path ? test_path : "/dev/socket/gb_power";
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    if (strlen(path) >= sizeof a.sun_path) { LOGE("control: socket path too long"); return -1; }
    snprintf(a.sun_path, sizeof a.sun_path, "%s", path);
    fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return -1;
    unlink(path);
    if (bind(fd, (struct sockaddr *)&a, sizeof a) || chmod(path, 0666)) {
      LOGE("control: bind %s: %s", path, strerror(errno));
      close(fd);
      return -1;
    }
    LOGI("control: listening on %s (own socket)", path);
  }
  if (fd < 0) return -1;
  fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
  if (listen(fd, 8)) { LOGE("control: listen: %s", strerror(errno)); close(fd); return -1; }
  g_listen = fd;
  prop_put("vendor.gb.power.api", "1");
  return 0;
}

/* ------------------------------------------------------------------------------------------------ uevents */
struct uev { const char *action, *devpath, *subsystem, *devname, *psy_name, *pci_id, *pci_class; };

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
    else if (!strncmp(s, "PCI_CLASS=", 10)) u->pci_class = s + 10;
    i += l + 1;
  }
}

static long long g_bat_due = 0, g_profile_due = 0, g_tp_due = 0, g_last_bat, g_start;
static int g_profile_done;

static void uev_handle(const struct uev *u) {
  if (!u->subsystem || !u->action) return;
  long long t = now_ms();
  if (!strcmp(u->subsystem, "power_supply")) {
    if (u->psy_name && !strncmp(u->psy_name, "test_", 5)) return;   /* our own writes */
    if (g_bat_due < 0 || g_bat_due > t + 300) g_bat_due = t + 300;    /* coalesce bursts */
  } else if (!strcmp(u->subsystem, "pci") && u->pci_id && !strncasecmp(u->pci_id, "10DE:", 5)) {
    if (!strcmp(u->action, "remove")) {
      const char *b = u->devpath ? strrchr(u->devpath, '/') : NULL;
      LOGI("dGPU %s removed (base detached or detach worker)", b ? b + 1 : "?");
    } else if (!strcmp(u->action, "add") || !strcmp(u->action, "bind")) {
      if (g_dgpu_on) g_dgpu_due = t + 500;
      g_dgpu_report_due = t + 8000;
      /* (v1.2) a re-added dGPU (PCI_CLASS 03xxxx; absent in old kernels: counted): a new appearance for the hot
       * attach (23.7), and the rebind guard if nouveau.ko is loaded and the last inventory had no 10de function */
      if (!strcmp(u->action, "add") && (!u->pci_class || (strlen(u->pci_class) == 5 && u->pci_class[0] == '3'))) {
        if (g_nv.inited) g_nv.appear = 1;
        rb_arm("10de PCI add", NULL);
      }
    }
    if (g_tick_due < 0 || g_tick_due > t + 100) g_tick_due = t + 100;   /* state, latch, node permissions */
  } else if (!strcmp(u->subsystem, "drm") || (!strcmp(u->subsystem, "module") && u->devpath &&
                                              !strcmp(u->devpath, "/module/nouveau"))) {
    if (g_tick_due < 0 || g_tick_due > t + 300) g_tick_due = t + 300;
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
    if (r < 0 && errno == ENOBUFS) {
      LOGW("uevent overrun: rescanning");
      g_bat_due = g_dgpu_due = g_tick_due = now_ms();
      continue;
    }
    if (r <= 0) return;
    buf[r] = 0;
    struct uev u;
    uev_parse(buf, (int)r, &u);
    uev_handle(&u);
  }
}

/* ------------------------------------------------------------------------------------------------ main loop */
static volatile sig_atomic_t g_term;
static void on_term(int s) { (void)s; g_term = 1; }
static sigset_t g_waitmask;
static int g_have_mask;

static long long bat_periodic(long long t) {
  /* backstop poll: 20 s while bridging; without test_power 5 s during boot, then 60 s (its load uevent wakes us) */
  return g_last_bat + (g_tp.ok ? 20000 : t - g_start < 120000 ? 5000 : 60000);
}

static void run_due(long long t) {
  if (g_tp_due > 0 && t >= g_tp_due) { g_tp_due = -1; if (tp_probe()) g_bat_due = t; }
  if ((g_bat_due >= 0 && t >= g_bat_due) || t >= bat_periodic(t)) {
    g_bat_due = -1;
    g_last_bat = t;
    if (!g_tp.ok) tp_probe();
    battery_update(0);
  }
  worker_deadline(t);
  if (g_dgpu_due >= 0 && t >= g_dgpu_due) { g_dgpu_due = -1; if (g_dgpu_on) dgpu_enforce("hot-plug", 0); }
  if (g_dgpu_report_due >= 0 && t >= g_dgpu_report_due) {
    g_dgpu_report_due = -1;
    if (g_dgpu_on) dgpu_enforce("report", 1);
    else { inventory(); dgpu_report("report"); }
  }
  if (g_dtx.fd >= 0 && g_dtx.requery_at > 0 && t >= g_dtx.requery_at) { g_dtx.requery_at = -1; dtx_query("re-check"); }
  if (g_dtx_on && g_dtx.fd < 0 && g_dtx.retry_at > 0 && t >= g_dtx.retry_at) {
    /* retry while the driver may still be probing; after 2 min only on its uevent */
    g_dtx.retry_at = dtx_open() && t - g_start < 120000 ? t + 3000 : -1;
  }
  if (g_profile_due > 0 && t >= g_profile_due) {
    char want[PROP_VALUE_MAX];
    prop_get("persist.vendor.gb.perf_profile", want);
    g_profile_done = profile_apply(want) == 0;
    g_profile_due = g_profile_done || t - g_start > 120000 ? -1 : t + 2000;
    if (!g_profile_done && g_profile_due < 0) LOGW("no platform profile interface after 2 min");
  }
  if (dec_running() && t >= g_dec.step_at) decision_step(t);
  if (g_pend.kind) pending_detach_step(t);
  if (g_rs.active) rescan_step(t);
  if (g_dtx.ec == EC_IN_PROGRESS && g_dtx.echo_until > 0 && t >= g_dtx.echo_until && !dec_running()) {
    LOGW("detach: no answer from the EC to LATCH_CANCEL after %d s: ready again", ECHO_WAIT_MS / 1000);
    ec_ready();
  }
  if (g_dtx.ec == EC_CONFIRMED && g_dtx.slow_confirm_at > 0 && t - g_dtx.slow_confirm_at >= CONFIRM_WATCHDOG_MS &&
      g_dtx.latch != SDTX_LATCH_OPENED) {
    g_dtx.slow_confirm_at = 0;
    dtx_query("confirm watchdog");                /* fresh EC state, not the cached one */
    if (g_dtx.latch == SDTX_LATCH_OPENED) {
      LOGI("detach: the latch is open after all (confirm watchdog)");
    } else {
      /* make the EC abort the confirmed process; the rescan waits for its REQUEST echo (or the echo timeout) */
      LOGW("detach: the latch did not open within %d s of the confirm: LATCH_CANCEL, treating the detach as canceled",
           CONFIRM_WATCHDOG_MS / 1000);
      g_dtx.ec = EC_IN_PROGRESS;
      dtx_cancel_await();
    }
  }
  clients_idle(t);
  if (t >= g_tick_due) status_tick(t);
  else watch_update(t);
}

static long long loop_due(long long t) {
  long long due = -1;
#define DUE(x) do { long long v_ = (x); if (v_ >= 0 && (due < 0 || v_ < due)) due = v_; } while (0)
  DUE(g_bat_due);
  DUE(bat_periodic(t));
  DUE(g_dgpu_due);
  DUE(g_dgpu_report_due);
  if (g_dtx.fd >= 0 && g_dtx.requery_at > 0) DUE(g_dtx.requery_at);
  if (g_dtx.fd < 0 && g_dtx_on && g_dtx.retry_at > 0) DUE(g_dtx.retry_at);
  if (g_profile_due > 0) DUE(g_profile_due);
  if (g_tp_due > 0) DUE(g_tp_due);
  DUE(g_tick_due);
  if (dec_running()) DUE(g_dec.step_at);
  if (g_pend.kind) DUE(g_pend.next_read);
  if (g_wk.busy) DUE(g_wk.t0 + g_wk.cur.deadline_ms);
  if (g_rs.active) DUE(g_rs.phase == RS_RETRY ? g_rs.next_at : t + 200);
  if (g_dtx.echo_until > 0) DUE(g_dtx.echo_until);
  if (g_dtx.slow_confirm_at > 0) DUE(g_dtx.slow_confirm_at + CONFIRM_WATCHDOG_MS);
  if (g_bcast_pending) DUE(g_bcast_last + BCAST_MS);
  for (int i = 0; i < g_ncl; i++) DUE(g_cl[i].watch ? g_cl[i].ping_at : g_cl[i].compute ? -1 : g_cl[i].last + CL_IDLE_MS + 1);
#undef DUE
  return due;
}

static void loop_once(int max_ms) {
  long long t = now_ms(), due = loop_due(t);
  int timeout = due < 0 ? 60000 : due <= t ? 0 : (int)(due - t > 60000 ? 60000 : due - t);
  if (max_ms >= 0 && timeout > max_ms) timeout = max_ms;
  struct pollfd pf[4 + MAXCL];
  int np = 0, iue = -1, idtx = -1, iwk = -1, il = -1, icl = 0, ncl = g_ncl, cfd[MAXCL];
  if (g_ue >= 0) { iue = np; pf[np++] = (struct pollfd){.fd = g_ue, .events = POLLIN}; }
  if (g_dtx.fd >= 0) { idtx = np; pf[np++] = (struct pollfd){.fd = g_dtx.fd, .events = POLLIN}; }
  if (g_wk.fd >= 0) { iwk = np; pf[np++] = (struct pollfd){.fd = g_wk.fd, .events = POLLIN}; }
  if (g_listen >= 0) { il = np; pf[np++] = (struct pollfd){.fd = g_listen, .events = POLLIN}; }
  icl = np;
  for (int i = 0; i < ncl; i++) {
    cfd[i] = g_cl[i].fd;
    pf[np++] = (struct pollfd){.fd = g_cl[i].fd, .events = (short)(POLLIN | (g_cl[i].outlen ? POLLOUT : 0))};
  }
  struct timespec ts = {timeout / 1000, (timeout % 1000) * 1000000L};
  int r = ppoll(pf, np, &ts, g_have_mask ? &g_waitmask : NULL);
  if (r < 0 && errno != EINTR) LOGE("poll: %s", strerror(errno));
  if (r > 0) {
    if (iue >= 0 && pf[iue].revents) uevent_read(g_ue);
    if (idtx >= 0 && pf[idtx].revents && pf[idtx].fd == g_dtx.fd) {   /* (closed meanwhile: misc remove uevent) */
      if (pf[idtx].revents & (POLLERR | POLLHUP | POLLNVAL)) { LOGW("DTX device hung up"); dtx_close(); g_dtx.retry_at = now_ms() + 2000; }
      else dtx_read();
    }
    if (iwk >= 0 && pf[iwk].revents && pf[iwk].fd == g_wk.fd) worker_read();
    for (int i = 0; i < ncl && i < g_ncl; i++) {
      short re = pf[icl + i].revents;
      struct client *c = &g_cl[i];
      if (!re || c->fd != cfd[i]) continue;
      if (re & POLLOUT) cl_flush(c);
      if (re & (POLLIN | POLLHUP | POLLERR)) cl_read(c);
    }
    if (il >= 0 && pf[il].revents) cl_accept();
  }
  run_due(now_ms());
  cl_sweep();
}

/* SIGTERM (stop gb_power, shutdown): UNLOCK only if driverless or shutdown is under way (D13) */
static void term_handle(void) {
  char pc[PROP_VALUE_MAX];
  inventory();
  prop_get("sys.powerctl", pc);
  char what[200];
  int disk = !pc[0] && bootdisk_guard(what, sizeof what);
  if (pc[0] || (!g_inv.bound_any && !disk)) latch_set(0, 1, pc[0] ? "SIGTERM during shutdown" : "SIGTERM, dGPU driverless");
  else if (g_dtx.fd >= 0)
    LOGW("SIGTERM with %s: the latch stays locked (the EC cancels detach requests until gb_power runs again)",
         g_inv.bound_any ? "a driver bound to the dGPU" : "a mounted drive in the keyboard base");
  /* (R-1) init sends SIGTERM a few ms after setting sys.powerctl, usually before the next tick saw it: the clean-end
   * marker handling runs here too. A SIGTERM without sys.powerctl (a stop) keeps them (adopted after a restart). */
  if (pc[0]) shutdown_markers("SIGTERM during shutdown");
  LOGI("exiting");
}

/* ------------------------------------------------------------------------------------------------ --dtx-monitor */
/* Read-only EC event printer for the timeout measurement (Phase 3 HW check 2): run it while the daemon is stopped.
 * Enables events on its own fd and only reads; never sends confirm/cancel/heartbeat/lock. */
static int dtx_monitor(FILE *out, long long idle_exit_ms) {
  char p[PATH_MAX];
  int fd = open(rpath(p, sizeof p, "/dev/surface/dtx"), O_RDWR | O_NONBLOCK | O_CLOEXEC);
  if (fd < 0) { fprintf(stderr, "gb_power: %s: %s\n", p, strerror(errno)); return 1; }
  int r = dtx_ioctl(fd, SDTX_IOCTL_EVENTS_ENABLE, NULL);
  if (r) { fprintf(stderr, "gb_power: EVENTS_ENABLE: %s\n", strerror(-r)); close(fd); return 1; }
  struct sdtx_base_info bi = {0, 0};
  uint16_t lv = 0xffff, mv = 0xffff;
  long long t = now_ms();
  dtx_ioctl(fd, SDTX_IOCTL_GET_BASE_INFO, &bi);
  dtx_ioctl(fd, SDTX_IOCTL_GET_LATCH_STATUS, &lv);
  dtx_ioctl(fd, SDTX_IOCTL_GET_DEVICE_MODE, &mv);
  fprintf(out, "%lld.%03lld STATE base=%s (0x%04x, id 0x%04x) latch=%s mode=%s\n", t / 1000, t % 1000, base_name(bi.state),
          bi.state, bi.base_id, latch_name(lv), mode_name(mv));
  fflush(out);
  unsigned char buf[1024];
  size_t blen = 0;
  long long last = t;
  while (!g_term) {
    struct pollfd pf = {.fd = fd, .events = POLLIN};
    int pr = poll(&pf, 1, idle_exit_ms > 0 ? 20 : 1000);
    if (pr > 0) {
      ssize_t n = read(fd, buf + blen, sizeof buf - blen);
      if (n == 0 || (n < 0 && errno != EAGAIN && errno != EINTR)) break;
      if (n > 0) { blen += (size_t)n; last = now_ms(); }
      size_t off = 0;
      while (blen - off >= 4) {
        unsigned len = buf[off] | buf[off + 1] << 8, code = buf[off + 2] | buf[off + 3] << 8;
        if (len > sizeof buf - 4) { off = blen; break; }
        if (blen - off < 4 + len) break;
        const unsigned char *d = buf + off + 4;
        unsigned v = len >= 2 ? (unsigned)(d[0] | d[1] << 8) : 0;
        t = now_ms();
        fprintf(out, "%lld.%03lld ", t / 1000, t % 1000);
        switch (code) {
          case SDTX_EVENT_REQUEST: fprintf(out, "REQUEST\n"); break;
          case SDTX_EVENT_CANCEL: fprintf(out, "CANCEL %s (0x%04x)\n", cancel_name(v), v); break;
          case SDTX_EVENT_BASE_CONNECTION:
            fprintf(out, "BASE_CONNECTION %s (0x%04x, id 0x%04x)\n", base_name(v), v, len >= 4 ? (unsigned)(d[2] | d[3] << 8) : 0);
            break;
          case SDTX_EVENT_LATCH_STATUS: fprintf(out, "LATCH_STATUS %s (0x%04x)\n", latch_name(v), v); break;
          case SDTX_EVENT_DEVICE_MODE: fprintf(out, "DEVICE_MODE %s (0x%04x)\n", mode_name(v), v); break;
          default: fprintf(out, "EVENT code=%u len=%u\n", code, len);
        }
        fflush(out);
        off += 4 + len;
      }
      memmove(buf, buf + off, blen - off);
      blen -= off;
    }
    if (idle_exit_ms > 0 && now_ms() - last > idle_exit_ms) break;
  }
  close(fd);
  return 0;
}

/* ------------------------------------------------------------------------------------------------ main */
static void read_cmdline(void) {
  char p[PATH_MAX], c[4096];
  if (read_text(rpath(p, sizeof p, "/proc/cmdline"), c, sizeof c) <= 0) return;
  char nv[32] = "", bl[512] = "";
  int have_nv = 0;
  for (char *t = strtok(c, " \t"); t; t = strtok(NULL, " \t")) {   /* repeated keys: last one wins */
    if (!strcmp(t, "gb.dgpu=keep")) { g_dgpu_on = 0; LOGI("gb.dgpu=keep: dGPU left alone"); }
    if (!strcmp(t, "gb.power.hide=0")) g_hide = 0;
    if (!strncmp(t, "gb.boot=", 8)) snprintf(g_boot, sizeof g_boot, "%s", t + 8);
    if (!strncmp(t, "gb.nouveau=", 11)) { snprintf(nv, sizeof nv, "%s", t + 11); have_nv = 1; }
    if (!strncmp(t, "module_blacklist=", 17)) {   /* every occurrence counts (fail safe, as gb_nouveau.sh) */
      size_t l = strlen(bl);
      snprintf(bl + l, sizeof bl - l, "%s%s", l ? "," : "", t + 17);
    }
  }
  for (char *t = strtok(bl, ","); t; t = strtok(NULL, ",")) if (!strcmp(t, "nouveau")) g_cmd_kill = 1;
  if (have_nv && strcmp(nv, "once") && strcmp(nv, "test")) g_cmd_kill = 1;   /* 0 and unknown values (D17) */
  g_cmd_test = have_nv && !strcmp(nv, "test");   /* (v1.2) app graphics stay Intel, no self-test (review C5) */
  g_cmd_once = have_nv && !strcmp(nv, "once");
  if (g_cmd_kill) LOGI("nouveau kill switch on the command line: state 'disabled' this boot");
}

#ifndef __ANDROID__
/* host test builds only, and only with -r: GB_POWER_TEST_DTX=1 answers the DTX ioctls like an EC with the base
 * attached and the latch closed (the socket test opens a FIFO as /dev/surface/dtx), for the 26.3 golden transcripts */
static int test_dtx_ioctl(int fd, unsigned long req, void *arg) {
  (void)fd;
  if (req == SDTX_IOCTL_GET_BASE_INFO) { struct sdtx_base_info *b = arg; b->state = SDTX_BASE_ATTACHED; b->base_id = 0x205; return 0; }
  if (req == SDTX_IOCTL_GET_LATCH_STATUS) { *(uint16_t *)arg = SDTX_LATCH_CLOSED; return 0; }
  if (req == SDTX_IOCTL_GET_DEVICE_MODE) { *(uint16_t *)arg = SDTX_DEVICE_MODE_LAPTOP; return 0; }
  return 0;
}
#endif

static void usage(void) {
  fprintf(stderr, "usage: gb_power [-p profile] [-o] [-r root [-S socket]] [-H] [-D] [-G] [-I] [-K driver] [-v]\n"
                  "       gb_power [-r root] --dtx-monitor\n");
}

int main(int argc, char **argv) {
  const char *profile = NULL, *sock = NULL;
  int once = 0, opt, monitor = 0;
  for (int i = 1; i < argc; i++)
    if (!strcmp(argv[i], "--dtx-monitor")) { monitor = 1; for (int k = i; k < argc - 1; k++) argv[k] = argv[k + 1]; argc--; i--; }
  while ((opt = getopt(argc, argv, "p:or:S:HDGIK:v")) != -1) {
    switch (opt) {
      case 'K': snprintf(g_keep_drv, sizeof g_keep_drv, "%s", optarg); g_keep_cli = 1; break;
      case 'p': profile = optarg; break;
      case 'o': once = 1; break;
      case 'r': snprintf(g_root, sizeof g_root, "%s", optarg); break;
      case 'S': sock = optarg; break;
      case 'H': g_hide = 0; break;
      case 'D': g_dtx_on = 0; break;
      case 'G': g_dgpu_on = 0; break;
      case 'I': g_inhibit_on = 0; break;
      case 'v': g_verbose = 1; break;
      default: usage(); return 2;
    }
  }
  if (sock && !g_root[0]) { fprintf(stderr, "gb_power: -S is only for tests (with -r)\n"); return 2; }
#ifndef __ANDROID__
  if (g_root[0] && getenv("GB_POWER_TEST_DTX")) dtx_ioctl = test_dtx_ioctl;
#endif
  setvbuf(stdout, NULL, _IOLBF, 0);
  signal(SIGPIPE, SIG_IGN);
  if (monitor) {
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_term;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    return dtx_monitor(stdout, 0);
  }
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

  gfx_state_reset();                              /* (the static initial values; unit tests call it per fake boot) */
  read_cmdline();
  LOGI("starting (hide=%d dtx=%d dgpu=%d inhibit=%d)", g_hide, g_dtx_on, g_dgpu_on, g_inhibit_on);
  if (g_keep_cli)
    LOGI("-K '%s': %s", g_keep_drv, g_keep_drv[0] ? "VGA functions bound to it stay bound except at detach"
                                                  : "every dGPU driver is unbound (test mode)");
  {
    char p[PATH_MAX];
    if (mkdir(rpath(p, sizeof p, "/dev/gb_dgpu"), 0700) && errno != EEXIST) LOGW("mkdir %s: %s", p, strerror(errno));
  }
  struct sigaction sa;
  memset(&sa, 0, sizeof sa);
  sa.sa_handler = on_term;                        /* no SA_RESTART: ppoll returns EINTR */
  sigaction(SIGTERM, &sa, NULL);
  sigset_t blk;
  sigemptyset(&blk);
  sigaddset(&blk, SIGTERM);
  sigprocmask(SIG_BLOCK, &blk, &g_waitmask);      /* SIGTERM only arrives inside ppoll: no lost wake-up */
  sigdelset(&g_waitmask, SIGTERM);
  g_have_mask = 1;
  g_start = g_last_bat = now_ms();
  prop_put("vendor.gb.dgpu.latch", "unknown");
  g_ue = uevent_open();                           /* before the first scan, so no change is missed */
  if (g_ue < 0) LOGW("uevent socket failed (%s): polling only", strerror(errno));
  if (!tp_probe()) LOGW("test_power not loaded yet: battery bridge waits for it");
  battery_update(1);
  inventory();
  used();
  wedged();
  if (g_dgpu_on) { dgpu_enforce("start", 0); g_dgpu_report_due = now_ms() + 8000; }
  if (g_dtx_on && dtx_open()) g_dtx.retry_at = now_ms() + 1000;
  if (ctl_open(sock)) LOGW("control socket unavailable: GbGpu and gb_gpu_compute cannot reach gb_power");
  char want[PROP_VALUE_MAX];
  prop_get("persist.vendor.gb.perf_profile", want);
  g_profile_done = profile_apply(want) == 0;
  if (!g_profile_done) g_profile_due = now_ms() + 2000;
  g_bat_due = -1;
  pkgs_refresh(1);
  status_tick(now_ms());
  for (;;) {
    if (g_term) { term_handle(); return 0; }
    loop_once(-1);
  }
}
