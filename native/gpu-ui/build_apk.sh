#!/bin/bash
# Builds and signs GbGpu.apk on the builder (INTERFACES 13.1, 13.2). No Gradle, no Maven, no AndroidX:
#   aapt2 compile/link -> javac (JDK 17, -source/-target 8, android.jar as the boot class path) -> d8 -> zip
#   -> zipalign -> apksigner (v2 + v3).
# Toolchain: the official Android SDK installed with sdkmanager into $SDK (default: the component's builder scratch
# dir): cmdline-tools;latest (23.0, sha1-checked against dl.google.com's repository2-3.xml), platforms;android-36,
# build-tools;36.1.0. sdk_bootstrap installs them if they are missing; only the android-sdk-license prompt that
# sdkmanager shows for these packages is answered.
# Key: ~/keys/gbgpu.p12 (alias gbgpu, password file ~/keys/gbgpu.pass), generated once if missing, never
#   regenerated; never inside a repository, a component directory, /build/dist* or an image. Back it up to
#   C:\GB-build\keys\ (same names).
# versionCode = minutes since the epoch (UTC); it must grow with every build, so the script waits for the next
#   minute when the previous build (out/BUILD_INFO.txt) used the current one.
# Output: out/GbGpu.apk, out/GbGpu.apk.sha256, out/BUILD_INFO.txt, plus copies of gb_gpu_ui.rc and
#   gb_gpu_ui_setup.sh (INTERFACES 1.3). Intermediates (kept for lint): build/.
# Usage: ./build_apk.sh            (run from anywhere; heavy steps at nice 19 / ionice idle)
set -euo pipefail
G=$(cd "$(dirname "$0")" && pwd)
SCRATCH=${SCRATCH:-/build/native/dgpu/gpu-ui}
SDK=${SDK:-$SCRATCH/sdk}
BT_VER=36.1.0
PLATFORM=android-36
API=36
CMDLINE_ZIP=commandlinetools-linux-16111833_latest.zip
CMDLINE_SHA1=e025545c62a8e64c7559119566a569fb1dec5f60
JAVA_HOME=${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}
export JAVA_HOME PATH="$JAVA_HOME/bin:$PATH"
OUT=${OUT:-$G/out}
B=$G/build
K=$HOME/keys
NICE="nice -n 19 ionice -c3"

sdk_bootstrap() {
  if [ -x "$SDK/build-tools/$BT_VER/aapt2" ] && [ -f "$SDK/platforms/$PLATFORM/android.jar" ] &&
     [ -x "$SDK/cmdline-tools/latest/bin/lint" ]; then
    return 0
  fi
  echo "== SDK bootstrap into $SDK"
  mkdir -p "$SDK/cmdline-tools" "$SCRATCH/dl"
  if [ ! -x "$SDK/cmdline-tools/latest/bin/sdkmanager" ]; then
    z=$SCRATCH/dl/$CMDLINE_ZIP
    [ -f "$z" ] || $NICE curl -fsS -o "$z" "https://dl.google.com/android/repository/$CMDLINE_ZIP"
    echo "$CMDLINE_SHA1  $z" | sha1sum -c -
    rm -rf "$SDK/cmdline-tools/latest" "$SDK/cmdline-tools/unpack"
    mkdir "$SDK/cmdline-tools/unpack"
    $NICE unzip -q "$z" -d "$SDK/cmdline-tools/unpack"
    mv "$SDK/cmdline-tools/unpack/cmdline-tools" "$SDK/cmdline-tools/latest"
    rmdir "$SDK/cmdline-tools/unpack"
  fi
  # sdkmanager asks once per licence; these two packages need only android-sdk-license.
  printf 'y\ny\ny\n' | $NICE "$SDK/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$SDK" \
    --install "platforms;$PLATFORM" "build-tools;$BT_VER" > "$SCRATCH/sdkmanager.log" 2>&1
  ls "$SDK/licenses"
}

key_once() {
  mkdir -p "$K"
  chmod 700 "$K"
  if [ ! -f "$K/gbgpu.p12" ]; then
    (
      umask 077
      [ -f "$K/gbgpu.pass" ] || head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$K/gbgpu.pass"
      keytool -genkeypair -storetype PKCS12 -keystore "$K/gbgpu.p12" -alias gbgpu -keyalg RSA -keysize 3072 \
        -validity 10000 -dname "CN=GbGpu, O=googlebook-sb3" -storepass:file "$K/gbgpu.pass" > /dev/null 2>&1
    )
    chmod 600 "$K/gbgpu.p12" "$K/gbgpu.pass"
    echo "KEY: generated $K/gbgpu.p12 (alias gbgpu); back it up to C:\\GB-build\\keys\\"
  fi
  [ -f "$K/gbgpu.pass" ] || { echo "missing $K/gbgpu.pass for the existing key store"; exit 1; }
}

