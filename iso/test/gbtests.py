r"""T0..T8 of DESIGN.md 7.2. Each public function `t_<id>(ctx)` returns a list of common.Result.

Sessions: one VM session = write the vmx, pre-start guard, (fresh NVRAM), vmrun start, serial pipe, GRUB hotkey,
wait for the main menu, drive the menu, power off (tools poweroff; vmrun stop hard after 60 s or on any error).
Only the throwaway VM D:\GB-iso\test\vm\gbiso-test.vmx is ever started; all disks are files below D:\GB-iso\test\.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback

import common as C
import mkdisks
import validate_dualboot as VD
import validate_wholedisk as W
import vmtest

FLOW_T = {"wholedisk": 45 * 60, "dualboot": 45 * 60, "uninstall": 5 * 60, "verify": 30 * 60,
          "refusal": 10 * 60}
DISCOVER = {"s": "iso9660,marker,isofile", "d": "marker,isofile", "t": "marker,isofile"}
DB_CHECKS_BEFORE_WINDOWS_ENTRY = ["incomplete_run", "not_efi", "payload_disk", "readonly", "in_use", "sector_size",
                                  "no_gpt", "gpt_invalid", "gpt_geometry", "bitlocker", "hibernated",
                                  "already_installed", "boot_entry_exists", "table_full", "no_region"]
BUILDER_FILES = ["builder_media.sh", "builder_send.py", "check_esp_tree.sh", "t0_static.py"]
EFIVARS_RW = "mount -o remount,rw /sys/firmware/efi/efivars"
EFIVARS_RO = "mount -o remount,ro,nosuid,nodev,noexec /sys/firmware/efi/efivars"


class Ctx:
    """Run context: build identity, payload manifest, log directory, VM tools, control result."""

    def __init__(self, keep_disks=False):
        if not os.path.isfile(C.BUILD_RECORD):
            raise C.HarnessError("no build record %s (build the ISO first)" % C.BUILD_RECORD)
        self.record = json.load(open(C.BUILD_RECORD, encoding="utf-8"))
        self.build_id = self.record["build_id"]
        if not re.match(r"^\d{8}-\d{6}-[0-9a-f]{8}$", self.build_id):
            raise C.HarnessError("bad build id in the build record")
        self.manifest = W.load_manifest()
        self.run_dir = C.mkdir(os.path.join(C.LOGS, self.build_id))
        self.work = C.mkdir(os.path.join(C.WORK, self.build_id))
        self.vmrun = vmtest.Vmrun()
        self.session_no = 0
        self.control_ok = None
        self.keep_disks = keep_disks
        self.builder_ready = False
        self.iso_sha = None
        self.media_out = {}

    def transcript(self, label):
        self.session_no += 1
        return os.path.join(self.run_dir, "%02d-%s.serial.log" % (self.session_no, label))

    def builder(self):
        """Copy the builder-side scripts + gpt_validate.py + forbidden_scan.py; record the ISO hash there."""
        if self.builder_ready:
            return
        files = [os.path.join(C.HERE, f) for f in BUILDER_FILES] + [C.GPT_VALIDATE, C.FORBIDDEN_SCAN]
        C.builder_prepare(files)
        C.ssh("printf '%%s  googlebook-sb3-installer.iso\\n' %s > %s/iso.sha256"
              % (self.record["iso_sha256"], C.REMOTE_TEST))
        self.builder_ready = True


# ------------------------------------------------------------------------------------------------ preflight
def preflight(ctx, need_vm=True):
    """Checks shared by all tests (raises HarnessError)."""
    probs = []
    for p in (C.ISO, C.PAYLOAD_MANIFEST, C.EXPORT_MANIFEST, C.GPT_VALIDATE, C.GBDISK, C.VERIFY_IMAGE):
        if not os.path.isfile(p):
            probs.append("missing " + p)
    if need_vm and not os.path.isfile(C.VMRUN):
        probs.append("missing vmrun")
    if ctx.manifest.get("build_id") != ctx.build_id:
        probs.append("payload manifest build_id %s != build record %s" % (ctx.manifest.get("build_id"), ctx.build_id))
    if C.file_sha256(C.EXPORT_MANIFEST) != ctx.manifest["dual_boot"]["export_manifest_sha256"]:
        probs.append("C:\\GB-x86\\iso\\work\\export\\manifest.json differs from the ISO's dual-boot export")
    if C.free_bytes(C.TEST_ROOT) < 8 * C.GiB:
        probs.append("D: has less than 8 GiB free")
    if probs:
        raise C.HarnessError("; ".join(probs))
    if ctx.iso_sha is None:
        cache = os.path.join(ctx.run_dir, "iso_sha256.json")
        st = os.stat(C.ISO)
        key = [st.st_size, int(st.st_mtime)]
        try:
            c = json.load(open(cache))
            if c.get("key") == key:
                ctx.iso_sha = c["sha256"]
        except (OSError, ValueError):
            pass
        if ctx.iso_sha is None:
            C.log("hashing the ISO on Windows (once per build)")
            ctx.iso_sha = C.file_sha256(C.ISO)
            C.write_json(cache, {"key": key, "sha256": ctx.iso_sha})
        if ctx.iso_sha != ctx.record["iso_sha256"]:
            raise C.HarnessError("ISO sha256 %s != build record %s" % (ctx.iso_sha, ctx.record["iso_sha256"]))
    if need_vm:
        running = [p for p in ctx.vmrun.running() if C.is_below(p, C.TEST_ROOT)]
        if running:
            raise C.HarnessError("a test VM is already running: %s" % running)


def control_verify_image(ctx):
    """7.1 Control: verify_image.py on the default image once before T1."""
    if ctx.control_ok is None:
        p = subprocess.run([sys.executable, "-I", C.VERIFY_IMAGE], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=7200)
        out = p.stdout.decode(errors="replace")
        ctx.control_ok = p.returncode == 0 and "RESULT: OK" in out
        C.write_json(os.path.join(ctx.run_dir, "control_verify_image.json"),
                     {"exit": p.returncode, "ok": ctx.control_ok, "tail": out[-3000:]})
    return ctx.control_ok


# ------------------------------------------------------------------------------------------------ session
class Session:
    """One VM session. disks: dict slot -> vmdk path ('nvme0:0', 'sata0:1', 'sata0:2'); cd: True = the ISO link."""

    def __init__(self, ctx, label, res, disks, cd=True, fresh_nvram=True, hotkey="s", grub_timeout=60,
                 vmx_extra=(), grub_cmds=None, discover=None):
        self.ctx, self.label, self.res = ctx, label, res
        self.disks, self.cd, self.fresh, self.hotkey = disks, cd, fresh_nvram, hotkey
        self.grub_timeout = grub_timeout
        self.vmx_extra = tuple(vmx_extra)
        self.grub_cmds = grub_cmds            # GRUB command-line boot instead of a menu hotkey (TEST only)
        self.discover = discover or DISCOVER.get(hotkey)
        self.c = None
        self.boot_kv = self.payload_kv = None
        self.started = False

    def sizes(self):
        out = []
        for vmdk in self.disks.values():
            with open(vmdk, encoding="utf-8") as f:
                _, ext, _ = vmtest.parse_vmdk(f.read())
            out.append(sum(n for _, n, _, _, _ in ext) * C.SECTOR)
        return out

    def __enter__(self):
        cd = mkdisks.link_iso() if self.cd else None
        text = vmtest.make_vmx(nvme=self.disks.get("nvme0:0"), cd=cd, sata1=self.disks.get("sata0:1"),
                               sata2=self.disks.get("sata0:2"), sata3=self.disks.get("sata0:3"),
                               sata4=self.disks.get("sata0:4"), extra=self.vmx_extra)
        vmtest.write_vmx(text)
        probs = vmtest.guard_vmx(C.VMX)
        if probs:
            raise C.HarnessError("guard: " + "; ".join(probs))
        if self.fresh:
            C.remove_file(os.path.join(C.VM_DIR, C.NVRAM_NAME))
        tr = self.ctx.transcript(self.label)
        self.res.evidence.setdefault("transcripts", []).append(tr)
        self.ctx.vmrun.start(C.VMX)
        self.started = True
        try:
            t = vmtest.PipeTransport()
            self.c = vmtest.Console(t, tr, allowed_sizes=self.sizes(), on_kill=self._kill)
            self.boot()
        except BaseException:
            self.close(error=True)
            raise
        return self

    def _kill(self, why):
        self.res.fail(why)
        self.ctx.vmrun.stop_hard(C.VMX)

    def boot(self):
        c = self.c
        try:
            c.wait(lambda: ("Googlebook SB3 installer" in vmtest.strip_ansi(c.raw.decode("utf-8", "replace"))
                            or "automatically in" in vmtest.strip_ansi(c.raw.decode("utf-8", "replace"))),
                   self.grub_timeout, "the GRUB menu")
        except vmtest.Timeout:
            raise GrubNotSeen("GRUB menu not seen within %ds" % self.grub_timeout)
        if self.grub_cmds:
            self.grub_command_line(self.grub_cmds)
        else:
            c.send(self.hotkey)
        _, kv = c.wait_marker("BOOT", 180, mark=0)
        self.boot_kv = kv
        self.res.evidence.setdefault("boot", []).append(kv)
        self.res.check(kv.get("build") == self.ctx.build_id, "%s: @@GB BOOT build=%s" % (self.label, kv.get("build")))
        self.res.check(kv.get("ui") in ("ttyS0", "/dev/ttyS0"), "%s: UI on ttyS0 (%s)" % (self.label, kv.get("ui")))
        self.res.check(kv.get("discover") == self.discover, "%s: discover=%s" % (self.label, kv.get("discover")))
        c.expect_prompt("main", 180, mark=0)
        pl = c.since(0, "PAYLOAD")
        self.payload_kv = pl[0][1] if pl else None
        self.res.evidence.setdefault("payload", []).append(self.payload_kv)

    def grub_command_line(self, cmds, timeout=30):
        """TEST only: GRUB's command line (key c in the menu), one command per line, then 'boot'. The menu's
        variables (root, gb_args) are visible there."""
        c = self.c
        start = len(c.raw)
        c.send("c")
        c.wait_text("grub>", timeout, from_byte=start, ansi=True)
        for cmd in list(cmds) + ["boot"]:
            if "\n" in cmd or "\r" in cmd:
                raise C.HarnessError("GRUB command must be one line")
            start = len(c.raw)
            for ch in cmd:
                c.send(ch)
                time.sleep(0.01)
            c.send("\r")
            if cmd != "boot":
                c.wait_text("grub>", timeout, from_byte=start, ansi=True)
        self.res.evidence.setdefault("grub_cmds", []).append(list(cmds))

    # -- shell helpers
    def efivars(self):
        v, sums = self.c.efivars()
        return v

    def nvram_prepare(self):
        """7.2 NVRAM preparation: a fake 'Windows Boot Manager' entry for nvme0n1 partition 1, first in BootOrder."""
        c = self.c
        before = self.efivars()
        c.sh_ok("%s && efibootmgr --create-only --disk /dev/nvme0n1 --part 1 --label 'Windows Boot Manager' "
                "--loader '\\EFI\\Microsoft\\Boot\\bootmgfw.efi' >/dev/null; r=$?; %s; [ $r = 0 ]"
                % (EFIVARS_RW, EFIVARS_RO))
        mid = self.efivars()
        new = sorted(set(VD.numbered(mid)) - set(VD.numbered(before)))
        if len(new) != 1:
            raise C.HarnessError("NVRAM preparation: %d new Boot#### variables" % len(new))
        order = [new[0]] + [x for x in (VD.boot_order(mid.get("BootOrder")) or []) if x != new[0]]
        c.sh_ok("%s && efibootmgr --bootorder %s >/dev/null; r=$?; %s; [ $r = 0 ]"
                % (EFIVARS_RW, VD.fmt_order(order), EFIVARS_RO))
        after = self.efivars()
        if VD.boot_order(after.get("BootOrder")) != order:
            raise C.HarnessError("NVRAM preparation: BootOrder not set")
        self.res.evidence["nvram_preparation"] = {"windows_entry": "%04X" % new[0], "bootorder": VD.fmt_order(order)}
        return new[0], order, after

    def bootnext_cd(self):
        """7.1 BootNext-to-CD rule (after all EFI checks of the session)."""
        v = self.efivars()
        cds = VD.find_by_description(v, "SATA CDROM")
        if not cds:
            raise C.HarnessError("no Boot#### with 'SATA CDROM' in its description")
        self.c.sh_ok("%s && efibootmgr --bootnext %04X >/dev/null; r=$?; %s; [ $r = 0 ]"
                     % (EFIVARS_RW, cds[0], EFIVARS_RO))
        self.res.evidence.setdefault("bootnext_cd", []).append("%04X" % cds[0])

    def close(self, error=False):
        c = self.c
        if self.started and self.ctx.vmrun.is_running(C.VMX):
            graceful = False
            if c is not None and not error and not c.killed:
                try:
                    c.power_off()
                    graceful = True
                except Exception as e:
                    self.res.info("graceful power-off failed: %s" % e)
            if not graceful or not self.ctx.vmrun.wait_off(C.VMX, 60):
                self.res.info("%s: vmrun stop hard (%s)" % (self.label, self.ctx.vmrun.stop_hard(C.VMX)))
                self.ctx.vmrun.wait_off(C.VMX, 60)
        if c is not None:
            if c.failures:
                for f in c.failures:
                    self.res.fail("%s: %s" % (self.label, f))
            c.close()
        self.started = False

    def __exit__(self, et, ev, tb):
        self.close(error=et is not None)
        return False


class GrubNotSeen(C.HarnessError):
    pass


# ------------------------------------------------------------------------------------------------ flow helpers
def pick_un_disk(c, line_mark, kname=None, boot_entry_only=False):
    """Answer un.disk with the listed number of '<kname>: n/18 partitions' or 'boot entry only' (screen text)."""
    c.expect_prompt("un.disk", 300)
    want = None
    for ln in c.lines[line_mark:]:
        m = re.match(r"^\s*(\d+)\s+(\S+): (\d+)/18 partitions", ln)
        if m and not boot_entry_only and (kname is None or m.group(2) == kname):
            want = m.group(1)
        m = re.match(r"^\s*(\d+)\s+boot entry only", ln)
        if m and boot_entry_only:
            want = m.group(1)
    idx = c.expect_prompt("un.disk", 5)
    c.answered.add(idx)
    if want is None:
        c.reply("b")
        raise C.HarnessError("un.disk: no listed entry for %s" % ("boot entry only" if boot_entry_only else kname))
    m = c.mark()
    c.reply(want)
    return m


def checks_since(c, mark):
    return [(kv.get("name"), kv.get("result"), kv.get("code")) for t, kv in c.since(mark, "CHECK")]


def refusal_flow(s, res, mode, size, code, flags=(), expect_ok_checks=(), kname=None, tolerate=()):
    """mode '1'|'2'|'3': pick the disk, expect RESULT status=REFUSED code=<code>. tolerate: (prompt id, reply) pairs
    answered on the way (e.g. an acknowledgement prompt answered with a wrong reply)."""
    c = s.c
    flow = {"1": "wholedisk", "2": "dualboot", "3": "uninstall"}[mode]
    line_mark = len(c.lines)
    m = c.ask("main", mode)
    if mode == "3":
        m2 = pick_un_disk(c, line_mark, kname=kname)
    else:
        d, m2 = c.pick_disk({"1": "wd.disk", "2": "db.disk"}[mode], m, size=size, kname=kname)
        res.evidence.setdefault("disks", []).append(d)
        fl = d.get("flags", "-").split(",")
        for f in flags:
            res.check(f in fl, "disk listed with flag %s (flags=%s)" % (f, d.get("flags")))
    kv = c.wait_result(flow, FLOW_T["refusal"], m2, tolerate=tolerate)
    res.evidence.setdefault("results", []).append(kv)
    chk = checks_since(c, m2)
    res.evidence.setdefault("checks", []).append(chk)
    res.check(kv.get("status") == "REFUSED" and kv.get("code") == code,
              "@@GB RESULT flow=%s status=REFUSED code=%s (got %s %s)" % (flow, code, kv.get("status"),
                                                                        kv.get("code")))
    for name in expect_ok_checks:
        got = [r for n, r, _ in chk if n == name]
        res.check(got == ["ok"], "@@GB CHECK name=%s result=ok (%s)" % (name, got))
    return kv, chk


def disk_identity(raw):
    return {"sha256": C.sparse_sha256(raw), "size": os.path.getsize(raw)}


def check_byte_identical(res, raw, before, snapshot=None, label="disk"):
    after = disk_identity(raw)
    res.evidence.setdefault("identity", []).append({"before": before, "after": after, "file": raw})
    res.check(after == before, "%s byte-identical (sha256 %s)" % (label, after["sha256"][:16]))
    if snapshot:
        VD.gpt_validate_cli(res, "validate_clean", "validate", raw, "--snapshot", snapshot, "--expect", "clean")


def snapshot_if_gpt(ctx, raw, name):
    info, probs = mkdisks.read_gpt(raw)
    if info is None or probs:
        return None
    snap = os.path.join(ctx.work, name + ".pre.json")
    C.remove_file(snap)
    rc, out = mkdisks.gpt_validate("snapshot", raw, snap)
    return snap if rc == 0 else None


def run_test(tid, fn):
    """Run fn(res) with exception capture; returns the finished Result."""
    res = C.Result(tid)
    C.log("=== %s" % tid)
    try:
        fn(res)
    except mkdisks.Skipped as e:
        res.status, res.reason = "SKIPPED", str(e)
    except Exception as e:
        res.fail("%s: %s" % (type(e).__name__, e))
        res.evidence["traceback"] = traceback.format_exc()[-4000:]
    res.finish()
    C.log("=== %s %s %s" % (tid, res.status, res.reason))
    return res


def cleanup_disk(ctx, name):
    if not ctx.keep_disks:
        mkdisks.delete(name)


# ------------------------------------------------------------------------------------------------ T0
def t0(ctx):
    def body(res):
        ctx.builder()
        C.log("T0: builder-side static checks (reads and decompresses the whole ISO)")
        p = C.ssh("python3 -I %s/t0_static.py %s %s" % (C.REMOTE_TEST, C.REMOTE_ISO, ctx.build_id), timeout=7200,
                  check=False)
        out = p.stdout.decode(errors="replace")
        with open(os.path.join(ctx.run_dir, "t0_builder.log"), "w", encoding="utf-8") as f:
            f.write(out + "\n" + p.stderr.decode(errors="replace"))
        line = [ln for ln in out.splitlines() if ln.startswith("T0-RESULT ")]
        if not res.check(bool(line), "t0_static.py produced a result (exit %d)" % p.returncode):
            return
        r = json.loads(line[-1][len("T0-RESULT "):])
        for ok, text in r["checks"]:
            res.check(ok, text)
        facts = r["facts"]
        res.evidence["facts"] = {k: v for k, v in facts.items() if k not in ("xorriso_report",)}
        res.evidence["xorriso_report"] = facts.get("xorriso_report")
        res.check(facts.get("iso_sha256") == ctx.record["iso_sha256"], "builder ISO sha256 == build record")
        res.check(facts.get("manifest_sha256") == C.file_sha256(C.PAYLOAD_MANIFEST),
                  "ISO gbsb3/manifest.json == D:\\GB-iso\\googlebook-sb3-installer.manifest.json")
        prim, back = W.image_gpt()
        import hashlib
        res.check(facts.get("gpt_primary_sha256") == hashlib.sha256(prim).hexdigest(),
                  "gpt-primary.bin == USB image LBA 0..33")
        res.check(facts.get("gpt_backup_sha256") == hashlib.sha256(back).hexdigest(),
                  "gpt-backup.bin == USB image last 33 LBAs")
        info, probs = mkdisks.read_gpt(C.USB_IMG)
        res.check(info is not None and not probs, "USB image GPT valid")
        img = sorted(info["entries"], key=lambda e: e["slot"]) if info else []
        mp = ctx.manifest["whole_disk"]["partitions"]
        same = len(img) == len(mp) == 18 and all(
            (e["slot"], e["name"], e["type"], e["id"], e["attributes"], e["first"], e["last"]) ==
            (p["slot"], p["name"], p["type"], p["guid"], int(p["attributes"], 16), p["first_lba"], p["last_lba"])
            for e, p in zip(img, mp))
        res.check(same, "manifest whole_disk.partitions == the USB image GPT (names, types, GUIDs, attrs, LBAs)")
        exm = json.load(open(C.EXPORT_MANIFEST, encoding="utf-8"))
        res.check(facts.get("dualboot_manifest_sha256") == C.file_sha256(C.EXPORT_MANIFEST),
                  "gbsb3/dualboot/manifest.json == the export manifest (verbatim)")
        keys = ("name", "type", "guid", "attributes", "size", "fill", "min_size", "file", "file_size", "sha256")
        dbp = ctx.manifest["dual_boot"]["partitions"]
        res.check(len(dbp) == len(exm["partitions"]) and all(
            all(a.get(k) == b.get(k) for k in keys) for a, b in zip(dbp, exm["partitions"])),
            "manifest dual_boot.partitions == the export manifest")
        res.check(facts.get("efiboot_img_bytes") == ctx.record.get("efiboot_img_bytes"),
                  "efiboot.img bytes == build record (%s)" % ctx.record.get("efiboot_img_bytes"))
        res.check(facts.get("bootx64_sha256") == ctx.record.get("grub_efi_sha256"), "BOOTX64.EFI == build record")
        # WAVE3 item 1 (TA-7): the installer kernel needs no 8250.nr_uarts (records before wave 3 lack the key)
        ru, rec = facts.get("installer_8250_runtime_uarts"), ctx.record.get("kernel_8250_runtime_uarts")
        res.check(isinstance(ru, int) and ru >= 1 and rec in (None, ru),
                  "installer kernel CONFIG_SERIAL_8250_RUNTIME_UARTS %s >= 1, == build record (%s)" % (ru, rec))
        fs = subprocess.run([sys.executable, "-I", C.FORBIDDEN_SCAN, r"C:\GB-x86\iso\installer"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        res.check(fs.returncode == 0 and b"FORBIDDEN-SCAN OK" in fs.stdout, "forbidden_scan.py on installer\\: OK")
    return [run_test("T0", body)]


# ------------------------------------------------------------------------------------------------ T1
def t1(ctx, which=("T1a", "T1b")):
    out = []
    for tid in which:
        name = "t1d" if tid.startswith("T1d") else tid.lower()
        grow = tid in ("T1b", "T1d(y)")

        def body(res, tid=tid, name=name, grow=grow):
            preflight(ctx)
            ctrl = control_verify_image(ctx)
            res.evidence["control_verify_image_ok"] = ctrl
            mkdisks.delete(name)
            raw, vmdk = mkdisks.RECIPES[name]()
            size = os.path.getsize(raw)
            try:
                if name == "t1d":
                    res.evidence["pattern_spots"] = len(mkdisks.t1d_spots(size, ctx.manifest))
                    res.check(C.physical_size(raw) > 256 * C.MiB, "T1d disk holds pattern data before the run "
                              "(%d MiB allocated)" % (C.physical_size(raw) // C.MiB))
                with Session(ctx, tid, res, {"nvme0:0": vmdk}) as s:
                    c = s.c
                    m = c.ask("main", "1")
                    d, m2 = c.pick_disk("wd.disk", m, size=size)
                    res.check(d.get("kname") == "nvme0n1", "target listed as nvme0n1 (%s)" % d.get("kname"))
                    if tid == "T1a":
                        c.expect_prompt("wd.confirm", 600)          # UnexpectedPrompt if wd.grow appears
                    else:
                        c.ask("wd.grow", "y" if grow else "n", timeout=600)
                        c.expect_prompt("wd.confirm", 120)
                    plan = c.since(m2, "PLAN")
                    res.evidence["plan"] = plan
                    c.ask("wd.confirm", "ERASE %s" % d["kname"])
                    kv = c.wait_result("wholedisk", FLOW_T["wholedisk"], m2)
                    res.evidence["result"] = kv
                    res.check(kv.get("status") == "OK", "@@GB RESULT flow=wholedisk status=OK (%s %s)"
                              % (kv.get("status"), kv.get("code")))
                    ver = c.since(m2, "VERIFY")
                    res.check(len(ver) == 18 and all(v.get("result") == "ok" for _, v in ver),
                              "18 @@GB VERIFY result=ok (%d)" % len(ver))
                    chk = checks_since(c, m2)
                    res.evidence["checks"] = chk
                    res.check(("internal_not_blank", "ok", "-") in chk, "@@GB CHECK name=internal_not_blank ok")
                if tid == "T1a":
                    W.check_exact(raw, res, control_ok=ctrl)
                else:
                    W.check_grown(raw, res, grow=grow, control_ok=ctrl, man=ctx.manifest)
                if name == "t1d":
                    W.check_patterned(raw, res, grow, man=ctx.manifest)
            finally:
                cleanup_disk(ctx, name)
        out.append(run_test(tid, body))
    return out


# ------------------------------------------------------------------------------------------------ T2
T2_SPEC = {
    # id: (source, recipe/medium, code, flags, extra ok checks)
    "T2a": ("py", "t2a", "R_WINDOWS_DISK", ("windows",), ()),
    "T2b": ("builder", "t2b", "R_WINDOWS_DISK", (), ()),
    "T2c": ("py", "t2c", "R_BITLOCKER", ("bitlocker",), ()),
    "T2d": ("py", "t2d", "R_TOO_SMALL", (), ()),
    "T2g": ("builder", "t2g", "R_PROTECTED_MEDIA", ("winmedia",), ("windows_disk",)),
    "T2h": ("builder", "t2h", "R_PROTECTED_MEDIA", ("gbbackup",), ("windows_disk",)),
    "T2i": ("builder", "t2i", "R_PROTECTED_MEDIA", ("ventoy",), ("windows_disk",)),
    # a whole-device exFAT ('superfloppy', no partition table) holding a GB-backup copy (audit 2, S1)
    "T2j": ("builder", "t2j", "R_PROTECTED_MEDIA", ("gbbackup",), ("windows_disk",)),
}
BUILDER_PHYS = {"t2b": 1 * C.GiB, "t2g": 1 * C.GiB, "t2h": 1 * C.GiB, "t2i": 1 * C.GiB, "t3nc": 2 * C.GiB,
                "t4n": 2 * C.GiB, "t5": 9 * C.GiB, "t5e": 9 * C.GiB, "t6a": 9 * C.GiB, "t6b": 1 * C.GiB,
                "t6n": 9 * C.GiB, "t2j": 1 * C.GiB, "t4nb": 2 * C.GiB, "t6d": 64 * C.MiB, "t6g": 9 * C.GiB,
                "t9s": 1 * C.GiB, "t9l": 2 * C.GiB}


def builder_disk(ctx, medium, name=None):
    """Build a medium on the builder, stream it back sparse, write its descriptor."""
    name = name or medium
    raw, vmdk = mkdisks.paths(name)
    mkdisks.delete(name)
    mkdisks.space_check(BUILDER_PHYS[medium])
    ctx.builder()
    C.log("builder: building %s" % medium)
    p = C.ssh("bash %s/builder_media.sh %s" % (C.REMOTE_TEST, medium), timeout=3 * 3600, check=False)
    out = p.stdout.decode(errors="replace") + p.stderr.decode(errors="replace")
    if p.returncode or "MEDIUM OK %s" % medium not in out:
        C.ssh("rm -f %s/%s.img" % (C.REMOTE_TEST, medium), check=False)
        raise C.HarnessError("builder_media.sh %s failed: %s" % (medium, out[-2000:]))
    ctx.media_out[medium] = out
    C.log("builder: fetching %s" % medium)
    mkdisks.fetch_builder_file(medium + ".img", raw, BUILDER_PHYS[medium])
    try:
        verify_medium(medium, raw)
    except Exception:
        mkdisks.delete(name)
        raise
    mkdisks.write_vmdk(vmdk, raw)
    return raw, vmdk


# medium -> (GPT entry count | "mbr", {slot: (fs signature kind, type GUID or None)})
MEDIA_SHAPE = {
    "t2b": (1, {0: ("ntfs", C.TYPE_BASIC)}), "t2g": (1, {0: ("fat32", C.TYPE_BASIC)}),
    "t2h": (1, {0: ("exfat", C.TYPE_BASIC)}), "t2i": ("mbr", {0: ("exfat", 0x07), 1: ("fat16", 0x06)}),
    "t3nc": (5, {2: ("ntfs", C.TYPE_BASIC)}), "t4n": (23, {2: ("ntfs", C.TYPE_BASIC)}),
    "t5": (1, {0: ("fat32", C.TYPE_BASIC)}), "t5e": (1, {0: ("fat32", C.TYPE_ESP)}),
    "t6a": (1, {0: ("exfat", C.TYPE_BASIC)}), "t6b": (1, {0: ("exfat", C.TYPE_BASIC)}),
    "t6n": (1, {0: ("ntfs", C.TYPE_BASIC)}),
    "t2j": ("whole", {0: ("exfat", None)}), "t4nb": (23, {2: ("bitlocker", C.TYPE_BASIC)}),
    "t6d": (1, {0: ("fat16", C.TYPE_BASIC)}), "t6g": (2, {0: ("exfat", C.TYPE_BASIC), 1: ("fat16", C.TYPE_ESP)}),
    "t9s": (1, {0: ("fat32", C.TYPE_BASIC)}), "t9l": (1, {0: ("fat32", C.TYPE_BASIC)}),
}
FS_SIG = {"ntfs": (3, b"NTFS    "), "exfat": (3, b"EXFAT   "), "fat32": (82, b"FAT32   "), "fat16": (54, b"FAT16   "),
          "bitlocker": (3, b"-FVE-FS-")}


def verify_medium(medium, raw):
    """A fetched builder medium has the expected partition table and file-system signatures (never trust an empty or
    half-made medium)."""
    count, parts = MEDIA_SHAPE[medium]
    if count == "whole":
        off, sig = FS_SIG[parts[0][0]]
        if C.read_at(raw, off, len(sig)) != sig or C.read_at(raw, 512 + 0, 8) == b"EFI PART":
            raise C.HarnessError("%s: no %s file system on the whole device" % (medium, parts[0][0]))
        return
    if count == "mbr":
        mbr = C.read_at(raw, 0, 512)
        if mbr[510:512] != b"\x55\xAA":
            raise C.HarnessError("%s: no MBR signature" % medium)
        ents = [mbr[446 + 16 * i:462 + 16 * i] for i in range(4)]
        for slot, (fs, ptype) in parts.items():
            e = ents[slot]
            if e[4] != ptype:
                raise C.HarnessError("%s: MBR entry %d type 0x%02x != 0x%02x" % (medium, slot + 1, e[4], ptype))
            start = int.from_bytes(e[8:12], "little")
            off, sig = FS_SIG[fs]
            if C.read_at(raw, start * C.SECTOR + off, len(sig)) != sig:
                raise C.HarnessError("%s: partition %d is not %s" % (medium, slot + 1, fs))
        return
    info, probs = mkdisks.read_gpt(raw)
    if info is None or probs:
        raise C.HarnessError("%s: GPT problems %s" % (medium, probs))
    ents = {e["slot"]: e for e in info["entries"]}
    if len(ents) != count:
        raise C.HarnessError("%s: %d GPT entries, expected %d" % (medium, len(ents), count))
    for slot, (fs, ptype) in parts.items():
        e = ents.get(slot)
        if e is None or (ptype and e["type"] != ptype):
            raise C.HarnessError("%s: slot %d missing or wrong type" % (medium, slot))
        if count == 1 and e["first"] != 2048:
            raise C.HarnessError("%s: partition 1 does not start at LBA 2048" % medium)
        off, sig = FS_SIG[fs]
        if C.read_at(raw, e["offset"] + off, len(sig)) != sig:
            raise C.HarnessError("%s: slot %d is not %s" % (medium, slot, fs))
    if medium in ("t4n", "t4nb"):
        exm = json.load(open(C.EXPORT_MANIFEST, encoding="utf-8"))
        if ents[3]["length"] != 2 * C.GiB:
            raise C.HarnessError("t4n: D: is not 2 GiB")
        for i, p in enumerate(exm["partitions"]):
            e = ents[5 + i]
            if (e["name"], e["type"], e["id"], e["attributes"], e["length"]) != (
                    p["name"], p["type"], p["guid"], int(p["attributes"], 16), C.MiB):
                raise C.HarnessError("t4n: slot %d does not match the export entry %s" % (5 + i, p["name"]))
            if e["offset"] != ents[3]["offset"] + ents[3]["length"] + i * C.MiB:
                raise C.HarnessError("t4n: %s not contiguous after D:" % p["name"])


def t2(ctx, which=("T2a", "T2b", "T2c", "T2d", "T2f", "T2g", "T2h", "T2i", "T2j")):
    out = []
    for tid in which:
        def body(res, tid=tid):
            preflight(ctx)
            if tid == "T2f":
                name = "t2f"
                mkdisks.delete(name)
                raw, vmdk = mkdisks.RECIPES[name]()
            else:
                src, name, code, flags, okc = T2_SPEC[tid]
                mkdisks.delete(name)
                raw, vmdk = mkdisks.RECIPES[name]() if src == "py" else builder_disk(ctx, name)
            try:
                before = disk_identity(raw)
                snap = snapshot_if_gpt(ctx, raw, name)
                size = before["size"]
                with Session(ctx, tid, res, {"nvme0:0": vmdk}) as s:
                    c = s.c
                    # EFI variables before/after the refused (or cancelled) run, in the same session (4.0.9)
                    c.shell_enter()
                    v1 = s.efivars()
                    c.shell_exit()
                    if tid == "T2f":
                        m = c.ask("main", "1")
                        d, m2 = c.pick_disk("wd.disk", m, size=size)
                        c.ask("wd.grow", "n", timeout=600)
                        c.ask("wd.confirm", "ERASE sdz")
                        kv = c.wait_result("wholedisk", FLOW_T["refusal"], m2)
                        res.evidence["result"] = kv
                        res.check(kv.get("status") == "CANCELLED", "@@GB RESULT status=CANCELLED (%s)"
                                  % kv.get("status"))
                    else:
                        refusal_flow(s, res, "1", size, code, flags, okc)
                    c.shell_enter()
                    v2 = s.efivars()
                    c.shell_exit()
                    VD.check_efi_unchanged(res, v1, v2)
                check_byte_identical(res, raw, before, snap)
                if tid == "T2f":
                    with open(raw, "rb") as f:
                        nonzero = [r for r in C.allocated_ranges(raw) if not W.zero_range(f, r[0], r[1])]
                    res.check(not nonzero, "disk still all zero (allocated ranges: %s)" % C.allocated_ranges(raw)[:4])
            finally:
                cleanup_disk(ctx, name)
        out.append(run_test(tid, body))
    return out


# ------------------------------------------------------------------------------------------------ T3 / T4 groups
def pick_listed_partition(c, pid, line_mark, disk_kname, timeout=300):
    """Answer a savelog.part / gptbackup.part prompt with the listed number of a partition on disk_kname (screen
    lines '  <n>  <part> on <disk> (...)'). Returns (marker mark, partition kname)."""
    idx = c.expect_prompt(pid, timeout)
    c.answered.add(idx)
    want = None
    for ln in c.lines[line_mark:]:
        mo = re.match(r"^\s*(\d+)\s+(\S+) on (\S+) \(", ln)
        if mo and mo.group(3) == disk_kname:
            want = (mo.group(1), mo.group(2))
    if want is None:
        c.reply("b" if pid == "savelog.part" else "s")
        raise C.HarnessError("%s: no listed partition on %s" % (pid, disk_kname))
    m = c.mark()
    c.reply(want[0])
    return m, want[1]


def install_flow(s, res, size, prefix="", gptbackup_size=None, res_backup=None):
    """Mode 2 on the disk of `size`: single region, userdata = fill, INSTALL nvme0n1. Returns (result kv, mark).
    gptbackup_size: answer the gptbackup.part prompt with the partition on the listed disk of that size (checks go
    to res_backup, which also gets evidence 'stick_kname')."""
    c = s.c
    m = c.ask("main", "2")
    d, m2 = c.pick_disk("db.disk", m, size=size)
    lm = len(c.lines)
    c.ask("db.userdata", "", timeout=600)                    # UnexpectedPrompt if db.region appears
    gptbackup_disk = None
    if gptbackup_size:
        ks = [kv_.get("kname") for kv_ in c.disks_since(m) if kv_.get("size") == str(gptbackup_size)]
        if len(ks) != 1:
            raise C.HarnessError("the test stick (size %d) is listed %d times" % (gptbackup_size, len(ks)))
        gptbackup_disk = ks[0]
        res_backup.evidence["stick_kname"] = gptbackup_disk
        _, part = pick_listed_partition(c, "gptbackup.part", lm, gptbackup_disk)
        res_backup.evidence["gptbackup_part"] = part
    c.ask("db.confirm", "INSTALL %s" % d["kname"], timeout=120)
    kv = c.wait_result("dualboot", FLOW_T["dualboot"], m2)
    res.evidence[prefix + "result"] = kv
    res.check(kv.get("status") == "OK", "%s1: @@GB RESULT flow=dualboot status=OK (%s %s)"
              % (prefix, kv.get("status"), kv.get("code")))
    ver = c.since(m2, "VERIFY")
    # one VERIFY per written partition: all 18 (DESIGN T3 said 17; the integrator settled it at 18, like mode 1)
    names = sorted(v.get("part") for _, v in ver)
    want = sorted(p["name"] for p in s.ctx.manifest["dual_boot"]["partitions"])
    res.check(len(ver) == 18 and names == want and all(v.get("result") == "ok" for _, v in ver),
              "%s1: 18 @@GB VERIFY result=ok, one per partition (%d)" % (prefix, len(ver)))
    chk = checks_since(c, m2)
    res.evidence[prefix + "checks"] = chk
    for n in ("no_windows_entry", "windows_dynamic", "no_windows", "table_full"):
        res.check([r for nn, r, _ in chk if nn == n] == ["ok"], "%s1: CHECK %s ok" % (prefix, n))
    res.evidence[prefix + "plan_marker"] = c.since(m2, "PLAN")
    if gptbackup_disk:
        gb = c.since(m2, "GPTBACKUP")
        res_backup.evidence["gptbackup_marker"] = gb
        res_backup.check(len(gb) == 1 and gb[0][1].get("result") == "ok" and
                         gb[0][1].get("part") == res_backup.evidence.get("gptbackup_part") and
                         (gb[0][1].get("dir") or "").startswith("/gbsb3-gptbackup/"),
                         "@@GB GPTBACKUP part=%s dir=/gbsb3-gptbackup/... result=ok (%s)"
                         % (res_backup.evidence.get("gptbackup_part"), gb))
    return kv, m2


def savelog_flow(s, res, logs_size, gptcopy_size=None):
    """Tools w onto the partition of the listed disk of logs_size; expects RESULT flow=savelog status=OK. The disk of
    gptcopy_size (it holds the off-disk GPT copy now) must be listed with flag gbbackup and not be offered. Returns
    the directory written (from the screen line 'written to <dir> on <part>'). Ends at the main menu."""
    c = s.c
    c.ask("main", "4")
    lm = len(c.lines)
    m = c.ask("tools", "w")
    c.expect_prompt("savelog.part", 300)
    listed = c.disks_since(m)
    logs = [d for d in listed if d.get("size") == str(logs_size)]
    if len(logs) != 1:
        raise C.HarnessError("the log stick (size %d) is listed %d times" % (logs_size, len(logs)))
    disk_kname = logs[0]["kname"]
    res.evidence["log_stick_kname"] = disk_kname
    if gptcopy_size:
        gc = [d for d in listed if d.get("size") == str(gptcopy_size)]
        offered = [ln for ln in c.lines[lm:]
                   if gc and re.match(r"^\s*\d+\s+\S+ on %s \(" % re.escape(gc[0]["kname"]), ln)]
        res.check(len(gc) == 1 and "gbbackup" in gc[0].get("flags", "").split(",") and not offered,
                  "the stick holding the GPT copy carries flag gbbackup and is not offered for the logs (%s)"
                  % (gc[0].get("flags") if gc else None))
    _, part = pick_listed_partition(c, "savelog.part", lm, disk_kname)
    kv = c.wait_result("savelog", FLOW_T["refusal"], m)
    res.evidence["savelog_result"] = kv
    res.check(kv.get("status") == "OK", "tools w: @@GB RESULT flow=savelog status=OK (%s %s)"
              % (kv.get("status"), kv.get("code")))
    path = None
    for ln in c.lines[lm:]:
        mo = re.match(r"^\s*written to (/gbsb3-logs/\S+) on (\S+)\s*$", ln)
        if mo:
            path = mo.group(1)
            res.check(mo.group(2) == part, "savelog wrote to the chosen partition %s (%s)" % (part, mo.group(2)))
    res.check(path is not None, "savelog reports its directory (%s)" % path)
    c.ask("tools", "b")
    return path


def t3_group(ctx, upto="G4"):
    """G1 (T3n(f), T3n(e)), G2 (T3, T3n(d)), G3 (T4), G4 (T4b) on one t3 disk."""
    order = ["G1", "G2", "G3", "G4"]
    groups = order[:order.index(upto) + 1]
    results = {}
    tw = C.mkdir(os.path.join(ctx.work, "t3"))
    st = {}

    def build(res):
        preflight(ctx)
        mkdisks.delete("t3")
        meta = mkdisks.t3(tw)
        st.update(meta)
        st["raw"], st["vmdk"] = mkdisks.paths("t3")
        st["size"] = os.path.getsize(st["raw"])
        res.evidence["t3_build"] = {k: v for k, v in meta.items() if k != "log"}
    rb = run_test("T3-build", build)
    if rb.status != "PASS":
        for t in ("T3n(f)", "T3n(e)", "T3", "T3n(d)", "T4", "T4b", "T9")[:{"G1": 2, "G2": 4, "G3": 5, "G4": 7}[upto]]:
            r = C.Result(t)
            r.status, r.reason = "FAIL", "T3 disk build failed: " + rb.reason
            results[t] = r.finish()
        return [rb] + list(results.values())
    raw, vmdk, size = st["raw"], st["vmdk"], st["size"]
    esp_guid = ctx.manifest["dual_boot"]["esp_guid"]

    # ---- G1
    rf, re_ = C.Result("T3n(f)"), C.Result("T3n(e)")
    try:
        C.log("=== G1: T3n(f), T3n(e)")
        preflight(ctx)
        with Session(ctx, "G1", rf, {"nvme0:0": vmdk}, fresh_nvram=True) as s:
            c = s.c
            c.shell_enter()
            v1 = s.efivars()
            c.shell_exit()
            kv, chk = refusal_flow(s, rf, "2", size, "R_NO_WINDOWS_ENTRY", (), DB_CHECKS_BEFORE_WINDOWS_ENTRY)
            c.shell_enter()
            v2 = s.efivars()
            VD.check_efi_unchanged(rf, v1, v2)
            wbm, order_prep, v3 = s.nvram_prepare()
            st["order_after_prep_g1"] = order_prep
            c.shell_exit()
            m = c.ask("main", "2")
            d, m2 = c.pick_disk("db.disk", m, size=size)
            c.ask("db.userdata", "", timeout=600)
            c.ask("db.confirm", "INSTALL sdz")
            kv = c.wait_result("dualboot", FLOW_T["refusal"], m2)
            re_.evidence["result"] = kv
            re_.check(kv.get("status") == "CANCELLED", "@@GB RESULT flow=dualboot status=CANCELLED (%s)"
                      % kv.get("status"))
            c.shell_enter()
            v4 = s.efivars()
            c.shell_exit()
            VD.check_efi_unchanged(re_, v3, v4, "EFI variables after the cancelled run (same as right after the "
                                                "NVRAM preparation)")
        sha = C.sparse_sha256(raw)
        for r in (rf, re_):
            r.check(sha == st["sha256_after_build"], "t3.img byte-identical to the state after the build steps")
        VD.gpt_validate_cli(rf, "validate_clean", "validate", raw, "--snapshot", st["pre"], "--expect", "clean")
    except Exception as e:
        for r in (rf, re_):
            r.fail("G1: %s: %s" % (type(e).__name__, e))
        rf.evidence["traceback"] = traceback.format_exc()[-4000:]
    results["T3n(f)"], results["T3n(e)"] = rf.finish(), re_.finish()
    if "G2" not in groups or rf.status != "PASS":
        return finish_group(ctx, rb, results, groups, "G1 did not pass")

    # ---- G2
    r3, rd = C.Result("T3"), C.Result("T3n(d)")
    gb_number = None
    try:
        C.log("=== G2: T3, T3n(d)")
        with Session(ctx, "G2", r3, {"nvme0:0": vmdk}, fresh_nvram=False) as s:
            c = s.c
            c.shell_enter()
            before = s.efivars()
            r3.evidence["efibootmgr_before"] = c.sh_ok("efibootmgr --verbose")
            st["g2_before_order"] = VD.boot_order(before.get("BootOrder"))
            c.shell_exit()
            kv, m2 = install_flow(s, r3, size)
            efi_markers = c.since(m2, "EFI")
            c.shell_enter()
            after = s.efivars()
            r3.evidence["efibootmgr_after"] = c.sh_ok("efibootmgr --verbose")
            state_dir = os.path.join(tw, "state")
            if os.path.isdir(state_dir):
                shutil.rmtree(state_dir)
            r3.evidence["state_files"] = c.pull_tree("/run/gb/state", state_dir)
            head1 = c.sh_ok("head -c 17408 /dev/nvme0n1 | sha256sum")
            c.shell_exit()
            # T3n(d)
            kvd, _ = refusal_flow(s, rd, "2", size, "R_ALREADY_INSTALLED")
            c.shell_enter()
            after_d = s.efivars()
            head2 = c.sh_ok("head -c 17408 /dev/nvme0n1 | sha256sum")
            VD.check_efi_unchanged(rd, after, after_d)
            rd.check(head1 == head2, "T3n(d): primary GPT area (LBA 0..33) unchanged by the refused run")
            s.bootnext_cd()
            c.shell_exit()
        esp_info = [p for p in ctx.manifest["dual_boot"]["partitions"] if p["name"] == "EFI-SYSTEM"][0]
        exp = json.load(open(st["expected_plan"], encoding="utf-8"))
        pesp = [p for p in exp["partitions"] if p["name"] == "EFI-SYSTEM"][0]
        gb_number = VD.check_efi_install(r3, before, after, esp_guid, pesp["offset"] // C.SECTOR,
                                         pesp["length"] // C.SECTOR, 6, efi_markers)
        st["gb_number"] = gb_number
        t3_host_checks(ctx, r3, st, tw, esp_info, gb_number)
        rd.info("T3n(d): the disk after the refused run passes the T3 host checks (see T3)")
        if r3.status is None and any(ok is False for ok, _ in r3.checks):
            rd.info("T3 host checks failed; T3n(d)'s disk evidence is the T3 result")
    except Exception as e:
        for r in (r3, rd):
            r.fail("G2: %s: %s" % (type(e).__name__, e))
        r3.evidence["traceback"] = traceback.format_exc()[-4000:]
    results["T3"], results["T3n(d)"] = r3.finish(), rd.finish()
    if "G3" not in groups or r3.status != "PASS":
        return finish_group(ctx, rb, results, groups, "G2 did not pass")

    # ---- G3
    r4 = C.Result("T4")
    try:
        C.log("=== G3: T4")
        with Session(ctx, "G3", r4, {"nvme0:0": vmdk}, fresh_nvram=False) as s:
            c = s.c
            c.shell_enter()
            before = s.efivars()
            c.shell_exit()
            lm = len(c.lines)
            c.ask("main", "3")
            m2 = pick_un_disk(c, lm, kname="nvme0n1")
            c.ask("un.confirm", "REMOVE nvme0n1", timeout=300)
            kv = c.wait_result("uninstall", FLOW_T["uninstall"], m2)
            r4.evidence["result"] = kv
            r4.check(kv.get("status") == "OK", "@@GB RESULT flow=uninstall status=OK (%s %s)"
                     % (kv.get("status"), kv.get("code")))
            un = c.since(m2, "UNINSTALL")
            r4.check(bool(un) and un[-1][1].get("gpt_matches_pre_install") == "yes",
                     "@@GB UNINSTALL gpt_matches_pre_install=yes (%s)" % (un[-1][1] if un else None))
            efi_markers = c.since(m2, "EFI")
            c.shell_enter()
            after = s.efivars()
            c.shell_exit()
        VD.gpt_validate_cli(r4, "validate_uninstalled", "validate", raw, "--snapshot", st["pre"], "--expect",
                            "uninstalled", "--plan", os.path.join(tw, "state", "plan.json"))
        VD.check_efi_uninstall(r4, before, after, st["gb_number"], st.get("g2_before_order"), efi_markers)
    except Exception as e:
        r4.fail("G3: %s: %s" % (type(e).__name__, e))
        r4.evidence["traceback"] = traceback.format_exc()[-4000:]
    results["T4"] = r4.finish()
    if "G4" not in groups:
        return finish_group(ctx, rb, results, groups, "")

    # ---- G4 (T4b; T9: the off-disk GPT copy and tools w on a test 'stick', import from the stick's copy)
    r4b, r9 = C.Result("T4b"), C.Result("T9")
    stick = None
    try:
        C.log("=== G4: T4b, T9")
        # the disk must be back in the pre-install state for a second install
        rc, out = mkdisks.gpt_validate("validate", raw, "--snapshot", st["pre"], "--expect", "uninstalled",
                                       "--plan", os.path.join(tw, "state", "plan.json"))
        if rc:
            raise C.HarnessError("t3.img is not in the uninstalled state before G4 (T4 failed?)")
        # T9 stick: 1 GiB FAT32 test disk on the persistent slot sata0:3. A VM has no USB, so the installer accepts
        # it as a savelog/gptbackup target only with gb.stick_any=1 (honoured only on VMware), typed on GRUB's
        # command line; every other part of the boot is the 's' entry's
        stick, stick_vmdk = builder_disk(ctx, "t9s")
        logs, logs_vmdk = builder_disk(ctx, "t9l")
        stick_size, logs_size = os.path.getsize(stick), os.path.getsize(logs)
        cmds = ["linux /boot/vmlinuz ${gb_args} console=ttyS0,115200n8 console=tty0 gb.serial=1 gb.stick_any=1",
                "initrd /boot/initrd.img"]
        with Session(ctx, "G4", r4b, {"nvme0:0": vmdk, "sata0:3": stick_vmdk, "sata0:4": logs_vmdk},
                     fresh_nvram=True, grub_cmds=cmds, discover=DISCOVER["s"]) as s:
            c = s.c
            c.shell_enter()
            wbm, order_prep, _ = s.nvram_prepare()
            c.shell_exit()
            kv, m2 = install_flow(s, r4b, size, prefix="", gptbackup_size=stick_size, res_backup=r9)
            r9.evidence["savelog_dir"] = savelog_flow(s, r9, logs_size, gptcopy_size=stick_size)
            c.shell_enter()
            gbi = os.path.join(C.HERE, "work", "t4b", "gbinstall")
            if os.path.isdir(gbi):
                shutil.rmtree(gbi)
            r4b.evidence["state_files"] = c.pull_tree("/run/gb/state", gbi)
            after_install = s.efivars()
            s.bootnext_cd()
            c.shell_exit()
        info_ok = VD.check_t3_disk(r4b, raw, st["pre"], st["expected_plan"], C.EXPORT_MANIFEST, ctx.manifest,
                                   os.path.join(gbi, "plan.json"))
        stick_gbi = t9_stick_checks(ctx, r9, stick, logs, gbi, r9.evidence.get("savelog_dir"), size)
        # T4b's Windows part runs on the gbinstall copy that tools w wrote to the stick (the README fallback path)
        windows_uninstall(ctx, r4b, raw, stick_gbi or gbi, st)
        r9.check(stick_gbi is not None and all(ok for ok, t in r4b.checks if ok is not None and "Import" in t),
                 "Import-GooglebookIsoInstall.ps1 -From <stick copy>\\gbsb3-logs\\...\\gbinstall: dry run and -Apply "
                 "exit 0 (see T4b)")
        with Session(ctx, "G4b", r4b, {"nvme0:0": vmdk}, fresh_nvram=False) as s:
            c = s.c
            lm = len(c.lines)
            c.ask("main", "3")
            m2 = pick_un_disk(c, lm, boot_entry_only=True)
            c.ask("un.confirm", "REMOVE efi", timeout=300)
            kv = c.wait_result("uninstall", FLOW_T["uninstall"], m2)
            r4b.check(kv.get("status") == "OK", "boot entry only: @@GB RESULT flow=uninstall status=OK (%s)"
                      % kv.get("status"))
            c.shell_enter()
            fin = s.efivars()
            c.shell_exit()
        r4b.check(VD.boot_order(fin.get("BootOrder")) == order_prep,
                  "BootOrder back to the order right after the NVRAM preparation (%s)" % VD.fmt_order(order_prep))
    except Exception as e:
        for r in (r4b, r9):
            r.fail("G4: %s: %s" % (type(e).__name__, e))
        r4b.evidence["traceback"] = traceback.format_exc()[-4000:]
    finally:
        if stick:
            cleanup_disk(ctx, "t9s")
            cleanup_disk(ctx, "t9l")
    results["T4b"], results["T9"] = r4b.finish(), r9.finish()
    return finish_group(ctx, rb, results, groups, "")


def copy_out_stick(ctx, res, raw, name, local):
    """The FAT partition of a T9 test stick -> builder (stream) -> mtools copy of /gbsb3-logs and /gbsb3-gptbackup
    (read-only) -> local folder. Returns True on success."""
    info, probs = mkdisks.read_gpt(raw)
    if info is None or probs or len(info["entries"]) != 1:
        res.fail("the stick's GPT is not readable after the session: %s" % probs)
        return False
    e = info["entries"][0]
    ctx.builder()
    stream_region_to_builder(raw, e["offset"], e["length"], "%s_p1.img" % name)
    p = C.ssh("bash %s/builder_media.sh fatextract %s_p1" % (C.REMOTE_TEST, name), check=False, timeout=600)
    res.check(p.returncode == 0 and b"FATEXTRACT OK" in p.stdout, "%s contents copied out (mtools, read-only)"
              % name)
    if os.path.isdir(local):
        shutil.rmtree(local)
    C.mkdir(local)
    import io
    import tarfile
    tp = C.ssh("tar -C %s/%s_p1_out -cf - ." % (C.REMOTE_TEST, name), check=False, timeout=600)
    C.ssh("rm -rf %s/%s_p1.img %s/%s_p1_out" % (C.REMOTE_TEST, name, C.REMOTE_TEST, name), check=False)
    if tp.returncode:
        res.fail("tar of the %s contents failed" % name)
        return False
    with tarfile.open(fileobj=io.BytesIO(tp.stdout)) as tf:
        tf.extractall(local, filter="data")
    return True


def t9_stick_checks(ctx, res, stick_raw, logs_raw, state_dir, savelog_dir, t3_size):
    """T9 host side: copy the sticks' /gbsb3-gptbackup (stick t9s) and /gbsb3-logs (stick t9l) out (builder, mtools,
    read-only), check both SHA256SUMS, the gbinstall copy == the RAM state pulled over the serial shell, the off-disk
    GPT copy == the pre-install GPT, and `gbdisk restore-gpt --from <the off-disk copy> --tag pre_install` on an
    image holding the post-install GPT. Returns the local path of the log stick's gbinstall copy (or None)."""
    base = os.path.join(ctx.work, "t9")
    lg, ll = os.path.join(base, "gptcopy_stick"), os.path.join(base, "log_stick")
    if not (copy_out_stick(ctx, res, stick_raw, "t9s", lg) and copy_out_stick(ctx, res, logs_raw, "t9l", ll)):
        return None
    res.check(not os.path.exists(os.path.join(lg, "gbsb3-logs")), "the GPT-copy stick got no logs")
    res.check(not os.path.exists(os.path.join(ll, "gbsb3-gptbackup")), "the log stick got no GPT copy")

    def sums_ok(d, label):
        sp = os.path.join(d, "SHA256SUMS")
        if not os.path.isfile(sp):
            res.check(False, "%s: SHA256SUMS present" % label)
            return {}
        sums = {}
        for ln in open(sp, encoding="ascii").read().splitlines():
            if ln.strip():
                h, _, n = ln.partition("  ")
                sums[n] = h
        files = sorted(os.path.relpath(os.path.join(dp, f), d).replace("\\", "/")
                       for dp, _, fs in os.walk(d) for f in fs if f != "SHA256SUMS" or dp != d)
        ok = sorted(sums) == files and all(C.file_sha256(os.path.join(d, n)) == h for n, h in sums.items())
        res.check(ok, "%s: SHA256SUMS lists exactly the %d files and verifies" % (label, len(files)))
        return sums
    # the off-disk GPT copy (gptbackup.part, 4.2.7)
    gbd = os.path.join(lg, "gbsb3-gptbackup")
    subs = sorted(os.listdir(gbd)) if os.path.isdir(gbd) else []
    res.check(len(subs) == 1 and subs[0].endswith("-nvme0n1"), "one /gbsb3-gptbackup/<UTC stamp>-nvme0n1 "
              "directory (%s)" % subs)
    marker_dir = ((res.evidence.get("gptbackup_marker") or [[None, {}]])[0][1] or {}).get("dir")
    if subs:
        gdir = os.path.join(gbd, subs[0])
        res.check(marker_dir == "/gbsb3-gptbackup/" + subs[0], "the GPTBACKUP marker names that directory")
        sums_ok(gdir, "gptbackup copy")
        res.check(sorted(os.listdir(gdir)) == ["SHA256SUMS", "gpt_pre_install_backup.bin",
                                               "gpt_pre_install_primary.bin", "layout_pre_install.json"],
                  "gptbackup copy holds the pre-install GPT files")
        for n in ("gpt_pre_install_primary.bin", "gpt_pre_install_backup.bin"):
            res.check(C.file_sha256(os.path.join(gdir, n)) == C.file_sha256(os.path.join(state_dir, n)),
                      "gptbackup %s == the install state's copy" % n)
        restore_gpt_check(ctx, res, gdir, state_dir, t3_size)
    # tools w (savelog, 4.4)
    lgd = os.path.join(ll, "gbsb3-logs")
    subs = sorted(os.listdir(lgd)) if os.path.isdir(lgd) else []
    res.check(len(subs) == 1 and subs[0].startswith(ctx.build_id + "-") and savelog_dir == "/gbsb3-logs/" + subs[0],
              "one /gbsb3-logs/<build>-<UTC stamp> directory, as reported (%s, %s)" % (subs, savelog_dir))
    if not subs:
        return None
    sdir = os.path.join(lgd, subs[0])
    sums = sums_ok(sdir, "savelog copy")
    have = set(sums)
    for need in ("log/installer.log", "log/init.log", "state/install_state.json", "gbinstall/install_state.json"):
        res.check(need in have, "savelog copy has %s" % need)
    res.check(any(n.startswith("journal-") and n.endswith(".json") for n in have), "savelog copy has the journal")
    res.check(any(n.startswith("backup/") and n.endswith("gpt_pre_install_primary.bin") for n in have),
              "savelog copy has the RAM pre-install GPT backup")
    stick_gbi = os.path.join(sdir, "gbinstall")
    names = sorted(os.listdir(stick_gbi)) if os.path.isdir(stick_gbi) else []
    res.check(names == C.GBINSTALL_FILES, "gbinstall copy (from the ESP) holds the %d \\gbinstall\\ files (%s)"
              % (len(C.GBINSTALL_FILES), names))
    same = names and all(C.file_sha256(os.path.join(stick_gbi, n)) == C.file_sha256(os.path.join(state_dir, n))
                         for n in names)
    res.check(bool(same), "gbinstall copy byte-identical to the install state pulled over the serial shell")
    return stick_gbi if names else None


