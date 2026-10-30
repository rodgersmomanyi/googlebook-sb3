#!/bin/bash
# Builds GbSb3SettingsOverlay.apk (readiness A-L3: SettingsProvider def_accelerometer_rotation=true) on the builder,
# like ../overlay/build_overlay.sh: Android SDK build-tools 36 + platform 36 in /build/native/sensors/tools/sdk, signed
# with the sensors overlay's local key (a preinstalled static RRO: any key works; one key for both of our overlays).
# Run from anywhere; it builds in its own directory (src/ -> out/GbSb3SettingsOverlay.apk).
set -e
SDK=${GB_SDK:-/build/native/sensors/tools/sdk}
BT=$SDK/android-16
JAR=$SDK/android-36/android.jar
KS=${GB_OVERLAY_KS:-/build/native/sensors/overlay/key/gb_overlay.jks}
D=$(cd "$(dirname "$0")" && pwd)
[ -f "$KS" ] || { echo "no overlay key $KS (build ../overlay first, or set GB_OVERLAY_KS)"; exit 1; }
cd "$D"
rm -rf out; mkdir -p out
"$BT/aapt2" compile --dir src/res -o out/res.zip
"$BT/aapt2" link -o out/unsigned.apk -I "$JAR" --manifest src/AndroidManifest.xml \
  --min-sdk-version 37 --target-sdk-version 37 --no-auto-version out/res.zip
"$BT/zipalign" -f -p 4 out/unsigned.apk out/aligned.apk
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:gboverlay --ks-key-alias gb \
  --min-sdk-version 37 --v2-signing-enabled true --v3-signing-enabled true --out out/GbSb3SettingsOverlay.apk out/aligned.apk
"$BT/apksigner" verify --min-sdk-version 37 -v out/GbSb3SettingsOverlay.apk | head -5
"$BT/aapt2" dump badging out/GbSb3SettingsOverlay.apk | head -3
"$BT/aapt2" dump resources out/GbSb3SettingsOverlay.apk
sha256sum out/GbSb3SettingsOverlay.apk
