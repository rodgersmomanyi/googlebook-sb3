#!/bin/bash
# k16 acceptance (wave 8). Builder, after kbuild16_native.sh finished. K-01..K-14 keep k15's meanings where they apply
# (K-02/K-03 changed: k16 adds five config lines and the exports the new modules import), K-15..K-18 are new.
# Reuses k14check.py (sig/unresolved/crc) and the k14 lapis extraction (read-only); /build/dist15 and /build/dist16
# are only read. Evidence: /build/k16/accept/K-xx.txt; summary on stdout and in /build/k16/accept/summary.txt.
# Usage: bash k16_accept.sh            (env K16_LOW3_CC=<dir with traps_patched.o>, default /var/tmp/k16/low3-test/cc)
# shellcheck disable=SC2015,SC2181  # 'grep -q ... && res PASS || res FAIL': res always succeeds, FAIL runs only on a real check failure
set -u
K15=/build/dist15
K16=/build/dist16
A=/build/k16/accept
T14=/build/k14/tools
L=/build/k14/lapis
SRC=/build/gki/common
PY="nice -n 19 python3 -I"
CLANG=/build/gki/prebuilts/clang/host/linux-x86/clang-r584948c/bin
LOW3_CC=${K16_LOW3_CC:-/var/tmp/k16/low3-test/cc}
NEW="rtsx_usb rtsx_usb_sdmmc rtsx_pci rtsx_pci_sdmmc"
mkdir -p "$A"
: > "$A/summary.txt"
res() { printf '%-5s %s  %s\n' "$1" "$2" "$3" | tee -a "$A/summary.txt"; }
G() { git -C "$SRC" "$@"; }

[ -f /build/kbuild16.done ] || { echo "kbuild16 not done (/build/kbuild16.done missing)"; exit 1; }

# K-01: patch scope. 40 nouveau commits in drivers/gpu/drm/nouveau/; k15 = 0110 (traps.c + gb_vnni_core.h) + 0111
#       (i915/display/); k16 = 0112 (traps.c only) + 0113 (gki_defconfig + BUILD.bazel only)
{
  nn=$(G rev-list --count k13-native..k14-nouveau)
  nbad=$(G diff --name-only k13-native k14-nouveau | grep -vc '^drivers/gpu/drm/nouveau/' || true)
  echo "nouveau commits: $nn, files outside: $nbad"
  echo "k15-nouveau $(G rev-parse --short=12 k15-nouveau) = k14-nouveau + $(G rev-list --count k14-nouveau..k15-nouveau)"
  c112=$(G rev-list --reverse k15-nouveau..k16-nouveau | sed -n 1p)
  c113=$(G rev-list --reverse k15-nouveau..k16-nouveau | sed -n 2p)
  echo "0112 $(G log -1 --format='%h %s' "$c112")"; G show --format= --name-only "$c112" | sed 's/^/  /'
  echo "0113 $(G log -1 --format='%h %s' "$c113")"; G show --format= --name-only "$c113" | sed 's/^/  /'
  o112=$(G show --format= --name-only "$c112" | grep -v '^arch/x86/kernel/traps\.c$' || true)
  o113=$(G show --format= --name-only "$c113" | grep -Ev '^(arch/x86/configs/gki_defconfig|BUILD\.bazel)$' || true)
  nc=$(G rev-list --count k15-nouveau..k16-nouveau)
  k15id=$(G rev-parse --short=12 k15-nouveau)
  echo "k16 commits: $nc; 0112 outside: [$o112]; 0113 outside: [$o113]; k15-nouveau id $k15id"
  [ "$nn" = 40 ] && [ "$nbad" = 0 ] && [ "$nc" = 2 ] && [ -z "$o112" ] && [ -z "$o113" ] && [ "$k15id" = 3c2c2f588d3f ] && echo RESULT=ok
} > "$A/K-01.txt" 2>&1
grep -q RESULT=ok "$A/K-01.txt" && res K-01 PASS "40 nouveau + k15 (3c2c2f588d3f) + 2 k16 commits, each in its files" || res K-01 FAIL "see $A/K-01.txt"

