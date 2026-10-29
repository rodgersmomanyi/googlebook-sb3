# Read-only: the ext4 superblock of lapis.bin's userdata (feature bits incl. RO_COMPAT_VERITY 0x8000, free space).
import struct, time, importlib.util, sys
spec = importlib.util.spec_from_file_location("vi", r"C:\GB-x86\verify_image.py")
vi = importlib.util.module_from_spec(spec); spec.loader.exec_module(vi)
L = sys.argv[1] if len(sys.argv) > 1 else r"C:\GB-x86\lapis.bin"
with open(L, "rb") as f:
    p = vi.gpt_read(f, 1)["parts"]["userdata"]
    f.seek(p["first"] * 512 + 1024); sb = f.read(1024)
u32 = lambda o: struct.unpack_from("<I", sb, o)[0]
assert struct.unpack_from("<H", sb, 0x38)[0] == 0xEF53, "no ext4 magic"
bs = 1024 << u32(0x18)
inc = u32(0x60); roc = u32(0x64)
blocks = u32(0x04) | ((u32(0x150) << 32) if inc & 0x80 else 0)
free = u32(0x0C) | ((u32(0x158) << 32) if inc & 0x80 else 0)
print(f"userdata ext4: block {bs}, {blocks*bs/2**30:.2f} GiB, free {free*bs/2**30:.2f} GiB (as of last write)")
print(f"compat 0x{u32(0x5C):x} incompat 0x{inc:x} ro_compat 0x{roc:x}; RO_COMPAT_VERITY(0x8000) = {'SET' if roc & 0x8000 else 'clear'}")
print(f"state 0x{struct.unpack_from('<H', sb, 0x3A)[0]:x} (1=clean), mount count {struct.unpack_from('<H', sb, 0x34)[0]}, "
      f"last mount {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(u32(0x2C)))}, last write {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(u32(0x30)))}")
