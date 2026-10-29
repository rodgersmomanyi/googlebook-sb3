"""Pre-Apply check (F9): verify that C:\\GB-x86\\inject\\vnni\\<id>\\ still matches lapis.bin's current boot slot and LP
partition sizes before writing the ESP. Reads GPT + LP metadata only (no builder use).

  python -I check_vnni_slot.py [--lapis PATH] [--vnni-dir DIR] [--slot a|b]
Exit 0 = prepared folder matches current boot slot and LP extent sizes; 1 = mismatch (re-run vnni_prepare.py).
"""
import argparse
import importlib.util
import os
import sys

HERE = os.environ.get("GB_HOME") or r"C:\GB-x86"
spec = importlib.util.spec_from_file_location("vnni_prepare", os.path.join(HERE, "vnni_prepare.py"))
vp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vp)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lapis", default=vp.LAPIS, help="path to lapis.bin")
    ap.add_argument("--vnni-dir", default=vp.OUT_DIR, help="path to inject\\vnni")
    ap.add_argument("--slot", choices=["a", "b"], default=None, help="override slot (default: boot slot)")
    a = ap.parse_args(argv)
    try:
        ok, msgs = vp.check_current_slot(a.lapis, a.vnni_dir, a.slot)
    except Exception as e:
        print(f"VNNI SLOT CHECK FAILED: {e.__class__.__name__}: {e}", flush=True)
        return 1
    for m in msgs:
        print(m, flush=True)
    if not ok:
        print("VNNI SLOT CHECK: MISMATCH -- re-run vnni_prepare.py before Apply", flush=True)
        return 1
    print("VNNI SLOT CHECK: OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
