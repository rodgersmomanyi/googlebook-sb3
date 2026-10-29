r"""Evaluate a W5 measurement run (both instances): python -I -B analyze_w5.py RUN_DIR

Reads RUN_DIR\{off,on}\RESULT.json, the saved sampler files (NN-sampler-series.txt / NN-sampler-full.txt) and the
serial logs (serial0.log.gz), maps every comm/RIP LRU entry with vnni-perf\tools\match_rips.py against the wave-3
static scan, and writes RUN_DIR\ANALYSIS.md + RUN_DIR\analysis.json (+ records.json / rip_matches.json)."""
import glob, gzip, json, os, re, subprocess, sys

RUN = os.path.abspath(sys.argv[1])
VP = r"C:\GB-build\native\vnni-perf"
FIELDS = ("emulated", "vex128", "vex256", "word", "byte", "irqon", "efault", "sse_init", "ymm_init", "zmmhi")


def first(txt):
    m = re.search(r"emulated (\d+) vex128 (\d+) vex256 (\d+) word (\d+) byte (\d+) irqon (\d+) efault (\d+) "
                  r"sse_init (\d+) ymm_init (\d+) zmmhi (\d+)", txt)
    return dict(zip(FIELDS, map(int, m.groups()))) if m else None


def body(path):
    t = open(path, encoding="utf-8").read()
    return t.split("\n", 2)[2] if t.startswith("$ ") else t


def load(inst):
    d = os.path.join(RUN, inst)
    R = json.load(open(os.path.join(d, "RESULT.json"), encoding="utf-8"))
    series, full = [], {}
    for p in glob.glob(os.path.join(d, "*-sampler-series.txt")):
        for l in body(p).splitlines():
            m = re.match(r"(\S+) t=([\d.]+) (emulated .*)", l)
            if m:
                series.append((m.group(1), float(m.group(2)), first(m.group(3))))
    for p in glob.glob(os.path.join(d, "*-sampler-full.txt")):
        cur = None
        for l in body(p).splitlines():
            m = re.match(r"== (\S+) t=([\d.]+)", l)
            if m:
                cur = {"label": m.group(1), "t": float(m.group(2)), "totals": None, "lru": []}
                full[(m.group(1), m.group(2))] = cur
                continue
            if cur is None:
                continue
            if l.startswith("emulated "):
                cur["totals"] = first(l)
            m = re.match(r"comm=(.*) rip=([0-9a-f]+) count=(\d+)", l)
            if m:
                cur["lru"].append({"comm": m.group(1), "rip": m.group(2), "count": int(m.group(3))})
    # also the snapshots taken by run_w5 (summary-<label>.txt) have LRU blocks
    for p in sorted(glob.glob(os.path.join(d, "*-summary-*.txt"))):
        lab = re.sub(r"^\d+-summary-|\.txt$", "", os.path.basename(p))
        txt = body(p)
        up = re.search(r"uptime=([\d.]+)", txt)
        lru = [{"comm": a, "rip": b, "count": int(c)} for a, b, c in re.findall(r"comm=(.*) rip=([0-9a-f]+) count=(\d+)", txt)]
        full[("run:" + lab, up.group(1) if up else "?")] = {"label": "run:" + lab, "t": float(up.group(1)) if up else None,
                                                            "totals": first(txt), "lru": lru}
    serial = []
    sp = os.path.join(d, "serial0.log.gz")
    if os.path.exists(sp):
        seen = set()
        for l in gzip.open(sp).read().decode("utf-8", "replace").replace("\r", "").split("\n"):
            l2 = re.sub(r"^<\d+>", "", l)
            if l2 not in seen:
                seen.add(l2); serial.append(l2)
    return R, series, full, serial


def kt(l):
    m = re.match(r"\[\s*([\d.]+)\]", l)
    return float(m.group(1)) if m else None


def at(series, label):
    for lab, t, s in series:
        if lab == label:
            return t, s
    return None, None


