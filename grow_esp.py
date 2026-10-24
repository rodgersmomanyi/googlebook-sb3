"""Enlarge the EFI-SYSTEM (ESP) partition in lapis.bin into the unused KERN-A
partition (ChromeOS depthcharge kernel, not used by the VMware-EFI/systemd-boot
path). Edits the primary + backup GPT and fixes CRC32s. Backs up the GPT regions first."""
import struct, binascii, os, sys

IMG = os.path.join(os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__)), "lapis.bin")
SECTOR = 512
ESP_START = 142606336            # bytes (fixed; EFI-SYSTEM start)
NEW_ESP_MB = 192
NEW_ESP_BYTES = NEW_ESP_MB * 1048576
NEW_ESP_END = ESP_START + NEW_ESP_BYTES          # exclusive
assert NEW_ESP_END % SECTOR == 0
new_esp_last_lba = NEW_ESP_END // SECTOR - 1
kern_a_new_first = NEW_ESP_END // SECTOR

f = open(IMG, "r+b")
def rd(off, n): f.seek(off); return f.read(n)

# --- primary header ---
hdr = bytearray(rd(SECTOR, SECTOR))
assert hdr[:8] == b"EFI PART", "no GPT"
hdr_size = struct.unpack("<I", hdr[12:16])[0]
backup_lba = struct.unpack("<Q", hdr[32:40])[0]
ptbl_lba = struct.unpack("<Q", hdr[72:80])[0]
nparts = struct.unpack("<I", hdr[80:84])[0]
psize = struct.unpack("<I", hdr[84:88])[0]
arr = bytearray(rd(ptbl_lba * SECTOR, nparts * psize))

# backup GPT
bhdr = bytearray(rd(backup_lba * SECTOR, SECTOR))
assert bhdr[:8] == b"EFI PART", "no backup GPT"
bptbl_lba = struct.unpack("<Q", bhdr[72:80])[0]
bhdr_size = struct.unpack("<I", bhdr[12:16])[0]
barr = bytearray(rd(bptbl_lba * SECTOR, nparts * psize))

# save GPT regions for rollback
bak = IMG + ".gptbak"
if not os.path.exists(bak):
    with open(bak, "wb") as b:
        b.write(struct.pack("<QQQQ", SECTOR, ptbl_lba*SECTOR, backup_lba*SECTOR, bptbl_lba*SECTOR))
        b.write(hdr); b.write(arr); b.write(bhdr); b.write(barr)
    print("GPT backup ->", bak)

# locate ESP + KERN-A
esp_i = kern_i = None
for i in range(nparts):
    e = arr[i*psize:(i+1)*psize]
    if e[:16] == b"\x00"*16: continue
    first, last = struct.unpack("<QQ", e[32:48])
    name = e[56:128].decode("utf-16le").split("\x00")[0]
    if first*SECTOR == ESP_START: esp_i = i
    if name == "KERN-A": kern_i = i
assert esp_i is not None and kern_i is not None, (esp_i, kern_i)
# verify KERN-A immediately follows ESP and new end fits within old KERN-A
k_first, k_last = struct.unpack("<QQ", arr[kern_i*psize+32:kern_i*psize+48])
assert kern_a_new_first <= k_last, "new ESP end overruns KERN-A end"

def apply(a):
    struct.pack_into("<Q", a, esp_i*psize+40, new_esp_last_lba)     # ESP last_lba
    struct.pack_into("<Q", a, kern_i*psize+32, kern_a_new_first)    # KERN-A first_lba

def fix_hdr(h, a, hsz):
    struct.pack_into("<I", h, 88, binascii.crc32(a) & 0xffffffff)   # PartitionEntryArrayCRC32
    struct.pack_into("<I", h, 16, 0)                                # zero header CRC
    struct.pack_into("<I", h, 16, binascii.crc32(h[:hsz]) & 0xffffffff)

apply(arr); apply(barr)
fix_hdr(hdr, arr, hdr_size); fix_hdr(bhdr, barr, bhdr_size)

f.seek(SECTOR); f.write(hdr)
f.seek(ptbl_lba*SECTOR); f.write(arr)
f.seek(backup_lba*SECTOR); f.write(bhdr)
f.seek(bptbl_lba*SECTOR); f.write(barr)
f.close()
print("ESP enlarged: start=%d new_end=%d size=%dMB (last_lba=%d); KERN-A first_lba=%d" %
      (ESP_START, NEW_ESP_END, NEW_ESP_MB, new_esp_last_lba, kern_a_new_first))
