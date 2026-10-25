#!/bin/bash
# GD-03 / GD-1B-11: static checks of GbGfxDriver.apk (builder): aapt2 badging + manifest tree (v1.2 label
# "NVIDIA app graphics"), zipalign -c -P 16, apksigner verify (v2 + v3, min SDK 36), STORED + 16 KiB-aligned
# libraries, NO assets/allowlist.txt (INTERFACES v1.2 24.5), ELF checks of the bundled libraries.
# The checks are handed to chk() as single-quoted strings and eval'd there, so their variables expand at check time.
# shellcheck disable=SC2016,SC2034
set -uo pipefail
G=${G:-$(cd "$(dirname "$0")/.." && pwd)}
SDK=${SDK:-/build/native/sensors/tools/sdk}
BT=$SDK/android-16
APK=${1:-$G/out/GbGfxDriver.apk}
export JAVA_HOME=${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}
export PATH=$JAVA_HOME/bin:$PATH
RE=/build/ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf
fail=0
chk() { if eval "$2"; then echo "  ok   $1"; else echo "  FAIL $1"; fail=1; fi; }
B=$("$BT/aapt2" dump badging "$APK"); echo "$B" | head -5
X=$("$BT/aapt2" dump xmltree --file AndroidManifest.xml "$APK")
chk "package gb.sb3.gfxdriver" 'echo "$B" | grep -q "package: name=.gb.sb3.gfxdriver."'
chk "minSdk 36 / targetSdk 36" 'echo "$B" | grep -q "^minSdkVersion:.36." && echo "$B" | grep -q "^targetSdkVersion:.36."'
chk "native-code x86_64" 'echo "$B" | grep -q "native-code: .x86_64."'
chk "label 'NVIDIA app graphics' (v1.2)" 'echo "$B" | grep -q "^application: label=.NVIDIA app graphics. " && echo "$X" | grep -q "android:label(0x01010001)=\"NVIDIA app graphics\""'
Z=$(unzip -Z1 "$APK")
chk "no assets/allowlist.txt (no member named allowlist.txt at all)" '! echo "$Z" | grep -q "\(^\|/\)allowlist\.txt$"'
chk "assets/sphal_libraries.txt present" 'echo "$Z" | grep -qx "assets/sphal_libraries.txt"'
chk "only the expected members" '[ "$(echo "$Z" | grep -v "^META-INF/" | LC_ALL=C sort | tr "\n" " ")" = "AndroidManifest.xml assets/sphal_libraries.txt lib/x86_64/libgb_nvk.so lib/x86_64/vulkan.intel.so resources.arsc " ]'
chk "hasCode=false" 'echo "$X" | grep -q "android:hasCode(0x0101000c)=false"'
chk "extractNativeLibs=false" 'echo "$X" | grep -q "android:extractNativeLibs(0x010104ea)=false"'
chk "meta-data com.android.graphics.driver.build_time = L<digits>" \
  'echo "$X" | grep -A2 "com.android.graphics.driver.build_time" | grep -Eq "android:value\(0x01010024\)=\"L[0-9]+\""'
chk "versionName 1.0.<versionCode>" 'vc=$(echo "$B" | sed -n "s/.*versionCode=.\([0-9]*\).*/\1/p"); echo "$B" | grep -q "versionName=.1.0.$vc."'
chk "no permissions requested" '! echo "$B" | grep -q "uses-permission"'
chk "zipalign -c -P 16 4" '"$BT/zipalign" -c -P 16 4 "$APK"'
# At the real min SDK (36) apksigner verifies only the newest scheme present (v3); with --min-sdk-version 24 it also
# verifies the v2 block, which INTERFACES 13.1 wants present.
V=$("$BT/apksigner" verify -v --min-sdk-version 36 "$APK" 2>&1); echo "$V" | grep -E "Verifies|scheme" | head -6
V24=$("$BT/apksigner" verify -v --min-sdk-version 24 "$APK" 2>&1)
chk "apksigner verify --min-sdk-version 36: verifies, v3" 'echo "$V" | grep -q "^Verifies" && echo "$V" | grep -q "Verified using v3 scheme (APK Signature Scheme v3): true"'
chk "apksigner verify --min-sdk-version 24: v2 block present and valid, v1 absent" 'echo "$V24" | grep -q "Verified using v2 scheme (APK Signature Scheme v2): true" && echo "$V24" | grep -q "Verified using v1 scheme (JAR signing): false"'
echo "$V" | grep -q "WARNING" && echo "$V" | grep "WARNING" | head -3
python3 -I "$G/apk/apk_tool.py" check "$APK" || fail=1
T=$(mktemp -d); unzip -q -o "$APK" 'lib/*' -d "$T"
for so in "$T"/lib/x86_64/*.so; do
  n=$(basename "$so")
  sn=$("$RE" -d "$so" | sed -n 's/.*SONAME.*\[\(.*\)\]/\1/p')
  chk "$n SONAME $sn" '[ "$sn" = "$n" ]'
  echo "     NEEDED: $("$RE" -d "$so" | sed -n 's/.*NEEDED.*\[\(.*\)\]/\1/p' | tr '\n' ' ')"
done
rm -rf "$T"
echo "RESULT: $([ $fail = 0 ] && echo PASS || echo FAIL)"
exit $fail
