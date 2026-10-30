/* Unit tests for gb_power.c on a Linux host: battery math, uevent/DTX parsing, the DTX state machine and the latch
 * lifecycle against a mock EC (with an EC timeout model), the slow-path detach (refcnt gate, holders from fd and maps,
 * forked worker, deadline -> wedged), boot-disk guard, staged rescan, watchdog, state derivation, the control socket
 * (roles, golden transcripts, limits), the notification queue and --dtx-monitor, all against a fake sysfs/procfs tree
 * and a fake clock. Worker jobs really fork; the fake kernel (mock_op) runs in the child and edits the fake tree.
 * Build + run: cc -O2 -Wall -Wextra -Wno-format-truncation -Iuapi -o test_gb_power test_gb_power.c -lm
 *              && ./test_gb_power <empty work dir>          (build.sh also runs it under ASan+UBSan) */
#define main gb_power_main
#include "../gb_power.c"
#undef main

#include <math.h>

static int fails, checks;
#define CHECK(c, ...) do { checks++; if (!(c)) { fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static char W[PATH_MAX];   /* work dir */
static long long T = 1000000;                     /* fake CLOCK_MONOTONIC (ms) */
static long long fake_now(void) { return T; }

static void mkdirs(const char *p) {
  char b[PATH_MAX];
  snprintf(b, sizeof b, "%s", p);
  for (char *s = b + 1; *s; s++) if (*s == '/') { *s = 0; mkdir(b, 0755); *s = '/'; }
  mkdir(b, 0755);
}
static void put(const char *rel, const char *content) {
  char p[PATH_MAX], d[PATH_MAX];
  snprintf(p, sizeof p, "%s%s", g_root, rel);
  snprintf(d, sizeof d, "%s", p);
  *strrchr(d, '/') = 0;
  mkdirs(d);
  FILE *f = fopen(p, "w");
  if (!f) { perror(p); exit(2); }
  fputs(content, f);
  fclose(f);
}
static const char *get(const char *rel) {
  static char b[4096];
  char p[PATH_MAX];
  snprintf(p, sizeof p, "%s%s", g_root, rel);
  if (read_text(p, b, sizeof b) < 0) strcpy(b, "<missing>");
  return b;
}
static void lnk(const char *target, const char *rel) {
  char p[PATH_MAX], d[PATH_MAX];
  snprintf(p, sizeof p, "%s%s", g_root, rel);
  snprintf(d, sizeof d, "%s", p);
  *strrchr(d, '/') = 0;
  mkdirs(d);
  unlink(p);
  if (symlink(target, p)) { perror(p); exit(2); }
}
static void rm(const char *rel) { char p[PATH_MAX]; snprintf(p, sizeof p, "%s%s", g_root, rel); unlink(p); }
static int exists(const char *rel) { char p[PATH_MAX]; snprintf(p, sizeof p, "%s%s", g_root, rel); return !access(p, F_OK); }
static int mode_of(const char *rel) {
  char p[PATH_MAX];
  struct stat st;
  snprintf(p, sizeof p, "%s%s", g_root, rel);
  return stat(p, &st) ? -1 : (int)(st.st_mode & 07777);
}

static pid_t g_stuck[8];                          /* hung worker children (the daemon leaves them alone; tests kill) */
static int g_nstuck;
static void reset_daemon(void) {                  /* per-boot daemon state back to a fresh start */
  if (g_wk.busy && g_wk.pid > 0) { kill(g_wk.pid, SIGKILL); waitpid(g_wk.pid, NULL, 0); }
  if (g_wk.fd >= 0) close(g_wk.fd);
  memset(&g_wk, 0, sizeof g_wk);
  g_wk.fd = -1;
  for (int i = 0; i < g_nstuck; i++) { kill(g_stuck[i], SIGKILL); waitpid(g_stuck[i], NULL, 0); }
  g_nstuck = 0;
  while (waitpid(-1, NULL, WNOHANG) > 0) {}
  for (int i = 0; i < g_ncl; i++) { close(g_cl[i].fd); cl_stage_free(&g_cl[i]); }
  g_ncl = 0;
  if (g_listen >= 0) { close(g_listen); g_listen = -1; }
  if (g_dtx.fd >= 0) close(g_dtx.fd);
  memset(&g_dtx, 0, sizeof g_dtx);
  g_dtx.fd = -1;
  g_dtx.base = SDTX_BASE_ATTACHED;
  g_dtx.mode = MODE_LAPTOP;
  g_dtx.inhibited = -1;
  g_dtx.requery_at = g_dtx.retry_at = -1;
  g_latch = -1;
  memset(&g_inv, 0, sizeof g_inv);
  g_inv.vga = -1;
  g_port[0] = 0;
  g_pcn = 0;
  g_used = g_wedged = g_removed = g_shutdown = g_cmd_kill = g_cmd_test = g_cmd_once = 0;
  g_fault[0] = g_boot[0] = 0;
  memset(&g_dec, 0, sizeof g_dec);
  memset(&g_rs, 0, sizeof g_rs);
  memset(&g_cmp, 0, sizeof g_cmp);
  g_unbind_pending = g_npn = 0;
  g_detflag = -1;
  g_bootdisk = 0;
  gfx_state_reset();                              /* every v1.2 graphics state */
  g_trace = NULL;
  g_submit_hook = NULL;
  g_mark_op = real_mark_op;
  g_owner_root = real_owner_root;
  g_ctl_log[0] = 0;
  holders_clear();
  g_d0_since = 0;
  g_refcnt = -1;
  g_nqn = 0;
  g_state[0] = g_sig_last[0] = g_susp[0] = 0;
  g_bcast_pending = 0;
  g_bcast_last = g_tick_last = -100000;
  memset(g_pub, 0, sizeof g_pub);
  memset(g_props, 0, sizeof g_props);
  memset(&g_pkgs, 0, sizeof g_pkgs);
  memset(&g_allow, 0, sizeof g_allow);
  snprintf(g_keep_drv, sizeof g_keep_drv, "nouveau");
  g_keep_cli = 0;
  g_dgpu_on = g_dtx_on = 1;
  g_tick_due = 0;
  g_dgpu_due = g_dgpu_report_due = g_bat_due = -1;
  g_last_bat = T;
  g_start = T - 200000;
  g_iommu_dmafq = g_iommu_done = 0;
  g_term = 0;
  memset(&g_esp_rel, 0, sizeof g_esp_rel);        /* wave 2b: a deferred ESP lock release, the steal race hook */
  g_esp_hook = NULL;
}
static void set_root(const char *name) {
  snprintf(g_root, sizeof g_root, "%s/%s", W, name);
  char cmd[PATH_MAX + 16];
  snprintf(cmd, sizeof cmd, "rm -rf '%s'", g_root);
  if (system(cmd)) {}
  mkdirs(g_root);
  memset(&g_tp, 0, sizeof g_tp);
  memset(g_psy_cache, 0, sizeof g_psy_cache);
  g_hidden = 0;
  reset_daemon();
}

/* ---- sample uevents (surface_battery, mW units = energy_*; values like an SB3) ---- */
static char *bat(char *b, const char *name, const char *status, long long e_now, long long e_full, long long pw,
                 long long v_now, long long v_design, const char *extra) {
  sprintf(b, "POWER_SUPPLY_NAME=%s\nPOWER_SUPPLY_TYPE=Battery\nPOWER_SUPPLY_STATUS=%s\nPOWER_SUPPLY_PRESENT=1\n"
             "POWER_SUPPLY_TECHNOLOGY=Li-ion\nPOWER_SUPPLY_CYCLE_COUNT=87\nPOWER_SUPPLY_VOLTAGE_MIN_DESIGN=%lld\n"
             "POWER_SUPPLY_VOLTAGE_NOW=%lld\nPOWER_SUPPLY_POWER_NOW=%lld\nPOWER_SUPPLY_ENERGY_FULL_DESIGN=%lld\n"
             "POWER_SUPPLY_ENERGY_FULL=%lld\nPOWER_SUPPLY_ENERGY_NOW=%lld\nPOWER_SUPPLY_CAPACITY=%lld\n"
             "POWER_SUPPLY_CAPACITY_LEVEL=Normal\nPOWER_SUPPLY_MODEL_NAME=M1087082\nPOWER_SUPPLY_MANUFACTURER=SMP\n%s",
          name, status, v_design, v_now, pw, e_full + 1000000, e_full, e_now, e_now * 100 / e_full, extra ? extra : "");
  return b;
}
static const char *ADP_ON = "POWER_SUPPLY_NAME=ADP1\nPOWER_SUPPLY_TYPE=Mains\nPOWER_SUPPLY_ONLINE=1\n";
static const char *ADP_OFF = "POWER_SUPPLY_NAME=ADP1\nPOWER_SUPPLY_TYPE=Mains\nPOWER_SUPPLY_ONLINE=0\n";

static int mk(struct psy *ps, int n, const char *txt, const char *driver) {
  psy_init(&ps[n]);
  psy_parse(txt, &ps[n]);
  snprintf(ps[n].driver, sizeof ps[n].driver, "%s", driver ? driver : "");
  return n + 1;
}
static int hal_level(const struct combo *c) {   /* what the desktop HAL derives (disassembled formula) */
  double real = (c->q_now > 0 && c->q_full > 0) ? c->q_now * 100.0 / c->q_full : c->capacity;
  int l = (int)floor(real / 100.0 * 100.0 + 0.05);
  return l < 0 ? 0 : l > 100 ? 100 : l;
}

static void test_parse(void) {
  char b[2048];
  struct psy p;
  psy_init(&p);
  int k = psy_parse(bat(b, "BAT1", "Discharging", 8750000, 17500000, 6000000, 7700000, 7600000, "POWER_SUPPLY_SCOPE=Device\n"), &p);
  CHECK(k >= 15, "keys %d", k);
  CHECK(!strcmp(p.name, "BAT1") && p.type == T_BATTERY && p.status == S_DISCHARGING && p.present == 1, "basic");
  CHECK(p.e_now == 8750000 && p.e_full == 17500000 && p.e_design == 18500000, "energy %lld %lld %lld", p.e_now, p.e_full, p.e_design);
  CHECK(p.p_now == 6000000 && p.v_now == 7700000 && p.v_design == 7600000 && p.cycle == 87, "power/voltage");
  CHECK(p.scope == SC_DEVICE && !strcmp(p.tech, "Li-ion") && p.capacity == 50, "scope/tech/cap");
  CHECK(p.q_now == -1 && p.i_now == -1, "absent props stay -1");
  psy_init(&p);
  psy_parse("POWER_SUPPLY_NAME=BAT2\nPOWER_SUPPLY_TYPE=Battery\nPOWER_SUPPLY_PRESENT=0\n", &p);
  CHECK(p.present == 0 && p.e_now == -1 && p.status == S_UNKNOWN, "absent battery");
  psy_init(&p);
  psy_parse("POWER_SUPPLY_NAME=ADP1\nPOWER_SUPPLY_TYPE=Mains\nPOWER_SUPPLY_ONLINE=1", &p);   /* no trailing \n */
  CHECK(p.type == T_MAINS && p.online == 1, "mains");
  psy_init(&p);
  psy_parse("POWER_SUPPLY_ENERGY_NOW=abc\nPOWER_SUPPLY_CAPACITY=140\nPOWER_SUPPLY_STATUS=Not charging\nGARBAGE\n", &p);
  CHECK(p.e_now == -1 && p.capacity == -1 && p.status == S_NOT_CHARGING, "invalid numbers ignored");
  CHECK(word_in("low-power balanced balanced-performance performance", "balanced") &&
        word_in("low-power balanced balanced-performance performance", "performance") &&
        !word_in("low-power balanced-performance", "balanced") && !word_in("", "x"), "word_in");
}

static void test_combine(void) {
  char b1[2048], b2[2048];
  struct psy ps[8];
  struct combo c;
  int n;

  /* 1. AC, clipboard full, base charging at 25 W */
  n = 0;
  n = mk(ps, n, bat(b1, "BAT1", "Full", 17500000, 17500000, 0, 8700000, 7600000, NULL), "surface_battery");
  n = mk(ps, n, bat(b2, "BAT2", "Charging", 30000000, 58000000, 25000000, 12300000, 11400000, NULL), "surface_battery");
  n = mk(ps, n, ADP_ON, NULL);
  combine(ps, n, &c);
  CHECK(c.nbat == 2 && c.present && c.ac == 1 && c.status == S_CHARGING, "1 status %s ac %d", status_name(c.status), c.ac);
  CHECK(c.capacity == 62, "1 cap %d (47.5/75.5 Wh = 62.9%%)", c.capacity);
  CHECK(hal_level(&c) == c.capacity, "1 HAL level %d vs %d", hal_level(&c), c.capacity);
  double vconv = (7.6 * 17500 + 11.4 * 58000) / 75500.0;
  CHECK(fabs(c.v_conv / 1e6 - vconv) < 0.01, "1 vconv %lld vs %.3f", c.v_conv, vconv);
  CHECK(c.i_ua > 0 && fabs(c.i_ua - 25.0 / vconv * 1e6) < 2000, "1 current %lld", c.i_ua);
  double ttf_h = (double)(c.q_full - c.q_now) / c.i_ua, want = (75.5 - 47.5) / 25.0;
  CHECK(fabs(ttf_h - want) / want < 0.01, "1 HAL time-to-full %.3f h vs %.3f h", ttf_h, want);
  CHECK(!strcmp(c.tech, "LION") && c.cycle == 87, "1 tech %s", c.tech);
  CHECK(c.v_uv > 8700000 && c.v_uv < 12300000, "1 voltage %lld", c.v_uv);

  /* 2. unplugged: base discharging 15 W, part of it charging the clipboard at 5 W -> net -10 W */
  n = 0;
  n = mk(ps, n, bat(b1, "BAT1", "Charging", 10000000, 17500000, 5000000, 8200000, 7600000, NULL), "surface_battery");
  n = mk(ps, n, bat(b2, "BAT2", "Discharging", 40000000, 58000000, 15000000, 11800000, 11400000, NULL), "surface_battery");
  n = mk(ps, n, ADP_OFF, NULL);
  combine(ps, n, &c);
  CHECK(c.ac == 0 && c.status == S_DISCHARGING, "2 status %s", status_name(c.status));
  CHECK(c.p_net == -10000000 && c.i_ua < 0 && fabs(c.i_ua + 10.0 / vconv * 1e6) < 2000, "2 current %lld net %lld", c.i_ua, c.p_net);
  CHECK(c.capacity == 66, "2 cap %d (50/75.5)", c.capacity);

  /* 3. base detached: clipboard only */
  n = 0;
  n = mk(ps, n, bat(b1, "BAT1", "Discharging", 8750000, 17500000, 6000000, 7700000, 7600000, NULL), "surface_battery");
  n = mk(ps, n, ADP_OFF, NULL);
  combine(ps, n, &c);
  CHECK(c.nbat == 1 && c.capacity == 50 && c.status == S_DISCHARGING && c.v_conv == 7600000, "3 cap %d", c.capacity);
  CHECK(c.i_ua == -6000000LL * 1000000 / 7600000, "3 current %lld", c.i_ua);

  /* 4. both full on AC */
  n = 0;
  n = mk(ps, n, bat(b1, "BAT1", "Full", 17500000, 17500000, 0, 8700000, 7600000, NULL), "surface_battery");
  n = mk(ps, n, bat(b2, "BAT2", "Full", 57990000, 58000000, 0, 12500000, 11400000, NULL), "surface_battery");
  n = mk(ps, n, ADP_ON, NULL);
  combine(ps, n, &c);
  CHECK(c.status == S_FULL && c.capacity == 100 && c.q_now == c.q_full && c.i_ua == 0, "4 full %s %d", status_name(c.status), c.capacity);

  /* 5. mA units (charge_*), clipboard only, discharging 0.8 A at 7.8 V */
  n = 0;
  n = mk(ps, n, "POWER_SUPPLY_NAME=BAT1\nPOWER_SUPPLY_TYPE=Battery\nPOWER_SUPPLY_STATUS=Discharging\nPOWER_SUPPLY_PRESENT=1\n"
                "POWER_SUPPLY_VOLTAGE_MIN_DESIGN=7600000\nPOWER_SUPPLY_VOLTAGE_NOW=7800000\nPOWER_SUPPLY_CURRENT_NOW=800000\n"
                "POWER_SUPPLY_CHARGE_FULL_DESIGN=2400000\nPOWER_SUPPLY_CHARGE_FULL=2300000\nPOWER_SUPPLY_CHARGE_NOW=1150000\n",
         "surface_battery");
  n = mk(ps, n, ADP_OFF, NULL);
  combine(ps, n, &c);
  CHECK(c.capacity == 50 && c.status == S_DISCHARGING, "5 cap %d", c.capacity);
  CHECK(c.q_full == 2300000 && c.q_now == 1150000, "5 uAh round trip %lld %lld", c.q_full, c.q_now);
  CHECK(llabs(c.i_ua + 821052) < 10, "5 current %lld (0.8 A * 7.8 V / 7.6 V)", c.i_ua);

  /* 6. no batteries */
  n = 0;
  n = mk(ps, n, ADP_ON, NULL);
  combine(ps, n, &c);
  CHECK(!c.present && !c.nbat && c.ac == 1, "6 none");
  n = 0;
  combine(ps, n, &c);
  CHECK(!c.present && c.ac == 1, "6b nothing at all -> mains");

  /* 7. BAT2 registered but absent */
  n = 0;
  n = mk(ps, n, bat(b1, "BAT1", "Discharging", 8750000, 17500000, 6000000, 7700000, 7600000, NULL), "surface_battery");
  n = mk(ps, n, "POWER_SUPPLY_NAME=BAT2\nPOWER_SUPPLY_TYPE=Battery\nPOWER_SUPPLY_PRESENT=0\n", "surface_battery");
  combine(ps, n, &c);
  CHECK(c.nreal == 2 && c.nbat == 1 && c.capacity == 50, "7 absent pack");

  /* 8. plugged, charge limit: both not charging */
  n = 0;
  n = mk(ps, n, bat(b1, "BAT1", "Not charging", 8750000, 17500000, 0, 8000000, 7600000, NULL), "surface_battery");
  n = mk(ps, n, bat(b2, "BAT2", "Not charging", 29000000, 58000000, 0, 11500000, 11400000, NULL), "surface_battery");
  n = mk(ps, n, ADP_ON, NULL);
  combine(ps, n, &c);
  CHECK(c.status == S_NOT_CHARGING && c.capacity == 50 && c.i_ua == 0, "8 %s %d", status_name(c.status), c.capacity);

  /* 9. pen battery (HID, scope Device) stays out; surface_battery with scope Device (patch 0001) stays in */
  n = 0;
  n = mk(ps, n, bat(b1, "BAT1", "Discharging", 8750000, 17500000, 6000000, 7700000, 7600000, "POWER_SUPPLY_SCOPE=Device\n"), "surface_battery");
  n = mk(ps, n, "POWER_SUPPLY_NAME=hid-0005:045E:0921.0003-battery\nPOWER_SUPPLY_TYPE=Battery\nPOWER_SUPPLY_SCOPE=Device\n"
                "POWER_SUPPLY_PRESENT=1\nPOWER_SUPPLY_CAPACITY=5\nPOWER_SUPPLY_STATUS=Discharging\n", "hid-generic");
  combine(ps, n, &c);
  CHECK(c.nreal == 1 && c.capacity == 50, "9 pen excluded: nreal %d cap %d", c.nreal, c.capacity);

  /* 10. plugged but load exceeds the adapter: net negative -> not charging, negative current */
  n = 0;
  n = mk(ps, n, bat(b1, "BAT1", "Discharging", 12000000, 17500000, 20000000, 8000000, 7600000, NULL), "surface_battery");
  n = mk(ps, n, bat(b2, "BAT2", "Charging", 40000000, 58000000, 5000000, 12000000, 11400000, NULL), "surface_battery");
  n = mk(ps, n, ADP_ON, NULL);
  combine(ps, n, &c);
  CHECK(c.status == S_NOT_CHARGING && c.i_ua < 0, "10 %s %lld", status_name(c.status), c.i_ua);

  /* 11. no charger supply registered: AC inferred from a charging pack */
  n = 0;
  n = mk(ps, n, bat(b1, "BAT1", "Charging", 8750000, 17500000, 10000000, 8300000, 7600000, NULL), "surface_battery");
  combine(ps, n, &c);
  CHECK(c.ac == 1 && c.status == S_CHARGING, "11 inferred ac");
  /* 11b. no charger supply, unplugged: the base charges the clipboard -> not AC */
  n = 0;
  n = mk(ps, n, bat(b1, "BAT1", "Charging", 10000000, 17500000, 5000000, 8200000, 7600000, NULL), "surface_battery");
  n = mk(ps, n, bat(b2, "BAT2", "Discharging", 40000000, 58000000, 15000000, 11800000, 11400000, NULL), "surface_battery");
  combine(ps, n, &c);
  CHECK(c.ac == 0 && c.status == S_DISCHARGING && c.i_ua < 0, "11b inferred battery power: ac %d %s", c.ac, status_name(c.status));

  /* 12. rounding: 57.96 % -> 58, 57.94 % -> 57 (HAL: floor(x + 0.05)) */
  n = 0;
  n = mk(ps, n, bat(b1, "BAT1", "Discharging", 5796000, 10000000, 1000000, 7700000, 7600000, NULL), "surface_battery");
  combine(ps, n, &c);
  CHECK(c.capacity == 58 && hal_level(&c) == 58, "12a %d", c.capacity);
  n = 0;
  n = mk(ps, n, bat(b1, "BAT1", "Discharging", 5794000, 10000000, 1000000, 7700000, 7600000, NULL), "surface_battery");
  combine(ps, n, &c);
  CHECK(c.capacity == 57 && hal_level(&c) == 57, "12b %d", c.capacity);

  /* 13. capacity-only pack is weighted as 50 Wh; 14. charging with unknown rate -> charging, current 0 */
  n = 0;
  n = mk(ps, n, "POWER_SUPPLY_NAME=BAT9\nPOWER_SUPPLY_TYPE=Battery\nPOWER_SUPPLY_STATUS=Charging\nPOWER_SUPPLY_PRESENT=1\n"
                "POWER_SUPPLY_CAPACITY=40\n", "");
  n = mk(ps, n, ADP_ON, NULL);
  combine(ps, n, &c);
  CHECK(c.capacity == 40 && c.status == S_CHARGING && c.i_ua == 0 && !c.p_known, "13/14 cap %d %s", c.capacity, status_name(c.status));

  /* 15. remaining > full (stale full) is clamped */
  n = 0;
  n = mk(ps, n, bat(b1, "BAT1", "Discharging", 18000000, 17500000, 3000000, 8700000, 7600000, NULL), "surface_battery");
  combine(ps, n, &c);
  CHECK(c.capacity == 100, "15 clamp %d", c.capacity);

  /* 16. test_power's own supplies never count (test_ac online must not mask ADP1 offline) */
  n = 0;
  n = mk(ps, n, bat(b1, "BAT1", "Discharging", 8750000, 17500000, 6000000, 7700000, 7600000, NULL), "surface_battery");
  n = mk(ps, n, ADP_OFF, NULL);
  n = mk(ps, n, "POWER_SUPPLY_NAME=test_ac\nPOWER_SUPPLY_TYPE=Mains\nPOWER_SUPPLY_ONLINE=1\n", NULL);
  n = mk(ps, n, "POWER_SUPPLY_NAME=test_battery\nPOWER_SUPPLY_TYPE=Battery\nPOWER_SUPPLY_PRESENT=1\nPOWER_SUPPLY_CAPACITY=50\n", NULL);
  combine(ps, n, &c);
  CHECK(c.ac == 0 && c.nreal == 1, "16 test_* ignored ac=%d nreal=%d", c.ac, c.nreal);
}

