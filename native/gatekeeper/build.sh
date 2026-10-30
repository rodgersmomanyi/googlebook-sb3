#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# build.sh -- build and test gb_gatekeeper (software Gatekeeper HAL) on the builder.
#
#   sh build.sh           1. BoringSSL libcrypto.a for the host and for Android (once, cached)
#                         2. host unit tests (ASan + UBSan) and the independent Python check;
#                            the frozen on-disk vectors (SA-6: tests/test_frozen_v1.cpp + frozen_check.py,
#                            the gk_key.v1 files and handles 0.2.0 enrolled must still verify; never regenerate);
#                            the tests also run the service binary itself, linked with a fake
#                            service manager (tests/fake_sm.cpp), as a child process
#                            2b. optional (REGRESS_OLD=dir): the same tests against older
#                            service sources, which must FAIL (tests/regress_old.sh)
#                         3. android.hardware.gatekeeper-service.gb for x86_64 Android (NDK) + ELF checks
#                         4. the unit tests built for Android (run on lapis bionic, see below)
#   OUT=dir sh build.sh   output directory (default ./out)
#   REGRESS_OLD=tests/regress/pre_w4_src sh build.sh
#                         also proves that the W4 tests fail on the reviewed pre-W4 sources
#   COMPARE_WITH=other/out/android/SHA256SUMS sh build.sh
#                         fails unless the binaries are byte-identical to another build's
#
# Inputs: this directory; the NDK ($NDK, default /build/ndk, r27c); AOSP platform/external/boringssl
# at $BSSL_COMMIT ($BSSL_SRC, cloned from android.googlesource.com if missing). Optional:
#   REF_LIBBINDER  a device libbinder_ndk.so to check every imported binder symbol + version against
#   LAPIS_RT       extracted lapis com.android.runtime APEX payload (bin/linker64, lib64/bionic)
#   LAPIS_SYSLIB   extracted lapis /system/lib64
#                  with both, step 4 runs the Android unit tests and the service binary's
#                  --version/--selftest on the builder through lapis's own bionic linker
# Runs itself under nice 19 / ionice idle (the builder is shared with kernel builds).
set -eu
if [ -z "${GBGK_NICED:-}" ]; then
  GBGK_NICED=1 exec nice -n 19 ionice -c3 sh "$0" "$@"
fi

HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-$HERE/out}
NDK=${NDK:-/build/ndk}
BSSL_SRC=${BSSL_SRC:-$HOME/gk/src_dl/boringssl}
BSSL_COMMIT=0cd1f6a94b670e6b61af95a06e71e690ae8e3262   # android17-release, 2026-03-13
BSSL_URL=https://android.googlesource.com/platform/external/boringssl
API=34
JOBS=${JOBS:-2}
VERSION=${VERSION:-0.2.0}   # 0.2.0: W4-GK-1..3 (GATEKEEPER.md 7.8)
BIN=android.hardware.gatekeeper-service.gb

TC=$NDK/toolchains/llvm/prebuilt/linux-x86_64
CXX_A="$TC/bin/clang++ --target=x86_64-linux-android$API"
CC_A="$TC/bin/clang --target=x86_64-linux-android$API"
READELF=$TC/bin/llvm-readelf
STRIP=$TC/bin/llvm-strip

step() { echo "== $*"; }
fail() { echo "BUILD FAILED: $*" >&2; exit 1; }

# ---- 1. BoringSSL ---------------------------------------------------------------------------
if [ ! -d "$BSSL_SRC/.git" ]; then
  step "clone BoringSSL ($BSSL_URL)"
  mkdir -p "$(dirname "$BSSL_SRC")"
  git clone -q --depth 1 -b android17-release "$BSSL_URL" "$BSSL_SRC"
fi
if [ "$(git -C "$BSSL_SRC" rev-parse HEAD)" != "$BSSL_COMMIT" ]; then
  git -C "$BSSL_SRC" fetch -q --depth 1 origin "$BSSL_COMMIT"
  git -C "$BSSL_SRC" checkout -q "$BSSL_COMMIT"
fi
[ "$(git -C "$BSSL_SRC" rev-parse HEAD)" = "$BSSL_COMMIT" ] || fail "BoringSSL is not at $BSSL_COMMIT"

