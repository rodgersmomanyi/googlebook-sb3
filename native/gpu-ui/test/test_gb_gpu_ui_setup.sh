#!/bin/sh
# UI-06: gb_gpu_ui_setup.sh under mksh with a fake GB_ROOT and PATH shims for pm, am, cmd, log, sleep and timeout.
#   sh test/test_gb_gpu_ui_setup.sh [<gb_gpu_ui_setup.sh>] [<work dir>]
# Pass criterion (INTERFACES 18.6; wave 6 notify: per user in headless system user mode): `pm grant --user <u>` for
# every FULL user and for the signed-in user, (wave 7, headless-settings, notify O10) once per user ever (marker
# granted.<u>), never for the headless system user 0; `add-tile` only while a FULL user is in the foreground and only
# once ever per user (a user removal is respected; the pre-wave-6 marker is ignored); (wave 7) the markers of removed
# user ids are removed; (wave 7, O14) at most 16 users; plus the guard and failure paths. Prints "N checks, M failures";
# exit 1 on a failure.
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
# pm: path (ctl/pkg_installed), grant (ctl/grant_fail), list users (ctl/users; ctl/list_fail)
cat > "$W/shims/pm" << 'EOF'
#!/bin/sh
echo "pm $*" >> "$GB_CALLS"
case "$1" in
  path) [ -f "$GB_CTL/pkg_installed" ] && echo "package:/odm/app/GbGpu/GbGpu.apk"; exit 0 ;;
  grant) [ -f "$GB_CTL/grant_fail" ] && exit 1; exit 0 ;;
  list) [ "$2" = users ] || exit 1; [ -f "$GB_CTL/list_fail" ] && exit 1; echo "Users:"; cat "$GB_CTL/users"; exit 0 ;;
esac
exit 1
EOF
# am get-current-user: pops the first line of ctl/cur (the last line stays); a line "<u> <file>" also installs <file>
# as the new ctl/users (a user created during the boot)
cat > "$W/shims/am" << 'EOF'
#!/bin/sh
PATH=/usr/bin:/bin
echo "am $*" >> "$GB_CALLS"
[ "$1" = get-current-user ] || exit 1
l=$(head -n 1 "$GB_CTL/cur")
if [ "$(wc -l < "$GB_CTL/cur")" -gt 1 ]; then sed -i 1d "$GB_CTL/cur"; fi
# shellcheck disable=SC2086  # split "<user> [<users file>]"
set -- $l
[ -n "${2:-}" ] && cp "$GB_CTL/$2" "$GB_CTL/users"
echo "$1" > "$GB_CTL/now"
echo "$1"
EOF
# cmd statusbar add-tile: records the foreground user it went to (ctl/now)
cat > "$W/shims/cmd" << 'EOF'
#!/bin/sh
echo "cmd $* @$(cat "$GB_CTL/now" 2> /dev/null)" >> "$GB_CALLS"
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
cat > "$W/shims/timeout" << 'EOF'
#!/bin/sh
shift
exec "$@"
EOF
for f in pm am cmd log sleep timeout; do chmod +x "$W/shims/$f"; done
# The script resets PATH to <shims>:/system/bin:/vendor/bin (toybox on the device); give it the host's cat.
ln -sf "$(command -v cat)" "$W/shims/cat"
ln -sf "$(command -v rm)" "$W/shims/rm"

TAB=$(printf '\t')
HSUM="${TAB}UserInfo{0:System User:811} running
${TAB}UserInfo{10:User:412}"
fresh() { # a new device: native, APK present, /data/vendor/gb/ui present (rc), package installed, headless mode
  rm -rf "$W/root" "$GB_CTL"
  mkdir -p "$W/root/odm/etc" "$W/root/odm/app/GbGpu" "$W/root/data/vendor/gb/ui" "$GB_CTL"
  echo native > "$W/root/odm/etc/gb_hw"
  : > "$W/root/odm/app/GbGpu/GbGpu.apk"
  : > "$GB_CTL/pkg_installed"
  printf '%s\n' "$HSUM" > "$GB_CTL/users"
  printf '0\n0\n10\n' > "$GB_CTL/cur" # the person signs in after two checks
  WAITN=180
}
boot() {
  : > "$GB_CALLS"
  : > "$GB_LOG"
  GB_ROOT=$W/root GB_TEST_PATH=$W/shims GB_UI_WAIT_CHECKS=$WAITN "$SHELL_UNDER_TEST" "$SCRIPT"
  RC=$?
}
UI=$W/root/data/vendor/gb/ui
G10="pm grant --user 10 gb.sb3.gpu android.permission.POST_NOTIFICATIONS"
G0="pm grant --user 0 "
ADD="cmd statusbar add-tile gb.sb3.gpu/.GpuTileService"

