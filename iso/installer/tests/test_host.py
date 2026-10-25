"""Host-side (Windows) unit tests of the installer's PURE modules (DESIGN.md 8.4) plus a cross-check against the
Windows dual-boot tools on synthetic image FILES (gpt_validate.py mkimage; gbdisk.exe --image only).

  python -I test_host.py [--work DIR] [--no-gbdisk]

Never touches a disk: gbdisk.exe is only given --image files below DIR. Prints PASS/FAIL lines and
'SUMMARY host: <n> passed, <m> failed'.
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
from gb import const, gpt, plan, run, selftest  # noqa: E402

DUALBOOT = r"C:\GB-build\native\dualboot"
GBDISK = os.path.join(DUALBOOT, "gbdisk.exe")
VALIDATE = os.path.join(DUALBOOT, "tools", "gpt_validate.py")
EXPORT_SMALL = r"D:\gb-test\export_small\manifest.json"
MiB = 1 << 20
RESULTS = []


def check(ok, msg):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + msg, flush=True)
    return ok


# ------------------------------------------------------------------------------------------------ pure tests
def test_selftest_parts():
    for name, fn in (("util", selftest.t_util), ("efi", selftest.t_efi), ("run_validation", selftest.t_run_validation),
                     ("fat_verdict", selftest.t_fat_verdict)):
        try:
            fn()
            check(True, "selftest part %s" % name)
        except Exception as e:
            check(False, "selftest part %s: %r" % (name, e))
    try:
        m = selftest.t_manifest()
        check(True, "selftest part manifest")
        selftest.t_dualboot(m)
        check(True, "selftest part dualboot (T3 geometry: start 3189 MiB, userdata 10,411 MiB)")
        selftest.t_wholedisk(m)
        check(True, "selftest part wholedisk (exact rebuild, grow to 67,104,767)")
    except Exception as e:
        import traceback
        traceback.print_exc()
        check(False, "selftest parts manifest/dualboot/wholedisk: %r" % e)


FORBIDDEN_FORMS = [
    [run.EFIBOOTMGR, "--create", "--disk", "/dev/nvme0n1", "--part", "6", "--label", "Googlebook OS", "--loader",
     "\\EFI\\BOOT\\BOOTX64.EFI"],
    [run.EFIBOOTMGR, "--bootnext", "0005"], [run.EFIBOOTMGR, "--delete-bootorder"], [run.EFIBOOTMGR, "--remove-dups"],
    [run.EFIBOOTMGR, "--active", "--bootnum", "0005"], [run.EFIBOOTMGR, "--inactive", "--bootnum", "0005"],
    [run.EFIBOOTMGR, "--bootorder"], [run.EFIBOOTMGR, "-o", "0000,0005"], [run.EFIBOOTMGR, "-b", "0000", "-B"],
    [run.SGDISK, "--zap-all", "/dev/nvme0n1"], [run.SGDISK, "--clear", "/dev/nvme0n1"],
    [run.SGDISK, "--mbrtogpt", "/dev/nvme0n1"], [run.SGDISK, "--move-second-header", "/dev/nvme0n1"],
    [run.SGDISK, "-Z", "/dev/nvme0n1"], [run.SGDISK, "-n", "1:0:0", "/dev/nvme0n1"],
    ["/usr/sbin/blkdiscard", "/dev/nvme0n1"], ["/sbin/wipefs", "-a", "/dev/nvme0n1"], ["/bin/dd", "if=/dev/zero"],
    ["/usr/sbin/mkfs.vfat", "/dev/sda1"], ["/usr/sbin/fdisk", "/dev/sda"], ["/usr/bin/ntfsfix", "/dev/sda1"],
    ["/usr/bin/ntfs-3g", "/dev/sda1", "/mnt"], ["/bin/busybox", "dd"], ["/bin/sh", "-c", "dd if=/dev/zero"],
    [run.MOUNT, "-t", "ntfs3", "-o", "rw", "/dev/nvme0n1p3", "/run/gb/probe/nvme0n1p3"],
    [run.MOUNT, "-o", "remount,rw", "/run/gb/payload"],
    [run.MOUNT, "/dev/sda1", "/run/gb/probe/sda1"],
    [run.UMOUNT, "-a"], [run.PARTX, "-a", "/dev/nvme0n1"], [run.FSCK_VFAT, "-w", "/dev/nvme0n1p6"],
    [run.LOSETUP, "/dev/loop0", "/run/gb/probe/sda1/x.iso"],
]


def test_forbidden_forms():
    bad = []
    for argv in FORBIDDEN_FORMS:
        ctx = selftest.FakeCtx()
        ctx.permits.grant("target", "/dev/nvme0n1")          # even with the target permit
        try:
            run.validate(argv, ctx)
            bad.append(argv)
        except run.ToolRefused:
            pass
    check(not bad, "run_tool refuses %d destructive/unlisted forms%s" % (len(FORBIDDEN_FORMS),
                                                                       (": accepted %r" % bad) if bad else ""))


def test_scan_rules():
    sys.path.insert(0, os.path.join(HERE))
    spec = importlib.util.spec_from_file_location("check_forbidden", os.path.join(HERE, "check_forbidden.py"))
    cf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cf)
    hits = cf.scan(os.path.join(HERE, ".."))
    check(not hits, "4.0.11 forbidden-token rules: installer tree clean%s" % ((": %s" % hits[:5]) if hits else ""))


# ------------------------------------------------------------------------------------------------ gbdisk cross-check
def sh(argv, ok=(0,)):
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode not in ok:
        raise RuntimeError("%s -> %d\n%s" % (" ".join(argv), p.returncode, out[-800:]))
    return p.returncode, out


class FileDisk:
    def __init__(self, path):
        self.path = path
        self.n = os.path.getsize(path) // 512

    def read(self, lba, count):
        with open(self.path, "rb") as f:
            f.seek(lba * 512)
            b = f.read(count * 512)
        if len(b) != count * 512:
            raise OSError("short read")
        return b

    def write(self, lba, data):
        with open(self.path, "r+b") as f:
            f.seek(lba * 512)
            f.write(data)


def db_parts_from_export(path):
    m = json.load(open(path))
    out = []
    for p in m["partitions"]:
        blank = p["file"] is None
        out.append(dict(name=p["name"], type=p["type"], guid=p["guid"], attrs=int(p["attributes"], 16),
                        size=p["size"], fill=bool(p["fill"]), blank=blank, object=None if blank else p["name"],
                        file=p["file"], file_size=p["file_size"], sha256=p["sha256"], min_size=p["min_size"]))
    return m, out


def make_t3(path):
    if os.path.exists(path):
        os.unlink(path)
    sh([sys.executable, "-I", "-B", VALIDATE, "mkimage", path, "--c-mib", "1024", "--d-mib", "26624"])
    sh([GBDISK, "resize-image", "--image", path, "--number", "4", "--length", "2147483648", "--apply"])


def test_gbdisk(work):
    if not os.path.exists(EXPORT_SMALL) or not os.path.exists(GBDISK):
        check(False, "gbdisk cross-check: %s or %s missing" % (EXPORT_SMALL, GBDISK))
        return
    a_img, b_img = os.path.join(work, "t3_gbdisk.img"), os.path.join(work, "t3_iso.img")
    make_t3(a_img)
    make_t3(b_img)
    da, db = FileDisk(a_img), FileDisk(b_img)
    check(da.read(0, 34) == db.read(0, 34) and da.read(da.n - 33, 33) == db.read(db.n - 33, 33),
          "two identical T3 images (mkimage + gbdisk resize-image)")
    pre_p, pre_b = db.read(0, 34), db.read(db.n - 33, 33)
    em, parts = db_parts_from_export(EXPORT_SMALL)
    ud_min = [p for p in parts if p["fill"]][0]["min_size"]

    # plan: gbdisk plan (read-only) vs the installer's port of PlanNew
    gplan = os.path.join(work, "plan_gbdisk.json")
    sh([GBDISK, "plan", "--manifest", EXPORT_SMALL, "--anchor", "4", "--reserve", "1073741824", "--allow-truncated",
        "--image", a_img, "--out", gplan])
    gp = json.load(open(gplan))
    g = gpt.read_gpt(db.read, db.n, 512)
    check(gpt.check_no_gpt(g) is None and not gpt.check_gpt_invalid(g) and not gpt.check_gpt_geometry(g),
          "T3 image passes the installer's no_gpt / gpt_invalid / gpt_geometry checks")
    hdr = g["primary"]["hdr"]
    regs = plan.dualboot_regions(g["primary"]["entries"], hdr["first"], hdr["last"], parts, const.RESERVE, 512, ud_min)
    check(len(regs) == 1 and regs[0]["anchor_ordinal"] == 4, "one candidate region after partition #4")
    rg = regs[0]
    pl = plan.dualboot_placement(rg, parts, rg["fill"])
    mine = plan.plan_json(hdr["disk"], db.n * 512, "nvme0n1", rg, g["primary"]["entries"], pl)
    diff = [k for k in gp if k not in ("target", "test_truncated") and gp[k] != mine.get(k)]
    check(not diff and list(gp) == list(mine), "plan.json == gbdisk plan (all keys but target/test_truncated; "
                                               "same key order)%s" % ((": differ in %s" % diff) if diff else ""))
    if diff:
        for k in diff:
            print("  gbdisk:", json.dumps(gp[k])[:300])
            print("  mine:  ", json.dumps(mine[k])[:300])
    check(rg["start"] == 3343908864, "region start = D: end = 3,343,908,864")

    # layout JSON vs gbdisk backup
    bdir = os.path.join(work, "backup_a")
    shutil.rmtree(bdir, ignore_errors=True)
    sh([GBDISK, "backup", "--image", a_img, "--out", bdir, "--tag", "pre_install"])
    gl = json.load(open(os.path.join(bdir, "layout_pre_install.json")))
    ml = plan.layout_json(hdr, g["primary"]["entries"], db.n * 512, "x", "x", "pre_install", pre_p, pre_b, db.n)
    diff = [k for k in gl if k not in ("target", "time") and gl[k] != ml.get(k)]
    check(not diff and sorted(gl) == sorted(ml), "layout_pre_install.json == gbdisk backup's layout%s"
          % ((": differ in %s" % diff) if diff else ""))
    with open(os.path.join(bdir, "gpt_pre_install_primary.bin"), "rb") as f:
        bp = f.read()
    with open(os.path.join(bdir, "gpt_pre_install_backup.bin"), "rb") as f:
        bb = f.read()
    check(bp == pre_p and bb == pre_b, "gpt_pre_install_*.bin == the installer's LBA 0..33 / last 33 LBAs")

    # the partition table write: gbdisk create on image A, the installer's GPT update on image B
    sh([GBDISK, "create", "--plan", gplan, "--image", a_img, "--apply"])
    h = gpt.highest_used_slot(g["primary"]["entries_raw"], hdr["n"])
    ents = gpt.put_entries(g["primary"]["entries_raw"], plan.new_entries_for_slots(pl, h + 1))
    upd = plan.gpt_area_update(pre_p, pre_b, hdr, g["backup"]["hdr"], ents, db.n)
    for key in ("backup_entries", "backup_hdr", "primary_entries", "primary_hdr"):
        db.write(*upd["pieces"][key])
    same = da.read(0, 34) == db.read(0, 34) and da.read(da.n - 33, 33) == db.read(db.n - 33, 33)
    check(same, "installer GPT update == gbdisk create, byte for byte (LBA 0..33 and the last 33 LBAs)")
    mplan = os.path.join(work, "plan_iso.json")
    with open(mplan, "w") as f:
        json.dump(mine, f, separators=(",", ":"))
    rc, out = sh([GBDISK, "verify", "--image", b_img, "--plan", mplan, "--layout-only"], ok=(0, 1, 2, 3, 4))
    check(rc == 0, "gbdisk verify --layout-only accepts the installer's plan.json on the installed image")
    spec = importlib.util.spec_from_file_location("gpt_validate", VALIDATE)
    gv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gv)
    with open(b_img, "rb") as f:
        info, probs = gv.read_gpt(f, os.path.getsize(b_img))
    check(info is not None and not probs and len(info["entries"]) == 23, "gpt_validate read_gpt: valid, 23 entries")
    g2 = gpt.read_gpt(db.read, db.n, 512)
    ords = gpt.ordinals(g2["primary"]["entries"])
    esp_slot = [e["slot"] for e in g2["primary"]["entries"] if e["name"] == "EFI-SYSTEM"][0]
    check(esp_slot + 1 == 6 and ords[esp_slot] == 6, "ESP: slot+1 = 6 = gbdisk ordinal (T3: anchor 4, ESP 6)")

    # removal: gbdisk remove on A, the installer's mode 3 on B -> both byte-identical to before the install
    sh([GBDISK, "remove", "--plan", gplan, "--protect", os.path.join(bdir, "layout_pre_install.json"), "--image",
        a_img, "--apply"])
    dbg = set(p["guid"] for p in parts)
    slots = [e["slot"] for e in g2["primary"]["entries"] if e["guid"] in dbg]
    ents3 = gpt.zero_slots(g2["primary"]["entries_raw"], slots)
    upd3 = plan.gpt_area_update(db.read(0, 34), db.read(db.n - 33, 33), g2["primary"]["hdr"], g2["backup"]["hdr"],
                                ents3, db.n)
    for key in ("backup_entries", "backup_hdr", "primary_entries", "primary_hdr"):
        db.write(*upd3["pieces"][key])
    check(db.read(0, 34) == pre_p and db.read(db.n - 33, 33) == pre_b,
          "installer removal: GPT byte-identical to before the install")
    check(da.read(0, 34) == db.read(0, 34) and da.read(da.n - 33, 33) == db.read(db.n - 33, 33),
          "installer removal == gbdisk remove, byte for byte")
    for p in (a_img, b_img):
        os.unlink(p)
    shutil.rmtree(bdir, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=os.path.join(tempfile.gettempdir(), "gbiso-installer-hosttest"))
    ap.add_argument("--no-gbdisk", action="store_true")
    a = ap.parse_args()
    test_selftest_parts()
    test_forbidden_forms()
    test_scan_rules()
    if not a.no_gbdisk:
        os.makedirs(a.work, exist_ok=True)
        try:
            test_gbdisk(a.work)
        except Exception as e:
            import traceback
            traceback.print_exc()
            check(False, "gbdisk cross-check crashed: %r" % e)
    fails = RESULTS.count(False)
    print("SUMMARY host: %d passed, %d failed" % (RESULTS.count(True), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