def restore_gpt_check(ctx, res, gdir, state_dir, t3_size):
    """gbdisk restore-gpt --from <off-disk copy> --tag pre_install on a sparse image of the T3 disk's size holding
    only the post-install GPT (dry run unchanged, -Apply -> exactly the pre-install GPT areas, valid GPT)."""
    img = os.path.join(C.DISK_DIR, "t9r.img")
    C.remove_file(img)
    C.create_sparse(img, t3_size)
    try:
        post_p = open(os.path.join(state_dir, "gpt_post_install_primary.bin"), "rb").read()
        post_b = open(os.path.join(state_dir, "gpt_post_install_backup.bin"), "rb").read()
        with open(img, "r+b") as f:
            f.write(post_p)
            f.seek(t3_size - len(post_b))
            f.write(post_b)
        sha0 = C.sparse_sha256(img)
        rc, out = mkdisks.gbdisk("restore-gpt", "--image", img, "--from", gdir, "--tag", "pre_install")
        res.check(rc == 0 and "dry run" in out and C.sparse_sha256(img) == sha0,
                  "gbdisk restore-gpt (dry run) from the off-disk copy: exit 0, image unchanged (%d)" % rc)
        rc, out = mkdisks.gbdisk("restore-gpt", "--image", img, "--from", gdir, "--tag", "pre_install", "--apply")
        res.check(rc == 0 and "read-back verified" in out, "gbdisk restore-gpt --apply: exit 0, read-back verified")
        pre_p = open(os.path.join(gdir, "gpt_pre_install_primary.bin"), "rb").read()
        pre_b = open(os.path.join(gdir, "gpt_pre_install_backup.bin"), "rb").read()
        res.check(C.read_at(img, 0, len(pre_p)) == pre_p and C.read_at(img, t3_size - len(pre_b), len(pre_b)) == pre_b,
                  "the image's GPT areas are now the pre-install copy")
        info, probs = mkdisks.read_gpt(img)
        res.check(info is not None and not probs and len(info["entries"]) == 5,
                  "restored GPT valid with the 5 Windows partitions (%s)" % probs)
    finally:
        C.remove_file(img)


