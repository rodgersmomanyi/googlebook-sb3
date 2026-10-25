#!/bin/bash
# Assemble the hybrid ISO (DESIGN.md 1.1, 2.4): tree checks + SHA256SUMS, xorriso, property reports.
# Usage: bash mk_iso.sh <workdir> <build_id>
#   <workdir> = /var/tmp/gbiso/<build_id>, holding iso/ (the complete tree except gbsb3/SHA256SUMS),
#   grub/BOOTX64.EFI and grub/efiboot.img. Output: <workdir>/out/googlebook-sb3-installer.iso (made read-only).
#   Only image FILES are read and written; sgdisk/blkid/xorriso/mtools are used read-only on the ISO file.
#   Last line: "ISO-JSON {...}".
set -euo pipefail
W=$1; BID=$2
HERE=$(cd "$(dirname "$0")" && pwd)
case "$W" in /var/tmp/gbiso/"$BID") ;; *) echo "mk_iso: refusing workdir $W"; exit 1 ;; esac
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C MTOOLS_SKIP_CHECK=1
umask 022
T=$W/iso
OUTD=$W/out
ISO=$OUTD/googlebook-sb3-installer.iso

echo "== tree checks + SHA256SUMS"
python3 -I -B "$HERE/iso_report.py" tree --tree "$T" --build-id "$BID"

echo "== xorriso"
mkdir -p "$OUTD"
[ -e "$ISO" ] && { chmod u+w "$ISO"; rm -f "$ISO"; }
set +e
xorriso -as mkisofs -iso-level 3 -r -J -joliet-long -V GBSB3INST \
  -volset "GBSB3 $BID" -A "Googlebook SB3 installer $BID - personal use only" \
  -o "$ISO" \
  -partition_offset 16 \
  -append_partition 2 C12A7328-F81F-11D2-BA4B-00A0C93EC93B "$W/grub/efiboot.img" \
  -appended_part_as_gpt \
  -iso_mbr_part_type EBD0A0A2-B9E5-4433-87C0-68B6B72699C7 \
  -c /boot/boot.cat \
  -e --interval:appended_partition_2:all:: -no-emul-boot \
  "$T" > "$W/xorriso.log" 2>&1
rc=$?
set -e
grep -v -E '% done|^xorriso : UPDATE :' "$W/xorriso.log" | tail -n 40 || true
[ $rc = 0 ] || { echo "mk_iso: xorriso failed (exit $rc)"; rm -f "$ISO"; exit 1; }
[ -s "$ISO" ] || { echo "mk_iso: xorriso produced no ISO"; exit 1; }
chmod a-w "$ISO"

echo "== ISO property checks"
python3 -I -B "$HERE/iso_report.py" iso --iso "$ISO" --tree "$T" --efiboot "$W/grub/efiboot.img" \
  --bootx64 "$W/grub/BOOTX64.EFI" --build-id "$BID"