/* ---- fake sysfs: power supplies + test_power ---- */
static void fake_psy(const char *name, const char *uevent, const char *type, const char *driver) {
  char p[256];
  snprintf(p, sizeof p, "/sys/class/power_supply/%s/uevent", name);
  put(p, uevent);
  snprintf(p, sizeof p, "/sys/class/power_supply/%s/type", name);
  put(p, type);
  if (driver) {
    char t[128];
    snprintf(p, sizeof p, "/sys/class/power_supply/%s/device/driver", name);
    snprintf(t, sizeof t, "../../../../bus/surface_aggregator/drivers/%s", driver);
    lnk(t, p);
  }
}
static void fake_tp(int full) {
  const char *params[] = {"ac_online", "usb_online", "battery_status", "battery_health", "battery_present",
                          "battery_technology", "battery_capacity", "battery_voltage", "battery_charge_counter",
                          "battery_current", "battery_extension"};
  char p[160];
  for (unsigned i = 0; i < sizeof params / sizeof *params; i++) {
    snprintf(p, sizeof p, TP "%s", params[i]);
    put(p, "x\n");
  }
  if (full) { put(TP "battery_charge_full", "100\n"); put(TP "battery_charge_full_design", "100\n"); put(TP "battery_temp", "26\n"); }
  fake_psy("test_battery", "POWER_SUPPLY_NAME=test_battery\nPOWER_SUPPLY_TYPE=Battery\n", "Battery\n", NULL);
  fake_psy("test_ac", "POWER_SUPPLY_NAME=test_ac\nPOWER_SUPPLY_TYPE=Mains\nPOWER_SUPPLY_ONLINE=1\n", "Mains\n", NULL);
}

static void test_publish(void) {
  char b[2048];
  set_root("tree_full");
  fake_tp(1);
  fake_psy("BAT1", bat(b, "BAT1", "Full", 17500000, 17500000, 0, 8700000, 7600000, NULL), "Battery\n", "surface_battery");
  fake_psy("BAT2", bat(b, "BAT2", "Charging", 30000000, 58000000, 25000000, 12300000, 11400000, "POWER_SUPPLY_SCOPE=Device\n"),
           "Battery\n", "surface_battery");
  fake_psy("ADP1", ADP_ON, "Mains\n", "surface_charger");
  put("/sys/module/surface_battery/parameters/scope_device", "N\n");
  CHECK(tp_probe() == 1 && g_tp.ok && g_tp.full && g_tp.temp, "probe");
  CHECK(!strcmp(get(TP "usb_online"), "off") && !strcmp(get(TP "battery_temp"), "250"), "defaults");
  battery_update(1);
  CHECK(!strcmp(get(TP "battery_status"), "charging"), "status %s", get(TP "battery_status"));
  CHECK(!strcmp(get(TP "battery_capacity"), "62"), "capacity %s", get(TP "battery_capacity"));
  CHECK(!strcmp(get(TP "battery_present"), "true") && !strcmp(get(TP "ac_online"), "on"), "present/ac");
  CHECK(!strcmp(get(TP "battery_technology"), "LION") && !strcmp(get(TP "battery_health"), "good"), "tech/health");
  long long full = atoll(get(TP "battery_charge_full")), cnt = atoll(get(TP "battery_charge_counter")),
            cur = atoll(get(TP "battery_current")), design = atoll(get(TP "battery_charge_full_design")),
            volt = atoll(get(TP "battery_voltage"));
  CHECK(full > 7000000 && full < 7300000 && cnt * 100 / full == 62 && design > full, "uAh full %lld cnt %lld design %lld", full, cnt, design);
  CHECK(cur > 2300000 && cur < 2450000 && cur % 1000 == 0, "uA current %lld", cur);
  CHECK(volt > 11000000 && volt < 12300000 && volt % 1000 == 0, "uV voltage %lld", volt);
  /* hiding: BAT1 has no scope -> hidden, BAT2 scope=Device -> left alone */
  CHECK(!strcmp(get("/sys/class/power_supply/BAT1/type"), "Unknown") && g_hidden == 1, "BAT1 hidden");
  CHECK(!strcmp(get("/sys/class/power_supply/BAT2/type"), "Battery"), "BAT2 not hidden (scope Device)");
  CHECK(!strcmp(get("/sys/module/surface_battery/parameters/scope_device"), "Y"), "scope_device on with test_power");
  /* idempotent: nothing rewritten when nothing changed; hiding not repeated */
  int w = g_tp.writes;
  battery_update(0);
  CHECK(g_tp.writes == w && g_hidden == 1, "no rewrite (%d -> %d)", w, g_tp.writes);
  /* sentinel: unchanged values are not written again */
  put(TP "battery_capacity", "sentinel\n");
  battery_update(0);
  CHECK(!strcmp(get(TP "battery_capacity"), "sentinel"), "cached value not rewritten");
  put(TP "battery_capacity", "62\n");
  /* transient read failure: cached parse keeps the pack in the sum */
  char p[PATH_MAX];
  rpath(p, sizeof p, "/sys/class/power_supply/BAT2/uevent");
  chmod(p, 0);
  if (access(p, R_OK)) {                          /* (skipped when the tests run as root) */
    battery_update(0);
    CHECK(!strcmp(get(TP "battery_capacity"), "62"), "cache on read error: %s", get(TP "battery_capacity"));
  }
  chmod(p, 0644);
  /* unplug: ADP1 offline, base discharging */
  fake_psy("ADP1", ADP_OFF, "Mains\n", "surface_charger");
  fake_psy("BAT2", bat(b, "BAT2", "Discharging", 30000000, 58000000, 12000000, 11600000, 11400000, "POWER_SUPPLY_SCOPE=Device\n"),
           "Battery\n", "surface_battery");
  fake_psy("BAT1", bat(b, "BAT1", "Discharging", 17500000, 17500000, 2000000, 8500000, 7600000, NULL), "Unknown\n", "surface_battery");
  battery_update(0);
  CHECK(!strcmp(get(TP "battery_status"), "discharging") && !strcmp(get(TP "ac_online"), "off"), "unplugged %s", get(TP "battery_status"));
  CHECK(atoll(get(TP "battery_current")) < -1000000, "discharge current %s", get(TP "battery_current"));
  /* base detached: BAT2 disappears */
  char cmd[PATH_MAX + 32];
  snprintf(cmd, sizeof cmd, "rm -rf '%s/sys/class/power_supply/BAT2'", g_root);
  if (system(cmd)) {}
  battery_update(0);
  CHECK(!strcmp(get(TP "battery_capacity"), "100"), "detached: clipboard only %s", get(TP "battery_capacity"));
  /* all batteries gone */
  snprintf(cmd, sizeof cmd, "rm -rf '%s/sys/class/power_supply/BAT1'", g_root);
  if (system(cmd)) {}
  battery_update(0);
  CHECK(!strcmp(get(TP "battery_present"), "false") && !strcmp(get(TP "battery_charge_counter"), "-1"), "none present");

  /* legacy test_power (no charge_full param): counter -1, current 0, capacity carries the level */
  set_root("tree_legacy");
  fake_tp(0);
  fake_psy("BAT1", bat(b, "BAT1", "Charging", 8750000, 17500000, 20000000, 8300000, 7600000, NULL), "Battery\n", "surface_battery");
  fake_psy("ADP1", ADP_ON, "Mains\n", "surface_charger");
  tp_probe();
  CHECK(g_tp.ok && !g_tp.full, "legacy probe");
  battery_update(1);
  CHECK(!strcmp(get(TP "battery_charge_counter"), "-1") && !strcmp(get(TP "battery_current"), "0") &&
        !strcmp(get(TP "battery_capacity"), "50") && !strcmp(get(TP "battery_status"), "charging"), "legacy values");
  CHECK(!strcmp(get(TP "battery_charge_full"), "<missing>"), "legacy: no charge_full written");

  /* -H: hiding off */
  set_root("tree_nohide");
  fake_tp(1);
  fake_psy("BAT1", bat(b, "BAT1", "Charging", 8750000, 17500000, 20000000, 8300000, 7600000, NULL), "Battery\n", "surface_battery");
  g_hide = 0;
  tp_probe();
  battery_update(1);
  CHECK(!strcmp(get("/sys/class/power_supply/BAT1/type"), "Battery") && g_hidden == 0, "no hide with -H");
  g_hide = 1;

  /* no test_power at all: nothing written, no crash */
  set_root("tree_notp");
  fake_psy("BAT1", bat(b, "BAT1", "Charging", 8750000, 17500000, 20000000, 8300000, 7600000, NULL), "Battery\n", "surface_battery");
  put("/sys/module/surface_battery/parameters/scope_device", "Y\n");
  CHECK(tp_probe() == 0, "no test_power");
  battery_update(1);
  CHECK(g_tp.writes == 0, "nothing written");
  CHECK(!strcmp(get("/sys/class/power_supply/BAT1/type"), "Battery") && g_hidden == 0, "no hiding without test_power");
  CHECK(!strcmp(get("/sys/module/surface_battery/parameters/scope_device"), "N"), "scope_device off without test_power");
}

/* ---- uevent netlink message parsing ---- */
static void test_uevent(void) {
  char m[512];
  int k = 0;
#define ADD(s) do { memcpy(m + k, s, strlen(s) + 1); k += strlen(s) + 1; } while (0)
  ADD("change@/devices/platform/MSHW0084:00/serial1-0/ssam:01:02:01:01:00/power_supply/BAT1");
  ADD("ACTION=change");
  ADD("DEVPATH=/devices/platform/MSHW0084:00/serial1-0/ssam:01:02:01:01:00/power_supply/BAT1");
  ADD("SUBSYSTEM=power_supply");
  ADD("POWER_SUPPLY_NAME=BAT1");
  ADD("SEQNUM=1234");
  struct uev u;
  uev_parse(m, k, &u);
  CHECK(u.action && !strcmp(u.action, "change") && !strcmp(u.subsystem, "power_supply") && !strcmp(u.psy_name, "BAT1"), "uevent parse");
  g_bat_due = -1;
  uev_handle(&u);
  CHECK(g_bat_due > 0, "BAT1 change schedules an update");
  k = 0;
  ADD("change@/devices/virtual/power_supply/test_battery");
  ADD("ACTION=change");
  ADD("SUBSYSTEM=power_supply");
  ADD("POWER_SUPPLY_NAME=test_battery");
  uev_parse(m, k, &u);
  g_bat_due = -1;
  uev_handle(&u);
  CHECK(g_bat_due == -1, "own test_battery change ignored (no feedback loop)");
  k = 0;
  ADD("add@/devices/pci0000:00/0000:00:1d.4/0000:02:00.0");
  ADD("ACTION=add");
  ADD("DEVPATH=/devices/pci0000:00/0000:00:1d.4/0000:02:00.0");
  ADD("SUBSYSTEM=pci");
  ADD("PCI_ID=10DE:1F91");
  uev_parse(m, k, &u);
  g_dgpu_due = -1;
  uev_handle(&u);
  CHECK(g_dgpu_due > 0, "dGPU hot-add schedules a scan");
  k = 0;
  ADD("add@/devices/virtual/misc/surface_dtx");
  ADD("ACTION=add");
  ADD("SUBSYSTEM=misc");
  ADD("DEVNAME=surface/dtx");
  uev_parse(m, k, &u);
  g_dtx.retry_at = -1;
  int fd = g_dtx.fd;
  g_dtx.fd = -1;
  uev_handle(&u);
  CHECK(g_dtx.retry_at > 0, "DTX node add schedules open");
  g_dtx.fd = fd;
#undef ADD
}

/* ================================================================================================ DTX / dGPU */
/* ---- mock EC. model=1 adds the EC's behaviour (dtx.rst): CONFIRM opens the latch, CANCEL echoes a REQUEST, a press
 * during a process aborts it, and a process without a heartbeat for EC_TIMEOUT ms is canceled (latch locked) or
 * opens the latch (unlocked). Events the EC sends are queued and delivered outside the handlers. */
static struct {
  int base, latch, mode, locked, active, model;
  long long last_hb;
  char log[65536];
  int q[32][2], qn;
  long long hb_t[512];
  int nhb;
  long long rescan_t[8];
  int nrescan;
} ec;
#define EC_TIMEOUT 5000                           /* model only: the real EC timeout is unknown (HW check 2) */
static int g_bind_on_hb;
static int g_fail_confirm;                       /* LATCH_CONFIRM returns -EIO */
static void bind_hda_hook(void);
static void ec_log(const char *s) { size_t l = strlen(ec.log); snprintf(ec.log + l, sizeof ec.log - l, "%s ", s); }
static void ec_queue(int code, int v) { if (ec.qn < 32) { ec.q[ec.qn][0] = code; ec.q[ec.qn][1] = v; ec.qn++; } }
static void ec_reset(void) {
  memset(&ec, 0, sizeof ec);
  ec.base = SDTX_BASE_ATTACHED; ec.latch = SDTX_LATCH_CLOSED; ec.mode = MODE_LAPTOP;
}
static int mock_ioctl(int fd, unsigned long req, void *arg) {
  (void)fd;
  if (req == SDTX_IOCTL_GET_BASE_INFO) { struct sdtx_base_info *b = arg; b->state = ec.base; b->base_id = ec.base == 1 ? 0x205 : 0; return 0; }
  if (req == SDTX_IOCTL_GET_LATCH_STATUS) { *(uint16_t *)arg = ec.latch; return 0; }
  if (req == SDTX_IOCTL_GET_DEVICE_MODE) { *(uint16_t *)arg = ec.mode; return 0; }
  const char *n = req == SDTX_IOCTL_EVENTS_ENABLE ? "EV" : req == SDTX_IOCTL_LATCH_CONFIRM ? "CONFIRM" :
                  req == SDTX_IOCTL_LATCH_HEARTBEAT ? "HB" : req == SDTX_IOCTL_LATCH_CANCEL ? "CANCEL" :
                  req == SDTX_IOCTL_LATCH_REQUEST ? "REQ" : req == SDTX_IOCTL_LATCH_LOCK ? "LOCK" :
                  req == SDTX_IOCTL_LATCH_UNLOCK ? "UNLOCK" : NULL;
  if (!n) return -EINVAL;
  ec_log(n);
  if (req == SDTX_IOCTL_LATCH_CONFIRM && g_fail_confirm) return -EIO;
  if (req == SDTX_IOCTL_LATCH_LOCK) ec.locked = 1;
  else if (req == SDTX_IOCTL_LATCH_UNLOCK) ec.locked = 0;
  else if (req == SDTX_IOCTL_LATCH_HEARTBEAT) {
    ec.last_hb = T;
    if (ec.nhb < 512) ec.hb_t[ec.nhb++] = T;
    if (g_bind_on_hb) { g_bind_on_hb = 0; bind_hda_hook(); }   /* a probe racing the quick path */
  }
  if (ec.model) {
    if (req == SDTX_IOCTL_LATCH_CONFIRM && ec.active) {
      ec.active = 0; ec.locked = 0; ec.latch = SDTX_LATCH_OPENED;
      ec_queue(SDTX_EVENT_LATCH_STATUS, SDTX_LATCH_OPENED);
    } else if (req == SDTX_IOCTL_LATCH_CANCEL) {
      ec.active = 0;
      ec_queue(SDTX_EVENT_REQUEST, 0);            /* "a generic request event is sent in response" */
    } else if (req == SDTX_IOCTL_LATCH_REQUEST) {
      if (ec.active) ec.active = 0; else { ec.active = 1; ec.last_hb = T; }
      ec_queue(SDTX_EVENT_REQUEST, 0);
    }
  }
  return 0;
}
static void push(int code, const unsigned char *d, int len) {
  g_dtx.buf[g_dtx.blen] = len & 0xff; g_dtx.buf[g_dtx.blen + 1] = len >> 8;
  g_dtx.buf[g_dtx.blen + 2] = code & 0xff; g_dtx.buf[g_dtx.blen + 3] = code >> 8;
  if (len) memcpy(g_dtx.buf + g_dtx.blen + 4, d, len);
  g_dtx.blen += 4 + len;
}
static void ev_raw(int code, int v) { unsigned char d[2] = {v & 0xff, v >> 8}; push(code, d, code == SDTX_EVENT_REQUEST ? 0 : 2); dtx_parse(); }
static void ec_deliver(void) {
  for (int guard = 0; ec.qn && guard < 32; guard++) {
    int code = ec.q[0][0], v = ec.q[0][1];
    memmove(ec.q[0], ec.q[1], (ec.qn - 1) * sizeof ec.q[0]);
    ec.qn--;
    ev_raw(code, v);
  }
}
static void ev(int code, int v) { ev_raw(code, v); ec_deliver(); }
static void press(void) {                         /* the detach button */
  if (ec.model) { if (ec.active) ec.active = 0; else { ec.active = 1; ec.last_hb = T; } }
  ev(SDTX_EVENT_REQUEST, 0);
}
static void ec_model_tick(void) {
  if (!ec.model || !ec.active || T - ec.last_hb < EC_TIMEOUT) return;
  ec.active = 0;
  if (ec.locked) { ec_log("EC-TIMEOUT-CANCEL"); ec_queue(SDTX_EVENT_CANCEL, SDTX_DETACH_TIMEDOUT); }
  else { ec_log("EC-TIMEOUT-OPEN"); ec.latch = SDTX_LATCH_OPENED; ec_queue(SDTX_EVENT_LATCH_STATUS, SDTX_LATCH_OPENED); }
}
static const char *prop(const char *k) { static char v[PROP_VALUE_MAX]; prop_get(k, v); return v; }
static int logpos(const char *tag) { const char *p = strstr(ec.log, tag); return p ? (int)(p - ec.log) : -1; }
static int count_in(const char *h, const char *n) { int k = 0; for (const char *p = h; (p = strstr(p, n)); p += strlen(n)) k++; return k; }
static long long hb_max_gap(long long from, long long to) {   /* largest heartbeat gap inside [from, to] */
  long long last = from, gap = 0;
  for (int i = 0; i < ec.nhb; i++) {
    if (ec.hb_t[i] < from || ec.hb_t[i] > to) continue;
    if (ec.hb_t[i] - last > gap) gap = ec.hb_t[i] - last;
    last = ec.hb_t[i];
  }
  if (to - last > gap) gap = to - last;
  return gap;
}

