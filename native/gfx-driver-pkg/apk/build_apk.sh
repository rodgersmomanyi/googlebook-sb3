#!/bin/bash
# Builds and signs GbGfxDriver.apk (builder). No Gradle/Maven: aapt2 link + Python zip (STORED libs) + zipalign -P 16
# + apksigner (v2+v3), with the official SDK pieces already on the builder (read only, INTERFACES 1.4):
#   build-tools 36.0.0 (aapt2, zipalign, apksigner) and platforms android-36 (android.jar).
# Inputs: $LIBDIR/vulkan.intel.so (shim, out/android) and $LIBDIR/libgb_nvk.so (NVK 8a build).
# v1.2: label "NVIDIA app graphics"; shipped on every native image (ro.gfx.driver.0 from the native build.prop).
# Key: ~/keys/gbgfxdriver.p12 (alias gbgfxdriver, password file ~/keys/gbgfxdriver.pass), generated once if missing,
#      never regenerated; never copied into a repository, component directory, /build/dist* or an image.
# Signing passes --min-sdk-version 24 only so that apksigner writes the v2 block next to v3 (INTERFACES 13.1 asks for
# v2 + v3; with a min SDK >= 28 apksigner leaves v2 out). The manifest's minSdkVersion stays 36; v1 stays off.
# Assets: exactly assets/sphal_libraries.txt (empty). NEVER assets/allowlist.txt (INTERFACES v1.2 24.5): GpuService
# copies that asset into updatable_driver_production_allowlist, and which apps get the driver is decided only by the
# Settings that gb_power writes (24.1). The build fails if one appears (the assets dir is created empty here, and
# apk_tool.py add-libs refuses an input APK that has one); check_apk.sh checks the result again (GD-1B-11).
# Output: $OUT/GbGfxDriver.apk (+ GbGfxDriver.apk.sha256)
set -euo pipefail
G=${G:-$(cd "$(dirname "$0")/.." && pwd)}
SDK=${SDK:-/build/native/sensors/tools/sdk}
BT=$SDK/android-16          # build-tools 36.0.0 (directory name as installed there)
JAR=$SDK/android-36/android.jar
LIBDIR=${LIBDIR:-$G/out/apk-libs}
OUT=${OUT:-$G/out}
JAVA_HOME=${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}
export JAVA_HOME PATH=$JAVA_HOME/bin:$PATH
K=$HOME/keys
W=$(mktemp -d "$G/out/apk-work.XXXXXX")
trap 'rm -rf "$W"' EXIT

for f in vulkan.intel.so libgb_nvk.so; do [ -f "$LIBDIR/$f" ] || { echo "missing $LIBDIR/$f"; exit 1; }; done
grep -q 'Pkg.Revision=36' "$BT/source.properties" && grep -q 'AndroidVersion.ApiLevel=36' "$SDK/android-36/source.properties"

# signing key (once)
mkdir -p "$K"; chmod 700 "$K"
if [ ! -f "$K/gbgfxdriver.p12" ]; then
  umask 077
  [ -f "$K/gbgfxdriver.pass" ] || head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$K/gbgfxdriver.pass"
  keytool -genkeypair -storetype PKCS12 -keystore "$K/gbgfxdriver.p12" -alias gbgfxdriver -keyalg RSA -keysize 3072 \
    -validity 10000 -dname "CN=GbGfxDriver, O=googlebook-sb3" -storepass:file "$K/gbgfxdriver.pass" >/dev/null 2>&1
  chmod 600 "$K/gbgfxdriver.p12" "$K/gbgfxdriver.pass"
  echo "generated a new signing key in $K (back it up to C:\\GB-build\\keys)"
fi

# versionCode = minutes since the epoch (UTC), build_time = seconds (INTERFACES 13.1 / 13.3)
NOW=${SOURCE_DATE_EPOCH:-$(date -u +%s)}
VC=$((NOW / 60))
sed -e "s/@VERSION_CODE@/$VC/g" -e "s/@BUILD_TIME@/$NOW/g" "$G/apk/AndroidManifest.xml.in" > "$W/AndroidManifest.xml"
mkdir -p "$W/assets"
: > "$W/assets/sphal_libraries.txt"   # the shim needs no extra sphal libraries (libhardware/libz come via GraphicsEnv)
# the asset directory holds exactly that one file (no allowlist.txt, INTERFACES 24.5)
[ "$(ls -A "$W/assets")" = sphal_libraries.txt ] || { echo "unexpected assets: $(ls -A "$W/assets")"; exit 1; }
"$BT/aapt2" link -o "$W/base.apk" -I "$JAR" --manifest "$W/AndroidManifest.xml" -A "$W/assets" \
  --min-sdk-version 36 --target-sdk-version 36 --version-code "$VC" --version-name "1.0.$VC"
python3 -I "$G/apk/apk_tool.py" add-libs "$W/base.apk" "$W/libs.apk" "$LIBDIR"
"$BT/zipalign" -f -P 16 4 "$W/libs.apk" "$W/aligned.apk"
"$BT/apksigner" sign --ks "$K/gbgfxdriver.p12" --ks-key-alias gbgfxdriver --ks-pass "file:$K/gbgfxdriver.pass" \
  --v1-signing-enabled false --v2-signing-enabled true --v3-signing-enabled true --min-sdk-version 24 \
  --alignment-preserved --out "$OUT/GbGfxDriver.apk" "$W/aligned.apk"
if unzip -Z1 "$OUT/GbGfxDriver.apk" | grep -q '\(^\|/\)allowlist\.txt$'; then
  echo "FAIL: allowlist.txt in $OUT/GbGfxDriver.apk (INTERFACES 24.5)"; rm -f "$OUT/GbGfxDriver.apk"; exit 1
fi
( cd "$OUT" && sha256sum GbGfxDriver.apk > GbGfxDriver.apk.sha256 )
echo "versionCode=$VC build_time=L$NOW"
cat "$OUT/GbGfxDriver.apk.sha256"
