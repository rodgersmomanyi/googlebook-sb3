/* Unit tests for gb_power.c on a Linux host: battery math, uevent/DTX parsing, the DTX state machine (mocked
 * ioctls), dGPU runtime-PM handling, platform profile and studio-mode inhibit, all against a fake sysfs tree.
 * Build + run: cc -O2 -Wall -Wextra -o test_gb_power test_gb_power.c && ./test_gb_power <empty work dir> */
#define main gb_power_main
#include "../gb_power.c"
#undef main

#include <math.h>

static int fails, checks;
#define CHECK(c, ...) do { checks++; if (!(c)) { fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static char W[PATH_MAX];   /* work dir */

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
static void set_root(const char *name) {
  snprintf(g_root, sizeof g_root, "%s/%s", W, name);
  char cmd[PATH_MAX + 16];
  snprintf(cmd, sizeof cmd, "rm -rf '%s'", g_root);
  if (system(cmd)) {}
  mkdirs(g_root);
  memset(&g_tp, 0, sizeof g_tp);
  memset(g_psy_cache, 0, sizeof g_psy_cache);
  g_hidden = 0;
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

/* ---- DTX with a mock EC ---- */
static struct { int base, latch, mode; char log[1024]; } ec;
static int mock_ioctl(int fd, unsigned long req, void *arg) {
  (void)fd;
  const char *n = req == SDTX_IOCTL_EVENTS_ENABLE ? "EV" : req == SDTX_IOCTL_LATCH_CONFIRM ? "CONFIRM" :
                  req == SDTX_IOCTL_LATCH_HEARTBEAT ? "HB" : req == SDTX_IOCTL_LATCH_CANCEL ? "CANCEL" :
                  req == SDTX_IOCTL_LATCH_REQUEST ? "REQ" : NULL;
  if (req == SDTX_IOCTL_GET_BASE_INFO) { struct sdtx_base_info *b = arg; b->state = ec.base; b->base_id = ec.base == 1 ? 0x205 : 0; return 0; }
  if (req == SDTX_IOCTL_GET_LATCH_STATUS) { *(uint16_t *)arg = ec.latch; return 0; }
  if (req == SDTX_IOCTL_GET_DEVICE_MODE) { *(uint16_t *)arg = ec.mode; return 0; }
  if (n) { strcat(ec.log, n); strcat(ec.log, " "); return 0; }
  return -EINVAL;
}
static void push(int code, const unsigned char *d, int len) {
  g_dtx.buf[g_dtx.blen] = len & 0xff; g_dtx.buf[g_dtx.blen + 1] = len >> 8;
  g_dtx.buf[g_dtx.blen + 2] = code & 0xff; g_dtx.buf[g_dtx.blen + 3] = code >> 8;
  if (len) memcpy(g_dtx.buf + g_dtx.blen + 4, d, len);
  g_dtx.blen += 4 + len;
}
static void ev(int code, int v) { unsigned char d[2] = {v & 0xff, v >> 8}; push(code, d, code == SDTX_EVENT_REQUEST ? 0 : 2); dtx_parse(); }
static const char *prop(const char *k) { static char v[92]; prop_get(k, v); return v; }

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

static void fake_pci(void) {
  put("/sys/devices/pci0000:00/0000:00:1d.4/vendor", "0x8086\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/class", "0x060400\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/power/control", "on\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/power/runtime_status", "active\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/power_state", "D0\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.0/vendor", "0x10de\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.0/device", "0x1f91\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.0/class", "0x030200\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.0/power/control", "on\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.0/power/runtime_status", "active\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.0/power_state", "D0\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.0/d3cold_allowed", "1\n");
  put("/sys/bus/pci/drivers/nouveau/unbind", "");
  lnk("../../../../bus/pci/drivers/nouveau", "/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.0/driver");
  put("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.1/vendor", "0x10de\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.1/device", "0x10fa\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.1/class", "0x040300\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.1/power/control", "on\n");
  put("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.1/d3cold_allowed", "0\n");
  put("/sys/devices/pci0000:00/0000:00:02.0/vendor", "0x8086\n");
  put("/sys/devices/pci0000:00/0000:00:02.0/class", "0x030000\n");
  put("/sys/devices/pci0000:00/0000:00:02.0/power/control", "on\n");
  lnk("../../../devices/pci0000:00/0000:00:1d.4", "/sys/bus/pci/devices/0000:00:1d.4");
  lnk("../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.0", "/sys/bus/pci/devices/0000:02:00.0");
  lnk("../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.1", "/sys/bus/pci/devices/0000:02:00.1");
  lnk("../../../devices/pci0000:00/0000:00:02.0", "/sys/bus/pci/devices/0000:00:02.0");
}

static void test_dgpu(void) {
  set_root("tree_pci");
  fake_pci();
  int bound = dgpu_scan("test", 1, 1);
  CHECK(bound == 0, "unbind ok, bound %d", bound);
  CHECK(!strcmp(get("/sys/bus/pci/drivers/nouveau/unbind"), "0000:02:00.0"), "unbind written: %s", get("/sys/bus/pci/drivers/nouveau/unbind"));
  CHECK(!strcmp(get("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.0/power/control"), "auto"), "dGPU rpm auto");
  CHECK(!strcmp(get("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.1/power/control"), "auto"), "dGPU audio rpm auto");
  CHECK(!strcmp(get("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.1/d3cold_allowed"), "1"), "d3cold allowed");
  CHECK(!strcmp(get("/sys/devices/pci0000:00/0000:00:1d.4/power/control"), "auto"), "root port rpm auto");
  CHECK(!strcmp(get("/sys/devices/pci0000:00/0000:00:02.0/power/control"), "on"), "iGPU untouched");
  CHECK(g_dgpu_seen == 2, "seen %d", g_dgpu_seen);
  /* unbind refused (driver busy) -> reported as still bound */
  char p[PATH_MAX];
  chmod(rpath(p, sizeof p, "/sys/bus/pci/drivers/nouveau/unbind"), 0444);
  if (access(p, W_OK)) {
    CHECK(dgpu_scan("test", 0, 1) == 1, "busy driver counted");
  }
  /* no dGPU (base detached) */
  set_root("tree_nopci");
  put("/sys/bus/pci/devices/.keep", "");
  CHECK(dgpu_scan("test", 1, 1) == 0 && g_dgpu_seen == 0, "no dGPU");
}

static void test_dtx(void) {
  set_root("tree_dtx");
  fake_input();
  fake_pci();
  put("/dev/surface/dtx", "");
  dtx_ioctl = mock_ioctl;
  g_race_wait_ms = 20;
  memset(&ec, 0, sizeof ec);
  ec.base = SDTX_BASE_ATTACHED; ec.latch = SDTX_LATCH_CLOSED; ec.mode = MODE_LAPTOP;
  g_dtx.fd = -1;
  CHECK(dtx_open() == 0 && g_dtx.fd >= 0, "dtx open");
  CHECK(strstr(ec.log, "EV") != NULL, "events enabled");
  CHECK(!strcmp(prop("vendor.gb.dtx.mode"), "laptop") && !strcmp(prop("vendor.gb.dtx.base"), "attached"), "initial props");
  CHECK(g_dtx.ec == EC_READY, "ready");

  /* A. normal detach */
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB CONFIRM "), "detach confirmed promptly: '%s'", ec.log);
  CHECK(g_dtx.ec == EC_CONFIRMED, "confirmed state");
  CHECK(!strcmp(get("/sys/bus/pci/drivers/nouveau/unbind"), "0000:02:00.0"), "dGPU unbound before confirm");
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
  /* re-attach: dGPU backstop scan scheduled */
  g_dgpu_due = -1;
  ec.base = SDTX_BASE_ATTACHED; ec.mode = MODE_LAPTOP;
  ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_ATTACHED);
  CHECK(g_dtx.base == SDTX_BASE_ATTACHED && g_dgpu_due > 0, "attach schedules dGPU scan");

  /* B. user aborts (presses the button again before the latch opened; the latch stays closed) */
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB CONFIRM ") && g_dtx.ec == EC_READY, "abort: '%s' ec %d", ec.log, g_dtx.ec);

  /* B2. confirm/abort race where the latch did open: stays confirmed (no second HB/CONFIRM on the next press) until
   * the latch closes */
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  ec.latch = SDTX_LATCH_OPENED;                   /* EC executed the confirm before the abort */
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(g_dtx.ec == EC_CONFIRMED && g_dtx.latch == SDTX_LATCH_OPENED && !strcmp(prop("vendor.gb.dtx.latch"), "opened"),
        "race, latch open: ec %d latch %d", g_dtx.ec, g_dtx.latch);
  ev(SDTX_EVENT_REQUEST, 0);                      /* press while open: EC closes the latch, we send nothing */
  CHECK(!strcmp(ec.log, "HB CONFIRM ") && g_dtx.ec == EC_CONFIRMED, "press while open: '%s'", ec.log);
  ec.latch = SDTX_LATCH_CLOSED;
  ev(SDTX_EVENT_LATCH_STATUS, SDTX_LATCH_CLOSED);
  CHECK(g_dtx.ec == EC_READY && g_dtx.latch == SDTX_LATCH_CLOSED, "race resolved by latch close");

  /* B3. unknown EC values are ignored (base state, latch status, mode): a detach still gets confirmed */
  ec.log[0] = 0;
  ec.base = 0xf005;                               /* SDTX_UNKNOWN(5) */
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
  ec.latch = SDTX_ERR_FAILED_TO_CLOSE;            /* hw error: re-read says still failed -> inferred open */
  ev(SDTX_EVENT_LATCH_STATUS, SDTX_ERR_FAILED_TO_CLOSE);
  CHECK(g_dtx.latch == SDTX_LATCH_OPENED, "failed-to-close -> open");
  ec.latch = SDTX_LATCH_CLOSED;
  ev(SDTX_EVENT_LATCH_STATUS, SDTX_ERR_FAILED_TO_REMAIN_OPEN);   /* re-read wins: closed */
  CHECK(g_dtx.latch == SDTX_LATCH_CLOSED && g_dtx.ec == EC_READY, "failed-to-remain-open -> re-read closed");

  /* B4. dGPU handling off (gb.dgpu=keep / -G): a detach still unbinds before confirming, runtime PM untouched */
  g_dgpu_on = 0;
  put("/sys/bus/pci/drivers/nouveau/unbind", "");
  put("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.0/power/control", "on\n");
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB CONFIRM ") && !strcmp(get("/sys/bus/pci/drivers/nouveau/unbind"), "0000:02:00.0") &&
        !strcmp(get("/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.0/power/control"), "on"), "keep mode detach: '%s'", ec.log);
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
  push(SDTX_EVENT_BASE_CONNECTION, (const unsigned char *)"\x01\x00\x7f\x7f", 4);   /* "latch opened" + garbage */
  size_t full = g_dtx.blen;
  push(SDTX_EVENT_REQUEST, NULL, 0);
  unsigned char hdr[4];
  memcpy(hdr, g_dtx.buf + full, 4);
  g_dtx.blen = full + 2;                          /* only half of the request header has arrived */
  int k = dtx_parse();
  CHECK(k == 2 && g_dtx.blen == 2 && !memcmp(g_dtx.buf, hdr, 2) && g_dtx.base == SDTX_BASE_ATTACHED,
        "partial: %d events, %zu left", k, g_dtx.blen);
  memcpy(g_dtx.buf + g_dtx.blen, hdr + 2, 2);     /* the rest arrives */
  g_dtx.blen += 2;
  k = dtx_parse();
  CHECK(k == 1 && g_dtx.blen == 0 && strstr(ec.log, "CONFIRM"), "rest parsed: %d '%s'", k, ec.log);
  ev(SDTX_EVENT_REQUEST, 0);                      /* abort again */

  /* F. studio mode inhibits base keyboard + touchpad only */
  ec.mode = MODE_STUDIO;
  ev(SDTX_EVENT_DEVICE_MODE, MODE_STUDIO);
  CHECK(!strcmp(prop("vendor.gb.dtx.mode"), "studio"), "studio prop");
  CHECK(!strcmp(get("/sys/class/input/input7/inhibited"), "1") && !strcmp(get("/sys/class/input/input8/inhibited"), "1"), "base input inhibited");
  CHECK(!strcmp(get("/sys/class/input/input3/inhibited"), "0") && !strcmp(get("/sys/class/input/input9/inhibited"), "0"), "other input untouched");
  ec.mode = MODE_LAPTOP;
  ev(SDTX_EVENT_DEVICE_MODE, MODE_LAPTOP);
  CHECK(!strcmp(get("/sys/class/input/input7/inhibited"), "0") && !strcmp(get("/sys/class/input/input8/inhibited"), "0"), "re-enabled");

  /* G. a dGPU driver that refuses to unbind aborts the detach */
  char p[PATH_MAX];
  chmod(rpath(p, sizeof p, "/sys/bus/pci/drivers/nouveau/unbind"), 0444);
  if (access(p, W_OK)) {
    ec.log[0] = 0;
    ev(SDTX_EVENT_REQUEST, 0);
    CHECK(!strcmp(ec.log, "HB CANCEL "), "busy dGPU -> cancel: '%s'", ec.log);
    ev(SDTX_EVENT_REQUEST, 0);
    CHECK(g_dtx.ec == EC_READY, "G reset");
  }
  dtx_close();
  dtx_ioctl = sys_ioctl;
}

