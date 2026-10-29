#!/bin/bash
# Stage the Android (lapis, x86_64) camera HAL tree for the SB3 native boot.
# Output: $O/odm/... (default O=$W/out; to be merged into the gbinit sb3/native /odm overlay tree).
#  - libc++_shared.so from NDK r27c is shipped as libc++_lcam.so (soname + NEEDED rewritten) so it
#    cannot clash with the vendor's own libc++_shared.so (OpenVINO) in the same process.
#  - RUNPATHs are removed (Android linker namespaces resolve /odm/lib64).
#  - The IPA module is re-signed after its last modification (libcamera verifies the signature to run the IPA
#    in-process; an unsigned/changed module would run isolated through /odm/bin/libcamera/).
#  - (wave 5, C-1) Tool order: llvm-strip FIRST, patchelf LAST. llvm-strip (llvm-objcopy) re-packs file offsets;
#    run after patchelf it moved the PT_LOAD that patchelf adds for the grown .dynstr off its vaddr (p_offset !=
#    p_vaddr mod p_align). bionic maps every PT_LOAD page-wise, so it read .dynstr 0xb20 bytes off and refused
#    camera.sb3.so ('cannot find "n34_0_Box_C" from verneed[0]'). patchelf gets --page-size = the file's largest
#    PT_LOAD p_align (NDK libc++_shared.so is 16 KiB aligned). Nothing rewrites an ELF after patchelf; the PT_LOAD
#    gate below fails the stage on any staged ELF bionic could not map. `stage.sh --gate FILE...` runs only the gate.
set -euo pipefail

