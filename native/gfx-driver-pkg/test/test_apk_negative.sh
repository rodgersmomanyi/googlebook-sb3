#!/bin/bash
# GD-1B-11 negative cases (builder): the allowlist guards really fire. From the built out/GbGfxDriver.apk it makes
# copies with assets/allowlist.txt ("*"), with a nested .../allowlist.txt, and an aapt2-style input APK holding the
# asset, then expects: apk_tool.py check FAIL, check_apk.sh FAIL, apk_tool.py add-libs REFUSED (exit 1, no output).
# The copies are unsigned/realigned test files in a temp dir and are deleted at the end.
set -uo pipefail
G=${G:-$(cd "$(dirname "$0")/.." && pwd)}
APK=${1:-$G/out/GbGfxDriver.apk}
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
pass=0 fail=0
expect() { if [ "$2" = "$3" ]; then echo "  PASS $1 (rc $3)"; pass=$((pass + 1)); else echo "  FAIL $1 (rc $3, want $2)"; fail=$((fail + 1)); fi; }
mk() { # mk <out> <member>: copy of APK plus <member> containing "*"
  python3 -I - "$APK" "$1" "$2" <<'EOF'
import sys, zipfile
src, dst, member = sys.argv[1:4]
with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w") as zo:
    for info in zi.infolist():
        zo.writestr(info, zi.read(info.filename))
    zo.writestr(member, "*\n")
EOF
}
mk "$T/with_allowlist.apk" assets/allowlist.txt
mk "$T/nested_allowlist.apk" assets/x/allowlist.txt
python3 -I "$G/apk/apk_tool.py" check "$APK" > "$T/ok.log" 2>&1; expect "apk_tool check on the real APK" 0 $?
python3 -I "$G/apk/apk_tool.py" check "$T/with_allowlist.apk" > "$T/a.log" 2>&1; expect "apk_tool check, assets/allowlist.txt" 1 $?
grep -q "FAIL forbidden member assets/allowlist.txt" "$T/a.log"; expect "  ... names the member" 0 $?
python3 -I "$G/apk/apk_tool.py" check "$T/nested_allowlist.apk" > "$T/b.log" 2>&1; expect "apk_tool check, assets/x/allowlist.txt" 1 $?
bash "$G/apk/check_apk.sh" "$T/with_allowlist.apk" > "$T/c.log" 2>&1; expect "check_apk.sh, assets/allowlist.txt" 1 $?
grep -q "FAIL no assets/allowlist.txt" "$T/c.log"; expect "  ... the allowlist check is the one that failed" 0 $?
mkdir -p "$T/libs" && : > "$T/libs/x.so"
python3 -I "$G/apk/apk_tool.py" add-libs "$T/with_allowlist.apk" "$T/out.apk" "$T/libs" > "$T/d.log" 2>&1
expect "apk_tool add-libs refuses an input APK with the asset" 1 $?
[ ! -e "$T/out.apk" ]; expect "  ... and writes no output APK" 0 $?
echo "RESULT: $([ $fail = 0 ] && echo PASS || echo FAIL) ($pass passed, $fail failed)"
[ $fail = 0 ]
