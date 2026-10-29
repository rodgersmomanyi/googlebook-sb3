r"""OTA gate (wave 5b) host tests: fingerprint gating of the /odm injections after a Google A/B OTA
(C:\GB-build\native\ota-gating\DESIGN.md). Nothing is written outside --scratch, the ota-gating evidence\ folder and
(with --builder / --secilc) the builder's /build/otagate-* scratch, which is removed again; lapis.bin is only read
(build_esp's import, the slot a/b init_boot ramdisks). C:\GB-x86 is only read.

  python -I -B test_ota_gating.py [--build-esp PATCHED] [--base UNPATCHED] [--scratch DIR] [--builder] [--secilc]

  BASE   base\GB-x86 == the CURRENT C:\GB-x86 files (sha256), so every comparison below is against today's pipeline
  FP     the validated build = ro.bootimage.build.fingerprint of the boot slot's init_boot ramdisk (independent parser),
         slot a (.271, before the OTA) differs; == the vendor fingerprint of inject\vnni's manifest (same build)
  FPF    ota_build_fingerprint on synthetic ramdisks (gzip/raw newc; CRLF, last assignment wins; missing, empty and
         unsafe values refuse)
  CIL    ota_safe_cil == the packed cil == tests\sepolicy\cil_transform.py; the stored secilc inputs == today's cil;
         refusals; the builder secilc evidence (identical policy; a missing type fails the plain form, not the wrapped)
  TREE   both trees: ota_validated, gb_otagate.sh, the gate's own rc file etc/init/gb_otagate.rc; GBINIT identical
  DIFF   patched vs unpatched trees: only gbinit and the cil change (gb.rc byte-identical), exactly 3 new files (+1 dir
         in the VM tree); GBINIT == the old one plus exactly the two insertions
  CHECK  _check_otagate passes on both patched trees and REFUSES the unpatched trees (the gate removed)
  MUT    mutations of a patched tree (gate/token/rm/order/file/mode/rc/trigger/classes/cil/state) are each refused
  MODMUT mutated copies of the patched build_esp.py (gate removed from GBINIT, rm removed, an unclassified graft) do not
         even build (module import fails)
  CLASS  the classification table (evidence\classification.tsv) with stock-partition evidence; no dead pattern except
         the C-2 private sonames; the C-2 (fixprep keymint-wifi) native graft names are classified
  NOTE   notification title/text limits of INTERFACES 12.1 (worst case); the two services and their identities
  VI     verify_image (a copy pointing at the patched build_esp) on the in-memory user-VM ESP and native USB ESP: OK
  APPLY  ota_gating.patch: git apply --check on a fresh copy of the CURRENT C:\GB-x86 files; applied == the tested
         copies; build_esp.py hunks are insertions only
  ORDER  tools\apply_order.py: ours and every fixprep build_esp.py patch apply in either order to the same file
  BUILDER (--builder) gate_harness.sh on the builder: GBINIT under the shipped busybox with the REAL slot a/b ramdisk
         props, gb_otagate.sh (both services), and the REAL gb_notify.sh delivering the message
  SECILC (--secilc) re-run tests\sepolicy\run_secilc.sh on the builder (lapis's own secilc)
Prints ok/FAIL lines and "OTA RESULT: OK" or "OTA RESULT: <n> failure(s)" (exit 0 / 1).
"""
import argparse
import contextlib
import fnmatch
import gzip
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

os.environ["GB_NO_BUILDER"] = "1"
sys.dont_write_bytecode = True
OG = r"C:\GB-build\native\ota-gating"
X86 = r"C:\GB-x86"
LAPIS = os.path.join(X86, "lapis.bin")
SSH = ["ssh", "-i", "C:/GB-build/builder_key", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=15",
       "builder@192.168.115.132"]
SUPER = "11111111-2222-4333-8444-555555555555"
PATCHED_FILES = ["build_esp.py", "tests/w3a/test_w3a.py", "tests/w4/test_w4.py", "tests/w5/test_w5.py"]
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f"  [{str(detail)[:400]}]" if detail and not cond else ""), flush=True)
    return bool(cond)


def raises(fn, exc=AssertionError):
    try:
        fn()
    except exc as e:
        return str(e) or type(e).__name__
    return None


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(m)
    return m


def cpio_entries(buf):
    out, o = [], 0
    while True:
        f = [int(buf[o + 6 + 8 * i:o + 14 + 8 * i], 16) for i in range(13)]
        mode, fsz, nsz = f[1], f[6], f[11]
        name = buf[o + 110:o + 110 + nsz - 1].decode()
        o = (o + 110 + nsz + 3) & ~3
        data = buf[o:o + fsz]
        o = (o + fsz + 3) & ~3
        if name == "TRAILER!!!":
            return out
        out.append((name, mode, data if (mode & 0o170000) != 0o040000 else None))


def newc(files):
    """A plain newc cpio of {name: bytes} (regular files, 0644) for synthetic ramdisks."""
    out, ino = bytearray(), 0
    for name, data in list(files.items()) + [("TRAILER!!!", b"")]:
        ino += 1
        nb = name.encode() + b"\0"
        hdr = "070701" + "".join(f"{v:08x}" for v in (ino, 0o100644 if name != "TRAILER!!!" else 0, 0, 0, 1, 0, len(data),
                                                      0, 0, 0, 0, len(nb), 0))
        out += hdr.encode() + nb
        out += b"\0" * ((-len(out)) % 4)
        out += data
        out += b"\0" * ((-len(out)) % 4)
    return bytes(out)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def indep_fingerprint(rd):
    """Independent of build_esp: legacy LZ4 -> newc -> system/etc/ramdisk/build.prop -> last fingerprint line."""
    import lz4.block
    assert rd[:4] == b"\x02\x21\x4c\x18"
    buf, o = bytearray(), 4
    while o + 4 <= len(rd):
        if rd[o:o + 4] == b"\x02\x21\x4c\x18":
            o += 4
            continue
        n = struct.unpack_from("<I", rd, o)[0]
        if not n or o + 4 + n > len(rd):
            break
        buf += lz4.block.decompress(rd[o + 4:o + 4 + n], uncompressed_size=8 << 20)
        o += 4 + n
    bp = {n: d for n, m, d in cpio_entries(bytes(buf))}["system/etc/ramdisk/build.prop"]
    return [ln.split(b"=", 1)[1].strip() for ln in bp.splitlines() if ln.startswith(b"ro.bootimage.build.fingerprint=")][-1]


