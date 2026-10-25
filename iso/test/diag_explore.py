r"""Ad-hoc diagnostic VM session with options (INTEGRATOR tool, not a test). Boots the ISO in the throwaway test VM
with a blank 1 GiB disk (plus optional extra test disks below D:\GB-iso\test\disks\), optionally through GRUB's
command line, opens the installer shell (tools s), runs the given shell commands, prints their output and powers
off. Same safety rules as every other session (pre-start guard, D:\GB-iso\test\ only, kill-switch).

  python -I diag_explore.py [--vmx svga.maxWidth=3000 ...] [--grub "set gfxpayload=..." ...] [--hotkey s]
                            [--disk sata0:1=<name>] [--discover <list>] -- "shell command" ...
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I: no script dir on sys.path
import argparse
import sys

import common as C
import gbtests as TS
import mkdisks


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--vmx", action="append", default=[])
    ap.add_argument("--grub", action="append", default=[])
    ap.add_argument("--hotkey", default="s")
    ap.add_argument("--disk", action="append", default=[])
    ap.add_argument("--discover", default=None)
    ap.add_argument("--no-cd", action="store_true")
    ap.add_argument("cmds", nargs="*")
    a = ap.parse_args(argv)
    ctx = TS.Ctx()
    ctx.run_dir = C.mkdir(_os.path.join(C.LOGS, "diag"))
    TS.preflight(ctx)
    res = C.Result("diag")
    mkdisks.delete("blank1g")
    raw, vmdk = mkdisks.RECIPES["blank1g"]()
    disks = {"nvme0:0": vmdk}
    for d in a.disk:
        slot, name = d.split("=", 1)
        disks[slot] = mkdisks.paths(name)[1]
    extra = [tuple(x.split("=", 1)) for x in a.vmx]
    try:
        with TS.Session(ctx, "diag", res, disks, cd=not a.no_cd, hotkey=a.hotkey, vmx_extra=extra,
                        grub_cmds=a.grub or None, discover=a.discover) as s:
            c = s.c
            print("BOOT", s.boot_kv)
            print("PAYLOAD", s.payload_kv)
            c.shell_enter()
            for cmd in a.cmds:
                st, out = c.sh(cmd, timeout=900)
                print("$ %s  (exit %d)\n%s" % (cmd, st, out))
            c.shell_exit()
    finally:
        mkdisks.delete("blank1g")
    for ok, t in res.checks:
        print(("PASS " if ok else "FAIL " if ok is False else "INFO ") + t)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
