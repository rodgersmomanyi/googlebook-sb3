"""vnni_prepare.py -- prepare the VNNI libz patch for build_esp.py (wave 5; run on demand, never inside build_esp).

What it does (lead decision W5, C:\\GB-build\\native\\vnni-perf\\INTEGRATION.md section 6 "As built"):
  1. reads lapis.bin READ-ONLY: the GPT, the boot slot (extract_slot.boot_slot, the slot build_esp.py builds; GB_SLOT
     or --slot override it) and the LP metadata of super. The metadata slot is the one that belongs to the A/B suffix
     (_a -> metadata slot 0, _b -> 1, as liblp's SlotNumberForSlotSuffix and vnni-perf\\tools\\carve_stream.py do; the
     first metadata slot that merely names system_b can be stale: carve_lp.py's choice is not used here);
  2. streams system_<x> and vendor_<x> to the builder (sha256 on both ends) and extracts them there with fsck.erofs
     --extract (lapis's partitions are LZ4-compressed EROFS; erofs-utils is installed on the builder). Only the target
     files and the two build.prop files come back (a tar of regular files; a symlink is refused); the builder scratch
     directory (below /build/w5vnni, never /build/dist*) is removed right away;
  3. checks each deployable target of vnni-perf\\patcher\\manifest.json (the targets with a "deploy" block: system and
     vendor libz.so) against its orig_sha256. A mismatch skips that target cleanly (it then stays under k15 emulation,
     which is always correct);
  4. runs vnni-perf\\patcher\\vnni_patch.py (python -I, its own process) on the matching files, re-checks the
     patched sha256 against the manifest's patched_sha256 and that the bytes differ only inside the manifest's edits;
  5. reads ro.system.build.fingerprint / ro.vendor.build.fingerprint from the partitions' own build.prop (the values
     init loads before early-init and build_esp's bind actions are gated on);
  6. writes C:\\GB-x86\\inject\\vnni\\<id>\\{system/lib64/libz.so, vendor/lib64/libz.so, manifest.json}, <id> =
     "<build id>_<incremental>-<first 8 hex of sha256(system fp + LF + vendor fp)>". The folder is built in
     C:\\GB-x86\\cache\\vnni-tmp and renamed into place; an existing folder with other bytes is MOVED (never deleted)
     to C:\\GB-x86\\archive\\vnni-prev\\<id>-<time>\\.
build_esp.py only packs inject\\vnni\\<id>\\ (both trees, /odm/vnni/<id>/) and appends the fingerprint-gated binds.
Patched Google binaries never go into the public repository (collect.py excludes inject\\vnni).

  python -I vnni_prepare.py [--slot a|b] [--info] [--keep-tmp]
    --info      print the slot, the metadata slot and the extents that would be carved, then exit (no builder use)
Exit 0 = done (or nothing to deploy: every target skipped), 1 = a check failed (nothing written), 2 = usage.
Our own code (Apache-2.0); idea credit for the libz finding: gbOS86 (no code copied).
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__))   # workspace (README: GB_HOME)
LAPIS = os.path.join(HERE, "lapis.bin")
OUT_DIR = os.path.join(HERE, "inject", "vnni")
TMP_DIR = os.path.join(HERE, "cache", "vnni-tmp")
PREV_DIR = os.path.join(HERE, "archive", "vnni-prev")
GB_BUILD = os.environ.get("GB_BUILD") or (HERE if os.path.isdir(os.path.join(HERE, "native")) else r"C:\GB-build")
VNNI = os.path.join(GB_BUILD, "native", "vnni-perf")
PATCHER = os.path.join(VNNI, "patcher", "vnni_patch.py")
PATCHER_MANIFEST = os.path.join(VNNI, "patcher", "manifest.json")
SSH = ["ssh", "-i", os.environ.get("GB_BUILDER_KEY") or r"C:\GB-build\builder_key",   # never commit the key
       "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=60",
       os.environ.get("GB_BUILDER") or "builder@192.168.115.132"]
BUILDER_ROOT = "/build/w5vnni"              # builder scratch (/build, never /build/dist*); one run-id dir per run
BUILDER_MIN_FREE = 6 * 10**9                # /build must keep this much free at the peak (as build_esp's payload)
SECTOR = 512
FORMAT = "gb-vnni-1"
# where each deployable target lives: manifest path prefix -> (partition, path inside the partition image, the
# build.prop inside the image, the fingerprint property). system is system-as-root (its image root holds system/).
PARTS = {"system": ("system", "system/", "system/build.prop", "ro.system.build.fingerprint"),
         "vendor": ("vendor", "", "build.prop", "ro.vendor.build.fingerprint")}
PROP_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/:+,@~")   # == vnni_patch _PROP_SAFE
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
FP_RE = re.compile(r"[^/]+/[^/]+/[^/:]+:[^/]+/([^/]+)/([^:/]+):[^/]+/[^/]+")


class PrepError(Exception):
    pass


def sha(b):
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------------------------------------ lapis.bin (read-only)
def gpt(f):
    """{name: (byte offset, byte size, attributes)} of the GPT of the open image f."""
    f.seek(SECTOR)
    hdr = f.read(92)
    if hdr[:8] != b"EFI PART":
        raise PrepError("lapis.bin has no GPT")
    ent_lba, n, esz = struct.unpack_from("<QII", hdr, 72)
    f.seek(ent_lba * SECTOR)
    raw = f.read(n * esz)
    parts = {}
    for i in range(n):
        e = raw[i * esz:(i + 1) * esz]
        if e[:16] == bytes(16):
            continue
        first, last, attr = struct.unpack_from("<QQQ", e, 32)
        parts[e[56:128].decode("utf-16-le").rstrip("\0")] = (first * SECTOR, (last - first + 1) * SECTOR, attr)
    return parts


def boot_slot(parts):
    """extract_slot.boot_slot's rule on the vbmeta_<x> GPT attributes: the highest priority among slots marked
    successful, else among slots with tries left, else a."""
    at = {}
    for x in "ab":
        if "vbmeta_" + x in parts:
            a = parts["vbmeta_" + x][2]
            at[x] = ((a >> 48) & 15, (a >> 52) & 15, (a >> 56) & 1)
    for ok in (lambda p, t, s: p and s, lambda p, t, s: p and t):
        cand = [x for x in "ab" if x in at and ok(*at[x])]
        if cand:
            return max(cand, key=lambda x: (at[x][0], x == "a")), at
    return "a", at


def lp_metadata(f, base):
    """[{partition name: (attributes, [(sectors, target type, target data)])}] per LP metadata slot of super at base."""
    f.seek(base + 4096)
    geo = f.read(4096)
    if struct.unpack_from("<I", geo, 0)[0] != 0x616C4467:
        raise PrepError("bad LP geometry magic")
    max_size, slot_count, _ = struct.unpack_from("<III", geo, 40)
    if not 0 < slot_count <= 8 or not 0 < max_size <= 1 << 20:
        raise PrepError(f"odd LP geometry (slots {slot_count}, metadata size {max_size})")
    slots = []
    for s in range(slot_count):
        f.seek(base + 3 * 4096 + s * max_size)
        m = f.read(max_size)
        magic, _maj, _min, hsize = struct.unpack_from("<IHHI", m, 0)
        if magic != 0x414C5030:
            slots.append({})
            continue
        t = m[hsize:]
        p_off, p_n, p_sz, e_off, e_n, e_sz = struct.unpack_from("<6I", m, 80)
        parts = {}
        for i in range(p_n):
            pe = t[p_off + i * p_sz:p_off + (i + 1) * p_sz]
            name = pe[:36].split(b"\0")[0].decode()
            attr, first, cnt, _g = struct.unpack_from("<IIII", pe, 36)
            exts = []
            for j in range(first, first + cnt):
                ee = t[e_off + j * e_sz:e_off + (j + 1) * e_sz]
                n_sec, ttype, tdata, _src = struct.unpack_from("<QIQI", ee, 0)
                exts.append((n_sec, ttype, tdata))
            parts[name] = (attr, exts)
        slots.append(parts)
    return slots


def carve_plan(lapis, slot):
    """(super offset, {'system': (name, extents, bytes), 'vendor': ...}, slot attributes) for the A/B slot `slot`,
    from the LP metadata slot that belongs to that suffix."""
    with open(lapis, "rb") as f:
        parts = gpt(f)
        if "super" not in parts:
            raise PrepError("lapis.bin has no super partition")
        base = parts["super"][0]
        md = lp_metadata(f, base)
    _, at = boot_slot(parts)
    k = "ab".index(slot)
    if k >= len(md) or not md[k]:
        raise PrepError(f"LP metadata slot {k} (for _{slot}) is empty")
    plan = {}
    for key, (pname, _, _, _) in PARTS.items():
        name = f"{pname}_{slot}"
        if name not in md[k]:
            raise PrepError(f"{name} is not in LP metadata slot {k}")
        attr, exts = md[k][name]
        if not exts or any(t not in (0, 1) for _, t, _ in exts):
            raise PrepError(f"{name}: no extents or an unknown extent type in metadata slot {k}")
        plan[key] = (name, exts, sum(n for n, _, _ in exts) * SECTOR)
    return base, plan, at


def stream_partition(lapis, base, exts, chunk=16 << 20):
    """The partition's bytes from lapis.bin (opened read-only), in chunks."""
    with open(lapis, "rb") as f:
        for n_sec, ttype, tdata in exts:
            left = n_sec * SECTOR
            if ttype == 0:
                f.seek(base + tdata * SECTOR)
            while left:
                k = min(left, chunk)
                b = f.read(k) if ttype == 0 else bytes(k)
                if len(b) != k:
                    raise PrepError("short read from lapis.bin")
                yield b
                left -= k


