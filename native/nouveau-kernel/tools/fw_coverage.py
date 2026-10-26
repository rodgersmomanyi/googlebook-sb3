#!/usr/bin/env python3
"""fw_coverage.py - firmware pinning / coverage check for the native Surface Book 3 tree (wave 3, item 14).

Read-only; runs on the builder (needs modinfo, dpkg-deb and zstd). Licence: Apache-2.0 (ours). Idea credit: gbOS86
(github.com/linuxkid473/gbos-x86 @f8a5c1a, tools/fetch_firmware.sh:72-80 and HANDOFF.md:211-213); no code taken.

  python3 -I fw_coverage.py --modules DIR [--modules DIR ...] --staged DIR [--staged DIR ...]
                            --source-tree DIR [--deb DEB ...] [--want MOD=REGEX ...] [--out TABLE]

--modules   DIR[@GLOB,...]: a directory searched (recursively) for *.ko, optionally only the modules whose name
            ("-" == "_") matches one of the fnmatch patterns: our native modules (inject/native/modules, all of them)
            and the SB3-relevant lapis vendor_dlkm modules (iwlwifi, btintel, SOF, HDA); each one's "modinfo -F
            firmware" is the requested set.
--staged    firmware roots as they are staged (inject/native/firmware, native/hw-gap-audit/tree/firmware): the names
            the kernel finds under /odm/firmware. FAT has no symlinks, so a WHENCE "Link:" is staged as a copy under
            the link name; it is checked under that name.
--source-tree  the extracted firmware packages the stage was taken from (lib/firmware of the debs); symlinks there
            are linux-firmware's WHENCE Link: entries, "*.zst" files are decompressed for the comparison.
--deb       the packages themselves (only listed: they give each source path its package name and version).
--want      MOD=REGEX: only report requested names of module MOD (fnmatch pattern on the module name, "-" == "_")
            that match REGEX (iwlwifi asks for every generation's ucode); modules without a --want report all.

Output (stdout or --out): the table "<requested name> <real source path> <package_version> <sha256 of the staged copy>
<status>" (status ok = staged bytes == the source bytes, DIFFERS, NO-SOURCE, MISSING = requested but not staged), then
the staged-but-unrequested names, then the Intel Bluetooth (AX201, USB 8087:0026) legacy/TLV name check.
"""
import argparse, fnmatch, hashlib, os, re, subprocess, sys

ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("--modules", action="append", default=[])
ap.add_argument("--staged", action="append", default=[])
ap.add_argument("--source-tree", required=True)
ap.add_argument("--deb", action="append", default=[])
ap.add_argument("--want", action="append", default=[])
ap.add_argument("--out")
a = ap.parse_args()


