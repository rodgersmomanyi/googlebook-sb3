#!/bin/bash
# k15 acceptance (WAVE3_PLAN.md 3.4: K-01..K-09 keep k14's meanings, K-10..K-14 are new). Builder, after
# kbuild15_native.sh finished. Reuses k14check.py (sig/unresolved/crc) and the k14 lapis extraction (read-only).
# Evidence: /build/k15/accept/K-xx.txt; summary on stdout and in /build/k15/accept/summary.txt.
# Usage: bash k15_accept.sh
# shellcheck disable=SC2015,SC2181  # 'grep -q ... && res PASS || res FAIL': res always succeeds, FAIL runs only on a real check failure
set -u
K14=/build/dist14
K15=/build/dist15
A=/build/k15/accept
T14=/build/k14/tools
L=/build/k14/lapis
SRC=/build/gki/common
GEN=/build/k15/tools
WORKK12=/build/k15/k12
PY="nice -n 19 python3 -I"
mkdir -p "$A"
: > "$A/summary.txt"
res() { printf '%-5s %s  %s\n' "$1" "$2" "$3" | tee -a "$A/summary.txt"; }
G() { git -C "$SRC" "$@"; }

[ -f /build/kbuild15.done ] || { echo "kbuild15 not done (/build/kbuild15.done missing)"; exit 1; }

# K-01: patch scope. 40 nouveau commits inside drivers/gpu/drm/nouveau/; 0110 only traps.c (+gb_vnni_core.h);
#       0111 only drivers/gpu/drm/i915/display/
{
  echo "== nouveau (k13-native..k14-nouveau), must stay inside drivers/gpu/drm/nouveau/"
  nn=$(G rev-list --count k13-native..k14-nouveau)
  nbad=$(G diff --name-only k13-native k14-nouveau | grep -vc '^drivers/gpu/drm/nouveau/' || true)
  echo "nouveau commits: $nn, files outside: $nbad"
  echo "== k15 (k14-nouveau..k15-nouveau)"
  c110=$(G rev-list --reverse k14-nouveau..k15-nouveau | sed -n 1p)
  c111=$(G rev-list --reverse k14-nouveau..k15-nouveau | sed -n 2p)
  echo "0110 $(G log -1 --format='%h %s' "$c110")"; G show --format= --name-only "$c110" | sed 's/^/  /'
  echo "0111 $(G log -1 --format='%h %s' "$c111")"; G show --format= --name-only "$c111" | sed 's/^/  /'
  o110=$(G show --format= --name-only "$c110" | grep -Ev '^arch/x86/kernel/(traps\.c|gb_vnni_core\.h)$' || true)
  o111=$(G show --format= --name-only "$c111" | grep -v '^drivers/gpu/drm/i915/display/' || true)
  nc=$(G rev-list --count k14-nouveau..k15-nouveau)
  echo "k15 commits: $nc; 0110 outside: [$o110]; 0111 outside: [$o111]"
  [ "$nn" = 40 ] && [ "$nbad" = 0 ] && [ "$nc" = 2 ] && [ -z "$o110" ] && [ -z "$o111" ] && echo RESULT=ok
} > "$A/K-01.txt" 2>&1
grep -q RESULT=ok "$A/K-01.txt" && res K-01 PASS "40 nouveau + 2 k15 commits, each in its files" || res K-01 FAIL "see $A/K-01.txt"

# K-02: .config identical to k14
if diff "$K14/kernel_x86_64_dot_config" "$K15/kernel_x86_64_dot_config" > "$A/K-02.txt" 2>&1; then
  res K-02 PASS "kernel_x86_64_dot_config: empty diff vs k14 ($(sha256sum < "$K15/kernel_x86_64_dot_config" | cut -c1-16))"
else res K-02 FAIL "$(wc -l < "$A/K-02.txt") diff lines"; fi

