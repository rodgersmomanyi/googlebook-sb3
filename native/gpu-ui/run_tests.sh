#!/bin/bash
# Runs every test of the gpu-ui component on the builder (INTERFACES 18.6; v1.2: TASKS_1B 4.2) after ./build_apk.sh.
#   ./run_tests.sh [<INTERFACES.md copy>] [<power component source dir with gb_power.c and uapi/>]
# Logs: out/test/<step>.log; one line per test in out/test/SUMMARY.txt. Exit 1 if any step failed.
#   UI-01 1B-10   check_apk.py: badging, versionCode, v2+v3 signature, alignment; manifest, permissions (POST_NOTIFICATIONS
#                 + QUERY_ALL_PACKAGES, nothing privileged), exported components, injection meta-data, dex strings
#   UI-02 lint    Android Lint (test/run_lint.sh)                                                   (UI-1B-13)
#   1B-13 javac   host JVM build of core + tests with -Xlint:all -Werror
#   UNIT          host JVM unit tests: UI-1B-01..03, 05..09 and the kept wave-1 groups (UI-03)
#   SPEC          the exact strings and the 27.1 table against INTERFACES.md v1.2 itself
#   UI-05 1B-11   no socket / stale socket / stalled server -> Unavailable within 2 s, page controls disabled
#   UI-04 1B-04   host JVM protocol tests against test/mock_gb_power.py over a UNIX socket (java.nio transport)
#   MOCK  1B-12   mock conformance (9.6 + 26.3 goldens, limits) and mock == real gb_power v1.2 (two sessions)
#   REAL          the app client against the real gb_power v1.2 host build, wave-1 tree (no device, no DTX)
#   REAL12        the app client against the real gb_power v1.2 host build, INTERFACES 26.3 tree (DTX attached)
#   UI-06         gb_gpu_ui_setup.sh under mksh with shims + shellcheck
#   RC            gb_gpu_ui.rc lint and byte equality with INTERFACES 15.4
#   SC-BASH, PY   shellcheck of the build/test scripts, py_compile of the Python tests
set -uo pipefail
G=$(cd "$(dirname "$0")" && pwd)
SCRATCH=${SCRATCH:-/build/native/dgpu/gpu-ui}
SDK=${SDK:-$SCRATCH/sdk}
BTD=$SDK/build-tools/36.1.0
JAR=$SDK/platforms/android-36/android.jar
SPEC=${1:-$SCRATCH/spec/INTERFACES.md}
POWER=${2:-$SCRATCH/ext/power}
JAVA_HOME=${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}
export JAVA_HOME PATH="$JAVA_HOME/bin:$PATH"
NICE="nice -n 19 ionice -c3"
W=$SCRATCH/testwork
R=$G/out/test
rm -rf "$W" "$R"
mkdir -p "$W" "$R"
FAILED=0

summary() { # <id> <PASS|FAIL|SKIP> <evidence>
  printf '%-13s %-4s %s\n' "$1" "$2" "$3" | tee -a "$R/SUMMARY.txt"
  [ "$2" = PASS ] || [ "$2" = SKIP ] || FAILED=1
}
last() { grep -E "checks, [0-9]+ failures|No issues found|errors, .* warnings|lint exit" "$1" | tail -n 1; }
run_step() { # <id> <log name> <command...>
  id=$1
  log=$R/$2.log
  shift 2
  echo "== $id ($*)"
  if "$@" > "$log" 2>&1; then summary "$id" PASS "$(last "$log")"; else summary "$id" FAIL "$(last "$log") (see $log)"; fi
}
wait_sock() { for _ in $(seq 200); do [ -S "$1" ] && return 0; sleep 0.05; done; return 1; }

[ -f "$G/out/GbGpu.apk" ] || { echo "run ./build_apk.sh first"; exit 1; }

# ---- UI-01 / UI-1B-10 static, UI-02 lint
run_step "UI-01 1B-10" check_apk python3 -I "$G/test/check_apk.py" "$G/out/GbGpu.apk" "$BTD" "$JAR" \
  "$G/out/BUILD_INFO.txt" "$G/AndroidManifest.xml" "$G/test/lint.xml"
run_step "UI-02 lint" lint bash "$G/test/run_lint.sh" "$G/build/lint"

