"""End-to-end test of iptsd + gb_iptsd.sh without IPTS hardware (run as root on a Linux box with uhid + uinput).

Creates a fake IPTS HID device through /dev/uhid that looks like the linux-surface ipts driver's device (name
"IPTS 045E:09B2", HID bus 0, the driver's singletouch + fallback descriptor, data report 0x41, set-mode feature 0x42,
optionally an EDS2 metadata feature 0x43). It answers iptsd's SET_REPORT/GET_REPORT, and once iptsd switched it to
multitouch mode it sends synthetic heatmaps (finger swipe, two fingers) and Surface Pen samples, while reading the
uinput devices iptsd created ("IPTSD Virtual Touchscreen/Stylus 045E:09B2") and checking what comes out.
--recreate removes and re-adds the HID device (as the MEI bus does on resume) and repeats the check; --suspend FILE
bumps a fake suspend_stats/success counter (GB_IPTSD_SUSPEND_STATS) and expects the launcher to restart iptsd.
Exit code 0 = all checks passed.
"""
import argparse, fcntl, glob, os, select, struct, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ipts_frames as F

UHID_DESTROY, UHID_START, UHID_STOP, UHID_OPEN, UHID_CLOSE, UHID_OUTPUT = 1, 2, 3, 4, 5, 6
UHID_GET_REPORT, UHID_GET_REPORT_REPLY, UHID_CREATE2, UHID_INPUT2 = 9, 10, 11, 12
UHID_SET_REPORT, UHID_SET_REPORT_REPLY = 13, 14
EV_SIZE = 4 + 4372

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y, ABS_PRESSURE, ABS_TILT_X, ABS_TILT_Y = 0x00, 0x01, 0x18, 0x1a, 0x1b
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x2f, 0x35, 0x36, 0x39
BTN_TOUCH, BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_STYLUS = 0x14a, 0x140, 0x141, 0x14b
INPUT_PROP_DIRECT = 1


def log(*a):
    print("[e2e %.2f]" % (time.time() % 1000), *a, flush=True)


class Uhid:
    def __init__(self, name, desc, vendor, product, bus=0):
        self.fd = os.open("/dev/uhid", os.O_RDWR)
        req = struct.pack("<128s64s64sHHIIII", name.encode(), b"ipts-e2e", b"", len(desc), bus, vendor, product,
                          0, 0) + desc.ljust(4096, b"\0")
        self.write(UHID_CREATE2, req)
        self.mode = None
        self.mode_changes = []

    def write(self, t, payload):
        buf = struct.pack("<I", t) + payload
        os.write(self.fd, buf.ljust(EV_SIZE, b"\0"))

    def input(self, data):
        self.write(UHID_INPUT2, struct.pack("<H", len(data)) + data.ljust(4096, b"\0"))

    def poll(self, timeout, feature):
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return
        ev = os.read(self.fd, EV_SIZE)
        t = struct.unpack_from("<I", ev)[0]
        if t == UHID_SET_REPORT:
            rid, rnum, rtype, size = struct.unpack_from("<IBBH", ev, 4)
            data = ev[12:12 + size]
            if rnum == F.MODE_ID and size >= 2:
                self.mode = data[1]
                self.mode_changes.append(data[1])
                log(f"SET_REPORT mode -> {data[1]} ({'multitouch' if data[1] else 'singletouch'})")
            self.write(UHID_SET_REPORT_REPLY, struct.pack("<IH", rid, 0))
        elif t == UHID_GET_REPORT:
            rid, rnum, rtype = struct.unpack_from("<IBB", ev, 4)
            if rnum == F.META_ID and feature is not None:
                log("GET_REPORT metadata")
                self.write(UHID_GET_REPORT_REPLY, struct.pack("<IHH", rid, 0, len(feature)) + feature)
            else:
                self.write(UHID_GET_REPORT_REPLY, struct.pack("<IHH", rid, 5, 0))  # EIO
        elif t in (UHID_START, UHID_OPEN, UHID_CLOSE, UHID_STOP):
            log("uhid event", {2: "START", 3: "STOP", 4: "OPEN", 5: "CLOSE"}[t])

    def destroy(self):
        self.write(UHID_DESTROY, b"")
        os.close(self.fd)


def evname(fd):
    buf = bytearray(256)
    n = fcntl.ioctl(fd, 0x80ff4506, buf)  # EVIOCGNAME(255)
    return bytes(buf).split(b"\0")[0].decode(errors="replace")


def evid(fd):
    buf = bytearray(8)
    fcntl.ioctl(fd, 0x80084502, buf)      # EVIOCGID
    return struct.unpack("<HHHH", buf)


