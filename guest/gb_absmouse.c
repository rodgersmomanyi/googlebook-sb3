/* gb_absmouse.c -- VMware pointer -> Android 15+ native absolute mouse, via uinput.
 *
 * VMware's absolute USB mouse (vmx mouse.vusb.useBasicMouse = "FALSE": ABS_X/ABS_Y 0..32767, no BTN_TOUCH) and the
 * psmouse/vmmouse nodes match no Android EventHub device class, so Android drops them. This daemon EVIOCGRABs every
 * VMware pointer node and re-emits one "GB Absolute Mouse": single-touch + INPUT_PROP_POINTER + BTN_TOOL_MOUSE, raw
 * ABS range == display pixels. EventHub classifies it TOUCH (BTN_TOUCH + ABS_X/Y); the IDC (touch.deviceType =
 * pointer) puts TouchInputMapper in POINTER mode with source MOUSE; ToolType::MOUSE -> dispatchPointerMouse ->
 * PointerChoreographer's absolute-mouse branch (pc.setPosition): the Android pointer sits exactly under the host
 * cursor, with hover, buttons and wheel. Relative sources (the basic USB mouse) are integrated as a fallback.
 * Wheel scrolling goes out on a second uinput device, "GB Mouse Wheel" (see WHEEL_NAME).
 * The ABS range follows the display: --size is the boot default, then the active CRTC mode is re-read every 2 s and
 * the uinput device is recreated when the resolution changes (Settings > Display, cmd display ...).
 * Static NDK build: clang --target=x86_64-linux-android34 -static -O2 -Wall -o gb_absmouse gb_absmouse.c */
#include <drm/drm.h>
#include <drm/drm_mode.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/inotify.h>
#include <sys/ioctl.h>
#include <sys/system_properties.h>
#include <time.h>
#include <unistd.h>

#define OUT_NAME "GB Absolute Mouse"   /* IDC: GB_Absolute_Mouse.idc */
/* Android's TouchInputMapper (POINTER mode) has no scroll axes, so the wheel goes to a second, cursor-class device
   (BTN_LEFT + REL_X/Y, never moved or pressed): CursorInputMapper turns it into ACTION_SCROLL at the shared pointer. */
#define WHEEL_NAME "GB Mouse Wheel"    /* IDC: GB_Mouse_Wheel.idc */
#define MAXSRC 8
#define LB (8 * sizeof(long))
#define NL(n) (((n) + LB - 1) / LB)
#define TST(b, a) (((a)[(b) / LB] >> ((b) % LB)) & 1UL)
static const int BTNS[] = {BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA, BTN_FORWARD, BTN_BACK};
#define NBTN ((int)(sizeof BTNS / sizeof BTNS[0]))
struct src { int fd, abs, dropped, minx, maxx, miny, maxy, hires, hhires; unsigned btn; char path[32]; };
static struct src S[MAXSRC];
static int W = 1280, H = 800, ufd = -1, wfd = -1, curx, cury, lastx = -1, lasty = -1;
static int wheel_hr, hwheel_hr, wheel_rem, hwheel_rem;   /* scroll in hi-res units (120 per notch) */
static unsigned outbtn;