/* ---- fake kernel for the worker ops (runs in the forked child; edits the fake tree, visible to the parent) */
static int k_unbind;                              /* 0 ok, 1 -EBUSY, 2 blocks on g_hang until released, 3 stays bound */
static int k_remove;                              /* 0 ok, 1 returns 0 but the device stays, 2 blocks on g_hang */
static int k_restore_at;                          /* the n-th rescan write re-creates both functions (0 = never) */
static int k_zero_bar;
static int k_refcnt_after_unbind;               /* a root opener races the detach unbind (refcnt -> 1 after it) */
static int g_hang[2] = {-1, -1};
#define PORT "/sys/devices/pci0000:00/0000:00:1d.4"
#define FN0 PORT "/0000:02:00.0"
#define FN1 PORT "/0000:02:00.1"
static void drm_gone(void) {
  rm("/sys/class/drm/renderD129"); rm("/sys/class/drm/card1"); rm("/dev/dri/renderD129"); rm("/dev/dri/card1");
}
static void fns_back(void) {
  rm(FN0 "/driver"); rm(FN1 "/driver");
  put(FN0 "/resource", k_zero_bar ? "0x0000000000000000 0x0000000000000000 0x0000000000000000\n"
                                  : "0x00000000e0000000 0x00000000e0ffffff 0x0000000000040200\n");
  lnk("../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.0", "/sys/bus/pci/devices/0000:02:00.0");
  lnk("../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.1", "/sys/bus/pci/devices/0000:02:00.1");
}
static int bump(const char *name) {
  char p[PATH_MAX], v[16];
  snprintf(p, sizeof p, "%s/%s", W, name);
  int n = read_text(p, v, sizeof v) > 0 ? atoi(v) + 1 : 1;
  snprintf(v, sizeof v, "%d\n", n);
  FILE *f = fopen(p, "w");
  if (f) { fputs(v, f); fclose(f); }
  return n;
}
static int mock_op(int kind, const char *path, const char *val) {
  char l[PATH_MAX], d2[PATH_MAX + 16], drv[48];
  if (kind == OP_UNBIND) {
    if (k_unbind == 2) { char ch; ssize_t r; do r = read(g_hang[0], &ch, 1); while (r < 0 && errno == EINTR); }
    if (k_unbind == 1) return -EBUSY;
    int r = write_text(path, val);
    if (r) return r;
    if (k_unbind == 3) return 0;
    snprintf(l, sizeof l, "%s", path);
    *strrchr(l, '/') = 0;                         /* <dev>/driver */
    link_base(l, drv, sizeof drv);
    if (!strcmp(drv, "nouveau")) drm_gone();
    int u = unlink(l) ? -errno : 0;
    if (k_refcnt_after_unbind && !strcmp(drv, "nouveau")) put("/sys/module/nouveau/refcnt", "1\n");
    return u;
  }
  if (kind == OP_REMOVE) {
    if (k_remove == 2) { char ch; ssize_t r; do r = read(g_hang[0], &ch, 1); while (r < 0 && errno == EINTR); }
    if (access(path, F_OK)) return -ENOENT;
    if (k_remove == 1) return 0;
    snprintf(l, sizeof l, "%s", path);
    *strrchr(l, '/') = 0;                         /* /sys/bus/pci/devices/<bdf> */
    snprintf(d2, sizeof d2, "%s/driver", l);
    link_base(d2, drv, sizeof drv);
    if (drv[0]) { if (!strcmp(drv, "nouveau")) drm_gone(); unlink(d2); }   /* remove implies unbind */
    return unlink(l) ? -errno : 0;
  }
  size_t pl = strlen(path);
  if (pl > 7 && !strcmp(path + pl - 7, "/rescan")) {
    int n = bump("rescans");
    if (k_restore_at && n >= k_restore_at) fns_back();
  }
  int r = write_text(path, val);
  if (!r && strstr(path, "1d.4/power/control") && !strcmp(val, "on")) put(PORT "/power_state", "D0\n");
  return r;
}
static void on_op(const struct wop *o) {          /* parent: op results into the EC log, in order */
  char tag[200];
  const char *k = o->kind == OP_UNBIND ? "UNBIND" : o->kind == OP_REMOVE ? "REMOVE" : "WRITE", *bad = op_ok(o) ? "" : "!";
  if (o->kind == OP_MARK || o->kind == OP_UNMARK) {   /* (v1.2) markers: MARK:gfx_session, UNMARK:nouveau.loading */
    const char *b = strrchr(o->path, '/');
    snprintf(tag, sizeof tag, "%s%s:%s", o->kind == OP_MARK ? "MARK" : "UNMARK", bad, b ? b + 1 : o->path);
  } else if (o->kind == OP_WRITE) {
    const char *a = strstr(o->path, o->bdf);
    a = a ? a + strlen(o->bdf) + 1 : o->path;
    snprintf(tag, sizeof tag, "%s%s:%s/%s=%s", k, bad, o->bdf, a, o->val);
    if (strstr(o->path, "/rescan") && ec.nrescan < 8) ec.rescan_t[ec.nrescan++] = T;
  } else {
    snprintf(tag, sizeof tag, "%s%s:%s", k, bad, o->bdf);
  }
  ec_log(tag);
}
static char g_chown_log[2048];
static int fake_chown(const char *p, uid_t u, gid_t g) {   /* non-root tests: record instead of chown */
  size_t l = strlen(g_chown_log);
  const char *b = strrchr(p, '/');
  snprintf(g_chown_log + l, sizeof g_chown_log - l, "%s:%u:%u ", b ? b + 1 : p, (unsigned)u, (unsigned)g);
  return access(p, F_OK) ? -ENOENT : 0;
}
static int fake_blkdev(const char *src, unsigned *ma, unsigned *mi) {
  static const struct { const char *n; unsigned a, b; } m[] = {
    {"/dev/block/sda1", 8, 1}, {"/dev/block/nvme0n1p5", 259, 5}, {"/dev/block/mmcblk0p1", 179, 1}, {"/dev/block/dm-0", 253, 0}};
  for (unsigned i = 0; i < sizeof m / sizeof m[0]; i++)
    if (!strcmp(src, m[i].n)) { *ma = m[i].a; *mi = m[i].b; return 0; }
  return -1;
}

/* ---- loop drivers (fake clock) */
static void serve(void);
static void worker_poll(void) {                   /* let a finished worker report (a hung one: give up quickly) */
  for (int k = 0; k < 40 && g_wk.busy && g_wk.fd >= 0; k++) {
    struct pollfd pf = {.fd = g_wk.fd, .events = POLLIN};
    if (poll(&pf, 1, 50) <= 0) break;
    worker_read();
  }
}
static void pump(long long ms, int step) {
  long long end = T + ms;
  while (T < end) {
    T += step;
    if (T > end) T = end;
    worker_poll();
    run_due(T);
    ec_model_tick();
    ec_deliver();
    serve();
    cl_sweep();
  }
}
static void settle(void) {                        /* run until the worker queue is empty */
  for (int k = 0; k < 60 && (g_wk.busy || g_wk.qn); k++) pump(100, 100);
  pump(300, 100);
}

/* ---- fake PCI tree: root port 00:1d.4, dGPU 02:00.0 (VGA/3D), HDA 02:00.1, iGPU 00:02.0 (i915, renderD128) */
static void bind_drv(const char *fn, const char *drv) {
  char t[128], u[128];
  snprintf(t, sizeof t, "../../../../bus/pci/drivers/%s", drv);
  snprintf(u, sizeof u, "/sys/bus/pci/drivers/%s/unbind", drv);
  put(u, "");
  char l[160];
  snprintf(l, sizeof l, "%s/driver", fn);
  lnk(t, l);
}
static void bind_nouveau(void) {
  bind_drv(FN0, "nouveau");
  put("/sys/module/nouveau/refcnt", "0\n");
  lnk("../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.0", "/sys/class/drm/renderD129/device");
  lnk("../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.0", "/sys/class/drm/card1/device");
  /* ueventd creates fresh nodes (new inode: made before the old one goes, then renamed over it), 0666 */
  char p[PATH_MAX], q[PATH_MAX];
  put("/dev/dri/.renderD129.new", ""); put("/dev/dri/.card1.new", "");
  chmod(rpath(p, sizeof p, "/dev/dri/.renderD129.new"), 0666);
  chmod(rpath(p, sizeof p, "/dev/dri/.card1.new"), 0666);
  if (rename(rpath(p, sizeof p, "/dev/dri/.renderD129.new"), rpath(q, sizeof q, "/dev/dri/renderD129"))) perror("rename");
  if (rename(rpath(p, sizeof p, "/dev/dri/.card1.new"), rpath(q, sizeof q, "/dev/dri/card1"))) perror("rename");
}
static void fake_pci(int nouveau, const char *hda_drv) {
  put(PORT "/vendor", "0x8086\n"); put(PORT "/class", "0x060400\n"); put(PORT "/power/control", "on\n");
  put(PORT "/power/runtime_status", "active\n"); put(PORT "/power_state", "D0\n"); put(PORT "/rescan", "");
  put(FN0 "/vendor", "0x10de\n"); put(FN0 "/device", "0x1f91\n"); put(FN0 "/class", "0x030200\n");
  put(FN0 "/power/control", "on\n"); put(FN0 "/power/runtime_status", "suspended\n"); put(FN0 "/power_state", "D3cold\n");
  put(FN0 "/d3cold_allowed", "1\n"); put(FN0 "/remove", "");
  put(FN0 "/resource", "0x00000000e0000000 0x00000000e0ffffff 0x0000000000040200\n");
  put(FN1 "/vendor", "0x10de\n"); put(FN1 "/device", "0x10fa\n"); put(FN1 "/class", "0x040300\n");
  put(FN1 "/power/control", "on\n"); put(FN1 "/power/runtime_status", "suspended\n"); put(FN1 "/power_state", "D3cold\n");
  put(FN1 "/d3cold_allowed", "0\n"); put(FN1 "/remove", "");
  put(FN1 "/resource", "0x00000000e1000000 0x00000000e1003fff 0x0000000000040200\n");
  put("/sys/devices/pci0000:00/0000:00:02.0/vendor", "0x8086\n");
  put("/sys/devices/pci0000:00/0000:00:02.0/class", "0x030000\n");
  put("/sys/devices/pci0000:00/0000:00:02.0/power/control", "on\n");
  lnk("../../../../bus/pci/drivers/i915", "/sys/devices/pci0000:00/0000:00:02.0/driver");
  lnk("../../../devices/pci0000:00/0000:00:1d.4", "/sys/bus/pci/devices/0000:00:1d.4");
  lnk("../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.0", "/sys/bus/pci/devices/0000:02:00.0");
  lnk("../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.1", "/sys/bus/pci/devices/0000:02:00.1");
  lnk("../../../devices/pci0000:00/0000:00:02.0", "/sys/bus/pci/devices/0000:00:02.0");
  lnk("../../../devices/pci0000:00/0000:00:02.0", "/sys/class/drm/renderD128/device");
  lnk("../../../devices/pci0000:00/0000:00:02.0", "/sys/class/drm/card0/device");
  put("/dev/dri/renderD128", "");
  char p[PATH_MAX];
  chmod(rpath(p, sizeof p, "/dev/dri/renderD128"), 0666);
  put("/proc/1/comm", "init\n");
  if (nouveau) bind_nouveau();
  if (hda_drv) bind_drv(FN1, hda_drv);
  g_op_write = mock_op;
  g_on_op = on_op;
  g_chown = fake_chown;
  g_chown_log[0] = 0;
  k_unbind = k_remove = k_restore_at = k_zero_bar = k_refcnt_after_unbind = 0;
  char c[PATH_MAX];
  snprintf(c, sizeof c, "%s/rescans", W);
  unlink(c);
}
static void bind_hda_hook(void) { bind_drv(FN1, "snd_hda_intel"); }
static int linked(const char *bdf) {
  char p[PATH_MAX], t[64];
  rpath(p, sizeof p, "/sys/bus/pci/devices/%s/driver", bdf);
  return readlink(p, t, sizeof t) > 0;
}
static void dtx_up(int model) {                   /* open the (mock) DTX device */
  put("/dev/surface/dtx", "");
  dtx_ioctl = mock_ioctl;
  ec_reset();
  ec.model = model;
  g_race_wait_ms = 20;
  CHECK(dtx_open() == 0 && g_dtx.fd >= 0, "dtx open");
}
static void notify_dir(void) { put("/data/vendor/gb/notify/.keep", ""); rm("/data/vendor/gb/notify/.keep"); prop_set("sys.boot_completed", "1"); }
/* a queued notification file containing s */
static int notified(const char *s) {
  char dir[PATH_MAX], p[PATH_MAX + 300], b[1024];
  DIR *d = opendir(rpath(dir, sizeof dir, "/data/vendor/gb/notify"));
  int hit = 0;
  if (!d) return 0;
  struct dirent *e;
  while (!hit && (e = readdir(d))) {
    if (e->d_name[0] == '.') continue;
    snprintf(p, sizeof p, "%s/%s", dir, e->d_name);
    int fd = open(p, O_RDONLY | O_CLOEXEC);       /* raw bytes: the trailing newline is part of the format */
    if (fd < 0) continue;
    ssize_t n = read(fd, b, sizeof b - 1);
    close(fd);
    if (n > 0) { b[n] = 0; if (strstr(b, s)) hit = 1; }
  }
  closedir(d);
  return hit;
}
static void pkgs(void) {
  put("/data/system/packages.list",
      "gb.sb3.gpu 10123 0 /data/user/0/gb.sb3.gpu platform:targetSdkVersion=36 none 0 1\n"
      "com.example.chat 10200 0 /data/user/0/com.example.chat default:targetSdkVersion=36 3003 0 1\n"
      "com.example.game 10300 0 /data/user/0/com.example.game default:targetSdkVersion=36 none 0 1\n"
      "com.example.other 10500 0 /data/user/0/com.example.other default:targetSdkVersion=36 none 0 1\n"
      "com.android.shell 2000 0 /data/user_de/0/com.android.shell platform:privapp:targetSdkVersion=36 1007,1011 0 36\n");
}
static void proc_holder(int pid, const char *comm, int uid, int by_fd, int by_maps) {
  char p[128], b[512];
  snprintf(p, sizeof p, "/proc/%d/comm", pid); snprintf(b, sizeof b, "%s\n", comm); put(p, b);
  snprintf(p, sizeof p, "/proc/%d/status", pid);
  snprintf(b, sizeof b, "Name:\t%s\nUmask:\t0077\nState:\tS (sleeping)\nPid:\t%d\nUid:\t%d\t%d\t%d\t%d\n", comm, pid, uid, uid, uid, uid);
  put(p, b);
  snprintf(p, sizeof p, "/proc/%d/fd/0", pid); lnk("/dev/null", p);
  if (by_fd) { snprintf(p, sizeof p, "/proc/%d/fd/7", pid); lnk("/dev/dri/renderD129", p); }
  snprintf(p, sizeof p, "/proc/%d/maps", pid);
  put(p, by_maps ? "5f0000000000-5f0000001000 r-xp 00000000 fd:00 1234 /system/bin/app_process64\n"
                   "7f0000000000-7f0000100000 rw-s 1a2b0000 00:05 512 /dev/dri/renderD129\n"
                   "7f0000200000-7f0000201000 r--p 00000000 00:00 0 [vvar]\n"
                 : "5f0000000000-5f0000001000 r-xp 00000000 fd:00 1234 /system/bin/app_process64\n");
}

/* ---- control-socket helpers (server end registered directly; the test holds the other end) */
static void serve(void) {
  for (int i = 0; i < g_ncl; i++) { cl_flush(&g_cl[i]); if (!g_cl[i].dead) cl_read(&g_cl[i]); }
}
static int cl_pair(int uid, int pid) {
  int sv[2];
  if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sv)) { perror("socketpair"); exit(2); }
  fcntl(sv[0], F_SETFL, O_NONBLOCK);
  cl_add(sv[0], uid, pid);
  return sv[1];
}
static char *rd_line(int fd, char *b, size_t cap, int ms) {   /* one reply line without the '\n', NULL if none */
  size_t n = 0;
  while (n + 1 < cap) {
    struct pollfd pf = {.fd = fd, .events = POLLIN};
    if (poll(&pf, 1, ms) <= 0) break;
    char ch;
    if (recv(fd, &ch, 1, 0) != 1) break;
    if (ch == '\n') { b[n] = 0; return b; }
    b[n++] = ch;
  }
  b[n] = 0;
  return NULL;
}
static const char *req(int fd, const char *line) {
  static char b[8192];
  if (send(fd, line, strlen(line), MSG_NOSIGNAL) < 0) return NULL;
  serve();
  const char *r = rd_line(fd, b, sizeof b, 300);
  cl_sweep();
  return r ? r : "<none>";
}
static int peer_eof(int fd) {                     /* the server closed the connection (after any pending data) */
  char b[8192];
  for (int k = 0; k < 50; k++) {
    struct pollfd pf = {.fd = fd, .events = POLLIN};
    if (poll(&pf, 1, 100) <= 0) return 0;
    ssize_t r = recv(fd, b, sizeof b, 0);
    if (r == 0) return 1;
    if (r < 0) return errno == ECONNRESET;
  }
  return 0;
}
static void drop(int fd) { close(fd); serve(); cl_sweep(); }

/* ================================================================================================ tests */
static void test_dgpu_passes(void) {
  set_root("tree_pci");
  ec_reset();
  fake_pci(0, NULL);
  dgpu_enforce("test", 1);
  CHECK(g_inv.n == 2 && g_inv.vga == 0 && !g_inv.bound_any && !strcmp(g_port, "0000:00:1d.4"), "inventory n %d vga %d port %s", g_inv.n, g_inv.vga, g_port);
  CHECK(!strcmp(get(FN0 "/power/control"), "auto") && !strcmp(get(FN1 "/power/control"), "auto"), "dGPU + audio rpm auto");
  CHECK(!strcmp(get(FN1 "/d3cold_allowed"), "1") && !strcmp(get(PORT "/power/control"), "auto"), "d3cold allowed, root port rpm auto");
  CHECK(!strcmp(get("/sys/devices/pci0000:00/0000:00:02.0/power/control"), "on"), "iGPU untouched");
  bind_drv(FN1, "snd_hda_intel");                 /* d3cold_allowed is never written while a driver is bound */
  put(FN1 "/d3cold_allowed", "0\n");
  inventory();
  dgpu_pm_pass();
  CHECK(!strcmp(get(FN1 "/d3cold_allowed"), "0"), "bound function: d3cold_allowed left alone");
  rm(FN1 "/driver");
  inventory();
  dgpu_pm_pass();
  CHECK(!strcmp(get(FN1 "/d3cold_allowed"), "1"), "driverless again: d3cold_allowed 1");
  CHECK(!g_wk.busy && !ec.log[0], "driverless: no worker '%s'", ec.log);
  /* a VGA function bound to another driver is unbound by the worker, then runtime PM auto */
  bind_drv(FN0, "vfio-pci");
  put(FN0 "/power/control", "on\n");
  dgpu_enforce("test", 0);
  CHECK(g_wk.busy && g_unbind_pending, "foreign VGA driver: worker started");
  CHECK(!strcmp(get(FN0 "/power/control"), "on"), "no rpm write while bound");
  settle();
  CHECK(strstr(ec.log, "UNBIND:0000:02:00.0") && !linked("0000:02:00.0") && !strcmp(get(FN0 "/power/control"), "auto"),
        "foreign driver unbound, then auto: '%s'", ec.log);
  CHECK(!strcmp(get("/sys/bus/pci/drivers/vfio-pci/unbind"), "0000:02:00.0"), "unbind attribute written");
  /* vendor/class/resource read once per appearance: a class change of a present device is not re-read */
  put(FN1 "/class", "0x030000\n");
  inventory();
  CHECK(!g_inv.f[1].vga, "class cached while present");
  rm("/sys/bus/pci/devices/0000:02:00.1");
  inventory();
  lnk("../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.1", "/sys/bus/pci/devices/0000:02:00.1");
  inventory();
  CHECK(g_inv.f[1].vga, "class re-read after re-appearance");
  /* no dGPU (base detached) */
  set_root("tree_nopci");
  put("/sys/bus/pci/devices/.keep", "");
  dgpu_enforce("test", 1);
  CHECK(g_inv.n == 0 && g_inv.vga < 0, "no dGPU");
}

