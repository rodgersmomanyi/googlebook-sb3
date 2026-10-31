#!/bin/bash
# k16 symbol pre-check (wave 8, kernel-k16): build the four Realtek card-reader modules OUT OF TREE against the k15
# modules_prepare output and k15's Module.symvers, to learn (before the 45-60 min Kleaf build) which symbols they import
# and which of those the trimmed k15 vmlinux does not export. Read-only for /build: the modules_prepare cache and the
# sources are copied to /var/tmp/k16/ext; dist15 is only read.
# Usage (builder): nice -n 19 ionice -c3 bash sym_precheck.sh
set -u
W=/var/tmp/k16/ext
SRC=/build/gki/common
MP=/build/gki/out/cache/15f297d1/common
D15=/build/dist15
P=/build/gki/prebuilts
export PATH=$P/clang/host/linux-x86/clang-r584948c/bin:$P/rust-toolchain/linux-x86/1.91.1.p3/bin:$P/clang-tools/linux-x86/bin:/usr/bin:/bin
rm -rf "$W"; mkdir -p "$W/src"
cp -a "$MP" "$W/mp" || exit 2
# the musl host tools (fixdep, modpost, objtool, gendwarfksyms) find libc_musl.so via ${ORIGIN}/../../../prebuilts
ln -s "$P" "$W/prebuilts"
cp "$D15/kernel_x86_64_Module.symvers" "$W/mp/Module.symvers"
for f in rtsx_usb.c rtsx_pcr.c rtsx_pcr.h rts5209.c rts5229.c rtl8411.c rts5227.c rts5249.c rts5260.c rts5260.h rts5261.c \
         rts5261.h rts5228.c rts5228.h rts5264.c rts5264.h; do
  [ -f "$SRC/drivers/misc/cardreader/$f" ] && cp "$SRC/drivers/misc/cardreader/$f" "$W/src/"
done
cp "$SRC/drivers/mmc/host/rtsx_usb_sdmmc.c" "$SRC/drivers/mmc/host/rtsx_pci_sdmmc.c" "$W/src/"
cat > "$W/src/Kbuild" <<'EOF'
obj-m := rtsx_usb.o rtsx_pci.o rtsx_usb_sdmmc.o rtsx_pci_sdmmc.o
rtsx_pci-objs := rtsx_pcr.o rts5209.o rts5229.o rtl8411.o rts5227.o rts5249.o rts5260.o rts5261.o rts5228.o rts5264.o
EOF
make -C "$SRC" O="$W/mp" M="$W/src" ARCH=x86_64 LLVM=1 HOSTCC=gcc KBUILD_MODPOST_WARN=1 -j3 modules > "$W/build.log" 2>&1
echo "make rc=$?"
grep -E 'error|undefined' "$W/build.log" | sed 's/^/LOG: /' | head -40
for m in rtsx_usb rtsx_pci rtsx_usb_sdmmc rtsx_pci_sdmmc; do
  ko=$W/src/$m.ko
  [ -f "$ko" ] || { echo "$m: NOT BUILT"; continue; }
  llvm-nm -u "$ko" | awk '{print $2}' | sort -u > "$W/$m.undef"
  echo "== $m: $(wc -l < "$W/$m.undef") undefined symbols; depends=$(/sbin/modinfo -F depends "$ko")"
  /sbin/modinfo -F alias "$ko" | sed 's/^/   alias /'
done
cat "$W"/*.undef | sort -u > "$W/all.undef"
cut -f2 "$D15/vmlinux.symvers" | sort -u > "$W/vmlinux.exports"
awk -F'\t' '$3 !~ /vmlinux/ {print $2}' "$D15/kernel_x86_64_Module.symvers" | sort -u > "$W/module.exports"
cat "$W"/rtsx_*.ko > /dev/null 2>&1
own=$(for m in rtsx_usb rtsx_pci; do llvm-nm --defined-only "$W/src/$m.ko" 2>/dev/null | awk '$2 ~ /[TDRB]/ {print $3}'; done | sort -u)
echo "== imported symbols not exported by k15 vmlinux and not provided by a k15 module or by rtsx_usb/rtsx_pci:"
comm -23 "$W/all.undef" "$W/vmlinux.exports" | comm -23 - "$W/module.exports" | comm -23 - <(printf '%s\n' "$own" | sort -u) \
  | grep -v '^__this_module$' | tee "$W/missing.txt"
echo "== imported from k15 modules:"
comm -12 "$W/all.undef" "$W/module.exports" | while read -r s; do printf '%s %s\n' "$s" "$(awk -F'\t' -v s="$s" '$2==s {print $3}' "$D15/kernel_x86_64_Module.symvers")"; done
echo "missing count: $(wc -l < "$W/missing.txt")"
