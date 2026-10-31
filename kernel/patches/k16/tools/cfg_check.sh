#!/bin/bash
# k16 config pre-check (wave 8, kernel-k16). Read-only for /build/gki/common: every Kconfig run is
# `make O=/var/tmp/k16/cfg/<case>` with the Kleaf toolchain (clang r584948c, rust 1.91.1, bindgen), so the source tree
# gets no .config and nothing in /build is written.
#   base: k15's gki_defconfig (git show k15-nouveau:arch/x86/configs/gki_defconfig) -> olddefconfig -> savedefconfig;
#         must reproduce the file byte for byte (proves this environment canonicalises like the Kleaf config step)
#   cand: the same + the k16 lines appended at the end -> savedefconfig gives the canonical placement
#   final: the defconfig from 0113 (passed as $1) -> savedefconfig must equal it byte for byte
# Also prints the resolved values of every option the SD-reader change touches.
# Usage (builder): nice -n 19 ionice -c3 bash cfg_check.sh [final_defconfig]
set -u
W=/var/tmp/k16/cfg
SRC=/build/gki/common
P=/build/gki/prebuilts
export PATH=$P/clang/host/linux-x86/clang-r584948c/bin:$P/rust-toolchain/linux-x86/1.91.1.p3/bin:$P/clang-tools/linux-x86/bin:/usr/bin:/bin
NEW='CONFIG_MISC_RTSX_PCI=m
CONFIG_MISC_RTSX_USB=m
CONFIG_MMC_REALTEK_PCI=m
CONFIG_MMC_REALTEK_USB=m'
mkdir -p "$W"
git -C "$SRC" show k15-nouveau:arch/x86/configs/gki_defconfig > "$W/k15_defconfig" || exit 2
{ cat "$W/k15_defconfig"; printf '%s\n' "$NEW"; } > "$W/cand_defconfig"
run() {  # <case> <defconfig file>
  local O=$W/$1
  rm -rf "$O"; mkdir -p "$O"; cp "$2" "$O/.config"
  make -s -C "$SRC" O="$O" ARCH=x86_64 LLVM=1 HOSTCC=gcc HOSTCXX=g++ olddefconfig > "$O/make.log" 2>&1 || { echo "make olddefconfig $1 failed"; tail "$O/make.log"; return 1; }
  make -s -C "$SRC" O="$O" ARCH=x86_64 LLVM=1 HOSTCC=gcc HOSTCXX=g++ savedefconfig >> "$O/make.log" 2>&1 || { echo "make savedefconfig $1 failed"; tail "$O/make.log"; return 1; }
  echo "$1: rustc $(grep -c '^CONFIG_RUST=y' "$O/.config") CONFIG_RUST=y line(s); $(grep '^CONFIG_CC_VERSION_TEXT' "$O/.config" | cut -c1-60)"
}
run base "$W/k15_defconfig" || exit 1
if cmp -s "$W/k15_defconfig" "$W/base/defconfig"; then echo "BASE: savedefconfig(k15 gki_defconfig) == k15 gki_defconfig (environment canonicalises like Kleaf)"
else echo "BASE: DIFFERS"; diff "$W/k15_defconfig" "$W/base/defconfig" | head -40; exit 1; fi
run cand "$W/cand_defconfig" || exit 1
echo "== canonical placement (diff k15 gki_defconfig -> savedefconfig of k15 + the k16 lines)"
diff -U2 "$W/k15_defconfig" "$W/cand/defconfig"
echo "== resolved values (base -> cand)"
for o in MISC_RTSX MISC_RTSX_PCI MISC_RTSX_USB MMC_REALTEK_PCI MMC_REALTEK_USB MFD_CORE MMC MMC_BLOCK USB_STORAGE \
         USB_STORAGE_REALTEK USB_UAS MEMSTICK MEMSTICK_REALTEK_PCI_MS MEMSTICK_REALTEK_USB_MS LEDS_CLASS TRIM_UNUSED_KSYMS; do
  a=$(grep -E "^(# )?CONFIG_$o[= ]" "$W/base/.config" | head -1); b=$(grep -E "^(# )?CONFIG_$o[= ]" "$W/cand/.config" | head -1)
  printf '%-26s %-40s -> %s\n' "$o" "${a:-(absent)}" "${b:-(absent)}"
done
echo "== full .config diff base -> cand"
diff "$W/base/.config" "$W/cand/.config" | grep '^[<>]'
if [ $# -ge 1 ]; then
  run final "$1" || exit 1
  if cmp -s "$1" "$W/final/defconfig"; then echo "FINAL: savedefconfig($1) == $1 (Kleaf's savedefconfig check will pass)"
  else echo "FINAL: DIFFERS"; diff "$1" "$W/final/defconfig"; exit 1; fi
  if cmp -s "$W/cand/.config" "$W/final/.config"; then echo "FINAL: .config == cand .config"; else echo "FINAL: .config differs from cand"; exit 1; fi
fi
echo "CFG_CHECK OK"