# ---- host JVM build of the core + tests (JDK 17)
echo "== host javac"
find "$G/src/gb/sb3/gpu/core" "$G/test/java" -name '*.java' | LC_ALL=C sort > "$W/host-sources.txt"
if $NICE javac --release 17 -encoding UTF-8 -Xlint:all -Werror -d "$W/classes" @"$W/host-sources.txt" \
    > "$R/host-javac.log" 2>&1; then
  summary "1B-13 javac" PASS "core + tests compile with -Xlint:all -Werror (host JDK 17), $(wc -l < "$W/host-sources.txt") files"
else
  summary "1B-13 javac" FAIL "see $R/host-javac.log"
fi
JT=(java -cp "$W/classes" gb.sb3.gpu.test.AllTests)

run_step "UNIT" unit "${JT[@]}" unit
if [ -f "$SPEC" ]; then
  run_step "SPEC" spec "${JT[@]}" spec "$SPEC"
else
  summary "SPEC" FAIL "INTERFACES.md copy not found at $SPEC"
fi
run_step "UI-05 1B-11" nosocket "${JT[@]}" nosocket "$W"

# ---- UI-04 / UI-1B-04 against the mock
python3 -I "$G/test/mock_gb_power.py" --socket "$W/mock.sock" --ctl "$W/mock.ctl" --uid 10123 \
  --ping-ms 300 --idle-ms 800 --sim-ms 200 > "$R/mock-server.log" 2>&1 &
MOCK=$!
for _ in $(seq 100); do [ -S "$W/mock.ctl" ] && break; sleep 0.05; done
run_step "UI-04 1B-04" protocol-mock "${JT[@]}" mock "$W/mock.sock" "$W/mock.ctl"
kill "$MOCK" 2> /dev/null
wait "$MOCK" 2> /dev/null

# ---- real gb_power host build (read-only copy of the power component's v1.2 source)
REAL=
if [ -f "$POWER/gb_power.c" ] && [ -f "$POWER/uapi/linux/surface_aggregator/dtx.h" ]; then
  if $NICE cc -O2 -Wall -Wextra -Wno-format-truncation -I"$POWER/uapi" -o "$W/gb_power_host" "$POWER/gb_power.c" \
      > "$R/gb_power_host-build.log" 2>&1; then
    REAL=$W/gb_power_host
    echo "gb_power.c sha256 $(sha256sum "$POWER/gb_power.c" | cut -d' ' -f1)" >> "$R/gb_power_host-build.log"
  else
    summary "REAL" FAIL "gb_power host build failed (see $R/gb_power_host-build.log)"
  fi
fi
run_step "MOCK 1B-12" mock-conformance python3 -I "$G/test/test_mock.py" "$G/test/mock_gb_power.py" "$W/mockconf" \
  ${REAL:+"$REAL"}

if [ -n "$REAL" ]; then
  # wave-1 tree: no NVIDIA device, no DTX (-D), no driver package
  RT=$W/real
  mkdir -p "$RT/props" "$RT/root/odm/etc" "$RT/root/odm/lib/modules" "$RT/root/odm/app/GbGpu" \
    "$RT/root/data/system" "$RT/root/data/vendor/gb" "$RT/root/sys/bus/pci/devices" "$RT/root/proc"
  echo native > "$RT/root/odm/etc/gb_hw"
  echo ko > "$RT/root/odm/lib/modules/nouveau.ko"
  echo apk > "$RT/root/odm/app/GbGpu/GbGpu.apk"
  printf '%s\n' "gb.sb3.gpu 10123 0 /data/user/0/gb.sb3.gpu platform:targetSdkVersion=36 none 0 1" \
    "com.example.other 10500 0 /data/user/0/com.example.other default:targetSdkVersion=36 none 0 1" \
    > "$RT/root/data/system/packages.list"
  echo "root=/dev/ram0 gb.hw=native" > "$RT/root/proc/cmdline"
  printf 1 > "$RT/props/vendor.gb.nouveau.booted"
  printf default > "$RT/props/vendor.gb.nouveau.mode"
  env -i PATH=/usr/bin:/bin GB_POWER_TEST_UID=10123 GB_POWER_PROPS_DIR="$RT/props" \
    "$REAL" -r "$RT/root" -S "$RT/gb_power.sock" -D -H -I > "$R/real-server.log" 2>&1 &
  RP=$!
  wait_sock "$RT/gb_power.sock"
  run_step "REAL" protocol-real "${JT[@]}" real "$RT/gb_power.sock" "$RT/props"
  kill -TERM "$RP" 2> /dev/null
  wait "$RP" 2> /dev/null
  # INTERFACES 26.3 tree with the host-only EC (GB_POWER_TEST_DTX=1)
  RT12=$W/real12
  mkdir -p "$RT12"
  python3 -I "$G/test/test_mock.py" --gfx-tree "$RT12/root" > "$R/real12-tree.log" 2>&1
  env -i PATH=/usr/bin:/bin GB_POWER_TEST_UID=10123 GB_POWER_PROPS_DIR="$RT12/root.props" GB_POWER_TEST_DTX=1 \
    "$REAL" -r "$RT12/root" -S "$RT12/gb_power.sock" -H -I > "$R/real12-server.log" 2>&1 &
  RP=$!
  wait_sock "$RT12/gb_power.sock"
  run_step "REAL12" protocol-real12 "${JT[@]}" real12 "$RT12/gb_power.sock" "$RT12/root.props"
  kill -TERM "$RP" 2> /dev/null
  wait "$RP" 2> /dev/null
