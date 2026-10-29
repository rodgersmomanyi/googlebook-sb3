"""Map the gb_vnni LRU RIPs (runtime addresses) to scanned libraries.

A library is mapped at a page-aligned base, so RIP - base must equal a verified VEX AVX-VNNI vaddr for EVERY RIP of a
(comm, mapping) cluster. Clusters: same comm, RIPs within 1 MiB. For each cluster every (library, base) that explains all
its RIPs is reported (identical copies of one library all match; their sha256 tells whether they are the same file).

usage: python -I match_rips.py SCAN_JSON RECORDS_JSON OUT_JSON
"""
import collections, json, sys


def main():
    scan = [r for r in json.load(open(sys.argv[1])) if r.get("hits")]
    recs = json.load(open(sys.argv[2]))
    idx = collections.defaultdict(list)
    sets = {}
    for r in scan:
        s = {int(i["addr"], 16): i for i in r["insns"]}
        sets[r["path"]] = s
        for a in s:
            idx[a & 0xfff].append((r["path"], a))
    out = []
    seen = set()
    for rec in recs:
        for blk in rec["lru_blocks"]:
            clusters = []
            for e in blk["entries"]:
                rip = int(e["rip"], 16)
                for c in clusters:
                    if c["comm"] == e["comm"] and abs(c["rips"][0] - rip) < (1 << 20):
                        c["rips"].append(rip); c["counts"].append(e["count"]); break
                else:
                    clusters.append(dict(comm=e["comm"], rips=[rip], counts=[e["count"]]))
            for c in clusters:
                key = (c["comm"], tuple(c["rips"]))
                if key in seen:
                    continue
                seen.add(key)
                full, best = [], (0, None)
                r0 = c["rips"][0]
                for path, a in idx[r0 & 0xfff]:
                    base = r0 - a
                    if base & 0xfff:
                        continue
                    hit = [rp - base in sets[path] for rp in c["rips"]]
                    sc = sum(hit)
                    if sc == len(c["rips"]):
                        f = sets[path]
                        full.append(dict(lib=path, base=hex(base),
                                         funcs=sorted({f[rp - base]["func"] or "?" for rp in c["rips"]}),
                                         insns=[dict(rip=hex(rp), vaddr=hex(rp - base), text=f[rp - base]["text"])
                                                for rp in c["rips"]]))
                    elif sc > best[0]:
                        best = (sc, path)
                out.append(dict(source=rec["file"], t=blk["t"], comm=c["comm"], n=len(c["rips"]),
                                rips=[hex(x) for x in c["rips"]], counts=c["counts"], sum=sum(c["counts"]),
                                full_matches=full, best_partial=None if full else best))
    with open(sys.argv[3], "w") as f:
        json.dump(out, f, indent=1)
    for o in out:
        libs = sorted({(m["lib"].split("/fs/")[-1].split("/apex/")[-1], m["base"]) for m in o["full_matches"]})
        print("%-28s t=%-11s %-16s n=%2d sum=%7d -> %s" % (o["source"], o["t"], o["comm"], o["n"], o["sum"],
              libs if libs else "NO FULL MATCH best %s" % (o["best_partial"],)))


if __name__ == "__main__":
    main()