/* ---- nouveau opt-in ---- */
/* Kernel emulation of <dev>/driver/unbind (the fake tree's attribute is a plain file): logs the write into ec.log, so
 * its order against the DTX ioctls is checked, and drops the driver link like device_driver_detach().
 * unbind_mode 0: ok; 1: refused (-EBUSY, link stays); 2: write "succeeds" but the function stays bound. */
static int unbind_mode;
static int mock_unbind(const char *path, const char *bdf) {
  static const char *const tag[] = {"UNBIND", "UNBIND-EBUSY", "UNBIND-STUCK"};
  strcat(ec.log, tag[unbind_mode]); strcat(ec.log, ":"); strcat(ec.log, bdf); strcat(ec.log, " ");
  if (unbind_mode == 1) return -EBUSY;
  int r = write_text(path, bdf);
  if (r || unbind_mode == 2) return r;
  char l[PATH_MAX];
  snprintf(l, sizeof l, "%s", path);
  *strrchr(l, '/') = 0;                           /* <dev>/driver */
  return unlink(l) ? -errno : 0;
}
static int dgpu_linked(const char *bdf) {         /* driver link present? */
  char p[PATH_MAX], t[64];
  rpath(p, sizeof p, "/sys/bus/pci/devices/%s/driver", bdf);
  return readlink(p, t, sizeof t) > 0;
}
#define FN0 "/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.0"
#define FN1 "/sys/devices/pci0000:00/0000:00:1d.4/0000:02:00.1"
static void bind_nouveau(void) { lnk("../../../../bus/pci/drivers/nouveau", FN0 "/driver"); put("/sys/bus/pci/drivers/nouveau/unbind", ""); }
static void bind_hda(void) {                      /* another driver on the HDMI audio function */
  put("/sys/bus/pci/drivers/snd_hda_intel/unbind", "");
  lnk("../../../../bus/pci/drivers/snd_hda_intel", FN1 "/driver");
}
/* LATCH_CONFIRM while any dGPU function still has a driver -> "BOUND!" in the log (the unsafe case).
 * rebind_on_hb: on the heartbeat that follows an unbind (i.e. after the dGPU pass, before the confirm), a driver binds
 * 02:00.1 (a probe that raced the slow nouveau teardown). */