# K-02: .config = k15's + exactly the five card-reader options
{
  diff "$K15/kernel_x86_64_dot_config" "$K16/kernel_x86_64_dot_config"
  got=$(diff "$K15/kernel_x86_64_dot_config" "$K16/kernel_x86_64_dot_config" | grep '^[<>]' | sort)
  want=$(printf '%s\n' '< # CONFIG_MISC_RTSX_PCI is not set' '< # CONFIG_MISC_RTSX_USB is not set' '> CONFIG_MISC_RTSX=m' \
         '> CONFIG_MISC_RTSX_PCI=m' '> CONFIG_MISC_RTSX_USB=m' '> CONFIG_MMC_REALTEK_PCI=m' '> CONFIG_MMC_REALTEK_USB=m' | sort)
  [ "$got" = "$want" ] && echo RESULT=ok
} > "$A/K-02.txt" 2>&1
grep -q RESULT=ok "$A/K-02.txt" && res K-02 PASS "kernel_x86_64_dot_config = k15 + MISC_RTSX(_PCI/_USB), MMC_REALTEK_PCI/_USB =m only" || res K-02 FAIL "see $A/K-02.txt"

# K-03: KMI. The KMI symbol lists are byte-identical to k15's; every k15 vmlinux export is unchanged (name, CRC,
#       namespace, export type); the added exports are exactly the vmlinux symbols the four new modules import that
#       k15 did not export, and nothing else
{
  for f in abi_symbollist abi_symbollist.raw; do
    cmp -s "$K15/$f" "$K16/$f" && echo "$f: identical to k15 ($(wc -l < "$K16/$f") lines)" || echo "$f: DIFFERS"
  done
  sort "$K15/vmlinux.symvers" > "$A/k15.symvers.sorted"; sort "$K16/vmlinux.symvers" > "$A/k16.symvers.sorted"
  gone=$(comm -23 "$A/k15.symvers.sorted" "$A/k16.symvers.sorted" | wc -l)
  comm -13 "$A/k15.symvers.sorted" "$A/k16.symvers.sorted" > "$A/K-03-added.txt"
  echo "k15 exports $(wc -l < "$K15/vmlinux.symvers"), k16 exports $(wc -l < "$K16/vmlinux.symvers"); k15 lines changed or gone: $gone; added: $(wc -l < "$A/K-03-added.txt")"
  sed 's/^/  added: /' "$A/K-03-added.txt"
  for m in $NEW; do "$CLANG/llvm-nm" -u "$K16/$m.ko" | awk '{print $2}'; done | sort -u > "$A/K-03-new-imports.txt"
  cut -f2 "$A/k15.symvers.sorted" | sort -u > "$A/k15.names"
  cut -f2 "$A/k16.symvers.sorted" | sort -u > "$A/k16.names"
  comm -12 "$A/K-03-new-imports.txt" "$A/k16.names" | comm -23 - "$A/k15.names" > "$A/K-03-expected-added.txt"
  cut -f2 "$A/K-03-added.txt" | sort -u > "$A/K-03-added.names"
  echo "imports of the new modules that k16 vmlinux exports and k15 did not: $(tr '\n' ' ' < "$A/K-03-expected-added.txt")"
  ab=$(cmp -s "$K15/abi_symbollist" "$K16/abi_symbollist" && cmp -s "$K15/abi_symbollist.raw" "$K16/abi_symbollist.raw" && echo same)
  [ "$ab" = same ] && [ "$gone" = 0 ] && cmp -s "$A/K-03-added.names" "$A/K-03-expected-added.txt" && echo RESULT=ok
} > "$A/K-03.txt" 2>&1
diff "$K15/kernel_x86_64_Module.symvers" "$K16/kernel_x86_64_Module.symvers" > "$A/K-03-modules.txt" 2>&1
grep -q RESULT=ok "$A/K-03.txt" && res K-03 PASS "KMI lists identical; 0 k15 exports changed; $(wc -l < "$A/K-03-added.txt") added = the new modules' imports: $(tr '\n' ' ' < "$A/K-03-added.names")" \
  || res K-03 FAIL "see $A/K-03.txt"
