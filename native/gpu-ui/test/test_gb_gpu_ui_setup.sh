#!/bin/sh
# UI-06: gb_gpu_ui_setup.sh under mksh with a fake GB_ROOT and PATH shims for pm, cmd, log and sleep.
#   sh test/test_gb_gpu_ui_setup.sh [<gb_gpu_ui_setup.sh>] [<work dir>]
# Pass criterion (INTERFACES 18.6): `pm grant` once per boot; `add-tile` only once ever (a user removal is
# respected); plus the guard and failure paths. Prints "N checks, M failures"; exit 1 on a failure.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT=${1:-$HERE/../gb_gpu_ui_setup.sh}
W=${2:-$(mktemp -d)}
SHELL_UNDER_TEST=${SHELL_UNDER_TEST:-mksh}
mkdir -p "$W"
checks=0
fails=0
GB_CALLS=$W/calls
GB_LOG=$W/log
GB_CTL=$W/ctl
export GB_CALLS GB_LOG GB_CTL

ok() { # <condition result 0/1> <what>
  checks=$((checks + 1))
  if [ "$1" -eq 0 ]; then
    echo "PASS $2"
  else
    fails=$((fails + 1))
    echo "FAIL $2"
  fi
}
is() { # <want> <got> <what>
  if [ "$1" = "$2" ]; then ok 0 "$3"; else ok 1 "$3 (want '$1', got '$2')"; fi
}
count() {
  n=$(grep -c -- "$1" "$GB_CALLS" 2> /dev/null)
  echo "${n:-0}"
}

mkdir -p "$W/shims" "$GB_CTL"
cat > "$W/shims/pm" << 'EOF'
#!/bin/sh
echo "pm $*" >> "$GB_CALLS"
case "$1" in
  path) [ -f "$GB_CTL/pkg_installed" ] && echo "package:/odm/app/GbGpu/GbGpu.apk"; exit 0 ;;
  grant) [ -f "$GB_CTL/grant_fail" ] && exit 1; exit 0 ;;
esac
exit 1
EOF
cat > "$W/shims/cmd" << 'EOF'
#!/bin/sh
echo "cmd $*" >> "$GB_CALLS"
if [ "$1" = statusbar ] && [ "$2" = add-tile ]; then
  [ -f "$GB_CTL/addtile_fail" ] && exit 20
  exit 0
fi
exit 1
EOF
cat > "$W/shims/log" << 'EOF'
#!/bin/sh
echo "log $*" >> "$GB_LOG"
EOF
cat > "$W/shims/sleep" << 'EOF'
#!/bin/sh
echo "sleep $*" >> "$GB_CALLS"
EOF
for f in pm cmd log sleep; do chmod +x "$W/shims/$f"; done
# The script resets PATH to <shims>:/system/bin:/vendor/bin (toybox on the device); give it the host's cat.
ln -sf "$(command -v cat)" "$W/shims/cat"

fresh() { # a new device: native, APK present, /data/vendor/gb/ui present (rc), package installed
  rm -rf "$W/root" "$GB_CTL"
  mkdir -p "$W/root/odm/etc" "$W/root/odm/app/GbGpu" "$W/root/data/vendor/gb/ui" "$GB_CTL"
  echo native > "$W/root/odm/etc/gb_hw"
  : > "$W/root/odm/app/GbGpu/GbGpu.apk"
  : > "$GB_CTL/pkg_installed"
}
boot() {
  : > "$GB_CALLS"
  GB_ROOT=$W/root GB_TEST_PATH=$W/shims "$SHELL_UNDER_TEST" "$SCRIPT"
  RC=$?
}
M=ui/tile_added
GRANT="pm grant gb.sb3.gpu android.permission.POST_NOTIFICATIONS"
ADD="cmd statusbar add-tile gb.sb3.gpu/.GpuTileService"

echo "== first boot"
fresh
boot
is 0 "$RC" "exit 0"
is 1 "$(count "$GRANT")" "pm grant once"
is 1 "$(count "$ADD")" "add-tile once"
ok "$([ -f "$W/root/data/vendor/gb/$M" ]; echo $?)" "marker created"
is 644 "$(stat -c %a "$W/root/data/vendor/gb/$M" 2> /dev/null)" "marker mode 0644"

echo "== second boot (the user may have removed the tile since)"
boot
is 0 "$RC" "exit 0"
is 1 "$(count "$GRANT")" "pm grant again (once per boot)"
is 0 "$(count "$ADD")" "add-tile not repeated"

echo "== add-tile fails, then succeeds on the next boot"
fresh
: > "$GB_CTL/addtile_fail"
boot
is 1 "$RC" "exit 1 when add-tile keeps failing"
is 3 "$(count "$ADD")" "add-tile tried 3 times"
is 2 "$(count "sleep 10")" "10 s between tries"
ok "$([ ! -e "$W/root/data/vendor/gb/$M" ]; echo $?)" "no marker after a failure"
rm -f "$GB_CTL/addtile_fail"
boot
is 0 "$RC" "next boot: exit 0"
is 1 "$(count "$ADD")" "next boot: add-tile once"
ok "$([ -f "$W/root/data/vendor/gb/$M" ]; echo $?)" "next boot: marker created"

echo "== pm grant fails"
fresh
: > "$GB_CTL/grant_fail"
boot
is 0 "$RC" "grant failure ignored"
is 1 "$(count "$ADD")" "add-tile still done"

echo "== not native (VM)"
fresh
echo vm > "$W/root/odm/etc/gb_hw"
boot
is 0 "$RC" "exit 0"
is 0 "$(count "pm ")" "no pm call"
is 0 "$(count "cmd ")" "no cmd call"

echo "== APK absent"
fresh
rm -f "$W/root/odm/app/GbGpu/GbGpu.apk"
boot
is 0 "$RC" "exit 0"
is 00 "$(count "pm ")$(count "cmd ")" "no pm/cmd call"

echo "== /data/vendor/gb/ui missing"
fresh
rm -rf "$W/root/data/vendor/gb/ui"
boot
is 0 "$RC" "exit 0"
is 1 "$(count "$GRANT")" "pm grant done"
is 0 "$(count "$ADD")" "no add-tile without the marker directory (it could not be recorded)"

echo "== package not installed"
fresh
rm -f "$GB_CTL/pkg_installed"
boot
is 1 "$RC" "exit 1"
is 12 "$(count "pm path gb.sb3.gpu")" "waited 12 x 5 s for PackageManager"
is 0 "$(count "pm grant")" "no grant"
is 0 "$(count "$ADD")" "no add-tile"

echo "== static: never needs root"
for bad in '\bsu\b' 'setprop' 'insmod' '> */sys/' 'chown' 'mount '; do
  if grep -v '^#' "$SCRIPT" | grep -Eq "$bad"; then ok 1 "script uses $bad"; else ok 0 "script does not use $bad"; fi
done

echo
echo "$checks checks, $fails failures"
[ "$fails" -eq 0 ]
