#!/bin/sh
# Refresh modules/*.ko from the vendor_dlkm image of the lapis build that will actually boot, then check their
# modversion CRCs against our kernel. Run on the builder VM:
#   sh refresh_modules.sh /build/native/audio/vendor_dlkm_X.img [/build/dist/vmlinux.symvers]
# The copies must come from the same lapis build as the booted slot's vendor_dlkm (its SOF modules link against
# them). gb_audio.sh swaps in the vendor_dlkm copies at runtime if they do not.
set -e
IMG=$1; SYMVERS=${2:-/build/dist/vmlinux.symvers}
W=/build/native/audio; MNT=$W/vdlkm_refresh; OUT=$W/out/modules
MODS="snd-hda-core snd-hda-codec snd-hda-codec-generic snd-hda-codec-realtek-lib snd-hda-scodec-component snd-hda-codec-alc269 snd-hda-codec-hdmi snd-hda-codec-intelhdmi"
mkdir -p $MNT $OUT
sudo mount -o ro,loop "$IMG" $MNT
grep -h build.fingerprint $MNT/etc/build.prop || true
for m in $MODS; do cp $MNT/lib/modules/$m.ko $OUT/; done
python3 - "$SYMVERS" $OUT $MODS <<'EOF'
import sys, struct, subprocess, os
symvers, out, mods = sys.argv[1], sys.argv[2], sys.argv[3:]
k = {}
for l in open(symvers):
    p = l.split("\t")
    if len(p) > 1: k[p[1]] = int(p[0], 16)
have = dict(k); bad = 0
for m in mods:
    p = os.path.join(out, m + ".ko")
    subprocess.run(["objcopy", "-O", "binary", "--only-section=__versions", p, "/tmp/_gbv.bin"], check=True)
    d = open("/tmp/_gbv.bin", "rb").read()
    for i in range(0, len(d), 64):
        crc = struct.unpack("<Q", d[i:i+8])[0] & 0xffffffff
        s = d[i+8:i+64].split(b"\0")[0].decode()
        if s not in have: print(m, "MISSING", s); bad += 1
        elif have[s] is not None and have[s] != crc and s in k: print(m, "CRC MISMATCH", s); bad += 1
    nm = subprocess.run(["nm", p], capture_output=True, text=True).stdout
    for l in nm.splitlines():
        f = l.split()
        if f and f[-1].startswith("__crc_"): have[f[-1][6:]] = None
os.remove("/tmp/_gbv.bin")
print("problems:", bad)
sys.exit(1 if bad else 0)
EOF
sudo umount $MNT
ls -la $OUT