# PT_LOAD gate (C-1): every PT_LOAD of every file needs a power-of-two p_align, p_offset == p_vaddr modulo
# max(p_align, 4 KiB page) and p_offset + p_filesz inside the file (readelf -lW: the program headers bionic uses).
ptload_gate() {
  local f t off va pa fsz msz rest al m sz n bad=0
  for f in "$@"; do
    n=0
    sz=$(stat -c %s "$f")
    while read -r t off va pa fsz msz rest; do
      [ "$t" = LOAD ] || continue
      n=$((n + 1))
      al=$(( ${rest##* } ))
      m=$(( al > 4096 ? al : 4096 ))
      if (( (al & (al - 1)) != 0 || (off - va) % m != 0 || off + fsz > sz )); then
        printf 'PT_LOAD gate: FAIL %s: LOAD offset %s vaddr %s filesz %s align 0x%x (offset != vaddr mod 0x%x)\n' \
          "$f" "$off" "$va" "$fsz" "$al" "$m" >&2
        bad=1
      fi
    done < <(readelf -lW "$f" 2>/dev/null)
    if [ "$n" = 0 ]; then
      echo "PT_LOAD gate: FAIL $f: no PT_LOAD (not a loadable ELF)" >&2
      bad=1
    fi
  done
  return $bad
}
if [ "${1:-}" = --gate ]; then
  shift
  ptload_gate "$@" || exit 1
  echo "PT_LOAD gate: OK ($# file(s))"
  exit 0
fi
maxalign() {   # the largest PT_LOAD p_align of $1, at least the 4 KiB page
  local t rest al m=4096
  while read -r t rest; do
    [ "$t" = LOAD ] || continue
    al=$(( ${rest##* } ))
    if [ "$al" -gt "$m" ]; then m=$al; fi
  done < <(readelf -lW "$1")
  echo $m
}

W=/build/native/camera-userspace
B=$W/lc-build
NDKBIN=/build/ndk/toolchains/llvm/prebuilt/linux-x86_64/bin
NDKLIB=/build/ndk/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/lib/x86_64-linux-android
O=${O:-$W/out}
CFG=$W/config
ninja -C $B -j${J:-2}
# lcam-probe: bring-up tool (tools/lcam_probe.cpp) linked against the build's libcamera
$NDKBIN/x86_64-linux-android34-clang++ -std=c++20 -O2 -I$W/src/libcamera/include -I$B/include \
  -o $W/lcam-probe $W/tools/lcam_probe.cpp -L$B/src/libcamera -L$B/src/libcamera/base \
  -lcamera -lcamera-base
# a previous output is kept, never deleted: moved to prev/
if [ -e $O ]; then mkdir -p ${O%/*}/prev && mv $O ${O%/*}/prev/${O##*/}.$(date -u +%Y%m%dT%H%M%SZ); fi
mkdir -p $O/odm/lib64/hw $O/odm/lib64/libcamera/ipa $O/odm/bin/libcamera \
  $O/odm/etc/libcamera/ipa/simple $O/odm/etc/permissions

cp $B/src/libcamera/base/libcamera-base.so $O/odm/lib64/
cp $B/src/libcamera/libcamera.so $O/odm/lib64/
cp $B/src/android/libcamera-hal.so $O/odm/lib64/hw/camera.sb3.so
cp $B/src/ipa/simple/ipa_soft_simple.so $O/odm/lib64/libcamera/ipa/
cp $B/src/libcamera/proxy/worker/soft_ipa_proxy $O/odm/bin/libcamera/
cp $NDKLIB/libc++_shared.so $O/odm/lib64/libc++_lcam.so
cp $W/lcam-probe $O/odm/bin/lcam-probe

LIBCXX=$O/odm/lib64/libc++_lcam.so
HAL=$O/odm/lib64/hw/camera.sb3.so
IPA=$O/odm/lib64/libcamera/ipa/ipa_soft_simple.so
USERS="$O/odm/lib64/libcamera-base.so $O/odm/lib64/libcamera.so $HAL $IPA $O/odm/bin/libcamera/soft_ipa_proxy \
       $O/odm/bin/lcam-probe"
# 1. strip first (it re-lays out the file)
for f in $LIBCXX $USERS; do
  $NDKBIN/llvm-strip --strip-unneeded "$f"
done
# 2. patchelf last, one call per file (it keeps every PT_LOAD congruent; nothing may re-lay a file out after it)
patchelf --page-size $(maxalign $LIBCXX) --set-soname libc++_lcam.so $LIBCXX
for f in $USERS; do
  args=(--replace-needed libc++_shared.so libc++_lcam.so --remove-rpath)
  if [ "$f" = "$HAL" ]; then args=(--set-soname camera.sb3.so "${args[@]}"); fi
  patchelf --page-size $(maxalign "$f") "${args[@]}" "$f"
done

# 3. re-sign the (modified) IPA module with the build's private key, after its last modification, and check it
$W/src/libcamera/src/ipa/ipa-sign.sh $B/src/ipa-priv-key.pem $IPA $IPA.sign
openssl pkey -in $B/src/ipa-priv-key.pem -pubout -out $W/ipa-pub.pem
openssl dgst -sha256 -verify $W/ipa-pub.pem -signature $IPA.sign $IPA

# 4. PT_LOAD gate over every ELF in the output (exactly the 7 staged ones)
ELFS=$(for f in $(find $O -type f | sort); do
         if [ "$(head -c4 "$f" | od -An -tx1 | tr -d ' \n')" = 7f454c46 ]; then echo "$f"; fi
       done)
WANT=$(printf '%s\n' $LIBCXX $USERS | sort)
if [ "$ELFS" != "$WANT" ]; then
  echo "stage: the staged ELF files are not exactly the expected 7:" >&2; echo "$ELFS" >&2; exit 1
fi
echo "--- PT_LOAD check (readelf -lW)"
for f in $ELFS; do
  echo "$f:"; readelf -lW "$f" | grep -E '^ +LOAD '
done
ptload_gate $ELFS || { echo "stage: PT_LOAD gate failed: bionic cannot load these files" >&2; exit 1; }
echo "PT_LOAD gate: OK ($(echo $ELFS | wc -w) ELF files)"

# IPA tuning: upstream uncalibrated fallback + SB3 starter files.
cp $W/src/libcamera/src/ipa/simple/data/uncalibrated.yaml $O/odm/etc/libcamera/ipa/simple/
cp $CFG/ov5693.yaml $CFG/ov8865.yaml $O/odm/etc/libcamera/ipa/simple/
cp $CFG/camera_hal.yaml $CFG/configuration.yaml $O/odm/etc/libcamera/

chmod 0644 $(find $O -type f -name '*.so' -o -type f -name '*.yaml' -o -type f -name '*.sign')
chmod 0755 $O/odm/bin/libcamera/soft_ipa_proxy $O/odm/bin/lcam-probe

echo "--- NEEDED / SONAME / RUNPATH check"
for f in $ELFS; do
  echo "$f: $(readelf -dW $f | grep -E 'NEEDED|SONAME|RUNPATH|RPATH' | sed 's/.*(\(.*\)).*\[\(.*\)\]/\1=\2/' | tr '\n' ' ')"
done
( cd $O && find . -type f | sort | xargs sha256sum ) > $O.sha256
cat $O.sha256
du -sh $O
