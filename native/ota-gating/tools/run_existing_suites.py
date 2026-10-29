r"""Integration impact of ota_gating.patch on the EXISTING host suites of C:\GB-x86\tests: each suite is copied into a
shadow folder and run twice, once against the unpatched build_esp.py (base copy == current C:\GB-x86) and once against
the patched copy, with build_usb.py / verify_image.py shadow copies whose only change is the path of build_esp.py.
Nothing in C:\GB-x86 is written; outputs go to the shadow folder and each suite's own --scratch.
  python -I -B run_existing_suites.py OUT_DIR [suite ...]      (suites: w5 w4 w3a trees tb3)
Prints, per suite, the FAIL lines of both runs and the lines that differ (the patch's impact)."""
import os
import re
import shutil
import subprocess
import sys

OG = r"C:\GB-build\native\ota-gating"
X86 = r"C:\GB-x86"
SUITES = {"w5": (r"tests\w5\test_w5.py", ["--scratch", "{tmp}"]),
          "w4": (r"tests\w4\test_w4.py", ["--scratch", "{tmp}"]),
          "w3a": (r"tests\w3a\test_w3a.py", ["--scratch", "{tmp}", "--no-builder"]),
          "trees": (r"tests\w2b\test_trees_from_build_esp.py", []),
          "tb3": (r"tests\w2b\test_verify_image.py", ["--work", "{tmp}"])}


def shadow(root, be_src):
    r"""root\GB-x86: build_esp.py (be_src), build_usb.py and verify_image.py pointing at it; returns their paths."""
    d = os.path.join(root, "GB-x86")
    os.makedirs(d, exist_ok=True)
    be = os.path.join(d, "build_esp.py")
    shutil.copyfile(be_src, be)
    bu_src = open(os.path.join(X86, "build_usb.py"), encoding="utf-8").read()
    old = 'spec_from_file_location("build_esp", os.path.join(HERE, "build_esp.py"))'
    assert bu_src.count(old) == 1
    bu = os.path.join(d, "build_usb.py")
    open(bu, "w", encoding="utf-8", newline="\n").write(bu_src.replace(old, f'spec_from_file_location("build_esp", r"{be}")'))
    vi_src = open(os.path.join(X86, "verify_image.py"), encoding="utf-8").read()
    old = '"build_esp": r"C:\\GB-x86\\build_esp.py",'
    assert vi_src.count(old) == 1
    vi = os.path.join(d, "verify_image.py")
    open(vi, "w", encoding="utf-8", newline="\n").write(vi_src.replace(old, f'"build_esp": r"{be}",'))
    return be, bu, vi


def copy_suite(root, rel, be, bu, vi, tests_root=None):
    src = os.path.join(X86, rel)
    dst = os.path.join(root, rel)
    shutil.copytree(os.path.dirname(src), os.path.dirname(dst), dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    alt = os.path.join(tests_root, rel) if tests_root else None
    s = open(alt if alt and os.path.isfile(alt) else src, encoding="utf-8").read()
    for old, new in ((r'os.path.join(HERE, "build_usb.py")', f'r"{bu}"'), (r'r"C:\GB-x86\build_usb.py"', f'r"{bu}"'),
                     (r'os.path.join(HERE, "verify_image.py")', f'r"{vi}"'), (r'r"C:\GB-x86\verify_image.py"', f'r"{vi}"'),
                     (r'os.path.join(HERE, "build_esp.py")', f'r"{be}"'), (r'r"C:\GB-x86\build_esp.py"', f'r"{be}"')):
        s = s.replace(old, new)
    open(dst, "w", encoding="utf-8", newline="\n").write(s)
    return dst


def run(out, name, tag, be_src, tests_root=None):
    root = os.path.join(out, tag, name)
    shutil.rmtree(root, ignore_errors=True)
    be, bu, vi = shadow(root, be_src)
    rel, args = SUITES[name]
    t = copy_suite(root, rel, be, bu, vi, tests_root)
    tmp = os.path.join(root, "scratch")
    p = subprocess.run([sys.executable, "-I", "-B", t] + [a.format(tmp=tmp) for a in args], capture_output=True,
                       text=True, timeout=3600, cwd=os.path.dirname(t))
    log = p.stdout + p.stderr
    open(os.path.join(out, f"{name}_{tag}.log"), "w", encoding="utf-8", newline="\n").write(log)
    fails = [ln for ln in log.splitlines() if re.match(r"\s*(FAIL|Traceback)", ln)]
    res = [ln for ln in log.splitlines() if "RESULT" in ln][-1:] or ["(no RESULT line)"]
    shutil.rmtree(os.path.join(root, "GB-x86"), ignore_errors=True)
    return p.returncode, fails, res[0]


def main():
    out = os.path.abspath(sys.argv[1])
    args = sys.argv[2:]
    tests_root = None
    if args and args[0] == "--patched-tests":      # the patched runs use work\GB-x86\tests\... where a copy exists
        tests_root, args = os.path.join(OG, "work", "GB-x86"), args[1:]
    names = args or list(SUITES)
    os.makedirs(out, exist_ok=True)
    for n in names:
        rb, fb, resb = run(out, n, "base", os.path.join(OG, "base", "GB-x86", "build_esp.py"))
        rp, fp, resp = run(out, n, "patched", os.path.join(OG, "work", "GB-x86", "build_esp.py"), tests_root)
        print(f"== {n}: base rc={rb} [{resb.strip()}] {len(fb)} FAIL; patched rc={rp} [{resp.strip()}] {len(fp)} FAIL")
        norm = lambda ls: {re.sub(r"\s+\[.*$", "", ln.strip()) for ln in ls}
        for ln in sorted(norm(fp) - norm(fb)):
            print(f"   NEW with the patch: {ln[:300]}")
        for ln in sorted(norm(fb) - norm(fp)):
            print(f"   only without the patch: {ln[:300]}")
        for ln in sorted(norm(fb) & norm(fp)):
            print(f"   both (pre-existing): {ln[:300]}")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
