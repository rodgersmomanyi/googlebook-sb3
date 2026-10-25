r"""Offline unit tests of the TEST harness (no VM, no builder, no disk device; small sparse files only, below
C:\GB-x86\iso\test\work\unit\).

  python -I C:\GB-x86\iso\test\test_harness.py [-v]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I: no script dir on sys.path
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import unittest
import uuid

import builder_send
import common as C
import gbtests as TS
import mkdisks
import run_all
import t0_static
import validate_dualboot as VD
import validate_wholedisk as W
import vmtest

UNIT = os.path.join(C.WORK, "unit")


def setUpModule():
    if os.path.isdir(UNIT):
        shutil.rmtree(UNIT)
    os.makedirs(UNIT)


def tearDownModule():
    shutil.rmtree(UNIT, ignore_errors=True)


# ------------------------------------------------------------------------------------------------ helpers
class FakeClock:
    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeShell:
    """Transport that answers the harness's shell command lines (begin/end marker protocol) with canned output."""

    def __init__(self, outputs, status=0, echo=True):
        self.outputs, self.status, self.echo = list(outputs), status, echo
        self.pending, self.written, self.closed = b"", b"", False

    def write(self, data):
        self.written += data
        text = data.decode()
        if self.echo:
            self.pending += data.replace(b"\n", b"\r\n")
        import re
        m = re.search(r'__G""BB([0-9a-f]+)__', text)
        if m:
            tag = m.group(1)
            out = self.outputs.pop(0) if self.outputs else ""
            self.pending += ("__GBB%s__\r\n%s__GBE%s__ %d\r\ngbsh# " % (tag, out.replace("\n", "\r\n"), tag,
                                                                       self.status)).encode()

    def read_available(self):
        d, self.pending = self.pending, b""
        return d

    def close(self):
        self.closed = True


def load_option(desc, part, start, size, guid, path, attrs=1):
    hd = struct.pack("<BBH", 4, 1, 42) + struct.pack("<IQQ", part, start, size) + uuid.UUID(guid).bytes_le + bytes([2, 2])
    p = (path + "\0").encode("utf-16-le")
    fp = struct.pack("<BBH", 4, 4, 4 + len(p)) + p
    end = struct.pack("<BBH", 0x7F, 0xFF, 4)
    fpl = hd + fp + end
    return struct.pack("<I", 7) + struct.pack("<IH", attrs, len(fpl)) + (desc + "\0").encode("utf-16-le") + fpl


def order_var(nums):
    return struct.pack("<I", 7) + b"".join(struct.pack("<H", n) for n in nums)


# ------------------------------------------------------------------------------------------------ tests
class TestMarkers(unittest.TestCase):
    def test_parse(self):
        t, kv = C.parse_marker('@@GB DISK n=1 kname=nvme0n1 size=28814885376 sector=512 model="VMware Virtual NVMe '
                               'Disk" tran=nvme flags=-\r')
        self.assertEqual(t, "DISK")
        self.assertEqual(kv["model"], "VMware Virtual NVMe Disk")
        self.assertEqual(kv["size"], "28814885376")
        self.assertEqual(kv["flags"], "-")
        self.assertIsNone(C.parse_marker("gbsh# @@GB DISK n=1"))
        t, kv = C.parse_marker("@@GB PAYLOAD-NONE")
        self.assertEqual((t, kv), ("PAYLOAD-NONE", {}))
        t, kv = C.parse_marker("@@GB RESULT flow=wholedisk status=REFUSED code=R_TOO_SMALL")
        self.assertEqual(kv, {"flow": "wholedisk", "status": "REFUSED", "code": "R_TOO_SMALL"})


class TestPaths(unittest.TestCase):
    def test_assert_test_path(self):
        for bad in (C.USB_IMG, r"C:\GB-x86\lapis.bin", r"\\.\PhysicalDrive0", r"D:\gb-usbtest\usbtest.vmdk",
                    r"D:\GB-iso\googlebook-sb3-installer.iso", r"C:\GB-x86\gbsh.py"):
            with self.assertRaises(C.HarnessError, msg=bad):
                C.assert_test_path(bad)
        C.assert_test_path(r"D:\GB-iso\test\disks\x.img")
        C.assert_test_path(os.path.join(UNIT, "x"))

    def test_vmrun_assert(self):
        with self.assertRaises(C.HarnessError):
            vmtest.Vmrun._assert(r"C:\GB-x86\lapis-boot.vmx")
        with self.assertRaises(C.HarnessError):
            vmtest.Vmrun._assert(r"D:\gb-usbtest\usbtest.vmx")
        vmtest.Vmrun._assert(C.VMX)

    def test_pipe_name(self):
        with self.assertRaises(C.HarnessError):
            vmtest.PipeTransport(r"\\.\pipe\lapis_con", open_timeout=0)