static void emit_to(int fd, int type, int code, int val) {
  struct input_event e; memset(&e, 0, sizeof e);
  e.type = type; e.code = code; e.value = val;
  if (write(fd, &e, sizeof e) != (ssize_t)sizeof e) perror("gb_absmouse: write");
}
static void emit(int type, int code, int val) { emit_to(ufd, type, code, val); }
static void flush_wheel(void) {           /* hi-res for smooth scrolling, plus whole notches for REL_WHEEL */
  int any = 0, n;
  if (wfd < 0) { wheel_hr = hwheel_hr = 0; return; }
  if (wheel_hr) {
    emit_to(wfd, EV_REL, REL_WHEEL_HI_RES, wheel_hr);
    wheel_rem += wheel_hr; wheel_hr = 0; any = 1;
    if ((n = wheel_rem / 120)) { emit_to(wfd, EV_REL, REL_WHEEL, n); wheel_rem -= n * 120; }
  }
  if (hwheel_hr) {
    emit_to(wfd, EV_REL, REL_HWHEEL_HI_RES, hwheel_hr);
    hwheel_rem += hwheel_hr; hwheel_hr = 0; any = 1;
    if ((n = hwheel_rem / 120)) { emit_to(wfd, EV_REL, REL_HWHEEL, n); hwheel_rem -= n * 120; }
  }
  if (any) emit_to(wfd, EV_SYN, SYN_REPORT, 0);
}
static int clampi(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }
static int scale(int v, int mn, int mx, int out) {
  if (mx <= mn) return 0;
  return clampi((int)(((long long)(v - mn) * (out - 1) + (mx - mn) / 2) / (mx - mn)), 0, out - 1);
}
static void flush(void) {                 /* one coalesced report per source SYN_REPORT */
  unsigned b = 0; int any = 0;
  for (int i = 0; i < MAXSRC; i++) if (S[i].fd >= 0) b |= S[i].btn;   /* OR all sources */
  if (curx != lastx) { emit(EV_ABS, ABS_X, curx); lastx = curx; any = 1; }
  if (cury != lasty) { emit(EV_ABS, ABS_Y, cury); lasty = cury; any = 1; }
  for (int i = 0; i < NBTN; i++)
    if (((b ^ outbtn) >> i) & 1) { emit(EV_KEY, BTNS[i], (b >> i) & 1); any = 1; }
  outbtn = b;
  if (any) emit(EV_SYN, SYN_REPORT, 0);
  flush_wheel();
}
static void resync(struct src *s) {       /* after open or SYN_DROPPED */
  struct input_absinfo ai; unsigned long key[NL(KEY_CNT)];
  memset(&ai, 0, sizeof ai); memset(key, 0, sizeof key);
  if (s->abs && !ioctl(s->fd, EVIOCGABS(ABS_X), &ai)) curx = scale(ai.value, s->minx, s->maxx, W);
  if (s->abs && !ioctl(s->fd, EVIOCGABS(ABS_Y), &ai)) cury = scale(ai.value, s->miny, s->maxy, H);
  s->btn = 0;
  if (ioctl(s->fd, EVIOCGKEY(sizeof key), key) >= 0)
    for (int i = 0; i < NBTN; i++) if (TST(BTNS[i], key)) s->btn |= 1u << i;
}
static void try_open(const char *path) {
  unsigned long ev[NL(EV_CNT)] = {0}, key[NL(KEY_CNT)] = {0}, ab[NL(ABS_CNT)] = {0}, rel[NL(REL_CNT)] = {0};
  char name[128] = {0}; struct src *s = NULL;
  for (int i = 0; i < MAXSRC; i++) {
    if (S[i].fd >= 0 && !strcmp(S[i].path, path)) return;
    if (S[i].fd < 0 && !s) s = &S[i];
  }
  if (!s) return;
  int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
  if (fd < 0) return;
  ioctl(fd, EVIOCGNAME(sizeof name - 1), name);
  ioctl(fd, EVIOCGBIT(0, sizeof ev), ev);
  ioctl(fd, EVIOCGBIT(EV_KEY, sizeof key), key);
  ioctl(fd, EVIOCGBIT(EV_ABS, sizeof ab), ab);
  ioctl(fd, EVIOCGBIT(EV_REL, sizeof rel), rel);
  int isabs = TST(EV_ABS, ev) && TST(ABS_X, ab) && TST(ABS_Y, ab) && !TST(BTN_TOUCH, key);
  int isrel = TST(EV_REL, ev) && TST(REL_X, rel) && TST(REL_Y, rel);
  /* VMware pointers only: 'VMware VMware Virtual USB Mouse' (0e0f:0003) and the
     'VirtualPS/2 VMware VMMouse' nodes (abs + rel/wheel) from psmouse/vmmouse. */
  if (!strstr(name, "VMware") || !(isabs || isrel) || ioctl(fd, EVIOCGRAB, 1) < 0) { close(fd); return; }
  memset(s, 0, sizeof *s);
  s->fd = fd; s->abs = isabs;
  s->hires = TST(REL_WHEEL_HI_RES, rel); s->hhires = TST(REL_HWHEEL_HI_RES, rel);
  snprintf(s->path, sizeof s->path, "%s", path);
  if (isabs) {
    struct input_absinfo ai; memset(&ai, 0, sizeof ai);
    ioctl(fd, EVIOCGABS(ABS_X), &ai); s->minx = ai.minimum; s->maxx = ai.maximum;
    ioctl(fd, EVIOCGABS(ABS_Y), &ai); s->miny = ai.minimum; s->maxy = ai.maximum;
  }
  resync(s);
  fprintf(stderr, "gb_absmouse: grabbed %s [%s] abs=%d rel=%d\n", path, name, isabs, isrel);
}
static void scan(void) {
  char p[32];
  for (int i = 0; i < 64; i++) { snprintf(p, sizeof p, "/dev/input/event%d", i); try_open(p); }
}
static void handle(struct src *s) {
  struct input_event ev[64];
  ssize_t n = read(s->fd, ev, sizeof ev);
  if (n < 0 && (errno == EAGAIN || errno == EINTR)) return;
  if (n <= 0) { close(s->fd); s->fd = -1; s->btn = 0; flush(); return; }  /* ENODEV: unplugged */
  for (int i = 0; i < (int)(n / (ssize_t)sizeof ev[0]); i++) {
    const struct input_event *e = &ev[i];
    if (s->dropped) {
      if (e->type == EV_SYN && e->code == SYN_REPORT) { s->dropped = 0; resync(s); flush(); }
      continue;
    }
    if (e->type == EV_ABS && e->code == ABS_X) curx = scale(e->value, s->minx, s->maxx, W);
    else if (e->type == EV_ABS && e->code == ABS_Y) cury = scale(e->value, s->miny, s->maxy, H);
    else if (e->type == EV_REL && e->code == REL_X) curx = clampi(curx + e->value, 0, W - 1);
    else if (e->type == EV_REL && e->code == REL_Y) cury = clampi(cury + e->value, 0, H - 1);
    /* sources with hi-res wheels (the VMware USB mouse) report both: use the hi-res stream, else 120 per notch */
    else if (e->type == EV_REL && e->code == REL_WHEEL) { if (!s->hires) wheel_hr += e->value * 120; }
    else if (e->type == EV_REL && e->code == REL_HWHEEL) { if (!s->hhires) hwheel_hr += e->value * 120; }
    else if (e->type == EV_REL && e->code == REL_WHEEL_HI_RES) wheel_hr += e->value;
    else if (e->type == EV_REL && e->code == REL_HWHEEL_HI_RES) hwheel_hr += e->value;
    else if (e->type == EV_KEY) {
      for (int b = 0; b < NBTN; b++)
        if (e->code == BTNS[b]) s->btn = e->value ? (s->btn | (1u << b)) : (s->btn & ~(1u << b));
    } else if (e->type == EV_SYN && e->code == SYN_REPORT) flush();
    else if (e->type == EV_SYN && e->code == SYN_DROPPED) s->dropped = 1;
  }
}
static int make_uinput(void) {
  struct uinput_setup us; struct uinput_abs_setup ab;
  int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
  if (fd < 0) return -1;
  ioctl(fd, UI_SET_EVBIT, EV_SYN); ioctl(fd, UI_SET_EVBIT, EV_KEY);
  ioctl(fd, UI_SET_EVBIT, EV_ABS);
  ioctl(fd, UI_SET_KEYBIT, BTN_TOUCH);       /* only so EventHub classifies TOUCH; never pressed */
  ioctl(fd, UI_SET_KEYBIT, BTN_TOOL_MOUSE);  /* => ToolType::MOUSE */
  for (int i = 0; i < NBTN; i++) ioctl(fd, UI_SET_KEYBIT, BTNS[i]);
  ioctl(fd, UI_SET_PROPBIT, INPUT_PROP_POINTER);  /* indirect => DeviceType::POINTER */
  memset(&us, 0, sizeof us);
  us.id.bustype = BUS_VIRTUAL;  /* vendor=product=0 => IDC looked up by name */
  us.id.version = 1;
  snprintf(us.name, sizeof us.name, "%s", OUT_NAME);
  memset(&ab, 0, sizeof ab);
  if (ioctl(fd, UI_DEV_SETUP, &us) < 0) goto fail;
  ab.code = ABS_X; ab.absinfo.maximum = W - 1;  /* raw range == display px => POINTER scale 1.0 */
  if (ioctl(fd, UI_ABS_SETUP, &ab) < 0) goto fail;
  ab.code = ABS_Y; ab.absinfo.maximum = H - 1;
  if (ioctl(fd, UI_ABS_SETUP, &ab) < 0) goto fail;
  if (ioctl(fd, UI_DEV_CREATE) < 0) goto fail;
  return fd;
fail:
  close(fd); return -1;
}
static int make_wheel(void) {             /* EventHub CURSOR class needs BTN_MOUSE + REL_X/Y; neither is ever sent */
  struct uinput_setup us;
  int fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK | O_CLOEXEC);
  if (fd < 0) return -1;
  ioctl(fd, UI_SET_EVBIT, EV_SYN); ioctl(fd, UI_SET_EVBIT, EV_KEY); ioctl(fd, UI_SET_EVBIT, EV_REL);
  ioctl(fd, UI_SET_KEYBIT, BTN_LEFT);
  ioctl(fd, UI_SET_RELBIT, REL_X); ioctl(fd, UI_SET_RELBIT, REL_Y);
  ioctl(fd, UI_SET_RELBIT, REL_WHEEL); ioctl(fd, UI_SET_RELBIT, REL_HWHEEL);
  ioctl(fd, UI_SET_RELBIT, REL_WHEEL_HI_RES); ioctl(fd, UI_SET_RELBIT, REL_HWHEEL_HI_RES);
  memset(&us, 0, sizeof us);
  us.id.bustype = BUS_VIRTUAL;
  us.id.version = 1;
  snprintf(us.name, sizeof us.name, "%s", WHEEL_NAME);
  if (ioctl(fd, UI_DEV_SETUP, &us) < 0 || ioctl(fd, UI_DEV_CREATE) < 0) { close(fd); return -1; }
  return fd;
}
/* Size of the active CRTC mode. card0 is opened only after boot_completed: by then the composer holds DRM master, so
   this open can never take master from it (the first opener of a DRM device becomes master). The fd is kept: every
   open does a CAP_SYS_ADMIN check, which logs an SELinux audit line each time. */
