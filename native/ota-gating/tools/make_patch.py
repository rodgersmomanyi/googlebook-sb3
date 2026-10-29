r"""Write C:\GB-build\native\ota-gating\ota_gating.patch = git diff of base\GB-x86 (copies of the CURRENT C:\GB-x86
files, see base\BASE.sha256) -> work\GB-x86 (the patched copies that were tested). Only reads base\ and work\; the
temporary git repository lives under the given scratch directory and is removed afterwards.
  python -I -B make_patch.py <scratch dir>"""
import os
import shutil
import stat
import subprocess
import sys

OG = r"C:\GB-build\native\ota-gating"
FILES = ["build_esp.py", "tests/w3a/test_w3a.py", "tests/w4/test_w4.py", "tests/w5/test_w5.py"]
GIT = ["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "-c", "user.name=ota-gating",
       "-c", "user.email=ota-gating@localhost", "-c", "commit.gpgsign=false"]


def rmtree(d):
    """shutil.rmtree that also removes git's read-only object files (Windows)."""
    def onerr(fn, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        fn(path)
    if os.path.exists(d):
        shutil.rmtree(d, onerror=onerr)


def main():
    repo = os.path.join(os.path.abspath(sys.argv[1]), "patchrepo")
    rmtree(repo)
    os.makedirs(repo)
    run = lambda *a: subprocess.run(GIT + list(a), cwd=repo, check=True, capture_output=True)
    run("init", "-q")
    for f in FILES:
        os.makedirs(os.path.dirname(os.path.join(repo, f)) or repo, exist_ok=True)
        shutil.copyfile(os.path.join(OG, "base", "GB-x86", *f.split("/")), os.path.join(repo, f))
    run("add", "-A")
    run("commit", "-q", "-m", "base")
    for f in FILES:
        shutil.copyfile(os.path.join(OG, "work", "GB-x86", *f.split("/")), os.path.join(repo, f))
    diff = subprocess.run(GIT + ["diff", "--no-color", "--no-ext-diff", "-U3", "--"] + FILES, cwd=repo, check=True,
                          capture_output=True).stdout
    out = os.path.join(OG, "ota_gating.patch")
    with open(out + ".tmp", "wb") as fh:
        fh.write(diff)
    os.replace(out + ".tmp", out)
    rmtree(repo)
    print(f"{out}: {len(diff)} bytes, {diff.count(b'diff --git')} files, {diff.count(b'@@ -')} hunks, "
          f"{sum(1 for l in diff.splitlines() if l.startswith(b'-') and not l.startswith(b'---'))} lines removed")


if __name__ == "__main__":
    main()