echo "== first boot, headless system user mode: the person (user 10) signs in after 20 s"
fresh
boot
is 0 "$RC" "exit 0"
is 1 "$(count "$G10")" "user 10 granted once (before the sign-in; the marker stops the second grant)"
ok "$([ -f "$UI/granted.10" ]; echo $?)" "marker granted.10 created"
is 644 "$(stat -c %a "$UI/granted.10" 2> /dev/null)" "marker granted.10 mode 0644"
is 0 "$(count "$G0")" "never a grant for the headless system user 0"
is 0 "$(count "pm grant gb.sb3.gpu")" "no grant without --user (it would reach user 0 only)"
is 1 "$(count "$ADD @10")" "add-tile once, while user 10 is in the foreground"
is 1 "$(count "$ADD")" "add-tile exactly once"
is 2 "$(count "sleep 10")" "two 10 s waits for the sign-in"
ok "$([ -f "$UI/tile_added.10" ]; echo $?)" "marker tile_added.10 created"
is 644 "$(stat -c %a "$UI/tile_added.10" 2> /dev/null)" "marker mode 0644"
ok "$([ ! -e "$UI/tile_added.0" ] && [ ! -e "$UI/tile_added" ]; echo $?)" "no marker for user 0, no legacy marker"

echo "== second boot (the user may have removed the tile since)"
printf '0\n10\n' > "$GB_CTL/cur"
boot
is 0 "$RC" "exit 0"
is 0 "$(count "$G10")" "user 10 not granted again (the person may have turned GbGpu's notifications off)"
is 0 "$(count "$ADD")" "add-tile not repeated for user 10"

echo "== pre-wave-6 marker (the tile went to user 0's list): ignored and kept"
fresh
: > "$UI/tile_added"
boot
is 1 "$(count "$ADD @10")" "add-tile for user 10 despite the legacy marker"
ok "$([ -f "$UI/tile_added" ] && [ -f "$UI/tile_added.10" ]; echo $?)" "legacy marker kept, tile_added.10 created"

echo "== nobody signs in this boot (bounded wait)"
fresh
printf '0\n' > "$GB_CTL/cur"
WAITN=5
boot
is 0 "$RC" "exit 0"
is 5 "$(count "am get-current-user")" "5 foreground checks (GB_UI_WAIT_CHECKS)"
is 1 "$(count "$G10")" "user 10 granted once (before the sign-in)"
is 0 "$(count "$ADD")" "no add-tile while the login screen (user 0) is in the foreground"
ok "$(grep -q 'no full user in the foreground after 5 checks' "$GB_LOG"; echo $?)" "logged"

echo "== a user created during this boot (11) signs in"
fresh
printf '%s\n' "$HSUM" "${TAB}UserInfo{11:Guest:412} running" > "$GB_CTL/users11"
printf '0\n11 users11\n' > "$GB_CTL/cur"
boot
is 0 "$RC" "exit 0"
is 1 "$(count "pm grant --user 11 ")" "user 11 granted after the sign-in"
ok "$([ -f "$UI/granted.10" ] && [ -f "$UI/granted.11" ]; echo $?)" "markers granted.10 and granted.11"
is 1 "$(count "$ADD @11")" "add-tile for user 11"
ok "$([ -f "$UI/tile_added.11" ]; echo $?)" "marker tile_added.11"
is 3 "$(count "pm list users")" "the list is read again only when the foreground user changes to an unknown one"

echo "== the user leaves the foreground before add-tile"
fresh
printf '0\n10\n0\n' > "$GB_CTL/cur"
boot
is 0 "$RC" "exit 0"
is 0 "$(count "$ADD")" "no add-tile (it would go to another user's tiles)"
ok "$([ ! -e "$UI/tile_added.10" ]; echo $?)" "no marker"

echo "== add-tile fails, then succeeds on the next boot"
fresh
printf '10\n' > "$GB_CTL/cur"
: > "$GB_CTL/addtile_fail"
boot
is 1 "$RC" "exit 1 when add-tile keeps failing"
is 3 "$(count "$ADD")" "add-tile tried 3 times"
is 2 "$(count "sleep 10")" "10 s between tries"
ok "$([ ! -e "$UI/tile_added.10" ]; echo $?)" "no marker after a failure"
rm -f "$GB_CTL/addtile_fail"
boot
is 0 "$RC" "next boot: exit 0"
is 1 "$(count "$ADD")" "next boot: add-tile once"
ok "$([ -f "$UI/tile_added.10" ]; echo $?)" "next boot: marker created"

echo "== pm grant fails"
fresh
: > "$GB_CTL/grant_fail"
boot
is 0 "$RC" "grant failure ignored"
is 1 "$(count "$ADD")" "add-tile still done"
ok "$([ ! -e "$UI/granted.10" ]; echo $?)" "no granted marker after a failed grant"
rm -f "$GB_CTL/grant_fail"
printf '0
10
' > "$GB_CTL/cur"
boot
is 1 "$(count "$G10")" "next boot: the grant is tried again (once) and recorded"
ok "$([ -f "$UI/granted.10" ]; echo $?)" "next boot: marker granted.10"

echo "== pm list users fails"
fresh
: > "$GB_CTL/list_fail"
WAITN=3
boot
is 0 "$RC" "exit 0"
is 0 "$(count "pm grant")" "no grant without a user list"
is 0 "$(count "$ADD")" "no add-tile (the foreground user cannot be shown to be a full user)"

