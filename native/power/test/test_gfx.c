/* v1.2 graphics switching tests (INTERFACES 22-26, 1b\TASKS_1B.md 2.2: GP-1B-01 .. GP-1B-23) against the fake tree,
 * the mock EC (ec.model) and the real forked worker. Included by test_gb_power.c, whose helpers it uses (put/get, the
 * fake PCI tree, pump/settle, cl_pair/req, notified). g_trace puts every setprop, node chmod and marker result into
 * ec.log next to the EC ioctls and worker op results, so one ordered log checks the 23.3 orderings. */

#define T_BUILD_ID "CL3B.260622.276.R4"
#define T_INCR "16658739"
#define T_BUILD T_BUILD_ID "/" T_INCR
#define T_KREL "6.18.21-android17-5-g3938c8d97142-dirty-ab16302624"
#define T_GFX "apk=1111 icd=2222 nvk=3333 probe=4444 pay=legacy"
#define T_BOOT "0b5e4c1a-1111-4222-8333-944455556666"
#define T_BOOT2 "0b5e4c1a-2222-4222-8333-944455556666"
#define T_BOOT_OLD "0b5e4c1a-0000-4000-8000-900000000000"
#define RENDER "/dev/dri/renderD129"
#define PREFS "/data/vendor/gb/gfx_app_prefs"
#define OPTIN "/data/vendor/gb/gfx_optin"
#define OPTOUT "/data/vendor/gb/gfx_optout"

static void tr(const char *s) { ec_log(s); }
static const char *raw(const char *rel) {         /* exact bytes (get() strips trailing whitespace) */
  static char b[200000];
  if (file_load(rel, b, sizeof b) < 0) snprintf(b, sizeof b, "<missing>");
  return b;
}
static int count_files_with(const char *s) {
  char dir[PATH_MAX], p[PATH_MAX + 300], b[1024];
  DIR *d = opendir(rpath(dir, sizeof dir, "/data/vendor/gb/notify"));
  int k = 0;
  if (!d) return 0;
  struct dirent *e;
  while ((e = readdir(d))) {
    if (e->d_name[0] == '.') continue;
    snprintf(p, sizeof p, "%s/%s", dir, e->d_name);
    int fd = open(p, O_RDONLY | O_CLOEXEC);
    if (fd < 0) continue;
    ssize_t n = read(fd, b, sizeof b - 1);
    close(fd);
    if (n > 0) { b[n] = 0; if (strstr(b, s)) k++; }
  }
  closedir(d);
  return k;
}
static void clear_notify(void) {
  char dir[PATH_MAX], p[PATH_MAX + 300];
  DIR *d = opendir(rpath(dir, sizeof dir, "/data/vendor/gb/notify"));
  struct dirent *e;
  while (d && (e = readdir(d))) {
    if (e->d_name[0] == '.') continue;
    snprintf(p, sizeof p, "%s/%s", dir, e->d_name);
    unlink(p);
  }
  if (d) closedir(d);
}
static void write_rec(const char *verdict, const char *reason, const char *build, const char *krel, const char *gfx,
                      long long t, int att, int cr) {
  char b[2048];
  snprintf(b, sizeof b, "verdict=%s\nreason=%s\nbuild=%s\nkrel=%s\ngfx=%s\nt=%lld\nattempts=%d\ncrashes=%d\nreport=selftest-3.txt\n",
           verdict, reason, build, krel, gfx, t, att, cr);
  put(REC_PATH, b);
}
static void rec_ok(const char *verdict, const char *reason, int att, int cr) {
  write_rec(verdict, reason, T_BUILD, T_KREL, T_GFX, 1761390000, att, cr);
}
static const char *rec_field(const char *k) {    /* a field of the record file as written */
  static char v[600];
  const char *b = raw(REC_PATH), *s;
  char key[32];
  snprintf(key, sizeof key, "%s=", k);
  v[0] = 0;
  for (s = b; s && *s; s = strchr(s, '\n') ? strchr(s, '\n') + 1 : NULL)
    if (!strncmp(s, key, strlen(key))) {
      const char *e = strchr(s, '\n');
      size_t l = e ? (size_t)(e - s - strlen(key)) : strlen(s + strlen(key));
      if (l >= sizeof v) l = sizeof v - 1;
      memcpy(v, s + strlen(key), l);
      v[l] = 0;
      break;
    }
  return v;
}
static void stamp_catalog(const char *body) {     /* gfx_catalog committed against the current packages.list */
  static char b[200000];
  char p[PATH_MAX];
  struct stat st;
  if (stat(rpath(p, sizeof p, "/data/system/packages.list"), &st)) { perror("stat packages.list"); exit(2); }
  snprintf(b, sizeof b, "# pkgs %lld %lld\n%s", (long long)st.st_mtim.tv_sec * 1000000000LL + st.st_mtim.tv_nsec,
           (long long)st.st_size, body);
  put("/data/vendor/gb/gfx_catalog", b);
}
static void gfx_props(void) {                     /* the properties of a native image with the driver package */
  prop_set("ro.gfx.driver.0", "gb.sb3.gfxdriver");
  prop_set("ro.build.id", T_BUILD_ID);
  prop_set("ro.build.version.incremental", T_INCR);
  prop_set("vendor.gb.nouveau.mode", "auto");
  prop_set("vendor.gb.nouveau.result", "clean");
  prop_set("vendor.gb.nouveau.booted", "1");
  prop_set("sys.boot_completed", "1");
  prop_set("ro.persistent_properties.ready", "true");   /* (R-15) */
}
/* INTERFACES 26.3's tree: native, stack, package, gfx_build_id, DTX attached (EC model), nouveau bound, D3cold, refcnt
 * 0, result=clean, a valid pass record (t=1761390000), gfx_mode/gfx_apps unset, empty catalog stamped with the current
 * packages.list, no choices */
static void gfx_tree(const char *name) {
  char p[PATH_MAX];
  set_root(name);
  fake_pci(1, NULL);
  put("/odm/etc/gb_hw", "native\n");
  put("/odm/lib/modules/nouveau.ko", "ko");
  put("/odm/app/GbGpu/GbGpu.apk", "apk");
  put("/odm/app/GbGfxDriver/GbGfxDriver.apk", "apk");
  put("/odm/etc/gb/gfx_build_id", T_GFX "\n");
  put("/proc/sys/kernel/osrelease", T_KREL "\n");
  put("/proc/sys/kernel/random/boot_id", T_BOOT "\n");
  put("/proc/uptime", "95.12 180.00\n");
  gfx_props();
  pkgs();
  notify_dir();
  mkdirs(rpath(p, sizeof p, "/data/vendor/gb/selftest"));
  chmod(p, 0700);
  rec_ok("pass", "", 1, 0);
  stamp_catalog("");
  dtx_up(1);
}
static void gfx_run(int ms) { status_tick(T); settle(); pump(ms, 100); }
static int is_nvidia(void) { return !strcmp(prop("vendor.gb.gfx_active"), "nvidia") && !strcmp(prop("vendor.gb.gfx_reason"), "ok") && g_gfx.active; }
/* gb_power restarted on the same tree (optionally in a new boot): memory state fresh, files kept */
static void gfx_reboot(const char *boot) {
  reset_daemon();
  gfx_props();
  if (boot) { char b[64]; snprintf(b, sizeof b, "%s\n", boot); put("/proc/sys/kernel/random/boot_id", b); }
  dtx_up(1);
}
static int owner_never(const struct stat *st) { (void)st; return 0; }   /* "not root-owned" */
static int mark_fail(int kind, const char *path, const char *val) { (void)kind; (void)path; (void)val; return -EIO; }
static int g_submits;
static int count_submit(const struct job *j) { (void)j; g_submits++; return 0; }
static void quiet(int on) {                       /* the daemon's log lines go to stdout: off for the big loops */
  static int saved = -1;
  fflush(stdout);
  if (on && saved < 0) { saved = dup(1); int n = open("/dev/null", O_WRONLY); if (n >= 0) { dup2(n, 1); close(n); } }
  else if (!on && saved >= 0) { dup2(saved, 1); close(saved); saved = -1; }
}

/* ================================================================== GP-1B-01 derivation matrix vs an oracle (23.2 as data) */
enum { MO_UNSET, MO_AUTO, MO_INTEL, MO_NVIDIA, MO_GARBAGE, MO_N };
enum { BA_ATT, BA_DET, BA_NF, BA_N };
enum { DG_ABSENT, DG_DRIVERLESS, DG_IDLE, DG_BUSY, DG_LOADING, DG_REBIND, DG_N };
enum { RE_NONE, RE_PASS, RE_FAIL, RE_ERROR, RE_SBUILD, RE_SKREL, RE_SGFX, RE_N };
enum { SP_NONE, SP_KILL, SP_TEST, SP_KEEP, SP_NOLOAD, SP_CRASHLOOP, SP_WEDGED, SP_FAULT, SP_DETACHING, SP_PENDING, SP_NODTX,
       SP_NOPKG, SP_N };
struct mxc { int mo, ba, dg, re, sp; };
enum { O_NOPKG, O_NODTX, O_KILL, O_TEST, O_RECOVERED, O_WEDGED, O_FAULT, O_MODE_INTEL, O_NOT_ATTACHED, O_DETACHING,
       O_LOADING, O_NOT_BOUND, O_NO_RECORD, O_REC_ERROR, O_REC_FAIL, O_OTHERWISE };
static const struct { int cond; const char *reason; } ORACLE[] = {   /* INTERFACES 23.2, first matching row wins */
  {O_NOPKG, "nopkg"}, {O_NODTX, "nodtx"}, {O_KILL, "kill"}, {O_TEST, "test"}, {O_RECOVERED, "crashloop"},
  {O_WEDGED, "wedged"}, {O_FAULT, "fault"}, {O_MODE_INTEL, "mode"}, {O_NOT_ATTACHED, "detached"},
  {O_DETACHING, "detaching"}, {O_LOADING, "loading"}, {O_NOT_BOUND, "nodriver"}, {O_NO_RECORD, "untested"},
  {O_REC_ERROR, "testerror"}, {O_REC_FAIL, "selftest"}, {O_OTHERWISE, "ok"}};
static int oracle_holds(int cond, const struct mxc *c) {
  switch (cond) {
    case O_NOPKG: return c->sp == SP_NOPKG;
    case O_NODTX: return c->sp == SP_NODTX;
    case O_KILL: return c->sp == SP_KILL;
    case O_TEST: return c->sp == SP_TEST;
    case O_RECOVERED: return c->sp == SP_KEEP || c->sp == SP_NOLOAD || c->sp == SP_CRASHLOOP;
    case O_WEDGED: return c->sp == SP_WEDGED;
    case O_FAULT: return c->sp == SP_FAULT;
    case O_MODE_INTEL: return c->mo == MO_INTEL;
    case O_NOT_ATTACHED: return c->ba == BA_DET;                 /* not-feasible counts as attached */
    case O_DETACHING: return c->sp == SP_DETACHING || c->sp == SP_PENDING;
    case O_LOADING: return c->dg == DG_LOADING || c->dg == DG_REBIND;
    case O_NOT_BOUND: return c->dg != DG_IDLE && c->dg != DG_BUSY;
    case O_NO_RECORD: return c->re == RE_NONE || c->re == RE_SBUILD || c->re == RE_SKREL || c->re == RE_SGFX;
    case O_REC_ERROR: return c->re == RE_ERROR;
    case O_REC_FAIL: return c->re == RE_FAIL;
    default: return 1;
  }
}
static const char *oracle(const struct mxc *c) {
  for (unsigned i = 0; i < sizeof ORACLE / sizeof ORACLE[0]; i++) if (oracle_holds(ORACLE[i].cond, c)) return ORACLE[i].reason;
  return "?";
}
static void mx_record(int re) {
  switch (re) {
    case RE_NONE: rm(REC_PATH); break;
    case RE_PASS: rec_ok("pass", "", 1, 0); break;
    case RE_FAIL: rec_ok("fail", "ahb-i915-readback", 1, 0); break;
    case RE_ERROR: rec_ok("error", "icd", 1, 0); break;
    case RE_SBUILD: write_rec("pass", "", T_BUILD_ID "/16658738", T_KREL, T_GFX, 1761390000, 1, 0); break;
    case RE_SKREL: write_rec("pass", "", T_BUILD, "6.18.21-other", T_GFX, 1761390000, 1, 0); break;
    default: write_rec("pass", "", T_BUILD, T_KREL, "apk=9999 icd=2222 nvk=3333 probe=4444 pay=legacy", 1761390000, 1, 0); break;
  }
  rec_validate("matrix");
}
static void mx_apply(const struct mxc *c, int dtx_fd) {
  static const char *const MODES[] = {"", "auto", "intel", "nvidia", "turbo"};
  prop_set("persist.vendor.gb.gfx_mode", MODES[c->mo]);
  g_dtx.fd = c->sp == SP_NODTX ? -1 : dtx_fd;
  g_dtx.base = c->ba == BA_ATT ? SDTX_BASE_ATTACHED : c->ba == BA_DET ? SDTX_BASE_DETACHED : SDTX_DETACH_NOT_FEASIBLE;
  g_dtx.ec = EC_READY;
  if (c->dg == DG_ABSENT) {
    rm("/sys/bus/pci/devices/0000:02:00.0");
    rm("/sys/bus/pci/devices/0000:02:00.1");
  } else {
    lnk("../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.0", "/sys/bus/pci/devices/0000:02:00.0");
    lnk("../../../devices/pci0000:00/0000:00:1d.4/0000:02:00.1", "/sys/bus/pci/devices/0000:02:00.1");
  }
  if (c->dg == DG_IDLE || c->dg == DG_BUSY) lnk("../../../../bus/pci/drivers/nouveau", FN0 "/driver");
  else rm(FN0 "/driver");
  put("/sys/module/nouveau/refcnt", c->dg == DG_BUSY ? "1\n" : "0\n");
  prop_set("vendor.gb.nouveau.result", c->sp == SP_CRASHLOOP ? "crashloop" : c->dg == DG_LOADING ? "loading" : "clean");
  g_rb.phase = c->dg == DG_REBIND ? RB_SEEN : RB_NONE;
  g_cmd_kill = c->sp == SP_KILL;
  g_cmd_test = c->sp == SP_TEST;
  if (c->sp == SP_KEEP) put("/dev/gb_dgpu/keep", "1\n"); else rm("/dev/gb_dgpu/keep");
  if (c->sp == SP_NOLOAD) put("/dev/gb_dgpu/noload", "1\n"); else rm("/dev/gb_dgpu/noload");
  if (c->sp == SP_WEDGED) put("/dev/gb_dgpu/wedged", "1\n"); else rm("/dev/gb_dgpu/wedged");
  g_wedged = 0;
  snprintf(g_fault, sizeof g_fault, "%s", c->sp == SP_FAULT ? "d0-idle" : "");
  g_dec.phase = c->sp == SP_DETACHING ? DEC_REFCNT : DEC_NONE;
  g_pend.kind = c->sp == SP_PENDING;
  if (c->sp == SP_NOPKG) rm("/odm/etc/gb/gfx_build_id"); else put("/odm/etc/gb/gfx_build_id", T_GFX "\n");
}
static void test_gfx_matrix(void) {
  gfx_tree("tree_gfx_matrix");
  int dtx_fd = g_dtx.fd, n = 0, bad = 0, nv = 0;
  g_submit_hook = count_submit;                   /* no forks: the session marker is set by hand per combination */
  quiet(1);
  struct mxc c;
  for (c.re = 0; c.re < RE_N; c.re++) {
    mx_record(c.re);
    for (c.mo = 0; c.mo < MO_N; c.mo++)
      for (c.ba = 0; c.ba < BA_N; c.ba++)
        for (c.dg = 0; c.dg < DG_N; c.dg++)
          for (c.sp = 0; c.sp < SP_N; c.sp++) {
            mx_apply(&c, dtx_fd);
            inventory();
            g_refcnt = nouveau_refcnt();
            holders_clear();
            gfx_inputs();
            g_gfx.active = 0;                     /* each combination starts from Intel with the marker on disk */
            g_sess.have = 1;
            g_sess.job = g_sess.stale = 0;
            g_gfx.n16 = 0;
            gfx_eval();
            const char *want = oracle(&c), *wa = strcmp(want, "ok") ? "intel" : "nvidia";
            n++;
            nv += !strcmp(wa, "nvidia");
            if (strcmp(prop("vendor.gb.gfx_reason"), want) || strcmp(prop("vendor.gb.gfx_active"), wa) ||
                (!strcmp(wa, "nvidia") && mode_of(RENDER) != 0666)) {
              if (bad++ < 12) {
                quiet(0);
                printf("FAIL matrix mo=%d ba=%d dg=%d re=%d sp=%d: got %s/%s want %s/%s (node %o)\n", c.mo, c.ba, c.dg, c.re, c.sp,
                       prop("vendor.gb.gfx_active"), prop("vendor.gb.gfx_reason"), wa, want, mode_of(RENDER));
                quiet(1);
              }
            }
          }
  }
  quiet(0);
  g_submit_hook = NULL;
  g_dtx.fd = dtx_fd;
  CHECK(n == 5 * 3 * 6 * 7 * 12 && !bad, "GP-1B-01 derivation matrix: %d combinations (%d nvidia), %d mismatches against the 23.2 oracle", n, nv, bad);
  printf("GP-1B-01 matrix: %d combinations (mode 5 x base 3 x dGPU 6 x record 7 x special 12), %d nvidia, %d mismatches\n", n, nv, bad);
}