/* the driverless quick path: unchanged sequences (old tests A-F on a driverless tree) */
static void fake_input(void) {
  put("/sys/devices/platform/MSHW0084:00/serial1-0/ssam_platform_hub/ssam:00:00:01:11:00/ssam:01:15:02:01:00/0005:045E:09AE.0001/input/input7/inhibited", "0\n");
  put("/sys/devices/platform/MSHW0084:00/serial1-0/ssam_platform_hub/ssam:00:00:01:11:00/ssam:01:15:02:03:00/0005:045E:09AF.0002/input/input8/inhibited", "0\n");
  put("/sys/devices/platform/i8042/serio0/input/input3/inhibited", "0\n");
  put("/sys/devices/virtual/input/input9/inhibited", "0\n");
  lnk("../../devices/platform/MSHW0084:00/serial1-0/ssam_platform_hub/ssam:00:00:01:11:00/ssam:01:15:02:01:00/0005:045E:09AE.0001/input/input7", "/sys/class/input/input7");
  lnk("../../devices/platform/MSHW0084:00/serial1-0/ssam_platform_hub/ssam:00:00:01:11:00/ssam:01:15:02:03:00/0005:045E:09AF.0002/input/input8", "/sys/class/input/input8");
  lnk("../../devices/platform/i8042/serio0/input/input3", "/sys/class/input/input3");
  lnk("../../devices/virtual/input/input9", "/sys/class/input/input9");
}
static void test_dtx(void) {
  set_root("tree_dtx");
  fake_input();
  fake_pci(0, NULL);
  dtx_up(0);
  CHECK(strstr(ec.log, "EV") != NULL, "events enabled");
  CHECK(!strcmp(ec.log, "EV UNLOCK ") && g_latch == 0 && !strcmp(prop("vendor.gb.dgpu.latch"), "unlocked"),
        "GP-06 start driverless: UNLOCK '%s'", ec.log);
  CHECK(!strcmp(prop("vendor.gb.dtx.mode"), "laptop") && !strcmp(prop("vendor.gb.dtx.base"), "attached"), "initial props");
  CHECK(g_dtx.ec == EC_READY, "ready");

  /* A. normal detach */
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB CONFIRM "), "detach confirmed promptly: '%s'", ec.log);
  CHECK(g_dtx.ec == EC_CONFIRMED, "confirmed state");
  CHECK(!strstr(ec.log, "UNBIND") && !g_wk.busy, "quick path: no worker");
  ec.latch = SDTX_LATCH_OPENED;
  ev(SDTX_EVENT_LATCH_STATUS, SDTX_LATCH_OPENED);
  CHECK(g_dtx.latch == SDTX_LATCH_OPENED && !strcmp(prop("vendor.gb.dtx.latch"), "opened"), "latch opened");
  ec.base = SDTX_BASE_DETACHED; ec.mode = MODE_TABLET;
  ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_DETACHED);
  CHECK(g_dtx.base == SDTX_BASE_DETACHED && !strcmp(prop("vendor.gb.dtx.base"), "detached"), "base detached");
  CHECK(!strcmp(prop("vendor.gb.dtx.mode"), "tablet"), "mode tablet via re-query");
  ec.latch = SDTX_LATCH_CLOSED;
  ev(SDTX_EVENT_LATCH_STATUS, SDTX_LATCH_CLOSED);
  CHECK(g_dtx.ec == EC_READY && g_dtx.requery_at > 0, "ready after latch close, re-check scheduled");
  g_dgpu_due = -1;
  ec.base = SDTX_BASE_ATTACHED; ec.mode = MODE_LAPTOP;
  ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_ATTACHED);
  CHECK(g_dtx.base == SDTX_BASE_ATTACHED && g_dgpu_due > 0, "attach schedules dGPU scan");
  CHECK(!strstr(ec.log, "LOCK"), "driverless: no LOCK on base events '%s'", ec.log);

  /* B. user aborts (presses the button again before the latch opened; the latch stays closed) */
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB CONFIRM ") && g_dtx.ec == EC_READY, "abort: '%s' ec %d", ec.log, g_dtx.ec);

  /* B2. confirm/abort race where the latch did open */
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  ec.latch = SDTX_LATCH_OPENED;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(g_dtx.ec == EC_CONFIRMED && g_dtx.latch == SDTX_LATCH_OPENED && !strcmp(prop("vendor.gb.dtx.latch"), "opened"),
        "race, latch open: ec %d latch %d", g_dtx.ec, g_dtx.latch);
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB CONFIRM ") && g_dtx.ec == EC_CONFIRMED, "press while open: '%s'", ec.log);
  ec.latch = SDTX_LATCH_CLOSED;
  ev(SDTX_EVENT_LATCH_STATUS, SDTX_LATCH_CLOSED);
  CHECK(g_dtx.ec == EC_READY && g_dtx.latch == SDTX_LATCH_CLOSED, "race resolved by latch close");

  /* B3. unknown EC values are ignored */
  ec.log[0] = 0;
  ec.base = 0xf005;
  ev(SDTX_EVENT_BASE_CONNECTION, 0xf005);
  CHECK(g_dtx.base == SDTX_BASE_ATTACHED, "unknown base state ignored: 0x%x", g_dtx.base);
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB CONFIRM ") && g_dtx.ec == EC_CONFIRMED, "unknown base state does not cancel: '%s'", ec.log);
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(g_dtx.ec == EC_READY, "B3 abort");
  ec.base = SDTX_BASE_ATTACHED;
  ev(SDTX_EVENT_LATCH_STATUS, 0xf007);
  CHECK(g_dtx.latch == SDTX_LATCH_CLOSED, "unknown latch status ignored");
  ev(SDTX_EVENT_DEVICE_MODE, 7);
  CHECK(g_dtx.mode == MODE_LAPTOP, "unknown mode ignored");
  ec.latch = SDTX_ERR_FAILED_TO_CLOSE;
  ev(SDTX_EVENT_LATCH_STATUS, SDTX_ERR_FAILED_TO_CLOSE);
  CHECK(g_dtx.latch == SDTX_LATCH_OPENED, "failed-to-close -> open");
  ec.latch = SDTX_LATCH_CLOSED;
  ev(SDTX_EVENT_LATCH_STATUS, SDTX_ERR_FAILED_TO_REMAIN_OPEN);
  CHECK(g_dtx.latch == SDTX_LATCH_CLOSED && g_dtx.ec == EC_READY, "failed-to-remain-open -> re-read closed");

  /* B4. dGPU handling off (gb.dgpu=keep / -G): quick path, runtime PM untouched */
  g_dgpu_on = 0;
  put(FN0 "/power/control", "on\n");
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB CONFIRM ") && !strcmp(get(FN0 "/power/control"), "on"), "keep mode detach: '%s'", ec.log);
  ev(SDTX_EVENT_REQUEST, 0);
  g_dgpu_on = 1;

  /* C. low clipboard battery: refuse with LATCH_CANCEL, EC echoes a request */
  ec.log[0] = 0;
  ec.base = SDTX_DETACH_NOT_FEASIBLE;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "CANCEL ") && g_dtx.ec == EC_IN_PROGRESS, "not feasible: '%s'", ec.log);
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(g_dtx.ec == EC_READY && !strstr(ec.log, "CONFIRM"), "echo resets");
  ec.base = SDTX_BASE_ATTACHED;
  ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_ATTACHED);

  /* D. EC cancel (hardware error) during a detach */
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(g_dtx.ec == EC_CONFIRMED, "D confirmed");
  ev(SDTX_EVENT_CANCEL, SDTX_ERR_FAILED_TO_OPEN);
  CHECK(g_dtx.ec == EC_READY, "D cancel -> ready");

  /* E. stream parsing: split event, unknown event skipped, v6.18 bogus base event carrying a latch status */
  ec.log[0] = 0;
  unsigned char junk[3] = {1, 2, 3};
  push(9, junk, 3);
  push(SDTX_EVENT_BASE_CONNECTION, (const unsigned char *)"\x01\x00\x7f\x7f", 4);
  size_t full = g_dtx.blen;
  push(SDTX_EVENT_REQUEST, NULL, 0);
  unsigned char hdr[4];
  memcpy(hdr, g_dtx.buf + full, 4);
  g_dtx.blen = full + 2;
  int k = dtx_parse();
  CHECK(k == 2 && g_dtx.blen == 2 && !memcmp(g_dtx.buf, hdr, 2) && g_dtx.base == SDTX_BASE_ATTACHED,
        "partial: %d events, %zu left", k, g_dtx.blen);
  memcpy(g_dtx.buf + g_dtx.blen, hdr + 2, 2);
  g_dtx.blen += 2;
  k = dtx_parse();
  CHECK(k == 1 && g_dtx.blen == 0 && strstr(ec.log, "CONFIRM"), "rest parsed: %d '%s'", k, ec.log);
  ev(SDTX_EVENT_REQUEST, 0);

  /* F. studio mode inhibits base keyboard + touchpad only */
  ec.mode = MODE_STUDIO;
  ev(SDTX_EVENT_DEVICE_MODE, MODE_STUDIO);
  CHECK(!strcmp(prop("vendor.gb.dtx.mode"), "studio"), "studio prop");
  CHECK(!strcmp(get("/sys/class/input/input7/inhibited"), "1") && !strcmp(get("/sys/class/input/input8/inhibited"), "1"), "base input inhibited");
  CHECK(!strcmp(get("/sys/class/input/input3/inhibited"), "0") && !strcmp(get("/sys/class/input/input9/inhibited"), "0"), "other input untouched");
  ec.mode = MODE_LAPTOP;
  ev(SDTX_EVENT_DEVICE_MODE, MODE_LAPTOP);
  CHECK(!strcmp(get("/sys/class/input/input7/inhibited"), "0") && !strcmp(get("/sys/class/input/input8/inhibited"), "0"), "re-enabled");
  dtx_close();
}

/* GP-06/07/08 latch lifecycle */
static void test_latch(void) {
  set_root("tree_latch_bound");
  fake_pci(1, NULL);
  dtx_up(0);
  CHECK(!strcmp(ec.log, "EV LOCK ") && g_latch == 1 && !strcmp(prop("vendor.gb.dgpu.latch"), "locked"), "GP-06 bound at start: '%s'", ec.log);
  set_root("tree_latch_wedged");
  fake_pci(0, NULL);
  put("/dev/gb_dgpu/wedged", "1\n");
  dtx_up(0);
  CHECK(!strcmp(ec.log, "EV ") && g_wedged, "GP-06 wedged earlier: latch left alone '%s'", ec.log);

  set_root("tree_latch");
  fake_pci(0, NULL);
  dtx_up(0);
  ec.log[0] = 0;
  bind_nouveau();
  status_tick(T);
  CHECK(!strcmp(ec.log, "LOCK "), "bind -> LOCK '%s'", ec.log);
  status_tick(T);
  CHECK(!strcmp(ec.log, "LOCK "), "idempotent '%s'", ec.log);
  rm(FN0 "/driver");                              /* gb_nouveau.sh off unbound it */
  status_tick(T);
  CHECK(!strcmp(ec.log, "LOCK UNLOCK ") && !strcmp(prop("vendor.gb.dgpu.latch"), "unlocked"), "driverless -> UNLOCK '%s'", ec.log);
  bind_drv(FN1, "snd_hda_intel");                 /* any driver on any NVIDIA function locks */
  inventory();
  latch_eval("test");
  CHECK(!strcmp(ec.log, "LOCK UNLOCK LOCK "), "HDA bound -> LOCK '%s'", ec.log);
  rm(FN1 "/driver");
  bind_nouveau();
  put("/sys/power/suspend_stats/success", "3\n");
  status_tick(T);
  ec.log[0] = 0;
  status_tick(T);
  CHECK(ec.log[0] == 0, "no resume, no ioctl '%s'", ec.log);
  put("/sys/power/suspend_stats/success", "4\n");
  status_tick(T);
  CHECK(!strcmp(ec.log, "LOCK "), "GP-07 re-LOCK after resume '%s'", ec.log);
  ec.log[0] = 0;
  ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_ATTACHED);
  CHECK(!strcmp(ec.log, "LOCK "), "GP-07 re-LOCK after BASE_CONNECTION '%s'", ec.log);
  /* GP-08 SIGTERM */
  ec.log[0] = 0;
  term_handle();
  CHECK(!strstr(ec.log, "UNLOCK"), "GP-08 SIGTERM bound, no powerctl: stays locked '%s'", ec.log);
  prop_set("sys.powerctl", "reboot,userrequested");
  term_handle();
  CHECK(!strcmp(ec.log, "UNLOCK "), "GP-08 SIGTERM during shutdown: unlock '%s'", ec.log);
  prop_set("sys.powerctl", "");
  latch_set(1, 1, "test");
  rm(FN0 "/driver");
  ec.log[0] = 0;
  term_handle();
  CHECK(!strcmp(ec.log, "UNLOCK "), "GP-08 SIGTERM driverless: unlock '%s'", ec.log);
  /* sys.powerctl seen by the tick: unlock once, never locked again */
  bind_nouveau();
  status_tick(T);
  ec.log[0] = 0;
  prop_set("sys.powerctl", "shutdown,");
  status_tick(T);
  CHECK(!strcmp(ec.log, "UNLOCK ") && g_shutdown, "powerctl -> UNLOCK '%s'", ec.log);
  ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_ATTACHED);
  put("/sys/power/suspend_stats/success", "5\n");
  status_tick(T);
  CHECK(!strcmp(ec.log, "UNLOCK "), "no re-lock after shutdown started '%s'", ec.log);
  dtx_close();
}

/* GP-10 used-but-driverless detach removes every function before CONFIRM; GP-11 attach: staged rescan */
static void test_used_detach_and_rescan(void) {
  set_root("tree_used");
  fake_pci(0, NULL);
  notify_dir();
  prop_set("vendor.gb.nouveau.used", "1");
  dtx_up(1);
  ec.log[0] = 0;
  press();
  CHECK(dec_running() && !strcmp(prop("vendor.gb.dgpu.state"), "detaching"), "slow path, state detaching (%s)", prop("vendor.gb.dgpu.state"));
  CHECK(notified("Getting the NVIDIA GPU ready. Keep the screen attached.") && !strcmp(prop("ctl.stop"), "gb_gpu_compute"), "N07 + compute stopped");
  CHECK(exists("/dev/gb_dgpu/used") && g_used, "used flag mirrored into /dev/gb_dgpu/used");
  CHECK(exists("/dev/gb_dgpu/detaching"), "detaching flag set for the decision");
  settle();
  int r1 = logpos("REMOVE:0000:02:00.1"), r0 = logpos("REMOVE:0000:02:00.0"), cf = logpos("CONFIRM"), lk = logpos("LOCK");
  CHECK(r1 >= 0 && r0 > r1 && cf > r0 && !strstr(ec.log, "UNBIND"), "GP-10 remove both, then confirm: '%s'", ec.log);
  CHECK(lk >= 0 && lk < r1, "latch locked for the driverless slow path before the remove: '%s'", ec.log);
  CHECK(g_dtx.ec == EC_CONFIRMED && g_dtx.latch == SDTX_LATCH_OPENED && !exists("/sys/bus/pci/devices/0000:02:00.0"), "confirmed, latch open");
  CHECK(notified("You can remove the screen now.") && !strcmp(prop("vendor.gb.dgpu.latch"), "unlocked"), "N12, CONFIRM resets the lock");
  CHECK(!strcmp(prop("vendor.gb.dgpu.state"), "unavailable"), "state unavailable after the remove (%s)", prop("vendor.gb.dgpu.state"));
  /* the user pulls the clipboard: base detached, latch closes; nothing to rescan while detached */
  ec.base = SDTX_BASE_DETACHED;
  ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_DETACHED);
  ec.latch = SDTX_LATCH_CLOSED;
  ev(SDTX_EVENT_LATCH_STATUS, SDTX_LATCH_CLOSED);
  pump(3000, 100);
  CHECK(!g_rs.active && !strstr(ec.log, "rescan"), "no rescan while detached");
  CHECK(!exists("/dev/gb_dgpu/detaching"), "latch closed: detaching flag removed");
  /* GP-11: re-attach with an empty bus (sticky ignore_hotplug): staged rescan 0/+2/+5/+15 s, then fault rescan */
  put(PORT "/power/control", "auto\n");
  put(PORT "/power_state", "D3cold\n");
  ec.log[0] = 0;
  ec.base = SDTX_BASE_ATTACHED;
  ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_ATTACHED);
  pump(400, 100);
  CHECK(g_rs.active, "rescan started");
  pump(30000, 100);
  int pon = logpos("WRITE:0000:00:1d.4/power/control=on"), rsc = logpos("WRITE:0000:00:1d.4/rescan=1");
  CHECK(pon >= 0 && rsc > pon && count_in(ec.log, "WRITE:0000:00:1d.4/rescan=1") == 4 && count_in(ec.log, "power/control=on") == 1,
        "GP-11 writes in order (port on once, 4 rescans): '%s'", ec.log);
  CHECK(ec.nrescan == 4, "4 rescans (%d)", ec.nrescan);
  if (ec.nrescan == 4) {
    long long g1 = ec.rescan_t[1] - ec.rescan_t[0], g2 = ec.rescan_t[2] - ec.rescan_t[1], g3 = ec.rescan_t[3] - ec.rescan_t[2];
    CHECK(llabs(g1 - 2000) <= 300 && llabs(g2 - 5000) <= 300 && llabs(g3 - 15000) <= 300, "backoff %lld/%lld/%lld ms", g1, g2, g3);
  }
  CHECK(!g_rs.active && !strcmp(g_fault, "rescan") && !strcmp(get(PORT "/power/control"), "auto"), "retries exhausted: fault rescan, port auto (%s)", g_fault);
  /* v1.2 (C22, INTERFACES 8.2): rescan does not block a detach, so it posts N20, not N06 */
  CHECK(notified("The NVIDIA GPU did not start correctly (rescan). Apps use Intel graphics until restart.") &&
        !notified("(rescan). Detaching is blocked until restart."), "N20 rescan (v1.2; v1.1 posted N06)");
  pump(20000, 1000);
  CHECK(count_in(ec.log, "rescan=1") == 4, "no rescan loop after the fault");

  /* base detached mid-way: abort, no fault */
  set_root("tree_rescan_abort");
  fake_pci(0, NULL);
  rm("/sys/bus/pci/devices/0000:02:00.0");
  rm("/sys/bus/pci/devices/0000:02:00.1");
  put(PORT "/power/control", "auto\n");
  prop_set("vendor.gb.nouveau.used", "1");
  dtx_up(1);
  pump(1000, 100);
  CHECK(g_rs.active && g_rs.phase == RS_RETRY && count_in(ec.log, "rescan=1") == 1, "first attempt done, waiting: '%s'", ec.log);
  ec.base = SDTX_BASE_DETACHED;
  ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_DETACHED);
  pump(20000, 100);
  CHECK(!g_rs.active && !g_fault[0] && count_in(ec.log, "rescan=1") == 1 && !strcmp(get(PORT "/power/control"), "auto"),
        "abort when detached: no retry, no fault, port auto '%s'", ec.log);
  ec.base = SDTX_BASE_ATTACHED;                   /* attached again later: a new sequence */
  ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_ATTACHED);
  pump(1000, 100);
  CHECK(count_in(ec.log, "rescan=1") == 2, "new sequence after re-attach");

  /* success on the second attempt; nouveau rebinds -> node permissions re-applied (D19) and LOCK */
  set_root("tree_rescan_ok");
  fake_pci(0, NULL);
  rm("/sys/bus/pci/devices/0000:02:00.0");
  rm("/sys/bus/pci/devices/0000:02:00.1");
  prop_set("vendor.gb.nouveau.used", "1");
  dtx_up(1);
  k_restore_at = 2;
  pump(4000, 100);
  CHECK(!g_rs.active && !g_fault[0] && g_inv.n == 2 && count_in(ec.log, "rescan=1") == 2, "back on attempt 2: '%s'", ec.log);
  CHECK(!strcmp(get(PORT "/power/control"), "auto"), "port auto restored");
  ec.log[0] = 0;
  bind_nouveau();
  status_tick(T);
  CHECK(mode_of("/dev/dri/renderD129") == 0660 && mode_of("/dev/dri/card1") == 0600 && mode_of("/dev/dri/renderD128") == 0666,
        "D19 nodes 0660/0600, i915 untouched (%o %o %o)", mode_of("/dev/dri/renderD129"), mode_of("/dev/dri/card1"), mode_of("/dev/dri/renderD128"));
  CHECK(strstr(g_chown_log, "renderD129:0:1003") && strstr(g_chown_log, "card1:0:0") && !strstr(g_chown_log, "renderD128"), "chown %s", g_chown_log);
  CHECK(!strcmp(ec.log, "LOCK "), "rebound -> LOCK '%s'", ec.log);
  char p[PATH_MAX];
  chmod(rpath(p, sizeof p, "/dev/dri/renderD129"), 0);   /* gb_nouveau.sh off: chmod 000 window */
  status_tick(T);
  CHECK(mode_of("/dev/dri/renderD129") == 0, "same inode: never re-applied (off gate window kept)");
  prop_set("vendor.gb.gfxdriver", "on");          /* spoofed property alone (any app can set it): stays 0660 */
  bind_nouveau();
  status_tick(T);
  CHECK(mode_of("/dev/dri/renderD129") == 0660, "gfxdriver property without the root-only flag: render 0660 (%o)", mode_of("/dev/dri/renderD129"));
  put("/dev/gb_dgpu/gfxdriver", "");              /* the retired v1.1 P8 gate (property + flag) */
  bind_nouveau();
  status_tick(T);
  /* v1.2 (TASKS_1B 9, GP-32): the gate is retired: 0660 unless gb_power itself derived gfx_active=nvidia */
  CHECK(mode_of("/dev/dri/renderD129") == 0660 && !g_gfx.active, "retired gfxdriver gate (property + flag): render 0660 (%o)", mode_of("/dev/dri/renderD129"));
  prop_set("vendor.gb.gfxdriver", "off");
  bind_nouveau();
  status_tick(T);
  CHECK(mode_of("/dev/dri/renderD129") == 0660, "flag without the property: 0660");

  /* zero BAR after rescan: fault bar */
  set_root("tree_rescan_bar");
  fake_pci(0, NULL);
  rm("/sys/bus/pci/devices/0000:02:00.0");
  rm("/sys/bus/pci/devices/0000:02:00.1");
  prop_set("vendor.gb.nouveau.used", "1");
  notify_dir();
  dtx_up(1);
  k_restore_at = 1;
  k_zero_bar = 1;
  pump(2000, 100);
  CHECK(!strcmp(g_fault, "bar") && notified("(bar)"), "zero BAR -> fault bar (%s)", g_fault);
}