def gate_of(gi):
    g = gi[gi.index(b"if [ -f /odm/etc/gb/ota_validated ]; then\n"):]
    return g[:g.index(b"\nfi\n") + 4]


# ============================================================================================== sections
def base_(a):
    root = os.path.dirname(a.base)
    for f in PATCHED_FILES:
        b = os.path.join(root, *f.split("/"))
        cur = os.path.join(X86, *f.split("/"))
        check(f"BASE {f}: base copy == the current C:\\GB-x86 file ({sha(open(cur, 'rb').read())[:12]}...)",
              os.path.isfile(b) and open(b, "rb").read() == open(cur, "rb").read())


def fp(be, xs):
    gen_a, _, _ = xs.slot_ramdisks("a", LAPIS)
    gen_b, _, _ = xs.slot_ramdisks("b", LAPIS)
    fa, fb = indep_fingerprint(gen_a), indep_fingerprint(gen_b)
    check(f"FP the boot slot is {be.SLOT} and OTA_FP == its init_boot ramdisk fingerprint (independent parser)",
          be.SLOT == "b" and be.OTA_FP == fb == be.ota_build_fingerprint(gen_b), (be.SLOT, be.OTA_FP, fb))
    check("FP slot a (the build before the .271 -> .276 OTA) has another fingerprint: a real 'other build'",
          fa != fb and b"CL3B.260622.271" in fa and b"CL3B.260622.276" in fb, (fa, fb))
    check("FP ota_build_fingerprint(slot a) == the independent parser", be.ota_build_fingerprint(gen_a) == fa)
    vfp = {}
    for n in os.listdir(os.path.join(X86, "inject", "vnni")):
        m = json.load(open(os.path.join(X86, "inject", "vnni", n, "manifest.json"), encoding="utf-8"))
        vfp = m["fingerprints"]
    check("FP the validated ramdisk fingerprint == ro.vendor.build.fingerprint read from vendor_b by vnni_prepare "
          "(init_boot and vendor come from the same build)", vfp.get("ro.vendor.build.fingerprint", "").encode() == fb, vfp)
    check("FP ota_validated bytes = 'ro.bootimage.build.fingerprint=<fp>\\n'",
          be.OTA_VALIDATED_BYTES == b"ro.bootimage.build.fingerprint=" + fb + b"\n")
    return fa.decode(), fb.decode()


def fpf(be):
    P = "system/etc/ramdisk/build.prop"
    F = b"A/b/c:17/ID.1/2:user/release-keys"
    good = newc({P: b"# x\nro.bootimage.build.id=ID.1\nro.bootimage.build.fingerprint=" + F + b"\n"})
    check("FPF plain newc ramdisk", be.ota_build_fingerprint(good) == F)
    check("FPF gzip newc ramdisk", be.ota_build_fingerprint(gzip.compress(good)) == F)
    check("FPF CRLF and two assignments: the last wins, CR dropped",
          be.ota_build_fingerprint(newc({P: b"ro.bootimage.build.fingerprint=X/1\r\nro.bootimage.build.fingerprint="
                                         + F + b"\r\n"})) == F)
    for what, rd in (("no build.prop", newc({"init": b"x"})),
                     ("no fingerprint line", newc({P: b"ro.bootimage.build.id=1\n"})),
                     ("an empty value", newc({P: b"ro.bootimage.build.fingerprint=\n"})),
                     ("a space", newc({P: b"ro.bootimage.build.fingerprint=a b\n"})),
                     ("a $", newc({P: b"ro.bootimage.build.fingerprint=a$b\n"})),
                     ("a quote", newc({P: b"ro.bootimage.build.fingerprint=a'b\n"})),
                     ("a backslash", newc({P: b"ro.bootimage.build.fingerprint=a\\b\n"})),
                     ("an indented line only", newc({P: b" ro.bootimage.build.fingerprint=" + F + b"\n"})),
                     ("not a cpio", b"garbage" * 40)):
        check(f"FPF refuses {what}", raises(lambda: be.ota_build_fingerprint(rd)) is not None)


