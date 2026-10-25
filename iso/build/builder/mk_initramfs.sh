#!/bin/bash
# Build the installer initramfs (DESIGN.md 3.1-3.3): staging tree, BUILD smoke tests, newc cpio + zstd.
# Usage: bash mk_initramfs.sh <workdir> <installer_dir> <build_id> <android_build> [<krel>]
#   <workdir> must be /var/tmp/gbiso/<build_id>; output <workdir>/initrd.img, staging <workdir>/staging.
# Writes only below <workdir>. The smoke tests run the installer's --selftest in a chroot of the staging tree, which
# has no /dev, /proc or /sys mounted (nothing there can reach a device). Last line: "INITRD-JSON {...}".
set -euo pipefail
W=$1; INST=$2; BID=$3; ABUILD=$4; KREL=${5:-6.8.0-147-generic}
HERE=$(cd "$(dirname "$0")" && pwd)
case "$W" in /var/tmp/gbiso/[0-9]*-[0-9]*-*) ;; *) echo "mk_initramfs: refusing workdir $W"; exit 1 ;; esac
[ "$(basename "$W")" = "$BID" ] || { echo "mk_initramfs: workdir does not match the build id"; exit 1; }
ST=$W/staging
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
umask 022

rm_tree() {   # sudo rm -rf of a path that MUST be inside $W
  case "$1" in "$W"/*) sudo rm -rf --one-file-system -- "$1" ;; *) echo "mk_initramfs: refusing to delete $1"; exit 1 ;; esac
}

[ -e "$ST" ] && rm_tree "$ST"
rm -f "$W/initrd.img" "$W/initrd.img.tmp"

echo "== forbidden-token scan of the installer source (4.0.11)"
python3 -I -B "$HERE/forbidden_scan.py" "$INST" --layout installer

echo "== staging"
python3 -I -B "$HERE/stage_initramfs.py" --staging "$ST" --installer "$INST" --build-id "$BID" \
  --android-build "$ABUILD" --krel "$KREL" --psf-double "$HERE/psf_double.py" | tee "$W/stage.log"
STAGE_JSON=$(sed -n 's/^STAGE-JSON //p' "$W/stage.log")
[ -n "$STAGE_JSON" ] || { echo "mk_initramfs: staging produced no summary"; exit 1; }

echo "== structure checks"
for d in bin sbin; do
  [ -d "$ST/$d" ] && [ ! -L "$ST/$d" ] || { echo "mk_initramfs: /$d is not a real directory"; exit 1; }
done
[ -f "$ST/bin/busybox" ] && [ ! -L "$ST/bin/busybox" ] || { echo "mk_initramfs: /bin/busybox is not a regular file"; exit 1; }
[ -x "$ST/init" ] || { echo "mk_initramfs: /init missing or not executable"; exit 1; }
[ ! -e "$ST/ventoy" ] || { echo "mk_initramfs: /ventoy must not exist"; exit 1; }
for f in usr/bin/ntfsls usr/bin/ntfscat lib/modules/$KREL/modules.builtin lib/modules/$KREL/modules.builtin.modinfo \
         lib/modules/$KREL/modules.dep usr/share/consolefonts/gb-ter-32x64.psf \
         usr/share/consolefonts/Lat15-TerminusBold32x16.psf.gz usr/share/consolefonts/Lat15-Terminus16.psf.gz \
         etc/gb/build.env etc/gb-installer-release etc/gb/modules.order etc/gb/modules.list etc/gb/initramfs.list \
         opt/gb/lib/gbinstall.py opt/gb/lib/gb/run.py opt/gb/bin/gbinstall.sh opt/gb/bin/gbmirror.sh; do
  [ -e "$ST/$f" ] || { echo "mk_initramfs: missing /$f"; exit 1; }
done
denied=$(find -L "$ST/lib/modules/$KREL" -name '*.ko' -printf '%f\n' | sed 's/\.ko$//; s/-/_/g' |
         grep -E '^(spi_intel.*|mtd.*|efi_test|efi_pstore|tpm.*)$' || true)
[ -z "$denied" ] || { echo "mk_initramfs: denied modules present: $denied"; exit 1; }

echo "== smoke tests (chroot of the staging tree, nothing mounted inside)"
( cd "$ST" && find . | sort ) > "$W/staging.before"
out=$(sudo chroot "$ST" /bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C /usr/bin/python3 -I -B \
      /opt/gb/lib/gbinstall.py --selftest 2>&1) || { echo "$out"; echo "mk_initramfs: --selftest failed"; exit 1; }
echo "$out" | tail -n 5
echo "$out" | grep -qx 'GB-SELFTEST OK' || { echo "mk_initramfs: --selftest did not print GB-SELFTEST OK"; exit 1; }
# (no redirection to /dev/null inside the chroot: the staging /dev is an empty directory)
out=$(sudo chroot "$ST" /bin/sh -c 'PATH=/usr/sbin:/usr/bin:/sbin:/bin; for t in sgdisk efibootmgr zstd blkid lsblk losetup blockdev partx modprobe setfont fsck.vfat stty ntfsls ntfscat; do command -v $t || { echo "missing tool: $t"; exit 1; }; done') \
  || { echo "$out"; echo "mk_initramfs: tool check failed"; exit 1; }
# every real tool must load its shared libraries inside the tree (exit 127 / loader message = missing library)
for t in /usr/sbin/sgdisk /usr/bin/efibootmgr /usr/bin/zstd /usr/sbin/blkid /usr/bin/lsblk /usr/sbin/losetup \
         /usr/sbin/blockdev /usr/bin/partx /usr/bin/kmod /usr/bin/setfont /usr/sbin/fsck.vfat /usr/bin/stty \
         /usr/bin/ntfsls /usr/bin/ntfscat; do
  set +e
  o=$(sudo chroot "$ST" /bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C "$t" --version 2>&1 </dev/null)
  rc=$?
  set -e
  if [ $rc = 127 ] || echo "$o" | grep -q 'error while loading shared libraries'; then
    echo "$o"; echo "mk_initramfs: $t cannot run inside the initramfs"; exit 1
  fi
done
( cd "$ST" && find . | sort ) > "$W/staging.after"
new=$(comm -13 "$W/staging.before" "$W/staging.after" || true)
if [ -n "$new" ]; then
  echo "mk_initramfs: WARNING: the smoke tests created files; removed:"; echo "$new"
  echo "$new" | sort -r | while read -r p; do rm_tree "$ST/${p#./}"; done
fi
sudo find "$ST" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
left=$(sudo find "$ST" ! -user "$(id -un)" -print -quit)
[ -z "$left" ] || { echo "mk_initramfs: files not owned by the build user remain: $left"; exit 1; }

echo "== modules resolve in staging"
grep -v '^\s*#' "$ST/etc/gb/modules.list" | sed 's/#.*//' | while read -r m; do
  [ -n "$m" ] || continue
  modprobe -d "$ST" -S "$KREL" --show-depends "$m" >/dev/null || { echo "mk_initramfs: $m does not resolve"; exit 1; }
done

echo "== forbidden-token scan of the staged tree (--layout initrd)"
python3 -I -B "$HERE/forbidden_scan.py" "$ST" --layout initrd

echo "== archive"
cpio_opts="-o -H newc -R 0:0 --quiet"
cpio --help 2>/dev/null | grep -q -- '--reproducible' && cpio_opts="$cpio_opts --reproducible"
( cd "$ST" && find . | sort | cpio $cpio_opts ) | zstd -q -19 -T3 > "$W/initrd.img.tmp"
mv "$W/initrd.img.tmp" "$W/initrd.img"
zstd -t -q "$W/initrd.img"
size=$(stat -c %s "$W/initrd.img")
sha=$(sha256sum "$W/initrd.img" | cut -d' ' -f1)
raw=$(du -sb --apparent-size "$ST" | cut -f1)
nent=$(cd "$ST" && find . | wc -l)
echo "initrd.img: $size bytes (staging $raw bytes, $nent entries) sha256 $sha"
echo "INITRD-JSON {\"initrd_size\": $size, \"initrd_sha256\": \"$sha\", \"staging_bytes\": $raw, \"entries\": $nent, \"stage\": $STAGE_JSON}"
