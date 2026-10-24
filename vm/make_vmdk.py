"""Write lapis.vmdk: a VMware 'monolithicFlat' descriptor that makes the raw disk image lapis.bin the VM's NVMe disk.

Run it after creating lapis.bin and again after anything changes its size (grow_userdata.py updates the extent itself).
Usage: python -I vm/make_vmdk.py [WORKSPACE]      (default: GB_HOME, else the repository root)
"""
import os, random, sys

ws = (sys.argv[1] if len(sys.argv) > 1 else
      os.environ.get("GB_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
img = os.path.join(ws, "lapis.bin")
size = os.path.getsize(img)
assert size % 512 == 0, "lapis.bin is not a whole number of sectors"
sectors = size // 512
cid = "%08x" % random.getrandbits(32)
desc = f"""# Disk DescriptorFile
version=1
encoding="windows-1252"
CID={cid}
parentCID=ffffffff
createType="monolithicFlat"

# Extent description
RW {sectors} FLAT "lapis.bin" 0

# The Disk Data Base
#DDB

ddb.adapterType = "lsilogic"
ddb.geometry.cylinders = "{max(1, min(sectors // (255 * 63), 65535))}"
ddb.geometry.heads = "255"
ddb.geometry.sectors = "63"
ddb.virtualHWVersion = "21"
"""
with open(os.path.join(ws, "lapis.vmdk"), "w", newline="\n") as f:
    f.write(desc)
print(f"wrote {os.path.join(ws, 'lapis.vmdk')}: {sectors} sectors ({size / 2**30:.1f} GiB)")
