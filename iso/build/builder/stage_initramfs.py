"""Stage the Googlebook installer initramfs tree (DESIGN.md 3.1-3.3, 3.5). Runs on the builder as the build user.

Usage: python3 -I -B stage_initramfs.py --staging DIR --installer DIR --build-id ID --android-build B
                                         [--krel 6.8.0-147-generic] [--psf-double psf_double.py]

Builds DIR from: the merged-usr layout of the builder (lib -> usr/lib, lib64 -> usr/lib64; /bin and /sbin are REAL
directories), busybox + its applet links in /bin, the installer tree (3.1 mapping), every directive of the installer's
etc/initramfs.list (3.2 grammar), the generated /etc/gb files, the resolved kernel modules (3.3, denylist) and the
console fonts (3.5). Reads builder files only; writes only below DIR (which must not exist yet). Never runs as root.
Last output line: "STAGE-JSON {...}".
"""
import argparse
import fnmatch
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys

PY_PRUNE = {"test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3", "pydoc_data", "venv", "sqlite3",
            "curses", "dbm", "unittest", "asyncio", "multiprocessing", "concurrent", "wsgiref", "xmlrpc", "ctypes",
            "config-3.12-x86_64-linux-gnu", "site-packages", "dist-packages", "__pycache__"}
PY_DYNLOAD_KEEP = {"_hashlib", "_json", "_contextvars", "_queue", "mmap", "termios", "resource"}
MODULE_DENY = ["spi_intel*", "mtd*", "efi_test", "efi_pstore", "tpm*", "nouveau", "nvidia*", "drm_gpuvm", "drm_exec",
               "gpu_sched"]
FONT_DIR = "/usr/share/consolefonts"
FONT_32x16 = "Lat15-TerminusBold32x16.psf.gz"
FONT_8x16 = "Lat15-Terminus16.psf.gz"
FONT_32x64 = "gb-ter-32x64.psf"
REQUIRED_INSTALLER = ["init", "bin/gbinstall.sh", "bin/gbmirror.sh", "lib/gbinstall.py", "lib/gb/run.py",
                      "etc/modules.list", "etc/initramfs.list"]
GENERATED_ETC_GB = {"build.env", "modules.order"}
EMPTY_DIRS = ["dev", "proc", "sys", "run", "tmp", "mnt"]

warnings = []


def die(msg):
    print(f"stage_initramfs: ERROR: {msg}", flush=True)
    sys.exit(1)


def note(msg):
    print(f"stage: {msg}", flush=True)


def warn(msg):
    warnings.append(msg)
    print(f"stage: WARNING: {msg}", flush=True)