/* ================================================================== GP-1B-02 transition order */
static void test_gfx_transitions(void) {
  gfx_tree("tree_gfx_trans");
  g_trace = tr;
  ec.log[0] = 0;
  status_tick(T);
  CHECK(!strcmp(prop("vendor.gb.gfx_active"), "intel") && !strcmp(prop("vendor.gb.gfx_reason"), "loading") && g_sess.job == 1,
        "first eligible tick: OP_MARK submitted, reason loading until it reports (%s)", prop("vendor.gb.gfx_reason"));
  settle();
  pump(500, 100);
  int f = logpos("setprop:vendor.gb.nouveau.foreign=1"), m = logpos("MARK:gfx_session"), ch = logpos("chmod:renderD129=0666"),
      a = logpos("setprop:vendor.gb.gfx_active=nvidia"), o = logpos("setprop:vendor.gb.gfx_reason=ok");
  CHECK(f >= 0 && m > f && ch > m && a > ch && o > a, "GP-1B-02 intel->nvidia: foreign, OP_MARK result, chmod 0666, gfx_active: '%s'", ec.log);
  CHECK(is_nvidia() && exists("/dev/gb_dgpu/foreign") && mode_of(RENDER) == 0666 && mode_of("/dev/dri/card1") == 0600 && g_gfx.seq == 1,
        "nvidia: foreign file, render 0666, card 0600, gfx_seq 1");
  CHECK(!strncmp(raw(SESS_PATH), "boot=" T_BOOT " t=", 42) && mode_of(SESS_PATH) == 0600, "gfx_session '%s' 0600 (%o)", raw(SESS_PATH), mode_of(SESS_PATH));
  ec.log[0] = 0;
  prop_set("persist.vendor.gb.gfx_mode", "intel");
  T += 2000;
  status_tick(T);
  settle();
  int a2 = logpos("setprop:vendor.gb.gfx_active=intel"), r2 = logpos("setprop:vendor.gb.gfx_reason=mode"),
      c2 = logpos("chmod:renderD129=0660"), u2 = logpos("UNMARK:gfx_session"), z = logpos("unclean=0");
  CHECK(a2 >= 0 && r2 > a2 && c2 > r2 && u2 > c2 && z > u2, "GP-1B-02 nvidia->intel: setprop first, chmod 0660, OP_UNMARK, gfx_unclean=0: '%s'", ec.log);
  CHECK(!exists(SESS_PATH) && !g_gfx.active && g_gfx.seq == 2 && !strcmp(prop("vendor.gb.gfx_reason"), "mode"), "intel: session removed, gfx_seq 2");
  /* a failed marker write: stay Intel (reason loading), node 0660, retried at the next tick */
  gfx_tree("tree_gfx_markfail");
  g_mark_op = mark_fail;                          /* (inherited by the forked worker) */
  gfx_run(5000);
  CHECK(!g_gfx.active && !strcmp(prop("vendor.gb.gfx_reason"), "loading") && mode_of(RENDER) == 0660 && !exists(SESS_PATH),
        "marker write fails: stays Intel (loading), node 0660");
  g_mark_op = real_mark_op;
  pump(5000, 500);
  CHECK(is_nvidia() && exists(SESS_PATH), "marker written at a later tick: nvidia");
}

/* ================================================================== GP-1B-03 spoofing */
static void rec_text(char *b, size_t n, int skip, const char *key, const char *val) {
  static const char *const K[] = {"verdict", "reason", "build", "krel", "gfx", "t", "attempts", "crashes", "report"};
  const char *V[] = {"pass", "", T_BUILD, T_KREL, T_GFX, "1761390000", "1", "0", "selftest-3.txt"};
  size_t k = 0;
  b[0] = 0;
  for (int i = 0; i < 9; i++) {
    if (i == skip) continue;
    const char *v = key && !strcmp(key, K[i]) ? val : V[i];
    k += (size_t)snprintf(b + k, n - k, "%s=%s\n", K[i], v);
  }
}
static void test_gfx_spoof(void) {
  char b[4096];
  gfx_tree("tree_gfx_spoof");
  rm(REC_PATH);
  prop_set("persist.vendor.gb.gfx_ok", "1");
  gfx_run(3000);
  CHECK(!strcmp(prop("vendor.gb.gfx_reason"), "untested") && !g_gfx.active && mode_of(RENDER) == 0660 &&
        !strcmp(prop("persist.vendor.gb.gfx_ok"), ""), "GP-1B-03 gfx_ok=1 without a record: untested, node 0660, mirror rewritten");
  prop_set("vendor.gb.gfx_active", "nvidia");
  chmod(rpath((char[PATH_MAX]){0}, PATH_MAX, RENDER), 0666);
  T += 2000;
  status_tick(T);
  CHECK(!strcmp(prop("vendor.gb.gfx_active"), "intel") && mode_of(RENDER) == 0660, "external gfx_active=nvidia overwritten, node back to 0660 (%o)", mode_of(RENDER));
  /* the record: symlink, not root-owned, > 2 KiB, a key missing, malformed values */
  gfx_tree("tree_gfx_rec");
  gfx_inputs();                                   /* the current identity */
  rec_validate("t");
  CHECK(gfx_ok_now() == 1, "valid record: pass");
  rec_text(b, sizeof b, -1, NULL, NULL);
  put("/data/vendor/gb/selftest/elsewhere", b);
  rm(REC_PATH);
  lnk("elsewhere", REC_PATH);
  rec_validate("t");
  CHECK(gfx_ok_now() == -1, "record is a symlink: invalid");
  rm(REC_PATH);
  rec_ok("pass", "", 1, 0);
  g_owner_root = owner_never;
  rec_validate("t");
  CHECK(gfx_ok_now() == -1, "record not root-owned (hook): invalid");
  g_owner_root = real_owner_root;
  rec_text(b, sizeof b, -1, NULL, NULL);
  size_t l = strlen(b);
  snprintf(b + l, sizeof b - l, "padding=");
  l = strlen(b);
  memset(b + l, 'x', 2100 - l);
  b[2100] = '\n';
  b[2101] = 0;
  put(REC_PATH, b);
  rec_validate("t");
  CHECK(gfx_ok_now() == -1, "record > 2 KiB: invalid");
  int missing_ok = 1;
  for (int i = 0; i < 9; i++) {
    rec_text(b, sizeof b, i, NULL, NULL);
    put(REC_PATH, b);
    rec_validate("t");
    if (gfx_ok_now() != -1) missing_ok = 0;
  }
  CHECK(missing_ok, "every one of the 9 keys missing: invalid");
  static const char *const BAD[][2] = {{"verdict", "maybe"}, {"reason", "Upper Case"}, {"t", "12ab"}, {"attempts", "-1"},
                                       {"report", "../x.txt"}, {"build", ""}, {"crashes", "1234567"}};
  int bad_ok = 1;
  for (unsigned i = 0; i < sizeof BAD / sizeof BAD[0]; i++) {
    rec_text(b, sizeof b, -1, BAD[i][0], BAD[i][1]);
    put(REC_PATH, b);
    rec_validate("t");
    if (gfx_ok_now() != -1) { bad_ok = 0; printf("FAIL accepted %s=%s\n", BAD[i][0], BAD[i][1]); }
  }
  CHECK(bad_ok, "malformed values: invalid");
  rec_text(b, sizeof b, -1, NULL, NULL);
  l = strlen(b);
  snprintf(b + l, sizeof b - l, "verdict=pass\n");
  put(REC_PATH, b);
  rec_validate("t");
  CHECK(gfx_ok_now() == -1, "a duplicate key: invalid");
  /* a spoofed init.svc.gb_nouveau_off=running never stops the node enforcement (only gb_power's own start does) */
  gfx_tree("tree_gfx_offspoof");
  gfx_run(3000);
  CHECK(is_nvidia() && mode_of(RENDER) == 0666, "nvidia, 0666");
  prop_set("init.svc.gb_nouveau_off", "running");
  chmod(rpath((char[PATH_MAX]){0}, PATH_MAX, RENDER), 0660);
  T += 2000;
  status_tick(T);
  CHECK(mode_of(RENDER) == 0666, "spoofed init.svc.gb_nouveau_off=running: enforcement goes on (0660 -> 0666, %o)", mode_of(RENDER));
}

/* ================================================================== GP-1B-04 Settings push */
static int nq_count(const char *key, const char *in_body) {
  int k = 0;
  for (int i = 0; i < g_nqn; i++) if (!strcmp(g_nq[i].key, key) && (!in_body || strstr(g_nq[i].body, in_body))) k++;
  return k;
}
static void test_gfx_settings(void) {
  gfx_tree("tree_gfx_set");
  prop_set("sys.boot_completed", "");
  gfx_run(5000);
  CHECK(!count_files_with("kind=settings") && !nq_count("set:all_apps", NULL), "GP-1B-04 nothing before sys.boot_completed=1");
  prop_set("sys.boot_completed", "1");
  T += 2000;
  status_tick(T);
  CHECK(count_files_with("kind=settings") == 3 &&
        notified("kind=settings\nkey=updatable_driver_all_apps\nvalue=0\n") &&
        notified("kind=settings\nkey=updatable_driver_production_opt_in_apps\nsrc=gfx_optin\n") &&
        notified("kind=settings\nkey=updatable_driver_production_opt_out_apps\nsrc=gfx_optout\n"),
        "boot push: exactly the three messages (%d)", count_files_with("kind=settings"));
  CHECK(exists(OPTIN) && exists(OPTOUT) && mode_of(OPTIN) == 0644 && mode_of(OPTOUT) == 0644, "gfx_optin/gfx_optout written (0644) before the push");
  pump(20000, 1000);
  CHECK(count_files_with("kind=settings") == 3, "once per boot (%d)", count_files_with("kind=settings"));
  prop_set("persist.vendor.gb.gfx_mode", "intel");
  T += 2000;
  status_tick(T);
  CHECK(count_files_with("kind=settings") == 4 && notified("key=updatable_driver_all_apps\nvalue=3\n"), "mode intel: all_apps=3 queued");
  T += 2000;
  status_tick(T);
  CHECK(count_files_with("kind=settings") == 4, "no repeat while the value is unchanged");
  /* newest wins while queued (the notify directory is gone: messages wait in memory) */
  char cmd[PATH_MAX + 32];
  snprintf(cmd, sizeof cmd, "rm -rf '%s/data/vendor/gb/notify'", g_root);
  if (system(cmd)) {}
  prop_set("persist.vendor.gb.gfx_mode", "auto");
  T += 2000;
  status_tick(T);
  prop_set("persist.vendor.gb.gfx_mode", "intel");
  T += 2000;
  status_tick(T);
  CHECK(nq_count("set:all_apps", NULL) == 1 && nq_count("set:all_apps", "value=3\n") == 1, "newest wins: one queued all_apps (value 3)");
  put("/data/vendor/gb/notify/.keep", "");
  rm("/data/vendor/gb/notify/.keep");
  nq_flush();
  CHECK(count_files_with("updatable_driver_all_apps") == 1 && notified("value=3\n") && !notified("value=0\n"), "flushed: only the newest");
  /* all_apps=3 in kill-switch, test, recovered and Intel boots */
  static const char *const W3[] = {"kill switch", "test boot", "recovered (keep)", "recovered (forced)", "Intel mode", "self-test fail"};
  for (unsigned i = 0; i < sizeof W3 / sizeof W3[0]; i++) {
    gfx_tree("tree_gfx_set3");
    if (i == 0) g_cmd_kill = 1;
    if (i == 1) g_cmd_test = 1;
    if (i == 2) put("/dev/gb_dgpu/keep", "1\n");
    if (i == 3) g_gfx.forced = 1;
    if (i == 4) prop_set("persist.vendor.gb.gfx_mode", "intel");
    if (i == 5) rec_ok("fail", "ahb-i915-readback", 1, 0);
    gfx_run(3000);
    CHECK(notified("key=updatable_driver_all_apps\nvalue=3\n") && !notified("value=0\n") && !g_gfx.active, "%s: all_apps=3 at the boot push", W3[i]);
  }
  /* never with cap 0 (no driver package) or without DTX (VMware, D8) */
  gfx_tree("tree_gfx_set_nocap");
  rm("/odm/etc/gb/gfx_build_id");
  gfx_run(5000);
  CHECK(!count_files_with("kind=settings") && !exists(OPTIN) && !strcmp(prop("vendor.gb.gfx_reason"), "nopkg"), "cap 0: no settings, no files");
  gfx_tree("tree_gfx_set_nodtx");
  dtx_close();
  g_dtx_on = 0;
  gfx_run(5000);
  CHECK(!count_files_with("kind=settings") && !strcmp(prop("vendor.gb.gfx_reason"), "nodtx"), "no DTX: no settings message");
  g_dtx_on = 1;
}

/* ================================================================== GP-1B-05 per-app choices */
static void big_pkgs(int n, int long_names) {      /* packages.list with n extra apps (com.example.* kept) */
  static char b[300000];
  size_t k = (size_t)snprintf(b, sizeof b, "gb.sb3.gpu 10123 0 /d platform none 0 1\ncom.example.chat 10200 0 /d default none 0 1\n"
                                         "com.example.game 10300 0 /d default none 0 1\ncom.example.other 10500 0 /d default none 0 1\n");
  for (int i = 0; i < n; i++) {
    if (long_names) {
      char nm[128];
      int l = snprintf(nm, sizeof nm, "com.p%03d.", i);
      memset(nm + l, 'q', 127 - l);
      nm[127] = 0;
      k += (size_t)snprintf(b + k, sizeof b - k, "%s %d 0 /d default none 0 1\n", nm, 11000 + i);
    } else {
      k += (size_t)snprintf(b + k, sizeof b - k, "com.big.%c%03d %d 0 /d default none 0 1\n", i < 400 ? 'g' : 'a', i < 400 ? i : i - 400, 11000 + i);
    }
  }
  put("/data/system/packages.list", b);
  g_pkgs.ok = 0;                                  /* re-read now (mtime granularity) */
  pkgs_refresh(1);
}
static void test_gfx_prefs(void) {
  gfx_tree("tree_gfx_prefs");
  status_tick(T);
  int c = cl_pair(0, 100);
  const char *r = req(c, "set-app-intel com.example.game:1\n");
  CHECK(!strcmp(r, "{\"v\":1,\"ok\":true,\"cmd\":\"set-app-intel\",\"pkg\":\"com.example.game\",\"choice\":\"intel\",\"effect\":\"restart-app\"}"),
        "GP-1B-05 set-app-intel :1: %s", r);
  CHECK(strstr(raw(PREFS), "\ncom.example.game intel\n") && mode_of(PREFS) == 0644, "file line, 0644: %s", raw(PREFS));
  r = req(c, "set-app-nvidia com.example.game:1\n");
  CHECK(strstr(r, "\"choice\":\"nvidia\"") && strstr(raw(PREFS), "com.example.game nvidia") && !strstr(raw(PREFS), "com.example.game intel"),
        "set-app-nvidia replaces intel: %s", r);
  r = req(c, "set-app-intel com.example.game:0\n");
  CHECK(strstr(r, "\"choice\":\"nvidia\""), ":0 clears only its own choice: %s", r);
  r = req(c, "set-app-nvidia com.example.game:0\n");
  CHECK(strstr(r, "\"choice\":\"default\"") && !strstr(raw(PREFS), "com.example.game"), "cleared: default: %s", r);
  req(c, "set-app-intel com.example.other:1\n");
  req(c, "set-app-nvidia com.example.chat:1\n");
  req(c, "set-app-intel com.example.chat:1\n");
  const char *f = raw(PREFS), *pc = strstr(f, "com.example.chat intel\n"), *po = strstr(f, "com.example.other intel\n");
  CHECK(pc && po && pc < po && count_in(f, "com.example.chat") == 1, "sorted and unique: %s", f);
  CHECK(strstr(req(c, "set-app-intel com.nothere.app:1\n"), "\"err\":\"ENOENT\""), "unknown package: ENOENT");
  static const char *const BADARG[] = {"set-app-intel com.example.game\n", "set-app-intel com.example.game:2\n",
                                       "set-app-intel bad..name:1\n", "set-app-nvidia :1\n", "set-app-intel com.example.game:1:1\n",
                                       "set-app-nvidia\n"};
  int ok = 1;
  for (unsigned i = 0; i < sizeof BADARG / sizeof BADARG[0]; i++)
    if (!strstr(req(c, BADARG[i]), "\"err\":\"EINVAL\",\"msg\":\"bad argument\"")) { ok = 0; printf("FAIL not EINVAL: %s", BADARG[i]); }
  CHECK(ok, "bad syntax: EINVAL bad argument");
  /* clearing a choice of an uninstalled package works; adding one does not */
  T += 6000;
  put("/data/system/packages.list", "gb.sb3.gpu 10123 0 /d platform none 0 1\ncom.example.other 10500 0 /d default none 0 1\n");
  r = req(c, "set-app-intel com.example.chat:0\n");
  CHECK(strstr(r, "\"choice\":\"default\"") && !strstr(raw(PREFS), "com.example.chat"), "clearing an uninstalled package: %s", r);
  /* atomic: a planted symlink at the temp name is replaced, never followed */
  put("/data/vendor/victim", "secret\n");
  lnk("../victim", PREFS ".tmp");
  r = req(c, "set-app-intel com.example.other:0\n");
  CHECK(strstr(r, "\"ok\":true") && !strcmp(get("/data/vendor/victim"), "secret") && !strstr(raw(PREFS), "com.example.other"),
        "gfx_app_prefs.tmp symlink not followed (O_EXCL)");
  drop(c);
  /* 128 choices at most */
  gfx_tree("tree_gfx_prefs128");
  big_pkgs(130, 0);
  status_tick(T);
  c = cl_pair(0, 100);
  int nok = 0;
  for (int i = 0; i < 128; i++) {
    char l[64];
    snprintf(l, sizeof l, "set-app-intel com.big.g%03d:1\n", i);
    nok += strstr(req(c, l), "\"ok\":true") != NULL;
  }
  r = req(c, "set-app-intel com.big.g128:1\n");
  CHECK(nok == 128 && !strcmp(r, "{\"v\":1,\"ok\":false,\"cmd\":\"set-app-intel\",\"err\":\"EBUSY\",\"msg\":\"too many choices\"}"),
        "128 choices accepted, the 129th: %s", r);
  CHECK(strstr(req(c, "set-app-nvidia com.big.g005:1\n"), "\"ok\":true"), "replacing an existing choice at 128 still works");
  drop(c);
  /* cap 0: ENODEV */
  gfx_tree("tree_gfx_prefs_nocap");
  rm("/odm/etc/gb/gfx_build_id");
  status_tick(T);
  c = cl_pair(0, 100);
  CHECK(!strcmp(req(c, "set-app-intel com.example.game:1\n"),
                "{\"v\":1,\"ok\":false,\"cmd\":\"set-app-intel\",\"err\":\"ENODEV\",\"msg\":\"no NVIDIA app graphics on this image\"}"), "cap 0: ENODEV");
  drop(c);
  /* the seed: used once when the file is absent, ignored when it exists */
  gfx_tree("tree_gfx_seed");
  put("/odm/etc/gb/gfx_app_prefs.default", "com.example.chat nvidia\n");
  status_tick(T);
  CHECK(strstr(raw(PREFS), "com.example.chat nvidia") && g_prefs.n == 1, "no file: seeded from /odm/etc/gb/gfx_app_prefs.default");
  gfx_tree("tree_gfx_seed2");
  put("/odm/etc/gb/gfx_app_prefs.default", "com.example.chat nvidia\n");
  put(PREFS, "# empty\n");
  status_tick(T);
  CHECK(g_prefs.n == 0 && !strstr(raw(PREFS), "chat"), "file present: the seed is ignored");
  gfx_tree("tree_gfx_seed3");
  status_tick(T);
  CHECK(!exists(PREFS) && g_prefs.n == 0, "no file and no seed: no choices, no file");
}

