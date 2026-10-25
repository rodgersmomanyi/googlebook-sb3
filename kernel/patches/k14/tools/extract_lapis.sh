#!/bin/bash
# Extract the lapis (Googlebook OS) foreign kernel modules used by K-06 into <out> (builder, read-only inputs):
#   <out>/vr/a<N>/lib/modules/*.ko   every newc archive of the vendor ramdisk (lz4); the modules are in a2 today
#   <out>/vdlkm_<tag>/...            vendor_dlkm erofs images (fsck.erofs --extract)
# Usage: bash extract_lapis.sh <out> <vendor_ramdisk.lz4> <tag>=<vendor_dlkm.img> [...]
#   e.g. bash extract_lapis.sh /build/k14/lapis /build/k14/lapis/vendor_ramdisk.lz4 \
#          b276=/build/native/audio/vendor_dlkm_b276.img a=/build/native/audio/vendor_dlkm_a.img
# (vendor_ramdisk.lz4 is C:\GB-x86\boot\vendor_ramdisk.lz4, copied to the builder.)
set -eu
OUT=${1:?usage}; VR=${2:?usage}; shift 2
mkdir -p "$OUT"
cpio_tmp=$(mktemp "$OUT/vr.cpio.XXXXXX")
trap 'rm -f "$cpio_tmp"' EXIT
lz4 -dc "$VR" > "$cpio_tmp"
python3 -I - "$cpio_tmp" "$OUT/vr" <<'EOF'
import os, sys
d = open(sys.argv[1], "rb").read()
out = sys.argv[2]
o = arch = kos = 0
while o < len(d):
    if d[o:o + 6] != b"070701":                    # padding between concatenated archives
        z = d.find(b"070701", o)
        if z < 0:
            break
        o = z
    h = d[o:o + 110]
    f = [int(h[6 + 8 * i:14 + 8 * i], 16) for i in range(13)]
    fsize, nsize = f[6], f[11]
    name = d[o + 110:o + 110 + nsize - 1].decode()
    p = (o + 110 + nsize + 3) & ~3
    data = d[p:p + fsize]
    o = (p + fsize + 3) & ~3
    if name == "TRAILER!!!":
        arch += 1
        continue
    if name.endswith(".ko") or "/modules." in name:
        dst = os.path.join(out, "a%d" % arch, name.lstrip("/"))
        if not os.path.realpath(dst).startswith(os.path.realpath(out) + os.sep):
            sys.exit("refusing path outside the output: %r" % name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, "wb").write(data)
        kos += name.endswith(".ko")
print("vendor ramdisk: %d archives, %d modules" % (arch, kos))
EOF
for a in "$@"; do
  tag=${a%%=*}; img=${a#*=}
  mkdir -p "$OUT/vdlkm_$tag"
  nice -n 19 fsck.erofs --extract="$OUT/vdlkm_$tag" "$img" > /dev/null
  echo "vendor_dlkm $tag: $(find "$OUT/vdlkm_$tag" -name '*.ko' | wc -l) modules," \
       "$(grep -h '^ro.vendor_dlkm.build.id=' "$OUT/vdlkm_$tag/etc/build.prop" 2>/dev/null || echo 'no build.prop')"
done
