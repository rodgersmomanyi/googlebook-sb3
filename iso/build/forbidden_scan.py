"""Forbidden-token scan of the Googlebook ISO installer (DESIGN.md 4.0.11, 6.1). Read-only.

Usage: python -I forbidden_scan.py <dir> [--layout installer|initrd]

  --layout installer (default): <dir> is C:\\GB-x86\\iso\\installer (or a copy of it). Every file below <dir> is
      scanned except <dir>/tests/ and Python bytecode caches (__pycache__/, *.pyc: never copied into the initramfs).
      Exempt paths: lib/gb/run.py (the only process launcher) and etc/initramfs.list.
  --layout initrd: <dir> is an unpacked initramfs root. Only init, opt/gb/** and etc/gb/** are scanned. Exempt paths:
      opt/gb/lib/gb/run.py and etc/gb/initramfs.list. Bytecode files there are themselves a hit (BUILD ships none).

Prints one line "FORBIDDEN <relpath>:<line>: <pattern>" per hit and exits 1, or prints "FORBIDDEN-SCAN OK" and exits
0. Exit 2 = usage error / directory missing. Runs with any Python >= 3.8 (Windows host and the builder).
"""
import os
import re
import sys

# 4.0.11: every scanned file, line by line, case-sensitive unless marked.
TOKENS = [
    r"\bblkdiscard\b", r"\bwipefs\b", r"\bmkfs", r"\bmke2fs\b", r"\bmkdosfs\b", r"\bmkntfs\b", r"\bmkexfatfs\b",
    r"\bmkswap\b", r"\bshred\b", r"\bdd\b", r"\bfdisk\b", r"\bsfdisk\b", r"\bcfdisk\b", r"\bgdisk\b", r"\bparted\b",
    r"\bntfsfix\b", r"\bntfs-3g\b", r"\bntfscp\b", r"\bntfsresize\b", r"\bntfswipe\b", r"\bfallocate\b",
    r"\bBLKDISCARD\b", r"\bBLKSECDISCARD\b", r"\bBLKZEROOUT\b", r"(?i)\b0x0*127[7df]\b", r"--zap", r"--clear\b",
    r"--mbrtogpt", r"--move-second-header", r"--delete-bootorder", r"--remove-dups", r"--create(?!-only)",
    r"--bootnext\b", r"--inactive\b", r"--active\b", r"\bctypes\b",
]
# In *.py files other than run.py: literal substrings that must not occur.
PY_ONLY_RUN = [
    "subprocess", "os.system", "os.popen", "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "import pty",
    "from pty", "efibootmgr", "sgdisk", "partx", "fsck.vfat",
]
# In every file other than run.py and initramfs.list: these words must not occur.
WORDS_RUN_LIST = [r"\befibootmgr\b", r"\bsgdisk\b", r"\bfsck\.vfat\b"]

LAYOUTS = {
    # layout: (scanned top-level entries or None = everything, excluded top dirs, run.py path, initramfs.list path)
    "installer": (None, ("tests",), "lib/gb/run.py", "etc/initramfs.list"),
    "initrd": (("init", "opt/gb", "etc/gb"), (), "opt/gb/lib/gb/run.py", "etc/gb/initramfs.list"),
}

_TOKEN_RE = [(p, re.compile(p)) for p in TOKENS]
_PY_RE = [(p, re.compile(re.escape(p))) for p in PY_ONLY_RUN]
_WORD_RE = [(p, re.compile(p)) for p in WORDS_RUN_LIST]


def iter_files(root, layout):
    tops, excluded, _, _ = LAYOUTS[layout]
    starts = [root] if tops is None else [os.path.join(root, *t.split("/")) for t in tops]
    for start in starts:
        if os.path.isfile(start) or os.path.islink(start):
            yield start
            continue
        if not os.path.isdir(start):
            continue
        for d, dirs, files in os.walk(start):
            rel_d = os.path.relpath(d, root).replace(os.sep, "/")
            keep = []
            for x in sorted(dirs):
                rel = x if rel_d == "." else rel_d + "/" + x
                if rel in excluded:
                    continue
                if layout == "installer" and x == "__pycache__":
                    continue
                keep.append(x)
            dirs[:] = keep
            for f in sorted(files):
                yield os.path.join(d, f)


def scan(root, layout):
    _, _, run_py, init_list = LAYOUTS[layout]
    hits = []
    nfiles = 0
    for path in iter_files(root, layout):
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        if layout == "installer" and rel.endswith(".pyc"):
            continue
        nfiles += 1
        if layout == "initrd" and (rel.endswith(".pyc") or "/__pycache__/" in "/" + rel):
            hits.append((rel, 0, "bytecode file"))
            continue
        if os.path.islink(path):
            # a symlink is scanned by its target text (no content of its own)
            lines = [os.readlink(path)]
        else:
            with open(path, "rb") as f:
                data = f.read()
            lines = data.decode("utf-8", errors="replace").split("\n")
        is_py = rel.endswith(".py")
        for no, line in enumerate(lines, 1):
            for pat, rx in _TOKEN_RE:
                if rx.search(line):
                    hits.append((rel, no, pat))
            if is_py and rel != run_py:
                for pat, rx in _PY_RE:
                    if rx.search(line):
                        hits.append((rel, no, pat))
            if rel not in (run_py, init_list):
                for pat, rx in _WORD_RE:
                    if rx.search(line):
                        hits.append((rel, no, pat))
    return hits, nfiles


def main(argv):
    args = list(argv)
    layout = "installer"
    if "--layout" in args:
        i = args.index("--layout")
        if i + 1 >= len(args) or args[i + 1] not in LAYOUTS:
            print("usage: forbidden_scan.py <dir> [--layout installer|initrd]", file=sys.stderr)
            return 2
        layout = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        print("usage: forbidden_scan.py <dir> [--layout installer|initrd]", file=sys.stderr)
        return 2
    root = args[0]
    if not os.path.isdir(root):
        print(f"forbidden_scan: {root} is not a directory", file=sys.stderr)
        return 2
    hits, nfiles = scan(root, layout)
    for rel, no, pat in hits:
        print(f"FORBIDDEN {rel}:{no}: {pat}")
    if hits:
        print(f"forbidden_scan: {len(hits)} hit(s) in {nfiles} file(s)", file=sys.stderr)
        return 1
    if nfiles == 0:
        print("forbidden_scan: no files scanned", file=sys.stderr)
        return 2
    print("FORBIDDEN-SCAN OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
