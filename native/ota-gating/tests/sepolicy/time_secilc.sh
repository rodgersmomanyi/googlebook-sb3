#!/bin/bash
# Builder: wall time of init's policy compile (lapis .276 secilc, same arguments as run_secilc.sh) with the plain and the
# OTA-robust odm cil, 5 runs each, interleaved.  bash time_secilc.sh <dir with odm_orig.cil odm_wrapped.cil> <fs root>
set -u
D=$(cd "$1" && pwd); FS=$2
SYS=$FS/system_b/system; SE=$FS/system_ext_b; PR=$FS/product_b; VE=$FS/vendor_b
V=$(cat "$VE/etc/selinux/plat_sepolicy_vers.txt"); G=$(cat "$VE/etc/selinux/genfs_labels_version.txt")
one() {
  local args=("$SYS/etc/selinux/plat_sepolicy.cil" -m -M true -G -N -c 30 "$SYS/etc/selinux/mapping/$V.cil" -o "$D/t.pol" -f /dev/null)
  [ -f "$SYS/etc/selinux/mapping/$V.compat.cil" ] && args+=("$SYS/etc/selinux/mapping/$V.compat.cil")
  args+=("$SE/etc/selinux/system_ext_sepolicy.cil" "$SE/etc/selinux/mapping/$V.cil" "$PR/etc/selinux/product_sepolicy.cil"
         "$PR/etc/selinux/mapping/$V.cil" "$VE/etc/selinux/plat_pub_versioned.cil" "$VE/etc/selinux/vendor_sepolicy.cil" "$1"
         "$SYS/etc/selinux/plat_sepolicy_genfs_$G.cil")
  local t0 t1; t0=$(date +%s%N)
  LD_LIBRARY_PATH=$SYS/lib64/bootstrap:$SYS/lib64 "$SYS/bin/bootstrap/linker64" "$SYS/bin/secilc" "${args[@]}" > /dev/null 2>&1
  t1=$(date +%s%N); echo $(( (t1 - t0) / 1000000 ))
}
for i in 1 2 3 4 5; do echo "plain $(one "$D/odm_orig.cil") ms   wrapped $(one "$D/odm_wrapped.cil") ms"; done