def cil(be):
    raw = open(os.path.join(X86, "inject", "odm_sepolicy.cil"), "rb").read()
    sd = os.path.join(OG, "tests", "sepolicy")
    tf = load("cil_transform", os.path.join(sd, "cil_transform.py"))
    w = be.ota_safe_cil(raw)
    types = re.findall(rb"^\(typepermissive ([A-Za-z0-9_-]+)\)\r?$", raw, re.M)
    check("CIL build_esp.ODMCIL == ota_safe_cil(inject\\odm_sepolicy.cil) == tests\\sepolicy\\cil_transform.py",
          be.ODMCIL == w == tf.ota_safe_cil(raw) and be.ODMCIL_RAW == raw)
    check(f"CIL every one of the {len(types)} types once, each in its own optional block, nothing else",
          len(types) == 2694 and w.count(b"\n") == len(types)
          and all(w.count(b"(optional gb_ota_" + t + b" (typepermissive " + t + b"))\n") == 1 for t in types))
    rd = lambda n: open(os.path.join(sd, n), "rb").read()
    miss = b"gb_ota_missing_type"
    check("CIL the stored secilc inputs are today's cil: odm_orig == inject\\odm_sepolicy.cil, odm_wrapped == its "
          "OTA-robust form, the *_missing pair == the same + one unknown type",
          rd("odm_orig.cil") == raw and rd("odm_wrapped.cil") == w
          and rd("odm_orig_missing.cil") == raw + b"\n(typepermissive " + miss + b")\n"
          and rd("odm_wrapped_missing.cil") == w + b"(optional gb_ota_" + miss + b" (typepermissive " + miss + b"))\n")
    for what, bad in (("an allow rule", raw + b"\n(allow a b (file (read)))\n"), ("a repeated type", raw + b"\n" +
                      b"(typepermissive " + types[0] + b")\n"), ("an empty file", b"\n\r\n"),
                      ("the wrapped form again (not idempotent: fail closed)", w)):
        check(f"CIL ota_safe_cil refuses {what}", raises(lambda: be.ota_safe_cil(bad)) is not None)
    ev = os.path.join(OG, "evidence", "sepolicy_secilc.txt")
    t = open(ev, encoding="utf-8").read() if os.path.exists(ev) else ""
    check("CIL evidence (builder, lapis .276 secilc, init's arguments): plain == wrapped policy, a missing type fails the "
          "plain form (rc 254) and not the wrapped one (identical policy)",
          "RESULT orig == wrapped: IDENTICAL" in t and "RESULT wrapped == wrapped+missing: IDENTICAL" in t
          and "RESULT orig+missing: FAILED" in t and "Failed to resolve typepermissive statement" in t
          and sha(raw) in t and sha(w) in t, ev)


def trees(be, nat_ents):
    vm = {p: (m, x) for p, m, x in be._VM_ENTRIES}
    nat = {p: (m, x) for p, m, x in nat_ents}
    for tr, d in (("gbstage", vm), ("gbnative", nat)):
        check(f"TREE {tr}: etc/gb/ota_validated 0644 == the validated fingerprint line",
              d.get(f"{tr}/etc/gb/ota_validated") == (0o100644, be.OTA_VALIDATED_BYTES))
        check(f"TREE {tr}: gb_otagate.sh 0755, etc/init/gb_otagate.rc 0644 == OTA_RC, no ota_state packed",
              d.get(f"{tr}/gb_otagate.sh") == (0o100755, be.GB_OTAGATE_SH) and
              d.get(f"{tr}/etc/init/gb_otagate.rc") == (0o100644, be.OTA_RC) and f"{tr}/etc/gb/ota_state" not in d)
        check(f"TREE {tr}: /odm/vnni still packed (removal happens only at boot, on another build)",
              any(p.startswith(f"{tr}/vnni/") for p in d))
        check(f"TREE {tr}: no other rc file names the gate (gb.rc untouched)",
              not any(s in x for p, (m, x) in d.items() if p.startswith(f"{tr}/etc/init/") and x is not None
                      and p != f"{tr}/etc/init/gb_otagate.rc" for s in (b"gb_otagate", b"ota_gate")))
    check("TREE GBINIT identical in both cpios", vm["gbinit"][1] == nat["gbinit"][1] == be.GBINIT)
    rc = be.OTA_RC
    check("TREE the rc trigger: boot_completed AND the unvalidated property (nothing runs on the validated build), "
          "starting both services",
          rc.count(b"\non property:sys.boot_completed=1 && property:ro.vendor.gb.ota_gate=unvalidated\n"
                   b"    start gb_otagate\n    start gb_otagate_post\n") == 1 and rc.count(b"\non ") == 1)
    check("TREE gb_otagate runs as root (kmsg, the queue); gb_otagate_post as user shell (gb_notify's identity); both "
          "disabled oneshot shell-domain services",
          b"service gb_otagate /system/bin/sh /odm/gb_otagate.sh\n    user root\n    group root shell\n    seclabel "
          b"u:r:shell:s0\n    oneshot\n    disabled\n" in rc and
          b"service gb_otagate_post /system/bin/sh /odm/gb_otagate.sh post\n    user shell\n    group shell\n    seclabel "
          b"u:r:shell:s0\n    oneshot\n    disabled\n" in rc and rc.count(b"\nservice ") == 2 and rc.startswith(b"#"))


def diff(be, base, nat_p, nat_b):
    for tr, p_ents, b_ents in (("gbstage", be._VM_ENTRIES, base._VM_ENTRIES), ("gbnative", nat_p, nat_b)):
        P = {p: (m, x) for p, m, x in p_ents}
        B = {p: (m, x) for p, m, x in b_ents}
        added, removed = set(P) - set(B), set(B) - set(P)
        changed = {p for p in set(P) & set(B) if P[p] != B[p]}
        want_add = {f"{tr}/etc/gb/ota_validated", f"{tr}/gb_otagate.sh", f"{tr}/etc/init/gb_otagate.rc"} | (
            {"gbstage/etc/gb"} if tr == "gbstage" else set())
        check(f"DIFF {tr}: added exactly {sorted(want_add)}", added == want_add, sorted(added))
        check(f"DIFF {tr}: nothing removed", not removed, sorted(removed))
        check(f"DIFF {tr}: changed exactly gbinit and the SELinux cil (gb.rc and everything else byte-identical)",
              changed == {"gbinit", f"{tr}/etc/selinux/odm_sepolicy.cil"}, sorted(changed))
        cp = f"{tr}/etc/selinux/odm_sepolicy.cil"
        check(f"DIFF {tr}: the cil == ota_safe_cil(the unpatched cil)", P[cp][1] == be.ota_safe_cil(B[cp][1]))
    gi = be.GBINIT
    gate = gate_of(gi)
    rest = gi.replace(be.OTA_GBINIT_TOKENS, b"", 1).replace(gate, b"", 1)
    check("DIFF GBINIT == the unpatched GBINIT plus exactly two insertions (token scan, gate block); no line changed",
          rest == base.GBINIT and gi.count(be.OTA_GBINIT_TOKENS) == 1 and be.OTA_GBINIT_UNVALIDATED in gate, len(gate))
    check("DIFF the VNNI kill-switch edits of W5 are still literally in GBINIT (build_esp's own assertion)",
          all(gi.count(n) == 1 for _o, n in be.VNNI_GBINIT_EDITS))
    check("DIFF the VM tree record changes (expected: a new user-VM preview record)",
          be.vm_tree_record() != base.vm_tree_record(), be.vm_tree_record().decode().split()[0])
    print(f"     VM tree record: base {base.vm_tree_record().decode().split()[0]} -> patched "
          f"{be.vm_tree_record().decode().split()[0]}")
    return gate


