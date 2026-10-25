#!/bin/bash
# Builder-side helper subcommands for build_iso.py (DESIGN.md 6.2). Every path argument is validated to lie below
# /var/tmp/gbiso/; nothing outside it is written. Only image FILES are handled (no block devices).
#   remote.sh compress <final.zst>            stdin = one raw chunk -> zstd -q -9 -T3 --long=27 -> tmp -> mv; prints
#                                             "SIZE <n>", "ZSHA <sha256 of .zst>", "RSHA <sha256 of decompressed>"
#   remote.sh cache-status <cache_dir>        prints "CACHEJSON <one line>" (if present) and "F <name> <size> <sha256>"
#   remote.sh put <file>                      stdin -> <file> (atomic)
#   remote.sh cache-clean <cache_dir> <keep-list-file>   delete parts/*.zst not listed, and parts/*.tmp.*
#   remote.sh link-chunks <cache_dir> <build_id> <list-file>   hard-link listed chunks into the ISO tree
#   remote.sh kernel <build_id> <clear|keep>  System.map initcall check, copy vmlinuz, clear NX_COMPAT
#   remote.sh assemble <build_id>             copy BOOTX64.EFI, vmlinuz, initrd.img into the ISO tree
#   remote.sh finish <build_id> <real|test> <keep|delete>   update last/ (real), delete the work dir (delete)
#   remote.sh prune-builds <build_id>         delete other /var/tmp/gbiso/<build_id> work dirs
#   remote.sh prune-cache <cache_dir>         delete other real cache dirs (keeps test-* dirs untouched)
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
umask 022
BASE=/var/tmp/gbiso
KREL=6.8.0-147-generic
LEVEL=9
LONG=27
HERE=$(cd "$(dirname "$0")" && pwd)

die() { echo "remote: ERROR: $*" >&2; exit 1; }
idok() { [[ "$1" =~ ^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$ ]] || die "bad build id $1"; }
under() {  # under <path> <prefix>: canonical path must be strictly below prefix
  local p; p=$(realpath -m -- "$1")
  case "$p" in "$2"/?*) ;; *) die "path $1 is not below $2" ;; esac
  [[ "$p" != *..* ]] || die "path $1 contains .."
}
cachedir_ok() { under "$1" "$BASE/cache"; [[ "$(basename "$1")" =~ ^[0-9a-f]{16}-[0-9a-f]{16}$|^test-[a-z0-9-]+$ ]] || die "bad cache dir $1"; }

cmd=${1:-}; shift || true
case "$cmd" in
compress)
  final=$1; under "$final" "$BASE/cache"
  [[ "$final" == *.zst ]] || die "not a .zst path"
  mkdir -p "$(dirname "$final")"
  tmp="$final.tmp.$$"
  zstd -q -$LEVEL -T3 --long=$LONG -c > "$tmp"
  mv -f "$tmp" "$final"
  echo "SIZE $(stat -c %s "$final")"
  echo "ZSHA $(sha256sum "$final" | cut -d' ' -f1)"
  echo "RSHA $(zstd -dcq --long=$LONG "$final" | sha256sum | cut -d' ' -f1)"
  ;;
cache-status)
  d=$1; cachedir_ok "$d"
  mkdir -p "$d/parts"
  if [ -f "$d/cache.json" ]; then echo "CACHEJSON $(tr -d '\n' < "$d/cache.json")"; fi
  cd "$d/parts"
  shopt -s nullglob
  for f in *.zst; do
    echo "F $f $(stat -c %s "$f") $(sha256sum "$f" | cut -d' ' -f1)"
  done
  ;;
put)
  f=$1; under "$f" "$BASE"
  mkdir -p "$(dirname "$f")"
  cat > "$f.tmp.$$"
  mv -f "$f.tmp.$$" "$f"
  ;;
cache-clean)
  d=$1; keep=$2; cachedir_ok "$d"; under "$keep" "$BASE"
  cd "$d/parts"
  shopt -s nullglob
  for f in *.tmp.*; do rm -f -- "$f"; echo "removed stale $f"; done
  for f in *.zst; do
    grep -qxF -- "$f" "$keep" || { rm -f -- "$f"; echo "removed unreferenced $f"; }
  done
  ;;