static int rebind_on_hb;
static int mock_ioctl_chk(int fd, unsigned long req, void *arg) {
  if (req == SDTX_IOCTL_LATCH_HEARTBEAT && rebind_on_hb && strstr(ec.log, "UNBIND")) { rebind_on_hb = 0; bind_hda(); }
  if (req == SDTX_IOCTL_LATCH_CONFIRM && (dgpu_linked("0000:02:00.0") || dgpu_linked("0000:02:00.1"))) strcat(ec.log, "BOUND! ");
  return mock_ioctl(fd, req, arg);
}
static void optin_tree(const char *name) {
  set_root(name);
  fake_pci();                                     /* 02:00.0 bound to nouveau, 02:00.1 driverless */
  bind_hda();
  ec.log[0] = 0;
}

static void test_dgpu_optin(void) {
  dgpu_unbind_write = mock_unbind;
  unbind_mode = 0;
  g_keep_cli = 0; g_keep_drv[0] = 0;

  /* 1. opt-in off (property unset): any bound driver is unbound, runtime PM auto, as before */
  optin_tree("tree_optin_off");
  CHECK(dgpu_scan("hot-plug", 1, 1) == 0 && g_keep_drv[0] == 0 && g_dgpu_kept == 0, "off: scan");
  CHECK(strstr(ec.log, "UNBIND:0000:02:00.0 ") && strstr(ec.log, "UNBIND:0000:02:00.1 "), "off: both unbound: '%s'", ec.log);
  CHECK(!dgpu_linked("0000:02:00.0") && !dgpu_linked("0000:02:00.1"), "off: driver links gone");
  CHECK(!strcmp(get("/sys/bus/pci/drivers/nouveau/unbind"), "0000:02:00.0") &&
        !strcmp(get("/sys/bus/pci/drivers/snd_hda_intel/unbind"), "0000:02:00.1"), "off: unbind attributes written");
  CHECK(!strcmp(get(FN0 "/power/control"), "auto") && !strcmp(get(FN1 "/power/control"), "auto") &&
        !strcmp(get("/sys/devices/pci0000:00/0000:00:1d.4/power/control"), "auto"), "off: rpm auto");
  /* property 0 is the same as unset */
  prop_set("persist.vendor.gb.nouveau", "0");
  optin_tree("tree_optin_zero");
  CHECK(dgpu_scan("hot-plug", 0, 1) == 0 && g_keep_drv[0] == 0 && !dgpu_linked("0000:02:00.0") &&
        !dgpu_linked("0000:02:00.1") && !strcmp(get(FN0 "/power/control"), "auto"), "property 0: unbound: '%s'", ec.log);

  /* 2. opt-in on: the nouveau function stays bound with power/control untouched; other drivers are still unbound */
  prop_set("persist.vendor.gb.nouveau", "1");
  optin_tree("tree_optin_on");
  put(FN0 "/d3cold_allowed", "0\n");
  CHECK(dgpu_scan("hot-plug", 0, 1) == 0, "on: kept driver not counted as bound");
  CHECK(!strcmp(g_keep_drv, "nouveau") && g_dgpu_kept == 1, "on: keep '%s' kept %d", g_keep_drv, g_dgpu_kept);
  CHECK(!strstr(ec.log, "0000:02:00.0") && dgpu_linked("0000:02:00.0") && !strcmp(get("/sys/bus/pci/drivers/nouveau/unbind"), ""),
        "on: nouveau left bound: '%s'", ec.log);
  CHECK(!strcmp(get(FN0 "/power/control"), "on"), "on: kept function's power/control untouched");
  CHECK(!strcmp(get(FN0 "/d3cold_allowed"), "1"), "on: d3cold still allowed on the kept function");
  CHECK(strstr(ec.log, "UNBIND:0000:02:00.1 ") && !dgpu_linked("0000:02:00.1") && !strcmp(get(FN1 "/power/control"), "auto"),
        "on: other driver (snd_hda_intel) unbound: '%s'", ec.log);
  CHECK(!strcmp(get("/sys/devices/pci0000:00/0000:00:1d.4/power/control"), "auto"), "on: root port rpm auto");
  ec.log[0] = 0;
  CHECK(dgpu_scan("report", 1, 1) == 0 && dgpu_linked("0000:02:00.0") && ec.log[0] == 0, "on: report pass keeps it too");
  /* a bind uevent (nouveau loaded at runtime) schedules the pass that leaves it bound */
  struct uev u = {.action = "bind", .subsystem = "pci", .devpath = "/devices/pci0000:00/0000:00:1d.4/0000:02:00.0",
                  .pci_id = "10DE:1F91"};
  g_dgpu_due = -1;
  uev_handle(&u);
  CHECK(g_dgpu_due > 0, "on: bind uevent schedules a pass");

  /* 3. opt-in on, detach request with nouveau bound: unbound and verified gone BEFORE LATCH_CONFIRM, extra heartbeat */
  put("/dev/surface/dtx", "");
  dtx_ioctl = mock_ioctl_chk;
  g_race_wait_ms = 20;
  memset(&ec, 0, sizeof ec);
  ec.base = SDTX_BASE_ATTACHED; ec.latch = SDTX_LATCH_CLOSED; ec.mode = MODE_LAPTOP;
  g_dtx.fd = -1;
  CHECK(dtx_open() == 0 && g_dtx.fd >= 0, "on: dtx open");
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB UNBIND:0000:02:00.0 HB CONFIRM "), "on: detach unbinds nouveau, then confirms: '%s'", ec.log);
  CHECK(g_dtx.ec == EC_CONFIRMED && !dgpu_linked("0000:02:00.0") &&
        !strcmp(get("/sys/bus/pci/drivers/nouveau/unbind"), "0000:02:00.0"), "on: confirmed with the driver gone");
  CHECK(!strcmp(get(FN0 "/power/control"), "auto"), "on: driverless after the detach pass -> rpm auto");
  ev(SDTX_EVENT_REQUEST, 0);                      /* user aborts; the latch never opened */
  CHECK(g_dtx.ec == EC_READY, "on: abort -> ready");

  /* 3b. opt-in on, nouveau not bound (not loaded yet): plain detach, no extra heartbeat */
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB CONFIRM "), "on, nothing bound: '%s'", ec.log);
  ev(SDTX_EVENT_REQUEST, 0);

  /* 3c. nouveau binds while a detach is under way (gb_nouveau.sh's insmod or a re-probe racing the button): the
   * bind-triggered pass (and any other pass) unbinds it again instead of leaving it bound while the base can be pulled;
   * once the latch is closed again (detach over) it is kept as usual */
  bind_nouveau();
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB UNBIND:0000:02:00.0 HB CONFIRM ") && g_dtx.ec == EC_CONFIRMED, "3c confirmed: '%s'", ec.log);
  bind_nouveau();                                 /* probe done after the confirm, latch still opening */
  put(FN0 "/power/control", "on\n");              /* as nouveau's teardown leaves it (pm_runtime_forbid) */
  ec.log[0] = 0;
  CHECK(dgpu_scan("hot-plug", 0, 1) == 0 && !dgpu_linked("0000:02:00.0") && !strcmp(ec.log, "UNBIND:0000:02:00.0 "),
        "on, confirmed: late bind unbound: '%s'", ec.log);
  CHECK(!strcmp(get(FN0 "/power/control"), "auto"), "on, confirmed: late-bound function back to rpm auto");
  ec.latch = SDTX_LATCH_OPENED;
  ev(SDTX_EVENT_LATCH_STATUS, SDTX_LATCH_OPENED);
  bind_nouveau();
  ec.log[0] = 0;
  CHECK(dgpu_scan("report", 1, 1) == 0 && !dgpu_linked("0000:02:00.0") && !strcmp(ec.log, "UNBIND:0000:02:00.0 "),
        "on, latch open: late bind unbound: '%s'", ec.log);
  ec.latch = SDTX_LATCH_CLOSED;                   /* clipboard not pulled: the EC closes the latch again */
  ev(SDTX_EVENT_LATCH_STATUS, SDTX_LATCH_CLOSED);
  bind_nouveau();
  ec.log[0] = 0;
  CHECK(g_dtx.ec == EC_READY && dgpu_scan("hot-plug", 0, 1) == 0 && dgpu_linked("0000:02:00.0") && ec.log[0] == 0,
        "on, detach over: nouveau kept again: '%s'", ec.log);

  /* 3d. a driver binds between the (slow) unbind pass and the confirm: the last look before LATCH_CONFIRM cancels */
  rebind_on_hb = 1;
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB UNBIND:0000:02:00.0 HB CANCEL ") && g_dtx.ec == EC_IN_PROGRESS && dgpu_linked("0000:02:00.1"),
        "on, rebound before the confirm -> cancel: '%s'", ec.log);
  ev(SDTX_EVENT_REQUEST, 0);                      /* EC echoes the cancel */
  CHECK(g_dtx.ec == EC_READY && !strstr(ec.log, "CONFIRM"), "3d: ready, never confirmed");
  ec.log[0] = 0;
  CHECK(dgpu_scan("hot-plug", 0, 1) == 0 && !dgpu_linked("0000:02:00.1") && !strcmp(ec.log, "UNBIND:0000:02:00.1 "),
        "3d: snd_hda_intel unbound by the next pass: '%s'", ec.log);

  /* 4. opt-in on, nouveau refuses the unbind: detach canceled, no confirm */
  bind_nouveau();
  unbind_mode = 1;
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB UNBIND-EBUSY:0000:02:00.0 CANCEL ") && g_dtx.ec == EC_IN_PROGRESS && dgpu_linked("0000:02:00.0"),
        "on: unbind refused -> cancel: '%s'", ec.log);
  ev(SDTX_EVENT_REQUEST, 0);                      /* EC echoes the cancel as a request event */
  CHECK(g_dtx.ec == EC_READY && !strstr(ec.log, "CONFIRM"), "on: refused -> ready, never confirmed");

  /* 5. opt-in on, the unbind write returns success but the driver link stays: the check catches it -> cancel */
  unbind_mode = 2;
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB UNBIND-STUCK:0000:02:00.0 CANCEL ") && dgpu_linked("0000:02:00.0"),
        "on: driver still bound after unbind -> cancel: '%s'", ec.log);
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(g_dtx.ec == EC_READY, "on: stuck -> ready");
  unbind_mode = 0;

  /* 6. opt-in on with -G / gb.dgpu=keep: the detach still unbinds (and verifies), runtime PM left alone */
  g_dgpu_on = 0;
  put(FN0 "/power/control", "on\n");
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB UNBIND:0000:02:00.0 HB CONFIRM ") && !dgpu_linked("0000:02:00.0") &&
        !strcmp(get(FN0 "/power/control"), "on"), "on + -G: '%s'", ec.log);
  ev(SDTX_EVENT_REQUEST, 0);
  g_dgpu_on = 1;

  /* 7. runtime opt-out (setprop persist.vendor.gb.nouveau 0): the next pass unbinds nouveau again; a detach is the
   * plain reviewed sequence (one heartbeat, no extra one) */
  bind_nouveau();
  bind_hda();
  prop_set("persist.vendor.gb.nouveau", "0");
  ec.log[0] = 0;
  CHECK(dgpu_scan("hot-plug", 0, 1) == 0 && g_keep_drv[0] == 0 && !dgpu_linked("0000:02:00.0") && !dgpu_linked("0000:02:00.1"),
        "opt-out: unbound again: '%s'", ec.log);
  bind_nouveau();
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB UNBIND:0000:02:00.0 CONFIRM ") && g_dtx.ec == EC_CONFIRMED, "off: detach as before: '%s'", ec.log);
  ev(SDTX_EVENT_REQUEST, 0);
  bind_nouveau();
  unbind_mode = 1;
  ec.log[0] = 0;
  ev(SDTX_EVENT_REQUEST, 0);
  CHECK(!strcmp(ec.log, "HB UNBIND-EBUSY:0000:02:00.0 CANCEL "), "off: refused unbind still cancels: '%s'", ec.log);
  ev(SDTX_EVENT_REQUEST, 0);
  unbind_mode = 0;
  dtx_close();
  dtx_ioctl = sys_ioctl;

  /* 8. -K <driver> on the command line wins over the property; only that driver is kept */
  optin_tree("tree_optin_cli");
  char root[PATH_MAX];
  snprintf(root, sizeof root, "%s", g_root);
  char *argv[] = {"gb_power", "-o", "-r", root, "-K", "nouveau", NULL};
  optind = 1;
  CHECK(gb_power_main(6, argv) == 0 && g_keep_cli && !strcmp(g_keep_drv, "nouveau"), "-K parsed");
  prop_set("persist.vendor.gb.nouveau", "0");
  CHECK(dgpu_scan("hot-plug", 0, 1) == 0 && dgpu_linked("0000:02:00.0") && !dgpu_linked("0000:02:00.1"), "-K nouveau beats property 0");
  snprintf(g_keep_drv, sizeof g_keep_drv, "snd_hda_intel");
  bind_hda();
  prop_set("persist.vendor.gb.nouveau", "1");
  CHECK(dgpu_scan("hot-plug", 0, 1) == 0 && !dgpu_linked("0000:02:00.0") && dgpu_linked("0000:02:00.1"), "-K other driver: nouveau unbound");
  g_keep_cli = 0; g_keep_drv[0] = 0;
  prop_set("persist.vendor.gb.nouveau", "");
  dgpu_unbind_write = write_text;
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

int main(int argc, char **argv) {
  if (argc < 2) { fprintf(stderr, "usage: %s <work dir>\n", argv[0]); return 2; }
  snprintf(W, sizeof W, "%s", argv[1]);
  mkdirs(W);
  test_parse();
  test_combine();
  test_publish();
  test_uevent();
  test_dgpu();
  test_dtx();
  test_dgpu_optin();
  test_profile();
  test_main_once();
  printf("\n%d checks, %d failures\n", checks, fails);
  return fails ? 1 : 0;
}