def chk(be, base, nat_p, nat_b):
    for tr, ents in (("gbstage", be._VM_ENTRIES), ("gbnative", nat_p)):
        check(f"CHECK _check_otagate passes on the patched {tr} tree", raises(lambda: be._check_otagate(ents, tr, tr)) is None,
              raises(lambda: be._check_otagate(ents, tr, tr)))
    for tr, ents in (("gbstage", base._VM_ENTRIES), ("gbnative", nat_b)):
        r = raises(lambda: be._check_otagate(ents, tr, tr))
        check(f"CHECK _check_otagate REFUSES the unpatched {tr} tree (the gate removed)", r is not None, r)


def mut(be, gate, nat_p):
    ents = list(be._VM_ENTRIES)
    tr, rcp, orc = "gbstage", "gbstage/etc/init/gb.rc", "gbstage/etc/init/gb_otagate.rc"

    def with_(path, fn, e=None):
        return [(p, m, fn(x) if p == path else x) for p, m, x in (e or ents)]

    def gbi(fn):
        return with_("gbinit", fn)
    late = lambda x: x.replace(gate, b"", 1).replace(b"exec /init\n", b"exec /init\n" + gate)
    trig = b"on property:sys.boot_completed=1 && property:ro.vendor.gb.ota_gate=unvalidated\n"
    muts = [
        ("GBINIT without the gate block", gbi(lambda x: x.replace(gate, b"", 1))),
        ("GBINIT without the token scan", gbi(lambda x: x.replace(be.OTA_GBINIT_TOKENS, b"", 1))),
        ("the unvalidated branch without rm -rf /odm/vnni", gbi(lambda x: x.replace(b"    /busybox rm -rf /odm/vnni\n", b"", 1))),
        ("the unvalidated branch without the property", gbi(lambda x: x.replace(
            b"    echo \"ro.vendor.gb.ota_gate=unvalidated\" >> /odm/etc/build.prop\n", b"", 1))),
        ("the gate after exec /init", gbi(late)),
        ("the gate before the /odm copy", gbi(lambda x: x.replace(gate, b"", 1).replace(
            b"/busybox cp -a $SRC/. /odm/\n", gate + b"/busybox cp -a $SRC/. /odm/\n", 1))),
        ("the gate twice", gbi(lambda x: x.replace(gate, gate + gate, 1))),
        ("the token scan after /proc is unmounted", gbi(lambda x: x.replace(be.OTA_GBINIT_TOKENS, b"", 1).replace(
            b"/busybox mount -t tmpfs tmpfs /odm\n", be.OTA_GBINIT_TOKENS + b"/busybox mount -t tmpfs tmpfs /odm\n", 1))),
        ("ota_validated missing", [e for e in ents if e[0] != "gbstage/etc/gb/ota_validated"]),
        ("ota_validated of another build", with_("gbstage/etc/gb/ota_validated", lambda x: x.replace(b"276.R4", b"271.R1"))),
        ("ota_validated 0755", [(p, 0o100755 if p == "gbstage/etc/gb/ota_validated" else m, x) for p, m, x in ents]),
        ("gb_otagate.sh missing", [e for e in ents if e[0] != "gbstage/gb_otagate.sh"]),
        ("gb_otagate.sh changed", with_("gbstage/gb_otagate.sh", lambda x: x.replace(b"exit 0\n", b"reboot\n"))),
        ("the gate's rc file missing", [e for e in ents if e[0] != orc]),
        ("the gate's rc file 0755", [(p, 0o100755 if p == orc else m, x) for p, m, x in ents]),
        ("the rc trigger on every boot", with_(orc, lambda x: x.replace(trig, b"on property:sys.boot_completed=1\n", 1))),
        ("gb.rc also starting gb_otagate (a second trigger outside the gate's rc file)",
         with_(rcp, lambda x: x + b"\non property:sys.boot_completed=1\n    start gb_otagate\n")),
        ("gb.rc setting the gate property", with_(rcp, lambda x: x + b"\non boot\n    setprop ro.vendor.gb.ota_gate x\n")),
        ("an unclassified /odm/vup library (a new build-specific graft)", ents + [("gbstage/vup/lib64/libnew.so", 0o100644, b"x")]),
        ("an unclassified gb.rc bind below /odm", with_(rcp, lambda x: x + b"\non init\n    mount none /odm/x.xml "
                                                                            b"/vendor/etc/x.xml bind\n")),
        ("an unclassified bind in another /odm rc file", ents + [("gbstage/etc/init/gb_other.rc", 0o100644,
                                                                  b"on init\n    mount none /odm/y.xml /vendor/etc/y.xml bind\n")]),
        ("the plain (not OTA-robust) cil", with_("gbstage/etc/selinux/odm_sepolicy.cil", lambda x: be.ODMCIL_RAW)),
        ("ota_state packed", ents + [("gbstage/etc/gb/ota_state", 0o100644, b"state=validated\n")]),
    ]
    for what, m in muts:
        r = raises(lambda: be._check_otagate(m, tr, "mut"))
        check(f"MUT _check_otagate refuses: {what}", r is not None, "accepted")
    nm = [e for e in nat_p if e[0] != "gbnative/etc/init/gb_otagate.rc"]
    check("MUT _check_otagate refuses: the native tree without the gate's rc file",
          raises(lambda: be._check_otagate(nm, "gbnative", "mut")) is not None, "accepted")
    # a new "skip" class without the matching GBINIT removal
    saved = be.OTA_CLASSES
    try:
        be.OTA_CLASSES = (("vup/lib64/libgbm.so", "skip", "x"),) + saved
        r = raises(lambda: be._check_otagate(ents, tr, "mut"))
        check("MUT _check_otagate refuses: a new skip class that GBINIT does not remove", r is not None, "accepted")
    finally:
        be.OTA_CLASSES = saved


