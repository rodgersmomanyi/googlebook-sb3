#!/bin/sh
# test_make_dgpu_payload.sh -- NP-21 (INTERFACES.md v1.1 18.1): make_dgpu_payload.sh on a real staged tree.
#
#   sh test_make_dgpu_payload.sh <work_dir> <fw_root> <module_dir> <vulkan.nouveau.so> [<compute_lib_dir>]
#     fw_root: a directory holding nvidia/tu117/... (the 30 files of firmware_staged_sha256.txt)
#     module_dir: a directory holding the 5 payload modules of one kernel build (e.g. a copy from /build/dist14)
#     compute_lib_dir: optional P6 libraries (lib64/gpu-compute/*.so)
# WHENCE.tu117 and LICENCE.nvidia come from ../firmware/ (P0-6, fetch_tu117_firmware.sh --licence).
# Checks: two builds byte-identical; fsck.erofs; the image listing (erofs_ls.py, python3 -I) equals the 6.2 list
# exactly, modes 0755/0644, owner 0:0; MANIFEST.sha256 matches the extracted image; the firmware sha256 match
# firmware_staged_sha256.txt; the .sha256 file format; the staged tree is not modified; and the refusals (extra entry,
# symlink, wrong krel, corrupted firmware, missing licence, bad modules.dgpu, unsigned module).
# Output: "ok"/"FAIL" lines, then "RESULT pass=<n> fail=<n>"; exit 0 only if fail=0. Runs at nice 19 / idle I/O.
LC_ALL=C
export LC_ALL
HERE=$(cd "$(dirname "$0")" && pwd)
[ $# -ge 4 ] || { echo "usage: test_make_dgpu_payload.sh <work_dir> <fw_root> <module_dir> <vulkan.nouveau.so> [<compute_lib_dir>]"; exit 2; }
WORK=$1
FWROOT=$2
MODDIR=$3
ICD=$4
CLIB=${5:-}
TOOL=$HERE/make_dgpu_payload.sh
FWSHA=$HERE/../firmware_staged_sha256.txt
MODS="drm_exec drm_gpuvm gpu-sched drm_ttm_helper nouveau"
NPASS=0
NFAIL=0

ok() { NPASS=$((NPASS + 1)); printf 'ok   NP-21: %s\n' "$1"; }
bad() { NFAIL=$((NFAIL + 1)); printf 'FAIL NP-21: %s\n' "$1"; }
check() { _d=$1; shift; if "$@"; then ok "$_d"; else bad "$_d"; fi; }
eq() { [ "$1" = "$2" ] || { printf '     expected [%s]\n     got      [%s]\n' "$2" "$1"; return 1; }; }

fp_tree() {
  (cd "$1" && find . | sort | while IFS= read -r f; do
    if [ -L "$f" ]; then printf 'l %s %s\n' "$f" "$(readlink "$f")"
    elif [ -d "$f" ]; then printf 'd %s %s\n' "$f" "$(stat -c %a "$f")"
    else printf 'f %s %s %s\n' "$f" "$(stat -c %a "$f")" "$(sha256sum < "$f" | cut -c1-64)"; fi
  done) | sha256sum | cut -c1-64
}

rm -rf "$WORK"
mkdir -p "$WORK"
KREL=$(modinfo -F vermagic "$MODDIR/nouveau.ko" | cut -d' ' -f1)
echo "== krel $KREL"

stage() {                                # stage <dir>: a tree per INTERFACES 6.2 (no VERSION/MANIFEST: the tool writes them)
  rm -rf "$1"
  mkdir -p "$1/firmware/nvidia" "$1/lib/modules" "$1/lib64/hw"
  while read -r _h _p; do
    _p=${_p#\*}
    mkdir -p "$1/firmware/${_p%/*}"
    cp "$FWROOT/$_p" "$1/firmware/$_p"
  done < "$FWSHA"
  cp "$HERE/../firmware/WHENCE.tu117" "$HERE/../firmware/LICENCE.nvidia" "$1/firmware/nvidia/"
  for _m in $MODS; do cp "$MODDIR/$_m.ko" "$1/lib/modules/"; done
  cp "$ICD" "$1/lib64/hw/vulkan.nouveau.so"
  if [ -n "$CLIB" ]; then
    mkdir -p "$1/lib64/gpu-compute"
    cp "$CLIB"/*.so "$1/lib64/gpu-compute/"
  fi
  chmod -R u+w "$1"
}

S=$WORK/stage
stage "$S"
FP0=$(fp_tree "$S")
sh "$TOOL" "$S" "$WORK/a.erofs" "$KREL" > "$WORK/a.log" 2>&1
_rc=$?
check "first build: exit 0 and the PAYLOAD ok line" eq "$_rc/$(grep -c '^PAYLOAD ok size=[0-9]* sha256=[0-9a-f]\{64\} files=[0-9]*$' "$WORK/a.log")" 0/1
cat "$WORK/a.log"
sh "$TOOL" "$S" "$WORK/b.erofs" "$KREL" > "$WORK/b.log" 2>&1
check "second build: exit 0" eq "$?" 0
SA=$(sha256sum < "$WORK/a.erofs" | cut -c1-64)
SB=$(sha256sum < "$WORK/b.erofs" | cut -c1-64)
check "two builds byte-identical ($SA)" eq "$SA" "$SB"
check "staged tree not modified" eq "$(fp_tree "$S")" "$FP0"
check ".sha256 file: '<sha>  dgpu-<krel>.erofs'" eq "$(cat "$WORK/a.erofs.sha256")" "$SA  dgpu-$KREL.erofs"
check "fsck.erofs" eq "$(fsck.erofs "$WORK/a.erofs" > "$WORK/fsck.txt" 2>&1; echo $?)" 0

# the listing must be exactly the 6.2 tree
python3 -I "$HERE/erofs_ls.py" "$WORK/a.erofs" > "$WORK/ls.txt"
check "erofs_ls.py reads the image" eq "$?" 0
{
  echo .
  for d in firmware firmware/nvidia firmware/nvidia/tu117 firmware/nvidia/tu117/acr firmware/nvidia/tu117/gr \
    firmware/nvidia/tu117/gsp firmware/nvidia/tu117/nvdec firmware/nvidia/tu117/sec2 lib lib/modules lib64 lib64/hw; do
    echo "$d"
  done
  [ -n "$CLIB" ] && echo lib64/gpu-compute
  echo VERSION
  echo MANIFEST.sha256
  echo firmware/nvidia/LICENCE.nvidia
  echo firmware/nvidia/WHENCE.tu117
  while read -r _h _p; do echo "firmware/${_p#\*}"; done < "$FWSHA"
  for _m in $MODS; do echo "lib/modules/$_m.ko"; done
  echo lib/modules/modules.dgpu
  echo lib64/hw/vulkan.nouveau.so
  if [ -n "$CLIB" ]; then for f in "$CLIB"/*.so; do echo "lib64/gpu-compute/${f##*/}"; done; fi
} | sort > "$WORK/want.txt"
cut -d' ' -f6 "$WORK/ls.txt" | sort > "$WORK/got.txt"
check "image entries == INTERFACES 6.2 (+ compute libraries)" eq "$(diff "$WORK/want.txt" "$WORK/got.txt" > "$WORK/ls.diff"; echo $?)" 0
[ -s "$WORK/ls.diff" ] && cat "$WORK/ls.diff"
check "directories 0755, files 0644, nothing else" eq \
  "$(awk '!(($1 == "d" && $2 == "0755") || ($1 == "f" && $2 == "0644"))' "$WORK/ls.txt" | wc -l | tr -d ' ')" 0
check "every entry owned by 0:0" eq "$(awk '$3 != 0 || $4 != 0' "$WORK/ls.txt" | wc -l | tr -d ' ')" 0
check "30 firmware files" eq "$(grep -c ' firmware/nvidia/tu117/.*\.bin$' "$WORK/ls.txt")" 30

# content: extracted image vs MANIFEST, firmware vs firmware_staged_sha256.txt, VERSION, modules.dgpu
X=$WORK/x
fsck.erofs --extract="$X" "$WORK/a.erofs" > /dev/null 2>&1
check "fsck.erofs --extract" eq "$?" 0
check "MANIFEST.sha256 matches the extracted image" eq "$(cd "$X" && sha256sum -c --quiet MANIFEST.sha256 > /dev/null 2>&1; echo $?)" 0
check "MANIFEST.sha256 lists every other regular file, sorted, no ./" eq \
  "$(cut -d' ' -f3 "$X/MANIFEST.sha256" | tr '\n' ' ')" "$(cd "$X" && find . -type f ! -name MANIFEST.sha256 | sed 's#^\./##' | sort | tr '\n' ' ')"
_fwbad=0
while read -r _h _p; do
  [ "$(sha256sum < "$X/firmware/${_p#\*}" | cut -c1-64)" = "$_h" ] || _fwbad=$((_fwbad + 1))
done < "$FWSHA"
check "firmware sha256 == firmware_staged_sha256.txt" eq "$_fwbad" 0
check "VERSION" eq "$(cat "$X/VERSION")" "$(printf 'gb-dgpu-payload 1\nkrel %s' "$KREL")"
check "modules.dgpu" eq "$(tr '\n' ' ' < "$X/lib/modules/modules.dgpu")" "drm_exec.ko drm_gpuvm.ko gpu-sched.ko drm_ttm_helper.ko nouveau.ko "
check "WHENCE.tu117 covers nvidia/tu117" eq "$(grep -c '^\(File\|Link\): nvidia/tu117/' "$X/firmware/nvidia/WHENCE.tu117" | awk '{print ($1 >= 1)}')" 1
check "LICENCE.nvidia present" eq "$(head -c 20 "$X/firmware/nvidia/LICENCE.nvidia" | wc -c | tr -d ' ')" 20
check "superblock UUID fixed" eq "$(od -An -tx1 -j $((1024 + 48)) -N 16 "$WORK/a.erofs" | tr -d ' \n')" 67622d64677075000000000000000001

# refusals: each one must fail and write no image
neg() {                                  # neg <description> <setup command...>
  _d=$1
  shift
  stage "$WORK/n"
  "$@"
  rm -f "$WORK/n.erofs" "$WORK/n.erofs.sha256"
  sh "$TOOL" "$WORK/n" "$WORK/n.erofs" "${NKREL:-$KREL}" > "$WORK/n.log" 2>&1
  _r=$?
  check "refused: $_d" eq "$_r/$(grep -c '^PAYLOAD FAIL' "$WORK/n.log")/$(test -e "$WORK/n.erofs.sha256" && echo sha)" 1/1/
  sed -n 's/^PAYLOAD FAIL /       /p' "$WORK/n.log"
}
neg "extra top-level entry" touch "$WORK/n/extra.txt"
neg "extra firmware file" touch "$WORK/n/firmware/nvidia/tu117/gsp/gsp-999.bin"
neg "symlink" ln -s ../hw/vulkan.nouveau.so "$WORK/n/lib64/link.so"
neg "corrupted firmware byte" sh -c "printf x >> '$WORK/n/firmware/nvidia/tu117/gsp/gsp-570.144.bin'"
neg "missing LICENCE.nvidia" rm -f "$WORK/n/firmware/nvidia/LICENCE.nvidia"
neg "missing nouveau.ko" rm -f "$WORK/n/lib/modules/nouveau.ko"
neg "bad modules.dgpu" sh -c "echo nouveau.ko > '$WORK/n/lib/modules/modules.dgpu'"
neg "unsigned module" sh -c "head -c -28 '$MODDIR/drm_exec.ko' > '$WORK/n/lib/modules/drm_exec.ko'"
neg "not ELF" sh -c "echo text > '$WORK/n/lib64/hw/vulkan.nouveau.so'"
neg "bad compute library name" sh -c "mkdir -p '$WORK/n/lib64/gpu-compute' && touch '$WORK/n/lib64/gpu-compute/x y.so'"
NKREL=6.18.21-android17-5-g0000000-wrong neg "krel != module vermagic" true
NKREL=

printf 'image: %s bytes, sha256 %s\n' "$(wc -c < "$WORK/a.erofs" | tr -d ' ')" "$SA"
printf 'RESULT pass=%s fail=%s\n' "$NPASS" "$NFAIL"
[ "$NFAIL" = 0 ]