def run(cmd, data=None):
    return subprocess.run(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ---- requested names (modinfo -F firmware)
want = []
for w in a.want:
    m, _, rx = w.partition("=")
    want.append((m.replace("-", "_"), re.compile(rx)))
req = {}                                                    # name -> sorted list of modules
nmods = 0
for spec in a.modules:
    d, _, globs = spec.partition("@")
    pats = [g.replace("-", "_") for g in globs.split(",") if g]
    for root, _, files in os.walk(d):
        for f in sorted(files):
            if not f.endswith(".ko"):
                continue
            mod = f[:-3].replace("-", "_")
            if pats and not any(fnmatch.fnmatch(mod, g) for g in pats):
                continue
            nmods += 1
            r = run(["modinfo", "-F", "firmware", os.path.join(root, f)])
            names = [x.strip() for x in r.stdout.decode("utf-8", "replace").splitlines() if x.strip()]
            filt = [rx for m, rx in want if fnmatch.fnmatch(mod, m)]
            for n in names:
                if filt and not any(rx.search(n) for rx in filt):
                    continue
                req.setdefault(n, set()).add(mod)

# ---- staged names
staged = {}
for d in a.staged:
    for root, _, files in os.walk(d):
        for f in files:
            if f.endswith((".sha256", ".txt", ".md")):         # checksum lists / notes next to the firmware
                continue
            p = os.path.join(root, f)
            staged.setdefault(os.path.relpath(p, d).replace(os.sep, "/"), p)

# ---- source tree (links = WHENCE Link:) and package of each path
src = a.source_tree
pkg_of = {}
for deb in a.deb:
    info = run(["dpkg-deb", "-f", deb, "Package", "Version"]).stdout.decode().split("\n")
    pv = "_".join(x.split(": ", 1)[1] for x in info if ": " in x)
    lst = run(["dpkg-deb", "-c", deb]).stdout.decode("utf-8", "replace").splitlines()
    for l in lst:
        m = re.search(r"\./lib/firmware/(\S+?)(?: -> \S+)?$", l)
        if m:
            pkg_of[re.sub(r"\.zst$", "", m.group(1))] = pv


def source(name):
    """(real path relative to the source tree without .zst, bytes) or (None, None)"""
    for cand in (name, name + ".zst"):
        p = os.path.join(src, cand)
        if os.path.lexists(p):
            rp = os.path.realpath(p)
            rel = os.path.relpath(rp, os.path.realpath(src)).replace(os.sep, "/")
            if not os.path.isfile(rp):
                return rel, None
            b = open(rp, "rb").read()
            if rp.endswith(".zst"):
                r = run(["zstd", "-d", "-c", "-q"], b)
                b = r.stdout if r.returncode == 0 else None
            return re.sub(r"\.zst$", "", rel), b
    return None, None


rows, bad = [], 0
for n in sorted(req):
    rp, sb = source(n)
    st = staged.get(n)
    h = sha(open(st, "rb").read()) if st else "-"
    if not st:
        status = "MISSING"
    elif sb is None:
        status = "NO-SOURCE"
    elif sha(sb) == h:
        status = "ok"
    else:
        status = "DIFFERS"
    if status in ("DIFFERS",):
        bad += 1
    rows.append((n, rp or "-", pkg_of.get(rp or "", "-"), h, status, ",".join(sorted(req[n]))))

out = []
out.append(f"# fw_coverage: {nmods} modules, {len(req)} requested names (after --want), {len(staged)} staged files")
out.append("# <requested name> <real source path> <package_version> <sha256 of the staged copy> <status> <modules>")
for r in rows:
    out.append(" ".join(r))
unreq = sorted(n for n in staged if n not in req)
out.append("")
out.append(f"# staged but not requested by modinfo ({len(unreq)}): names built at run time (btintel ibt-<variant>-*, "
           "SOF, IPU, nouveau's own payload list) or unused; each with its source path, package and sha256")
for n in unreq:
    rp, sb = source(n)
    h = sha(open(staged[n], "rb").read())
    status = "ok" if sb is not None and sha(sb) == h else ("NO-SOURCE" if sb is None else "DIFFERS")
    if status == "DIFFERS":
        bad += 1
    out.append(" ".join((n, rp or "-", pkg_of.get(rp or "", "-"), h, status)))

# ---- Intel Bluetooth: the legacy name family (intel/ibt-<hw_variant>-<hw_rev>-<fw_rev>, decimal %u, btintel.c
# btintel_get_fw_name) vs the TLV family (intel/ibt-<cnvi_top>-<cnvr_top>, %04x), which newer controllers request
out.append("")
out.append("# Intel Bluetooth (AX201, USB 8087:0026): staged legacy names and where linux-firmware links them")
leg = sorted(n for n in staged if re.fullmatch(r"intel/ibt-\d+-\d+-\d+\.(sfi|ddc)", n))
tlv_staged = sorted(n for n in staged if re.fullmatch(r"intel/ibt-[0-9a-f]{4}-[0-9a-f]{4}\.(sfi|ddc)", n))
for n in leg:
    rp, _ = source(n)
    out.append(f"{n} -> {rp or '-'}")
srcdir = os.path.join(src, "intel")
tlv_src = sorted(set(re.sub(r"\.zst$", "", f) for f in (os.listdir(srcdir) if os.path.isdir(srcdir) else [])
                     if re.fullmatch(r"ibt-[0-9a-f]{4}-[0-9a-f]{4}\.(sfi|ddc)(\.zst)?", f)))
targets = sorted(set((source(n)[0] or "") for n in leg if re.search(r"ibt-[0-9a-f]{4}-[0-9a-f]{4}", source(n)[0] or "")))
out.append(f"# staged legacy names: {len(leg)}; staged TLV names: {len(tlv_staged)}; TLV files in the source: {len(tlv_src)}")
out.append("# legacy names whose linux-firmware file is a TLV file (the same bytes, staged under the legacy name): "
           + (", ".join(targets) or "none"))
out.append("# TLV names in the source (what a controller that boots in TLV mode asks for: intel/ibt-<cnvi>-<cnvr>, %04x;"
           " gbOS86 saw ibt-0040-1050.sfi on another laptop) -> their linux-firmware file, staged or not:")
for f in tlv_src:
    rp, _ = source("intel/" + f)
    out.append(f"intel/{f} -> {rp or '-'} {'staged' if ('intel/' + f) in staged else 'NOT staged'}")
out.append("# None of them is staged: if the first native boot logs 'Direct firmware load for intel/ibt-<cnvi>-<cnvr>.sfi "
           "failed', stage that name (a copy of its linux-firmware file above, like the legacy names).")
out.append("")
out.append(f"# summary: {sum(1 for r in rows if r[4] == 'ok')} ok, {sum(1 for r in rows if r[4] == 'MISSING')} requested "
           f"but missing, {sum(1 for r in rows if r[4] == 'NO-SOURCE')} without a source copy, {bad} differing")
text = "\n".join(out) + "\n"
if a.out:
    open(a.out, "w", encoding="ascii", errors="replace", newline="\n").write(text)
sys.stdout.write(text)
sys.exit(1 if bad else 0)
