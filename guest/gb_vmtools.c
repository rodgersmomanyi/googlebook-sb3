/* gb_vmtools.c -- minimal "VMware Tools" for the lapis Android guest.
 *
 * VMware Workstation only runs the pointer ungrabbed (seamless: no click-to-capture, no Ctrl+Alt release; the host
 * pointer simply moves across the VM window as an absolute pointer) when VMware Tools is running in the guest and the
 * guest uses an absolute pointing device (psmouse/vmmouse here). This daemon is just enough of Tools for that, over
 * the VMware backdoor (I/O port 0x5658, GuestRPC "RPCI" + "TCLO" channels, low-bandwidth protocol):
 *   - RPCI: "tools.set.version 2147483647" (unmanaged / OS-provided tools) + capability advertisements
 *   - TCLO: answers the host's reset / ping / Capabilities_Register / Set_Option heartbeat so Tools stay "running";
 *           OS_Halt / OS_PowerDown / OS_Reboot (VM > Power > Shut Down / Restart Guest) run a clean Android
 *           power-off / reboot.
 * Needs root (ioperm). Static NDK build: clang --target=x86_64-linux-android34 -static -O2 -o gb_vmtools gb_vmtools.c
 */
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#define BDOOR_MAGIC 0x564D5868u
#define BDOOR_PORT 0x5658u
#define BDOOR_CMD_MESSAGE 30u
enum { MSG_OPEN = 0, MSG_SENDSIZE, MSG_SENDPAYLOAD, MSG_RECVSIZE, MSG_RECVPAYLOAD, MSG_RECVSTATUS, MSG_CLOSE };
#define ST_SUCCESS 0x0001u
#define ST_DORECV 0x0002u
#define ST_CPT 0x0010u
#define FLAG_COOKIE 0x80000000u
#define PROTO_RPCI 0x49435052u
#define PROTO_TCLO 0x4f4c4354u

struct regs { uint32_t ax, bx, cx, dx, si, di; };
static void bdoor(struct regs *r) {
  r->ax = BDOOR_MAGIC;
  r->dx = (r->dx & 0xffff0000u) | BDOOR_PORT;
  __asm__ __volatile__("inl %%dx, %%eax"
                       : "+a"(r->ax), "+b"(r->bx), "+c"(r->cx), "+d"(r->dx), "+S"(r->si), "+D"(r->di)
                       : : "memory");
}
#define HI(x) ((x) >> 16)

struct chan { uint16_t id; uint32_t ck_hi, ck_lo; int ok; };

static int ch_open(struct chan *c, uint32_t proto) {
  uint32_t flags = FLAG_COOKIE;
  for (;;) {
    struct regs r = {0};
    r.cx = (MSG_OPEN << 16) | BDOOR_CMD_MESSAGE; r.bx = proto | flags;
    bdoor(&r);
    if (HI(r.cx) & ST_SUCCESS) { c->id = HI(r.dx); c->ck_hi = r.si; c->ck_lo = r.di; c->ok = 1; return 0; }
    if (!flags) return -1;
    flags = 0;
  }
}
static void ch_close(struct chan *c) {
  if (!c->ok) return;
  struct regs r = {0};
  r.cx = (MSG_CLOSE << 16) | BDOOR_CMD_MESSAGE; r.dx = (uint32_t)c->id << 16; r.si = c->ck_hi; r.di = c->ck_lo;
  bdoor(&r); c->ok = 0;
}
static void prep(struct chan *c, struct regs *r, uint32_t type) {
  memset(r, 0, sizeof *r);
  r->cx = (type << 16) | BDOOR_CMD_MESSAGE; r->dx = (uint32_t)c->id << 16; r->si = c->ck_hi; r->di = c->ck_lo;
}
/* returns 0 ok, -1 error, 1 checkpoint (retry) */
static int ch_send(struct chan *c, const char *buf, uint32_t n) {
  struct regs r;
  prep(c, &r, MSG_SENDSIZE); r.bx = n; bdoor(&r);
  if (!(HI(r.cx) & ST_SUCCESS)) return -1;
  for (uint32_t i = 0; i < n; i += 4) {
    uint32_t w = 0; memcpy(&w, buf + i, n - i < 4 ? n - i : 4);
    prep(c, &r, MSG_SENDPAYLOAD); r.bx = w; bdoor(&r);
    if (!(HI(r.cx) & ST_SUCCESS)) return (HI(r.cx) & ST_CPT) ? 1 : -1;
  }
  return 0;
}
/* returns length (>=0), -1 error; *got = 0 if no message pending */
static int ch_recv(struct chan *c, char *buf, uint32_t cap, int *got) {
  struct regs r;
  *got = 0;
  prep(c, &r, MSG_RECVSIZE); bdoor(&r);
  if (!(HI(r.cx) & ST_SUCCESS)) return -1;
  if (!(HI(r.cx) & ST_DORECV)) return 0;
  if (HI(r.dx) != MSG_SENDSIZE) return -1;
  uint32_t n = r.bx;
  if (n + 1 > cap) return -1;
  for (uint32_t i = 0; i < n; i += 4) {
    prep(c, &r, MSG_RECVPAYLOAD); r.bx = ST_SUCCESS; bdoor(&r);
    if (!(HI(r.cx) & ST_SUCCESS) || HI(r.dx) != MSG_SENDPAYLOAD) return -1;
    memcpy(buf + i, &r.bx, n - i < 4 ? n - i : 4);
  }
  buf[n] = 0;
  prep(c, &r, MSG_RECVSTATUS); r.bx = ST_SUCCESS; bdoor(&r);
  if (!(HI(r.cx) & ST_SUCCESS)) return -1;
  *got = 1;
  return (int)n;
}

