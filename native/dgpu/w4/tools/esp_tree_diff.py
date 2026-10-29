# Diff the gb cpio (gb/inject.gz) and ESP files of two ESP images. argv: OLD NEW
import hashlib, importlib.util, sys
spec = importlib.util.spec_from_file_location("vi", r"C:\GB-x86\verify_image.py")
vi = importlib.util.module_from_spec(spec); spec.loader.exec_module(vi)
def load(p):
    f = vi.fat_files(open(p, "rb").read())
    t = {n: (m, d) for n, m, d in vi.cpio_entries(vi.gunzip_padded(f["gb/inject.gz"]))}
    return f, t
fo, to = load(sys.argv[1]); fn, tn = load(sys.argv[2])
h = lambda d: hashlib.sha256(d).hexdigest()[:12] if isinstance(d, bytes) else str(d)
for k in sorted(set(fo) | set(fn)):
    a, b = fo.get(k), fn.get(k)
    if a != b: print(f"ESP  {'+' if a is None else '-' if b is None else '~'} {k}  {len(a) if a else '':>10} -> {len(b) if b else ''}")
for k in sorted(set(to) | set(tn)):
    a, b = to.get(k), tn.get(k)
    if a != b:
        print(f"TREE {'+' if a is None else '-' if b is None else '~'} {k}  " + (f"{len(a[1]) if a and a[1] else ''} -> {len(b[1]) if b and b[1] else ''}"))