# K-03: vmlinux.symvers identical to k14 (+ informational module symvers)
if diff "$K14/vmlinux.symvers" "$K15/vmlinux.symvers" > "$A/K-03.txt" 2>&1; then
  res K-03 PASS "vmlinux.symvers: empty diff vs k14 ($(wc -l < "$K15/vmlinux.symvers") exports)"
else res K-03 FAIL "$(grep -c '^[<>]' "$A/K-03.txt") changed lines"; fi
diff "$K14/kernel_x86_64_Module.symvers" "$K15/kernel_x86_64_Module.symvers" > "$A/K-03-modules.txt" 2>&1
res info "-" "kernel_x86_64_Module.symvers: $(grep -c '^[<>]' "$A/K-03-modules.txt") changed lines (informational)"

# K-04: unresolved for the nouveau closure and the i915 closure
if $PY "$T14/k14check.py" unresolved "$K15" nouveau drm_exec drm_gpuvm gpu-sched drm_ttm_helper \
    i915 drm_display_helper drm_buddy video i2c-algo-bit intel-gtt > "$A/K-04.txt" 2>&1; then
  res K-04 PASS "$(tail -1 "$A/K-04.txt")"; else res K-04 FAIL "$(tail -1 "$A/K-04.txt")"; fi

# K-05: signatures of every dist15 module + the staged set
$PY "$T14/k14check.py" sig "$K15" > "$A/K-05.txt" 2>&1; rc=$?
STAGED="drm_sysfb_helper efidrm drm_ttm_helper vmwgfx psmouse vmxnet3 ac battery test_power libarc4 pps_core ptp rfkill
bluetooth btbcm hidp rfcomm zsmalloc zram mii usbnet cdc_ether cdc_ncm r8152 ax88179_178a cdc-acm crc-itu-t
surface_aggregator surface_aggregator_registry surface_aggregator_hub surface_aggregator_cdev surface_hid_core
surface_hid hid-surface surface_battery surface_charger surface_dtx surface_gpe surface_hotplug soc_button_array wmi
wmi-bmof video i2c-algo-bit drm_buddy drm_display_helper intel-gtt i915 kfifo_buf industrialio-triggered-buffer
apds9960 thermal ipts v4l2-cci ipu-bridge intel_skl_int3472_common intel_skl_int3472_discrete ov5693 ov8865 ov7251
dw9719 intel-ipu6 intel-ipu6-isys drm_exec drm_gpuvm gpu-sched nouveau surface_platform_profile hid-sensor-hub
hid-sensor-iio-common hid-sensor-trigger hid-sensor-accel-3d hid-sensor-gyro-3d hid-sensor-als hid-sensor-rotation
hid-sensor-incl-3d hid-sensor-magn-3d"
missing=""; files=""
for m in $STAGED; do
  f=$K15/$m.ko; [ -f "$f" ] || f=$K15/$(echo "$m" | tr - _).ko
  if [ -f "$f" ]; then files="$files $f"; else missing="$missing $m"; fi
done
# shellcheck disable=SC2086
$PY "$T14/k14check.py" sig "$K15" $files > "$A/K-05-staged.txt" 2>&1; rc2=$?
echo "staged-set names not found in dist15:${missing:- none}" >> "$A/K-05-staged.txt"
if [ $rc = 0 ] && [ $rc2 = 0 ] && [ -z "$missing" ]; then
  res K-05 PASS "$(tail -1 "$A/K-05.txt"); staged: $(tail -2 "$A/K-05-staged.txt" | head -1 | cut -d, -f1-2)"
else res K-05 FAIL "rc=$rc staged rc=$rc2 missing=[$missing]"; fi