/* ================================================================== GP-1B-06 catalog protocol */
static void test_gfx_catalog(void) {
  gfx_tree("tree_gfx_cat");
  status_tick(T);
  int c = cl_pair(10123, 102);
  CHECK(!strcmp(req(c, "apps-begin 3\n"), "{\"v\":1,\"ok\":true,\"cmd\":\"apps-begin\",\"n\":3}"), "GP-1B-06 apps-begin 3");
  CHECK(!strcmp(req(c, "apps-add g:com.example.game\n"), "{\"v\":1,\"ok\":true,\"cmd\":\"apps-add\",\"staged\":1}"), "apps-add g");
  CHECK(!strcmp(req(c, "apps-add a:com.example.chat,com.example.other\n"), "{\"v\":1,\"ok\":true,\"cmd\":\"apps-add\",\"staged\":3}"), "apps-add a (2)");
  const char *r = req(c, "apps-commit 3\n");
  CHECK(!strcmp(r, "{\"v\":1,\"ok\":true,\"cmd\":\"apps-commit\",\"n\":3,\"dropped\":0,\"opt_in_n\":1,\"apps_sync\":1}"), "apps-commit 3: %s", r);
  char want[256];
  snprintf(want, sizeof want, "# pkgs %lld %lld\ncom.example.chat a\ncom.example.game g\ncom.example.other a\n", g_pkgs.mt, g_pkgs.sz);
  CHECK(!strcmp(raw("/data/vendor/gb/gfx_catalog"), want) && mode_of("/data/vendor/gb/gfx_catalog") == 0644, "catalog file with the stamp header: %s", raw("/data/vendor/gb/gfx_catalog"));
  CHECK(!strcmp(raw(OPTIN), "com.example.game\n"), "policy games: the game is opted in (%s)", raw(OPTIN));
  CHECK(!strcmp(req(c, "apps-commit 3\n"), "{\"v\":1,\"ok\":false,\"cmd\":\"apps-commit\",\"err\":\"EINVAL\",\"msg\":\"no apps-begin\"}"), "staging consumed by the commit");
  req(c, "apps-begin 2\n");
  req(c, "apps-add g:com.example.game\n");
  CHECK(!strcmp(req(c, "apps-commit 2\n"), "{\"v\":1,\"ok\":false,\"cmd\":\"apps-commit\",\"err\":\"EINVAL\",\"msg\":\"count mismatch\"}"), "count mismatch");
  req(c, "apps-begin 1\n");
  CHECK(strstr(req(c, "apps-add g:a.b,c.d\n"), "\"err\":\"EINVAL\",\"msg\":\"count mismatch\""), "more than n: EINVAL");
  CHECK(strstr(req(c, "apps-begin 513\n"), "\"err\":\"EINVAL\""), "> 512: EINVAL");
  CHECK(strstr(req(c, "apps-begin 512\n"), "\"ok\":true"), "512: ok");
  static const char *const SYN[] = {"apps-add com.example.game\n", "apps-add x:abc.d\n", "apps-add g:\n", "apps-add g:a.b,,c.d\n",
                                    "apps-add g:a.b,\n", "apps-add g:a/b.c\n", "apps-add\n"};
  int ok = 1;
  for (unsigned i = 0; i < sizeof SYN / sizeof SYN[0]; i++) if (!strstr(req(c, SYN[i]), "\"err\":\"EINVAL\"")) { ok = 0; printf("FAIL syntax %s", SYN[i]); }
  CHECK(ok, "apps-add syntax errors: EINVAL");
  req(c, "apps-begin 4\n");
  req(c, "apps-add g:com.example.game,com.nothere.app\n");
  req(c, "apps-add a:com.example.game,com.example.chat\n");
  r = req(c, "apps-commit 4\n");
  CHECK(strstr(r, "\"n\":2,\"dropped\":2"), "unknown and duplicate packages dropped: %s", r);
  req(c, "apps-begin 2\n");
  req(c, "apps-add g:com.example.game\n");
  req(c, "apps-begin 1\n");                       /* a new apps-begin restarts the staging */
  CHECK(strstr(req(c, "apps-add a:com.example.chat\n"), "\"staged\":1") && strstr(req(c, "apps-commit 1\n"), "\"n\":1"), "apps-begin restarts the staging");
  int c2 = cl_pair(10123, 102);
  req(c2, "apps-begin 1\n");
  req(c2, "apps-add g:com.example.game\n");
  drop(c2);
  int c3 = cl_pair(10123, 102);
  CHECK(strstr(req(c3, "apps-commit 1\n"), "\"msg\":\"no apps-begin\""), "staging is per connection and discarded on close");
  drop(c3);
  /* apps_sync: 0 after a packages.list change, 1 after the next commit */
  CHECK(strstr(req(c, "status\n"), "\"apps_sync\":1"), "apps_sync 1");
  T += 6000;
  put("/data/system/packages.list", "gb.sb3.gpu 10123 0 /d platform none 0 1\ncom.example.chat 10200 0 /d default none 0 1\n"
                                    "com.example.game 10300 0 /d default none 0 1\ncom.example.new 10600 0 /d default none 0 1\n");
  status_tick(T);
  CHECK(strstr(req(c, "status\n"), "\"apps_sync\":0") && strstr(req(c, "get-mode\n"), "\"apps_sync\":0"), "packages.list changed: apps_sync 0");
  req(c, "apps-begin 1\n");
  req(c, "apps-add g:com.example.new\n");
  CHECK(strstr(req(c, "apps-commit 1\n"), "\"apps_sync\":1"), "after the commit: apps_sync 1");
  drop(c);
}

/* ================================================================== GP-1B-07 opt-in computation vs an oracle (24.4) */
#define POOL 20
static unsigned g_rng = 12345;
static unsigned rnd(unsigned n) { g_rng = g_rng * 1103515245u + 12345u; return (g_rng >> 16) % n; }
/* oracle: base = all ? catalog : catalog games; opt_in = (base + nvidia choices) - intel choices, installed, sorted */
static void optin_oracle(int all, const int *cat, const int *pref, const int *inst, char *out, size_t n, char *outo, size_t no) {
  size_t k = 0, ko = 0;
  out[0] = outo[0] = 0;
  for (int i = 0; i < POOL; i++) {                /* names com.pool.pNN sort in index order */
    int base = cat[i] && (all || cat[i] == 'g');
    if ((base || pref[i] == 'n') && pref[i] != 'i' && inst[i]) k += (size_t)snprintf(out + k, n - k, "com.pool.p%02d\n", i);
    if (pref[i] == 'i') ko += (size_t)snprintf(outo + ko, no - ko, "com.pool.p%02d\n", i);
  }
}
static void test_gfx_optin(void) {
  gfx_tree("tree_gfx_optin");
  put("/data/system/packages.list", "gb.sb3.gpu 10123 0 /d platform none 0 1\ncom.example.chat 10200 0 /d default none 0 1\n"
                                    "com.example.game 10300 0 /d default none 0 1\ncom.example.other 10500 0 /d default none 0 1\n"
                                    "com.example.extra 10700 0 /d default none 0 1\n");
  stamp_catalog("com.example.chat a\ncom.example.game g\ncom.example.other a\ncom.gone.app g\n");
  put(PREFS, "com.example.chat nvidia\ncom.example.game intel\ncom.example.extra nvidia\n");
  status_tick(T);
  CHECK(!strcmp(raw(OPTIN), "com.example.chat\ncom.example.extra\n") && !strcmp(raw(OPTOUT), "com.example.game\n"),
        "GP-1B-07 games: (games + Use NVIDIA) - Always Intel, installed only: '%s' / '%s'", raw(OPTIN), raw(OPTOUT));
  prop_set("persist.vendor.gb.gfx_apps", "all");
  T += 2000;
  status_tick(T);
  CHECK(!strcmp(raw(OPTIN), "com.example.chat\ncom.example.extra\ncom.example.other\n"), "all: every catalog app + Use NVIDIA - Always Intel: '%s'", raw(OPTIN));
  char p[PATH_MAX];
  struct stat s1, s2;
  stat(rpath(p, sizeof p, OPTIN), &s1);
  int c = cl_pair(0, 100);
  CHECK(strstr(req(c, "set-app-policy all\n"), "\"policy\":\"all\",\"opt_in_n\":3,\"effect\":\"restart-apps\""), "set-app-policy reply");
  T += 2000;
  status_tick(T);
  stat(rpath(p, sizeof p, OPTIN), &s2);
  CHECK(s1.st_ino == s2.st_ino, "same content: gfx_optin not rewritten (inode %lu -> %lu)", (unsigned long)s1.st_ino, (unsigned long)s2.st_ino);
  CHECK(strstr(req(c, "set-app-policy games\n"), "\"opt_in_n\":2") && !strcmp(raw(OPTIN), "com.example.chat\ncom.example.extra\n"), "set-app-policy games");
  CHECK(!strcmp(prop("persist.vendor.gb.gfx_apps"), "games"), "persist.vendor.gb.gfx_apps written");
  drop(c);
  /* random scenarios against the oracle */
  gfx_tree("tree_gfx_optin_rnd");
  status_tick(T);
  int bad = 0;
  quiet(1);
  for (int s = 0; s < 60; s++) {
    static char pl[8192], cb[8192], pb[8192], want[4096], wanto[4096];
    int cat[POOL], pref[POOL], inst[POOL], all = rnd(2);
    size_t kp = (size_t)snprintf(pl, sizeof pl, "gb.sb3.gpu 10123 0 /d platform none 0 1\n"), kc = 0, kb = 0;
    for (int i = 0; i < POOL; i++) {
      inst[i] = rnd(4) != 0;
      cat[i] = (int[]){0, 'g', 'a'}[rnd(3)];
      pref[i] = (int[]){0, 0, 'i', 'n'}[rnd(4)];
      if (inst[i]) kp += (size_t)snprintf(pl + kp, sizeof pl - kp, "com.pool.p%02d %d 0 /d default none 0 1\n", i, 12000 + i);
      if (cat[i]) kc += (size_t)snprintf(cb + kc, sizeof cb - kc, "com.pool.p%02d %c\n", i, cat[i]);
      if (pref[i]) kb += (size_t)snprintf(pb + kb, sizeof pb - kb, "com.pool.p%02d %s\n", i, pref[i] == 'i' ? "intel" : "nvidia");
    }
    cb[kc] = pb[kb] = 0;
    put("/data/system/packages.list", pl);
    g_pkgs.ok = 0;
    pkgs_refresh(1);
    stamp_catalog(cb);
    put(PREFS, pb);
    prop_set("persist.vendor.gb.gfx_apps", all ? "all" : "games");
    g_prefs.loaded = g_cat.loaded = 0;
    g_apps.dirty = 1;
    gfx_inputs();
    gfx_apps_eval();
    optin_oracle(all, cat, pref, inst, want, sizeof want, wanto, sizeof wanto);
    if (strcmp(raw(OPTIN), want) || strcmp(raw(OPTOUT), wanto)) {
      if (bad++ < 3) { quiet(0); printf("FAIL optin scenario %d: got '%s' want '%s'\n", s, raw(OPTIN), want); quiet(1); }
    }
  }
  quiet(0);
  CHECK(!bad, "60 random scenarios (catalog x choices x installed x policy) against the 24.4 oracle: %d mismatches", bad);
  /* over 512: class a goes first */
  gfx_tree("tree_gfx_optin_big");
  big_pkgs(700, 0);
  {
    static char cb[200000];
    size_t k = 0;
    for (int i = 0; i < 400; i++) k += (size_t)snprintf(cb + k, sizeof cb - k, "com.big.g%03d g\n", i);
    for (int i = 0; i < 300; i++) k += (size_t)snprintf(cb + k, sizeof cb - k, "com.big.a%03d a\n", i);
    cb[k] = 0;
    stamp_catalog(cb);
  }
  prop_set("persist.vendor.gb.gfx_apps", "all");
  quiet(1);
  status_tick(T);
  quiet(0);
  const char *o = raw(OPTIN);
  int games = 0, apps = 0, lines = count_in(o, "\n");
  for (int i = 0; i < 400; i++) { char nm[32]; snprintf(nm, sizeof nm, "com.big.g%03d\n", i); games += strstr(o, nm) != NULL; }
  for (int i = 0; i < 300; i++) { char nm[32]; snprintf(nm, sizeof nm, "com.big.a%03d\n", i); apps += strstr(o, nm) != NULL; }
  CHECK(lines == 512 && games == 400 && apps == 112 && strstr(o, "com.big.a111\n") && !strstr(o, "com.big.a112\n") && g_apps.n_in == 512,
        "700 candidates: 512 kept, every game, the first 112 other apps (%d lines, %d games, %d apps)", lines, games, apps);
}

/* ================================================================== GP-1B-08 node enforcement */
static void test_gfx_nodes(void) {
  char p[PATH_MAX], c1[PATH_MAX];
  gfx_tree("tree_gfx_nodes");
  gfx_run(3000);
  CHECK(is_nvidia() && mode_of(RENDER) == 0666, "nvidia: render 0666");
  chmod(rpath(p, sizeof p, RENDER), 0660);
  T += 2000;
  status_tick(T);
  CHECK(mode_of(RENDER) == 0666, "GP-1B-08 a node drifted to 0660 is back at 0666 within one fast tick (%o)", mode_of(RENDER));
  chmod(p, 0);
  T += 2000;
  status_tick(T);
  CHECK(mode_of(RENDER) == 0, "a 0000 node is never raised by the rule (%o)", mode_of(RENDER));
  chmod(p, 0666);
  prop_set("persist.vendor.gb.gfx_mode", "intel");
  T += 2000;
  status_tick(T);
  settle();
  CHECK(!g_gfx.active && mode_of(RENDER) == 0660, "intel: a 0666 node is lowered to 0660 (%o)", mode_of(RENDER));
  chmod(p, 0644);
  T += 2000;
  status_tick(T);
  CHECK(mode_of(RENDER) == 0660, "more permissive than 0660 (0644, other-readable): set to 0660 (%o)", mode_of(RENDER));
  chmod(p, 0600);
  T += 2000;
  status_tick(T);
  CHECK(mode_of(RENDER) == 0600, "less permissive (0600): left alone (%o)", mode_of(RENDER));
  chmod(p, 0666);
  /* (R-5) a decision owns the nodes: untouched; a wedge: every nouveau node 000; while a live gb_nouveau owns the ESP
   * lock and for 30 s after gb_power's own gb_nouveau_off start: still lowered (new inodes too), only the raise waits */
  g_dec.phase = DEC_REFCNT;
  nodes_enforce();
  CHECK(mode_of(RENDER) == 0666, "untouched during a decision");
  g_dec.phase = DEC_NONE;
  put("/dev/gb_dgpu/wedged", "1\n");
  nodes_enforce();
  CHECK(mode_of(RENDER) == 0 && mode_of("/dev/dri/card1") == 0, "R-5 after a wedge: every nouveau node chmod 000 (%o)", mode_of(RENDER));
  rm("/dev/gb_dgpu/wedged");
  g_wedged = 0;
  chmod(p, 0666);
  chmod(rpath(c1, sizeof c1, "/dev/dri/card1"), 0600);
  put("/proc/4300/comm", "sh\n");
  put("/dev/gb_esp.lock/owner", "4300 gb_nouveau 95\n");
  nodes_enforce();
  CHECK(mode_of(RENDER) == 0660, "R-5 a live gb_nouveau owns the ESP lock: a 0666 node is lowered all the same (%o)", mode_of(RENDER));
  g_gfx.active = 1;                               /* (white box: only the raise waits) */
  nodes_enforce();
  CHECK(mode_of(RENDER) == 0660, "... and not raised to 0666 while it owns the lock");
  g_gfx.active = 0;
  rm("/dev/gb_esp.lock/owner");
  g_off.last_start = T;
  prop_set("init.svc.gb_nouveau_off", "running");
  chmod(p, 0666);
  nodes_enforce();
  CHECK(mode_of(RENDER) == 0660, "R-5 within 30 s of gb_power's own gb_nouveau_off start: lowered too (%o)", mode_of(RENDER));
  g_gfx.active = 1;
  nodes_enforce();
  CHECK(mode_of(RENDER) == 0660, "... not raised within the 30 s");
  T += 31000;
  nodes_enforce();
  CHECK(mode_of(RENDER) == 0666, "raised after the 30 s (%o)", mode_of(RENDER));
  g_gfx.active = 0;
  nodes_enforce();
  CHECK(mode_of(RENDER) == 0660, "and lowered again (%o)", mode_of(RENDER));
  /* a new inode (ueventd re-creation, 0666) gets the wanted mode */
  prop_set("init.svc.gb_nouveau_off", "stopped");
  bind_nouveau();
  nodes_enforce();
  CHECK(mode_of(RENDER) == 0660 && mode_of("/dev/dri/card1") == 0600, "re-created nodes: 0660 / 0600");
  /* gfx_active never says nvidia before the node could be raised: inside gb_power's own off window it waits */
  gfx_tree("tree_gfx_nodes_off");
  chmod(rpath(p, sizeof p, RENDER), 0660);
  g_off.last_start = T;
  prop_set("init.svc.gb_nouveau_off", "running");
  gfx_run(3000);
  CHECK(!g_gfx.active && !strcmp(prop("vendor.gb.gfx_reason"), "loading") && mode_of(RENDER) != 0666,
        "own gb_nouveau_off start < 30 s ago and running: no nvidia yet (reason loading)");
  T += 31000;
  pump(3000, 500);
  CHECK(is_nvidia() && mode_of(RENDER) == 0666, "after 30 s: node raised, then nvidia");
}