else
  summary "REAL" SKIP "power component source not found at $POWER"
fi

# ---- UI-06, rc, shell scripts
run_step "UI-06" setup-script sh "$G/test/test_gb_gpu_ui_setup.sh" "$G/gb_gpu_ui_setup.sh" "$W/ui06"
if shellcheck -s sh "$G/gb_gpu_ui_setup.sh" "$G/test/test_gb_gpu_ui_setup.sh" > "$R/shellcheck-sh.log" 2>&1; then
  summary "SC-SH" PASS "shellcheck -s sh gb_gpu_ui_setup.sh test/test_gb_gpu_ui_setup.sh: 0 findings"
else
  summary "SC-SH" FAIL "see $R/shellcheck-sh.log"
fi
if [ -f "$SPEC" ]; then
  run_step "RC" rc python3 -I "$G/test/check_rc.py" "$G/gb_gpu_ui.rc" "$SPEC"
else
  run_step "RC" rc python3 -I "$G/test/check_rc.py" "$G/gb_gpu_ui.rc"
fi
if shellcheck -s bash "$G/build_apk.sh" "$G/run_tests.sh" "$G/test/run_lint.sh" "$G/build_on_builder.sh" \
    > "$R/shellcheck-bash.log" 2>&1; then
  summary "SC-BASH" PASS "shellcheck build_apk.sh run_tests.sh test/run_lint.sh build_on_builder.sh: 0 findings"
else
  summary "SC-BASH" FAIL "see $R/shellcheck-bash.log"
fi
if python3 -I -m py_compile "$G/test/mock_gb_power.py" "$G/test/test_mock.py" "$G/test/check_apk.py" \
    "$G/test/check_rc.py" > "$R/py_compile.log" 2>&1; then
  summary "PY" PASS "python3 -I -m py_compile of the 4 test scripts"
else
  summary "PY" FAIL "see $R/py_compile.log"
fi
SEED_OK=$(python3 -I -c 'import json, sys
j = json.load(open(sys.argv[1], encoding="utf-8"))
want = {"src": "C:\\GB-build\\native\\gpu-ui\\gfx_app_prefs.default", "dest": "etc/gb/gfx_app_prefs.default",
        "mode": "0644"}
print("yes" if want in j["files"] and j["build_prop"] == [] and j["rc_text"] == "" else "no")' "$G/gpu_ui.json" 2>&1)
if [ -f "$G/gfx_app_prefs.default" ] && [ ! -s "$G/gfx_app_prefs.default" ] && [ "$SEED_OK" = yes ]; then
  summary "SEED" PASS "gfx_app_prefs.default is empty (24.2) and gpu_ui.json installs it as etc/gb/gfx_app_prefs.default 0644 (T4.12)"
else
  summary "SEED" FAIL "gfx_app_prefs.default must exist, be empty and be listed in gpu_ui.json ($SEED_OK)"
fi
find "$G/test" -name '__pycache__' -prune -exec rm -rf {} +
rm -rf "$W"
echo
cat "$R/SUMMARY.txt"
exit $FAILED