class Stager:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.files = 0
        self.bytes = 0
        self.links = 0

    def sp(self, p):
        """Host path of the staging-namespace absolute path p."""
        if not p.startswith("/"):
            die(f"not an absolute path: {p}")
        q = os.path.normpath(os.path.join(self.root, p.lstrip("/")))
        if q != self.root and not q.startswith(self.root + os.sep):
            die(f"path escapes the staging tree: {p}")
        return q

    def mkdirs(self, p):
        """Create directory p (staging namespace) resolving staging symlinks (lib -> usr/lib) inside the tree."""
        r = self.resolve(p)
        os.makedirs(self.sp(r), exist_ok=True)
        return r

    def resolve(self, p, depth=0):
        """Resolve staging-namespace path p following symlinks INSIDE the staging tree (absolute link targets are
        taken relative to the staging root). Missing components are kept literally."""
        if depth > 40:
            die(f"symlink loop resolving {p}")
        cur = "/"
        for part in [x for x in p.split("/") if x and x != "."]:
            if part == "..":
                cur = posixpath.dirname(cur) or "/"
                continue
            nxt = posixpath.join(cur, part)
            h = self.sp(nxt)
            if os.path.islink(h):
                t = os.readlink(h)
                tgt = t if t.startswith("/") else posixpath.join(cur, t)
                cur = self.resolve(posixpath.normpath(tgt), depth + 1)
            else:
                cur = nxt
        return posixpath.normpath(cur)

    def put_file(self, src, dst, mode=None):
        """Copy regular file src (host) to staging path dst (exact path; parent resolved inside staging)."""
        parent = self.mkdirs(posixpath.dirname(dst))
        h = self.sp(posixpath.join(parent, posixpath.basename(dst)))
        if os.path.lexists(h):
            os.unlink(h)
        shutil.copy2(src, h)
        if mode is not None:
            os.chmod(h, mode)
        self.files += 1
        self.bytes += os.path.getsize(h)
        return h

    def put_link(self, dst, target, replace=True):
        parent = self.mkdirs(posixpath.dirname(dst))
        h = self.sp(posixpath.join(parent, posixpath.basename(dst)))
        if os.path.lexists(h):
            if os.path.islink(h) and os.readlink(h) == target:
                return
            if not replace:
                die(f"{dst} already exists in staging")
            if os.path.isdir(h) and not os.path.islink(h):
                die(f"{dst} is a directory in staging")
            os.unlink(h)
        os.symlink(target, h)
        self.links += 1

    def stage_path(self, p):
        """Copy builder file p into staging at the same path, keeping its symlink chain. Returns the real path."""
        cur = p
        for _ in range(40):
            d = os.path.realpath(os.path.dirname(cur))
            c = os.path.join(d, os.path.basename(cur))
            if os.path.islink(c):
                t = os.readlink(c)
                h = self.sp(c)
                if not os.path.lexists(h):
                    self.put_link(c, t)
                cur = t if t.startswith("/") else os.path.join(d, t)
                continue
            if not os.path.isfile(c):
                die(f"{p}: {c} is not a regular file on the builder")
            h = self.sp(c)
            if not os.path.lexists(h):
                self.put_file(c, c)
            real = c
            break
        else:
            die(f"symlink chain too long: {p}")
        # the literal path must resolve to the copy too (e.g. /sbin/x when /sbin is a real directory in staging)
        if self.resolve(p) != posixpath.normpath(real):
            if os.path.lexists(self.sp(p)):
                die(f"{p} exists in staging but does not resolve to {real}")
            self.put_link(p, real)
        return real

    def stage_bin(self, p):
        real = self.stage_path(p)
        for lib in ldd(real):
            self.stage_path(lib)
        return real


def ldd(path):
    r = subprocess.run(["ldd", path], capture_output=True, text=True)
    text = r.stdout + r.stderr
    if "not a dynamic executable" in text or "statically linked" in text:
        return []
    if r.returncode != 0:
        die(f"ldd {path} failed: {text.strip()}")
    libs = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if "=>" in line:
            name, rest = line.split("=>", 1)
            rest = rest.strip()
            if rest.startswith("not found"):
                die(f"ldd {path}: {name.strip()} not found")
            lib = rest.split(" (")[0].strip()
            if lib:
                libs.append(lib)
        elif line.startswith("/"):
            libs.append(line.split(" (")[0].strip())
    return libs


def check_lf(path):
    with open(path, "rb") as f:
        if b"\r" in f.read():
            die(f"{path}: CR bytes (installer files must be LF-only)")


def stage_layout(st):
    os.makedirs(st.root)
    # merged-usr links exactly as on the builder; /bin and /sbin are real directories (DESIGN 3.1)
    for name in ("lib", "lib64"):
        t = os.readlink("/" + name) if os.path.islink("/" + name) else None
        if t not in ("usr/" + name, "/usr/" + name):
            die(f"unexpected builder layout: /{name} -> {t}")
        os.makedirs(st.sp("/usr/" + name), exist_ok=True)
        st.put_link("/" + name, t)
    for d in ["bin", "sbin", "usr/bin", "usr/sbin", "etc/gb", "opt/gb/bin", "opt/gb/lib", FONT_DIR.lstrip("/")] + \
            EMPTY_DIRS:
        os.makedirs(st.sp("/" + d), exist_ok=True)
    os.chmod(st.sp("/tmp"), 0o1777)


