# Resolve linux-firmware WHENCE Link: entries (file and directory links) for requested names; print name, real path, sha256.
import sys, os, hashlib, posixpath
repo, names_file = sys.argv[1], sys.argv[2]
links = {}
for line in open(os.path.join(repo, "WHENCE"), encoding="utf-8", errors="replace"):
    if line.startswith("Link:"):
        a, b = line[5:].strip().split(" -> ")
        links[a.strip()] = b.strip()
def resolve(p, depth=0):
    assert depth < 20, p
    if os.path.isfile(os.path.join(repo, p)):
        return p
    parts = p.split("/")
    for i in range(len(parts), 0, -1):
        pre = "/".join(parts[:i])
        if pre in links:
            tgt = posixpath.normpath(posixpath.join(posixpath.dirname(pre), links[pre]))
            return resolve("/".join([tgt] + parts[i:]), depth + 1)
    return None
for n in open(names_file).read().split():
    r = resolve(n)
    if r is None:
        print(n, "NOT_IN_LINUX_FIRMWARE", "-")
    else:
        print(n, r, hashlib.sha256(open(os.path.join(repo, r), "rb").read()).hexdigest())