/* ================================================================== GP-1B-09 reconcile_off */
static void test_gfx_reconcile(void) {
  gfx_tree("tree_gfx_roff");
  prop_set("persist.vendor.gb.gfx_mode", "intel");
  g_ctl_log[0] = 0;
  status_tick(T);
  CHECK(!strstr(g_ctl_log, "gb_nouveau_off"), "GP-1B-09 one tick with refcnt 0: not yet");
  T += 2000;
  status_tick(T);
  CHECK(count_in(g_ctl_log, "ctl.start=gb_nouveau_off") == 1, "intel, bound, refcnt 0 on two ticks: exactly one start '%s'", g_ctl_log);
  T += 2000;
  status_tick(T);
  T += 2000;
  status_tick(T);
  CHECK(count_in(g_ctl_log, "ctl.start=gb_nouveau_off") == 1, "not again within 10 s");
  CHECK(!strstr(ec.log, "UNBIND"), "gb_power never writes unbind '%s'", ec.log);
  static const char *const NO[] = {"keep", "once boot", "test boot", "rebind guard", "off running", "auto running", "decision", "in use", "auto mode"};
  for (unsigned i = 0; i < sizeof NO / sizeof NO[0]; i++) {
    gfx_tree("tree_gfx_roff_no");
    prop_set("persist.vendor.gb.gfx_mode", i == 8 ? "auto" : "intel");
    if (i == 0) put("/dev/gb_dgpu/keep", "1\n");
    if (i == 1) g_cmd_once = 1;
    if (i == 2) g_cmd_test = 1;
    if (i == 3) g_rb.phase = RB_CLEAN;
    if (i == 4) prop_set("init.svc.gb_nouveau_off", "running");
    if (i == 5) prop_set("init.svc.gb_nouveau", "running");
    if (i == 6) g_dec.phase = DEC_REFCNT;
    if (i == 7) put("/sys/module/nouveau/refcnt", "1\n");
    g_ctl_log[0] = 0;
    for (int k = 0; k < 4; k++) {
      T += 3000;
      g_rb.seen_at = T;                           /* (keeps the white-box guard in its clean phase) */
      status_tick(T);
    }
    CHECK(!strstr(g_ctl_log, "ctl.start=gb_nouveau_off"), "no start: %s '%s'", NO[i], g_ctl_log);
    if (i == 7) {
      char b[4096];
      status_json(b, sizeof b, "status", 0);
      CHECK(strstr(b, "\"pending\":\"intel\""), "intel requested while apps hold nouveau: pending=intel");
    }
    g_dec.phase = DEC_NONE;
  }
}

/* ================================================================== GP-1B-10 hot attach */
static void hot_tree(const char *name) {         /* module absent, dGPU absent at gb_power's start */
  char p[PATH_MAX];
  gfx_tree(name);
  rm(FN0 "/driver");
  rm("/sys/module/nouveau/refcnt");
  rmdir(rpath(p, sizeof p, "/sys/module/nouveau"));
  rm("/sys/bus/pci/devices/0000:02:00.0");
  rm("/sys/bus/pci/devices/0000:02:00.1");
  drm_gone();
  g_rs.done = 1;                                  /* (no staged rescan in these cases) */
  g_ctl_log[0] = 0;
  status_tick(T);
}
static void appear(void) { fns_back(); T += 2000; status_tick(T); }
static void vanish(void) { rm("/sys/bus/pci/devices/0000:02:00.0"); rm("/sys/bus/pci/devices/0000:02:00.1"); T += 2000; status_tick(T); }
static void test_gfx_hotattach(void) {
  hot_tree("tree_gfx_hot");
  CHECK(!strstr(g_ctl_log, "ctl.start"), "absent at start: nothing");
  appear();
  CHECK(count_in(g_ctl_log, "ctl.start=gb_nouveau ") == 1, "GP-1B-10 the dGPU appears driverless: one ctl.start gb_nouveau '%s'", g_ctl_log);
  T += 2000;
  status_tick(T);
  CHECK(count_in(g_ctl_log, "ctl.start=gb_nouveau ") == 1, "once per appearance");
  vanish();
  appear();
  vanish();
  appear();
  CHECK(count_in(g_ctl_log, "ctl.start=gb_nouveau ") == 3, "three appearances: three starts");
  vanish();
  appear();
  CHECK(count_in(g_ctl_log, "ctl.start=gb_nouveau ") == 3, "at most 3 per boot");
  static const char *const NO[] = {"intel mode", "kill", "keep", "noload", "wedged", "fault", "rebind watch", "booted unset", "detached"};
  for (unsigned i = 0; i < sizeof NO / sizeof NO[0]; i++) {
    hot_tree("tree_gfx_hot_no");
    if (i == 0) prop_set("persist.vendor.gb.gfx_mode", "intel");
    if (i == 1) g_cmd_kill = 1;
    if (i == 2) put("/dev/gb_dgpu/keep", "1\n");
    if (i == 3) put("/dev/gb_dgpu/noload", "1\n");
    if (i == 4) put("/dev/gb_dgpu/wedged", "1\n");
    if (i == 5) snprintf(g_fault, sizeof g_fault, "rescan");
    if (i == 6) { g_rb.phase = RB_SEEN; g_rb.seen_at = T + 100000; }
    if (i == 7) prop_set("vendor.gb.nouveau.booted", "");
    if (i == 8) g_dtx.base = SDTX_BASE_DETACHED;
    appear();
    CHECK(!strstr(g_ctl_log, "ctl.start=gb_nouveau "), "no start: %s '%s'", NO[i], g_ctl_log);
    g_rb.phase = RB_NONE;
  }
  /* deferred while gb_nouveau runs: not counted, issued when it stops */
  hot_tree("tree_gfx_hot_def");
  prop_set("init.svc.gb_nouveau", "running");
  appear();
  CHECK(!strstr(g_ctl_log, "ctl.start=gb_nouveau ") && g_nv.deferred && g_nv.starts == 0, "init.svc.gb_nouveau running: deferred, not counted");
  T += 2000;
  status_tick(T);
  prop_set("init.svc.gb_nouveau", "stopped");
  T += 2000;
  status_tick(T);
  CHECK(count_in(g_ctl_log, "ctl.start=gb_nouveau ") == 1 && g_nv.starts == 1, "issued when it reads stopped '%s'", g_ctl_log);
  /* set-mode auto while gb_nouveau runs records a deferred start (the rc trigger's start is a no-op then) */
  hot_tree("tree_gfx_hot_setmode");
  prop_set("persist.vendor.gb.gfx_mode", "intel");
  fns_back();
  T += 2000;
  status_tick(T);                                 /* present, driverless, intel: nothing */
  prop_set("init.svc.gb_nouveau", "running");
  int c = cl_pair(0, 100);
  CHECK(strstr(req(c, "set-mode auto\n"), "\"ok\":true") && g_nv.deferred, "set-mode auto with gb_nouveau running: deferred start recorded");
  drop(c);
  prop_set("init.svc.gb_nouveau", "stopped");
  T += 2000;
  status_tick(T);
  CHECK(count_in(g_ctl_log, "ctl.start=gb_nouveau ") == 1, "deferred start issued once gb_nouveau stopped '%s'", g_ctl_log);
}

/* ================================================================== GP-1B-11 rebind guard */
static void rb_tree(const char *name) {           /* nvidia, then the base was detached (functions removed, module kept) */
  gfx_tree(name);
  gfx_run(3000);
  rm(FN0 "/driver");
  drm_gone();
  rm("/sys/bus/pci/devices/0000:02:00.0");
  rm("/sys/bus/pci/devices/0000:02:00.1");
  ec.base = SDTX_BASE_DETACHED;
  ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_DETACHED);
  T += 2000;
  status_tick(T);
  settle();
  g_rs.done = 1;                                  /* (the staged rescan stays out of these cases) */
}
static void reattach(void) { ec.base = SDTX_BASE_ATTACHED; ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_ATTACHED); }
static void test_gfx_rebind(void) {
  char want[160], own[64];
  rb_tree("tree_gfx_rb");
  CHECK(!g_gfx.active && !strcmp(prop("vendor.gb.gfx_reason"), "detached"), "detached: intel");
  clear_notify();
  ec.log[0] = 0;
  reattach();
  snprintf(own, sizeof own, "%d gb_power 95", (int)getpid());
  CHECK(g_rb.phase == RB_WAIT && g_rb.locked && !strcmp(get("/dev/gb_esp.lock/owner"), own), "GP-1B-11 armed on the DTX attach, ESP lock owner '%s'", get("/dev/gb_esp.lock/owner"));
  settle();
  snprintf(want, sizeof want, "mode=rebind t=95 boot=" T_BOOT " argv=-\n");
  CHECK(!strcmp(raw(LOADING_MARKER), want) && mode_of(LOADING_MARKER) == 0600, "marker line exact, 0600: '%s'", raw(LOADING_MARKER));
  CHECK(load_window_open() && !strcmp(prop("vendor.gb.gfx_reason"), "loading"), "the watch is a load window (reason loading)");
  /* a detach during the watch: N15 */
  clear_notify();
  press();
  CHECK(strstr(ec.log, "CANCEL") && !strstr(ec.log, "CONFIRM") && notified(N15_TEXT), "detach during the watch: refused with N15 '%s'", ec.log);
  pump(500, 100);
  /* the kernel's probe binds the re-added dGPU, it runtime-suspends into D3cold */
  fns_back();
  bind_nouveau();
  T += 2000;
  status_tick(T);
  CHECK(g_rb.phase == RB_CLEAN && !exists("/dev/gb_esp.lock"), "healthy (bound + suspended + D3cold): lock released");
  settle();
  pump(1000, 100);
  CHECK(is_nvidia() && notified(N16_TEXT), "nvidia allowed after the 30 s check, N16 (first nvidia after an attach event)");
  CHECK(exists(LOADING_MARKER) && !g_st.clean_seen, "marker kept until +120 s, clean not seen yet");
  pump(120000, 2000);
  settle();
  CHECK(!exists(LOADING_MARKER) && g_rb.phase == RB_NONE && g_st.clean_seen, "+120 s: marker unlinked, clean_seen");
  /* failure: not bound within 30 s */
  rb_tree("tree_gfx_rb_fail");
  clear_notify();
  reattach();
  settle();
  fns_back();
  pump(34000, 1000);                              /* t_seen is the first tick that sees it; + 30 s */
  settle();
  char n02[200];
  snprintf(n02, sizeof n02, N02_FMT, "rebind");
  CHECK(g_rb.phase == RB_NONE && !exists(LOADING_MARKER) && !exists("/dev/gb_esp.lock") && exists("/dev/gb_dgpu/keep") &&
        exists("/dev/gb_dgpu/noload") && !strcmp(g_fault, "rebind") && !strcmp(prop("persist.vendor.gb.gfx_mode"), "intel") &&
        notified(n02) && !strcmp(prop("vendor.gb.gfx_reason"), "crashloop"),
        "failure: unmark, keep + noload, fault rebind, gfx_mode=intel, N02 rebind, reason crashloop (fault %s)", g_fault);
  CHECK(!dgpu_fault_blocks() && !notified("Detaching is blocked"), "fault rebind does not block a detach and posts no N06");
  /* failure: bound but no D3cold within 30 s */
  rb_tree("tree_gfx_rb_d0");
  reattach();
  settle();
  fns_back();
  bind_nouveau();
  put(FN0 "/power_state", "D0\n");
  put(FN0 "/power/runtime_status", "active\n");
  pump(34000, 1000);
  CHECK(!strcmp(g_fault, "rebind") && exists("/dev/gb_dgpu/keep"), "bound, no D3cold within 30 s: failure");
  /* no dGPU within 60 s: unmark, no fault */
  rb_tree("tree_gfx_rb_none");
  reattach();
  settle();
  CHECK(exists(LOADING_MARKER), "armed, marker written");
  pump(61000, 1000);
  settle();
  CHECK(g_rb.phase == RB_NONE && !exists(LOADING_MARKER) && !g_fault[0] && !exists("/dev/gb_esp.lock") && !exists("/dev/gb_dgpu/keep"),
        "no dGPU within 60 s: unmark, lock released, no fault");
  /* a held lock: logged, the guard goes on; a stale one is stolen atomically */
  rb_tree("tree_gfx_rb_lock");
  put("/proc/4300/comm", "sh\n");
  put("/dev/gb_esp.lock/owner", "4300 gb_bootlog 95\n");
  reattach();
  CHECK(g_rb.phase == RB_WAIT && !g_rb.locked && !strcmp(get("/dev/gb_esp.lock/owner"), "4300 gb_bootlog 95"), "lock held by a live process: guard armed without it");
  rb_tree("tree_gfx_rb_stale");
  put("/dev/gb_esp.lock/owner", "4399 gb_nouveau 50\n");   /* dead pid */
  reattach();
  char stale[64];
  snprintf(stale, sizeof stale, "/dev/gb_esp.lock.stale.%d", (int)getpid());
  CHECK(g_rb.phase == RB_WAIT && g_rb.locked && !strcmp(get("/dev/gb_esp.lock/owner"), own) && !exists(stale),
        "stale lock (dead owner): stolen atomically, owner '%s'", get("/dev/gb_esp.lock/owner"));
  /* a marker of this boot already there is left alone */
  rb_tree("tree_gfx_rb_marker");
  put(LOADING_MARKER, "mode=bind t=80 boot=" T_BOOT " argv=-\n");
  reattach();
  settle();
  CHECK(g_rb.phase == RB_WAIT && !g_rb.own_marker && !strcmp(raw(LOADING_MARKER), "mode=bind t=80 boot=" T_BOOT " argv=-\n"),
        "a marker of this boot is left alone");
  /* arming on a 10de PCI add uevent (class 03xxxx), not on the HDA function */
  rb_tree("tree_gfx_rb_uev");
  struct uev u = {.action = "add", .subsystem = "pci", .pci_id = "10DE:10FA", .pci_class = "40300", .devpath = "/devices/x/0000:02:00.1"};
  uev_handle(&u);
  CHECK(g_rb.phase == RB_NONE, "HDA add uevent: no guard");
  u.pci_id = "10DE:1F91";
  u.pci_class = "30200";
  uev_handle(&u);
  CHECK(g_rb.phase == RB_WAIT && !strcmp(g_rb.why, "10de PCI add"), "10de VGA add uevent: armed (%s)", g_rb.why);
  /* the first op of gb_power's own rescan job */
  rb_tree("tree_gfx_rb_rescan");
  ec.base = SDTX_BASE_ATTACHED;                   /* attached without the DTX event: the rescan arms the guard */
  g_dtx.base = SDTX_BASE_ATTACHED;
  ec.log[0] = 0;
  g_rs.done = 0;
  k_restore_at = 1;
  T += 2000;
  status_tick(T);
  for (int k = 0; k < 40 && !strstr(ec.log, "rescan=1"); k++) pump(500, 100);
  settle();
  int mk = logpos("MARK:nouveau.loading"), rs = logpos("WRITE:0000:00:1d.4/rescan=1");
  CHECK(mk >= 0 && rs > mk && !strcmp(g_rb.why, "rescan"), "J_RESCAN: OP_MARK first, then the rescan write '%s'", ec.log);
}

/* ================================================================== GP-1B-12 detach integration */
static void test_gfx_detach(void) {
  gfx_tree("tree_gfx_det");
  gfx_run(3000);
  CHECK(is_nvidia(), "nvidia before the detach");
  put("/sys/module/nouveau/refcnt", "1\n");
  proc_holder(4242, "game", 10300, 0, 1);
  T += 2000;
  status_tick(T);
  g_trace = tr;
  ec.log[0] = 0;
  g_ctl_log[0] = 0;
  press();
  int a = logpos("setprop:vendor.gb.gfx_active=intel"), l = logpos(" LOCK "), z = logpos("chmod:renderD129=0000");
  CHECK(a >= 0 && l > a && z > a, "GP-1B-12 gfx_active=intel before the decision's LATCH_LOCK and before chmod 000 '%s'", ec.log);
  CHECK(strstr(g_ctl_log, "ctl.stop=gb_gfx_selftest"), "ctl.stop gb_gfx_selftest at decision start '%s'", g_ctl_log);
  pump(4500, 250);
  CHECK(dec_running(), "only app holders: still waiting at 4.5 s");
  pump(1500, 250);
  CHECK(!dec_running() && strstr(ec.log, "CANCEL") && !strstr(ec.log, "UNBIND") && g_gfx.detach_ref == 1, "only closable app holders: refused after 5 s, detach_ref 1");
  settle();
  pump(4000, 250);
  CHECK(is_nvidia(), "refused: nvidia again after the decision");
  /* a root holder keeps the 30 s wait */
  gfx_tree("tree_gfx_det_root");
  gfx_run(3000);
  put("/sys/module/nouveau/refcnt", "1\n");
  proc_holder(4243, "nvk_probe", 0, 1, 0);
  T += 2000;
  status_tick(T);
  press();
  pump(10000, 500);
  CHECK(dec_running(), "a root holder (nvk_probe): still waiting at 10 s");
  pump(21000, 500);
  CHECK(!dec_running() && g_gfx.detach_ref == 1, "refused after 30 s");
  /* a confirmed detach in auto mode: N17 when the base goes */
  gfx_tree("tree_gfx_det_ok");
  gfx_run(3000);
  clear_notify();
  press();
  settle();
  CHECK(g_dtx.ec == EC_CONFIRMED && g_dtx.latch == SDTX_LATCH_OPENED && !g_gfx.active, "confirmed, latch open, intel");
  ec.base = SDTX_BASE_DETACHED;
  ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_DETACHED);
  CHECK(notified(N17_TEXT) && notified("title=" N17_TITLE), "N17 after the confirmed detach");
  /* Intel mode: no N17 */
  gfx_tree("tree_gfx_det_intel");
  prop_set("persist.vendor.gb.gfx_mode", "intel");
  gfx_run(1000);
  clear_notify();
  press();
  settle();
  ec.base = SDTX_BASE_DETACHED;
  ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_DETACHED);
  CHECK(!notified(N17_TEXT), "Intel mode: no N17");
}

