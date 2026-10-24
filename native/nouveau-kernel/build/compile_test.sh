#!/bin/bash
# nouveau-kernel compile test (builder VM). Out-of-tree O= build against the shared read-only tree
# /build/native/ktree; never writes into the tree. Run: bash compile_test.sh [config|prep|build|all]
#
#   config : base = /build/dist/kernel_x86_64_dot_config (the kernel #11 .config), + CONFIG_DRM_NOUVEAU=m,
#            olddefconfig, then diff / savedefconfig -> exact gki_defconfig delta.
#   prep   : RUST and TRIM_UNUSED_KSYMS are switched off ONLY in the compile-test objdir (nouveau has no Rust code;
#            avoids building rust/ and vmlinux), modules_prepare, Module.symvers of kernel #11 copied in so modpost
#            resolves vmlinux + GKI-module exports with their real CRCs.
#   build  : copies of the touched source dirs under src/ (nouveau, scheduler, drm_exec/drm_gpuvm/mxm-wmi) are built
#            as M= modules; modpost runs in warn mode and every unresolved symbol is classified against kernel #11's
#            System.map (present but trimmed = kept automatically by an in-tree Kleaf build; absent = real problem).
set -e
export PATH=/build/gki/prebuilts/clang/host/linux-x86/clang-r584948c/bin:/build/gki/prebuilts/rust-toolchain/linux-x86/1.91.1.p3/bin:/build/gki/prebuilts/clang-tools/linux-x86/bin:$PATH
K=/build/native/ktree
W=/build/native/nouveau-kernel
O=$W/kobj
D=/build/dist
M="make -C $K O=$O LLVM=1 LLVM_IAS=1"
step=${1:-all}

do_config() {
  mkdir -p $O $W/out
  cp $D/kernel_x86_64_dot_config $O/.config
  $M olddefconfig >/dev/null
  cp $O/.config $W/out/config.base
  $M savedefconfig >/dev/null && cp $O/defconfig $W/out/defconfig.base
  $K/scripts/config --file $O/.config --module DRM_NOUVEAU
  $M olddefconfig >/dev/null
  # dispnv04-only TV/TMDS encoder modules (pre-NV50 boards); useless for TU117, keep them out of the module set
  $K/scripts/config --file $O/.config --disable DRM_NOUVEAU_CH7006 --disable DRM_NOUVEAU_SIL164
  $M olddefconfig >/dev/null
  cp $O/.config $W/out/config.nouveau
  $M savedefconfig >/dev/null && cp $O/defconfig $W/out/defconfig.nouveau
  diff $W/out/config.base $W/out/config.nouveau > $W/out/config.diff || true
  diff $W/out/defconfig.base $W/out/defconfig.nouveau > $W/out/defconfig.diff || true
  grep -c . $W/out/config.diff || true
  echo "RUST in base: $(grep -E '^CONFIG_RUST=' $W/out/config.base || echo off)"
  echo CONFIG_DONE
}

do_prep() {
  cp $W/out/config.nouveau $O/.config
  $K/scripts/config --file $O/.config --disable RUST --disable TRIM_UNUSED_KSYMS
  $M olddefconfig >/dev/null
  grep -E '^CONFIG_DRM_NOUVEAU|^CONFIG_DRM_(EXEC|GPUVM|SCHED|TTM_HELPER|DISPLAY_HELPER)=|^CONFIG_MXM_WMI|^CONFIG_ACPI_(WMI|VIDEO)=|^CONFIG_I2C_ALGOBIT|^CONFIG_FW_CACHE' $O/.config
  $M -j3 modules_prepare > $W/out/prep.log 2>&1 || { tail -30 $W/out/prep.log; exit 1; }
  cp $D/kernel_x86_64_Module.symvers $O/Module.symvers
  # CONFIG_MODULE_SIG_PROTECT_LIST: Kleaf generates it; modpost looks in objtree first. Empty = no GKI-protected
  # module names (only matters for signing/export-protection checks, not for compiling).
  : > $O/protected_module_names_list
  echo PREP_DONE
}

do_build() {
  rm -rf $W/src; mkdir -p $W/src/deps
  cp -r $K/drivers/gpu/drm/nouveau $W/src/nouveau
  cp -r $K/drivers/gpu/drm/scheduler $W/src/sched
  rm -rf $W/src/sched/tests
  sed -i '/KUNIT_TEST/d' $W/src/sched/Makefile
  cp $K/drivers/gpu/drm/drm_exec.c $K/drivers/gpu/drm/drm_gpuvm.c $K/drivers/platform/x86/mxm-wmi.c $W/src/deps/
  printf 'obj-m += drm_exec.o drm_gpuvm.o mxm-wmi.o\n' > $W/src/deps/Kbuild
  export KBUILD_MODPOST_WARN=1
  $M -j3 M=$W/src/deps modules > $W/out/build_deps.log 2>&1 || { tail -40 $W/out/build_deps.log; exit 1; }
  $M -j3 M=$W/src/sched modules > $W/out/build_sched.log 2>&1 || { tail -40 $W/out/build_sched.log; exit 1; }
  $M -j3 M=$W/src/nouveau KBUILD_EXTRA_SYMBOLS="$W/src/deps/Module.symvers $W/src/sched/Module.symvers" \
     modules > $W/out/build_nouveau.log 2>&1 || { tail -60 $W/out/build_nouveau.log; exit 1; }
  grep -iE 'warning|error' $W/out/build_*.log | grep -v 'modpost: "' | head -40 || true
  # classify modpost unresolved symbols
  cut -f2 $O/Module.symvers | sort -u > $W/out/exported_k11.txt
  awk '$2 ~ /^[TtDdRrBb]$/ {print $3}' $D/System.map | sort -u > $W/out/systemmap_k11.txt
  : > $W/out/unresolved.txt
  for f in $(find $W/src -name '*.ko'); do
    llvm-nm -u $f | awk '{print $2}' | sort -u > /tmp/nvk_u.txt
    cat $W/src/deps/Module.symvers $W/src/sched/Module.symvers 2>/dev/null | cut -f2 | sort -u > /tmp/nvk_own.txt
    comm -23 /tmp/nvk_u.txt $W/out/exported_k11.txt | comm -23 - /tmp/nvk_own.txt | while read s; do
      if grep -qx "$s" $W/out/systemmap_k11.txt; then echo "$(basename $f) $s TRIMMED_IN_K11(kept-by-in-tree-build)";
      else echo "$(basename $f) $s ABSENT_FROM_VMLINUX"; fi
    done >> $W/out/unresolved.txt
  done
  echo "unresolved total: $(wc -l < $W/out/unresolved.txt), absent from vmlinux: $(grep -c ABSENT $W/out/unresolved.txt || true)"
  find $W/src -name '*.ko' -exec ls -la {} \;
  for f in $(find $W/src -name '*.ko'); do echo "== $(basename $f)"; /sbin/modinfo $f | grep -E '^(depends|vermagic|firmware)' | grep -v '^firmware' ; done
  /sbin/modinfo -F firmware $W/src/nouveau/nouveau.ko | grep -E 'tu117|tu116' > $W/out/nouveau_fw_tu11x.txt || true
  /sbin/modinfo -F firmware $W/src/nouveau/nouveau.ko > $W/out/nouveau_fw_all.txt || true
  /sbin/modinfo -p $W/src/nouveau/nouveau.ko > $W/out/nouveau_params.txt || true
  echo BUILD_DONE
}

case $step in
  config) do_config;;
  prep) do_prep;;
  build) do_build;;
  all) do_config; do_prep; do_build;;
esac