def modmut(patched, gate, scratch):
    """Mutated copies of the patched build_esp.py: each must fail to build (import raises AssertionError)."""
    src = open(patched, "rb").read()
    gate_lit = (b'          b"if [ -f /odm/etc/gb/ota_validated ]; then\\n"\n')
    i = src.index(gate_lit)
    j = src.index(b'          b"fi\\n"\n', i) + len(b'          b"fi\\n"\n')
    rm_lit = b'\n          b"    /busybox rm -rf /odm/vnni\\n"\n'                  # the GBINIT literal line
    rm_const = b'\n                          b"    /busybox rm -rf /odm/vnni\\n"\n'    # in OTA_GBINIT_UNVALIDATED
    anchor = b"_VM_ENTRIES = _dedupe("
    cases = [
        ("the gate block removed from GBINIT (the OTA_GBINIT_* literals kept)", src[:i] + src[j:]),
        ("rm -rf /odm/vnni removed from GBINIT and from OTA_GBINIT_UNVALIDATED (gate no longer skips)",
         src.replace(rm_lit, b"\n", 1).replace(rm_const, b"\n", 1) if src.count(rm_lit) == 1 and src.count(rm_const) == 1
         else b"bad mutation"),
        ("an unclassified /odm/vup graft added to the VM tree",
         src.replace(anchor, b'_add_entry(_entries, "gbstage/vup/lib64/libgrafted.so", b"x", 0o100644)\n' + anchor, 1)),
    ]
    for what, body in cases:
        p = os.path.join(scratch, "be_modmut.py")
        open(p, "wb").write(body)
        try:
            r = raises(lambda: load("be_modmut", p), Exception)
        finally:
            os.remove(p)
        check(f"MODMUT the patched build_esp.py with {what} does not build", r is not None and body != src
              and body != b"bad mutation", r)


