# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Googlebook OS build (GB-build) authors.
#
# test_patcher.py -- behavioural tests of vnni_patch.py's safety properties
# (sha256 pin, result pin, byte check, and the boot-time fingerprint gate that
# keeps an A/B OTA'd slot from getting this build's patched libz).
#
# Needs the ORIGINAL carved files (builder):
#   python3 -I test_patcher.py --system-libz <..>/system/lib64/libz.so \
#       --vendor-libz <..>/lib64/libz.so --system-prop <..>/system/build.prop \
#       --vendor-prop <..>/vendor/build.prop [--manifest ../patcher/manifest.json]

import argparse
import copy
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "patcher"))
import vnni_patch  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print("%-4s %s%s" % ("ok" if cond else "FAIL", name, ("  -- " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


def stage(root, files):
    for rel, src in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if isinstance(src, bytes):
            with open(p, "wb") as f:
                f.write(src)
        else:
            shutil.copyfile(src, p)


def run_patch(manifest, files, extra=()):
    d = tempfile.mkdtemp(prefix="vnni_t_")
    mpath = os.path.join(d, "manifest.json")
    with open(mpath, "w") as f:
        json.dump(manifest, f)
    inr, outr, odm = (os.path.join(d, x) for x in ("in", "out", "odm"))
    os.makedirs(inr)
    stage(inr, files)
    rcp, om = os.path.join(d, "vnni.rc"), os.path.join(d, "om.json")
    devnull = open(os.devnull, "w")
    so, se = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = devnull
    try:
        rc = vnni_patch.main(["--manifest", mpath, "--in-root", inr, "--out-root", outr,
                              "--out-manifest", om, "--out-rc", rcp, "--odm-root", odm] + list(extra))
    finally:
        sys.stdout, sys.stderr = so, se
        devnull.close()
    with open(rcp) as f:
        rct = f.read()
    with open(om) as f:
        res = {r["name"]: r for r in json.load(f)["results"]}
    return rc, rct, res, odm, outr


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=os.path.join(HERE, "..", "patcher", "manifest.json"))
    ap.add_argument("--system-libz", required=True)
    ap.add_argument("--vendor-libz", required=True)
    ap.add_argument("--system-prop", required=True)
    ap.add_argument("--vendor-prop", required=True)
    a = ap.parse_args(argv)
    with open(a.manifest) as f:
        man = json.load(f)
    libz = {t["name"]: t for t in man["targets"] if t["name"].endswith("lib64/libz.so")}
    m2 = {"build": man["build"], "targets": [libz["system/lib64/libz.so"], libz["vendor/lib64/libz.so"]]}
    sfp = vnni_patch.read_prop(a.system_prop, "ro.system.build.fingerprint")
    vfp = vnni_patch.read_prop(a.vendor_prop, "ro.vendor.build.fingerprint")
    good = {"system/lib64/libz.so": a.system_libz, "vendor/lib64/libz.so": a.vendor_libz,
            "system/build.prop": a.system_prop, "vendor/build.prop": a.vendor_prop}

    # 1. the build the manifest was made for
    rc, rct, res, odm, outr = run_patch(m2, good)
    check("T1 matching image: both libz patched", rc == 0 and all(
        r["status"] == "patched" for r in res.values()))
    check("T1 patched hashes == manifest patched_sha256", all(
        res[n]["patched_sha256"] == libz[n]["patched_sha256"] for n in res))
    with open(a.system_libz, "rb") as f:
        o = f.read()
    with open(os.path.join(outr, "system", "lib64", "libz.so"), "rb") as f:
        p = f.read()
    diff = [i for i in range(len(o)) if o[i] != p[i]]
    check("T1 only the je opcode bytes changed", len(o) == len(p) and diff == [0xa7d5, 0xa7d6],
          "diff at %s" % [hex(x) for x in diff])
    acts = [l for l in rct.splitlines() if l.startswith("on ")]
    check("T1 rc: exactly 2 actions, both fingerprint-gated with the image's values",
          acts == ["on early-init && property:ro.system.build.fingerprint=%s" % sfp,
                   "on early-init && property:ro.vendor.build.fingerprint=%s" % vfp], repr(acts))
    check("T1 rc binds /odm/vnni copies over the two libz, relabels, then remounts ro",
          "mount none /odm/vnni/system_libz.so /system/lib64/libz.so bind\n"
          "    restorecon /system/lib64/libz.so\n"
          "    mount none none /system/lib64/libz.so remount bind ro\n" in rct and
          "mount none /odm/vnni/vendor_libz.so /vendor/lib64/libz.so bind\n"
          "    restorecon /vendor/lib64/libz.so\n"
          "    mount none none /vendor/lib64/libz.so remount bind ro\n" in rct)
    check("T1 odm staging copies written", sorted(os.listdir(odm)) == ["system_libz.so", "vendor_libz.so"])

    # 2. no build.prop in the in-root -> gate from the manifest's recorded value
    rc, rct, res, odm, _ = run_patch(m2, {k: v for k, v in good.items() if "build.prop" not in k})
    n_gated = sum(1 for l in rct.splitlines() if l.startswith("on early-init && property:"))
    check("T2 no build.prop: gate falls back to manifest gate_value", rc == 0 and n_gated == 2 and
          ("property:ro.system.build.fingerprint=%s\n" % libz["system/lib64/libz.so"]["deploy"]["gate_value"]) in rct)

    # 3. OTA / other slot: a different libz (one byte changed) -> nothing patched, no bind
    other = bytearray(o)
    other[0x2a0 + 0x10] ^= 0xff  # inside .note.gnu.build-id: a "different build"
    files = dict(good)
    files["system/lib64/libz.so"] = bytes(other)
    rc, rct, res, odm, _ = run_patch(m2, files)
    check("T3 different system libz: skipped, no system bind, vendor still gated",
          res["system/lib64/libz.so"]["status"] == "skipped-no-sha256-match" and
          "/system/lib64/libz.so bind" not in rct and "system_libz.so" not in os.listdir(odm)
          and rct.count("on early-init && property:ro.vendor.build.fingerprint=") == 1)

    # 4. same libz bytes but the image is a different build -> gate on the IMAGE's fingerprint
    newprop = os.path.join(tempfile.mkdtemp(prefix="vnni_p_"), "build.prop")
    with open(a.system_prop) as f:
        txt = f.read()
    with open(newprop, "w") as f:
        f.write(txt.replace(sfp, sfp.replace("16658739", "99999999")))
    files = dict(good)
    files["system/build.prop"] = newprop
    rc, rct, res, odm, _ = run_patch(m2, files)
    check("T4 identical libz on another build: bind gated on that build's fingerprint only",
          ("property:ro.system.build.fingerprint=%s\n" % sfp.replace("16658739", "99999999")) in rct
          and ("=%s\n" % sfp) not in rct)

    # 5. build.prop without the gate property -> patched copy for inspection, NO bind
    noprop = os.path.join(tempfile.mkdtemp(prefix="vnni_p_"), "build.prop")
    with open(noprop, "w") as f:
        f.write("\n".join(l for l in txt.splitlines() if not l.startswith("ro.system.build.fingerprint")))
    files = dict(good)
    files["system/build.prop"] = noprop
    rc, rct, res, odm, _ = run_patch(m2, files)
    check("T5 gate property absent: no system bind, not staged in odm",
          res["system/lib64/libz.so"]["deploy"]["status"] == "no-bind" and
          "/system/lib64/libz.so bind" not in rct and "system_libz.so" not in os.listdir(odm))

    # 6. whitespace / unsafe gate value -> no bind
    m6 = copy.deepcopy(m2)
    m6["targets"][0]["deploy"]["gate_value"] = "a b"
    rc, rct, res, odm, _ = run_patch(m6, {k: v for k, v in good.items() if "build.prop" not in k})
    check("T6 unsafe gate value: no bind", "/system/lib64/libz.so bind" not in rct)

    # 7. wrong original bytes in the manifest -> refused, exit 2, nothing written
    m7 = copy.deepcopy(m2)
    m7["targets"][0]["edits"][0]["orig"] = "0f8459ffffff"
    rc, rct, res, odm, outr = run_patch(m7, good)
    check("T7 byte mismatch: refused (exit 2), no output, no bind",
          rc == 2 and res["system/lib64/libz.so"]["status"] == "refused" and
          not os.path.exists(os.path.join(outr, "system", "lib64", "libz.so")) and
          "/system/lib64/libz.so bind" not in rct)

    # 8. tampered expected result hash -> refused
    m8 = copy.deepcopy(m2)
    m8["targets"][0]["patched_sha256"] = "0" * 64
    rc, rct, res, odm, outr = run_patch(m8, good)
    check("T8 result-hash mismatch: refused, no bind",
          rc == 2 and res["system/lib64/libz.so"]["status"] == "refused" and
          "/system/lib64/libz.so bind" not in rct)

    # 9. hint is a symlink -> skipped (not followed), no crash
    d9 = tempfile.mkdtemp(prefix="vnni_s_")
    tgt = os.path.join(d9, "real.so")
    shutil.copyfile(a.system_libz, tgt)
    try:
        inr = tempfile.mkdtemp(prefix="vnni_i_")
        os.makedirs(os.path.join(inr, "system", "lib64"))
        os.symlink(tgt, os.path.join(inr, "system", "lib64", "libz.so"))
        mp = os.path.join(d9, "m.json")
        with open(mp, "w") as f:
            json.dump({"targets": [libz["system/lib64/libz.so"]]}, f)
        devnull = open(os.devnull, "w")
        so = sys.stdout
        sys.stdout = devnull
        try:
            r9 = vnni_patch.main(["--manifest", mp, "--in-root", inr,
                                  "--out-root", os.path.join(d9, "o"), "--out-rc", os.path.join(d9, "rc")])
        finally:
            sys.stdout = so
            devnull.close()
        with open(os.path.join(d9, "rc")) as f:
            check("T9 symlinked target: skipped, no bind, no crash", r9 == 0 and f.read() == "")
    except OSError as ex:
        print("skip T9 (no symlink support: %s)" % ex)

    # 10. statics carry no deploy block (never bound by this tool)
    statics = [t for t in man["targets"] if not t["name"].endswith("lib64/libz.so")]
    check("T10 static copies are not deployable (no deploy block, deploy_note set)",
          all("deploy" not in t and t.get("deploy_note") for t in statics), "%d statics" % len(statics))

    print("\ntest_patcher: %d failure(s)%s" % (len(FAILS), (": " + ", ".join(FAILS)) if FAILS else ""))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
