r"""Ad-hoc diagnostic for the dual-boot flow (INTEGRATOR tool, not a test): builds the T3 disk, boots the ISO in the
throwaway test VM (fresh NVRAM), prepares the fake Windows Boot Manager entry, runs option 2 (INSTALL nvme0n1), then
opens the installer shell and runs the given read-only commands (default: the FAT check of the new ESP and the tail of
the installer log), prints everything and powers off. The disk is deleted afterwards. Same safety rules as the tests.

  python -I C:\GB-x86\iso\test\diag_t3.py ["shell command" ...]
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I: no script dir on sys.path
import os
import sys

import common as C
import gbtests as TS
import mkdisks

DEFAULT = ["fsck.vfat -n /dev/nvme0n1p6; echo rc=$?",
           "grep -n -A12 'fsck' /run/gb/log/installer.log | tail -40",
           "grep -n 'ERROR\\|FAT check' /run/gb/log/installer.log | tail -20"]


def main(cmds):
    cmds = cmds or DEFAULT
    ctx = TS.Ctx()
    TS.preflight(ctx)
    res = C.Result("diag-t3")
    tw = C.mkdir(os.path.join(ctx.work, "diag_t3"))
    mkdisks.delete("t3")
    try:
        meta = mkdisks.t3(tw)
        raw, vmdk = mkdisks.paths("t3")
        size = os.path.getsize(raw)
        with TS.Session(ctx, "diag-t3", res, {"nvme0:0": vmdk}, fresh_nvram=True) as s:
            c = s.c
            c.shell_enter()
            s.nvram_prepare()
            c.shell_exit()
            kv, m2 = TS.install_flow(s, res, size)
            print("RESULT", kv)
            c.expect_prompt("main", 120, tolerate=(("journal.ack", "b"),))
            c.failures = [f for f in c.failures if "journal.ack" not in f]
            c.shell_enter()
            for cmd in cmds:
                st, out = c.sh(cmd, timeout=600)
                print("$ %s  (exit %d)\n%s" % (cmd, st, out))
            c.shell_exit()
    finally:
        mkdisks.delete("t3")
    for ok, t in res.checks:
        print(("PASS " if ok else "FAIL " if ok is False else "INFO ") + t)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