sdk_bootstrap
key_once
BTD=$SDK/build-tools/$BT_VER
JAR=$SDK/platforms/$PLATFORM/android.jar

# versionCode: epoch minutes, strictly greater than the last build's
LAST=$(sed -n 's/^versionCode=//p' "$OUT/BUILD_INFO.txt" 2> /dev/null || true)
LAST=${LAST:-0}
VC=$(( $(date -u +%s) / 60 ))
if [ "$LAST" -gt $((VC + 2)) ]; then echo "last versionCode $LAST is in the future (clock?)"; exit 1; fi
while [ "$VC" -le "$LAST" ]; do sleep 5; VC=$(( $(date -u +%s) / 60 )); done
VN=1.0.$VC

rm -rf "$B"
mkdir -p "$B/gen" "$B/classes" "$B/dex" "$OUT"
echo "== aapt2 compile/link (versionCode $VC)"
$NICE "$BTD/aapt2" compile --dir "$G/res" -o "$B/res.zip"
$NICE "$BTD/aapt2" link -o "$B/base.apk" -I "$JAR" --manifest "$G/AndroidManifest.xml" --java "$B/gen" \
  --min-sdk-version $API --target-sdk-version $API --version-code "$VC" --version-name "$VN" "$B/res.zip"

echo "== javac"
find "$G/src" "$B/gen" -name '*.java' | LC_ALL=C sort > "$B/sources.txt"
$NICE javac -source 8 -target 8 -bootclasspath "$JAR" -encoding UTF-8 -Xlint:all -Xlint:-options -Werror \
  -d "$B/classes" @"$B/sources.txt"

echo "== d8"
find "$B/classes" -name '*.class' | LC_ALL=C sort > "$B/classes.txt"
$NICE "$BTD/d8" --release --min-api $API --lib "$JAR" --output "$B/dex" @"$B/classes.txt"

echo "== package, align, sign"
cp "$B/base.apk" "$B/unsigned.apk"
(cd "$B/dex" && TZ=UTC touch -t 198001010000 classes.dex && zip -q -X -D "$B/unsigned.apk" classes.dex)
"$BTD/zipalign" -f -p 4 "$B/unsigned.apk" "$B/aligned.apk"
# --min-sdk-version 24 only steers the signer: with the manifest's 36 apksigner would write v3 alone, and
# INTERFACES 13.1 asks for v2 + v3. The APK itself still says minSdkVersion 36; v1 stays off.
"$BTD/apksigner" sign --ks "$K/gbgpu.p12" --ks-key-alias gbgpu --ks-pass "file:$K/gbgpu.pass" \
  --v1-signing-enabled false --v2-signing-enabled true --v3-signing-enabled true --min-sdk-version 24 \
  --out "$OUT/GbGpu.apk" "$B/aligned.apk"
rm -f "$OUT/GbGpu.apk.idsig"
"$BTD/zipalign" -c -p 4 "$OUT/GbGpu.apk"
"$BTD/apksigner" verify --min-sdk-version $API "$OUT/GbGpu.apk"

cp "$G/gb_gpu_ui.rc" "$G/gb_gpu_ui_setup.sh" "$OUT/"
chmod 0755 "$OUT/gb_gpu_ui_setup.sh"
(cd "$OUT" && sha256sum GbGpu.apk > GbGpu.apk.sha256)
SRC_SHA=$(cd "$G" && find AndroidManifest.xml res src -type f | LC_ALL=C sort | xargs sha256sum | sha256sum | cut -d' ' -f1)
CERT=$("$BTD/apksigner" verify --print-certs "$OUT/GbGpu.apk" | sed -n 's/^Signer #1 certificate SHA-256 digest: //p')
{
  echo "versionCode=$VC"
  echo "versionName=$VN"
  echo "apk_sha256=$(cut -d' ' -f1 "$OUT/GbGpu.apk.sha256")"
  echo "apk_bytes=$(stat -c %s "$OUT/GbGpu.apk")"
  echo "signer_cert_sha256=$CERT"
  echo "sources_sha256=$SRC_SHA"
  echo "rc_sha256=$(sha256sum "$OUT/gb_gpu_ui.rc" | cut -d' ' -f1)"
  echo "setup_sh_sha256=$(sha256sum "$OUT/gb_gpu_ui_setup.sh" | cut -d' ' -f1)"
  echo "aapt2=$("$BTD/aapt2" version 2>&1 | head -n 1)"
  echo "d8=$("$BTD/d8" --version 2>&1 | head -n 1)"
  echo "apksigner=$("$BTD/apksigner" --version)"
  echo "javac=$(javac -version 2>&1)"
  echo "platform=$PLATFORM build-tools=$BT_VER"
  echo "built_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OUT/BUILD_INFO.txt"
cat "$OUT/BUILD_INFO.txt"
