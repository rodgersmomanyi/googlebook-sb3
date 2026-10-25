"""EFI variables read directly from efivarfs (4.0.12) and the read-write window of 4.2.10 / 4.3.5. Device side."""
import contextlib
import hashlib
import os

from . import const
from . import efi
from . import log
from . import run
from . import scan


def available():
    """EFI firmware and a readable efivarfs at /sys/firmware/efi/efivars."""
    if not os.path.isdir("/sys/firmware/efi"):
        return False
    if scan.mounted_at(const.EFIVARS) is None:
        return False
    try:
        os.listdir(const.EFIVARS)
    except OSError:
        return False
    return True


def read_raw(name):
    """Raw efivarfs bytes (4 attribute bytes + data) of a global variable, or None."""
    try:
        with open(os.path.join(const.EFIVARS, efi.var_name(name)), "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def boot_numbers():
    out = []
    try:
        names = os.listdir(const.EFIVARS)
    except OSError:
        return out
    for n in names:
        mo = efi.BOOT_VAR_RE.match(n)
        if mo:
            out.append(int(mo.group(1), 16))
    return sorted(set(out))


def boot_order():
    raw = read_raw("BootOrder")
    if raw is None:
        return None
    return efi.parse_u16_list(efi.split_var(raw)[1])


def boot_next():
    raw = read_raw("BootNext")
    if raw is None:
        return None
    lst = efi.parse_u16_list(efi.split_var(raw)[1])
    return lst[0] if lst else None


def boot_current():
    raw = read_raw("BootCurrent")
    if raw is None:
        return None
    lst = efi.parse_u16_list(efi.split_var(raw)[1])
    return lst[0] if lst else None


def load_option(num):
    raw = read_raw(efi.boot_name(num))
    if raw is None:
        return None, None
    try:
        return efi.parse_load_option(efi.split_var(raw)[1]), raw
    except ValueError as e:
        log.warn("%s: unparsable load option: %s" % (efi.boot_name(num), e))
        return None, raw


def snapshot():
    """Raw bytes of every Boot####, BootOrder and BootNext: dict name -> bytes."""
    snap = {}
    for n in boot_numbers():
        raw = read_raw(efi.boot_name(n))
        if raw is not None:
            snap[efi.boot_name(n)] = raw
    for nm in ("BootOrder", "BootNext"):
        raw = read_raw(nm)
        if raw is not None:
            snap[nm] = raw
    return snap


def snapshot_json(snap):
    """efivars_*.json document (4.5)."""
    entries = {}
    for k in sorted(snap):
        if k.startswith("Boot") and len(k) == 8 and k not in ("BootNext",):
            try:
                desc = efi.parse_load_option(efi.split_var(snap[k])[1])["description"]
            except ValueError:
                desc = None
            entries[k] = {"label": desc, "sha256": hashlib.sha256(snap[k]).hexdigest()}
    order = None
    if "BootOrder" in snap:
        order = efi.fmt_order(efi.parse_u16_list(efi.split_var(snap["BootOrder"])[1]))
    nxt = None
    if "BootNext" in snap:
        lst = efi.parse_u16_list(efi.split_var(snap["BootNext"])[1])
        nxt = efi.fmt_num(lst[0]) if lst else None
    cur = boot_current()
    return {"BootOrder": order, "BootNext": nxt, "BootCurrent": efi.fmt_num(cur) if cur is not None else None,
            "entries": entries}


@contextlib.contextmanager
def rw_window():
    """efivarfs read-write for one EFI step only; always remounted read-only afterwards (4.0.12)."""
    run.PERMITS.grant("efivars-rw")
    ok = run.efivars_remount_rw()
    run.PERMITS.revoke("efivars-rw")
    if not ok:
        raise OSError("efivarfs could not be remounted read-write")
    try:
        yield
    finally:
        if not run.efivars_remount_ro():
            log.error("efivarfs could not be remounted read-only")
            log.say("ERROR: the EFI variable file system could not be made read-only again (see the log).")
