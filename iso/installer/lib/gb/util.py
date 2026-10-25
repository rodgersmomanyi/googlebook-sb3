"""Small pure helpers (no device, mount, ioctl or process code)."""
import datetime
import hashlib
import json
import re
import uuid
import zlib


def align_up(v, a):
    return (v + a - 1) // a * a


def align_down(v, a):
    return v // a * a


def crc32(b):
    return zlib.crc32(b) & 0xFFFFFFFF


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def guid_le(b):
    """16 bytes in EFI/GPT mixed-endian order -> lower-case GUID string."""
    return str(uuid.UUID(bytes_le=bytes(b)))


def guid_bytes(s):
    return uuid.UUID(s).bytes_le


GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def is_guid(s):
    return isinstance(s, str) and bool(GUID_RE.match(s))


def hex_attr(v):
    return "0x%016x" % v


def parse_attr(s):
    if not isinstance(s, str) or not re.match(r"^0x[0-9a-fA-F]{16}$", s):
        raise ValueError("attribute string %r" % (s,))
    return int(s, 16)


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def utc_iso(t=None):
    t = t or utc_now()
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_stamp(t=None):
    t = t or utc_now()
    return t.strftime("%Y%m%d-%H%M%S")


def ascii_screen(s, limit=48):
    """Text safe for the plain console (no escapes, ASCII only)."""
    out = "".join(c if 32 <= ord(c) < 127 else "?" for c in str(s))
    return out[:limit]


def dumps_compact(obj):
    return json.dumps(obj, separators=(",", ":"))


def dumps_pretty(obj):
    return json.dumps(obj, indent=1)


def gib2(n):
    return "%.2f" % (n / float(1 << 30))


def mib(n):
    return "%d MiB" % (n // (1 << 20))


def fmt_bytes(n):
    return "%s GiB (%d bytes)" % (gib2(n), n)


def marker_value(v):
    """A marker value: no double quote inside; values with spaces in double quotes (5.2)."""
    s = "-" if v is None else str(v)
    s = "".join(c if 32 <= ord(c) < 127 and c != '"' else "?" for c in s)
    if s == "":
        s = "-"
    if " " in s:
        s = '"' + s + '"'
    return s


def sha256sums_text(items):
    """items: list of (name, bytes) -> sha256sum(1) text."""
    return "".join("%s  %s\n" % (sha256(data), name) for name, data in items)


def power_verdict(supplies):
    """4.0.15 (pure). supplies: [(name, type, online int|None, status str|None)] from /sys/class/power_supply.
    Returns (state, why): 'ac' when a Mains/USB supply is online, or, when no supply says it is offline and no
    battery discharges, at least one battery reports Charging, Full or Not charging (a running machine whose
    batteries do not discharge is on external power); 'battery' when a battery discharges or a Mains/USB supply
    reports online 0; else 'unknown'."""
    mains = [s for s in supplies if s[1] in ("Mains", "USB")]
    batts = [s for s in supplies if s[1] == "Battery"]
    on = [s[0] for s in mains if s[2] == 1]
    if on:
        return "ac", "%s online" % ",".join(on)
    st = [(b[0], (b[3] or "").strip()) for b in batts]
    desc = ",".join("%s=%s" % (n, x or "-") for n, x in st)
    if any(x == "Discharging" for _, x in st):
        return "battery", "battery discharging (%s)" % desc
    if any(s[2] == 0 for s in mains):
        return "battery", "%s offline" % ",".join(s[0] for s in mains if s[2] == 0)
    good = [x for _, x in st if x in ("Charging", "Full", "Not charging")]
    if good and all(x in ("Charging", "Full", "Not charging", "Unknown", "") for _, x in st):
        return "ac", "no charger reported, battery status %s (not discharging)" % desc
    if batts:
        return "unknown", "battery status %s, no charger reported" % desc
    return "unknown", "no charger or battery reported by the firmware"


# hiberfil.sys header signatures (4.2.2). Blocking: a hibernation image Windows may still resume from ('hibr'/'HIBR')
# or a restore in progress ('rstr'/'RSTR'). 'wake'/'WAKE' marks an image Windows has already resumed from: not
# hibernated (the userspace NTFS library, the reference for this check, refuses only hibr/HIBR).
HIBER_BLOCKING = (b"hibr", b"HIBR", b"rstr", b"RSTR")


def hiber_signature(head):
    """(blocking, printable signature) for the first bytes of hiberfil.sys (pure)."""
    sig = bytes(head[:4])
    if not sig:
        return False, "(empty)"
    text = "zeros" if not any(sig) else "".join(chr(c) if 32 <= c < 127 else "." for c in sig)
    return sig in HIBER_BLOCKING, text


def parse_input_devices(text):
    """/proc/bus/input/devices -> [(name, handlers)] from the N: and H: lines only (never U:, the unique id)."""
    out, name, hand = [], None, ""
    for ln in (text or "").splitlines() + [""]:
        if ln.startswith("N: Name="):
            name = ln[len("N: Name="):].strip().strip('"')
        elif ln.startswith("H: Handlers="):
            hand = ln[len("H: Handlers="):].strip()
        elif not ln.strip():
            if name is not None:
                out.append((name, hand))
            name, hand = None, ""
    return out


NOT_KEYBOARDS = ("button", "switch", "video bus", "pc speaker")


def keyboard_names(devices):
    """Names of the input devices that can type (handler kbd; power/sleep buttons, lid switches and the like
    excluded)."""
    return [n for n, h in devices if "kbd" in h.split() and not any(x in n.lower() for x in NOT_KEYBOARDS)]


def parse_sha256sums(text):
    out = {}
    for line in text.splitlines():
        line = line.rstrip("\r")
        if not line.strip():
            continue
        m = re.match(r"^([0-9a-fA-F]{64}) [ *](.+)$", line)
        if not m:
            raise ValueError("bad SHA256SUMS line")
        out[m.group(2)] = m.group(1).lower()
    return out