/* One RPCI request -> reply (reply "1 ..." = success) */
static int rpci(const char *cmd, char *reply, uint32_t cap) {
  struct chan c = {0};
  int got = 0, n = -1;
  if (ch_open(&c, PROTO_RPCI)) return -1;
  if (ch_send(&c, cmd, (uint32_t)strlen(cmd)) == 0) n = ch_recv(&c, reply, cap, &got);
  ch_close(&c);
  return n;
}

static void log_rpci(const char *cmd) {
  char rep[512] = "";
  int n = rpci(cmd, rep, sizeof rep);
  printf("gb_vmtools: RPCI '%s' -> %d '%s'\n", cmd, n, n >= 0 ? rep : "");
  fflush(stdout);
}

static void msleep(int ms) { struct timespec t = {ms / 1000, (ms % 1000) * 1000000L}; nanosleep(&t, NULL); }

static void announce(void) {
  log_rpci("tools.set.version 2147483647");
  log_rpci("tools.capability.statechange");
  log_rpci("tools.capability.softpowerop_retry");
  log_rpci("log gb_vmtools: Android guest tools started");
}

/* "toolbox-dnd" = the user-session half of Tools (open-vm-tools' vmusr). Workstation's seamless pointer (pointer moves
 * in/out of the VM window without click-to-grab) wants the user-level Tools application present as well. */
static void user_session_announce(void) {
  /* No DnD / copy-paste implementation here, so advertise no capabilities -- just be present. */
  log_rpci("log gb_vmtools: user-session tools (toolbox-dnd) present");
}

/* Host battery bridge. VMware forwards only the host's FIRST battery (on the Surface Book 3 that is the clipboard
 * battery, which stays full while the keyboard-base battery drains first), so the guest's ACPI battery is wrong.
 * The host helper gb_hostbatt.exe puts Windows' composite battery into guestinfo.gb.battery as
 * "<percent> <ac 0|1> <charging|discharging|not-charging|full>"; this loop mirrors it into the test_power power
 * supply (kernel module), whose parameter writes raise power_supply uevents, so Android's health HAL updates at once.
 * Without a value (helper not running) the battery is reported absent rather than wrong. */
#define TP "/sys/module/test_power/parameters/"
static int put(const char *param, const char *val) {
  char path[128]; snprintf(path, sizeof path, TP "%s", param);
  FILE *f = fopen(path, "w");
  if (!f) return -1;
  int ok = fputs(val, f) >= 0;
  return (fclose(f) == 0 && ok) ? 0 : -1;
}
static void battery_loop(void) {
  char rep[256], last[256] = "";
  if (access(TP "battery_capacity", W_OK)) { printf("gb_vmtools: no test_power module, battery bridge off\n"); return; }
  put("battery_technology", "LION"); put("battery_health", "good"); put("usb_online", "off");
  put("battery_voltage", "7700000");          /* uV (test_power reports the parameter as voltage_now) */
  for (;;) {
    int n = rpci("info-get guestinfo.gb.battery", rep, sizeof rep);
    int pct = -1, ac = 1; char st[32] = "";
    if (n > 2 && rep[0] == '1' && sscanf(rep + 2, "%d %d %31s", &pct, &ac, st) >= 2 && pct >= 0 && pct <= 100) {
      if (strcmp(rep, last)) {
        char v[16]; snprintf(v, sizeof v, "%d", pct);
        if (strcmp(st, "full") && strcmp(st, "charging") && strcmp(st, "not-charging") && strcmp(st, "discharging"))
          snprintf(st, sizeof st, "%s", ac ? (pct >= 100 ? "full" : "charging") : "discharging");
        put("battery_present", "true");
        put("battery_capacity", v);
        put("ac_online", ac ? "on" : "off");
        /* current_now (uA): Android's health HAL derives charging vs discharging from its sign */
        put("battery_current", !strcmp(st, "charging") ? "1500000" : !strcmp(st, "discharging") ? "-1000000" : "0");
        put("battery_status", st);
        printf("gb_vmtools: host battery %d%% ac=%d %s\n", pct, ac, st);
        snprintf(last, sizeof last, "%s", rep);
      }
    } else if (strcmp(last, "-")) {
      put("battery_present", "false"); put("ac_online", "on"); put("battery_status", "unknown");
      printf("gb_vmtools: no host battery data (guestinfo.gb.battery unset): battery reported absent\n");
      strcpy(last, "-");
    }
    msleep(5000);
  }
}

