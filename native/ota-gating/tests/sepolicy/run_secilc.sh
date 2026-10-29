#!/bin/bash
# Builder: compile lapis .276's split SELinux policy exactly as its init does (selinux.cpp LoadSplitPolicy argument
# order; lapis's own /system/bin/secilc run through its bootstrap linker) with different /odm cil files, and compare.
#   bash run_secilc.sh <dir with odm_orig.cil odm_wrapped.cil odm_orig_missing.cil odm_wrapped_missing.cil> <fs root>
set -u
D=$(cd "$1" && pwd); FS=$2
SYS=$FS/system_b/system; SE=$FS/system_ext_b; PR=$FS/product_b; VE=$FS/vendor_b
V=$(cat "$VE/etc/selinux/plat_sepolicy_vers.txt"); G=$(cat "$VE/etc/selinux/genfs_labels_version.txt")
echo "vendor plat version $V, genfs $G"
sha256sum "$SYS/bin/secilc" "$SYS/etc/selinux/plat_sepolicy.cil" "$VE/etc/selinux/vendor_sepolicy.cil" | sed "s#$FS/##"
secilc() { LD_LIBRARY_PATH=$SYS/lib64/bootstrap:$SYS/lib64 nice -n 19 ionice -c3 "$SYS/bin/bootstrap/linker64" "$SYS/bin/secilc" "$@" 2>&1 | grep -v 'linker configuration'; return "${PIPESTATUS[0]}"; }
compile() {   # compile <odm cil or -> <out>
  local args=("$SYS/etc/selinux/plat_sepolicy.cil" -m -M true -G -N -c 30 "$SYS/etc/selinux/mapping/$V.cil" -o "$2" -f /dev/null)
  [ -f "$SYS/etc/selinux/mapping/$V.compat.cil" ] && args+=("$SYS/etc/selinux/mapping/$V.compat.cil")
  args+=("$SE/etc/selinux/system_ext_sepolicy.cil" "$SE/etc/selinux/mapping/$V.cil")
  [ -f "$SE/etc/selinux/mapping/$V.compat.cil" ] && args+=("$SE/etc/selinux/mapping/$V.compat.cil")
  args+=("$PR/etc/selinux/product_sepolicy.cil" "$PR/etc/selinux/mapping/$V.cil")
  args+=("$VE/etc/selinux/plat_pub_versioned.cil" "$VE/etc/selinux/vendor_sepolicy.cil")
  [ "$1" != - ] && args+=("$1")
  args+=("$SYS/etc/selinux/plat_sepolicy_genfs_$G.cil")
  rm -f "$2"; secilc "${args[@]}" > "$2.log"; local rc=$?
  echo "$(basename "$2"): rc=$rc $( [ -f "$2" ] && sha256sum < "$2" | cut -c1-64 || echo no-output) $(grep -c . "$2.log") log lines"
}
compile - "$D/out_stock.pol"
compile "$D/odm_orig.cil" "$D/out_orig.pol"
compile "$D/odm_wrapped.cil" "$D/out_wrapped.pol"
compile "$D/odm_orig_missing.cil" "$D/out_orig_missing.pol"
compile "$D/odm_wrapped_missing.cil" "$D/out_wrapped_missing.pol"
echo "--- log of orig_missing (the reboot-loop case):"; head -n 5 "$D/out_orig_missing.pol.log"
echo "--- log of wrapped_missing:"; grep -i -E 'optional|disabl|missing' "$D/out_wrapped_missing.pol.log" | head -n 5
cmp -s "$D/out_orig.pol" "$D/out_wrapped.pol" && echo "RESULT orig == wrapped: IDENTICAL" || echo "RESULT orig == wrapped: DIFFERENT"
cmp -s "$D/out_wrapped.pol" "$D/out_wrapped_missing.pol" && echo "RESULT wrapped == wrapped+missing: IDENTICAL" || echo "RESULT wrapped == wrapped+missing: DIFFERENT"
cmp -s "$D/out_stock.pol" "$D/out_orig.pol" && echo "RESULT stock == orig: IDENTICAL (unexpected)" || echo "RESULT stock != orig (expected: the odm cil makes types permissive)"
[ -f "$D/out_orig_missing.pol" ] && echo "RESULT orig+missing: COMPILED (unexpected)" || echo "RESULT orig+missing: FAILED (expected: unknown type)"
