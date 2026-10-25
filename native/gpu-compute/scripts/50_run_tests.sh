#!/bin/bash
# All builder-side tests of the gpu-compute component (needs 20_build_host.sh and 30_build_android.sh first).
# usage: 50_run_tests.sh [host build dir] [android out dir]       logs: $W/logs/tests/*.log, summary on stdout
#   GC-01  ELF contract (check_elf.py) + import check against lapis 276.R4 (nvk-gfxdriver's import_check.py, read-only)
#   GC-02  stub self test (test_loader) + llama.cpp test-backend-ops -b Vulkan0 subsets (element-wise/norm/rope/softmax/
#          glu, MUL_MAT f16/q4_0/q8_0/q4_K/q5_K/q6_K, FLASH_ATTN_EXT f16 hs 64/128) + gc_probe, all through the stub on lavapipe
#   GC-03..08, GC-11 (host part)  end-to-end service tests (test_service.py; sudo: real SO_PEERCRED for several uids)
#   GC-09  drm-shim TU117 smoke + no-device refusal in a bionic chroot of 276.R4 (Android build, component 4's 8a ICD)
#   models: synthetic tiny GGUFs (40_make_tiny_gguf.py), generated twice to show determinism
# shellcheck disable=SC2015,SC2024,SC2086  # res() never fails; redirects run as the builder user; $NICE splits on purpose
set -uo pipefail
. "$(dirname "$0")/00_env.sh"
HB=${1:-$W/b-host}
AO=${2:-$W/out-android}
L=$W/logs/tests
rm -rf "$L"
mkdir -p "$L"
ROOTFS=/build/native/dgpu/nvk-sync/rootfs-276R4
IMPORT_CHECK=/build/native/dgpu/nvk-sync/import_check.py
# GC-09 inputs from component 4 (nvk-gfxdriver), copied once into this scratch (their scratch layout changes):
# the shipped 8a ICD (= C:/GB-build/native/nvk-android/bin/vulkan.nouveau.so) and their bionic noop drm-shim.
ICD8A=$W/inputs/vulkan.nouveau.so
SHIM=$W/inputs/libnouveau_noop_drm_shim.so
mkdir -p "$W/inputs"
[ -f "$ICD8A" ] || cp /build/native/nvk-android/stage-8a/vulkan.nouveau.so "$ICD8A"
[ -f "$SHIM" ] || cp /build/native/dgpu/nvk-sync/run-8a-1/libnouveau_noop_drm_shim.so "$SHIM"
OPS=${OPS:-ADD,MUL,SCALE,RMS_NORM,GET_ROWS,SET_ROWS,CPY,CONT,ROPE,SOFT_MAX,GLU}
MM_FILTER=${MM_FILTER:-'type_a=(f16|q4_0|q8_0|q4_K|q5_K|q6_K),type_b=f32,m=16,n=(1|9),k=256,bs=\[1,1\]'}
FA_FILTER=${FA_FILTER:-'hsk=(64|128),hsv=(64|128),.*mask=1,sinks=0,max_bias=0.000000,logit_softcap=0.000000,prec=f32,type_K=f16,type_V=f16,permute=\[0,1,2,3\]'}
SUMMARY=()
res() { SUMMARY+=("$1 $2 $3"); echo "RESULT $1 $2 $3"; }
gb_check_space || exit 1

echo "### models"
mkdir -p "$W/models" "$L/m2"
for q in q4_0 q8_0; do
  python3 -I -B "$SRC/scripts/40_make_tiny_gguf.py" "$LLAMA" "$W/models/tiny-$q.gguf" "$q" 1234 > /dev/null
  python3 -I -B "$SRC/scripts/40_make_tiny_gguf.py" "$LLAMA" "$L/m2/tiny-$q.gguf" "$q" 1234