/* GP-04 holder by maps only (and one by fd): refuse after 30 s, permissions restored, N08; GP-03 EC timeout model */
static void busy_tree(const char *name) {
  set_root(name);
  fake_pci(1, NULL);
  notify_dir();
  pkgs();
  put("/sys/module/nouveau/refcnt", "2\n");
  proc_holder(4242, "game", 10300, 0, 1);         /* maps only */
  proc_holder(4243, "nvk_probe", 0, 1, 0);        /* fd */
  proc_holder(500, "surfaceflinger", 1000, 0, 0); /* not a holder */
}
static void test_busy_refuse(void) {
  busy_tree("tree_busy");
  dtx_up(1);
  status_tick(T);
  CHECK(!strcmp(prop("vendor.gb.dgpu.state"), "busy:2"), "busy:2 (%s)", prop("vendor.gb.dgpu.state"));
  CHECK(!strcmp(prop("vendor.gb.dgpu.holders"), "game:4242,nvk_probe:4243") || !strcmp(prop("vendor.gb.dgpu.holders"), "nvk_probe:4243,game:4242"),
        "holders prop '%s'", prop("vendor.gb.dgpu.holders"));
  ec.log[0] = 0;
  ec.nhb = 0;
  long long t0 = T;
  press();
  CHECK(mode_of("/dev/dri/renderD129") == 0 && mode_of("/dev/dri/card1") == 0 && mode_of("/dev/dri/renderD128") == 0666, "nodes 000 during the decision");
  pump(29000, 250);
  CHECK(dec_running() && !strstr(ec.log, "CANCEL"), "still waiting at 29 s");
  pump(2000, 250);
  CHECK(!dec_running() && strstr(ec.log, "CANCEL") && !strstr(ec.log, "CONFIRM") && !strstr(ec.log, "UNBIND") && !strstr(ec.log, "REMOVE"),
        "GP-04 refused: '%s'", ec.log);
  CHECK(mode_of("/dev/dri/renderD129") == 0660 && mode_of("/dev/dri/card1") == 0600, "permissions restored");
  CHECK(notified("The NVIDIA GPU is in use by com.example.game, nvk_probe. Close them, then press detach again.") ||
        notified("The NVIDIA GPU is in use by nvk_probe, com.example.game. Close them, then press detach again."), "N08 names the holders");
  CHECK(hb_max_gap(t0, t0 + 30000) <= 2000 && ec.nhb >= 25, "GP-03 heartbeats <= 2 s for the whole decision (max gap %lld, %d HB)", hb_max_gap(t0, t0 + 30000), ec.nhb);
  CHECK(!strstr(ec.log, "EC-TIMEOUT") && ec.latch == SDTX_LATCH_CLOSED && g_dtx.ec == EC_READY, "EC never timed out, latch closed, echo -> ready");
  /* GP-03: gb_power stops answering mid-decision (killed): the locked latch makes the EC cancel, never open */
  ec.log[0] = 0;
  press();
  T += EC_TIMEOUT + 100;                          /* no run_due: the daemon is gone */
  ec_model_tick();
  CHECK(strstr(ec.log, "EC-TIMEOUT-CANCEL") && !strstr(ec.log, "EC-TIMEOUT-OPEN") && ec.latch == SDTX_LATCH_CLOSED, "GP-03 locked + timeout: CANCEL '%s'", ec.log);
  ec_deliver();                                   /* the daemon (if it comes back) sees the CANCEL event */
  pump(1000, 250);
  CHECK(!dec_running() && g_dtx.ec == EC_READY && mode_of("/dev/dri/renderD129") == 0660 && !strstr(ec.log, "CONFIRM"),
        "EC cancel ends the decision, permissions restored");
  /* contrast: an unlocked latch and no daemon answer -> the EC opens it (why the lock exists) */
  set_root("tree_unlocked_timeout");
  fake_pci(0, NULL);
  dtx_up(1);
  ec.active = 1;
  ec.last_hb = T;
  T += EC_TIMEOUT + 100;
  ec_model_tick();
  CHECK(strstr(ec.log, "EC-TIMEOUT-OPEN"), "model: unlocked + timeout opens the latch");
}

/* GP-12 double press while a worker job runs: cancel at the next safe point (after that job), never inside it; a
 * press during job 1 (unbind) cancels before any remove, a press during job 2 (remove) cancels after it, then the
 * cancel rescan brings the functions back */
static void test_double_press(void) {
  set_root("tree_double");
  fake_pci(1, NULL);
  notify_dir();
  dtx_up(1);
  status_tick(T);
  if (pipe(g_hang)) { perror("pipe"); exit(2); }
  k_unbind = 2;
  ec.log[0] = 0;
  press();
  pump(1000, 100);
  CHECK(g_wk.busy && g_dec.phase == DEC_UNBIND, "worker running (hung in unbind)");
  press();                                        /* user abort */
  pump(2000, 100);
  CHECK(dec_running() && g_dec.abort && !strstr(ec.log, "CANCEL") && !strstr(ec.log, "CONFIRM"), "nothing sent inside the worker: '%s'", ec.log);
  if (write(g_hang[1], "x", 1) != 1) {}
  settle();
  int ub0 = logpos("UNBIND:0000:02:00.0"), cn = logpos("CANCEL");
  CHECK(ub0 >= 0 && cn > ub0 && !strstr(ec.log, "REMOVE") && !strstr(ec.log, "CONFIRM") && !dec_running(),
        "press during the unbind job: canceled after it, before any remove '%s'", ec.log);
  CHECK(g_dtx.ec == EC_READY && !g_removed && g_inv.n == 2, "echo -> ready, functions still there (no rescan needed)");
  close(g_hang[0]); close(g_hang[1]);
  g_hang[0] = g_hang[1] = -1;
  /* press during the remove job (used, driverless): cancel after it, then the cancel rescan */
  set_root("tree_double_rm");
  fake_pci(0, NULL);
  notify_dir();
  prop_set("vendor.gb.nouveau.used", "1");
  dtx_up(1);
  if (pipe(g_hang)) { perror("pipe"); exit(2); }
  k_remove = 2;
  ec.log[0] = 0;
  press();
  pump(1000, 100);
  CHECK(g_wk.busy && g_dec.phase == DEC_REMOVE, "worker running (hung in remove)");
  press();
  pump(2000, 100);
  CHECK(dec_running() && g_dec.abort && !strstr(ec.log, "CANCEL"), "nothing sent inside the remove job: '%s'", ec.log);
  if (write(g_hang[1], "xx", 2) != 2) {}
  settle();
  int rm0 = logpos("REMOVE:0000:02:00.0");
  cn = logpos("CANCEL");
  CHECK(rm0 >= 0 && cn > rm0 && !strstr(ec.log, "CONFIRM") && !dec_running(), "cancel after the remove job: '%s'", ec.log);
  CHECK(g_dtx.ec == EC_READY && g_removed, "echo -> ready, functions were removed");
  k_remove = 0;
  k_restore_at = 1;
  pump(3000, 100);
  CHECK(strstr(ec.log, "rescan=1") && g_inv.n == 2 && !g_fault[0], "cancel rescan brought the functions back");
  close(g_hang[0]); close(g_hang[1]);
  g_hang[0] = g_hang[1] = -1;
}

/* GP-05 hung unbind: wedged within 21 s, battery bridge keeps updating, no further dGPU writes */
static void test_hung_unbind(void) {
  char b[2048];
  set_root("tree_hang");
  fake_pci(1, NULL);
  fake_tp(1);
  fake_psy("BAT1", bat(b, "BAT1", "Discharging", 8750000, 17500000, 6000000, 7700000, 7600000, NULL), "Battery\n", "surface_battery");
  fake_psy("ADP1", ADP_OFF, "Mains\n", "surface_charger");
  notify_dir();
  tp_probe();
  battery_update(1);
  CHECK(!strcmp(get(TP "battery_capacity"), "50"), "battery before %s", get(TP "battery_capacity"));
  dtx_up(1);
  status_tick(T);
  if (pipe(g_hang)) { perror("pipe"); exit(2); }
  k_unbind = 2;
  ec.log[0] = 0;
  ec.nhb = 0;
  press();
  pump(600, 100);
  CHECK(g_wk.busy && g_wk.cur.kind == J_DETACH, "detach worker started");
  long long w0 = g_wk.t0;
  pid_t kid = g_wk.pid;
  g_stuck[g_nstuck++] = kid;
  pump(4000, 500);
  fake_psy("BAT1", bat(b, "BAT1", "Discharging", 5250000, 17500000, 6000000, 7700000, 7600000, NULL), "Battery\n", "surface_battery");
  struct uev u = {.action = "change", .subsystem = "power_supply", .psy_name = "BAT1"};
  uev_handle(&u);
  pump(1000, 500);
  CHECK(!strcmp(get(TP "battery_capacity"), "30") && g_wk.busy, "GP-05 battery bridge updated during the hang (%s)", get(TP "battery_capacity"));
  long long tw = -1;
  for (int k = 0; k < 60 && tw < 0; k++) { pump(500, 500); if (g_wedged) tw = T; }
  CHECK(tw >= 0 && tw - w0 >= 20000 && tw - w0 <= 21000, "GP-05 wedged %lld ms after the worker started", tw - w0);
  CHECK(exists("/dev/gb_dgpu/wedged") && !strcmp(prop("vendor.gb.dgpu.state"), "wedged") && strstr(ec.log, "CANCEL") && !strstr(ec.log, "CONFIRM"),
        "wedged: file, state, CANCEL: '%s'", ec.log);
  CHECK(hb_max_gap(w0 - 500, tw) <= 2000, "heartbeats kept during the hang (max gap %lld)", hb_max_gap(w0 - 500, tw));
  CHECK(notified("The NVIDIA GPU did not shut down in time. Restart, then detach.") && g_latch == 1 && ec.locked, "N11, latch stays locked");
  CHECK(!strstr(ec.log, "EC-TIMEOUT"), "the EC never timed out");
  /* no further dGPU writes this boot */
  bind_drv(FN1, "snd_hda_intel");
  put(FN1 "/power/control", "on\n");
  status_tick(T);
  dgpu_enforce("hot-plug", 0);
  CHECK(!g_wk.busy && linked("0000:02:00.1") && !strcmp(get(FN1 "/power/control"), "on"), "after the wedge: no unbind, no runtime PM writes");
  ec.log[0] = 0;
  press();
  CHECK(!strcmp(ec.log, "CANCEL REQ ") || !strncmp(ec.log, "CANCEL ", 7), "detach refused at once (no heartbeat): '%s'", ec.log);
  g_cmp.want = 1;
  hold_eval();
  CHECK(!g_wk.busy && !g_cmp.pending, "no compute hold write after the wedge");
  kill(kid, SIGKILL);                             /* test cleanup only: the daemon leaves the stuck child alone */
  waitpid(kid, NULL, 0);
  g_nstuck = 0;
  close(g_hang[0]); close(g_hang[1]);
  g_hang[0] = g_hang[1] = -1;
}

/* GP-09 boot-disk guard */
static void blk_tree(void) {
  put("/sys/devices/pci0000:00/0000:00:14.0/usb2/2-1/2-1:1.0/host0/target0:0:0/0:0:0:0/block/sda/sda1/dev", "8:1\n");
  put("/sys/devices/pci0000:00/0000:00:14.0/usb2/2-1/2-1:1.0/host0/target0:0:0/0:0:0:0/block/sda/sda5/dev", "8:5\n");
  put("/sys/devices/pci0000:00/0000:00:1d.0/0000:01:00.0/nvme/nvme0/nvme0n1/nvme0n1p5/dev", "259:5\n");
  put("/sys/devices/pci0000:00/0000:00:14.5/mmc_host/mmc0/mmc0:aaaa/block/mmcblk0/mmcblk0p1/dev", "179:1\n");
  put("/sys/devices/virtual/block/dm-0/dev", "253:0\n");
  lnk("../../devices/pci0000:00/0000:00:14.0/usb2/2-1/2-1:1.0/host0/target0:0:0/0:0:0:0/block/sda/sda1", "/sys/dev/block/8:1");
  lnk("../../devices/pci0000:00/0000:00:14.0/usb2/2-1/2-1:1.0/host0/target0:0:0/0:0:0:0/block/sda/sda5", "/sys/dev/block/8:5");
  lnk("../../devices/pci0000:00/0000:00:1d.0/0000:01:00.0/nvme/nvme0/nvme0n1/nvme0n1p5", "/sys/dev/block/259:5");
  lnk("../../devices/pci0000:00/0000:00:14.5/mmc_host/mmc0/mmc0:aaaa/block/mmcblk0/mmcblk0p1", "/sys/dev/block/179:1");
  lnk("../../devices/virtual/block/dm-0", "/sys/dev/block/253:0");
  lnk("../../../../pci0000:00/0000:00:14.0/usb2/2-1/2-1:1.0/host0/target0:0:0/0:0:0:0/block/sda/sda5", "/sys/devices/virtual/block/dm-0/slaves/sda5");
}
static void test_bootdisk(void) {
  static const struct { const char *mounts, *want; int usb_boot; } C[] = {
    {"/dev/block/nvme0n1p5 /data ext4 rw 0 0\n/dev/block/sda1 /mnt/media_rw/1234-ABCD vfat rw 0 0\ntmpfs /dev tmpfs rw 0 0\n",
     "A USB or SD drive in the keyboard base is in use. Eject it, then press detach again.", 0},
    {"/dev/block/mmcblk0p1 /mnt/media_rw/SD vfat rw 0 0\n", "A USB or SD drive in the keyboard base is in use.", 0},
    {"/dev/block/dm-0 /system ext4 ro 0 0\n", "The system is running from a drive in the keyboard base. Shut down before detaching.", 1},
  };
  for (unsigned i = 0; i < sizeof C / sizeof C[0]; i++) {
    set_root("tree_bootdisk");
    fake_pci(0, NULL);
    notify_dir();
    blk_tree();
    g_blkdev = fake_blkdev;
    if (C[i].usb_boot) snprintf(g_boot, sizeof g_boot, "usb");
    put("/proc/mounts", C[i].mounts);
    dtx_up(1);
    ec.log[0] = 0;
    press();
    CHECK(!strcmp(ec.log, "CANCEL ") && g_dtx.ec == EC_READY && notified(C[i].want), "GP-09 case %u: refused '%s'", i, ec.log);
  }
  set_root("tree_bootdisk_internal");
  fake_pci(0, NULL);
  blk_tree();
  g_blkdev = fake_blkdev;
  put("/proc/mounts", "/dev/block/nvme0n1p5 /data ext4 rw 0 0\nproc /proc proc rw 0 0\n");
  dtx_up(1);
  ec.log[0] = 0;
  press();
  CHECK(!strcmp(ec.log, "HB CONFIRM "), "internal disk only: quick path '%s'", ec.log);
  g_blkdev = real_blkdev;
}

/* GP-13 property flip; GP-14 HDA always driverless; -K */
static void test_never_unbind_nouveau(void) {
  set_root("tree_flip");
  fake_pci(1, NULL);
  prop_set("persist.vendor.gb.nouveau", "1");
  status_tick(T);
  dgpu_enforce("hot-plug", 1);
  prop_set("persist.vendor.gb.nouveau", "0");
  pump(30000, 1000);
  dgpu_enforce("hot-plug", 0);
  dgpu_enforce("report", 1);
  CHECK(linked("0000:02:00.0") && !strstr(ec.log, "UNBIND") && !g_wk.busy, "GP-13 persist 1 -> 0: gb_power never unbinds nouveau '%s'", ec.log);
  CHECK(!strcmp(get(FN0 "/power/control"), "on"), "nouveau's power/control left to nouveau");

  static const char *const persist[] = {"", "0", "1"};
  for (int nv = 0; nv < 2; nv++)
    for (int m = 0; m < 3; m++) {
      set_root("tree_hda");
      fake_pci(nv, "snd_hda_intel");
      prop_set("persist.vendor.gb.nouveau", persist[m]);
      ec.log[0] = 0;
      dgpu_enforce("hot-plug", 0);
      settle();
      CHECK(strstr(ec.log, "UNBIND:0000:02:00.1") && !linked("0000:02:00.1") && !strstr(ec.log, "UNBIND:0000:02:00.0") &&
            linked("0000:02:00.0") == nv, "GP-14 nouveau=%d persist='%s': HDA unbound, VGA kept '%s'", nv, persist[m], ec.log);
      CHECK(!strcmp(get(FN1 "/power/control"), "auto"), "HDA runtime PM auto after the unbind");
    }
  /* -K: the VGA keep driver from the command line; non-VGA functions stay driverless whatever it says */
  set_root("tree_k");
  fake_pci(1, "snd_hda_intel");
  char root[PATH_MAX];
  snprintf(root, sizeof root, "%s", g_root);
  char *argv[] = {"gb_power", "-o", "-r", root, "-K", "snd_hda_intel", NULL};
  optind = 1;
  CHECK(gb_power_main(6, argv) == 0 && g_keep_cli && !strcmp(g_keep_drv, "snd_hda_intel"), "-K parsed");
  ec.log[0] = 0;
  dgpu_enforce("hot-plug", 0);
  settle();
  CHECK(strstr(ec.log, "UNBIND:0000:02:00.1") && strstr(ec.log, "UNBIND:0000:02:00.0"), "-K other driver: nouveau unbound (test override), HDA still unbound '%s'", ec.log);
}

/* GP-15 watchdog */
static void test_watchdog(void) {
  set_root("tree_wd");
  fake_pci(1, NULL);
  notify_dir();
  dtx_up(0);
  put(FN0 "/power/runtime_status", "error\n");
  status_tick(T);
  CHECK(!strcmp(g_fault, "rpm-error") && !strcmp(prop("vendor.gb.dgpu.state"), "fault") && g_latch == 1, "GP-15 rpm-error -> fault, locked");
  CHECK(notified("The NVIDIA GPU is not responding to power management (rpm-error). Graphics are set to Intel only. Restart, then detach."),
        "N06 text (R-8)");
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "CANCEL "), "fault blocks detach '%s'", ec.log);
  rm(FN0 "/driver");
  status_tick(T);
  CHECK(g_latch == 1, "fault: latch stays locked even if the driver goes");

  set_root("tree_wd_d0");
  fake_pci(1, NULL);
  put(FN0 "/power_state", "D0\n");
  put(FN0 "/power/runtime_status", "active\n");
  status_tick(T);
  pump(59000, 1000);
  CHECK(!g_fault[0], "D0 59 s: no fault yet");
  pump(3000, 1000);
  CHECK(!strcmp(g_fault, "d0-idle"), "GP-15 D0 > 60 s with refcnt 0 -> d0-idle (%s)", g_fault);

  set_root("tree_wd_busy");
  fake_pci(1, NULL);
  put(FN0 "/power_state", "D0\n");
  put("/sys/module/nouveau/refcnt", "1\n");
  pump(70000, 1000);
  CHECK(!g_fault[0], "D0 while in use (refcnt 1): no fault");

  set_root("tree_wd_hold");
  fake_pci(1, NULL);
  put(FN0 "/power_state", "D0\n");
  put(FN0 "/power/control", "auto\n");
  g_cmp.want = 1;
  pump(70000, 1000);
  CHECK(!g_fault[0] && !strcmp(get(FN0 "/power/control"), "on"), "D0 with the compute hold: no fault, power/control on (%s)", g_fault);
}

/* GP-16 state derivation, every row of INTERFACES 8.1 */
static const char *state_now(void) { status_tick(T); return prop("vendor.gb.dgpu.state"); }
static void test_state(void) {
  set_root("tree_state");
  fake_pci(0, NULL);
  CHECK(!strcmp(state_now(), "off"), "10 off: %s", prop("vendor.gb.dgpu.state"));
  bind_nouveau();
  CHECK(!strcmp(state_now(), "standby"), "9 standby: %s", prop("vendor.gb.dgpu.state"));
  put("/sys/module/nouveau/refcnt", "1\n");
  CHECK(!strcmp(state_now(), "busy:1"), "8 busy:1 without named holders: %s", prop("vendor.gb.dgpu.state"));
  proc_holder(4243, "nvk_probe", 0, 1, 0);
  g_hold.valid = 0;
  CHECK(!strcmp(state_now(), "busy:1") && !strcmp(prop("vendor.gb.dgpu.holders"), "nvk_probe:4243"), "8 busy:1 named: %s %s", prop("vendor.gb.dgpu.state"), prop("vendor.gb.dgpu.holders"));
  put("/sys/module/nouveau/refcnt", "0\n");
  rm(FN0 "/driver");
  prop_set("vendor.gb.nouveau.result", "loading");
  CHECK(!strcmp(state_now(), "loading"), "7 loading: %s", prop("vendor.gb.dgpu.state"));
  bind_nouveau();
  CHECK(!strcmp(state_now(), "standby"), "7 loading but bound -> standby: %s", prop("vendor.gb.dgpu.state"));
  rm("/sys/bus/pci/devices/0000:02:00.0");
  rm("/sys/bus/pci/devices/0000:02:00.1");
  CHECK(!strcmp(state_now(), "unavailable") && !strcmp(prop("vendor.gb.dgpu.holders"), ""), "6 unavailable: %s", prop("vendor.gb.dgpu.state"));
  prop_set("vendor.gb.nouveau.result", "crashloop");
  CHECK(!strcmp(state_now(), "crashloop"), "5 crashloop: %s", prop("vendor.gb.dgpu.state"));
  prop_set("vendor.gb.nouveau.mode", "disabled");
  CHECK(!strcmp(state_now(), "disabled"), "4 disabled: %s", prop("vendor.gb.dgpu.state"));
  prop_set("vendor.gb.nouveau.mode", "bogus");
  CHECK(!strcmp(state_now(), "crashloop"), "unknown mode ignored: %s", prop("vendor.gb.dgpu.state"));
  g_cmd_kill = 1;
  CHECK(!strcmp(state_now(), "disabled"), "4 cmdline kill switch -> disabled: %s", prop("vendor.gb.dgpu.state"));
  g_dec.phase = DEC_REFCNT;
  CHECK(!strcmp(state_now(), "detaching"), "3 detaching: %s", prop("vendor.gb.dgpu.state"));
  snprintf(g_fault, sizeof g_fault, "d0-idle");
  CHECK(!strcmp(state_now(), "fault"), "2 fault: %s", prop("vendor.gb.dgpu.state"));
  put("/dev/gb_dgpu/wedged", "1\n");
  CHECK(!strcmp(state_now(), "wedged"), "1 wedged (file only): %s", prop("vendor.gb.dgpu.state"));
  g_dec.phase = DEC_NONE;
  /* the cmdline parser (D17): unknown values and module_blacklist are the kill switch, last occurrence wins */
  static const struct { const char *cmd; int kill; } K[] = {
    {"quiet gb.nouveau=once", 0}, {"gb.nouveau=test", 0}, {"gb.nouveau=0", 1}, {"gb.nouveau=bogus", 1},
    {"gb.nouveau=once gb.nouveau=0", 1}, {"gb.nouveau=0 gb.nouveau=once", 0}, {"module_blacklist=a,nouveau,b", 1},
    {"module_blacklist=nouveau module_blacklist=a", 1}, {"module_blacklist=a module_blacklist=b", 0}, {"", 0}};
  for (unsigned i = 0; i < sizeof K / sizeof K[0]; i++) {
    put("/proc/cmdline", K[i].cmd[0] ? K[i].cmd : "x");
    g_cmd_kill = 0;
    read_cmdline();
    CHECK(g_cmd_kill == K[i].kill, "cmdline '%s' -> kill %d", K[i].cmd, g_cmd_kill);
  }
  put("/proc/cmdline", "root=x gb.boot=usb gb.dgpu=keep");
  read_cmdline();
  CHECK(!strcmp(g_boot, "usb") && !g_dgpu_on, "gb.boot / gb.dgpu=keep parsed");
  g_dgpu_on = 1;
}

