#!/bin/sh
# make_dgpu_payload.sh -- builds the dGPU payload image ESP:/gb/dgpu-<krel>.erofs (INTERFACES.md v1.1 sections 6.1-6.3,
# plan Phase 5). Runs on the builder (Ubuntu: erofs-utils, kmod modinfo, coreutils). Wave 2's build_esp
# dgpu_payload() stages a tree and calls it; NP-21 (test_make_dgpu_payload.sh next to this file) tests it.
#
#   make_dgpu_payload.sh <staged_tree_dir> <out.erofs> <krel> [firmware_staged_sha256.txt]
#
# 1. Checks the staged tree against the 6.2 list: every required file present and a regular file; no symlink, device,
#    FIFO or unexpected entry anywhere (lib64/gpu-compute/*.so are the optional P6 entries); every module's vermagic
#    starts with <krel> and carries a module signature; vulkan.nouveau.so is an ELF file; the 30 firmware files are
#    exactly those of firmware_staged_sha256.txt with the same sha256 (default: ../firmware_staged_sha256.txt next to
#    this tool); WHENCE.tu117 and LICENCE.nvidia are present (invariant 16).
# 2. Works on a private copy (the staged tree is never modified): directories 0755, files 0644, writes
#    lib/modules/modules.dgpu (or checks it), VERSION and MANIFEST.sha256.
# 3. nice -n 19 ionice -c3 mkfs.erofs -zlz4hc,12 -T0 --all-root -x -1 -U 67622d64-6770-7500-0000-000000000001
#    (LZ4 only: k13/k14 have EROFS_FS_ZIP with LZMA/DEFLATE/ZSTD off; no -E options).
# 4. fsck.erofs <out>; with --extract support the extracted tree must match MANIFEST.sha256; then <out>.sha256 =
#    "<sha256>  dgpu-<krel>.erofs" (sha256sum format, two spaces).
# 5. Prints "PAYLOAD ok size=<bytes> sha256=<hex> files=<n>"; any failure prints "PAYLOAD FAIL <reason>", exit 1.
# Two runs on the same tree give byte-identical images (fixed UUID, all timestamps 0, root owner, no xattrs).
set -u
LC_ALL=C
export LC_ALL
HERE=$(cd "$(dirname "$0")" && pwd)
UUID=67622d64-6770-7500-0000-000000000001
MODS="drm_exec drm_gpuvm gpu-sched drm_ttm_helper nouveau"
W=

fail() {
  printf 'PAYLOAD FAIL %s\n' "$*"
  [ -n "$W" ] && rm -rf "$W"
  exit 1
}

