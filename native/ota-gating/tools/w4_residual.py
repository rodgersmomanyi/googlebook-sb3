"""Residual of the pre-existing w4 'DIFF native vs the pre-W4 snapshot' failure, base vs patched: the paths that differ
from the snapshot minus the paths the (base / patched) test expects. Read-only."""
import contextlib, hashlib, importlib.util, io, json, os, sys
os.environ["GB_NO_BUILDER"] = "1"
sys.dont_write_bytecode = True
OG = r"C:\GB-build\native\ota-gating"


def load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s)
    with contextlib.redirect_stdout(io.StringIO()):
        s.loader.exec_module(m)
    return m


def ents(buf):
    out, o = {}, 0
    while True:
        f = [int(buf[o + 6 + 8 * i:o + 14 + 8 * i], 16) for i in range(13)]
        mode, fsz, nsz = f[1], f[6], f[11]
        name = buf[o + 110:o + 110 + nsz - 1].decode()
        o = (o + 110 + nsz + 3) & ~3
        data = buf[o:o + fsz]
        o = (o + fsz + 3) & ~3
        if name == "TRAILER!!!":
            return out
        out[name] = (mode, None if (mode & 0o170000) == 0o040000 else hashlib.sha256(data).hexdigest())


snap = json.load(open(r"C:\GB-build\native\dgpu\w4\evidence\integration\pre_w4_trees.json", encoding="utf-8"))
pre = {e[0]: (e[1], e[2]) for e in snap["native"]}
for tag, path in (("base", os.path.join(OG, "base", "GB-x86", "build_esp.py")),
                  ("patched", os.path.join(OG, "work", "GB-x86", "build_esp.py"))):
    be = load("be_" + tag, path)
    with contextlib.redirect_stdout(io.StringIO()):
        now = ents(be.native_cpio())
    d = {p for p in set(pre) | set(now) if pre.get(p) != now.get(p)}
    w5 = {"gbinit"}                                      # == w5_paths() of tests/w4/test_w4.py
    for p, _, _ in be.VNNI_FILES:
        parts = ("gbnative/" + p).split("/")
        w5.update("/".join(parts[:i]) for i in range(2, len(parts) + 1))
    if hasattr(be, "OTA_RC_FILE"):
        w5 |= {f"gbnative/{be.OTA_VALIDATED}", f"gbnative/{be.OTA_SCRIPT}", f"gbnative/{be.OTA_RC_FILE}",
               "gbnative/etc/selinux/odm_sepolicy.cil"}
    want = {"gbnative/" + be.GK_BIN_DEST, "gbnative/" + be.GK_VINTF_DEST, "gbnative/etc/vintf",
            "gbnative/etc/vintf/manifest", "gbnative/etc/init/gb.rc", "gbbringup.sh"} | w5
    print(f"{tag}: unexpected {sorted(d - want)}; expected but equal {sorted(want - d)}")
