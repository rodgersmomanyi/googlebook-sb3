"""EFI load options, device paths and BootOrder (pure: works on variable bytes; 4.2 "EFI load options")."""
import re
import struct

from . import const
from .util import guid_le, guid_bytes

BOOT_VAR_RE = re.compile(r"^Boot([0-9A-Fa-f]{4})-" + const.EFI_GLOBAL + r"$")


def var_name(name):
    return "%s-%s" % (name, const.EFI_GLOBAL)


def boot_name(num):
    return "Boot%04X" % num


def fmt_num(num):
    return "%04X" % num


def fmt_order(order):
    return ",".join(fmt_num(n) for n in order)


def parse_u16_list(data):
    if len(data) % 2:
        raise ValueError("odd BootOrder length")
    return list(struct.unpack("<%dH" % (len(data) // 2), data))


def split_var(raw):
    """efivarfs file bytes -> (attributes u32, data)."""
    if len(raw) < 4:
        raise ValueError("efivarfs file shorter than 4 bytes")
    return struct.unpack_from("<I", raw, 0)[0], bytes(raw[4:])


def _utf16z(b, off):
    """NUL-terminated UTF-16LE string at off -> (str, offset after the NUL)."""
    i = off
    while i + 1 < len(b):
        if b[i] == 0 and b[i + 1] == 0:
            return b[off:i].decode("utf-16-le", "replace"), i + 2
        i += 2
    raise ValueError("unterminated UTF-16 string")


def parse_device_path(fpl):
    """First device-path instance -> list of nodes dict(type, subtype, data). Stops at the first End node."""
    nodes = []
    off = 0
    while off + 4 <= len(fpl):
        t, st, ln = struct.unpack_from("<BBH", fpl, off)
        if ln < 4 or off + ln > len(fpl):
            raise ValueError("bad device path node length")
        if t == 0x7F:
            nodes.append(dict(type=t, subtype=st, data=b""))
            return nodes
        nodes.append(dict(type=t, subtype=st, data=bytes(fpl[off + 4:off + ln])))
        off += ln
    raise ValueError("device path without End node")


def parse_load_option(data):
    """EFI_LOAD_OPTION (variable data without the 4 attribute bytes) -> dict(attributes, description, nodes, hd,
    file_path, nodes_before_hd, end_ok)."""
    if len(data) < 6:
        raise ValueError("load option too short")
    attrs, fpl_len = struct.unpack_from("<IH", data, 0)
    desc, off = _utf16z(data, 6)
    fpl = data[off:off + fpl_len]
    if len(fpl) != fpl_len:
        raise ValueError("file path list truncated")
    nodes = parse_device_path(fpl)
    hd = None
    hd_index = None
    for i, n in enumerate(nodes):
        if n["type"] == 4 and n["subtype"] == 1:
            d = n["data"]
            if len(d) != 38:
                raise ValueError("HD node length")
            partno, start, size = struct.unpack_from("<IQQ", d, 0)
            sig = d[20:36]
            fmt, sigtype = d[36], d[37]
            hd = dict(partno=partno, start=start, size=size, signature=sig, mbr_type=fmt, sig_type=sigtype,
                      guid=guid_le(sig) if sigtype == 2 else None)
            hd_index = i
            break
    file_path = None
    end_ok = False
    if hd is not None:
        parts = []
        for n in nodes[hd_index + 1:]:
            if n["type"] == 4 and n["subtype"] == 4:
                s, _ = _utf16z(n["data"] + b"\0\0", 0)
                parts.append(s)
            elif n["type"] == 0x7F:
                end_ok = True
                break
            else:
                parts = None
                break
        if parts:
            file_path = "".join(parts)
    return dict(attributes=attrs, description=desc, nodes=nodes, hd=hd, file_path=file_path,
                nodes_before_hd=hd_index if hd_index is not None else 0, end_ok=end_ok,
                optional_data=bytes(data[off + fpl_len:]))


def path_equal_ci(found, want):
    """Case-insensitive path compare; a missing leading backslash is accepted, '/' is not."""
    if found is None:
        return False
    f = found if found.startswith("\\") else "\\" + found
    return f.lower() == want.lower()


def is_windows_boot_manager(opt, num, boot_order, esp_guids):
    """4.2: listed in BootOrder, active, HD GUID == an ESP of the disk, path \\EFI\\Microsoft\\Boot\\bootmgfw.efi."""
    hd = opt.get("hd")
    return (num in boot_order and bool(opt["attributes"] & const.LOAD_OPTION_ACTIVE) and hd is not None and
            hd["sig_type"] == 2 and hd["guid"] in esp_guids and path_equal_ci(opt["file_path"], const.WINDOWS_LOADER))


def is_ours(opt, esp_guid):
    hd = opt.get("hd")
    return opt["description"] == const.BOOT_LABEL and hd is not None and hd["sig_type"] == 2 and hd["guid"] == esp_guid


def check_new_entry(opt, partno, start, size, esp_guid):
    """4.2.10.4 verification of the created entry. Returns a list of problems."""
    p = []
    if opt["attributes"] != const.LOAD_OPTION_ACTIVE:
        p.append("attributes 0x%08x (expected ACTIVE only)" % opt["attributes"])
    if opt["description"] != const.BOOT_LABEL:
        p.append("description differs")
    hd = opt["hd"]
    if hd is None:
        return p + ["no HD() node"]
    if hd["partno"] != partno or hd["start"] != start or hd["size"] != size:
        p.append("HD(%d,%d,%d) != HD(%d,%d,%d)" % (hd["partno"], hd["start"], hd["size"], partno, start, size))
    if hd["guid"] != esp_guid or hd["mbr_type"] != 2 or hd["sig_type"] != 2:
        p.append("HD() signature/type differs")
    if not path_equal_ci(opt["file_path"], const.LOADER) or not opt["file_path"].startswith("\\"):
        p.append("file path %r" % (opt["file_path"],))
    if not opt["end_ok"]:
        p.append("no End node after the file path")
    return p


# ------------------------------------------------------------------------------------------------ builders (self-test)
def build_hd_node(partno, start, size, guid):
    d = struct.pack("<IQQ", partno, start, size) + guid_bytes(guid) + bytes([2, 2])
    return struct.pack("<BBH", 4, 1, 4 + len(d)) + d


def build_file_node(path):
    d = (path + "\0").encode("utf-16-le")
    return struct.pack("<BBH", 4, 4, 4 + len(d)) + d


END_NODE = struct.pack("<BBH", 0x7F, 0xFF, 4)


def build_load_option(desc, fpl, attrs=const.LOAD_OPTION_ACTIVE, optional=b""):
    return struct.pack("<IH", attrs, len(fpl)) + (desc + "\0").encode("utf-16-le") + fpl + optional