def classes(be, nat_ents):
    stock = {}
    ev = os.path.join(OG, "evidence", "stock_scan_slot_b.tsv")
    if os.path.exists(ev):
        for ln in open(ev, encoding="utf-8"):
            f = ln.rstrip("\n").split("\t")
            if len(f) >= 5 and f[0] in ("vup-file", "vup-symlink"):
                stock[(f[1], f[2])] = f[4]
            elif len(f) >= 5 and f[0] == "bind":
                stock[(f[1], f[2], f[3])] = f[4]
    rows, hit = [], set()

    def cls(rel):
        for p, c, l in be.OTA_CLASSES:
            if fnmatch.fnmatchcase(rel, p):
                hit.add(p)
                return p, c, l
        return None, None, None
    for tr, ents in (("gbstage", be._VM_ENTRIES), ("gbnative", nat_ents)):
        d = {p: (m, x) for p, m, x in ents}
        rels = sorted(p[len(tr) + 1:] for p, (m, x) in d.items() if x is not None and p.startswith(
            tuple(f"{tr}/{s}/" for s in ("vup", "vnni", "etc/selinux", "etc/vintf"))))
        rc = b"".join(x for p, (m, x) in sorted(d.items()) if p.startswith(f"{tr}/etc/init/") and x).decode()
        binds = re.findall(r"^\s*mount none (/odm/\S+) (\S+) bind$", rc, re.M)
        for r in rels:
            p, c, l = cls(r)
            st = stock.get((tr, r), "")
            rows.append((tr, "/odm/" + r, c, l, ("shadows stock " + st.split()[0]) if st and st != "ABSENT" else
                         ("new path" if st == "ABSENT" else "")))
        for src, dst in binds:
            p, c, l = cls(src[len("/odm/"):])
            st = stock.get((tr, src, dst), "")
            rows.append((tr, f"bind {src} -> {dst}", c, l, ("over stock " + st.split()[0]) if st and st != "ABSENT"
                         else ("target absent in .276" if st == "ABSENT" else "")))
    os.makedirs(os.path.join(OG, "evidence"), exist_ok=True)
    with open(os.path.join(OG, "evidence", "classification.tsv"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("# tree\tinjection\tclass\tlabel\tstock (slot b, .276)\n")
        for r in rows:
            fh.write("\t".join(str(x) for x in r) + "\n")
    check("CLASS every injection has a class (written to evidence\\classification.tsv)", all(r[2] for r in rows),
          [r for r in rows if not r[2]][:5])
    dead = [p for p, c, l in be.OTA_CLASSES if p not in hit and p not in be.OTA_KM_NATIVE]
    check("CLASS no dead pattern in OTA_CLASSES (each matches a packed file or bind source; the C-2 private sonames "
          "excepted until fixprep keymint-wifi is integrated)", not dead, dead)
    check("CLASS the only skip class is the VNNI patch (/odm/vnni)",
          {p for p, c, l in be.OTA_CLASSES if c == "skip"} == {"vnni/*"})
    sh = [r for r in rows if r[4].startswith(("shadows stock", "over stock")) and r[2] != "skip" and r[3] not in
          ("stub", "mask", "config", "KeyMint graft", "VM graphics", "fstab", "system_ext VINTF", "own")]
    check("CLASS every injection over a stock Google file is gated (skip), a stub/mask, our config, or a kept "
          "boot-critical graft", not sh, sh[:5])
    n = {c: sum(1 for r in rows if r[2] == c) for c in ("skip", "keep", "always")}
    print(f"     classes: {n} ({len(rows)} rows)")
    other, odm_odd = [], []
    for tr, ents in (("gbstage", be._VM_ENTRIES), ("gbnative", nat_ents)):
        for p, m, x in ents:
            if p.startswith(f"{tr}/etc/init/") and x:
                for ln in x.decode().splitlines():
                    w = ln.split()
                    if w[:1] == ["mount"] and not (len(w) > 2 and w[2].startswith("/odm/")):
                        other.append((tr, " ".join(w)))
                    elif w[:1] == ["mount"] and not re.fullmatch(r"mount none /odm/\S+ \S+ bind", " ".join(w)):
                        odm_odd.append((tr, " ".join(w)))
    check("CLASS every rc mount of a /odm source is a plain 'mount none /odm/<src> <dst> bind' (the form the class "
          "check reads)", not odm_odd, odm_odd[:4])
    allowed = re.compile(r"mount none /vendor /odm/vlower bind|mount overlay gbkm /vendor lowerdir=/odm/vlower,"
                         r"upperdir=/odm/vup,workdir=/odm/vwk|mount none none /(system|vendor)/lib64/libz\.so remount bind ro")
    check("CLASS every rc mount whose source is not below /odm is the /vendor overlay setup or a VNNI read-only remount "
          "(so the classified bind sources cover every Google file an rc bind replaces)",
          other and all(allowed.fullmatch(l) for _t, l in other), [o for o in other if not allowed.fullmatch(o[1])][:4])
    # C-2 (fixprep keymint-wifi 02-build_esp.patch): the native tree's graft libraries become lib<x>_gbkm16.so
    ren = {f"gbnative/vup/lib64/{n}": f"gbnative/vup/lib64/{n[:-3]}_gbkm16.so" for n in be._KMLIBS}
    c2 = [(ren.get(p, p), m, x) for p, m, x in nat_ents]
    check("CLASS with the C-2 private sonames (lib<x>_gbkm16.so, fixprep keymint-wifi) the native tree still passes "
          "_check_otagate, each classed keep/KeyMint graft",
          raises(lambda: be._check_otagate(c2, "gbnative", "c2")) is None and
          all(cls(v[len("gbnative/"):])[1:] == ("keep", "KeyMint graft") for v in ren.values()) and
          sum(1 for p, m, x in c2 if p in ren.values()) == len(be._KMLIBS),
          raises(lambda: be._check_otagate(c2, "gbnative", "c2")))
    c2bad = [(p.replace("_gbkm16.so", "_gbkm17.so"), m, x) for p, m, x in c2]
    check("CLASS ... and any other new name for them is refused (unclassified)",
          raises(lambda: be._check_otagate(c2bad, "gbnative", "c2")) is not None)
    fx = r"C:\GB-build\native\fixprep\keymint-wifi\patches\02-build_esp.patch"
    if os.path.exists(fx):
        t = open(fx, encoding="utf-8").read()
        check("CLASS the C-2 name rule matches fixprep keymint-wifi's patch ('lib64/' + n[:-3] + '_gbkm16.so')",
              '"lib64/" + n[:-3] + "_gbkm16.so" for n in _KMLIBS' in t)


def note(be):
    worst = be.OTA_TEXT.replace(b"$B", b"x" * 24).replace(b"$V", b"x" * 24)
    check("NOTE title <= 40 and the worst-case text <= 200 printable ASCII (gb_notify's limits)",
          len(be.OTA_TITLE) <= 40 and len(worst) <= 200 and all(32 <= c <= 126 for c in worst + be.OTA_TITLE),
          (len(be.OTA_TITLE), len(worst)))
    sh = be.GB_OTAGATE_SH
    check("NOTE tag gb_update (in gb_notify's fixed tag set) and the queue format of INTERFACES 12.1",
          b"tag=gb_update" in sh and b"$Q/$ms-$$-1.msg" in sh and b'"$f.tmp"' in sh and b"setprop vendor.gb.notify" in sh)
    post = sh[sh.index(b'if [ "${1:-}" = post ]; then\n'):]
    post = post[:post.index(b"\nfi\n") + 4]
    check("NOTE the queue is used only where gb_notify ships and its queue is a real directory (qok); the post mode "
          "(user shell) posts everywhere else and exits before the kmsg line and the queue code",
          b'qok() { [ -f "$R/odm/vup/bin/gb_notify.sh" ] && [ -d "$Q" ] && [ ! -L "$Q" ]; }\n' in sh
          and b"  qok && exit 0\n" in post and b"cmd notification post" in post and sh.count(b"if qok; then\n") == 1
          and sh.index(post) < sh.index(b'log1 "UNVALIDATED build') and b"setprop" not in post and b".msg" not in post)
    check("NOTE nothing in gb_otagate.sh changes the system (no reboot/stop/start/setprop other than vendor.gb.notify)",
          not re.search(rb"\b(reboot|stop|start|svc|pm|am|settings|rm -rf|mount|insmod)\b", sh.replace(b"rm -f \"$f.tmp\"", b""))
          and all(sh[m.end():].startswith(b"vendor.gb.notify ") for m in re.finditer(rb"setprop ", sh)))


def vi_run(be, nat_cpio, scratch):
    vip = os.path.join(scratch, "verify_image_otagate.py")
    src = open(os.path.join(X86, "verify_image.py"), encoding="utf-8").read()
    old = '"build_esp": r"C:\\GB-x86\\build_esp.py",'
    assert src.count(old) == 1
    open(vip, "w", encoding="utf-8", newline="\n").write(src.replace(old, f'"build_esp": r"{be.__file__}",'))
    bu = load("build_usb_otagate", os.path.join(X86, "build_usb.py"))
    rec = be.vm_tree_record().split()[0].decode()
    lf = be.slot_files(be.CMDLINE_VM, be.INJECT_CPIO, "Googlebook lapis x86")
    nf = be.slot_files(bu.native_cmdline(bu.USB_BOOT_DEVICES, "usb", part_uuid=SUPER), nat_cpio, bu.TITLE_NATIVE,
                       timeout=4, native=True)
    for label, files, size, mf, args in (
            ("the user-VM ESP (192 MiB, as GB_WRITE_LAPIS=1 build_esp.py would write it)", lf, be.ESP_BYTES,
             be.vm_min_free(lf), ["--expect-tree", "vm", "--esp-size", str(be.ESP_BYTES), "--expect-inject-sha256", rec,
                                   "--krel", r"C:\GB-build\kernel\k15_release.txt"]),
            ("the native USB ESP (512 MiB)", nf, 512 << 20, be.native_min_free(nf),
             ["--boot", "usb", "--super-guid", SUPER, "--expect-tree", "native"])):
        with contextlib.redirect_stdout(io.StringIO()):
            img = be.make_esp(files, size, min_free=mf)
        path = os.path.join(scratch, "esp_otagate.img")
        with open(path, "wb") as f:
            f.write(img)
        p = subprocess.run([sys.executable, "-I", "-B", vip, "--esp", path] + args, capture_output=True, text=True,
                           timeout=900)
        out = p.stdout + p.stderr
        open(os.path.join(OG, "evidence", f"verify_image_{'vm' if 'VM' in label else 'native'}.txt"), "w",
             encoding="utf-8", newline="\n").write(out)
        check(f"VI verify_image (patched build_esp) on {label}: RESULT OK",
              p.returncode == 0 and out.rstrip().endswith("RESULT: OK"), out[-600:])
        os.remove(path)
    os.remove(vip)


def apply_(scratch):
    patch = os.path.join(OG, "ota_gating.patch")
    if not check("APPLY ota_gating.patch exists", os.path.exists(patch)):
        return
    txt = open(patch, encoding="utf-8").read()
    names = sorted(set(re.findall(r"^\+\+\+ b/(\S+)", txt, re.M)))
    check(f"APPLY the patch touches exactly {PATCHED_FILES}", names == sorted(PATCHED_FILES), names)
    git = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "apply"]        # this host has core.autocrlf=true
    d = os.path.join(scratch, "fresh")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    for n in names:
        os.makedirs(os.path.dirname(os.path.join(d, n)) or d, exist_ok=True)
        shutil.copy2(os.path.join(X86, *n.split("/")), os.path.join(d, n))
    r = subprocess.run(git + ["--check", "-v", patch], cwd=d, capture_output=True, text=True)
    check(f"APPLY git apply --check on a fresh copy of the current C:\\GB-x86 {names}", r.returncode == 0,
          r.stdout + r.stderr)
    r2 = subprocess.run(git + [patch], cwd=d, capture_output=True, text=True)
    same = r2.returncode == 0 and all(open(os.path.join(d, n), "rb").read() == open(
        os.path.join(OG, "work", "GB-x86", *n.split("/")), "rb").read() for n in names)
    check("APPLY the applied result == the tested patched copies (work\\GB-x86), byte for byte", same, r2.stderr)
    shutil.rmtree(d, ignore_errors=True)
    be_part = txt[txt.index("diff --git a/build_esp.py"):]
    be_part = be_part[:be_part.index("\ndiff --git", 1) + 1] if "\ndiff --git" in be_part[1:] else be_part
    removed = [l for l in be_part.splitlines() if l.startswith("-") and not l.startswith("---")]
    check(f"APPLY build_esp.py: {be_part.count('@@ -')} hunks, insertions only (no existing line changed or removed)",
          not removed, removed[:3])


def order(scratch):
    p = subprocess.run([sys.executable, "-I", "-B", os.path.join(OG, "tools", "apply_order.py"),
                        os.path.join(scratch, "applyorder")], capture_output=True, text=True, timeout=600)
    out = p.stdout + p.stderr
    open(os.path.join(OG, "evidence", "apply_order.txt"), "w", encoding="utf-8", newline="\n").write(out)
    for ln in out.splitlines():
        print("     | " + ln)
    check("ORDER ota_gating.patch and every fixprep build_esp.py patch apply in either order to the same file",
          p.returncode == 0 and "APPLY-ORDER RESULT: OK" in out and out.count("\nok   ") >= 2, out[-400:])


def builder(be, base, nat_ents, fa, fb, scratch, gate):
    d = os.path.join(scratch, "harness_in")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    nat = {p: x for p, m, x in nat_ents}
    vm = {p: x for p, m, x in be._VM_ENTRIES}
    xs = be.XS
    pa = be._ramdisk_files(xs.slot_ramdisks("a", LAPIS)[0])[be.OTA_RAMDISK_PROP]
    pb = be._ramdisk_files(xs.slot_ramdisks("b", LAPIS)[0])[be.OTA_RAMDISK_PROP]
    late = be.GBINIT.replace(gate, b"", 1).replace(b"exec /init\n", b"exec /init\n" + gate)
    files = {"busybox": open(os.path.join(X86, "inject", "busybox"), "rb").read(), "gbinit": be.GBINIT,
             "gbinit.base": base.GBINIT, "gbinit.norm": be.GBINIT.replace(b"    /busybox rm -rf /odm/vnni\n", b"", 1),
             "gbinit.late": late, "vm.build.prop": vm["gbstage/etc/build.prop"],
             "native.build.prop": nat["gbnative/etc/build.prop"], "ota_validated": be.OTA_VALIDATED_BYTES,
             "ramdisk_a.prop": pa, "ramdisk_b.prop": pb, "gb_otagate.sh": be.GB_OTAGATE_SH,
             "gb_notify.sh": nat["gbnative/vup/bin/gb_notify.sh"], "modules.load": nat["gbnative/lib/modules/modules.load"],
             "expect.env": f"FP_A='{fa}'\nFP_B='{fb}'\n".encode(),
             "gate_harness.sh": open(os.path.join(OG, "tests", "gate_harness.sh"), "rb").read()}
    for n, b in files.items():
        with open(os.path.join(d, n), "wb") as f:
            f.write(b)
    tarp = os.path.join(scratch, "harness_in.tar")
    subprocess.run(["tar", "cf", tarp.replace("\\", "/"), "-C", d.replace("\\", "/"), "."], check=True)
    remote = ("df -BG --output=avail /build | tail -n 1 | tr -d ' G' | { read a; [ \"$a\" -ge 3 ] || { echo \"/build has "
              "only ${a} GB free\"; exit 3; }; } && "
              "rm -rf /build/otagate-harness && mkdir -p /build/otagate-harness/in && cd /build/otagate-harness/in && "
              "tar xf - && chmod 0755 busybox && nice -n 19 ionice -c3 bash gate_harness.sh /build/otagate-harness/work "
              "/build/otagate-harness/in; rc=$?; cd /; rm -rf /build/otagate-harness; "
              "[ -e /build/otagate-harness ] && echo 'scratch NOT removed' || echo 'scratch /build/otagate-harness removed'; "
              "exit $rc")
    with open(tarp, "rb") as fh:
        p = subprocess.run(SSH + [remote], stdin=fh, capture_output=True, text=True, timeout=1800)
    out = p.stdout + p.stderr
    open(os.path.join(OG, "evidence", "gate_harness_builder.txt"), "w", encoding="utf-8", newline="\n").write(out)
    for ln in out.splitlines():
        if ln.startswith(("ok   ", "FAIL ", "     ", "RESULT", "==", "scratch")):
            print("     | " + ln)
    m = re.search(r"RESULT pass=(\d+) fail=(\d+)", out)
    check("BUILDER gate_harness.sh: GBINIT under the shipped busybox (real slot a/b ramdisk props), gb_otagate.sh (both "
          "services) and the real gb_notify.sh: all pass; scratch removed",
          p.returncode == 0 and m and m.group(2) == "0" and int(m.group(1)) >= 50
          and "scratch /build/otagate-harness removed" in out, out[-800:])
    shutil.rmtree(d, ignore_errors=True)
    os.remove(tarp)


def secilc():
    sd = os.path.join(OG, "tests", "sepolicy")
    names = ["odm_orig.cil", "odm_wrapped.cil", "odm_orig_missing.cil", "odm_wrapped_missing.cil", "run_secilc.sh"]
    tarp = os.path.join(tempfile.gettempdir(), "otagate_sepol.tar")
    subprocess.run(["tar", "cf", tarp.replace("\\", "/"), "-C", sd.replace("\\", "/")] + names, check=True)
    remote = ("rm -rf /build/otagate-sepol && mkdir -p /build/otagate-sepol && cd /build/otagate-sepol && tar xf - && "
              "sha256sum *.cil && bash run_secilc.sh /build/otagate-sepol /var/tmp/vnniperf/fs; rc=$?; "
              "cd /; rm -rf /build/otagate-sepol; exit $rc")
    with open(tarp, "rb") as fh:
        p = subprocess.run(SSH + [remote], stdin=fh, capture_output=True, text=True, timeout=1800)
    os.remove(tarp)
    out = p.stdout + p.stderr
    open(os.path.join(OG, "evidence", "sepolicy_secilc.txt"), "w", encoding="utf-8", newline="\n").write(out)
    check("SECILC re-run on the builder: plain == wrapped, wrapped tolerates a missing type, plain fails on it",
          "RESULT orig == wrapped: IDENTICAL" in out and "RESULT wrapped == wrapped+missing: IDENTICAL" in out
          and "RESULT orig+missing: FAILED" in out, out[-600:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-esp", default=os.path.join(OG, "work", "GB-x86", "build_esp.py"))
    ap.add_argument("--base", default=os.path.join(OG, "base", "GB-x86", "build_esp.py"))
    ap.add_argument("--scratch", default=os.path.join(tempfile.gettempdir(), "otagate_test"))
    ap.add_argument("--builder", action="store_true")
    ap.add_argument("--secilc", action="store_true")
    ap.add_argument("--no-vi", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.scratch, exist_ok=True)
    print(f"patched build_esp {a.build_esp} ({sha(open(a.build_esp, 'rb').read())[:16]}...), "
          f"base {a.base} ({sha(open(a.base, 'rb').read())[:16]}...)", flush=True)
    base_(a)
    if a.secilc:
        secilc()
    be = load("be_otagate", a.build_esp)
    base = load("be_base", a.base)
    with contextlib.redirect_stdout(io.StringIO()):
        nat_cpio = be.native_cpio()
        nat_b = cpio_entries(base.native_cpio())
    nat_p = cpio_entries(nat_cpio)
    fa, fb = fp(be, be.XS)
    fpf(be)
    cil(be)
    trees(be, nat_p)
    gate = diff(be, base, nat_p, nat_b)
    chk(be, base, nat_p, nat_b)
    mut(be, gate, nat_p)
    modmut(a.build_esp, gate, a.scratch)
    classes(be, nat_p)
    note(be)
    if not a.no_vi:
        vi_run(be, nat_cpio, a.scratch)
    apply_(a.scratch)
    order(a.scratch)
    if a.builder:
        builder(be, base, nat_p, fa, fb, a.scratch, gate)
    bad = [n for n, okk in RESULTS if not okk]
    print(f"OTA RESULT: {'OK' if not bad else f'{len(bad)} failure(s)'} ({len(RESULTS)} checks)")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
