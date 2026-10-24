#!/usr/bin/env python3
# Grow lapis.bin and enlarge the userdata partition so /data has room (fs_mgr formats/resizes /data to the
# partition size). userdata is followed by ota_recovery_b (deleted here) then the backup GPT; we extend the
# disk by +28GB, extend userdata to the new last-usable-LBA, delete ota_recovery_b, and rewrite primary +
# backup GPT (recomputed CRC32s). Idempotent-ish: refuses if userdata already > 8GB.
import struct, zlib, os, shutil

BIN = os.path.join(os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__)), "lapis.bin")
VMDK = os.path.join(os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__)), "lapis.vmdk")
SEC = 512
ADD_BYTES = 28 * 1024 * 1024 * 1024   # +28 GB

f = open(BIN, "r+b")
old_sectors = os.path.getsize(BIN) // SEC
new_sectors = old_sectors + ADD_BYTES // SEC
print(f"old_sectors={old_sectors} new_sectors={new_sectors} (+{ADD_BYTES//1024//1024//1024}GB)")

# --- read primary GPT header (LBA1) + partition array (LBA2..) ---
f.seek(1 * SEC); hdr = bytearray(f.read(SEC))
assert hdr[0:8] == b"EFI PART", "no GPT"
part_lba   = struct.unpack("<Q", hdr[72:80])[0]
num        = struct.unpack("<I", hdr[80:84])[0]
esize      = struct.unpack("<I", hdr[84:88])[0]
first_use  = struct.unpack("<Q", hdr[40:48])[0]
old_last_use = struct.unpack("<Q", hdr[48:56])[0]
arr_sectors = (num * esize + SEC - 1) // SEC
f.seek(part_lba * SEC); arr = bytearray(f.read(num * esize))
print(f"entries={num} esize={esize} part_lba={part_lba} first_use={first_use} old_last_use={old_last_use}")

# backup the GPT regions (first 34 sectors + old backup GPT tail) before editing
bak = BIN + ".gptbak"
if not os.path.exists(bak):
    with open(bak, "wb") as b:
        f.seek(0); b.write(f.read(34 * SEC))
        f.seek((old_sectors - 33) * SEC); b.write(f.read(33 * SEC))
    print("saved GPT backup ->", bak)

# --- locate userdata + ota_recovery_b ---
def name_of(e):
    return e[56:128].decode("utf-16-le").rstrip("\x00")
ud_i = ota_i = None
for i in range(num):
    e = arr[i*esize:(i+1)*esize]
    if e[0:16] == b"\x00"*16: continue
    nm = name_of(e)
    if nm == "userdata": ud_i = i
    if nm == "ota_recovery_b": ota_i = i
assert ud_i is not None, "userdata not found"
ud = arr[ud_i*esize:(ud_i+1)*esize]
ud_first = struct.unpack("<Q", ud[32:40])[0]
ud_last  = struct.unpack("<Q", ud[40:48])[0]
print(f"userdata idx={ud_i} LBA {ud_first}-{ud_last} ({(ud_last-ud_first+1)*SEC//1024//1024}MB); ota_recovery_b idx={ota_i}")
if (ud_last - ud_first + 1) * SEC > 8*1024*1024*1024:
    print("userdata already > 8GB; aborting (already grown)."); raise SystemExit(0)

new_last_use = new_sectors - 34        # reserve 33 sectors for backup GPT
# delete ota_recovery_b (zero its entry) so userdata can extend over it
if ota_i is not None:
    arr[ota_i*esize:(ota_i+1)*esize] = b"\x00" * esize
# extend userdata last LBA
ud = bytearray(arr[ud_i*esize:(ud_i+1)*esize])
struct.pack_into("<Q", ud, 40, new_last_use)
arr[ud_i*esize:(ud_i+1)*esize] = ud
print(f"userdata new last LBA = {new_last_use} ({(new_last_use-ud_first+1)*SEC//1024//1024//1024}GB)")

arr_crc = zlib.crc32(bytes(arr)) & 0xFFFFFFFF

def build_header(my_lba, alt_lba, pe_lba):
    h = bytearray(hdr)                 # copy common fields (sig, rev, size, disk guid, first_use, num, esize)
    struct.pack_into("<Q", h, 24, my_lba)
    struct.pack_into("<Q", h, 32, alt_lba)
    struct.pack_into("<Q", h, 48, new_last_use)
    struct.pack_into("<Q", h, 72, pe_lba)
    struct.pack_into("<I", h, 88, arr_crc)
    struct.pack_into("<I", h, 16, 0)   # zero header crc
    hsize = struct.unpack("<I", h[12:16])[0]
    hcrc = zlib.crc32(bytes(h[0:hsize])) & 0xFFFFFFFF
    struct.pack_into("<I", h, 16, hcrc)
    return h

# --- extend the backing file ---
f.truncate(new_sectors * SEC)

# --- write primary: header @LBA1, array @LBA2 ---
prim = build_header(1, new_sectors - 1, part_lba)
f.seek(1 * SEC); f.write(prim)
f.seek(part_lba * SEC); f.write(bytes(arr))

# --- write backup: array @ (new_sectors-33), header @ (new_sectors-1) ---
bak_arr_lba = new_sectors - arr_sectors - 1
back = build_header(new_sectors - 1, 1, bak_arr_lba)
f.seek(bak_arr_lba * SEC); f.write(bytes(arr))
f.seek((new_sectors - 1) * SEC); f.write(back)
f.flush(); f.close()
print(f"wrote primary GPT (LBA1, arr@{part_lba}) + backup GPT (arr@{bak_arr_lba}, hdr@{new_sectors-1})")

# --- update vmdk extent size ---
txt = open(VMDK).read()
import re
txt2 = re.sub(r'RW \d+ FLAT "lapis.bin" 0', f'RW {new_sectors} FLAT "lapis.bin" 0', txt)
open(VMDK, "w").write(txt2)
print("updated lapis.vmdk extent ->", new_sectors, "sectors")
print("DONE")