static int display_size(int *w, int *h) {
  static int fd = -1;
  if (fd < 0) {
    char v[PROP_VALUE_MAX] = "";
    if (__system_property_get("sys.boot_completed", v) <= 0 || strcmp(v, "1")) return 0;
    if ((fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC)) < 0) return 0;
  }
  uint32_t ids[16]; struct drm_mode_card_res res; int ok = 0;
  memset(&res, 0, sizeof res);
  res.crtc_id_ptr = (uintptr_t)ids; res.count_crtcs = 16;
  if (!ioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &res))
    for (uint32_t i = 0; i < res.count_crtcs && i < 16 && !ok; i++) {
      struct drm_mode_crtc c; memset(&c, 0, sizeof c); c.crtc_id = ids[i];
      if (!ioctl(fd, DRM_IOCTL_MODE_GETCRTC, &c) && c.mode_valid && c.mode.hdisplay && c.mode.vdisplay) {
        *w = c.mode.hdisplay; *h = c.mode.vdisplay; ok = 1;
      }
    }
  return ok;
}
static void resize(int w, int h) {          /* new display size -> new ABS range (raw px == display px) */
  if (w == W && h == H) return;
  W = w; H = h;
  ioctl(ufd, UI_DEV_DESTROY); close(ufd);
  while ((ufd = make_uinput()) < 0) { perror("gb_absmouse: /dev/uinput"); sleep(1); }
  curx = W / 2; cury = H / 2; lastx = lasty = -1; outbtn = 0;
  for (int i = 0; i < MAXSRC; i++) if (S[i].fd >= 0) resync(&S[i]);
  emit(EV_KEY, BTN_TOOL_MOUSE, 1);
  flush();
  fprintf(stderr, "gb_absmouse: display %dx%d\n", W, H);
}
static long long now_ms(void) {
  struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec * 1000LL + t.tv_nsec / 1000000;
}
int main(int argc, char **argv) {
  for (int i = 1; i + 1 < argc; i++)
    if (!strcmp(argv[i], "--size") && sscanf(argv[i + 1], "%dx%d", &W, &H) != 2) return 2;
  for (int i = 0; i < MAXSRC; i++) S[i].fd = -1;
  while ((ufd = make_uinput()) < 0) { perror("gb_absmouse: /dev/uinput"); sleep(1); }
  if ((wfd = make_wheel()) < 0) perror("gb_absmouse: wheel device");   /* pointer still works without it */
  curx = W / 2; cury = H / 2;
  emit(EV_KEY, BTN_TOOL_MOUSE, 1);   /* mouse tool permanently in range */
  flush();
  int in = inotify_init1(IN_NONBLOCK | IN_CLOEXEC);
  if (in >= 0 && inotify_add_watch(in, "/dev/input", IN_CREATE | IN_ATTRIB) < 0) { close(in); in = -1; }
  scan();
  long long next_size = 0;
  for (;;) {
    struct pollfd pf[MAXSRC + 1]; struct src *who[MAXSRC + 1]; int n = 0;
    if (in >= 0) { pf[n].fd = in; pf[n].events = POLLIN; who[n++] = NULL; }
    for (int i = 0; i < MAXSRC; i++)
      if (S[i].fd >= 0) { pf[n].fd = S[i].fd; pf[n].events = POLLIN; who[n++] = &S[i]; }
    int r = poll(pf, n, 2000);
    if (now_ms() >= next_size) {
      int w, h;
      if (display_size(&w, &h)) resize(w, h);
      next_size = now_ms() + 2000;
    }
    if (r <= 0) { if (in < 0) scan(); continue; }
    for (int i = 0; i < n; i++) {
      if (!(pf[i].revents & (POLLIN | POLLHUP | POLLERR))) continue;
      if (who[i]) { if (who[i]->fd >= 0) handle(who[i]); continue; }
      char buf[4096];
      while (read(in, buf, sizeof buf) > 0) {}
      usleep(100000);   /* let ueventd finish the node */
      scan();
    }
  }
}