def stage_busybox(st):
    bb = "/usr/bin/busybox"
    if ldd(bb):
        die("/usr/bin/busybox is not static")
    st.put_file(bb, "/bin/busybox", 0o755)
    applets = subprocess.run([bb, "--list"], capture_output=True, text=True, check=True).stdout.split()
    n = 0
    for a in applets:
        if a == "busybox" or "/" in a:
            continue
        st.put_link("/bin/" + a, "busybox")
        n += 1
    note(f"busybox: {n} applet links in /bin")
    return n


def stage_installer(st, inst):
    for r in REQUIRED_INSTALLER:
        if not os.path.isfile(os.path.join(inst, r)):
            die(f"installer file missing: {r}")
    count = 0
    for top in sorted(os.listdir(inst)):
        if top not in ("init", "bin", "lib", "etc", "tests"):
            warn(f"installer/{top} is not part of the 3.1 mapping: not copied")
    check_lf(os.path.join(inst, "init"))
    st.put_file(os.path.join(inst, "init"), "/init", 0o755)
    count += 1
    for sub, dst, mode in (("bin", "/opt/gb/bin", 0o755), ("lib", "/opt/gb/lib", 0o644), ("etc", "/etc/gb", 0o644)):
        base = os.path.join(inst, sub)
        for d, dirs, files in os.walk(base):
            dirs[:] = sorted(x for x in dirs if x != "__pycache__")
            rel_d = os.path.relpath(d, base)
            for f in sorted(files):
                if f.endswith((".pyc", ".pyo")):
                    continue
                src = os.path.join(d, f)
                if os.path.islink(src):
                    die(f"installer/{sub}/{rel_d}/{f} is a symlink (not supported)")
                rel = f if rel_d == "." else posixpath.join(rel_d.replace(os.sep, "/"), f)
                if sub == "etc" and rel in GENERATED_ETC_GB:
                    die(f"installer/etc/{rel} would overwrite a BUILD-generated file")
                check_lf(src)
                st.put_file(src, posixpath.join(dst, rel), mode)
                count += 1
    note(f"installer: {count} files")
    return count


