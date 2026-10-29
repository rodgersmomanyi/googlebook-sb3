"""Read-only: the LP partitions of lapis.bin's super per metadata slot (name, extents). Prints only."""
import importlib.util, sys
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("vnni_prepare", r"C:\GB-x86\vnni_prepare.py")
VP = importlib.util.module_from_spec(spec); spec.loader.exec_module(VP)
with open(r"C:\GB-x86\lapis.bin", "rb") as f:
    parts = VP.gpt(f)
    base = parts["super"][0]
    md = VP.lp_metadata(f, base)
print("super base", base, "boot slot", VP.boot_slot(parts)[0])
for k, s in enumerate(md):
    print("metadata slot", k)
    for n, (attr, exts) in sorted(s.items()):
        print("  ", n, attr, exts)