def evprop(fd):
    buf = bytearray(4)
    fcntl.ioctl(fd, 0x80045509 if False else (2 << 30) | (4 << 16) | (ord('E') << 8) | 0x09, buf)  # EVIOCGPROP(4)
    return struct.unpack("<I", buf)[0]


def absinfo(fd, code):
    buf = bytearray(24)
    fcntl.ioctl(fd, (2 << 30) | (24 << 16) | (ord('E') << 8) | (0x40 + code), buf)  # EVIOCGABS
    return struct.unpack("<6i", buf)  # value, min, max, fuzz, flat, resolution


def find_event(name, timeout):
    end = time.time() + timeout
    while time.time() < end:
        for p in sorted(glob.glob("/dev/input/event*")):
            try:
                fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            if evname(fd) == name:
                return p, fd
            os.close(fd)
        time.sleep(0.2)
    return None, None


def drain(fd, out):
    while True:
        try:
            b = os.read(fd, 24 * 64)
        except BlockingIOError:
            return
        for i in range(0, len(b), 24):
            _, _, t, c, v = struct.unpack_from("<qqHHi", b, i)
            out.append((t, c, v))


def wait_mode(dev, want, timeout, feature):
    end = time.time() + timeout
    while time.time() < end:
        dev.poll(0.2, feature)
        if dev.mode == want:
            return True
    return False