# ------------------------------------------------------------------------------------------------ builder
def builder(cmd, data=None, timeout=1800):
    r = subprocess.run(SSH + [cmd], input=data, capture_output=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr.decode(errors="replace")


def builder_extract(name, chunks, size, wanted, rdir):
    """Stream one partition image into rdir/<name>.img on the builder, check its sha256 there, extract it with
    fsck.erofs --extract and return ({image path: bytes} of the wanted regular files that exist, image sha256).
    The image and the extraction are removed before returning."""
    img, xd = f"{rdir}/{name}.img", f"{rdir}/{name}.x"
    rc, out, err = builder(f"df -B1 --output=avail /build | tail -n 1")
    if rc != 0:
        raise PrepError(f"builder preflight failed: {err.strip()}")
    free = int(out.split()[0])
    need = 4 * size                                  # image + extraction (~1.4x for system) + margin
    if free - need < BUILDER_MIN_FREE:
        raise PrepError(f"builder /build has {free:,} B free; {name} needs about {need:,} B and must keep "
                        f"{BUILDER_MIN_FREE:,} B")
    h = hashlib.sha256()
    p = subprocess.Popen(SSH + [f"set -e; mkdir -p '{rdir}'; cat > '{img}'"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    total = 0
    try:
        for b in chunks:
            h.update(b)
            p.stdin.write(b)
            total += len(b)
        p.stdin.close()
    except BrokenPipeError:
        pass
    out, err = p.communicate(timeout=1800)
    if p.returncode != 0 or total != size:
        raise PrepError(f"streaming {name} to the builder failed (rc {p.returncode}, {total:,} of {size:,} B): "
                        f"{err.decode(errors='replace').strip()[-300:]}")
    local = h.hexdigest()
    paths = " ".join(f"'{w}'" for w in sorted(wanted))
    script = (f"set -e; cd '{rdir}'; s=$(sha256sum '{name}.img' | cut -c1-64); echo \"SHA $s\" >&2; "
              f"[ \"$s\" = '{local}' ] || {{ echo 'sha256 mismatch after the transfer' >&2; exit 3; }}; "
              f"rm -rf '{xd}'; nice -n 19 ionice -c3 fsck.erofs --extract='{xd}' --no-preserve '{name}.img' >/dev/null; "
              f"rm -f '{name}.img'; cd '{xd}'; for w in {paths}; do [ -e \"$w\" ] || [ -L \"$w\" ] || "
              f"echo \"MISSING $w\" >&2; done; "
              f"tar -cf - --ignore-failed-read $(for w in {paths}; do [ -e \"$w\" ] || [ -L \"$w\" ] && echo \"$w\"; done) "
              f"2>/dev/null; cd '{rdir}'; rm -rf '{xd}'")
    rc, out, err = builder(script)
    if rc != 0:
        builder(f"rm -rf '{img}' '{xd}'")
        raise PrepError(f"extracting {name} on the builder failed (rc {rc}): {err.strip()[-400:]}")
    got = {}
    with tarfile.open(fileobj=io.BytesIO(out), mode="r:") as tf:
        for m in tf.getmembers():
            if m.name not in wanted:
                raise PrepError(f"{name}: unexpected tar member {m.name!r}")
            if not m.isreg():
                raise PrepError(f"{name}: {m.name} is not a regular file in the image (refused)")
            got[m.name] = tf.extractfile(m).read()
    missing = sorted(set(wanted) - set(got))
    print(f"  {name}: {total:,} B streamed, sha256 {local} (equal on the builder); extracted with fsck.erofs; "
          f"fetched {sorted(got)}" + (f"; absent: {missing}" if missing else ""), flush=True)
    return got, local


# ------------------------------------------------------------------------------------------------ the prepare step
def read_prop(data, name):
    """Value of `name` in build.prop bytes (last assignment wins, as init's loader; == vnni_patch.read_prop)."""
    val = None
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == name:
            val = v.strip()
    return val


def fp_id(sys_fp, ven_fp):
    """<build id>_<incremental>-<8 hex>: build id and incremental from the system fingerprint
    (brand/product/device:release/ID/INCREMENTAL:type/tags), the hash over both fingerprints."""
    m = FP_RE.fullmatch(sys_fp or "")
    if not m:
        raise PrepError(f"cannot parse the system fingerprint {sys_fp!r}")
    i = f"{m.group(1)}_{m.group(2)}-{sha((sys_fp + chr(10) + (ven_fp or '')).encode())[:8]}"
    if not ID_RE.fullmatch(i):
        raise PrepError(f"unusable id {i!r}")
    return i


def deployable(manifest):
    """[(key, target)] of the patcher manifest targets with a deploy block, keyed 'system'/'vendor' by path."""
    out = []
    for t in manifest["targets"]:
        if not t.get("deploy"):
            continue
        key = t["path"].split("/", 1)[0]
        d = t["deploy"]
        if key not in PARTS or d.get("gate_prop") != PARTS[key][3] or d.get("bind") != "/" + t["path"]:
            raise PrepError(f"patcher manifest target {t.get('name')}: unexpected deploy block {d}")
        out.append((key, t))
    if not out:
        raise PrepError("the patcher manifest has no deployable target")
    return out


def run_patcher(in_root, work, patcher=PATCHER, manifest=PATCHER_MANIFEST):
    """vnni_patch.py in its own python -I process; returns its out-manifest dict."""
    outm = os.path.join(work, "out-manifest.json")
    cmd = [sys.executable, "-I", "-B", patcher, "--manifest", manifest, "--in-root", in_root,
           "--out-root", os.path.join(work, "out"), "--out-manifest", outm]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    print("  vnni_patch.py: " + " | ".join(ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip())[-900:],
          flush=True)
    if r.returncode != 0:
        raise PrepError(f"vnni_patch.py refused a target (exit {r.returncode}): the manifest and a binary disagree")
    with open(outm, encoding="utf-8") as fh:
        return json.load(fh)


def prepare(files, slot, part_info, out_dir=OUT_DIR, tmp_dir=TMP_DIR, prev_dir=PREV_DIR, patcher=PATCHER,
            manifest_path=PATCHER_MANIFEST, keep_tmp=False):
    """files: {'system': {image path: bytes}, 'vendor': {...}} (the targets and build.prop of each partition);
    part_info: {'system': {'name': .., 'bytes': .., 'sha256': ..}, ...}. Returns (folder or None, record dict)."""
    with open(manifest_path, "rb") as fh:
        mraw = fh.read()
    manifest = json.loads(mraw.decode("utf-8"))
    praw = open(patcher, "rb").read()
    fps = {}
    for key, (_, _, prop_path, prop) in PARTS.items():
        bp = files.get(key, {}).get(prop_path)
        if bp is None:
            raise PrepError(f"{key}: no {prop_path} in the partition image")
        v = read_prop(bp, prop)
        if not v or v == "*" or any(c not in PROP_SAFE for c in v) or not FP_RE.fullmatch(v):
            raise PrepError(f"{key} build.prop: {prop} missing or not init-safe ({v!r})")
        fps[prop] = v
    ident = fp_id(fps["ro.system.build.fingerprint"], fps["ro.vendor.build.fingerprint"])
    work = tempfile.mkdtemp(prefix="vnni_prep_", dir=tmp_dir if os.path.isdir(tmp_dir) else None)
    try:
        in_root = os.path.join(work, "in")
        targets, skipped = [], []
        for key, t in deployable(manifest):
            _, prefix, prop_path, prop = PARTS[key]
            ipath = prefix + t["path"].split("/", 1)[1]
            data = files.get(key, {}).get(ipath)
            if data is None:
                skipped.append({"name": t["name"], "reason": f"/{t['path']} absent from {part_info[key]['name']}"})
                continue
            if sha(data) != t["orig_sha256"]:
                skipped.append({"name": t["name"], "reason": f"sha256 {sha(data)} != orig_sha256 {t['orig_sha256']}"})
                continue
            for rel, b in ((t["path"], data), (key + "/build.prop", files[key][prop_path])):
                p = os.path.join(in_root, *rel.split("/"))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as fh:
                    fh.write(b)
            targets.append((key, t, data))
        for s in skipped:
            print(f"  skip {s['name']}: {s['reason']} (it stays under k15 emulation)", flush=True)
        if not targets:
            print("nothing to deploy: every target was skipped; nothing written", flush=True)
            return None, {"id": ident, "targets": [], "skipped": skipped}
        om = run_patcher(in_root, work, patcher, manifest_path)
        res = {r["name"]: r for r in om["results"]}
        recs = []
        for key, t, orig in targets:
            r = res.get(t["name"], {})
            if r.get("status") != "patched":
                raise PrepError(f"{t['name']}: vnni_patch status {r.get('status')} ({r.get('note')})")
            new = open(os.path.join(work, "out", *t["out"].split("/")), "rb").read()
            if sha(new) != t["patched_sha256"] or r.get("patched_sha256") != t["patched_sha256"]:
                raise PrepError(f"{t['name']}: patched sha256 {sha(new)} != manifest {t['patched_sha256']}")
            spans = [(int(e["file_off"], 16), len(bytes.fromhex(e["orig"]))) for e in r["edits"]]
            diff = [i for i in range(len(orig)) if orig[i] != new[i]] if len(orig) == len(new) else None
            if diff is None or not diff or any(not any(o <= i < o + n for o, n in spans) for i in diff):
                raise PrepError(f"{t['name']}: the patched file differs outside the manifest's edits")
            d = r.get("deploy", {})
            if d.get("status") != "bind" or d.get("gate_value") != fps[t["deploy"]["gate_prop"]]:
                raise PrepError(f"{t['name']}: the patcher's bind gate {d} != the partition fingerprint")
            recs.append({"file": t["path"], "bind": t["deploy"]["bind"], "gate_prop": t["deploy"]["gate_prop"],
                         "gate_value": fps[t["deploy"]["gate_prop"]], "orig_sha256": t["orig_sha256"],
                         "patched_sha256": t["patched_sha256"], "size": len(new),
                         "edits": [{"file_off": e["file_off"], "orig": e["orig"], "new": e["new"]} for e in r["edits"]],
                         "changed_bytes": len(diff), "_data": new})
        rec = {"format": FORMAT, "id": ident, "build": manifest.get("build"), "slot": slot,
               "fingerprints": fps,
               "patcher": {"vnni_patch.py_sha256": sha(praw), "manifest.json_sha256": sha(mraw)},
               "partitions": {part_info[k]["name"]: {"bytes": part_info[k]["bytes"], "sha256": part_info[k]["sha256"]}
                              for k in sorted(part_info)},
               "targets": [{k: v for k, v in x.items() if k != "_data"} for x in recs],
               "skipped": skipped}
        mbytes = (json.dumps(rec, indent=1, sort_keys=True) + "\n").encode()
        stage = os.path.join(work, ident)
        for x in recs:
            p = os.path.join(stage, *x["file"].split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(x["_data"])
        with open(os.path.join(stage, "manifest.json"), "wb") as fh:
            fh.write(mbytes)
        folder = os.path.join(out_dir, ident)
        os.makedirs(out_dir, exist_ok=True)
        if os.path.exists(folder):
            if folder_files(folder) == folder_files(stage):
                print(f"{folder}: unchanged (byte-identical)", flush=True)
                return folder, rec
            os.makedirs(prev_dir, exist_ok=True)
            dst = os.path.join(prev_dir, f"{ident}-{time.strftime('%Y%m%d-%H%M%S')}")
            os.replace(folder, dst)
            print(f"moved the superseded {folder} to {dst} (not deleted)", flush=True)
        os.replace(stage, folder)
        print(f"wrote {folder}: " + ", ".join(f"{x['file']} {x['patched_sha256'][:16]}..." for x in recs)
              + f", manifest.json {sha(mbytes)[:16]}...", flush=True)
        return folder, rec
    finally:
        if keep_tmp:
            print(f"(kept {work})")
        else:
            shutil.rmtree(work, ignore_errors=True)


def folder_files(d):
    """{relative path: bytes} of every file below d."""
    out = {}
    for dp, _, fs in os.walk(d):
        for n in fs:
            p = os.path.join(dp, n)
            out[os.path.relpath(p, d).replace("\\", "/")] = open(p, "rb").read()
    return out


def check_current_slot(lapis=LAPIS, out_dir=OUT_DIR, slot=None):
    """(F9) Compare inject\\vnni\\<id>\\manifest.json slot and partition byte sizes against lapis.bin's current boot
    slot (GPT + LP metadata only; no builder). Returns (ok, [messages])."""
    with open(lapis, "rb") as f:
        parts = gpt(f)
    auto, _ = boot_slot(parts)
    slot = slot or os.environ.get("GB_SLOT", "").strip().lower() or auto
    _, plan, _ = carve_plan(lapis, slot)
    names = sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []
    if not names:
        return False, [f"no prepared folder in {out_dir} for slot _{slot}"]
    probs, matched = [], []
    for n in names:
        mp = os.path.join(out_dir, n, "manifest.json")
        if not os.path.isfile(mp):
            probs.append(f"{n}: no manifest.json")
            continue
        m = json.load(open(mp, encoding="utf-8"))
        mslot = m.get("slot")
        mparts = m.get("partitions") or {}
        if mslot != slot:
            probs.append(f"{n}: manifest slot _{mslot} != current boot slot _{slot}")
            continue
        bad = [f"{pname} manifest {mparts.get(pname, {}).get('bytes')} B != LP {sz} B"
               for _, (pname, _, sz) in sorted(plan.items()) if mparts.get(pname, {}).get("bytes") != sz]
        if bad:
            probs.append(f"{n}: " + "; ".join(bad))
        else:
            matched.append(n)
    if not matched:
        return False, probs or [f"no folder in {out_dir} matches slot _{slot}"]
    return not probs, ([f"OK: {', '.join(matched)} matches slot _{slot} ("
                        + ", ".join(f"{pname}={sz:,} B" for _, (pname, _, sz) in sorted(plan.items())) + ")"] + probs)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vnni_prepare.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slot", choices=["a", "b"], help="A/B slot (default: GB_SLOT, else lapis.bin's boot slot)")
    ap.add_argument("--info", action="store_true", help="print the carve plan only (no builder, nothing written)")
    ap.add_argument("--check-slot", action="store_true",
                    help="check that inject\\vnni\\<id> matches lapis.bin's boot slot and LP partition sizes (exit 0/1)")
    ap.add_argument("--keep-tmp", action="store_true", help="keep the local work directory (debugging)")
    a = ap.parse_args(argv)
    t0 = time.time()
    try:
        if a.check_slot:
            ok, msgs = check_current_slot(LAPIS, OUT_DIR, a.slot)
            for msg in msgs:
                print(msg, flush=True)
            return 0 if ok else 1
        with open(LAPIS, "rb") as f:
            parts = gpt(f)
        auto, at = boot_slot(parts)
        slot = a.slot or os.environ.get("GB_SLOT", "").strip().lower() or auto
        if slot not in ("a", "b"):
            raise PrepError(f"bad slot {slot!r}")
        print(f"lapis.bin: {os.path.getsize(LAPIS):,} B (opened read-only); slot attributes {at} -> boot slot {auto}; "
              f"preparing slot {slot}", flush=True)
        base, plan, _ = carve_plan(LAPIS, slot)
        for key, (name, exts, size) in plan.items():
            print(f"  {name}: LP metadata slot {'ab'.index(slot)}, {len(exts)} extent(s) "
                  f"{[(n, t, d) for n, t, d in exts][:3]}, {size:,} B", flush=True)
        if a.info:
            return 0
        manifest = json.load(open(PATCHER_MANIFEST, encoding="utf-8"))
        wanted = {k: set() for k in PARTS}
        for key, t in deployable(manifest):
            wanted[key].add(PARTS[key][1] + t["path"].split("/", 1)[1])
        for key in PARTS:
            wanted[key].add(PARTS[key][2])
        rdir = f"{BUILDER_ROOT}/{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
        files, info = {}, {}
        try:
            for key, (name, exts, size) in plan.items():
                got, h = builder_extract(name, stream_partition(LAPIS, base, exts), size, wanted[key], rdir)
                files[key] = got
                info[key] = {"name": name, "bytes": size, "sha256": h}
        finally:
            rc, _, err = builder(f"rm -rf '{rdir}'; rmdir '{BUILDER_ROOT}' 2>/dev/null; true")
            print(f"  builder scratch {rdir} removed (rc {rc})", flush=True)
        # the user's VM may run meanwhile (it writes /data and metadata, never these read-only partitions): the plan
        # and every carved byte must be the same after the extraction, else nothing is written
        base2, plan2, _ = carve_plan(LAPIS, slot)
        if (base2, plan2) != (base, plan):
            raise PrepError("the LP metadata of super changed while it was read (an OTA?): nothing written")
        for key, (name, exts, size) in plan.items():
            h = hashlib.sha256()
            for b in stream_partition(LAPIS, base, exts):
                h.update(b)
            if h.hexdigest() != info[key]["sha256"]:
                raise PrepError(f"{name} changed while it was read: nothing written")
            print(f"  {name}: re-read after the extraction, sha256 unchanged", flush=True)
        os.makedirs(TMP_DIR, exist_ok=True)
        folder, rec = prepare(files, slot, info, keep_tmp=a.keep_tmp)
        print(f"fingerprints: system {rec.get('fingerprints', {}).get('ro.system.build.fingerprint')}; vendor "
              f"{rec.get('fingerprints', {}).get('ro.vendor.build.fingerprint')}")
        print(f"VNNI PREPARE OK: {folder or 'nothing to deploy'} ({time.time() - t0:.0f} s)")
        return 0
    except (PrepError, OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as e:
        print(f"VNNI PREPARE FAILED: {e.__class__.__name__}: {e}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