/* ================================================================== GP-1B-13 N08 rule */
static int n08_case(int apk, const char *caps) {  /* 1 = N08 posted for a busy refusal */
  gfx_tree("tree_gfx_n08");
  if (!apk) rm("/odm/app/GbGpu/GbGpu.apk");
  put("/sys/module/nouveau/refcnt", "1\n");
  proc_holder(4243, "nvk_probe", 0, 1, 0);
  status_tick(T);
  if (caps) {
    int c = cl_pair(0, 100);
    char l[32];
    snprintf(l, sizeof l, "ui-caps %s\n", caps);
    req(c, l);
    if (!strcmp(caps, "none")) { req(c, "ui-caps notify\n"); req(c, l); }
    drop(c);
  }
  clear_notify();
  g_bcast_pending = 0;
  press();
  pump(31000, 500);
  return notified("title=Can't detach yet\ntext=The NVIDIA GPU is in use by nvk_probe. Close them, then press detach again.\n");
}
static void test_gfx_n08(void) {
  CHECK(n08_case(0, "notify"), "GP-1B-13 GbGpu absent (even with ui-caps notify): N08 exact text");
  CHECK(n08_case(1, NULL), "GbGpu present without ui-caps notify: N08");
  int posted = n08_case(1, "notify");
  CHECK(!posted && g_gfx.detach_ref == 1 && (g_bcast_pending || notified("kind=broadcast")), "ui-caps notify: no N08, detach_ref 1, broadcast queued");
  CHECK(n08_case(1, "none"), "ui-caps none restores N08");
}

/* ================================================================== GP-1B-14 close-holders-and-detach */
static void chd_tree(const char *name) {
  gfx_tree(name);
  put("/sys/module/nouveau/refcnt", "2\n");
  proc_holder(4242, "game", 10300, 0, 1);
  proc_holder(4245, "chat", 10200, 1, 0);
  status_tick(T);
}
static void test_gfx_close_detach(void) {
  chd_tree("tree_gfx_chd");
  int c = cl_pair(10123, 102);
  ec.log[0] = 0;
  const char *r = req(c, "close-holders-and-detach 4242,4245\n");
  CHECK(strstr(r, "\"closed\":[{\"pid\":4242,\"pkg\":\"com.example.game\",\"user\":0},{\"pid\":4245,\"pkg\":\"com.example.chat\",\"user\":0}],\"refused\":[],\"detach\":\"pending\"}"),
        "GP-1B-14 closed both, detach pending: %s", r);
  CHECK(notified("kind=force-stop\npkg=com.example.game\nuser=0\n") && notified("kind=force-stop\npkg=com.example.chat\nuser=0\n"), "force-stop messages");
  CHECK(g_pend.kind && strstr(req(c, "status\n"), "\"pending\":\"detach\"") && !strcmp(prop("vendor.gb.gfx_reason"), "detaching"), "pending=detach, reason detaching");
  CHECK(!strcmp(req(c, "close-holders-and-detach 4242\n"), "{\"v\":1,\"ok\":false,\"cmd\":\"close-holders-and-detach\",\"err\":\"EBUSY\",\"msg\":\"a detach is pending\"}"),
        "second request while pending: EBUSY");
  put("/sys/module/nouveau/refcnt", "0\n");      /* the apps exited */
  pump(400, 100);
  CHECK(!strstr(ec.log, "REQ"), "one read of 0 is not enough");
  pump(800, 100);
  CHECK(strstr(ec.log, "REQ") && !g_pend.kind, "refcnt 0 on two reads 500 ms apart: SDTX_IOCTL_LATCH_REQUEST '%s'", ec.log);
  settle();
  CHECK(strstr(ec.log, "CONFIRM"), "the EC's request ran the normal decision to the confirm '%s'", ec.log);
  drop(c);
  /* still in use at 15 s */
  chd_tree("tree_gfx_chd_late");
  c = cl_pair(10123, 102);
  ec.log[0] = 0;
  req(c, "close-holders-and-detach 4242\n");
  pump(14000, 500);
  CHECK(g_pend.kind, "pending at 14 s");
  pump(1500, 500);
  CHECK(!g_pend.kind && g_gfx.detach_ref == 1 && !strstr(ec.log, "REQ"), "refcnt > 0 at 15 s: pending cleared, detach_ref 1, no request");
  /* EBUSY during a decision; ENODEV without DTX or with the base detached */
  g_dec.phase = DEC_REFCNT;
  CHECK(strstr(req(c, "close-holders-and-detach 4242\n"), "\"err\":\"EBUSY\""), "decision running: EBUSY");
  g_dec.phase = DEC_NONE;
  g_dtx.base = SDTX_BASE_DETACHED;
  CHECK(strstr(req(c, "close-holders-and-detach 4242\n"), "\"err\":\"ENODEV\""), "base detached: ENODEV");
  g_dtx.base = SDTX_BASE_ATTACHED;
  dtx_close();
  CHECK(strstr(req(c, "close-holders-and-detach 4242\n"), "\"err\":\"ENODEV\""), "no DTX: ENODEV");
  CHECK(strstr(req(c, "close-holders-and-detach 1,abc\n"), "\"err\":\"EINVAL\",\"msg\":\"bad pid list\""), "bad pid list: EINVAL");
  drop(c);
}

/* ================================================================== GP-1B-15 self-test scheduling and the hold */
static void test_gfx_selftest(void) {
  gfx_tree("tree_gfx_st");
  rm(REC_PATH);
  prop_set("vendor.gb.nouveau.result", "healthy");
  g_ctl_log[0] = 0;
  ec.log[0] = 0;
  status_tick(T);
  T += 2000;
  status_tick(T);
  CHECK(!strstr(g_ctl_log, "gb_gfx_selftest") && !strcmp(prop("vendor.gb.gfx_test"), "pending"), "GP-1B-15 not before clean_seen (gfx_test %s)", prop("vendor.gb.gfx_test"));
  prop_set("vendor.gb.nouveau.result", "clean");
  T += 2000;
  status_tick(T);
  settle();
  CHECK(strstr(g_ctl_log, "ctl.start=gb_gfx_selftest") && strstr(ec.log, "WRITE:0000:02:00.0/power/control=on") && g_st.want && g_cmp.applied &&
        !strcmp(prop("vendor.gb.gfx_test"), "running"), "clean: ctl.start gb_gfx_selftest + hold (power/control=on by the worker) '%s'", ec.log);
  prop_set("init.svc.gb_gfx_selftest", "running");
  put(FN0 "/power_state", "D0\n");
  put(FN0 "/power/runtime_status", "active\n");
  pump(90000, 1000);
  CHECK(!g_fault[0] && g_st.running, "90 s idle D0 during the test: no d0-idle fault");
  rec_ok("pass", "", 1, 0);
  put(FN0 "/power_state", "D3cold\n");
  prop_set("init.svc.gb_gfx_selftest", "stopped");
  T += 2000;
  status_tick(T);
  CHECK(!g_st.want && !g_cmp.applied && !strcmp(get(FN0 "/power/control"), "auto") && gfx_ok_now() == 1 &&
        !strcmp(prop("persist.vendor.gb.gfx_ok"), "1") && !strcmp(prop("vendor.gb.gfx_test"), "pass"), "stopped: hold released (auto), record -> gfx_ok 1");
  settle();
  pump(2000, 200);
  CHECK(is_nvidia() && notified(N16_TEXT), "nvidia after the pass, N16");
  /* error: retried once per boot, at most 3 attempts per identity */
  gfx_tree("tree_gfx_st_err");
  rec_ok("error", "icd", 1, 0);
  g_ctl_log[0] = 0;
  status_tick(T);
  CHECK(count_in(g_ctl_log, "ctl.start=gb_gfx_selftest") == 1 && !strcmp(prop("vendor.gb.gfx_reason"), "testerror"), "error, attempts 1: retried (reason testerror)");
  prop_set("init.svc.gb_gfx_selftest", "running");
  T += 2000;
  status_tick(T);
  rec_ok("error", "icd", 2, 0);
  prop_set("init.svc.gb_gfx_selftest", "stopped");
  for (int k = 0; k < 12; k++) { T += 2000; status_tick(T); }
  CHECK(count_in(g_ctl_log, "ctl.start=gb_gfx_selftest") == 1, "only once per boot");
  gfx_reboot(T_BOOT2);
  g_ctl_log[0] = 0;
  status_tick(T);
  CHECK(count_in(g_ctl_log, "ctl.start=gb_gfx_selftest") == 1, "next boot, attempts 2: retried");
  rec_ok("error", "icd", 3, 0);
  gfx_reboot(T_BOOT_OLD);
  g_ctl_log[0] = 0;
  status_tick(T);
  T += 12000;
  status_tick(T);
  CHECK(!strstr(g_ctl_log, "ctl.start=gb_gfx_selftest"), "attempts 3: no automatic run any more");
  /* run-selftest bypasses the cap and a stored fail */
  int c = cl_pair(10123, 102);
  CHECK(!strcmp(req(c, "run-selftest\n"), "{\"v\":1,\"ok\":true,\"cmd\":\"run-selftest\",\"test\":\"running\"}") &&
        strstr(g_ctl_log, "ctl.start=gb_gfx_selftest"), "run-selftest bypasses attempts 3");
  CHECK(!strcmp(req(c, "run-selftest\n"), "{\"v\":1,\"ok\":false,\"cmd\":\"run-selftest\",\"err\":\"EBUSY\",\"msg\":\"self-test running\"}"), "already running: EBUSY");
  drop(c);
  gfx_tree("tree_gfx_st_fail");
  rec_ok("fail", "ahb-i915-readback", 1, 0);
  g_ctl_log[0] = 0;
  status_tick(T);
  T += 12000;
  status_tick(T);
  CHECK(!strstr(g_ctl_log, "gb_gfx_selftest") && !strcmp(prop("vendor.gb.gfx_reason"), "selftest") && !strcmp(prop("persist.vendor.gb.gfx_ok"), "0"),
        "a stored fail is sticky (reason selftest, gfx_ok 0)");
  c = cl_pair(10123, 102);
  CHECK(strstr(req(c, "run-selftest\n"), "\"test\":\"running\""), "run-selftest bypasses a stored fail");
  char n18a[300];
  snprintf(n18a, sizeof n18a, N18_FMT, "ahb-i915-readback");
  for (int run = 0; run < 2; run++) {             /* the re-run fails again, twice: one N18 per identity */
    if (run) { T += 11000; status_tick(T); CHECK(strstr(req(c, "run-selftest\n"), "\"test\":\"running\""), "second re-run"); }
    prop_set("init.svc.gb_gfx_selftest", "running");
    T += 2000;
    status_tick(T);
    write_rec("fail", "ahb-i915-readback", T_BUILD, T_KREL, T_GFX, 1761390500 + run, 2 + run, 0);
    prop_set("init.svc.gb_gfx_selftest", "stopped");
    T += 2000;
    status_tick(T);
  }
  CHECK(count_files_with(n18a) == 1, "a new fail verdict posts N18, the same identity failing again does not (%d)", count_files_with(n18a));
  drop(c);
  /* never in a test boot, during a decision, a pending detach, a rebind watch or in Intel mode */
  static const char *const NO[] = {"test boot", "decision", "pending detach", "rebind watch", "Intel mode", "kill switch", "fault"};
  for (unsigned i = 0; i < sizeof NO / sizeof NO[0]; i++) {
    gfx_tree("tree_gfx_st_no");
    rm(REC_PATH);
    if (i == 0) g_cmd_test = 1;
    if (i == 1) g_dec.phase = DEC_REFCNT;
    if (i == 2) g_pend.kind = 1, g_pend.next_read = T + 1000000, g_pend.deadline = T + 1000000;
    if (i == 3) { g_rb.phase = RB_SEEN; g_rb.seen_at = T + 1000000; put(FN0 "/power_state", "D0\n"); }   /* not healthy yet */
    if (i == 4) prop_set("persist.vendor.gb.gfx_mode", "intel");
    if (i == 5) g_cmd_kill = 1;
    if (i == 6) snprintf(g_fault, sizeof g_fault, "bar");
    g_ctl_log[0] = 0;
    status_tick(T);
    T += 2000;
    status_tick(T);
    int cc = cl_pair(0, 100);
    const char *r = req(cc, "run-selftest\n");
    drop(cc);
    CHECK(!strstr(g_ctl_log, "gb_gfx_selftest") && strstr(r, "\"ok\":false"), "never: %s (run-selftest: %s)", NO[i], r);
    g_dec.phase = DEC_NONE;
    g_rb.phase = RB_NONE;
  }
}

/* ================================================================== GP-1B-16 identity */
static void test_gfx_identity(void) {
  static const char *const WHAT[] = {"ro.build.id", "ro.build.version.incremental", "uname -r", "gfx_build_id"};
  for (int i = 0; i < 4; i++) {
    gfx_tree("tree_gfx_id");
    gfx_run(3000);
    CHECK(is_nvidia() && gfx_ok_now() == 1, "pass of this identity: nvidia");
    g_ctl_log[0] = 0;
    if (i == 0) prop_set("ro.build.id", "CL3B.260701.300.R5");
    if (i == 1) prop_set("ro.build.version.incremental", "16700000");
    if (i == 2) put("/proc/sys/kernel/osrelease", "6.18.30-android17-new\n");
    if (i == 3) put("/odm/etc/gb/gfx_build_id", "apk=5555 icd=2222 nvk=3333 probe=4444 pay=legacy\n");
    T += 2000;
    status_tick(T);
    CHECK(gfx_ok_now() == -1 && !g_gfx.active && !strcmp(prop("vendor.gb.gfx_reason"), "untested") && strstr(g_ctl_log, "ctl.start=gb_gfx_selftest"),
          "GP-1B-16 %s changed: record stale -> untested -> self-test started", WHAT[i]);
  }
}

/* ================================================================== GP-1B-17 interruptions and crashes */
static void test_gfx_interrupt(void) {
  gfx_tree("tree_gfx_int");
  rec_ok("error", "icd", 2, 0);
  status_tick(T);
  CHECK(g_st.running, "error attempts 2: test started");
  prop_set("init.svc.gb_gfx_selftest", "running");
  put(REC_MARKER, "boot=" T_BOOT " t=1761390100\n");
  T += 2000;
  status_tick(T);
  int c = cl_pair(10123, 102);
  g_ctl_log[0] = 0;
  req(c, "set-mode intel\n");
  drop(c);
  CHECK(strstr(g_ctl_log, "ctl.stop=gb_gfx_selftest"), "set-mode intel stops the test '%s'", g_ctl_log);
  prop_set("init.svc.gb_gfx_selftest", "stopped");   /* killed hard: no trap, the marker stays */
  T += 2000;
  status_tick(T);
  CHECK(exists(REC_MARKER) && !strcmp(rec_field("reason"), "icd"), "stopped: the marker is given 10 s");
  T += 10000;
  status_tick(T);
  CHECK(!strcmp(rec_field("verdict"), "error") && !strcmp(rec_field("reason"), "interrupted") && !strcmp(rec_field("attempts"), "2") &&
        !exists(REC_MARKER) && !strcmp(rec_field("report"), "-"), "GP-1B-17 killed hard: error interrupted after 10 s, attempts unchanged, marker removed");
  /* the boot ended inside the test: crash 1 -> error crash, crash 2 -> fail crash + one N18 */
  gfx_tree("tree_gfx_crash");
  rec_ok("pass", "", 1, 0);
  put(REC_MARKER, "boot=" T_BOOT_OLD " t=1761390100\n");
  clear_notify();
  status_tick(T);
  CHECK(!strcmp(rec_field("verdict"), "error") && !strcmp(rec_field("reason"), "crash") && !strcmp(rec_field("crashes"), "1") &&
        !strcmp(rec_field("attempts"), "1") && !exists(REC_MARKER), "a marker of another boot at the first tick: error crash, crashes 1");
  gfx_reboot(T_BOOT2);
  put(REC_MARKER, "boot=" T_BOOT " t=1761390200\n");
  status_tick(T);
  char n18t[300];
  snprintf(n18t, sizeof n18t, N18_FMT, "crash");
  CHECK(!strcmp(rec_field("verdict"), "fail") && !strcmp(rec_field("reason"), "crash") && !strcmp(rec_field("crashes"), "2") &&
        count_files_with(n18t) == 1 && !strcmp(prop("vendor.gb.gfx_reason"), "selftest"), "a second crash: fail crash, one N18 (reason selftest)");
  /* a marker of the current boot (gb_power restarted, the service not running): interrupted */
  gfx_tree("tree_gfx_int_self");
  rec_ok("pass", "", 1, 0);
  put(REC_MARKER, "boot=" T_BOOT " t=1761390100\n");
  status_tick(T);
  CHECK(!strcmp(rec_field("reason"), "interrupted") && !strcmp(rec_field("crashes"), "0") && !exists(REC_MARKER), "a marker of the current boot: interrupted");
  /* the service runs while gb_power restarts: adopted, not interrupted */
  gfx_tree("tree_gfx_int_adopt");
  rec_ok("pass", "", 1, 0);
  put(REC_MARKER, "boot=" T_BOOT " t=1761390100\n");
  prop_set("init.svc.gb_gfx_selftest", "running");
  status_tick(T);
  CHECK(g_st.running && g_st.want && exists(REC_MARKER) && !strcmp(rec_field("verdict"), "pass"), "running test adopted after a gb_power restart");
}

