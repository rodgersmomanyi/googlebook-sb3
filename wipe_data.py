"""Factory-reset the lapis VM's /data (VM must be OFF): zero the first 16 MiB of the GPT 'userdata' partition in
lapis.bin, so fs_mgr finds no ext4 superblock and re-formats it ('formattable') on the next boot.
Needed only if the persistent /data gets corrupted. Usage: python -I wipe_data.py --yes
"""
import os, struct, subprocess, sys

IMG = os.path.join(os.environ.get("GB_HOME") or os.path.dirname(os.path.abspath(__file__)), "lapis.bin")
VMRUN = r'C:\Program Files\VMware\VMware Workstation\vmrun.exe'

if '--yes' not in sys.argv:
    sys.exit(__doc__)
if 'lapis-boot' in subprocess.run([VMRUN, 'list'], capture_output=True, text=True).stdout:
    sys.exit('VM is running -- power it off first')
with open(IMG, 'r+b') as f:
    f.seek(512)
    hdr = f.read(92)
    assert hdr[:8] == b'EFI PART', 'no GPT header'
    ent_lba, n_ent, ent_sz = struct.unpack_from('<QII', hdr, 72)
    f.seek(ent_lba * 512)
    for i in range(n_ent):
        e = f.read(ent_sz)
        first, last = struct.unpack_from('<QQ', e, 32)
        name = e[56:128].decode('utf-16-le').rstrip('\0')
        if name == 'userdata':
            f.seek(first * 512)
            f.write(b'\0' * (16 << 20))
            print('wiped userdata header: LBA %d..%d (%.1f GiB)' % (first, last, (last - first + 1) * 512 / 2**30))
            break
    else:
        sys.exit('userdata partition not found')