def stage_list(st, list_path):
    out = {"bin": 0, "lib": 0, "file": 0, "link": 0, "python3": 0}
    py = None
    with open(list_path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    for no, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = re.sub(r"\s+#.*$", "", s)
        w = s.split()
        kind = w[0]
        want = {"bin": 2, "lib": 2, "file": 3, "link": 3, "python3": 2}.get(kind)
        if want is None or len(w) != want:
            die(f"initramfs.list:{no}: cannot parse {line!r}")
        if kind in ("bin", "lib", "python3", "file") and not w[1].startswith("/"):
            die(f"initramfs.list:{no}: path must be absolute")
        if kind == "bin" or kind == "lib":
            if not os.path.exists(w[1]):
                die(f"initramfs.list:{no}: {w[1]} does not exist on the builder")
            st.stage_bin(w[1])
        elif kind == "file":
            if not w[2].startswith("/") or not os.path.isfile(w[1]):
                die(f"initramfs.list:{no}: bad file directive")
            st.put_file(os.path.realpath(w[1]), w[2])
        elif kind == "link":
            if not w[1].startswith("/"):
                die(f"initramfs.list:{no}: link destination must be absolute")
            st.put_link(w[1], w[2])
        elif kind == "python3":
            py = stage_python(st, w[1])
        out[kind] += 1
    note(f"initramfs.list: {out}")
    return out, py


def stage_python(st, exe):
    m = re.search(r"python(\d+\.\d+)$", exe)
    if not m:
        die(f"python3 directive: cannot tell the version of {exe}")
    ver = m.group(1)
    st.stage_bin(exe)
    src = f"/usr/lib/python{ver}"
    if os.path.realpath(src) != src:
        die(f"{src} is not a real directory")
    nfiles = nbytes = 0
    dyn = []
    libs = set()
    for d, dirs, files in os.walk(src):
        rel = os.path.relpath(d, src)
        keep = []
        for x in sorted(dirs):
            full = os.path.join(d, x)
            if x == "__pycache__" or (rel == "." and x in PY_PRUNE):
                continue
            if os.path.islink(full):
                warn(f"python: directory symlink {full} skipped")
                continue
            keep.append(x)
        dirs[:] = keep
        os.makedirs(st.sp(d), exist_ok=True)
        for f in sorted(files):
            if f.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(d, f)
            if rel == "lib-dynload" and f.split(".")[0] not in PY_DYNLOAD_KEEP:
                continue
            if os.path.islink(full):
                tgt = os.path.realpath(full)
                if tgt.startswith(src + "/") and os.path.isfile(tgt):
                    st.put_link(full, os.readlink(full))
                else:
                    warn(f"python: {full} -> {os.readlink(full)} points outside the stdlib: skipped")
                continue
            st.put_file(full, full)
            nfiles += 1
            nbytes += os.path.getsize(full)
            if rel == "lib-dynload":
                dyn.append(f.split(".")[0])
                libs.update(ldd(full))
    for lib in sorted(libs):
        st.stage_path(lib)
    missing = PY_DYNLOAD_KEEP - set(dyn)
    if missing:
        die(f"python: lib-dynload lacks {sorted(missing)}")
    note(f"python {ver}: {nfiles} stdlib files, {nbytes / 2**20:.1f} MiB, lib-dynload {sorted(dyn)}")
    return {"version": ver, "stdlib_files": nfiles, "stdlib_bytes": nbytes, "lib_dynload": sorted(dyn)}


def norm_mod(name):
    return name.replace("-", "_")


def stage_modules(st, modules_list, krel):
    names = []
    with open(modules_list, encoding="utf-8") as f:
        for line in f:
            s = re.sub(r"#.*$", "", line).strip()
            if s:
                if len(s.split()) != 1:
                    die(f"modules.list: bad line {line!r}")
                names.append(s)
    mdir = f"/lib/modules/{krel}"
    order, builtin = [], []
    for n in names:
        r = subprocess.run(["modprobe", "-S", krel, "--show-depends", n], capture_output=True, text=True)
        if r.returncode != 0:
            die(f"module {n}: neither a module nor built in ({(r.stdout + r.stderr).strip()})")
        for line in r.stdout.splitlines():
            w = line.split()
            if not w:
                continue
            if w[0] == "insmod":
                path = w[1]
                if not path.startswith(mdir + "/"):
                    die(f"module {n}: unexpected path {path}")
                rel = path[len(mdir) + 1:]
                if rel.endswith(".ko.zst"):
                    relko = rel[:-4]
                elif rel.endswith(".ko"):
                    relko = rel
                else:
                    die(f"module {n}: unsupported module file {rel}")
                if relko not in order:
                    order.append(relko)
                    dst = st.sp(posixpath.join(st.resolve(mdir), relko))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, "wb") as out:
                        if rel.endswith(".zst"):
                            subprocess.run(["zstd", "-dcq", path], stdout=out, check=True)
                        else:
                            with open(path, "rb") as src:
                                shutil.copyfileobj(src, out)
                    st.files += 1
                    st.bytes += os.path.getsize(dst)
            elif w[0] == "builtin":
                builtin.append(w[1])
            else:
                die(f"module {n}: unexpected modprobe output {line!r}")
    resolved = sorted({norm_mod(posixpath.basename(r)[:-3]) for r in order})
    denied = [m for m in resolved for pat in MODULE_DENY if fnmatch.fnmatchcase(m, pat)]
    if denied:
        die(f"denied modules in the resolved set: {sorted(set(denied))}")
    for f in ("modules.builtin", "modules.builtin.modinfo", "modules.order"):
        st.put_file(posixpath.join(mdir, f), posixpath.join(mdir, f), 0o644)
    r = subprocess.run(["depmod", "-b", st.root, krel], capture_output=True, text=True)
    if r.returncode != 0:
        die(f"depmod failed: {(r.stdout + r.stderr).strip()}")
    with open(st.sp("/etc/gb/modules.order"), "w", newline="\n") as f:
        f.write("".join(x + "\n" for x in order))
    # every name must resolve against the staging tree (built-ins via the copied modules.builtin*)
    for n in names:
        r = subprocess.run(["modprobe", "-d", st.root, "-S", krel, "--show-depends", n], capture_output=True, text=True)
        if r.returncode != 0:
            die(f"module {n} does not resolve in staging: {(r.stdout + r.stderr).strip()}")
        for line in r.stdout.splitlines():
            w = line.split()
            if w and w[0] == "insmod":
                p = w[1]
                h = p if p.startswith(st.root + "/") else st.sp(p)
                if not os.path.isfile(os.path.realpath(h)):
                    die(f"module {n}: {p} missing in staging")
    note(f"modules: {len(names)} names -> {len(order)} .ko files, built in: {sorted(set(builtin))}")
    return {"names": len(names), "ko_files": len(order), "resolved": resolved, "builtin": sorted(set(builtin))}