class TestGuard(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(UNIT, "guard")
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "vm"))
        os.makedirs(os.path.join(self.root, "disks"))
        self.raw = os.path.join(self.root, "disks", "a.img")
        C.create_sparse(self.raw, 64 * C.MiB)
        self.vmdk = os.path.join(self.root, "disks", "a.vmdk")
        with open(self.vmdk, "w", newline="\n") as f:
            f.write(mkdisks.descriptor_text(self.raw, 64 * C.MiB // 512, "RW", os.path.dirname(self.vmdk)))
        self.iso = os.path.join(self.root, "x.iso")
        with open(self.iso, "wb") as f:
            f.write(bytes(4096))
        self.vmx = os.path.join(self.root, "vm", "t.vmx")

    def write(self, text):
        with open(self.vmx, "w", newline="\n") as f:
            f.write(text)

    def good(self):
        return vmtest.make_vmx(nvme=self.vmdk, cd=self.iso, sata1=self.vmdk)

    def test_good(self):
        self.write(self.good())
        self.assertEqual(vmtest.guard_vmx(self.vmx, root=self.root), [])
        self.assertEqual(vmtest.guard_vmdk(self.vmdk, root=self.root), [])

    def test_fixed_values(self):
        t = self.good()
        self.assertIn('memSize = "2048"', t)
        self.assertIn('numvcpus = "2"', t)
        self.assertIn('usb.present = "FALSE"', t)
        self.assertIn('serial0.fileName = "\\\\.\\pipe\\gbiso_con"', t)
        self.assertIn('nvme0:0.mode = "independent-persistent"', t)
        self.assertIn('sata0:1.mode = "independent-nonpersistent"', t)
        self.assertNotIn("usb.autoConnect", t)

    def bad(self, old, new):
        t = self.good()
        self.assertIn(old, t)
        self.write(t.replace(old, new))
        p = vmtest.guard_vmx(self.vmx, root=self.root)
        self.assertTrue(p, "guard accepted: %s -> %s" % (old, new))
        return p

    def test_rejects(self):
        self.bad('usb.present = "FALSE"', 'usb.present = "TRUE"')
        self.bad('ethernet0.present = "FALSE"', 'ethernet0.present = "TRUE"')
        self.bad('memSize = "2048"', 'memSize = "4096"')
        self.bad('numvcpus = "2"', 'numvcpus = "4"')
        self.bad('uefi.secureBoot.enabled = "FALSE"', 'uefi.secureBoot.enabled = "TRUE"')
        self.bad('serial0.fileName = "\\\\.\\pipe\\gbiso_con"', 'serial0.fileName = "\\\\.\\pipe\\lapis_con"')
        self.bad('nvme0:0.deviceType = "disk"', 'nvme0:0.deviceType = "rawDisk"')
        self.bad('sata0:0.deviceType = "cdrom-image"', 'sata0:0.deviceType = "cdrom-raw"')
        self.bad('nvme0:0.fileName = "%s"' % self.vmdk, 'nvme0:0.fileName = "\\\\.\\PhysicalDrive0"')
        self.bad('nvram = "gbiso-test.nvram"', 'nvram = "C:\\x.nvram"')
        self.bad('sound.present = "FALSE"', 'sound.present = "TRUE"')
        p = self.bad('usb.generic.autoConnect = "FALSE"',
                     'usb.generic.autoConnect = "FALSE"\nusb.autoConnect.device0 = "0x0781:0x5581"')
        self.assertTrue(any("usb.autoconnect.device" in x for x in p))
        outside = os.path.join(C.WORK, "outside.iso")
        with open(outside, "wb") as f:
            f.write(bytes(2048))
        try:
            self.bad('sata0:0.fileName = "%s"' % self.iso, 'sata0:0.fileName = "%s"' % outside)
        finally:
            os.remove(outside)

    def test_vmx_outside_root(self):
        self.assertTrue(vmtest.guard_vmx(r"C:\GB-x86\lapis-boot.vmx", root=self.root))

    def test_vmdk_rejects(self):
        def bad_desc(text):
            with open(self.vmdk, "w", newline="\n") as f:
                f.write(text)
            return vmtest.guard_vmdk(self.vmdk, root=self.root)
        good = mkdisks.descriptor_text(self.raw, 64 * C.MiB // 512, "RW", os.path.dirname(self.vmdk))
        self.assertTrue(bad_desc(good.replace('monolithicFlat', 'twoGbMaxExtentSparse')))
        self.assertTrue(bad_desc(good.replace('FLAT "a.img" 0', 'FLAT "a.img" 2048')))
        self.assertTrue(bad_desc(good.replace('FLAT "a.img"', 'FLAT "\\\\.\\PhysicalDrive1"')))
        self.assertTrue(bad_desc(good.replace('FLAT "a.img"', 'FLAT "..\\..\\..\\outside.img"')))
        self.assertTrue(bad_desc(good.replace("RW %d FLAT" % (64 * C.MiB // 512), "RW %d FLAT" % (128 * C.MiB // 512))))
        self.assertTrue(bad_desc(good.replace('RW ', 'NOACCESS ')))
        self.assertTrue(bad_desc(good.replace("parentCID=ffffffff", "parentCID=12345678")))
        self.assertEqual(bad_desc(good.replace('RW ', 'RDONLY ')), [])

    def test_parse_real_descriptor(self):
        p = r"D:\gb-usbtest\usbtest.vmdk"
        if not os.path.isfile(p):
            self.skipTest("no usbtest.vmdk")
        with open(p, encoding="utf-8", errors="replace") as f:      # read-only
            fields, ext, probs = vmtest.parse_vmdk(f.read())
        self.assertEqual(fields.get("createType"), "monolithicFlat")
        self.assertEqual(len(ext), 1)
        self.assertEqual(ext[0][2], "FLAT")


class TestConsole(unittest.TestCase):
    def console(self, script, sizes=(1024,), kill=None):
        clk = FakeClock()
        t = vmtest.ScriptTransport(script)
        return vmtest.Console(t, allowed_sizes=sizes, on_kill=kill, clock=clk.time, sleep=clk.sleep), t

    def test_prompt_reply_and_pick(self):
        c, t = self.console([
            (None, b"@@GB MENU id=main\r\n  1  Write ...\r\n@@GB PROMPT id=main\r\n"),
            (r"1\r$", b"1\r\n@@GB DISK n=1 kname=nvme0n1 size=1024 sector=512 model=\"x y\" tran=nvme flags=-\r\n"
                      b"@@GB DISK n=2 kname=sda size=2048 sector=512 model=\"z\" tran=sata flags=payload\r\n"),
            (None, b"@@GB PROMPT id=wd.disk\r\n"),
        ], sizes=(1024, 2048))
        m = c.ask("main", "1")
        d, m2 = c.pick_disk("wd.disk", m, size=2048)
        self.assertEqual(d["kname"], "sda")
        self.assertTrue(t.written.endswith(b"2\r"))

    def test_reply_waits_for_prompt_line(self):
        c, t = self.console([(None, b"@@GB PROMPT id=ma"), (None, b"in\r\n")])
        c.ask("main", "4")
        self.assertEqual(t.written, b"4\r")

    def test_journal_ack_answered_b(self):
        c, t = self.console([(None, b"@@GB JOURNAL flow=dualboot disk=nvme0n1 phase=planned result=none\r\n"
                                    b"@@GB PROMPT id=journal.ack\r\n"),
                             (r"b\r$", b"@@GB MENU id=main\r\n@@GB PROMPT id=main\r\n")])
        c.expect_prompt("main", 30)
        self.assertTrue(t.written.startswith(b"b\r"))
        self.assertTrue(c.failures)

    def test_unexpected_prompt(self):
        c, t = self.console([(None, b"@@GB PROMPT id=wd.grow\r\n")])
        with self.assertRaises(vmtest.UnexpectedPrompt):
            c.expect_prompt("wd.confirm", 30)
        self.assertEqual(t.written, b"")

    def test_kill_switch_size(self):
        killed = []
        c, t = self.console([(None, b"@@GB DISK n=1 kname=sdb size=999 sector=512 model=\"m\" tran=sata flags=-\r\n")],
                            kill=killed.append)
        with self.assertRaises(vmtest.KillSwitch):
            c.wait_marker("PROMPT", 30)
        self.assertTrue(killed and "unexpected disk" in killed[0])

    def test_kill_switch_usb(self):
        killed = []
        c, t = self.console([(None, b"@@GB DISK n=1 kname=sdb size=1024 sector=512 model=\"m\" tran=usb flags=-\r\n")],
                            kill=killed.append)
        with self.assertRaises(vmtest.KillSwitch):
            c.wait_marker("PROMPT", 30)
        self.assertTrue(killed)

    def test_wait_result_and_continue(self):
        c, t = self.console([(None, b"@@GB PROMPT id=main\r\n"),
                             (r"2\r$", b"@@GB CHECK name=not_efi result=ok code=-\r\n"
                                       b"@@GB RESULT flow=dualboot status=REFUSED code=R_NO_REGION\r\n"
                                       b"@@GB PROMPT id=continue\r\n"),
                             (r"2\r\r$", b"@@GB PROMPT id=main\r\n")])
        m = c.ask("main", "2")
        kv = c.wait_result("dualboot", 60, m)
        self.assertEqual(kv["code"], "R_NO_REGION")
        self.assertEqual(TS.checks_since(c, m), [("not_efi", "ok", "-")])
        c.expect_prompt("main", 30)

    def test_timeout(self):
        c, t = self.console([])
        with self.assertRaises(vmtest.Timeout):
            c.wait_marker("BOOT", 5)

    def test_shell(self):
        clk = FakeClock()
        t = FakeShell(["hello\nworld\n"])
        c = vmtest.Console(t, clock=clk.time, sleep=clk.sleep)
        st, out = c.sh("echo hello; echo world")
        self.assertEqual((st, out), (0, "hello\nworld\n"))
        self.assertIn(b'__G""BB', t.written)
        t.status = 3
        with self.assertRaises(C.HarnessError):
            c.sh_ok("false")
        with self.assertRaises(C.HarnessError):
            c.sh("a\nb")

    def test_pull_tree(self):
        import base64
        import gzip
        import tarfile
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            data = b'{"format": "gb-dualboot-plan/1"}'
            ti = tarfile.TarInfo("./plan.json"); ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        b64 = base64.b64encode(gzip.compress(buf.getvalue())).decode()
        clk = FakeClock()
        c = vmtest.Console(FakeShell([b64 + "\n"]), clock=clk.time, sleep=clk.sleep)
        d = os.path.join(UNIT, "pull")
        self.assertEqual(c.pull_tree("/run/gb/state", d), ["plan.json"])
        with self.assertRaises(C.HarnessError):
            c.pull_tree("/etc", d)

    def test_power_off_waits_for_menu(self):
        c, t = self.console([(None, b"@@GB RESULT flow=verify status=OK code=-\r\n@@GB PROMPT id=continue\r\n"),
                             (r"\r$", b""), (None, b""),
                             (None, b"@@GB MENU id=main\r\n@@GB PROMPT id=main\r\n"),
                             (r"4\r$", b"@@GB MENU id=tools\r\n@@GB PROMPT id=tools\r\n"),
                             (r"poweroff\r$", b"@@GB POWEROFF\r\n")])
        c.wait_result("verify", 30, 0)
        c.power_off()
        self.assertEqual(t.written, b"\r4\rpoweroff\r")

    def test_ansi(self):
        self.assertEqual(vmtest.strip_ansi("\x1b[2J\x1b[1;1H Googlebook SB3 installer\x1b[m"),
                         " Googlebook SB3 installer")


class TestPipe(unittest.TestCase):
    """PipeTransport + Console against a local named-pipe server on \\\\.\\pipe\\gbiso_con (skipped if a VM holds it)."""

    def test_pipe_roundtrip(self):
        import ctypes
        import threading
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateNamedPipeW.restype = wintypes.HANDLE
        k32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                  ctypes.c_void_p]
        k32.ReadFile.argtypes = k32.WriteFile.argtypes
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.CreateNamedPipeW(C.PIPE, 3, 0, 1, 65536, 65536, 0, None)   # duplex, byte mode, 1 instance
        if h in (None, ctypes.c_void_p(-1).value):
            self.skipTest("pipe in use (a VM is running?)")
        got = {}

        def server():
            k32.ConnectNamedPipe(h, None)
            msg = b"GRUB\r\n@@GB PROMPT id=main\r\n"
            n = wintypes.DWORD()
            k32.WriteFile(h, msg, len(msg), ctypes.byref(n), None)
            buf = ctypes.create_string_buffer(64)
            k32.ReadFile(h, buf, 2, ctypes.byref(n), None)
            got["reply"] = buf.raw[:n.value]
        th = threading.Thread(target=server, daemon=True)
        th.start()
        try:
            t = vmtest.PipeTransport(open_timeout=10)
            c = vmtest.Console(t)
            c.ask("main", "4", timeout=10)
            th.join(10)
            self.assertEqual(got.get("reply"), b"4\r")
            c.close()
        finally:
            k32.CloseHandle(h)


class TestSparse(unittest.TestCase):
    def test_sparse_hash_and_ranges(self):
        p = os.path.join(UNIT, "sparse.img")
        C.create_sparse(p, 256 * C.MiB)
        with open(p, "r+b") as f:
            f.seek(100 * C.MiB)
            f.write(os.urandom(70000))
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(16 * C.MiB), b""):
                h.update(b)
        self.assertEqual(C.sparse_sha256(p), h.hexdigest())
        self.assertLess(C.physical_size(p), 8 * C.MiB)
        with self.assertRaises(FileExistsError):
            C.create_sparse(p, 256 * C.MiB)

    def test_stream_roundtrip(self):
        size = 64 * C.MiB
        src = bytearray(size)
        src[0:512] = os.urandom(512)
        src[10 * C.MiB:10 * C.MiB + 3 * C.MiB] = os.urandom(3 * C.MiB)
        src[20 * C.MiB:21 * C.MiB] = bytes(C.MiB)                # a zero extent: must stay unallocated
        ext = [(0, 4096), (10 * C.MiB, 3 * C.MiB), (20 * C.MiB, C.MiB)]
        out = io.BytesIO()
        builder_send.encode(io.BytesIO(bytes(src)), size, ext, out)
        p = os.path.join(UNIT, "recv.img")
        size2, frames, written = mkdisks.receive_stream(io.BytesIO(out.getvalue()), p, size)
        self.assertEqual(size2, size)
        with open(p, "rb") as f:
            self.assertEqual(f.read(), bytes(src))
        self.assertLessEqual(written, 3 * C.MiB + 4096)
        bad = bytearray(out.getvalue())
        bad[200] ^= 1
        with self.assertRaises(C.HarnessError):
            mkdisks.receive_stream(io.BytesIO(bytes(bad)), os.path.join(UNIT, "recv2.img"), size)


class TestGptWriter(unittest.TestCase):
    def test_roundtrip_with_gpt_validate(self):
        p = os.path.join(UNIT, "gpt.img")
        size = 128 * C.MiB
        g = str(uuid.uuid4())
        mkdisks.write_gpt_disk(p, size, [dict(type=C.TYPE_BASIC, guid=g, first=2048, last=2048 + 65535,
                                              name="Basic data partition")],
                               writes=[(2048 * 512, mkdisks.bitlocker_sector())])
        info, probs = mkdisks.read_gpt(p)
        self.assertEqual(probs, [])
        self.assertEqual(info["entries"][0]["id"], g)
        self.assertEqual(C.read_at(p, 2048 * 512 + 3, 8), b"-FVE-FS-")

    def test_t4n_lines(self):
        exm = json.load(open(C.EXPORT_MANIFEST, encoding="utf-8"))
        ls = TS.t4n_lines(exm)
        self.assertEqual(len(ls), 19)
        d = ls[0].split(" ", 6)
        self.assertEqual(d[:4], ["D", "4", str(1141 * 2048), str(1141 * 2048 + 4194304 - 1)])
        self.assertEqual(d[6], "Basic data partition")
        firsts = [int(x.split()[2]) for x in ls[1:]]
        self.assertEqual(firsts, [3189 * 2048 + 2048 * i for i in range(18)])
        names = [x.split()[6] for x in ls[1:]]
        self.assertEqual(names, C.GB_NAMES)
        vb = [x for x in ls[1:] if x.split()[6] == "vbmeta_b"][0].split()[7]
        a = int([p for p in exm["partitions"] if p["name"] == "vbmeta_b"][0]["attributes"], 16)
        self.assertEqual(sum(1 << int(b) for b in vb.split(",")) if vb != "-" else 0, a)


class TestValidators(unittest.TestCase):
    def test_image_gpt(self):
        if not os.path.isfile(C.USB_IMG):
            self.skipTest("no USB image")
        prim, back = W.image_gpt()                               # read-only
        self.assertEqual((len(prim), len(back)), (17408, 16896))
        ents = [e for e in W.entries_of(prim) if e[:16] != bytes(16)]
        self.assertEqual(len(ents), 18)
        names = [e[56:128].decode("utf-16-le").rstrip("\0") for e in ents]
        self.assertEqual(names, C.GB_NAMES)
        self.assertEqual(back[:16384], prim[1024:])

    def test_check_contents(self):
        p = os.path.join(UNIT, "contents.img")
        C.create_sparse(p, 64 * C.MiB)
        data = os.urandom(3 * C.MiB + 512)
        with open(p, "r+b") as f:
            f.seek(4 * C.MiB)
            f.write(data)
        man = {"objects": {"o": {"raw_size": len(data), "sha256": hashlib.sha256(data).hexdigest()}},
               "whole_disk": {"partitions": [{"slot": 0, "name": "src", "object": "o"},
                                             {"slot": 1, "name": "blank", "object": None}]}}
        layout = {0: (8192, 8192 + 8 * 2048 - 1), 1: (32768, 32768 + 16 * 2048 - 1)}
        r = C.Result("c")
        with open(p, "rb") as f:
            W.check_contents(f, r, man, layout)
        self.assertTrue(all(ok for ok, _ in r.checks), r.checks)
        with open(p, "r+b") as f:
            f.seek(32768 * 512 + 100)
            f.write(b"x")
        r = C.Result("d")
        with open(p, "rb") as f:
            W.check_contents(f, r, man, layout)
        self.assertEqual([ok for ok, _ in r.checks], [True, True, False])


@unittest.skipUnless("--slow" in sys.argv, "slow: run with --slow")
class TestT3DiskLogic(unittest.TestCase):
    """mkimage (C 1024 / D 26624 MiB) + resize-image + snapshot + gbdisk plan/create in image mode: the GPT side of
    T3 criteria 2, 3 and 7 must pass (contents cannot: no partition data is written)."""

    def test_layout_checks(self):
        raw = os.path.join(UNIT, "t3logic.img")
        mkdisks.mkimage(raw, 1024, 26624)
        rc, out = mkdisks.gbdisk("resize-image", "--image", raw, "--number", "4", "--length", "2147483648",
                                 "--apply")
        self.assertEqual(rc, 0, out)
        pre = os.path.join(UNIT, "t3logic.pre.json")
        rc, out = mkdisks.gpt_validate("snapshot", raw, pre)
        self.assertEqual(rc, 0, out)
        plan = os.path.join(UNIT, "t3logic.plan.json")
        rc, out = mkdisks.gbdisk("plan", "--manifest", C.EXPORT_MANIFEST, "--anchor", "4", "--reserve",
                                 "1073741824", "--image", raw, "--out", plan)
        self.assertEqual(rc, 0, out)
        rc, out = mkdisks.gbdisk("create", "--plan", plan, "--image", raw, "--apply")
        self.assertEqual(rc, 0, out)
        man = {"objects": {}, "dual_boot": {"partitions": []}}
        r = C.Result("t3logic")
        VD.check_t3_disk(r, raw, pre, plan, C.EXPORT_MANIFEST, man, plan)
        bad = [t for ok, t in r.checks if ok is False]
        self.assertEqual(bad, [], "\n".join(bad))
        self.assertTrue(any("3,343,908,864" in t for ok, t in r.checks if ok))
        self.assertTrue(any("10,411 MiB" in t for ok, t in r.checks if ok))
        self.assertTrue(any("1 GiB unallocated" in t for ok, t in r.checks if ok))


class TestEfi(unittest.TestCase):
    ESP = "32ddc856-367c-4e19-9fa5-05d361936496"

    def vars(self):
        v = {"Boot0000": load_option("Windows Boot Manager", 1, 2048, 204800, "e89e4eda-1cfb-4449-844a-065414d2ed6b",
                                     "\\EFI\\Microsoft\\Boot\\bootmgfw.efi"),
             "Boot0001": load_option("EFI VMware Virtual SATA CDROM Drive (0.0)", 1, 0, 1, str(uuid.uuid4()), "x"),
             "BootOrder": order_var([0, 1])}
        return v

    def test_parse(self):
        lo = VD.parse_load_option(load_option("Googlebook OS", 6, 6531072, 1048576, self.ESP, "\\EFI\\BOOT\\BOOTX64.EFI"))
        self.assertEqual(lo["description"], "Googlebook OS")
        self.assertEqual(lo["hd"]["part"], 6)
        self.assertEqual(lo["hd"]["guid"], self.ESP)
        self.assertEqual(lo["path"], "\\EFI\\BOOT\\BOOTX64.EFI")
        self.assertEqual(VD.boot_order(order_var([3, 0, 1])), [3, 0, 1])
        self.assertEqual(VD.find_by_description(self.vars(), "SATA CDROM"), [1])

    def test_install_check(self):
        before = self.vars()
        after = dict(before)
        after["Boot0002"] = load_option("Googlebook OS", 6, 6531072, 1048576, self.ESP, "\\EFI\\BOOT\\BOOTX64.EFI")
        after["BootOrder"] = order_var([0, 1, 2])
        mk = [("EFI", {"phase": "before", "bootorder": "0000,0001", "new": "-", "removed": "-"}),
              ("EFI", {"phase": "after", "bootorder": "0000,0001,0002", "new": "0002", "removed": "-"})]
        r = C.Result("x")
        n = VD.check_efi_install(r, before, after, self.ESP, 6531072, 1048576, 6, mk)
        self.assertEqual(n, 2)
        self.assertTrue(all(ok for ok, _ in r.checks if ok is not None), r.checks)
        r2 = C.Result("y")
        after["Boot0001"] = after["Boot0001"] + b"\0"
        VD.check_efi_install(r2, before, after, self.ESP, 6531072, 1048576, 6, mk)
        self.assertTrue(any(ok is False for ok, _ in r2.checks))
        r3 = C.Result("z")
        un = dict(before)
        VD.check_efi_uninstall(r3, after | {"Boot0001": before["Boot0001"]}, un, 2, [0, 1])
        self.assertTrue(all(ok for ok, _ in r3.checks if ok is not None), r3.checks)


class TestMisc(unittest.TestCase):
    def test_zero_ranges(self):
        self.assertEqual(W.zero_ranges_405(4 * C.MiB), [(0, 4 * C.MiB)])
        self.assertEqual(W.zero_ranges_405(256 * C.MiB), [(0, 256 * C.MiB)])
        L = 14 * C.GiB
        self.assertEqual(W.zero_ranges_405(L), [(0, 64 * C.MiB), (L - 64 * C.MiB, 64 * C.MiB)])

    def test_ids_and_plan(self):
        self.assertEqual(run_all.canon("t3na"), "T3n(a)")
        self.assertEqual(run_all.canon("T3n(f)"), "T3n(f)")
        self.assertEqual(run_all.canon("T1A"), "T1a")
        steps = run_all.plan(["T4"])
        self.assertEqual([(f.__name__, kw) for f, kw in steps], [("t3_group", {"upto": "G3"})])
        steps = run_all.plan(["T2e", "T2a"])
        self.assertEqual([f.__name__ for f, kw in steps], ["t2", "t7"])
        self.assertEqual(set(run_all.REQUIRED), set(run_all.ORDER) - {"T1c"})

    def test_gpt_validate_import(self):
        gv = mkdisks.load_gpt_validate()
        self.assertEqual(len(gv.HOST), 5)
        self.assertEqual(gv.HOST[3][1], C.TYPE_BASIC)

    def test_gbdisk_image_only(self):
        with self.assertRaises(C.HarnessError):
            mkdisks.gbdisk("layout", "--disk", "0")
        with self.assertRaises(C.HarnessError):
            mkdisks.gbdisk("layout", "--image", r"C:\GB-x86\out\googlebook-sb3-usb.img")

    def test_shell_scripts(self):
        bash = r"C:\Program Files\Git\bin\bash.exe"           # Git Bash only (never the WSL launcher)
        for f in ("builder_media.sh", "check_esp_tree.sh"):
            data = open(os.path.join(C.HERE, f), "rb").read()
            self.assertNotIn(b"\r", data, f)
            if not os.path.isfile(bash):
                continue
            p = subprocess.run([bash, "-n", os.path.join(C.HERE, f)], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
            self.assertEqual(p.returncode, 0, p.stdout.decode(errors="replace"))
        for f in ("builder_send.py", "t0_static.py"):
            self.assertNotIn(b"\r", open(os.path.join(C.HERE, f), "rb").read(), f)


class TestT0Pieces(unittest.TestCase):
    def setUp(self):
        t0_static.checks.clear()
        t0_static.facts.clear()

    def test_pe(self):
        b = bytearray(1024)
        b[:2] = b"MZ"
        struct.pack_into("<I", b, 0x3C, 0x80)
        b[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", b, 0x80 + 24, 0x20B)
        struct.pack_into("<H", b, 0x80 + 24 + 70, 0x0160)
        self.assertEqual(t0_static.pe_dllchar(bytes(b)), 0x0160)
        self.assertIsNone(t0_static.pe_dllchar(b"xx" + bytes(100)))

    def test_grub_template(self):
        tpl = r"C:\GB-x86\iso\build\templates\grub.cfg.in"
        if not os.path.isfile(tpl):
            self.skipTest("no BUILD template")
        bid = "20261025-153000-1a2b3c4d"
        cfg = open(tpl, encoding="utf-8").read().replace("@BUILD_ID@", bid)
        t0_static.grub_checks(cfg, bid)
        bad = [t for ok, t in t0_static.checks if not ok]
        self.assertEqual(bad, [])
        self.assertEqual(len([t for ok, t in t0_static.checks if "console list" in t]), 3)

    def test_eltorito(self):
        img = bytearray(40 * 2048)
        br = bytearray(2048)
        br[0] = 0; br[1:6] = b"CD001"; br[6] = 1; br[7:30] = b"EL TORITO SPECIFICATION"
        struct.pack_into("<I", br, 0x47, 30)
        img[17 * 2048:18 * 2048] = br
        cat = bytearray(2048)
        cat[0] = 1; cat[1] = 0xEF
        s = sum(struct.unpack_from("<16H", bytes(cat[:30]) + b"\x55\xAA"))
        cat[30:32] = b"\x55\xAA"
        cat[28:30] = struct.pack("<H", (-s) & 0xFFFF)
        d = bytearray(32); d[0] = 0x88; d[1] = 0; struct.pack_into("<H", d, 6, 16384); struct.pack_into("<I", d, 8, 33)
        cat[32:64] = d
        img[30 * 2048:31 * 2048] = cat
        e = t0_static.eltorito(io.BytesIO(bytes(img)))
        self.assertEqual(e, [(0xEF, 0, 16384, 33, True)])


class TestAudit2Pieces(unittest.TestCase):
    """Harness pieces added after the second audit (T1d spots, vmx extras, two-extent descriptors, GRUB command
    line)."""

    def test_t1d_spots(self):
        man = W.load_manifest() if os.path.isfile(C.PAYLOAD_MANIFEST) else None
        if man is None:
            self.skipTest("no payload manifest")
        size = 34359738368
        N = size // 512
        spots = mkdisks.t1d_spots(size, man)
        self.assertTrue(all(o % mkdisks.SPOT == 0 for o in spots))
        self.assertTrue(spots[0] >= 2048 * 512 and spots[-1] + mkdisks.SPOT <= (N - 2081) * 512)
        for grow in (False, True):
            Wa = W.written_areas(N, man, grow)
            outside = [o for o in spots if not any(o < b and a < o + mkdisks.SPOT for a, b in Wa)]
            inside = [o for o in spots if any(a <= o and o + mkdisks.SPOT <= b for a, b in Wa)]
            self.assertGreater(len(outside), 100)
            self.assertGreater(len(inside), 100)
        self.assertNotEqual(mkdisks.pattern_block(65536), mkdisks.pattern_block(131072))
        self.assertEqual(len(mkdisks.pattern_block(65536)), mkdisks.SPOT)
        self.assertTrue(any(mkdisks.pattern_block(0)))

    def test_vmx_extra_and_stick_slot(self):
        t = vmtest.make_vmx(sata3=r"D:\GB-iso\test\disks\s.vmdk", extra=(("svga.maxWidth", "3000"),))
        self.assertIn('sata0:3.mode = "independent-persistent"', t)
        self.assertIn('svga.maxWidth = "3000"', t)
        self.assertIn('rtc.diffFromUTC = "0"', t)
        with self.assertRaises(C.HarnessError):
            vmtest.make_vmx(extra=(("usb.present", "TRUE"),))
        with self.assertRaises(C.HarnessError):
            vmtest.make_vmx(extra=(("svga.maxWidth", "3000\nusb.present = TRUE"),))

    def test_two_extent_descriptor(self):
        root = os.path.join(UNIT, "t7b")
        shutil.rmtree(root, ignore_errors=True)
        os.makedirs(root)
        a, b = os.path.join(root, "a.img"), os.path.join(root, "b.img")
        C.create_sparse(a, 4 * C.MiB)
        C.create_sparse(b, 8 * C.MiB)
        vmdk = os.path.join(root, "t.vmdk")
        real = vmtest.guard_vmdk
        try:
            vmtest.guard_vmdk = lambda p, root_=None: real(p, root=root)
            TS.write_two_extent_vmdk(vmdk, [(a, 4 * C.MiB // 512), (b, 8 * C.MiB // 512)])
        finally:
            vmtest.guard_vmdk = real
        fields, ext, probs = vmtest.parse_vmdk(open(vmdk).read())
        self.assertEqual(probs, [])
        self.assertEqual([(x[0], x[1], x[2]) for x in ext], [("RDONLY", 8192, "FLAT"), ("RDONLY", 16384, "FLAT")])
        self.assertEqual(vmtest.guard_vmdk(vmdk, root=root), [])

    def test_grub_command_line(self):
        clk = FakeClock()
        t = vmtest.ScriptTransport([
            (None, b"Googlebook SB3 installer\r\n"),
            (r"c$", b"\x1b[0mgrub> "),
            (r"initrd /boot/initrd\.img\r$", b"\r\ngrub> "),
        ])
        c = vmtest.Console(t, clock=clk.time, sleep=clk.sleep)
        s = TS.Session.__new__(TS.Session)
        s.c, s.res = c, C.Result("x")
        # one command only: the script answers the first prompt and the one after 'initrd'
        s.grub_command_line(["initrd /boot/initrd.img"])
        self.assertTrue(t.written.decode().startswith("cinitrd /boot/initrd.img\rboot\r"))


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in sys.argv else 1, argv=[sys.argv[0]])