echo "== not headless (user 0 is a FULL user)"
fresh
printf '\tUserInfo{0:Owner:c13} running\n' > "$GB_CTL/users"
printf '0\n' > "$GB_CTL/cur"
boot
is 0 "$RC" "exit 0"
is 1 "$(count "pm grant --user 0 gb.sb3.gpu")" "user 0 granted once (it is the person)"
is 1 "$(count "$ADD @0")" "add-tile for user 0"
ok "$([ -f "$UI/tile_added.0" ]; echo $?)" "marker tile_added.0"

echo "== user list parsing: names with ':' or '}', profiles, junk"
fresh
printf '%s\n' "${TAB}UserInfo{0:System User:811} running" "${TAB}UserInfo{10:a:b:c}:412}" \
  "${TAB}UserInfo{11:x:412} r:10}" "${TAB}UserInfo{12:Work:1030} running" "${TAB}UserInfo{1x:Bad:412}" \
  "${TAB}UserInfo{13:Big:412412412}" 'garbage' "${TAB}UserInfo{14:sp ace:412} running" > "$GB_CTL/users"
printf '0\n' > "$GB_CTL/cur"
WAITN=1
boot
is 1 "$(count "pm grant --user 10 ")" "name with ':' and '}': the flags after the last ':' (412, FULL)"
is 0 "$(count "pm grant --user 11 ")" "a name cannot fake the flags (they end the line: 10, not FULL)"
is 0 "$(count "pm grant --user 12 ")" "a work profile (1030, no FULL) is skipped"
is 1 "$(count "pm grant --user 14 ")" "a name with a space"
is 2 "$(count "pm grant --user ")" "only users 10 and 14 (no 1x, no 9-digit flags)"

echo "== (wave 7) markers of removed users are removed; kept when the list does not name user 0"
fresh
for m in granted.10 tile_added.10 granted.12 tile_added.12 tile_added granted.x; do : > "$UI/$m"; done
printf '0
' > "$GB_CTL/cur"
WAITN=1
boot
ok "$([ -f "$UI/granted.10" ] && [ -f "$UI/tile_added.10" ]; echo $?)" "user 10's markers kept"
ok "$([ ! -e "$UI/granted.12" ] && [ ! -e "$UI/tile_added.12" ]; echo $?)" "removed user 12's markers removed"
ok "$([ -f "$UI/tile_added" ] && [ -f "$UI/granted.x" ]; echo $?)" "the legacy marker and a non-numeric name are left alone"
ok "$(grep -q 'user 12 no longer exists: granted.12 removed' "$GB_LOG"; echo $?)" "removal logged"
is 0 "$(count "$G10")" "user 10 (marker) not granted"
: > "$UI/granted.12"
printf '%s
' "${TAB}UserInfo{10:User:412}" > "$GB_CTL/users"
boot
ok "$([ -f "$UI/granted.12" ]; echo $?)" "a list without user 0: nothing removed"
printf '%s
' "$HSUM" "${TAB}UserInfo{120:Other:412}" > "$GB_CTL/users"
boot
ok "$([ ! -e "$UI/granted.12" ]; echo $?)" "user 120 does not keep user 12's marker"
is 1 "$(count "pm grant --user 120 ")" "user 120 granted"

echo "== (wave 7, notify O14) at most 16 users"
fresh
{ echo "${TAB}UserInfo{0:System User:811} running"; i=10; while [ $i -lt 30 ]; do echo "${TAB}UserInfo{$i:U$i:412}"; i=$((i + 1)); done; } > "$GB_CTL/users"
printf '0
' > "$GB_CTL/cur"
WAITN=1
boot
is 16 "$(count "pm grant --user ")" "16 grants for 20 full users"
is 1 "$(count "pm grant --user 25 ")" "the 16th user (25) is granted"
is 0 "$(count "pm grant --user 26 ")" "the 17th user (26) is not"

echo "== not native (VM)"
fresh
echo vm > "$W/root/odm/etc/gb_hw"
boot
is 0 "$RC" "exit 0"
is 0 "$(count "pm ")" "no pm call"
is 0 "$(count "cmd ")" "no cmd call"
is 0 "$(count "am ")" "no am call"

echo "== APK absent"
fresh
rm -f "$W/root/odm/app/GbGpu/GbGpu.apk"
boot
is 0 "$RC" "exit 0"
is 000 "$(count "pm ")$(count "cmd ")$(count "am ")" "no pm/cmd/am call"

echo "== /data/vendor/gb/ui missing"
fresh
rm -rf "$W/root/data/vendor/gb/ui"
boot
is 0 "$RC" "exit 0"
is 2 "$(count "$G10")" "pm grant done (before and after the sign-in: it cannot be recorded)"
is 0 "$(count "$ADD")" "no add-tile without the marker directory (it could not be recorded)"
ok "$(grep -q 'is missing: not recorded' "$GB_LOG"; echo $?)" "unrecorded grant logged"

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