BSSL_LIB=
build_bssl() {  # name cmake-args...   -> $BSSL_LIB
  name=$1; shift
  d=$OUT/bssl-$name
  # The NDK's CMake toolchain adds -g, so DWARF would record the build directory (DW_AT_comp_dir)
  # and lld's --build-id (a hash of the whole unstripped file) would depend on $OUT. Map it to a
  # fixed name: the binary is then the same for any OUT.
  pm="-fdebug-prefix-map=$d=bssl-$name"
  stamp="v3 $pm $*"   # the cache is reused only if it was configured exactly like this
  if [ ! -f "$d/.done" ] || [ "$(cat "$d/.done")" != "$stamp" ]; then
    step "BoringSSL libcrypto ($name)"
    rm -rf "$d"; mkdir -p "$d"
    # -DCMAKE_C_FLAGS: the NDK's default (legacy) toolchain file appends this cached value after
    # its own ANDROID_COMPILER_FLAGS (-g -DANDROID ... -fstack-protector-strong ...), and the host
    # build has no toolchain flags to lose. (CFLAGS in the environment is ignored by the NDK file.)
    cmake -S "$BSSL_SRC/src" -B "$d" -G Ninja -DCMAKE_BUILD_TYPE=Release -DOPENSSL_NO_ASM=1 -DBUILD_TESTING=OFF \
      -DCMAKE_C_FLAGS="$pm" -DCMAKE_CXX_FLAGS="$pm" "$@" \
      > "$d/cmake.log" 2>&1 || { tail -20 "$d/cmake.log"; fail "cmake ($name)"; }
    ninja -C "$d" -j "$JOBS" crypto > "$d/ninja.log" 2>&1 || { tail -20 "$d/ninja.log"; fail "ninja ($name)"; }
    printf '%s\n' "$stamp" > "$d/.done"
  fi
  BSSL_LIB=$(find "$d" -name libcrypto.a | head -1)
  [ -n "$BSSL_LIB" ] || fail "libcrypto.a ($name) not found"
}
build_bssl host -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++ -DCMAKE_POSITION_INDEPENDENT_CODE=ON
LIBCRYPTO_HOST=$BSSL_LIB
build_bssl android -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=x86_64 -DANDROID_PLATFORM=android-$API
LIBCRYPTO_ANDROID=$BSSL_LIB

INC="-I$HERE/src -I$HERE/third_party/aosp/system_gatekeeper/include \
     -I$HERE/third_party/aosp/libhardware/include -I$BSSL_SRC/src/include"
OURS="src/gb_gatekeeper.cpp src/gk_store.cpp src/shared_secret.cpp src/aidl_service.cpp src/gk_log.cpp"
AOSP="third_party/aosp/system_gatekeeper/gatekeeper.cpp third_party/aosp/system_gatekeeper/gatekeeper_messages.cpp"
cd "$HERE"

# ---- 2. host tests --------------------------------------------------------------------------
step "host unit tests (ASan, UBSan)"
H=$OUT/host; rm -rf "$H"; mkdir -p "$H/obj" "$H/tmp"
SAN="-fsanitize=address,undefined -fno-sanitize-recover=undefined -fno-omit-frame-pointer"
HOBJ=""   # the service code shared by the test binary and the fake-servicemanager service
for f in $OURS tests/fake_binder.cpp; do
  o=$H/obj/$(basename "$f" .cpp).o
  g++ -std=c++17 -O1 -g $SAN -Wall -Wextra -Werror $INC -c "$f" -o "$o"
  HOBJ="$HOBJ $o"
done
for f in $AOSP; do
  o=$H/obj/aosp_$(basename "$f" .cpp).o
  g++ -std=c++17 -O1 -g $SAN -Wall -Werror $INC -c "$f" -o "$o"
  HOBJ="$HOBJ $o"
done
for f in tests/test_main.cpp tests/fake_sm.cpp src/service_main.cpp; do
  g++ -std=c++17 -O1 -g $SAN -Wall -Wextra -Werror -DGBGK_VERSION=\"$VERSION\" $INC \
    -c "$f" -o "$H/obj/$(basename "$f" .cpp).o"
done
g++ $SAN $HOBJ "$H/obj/test_main.o" "$LIBCRYPTO_HOST" -lpthread -o "$H/gk_host_tests"
g++ $SAN $HOBJ "$H/obj/service_main.o" "$H/obj/fake_sm.o" "$LIBCRYPTO_HOST" -lpthread \
  -o "$H/gk_host_service"
