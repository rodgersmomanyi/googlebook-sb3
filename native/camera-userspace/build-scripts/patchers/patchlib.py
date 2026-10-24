#!/usr/bin/env python3
# Tiny exact-match source editor used by the pN_*.py patchers (each old string must match once).
import sys


def edit(root, path, pairs):
    p = root + "/" + path
    s = open(p).read()
    for old, new in pairs:
        n = s.count(old)
        if n != 1:
            sys.exit("%s: expected 1 match, got %d for:\n%s" % (path, n, old))
        s = s.replace(old, new)
    open(p, "w").write(s)


def write(root, path, content):
    p = root + "/" + path
    open(p, "w").write(content)
