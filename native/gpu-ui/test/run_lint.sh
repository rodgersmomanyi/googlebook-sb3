#!/bin/bash
# UI-02 lint: Android Lint from the SDK's cmdline-tools, run without Gradle through a project descriptor that lists
# the manifest, every resource and source file (including the aapt2-generated R.java) and the compiled classes of
# the last build_apk.sh run (build/). Errors fail the run; the full report is kept next to the build.
#   test/run_lint.sh [<report dir>]
set -euo pipefail
G=$(cd "$(dirname "$0")/.." && pwd)
SCRATCH=${SCRATCH:-/build/native/dgpu/gpu-ui}
SDK=${SDK:-$SCRATCH/sdk}
L=${1:-$G/build/lint}
JAVA_HOME=${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}
export JAVA_HOME PATH="$JAVA_HOME/bin:$PATH"
[ -d "$G/build/classes" ] || { echo "run build_apk.sh first"; exit 1; }
mkdir -p "$L"
x() { printf '%s\n' "$1" | sed -e 's/&/\&amp;/g' -e 's/"/\&quot;/g'; }
{
  echo '<project>'
  echo "  <sdk dir=\"$(x "$SDK")\" />"
  echo '  <module name="GbGpu" android="true" library="false" compile-sdk-version="36">'
  echo "    <manifest file=\"$(x "$G/AndroidManifest.xml")\" />"
  (cd "$G" && find res -type f | LC_ALL=C sort) | while read -r f; do
    echo "    <resource file=\"$(x "$G/$f")\" />"
  done
  (cd "$G" && find src build/gen -name '*.java' | LC_ALL=C sort) | while read -r f; do
    echo "    <src file=\"$(x "$G/$f")\" />"
  done
  echo "    <classes file=\"$(x "$G/build/classes")\" />"
  echo '  </module>'
  echo '</project>'
} > "$L/project.xml"
cd "$L"
set +e
nice -n 19 ionice -c3 "$SDK/cmdline-tools/latest/bin/lint" --project "$L/project.xml" --config "$G/test/lint.xml" \
  --sdk-home "$SDK" --text "$L/lint.txt" --xml "$L/lint-results.xml" --exitcode --quiet > "$L/lint.log" 2>&1
rc=$?
set -e
cat "$L/lint.txt"
echo "lint exit $rc"
exit $rc
