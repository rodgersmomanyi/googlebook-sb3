"""RAM journal of the phase a destructive flow reached (4.0.14). Device side (files in /run/gb only)."""
import glob
import json
import os

from . import const
from . import log
from .util import utc_iso, utc_stamp

RECOVERY = {
    ("any", "planned"): "Nothing was written to the disk.",
    ("any", "offdisk_backup_written"): "Nothing was written to the disk (only the off-disk partition table copy).",
    ("wholedisk", "head_zeroed"): "The disk was partly overwritten and has no valid partition table yet: run option 1 "
                                  "on it again, or discard it.",
    ("wholedisk", "data_written"): "The disk was partly overwritten and has no valid partition table yet: run option 1 "
                                   "on it again, or discard it.",
    ("wholedisk", "gpt_written"): "The disk was written but not verified: run option 1 on it again, or discard it.",
    ("wholedisk", "verified"): "The disk is complete; only the final report is missing.",
    ("dualboot", "data_written"): "Only unallocated space was written; the partition table is unchanged; nothing to "
                                  "undo.",
    ("dualboot", "data_verified"): "Only unallocated space was written; the partition table is unchanged; nothing to "
                                   "undo.",
    ("dualboot", "gpt_backup_written"): "The backup GPT copy holds the new table, the primary the old one: restore "
                                        "both from the pre-install copy (on Windows: gbdisk restore-gpt --from <dir> "
                                        "--tag pre_install --apply, from the off-disk copy or C:\\GB-backup\\pre-iso-"
                                        "<date>; README_DUALBOOT, section Recovery).",
    ("dualboot", "gpt_primary_written"): "The new partition table is complete; the boot entry is missing: boot the "
                                         "installer again and use option 3 to remove it, or import on Windows and use "
                                         "install_dualboot.ps1 -RepairBootEntry.",
    ("dualboot", "efi_created"): "Partitions and boot entry exist (Windows stays first); the \\gbinstall\\ state is "
                                 "missing: option 3 removes everything.",
    ("dualboot", "efi_ordered"): "Partitions and boot entry exist (Windows stays first); the \\gbinstall\\ state is "
                                 "missing: option 3 removes everything.",
    ("dualboot", "state_written"): "The install is complete; only the final report is missing.",
    ("uninstall", "efi_removed"): "The boot entry is gone, the partitions are still there: run option 3 again.",
    ("uninstall", "gpt_backup_written"): "The two GPT copies differ: restore both from the pre_uninstall copy (the "
                                         "off-disk copy, or gbdisk restore-gpt --tag pre_uninstall).",
    ("uninstall", "gpt_primary_written"): "The partitions are removed; only the verification/report is missing.",
}


def recovery_text(flow, phase):
    return RECOVERY.get((flow, phase)) or RECOVERY.get(("any", phase)) or "See the installer log."


def _write(j):
    tmp = os.path.join(const.RUN, ".journal.tmp")
    with open(tmp, "w") as f:
        json.dump(j, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, const.JOURNAL)


class Journal:
    def __init__(self, flow, disk, diskseq, build_id):
        self.j = {"format": "gbsb3-journal/1", "flow": flow, "disk": disk, "diskseq": diskseq, "build_id": build_id,
                  "started": utc_iso(), "phases": [], "result": None, "acknowledged": False}
        self.open = False

    def phase(self, name):
        self.j["phases"].append({"phase": name, "time": utc_iso()})
        _write(self.j)
        self.open = True
        log.info("journal: %s %s -> %s" % (self.j["flow"], self.j["disk"], name))

    def result(self, text):
        if not self.open:
            return
        self.j["result"] = text
        _write(self.j)
        if not text.startswith("FAILED"):
            archive()


def load():
    try:
        with open(const.JOURNAL) as f:
            j = json.load(f)
        if j.get("format") != "gbsb3-journal/1":
            return None
        return j
    except (OSError, ValueError):
        return None


def is_open(j):
    return j is not None and not j.get("acknowledged") and (j.get("result") is None or
                                                             str(j["result"]).startswith("FAILED"))


def last_phase(j):
    ph = j.get("phases") or []
    return ph[-1]["phase"] if ph else "planned"


def archive():
    """Rename the current journal to journal-<UTC stamp>.json."""
    if not os.path.exists(const.JOURNAL):
        return
    dst = os.path.join(const.RUN, "journal-%s.json" % utc_stamp())
    k = 1
    while os.path.exists(dst):
        dst = os.path.join(const.RUN, "journal-%s-%d.json" % (utc_stamp(), k))
        k += 1
    os.replace(const.JOURNAL, dst)
    log.info("journal archived as %s" % os.path.basename(dst))


def acknowledge():
    j = load()
    if j is None:
        return
    j["acknowledged"] = True
    _write(j)
    archive()


def blocks(j, kname, diskseq):
    """R_INCOMPLETE_RUN: an open journal names this disk (same kname and diskseq)."""
    return is_open(j) and j.get("disk") == kname and j.get("diskseq") == diskseq


def all_files():
    return sorted(glob.glob(os.path.join(const.RUN, "journal*.json")))


# ------------------------------------------------------------------------------------------------ running flow flag
FLOW_FLAG = "/run/gb/flow.running"


def flow_flag_set():
    """Mark a destructive flow as running in this process (removed when it ends)."""
    try:
        with open(FLOW_FLAG, "w") as f:
            f.write("%d\n" % os.getpid())
    except OSError as e:
        log.warn("flow flag: %s" % e)


def flow_flag_clear():
    try:
        os.unlink(FLOW_FLAG)
    except OSError:
        pass


def flow_running():
    """True while a destructive flow runs: the flag file names a live installer UI process."""
    try:
        with open(FLOW_FLAG) as f:
            pid = int(f.read().strip())
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return b"gbinstall.py" in f.read()
    except (OSError, ValueError):
        return False