/* ================================================================== GP-1B-18 deadline */
static void test_gfx_deadline(void) {
  gfx_tree("tree_gfx_dl");
  rm(REC_PATH);
  g_ctl_log[0] = 0;
  status_tick(T);
  settle();
  prop_set("init.svc.gb_gfx_selftest", "running");
  clear_notify();
  pump(358000, 2000);
  CHECK(!strstr(g_ctl_log, "ctl.stop=gb_gfx_selftest"), "running at 358 s: no stop yet");
  pump(4000, 1000);
  CHECK(strstr(g_ctl_log, "ctl.stop=gb_gfx_selftest") && !g_fault[0], "GP-1B-18 360 s: ctl.stop");
  pump(11000, 1000);
  char n20[200];
  snprintf(n20, sizeof n20, N20_FMT, "selftest-hang");
  CHECK(!strcmp(g_fault, "selftest-hang") && notified(n20) && !g_st.want && !strcmp(get(FN0 "/power/control"), "auto") &&
        !strcmp(rec_field("verdict"), "fail") && !strcmp(rec_field("reason"), "hang") && !dgpu_fault_blocks() && !notified("Detaching is blocked"),
        "still running 10 s later: fault selftest-hang, N20, hold released, verdict fail hang (fault %s)", g_fault);
}

/* ================================================================== GP-1B-19 session marker */
static void test_gfx_session(void) {
  gfx_tree("tree_gfx_sess");
  put(SESS_PATH, "boot=" T_BOOT_OLD " t=1\n");
  status_tick(T);
  CHECK(unclean_read() == 1 && g_sess.stale, "GP-1B-19 a session of another boot at start: gfx_unclean 1");
  settle();
  pump(3000, 200);
  CHECK(is_nvidia() && unclean_read() == 1 && !strncmp(raw(SESS_PATH), "boot=" T_BOOT, 41), "old session removed, a new one of this boot, counter kept");
  gfx_reboot(T_BOOT2);                            /* the boot ended with apps on NVIDIA again */
  clear_notify();
  status_tick(T);
  CHECK(!strcmp(prop("persist.vendor.gb.gfx_mode"), "intel") && notified(N19_TEXT) && !strcmp(prop("vendor.gb.gfx_reason"), "crashloop") &&
        unclean_read() == 0 && !g_gfx.active, "twice in a row: gfx_mode=intel, N19, reason crashloop, counter 0");
  settle();
  CHECK(!exists(SESS_PATH), "stale session removed");
  /* sys.powerctl: removed at once, counter 0 */
  gfx_tree("tree_gfx_sess_pc");
  put("/data/vendor/gb/gfx_unclean", "1\n");
  gfx_run(3000);
  CHECK(is_nvidia() && exists(SESS_PATH), "nvidia: session on disk");
  prop_set("sys.powerctl", "reboot,userrequested");
  T += 2000;
  status_tick(T);
  CHECK(!exists(SESS_PATH) && unclean_read() == 0, "sys.powerctl: gfx_session removed, gfx_unclean 0");
  settle();
  CHECK(!exists(SESS_PATH), "no new session during the shutdown");
  /* a session of the current boot is adopted */
  gfx_tree("tree_gfx_sess_adopt");
  put(SESS_PATH, "boot=" T_BOOT " t=5\n");
  ec.log[0] = 0;
  gfx_run(2000);
  CHECK(is_nvidia() && unclean_read() == 0 && !strstr(ec.log, "MARK:gfx_session") && !strcmp(raw(SESS_PATH), "boot=" T_BOOT " t=5\n"),
        "a session of this boot: adopted (no new marker, no unclean count)");
}

/* ================================================================== GP-1B-20 socket: goldens, effects, paging */
static void test_gfx_socket(void) {
  gfx_tree("tree_gfx_golden");
  gfx_run(3000);
  int ui = cl_pair(10123, 102), other = cl_pair(10500, 104);
  static const char *const G[][2] = {
    {"get-mode\n", "{\"v\":1,\"ok\":true,\"cmd\":\"get-mode\",\"mode\":\"auto\",\"policy\":\"games\",\"active\":\"nvidia\",\"reason\":\"ok\",\"gfx_ok\":1,\"test\":\"pass\",\"test_t\":1761390000,\"cap\":1,\"base\":\"attached\",\"pending\":\"\",\"apps_sync\":1,\"prefs_n\":0,\"opt_in_n\":0}"},
    {"set-mode turbo\n", "{\"v\":1,\"ok\":false,\"cmd\":\"set-mode\",\"err\":\"EINVAL\",\"msg\":\"bad mode\"}"},
    {"*set-mode intel\n", "{\"v\":1,\"ok\":false,\"cmd\":\"set-mode\",\"err\":\"EPERM\",\"msg\":\"not allowed\"}"},
    {"set-app-intel com.example.game\n", "{\"v\":1,\"ok\":false,\"cmd\":\"set-app-intel\",\"err\":\"EINVAL\",\"msg\":\"bad argument\"}"},
    {"apps-commit 3\n", "{\"v\":1,\"ok\":false,\"cmd\":\"apps-commit\",\"err\":\"EINVAL\",\"msg\":\"no apps-begin\"}"},
    {"ui-caps notify\n", "{\"v\":1,\"ok\":true,\"cmd\":\"ui-caps\",\"notify\":1}"},
    {"#set-mode intel\n", "{\"v\":1,\"ok\":false,\"cmd\":\"set-mode\",\"err\":\"ENODEV\",\"msg\":\"no NVIDIA driver on this image\"}"},
    {"set-mode intel\n", "{\"v\":1,\"ok\":true,\"cmd\":\"set-mode\",\"mode\":\"intel\",\"active\":\"intel\",\"effect\":\"now\",\"n\":0}"}};
  for (unsigned i = 0; i < sizeof G / sizeof G[0]; i++) {
    const char *line = G[i][0];
    int fd = ui;
    if (line[0] == '*') { fd = other; line++; }
    if (line[0] == '#') { put("/odm/etc/gb_hw", "vm\n"); line++; }
    const char *r = req(fd, line);
    CHECK(!strcmp(r, G[i][1]), "GP-1B-20 golden 26.3 %u '%.*s': %s", i, (int)strlen(line) - 1, line, r);
    put("/odm/etc/gb_hw", "native\n");
  }
  CHECK(!strcmp(prop("persist.vendor.gb.gfx_mode"), "intel"), "set-mode intel wrote persist.vendor.gb.gfx_mode");
  drop(ui);
  drop(other);
  /* the 26.2 effect matrix */
  static const struct { const char *what, *cmd, *eff; } E[] = {
    {"booted unset", "set-mode auto", "next-boot"}, {"booted unset", "set-mode intel", "next-boot"},
    {"once", "set-mode auto", "next-boot"}, {"test", "set-mode nvidia", "next-boot"}, {"kill", "set-mode auto", "next-boot"},
    {"wedged", "set-mode intel", "next-boot"}, {"noload", "set-mode auto", "next-boot"}, {"noload", "set-mode intel", "now"},
    {"keep", "set-mode nvidia", "next-boot"}, {"keep", "set-mode intel", "next-boot"}, {"detached", "set-mode auto", "next-attach"},
    {"detached", "set-mode intel", "now"}, {"nodtx", "set-mode nvidia", "next-attach"}, {"busy", "set-mode intel", "pending"},
    {"busy", "set-mode auto", "now"}, {"none", "set-mode auto", "now"}, {"none", "set-mode nvidia", "now"},
    {"detached", "enable", "now"}, {"busy", "disable", "now"}, {"keep", "disable", "next-boot"}};
  for (unsigned i = 0; i < sizeof E / sizeof E[0]; i++) {
    gfx_tree("tree_gfx_eff");
    const char *w = E[i].what;
    if (!strcmp(w, "booted unset")) prop_set("vendor.gb.nouveau.booted", "");
    if (!strcmp(w, "once")) g_cmd_once = 1;
    if (!strcmp(w, "test")) g_cmd_test = 1;
    if (!strcmp(w, "kill")) g_cmd_kill = 1;
    if (!strcmp(w, "wedged")) put("/dev/gb_dgpu/wedged", "1\n");
    if (!strcmp(w, "noload")) put("/dev/gb_dgpu/noload", "1\n");
    if (!strcmp(w, "keep")) put("/dev/gb_dgpu/keep", "1\n");
    if (!strcmp(w, "detached")) { ec.base = SDTX_BASE_DETACHED; ev(SDTX_EVENT_BASE_CONNECTION, SDTX_BASE_DETACHED); }
    if (!strcmp(w, "nodtx")) dtx_close();
    if (!strcmp(w, "busy")) { put("/sys/module/nouveau/refcnt", "1\n"); proc_holder(4242, "game", 10300, 0, 1); }
    status_tick(T);
    int c = cl_pair(0, 100);
    char l[64], want[64];
    snprintf(l, sizeof l, "%s\n", E[i].cmd);
    const char *r = req(c, l);
    snprintf(want, sizeof want, "\"effect\":\"%s\"", E[i].eff);
    int ok = strstr(r, want) != NULL;
    if (!strcmp(E[i].eff, "pending")) ok = ok && strstr(r, "\"n\":1}");
    CHECK(ok, "26.2 %s, %s -> %s: %s", w, E[i].cmd, E[i].eff, r);
    if (!strcmp(E[i].cmd, "enable") || !strcmp(E[i].cmd, "disable"))
      CHECK(!strcmp(prop("persist.vendor.gb.gfx_mode"), !strcmp(E[i].cmd, "enable") ? "auto" : "intel") && !strcmp(prop("persist.vendor.gb.nouveau"), ""),
            "%s writes gfx_mode, never persist.vendor.gb.nouveau", E[i].cmd);
    drop(c);
  }
  /* get-apps paging: 128 choices of 127-character names, every reply <= 4096 (and <= 3500) bytes */
  gfx_tree("tree_gfx_page");
  big_pkgs(128, 1);
  status_tick(T);
  int c = cl_pair(0, 100);
  int ok = 0;
  for (int i = 0; i < 128; i++) {
    char nm[128], l[200];
    int k = snprintf(nm, sizeof nm, "com.p%03d.", i);
    memset(nm + k, 'q', 127 - k);
    nm[127] = 0;
    snprintf(l, sizeof l, "set-app-nvidia %s:1\n", nm);
    ok += strstr(req(c, l), "\"ok\":true") != NULL;
  }
  int start = 0, pages = 0, got = 0, maxlen = 0, sane = 1;
  while (start >= 0 && pages < 20) {
    char l[32];
    snprintf(l, sizeof l, start ? "get-apps %d\n" : "get-apps\n", start);
    const char *r = req(c, l);
    int len = (int)strlen(r) + 1;
    if (len > maxlen) maxlen = len;
    got += count_in(r, "\"choice\":\"nvidia\"");
    const char *nx = strstr(r, "\"next\":");
    int next = nx ? atoi(nx + 7) : -2;
    if (!strstr(r, "\"total\":128") || next == -2) sane = 0;
    start = next;
    pages++;
  }
  CHECK(ok == 128 && got == 128 && sane && maxlen <= 3500 && pages >= 6, "get-apps paging: %d choices in %d pages, longest reply %d bytes", got, pages, maxlen);
  CHECK(strstr(req(c, "get-apps 129\n"), "\"err\":\"EINVAL\"") && strstr(req(c, "get-apps x\n"), "\"err\":\"EINVAL\"") &&
        strstr(req(c, "get-apps 128\n"), "\"next\":-1,\"prefs\":[]"), "get-apps start > 128 / not decimal: EINVAL; 128: empty page");
  drop(c);
  /* list-holders = holders except the cmd */
  gfx_tree("tree_gfx_lh");
  put("/sys/module/nouveau/refcnt", "1\n");
  proc_holder(4242, "game", 10300, 0, 1);
  status_tick(T);
  c = cl_pair(0, 100);
  char h1[4096], h2[4096];
  snprintf(h1, sizeof h1, "%s", req(c, "holders\n"));
  snprintf(h2, sizeof h2, "%s", req(c, "list-holders\n"));
  char *p = strstr(h2, "\"cmd\":\"list-holders\"");
  if (p) { memmove(p + 15, p + 20, strlen(p + 20) + 1); memcpy(p, "\"cmd\":\"holders\"", 15); }
  CHECK(p && !strcmp(h1, h2) && strstr(h1, "\"pid\":4242"), "list-holders = holders except cmd: %s", h2);
  drop(c);
}

/* ================================================================== GP-1B-21 status object */
static void test_gfx_status(void) {
  gfx_tree("tree_gfx_stat");
  gfx_run(3000);
  int c = cl_pair(0, 100);
  const char *r = req(c, "status\n");
  static const char *const K[] = {"\"gfxdriver\":1", "\"gfx_mode\":\"auto\"", "\"gfx_policy\":\"games\"", "\"gfx_active\":\"nvidia\"",
                                  "\"gfx_reason\":\"ok\"", "\"gfx_ok\":1", "\"gfx_test\":\"pass\"", "\"gfx_test_t\":1761390000",
                                  "\"gfx_cap\":1", "\"gfx_seq\":1", "\"pending\":\"\"", "\"detach_ref\":0", "\"apps_sync\":1", "\"t_ms\":"};
  const char *last = r;
  int order = 1;
  for (unsigned i = 0; i < sizeof K / sizeof K[0]; i++) {
    const char *f = strstr(r, K[i]);
    if (!f || f < last) { order = 0; printf("FAIL order at %s in %s\n", K[i], r); break; }
    last = f;
  }
  CHECK(order && strstr(r, "\"persist\":1") && strstr(r, "\"probation\":1"), "GP-1B-21 new fields in the 9.5 order after gfxdriver, before t_ms");
  drop(c);
  /* 48 holders: the reply stays <= 4096 bytes */
  gfx_tree("tree_gfx_stat48");
  put("/sys/module/nouveau/refcnt", "48\n");
  static char pl[16384];
  size_t k = (size_t)snprintf(pl, sizeof pl, "gb.sb3.gpu 10123 0 /d platform none 0 1\n");
  for (int i = 0; i < 48; i++) {
    char comm[32];
    snprintf(comm, sizeof comm, "app%02d.with.long", i);
    k += (size_t)snprintf(pl + k, sizeof pl - k, "com.example.holderapp%02d.with.a.rather.long.package.name.indeed %d 0 /d default none 0 1\n", i, 10600 + i);
    proc_holder(5000 + i, comm, 10600 + i, 1, 0);
  }
  put("/data/system/packages.list", pl);
  T += 6000;
  status_tick(T);
  c = cl_pair(0, 100);
  r = req(c, "status\n");
  CHECK(strlen(r) + 1 <= 4096 && strstr(r, "\"ok\":true") && strstr(r, "\"state\":\"busy:48\"") && strstr(r, "\"t_ms\":"),
        "48 holders: status reply %zu bytes (<= 4096), complete", strlen(r) + 1);
  drop(c);
}

/* ================================================================== GP-1B-22 broadcast */
static int bcast_after(void (*change)(void)) {   /* 1 = a broadcast file appeared for this change */
  clear_notify();
  T += 3000;
  run_due(T);
  clear_notify();
  change();
  T += 2000;
  status_tick(T);
  settle();
  T += 2100;
  run_due(T);
  return notified("kind=broadcast\n");
}
static void ch_mode(void) { prop_set("persist.vendor.gb.gfx_mode", "nvidia"); }
static void ch_policy(void) { prop_set("persist.vendor.gb.gfx_apps", "all"); }
static void ch_active(void) { prop_set("persist.vendor.gb.gfx_mode", "intel"); }
static void ch_reason(void) { g_cmd_kill = 1; }   /* intel/mode -> intel/kill: only the reason changes */
static void ch_test(void) { prop_set("init.svc.gb_gfx_selftest", "running"); }
static void ch_detach_ref(void) { g_gfx.detach_ref++; }
static void ch_pkgs(void) {
  put("/data/system/packages.list", "gb.sb3.gpu 10123 0 /d platform none 0 1\ncom.example.chat 10200 0 /d default none 0 1\n");
  T += 6000;
}
static void ch_nothing(void) {}
static void test_gfx_broadcast(void) {
  gfx_tree("tree_gfx_bc");
  gfx_run(3000);
  CHECK(!bcast_after(ch_nothing), "GP-1B-22 no change: no broadcast");
  CHECK(bcast_after(ch_mode), "gfx_mode change: broadcast");
  CHECK(bcast_after(ch_policy), "gfx_policy change: broadcast");
  CHECK(bcast_after(ch_active), "gfx_active (+ reason, persist) change: broadcast");
  CHECK(bcast_after(ch_reason), "gfx_reason change: broadcast");
  CHECK(bcast_after(ch_test), "gfx_test change: broadcast");
  CHECK(bcast_after(ch_detach_ref), "detach_ref change: broadcast");
  CHECK(bcast_after(ch_pkgs) && !apps_sync(), "packages.list change (apps_sync 0): broadcast");
  /* pending */
  gfx_tree("tree_gfx_bc2");
  put("/sys/module/nouveau/refcnt", "1\n");
  proc_holder(4242, "game", 10300, 0, 1);
  gfx_run(3000);
  CHECK(bcast_after(ch_active) && !strcmp(gfx_pending(), "intel"), "pending (intel while apps hold nouveau): broadcast");
  /* at most one per 2 s */
  clear_notify();
  T += 3000;
  run_due(T);
  clear_notify();
  prop_set("persist.vendor.gb.gfx_mode", "auto");
  status_tick(T);
  prop_set("persist.vendor.gb.gfx_apps", "all");
  T += 500;
  status_tick(T);
  int first = count_files_with("kind=broadcast");
  T += 2000;
  run_due(T);
  CHECK(first == 1 && count_files_with("kind=broadcast") == 2, "two changes 0.5 s apart: one broadcast now, the second after 2 s (%d)", first);
  /* once after boot while apps_sync is 0 */
  gfx_tree("tree_gfx_bc3");
  T += 6000;
  put("/data/system/packages.list", "gb.sb3.gpu 10123 0 /d platform none 0 1\n");
  prop_set("sys.boot_completed", "");
  status_tick(T);
  clear_notify();
  prop_set("sys.boot_completed", "1");
  T += 3000;
  status_tick(T);
  CHECK(notified("kind=broadcast\n") && !apps_sync(), "after sys.boot_completed with apps_sync 0: one broadcast");
}