if [ $# -lt 3 ] || [ $# -gt 4 ]; then
  echo "usage: make_dgpu_payload.sh <staged_tree_dir> <out.erofs> <krel> [fw_sha_list]"
  exit 2
fi
TREE=$1
OUT=$2
KREL=$3
FWSHA=${4:-$HERE/../firmware_staged_sha256.txt}
case $KREL in '' | *[!A-Za-z0-9._+-]*) fail "bad krel '$KREL'" ;; esac
if [ ! -d "$TREE" ] || [ -L "$TREE" ]; then fail "no staged tree $TREE"; fi
[ -r "$FWSHA" ] || fail "no firmware sha list $FWSHA"
for t in mkfs.erofs fsck.erofs modinfo sha256sum; do command -v "$t" > /dev/null 2>&1 || fail "$t not installed"; done
TREE=$(cd "$TREE" && pwd)
case $OUT in "$TREE"/*) fail "output inside the staged tree" ;; esac

# ---------------------------------------------------------------- 1. the 6.2 list
FW_FILES=
while read -r _h _p; do
  _p=${_p#\*}
  case $_p in nvidia/tu117/*) ;; *) fail "unexpected path in $FWSHA: $_p" ;; esac
  case $_h in *[!0-9a-f]*) fail "bad sha in $FWSHA" ;; esac
  [ ${#_h} = 64 ] || fail "bad sha in $FWSHA"
  FW_FILES="$FW_FILES firmware/$_p"
done < "$FWSHA"
_nfw=0
for _f in $FW_FILES; do _nfw=$((_nfw + 1)); done
[ $_nfw = 30 ] || fail "$FWSHA lists $_nfw firmware files, not 30"

REQ="firmware/nvidia/LICENCE.nvidia firmware/nvidia/WHENCE.tu117 lib64/hw/vulkan.nouveau.so $FW_FILES"
for _m in $MODS; do REQ="$REQ lib/modules/$_m.ko"; done
DIRS=" firmware firmware/nvidia firmware/nvidia/tu117 firmware/nvidia/tu117/acr firmware/nvidia/tu117/gr \
firmware/nvidia/tu117/gsp firmware/nvidia/tu117/nvdec firmware/nvidia/tu117/sec2 lib lib/modules lib64 lib64/hw \
lib64/gpu-compute "

is_req() { case " $REQ " in *" $1 "*) return 0 ;; esac; return 1; }

# every entry: a directory of the list, a required file, or an optional one; nothing else, nothing special
_bad=$(cd "$TREE" && find . -mindepth 1 | sed 's#^\./##' | sort | while IFS= read -r p; do
  if [ -L "$p" ]; then echo "symlink $p"; continue; fi
  if [ -d "$p" ]; then
    case "$DIRS" in *" $p "*) ;; *) echo "unexpected directory $p" ;; esac
    continue
  fi
  if [ ! -f "$p" ]; then echo "special file $p"; continue; fi
  is_req "$p" && continue
  case $p in
    lib/modules/modules.dgpu | VERSION | MANIFEST.sha256) ;;
    lib64/gpu-compute/*/*) echo "unexpected file $p" ;;
    lib64/gpu-compute/*.so | lib64/gpu-compute/*.so.[0-9]*)
      case ${p#lib64/gpu-compute/} in *[!A-Za-z0-9._+-]*) echo "bad compute library name $p" ;; esac ;;
    *) echo "unexpected file $p" ;;
  esac
done)
[ -z "$_bad" ] || fail "$(printf '%s' "$_bad" | head -n 5 | tr '\n' ';')"
for _f in $REQ; do
  if [ ! -f "$TREE/$_f" ] || [ -L "$TREE/$_f" ]; then fail "missing $_f"; fi
done

while read -r _h _p; do
  _p=${_p#\*}
  _s=$(sha256sum < "$TREE/firmware/$_p" | cut -c1-64)
  [ "$_s" = "$_h" ] || fail "firmware sha256 mismatch: firmware/$_p"
done < "$FWSHA"
for _m in $MODS; do
  _k=$TREE/lib/modules/$_m.ko
  _vm=$(modinfo -F vermagic "$_k" 2>/dev/null)
  [ "${_vm%% *}" = "$KREL" ] || fail "$_m.ko vermagic '${_vm%% *}' is not $KREL"
  [ "$(tail -c 28 "$_k" | tr -d '\n')" = "~Module signature appended~" ] || fail "$_m.ko is not signed"
done
[ "$(head -c 4 "$TREE/lib64/hw/vulkan.nouveau.so" | od -An -c | tr -d ' ')" = '177ELF' ] || fail "vulkan.nouveau.so is not ELF"
if [ -f "$TREE/lib/modules/modules.dgpu" ]; then
  # shellcheck disable=SC2086  # the fixed module list, split on purpose
  [ "$(cat "$TREE/lib/modules/modules.dgpu")" = "$(printf '%s.ko\n' $MODS)" ] || fail "modules.dgpu content"
fi

# ---------------------------------------------------------------- 2. private copy, modes, generated files
W=$(mktemp -d "${TMPDIR:-/tmp}/dgpu-payload.XXXXXX") || fail "mktemp"
trap 'rm -rf "$W"' EXIT
mkdir "$W/root"
cp -R "$TREE/." "$W/root/" || fail "copy"
rm -f "$W/root/VERSION" "$W/root/MANIFEST.sha256"
# shellcheck disable=SC2086  # the fixed module list, split on purpose
printf '%s.ko\n' $MODS > "$W/root/lib/modules/modules.dgpu"
printf 'gb-dgpu-payload 1\nkrel %s\n' "$KREL" > "$W/root/VERSION"
find "$W/root" -type d -exec chmod 0755 {} + || fail "chmod"
find "$W/root" -type f -exec chmod 0644 {} + || fail "chmod"
(cd "$W/root" && find . -type f ! -path ./MANIFEST.sha256 | sed 's#^\./##' | sort | while IFS= read -r f; do
  sha256sum "$f"
done) > "$W/MANIFEST.sha256" || fail "manifest"
mv "$W/MANIFEST.sha256" "$W/root/MANIFEST.sha256"
chmod 0644 "$W/root/MANIFEST.sha256"
NFILES=$(find "$W/root" -type f | wc -l | tr -d ' ')

# ---------------------------------------------------------------- 3. image
rm -f "$OUT" "$OUT.sha256"
nice -n 19 ionice -c3 mkfs.erofs -zlz4hc,12 -T0 --all-root -x -1 -U "$UUID" "$OUT" "$W/root" > "$W/mkfs.log" 2>&1 ||
  fail "mkfs.erofs: $(tail -n 3 "$W/mkfs.log" | tr '\n' ' ')"

# ---------------------------------------------------------------- 4. check, sha file
nice -n 19 fsck.erofs "$OUT" > "$W/fsck.log" 2>&1 || fail "fsck.erofs: $(tail -n 3 "$W/fsck.log" | tr '\n' ' ')"
if fsck.erofs --help 2>&1 | grep -q -- '--extract'; then
  nice -n 19 fsck.erofs --extract="$W/x" "$OUT" > "$W/extract.log" 2>&1 || fail "fsck.erofs --extract"
  (cd "$W/x" && sha256sum -c --quiet MANIFEST.sha256) > "$W/verify.log" 2>&1 || fail "extracted image does not match MANIFEST.sha256"
  [ "$(find "$W/x" -type f | wc -l | tr -d ' ')" = "$NFILES" ] || fail "extracted image has a different file count"
fi
SHA=$(sha256sum < "$OUT" | cut -c1-64)
printf '%s  dgpu-%s.erofs\n' "$SHA" "$KREL" > "$OUT.sha256"
SIZE=$(wc -c < "$OUT" | tr -d ' ')
printf 'PAYLOAD ok size=%s sha256=%s files=%s\n' "$SIZE" "$SHA" "$NFILES"
exit 0