def finish_group(ctx, rb, results, groups, why):
    want = {"G1": ["T3n(f)", "T3n(e)"], "G2": ["T3", "T3n(d)"], "G3": ["T4"], "G4": ["T4b", "T9"]}
    for g in groups:
        for t in want[g]:
            if t not in results:
                r = C.Result(t)
                r.status, r.reason = "FAIL", "not run: " + why
                results[t] = r.finish()
    cleanup_disk(ctx, "t3")
    return [rb] + [results[t] for g in groups for t in want[g]]


def t3_host_checks(ctx, res, st, tw, esp_info, gb_number):
    """T3 criteria 2-7 and 9 after G2's power-off."""
    plan_esp_dir = os.path.join(tw, "esp_gbinstall")
    esp = VD.check_t3_disk(res, st["raw"], st["pre"], st["expected_plan"], C.EXPORT_MANIFEST, ctx.manifest,
                           os.path.join(tw, "state", "plan.json"))
    if esp is None:
        return
    # 6: ESP tree on the builder
    ctx.builder()
    stream_region_to_builder(st["raw"], esp["offset"], esp["length"], "t3_esp.img")
    stream_region_to_builder(os.path.join(C.EXPORT_DIR, "EFI-SYSTEM.img"), 0,
                             os.path.getsize(os.path.join(C.EXPORT_DIR, "EFI-SYSTEM.img")), "export_esp.img")
    p = C.ssh("bash %s/check_esp_tree.sh %s/t3_esp.img %s/export_esp.img %s" % (
        C.REMOTE_TEST, C.REMOTE_TEST, C.REMOTE_TEST, ctx.manifest["dual_boot"]["super_guid"]), check=False,
        timeout=1800)
    out = p.stdout.decode(errors="replace")
    res.evidence["check_esp_tree"] = out[-6000:]
    for ln in out.splitlines():
        if ln.startswith("INFO ") or ln.startswith("  fsck("):
            res.info("6: " + ln.strip())
        if ln.startswith("CHECK "):
            res.check(ln.startswith("CHECK ok "), "6: " + ln[len("CHECK ok "):] if ln.startswith("CHECK ok ")
                      else "6: " + ln[len("CHECK fail "):])
    res.check("ESP-TREE PASS" in out, "6: check_esp_tree.sh ESP-TREE PASS")
    if os.path.isdir(plan_esp_dir):
        shutil.rmtree(plan_esp_dir)
    C.mkdir(plan_esp_dir)
    import tarfile
    import io
    tp = C.ssh("tar -C %s/esp_gbinstall -cf - ." % C.REMOTE_TEST, check=False)
    if tp.returncode == 0:
        with tarfile.open(fileobj=io.BytesIO(tp.stdout)) as tf:
            tf.extractall(plan_esp_dir, filter="data")
        esp_plan = os.path.join(plan_esp_dir, "plan.json")
        pulled = os.path.join(tw, "state", "plan.json")
        res.check(os.path.isfile(esp_plan) and C.file_sha256(esp_plan) == C.file_sha256(pulled),
                  "4: plan.json on the ESP == /run/gb/state/plan.json")
    C.ssh("rm -rf %s/t3_esp.img %s/export_esp.img %s/esp_gbinstall" % ((C.REMOTE_TEST,) * 3), check=False)
    # 9
    VD.check_install_state(res, os.path.join(plan_esp_dir if os.path.isfile(os.path.join(
        plan_esp_dir, "install_state.json")) else os.path.join(tw, "state"), "install_state.json"),
        st["expected_plan"], C.EXPORT_MANIFEST, ctx.manifest, ctx.build_id, gb_number)


