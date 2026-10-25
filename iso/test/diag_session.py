r"""Ad-hoc diagnostic VM session (INTEGRATOR tool, not a test): boots the ISO in the throwaway test VM with a blank
1 GiB disk, opens the installer shell (tools s), runs the given read-only shell commands, prints their output and
powers off. Same safety rules as every other session (pre-start guard, D:\GB-iso\test\ only, kill-switch).

  python -I C:\GB-x86\iso\test\diag_session.py "cat /run/gb/log/init.log" "cat /sys/class/graphics/fb0/virtual_size"
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I: no script dir on sys.path
import sys

import common as C
import gbtests as TS
import mkdisks


def main(cmds):
    ctx = TS.Ctx()
    TS.preflight(ctx)
    res = C.Result("diag")
    mkdisks.delete("blank1g")
    raw, vmdk = mkdisks.RECIPES["blank1g"]()
    try:
        with TS.Session(ctx, "diag", res, {"nvme0:0": vmdk}) as s:
            c = s.c
            c.shell_enter()
            for cmd in cmds:
                st, out = c.sh(cmd, timeout=300)
                print("$ %s  (exit %d)\n%s" % (cmd, st, out))
            c.shell_exit()
    finally:
        mkdisks.delete("blank1g")
    for ok, t in res.checks:
        print(("PASS " if ok else "FAIL " if ok is False else "INFO ") + t)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
