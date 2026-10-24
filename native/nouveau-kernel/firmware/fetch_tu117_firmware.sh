#!/bin/sh
# fetch_tu117_firmware.sh - verify the NVIDIA TU117 (GTX 1650 Max-Q) firmware that nouveau (Linux 6.18.21) requests
# and download ONLY missing or mismatching files from linux-firmware into a NEW staging dir. The existing staged dir
# is only read, never modified.
#
# Runs in Git Bash on Windows or on the builder (needs sh, curl, sha256sum).
#   sh fetch_tu117_firmware.sh [STAGED_DIR] [OUT_DIR]
#   defaults: STAGED_DIR=/c/GB-x86/inject/native/firmware   OUT_DIR=<this script's dir> (files land in OUT_DIR/nvidia/..)
#   env CHECK_ONLY=1 : report only, download nothing.
#
# tu117_fw_table.txt (next to this script) = "<name nouveau requests> <real linux-firmware path> <sha256>", generated
# on the builder by resolving the WHENCE "Link:" entries of linux-firmware commit
# bf992df7a95b5e19bb8f8546eb4d7c53ab2598d0 (2026-10-23): nvidia/tu117/gsp is a directory link to tu116/gsp, whose
# bootloader-* and gsp-* link to tu102/gsp; tu117 acr/*, sec2/*, nvdec/scrubber.bin and gr/{fecs,gpccs}_bl.bin link
# to tu116. Names = the 30 nvidia/tu117/* entries of "modinfo -F firmware nouveau.ko" of the compile-tested module
# (acr/tu102.c, gr/tu102.c, sec2/tu102.c, fb/tu102.c nvdec/scrubber, gsp/tu116.c NVKM_GSP_FIRMWARE_BOOTER(tu117,
# 535.113.01|570.144)); checked identical to this table.
#
# Needed at runtime (nouveau 6.18 nvkm/subdev/gsp/tu116.c tu116_gsps[] = 570.144, then 535.113.01, then no-GSP):
#   GSP-RM (default, NvGspRm=1): gsp/{booter_load,booter_unload,bootloader,gsp}-570.144.bin is enough;
#     the 535.113.01 set is only a fallback (23.8 MB that sits in RAM in the /odm tmpfs).
#   Legacy (nouveau.config=NvGspRm=0): acr/*, gr/*, sec2/*, nvdec/scrubber.bin (~0.3 MB).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
STAGED=${1:-/c/GB-x86/inject/native/firmware}
OUT=${2:-$HERE}
TABLE=$HERE/tu117_fw_table.txt
COMMIT=bf992df7a95b5e19bb8f8546eb4d7c53ab2598d0
BASE="https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/plain"
[ -f "$TABLE" ] || { echo "missing $TABLE"; exit 2; }
case "$(cd "$OUT" 2>/dev/null && pwd)" in "$(cd "$STAGED" 2>/dev/null && pwd)") echo "OUT must differ from STAGED"; exit 2;; esac

n_ok=0; n_need=0; n_got=0; n_fail=0
while read -r name src sha; do
  [ -n "$name" ] || continue
  have=""
  [ -f "$STAGED/$name" ] && have=$(sha256sum "$STAGED/$name" | cut -d' ' -f1)
  if [ "$have" = "$sha" ]; then echo "ok       $name"; n_ok=$((n_ok+1)); continue; fi
  n_need=$((n_need+1))
  if [ -n "$have" ]; then echo "MISMATCH $name (staged sha256 $have)"; else echo "MISSING  $name"; fi
  [ "${CHECK_ONLY:-0}" = 1 ] && continue
  dst="$OUT/$name"; mkdir -p "$(dirname "$dst")"
  if [ -f "$dst" ] && [ "$(sha256sum "$dst" | cut -d' ' -f1)" = "$sha" ]; then
    echo "         already in $dst"; n_got=$((n_got+1)); continue
  fi
  if curl -fsSL --retry 3 -o "$dst.part" "$BASE/$src?id=$COMMIT" && \
     [ "$(sha256sum "$dst.part" | cut -d' ' -f1)" = "$sha" ]; then
    mv "$dst.part" "$dst"; echo "         fetched $src -> $dst"; n_got=$((n_got+1))
  else
    rm -f "$dst.part"; echo "         FETCH FAILED (or sha256 mismatch) for $src"; n_fail=$((n_fail+1))
  fi
done < "$TABLE"
echo "summary: $n_ok ok in $STAGED, $n_need missing/mismatching, $n_got now in $OUT, $n_fail failed (staged dir untouched)"
[ "$n_fail" = 0 ]