done
sha256sum "$W"/models/*.gguf "$L"/m2/*.gguf | tee "$L/models.sha256"
if cmp -s "$W/models/tiny-q4_0.gguf" "$L/m2/tiny-q4_0.gguf" && cmp -s "$W/models/tiny-q8_0.gguf" "$L/m2/tiny-q8_0.gguf"; then
  res MODEL PASS "synthetic GGUFs byte-identical across two generations"
else
  res MODEL FAIL "GGUF generation not deterministic"
fi
rm -rf "$L/m2"

echo "### GC-01 ELF checks"
python3 -I -B "$SRC/test/check_elf.py" "$AO" "$HB/gb_vk_core_names.txt" > "$L/gc01_check_elf.log" 2>&1
r=$?; cat "$L/gc01_check_elf.log"
[ $r -eq 0 ] && res GC-01a PASS "$(tail -1 "$L/gc01_check_elf.log")" || res GC-01a FAIL "$(tail -1 "$L/gc01_check_elf.log")"
python3 -I "$IMPORT_CHECK" --root "$ROOTFS" --extra-provider "$AO/lib64/libgb_nvk_loader.so" \
  "$AO/lib64/libgb_gpu_compute.so" "$AO/lib64/libgb_nvk_loader.so" "$AO/gb_gpu_compute" > "$L/gc01_import.log" 2>&1
r=$?; cat "$L/gc01_import.log"
[ $r -eq 0 ] && res GC-01b PASS "NEEDED within LLNDK(276.R4)+siblings, all imports resolve" ||
  res GC-01b FAIL "$(grep -c FAIL "$L/gc01_import.log") problems"

echo "### GC-02 stub self test + test-backend-ops on lavapipe through the stub"
"$HB/test_loader" symbols "$HB/gb_vk_core_names.txt" > "$L/gc02_loader.log" 2>&1; r1=$?
GB_NVK_ICD=$LVP_ICD "$HB/test_loader" full >> "$L/gc02_loader.log" 2>&1; r2=$?
GB_NVK_ICD=/nonexistent/vulkan.so "$HB/test_loader" noicd >> "$L/gc02_loader.log" 2>&1; r3=$?
cat "$L/gc02_loader.log"
[ $r1$r2$r3 = 000 ] && res GC-02a PASS "$(grep -c 'TEST_LOADER.*PASS' "$L/gc02_loader.log")/3 test_loader modes" ||
  res GC-02a FAIL "test_loader rc $r1/$r2/$r3"
ldd "$HB/test-backend-ops" | grep -iE "vulkan|gb_nvk" | tee "$L/gc02_ldd.log"
if grep -q libvulkan "$L/gc02_ldd.log"; then res GC-02b FAIL "test-backend-ops links libvulkan"; else
  res GC-02b PASS "test-backend-ops resolves Vulkan only through libgb_nvk_loader.so"; fi
GB_NVK_ICD=$LVP_ICD GGML_VK_VISIBLE_DEVICES=0 timeout 3600 $NICE "$HB/test-backend-ops" test -b Vulkan0 -o "$OPS" \
  > "$L/gc02_backend_ops.log" 2>&1; r=$?
tail -4 "$L/gc02_backend_ops.log"
ok=$(grep -E "^\s+[0-9]+/[0-9]+ tests passed" "$L/gc02_backend_ops.log" | tail -1)
[ $r -eq 0 ] && grep -q "Backend Vulkan0: .*OK" "$L/gc02_backend_ops.log" && res GC-02c PASS "ops $OPS: $ok" ||
  res GC-02c FAIL "rc $r $ok"
GB_NVK_ICD=$LVP_ICD GGML_VK_VISIBLE_DEVICES=0 timeout 3600 $NICE "$HB/test-backend-ops" test -b Vulkan0 -o MUL_MAT \
  -p "$MM_FILTER" > "$L/gc02_mul_mat.log" 2>&1; r=$?
tail -4 "$L/gc02_mul_mat.log"
ok=$(grep -E "^\s+[0-9]+/[0-9]+ tests passed" "$L/gc02_mul_mat.log" | tail -1)
[ $r -eq 0 ] && grep -q "Backend Vulkan0: .*OK" "$L/gc02_mul_mat.log" && res GC-02d PASS "MUL_MAT $MM_FILTER: $ok" ||
  res GC-02d FAIL "rc $r $ok"
GB_NVK_ICD=$LVP_ICD GGML_VK_VISIBLE_DEVICES=0 timeout 3600 $NICE "$HB/test-backend-ops" test -b Vulkan0 -o FLASH_ATTN_EXT   -p "$FA_FILTER" > "$L/gc02_flash_attn.log" 2>&1; r=$?
tail -4 "$L/gc02_flash_attn.log"
ok=$(grep -E "^\s+[0-9]+/[0-9]+ tests passed" "$L/gc02_flash_attn.log" | tail -1)
[ $r -eq 0 ] && grep -q "Backend Vulkan0: .*OK" "$L/gc02_flash_attn.log" && res GC-02f PASS "FLASH_ATTN_EXT f16 hs 64/128: $ok" ||
  res GC-02f FAIL "rc $r $ok"
GB_NVK_ICD=$LVP_ICD GGML_VK_VISIBLE_DEVICES=0 "$HB/gc_probe" "$HB/libgb_gpu_compute.so" > "$L/gc02_probe.log" 2>&1; r=$?
grep PROBE "$L/gc02_probe.log"
if [ $r -eq 0 ] && grep -q "PROBE compute .*correct" "$L/gc02_probe.log"; then
  res GC-02e PASS "$(grep PROBE "$L/gc02_probe.log" | tr '\n' ' ')"
else
  res GC-02e FAIL "probe rc $r $(grep PROBE "$L/gc02_probe.log" | tr '\n' ' ')"
fi

echo "### GC-03..08 + GC-11 (host): end-to-end over the UNIX socket (sudo for multi-uid SO_PEERCRED)"
sudo -n python3 -I -B "$SRC/test/test_service.py" "$HB" "$W/models/tiny-q4_0.gguf" "$W/models/tiny-q8_0.gguf" \
  "$W/run-service" > "$L/gc03_service.log" 2>&1; r=$?
grep -E "^(PASS|FAIL)|SUMMARY|Traceback|Error" "$L/gc03_service.log"
sudo -n cp "$W"/run-service/server-*.log "$W/run-service/results.json" "$L/" 2>/dev/null
sudo -n chown "$(id -u):$(id -g)" "$L"/*.log "$L"/results.json 2>/dev/null
[ $r -eq 0 ] && res GC-03-08 PASS "$(grep SUMMARY "$L/gc03_service.log")" || res GC-03-08 FAIL "$(grep SUMMARY "$L/gc03_service.log")"

echo "### GC-09 / GC-11 (bionic): Android build in a chroot of lapis $(cat "$ROOTFS/BUILD_ID")"
D=$W/chroot-data; O=$W/chroot-odm
sudo -n rm -rf "$D" "$O"
mkdir -p "$D" "$O/etc" "$O/dgpu/lib64/gpu-compute" "$O/dgpu/lib64/hw"
cp "$AO/gb_gpu_compute" "$AO/test/gc_probe" "$SHIM" "$D/"
cp "$AO/lib64/libgb_gpu_compute.so" "$AO/lib64/libgb_nvk_loader.so" "$O/dgpu/lib64/gpu-compute/"
cp "$ICD8A" "$O/dgpu/lib64/hw/vulkan.nouveau.so"
sha256sum "$O/dgpu/lib64/hw/vulkan.nouveau.so" "$SHIM" | tee "$L/gc09_inputs.sha256"
P=/odm/dgpu/lib64/gpu-compute/libgb_gpu_compute.so
echo vm > "$O/etc/gb_hw"
CHROOT_ODM=$O bash "$SRC/test/chroot_dir.sh" "$ROOTFS" "$D" "/data/gb_gpu_compute" > "$L/gc11_bionic_vm.log" 2>&1
cat "$L/gc11_bionic_vm.log"
grep -q "^exit=3" "$L/gc11_bionic_vm.log" && res GC-11h PASS "bionic launcher, gb_hw=vm: exit 3 NO_DEVICE" ||
  res GC-11h FAIL "$(grep ^exit= "$L/gc11_bionic_vm.log")"
echo native > "$O/etc/gb_hw"
CHROOT_ODM=$O bash "$SRC/test/chroot_dir.sh" "$ROOTFS" "$D" "/data/gb_gpu_compute" "/data/gc_probe $P" \
  > "$L/gc11_bionic_nodev.log" 2>&1
cat "$L/gc11_bionic_nodev.log"
if [ "$(grep -c '^exit=3' "$L/gc11_bionic_nodev.log")" = 2 ] && grep -q "PROBE no-device" "$L/gc11_bionic_nodev.log"; then
  res GC-11i PASS "bionic, native, no nouveau node: launcher exit 3; payload NVK via stub: 0 devices, exit 3"
else
  res GC-11i FAIL "$(grep -E '^exit=|PROBE' "$L/gc11_bionic_nodev.log" | tr '\n' ' ')"
fi
CHROOT_ODM=$O CHROOT_ENV="LD_PRELOAD=/data/$(basename "$SHIM") NOUVEAU_CHIPSET=167" \
  bash "$SRC/test/chroot_dir.sh" "$ROOTFS" "$D" "/data/gc_probe $P" > "$L/gc09_drmshim.log" 2>&1
cat "$L/gc09_drmshim.log"
# exit 0 (result correct) or 5 (wrong result: the noop drm-shim executes nothing). Either way the NVK device was
# enumerated through the stub, a ggml backend (VkDevice) was created and a mul_mat pipeline compiled (NAK) and run.
if grep -qE "^exit=(0|5)$" "$L/gc09_drmshim.log" && grep -q "PROBE backend" "$L/gc09_drmshim.log" &&
   grep -q "PROBE compute mul_mat" "$L/gc09_drmshim.log"; then
  res GC-09 PASS "$(grep PROBE "$L/gc09_drmshim.log" | tr '\n' ' ')"
else
  res GC-09 FAIL "$(grep -E '^exit=|PROBE' "$L/gc09_drmshim.log" | tr '\n' ' ')"
fi
sudo -n rm -rf "$D" "$O"

echo
echo "===== SUMMARY"
nf=0
for s in "${SUMMARY[@]}"; do echo "$s"; case $s in *" FAIL "*) nf=$((nf + 1)) ;; esac; done
echo "failures: $nf"
[ $nf -eq 0 ]