/* hot attach (7.9), confirm watchdog, echo timeout, optional IOMMU domain */
static void test_misc(void) {
  /* v1.2: 7.9 is replaced by 23.7 (keyed on the graphics mode and a new appearance; GP-1B-10 has the full matrix) */
  set_root("tree_hotattach");
  fake_pci(0, NULL);
  rm("/sys/bus/pci/devices/0000:02:00.0");
  rm("/sys/bus/pci/devices/0000:02:00.1");
  prop_set("vendor.gb.nouveau.booted", "1");
  prop_set("vendor.gb.nouveau.result", "refused:nodgpu");
  dtx_up(0);
  status_tick(T);
  CHECK(!strcmp(prop("ctl.start"), ""), "dGPU absent at start: nothing started");
  fns_back();
  status_tick(T);
  CHECK(!strcmp(prop("ctl.start"), "gb_nouveau"), "23.7 hot attach (gfx_mode unset = auto) starts gb_nouveau");
  prop_set("ctl.start", "");
  status_tick(T);
  CHECK(!strcmp(prop("ctl.start"), ""), "once per appearance");
  set_root("tree_hotattach_no");
  fake_pci(0, NULL);
  rm("/sys/bus/pci/devices/0000:02:00.0");
  rm("/sys/bus/pci/devices/0000:02:00.1");
  prop_set("vendor.gb.nouveau.booted", "1");
  prop_set("persist.vendor.gb.gfx_mode", "intel");
  dtx_up(0);
  status_tick(T);
  fns_back();
  status_tick(T);
  CHECK(!strcmp(prop("ctl.start"), ""), "gfx_mode intel: no start");

  /* slow-path confirm the EC ignores (latch never opens): ready again after 10 s, functions rescanned */
  set_root("tree_confirm_wd");
  fake_pci(0, NULL);
  prop_set("vendor.gb.nouveau.used", "1");
  dtx_up(1);
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);                      /* a stray REQUEST event (no EC process in the model) */
  settle();
  CHECK(g_dtx.ec == EC_CONFIRMED && strstr(ec.log, "CONFIRM") && g_dtx.latch == SDTX_LATCH_CLOSED, "confirm sent, latch closed");
  k_restore_at = 1;
  pump(12000, 200);
  CHECK(g_dtx.ec == EC_READY && strstr(ec.log, "rescan=1") && g_inv.n == 2, "confirm watchdog -> ready -> rescan '%s'", ec.log);
  CHECK(logpos("CANCEL") > logpos("CONFIRM") && logpos("CANCEL") < logpos("rescan=1"), "confirm watchdog: fresh query, LATCH_CANCEL before the rescan '%s'", ec.log);

  /* quick path, a driver binds between the heartbeat and the confirm: the last look diverts to the checked slow path */
  set_root("tree_quick_race");
  fake_pci(0, NULL);
  notify_dir();
  dtx_up(1);
  ec.log[0] = 0;
  g_bind_on_hb = 1;
  press();
  settle();
  int u1 = logpos("UNBIND:0000:02:00.1"), rr = logpos("REMOVE:0000:02:00.0"), cf = logpos("CONFIRM");
  CHECK(u1 > logpos("HB") && rr > u1 && cf > rr && g_inv.n == 0, "late bind before the confirm -> slow path: '%s'", ec.log);
  /* a driver binding while a quick-path detach is under way (confirmed / latch open) is unbound again; once the latch
   * closes (detach over) nouveau is kept as usual */
  set_root("tree_late_bind");
  fake_pci(0, NULL);
  dtx_up(1);
  press();
  CHECK(g_dtx.ec == EC_CONFIRMED && g_dtx.latch == SDTX_LATCH_OPENED, "quick path confirmed, latch open");
  ec.log[0] = 0;
  bind_nouveau();
  dgpu_enforce("hot-plug", 0);
  settle();
  CHECK(strstr(ec.log, "UNBIND:0000:02:00.0") && !linked("0000:02:00.0"), "late nouveau bind while the latch is open (refcnt 0): unbound '%s'", ec.log);
  bind_nouveau();
  put("/sys/module/nouveau/refcnt", "1\n");
  ec.log[0] = 0;
  g_latch = 0;
  dgpu_enforce("hot-plug", 0);
  settle();
  CHECK(linked("0000:02:00.0") && !strstr(ec.log, "UNBIND") && strstr(ec.log, "LOCK"),
        "late nouveau bind in use while the latch is open: never past the refcnt gate, latch locked '%s'", ec.log);
  put("/sys/module/nouveau/refcnt", "0\n");
  ec.latch = SDTX_LATCH_CLOSED;
  ev(SDTX_EVENT_LATCH_STATUS, SDTX_LATCH_CLOSED);
  bind_nouveau();
  ec.log[0] = 0;
  dgpu_enforce("hot-plug", 0);
  settle();
  CHECK(linked("0000:02:00.0") && !strstr(ec.log, "UNBIND"), "detach over: nouveau kept again '%s'", ec.log);

  /* a detach pressed while a staged rescan waits for its retry: the rescan stops, the detach proceeds */
  set_root("tree_rescan_vs_detach");
  fake_pci(0, NULL);
  rm("/sys/bus/pci/devices/0000:02:00.0");
  rm("/sys/bus/pci/devices/0000:02:00.1");
  prop_set("vendor.gb.nouveau.used", "1");
  dtx_up(1);
  pump(1000, 100);
  CHECK(g_rs.active && g_rs.phase == RS_RETRY, "rescan waiting for its retry");
  ec.log[0] = 0;
  press();
  settle();
  pump(20000, 500);
  CHECK(!g_rs.active && !strstr(ec.log, "rescan=1") && strstr(ec.log, "CONFIRM") && !g_fault[0],
        "detach during a rescan: no further rescan writes, confirmed (empty bus) '%s'", ec.log);

  /* echo timeout after our LATCH_CANCEL */
  set_root("tree_echo");
  fake_pci(0, NULL);
  put("/dev/gb_dgpu/wedged", "1\n");
  dtx_up(0);
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(g_dtx.ec == EC_IN_PROGRESS && g_dtx.echo_until > 0, "wedged: CANCEL, waiting for the echo");
  pump(11000, 500);
  CHECK(g_dtx.ec == EC_READY, "no echo within 10 s -> ready");

  /* quick path, LATCH_CONFIRM fails: not confirmed; the detaching flag stays until the echo wait ends, then ready */
  set_root("tree_confirm_fail");
  fake_pci(0, NULL);
  dtx_up(0);
  ec.log[0] = 0;
  g_fail_confirm = 1;
  ev(SDTX_EVENT_REQUEST, 0);
  g_fail_confirm = 0;
  CHECK(!strcmp(ec.log, "HB CONFIRM ") && g_dtx.ec == EC_IN_PROGRESS && g_dtx.echo_until > 0 &&
        exists("/dev/gb_dgpu/detaching"), "quick-path confirm failed: not confirmed, flag kept, echo wait '%s' ec %d",
        ec.log, g_dtx.ec);
  pump(11000, 500);
  CHECK(g_dtx.ec == EC_READY && !exists("/dev/gb_dgpu/detaching"), "quick-path confirm failed: ready after the wait, flag gone");

  /* optional DMA-FQ group (compile-time flag, here flipped by the test) */
  set_root("tree_iommu");
  fake_pci(0, NULL);
  put("/sys/kernel/iommu_groups/12/type", "identity\n");
  lnk("../../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.0", "/sys/kernel/iommu_groups/12/devices/0000:02:00.0");
  lnk("../../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.1", "/sys/kernel/iommu_groups/12/devices/0000:02:00.1");
  lnk("../../../kernel/iommu_groups/12", FN0 "/iommu_group");
  status_tick(T);
  CHECK(!g_wk.busy && !strcmp(get("/sys/kernel/iommu_groups/12/type"), "identity"), "flag off: untouched");
  g_iommu_dmafq = 1;
  status_tick(T);
  settle();
  CHECK(!strcmp(get("/sys/kernel/iommu_groups/12/type"), "DMA-FQ"), "flag on: DMA-FQ by the worker (%s)", get("/sys/kernel/iommu_groups/12/type"));
  set_root("tree_iommu_mixed");
  fake_pci(0, NULL);
  put("/sys/kernel/iommu_groups/3/type", "identity\n");
  lnk("../../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.0", "/sys/kernel/iommu_groups/3/devices/0000:02:00.0");
  lnk("../../../../devices/pci0000:00/0000:00:1d.4", "/sys/kernel/iommu_groups/3/devices/0000:00:1d.4");
  lnk("../../../kernel/iommu_groups/3", FN0 "/iommu_group");
  g_iommu_dmafq = 1;
  status_tick(T);
  settle();
  CHECK(!strcmp(get("/sys/kernel/iommu_groups/3/type"), "identity"), "group with non-NVIDIA devices: untouched");
}


/* R2-2: no confirm while a gb_nouveau.sh load window is open (ESP lock owner gb_nouveau, live pid); detaching flag */
static void test_load_window(void) {
  set_root("tree_lw");
  fake_pci(0, NULL);
  notify_dir();
  dtx_up(1);
  put("/proc/4300/comm", "sh\n");
  put("/dev/gb_esp.lock/owner", "4300 gb_nouveau 95\n");
  ec.log[0] = 0;
  press();
  CHECK(strstr(ec.log, "CANCEL") && !strstr(ec.log, "CONFIRM") && !strstr(ec.log, "HB") && notified(N15_TEXT),
        "load window open: refused at once with N15 '%s'", ec.log);
  CHECK(g_dtx.ec == EC_READY && !exists("/dev/gb_dgpu/detaching"), "echo -> ready, detaching flag removed");
  put("/dev/gb_esp.lock/owner", "4300 gb_bootlog 95\n");   /* another lock holder: not a load window */
  ec.log[0] = 0;
  press();
  CHECK(!strcmp(ec.log, "HB CONFIRM "), "bootlog holds the ESP lock: quick path '%s'", ec.log);
  CHECK(exists("/dev/gb_dgpu/detaching"), "quick-path confirm: detaching flag kept until the latch closes");
  ec.latch = SDTX_LATCH_CLOSED;
  ev(SDTX_EVENT_LATCH_STATUS, SDTX_LATCH_CLOSED);
  CHECK(!exists("/dev/gb_dgpu/detaching") && g_dtx.ec == EC_READY, "latch closed: flag removed");
  put("/dev/gb_esp.lock/owner", "4399 gb_nouveau 95\n");   /* dead owner pid */
  ec.log[0] = 0;
  press();
  CHECK(!strcmp(ec.log, "HB CONFIRM "), "stale owner (dead pid): quick path '%s'", ec.log);
  ec.latch = SDTX_LATCH_CLOSED;
  ev(SDTX_EVENT_LATCH_STATUS, SDTX_LATCH_CLOSED);
  rm("/dev/gb_esp.lock/owner");
  /* slow path: the load window opens during the decision -> waits, then N15, never an unbind */
  set_root("tree_lw_slow");
  fake_pci(1, NULL);
  notify_dir();
  dtx_up(1);
  status_tick(T);
  put("/sys/module/nouveau/refcnt", "1\n");
  ec.log[0] = 0;
  press();
  pump(2000, 250);
  put("/proc/4300/comm", "sh\n");
  put("/dev/gb_esp.lock/owner", "4300 gb_nouveau 95\n");
  put("/sys/module/nouveau/refcnt", "0\n");
  pump(10000, 250);
  CHECK(dec_running() && g_dec.phase == DEC_REFCNT && !strstr(ec.log, "UNBIND"), "refcnt 0 but load window open: still waiting");
  pump(22000, 250);
  CHECK(!dec_running() && strstr(ec.log, "CANCEL") && !strstr(ec.log, "UNBIND") && !strstr(ec.log, "CONFIRM") && notified(N15_TEXT),
        "load window for the whole wait: refused with N15 '%s'", ec.log);
  /* a stale detaching flag of a crashed instance is removed at start */
  set_root("tree_lw_stale");
  fake_pci(0, NULL);
  put("/dev/gb_dgpu/detaching", "1\n");
  dtx_up(0);
  CHECK(!exists("/dev/gb_dgpu/detaching"), "stale detaching flag removed at DTX start");
}

/* R2-1: split unbind/remove with the refcnt gate re-checked between the jobs and in the child; users stopped;
 * nodes that appear during the decision are locked; an unreadable refcnt while loaded counts as busy */
static void test_gate_split(void) {
  set_root("tree_split");
  fake_pci(1, NULL);
  notify_dir();
  dtx_up(1);
  status_tick(T);
  k_refcnt_after_unbind = 1;                      /* a root opener grabs nouveau right after the unbind */
  ec.log[0] = 0;
  press();
  settle();
  CHECK(strstr(ec.log, "UNBIND:0000:02:00.0") && !strstr(ec.log, "REMOVE") && strstr(ec.log, "CANCEL") && !strstr(ec.log, "CONFIRM"),
        "refcnt rose between the unbind and the remove: refused before any remove '%s'", ec.log);
  CHECK(notified("Close them, then press detach again."), "N08 after the re-check");
  /* op_gate in the worker child, called directly */
  set_root("tree_gate");
  fake_pci(1, NULL);
  struct wop o;
  memset(&o, 0, sizeof o);
  o.kind = OP_UNBIND;
  snprintf(o.bdf, sizeof o.bdf, "0000:02:00.0");
  rpath(o.path, sizeof o.path, "/sys/bus/pci/devices/0000:02:00.0/driver/unbind");
  CHECK(op_gate(&o) == 0, "gate: nouveau refcnt 0 -> unbind allowed");
  put("/sys/module/nouveau/refcnt", "1\n");
  CHECK(op_gate(&o) == -EBUSY, "gate: nouveau refcnt 1 -> unbind refused");
  put("/sys/module/nouveau/refcnt", "x\n");
  CHECK(op_gate(&o) == -EBUSY, "gate: unreadable refcnt while loaded -> refused (fail closed)");
  bind_drv(FN1, "snd_hda_intel");
  snprintf(o.bdf, sizeof o.bdf, "0000:02:00.1");
  rpath(o.path, sizeof o.path, "/sys/bus/pci/devices/0000:02:00.1/driver/unbind");
  CHECK(op_gate(&o) == 0, "gate: a non-nouveau driver is unbound whatever the refcnt");
  o.kind = OP_REMOVE;
  snprintf(o.bdf, sizeof o.bdf, "0000:02:00.0");
  rpath(o.path, sizeof o.path, "/sys/bus/pci/devices/0000:02:00.0/remove");
  put("/sys/module/nouveau/refcnt", "0\n");
  CHECK(op_gate(&o) == -EBUSY, "gate: remove of a bound function refused");
  rm(FN0 "/driver");
  put("/sys/module/nouveau/refcnt", "1\n");
  CHECK(op_gate(&o) == -EBUSY, "gate: remove while nouveau is held refused");
  put("/sys/module/nouveau/refcnt", "0\n");
  CHECK(op_gate(&o) == 0, "gate: driverless + refcnt 0 -> remove allowed");
  rm("/sys/module/nouveau/refcnt");
  rmdir(rpath((char[PATH_MAX]){0}, PATH_MAX, "/sys/module/nouveau"));
  CHECK(op_gate(&o) == 0, "gate: nouveau not loaded -> remove allowed");
  o.kind = OP_WRITE;
  CHECK(op_gate(&o) == 0, "gate: plain writes are not gated");
  /* a gated op result ends the decision with N08 (synthetic job result) */
  set_root("tree_gated_job");
  fake_pci(1, NULL);
  notify_dir();
  dtx_up(1);
  status_tick(T);
  put("/sys/module/nouveau/refcnt", "1\n");
  press();
  pump(1000, 250);
  struct job j;
  memset(&j, 0, sizeof j);
  j.kind = J_DETACH;
  j.nops = 1;
  j.op[0].kind = OP_UNBIND;
  j.op[0].res = -EBUSY;
  snprintf(j.op[0].bdf, sizeof j.op[0].bdf, "0000:02:00.0");
  g_dec.phase = DEC_UNBIND;
  ec.log[0] = 0;
  decision_worker_done(&j, 0);
  CHECK(!dec_running() && strstr(ec.log, "CANCEL") && mode_of("/dev/dri/renderD129") == 0660, "gate refusal in the worker: canceled, permissions restored '%s'", ec.log);
  /* GPU user services are stopped and waited for; new nodes are locked during the decision */
  set_root("tree_svc");
  fake_pci(1, NULL);
  notify_dir();
  dtx_up(1);
  status_tick(T);
  prop_set("init.svc.gb_nvk_probe", "running");
  g_ctl_log[0] = 0;
  ec.log[0] = 0;
  press();
  CHECK(strstr(g_ctl_log, "ctl.stop=gb_dgpu_test") && strstr(g_ctl_log, "ctl.stop=gb_nvk_probe") && strstr(g_ctl_log, "ctl.stop=gb_gpu_compute"),
        "decision stops gb_dgpu_test, gb_nvk_probe, gb_gpu_compute: '%s'", g_ctl_log);
  CHECK(strstr(ec.log, "LOCK"), "latch locked at decision start '%s'", ec.log);
  pump(3000, 250);
  CHECK(dec_running() && g_dec.phase == DEC_REFCNT && !strstr(ec.log, "UNBIND"), "waits while gb_nvk_probe still runs");
  bind_nouveau();                                 /* ueventd re-created the nodes (new inode, 0666) meanwhile */
  pump(1000, 250);
  CHECK(mode_of("/dev/dri/renderD129") == 0 && mode_of("/dev/dri/card1") == 0, "a node that appears during the decision is chmod 000 (%o)", mode_of("/dev/dri/renderD129"));
  prop_set("init.svc.gb_nvk_probe", "stopped");
  pump(1000, 250);
  settle();
  int ub = logpos("UNBIND:0000:02:00.0"), rv = logpos("REMOVE:0000:02:00.0"), cf = logpos("CONFIRM");
  CHECK(ub >= 0 && rv > ub && cf > rv, "probe stopped: unbind job, then remove job, then confirm '%s'", ec.log);
  /* unreadable refcnt while nouveau is loaded: busy for the whole wait */
  set_root("tree_rcbad");
  fake_pci(1, NULL);
  notify_dir();
  dtx_up(1);
  status_tick(T);
  put("/sys/module/nouveau/refcnt", "x\n");
  ec.log[0] = 0;
  press();
  pump(31000, 500);
  CHECK(!dec_running() && strstr(ec.log, "CANCEL") && !strstr(ec.log, "UNBIND") && !strstr(ec.log, "CONFIRM"), "unreadable refcnt counts as busy '%s'", ec.log);
}

/* R2-3: during the echo wait after a busy refusal (EC_IN_PROGRESS, no decision) nouveau is never unbound */
static void test_release_window(void) {
  set_root("tree_release");
  fake_pci(1, NULL);
  notify_dir();
  dtx_up(0);                                      /* no EC model: the echo never comes */
  status_tick(T);
  put("/sys/module/nouveau/refcnt", "2\n");
  press();
  pump(31000, 500);
  CHECK(!dec_running() && g_dtx.ec == EC_IN_PROGRESS && g_dtx.echo_until > 0, "busy refusal, waiting for the echo");
  put("/sys/module/nouveau/refcnt", "0\n");
  ec.log[0] = 0;
  dgpu_enforce("hot-plug", 0);
  settle();
  CHECK(linked("0000:02:00.0") && !strstr(ec.log, "UNBIND"), "echo wait: gb_power never unbinds nouveau '%s'", ec.log);
}

/* R1-M3 / GP-30, rewritten for v1.2 (TASKS_1B 9): a re-bind after "off" is undone by reconcile_off() when the graphics
 * mode is intel (refcnt 0 on two ticks, 10 s apart at least); in auto it stays (review B4). GP-1B-09 has the rest. */
static void test_off_rebind(void) {
  set_root("tree_offrebind");
  fake_pci(0, NULL);
  put("/dev/gb_dgpu/off", "1\n");
  prop_set("persist.vendor.gb.gfx_mode", "intel");
  status_tick(T);
  g_ctl_log[0] = 0;
  bind_nouveau();                                 /* a rescan or pciehp re-created the function, the module probed it */
  status_tick(T);
  CHECK(!strstr(g_ctl_log, "gb_nouveau_off"), "first tick with refcnt 0: not yet '%s'", g_ctl_log);
  T += 2000;
  status_tick(T);
  CHECK(count_in(g_ctl_log, "ctl.start=gb_nouveau_off") == 1, "re-bind in Intel mode: gb_nouveau_off started '%s'", g_ctl_log);
  T += 2000;
  status_tick(T);
  CHECK(count_in(g_ctl_log, "gb_nouveau_off") == 1, "not again within 10 s");
  T += 9000;
  status_tick(T);
  CHECK(count_in(g_ctl_log, "gb_nouveau_off") == 2, "still bound 10 s later: started again");
  prop_set("persist.vendor.gb.gfx_mode", "auto"); /* the user picked Auto again: the bind is wanted */
  for (int i = 0; i < 4; i++) { T += 11000; status_tick(T); }
  CHECK(count_in(g_ctl_log, "gb_nouveau_off") == 2, "gfx_mode auto: the re-bind stays (review B4)");
  CHECK(!strstr(ec.log, "UNBIND"), "gb_power never unbinds nouveau itself '%s'", ec.log);
  set_root("tree_offrebind_no");
  fake_pci(0, NULL);
  bind_nouveau();
  for (int i = 0; i < 3; i++) { T += 11000; status_tick(T); }
  CHECK(!strstr(g_ctl_log, "gb_nouveau_off"), "gfx_mode unset (auto): nothing started");
}

