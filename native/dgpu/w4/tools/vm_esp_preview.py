# Build the VM ESP that `GB_WRITE_LAPIS=1 build_esp.py` would write, into a file (lapis.bin is only read: build_esp's
# write is under its __main__ guard). argv[1] = output ESP image. Run with GB_NO_BUILDER=1.
# (W4 integration, 2026-10-26: the same min_free as build_esp's __main__, and the tree record for 2.4's V30 check)
import importlib.util, sys
spec = importlib.util.spec_from_file_location("build_esp", r"C:\GB-x86\build_esp.py")
be = importlib.util.module_from_spec(spec); spec.loader.exec_module(be)
files = be.slot_files(be.CMDLINE_VM, be.INJECT_CPIO, "Googlebook lapis x86")
img = be.make_esp(files, min_free=be.vm_min_free(files))
open(sys.argv[1], "wb").write(img)
ini = max(len(v) for k, v in files.items() if k.startswith("initrd-"))
print(f"preview ESP {len(img):,} B; slot {be.SLOT}; krel {be.kernel_release()}; largest initrd {ini:,} B")
print("VM tree:", be.vm_tree_record().decode().replace("\n", "; "))