res info "-" "kernel_x86_64_Module.symvers: $(grep -c '^[<>]' "$A/K-03-modules.txt") changed lines (informational: the new exports + rtsx_usb/rtsx_pci's own)"

# K-04: unresolved for the nouveau closure, the i915 closure and the card-reader closure
if $PY "$T14/k14check.py" unresolved "$K16" nouveau drm_exec drm_gpuvm gpu-sched drm_ttm_helper \
    i915 drm_display_helper drm_buddy video i2c-algo-bit intel-gtt $NEW mfd-core > "$A/K-04.txt" 2>&1; then
  res K-04 PASS "$(tail -1 "$A/K-04.txt")"; else res K-04 FAIL "$(tail -1 "$A/K-04.txt")"; fi

# K-05: signatures of every dist16 module + the staged set (k15's + the four modalias modules)
$PY "$T14/k14check.py" sig "$K16" > "$A/K-05.txt" 2>&1; rc=$?
STAGED="drm_sysfb_helper efidrm drm_ttm_helper vmwgfx psmouse vmxnet3 ac battery test_power libarc4 pps_core ptp rfkill
bluetooth btbcm hidp rfcomm zsmalloc zram mii usbnet cdc_ether cdc_ncm r8152 ax88179_178a cdc-acm crc-itu-t
surface_aggregator surface_aggregator_registry surface_aggregator_hub surface_aggregator_cdev surface_hid_core
surface_hid hid-surface surface_battery surface_charger surface_dtx surface_gpe surface_hotplug soc_button_array wmi
wmi-bmof video i2c-algo-bit drm_buddy drm_display_helper intel-gtt i915 kfifo_buf industrialio-triggered-buffer
apds9960 thermal ipts v4l2-cci ipu-bridge intel_skl_int3472_common intel_skl_int3472_discrete ov5693 ov8865 ov7251
dw9719 intel-ipu6 intel-ipu6-isys drm_exec drm_gpuvm gpu-sched nouveau surface_platform_profile hid-sensor-hub
hid-sensor-iio-common hid-sensor-trigger hid-sensor-accel-3d hid-sensor-gyro-3d hid-sensor-als hid-sensor-rotation
hid-sensor-incl-3d hid-sensor-magn-3d $NEW"
missing=""; files=""
for m in $STAGED; do
  f=$K16/$m.ko; [ -f "$f" ] || f=$K16/$(echo "$m" | tr - _).ko
  if [ -f "$f" ]; then files="$files $f"; else missing="$missing $m"; fi
