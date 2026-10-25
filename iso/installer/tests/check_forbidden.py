"""INSTALLER-side copy of the DESIGN.md 4.0.11 forbidden-token rules, to check installer/ before BUILD's own
build/forbidden_scan.py runs. Usage: python -I check_forbidden.py [<installer dir>]. Exit 0 = clean."""
import os
import re
import sys

PATTERNS = [r"\bblkdiscard\b", r"\bwipefs\b", r"\bmkfs", r"\bmke2fs\b", r"\bmkdosfs\b", r"\bmkntfs\b",
            r"\bmkexfatfs\b", r"\bmkswap\b", r"\bshred\b", r"\bdd\b", r"\bfdisk\b", r"\bsfdisk\b", r"\bcfdisk\b",
            r"\bgdisk\b", r"\bparted\b", r"\bntfsfix\b", r"\bntfs-3g\b", r"\bntfscp\b", r"\bntfsresize\b",
            r"\bntfswipe\b", r"\bfallocate\b", r"\bBLKDISCARD\b", r"\bBLKSECDISCARD\b", r"\bBLKZEROOUT\b",
            r"(?i)\b0x0*127[7df]\b", r"--zap", r"--clear\b", r"--mbrtogpt", r"--move-second-header",
            r"--delete-bootorder", r"--remove-dups", r"--create(?!-only)", r"--bootnext\b", r"--inactive\b",
            r"--active\b", r"\bctypes\b"]
PY_ONLY = ["subprocess", "os.system", "os.popen", "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "import pty",
           "from pty", "efibootmgr", "sgdisk", "partx", "fsck.vfat"]
TOOL_WORDS = ["efibootmgr", "sgdisk", "fsck.vfat"]
RUN_PY = "lib/gb/run.py"
INITRAMFS_LIST = "etc/initramfs.list"


def scan(root):
    hits = []
    regs = [(p, re.compile(p)) for p in PATTERNS]
    for dp, dn, fn in os.walk(root):
        rel_dir = os.path.relpath(dp, root).replace(os.sep, "/")
        if rel_dir == "tests" or rel_dir.startswith("tests/"):
            continue
        dn[:] = [d for d in dn if d != "__pycache__"]
        for n in sorted(fn):
            rel = (n if rel_dir == "." else rel_dir + "/" + n)
            with open(os.path.join(dp, n), "rb") as f:
                data = f.read()
            if b"\r" in data:
                hits.append("%s: CR bytes" % rel)
            text = data.decode("utf-8", "replace")
            for i, line in enumerate(text.split("\n"), 1):
                for p, r in regs:
                    if r.search(line):
                        hits.append("FORBIDDEN %s:%d: %s" % (rel, i, p))
                if rel.endswith(".py") and rel != RUN_PY:
                    for w in PY_ONLY:
                        if w in line:
                            hits.append("FORBIDDEN %s:%d: %s" % (rel, i, w))
                if rel not in (RUN_PY, INITRAMFS_LIST):
                    for w in TOOL_WORDS:
                        if w in line and not (rel.endswith(".py")):
                            hits.append("FORBIDDEN %s:%d: %s" % (rel, i, w))
    return hits


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    hits = scan(os.path.abspath(root))
    for h in hits:
        print(h)
    print("FORBIDDEN-SCAN OK" if not hits else "FORBIDDEN-SCAN FAILED (%d)" % len(hits))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
