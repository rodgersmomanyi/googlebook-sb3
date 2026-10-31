"""Extract every gb_vnni counter record from the wave-3 evidence (read-only).

Collects, per evidence file:
  * totals lines   `emulated N vex128 N ... zmmhi N` (sysfs summary line 1), with the kernel timestamp when the
                   line came through kmsg (serial log) or the snapshot name (gblog boot1-tNNN);
  * LRU lines      `comm=<comm> rip=<hex> count=<N>` (summary lines 2..17, newest first);
  * kmsg buckets   `gb_vnni: emulating VEX AVX-VNNI for comm=<comm>` (printk_ratelimited, one per NEW comm/RIP bucket);
  * KAT foreign    `other processes emulated during the run (<comm>,...)`.
Serial logs carry every kmsg line twice (two consoles); identical (timestamp, text) pairs are de-duplicated.

usage: python -I extract_gbvnni.py EVIDENCE_DIR OUT_JSON
"""
import json, os, re, sys

TOT = re.compile(r"emulated (\d+) vex128 (\d+) vex256 (\d+) word (\d+) byte (\d+) irqon (\d+) efault (\d+) "
                 r"sse_init (\d+) ymm_init (\d+) zmmhi (\d+)")
# (wave 8, LOW-3 F-2) kernel build #16 prints the task name escaped (`\xHH` for control bytes, `\` and `=`): a 15-byte
# name can be up to 60 characters, so the window is 64 (a k15 name is at most 15 characters: the same matches)
LRU = re.compile(r"comm=(.{0,64}?) rip=([0-9a-f]+) count=(\d+)")
KMSG = re.compile(r"\[\s*([0-9.]+)\].*gb_vnni: emulating VEX AVX-VNNI for comm=(.*?)\s*$")
TS = re.compile(r"\[\s*([0-9]+\.[0-9]+)\]")
FOREIGN = re.compile(r"other processes emulated during the run \(([^)]*)\)")
KEYS = ("emulated", "vex128", "vex256", "word", "byte", "irqon", "efault", "sse_init", "ymm_init", "zmmhi")


def scan(path, rel):
    recs = dict(file=rel, totals=[], lru_blocks=[], kmsg=[], foreign=[])
    seen = set()
    cur = None
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    for ln in lines:
        ts = TS.search(ln)
        t = float(ts.group(1)) if ts else None
        m = KMSG.search(ln)
        if m:
            k = ("k", m.group(1), m.group(2))
            if k not in seen:
                seen.add(k); recs["kmsg"].append(dict(t=float(m.group(1)), comm=m.group(2)))
            continue
        m = FOREIGN.search(ln)
        if m:
            k = ("f", t, m.group(1))
            if k not in seen:
                seen.add(k); recs["foreign"].append(dict(t=t, comms=m.group(1).split(",")))
            continue
        m = TOT.search(ln)
        if m and "counters" not in ln:
            k = ("t", t, m.group(0))
            if k in seen:
                continue
            seen.add(k)
            tot = dict(zip(KEYS, map(int, m.groups())), t=t)
            recs["totals"].append(tot)
            cur = dict(t=t, after_totals=tot["emulated"], entries=[])
            recs["lru_blocks"].append(cur)
            continue
        m = LRU.search(ln)
        if m and cur is not None:
            k = ("l", t, m.group(0))
            if k in seen:
                continue
            seen.add(k)
            cur["entries"].append(dict(comm=m.group(1), rip=m.group(2), count=int(m.group(3))))
    recs["lru_blocks"] = [b for b in recs["lru_blocks"] if b["entries"]]
    return recs


def main():
    root, out = sys.argv[1], sys.argv[2]
    res = []
    for dp, dn, fn in os.walk(root):
        dn.sort()
        for n in sorted(fn):
            if not n.endswith((".txt", ".log")):
                continue
            p = os.path.join(dp, n)
            r = scan(p, os.path.relpath(p, root).replace("\\", "/"))
            if r["totals"] or r["kmsg"] or r["foreign"]:
                res.append(r)
    with open(out, "w") as f:
        json.dump(res, f, indent=1)
    for r in res:
        print("%-34s totals %2d  lru-blocks %2d (%3d entries)  kmsg %4d  foreign %d" % (
            r["file"], len(r["totals"]), len(r["lru_blocks"]), sum(len(b["entries"]) for b in r["lru_blocks"]),
            len(r["kmsg"]), len(r["foreign"])))


if __name__ == "__main__":
    main()
