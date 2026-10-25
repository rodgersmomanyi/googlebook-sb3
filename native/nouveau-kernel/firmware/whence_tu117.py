"""WHENCE.tu117: the linux-firmware WHENCE lines that cover the TU117 firmware nouveau requests (P0-6, invariant 16).

Usage: python3 -I whence_tu117.py <WHENCE> <names.txt> <source text>
  names.txt: the requested names (nvidia/tu117/...), whitespace separated (column 1 of tu117_fw_table.txt)
Output (stdout): a header naming the source, the "Driver: nouveau" line of the stanza, every File:/RawFile:/Link: line
of that stanza on the resolution path of a requested name (the name itself, every directory or file Link: followed,
the File: that holds the data, with the Version:/Info: lines that belong to it), and the stanza's Licence: line, in
WHENCE order. Exit 1 if a name does not resolve inside the stanza."""
import posixpath
import sys


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    whence, names_file, source = sys.argv[1:4]
    with open(whence, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    start = next((i for i, l in enumerate(lines) if l.startswith("Driver: nouveau")), None)
    if start is None:
        print("no 'Driver: nouveau' stanza", file=sys.stderr)
        return 1
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("-----")), len(lines))
    stanza = list(range(start, end))
    files, links = {}, {}
    for i in stanza:
        l = lines[i]
        for key in ("File:", "RawFile:"):
            if l.startswith(key):
                files[l[len(key):].strip()] = i
        if l.startswith("Link:"):
            a, b = l[5:].split(" -> ")
            links[a.strip()] = (b.strip(), i)
    keep = set()

    def resolve(p, depth=0):
        if depth > 20:
            return None
        if p in files:
            keep.add(files[p])
            return p
        parts = p.split("/")
        for k in range(len(parts), 0, -1):
            pre = "/".join(parts[:k])
            if pre in links:
                tgt, li = links[pre]
                keep.add(li)
                new = posixpath.normpath(posixpath.join(posixpath.dirname(pre), tgt))
                return resolve("/".join([new] + parts[k:]), depth + 1)
        return None

    with open(names_file, encoding="ascii") as f:
        names = f.read().split()
    bad = [n for n in names if not n.startswith("nvidia/tu117/") or resolve(n) is None]
    if bad:
        print("unresolved: %s" % " ".join(bad), file=sys.stderr)
        return 1
    for i in sorted(keep):                         # Version:/Info: lines that follow a kept File: line
        j = i + 1
        while j < end and lines[j].split(":", 1)[0] in ("Version", "Info", "Source"):
            keep.add(j)
            j += 1
    lic = [i for i in stanza if lines[i].startswith(("Licence:", "License:"))]
    if not lic:
        print("no Licence: line in the nouveau stanza", file=sys.stderr)
        return 1
    print("# WHENCE excerpt for the NVIDIA TU117 firmware shipped by Googlebook OS (Surface Book 3 dGPU payload).")
    print("# Source: %s" % source)
    print("# The %d files under firmware/nvidia/tu117/ of the payload are the resolved copies of the names below;" % len(names))
    print("# the full licence text is LICENCE.nvidia next to this file.")
    print("")
    print(lines[start])
    print("")
    for i in sorted(keep):
        print(lines[i])
    print("")
    for i in lic:
        print(lines[i])
    return 0


if __name__ == "__main__":
    sys.exit(main())