static void tclo_loop(const char *app);

int main(void) {
  if (syscall(SYS_ioperm, BDOOR_PORT, 2, 1)) { perror("gb_vmtools: ioperm"); return 1; }
  setvbuf(stdout, NULL, _IOLBF, 0);
  signal(SIGCHLD, SIG_IGN);   /* auto-reap: the battery child exits when test_power is absent */
  announce();
  if (fork() == 0) {
    if (syscall(SYS_ioperm, BDOOR_PORT, 2, 1)) { perror("gb_vmtools: ioperm (dnd)"); return 1; }
    tclo_loop("toolbox-dnd");
  }
  if (fork() == 0) {
    if (syscall(SYS_ioperm, BDOOR_PORT, 2, 1)) { perror("gb_vmtools: ioperm (battery)"); return 1; }
    battery_loop();
    return 0;
  }
  tclo_loop("toolbox");
  return 0;
}

static void tclo_loop(const char *app) {
  int is_dnd = !strcmp(app, "toolbox-dnd");
  char cmd[4096], reply[512];
  for (;;) {
    struct chan t = {0};
    if (ch_open(&t, PROTO_TCLO)) { printf("gb_vmtools: TCLO open failed\n"); msleep(5000); continue; }
    printf("gb_vmtools[%s]: TCLO channel %u open\n", app, t.id);
    strcpy(reply, "");
    int idle = 0, action = 0;   /* 1 = power off, 2 = reboot, done after the "OK" reply reached the host */
    for (;;) {
      int r = ch_send(&t, reply, (uint32_t)strlen(reply));
      if (r < 0) break;
      if (action) {
        log_rpci(action == 1 ? "tools.os.statechange.status 1 1" : "tools.os.statechange.status 1 2");
        printf("gb_vmtools: host requested %s\n", action == 1 ? "power off" : "reboot");
        system(action == 1 ? "/system/bin/reboot -p" : "/system/bin/reboot");
        action = 0;
      }
      int got = 0, n = ch_recv(&t, cmd, sizeof cmd, &got);
      if (n < 0) break;
      if (!got) {          /* nothing from the host: poll again shortly, answering with an empty message */
        strcpy(reply, "");
        msleep(idle > 50 ? 500 : 50);
        idle++;
        continue;
      }
      idle = 0;
      if (!strncmp(cmd, "reset", 5)) {
        snprintf(reply, sizeof reply, "OK ATR %s", app);
        if (is_dnd) user_session_announce(); else announce();
      } else if (!strncmp(cmd, "OS_PowerOn", 10) || !strncmp(cmd, "OS_Resume", 9) || !strncmp(cmd, "OS_Suspend", 10)) {
        strcpy(reply, "OK ");   /* state-change notifications: nothing to do, but acknowledge */
      } else if (!strncmp(cmd, "ping", 4) || !strncmp(cmd, "Capabilities_Register", 21) ||
                 !strncmp(cmd, "Set_Option", 10) || !strncmp(cmd, "vmsvc.", 6)) {
        strcpy(reply, "OK ");
      } else if (!strncmp(cmd, "OS_Halt", 7) || !strncmp(cmd, "OS_PowerDown", 12)) {
        strcpy(reply, "OK ");
        action = 1;
      } else if (!strncmp(cmd, "OS_Reboot", 9)) {
        strcpy(reply, "OK ");
        action = 2;
      } else {
        snprintf(reply, sizeof reply, "ERROR Unknown command");
      }
      if (strncmp(cmd, "ping", 4)) printf("gb_vmtools[%s]: TCLO '%.80s' -> '%s'\n", app, cmd, reply);
    }
    printf("gb_vmtools: TCLO channel lost, reopening\n");
    ch_close(&t);
    msleep(1000);
  }
}
