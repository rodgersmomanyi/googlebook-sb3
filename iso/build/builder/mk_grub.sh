#!/bin/bash
# Build the GRUB standalone EFI image and the El Torito ESP image (DESIGN.md 2.1, 2.4).
# Usage: bash mk_grub.sh <outdir> <early.cfg> <grub.cfg> <build_id>
#   <outdir> must be below /var/tmp/gbiso/<build_id>/. Writes BOOTX64.EFI, early.cfg, efiboot.img there.
#   <grub.cfg> (the rendered menu) is only syntax-checked here. Last line: "GRUB-JSON {...}".
set -euo pipefail
OUT=$1; EARLY=$2; MENU=$3; BID=$4
HERE=$(cd "$(dirname "$0")" && pwd)
case "$OUT" in /var/tmp/gbiso/"$BID"/*) ;; *) echo "mk_grub: refusing outdir $OUT"; exit 1 ;; esac
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C MTOOLS_SKIP_CHECK=1
umask 022
mkdir -p "$OUT"
cd "$OUT"
rm -f BOOTX64.EFI efiboot.img early.cfg
cp "$EARLY" early.cfg
grep -qF "set gb_marker=/gbsb3/GBSB3-$BID.marker" early.cfg || { echo "mk_grub: early.cfg lacks the marker of $BID"; exit 1; }
grub-script-check early.cfg
grub-script-check "$MENU"

grub-mkstandalone -O x86_64-efi -o BOOTX64.EFI --locales="" --fonts="" --themes="" \
  --modules="part_gpt part_msdos iso9660 fat exfat ntfs search search_fs_file configfile normal echo test sleep" \
  --install-modules="part_gpt part_msdos iso9660 fat exfat ntfs search search_fs_file search_label configfile normal linux echo test true sleep reboot halt efifwsetup serial terminfo terminal smbios all_video efi_gop efi_uga video_fb regexp ls cat minicmd" \
  "boot/grub/grub.cfg=early.cfg"

# (a) NX_COMPAT (0x0100) must be clear in BOOTX64.EFI
dc=$(python3 -I -B "$HERE/pe_tool.py" get BOOTX64.EFI)
if [ $(( dc & 0x0100 )) -ne 0 ]; then echo "mk_grub: BOOTX64.EFI advertises NX_COMPAT ($dc)"; exit 1; fi
# (b) no tpm / shim_lock / gfxterm module in the memdisk
for s in tpm.mod shim_lock.mod gfxterm.mod; do
  if grep -a -q -F "$s" BOOTX64.EFI; then echo "mk_grub: BOOTX64.EFI contains $s"; exit 1; fi
done
# the embedded early config (uncompressed memdisk) carries this build's marker path
grep -a -q -F "/gbsb3/GBSB3-$BID.marker" BOOTX64.EFI || { echo "mk_grub: marker path not found in BOOTX64.EFI"; exit 1; }
for m in search_fs_file.mod iso9660.mod exfat.mod ntfs.mod linux.mod smbios.mod serial.mod efifwsetup.mod; do
  grep -a -q -F "$m" BOOTX64.EFI || { echo "mk_grub: BOOTX64.EFI lacks $m"; exit 1; }
done
efi_size=$(stat -c %s BOOTX64.EFI)
efi_sha=$(sha256sum BOOTX64.EFI | cut -d' ' -f1)

# ESP image: 8 MiB, 16 MiB if BOOTX64.EFI > 6 MiB; MUST stay < 32 MiB (El Torito load size is 16-bit sectors)
kib=8192
[ "$efi_size" -gt $((6 * 1024 * 1024)) ] && kib=16384
mkfs.vfat -C efiboot.img $kib >/dev/null
mmd -i efiboot.img ::/EFI ::/EFI/BOOT
mcopy -i efiboot.img BOOTX64.EFI ::/EFI/BOOT/BOOTX64.EFI
mcopy -n -i efiboot.img ::/EFI/BOOT/BOOTX64.EFI - | cmp -s - BOOTX64.EFI || { echo "mk_grub: efiboot.img copy differs"; exit 1; }
fsck.vfat -n efiboot.img >/dev/null
img_size=$(stat -c %s efiboot.img)
[ "$img_size" -lt $((32 * 1024 * 1024)) ] || { echo "mk_grub: efiboot.img is not < 32 MiB"; exit 1; }
[ $((img_size % 512)) -eq 0 ] || { echo "mk_grub: efiboot.img size not a multiple of 512"; exit 1; }
echo "BOOTX64.EFI $efi_size bytes, DllCharacteristics $dc, sha256 $efi_sha; efiboot.img $img_size bytes"
echo "GRUB-JSON {\"grub_efi_size\": $efi_size, \"grub_efi_sha256\": \"$efi_sha\", \"grub_efi_dllcharacteristics\": \"$dc\", \"efiboot_img_bytes\": $img_size}"