/* R2 low: a mounted base drive keeps the latch locked (also on SIGTERM) and is re-checked right before the confirm */
static void test_bootdisk_latch(void) {
  set_root("tree_disklock");
  fake_pci(0, NULL);
  notify_dir();
  blk_tree();
  g_blkdev = fake_blkdev;
  put("/proc/mounts", "/dev/block/nvme0n1p5 /data ext4 rw 0 0\n");
  dtx_up(1);
  status_tick(T);
  CHECK(g_latch == 0, "internal disk only: unlocked");
  put("/proc/mounts", "/dev/block/nvme0n1p5 /data ext4 rw 0 0\n/dev/block/sda1 /mnt/media_rw/1234-ABCD vfat rw 0 0\n");
  ec.log[0] = 0;
  status_tick(T);
  CHECK(!strcmp(ec.log, "LOCK ") && g_bootdisk && g_latch == 1, "a mounted base drive locks the latch '%s'", ec.log);
  term_handle();
  CHECK(!strstr(ec.log, "UNLOCK"), "SIGTERM with a base drive mounted: stays locked '%s'", ec.log);
  put("/proc/mounts", "/dev/block/nvme0n1p5 /data ext4 rw 0 0\n");
  status_tick(T);
  CHECK(strstr(ec.log, "UNLOCK") && g_latch == 0, "unmounted: unlocked '%s'", ec.log);
  prop_set("vendor.gb.nouveau.used", "1");
  ec.log[0] = 0;
  press();                                        /* slow path (used); the stick is plugged in during the decision */
  put("/proc/mounts", "/dev/block/nvme0n1p5 /data ext4 rw 0 0\n/dev/block/sda1 /mnt/media_rw/1234-ABCD vfat rw 0 0\n");
  settle();
  CHECK(strstr(ec.log, "REMOVE") && !strstr(ec.log, "CONFIRM") && strstr(ec.log, "CANCEL") &&
        notified("A USB or SD drive in the keyboard base is in use."), "boot-disk guard re-run before the confirm '%s'", ec.log);
  g_blkdev = real_blkdev;
}

/* ---- control socket */
static const struct { int uid, pid, role; } ROLES[] = {
  {0, 100, R_ROOT}, {2000, 101, R_SHELL}, {10123, 102, R_UI}, {9999, 777, R_CSVC}, {10200, 103, R_CCLIENT}, {10500, 104, R_OTHER}};
static void roles_tree(const char *name) {
  set_root(name);
  fake_pci(1, NULL);
  put("/odm/etc/gb_hw", "native\n");
  put("/odm/lib/modules/nouveau.ko", "ko");
  put("/odm/app/GbGpu/GbGpu.apk", "apk");
  pkgs();
  put("/data/vendor/gb/compute_allow", "# test\n10200 com.example.chat\n");
  lnk("/vendor/bin/gb_gpu_compute", "/proc/777/exe");
  lnk("/system/bin/app_process64", "/proc/778/exe");
  notify_dir();
  status_tick(T);
}
static void test_socket_roles(void) {
  roles_tree("tree_roles");
  for (unsigned r = 0; r < sizeof ROLES / sizeof ROLES[0]; r++) {
    int fd = cl_pair(ROLES[r].uid, ROLES[r].pid);
    CHECK(g_cl[g_ncl - 1].role == ROLES[r].role, "uid %d -> role %s", ROLES[r].uid, role_names[g_cl[g_ncl - 1].role]);
    drop(fd);
  }
  int fd = cl_pair(1010123, 300);                 /* GbGpu in a secondary user */
  CHECK(g_cl[g_ncl - 1].role == R_UI, "ui in user 10");
  drop(fd);
  fd = cl_pair(9999, 778);                        /* nobody, but not the compute binary */
  CHECK(g_cl[g_ncl - 1].role == R_OTHER, "uid 9999 other exe -> other");
  drop(fd);
  /* GP-17: every command x every role */
  static const struct { const char *line, *name; unsigned roles; } MX[] = {
    {"hello\n", "hello", RALL}, {"status\n", "status", RSU}, {"watch\n", "watch", RSU}, {"holders\n", "holders", RSU},
    {"enable\n", "enable", RSU}, {"disable\n", "disable", RSU}, {"detach-request\n", "detach-request", RSU},
    {"report\n", "report", RSU}, {"stop-compute\n", "stop-compute", RSU}, {"close-holders 4242\n", "close-holders", RSU},
    {"compute-list\n", "compute-list", RSU}, {"compute-allow com.example.game\n", "compute-allow", RSU},
    {"compute-deny com.example.game\n", "compute-deny", RSU}, {"compute-start\n", "compute-start", RSU | RB(R_CCLIENT)},
    {"compute-state idle\n", "compute-state", RB(R_CSVC)},
    /* v1.2 (26.1, GP-1B-20): root, shell, ui; compute-svc, compute-client and other get EPERM */
    {"get-mode\n", "get-mode", RSU}, {"set-mode auto\n", "set-mode", RSU}, {"set-app-policy games\n", "set-app-policy", RSU},
    {"set-app-intel com.example.game:1\n", "set-app-intel", RSU}, {"set-app-nvidia com.example.game:0\n", "set-app-nvidia", RSU},
    {"get-apps\n", "get-apps", RSU}, {"apps-begin 0\n", "apps-begin", RSU}, {"apps-add g:com.example.game\n", "apps-add", RSU},
    {"apps-commit 0\n", "apps-commit", RSU}, {"run-selftest\n", "run-selftest", RSU}, {"list-holders\n", "list-holders", RSU},
    {"close-holders-and-detach 4242\n", "close-holders-and-detach", RSU}, {"ui-caps none\n", "ui-caps", RSU}};
  int bad = 0;
  for (unsigned m = 0; m < sizeof MX / sizeof MX[0]; m++)
    for (unsigned r = 0; r < sizeof ROLES / sizeof ROLES[0]; r++) {
      fd = cl_pair(ROLES[r].uid, ROLES[r].pid);
      const char *rep = req(fd, MX[m].line);
      char want[64];
      snprintf(want, sizeof want, "{\"v\":1,\"ok\":%s,\"cmd\":\"%s\"", strstr(rep, "\"ok\":true") ? "true" : "false",
               !strcmp(MX[m].line, "watch\n") && strstr(rep, "\"ok\":true") ? "status" : MX[m].name);
      int allowed = !!(MX[m].roles & RB(ROLES[r].role)), eperm = strstr(rep, "\"err\":\"EPERM\"") != NULL;
      if (eperm == allowed || strncmp(rep, want, strlen(want))) {
        bad++;
        printf("FAIL matrix: %s as %s -> %s\n", MX[m].name, role_names[ROLES[r].role], rep);
      }
      drop(fd);
    }
  CHECK(!bad, "GP-17/GP-1B-20 command x role matrix (%u commands x %u roles): %d mismatches",
        (unsigned)(sizeof MX / sizeof MX[0]), (unsigned)(sizeof ROLES / sizeof ROLES[0]), bad);
}
static void test_socket_golden(void) {
  roles_tree("tree_golden");
  prop_set("vendor.gb.nouveau.booted", "1");
  prop_set("vendor.gb.nouveau.mode", "default");
  int ui = cl_pair(10123, 102), other = cl_pair(10500, 104);
  CHECK(!strcmp(req(ui, "hello\n"), "{\"v\":1,\"ok\":true,\"cmd\":\"hello\",\"api\":1,\"role\":\"ui\",\"uid\":10123,\"server\":\"gb_power 1\"}"), "golden hello");
  CHECK(!strcmp(req(ui, "frobnicate\n"), "{\"v\":1,\"ok\":false,\"cmd\":\"frobnicate\",\"err\":\"ENOSYS\",\"msg\":\"unknown command\"}"), "golden ENOSYS");
  CHECK(!strcmp(req(other, "enable\n"), "{\"v\":1,\"ok\":false,\"cmd\":\"enable\",\"err\":\"EPERM\",\"msg\":\"not allowed\"}"), "golden EPERM");
  CHECK(!strcmp(prop("persist.vendor.gb.gfx_mode"), "") && !strcmp(prop("persist.vendor.gb.nouveau"), ""), "EPERM: nothing set");
  CHECK(!strcmp(req(ui, "enable\n"), "{\"v\":1,\"ok\":true,\"cmd\":\"enable\",\"effect\":\"now\"}"), "golden enable now");
  /* v1.2 (TASKS_1B 9, GP-17): enable = set-mode auto; the retired persist.vendor.gb.nouveau is never written */
  CHECK(!strcmp(prop("persist.vendor.gb.gfx_mode"), "auto") && !strcmp(prop("persist.vendor.gb.nouveau"), ""),
        "enable sets gfx_mode=auto (not persist.vendor.gb.nouveau)");
  CHECK(!strcmp(req(ui, "close-holders 1,abc\n"), "{\"v\":1,\"ok\":false,\"cmd\":\"close-holders\",\"err\":\"EINVAL\",\"msg\":\"bad pid list\"}"), "golden EINVAL");
  drop(ui);
  drop(other);
}
static void test_socket_cmds(void) {
  roles_tree("tree_cmds");
  int c = cl_pair(0, 100);
  const char *r = req(c, "status\n");
  /* v1.2: persist = the graphics mode is not intel (unset = auto -> 1); gfxdriver = gfx_cap */
  static const char *const keys[] = {"\"state\":\"standby\"", "\"n\":0", "\"holders\":[]", "\"mode\":", "\"result\":", "\"persist\":1",
                                     "\"clean_boots\":0", "\"probation\":0", "\"used\":1", "\"foreign\":0", "\"wedged\":0",
                                     "\"fault_reason\":\"\"", "\"bdf\":\"0000:02:00.0\"", "\"driver\":\"nouveau\"",
                                     "\"runtime_status\":\"suspended\"", "\"power_state\":\"D3cold\"", "\"refcnt\":0",
                                     "\"latch\":\"unknown\"", "\"base\":\"unknown\"", "\"dtx\":\"n/a\"", "\"compute\":\"stopped\"",
                                     "\"payload\":\"\"", "\"gfxdriver\":0", "\"t_ms\":"};
  int miss = 0;
  for (unsigned i = 0; i < sizeof keys / sizeof keys[0]; i++) if (!strstr(r, keys[i])) { miss++; printf("missing %s in %s\n", keys[i], r); }
  CHECK(!miss && strlen(r) < 4095, "status object fields (%d missing)", miss);
  prop_set("vendor.gb.nouveau.mode", "auto");     /* v1.2: probation for auto boots (v1.1: persist) */
  prop_set("vendor.gb.nouveau.clean", "2");
  prop_set("vendor.gb.dgpu.payload", "mounted");
  prop_set("vendor.gb.nouveau.foreign", "1");
  r = req(c, "status\n");
  CHECK(strstr(r, "\"probation\":1") && strstr(r, "\"clean_boots\":2") && strstr(r, "\"payload\":\"mounted\"") && strstr(r, "\"foreign\":1"), "probation/payload/foreign: %s", r);
  prop_set("vendor.gb.nouveau.mode", "persist");  /* the v1.1 value is no longer produced: no probation */
  T += 1000;
  CHECK(strstr(req(c, "status\n"), "\"probation\":0"), "mode persist: probation 0 (v1.2)");
  prop_set("vendor.gb.dgpu.payload", "error:bogus\"x");
  r = req(c, "status\n");
  CHECK(strstr(r, "\"payload\":\"\""), "unknown payload value not echoed");
  CHECK(strstr(req(c, "status extra\n"), "\"cmd\":\"status\",\"err\":\"EINVAL\""), "unexpected argument");
  /* enable / disable effects. v1.2 (26.2): enable = set-mode auto, disable = set-mode intel; once/test boots come from
   * the command line (gb_power's own read, not vendor.gb.nouveau.mode); next-attach and pending are reported as now */
  prop_set("vendor.gb.nouveau.booted", "1");
  put("/proc/cmdline", "root=x gb.nouveau=once");
  read_cmdline();
  CHECK(strstr(req(c, "enable\n"), "\"effect\":\"next-boot\""), "enable in a once boot: next-boot");
  put("/proc/cmdline", "root=x");
  read_cmdline();
  CHECK(strstr(req(c, "disable\n"), "\"effect\":\"now\"") && !strcmp(prop("persist.vendor.gb.gfx_mode"), "intel") &&
        !strcmp(prop("persist.vendor.gb.nouveau"), ""), "disable now: gfx_mode=intel (persist.vendor.gb.nouveau never written)");
  put("/proc/cmdline", "root=x gb.nouveau=test");
  read_cmdline();
  CHECK(strstr(req(c, "disable\n"), "\"effect\":\"next-boot\""), "disable in a test boot: next-boot");
  put("/proc/cmdline", "root=x");
  read_cmdline();
  prop_set("vendor.gb.nouveau.booted", "");
  CHECK(strstr(req(c, "enable\n"), "\"effect\":\"next-boot\""), "not booted yet: next-boot");
  prop_set("vendor.gb.nouveau.booted", "1");
  rm(FN0 "/driver");                              /* nouveau.ko loaded, dGPU driverless (off kept the module) */
  T += 1000;
  CHECK(strstr(req(c, "enable\n"), "\"effect\":\"now\""), "v1.2: module loaded without a device: now (gb_nouveau.sh's bind path, 7.2a)");
  bind_nouveau();
  T += 1000;
  CHECK(strstr(req(c, "enable\n"), "\"effect\":\"now\""), "bound: now");
  rm(FN0 "/driver");
  rm("/sys/module/nouveau/refcnt");
  rmdir(rpath((char[PATH_MAX]){0}, PATH_MAX, "/sys/module/nouveau"));
  prop_set("init.svc.gb_nouveau", "running");
  T += 1000;
  CHECK(strstr(req(c, "enable\n"), "\"effect\":\"now\"") && g_nv.deferred,
        "v1.2: a previous auto still runs: now, a deferred start is recorded (23.7, C-21)");
  prop_set("init.svc.gb_nouveau", "stopped");
  T += 1000;
  CHECK(strstr(req(c, "enable\n"), "\"effect\":\"now\""), "module absent, auto idle: now");
  put("/dev/gb_dgpu/noload", "1\n");
  CHECK(strstr(req(c, "enable\n"), "\"effect\":\"next-boot\""), "noload (crash-loop boot): next-boot");
  rm("/dev/gb_dgpu/noload");
  put("/dev/gb_dgpu/keep", "1\n");
  CHECK(strstr(req(c, "disable\n"), "\"effect\":\"next-boot\""), "keep (unhealthy load stays until restart): disable next-boot");
  rm("/dev/gb_dgpu/keep");
  bind_nouveau();
  prop_set("vendor.gb.nouveau.booted", "");
  T += 1000;
  put("/odm/etc/gb_hw", "vm\n");
  CHECK(strstr(req(c, "enable\n"), "\"err\":\"ENODEV\""), "VM: ENODEV");
  put("/odm/etc/gb_hw", "native\n");
  rm("/odm/lib/modules/nouveau.ko");
  CHECK(strstr(req(c, "enable\n"), "\"err\":\"ENODEV\""), "no module/payload: ENODEV");
  put("/odm/etc/gb/dgpu_payload.sha256", "x  dgpu-k.erofs\n");
  CHECK(strstr(req(c, "enable\n"), "\"ok\":true"), "payload mode counts as present");
  /* report, stop-compute, detach-request */
  CHECK(strstr(req(c, "report\n"), "\"ok\":true") && !strcmp(prop("vendor.gb.dgpu_report"), "1"), "report");
  CHECK(strstr(req(c, "stop-compute\n"), "\"ok\":true") && !strcmp(prop("ctl.stop"), "gb_gpu_compute"), "stop-compute");
  CHECK(strstr(req(c, "detach-request\n"), "\"err\":\"ENODEV\""), "detach-request without DTX: ENODEV");
  dtx_up(0);
  ec.log[0] = 0;
  CHECK(strstr(req(c, "detach-request\n"), "\"ok\":true") && !strcmp(ec.log, "REQ "), "detach-request -> LATCH_REQUEST '%s'", ec.log);
  g_dec.phase = DEC_REFCNT;
  CHECK(strstr(req(c, "detach-request\n"), "\"err\":\"EBUSY\""), "decision running: EBUSY");
  g_dec.phase = DEC_NONE;
  /* compute-start */
  CHECK(strstr(req(c, "compute-start\n"), "\"state\":\"starting\"") && !strcmp(prop("ctl.start"), "gb_gpu_compute"), "compute-start in standby");
  prop_set("init.svc.gb_gpu_compute", "running");
  CHECK(strstr(req(c, "compute-start\n"), "\"state\":\"running\""), "already running");
  rm(FN0 "/driver");
  T += 1000;
  CHECK(strstr(req(c, "compute-start\n"), "\"err\":\"ENODEV\""), "state off: ENODEV");
  bind_nouveau();
  snprintf(g_fault, sizeof g_fault, "rpm-error");
  T += 1000;
  CHECK(strstr(req(c, "compute-start\n"), "\"err\":\"EBUSY\""), "fault: EBUSY");
  g_fault[0] = 0;
  put("/dev/gb_dgpu/wedged", "1\n");
  CHECK(strstr(req(c, "enable\n"), "\"err\":\"EBUSY\""), "wedged: enable EBUSY");
  rm("/dev/gb_dgpu/wedged");
  g_wedged = 0;
  /* holders and close-holders */
  rm("/sys/module/nouveau/refcnt");
  rmdir(rpath((char[PATH_MAX]){0}, PATH_MAX, "/sys/module/nouveau"));
  CHECK(strstr(req(c, "holders\n"), "\"err\":\"ENODEV\""), "holders without nouveau: ENODEV");
  put("/sys/module/nouveau/refcnt", "2\n");
  proc_holder(4242, "game", 10300, 0, 1);
  proc_holder(4243, "nvk_probe", 0, 1, 0);
  proc_holder(4244, "gpu", 10123, 1, 0);          /* GbGpu itself */
  T += 2000;
  r = req(c, "holders\n");
  CHECK(strstr(r, "{\"pid\":4242,\"comm\":\"game\",\"uid\":10300,\"user\":0,\"pkg\":\"com.example.game\",\"closable\":true}") &&
        strstr(r, "{\"pid\":4243,\"comm\":\"nvk_probe\",\"uid\":0,\"user\":0,\"pkg\":\"\",\"closable\":false}"), "holders: %s", r);
  T += 2000;
  r = req(c, "close-holders 4242,4243,4244,999\n");
  CHECK(strstr(r, "\"closed\":[{\"pid\":4242,\"pkg\":\"com.example.game\",\"user\":0}]") && strstr(r, "{\"pid\":4243,\"reason\":\"system\"}") &&
        strstr(r, "{\"pid\":4244,\"reason\":\"self\"}") && strstr(r, "{\"pid\":999,\"reason\":\"not-holder\"}"), "close-holders: %s", r);
  CHECK(notified("kind=force-stop\npkg=com.example.game\nuser=0\n"), "force-stop queued");
  T += 2000;
  CHECK(strstr(req(c, "close-holders 4243,999\n"), "\"err\":\"ESTALE\""), "nothing closable: ESTALE");
  CHECK(strstr(req(c, "close-holders 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17\n"), "\"err\":\"EINVAL\""), "> 16 pids: EINVAL");
  CHECK(strstr(req(c, "close-holders 1,\n"), "\"err\":\"EINVAL\"") && strstr(req(c, "close-holders\n"), "\"err\":\"EINVAL\""), "syntax: EINVAL");
  /* compute allowlist */
  CHECK(strstr(req(c, "compute-list\n"), "\"allow\":[{\"appId\":10200,\"pkg\":\"com.example.chat\"}]"), "compute-list");
  CHECK(strstr(req(c, "compute-allow com.example.game\n"), "\"appId\":10300"), "compute-allow");
  CHECK(strstr(get("/data/vendor/gb/compute_allow"), "10300 com.example.game") && mode_of("/data/vendor/gb/compute_allow") == 0644, "allowlist file");
  put("/data/vendor/victim", "secret\n");         /* a planted symlink at the temp name is replaced, never followed */
  lnk("../victim", "/data/vendor/gb/compute_allow.tmp");
  CHECK(strstr(req(c, "compute-allow com.example.game\n"), "\"appId\":10300") && !strcmp(get("/data/vendor/victim"), "secret") &&
        strstr(get("/data/vendor/gb/compute_allow"), "10300 com.example.game"), "compute_allow.tmp symlink not followed");
  int cc = cl_pair(1010300, 400);
  CHECK(g_cl[g_ncl - 1].role == R_CCLIENT, "allowed app (user 10) is a compute-client");
  drop(cc);
  CHECK(strstr(req(c, "compute-allow com.example.nothere\n"), "\"err\":\"ENOENT\""), "not installed: ENOENT");
  CHECK(strstr(req(c, "compute-allow Bad..Name\n"), "\"err\":\"EINVAL\"") && strstr(req(c, "compute-allow\n"), "\"err\":\"EINVAL\""), "bad name: EINVAL");
  CHECK(strstr(req(c, "compute-deny com.example.game\n"), "\"ok\":true") && !strstr(get("/data/vendor/gb/compute_allow"), "com.example.game"), "compute-deny");
  CHECK(strstr(req(c, "compute-deny com.example.game\n"), "\"err\":\"ENOENT\""), "deny twice: ENOENT");
  /* packages.list change drops pairs that no longer match */
  T += 6000;
  put("/data/system/packages.list", "gb.sb3.gpu 10123 0 /d platform none 0 1\ncom.example.chat 10201 0 /d default none 0 1\n");
  status_tick(T);                                 /* the tick notices the changed packages.list */
  CHECK(!strstr(get("/data/vendor/gb/compute_allow"), "com.example.chat"), "reinstalled with a new appId: removed from the allowlist");
  drop(c);
}
static void test_socket_limits(void) {
  roles_tree("tree_limits");
  /* E2BIG: 300-byte line -> reply, then closed */
  int c = cl_pair(0, 100);
  char big[302];
  memset(big, 'a', 300);
  big[300] = '\n';
  big[301] = 0;
  CHECK(!strcmp(req(c, big), "{\"v\":1,\"ok\":false,\"cmd\":\"\",\"err\":\"E2BIG\",\"msg\":\"request too long\"}") && peer_eof(c), "GP-18 E2BIG + close");
  close(c);
  char ok254[257];
  memset(ok254, 'x', 254);
  ok254[254] = '\n';
  ok254[255] = 0;
  c = cl_pair(0, 100);
  CHECK(strstr(req(c, ok254), "\"err\":\"ENOSYS\""), "254 chars + newline is accepted (255 bytes)");
  /* garbage bytes: EINVAL, the connection stays usable */
  CHECK(!strcmp(req(c, "\x01\x02\xfe\xff\n"), "{\"v\":1,\"ok\":false,\"cmd\":\"\",\"err\":\"EINVAL\",\"msg\":\"bad request\"}"), "garbage -> EINVAL");
  CHECK(strstr(req(c, "  hello\n"), "\"err\":\"EINVAL\"") && strstr(req(c, "hello\r\n"), "\"ok\":true"), "spaces EINVAL, CRLF tolerated");
  CHECK(strstr(req(c, "hello\n"), "\"ok\":true"), "still usable after garbage");
  drop(c);
  /* 9 clients over a real listening socket: the 9th gets EBUSY and is closed */
  char sp[PATH_MAX];
  snprintf(sp, sizeof sp, "%s/ctl.sock", g_root);
  CHECK(ctl_open(sp) == 0 && g_listen >= 0 && !strcmp(prop("vendor.gb.power.api"), "1"), "listening, api=1");
  int fds[9];
  for (int i = 0; i < 9; i++) {
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    snprintf(a.sun_path, sizeof a.sun_path, "%s", sp);
    fds[i] = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (connect(fds[i], (struct sockaddr *)&a, sizeof a)) perror("connect");
    cl_accept();
  }
  char b[512];
  CHECK(g_ncl == 8 && rd_line(fds[8], b, sizeof b, 300) && strstr(b, "\"err\":\"EBUSY\"") && peer_eof(fds[8]), "GP-18 9th client: EBUSY + closed (%d clients)", g_ncl);
  CHECK(g_cl[0].role == (getuid() == 0 ? R_ROOT : R_OTHER), "real SO_PEERCRED role");
  for (int i = 0; i < 9; i++) close(fds[i]);
  serve();
  cl_sweep();
  CHECK(g_ncl == 0, "all closed");
  close(g_listen);
  g_listen = -1;
  /* 5 watchers: the 5th gets EBUSY */
  int w[5];
  for (int i = 0; i < 5; i++) w[i] = cl_pair(0, 100);
  for (int i = 0; i < 4; i++) CHECK(strstr(req(w[i], "watch\n"), "\"cmd\":\"status\""), "watcher %d", i);
  CHECK(strstr(req(w[4], "watch\n"), "\"err\":\"EBUSY\""), "GP-18 5th watcher: EBUSY");
  CHECK(strstr(req(w[4], "hello\n"), "\"ok\":true"), "5th stays a normal connection");
  for (int i = 0; i < 5; i++) drop(w[i]);
  /* idle timeout 30 s for normal connections, not for watchers */
  c = cl_pair(0, 100);
  int wt = cl_pair(0, 100);
  req(wt, "watch\n");
  T += 30001;
  run_due(T);
  cl_sweep();
  CHECK(peer_eof(c) && g_ncl == 1 && g_cl[0].watch, "idle client closed after 30 s, watcher kept");
  close(c);
  drop(wt);
  /* slow reader: never reads; dropped at 8 KiB of backlog without ever blocking the loop */
  int s = cl_pair(0, 100);
  int small = 4096;
  setsockopt(s, SOL_SOCKET, SO_RCVBUF, &small, sizeof small);
  if (send(s, "watch\n", 6, 0) != 6) {}
  serve();
  long long worst = 0;
  int k;
  for (k = 0; k < 2000 && g_ncl; k++) {
    put(FN0 "/power_state", k & 1 ? "D0\n" : "D3cold\n");
    long long a = real_now_ms();
    watch_update(T);
    long long d = real_now_ms() - a;
    if (d > worst) worst = d;
    cl_sweep();
  }
  CHECK(g_ncl == 0 && worst < 50, "GP-18 slow reader dropped after %d updates, slowest update %lld ms", k, worst);
  close(s);
}
static void test_socket_watch(void) {
  roles_tree("tree_watch");
  int w = cl_pair(10123, 102);
  char b[4096];
  CHECK(strstr(req(w, "watch\n"), "\"state\":\"standby\""), "GP-19 first status");
  watch_update(T);
  CHECK(!rd_line(w, b, sizeof b, 50), "no change, no line");
  T += 5000;
  watch_update(T);
  CHECK(!rd_line(w, b, sizeof b, 50), "t_ms alone does not count as a change");
  put("/sys/module/nouveau/refcnt", "1\n");
  status_tick(T);
  CHECK(rd_line(w, b, sizeof b, 200) && strstr(b, "\"state\":\"busy:1\""), "change -> status line");
  if (send(w, "status\n", 7, 0) != 7) {}
  serve();
  CHECK(!rd_line(w, b, sizeof b, 50), "input ignored while watching");
  T += 30000;
  run_due(T);
  int ping = 0;
  while (rd_line(w, b, sizeof b, 50)) if (!strcmp(b, "{\"v\":1,\"ok\":true,\"cmd\":\"ping\"}")) ping++;
  CHECK(ping == 1, "GP-19 ping after 30 s (%d)", ping);
  drop(w);
}
static void test_socket_ui_role(void) {
  roles_tree("tree_ui");
  int c = cl_pair(10123, 102);
  CHECK(g_cl[g_ncl - 1].role == R_UI, "GP-20 ui with APK and packages.list");
  drop(c);
  rm("/odm/app/GbGpu/GbGpu.apk");
  T += 6000;
  c = cl_pair(10123, 102);
  CHECK(g_cl[g_ncl - 1].role == R_OTHER, "APK absent: other");
  drop(c);
  put("/odm/app/GbGpu/GbGpu.apk", "apk");
  T += 6000;
  c = cl_pair(10123, 102);
  drop(c);
  put("/data/system/packages.list", "gb.sb3.gpu 10124 0 /data/user/0/gb.sb3.gpu platform:targetSdkVersion=36 none 0 1\n");
  T += 1000;                                      /* within 5 s: cached */
  c = cl_pair(10123, 102);
  CHECK(g_cl[g_ncl - 1].role == R_UI, "cached for up to 5 s");
  drop(c);
  T += 5000;
  c = cl_pair(10123, 102);
  CHECK(g_cl[g_ncl - 1].role == R_OTHER, "re-read after 5 s (mtime changed): old uid no longer ui");
  drop(c);
  c = cl_pair(10124, 102);
  CHECK(g_cl[g_ncl - 1].role == R_UI, "new appId is ui");
  drop(c);
  put("/data/system/packages.list", "gb.sb3.gpu 9000 0 /d platform none 0 1\n");
  T += 6000;
  c = cl_pair(9000, 102);
  CHECK(g_cl[g_ncl - 1].role == R_OTHER, "appId outside 10000-19999 never ui");
  drop(c);
}
static void test_socket_compute(void) {
  roles_tree("tree_compute");
  put(FN0 "/power/control", "auto\n");             /* nouveau runpm owns it */
  prop_set("init.svc.gb_gpu_compute", "running");
  status_tick(T);
  CHECK(!strcmp(prop("vendor.gb.compute.state"), "starting"), "running, no connection: starting");
  int s = cl_pair(9999, 777);
  CHECK(!strcmp(req(s, "compute-state idle\n"), "{\"v\":1,\"ok\":true,\"cmd\":\"compute-state\",\"hold\":0}"), "idle: hold 0");
  CHECK(!strcmp(prop("vendor.gb.compute.state"), "idle"), "compute.state idle");
  ec.log[0] = 0;
  CHECK(!strcmp(req(s, "compute-state loading\n"), "{\"v\":1,\"ok\":true,\"cmd\":\"compute-state\",\"hold\":1}"), "loading: hold 1");
  settle();
  CHECK(!strcmp(get(FN0 "/power/control"), "on") && strstr(ec.log, "WRITE:0000:02:00.0/power/control=on") && g_cmp.applied,
        "GP-21 hold: power/control on by the worker '%s'", ec.log);
  int s2 = cl_pair(9999, 777);
  CHECK(strstr(req(s2, "compute-state loaded\n"), "\"err\":\"EBUSY\""), "second compute connection: EBUSY");
  drop(s2);
  CHECK(strstr(req(s, "compute-state bogus\n"), "\"err\":\"EINVAL\""), "bad state");
  req(s, "compute-state serving\n");
  CHECK(!strcmp(prop("vendor.gb.compute.state"), "serving"), "compute.state serving");
  dgpu_enforce("hot-plug", 0);
  CHECK(!strcmp(get(FN0 "/power/control"), "on"), "passes leave the hold alone");
  drop(s);                                        /* EOF: the service died or exited */
  CHECK(!strcmp(get(FN0 "/power/control"), "auto") && !g_cmp.applied && !g_cmp.want, "GP-21 EOF releases the hold (auto)");
  CHECK(!strcmp(prop("vendor.gb.compute.state"), "starting"), "no connection: starting");
  prop_set("init.svc.gb_gpu_compute", "stopped");
  status_tick(T);
  CHECK(!strcmp(prop("vendor.gb.compute.state"), "stopped"), "stopped");
  s = cl_pair(9999, 777);
  req(s, "compute-state loaded\n");
  settle();
  CHECK(!strcmp(get(FN0 "/power/control"), "on"), "hold again");
  int quiet = cl_pair(0, 100);
  T += 60000;                                     /* 60 s without a request on either connection */
  run_due(T);
  cl_sweep();
  CHECK(peer_eof(quiet), "a normal client is closed after 30 s idle");
  close(quiet);
  CHECK(g_cmp.conn && g_cmp.want && g_cmp.applied && !strcmp(get(FN0 "/power/control"), "on") &&
        strstr(req(s, "compute-state loaded\n"), "\"hold\":1"), "INTERFACES 9.1: the compute-svc connection is never idle-closed (hold kept 60 s)");
  req(s, "compute-state unloading\n");
  CHECK(!strcmp(get(FN0 "/power/control"), "auto"), "unloading releases the hold");
  drop(s);
  /* a detach decision stops the compute service first */
  dtx_up(1);
  prop_set("ctl.stop", "");
  press();
  CHECK(!strcmp(prop("ctl.stop"), "gb_gpu_compute"), "detach stops gb_gpu_compute");
  settle();
}