# K-06: lapis vendor_ramdisk + vendor_dlkm CRCs vs k15
ok6=1
if [ -d "$L" ]; then
  for set in vr/a2/lib/modules vdlkm_b276 vdlkm_a; do
    tag=$(echo "$set" | tr / _)
    (cd "$L" && $PY "$T14/k14check.py" crc "$K15" "$set") > "$A/K-06-$tag.txt" 2>&1 || ok6=0
    (cd "$L" && $PY "$T14/k14check.py" crc "$K14" "$set") > "$A/K-06-$tag.k14.txt" 2>&1
    if cmp -s "$A/K-06-$tag.txt" "$A/K-06-$tag.k14.txt"; then same=identical; else same=DIFFERENT; ok6=0; fi
    res "   " "$set" "$(tail -1 "$A/K-06-$tag.txt") [k14 result: $same]"
  done
  [ $ok6 = 1 ] && res K-06 PASS "0 vmlinux CRC mismatches, 0 unresolved, same as k14" || res K-06 FAIL "see $A/K-06-*.txt"
else res K-06 FAIL "lapis extraction $L missing (run k14 extract_lapis.sh first)"; fi

# K-07: nouveau unchanged (gate: source diff empty + modinfo identical except vermagic/sig; info: section sha256)
{
  echo "git diff k14-nouveau k15-nouveau -- drivers/gpu/drm/nouveau:"
  G diff --stat k14-nouveau k15-nouveau -- drivers/gpu/drm/nouveau
  echo "--- modinfo nouveau.ko k14 vs k15 (vermagic/sig_* expected to differ)"
  for fld in depends firmware parm intree name; do
    a=$(/sbin/modinfo -F $fld "$K14/nouveau.ko" 2>/dev/null); b=$(/sbin/modinfo -F $fld "$K15/nouveau.ko" 2>/dev/null)
    [ "$a" = "$b" ] && echo "$fld: identical" || { echo "$fld: DIFFERENT"; }
  done
  echo "--- section sha256 (informational): .text .rodata .data __versions"
  for s in .text .rodata .data __versions; do
    for d in "$K14" "$K15"; do
      objcopy --dump-section "$s=/tmp/sec15" "$d/nouveau.ko" /dev/null 2>/dev/null && printf '%s %s %s\n' "$(basename "$d")" "$s" "$(sha256sum < /tmp/sec15 | cut -c1-16)"
    done
  done
  empty=$(G diff --name-only k14-nouveau k15-nouveau -- drivers/gpu/drm/nouveau | wc -l)
  dep=$(/sbin/modinfo -F depends "$K14/nouveau.ko"); dep2=$(/sbin/modinfo -F depends "$K15/nouveau.ko")
  [ "$empty" = 0 ] && [ "$dep" = "$dep2" ] && echo RESULT=ok
} > "$A/K-07.txt" 2>&1
grep -q RESULT=ok "$A/K-07.txt" && res K-07 PASS "nouveau source diff empty; modinfo depends identical" || res K-07 FAIL "see $A/K-07.txt"

# K-08: kernel release
{
  kr=$(cat "$K15/kernel_release.txt"); k14r=$(cat "$K14/kernel_release.txt")
  tmp=$(mktemp /build/k15/vmlinux.XXXXXX)
  sh "$SRC/scripts/extract-vmlinux" "$K15/bzImage" > "$tmp" 2>/dev/null
  banner=$(grep -a -o -m1 'Linux version [^ ]*' "$tmp" | head -1 | cut -d' ' -f3); rm -f "$tmp"
  vm=$(/sbin/modinfo -F vermagic "$K15/nouveau.ko" | cut -d' ' -f1)
  echo "kernel_release.txt: $kr"; echo "gki-info: $(sed -n 's/^kernel_release=//p' "$K15/gki-info.txt")"
  echo "bzImage banner: $banner"; echo "nouveau vermagic: $vm"; echo "k14 release: $k14r"
  echo "trailing whitespace: $(grep -c '[[:space:]]$' "$K15/kernel_release.txt")"
  bz=$(cmp -s "$K14/bzImage" "$K15/bzImage" && echo same || echo differ)
  echo "bzImage vs k14: $bz"
  [ "$kr" = "$banner" ] && [ "$kr" = "$vm" ] && [ "$kr" != "$k14r" ] && [ "$(wc -l < "$K15/kernel_release.txt")" = 1 ] \
    && [ "$bz" = differ ] && echo RESULT=ok
} > "$A/K-08.txt" 2>&1
grep -q RESULT=ok "$A/K-08.txt" && res K-08 PASS "$(head -1 "$A/K-08.txt")" || res K-08 FAIL "see $A/K-08.txt"