"$H/gk_host_tests" "$H/vectors.json" "$H/tmp" "$H/gk_host_service" 2>&1 | tee "$H/test.log"
grep -q "GK_HOST_TESTS: [0-9]* passed, 0 failed" "$H/test.log" || fail "host unit tests"
python3 -I tests/ref_check.py "$H/vectors.json" | tee "$H/ref_check.log"
grep -q "GK_REF_CHECK: PASS" "$H/ref_check.log" || fail "reference check"
# SA-6: the frozen on-disk vectors through the production verify path (a scheme change would lock enrolled users out)
g++ -std=c++17 -O1 -g $SAN -Wall -Wextra -Werror $INC -c tests/test_frozen_v1.cpp -o "$H/obj/test_frozen_v1.o"
g++ $SAN $HOBJ "$H/obj/test_frozen_v1.o" "$LIBCRYPTO_HOST" -lpthread -o "$H/gk_frozen_tests"
mkdir -p "$H/frozen_tmp"
"$H/gk_frozen_tests" "$H/frozen_tmp" 2>&1 | tee "$H/frozen.log"
grep -q "GK_FROZEN_V1: PASS" "$H/frozen.log" || fail "frozen on-disk vectors (SA-6): enrolled PINs would stop verifying"
python3 -I tests/frozen_check.py tests/frozen_v1.json tests/test_frozen_v1.cpp | tee "$H/frozen_ref.log"
grep -q "GK_FROZEN_REF: PASS" "$H/frozen_ref.log" || fail "frozen vector reference check (SA-6)"

# ---- 2b. the same tests against older sources (optional): they must fail there ---------------
if [ -n "${REGRESS_OLD:-}" ]; then
  step "regression proof: current tests against $REGRESS_OLD"
  sh tests/regress_old.sh "$REGRESS_OLD" "$OUT/regress" "$LIBCRYPTO_HOST" "$BSSL_SRC/src/include" \
    || fail "the current tests do not fail on $REGRESS_OLD"
fi

# ---- 3. Android binary ----------------------------------------------------------------------
step "Android x86_64 binary (API $API)"
A=$OUT/android; rm -rf "$A"; mkdir -p "$A/obj" "$A/stub" "$A/licenses"
CF="-std=c++17 -O2 -fPIE -fstack-protector-strong -D_FORTIFY_SOURCE=2 -fno-exceptions -fno-rtti \
    -ffunction-sections -fdata-sections -DGBGK_VERSION=\"$VERSION\""
$CXX_A -std=c++17 -fsyntax-only -Wall -Werror -Wno-nullability-completeness \
  -I"$HERE/third_party/aosp/libbinder_ndk_platform" -I"$HERE/src" src/abi_check.cpp \
  || fail "binder_api.h does not match the NDK/platform headers"
AOBJ=""
for f in $OURS src/service_main.cpp; do
  o=$A/obj/$(basename "$f" .cpp).o
  $CXX_A $CF -Wall -Wextra -Werror $INC -c "$f" -o "$o"
  AOBJ="$AOBJ $o"
done
for f in $AOSP; do
  o=$A/obj/aosp_$(basename "$f" .cpp).o
  $CXX_A $CF -Wall -Werror $INC -c "$f" -o "$o"
  AOBJ="$AOBJ $o"
done
$CC_A -shared -fPIC -nostdlib -Wl,-soname,libbinder_ndk.so \
  -Wl,--version-script="$HERE/tools/libbinder_ndk_stub.map" tools/libbinder_ndk_stub.c \
  -o "$A/stub/libbinder_ndk.so"
$CXX_A -pie -static-libstdc++ -Wl,-z,relro -Wl,-z,now -Wl,-z,noexecstack -Wl,--gc-sections \
  -Wl,--no-undefined -Wl,--build-id=sha1 $AOBJ "$LIBCRYPTO_ANDROID" -L"$A/stub" -lbinder_ndk -llog \
  -o "$A/$BIN.unstripped"
$STRIP --strip-all "$A/$BIN.unstripped" -o "$A/$BIN"

step "ELF checks"
$READELF -d "$A/$BIN" > "$A/dynamic.txt"
$READELF -V "$A/$BIN" > "$A/versions.txt"
$READELF -l "$A/$BIN" > "$A/program_headers.txt"
needed=$(grep NEEDED "$A/dynamic.txt" | sed 's/.*\[\(.*\)\]/\1/' | sort | tr '\n' ' ')
echo "NEEDED: $needed"
for n in $needed; do
  case $n in
    libbinder_ndk.so|liblog.so|libc.so|libm.so|libdl.so) ;;
    *) fail "unexpected NEEDED $n (only LLNDK libraries are allowed)";;
  esac
