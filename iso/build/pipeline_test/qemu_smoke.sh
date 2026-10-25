#!/bin/bash
# PIPELINE TEST ONLY (BUILD): boot a pipeline-test ISO (built with --test-tiny-payload and the stub installer) in
# QEMU on the builder and capture the serial console. TCG only, no network, no host devices: the guest sees only
# the ISO file (read-only) or an image FILE made from it. Never used with the real installer.
# Usage: bash qemu_smoke.sh <iso> <cd|disk|fat> <outdir below /var/tmp/gbiso/> [timeout_s]
#   cd   = the ISO as a CD (El Torito)           disk = the ISO as a raw disk (dd/Rufus-DD mode)
#   fat  = Rufus ISO-mode equivalent: GPT image FILE, one Basic Data FAT32 partition holding the ISO's files
set -euo pipefail
ISO=$1; MODE=$2; OUT=$3; TMO=${4:-900}
case "$OUT" in /var/tmp/gbiso/?*) ;; *) echo "qemu_smoke: outdir must be below /var/tmp/gbiso/"; exit 1 ;; esac
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C MTOOLS_SKIP_CHECK=1
rm -rf "$OUT"; mkdir -p "$OUT"
cp /usr/share/OVMF/OVMF_VARS_4M.fd "$OUT/vars.fd"
args=(-machine q35 -m 2048 -smp 2 -nodefaults -no-user-config -nic none -display none -device VGA
      -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd
      -drive "if=pflash,format=raw,file=$OUT/vars.fd"
      -smbios "type=1,manufacturer=VMware,, Inc.,product=VMware7,,1"
      -serial "file:$OUT/serial.log" -no-reboot)
case "$MODE" in
  cd)   args+=(-drive "if=none,id=m,format=raw,readonly=on,file=$ISO" -device ide-cd,drive=m,bus=ide.0) ;;
  disk) args+=(-drive "if=none,id=m,format=raw,snapshot=on,file=$ISO" -device ide-hd,drive=m,bus=ide.0) ;;  # base read-only
  fat)
    IMG=$OUT/fat.img
    xorriso -osirrox on -indev "$ISO" -extract / "$OUT/tree" >/dev/null 2>&1
    chmod -R u+w "$OUT/tree"
    rm -f "$OUT/tree/boot/boot.cat"
    truncate -s 300M "$IMG"
    sgdisk -n 1:2048:0 -t 1:0700 "$IMG" >/dev/null
    end=$(sgdisk -i 1 "$IMG" | sed -n 's/^Last sector: \([0-9]*\).*/\1/p')
    mkfs.vfat -F 32 -n GBSB3INST --offset 2048 "$IMG" $(( (end - 2048 + 1) / 2 )) >/dev/null
    (cd "$OUT/tree" && mcopy -s -i "$IMG@@1048576" ./* ::/)
    args+=(-drive "if=none,id=m,format=raw,file=$IMG" -device ide-hd,drive=m,bus=ide.0) ;;
  *) echo "qemu_smoke: mode cd|disk|fat"; exit 1 ;;
esac
echo "qemu_smoke: $MODE boot (TCG, timeout ${TMO}s)"
set +e
timeout "$TMO" qemu-system-x86_64 "${args[@]}"
rc=$?
set -e
echo "qemu exit $rc"
tr -d '\r' < "$OUT/serial.log" | grep -a -E 'GBSTUB|Googlebook SB3 installer|was not found|TEST:' || true
grep -a -q 'GBSTUB DONE' "$OUT/serial.log" && echo "QEMU-SMOKE $MODE OK" || { echo "QEMU-SMOKE $MODE FAILED"; exit 1; }