/* ================================================================== GP-1B-23 VMware model */
static void test_gfx_vmware(void) {
  gfx_tree("tree_gfx_vm");
  dtx_close();
  g_dtx_on = 0;                                   /* gb_power -D: no /dev/surface/dtx */
  status_tick(T);
  int c = cl_pair(10123, 102);
  const char *r = req(c, "get-mode\n");
  CHECK(strstr(r, "\"ok\":true,\"cmd\":\"get-mode\"") && strstr(r, "\"active\":\"intel\",\"reason\":\"nodtx\"") && strstr(r, "\"base\":\"unknown\""),
        "GP-1B-23 VMware model: get-mode works, reason nodtx: %s", r);
  drop(c);
  quiet(1);
  pump(20 * 60 * 1000, 5000);
  quiet(0);
  CHECK(!count_files_with("kind=settings") && !strcmp(prop("vendor.gb.gfx_reason"), "nodtx") && !g_gfx.active,
        "no settings message in 20 fake minutes, gfx_reason nodtx");
  g_dtx_on = 1;
  /* the VM image itself (gb_hw vm, no package): cap 0, nopkg, set-mode ENODEV */
  gfx_tree("tree_gfx_vmimg");
  put("/odm/etc/gb_hw", "vm\n");
  rm("/odm/etc/gb/gfx_build_id");
  rm("/odm/app/GbGfxDriver/GbGfxDriver.apk");
  prop_set("ro.gfx.driver.0", "");
  dtx_close();
  g_dtx_on = 0;
  status_tick(T);
  c = cl_pair(10123, 102);
  CHECK(strstr(req(c, "get-mode\n"), "\"reason\":\"nopkg\"") && strstr(req(c, "set-mode auto\n"), "\"err\":\"ENODEV\"") &&
        !count_files_with("kind=settings") && !exists(OPTIN), "VM image: nopkg, set-mode ENODEV, nothing written");
  drop(c);
  g_dtx_on = 1;
}

/* ================================================================== review fix pass (INTERFACES 29, TASKS_1B 12) */
#define DSESS "/data/vendor/gb/dgpu_session"
/* GP-R1 (R-1): a clean shutdown ends gfx_session, dgpu_session and the rebind guard's own crash marker from the
 * SIGTERM that init sends right after sys.powerctl (no tick in between) */
static void test_r1_shutdown(void) {
  gfx_tree("tree_r1");
  put("/data/vendor/gb/gfx_unclean", "1\n");
  gfx_run(3000);
  settle();
  CHECK(is_nvidia() && exists(SESS_PATH) && exists(DSESS), "GP-R1 nvidia: gfx_session and dgpu_session on disk");
  prop_set("sys.powerctl", "reboot,userrequested");
  term_handle();                                  /* the SIGTERM, before any tick saw sys.powerctl */
  CHECK(!exists(SESS_PATH) && !exists(DSESS) && unclean_read() == 0 && dunclean_read() == 0,
        "SIGTERM with sys.powerctl: both sessions removed at once, counters 0");
  gfx_reboot(T_BOOT2);
  prop_set("sys.powerctl", "");
  clear_notify();
  status_tick(T);
  CHECK(unclean_read() == 0 && dunclean_read() == 0 && !notified(N19_TEXT) && !notified(N21_TEXT) &&
        strcmp(prop("persist.vendor.gb.gfx_mode"), "intel"), "next boot: no unclean count, no N19/N21");
  /* a plain stop (no sys.powerctl): kept, adopted by the restarted gb_power in the same boot */
  gfx_tree("tree_r1_stop");
  gfx_run(3000);
  settle();
  term_handle();
  CHECK(exists(SESS_PATH) && exists(DSESS), "SIGTERM without sys.powerctl: sessions kept");
  gfx_reboot(NULL);
  ec.log[0] = 0;
  gfx_run(2000);
  CHECK(is_nvidia() && unclean_read() == 0 && dunclean_read() == 0 && !strstr(ec.log, "MARK:gfx_session") &&
        !strstr(ec.log, "MARK:dgpu_session"), "restart in the same boot: both adopted, nothing counted");
  /* the rebind guard's marker: removed once the probe returned (bound), kept while it may still run */
  static const char *const PH[] = {"RB_CLEAN", "RB_SEEN bound", "RB_SEEN not bound", "RB_CLEAN, not our line"};
  for (int i = 0; i < 4; i++) {
    rb_tree("tree_r1_rb");
    reattach();
    settle();
    fns_back();
    if (i != 2) bind_nouveau();
    if (i == 1) { put(FN0 "/power_state", "D0\n"); put(FN0 "/power/runtime_status", "active\n"); }
    T += 2000;
    status_tick(T);
    if (i == 3) put(LOADING_MARKER, "mode=bind t=99 boot=" T_BOOT " argv=-\n");
    int want_phase = i == 0 || i == 3 ? RB_CLEAN : RB_SEEN;
    CHECK(g_rb.phase == want_phase && g_rb.own_marker, "%s: guard phase %d, own marker", PH[i], g_rb.phase);
    prop_set("sys.powerctl", "shutdown,");
    term_handle();
    int gone = !exists(LOADING_MARKER);
    CHECK(i < 2 ? gone : !gone, "%s at the shutdown SIGTERM: crash marker %s", PH[i], gone ? "removed" : "kept");
    prop_set("sys.powerctl", "");
  }
  /* the tick path (sys.powerctl seen first) does the same */
  rb_tree("tree_r1_tick");
  reattach();
  settle();
  fns_back();
  bind_nouveau();
  T += 2000;
  status_tick(T);
  prop_set("sys.powerctl", "reboot,");
  T += 2000;
  status_tick(T);
  CHECK(!exists(LOADING_MARKER) && g_shut_marks, "sys.powerctl seen by the tick: the healthy guard's marker removed");
  term_handle();
  CHECK(g_shut_marks == 1, "handled once");
}

/* GP-R2 (R-2): an Intel switch during the rebind guard's watch is not a failed probe */
static void test_r2_rebind_intel(void) {
  for (int i = 0; i < 2; i++) {
    rb_tree("tree_r2");
    clear_notify();
    reattach();
    settle();
    fns_back();                                   /* present, the kernel's probe has not bound (yet) */
    T += 2000;
    status_tick(T);
    CHECK(g_rb.phase == RB_SEEN, "GP-R2 watching");
    if (i == 0) {
      int c = cl_pair(0, 100);
      CHECK(strstr(req(c, "set-mode intel\n"), "\"ok\":true"), "set-mode intel during the watch");
      drop(c);
    } else {
      prop_set("persist.vendor.gb.gfx_mode", "auto");   /* Intel then Auto again: off ran in between */
    }
    prop_set("init.svc.gb_nouveau_off", "running");
    T += 2000;
    status_tick(T);
    prop_set("init.svc.gb_nouveau_off", "stopped");
    pump(34000, 1000);
    settle();
    CHECK(g_rb.phase == RB_NONE && !g_fault[0] && !exists("/dev/gb_dgpu/keep") && !exists("/dev/gb_dgpu/noload") &&
          !exists(LOADING_MARKER) && !notified("(rebind)"),
          "%s: guard ended without a fault (no keep/noload, no N02), marker removed (fault '%s')",
          i == 0 ? "intel" : "off ran, then auto", g_fault);
  }
  /* without an off the not-bound probe is still a failure (GP-1B-11) */
  rb_tree("tree_r2_fail");
  reattach();
  settle();
  fns_back();
  pump(34000, 1000);
  CHECK(!strcmp(g_fault, "rebind"), "no Intel switch, no off: fault rebind as before");
}

/* GP-R3 (R-3): reconcile_on */
static void ron_tree(const char *name) {         /* nvidia mode, nouveau loaded, the dGPU driverless (an off won) */
  gfx_tree(name);
  prop_set("persist.vendor.gb.gfx_mode", "nvidia");
  prop_set("vendor.gb.nouveau.result", "off-unbound");
  rm(FN0 "/driver");
  drm_gone();
  g_ctl_log[0] = 0;
  status_tick(T);
}
static void ron_ticks(int n) { for (int k = 0; k < n; k++) { T += 2000; status_tick(T); } }
static void test_r3_reconcile_on(void) {
  ron_tree("tree_r3");
  CHECK(!strstr(g_ctl_log, "ctl.start=gb_nouveau "), "GP-R3 first tick: nothing yet (10 s from gb_power's first tick)");
  ron_ticks(4);                                   /* T0 + 8 s */
  CHECK(!strstr(g_ctl_log, "ctl.start=gb_nouveau "), "nothing within those 10 s");
  prop_set("init.svc.gb_nouveau_off", "running");
  ron_ticks(1);                                   /* T0 + 10 s: off seen running */
  prop_set("init.svc.gb_nouveau_off", "stopped");
  ron_ticks(4);                                   /* T0 + 18 s */
  CHECK(!strstr(g_ctl_log, "ctl.start=gb_nouveau "), "not within 10 s of the last tick that saw gb_nouveau_off running");
  ron_ticks(1);                                   /* T0 + 20 s */
  CHECK(count_in(g_ctl_log, "ctl.start=gb_nouveau ") == 1 && g_nv.starts == 1, "then exactly one ctl.start gb_nouveau '%s'", g_ctl_log);
  ron_ticks(4);
  CHECK(count_in(g_ctl_log, "ctl.start=gb_nouveau ") == 1, "not again within 10 s of its own start");
  ron_ticks(15);
  CHECK(count_in(g_ctl_log, "ctl.start=gb_nouveau ") == 3 && g_nv.starts == 3, "at most 3 per boot (shared with the hot attach): %d", g_nv.starts);
  static const char *const NO[] = {"intel mode", "kill", "keep", "noload", "wedged", "fault", "rebind watch", "booted unset",
                                   "detached", "decision", "pending detach", "insmod-failed", "refused:i915", "gb_nouveau running"};
  for (unsigned i = 0; i < sizeof NO / sizeof NO[0]; i++) {
    ron_tree("tree_r3_no");
    if (i == 0) prop_set("persist.vendor.gb.gfx_mode", "intel");
    if (i == 1) g_cmd_kill = 1;
    if (i == 2) put("/dev/gb_dgpu/keep", "1\n");
    if (i == 3) put("/dev/gb_dgpu/noload", "1\n");
    if (i == 4) put("/dev/gb_dgpu/wedged", "1\n");
    if (i == 5) snprintf(g_fault, sizeof g_fault, "rescan");
    if (i == 6) { g_rb.phase = RB_SEEN; g_rb.seen_at = T + 100000; }
    if (i == 7) prop_set("vendor.gb.nouveau.booted", "");
    if (i == 8) g_dtx.base = SDTX_BASE_DETACHED;
    if (i == 9) g_dec.phase = DEC_REFCNT;
    if (i == 10) { g_pend.kind = 1; g_pend.deadline = T + 100000; g_pend.next_read = T + 100000; }
    if (i == 11) prop_set("vendor.gb.nouveau.result", "insmod-failed:nouveau");
    if (i == 12) prop_set("vendor.gb.nouveau.result", "refused:i915");
    if (i == 13) prop_set("init.svc.gb_nouveau", "running");
    for (int k = 0; k < 12; k++) {
      T += 2000;
      if (i == 6) g_rb.seen_at = T;
      if (i == 9) { g_dec.phase = DEC_REFCNT; g_dec.step_at = T + 100000; }
      status_tick(T);
    }
    CHECK(!strstr(g_ctl_log, "ctl.start=gb_nouveau "), "no start: %s '%s'", NO[i], g_ctl_log);
    g_rb.phase = RB_NONE;
    g_dec.phase = DEC_NONE;
    memset(&g_pend, 0, sizeof g_pend);
  }
  /* the review's race: Auto -> Intel -> NVIDIA on the tile while nouveau is bound; off unbinds after the rc's auto
   * run exited on "already bound": the deferred start survives the off and is issued when it stops */
  gfx_tree("tree_r3_race");
  gfx_run(3000);
  int c = cl_pair(0, 100);
  req(c, "set-mode intel\n");
  prop_set("init.svc.gb_nouveau_off", "running");  /* the rc's off: still before its unbind */
  CHECK(strstr(req(c, "set-mode nvidia\n"), "\"ok\":true") && g_nv.deferred, "set-mode nvidia while gb_nouveau_off runs: deferred start");
  drop(c);
  g_ctl_log[0] = 0;
  T += 2000;
  status_tick(T);
  CHECK(g_nv.deferred && !strstr(g_ctl_log, "ctl.start=gb_nouveau "), "still bound, off running: deferred kept");
  rm(FN0 "/driver");                              /* off unbinds */
  drm_gone();
  prop_set("vendor.gb.nouveau.result", "off-unbound");
  T += 2000;
  status_tick(T);
  CHECK(!strstr(g_ctl_log, "ctl.start=gb_nouveau "), "driverless, off still running: not yet");
  prop_set("init.svc.gb_nouveau_off", "stopped");
  T += 2000;
  status_tick(T);
  CHECK(count_in(g_ctl_log, "ctl.start=gb_nouveau ") == 1, "off stopped: the deferred gb_nouveau start (bind path) '%s'", g_ctl_log);
}

/* GP-R4 (R-4) dgpu_session; (R-15) the persistent-properties gate */
static void test_r4_dgpu_session(void) {
  gfx_tree("tree_r4");
  put(LOADING_MARKER, "mode=auto t=31 boot=" T_BOOT " argv=runpm=1 modeset=2\n");   /* gb_nouveau's clean-mark wait */
  ec.log[0] = 0;
  gfx_run(3000);
  settle();
  CHECK(!exists(DSESS) && !strstr(ec.log, "MARK:dgpu_session"), "GP-R4 not while the crash-loop marker exists");
  rm(LOADING_MARKER);
  T += 2000;
  status_tick(T);
  settle();
  CHECK(exists(DSESS) && !strncmp(raw(DSESS), "boot=" T_BOOT " t=", 41) && mode_of(DSESS) == 0600 && strstr(ec.log, "MARK:dgpu_session"),
        "bound, no load window, no marker: dgpu_session written by the worker (0600) '%s'", raw(DSESS));
  prop_set("persist.vendor.gb.gfx_mode", "intel");   /* an Intel switch: the session follows the binding, not gfx_active */
  T += 2000;
  status_tick(T);
  settle();
  CHECK(!g_gfx.active && exists(DSESS) && !exists(SESS_PATH), "Intel while still bound: gfx_session gone, dgpu_session kept");
  put("/data/vendor/gb/dgpu_unclean", "1\n");
  rm(FN0 "/driver");
  drm_gone();
  T += 2000;
  status_tick(T);
  settle();
  CHECK(!exists(DSESS) && dunclean_read() == 0, "unbound: removed, dgpu_unclean 0");
  /* compute-only use (no app graphics): an earlier boot's session counts, twice in a row = Intel + N21 */
  gfx_tree("tree_r4_cnt");
  rec_ok("fail", "a17-swapchain-model", 1, 0);
  put(DSESS, "boot=" T_BOOT_OLD " t=1\n");
  clear_notify();
  status_tick(T);
  settle();
  CHECK(dunclean_read() == 1 && !notified(N21_TEXT) && strcmp(prop("persist.vendor.gb.gfx_mode"), "intel"), "one unclean ending: dgpu_unclean 1");
  pump(3000, 500);
  settle();
  CHECK(!g_gfx.active && exists(DSESS) && !strncmp(raw(DSESS), "boot=" T_BOOT, 41), "self-test fail (Intel apps), nouveau bound: a new session of this boot");
  gfx_reboot(T_BOOT2);
  clear_notify();
  status_tick(T);
  CHECK(!strcmp(prop("persist.vendor.gb.gfx_mode"), "intel") && notified(N21_TEXT) && dunclean_read() == 0 &&
        !strcmp(prop("vendor.gb.gfx_reason"), "crashloop"), "twice in a row: gfx_mode=intel, N21, counter 0, reason crashloop");
  settle();
  CHECK(!exists(DSESS), "the stale session removed");
  /* both sessions stale at the second ending: N19 only */
  gfx_tree("tree_r4_both");
  put(SESS_PATH, "boot=" T_BOOT_OLD " t=1\n");
  put(DSESS, "boot=" T_BOOT_OLD " t=1\n");
  put("/data/vendor/gb/gfx_unclean", "1\n");
  put("/data/vendor/gb/dgpu_unclean", "1\n");
  clear_notify();
  status_tick(T);
  CHECK(notified(N19_TEXT) && !notified(N21_TEXT) && unclean_read() == 0 && dunclean_read() == 0, "both stale: N19 only, both counters 0");
  /* R-15: no start check (persist.* writes) before ro.persistent_properties.ready or boot_completed */
  gfx_tree("tree_r15");
  put(DSESS, "boot=" T_BOOT_OLD " t=1\n");
  put("/data/vendor/gb/dgpu_unclean", "1\n");
  prop_set("sys.boot_completed", "");
  prop_set("ro.persistent_properties.ready", "");
  clear_notify();
  status_tick(T);
  CHECK(g_gfx.data_seen && !g_gfx.sess_checked && dunclean_read() == 1 && !notified(N21_TEXT),
        "GP-R15 persistent properties not loaded: prefs/catalog read, no session start check yet");
  prop_set("ro.persistent_properties.ready", "true");
  T += 2000;
  status_tick(T);
  CHECK(g_gfx.sess_checked && !strcmp(prop("persist.vendor.gb.gfx_mode"), "intel"), "ready=true (boot not completed yet): the check runs");
  prop_set("sys.boot_completed", "1");
  T += 2000;
  status_tick(T);
  CHECK(notified(N21_TEXT), "N21 queued, posted after boot_completed");
}