done
grep -q "BIND_NOW" "$A/dynamic.txt" || fail "not BIND_NOW"
grep -Eq "FLAGS_1\) +NOW PIE" "$A/dynamic.txt" || fail "not PIE"
! grep -q TEXTREL "$A/dynamic.txt" || fail "TEXTREL"
grep -q "/system/bin/linker64" "$A/program_headers.txt" || fail "interpreter is not /system/bin/linker64"
for v in LIBBINDER_NDK LIBBINDER_NDK30 LIBBINDER_NDK31; do
  grep -Eq "Name: $v( |\$)" "$A/versions.txt" || fail "no version need $v on libbinder_ndk.so"
done
$READELF --dyn-syms -W "$A/$BIN" | awk '$7 == "UND" && $8 != "" {print $8}' | sort > "$A/imports.txt"
if [ -n "${REF_LIBBINDER:-}" ]; then
  $READELF --dyn-syms -W "$REF_LIBBINDER" | awk '$7 != "UND" {print $8}' | sed 's/@@/@/' | sort > "$A/ref_exports.txt"
  for s in $(grep '@LIBBINDER_NDK' "$A/imports.txt"); do
    grep -qx "$s" "$A/ref_exports.txt" || fail "$s is not exported by $REF_LIBBINDER"
  done
  echo "all $(grep -c '@LIBBINDER_NDK' "$A/imports.txt") binder imports resolve in $REF_LIBBINDER"
fi

# ---- 4. the same unit tests built for Android, run on lapis's own bionic ---------------------
# With LAPIS_RT (the extracted com.android.runtime APEX payload: bin/linker64, lib64/bionic) and
# LAPIS_SYSLIB (lapis /system/lib64), the Android test binary and the real service binary
# (--version, --selftest) run on the builder through lapis's dynamic linker. The service binary
# is BIND_NOW, so this also proves that every import resolves against those libraries.
step "Android unit tests"
T=$A/test; mkdir -p "$T/obj" "$T/tmp"
TOBJ=""
for f in $OURS tests/fake_binder.cpp; do
  o=$T/obj/$(basename "$f" .cpp).o
  $CXX_A $CF -Wall -Wextra -Werror $INC -c "$f" -o "$o"
  TOBJ="$TOBJ $o"
done
for f in $AOSP; do
  o=$T/obj/aosp_$(basename "$f" .cpp).o
  $CXX_A $CF -Wall -Werror $INC -c "$f" -o "$o"
  TOBJ="$TOBJ $o"
done
for f in tests/test_main.cpp tests/fake_sm.cpp src/service_main.cpp; do
  $CXX_A $CF -Wall -Wextra -Werror $INC -c "$f" -o "$T/obj/$(basename "$f" .cpp).o"
done
$CXX_A -pie -static-libstdc++ -Wl,-z,now -Wl,--no-undefined $TOBJ "$T/obj/test_main.o" \
  "$LIBCRYPTO_ANDROID" -llog -o "$T/gk_android_tests"
# the real service_main.cpp with the fake service manager, for test_registration
$CXX_A -pie -static-libstdc++ -Wl,-z,now -Wl,--no-undefined $TOBJ "$T/obj/service_main.o" \
  "$T/obj/fake_sm.o" "$LIBCRYPTO_ANDROID" -llog -o "$T/gk_android_service_fake"
# SA-6: the frozen on-disk vectors, Android build
$CXX_A $CF -Wall -Wextra -Werror $INC -c tests/test_frozen_v1.cpp -o "$T/obj/test_frozen_v1.o"
$CXX_A -pie -static-libstdc++ -Wl,-z,now -Wl,--no-undefined $TOBJ "$T/obj/test_frozen_v1.o" \
  "$LIBCRYPTO_ANDROID" -llog -o "$T/gk_android_frozen_tests"
