"""Synthetic IPTS data for testing iptsd without the hardware.

Builds the HID report descriptor the linux-surface ipts driver exposes (parsed from drivers/hid/ipts/desc.h), and
input reports in the formats the driver/firmware deliver and iptsd parses (src/ipts/protocol/*.hpp, v3.1.1):
  EDS1 ("legacy"): [0x41][scan time u16] + hid::Frame{size, type=0xEE} + legacy::Header + ReportGroups(touch/stylus)
  EDS2:            [id][scan time u16]  + hid::Frame{size, type=0xFF reports} + report frames,
                   metadata via a feature report: hid::Frame{type=2} + metadata::Frame (105 bytes)
Heatmaps follow the IPTS convention: 255 = no contact, lower = stronger contact.
Also writes iptsd-dump compatible .bin files (hidraw_devinfo, u32 desc size, desc, then u64 size + data records)
that iptsd-perf replays.

Usage: python3 ipts_frames.py --desc-h /build/gki/common/drivers/hid/ipts/desc.h --out test.bin [--eds2]
"""
import argparse, math, re, struct

ROWS, COLS = 44, 64          # heatmap size used by the tests (the real SB3 size comes from the device)
MPP_MAX_X, MPP_MAX_Y = 9600, 7200
DATA_ID, MODE_ID, META_ID = 0x41, 0x42, 0x43


def kernel_descriptors(desc_h):
    """(singletouch, fallback) byte strings from the kernel's desc.h C arrays."""
    src = open(desc_h).read()
    out = {}
    for name in ("ipts_singletouch_descriptor", "ipts_fallback_descriptor"):
        body = re.search(name + r"\[\]\s*=\s*\{(.*?)\};", src, re.S).group(1)
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        out[name] = bytes(int(x, 16) for x in re.findall(r"0x[0-9A-Fa-f]{2}", body))
    return out["ipts_singletouch_descriptor"], out["ipts_fallback_descriptor"]


def data_report_count(fallback):
    """The Report Count (16 bit, 0x96 LL HH) of the gesture data field in the fallback descriptor."""
    i = fallback.index(b"\x96")
    return struct.unpack_from("<H", fallback, i + 1)[0]


def descriptor(desc_h, eds2=False, max_count=None):
    """HID descriptor as the ipts driver builds it: singletouch + fallback (EDS1) or + a device descriptor (EDS2).
    max_count shrinks the data report (uhid caps reports at 4096 bytes; the real one is 7485 + 3)."""
    st, fb = kernel_descriptors(desc_h)
    if max_count is not None:
        i = fb.index(b"\x96")
        fb = fb[:i + 1] + struct.pack("<H", max_count) + fb[i + 3:]
    if not eds2:
        return st + fb
    # EDS2-like device descriptor: same data/set-mode reports plus a metadata feature report (usage 0x0D/0x63).
    meta = bytes([0x85, META_ID, 0x05, 0x0D, 0x09, 0x63, 0x75, 0x08, 0x95, 7 + 105, 0xB1, 0x02])
    return st + fb[:-1] + meta + fb[-1:]


def heatmap(blobs, rows=ROWS, cols=COLS):
    """blobs: [(x 0..1, y 0..1, depth 0..255, sigma_px)] -> rows*cols bytes, row-major, 255 = idle."""
    hm = bytearray(b"\xff" * (rows * cols))
    for bx, by, depth, sigma in blobs:
        cx, cy = bx * (cols - 1), by * (rows - 1)
        for r in range(rows):
            for c in range(cols):
                d = depth * math.exp(-((c - cx) ** 2 + (r - cy) ** 2) / (2 * sigma * sigma))
                hm[r * cols + c] = max(0, hm[r * cols + c] - int(round(d)))
    return bytes(hm)


def rframe(rtype, payload):
    """report::Frame {u8 type, u8 flags, u16 size} + payload."""
    return struct.pack("<BBH", rtype, 0, len(payload)) + payload


def touch_frames(hm, rows=ROWS, cols=COLS):
    dims = struct.pack("<8B", rows, cols, 0, rows - 1, 0, cols - 1, 0, 255)
    return rframe(0x03, dims) + rframe(0x25, hm)


def stylus_frame(x, y, pressure, proximity=True, button=False, rubber=False, altitude=4500, azimuth=9000, ts=0):
    """MPP 1.51 sample (type 0x60): x/y in 0..9600/0..7200, pressure 0..4096, angles in degrees*100."""
    state = (1 if proximity else 0) | ((1 if pressure > 0 and not rubber else 0) << 1) | \
            ((1 if button else 0) << 2) | ((1 if rubber else 0) << 3)
    rep = struct.pack("<B3xI", 1, 0x12345678)
    sample = struct.pack("<HHHHHHH2x", ts & 0xFFFF, state, x, y, pressure, altitude, azimuth)
    return rframe(0x60, rep + sample)