def stream_region_to_builder(path, off, length, remote_name):
    """Copy [off, off+length) of a local file to /var/tmp/gbiso/test/<remote_name> (cat over ssh)."""
    if "/" in remote_name:
        raise C.HarnessError("plain remote name only")
    p = subprocess.Popen(C.SSH_BASE + [C.BUILDER, "cat > %s/%s" % (C.REMOTE_TEST, remote_name)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with open(path, "rb") as f:
        f.seek(off)
        left = length
        while left:
            b = f.read(min(left, 8 * C.MiB))
            if not b:
                break
            p.stdin.write(b)
            left -= len(b)
    p.stdin.close()
    if p.wait(timeout=1800) or left:
        raise C.HarnessError("streaming %s to the builder failed" % path)


def ps(script, *args, timeout=7200, log_path=None):
    """powershell -NoProfile -ExecutionPolicy Bypass -File <script> <args>."""
    p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script] + list(args),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    out = p.stdout.decode(errors="replace")
    if log_path:
        C.assert_test_path(log_path)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(out)
    return p.returncode, out


def windows_uninstall(ctx, res, raw, gbi, st):
    """T4b Windows part: Import (dry run, -Apply), uninstall_dualboot.ps1 -TargetImage -Apply, validate."""
    imp = os.path.join(C.DUALBOOT_DIR, "Import-GooglebookIsoInstall.ps1")
    uni = os.path.join(C.DUALBOOT_DIR, "uninstall_dualboot.ps1")
    C.assert_test_path(raw)
    lg = C.mkdir(os.path.join(ctx.run_dir, "t4b"))
    sha0 = C.sparse_sha256(raw)
    rc, out = ps(imp, "-TargetImage", raw, "-From", gbi, log_path=os.path.join(lg, "import_dry.log"))
    res.check(rc == 0, "Import-GooglebookIsoInstall.ps1 dry run exit 0 (%d)" % rc)
    res.check(C.sparse_sha256(raw) == sha0, "dry run: image unchanged")
    rc, out = ps(imp, "-TargetImage", raw, "-From", gbi, "-Apply", log_path=os.path.join(lg, "import_apply.log"))
    res.check(rc == 0, "Import -Apply exit 0 (%d)" % rc)
    rc, out = ps(uni, "-TargetImage", raw, "-Apply", log_path=os.path.join(lg, "uninstall_apply.log"))
    res.check(rc == 0, "uninstall_dualboot.ps1 -TargetImage -Apply exit 0 (%d)" % rc)
    res.check("already has its original size" in out, "step 4 prints the guard A text")
    res.check("are not from" not in out, "no guard B failure")
    VD.gpt_validate_cli(res, "validate_uninstalled_windows", "validate", raw, "--snapshot", st["pre"], "--expect",
                        "uninstalled", "--plan", os.path.join(gbi, "plan.json"))


# ------------------------------------------------------------------------------------------------ T3n(a-c), T4n
def t3n_single(ctx, which=("T3n(a)", "T3n(b)", "T3n(c)", "T4n", "T4nb")):
    spec = {"T3n(a)": ("py", "t3na", "2", "R_NO_REGION", ()),
            "T3n(b)": ("py", "t3nb", "2", "R_BITLOCKER", ()),
            "T3n(c)": ("builder", "t3nc", "2", "R_HIBERNATED", ()),
            "T4n": ("builder", "t4n", "3", "R_HIBERNATED", ("mismatch", "foreign_between")),
            # BitLocker C: in mode 3: Fast Startup cannot be checked -> typed acknowledgement; a wrong reply refuses
            "T4nb": ("builder", "t4nb", "3", "R_NTFS_UNCHECKED", ("mismatch", "foreign_between"))}
    out = []
    for tid in which:
        def body(res, tid=tid):
            preflight(ctx)
            src, name, mode, code, okc = spec[tid]
            mkdisks.delete(name)
            if src == "py":
                raw, vmdk = mkdisks.RECIPES[name]()
            else:
                if name in ("t4n", "t4nb"):
                    write_t4n_parts(ctx)
                raw, vmdk = builder_disk(ctx, name)
            try:
                before = disk_identity(raw)
                snap = snapshot_if_gpt(ctx, raw, name)
                with Session(ctx, tid, res, {"nvme0:0": vmdk}) as s:
                    c = s.c
                    c.shell_enter()
                    v1 = s.efivars()
                    c.shell_exit()
                    mk = c.mark()
                    kv, chk = refusal_flow(s, res, mode, before["size"], code, (), okc,
                                           kname="nvme0n1" if mode == "3" else None,
                                           tolerate=(("hiber.ack", "no"),) if tid == "T4nb" else ())
                    if tid == "T4nb":
                        asked = [kv_.get("id") for _, kv_ in c.since(mk, "PROMPT")]
                        res.check("hiber.ack" in asked, "the hiber.ack prompt was asked (%s)" % asked)
                        res.check([r for n, r, _ in chk if n == "hibernated"] == ["warn"],
                                  "CHECK hibernated result=warn (acknowledgement required)")
                        res.check([r for n, r, _ in chk if n == "bitlocker"] == ["warn"], "CHECK bitlocker warn")
                    c.shell_enter()
                    v2 = s.efivars()
                    c.shell_exit()
                    VD.check_efi_unchanged(res, v1, v2)
                check_byte_identical(res, raw, before, snap)
            finally:
                cleanup_disk(ctx, name)
        out.append(run_test(tid, body))
    return out


def t4n_lines(export_manifest):
    """Lines of t4n_parts.txt: D: shrunk to 2 GiB (same start/type/GUID/name), then 18 x 1 MiB partitions after it
    in export placement order (names, types, GUIDs, attribute bit numbers), partition numbers 6..23."""
    gv = mkdisks.load_gpt_validate()
    d_name, d_type, d_guid, _ = gv.HOST[3]
    d_first = (1 + 100 + 16 + 1024) * C.MiB // C.SECTOR          # mkimage default geometry, C 1024 MiB
    d_last = d_first + 2 * C.GiB // C.SECTOR - 1
    lines = ["D 4 %d %d %s %s %s" % (d_first, d_last, d_type.upper(), d_guid, d_name)]
    first = d_last + 1
    for i, p in enumerate(export_manifest["partitions"]):
        a = int(p["attributes"], 16)
        bits = ",".join(str(b) for b in range(64) if a >> b & 1) or "-"
        if " " in p["name"]:
            raise C.HarnessError("partition name with a space: %r" % p["name"])
        lines.append("P %d %d %d %s %s %s %s" % (6 + i, first, first + 2047, p["type"].upper(), p["guid"],
                                                 p["name"], bits))
        first += 2048
    return lines


def write_t4n_parts(ctx):
    """t4n_parts.txt for builder_media.sh (t4n_lines), copied to the builder."""
    lines = t4n_lines(json.load(open(C.EXPORT_MANIFEST, encoding="utf-8")))
    path = os.path.join(ctx.work, "t4n_parts.txt")
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    ctx.builder()
    C.scp_to_builder(path, "t4n_parts.txt")
    return path


# ------------------------------------------------------------------------------------------------ T5 / T6 / T6n / T7
def verify_flow(s, res):
    c = s.c
    c.ask("main", "4")
    m = c.ask("tools", "v")
    kv = c.wait_result("verify", FLOW_T["verify"], m)
    res.evidence["verify"] = kv
    res.check(kv.get("status") == "OK", "tools v: @@GB RESULT flow=verify status=OK (%s %s)"
              % (kv.get("status"), kv.get("code")))
    c.ask("tools", "b")


def list_disks_flow(s, res):
    """'1' -> the wd.disk listing -> 'b'. Returns the DISK kvs."""
    c = s.c
    m = c.ask("main", "1")
    c.expect_prompt("wd.disk", 300)
    disks = c.disks_since(m)
    c.ask("wd.disk", "b")
    res.evidence["disks"] = disks
    return disks


def t5(ctx):
    def body(res):
        preflight(ctx)
        mkdisks.delete("blank1g")
        b_raw, b_vmdk = mkdisks.RECIPES["blank1g"]()
        used = None
        try:
            for medium in ("t5", "t5e"):
                raw, vmdk = builder_disk(ctx, medium)
                try:
                    with Session(ctx, "T5-" + medium, res, {"nvme0:0": b_vmdk, "sata0:1": vmdk}, cd=False) as s:
                        used = medium
                        pl = s.payload_kv or {}
                        res.check(pl.get("kind") == "marker" and pl.get("dev") == "/dev/sda1" and pl.get("disk") == "sda",
                                  "@@GB PAYLOAD kind=marker dev=/dev/sda1 disk=sda (%s)" % pl)
                        verify_flow(s, res)
                        disks = list_disks_flow(s, res)
                        sda = [d for d in disks if d.get("kname") == "sda"]
                        res.check(bool(sda) and "payload" in sda[0].get("flags", "").split(","),
                                  "in 1, sda carries flag payload (%s)" % (sda[0].get("flags") if sda else None))
                    break
                except GrubNotSeen as e:
                    res.info("%s: %s" % (medium, e))
                    if medium == "t5e":
                        raise
                finally:
                    cleanup_disk(ctx, medium)
            if used == "t5e":
                res.info("VMware needed EF00 (open issue for real Rufus sticks)")
                res.evidence["vmware_needed_ef00"] = True
        finally:
            cleanup_disk(ctx, "blank1g")
    return [run_test("T5", body)]


def t6(ctx, which=("T6", "T6m", "T6n")):
    out = []
    keep = {}                      # t6a is shared by T6 and T6m (both attach it non-persistent: it never changes)
    for tid in which:
        if tid == "T6m":
            out.append(run_test(tid, lambda res: t6m_body(ctx, res, keep)))
            continue

        def body(res, tid=tid):
            preflight(ctx)
            disks, names = {}, []
            try:
                if tid == "T6":
                    r1, v1 = builder_disk(ctx, "t6a")
                    if "T6m" in which:
                        keep["t6a"] = (r1, v1)
                    else:
                        names.append("t6a")
                    r2, v2 = builder_disk(ctx, "t6b")
                    names.append("t6b")
                    disks = {"sata0:1": v1, "sata0:2": v2}
                    hot = "d"          # GRUB reserves v (menu down) and ^: the nocd entry uses d
                else:
                    r1, v1 = builder_disk(ctx, "t6n")
                    names.append("t6n")
                    disks = {"sata0:1": v1}
                    hot = "t"
                with Session(ctx, tid, res, disks, cd=True, hotkey=hot) as s:
                    pl = s.payload_kv or {}
                    res.check(pl.get("kind") == "isofile" and pl.get("file") == "/ISO/googlebook-sb3-installer.iso",
                              "@@GB PAYLOAD kind=isofile file=/ISO/googlebook-sb3-installer.iso (%s)" % pl)
                    res.check(re.match(r"^/dev/loop\d+$", pl.get("dev", "")) is not None,
                              "payload dev is a loop device (%s)" % pl.get("dev"))
                    if tid == "T6":
                        res.check(pl.get("dev") == "/dev/loop0", "payload dev=/dev/loop0")
                    res.check(pl.get("dev") != "/dev/sr0" and pl.get("disk") != "sr0", "sr0 is not the payload")
                    verify_flow(s, res)
                    dl = list_disks_flow(s, res)
                    by_size = {int(d.get("size", 0)): d for d in dl}
                    k1 = by_size.get(os.path.getsize(r1), {}).get("kname")
                    res.check(pl.get("disk") == k1, "payload disk == disk (1) %s (%s)" % (k1, pl.get("disk")))
                    prot = (pl.get("protected") or "").split(",")
                    want = [k1, "sr0"]
                    if tid == "T6":
                        want.append(by_size.get(os.path.getsize(r2), {}).get("kname"))
                    res.check(all(k in prot for k in want), "protected=%s contains %s" % (prot, want))
                    for d in dl:
                        res.check("payload" in d.get("flags", "").split(","), "in 1, %s carries flag payload (%s)"
                                  % (d.get("kname"), d.get("flags")))
            finally:
                for n in names:
                    cleanup_disk(ctx, n)
        out.append(run_test(tid, body))
    if "t6a" in keep:
        cleanup_disk(ctx, "t6a")
    return out


def t6m_body(ctx, res, keep):
    """T6m: Ventoy normal mode's /dev/mapper/ventoy, reproduced: a read-only dm-linear map of the .iso file's disk
    extent over the whole Ventoy-equivalent disk (dmsetup from the Ubuntu archive, run from a separate test disk in
    the throwaway VM's tools shell). After tools r the payload is that map (kind=iso9660 dev=/dev/dm-0), the map
    holds the disk so its exFAT partition cannot be mounted (EBUSY), and the disk is protected."""
    preflight(ctx)
    names = []
    try:
        if "t6a" in keep:
            r1, v1 = keep["t6a"]
        else:
            r1, v1 = builder_disk(ctx, "t6a")
            names.append("t6a")
        mo = re.search(r"EXTENT lba=(\d+) sectors=(\d+)", ctx.media_out.get("t6a", ""))
        if not res.check(mo is not None, "t6a: the ISO file's disk extent is known (%s)" % (mo.group(0) if mo else
                                                                                           None)):
            return
        lba, sectors = int(mo.group(1)), int(mo.group(2))
        r3, v3 = builder_disk(ctx, "t6d")
        names.append("t6d")
        with Session(ctx, "T6m", res, {"sata0:1": v1, "sata0:2": v3}, cd=True, hotkey="s") as s:
            c = s.c
            pl = s.payload_kv or {}
            res.check(pl.get("kind") == "iso9660" and pl.get("dev") == "/dev/sr0", "before the map: payload on the "
                      "CD (%s)" % pl)
            c.shell_enter()
            tools = c.sh_ok("for d in /sys/block/sd*; do [ \"$(cat $d/size)\" = %d ] && echo ${d##*/}; done; true"
                            % (os.path.getsize(r3) // C.SECTOR)).split()
            vent = c.sh_ok("for d in /sys/block/sd*; do [ \"$(cat $d/size)\" = %d ] && echo ${d##*/}; done; true"
                           % (os.path.getsize(r1) // C.SECTOR)).split()
            if len(tools) != 1 or len(vent) != 1:
                raise C.HarnessError("T6m: test disks not found (%s %s)" % (tools, vent))
            tk, vk = tools[0], vent[0]
            c.sh_ok("mount -t vfat -o ro /dev/%s1 /mnt" % tk)
            out = c.sh_ok("/lib64/ld-linux-x86-64.so.2 --library-path /mnt/dm /mnt/dm/dmsetup --readonly "
                          "--noudevsync create ventoy --table '0 %d linear /dev/%s %d'; echo rc=$?" % (sectors, vk, lba))
            res.evidence["dmsetup"] = out[-600:]
            info = c.sh_ok("umount /mnt; ls /sys/block/dm-0/slaves; blkid -p -o export /dev/dm-0; true")
            res.evidence["dm0"] = info
            res.check("rc=0" in out and vk in info.split() and "TYPE=iso9660" in info and "LABEL=GBSB3INST" in info,
                      "dm-0 maps the ISO file read-only over the whole disk %s (slave %s, iso9660 GBSB3INST)"
                      % (vk, vk))
            c.shell_exit()
            c.ask("main", "4")
            m = c.ask("tools", "r")
            _, kv = c.wait_marker("PAYLOAD", 180, mark=m)
            res.evidence["payload_after_map"] = kv
            prot = (kv.get("protected") or "").split(",")
            res.check(kv.get("kind") == "iso9660" and kv.get("dev") == "/dev/dm-0" and kv.get("disk") == vk,
                      "tools r: @@GB PAYLOAD kind=iso9660 dev=/dev/dm-0 disk=%s (%s)" % (vk, kv))
            res.check(vk in prot and "sr0" in prot, "protected=%s contains %s and sr0" % (prot, vk))
            c.ask("tools", "b")
            verify_flow(s, res)
            dl = list_disks_flow(s, res)
            d1 = [d for d in dl if d.get("kname") == vk]
            res.check(bool(d1) and "payload" in d1[0].get("flags", "").split(","),
                      "in 1, %s carries flag payload (%s)" % (vk, d1[0].get("flags") if d1 else None))
            c.shell_enter()
            busy = c.sh("grep -c 'Device or resource busy' /run/gb/log/installer.log")[1].strip()
            c.shell_exit()
            res.check(busy.isdigit() and int(busy) > 0, "the exFAT partition of the mapped disk was refused with EBUSY "
                      "(held by the map; %s log lines)" % busy)
    finally:
        for n in names:
            cleanup_disk(ctx, n)


def t6g(ctx):
    """T6g: Ventoy GRUB2-mode equivalent, no CD: a stock GRUB on the stick's ESP loop-mounts the .iso file and runs
    the ISO's grub.cfg with root=(loop); the default discovery (iso9660, marker, isofile) must find the .iso file."""
    def body(res):
        preflight(ctx)
        mkdisks.delete("blank1g")
        b_raw, b_vmdk = mkdisks.RECIPES["blank1g"]()
        names = ["blank1g"]
        try:
            r1, v1 = builder_disk(ctx, "t6g")
            names.append("t6g")
            with Session(ctx, "T6g", res, {"nvme0:0": b_vmdk, "sata0:1": v1}, cd=False, hotkey="s") as s:
                pl = s.payload_kv or {}
                res.check(pl.get("kind") == "isofile" and pl.get("file") == "/ISO/googlebook-sb3-installer.iso" and
                          re.match(r"^/dev/loop\d+$", pl.get("dev", "")) is not None,
                          "@@GB PAYLOAD kind=isofile dev=/dev/loopN file=/ISO/googlebook-sb3-installer.iso (%s)" % pl)
                verify_flow(s, res)
                dl = list_disks_flow(s, res)
                k1 = [d for d in dl if d.get("size") == str(os.path.getsize(r1))]
                res.check(bool(k1) and pl.get("disk") == k1[0].get("kname") and
                          "payload" in k1[0].get("flags", "").split(","),
                          "payload disk is the stick and carries flag payload (%s)" % (k1[0] if k1 else None))
                res.check(bool(k1) and "ventoy" in k1[0].get("flags", "").split(","),
                          "the stick is recognised as a Ventoy layout (flag ventoy)")
        finally:
            for n in names:
                cleanup_disk(ctx, n)
    return [run_test("T6g", body)]


def write_two_extent_vmdk(vmdk, extents):
    """monolithicFlat descriptor with several RDONLY FLAT extents [(path, sectors)] (T7b)."""
    C.assert_test_path(vmdk)
    lines = "".join('RDONLY %d FLAT "%s" 0\n' % (n, os.path.basename(p) if C.norm(os.path.dirname(p)) ==
                                                C.norm(os.path.dirname(vmdk)) else p) for p, n in extents)
    import uuid as _u
    cid = _u.uuid4().hex[:8]
    with open(vmdk, "w", encoding="utf-8", newline="\n") as f:
        f.write("# Disk DescriptorFile\nversion=1\nencoding=\"UTF-8\"\nCID=%s\nparentCID=ffffffff\n"
                "createType=\"monolithicFlat\"\n\n# Extent description\n%s\n# The Disk Data Base\n#DDB\n\n"
                "ddb.adapterType = \"lsilogic\"\nddb.virtualHWVersion = \"21\"\n" % (cid, lines))
    probs = vmtest.guard_vmdk(vmdk)
    if probs:
        C.remove_file(vmdk)
        raise C.HarnessError("guard (descriptor): " + "; ".join(probs))
    return vmdk


def t7(ctx, which=("T7", "T2e", "T7b")):
    def body(res):
        # (T7: dd mode on a disk of exactly the ISO size; T7b below: a larger disk)
        preflight(ctx)
        mkdisks.delete("blank1g")
        b_raw, b_vmdk = mkdisks.RECIPES["blank1g"]()
        vmdk = os.path.join(C.DISK_DIR, "t7.vmdk")
        try:
            link = mkdisks.link_iso()
            mkdisks.write_vmdk(vmdk, link, access="RDONLY")
            iso_size = os.path.getsize(link)
            with Session(ctx, "T7", res, {"nvme0:0": b_vmdk, "sata0:1": vmdk}, cd=False) as s:
                pl = s.payload_kv or {}
                res.check(pl.get("kind") == "iso9660" and pl.get("dev") == "/dev/sda1" and pl.get("disk") == "sda",
                          "@@GB PAYLOAD kind=iso9660 dev=/dev/sda1 disk=sda (%s)" % pl)
                verify_flow(s, res)
                # T2e
                kv, chk = refusal_flow(s, res, "1", iso_size, "R_PAYLOAD_DISK", ("payload",), kname="sda")
            sha = C.file_sha256(C.ISO)
            res.check(sha == ctx.record["iso_sha256"], "ISO sha256 unchanged after power-off")
        finally:
            C.remove_file(vmdk)
            cleanup_disk(ctx, "blank1g")
    out = []
    if "T7" in which or "T2e" in which:
        r = run_test("T7", body)
        r2 = C.Result("T2e")
        r2.status = r.status
        r2.reason = "inside T7: " + r.reason
        r2.evidence["see"] = "T7"
        out += [r, r2.finish()]
    if "T7b" in which:
        out.append(run_test("T7b", lambda res: t7b_body(ctx, res)))
    return out


def t7b_body(ctx, res):
    """T7b: dd onto a 32 GiB disk (larger than the ISO): extent 1 = the ISO (RDONLY), extent 2 = a sparse zero file
    (RDONLY). The ISO's backup GPT is then not at the end of the disk. The firmware must still boot it, the payload
    is sda1, and mode 1 refuses it as the payload disk while too_small passes."""
    preflight(ctx)
    mkdisks.delete("blank1g")
    b_raw, b_vmdk = mkdisks.RECIPES["blank1g"]()
    vmdk = os.path.join(C.DISK_DIR, "t7b.vmdk")
    tail = os.path.join(C.DISK_DIR, "t7b_tail.img")
    try:
        link = mkdisks.link_iso()
        iso_size = os.path.getsize(link)
        total = 32 * C.GiB
        C.remove_file(tail)
        C.create_sparse(tail, total - iso_size)
        write_two_extent_vmdk(vmdk, [(link, iso_size // C.SECTOR), (tail, (total - iso_size) // C.SECTOR)])
        with Session(ctx, "T7b", res, {"nvme0:0": b_vmdk, "sata0:1": vmdk}, cd=False) as s:
            pl = s.payload_kv or {}
            res.check(pl.get("kind") == "iso9660" and pl.get("dev") == "/dev/sda1" and pl.get("disk") == "sda",
                      "the firmware booted the 32 GiB dd disk: @@GB PAYLOAD kind=iso9660 dev=/dev/sda1 disk=sda (%s)"
                      % pl)
            kv, chk = refusal_flow(s, res, "1", total, "R_PAYLOAD_DISK", ("payload",), kname="sda")
            got = dict((n, r) for n, r, _ in chk)
            res.check(got.get("payload_disk") == "refuse" and got.get("too_small") == "ok",
                      "CHECK payload_disk refuse, too_small ok (%s)" % chk)
            others = [n for n, r, _ in chk if r == "refuse" and n not in ("payload_disk", "internal_not_blank")]
            res.check(not others, "no other refusal than payload_disk (and internal_not_blank, which a VM's non-USB "
                                  "disk always gets: its head holds the ISO) (%s)" % others)
            res.info("internal_not_blank: %s (a real dd stick is USB: the check does not apply there)"
                     % got.get("internal_not_blank"))
        res.check(C.file_sha256(C.ISO) == ctx.record["iso_sha256"], "ISO sha256 unchanged after power-off")
        res.check(C.physical_size(tail) <= 64 * C.MiB, "the zero extent was not written (read-only)")
    finally:
        C.remove_file(vmdk)
        C.remove_file(tail)
        cleanup_disk(ctx, "blank1g")


# ------------------------------------------------------------------------------------------------ T10 font
HIRES_VMX = (("svga.autodetect", "FALSE"), ("svga.maxWidth", "3000"), ("svga.maxHeight", "2000"),
             ("svga.vramSize", "67108864"))


def t10(ctx):
    """T10: the console font ladder (3.5) on a high-resolution framebuffer. VMware's EFI GOP offers at most
    2048x1536 here; the kernel's EFI stub picks the largest mode with video=efifb:auto (GRUB's gfxpayload is not
    used by the EFI hand-over). (a) gb.font=auto: L2 16x32 chosen; the 32x64 PSF2 font is refused by the kernel
    (KDFONTOP EINVAL: kernel 6.8's fbcon handles glyphs up to 32 pixels high); 16x32 fits 80x24. (b) gb.font=32x64:
    /init tries L3, it fails, L2 16x32 is used."""
    def one(res, label, extra_args, expect_l3_tried):
        mkdisks.delete("blank1g")
        b_raw, b_vmdk = mkdisks.RECIPES["blank1g"]()
        try:
            cmds = ["linux /boot/vmlinuz ${gb_args} console=ttyS0,115200n8 console=tty0 gb.serial=1 video=efifb:auto"
                    + extra_args, "initrd /boot/initrd.img"]
            with Session(ctx, label, res, {"nvme0:0": b_vmdk}, vmx_extra=HIRES_VMX, grub_cmds=cmds,
                         discover=DISCOVER["s"]) as s:
                c = s.c
                c.shell_enter()
                fb = c.sh_ok("cat /sys/class/graphics/fb0/virtual_size").strip()
                ln = c.sh_ok("grep -E 'framebuffer [0-9]+x[0-9]+ font=|setfont .* failed' /run/gb/log/init.log; "
                             "true")
                t32 = c.sh("setfont -C /dev/tty1 /usr/share/consolefonts/gb-ter-32x64.psf; echo rc=$?")[1]
                t16 = c.sh("setfont -C /dev/tty1 /usr/share/consolefonts/Lat15-TerminusBold32x16.psf.gz; "
                           "echo rc=$?; stty size </dev/tty1")[1]
                c.shell_exit()
            res.evidence[label] = {"fb": fb, "init": ln, "setfont_32x64": t32, "setfont_16x32": t16,
                                   "boot": s.boot_kv}
            w, _, h = fb.partition(",")
            res.check(w.isdigit() and h.isdigit() and int(w) >= 1280 and int(h) >= 768,
                      "%s: high-resolution framebuffer %s (>= 1280x768)" % (label, fb))
            mo = re.search(r"framebuffer (\d+)x(\d+) font=(\S+) cols=(\d+) rows=(\d+)", ln)
            res.check(bool(mo) and mo.group(3) == "Lat15-TerminusBold32x16.psf.gz" and int(mo.group(4)) >= 80 and
                      int(mo.group(5)) >= 24, "%s: /init chose the 16x32 font with >= 80x24 (%s)"
                      % (label, mo.group(0) if mo else ln.strip()[-120:]))
            res.check((s.boot_kv or {}).get("font") == "Lat15-TerminusBold32x16.psf.gz",
                      "%s: @@GB BOOT font=Lat15-TerminusBold32x16.psf.gz" % label)
            if expect_l3_tried:
                res.check("gb-ter-32x64.psf failed" in ln, "%s: /init tried the 32x64 font first and fell back"
                          % label)
            res.check("rc=0" not in t32 and "KDFONTOP" in t32, "%s: setfont 32x64 refused by the kernel (%s)"
                      % (label, t32.strip()[-90:]))
            m2 = re.search(r"rc=(\d+)\s+(\d+) (\d+)", t16)
            res.check(bool(m2) and m2.group(1) == "0" and int(m2.group(2)) >= 24 and int(m2.group(3)) >= 80,
                      "%s: setfont 16x32 works, %s rows x %s columns" % (label, m2.group(2) if m2 else "?",
                                                                         m2.group(3) if m2 else "?"))
        finally:
            cleanup_disk(ctx, "blank1g")

    def body(res):
        preflight(ctx)
        one(res, "T10a", "", False)
        one(res, "T10b", " gb.font=32x64", True)
    return [run_test("T10", body)]


# ------------------------------------------------------------------------------------------------ T8
def t8(ctx):
    def body(res):
        cfree, dfree = C.free_bytes("C:\\"), C.free_bytes("D:\\")
        if max(cfree, dfree) < 30 * C.GiB:
            raise mkdisks.Skipped("SKIPPED (space): neither C: nor D: has 30 GiB free")
        base = os.path.join(C.TEST_ROOT, "t8") if dfree >= cfree else os.path.join(C.WORK, "t8")
        C.mkdir(base)
        lg = C.mkdir(os.path.join(ctx.run_dir, "t8"))
        test = os.path.join(C.DUALBOOT_DIR, "test")
        res.evidence["work"] = base
        rc, out = ps(os.path.join(test, "test_bcd_parse.ps1"), log_path=os.path.join(lg, "test_bcd_parse.log"))
        res.check("SUMMARY bcd-parse: 0 failed" in out, "test_bcd_parse.ps1: SUMMARY bcd-parse: 0 failed")
        # run_edge_tests E10 hands the text \\.\PhysicalDrive0 to gbdisk to prove that gbdisk refuses it by its text;
        # it must only ever run in a session that could not open a disk anyway (not elevated)
        p = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent())"
                            ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        elevated = p.stdout.decode(errors="replace").strip()
        res.evidence["elevated"] = elevated
        if not res.check(elevated == "False", "the harness session is not elevated (%s): run_edge_tests E10 cannot "
                                              "open a device" % elevated):
            return
        rc, out = ps(os.path.join(test, "run_edge_tests.ps1"), "-WorkDir", os.path.join(base, "edge"),
                     "-ExportDir", r"D:\gb-test\export_small", "-LogDir", lg,
                     log_path=os.path.join(lg, "run_edge_tests.out.log"), timeout=6 * 3600)
        res.check("SUMMARY edge cases: 56 passed, 0 failed" in out, "run_edge_tests.ps1: 56 passed, 0 failed")
        rc, out = ps(os.path.join(test, "run_image_tests.ps1"), "-Image", os.path.join(base, "cycle_small.img"),
                     "-ExportDir", r"D:\gb-test\export_small", "-Tag", "iso_t8", "-DeleteImage", "-LogDir", lg,
                     log_path=os.path.join(lg, "run_image_tests.out.log"), timeout=6 * 3600)
        # 57 = the 25 cases of wave 2 + the 32 slot-aware -UpdateEspOnly cases WAVE3_PLAN 5.4 added (W3 (1)-(6))
        res.check("SUMMARY 'iso_t8': 57 passed, 0 failed" in out, "run_image_tests.ps1: 57 passed, 0 failed")
        rc, out = ps(os.path.join(test, "test_import_iso_state.ps1"), "-WorkDir", os.path.join(base, "import"),
                     "-LogDir", lg, log_path=os.path.join(lg, "test_import_iso_state.out.log"), timeout=6 * 3600)
        m = re.search(r"SUMMARY import-iso: (\d+) passed, (\d+) failed", out)
        res.check(bool(m) and m.group(2) == "0", "test_import_iso_state.ps1: 0 failed (%s)"
                  % (m.group(0) if m else "no SUMMARY"))
        res.check("stale" in out.lower() or "are not from" in out, "the stale-pointer case ran")
    return [run_test("T8", body)]
