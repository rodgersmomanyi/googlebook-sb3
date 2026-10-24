#!/bin/bash
# Builds GbSb3SensorsOverlay.apk on the builder (Android SDK build-tools 36 + platform 36 in
# /build/native/sensors/tools/sdk). Signed with a locally generated key (preinstalled static RRO:
# any key works; the key never leaves /build/native/sensors/overlay/key).
set -e
SDK=/build/native/sensors/tools/sdk
BT=$SDK/android-16
JAR=$SDK/android-36/android.jar
D=/build/native/sensors/overlay
cd $D
rm -rf out; mkdir -p out key
$BT/aapt2 compile --dir src/res -o out/res.zip
$BT/aapt2 link -o out/unsigned.apk -I $JAR --manifest src/AndroidManifest.xml \
  --min-sdk-version 37 --target-sdk-version 37 --no-auto-version out/res.zip
if [ ! -f key/gb_overlay.jks ]; then
  keytool -genkeypair -keystore key/gb_overlay.jks -storepass gboverlay -keypass gboverlay -alias gb \
    -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=Googlebook SB3 overlay" >/dev/null 2>&1
fi
$BT/zipalign -f -p 4 out/unsigned.apk out/aligned.apk
$BT/apksigner sign --ks key/gb_overlay.jks --ks-pass pass:gboverlay --ks-key-alias gb \
  --min-sdk-version 37 --v2-signing-enabled true --v3-signing-enabled true --out out/GbSb3SensorsOverlay.apk out/aligned.apk
$BT/apksigner verify --min-sdk-version 37 -v out/GbSb3SensorsOverlay.apk | head -5
$BT/aapt2 dump badging out/GbSb3SensorsOverlay.apk | head -6
$BT/aapt2 dump resources out/GbSb3SensorsOverlay.apk