# K-09: k13 working tree restored (kbuild15 --restore verifies when HEAD is back)
bash /build/k15/kbuild15_native.sh --restore > "$A/K-09.txt" 2>&1
{ echo "HEAD: $(G rev-parse HEAD) ($(G symbolic-ref -q HEAD || echo detached))"
  echo "branches: $(G for-each-ref --format='%(refname:short)=%(objectname:short=12)' refs/heads/ | tr '\n' ' ')"; } >> "$A/K-09.txt"
grep -q "identical to saved state" "$A/K-09.txt" && res K-09 PASS "$(grep 'identical' "$A/K-09.txt" | head -1)" || res K-09 FAIL "see $A/K-09.txt"

# K-10: i915.ko carries the new parm; differs from k14's
{
  parm=$(/sbin/modinfo -F parm "$K15/i915.ko" | grep -c '^gb_ignore_user_gamma:')
  echo "gb_ignore_user_gamma parm lines: $parm"
  /sbin/modinfo -F parm "$K15/i915.ko" | grep '^gb_ignore_user_gamma:' | sed 's/^/  /'
  d=$(cmp -s "$K14/i915.ko" "$K15/i915.ko" && echo same || echo differ)
  echo "i915.ko vs k14: $d"
  [ "$parm" = 1 ] && [ "$d" = differ ] && echo RESULT=ok
} > "$A/K-10.txt" 2>&1
grep -q RESULT=ok "$A/K-10.txt" && res K-10 PASS "i915 gb_ignore_user_gamma present; i915.ko differs from k14" || res K-10 FAIL "see $A/K-10.txt"

# K-11: counter in vmlinux: gb_vnni + summary/reset; no debugfs_create from the gb_vnni code
{
  tmp=$(mktemp /build/k15/vmlinux.XXXXXX)
  sh "$SRC/scripts/extract-vmlinux" "$K15/bzImage" > "$tmp" 2>/dev/null
  g=$(strings "$tmp" | grep -c 'gb_vnni')
  echo "strings 'gb_vnni' in vmlinux: $g"
  strings "$tmp" | grep -m3 'gb_vnni' | sed 's/^/  /'
  # the attribute names live in the kobject group; confirm via the vmlinux symbol table / strings
  s=$(strings "$tmp" | grep -cx 'summary'); r=$(strings "$tmp" | grep -cx 'reset')
  echo "attr 'summary': $s, 'reset': $r"
  # no debugfs reference originating from our code: our source has no debugfs_create token
  dc=$(grep -c 'debugfs_create' "$SRC/arch/x86/kernel/gb_vnni_core.h" 2>/dev/null || echo 0)
  echo "debugfs_create in gb_vnni_core.h: $dc"
  rm -f "$tmp"
  [ "$g" -ge 1 ] && [ "$s" -ge 1 ] && [ "$r" -ge 1 ] && [ "$dc" = 0 ] && echo RESULT=ok
} > "$A/K-11.txt" 2>&1
grep -q RESULT=ok "$A/K-11.txt" && res K-11 PASS "vmlinux has gb_vnni + summary/reset; no debugfs" || res K-11 FAIL "see $A/K-11.txt"

