#!/bin/bash
# Stage the Android (lapis, x86_64) camera HAL tree for the SB3 native boot.
# Output: $W/out/odm/... (to be merged into the gbinit sb3/native /odm overlay tree).
#  - libc++_shared.so from NDK r27c is shipped as libc++_lcam.so (soname + NEEDED rewritten) so it
#    cannot clash with the vendor's own libc++_shared.so (OpenVINO) in the same process.
#  - RUNPATHs are removed (Android linker namespaces resolve /odm/lib64).
#  - The IPA module is re-signed after patchelf (libcamera verifies the signature to run the IPA
#    in-process; an unsigned/changed module would run isolated through /odm/bin/libcamera/).
set -euo pipefail
W=/build/native/camera-userspace
B=$W/lc-build
NDKBIN=/build/ndk/toolchains/llvm/prebuilt/linux-x86_64/bin
NDKLIB=/build/ndk/toolchains/llvm/prebuilt/linux-x86_64/sysroot/usr/lib/x86_64-linux-android
O=$W/out
CFG=$W/config
ninja -C $B -j${J:-2}
# lcam-probe: bring-up tool (tools/lcam_probe.cpp) linked against the build's libcamera
$NDKBIN/x86_64-linux-android34-clang++ -std=c++20 -O2 -I$W/src/libcamera/include -I$B/include \
  -o $W/lcam-probe $W/tools/lcam_probe.cpp -L$B/src/libcamera -L$B/src/libcamera/base \
  -lcamera -lcamera-base
rm -rf $O && mkdir -p $O/odm/lib64/hw $O/odm/lib64/libcamera/ipa $O/odm/bin/libcamera \
  $O/odm/etc/libcamera/ipa/simple $O/odm/etc/permissions

cp $B/src/libcamera/base/libcamera-base.so $O/odm/lib64/
cp $B/src/libcamera/libcamera.so $O/odm/lib64/
cp $B/src/android/libcamera-hal.so $O/odm/lib64/hw/camera.sb3.so
cp $B/src/ipa/simple/ipa_soft_simple.so $O/odm/lib64/libcamera/ipa/
cp $B/src/libcamera/proxy/worker/soft_ipa_proxy $O/odm/bin/libcamera/
cp $NDKLIB/libc++_shared.so $O/odm/lib64/libc++_lcam.so
[ -x $W/lcam-probe ] && cp $W/lcam-probe $O/odm/bin/lcam-probe

patchelf --set-soname libc++_lcam.so $O/odm/lib64/libc++_lcam.so
patchelf --set-soname camera.sb3.so $O/odm/lib64/hw/camera.sb3.so
for f in $O/odm/lib64/libcamera-base.so $O/odm/lib64/libcamera.so $O/odm/lib64/hw/camera.sb3.so \
         $O/odm/lib64/libcamera/ipa/ipa_soft_simple.so $O/odm/bin/libcamera/soft_ipa_proxy \
         $( [ -f $O/odm/bin/lcam-probe ] && echo $O/odm/bin/lcam-probe ); do
  patchelf --replace-needed libc++_shared.so libc++_lcam.so "$f"
  patchelf --remove-rpath "$f"
  $NDKBIN/llvm-strip --strip-unneeded "$f"
done
$NDKBIN/llvm-strip --strip-unneeded $O/odm/lib64/libc++_lcam.so

# Re-sign the (modified) IPA module with the build's private key.
$W/src/libcamera/src/ipa/ipa-sign.sh $B/src/ipa-priv-key.pem \
  $O/odm/lib64/libcamera/ipa/ipa_soft_simple.so $O/odm/lib64/libcamera/ipa/ipa_soft_simple.so.sign

# IPA tuning: upstream uncalibrated fallback + SB3 starter files.
cp $W/src/libcamera/src/ipa/simple/data/uncalibrated.yaml $O/odm/etc/libcamera/ipa/simple/
cp $CFG/ov5693.yaml $CFG/ov8865.yaml $O/odm/etc/libcamera/ipa/simple/
cp $CFG/camera_hal.yaml $CFG/configuration.yaml $O/odm/etc/libcamera/

chmod 0644 $(find $O -type f -name '*.so' -o -type f -name '*.yaml' -o -type f -name '*.sign')
chmod 0755 $O/odm/bin/libcamera/soft_ipa_proxy $( [ -f $O/odm/bin/lcam-probe ] && echo $O/odm/bin/lcam-probe )

echo "--- NEEDED check"
for f in $(find $O -type f \( -name '*.so' -o -perm -u+x \) ! -name '*.sign'); do
  echo "$f: $(readelf -d $f 2>/dev/null | grep NEEDED | sed 's/.*\[\(.*\)\]/\1/' | tr '\n' ' ')"
done
( cd $O && find . -type f | sort | xargs sha256sum ) > $W/out.sha256
cat $W/out.sha256
du -sh $O