done
# shellcheck disable=SC2086
$PY "$T14/k14check.py" sig "$K16" $files > "$A/K-05-staged.txt" 2>&1; rc2=$?
echo "staged-set names not found in dist16:${missing:- none}" >> "$A/K-05-staged.txt"
nko15=$(ls "$K15"/*.ko | wc -l); nko16=$(ls "$K16"/*.ko | wc -l)
echo "dist15 .ko: $nko15, dist16 .ko: $nko16; new: $(comm -13 <(cd "$K15" && ls *.ko) <(cd "$K16" && ls *.ko) | tr '\n' ' ')" >> "$A/K-05.txt"
if [ $rc = 0 ] && [ $rc2 = 0 ] && [ -z "$missing" ] && [ "$nko16" = $((nko15 + 4)) ]; then
  res K-05 PASS "$(tail -2 "$A/K-05.txt" | head -1); staged: $(tail -2 "$A/K-05-staged.txt" | head -1 | cut -d, -f1-2); $nko15 -> $nko16 .ko"
else res K-05 FAIL "rc=$rc staged rc=$rc2 missing=[$missing] ko $nko15 -> $nko16"; fi

# K-06: lapis vendor_ramdisk + vendor_dlkm CRCs vs k16: the same result as against k15
ok6=1
if [ -d "$L" ]; then
  for set in vr/a2/lib/modules vdlkm_b276 vdlkm_a; do
    tag=$(echo "$set" | tr / _)
    (cd "$L" && $PY "$T14/k14check.py" crc "$K16" "$set") > "$A/K-06-$tag.txt" 2>&1 || ok6=0
    (cd "$L" && $PY "$T14/k14check.py" crc "$K15" "$set") > "$A/K-06-$tag.k15.txt" 2>&1
    if cmp -s "$A/K-06-$tag.txt" "$A/K-06-$tag.k15.txt"; then same=identical; else same=DIFFERENT; ok6=0; fi
    res "   " "$set" "$(tail -1 "$A/K-06-$tag.txt") [k15 result: $same]"
  done
  [ $ok6 = 1 ] && res K-06 PASS "0 vmlinux CRC mismatches, 0 unresolved, same as k15" || res K-06 FAIL "see $A/K-06-*.txt"
else res K-06 FAIL "lapis extraction $L missing (run k14 extract_lapis.sh first)"; fi

# K-07: nouveau unchanged (source diff k15..k16 empty, modinfo depends identical)
{
  G diff --stat k15-nouveau k16-nouveau -- drivers/gpu/drm/nouveau
  empty=$(G diff --name-only k15-nouveau k16-nouveau -- drivers/gpu/drm/nouveau drivers/gpu/drm/i915 | wc -l)
  dep=$(/sbin/modinfo -F depends "$K15/nouveau.ko"); dep2=$(/sbin/modinfo -F depends "$K16/nouveau.ko")
  echo "nouveau/i915 source diff files: $empty; nouveau depends k15 [$dep] k16 [$dep2]"
  [ "$empty" = 0 ] && [ "$dep" = "$dep2" ] && echo RESULT=ok
} > "$A/K-07.txt" 2>&1
grep -q RESULT=ok "$A/K-07.txt" && res K-07 PASS "nouveau + i915 source diff vs k15 empty; modinfo depends identical" || res K-07 FAIL "see $A/K-07.txt"

# K-08: kernel release
{
  kr=$(cat "$K16/kernel_release.txt"); k15r=$(cat "$K15/kernel_release.txt")
  tmp=$(mktemp /build/k16/vmlinux.XXXXXX)
  sh "$SRC/scripts/extract-vmlinux" "$K16/bzImage" > "$tmp" 2>/dev/null
  banner=$(grep -a -o -m1 'Linux version [^ ]*' "$tmp" | head -1 | cut -d' ' -f3); rm -f "$tmp"
  vm=$(/sbin/modinfo -F vermagic "$K16/rtsx_usb.ko" | cut -d' ' -f1)
  head=$(G rev-parse --short=12 k16-nouveau)
  echo "kernel_release.txt: $kr"; echo "gki-info: $(sed -n 's/^kernel_release=//p' "$K16/gki-info.txt")"
  echo "bzImage banner: $banner"; echo "rtsx_usb vermagic: $vm"; echo "k15 release: $k15r"; echo "k16-nouveau: $head"
  bz=$(cmp -s "$K15/bzImage" "$K16/bzImage" && echo same || echo differ)
  echo "bzImage vs k15: $bz"
  [ "$kr" = "$banner" ] && [ "$kr" = "$vm" ] && [ "$kr" != "$k15r" ] && [ "$(wc -l < "$K16/kernel_release.txt")" = 1 ] \
    && [ "$bz" = differ ] && [ "$kr" = "6.18.21-android17-5-g$head-ab16302716" ] && echo RESULT=ok
} > "$A/K-08.txt" 2>&1
grep -q RESULT=ok "$A/K-08.txt" && res K-08 PASS "$(head -1 "$A/K-08.txt")" || res K-08 FAIL "see $A/K-08.txt"

# K-09: k13 working tree restored (kbuild16 --restore verifies when HEAD is back)
bash /build/k16/kbuild16_native.sh --restore > "$A/K-09.txt" 2>&1
{ echo "HEAD: $(G rev-parse HEAD) ($(G symbolic-ref -q HEAD || echo detached))"
  echo "branches: $(G for-each-ref --format='%(refname:short)=%(objectname:short=12)' refs/heads/ | tr '\n' ' ')"; } >> "$A/K-09.txt"
grep -q "identical to saved state" "$A/K-09.txt" && res K-09 PASS "$(grep 'identical' "$A/K-09.txt" | head -1)" || res K-09 FAIL "see $A/K-09.txt"

# K-10: i915.ko still carries gb_ignore_user_gamma
{
  parm=$(/sbin/modinfo -F parm "$K16/i915.ko" | grep -c '^gb_ignore_user_gamma:')
  echo "gb_ignore_user_gamma parm lines: $parm"
  [ "$parm" = 1 ] && echo RESULT=ok
} > "$A/K-10.txt" 2>&1
grep -q RESULT=ok "$A/K-10.txt" && res K-10 PASS "i915 gb_ignore_user_gamma present" || res K-10 FAIL "see $A/K-10.txt"

# K-11: the counter and 0112 in vmlinux: gb_vnni/summary/reset, both format strings unchanged, string_escape_mem
#       defined (T), no debugfs from our code
{
  tmp=$(mktemp /build/k16/vmlinux.XXXXXX)
  sh "$SRC/scripts/extract-vmlinux" "$K16/bzImage" > "$tmp" 2>/dev/null
  g=$(strings "$tmp" | grep -c 'gb_vnni'); s=$(strings "$tmp" | grep -cx 'summary'); r=$(strings "$tmp" | grep -cx 'reset')
  f1=$(strings "$K16/vmlinux" | grep -c 'comm=%s rip=%lx count=%llu'); f2=$(strings "$K16/vmlinux" | grep -c 'gb_vnni: emulating VEX AVX-VNNI for comm=%s')
  se=$("$CLANG/llvm-nm" "$K16/vmlinux" | grep -c ' [Tt] string_escape_mem$')
  dc=$(G show k16-nouveau:arch/x86/kernel/traps.c | grep -c 'debugfs_create' || true)
  echo "strings gb_vnni: $g; attr summary: $s, reset: $r; format 'comm=%s rip=%lx count=%llu': $f1; kmsg format: $f2; string_escape_mem T: $se; debugfs_create in traps.c: $dc"
  rm -f "$tmp"
  [ "$g" -ge 1 ] && [ "$s" -ge 1 ] && [ "$r" -ge 1 ] && [ "$f1" -ge 1 ] && [ "$f2" -ge 1 ] && [ "$se" = 1 ] && [ "$dc" = 0 ] && echo RESULT=ok
} > "$A/K-11.txt" 2>&1
grep -q RESULT=ok "$A/K-11.txt" && res K-11 PASS "$(head -1 "$A/K-11.txt")" || res K-11 FAIL "see $A/K-11.txt"

# K-12: repo-patch equivalence: pristine (3938c8d97142) + repo 0002 + 0006 (= 0112) == k16-nouveau traps.c and
#       gb_vnni_core.h; + 0005 == the i915 files
{
  W=/build/k16/k12; rm -rf "$W"; mkdir -p "$W"
  REPO=${GB_REPO_PATCHES:-/build/k16/repo}
  G archive 3938c8d97142 arch/x86/kernel/traps.c drivers/gpu/drm/i915/display/intel_display.c \
     drivers/gpu/drm/i915/display/intel_display_params.c drivers/gpu/drm/i915/display/intel_display_params.h | tar -x -C "$W"
  ( cd "$W" && for p in "$REPO"/0002-*.patch "$REPO"/0005-*.patch "$REPO"/0006-*.patch; do patch -p1 --no-backup-if-mismatch < "$p" || exit 1; done ) > "$A/K-12-apply.txt" 2>&1
  bad=0
  for f in arch/x86/kernel/traps.c arch/x86/kernel/gb_vnni_core.h drivers/gpu/drm/i915/display/intel_display.c \
           drivers/gpu/drm/i915/display/intel_display_params.c drivers/gpu/drm/i915/display/intel_display_params.h; do
    if G show "k16-nouveau:$f" > "$W/.ref" 2>/dev/null && cmp -s "$W/$f" "$W/.ref"; then echo "OK   $f"; else echo "DIFF $f"; bad=1; fi
  done
  [ $bad = 0 ] && echo RESULT=ok
} > "$A/K-12.txt" 2>&1
grep -q RESULT=ok "$A/K-12.txt" && res K-12 PASS "repo 0002+0005+0006 applied to pristine == k16-nouveau files" || res K-12 FAIL "see $A/K-12.txt"

# K-13: build hygiene: no new objtool/modpost warnings vs the k15 build log
{
  grep -E 'objtool|WARNING: modpost' /build/kbuild16.log | sort -u > "$A/K-13-k16.txt"
  grep -E 'objtool|WARNING: modpost' /build/kbuild15.log | sort -u > "$A/K-13-k15.txt"
  newl=$(comm -13 "$A/K-13-k15.txt" "$A/K-13-k16.txt" | grep -c . || true)
  echo "k16 objtool/modpost lines: $(wc -l < "$A/K-13-k16.txt"); k15: $(wc -l < "$A/K-13-k15.txt"); new: $newl"
  comm -13 "$A/K-13-k15.txt" "$A/K-13-k16.txt" | sed 's/^/  /'
  [ "$newl" = 0 ] && echo RESULT=ok
} > "$A/K-13.txt" 2>&1
grep -q RESULT=ok "$A/K-13.txt" && res K-13 PASS "no new objtool/modpost warnings vs k15" || res K-13 FAIL "see $A/K-13.txt"

# K-14: KAT-0 host unit test of the emulator core: gb_vnni_core.h is byte-identical to k15's (0112 touches traps.c
#       only); kat0_run.sh regenerates the header with apply_vnni4.py and compares it with k16-nouveau's
{
  same=$(G diff --quiet k15-nouveau k16-nouveau -- arch/x86/kernel/gb_vnni_core.h && echo same || echo differs)
  echo "gb_vnni_core.h k15 vs k16: $same"
  mkdir -p /build/k16/k14tree
  G show k16-nouveau:arch/x86/kernel/gb_vnni_core.h > /build/k16/k14tree/gb_vnni_core.h
  bash /build/k15/tools/kat0_run.sh /build/k15/tools /build/k15/tools/apply_vnni4.py /build/k16/kat0 /build/k16/k14tree 2>&1 | grep -E '^(core header|KAT0 RESULT|K-14)'
} > "$A/K-14.txt" 2>&1
grep -q "gb_vnni_core.h k15 vs k16: same" "$A/K-14.txt" && grep -q "core header: identical" "$A/K-14.txt" && grep -q 'K-14 KAT-0: PASS' "$A/K-14.txt"   && res K-14 PASS "core header == k15's == apply_vnni4.py; $(grep 'K-14 KAT-0:' "$A/K-14.txt" | tail -1)" || res K-14 FAIL "see $A/K-14.txt"

# K-15 (new): the card-reader modules: depends, vermagic, modaliases (rtsx_usb: the three Realtek USB ids;
#       rtsx_pci: the PCI ids; the sdmmc halves: platform:<name>), intree, GPL
{
  for m in $NEW; do
    echo "== $m: depends [$(/sbin/modinfo -F depends "$K16/$m.ko")] license [$(/sbin/modinfo -F license "$K16/$m.ko")] intree [$(/sbin/modinfo -F intree "$K16/$m.ko")]"
    /sbin/modinfo -F alias "$K16/$m.ko" | sed 's/^/  alias /'
  done
  u=$(/sbin/modinfo -F alias "$K16/rtsx_usb.ko" | sort | tr '\n' ' ')
  us=$(/sbin/modinfo -F alias "$K16/rtsx_usb_sdmmc.ko"); ps=$(/sbin/modinfo -F alias "$K16/rtsx_pci_sdmmc.ko")
  np=$(/sbin/modinfo -F alias "$K16/rtsx_pci.ko" | grep -c '^pci:v000010ECd0000')
  echo "rtsx_usb aliases: $u"
  [ "$u" = "usb:v0BDAp0129d*dc*dsc*dp*ic*isc*ip*in* usb:v0BDAp0139d*dc*dsc*dp*ic*isc*ip*in* usb:v0BDAp0140d*dc*dsc*dp*ic*isc*ip*in* " ] \
    && [ "$us" = "platform:rtsx_usb_sdmmc" ] && [ "$ps" = "platform:rtsx_pci_sdmmc" ] && [ "$np" -ge 10 ] \
    && [ "$(/sbin/modinfo -F depends "$K16/rtsx_usb.ko")" = mfd-core ] && [ "$(/sbin/modinfo -F depends "$K16/rtsx_usb_sdmmc.ko")" = rtsx_usb ] \
    && [ "$(/sbin/modinfo -F depends "$K16/rtsx_pci.ko")" = mfd-core ] && [ "$(/sbin/modinfo -F depends "$K16/rtsx_pci_sdmmc.ko")" = rtsx_pci ] \
    && echo RESULT=ok
} > "$A/K-15.txt" 2>&1
grep -q RESULT=ok "$A/K-15.txt" && res K-15 PASS "modaliases: rtsx_usb 0bda:0129/0139/0140, rtsx_pci $(/sbin/modinfo -F alias "$K16/rtsx_pci.ko" | wc -l) PCI ids, platform:rtsx_{usb,pci}_sdmmc; depends mfd-core / rtsx_usb / rtsx_pci" \
  || res K-15 FAIL "see $A/K-15.txt"

# K-16 (new): natively the card-reader modules link against lapis's own mfd-core (first stage loads it; ours is
#       never staged): the CRCs their __versions record for mfd_* equal lapis's mfd-core exports
if $PY /build/k16/tools/k16check.py modcrc "$K16" "$L/vdlkm_b276/lib/modules/mfd-core.ko" "$L/vr/a2/lib/modules/mfd-core.ko" > "$A/K-16.txt" 2>&1; then
  res K-16 PASS "$(tail -1 "$A/K-16.txt")"
else res K-16 FAIL "$(tail -1 "$A/K-16.txt")"; fi

# K-17 (new): 0112 in the k16 build: the k16 Kleaf traps.o disassembles exactly like the LOW-3 compile test's
#       traps_patched.o (backlog low3 README section 6), and its only new undefined symbol vs k15's is string_escape_mem
{
  o16=$(readlink -f /build/gki/out/cache/last_build)/common/arch/x86/kernel/traps.o
  echo "k16 cache traps.o: $o16"
  "$CLANG/llvm-objdump" -d --no-show-raw-insn "$o16" | sed 1,3d > "$A/K-17-k16.dis"
  "$CLANG/llvm-objdump" -d --no-show-raw-insn "$LOW3_CC/traps_patched.o" | sed 1,3d > "$A/K-17-cc.dis"
  if cmp -s "$A/K-17-k16.dis" "$A/K-17-cc.dis"; then echo "disassembly identical ($(wc -l < "$A/K-17-k16.dis") lines)"; d=ok
  else echo "disassembly DIFFERS"; diff "$A/K-17-cc.dis" "$A/K-17-k16.dis" | head -20; d=bad; fi
  "$CLANG/llvm-nm" -u "$o16" | awk '{print $2}' | sort > "$A/K-17-u16.txt"
  [ -f "$LOW3_CC/traps_base.o" ] && "$CLANG/llvm-nm" -u "$LOW3_CC/traps_base.o" | awk '{print $2}' | sort > "$A/K-17-u15.txt"
  echo "new undefined vs the k15 baseline object: $(comm -13 "$A/K-17-u15.txt" "$A/K-17-u16.txt" 2>/dev/null | tr '\n' ' ')"
  [ "$d" = ok ] && [ "$(comm -13 "$A/K-17-u15.txt" "$A/K-17-u16.txt" | tr -d '\n')" = string_escape_mem ] && echo RESULT=ok
} > "$A/K-17.txt" 2>&1
grep -q RESULT=ok "$A/K-17.txt" && res K-17 PASS "k16 traps.o == the LOW-3 compile test's object; only new import string_escape_mem" || res K-17 FAIL "see $A/K-17.txt"

# K-18 (new): the signing key is k15's (the incremental cache kept certs/signing_key.pem): the certificate built into
#       vmlinux is byte-identical, so every k16 module verifies against the same key as k15's
if $PY /build/k16/tools/k16check.py cert "$K15" "$K16" > "$A/K-18.txt" 2>&1; then res K-18 PASS "$(tail -1 "$A/K-18.txt")"
else res K-18 FAIL "$(tail -1 "$A/K-18.txt")"; fi

grep -q FAIL "$A/summary.txt" && { echo "SOME CHECKS FAILED"; exit 1; }
echo "ALL K-01..K-18 PASS"
exit 0