def hid_frame(ftype, payload):
    """hid::Frame {u32 size (incl. this 7 byte header), u8, u8 type, u8} + payload."""
    return struct.pack("<IBBB", 7 + len(payload), 0, ftype, 0) + payload


def legacy_payload(counter, touch=None, stylus=None):
    groups = []
    if stylus is not None:
        groups.append((6, stylus))
    if touch is not None:
        groups.append((8, touch))
    out = struct.pack("<II4x", counter, len(groups))
    for i, (gtype, data) in enumerate(groups):
        out += struct.pack("<HHI8x", i, gtype, len(data)) + data
    return out


def input_report(counter, touch=None, stylus=None, eds2=False, size=None):
    """One input report (what a hidraw read returns), zero padded to size like the kernel's fixed-size report."""
    if eds2:
        frame = hid_frame(0xFF, (touch or b"") + (stylus or b""))
    else:
        frame = hid_frame(0xEE, legacy_payload(counter, touch, stylus))
    rep = struct.pack("<BH", DATA_ID, counter & 0xFFFF) + frame
    if size is not None:
        assert len(rep) <= size, (len(rep), size)
        rep += b"\0" * (size - len(rep))
    return rep


def metadata_feature(width_cm=28.53, height_cm=19.02, invert_x=False, invert_y=True, rows=ROWS, cols=COLS):
    """Feature report body after the report id: hid::Frame{type=2} + metadata::Frame."""
    dims = struct.pack("<IIII", rows, cols, int(round(width_cm * 1000)), int(round(height_cm * 1000)))
    xx = -1.0 if invert_x else 1.0
    yy = -1.0 if invert_y else 1.0
    transform = struct.pack("<6f", xx, 0, 0, 0, yy, 0)
    meta = dims + b"\x01" + transform + b"\0" * 64
    assert len(meta) == 105
    return hid_frame(0x02, meta)


def scenario(n=60):
    """(touch_blobs, stylus) per frame: a finger swiping left->right, then two fingers, then a pen stroke."""
    frames = []
    for i in range(n):
        frames.append(([(0.15 + 0.7 * i / (n - 1), 0.30, 200, 1.3)], None))
    for i in range(n // 2):
        frames.append(([(0.25, 0.60 + 0.1 * i / n, 200, 1.3), (0.75, 0.60 + 0.1 * i / n, 200, 1.3)], None))
    frames.append(([], None))
    for i in range(n):
        p = 0 if i < 5 else 2000
        frames.append(([], (int(MPP_MAX_X * (0.2 + 0.6 * i / (n - 1))), int(MPP_MAX_Y * 0.5), p)))
    frames.append(([], (0, 0, -1)))  # pen leaves proximity
    return frames


def build_reports(eds2=False, size=None, n=60):
    reps = []
    for k, (blobs, pen) in enumerate(scenario(n)):
        touch = touch_frames(heatmap(blobs))
        stylus = None
        if pen is not None:
            x, y, p = pen
            stylus = stylus_frame(x, y, max(p, 0), proximity=p >= 0, ts=k)
            touch = touch_frames(heatmap([]))
        reps.append(input_report(k, touch, stylus, eds2=eds2, size=size))
    return reps


def write_dump(path, desc, reports, vendor=0x045E, product=0x09B2, feature=None):
    """iptsd-dump format (core/linux/device/{capture,file}.hpp)."""
    with open(path, "wb") as f:
        f.write(struct.pack("<Ihh", 0, vendor - 0x10000 if vendor > 0x7FFF else vendor,
                            product - 0x10000 if product > 0x7FFF else product))
        f.write(struct.pack("<I", len(desc)) + desc)
        if feature is not None:              # Device::metadata() get_feature at startup
            f.write(struct.pack("<Q", len(feature)) + feature)
        for r in reports:
            f.write(struct.pack("<Q", len(r)) + r)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--desc-h", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--eds2", action="store_true")
    a = ap.parse_args()
    d = descriptor(a.desc_h, eds2=a.eds2)
    _, fb = kernel_descriptors(a.desc_h)
    # what hidraw returns into iptsd's buffer: report.bytes() = scan time + gesture data (the kernel sends one more
    # byte, the report id prefix, and hidraw truncates the read to the buffer size)
    size = 2 + data_report_count(fb)
    feat = bytes([META_ID]) + metadata_feature() if a.eds2 else None
    if feat is not None:
        feat += b"\0" * (1 + 7 + 105 - len(feat))
    write_dump(a.out, d, build_reports(eds2=a.eds2, size=size), feature=feat)
    print(f"wrote {a.out}: descriptor {len(d)} bytes, report size {size}")