if [ -n "${LAPIS_RT:-}" ] && [ -n "${LAPIS_SYSLIB:-}" ]; then
  LDP="$LAPIS_RT/lib64/bionic:$LAPIS_RT/lib64:$LAPIS_SYSLIB"
  run_lapis() { LD_LIBRARY_PATH=$LDP "$LAPIS_RT/bin/linker64" "$@" 2>&1 | grep -v "linker configuration"; }
  # the child service runs through the same lapis linker (LD_LIBRARY_PATH is inherited)
  run_lapis "$T/gk_android_tests" "$T/vectors.json" "$T/tmp" \
    "$LAPIS_RT/bin/linker64" "$T/gk_android_service_fake" | tee "$T/test.log"
  grep -q "GK_HOST_TESTS: [0-9]* passed, 0 failed" "$T/test.log" || fail "Android unit tests"
  python3 -I tests/ref_check.py "$T/vectors.json" | tee "$T/ref_check.log"
  grep -q "GK_REF_CHECK: PASS" "$T/ref_check.log" || fail "reference check (Android build)"
  mkdir -p "$T/frozen_tmp"
  run_lapis "$T/gk_android_frozen_tests" "$T/frozen_tmp" | tee "$T/frozen.log"
  grep -q "GK_FROZEN_V1: PASS" "$T/frozen.log" || fail "frozen on-disk vectors (Android build, SA-6)"
  run_lapis "$A/$BIN" --version | tee "$A/lapis_run.log"
  run_lapis "$A/$BIN" --selftest | tee -a "$A/lapis_run.log"
  grep -q "selftest: PASS" "$A/lapis_run.log" || fail "service binary self-test on lapis bionic"
else
  echo "(LAPIS_RT/LAPIS_SYSLIB not set: Android test binary built, not run)"
fi

cp "$HERE/NOTICE" "$A/licenses/NOTICE"
cp "$BSSL_SRC/src/LICENSE" "$A/licenses/LICENSE.boringssl"
(cd "$A" && sha256sum "$BIN" "$BIN.unstripped" > SHA256SUMS)
cat "$A/SHA256SUMS"
ls -l "$A/$BIN"

# ---- 5. reproducibility (optional): COMPARE_WITH = the SHA256SUMS of another clean build -----
if [ -n "${COMPARE_WITH:-}" ]; then
  step "reproducibility: compare with $COMPARE_WITH"
  cmp -s "$A/SHA256SUMS" "$COMPARE_WITH" || { diff "$COMPARE_WITH" "$A/SHA256SUMS" || true; fail "not reproducible"; }
  echo "SHA256SUMS identical to $COMPARE_WITH"
fi

# The acceptance record VM_UPGRADE.md P5 asks for (build-time part; the device part is wave 4's).
{
  echo "gb_gatekeeper prototype acceptance, built $(date -u +%Y-%m-%dT%H:%M:%SZ) on $(hostname)"
  echo "version: $VERSION"
  sed 's/^/sha256: /' "$A/SHA256SUMS"
  echo "boringssl: $BSSL_COMMIT (AOSP external/boringssl android17-release, OPENSSL_NO_ASM)"
  echo "ndk: $(sed -n 's/^Pkg.Revision = //p' "$NDK/source.properties"), target x86_64-linux-android$API"
  echo "elf: NEEDED $needed| BIND_NOW | PIE | verneed LIBBINDER_NDK LIBBINDER_NDK30 LIBBINDER_NDK31"
  [ -n "${REF_LIBBINDER:-}" ] && echo "elf: binder imports checked against $REF_LIBBINDER"
  [ -n "${COMPARE_WITH:-}" ] && echo "reproducible: SHA256SUMS identical to $COMPARE_WITH"
  echo "host: $(grep GK_HOST_TESTS "$H/test.log") (ASan+UBSan)"
  echo "host: $(grep GK_REF_CHECK "$H/ref_check.log")"
  echo "host: $(grep GK_FROZEN_V1 "$H/frozen.log")"
  echo "host: $(grep GK_FROZEN_REF "$H/frozen_ref.log")"
  if [ -n "${REGRESS_OLD:-}" ]; then
    echo "regress-old ($REGRESS_OLD): $(grep GK_HOST_TESTS "$OUT/regress/test.log")"
    grep "^regress-old:" "$OUT/regress/summary.txt"
  fi
  if [ -f "$T/test.log" ]; then
    echo "android-on-lapis-bionic: $(grep GK_HOST_TESTS "$T/test.log")"
    echo "android-on-lapis-bionic: $(grep GK_REF_CHECK "$T/ref_check.log")"
    echo "android-on-lapis-bionic: $(grep GK_FROZEN_V1 "$T/frozen.log")"
    echo "android-on-lapis-bionic: $(grep selftest "$A/lapis_run.log")"
  else
    echo "android-on-lapis-bionic: NOT RUN (LAPIS_RT/LAPIS_SYSLIB unset)"
  fi
  printf '%s\n' 'device: NOT RUN (wave 4: C:\GB-build\native\dgpu\w4\GATEKEEPER.md section 9)'
} > "$OUT/ACCEPTANCE.txt"
cat "$OUT/ACCEPTANCE.txt"
echo "GK_BUILD: OK ($VERSION)"