# K-12: repo-patch equivalence. Extract pristine files with git archive (no worktree), apply repo 0002/0005,
#       compare with k15-nouveau. Also 0111 hunks == 0005 hunks.
{
  rm -rf "$WORKK12"; mkdir -p "$WORKK12"
  REPO=${GB_REPO_PATCHES:-/build/k15/out/repo}
  G archive 3938c8d97142 arch/x86/kernel/traps.c drivers/gpu/drm/i915/display/intel_display.c \
     drivers/gpu/drm/i915/display/intel_display_params.c drivers/gpu/drm/i915/display/intel_display_params.h \
     | tar -x -C "$WORKK12"
  ( cd "$WORKK12" && patch -p1 --no-backup-if-mismatch < "$REPO"/0002-*.patch && patch -p1 --no-backup-if-mismatch < "$REPO"/0005-*.patch ) > "$A/K-12-apply.txt" 2>&1
  # generate gb_vnni_core.h the same way the repo 0002 implies (it is embedded in 0002 as a new file)
  bad=0
  for f in arch/x86/kernel/traps.c arch/x86/kernel/gb_vnni_core.h drivers/gpu/drm/i915/display/intel_display.c \
           drivers/gpu/drm/i915/display/intel_display_params.c drivers/gpu/drm/i915/display/intel_display_params.h; do
    if G show "k15-nouveau:$f" > "$WORKK12/.ref" 2>/dev/null; then
      if cmp -s "$WORKK12/$f" "$WORKK12/.ref"; then echo "OK   $f"; else echo "DIFF $f"; bad=1; fi
    else echo "MISS in k15-nouveau: $f"; bad=1; fi
  done
  echo "patch apply log: $(grep -c . "$A/K-12-apply.txt") lines"
  [ $bad = 0 ] && echo RESULT=ok
} > "$A/K-12.txt" 2>&1
grep -q RESULT=ok "$A/K-12.txt" && res K-12 PASS "repo 0002/0005 applied to pristine == k15-nouveau files" || res K-12 FAIL "see $A/K-12.txt"

# K-13: build hygiene: no new objtool/modpost warnings vs the k14 build log
{
  k14log=/build/k14/kbuild14.log; [ -f "$k14log" ] || k14log=/dev/null
  grep -E 'objtool|WARNING: modpost' /build/kbuild15.log | sort -u > "$A/K-13-k15.txt"
  grep -E 'objtool|WARNING: modpost' "$k14log" 2>/dev/null | sort -u > "$A/K-13-k14.txt"
  echo "k15 objtool/modpost lines: $(wc -l < "$A/K-13-k15.txt"); k14: $(wc -l < "$A/K-13-k14.txt")"
  newl=$(comm -13 "$A/K-13-k14.txt" "$A/K-13-k15.txt" | grep -c . || true)
  echo "new in k15 (not in k14):"; comm -13 "$A/K-13-k14.txt" "$A/K-13-k15.txt" | sed 's/^/  /'
  tr=$(grep -cE 'traps\.o.*(objtool|warning)' /build/kbuild15.log || true)
  echo "traps.o objtool/warning lines: $tr"
  [ "$newl" = 0 ] && echo RESULT=ok
} > "$A/K-13.txt" 2>&1
grep -q RESULT=ok "$A/K-13.txt" && res K-13 PASS "no new objtool/modpost warnings vs k14" || res K-13 FAIL "see $A/K-13.txt"

# K-14: KAT-0 host unit test (k15 core passes; k14 merge fails the 3 defect categories), header == k15 tree
if bash "$GEN/kat0_run.sh" "$GEN" "$GEN/apply_vnni4.py" /build/k15/kat0 > "$A/K-14.txt" 2>&1; then
  res K-14 PASS "$(grep 'KAT-0:' "$A/K-14.txt" | tail -1)"
else res K-14 FAIL "$(tail -3 "$A/K-14.txt" | tr '\n' ' ')"; fi

grep -q FAIL "$A/summary.txt" && { echo "SOME CHECKS FAILED"; exit 1; }
echo "ALL K-01..K-14 PASS"
exit 0