/* GP-R6 (R-6): no compute start or power hold inside a load window */
static void test_r6_compute_window(void) {
  rb_tree("tree_r6");
  reattach();
  settle();
  fns_back();
  bind_nouveau();
  put(FN0 "/power_state", "D0\n");
  put(FN0 "/power/runtime_status", "active\n");
  T += 2000;
  status_tick(T);
  CHECK(g_rb.phase == RB_SEEN, "GP-R6 watching (bound, D0)");
  int c = cl_pair(0, 100);
  CHECK(strstr(req(c, "compute-start\n"), "\"err\":\"EAGAIN\",\"msg\":\"NVIDIA driver starting\""), "compute-start during the rebind watch: EAGAIN");
  lnk("/vendor/bin/gb_gpu_compute", "/proc/777/exe");
  int s = cl_pair(9999, 777);
  ec.log[0] = 0;
  req(s, "compute-state loaded\n");
  settle();
  CHECK(!strstr(ec.log, "power/control=on") && !g_cmp.applied, "compute-state loaded during the watch: no power hold '%s'", ec.log);
  put(FN0 "/power_state", "D3cold\n");
  put(FN0 "/power/runtime_status", "suspended\n");
  T += 2000;
  status_tick(T);
  settle();
  CHECK(g_rb.phase == RB_CLEAN && !g_fault[0] && g_cmp.applied, "D3cold reached: healthy, then the hold is applied");
  drop(s);
  drop(c);
  /* gb_nouveau.sh's window (live owner gb_nouveau) and the loading state */
  gfx_tree("tree_r6_w");
  gfx_run(2000);
  c = cl_pair(0, 100);
  put("/proc/4300/comm", "sh\n");
  put("/dev/gb_esp.lock/owner", "4300 gb_nouveau 95\n");
  CHECK(strstr(req(c, "compute-start\n"), "\"err\":\"EAGAIN\""), "gb_nouveau's load window: EAGAIN");
  rm("/dev/gb_esp.lock/owner");
  rm(FN0 "/driver");
  prop_set("vendor.gb.nouveau.result", "loading");
  T += 2000;
  CHECK(strstr(req(c, "compute-start\n"), "\"err\":\"EAGAIN\""), "state loading: EAGAIN");
  bind_nouveau();
  prop_set("vendor.gb.nouveau.result", "clean");
  T += 2000;
  CHECK(strstr(req(c, "compute-start\n"), "\"state\":\"starting\""), "standby again: started");
  drop(c);
}

/* GP-R8 (R-8): a fault that blocks the detach also sets Intel mode; (R-9) N08 for service holders */
static void test_r8_faults(void) {
  static const char *const F[] = {"rpm-error", "d0-idle"};
  for (int i = 0; i < 2; i++) {
    gfx_tree("tree_r8");
    clear_notify();
    if (i == 0) put(FN0 "/power/runtime_status", "error\n");
    else { put(FN0 "/power_state", "D0\n"); put(FN0 "/power/runtime_status", "active\n"); }
    gfx_run(2000);
    if (i == 1) pump(65000, 1000);
    char t[220];
    snprintf(t, sizeof t, N06_FMT, F[i]);
    CHECK(!strcmp(g_fault, F[i]) && exists("/dev/gb_dgpu/keep") && exists("/dev/gb_dgpu/noload") && g_gfx.forced &&
          !strcmp(prop("persist.vendor.gb.gfx_mode"), "intel") && notified(t) && !g_gfx.active,
          "GP-R8 %s: keep + noload, gfx_mode=intel, N06 '%s'", F[i], t);
    clear_notify();
    press();
    CHECK(notified(t), "%s: a detach press is refused with the same N06", F[i]);
  }
  gfx_tree("tree_r8_rescan");
  set_fault("rescan");
  CHECK(!exists("/dev/gb_dgpu/keep") && strcmp(prop("persist.vendor.gb.gfx_mode"), "intel"), "a non-blocking fault (rescan): no Intel mode");
  /* R-9: a refusal with only a GPU-user service (refcnt 0, still running): N08 from gb_power even with ui-caps notify */
  gfx_tree("tree_r9");
  status_tick(T);
  int c = cl_pair(0, 100);
  req(c, "ui-caps notify\n");
  drop(c);
  prop_set("init.svc.gb_gpu_compute", "running");
  clear_notify();
  press();
  pump(31000, 500);
  CHECK(notified("text=The NVIDIA GPU is in use by gb_gpu_compute. Close them, then press detach again.\n") && g_gfx.detach_ref == 1,
        "GP-R9 service holder with ui-caps notify: N08 names the service, detach_ref 1");
}

/* GP-R12 (R-12): the rebind marker is unlinked only with gb_power's own line; a resume clears a pending detach */
static void test_r12_markers(void) {
  rb_tree("tree_r12");
  reattach();
  settle();
  CHECK(exists(LOADING_MARKER) && g_rb.own_marker, "GP-R12 guard marker written");
  put(LOADING_MARKER, "mode=bind t=99 boot=" T_BOOT " argv=-\n");   /* gb_nouveau.sh's window took over */
  pump(61000, 1000);                              /* no dGPU within 60 s: rb_end */
  settle();
  CHECK(g_rb.phase == RB_NONE && !strcmp(raw(LOADING_MARKER), "mode=bind t=99 boot=" T_BOOT " argv=-\n"), "another window's marker: left alone");
  rb_tree("tree_r12_own");
  reattach();
  settle();
  pump(61000, 1000);
  settle();
  CHECK(!exists(LOADING_MARKER), "its own line: removed");
  chd_tree("tree_r12_pend");
  put("/sys/power/suspend_stats/success", "1\n");
  status_tick(T);
  int c = cl_pair(10123, 102);
  ec.log[0] = 0;
  req(c, "close-holders-and-detach 4242\n");
  CHECK(g_pend.kind, "close-holders-and-detach: pending");
  put("/sys/power/suspend_stats/success", "2\n");
  T += 2000;
  status_tick(T);
  CHECK(!g_pend.kind, "a system resume clears the pending detach");
  put("/sys/module/nouveau/refcnt", "0\n");
  pump(2000, 100);
  CHECK(!strstr(ec.log, "REQ"), "no LATCH_REQUEST after the resume '%s'", ec.log);
  drop(c);
}
/* ---- wave 2b: the ESP lock r3 (INTERFACES 5.4, section 30 E-1) ---- */
static int g_hk_stage[4];
static int g_hk_mode;   /* 1: a live holder re-creates the lock right before our rename; 2: also a newcomer's empty
                           directory right after it, gone after 3 put-back retries; 3: an ownerless re-creation + newcomer */
static void esp_hook(int stage) {
  char p[PATH_MAX];
  g_hk_stage[stage]++;
  if (stage == 0 && g_hk_mode) {
    rm("/dev/gb_esp.lock/owner");
    rmdir(rpath(p, sizeof p, "/dev/gb_esp.lock"));
    mkdirs(rpath(p, sizeof p, "/dev/gb_esp.lock"));
    if (g_hk_mode != 3) put("/dev/gb_esp.lock/owner", "4300 gb_bootlog 30\n");
  }
  if (stage == 1 && g_hk_mode >= 2) mkdirs(rpath(p, sizeof p, "/dev/gb_esp.lock"));
  if (stage == 2 && g_hk_mode == 2 && g_hk_stage[2] == 3) rmdir(rpath(p, sizeof p, "/dev/gb_esp.lock"));
}
static void test_w2b_esp_lock(void) {
  char line[96], own[64], p[PATH_MAX], q[PATH_MAX], d[PATH_MAX], o[PATH_MAX + 8];
  snprintf(own, sizeof own, "%d gb_power ", (int)getpid());
  /* a steal in progress (a live .stale.<pid> directory): back off; our directory goes, the stealer's stays */
  set_root("tree_w2b_busy");
  put("/proc/4301/comm", "sh\n");
  mkdirs(rpath(p, sizeof p, "/dev/gb_esp.lock.stale.4301"));
  CHECK(esp_lock_try(line, sizeof line) == 0 && !line[0] && !exists("/dev/gb_esp.lock") && exists("/dev/gb_esp.lock.stale.4301"),
        "W2B-1 steal in progress: going on without the lock, our new directory removed, the stealer's kept");
  /* a leftover of a dead stealer, and one with our own pid: not busy */
  set_root("tree_w2b_dead");
  mkdirs(rpath(p, sizeof p, "/dev/gb_esp.lock.stale.4302"));
  snprintf(q, sizeof q, "/dev/gb_esp.lock.stale.%d", (int)getpid());
  mkdirs(rpath(p, sizeof p, "%s", q));
  CHECK(esp_lock_try(line, sizeof line) == 1 && !strncmp(line, own, strlen(own)) && !strcmp(get("/dev/gb_esp.lock/owner"), line),
        "W2B-2 dead stealer / own pid: not busy, lock taken '%s'", line);
  esp_unlock(line);
  CHECK(!exists("/dev/gb_esp.lock") && !g_esp_rel.line[0], "W2B-2 released at once");
  /* the owner file is created exclusively: one already in our new directory is neither removed nor overwritten */
  set_root("tree_w2b_excl");
  rpath(d, sizeof d, ESP_LOCK);
  snprintf(o, sizeof o, "%s/owner", d);
  put("/dev/gb_esp.lock/owner", "4300 gb_bootlog 30\n");
  CHECK(esp_claim(d, o, line, sizeof line) == 0 && !line[0] && !strcmp(get("/dev/gb_esp.lock/owner"), "4300 gb_bootlog 30"),
        "W2B-3 an owner file already there: not ours, kept '%s'", get("/dev/gb_esp.lock/owner"));
  rm("/dev/gb_esp.lock/owner");
  rmdir(d);
  CHECK(esp_claim(d, o, line, sizeof line) == 0 && !line[0] && !exists("/dev/gb_esp.lock"),
        "W2B-3 our new directory moved away (ENOENT): not ours, nothing created");
  /* release: our lock moved away by a steal in progress -> retried from the status tick until it is back */
  set_root("tree_w2b_rel");
  put("/proc/4303/comm", "sh\n");
  mkdirs(rpath(p, sizeof p, "/dev"));
  rpath(p, sizeof p, ESP_LOCK);
  rpath(q, sizeof q, ESP_LOCK ".stale.4303");
  CHECK(esp_lock_try(line, sizeof line) == 1, "W2B-4 taken");
  rename(p, q);
  esp_unlock(line);
  CHECK(!strcmp(g_esp_rel.line, line) && exists("/dev/gb_esp.lock.stale.4303"), "W2B-4 moved away at release: release pending");
  esp_release_retry(T + 1000);
  CHECK(g_esp_rel.line[0] != 0, "W2B-4 still away after 1 s: still pending");
  rename(q, p);                                   /* the stealer puts it back */
  esp_release_retry(T + 2000);
  CHECK(!g_esp_rel.line[0] && !exists("/dev/gb_esp.lock") && !exists("/dev/gb_esp.lock.stale.4303"), "W2B-4 put back: released");
  CHECK(esp_lock_try(line, sizeof line) == 1, "W2B-5 taken");
  rename(p, q);
  esp_unlock(line);
  esp_release_retry(T + ESP_REL_MS);
  CHECK(!g_esp_rel.line[0] && exists("/dev/gb_esp.lock.stale.4303") && !exists("/dev/gb_esp.lock"),
        "W2B-5 not back within %d s: given up, left alone", ESP_REL_MS / 1000);
  /* the steal: the lock changed meanwhile -> put back without replacing; a newcomer in the way -> retried until it
   * backs off; an ownerless moved directory with a newcomer in the way -> removed */
  for (int mode = 1; mode <= 3; mode++) {
    char tn[32];
    snprintf(tn, sizeof tn, "tree_w2b_steal%d", mode);
    set_root(tn);
    g_esp_hook = esp_hook;                        /* (set_root clears it) */
    memset(g_hk_stage, 0, sizeof g_hk_stage);
    g_hk_mode = mode;
    put("/proc/4300/comm", "sh\n");
    put("/dev/gb_esp.lock/owner", "4399 gb_nouveau 50\n");   /* dead pid: stale */
    int r = esp_lock_try(line, sizeof line);
    snprintf(q, sizeof q, "/dev/gb_esp.lock.stale.%d", (int)getpid());
    if (mode == 1)
      CHECK(r == 0 && !strcmp(get("/dev/gb_esp.lock/owner"), "4300 gb_bootlog 30") && !exists(q) && !g_hk_stage[2],
            "W2B-6 re-created meanwhile: put back at once (r %d, retries %d)", r, g_hk_stage[2]);
    if (mode == 2)
      CHECK(r == 0 && !strcmp(get("/dev/gb_esp.lock/owner"), "4300 gb_bootlog 30") && !exists(q) && g_hk_stage[2] == 3,
            "W2B-7 a newcomer's directory in the way: put back after %d retries (r %d)", g_hk_stage[2], r);
    if (mode == 3)
      CHECK(r == 0 && !exists(q) && exists("/dev/gb_esp.lock") && !exists("/dev/gb_esp.lock/owner"),
            "W2B-8 an ownerless moved directory, a newcomer in the way: removed, the newcomer's kept (r %d)", r);
  }
  g_esp_hook = NULL;
  g_hk_mode = 0;
}
/* ---- wave 3: the ESP lock r4 (INTERFACES 5.4 r4; wave 2b review L1, L2, L4) ---- */
static void test_w3_esp_lock(void) {
  char line[96], p[PATH_MAX], q[PATH_MAX], ol[64];
  /* L1: while a steal is in progress there is no mkdir at all */
  set_root("tree_w3_busy");
  g_esp_hook = esp_hook;
  memset(g_hk_stage, 0, sizeof g_hk_stage);
  g_hk_mode = 0;
  put("/proc/4311/comm", "sh\n");
  mkdirs(rpath(p, sizeof p, "/dev/gb_esp.lock.stale.4311"));
  CHECK(esp_lock_try(line, sizeof line) == 0 && !line[0] && g_hk_stage[3] == 0 && !exists("/dev/gb_esp.lock"),
        "W3-1 steal in progress: no mkdir (%d), going on without the lock", g_hk_stage[3]);
  rmdir(rpath(p, sizeof p, "/dev/gb_esp.lock.stale.4311"));
  CHECK(esp_lock_try(line, sizeof line) == 1 && g_hk_stage[3] == 1, "W3-1 no steal any more: one mkdir, taken");
  esp_unlock(line);
  g_esp_hook = NULL;
  /* L2: a lock naming our own pid while we hold none is stale (a put-back that came after our release gave up) */
  set_root("tree_w3_self");
  snprintf(ol, sizeof ol, "%d gb_power 7\n", (int)getpid());
  put("/dev/gb_esp.lock/owner", ol);
  CHECK(esp_lock_try(line, sizeof line) == 1 && line[0] && !strcmp(get("/dev/gb_esp.lock/owner"), line),
        "W3-2 own pid in a lock we do not hold: stolen as stale, taken '%s'", line);
  esp_unlock(line);
  CHECK(!exists("/dev/gb_esp.lock"), "W3-2 released");
  CHECK(ESP_REL_MS >= 7000, "W3-3 the release retry (%d ms) outlasts a stealer's 5 s put-back budget", ESP_REL_MS);
  /* L4: the load window is open while a live stealer has gb_nouveau's lock moved into its .stale.<pid> directory */
  set_root("tree_w3_window");
  put("/proc/4300/comm", "sh\n");
  put("/proc/4312/comm", "sh\n");
  put("/dev/gb_esp.lock.stale.4312/owner", "4300 gb_nouveau 95\n");
  CHECK(nouveau_window_open() && load_window_open(), "W3-4 gb_nouveau's lock moved away by a live stealer: window open");
  rename(rpath(p, sizeof p, "/dev/gb_esp.lock.stale.4312"), rpath(q, sizeof q, "/dev/gb_esp.lock.stale.4313"));
  CHECK(!nouveau_window_open(), "W3-4 the same in the directory of a dead stealer (4313): not a window");
  rename(rpath(p, sizeof p, "/dev/gb_esp.lock.stale.4313"), rpath(q, sizeof q, "/dev/gb_esp.lock.stale.4312"));
  put("/dev/gb_esp.lock.stale.4312/owner", "4300 gb_bootlog 95\n");
  CHECK(!nouveau_window_open(), "W3-4 a moved gb_bootlog lock: not a window");
  put("/dev/gb_esp.lock.stale.4312/owner", "4399 gb_nouveau 95\n");
  CHECK(!nouveau_window_open(), "W3-4 a moved lock of a dead gb_nouveau: not a window");
  rm("/dev/gb_esp.lock.stale.4312/owner");
  rmdir(rpath(p, sizeof p, "/dev/gb_esp.lock.stale.4312"));
  snprintf(q, sizeof q, "/dev/gb_esp.lock.stale.%d/owner", (int)getpid());
  put(q, "4300 gb_nouveau 95\n");
  CHECK(!nouveau_window_open(), "W3-4 our own .stale directory is not a steal in progress");
}
#define GROUP(id, fn) do { int c0_ = checks, f0_ = fails; fn(); \
    printf("%s %s: %d checks, %d failures\n", id, #fn, checks - c0_, fails - f0_); } while (0)
static void test_gfx_all(void) {
  GROUP("GP-1B-01", test_gfx_matrix);
  GROUP("GP-1B-02", test_gfx_transitions);
  GROUP("GP-1B-03", test_gfx_spoof);
  GROUP("GP-1B-04", test_gfx_settings);
  GROUP("GP-1B-05", test_gfx_prefs);
  GROUP("GP-1B-06", test_gfx_catalog);
  GROUP("GP-1B-07", test_gfx_optin);
  GROUP("GP-1B-08", test_gfx_nodes);
  GROUP("GP-1B-09", test_gfx_reconcile);
  GROUP("GP-1B-10", test_gfx_hotattach);
  GROUP("GP-1B-11", test_gfx_rebind);
  GROUP("GP-1B-12", test_gfx_detach);
  GROUP("GP-1B-13", test_gfx_n08);
  GROUP("GP-1B-14", test_gfx_close_detach);
  GROUP("GP-1B-15", test_gfx_selftest);
  GROUP("GP-1B-16", test_gfx_identity);
  GROUP("GP-1B-17", test_gfx_interrupt);
  GROUP("GP-1B-18", test_gfx_deadline);
  GROUP("GP-1B-19", test_gfx_session);
  GROUP("GP-1B-20", test_gfx_socket);
  GROUP("GP-1B-21", test_gfx_status);
  GROUP("GP-1B-22", test_gfx_broadcast);
  GROUP("GP-1B-23", test_gfx_vmware);
}
static void test_fix_all(void) {                  /* review fix pass (INTERFACES 29, TASKS_1B 12) */
  GROUP("GP-R1", test_r1_shutdown);
  GROUP("GP-R2", test_r2_rebind_intel);
  GROUP("GP-R3", test_r3_reconcile_on);
  GROUP("GP-R4", test_r4_dgpu_session);
  GROUP("GP-R6", test_r6_compute_window);
  GROUP("GP-R8", test_r8_faults);
  GROUP("GP-R12", test_r12_markers);
}
static void test_w2b_all(void) {                  /* Wave 2b (INTERFACES 30) */
  GROUP("GP-W2B-1", test_w2b_esp_lock);
  GROUP("GP-W3-1", test_w3_esp_lock);
}