def main():
    data = {i: load(i) for i in ("off", "on")}
    out = {}
    # ---- records for match_rips
    recs = []
    for inst, (R, series, full, serial) in data.items():
        for (lab, t), blk in sorted(full.items(), key=lambda x: (x[1]["t"] or 0)):
            if blk["lru"]:
                recs.append({"file": f"w5/{inst}/{lab}@{t}", "totals": [], "kmsg": [], "foreign": [],
                             "lru_blocks": [{"t": blk["t"], "entries": blk["lru"]}]})
    rj = os.path.join(RUN, "records.json")
    json.dump(recs, open(rj, "w", encoding="utf-8"), indent=1)
    mj = os.path.join(RUN, "rip_matches.json")
    subprocess.run([sys.executable, "-I", os.path.join(VP, "tools", "match_rips.py"), os.path.join(VP, "evidence", "scan.json"),
                    rj, mj], check=True, capture_output=True)
    matches = json.load(open(mj, encoding="utf-8"))
    md = []
    # ---- fixed-time table
    rows = []
    for lab in ("t60", "t200", "t420", "boot_completed", "t600", "t900", "t1200"):
        r = [lab]
        for inst in ("off", "on"):
            t, s = at(data[inst][1], lab)
            r += [t, s["emulated"] if s else None]
        rows.append(r)
    out["fixed"] = rows
    md.append("| point | off: uptime s | off: emulated | on: uptime s | on: emulated | on / off |")
    md.append("|---|---:|---:|---:|---:|---:|")
    for lab, t1, e1, t2, e2 in rows:
        ratio = f"{e2 / e1:.2e}" if e1 and e2 is not None else "-"
        md.append(f"| {lab} | {t1 if t1 is not None else '-'} | {e1:,} | {t2 if t2 is not None else '-'} | {e2:,} | {ratio} |"
                  if e1 is not None and e2 is not None else f"| {lab} | {t1} | {e1} | {t2} | {e2} | - |")
    # ---- per-instance key numbers
    for inst, (R, series, full, serial) in data.items():
        dex = [(kt(l), l.split("] ", 2)[-1].strip()) for l in serial if "GB_DEXOPT" in l and kt(l) is not None]
        starts = [(k, t.split()[-1]) for k, t in dex if "GB_DEXOPT start" in t]
        done = [k for k, t in dex if "GB_DEXOPT done" in t]
        per = []
        for i, (k, p) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else (done[0] if done else None)
            per.append((p, round(end - k, 1) if end else None))
        bt, bs = at(series, "boot_completed")
        snaps = R.get("snaps", {})

        def d(a, b, f="emulated"):
            return snaps[b][f] - snaps[a][f] if a in snaps and b in snaps and f in snaps[a] and f in snaps[b] else None
        out[inst] = {"boot_completed_k": R.get("boot_completed_k"), "gb_console_k": R.get("gb_console_started_k"),
                     "esp": R.get("esp_sha256"), "gb_dexopt_per_pkg": per,
                     "gb_dexopt_wall": round(done[0] - starts[0][0], 1) if starts and done else None,
                     "compile": R.get("compile"), "compile_emulated": d("compile-before", "compile-after"),
                     "compile_wall_sum": round(sum(v["wall"] for v in (R.get("compile") or {}).values() if v.get("wall")), 2),
                     "kat": R.get("kat"), "kat_exit": R.get("kat_exit"), "kat_emulated": d("kat-before", "kat-after"),
                     "install": R.get("install"), "install_emulated": d("install-before", "install-after"),
                     "sha256": R.get("sha256"), "avc": [R.get("avc_denied"), R.get("avc_enforced"), R.get("avc_vnni")],
                     "logcat": R.get("logcat"), "final": snaps.get("final"), "series_n": len(series)}
    # ---- LRU attribution (per instance: sum of counts per library/function over all snapshots, de-duplicated by
    # cluster as match_rips reports them)
    attrib = {"off": {}, "on": {}}
    for m in matches:
        inst = m["source"].split("/")[1]
        libs = sorted(set(f["lib"] for f in m.get("full_matches", []))) or ["(no full match)"]
        funcs = sorted(set(fn for f in m.get("full_matches", []) for fn in f.get("funcs", [])))[:2]
        key = (m["comm"], " | ".join(libs)[:120], " ".join(funcs)[:80])
        a = attrib[inst].setdefault(key, {"clusters": 0, "lru_count_sum": 0, "first_seen": m["source"]})
        a["clusters"] += 1; a["lru_count_sum"] += m["sum"]
    out["lru_attribution"] = {i: [{"comm": k[0], "lib": k[1], "func": k[2], **v} for k, v in a.items()] for i, a in attrib.items()}
    json.dump(out, open(os.path.join(RUN, "analysis.json"), "w", encoding="utf-8"), indent=1)
    md.append("")
    for inst in ("off", "on"):
        md.append(f"LRU clusters {inst}:")
        for e in out["lru_attribution"][inst]:
            md.append(f"- {inst}: comm={e['comm']} lib={e['lib']} func={e['func']} clusters={e['clusters']} "
                      f"lru_sum={e['lru_count_sum']} first={e['first_seen']}")
    open(os.path.join(RUN, "ANALYSIS.md"), "w", encoding="utf-8", newline="\n").write("\n".join(md) + "\n")
    print("\n".join(md))
    print(json.dumps({k: v for k, v in out.items() if k in ("off", "on")}, indent=1)[:6000])


if __name__ == "__main__":
    main()