def stage_fonts(st, psf_double):
    st.put_file(os.path.join(FONT_DIR, FONT_32x16), posixpath.join(FONT_DIR, FONT_32x16), 0o644)
    st.put_file(os.path.join(FONT_DIR, FONT_8x16), posixpath.join(FONT_DIR, FONT_8x16), 0o644)
    out = st.sp(posixpath.join(FONT_DIR, FONT_32x64))
    r = subprocess.run([sys.executable, "-I", "-B", psf_double, os.path.join(FONT_DIR, FONT_32x16), out],
                       capture_output=True, text=True)
    if r.returncode != 0:
        die(f"psf_double failed: {(r.stdout + r.stderr).strip()}")
    os.chmod(out, 0o644)
    st.files += 1
    st.bytes += os.path.getsize(out)
    note(r.stdout.strip())
    return r.stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", required=True)
    ap.add_argument("--installer", required=True)
    ap.add_argument("--build-id", required=True)
    ap.add_argument("--android-build", required=True)
    ap.add_argument("--krel", default="6.8.0-147-generic")
    ap.add_argument("--psf-double", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "psf_double.py"))
    a = ap.parse_args()
    os.umask(0o022)
    if os.geteuid() == 0:
        die("must not run as root")
    if not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", a.build_id):
        die(f"bad build id {a.build_id!r}")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", a.android_build):
        die(f"bad android build {a.android_build!r}")
    if os.path.lexists(a.staging):
        die(f"{a.staging} exists")
    st = Stager(a.staging)
    stage_layout(st)
    applets = stage_busybox(st)
    ninst = stage_installer(st, a.installer)
    lst, py = stage_list(st, os.path.join(a.installer, "etc", "initramfs.list"))
    if py is None:
        die("initramfs.list has no python3 directive")
    with open(st.sp("/etc/gb/build.env"), "w", newline="\n") as f:
        f.write(f"GB_BUILD_ID={a.build_id}\nGB_VOLUME_LABEL=GBSB3INST\nGB_MARKER=gbsb3/GBSB3-{a.build_id}.marker\n"
                f"GB_KREL={a.krel}\nGB_ANDROID_BUILD={a.android_build}\n")
    with open(st.sp("/etc/gb-installer-release"), "w", newline="\n") as f:
        f.write(f"Googlebook SB3 installer {a.build_id}\n")
    mods = stage_modules(st, os.path.join(a.installer, "etc", "modules.list"), a.krel)
    font = stage_fonts(st, a.psf_double)
    for d in ("/bin", "/sbin"):
        if os.path.islink(st.sp(d)) or not os.path.isdir(st.sp(d)):
            die(f"{d} is not a real directory")
    summary = {"files": st.files, "bytes": st.bytes, "links": st.links, "busybox_applets": applets,
               "installer_files": ninst, "initramfs_list": lst, "python": py, "modules": mods, "font": font,
               "warnings": warnings}
    print("STAGE-JSON " + json.dumps(summary, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
