r"""TEST orchestrator for googlebook-sb3-installer.iso (DESIGN.md section 7).

  python -I C:\GB-x86\iso\test\run_all.py                    every test (T1c only with --with-t1c)
  python -I C:\GB-x86\iso\test\run_all.py --only T1a         one test (comma list allowed: --only T2a,T2c,T7)
  python -I C:\GB-x86\iso\test\run_all.py --list             the test ids
  python -I C:\GB-x86\iso\test\run_all.py --preflight        build record / ISO / manifest / tools checks only

Ids: T0 T1a T1b T1c T1d(n) T1d(y) T2a T2b T2c T2d T2e T2f T2g T2h T2i T2j T3 T3n(a) T3n(b) T3n(c) T3n(d) T3n(e)
T3n(f) T4 T4b T4n T4nb T5 T6 T6m T6n T6g T7 T7b T8 T9 T10 (parentheses optional: T3na == T3n(a)). T2e runs inside
T7. T3n(d/e/f), T3, T4, T4b and T9 share one disk and one NVRAM (session groups G1-G4): selecting any of them runs
the groups up to the one it needs. T6m reuses T6's Ventoy-equivalent disk.

Output: C:\GB-x86\iso\test\logs\<build_id>\ — serial transcripts (NN-<session>.serial.log), report.json (every
test with its checks and evidence), summary.txt (one line per test "PASS|FAIL|SKIPPED|INCONCLUSIVE <id> <reason>",
the build id and the overall verdict). Exit status 0 only if every selected test passed (and, for a full run, the
overall verdict is PASS).

Safety: only the throwaway VM D:\GB-iso\test\vm\gbiso-test.vmx is started (pre-start guard before every start, at
most one VM, 2 GB RAM, 2 vCPUs, no USB/network); all disks are sparse files below D:\GB-iso\test\disks\; builder
media are made from image files with loop devices in /var/tmp/gbiso/test/. Nothing is published.
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # python -I: no script dir on sys.path
import argparse
import json
import os
import re
import shutil
import sys
import time

import common as C
import mkdisks
import gbtests as TS

ORDER = ["T0", "T1a", "T1b", "T1c", "T1d(n)", "T1d(y)", "T2a", "T2b", "T2c", "T2d", "T2f", "T2g", "T2h", "T2i",
         "T2j", "T3n(a)", "T3n(b)", "T3n(c)", "T4n", "T4nb", "T3n(f)", "T3n(e)", "T3", "T3n(d)", "T4", "T4b", "T9",
         "T5", "T6", "T6m", "T6n", "T6g", "T7", "T2e", "T7b", "T10", "T8"]
REQUIRED = [t for t in ORDER if t != "T1c"]
GROUP_OF = {"T3n(f)": "G1", "T3n(e)": "G1", "T3": "G2", "T3n(d)": "G2", "T4": "G3", "T4b": "G4", "T9": "G4"}
LOCK = os.path.join(C.TEST_ROOT, "harness.lock")


def canon(tid):
    k = re.sub(r"[()_\s-]", "", tid).lower()
    for t in ORDER:
        if re.sub(r"[()]", "", t).lower() == k:
            return t
    raise SystemExit("unknown test id %r (see --list)" % tid)


def plan(selected):
    """[(runner, args)] in ORDER for the selected ids."""
    sel = set(selected)
    steps = []
    if "T0" in sel:
        steps.append((TS.t0, {}))
    t1 = tuple(t for t in ("T1a", "T1b", "T1c", "T1d(n)", "T1d(y)") if t in sel)
    if t1:
        steps.append((TS.t1, {"which": t1}))
    t2 = tuple(t for t in ("T2a", "T2b", "T2c", "T2d", "T2f", "T2g", "T2h", "T2i", "T2j") if t in sel)
    if t2:
        steps.append((TS.t2, {"which": t2}))
    t3n = tuple(t for t in ("T3n(a)", "T3n(b)", "T3n(c)", "T4n", "T4nb") if t in sel)
    if t3n:
        steps.append((TS.t3n_single, {"which": t3n}))
    groups = sorted({GROUP_OF[t] for t in sel if t in GROUP_OF})
    if groups:
        steps.append((TS.t3_group, {"upto": groups[-1]}))
    if "T5" in sel:
        steps.append((TS.t5, {}))
    t6 = tuple(t for t in ("T6", "T6m", "T6n") if t in sel)
    if t6:
        steps.append((TS.t6, {"which": t6}))
    if "T6g" in sel:
        steps.append((TS.t6g, {}))
    t7 = tuple(t for t in ("T7", "T2e", "T7b") if t in sel)
    if t7:
        steps.append((TS.t7, {"which": t7}))
    if "T10" in sel:
        steps.append((TS.t10, {}))
    if "T8" in sel:
        steps.append((TS.t8, {}))
    return steps


def take_lock():
    C.mkdir(C.TEST_ROOT)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit("another harness run holds %s (delete it if no run is active)" % LOCK)
    os.write(fd, ("%d %s\n" % (os.getpid(), C.utc_iso())).encode())
    os.close(fd)


def release_lock():
    C.remove_file(LOCK)


def write_outputs(ctx, results, selected, started, full):
    by_id = {}
    for r in results:
        by_id[r.id] = r
    missing = [t for t in REQUIRED if t not in by_id] if full else []
    overall = "PASS" if full and not missing and all(by_id[t].status == "PASS" for t in REQUIRED) else (
        "FAIL" if full else "PARTIAL")
    rep = {"format": "gbsb3-test-report/1", "build_id": ctx.build_id, "iso_sha256": ctx.record.get("iso_sha256"),
           "started": started, "ended": C.utc_iso(), "selected": selected, "overall": overall,
           "missing": missing, "tests": [r.to_json() for r in results]}
    C.write_json(os.path.join(ctx.run_dir, "report.json"), rep)
    lines = ["build_id %s" % ctx.build_id]
    for r in results:
        reason = re.sub(r"\s+", " ", r.reason or "")[:300]
        lines.append("%s %s %s" % (r.status, r.id, reason))
    for t in missing:
        lines.append("FAIL %s not run" % t)
    lines.append("OVERALL %s" % overall)
    p = os.path.join(ctx.run_dir, "summary.txt")
    C.assert_test_path(p)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return overall


def archive_prior_runs(ctx):
    """A full run starts with an empty evidence folder: transcripts and results of earlier (partial) runs of the
    same build move to <run_dir>\\prior-<UTC stamp>\\ (transcripts are opened in append mode and would mix)."""
    names = [n for n in os.listdir(ctx.run_dir)
             if n.endswith(".serial.log") or n in ("report.json", "summary.txt", "t4b", "t8", "t0_builder.log",
                                                   "control_verify_image.json")]
    if not names:
        return
    dst = C.mkdir(os.path.join(ctx.run_dir, "prior-%s" % C.utc_stamp()))
    for n in names:
        shutil.move(os.path.join(ctx.run_dir, n), os.path.join(dst, n))
    with open(os.path.join(dst, "README.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("Evidence of earlier (partial) harness runs of build %s, moved here when the full run started at %s.\n"
                "Not evidence of that full run.\n" % (ctx.build_id, C.utc_iso()))
    C.log("moved %d earlier evidence files to %s" % (len(names), dst))


def final_cleanup(ctx):
    """Power-off check, ISO hard link, VM folder, builder scratch files."""
    try:
        if ctx.vmrun.is_running(C.VMX):
            ctx.vmrun.stop_hard(C.VMX)
            ctx.vmrun.wait_off(C.VMX, 60)
    except Exception as e:
        C.log("cleanup: vmrun: %s" % e)
    try:
        mkdisks.unlink_iso()
    except Exception as e:
        C.log("cleanup: ISO link: %s" % e)
    if os.path.isdir(C.VM_DIR) and not ctx.vmrun.is_running(C.VMX):
        C.assert_test_path(C.VM_DIR)
        shutil.rmtree(C.VM_DIR, ignore_errors=True)
    if ctx.builder_ready:
        C.ssh("bash %s/builder_media.sh clean" % C.REMOTE_TEST, check=False, timeout=600)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*", help="test ids (same as --only)")
    ap.add_argument("--only", default="", help="comma-separated test ids")
    ap.add_argument("--with-t1c", action="store_true", help="add the optional T1c to a full run")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--preflight", action="store_true", help="only the preflight checks")
    ap.add_argument("--keep-disks", action="store_true", help="keep test disks after each test (debugging)")
    a = ap.parse_args(argv)
    if a.list:
        print(" ".join(ORDER))
        return 0
    ids = [canon(x) for x in (a.ids + [x for x in a.only.split(",") if x.strip()])]
    full = not ids
    selected = ids or [t for t in ORDER if t != "T1c" or a.with_t1c]
    try:
        ctx = TS.Ctx(keep_disks=a.keep_disks)
        if a.preflight:
            TS.preflight(ctx)
            print("PREFLIGHT OK build %s" % ctx.build_id)
            return 0
    except C.HarnessError as e:
        print("PRECONDITION FAILED: %s" % e)
        return 2
    take_lock()
    started = C.utc_iso()
    if full:
        archive_prior_runs(ctx)
    results = []
    try:
        for fn, kw in plan(selected):
            results += fn(ctx, **kw)
            write_outputs(ctx, results, selected, started, False)
    finally:
        final_cleanup(ctx)
        release_lock()
    results = [r for r in results if r.id in selected or r.id == "T3-build" or not full]
    overall = write_outputs(ctx, results, selected, started, full)
    ok = all(r.status == "PASS" for r in results if r.id in selected)
    return 0 if ok and overall in ("PASS", "PARTIAL") else 1


if __name__ == "__main__":
    sys.exit(main())