link-chunks)
  d=$1; bid=$2; list=$3; cachedir_ok "$d"; idok "$bid"; under "$list" "$BASE"
  dst="$BASE/$bid/iso/gbsb3/parts"
  mkdir -p "$dst"
  n=0
  while read -r f; do
    [[ "$f" =~ ^[a-z0-9_-]+\.[0-9]{3}\.zst$ ]] || die "bad chunk name $f"
    ln -f "$d/parts/$f" "$dst/$f"
    n=$((n + 1))
  done < "$list"
  echo "linked $n chunks"
  ;;
kernel)
  bid=$1; mode=$2; idok "$bid"
  W="$BASE/$bid"
  for s in init_tis crb_acpi_driver_init; do
    sudo grep -q -w -- "$s" /boot/System.map-$KREL || die "initcall $s not in System.map-$KREL (renamed? the TPM blacklist would be void)"
  done
  sudo cat /boot/vmlinuz-$KREL > "$W/vmlinuz.orig"
  before=$(python3 -I -B "$HERE/pe_tool.py" get "$W/vmlinuz.orig")
  if [ "$mode" = clear ]; then
    python3 -I -B "$HERE/pe_tool.py" clear-nx "$W/vmlinuz.orig" "$W/vmlinuz" >/dev/null
  elif [ "$mode" = keep ]; then
    cp "$W/vmlinuz.orig" "$W/vmlinuz"
  else
    die "bad mode $mode"
  fi
  after=$(python3 -I -B "$HERE/pe_tool.py" get "$W/vmlinuz")
  rm -f "$W/vmlinuz.orig"
  echo "KERNEL-JSON {\"krel\": \"$KREL\", \"before\": \"$before\", \"after\": \"$after\", \"size\": $(stat -c %s "$W/vmlinuz"), \"sha256\": \"$(sha256sum "$W/vmlinuz" | cut -d' ' -f1)\"}"
  ;;
assemble)
  bid=$1; idok "$bid"
  W="$BASE/$bid"
  mkdir -p "$W/iso/EFI/BOOT" "$W/iso/boot/grub"
  cp "$W/grub/BOOTX64.EFI" "$W/iso/EFI/BOOT/BOOTX64.EFI"
  cp "$W/vmlinuz" "$W/iso/boot/vmlinuz"
  cp "$W/initrd.img" "$W/iso/boot/initrd.img"
  echo "assembled"
  ;;
finish)
  bid=$1; kind=$2; keep=$3; idok "$bid"
  W="$BASE/$bid"
  if [ "$kind" = real ]; then
    mkdir -p "$BASE/last"
    ln -f "$W/out/googlebook-sb3-installer.iso" "$BASE/last/googlebook-sb3-installer.iso.new"
    mv -f "$BASE/last/googlebook-sb3-installer.iso.new" "$BASE/last/googlebook-sb3-installer.iso"
    echo "last -> $bid"
  fi
  if [ "$keep" = delete ]; then
    sudo rm -rf --one-file-system -- "$W"
    echo "deleted $W"
  fi
  ;;
prune-builds)
  bid=$1; idok "$bid"
  shopt -s nullglob
  for d in "$BASE"/*; do
    b=$(basename "$d")
    [[ "$b" =~ ^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$ ]] || continue
    [ "$b" = "$bid" ] && continue
    sudo rm -rf --one-file-system -- "$d"
    echo "pruned stale work dir $b"
  done
  ;;
prune-cache)
  keepd=$1; cachedir_ok "$keepd"
  shopt -s nullglob
  for d in "$BASE"/cache/*; do
    b=$(basename "$d")
    [[ "$b" =~ ^[0-9a-f]{16}-[0-9a-f]{16}$ ]] || continue
    [ "$d" = "$keepd" ] && continue
    rm -rf --one-file-system -- "$d"
    echo "pruned old cache $b"
  done
  ;;
*)
  die "unknown subcommand '$cmd'"
  ;;
esac
