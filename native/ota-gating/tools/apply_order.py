r"""Order independence of ota_gating.patch and the readiness fixes prepared as patches by other agents
(C:\GB-build\native\fixprep\*\patches\*.patch that touch build_esp.py): on fresh copies of the CURRENT C:\GB-x86\build_esp.py,
each fixprep patch alone, ours then it, it then ours, all of them then ours, ours then all of them (git apply, exact
context, no fuzz). Only the build_esp.py part of each patch is used (git apply --include=build_esp.py). Writes nothing
outside the scratch dir.
  python -I -B apply_order.py <scratch dir>      -> prints one line per case and "APPLY-ORDER RESULT: ..."; exit 0/1"""
import glob
import os
import shutil
import subprocess
import sys

OG = r"C:\GB-build\native\ota-gating"
X86 = r"C:\GB-x86"
OURS = os.path.join(OG, "ota_gating.patch")
GIT = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "apply", "--include=build_esp.py"]


def fixprep_patches():
    out = []
    for p in sorted(glob.glob(r"C:\GB-build\native\fixprep\*\patches\*.patch")):
        if "\n+++ b/build_esp.py" in open(p, encoding="utf-8", errors="replace").read():
            out.append(p)
    return out


def trial(root, seq):
    d = os.path.join(root, "t")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    shutil.copyfile(os.path.join(X86, "build_esp.py"), os.path.join(d, "build_esp.py"))
    for p in seq:
        r = subprocess.run(GIT + [p], cwd=d, capture_output=True, text=True)
        if r.returncode:
            return False, f"{os.path.basename(p)}: {(r.stderr or r.stdout).strip().splitlines()[-1:]}"
    data = open(os.path.join(d, "build_esp.py"), "rb").read()
    shutil.rmtree(d, ignore_errors=True)
    return True, data


def main():
    root = os.path.abspath(sys.argv[1])
    os.makedirs(root, exist_ok=True)
    fx = fixprep_patches()
    res, bad = [], 0
    print(f"current C:\\GB-x86\\build_esp.py; fixprep patches touching it: {[os.path.relpath(p, r'C:\\GB-build\\native\\fixprep') for p in fx]}")
    ok_o, _ = trial(root, [OURS])
    print(f"{'ok  ' if ok_o else 'FAIL'} ours alone")
    bad += not ok_o
    for p in fx:
        name = os.path.relpath(p, r"C:\GB-build\native\fixprep")
        a_ok, a = trial(root, [p])
        if not a_ok:
            print(f"note {name} alone does not apply to the current file ({a}); its order with ours is not testable")
            continue
        b_ok, b = trial(root, [OURS, p])
        c_ok, c = trial(root, [p, OURS])
        same = b_ok and c_ok and b == c
        print(f"{'ok  ' if same else 'FAIL'} {name}: ours then it {'applies' if b_ok else 'FAILS ' + str(b)}; "
              f"it then ours {'applies' if c_ok else 'FAILS ' + str(c)}; both orders give the same file: {same}")
        bad += not same
    appl = [p for p in fx if trial(root, [p])[0]]
    if len(appl) > 1:
        b_ok, b = trial(root, [OURS] + appl)
        c_ok, c = trial(root, appl + [OURS])
        same = b_ok and c_ok and b == c
        print(f"{'ok  ' if same else 'FAIL'} all {len(appl)} applicable fixprep patches then ours / ours then all: same file: {same}"
              + ("" if b_ok and c_ok else f" ({b if not b_ok else c})"))
        bad += not same
    print(f"APPLY-ORDER RESULT: {'OK' if not bad else f'{bad} failure(s)'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
