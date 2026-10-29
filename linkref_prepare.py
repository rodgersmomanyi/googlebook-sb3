"""linkref_prepare.py -- make inject\\keymint\\native\\lapis_linkref.json.gz, the lapis side of build_esp.py's native
link-closure gate (link_closure.py; wave 5, readiness C-2). Run on demand when lapis.bin's vendor/system change (an OTA
moves the boot slot or rewrites it: build_esp.py then refuses the native tree until this is re-run); never run inside
build_esp.

What it does:
  1. reads lapis.bin READ-ONLY with vnni_prepare.py's carve_plan / stream_partition (the GPT, the boot slot as
     extract_slot.boot_slot returns it, or GB_SLOT / --slot, and the LP metadata slot of that suffix);
  2. streams vendor_<x> and system_<x> to the builder (sha256 on both ends) into <builder root>/<run>/ and extracts each
     with fsck.erofs --extract, removing each image right after its extraction; unpacks the payloads of the APEXes that
     hold LLNDK libraries (runtime = bionic libc/libm/libdl, neuralnetworks, tethering, virt) the same way. Every
     builder command runs under nice -n 19 ionice -c3; /build must keep MIN_FREE bytes free at the peak;
  3. runs link_closure.py `summarize` there (python3 -I) with the slot, the two partitions' sizes and sha256 and their
     build fingerprints (read from the extracted build.prop files);
  4. fetches the .json.gz, checks that it loads, has format gb-linkref-1, the streamed pin, wpa_supplicant / hostapd /
     libssl / libcrypto and a source for every LLNDK name, and writes inject\\keymint\\native\\lapis_linkref.json.gz;
     an existing file with other bytes is MOVED (never deleted) to archive\\linkref-prev\\<time>\\;
  5. removes the builder scratch directory (below the builder root, never /build/dist*) unless --keep-tmp.

  python -I linkref_prepare.py [--slot a|b] [--keep-tmp] [--out FILE] [--builder-root DIR]
  python -I linkref_prepare.py --check [--slot a|b] [--out FILE]   (no builder: does the reference's pin match
                                                                     lapis.bin's boot slot? exit 0 = yes)
Exit 0 = written / unchanged / pin OK, 1 = a check failed (nothing written), 2 = usage.
The reference holds symbol names and library names of lapis's own files only (no code); it stays in inject\\ and is
never published (it describes the user's own image). Our own code (Apache-2.0).
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

HERE = os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__))   # workspace (README: GB_HOME)
OUT = os.path.join(HERE, "inject", "keymint", "native", "lapis_linkref.json.gz")
PREV_DIR = os.path.join(HERE, "archive", "linkref-prev")
BUILDER_ROOT = "/build/linkref"
MIN_FREE = 2 * 10**9                      # /build keeps at least this much free at the peak
APEXES = ("com.android.runtime", "com.google.android.neuralnetworks", "com.google.android.tethering",
          "com.google.android.virt")       # the APEXes whose lib64 hold LLNDK libraries lapis's system/lib64 lacks
MUST_HAVE = ("vendor/bin/hw/wpa_supplicant", "vendor/bin/hw/hostapd", "vendor/lib64/libssl.so",
             "vendor/lib64/libcrypto.so", "system/libc.so")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class LinkrefError(Exception):
    pass


def niced(cmd):
    """The builder command line: the whole shell command under nice -n 19 ionice -c3."""
    return "nice -n 19 ionice -c3 sh -c " + shlex.quote(cmd)


def run(vp, cmd, data=None, timeout=3600):
    rc, out, err = vp.builder(niced(cmd), data, timeout)
    if rc != 0:
        raise LinkrefError(f"builder command failed (rc {rc}): {str(err).strip()[-500:]}")
    return out


def safe_root(root):
    r = root.rstrip("/")
    if not r.startswith("/build/") or r.startswith("/build/dist") or ".." in r.split("/") or "'" in r:
        raise LinkrefError(f"builder root {root!r} must be below /build/ and not /build/dist*")
    return r


def stream_and_extract(vp, base, name, exts, size, rdir):
    out = run(vp, "df -B1 --output=avail /build | tail -n 1")
    free = int(out.split()[0])
    if free - int(2.5 * size) < MIN_FREE:
        raise LinkrefError(f"builder /build has {free:,} B free; {name} needs about {int(2.5 * size):,} B and /build "
                           f"must keep {MIN_FREE:,} B")
    h = hashlib.sha256()
    p = subprocess.Popen(vp.SSH + [niced(f"set -e; mkdir -p '{rdir}'; cat > '{rdir}/{name}.img'")],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    total = 0
    try:
        for b in vp.stream_partition(vp.LAPIS, base, exts):
            h.update(b)
            p.stdin.write(b)
            total += len(b)
        p.stdin.close()
    except BrokenPipeError:
        pass
    _, err = p.communicate(timeout=3600)
    if p.returncode != 0 or total != size:
        raise LinkrefError(f"streaming {name} failed (rc {p.returncode}, {total:,} of {size:,} B): "
                           f"{err.decode(errors='replace').strip()[-300:]}")
    local = h.hexdigest()
    run(vp, f"set -e; cd '{rdir}'; s=$(sha256sum '{name}.img' | cut -c1-64); [ \"$s\" = '{local}' ] || "
            f"{{ echo 'sha256 mismatch after the transfer' >&2; exit 3; }}; "
            f"fsck.erofs --extract='{rdir}/{name}' --no-preserve '{name}.img' >/dev/null; rm -f '{name}.img'")
    print(f"  {name}: {size:,} B streamed, sha256 {local} (equal on the builder), extracted", flush=True)
    return local


def check_reference(ref, lc, rec, slot):
    """Problems of a freshly made reference (empty list = good)."""
    problems = []
    if ref.get("format") != lc.FORMAT:
        problems.append(f"format {ref.get('format')!r}")
    if ref.get("lapis", {}).get("partitions") != rec["partitions"] or ref.get("lapis", {}).get("slot") != slot:
        problems.append("the reference's pin is not the streamed partitions")
    for r in MUST_HAVE:
        if r not in ref.get("elfs", {}):
            problems.append(f"{r} missing from the reference")
    llndk = ref.get("llndk") or []
    if not llndk:
        problems.append("no LLNDK list")
    lost = sorted(n for n in llndk if f"system/{n}" not in ref.get("elfs", {}))
    if lost:
        problems.append(f"LLNDK libraries without a source (add their APEX to APEXES): {lost}")
    return problems


def check_pin(vp, lc, path, slot):
    """--check: the existing reference against lapis.bin's (boot) slot, as build_esp.py's N0 does."""
    if not os.path.isfile(path):
        print(f"linkref_prepare --check: {path} is missing", file=sys.stderr)
        return 1
    ref = lc.load_ref(path)
    probs = lc.pin_problems(ref, vp.LAPIS, slot, vp)
    for p in probs:
        print(f"linkref_prepare --check: {p}", file=sys.stderr)
    if not probs:
        fp = ref["lapis"].get("fingerprints", {}).get("ro.vendor.build.fingerprint", "?")
        print(f"{path}: pin OK (slot {slot}, {fp}, vendor/system sha256 equal)")
    return 1 if probs else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--slot", choices=("a", "b"))
    ap.add_argument("--keep-tmp", action="store_true")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--builder-root", default=BUILDER_ROOT)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    vp = load("vnni_prepare", os.path.join(HERE, "vnni_prepare.py"))
    xs = load("extract_slot", os.path.join(HERE, "extract_slot.py"))
    lc = load("link_closure", os.path.join(HERE, "link_closure.py"))
    slot = a.slot or os.environ.get("GB_SLOT", "").strip().lower() or xs.boot_slot(vp.LAPIS)
    if slot not in ("a", "b"):
        print(f"linkref_prepare: bad slot {slot!r}", file=sys.stderr)
        return 2
    if a.check:
        return check_pin(vp, lc, a.out, slot)
    try:
        root = safe_root(a.builder_root)
    except LinkrefError as e:
        print(f"linkref_prepare: {e}", file=sys.stderr)
        return 2
    run_id = time.strftime("%Y%m%d-%H%M%S")
    rdir = f"{root}/{run_id}"
    made = False                                            # remove only the directory this run created
    try:
        run(vp, f"[ ! -e '{rdir}' ] || {{ echo '{rdir} exists' >&2; exit 4; }}; mkdir -p '{rdir}'")
        made = True
        base, plan, _ = vp.carve_plan(vp.LAPIS, slot)
        rec = {"slot": slot, "partitions": {}, "fingerprints": {}}
        for key in ("vendor", "system"):
            name, exts, size = plan[key]
            rec["partitions"][name] = {"bytes": size, "sha256": stream_and_extract(vp, base, name, exts, size, rdir)}
        vname, sname = plan["vendor"][0], plan["system"][0]
        for prop_file, key in ((f"{rdir}/{vname}/build.prop", "ro.vendor.build.fingerprint"),
                               (f"{rdir}/{sname}/system/build.prop", "ro.system.build.fingerprint")):
            v = run(vp, f"sed -n 's/^{key}=//p' '{prop_file}' | head -n 1").decode().strip()
            if not v:
                raise LinkrefError(f"{key} not found in {prop_file}")
            rec["fingerprints"][key] = v
        with open(os.path.join(HERE, "link_closure.py"), "rb") as f:
            run(vp, f"cat > '{rdir}/link_closure.py'", f.read())
        run(vp, f"cat > '{rdir}/record.json'", json.dumps(rec, sort_keys=True).encode())
        apex_args = ""
        for ax in APEXES:
            run(vp, f"set -e; cd '{rdir}'; f='{sname}/system/apex/{ax}.apex'; if [ -f \"$f\" ]; then "
                    f"mkdir -p 'apex/{ax}.d'; python3 -I -c 'import sys, zipfile; "
                    f"zipfile.ZipFile(sys.argv[1]).extract(\"apex_payload.img\", sys.argv[2])' \"$f\" 'apex/{ax}.d'; "
                    f"fsck.erofs --extract='apex/{ax}' --no-preserve 'apex/{ax}.d/apex_payload.img' >/dev/null; "
                    f"rm -rf 'apex/{ax}.d'; fi")
            if ax != "com.android.runtime":
                apex_args += f" --apex-lib '{rdir}/apex/{ax}/lib64'"
        run(vp, f"set -e; cd '{rdir}'; python3 -I link_closure.py summarize --vendor '{vname}' "
                f"--system '{sname}/system' --bionic 'apex/com.android.runtime/lib64/bionic'{apex_args} "
                f"--record record.json --out linkref.json.gz")
        raw = run(vp, f"cat '{rdir}/linkref.json.gz'")
    except (LinkrefError, vp.PrepError, OSError, ValueError, subprocess.TimeoutExpired) as e:
        print(f"linkref_prepare: {e}", file=sys.stderr)
        if made and not a.keep_tmp:
            vp.builder(niced(f"rm -rf '{rdir}'"))
        return 1
    if not a.keep_tmp:
        vp.builder(niced(f"rm -rf '{rdir}'"))
    try:
        ref = json.loads(gzip.decompress(raw))
    except (OSError, ValueError) as e:
        print(f"linkref_prepare: refusing to write: the reference does not load ({e})", file=sys.stderr)
        return 1
    problems = check_reference(ref, lc, rec, slot)
    if problems:
        print("linkref_prepare: refusing to write: " + "; ".join(problems), file=sys.stderr)
        return 1
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    if os.path.exists(a.out):
        with open(a.out, "rb") as f:
            old = f.read()
        if old == raw:
            print(f"{a.out}: unchanged ({len(ref['elfs'])} ELFs)")
            return 0
        dst = os.path.join(PREV_DIR, run_id)
        os.makedirs(dst, exist_ok=True)
        shutil.move(a.out, os.path.join(dst, os.path.basename(a.out)))
        print(f"previous reference moved to {dst}")
    tmp = a.out + ".part"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, a.out)
    print(f"{a.out}: {len(raw):,} B, {len(ref['elfs'])} ELFs, slot {slot}, "
          f"{rec['fingerprints']['ro.vendor.build.fingerprint']}, sha256 {hashlib.sha256(raw).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