def run_round(dev, args, feature, size, tag):
    ok = True
    if not wait_mode(dev, 1, args.timeout, feature):
        log(f"{tag}: FAIL iptsd never switched the device to multitouch mode")
        return False
    vp = "%04X:%04X" % (args.vendor, args.product)
    tname, sname = f"IPTSD Virtual Touchscreen {vp}", f"IPTSD Virtual Stylus {vp}"
    tp, tfd = find_event(tname, 10)
    sp, sfd = find_event(sname, 10)
    if tfd is None or sfd is None:
        log(f"{tag}: FAIL uinput devices missing: {tname}={tp} {sname}={sp}")
        return False
    log(f"{tag}: {tname} = {tp} id(bus,vid,pid,ver)={evid(tfd)} props=0x{evprop(tfd):x}")
    log(f"{tag}: {sname} = {sp} id={evid(sfd)} props=0x{evprop(sfd):x}")
    log(f"{tag}: touch ABS_MT_POSITION_X {absinfo(tfd, ABS_MT_POSITION_X)[1:]} Y {absinfo(tfd, ABS_MT_POSITION_Y)[1:]}")
    log(f"{tag}: stylus ABS_X {absinfo(sfd, ABS_X)[1:]} PRESSURE {absinfo(sfd, ABS_PRESSURE)[1:]} "
        f"TILT_X {absinfo(sfd, ABS_TILT_X)[1:]}")
    tev, sev = [], []
    drain(tfd, tev); drain(sfd, sev); tev.clear(); sev.clear()
    reps = F.build_reports(eds2=args.eds2, size=size)
    for r in reps:
        dev.input(r)
        dev.poll(0.008, feature)
        drain(tfd, tev); drain(sfd, sev)
    t_end = time.time() + 0.5
    while time.time() < t_end:
        dev.poll(0.05, feature); drain(tfd, tev); drain(sfd, sev)
    # --- touch checks
    xs = [v for t, c, v in tev if t == EV_ABS and c == ABS_MT_POSITION_X]
    ys = [v for t, c, v in tev if t == EV_ABS and c == ABS_MT_POSITION_Y]
    tids = [v for t, c, v in tev if t == EV_ABS and c == ABS_MT_TRACKING_ID]
    slots = {v for t, c, v in tev if t == EV_ABS and c == ABS_MT_SLOT}
    starts = len([v for v in tids if v >= 0]); lifts = len([v for v in tids if v < 0])
    log(f"{tag}: touch events={len(tev)} X {min(xs) if xs else None}..{max(xs) if xs else None} "
        f"Y {min(ys) if ys else None}..{max(ys) if ys else None} slots={sorted(slots)} "
        f"tracking-id sets={starts} lifts={lifts}")
    # finger 1 swipes x 0.15 -> 0.85 at y 0.30; InvertY (SB3 preset / metadata) puts it at y ~0.70 of 7200
    if not xs or max(xs) - min(xs) < 0.5 * 9600 or not (0.05 * 9600 < min(xs) < 0.35 * 9600):
        log(f"{tag}: FAIL touch X range"); ok = False
    swipe_y = [v for t, c, v in tev[:len(tev) // 2] if t == EV_ABS and c == ABS_MT_POSITION_Y]
    if not swipe_y or not (0.60 * 7200 < sum(swipe_y) / len(swipe_y) < 0.80 * 7200):
        log(f"{tag}: FAIL touch Y (expected ~0.70*7200 with InvertY, got mean "
            f"{sum(swipe_y) / max(1, len(swipe_y)):.0f})"); ok = False
    if len(slots) < 2:
        log(f"{tag}: FAIL second finger never reported"); ok = False
    if lifts < 1:
        log(f"{tag}: FAIL no touch lift"); ok = False
    # --- stylus checks
    sx = [v for t, c, v in sev if t == EV_ABS and c == ABS_X]
    sp_ = [v for t, c, v in sev if t == EV_ABS and c == ABS_PRESSURE]
    pen = [v for t, c, v in sev if t == EV_KEY and c == BTN_TOOL_PEN]
    btouch = [v for t, c, v in sev if t == EV_KEY and c == BTN_TOUCH]
    tilt = [v for t, c, v in sev if t == EV_ABS and c in (ABS_TILT_X, ABS_TILT_Y)]
    log(f"{tag}: stylus events={len(sev)} X {min(sx) if sx else None}..{max(sx) if sx else None} "
        f"pressure max {max(sp_) if sp_ else None} BTN_TOOL_PEN {sorted(set(pen))} BTN_TOUCH {sorted(set(btouch))} "
        f"tilt x/y values {sorted(set(tilt))[:4]}")
    if not sx or not (0.15 * 9600 < min(sx) < 0.25 * 9600) or not (0.75 * 9600 < max(sx) < 0.85 * 9600):
        log(f"{tag}: FAIL stylus X range"); ok = False
    if not sp_ or not (1900 < max(sp_) < 2100):
        log(f"{tag}: FAIL stylus pressure (sent 2000/4096)"); ok = False
    if 1 not in pen or 0 not in pen:
        log(f"{tag}: FAIL pen proximity in/out"); ok = False
    if 1 not in btouch:
        log(f"{tag}: FAIL pen contact"); ok = False
    os.close(tfd); os.close(sfd)
    log(f"{tag}: {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desc-h", required=True)
    ap.add_argument("--eds2", action="store_true")
    ap.add_argument("--vendor", type=lambda s: int(s, 0), default=0x045E)
    ap.add_argument("--product", type=lambda s: int(s, 0), default=0x09B2)
    ap.add_argument("--timeout", type=float, default=40)
    ap.add_argument("--recreate", action="store_true")
    ap.add_argument("--suspend", help="fake suspend_stats/success file to bump (launcher GB_IPTSD_SUSPEND_STATS)")
    ap.add_argument("--hold", type=float, default=0, help="keep the device alive N s at the end")
    ap.add_argument("--name", help="HID device name (default 'IPTS VVVV:PPPP' like the ipts driver)")
    a = ap.parse_args()

    count = 4000                                   # uhid limit: reports <= 4096 bytes
    desc = F.descriptor(a.desc_h, eds2=a.eds2, max_count=count)
    size = 3 + count
    feature = None
    if a.eds2:
        feature = bytes([F.META_ID]) + F.metadata_feature()
    name = a.name or "IPTS %04X:%04X" % (a.vendor, a.product)
    ok = True
    dev = Uhid(name, desc, a.vendor, a.product)
    log(f"created uhid device '{name}' descriptor {len(desc)} bytes eds2={a.eds2}")
    time.sleep(0.5)
    for h in glob.glob("/sys/class/hidraw/hidraw*/device/uevent"):
        u = open(h).read()
        if f"HID_NAME={name}" in u:
            hr = h.split("/")[4]
            names = [open(n).read().strip() for n in glob.glob(f"/sys/class/hidraw/{hr}/device/input/input*/name")]
            log(f"hidraw node {hr}: {u.split()} kernel input devices {names}")
    ok &= run_round(dev, a, feature, size, "round1")

    if a.suspend:
        dev.mode_changes.clear()
        n = int(open(a.suspend).read().strip() or 0) + 1
        open(a.suspend, "w").write(f"{n}\n")
        log(f"bumped {a.suspend} to {n}: expecting the launcher to restart iptsd")
        dev.mode = 0 if dev.mode == 1 else dev.mode
        # iptsd's shutdown sends singletouch (0), the new instance multitouch (1)
        if not wait_mode(dev, 1, a.timeout, feature) or 0 not in dev.mode_changes:
            log(f"suspend: FAIL no restart seen (mode changes {dev.mode_changes})"); ok = False
        else:
            log(f"suspend: iptsd restarted (mode changes {dev.mode_changes})")
            ok &= run_round(dev, a, feature, size, "after-resume")

    if a.recreate:
        log("destroying the HID device (like ipts_remove on an MEI reset)")
        dev.destroy()
        time.sleep(3)
        dev = Uhid(name, desc, a.vendor, a.product)
        log("re-created the HID device")
        ok &= run_round(dev, a, feature, size, "recreated")

    end = time.time() + a.hold
    while time.time() < end:
        dev.poll(0.2, feature)
    dev.destroy()
    log("RESULT", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
