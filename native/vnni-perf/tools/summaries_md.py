"""Markdown tables of every distinct gb_vnni summary snapshot (totals + LRU) with each RIP mapped to library/function.

usage: python -I summaries_md.py EVIDENCE_DIR > summaries.md
"""
import json, os, re, sys

ev = sys.argv[1]
recs = json.load(open(os.path.join(ev, "gbvnni_records.json")))
rips = json.load(open(os.path.join(ev, "rip_matches.json")))
scan = {r["path"]: {i["addr"]: i for i in r["insns"]} for r in json.load(open(os.path.join(ev, "scan.json"))) if r.get("hits")}
rmap = {}
for m in rips:
    for k, rp in enumerate(m["rips"]):
        if m["comm"] == "vnni_kat":
            rmap[(m["comm"], rp)] = ("vnni_kat (KAT test binary, not part of the image)", "-", "-", "-")
        elif m["full_matches"]:
            f = m["full_matches"][0]
            libs = sorted({re.sub(r"^.*?/(system|vendor)/lib64/", r"/\1/lib64/", x["lib"].replace("fs/system_b/system/", "fs/system/").replace("fs/vendor_b/", "fs/vendor/"))
                           .replace("apex/system_b__com.google.android.", "apex:com.google.android.") for x in m["full_matches"]})
            ins = f["insns"][k]
            fn = scan[f["lib"]][ins["vaddr"]]["func"] or "?"
            rmap[(m["comm"], rp)] = (" or ".join(libs), ins["vaddr"], fn, ins["text"])


def demangle_short(s):
    return s if len(s) < 60 else s[:57] + "..."


seen = set()
with_lru = {t["emulated"] for r in recs for t in r["totals"] for b in r["lru_blocks"] if b["after_totals"] == t["emulated"]}
out = []
for r in recs:
    for t in r["totals"]:
        blk = next((b for b in r["lru_blocks"] if b["t"] == t["t"] and b["after_totals"] == t["emulated"]), None)
        key = (t["emulated"], t["vex256"], tuple((e["comm"], e["rip"]) for e in (blk["entries"] if blk else [])))
        if not blk and t["emulated"] in with_lru:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append("#### `%s`%s\n" % (r["file"], " at %.1f s (kmsg time)" % t["t"] if t["t"] else " (snapshot)"))
        out.append("`emulated %d vex128 %d vex256 %d word %d byte %d irqon %d efault %d sse_init %d ymm_init %d zmmhi %d`\n"
                   % tuple(t[k] for k in ("emulated", "vex128", "vex256", "word", "byte", "irqon", "efault", "sse_init",
                                          "ymm_init", "zmmhi")))
        if blk:
            out.append("| comm | rip | count | library (layout match) | vaddr | function | instruction |")
            out.append("|---|---|---:|---|---|---|---|")
            for e in blk["entries"]:
                lib, va, fn, txt = rmap.get((e["comm"], "0x" + e["rip"]), ("unmatched", "-", "-", "-"))
                out.append("| %s | `%s` | %d | %s | `%s` | `%s` | `%s` |" % (e["comm"], e["rip"], e["count"], lib, va,
                                                                           demangle_short(fn), txt))
            out.append("")
        else:
            out.append("(no LRU lines in this file)\n")
print("\n".join(out))