/* GP-22 notification queue */
static void test_notify(void) {
  set_root("tree_notify");
  notify("gb_dtx", "Preparing to detach", "first");
  notify("gb_dtx", "Ready to detach", "You can remove the screen now.");
  notify("gb_dgpu", "NVIDIA GPU fault", "x\x01y\xc3\xa9");
  CHECK(g_nqn == 2, "before boot_completed: queued in memory, newest per tag (%d)", g_nqn);
  prop_set("sys.boot_completed", "1");
  nq_flush();
  CHECK(g_nqn == 2, "no directory yet: still queued");
  put("/data/vendor/gb/notify/.keep", "");
  rm("/data/vendor/gb/notify/.keep");
  nq_flush();
  CHECK(g_nqn == 0 && notified("kind=notify\ntag=gb_dtx\ntitle=Ready to detach\ntext=You can remove the screen now.\n") &&
        !notified("text=first") && notified("text=x?y??\n"), "flushed, exact content, ASCII only");
  char dir[PATH_MAX];
  DIR *d = opendir(rpath(dir, sizeof dir, "/data/vendor/gb/notify"));
  int n = 0, okname = 1, okmode = 1;
  struct dirent *e;
  while (d && (e = readdir(d))) {
    if (e->d_name[0] == '.') continue;
    n++;
    long long ms;
    int pid, seq, used_ = 0;
    if (sscanf(e->d_name, "%13lld-%d-%d.msg%n", &ms, &pid, &seq, &used_) != 3 || used_ != (int)strlen(e->d_name) || ms < 1000000000000LL || pid != getpid()) okname = 0;
    char p[PATH_MAX + 300];
    struct stat st;
    snprintf(p, sizeof p, "%s/%s", dir, e->d_name);
    if (stat(p, &st) || (st.st_mode & 0777) != 0644) okmode = 0;
  }
  if (d) closedir(d);
  CHECK(n == 2 && okname && okmode, "2 files <13-digit ms>-<pid>-<n>.msg, mode 0644 (%d %d %d)", n, okname, okmode);
  CHECK(strlen(prop("vendor.gb.notify")) == 13, "vendor.gb.notify = epoch ms (%s)", prop("vendor.gb.notify"));
  prop_set("sys.boot_completed", "");
  for (int i = 0; i < 17; i++) { char k[16]; snprintf(k, sizeof k, "fs:p%d:0", i); nq_put(k, "kind=force-stop\npkg=a.b\nuser=0\n"); }
  /* v1.2 (G14, INTERFACES 12.1): the in-memory queue holds 16 (v1.1: 8) */
  CHECK(g_nqn == 16 && !strcmp(g_nq[0].key, "fs:p1:0"), "max 16 queued, oldest dropped (%d)", g_nqn);
  /* state broadcast: only after boot, only with GbGpu, coalesced to one per 2 s */
  set_root("tree_bcast");
  fake_pci(0, NULL);
  notify_dir();
  status_tick(T);
  bind_nouveau();
  status_tick(T);
  CHECK(!notified("kind=broadcast"), "no APK: no broadcast");
  put("/odm/app/GbGpu/GbGpu.apk", "apk");
  rm(FN0 "/driver");
  T += 3000;
  status_tick(T);
  CHECK(notified("kind=broadcast\n"), "broadcast on state change");
  bind_nouveau();
  status_tick(T);
  CHECK(g_bcast_pending, "second change within 2 s: pending");
  T += 2000;
  run_due(T);
  CHECK(!g_bcast_pending, "sent after 2 s");
}

/* GP-23 --dtx-monitor: prints events, issues no write ioctls */
static void test_monitor(void) {
  set_root("tree_mon");
  char p[PATH_MAX];
  mkdirs(rpath(p, sizeof p, "/dev/surface"));
  rpath(p, sizeof p, "/dev/surface/dtx");
  CHECK(!mkfifo(p, 0600), "fifo");
  int wfd = open(p, O_RDWR | O_NONBLOCK);
  dtx_ioctl = mock_ioctl;
  ec_reset();
  unsigned char evs[] = {0, 0, SDTX_EVENT_REQUEST, 0, 2, 0, SDTX_EVENT_LATCH_STATUS, 0, 1, 0, 4, 0, SDTX_EVENT_BASE_CONNECTION, 0, 0, 0, 0, 0,
                         2, 0, SDTX_EVENT_CANCEL, 0, 0x02, 0x10};
  if (write(wfd, evs, sizeof evs) != (ssize_t)sizeof evs) perror("write");
  long long (*saved)(void) = g_clock;
  g_clock = real_now_ms;
  char out[PATH_MAX];
  snprintf(out, sizeof out, "%s/mon.txt", W);
  FILE *f = fopen(out, "w+");
  int r = dtx_monitor(f, 300);
  g_clock = saved;
  rewind(f);
  char all[4096] = "", line[512];
  while (fgets(line, sizeof line, f)) strncat(all, line, sizeof all - strlen(all) - 1);
  fclose(f);
  close(wfd);
  CHECK(r == 0 && strstr(all, "STATE base=attached") && strstr(all, " REQUEST\n") && strstr(all, " LATCH_STATUS opened (0x0001)") &&
        strstr(all, " BASE_CONNECTION detached (0x0000") && strstr(all, " CANCEL timed out while the latch was locked (0x1002)"),
        "GP-23 monitor output:\n%s", all);
  CHECK(!strcmp(ec.log, "EV "), "GP-23 no write ioctls: '%s'", ec.log);
  dtx_ioctl = sys_ioctl;
}

static void test_profile(void) {
  set_root("tree_prof");
  CHECK(profile_apply("balanced") == -1, "no interface");
  put("/sys/firmware/acpi/platform_profile_choices", "low-power balanced balanced-performance performance\n");
  put("/sys/firmware/acpi/platform_profile", "performance\n");
  CHECK(profile_apply("") == 0 && !strcmp(get("/sys/firmware/acpi/platform_profile"), "balanced"), "default balanced");
  CHECK(profile_apply("performance") == 0 && !strcmp(get("/sys/firmware/acpi/platform_profile"), "performance"), "performance");
  CHECK(profile_apply("turbo") == 0 && !strcmp(get("/sys/firmware/acpi/platform_profile"), "balanced"), "invalid -> balanced");
  CHECK(profile_apply("low-power") == 0 && !strcmp(get("/sys/firmware/acpi/platform_profile"), "low-power"), "low-power");
  put("/sys/firmware/acpi/platform_profile_choices", "quiet performance\n");
  CHECK(profile_apply("cool") == -2, "no balanced available");
}

static void test_main_once(void) {
  char b[2048];
  set_root("tree_once");
  fake_tp(1);
  fake_psy("BAT1", bat(b, "BAT1", "Discharging", 8750000, 17500000, 6000000, 7700000, 7600000, NULL), "Battery\n", "surface_battery");
  fake_psy("ADP1", ADP_OFF, "Mains\n", "surface_charger");
  char root[PATH_MAX];
  snprintf(root, sizeof root, "%s", g_root);
  char *argv[] = {"gb_power", "-o", "-r", root, NULL};
  optind = 1;
  int r = gb_power_main(4, argv);
  CHECK(r == 0 && !strcmp(get(TP "battery_capacity"), "50") && !strcmp(get(TP "battery_status"), "discharging"), "main -o");
  put("/sys/firmware/acpi/platform_profile_choices", "low-power balanced balanced-performance performance\n");
  put("/sys/firmware/acpi/platform_profile", "balanced\n");
  char *argv2[] = {"gb_power", "-r", root, "-p", "balanced-performance", NULL};
  optind = 1;
  r = gb_power_main(5, argv2);
  CHECK(r == 0 && !strcmp(get("/sys/firmware/acpi/platform_profile"), "balanced-performance"), "main -p");
}

/* v1.2 graphics switching tests (TASKS_1B 2.2, GP-1B-01 .. GP-1B-23) */
#include "test_gfx.c"

int main(int argc, char **argv) {
  if (argc < 2) { fprintf(stderr, "usage: %s <work dir>\n", argv[0]); return 2; }
  snprintf(W, sizeof W, "%s", argv[1]);
  mkdirs(W);
  g_clock = fake_now;
  setvbuf(stdout, NULL, _IOLBF, 0);
  signal(SIGPIPE, SIG_IGN);
  test_parse();
  test_combine();
  test_publish();
  test_uevent();
  test_dgpu_passes();
  test_dtx();
  test_latch();
  test_used_detach_and_rescan();
  test_busy_refuse();
  test_double_press();
  test_hung_unbind();
  test_bootdisk();
  test_never_unbind_nouveau();
  test_watchdog();
  test_state();
  test_misc();
  test_load_window();
  test_gate_split();
  test_release_window();
  test_off_rebind();
  test_bootdisk_latch();
  test_socket_roles();
  test_socket_golden();
  test_socket_cmds();
  test_socket_limits();
  test_socket_watch();
  test_socket_ui_role();
  test_socket_compute();
  test_notify();
  test_monitor();
  test_profile();
  test_main_once();
  int wave1 = checks, wave1_fails = fails;
  printf("wave-1 groups: %d checks, %d failures\n", wave1, wave1_fails);
  test_gfx_all();
  int v12 = checks, v12_fails = fails;
  printf("v1.2 groups: %d checks, %d failures\n", checks - wave1, fails - wave1_fails);
  test_fix_all();
  printf("review-fix groups: %d checks, %d failures\n", checks - v12, fails - v12_fails);
  int rf = checks, rf_fails = fails;
  test_w2b_all();
  printf("wave-2b groups: %d checks, %d failures\n", checks - rf, fails - rf_fails);
  int w6 = checks, w6_fails = fails;
  test_w6_all();
  printf("wave-6 groups: %d checks, %d failures\n", checks - w6, fails - w6_fails);
  set_root("tree_end");                           /* reaps every worker */
  printf("\n%d checks, %d failures\n", checks, fails);
  return fails ? 1 : 0;
}
